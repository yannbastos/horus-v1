"""Forma das respostas do fluxo do primeiro vídeo (chaves e tipos), sem response_model e sem código de produção.

Percorre o fluxo com o executor simulado e compara o CONJUNTO DE CHAVES e os TIPOS de cada resposta com um snapshot versionado
(`backend/tests/snapshots/first-video-shapes.json`). Chaves que o frontend lê (derivadas dos tipos TS) são obrigatórias:
sumir, mudar de tipo ou virar opcional é quebra mesmo que o snapshot seja atualizado.
Mudança intencional:  HORUS_UPDATE_API_SNAPSHOT=1 backend/.venv/Scripts/python.exe -m pytest backend/tests/test_first_video_response_shapes.py
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

import pytest

from support_a import client_for, create, pg_admin_connect, review, settings, wait  # noqa: F401  (fixture)
from test_scripts import approved, decide
from test_scripts_manual import doc, manual, version
from test_production import start, finished
from test_license_reviews import body as license_body
from horus_backend.production import inspect_artifact

SNAPSHOT = Path(__file__).parent / "snapshots" / "first-video-shapes.json"
SERVICES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "services"
ARRAY = "__array__"

UPDATE_HELP = ("Se a mudança é intencional, atualize o snapshot conscientemente e inclua-o no commit:\n"
               "    HORUS_UPDATE_API_SNAPSHOT=1 backend/.venv/Scripts/python.exe -m pytest backend/tests/test_first_video_response_shapes.py\n"
               "(revise o diff de backend/tests/snapshots/first-video-shapes.json; o frontend lê estas chaves).")


# --- forma de uma resposta ----------------------------------------------------------------------------------------

def shape(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if value is None:
        return "null"
    if isinstance(value, list):
        merged = None
        for item in value:
            merged = shape(item) if merged is None else merge(merged, shape(item))
        return {ARRAY: merged if merged is not None else "unknown"}
    return {key: shape(item) for key, item in sorted(value.items())}


def label(value):
    if isinstance(value, dict):
        return "array" if ARRAY in value else "object"
    return value


def merge(left, right):
    """União de duas formas do MESMO endpoint/estado: chave ausente de um lado vira opcional ('chave?')."""
    if isinstance(left, dict) and isinstance(right, dict):
        if ARRAY in left and ARRAY in right:
            a, b = left[ARRAY], right[ARRAY]
            return {ARRAY: b if a == "unknown" else a if b == "unknown" else merge(a, b)}
        if ARRAY in left or ARRAY in right:
            return "|".join(sorted({label(left), label(right)}))
        out = {}
        bare = lambda key: key.rstrip("?")  # noqa: E731
        names = {bare(key) for key in (*left, *right)}
        for name in sorted(names):
            in_left = [key for key in (name, name + "?") if key in left]
            in_right = [key for key in (name, name + "?") if key in right]
            optional = not in_left or not in_right or in_left[0].endswith("?") or in_right[0].endswith("?")
            a = left[in_left[0]] if in_left else None
            b = right[in_right[0]] if in_right else None
            out[name + ("?" if optional else "")] = a if b is None else b if a is None else merge(a, b)
        return out
    if left == right:
        return left
    return "|".join(sorted(set(str(label(left)).split("|")) | set(str(label(right)).split("|"))))


def flatten(node, prefix=""):
    """{caminho: tipo}; '?' no nome da chave vira o sufixo ' (opcional)' no valor; arrays usam '[]'."""
    out = {}
    if isinstance(node, dict) and ARRAY in node:
        item = node[ARRAY]
        if isinstance(item, dict):
            out.update(flatten(item, prefix + "[]"))
        else:
            out[prefix + "[]"] = item
        out.setdefault(prefix, "array")
        return out
    if isinstance(node, dict):
        out[prefix or "$"] = "object"
        for key, child in node.items():
            name = key.rstrip("?")
            path = f"{prefix}.{name}" if prefix else name
            child_flat = flatten(child, path)
            if key.endswith("?"):
                child_flat = {k: (v if v.endswith("(opcional)") else v + " (opcional)") for k, v in child_flat.items()}
            out.update(child_flat)
        return out
    out[prefix or "$"] = node
    return out


# --- gravação do fluxo --------------------------------------------------------------------------------------------

class Recorder:
    """Embrulha o TestClient: cada chamada deliberada registra a forma JSON da resposta por 'MÉTODO /rota status [estado]'."""

    def __init__(self, client):
        self.client, self.shapes = client, {}

    @staticmethod
    def template(url):
        path = url.split("?")[0]
        path = re.sub(r"/[0-9a-f]{64}(?=/|$)", "/{hash}", path)
        return re.sub(r"/[0-9a-f]{32}(?=/|$)", "/{id}", path)

    def call(self, method, url, state=None, **kwargs):
        response = self.client.request(method, url, **kwargs)
        key = f"{method} {self.template(url)} {response.status_code}" + (f" [{state}]" if state else "")
        if "application/json" in response.headers.get("content-type", ""):
            value = shape(response.json())
        else:
            value = {"__binary__": response.headers.get("content-type", "").split(";")[0]}
        self.shapes[key] = merge(self.shapes[key], value) if key in self.shapes else value
        return response

    def get(self, url, **kwargs):
        return self.call("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.call("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self.call("PUT", url, **kwargs)


def run_flow(settings, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        rc = Recorder(client)
        # 1) proposta aprovada (executor simulado; nada chama o Hermes)
        created = rc.post("/v1/workflows", headers={"Idempotency-Key": "forma-1"},
                          json={"objective": "Vídeo curto de teste", "references": []})
        assert created.status_code == 202, created.text
        workflow = wait(client, created.json()["id"])
        rc.get(f"/v1/workflows/{workflow['id']}", state="aguardando aprovação")
        approved_review = rc.post(f"/v1/workflows/{workflow['id']}/review", headers={"Idempotency-Key": "forma-rev"},
                                  json={"expected_revision": workflow["revision"], "artifact_id": workflow["current_artifact_id"], "decision": "approve"})
        assert approved_review.status_code == 200, approved_review.text
        workflow = client.get(f"/v1/workflows/{workflow['id']}").json()
        # 2) roteiro manual, pedido de alterações, nova versão humana, aprovação
        first = rc.post(f"/v1/workflows/{workflow['id']}/scripts", headers={"Idempotency-Key": "forma-s1"},
                        json={"source": "manual", "expected_revision": workflow["revision"],
                              "proposal_artifact_id": workflow["current_artifact_id"], "document": doc()})
        assert first.status_code == 201, first.text
        script = first.json()
        rc.get(f"/v1/workflows/{workflow['id']}/scripts", state="uma versão")
        changes = rc.post(f"/v1/scripts/{script['id']}/review", headers={"Idempotency-Key": "forma-sr1"},
                          json={"expected_version": 1, "decision": "request_changes", "feedback": "Ajustar o gancho."}, state="alterações")
        assert changes.status_code == 200, changes.text
        second = rc.post(f"/v1/scripts/{script['id']}/versions", headers={"Idempotency-Key": "forma-v2"},
                         json={"expected_version": 1, "document": doc(title="Meu primeiro vídeo v2")})
        assert second.status_code == 201, second.text
        script2 = second.json()
        rc.get(f"/v1/scripts/{script['id']}", state="v1")
        rc.get(f"/v1/workflows/{workflow['id']}/scripts", state="duas versões")
        ok = rc.post(f"/v1/scripts/{script2['id']}/review", headers={"Idempotency-Key": "forma-sr2"},
                     json={"expected_version": script2["version"], "decision": "approve"}, state="aprovado")
        assert ok.status_code == 200, ok.text
        # 3) produção simulada
        started = rc.post(f"/v1/scripts/{script2['id']}/productions", headers={"Idempotency-Key": "forma-p"},
                          json={"expected_version": script2["version"], "mode": "simulated"})
        assert started.status_code == 201, started.text
        record = finished(client, started.json())
        rc.get(f"/v1/productions/{record['id']}", state="simulada concluída")
        rc.get(f"/v1/scripts/{script2['id']}/productions", state="uma produção")
        # 4) vídeo, proveniência e modo piloto registrados como no ensaio (sem render real) para revisar e exportar
        root = settings.data_dir / "productions" / record["id"]
        (root / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftyp" + b"fixture-video")
        artifacts = record["artifacts"] + [inspect_artifact(root, "video.mp4", "video")]
        scene = next(a for a in artifacts if a["kind"] == "scene")
        provenance = {"schema_version": 1, "assets": [{
            "asset_id": ("scene-" if a["kind"] == "scene" else "audio-") + str(index), "relative_path": a["relative_path"],
            "sha256": a["sha256"], "kind": "image" if a["kind"] == "scene" else "audio", "origin": "original_local",
            "source_url": None, "author": None,
            "license": {"id": None, "name": None, "url": None, "commercial_use": "unknown", "attribution_required": None,
                        "verified_by": None, "verified_at": None}, "status": "unverified", "notes": ""}
            for index, a in enumerate([a for a in artifacts if a["kind"] in {"scene", "audio"}], start=1)]}
        (root / "asset-provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
        artifacts.append(inspect_artifact(root, "asset-provenance.json", "manifest"))
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            db.execute("UPDATE horus.productions SET mode='local_pilot', artifacts=%s::jsonb WHERE id=%s", (json.dumps(artifacts), record["id"]))
        asset = next(a for a in provenance["assets"] if a["sha256"] == scene["sha256"])
        # 5) licença, revisão humana da produção, exportação
        rc.get(f"/v1/productions/{record['id']}/license-reviews", state="sem decisões")
        license_review = rc.post(f"/v1/productions/{record['id']}/license-reviews", headers={"Idempotency-Key": "forma-l"},
                                 json=license_body(asset_id=asset["asset_id"], asset_sha256=scene["sha256"]))
        assert license_review.status_code == 201, license_review.text
        rc.get(f"/v1/productions/{record['id']}/license-reviews", state="com decisão")
        video_sha = hashlib.sha256((root / "video.mp4").read_bytes()).hexdigest()
        decided = rc.post(f"/v1/productions/{record['id']}/review", headers={"Idempotency-Key": "forma-pr"},
                          json={"decision": "approve", "artifact_sha256": video_sha, "acknowledge_quality_errors": True, "notes": []})
        assert decided.status_code == 200, decided.text
        rc.get(f"/v1/productions/{record['id']}", state="aprovada")
        exported = rc.post(f"/v1/productions/{record['id']}/export", headers={"Idempotency-Key": "forma-e"})
        assert exported.status_code == 201, exported.text
        assert rc.post(f"/v1/productions/{record['id']}/export", headers={"Idempotency-Key": "forma-e"}, state="repetida").status_code == 200
        rc.get(f"/v1/productions/{record['id']}/exports")
        rc.get(f"/v1/productions/{record['id']}/exports/{exported.json()['id']}/file")
        rc.get(f"/v1/productions/{record['id']}/metadata", state="rascunho")
        rc.put(f"/v1/productions/{record['id']}/metadata", json={"title": "Título", "description": "Descrição"})
        # 6) telas de apoio do fluxo
        rc.get("/v1/narration/providers")
        rc.get("/v1/narration/preference")
        rc.get("/v1/diagnostics")
        return rc.shapes


# --- chaves que o frontend lê (derivadas dos tipos TS) ------------------------------------------------------------

SCRIPT_ENDPOINTS = ["POST /v1/workflows/{id}/scripts 201", "POST /v1/scripts/{id}/versions 201", "GET /v1/scripts/{id} 200 [v1]",
                    "POST /v1/scripts/{id}/review 200 [aprovado]"]
PRODUCTION_ENDPOINTS = ["POST /v1/scripts/{id}/productions 201", "GET /v1/productions/{id} 200 [simulada concluída]",
                        "GET /v1/productions/{id} 200 [aprovada]", "POST /v1/productions/{id}/review 200"]
#            (arquivo TS, tipo TS, endpoint, prefixo do caminho na resposta)
FRONTEND_MAP = (
    [("scripts.ts", "ScriptRecord", e, "") for e in SCRIPT_ENDPOINTS]
    + [("scripts.ts", "ScriptDocument", e, "document") for e in SCRIPT_ENDPOINTS]
    + [("scripts.ts", "ScriptSection", e, "document.sections[]") for e in SCRIPT_ENDPOINTS]
    + [("scripts.ts", "ScriptRecord", "GET /v1/workflows/{id}/scripts 200 [duas versões]", "items[]")]
    + [("scripts.ts", "ScriptReviewInfo", "POST /v1/scripts/{id}/review 200 [aprovado]", "review")]
    + [("production.ts", "ProductionRecord", e, "") for e in PRODUCTION_ENDPOINTS]
    # Na criação (201) a produção está na fila e `artifacts` é uma lista vazia: sem elementos para conferir.
    + [("production.ts", "ProductionArtifact", e, "artifacts[]") for e in PRODUCTION_ENDPOINTS[1:]]
    + [("production.ts", "ProductionRecord", "GET /v1/scripts/{id}/productions 200 [uma produção]", "items[]")]
    + [("production.ts", "ProductionReview", "POST /v1/productions/{id}/review 200", "review"),
       ("production.ts", "ProductionReview", "GET /v1/productions/{id} 200 [aprovada]", "review")]
    + [("exports.ts", "ProductionExportItem", "POST /v1/productions/{id}/export 201", ""),
       ("exports.ts", "ProductionExportItem", "POST /v1/productions/{id}/export 200 [repetida]", ""),
       ("exports.ts", "ProductionExportItem", "GET /v1/productions/{id}/exports 200", "items[]")]
    + [("licenseReviews.ts", "LicenseReviewsResponse", "GET /v1/productions/{id}/license-reviews 200 [com decisão]", ""),
       ("licenseReviews.ts", "LicenseReview", "GET /v1/productions/{id}/license-reviews 200 [com decisão]", "current[]"),
       ("licenseReviews.ts", "ReviewLicense", "GET /v1/productions/{id}/license-reviews 200 [com decisão]", "current[].license")]
    + [("narration.ts", "DefaultVoice", "GET /v1/narration/preference 200", "")]
)


def ts_required(file, type_name):
    """Campos NÃO opcionais do nível superior de `export type Nome = { ... }`; None se o frontend não estiver disponível."""
    path = SERVICES / file
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    match = re.search(r"export type " + re.escape(type_name) + r" = \{(.*?)\n\};", text, re.S) or \
        re.search(r"export type " + re.escape(type_name) + r" = \{([^}]*)\}", text)
    assert match, f"Tipo TS {type_name} não encontrado em {file}"
    names = []
    depth = 0
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if depth == 0:
            found = re.match(r"([A-Za-z_][A-Za-z0-9_]*)(\??):", stripped)
            if found and not found.group(2):
                names.append(found.group(1))
        depth += stripped.count("{") - stripped.count("}")
    return names


def derive_required():
    required = {}
    for file, type_name, endpoint, prefix in FRONTEND_MAP:
        names = ts_required(file, type_name)
        if names is None:
            return None
        required.setdefault(endpoint, []).extend(f"{prefix}.{name}" if prefix else name for name in names)
    return {endpoint: sorted(set(paths)) for endpoint, paths in sorted(required.items())}


def required_problems(shapes, required):
    problems = []
    for endpoint, paths in required.items():
        facts = flatten(shapes[endpoint]) if endpoint in shapes else {}
        for path in paths:
            if path not in facts:
                problems.append(f"{endpoint}: o frontend lê '{path}' e a resposta não tem essa chave")
            elif facts[path].endswith("(opcional)"):
                problems.append(f"{endpoint}: '{path}' é lida pelo frontend e passou a ser opcional (ausente em algum estado)")
    return problems


def all_facts(shapes):
    return {f"{endpoint} :: {path}": kind for endpoint, node in shapes.items() for path, kind in flatten(node).items()}


def classify(old, new):
    breaking, additive = [], []
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key), new.get(key)
        if before == after:
            continue
        if before is None:
            additive.append(f"+ {key}: {after}")
        elif after is None:
            breaking.append(f"- {key}: {before}")
        else:
            breaking.append(f"~ {key}: {before}  ->  {after}")
    return breaking, additive


# --- testes -----------------------------------------------------------------------------------------------------

def test_first_video_response_shapes_match_the_snapshot(settings, pg_app_cluster, pg_app_database):
    shapes = run_flow(settings, pg_app_cluster, pg_app_database)
    required = derive_required()
    if os.environ.get("HORUS_UPDATE_API_SNAPSHOT") == "1":
        if required is None:
            required = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["frontend_required"]
        problems = required_problems(shapes, required)
        assert not problems, "Não é possível gravar o snapshot: o frontend depende de chaves ausentes:\n" + "\n".join(problems)
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps({"schema": 1, "shapes": shapes, "frontend_required": required}, ensure_ascii=False,
                                       indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        pytest.skip("Snapshot das respostas atualizado por HORUS_UPDATE_API_SNAPSHOT=1; revise o diff e commite.")
    assert SNAPSHOT.is_file(), "Snapshot ausente. " + UPDATE_HELP
    saved = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    lines = required_problems(shapes, saved["frontend_required"])
    if lines:
        pytest.fail("QUEBRA PARA O FRONTEND (chaves obrigatórias):\n" + "\n".join(lines) + "\n\n" + UPDATE_HELP, pytrace=False)
    if required is not None and required != saved["frontend_required"]:
        pytest.fail("O frontend mudou os campos não opcionais dos tipos TS; atualize a lista de obrigatórios.\n" + UPDATE_HELP, pytrace=False)
    if saved["shapes"] == shapes:
        return
    breaking, additive = classify(all_facts(saved["shapes"]), all_facts(shapes))
    out = []
    if breaking:
        out += ["QUEBRA DE CONTRATO (chave removida, tipo alterado, resposta ausente ou chave que ficou opcional):", *breaking]
    if additive:
        out += ["MUDANÇA ADITIVA (chave nova ou resposta nova):", *additive]
    pytest.fail("\n".join(out[:200]) + "\n\n" + UPDATE_HELP, pytrace=False)


def test_snapshot_covers_the_whole_first_video_flow_and_the_frontend_keys():
    saved = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    endpoints = " ".join(saved["shapes"])
    for expected in ("POST /v1/workflows 202", "/scripts 201", "/versions 201", "/review 200", "/productions 201", "/license-reviews 201",
                     "/export 201", "/exports 200", "/metadata 200", "/narration/providers 200", "/narration/preference 200", "/diagnostics 200"):
        assert expected in endpoints, expected
    assert set(saved["frontend_required"]) <= set(saved["shapes"])
    for endpoint, paths in saved["frontend_required"].items():
        assert paths, endpoint
        facts = flatten(saved["shapes"][endpoint])
        assert all(path in facts and not facts[path].endswith("(opcional)") for path in paths), endpoint


def test_required_keys_come_from_the_ts_types_when_the_frontend_is_present():
    derived = derive_required()
    if derived is None:
        pytest.skip("frontend/src/services ausente")
    assert derived == json.loads(SNAPSHOT.read_text(encoding="utf-8"))["frontend_required"]
    assert "id" in derived["POST /v1/workflows/{id}/scripts 201"] and "document.sections[].narration" in derived["GET /v1/scripts/{id} 200 [v1]"]


def test_comparator_separates_breaking_from_additive_and_detects_optional_drift():
    old = {"E 200": shape({"id": "x", "n": 1, "tags": [{"a": "x"}], "gone": True})}
    new = {"E 200": shape({"id": "x", "n": "um", "tags": [{"a": "x", "b": 2}], "extra": 1})}
    breaking, additive = classify(all_facts(old), all_facts(new))
    assert any("gone" in line for line in breaking) and any("n:" in line and "integer  ->  string" in line for line in breaking)
    assert any("tags[].b" in line for line in additive) and any("extra" in line for line in additive)
    merged = merge(shape({"id": "x", "opt": 1}), shape({"id": "y"}))
    assert flatten(merged)["opt"] == "integer (opcional)" and flatten(merged)["id"] == "string"
    problems = required_problems({"E 200": merged}, {"E 200": ["id", "opt", "faltando"]})
    assert len(problems) == 2 and any("opcional" in p for p in problems) and any("faltando" in p for p in problems)
    assert merge(shape({"a": 1}), shape({"a": None}))["a"] == "integer|null"
    assert shape([]) == {ARRAY: "unknown"} and merge(shape([]), shape([1])) == {ARRAY: "integer"}


def test_flow_is_deterministic(settings, pg_app_cluster, pg_app_database, tmp_path, monkeypatch):
    from dataclasses import replace
    from pg_app_support import _patch_pg_transport
    from keyring_support import MemoryKeyring
    from schema_support import schema_database

    first = run_flow(settings, pg_app_cluster, pg_app_database)
    with schema_database(pg_app_cluster) as (admin, pool):
        with pool.connection() as conn:
            other_db = conn.info.dbname
        admin.execute(
            "INSERT INTO horus.database_identity(singleton, instance_id, created_at) "
            "VALUES (true, gen_random_uuid(), now()) ON CONFLICT (singleton) DO NOTHING"
        )
        _patch_pg_transport(pg_app_cluster, other_db, MemoryKeyring(), monkeypatch)
        other = replace(settings, data_dir=tmp_path / "outra-base")
        assert run_flow(other, pg_app_cluster, other_db) == first
