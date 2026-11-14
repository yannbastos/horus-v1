"""HOR-056: revisão adversarial de thumbnail/metadados de publicação. FFmpeg sempre simulado; dados fictícios."""
import hashlib
import json
import re
import struct
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from test_workflows import settings  # noqa: F401  (fixture)
from test_exports import production, approve, export  # noqa: F401
from test_publishing import tools, thumbnail  # noqa: F401
from horus_backend import publishing as publishing_module
from horus_backend.publishing import PublishingService

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1080, 1920)


def put_metadata(client, pid, title="Título", description="Descrição"):
    return client.put(f"/v1/productions/{pid}/metadata", json={"title": title, "description": description})


def registered_artifacts(client, pid):
    with client.app.state.store.connect() as db:
        return json.loads(db.execute("SELECT artifacts FROM productions WHERE id=?", (pid,)).fetchone()[0])


def fake_run(monkeypatch, duration='{"format":{"duration":"4.0"}}', png=PNG, jpg=b"\xff\xd8\xfffixture", after_extract=None):
    calls = []

    def run(self, command, timeout):
        calls.append(list(command))
        if "-show_entries" in command:
            return duration if isinstance(duration, bytes) else duration.encode()
        path = Path(command[-1])
        if path.suffix == ".png":
            path.write_bytes(png)
            if after_extract:
                after_extract()
        elif jpg is not None:
            path.write_bytes(jpg)
        return b""
    monkeypatch.setattr(PublishingService, "_run", run)
    return calls


# --- timecode -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "-1", "-0.001", '"1"', '"0.5"', "null", "true", "[]", "{}", '"NaN"', "1e999"])
def test_invalid_timecodes_are_rejected_before_any_tool_runs(production, tools, raw):
    client, record, root, settings = production
    approve(client, record)
    response = client.post(f"/v1/productions/{record['id']}/thumbnail", headers={"Idempotency-Key": "t", "Content-Type": "application/json"},
                           content=('{"timecode_seconds": ' + raw + '}').encode())
    assert response.status_code == 422, (raw, response.status_code)
    assert tools == [] and not list(root.glob("publishing/**/*"))


@pytest.mark.parametrize("timecode", [4.0, 4.0000001, 1e9, 1e308])
def test_timecode_at_or_beyond_duration_is_refused_without_extracting(production, tools, timecode):
    client, record, root, settings = production
    approve(client, record)
    assert thumbnail(client, record["id"], timecode).status_code == 409
    assert len(tools) == 1 and not list(root.glob("publishing/**/*"))  # só o ffprobe rodou


@pytest.mark.parametrize("duration", [
    '{"format":{"duration":"N/A"}}', '{"format":{"duration":"nan"}}', '{"format":{"duration":"inf"}}', '{"format":{"duration":"-3"}}',
    '{"format":{"duration":"0"}}', '{"format":{}}', '{}', '[]', '"texto"', "não é json", '{"format":{"duration":null}}',
    '{"format":{"duration":[4]}}', b"\xff\xfe\x00",
])
def test_unusable_ffprobe_output_is_a_clean_409(production, monkeypatch, tools, duration):
    client, record, root, settings = production
    approve(client, record)
    fake_run(monkeypatch, duration=duration)
    assert thumbnail(client, record["id"], 0.5).status_code == 409
    assert not list(root.glob("publishing/**/*")) and not [a for a in registered_artifacts(client, record["id"]) if a["kind"] == "thumbnail"]


def test_ffmpeg_arguments_contain_only_flags_absolute_paths_and_a_numeric_timecode(production, monkeypatch, tools):
    client, record, root, settings = production
    approve(client, record)
    calls = fake_run(monkeypatch)
    key = "../../; rm -rf / & -vf evil $(x)\n--help"
    assert thumbnail(client, record["id"], 1e-05, key).status_code == 201
    for command in calls:
        assert command[0] == str(settings.ffmpeg) or command[0].endswith(("ffprobe.exe", "ffprobe"))
        for index, arg in enumerate(command[1:], start=1):
            if arg.startswith("-") and not re.fullmatch(r"-[0-9.e+-]+", arg):
                continue  # opção
            assert Path(arg).is_absolute() or re.fullmatch(r"[0-9.e+-]+|[a-z_:,0-9()/*=-]+|file,pipe|0:v:0|json|format=duration|error", arg), arg
    extract = next(c for c in calls if "-ss" in c)
    assert re.fullmatch(r"[0-9.e+-]+", extract[extract.index("-ss") + 1])
    assert all(str(root) in c[c.index("-i") + 1] for c in calls if "-i" in c)
    folder = next((root / "publishing" / "thumbnails").iterdir())
    assert re.fullmatch(r"[a-f0-9]{64}", folder.name)  # a chave do cliente nunca vira nome de arquivo


def test_ffmpeg_runs_without_shell_stdin_secrets_or_unbounded_time(production, monkeypatch, tmp_path):
    client, record, root, settings = production
    approve(client, record)
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"x")
    ffmpeg.with_name("ffprobe.exe").write_bytes(b"x")
    object.__setattr__(settings, "ffmpeg", ffmpeg)
    monkeypatch.setenv("HORUS_API_TOKEN", "segredo-que-nao-pode-vazar")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "outro-segredo")
    seen = []

    def fake_subprocess(command, **kwargs):
        seen.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(publishing_module.subprocess, "run", fake_subprocess)
    assert thumbnail(client, record["id"]).status_code == 409
    command, kwargs = seen[0]
    assert isinstance(command, list) and not kwargs.get("shell") and kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["timeout"] and kwargs["check"] is True
    assert not {"HORUS_API_TOKEN", "AWS_SECRET_ACCESS_KEY"} & set(kwargs["env"])
    assert {k.upper() for k in kwargs["env"]} <= {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}


@pytest.mark.parametrize("missing", ["ffmpeg", "ffprobe", "directory", "unset"])
def test_missing_or_wrong_tools_are_a_clean_409(production, tmp_path, missing):
    client, record, root, settings = production
    approve(client, record)
    folder = tmp_path / "bin"
    folder.mkdir()
    ffmpeg = folder / "ffmpeg.exe"
    if missing != "ffmpeg":
        ffmpeg.write_bytes(b"x")
    if missing not in {"ffprobe", "ffmpeg"}:
        ffmpeg.with_name("ffprobe.exe").write_bytes(b"x")
    object.__setattr__(settings, "ffmpeg", None if missing == "unset" else (folder if missing == "directory" else ffmpeg))
    assert thumbnail(client, record["id"]).status_code == 409


# --- vídeo trocado, saída adulterada ou gigante --------------------------------------------------------------------

def test_video_swapped_during_extraction_is_not_registered_or_exported(production, monkeypatch, tools):
    client, record, root, settings = production
    approve(client, record)
    before = registered_artifacts(client, record["id"])
    fake_run(monkeypatch, after_extract=lambda: (root / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypTROCADO"))
    response = thumbnail(client, record["id"])
    assert response.status_code == 409
    assert registered_artifacts(client, record["id"]) == before  # nada registrado a partir do vídeo trocado
    assert export(client, record["id"]).status_code == 409  # o próprio vídeo adulterado já bloqueia a exportação


@pytest.mark.parametrize("png, jpg", [
    (b"<html>nao e png</html>", b"\xff\xd8\xff-ok"), (PNG, b"GIF89a nao e jpg"), (PNG[:20], b"\xff\xd8\xff-ok"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 640, 480), b"\xff\xd8\xff-ok"), (b"", b"\xff\xd8\xff-ok"),
    (PNG, None),
])
def test_bad_tool_output_is_never_registered(production, monkeypatch, tools, png, jpg):
    client, record, root, settings = production
    approve(client, record)
    before = registered_artifacts(client, record["id"])
    fake_run(monkeypatch, png=png, jpg=jpg)
    assert thumbnail(client, record["id"]).status_code == 409
    assert registered_artifacts(client, record["id"]) == before


def test_huge_tool_output_is_refused_not_registered_or_loaded(production, monkeypatch, tools):
    client, record, root, settings = production
    approve(client, record)
    before = registered_artifacts(client, record["id"])
    fake_run(monkeypatch, jpg=b"\xff\xd8\xff" + b"0" * (publishing_module.MAX_IMAGE_BYTES + 1))
    assert thumbnail(client, record["id"]).status_code == 409
    assert registered_artifacts(client, record["id"]) == before


def test_registered_thumbnail_tampered_later_blocks_replay_and_export(production, monkeypatch, tools):
    client, record, root, settings = production
    approve(client, record)
    fake_run(monkeypatch)
    saved = thumbnail(client, record["id"]).json()
    assert export(client, record["id"], "limpo").status_code == 201
    (root / saved["artifacts"][1]["relative_path"]).write_bytes(b"\xff\xd8\xff-ADULTERADO")
    assert thumbnail(client, record["id"]).status_code == 409
    assert export(client, record["id"], "adulterado").status_code == 409
    assert client.get(f"/v1/productions/{record['id']}/artifacts/{saved['artifacts'][1]['id']}").status_code in {409, 404}


def test_receipt_tampered_blocks_reuse_and_never_creates_a_second_registration(production, monkeypatch, tools):
    client, record, root, settings = production
    approve(client, record)
    calls = fake_run(monkeypatch)
    saved = thumbnail(client, record["id"]).json()
    receipt = next((root / "publishing" / "thumbnails").iterdir()) / "receipt.json"
    receipt.write_text(json.dumps({**saved, "timecode_seconds": 3.9}), encoding="utf-8")
    count = len(calls)
    assert thumbnail(client, record["id"], 3.9).status_code == 409
    assert len(calls) == count  # a pasta já existe: recusa antes de rodar qualquer ferramenta
    assert len([a for a in registered_artifacts(client, record["id"]) if a["kind"] == "thumbnail"]) == 2


# --- idempotência e concorrência -------------------------------------------------------------------------------

def test_same_key_concurrently_extracts_once_and_different_keys_extract_each(production, monkeypatch, tools):
    client, record, root, settings = production
    approve(client, record)
    calls = fake_run(monkeypatch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        same = list(pool.map(lambda _: thumbnail(client, record["id"], 0.5, "igual"), range(4)))
    assert sorted(r.status_code for r in same) == [200, 200, 200, 201]
    assert len({json.dumps(r.json()["artifacts"], sort_keys=True) for r in same}) == 1
    assert len([c for c in calls if "-ss" in c]) == 1
    with ThreadPoolExecutor(max_workers=3) as pool:
        different = list(pool.map(lambda index: thumbnail(client, record["id"], 0.5, f"k{index}"), range(3)))
    assert [r.status_code for r in different] == [201] * 3
    assert len(list((root / "publishing" / "thumbnails").iterdir())) == 4


# --- metadados -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("title", ["com\x00nul", "com\x1bescape", "com \u202e bidi", "com \u2066 isolado", "quebra\nde linha", "tab\taqui", "\x85", "\u2028sep"])
def test_title_with_control_or_bidi_characters_is_refused(production, title):
    client, record, root, settings = production
    approve(client, record)
    assert put_metadata(client, record["id"], title=title).status_code == 422


@pytest.mark.parametrize("description", ["a\x00b", "a\x1bb", "a \u202e b", "a \u2069 b", "a\u0085b"])
def test_description_with_control_or_bidi_characters_is_refused(production, description):
    client, record, root, settings = production
    approve(client, record)
    assert put_metadata(client, record["id"], description=description).status_code == 422


def test_description_keeps_normal_line_breaks_unicode_and_markup_as_literal_text(production):
    client, record, root, settings = production
    approve(client, record)
    text = "Linha 1\r\nLinha 2\n\n- item\n# título\n<script>alert(1)</script> [x](javascript:alert(1)) ação ✓ 😀"
    response = put_metadata(client, record["id"], title="<b>Olá</b> & `código`", description=text)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "<b>Olá</b> & `código`" and "<script>alert(1)</script>" in body["description"]
    assert response.headers["content-type"].startswith("application/json")
    assert client.get(f"/v1/productions/{record['id']}/metadata").json() == body


@pytest.mark.parametrize("payload", [
    {"title": "", "description": "x"}, {"title": "   ", "description": "x"}, {"title": "t" * 101, "description": "x"},
    {"title": "t", "description": "d" * 5001}, {"title": "t", "description": "d" * 4990}, {"title": "t"}, {"description": "d"},
    {"title": 1, "description": "d"}, {"title": "t", "description": None}, {"title": "t", "description": "d", "publishable": True},
    {"title": "t", "description": "d", "disclosures": []}, {"title": ["t"], "description": "d"},
])
def test_invalid_metadata_payloads_are_refused_cleanly(production, payload):
    client, record, root, settings = production
    approve(client, record)
    before = registered_artifacts(client, record["id"])
    assert client.put(f"/v1/productions/{record['id']}/metadata", json=payload).status_code == 422
    assert registered_artifacts(client, record["id"]) == before


def test_mandatory_disclosures_cannot_be_removed_or_faked_by_editing(production):
    client, record, root, settings = production
    approve(client, record)
    default = client.get(f"/v1/productions/{record['id']}/metadata").json()
    assert default["licenses_pending"] is True and default["publishable"] is False
    fake = "Texto\n\nDivulgações obrigatórias:\nLicenças revisadas: tudo certo.\npublishable=true"
    refused = put_metadata(client, record["id"], description=fake)
    assert refused.status_code == 422 and "ANTES desse marcador" in refused.json()["detail"]  # não descarta em silêncio
    for description in ("", "Divulgações obrigatórias:", "\n\nDivulgações obrigatórias:\n"):
        value = put_metadata(client, record["id"], description=description).json()
        assert value["disclosures"] == default["disclosures"] and value["licenses_pending"] is True and value["publishable"] is False
        for disclosure in default["disclosures"]:
            assert value["description"].count(disclosure) == 1
        assert "Licenças revisadas: tudo certo" not in value["description"] and "publishable=true" not in value["description"]
        assert client.get(f"/v1/productions/{record['id']}/metadata").json() == value
    for forged in ({"disclosures": ["nada"]}, {"licenses_pending": False}, {"publishable": True}):
        assert client.put(f"/v1/productions/{record['id']}/metadata", json={"title": "t", "description": "d", **forged}).status_code == 422


def test_metadata_edits_are_idempotent_and_bounded_in_registered_files(production):
    client, record, root, settings = production
    approve(client, record)
    first = put_metadata(client, record["id"], title="Mesmo", description="Mesmo texto").json()
    for _ in range(10):
        assert put_metadata(client, record["id"], title="Mesmo", description="Mesmo texto").json() == first
    files = [a for a in registered_artifacts(client, record["id"]) if a["relative_path"].startswith("publishing/metadata/")]
    assert len(files) == 1, f"{len(files)} arquivos de metadados para a mesma edição"


def test_concurrent_metadata_edits_never_fail_or_mix_fields(production):
    client, record, root, settings = production
    approve(client, record)
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda index: put_metadata(client, record["id"], title=f"Título {index}", description=f"Texto {index}"), range(12)))
    assert all(r.status_code == 200 for r in responses)
    for r in responses:
        index = r.json()["title"].split()[-1]
        assert r.json()["description"].startswith(f"Texto {index}\n\nDivulgações obrigatórias:")
    final = client.get(f"/v1/productions/{record['id']}/metadata").json()
    assert final["title"].startswith("Título ") and final["description"].startswith("Texto ")


def test_registered_metadata_tampered_is_refused_everywhere(production):
    client, record, root, settings = production
    approve(client, record)
    put_metadata(client, record["id"], title="Original", description="Texto")
    file = next(root.glob("publishing/metadata/*.json"))
    file.write_text(json.dumps({"title": "Adulterado", "description": "Sem divulgações", "publishable": True}), encoding="utf-8")
    assert client.get(f"/v1/productions/{record['id']}/metadata").status_code == 409
    assert put_metadata(client, record["id"], title="Novo", description="Novo").status_code in {200, 409}
    assert export(client, record["id"]).status_code == 409 or "Sem divulgações" not in json.dumps(
        json.loads(ZipFile(settings.data_dir / export(client, record["id"], "x").json()["relative_path"]).read("metadata.json")))


# --- aprovação revogada, produção rejeitada --------------------------------------------------------------------------

@pytest.mark.parametrize("change", ["rejected", "revoked", "job_failed", "other_video", "video_missing"])
def test_publishing_routes_follow_the_current_approval_state(production, monkeypatch, tools, change):
    client, record, root, settings = production
    approve(client, record)
    assert thumbnail(client, record["id"], 0.5, "antes").status_code == 201
    put_metadata(client, record["id"], title="Antes", description="Antes")
    with client.app.state.store.connect() as db:
        row = db.execute("SELECT review,job_id FROM productions WHERE id=?", (record["id"],)).fetchone()
        review = json.loads(row["review"])
        if change in {"rejected", "revoked"}:
            review["decision"] = "reject" if change == "rejected" else "request_changes"
            db.execute("UPDATE productions SET review=? WHERE id=?", (json.dumps(review), record["id"]))
        elif change == "job_failed":
            db.execute("UPDATE jobs SET status='failed' WHERE id=?", (row["job_id"],))
        elif change == "other_video":
            review["artifact_sha256"] = "0" * 64
            db.execute("UPDATE productions SET review=? WHERE id=?", (json.dumps(review), record["id"]))
    if change == "video_missing":
        (root / "video.mp4").unlink()
    before = registered_artifacts(client, record["id"])
    assert thumbnail(client, record["id"], 0.5, "antes").status_code == 409  # nem o replay passa sem aprovação vigente
    assert thumbnail(client, record["id"], 0.5, "depois").status_code == 409
    assert client.get(f"/v1/productions/{record['id']}/metadata").status_code == 409
    assert put_metadata(client, record["id"], title="Depois", description="Depois").status_code == 409
    assert export(client, record["id"]).status_code == 409
    assert registered_artifacts(client, record["id"]) == before


def test_unreviewed_and_rejected_productions_cannot_publish(production, tools):
    client, record, root, settings = production
    assert thumbnail(client, record["id"]).status_code == 409 and put_metadata(client, record["id"]).status_code == 409
    rejected = client.post(f"/v1/productions/{record['id']}/review", headers={"Idempotency-Key": "rej"},
                           json={"decision": "reject", "artifact_sha256": hashlib.sha256((root / "video.mp4").read_bytes()).hexdigest(), "notes": []})
    assert rejected.status_code == 200, rejected.text
    assert thumbnail(client, record["id"], 0.5, "novo").status_code == 409
    assert client.get(f"/v1/productions/{record['id']}/metadata").status_code == 409


# --- pacote com opcionais (merge HOR-099 + HOR-056) -------------------------------------------------------------------

def test_export_with_optional_files_keeps_review_hardening_and_redacts_metadata(production, monkeypatch, tools):
    client, record, root, settings = production
    approve(client, record)
    fake_run(monkeypatch)
    thumb = thumbnail(client, record["id"]).json()
    assert put_metadata(client, record["id"], title="Vídeo C:\\Users\\operador\\x", description="Veja token=abc123XYZ e 100 km /h, 24/7, e/ou.").status_code == 200
    package = export(client, record["id"]).json()
    with ZipFile(BytesIO((settings.data_dir / package["relative_path"]).read_bytes())) as zipped:
        names = set(zipped.namelist())
        assert names == {"manifest.json", "video.mp4", "subtitles.srt", "script.json", "script.txt", "quality-report.json",
                         "licenses.json", "review.json", "metadata.json", "thumbnail.png", "thumbnail.jpg"}
        for name in names:
            assert "/" not in name and "\\" not in name
        metadata = json.loads(zipped.read("metadata.json"))
        manifest = json.loads(zipped.read("manifest.json"))
        assert "operador" not in json.dumps(metadata) and "abc123XYZ" not in json.dumps(metadata)
        assert "100 km /h, 24/7, e/ou." in metadata["description"]  # texto comum preservado pela trava revisada
        assert manifest["metadata_redacted"] is True and len(manifest["files"]) == 10
        assert zipped.read("thumbnail.png") == (root / thumb["artifacts"][0]["relative_path"]).read_bytes()
        assert zipped.testzip() is None
    assert client.get(f"/v1/productions/{record['id']}/exports").json()["items"][0]["id"] == package["id"]
