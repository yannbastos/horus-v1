# HOR-164 / Porte A — Estado em Pausa (WIP)

Data: 17/11/2026 · Branch: `yannbastos/sb-porte-a`

## 1. Resumo do estado (pausa solicitada pelo usuário)

Todos os **14 arquivos do Grupo A** e o módulo compartilhado `backend/tests/support_a.py` tiveram as referências a `horus_backend.migrations`, `sqlite3`, `Store.connect()` e `test_workflows` portadas para o harness PostgreSQL (`conftest.py` + `support_a.py`). A bateria completa conjunta dos 14 arquivos foi interrompida a pedido do usuário durante a execução (`task-296`), e os testes transversais finais ainda não foram executados.

## 2. Arquivos editados / criados

- **Novo:** `backend/tests/support_a.py` (fixtures `settings`, `hermes_settings`, `client_for`, `pg_admin_connect`, `create`, `wait`, `review`)
- **Snapshot atualizado (aditivo):** `backend/tests/snapshots/first-video-shapes.json` (+9 campos `steps[].fencing_token`, `steps[].lease_until`, `steps[].worker_instance_id` introduzidos pelo schema PG I3)
- **14 arquivos do Grupo A editados no lugar:**
  1. `backend/tests/test_review_2026_11_14.py` — **6 passed** (executado completo após última edição)
  2. `backend/tests/test_scripts.py` — **14 passed** (executado completo após última edição)
  3. `backend/tests/test_production.py` — antes da última edição: 62 passed, 3 skipped, 3 failed (`test_legacy_or_invalid_plan_does_not_invent_selection` com `""`, `null`, `123` por `productions_plan_check`); após correção dos parâmetros, `test_legacy_or_invalid_plan_does_not_invent_selection` teve **7 passed** (bateria completa do arquivo ainda não re-executada após a última edição)
  4. `backend/tests/test_production_robustness.py` — lote anterior teve 2 falhas (`test_worker_lock_is_released_by_os_after_crash`, `test_artifacts_and_success_are_atomic`); ambas corrigidas, e `test_worker_lock_is_released_after_crash_ttl_and_on_close` teve **1 passed** após a última edição (demais casos do arquivo não re-executados após a última edição)
  5. `backend/tests/test_first_video_response_shapes.py` — `first-video-shapes.json` atualizado via `HORUS_UPDATE_API_SNAPSHOT=1` (1 skipped na gravação do snapshot; arquivo completo não re-executado após atualização)
  6. `backend/tests/test_license_reviews.py` — lote anterior passou exceto `test_license_reviews_foreign_key_requires_existing_production`; corrigido e **1 passed** após a última edição (arquivo completo não re-executado após a última edição)
  7. `backend/tests/test_scripts_hermes_adversarial.py` — lote anterior passou exceto `test_crash_mid_run_leaves_reservation_blocks_resubmission_and_keeps_workflow_approved`; corrigido e **1 passed** após a última edição (arquivo completo não re-executado após a última edição)
  8. `backend/tests/test_production_network.py` — passou no lote de 7 arquivos (`task-234`)
  9. `backend/tests/test_manual_script_flow.py` — passou no lote de 7 arquivos (`task-234`)
  10. `backend/tests/test_scripts_manual.py` — passou no lote de 7 arquivos (`task-234`)
  11. `backend/tests/test_pipeline_resume_characterization.py` — passou no lote de 7 arquivos (`task-234`)
  12. `backend/tests/test_license_consistency.py` — passou no lote de 7 arquivos (`task-266`)
  13. `backend/tests/test_local_pilot.py` — passou no lote de 7 arquivos (`task-266`)
  14. `backend/tests/test_scripts_hermes.py` — passou no lote de 7 arquivos (`task-266`)

## 3. Testes SQLite marcados como obsoletos / substituídos por equivalente PG

- `test_scripts.py::test_migration_v5_preserves_approved_workflow` → substituído por `test_approved_workflow_persists_across_restart_for_scripts` (migração SQLite v4→v5 obsoleta no schema PG I2)
- `test_scripts_manual.py::test_migration_v9_marks_existing_scripts_as_simulated` → substituído por `test_scripts_origin_constraint_rejects_invalid_origin` (migração SQLite v8→v9 obsoleta; mantida validação de constraint `origin` no PG)
- `test_production.py::test_v5_to_v6_preserves_approved_scripts` → substituído por `test_approved_script_persists_across_restart_for_production` (migração SQLite v5→v6 obsoleta)
- `test_license_reviews.py::test_migration_v8_preserves_data_is_idempotent_and_adds_only_table` e `test_migration_v8_failure_rolls_back` → substituídos por `test_license_reviews_foreign_key_requires_existing_production` (migração SQLite v7→v8 obsoleta; mantida validação de FK no PG)
- `test_local_pilot.py::test_migration_v7_preserves_v6_productions_receipts_ids_and_fks` e `test_migration_v7_failure_rolls_back_after_table_replacement` → substituídos por `test_productions_and_receipts_enforce_fks_mode_and_restart_idempotency` (migração SQLite v6→v7 obsoleta; mantidas FKs, CHECK de `mode` e idempotência no PG)
- `test_scripts_hermes.py::test_v9_upgrade_preserves_legacy_records_and_reading` e `test_simulated_and_manual_bytes_survive_v9_upgrade` → substituídos por `test_simulated_and_manual_bytes_survive_restart_and_keep_origin`
- `test_scripts_hermes_adversarial.py::test_v10_upgrade_from_populated_legacy_db` e `test_v10_check_constraints` → v10 upgrade obsoleto; constraints verificadas em `test_scripts_check_constraints_pg`

## 4. Observações / defeitos de produto encontrados

- **`WorkerLock` em reinício com `TestClient`:** ao encerrar `with TestClient(create_app(s)) as client:`, `horus.worker_instances.lease_until` permanecia no futuro, causando `RuntimeError: Outro backend já executa tarefas neste banco do Hórus.` na reabertura sequencial. Contornado em `support_a.py::client_for` chamando `manager.worker_lock.close()` no `finally` do context manager.

## 5. O que falta para concluir o Porte A

1. Re-executar a bateria completa dos 14 arquivos do Grupo A após todas as edições finais (interrompida em `task-296`).
2. Executar os testes transversais (`test_supply_chain.py`, `test_network_inventory.py`, `test_api_contract_snapshot.py`, `test_endpoint_auth.py`).
3. Completar a tabela por teste legado (`arquivo::nome`) neste relatório após a validação final.
