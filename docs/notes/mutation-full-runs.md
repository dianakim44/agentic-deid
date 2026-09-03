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

---

## Outstanding re-measurement debt — maintained by hand

Not written by `parallel.py`. This section names counts in the record below that are no longer
comparable to the current suite, so the next full run knows what it is settling rather than
rediscovering it. An entry is deleted only when a full run has re-measured it; an entry cleared by
an impact-scope run is marked measured and kept until then, because a scope run cannot restate the
full run's denominator.

CLAUDE.md's rule that re-anchored mutations are re-measured **regardless of whether they fell in
scope** is what this section serves: an anchor edit is a change to the mutation itself, so its old
count describes a different experiment, and that fact is invisible from the count alone.

### Settled by the full run of 2026-09-02 — the three entries that stood here are discharged

**185 of 185 caught, nothing survived, at commit `031bff3db479` with a clean tree.** The record
is appended below. Three "owed to the next full run" entries stood above this line and are
deleted rather than kept, which is this section's rule: an entry goes when a full run has
re-measured it.

| opened | what it owed | how it came back |
|---|---|---|
| 2026-09-01 | confirmation for the lexicon-collection change — two `src/` modules, two added mutations, one re-anchor — deferred to the `port-multi` commit | the deferral was honoured at the commit named; the two added mutations reproduce their scope-run counts (2 and 1) and the re-anchored `run_fold_infers_its_own_rule_path` reproduces its 1 |
| 2026-08-29 | no expectation in either direction for the whole seal path, because four assertions had been *loosened* from absolute to relative and a loosening can in principle lower a count | **no count fell anywhere in the set.** The tracing recorded there as an argument is now a measurement on the only question it was making a claim about |
| 2026-08-28 | confirmation that the other 181 counts, measured at baseline 1880, were not left low by the one test added afterwards | measured at baseline 1945; the added mutation `the_entry_point_calls_its_own_copy_of_main` reproduces its 1 |

**Also settled: the 27 README cells marked `†`.** They were the 2026-09-01 entry's bookkeeping
finding — cells that contradicted the 2026-08-28 sidecar and had not been re-measured, so they
carried a marker meaning *lower bound, not a current value*. This run re-measured all 27, and
they are rewritten in `tests/mutations/README.md` from this run's own sidecar with the markers
removed, together with one cell that drifted after the marking was done and so was never marked
(`drift_is_checked_against_todays_window_not_the_recorded_one`, 2 → 3). Twenty-eight cells
changed and all 28 rose, which is the only direction the drift has.
`test_a_readme_count_that_contradicts_the_last_full_run_is_marked` is what took the markers off
rather than memory: it fails on a surplus `†` as well as a missing one.

**The baseline moved 1880 → 1945 tests**, all eight shards agreeing — one of `parallel.py`'s five
invariants, so the number is checked and not merely reported. Eleven counts rose, none fell, four
were first measured under the full denominator and 170 are unchanged; the largest single rise is
`the_per_iteration_key_replaces_the_arm_level_one` 146 → 164. With no decrease anywhere, nothing
in the record below describes a guarantee that has since been weakened.

### The four tests the sealed opening turned red — same event, recorded here because it is a gate fact

Commit `de1d881`, which recorded the opening, left the suite **red**: four tests asserted a
never-opened fold, and opening one falsified them. Two read `count_runs == 1` on a `temp_log`
seeded from the real log (`test_the_authorised_caller_passes_the_gate_and_is_logged`,
`test_count_runs_still_keys_on_the_corpus_column`), one asserted the row numbers `"1"` and
`"2"` (`test_rows_are_numbered_consecutively`), and one required the sealed `metrics.json` not
to exist (`test_verify_dev_writes_nothing_and_opens_nothing`). The first three were repaired by
loosening the absolute expectations to relative ones; the fourth was strictly broken rather
than loosened, since it asserted a file's absence after the file legitimately existed, and so
had been failing at baseline and killing nothing. That loosening is what the 2026-08-29 debt
entry was about, and the full run of 2026-09-02 settled it — no count fell.

The gate consequence is the part that belongs in this file rather than only in
`tests/mutations/README.md`: **`run.py` refuses to measure anything on a red baseline** — it
prints `BASELINE IS NOT GREEN` and aborts before applying a single mutation. So between the
opening and the fix, no kill count could be produced at all, by a scope run or a full one.
An opening was the one event guaranteed to trigger it, and the tests that broke were the
seal's own.

### Nothing outstanding — settled by the full run of 2026-08-28, the same day it was opened

The debt was opened by `86415c8` under CLAUDE.md's second trigger — two `src/` modules
(`src/corpora/base.py`: the loader's refusal of the log row's fields off the sealed path, and
`loader_for` becoming the one registry lookup; `src/eval/run_sealed_eval.py`: `--arm`, and
`_loader_for` delegating to that lookup) — and **settled by the run appended below, at that same
commit: 181 of 181 caught, none survived.** What was owed was confirmation rather than repair,
because the set grew 179 → 181 with `TEST_FILES` unmoved, and confirmation is what arrived.

**Two counts rose and none fell.** `the_per_iteration_key_replaces_the_arm_level_one` 144 → 146 and
`type_in_both_lists` 162 → 164, +2 each. The thirteen tests added by `86415c8` are the only
candidates, since nothing else about the suite or those two mutations moved, and both are mutations
of metrics-record structure that any test planning an arm reads through. *Which* two of the thirteen
is not recorded: the harness stores a verdict and a kill count per mutation, not the killers' names,
so attributing an increase to a named test would be a guess dressed as a measurement. The direction
is the whole of what is established, and it is the only direction added assertions can move a count.

**The two mutations added with those guards reproduce their scope-run numbers**, 1 each
(`the_arm_and_the_round_are_ignored_off_the_sealed_path`,
`the_two_arm_spellings_are_merged_instead_of_refused`). They were first measurements when the scope
run made them, so strictly this run is their first *comparable* number; the point worth keeping is
that a count of 1 held under the full denominator rather than being an artefact of the narrower
one. A count of 1 is the honest number here rather than a thin one: each guard has exactly one test
written against it and no older test would have noticed either edit.

**The baseline moved 1867 → 1880 tests** and all eight shards agree on 1880 — the thirteen are
inside existing `TEST_FILES` members (twelve in `tests/test_sealed_scoring.py`, one in
`tests/test_seal.py`). Agreement across shards on that total is one of `parallel.py`'s five
invariants, so the number is checked here and not merely reported.

**177 counts unchanged.** With no decreases anywhere in the set, nothing in the record below
describes a guarantee that has since been weakened.

### Nothing outstanding — settled by the full run of 2026-08-26

Two re-anchored mutations were owed to this run, both from the commit that built the sealed
scoring path (`3efc5ee`), and **both reproduce their old numbers**: `log_append_disabled` **2**
and `the_frozen_split_is_verified_after_the_read` **1**. The anchors moved for the same reason —
`base.load`'s sealed branch and `run_sealed_eval`'s verify/read pair both gained the arm and the
round — so in each case the guarantee is unchanged and only the text carrying it moved. That is
the case where a reproduced count is the expected result rather than a lucky one, and it is worth
distinguishing from the 2026-08-24 entries below, where a re-anchoring *did* change the number.

The two mutations added in the same commit are first measurements and owe nothing:
`the_arm_cell_is_a_constant_again` **2**, `a_round_other_than_the_last_can_be_scored` **4**.
`tests/test_sealed_scoring.py` entering `TEST_FILES` is what made the run mandatory — a new
denominator, not a stale count — and it raised twenty other counts and lowered none.

### Settled earlier — the full run of 2026-08-25

Both entries that stood here are deleted, which is what the rule above says to do once a full run
has re-measured them. They are reproduced in shortened form for the one thing that is worth
keeping: **all nine numbers a scope run had measured came back identical under the full run's
denominator.**

- **`the_client_hardcodes_botocores_default_attempts` — re-anchored in `efe11e1`.** `55e7b35`
  changed `max_attempts` to `total_max_attempts`, the key that actually means what
  `MAX_ATTEMPTS = 1` claims, so until the anchor was repaired the mutation would have raised
  `StaleMutation` and the 2026-08-20 count of 1 was not going to be reproduced by any run. The
  scope run of 2026-08-24 measured 2; the full run measures **2**. The second killer is
  `test_the_transport_is_pinned_to_one_attempt`, which asserts against the built client's
  `retries` rather than against the constant — the assertion whose absence let the live defect
  pass for sixteen days.
- **The eight in scope of `tests/test_release_screen.py`'s acknowledged-violations tests.** All
  eight reproduce: `allowlist_may_name_corpus_paths` **2**, `rule_id_vocabulary_not_checked`
  **25**, `the_language_layer_is_keyed_on_the_id_the_model_wrote` **3**, and the five that were
  unchanged at the scope run (`sealed_exempt_from_exit_code` 1, `staged_sealed_not_escalated` 2,
  `a_disagreeing_prefix_still_opens_the_layer` 1, `an_unknown_language_gets_every_layer` 1,
  `the_language_layer_is_a_substring_test` 1) are unchanged again. The three rises were predicted
  before being measured — `test_every_acknowledged_entry_is_a_hit_that_actually_happens` asserts a
  live sniffer hit, so a mutation that stops the vocabulary check from firing now fails a
  `test_release_screen.py` test that never mentions the vocabulary — and the full run is where that
  prediction stops resting on a scope run's denominator.

That a scope run's numbers reproduced under the full run is a fact about these nine and not a
general licence: the reason the entries were kept was that a scope run *cannot* restate the
denominator, and nine agreements do not make it able to. What it does establish is that the
runtime-reach scoping used on 2026-08-24 picked the right mutations, twice.

<details><summary>The two entries as they stood before the run</summary>

**`the_client_hardcodes_botocores_default_attempts` — re-anchored in `efe11e1` (2026-08-24).**
The 2026-08-20 record says 1 kill. That number was measured against the anchor
`config=Config(retries={"max_attempts": MAX_ATTEMPTS, "mode": "standard"}),`, a line that no longer
exists: `55e7b35` changed the key to `total_max_attempts`, which is the key that actually means what
`MAX_ATTEMPTS = 1` claims. Until it was re-anchored the mutation was not merely stale but **broken**
— it would have raised `StaleMutation` — so the old count was not going to be reproduced by any run.

*Measured 2026-08-24 by scope run: **2 kills** (was 1), baseline 1724 tests, tree
`d6bcf094d3c89f45`.* The second killer is `test_the_transport_is_pinned_to_one_attempt`, which now
asserts against the built client's `retries` rather than against the constant — the assertion whose
absence let the live defect pass for sixteen days. Still owed to the next full run, which is the
only place this becomes comparable to the other 169.

**`tests/test_release_screen.py` gained the acknowledged-violations tests (2026-08-24).** Ten new
tests and eight edited call sites, for `screen_allowlist.json`'s third category. `TEST_FILES`
membership is unchanged, so the denominator holds and an impact-scope run is what CLAUDE.md calls
for. Two things make the scope wider than the diff looks:

- The new tests exercise `load_allowlist`'s path rules and `partition_suspect`'s categories
  directly, so the mutations anchored there can gain killers.
- `test_every_acknowledged_entry_is_a_hit_that_actually_happens` asserts that each acknowledged
  entry still *matches a live sniffer hit*. Any mutation that stops the `rule_id` vocabulary check
  from firing on this arm's round-4 and round-5 rule files now breaks that assertion — so the
  vocabulary and language-layer mutations are in scope through a test that never mentions them.
  This is the runtime-reach rule doing its job: filename overlap would have missed it entirely.

No expectation of "unchanged" is recorded for anything here, per CLAUDE.md — there is no comparable
value until it is measured.

*Measured 2026-08-24 by scope run, 8 mutations, baseline 1724 tests, tree `f4a0adba011f890c`. All
eight caught; the working tree was byte-identical before and after.*

| mutation | 2026-08-20 | now |
|---|---|---|
| `allowlist_may_name_corpus_paths` | 1 | **2** |
| `rule_id_vocabulary_not_checked` | 20 | **25** |
| `the_language_layer_is_keyed_on_the_id_the_model_wrote` | 2 | **3** |
| `sealed_exempt_from_exit_code` | 1 | 1 |
| `staged_sealed_not_escalated` | 2 | 2 |
| `a_disagreeing_prefix_still_opens_the_layer` | 1 | 1 |
| `an_unknown_language_gets_every_layer` | 1 | 1 |
| `the_language_layer_is_a_substring_test` | 1 | 1 |

The two rises worth naming are the second and third rows, because they are the ones the paragraph
above predicted and neither is anchored in a file the diff touched.
`test_every_acknowledged_entry_is_a_hit_that_actually_happens` asserts a live sniffer hit, so a
mutation that stops the vocabulary check from firing now fails a `test_release_screen.py` test that
never mentions the vocabulary. The prediction was recorded before the measurement and the
measurement agrees with it, which is the only order in which that is worth anything.

Five of the eight came back identical, and that is recorded as a measurement and not as a
reassurance: an unchanged count under a *changed* suite is a fact about reach, namely that these
five are killed by tests the diff did not touch.

**Still owed to the next full run — all eight, plus
`the_client_hardcodes_botocores_default_attempts` above.** A scope run cannot restate the full
run's denominator, so these nine numbers are measured but not yet comparable to the other 161.
The 161 are deferred to the next full run, not exempt from it.

</details>

---

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

## 2026-08-25 — full run

- commit `87d51818f194` (clean working tree)
- 8 shards, 176 of 176 mutations measured
- wall clock **2.02 h** (7261 s)
- tree fingerprint `276308a0483f5f1d` (all shards agree)
- baseline 1804 tests (all shards agree)

| verdict | n |
|---|---|
| caught | 176 |
| survived | 0 |
| stale | 0 |
| broken | 0 |
| dirty | 0 |

### Kill counts that differ from the previous record

No decreases.

Increases:

- `allowlist_may_name_corpus_paths` 1 → 2
- `an_unreadable_tree_state_reads_as_clean` 107 → 117
- `arm_rules_path_drops_the_axes` 74 → 81
- `no_bom_shift` 142 → 151
- `only_the_score_is_scoped_to_the_round` 26 → 32
- `rule_id_vocabulary_not_checked` 20 → 25
- `run_fold_omits_the_layer` 32 → 38
- `run_fold_writes_a_null_model_id` 45 → 48
- `spans_file_carries_the_surface` 34 → 40
- `the_audit_report_is_allowed_instead_of_denied` 2 → 4
- `the_audit_report_is_read_as_the_previous_rounds_file` 69 → 75
- `the_client_hardcodes_botocores_default_attempts` 1 → 2
- `the_folds_seconds_go_to_the_round_and_not_the_arm` 76 → 83
- `the_iteration_allow_pattern_covers_the_whole_directory` 3 → 4
- `the_language_layer_is_keyed_on_the_id_the_model_wrote` 2 → 3
- `the_mask_tags_are_emitted_in_the_order_they_were_applied` 85 → 91
- `the_per_iteration_key_replaces_the_arm_level_one` 110 → 122
- `the_reply_text_is_taken_from_the_first_block` 85 → 92
- `type_in_both_lists` 150 → 159
- `unsealed_load_filters_instead_of_not_reaching` 145 → 154
- `utf8_sig` 142 → 151

First measured here (6): `the_abandoned_attempt_count_comes_from_the_log_alone`, `the_abandoned_block_uses_the_cost_block_names`, `the_abandoned_gate_is_the_draw_count_alone`, `the_audit_report_records_no_draw_number`, `the_next_draw_counts_the_draws_that_exist`, `the_preserved_draw_is_written_to_the_canonical_path`

Unchanged: 149.

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
| `allowlist_may_name_corpus_paths` | 2 | 1 |
| `an_empty_lifecycle_mapping_is_written_as_no_probe` | 1 | 1 |
| `an_out_of_range_column_is_snapped_to_the_line` | 2 | 2 |
| `an_undefined_rate_prints_as_zero` | 1 | 1 |
| `an_unimplemented_checksum_is_ignored` | 1 | 1 |
| `an_unknown_flag_field_is_ignored_instead_of_refused` | 8 | 1 |
| `an_unknown_language_gets_every_layer` | 1 | 1 |
| `an_unreadable_tree_state_reads_as_clean` | 117 | 3 |
| `arm_rules_path_drops_the_axes` | 81 | 3 |
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
| `no_bom_shift` | 151 | 23 |
| `non_target_filter_removed` | 9 | 1 |
| `non_target_types_hardcoded_not_read_from_config` | 1 | 1 |
| `only_the_round_the_report_names_is_checked` | 1 | 1 |
| `only_the_score_is_scoped_to_the_round` | 32 | 1 |
| `only_tracked_modifications_count_as_dirty` | 2 | 2 |
| `overlapping_mask_tags_are_accepted` | 5 | 3 |
| `prompt_tokens_is_what_the_invoice_was_computed_on` | 4 | 3 |
| `render_offsets_are_document_offsets` | 1 | 1 |
| `rendered_window_may_be_redirected` | 2 | 1 |
| `renderer_writes_a_debug_copy` | 1 | 1 |
| `round_one_ignores_the_feedback_it_was_handed` | 4 | 1 |
| `round_one_reassembles_the_baselines_prompt` | 1 | 1 |
| `rule_id_vocabulary_not_checked` | 25 | 1 |
| `rule_source_not_recorded` | 2 | 1 |
| `rule_source_recorded_absolute` | 7 | 1 |
| `run_fold_detects_separately` | 4 | 1 |
| `run_fold_hardcodes_the_absent_value` | 1 | 1 |
| `run_fold_infers_its_own_rule_path` | 1 | 1 |
| `run_fold_omits_the_layer` | 38 | 1 |
| `run_fold_reads_the_sealed_fold` | 2 | 1 |
| `run_fold_skips_axis_validation` | 1 | 1 |
| `run_fold_writes_a_null_model_id` | 48 | 1 |
| `run_fold_writes_unsorted_spans` | 3 | 1 |
| `sample_pool_not_sorted` | 3 | 1 |
| `sample_seed_from_process_hash` | 2 | 1 |
| `sealed_callable_from_anywhere` | 2 | 2 |
| `sealed_exempt_from_exit_code` | 1 | 1 |
| `sealed_flag_not_cleared` | 1 | 1 |
| `sealed_root_falls_back_to_corpus` | 1 | 1 |
| `self_report_defaults_to_none` | 1 | 1 |
| `self_report_refuses_the_violation` | 3 | 1 |
| `spans_file_carries_the_surface` | 40 | 1 |
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
| `the_abandoned_attempt_count_comes_from_the_log_alone` | 1 | 1 |
| `the_abandoned_block_uses_the_cost_block_names` | 15 | 3 |
| `the_abandoned_gate_is_the_draw_count_alone` | 2 | 2 |
| `the_arm_freeze_guard_only_checks_the_file` | 7 | 3 |
| `the_arm_reports_no_model_and_no_cost_to_the_scorer` | 4 | 2 |
| `the_arms_total_is_the_last_rounds_cost` | 3 | 1 |
| `the_assembled_total_is_trusted_rather_than_checked` | 1 | 1 |
| `the_audit_report_gets_a_second_path_key` | 1 | 1 |
| `the_audit_report_is_allowed_instead_of_denied` | 4 | 2 |
| `the_audit_report_is_read_as_the_previous_rounds_file` | 75 | 1 |
| `the_audit_report_records_no_draw_number` | 36 | 3 |
| `the_baseline_draws_error_spans` | 2 | 1 |
| `the_cache_boundary_crosses_onto_the_masked_document` | 2 | 1 |
| `the_call_is_logged_after_the_response_is_judged` | 8 | 1 |
| `the_call_role_is_written_without_being_validated` | 1 | 1 |
| `the_captured_surface_check_reads_only_direct_stream_access` | 1 | 1 |
| `the_captured_surface_control_is_scoped_to_the_function` | 1 | 1 |
| `the_client_hardcodes_botocores_default_attempts` | 2 | 1 |
| `the_conftest_suite_glob_points_one_level_deep` | 5 | 1 |
| `the_declared_rule_file_language_is_trusted` | 2 | 1 |
| `the_export_index_is_the_in_scope_position` | 2 | 1 |
| `the_export_reads_the_missing_index_as_zero` | 1 | 1 |
| `the_failure_record_paraphrases_the_validator` | 3 | 1 |
| `the_final_rounds_duplicate_comes_from_a_second_scoring` | 1 | 1 |
| `the_folds_seconds_go_to_the_round_and_not_the_arm` | 83 | 1 |
| `the_freeze_record_claims_the_sampling_parameters_applied` | 5 | 2 |
| `the_freeze_record_drops_the_empty_block_marking` | 4 | 1 |
| `the_frozen_split_check_ignores_a_moved_document` | 3 | 2 |
| `the_frozen_split_is_verified_after_the_read` | 1 | 1 |
| `the_history_is_pre_seeded_with_this_rounds_rate` | 5 | 3 |
| `the_iteration_allow_pattern_covers_the_whole_directory` | 4 | 1 |
| `the_language_layer_is_a_substring_test` | 1 | 1 |
| `the_language_layer_is_keyed_on_the_id_the_model_wrote` | 3 | 1 |
| `the_lifecycle_block_moves_into_the_run_block` | 2 | 2 |
| `the_lifecycle_probe_can_abort_the_arm` | 2 | 2 |
| `the_logging_check_reports_an_unreadable_setting_as_clean` | 4 | 2 |
| `the_mask_tags_are_emitted_in_the_order_they_were_applied` | 91 | 3 |
| `the_next_draw_counts_the_draws_that_exist` | 2 | 2 |
| `the_parse_error_quotes_the_line_it_choked_on` | 1 | 1 |
| `the_patch_allowlist_stops_requiring_a_reason` | 2 | 2 |
| `the_patch_check_credits_a_bare_function_name` | 1 | 1 |
| `the_patch_check_credits_a_whole_file` | 1 | 1 |
| `the_per_iteration_key_replaces_the_arm_level_one` | 122 | 2 |
| `the_practice_window_may_overlap_iteration_one` | 4 | 1 |
| `the_preserved_draw_is_written_to_the_canonical_path` | 4 | 3 |
| `the_probe_error_carries_the_exception_message` | 3 | 1 |
| `the_provenance_fields_are_optional_again` | 5 | 3 |
| `the_recorded_files_list_is_ignored_in_favour_of_the_fields_present` | 1 | 1 |
| `the_reply_text_is_taken_from_the_first_block` | 92 | 1 |
| `the_report_reads_its_own_round_as_the_masked_one` | 1 | 1 |
| `the_role_is_appended_at_the_end_of_the_line` | 2 | 1 |
| `the_round_s_files_are_written_by_every_arm` | 3 | 2 |
| `the_rule_authors_prompt_is_cached_too` | 2 | 2 |
| `the_score_block_carries_the_run_and_cost_blocks_too` | 1 | 1 |
| `the_suite_glob_points_one_level_deep` | 5 | 1 |
| `the_writer_adds_the_rounds_up_itself` | 10 | 1 |
| `the_writer_calls_the_stopping_rule_itself` | 1 | 1 |
| `top_level_leak_allowed` | 1 | 1 |
| `type_in_both_lists` | 159 | 2 |
| `unsealed_load_filters_instead_of_not_reaching` | 154 | 1 |
| `utf8_sig` | 151 | 23 |
| `zero_minutes_read_as_not_started` | 1 | 1 |

</details>

#### Annotation, added by hand after the run

**Provenance is the plain case this time.** Clean tree at `87d51818f194`, and the fifth invariant
confirms nothing moved under the two hours: `git rev-parse HEAD` and `git status --porcelain` were
byte-identical at start and end. Re-running at that commit reproduces these numbers, with no
"except for" attached — which is the difference from the 2026-08-20 record, whose run started
dirty because the uncommitted change *was* `parallel.py`.

**The comparison baseline is `mutation-full-runs.counts.json`, not a scrape.** The previous record
had to say that its diff was off by one in two places because `--previous` was fed counts parsed
out of README tables and one cell contained a literal `|`. This run diffed against the sidecar the
last run wrote from its own measurements, so the increase list above is arithmetic on measured
numbers.

**176 is 170 + the six new mutations, and the denominator moved for that reason alone.** No
`TEST_FILES` membership change: the suite list is the same 27 files, and the harness baseline grew
1696 → 1804 tests from five days of assertions added inside them. That is what the 21 increases
are, and it is worth stating that none of them is a new assertion about the mutated guarantee.
`the_per_iteration_key_replaces_the_arm_level_one` 110 → 122, `type_in_both_lists` 150 → 159,
`unsealed_load_filters_instead_of_not_reaching` 145 → 154, `utf8_sig` 142 → 151 and
`no_bom_shift` 142 → 151 all break something most tests in a file write through, so they scale with
how much of the suite exercises the writer. **A large kill count is a statement about blast radius
and only a small one is a statement about coverage** — the same reading the previous record used.

**Zero decreases across all 170 carried forward, and that is the thing being looked for.** Adding
assertions can only raise a count; a count that fell while the suite grew would mean a test stopped
being able to see a defect it used to see, which is what `### The split moved three tests'
observation point` records happening before. None fell.

**The six first measured here are in `tests/mutations/README.md` §"Six on the draw and the abandoned
spend", including the one whose selective run came back SURVIVED.**
`the_next_draw_counts_the_draws_that_exist` reads 2 above, and 2 is the post-fix number: the
verdict was a finding about `test_the_next_draw_is_the_number_that_does_not_overwrite`, which
walked its property over four contiguous draws and so could not see the one failure the property is
about. The mutation was not weakened to match the suite; the walk now includes a gap.

## 2026-08-26 — full run

- commit `3efc5ee15cfd` (clean working tree)
- 8 shards, 179 of 179 mutations measured
- wall clock **2.07 h** (7456 s)
- tree fingerprint `445d096e62c07085` (all shards agree)
- baseline 1867 tests (all shards agree)

| verdict | n |
|---|---|
| caught | 179 |
| survived | 0 |
| stale | 0 |
| broken | 0 |
| dirty | 0 |

### Kill counts that differ from the previous record

No decreases.

Increases:

- `a_cue_span_swallows_the_cue` 2 → 3
- `an_unreadable_tree_state_reads_as_clean` 117 → 119
- `by_rule_fp_from_coverage` 5 → 6
- `detect_fold_drops_overlaps` 2 → 3
- `drop_excluded` 12 → 13
- `familiares_as_other` 8 → 9
- `fully_covered_is_relaxed` 14 → 15
- `greedy_allows_reuse` 9 → 10
- `greedy_tiebreak_dropped` 1 → 2
- `leak_rate_from_assignment` 10 → 11
- `no_bom_shift` 151 → 154
- `run_fold_detects_separately` 4 → 5
- `run_fold_writes_a_null_model_id` 48 → 49
- `the_abandoned_block_uses_the_cost_block_names` 15 → 19
- `the_folds_seconds_go_to_the_round_and_not_the_arm` 83 → 84
- `the_language_layer_is_a_substring_test` 1 → 3
- `the_per_iteration_key_replaces_the_arm_level_one` 122 → 144
- `the_writer_adds_the_rounds_up_itself` 10 → 11
- `type_in_both_lists` 159 → 162
- `unsealed_load_filters_instead_of_not_reaching` 154 → 157
- `utf8_sig` 151 → 154

First measured here (3): `a_round_other_than_the_last_can_be_scored`, `the_arm_axis_comes_back_off_an_auxiliary_input`, `the_arm_cell_is_a_constant_again`

Unchanged: 155.

<details><summary>All measured counts</summary>

| mutation | kills | min_kills |
|---|---|---|
| `a_ceiling_stop_is_recorded_as_converged` | 4 | 2 |
| `a_checksum_accepts_every_match` | 1 | 1 |
| `a_cue_span_swallows_the_cue` | 3 | 1 |
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
| `a_round_other_than_the_last_can_be_scored` | 4 | 2 |
| `a_round_with_no_score_walks_back_to_the_last_one` | 1 | 1 |
| `a_rule_layer_is_derived_from_the_rule_id` | 1 | 1 |
| `a_stale_patch_exemption_is_ignored` | 1 | 1 |
| `a_total_below_its_round_is_published` | 2 | 2 |
| `absent_token_counts_default_to_zero` | 2 | 1 |
| `allowlist_may_name_corpus_paths` | 2 | 1 |
| `an_empty_lifecycle_mapping_is_written_as_no_probe` | 1 | 1 |
| `an_out_of_range_column_is_snapped_to_the_line` | 2 | 2 |
| `an_undefined_rate_prints_as_zero` | 1 | 1 |
| `an_unimplemented_checksum_is_ignored` | 1 | 1 |
| `an_unknown_flag_field_is_ignored_instead_of_refused` | 8 | 1 |
| `an_unknown_language_gets_every_layer` | 1 | 1 |
| `an_unreadable_tree_state_reads_as_clean` | 119 | 3 |
| `arm_rules_path_drops_the_axes` | 81 | 3 |
| `arm_rules_path_drops_the_iteration` | 10 | 2 |
| `arm_rules_path_loses_the_rules_component` | 7 | 1 |
| `arm_started_reads_the_last_line_only` | 1 | 1 |
| `assert_offsets_noop` | 3 | 3 |
| `both_halves_of_the_export_use_one_mode` | 4 | 2 |
| `bucket_unknown_types` | 1 | 1 |
| `by_rule_fp_from_coverage` | 6 | 1 |
| `caching_is_inferred_from_the_prompt_carrying_a_boundary` | 2 | 2 |
| `check_rules_detects_separately` | 1 | 1 |
| `check_rules_reads_every_fold` | 3 | 1 |
| `conftest_availability_from_a_load` | 1 | 1 |
| `converged_is_stored_beside_the_reason` | 1 | 1 |
| `delta_reverts_to_the_constant_half_point` | 6 | 2 |
| `detect_fold_drops_overlaps` | 3 | 1 |
| `drift_is_checked_against_todays_window_not_the_recorded_one` | 2 | 2 |
| `drop_excluded` | 13 | 11 |
| `familiares_as_other` | 9 | 7 |
| `filled_prompt_exposes_its_text` | 2 | 1 |
| `filled_prompt_paths_allowed` | 4 | 1 |
| `fold_from_directory_not_file` | 2 | 1 |
| `freeze_guard_only_checks_the_file` | 8 | 1 |
| `fully_covered_is_relaxed` | 15 | 1 |
| `generated_accepts_a_bare_date` | 3 | 1 |
| `greedy_allows_reuse` | 10 | 1 |
| `greedy_tiebreak_dropped` | 2 | 1 |
| `grouping_name_only` | 2 | 1 |
| `grouping_numeric_suffix_only` | 1 | 1 |
| `human_log_allowed_under_any_arm` | 1 | 1 |
| `human_log_path_from_a_literal` | 1 | 1 |
| `initial_pool_excludes_train_instead_of_selecting_dev` | 1 | 1 |
| `k_drops_to_one_so_consecutive_means_nothing` | 11 | 3 |
| `layer_family_union_becomes_subset` | 2 | 1 |
| `leak_rate_from_assignment` | 11 | 1 |
| `log_append_disabled` | 2 | 2 |
| `logging_gate_defaults_to_open` | 4 | 2 |
| `missed_is_the_unmatched_gold_rather_than_the_uncovered` | 3 | 3 |
| `missing_test_fold` | 2 | 1 |
| `no_bom_shift` | 154 | 23 |
| `non_target_filter_removed` | 9 | 1 |
| `non_target_types_hardcoded_not_read_from_config` | 1 | 1 |
| `only_the_round_the_report_names_is_checked` | 1 | 1 |
| `only_the_score_is_scoped_to_the_round` | 32 | 1 |
| `only_tracked_modifications_count_as_dirty` | 2 | 2 |
| `overlapping_mask_tags_are_accepted` | 5 | 3 |
| `prompt_tokens_is_what_the_invoice_was_computed_on` | 4 | 3 |
| `render_offsets_are_document_offsets` | 1 | 1 |
| `rendered_window_may_be_redirected` | 2 | 1 |
| `renderer_writes_a_debug_copy` | 1 | 1 |
| `round_one_ignores_the_feedback_it_was_handed` | 4 | 1 |
| `round_one_reassembles_the_baselines_prompt` | 1 | 1 |
| `rule_id_vocabulary_not_checked` | 25 | 1 |
| `rule_source_not_recorded` | 2 | 1 |
| `rule_source_recorded_absolute` | 7 | 1 |
| `run_fold_detects_separately` | 5 | 1 |
| `run_fold_hardcodes_the_absent_value` | 1 | 1 |
| `run_fold_infers_its_own_rule_path` | 1 | 1 |
| `run_fold_omits_the_layer` | 38 | 1 |
| `run_fold_reads_the_sealed_fold` | 2 | 1 |
| `run_fold_skips_axis_validation` | 1 | 1 |
| `run_fold_writes_a_null_model_id` | 49 | 1 |
| `run_fold_writes_unsorted_spans` | 3 | 1 |
| `sample_pool_not_sorted` | 3 | 1 |
| `sample_seed_from_process_hash` | 2 | 1 |
| `sealed_callable_from_anywhere` | 2 | 2 |
| `sealed_exempt_from_exit_code` | 1 | 1 |
| `sealed_flag_not_cleared` | 1 | 1 |
| `sealed_root_falls_back_to_corpus` | 1 | 1 |
| `self_report_defaults_to_none` | 1 | 1 |
| `self_report_refuses_the_violation` | 3 | 1 |
| `spans_file_carries_the_surface` | 40 | 1 |
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
| `the_abandoned_attempt_count_comes_from_the_log_alone` | 1 | 1 |
| `the_abandoned_block_uses_the_cost_block_names` | 19 | 3 |
| `the_abandoned_gate_is_the_draw_count_alone` | 2 | 2 |
| `the_arm_axis_comes_back_off_an_auxiliary_input` | 5 | 4 |
| `the_arm_cell_is_a_constant_again` | 2 | 2 |
| `the_arm_freeze_guard_only_checks_the_file` | 7 | 3 |
| `the_arm_reports_no_model_and_no_cost_to_the_scorer` | 4 | 2 |
| `the_arms_total_is_the_last_rounds_cost` | 3 | 1 |
| `the_assembled_total_is_trusted_rather_than_checked` | 1 | 1 |
| `the_audit_report_gets_a_second_path_key` | 1 | 1 |
| `the_audit_report_is_allowed_instead_of_denied` | 4 | 2 |
| `the_audit_report_is_read_as_the_previous_rounds_file` | 75 | 1 |
| `the_audit_report_records_no_draw_number` | 36 | 3 |
| `the_baseline_draws_error_spans` | 2 | 1 |
| `the_cache_boundary_crosses_onto_the_masked_document` | 2 | 1 |
| `the_call_is_logged_after_the_response_is_judged` | 8 | 1 |
| `the_call_role_is_written_without_being_validated` | 1 | 1 |
| `the_captured_surface_check_reads_only_direct_stream_access` | 1 | 1 |
| `the_captured_surface_control_is_scoped_to_the_function` | 1 | 1 |
| `the_client_hardcodes_botocores_default_attempts` | 2 | 1 |
| `the_conftest_suite_glob_points_one_level_deep` | 5 | 1 |
| `the_declared_rule_file_language_is_trusted` | 2 | 1 |
| `the_export_index_is_the_in_scope_position` | 2 | 1 |
| `the_export_reads_the_missing_index_as_zero` | 1 | 1 |
| `the_failure_record_paraphrases_the_validator` | 3 | 1 |
| `the_final_rounds_duplicate_comes_from_a_second_scoring` | 1 | 1 |
| `the_folds_seconds_go_to_the_round_and_not_the_arm` | 84 | 1 |
| `the_freeze_record_claims_the_sampling_parameters_applied` | 5 | 2 |
| `the_freeze_record_drops_the_empty_block_marking` | 4 | 1 |
| `the_frozen_split_check_ignores_a_moved_document` | 3 | 2 |
| `the_frozen_split_is_verified_after_the_read` | 1 | 1 |
| `the_history_is_pre_seeded_with_this_rounds_rate` | 5 | 3 |
| `the_iteration_allow_pattern_covers_the_whole_directory` | 4 | 1 |
| `the_language_layer_is_a_substring_test` | 3 | 1 |
| `the_language_layer_is_keyed_on_the_id_the_model_wrote` | 3 | 1 |
| `the_lifecycle_block_moves_into_the_run_block` | 2 | 2 |
| `the_lifecycle_probe_can_abort_the_arm` | 2 | 2 |
| `the_logging_check_reports_an_unreadable_setting_as_clean` | 4 | 2 |
| `the_mask_tags_are_emitted_in_the_order_they_were_applied` | 91 | 3 |
| `the_next_draw_counts_the_draws_that_exist` | 2 | 2 |
| `the_parse_error_quotes_the_line_it_choked_on` | 1 | 1 |
| `the_patch_allowlist_stops_requiring_a_reason` | 2 | 2 |
| `the_patch_check_credits_a_bare_function_name` | 1 | 1 |
| `the_patch_check_credits_a_whole_file` | 1 | 1 |
| `the_per_iteration_key_replaces_the_arm_level_one` | 144 | 2 |
| `the_practice_window_may_overlap_iteration_one` | 4 | 1 |
| `the_preserved_draw_is_written_to_the_canonical_path` | 4 | 3 |
| `the_probe_error_carries_the_exception_message` | 3 | 1 |
| `the_provenance_fields_are_optional_again` | 5 | 3 |
| `the_recorded_files_list_is_ignored_in_favour_of_the_fields_present` | 1 | 1 |
| `the_reply_text_is_taken_from_the_first_block` | 92 | 1 |
| `the_report_reads_its_own_round_as_the_masked_one` | 1 | 1 |
| `the_role_is_appended_at_the_end_of_the_line` | 2 | 1 |
| `the_round_s_files_are_written_by_every_arm` | 3 | 2 |
| `the_rule_authors_prompt_is_cached_too` | 2 | 2 |
| `the_score_block_carries_the_run_and_cost_blocks_too` | 1 | 1 |
| `the_suite_glob_points_one_level_deep` | 5 | 1 |
| `the_writer_adds_the_rounds_up_itself` | 11 | 1 |
| `the_writer_calls_the_stopping_rule_itself` | 1 | 1 |
| `top_level_leak_allowed` | 1 | 1 |
| `type_in_both_lists` | 162 | 2 |
| `unsealed_load_filters_instead_of_not_reaching` | 157 | 1 |
| `utf8_sig` | 154 | 23 |
| `zero_minutes_read_as_not_started` | 1 | 1 |

</details>

## 2026-08-28 — full run

- commit `86415c85fae7` (clean working tree)
- 8 shards, 181 of 181 mutations measured
- wall clock **2.05 h** (7393 s)
- tree fingerprint `683e1e584f28ba2b` (all shards agree)
- baseline 1880 tests (all shards agree)

| verdict | n |
|---|---|
| caught | 181 |
| survived | 0 |
| stale | 0 |
| broken | 0 |
| dirty | 0 |

### Kill counts that differ from the previous record

No decreases.

Increases:

- `the_per_iteration_key_replaces_the_arm_level_one` 144 → 146
- `type_in_both_lists` 162 → 164

First measured here (2): `the_arm_and_the_round_are_ignored_off_the_sealed_path`, `the_two_arm_spellings_are_merged_instead_of_refused`

Unchanged: 177.

<details><summary>All measured counts</summary>

| mutation | kills | min_kills |
|---|---|---|
| `a_ceiling_stop_is_recorded_as_converged` | 4 | 2 |
| `a_checksum_accepts_every_match` | 1 | 1 |
| `a_cue_span_swallows_the_cue` | 3 | 1 |
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
| `a_round_other_than_the_last_can_be_scored` | 4 | 2 |
| `a_round_with_no_score_walks_back_to_the_last_one` | 1 | 1 |
| `a_rule_layer_is_derived_from_the_rule_id` | 1 | 1 |
| `a_stale_patch_exemption_is_ignored` | 1 | 1 |
| `a_total_below_its_round_is_published` | 2 | 2 |
| `absent_token_counts_default_to_zero` | 2 | 1 |
| `allowlist_may_name_corpus_paths` | 2 | 1 |
| `an_empty_lifecycle_mapping_is_written_as_no_probe` | 1 | 1 |
| `an_out_of_range_column_is_snapped_to_the_line` | 2 | 2 |
| `an_undefined_rate_prints_as_zero` | 1 | 1 |
| `an_unimplemented_checksum_is_ignored` | 1 | 1 |
| `an_unknown_flag_field_is_ignored_instead_of_refused` | 8 | 1 |
| `an_unknown_language_gets_every_layer` | 1 | 1 |
| `an_unreadable_tree_state_reads_as_clean` | 119 | 3 |
| `arm_rules_path_drops_the_axes` | 81 | 3 |
| `arm_rules_path_drops_the_iteration` | 10 | 2 |
| `arm_rules_path_loses_the_rules_component` | 7 | 1 |
| `arm_started_reads_the_last_line_only` | 1 | 1 |
| `assert_offsets_noop` | 3 | 3 |
| `both_halves_of_the_export_use_one_mode` | 4 | 2 |
| `bucket_unknown_types` | 1 | 1 |
| `by_rule_fp_from_coverage` | 6 | 1 |
| `caching_is_inferred_from_the_prompt_carrying_a_boundary` | 2 | 2 |
| `check_rules_detects_separately` | 1 | 1 |
| `check_rules_reads_every_fold` | 3 | 1 |
| `conftest_availability_from_a_load` | 1 | 1 |
| `converged_is_stored_beside_the_reason` | 1 | 1 |
| `delta_reverts_to_the_constant_half_point` | 6 | 2 |
| `detect_fold_drops_overlaps` | 3 | 1 |
| `drift_is_checked_against_todays_window_not_the_recorded_one` | 2 | 2 |
| `drop_excluded` | 13 | 11 |
| `familiares_as_other` | 9 | 7 |
| `filled_prompt_exposes_its_text` | 2 | 1 |
| `filled_prompt_paths_allowed` | 4 | 1 |
| `fold_from_directory_not_file` | 2 | 1 |
| `freeze_guard_only_checks_the_file` | 8 | 1 |
| `fully_covered_is_relaxed` | 15 | 1 |
| `generated_accepts_a_bare_date` | 3 | 1 |
| `greedy_allows_reuse` | 10 | 1 |
| `greedy_tiebreak_dropped` | 2 | 1 |
| `grouping_name_only` | 2 | 1 |
| `grouping_numeric_suffix_only` | 1 | 1 |
| `human_log_allowed_under_any_arm` | 1 | 1 |
| `human_log_path_from_a_literal` | 1 | 1 |
| `initial_pool_excludes_train_instead_of_selecting_dev` | 1 | 1 |
| `k_drops_to_one_so_consecutive_means_nothing` | 11 | 3 |
| `layer_family_union_becomes_subset` | 2 | 1 |
| `leak_rate_from_assignment` | 11 | 1 |
| `log_append_disabled` | 2 | 2 |
| `logging_gate_defaults_to_open` | 4 | 2 |
| `missed_is_the_unmatched_gold_rather_than_the_uncovered` | 3 | 3 |
| `missing_test_fold` | 2 | 1 |
| `no_bom_shift` | 154 | 23 |
| `non_target_filter_removed` | 9 | 1 |
| `non_target_types_hardcoded_not_read_from_config` | 1 | 1 |
| `only_the_round_the_report_names_is_checked` | 1 | 1 |
| `only_the_score_is_scoped_to_the_round` | 32 | 1 |
| `only_tracked_modifications_count_as_dirty` | 2 | 2 |
| `overlapping_mask_tags_are_accepted` | 5 | 3 |
| `prompt_tokens_is_what_the_invoice_was_computed_on` | 4 | 3 |
| `render_offsets_are_document_offsets` | 1 | 1 |
| `rendered_window_may_be_redirected` | 2 | 1 |
| `renderer_writes_a_debug_copy` | 1 | 1 |
| `round_one_ignores_the_feedback_it_was_handed` | 4 | 1 |
| `round_one_reassembles_the_baselines_prompt` | 1 | 1 |
| `rule_id_vocabulary_not_checked` | 25 | 1 |
| `rule_source_not_recorded` | 2 | 1 |
| `rule_source_recorded_absolute` | 7 | 1 |
| `run_fold_detects_separately` | 5 | 1 |
| `run_fold_hardcodes_the_absent_value` | 1 | 1 |
| `run_fold_infers_its_own_rule_path` | 1 | 1 |
| `run_fold_omits_the_layer` | 38 | 1 |
| `run_fold_reads_the_sealed_fold` | 2 | 1 |
| `run_fold_skips_axis_validation` | 1 | 1 |
| `run_fold_writes_a_null_model_id` | 49 | 1 |
| `run_fold_writes_unsorted_spans` | 3 | 1 |
| `sample_pool_not_sorted` | 3 | 1 |
| `sample_seed_from_process_hash` | 2 | 1 |
| `sealed_callable_from_anywhere` | 2 | 2 |
| `sealed_exempt_from_exit_code` | 1 | 1 |
| `sealed_flag_not_cleared` | 1 | 1 |
| `sealed_root_falls_back_to_corpus` | 1 | 1 |
| `self_report_defaults_to_none` | 1 | 1 |
| `self_report_refuses_the_violation` | 3 | 1 |
| `spans_file_carries_the_surface` | 40 | 1 |
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
| `the_abandoned_attempt_count_comes_from_the_log_alone` | 1 | 1 |
| `the_abandoned_block_uses_the_cost_block_names` | 19 | 3 |
| `the_abandoned_gate_is_the_draw_count_alone` | 2 | 2 |
| `the_arm_and_the_round_are_ignored_off_the_sealed_path` | 1 | 1 |
| `the_arm_axis_comes_back_off_an_auxiliary_input` | 5 | 4 |
| `the_arm_cell_is_a_constant_again` | 2 | 2 |
| `the_arm_freeze_guard_only_checks_the_file` | 7 | 3 |
| `the_arm_reports_no_model_and_no_cost_to_the_scorer` | 4 | 2 |
| `the_arms_total_is_the_last_rounds_cost` | 3 | 1 |
| `the_assembled_total_is_trusted_rather_than_checked` | 1 | 1 |
| `the_audit_report_gets_a_second_path_key` | 1 | 1 |
| `the_audit_report_is_allowed_instead_of_denied` | 4 | 2 |
| `the_audit_report_is_read_as_the_previous_rounds_file` | 75 | 1 |
| `the_audit_report_records_no_draw_number` | 36 | 3 |
| `the_baseline_draws_error_spans` | 2 | 1 |
| `the_cache_boundary_crosses_onto_the_masked_document` | 2 | 1 |
| `the_call_is_logged_after_the_response_is_judged` | 8 | 1 |
| `the_call_role_is_written_without_being_validated` | 1 | 1 |
| `the_captured_surface_check_reads_only_direct_stream_access` | 1 | 1 |
| `the_captured_surface_control_is_scoped_to_the_function` | 1 | 1 |
| `the_client_hardcodes_botocores_default_attempts` | 2 | 1 |
| `the_conftest_suite_glob_points_one_level_deep` | 5 | 1 |
| `the_declared_rule_file_language_is_trusted` | 2 | 1 |
| `the_export_index_is_the_in_scope_position` | 2 | 1 |
| `the_export_reads_the_missing_index_as_zero` | 1 | 1 |
| `the_failure_record_paraphrases_the_validator` | 3 | 1 |
| `the_final_rounds_duplicate_comes_from_a_second_scoring` | 1 | 1 |
| `the_folds_seconds_go_to_the_round_and_not_the_arm` | 84 | 1 |
| `the_freeze_record_claims_the_sampling_parameters_applied` | 5 | 2 |
| `the_freeze_record_drops_the_empty_block_marking` | 4 | 1 |
| `the_frozen_split_check_ignores_a_moved_document` | 3 | 2 |
| `the_frozen_split_is_verified_after_the_read` | 1 | 1 |
| `the_history_is_pre_seeded_with_this_rounds_rate` | 5 | 3 |
| `the_iteration_allow_pattern_covers_the_whole_directory` | 4 | 1 |
| `the_language_layer_is_a_substring_test` | 3 | 1 |
| `the_language_layer_is_keyed_on_the_id_the_model_wrote` | 3 | 1 |
| `the_lifecycle_block_moves_into_the_run_block` | 2 | 2 |
| `the_lifecycle_probe_can_abort_the_arm` | 2 | 2 |
| `the_logging_check_reports_an_unreadable_setting_as_clean` | 4 | 2 |
| `the_mask_tags_are_emitted_in_the_order_they_were_applied` | 91 | 3 |
| `the_next_draw_counts_the_draws_that_exist` | 2 | 2 |
| `the_parse_error_quotes_the_line_it_choked_on` | 1 | 1 |
| `the_patch_allowlist_stops_requiring_a_reason` | 2 | 2 |
| `the_patch_check_credits_a_bare_function_name` | 1 | 1 |
| `the_patch_check_credits_a_whole_file` | 1 | 1 |
| `the_per_iteration_key_replaces_the_arm_level_one` | 146 | 2 |
| `the_practice_window_may_overlap_iteration_one` | 4 | 1 |
| `the_preserved_draw_is_written_to_the_canonical_path` | 4 | 3 |
| `the_probe_error_carries_the_exception_message` | 3 | 1 |
| `the_provenance_fields_are_optional_again` | 5 | 3 |
| `the_recorded_files_list_is_ignored_in_favour_of_the_fields_present` | 1 | 1 |
| `the_reply_text_is_taken_from_the_first_block` | 92 | 1 |
| `the_report_reads_its_own_round_as_the_masked_one` | 1 | 1 |
| `the_role_is_appended_at_the_end_of_the_line` | 2 | 1 |
| `the_round_s_files_are_written_by_every_arm` | 3 | 2 |
| `the_rule_authors_prompt_is_cached_too` | 2 | 2 |
| `the_score_block_carries_the_run_and_cost_blocks_too` | 1 | 1 |
| `the_suite_glob_points_one_level_deep` | 5 | 1 |
| `the_two_arm_spellings_are_merged_instead_of_refused` | 1 | 1 |
| `the_writer_adds_the_rounds_up_itself` | 11 | 1 |
| `the_writer_calls_the_stopping_rule_itself` | 1 | 1 |
| `top_level_leak_allowed` | 1 | 1 |
| `type_in_both_lists` | 164 | 2 |
| `unsealed_load_filters_instead_of_not_reaching` | 157 | 1 |
| `utf8_sig` | 154 | 23 |
| `zero_minutes_read_as_not_started` | 1 | 1 |

</details>

## 2026-09-02 — full run

- commit `031bff3db479` (clean working tree)
- 8 shards, 185 of 185 mutations measured
- wall clock **2.20 h** (7918 s)
- tree fingerprint `976b4f854dcf74ac` (all shards agree)
- baseline 1945 tests (all shards agree)

| verdict | n |
|---|---|
| caught | 185 |
| survived | 0 |
| stale | 0 |
| broken | 0 |
| dirty | 0 |

### Kill counts that differ from the previous record

No decreases.

Increases:

- `an_unreadable_tree_state_reads_as_clean` 119 → 121
- `arm_rules_path_drops_the_axes` 81 → 84
- `drift_is_checked_against_todays_window_not_the_recorded_one` 2 → 3
- `no_bom_shift` 154 → 157
- `the_abandoned_block_uses_the_cost_block_names` 19 → 20
- `the_folds_seconds_go_to_the_round_and_not_the_arm` 84 → 87
- `the_per_iteration_key_replaces_the_arm_level_one` 146 → 164
- `the_reply_text_is_taken_from_the_first_block` 92 → 95
- `type_in_both_lists` 164 → 167
- `unsealed_load_filters_instead_of_not_reaching` 157 → 160
- `utf8_sig` 154 → 157

First measured here (4): `a_lexicon_falls_back_to_the_hand_written_collection`, `the_entry_point_calls_its_own_copy_of_main`, `the_lexicon_collection_is_dropped_on_the_way_down`, `the_tree_state_is_sampled_after_the_run_wrote_to_the_tree`

Unchanged: 170.

<details><summary>All measured counts</summary>

| mutation | kills | min_kills |
|---|---|---|
| `a_ceiling_stop_is_recorded_as_converged` | 4 | 2 |
| `a_checksum_accepts_every_match` | 1 | 1 |
| `a_cue_span_swallows_the_cue` | 3 | 1 |
| `a_dirty_tree_reads_as_clean` | 7 | 3 |
| `a_disagreeing_prefix_still_opens_the_layer` | 1 | 1 |
| `a_duplicate_rule_id_is_allowed` | 1 | 1 |
| `a_flag_overlapping_a_mask_tag_is_kept_when_it_is_not_contained` | 3 | 2 |
| `a_format_failure_writes_zeroed_metrics_too` | 1 | 1 |
| `a_gazetteer_term_is_a_regex` | 2 | 1 |
| `a_gazetteer_term_needs_a_word_character_at_each_edge` | 1 | 1 |
| `a_heterogeneous_union_prints_one_of_its_types` | 4 | 3 |
| `a_later_round_audits_and_samples_round_one` | 2 | 2 |
| `a_lexicon_falls_back_to_the_hand_written_collection` | 2 | 1 |
| `a_lexicon_name_may_traverse_directories` | 1 | 1 |
| `a_mismatched_model_is_recorded_rather_than_refused` | 3 | 2 |
| `a_non_target_type_may_be_a_rule_target` | 1 | 1 |
| `a_null_commit_needs_no_unknown_tree` | 1 | 1 |
| `a_real_arm_may_draw_a_practice_number` | 4 | 1 |
| `a_rehearsal_may_draw_a_real_number` | 5 | 1 |
| `a_round_other_than_the_last_can_be_scored` | 4 | 2 |
| `a_round_with_no_score_walks_back_to_the_last_one` | 1 | 1 |
| `a_rule_layer_is_derived_from_the_rule_id` | 1 | 1 |
| `a_stale_patch_exemption_is_ignored` | 1 | 1 |
| `a_total_below_its_round_is_published` | 2 | 2 |
| `absent_token_counts_default_to_zero` | 2 | 1 |
| `allowlist_may_name_corpus_paths` | 2 | 1 |
| `an_empty_lifecycle_mapping_is_written_as_no_probe` | 1 | 1 |
| `an_out_of_range_column_is_snapped_to_the_line` | 2 | 2 |
| `an_undefined_rate_prints_as_zero` | 1 | 1 |
| `an_unimplemented_checksum_is_ignored` | 1 | 1 |
| `an_unknown_flag_field_is_ignored_instead_of_refused` | 8 | 1 |
| `an_unknown_language_gets_every_layer` | 1 | 1 |
| `an_unreadable_tree_state_reads_as_clean` | 121 | 3 |
| `arm_rules_path_drops_the_axes` | 84 | 3 |
| `arm_rules_path_drops_the_iteration` | 10 | 2 |
| `arm_rules_path_loses_the_rules_component` | 7 | 1 |
| `arm_started_reads_the_last_line_only` | 1 | 1 |
| `assert_offsets_noop` | 3 | 3 |
| `both_halves_of_the_export_use_one_mode` | 4 | 2 |
| `bucket_unknown_types` | 1 | 1 |
| `by_rule_fp_from_coverage` | 6 | 1 |
| `caching_is_inferred_from_the_prompt_carrying_a_boundary` | 2 | 2 |
| `check_rules_detects_separately` | 1 | 1 |
| `check_rules_reads_every_fold` | 3 | 1 |
| `conftest_availability_from_a_load` | 1 | 1 |
| `converged_is_stored_beside_the_reason` | 1 | 1 |
| `delta_reverts_to_the_constant_half_point` | 6 | 2 |
| `detect_fold_drops_overlaps` | 3 | 1 |
| `drift_is_checked_against_todays_window_not_the_recorded_one` | 3 | 2 |
| `drop_excluded` | 13 | 11 |
| `familiares_as_other` | 9 | 7 |
| `filled_prompt_exposes_its_text` | 2 | 1 |
| `filled_prompt_paths_allowed` | 4 | 1 |
| `fold_from_directory_not_file` | 2 | 1 |
| `freeze_guard_only_checks_the_file` | 8 | 1 |
| `fully_covered_is_relaxed` | 15 | 1 |
| `generated_accepts_a_bare_date` | 3 | 1 |
| `greedy_allows_reuse` | 10 | 1 |
| `greedy_tiebreak_dropped` | 2 | 1 |
| `grouping_name_only` | 2 | 1 |
| `grouping_numeric_suffix_only` | 1 | 1 |
| `human_log_allowed_under_any_arm` | 1 | 1 |
| `human_log_path_from_a_literal` | 1 | 1 |
| `initial_pool_excludes_train_instead_of_selecting_dev` | 1 | 1 |
| `k_drops_to_one_so_consecutive_means_nothing` | 11 | 3 |
| `layer_family_union_becomes_subset` | 2 | 1 |
| `leak_rate_from_assignment` | 11 | 1 |
| `log_append_disabled` | 2 | 2 |
| `logging_gate_defaults_to_open` | 4 | 2 |
| `missed_is_the_unmatched_gold_rather_than_the_uncovered` | 3 | 3 |
| `missing_test_fold` | 2 | 1 |
| `no_bom_shift` | 157 | 23 |
| `non_target_filter_removed` | 9 | 1 |
| `non_target_types_hardcoded_not_read_from_config` | 1 | 1 |
| `only_the_round_the_report_names_is_checked` | 1 | 1 |
| `only_the_score_is_scoped_to_the_round` | 32 | 1 |
| `only_tracked_modifications_count_as_dirty` | 2 | 2 |
| `overlapping_mask_tags_are_accepted` | 5 | 3 |
| `prompt_tokens_is_what_the_invoice_was_computed_on` | 4 | 3 |
| `render_offsets_are_document_offsets` | 1 | 1 |
| `rendered_window_may_be_redirected` | 2 | 1 |
| `renderer_writes_a_debug_copy` | 1 | 1 |
| `round_one_ignores_the_feedback_it_was_handed` | 4 | 1 |
| `round_one_reassembles_the_baselines_prompt` | 1 | 1 |
| `rule_id_vocabulary_not_checked` | 25 | 1 |
| `rule_source_not_recorded` | 2 | 1 |
| `rule_source_recorded_absolute` | 7 | 1 |
| `run_fold_detects_separately` | 5 | 1 |
| `run_fold_hardcodes_the_absent_value` | 1 | 1 |
| `run_fold_infers_its_own_rule_path` | 1 | 1 |
| `run_fold_omits_the_layer` | 38 | 1 |
| `run_fold_reads_the_sealed_fold` | 2 | 1 |
| `run_fold_skips_axis_validation` | 1 | 1 |
| `run_fold_writes_a_null_model_id` | 49 | 1 |
| `run_fold_writes_unsorted_spans` | 3 | 1 |
| `sample_pool_not_sorted` | 3 | 1 |
| `sample_seed_from_process_hash` | 2 | 1 |
| `sealed_callable_from_anywhere` | 2 | 2 |
| `sealed_exempt_from_exit_code` | 1 | 1 |
| `sealed_flag_not_cleared` | 1 | 1 |
| `sealed_root_falls_back_to_corpus` | 1 | 1 |
| `self_report_defaults_to_none` | 1 | 1 |
| `self_report_refuses_the_violation` | 3 | 1 |
| `spans_file_carries_the_surface` | 40 | 1 |
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
| `the_abandoned_attempt_count_comes_from_the_log_alone` | 1 | 1 |
| `the_abandoned_block_uses_the_cost_block_names` | 20 | 3 |
| `the_abandoned_gate_is_the_draw_count_alone` | 2 | 2 |
| `the_arm_and_the_round_are_ignored_off_the_sealed_path` | 1 | 1 |
| `the_arm_axis_comes_back_off_an_auxiliary_input` | 5 | 4 |
| `the_arm_cell_is_a_constant_again` | 2 | 2 |
| `the_arm_freeze_guard_only_checks_the_file` | 7 | 3 |
| `the_arm_reports_no_model_and_no_cost_to_the_scorer` | 4 | 2 |
| `the_arms_total_is_the_last_rounds_cost` | 3 | 1 |
| `the_assembled_total_is_trusted_rather_than_checked` | 1 | 1 |
| `the_audit_report_gets_a_second_path_key` | 1 | 1 |
| `the_audit_report_is_allowed_instead_of_denied` | 4 | 2 |
| `the_audit_report_is_read_as_the_previous_rounds_file` | 75 | 1 |
| `the_audit_report_records_no_draw_number` | 36 | 3 |
| `the_baseline_draws_error_spans` | 2 | 1 |
| `the_cache_boundary_crosses_onto_the_masked_document` | 2 | 1 |
| `the_call_is_logged_after_the_response_is_judged` | 8 | 1 |
| `the_call_role_is_written_without_being_validated` | 1 | 1 |
| `the_captured_surface_check_reads_only_direct_stream_access` | 1 | 1 |
| `the_captured_surface_control_is_scoped_to_the_function` | 1 | 1 |
| `the_client_hardcodes_botocores_default_attempts` | 2 | 1 |
| `the_conftest_suite_glob_points_one_level_deep` | 5 | 1 |
| `the_declared_rule_file_language_is_trusted` | 2 | 1 |
| `the_entry_point_calls_its_own_copy_of_main` | 1 | 1 |
| `the_export_index_is_the_in_scope_position` | 2 | 1 |
| `the_export_reads_the_missing_index_as_zero` | 1 | 1 |
| `the_failure_record_paraphrases_the_validator` | 3 | 1 |
| `the_final_rounds_duplicate_comes_from_a_second_scoring` | 1 | 1 |
| `the_folds_seconds_go_to_the_round_and_not_the_arm` | 87 | 1 |
| `the_freeze_record_claims_the_sampling_parameters_applied` | 5 | 2 |
| `the_freeze_record_drops_the_empty_block_marking` | 4 | 1 |
| `the_frozen_split_check_ignores_a_moved_document` | 3 | 2 |
| `the_frozen_split_is_verified_after_the_read` | 1 | 1 |
| `the_history_is_pre_seeded_with_this_rounds_rate` | 5 | 3 |
| `the_iteration_allow_pattern_covers_the_whole_directory` | 4 | 1 |
| `the_language_layer_is_a_substring_test` | 3 | 1 |
| `the_language_layer_is_keyed_on_the_id_the_model_wrote` | 3 | 1 |
| `the_lexicon_collection_is_dropped_on_the_way_down` | 1 | 1 |
| `the_lifecycle_block_moves_into_the_run_block` | 2 | 2 |
| `the_lifecycle_probe_can_abort_the_arm` | 2 | 2 |
| `the_logging_check_reports_an_unreadable_setting_as_clean` | 4 | 2 |
| `the_mask_tags_are_emitted_in_the_order_they_were_applied` | 91 | 3 |
| `the_next_draw_counts_the_draws_that_exist` | 2 | 2 |
| `the_parse_error_quotes_the_line_it_choked_on` | 1 | 1 |
| `the_patch_allowlist_stops_requiring_a_reason` | 2 | 2 |
| `the_patch_check_credits_a_bare_function_name` | 1 | 1 |
| `the_patch_check_credits_a_whole_file` | 1 | 1 |
| `the_per_iteration_key_replaces_the_arm_level_one` | 164 | 2 |
| `the_practice_window_may_overlap_iteration_one` | 4 | 1 |
| `the_preserved_draw_is_written_to_the_canonical_path` | 4 | 3 |
| `the_probe_error_carries_the_exception_message` | 3 | 1 |
| `the_provenance_fields_are_optional_again` | 5 | 3 |
| `the_recorded_files_list_is_ignored_in_favour_of_the_fields_present` | 1 | 1 |
| `the_reply_text_is_taken_from_the_first_block` | 95 | 1 |
| `the_report_reads_its_own_round_as_the_masked_one` | 1 | 1 |
| `the_role_is_appended_at_the_end_of_the_line` | 2 | 1 |
| `the_round_s_files_are_written_by_every_arm` | 3 | 2 |
| `the_rule_authors_prompt_is_cached_too` | 2 | 2 |
| `the_score_block_carries_the_run_and_cost_blocks_too` | 1 | 1 |
| `the_suite_glob_points_one_level_deep` | 5 | 1 |
| `the_tree_state_is_sampled_after_the_run_wrote_to_the_tree` | 1 | 1 |
| `the_two_arm_spellings_are_merged_instead_of_refused` | 1 | 1 |
| `the_writer_adds_the_rounds_up_itself` | 11 | 1 |
| `the_writer_calls_the_stopping_rule_itself` | 1 | 1 |
| `top_level_leak_allowed` | 1 | 1 |
| `type_in_both_lists` | 167 | 2 |
| `unsealed_load_filters_instead_of_not_reaching` | 160 | 1 |
| `utf8_sig` | 157 | 23 |
| `zero_minutes_read_as_not_started` | 1 | 1 |

</details>
