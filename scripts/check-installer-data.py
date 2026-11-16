"""Dados sintéticos e recusa de schema futuro no ensaio isolado do instalador."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import shutil


def check(folder, action, executable):
    database = folder / "horus.sqlite3"
    media = folder / "fixture.txt"
    if action == "seed":
        media.write_bytes(b"HORUS upgrade rollback synthetic fixture\n")
        (folder / 'productions' / 'installer-fixture').mkdir(parents=True, exist_ok=True)
        (folder / 'productions' / 'installer-fixture' / 'project.json').write_text('{"name":"Projeto sintético"}', encoding='utf-8')
        (folder / 'narration-preference.json').write_text('{"voice":"fixture"}', encoding='utf-8')
        (folder / 'exports').mkdir(exist_ok=True)
        shutil.copyfile(Path(__file__).parent / 'fixtures' / 'installer-video.mp4', folder / 'exports' / 'example.mp4')
        files = ['fixture.txt', 'productions/installer-fixture/project.json', 'narration-preference.json', 'exports/example.mp4']
        hashes = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in files}
        (folder / 'installer-fixture-hashes.json').write_text(json.dumps(hashes), encoding='utf-8')
    assert database.is_file(), "Banco do ensaio não foi preservado"
    with closing(sqlite3.connect(database)) as db, db:
        if action == "seed":
            db.execute("INSERT INTO media VALUES (?, ?, ?, ?, ?, ?)",
                       ("installer-fixture", str(media), media.name, media.stat().st_size, 1, "2026-11-14"))
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT name, size FROM media WHERE id='installer-fixture'").fetchone() == (media.name, 41)
        version = db.execute("PRAGMA user_version").fetchone()[0]
    assert media.read_bytes() == b"HORUS upgrade rollback synthetic fixture\n"
    hashes = json.loads((folder / 'installer-fixture-hashes.json').read_text(encoding='utf-8'))
    assert all(hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())
    if action == "future":
        # Cópia exclusiva: nunca aumentar o schema do banco do ciclo N → N+1 → N.
        future = folder / ("future-schema-" + secrets.token_hex(8))
        future.mkdir()
        target = future / "horus.sqlite3"
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(target)) as db, db:
            source.backup(db)
            db.execute(f"PRAGMA user_version={version + 1}")
        before = hashlib.sha256(target.read_bytes()).hexdigest()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("HORUS_", "FABRICA_", "PYTHON", "UV_", "VIRTUAL_ENV"))}
        env.update(HORUS_DATA_DIR=str(future), HORUS_API_TOKEN=secrets.token_hex(32),
                   HORUS_HERMES_CONFIG=str(future / "missing.json"),
                   HORUS_HERMES_PROFILE=str(future / "profile"),
                   PATH=os.path.join(os.environ["SystemRoot"], "System32"))
        result = subprocess.run([str(executable), "--port", "0"], env=env, cwd=future,
                                capture_output=True, timeout=45)
        raw = result.stdout + result.stderr
        try:
            output = raw.decode("utf-8")
        except UnicodeDecodeError:
            output = raw.decode("cp1252")  # stderr do Python congelado no Windows pt-BR
        assert result.returncode != 0, output
        assert '"event": "ready"' not in output
        assert "Banco criado por uma versão mais recente do Hórus. Migração recusada." in output, output
        assert hashlib.sha256(target.read_bytes()).hexdigest() == before
    print(json.dumps(dict(action=action, schema_version=version, data_preserved=True,
                         fixture_rows=1, hashed_files=len(hashes),
                         future_schema_refused=action == "future")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("action", choices=["seed", "verify", "future"])
    parser.add_argument("--executable", type=Path)
    args = parser.parse_args()
    check(args.folder, args.action, args.executable)
