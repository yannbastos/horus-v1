# HOR-164 / I-final A — Corte de Runtime para PostgreSQL, Remoção do SQLite e Gate

Data: 17/11/2026 · Branch: `yannbastos/sb-ifinal-a`

## 1. O que foi cortado e alterado

1. **`backend/src/horus_backend/api.py`**
   - Removidos `import sqlite3`, `horus.sqlite3`, `radar-youtube.sqlite3`, `store.connect()`, `PRAGMA integrity_check`, `PRAGMA user_version` e `sqlite3.Error`/`sqlite3.IntegrityError`.
   - `create_app` instancia `install_database(app, settings.data_dir, ...)` e constrói `Store(db_manager, data_dir=settings.data_dir)`, `RadarState(db_manager)`, `ImportService(settings.data_dir, store=store)` e `ReviewCommentService(settings.data_dir, manager.productions, store=store)` exclusivamente sobre o `DatabaseManager` de I1.
   - **Modo diagnóstico:** o `lifespan` sobe a API mesmo sem banco configurado/alcançável (`not_configured`, `unreachable`, `auth_failed`, `schema_mismatch`, `vault_unavailable`) sem criar nenhum arquivo local; inicia o `JobManager` ao conectar no startup ou após `POST /v1/database/connection` / `POST /v1/database/test` e fecha ao desconectar (`DELETE /v1/database/connection`).
   - `/health`, `/v1/database/*`, `/v1/security/status`, `/v1/update/activity` e `/v1/update/health` continuam operando em modo diagnóstico.
   - `@app.exception_handler(DatabaseRefused)` padroniza falhas 503 de qualquer rota de domínio (incluindo `Store`, `RadarState` e serviços) no envelope `{detail: {code: "database_unavailable", message, status}}` com `Cache-Control: no-store`.
   - `radar_demo` trata `psycopg.IntegrityError`; `download_whisper_model` consulta `horus.jobs` via `store.uow(readonly=True)` com `dict_row`; `update_backup` passa `store=store` e não captura `sqlite3.Error`.

2. **Remoção de `migrations.py` do runtime**
   - `backend/src/horus_backend/migrations.py` movido via `git mv` para `backend/tools/migrate_sqlite_pg/legacy_schema.py`.
   - `backend/tests/pg/migration_fixtures/synthetic_builder.py` atualizado para importar `MIGRATIONS` de `migrate_sqlite_pg.legacy_schema`.

3. **Empacotamento PyInstaller e dependências**
   - `scripts/backend-bundle-requirements.txt` atualizado via `uv export --project backend --no-dev --extra bundle --format requirements.txt --no-emit-project --frozen` a partir de `backend/uv.lock` (inclui `psycopg==3.3.9`, `psycopg-binary==3.3.9`, `psycopg-pool==3.3.3`, `tzdata==2026.6` com hashes SHA-256).
   - `scripts/build-backend.ps1` atualizado com `--collect-submodules psycopg --collect-submodules psycopg_pool --collect-all psycopg_binary`, preservando `--add-data $workerSources` (que inclui `horus_backend/database/supabase-ca.crt`).

4. **Fixtures PG de aplicação e testes**
   - `backend/tests/conftest.py`: adicionadas as novas fixtures PG (`pg_app_cluster`, `pg_app_database`, `pg_app_settings`, `pg_app`, `pg_client`, `pg_diagnostic_app`) sem remover as fixtures legadas.
   - `backend/tests/test_endpoint_auth.py`: os 3 testes de registro/streaming de mídia (`test_media_stream_ticket_is_bound_to_id_and_expiry`, `test_media_stream_refuses_file_swapped_for_symlink`, `test_media_stream_refuses_symlink_even_when_symlinks_cannot_be_created`) passaram a usar `pg_client`/`pg_app_settings`.
   - `backend/tests/test_no_hidden_sqlite.py` (novo): gate AST + gate textual + auto-teste dos scanners + teste de modo diagnóstico (200 em diagnóstico/health/status/update, 503 `database_unavailable` em rotas de domínio, zero `.db`/`.sqlite*` no `data_dir`).
   - `backend/tests/pg/test_journey_pg.py` (novo, `@pytest.mark.pg_strong`): bloqueio `sys.modules["sqlite3"] = None` e `_sqlite3 = None` no processo pai e no subprocesso `production_worker.py` (via `sitecustomize.py` injetado em `PYTHONPATH`), percorrendo jornada HTTP real em PG (`health` → `database/status` → 503 sem banco → `POST /v1/database/connection` → `health` conectado → mídia → `radar/datasets/demo` → checkpoint → workflow manual → roteiro + aprovação → produção simulada em subprocesso → revisão/comentário/exportação → zero `.db`/`.sqlite*` no `data_dir`).

## 2. O que sobrou de SQLite (Allowlist explícita)

- **`backend/src/**`:** **0** imports de `sqlite3`/`_sqlite3`/`aiosqlite`; **0** código SQL/SQLite. Permanecem apenas 6 linhas de comentário/docstring explicando a substituição do SQLite (`backups.py:3`, `data_deletion.py:18`, `database/__init__.py:1`, `local_data_export.py:3`, `retention.py:8,58`) e 2 linhas do sanitizador de perfil do Hermes externo (`security.py:694-695`).
- **`backend/tools/migrate_sqlite_pg/**` e `backend/tests/pg/migration_fixtures/**`:** únicos locais autorizados com `sqlite3` e `legacy_schema.py` (ferramenta de migração I-mig).
- **`scripts/` (fora do escopo do corte):** 4 scripts legados pré-existentes (`scripts/check-installer-data.py`, `scripts/ensaio_atualizacao_banco.py`, `scripts/measure-local-pilot.py`, `scripts/updater-e2e-support.py`) mantidos em `LEGACY_SCRIPTS_ALLOWLIST` no gate até atualização/remoção em HOR-163/pós-corte.

## 3. Defeitos/observações revelados pelo wiring

- **Log em `JobManager.run_retention()` (`jobs.py:174`):** durante `test_journey_pg.py`, `jobs.py:174` chama `run_retention(self.store, ...)` passando `Store`, enquanto `retention.py` espera `DatabaseManager` (com `.connection()`), registrando `WARNING Ciclo de retenção do Radar falhou: DatabaseRefused` sem impedir a inicialização nem o restante da jornada. Não editado aqui por estar fora da lista de arquivos de propriedade exclusiva (`jobs.py` / `retention.py`).
- **`test_supply_chain.py::test_no_secret_in_versioned_files`:** falha pré-existente em `backend/tests/pg/test_backup_export_pg.py: provider_key` (fixture sintética de I8, fora do escopo deste corte).

## 4. Fixtures/testes legados ainda pendentes de porte (próximo incremento)

Os arquivos abaixo em `backend/tests/` ainda referenciam `horus_backend.migrations`, `sqlite3` ou `.sqlite3` e dependem do porte das fixtures legadas:
- `test_adversarial_battery.py`, `test_api.py`, `test_backup_verification.py`, `test_crash_recovery_real.py`, `test_data_dir_acl.py`, `test_egress_gateway.py`, `test_error_center.py`, `test_exports.py`, `test_exports_low.py`, `test_human_checkpoints.py`, `test_license_consistency.py`, `test_license_reviews.py`, `test_local_data_restore.py`, `test_local_pilot.py`, `test_narration.py`, `test_notifications.py`, `test_persistence_contracts.py`, `test_production.py`, `test_progress_disconnect.py`, `test_radar_workflow.py`, `test_retention.py`, `test_review_2026_11_14.py`, `test_review_comments.py`, `test_scripts.py`, `test_scripts_hermes.py`, `test_scripts_hermes_adversarial.py`, `test_scripts_manual.py`, `test_secrets_at_rest_scan.py`, `test_sqlite_hardening.py`, `test_telemetry_and_local_data.py`, `test_transcription.py`, `test_transmission_audit.py`, `test_update_backup.py`, `test_whisper_download.py`, `test_whisper_egress.py`, `test_workflows.py`, `test_worktree_config.py`, `radar_youtube/test_core.py`.

## 5. Verificações executadas

- `pytest backend/tests/test_no_hidden_sqlite.py backend/tests/test_api_contract_snapshot.py backend/tests/test_endpoint_auth.py -q`: **73 passed, 4 skipped** (15.17s).
- `pytest backend/tests/pg/test_journey_pg.py backend/tests/pg/test_migration_sqlite_to_pg.py -q`: **7 passed** (16.22s).
- `pytest backend/tests/test_supply_chain.py backend/tests/test_network_inventory.py -q`: **30 passed, 1 failed** (`test_no_secret_in_versioned_files` por `backend/tests/pg/test_backup_export_pg.py: provider_key` pré-existente de I8).
