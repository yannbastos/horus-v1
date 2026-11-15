"""HOR-058: contrato do catálogo de técnicas (fonte verificável + timecode). Puro: sem rede, disco real ou fábrica.

As fontes abaixo são FICTÍCIAS (exemplo.com é domínio reservado a exemplos): o catálogo real começa vazio.
"""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from horus_backend import motion_library as ml

ROOT = Path(__file__).resolve().parents[2]
H1, H2 = "a" * 64, "b" * 64


def source(**changes):
    value = {"id": "src-1", "kind": "external_video", "title": "Vídeo de exemplo (fictício)", "url": "https://exemplo.com/watch?v=1",
             "author": "Canal fictício", "duration_seconds": 120.0, "retrieved_at": "2026-11-15T10:00:00Z",
             "verified_by": "human", "verified_at": "2026-11-15T10:05:00Z"}
    return value | changes


def technique(**changes):
    value = {"id": "ken-burns-lento", "name": "Ken Burns lento", "description": "Zoom contínuo e suave sobre imagem estática.",
             "category": "camera", "tags": ["zoom", "imagem"],
             "references": [{"source_id": "src-1", "start": 12.5, "end": 18.0, "note": "plano de abertura"}],
             "parameters": [{"name": "escala_final", "value": 1.15, "min": 1.0, "max": 1.5, "certainty": "measured", "reference": 0},
                            {"name": "easing", "value": "ease-in-out", "certainty": "inferred"}],
             "status": "analyzed"}
    return value | changes


def catalog(*techniques, sources=None):
    return {"schema_version": 1, "sources": sources if sources is not None else [source()],
            "techniques": list(techniques) or [technique()]}


def codes(result):
    return {e["code"] for e in result["errors"]}


def implemented(**changes):
    return technique(status="implemented", implementation={"component": "KenBurns", "factory_path": "remotion/src/KenBurns.tsx"}) | changes


def validated():
    return implemented(status="validated", validation_context="Short9x16 rascunho, 540x960, 30 fps",
                       evidence=[{"kind": "test", "path": "evidencias/kenburns.json", "sha256": H2, "description": "comparação de quadros"}])


def test_valid_catalog_normalizes_and_never_claims_publication_or_reuse():
    result = ml.validate_technique_catalog(catalog())
    assert result["ok"] and result["publishable"] is False and result["value"]["techniques"][0]["status"] == "analyzed"
    assert "reuse_license_not_confirmed" in result["warnings"]     # analisar não prova licença
    assert result["value"]["sources"][0]["reuse"]["status"] == "unknown"


def test_empty_catalog_is_valid_and_is_the_real_starting_point():
    assert ml.validate_technique_catalog({"schema_version": 1, "sources": [], "techniques": []})["ok"]


@pytest.mark.parametrize('change,code', [
    ({"url": None}, 'missing_url'), ({"url": "http://exemplo.com/v"}, 'invalid_url'),
    ({"url": "https://user:pw@exemplo.com/v"}, 'invalid_url'), ({"url": "https://exemplo.com/a b"}, 'invalid_url'),
    ({"verified_at": None}, 'verification_incomplete'), ({"title": "ok‮txt"}, 'unsafe_text'),
    ({"retrieved_at": "ontem"}, 'schema_value_error'),
    ({"kind": "local_media", "url": None}, 'missing_hash'),
    ({"kind": "local_media", "media_sha256": H1}, 'unexpected_url'),
    ({"reuse": {"status": "allowed"}}, 'allowed_without_human'),
])
def test_source_must_be_traceable(change, code):
    result = ml.validate_technique_catalog(catalog(sources=[source(**change)]))
    assert not result["ok"] and code in codes(result), result["errors"]


def test_local_media_source_is_valid_with_hash():
    local = source(kind="local_media", url=None, media_sha256=H1)
    assert ml.validate_technique_catalog(catalog(sources=[local]))["ok"]


@pytest.mark.parametrize('references,code', [
    ([{"source_id": "src-1", "start": 5.0, "end": 5.0}], 'interval_too_short'),
    ([{"source_id": "src-1", "start": 9.0, "end": 3.0}], 'interval_too_short'),
    ([{"source_id": "src-1", "start": 100.0, "end": 130.0}], 'reference_beyond_source'),
    ([{"source_id": "nao-existe", "start": 1.0, "end": 2.0}], 'unknown_source'),
    ([], 'schema_too_short'),
])
def test_every_technique_needs_a_valid_timecode_reference(references, code):
    result = ml.validate_technique_catalog(catalog(technique(references=references)))
    assert not result["ok"] and code in codes(result), result["errors"]


def test_missing_reference_field_and_nan_or_bool_times_are_refused():
    for bad in ({"source_id": "src-1", "start": float('nan'), "end": 2.0}, {"source_id": "src-1", "start": True, "end": 2.0},
                {"source_id": "src-1", "start": "1", "end": 2.0}, {"source_id": "src-1", "start": 1.0}):
        assert not ml.validate_technique_catalog(catalog(technique(references=[bad])))["ok"]


def test_timecode_helpers_round_trip():
    assert ml.format_timecode(3725.5) == "01:02:05.500" and ml.parse_timecode("01:02:05.5") == 3725.5
    assert ml.parse_timecode("00:00:12") == 12.0
    for bad in ("1:2", "00:61:00", "abc", ""):
        with pytest.raises(ValueError):
            ml.parse_timecode(bad)
    reference = ml.Reference(source_id="s", start=12.5, end=18.0)
    assert reference.timecode == "00:00:12.500-00:00:18.000"


@pytest.mark.parametrize('parameter,code', [
    ({"name": "x", "value": 2.0, "min": 0, "max": 1, "certainty": "inferred"}, 'value_out_of_limits'),
    ({"name": "x", "value": 0.5, "min": 2, "max": 1, "certainty": "inferred"}, 'limits_inverted'),
    ({"name": "x", "value": "texto", "min": 0, "certainty": "inferred"}, 'limits_on_non_numeric'),
    ({"name": "x", "value": 1, "certainty": "measured"}, 'measured_without_reference'),
    ({"name": "x", "value": 1, "certainty": "measured", "reference": 3}, 'reference_out_of_range'),
])
def test_parameters_distinguish_measured_from_inferred_and_respect_limits(parameter, code):
    result = ml.validate_technique_catalog(catalog(technique(parameters=[parameter])))
    assert not result["ok"] and code in codes(result), result["errors"]


def test_maturity_rules_keep_analysis_implementation_and_validation_apart():
    assert ml.validate_technique_catalog(catalog(implemented()))["ok"]
    assert ml.validate_technique_catalog(catalog(validated()), artifacts={"evidencias/kenburns.json": H2})["ok"]
    cases = [(technique(implementation={"component": "KenBurns", "factory_path": "remotion/src/KenBurns.tsx"}), 'analysis_with_implementation'),
             (technique(status="implemented"), 'missing_implementation'),
             (implemented(validation_context="x"), 'implementation_not_validation'),
             (validated() | {"evidence": []}, 'validated_without_evidence'),
             (validated() | {"evidence": [{"kind": "render", "path": "a/b.mp4", "sha256": H2, "description": "só um render isolado"}]},
              'validated_without_behavior'),
             (implemented(implementation={"component": "KenBurns", "factory_path": "../fora.tsx"}), 'unsafe_path'),
             (implemented(implementation={"component": "nome inválido!", "factory_path": "a.tsx"}), 'schema_string_pattern_mismatch')]
    for item, code in cases:
        result = ml.validate_technique_catalog(catalog(item))
        assert not result["ok"] and code in codes(result), (code, result["errors"])


def test_evidence_hashes_are_cross_checked_against_registered_artifacts():
    doc = catalog(validated())
    assert "evidence_not_checked" in ml.validate_technique_catalog(doc)["warnings"]
    assert 'hash_mismatch' in codes(ml.validate_technique_catalog(doc, artifacts={"evidencias/kenburns.json": H1}))
    assert 'evidence_not_registered' in codes(ml.validate_technique_catalog(doc, artifacts={}))


def test_catalog_rejects_duplicates_and_forbidden_fields():
    assert 'duplicate_source' in codes(ml.validate_technique_catalog(catalog(sources=[source(), source()])))
    assert 'duplicate_technique' in codes(ml.validate_technique_catalog(catalog(technique(), technique(name="Outro"))))
    assert 'duplicate_name' in codes(ml.validate_technique_catalog(catalog(technique(), technique(id="b", name="KEN BURNS LENTO"))))
    for extra in ({"publishable": True}, {"approved": True}):
        assert not ml.validate_technique_catalog(catalog() | extra)["ok"]
        assert not ml.validate_technique_catalog(catalog(technique(**extra)))["ok"]
    assert not ml.validate_technique_catalog(catalog() | {"schema_version": True})["ok"]


def test_validation_never_raises_on_garbage():
    for garbage in (None, [], "x", 3, {"schema_version": 1}, {"schema_version": 1, "sources": [None], "techniques": [[]]}):
        assert ml.validate_technique_catalog(garbage)["ok"] is False


def test_search_and_filter_over_a_validated_catalog():
    doc = catalog(technique(), implemented(id="legenda", name="Legenda por palavra", description="Palavra a palavra sincronizada.",
                                            category="text", tags=["legenda"]), validated() | {"id": "v", "name": "Zoom validado", "tags": ["zoom"]})
    value = ml.validate_technique_catalog(doc, artifacts={"evidencias/kenburns.json": H2})["value"]
    assert [t["id"] for t in ml.search_techniques(value)] == ["ken-burns-lento", "legenda", "v"]
    assert [t["id"] for t in ml.search_techniques(value, text="PALAVRA")] == ["legenda"]
    assert [t["id"] for t in ml.search_techniques(value, tags=["zoom"])] == ["ken-burns-lento", "v"]
    assert [t["id"] for t in ml.search_techniques(value, status="validated")] == ["v"]
    assert [t["id"] for t in ml.search_techniques(value, category="text", source_id="src-1")] == ["legenda"]
    assert ml.search_techniques(value, text="inexistente") == []


def test_exported_schema_is_in_sync_and_module_is_isolated_from_runtime():
    expected = json.dumps(ml.TechniqueCatalog.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert (ROOT / "contracts/motion/technique-catalog.schema.json").read_text(encoding="utf-8") == expected, \
        "rode contracts/motion/generate_schemas.py"
    source_text = (ROOT / "backend/src/horus_backend/motion_library.py").read_text(encoding="utf-8")
    import ast
    imported = {n.module.split(".")[0] if isinstance(n, ast.ImportFrom) and n.level == 0 else (n.names[0].name.split(".")[0] if isinstance(n, ast.Import) else "")
                for n in ast.walk(ast.parse(source_text)) if isinstance(n, (ast.Import, ast.ImportFrom))}
    assert imported <= {"__future__", "re", "typing", "pydantic", ""}      # só stdlib pura + pydantic (e o pacote, relativo)
    assert "open(" not in source_text
    api = (ROOT / "backend/src/horus_backend/api.py").read_text(encoding="utf-8")
    assert "motion_library" not in api      # não ligado à API nem ao fluxo de produção neste incremento
