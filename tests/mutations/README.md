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
dangerous: **ninety-four of the hundred and five change no total.** The corpus still loads,
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
| `utf8_sig` | `meddocan.py` reads the text with `encoding="utf-8-sig"` | BOM removed at decode time, so `strip_bom` finds nothing and applies no shift; all 761 spans in the 32 BOM files are off by one. DESIGN §9.7 | **70** |
| `no_bom_shift` | offsets are not decremented by the BOM length | same one-character error, reached from the other direction | **70** |
| `assert_offsets_noop` | `Document.assert_offsets` returns immediately | the §9.7 assertion stops asserting; counts are unaffected, so only tests that slice spans themselves can notice | **3** |
| `drop_excluded` | `load()` filters out `excluded` spans | §9.1 spans discarded instead of flagged; the canonical count stays a correct 20,538 while the reported exclusion volume becomes unmeasurable | **12** |
| `familiares_as_other` | `FAMILIARES_SUJETO_ASISTENCIA` moves from `EXCLUDED_TYPES` into `TYPE_MAP` as `OTHER` | an excluded type is scored; every span still loads and the total still reconciles to 22,795, so the corruption is entirely in *which* spans count | **8** |
| `type_in_both_lists` | the same type is added to `TYPE_MAP` while left in `EXCLUDED_TYPES` | `_check_type_map` must reject it at construction. See "What this found", below | **78** |
| `missing_test_fold` | `SPLIT_DIRS` loses its `test` entry | before the seal: 750 documents loaded instead of 1,000. Now 750 is correct, so what remains visible is that an *authorised* sealed read would return no sealed documents while the log records a completed evaluation | **2** |
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
| `grouping_name_only` | §9.5 step 2 accepts a name match without a record number or date | the one stem sharing a bare given name across different surnames becomes a group, and two independent units stop being independent | **2** |
| `split_file_span_count` | `"n_spans": 5801` → `5800` **in the committed JSON** | a stale summary — which is what a re-released corpus actually produces. Direction reversed from every other mutation here: the artefact is the suspect and the code is the check | **3** |

## The seal mutations

The test fold is behind `sealed/` and reachable only through
`src/eval/run_sealed_eval.py` (DESIGN §6). These ten are what make that a
guarantee, and they fall into two groups for a reason worth stating first: the
five below are at the **call sites** in `src/corpora/base.py`, and for a long
time they were the only five this section had. Every one of them asks whether the
guard is *reached*. None of them asks what the guard *does* once it is —
`tree_state`, `record_access` and `_verify_frozen_split` were all patched out in
the tests that mentioned them. The second table is the answer to that, and
`### Unreadable state, twice` below is what it found.

### The call sites

The first two are the pair the seal's design rests on, and they are listed
together because neither guard is sufficient alone:

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `sealed_callable_from_anywhere` | the `SEALED_CALLER` check becomes `if False` | `load(sealed=True)` works from any module — a notebook, a rule-development script. **The log append survives**, so a bypass here still leaves a trace, which is what makes it recoverable rather than merely wrong | **2** |
| `log_append_disabled` | the `record_access` call is wrapped in `except Exception: pass` | an evaluation proceeds unlogged. The numbers are real and the log says the test fold was never opened. **The caller check survives**, so this needs the allowed script — the counterpart of the mutation above, and the one that leaves nothing behind | **2** |
| `sealed_flag_not_cleared` | `_sealed_ok` is not reset after the read | one authorised evaluation leaves that loader object permanently able to reach the sealed fold; every later ordinary `load()` silently includes 250 test documents, with no second log row | **1** |
| `sealed_root_falls_back_to_corpus` | an absent `sealed:` entry resolves to the corpus root | a "sealed evaluation" reads unsealed data and logs itself as a test run. Worse than a refusal: the row is indistinguishable from a real evaluation, so the reported count becomes wrong in the flattering direction | **1** |
| `unsealed_load_filters_instead_of_not_reaching` | `fold_roots()` hands out the sealed path unconditionally | the sealed fold is read and then discarded downstream. Every count still comes out right; the test fold's text has been read on every ordinary load, unlogged. Defends the distinction that the seal is a path that is not known, not a filter that is applied | **73** |

### What the guards do once reached

On `src/eval/sealed_log.py` and `src/eval/run_sealed_eval.py`, added after an
audit found neither module had a single mutation aimed at it. Their tests are in
`tests/test_seal_internals.py`, against a real `git` repository in `tmp_path`:

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `an_unreadable_tree_state_reads_as_clean` | `tree_state`'s `if commit is None or porcelain is None` becomes `if False` | git cannot be reached and the tree reports **clean** — `None` is falsy, so the reassuring branch is the one that already handles it. `load_sealed` proceeds and the log gets a row asserting a clean tree at a commit nobody could confirm. See `### Unreadable state, twice` | **5** |
| `a_dirty_tree_reads_as_clean` | `"dirty" if porcelain else "clean"` → `"clean"` | the state is never dirty. The refusal in `load_sealed` is intact and unreachable, and every row records a commit that does not describe the code that ran. **The pre-audit suite could not catch this**: its one dirty-tree test patched `tree_state` to *return* `dirty`, proving the refusal fires when told, and unable to notice that nothing ever tells it | **7** |
| `only_tracked_modifications_count_as_dirty` | `git status --porcelain` → `git diff --name-only` | the plausible edit, and the subtler half of the one above. `git diff` reports unstaged changes to tracked files only, so an **untracked** file and a **staged** change both read clean — leaving the one case a person checks by hand working | **2** |
| `the_frozen_split_check_ignores_a_moved_document` | the fold comparison becomes unreachable | drift stops being detected; a sealed evaluation runs against a corpus the split file no longer describes, after which no number can be tied to a fold. **Before the audit this function was patched out in both tests that mentioned it and executed by none** | **3** |
| `the_frozen_split_is_verified_after_the_read` | the verify/read pair is reordered | the check still runs, still raises on drift, and is worthless: the fold is open and the log has spent a row. `sealed_eval_log.md` cannot un-count a run. Ordering mutations survive behavioural suites because every assertion about the *outcome* still holds | **1** |

### Unreadable state, twice

`an_unreadable_tree_state_reads_as_clean` is the second appearance in this
repository of one question: **what happens when the state cannot be read?** The
first was `check_region`'s `except (ClientError, BotoCoreError)` in
`tools/check_bedrock_logging.py`, where an IAM denial would otherwise become a
dated row saying logging was off. They are worth reading as one family:

| | unreadable because | would otherwise read as | consequence |
|---|---|---|---|
| `check_region` | the caller lacks `bedrock:GetModelInvocationLoggingConfiguration`, or the endpoint is unreachable | logging is **off** | a false row in `compliance.md` §3, which the client's gate accepts as licence to call and the paper's ethics section cites |
| `tree_state` | `git` is absent, the directory is not a repository, or `HEAD` has no commit | the tree is **clean** | a row in `sealed_eval_log.md` asserting a clean tree at an unconfirmable commit — and the count of those rows is the paper's N |

Four things they have in common, and each is the reason to write this down:

- **The unreadable case and the reassuring case share a code path.** Absence and
  "nothing configured" are both the empty answer; `None` and "no changes" are
  both falsy. Neither failure requires anyone to write a wrong branch — it
  requires only that they not write the extra one.
- **The safe direction is not the quiet one.** Both guards must escalate an
  unknown into a refusal, which means the correct behaviour is the one that
  interrupts somebody. That is exactly the behaviour a reviewer is tempted to
  soften, so it needs a mutation standing behind it rather than a comment.
- **Both were written correctly and executed by nothing.** These are not bugs
  that were fixed; they are guards that were right on the first attempt and had
  no coverage, in files with 20 and 30-odd passing tests respectively. Which is
  the whole argument for the harness: a suite cannot report the tests it does
  not contain.
- **Fabricating a control is worse than losing one.** Losing it leaves you where
  you were before it existed. Fabricating it leaves you confidently past a line
  you never crossed, holding a document that says you crossed it legitimately —
  and both of those documents are committed evidence.

The generalisation: patching **data or a path** is legitimate; patching **the
function that holds the guarantee** removes the guarantee from the suite while
leaving the count of passing tests unchanged. A structural check enforcing this
is the next item of the audit and is not in the tree yet — said here as work
owed, not as a safeguard in place. `tests/test_seal.py:152` already said so — "the
substitution is deliberately placed at the *data* and never at the frame" — and
the same file broke it three times. A principle written in a comment is not a
check either.

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
| `fully_covered_is_relaxed` | `_covers()` tests `> 0` instead of `== mark.length` under `fully_covered` | the headline mode collapses into the lower bound while keeping its name; a gold span with one character covered counts as hidden | **11** |
| `leak_rate_from_assignment` | the `leak.leaked` figure is taken from the assignment's false negatives | the error DESIGN §9.3 exists to prevent: a leak reported on an identifier whose every character is hidden | **9** |
| `greedy_tiebreak_dropped` | the sort key becomes `(-overlap, pi, gi)` | ties fall through to emission order, so the metrics move when the same spans arrive shuffled | **1** |
| `by_rule_fp_from_coverage` | `by_rule`'s hits are taken from type-matched overlap instead of from the assignment | a rule whose spans always lose the assignment to a better one reads as harmless, and the only signal that licenses deleting a rule disappears while every aggregate stays correct | **5** |
| `the_provenance_fields_are_optional_again` | `REQUIRED_RUN` loses `generated`, `commit` and `tree` | §10 A2's mitigation goes back to being described in the design and absent from the writer. Every existing metrics file is unchanged, because `run_fold` still writes all three; the loss is in what a new writer may omit, and the orchestrator is the new writer | **5** |
| `generated_accepts_a_bare_date` | `GENERATED_RE` matches `YYYY-MM-DD` and stops there | the field is still required and no longer answers its question: two runs on the day an alias moves carry the same string, and ordering them is the whole point | **3** |
| `a_null_commit_needs_no_unknown_tree` | the paired check on `commit`/`tree` is skipped | the nullable field becomes an optional one. `{"commit": null, "tree": "clean"}` is accepted — a run that read the working tree, which means it ran a git command that also produced a revision, and recorded no revision | **1** |

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

The last three are about the run block rather than the numbers, and they are here
because the run block is what makes the numbers re-runnable. **`a_null_commit_needs_no_unknown_tree`
is the one worth reading, because the guard it removes was written after the strict
version of the same guard failed.** The first draft of schema 4 required a truthy
`commit`. That looked like the careful choice and was not: `sealed_log.tree_state()`
returns `(None, "unknown")` when git cannot be read, so the strict check made a real
run unscoreable — and it was found not by review but by the harness's own tree, a
repository with no commits, where thirteen `test_run_fold.py` tests stopped being able
to write at all.

A validator that refuses the honest record leaves the writer two options: refuse to
score, or put something in the field. The second is what happens, and a hash that
reads as identifying the code while nobody checked whether it does is exactly what
`tree` was added to prevent — the strict check would have manufactured the failure it
was guarding against. So null is permitted, **and only in company**: `clean` and
`dirty` are both read from a git command that also produced a revision, so either of
them beside a null hash is refused. Unpaired, the exemption stops being an exemption
and becomes the way to omit the hash on a tree that could be read perfectly well.
That is the mutation, and its count of **1** is honest: there is one test that can
see it, because every other check in `check_run` passes on the contradictory block.

Counts are the number of tests that fail or error, from
`tests/test_meddocan_loader.py`, `tests/test_split_file.py`, `tests/test_seal.py`,
`tests/test_release_screen.py`, `tests/test_layer_families.py`,
`tests/test_scorer.py`, `tests/test_sample.py`, `tests/test_human_arm.py`,
`tests/test_show_human_window.py`, `tests/test_rules.py` and
`tests/test_check_rules.py` (531 tests). Errors
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
| `sample_pool_not_sorted` | the error pool keeps the caller's iteration order instead of `sorted(..., key=e.key)` | the seed pins which *indices* are drawn and the caller's ordering pins which spans those indices hit — reproducible from the log, different in fact | **3** |
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

> **The arm was retired on 2026-08-07** (DESIGN §11, §4.1) — no human author could be
> secured. These mutations and the guards they cover are **kept and still run.** Two
> reasons. First, most of what they protect is not `port-human`-specific: `src/sample.py`
> is the draw both porting arms use, and the practice band, the seeded selection and the
> surface-form rules apply to whatever arm reads a dev sample. Second, a guard whose test
> is deleted when its arm is paused is a guard that comes back untested — and a revived
> human arm inherits DESIGN §11's pre-registered protocol, so it inherits these
> enforcement points too. A mutation that no longer corresponds to a running arm still
> answers "would this defect be noticed", which is the only question this directory asks.
>
> The one thing to read differently: where a row says a defect makes `port-human`
> uninterpretable, the consequence is now conditional on the arm being revived. The
> defect is still a defect; its cost is deferred rather than removed.

`src/porting/human_arm.py` runs what was the control arm: freeze the window, draw, append to
`human_log.jsonl`. It no longer renders — `render_window()` moved to `src/llm/prompt.py`
(DESIGN §5.4), and the one mutation on the rendering moved with it. Its guarantees are of a
different kind from the sampler's. Most of them are not about numbers at all — one is the seal, one is what may
be said out loud about a sample, and two are about a clause that binds a person and has
no enforcement but a field in a log.

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `initial_pool_excludes_train_instead_of_selecting_dev` | `split != "dev"` becomes `split == "train"` | the test fold enters the window a person writes rules from — the seal violation that invalidates the experiment, arriving as a plausible spelling of the same filter | **1** |
| `summary_reports_offsets` | `summarise()` gains a `spans` field of `(doc_id, start, end)` | the view built to be pasted into a terminal or a commit message starts carrying pointers into the corpus. No surface form is quoted, which is why it would survive review | **1** |
| `human_log_path_from_a_literal` | the arm rebuilds its output paths instead of reading `paths.humanlog` from naming.yaml | two authorities on where this arm writes, identical today; the day the config moves, results are written to one path and read from another | **1** |
| `human_log_allowed_under_any_arm` | the screener's allowed path for `human_log.jsonl` takes `[^/]+` for the porting component instead of the literal `port-human` | a log under any other arm is counted as reviewed, and nothing writes one there — its presence is the signal, and the wildcard is what hides it | **1** |
| `self_report_defaults_to_none` | `model_consulted` acquires a default of `"none"` | every caller keeps working and every line says no model was consulted — recording that nobody was *asked*, not that nobody consulted a model. A default on this parameter is a default for `rule_author.md` §8 | **1** |
| `self_report_refuses_the_violation` | `log_line()` raises on `model_consulted="rule_content"` | looks like enforcement, removes the report. §8 binds a person and cannot be enforced by code; all a refusal deletes is the record, after which every log attests to a clean run by construction | **3** |
| `rendered_window_may_be_redirected` | `show_human_window.py`'s `stdout.isatty()` check becomes `if False` | `> window.txt` succeeds and a DUA corpus's ±120-character contexts are on disk — the file §6 says must not exist. The author sees the same window, the script exits 0, and the leak is a file nobody opens again | **2** |
| `freeze_guard_only_checks_the_file` | the `arm_has_started()` condition in `freeze_window()` becomes `if False` | restores the hole this repository fell through three times: `rm window_freeze.json` then re-freeze, which `path.exists()` cannot see. The new record hashes today's files and claims to be the opening window | **8** |
| `arm_started_reads_the_last_line_only` | `arm_has_started()` inspects the final log line instead of every line | appending any null-minutes event re-opens the freeze, and appending is what this arm does constantly. A `read_sample` at iteration 7 makes six iterations of attention re-writable | **1** |
| `zero_minutes_read_as_not_started` | `is not None` becomes a truthiness test | a logged `human_minutes: 0` reads as "nothing happened", though `log_line()` validates the field to accept 0 because an event can take under a minute | **1** |
| `started_where_reads_the_worktree_only` | `started_where()` stops consulting git history | reverts the guard to reading `human_log.jsonl` on disk and nothing else, so `rm human_log.jsonl` re-opens the freeze — one file, one command, and the guard's own input is gone | **8** |
| `a_real_arm_may_draw_a_practice_number` | the band's refusal drops the arm-side half | iteration 901 becomes a legal arm iteration and its provenance says `practice: false`, so a rehearsal's numbers are reportable as a run | **4** |
| `a_rehearsal_may_draw_a_real_number` | the band's refusal drops the practice-side half | a rehearsal aimed at iteration 1 draws iteration 1. The draw is seeded, so the printed window is byte-for-byte the real one and nothing records that it was read early — there is no artifact to find afterwards | **5** |
| `the_practice_window_may_overlap_iteration_one` | `practice_pool()` stops subtracting iteration 1's spans | a different iteration number is not a different sample; the spans read in rehearsal are the ones iteration 1 no longer measures honestly | **4** |
| `a_rule_layer_is_derived_from_the_rule_id` | a span's `layer` comes from a substring test on its `rule_id` instead of the rule's declaration | the one derivation DESIGN §3 forbids, in the form it would actually take. Every span still carries a valid layer from naming.yaml, so §7's per-layer comparison measures the substring test | **1** |
| `a_gazetteer_term_is_a_regex` | gazetteer terms are interpolated unescaped | makes the regex-free layer a regex layer without saying so. `C.S. (Norte)` is an ordinary institution name and a broken pattern | **2** |
| `a_gazetteer_term_needs_a_word_character_at_each_edge` | the per-edge boundary assertion becomes `\b` | restores the defect `test_rules.py` caught on its first run: a term beginning or ending in punctuation can never match, so the rule loads, compiles, fires nowhere, and reads as a name that does not occur | **1** |
| `a_cue_span_swallows_the_cue` | a `context_cue` span covers the cue words as well as the identifier | scored against gold that starts at the name: a `fully_covered` miss that passes `relaxed`, depressing exactly the layer §7 predicts most for and reading as a scoring artefact | **2** |
| `a_checksum_accepts_every_match` | the check-digit test is skipped | turns `regex_checksum` into `regex`. The layer's whole claim is shape *plus* arithmetic, and its name still says the arithmetic is there | **1** |
| `an_unimplemented_checksum_is_ignored` | an unknown `checksum:` name loads | raises at match time instead of load time — mid-detection rather than with the rule id in hand. Refusing at load costs a line of output; refusing at first match costs a scoring round | **1** |
| `a_lexicon_name_may_traverse_directories` | the `[a-z0-9_]+` check on a lexicon name is removed | `es/../../sealed/es-meddocan/test` is a valid-looking lexicon name, and a rule file is the one place an agent-authored artifact names a path | **1** |
| `the_declared_rule_file_language_is_trusted` | the file's `lang:` is no longer checked against the language it was loaded as | the `rule_id` prefix comes from the load language, so a `cat` file loaded as `es` sends every span's precision to the wrong file, consistently and invisibly (DESIGN §5.2) | **1** |
| `a_duplicate_rule_id_is_allowed` | two rules may share an id | `by_rule` and the span's `rule_id` are the same identifier, so two rules' attribution merges into one bucket and the per-rule figure belongs to neither | **1** |
| `a_non_target_type_may_be_a_rule_target` | a rule may target `OTHER` | it is a residual bucket a corpus ships, not a phenomenon; the recall bought is a property of that corpus's annotation practice (Prohibition 4, §9.1) | **1** |
| `check_rules_reads_every_fold` | the feedback tool's `split == "dev"` filter is dropped | the command an author runs forty times an evening starts scoring across folds. Not a seal break by itself — `sealed/` is not returned by the loader — but it is the step before one | **3** |
| `check_rules_detects_separately` | the feedback tool iterates rules itself instead of calling `detect_fold` | a second detection implementation, *faithful on the day it is written* — same rules, same documents, same offsets. Caught structurally, because there is nothing behavioural to see yet | **1** |
| `run_fold_detects_separately` | the run path grows its own detection loop, which dedupes by offsets | what a hand-rolled loop turns into: two rules matching the same bytes collapse to one, so the tool shows the author two matches and the score counted one. Sets and totals both agree; only the multiset does not | **2** |
| `detect_fold_drops_overlaps` | the shared detector resolves overlaps first-rule-wins | takes the merge decision away from the merge axis. fixed-priority, union and agent-arbiter would then score identically, having been handed a prediction set with the conflicts already settled (DESIGN §4) | **2** |
| `spans_file_carries_the_surface` | `spans.jsonl` gains a `surface` field | matched corpus text in a published file the screener allows by pattern. This is the edit someone makes to debug a boundary, and it is the DUA violation the field whitelist in `write_spans` exists to prevent | **2** |
| `run_fold_reads_the_sealed_fold` | the `split == "test"` refusal becomes unreachable | the loader's gate still refuses the import, so not a seal break by itself — what it removes is the layer that says *why*. What reaches the caller instead is a corpus-shaped error that sends them looking for a missing fold | **2** |
| `run_fold_omits_the_layer` | the published `layer` is the span's `detector` | the derivation DESIGN §3 forbids, in the form it takes at the writer rather than the detector. Every `R` span would read as layer `R`, and §7's per-layer complementarity would collapse to one bucket | **3** |
| `run_fold_writes_a_null_model_id` | `model_id` is `null` for an arm that called no model | indistinguishable from a field nobody filled in, so the record cannot say whether `R` used no model or the run forgot to write down which one it used. Absent is refused, explicitly-absent is recorded (§5.0) | **21** |
| `run_fold_hardcodes_the_absent_value` | `"none"` is written as a literal instead of read from naming.yaml | breaks nothing today — that is why it is here. CLAUDE.md requires config-defined vocabulary in results files, and the cost is paid on the day the config moves and one of the two spellings does not | **1** |
| `run_fold_skips_axis_validation` | `spans.jsonl` is written without `check_run` | a misspelled axis value mints a results directory no axis defines. `write_metrics` still validates, so the failure is an orphan spans file beside no metrics — the halfway state validate-before-write exists to prevent | **1** |
| `run_fold_writes_unsorted_spans` | the sort before writing is removed | stable today, for an upstream reason rather than a stated one. Reorder the rules in the file and a committed results file gets a diff a reviewer cannot tell from a change in what was detected | **1** |
| `arm_rules_path_drops_the_axes` | `paths.armrules` becomes `rules/{lang}.yaml` — the state before DESIGN §5.3 | `port-oneshot` and `port-loop` then write the same file and the second arm to run overwrites the first's rules. `str.format` ignores unused keys, so nothing raises: every arm's path collapses to one silently. Worse than the `armfreeze` collision it repeats — an overwritten record is visibly gone, an overwritten *input* leaves a complete, consistent metrics.json whose premise no longer exists | **6** |
| `arm_rules_path_drops_the_iteration` | the four axes stay, `iter{N}/` goes | the collision stays closed and the history does not. `port-loop` rewrites its file every round, and that sequence is what δ/k was computed over and the only answer to "which rules existed at iteration 4". Keeps the last round, discards the arm's process — §5.1's objection to aggregates, applied to inputs | **4** |
| `arm_rules_path_loses_the_rules_component` | `.../{porting}/iter3/es.yaml` instead of `.../{porting}/rules/iter3/es.yaml` | every axis is present and the overwrite argument is untouched. What breaks is invisible from the path: the screener's `rule_id` mechanism-vocabulary check matches `rules/*.yaml`, and it is Prohibition 2's only enforcement. Unmatched is not rejected — the check never runs and the file is reported clean | **3** |
| `run_fold_infers_its_own_rule_path` | `run_fold` builds `arm_rules_path()` from its own axis arguments instead of being told | behaviourally invisible on the happy path, which is why the assertion is structural. The cost is that the module has one possible input, so a trial file and the bootstrap each need a special case, and the input becomes a function of the run block — the coupling that lets a run read its own results directory. The hardcoded `iteration=1` is the tell: an inferring version has to invent a round it was never given | **1** |
| `rule_source_not_recorded` | `rules_source` is dropped from the run block, `rules_version` stays | the version is whatever the author declared, so it survives an overwrite looking correct; only the path names the arm and the iteration. Without it §5.3's decision is undetectable from the published record — the reader sees a well-formed metrics.json either way | **1** |
| `rule_source_recorded_absolute` | the rule file's path is recorded absolute instead of repo-relative | names a home directory in a published run block, and on a machine where the corpus checkout sits beside the repository it names the layout of DUA data. Still a string, still identifies the file, so every present-and-non-empty assertion passes | **4** |
| `render_offsets_are_document_offsets` | the rendered window labels document offsets as within-context offsets | an author counting characters lands on the wrong span, and one trusting the number is handed a document coordinate — an invitation to read past the ±120 characters | **1** |
| `renderer_writes_a_debug_copy` | `render_window()` also writes the rendered text to `/tmp/last_prompt.txt` | the filled prompt on disk — the file rule_author.md §6 says must not exist, ±120 characters of dev text per span. Nothing about the run changes: same prompt, same model input, every content assertion still passes. The screener blocks the committed paths an instance would land under, and `/tmp` is not one of them — which is why the convention is "never written" and not "never committed". Also the reason the renderer's *interior* is checked and not only the type: the type is intact here and protects a value that already escaped | **1** |
| `filled_prompt_exposes_its_text` | `FilledPrompt` gains a `.text` property | an accessor not named for a destination, which is the distinction the type exists to draw. `to_terminal` checks where it is going, `for_transport` declares it; `.text` answers to a log line, a `json.dumps` of a record that happens to hold it, an f-string in an exception message. Adding it breaks nothing and is the natural edit for a caller wanting to assert on the text | **2** |
| `terminal_exit_does_not_check_the_destination` | `to_terminal`'s `isatty` check becomes `if False` | the exit writes to whatever it is handed, so a redirected stream receives the window. `show_human_window.py`'s own check is why this is caught rather than fatal — but that one is for the error message and this one is the guarantee, and the next caller of the exit is the orchestrator, which has no check of its own | **2** |
| `logging_gate_defaults_to_open` | the `checked_today()` condition in `_require_logging_check` becomes `if False` | every call proceeds with no Bedrock model-invocation logging check on record — the state `compliance.md` §3 says cannot be assumed, since it is a mutable account setting and yesterday's `None` is evidence about yesterday. Nothing observable changes: the call succeeds, the arm writes its artefact, the scores are the same numbers. If logging is on, Bedrock is writing the full prompt — ±120 characters of dev-fold context per span — to a bucket in this account, and the only sign from inside the run is that the run worked | **4** |
| `absent_token_counts_default_to_zero` | `usage.get("inputTokens")` gains a default of `0` | a partial `usage` block becomes a cost block asserting the call consumed nothing, in the same column as measured counts. CLAUDE.md requires cost beside quality so a gain bought at twice the price is legible; a zero does not weaken that comparison but strengthens it wrongly — the arm that lost a field looks free. The two-argument `.get` is the natural edit, removing an exception from a path nobody has seen fire | **1** |
| `a_mismatched_model_is_recorded_rather_than_refused` | `_resolution` returns `check_model_resolution(MISMATCH)` where it raises | the mutation that looks like an improvement: it uses the declared vocabulary, loses no information, and puts the disagreement in `metrics.json` where a reader could find it. Recording is strictly more data than refusing — and still wrong, because a `mismatch` row means nobody can say which model produced the artefact, so it is unusable for the one purpose it exists for and writing it down does not make it usable (§10 A2). naming.yaml declares the value so the refusal can name it; declaring is not permission to emit | **2** |
| `the_client_hardcodes_botocores_default_attempts` | `Config(retries={"max_attempts": 3})` instead of `MAX_ATTEMPTS` | one `invoke()` becomes up to three calls. `MAX_ATTEMPTS = 1`, its comment, and the module docstring's claim that the transport is pinned all stay exactly as they are. The damage is invisible and lands in the cost column: `Response.cost()` reports `llm_calls: 1` because the type is one call, so a throttled run bills three times and reports once — undoing §10 A2's zero-retry symmetry underneath it, in the direction where the throttled arm looks cheap | **1** |
| `the_reply_text_is_taken_from_the_first_block` | `_text` reads `blocks[:1]` instead of every text block | reverts the client to the shape the response *looks* like it has. Not hypothetical — it is what was written first and it failed on the first real call: this model returns `reasoningContent` and *then* `text`, so a good reply is reported as having none. Kept as a mutation because the fix is invisible in a fixture written from the API docs, which is why `test_bedrock.py`'s fixtures put a reasoning block first by default | **16** |
| `the_logging_check_reports_an_unreadable_setting_as_clean` | `check_region` returns `(region, CLEAN)` where it raises on `ClientError` | an IAM denial becomes a clean bill of health, the tool appends a dated record for a region it could not read, the client's gate opens on it, and `compliance.md` — cited by the paper's ethics section — carries a measurement nobody made. The worst failure in the pair, because it manufactures evidence rather than losing it, and the plausible edit: `AccessDeniedException` in an unused region reads as noise, and `cloudtrail:DescribeTrails` already returns exactly that for this principal | **4** |
| `conftest_availability_from_a_load` | the shared availability fixture goes back to deciding availability by loading the corpus | the defect that shipped four times, reverted. Changes nothing until a real loader bug arrives, and then hides it: measured alongside `type_in_both_lists`, **93 tests skip and 78 non-passing outcomes become 3**, reported as a green suite | **1** |
| `test_file_shadows_the_shared_fixture` | one test file defines its own `corpus_present`, in the defective form | the propagation rather than the defect: the local definition wins over conftest's silently, and only that file's tests are affected — which is how three files carried it unnoticed | **2** |
| `the_patch_check_credits_a_whole_file` | `satisfies` drops the function name and compares only the file | the verdict becomes a subset test: **one executed function in a module vouches for every patched function in it.** `record_access` runs in a dozen tests, so `tree_state` would be credited without ever running — the exact state the audit found. The check still runs, prints a count and exits 0. The weakening to expect, because it is what a false positive tempts you into | **1** |
| `the_patch_check_credits_a_bare_function_name` | `satisfies` drops the file and compares only the name | the same weakening in the other axis: any `axis` anywhere satisfies `src/corpora/base.py`'s, including one a test defined itself. Separate from the row above because the two are accepted for different reasons — this one looks like tolerance for import aliasing, that one for module copies | **1** |
| `the_patch_allowlist_stops_requiring_a_reason` | the `why` word-count check becomes `if False` | an exemption no longer has to say why, and the fastest way to close a finding stops being *run the function* and becomes *add two lines of JSON*. Nothing breaks today; what breaks is the review in six months, when nobody can tell an impossible case from a Friday afternoon | **2** |
| `a_stale_patch_exemption_is_ignored` | the stale-entry comparison becomes `[]` | an exemption outlives the function it describes and waits to cover whatever takes the name next. The failure mode of every allowlist that is never pruned | **1** |
| `the_arm_freeze_guard_only_checks_the_file` | `orchestrate.freeze_window()`'s refusal becomes `freeze_path().exists()` | **the defect this repository shipped, moved to the arm that will actually run.** `rm window_freeze.json` then re-freeze writes today's hashes and reports success — the sequence `window-freeze-history.md` records running three times. Worse here than for `port-human`: `port-oneshot` writes no per-line hashes, so the record is the *only* thing attesting to the window the call ran under. It is also wrong in the other direction, refusing the pre-call re-freeze §6.3 permits, which is why the count is high | **7** |
| `the_freeze_record_drops_the_empty_block_marking` | `sections_empty` is dropped from the record | the record stops saying which blocks the call did *not* carry, so a reader must derive it from `INPUT_BLOCKS` — and a reader who knows `INPUT_BLOCKS` is not the reader the field is for. `sampling_applied` survives, so the record still distinguishes the two cases and no longer says what the distinction is about | **2** |
| `the_freeze_record_claims_the_sampling_parameters_applied` | `sampling_applied` becomes the constant `True` | the field it was added to prevent, restored: a `port-oneshot` record then claims *n*=40 at ±120 characters governed a call that carried no §1.4 at all. §6.3 keeps `sampling_sha256` for comparability with the arms that do use it, which is exactly why the record needs a field saying the hash did not govern this call | **3** |
| `the_baseline_draws_error_spans` | `orchestrate.freeze_window()` calls `initial_error_pool()` | **DESIGN §4's ladder condition broken in the direction that looks like an improvement.** At iteration 1 the §1.4 pool comes from an empty rule file, so those spans are dev **gold** — the baseline shown 40 of them has dev information `port-loop` call 1 does not, and the two arms differ in two things instead of one. A `port-loop` win is then unattributable at the comparison the paper leads with, and the arm flattered is the rung above. Caught structurally: the plumbing is a two-line addition and a behavioural test notices only once it moves a number | **2** |

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

### The two that fail with nothing left behind

`a_rehearsal_may_draw_a_real_number` and `the_practice_window_may_overlap_iteration_one`
belong together, and they are the sharpest instances in this file of a category the seal
mutations opened: **a defect whose symptom is an absence.**

The practice band exists because iteration 1's window opens once. If a rehearsal draws it —
either by taking iteration 1's number, or by drawing a different number from a pool that
still contains iteration 1's spans — the author has read the window, and there is no
artifact anywhere that says so. The sample is the real sample, byte for byte, because the
draw is a pure seeded function of `(corpus, iteration, error list)`. Nothing is corrupted;
nothing is missing; the numbers reported at the end of iteration 1 are exactly the numbers
that would have been reported. What is gone is the thing being measured.

This is why the practice/real distinction is a **caller-declared flag checked against the
number in both directions**, and not an inference from the number. Inference is not merely
riskier here — it is impossible. An `--iteration 1` call with no flag is either a rehearsal
whose caller forgot to say so or the real run, and the two are the same call. A default
value for that parameter would be a default answer to a question only the caller can
answer, which is the same shape as `self_report_defaults_to_none` further up this table.

### And a boundary that was found by writing the test

`a_gazetteer_term_needs_a_word_character_at_each_edge` is not a hypothetical. The gazetteer
layer shipped with `\b` on both sides of each term, which is the obvious way to say "a
term is a word and not a substring", and `tests/test_rules.py` failed on its first run:
`\b` asserts a word character on the *inside* of the boundary, so a term ending in `)` or
`.` can never match anything. `C.S. (Norte)` is an ordinary Spanish institution name.

The reason it is in this table rather than only in a commit message is what the failure
would have looked like in the results. The rule loads. It compiles. It matches nothing.
The author sees `caught 0/40` and concludes the name does not occur in the dev fold — and
"the phenomenon does not occur" is exactly what DESIGN §7 reports as a negative result for
a layer. A silent zero from a gazetteer is indistinguishable from a finding about
gazetteers.

`started_where_reads_the_worktree_only` is the same defect one level out, and it was
shipped and documented as an open hole before it was closed. The guard's first version
read the working tree's log only, which meant the fix for a deletable freeze record was a
guard whose own input was a deletable log. The note called that acceptable on the grounds
that deleting the log is louder than deleting the freeze record — which is true, since the
log is the arm's only record of what a person did — and "louder" is not "prevented". The
second source is `git log --all` over that one path, every commit rather than the newest,
because the newest is the one an edit would have changed. Two limits stay open and are
asserted rather than implied: a rewritten history defeats it, and minutes that were never
committed cannot be recovered
(`test_a_log_never_committed_and_then_deleted_reads_as_not_started`). The purpose is to
stop an accident and make a deliberate change conspicuous, not to make one impossible —
those are different goals and only the first is reachable in code.

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

`terminal_exit_does_not_check_the_destination` is the same guarantee one layer in, and the
pair is worth reading together. The script's check produces the *message* — it runs before
a corpus is loaded and can therefore say what to do instead. The type's check is the
*guarantee*, and it is the one that survives a new caller: the next thing to hold a
`FilledPrompt` is the agent orchestrator, which has no `isatty` check of its own and no
reason to grow one. That the two are separately mutable, and separately caught, is the
point of having both.

`render_offsets_are_document_offsets` is in this table rather than the `port-human` one
because the renderer is, and it arrived here the way a moved anchor should: the harness
reported it **STALE** on the run after `render_for_author()` was deleted, naming a file the
code had left. It did not pass, and it did not silently match nothing — a vanished anchor is
an outcome, which is the property that makes moving code safe to do.

The three after it break the three properties the type rests on, and each is an
edit someone makes for a good reason. A debug copy while chasing a boundary error; a
`.text` property so a test can assert on the text; a plain `print` because the window is
going to a screen anyway. None of them fails on any machine where anyone is looking, which
is the same reason `tests/test_conftest.py` checks structure rather than behaviour —
`renderer_writes_a_debug_copy` in particular leaves the type entirely intact and defeats it
completely, because a type protecting a value that has already been copied to disk protects
nothing. That is what justifies checking the renderer's interior rather than only its
signature.

Six of these are caught by exactly one test, which is the honest number and a thin
one; they are listed at 1 rather than padded.

### The detection-path mutations, and the one failure shape they all produce

The last eleven are on `src/eval/run_fold.py` and `tools/check_rules.py`, and four of them
are here for a single reason: **the two tools must not be able to disagree about what was
detected.** `check_rules.py` shows an author a sample; `run_fold.py` scores the fold and
writes the file a paper is built from. One implementation (`detect_fold`), two views of it.

The failure a second implementation produces is the worst shape available in this
repository, which is why four mutations are spent on it. It is not a wrong number — it is
*two* numbers with the same name. The sample says a rule fires and the fold-wide metrics
say it does not, and **nothing in either output identifies which one is wrong.** An author
cannot act on it: they tune against whichever number is lying to them, and the tuning
looks like progress in the tool. A reader comparing the tool's counts to `metrics.json`
cannot act on it either, and the natural conclusion — that the sample and the fold simply
differ — is available, wrong, and unfalsifiable from the outside.

`check_rules_detects_separately` is the one to read twice, because **it is faithful.** It
iterates the same rules over the same documents and, on the day it is applied, produces the
same offsets from both sides. Every behavioural test passes. That is not a weakness of the
mutation, it is the fact being recorded: a second implementation does not arrive broken,
it arrives correct and drifts later when one side is changed and the other is not. Nothing
observable exists yet, so nothing behavioural can catch it, and the test that does is
structural — `.finditer(` must not appear in the tool. Structural tests are usually the
weaker kind; here it is the only kind available, and the mutation is what shows why.

`run_fold_detects_separately` is the same edit from the other side and it *has* drifted —
one line, a `seen` set, skipping offsets it has already emitted. That is what a hand-rolled
loop turns into within a week, because deduplicating looks like hygiene. It is not: two
rules matching the same bytes is a merge-policy question (DESIGN §4), and answering it
inside the detector means the tool shows the author two matches while the score counted
one. Catching it needed the test to compare **multisets**. Totals agree, sets agree, and
only the count of the repeated span differs — which is also why `probe_org_dup` sits in
`test_run_fold.py`'s rule file duplicating another rule's term. Without a guaranteed
byte-identical collision in the fold, that test and `detect_fold_drops_overlaps` both
depend on the corpus happening to make two rules collide, and a test that passes because
of a property of MEDDOCAN is a test that stops holding on the next corpus.

Two more are the ones that break nothing when applied, and they are grouped with
`human_log_path_from_a_literal` in spirit. `run_fold_hardcodes_the_absent_value` writes
`"none"` as a literal instead of reading `model_id_absent()` — character-for-character what
the config holds, so every assertion about the written value still passes. What it removes
is the property that one file decides the vocabulary, and the cost is paid on the day
naming.yaml changes and one of the two spellings does not. `run_fold_writes_unsorted_spans`
is the same shape in the output: `RuleSet.detect` iterates rules in file order, so removing
the sort leaves the file byte-identical across reruns and
`test_the_file_is_byte_identical_across_runs` green. It stops being stable when something
upstream reorders, and then a committed results file has a diff nobody can distinguish
from a change in what was detected. The test that catches it asserts the order is *sorted*
rather than *reproducible*, and had to check that more than one document and more than one
rule appear — with either at one, a per-rule or per-document grouping looks sorted.

`spans_file_carries_the_surface` is the DUA one and it needs no argument beyond its own
description: `spans.jsonl` is allowed by the screener by pattern, so adding a `surface`
field publishes corpus text through a path that is *already approved*. It is also the edit
someone genuinely makes, at two in the morning, to find out why a boundary is off by one.
The whitelist in `write_spans` exists because that edit is reasonable and its consequence
is not.

`run_fold_reads_the_sealed_fold` is the mildest of the group and is included for what it
says about layering. The loader's own gate still refuses, so the seal holds with this
mutation applied — nothing leaks. What disappears is the sentence naming the rule. The
caller gets a corpus-shaped error instead and goes looking for a missing fold, and the
person most likely to do that is the one who typed `--split test` because they did not
know it was sealed. A guard whose failure message teaches the rule is doing a second job,
and the second job is the one that survives contact with a tired evening.

### The Bedrock mutations, and the one that survived

Six are on `src/llm/bedrock.py` and `tools/check_bedrock_logging.py`, and they
share a property none of the earlier groups has: **five of the six leave a client that
returns a perfectly good `Response`.** No exception, no missing field, no malformed
artefact. The arm runs, the rules are written, `metrics.json` validates. What each one
removes is a refusal, and a refusal that has been removed is indistinguishable from a
refusal that was never needed.

`logging_gate_defaults_to_open` and `the_logging_check_reports_an_unreadable_setting_as_clean`
are the two halves of the same guarantee, and they fail in opposite directions. The first
loses the evidence: no record, no check, calls proceed anyway. The second **manufactures**
it — a dated row in `compliance.md` §3 saying logging was off in a region where nobody could
read the setting, which the client's gate then accepts as license to call. Of the two the
second is worse, and it is worse in a way that is easy to miss when writing the tool: losing
a control leaves you where you were before the control existed, while fabricating one leaves
you confidently past a line you never crossed. `compliance.md` is what the paper's ethics
section cites. A row in it that describes an unread setting is not a weaker claim than no
row; it is a false one. The same question came up a second time in `tree_state`, and the
two are read together under `### Unreadable state, twice` above.

**`the_logging_check_reports_an_unreadable_setting_as_clean` SURVIVED when it was first
run, and that is the most useful thing in this group.** `tests/test_check_bedrock_logging.py`
had twenty passing tests at the time. Every one of them patched `check_all` — the sensible
way to test a tool whose real behaviour needs an AWS account — and `check_region`'s
`except (ClientError, BotoCoreError)` branch, the only place in the repository where an
unreadable setting is turned into a refusal, was therefore executed by nothing at all. The
tool was green, the guarantee was documented in its own docstring and in `compliance.md`,
and it had no coverage whatsoever. **A test that patches out the function containing the
guarantee cannot test the guarantee**, and the twenty green tests could not say so. Only
the mutation could.

The repair was not to weaken the mutation but to drive the real `check_region` through a
fake `boto3.client`, which is a smaller patch in exactly the place that matters: the AWS
call is faked, the error handling is not. Four tests catch it now, one of them
(`test_check_all_stops_at_the_first_unreadable_region`) checking that the sweep *stops*
rather than continuing — because six regions of which one is unreadable is five
measurements and an unknown, not a clean result with a gap. Writing that test surfaced its
own trap: raising the `ClientError` from the fake client's *constructor* let it escape
untranslated, because construction happens outside `check_region`'s `try`. The test passed
on the wrong exception until the error was moved onto the API call.

`a_mismatched_model_is_recorded_rather_than_refused` is the one to read twice, because it
is the only mutation in this file that is arguably an improvement. It replaces a raise with
`check_model_resolution(MISMATCH)`: the value is declared in naming.yaml, the disagreement
is preserved rather than discarded, and it lands in `metrics.json` where a reader could
find it. Recording strictly dominates refusing on information content. It is still wrong,
and the reason is what the value is for. A `mismatch` means the response named a model other
than the one requested, so nobody can say which model produced the output — and §10 A2's
whole purpose is attributing a number to a model family. An artefact that cannot be
attributed is unusable for the only thing it is for, and writing down that it is unusable
does not make it usable. A refused call costs one re-run. A recorded mismatch costs a number
in the appendix that nobody will notice is unattributable until they think to grep the
resolution column. **Declaring a value in naming.yaml so a refusal can name it is not
permission to emit it**, which is a distinction the vocabulary rule does not make on its own.

`the_reply_text_is_taken_from_the_first_block` is the only one of the six that was a real
bug rather than a hypothesised one, and it is kept for what it says about fixtures. The
obvious reading of a `converse` response — content is a list, take the text off the first
element — is what was written, and it raised on the very first real call, because this model
returns `reasoningContent` and then `text`. The fix is one slice. The point is that a
fixture written from the API documentation, with one text block in it, passes under both the
correct and the broken version: the shape that catches the bug is the shape you only know
about after making a real call. That is why the fixtures in `test_bedrock.py` put a reasoning
block first *by default* rather than in one dedicated test, and why the sixteen tests that
catch this mutation are mostly not about text extraction at all.

### The orchestrator mutations: the same guard, on the arm that runs

The last four are on `src/orchestrate.py`, and the first of them is a repeat. `the_arm_freeze_guard_only_checks_the_file` is `freeze_guard_only_checks_the_file` moved from
the arm that was retired to the arm that will actually run, and the general form above —
**a refusal conditioned on the presence of the thing being protected is a request addressed
to whoever can remove the evidence** — is the whole reason the orchestrator's guard reads a
log rather than a path. What does not transfer is the *input*. `port-human`'s second
condition is a non-null `human_minutes` on some line, because a line there can precede any
spent attention (`event: read_sample`). `agent_calls.jsonl` has no such line: it is appended
to when a call is made, so a line's existence *is* the event, and the guard does not parse
the line — refusing to see a malformed one would fail in the unsafe direction.

The fallback differs for a harder reason. `port-human`'s is `git log --all` over the log
itself; `agent_calls.jsonl` is deny-listed by `tools/release_screen.py`, since an agent
prompt quotes dev text, so **git history can never hold a copy of it.** What history holds
is the arm's other artefacts, so the second source asks whether this arm has ever committed
an output — a per-commit listing of its results directory, **excluding the freeze record**.
Counting the record would be `path.exists()` arriving by the back door with a `git` walk in
front of it to look thorough, which is why
`test_the_committed_freeze_record_alone_is_not_evidence_of_a_call` exists and why the
exclusion is by basename read from `paths.armfreeze` rather than a literal. The two limits
`port-human`'s note states stay open here too and are asserted, not implied
(`test_a_call_never_committed_and_then_deleted_reads_as_not_called`).

There is one inversion worth stating, because it looks like an inconsistency. `port-human`'s
`freeze_window()` hands back the existing record so a re-run gets the opening window;
`port-oneshot`'s overwrites, and bumps a `revision` counter. The difference is what else
attests to the window. `port-human` writes per-line hashes on every log line, so a stale
record is corroborated elsewhere; §6.3 excuses `port-oneshot` from those on the grounds that
*n*=1 makes freeze and call one moment — which leaves the record as the **only** attestation,
and handing back a stale proposal would let the prompt move between freeze and call with
nothing disagreeing. Both directions of the guard are therefore live, and that is why seven
tests fail when the refusal collapses to `exists()`.

`the_freeze_record_drops_the_empty_block_marking` and
`the_freeze_record_claims_the_sampling_parameters_applied` are one guarantee split in two,
and it is a shape none of the earlier groups has: **a record that is accurate field by field
and false as a whole.** `port-oneshot` hashes `config/sampling.yaml` and uses none of *n*,
`min_per_type` or `context_chars`, because DESIGN §4 truncates the ladder before §1.4 exists.
The hash stays for comparability with the arms that do use it, so a `port-oneshot` record and
a `port-loop` record carry the same `sampling_sha256` over calls that differed in whether
those parameters governed anything at all. Every field is correct; read together they say the
wrong thing. `sections_shown`, `sections_empty` and `sampling_applied` are what make the two
distinguishable, and `test_an_applied_and_an_unapplied_window_do_not_read_alike` compares the
two records against each other rather than asserting a field is present — because the claim is
about a difference, and a test that checks for a field passes on two records that both lie.
`sampling_applied` is **derived** from the sections rather than passed in, so the two cannot
disagree; the mutation that pins it to `True` is the version where they can.

`the_baseline_draws_error_spans` is the one to read twice, and it is the only mutation in this
file that breaks a *comparison* rather than a number. DESIGN §4 fixes `port-oneshot` as
`port-loop` truncated after call 1, which means `port-loop`'s own first call reads §§1.3–1.4
empty. At iteration 1 the §1.4 pool comes from `initial_error_pool()` against an empty rule
file, so those spans are dev **gold**. Showing forty of them to the baseline gives it dev
information the rung above does not have at the same point, the two arms then differ in two
things instead of one, and a `port-loop` win becomes unattributable at exactly the comparison
the paper leads with — flattering the rung above. It is caught structurally, by AST, for the
reason the row gives: the plumbing is a two-line addition, and a behavioural test notices only
once it has already moved a number.

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

The maintenance cost is real but bounded: eighty-three anchors, each a line or two,
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

And then the same question has to be asked of the fix, which is where the fifth member came
from within the hour: the log is also a file, so the first version of the corrected guard
was conditioned on an artifact the operator could remove — one command, `rm
human_log.jsonl`, and the freeze re-opened. It was written down as an accepted limit, which
is better than not noticing but is not the same as closing it. So `started_where()` now
reads git history as well, and the recursion stops there for a real reason rather than
because patience ran out: history is not a file this repository's own code writes, removing
the minutes from it takes a rewrite, and a rewrite of a public repository's history is
visible to anyone who has fetched it. **The endpoint of "condition it on something else" is
not an unremovable artifact — there is none — it is an artifact whose removal is
conspicuous.** A guard's job is to stop an accident and to force a deliberate act to be
deliberate, and the two remaining holes (a rewritten history, a log never committed) are
asserted as tests so the boundary is stated rather than discovered.

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
two of those mutations went *up* afterwards, because tests that had been skipping now
fail on a broken loader. (The figures at the time were 35 and 43; both are larger now —
see the next paragraph, where two more files turned out to be doing the same thing.)

**Then it happened twice more, and the count is the point.** `tests/test_check_rules.py`
was written with `except Exception: pytest.skip(...)` around a `load()` — broader than the
original, since it swallows every exception type and not just `CorpusError` — and
`tests/test_run_fold.py` copied that fixture from it on 2026-08-07. The same four loader
mutations went `BROKEN` again, at 521 tests against a baseline of 570, and stayed that way
across two sessions in which this section was on screen. Fixing both to `corpus_root()`
took the four from BROKEN to **caught by 70, 70, 78 and 73 tests** — the largest kill counts
in the file, because the whole corpus-dependent suite had been skipping. Four occurrences of
one defect, three of them after it was written up, is not a story about carelessness in any
single instance: **a fixture is copied from the nearest similar file, so a defect in one
propagates at the rate new test files are added.** The guard that catches it has to live in
the harness rather than in a reviewer's memory, which is the only reason this was ever
found.

**The fifth was prevented, and here is the mechanism.** A warning in this file had already
failed three times, so what changed is structural, in four parts:

1. **`tests/conftest.py` holds the only definition.** `corpus_present` and `sealed_corpus`
   answer availability from `corpus_root()` / `sealed_root()` — path resolvers, which fail
   for one reason. `loader` and `unsplit_loader` construct, take `corpus_present` as an
   argument, and contain **no `try` at all**: availability was already settled upstream, so
   a failure at construction is a loader bug and reaches the test as one. That split is the
   whole fix; every occurrence collapsed the two questions into one `except`.
2. **Six files were converted and none kept a local copy.** `test_meddocan_loader.py`,
   `test_split_file.py`, `test_seal.py`, `test_show_human_window.py`,
   `test_check_rules.py`, `test_run_fold.py`. Each keeps a comment at the deleted fixture's
   old location saying what was there and why it is gone, because the next author looks
   where the fixture used to be.
3. **`tests/test_conftest.py` refuses the copy**, against the syntax tree rather than
   behaviour — the defective and correct forms behave identically on every machine where
   anyone would look, so only structure separates them. Four rules, one per way the defect
   actually arrived: no test file may `pytest.skip` from inside a fixture; none may name
   `corpus_root`/`sealed_root` outside a test body (that is what catches the
   `test_show_human_window.py` variant, a module-level function feeding `skipif`, which the
   fixture rule alone misses); none may define a fixture conftest already defines; and
   conftest's own availability fixtures may call nothing but the path resolvers while its
   construction fixtures may hold no handler and no skip. One further test parses the
   reverted form as a string and asserts the check sees it, because a structural check that
   silently matches nothing is this same family again.
4. **Two mutations keep all of that honest**: `conftest_availability_from_a_load` reverts
   the fixture, `test_file_shadows_the_shared_fixture` re-adds a local copy to one file.

The count that justifies the work is in `conftest_availability_from_a_load`'s own
`breaks` text and is worth repeating here, because on a working machine the defect is
invisible — same 596 tests, same green. Measured by applying it together with a real loader
bug (`type_in_both_lists`): the correct fixture gives **31 failures and 47 errors**; the
reverted one gives **3 failures, 0 errors and 93 skips**. **93 tests silently disabled, 78
non-passing outcomes reduced to 3**, and pytest reports it in the colour of success. The
three survivors are the tests that construct a loader themselves instead of through the
fixture — which is also why the conversion had to reach every file rather than most of them.

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

**The seal round found one of each.** Adding the five call-site seal mutations turned up a
real gate defect and a harness defect, in that order:

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

**The Bedrock round found a third defect in the tests, and it was the largest.**
`the_logging_check_reports_an_unreadable_setting_as_clean` survived at 0 kills against
20 passing tests, all of which patched `check_all`. Not a wrong assertion but an
*absent* one: the branch turning an unreadable setting into a refusal had never been
executed. Generalising the pattern — patch the data, never the guarantee — produced the
audit that this file's second seal table and `### Unreadable state, twice` come from,
which found three more instances inside `tests/test_seal.py` alone. Three of the four
test defects found so far are the same shape, and none of them is visible in a test
count.

### The two axes: doing too much, and taking too much away

Two structural checks now sit either side of the same problem, and the pairing is the
useful part:

| | `tests/test_conftest.py` | `tools/check_patched_guarantees.py` + `tests/test_structure.py` |
|---|---|---|
| the fault | a test **doing too much** — deciding corpus availability privately | a test **taking too much away** — replacing the function that holds a guarantee |
| how it hides | a real bug presents itself as an absent corpus; tests skip and the suite is green | the guarantee simply leaves the suite; the tests that remain still pass |
| what it costs | 93 tests silently disabled, 78 non-passing outcomes reported as 3 | four guards with zero coverage, in files with 20 and 30 passing tests |
| shipped | four times | four times |
| the evidence | the syntax tree — which calls sit inside which `except` | the interpreter — which code objects were actually entered |

Neither fault is visible in a count of passing tests, and each shipped four times *after*
being written up here. That is the argument for both checks in one sentence: prose does
not fail. A note explains a defect to whoever reads it; a check refuses the defect from
whoever does not.

They also differ in where the evidence comes from, and that was forced rather than
chosen. Availability is decided by *structure* — the defective and correct forms behave
identically on every machine where anyone would notice, so only the tree separates them.
Execution is a *runtime* fact, and the static approximation gets it wrong: asking whether
the suite calls a function by name reports `_require_logging_check` as uncovered, when
five tests exercise it through a module-level alias and an `invoke()` call. That is the
normal way a gate gets tested — through the thing it guards — so a static check would
need a list of blessed idioms, and a check with a list of blessed idioms stops applying to
code written next month.

### The fifth, and what it actually cost

The point of the check is the occurrence that does not happen, which is unprovable, so
here is what it did on its first run instead:

- **It found a candidate nobody had noticed.** `src/eval/run_sealed_eval.py::_loader_for`
  was patched in five tests and executed by none. Patching it there is right — a loader is
  *data* to the function under test, and the real MEDDOCAN loader needs the corpus on the
  machine — but its own refusal branch is what makes an unimplemented corpus fail before
  the seal is touched rather than somewhere further in. Closed by writing the test, not by
  writing an exemption, and the allowlist is still empty.
- **It caught its own first version being too strict.** The check initially required the
  executing file to be inside the repository, and reported `check_region` — the guarantee
  the whole audit started from — as never executed, because `test_check_bedrock_logging.py`
  imports a *copy* of the tool from a temporary tree. A check that fails on the honest
  idiom the suite already uses gets loosened until it matches nothing, so `satisfies`
  compares basenames and its docstring records what that costs.
- **Its own weakenings have mutations.** Four of them, in the table above. Two collapse the
  verdict into a subset test, two disarm the allowlist. All four leave a check that runs,
  prints a reassuring count, and exits 0 — which is the family this whole file is about,
  now applied to the check that detects the family.

The last one matters most. The place a control could be defeated is exactly where this
project has repeatedly had no coverage, so `tests/test_structure.py` also forbids what
would defeat this one: no test may set the profiler, write to the checker or its
allowlist, or patch the checker's own functions. No file is exempt from those rules,
including `tests/test_structure.py` — which is why they are phrased as *writes to those
paths* rather than *mentions of them*. The first draft used a substring ban and failed on
the test that forbids the thing and on a docstring that explains why a test exists.
Mentioning a control is how it stays understood; calling it is the risk.

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
