"""HOR-123: política de acesso a arquivos. Caminhos vindos do cliente (mídia, pasta de mídia, salvar .txt, transcrição,
download) passam por `file_access.client_path`; artefatos/exportações usam `production.confined`. Só diretórios temporários."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from horus_backend.api import create_app
from horus_backend.config import Settings
from horus_backend.file_access import PathNotAllowed, client_path
from horus_backend.jobs import DownloadRequest, TranscriptionRequest

TOKEN = "file-access-tests-token-0123456789"
GOOD = {"Authorization": f"Bearer {TOKEN}"}

HOSTILE = [
    r"\\servidor\compartilhado\a.mp4",             # UNC
    "//servidor/compartilhado/a.mp4",
    r"\\?\C:\Users\x\a.mp4",                       # caminho estendido
    r"\\.\PhysicalDrive0",                         # dispositivo
    r"C:\pasta\a.mp4:stream",                      # fluxo alternativo NTFS
    r"C:\pasta\a.mp4::$DATA",
    r"C:\pasta\..\..\Windows\a.mp4",               # travessia
    r"C:/pasta/../a.mp4",
    r"C:\pasta\CON", r"C:\pasta\nul.txt", r"C:\pasta\COM1.mp4", r"C:\pasta\lpt9",   # nomes reservados
    "C:\\pasta\\a.mp4.", "C:\\pasta\\a.mp4 ",      # ponto/espaço final (o Windows os descarta)
    "C:\\pasta\\a\x00.mp4", "C:\\pasta\\a\n.mp4",  # controle
    "relativo\\a.mp4", "..\\a.mp4", "a.mp4", "./a.mp4",   # relativos
    "   ",
]


@pytest.fixture
def settings(tmp_path):
    return Settings(token=TOKEN, data_dir=tmp_path / "data", factory_root=None, hermes_executable=None,
                    hermes_config=tmp_path / "hermes.json")


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings), base_url="http://127.0.0.1", headers=GOOD)


# --- helper ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", HOSTILE)
def test_helper_refuses_hostile_paths(raw):
    with pytest.raises(PathNotAllowed):
        client_path(raw)


def test_helper_accepts_an_ordinary_absolute_path_and_resolves_it(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "video.mp4").write_bytes(b"x")
    assert client_path(str(tmp_path / "sub" / "video.mp4"), strict=True) == (tmp_path / "sub" / "video.mp4").resolve()
    assert client_path(str(tmp_path / "novo.txt")) == (tmp_path / "novo.txt").resolve()
    assert client_path(str(tmp_path / "pasta com espaço" / ".oculto")) .name == ".oculto"


def test_helper_strict_requires_existence_and_protected_roots_are_refused(tmp_path):
    with pytest.raises(ValueError):
        client_path(str(tmp_path / "ausente.mp4"), strict=True)
    protected = tmp_path / "data"
    protected.mkdir()
    for inside in (protected / "x.txt", protected / "a" / "b.txt"):
        with pytest.raises(PathNotAllowed):
            client_path(str(inside), protected=(protected,))
    assert client_path(str(tmp_path / "fora.txt"), protected=(protected,)).name == "fora.txt"


def link_or_skip(link: Path, target: Path):
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink indisponível sem privilégio")


# --- /v1/media e /v1/media/folder -----------------------------------------------------------------------------

@pytest.mark.parametrize("raw", HOSTILE)
@pytest.mark.parametrize("route", ["/v1/media", "/v1/media/folder"])
def test_media_routes_refuse_hostile_paths(client, route, raw):
    response = client.post(route, json={"path": raw})
    assert response.status_code in (400, 422), response.text
    assert client.get("/v1/media").json() == []


def test_media_register_refuses_non_media_and_link_to_it(client, tmp_path):
    secret = tmp_path / "segredo.db"
    secret.write_bytes(b"x")
    assert client.post("/v1/media", json={"path": str(secret)}).status_code == 400
    link = tmp_path / "video.mp4"
    link_or_skip(link, secret)
    assert client.post("/v1/media", json={"path": str(link)}).status_code == 400   # alvo resolvido não é mídia
    assert client.get("/v1/media").json() == []


def test_media_folder_skips_links_and_non_media(client, tmp_path):
    folder = tmp_path / "pasta"
    folder.mkdir()
    (folder / "ok.mp4").write_bytes(b"x")
    (folder / "nota.txt").write_bytes(b"x")
    outside = tmp_path / "fora.mp4"
    outside.write_bytes(b"x")
    link_or_skip(folder / "atalho.mp4", outside)
    names = {item["name"] for item in client.post("/v1/media/folder", json={"path": str(folder)}).json()}
    assert names == {"ok.mp4"}


# --- /v1/transcriptions/save ----------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [h for h in HOSTILE if not h.endswith((".mp4", ".mp4 ", ".mp4."))] +
                         [h + ".txt" for h in HOSTILE if h.endswith(".mp4")])
def test_save_refuses_hostile_paths(client, raw):
    assert client.post("/v1/transcriptions/save", json={"path": raw, "text": "x"}).status_code == 400


def test_save_refuses_the_data_directory_and_links(client, settings, tmp_path):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    assert client.post("/v1/transcriptions/save", json={"path": str(settings.data_dir / "x.txt"), "text": "x"}).status_code == 400
    assert not (settings.data_dir / "x.txt").exists()
    victim = tmp_path / "vitima.txt"
    victim.write_text("original", encoding="utf-8")
    link = tmp_path / "link.txt"
    link_or_skip(link, victim)
    assert client.post("/v1/transcriptions/save", json={"path": str(link), "text": "novo"}).status_code == 400
    assert victim.read_text(encoding="utf-8") == "original"


def test_save_legitimate_path_still_works(client, tmp_path):
    target = tmp_path / "transcricao.txt"
    response = client.post("/v1/transcriptions/save", json={"path": str(target), "text": "olá"})
    assert response.status_code == 200 and target.read_text(encoding="utf-8") == "olá"


# --- jobs: transcrição e download -----------------------------------------------------------------------------

@pytest.fixture
def manager(client):
    return client.app.state.manager


@pytest.mark.parametrize("raw", HOSTILE)
def test_transcription_job_refuses_hostile_paths(manager, raw):
    with pytest.raises(ValueError):
        manager.command(TranscriptionRequest(operation="transcription.run", path=raw))


@pytest.mark.parametrize("raw", HOSTILE)
def test_download_job_refuses_hostile_directories(manager, raw):
    with pytest.raises(ValueError):
        manager.command(DownloadRequest(operation="youtube.download", url="https://youtu.be/dQw4w9WgXcQ", directory=raw))


def test_download_cannot_target_the_data_directory(manager, settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(PathNotAllowed):
        manager.command(DownloadRequest(operation="youtube.download", url="https://youtu.be/dQw4w9WgXcQ", directory=str(settings.data_dir)))


def test_legitimate_transcription_and_download_commands_are_built(manager, tmp_path):
    media = tmp_path / "aula.mp4"
    media.write_bytes(b"x")
    command = manager.command(TranscriptionRequest(operation="transcription.run", path=str(media)))
    assert str(media.resolve()) in command
    out = tmp_path / "saida"
    out.mkdir()
    command = manager.command(DownloadRequest(operation="youtube.download", url="https://youtu.be/dQw4w9WgXcQ", directory=str(out)))
    assert str(out.resolve()) in command


# --- rotas por identificador (produção, exportação, artefatos, fábrica) ---------------------------------------

@pytest.mark.parametrize("identifier", ["..", "..%2f..%2fetc", "%2e%2e%5c%2e%2e", "a%00b", "C%3A%5CWindows", "con", "x:stream"])
def test_id_routes_never_resolve_client_supplied_paths(client, identifier):
    for url in (f"/v1/productions/{identifier}", f"/v1/productions/{identifier}/exports",
                f"/v1/productions/{identifier}/artifacts/{identifier}",
                f"/v1/productions/{identifier}/exports/{identifier}/file"):
        assert client.get(url).status_code in (404, 405, 409, 422), url


@pytest.mark.parametrize("payload", [{"channel_id": "..", "operation": "factory.profile"},
                                     {"channel_id": "canal01", "video_id": "..\\x", "operation": "factory.render.preview"},
                                     {"channel_id": "C:\\x", "operation": "factory.profile"},
                                     {"channel_id": "a/b", "operation": "factory.profile"}])
def test_factory_ids_are_not_paths(client, payload):
    assert client.post("/v1/jobs", json=payload).status_code == 422


# --- inventário: nenhuma rota nova com caminho do cliente sem passar pela política ----------------------------

def test_no_route_models_a_client_path_outside_the_policy():
    """Campos de caminho em modelos de requisição conhecidos. Um campo novo exige decisão explícita aqui."""
    from horus_backend import api, jobs
    expected = {(api.MediaRequest, "path"), (api.TranscriptSave, "path"), (api.ImportRequest, "path"),
                (jobs.TranscriptionRequest, "path"), (jobs.DownloadRequest, "directory")}
    found = set()
    for module in (api, jobs):
        for value in vars(module).values():
            fields = getattr(value, "model_fields", None)
            if fields and value.__module__ == module.__name__:
                found |= {(value, n) for n in fields if re.search(r"(^|_)(path|paths|dir|directory|folder|file|filename|output)($|_)", n)}
    assert found == expected, f"Novo campo de caminho do cliente: {found ^ expected}"


