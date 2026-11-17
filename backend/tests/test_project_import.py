"""HOR-099: importação de projeto a partir do pacote de exportação. Sem tabelas; só arquivos em <data_dir>/imports.
Pacotes reais vêm da exportação existente; os hostis são montados a partir deles. Só diretórios temporários."""
import hashlib
import io
import json
import stat
import threading
import zipfile
from pathlib import Path

import psycopg
import pytest

from support_c import client_for, pg_connect, schema_state, settings  # noqa: F401  (fixtures)
from test_exports import approve, export, production  # noqa: F401  (fixtures)
from horus_backend import project_import as module
from horus_backend.project_import import ImportService, verify_package, ImportRefused


@pytest.fixture
def package(production, tmp_path):
    client, record, root, settings = production
    approve(client, record)
    created = export(client, record["id"])
    assert created.status_code == 201, created.text
    export_id = created.json()["id"]
    data = client.get(f"/v1/productions/{record['id']}/exports/{export_id}/file").content
    path = tmp_path / "pacote" / "horus.zip"
    path.parent.mkdir()
    path.write_bytes(data)
    return client, record, settings, path


def post(client, path, key="import-1"):
    return client.post("/v1/imports", json={"path": str(path)}, headers={"Idempotency-Key": key})


def read_zip(path):
    with zipfile.ZipFile(path) as archive:
        return {i.filename: archive.read(i.filename) for i in archive.infolist()}


def rebuild(path, target, mutate=None, rehash=True, manifest_edit=None, order=None, extra_info=None):
    """Reescreve o pacote com a mutação pedida; por padrão recalcula o manifesto (o pacote hostil passa no hash)."""
    members = read_zip(path)
    manifest = json.loads(members.pop("manifest.json"))
    if mutate:
        mutate(members)
    if rehash:
        manifest["files"] = [{"name": n, "sha256": hashlib.sha256(d).hexdigest(), "size_bytes": len(d)} for n, d in members.items()]
    if manifest_edit:
        manifest_edit(manifest)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_STORED if name == "video.mp4" else zipfile.ZIP_DEFLATED
            if extra_info and name in extra_info:
                extra_info[name](info)
            out.writestr(info, data)
        out.writestr("manifest.json", json.dumps(manifest))
    return target


def tables(settings):
    with pg_connect(settings) as db:
        names = [
            r[0]
            for r in db.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'horus' AND tablename <> 'imports' ORDER BY tablename"
            ).fetchall()
        ]
        counts = {n: db.execute(f'SELECT COUNT(*) FROM horus."{n}"').fetchone()[0] for n in names}
        return counts, schema_state(db)


# --- caminho feliz -------------------------------------------------------------------------------------------------

def test_import_creates_a_verified_historical_copy_without_touching_the_database(package):
    client, record, settings, path = package
    before = tables(settings)
    response = post(client, path)
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["imported"] is True and item["publishable"] is False and item["approved"] is False and item["historical_only"] is True
    assert item["id"] != record["id"] and item["source_production_id"] == record["id"] and len(item["id"]) == 32
    assert {"script", "quality_report", "licenses", "original_review"} <= set(item) and item["title"]
    assert item["original_review"]["production_review"]["decision"] == "approve"      # só histórico
    assert tables(settings) == before                                                  # nenhuma outra tabela/versão mudou
    folder = settings.data_dir / "imports" / item["id"]
    assert {p.name for p in folder.iterdir()} >= {"video.mp4", "subtitles.srt", "script.json", "quality-report.json",
                                                   "licenses.json", "review.json"}
    assert not [p for p in (settings.data_dir / "imports").iterdir() if p.name.startswith(".staging")]
    with pg_connect(settings) as db:
        row = db.execute("SELECT approved, publishable, manifest::text FROM horus.imports WHERE id = %s", (item["id"],)).fetchone()
        assert row is not None and row[0] is False and row[1] is False
        assert str(path) not in row[2] and "pacote" not in row[2]
    assert client.get("/v1/imports").json()["items"][0]["id"] == item["id"]
    assert client.get(f"/v1/imports/{item['id']}").json()["script"] == item["script"]
    assert client.get(f"/v1/imports/{item['id']}/video").content == read_zip(path)["video.mp4"]
    assert client.get(f"/v1/imports/{item['id']}/subtitles").content == read_zip(path)["subtitles.srt"]
    assert not (settings.data_dir / "productions" / item["id"]).exists()


def test_imported_project_is_not_a_production_nor_exportable_nor_approvable(package):
    client, record, settings, path = package
    item = post(client, path).json()
    assert client.get(f"/v1/productions/{item['id']}").status_code == 404
    assert client.post(f"/v1/productions/{item['id']}/export", headers={"Idempotency-Key": "x"}).status_code == 404
    assert client.post(f"/v1/productions/{item['id']}/review", headers={"Idempotency-Key": "x"},
                       json={"decision": "approve", "artifact_sha256": "0" * 64}).status_code == 404
    assert item["id"] not in json.dumps(client.get(f"/v1/scripts/{record['script_id']}/productions").json())


# --- idempotência e conflitos -------------------------------------------------------------------------------------

def test_replay_is_200_and_other_keys_or_copies_of_the_same_package_conflict(package, tmp_path):
    client, record, settings, path = package
    first = post(client, path)
    again = post(client, path)
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    other_key = post(client, path, key="outra-chave")
    assert other_key.status_code == 409 and other_key.json()["detail"]["code"] == "already_imported"
    assert other_key.json()["detail"]["existing_id"] == first.json()["id"]
    copy = tmp_path / "copia.zip"
    copy.write_bytes(path.read_bytes())
    assert post(client, copy, key="copia").status_code == 409
    assert len(client.get("/v1/imports").json()["items"]) == 1


def test_two_different_packages_get_distinct_new_ids_and_nothing_is_overwritten(package, tmp_path):
    client, record, settings, path = package
    first = post(client, path).json()
    second_path = rebuild(path, tmp_path / "segundo.zip", manifest_edit=lambda m: m.update(id="a" * 64))
    second = post(client, second_path, key="k2")
    assert second.status_code == 201 and second.json()["id"] != first["id"]
    assert client.get(f"/v1/imports/{first['id']}/video").content == read_zip(path)["video.mp4"]
    assert {i["id"] for i in client.get("/v1/imports").json()["items"]} == {first["id"], second.json()["id"]}


def test_concurrent_imports_of_the_same_package_create_one_copy(package, tmp_path):
    client, record, settings, path = package
    codes = []
    barrier = threading.Barrier(4)

    def run(i):
        barrier.wait()
        codes.append(post(client, path, key=f"conc-{i}").status_code)
    threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert codes.count(201) == 1 and set(codes) <= {201, 409}
    assert len([p for p in (settings.data_dir / "imports").iterdir() if not p.name.startswith(".")]) == 1


# --- pacotes hostis: recusados ANTES de qualquer escrita -----------------------------------------------------------

def flip(name):
    return lambda members: members.__setitem__(name, members[name] + b" ")


def bad_script(members):
    document = json.loads(members["script.json"])
    document["sections"] = []
    members["script.json"] = json.dumps(document).encode()


HOSTILE = {
    "video adulterado (hash)": dict(rehash=False, mutate=lambda m: m.__setitem__("video.mp4", m["video.mp4"] + b"x")),
    "script adulterado (hash)": dict(rehash=False, mutate=flip("script.json")),
    "tamanho mentido no manifesto": dict(manifest_edit=lambda m: m["files"][0].update(size_bytes=1)),
    "arquivo extra fora da lista": dict(mutate=lambda m: m.__setitem__("evil.exe", b"MZ")),
    "traversal ../": dict(mutate=lambda m: m.__setitem__("../fora.txt", b"x")),
    "traversal profundo": dict(mutate=lambda m: m.__setitem__("a/../../b.json", b"x")),
    "subpasta": dict(mutate=lambda m: m.__setitem__("sub/video.mp4", b"x")),
    "caminho absoluto": dict(mutate=lambda m: m.__setitem__("/etc/passwd", b"x")),
    "unidade": dict(mutate=lambda m: m.__setitem__("C:/Windows/x", b"x")),
    "barra invertida": dict(mutate=lambda m: m.__setitem__("..\\x.json", b"x")),
    "fluxo alternativo": dict(mutate=lambda m: m.__setitem__("video.mp4:stream", b"x")),
    "nome reservado": dict(mutate=lambda m: m.__setitem__("CON", b"x")),
    "nome com maiúsculas (duplicata)": dict(mutate=lambda m: m.__setitem__("VIDEO.MP4", b"x")),
    "obrigatório ausente": dict(mutate=lambda m: m.pop("review.json")),
    "manifesto lista arquivo que não existe": dict(manifest_edit=lambda m: m["files"].append(
        {"name": "metadata.json", "sha256": "0" * 64, "size_bytes": 1})),
    "arquivo sem entrada no manifesto": dict(rehash=True, manifest_edit=lambda m: m["files"].pop()),
    "manifesto schema 2": dict(manifest_edit=lambda m: m.update(schema_version=2)),
    "manifesto schema booleano": dict(manifest_edit=lambda m: m.update(schema_version=True)),
    "manifesto publishable true": dict(manifest_edit=lambda m: m.update(publishable=True)),
    "manifesto sem publishable": dict(manifest_edit=lambda m: m.pop("publishable")),
    "id de manifesto inválido": dict(manifest_edit=lambda m: m.update(id="../../x")),
    "production_id inválido": dict(manifest_edit=lambda m: m.update(production_id="X" * 32)),
    "entradas duplicadas no manifesto": dict(manifest_edit=lambda m: m["files"].append(dict(m["files"][0]))),
    "campo extra em entrada": dict(manifest_edit=lambda m: m["files"][0].update(extra=1)),
    "sha256 em maiúsculas": dict(manifest_edit=lambda m: m["files"][0].update(sha256=m["files"][0]["sha256"].upper())),
    "tamanho negativo/booleano": dict(manifest_edit=lambda m: m["files"][0].update(size_bytes=True)),
    "vídeo que não é MP4": dict(mutate=lambda m: m.__setitem__("video.mp4", b"\x00" * 64)),
    "thumbnail com assinatura errada": dict(mutate=lambda m: m.__setitem__("thumbnail.png", b"nao e png")),
    "roteiro fora do contrato": dict(mutate=bad_script),
    "QC fora do contrato": dict(mutate=lambda m: m.__setitem__("quality-report.json", b'{"status": "pass", "checks": 7}')),
    "JSON inválido": dict(mutate=lambda m: m.__setitem__("licenses.json", b"{nao json")),
    "licenses não é objeto": dict(mutate=lambda m: m.__setitem__("licenses.json", b"[1,2]")),
    "legenda não UTF-8": dict(mutate=lambda m: m.__setitem__("subtitles.srt", b"\xff\xfe\x00\xd8")),
    "zip bomb (razão de compressão)": dict(mutate=lambda m: m.__setitem__("script.txt", b"\x00" * (8 * 1024 * 1024))),
    "link simbólico": dict(extra_info={"subtitles.srt": lambda i: setattr(i, "external_attr", (stat.S_IFLNK | 0o777) << 16)}),
    "item que não é arquivo comum": dict(extra_info={"subtitles.srt": lambda i: setattr(i, "external_attr", (stat.S_IFIFO | 0o644) << 16)}),
}


@pytest.mark.parametrize("case", list(HOSTILE))
def test_hostile_packages_are_refused_before_any_write(package, tmp_path, case):
    client, record, settings, path = package
    members = read_zip(path)
    if case == "thumbnail com assinatura errada":
        spec = dict(HOSTILE[case])
    else:
        spec = HOSTILE[case]
    target = tmp_path / "hostil.zip"
    try:
        rebuild(path, target, **spec)
    except (ValueError, OSError):
        pytest.skip("o próprio zipfile recusa montar este pacote hostil")
    before = tables(settings)
    response = post(client, target)
    assert response.status_code in {409, 422}, (case, response.text)
    assert "detail" in response.json() and str(tmp_path) not in response.text and "Traceback" not in response.text
    assert not (settings.data_dir / "imports").exists(), "algo foi escrito antes da verificação completa"
    assert tables(settings) == before and members


def test_not_a_zip_truncated_and_empty_files(package, tmp_path):
    client, record, settings, path = package
    junk = tmp_path / "lixo.zip"
    junk.write_bytes(b"isto nao e um zip" * 100)
    truncated = tmp_path / "cortado.zip"
    truncated.write_bytes(path.read_bytes()[: path.stat().st_size // 2])
    empty = tmp_path / "vazio.zip"
    with zipfile.ZipFile(empty, "w"):
        pass
    zero = tmp_path / "zero.zip"
    zero.write_bytes(b"")
    for target in (junk, truncated, empty, zero):
        assert post(client, target).status_code in {409, 422}, target.name
    assert not (settings.data_dir / "imports").exists()


def test_limits_on_members_and_total_size_are_enforced(package, tmp_path, monkeypatch):
    client, record, settings, path = package
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 10)
    assert post(client, path).json()["detail"]["code"] == "package_too_large"
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 8 * 1024 ** 3)
    monkeypatch.setattr(module, "MAX_MEMBERS", 3)
    assert post(client, path, key="k2").json()["detail"]["code"] == "package_structure"
    monkeypatch.setattr(module, "MAX_MEMBERS", len(module.ALLOWED))
    monkeypatch.setattr(module, "MAX_VIDEO_BYTES", 4)
    assert post(client, path, key="k3").json()["detail"]["code"] == "member_too_large"
    assert not (settings.data_dir / "imports").exists()


def test_a_member_with_more_bytes_than_declared_stops_reading(package, tmp_path):
    client, record, settings, path = package
    members = read_zip(path)
    manifest = json.loads(members.pop("manifest.json"))
    for item in manifest["files"]:
        if item["name"] == "subtitles.srt":
            item["size_bytes"] = len(members["subtitles.srt"]) - 1            # o manifesto declara menos do que o conteúdo real
    target = tmp_path / "mentiroso.zip"
    with zipfile.ZipFile(target, "w") as out:
        for name, data in members.items():
            out.writestr(name, data)
        out.writestr("manifest.json", json.dumps(manifest))
    response = post(client, target)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "size_mismatch"


# --- caminho do pacote -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["\\\\servidor\\compartilhado\\a.zip", "C:\\pasta\\..\\a.zip", "relativo\\a.zip", "a.zip", "C:\\pasta\\a.zip:stream",
                                 "C:\\pasta\\CON.zip", "C:\\pasta\\a\x00.zip", "\\\\?\\C:\\a.zip"])
def test_hostile_package_paths_are_refused(package, raw):
    client, record, settings, path = package
    response = client.post("/v1/imports", json={"path": raw}, headers={"Idempotency-Key": "k"})
    assert response.status_code in {422}, raw
    assert not (settings.data_dir / "imports").exists()


def test_missing_wrong_extension_directory_and_extra_fields(package, tmp_path):
    client, record, settings, path = package
    renamed = tmp_path / "pacote.txt"
    renamed.write_bytes(path.read_bytes())
    for target in (tmp_path / "nao-existe.zip", renamed, tmp_path):
        assert post(client, target).status_code == 422
    assert client.post("/v1/imports", json={"path": str(path), "extra": 1}, headers={"Idempotency-Key": "k"}).status_code == 422
    assert client.post("/v1/imports", json={"path": str(path)}).status_code == 422                  # sem Idempotency-Key
    assert client.post("/v1/imports", json={}, headers={"Idempotency-Key": "k"}).status_code == 422
    assert not (settings.data_dir / "imports").exists()


def test_a_junction_or_symlink_package_is_resolved_not_trusted(package, tmp_path):
    client, record, settings, path = package
    link = tmp_path / "atalho.zip"
    try:
        link.symlink_to(path)
    except (OSError, NotImplementedError):
        pytest.skip("symlink indisponível sem privilégio")
    assert post(client, link).status_code in {201, 422}


# --- falhas durante a cópia e adulteração posterior -------------------------------------------------------------------

def test_failure_while_copying_leaves_no_partial_import(package, monkeypatch):
    client, record, settings, path = package
    real = module._stream
    calls = {"n": 0}

    def flaky(archive, name, declared, sink=None):
        if sink is not None:
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("disco cheio")
        return real(archive, name, declared, sink)
    monkeypatch.setattr(module, "_stream", flaky)
    response = post(client, path)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "import_failed" and "disco" not in response.text
    root = settings.data_dir / "imports"
    assert [p.name for p in root.iterdir()] == []
    monkeypatch.setattr(module, "_stream", real)
    assert post(client, path, key="de-novo").status_code == 201


def test_package_changed_between_verification_and_copy_is_refused(package, monkeypatch, tmp_path):
    client, record, settings, path = package
    real = module.verify_package

    def verify_then_swap(p):
        result = real(p)
        members = read_zip(p)
        members["subtitles.srt"] += b"1\n"
        manifest = json.loads(members.pop("manifest.json"))
        with zipfile.ZipFile(p, "w") as out:
            for name, data in members.items():
                out.writestr(name, data)
            out.writestr("manifest.json", json.dumps(manifest))
        return result
    monkeypatch.setattr(module, "verify_package", verify_then_swap)
    response = post(client, path)
    assert response.status_code in {409, 422}
    assert [p for p in (settings.data_dir / "imports").iterdir()] == []


def test_tampering_after_import_is_detected_on_read(package):
    client, record, settings, path = package
    item = post(client, path).json()
    folder = settings.data_dir / "imports" / item["id"]
    (folder / "video.mp4").write_bytes(b"outro conteudo")
    assert client.get(f"/v1/imports/{item['id']}/video").status_code == 409
    (folder / "script.json").write_text('{"trocado": true}', encoding="utf-8")
    assert client.get(f"/v1/imports/{item['id']}").status_code == 409
    with pg_connect(settings) as db:
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("UPDATE horus.imports SET approved = true WHERE id = %s", (item["id"],))
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("DELETE FROM horus.imports WHERE id = %s", (item["id"],))


def test_corrupt_import_record_never_breaks_the_listing(package):
    client, record, settings, path = package
    item = post(client, path).json()
    (settings.data_dir / "imports" / ("a" * 32)).mkdir()
    (settings.data_dir / "imports" / "pasta-estranha").mkdir()
    listed = client.get("/v1/imports").json()
    assert [i["id"] for i in listed["items"]] == [item["id"]]


# --- ids e autenticação -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("import_id", ["..", "%2e%2e", "..%2f..%2fWindows", "A" * 32, "a" * 31, "a" * 33, "con", "x:stream", "%00"])
def test_import_ids_are_never_paths(package, import_id):
    client, record, settings, path = package
    for suffix in ("", "/video", "/subtitles"):
        assert client.get(f"/v1/imports/{import_id}{suffix}").status_code in {404, 405, 422}, (import_id, suffix)
    assert client.get("/v1/imports/" + "a" * 32 + "/video").status_code == 404
    assert client.get("/v1/imports/" + "a" * 32 + "/manifest.json").status_code == 404


@pytest.mark.parametrize("method,path", [("GET", "/v1/imports"), ("POST", "/v1/imports"), ("GET", "/v1/imports/" + "a" * 32),
                                         ("GET", "/v1/imports/" + "a" * 32 + "/video"), ("GET", "/v1/imports/" + "a" * 32 + "/subtitles")])
def test_routes_require_token_and_refuse_foreign_origin(package, method, path):
    client, record, settings, _ = package
    assert client.request(method, path, headers={"Authorization": ""}).status_code in {401, 403}
    for hostile in ({"Origin": "https://evil.example"}, {"Host": "evil.example"}):
        assert client.request(method, path, headers={**dict(client.headers), **hostile}, json={}).status_code == 403


def test_no_local_path_or_token_leaks_in_responses(package):
    client, record, settings, path = package
    item = post(client, path)
    text = item.text + client.get("/v1/imports").text + client.get(f"/v1/imports/{item.json()['id']}").text
    for forbidden in (str(settings.data_dir), str(path.parent), "Bearer", "C:\\Users"):
        assert forbidden not in text, forbidden


def test_verify_package_reports_stable_codes(package, tmp_path):
    client, record, settings, path = package
    assert set(verify_package(path)) == {"manifest", "contents", "parsed"}
    target = rebuild(path, tmp_path / "x.zip", mutate=lambda m: m.pop("video.mp4"))
    with pytest.raises(ImportRefused) as error:
        verify_package(target)
    assert error.value.code in {"manifest_incomplete", "manifest_mismatch"} and not str(tmp_path) in str(error.value)


def test_encrypted_members_are_refused(package, tmp_path):
    """O zipfile não grava o bit de criptografia pedido via ZipInfo: ele é ligado direto nos cabeçalhos do arquivo."""
    client, record, settings, path = package
    data = bytearray(path.read_bytes())
    start = 0
    while (start := data.find(b"PK", start)) != -1:      # diretório central: flags em +8
        data[start + 8] |= 0x1
        start += 4
    target = tmp_path / "cifrado.zip"
    target.write_bytes(bytes(data))
    response = post(client, target)
    assert response.status_code == 422 and response.json()["detail"]["code"] in {"member_encrypted", "package_unreadable"}
    assert not (settings.data_dir / "imports").exists()
