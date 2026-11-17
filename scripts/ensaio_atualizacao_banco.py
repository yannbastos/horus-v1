"""Ensaio HOR-157: exclusivamente dados sintéticos em TEMP; nunca abre dados reais."""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile


def safe_path(value: str | Path) -> Path:
    path = Path(os.path.abspath(value))
    local = Path(os.path.abspath(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')))
    for forbidden in (local / 'com.horus.audiovisual', local / 'Horus'):
        if path == forbidden or forbidden in path.parents:
            raise ValueError('Pasta operacional proibida')
    path = path.resolve()
    for forbidden in (local / 'com.horus.audiovisual', local / 'Horus'):
        if path == forbidden or forbidden in path.parents:
            raise ValueError('Pasta operacional proibida')
    temp = Path(os.environ.get('TEMP', tempfile.gettempdir())).resolve()
    if path == temp or temp not in path.parents:
        raise ValueError('Exige uma subpasta exclusiva dentro de TEMP')
    return path


def snapshot(path: Path, columns: dict | None = None) -> dict:
    """Hash lógico determinístico: colunas originais e linhas ordenadas, não páginas SQLite."""
    path = safe_path(path)
    with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('integrity_check falhou')
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('foreign_key_check falhou')
        quote = lambda name: '"' + name.replace('"', '""') + '"'
        if columns is None:
            names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            columns = {name: [r[1] for r in db.execute('PRAGMA table_info(' + quote(name) + ')')] for name in names}
        result = {}
        for name, fields in columns.items():
            rows = db.execute('SELECT ' + ','.join(map(quote, fields)) + ' FROM ' + quote(name)).fetchall()
            encoded = sorted(json.dumps(row, ensure_ascii=False, default=lambda b: {'blob': b.hex()}) for row in rows)
            result[name] = {'columns': fields, 'count': len(rows), 'sha256': hashlib.sha256('\n'.join(encoded).encode()).hexdigest()}
        return result


def cli(*args: str) -> None:
    command = [sys.executable, '-m', *map(str, args)]
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf8', env=env)
    print('$ ' + subprocess.list2cmdline(command))
    print(result.stdout.strip())
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)


def run(base: Path) -> None:
    from horus_backend.migrations import MIGRATIONS, migrate
    from horus_backend.backups import inspect_database
    base = safe_path(base)
    base.mkdir(parents=True, exist_ok=False)
    os.environ['HORUS_DATA_DIR'] = str(base / 'isolamento')
    source = safe_path(base / 'origem'); source.mkdir()
    original = source / 'horus.sqlite3'
    with closing(sqlite3.connect(original)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for version in range(1, 10):
            for statement in MIGRATIONS[version]:
                db.execute(statement)
            db.execute(f'PRAGMA user_version={version}')
        db.execute("INSERT INTO media VALUES ('midia-ficticia','ficcao.mp4','Exemplo fictício',123,456,'2026-11-17')")
        db.execute("INSERT INTO workflows(id,idempotency_key,payload,revision,status,created_at,updated_at) VALUES ('w','w','{}',1,'queued','2026-11-17','2026-11-17')")
        db.execute("INSERT INTO jobs(id,idempotency_key,payload,status,created_at,updated_at,workflow_id) VALUES ('j','j','{}','succeeded','2026-11-17','2026-11-17','w')")
        db.execute("INSERT INTO job_attempts(id,job_id,number,status,executor,input_hash,input_artifacts,started_at) VALUES ('a','j',1,'succeeded','simulated','ficticio','[]','2026-11-17')")
        db.execute("INSERT INTO artifacts VALUES ('artefato','w',1,'proposal','j','a',1,'{}','ficticio','2026-11-17')")
        db.execute("INSERT INTO scripts(id,workflow_id,workflow_revision,proposal_artifact_id,proposal_content_hash,version,status,document,created_at,origin) VALUES ('roteiro','w',1,'artefato','ficticio',1,'awaiting_review','{}','2026-11-17','human')")
        db.execute("INSERT INTO script_receipts VALUES ('ensaio','chave-ficticia','{}','{}')")
        db.commit()
    baseline = snapshot(original)
    assert inspect_database(original)['user_version'] == 9
    print('PASSOU criação sintética v9, integridade e chaves estrangeiras')
    package = base / 'pacote.zip'
    cli('horus_backend.local_data_export', 'export', source, '--to', package)
    cli('horus_backend.local_data_export', 'verify', package)
    print('PASSOU exportação e verificação SHA-256 do pacote')
    restored = safe_path(base / 'restaurado')
    cli('horus_backend.local_data_export', 'restore', package, '--to', restored)
    assert snapshot(restored / 'horus.sqlite3') == baseline
    print('PASSOU restauração do pacote e comparação de contagens/hashes de todas as tabelas')
    copydir = safe_path(base / 'migrado'); copydir.mkdir()
    copied = copydir / 'horus.sqlite3'
    cli('horus_backend.backups', 'restore', restored / 'horus.sqlite3', '--to', copied, '--json')
    migrate(copied)
    assert inspect_database(copied)['user_version'] == 12
    fields = {name: table['columns'] for name, table in baseline.items()}
    assert snapshot(copied, fields) == baseline
    print('PASSOU migração da cópia v9→v12, integridade e preservação dos dados anteriores')
    backups = list(copydir.glob('horus.backup-v9-*.sqlite3'))
    assert len(backups) == 1 and snapshot(backups[0]) == baseline
    assert inspect_database(backups[0])['user_version'] == 9
    cli('horus_backend.backups', 'verify', copied, '--json')
    print('PASSOU backup automático v9, integridade e hashes')
    before = snapshot(copied)
    migrate(copied)
    assert snapshot(copied) == before and list(copydir.glob('horus.backup-v9-*.sqlite3')) == backups
    print('PASSOU idempotência ao reabrir (sem dados alterados nem novo backup)')
    rollback = safe_path(base / 'rollback.sqlite3')
    cli('horus_backend.backups', 'restore', backups[0], '--to', rollback, '--json')
    assert snapshot(rollback) == baseline and inspect_database(rollback)['user_version'] == 9
    assert snapshot(original) == baseline
    (base / 'hashes-v9.json').write_text(json.dumps(baseline, indent=2, ensure_ascii=False), encoding='utf8')
    (base / 'hashes-v12.json').write_text(json.dumps(before, indent=2, ensure_ascii=False), encoding='utf8')
    print('PASSOU rollback em arquivo novo e origem v9 preservada')
    print('PASSOU todas as etapas; evidências em ' + str(base))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destino', required=True, help='Subpasta nova e exclusiva em TEMP')
    args = parser.parse_args()
    try:
        run(safe_path(args.destino))
    except Exception as error:
        print(f'FALHOU: {type(error).__name__}: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
