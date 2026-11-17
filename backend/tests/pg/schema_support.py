"""Aplica a cadeia SQL exclusivamente no cluster sintético do harness."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = sorted((ROOT / 'supabase/migrations').glob('*.sql'))
HORUS_MIGRATIONS = [p for p in MIGRATIONS if '_horus_' in p.name]


def migration_checksum(path):
    """Conteúdo UTF-8/LF sem BOM: estável entre checkouts Windows e Linux."""
    return sha256(path.read_text(encoding='utf-8-sig').encode('utf-8')).hexdigest()


def apply_chain(admin):
    """Ledger atômico com cada DDL; checksum do conteúdo inteiro normalizado."""
    for path in MIGRATIONS:
        version = path.name.split('_', 1)[0]
        digest = migration_checksum(path)
        operational = '_horus_' in path.name
        ledger = admin.execute("SELECT to_regclass('horus.schema_migrations')").fetchone()[0]
        if ledger and operational:
            row = admin.execute('SELECT checksum FROM horus.schema_migrations WHERE version=%s', (version,)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError('Checksum de migration divergente')
                continue
        # O runner Supabase mantém o ledger próprio da migration histórica.
        if not operational and admin.execute("SELECT to_regnamespace('horus_sync')").fetchone()[0]:
            continue
        with admin.transaction():
            admin.execute(path.read_text(encoding='utf-8-sig'))
            if operational:
                admin.execute('INSERT INTO horus.schema_migrations(version,checksum,applied_at) VALUES(%s,%s,clock_timestamp())', (version,digest))


@contextmanager
def schema_database(cluster):
    # Papéis/auth.users sintéticos: nunca nas migrations operacionais novas.
    with cluster.connect(autocommit=True) as bootstrap:
        for name in ('anon','authenticated'):
            bootstrap.execute(f"DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='{name}') THEN CREATE ROLE {name} NOLOGIN; END IF; END $$")
    with cluster.database() as pool:
        with pool.connection() as app:
            database = app.info.dbname
        with cluster.connect(database, autocommit=True) as admin:
            admin.execute('DROP SCHEMA horus CASCADE')  # Exclusivamente clone descartável.
            admin.execute('CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY)')
            apply_chain(admin)
            yield admin, pool
