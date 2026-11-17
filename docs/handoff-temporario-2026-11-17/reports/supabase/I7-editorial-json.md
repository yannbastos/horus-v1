# HOR-164 / I7 — Notificações, Comentários de Revisão, Centro de Erros, Publicação, Exports, Narração e Importação de Projeto em PostgreSQL

Data: 17/11/2026 · Branch: `yannbastos/sb-i7`

## 1. O que foi migrado

1. **`backend/src/horus_backend/notifications.py`**
   - Removidos `notifications-state.json`, escrita atômica em disco e `self.store.connect()`.
   - `NotificationService` opera via `self.store.uow(readonly=...)` (propagando `DatabaseUnavailable` 503 sem fallback).
   - Eventos derivados de `horus.productions`, `horus.jobs`, `horus.scripts` e `horus.workflows` em SQL parametrizado (`%s`, `dict_row`), e estado lido/não lido + watermark `notifications.read_all_before` persistidos em `horus.notification_state` e `horus.preferences` (O(1) consultas, sem N+1).

2. **`backend/src/horus_backend/error_center.py`**
   - Removidos `self.store.connect()`, `json_extract` e sentinelas `''` / `'9999'`.
   - `ErrorCenter` usa `self.store.uow(readonly=True)`, filtros tipados `%s::timestamptz IS NULL OR ...`, `j.payload->>'operation'` e `dict_row` (2 consultas fixas, sem N+1).

3. **`backend/src/horus_backend/project_import.py`**
   - Removido índice `import.json` em disco; arquivos de pacote (`video.mp4`, `subtitles.srt`, `script.json`, etc.) permanecem em `<data_dir>/imports/<id>/` apenas como artefatos verificados por SHA-256.
   - `ImportService` aceita `store=None` (compatível com o wiring atual de `api.py` até o corte I-final, levantando `DatabaseUnavailable` 503 quando `store` ausente/indisponível) e persiste o índice/manifesto em `horus.imports` (`approved=false`, `publishable=false`, append-only protegido por triggers `no_update`/`no_delete`), com `pg_advisory_xact_lock(16408, hashtext(export_id))` para concorrência sem manter transação aberta durante a cópia do ZIP.

4. **`backend/src/horus_backend/review_comments.py`**
   - Removidos `production-comments/<id>.json` e locks em memória de arquivo.
   - `ReviewCommentService` persiste comentários, trilha append-only e recibos idempotentes em `horus.review_comments`, `horus.review_comment_events` e `horus.review_comment_receipts` (com `response_canonical` UTF-8), serialização por produção via `pg_advisory_xact_lock(16409, hashtext(production_id))`, e `page()` com busca em lote `WHERE comment_id = ANY(%s)` (3 consultas fixas, zero N+1).

5. **`backend/src/horus_backend/narration/service.py`**
   - Removido `narration-preference.json` e `self.store.connect()`.
   - `get_preference()` e `save_preference()` persistem em `horus.preferences` (`key='narration.preference'`).
   - `estimate()` com `script_id` usa `self.store.uow(readonly=True)` e valida aprovação vigente em `horus.scripts` + `horus.script_receipts` (sem depender do `production.py` ainda em SQLite).

6. **`backend/src/horus_backend/publishing.py`**
   - Removidos `self.store.connect()`, `BEGIN IMMEDIATE` e placeholders `?`.
   - `PublishingService` usa `self.store.uow()`, `SELECT ... FOR UPDATE` em `horus.productions`, recibos/falhas de thumbnail em `horus.publishing_receipts` (`scope='thumbnail'`, com `response_canonical`) e consultas diretas a `horus.script_receipts`, `horus.license_reviews`, `horus.scripts` e `horus.artifacts`, mantendo hash de vídeo e execução de `ffmpeg`/`ffprobe` fora de transação.

7. **`backend/src/horus_backend/exports.py`**
   - Removidos `self.store.connect()`, `BEGIN` e importação de `migrations.py` (`sqlite3`).
   - `ExportService` usa `self.store.uow()`, recibos/falhas em `horus.publishing_receipts` (`scope='export'`), leitura de `horus.productions`, `horus.jobs`, `horus.script_receipts` e `horus.license_reviews` em transação curta, e hash/empacotamento ZIP fora da transação.

## 2. Dependências de I5 / I-final

- `production.py` e `license_reviews.py` (escopo I5) ainda estão em SQLite nesta base; por isso `publishing.py`, `exports.py` e `narration/service.py` consultam diretamente as tabelas `horus.productions`, `horus.scripts`, `horus.script_receipts` e `horus.license_reviews` via UoW PostgreSQL, e os testes PG usam adaptadores mínimos de `productions` (para `root`/`get`/`require_local_data`).
- `api.py` (escopo I-final) ainda instancia `Store(sqlite)` e `ImportService(settings.data_dir)` sem `store`; todos os testes legados que passam por `create_app` / `TestClient` ficam pendentes do corte I-final.

## 3. Tabela de mapeamento: testes legados -> testes PG

| Teste legado | Novo teste PG (`backend/tests/pg/...`) | Status |
|---|---|---|
| `test_notifications.py::test_empty_history_and_no_schema_change` | `test_editorial_notifications_errors_pg.py::test_notifications_empty_and_derived_events` | Migrado (direto) + HTTP pendente I-final |
| `test_notifications.py::test_workflow_script_and_production_events_follow_the_real_state` | `test_editorial_notifications_errors_pg.py::test_notifications_empty_and_derived_events` | Migrado (direto) + HTTP pendente I-final |
| `test_notifications.py::test_failed_blocked_and_interrupted_productions` | `test_editorial_notifications_errors_pg.py::test_notifications_production_statuses_and_unsafe_code_ignored` | Migrado |
| `test_notifications.py::test_failure_code_with_unsafe_characters_is_ignored` | `test_editorial_notifications_errors_pg.py::test_notifications_production_statuses_and_unsafe_code_ignored` | Migrado |
| `test_notifications.py::test_ordering_filters_pagination_and_cursor_validation` | `test_editorial_notifications_errors_pg.py::test_notifications_pagination_validation_and_query_count` | Migrado |
| `test_notifications.py::test_mark_read_unread_filter_count_and_persistence_across_restarts` | `test_editorial_notifications_errors_pg.py::test_notifications_read_unread_persistence_and_watermark` | Migrado |
| `test_notifications.py::test_read_all_then_new_events_are_unread_and_single_unread_overrides` | `test_editorial_notifications_errors_pg.py::test_notifications_read_unread_persistence_and_watermark` | Migrado |
| `test_notifications.py::test_read_state_validation` | `test_editorial_notifications_errors_pg.py::test_notifications_pagination_validation_and_query_count` | Migrado |
| `test_notifications.py::test_corrupt_state_never_breaks_the_api_and_is_recreated` | N/A (estado agora em PG tipado; falha de BD -> 503 `test_notifications_and_error_center_unavailable_raise_503`) | Substituído por 503 PG |
| `test_notifications.py::test_unreadable_state_path_warns_and_write_failure_is_a_clean_500` | `test_editorial_notifications_errors_pg.py::test_notifications_and_error_center_unavailable_raise_503` | Migrado para 503 PG |
| `test_notifications.py::test_writes_are_atomic_leave_no_temp_files_and_survive_concurrency` | `test_editorial_notifications_errors_pg.py::test_notifications_read_unread_persistence_and_watermark` | Migrado (transacional PG, sem arquivo) |
| `test_notifications.py::test_old_read_marks_are_pruned_and_state_is_bounded` | Coberto por SQL de poda em `notifications.py` | Pendente corte HTTP |
| `test_notifications.py::test_routes_require_the_token_and_refuse_foreign_origin_or_host` | — | Pendente I-final (`create_app`) |
| `test_notifications.py::test_no_path_script_content_or_token_leaks_in_any_response_or_state_file` | `test_editorial_notifications_errors_pg.py::test_notifications_empty_and_derived_events` | Migrado |
| `test_notifications.py::test_derivation_is_read_only` | `test_editorial_notifications_errors_pg.py::test_notifications_empty_and_derived_events` | Migrado (`uow(readonly=True)`) |
| `test_error_center.py::test_lists_failures_with_filters_counts_and_portuguese_text` | `test_editorial_notifications_errors_pg.py::test_error_center_filters_counts_pagination_and_no_leak` | Migrado |
| `test_error_center.py::test_production_and_workflow_items_link_to_their_origin` | `test_editorial_notifications_errors_pg.py::test_error_center_filters_counts_pagination_and_no_leak` | Migrado |
| `test_error_center.py::test_no_leak_of_paths_secrets_or_free_text` | `test_editorial_notifications_errors_pg.py::test_error_center_filters_counts_pagination_and_no_leak` | Migrado |
| `test_error_center.py::test_pagination_cursor_and_limits` | `test_editorial_notifications_errors_pg.py::test_error_center_filters_counts_pagination_and_no_leak` | Migrado |
| `test_error_center.py::test_invalid_parameters_are_422_without_echo` | `test_editorial_notifications_errors_pg.py::test_error_center_filters_counts_pagination_and_no_leak` | Migrado (validação direta; HTTP pendente I-final) |
| `test_error_center.py::test_requires_authentication` | — | Pendente I-final (`create_app`) |
| `test_error_center.py::test_read_only_and_no_schema_change` | `test_editorial_notifications_errors_pg.py::test_error_center_filters_counts_pagination_and_no_leak` | Migrado (`uow(readonly=True)`) |
| `test_review_comments.py::test_create_list_resolve_reopen_with_decision_trail` | `test_editorial_comments_import_narration_pg.py::test_review_comments_crud_trail_idempotency_immutability_and_no_n_plus_1` | Migrado |
| `test_review_comments.py::test_filters_ordering_and_pagination` | `test_editorial_comments_import_narration_pg.py::test_review_comments_crud_trail_idempotency_immutability_and_no_n_plus_1` | Migrado |
| `test_review_comments.py::test_idempotency_and_replay` | `test_editorial_comments_import_narration_pg.py::test_review_comments_crud_trail_idempotency_immutability_and_no_n_plus_1` | Migrado |
| `test_review_comments.py::test_invalid_bodies_are_422_without_echo` | — | Pendente I-final (`create_app`) |
| `test_review_comments.py::test_nan_and_control_characters_are_refused` | `test_editorial_comments_import_narration_pg.py::test_review_comments_crud_trail_idempotency_immutability_and_no_n_plus_1` | Migrado |
| `test_review_comments.py::test_foreign_hash_unknown_production_and_comment_and_missing_key` | `test_editorial_comments_import_narration_pg.py::test_review_comments_crud_trail_idempotency_immutability_and_no_n_plus_1` | Migrado |
| `test_review_comments.py::test_invalid_query_is_422` | — | Pendente I-final (`create_app`) |
| `test_review_comments.py::test_requires_authentication` | — | Pendente I-final (`create_app`) |
| `test_review_comments.py::test_limit_of_comments_per_production` | `test_editorial_comments_import_narration_pg.py::test_review_comments_limits_and_deleted_local_data` | Migrado |
| `test_review_comments.py::test_trail_limit` | `test_editorial_comments_import_narration_pg.py::test_review_comments_limits_and_deleted_local_data` | Migrado |
| `test_review_comments.py::test_corrupt_file_is_never_overwritten` | `test_editorial_comments_import_narration_pg.py::test_review_comments_crud_trail_idempotency_immutability_and_no_n_plus_1` | Migrado (triggers append-only PG) |
| `test_review_comments.py::test_storage_is_atomic_json_without_secrets_or_paths_and_no_schema_change` | `test_editorial_comments_import_narration_pg.py::test_review_comments_crud_trail_idempotency_immutability_and_no_n_plus_1` | Migrado |
| `test_review_comments.py::test_comments_do_not_change_the_existing_review_endpoint` | — | Pendente I-final (`create_app`) |
| `test_project_import.py::test_import_creates_a_verified_historical_copy_without_touching_the_database` | `test_editorial_comments_import_narration_pg.py::test_project_import_pg_immutability_tampering_and_hostile` | Migrado (`horus.imports`) |
| `test_project_import.py::test_imported_project_is_not_a_production_nor_exportable_nor_approvable` | `test_editorial_comments_import_narration_pg.py::test_project_import_pg_immutability_tampering_and_hostile` | Migrado (`approved=false, publishable=false` + trigger) |
| `test_project_import.py::test_replay_is_200_and_other_keys_or_copies_of_the_same_package_conflict` | `test_editorial_comments_import_narration_pg.py::test_project_import_pg_immutability_tampering_and_hostile` | Migrado |
| `test_project_import.py::test_concurrent_imports_of_the_same_package_create_one_copy` | `test_editorial_comments_import_narration_pg.py::test_project_import_concurrent_same_package_creates_one_copy` | Migrado (`pg_strong`) |
| `test_project_import.py::test_hostile_packages_are_refused_before_any_write` (e variantes ZIP/limite/tamper) | `test_editorial_comments_import_narration_pg.py::test_project_import_pg_immutability_tampering_and_hostile` | Migrado (lógica de ZIP preservada) |
| `test_project_import.py::test_routes_require_token_and_refuse_foreign_origin` / `test_no_local_path_or_token_leaks_in_responses` | — | Pendente I-final (`create_app`) |
| `test_narration.py::*` (preferência, estimativa com roteiro aprovado, recusa pago) | `test_editorial_comments_import_narration_pg.py::test_narration_preferences_estimate_and_paid_refusal` | Migrado (direto); rotas HTTP pendentes I-final |
| `test_publishing.py::*` e `test_publishing_adversarial.py::*` | `test_editorial_publishing_exports_pg.py::test_publishing_*` | Migrado (direto); rotas HTTP pendentes I-final |
| `test_exports.py::*`, `test_exports_adversarial.py::*`, `test_exports_low.py::*` | `test_editorial_publishing_exports_pg.py::test_exports_create_redaction_idempotency_and_failures` | Migrado (direto); rotas HTTP pendentes I-final |

## 4. Verificações executadas

- `git grep -n -E "sqlite3|PRAGMA|sqlite|executescript|BEGIN IMMEDIATE|\.connect\(" -- <7 arquivos I7>`: 0 ocorrências.
- `backend/tests/pg/test_editorial_notifications_errors_pg.py` + `test_editorial_comments_import_narration_pg.py` + `test_editorial_publishing_exports_pg.py`: **18 passed** (inclui `pg_strong`).
- `backend/tests/test_supply_chain.py` + `backend/tests/test_network_inventory.py`: **31 passed**.
- `backend/tests/test_api_contract_snapshot.py` + `backend/tests/test_endpoint_auth.py`: **3 failed, 12 passed, 1 skipped, 56 errors** no setup de `create_app` por `api.py:124` (`RadarState` ainda com `.sqlite3`, escopo I-final).
