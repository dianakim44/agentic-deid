# Full mutation-run log

Every run of *all* registered mutations, appended, newest at the bottom. Written by
`tests/mutations/parallel.py`, which refuses to write "full run" unless five invariants
hold — see that file's docstring for what each one catches and why.

**This file is the answer to "when was the gate last run in full".** The trigger — when a
full run is required and when an impact-scope run suffices — is in `CLAUDE.md`, because it
fires at commit time and a rule nobody consults at commit time is not a rule. Its reasoning,
and the cost measurements that force it, are in `tests/mutations/README.md`, which is also
where the per-mutation tables and the incidents behind them live. This file is dates,
totals and deltas.

An `INCOMPLETE` entry is kept rather than deleted. A refused run is evidence about the
harness, and removing it would leave the log reading as though the run never happened.

## 2026-08-20 — full run

- commit `50ea5e236015` (dirty working tree)
- 8 shards, 170 of 170 mutations measured
- wall clock **1.87 h** (6736 s)
- tree fingerprint `55708073957b6e6f` (all shards agree)
- baseline 1696 tests (all shards agree)

| verdict | n |
|---|---|
| caught | 170 |
| survived | 0 |
| stale | 0 |
| broken | 0 |
| dirty | 0 |

### Kill counts that differ from the previous record

No decreases.

Increases:

- `a_mismatched_model_is_recorded_rather_than_refused` 2 → 3
- `an_unreadable_tree_state_reads_as_clean` 5 → 107
- `arm_rules_path_drops_the_axes` 6 → 74
- `arm_rules_path_drops_the_iteration` 4 → 10
- `arm_rules_path_loses_the_rules_component` 3 → 7
- `fully_covered_is_relaxed` 11 → 14
- `greedy_allows_reuse` 8 → 9
- `leak_rate_from_assignment` 9 → 10
- `no_bom_shift` 70 → 142
- `rule_id_vocabulary_not_checked` 8 → 20
- `the_history_is_pre_seeded_with_this_rounds_rate` 3 → 5
- `the_reply_text_is_taken_from_the_first_block` 16 → 85
- `type_in_both_lists` 78 → 150
- `unsealed_load_filters_instead_of_not_reaching` 73 → 145
- `utf8_sig` 70 → 142

First measured here (35): `a_ceiling_stop_is_recorded_as_converged`, `a_flag_overlapping_a_mask_tag_is_kept_when_it_is_not_contained`, `a_heterogeneous_union_prints_one_of_its_types`, `a_total_below_its_round_is_published`, `an_empty_lifecycle_mapping_is_written_as_no_probe`, `an_out_of_range_column_is_snapped_to_the_line`, `an_undefined_rate_prints_as_zero`, `an_unknown_flag_field_is_ignored_instead_of_refused`, `both_halves_of_the_export_use_one_mode`, `delta_reverts_to_the_constant_half_point`, `filled_prompt_paths_allowed`, `k_drops_to_one_so_consecutive_means_nothing`, `missed_is_the_unmatched_gold_rather_than_the_uncovered`, `only_the_round_the_report_names_is_checked`, `overlapping_mask_tags_are_accepted`, `prompt_tokens_is_what_the_invoice_was_computed_on`, `round_one_ignores_the_feedback_it_was_handed`, `round_one_reassembles_the_baselines_prompt`, `summing_carries_an_undeclared_key_into_the_total`, `summing_takes_the_longest_call_as_the_wall_time`, `tags_out_of_order_are_sorted_instead_of_refused`, `the_assembled_total_is_trusted_rather_than_checked`, `the_audit_report_gets_a_second_path_key`, `the_audit_report_is_allowed_instead_of_denied`, `the_audit_report_is_read_as_the_previous_rounds_file`, `the_export_index_is_the_in_scope_position`, `the_export_reads_the_missing_index_as_zero`, `the_iteration_allow_pattern_covers_the_whole_directory`, `the_lifecycle_probe_can_abort_the_arm`, `the_mask_tags_are_emitted_in_the_order_they_were_applied`, `the_per_iteration_key_replaces_the_arm_level_one`, `the_probe_error_carries_the_exception_message`, `the_report_reads_its_own_round_as_the_masked_one`, `the_score_block_carries_the_run_and_cost_blocks_too`, `the_writer_adds_the_rounds_up_itself`

Unchanged: 120.

<details><summary>All measured counts</summary>

| mutation | kills | min_kills |
|---|---|---|
| `a_ceiling_stop_is_recorded_as_converged` | 4 | 2 |
| `a_checksum_accepts_every_match` | 1 | 1 |
| `a_cue_span_swallows_the_cue` | 2 | 1 |
| `a_dirty_tree_reads_as_clean` | 7 | 3 |
| `a_disagreeing_prefix_still_opens_the_layer` | 1 | 1 |
| `a_duplicate_rule_id_is_allowed` | 1 | 1 |
| `a_flag_overlapping_a_mask_tag_is_kept_when_it_is_not_contained` | 3 | 2 |
| `a_format_failure_writes_zeroed_metrics_too` | 1 | 1 |
| `a_gazetteer_term_is_a_regex` | 2 | 1 |
| `a_gazetteer_term_needs_a_word_character_at_each_edge` | 1 | 1 |
| `a_heterogeneous_union_prints_one_of_its_types` | 4 | 3 |
| `a_later_round_audits_and_samples_round_one` | 2 | 2 |
| `a_lexicon_name_may_traverse_directories` | 1 | 1 |
| `a_mismatched_model_is_recorded_rather_than_refused` | 3 | 2 |
| `a_non_target_type_may_be_a_rule_target` | 1 | 1 |
| `a_null_commit_needs_no_unknown_tree` | 1 | 1 |
| `a_real_arm_may_draw_a_practice_number` | 4 | 1 |
| `a_rehearsal_may_draw_a_real_number` | 5 | 1 |
| `a_round_with_no_score_walks_back_to_the_last_one` | 1 | 1 |
| `a_rule_layer_is_derived_from_the_rule_id` | 1 | 1 |
| `a_stale_patch_exemption_is_ignored` | 1 | 1 |
| `a_total_below_its_round_is_published` | 2 | 2 |
| `absent_token_counts_default_to_zero` | 2 | 1 |
| `allowlist_may_name_corpus_paths` | 1 | 1 |
| `an_empty_lifecycle_mapping_is_written_as_no_probe` | 1 | 1 |
| `an_out_of_range_column_is_snapped_to_the_line` | 2 | 2 |
| `an_undefined_rate_prints_as_zero` | 1 | 1 |
| `an_unimplemented_checksum_is_ignored` | 1 | 1 |
| `an_unknown_flag_field_is_ignored_instead_of_refused` | 8 | 1 |
| `an_unknown_language_gets_every_layer` | 1 | 1 |
| `an_unreadable_tree_state_reads_as_clean` | 107 | 3 |
| `arm_rules_path_drops_the_axes` | 74 | 3 |
| `arm_rules_path_drops_the_iteration` | 10 | 2 |
| `arm_rules_path_loses_the_rules_component` | 7 | 1 |
| `arm_started_reads_the_last_line_only` | 1 | 1 |
| `assert_offsets_noop` | 3 | 3 |
| `both_halves_of_the_export_use_one_mode` | 4 | 2 |
| `bucket_unknown_types` | 1 | 1 |
| `by_rule_fp_from_coverage` | 5 | 1 |
| `caching_is_inferred_from_the_prompt_carrying_a_boundary` | 2 | 2 |
| `check_rules_detects_separately` | 1 | 1 |
| `check_rules_reads_every_fold` | 3 | 1 |
| `conftest_availability_from_a_load` | 1 | 1 |
| `converged_is_stored_beside_the_reason` | 1 | 1 |
| `delta_reverts_to_the_constant_half_point` | 6 | 2 |
| `detect_fold_drops_overlaps` | 2 | 1 |
| `drift_is_checked_against_todays_window_not_the_recorded_one` | 2 | 2 |
| `drop_excluded` | 12 | 11 |
| `familiares_as_other` | 8 | 7 |
| `filled_prompt_exposes_its_text` | 2 | 1 |
| `filled_prompt_paths_allowed` | 4 | 1 |
| `fold_from_directory_not_file` | 2 | 1 |
| `freeze_guard_only_checks_the_file` | 8 | 1 |
| `fully_covered_is_relaxed` | 14 | 1 |
| `generated_accepts_a_bare_date` | 3 | 1 |
| `greedy_allows_reuse` | 9 | 1 |
| `greedy_tiebreak_dropped` | 1 | 1 |
| `grouping_name_only` | 2 | 1 |
| `grouping_numeric_suffix_only` | 1 | 1 |
| `human_log_allowed_under_any_arm` | 1 | 1 |
| `human_log_path_from_a_literal` | 1 | 1 |
| `initial_pool_excludes_train_instead_of_selecting_dev` | 1 | 1 |
| `k_drops_to_one_so_consecutive_means_nothing` | 11 | 3 |
| `layer_family_union_becomes_subset` | 2 | 1 |
| `leak_rate_from_assignment` | 10 | 1 |
| `log_append_disabled` | 2 | 2 |
| `logging_gate_defaults_to_open` | 4 | 2 |
| `missed_is_the_unmatched_gold_rather_than_the_uncovered` | 3 | 3 |
| `missing_test_fold` | 2 | 1 |
| `no_bom_shift` | 142 | 23 |
| `non_target_filter_removed` | 9 | 1 |
| `non_target_types_hardcoded_not_read_from_config` | 1 | 1 |
| `only_the_round_the_report_names_is_checked` | 1 | 1 |
| `only_the_score_is_scoped_to_the_round` | 26 | 1 |
| `only_tracked_modifications_count_as_dirty` | 2 | 2 |
| `overlapping_mask_tags_are_accepted` | 5 | 3 |
| `prompt_tokens_is_what_the_invoice_was_computed_on` | 4 | 3 |
| `render_offsets_are_document_offsets` | 1 | 1 |
| `rendered_window_may_be_redirected` | 2 | 1 |
| `renderer_writes_a_debug_copy` | 1 | 1 |
| `round_one_ignores_the_feedback_it_was_handed` | 4 | 1 |
| `round_one_reassembles_the_baselines_prompt` | 1 | 1 |
| `rule_id_vocabulary_not_checked` | 20 | 1 |
| `rule_source_not_recorded` | 2 | 1 |
| `rule_source_recorded_absolute` | 7 | 1 |
| `run_fold_detects_separately` | 4 | 1 |
| `run_fold_hardcodes_the_absent_value` | 1 | 1 |
| `run_fold_infers_its_own_rule_path` | 1 | 1 |
| `run_fold_omits_the_layer` | 32 | 1 |
| `run_fold_reads_the_sealed_fold` | 2 | 1 |
| `run_fold_skips_axis_validation` | 1 | 1 |
| `run_fold_writes_a_null_model_id` | 45 | 1 |
| `run_fold_writes_unsorted_spans` | 3 | 1 |
| `sample_pool_not_sorted` | 3 | 1 |
| `sample_seed_from_process_hash` | 2 | 1 |
| `sealed_callable_from_anywhere` | 2 | 2 |
| `sealed_exempt_from_exit_code` | 1 | 1 |
| `sealed_flag_not_cleared` | 1 | 1 |
| `sealed_root_falls_back_to_corpus` | 1 | 1 |
| `self_report_defaults_to_none` | 1 | 1 |
| `self_report_refuses_the_violation` | 3 | 1 |
| `spans_file_carries_the_surface` | 34 | 1 |
| `split_disagreement_ignored` | 1 | 1 |
| `split_file_span_count` | 3 | 1 |
| `split_ignores_membership` | 1 | 1 |
| `split_verify_noop` | 2 | 1 |
| `staged_sealed_not_escalated` | 2 | 2 |
| `started_where_reads_the_worktree_only` | 8 | 1 |
| `summary_reports_offsets` | 1 | 1 |
| `summing_carries_an_undeclared_key_into_the_total` | 1 | 1 |
| `summing_takes_the_longest_call_as_the_wall_time` | 1 | 1 |
| `tags_out_of_order_are_sorted_instead_of_refused` | 2 | 2 |
| `terminal_exit_does_not_check_the_destination` | 3 | 1 |
| `test_file_shadows_the_shared_fixture` | 2 | 2 |
| `the_arm_freeze_guard_only_checks_the_file` | 7 | 3 |
| `the_arm_reports_no_model_and_no_cost_to_the_scorer` | 4 | 2 |
| `the_arms_total_is_the_last_rounds_cost` | 3 | 1 |
| `the_assembled_total_is_trusted_rather_than_checked` | 1 | 1 |
| `the_audit_report_gets_a_second_path_key` | 1 | 1 |
| `the_audit_report_is_allowed_instead_of_denied` | 2 | 2 |
| `the_audit_report_is_read_as_the_previous_rounds_file` | 69 | 1 |
| `the_baseline_draws_error_spans` | 2 | 1 |
| `the_cache_boundary_crosses_onto_the_masked_document` | 2 | 1 |
| `the_call_is_logged_after_the_response_is_judged` | 8 | 1 |
| `the_call_role_is_written_without_being_validated` | 1 | 1 |
| `the_captured_surface_check_reads_only_direct_stream_access` | 1 | 1 |
| `the_captured_surface_control_is_scoped_to_the_function` | 1 | 1 |
| `the_client_hardcodes_botocores_default_attempts` | 1 | 1 |
| `the_conftest_suite_glob_points_one_level_deep` | 5 | 1 |
| `the_declared_rule_file_language_is_trusted` | 2 | 1 |
| `the_export_index_is_the_in_scope_position` | 2 | 1 |
| `the_export_reads_the_missing_index_as_zero` | 1 | 1 |
| `the_failure_record_paraphrases_the_validator` | 3 | 1 |
| `the_final_rounds_duplicate_comes_from_a_second_scoring` | 1 | 1 |
| `the_folds_seconds_go_to_the_round_and_not_the_arm` | 76 | 1 |
| `the_freeze_record_claims_the_sampling_parameters_applied` | 5 | 2 |
| `the_freeze_record_drops_the_empty_block_marking` | 4 | 1 |
| `the_frozen_split_check_ignores_a_moved_document` | 3 | 2 |
| `the_frozen_split_is_verified_after_the_read` | 1 | 1 |
| `the_history_is_pre_seeded_with_this_rounds_rate` | 5 | 3 |
| `the_iteration_allow_pattern_covers_the_whole_directory` | 3 | 1 |
| `the_language_layer_is_a_substring_test` | 1 | 1 |
| `the_language_layer_is_keyed_on_the_id_the_model_wrote` | 2 | 1 |
| `the_lifecycle_block_moves_into_the_run_block` | 2 | 2 |
| `the_lifecycle_probe_can_abort_the_arm` | 2 | 2 |
| `the_logging_check_reports_an_unreadable_setting_as_clean` | 4 | 2 |
| `the_mask_tags_are_emitted_in_the_order_they_were_applied` | 85 | 3 |
| `the_parse_error_quotes_the_line_it_choked_on` | 1 | 1 |
| `the_patch_allowlist_stops_requiring_a_reason` | 2 | 2 |
| `the_patch_check_credits_a_bare_function_name` | 1 | 1 |
| `the_patch_check_credits_a_whole_file` | 1 | 1 |
| `the_per_iteration_key_replaces_the_arm_level_one` | 110 | 2 |
| `the_practice_window_may_overlap_iteration_one` | 4 | 1 |
| `the_probe_error_carries_the_exception_message` | 3 | 1 |
| `the_provenance_fields_are_optional_again` | 5 | 3 |
| `the_recorded_files_list_is_ignored_in_favour_of_the_fields_present` | 1 | 1 |
| `the_reply_text_is_taken_from_the_first_block` | 85 | 1 |
| `the_report_reads_its_own_round_as_the_masked_one` | 1 | 1 |
| `the_role_is_appended_at_the_end_of_the_line` | 2 | 1 |
| `the_round_s_files_are_written_by_every_arm` | 3 | 2 |
| `the_rule_authors_prompt_is_cached_too` | 2 | 2 |
| `the_score_block_carries_the_run_and_cost_blocks_too` | 1 | 1 |
| `the_suite_glob_points_one_level_deep` | 5 | 1 |
| `the_writer_adds_the_rounds_up_itself` | 10 | 1 |
| `the_writer_calls_the_stopping_rule_itself` | 1 | 1 |
| `top_level_leak_allowed` | 1 | 1 |
| `type_in_both_lists` | 150 | 2 |
| `unsealed_load_filters_instead_of_not_reaching` | 145 | 1 |
| `utf8_sig` | 142 | 23 |
| `zero_minutes_read_as_not_started` | 1 | 1 |

</details>

#### Annotation, added by hand after the run

Two things the driver could not know about its own record.

**Provenance.** The run started at `50ea5e2` with a dirty tree, because the uncommitted change
*was* `parallel.py` and the harness options it needs. Nothing was edited during the run — the
fifth invariant confirms it, `git status --porcelain` being byte-identical at start and end —
and the commit that follows this record froze exactly the tree that was measured. So the
reproducible reference for these numbers is that commit, not `50ea5e2`, and the two differ by
no file content at all.

**The comparison baseline was a scrape, and it had one error.** `--previous` was fed counts
parsed out of `tests/mutations/README.md`'s tables. That parse read the wrong column for
`filled_prompt_paths_allowed`, whose cell contains a literal `|` inside
`prompts/(filled|rendered)/`. So the counts above are right and the *diff* above is off by one
in two places: 16 counts rose, not 15, and 34 were first measured here, not 35. Corrected in
`tests/mutations/README.md` §"The first full run". Future comparisons are against
`mutation-full-runs.counts.json`, which is written from measurements rather than parsed from
prose — this is the last run whose baseline came from a scrape.

**Zero decreases across all 170, and all 72 counts that the impact-scope run of 2026-08-19 had
measured under the current 27-file suite came back identical.** The 16 rises are all against
pre-27-file numbers.
