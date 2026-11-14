"""HOR-099/HOR-056: pendências baixas — chave que falha fica registrada, texto do usuário não some, hash fora da transação."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from test_workflows import settings  # noqa: F401  (fixture)
from test_exports import production, approve, export  # noqa: F401
from test_publishing import tools, thumbnail  # noqa: F401
from test_publishing_adversarial import fake_run, put_metadata, registered_artifacts
from horus_backend import exports as exports_module
from horus_backend import publishing as publishing_module
from horus_backend.publishing import FAILED_MARKER

VIDEO = b"\x00\x00\x00\x18ftyp" + b"fixture-video"


def folders(settings, production_id):
    base = settings.data_dir / "exports" / production_id
    return sorted(base.iterdir()) if base.exists() else []


# --- (1) chave que falha -----------------------------------------------------------------------------------------

def test_export_failed_during_copy_is_recorded_for_that_key_and_new_key_works(production, monkeypatch):
    client, record, root, settings = production
    approve(client, record)
    real = exports_module.inspect_artifact

    def swap_after_verification(source, relative, kind):
        result = real(source, relative, kind)
        if kind == "video":
            (source / relative).write_bytes(b"\x00\x00\x00\x18ftypTROCADO")
        return result
    monkeypatch.setattr(exports_module, "inspect_artifact", swap_after_verification)
    first = export(client, record["id"], "chave-a")
    assert first.status_code == 409 and "Vídeo alterado" in first.json()["detail"]
    monkeypatch.undo()
    (root / "video.mp4").write_bytes(VIDEO)  # restaura o vídeo aprovado
    # Nada órfão: só o registro da falha, sem ZIP, sem parcial, sem item na listagem.
    [folder] = folders(settings, record["id"])
    assert [p.name for p in folder.iterdir()] == [FAILED_MARKER]
    marker = json.loads((folder / FAILED_MARKER).read_text(encoding="utf-8"))
    assert "Vídeo alterado" in marker["reason"] and str(settings.data_dir) not in json.dumps(marker)
    assert client.get(f"/v1/productions/{record['id']}/exports").json() == {"items": []}
    # A MESMA chave não é reexecutada em silêncio: erro claro, e o vídeo íntegro não muda isso.
    again = export(client, record["id"], "chave-a")
    assert again.status_code == 409 and "falhou" in again.json()["detail"] and "nova chave" in again.json()["detail"]
    assert [p.name for p in folder.iterdir()] == [FAILED_MARKER]
    # Nova chave funciona normalmente.
    ok = export(client, record["id"], "chave-b")
    assert ok.status_code == 201
    assert [item["id"] for item in client.get(f"/v1/productions/{record['id']}/exports").json()["items"]] == [ok.json()["id"]]


def test_export_write_error_is_recorded_without_leaking_paths_and_new_key_works(production, monkeypatch):
    client, record, root, settings = production
    approve(client, record)
    real = exports_module.ZipFile.writestr
    calls = {"n": 0}

    def disk_full(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(28, f"No space left on device: {settings.data_dir}")
        return real(self, *args, **kwargs)
    monkeypatch.setattr(exports_module.ZipFile, "writestr", disk_full)
    response = export(client, record["id"], "cheia")
    assert response.status_code == 409 and str(settings.data_dir) not in response.text
    monkeypatch.undo()
    [folder] = folders(settings, record["id"])
    assert [p.name for p in folder.iterdir()] == [FAILED_MARKER]
    assert str(settings.data_dir) not in (folder / FAILED_MARKER).read_text(encoding="utf-8")
    assert "falhou" in export(client, record["id"], "cheia").json()["detail"]
    assert export(client, record["id"], "livre").status_code == 201


def test_failure_marker_is_ignored_by_listing_and_download(production, monkeypatch):
    client, record, root, settings = production
    approve(client, record)
    monkeypatch.setattr(exports_module.ZipFile, "writestr", lambda *a, **k: (_ for _ in ()).throw(OSError(5, "x")))
    export(client, record["id"], "falha")
    monkeypatch.undo()
    export_id = hashlib.sha256(f"{record['id']}:falha".encode()).hexdigest()
    assert client.get(f"/v1/productions/{record['id']}/exports").json() == {"items": []}
    assert client.get(f"/v1/productions/{record['id']}/exports/{export_id}/file").status_code in {404, 409}


def test_thumbnail_failure_leaves_only_the_marker_and_new_key_works(production, monkeypatch, tools):
    client, record, root, settings = production
    approve(client, record)
    before = registered_artifacts(client, record["id"])
    fake_run(monkeypatch, after_extract=lambda: (root / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypTROCADO"))
    assert thumbnail(client, record["id"], 0.5, "t1").status_code == 409
    (root / "video.mp4").write_bytes(VIDEO)
    assert registered_artifacts(client, record["id"]) == before
    [folder] = list((root / "publishing" / "thumbnails").iterdir())
    assert [p.name for p in folder.iterdir()] == [FAILED_MARKER]  # sem PNG/JPG/recibo órfãos
    calls = fake_run(monkeypatch)
    again = thumbnail(client, record["id"], 0.5, "t1")
    assert again.status_code == 409 and "falhou" in again.json()["detail"] and "nova chave" in again.json()["detail"]
    assert calls == []  # nem ffprobe nem ffmpeg rodaram de novo para a mesma chave
    assert thumbnail(client, record["id"], 0.5, "t2").status_code == 201


@pytest.mark.parametrize("png", [b"nao e png", b""])
def test_thumbnail_bad_output_marks_the_key_failed(production, monkeypatch, tools, png):
    client, record, root, settings = production
    approve(client, record)
    fake_run(monkeypatch, png=png)
    assert thumbnail(client, record["id"], 0.5, "ruim").status_code == 409
    [folder] = list((root / "publishing" / "thumbnails").iterdir())
    assert [p.name for p in folder.iterdir()] == [FAILED_MARKER]
    fake_run(monkeypatch)
    assert thumbnail(client, record["id"], 0.5, "boa").status_code == 201


# --- (2) texto depois do marcador de divulgações -----------------------------------------------------------------

MARKER = "\n\nDivulgações obrigatórias:\n"


def test_text_after_the_disclosure_marker_is_refused_with_an_explanation_not_dropped(production):
    client, record, root, settings = production
    approve(client, record)
    before = registered_artifacts(client, record["id"])
    for tail in ("Meu texto importante", "Licenças revisadas: tudo certo.", "publishable=true", "Linha extra\nOutra"):
        response = client.put(f"/v1/productions/{record['id']}/metadata", json={"title": "t", "description": "Início" + MARKER + tail})
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "Divulgações obrigatórias" in detail and "ANTES desse marcador" in detail
    assert registered_artifacts(client, record["id"]) == before  # nada gravado


def test_reading_and_writing_back_the_text_is_allowed_even_if_disclosures_changed(production):
    client, record, root, settings = production
    approve(client, record)
    assert put_metadata(client, record["id"], title="Meu título", description="Texto do usuário\n\nSegundo parágrafo").status_code == 200
    fetched = client.get(f"/v1/productions/{record['id']}/metadata").json()
    assert fetched["description"].startswith("Texto do usuário\n\nSegundo parágrafo" + MARKER)
    echoed = client.put(f"/v1/productions/{record['id']}/metadata", json={"title": fetched["title"], "description": fetched["description"]})
    assert echoed.status_code == 200 and echoed.json() == fetched  # ida e volta sem perder nada
    # Rodapé de uma leitura anterior devolvido depois de a licença mudar (pendente -> revisada) continua aceito: só linhas conhecidas.
    stale = fetched["description"].replace(publishing_module.LICENSES_PENDING, publishing_module.LICENSES_REVIEWED)
    assert client.put(f"/v1/productions/{record['id']}/metadata", json={"title": "Meu título", "description": stale}).status_code == 200
    # Uma linha alterada, mesmo parecida, é recusada.
    changed = fetched["description"].replace("publishable=false", "publishable=true")
    assert client.put(f"/v1/productions/{record['id']}/metadata", json={"title": "t", "description": changed}).status_code == 422


def test_marker_without_extra_text_and_text_before_the_marker_are_preserved(production):
    client, record, root, settings = production
    approve(client, record)
    value = put_metadata(client, record["id"], title="t", description="Antes do marcador" + MARKER).json()
    assert value["description"].startswith("Antes do marcador" + MARKER) and value["description"].count("Divulgações obrigatórias:") == 1


# --- (3) hash fora da transação ---------------------------------------------------------------------------------

def write_lock_is_free(settings):
    connection = sqlite3.connect(settings.data_dir / "horus.sqlite3", timeout=0.3)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        connection.close()


def spy_video_hash(monkeypatch, module, settings):
    seen = []
    real = module.inspect_artifact

    def spy(source, relative, kind):
        if kind == "video":
            seen.append(write_lock_is_free(settings))  # durante o hash do vídeo, ninguém pode estar segurando o lock de escrita
        return real(source, relative, kind)
    monkeypatch.setattr(module, "inspect_artifact", spy)
    return seen


def test_video_hash_never_runs_inside_a_write_transaction(production, monkeypatch, tools):
    client, record, root, settings = production
    approve(client, record)
    seen = spy_video_hash(monkeypatch, publishing_module, settings)
    fake_run(monkeypatch)
    assert thumbnail(client, record["id"]).status_code == 201
    assert put_metadata(client, record["id"], title="t", description="d").status_code == 200
    assert client.get(f"/v1/productions/{record['id']}/metadata").status_code == 200
    assert len(seen) >= 4 and all(seen), seen  # verificação inicial e final da miniatura, edição e leitura


def test_export_hashes_artifacts_outside_the_database_transaction(production, monkeypatch):
    client, record, root, settings = production
    approve(client, record)
    seen = spy_video_hash(monkeypatch, exports_module, settings)
    assert export(client, record["id"]).status_code == 201
    assert seen and all(seen)


def test_guarantee_that_the_verified_file_is_the_exported_one_still_holds(production, monkeypatch):
    client, record, root, settings = production
    approve(client, record)
    real = exports_module.inspect_artifact

    def swap(source, relative, kind):
        result = real(source, relative, kind)
        if kind == "video":
            (source / relative).write_bytes(b"\x00\x00\x00\x18ftypOUTRO-ARQUIVO")
        return result
    monkeypatch.setattr(exports_module, "inspect_artifact", swap)
    assert export(client, record["id"], "troca").status_code == 409
    assert not list(settings.data_dir.glob("exports/**/*.zip"))
