"""HOR-099: revisão adversarial da exportação local. Somente dados fictícios em tmp_path."""
import hashlib
import json
import subprocess
import sys
import threading
import tracemalloc
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from test_workflows import settings  # noqa: F401  (fixture)
from test_exports import production, approve, export  # noqa: F401
from horus_backend import exports as exports_module
from horus_backend.exports import ExportService
from horus_backend.production import inspect_artifact
from horus_backend.workflows import WorkflowConflict

TOKEN = "export-review-token-0123456789abcdef"


def service():
    """ExportService sem banco: só as regras de texto (token fictício)."""
    instance = ExportService.__new__(ExportService)
    instance.settings = SimpleNamespace(token=TOKEN)
    return instance


# --- trava de roteiro: falsos positivos e caminhos reais -----------------------------------------------------------

LEGITIMATE = [
    "O carro corre a 100 km /h na pista.", "Funcionamos 24/7.", "Contato por e-mail e/ou telefone.", "Metade é 1/2 do total.",
    "Veja https://exemplo.com.br/a/b?x=1 para detalhes.", "Data: 12/11/2026.", "Custa R$ 10/mês.", "Protocolo TCP/IP e HTTP/2.",
    "Escolha sim/não.", "início/fim do vídeo", "Use w/ cuidado ou c/ ele.", "Percentual de 50 % / 60 %.", "(1/2) concluído",
    "Distância de 5 km/h e 3 m/s.", "Ele disse: /ou/ talvez.", "Dia /noite e /madrugada.", "link http://localhost:1420/app",
    "Preço: 3 x 1/4 de xícara.", "Barra isolada / aqui", "Fim da linha /\nOutra", "Visite www.site.com/pagina/1.",
]


@pytest.mark.parametrize("text", LEGITIMATE)
def test_common_ptbr_text_is_not_refused(text):
    service()._private(text.encode("utf-8"))
    service()._private(json.dumps({"narration": text}, ensure_ascii=False).encode("utf-8"))


REAL_PATHS = [
    "C:\\Users\\operador\\Videos\\a.mp4", "c:/users/operador/a.mp4", "D:\\dados\\x", "\\\\servidor\\compartilhado\\arquivo",
    "//servidor/compartilhado/arquivo", "/home/operador/segredo.txt", "/etc/passwd", "/Users/operador/Movies/x.mp4",
    "/var/log/syslog", "/tmp/arquivo", "~/Documentos/x.txt", "file:///home/operador/x", "file:///C:/Users/x", "/mnt/c/Users/x",
    "veja em /opt/horus/dados e depois", "arquivo=\"/srv/media/a.mp4\"", "(/home/operador/a)", "[/usr/local/bin/x]",
    "path: /a/b", "em /var", "salvo em /root",
]


@pytest.mark.parametrize("text", REAL_PATHS)
def test_real_absolute_paths_are_still_refused(text):
    for payload in (text, "linha anterior\n" + text, "\t" + text):
        with pytest.raises(WorkflowConflict):
            service()._private(payload.encode("utf-8"))


@pytest.mark.parametrize("text", [
    "Authorization: Bearer abc.def", "token=abc123", "api_key: sk-123", "access_token=abc", "client_secret = xyz",
    "refresh_token: r1", "password=hunter2", "x-api-key: k", "SECRET=abc",
])
def test_credentials_in_text_are_refused(text):
    with pytest.raises(WorkflowConflict):
        service()._private(text.encode("utf-8"))
    with pytest.raises(WorkflowConflict):
        service()._private(f"antes {TOKEN} depois".encode("utf-8"))


@pytest.mark.parametrize("text", ["A senha do wifi é longa.", "O token de acesso é um conceito.", "segredo de família: bolo", "tokens são unidades"])
def test_credential_words_without_values_are_not_refused(text):
    service()._private(text.encode("utf-8"))


@pytest.mark.parametrize("text, forbidden", [
    ("Bearer FAKE.JWT.VALUE", "FAKE.JWT.VALUE"), ("token=abc123XYZ", "abc123XYZ"), ("access_token=abc123XYZ", "abc123XYZ"),
    ("client_secret: s3cr3tvalue", "s3cr3tvalue"), ("api-key=k3yvalue", "k3yvalue"), ("C:\\Users\\operador\\a.png", "operador"),
    ("\\\\servidor\\compartilhado\\a", "compartilhado"), ("/home/operador/a.png", "operador"), ("//servidor/compartilhado/a", "compartilhado"),
    ("file:///home/operador/a", "operador"), ("~/Documentos/a.png", "Documentos"), (f"valor {TOKEN} fim", TOKEN),
    ("em /opt/horus/dados ok", "horus/dados"),
])
def test_audit_metadata_redaction_catches_secrets_and_real_paths(text, forbidden):
    value = service()._audit_metadata({"notes": text, "nested": [{"x": text}], "Authorization": "Bearer abc", "api_token": "zzz"})
    dumped = json.dumps(value)
    assert forbidden not in dumped
    assert value["Authorization"] == "[credencial omitida]" and value["api_token"] == "[credencial omitida]"


@pytest.mark.parametrize("text", LEGITIMATE)
def test_audit_metadata_redaction_keeps_legitimate_text(text):
    assert service()._audit_metadata({"notes": text})["notes"] == text


# --- pacote: nomes, TOCTOU, escrita, concorrência, memória, download ---------------------------------------------------

def read_zip(settings, package):
    return ZipFile(BytesIO((settings.data_dir / package["relative_path"]).read_bytes()))


def test_zip_entries_are_fixed_relative_names_only(production):
    client, record, root, settings = production
    approve(client, record)
    package = export(client, record["id"]).json()
    with read_zip(settings, package) as zipped:
        names = zipped.namelist()
        assert sorted(names) == sorted(["manifest.json", *exports_module.FILES])
        for info in zipped.infolist():
            assert "/" not in info.filename and "\\" not in info.filename and ":" not in info.filename
            assert not info.filename.startswith(".") and not info.is_dir() and info.flag_bits & 0x1 == 0
        assert zipped.testzip() is None


def test_artifact_tampered_after_verification_is_caught_while_copying(production, monkeypatch):
    client, record, root, settings = production
    approve(client, record)
    real = exports_module.inspect_artifact
    seen = {"video": False}

    def inspect_then_swap(source, relative, kind):
        result = real(source, relative, kind)
        if kind == "video":
            seen["video"] = True
            (source / relative).write_bytes(b"\x00\x00\x00\x18ftypTROCADO-DEPOIS-DA-VERIFICACAO")  # janela TOCTOU
        return result
    monkeypatch.setattr(exports_module, "inspect_artifact", inspect_then_swap)
    response = export(client, record["id"])
    assert seen["video"] and response.status_code == 409
    assert not list(settings.data_dir.glob("exports/**/*.zip")) and not list(settings.data_dir.glob("exports/**/partial-*"))
    assert client.get(f"/v1/productions/{record['id']}/exports").json() == {"items": []}


def test_subtitles_tampered_after_verification_are_caught(production, monkeypatch):
    client, record, root, settings = production
    approve(client, record)
    real = exports_module.inspect_artifact

    def inspect_then_swap(source, relative, kind):
        result = real(source, relative, kind)
        if kind == "subtitles":
            (source / relative).write_text("1\n00:00:00,000 --> 00:00:01,000\ntrocado\n", encoding="utf-8")
        return result
    monkeypatch.setattr(exports_module, "inspect_artifact", inspect_then_swap)
    assert export(client, record["id"]).status_code == 409
    assert not list(settings.data_dir.glob("exports/**/*.zip"))


@pytest.mark.skipif(sys.platform != "win32", reason="junction de diretório do Windows")
def test_real_junction_in_export_folder_is_refused(production, tmp_path):
    client, record, root, settings = production
    approve(client, record)
    outside = tmp_path / "fora"
    outside.mkdir()
    (settings.data_dir / "exports").mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(settings.data_dir / "exports" / record["id"]), str(outside)],
                   check=True, capture_output=True)
    response = export(client, record["id"])
    assert response.status_code == 409
    assert list(outside.iterdir()) == []  # nada foi escrito através do junction


@pytest.mark.skipif(sys.platform != "win32", reason="junction de diretório do Windows")
def test_real_junction_in_production_artifacts_is_refused(production, tmp_path):
    client, record, root, settings = production
    approve(client, record)
    with client.app.state.store.connect() as db:
        artifacts = json.loads(db.execute("SELECT artifacts FROM productions WHERE id=?", (record["id"],)).fetchone()[0])
    scene = next(a for a in artifacts if a["kind"] == "scene")
    folder = (root / scene["relative_path"]).parent
    if folder == root:
        pytest.skip("artefato na raiz da produção")
    moved = tmp_path / "movida"
    folder.rename(moved)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(folder), str(moved)], check=True, capture_output=True)
    assert export(client, record["id"]).status_code == 409
    assert not list(settings.data_dir.glob("exports/**/*.zip"))


def test_write_error_midway_leaves_no_package_listed_or_downloadable(production, monkeypatch):
    client, record, root, settings = production
    approve(client, record)
    real = exports_module.ZipFile.writestr
    calls = {"n": 0}

    def disk_full(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError(28, "No space left on device")
        return real(self, *args, **kwargs)
    monkeypatch.setattr(exports_module.ZipFile, "writestr", disk_full)
    response = export(client, record["id"])
    assert response.status_code == 409 and "Não foi possível exportar" in response.json()["detail"]
    assert not list(settings.data_dir.glob("exports/**/*.zip")) and not list(settings.data_dir.glob("exports/**/partial-*"))
    assert client.get(f"/v1/productions/{record['id']}/exports").json() == {"items": []}
    export_id = hashlib.sha256(f"{record['id']}:export".encode()).hexdigest()
    assert client.get(f"/v1/productions/{record['id']}/exports/{export_id}/file").status_code in {404, 409}
    monkeypatch.undo()
    assert export(client, record["id"], "outra-chave").status_code == 201


def test_video_write_error_leaves_no_partial_file(production, monkeypatch):
    client, record, root, settings = production
    approve(client, record)

    class Broken:
        def __init__(self, inner):
            self.inner = inner

        def write(self, data):
            raise OSError(28, "No space left on device")

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)
    real_open = exports_module.ZipFile.open

    def open_broken(self, name, mode="r", *args, **kwargs):
        stream = real_open(self, name, mode, *args, **kwargs)
        return Broken(stream) if mode == "w" else stream
    monkeypatch.setattr(exports_module.ZipFile, "open", open_broken)
    assert export(client, record["id"]).status_code == 409
    assert not list(settings.data_dir.glob("exports/**/*.zip")) and not list(settings.data_dir.glob("exports/**/partial-*"))


def test_concurrent_exports_with_different_keys_create_distinct_complete_packages(production):
    from concurrent.futures import ThreadPoolExecutor
    client, record, root, settings = production
    approve(client, record)
    keys = [f"chave-{index}" for index in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda key: export(client, record["id"], key), keys + keys))
    assert sorted(r.status_code for r in responses) == [200] * 4 + [201] * 4
    assert len({r.json()["id"] for r in responses}) == 4
    listed = client.get(f"/v1/productions/{record['id']}/exports").json()["items"]
    assert len(listed) == 4 and len(list(settings.data_dir.glob("exports/**/*.zip"))) == 4
    assert not list(settings.data_dir.glob("exports/**/partial-*"))
    for item in listed:
        with read_zip(settings, item) as zipped:
            assert zipped.testzip() is None


def test_large_video_is_streamed_not_loaded_in_memory(production):
    client, record, root, settings = production
    size = 64 * 1024 * 1024
    with (root / "video.mp4").open("wb") as stream:
        stream.write(b"\x00\x00\x00\x18ftyp")
        block = bytes(range(256)) * 4096
        for _ in range(size // len(block)):
            stream.write(block)
    with client.app.state.store.connect() as db:
        artifacts = json.loads(db.execute("SELECT artifacts FROM productions WHERE id=?", (record["id"],)).fetchone()[0])
        artifacts = [inspect_artifact(root, a["relative_path"], a["kind"]) if a["kind"] == "video" else a for a in artifacts]
        video = next(a for a in artifacts if a["kind"] == "video")
        db.execute("UPDATE productions SET artifacts=? WHERE id=?", (json.dumps(artifacts), record["id"]))
    reviewed = client.post(f"/v1/productions/{record['id']}/review", headers={"Idempotency-Key": "grande"},
                           json={"decision": "approve", "artifact_sha256": video["sha256"], "acknowledge_quality_errors": True})
    assert reviewed.status_code == 200, reviewed.text
    tracemalloc.start()
    response = export(client, record["id"])
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert response.status_code == 201, response.text
    assert peak < 16 * 1024 * 1024, f"pico de memória Python {peak / 2**20:.1f} MiB para um vídeo de 64 MiB"
    package = response.json()
    assert package["size_bytes"] >= size
    with read_zip(settings, package) as zipped:
        assert zipped.getinfo("video.mp4").file_size == video["size_bytes"]


def test_download_is_authenticated_validates_ids_and_uses_safe_disposition(production):
    client, record, root, settings = production
    approve(client, record)
    package = export(client, record["id"]).json()
    base = f"/v1/productions/{record['id']}/exports"
    assert client.get(f"{base}/{package['id']}/file", headers={"Authorization": "Bearer errado"}).status_code == 401
    response = client.get(f"{base}/{package['id']}/file")
    disposition = response.headers["content-disposition"]
    assert disposition == f'attachment; filename="horus-{record["id"]}-{package["id"][:12]}.zip"'
    assert response.headers["content-type"] == "application/zip" and response.headers["cache-control"] == "no-store"
    for bad in ("..", "%2e%2e", package["id"].upper(), package["id"][:-1], package["id"] + "0", "a" * 500, "x y", "%0d%0a", "package.zip"):
        assert client.get(f"{base}/{bad}/file").status_code in {404, 405, 422}, bad
    assert client.get(f"/v1/productions/{'z' * 32}/exports/{package['id']}/file").status_code == 404
    assert client.get(f"/v1/productions/..%2f..%2fetc/exports/{package['id']}/file").status_code == 404


def test_one_corrupt_package_does_not_hide_the_good_ones(production):
    client, record, root, settings = production
    approve(client, record)
    good = export(client, record["id"], "boa").json()
    bad = export(client, record["id"], "ruim").json()
    (settings.data_dir / bad["relative_path"]).write_bytes(b"corrompido")
    listing = client.get(f"/v1/productions/{record['id']}/exports")
    assert listing.status_code == 200 and [item["id"] for item in listing.json()["items"]] == [good["id"]]
    assert [item["id"] for item in listing.json()["invalid"]] == [bad["id"]]  # adulteração sinalizada, não escondida
    assert client.get(f"/v1/productions/{record['id']}/exports/{good['id']}/file").status_code == 200
    assert client.get(f"/v1/productions/{record['id']}/exports/{bad['id']}/file").status_code == 409
