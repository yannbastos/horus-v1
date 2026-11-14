"""Contrato da API: compara o OpenAPI gerado com um snapshot versionado (sem rede, sem banco real).

Falha com diff legível. Quebra (rota/método/status removido, campo obrigatório novo no pedido, campo removido ou de tipo
alterado na resposta) e mudança aditiva (rota nova, campo opcional novo) falham igualmente; a mensagem diz como atualizar o
snapshot conscientemente:  HORUS_UPDATE_API_SNAPSHOT=1 pytest backend/tests/test_api_contract_snapshot.py
"""
import difflib
import json
import os
from pathlib import Path

import pytest

from horus_backend.api import create_app
from horus_backend.config import Settings

SNAPSHOT = Path(__file__).parent / "snapshots" / "openapi.json"
METHODS = ("get", "post", "put", "delete", "patch")


def current_openapi(tmp_path):
    settings = Settings(token="snapshot-test-token-0123456789", data_dir=tmp_path / "data", factory_root=None,
                        hermes_executable=None, hermes_config=tmp_path / "hermes.json")
    return normalize(create_app(settings).openapi())


def normalize(value):
    """Ordem estável: chaves ordenadas; listas de `required` e `tags` ordenadas; resto preservado."""
    if isinstance(value, dict):
        return {key: normalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        items = [normalize(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True)) if all(isinstance(i, str) for i in items) else items
    return value


def dump(spec):
    return json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# --- extração de fatos do contrato --------------------------------------------------------------------------------

def resolve(spec, schema):
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema and seen < 20:
        node = spec
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        schema, seen = node, seen + 1
    return schema or {}


def kind(spec, schema, depth=0):
    schema = resolve(spec, schema)
    if "anyOf" in schema or "oneOf" in schema:
        options = schema.get("anyOf") or schema.get("oneOf")
        return "|".join(sorted(kind(spec, option, depth + 1) for option in options))
    if "enum" in schema:
        return "enum(" + ",".join(map(str, schema["enum"])) + ")"
    if "const" in schema:
        return f"const({schema['const']})"
    value = schema.get("type")
    if value == "array":
        return "array<" + kind(spec, schema.get("items", {}), depth + 1) + ">"
    if value is None:
        return "object" if "properties" in schema else "any"
    return value + (f":{schema['format']}" if "format" in schema else "")


def fields(spec, schema, prefix="", depth=0, trail=()):
    """{nome: (tipo, obrigatório)} com aninhamento limitado e proteção contra ciclos."""
    out = {}
    node = resolve(spec, schema)
    if "anyOf" in node or "oneOf" in node:
        for option in node.get("anyOf") or node.get("oneOf"):
            out.update(fields(spec, option, prefix, depth, trail))
        return out
    if node.get("type") == "array":
        return fields(spec, node.get("items", {}), prefix + "[]", depth, trail)
    ref = schema.get("$ref") if isinstance(schema, dict) else None
    if ref in trail or depth > 4:
        return out
    required = set(node.get("required", []))
    for name, child in (node.get("properties") or {}).items():
        path = f"{prefix}.{name}" if prefix else name
        out[path] = (kind(spec, child), name in required)
        resolved = resolve(spec, child)
        if "properties" in resolved or resolved.get("type") == "array" or "anyOf" in resolved:
            out.update(fields(spec, child, path, depth + 1, (*trail, ref)))
    return out


def facts(spec):
    """Chave legível -> valor. Cada entrada é um pedaço do contrato que um cliente pode usar."""
    result = {}
    for path, item in spec["paths"].items():
        for method in METHODS:
            operation = item.get(method)
            if operation is None:
                continue
            route = f"{method.upper()} {path}"
            result[f"rota {route}"] = "existe"
            for parameter in operation.get("parameters", []):
                result[f"parâmetro {route} {parameter['in']}:{parameter['name']}"] = (
                    ("obrigatório" if parameter.get("required") else "opcional") + " " + kind(spec, parameter.get("schema", {})))
            for media, body in (operation.get("requestBody", {}).get("content", {}) or {}).items():
                for name, (type_, required) in fields(spec, body.get("schema", {})).items():
                    result[f"pedido {route} [{media}] campo {name}"] = ("obrigatório" if required else "opcional") + " " + type_
                if not fields(spec, body.get("schema", {})):
                    result[f"pedido {route} [{media}] corpo"] = kind(spec, body.get("schema", {}))
            if operation.get("requestBody", {}).get("required"):
                result[f"pedido {route} corpo"] = "obrigatório"
            for code, response in operation.get("responses", {}).items():
                result[f"status {route} {code}"] = "existe"
                for media, body in (response.get("content", {}) or {}).items():
                    schema = body.get("schema", {})
                    found = fields(spec, schema)
                    for name, (type_, _) in found.items():
                        result[f"resposta {route} {code} [{media}] campo {name}"] = type_
                    if not found:
                        result[f"resposta {route} {code} [{media}] corpo"] = kind(spec, schema)
    return result


def classify(old, new):
    breaking, additive = [], []
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key), new.get(key)
        if before == after:
            continue
        if before is None:
            required_new = str(after).startswith("obrigatório") and key.startswith(("pedido", "parâmetro"))
            (breaking if required_new else additive).append(f"+ {key}: {after}")
        elif after is None:
            breaking.append(f"- {key}: {before}")
        else:
            breaking.append(f"~ {key}: {before}  ->  {after}")
    return breaking, additive


UPDATE_HELP = ("Se a mudança é intencional, atualize o snapshot conscientemente e inclua-o no commit:\n"
               "    HORUS_UPDATE_API_SNAPSHOT=1 backend/.venv/Scripts/python.exe -m pytest backend/tests/test_api_contract_snapshot.py\n"
               "(revise o diff de backend/tests/snapshots/openapi.json antes de commitar; clientes e frontend dependem destas rotas).")


def test_openapi_matches_the_versioned_snapshot(tmp_path):
    spec = current_openapi(tmp_path)
    if os.environ.get("HORUS_UPDATE_API_SNAPSHOT") == "1":
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(dump(spec), encoding="utf-8", newline="\n")
        pytest.skip("Snapshot da API atualizado por HORUS_UPDATE_API_SNAPSHOT=1; revise o diff e commite.")
    assert SNAPSHOT.is_file(), "Snapshot ausente. " + UPDATE_HELP
    saved = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if saved == spec:
        return
    breaking, additive = classify(facts(saved), facts(spec))
    lines = []
    if breaking:
        lines += ["QUEBRA DE CONTRATO (rota/método/status removido ou renomeado, campo obrigatório novo, campo removido ou tipo alterado):", *breaking]
    if additive:
        lines += ["MUDANÇA ADITIVA (rota nova, status novo ou campo opcional novo):", *additive]
    if not lines:
        diff = difflib.unified_diff(dump(saved).splitlines(), dump(spec).splitlines(), "snapshot", "atual", lineterm="", n=2)
        lines = ["O OpenAPI mudou só em detalhes (títulos, descrições, exemplos):", *list(diff)[:60]]
    pytest.fail("\n".join(lines[:200]) + "\n\n" + UPDATE_HELP, pytrace=False)


# --- o próprio comparador precisa ser confiável --------------------------------------------------------------------

def spec_with(request_required=("a",), response_props=("x",), responses=("200",), paths=("/v1/x",)):
    def schema(props, required=()):
        return {"type": "object", "properties": {name: {"type": "string"} for name in props}, "required": list(required)}
    operation = {"requestBody": {"required": True, "content": {"application/json": {"schema": schema(("a", "b"), request_required)}}},
                 "responses": {code: {"description": "ok", "content": {"application/json": {"schema": schema(response_props)}}}
                               for code in responses}}
    return {"paths": {path: {"post": operation} for path in paths}}


def verdict(old, new):
    return classify(facts(old), facts(new))


def test_comparator_flags_every_breaking_change_and_separates_additive_ones():
    base = spec_with()
    breaking, additive = verdict(base, spec_with(paths=("/v1/y",)))
    assert any("rota POST /v1/x" in line for line in breaking) and any("rota POST /v1/y" in line for line in additive)
    breaking, _ = verdict(base, spec_with(responses=("201",)))
    assert any("status POST /v1/x 200" in line for line in breaking)
    breaking, additive = verdict(base, spec_with(request_required=("a", "b")))
    assert any("campo b" in line and "obrigatório" in line for line in breaking) and not additive
    breaking, additive = verdict(base, spec_with(response_props=("x", "novo")))
    assert not breaking and any("campo novo" in line for line in additive)
    breaking, _ = verdict(base, spec_with(response_props=()))
    assert any("campo x" in line for line in breaking)
    changed = spec_with()
    changed["paths"]["/v1/x"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["x"] = {"type": "integer"}
    breaking, _ = verdict(base, changed)
    assert any("string  ->  integer" in line for line in breaking)
    optional_new = spec_with(request_required=("a",))
    assert verdict(base, optional_new) == ([], [])


def test_openapi_generation_is_deterministic(tmp_path):
    first = dump(current_openapi(tmp_path / "um"))
    second = dump(current_openapi(tmp_path / "dois"))
    assert first == second and "127.0.0.1" not in first and str(tmp_path) not in first
