"""Contrato do catálogo de técnicas de motion/edição, com fonte verificável e timecode de referência (HOR-058).

Módulo puro, sem rede, banco, disco, subprocesso ou download, e **não ligado** à API, ao worker nem à fábrica: define o formato
do catálogo, valida-o e oferece busca/filtro em memória. Segue as convenções de `media_contracts.py`: modelos estritos
(`extra=forbid`, sem coerção), `validate_*` nunca levanta e devolve `{ok, errors, warnings, publishable:false, value}`.

Regras (docs/MOTION-LIBRARY.md):
- Toda técnica cita ao menos uma referência a uma fonte do catálogo, com intervalo `start`–`end` em segundos (e timecode
  `HH:MM:SS.mmm` derivado). A fonte é rastreável (título, URL https sem credenciais ou hash do arquivo local, data da
  consulta) e a verificação humana é registrada à parte; `analisar` uma técnica nunca prova licença de reutilização
  (`reuse.status` começa `unknown`, e `allowed` exige verificação humana).
- Parâmetros distinguem `measured` (exige referência), `inferred` e `estimated`; valor numérico respeita limites declarados.
- Maturidade: `analyzed` (descrição fundamentada; sem implementação nem evidência), `implemented` (componente identificável;
  não é validação) e `validated` (exige contexto e evidência de comportamento/resultado com hash). Nenhum documento pode
  declarar `publishable`.
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Mapping, Sequence

from pydantic import BaseModel, Field, ValidationError, model_validator

from .media_contracts import (MAX_ERRORS, SHA256, UNSAFE_TEXT, IsoDate, Scalar, Seconds, SchemaOne, _ID, _Media, _error,
                              _issues_to_error, _parse, _result, _safe_path, _url_problem)

Category = Literal["transition", "camera", "text", "audio_sync", "layout", "color", "effect", "other"]
Status = Literal["analyzed", "implemented", "validated"]
Certainty = Literal["measured", "inferred", "estimated"]
ReuseStatus = Literal["unknown", "restricted", "allowed"]
EvidenceKind = Literal["test", "render", "review"]
SourceKind = Literal["external_video", "local_media"]
TAG = r"^[a-z0-9][a-z0-9-]{0,29}$"
MIN_REFERENCE_SECONDS = 0.04     # ~1 quadro a 25 fps: intervalos menores não demonstram uma técnica


def format_timecode(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    return f"{hours:02d}:{minutes:02d}:{rest // 1000:02d}.{rest % 1000:03d}"


def parse_timecode(text: str) -> float:
    match = re.fullmatch(r"(\d{1,2}):([0-5]\d):([0-5]\d)(?:\.(\d{1,3}))?", text or "")
    if not match:
        raise ValueError("timecode deve ser HH:MM:SS ou HH:MM:SS.mmm")
    hours, minutes, secs, fraction = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(secs) + int((fraction or "0").ljust(3, "0")) / 1000


def _text_issues(pairs) -> list[tuple[str, str, str]]:
    return [("unsafe_text", field, "texto com caracteres de controle ou de direção.")
            for field, text in pairs if text is not None and UNSAFE_TEXT.search(text)]


class SourceReuse(_Media):
    status: ReuseStatus = "unknown"
    verified_by: Literal["human"] | None = None
    verified_at: IsoDate | None = None
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def coherent(self):
        issues = _text_issues([("note", self.note)])
        if (self.verified_by is None) != (self.verified_at is None):
            issues.append(("verification_incomplete", "reuse", "verified_by e verified_at devem vir juntos."))
        if self.status == "allowed" and self.verified_by != "human":
            issues.append(("allowed_without_human", "reuse.status", "uso permitido exige verificação humana registrada."))
        _issues_to_error(issues)
        return self


class Source(_Media):
    id: str = Field(pattern=_ID)
    kind: SourceKind
    title: str = Field(min_length=1, max_length=300)
    url: str | None = Field(default=None, min_length=1, max_length=2000)
    author: str | None = Field(default=None, min_length=1, max_length=300)
    media_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    duration_seconds: Seconds | None = None
    retrieved_at: IsoDate
    verified_by: Literal["human"] | None = None
    verified_at: IsoDate | None = None
    reuse: SourceReuse = Field(default_factory=SourceReuse)

    @model_validator(mode="after")
    def traceable(self):
        issues = _text_issues([("title", self.title), ("author", self.author)])
        if self.kind == "external_video":
            if not self.url:
                issues.append(("missing_url", "url", "fonte externa exige URL https rastreável."))
            elif problem := _url_problem(self.url, None):
                issues.append(("invalid_url", "url", problem))
        else:
            if not self.media_sha256:
                issues.append(("missing_hash", "media_sha256", "fonte local exige o SHA-256 do arquivo analisado."))
            if self.url is not None:
                issues.append(("unexpected_url", "url", "fonte local não declara URL."))
        if (self.verified_by is None) != (self.verified_at is None):
            issues.append(("verification_incomplete", "verified_by", "verified_by e verified_at devem vir juntos."))
        _issues_to_error(issues)
        return self


class Reference(_Media):
    source_id: str = Field(pattern=_ID)
    start: Seconds
    end: Seconds
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def interval(self):
        issues = _text_issues([("note", self.note)])
        if self.end - self.start < MIN_REFERENCE_SECONDS:
            issues.append(("interval_too_short", "end", f"o intervalo deve ter ao menos {MIN_REFERENCE_SECONDS} s."))
        _issues_to_error(issues)
        return self

    @property
    def timecode(self) -> str:
        return f"{format_timecode(self.start)}-{format_timecode(self.end)}"


class Parameter(_Media):
    name: str = Field(min_length=1, max_length=80)
    value: Scalar
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    min: float | None = Field(default=None, allow_inf_nan=False)
    max: float | None = Field(default=None, allow_inf_nan=False)
    certainty: Certainty
    reference: int | None = Field(default=None, ge=0, le=19)
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def coherent(self):
        issues = _text_issues([("name", self.name), ("note", self.note)])
        numeric = type(self.value) in (int, float)
        if (self.min is not None or self.max is not None) and not numeric:
            issues.append(("limits_on_non_numeric", "value", "limites só valem para valor numérico."))
        if self.min is not None and self.max is not None and self.min > self.max:
            issues.append(("limits_inverted", "min", "min não pode ser maior que max."))
        if numeric:
            if self.min is not None and self.value < self.min or self.max is not None and self.value > self.max:
                issues.append(("value_out_of_limits", "value", "valor fora dos limites declarados."))
        if self.certainty == "measured" and self.reference is None:
            issues.append(("measured_without_reference", "reference", "parâmetro medido exige a referência de onde foi medido."))
        _issues_to_error(issues)
        return self


class Implementation(_Media):
    component: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,79}$")      # componente/composição Remotion existente
    factory_path: str = Field(min_length=1, max_length=260)                # caminho relativo na fábrica; não é verificado aqui
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def safe(self):
        issues = _text_issues([("note", self.note)])
        if reason := _safe_path(self.factory_path):
            issues.append(("unsafe_path", "factory_path", reason))
        _issues_to_error(issues)
        return self


class Evidence(_Media):
    kind: EvidenceKind
    path: str = Field(min_length=1, max_length=260)
    sha256: str = SHA256
    description: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def safe(self):
        issues = _text_issues([("description", self.description)])
        if reason := _safe_path(self.path):
            issues.append(("unsafe_path", "path", reason))
        _issues_to_error(issues)
        return self


class Technique(_Media):
    id: str = Field(pattern=_ID)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    category: Category
    tags: list[Annotated[str, Field(pattern=TAG)]] = Field(default_factory=list, max_length=10)
    references: list[Reference] = Field(min_length=1, max_length=20)
    parameters: list[Parameter] = Field(default_factory=list, max_length=30)
    status: Status
    implementation: Implementation | None = None
    validation_context: str | None = Field(default=None, min_length=1, max_length=500)
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def coherent(self):
        issues = _text_issues([("name", self.name), ("description", self.description), ("validation_context", self.validation_context)])
        if len(set(self.tags)) != len(self.tags):
            issues.append(("duplicate_tag", "tags", "tags repetidas."))
        for position, parameter in enumerate(self.parameters):
            if parameter.reference is not None and parameter.reference >= len(self.references):
                issues.append(("reference_out_of_range", f"parameters[{position}].reference", "referência inexistente."))
        if len({p.name.lower() for p in self.parameters}) != len(self.parameters):
            issues.append(("duplicate_parameter", "parameters", "nome de parâmetro repetido."))
        if self.status == "analyzed" and (self.implementation or self.evidence or self.validation_context):
            issues.append(("analysis_with_implementation", "status", "técnica apenas analisada não declara implementação nem validação."))
        if self.status in ("implemented", "validated") and not self.implementation:
            issues.append(("missing_implementation", "implementation", "implemented/validated exige o componente identificável."))
        if self.status == "implemented" and (self.evidence or self.validation_context):
            issues.append(("implementation_not_validation", "status", "evidência de validação exige o estado validated."))
        if self.status == "validated":
            if not self.evidence or not self.validation_context:
                issues.append(("validated_without_evidence", "evidence", "validated exige contexto e ao menos uma evidência com hash."))
            elif not any(e.kind in ("test", "review") for e in self.evidence):
                issues.append(("validated_without_behavior", "evidence", "uma evidência de comportamento (test ou review) é obrigatória."))
        _issues_to_error(issues)
        return self


class TechniqueCatalog(_Media):
    schema_version: SchemaOne = 1
    sources: list[Source] = Field(max_length=500)
    techniques: list[Technique] = Field(max_length=2000)

    @model_validator(mode="after")
    def cross(self):
        issues: list[tuple[str, str, str]] = []
        by_id = {s.id: s for s in self.sources}
        if len(by_id) != len(self.sources):
            issues.append(("duplicate_source", "sources", "id de fonte repetido."))
        names = [t.name.casefold() for t in self.techniques]
        if len({t.id for t in self.techniques}) != len(self.techniques):
            issues.append(("duplicate_technique", "techniques", "id de técnica repetido."))
        if len(set(names)) != len(names):
            issues.append(("duplicate_name", "techniques", "nome de técnica repetido (sem diferenciar maiúsculas)."))
        for t, technique in enumerate(self.techniques):
            for r, ref in enumerate(technique.references):
                where = f"techniques[{t}].references[{r}]"
                source = by_id.get(ref.source_id)
                if source is None:
                    issues.append(("unknown_source", where, "source_id não existe em sources."))
                elif source.duration_seconds is not None and ref.end > source.duration_seconds + 0.001:
                    issues.append(("reference_beyond_source", where, "o intervalo ultrapassa a duração declarada da fonte."))
        _issues_to_error(issues)
        return self


SCHEMAS = {"technique-catalog.schema.json": TechniqueCatalog}


def validate_technique_catalog(data: Any, *, artifacts: Mapping[str, str] | None = None) -> dict:
    """Valida o catálogo. Com `artifacts` ({caminho: sha256} registrados) cruza o hash das evidências; sem ele, avisa."""
    try:
        model, errors = _parse(TechniqueCatalog, data)
        warnings: list[str] = []
        if model is None:
            return _result(errors, warnings, None)
        if artifacts is None:
            if any(t.evidence for t in model.techniques):
                warnings.append("evidence_not_checked")
        else:
            registered = {str(p).lower(): sha for p, sha in artifacts.items()}
            for t, technique in enumerate(model.techniques):
                for e, item in enumerate(technique.evidence):
                    sha = registered.get(item.path.lower())
                    where = f"techniques[{t}].evidence[{e}]"
                    if sha is None:
                        errors.append(_error("evidence_not_registered", where, "evidência não está entre os artefatos registrados."))
                    elif sha != item.sha256:
                        errors.append(_error("hash_mismatch", where, "sha256 difere do artefato registrado."))
        if any(s.verified_by is None for s in model.sources):
            warnings.append("unverified_sources")
        if any(s.reuse.status != "allowed" for s in model.sources):
            warnings.append("reuse_license_not_confirmed")
        return _result(errors, warnings, model)
    except Exception as error:  # noqa: BLE001 - contrato: validação pura nunca levanta
        return _result([_error("internal_error", "", type(error).__name__)], [], None)


def search_techniques(catalog: Mapping, *, text: str | None = None, category: str | None = None, tags: Sequence[str] = (),
                      status: str | None = None, source_id: str | None = None) -> list[dict]:
    """Busca/filtro em memória sobre um catálogo JÁ validado (`validate_technique_catalog(...)['value']`).

    `text` procura (sem diferenciar maiúsculas) em nome, descrição e tags; `tags` exige todas; ordenação estável por nome.
    """
    needle = text.casefold().strip() if text else None
    found = []
    for technique in catalog["techniques"]:
        if category and technique["category"] != category or status and technique["status"] != status:
            continue
        if tags and not set(tags) <= set(technique["tags"]):
            continue
        if source_id and not any(r["source_id"] == source_id for r in technique["references"]):
            continue
        if needle and needle not in " ".join([technique["name"], technique["description"], *technique["tags"]]).casefold():
            continue
        found.append(technique)
    return sorted(found, key=lambda t: (t["name"].casefold(), t["id"]))
