# HOR-164 / I4 — Workflows, Gates, Checkpoints Humanos, Transmissões e Orçamento em PostgreSQL

Data: 17/11/2026 · Branch: `yannbastos/sb-i4`

## 1. O que foi migrado

1. **`backend/src/horus_backend/transmissions.py`**
   - Removidos `store.connect()` e SQLite; `TransmissionLog.record()` e `page()` operam exclusivamente via `store.uow()` (`horus.transmissions`, `horus.authorizations`).
   - Envelope reduzido a `(envelope_bytes, envelope_sha256)`; `detail` sanitizado; `DatabaseUnavailable` (503) propagado em `record()`/`page()`, com `best_effort()` preservado para auditoria não bloqueante de recusas/desfechos.
   - Imutabilidade garantida pelos triggers `transmissions_no_update`, `transmissions_no_delete` e `transmissions_append_lock` do schema I2.

2. **`backend/src/horus_backend/security.py`** (somente orçamento/limites/persistência)
   - Adicionados `BUDGET_LOCK_NAMESPACE = 16412`, `BUDGET_LOCK_KEY = 1`, `acquire_budget_lock(conn)` (`pg_advisory_xact_lock(16412, 1)`) e `pg_clock(conn, clock=None)` (`SELECT clock_timestamp()` em produção, `clock` injetável apenas no harness).
   - `budget_usage`, `daily_tokens`, `daily_runs` e `require_budget` usam `store.uow(readonly=True)` / conexão da UoW, filtro de dia em UTC tipado `[day_start, day_end)` sem fatiamento de string, e preservam reservas abertas/`unknown` atravessando a meia-noite UTC.
   - API pública de `security_status` preservada.

3. **`backend/src/horus_backend/human_checkpoints.py`**
   - `CheckpointService` migrado 100% para `store.uow()`/`horus.human_checkpoints`, com `request_canonical` (`canonical_json_bytes`), `pg_advisory_xact_lock(16411, hashtext(action:subject))`, leitura de `pg_clock` **depois** da espera do lock e releitura antes do `UPDATE`.
   - `consume` implementa exatamente a emenda A1 (`ORDER BY seq DESC LIMIT 1 FOR UPDATE`, validação de `limit_amount`/`currency` na linha escolhida sem fallback para anterior mais permissivo, distinção `checkpoint_missing` / `checkpoint_consumed` / `checkpoint_expired` / `checkpoint_limit_exceeded`, proteção `consume_once` e `no_delete`).
   - `grant` mantém recusa acima do teto do backend (`checkpoint_limit_exceeds_backend_ceiling`).

4. **`backend/src/horus_backend/workflow_hermes.py`**
   - `HermesStages._record` e `analysis` operam via `store.uow()` sob `acquire_budget_lock(conn)`, `pg_clock(conn)` e `request_canonical`.
   - Intenção auditada em `horus.transmissions` (`strict=True`) antes de `client.submit`: falha de auditoria bloqueia o envio (`CognitiveFailure`) e grava `status='failed'` localmente.

5. **`backend/src/horus_backend/workflows.py`**
   - Todos os 10 `BEGIN IMMEDIATE` / `store.connect()` migrados para `store.uow()` com lock de linha na raiz (`SELECT ... FROM horus.workflows WHERE id = %s FOR UPDATE`) antes de `jobs`/`job_attempts`/`authorizations`/`checkpoints` (ordem global: orçamento → workflow/job → checkpoint).
   - `*_canonical` preenchido e comparado em bytes em `workflows`, `jobs`, `job_attempts`, `artifacts`, `approvals` e `authorizations`.

## 2. Mapeamento completo de testes legados → testes PG

| Teste legado | Novo teste PG (`backend/tests/pg/...`) | Estado |
|---|---|---|
| `test_human_checkpoints.py::test_grant_records_who_when_what_limit_and_expiry` | `test_checkpoints_pg.py::test_grant_records_who_when_what_limit_and_expiry` | Portado e verde |
| `test_human_checkpoints.py::test_invalid_grants_are_refused_and_store_nothing` | `test_checkpoints_pg.py::test_invalid_grants_are_refused_and_store_nothing` | Portado e verde |
| `test_human_checkpoints.py::test_grant_is_idempotent_and_key_cannot_be_reused_for_another_request` | `test_checkpoints_pg.py::test_grant_is_idempotent_and_key_cannot_be_reused_for_another_request` | Portado e verde |
| `test_human_checkpoints.py::test_backend_ceiling_refuses_larger_grants_before_insert` | `test_checkpoints_pg.py::test_backend_ceiling_refuses_larger_grants_before_insert` | Portado e verde |
| `test_human_checkpoints.py::test_backend_ceiling_accepts_lower_or_equal_grants` | `test_checkpoints_pg.py::test_backend_ceiling_accepts_lower_or_equal_grants` | Portado e verde |
| `test_human_checkpoints.py::test_backend_ceiling_cannot_be_bypassed_with_another_currency` | `test_checkpoints_pg.py::test_backend_ceiling_cannot_be_bypassed_with_another_currency` | Portado e verde |
| `test_human_checkpoints.py::test_monetary_hermes_grant_requires_backend_configuration` | `test_checkpoints_pg.py::test_monetary_hermes_grant_requires_backend_configuration` | Portado e verde |
| `test_human_checkpoints.py::test_actions_without_backend_ceiling_preserve_existing_grants_and_consumption` | `test_checkpoints_pg.py::test_actions_without_backend_ceiling_preserve_existing_grants_and_consumption` | Portado e verde |
| `test_human_checkpoints.py::test_without_any_approval_nothing_is_allowed` | `test_checkpoints_pg.py::test_without_any_approval_nothing_is_allowed` | Portado e verde |
| `test_human_checkpoints.py::test_approval_for_another_object_or_action_is_useless` | `test_checkpoints_pg.py::test_approval_for_another_object_or_action_is_useless` | Portado e verde |
| `test_human_checkpoints.py::test_expired_approval_is_refused` | `test_checkpoints_pg.py::test_expired_approval_and_boundary_are_refused` | Portado e verde |
| `test_human_checkpoints.py::test_amount_above_limit_or_other_currency_is_refused_and_not_consumed` | `test_checkpoints_pg.py::test_amount_above_limit_or_other_currency_is_refused_and_not_consumed` | Portado e verde |
| `test_human_checkpoints.py::test_a_spending_action_with_an_unlimited_approval_is_refused` | `test_checkpoints_pg.py::test_a_spending_action_with_an_unlimited_approval_is_refused` | Portado e verde |
| `test_human_checkpoints.py::test_replay_is_refused_and_a_new_approval_is_needed` | `test_checkpoints_pg.py::test_replay_is_refused_and_a_new_approval_is_needed` | Portado e verde |
| `test_human_checkpoints.py::test_concurrent_attempts_consume_the_approval_only_once` | `test_checkpoints_pg.py::test_concurrent_attempts_consume_the_approval_only_once` | Portado (`pg_strong`) e verde |
| `test_human_checkpoints.py::test_publication_barrier_requires_a_human_checkpoint_per_object` | `test_checkpoints_pg.py::test_publication_barrier_requires_a_human_checkpoint_per_object` | Portado e verde |
| `test_human_checkpoints.py::test_database_rejects_delete_edit_and_a_second_consumption` | `test_checkpoints_pg.py::test_database_rejects_delete_edit_and_a_second_consumption` | Portado e verde |
| `test_human_checkpoints.py::test_cannot_edit_an_unconsumed_checkpoint_to_widen_it` | `test_checkpoints_pg.py::test_database_rejects_delete_edit_and_a_second_consumption` | Coberto por `InsufficientPrivilege` |
| `test_human_checkpoints.py::test_refusals_are_audited_without_content_and_success_does_not_duplicate_the_ledger` | `test_checkpoints_pg.py::test_refusals_are_audited_without_content_and_success_does_not_duplicate_the_ledger` | Portado e verde |
| `test_human_checkpoints.py::test_api_*`, `test_authorizing_*`, `test_expired_hermes_*`, `test_price_increase_*`, `test_attempt_without_*`, `test_hermes_script_stage_*` | *(Pendente corte I-final — usa `create_app` / `api.py`)* | Pendente I-final |
| `test_human_checkpoints.py::test_paid_narration_*` (3 testes) | *(Fora do escopo I4 — módulo `narration/`, I7/I8)* | Pendente I7/I8 |
| `test_transmission_audit.py::test_analysis_submit_and_outcome_*`, `test_no_content_*`, `test_log_is_append_only`, `test_pagination_*` | `test_transmissions_pg.py::test_record_hashes_envelope_without_storing_content_and_paginates`, `test_transmissions_are_append_only_and_validate_constraints`, `test_strict_audit_failure_blocks_hermes_submission` | Portado em nível de serviço; rotas HTTP pendentes I-final |
| `test_transmission_audit.py::test_gate_refusals_are_recorded_for_authorize_and_script` | *(Pendente corte I-final — usa `create_app` / `scripts`)* | Pendente I-final |
| `test_security_budget.py::test_cost_is_recorded_with_frozen_prices_and_cache_conservatively` | `test_budget_pg.py::test_cost_is_recorded_with_frozen_prices_and_cache_conservatively` | Portado e verde |
| `test_security_budget.py::test_unknown_or_invalid_usage_retains_full_reservation` | `test_budget_pg.py::test_unknown_or_invalid_usage_retains_full_reservation` | Portado e verde |
| `test_security_budget.py::test_reservation_blocks_exceeding_budget_and_allows_equality` | `test_budget_pg.py::test_reservation_blocks_exceeding_budget_and_allows_equality` | Portado e verde |
| `test_security_budget.py::test_utc_rollover_resets_daily_spend_and_runs_but_keeps_total` | `test_budget_pg.py::test_utc_rollover_resets_daily_spend_and_runs_but_keeps_total` | Portado e verde |
| `test_security_budget.py::test_open_and_unknown_reservations_cross_utc_midnight` | `test_budget_pg.py::test_open_and_unknown_reservations_cross_utc_midnight` | Portado e verde |
| `test_security_budget.py::test_simulation_ignores_all_limits_prices_and_accounting` | `test_budget_pg.py::test_simulation_ignores_all_limits_prices_and_accounting` | Portado e verde |
| `test_security_budget.py::test_concurrent_admissions_reserve_under_same_sqlite_lock` | `test_budget_pg.py::test_concurrent_admissions_across_workflows_never_overspend` | Portado (`pg_strong`) e verde |
| `test_security_budget.py::test_status_*`, `test_approved_defaults_*`, `test_operator_override_*`, `test_invalid_budget_*`, `test_missing_pricing_*`, `test_concurrent_human_authorizations_and_status_endpoint` | *(Pendente corte I-final para endpoints HTTP)* | Pendente I-final |
| `test_workflows.py::test_complete_workflow_*`, `test_create_and_review_*`, `test_changes_create_*`, `test_rejection_*`, `test_tampered_*`, `test_late_result_*`, `test_competing_reviews_*` | `test_workflows_pg.py` (testes equivalentes diretos em `WorkflowService`) | Portado e verde; variantes HTTP/API pendentes I-final |
| `test_workflows_manual.py::*` | `test_workflows_pg.py::test_manual_workflow_creation_idempotency_and_conflict` | Portado em serviço; rota HTTP pendente I-final |
| `test_workflow_hermes.py::*` | `test_workflows_pg.py::test_hermes_authorization_checkpoint_and_reconcile_without_resend` | Portado em serviço; rotas HTTP pendentes I-final |

## 3. Verificações executadas

- `pytest backend/tests/pg/test_checkpoints_pg.py backend/tests/pg/test_transmissions_pg.py backend/tests/pg/test_budget_pg.py backend/tests/pg/test_workflows_pg.py -q`: **77 passed** em 27.18s (com `PYTHONPATH=.../backend/src` no worktree).
- `pytest backend/tests/test_supply_chain.py backend/tests/test_network_inventory.py -q`: **31 passed** em 2.41s.
- `pytest backend/tests/test_api_contract_snapshot.py backend/tests/test_endpoint_auth.py -q`: **12 passed, 1 skipped, 3 failed, 56 errors** (falhas esperadas no `create_app` em `api.py:124`, escopo I-final).
