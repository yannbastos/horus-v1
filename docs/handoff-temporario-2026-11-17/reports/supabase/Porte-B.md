# HOR-164 / Porte B — Testes Legados Portados para PostgreSQL (Workflows, Checkpoints, Segurança e Saída)

Data: 17/11/2026 · Branch: `yannbastos/sb-porte-b` · Helper compartilhado: `backend/tests/support_b.py`

## 1. Contagens antes × depois

| Métrica | Antes (pós-corte I-final A) | Depois (Porte B em PG) |
|---|---|---|
| Arquivos do Grupo B | 10 | 10 (+ `support_b.py`) |
| Coleta | 10 erros de coleta (`ModuleNotFoundError: horus_backend.migrations` / `Store(path)`) | 300 casos coletados, 0 erros |
| Execução do Grupo B completo | 0 executados | **298 passed, 2 xfailed** (0 failed, 0 skipped) |
| Transversais (`supply_chain`, `network_inventory`, `api_contract_snapshot`, `no_hidden_sqlite`, `endpoint_auth`) | — | **104 passed, 4 skipped** (0 failed) |

## 2. Defeitos de produto encontrados (não editados — fora do escopo de arquivos do Grupo B)

1. **Ordem de shutdown entre `api.py` e `database/router.py` (`install_database`):**
   `database_router` registra um `lifespan` próprio que fecha `db_manager._pool` **antes** do `finally` do `lifespan` de `api.py` (`await manager.close()`). Consequências:
   - `worker_lock.close()` recebe `DatabaseUnavailable` e não reduz `lease_until` em `horus.worker_instances` no encerramento limpo, bloqueando reinícios nos 30 s seguintes (`RuntimeError: Outro backend já executa tarefas...`). Em `support_b.py`, `client_for` libera `worker_lock.close()` antes do `__exit__` do `TestClient` para permitir testes de reinício.
   - Se houver etapa em execução no shutdown (`test_workflows.py::test_second_backend_cannot_recover_another_workers_running_step`), o cancelamento da task tenta persistir `interrupted` com o pool já fechado e propaga `DatabaseUnavailable` (`xfail(strict=True)`).
2. **`POST /v1/jobs` aceita `\x00` no `path` e falha com 500 no PostgreSQL:**
   Em `test_adversarial_battery.py::test_hostile_file_names_are_refused_by_every_client_path_endpoint[a\x00.mp4]`, `TranscriptionRequest` não rejeita `\x00`, e `Store.create_job` falha com `psycopg.errors.UntranslatableCharacter` ao gravar `jsonb` (`xfail(strict=True)`).
3. **Pré-existente (`jobs.py:174`):** `JobManager.run_retention()` passa `Store` para `retention.run_retention()` que espera `DatabaseManager`, registrando `WARNING Ciclo de retenção do Radar falhou: DatabaseRefused`.

## 3. Mapeamento completo dos testes do Grupo B

### `test_human_checkpoints.py` (31 funções)

| Teste legado (`test_human_checkpoints.py::...`) | Status |
|---|---|
| `test_grant_records_who_when_what_limit_and_expiry` | portado (`pg_store`, `Clock` UTC atual) |
| `test_invalid_grants_are_refused_and_store_nothing` | portado (`pg_store`) |
| `test_grant_is_idempotent_and_key_cannot_be_reused_for_another_request` | portado (`pg_store`) |
| `test_backend_ceiling_refuses_larger_grants_before_insert` | portado (`pg_store`, `uow` com `db=conn`) |
| `test_backend_ceiling_accepts_lower_or_equal_grants` | portado (`pg_store`) |
| `test_backend_ceiling_cannot_be_bypassed_with_another_currency` | portado (`pg_store`) |
| `test_monetary_hermes_grant_requires_backend_configuration` | portado (`pg_store`) |
| `test_actions_without_backend_ceiling_preserve_existing_grants_and_consumption` | portado (`pg_store`) |
| `test_without_any_approval_nothing_is_allowed` | portado (`pg_store`) |
| `test_approval_for_another_object_or_action_is_useless` | portado (`pg_store`) |
| `test_expired_approval_is_refused` | portado (`pg_store`, `Clock` UTC atual) |
| `test_amount_above_limit_or_other_currency_is_refused_and_not_consumed` | portado (`pg_store`) |
| `test_a_spending_action_with_an_unlimited_approval_is_refused` | portado (`pg_store`) |
| `test_replay_is_refused_and_a_new_approval_is_needed` | portado (`pg_store`, `Clock` UTC atual) |
| `test_concurrent_attempts_consume_the_approval_only_once` | portado (`pg_store` + `psycopg.OperationalError`) |
| `test_publication_barrier_requires_a_human_checkpoint_per_object` | portado (`pg_store`) |
| `test_database_rejects_delete_edit_and_a_second_consumption` | portado (`InsufficientPrivilege`/`CheckViolation`/`IntegrityError` PG) |
| `test_cannot_edit_an_unconsumed_checkpoint_to_widen_it` | portado (`InsufficientPrivilege` PG) |
| `test_refusals_are_audited_without_content_and_success_does_not_duplicate_the_ledger` | portado (`pg_store`) |
| `test_api_creates_lists_and_is_idempotent` | portado (`client_for` PG) |
| `test_api_cannot_expand_ttl_or_grant_cognitive_spending` | portado (`client_for` PG) |
| `test_api_rejects_invalid_or_non_human_approvals` | portado (`client_for` PG) |
| `test_api_requires_idempotency_key_and_authentication` | portado (`client_for` PG) |
| `test_authorizing_a_hermes_run_registers_and_consumes_a_bounded_checkpoint` | portado (`client_for` PG + `FakeHermes`) |
| `test_expired_hermes_checkpoint_blocks_the_run_before_any_submission` | portado (`client_for` PG + `FakeHermes`) |
| `test_price_increase_after_approval_exceeds_the_approved_limit` | portado (`client_for` PG + `FakeHermes`) |
| `test_attempt_without_a_checkpoint_is_refused_even_with_an_authorization_row` | portado (`client_for` PG + `FakeHermes`) |
| `test_hermes_script_stage_registers_a_consumed_checkpoint` | portado (`client_for` PG + `support_b`) |
| `test_paid_narration_needs_a_matching_human_checkpoint_each_time` | portado (`pg_store`) |
| `test_paid_narration_without_a_checkpoint_service_or_with_unknown_cost_never_reaches_the_provider` | portado (`pg_store`) |
| `test_paid_narration_disabled_provider_ignores_even_a_valid_checkpoint` | portado (`pg_store`) |

### `test_egress_gateway.py` (30 funções)

| Teste legado (`test_egress_gateway.py::...`) | Status |
|---|---|
| `test_default_policy_lists_only_the_known_product_destinations` .. `test_env_allowlist_is_the_only_way_to_open_a_new_destination` (22 testes) | portado (sem dependência SQLite) |
| `test_every_decision_is_audited_without_content` | portado (`pg_store`) |
| `test_audit_failure_never_changes_the_decision` | portado (`pg_store`) |
| `test_transmissions_remain_append_only` | portado (`pg_store` + `InsufficientPrivilege`/`RaiseException`) |
| `test_hermes_run_client_consults_the_gateway_before_any_socket` | portado |
| `test_youtube_download_job_requires_an_allowlisted_destination` | portado (`pg_store`) |
| `test_radar_transport_consults_the_gateway_without_leaking_the_key` | portado (`pg_store`) |
| `test_authorized_hermes_calls_do_not_duplicate_the_hermes_ledger_but_refusals_and_other_purposes_are_logged` | portado (`pg_store`) |
| `test_download_allowlist_matches_the_hosts_the_url_validator_accepts` | portado |

### `test_workflows.py` (21 legados → 20 funções PG)

| Teste legado (`test_workflows.py::...`) | Status |
|---|---|
| `test_complete_workflow_is_simulated_and_keeps_worker_free` | portado (`client_for` PG) |
| `test_create_and_review_are_idempotent_and_conflicts_are_rejected` | portado (`client_for` PG) |
| `test_changes_create_versioned_artifacts_and_reject_obsolete_approval` | portado (`client_for` PG) |
| `test_rejection_is_terminal_and_persists` | portado (`client_for` PG) |
| `test_awaiting_approval_and_journal_survive_restart` | portado (`client_for` PG) |
| `test_failed_attempt_is_preserved_and_retry_does_not_repeat_completed_step` | portado (`client_for` PG) |
| `test_cancellation_stops_simulated_step_and_prevents_dependents` | portado (`client_for` PG) |
| `test_abrupt_process_exit_recovers_without_duplicate_artifact` | portado (subprocesso conectado ao PG + expiração de lease pós-crash via `pg_admin`) |
| `test_dependency_and_artifact_integrity_are_checked_before_execution` | portado (`pg_store` + `pg_admin`) |
| `test_tampered_dependency_artifact_is_not_consumed` | portado (`pg_store` + `pg_admin` em `content` + `content_canonical`) |
| `test_step_result_and_next_release_commit_atomically` | portado (trigger PL/pgSQL via `pg_admin`) |
| `test_late_result_from_old_attempt_cannot_change_recovered_workflow` | portado (`pg_store` + `client_for` PG) |
| `test_technical_log_retention_does_not_erase_editorial_history` | portado (`client_for` PG + `generate_series`) |
| `test_cancelling_completed_step_does_not_cancel_workflow_waiting_for_review` | portado (`client_for` PG) |
| `test_invalid_executor_output_does_not_advance_or_approve` | portado (`client_for` PG) |
| `test_competing_reviews_have_one_winner` | portado (`client_for` PG) |
| `test_second_backend_cannot_recover_another_workers_running_step` | **revela defeito** (`xfail(strict=True)`: `database_router` fecha `db_manager` antes de `manager.close()`) |
| `test_contract_rejects_unapproved_operations_and_requires_authentication` | portado (`client_for` PG) |
| `test_migration_preserves_legacy_rows_and_creates_one_consistent_backup` | OBSOLETO (backup `.sqlite3`/`PRAGMA user_version` intrínseco de SQLite); regra de preservação/idempotência coberta em `test_pg_schema_migrations_preserve_rows_and_rollback_on_failure` |
| `test_migration_failure_rolls_back_schema_and_version` | equivalente PG em `test_workflows.py::test_pg_schema_migrations_preserve_rows_and_rollback_on_failure` |
| `test_future_database_is_refused_without_downgrade` | equivalente PG em `test_workflows.py::test_pg_schema_checksum_mismatch_is_refused` |

### `test_workflows_manual.py` (3 funções)

| Teste legado (`test_workflows_manual.py::...`) | Status |
|---|---|
| `test_human_workflow_scripts_versions_and_restart` | portado (`client_for` PG; verificação FK via `horus.scripts`) |
| `test_invalid` | portado (`client_for` PG) |
| `test_auth_key_and_concurrency` | portado (`client_for` PG) |

### `test_adversarial_battery.py` (18 funções)

| Teste legado (`test_adversarial_battery.py::...`) | Status |
|---|---|
| `test_injected_script_text_stays_data_and_triggers_no_spend_publication_or_egress` | portado (`client_for` PG + `query_rows`) |
| `test_feedback_with_paths_or_credentials_never_reaches_hermes` | portado (`client_for` PG) |
| `test_hermes_clients_never_follow_redirects_or_forward_the_token` .. `test_gateway_blocks_the_dns_rebinding_and_redirect_chain_end_to_end` (4 testes) | portado |
| `test_concurrent_authorizations_of_the_same_step_spend_once` | portado (`client_for` PG) |
| `test_a_consumed_hermes_checkpoint_cannot_be_replayed_by_a_retry` | portado (`client_for` PG) |
| `test_checkpoint_description_rejects_bidi_and_invisible_format_characters` | portado (`client_for` PG) |
| `test_a_forged_checkpoint_row_cannot_widen_itself_through_sql_or_api` | portado (`client_for` PG + `InsufficientPrivilege`/`CheckViolation`) |
| `test_checkpoint_cannot_be_granted_for_hermes_actions_through_the_api` | portado (`client_for` PG) |
| `test_no_token_or_credential_is_persisted_logged_or_exported_after_a_full_hermes_flow` | portado (`client_for` PG + varredura de todas as tabelas `horus.*`) |
| `test_errors_never_echo_paths_or_tokens` | portado (`client_for` PG) |
| `test_every_route_incl_checkpoints_and_transmissions_refuses_without_token_and_foreign_host_or_origin` | portado (`client_for` PG) |
| `test_preflight_from_foreign_origin_never_grants_access_to_the_new_routes` | portado (`client_for` PG) |
| `test_token_in_query_string_or_cookie_is_not_accepted` | portado (`client_for` PG) |
| `test_local_data_export_refuses_hostile_trees_and_verifies_hostile_zips` | portado (`client_for` PG + `store=pg_store`) |
| `test_hostile_file_names_are_refused_by_every_client_path_endpoint` | portado (`12` casos) + **revela defeito** em `[a\x00.mp4]` (`xfail(strict=True)`) |

### `test_transmission_audit.py` (13 funções)

| Teste legado (`test_transmission_audit.py::...`) | Status |
|---|---|
| `test_analysis_submit_and_outcome_are_recorded_with_hash_and_reconciled_usage` | portado (`client_for` PG) |
| `test_no_content_reaches_the_transmission_log_api_or_logs` | portado (`information_schema.columns` PG) |
| `test_log_is_append_only` | portado (`InsufficientPrivilege`/`RaiseException`/`CheckViolation` PG) |
| `test_pagination_is_bounded_and_authenticated` | portado (`client_for` PG) |
| `test_gate_refusals_are_recorded_for_authorize_and_script` | portado (`client_for` PG) |
| `test_budget_refusal_before_send_is_recorded_as_gate_refusal` | portado (`client_for` PG) |
| `test_script_stage_replay_does_not_duplicate_transmissions` | portado (`client_for` PG) |
| `test_unknown_outcome_and_reconciliation_append_new_events_without_rewriting` | portado (`client_for` PG) |
| `test_failed_run_is_recorded_as_failed_outcome` | portado (`client_for` PG) |
| `test_audit_write_failure_blocks_the_send` | portado (`DatabaseUnavailable` PG) |
| `test_simulated_executors_write_no_transmission_events` | portado (`client_for` PG) |
| `test_status_reports_audit_available_and_probe_is_logged` | portado (`client_for` PG) |
| `test_v11_upgrade_is_additive_and_idempotent` | OBSOLETO (upgrade SQLite v6/v9/v10→v11); equivalente PG em `test_transmission_audit.py::test_pg_schema_migrations_are_idempotent_and_preserve_data` |

### `test_hermes_credential.py` (14), `test_whisper_egress.py` (10), `test_secrets_at_rest_scan.py` (4), `test_worker_runtime.py` (8)

| Arquivo | Status |
|---|---|
| `test_hermes_credential.py` (14 testes) | todos portados (`support_b.settings`) |
| `test_whisper_egress.py` (10 testes) | todos portados (`pg_store` + `query_rows`) |
| `test_secrets_at_rest_scan.py` (4 testes) | todos portados (`client_for` PG, `pg_admin` em todas as tabelas `horus.*`, ausência de `*.sqlite*`/`*.db` no `data_dir`, `export_local_data(..., store=pg_store)`) |
| `test_worker_runtime.py` (8 testes) | todos portados (`support_b.settings`) |
