# HOR-164 / I8 — Backup, Exportação Local, Atualização-Backup e Exclusão de Dados em PostgreSQL

Data: 17/11/2026 · Branch: `yannbastos/sb-i8`

## 1. O que foi migrado

1. **`backend/src/horus_backend/backups.py`**
   - Removidos `sqlite3`, `PRAGMA`, `sqlite_master`, `json_extract` e caminhos `.sqlite3`.
   - `inspect_database(store)` usa `store.uow(readonly=True)` com o papel `horus_app` (respeitando RLS), propagando `DatabaseUnavailable` (HTTP 503) sem fallback.
   - `inventory`, `plan` e `remove_backups` operam sobre pacotes lógicos v2 (`horus-pg-backup-v*.zip`, `pre-update-*.zip`, `pre-exclusao-*.zip`) preservando as 4 regras de retenção e a sinalização de backups com Radar não redigido.
   - `restore_to_isolated_pg(package, target_conn)` restaura exclusivamente em banco PostgreSQL **isolado e vazio**, recusando qualquer banco com dados operacionais ou schema incompatível, verificando contagens e SHA-256 por tabela e sincronizando sequences.
   - Procedimento separado de `pg_dump` completo documentado em `scripts/pg-backup-maintenance.py` (credencial via cofre `keyring` + `PGPASSFILE` temporário protegido + `--no-password` + `verify-full` com `supabase-ca.crt`, nunca em `argv`/log/Git; não executado contra banco real).

2. **`backend/src/horus_backend/local_data_export.py`**
   - Novo formato versionado: `schema_version=2`, `format="horus-pg-export-v2"`.
   - `export_local_data(data_dir, destination, *, store=...)` lê todas as tabelas `horus.*` em transação única `REPEATABLE READ` somente leitura via `horus_app` (`pg_dump/<table>.jsonl` com hash SHA-256 determinístico por tabela) e separa os arquivos locais permitidos em `local_files/productions/<id>/*.json`, mantendo exclusões (`integrations/`, `models/`, `exports/`, `.quarentena/`, `update-backups/`, links, `SECRET_SHAPE`).
   - `verify_export` valida estrutura ZIP, `SAFE_NAME`, symlinks, tamanhos, SHA-256 por tabela e arquivo, contagens JSONL e ausência de segredos.
   - `restore_local_data` restaura `local_files/` atomicamente para pasta nova/vazia e, quando `target_conn` é informada, restaura o dump PG em banco isolado e vazio.

3. **`backend/src/horus_backend/update_backup.py`**
   - Preserva o contrato `/v1/update/backup` (`ok`, `label` seguro `pre-update-<hex>.zip`, `sha256`, `files`) e adiciona `code="update_backup_verified"` e `remote_proof`.
   - Falha fechada: `DatabaseUnavailable` (503) se PG indisponível; `UpdateBackupBlocked` (subclasse de `ValueError` com `.code` estável) se o pacote estiver incompleto, falhar na verificação ou tiver comprovante remoto incompatível.

4. **`backend/src/horus_backend/data_deletion.py`**
   - Removido `sqlite3` e `deletion-audit.json`.
   - `DeletionAudit` persiste e verifica a cadeia append-only em `horus.deletion_events` via `store.uow()` com `pg_advisory_xact_lock(164, 3)`, `payload_canonical` UTF-8 e `hash` SHA-256 compatíveis com o trigger `horus.deletion_chain()`. Aceita assinatura legada `DeletionAudit(settings.data_dir)` de `jobs.py` sem editar `jobs.py`.
   - `DataDeletionService` audita `started` em PG **antes** de mover arquivos para `.quarentena/<audit_id>/` e `completed`/`failed` **depois**. Queda de rede/PG após o movimento deixa estado `incomplete` reconciliável por hash via `reconcile(audit_id)`.
   - Removida qualquer promessa de `secure_delete` / zeragem física imediata; `notice` explicita retenção real PostgreSQL (MVCC, WAL e backups do provedor).

5. **`backend/src/horus_backend/persistence_contracts.py`**
   - Removidos `sqlite3`, `_DDL`, `BEGIN IMMEDIATE`, `rowid`, `?` e `store.connect()`.
   - `LocalUnitOfWork` e `LocalSyncJournal` operam sobre `store.uow()` em `horus.preferences` com atomicidade e rollback transacional real em PostgreSQL.

## 2. Tabela de mapeamento: testes legados -> testes PG

| Teste legado | Novo teste PG (`backend/tests/pg/...`) | Status |
|---|---|---|
| `test_backup_verification.py::test_verify_integrity_counts_comparison_and_no_writes` | `test_backup_export_pg.py::test_inspect_database_uses_horus_app_and_counts_tables` | Migrado |
| `test_backup_verification.py::test_corrupt_backup_is_reported_and_cannot_restore` | `test_backup_export_pg.py::test_verify_detects_tampering_and_unsafe_names` | Migrado |
| `test_backup_verification.py::test_restore_and_current_version_migrate_only_copy` | `test_backup_export_pg.py::test_export_verify_and_roundtrip_restore_to_isolated_empty_db` | Migrado |
| `test_backup_verification.py::test_existing_destination_and_source_refused` | `test_backup_export_pg.py::test_export_verify_and_roundtrip_restore_to_isolated_empty_db` | Migrado |
| `test_backup_verification.py::test_pending_wal_and_existing_destination_sidecar_refused` | N/A em PG (sidecars `-wal`/`-journal` SQLite eliminados; `REPEATABLE READ` via UoW) | Substituído por `REPEATABLE READ` |
| `test_backup_verification.py::test_missing_current_is_not_created` | `test_backup_export_pg.py::test_unavailable_pg_raises_503_without_sqlite_fallback` | Migrado (503 PG) |
| `test_backup_verification.py::test_corruption_does_not_skip_other_backups` | `test_backup_export_pg.py::test_backup_retention_policy_and_radar_flags` | Migrado |
| `test_backup_verification.py::test_counts_quote_table_names_and_show_new_tables` | `test_backup_export_pg.py::test_inspect_database_uses_horus_app_and_counts_tables` | Migrado |
| `test_update_backup.py::test_backup_live_wal_radar_preferences_restore_and_unique_label` | `test_backup_export_pg.py::test_update_backup_fail_closed_and_success` + `test_export_verify_and_roundtrip_restore_to_isolated_empty_db` | Migrado |
| `test_update_backup.py::test_missing_database_refused` | `test_backup_export_pg.py::test_unavailable_pg_raises_503_without_sqlite_fallback` | Migrado (503 PG) |
| `test_update_backup.py::test_concurrent_wal_writer_backup_restores_a_consistent_committed_snapshot` | `test_backup_export_pg.py::test_export_verify_and_roundtrip_restore_to_isolated_empty_db` (`REPEATABLE READ`) | Migrado |
| `test_update_backup.py::test_invalid_preference_refuses_incomplete_backup` | `test_backup_export_pg.py::test_update_backup_fail_closed_and_success` | Migrado |
| `test_update_backup.py::test_activity_not_limited_to_recent_100_and_backup_guard` | — | Pendente I-final (`create_app`) |
| `test_update_backup.py::test_preview_is_busy_until_provider_returns_even_on_failure` | — | Pendente I-final (`create_app`) |
| `test_telemetry_and_local_data.py::test_export_contains_database_settings_and_manifests_but_no_secrets_models_or_media` | `test_backup_export_pg.py::test_export_verify_and_roundtrip_restore_to_isolated_empty_db` | Migrado |
| `test_telemetry_and_local_data.py::test_export_is_consistent_with_a_live_wal_database_and_never_changes_the_source` | `test_backup_export_pg.py::test_export_verify_and_roundtrip_restore_to_isolated_empty_db` | Migrado |
| `test_telemetry_and_local_data.py::test_export_refuses_existing_destination_destination_inside_data_dir_and_missing_database` | `test_backup_export_pg.py::test_export_refuses_existing_dest_inside_data_dir_secrets_and_links` + `test_unavailable_pg_raises_503_without_sqlite_fallback` | Migrado |
| `test_telemetry_and_local_data.py::test_export_refuses_a_corrupt_database_and_creates_nothing` | `test_backup_export_pg.py::test_unavailable_pg_raises_503_without_sqlite_fallback` | Migrado (503 PG) |
| `test_telemetry_and_local_data.py::test_credential_shaped_or_invalid_json_is_excluded_from_manifests` | `test_backup_export_pg.py::test_export_refuses_existing_dest_inside_data_dir_secrets_and_links` | Migrado |
| `test_telemetry_and_local_data.py::test_links_are_never_followed` | `test_backup_export_pg.py::test_verify_detects_tampering_and_unsafe_names` | Migrado |
| `test_telemetry_and_local_data.py::test_verify_detects_tampering` | `test_backup_export_pg.py::test_verify_detects_tampering_and_unsafe_names` | Migrado |
| `test_telemetry_and_local_data.py::test_verify_refuses_unsafe_names_and_non_packages` | `test_backup_export_pg.py::test_verify_detects_tampering_and_unsafe_names` | Migrado |
| `test_telemetry_and_local_data.py::test_cli_export_and_verify` | `test_backup_export_pg.py::test_export_verify_and_roundtrip_restore_to_isolated_empty_db` | Migrado |
| `test_persistence_contracts.py::test_disabled_default_does_not_touch_store` | `test_persistence_contracts_pg.py::test_disabled_default_does_not_touch_store` | Migrado |
| `test_persistence_contracts.py::test_local_job_adapter_preserves_current_contract` | `test_persistence_contracts_pg.py::test_local_job_adapter_preserves_current_contract` | Migrado |
| `test_persistence_contracts.py::test_outbox_atomic_with_local_write_and_rollback` | `test_persistence_contracts_pg.py::test_outbox_atomic_with_local_write_and_rollback` | Migrado |
| `test_persistence_contracts.py::test_inbox_cursor_and_logout_are_scoped_and_atomic` | `test_persistence_contracts_pg.py::test_inbox_cursor_and_logout_are_scoped_and_atomic` | Migrado |
| `test_persistence_contracts.py::test_secrets_oversized_or_unauthorized_payload_never_queued` | `test_persistence_contracts_pg.py::test_secrets_oversized_or_unauthorized_payload_never_queued` | Migrado |
| `test_persistence_contracts.py::test_queue_limit_preserves_existing_pending` | `test_persistence_contracts_pg.py::test_queue_limit_preserves_existing_pending` | Migrado |
| `test_persistence_contracts.py::test_remote_review_is_only_historical` | `test_persistence_contracts_pg.py::test_remote_review_is_only_historical` | Migrado |
| `test_persistence_contracts.py::test_verified_identity_semantics_are_not_signature_validation` | `test_persistence_contracts_pg.py::test_verified_identity_semantics_are_not_signature_validation` | Migrado |
| `test_data_deletion.py::test_preview_is_exact_read_only_and_lists_db_rows_kept_and_dependencies` | `test_deletion_pg.py::test_preview_is_exact_lists_db_kept_and_honest_retention_notice` | Migrado (HTTP pendente I-final) |
| `test_data_deletion.py::test_wrong_phrase_wrong_token_unknown_plan_and_reuse_delete_nothing` | `test_deletion_pg.py::test_wrong_phrase_wrong_token_unknown_plan_and_single_use` | Migrado |
| `test_data_deletion.py::test_execute_makes_verified_backup_quarantines_files_audits_and_marks_production_deleted` | `test_deletion_pg.py::test_execute_backup_quarantine_pg_audit_chain_and_restore` | Migrado |
| `test_data_deletion.py::test_active_jobs_block_preview_execute_and_a_plan_made_before_the_job_started` | `test_deletion_pg.py::test_active_jobs_and_changed_files_block_execution` | Migrado |
| `test_data_deletion.py::test_files_changed_after_preview_invalidate_the_plan` | `test_deletion_pg.py::test_active_jobs_and_changed_files_block_execution` | Migrado |
| `test_data_deletion.py::test_backup_failure_aborts_before_any_move_and_without_audit_start` | `test_deletion_pg.py::test_backup_failure_aborts_before_move_and_without_audit_start` | Migrado |
| `test_data_deletion.py::test_unverified_backup_aborts` | `test_deletion_pg.py::test_backup_failure_aborts_before_move_and_without_audit_start` | Migrado |
| `test_data_deletion.py::test_move_failure_rolls_back_every_item_and_audits_the_failure` | `test_deletion_pg.py::test_move_failure_rolls_back_and_audits_failed` | Migrado |
| `test_data_deletion.py::test_restore_brings_files_back_verified_once_and_refuses_conflicts_or_tampering` | `test_deletion_pg.py::test_execute_backup_quarantine_pg_audit_chain_and_restore` | Migrado |
| `test_data_deletion.py::test_audit_is_hash_chained_and_tampering_blocks_new_deletions` | `test_deletion_pg.py::test_pg_triggers_enforce_append_only_and_valid_hash_chain` | Migrado |
| `test_data_deletion.py::test_backups_are_never_deleted_automatically_across_cycles` | `test_deletion_pg.py::test_execute_backup_quarantine_pg_audit_chain_and_restore` | Migrado |
| `test_data_deletion.py::test_invalid_requests_and_authentication` | — | Pendente I-final (`create_app`) |
| `test_data_deletion.py::test_imports_can_be_deleted_hidden_and_restored` | `test_deletion_pg.py::test_imports_can_be_deleted_and_restored` | Migrado |
| *(novo I8)* Queda de rede após movimento em disco → reconciliação por hash | `test_deletion_pg.py::test_network_drop_after_move_leaves_incomplete_and_reconciles_by_hash` | Novo |

## 3. Textos de UI e documentação que precisam de atualização (não editados em I8)

- `frontend/src/components/DataDeletionDialog.tsx:276-280`: menciona "banco de dados e manifestos JSON" no pacote de backup de pré-exclusão; atualizar para explicitar dump lógico PostgreSQL, retenção real (MVCC/WAL/backups do provedor, sem zeragem física imediata).
- `docs/ARCHITECTURE.md:23`: cita SQLite, `secure_delete` e checkpoint `TRUNCATE` do WAL.
- `docs/seguranca-dados.md:3,9,17,22,26,30,48`: documenta `horus.sqlite3`, `PRAGMA secure_delete=ON`, `-wal` e `VACUUM`.
- `docs/adr-atualizador.md:71`: referencia `workspace/horus.sqlite3` e `horus.backup-v*.sqlite3`.
- `docs/procedimento-atualizacao-banco-v9-v12.md:41,43,55,64`: referencia `horus.sqlite3` e `PRAGMA` (preservar apenas como registro histórico do importador legado).
- `docs/radar-youtube.md:42` e `docs/ROADMAP.md:100`: citam `secure_delete` e checkpoint do WAL.

## 4. Verificações executadas

- `backend/tests/pg/test_backup_export_pg.py`, `backend/tests/pg/test_deletion_pg.py`, `backend/tests/pg/test_persistence_contracts_pg.py`: **32 passed** (25.58s).
- `backend/tests/test_supply_chain.py`: **24 passed** (1.99s).
- `backend/tests/test_network_inventory.py`: **7 passed** (0.88s).
- `backend/tests/test_api_contract_snapshot.py`: 1 passed, 2 failed na inicialização de `create_app` (`api.py:124` ainda passa caminho SQLite para `RadarState`, pendente I-final).
- `backend/tests/test_endpoint_auth.py`: 11 passed, 1 skipped, 56 errors / 1 failed pelo mesmo `api.py:124` (`create_app` pendente I-final).
