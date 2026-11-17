# HOR-164 / I5 — Roteiros, Produção, Revisões de Licença e Retenção de Roteiros em PostgreSQL

Data: 17/11/2026 · Branch: `yannbastos/sb-i5`

## 1. O que foi migrado

1. **`backend/src/horus_backend/scripts.py`**
   - Removidos `self.store.connect()`, `BEGIN IMMEDIATE` e placeholders `?`.
   - `ScriptService` opera via `self.store.uow(readonly=...)` (`DatabaseUnavailable` 503 quando indisponível, sem fallback SQLite).
   - `create`, `new_version` e `review` bloqueiam a raiz `horus.workflows` com `FOR UPDATE` antes de ler/gravar `horus.scripts` e `horus.script_receipts`, preservando a ordem global de locks (`workflows -> scripts -> script_receipts`).
   - `document_canonical`, `review_canonical`, `request_canonical` e `response_canonical` gravados em UTF-8 determinístico (`encode(...).encode("utf-8")`) e comparados por bytes em `_replay` e `create(_document=...)`.
   - `create(_prepare=True)` grava `horus.jobs` (`payload_canonical`), `horus.job_attempts` (`input_artifacts_canonical`), `horus.authorizations` (`request_canonical`) e chama `CheckpointService.grant/consume(..., db=conn)` na mesma transação.

2. **`backend/src/horus_backend/production.py`**
   - Removidos `self.store.connect()`, `BEGIN IMMEDIATE`, placeholders `?` e leitura do arquivo `narration-preference.json` em disco.
   - `ProductionService` opera via `self.store.uow(readonly=...)` (`DatabaseUnavailable` 503 sem fallback).
   - `create` lê `horus.preferences` (`device_id='default', key='narration.preference'`) para `local_pilot` quando `narration` não é informado, serializa por `pg_advisory_xact_lock(16420, hashtext(script_id))` e grava `script_review_canonical`, `plan_canonical` e `request_canonical`.
   - `list` carrega `horus.jobs` e `horus.license_reviews` em lote (`ANY(%s)`) para eliminar N+1.
   - `review` bloqueia `horus.productions` com `FOR UPDATE`, compara `request_canonical` em bytes e grava `review_canonical`.
   - `run` verifica `bytes(script["review_canonical"]) == bytes(row["script_review_canonical"])`, valida e grava `plan.json` a partir de `bytes(row["plan_canonical"]).decode("utf-8")` (emenda A4), faz `claim_job` com `worker_lock` quando ativo, propaga `worker_instance_id`/`fencing_token` em `store.update`, trata `LeaseLostError` sem sobrescrever estado e verifica `_verify_worker_claim` antes de concluir `productions.artifacts` + `jobs.status='succeeded'`.

3. **`backend/src/horus_backend/license_reviews.py`**
   - Removidos `self.store.connect()`, `BEGIN IMMEDIATE` e placeholders `?`.
   - `LicenseReviewService` opera via `self.store.uow(readonly=...)` (`DatabaseUnavailable` 503), bloqueando `horus.productions` com `FOR UPDATE` em `create` para serializar a cadeia `supersedes` e comparando `request_canonical` em bytes.
   - Imutabilidade garantida pelos triggers `license_reviews_no_update` / `license_reviews_no_delete` e grants de `horus_app`.

4. **`backend/src/horus_backend/retention.py`**
   - Implementado `_script_literal_matches` real lendo `horus.scripts` e `horus.script_receipts` (`generate:<wid>`, `review:<sid>`, `version:<sid>`) sem alterar conteúdo editorial nem hashes.
   - Removido `@pytest.mark.skip` em `backend/tests/pg/test_retention_pg.py`, substituído por `test_script_retention_preserves_editorial_copies_and_counts_literals`.

5. **Workers e adaptadores I7**
   - `publishing.py`, `exports.py` e `narration/service.py` (já em PG via I7) não precisaram de alterações: seus contratos de consulta direta sobre `horus.productions`, `horus.scripts`, `horus.script_receipts` e `horus.license_reviews` são compatíveis.
   - `production_worker.py` e `narration/*_worker.py` verificados sem importação de `sqlite3`.

## 2. Tabela de mapeamento: testes legados -> novos testes PG

| Teste legado | Novo teste PG (`backend/tests/pg/...`) | Status |
|---|---|---|
| `test_scripts.py::test_script_create_list_diff_review_and_idempotency` (serviço) | `test_scripts_pg.py::test_script_create_list_diff_review_and_idempotency` | Portado e verde |
| `test_scripts.py::*` (rotas HTTP via `create_app`) | — | Pendente I-final (`create_app`) |
| `test_scripts_manual.py::test_first_manual_script_then_human_versions_history_and_restart`, `test_simulated_origin_is_preserved_*`, `test_version_conflicts_and_final_states` | `test_scripts_pg.py::test_manual_script_and_new_version_preserve_origin_and_canonical` | Portado (serviço); HTTP pendente I-final |
| `test_scripts_manual.py::test_competing_versions_have_one_winner` | `test_scripts_pg.py::test_competing_script_versions_have_one_winner` | Portado (`pg_strong`) e verde |
| `test_scripts_manual.py::test_manual_requires_approved_current_proposal`, `test_invalid_*`, `test_human_script_approval_*`, `test_simulated_flag_*` | `test_scripts_pg.py::test_script_create_list_diff_review_and_idempotency`, `test_manual_script_and_new_version_preserve_origin_and_canonical` | Coberto em serviço; HTTP pendente I-final |
| `test_scripts_manual.py::test_migration_v9_marks_existing_scripts_as_simulated` | N/A (migração SQLite v9 substituída por schema PG I2) | Legado SQLite |
| `test_scripts_hermes.py::test_script_authorization_reservation_validation_and_replay` | `test_scripts_pg.py::test_hermes_script_create_and_reconcile` | Portado (serviço); HTTP pendente I-final |
| `test_scripts_hermes.py::test_unknown_outcome_blocks_new_authorization`, `test_scripts_hermes_adversarial.py::test_reconcile_*` | `test_scripts_pg.py::test_hermes_script_unknown_then_reconcile_without_resubmit` | Portado (serviço); HTTP pendente I-final |
| `test_scripts_hermes_adversarial.py::*` (rotas HTTP / orçamento integrado) | — | Orçamento em `test_budget_pg.py` (I4); HTTP pendente I-final |
| `test_script_retention.py::test_editorial_copies_preserved_and_raw_sources_redacted` | `test_retention_pg.py::test_script_retention_preserves_editorial_copies_and_counts_literals` | Portado e verde |
| `test_script_retention.py::test_redacted_radar_blocks_new_changes_atomically_but_allows_final_review`, `test_changes_replay_*` | `test_scripts_pg.py::test_radar_redaction_blocks_simulated_script_and_request_changes` | Portado (serviço); HTTP pendente I-final |
| `test_production.py::test_simulated_artifacts_idempotency_and_restart`, `test_production_contract.py::*` (persistência) | `test_production_pg.py::test_production_simulated_run_artifacts_idempotency_and_list` | Portado (subprocesso real simulado) e verde |
| `test_production.py::test_approval_snapshot_hash_is_required`, `test_queued_snapshot_is_rechecked_before_execution` | `test_production_pg.py::test_production_stale_approval_and_canonical_integrity`, `test_production_plan_canonical_divergence_blocks_execution` | Portado (inclui A4 canonical) e verde |
| `test_production.py::test_production_narration_selection_and_receipt`, `test_legacy_pilot_receipt_*` | `test_production_pg.py::test_production_narration_preference_from_pg` | Portado (`horus.preferences`) e verde |
| `test_production.py::test_review_hash_and_idempotency_on_registered_real_fixture`, `test_pilot_review_*`, `test_artifact_tampering_and_confinement` | `test_production_pg.py::test_production_review_hash_notes_and_artifact_confinement` | Portado (serviço); HTTP pendente I-final |
| `test_production.py::test_concurrent_submission_is_one_job`, `test_production_robustness.py::*` (lease/fencing/crash) | `test_production_pg.py::test_production_live_lease_blocks_other_instance_recovery` | Portado (`pg_strong`) e verde |
| `test_production.py::test_v5_to_v6_preserves_approved_scripts` | N/A (migração SQLite v5->v6 substituída por schema PG I2) | Legado SQLite |
| `test_license_reviews.py::test_empty_listing_then_create_and_effective_view`, `test_idempotency_replay_and_conflict`, `test_new_decision_supersedes_and_history_is_preserved` | `test_license_reviews_pg.py::test_license_review_list_create_supersedes_and_idempotency`, `test_concurrent_license_reviews_serialize_supersedes_chain` | Portado (inclui `pg_strong`) e verde |
| `test_license_reviews.py::test_conflicts_and_negative_cases`, `test_missing_or_tampered_provenance_is_conflict` | `test_license_reviews_pg.py::test_license_review_negative_cases` | Portado e verde |
| `test_license_reviews.py::test_database_rejects_update_and_delete` | `test_license_reviews_pg.py::test_license_reviews_are_append_only_in_postgres` | Portado (triggers/grants PG) e verde |
| `test_license_reviews.py::test_requires_auth`, `test_invalid_payloads_are_422_without_echo`, `test_approval_does_not_publish_*`, `test_production_qc_*` | — | Pendente I-final (`create_app`) |
| `test_license_reviews.py::test_migration_v8_*` | N/A (migração SQLite v8 substituída por schema PG I2) | Legado SQLite |
| Indisponibilidade de banco (503 sem fallback SQLite) | `test_scripts_pg.py::test_scripts_unavailable_raises_503`, `test_production_pg.py::test_production_unavailable_raises_503`, `test_license_reviews_pg.py::test_license_reviews_unavailable_raises_503` | Novo e verde |

## 3. Verificações executadas

- `git grep -n -E "sqlite3|PRAGMA|sqlite|executescript|BEGIN IMMEDIATE|\.connect\(" -- backend/src/horus_backend/scripts.py backend/src/horus_backend/production.py backend/src/horus_backend/license_reviews.py backend/src/horus_backend/retention.py`: 0 ocorrências.
- `pytest backend/tests/pg/test_scripts_pg.py backend/tests/pg/test_production_pg.py backend/tests/pg/test_license_reviews_pg.py backend/tests/pg/test_retention_pg.py backend/tests/pg/test_editorial_publishing_exports_pg.py -q`: **33 passed** em 20.58s (inclui `pg_strong`).
- `pytest backend/tests/test_supply_chain.py backend/tests/test_network_inventory.py -q`: **31 passed** em 2.43s.
- `pytest backend/tests/test_api_contract_snapshot.py backend/tests/test_endpoint_auth.py -q`: **3 failed, 12 passed, 1 skipped, 56 errors** no setup de `create_app` por `api.py:124` (`RadarState` ainda com `.sqlite3`, escopo I-final).
