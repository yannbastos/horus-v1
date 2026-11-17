# HOR-164 / Porte C — Testes legados portados para PostgreSQL

Data: 17/11/2026 · Branch: `yannbastos/sb-porte-c`

## 1. Resumo e contagens (antes × depois)

- **Antes do porte:** 10 arquivos / 136 funções de teste falhavam na coleta (`ModuleNotFoundError: horus_backend.migrations` via `test_workflows.py` / `test_exports.py`).
- **Depois do porte:** 10 arquivos coletam e executam contra o harness PostgreSQL descartável (`pg_app_cluster` + `pg_app_database` + `backend/tests/support_c.py`).
- **Mapeamento das 136 funções de teste:** 129 `portado`, 4 `equivalente PG`, 3 `revela defeito` (0 `OBSOLETO`, 0 `skip`/`xfail` novos).
- **Infraestrutura compartilhada criada:** `backend/tests/support_c.py` (fixtures `settings`, `client_for`, `pg_connect`, `schema_state` e helpers de workflow/roteiro/produção/licença sem depender de `test_workflows.py` ou `migrations.py`).

## 2. Defeitos de produto revelados (sem editar `backend/src/`) 

1. **`backend/src/horus_backend/exports.py:229` (`_INPUTS`):** filtra `artifacts` com `re.compile(r"^inputs/[A-Za-z0-9_.-]+$")`, descartando artefatos `scene`/`audio` de produções simuladas/locais fora de `inputs/`. Isso faz `validate_asset_provenance(prov_data, artifacts=inputs)` falhar e gravar `original_provenance: null` e `effective_provenance: null` em `licenses.json` (revelado por `test_exports.py::test_export_approved_package_hashes_and_effective_licenses` e `test_exports.py::test_font_source_path_in_provenance_does_not_block_export`).
2. **`backend/src/horus_backend/publishing.py` (replay de miniatura com vídeo ausente):** após remover `video.mp4` do disco, o replay idempotente de `POST /v1/productions/{id}/thumbnail` com a mesma `Idempotency-Key` retorna `200` em vez de `409` (revelado por `test_publishing_adversarial.py::test_publishing_routes_follow_the_current_approval_state[video_missing]`).

## 3. Tabela completa: teste legado -> status PG

| Teste legado (`arquivo::nome`) | Status / Destino PG |
|---|---|
| `test_publishing_adversarial.py::test_invalid_timecodes_are_rejected_before_any_tool_runs` | portado |
| `test_publishing_adversarial.py::test_timecode_at_or_beyond_duration_is_refused_without_extracting` | portado |
| `test_publishing_adversarial.py::test_unusable_ffprobe_output_is_a_clean_409` | portado |
| `test_publishing_adversarial.py::test_ffmpeg_arguments_contain_only_flags_absolute_paths_and_a_numeric_timecode` | portado |
| `test_publishing_adversarial.py::test_ffmpeg_runs_without_shell_stdin_secrets_or_unbounded_time` | portado |
| `test_publishing_adversarial.py::test_missing_or_wrong_tools_are_a_clean_409` | portado |
| `test_publishing_adversarial.py::test_video_swapped_during_extraction_is_not_registered_or_exported` | portado |
| `test_publishing_adversarial.py::test_bad_tool_output_is_never_registered` | portado |
| `test_publishing_adversarial.py::test_huge_tool_output_is_refused_not_registered_or_loaded` | portado |
| `test_publishing_adversarial.py::test_registered_thumbnail_tampered_later_blocks_replay_and_export` | portado |
| `test_publishing_adversarial.py::test_receipt_tampered_blocks_reuse_and_never_creates_a_second_registration` | portado |
| `test_publishing_adversarial.py::test_same_key_concurrently_extracts_once_and_different_keys_extract_each` | portado |
| `test_publishing_adversarial.py::test_title_with_control_or_bidi_characters_is_refused` | portado |
| `test_publishing_adversarial.py::test_description_with_control_or_bidi_characters_is_refused` | portado |
| `test_publishing_adversarial.py::test_description_keeps_normal_line_breaks_unicode_and_markup_as_literal_text` | portado |
| `test_publishing_adversarial.py::test_invalid_metadata_payloads_are_refused_cleanly` | portado |
| `test_publishing_adversarial.py::test_mandatory_disclosures_cannot_be_removed_or_faked_by_editing` | portado |
| `test_publishing_adversarial.py::test_metadata_edits_are_idempotent_and_bounded_in_registered_files` | portado |
| `test_publishing_adversarial.py::test_concurrent_metadata_edits_never_fail_or_mix_fields` | portado |
| `test_publishing_adversarial.py::test_registered_metadata_tampered_is_refused_everywhere` | portado |
| `test_publishing_adversarial.py::test_publishing_routes_follow_the_current_approval_state` | revela defeito (caso `video_missing`: replay de miniatura em `publishing.py` retorna `200` em vez de `409` após remoção de `video.mp4`) |
| `test_publishing_adversarial.py::test_unreviewed_and_rejected_productions_cannot_publish` | portado |
| `test_publishing_adversarial.py::test_export_with_optional_files_keeps_review_hardening_and_redacts_metadata` | portado |
| `test_project_import.py::test_import_creates_a_verified_historical_copy_without_touching_the_database` | portado |
| `test_project_import.py::test_imported_project_is_not_a_production_nor_exportable_nor_approvable` | portado |
| `test_project_import.py::test_replay_is_200_and_other_keys_or_copies_of_the_same_package_conflict` | portado |
| `test_project_import.py::test_two_different_packages_get_distinct_new_ids_and_nothing_is_overwritten` | portado |
| `test_project_import.py::test_concurrent_imports_of_the_same_package_create_one_copy` | portado |
| `test_project_import.py::test_hostile_packages_are_refused_before_any_write` | portado |
| `test_project_import.py::test_not_a_zip_truncated_and_empty_files` | portado |
| `test_project_import.py::test_limits_on_members_and_total_size_are_enforced` | portado |
| `test_project_import.py::test_a_member_with_more_bytes_than_declared_stops_reading` | portado |
| `test_project_import.py::test_hostile_package_paths_are_refused` | portado |
| `test_project_import.py::test_missing_wrong_extension_directory_and_extra_fields` | portado |
| `test_project_import.py::test_a_junction_or_symlink_package_is_resolved_not_trusted` | portado |
| `test_project_import.py::test_failure_while_copying_leaves_no_partial_import` | portado |
| `test_project_import.py::test_package_changed_between_verification_and_copy_is_refused` | portado |
| `test_project_import.py::test_tampering_after_import_is_detected_on_read` | portado |
| `test_project_import.py::test_corrupt_import_record_never_breaks_the_listing` | portado |
| `test_project_import.py::test_import_ids_are_never_paths` | portado |
| `test_project_import.py::test_routes_require_token_and_refuse_foreign_origin` | portado |
| `test_project_import.py::test_no_local_path_or_token_leaks_in_responses` | portado |
| `test_project_import.py::test_verify_package_reports_stable_codes` | portado |
| `test_project_import.py::test_encrypted_members_are_refused` | portado |
| `test_exports_adversarial.py::test_common_ptbr_text_is_not_refused` | portado |
| `test_exports_adversarial.py::test_real_absolute_paths_are_still_refused` | portado |
| `test_exports_adversarial.py::test_credentials_in_text_are_refused` | portado |
| `test_exports_adversarial.py::test_credential_words_without_values_are_not_refused` | portado |
| `test_exports_adversarial.py::test_audit_metadata_redaction_catches_secrets_and_real_paths` | portado |
| `test_exports_adversarial.py::test_audit_metadata_redaction_keeps_legitimate_text` | portado |
| `test_exports_adversarial.py::test_zip_entries_are_fixed_relative_names_only` | portado |
| `test_exports_adversarial.py::test_artifact_tampered_after_verification_is_caught_while_copying` | portado |
| `test_exports_adversarial.py::test_subtitles_tampered_after_verification_are_caught` | portado |
| `test_exports_adversarial.py::test_real_junction_in_export_folder_is_refused` | portado |
| `test_exports_adversarial.py::test_real_junction_in_production_artifacts_is_refused` | portado |
| `test_exports_adversarial.py::test_write_error_midway_leaves_no_package_listed_or_downloadable` | portado |
| `test_exports_adversarial.py::test_video_write_error_leaves_no_partial_file` | portado |
| `test_exports_adversarial.py::test_concurrent_exports_with_different_keys_create_distinct_complete_packages` | portado |
| `test_exports_adversarial.py::test_large_video_is_streamed_not_loaded_in_memory` | portado |
| `test_exports_adversarial.py::test_download_is_authenticated_validates_ids_and_uses_safe_disposition` | portado |
| `test_exports_adversarial.py::test_one_corrupt_package_does_not_hide_the_good_ones` | portado |
| `test_notifications.py::test_empty_history_and_no_schema_change` | portado |
| `test_notifications.py::test_workflow_script_and_production_events_follow_the_real_state` | portado |
| `test_notifications.py::test_failed_blocked_and_interrupted_productions` | portado |
| `test_notifications.py::test_failure_code_with_unsafe_characters_is_ignored` | portado |
| `test_notifications.py::test_ordering_filters_pagination_and_cursor_validation` | portado |
| `test_notifications.py::test_mark_read_unread_filter_count_and_persistence_across_restarts` | portado |
| `test_notifications.py::test_read_all_then_new_events_are_unread_and_single_unread_overrides` | portado |
| `test_notifications.py::test_read_state_validation` | portado |
| `test_notifications.py::test_corrupt_state_never_breaks_the_api_and_is_recreated` | equivalente PG em `test_notifications.py::test_corrupt_state_never_breaks_the_api_and_is_recreated` (estado tipado em `horus.notification_state`, sem JSON em disco) |
| `test_notifications.py::test_unreadable_state_path_warns_and_write_failure_is_a_clean_500` | equivalente PG em `test_notifications.py::test_unreadable_state_path_warns_and_write_failure_is_a_clean_500` (indisponibilidade PG retorna 503 `database_unavailable` sem vazamento) |
| `test_notifications.py::test_writes_are_atomic_leave_no_temp_files_and_survive_concurrency` | portado |
| `test_notifications.py::test_old_read_marks_are_pruned_and_state_is_bounded` | portado |
| `test_notifications.py::test_routes_require_the_token_and_refuse_foreign_origin_or_host` | portado |
| `test_notifications.py::test_no_path_script_content_or_token_leaks_in_any_response_or_state_file` | portado |
| `test_notifications.py::test_derivation_is_read_only` | portado |
| `test_exports.py::test_export_approved_package_hashes_and_effective_licenses` | revela defeito (`exports.py:229`: `_INPUTS` filtra apenas `^inputs/...`, descartando `scene`/`audio` fora de `inputs/` e zerando `effective_provenance`) |
| `test_exports.py::test_export_auth_and_approval_required` | portado |
| `test_exports.py::test_export_refuses_tampering_and_unsafe_paths` | portado |
| `test_exports.py::test_export_refuses_production_without_registered_video` | portado |
| `test_exports.py::test_export_survives_restart_and_key_scopes` | portado |
| `test_exports.py::test_export_includes_registered_quality_report` | portado |
| `test_exports.py::test_concurrent_idempotent_exports_create_only_one_package` | portado |
| `test_exports.py::test_corrupt_package_is_not_replayed_or_downloaded` | portado |
| `test_exports.py::test_interrupted_export_does_not_overwrite_or_block_listing` | portado |
| `test_exports.py::test_sensitive_audit_notes_are_redacted_and_disclosed` | portado |
| `test_exports.py::test_font_source_path_in_provenance_does_not_block_export` | revela defeito (`exports.py:229`: `_INPUTS` filtra apenas `^inputs/...`, zerando `original_provenance` em `licenses.json`) |
| `test_exports.py::test_sensitive_subtitles_are_refused_without_silent_change` | portado |
| `test_publishing.py::test_thumbnail_is_approved_local_idempotent_and_registered` | portado |
| `test_publishing.py::test_thumbnail_timecode_validation` | portado |
| `test_publishing.py::test_thumbnail_and_metadata_require_approval_and_authentication` | portado |
| `test_publishing.py::test_thumbnail_missing_ffmpeg_is_actionable` | portado |
| `test_publishing.py::test_thumbnail_missing_ffprobe_is_actionable` | portado |
| `test_publishing.py::test_thumbnail_tampering_is_refused` | portado |
| `test_publishing.py::test_metadata_is_deterministic_and_disclosures_cannot_be_removed` | portado |
| `test_publishing.py::test_metadata_limits_include_disclosures` | portado |
| `test_publishing.py::test_large_script_still_produces_bounded_draft` | portado |
| `test_publishing.py::test_export_optional_thumbnail_and_metadata_hashes` | portado |
| `test_publishing.py::test_process_timeout_has_no_private_stderr_or_environment` | portado |
| `test_publishing.py::test_real_ffmpeg_extracts_png_and_jpg_1080x1920` | portado |
| `test_exports_low.py::test_export_failed_during_copy_is_recorded_for_that_key_and_new_key_works` | portado |
| `test_exports_low.py::test_export_write_error_is_recorded_without_leaking_paths_and_new_key_works` | portado |
| `test_exports_low.py::test_failure_marker_is_ignored_by_listing_and_download` | portado |
| `test_exports_low.py::test_thumbnail_failure_leaves_only_the_marker_and_new_key_works` | portado |
| `test_exports_low.py::test_thumbnail_bad_output_marks_the_key_failed` | portado |
| `test_exports_low.py::test_text_after_the_disclosure_marker_is_refused_with_an_explanation_not_dropped` | portado |
| `test_exports_low.py::test_reading_and_writing_back_the_text_is_allowed_even_if_disclosures_changed` | portado |
| `test_exports_low.py::test_marker_without_extra_text_and_text_before_the_marker_are_preserved` | portado |
| `test_exports_low.py::test_video_hash_never_runs_inside_a_write_transaction` | portado |
| `test_exports_low.py::test_export_hashes_artifacts_outside_the_database_transaction` | portado |
| `test_exports_low.py::test_guarantee_that_the_verified_file_is_the_exported_one_still_holds` | portado |
| `test_error_center.py::test_lists_failures_with_filters_counts_and_portuguese_text` | portado |
| `test_error_center.py::test_production_and_workflow_items_link_to_their_origin` | portado |
| `test_error_center.py::test_no_leak_of_paths_secrets_or_free_text` | portado |
| `test_error_center.py::test_pagination_cursor_and_limits` | portado |
| `test_error_center.py::test_invalid_parameters_are_422_without_echo` | portado |
| `test_error_center.py::test_requires_authentication` | portado |
| `test_error_center.py::test_read_only_and_no_schema_change` | portado |
| `test_review_comments.py::test_create_list_resolve_reopen_with_decision_trail` | portado |
| `test_review_comments.py::test_filters_ordering_and_pagination` | portado |
| `test_review_comments.py::test_idempotency_and_replay` | portado |
| `test_review_comments.py::test_invalid_bodies_are_422_without_echo` | portado |
| `test_review_comments.py::test_nan_and_control_characters_are_refused` | portado |
| `test_review_comments.py::test_foreign_hash_unknown_production_and_comment_and_missing_key` | portado |
| `test_review_comments.py::test_invalid_query_is_422` | portado |
| `test_review_comments.py::test_requires_authentication` | portado |
| `test_review_comments.py::test_limit_of_comments_per_production` | portado |
| `test_review_comments.py::test_trail_limit` | portado |
| `test_review_comments.py::test_corrupt_file_is_never_overwritten` | equivalente PG em `test_review_comments.py::test_corrupt_file_is_never_overwritten` (triggers append-only `no_update`/`no_delete` em `horus.review_comment_events`, sem JSON em disco) |
| `test_review_comments.py::test_storage_is_atomic_json_without_secrets_or_paths_and_no_schema_change` | equivalente PG em `test_review_comments.py::test_storage_is_atomic_json_without_secrets_or_paths_and_no_schema_change` (inspeção de `horus.review_comments*` e `horus.schema_migrations`) |
| `test_review_comments.py::test_comments_do_not_change_the_existing_review_endpoint` | portado |
| `test_local_data_comments.py::test_round_trip_restores_comments_trail_receipts_and_notification_state` | portado |
| `test_local_data_comments.py::test_package_without_comments_or_notifications_still_verifies_and_restores` | portado |
| `test_local_data_comments.py::test_tampered_comment_and_state_packages_are_refused_before_writing` | portado |
| `test_local_data_comments.py::test_export_excludes_unreadable_invalid_or_credential_shaped_comment_files` | portado |
| `test_local_data_comments.py::test_human_author_kind_cannot_be_forged_through_the_api` | portado |

## 4. Verificações executadas

- **Grupo C completo (`pytest -n 4` nos 10 arquivos):** **392 passed, 2 skipped (pré-existentes), 3 failed** — os 3 `failed` correspondem exatamente aos 2 defeitos de produto descritos na seção 2 (`exports.py:229` e `publishing.py` replay com `video_missing`).
- **Transversais (`test_supply_chain.py`, `test_network_inventory.py`, `test_api_contract_snapshot.py`, `test_endpoint_auth.py`, `test_no_hidden_sqlite.py`):** **104 passed, 4 skipped** (exit code 0).
