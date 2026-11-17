# HOR-164 / I-mig — Ferramenta verificável de migração SQLite → PostgreSQL

Data: 17/11/2026 · Branch: `yannbastos/sb-i-mig`

## 1. Entrega

- `backend/tools/migrate_sqlite_to_pg.py` e pacote `backend/tools/migrate_sqlite_pg/` (`guards.py`, `mapping.py`, `sqlite_reader.py`, `pg_loader.py`, `runner.py`):
  - Única exceção autorizada a importar `sqlite3`; `backend/src/**` não foi alterado.
  - **Dry-run por padrão** (`--apply` exigido para gravar).
  - **Guardas de segurança:** recusa caminhos relativos, symlinks/junctions, pastas operacionais (`%LOCALAPPDATA%\com.horus.audiovisual`, `%LOCALAPPDATA%\Horus`, `Programs\Horus`), cópias quentes com `-wal`/`-shm`/`-journal`, destinos remotos/com credenciais em `argv`/DSN e destinos não vazios.
  - **Backup e mapeamento (v9–v12):** backup imutável via SQLite Backup API, `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, `PRAGMA user_version` (`9..12`), rejeição de tabelas inesperadas e ordenação topológica (`jobs.depends_on`, `license_reviews.supersedes`).
  - **Carga e conferência transacional única:** `pg_advisory_xact_lock(1213157973, 164)`, `SET CONSTRAINTS ALL DEFERRED` → carga na ordem de FKs (`workflows.current_artifact_id` atualizado após `artifacts`) → `OVERRIDING SYSTEM VALUE` + `setval` → preservação byte a byte de `*_canonical` → `SET CONSTRAINTS ALL IMMEDIATE` → conferência de contagens, `NULL`s, min/max de IDs/`seq`, zero órfãos e SHA-256 linha a linha/tabela com o mesmo mapeamento canônico → gravação de `horus.migration_runs` na mesma transação. Qualquer falha/inconsistência faz rollback total e `exit != 0`.
  - **Idempotência / COMMIT ambíguo:** suporte a `--check-run-id <uuid>` e detecção de `run_id` já aplicado com mesmo `source_sha256`; repetição com novo `run_id` no mesmo destino é recusada.
  - **Sem conteúdo privado em relatórios/logs:** somente contagens, hashes, faixas de IDs/sequências e códigos de rejeição.
- `backend/tests/pg/migration_fixtures/synthetic_builder.py` e `backend/tests/pg/test_migration_sqlite_to_pg.py`: ensaios 100% sintéticos (todas as 21 tabelas, FKs cíclicas, JSON `1.0` vs `1`, `Decimal`, datas UTC/epoch, IDs `seq > 2^31`, v9→v12, guardas, falha injetada com rollback total, órfãos/tabela inesperada, idempotência e CLI em subprocesso).
- `docs/procedimento-migracao-sqlite-postgres.md` (44 linhas, português).

## 2. Verificações executadas

- `backend/tests/pg/test_migration_sqlite_to_pg.py -v`: **6 passed** (14.05s).
- `backend/tests/test_supply_chain.py` + `backend/tests/test_network_inventory.py -q`: **31 passed** (2.38s).
- `backend/tests/test_api_contract_snapshot.py` + `backend/tests/test_endpoint_auth.py -q`: **3 failed, 12 passed, 1 skipped, 56 errors** no setup de `create_app` por `api.py:124` (`RadarState` ainda com `.sqlite3`, escopo I-final).

## 3. Comando de ensaio futuro (executar SOMENTE com autorização específica)

> **Atenção:** nunca executar contra o banco operacional ativo nem contra Supabase real sem autorização explícita, cópia fria isolada e janela de corte definida.

```powershell
# 1. Dry-run contra cópia fria explícita e destino descartável em loopback:
python backend/tools/migrate_sqlite_to_pg.py `
  --source-dir "<COPIA_FRIA_EXPLICITA_DATA_DIR>" `
  --target-dsn "host=127.0.0.1 port=<PORTA> dbname=<DB_DESCARTAVEL> user=postgres sslmode=disable" `
  --backup-dir "<PASTA_BACKUP_IMUTAVEL>" `
  --report-out "<PASTA_RELATORIOS>\migration-dry-run.json"

# 2. Carga efetiva (somente após dry-run aprovado e autorização):
python backend/tools/migrate_sqlite_to_pg.py `
  --source-dir "<COPIA_FRIA_EXPLICITA_DATA_DIR>" `
  --target-dsn "host=127.0.0.1 port=<PORTA> dbname=<DB_DESCARTAVEL> user=postgres sslmode=disable" `
  --backup-dir "<PASTA_BACKUP_IMUTAVEL>" `
  --apply `
  --report-out "<PASTA_RELATORIOS>\migration-apply.json"
```
