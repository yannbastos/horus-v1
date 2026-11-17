"""Revisão humana de licença por asset (HOR-097). Append-only; nunca reescreve o `asset-provenance.json` registrado."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import uuid
from pathlib import Path
from typing import Literal

from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .media_contracts import PILOT_INPUT_PATH, STOCK_HOSTS, UNSAFE_TEXT, _url_problem, apply_license_reviews, validate_asset_provenance
from .storage import DatabaseUnavailable
from .workflows import WorkflowConflict, encode

_FIELDS = ("id", "production_id", "asset_id", "asset_sha256", "decision", "license", "evidence",
           "reviewed_by", "reviewed_at", "supersedes")
_INPUTS = PILOT_INPUT_PATH
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReviewLicense(_Strict):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    commercial_use: Literal["allowed", "restricted", "unknown"]
    attribution_required: bool

    @field_validator("id", "name")
    @classmethod
    def readable(cls, value):
        if not value.strip() or UNSAFE_TEXT.search(value):
            raise ValueError("texto vazio ou com caracteres de controle")
        return value


class LicenseReviewRequest(_Strict):
    asset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["verified", "rejected"]
    license: ReviewLicense
    evidence: str = Field(min_length=1, max_length=2000)
    reviewed_by: Literal["human"]

    @field_validator("evidence")
    @classmethod
    def readable(cls, value):
        if not value.strip() or UNSAFE_TEXT.search(value):
            raise ValueError("evidência vazia ou com caracteres de controle")
        return value


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _json_val(value):
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    return value


def _row(row) -> dict:
    item = {}
    for key in _FIELDS:
        if key == "license":
            item[key] = _json_val(row[key])
        elif key == "reviewed_at":
            item[key] = _iso(row[key])
        else:
            item[key] = row[key]
    return item


class LicenseReviewService:
    def __init__(self, store, data_dir: Path):
        self.store, self.data_dir = store, Path(data_dir)

    def _uow(self, *, readonly: bool = False):
        if self.store is None or not hasattr(self.store, "uow"):
            raise DatabaseUnavailable("database_unavailable")
        return self.store.uow(readonly=readonly)

    def _provenance(self, conn, production_id: str, *, for_update: bool = False) -> dict | None:
        """Proveniência registrada (hash conferido) e validada; `None` se ausente ou inválida. Só leitura."""
        from .local_pilot import registered_json
        sql = "SELECT artifacts FROM horus.productions WHERE id = %s" + (" FOR UPDATE" if for_update else "")
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(sql, (production_id,)).fetchone()
        if row is None:
            raise KeyError(production_id)
        artifacts = _json_val(row["artifacts"])
        root = self.data_dir / "productions" / production_id
        inputs = {a["relative_path"]: a["sha256"] for a in artifacts if _INPUTS.fullmatch(a["relative_path"])}
        data = registered_json(root, artifacts, "asset-provenance.json")
        verdict = validate_asset_provenance(data, artifacts=inputs)
        return verdict["value"] if verdict["ok"] else None

    @staticmethod
    def _history(conn, production_id: str) -> list[dict]:
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                "SELECT * FROM horus.license_reviews WHERE production_id = %s ORDER BY seq",
                (production_id,),
            ).fetchall()
        return [_row(r) for r in rows]

    def list(self, production_id: str) -> dict:
        with self._uow(readonly=True) as conn:
            provenance = self._provenance(conn, production_id)
            history = self._history(conn, production_id)
        current = list({r["asset_id"]: r for r in history}.values())
        effective = None
        if provenance is not None:
            try:
                effective = apply_license_reviews(provenance, history)
            except ValueError:
                effective = None
        return {"production_id": production_id, "current": current, "history": history,
                "effective_provenance": effective, "publishable": False}

    def create(self, production_id: str, request: LicenseReviewRequest, key: str) -> tuple[dict, bool]:
        encoded = encode(request.model_dump())
        encoded_bytes = encoded.encode("utf-8")
        with self._uow() as conn:
            provenance = self._provenance(conn, production_id, for_update=True)  # KeyError (404) se a produção não existe
            with conn.cursor(row_factory=dict_row) as cur:
                receipt = cur.execute(
                    "SELECT * FROM horus.license_reviews WHERE production_id = %s AND idempotency_key = %s",
                    (production_id, key),
                ).fetchone()
                if receipt:
                    if bytes(receipt["request_canonical"]) != encoded_bytes:
                        raise WorkflowConflict("Chave de idempotência usada com outra decisão.")
                    return _row(receipt), False
                if provenance is None:
                    raise WorkflowConflict("Produção sem asset-provenance.json registrado e válido.")
                asset = next((a for a in provenance["assets"] if a["asset_id"] == request.asset_id), None)
                if asset is None:
                    raise WorkflowConflict("Asset não consta na proveniência da produção.")
                if asset["sha256"] != request.asset_sha256:
                    raise WorkflowConflict("asset_sha256 diverge do asset registrado.")
                lic = request.license
                if request.decision == "verified" and lic.commercial_use == "unknown":
                    raise WorkflowConflict("commercial_use desconhecido não pode ser verified.")
                if problem := _url_problem(lic.url, STOCK_HOSTS.get(asset["origin"])):
                    raise WorkflowConflict(f"license.url inválida: {problem}")
                moment = datetime.now(timezone.utc)
                candidate = {"asset_id": request.asset_id, "asset_sha256": request.asset_sha256, "decision": request.decision,
                             "license": lic.model_dump(), "reviewed_at": moment.isoformat()}
                try:   # nenhum registro que torne a visão efetiva da proveniência inválida (estado impossível) é gravado
                    apply_license_reviews(provenance, [*self._history(conn, production_id), candidate])
                except ValueError:
                    raise WorkflowConflict("A decisão tornaria a proveniência efetiva inválida.") from None
                previous = cur.execute(
                    "SELECT id FROM horus.license_reviews WHERE production_id = %s AND asset_id = %s ORDER BY seq DESC LIMIT 1",
                    (production_id, request.asset_id),
                ).fetchone()
                review_id = uuid.uuid4().hex
                lic_encoded = encode(lic.model_dump())
                inserted = cur.execute(
                    """INSERT INTO horus.license_reviews(
                        id, production_id, asset_id, asset_sha256, decision, license, evidence,
                        reviewed_by, reviewed_at, supersedes, idempotency_key, request, request_canonical
                    ) VALUES(%s, %s, %s, %s, %s, %s::jsonb, %s, 'human', %s, %s, %s, %s::jsonb, %s)
                    RETURNING *""",
                    (
                        review_id, production_id, request.asset_id, request.asset_sha256, request.decision,
                        lic_encoded, request.evidence, moment, previous["id"] if previous else None,
                        key, encoded, encoded_bytes,
                    ),
                ).fetchone()
                return _row(inserted), True
