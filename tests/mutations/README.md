# Loader mutation checks

A test suite that always passes proves nothing. These checks break one loader
guarantee at a time and confirm the suite notices — so the claim "the loader is
tested" rests on evidence rather than on test count.

```
python3 tests/mutations/run.py            # all mutations
python3 tests/mutations/run.py --list     # names and expected kills
python3 tests/mutations/run.py utf8_sig   # one mutation
```

Exit 0 when every mutation is caught by at least its expected number of tests.
Not collected by pytest: it runs pytest itself, and each mutation costs a full
suite run. The harness's own logic — the checks that a mutation was really applied —
is tested by `tests/test_mutation_harness.py`, which pytest does collect.

## Why these particular mutations

Each one corresponds to a decision in DESIGN.md that a plausible "simplification"
would silently undo. What they have in common is the property that makes them
dangerous: **thirty-eight of the forty-seven change no total.** The corpus still loads,
the document count is still 750, the span count is still 17,134 — and every
downstream number is wrong. Those are the errors a reviewer cannot catch and an
aggregate cannot reveal, which is why they get a harness rather than trust.

The seal mutations are the sharpest case of that. A broken seal produces no wrong
number at all: the figures are real, they are simply computed on data that was
supposed to be untouched, and nothing in the output distinguishes them from
legitimate ones. There is no aggregate to check and no way to undo it after the
fact, so a harness is the only place the guarantee can live.

## The loader mutations

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `utf8_sig` | `meddocan.py` reads the text with `encoding="utf-8-sig"` | BOM removed at decode time, so `strip_bom` finds nothing and applies no shift; all 761 spans in the 32 BOM files are off by one. DESIGN §9.7 | **35** |
| `no_bom_shift` | offsets are not decremented by the BOM length | same one-character error, reached from the other direction | **35** |
| `assert_offsets_noop` | `Document.assert_offsets` returns immediately | the §9.7 assertion stops asserting; counts are unaffected, so only tests that slice spans themselves can notice | **3** |
| `drop_excluded` | `load()` filters out `excluded` spans | §9.1 spans discarded instead of flagged; the canonical count stays a correct 20,538 while the reported exclusion volume becomes unmeasurable | **11** |
| `familiares_as_other` | `FAMILIARES_SUJETO_ASISTENCIA` moves from `EXCLUDED_TYPES` into `TYPE_MAP` as `OTHER` | an excluded type is scored; every span still loads and the total still reconciles to 22,795, so the corruption is entirely in *which* spans count | **7** |
| `type_in_both_lists` | the same type is added to `TYPE_MAP` while left in `EXCLUDED_TYPES` | `_check_type_map` must reject it at construction. See "What this found", below | **43** |
| `missing_test_fold` | `SPLIT_DIRS` loses its `test` entry | before the seal: 750 documents loaded instead of 1,000. Now 750 is correct, so what remains visible is that an *authorised* sealed read would return no sealed documents while the log records a completed evaluation | **1** |
| `bucket_unknown_types` | `classify()` returns `("OTHER", False)` instead of raising | an unmapped type is scored as a residual bucket. Invisible on today's corpus and waiting for the day a re-release adds a type | **1** |

## The split-file mutations

`splits/es-meddocan.json` is the seal's reference point (CLAUDE.md), so the checks
around it get the same treatment. These eight are what make the file a claim rather
than a comment — every one of them leaves all 22,795 spans loading correctly.

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `split_verify_noop` | `split.verify()` returns immediately | the recorded summaries stop being compared to the corpus, so a stale split file passes | **2** |
| `split_ignores_membership` | `verify()` checks the counts but not `document_ids` | a file that swapped one dev document for one test document of equal span count would verify. This is the seal violation the aggregates cannot see | **1** |
| `fold_from_directory_not_file` | `load()` skips `_apply_split_file` | folds come from the directory layout instead of the frozen file. No count changes, because the two agree today; what is lost is that the *file* decides what is sealed | **2** |
| `split_disagreement_ignored` | the corpus-vs-file fold cross-check becomes `if False` | the file silently overrides the disk, so a re-release that moved a document out of `test` is accepted without a word | **1** |
| `top_level_leak_allowed` | `check_schema` stops rejecting unknown top-level keys | corpus-specific fields may then sit beside the common ones. Nothing fails today; the schema stops being shared the first time GraSCCo's generator adds a key | **1** |
| `grouping_numeric_suffix_only` | `STEM_RE` becomes the old `^(S\d{4}-\d+)-(\d+)$` | reinstates the §9.5 bug that dropped the 31 ids with a letter in the journal prefix, so the grouping audit covers 969 of 1,000 documents and calls itself complete | **1** |
| `grouping_name_only` | §9.5 step 2 accepts a name match without a record number or date | the one stem sharing a bare given name across different surnames becomes a group, and two independent units stop being independent | **1** |
| `split_file_span_count` | `"n_spans": 5801` → `5800` **in the committed JSON** | a stale summary — which is what a re-released corpus actually produces. Direction reversed from every other mutation here: the artefact is the suspect and the code is the check | **3** |

## The seal mutations

The test fold is behind `sealed/` and reachable only through
`src/eval/run_sealed_eval.py` (DESIGN §6). These five are what make that a
guarantee. The first two are the pair the seal's design rests on, and they are
listed together because neither guard is sufficient alone:

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `sealed_callable_from_anywhere` | the `SEALED_CALLER` check becomes `if False` | `load(sealed=True)` works from any module — a notebook, a rule-development script. **The log append survives**, so a bypass here still leaves a trace, which is what makes it recoverable rather than merely wrong | **2** |
| `log_append_disabled` | the `record_access` call is wrapped in `except Exception: pass` | an evaluation proceeds unlogged. The numbers are real and the log says the test fold was never opened. **The caller check survives**, so this needs the allowed script — the counterpart of the mutation above, and the one that leaves nothing behind | **2** |
| `sealed_flag_not_cleared` | `_sealed_ok` is not reset after the read | one authorised evaluation leaves that loader object permanently able to reach the sealed fold; every later ordinary `load()` silently includes 250 test documents, with no second log row | **1** |
| `sealed_root_falls_back_to_corpus` | an absent `sealed:` entry resolves to the corpus root | a "sealed evaluation" reads unsealed data and logs itself as a test run. Worse than a refusal: the row is indistinguishable from a real evaluation, so the reported count becomes wrong in the flattering direction | **1** |
| `unsealed_load_filters_instead_of_not_reaching` | `fold_roots()` hands out the sealed path unconditionally | the sealed fold is read and then discarded downstream. Every count still comes out right; the test fold's text has been read on every ordinary load, unlogged. Defends the distinction that the seal is a path that is not known, not a filter that is applied | **38** |

## The release screener mutations

The screener is the seal's other half: the gate stops the fold being *read*, and
`tools/release_screen.py` is what stops it being *published*. It reports `sealed/`
on its own `SEALED` line — expected, exit 0 — which introduces one new way to be
wrong, so that way gets a mutation.

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `staged_sealed_not_escalated` | sealed paths are excluded from `blocked` | the plausible misreading of "SEALED is expected, so it should not block a commit". What is expected is a sealed fold git *cannot* see; a staged one lands in neither list, the screener exits 0, and the fold goes into a public commit with the output saying nothing | **2** |
| `sealed_exempt_from_exit_code` | `if blocked or suspect` becomes `if suspect` | the exit status stops depending on BLOCKED. Its own mutation because the SEALED change moved exactly this line's meaning — SEALED must not affect the exit code and BLOCKED must, and one edit could get the first half right and the second half wrong | **1** |
| `allowlist_may_name_corpus_paths` | `load_allowlist` stops refusing entries under `data/` and `sealed/` | the allowlist's one hard limit. `deny(p)` below still covers most corpus paths, so the edit looks harmless until `data/README.md` — the single file published out of a denied prefix, and therefore not denied. With this, a four-line JSON entry silences the content sniffer on a file inside the corpus tree | **1** |
| `filled_prompt_paths_allowed` | the `prompts/(filled|rendered)/` deny pattern stops matching | a filled RuleAuthor prompt at `prompts/filled/iter03.md` — carrying the ±120-character context of every sampled dev error — reads as an ordinary file under `prompts/`, an ALLOW_HINTS prefix. Not merely unblocked: reported clean | **3** |
| `rule_id_vocabulary_not_checked` | the mechanism-vocabulary check in `rule_id_findings` is removed, leaving the shape rules | the screener returns to its first version, which passes every legitimate name and also passes `es:perez_ruiz` — a surname published through `metrics.json`'s `by_rule` block, which is on the *allow* list | **8** |

`allowlist_may_name_corpus_paths` is the same shape as the two rows above it: a mechanism added to *reduce*
noise, mutated at the point where reducing noise turns into suppressing the signal.
The allowlist exists because five permanent SUSPECT lines meant a sixth would arrive
unread; what it must never become is a way to make a real hit permanent too. The
invariant is directional — an entry can only excuse a file the path rules already
publish, never widen what they publish — and it is a JSON edit away from being
violated by someone who never reads this file, which is why it is enforced in code.

Its count of **1** is worth reading carefully, because the test that catches it is
parametrized over five corpus paths and only one of the five fails. That is not thin
coverage of the guarantee; it is the guarantee having exactly one uncovered path. Four
of those five are refused twice over — by the `data/`-and-`sealed/` check and by
`deny()` — so removing the first leaves them refused and the assertion passes.
`data/README.md` is the only path the second check does not cover, which is precisely
why the rule extends past `deny()` at all. A single test failing here means the
mutation is only visible where it is actually dangerous.

The last two rows are not about the sealed fold at all — they are the screener's other
job, which is stopping dev text and corpus surface forms from reaching a public file by
a route nobody thought of as a leak.

`filled_prompt_paths_allowed` is worth a row of its own even though three sibling
patterns survive it, because the failure is not "the file gets committed". The
convention for a filled RuleAuthor prompt is *never written to disk*
(`docs/prompts/rule_author.md` §6), so the screener has to object to the file existing.
That is why these four patterns are deliberately **not** in `.gitignore`: an ignored
path is reported as Quarantined — expected, one summary line, exit 0 — which is the
right treatment for a downloaded corpus and exactly the wrong treatment here. The
opposite call from `sealed/`, and for a reason that does not generalise: the sealed fold
must be on disk and this must not. `test_the_filled_prompt_patterns_are_not_gitignored`
exists so that adding them, which looks like an improvement, fails instead.

`rule_id_vocabulary_not_checked` is the one mutation here that reverts the check to a
version that was actually written and shipped in a draft. The shape-only screener passed
all ten legitimate mechanism names and four real violations, `es:perez_ruiz` among them,
and the reason is not a missing pattern: `perez_ruiz` and `street_type` are both two
lowercase ASCII tokens with no digits, so **no property of the string separates them**.
The fix could not be a blacklist either, since listing the names to reject means storing
surface forms in the repository — the thing being prevented. What works is the inversion:
a positive mechanism vocabulary, because a name assembled only from mechanism words
*cannot* designate an individual. The mutation is the argument for that design, since it
demonstrates that the natural alternative fails silently and in the direction that
publishes a surname through `metrics.json`.

`git_tracked()` is not mutated, and that is worth stating rather than leaving as a
gap in the table. It asks the index directly whether a denied path is staged or
tracked. On git 2.54 `check-ignore` consults the index too, so a force-added file is
already reported as visible without it and every mutation of `git_tracked` survives —
the function is redundancy, not a load-bearing check. It is kept because the
escalation it guarantees is too consequential to rest on the behaviour of one command
on one version, and it is listed here as untested-because-redundant rather than
quietly omitted.

## The vocabulary mutations

`config/naming.yaml` is the single definition site for identifiers, and a check that
the config and the code agree is only worth having if it cannot be weakened into a
check that agrees with anything.

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `layer_family_union_becomes_subset` | `layer_families()` compares `set(assigned) - layers` twice instead of once in each direction | the union check becomes a subset check — the plausible reading of "make sure every declared member is a real layer", which sounds like the whole job and is half of it. A layer added to the `layer` axis and left out of every family then validates, and every span it emits is counted as `neither` in the complementarity breakdown: indistinguishable from spans that genuinely nothing found | **2** |

This one belongs in the same family as `bucket_unknown_types` and
`split_disagreement_ignored`: a guard whose weakened form is *more* permissive in a
way that produces no error, only a wrong number in the flattering direction. An
unfamilied layer's detections vanish into `neither`, which reads as evidence that a
mechanism found nothing — the opposite of the truth — while every total still
reconciles. DESIGN §3 records why the validation lives in `src/corpora/base.py` rather
than in the scorer that first consumes it.

## The scorer mutations

The scorer is where every other guarantee is finally cashed in: a correct loader, a
sealed test fold and a validated vocabulary all exist to make these numbers mean
something. It is also the one component whose output nobody can sanity-check by
inspection — a leak rate of 3.1% looks exactly as reasonable as a wrong one.

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `greedy_allows_reuse` | `assign()` drops `pi in used` from the skip condition | the matching stops being one-to-one, so one wide prediction collects credit for several gold spans. Recall rises for emitting one coarse span instead of two correct ones — the detector is paid for being vaguer about boundaries | **8** |
| `fully_covered_is_relaxed` | `_covers()` tests `> 0` instead of `== mark.length` under `fully_covered` | the headline mode collapses into the lower bound while keeping its name; a gold span with one character covered counts as hidden | **10** |
| `leak_rate_from_assignment` | the `leak.leaked` figure is taken from the assignment's false negatives | the error DESIGN §9.3 exists to prevent: a leak reported on an identifier whose every character is hidden | **9** |
| `greedy_tiebreak_dropped` | the sort key becomes `(-overlap, pi, gi)` | ties fall through to emission order, so the metrics move when the same spans arrive shuffled | **1** |
| `by_rule_fp_from_coverage` | `by_rule`'s hits are taken from type-matched overlap instead of from the assignment | a rule whose spans always lose the assignment to a better one reads as harmless, and the only signal that licenses deleting a rule disappears while every aggregate stays correct | **5** |

The first three are ordinary wrong-number mutations and the counts are comfortable.
`greedy_tiebreak_dropped` is the interesting one, and it is caught by exactly one
test — necessarily so. A tie-break that reads the emission index is still a *total*
order over the candidate list, so every individual run is self-consistent, internally
plausible and reproducible when repeated. The only way to see the fault is to score
one input twice with the spans in different orders and compare the whole result, which
is one test by construction: `test_scoring_is_order_independent`. No fixture, however
carefully designed, can catch it in a single run, and a table row of **1** is the
honest way to say that the guarantee has a single point of failure rather than to pad
it.

`leak_rate_from_assignment` is the mutation this project's central design argument is
about. Collapsing the two matchings into one is not a careless edit — it is the
obvious simplification, it removes a whole code path, and the resulting leak rate is
in the plausible range. What it destroys is the distinction between *is this
identifier hidden* and *does the detector get credit for it*, and the damage is
one-directional: it over-reports leaks wherever the detector groups span boundaries
differently from the gold annotation guideline. A project reporting a leak rate as its
headline would be publishing a number biased against itself for reasons no reader
could reconstruct. The gap has its own name in the output — `assignment_slack` — so
that it is a reported quantity instead of an unexplained discrepancy between the leak
rate and recall.

`by_rule_fp_from_coverage` is the same argument one level down, and it is worth stating
separately because the coverage basis is *more* natural here than it was there. A rule's
span that overlaps a gold identifier did help hide it, and calling that a hit sounds
generous rather than wrong. But per-rule attribution exists so the RuleAuthor can
*delete* a rule (DESIGN §9.3, `docs/prompts/rule_author.md` §1.3), and the rule worth
deleting is precisely the one whose spans are always beaten to the credit by a better
prediction. Under the coverage basis that rule shows a healthy hit count, the file grows
monotonically, and no aggregate in `metrics.json` is wrong — the arm simply never
shrinks its rule set and nobody can say why.

Counts are the number of tests that fail or error, from
`tests/test_meddocan_loader.py`, `tests/test_split_file.py`, `tests/test_seal.py`,
`tests/test_release_screen.py`, `tests/test_layer_families.py`,
`tests/test_scorer.py`, `tests/test_sample.py`, `tests/test_human_arm.py` and
`tests/test_show_human_window.py` (413 tests). Errors
count as kills: a mutation that breaks the module-scoped fixture takes whole tests
out, and those are caught, not uncounted.

`bucket_unknown_types` is caught by exactly one test, which is the honest number
and not a comfortable one — the guarantee has a single point of failure. It is
listed at 1 rather than padded, because the value of this table is that it says
where the coverage is thin. Seven of the split-file mutations are in the same
position. `non_target_types_hardcoded_not_read_from_config` is also at 1, but for a
different reason: there is only one test that *could* catch it, because the defect
concerns a config value that does not exist yet.

The loader counts rose when the split tests joined the run (23→34, 11→12, 7→8): a
corrupted load fails the recount too. The `min_kills` thresholds were left at the
original figures rather than raised to match. The threshold states what the check
requires; pinning it to today's incidental total would turn every unrelated new test
into a reason to edit this file.

**One threshold was lowered, and that needs saying out loud.** `missing_test_fold`
went from 16 to 1 when the test fold was sealed. The coverage did not decay: 750
documents became the correct figure, so the count-based tests can no longer see a
missing test fold, and the fault is now visible only in the sealed-read path. The
alternative — adding a test that recounts 1,000 documents — would mean reading the
sealed fold from the suite, which is the thing being protected. A lowered threshold
with the reason recorded is honest; the same number preserved by a test that
breaches the seal would not be.

## The sampling mutations

`src/sample.py` decides which dev errors a rule author is shown at a given iteration,
and both porting arms call it. DESIGN §11.1 makes `port-human` interpretable only on the
premise that **the two arms drew by the same procedure at the same iteration** — the
error pools differ, since each arm draws from its own current errors, and that difference
is the experiment. If the procedure also differs, no analysis afterwards separates the
two.

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `sample_seed_from_process_hash` | `sample_seed()` returns `abs(hash(material))` instead of SHA-256 | Python salts string hashing per process, so the seed is stable within a run and different in the next. The recorded seed documents a draw nobody can repeat | **2** |
| `sample_pool_not_sorted` | the error pool keeps the caller's iteration order instead of `sorted(..., key=e.key)` | the seed pins which *indices* are drawn and the caller's ordering pins which spans those indices hit — reproducible from the log, different in fact | **2** |
| `non_target_filter_removed` | `non_target_types()` returns an empty set | `OTHER` takes a slot, and with `min_per_type: 1` it takes one in *every* iteration of *every* arm — a permanent slot handed to a type Prohibition 4 forbids writing a rule for | **7** |
| `non_target_types_hardcoded_not_read_from_config` | `non_target_types()` returns the literal `{"OTHER"}` instead of reading naming.yaml's gloss | nothing, today. The day a corpus ships a second residual bucket, that type is declared, glossed as non-target, forbidden — and drawn anyway, with config and code each looking correct alone | **1** |

All three are in the same class and it is a class this table has met before: a wrong
number in the flattering direction with no symptom. What is new is *where* the symptom
is absent. The first two are invisible to the obvious test.

`sample_seed_from_process_hash` passes every in-process check of determinism. Call the
function twice: equal. Call it after `random.seed(999)`: equal. Compare two arms at one
iteration: equal. Write the sample and the seed to the results and re-derive the seed
from the record: equal. Every one of those is a test someone would write, and the
mutation survives all of them, because within a single interpreter `hash()` **is**
deterministic. Only a fresh process sees it, which is why
`test_the_seed_is_stable_across_processes` spawns one rather than calling the function
again.

Its count of **2** is the honest reading of that. Nineteen tests in `test_sample.py`
exercise the seed and the draw; two of them survive a fresh interpreter, and those two
are the entire coverage of this guarantee. The other seventeen are not redundant — they
check stratification, size, and ordering — but not one of them can see a per-process
seed, and a table row of 19 would be describing a suite that does not exist.

`sample_pool_not_sorted` is the sharper of the two, because the artifact it corrupts is
the record that would be used to detect the corruption. A sampler that draws from an
unordered pool still writes a seed to `metrics.json`, and that seed is correct: it is
what the RNG was given. The indices drawn from it are correct too. What moves is which
span each index lands on — so rebuilding a dict, changing how the scorer accumulates its
errors, or upgrading Python reshuffles the sample while every recorded quantity stays
identical. Reproducible in the log and different in fact is a worse position than
plainly irreproducible, since the log invites the reader to trust it.

Neither of those two touches sample size, type coverage, or stratification, so the
sample still looks entirely correct: 40 spans, every type present, the sparse type
included.

The last two are a pair, and the split is deliberate: one restores a defect that
actually shipped, the other restores a defect that has not happened yet and would not
announce itself when it does. The incident is recorded in its own section below, with the
two it belongs beside.

`non_target_filter_removed` is the shipped one. `min_per_type` guarantees the slot, so
this does not decay into an occasional nuisance — it is one wasted slot in every window
of the experiment, and the author who acts on it anyway is breaking a prohibition the
sample itself put in front of them.

`non_target_types_hardcoded_not_read_from_config` is the interesting half, because
**applying it breaks nothing at all.** `OTHER` is the only non-target type in
naming.yaml today, so `frozenset({"OTHER"})` and the real implementation return the same
value on the same config, and the literal is the shorter and more obvious-looking line —
the kind of edit a reviewer waves through and a future contributor writes from scratch.
The defect is latent and it surfaces silently: when a corpus ships a second residual
bucket, that type is declared in naming.yaml, glossed as not a rule-development target,
forbidden by Prohibition 4, and drawn into every window anyway, with the config and the
code each looking correct when read alone. Nothing in the sample looks wrong, for the
same reason nothing looked wrong the first time.

This is the mutation that pins *why* the exclusion is derived rather than declared
twice. Only one test catches it — `test_a_second_non_target_type_would_also_be_excluded`,
which patches the axis to declare a second such type — and no property of today's
sample can. That is the honest coverage of a guarantee about a config that does not exist
yet.

## The port-human mutations

`src/porting/human_arm.py` runs the control arm: freeze the window, draw, render for the
person, append to `human_log.jsonl`. Its guarantees are of a different kind from the
sampler's. Most of them are not about numbers at all — one is the seal, one is what may
be said out loud about a sample, and two are about a clause that binds a person and has
no enforcement but a field in a log.

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `initial_pool_excludes_train_instead_of_selecting_dev` | `split != "dev"` becomes `split == "train"` | the test fold enters the window a person writes rules from — the seal violation that invalidates the experiment, arriving as a plausible spelling of the same filter | **1** |
| `summary_reports_offsets` | `summarise()` gains a `spans` field of `(doc_id, start, end)` | the view built to be pasted into a terminal or a commit message starts carrying pointers into the corpus. No surface form is quoted, which is why it would survive review | **1** |
| `render_offsets_are_document_offsets` | the rendered window labels document offsets as within-context offsets | an author counting characters lands on the wrong span, and one trusting the number is handed a document coordinate — an invitation to read past the ±120 characters | **1** |
| `human_log_path_from_a_literal` | the arm rebuilds its output paths instead of reading `paths.humanlog` from naming.yaml | two authorities on where this arm writes, identical today; the day the config moves, results are written to one path and read from another | **1** |
| `human_log_allowed_under_any_arm` | the screener's allowed path for `human_log.jsonl` takes `[^/]+` for the porting component instead of the literal `port-human` | a log under any other arm is counted as reviewed, and nothing writes one there — its presence is the signal, and the wildcard is what hides it | **1** |
| `self_report_defaults_to_none` | `model_consulted` acquires a default of `"none"` | every caller keeps working and every line says no model was consulted — recording that nobody was *asked*, not that nobody consulted a model. A default on this parameter is a default for `rule_author.md` §8 | **1** |
| `self_report_refuses_the_violation` | `log_line()` raises on `model_consulted="rule_content"` | looks like enforcement, removes the report. §8 binds a person and cannot be enforced by code; all a refusal deletes is the record, after which every log attests to a clean run by construction | **3** |
| `rendered_window_may_be_redirected` | `show_human_window.py`'s `stdout.isatty()` check becomes `if False` | `> window.txt` succeeds and a DUA corpus's ±120-character contexts are on disk — the file §6 says must not exist. The author sees the same window, the script exits 0, and the leak is a file nobody opens again | **2** |
| `freeze_guard_only_checks_the_file` | the `arm_has_started()` condition in `freeze_window()` becomes `if False` | restores the hole this repository fell through three times: `rm window_freeze.json` then re-freeze, which `path.exists()` cannot see. The new record hashes today's files and claims to be the opening window | **4** |
| `arm_started_reads_the_last_line_only` | `arm_has_started()` inspects the final log line instead of every line | appending any null-minutes event re-opens the freeze, and appending is what this arm does constantly. A `read_sample` at iteration 7 makes six iterations of attention re-writable | **1** |
| `zero_minutes_read_as_not_started` | `is not None` becomes a truthiness test | a logged `human_minutes: 0` reads as "nothing happened", though `log_line()` validates the field to accept 0 because an event can take under a minute | **1** |

`initial_pool_excludes_train_instead_of_selecting_dev` is the one to read twice. The two
filters are the same length, the same shape, and equivalent on any corpus with two
folds; they differ only on the corpus this project actually has. It leaves no trace: the
sample is the right size, the provenance record is correct, and a test-fold span is
indistinguishable from a dev one in a summary that reports counts by type. In this
repository the sealed loader refuses first, so the mutation is caught — but that is the
seal defending the harness rather than the harness defending the seal, and the harness
must not be written as though the layer beneath it will always be there.

`summary_reports_offsets` is the inverse of the usual failure mode here: it makes the
output *more* informative and that is the defect. `summarise()` exists to be quotable —
its whole contract is that its result can go into a commit message or be read aloud —
and the same `(doc_id, offset)` pairs it would gain are entirely correct on a
`human_log.jsonl` line, because that file's reader already holds the corpus. The
distinction is the audience, not the data, so nothing about the added field looks wrong
in isolation.

`human_log_path_from_a_literal` is the odd one in this table because **it breaks nothing
at the moment it is applied.** The literal it introduces is character-for-character what
naming.yaml holds, so every path assertion still passes and every file still lands where
it should. What it removes is the property that there is one authority on where this arm
writes — and the test that catches it has to be built accordingly. Asserting the path
string cannot work: the mutation produces that string. What works is redirecting the
config and checking that the paths follow, which is why
`test_the_paths_follow_the_config_rather_than_a_copy_of_it` monkeypatches
`path_template` rather than comparing to an expected path. DESIGN §11.2 asks for
`paths.humanlog` in the config by name; this is what makes that requirement checkable
instead of aspirational.

`human_log_allowed_under_any_arm` is on the screener rather than the arm, and it is
here because the ALLOW list is the one place in this repository where being *more*
permissive costs nothing visible. A wildcard where the literal `port-human` belongs makes
the summary read identically — the same file counted in `Explicitly allowed`, the same
`BLOCKED: 0` — while a `human_log.jsonl` appearing under `port-loop` goes from a question
to a checkmark. Nothing writes that file there, which is the whole point: the file's
*location* is the finding, so a pattern that stops distinguishing locations stops being a
check. Its companion test also pins the other half, that being on the ALLOW list says
nothing about content: a log line whose free-text `decision` field holds note text is
still SUSPECT, since `decision` is written by a person and is exactly where a surface
form gets pasted.

The last two are the §8 pair, and they are here because §8 is the one guarantee in
this repository that **code cannot enforce at all.** The clause forbids asking a language
model what a rule should be during a `port-human` iteration; nothing in a rule file
distinguishes a pattern its author designed from one they transcribed, and no test can
tell them apart. What is implementable is the report — a required `model_consulted` on
every line — so these two mutations are about the report's two failure modes, and both
produce a log that *looks* clean.

`self_report_defaults_to_none` is the quieter one. Adding `= "none"` is the change a
contributor makes to stop a positional argument being annoying, every existing caller
keeps working, and the field then answers a different question than it was written for:
not "did the author consult a model" but "did the caller pass anything". The exculpating
value is the natural default, which is the whole difficulty — a default of
`"rule_content"` would be caught in an afternoon by everyone it inconvenienced.

`self_report_refuses_the_violation` is the one worth reading twice, because it is the
mutation that *reads as a fix*. Raising on `rule_content` looks like the harness taking
the clause seriously, and a reviewer who has just read §8 would be inclined to approve
it. What it actually removes is the only evidence anybody will ever have. The clause
still binds nothing, so the violation still happens; it simply cannot be written down,
and every `human_log.jsonl` in the experiment then reports a clean run because a dirty
one was unrepresentable. That is the loader-fixture `skip` in a new place — a mechanism
that turns "this did not happen" and "this could not be recorded" into the same output
— and it is why §8.2 states that `rule_content` is a value the field can hold rather
than an error the harness refuses.

**The freeze trio is the one place in this table where a mutation restores a defect
that shipped and was documented as prevented.** `freeze_window()`'s docstring said it
"refuses to overwrite an existing record", the reasoning in that docstring was correct,
and the implementation checked `freeze_path().exists()` — which a preceding `rm` makes
`False`. The window was re-frozen three times before iteration 1 by exactly that
sequence, each time reported honestly as a re-freeze and each time entirely outside the
guard. `docs/notes/window-freeze-history.md` records the three values and the fact that
no rule and no `human_minutes` existed at any of them, which is why the arm survives.

`freeze_guard_only_checks_the_file` restores it, and the general form is worth having in
this file: **a refusal conditioned on the presence of the thing being protected is not a
refusal.** It is a request addressed to whoever is in a position to remove the evidence.
The fix is that the second condition reads the *append-only log* — a non-null
`human_minutes` on any line — which `rm window_freeze.json` cannot reach. That is also
what makes the mutation catchable: four tests fail, where the original guard had no test
that deleted anything.

`arm_started_reads_the_last_line_only` is the subtler one and it is the reason the guard
loops rather than tailing. Reading the final line looks like a cheap optimisation, agrees
with the real implementation whenever the last line happens to carry minutes, and quietly
makes an append-only file's central property — that the evidence stays in it — into a
property of whichever line came last. Since this arm appends constantly, a `read_sample`
event at the start of iteration 7 would re-open a freeze that six iterations of a person's
attention had fixed.

`zero_minutes_read_as_not_started` is one character. `log_line()` accepts
`human_minutes: 0` deliberately, because an event can take under a minute, so a
truthiness test throws away the distinction between a measurement of zero and the absence
of a measurement — in the direction that leaves the freeze writable.

`rendered_window_may_be_redirected` is on the hand-off script rather than the module,
and it is the only mutation here whose consequence is a **file that should not exist**
rather than a wrong number. `tools/show_human_window.py` is the one place in this
repository that puts corpus text on a screen, so its guarantee is about where the text
may not go: not to disk, not through a pipe, not into a transcript. Removing the check
leaves every visible thing identical — same window, same exit code — and differs only in
that `window.txt` is now sitting in the working tree. `release_screen.py` would have to
catch it by content sniff at commit time, which is the layer this check exists so as not
to depend on, and a scrollback capture or a terminal log is outside the screener
altogether.

Worth noting where the check sits: before the corpus is loaded. A refusal that ran after
the text was in memory would be one exception away from having rendered it, and the test
that catches this mutation is deliberately not marked as needing the corpus for the same
reason.

Five of these are caught by exactly one test, which is the honest number and a thin
one; they are listed at 1 rather than padded.

## What the seal cost, and what carries the difference

Sealing 250 documents removed checks that cannot be replaced, and pretending
otherwise would be the more dangerous outcome:

- **The recount stops at the fold boundary.** `split.verify()` recounts train and
  dev; the sealed fold's recorded summaries are carried by `reconcile_totals()`
  (folds must sum to `totals`) and by the freeze commit. That is weaker than a
  recount and not weak: `totals` and the fold blocks are written independently, so
  corrupting the sealed figures now requires two consistent edits — no longer a
  stale file but a forged one, which no check inside the file can detect.
- **One §9.5 case survived only by being restated.** The single MEDDOCAN stem that
  shares a name surface and nothing else straddles the split, so `grouping_name_only`
  lost its only killing test when the audit recount was narrowed to wholly-unsealed
  stems. The rule was extracted into `step_2_confirms(shared: dict[str, int])` so it
  can be re-applied to the counts the split file already records — which cover all
  48 stems, sealed or not. Taking counts rather than documents is what keeps the
  discriminating case checkable, and is worth preserving under refactoring.
- **`--check` says what it did not check.** `python3 -m src.split --check` prints
  the folds it recounted and names the sealed one it did not. A check that covered
  two thirds of the corpus and printed `ok` would be read as covering all of it.

## Code, not just prose

The mutations are executable. The alternative — a README describing seven edits
and the counts a past run produced — was rejected for one reason: **a documented
mutation is not a check, it is a claim about a check.** Anchors drift as the
loader is refactored, and prose cannot notice when a described edit no longer
applies to the code. Two safeguards follow from that:

- **Stale anchors are reported, not skipped.** Every mutation names text that
  must exist in the target file. If a refactor removes it, the run reports
  `STALE` and exits non-zero. Without this the harness degrades into a file of
  no-ops that reports every mutation as caught — worse than no harness, because
  it produces a green result.
- **Mutations never touch the working copy.** `src/`, `tests/`, `config/`,
  `splits/`, `results/` and `tools/` are copied to a temporary directory, along with
  `.gitignore` and the publishable part of `data/`; `data/raw/` and `sealed/` are
  symlinked, because they are restricted and large and no mutation touches either
  path. An interrupted run cannot leave a mutated file behind.

  `data/` itself is a real directory in the copy and only `data/raw/` is a symlink,
  which is not fussiness: `git check-ignore` refuses a pathspec "beyond a symbolic
  link", so with the whole of `data/` symlinked the two screener tests comparing
  `.gitignore` against `DENY_EXCEPTIONS` got rc=128 for every question and read a git
  error as an answer. The tree is also `git init`ed, for the same reason — otherwise
  those tests run against no repository at all and pass by both sides being
  unavailable. Nothing is committed there: history screening must find an empty
  history rather than this repository's.
  `splits/` is copied rather than symlinked precisely so that
  `split_file_span_count` can edit the committed JSON without touching the real
  one, and `results/` is copied so a mutated gate cannot append to the real
  `sealed_eval_log.md` — the row count there is a reported number.
- **A mutation is verified to have been applied before its result is believed.**
  Three checks, described in the next section. Skipping them lets the harness count
  its own breakage as a kill.

The maintenance cost is real but bounded: forty-seven anchors, each a line or two,
and a refactor that breaks one gets a `STALE` message naming the file. That is
cheaper than the failure mode it prevents.

## Verifying the mutation

Every result here is a claim of the form "breaking X was noticed by N tests". That
claim is only worth reading if X was actually broken — and the two ways it can fail to
be both produce a *larger* N than a working mutation. The harness therefore verifies
its own edit before believing the count.

Three checks in `Mutation.apply()`, after the write:

1. **The anchor exists.** Otherwise nothing is edited and whatever was already
   failing is reported as the kill. Reported `STALE`, exit non-zero.
2. **The file changed.** An anchor identical to its replacement passes check 1 and
   mutates nothing — reachable by copy-paste, or by updating an anchor to match code
   that had already drifted in the direction the mutation was meant to introduce.
3. **The result parses.** `.py` files only; `split_file_span_count` mutates JSON.

Two more at the run level:

4. **The suite ran.** `Interrupted: N errors during collection` and `INTERNALERROR`
   mean pytest gave up before executing a test. Reported `BROKEN`, not counted — the
   number of collection errors is the same whether the mutation works or the import
   is broken.
5. **The suite did not shrink.** The total tests reported on must equal the
   baseline's. A mutation changes *which* tests pass, never *how many exist*; a
   smaller total means the suite was damaged rather than challenged.

`tests/test_mutation_harness.py` tests all five, including the indentation case
specifically. A check against false greens that is itself unchecked is the same
category of problem one level further up.

### The incident this section records

It is not hypothetical. Wrapping `docs = list(self._read())` in a `try/finally` while
implementing the seal re-indented that line by four spaces. `drop_excluded`'s anchor
is the line's text, and anchors are indentation-blind, so it went on matching — and
inserted its replacement at the old nesting level. The file no longer parsed. pytest
errored on every test in it. The run printed:

```
ok      drop_excluded             37 tests caught it (expected >= 11)
```

Three times the expected number, the most convincing line in the output, and nothing
had been tested at all. The seal was being built at the time, and `drop_excluded` was
green throughout.

**This is the loader fixture's failure, one layer up.** That defect was a fixture
wrapping construction in `except CorpusError: pytest.skip(...)`, so a real loader bug
was reported as "MEDDOCAN not available on this machine" — 27 skips and a green suite.
The shape is identical: a mechanism that cannot distinguish *the check worked* from
*the check could not run* resolves the ambiguity in the reassuring direction. It
happened once in the tests and once in the harness that tests the tests, which is
enough repetitions to treat it as the standing hazard of this kind of code rather than
as two mistakes.

The general form, worth checking against any future addition here: **anything that
counts failures as evidence must first establish that the thing failed for the reason
claimed.** A skip is not a pass, a collection error is not a kill, and a syntax error
is not thirty-seven tests agreeing with you.

### The fourth of the family: a guard whose precondition the operator controlled

`freeze_window()` was documented as refusing to overwrite an existing freeze record, and
it did — by checking `freeze_path().exists()`. The window was then re-frozen three times
before iteration 1 by `rm` followed by a second call, which that condition cannot see.
Full history in `docs/notes/window-freeze-history.md`; what belongs here is the shape.

`exists()` could not distinguish **no freeze has been taken yet** from **the freeze was
deleted a second ago**, and both readings produce the same cheerful successful write. That
is this family's signature: a mechanism resolves an ambiguity it cannot see, in the
reassuring direction, and reports something that looks better than the truth — a skip as a
pass, a collection error as thirty-seven kills, a rule-violating sample as well-formed, a
re-created record as the opening window.

Two things distinguish it from the other three. First, **the docstring's reasoning was
right and its conclusion was false.** It argued correctly that a rewritable freeze record
answers the wrong question, then implemented a check that a rewrite steps around in one
command. Correct reasoning attached to an insufficient mechanism reads exactly like
correct reasoning attached to a sufficient one, and the prose was what got reviewed.
Second, **nothing was being circumvented.** Each `rm` was deliberate, reported in a commit
message, and genuinely permitted by `rule_author.md` §7's pre-start allowance. The defect
is that the code's only enforcement was a condition the operator controlled, so every
re-freeze's correctness rested on the operator's judgement about whether §7 applied —
which is precisely what the record existed to take out of the operator's hands.

The fix is a second condition on something the deletion cannot reach: a non-null
`human_minutes` on any line of the append-only log. Generalising, for the next addition to
this file: **a guard must not be conditioned on the artifact it protects.** Ask something
that survives the artifact's removal, or the guard is a request.

### The same defect again, in a new file, caught the same way

Recorded because it happened *after* the incident below was written up, in a test file
added the same day, by someone who had just finished writing about it.

`tests/test_show_human_window.py` needs to skip when the corpus is absent from a machine,
so it had a helper that called `load(CORPUS)` inside `except CorpusError: return False`.
That is the loader fixture's original defect verbatim: any loader bug becomes "the corpus
is not on this machine", the tests skip, and the suite is green. Four loader mutations —
`utf8_sig`, `no_bom_shift`, `type_in_both_lists`,
`unsealed_load_filters_instead_of_not_reaching` — came back **BROKEN**, not caught, with
the harness reporting *the suite reported on 399 tests, baseline 402*. The skip changed
how many tests existed, so no kill count from those runs meant anything.

Two things are worth taking from it. The first is that the harness's total-count guard is
what noticed, and that guard exists only because of the earlier incident — the check
added after a mistake caught the same mistake in a place nobody was watching. The second
is less comfortable: knowing about a failure mode in detail is not the same as not
committing it. The fix is the same as the fixture's, for the same reason — ask
`corpus_root()`, which answers only the availability question — and the kill counts for
two of those mutations went *up* afterwards (35 and 43), because tests that had been
skipping now fail on a broken loader.

### The third of the family: a sample that satisfied every property and broke a rule

The `OTHER` incident belongs in this section rather than in the sampling table, because
it is the same failure as the two above with the layer changed again.

The first `port-human` iteration-1 draw on `es-meddocan` came back at 40 spans across 36
documents, ten `phi_type` values represented, the sparse `PROFESSION` present at 1 —
which is `min_per_type` doing exactly its job — and every one of them a `missed` error,
as iteration 1 requires. Every property the suite checks was satisfied. Fifty-six tests
in `test_sample.py` were green. One of the 40 slots held an `OTHER` span, and
`config/naming.yaml` glosses `OTHER` as "residual bucket shipped by a corpus; **not a
rule-development target**" while `docs/prompts/rule_author.md` Prohibition 4 forbids
writing a rule for it. The window handed its author a span they were forbidden to act
on, and `min_per_type: 1` guaranteed it would do so in every iteration of every arm on
every corpus.

**The family resemblance.** The fixture `skip` could not distinguish *the loader is
broken* from *the corpus is absent*. The re-indented anchor could not distinguish *the
tests caught it* from *the tests could not run*. This one is one step further out: the
suite could not distinguish *the sample is valid* from *the sample satisfies every
property anyone thought to write down*. In all three the mechanism resolves an ambiguity
it cannot see, in the reassuring direction, and reports a number that looks better than
the truth — 27 skips as a green suite, 37 errors as 37 kills, a rule-violating sample as
a well-formed one.

The difference is what closes each. The first two were closed by making the mechanism
able to tell the cases apart. This one cannot be: no property of a sample tells you what
a *document elsewhere in the repository* forbids. What closed it is that the constraint
is now read from where it is stated — `non_target_types()` greps naming.yaml's own gloss
— so the config and the sampler cannot disagree, because there is only one of them. The
alternative, a hardcoded `{"OTHER"}`, would have been a second copy of a fact and is now
its own mutation for exactly that reason.

**How it was actually found:** by printing the distribution and reading it, because the
sample was about to be handed to a person. Not by a test, not by a mutation, not by the
screener. Recorded because the honest lesson is uncomfortable — the property-based tests
here are good and they were all green, and the thing that caught it was one look at the
output with the prohibition in mind. The tests and mutations that now cover it were
written *after*, which is the normal and correct order but is not the same as having
prevented it.

## What this found

The harness earned its place on the first run. `familiares_as_other` was expected
to be caught by 7 tests and was caught by 1 — because the `loader` fixture wrapped
construction in `except CorpusError: pytest.skip(...)`, so a real loader bug
(`_check_type_map` correctly rejecting a type that is both mapped and excluded)
was reported as "MEDDOCAN not available on this machine". 27 tests skipped and the
suite stayed green.

Two fixes: the fixture now resolves corpus availability *before* constructing the
loader and only skips on that, so a skip means one thing; and the two faults are
now separate mutations, since being in both lists (rejected at construction) and
being moved between them (accepted, and silently wrong) fail in completely
different ways.

Worth stating plainly: this was a defect in the tests, found by testing the tests.
The loader was correct throughout.

**The seal round found one of each.** Adding the five seal mutations turned up a real
gate defect and a harness defect, in that order:

- `missing_test_fold` survived at 1 kill against an expected 16, and the reason was a
  genuine hole: with `test` absent from `fold_dirs`, an *authorised* sealed read
  returned 750 documents while the log recorded a completed test evaluation. A run
  that spends a row and reads the wrong data is worse than a refused one.
  `fold_roots()` now raises when an authorised sealed fold is unreachable. The
  threshold went to 1 afterwards, for the reason recorded above.
- `drop_excluded` reported 37 kills and had tested nothing — the incident in the
  previous section. Found by reading a suspiciously good number rather than by any
  check, which is exactly why the checks now exist.

So the tally over two rounds is two defects in the tests, one in the harness, one in
the code. The harness is not primarily finding loader bugs; it is finding places where
a green result meant less than it appeared to. That is the thing it is for.

## Applied to every loader

**GraSCCo and CARMEN-I get the same treatment.** A loader is not finished when its
tests pass; it is finished when its tests are shown to fail on the mutations that
matter for that corpus. Both have known encoding and offset hazards already
measured in `docs/notes/corpus-observations.md`, so the mutations are largely
predictable in advance:

- **GraSCCo** — 5 BOM files, and in `Baastrup.txt` and `Dupuytren.txt` the first
  gold span starts at index 0, so the annotated surface itself begins with U+FEFF
  (DESIGN §9.7). That is a sharper version of `utf8_sig` than MEDDOCAN offers,
  where no BOM-file span starts at 0. Also `NAME_TITLE`, its §9.1 exclusion, for
  `drop_excluded` and `familiares_as_other`.
- **CARMEN-I** — the masked and replaced variants have different offsets for
  1,533 of 2,000 documents, so a mutation that loads the annotations of one
  variant against the text of the other belongs in the list. So do the two
  undecided type mappings, for `bucket_unknown_types`. And because it is
  DUA-restricted, add one mutation asserting the reverse direction: that putting a
  span surface into an exception message is caught (CLAUDE.md forbids it, and
  `test_offset_mismatch_message_quotes_no_surface` is the check that must fail
  when it happens).

The split-file mutations transfer with more force, not less. Both corpora need
constructed splits rather than adopted ones (§9.5), which adds failure modes
MEDDOCAN does not have: a seed that is recorded but not used, stratification that
is claimed but not achieved, and — for CARMEN-I — a grouping rule that must reject
189 candidate groups on measured grounds. `grouping_name_only` and
`grouping_numeric_suffix_only` are the templates for those, and
`test_the_committed_file_contains_no_span_surface` is the one that must be run
against CARMEN-I before its split file is committed, because that is the corpus
where the generator would be writing restricted text into a file the release
screener reports as allowed.

The seal mutations transfer unchanged, because the gate is corpus-agnostic: it
guards `sealed_root(corpus_id)` and `fold_roots()`, neither of which knows which
corpus it is protecting. What each new corpus adds is the ordering check —
`splits/{corpus}.json` must be committed before the fold moves, or the sealed
fold's summaries are unverifiable forever (DESIGN §6). For CARMEN-I that is the
step where `test_the_committed_file_contains_no_span_surface` has to run first,
since the generator would otherwise be writing DUA-restricted text into a file the
release screener reports as allowed.

Mutation counts will differ per corpus and the table is expected to grow a column
rather than be replaced.
