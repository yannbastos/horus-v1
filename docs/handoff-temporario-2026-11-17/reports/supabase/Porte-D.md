# HOR-164 (Porte D) — Porte dos Testes Legados de Dados Locais, Backup, Exclusão e Endurecimento para PostgreSQL

Data: 17/11/2026 · Branch: `yannbastos/sb-porte-d`

## 1. Resumo e Contagens (Antes × Depois)

- **Antes (pós-corte I-final A):** os 6 arquivos do Grupo D (`test_sqlite_hardening.py`, `test_telemetry_and_local_data.py`, `test_data_deletion.py`, `test_backup_verification.py`, `test_disk_encryption.py`, `test_local_data_restore.py`) falhavam **100% na coleta** (6 erros de importação por `horus_backend.migrations`, `backups.restore` e dependências cruzadas com `test_workflows`/`test_review_comments`).
- **Helper comum criado:** `backend/tests/support_d.py` (sem editar `conftest.py` nem `backend/tests/pg/conftest.py`).
- **Depois (execução em PG descartável):**
  - `test_disk_encryption.py`: **14 passed**
  - `test_backup_verification.py`: **8 passed**
  - `test_telemetry_and_local_data.py`: **15 passed, 2 skipped** (skips pré-existentes: `horus-storybook` ausente e `symlink` sem privilégio no Windows)
  - `test_local_data_restore.py`: **6 passed**
  - `test_data_deletion.py`: **12 passed, 1 xfailed** (`strict=True`, defeito de produto registrado na seção 3)
  - `test_sqlite_hardening.py`: **25 passed** (9 testes intrínsecos de `PRAGMA`/`VACUUM`/`-wal` SQLite declarados OBSOLETOS na tabela abaixo)
  - **Grupo D completo (execução conjunta):** **80 passed, 2 skipped, 1 xfailed** (168.26s).
  - **Transversais (`test_supply_chain.py`, `test_network_inventory.py`, `test_api_contract_snapshot.py`, `test_endpoint_auth.py`, `test_no_hidden_sqlite.py`):** **104 passed, 4 skipped**.

## 2. Tabela Completa: Teste Legado → Novo Teste PG / Status

### `test_disk_encryption.py`

| Teste legado (`arquivo::nome`) | Status | Destino / Justificativa |
|---|---|---|
| `test_disk_encryption.py::test_value_mapping_only_trusts_on_and_off` (6 casos) | Portado | `test_disk_encryption.py::test_value_mapping_only_trusts_on_and_off` |
| `test_disk_encryption.py::test_failure_or_elevation_requirement_degrades_to_unverified_with_guidance` (3 casos) | Portado | `test_disk_encryption.py::test_failure_or_elevation_requirement_degrades_to_unverified_with_guidance` |
| `test_disk_encryption.py::test_guidance_explains_what_why_how_and_home_edition` | Portado | `test_disk_encryption.py::test_guidance_explains_what_why_how_and_home_edition` |
| `test_disk_encryption.py::test_volume_without_drive_letter_is_unverified_and_never_runs_the_query` | Portado | `test_disk_encryption.py::test_volume_without_drive_letter_is_unverified_and_never_runs_the_query` |
| `test_disk_encryption.py::test_real_query_uses_run_sync_with_fixed_read_only_command_and_validated_letter` | Portado | `test_disk_encryption.py::test_real_query_uses_run_sync_with_fixed_read_only_command_and_validated_letter` |
| `test_disk_encryption.py::test_result_is_cached_briefly_for_the_real_query` | Portado | `test_disk_encryption.py::test_result_is_cached_briefly_for_the_real_query` |
| `test_disk_encryption.py::test_diagnostics_exposes_disk_encryption_without_affecting_production_readiness` | Portado (`pg_client`/`pg_app_settings`) | `test_disk_encryption.py::test_diagnostics_exposes_disk_encryption_without_affecting_production_readiness` |

### `test_backup_verification.py`

| Teste legado (`arquivo::nome`) | Status | Destino / Justificativa |
|---|---|---|
| `test_backup_verification.py::test_verify_integrity_counts_comparison_and_no_writes` | Portado | `test_backup_verification.py::test_verify_integrity_counts_comparison_and_no_writes` (`inspect_database` + `export_local_data` + `verify_export`) |
| `test_backup_verification.py::test_corrupt_backup_is_reported_and_cannot_restore` | Portado | `test_backup_verification.py::test_corrupt_backup_is_reported_and_cannot_restore` (`verify_export` + `restore_to_isolated_pg`) |
| `test_backup_verification.py::test_restore_and_current_version_migrate_only_copy` | Portado | `test_backup_verification.py::test_restore_and_current_version_migrate_only_copy` (`restore_to_isolated_pg` em banco isolado vazio) |
| `test_backup_verification.py::test_existing_destination_and_source_refused` | Portado | `test_backup_verification.py::test_existing_destination_and_source_refused` (`restore_to_isolated_pg` recusa banco não vazio) |
| `test_backup_verification.py::test_pending_wal_and_existing_destination_sidecar_refused` | Equivalente PG / OBSOLETO (sidecar `-wal`) | `test_backup_verification.py::test_consistent_repeatable_read_snapshot_ignores_uncommitted_writer` (sidecars `-wal`/`-journal` SQLite eliminados; substituído por leitura `REPEATABLE READ` via UoW) |
| `test_backup_verification.py::test_missing_current_is_not_created` | Portado | `test_backup_verification.py::test_missing_current_is_not_created` (`DatabaseUnavailable` sem criar arquivos) |
| `test_backup_verification.py::test_corruption_does_not_skip_other_backups` | Portado | `test_backup_verification.py::test_corruption_does_not_skip_other_backups` (`backups.inventory` em pacotes v2) |
| `test_backup_verification.py::test_counts_quote_table_names_and_show_new_tables` | Portado | `test_backup_verification.py::test_counts_quote_table_names_and_show_new_tables` (`sql.Identifier` em `horus."quoted""name"`) |

### `test_telemetry_and_local_data.py`

| Teste legado (`arquivo::nome`) | Status | Destino / Justificativa |
|---|---|---|
| `test_telemetry_and_local_data.py::test_no_dependency_in_any_lockfile_is_a_telemetry_or_auto_update_library` | Portado (intacto) | `test_telemetry_and_local_data.py::test_no_dependency_in_any_lockfile_is_a_telemetry_or_auto_update_library` |
| `test_telemetry_and_local_data.py::test_tauri_has_no_updater_http_or_shell_plugin_and_only_loopback_connect_src` | Portado (intacto) | `test_telemetry_and_local_data.py::test_tauri_has_no_updater_http_or_shell_plugin_and_only_loopback_connect_src` |
| `test_telemetry_and_local_data.py::test_versioned_source_has_no_telemetry_hosts_or_sdk_imports` | Portado (intacto) | `test_telemetry_and_local_data.py::test_versioned_source_has_no_telemetry_hosts_or_sdk_imports` |
| `test_telemetry_and_local_data.py::test_every_subprocess_starts_with_telemetry_opt_outs` | Portado (intacto) | `test_telemetry_and_local_data.py::test_every_subprocess_starts_with_telemetry_opt_outs` |
| `test_telemetry_and_local_data.py::test_model_workers_also_force_offline_and_no_telemetry_in_their_own_env` | Portado (intacto) | `test_telemetry_and_local_data.py::test_model_workers_also_force_offline_and_no_telemetry_in_their_own_env` |
| `test_telemetry_and_local_data.py::test_storybook_telemetry_is_disabled_when_the_sibling_repo_is_available` | Portado (intacto) | `test_telemetry_and_local_data.py::test_storybook_telemetry_is_disabled_when_the_sibling_repo_is_available` |
| `test_telemetry_and_local_data.py::test_export_contains_database_settings_and_manifests_but_no_secrets_models_or_media` | Portado | `test_telemetry_and_local_data.py::test_export_contains_database_settings_and_manifests_but_no_secrets_models_or_media` (formato v2: `pg_dump/*.jsonl`, `local_files/*`) |
| `test_telemetry_and_local_data.py::test_export_is_consistent_with_a_live_wal_database_and_never_changes_the_source` | Portado | `test_telemetry_and_local_data.py::test_export_is_consistent_with_a_live_wal_database_and_never_changes_the_source` (`REPEATABLE READ` ignora escrita concorrente não commitada) |
| `test_telemetry_and_local_data.py::test_export_refuses_existing_destination_destination_inside_data_dir_and_missing_database` | Portado | `test_telemetry_and_local_data.py::test_export_refuses_existing_destination_destination_inside_data_dir_and_missing_database` |
| `test_telemetry_and_local_data.py::test_export_refuses_a_corrupt_database_and_creates_nothing` | Portado | `test_telemetry_and_local_data.py::test_export_refuses_a_corrupt_database_and_creates_nothing` (`DatabaseUnavailable` sem criar `.zip`) |
| `test_telemetry_and_local_data.py::test_credential_shaped_or_invalid_json_is_excluded_from_manifests` | Portado | `test_telemetry_and_local_data.py::test_credential_shaped_or_invalid_json_is_excluded_from_manifests` |
| `test_telemetry_and_local_data.py::test_links_are_never_followed` | Portado | `test_telemetry_and_local_data.py::test_links_are_never_followed` |
| `test_telemetry_and_local_data.py::test_verify_detects_tampering` (3 casos) | Portado | `test_telemetry_and_local_data.py::test_verify_detects_tampering` (`pg_dump/media.jsonl` e `local_files/*`) |
| `test_telemetry_and_local_data.py::test_verify_refuses_unsafe_names_and_non_packages` | Portado | `test_telemetry_and_local_data.py::test_verify_refuses_unsafe_names_and_non_packages` |
| `test_telemetry_and_local_data.py::test_cli_export_and_verify` | Portado | `test_telemetry_and_local_data.py::test_cli_export_and_verify` |

### `test_local_data_restore.py`

| Teste legado (`arquivo::nome`) | Status | Destino / Justificativa |
|---|---|---|
| `test_local_data_restore.py::test_end_to_end_restore_boots_backend_with_production_preferences_and_files` | Portado | `test_local_data_restore.py::test_end_to_end_restore_boots_backend_with_production_preferences_and_files` (`restore_local_data` em pasta nova + banco PG isolado vazio) |
| `test_local_data_restore.py::test_restores_into_an_existing_empty_folder_and_cli_works` | Portado | `test_local_data_restore.py::test_restores_into_an_existing_empty_folder_and_cli_works` |
| `test_local_data_restore.py::test_refuses_non_empty_existing_file_link_and_missing_parent_without_touching_anything` | Portado | `test_local_data_restore.py::test_refuses_non_empty_existing_file_link_and_missing_parent_without_touching_anything` |
| `test_local_data_restore.py::test_hostile_or_tampered_packages_are_refused_before_writing` | Portado | `test_local_data_restore.py::test_hostile_or_tampered_packages_are_refused_before_writing` |
| `test_local_data_restore.py::test_newer_database_schema_is_refused_with_clear_message` | Portado | `test_local_data_restore.py::test_newer_database_schema_is_refused_with_clear_message` |
| `test_local_data_restore.py::test_failure_midway_leaves_no_partial_destination` | Portado | `test_local_data_restore.py::test_failure_midway_leaves_no_partial_destination` |

### `test_data_deletion.py`

| Teste legado (`arquivo::nome`) | Status | Destino / Justificativa |
|---|---|---|
| `test_data_deletion.py::test_preview_is_exact_read_only_and_lists_db_rows_kept_and_dependencies` | Portado | `test_data_deletion.py::test_preview_is_exact_read_only_and_lists_db_rows_kept_and_dependencies` (comentários agora em PG `horus.production_comments`, não em disco) |
| `test_data_deletion.py::test_wrong_phrase_wrong_token_unknown_plan_and_reuse_delete_nothing` | Portado | `test_data_deletion.py::test_wrong_phrase_wrong_token_unknown_plan_and_reuse_delete_nothing` |
| `test_data_deletion.py::test_execute_makes_verified_backup_quarantines_files_audits_and_marks_production_deleted` | Portado | `test_data_deletion.py::test_execute_makes_verified_backup_quarantines_files_audits_and_marks_production_deleted` |
| `test_data_deletion.py::test_active_jobs_block_preview_execute_and_a_plan_made_before_the_job_started` | Portado | `test_data_deletion.py::test_active_jobs_block_preview_execute_and_a_plan_made_before_the_job_started` |
| `test_data_deletion.py::test_files_changed_after_preview_invalidate_the_plan` | Portado | `test_data_deletion.py::test_files_changed_after_preview_invalidate_the_plan` |
| `test_data_deletion.py::test_backup_failure_aborts_before_any_move_and_without_audit_start` | Portado | `test_data_deletion.py::test_backup_failure_aborts_before_any_move_and_without_audit_start` |
| `test_data_deletion.py::test_unverified_backup_aborts` | Portado | `test_data_deletion.py::test_unverified_backup_aborts` |
| `test_data_deletion.py::test_move_failure_rolls_back_every_item_and_audits_the_failure` | Portado | `test_data_deletion.py::test_move_failure_rolls_back_every_item_and_audits_the_failure` |
| `test_data_deletion.py::test_restore_brings_files_back_verified_once_and_refuses_conflicts_or_tampering` | Portado | `test_data_deletion.py::test_restore_brings_files_back_verified_once_and_refuses_conflicts_or_tampering` |
| `test_data_deletion.py::test_audit_is_hash_chained_and_tampering_blocks_new_deletions` | Portado | `test_data_deletion.py::test_audit_is_hash_chained_and_tampering_blocks_new_deletions` (prova trigger append-only em `horus_app` + adulteração via admin) |
| `test_data_deletion.py::test_backups_are_never_deleted_automatically_across_cycles` | Portado | `test_data_deletion.py::test_backups_are_never_deleted_automatically_across_cycles` |
| `test_data_deletion.py::test_invalid_requests_and_authentication` | Portado | `test_data_deletion.py::test_invalid_requests_and_authentication` |
| `test_data_deletion.py::test_imports_can_be_deleted_hidden_and_restored` | **Revela defeito** (`xfail strict`) | `test_data_deletion.py::test_imports_can_be_deleted_hidden_and_restored` (`GET /v1/imports/{id}` retorna `409` em vez de `404` após exclusão) |

### `test_sqlite_hardening.py`

| Teste legado (`arquivo::nome`) | Status | Destino / Justificativa |
|---|---|---|
| `test_sqlite_hardening.py::test_every_connection_enables_secure_delete` | Equivalente PG / OBSOLETO (`PRAGMA`) | `test_sqlite_hardening.py::test_pg_connection_runs_as_horus_app_with_foreign_keys_and_timeouts` (valida papel `horus_app` não-superuser e `transaction_read_only`) |
| `test_sqlite_hardening.py::test_retention_leaves_no_marker_in_db_or_wal` | Equivalente PG | `test_sqlite_hardening.py::test_retention_redacts_expired_radar_payload_without_leaving_marker` |
| `test_sqlite_hardening.py::test_control_without_secure_delete_marker_survives` | **OBSOLETO** | Investigação empírica específica de `PRAGMA secure_delete=OFF` no arquivo `.sqlite3`; sem equivalente em PG |
| `test_sqlite_hardening.py::test_secure_delete_zeroes_overflow_pages` | **OBSOLETO** | Investigação específica de páginas de overflow SQLite; sem equivalente em PG |
| `test_sqlite_hardening.py::test_wal_keeps_old_value_until_truncate_checkpoint` | **OBSOLETO** | Específico de arquivo `-wal` e `PRAGMA wal_checkpoint(TRUNCATE)` SQLite |
| `test_sqlite_hardening.py::test_closing_last_connection_removes_wal_but_not_plain_residue` | **OBSOLETO** | Específico de remoção de `-wal` ao fechar última conexão SQLite |
| `test_sqlite_hardening.py::test_checkpoint_with_active_reader_is_busy_and_does_not_break_cycle` | Equivalente PG | `test_sqlite_hardening.py::test_retention_with_active_reader_does_not_block_or_break_cycle` (prova MVCC com leitor `REPEATABLE READ` concorrente) |
| `test_sqlite_hardening.py::test_checkpoint_errors_are_reported_not_raised` | **OBSOLETO** | Específico de erro `OperationalError` ao abrir conexão de `wal_checkpoint` SQLite; PG não faz checkpoint via `retention.py` |
| `test_sqlite_hardening.py::test_dry_run_never_checkpoints` | Equivalente PG | `test_sqlite_hardening.py::test_dry_run_never_mutates_database` |
| `test_sqlite_hardening.py::test_job_manager_cycle_tracks_pending_checkpoint` | **OBSOLETO** | Específico de `checkpoint_pending` do `wal_checkpoint` SQLite (`RetentionReport.checkpoint_pending` é sempre `False` em PG) |
| `test_sqlite_hardening.py::test_integrity_and_row_counts_after_cycle` | Portado | `test_sqlite_hardening.py::test_integrity_and_row_counts_after_cycle` |
| `test_sqlite_hardening.py::test_reopen_and_recover_after_failure_in_the_middle` | Portado | `test_sqlite_hardening.py::test_reopen_and_recover_after_failure_in_the_middle` |
| `test_sqlite_hardening.py::test_copy_of_files_mid_transaction_recovers_from_wal` | Equivalente PG | `test_sqlite_hardening.py::test_uncommitted_transaction_never_leaks_partial_write` |
| `test_sqlite_hardening.py::test_preexisting_residue_needs_one_time_vacuum` | **OBSOLETO** | Específico de `freelist_count` e `VACUUM` SQLite |
| `test_sqlite_hardening.py::test_auto_vacuum_shrinks_file_but_does_not_replace_secure_delete` | **OBSOLETO** | Específico de `PRAGMA auto_vacuum=FULL` SQLite |
| `test_sqlite_hardening.py::test_vacuum_cost_scales_with_database_size_and_needs_space` | **OBSOLETO** | Específico de `VACUUM` / `VACUUM INTO` SQLite |
| `test_sqlite_hardening.py::test_migration_created_backup_is_inventoried_with_radar_flag` | Portado | `test_sqlite_hardening.py::test_migration_created_backup_is_inventoried_with_radar_flag` |
| `test_sqlite_hardening.py::test_inventory_radar_detection_variants` | Portado | `test_sqlite_hardening.py::test_inventory_radar_detection_variants` |
| `test_sqlite_hardening.py::test_dry_run_lists_reasons_and_deletes_nothing` | Portado | `test_sqlite_hardening.py::test_dry_run_lists_reasons_and_deletes_nothing` |
| `test_sqlite_hardening.py::test_removal_disabled_without_explicit_configuration` | Portado | `test_sqlite_hardening.py::test_removal_disabled_without_explicit_configuration` |
| `test_sqlite_hardening.py::test_explicit_removal_keeps_latest_only_and_future_backups` | Portado | `test_sqlite_hardening.py::test_explicit_removal_keeps_latest_only_and_future_backups` |
| `test_sqlite_hardening.py::test_single_backup_is_never_removed` | Portado | `test_sqlite_hardening.py::test_single_backup_is_never_removed` |
| `test_sqlite_hardening.py::test_removal_error_is_reported_and_others_continue` | Portado | `test_sqlite_hardening.py::test_removal_error_is_reported_and_others_continue` |
| `test_sqlite_hardening.py::test_retention_days_environment_parsing` (9 casos) | Portado (intacto) | `test_sqlite_hardening.py::test_retention_days_environment_parsing` |
| `test_sqlite_hardening.py::test_cli_lists_by_default_and_requires_env_to_apply` | Portado | `test_sqlite_hardening.py::test_cli_lists_by_default_and_requires_env_to_apply` |
| `test_sqlite_hardening.py::test_backups_are_never_touched_by_retention_cycle` | Portado | `test_sqlite_hardening.py::test_backups_are_never_touched_by_retention_cycle` |

## 3. Defeitos de Produto Revelados

1. **`GET /v1/imports/{import_id}` retorna `409 (import_missing_file)` em vez de `404` após exclusão via `/v1/data-deletion/execute` (`api.py:300-307` / `project_import.py`):**
   - `GET /v1/imports` oculta corretamente a importação excluída, mas `GET /v1/imports/{import_id}` chama `imports.get(import_id)` sem checar `deletion_audit`, recebendo `ImportRefused("import_missing_file", ..., 409)` porque os arquivos foram movidos para `.quarentena/`.
   - O teste `test_data_deletion.py::test_imports_can_be_deleted_hidden_and_restored` foi mantido fiel à asserção original (`404`) com `@pytest.mark.xfail(strict=True)` até correção em `api.py` (fora do escopo do Grupo D).

## 4. Verificações Executadas

- `backend/tests/test_disk_encryption.py`: **14 passed**
- `backend/tests/test_backup_verification.py`: **8 passed**
- `backend/tests/test_telemetry_and_local_data.py`: **15 passed, 2 skipped**
- `backend/tests/test_local_data_restore.py`: **6 passed**
- `backend/tests/test_data_deletion.py`: **12 passed, 1 xfailed**
- `backend/tests/test_sqlite_hardening.py`: **25 passed**
- Transversais (`test_supply_chain.py`, `test_network_inventory.py`, `test_api_contract_snapshot.py`, `test_endpoint_auth.py`, `test_no_hidden_sqlite.py`): **104 passed, 4 skipped**
