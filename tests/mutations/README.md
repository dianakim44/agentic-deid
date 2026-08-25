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
dangerous: **the great majority change no total.** The corpus still loads,
the document count is still 750, the span count is still 17,134 — and every
downstream number is wrong. Those are the errors a reviewer cannot catch and an
aggregate cannot reveal, which is why they get a harness rather than trust.

That sentence used to read "ninety-four of the hundred and five", and it was wrong by
five when it was noticed — the harness held a hundred and ten. `run.py --list` is the
count; a fraction maintained by hand in prose beside it is a second answer that drifts,
which is the failure this whole directory is about. The current total is whatever
`--list` prints.

The seal mutations are the sharpest case of that. A broken seal produces no wrong
number at all: the figures are real, they are simply computed on data that was
supposed to be untouched, and nothing in the output distinguishes them from
legitimate ones. There is no aggregate to check and no way to undo it after the
fact, so a harness is the only place the guarantee can live.

## The loader mutations

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `utf8_sig` | `meddocan.py` reads the text with `encoding="utf-8-sig"` | BOM removed at decode time, so `strip_bom` finds nothing and applies no shift; all 761 spans in the 32 BOM files are off by one. DESIGN §9.7 | **142** |
| `no_bom_shift` | offsets are not decremented by the BOM length | same one-character error, reached from the other direction | **142** |
| `assert_offsets_noop` | `Document.assert_offsets` returns immediately | the §9.7 assertion stops asserting; counts are unaffected, so only tests that slice spans themselves can notice | **3** |
| `drop_excluded` | `load()` filters out `excluded` spans | §9.1 spans discarded instead of flagged; the canonical count stays a correct 20,538 while the reported exclusion volume becomes unmeasurable | **12** |
| `familiares_as_other` | `FAMILIARES_SUJETO_ASISTENCIA` moves from `EXCLUDED_TYPES` into `TYPE_MAP` as `OTHER` | an excluded type is scored; every span still loads and the total still reconciles to 22,795, so the corruption is entirely in *which* spans count | **8** |
| `type_in_both_lists` | the same type is added to `TYPE_MAP` while left in `EXCLUDED_TYPES` | `_check_type_map` must reject it at construction. See "What this found", below | **150** |
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
| `unsealed_load_filters_instead_of_not_reaching` | `fold_roots()` hands out the sealed path unconditionally | the sealed fold is read and then discarded downstream. Every count still comes out right; the test fold's text has been read on every ordinary load, unlogged. Defends the distinction that the seal is a path that is not known, not a filter that is applied | **145** |

### What the guards do once reached

On `src/eval/sealed_log.py` and `src/eval/run_sealed_eval.py`, added after an
audit found neither module had a single mutation aimed at it. Their tests are in
`tests/test_seal_internals.py`, against a real `git` repository in `tmp_path`:

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `an_unreadable_tree_state_reads_as_clean` | `tree_state`'s `if commit is None or porcelain is None` becomes `if False` | git cannot be reached and the tree reports **clean** — `None` is falsy, so the reassuring branch is the one that already handles it. `load_sealed` proceeds and the log gets a row asserting a clean tree at a commit nobody could confirm. See `### Unreadable state, twice` | **107** |
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
| `filled_prompt_paths_allowed` | the `prompts/(filled|rendered)/` deny pattern stops matching | a filled RuleAuthor prompt at `prompts/filled/iter03.md` — carrying the ±120-character context of every sampled dev error — reads as an ordinary file under `prompts/`, an ALLOW_HINTS prefix. Not merely unblocked: reported clean | **4** |
| `rule_id_vocabulary_not_checked` | the mechanism-vocabulary check in `rule_id_findings` is removed, leaving the shape rules | the screener returns to its first version, which passes every legitimate name and also passes `es:perez_ruiz` — a surname published through `metrics.json`'s `by_rule` block, which is on the *allow* list | **25** |

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

Its count moved 20 → 24 on 2026-08-23, and the four are the `--propose` tests. That is the
reading worth recording rather than the number: the proposal lines are computed from
`_rule_id_scan`, the same pass the finding comes from, so deleting the vocabulary check
silences the proposals too and the tests that name a token notice. Had the proposals been a
second implementation of the lookup — the obvious way to write them — those four tests would
still pass with the check gone, and the mutation's count would have held at 20 while the
screener's own report and its proposal block disagreed about what the vocabulary contains.
The rise is the evidence that the one-writer split did what it was for.

### The language layer: four mutations on a check that was wrong in the safe direction

The vocabulary above shipped English-only, and the first arm to actually run against a
Spanish corpus produced 28 rule names of which **23 were reported SUSPECT** — every one
of them for naming a clinical formula in the corpus language (`paciente_cue`,
`calle_cue`, `firmado_cue`). Prohibition 2 permits exactly that: a formula is allowed
and designating an individual is not. So the check was wrong, and it was wrong in the
direction this project prefers — it over-rejected, nothing leaked, and the cost was a
screener that would exit 1 on every non-English arm forever. That is still a cost worth
paying attention to, because a permanent finding is not a finding, which is the same
argument the SEALED line and the allowlist were built on.

The fix widens the vocabulary with a per-language layer of that language's formulae
(`RULE_ID_VOCAB_BY_LANG`). Widening a whitelist is the dangerous kind of change, so all
four mutations below are ways the widening goes further than the categories justify.

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `the_language_layer_is_keyed_on_the_id_the_model_wrote` | `lang` comes from the rule id's `es:` prefix instead of from the file path | the screened text nominates its own vocabulary. The prefix is free text in a file the model produced; the path comes from the arm's configuration. Reads as the *more* precise source, and for every legitimate file the two agree | **2** |
| `a_disagreeing_prefix_still_opens_the_layer` | the branch that drops the layer when prefix and path disagree is deleted | `de:` inside `es.yaml` keeps the Spanish set. Looks like deleting an unreachable case — why would an id in `es.yaml` say `de:`? Because the model writes it, and disagreement is the one state where the two sources can be played against each other | **1** |
| `an_unknown_language_gets_every_layer` | an unrecognised `lang` falls back to the union of all layers rather than to none | presented as robustness for a corpus whose vocabulary nobody has written yet. It makes the union — the widest vocabulary in the tool — reachable from any unclassified filename, so one name may be built from Spanish, Catalan, German and Korean formulae at once | **1** |
| `the_language_layer_is_a_substring_test` | layer membership becomes `p in w` instead of `p == w` | reads as tolerance for inflection (`ano` inside `anos`). A closed set decided by containment is not closed: `ana` passes on `anos`, `mar` on `marzo`, and short fragments are what names are made of | **1** |

**What holds the line, and what cannot.** The layers contain no personal names, no
place names, and nothing that can designate one individual or institution — and that is
held by review, not by code. Two tests get as close as a test can. `test_no_layer_word_could_be_a_surface_form_by_shape`
re-applies the shape rules to the layers themselves, so a capitalised, digit-bearing or
non-ASCII word cannot enter a layer even though those words would be caught anyway when
used; it closes the crudest route rather than the interesting one.
`test_the_spanish_layer_does_not_pass_a_person_or_place` carries the actual criterion,
and `calle_mayor` is the row that shows the categories are doing the work: `calle` is in
the layer as a *kind* of street and `mayor` is not in anything, so a street's name is
still flagged while the mechanism name that matches streets is not. `paciente_perez` is
the same shape — one token from the layer does not license the rest of the name.

The layer is code in `tools/release_screen.py` rather than data in `config/naming.yaml`,
and the reason is that the screener imports nothing but the standard library. It is the
one tool that must run before every commit, so giving it a YAML dependency adds a
failure mode to the gate itself. `naming.yaml` is the *experiment's* vocabulary, read by
`src/`; this is a publication-screening criterion with no experimental meaning. The two
are connected by `test_every_layer_language_is_a_declared_lang_axis_value`, which
refuses a layer for a language the `lang` axis does not declare — so the layer can be
narrower than the axis but never invent a language.

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
| `greedy_allows_reuse` | `assign()` drops `pi in used` from the skip condition | the matching stops being one-to-one, so one wide prediction collects credit for several gold spans. Recall rises for emitting one coarse span instead of two correct ones — the detector is paid for being vaguer about boundaries | **9** |
| `fully_covered_is_relaxed` | `_covers()` tests `> 0` instead of `== mark.length` under `fully_covered` | the headline mode collapses into the lower bound while keeping its name; a gold span with one character covered counts as hidden | **14** |
| `leak_rate_from_assignment` | the `leak.leaked` figure is taken from the assignment's false negatives | the error DESIGN §9.3 exists to prevent: a leak reported on an identifier whose every character is hidden | **10** |
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

Counts are the number of tests that fail or error across the whole of `run.py`'s
`TEST_FILES` — **27 files, 1696 tests**, measured by the harness's own baseline run.
Errors count as kills: a mutation that breaks the module-scoped fixture takes whole tests
out, and those are caught, not uncounted.

**The cells in this file are the full run of 2026-08-20's numbers — 170 mutations, one 1696-test
suite run each — and `docs/notes/mutation-full-runs.counts.json` is the authority, not this file.**
The sidecar is written from measurements by `parallel.py`; these cells are prose, and prose is
transcribed. The two agreed exactly while there had been one full run. There have now been two, and
the second moved 21 of the numbers below, so the honest statement is where each kind of number
comes from rather than a single date over all of them.

The 21 that rose on 2026-08-25, under a suite that grew from 1696 to 1804 tests inside the same 27
files: `allowlist_may_name_corpus_paths` 1 → 2, `an_unreadable_tree_state_reads_as_clean`
107 → 117, `arm_rules_path_drops_the_axes` 74 → 81, `no_bom_shift` 142 → 151,
`only_the_score_is_scoped_to_the_round` 26 → 32, `rule_id_vocabulary_not_checked` 20 → 25,
`run_fold_omits_the_layer` 32 → 38, `run_fold_writes_a_null_model_id` 45 → 48,
`spans_file_carries_the_surface` 34 → 40, `the_audit_report_is_allowed_instead_of_denied` 2 → 4,
`the_audit_report_is_read_as_the_previous_rounds_file` 69 → 75,
`the_client_hardcodes_botocores_default_attempts` 1 → 2,
`the_folds_seconds_go_to_the_round_and_not_the_arm` 76 → 83,
`the_iteration_allow_pattern_covers_the_whole_directory` 3 → 4,
`the_language_layer_is_keyed_on_the_id_the_model_wrote` 2 → 3,
`the_mask_tags_are_emitted_in_the_order_they_were_applied` 85 → 91,
`the_per_iteration_key_replaces_the_arm_level_one` 110 → 122,
`the_reply_text_is_taken_from_the_first_block` 85 → 92, `type_in_both_lists` 150 → 159,
`unsealed_load_filters_instead_of_not_reaching` 145 → 154, `utf8_sig` 142 → 151.

**A cell below that reads lower than the sidecar means the suite grew, and never that a guarantee
weakened.** That direction is checked and not assumed: both full runs recorded zero decreases, and
a decrease is the finding that would matter — it would mean a test stopped being able to see a
defect it used to see. The six draw / abandoned-spend mutations were first measured in the
2026-08-25 run and their cells carry those numbers; `rule_id_vocabulary_not_checked` no longer
carries the 2026-08-23 date it used to, because a full run has since restated it. The 2026-08-19
markers were retired when the minority became everyone (§"The first full run"), and this paragraph
is the same problem in the opposite direction: 21 exceptions are too many to mark and few enough to
list, so they are listed.

**Counts recorded at different times are not comparable, because the denominator moved.**
This paragraph used to name eleven files and 531 tests, which is what `TEST_FILES` held when
the loader and split-file tables above were first measured; sixteen files have joined since.
A count is a number *about the suite of the day it was measured*, so a row not re-run under
the current list reads low against one that has — not because coverage thinned but because
there was less suite to fail. That is the whole reason a full run has a section of its own:
until every cell is measured under one list, the tables cannot be read against each other,
only individually. The direction to be suspicious of is the other one: a count that *fell*
while the suite only grew is a real loss of coverage.

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
| `non_target_filter_removed` | `non_target_types()` returns an empty set | `OTHER` takes a slot, and with `min_per_type: 1` it takes one in *every* iteration of *every* arm — a permanent slot handed to a type Prohibition 4 forbids writing a rule for | **9** |
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
| `the_declared_rule_file_language_is_trusted` | the file's `lang:` is no longer checked against the language it was loaded as | the `rule_id` prefix comes from the load language, so a `cat` file loaded as `es` sends every span's precision to the wrong file, consistently and invisibly (DESIGN §5.2) | **2** |
| `a_duplicate_rule_id_is_allowed` | two rules may share an id | `by_rule` and the span's `rule_id` are the same identifier, so two rules' attribution merges into one bucket and the per-rule figure belongs to neither | **1** |
| `a_non_target_type_may_be_a_rule_target` | a rule may target `OTHER` | it is a residual bucket a corpus ships, not a phenomenon; the recall bought is a property of that corpus's annotation practice (Prohibition 4, §9.1) | **1** |
| `check_rules_reads_every_fold` | the feedback tool's `split == "dev"` filter is dropped | the command an author runs forty times an evening starts scoring across folds. Not a seal break by itself — `sealed/` is not returned by the loader — but it is the step before one | **3** |
| `check_rules_detects_separately` | the feedback tool iterates rules itself instead of calling `detect_fold` | a second detection implementation, *faithful on the day it is written* — same rules, same documents, same offsets. Caught structurally, because there is nothing behavioural to see yet | **1** |
| `run_fold_detects_separately` | the run path grows its own detection loop, which dedupes by offsets | what a hand-rolled loop turns into: two rules matching the same bytes collapse to one, so the tool shows the author two matches and the score counted one. Sets and totals both agree; only the multiset does not | **4** |
| `detect_fold_drops_overlaps` | the shared detector resolves overlaps first-rule-wins | takes the merge decision away from the merge axis. fixed-priority, union and agent-arbiter would then score identically, having been handed a prediction set with the conflicts already settled (DESIGN §4) | **2** |
| `spans_file_carries_the_surface` | `spans.jsonl` gains a `surface` field | matched corpus text in a published file the screener allows by pattern. This is the edit someone makes to debug a boundary, and it is the DUA violation the field whitelist in `write_spans` exists to prevent | **34** |
| `run_fold_reads_the_sealed_fold` | the `split == "test"` refusal becomes unreachable | the loader's gate still refuses the import, so not a seal break by itself — what it removes is the layer that says *why*. What reaches the caller instead is a corpus-shaped error that sends them looking for a missing fold | **2** |
| `run_fold_omits_the_layer` | the published `layer` is the span's `detector` | the derivation DESIGN §3 forbids, in the form it takes at the writer rather than the detector. Every `R` span would read as layer `R`, and §7's per-layer complementarity would collapse to one bucket | **32** |
| `run_fold_writes_a_null_model_id` | `model_id` is `null` for an arm that called no model | indistinguishable from a field nobody filled in, so the record cannot say whether `R` used no model or the run forgot to write down which one it used. Absent is refused, explicitly-absent is recorded (§5.0) | **45** |
| `run_fold_hardcodes_the_absent_value` | `"none"` is written as a literal instead of read from naming.yaml | breaks nothing today — that is why it is here. CLAUDE.md requires config-defined vocabulary in results files, and the cost is paid on the day the config moves and one of the two spellings does not | **1** |
| `run_fold_skips_axis_validation` | `spans.jsonl` is written without `check_run` | a misspelled axis value mints a results directory no axis defines. `write_metrics` still validates, so the failure is an orphan spans file beside no metrics — the halfway state validate-before-write exists to prevent | **1** |
| `run_fold_writes_unsorted_spans` | the sort before writing is removed | stable today, for an upstream reason rather than a stated one. Reorder the rules in the file and a committed results file gets a diff a reviewer cannot tell from a change in what was detected | **3** |
| `arm_rules_path_drops_the_axes` | `paths.armrules` becomes `rules/{lang}.yaml` — the state before DESIGN §5.3 | `port-oneshot` and `port-loop` then write the same file and the second arm to run overwrites the first's rules. `str.format` ignores unused keys, so nothing raises: every arm's path collapses to one silently. Worse than the `armfreeze` collision it repeats — an overwritten record is visibly gone, an overwritten *input* leaves a complete, consistent metrics.json whose premise no longer exists | **74** |
| `arm_rules_path_drops_the_iteration` | the four axes stay, `iter{N}/` goes | the collision stays closed and the history does not. `port-loop` rewrites its file every round, and that sequence is what δ/k was computed over and the only answer to "which rules existed at iteration 4". Keeps the last round, discards the arm's process — §5.1's objection to aggregates, applied to inputs | **10** |
| `arm_rules_path_loses_the_rules_component` | `.../{porting}/iter3/es.yaml` instead of `.../{porting}/rules/iter3/es.yaml` | every axis is present and the overwrite argument is untouched. What breaks is invisible from the path: the screener's `rule_id` mechanism-vocabulary check matches `rules/*.yaml`, and it is Prohibition 2's only enforcement. Unmatched is not rejected — the check never runs and the file is reported clean | **7** |
| `run_fold_infers_its_own_rule_path` | `run_fold` builds `arm_rules_path()` from its own axis arguments instead of being told | behaviourally invisible on the happy path, which is why the assertion is structural. The cost is that the module has one possible input, so a trial file and the bootstrap each need a special case, and the input becomes a function of the run block — the coupling that lets a run read its own results directory. The hardcoded `iteration=1` is the tell: an inferring version has to invent a round it was never given | **1** |
| `rule_source_not_recorded` | `rules_source` is dropped from the run block, `rules_version` stays | the version is whatever the author declared, so it survives an overwrite looking correct; only the path names the arm and the iteration. Without it §5.3's decision is undetectable from the published record — the reader sees a well-formed metrics.json either way | **2** |
| `rule_source_recorded_absolute` | the rule file's path is recorded absolute instead of repo-relative | names a home directory in a published run block, and on a machine where the corpus checkout sits beside the repository it names the layout of DUA data. Still a string, still identifies the file, so every present-and-non-empty assertion passes | **7** |
| `render_offsets_are_document_offsets` | the rendered window labels document offsets as within-context offsets | an author counting characters lands on the wrong span, and one trusting the number is handed a document coordinate — an invitation to read past the ±120 characters | **1** |
| `renderer_writes_a_debug_copy` | `render_window()` also writes the rendered text to `/tmp/last_prompt.txt` | the filled prompt on disk — the file rule_author.md §6 says must not exist, ±120 characters of dev text per span. Nothing about the run changes: same prompt, same model input, every content assertion still passes. The screener blocks the committed paths an instance would land under, and `/tmp` is not one of them — which is why the convention is "never written" and not "never committed". Also the reason the renderer's *interior* is checked and not only the type: the type is intact here and protects a value that already escaped | **1** |
| `filled_prompt_exposes_its_text` | `FilledPrompt` gains a `.text` property | an accessor not named for a destination, which is the distinction the type exists to draw. `to_terminal` checks where it is going, `for_transport` declares it; `.text` answers to a log line, a `json.dumps` of a record that happens to hold it, an f-string in an exception message. Adding it breaks nothing and is the natural edit for a caller wanting to assert on the text | **2** |
| `terminal_exit_does_not_check_the_destination` | `to_terminal`'s `isatty` check becomes `if False` | the exit writes to whatever it is handed, so a redirected stream receives the window. `show_human_window.py`'s own check is why this is caught rather than fatal — but that one is for the error message and this one is the guarantee, and the next caller of the exit is the orchestrator, which has no check of its own | **3** |
| `logging_gate_defaults_to_open` | the `checked_today()` condition in `_require_logging_check` becomes `if False` | every call proceeds with no Bedrock model-invocation logging check on record — the state `compliance.md` §3 says cannot be assumed, since it is a mutable account setting and yesterday's `None` is evidence about yesterday. Nothing observable changes: the call succeeds, the arm writes its artefact, the scores are the same numbers. If logging is on, Bedrock is writing the full prompt — ±120 characters of dev-fold context per span — to a bucket in this account, and the only sign from inside the run is that the run worked | **4** |
| `absent_token_counts_default_to_zero` | `usage.get("inputTokens")` gains a default of `0` | a partial `usage` block becomes a cost block asserting the call consumed nothing, in the same column as measured counts. CLAUDE.md requires cost beside quality so a gain bought at twice the price is legible; a zero does not weaken that comparison but strengthens it wrongly — the arm that lost a field looks free. The two-argument `.get` is the natural edit, removing an exception from a path nobody has seen fire | **2** |
| `a_mismatched_model_is_recorded_rather_than_refused` | `_resolution` returns `check_model_resolution(MISMATCH)` where it raises | the mutation that looks like an improvement: it uses the declared vocabulary, loses no information, and puts the disagreement in `metrics.json` where a reader could find it. Recording is strictly more data than refusing — and still wrong, because a `mismatch` row means nobody can say which model produced the artefact, so it is unusable for the one purpose it exists for and writing it down does not make it usable (§10 A2). naming.yaml declares the value so the refusal can name it; declaring is not permission to emit | **3** |
| `the_client_hardcodes_botocores_default_attempts` | `Config(retries={"max_attempts": 3})` instead of `MAX_ATTEMPTS` | one `invoke()` becomes up to three calls. `MAX_ATTEMPTS = 1`, its comment, and the module docstring's claim that the transport is pinned all stay exactly as they are. The damage is invisible and lands in the cost column: `Response.cost()` reports `llm_calls: 1` because the type is one call, so a throttled run bills three times and reports once — undoing §10 A2's zero-retry symmetry underneath it, in the direction where the throttled arm looks cheap | **1** |
| `the_reply_text_is_taken_from_the_first_block` | `_text` reads `blocks[:1]` instead of every text block | reverts the client to the shape the response *looks* like it has. Not hypothetical — it is what was written first and it failed on the first real call: this model returns `reasoningContent` and *then* `text`, so a good reply is reported as having none. Kept as a mutation because the fix is invisible in a fixture written from the API docs, which is why `test_bedrock.py`'s fixtures put a reasoning block first by default | **85** |
| `the_logging_check_reports_an_unreadable_setting_as_clean` | `check_region` returns `(region, CLEAN)` where it raises on `ClientError` | an IAM denial becomes a clean bill of health, the tool appends a dated record for a region it could not read, the client's gate opens on it, and `compliance.md` — cited by the paper's ethics section — carries a measurement nobody made. The worst failure in the pair, because it manufactures evidence rather than losing it, and the plausible edit: `AccessDeniedException` in an unused region reads as noise, and `cloudtrail:DescribeTrails` already returns exactly that for this principal | **4** |
| `conftest_availability_from_a_load` | the shared availability fixture goes back to deciding availability by loading the corpus | the defect that shipped four times, reverted. Changes nothing until a real loader bug arrives, and then hides it: measured alongside `type_in_both_lists`, **93 tests skip and 78 non-passing outcomes become 3**, reported as a green suite | **1** |
| `test_file_shadows_the_shared_fixture` | one test file defines its own `corpus_present`, in the defective form | the propagation rather than the defect: the local definition wins over conftest's silently, and only that file's tests are affected — which is how three files carried it unnoticed | **2** |
| `the_patch_check_credits_a_whole_file` | `satisfies` drops the function name and compares only the file | the verdict becomes a subset test: **one executed function in a module vouches for every patched function in it.** `record_access` runs in a dozen tests, so `tree_state` would be credited without ever running — the exact state the audit found. The check still runs, prints a count and exits 0. The weakening to expect, because it is what a false positive tempts you into | **1** |
| `the_patch_check_credits_a_bare_function_name` | `satisfies` drops the file and compares only the name | the same weakening in the other axis: any `axis` anywhere satisfies `src/corpora/base.py`'s, including one a test defined itself. Separate from the row above because the two are accepted for different reasons — this one looks like tolerance for import aliasing, that one for module copies | **1** |
| `the_patch_allowlist_stops_requiring_a_reason` | the `why` word-count check becomes `if False` | an exemption no longer has to say why, and the fastest way to close a finding stops being *run the function* and becomes *add two lines of JSON*. Nothing breaks today; what breaks is the review in six months, when nobody can tell an impossible case from a Friday afternoon | **2** |
| `a_stale_patch_exemption_is_ignored` | the stale-entry comparison becomes `[]` | an exemption outlives the function it describes and waits to cover whatever takes the name next. The failure mode of every allowlist that is never pruned | **1** |
| `the_arm_freeze_guard_only_checks_the_file` | `orchestrate.freeze_window()`'s refusal becomes `freeze_path().exists()` | **the defect this repository shipped, moved to the arm that will actually run.** `rm window_freeze.json` then re-freeze writes today's hashes and reports success — the sequence `window-freeze-history.md` records running three times. Worse here than for `port-human`: `port-oneshot` writes no per-line hashes, so the record is the *only* thing attesting to the window the call ran under. It is also wrong in the other direction, refusing the pre-call re-freeze §6.3 permits, which is why the count is high | **7** |
| `the_freeze_record_drops_the_empty_block_marking` | `sections_empty` is dropped from the record | the record stops saying which blocks the call did *not* carry, so a reader must derive it from `INPUT_BLOCKS` — and a reader who knows `INPUT_BLOCKS` is not the reader the field is for. `sampling_applied` survives, so the record still distinguishes the two cases and no longer says what the distinction is about | **4** |
| `the_freeze_record_claims_the_sampling_parameters_applied` | `sampling_applied` becomes the constant `True` | the field it was added to prevent, restored: a `port-oneshot` record then claims *n*=40 at ±120 characters governed a call that carried no §1.4 at all. §6.3 keeps `sampling_sha256` for comparability with the arms that do use it, which is exactly why the record needs a field saying the hash did not govern this call | **5** |
| `the_baseline_draws_error_spans` | `orchestrate.freeze_window()` calls `initial_error_pool()` | **DESIGN §4's ladder condition broken in the direction that looks like an improvement.** At iteration 1 the §1.4 pool comes from an empty rule file, so those spans are dev **gold** — the baseline shown 40 of them has dev information `port-loop` call 1 does not, and the two arms differ in two things instead of one. A `port-loop` win is then unattributable at the comparison the paper leads with, and the arm flattered is the rung above. Caught structurally: the plumbing is a two-line addition and a behavioural test notices only once it moves a number | **2** |
| `the_call_is_logged_after_the_response_is_judged` | `append_call(...)` becomes a lambda, invoked just before `run_fold` | the freeze guard's premise read backwards. The log line is what fixes this arm's window and *n*=1 means there are no per-line hashes to disagree with the record, so between the call and the log the window is still re-freezable. After a successful validation the log is byte-identical, so every assertion about its *contents* passes — what breaks is only the ordering, and only visibly in the branch where the response does not load, which returns having made a call, paid for it, and recorded nothing | **8** |
| `a_format_failure_writes_zeroed_metrics_too` | the failure branch calls `run_fold` before writing `format_failure.json` | **§10 A2's central distinction erased.** A format failure now also leaves a `metrics.json`, scored over the bootstrap file, and near-zero numbers are indistinguishable from the opposite finding — a rule set that ran and caught almost nothing. This is why the failure is a *file name* and not a `status` field: an aggregation walking `results/` counts the failure as a scored arm with a bad score, understating capability and overstating compliance in one number | **1** |
| `the_arm_reports_no_model_and_no_cost_to_the_scorer` | `model_record=model, cost=cost` dropped from the arm's `run_fold` call | the published metrics carry `model_id: "none"` — the `naming.yaml` value meaning *no model was used* — and three zeros for cost, for an arm whose whole content is one LLM call. Nothing about the file looks wrong: it is the `R` arm's record written under `port-oneshot`, so the baseline reads as a rules arm that cost nothing. The default is *correct* in `run_fold`, which closes an arm that genuinely calls none, so only a test on this arm's metrics can tell the two apart | **4** |
| `the_failure_record_paraphrases_the_validator` | `error=str(exc)` becomes a fixed summary string | §10 A2's third recorded content stops being evidence. "The response was not a valid rule file" is not checkable and not comparable: a wrong `lang`, a fenced block and an invented matcher key become one row in the appendix. The raw response is still on disk beside it, so a reader can re-derive the message — which is the work the field saved, and the reason nobody notices it is gone | **3** |
| `the_parse_error_quotes_the_line_it_choked_on` | `safe_load(fh)` → `safe_load(fh.read())`, **and** the picked-out fields → `{exc}` | two edits that only leak together, which is the finding. `MarkedYAMLError` prints the offending source line when its `Mark` carries a buffer, and a stream leaves it null while a string fills it — so the stream/string change is a refactor with no visible effect (and it is what every other loader here does), and `{exc}` is the second half. Together they put an LLM's response, which can echo its own §1.4 block, into a message bound for terminals and CI logs that `release_screen.py` never reaches | **1** |
| `the_history_is_pre_seeded_with_this_rounds_rate` | the pending history becomes `(*previous_rates, previous_rates[-1])` | the round's own rate is counted twice, so `improvements` gains a `0.0` that is below δ by definition and counts toward stopping — the arm converges a round early with `iterations` one too high and nothing in the file disagreeing with anything else in it. The name says rounds 1..N and the round is N, so handing over a sequence one short is what looks like the bug | **5** |
| `the_writer_calls_the_stopping_rule_itself` | `run_fold` imports `should_stop` and inlines what `resolve()` does | **no byte of any output changes**, because `resolve()` is exactly that line. §3's pre-registered decision acquires a second home inside the module that publishes it, and the cost is the next edit rather than this one: a writer holding the rule can grow a branch no reader of `src/termination.py` can reproduce. Reads as one indirection removed | **1** |
| `converged_is_stored_beside_the_reason` | `Termination.converged` becomes a dataclass field, set from `reason` by both producers | every published value stays correct and `check_termination`'s cross-check is satisfied, because one producer computes both. What goes is the *mechanism*: `Termination(reason=CEILING, converged=True, …)` becomes constructible, and the hand-assembling caller is who §3's prohibition is about. Frozen hides it — `verdict.converged = True` raises either way | **1** |
| `a_later_round_audits_and_samples_round_one` | `iteration=previous` → `iteration=ITERATION` at `read_spans` **and** `read_errors` | §1.3's predictions and §1.4's error pool come from round 1, which *is* round *N−1* at round 2. From round 3 on the Auditor reports residual PHI against rules two rounds stale while `masked_from_iteration` still says *N−1*, and the sample asks for errors the arm may already have fixed. The constant is the module's own vocabulary, which is what makes it plausible | **2** |
| `a_round_with_no_score_walks_back_to_the_last_one` | `_previous_round`'s refusal gains a loop that decrements to the newest scored round | reads as robustness and dissolves the mechanism §5.5 relies on: a format failure ends the arm by *not writing a score*, so walking back reads round 1's file as round 2's, the history holds one rate twice, and a genuine gap is swallowed by the same edit. What the guard buys is which refusal a reader gets — the round named, or a `FoldRunError` about predictions two modules away | **1** |

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

Six are on `src/llm/bedrock.py` and `tools/check_bedrock_logging.py` (the two lifecycle-probe
mutations added later are counted with their own group below), and they
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

The last five are about the arm that runs rather than about the record it starts from, and they
sort into three shapes worth naming separately.

**An ordering, not a value.** `the_call_is_logged_after_the_response_is_judged` writes exactly
the same log line, with the same contents, to the same path — later. After a successful
validation the file is byte-identical, so no assertion about what the log *says* can see this,
and the group of tests that catches it is the group that drives the arm's failure branch. That
is the argument for testing a format failure end to end even though it is the branch nobody
expects to take: a suite that only ever exercised the happy path would have every field
correct and the ordering guarantee unprotected. What the ordering protects is narrow and real
— the log line is what `called_where()` reads, so until it is written the window can still be
re-frozen, and *n*=1 means there is no per-line hash anywhere to contradict a rewritten record.

**A record that is complete and describes a different arm.**
`the_arm_reports_no_model_and_no_cost_to_the_scorer` and
`a_format_failure_writes_zeroed_metrics_too` both produce schema-valid, internally consistent
files with nothing blank in them. The first writes the `R` arm's record — `model_id: "none"`,
cost zero — under `port-oneshot`, so the LLM baseline reads as a rules arm that cost nothing,
and CLAUDE.md's requirement that cost travel beside quality is satisfied in form by a number
that is false. The second gives a format failure a metrics file too, at which point the
appendix's format-failure *rate* stops being computable from the directory: the state that A2
reports is defined by the absence of `metrics.json`, which is why it is a filename and not a
`status` field. Both mutations are reachable precisely because the value they substitute is
*correct somewhere else* — `run_fold` closes an arm that genuinely calls no model, and zeros
are the honest record there. Neither can be caught by a validator, only by a test that knows
which arm it is looking at.

**A guarantee that was safe by accident.** `the_parse_error_quotes_the_line_it_choked_on` is
the only two-edit mutation in this group and the reason it needs two is the finding.
`pyyaml`'s `MarkedYAMLError` prints the offending source line when its `Mark` carries a
buffer, and whether it does is decided by how the input was handed in: `safe_load(fh)` leaves
it null, `safe_load(fh.read())` fills it. So the loader's message was already careful *and*
would have stayed clean under `{exc}` — until the day someone switched to reading the file to a
string, which is the form every other loader in this repository uses and a change with no
visible effect of its own. Two harmless-looking edits, in either order, and the second one
completes a leak of an LLM response — which can echo the §1.4 block of its own prompt — into a
message bound for terminals, CI logs and stack traces that `release_screen.py` never sees. The
loader therefore picks the parser's fixed phrasing and the mark out by hand rather than
interpolating the exception, and the mutation is what says that choice is load-bearing rather
than fussy.

### The lifecycle probe: four mutations, three of which make the code nicer

Added 2026-08-11 with the probe itself, and the group is unusual because **the thing being
protected is optional metadata.** `GetFoundationModel`'s record is supplementary — nothing
in the paper's numbers depends on it — so the ordinary argument for a guard ("a wrong number
gets published") does not apply. Three of the four mutations are edits a reviewer would
wave through, and two of them make the code read *better* than the real version.

`the_lifecycle_probe_can_abort_the_arm` narrows `except Exception` to `except ValueError`.
Every style guide in the world is on the mutation's side; the bare `except` is the thing you
are taught not to write. What it costs is the arm. The freeze is already taken by the time
the probe runs (`freeze_window` is step 1), so a probe that raises leaves a frozen window, no
call log line, and a metadata endpoint as the cause of death — for a field nobody would have
missed. The four live failure modes are `ClientError`, a credentials error, a `KeyError` from
a changed envelope and `ImportError` with no boto3, and none of them is a `ValueError`. The
probe is placed *before* the call for exactly this reason and the mutation converts that
placement from a safety into a liability, which is the sharpest version of "a guard whose
cost is paid by the thing it was meant to protect" in this file.

`the_probe_error_carries_the_exception_message` swaps `type(exc).__name__` for `str(exc)`,
which is what anyone debugging would want. This dict reaches three files and one of them is
`agent_calls.jsonl`, which `release_screen.py` deny-lists — so nothing screens what lands
there. CLAUDE.md's rule about exception text is not conditional on the exception being about
a span; a rule that varies by context is a rule whose violations go quiet.

`the_lifecycle_block_moves_into_the_run_block` is the one to read twice, and **it survived
its first writing.** It spreads the lifecycle dict into `run_fold`'s run block — one line,
tidier than the real code, which threads an argument through two functions to keep it out.
What it produces is `start_of_life_time` sitting beside `model_id_resolution`, where a reader
takes a catalogue timestamp as evidence for a resolution verdict. That is **the sixth family
reintroduced as data**: a claim of resolution that nothing resolved, filed where no comment
can warn anyone, because it reaches `metrics.json` and a reader who never opens the module.

It survived because the guarantee had been credited to `MODEL_FIELDS`, and `MODEL_FIELDS`
does not reach it — that check constrains `model_record`, and the mutation spreads a
*different* argument into the same dict one line below. Worse, `MODEL_FIELDS` had no test
anywhere in the suite: a closed set nothing exercised, cited in a docstring as the mechanism.
Two gaps of one shape, and the shape is the recurring one in this file — a guarantee
attributed to a mechanism that does not touch it. The repair added
`test_model_record_is_a_closed_set` (the check finally has one) and
`test_the_lifecycle_record_stays_out_of_the_run_block` at the writer's own layer, rather than
relying on the two downstream tests in `test_orchestrate.py` that were already there.

`an_empty_lifecycle_mapping_is_written_as_no_probe` is the smallest of the four and the
easiest to argue against: an empty dict and no dict produce the same file, so refusing one
looks like pedantry. The two states are "no probe was made" (the `R` arm, which calls
nothing) and "a probe was made" — the failing case has its own record, `status: unavailable`.
An empty mapping means a caller assembled the block by hand and lost that distinction, and
writing it as absent files the arm as having called no model. It is also the reason
`SCHEMA_VERSION` moved for an *optional* field: absence has to stay readable, and it only
does if every writer means the same thing by it.

### The termination mutations: a rule with no wrong number today

Added 2026-08-12 with `src/termination.py`, and the group has a property none of the others
do: **two of the three produce no wrong number on any corpus that currently exists.** The
stopping rule was pre-registered before `port-loop`'s first call (DESIGN §3), so at the time
these were written there was no run for them to corrupt. That is the argument for mutating it
now rather than when the arm runs — a pre-registration that only becomes checkable after the
arm has run is a pre-registration whose code nobody verified while it still mattered.

`delta_reverts_to_the_constant_half_point` collapses `max(delta_floor, delta_spans / n_dev)`
back to the bare `0.005`, and **the number does not change.** es-meddocan's dev fold holds
5,254 in-scope spans, above the 5,200 crossover, so the floor branch is binding there and both
versions return 0.005. Every test that asks the real split file passes. What the edit removes
is the invariant §3 pre-registered: the standard held constant across corpora is a span
*count*, and the rate is derived from it. A single fixed rate demands 26 spans on
es-meddocan and 1.62 on GraSCCo's 1,297-span corpus — a strict standard on the large fold and
none at all on the small one, where 1.62 spans is inside the fold's own noise, so the arm
there would terminate on variation and report it as convergence. It is the seal mutations'
shape moved into a threshold: the figures are real, they are simply computed against a
standard that means something different on each corpus, and nothing in the output
distinguishes them. Only tests on synthetic fold sizes can see it, which is why
`test_delta_is_the_span_count_on_every_fold_size` exists in that form — a δ test that only
ever asks es-meddocan is a test this edit passes.

`a_ceiling_stop_is_recorded_as_converged` changes one word in the `elif`, and the mutated
file is **internally consistent**. `Termination.converged` is a property derived from
`reason`, so it agrees; `check_termination`'s cross-check compares the two and finds them
agreeing; the block is schema-valid and `iterations` still says 8. Nothing in the published
file contradicts anything else in it. This is why the §3 prohibition is tested in three
places rather than one: the derived property prevents a *contradictory* record and does
nothing about a *wrong* one, and the scorer's consistency check is satisfied by this edit
too. Only a test that reads the verdict for a still-improving arm at the cap sees it. The
repair for it must not be "check the ceiling first" —
`test_convergence_wins_when_both_are_true` pins the other direction, because an arm whose
k-th thin iteration happens to be its 8th did converge, and reclassifying it as a budget
exhaustion would understate the rule in the opposite direction.

`k_drops_to_one_so_consecutive_means_nothing` is the one with an argument on its side, and
the argument is cost. Each k is a full RuleAuthor + Auditor + scorer pass spent purely to
confirm a stop — roughly 135k tokens by §3's estimate — so k = 1 reads as saving one
iteration in every run. What it buys is arms that stop on sampling variance: the error sample
is a seeded draw of 40 spans stratified by type, so one iteration can land on a stratum the
current rules already cover and produce a below-δ improvement for reasons unrelated to the
arm running out of ideas. It also makes the word "consecutive" vacuous while leaving it in
the spec's wording. The edit is in `config/naming.yaml` rather than in a module, which is the
rule about vocabulary going into the config first doing its job: the collision is visible in
one committed file rather than distributed across callers.

### The iteration-scoped paths: three mutations where the deny rule still fires

Added 2026-08-12 with `paths.itermetrics` / `iterspans` / `auditreport` / `itererrors`
(DESIGN §5.5). One `iter{n}/` directory holds four files with two classifications — the
round's score and predictions are publishable, the audit report and the per-span error
export are not — and every mutation here attacks the boundary between them rather than the
screener as a whole.

`the_audit_report_is_allowed_instead_of_denied` does not delete a pattern. It rewrites the
deny entry into an ALLOW-shaped one, four axes and an iteration deep, identical in form to
the score sitting beside it — so the edit reads as *tightening*, being strictly more specific
than the line it replaces. **The content sniffer does not save it**, and that is the part
worth stating plainly: the report holds offsets, types and scores and no surface forms by
construction, which makes it exactly the kind of file `sniff()` passes. §6.1's allowlist
argument runs the other way — a path may be excused from the sniffer only where the path
rules already publish it, and nothing requires publishing a list of positions an agent
believes are surviving PHI in a DUA corpus. The mutation also produces no BLOCKED line,
because `.gitignore` still covers the filename: an ignored-but-not-denied path is reported as
Quarantined, which is the class that reads as fine.

`the_iteration_allow_pattern_covers_the_whole_directory` reaches the same publication from
the other side and is the *shorter* edit — one ALLOW pattern on `iter[0-9]+/` instead of two
on filenames. Today it changes no reported classification at all, because `deny()` is
consulted before ALLOW and both denied files are still caught. What it changes is that the
two lists now contradict each other, and the contradiction is invisible until somebody edits
either one. That is the whole reason these patterns anchor on filenames: the directory is the
wrong unit when it holds two classes, and a screener whose ALLOW list *would* publish a
denied file is one deletion away from doing it. So the test asserts the denied two match **no
ALLOW pattern**, not merely that they are denied — a test written the weaker way passes this
mutation and every future variant of it.

`the_per_iteration_key_replaces_the_arm_level_one` restores the bullet §5.5 had to correct.
The superseded text said `paths.metrics` gains `{iteration}` *and* that the un-iterated path
stays valid for the single-call arms; a template is either formatted with an iteration or it
is not. Taking the first half orphans two **committed** files —
`port-oneshot-nofence/metrics.json` and `spans.jsonl` at four axes deep — leaving them matched
by no ALLOW entry and reachable from no `metrics_path()` call, which is exactly the migration
§4 refused for a freeze record. This one belongs to the same family as the two above and to
the seal mutations further up: nothing it produces is malformed, and the damage is to files
that already exist and are not being looked at.

### The per-span error export: five mutations, and none of them changes a number

Added 2026-08-12 with `scorer.error_spans()` and `run_fold.write_errors()` (DESIGN §5.5).
The export is the pool an iterating arm's next window is drawn from, so its failure mode is
unlike the rest of the scorer's: **nothing in `metrics.json` moves.** `score()`'s return is
deliberately not widened, so the published numbers are computed from the same matchings
either way and stay correct under every edit below. What breaks is what the agent is shown,
and the arm then spends its rounds — each one a full RuleAuthor + Auditor + scorer pass —
moving something else.

Two of the five attack the **referent** and two attack the **verdict**, which is the split
the step's instruction named.

`the_export_index_is_the_in_scope_position` swaps `g.span_index` for the loop variable `gi`,
and every value it produces is a valid index. `gi` counts the in-scope subset; the reference
DESIGN §11.2 fixes counts the document's own `spans` list, excluded types included. On
MEDDOCAN the two differ on most documents and the offset varies per document with how many
§9.1 spans came before. The file validates, the offsets beside each index are untouched and
correct, no metric changes at all — the index enters no numerator or denominator — and the
wrongness is visible only to whoever holds the corpus and resolves the reference. That is
the same property that made this the referent in the first place: inert to everyone else,
and therefore un-noticeable by anyone else. The cost is that `(doc_id, span_index)` stops
meaning one thing across rounds, since `initial_error_pool()` enumerates the unfiltered list
for iteration 1. The test that catches it puts the excluded span **first**, so a filtered
index names span 0 for a span that is span 1 — a fixture whose excluded spans all sit last
passes this mutation.

`the_export_reads_the_missing_index_as_zero` turns the refusal into a default. In the form
written here it fails loudly, on a `TypeError` from `ErrorSpan.__post_init__` comparing
`None` with `<`; that is the mutation's tell and not the guarantee, because
`item.span_index or 0` is the same edit silently. Every span with no index would then be
exported as span 0 of its document, which resolves to a real span and to the wrong one. The
reason to refuse is that there is nothing to substitute *from*: the only number in hand at
that point is the in-scope position, which is the mutation above.

`missed_is_the_unmatched_gold_rather_than_the_uncovered` is the verdict computed on the
assignment instead of on coverage, and it is the one that reads as **more thorough**: every
uncovered span is unmatched, so the export grows to the leak set plus `assignment_slack`.
Every span it adds is an identifier that is already hidden. D1's gold `[0, 4)` is covered by
one wide prediction and loses the assignment to its neighbour; shown to a rule author as
missed, it asks for a rule against text that is already masked. So the arm writes rules for
non-leaks, the leak rate stops moving, and the run terminates on δ having improved nothing —
while the window's own header still tells the author "missed = leaked under fully_covered"
(`rule_author.md` §1.4). The prompt asserts the definition the data no longer satisfies.
This is DESIGN §9.3's two-matchings argument arriving in a place that is not a metric.

`both_halves_of_the_export_use_one_mode` is the tidier-looking constant — two matchings over
one mode instead of two — and it leaves `missed` correct, so the stopping rule still works.
The other half inverts: under `fully_covered` a prediction covering most of a gold span is
unmatched and would be exported as a false positive, while the *published* precision
(`relaxed`, per `HEADLINE_MODE`) counts it as a hit. The author is shown correct predictions
as errors, narrows rules that are already scoring, and the arm degrades the number it is
optimising. Every file stays internally consistent, because the export agrees with
`modes.fully_covered.overall.fp`, which is a real figure in the same `metrics.json`. This is
why `ERROR_MODE` is *derived* from `HEADLINE_MODE` rather than written as two literals: the
headline choice belongs to the reporting layer and may move, and the window has to follow it.

### Three on the round's record, and two of them look like the tidier design

Added 2026-08-12 with the `paths.itermetrics` / `paths.iterspans` writer (DESIGN §5.5). An
iterating arm's round produces three files — predictions, score, errors — and they are one
record: scoped together, or the record has a hole in it. All three mutations here are ways of
producing a tree that looks complete.

`the_round_s_files_are_written_by_every_arm` is opt-in becoming always-on, in the silent
form, and it needs four edits together (`also=`) to be faithful to its name — the bare
`if True:` fails on `iter{iteration}` formatted with `None`, and `or 1` at each of the three
writes is what makes it quiet. Then every arm on every corpus grows an `iter1/` directory
holding a copy of its score, a copy of its predictions, and a list of the positions of every
missed identifier in the fold. `port-oneshot-nofence`'s `metrics.json` and `spans.jsonl` are
committed at four axes, so this puts a second copy of a published result beside them, and
`iter1` under an arm with no rounds is a false statement about the arm. The error list stays
unpublished — the deny rule and the `.gitignore` entry hold — but the other two are
*allowed*, which makes them the worse half: a duplicate of a committed result reaches a
commit with nothing objecting. This is the direction §5.5 decided against, and the decision
is recorded on `run_fold` rather than in a commit message because both readings are
defensible (a uniform tree is the argument for). Two tests walk the whole results tree rather
than checking a path either could name.

`only_the_score_is_scoped_to_the_round` is the design §5.5 corrected before either key was
implemented, restored: scope `metrics.json`, leave `spans.jsonl` arm-wide. It is the smaller
change and it loses more than scoping nothing would. Every round's score survives and every
round's error list survives, so the record reads as complete — but `iter{N}/errors.jsonl` is
*derived* from round N's predictions against gold, and those predictions are overwritten by
round N+1. From round 2 onward the arm holds a list of missed identifiers that nothing can
re-derive or check against the spans it came from, and the one file that could contradict it
is gone. Nothing about what remains looks wrong: each round's `metrics.json` is internally
consistent, and the arm-wide `spans.jsonl` is a valid prediction file for *some* round. That
is §5.3's rule-file argument one artefact over — an overwritten record is visibly gone, an
overwritten premise leaves a complete file behind whose input no longer exists.

`the_final_rounds_duplicate_comes_from_a_second_scoring` **changes no byte of any output on
any corpus in this repository**, and it is here because that is the failure. §5.5 duplicates
the final round's score and spans at the un-iterated paths, and the guarantee is not "the two
copies happen to match" — it is "there is one scoring pass, so they cannot differ". A second
detection and a second scoring for the un-iterated pair removes the guarantee and leaves the
property, because detection is deterministic today. The day something moves — a rule file
edited mid-run, a corpus re-exported, or a detector with any non-determinism in it, and the
`RT` and `T` arms are on the ladder — the two files disagree with **neither looking wrong**:
each internally consistent with its own pass, run and cost and termination blocks identical
in both, nothing recording which pass produced which. There is then no way to say which
number is the arm's headline, which is exactly what §5.5's duplication was for.

So this one is **not** caught by
`test_the_final_rounds_duplicate_is_byte_identical_to_the_round_copy`, which is what a reader
can check on a finished run and which passes under the mutation. It is caught by
`test_the_fold_is_detected_once_and_scored_once`, which counts the calls. Both tests stay: the
byte comparison is the property §5.5 promises, the call count is the mechanism that delivers
it, and the mutation is the reason to know which is which. Written this way after the first
draft of the mutation — a second pass spelled out inline — was about to be filed as caught by
a test that cannot see it.

### The Auditor's validator: four mutations, and each one makes the report look better

Added 2026-08-12 with `src/porting/audit.py` (`docs/prompts/auditor.md` §2.3). The
validator's failure mode is the opposite of the scorer's: nothing here is a metric at all, so
no edit below is visible in any published number. Three of the four make the report read as
*cleaner* — fewer refusals, more surviving flags — which is the direction a reviewer would
approve of.

`an_unknown_flag_field_is_ignored_instead_of_refused` drops the whitelist and keeps the
required-field check, which is how most JSON consumers work and reads as permissive in the
harmless direction. The field it starts ignoring is the one carrying the text. `auditor.md`
§3 strips every free-text field from a flag because any justification for a span is a
description of that span's text and the shortest honest one is a quotation, so the natural
addition is `"reason": "the name after 'Dr.'"` with the name in it. Ignored is not written —
today. The failure is that the next edit to the prompt or to the assembler can start carrying
it and nothing objects, which is `write_errors()`'s whitelist rule (DESIGN §5.5.1) in the one
place where "the day it is added" means publishing a residual identifier from a DUA fold into
a file under `results/` in a public repository. Eight tests catch it, one per field name a
helpful model would reach for.

`an_out_of_range_column_is_snapped_to_the_line` clamps instead of refusing, and this is the
one worth reading twice: it is indistinguishable from robustness. The clamped flag sits at a
position the agent never claimed, `counts.refused` falls, and the report looks like a round
where the model did better. It also destroys the diagnostic the count exists to be — a round
where the model lost the coordinate scheme should be a number, and clamped it becomes ordinary
flags at line ends. `test_nothing_is_repaired` exists because every individual refusal test is
also consistent with a validator that repaired some *other* case.

`a_flag_overlapping_a_mask_tag_is_kept_when_it_is_not_contained` turns overlap into
containment. It still refuses flags inside a tag — which is what the reason is named for — so
it reads as a tightening. What survives is a flag partly over a replacement, whose translated
offsets are wrong because the part inside the tag has no document counterpart. The surviving
flags are *plausible*: they point at real text immediately beside a detected span, which is
where a missed identifier often sits, so the wrong offsets arrive looking like the report's
most credible entries. The paired test that a flag *touching* a tag boundary is kept is what
stops the fix from being "refuse anything near a tag", which would lose the common case.

`the_report_reads_its_own_round_as_the_masked_one` lets `masked_from_iteration` equal the
current round. Every number in the file stays consistent, because the field is a label on the
derivation rather than an input to it — flag counts, per-type counts and the marked sample all
come out identical, and the file's arithmetic agrees with itself. What breaks is the one
question the field answers, and it breaks in the flattering direction: the arm appears to have
audited fresher output than it did. The permissive form is exactly what a caller reaches for
while wiring the loop driver and unsure which round number they are holding.

### Two more on the mask map, guarding a component that does not exist yet

Added 2026-08-12, when checking the validator's contract from the masker's side found that
`_to_document()` had a precondition nothing enforced. Both mutations target `_check_tags`, and
what makes them worth having is that **the bug they catch belongs to a module nobody has
written**. The masker is next; these are the two ways its output can be wrong while producing
a report that reads correctly.

`tags_out_of_order_are_sorted_instead_of_refused` replaces the ascending check with a
`sorted()` in `MaskedLine.__post_init__`. Every existing call still returns the right offsets,
because sorting *is* the repair — that is what makes it the tempting edit and the wrong one.
`_to_document()` walks the tags once, left to right, stopping at the first tag ending after its
column; measured before the check existed, the same two tags reversed translated column 5 to
document 5 rather than 12, silently. **The masker applies replacements right-to-left**
(DESIGN §3), so descending is its *natural* emission order — precisely the one the walk reads
wrongly. A sort fixes that emission on every call and thereby hides it permanently: the masker
can emit in any order forever and no test, no run and no report will say so, until something
else consumes the same map and does not sort. Refusing sends a caller bug back to the caller,
which is the division `AuditError` already draws between an agent's mistake (data, counted)
and the harness's (an exception).

`overlapping_mask_tags_are_accepted` keeps the ascending check and disables the non-overlap
half. Two tags may then share columns, `_to_document()` double-counts the shared ones, and
every column past the overlap translates too far by exactly its width — a number, not a
failure, on flags that look like all the others. The input that produces overlapping tags is a
specific and likely masker bug: emitting one tag per overlapping *span* rather than one per
union of extents, which is the rule DESIGN §3 fixes exactly because `RuleSet.detect` preserves
overlaps by design. `test_adjacent_tags_are_not_refused` guards the other side, and it is not
a formality: es-meddocan's dev fold has 393 gold pairs within one character of each other, so
tag-abutting-tag is the ordinary case and a check that refused touching tags would refuse
ordinary documents.

One incidental result worth recording, because it is the argument for writing the check at all.
Adding `_check_tags` failed a test that had passed for as long as it existed: a fixture built a
6-character tag on a 5-character line. It was wrong and inert — nothing translated a column on
that line — and it would have produced a wrong offset on the day a flag landed there. The check
found a latent inconsistency in the tests before it ever saw the masker's output.

### One on the widened window, where the damage is a false alarm

`drift_is_checked_against_todays_window_not_the_recorded_one`, added 2026-08-12 with the
widening of `WINDOW_FILES` to three files (`auditor.md` joined it — DESIGN §5.5). It restores
the pre-widening line in `orchestrate.window_drift()`: iterate the fields of today's
`window_hashes()` rather than the fields the record itself names.

The interesting thing about this one is that it produces no wrong number and no crash. It
produces a *report*, and the report is a false alarm on both frozen agent arms
(`port-oneshot`, `port-oneshot-nofence`): permanent drift on `auditor_sha256`, a file neither
of their calls ever saw. What makes that worse than a crash is what `window_drift()` is
documented to mean — the record and the files disagree about a call that has already happened.
A reader who trusts that reads it as the record being wrong, and the record is the only thing
in the repository that still says what those calls were held to. The repair such a reader
reaches for is re-freezing, which hashes today's files onto a record about last week's call:
DESIGN §6.3's objection, arrived at by way of a helpful-looking warning.
`docs/notes/window-freeze-history.md` is a record of that exact sequence happening for other
reasons, three times.

**It was first written against the wrong line, and that is worth recording.** The first version
targeted `recorded_window_fields()`, replacing its `record.get("files")` with `None` to force
the fallback branch — and it survived, killed by nothing. The reason is that the fallback
answers correctly on every record that exists: it returns the hash fields *present* in the
record, and the committed two-file records hold exactly two. So the `files` list is not what
makes the widening local; it is a second, stricter authority for records that will be written
later. The line that actually holds the guarantee is the one in `window_drift()` that chooses
which list to iterate. A surviving mutation is usually a missing test; this one was a wrong
belief about where a guarantee lived, and only running it distinguished the two.

`the_recorded_files_list_is_ignored_in_favour_of_the_fields_present` is that first version,
kept rather than discarded, because the branch it removes does do something — just not
anything the committed records can show. The two branches diverge on a record with two files
named and three hashes written, which is what a widened writer produces against an old freeze
record. `test_the_files_list_outranks_the_hash_fields_present` is that synthetic record; the
committed ones stay in the file above, testing the thing they can test. The pair is a small
lesson about coverage from real artefacts: they are the only evidence for what *did* happen and
no evidence at all for a branch that exists so something does not.

Caught by `tests/test_window_widening.py`, which reads the committed `window_freeze.json`
files rather than building any. That coupling to committed data is deliberate and is the whole
test: every other freeze-record test in the suite builds its record under a redirected `ROOT`,
and a record created inside the test cannot fail to be retroactively rewritten. A
fixture-based version of this test passes with the mutation applied.

The second half of the widening — `human_arm.log_line()` writing three hashes onto a retired
arm's line — is not mutated here, because it is not silent: `log_line()` asserts its own field
order against `FIELDS`, and the naive widening failed 39 tests immediately. Worth noting that
that assertion was written for an unrelated reason (a reader diffing two lines should see
field changes rather than reordering) and caught this by accident. A guard that catches what
it was not aimed at is luck, not coverage, which is why the freeze records get a test of their
own rather than being left to it.

### Two on the masker, and the component whose absence the section above predicted

Added 2026-08-12 with `llm/prompt.mask_document()`. The section two above wrote its two
mutations against `_check_tags` for a module nobody had written yet and named the two ways its
output could be wrong while reading correctly. These are those two ways, now written from the
masker's side — one for each — and the pairing is the point: the validator refuses, and these
say what it refuses.

`a_heterogeneous_union_prints_one_of_its_types` replaces the tag decision in `_close()` with
`sorted(phi_types)[0]`. A union whose spans disagreed then prints the alphabetically first type
instead of the tag that names none. **Nothing downstream notices.** The tag is bracketed, the
geometry is untouched, `_check_tags` passes, the Auditor reads it as a tag and does not flag it
(`auditor.md` §1.2 — a tag is not a candidate), and no offset moves. The masked text looks
correct and one component has quietly started resolving overlaps, which is the failure DESIGN §3
was written to prevent before there was a masker to prevent it in. `RuleSet.detect` preserves
overlapping matches exactly so merge policy stays a replaceable strategy compared on identical
detections (§4, §9.3); a policy baked in here runs inside every `port-loop` arm no matter which
policy that arm was configured with, and the arm's record would not say so. `sorted(...)[0]` is
the shape the accident really takes — not a considered precedence order, just whichever type the
implementation reached first.

What makes it worth having is which of the two guards catches it. `n_heterogeneous_tags` is
computed from `phi_types`, not from the tag text, so the mutant reports the right number while
printing the wrong string: the count says one heterogeneous union and the text names a type. A
suite that checked only the counts would pass. Both are asserted for that reason, along with
`test_the_heterogeneous_tag_is_read_from_the_config` — `tests/test_masked_tag.py` already
forbids `[PHI]` as a literal anywhere in `src/`, and `TAG_FORM` is deliberately not derived from
the config value, so the two strings cannot drift into agreement by construction.

`the_mask_tags_are_emitted_in_the_order_they_were_applied` drops the `reversed()` in
`mask_document`'s single conversion from the right-to-left walk to left-to-right columns. The
tags then come out descending. Every offset is still correct; only the order is wrong. This is
precisely the input `tags_out_of_order_are_sorted_instead_of_refused` exists to refuse, and the
two mutations together close the loop: with the sort in place this one produces silently wrong
translations, and with the refusal in place it produces an `AuditError` — a caller bug sent back
to the caller rather than repaired into a double-counted column.

**It survives most tests, and the reason is the one worth recording.** On a line with one tag or
none the output is byte-identical — which is most lines of any real document and almost every
small fixture. Only a line carrying two tags distinguishes mutant from original. So the ordering
test is paired with
`test_the_masker_emits_more_than_one_tag_per_line_so_the_order_is_testable`, which asserts the
fixture actually puts two tags on one line. Without it every ordering assertion and every
round-trip passes on the mutant and the whole check measures nothing. That guard-on-the-guard is
the same shape as `test_adjacent_tags_are_not_refused` in the section two above: a test whose
job is to keep another test from becoming vacuous.

One thing neither mutation can be aimed at yet. The masker's overlap handling is the union rule,
and on es-meddocan dev today there is nothing to union: the committed 3-rule `rules/es.yaml`
predicts 0 spans over the 250 dev documents, so 0 overlapping pairs. Gold cannot stand in
either — 5254 in-scope dev spans, 0 overlapping pairs, 0 type disagreements, because annotations
do not overlap by construction. Both mutations are therefore killed by fixtures alone until a
`port-loop` arm runs, and `n_overlapping_pairs` is on the `MaskedDocument` counts so that the
first arm measures it rather than a later reader assuming it. Recorded in DESIGN §3.

### One on the path block, and it is the near-miss rather than an invented one

Added 2026-08-13. `the_audit_report_gets_a_second_path_key` adds a `paths.leakreport` to
`config/naming.yaml` formatting to the byte-identical path `paths.auditreport` already names.
It is not a hypothetical: the loop's implementation order called for exactly that key, on the
reading that DESIGN §5.5's two bullets about `reports/leaks_{iter}.json` — deny-listed, and
axis- plus iteration-scoped — described a path still to be declared. They described
`auditreport`, declared and screened in `c998610`. So this mutation is the tree as it would
have stood had the order been followed literally, and what it demonstrates is how little
would have objected.

**Every layer that looks like it should catch it passes, and each for a good reason.** The new
key formats to a real path under a real arm at a real round, so `path_template()` resolves it.
`deny()` denies it and `.gitignore` ignores it, because both are anchored on the filename
rather than on the key — which was the right call for the `iter{n}/` directory and is exactly
why a second key inherits the protection for free.
`test_the_four_iteration_scoped_paths_split_two_and_two` names its four keys as literals and
does not walk the block, so a fifth is outside what it can see; the deny-sample table is keyed
on patterns and is untouched. The screener reports nothing, correctly: there is nothing wrong
with the path.

**The damage is one layer up and it is worse than the defect §5.3 rejected.** An axis-free path
makes two arms collide on one file, and the collision is visible — a reader looking at the path
sees whichever arm wrote last. Two keys for one file collide on nothing: a driver holding
`leakreport` and a validator holding `auditreport` agree on every byte, forever, until one of
them is moved and the other is not. Nothing in the file records which name produced it, and
DESIGN §3's "two agents never write the same file" stops being checkable, because the file has
two names and neither of them is wrong. That is the property `test_no_two_path_keys_name_one_file`
asserts, over the whole block and on the *formatted* result — the template strings differ, so a
comparison on templates would pass this mutant, and so would one that had merely renamed
`{iteration}` to `{round}` in a copied line.

`min_kills` is 1 because one test is the honest count: this is a gap nothing else was covering,
which is the reason the finding is recorded in DESIGN §5.5 rather than fixed by declaring the
key and moving on.

### Two on the call log's new field, where one is a value and the other is only an order

Added 2026-08-13, with `role` on `call_line()`. RuleAuthor and Auditor share one
`agent_calls.jsonl` from `port-loop` round 2, `llm_calls` sums their lines — the right total
for cost and no answer at all for attribution — so each line has to say whose call it was.
Both mutations leave a log that parses, and neither changes a number in `metrics.json`.

`the_call_role_is_written_without_being_validated` drops `check_agent_role()` and writes the
argument through. This is the defect `tests/test_agent_role.py` describes as having no symptom
in the file: `"RuleAuthor"` at one call site and `"rule_author"` at another produce a
well-formed log in which one agent's calls total as two. It is worth a mutation rather than
only a unit test because of where the wrong value would come from — `port-loop`'s driver passes
a role at each of two call sites, in a module that also handles `porting` and template
filenames, all strings and none interchangeable. Caught by
`test_a_role_outside_the_vocabulary_is_refused_at_write_time`, which is deliberately *not* a
test of the near-spellings; those belong to the validator's own file. What this one asserts is
that `call_line()` is on the validated path, and that is the half a test of the validator
cannot see.

`the_role_is_appended_at_the_end_of_the_line` is the one worth arguing about, because it looks
like the tidier diff: same fields, same values, same validation, `role` after `generated`
instead of beside `iteration`. Nothing about a single line changes. What changes is the log as
a document — `(iteration, role)` is what a per-round per-role total groups by, and those two
keys adjacent at the head of the line are what make twelve lines of a six-round two-agent run
legible by eye. Between a timestamp and three window hashes they are not. The same argument
`human_arm.FIELDS` makes about its own tail. `test_the_role_sits_beside_the_iteration` is the
only test in the suite that catches it, which is the point: field order is the property a
reviewer reads past, and it is not implied by anything else that is checked.

Both are `min_kills=1`, and the frozen-record half of the change — that the two real
`agent_calls.jsonl` lines did not acquire the field — has no mutation, because there is no edit
to `src/` that produces it. A backfill is a script someone runs once; the file it would rewrite
is not reachable from any function here. What the two mutations above cover is the writer, and
the record is covered by reading it.

Worth stating because it nearly went the other way: those logs are gitignored, and the first
draft of this section said the tests that read them skip inside the mutation tree. They do not.
`COPIED` includes `results/`, and `shutil.copytree` copies what is on disk rather than what git
tracks — so the frozen lines are present in every mutated tree and their tests run there. The
baseline count moved 1330 → 1344 when `tests/test_call_role.py` joined `TEST_FILES`, which is
that measured rather than assumed. The gitignore only means the logs are absent from a *fresh
clone*, which is what the skip in that file is for.

### Six on the iteration prompt, and the first two are the same off-by-one twice

Added 2026-08-13 with `assemble_iteration_prompt()`. The function has one job that can be
stated in two numbers — round 1 is the baseline's prompt, and from round 2 every §1 block is
filled — and every mutation here is a way of getting one of those numbers wrong while
producing a prompt that sends, costs what a round costs, and reads correctly.

**`the_audit_report_is_read_as_the_previous_rounds_file` is not invented.** `_audit_block()`
shipped with it. The check was `report["iteration"] == iteration - 1`, on the reasoning that
the report is the previous round's — which is true of the *predictions* it describes and false
of the file. `auditor.md`'s banner fixes the handover: the Auditor runs as round *n*'s first
step, so its report is written to `iter{n}/audit_report.json` carrying `iteration: n`, and what
it read was round *n−1*'s `spans.jsonl`, carried as `masked_from_iteration: n−1`. Two numbers on
one file. A reader demanding the first be *n−1* refuses the correct report and accepts the
round-old one, and the refusal message reads plausibly enough that a driver written against it
would be written to pass it — by handing over the stale file, which is the artefact the check
was there to reject.

What follows is quiet. The prompt carries round *n−2*'s flags under a heading naming *n−1*, the
agent reads them against a rule file two revisions newer, and every flag it already fixed
reappears. `auditor.md` §5 turns on a mechanism DESIGN §3 states outright — a residual flagged
at round 3 and fixed at round 4 is *masked* at round 4 and cannot be flagged again — and that
mechanism is what this switches off, silently, in the direction that looks like the Auditor
disagreeing with the fix. Nothing downstream objects: `audit.report()` validated the pair
against each other rather than against the round reading it, so both files are internally
consistent, and the reference form faithfully records the number it was given.

`only_the_round_the_report_names_is_checked` is the reason the reader checks both numbers rather
than one. Verify `iteration` against this call and trust `masked_from_iteration`, and a driver
that is consistently off by one passes: it calls the Auditor on the wrong round's spans, records
the relationship `report()` demands, and produces a file this reader accepts. `masked_from` is
also what the heading is rendered from, so the check and the sentence the agent reads come from
the same field — which is the argument for reading both and for rendering the one that names the
predictions rather than the directory.

**`round_one_reassembles_the_baselines_prompt` passes the behavioural test.** It inlines the
whole of `assemble_task_prompt()`'s body into round 1's branch — every sentence, both
constants, the same reference form — so the two prompts hash identically and
`test_round_one_is_the_no_feedback_prompt_byte_for_byte` is green. It breaks on the next edit to
anything §§1.1–1.2 are made of: a widened `_task_frame()` reaches `port-oneshot` and not
`port-loop` round 1, the two rungs diverge in a way no record names, and the measured difference
between them stops being feedback. DESIGN §4's claim can rest on one code path or on two
implementations somebody remembers to keep equal, and this is the mutation that shows which of
those the repository has. Caught structurally, by
`test_round_one_delegates_rather_than_reassembling`, because that is the only kind of test that
can tell the two apart today.

`round_one_ignores_the_feedback_it_was_handed` removes two characters: `if value` for
`if value is not None`. Idiomatic, and wrong for precisely the values a round-1 call plausibly
carries — `{}` from a metrics block that was read and empty, `[]` from an error pool that came
back with nothing, `0` from a context width. All falsy, all dropped, and what comes out is a
correct round-1 prompt from an incorrect call. That is the asymmetry the refusal exists for:
there is no round 0, so a caller holding this data computed it somewhere else, which means its
counter is off by one for every round that follows, and round 1 is the only round where the
discrepancy is visible at all. The same two characters carry the mirror check one branch down,
where the direction reverses — an empty error pool at round 3 is a *supplied* block and must not
be reported as missing — so `test_a_later_round_accepts_an_empty_block_that_was_supplied` is
half of this guarantee and the parametrised refusal test is the other half.

`an_undefined_rate_prints_as_zero` renders the scorer's `None` as `0.000`. `None` means
undefined — a rate whose denominator is zero, which is what a type with false positives and no
gold in this fold has — and as a number it becomes the best possible score, in the column the
agent scans for what to work on. The direction is what earns it a mutation: an agent shown
`leak_rate 0.000` for a type the fold cannot score concludes the type is handled, and the types
this happens to are the sparse ones DESIGN §9.4 already warns against over-fitting. The
opposite error would waste a round; this one hides a hole and looks like progress. The test
asserts on the field's *position* in the row, because the fixture's type has a measured 0.000
precision beside an undefined leak rate and a looser assertion passes with the two swapped.

`the_score_block_carries_the_run_and_cost_blocks_too` is the reduction turned back into a
forward, which is the shape a reviewer asks for: the blocks are in `metrics.json`, the builder
already walks it, four more lines reads as completeness. It arrives with `model_id`, `commit`,
`wall_seconds` and the token counts. The run block is facts about the harness the agent must not
act on; the cost block is worse, because an agent that can see its own token spend can reason
about the budget, and the cheap move available to it — emit fewer rules — improves the number it
can see while damaging the one being measured. CLAUDE.md requires cost beside quality *to a
reader*, not to the agent generating both. `test_the_run_and_cost_blocks_are_not_forwarded`
injects both into the fixture for a reason worth naming: the fixture is built from the scorer's
return, which has neither, so the version of that test written without the injection is the
version that passes under this mutation.

All six are `min_kills=1`. The score and audit blocks are also covered by tests that assert
against the scorer's and the Auditor's own output rather than against literals — every rule the
scorer attributes must have a row, every mode must appear, the flag table must carry what
`audit.report()` assembled — and those have no mutation here because what they guard against is
schema drift in another module, which no edit to `prompt.py` produces.

### Six on the two cost blocks, where every mutated file is internally consistent

Added 2026-08-13 with schema 7, and the family has one property in common that no other section
here does: **not one of them produces a file that contradicts itself.** `metrics.json` gained
`cost_to_date` because `port-loop`'s iteration is 1 + N calls — RuleAuthor once, the Auditor once
per dev document — so a round's spend and an arm's total became different numbers, and DESIGN
§11.3's 1.9× standard is read off the second one. Both blocks carry the same four keys, both are
required, both are validated for shape. What a wrong figure disagrees with is the *run history*,
which no single file holds.

`the_arms_total_is_the_last_rounds_cost` is the one the whole block exists to prevent, and it is
also the state the code was in before the change: `run_fold` passed one cost dict to both of its
`write_metrics` calls, which is correct while an arm is one round and becomes the arm's headline
holding round 8's spend the moment it is not. Dropping `cost_to_date` from the assembly restores
exactly that. An eight-iteration arm then reports about 2.2M tokens where the truth is 15.6M —
and `check_cost_to_date` is *satisfied*, because the two blocks come out equal and the relation
refuses only a total that is smaller. Equality is the correct state for every rung except
`port-loop` past iteration 1, which is why the tests that see this pass a total differing from
the round rather than a realistic-looking one.

`the_folds_seconds_go_to_the_round_and_not_the_arm` is the only place the two blocks can drift by
construction, because `wall_seconds` is the one key `run_fold` measures rather than passes
through. Adding the detection pass to the round and not to the total makes the total smaller than
the sum of the rounds it contains, by a fold's compute per iteration, in the direction that makes
the iterating arm look cheaper. Still no contradiction available to a reader: a total above the
round's calls and below their sum is not a state any single file exposes.
`test_the_detection_pass_lands_in_both_blocks` measures what each block *grew by* for this
reason — an assertion on either number could not tell which block the seconds went to.

`the_writer_adds_the_rounds_up_itself` is the placement question answered wrongly. The scorer
adds the round into the total it was handed, so every round is counted twice and an eight-round
arm publishes roughly double its spend — and the file agrees with itself *more* comfortably than
before, since `cost_to_date` is now further above `cost`. It also leaves the non-iterating arms
untouched, because the sum of a single block is that block. What it costs is the one-producer
rule: two accumulators, one in the driver and one in the writer, and nothing recording which
produced the published figure. Caught by
`test_the_writer_does_not_derive_the_total_from_anything`, which hands the writer a total no sum
of that round could yield — a behavioural test built to detect a computation rather than a value.

`a_total_below_its_round_is_published` deletes the relation check, and the reason it is
`min_kills=2` is that the reachable failure is a swap. Both arguments are `REQUIRED_COST` blocks
with identical shapes, so no type error and no shape check distinguishes them; passed the other
way round, the file says round 3 spent the arm's whole budget and the arm spent one round's, and
every reading of §11.3 inverts while both blocks stay well-formed. This is the
guard-whose-precondition-was-never-asked shape again: the property holds of a correct driver and
was unchecked at the writer, so it held for one code path rather than for the file.

`summing_takes_the_longest_call_as_the_wall_time` has the best argument of the six, which is why
it is here. Taking a maximum is what you write if you think the field is elapsed wall-clock, and
for a concurrent driver it would be nearer right. The driver issues the Auditor's documents
sequentially, so the sum is the honest number — and `sum_costs` says so in prose precisely
because the alternative is defensible enough to be adopted silently. `llm_calls` and the token
counts stay correct under it, so every test about the call count passes and the block stays
four-keyed; only a fixture of three calls with *distinct* times can see it.

`summing_carries_an_undeclared_key_into_the_total` opens the block on the way in. The plausible
caller is one passing Bedrock's own `inputTokens` beside `prompt_tokens` — the provider's name
for the same quantity — and the sum silently ignores it rather than refusing, so the total is
short by whatever it held and the published block is still valid and comparable-looking. The
`termination` block's closed-set argument, at the cost block: a field this project never declared
cannot be told from part of the cost model.

### Five on `port-loop`'s rounds, where the wrong round produces the right-looking file

Added 2026-08-15 with `tests/test_loop.py`, and the family's property is the one the loop
introduces to this repository: **a round assembled from the wrong predecessor is complete,
well-formed and internally consistent.** Every other section here has some file that
contradicts itself or some count that fails to add up. A round does not. It has a rule file, a
score, an audit report whose two numbers agree, a `termination` block whose `improvements` list
differences its own leak rates, and a cost block that prices the calls it made. Nothing in
`iter4/` says which round's feedback the agent was shown, because the artefacts of a round
assembled from round 1 and a round assembled from round 3 differ only in content nobody can
check without rerunning the arm.

That is also why three of the five needed tests written before them. The two off-by-one
mutations are invisible at round 2 — `previous` and `1` are the same number there — and
invisible at any round if the rounds' rule files are identical, which they are in any fixture
that answers every call with the same YAML. So
`test_a_later_round_masks_the_previous_rounds_spans_and_not_round_ones` and
`test_the_sample_is_drawn_over_the_previous_rounds_errors` both run three rounds and make round
2 author a rule the round-1 file does not have; the first then reads the masked *text* the
Auditor was sent rather than the report's `masked_from_iteration`, because that number is what
the driver *says* it masked and `audit.report()` checks only that it agrees with the round — a
driver reading some other round's `spans.jsonl` satisfies it, since it records what it was told.
The masking assertion is two-sided for the reason the seal mutations are: without checking that
round 2's Auditor *does* see the term in the clear, a term absent from the fold would make the
round-3 half pass against any driver at all.

`the_history_is_pre_seeded_with_this_rounds_rate` and
`a_round_with_no_score_walks_back_to_the_last_one` are the same corruption reached from opposite
ends, which is why they are worth having as two entries. Both make the leak-rate sequence hold
one round's rate twice, so `improvements` gains a `0.0` — an iteration that changed nothing —
and a zero is below δ for every δ this rule can compute, so it *counts toward stopping*. At
k = 2 one genuinely thin round beside the phantom converges the arm. One of them gets there by
extending the history forward to cover the round being scored, the other by walking it backward
over a round that left no score; the first is a plausible reading of the field's name, the
second is plausible robustness. Neither leaves a file that looks wrong, and the second is the
answer to a question DESIGN §5.5 deliberately does not ask: a format failure ends the arm by
*not writing a score*, and there is no flag anywhere recording that it happened.

`the_writer_calls_the_stopping_rule_itself` is the one with nothing behavioural to catch it, in
any fixture, ever. `PendingTermination.resolve()` is one line —
`should_stop(self.corpus, (*self.previous_leak_rates, leak_rate))` — so inlining it at the call
site in `run_fold` produces byte-identical output for every arm, every corpus and every round.
The mutation is a strict simplification by every local measure, and what it costs is that §3's
pre-registered decision now has a second home in the module that writes the file the decision
is published in. The refusal is about the *next* edit: a writer that holds `should_stop` can
grow a mode check or a first-round special case, and the arm's record would then carry a verdict
no reader of `src/termination.py` can reproduce. `test_the_writer_never_imports_the_stopping_rule`
reads the import list from the syntax tree rather than the text, because that module's
docstrings name `should_stop` on purpose and a substring search would forbid explaining the
boundary in order to enforce it.

`converged_is_stored_beside_the_reason` is the section's contribution to the §3 prohibition, and
it is the mutation that shows why `a_ceiling_stop_is_recorded_as_converged` was not enough on its
own. That one produces a wrong value; this one produces every correct value and removes the
*mechanism*. With `converged` as a field, `should_stop` sets it from `reason`, `not_applicable`
sets it false, `record()` copies it out, and the scorer's cross-check finds the two agreeing —
there is no input on which they disagree, because one producer computes them together. What
becomes possible is `Termination(reason=CEILING, converged=True, …)`, and the caller who reaches
for that constructor is precisely the one §3's prohibition is about, since `write_metrics` takes
a mapping and a hand-assembled block is the path around the dataclass in the first place. The
trap is that `frozen=True` hides it: `verdict.converged = True` raises whether the attribute is a
property or a field, so `test_converged_cannot_be_set` — written as *the mechanism's* test —
passes on the mutant. `test_converged_is_not_a_field_and_no_caller_can_supply_one` was added with
this mutation and asserts the shape three ways: absent from `dataclasses.fields`, a `property` on
the class, and refused as a constructor argument.

### Five on prompt caching, where the saving is real and the result is not

Added 2026-08-18 with schema 8. The family's property is not that the mutants produce wrong
numbers — three of them produce numbers a service actually charged — it is that **a transport
optimisation ends up in the column a claim about role specialisation is read off.** DESIGN §11.3
pre-registers a 1.9× cost standard for `port-loop` against `port-oneshot`, and caching is
available to `port-loop` and to nothing else on the ladder, because the Auditor is the only agent
called once per dev document. So every accounting mistake here is directional: it moves the arm
whose result is in question, and it moves it by an amount nobody has a prior for.

`prompt_tokens_is_what_the_invoice_was_computed_on` is the one the whole schema exists to prevent,
and it has the best argument of the five: `inputTokens` is what AWS bills, and CLAUDE.md says to
report cost. Measured 2026-08-16, `inputTokens` was 7193 on a control call and 21 on a cache read
of the same text — a factor of 340. Since `auditor.md` is 80.7% of an average audit call, and 1.71M
of a round's 2.12M prompt tokens are that one template sent 250 times, the arm would appear to
clear the 1.9× standard on the strength of AWS's cache infrastructure. The mutation is
`min_kills=3` and carries a second edit, which is what makes it worth having as an entry: without
adjusting the `totalTokens` comparison the mutant refuses every cached call and anyone would
notice in a minute, so the faithful version keeps the cross-check *passing*. Then nothing in the
file contradicts it. The `caching` block still reports the reads; what a reader cannot tell is
whether those reads were already subtracted from `prompt_tokens` or are published beside a raw
total, because both files have the same shape and neither says which. Note the direction against
the entry above it: `the_rule_authors_prompt_is_cached_too` costs the arm money and reads as
conservatism, this one earns the arm its headline and reads as accuracy.

Which tests catch it is worth stating precisely, because one of them does not.
`test_prompt_tokens_is_the_raw_total_on_all_three_probe_calls` is parametrised over the control,
the write and the read, and only the last two fail — the control call has no cache tokens, so its
`prompt_tokens` is identical under the mutation. That is the family's exposure in miniature: an
uncached arm's cost column cannot see this defect at all, and every rung below `port-loop` is
uncached. The suite has to carry a cached envelope or it carries nothing.

`the_rule_authors_prompt_is_cached_too` is the same reasoning applied one agent over, and the
reason it fails is *N*. The Auditor's prefix is one template repeated once per document within a
round, seconds apart — one write and N−1 reads inside the 5m TTL. The RuleAuthor is called once
per round and rounds are 40–80 minutes apart, so every write expires unread and the arm buys a
`cacheWrite` charge per round for nothing. It also breaks §4 in a way no count exposes: the
boundary goes on `assemble_iteration_prompt`'s return, which round 1 does not take, so rounds 2+
of an arm are transported differently from round 1 of the same arm. The entry carries two edits
because one alone is not the mutation — an assembler growing a boundary and a call site consuming
it is what the change actually looks like — and it is `min_kills=2` because the behavioural test
(`cachePoint` blocks counted per call) and the structural one (`cache=` keywords read off every
`invoke` in `src/`) catch different things: the first catches this instance, the second catches
the shape.

`the_assembled_total_is_trusted_rather_than_checked` is the entry with nothing behavioural to
catch it on any input the project has ever seen. It deletes the `totalTokens` comparison and
keeps the key required, so every real response passes and the branch reads as a check found to be
redundant. What it removes is the reason the 2026-08-16 measurement was worth making: Bedrock
publishes the same quantity by two paths that do not share an implementation, and `totalTokens`
was 7197 on the control, the write and the read while the components moved between them. The
failure it guards is a platform redefining `cacheReadInputTokens` to overlap `inputTokens`, or
adding a fourth component — which produces plausible numbers in every arm and every file, leaves
each arm internally consistent, and makes the comparison between them wrong. A mutation that
removes a check is invisible to every input on which the check passes, so the suite has to carry
an input on which it must fail; `test_the_assembled_total_is_cross_checked_against_the_envelopes_own`
perturbs one component of a measured envelope so the sum no longer reaches 7197.

`the_cache_boundary_crosses_onto_the_masked_document` is the near-miss rather than the invented
one. It moves the boundary past §1.2's heading and its two counts — structurally the same
sentence on every call, so extending the cached prefix over it reads as taking in a few dozen
constant characters. The counts are `n_lines` and `n_tags` of *this* document, so the prefix now
differs per document, the cache misses on nearly every call, and the round pays N writes instead
of one — visible only as a `write_tokens` figure nobody has a prior for. The recorded boundary
still says `after_audit_frame`, whose `config/naming.yaml` gloss states which bytes are retained
and which `auditor.md` §6 publishes to the agent; both statements are now false while the value is
unchanged. One more join along the same edit and the masked document's own text is what a third
party retains for five minutes. `tests/test_prompt.py` carries four assertions on this offset,
which is the redundancy an integer nobody re-derives downstream earns — and only two of them fail
on this mutant. The cached side still contains the three things `auditor.md` §6 names; it now
contains a fourth, which is why the test that catches it is the one asserting what must be
*absent*, and why that assertion had to use the *filled* heading with its counts rather than a
bare `### 1.2` — the bare string is in the committed template and is legitimately cached, so an
assertion on it would fail on the correct split and prove nothing about the wrong one.

`caching_is_inferred_from_the_prompt_carrying_a_boundary` is the sixth family, and it is the only
entry here whose subject is a *design decision* rather than a value. On 2026-08-18 the choice was
between `invoke(..., cache=True)` and inferring caching from the prompt's shape; the keyword won
because inference makes the boundary's consumer implicit while its producer stays single. The
mutant is the rejected design, and on today's code it is **byte-identical**: the only assembler
that produces a boundary is the only call site that passes the keyword, every refusal in the
function is kept, and the keyword still works. It reads as removing a redundant argument. The
failure is in the next commit — the moment any assembler grows a `cache_after`, its calls begin
being cached at a boundary nobody chose for that prompt, and
`the_rule_authors_prompt_is_cached_too` arrives as an **omission**: two keys added to a reference
dict, no `cache=True` in the diff to stop at, §4's byte-identical claim broken with no line to
point to.

That is an argument about diffs, so no test of current behaviour carries it — which is precisely
why the mutation had to be written before the defence could be. **And
`test_cache_is_off_by_default` does not catch it.** The signature is untouched: the default is
still `False`, still keyword-only, and the override happens in the body. A structural test on the
parameter asserts that the keyword *exists*, not that it decides anything. What catches the mutant
is `test_caching_is_never_inferred_from_the_prompt_carrying_a_boundary`, written for this purpose —
hand `invoke` a boundary-carrying prompt, omit the keyword, require one content block and no
`caching` record — plus `test_the_cached_call_sends_the_same_bytes_as_the_uncached_one`
incidentally, whose *uncached* control is a boundary-carrying prompt and so stops being uncached
on the mutant. Before those two, the decision was held by a docstring plus the coincidence that
one assembler and one call site happen to line up. A coincidence is not a guarantee, and the
distance between them is what this section is for.

### The split moved three tests' observation point, and they still passed

Worth recording as its own note, because the mechanism is not a mutation and no mutation would
have found it. Splitting the Auditor's prompt into two content blocks changed where the prompt
*is* on the wire, and `tests/test_loop.py`'s `Transport` fake read `messages[0]["content"][0]
["text"]` — one block, by an assumption that was true when it was written. After the split that
expression returns `auditor.md` plus the input banner plus §1.1's frame, and stops at the
boundary: **§1.2's masked document, which is the half three tests search, is simply not in the
string any more.**

What happened when the split landed is the finding. Of the three call sites, one failed and two
passed:

- `test_a_later_round_masks_the_previous_rounds_spans_and_not_round_ones` failed — but on its
  *positive* half, the meta-guard asserting that round 2's Auditor **did** see `Centro` in the
  clear. Deleting that half from the pre-fix file and rerunning against the split code makes the
  test pass. So the assertion the test exists for — that round 3's Auditor never sees the term —
  passed vacuously over prompts whose document half was gone, and the only thing that failed was
  the guard put there to detect exactly that vacuity.
- `test_the_previous_rounds_rule_file_is_the_one_shown_as_section_1_2` passed. It reads the
  RuleAuthor's call, which is still one block, and its filter — `"Auditor prompt" not in text` —
  keeps working because the banner is on the cached side.
- The `Transport` fake's own role dispatch passed, for the same reason: it sniffs `"Auditor
  prompt"`, which the truncated prefix still contains.

The general shape: **a change to how a prompt is transported can silently narrow what a test
observes, and an assertion of the form "X is not in what was sent" gets *more* likely to pass as
the observation window shrinks.** Every negative assertion over transported bytes has this
exposure. Two things contained it here, and neither was luck: the two-sided assertion, which is
the seal mutations' rule (`test_the_gate_is_not_satisfied_by_an_empty_log`'s reasoning — check
that the thing you are asserting the absence of is present when it should be), and the fact that
the fake reads what the transport actually sent rather than what the assembler returned. Had the
fake been handed `prompt.for_transport()` instead, all three would have passed and the split
would have been invisible to the suite.

The fix is `sent_text(call)`, which joins the text blocks back across the boundary — not a
workaround for the split but a reading of it: §4 requires the concatenation to be the bytes the
uncached call would have sent, so joining is reading what the model read. It is one helper rather
than three inline expressions so the next transport change has one place to fail.

### The glob that was right and unasserted

**A near-miss, not an incident.** Nothing broke. `suite_files()` returns 33 files and always
has, and the eight loops that consume it have been examining the suite they claim to examine.
What is being recorded is that *no assertion made that true* — the glob was correct by
construction, and on the day it stopped being correct nothing would have said so.

The shape is the quietest one in this file. Eight callers do

```python
for path in suite_files():
    assert <something is absent from path>
```

split three ways in `tests/test_structure.py` and five in `tests/test_conftest.py`, and one of
the five is `assert CONFTEST not in suite_files()` — an assertion that an empty list satisfies
for no reason at all. Every one of them passes over `[]`. So a `suite_files()` that returned
nothing would produce no failure anywhere: **no assertion failing was the whole of the evidence
that the eight were working**, and that evidence is indistinguishable from the evidence a
suite-wide pass over nothing produces. There is no error message to read, no count that looks
wrong, no test to bisect. It is the same exposure as the Auditor split above — an absence
asserted over a surface that shrank — with the surface being a directory listing instead of a
wire payload, and with the shrink not having happened yet.

The pin goes *inside* the helper rather than into a test of its own:

```python
files = sorted(TESTS.glob("test_*.py"))
assert Path(__file__).resolve() in files, (...)
```

Inside, because a separate `test_suite_files_is_not_empty` would leave the eight loops still
trusting an unchecked surface — they would inherit nothing, and the family's signature failure
is exactly a caller trusting a surface somebody else checked once. And *this file finding
itself* rather than a count or a bare `assert files`: a count drifts as test files are added,
non-emptiness still passes if the glob drifts sideways into a neighbouring directory that
happens to contain `test_*.py`, and the self-reference cannot be satisfied by any directory but
the right one.

The mutation is the glob one level deeper — `(TESTS / "tests").glob("test_*.py")` — in each of
the two files: `the_suite_glob_points_one_level_deep` and
`the_conftest_suite_glob_points_one_level_deep`, since the two copies gate ten checks between
them and neither pin covers the other. Both are caught, at 5 kills each. The first attempt
moved `TESTS` itself and scored 28 kills in one file and 9 in the other, which is
not evidence about this guarantee: `ROOT = TESTS.parent` in one file and `CONFTEST = TESTS /
"conftest.py"` in the other mean a moved constant breaks unrelated machinery, and a kill count
that large says the file failed to import its own scaffolding. Retargeted onto the glob
expression the counts are readable and the mutation is about the thing it names — the same
kill-count-as-diagnostic reasoning as `### Verifying the mutation`.

**Correction to that run's own record.** e860ac3's commit message reports "Baseline 1692".
Re-measured on 2026-08-19 by `--collect-only` in a worktree at that commit, over the same
27-file `TEST_FILES` (byte-identical between e860ac3 and today), the baseline there is
**1690**; HEAD is **1695**, the five added by 40bd89c. Two tests off, and nothing downstream
moves — check 5's equal-totals comparison is made against the number each run measures for
itself, never against the number written in a message. What the wrong figure would cost is a
future reader reconstructing why a total changed and finding a two-test discrepancy that never
existed. Corrected here rather than by rewriting the commit: the message is the artefact that
was wrong, and this file is the one that gets read.

#### How this item was reported, and why that matters here

The sweep's finding was **"no assertion pins non-emptiness"**. It was read — by me, from my own
wording — as **"the glob returns empty"**, and the next instruction was to fix a glob that was
never broken. The 6,614 in the sweep output was the loops' iteration count, which is a number
that could only exist if the list were non-empty; it was reported beside the finding and read as
corroborating the opposite of what it says.

Both statements are about the same line and only one is falsifiable by looking at it. "Returns
empty" is checked in one command. "Nothing pins non-emptiness" is a claim about the *absence* of
an assertion elsewhere, and absence claims are what this entire family of findings is about — so
a sweep report that states them loosely is reproducing, in its own prose, the defect it is
reporting. The rule for these records: **state a sweep result as something that can be checked
false.** "`suite_files()` has no assertion that its result is non-empty; it currently returns 33
files" is one sentence and cannot be read as the other claim. "The glob is unasserted" can.

### Promoting the sweep into two checks, and the two ways the first draft was green and blind

The sweep that found the vacuous-absence family is prose, and prose does not fail. Two of its
classes are structural enough to become checks, and both now live in `tests/test_structure.py`
§3b. What is worth recording is not that they exist but **how each first draft passed while
covering almost nothing**, because that is the failure mode of every check in this file and it
was invisible from the fixed tree.

The method that found it: run the new check against `HEAD~2` — the tree *before* the seven sites
were fixed — and count what it reports against the seven the sweep found by hand. On the fixed
tree the answer is zero either way, so a narrowed check and a working one are indistinguishable.

**The captured-output check (class B).** Draft one reported **2 of 7**.

- *It matched the shape nobody writes.* `assert "x" not in result.stdout` has a stream token in
  the container; five of the seven sites bind a name first — `out = run(...).stdout`, then
  `assert "context" not in out.lower()` — and one reads it back through a comprehension and a
  `join`, so the container's source text names no stream at all. Fixed by following aliases to a
  fixed point, and by locating the surface *inside* the expression rather than at its root.
- *Its control was scoped to the function, and one real site made that wrong rather than merely
  weak.* `tests/test_run_fold.py` pinned `"iter4" in printed`, then made a **second** CLI call
  and asserted `"iter" not in capsys.readouterr().out`. `readouterr()` drains the buffer, so the
  pin is over a surface that no longer exists when the absence is asserted — and to a
  function-scoped checker, and to a reader, the test looks controlled. This is the concrete
  counterexample to the loose form the class-A docstring rejects on principle: there it is
  argued as trivially evadable, here it is simply wrong on a site that was in the suite.

A third correction went the other way. Draft one flagged `tests/test_run_loop_cli.py:323`, whose
control is `calls = [l for l in done.stdout.splitlines() if ...]` followed by `assert calls` — a
real control, and a good one, since an empty stream gives an empty list. **The rule was fixed,
not the test.** That is the same false-positive class the class-A docstring argues against, found
in the companion check.

**The one-block check (class A).** Deliberately narrow: an absence over a name obtained by
integer-subscripting a `content` list, which is the literal expression the cache split truncated.
The broad rule — "every negative needs a positive on the same surface" — was written, run, and
rejected on measurement: strict, it reported 172 sites where careful reading finds a couple of
dozen (`tests/test_prompt.py` guards `document not in cached` with `document in tail`, a sibling
surface); loose, it is satisfiable by one unrelated assertion, which would have silenced the real
offender instead of fixing it. The narrow form fires on exactly the shape that broke, and would
have fired the day the split landed.

**Both are mutated, and both mutations exist because of the above.** Not deletions — deleting a
check is loud. `the_captured_surface_control_is_scoped_to_the_function` collapses `surface_key`
to a truthiness test, restoring draft one's blindness to the `readouterr()` site.
`the_captured_surface_check_reads_only_direct_stream_access` stops following aliases, restoring
2-of-7. Neither is caught by the checks' own assertions over the live suite — the suite is clean,
so a widened check reports zero exactly as the real one does. They are caught by three capability
tests that run the check bodies over a constructed tree with the known answer written down, which
is why `uncontrolled_absences()` and `absences_over_one_block()` are functions and not test
bodies. Section 4 makes the identical argument for the profiler check, and this is that argument
applied one level down: **a check whose only evidence is a green suite is the defect it exists to
prevent.**

### Six on the draw and the abandoned spend, where the artefact is the only reader — 2026-08-24

The path added after round 6's first attempt died on auditor call 123 of 250 (DESIGN §5.5.2,
schema 9): a preserved `draw{N}/audit_report.json` per attempt, a `draw_index`/`draws_total` pair
that reconciles the report against `agent_calls.jsonl`, and an `abandoned_spend` block recording
what the attempts that produced no round cost.

**Why this path needs mutations more than most.** Nothing downstream reads these numbers. No
score depends on them, no gate consults them, no later round is assembled from them — they exist
to be read by a person, in the paper and in the record. So the failure mode is never a crash and
never a red suite: it is a well-formed file, in the right place, that understates spend or has
quietly lost an earlier attempt's report. The only thing between an understated figure and
publication is the assertion that names it, which is exactly the situation mutation testing is
for. Measured individually against tree `82f101b925fcfbcc`, baseline 1804 passed:

| mutation | kills |
| --- | --- |
| `the_abandoned_gate_is_the_draw_count_alone` | 2 |
| `the_abandoned_attempt_count_comes_from_the_log_alone` | 1 |
| `the_audit_report_records_no_draw_number` | 36 |
| `the_next_draw_counts_the_draws_that_exist` | 2 |
| `the_preserved_draw_is_written_to_the_canonical_path` | 4 |
| `the_abandoned_block_uses_the_cost_block_names` | 15 |

**The first one is not invented.** It is the defect that shipped, restored verbatim: gating
`_abandoned_spend` on `draws_before < 1` returns `None` for a round that wrote no draw directory,
and under the absent-means-unrecorded convention that publishes a round nothing was abandoned on.
Round 6's 122 paid calls and 1,133,206 prompt tokens would have disappeared with no trace in
`metrics.json`. It survived review, a docstring that described the union gate, and the tests
already on the path; it was found only because a transport failure landed in the one window where
the two records disagree. Two kills — that is the whole margin, and both of those tests were
written the day the defect was found.

**The two-kill and one-kill entries are the point of the table, not its weak rows.** The three
lower rows are all in the same place: the arithmetic that combines the two records, where the
mutation is wrong only in the regime where the records disagree. `..._comes_from_the_log_alone`
replaces `max(draws_before, 1 if lines else 0)` with the log-first reading and is *correct* on
round 6's own case; it is wrong when two draws exist and one line does. One test covers that, on
purpose, and its sibling pins the undercount in the other direction deliberately — the formula
has a test per direction rather than one test of the formula. The three upper rows are broad
because the key names and the written fields are load-bearing surfaces that many tests touch; a
kill count of 36 measures reach, not the strength of the guarantee.

**The survivor, and what it found.** `the_next_draw_counts_the_draws_that_exist` — one past the
highest replaced by how many exist — was measured at **1 kill against a `min_kills` of 2** and
reported SURVIVED. The count was right and the expectation was the thing worth keeping:
`test_the_next_draw_is_the_number_that_does_not_overwrite` states the property this whole path
exists for ("whatever `next_draw` returns, no report is already there") and walked it over four
*contiguous* draws — precisely the regime where the two readings agree. The property test could
not see the failure the property is about, and the arithmetic test beside it was carrying the
guarantee alone. The walk now removes a draw halfway through; the mutation is caught by both, and
`min_kills=2` stands. This is the second SURVIVED verdict in this file that was a finding about a
test rather than about a mutation — `absent_token_counts_default_to_zero` is the other, and there
too the fix was in the assertion and not in the code. The shapes differ in a way worth keeping
apart: that one was an assertion passing for a reason other than the one it names, this one is an
assertion exercised only where the defect cannot appear.

`the_abandoned_block_uses_the_cost_block_names` is the section's one `also` mutation. Renaming the
four cost-shaped keys in `loop._abandoned_spend` alone is refused by the block's closed-key
validation and killed as a missing key — a kill that says the validator works and says nothing
about summing. Renamed in `scorer.REQUIRED_ABANDONED` too, the block validates, reaches
`metrics.json`, and `sum_costs([cost, abandoned_spend])` goes through: DESIGN §11.3's 1.9× rung
priced for work that produced nothing. That is the mutation the name claims, and it takes two
files to write it.

These six took `MUTATIONS` from 170 to 176, which is a change of denominator and not a stale
count — see "When a full run is required" below. **The full run of 2026-08-25 settled it and
reproduced all six of the counts above exactly**, measured under a different tree fingerprint
(`276308a0483f5f1d`) at the same 1804-test baseline. Six agreements between a selective run and a
full one say the selective numbers were not artefacts of the tree they were taken on; they say
nothing about the other 170, which is why the run had to happen.

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

The maintenance cost is real but bounded: **187 anchors across 170 mutations**, each a
line or two, and a refactor that breaks one gets a `STALE` message naming the file.
That is cheaper than the failure mode it prevents.

It is bounded on the *size* of the cost and it was not, until 2026-08-19, bounded on the
*latency* of the notice: `STALE` is printed by `apply()`, which runs one mutation per full
suite run, so a moved anchor was reported only to whoever spent the thirteen hours. Two had
been stale for days. `tests/test_mutation_harness.py::test_every_anchor_is_present_in_its_target`
now makes the same string search against the working tree in milliseconds — see "Two stale
anchors, and a check that was one refactor late" below.

## Verifying the mutation

Every result here is a claim of the form "breaking X was noticed by N tests". That
claim is only worth reading if X was actually broken — and the two ways it can fail to
be both produce a *larger* N than a working mutation. The harness therefore verifies
its own edit before believing the count.

One check in `Mutation.apply()` *before* the write:

0. **The tree carries no earlier mutation.** `.mutations-applied` is read first and a
   non-empty marker is refused. This is the one check aimed at the operator rather than at
   the code: two edits on one tree apply cleanly, run cleanly, and give a real count of a
   tree nobody meant to build. See "Hand-built probe trees" below for the run it invalidated.

Three more after the write:

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

`tests/test_mutation_harness.py` tests all six, including the indentation case
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

### Two stale anchors, and a check that was one refactor late

Check 1 above — the anchor exists — was working. It was simply not being *asked*. On
2026-08-19 a sweep of all 187 anchors against the working tree found two that no longer
matched, which means the last time this harness could have run to completion was before
the commits that moved them:

- **`run_fold_skips_axis_validation`.** §5.5's round widening inserted an
  `if iteration is not None:` branch between `spans_path`'s `check_run(run)` and its
  template, so the old two-line anchor spanned text that was no longer adjacent. Re-anchored
  onto the new pair, and deliberately kept at two lines: `    check_run(run)` alone also
  occurs in `write_errors`, and a one-line anchor would have applied cleanly there and
  reported a real count for a different mutation than the name claims.
- **`absent_token_counts_default_to_zero`.** `prompt_tokens` became the raw total
  (`inputTokens + cacheRead + cacheWrite`, measured 2026-08-16) and the read this mutation
  targets was renamed `input_tokens`. The mutation's meaning survives the rename unchanged —
  it re-adds the `, 0` default, and the `isinstance(..., int)` refusal two lines below
  accepts a defaulted 0, so a partial `usage` block still reaches the cost block as a
  measurement of nothing.

**Neither commit was wrong and neither mentioned this harness**, which is the whole of the
lesson. The drift is produced by ordinary work on exactly the code a mutation is aimed at —
the better the mutation's aim, the likelier some future edit moves its anchor. So the notice
cannot live only in the fifteen-hour run; it has to be cheap enough to fire beside the work
that causes it. `tests/test_mutation_harness.py::test_every_anchor_is_present_in_its_target`
performs the same string search `apply()` does, against the working tree, for every anchor
including `also` edits, in milliseconds. It fails in the ordinary suite on the commit that
moves the code.

Worth naming the near-miss in the old defences: `test_every_mutation_targets_a_file_that_exists`
already existed and passed throughout. It checks the *path*, which survives a refactor that
moves a line inside the file — so the guard that looked like it covered this covered the
easier half of it. A stale path reads as a deleted module and gets noticed; a stale anchor
reads as nothing at all.

And the failure mode this closes is milder than the syntax-error one above but the same
family: a `STALE` mutation is *reported*, so it does not manufacture a false green in the
run — the run exits non-zero. What it manufactures is a **gate nobody can pass**, and a gate
that has been unpassable for days is indistinguishable from a gate nobody ran.

### Hand-built probe trees, and three attributions that were wrong while every count was right

The five caching mutations were each `ok`, and then the question was the one this README
answers per section: *which* tests killed them. `main()` reports a number and discards the
tree, so the by-hand method is to rebuild a tree, apply one mutation, and run pytest with
`-x` off and the failure list read off the tail. That is what happened, and it is where the
error entered.

The trees were built by calling `make_tree()` and `apply()` from a shell whose working
directory had drifted into `/tmp/probe2/repo` — an earlier probe tree that already carried
mutation 5's edit. Trees created from there inherited it. Two mutations were then credited
with killers belonging to a mutation they were never combined with in any real run:
mutation 2 (`prompt_tokens_is_what_the_invoice_was_computed_on`) appeared to have six, of
which two were mutation 5's, and the boundary mutation likewise. Both `breaks` texts were
corrected against clean trees rebuilt with absolute paths. Mutation 2's real figure is
four, and the shape of those four is the finding the entry now states: the write and read
parametrisations fail and **the control case does not**, because on a call that sends no
cache point `inputTokens` already *is* the raw total, so the mutated arithmetic and the
correct arithmetic agree there.

**What was wrong was the reading, not the harness.** `main()` copies `pristine` afresh for
every mutation, `apply()` verifies its own three conditions, and `outcomes()` compares the
suite size against the baseline; nothing in that path can pick up a second mutation's edit,
and the published counts — 2, 4, 1, 2, 2 — were correct before and after the correction.
The contamination was entirely in the hand-built trees standing beside it, and the damage
was to prose that named tests. That is a mild failure and an instructive one: the harness's
own numbers are structurally defended and the per-test attributions in this file are not,
so **the attributions are the part of every section here that a reader should treat as the
weaker claim.**

The recurrence fix, in the same shape as the three checks above — make the tree able to
answer what it is, rather than trusting the operator to remember. All three are built:

1. **`apply()` writes a marker.** The mutation's name is appended to `.mutations-applied` at
   the tree root, and a tree whose marker is non-empty is refused — `ContaminatedTree`, a
   distinct exception for the same reason `BrokenSuite` is one: the diagnosis differs, and
   this one is fixed by building a fresh tree. The contaminated trees would have failed at
   construction naming the mutation already there, instead of producing a plausible failure
   list. A single-mutation harness has no use for a second edit, so the refusal costs
   nothing and closes the case.

   Two details that are not incidental. The marker is written **last**, after all three
   verification checks pass, so a tree whose mutation raised `STALE` is not marked as
   carrying one — otherwise fixing an anchor and retrying reports contamination for a reason
   that is not true, which is its own misleading refusal. And it lives **inside** the tree
   rather than in the harness's memory, because a marker the harness holds is exactly as
   absent from a hand-copied tree as no marker at all, and hand-copying is the thing that
   went wrong.
2. **A `--probe NAME` subcommand.** It builds a tree, applies one mutation, runs the suite,
   and prints the failing test ids *and* the tree's absolute path and the marker's contents.
   No attribution is produced by a hand-assembled tree any more, and the reading that goes
   into a `breaks` text comes from the same code path that produced the count beside it. It
   prints ids rather than a number on purpose: a number is what `main()` already gives, and
   wanting the names is the only reason to reach for a probe.
3. **No relative paths.** `make_tree()` takes a `Path` and `ROOT` is absolute, so the
   library was never at risk; the drift was in the shell that called it. `--probe` removes
   the shell from the loop, which is the durable half of this.

Verified by construction rather than by inspection: `tests/test_mutation_harness.py` has
three tests for the marker — a second `apply()` on the same tree raises and **edits
nothing**, the marker names which mutation, and a stale mutation leaves the tree usable —
and the refusal was also exercised on a real `make_tree()` copy, where applying a second
mutation to an already-mutated tree failed at construction and the second edit did not land.
`--probe the_suite_glob_points_one_level_deep` prints the five ids behind that mutation's
kill count, which is the exact kind of reading that was done by hand and got wrong.

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

### The sixth of the family: a comment that overstated a guarantee

**`the_parse_error_quotes_the_line_it_choked_on` SURVIVED at 0 kills on its first run, and
the mutation was not what was wrong.** The comment in `src/rules.py` said that `pyyaml`'s
`MarkedYAMLError` renders the offending source line, and that the loader therefore picks
`problem`, `context` and `problem_mark` out by hand rather than interpolating `str(exc)`.
The single-edit mutation restored the interpolation, no test failed, and the reason is that
the claim was too strong: `MarkedYAMLError.__str__` prints the line only when its `Mark`
carries a buffer, and `yaml.reader.Reader.get_mark` passes `buffer=None` for a *stream*
while filling it for a *string*. `load_rules` hands `pyyaml` an open file, so `{exc}`
would have leaked nothing — the message was clean because of the argument at the call site,
not because of the fields the comment was defending.

**This is a different fault from the five above, and the difference is worth a name.** In
each of those a mechanism existed and resolved an ambiguity it could not see, in the
reassuring direction — a skip read as a pass, a collection error as thirty-seven kills, a
deleted record as an unopened window. Here **no mechanism resolved anything.** The property
held, the code that was supposed to hold it was doing nothing, and the only artefact
asserting a causal connection between the two was a comment. A comment cannot be caught
resolving an ambiguity wrongly; it is not in the execution path at all. What it can do is
describe a property as *decided* when the property is *incidental*, after which every
reader — including the author, later — treats the incidental one as load-bearing and the
load-bearing one as absent. The five above cost coverage. This one costs the map.

Nothing was wrong with the code and the mutation is the only thing that could have said so.
A passing suite cannot: the guarantee held. A reviewer cannot: the comment was specific,
cited the right class, and described a real mechanism in `pyyaml`. Only breaking the thing
the comment claimed to protect and watching nothing happen distinguishes **this code
prevents the leak** from **nothing here leaks yet**. The repair was to make the comment true
— it now says the no-leak property is safe by accident and names the stream/string
dependency — and to make the mutation two edits, so it flips `safe_load(fh)` to
`safe_load(fh.read())` *and* interpolates `{exc}`. At that point it kills 1 test, and the
hand-picked fields are load-bearing rather than decorative: they are what makes the first
edit survivable.

**The generalisation, and it is the sharpest one in this file:** a comment claiming a
property is a claim about the *code*, and the harness can check it exactly the way it checks
any other — break the code and see whether anything notices. Where nothing does, one of two
things is true, and they are not close: the property is enforced elsewhere and the comment
names the wrong enforcer, or the property is not enforced at all and the comment is a
prediction. **A guarantee asserted in a comment and held by an accident of a third-party
call is indistinguishable, from inside the file, from one the code enforces.**

**And it recurs as data, which is worse.** `the_lifecycle_block_moves_into_the_run_block`
(2026-08-11, `### The lifecycle probe` above) is this same fault with the comment replaced by
a field: `start_of_life_time` filed beside `model_id_resolution` asserts that something was
resolved, and nothing was. The difference in severity is where the claim can be read. A
comment misleads whoever opens the file; a field misleads whoever opens `metrics.json`, which
is everyone the paper is written for and nobody who will check the module. That is the reason
`bedrock.model_lifecycle`'s docstring leads with what it is *not* and the reason no field in
it is named `model_resolved` — the naming is the guard, chosen because this family's lesson
was already on the books.

#### The sweep, and what it turned up

`src/` was then read for comments of the same shape — a stated property, an argument for
why the code has it, and no mutation or test standing behind the claim. Six sites. The
first has since been closed (`#### Closing the first site`, below) and **the other five are
listed as work owed and not changed**; each is a candidate for a mutation rather than a known
defect, and the middle column is why each one holds today:

| site | the comment claims | why it holds today | what would notice a change |
|---|---|---|---|
| `src/llm/bedrock.py:305` — **closed, see below** | `dated` may be tested on the requested id because "the response never adds one", so no parsing of the reported id is needed | a measured property of one vendor's envelope on 2026-08-08, recorded in `docs/notes/baseline-model-family.md` | *was* nothing. `test_a_dated_id_is_recorded_as_dated` passes a dated *request*; no test drove a dated response against an undated request, which would be recorded `alias-unresolved` while resolvable. The closest relative in this file is `the_reply_text_is_taken_from_the_first_block` — the other place a claim about this envelope was measured rather than derived, and the one that was wrong |
| `src/corpora/meddocan.py:81` | `SPLIT_DIRS` is an indirection so a corpus whose directories are named differently "cannot tempt anyone into renaming a fold" | every `fold_dirs` in the repository, in `src/` and in the tests, is the identity mapping | nothing. `base.py` iterates `.items()`; a reader that used the key where it means the value is invariant under identity. The same shape as `run_fold_hardcodes_the_absent_value`, which has a mutation because a second spelling of one fact is exactly this fault |
| `src/rules.py:53` | `rule_layers()` derives the rules family so "a fourth rules-family layer added to the config must reach this module without an edit here" | there is no fourth; the derivation and any correct literal agree on today's axis | thinly. `test_the_rule_layers_are_the_rules_family_from_naming_yaml` asserts the same expression the code computes *and* the literal three, so it pins today's value from both sides and the drift claim from neither. `test_sample.py`'s `a_second_non_target_type` fixture is the pattern this is missing — it patches the axis to declare the value that does not exist yet |
| `src/llm/prompt.py:355` | each `phi_type` gloss is quoted as it stands and "nothing is appended to it, including for an excluded type" | nothing appends one | nothing. `test_the_task_frame_names_every_canonical_type_with_its_own_gloss` asserts the gloss is a *substring* of the prompt, which a `(non-target)` marker beside it satisfies. The prohibition's own paragraph is separately tested, so the marker would be redundant rather than wrong — which is why it is the edit someone makes |
| `src/eval/scorer.py:72` | `HEADLINE_MODE` is "recorded in the output rather than acted on: no code path here treats one mode as primary" | true — the name appears twice in the module, at its definition and in the output dict | nothing, and this one is structural by nature. A branch on it would change a reported number and every test asserting that number would move with it, so behavioural coverage cannot see the property; `test_scorer.py` reads the value out of the output and asserts the pair. CLAUDE.md puts the headline choice in the reporting layer, which makes this the scorer's half of that rule |
| `src/eval/scorer.py:768` | `by_rule` is "sorted for a stable file" | `dict` preserves insertion order and the comprehension inserts `sorted(by_rule.items())`, so removing the sort leaves the file byte-identical whenever the accumulation order already agrees | nothing. `run_fold_writes_unsorted_spans` is this same claim one field over and it has a mutation, with a `breaks` text saying why byte-identity across reruns is the wrong assertion for it — the test has to check the order is *sorted* rather than *reproducible* |

#### Closing the first site, and what closing it changed

The `bedrock.py` row was taken first, on the grounds the row itself gives: the other
measured claim about that same envelope was already wrong once, so a second claim resting on
the same afternoon's measurement should not be stronger than the measurement. Three changes,
and the third is the one worth reading.

1. **The comment is now dated and marked unchecked.** It says the datedness rule rests on a
   2026-08-08 measurement rather than on a property of the platform, names its sibling's
   failure, and states what would go wrong: a response resolving an alias to a snapshot
   would be recorded `alias-unresolved` while being resolvable.
2. **What is checkable is checked.** No fake response can establish what Bedrock does, but
   `_resolution`'s accept set decides what happens if the measurement stops holding —
   `reported` is accepted only as `requested` or one of three *strippings* of it, and
   stripping cannot add a component. So the failure is a `mismatch` and the run stops
   rather than under-recording. `test_no_accepted_report_adds_a_date_the_request_did_not_have`
   enumerates the accept set over 1,152 pairs and asserts that;
   `test_a_response_that_adds_a_date_is_a_mismatch_and_not_a_quiet_unresolved` pins the one
   case in both directions.
3. **The enumeration falsified the tidier version of its own claim, which is why it is an
   enumeration.** The assertion was first written symmetrically — an accepted report and its
   request never *disagree* about datedness — and it failed immediately: `rsplit("-v", 1)`
   on a body like `claude-v2-20251101` yields `claude`, so an accepted report can be undated
   while the request is dated. Harmless, since the date is read off the request. But it
   means the accept set is looser than the stripping list reads, and it means the honest
   assertion is one-directional. **A sweep like this produces claims of exactly the kind it
   was written to distrust**, and the only thing that separated the true half from the tidy
   half was running it.

What remains unchecked here is a *policy* and not a mechanism: on the day Bedrock does
resolve aliases, this refuses, so a platform improvement arrives as a blocked run. Nothing
inside the module can see that coming, which is the reason the claim is dated in the comment
rather than argued.

Two things about that list. The first is that **every one of them holds.** This is not six
defects; it is six places where the reason a property holds and the reason a comment gives
for it are not the same sentence, which is the state `src/rules.py` was in when the mutation
survived. The second is that the sweep found them by reading for a *rhetorical* pattern —
"cannot", "never", "by definition", a comment explaining why an edit is unnecessary — and
that is a search a person does, not a check. **The sweep does not generalise into a tool and
should not be written up as though it did.** What generalises is the harness's answer to any
comment of this kind: apply the edit the comment says is unsafe, and read the count.

### The seventh of the family — and the five failures beside it that are not

**Judged 2026-08-11, on the state the first `port-oneshot` run left behind.** The run made
its one call, `agent_calls.jsonl` gained a line, and `tools/run_arm.py --dry-run` began
refusing that cell instead of printing its plan — the freeze discipline working as designed
(DESIGN §6.3). Five tests in `tests/test_run_arm_cli.py` then failed, because all eighteen
ran the baseline cell and the plan tests presumed no arm had called. Confirmed by `git
stash` to fail on the unmodified tree: state, not a code change.

**The five failures are ordinary test debt, and writing them up as a family member would be
the more flattering mistake.** A test that presumes a precondition and fails when the
precondition changes has failed loudly, at the moment the state changed, naming the
assertion that no longer holds. Nothing was hidden and nothing resolved an ambiguity in a
reassuring direction — the fix was to give the file two fixtures, one cell that has called
and one that has not, and to test both outcomes rather than presume one. That is a repair,
not an incident. **Recording it as a seventh member would inflate this file's central claim
by counting an ordinary breakage as a silent one**, and a document whose examples drift
toward the dramatic stops being usable as a diagnostic.

**What is a family member is the three tests in the same file that went on passing.**
`test_no_sealed_path_appears_in_the_output` asserts `"sealed/" not in done.stdout`, and the
refusal writes to stderr — so `stdout` was the empty string, the assertion was vacuously
true, and the test reported a pass having examined nothing.
`test_a_dry_run_reaches_no_transport` is the same shape over three absences in `stderr`, and
it is the worse of the two: what it exists to check is that `_plan()`'s call to
`bedrock._resolution` opens no
client, and `_plan()` was never reached. `test_a_dry_run_writes_nothing_under_the_arm` is
the third — it compared a directory listing before and after an invocation that returned at
the guard, so it stopped covering the freeze it is named for.

This is the signature exactly: **an absence check cannot distinguish *the output was
produced and lacks the forbidden thing* from *there was no output*, and it resolves that
ambiguity in the reassuring direction.** A skip read as a pass, a collection error as
thirty-seven kills, a deleted freeze record as an unopened window, and now an empty stdout
as a clean one. The mechanism is different each time and the resolution is always the
comfortable one.

Two things distinguish this occurrence from the six above, and both are about how it was
found.

**1. The loud failures are what made the silent ones findable.** No mutation caught these,
no check caught them, and — this is the uncomfortable half, as with the `OTHER` incident —
nothing would have. Five sibling tests breaking is what caused anyone to read the file at
all, and the three vacuous passes were sitting in the output of that reading. Had the plan
tests been written as absence checks too, the whole file would have gone green against a
tool that printed nothing, and the run that changed the state would have looked like it
changed nothing. **The debt and the defect arrived from one cause and only the debt was
visible**, which is an argument for treating a test that breaks on state as worth reading
past rather than merely repairing.

**2. It is closable, and by the cheap half of the pattern.** The first two members were
closed by making the mechanism able to tell the cases apart; the third could not be and was
closed by reading the constraint from where it is stated. This one is the first kind: an
absence check is completed by a positive control, so each of the three now asserts that the
plan was *reached* before asserting what is missing from it — `armrules` present, then no
`sealed/`; `resolution` present, then no `botocore`. One line each, and the vacuous form
cannot come back silently because the control fails first.

**The generalisation, and it is narrower than the fifth's but has more sites:** *an
assertion of the form "X is not in the output" is a claim about an output, so the output's
existence is part of what it claims and has to be asserted.* Where the thing under test can
exit early — a guard, a refusal, a shut gate, an empty fold — that early exit satisfies
every absence check in the file at once. The sweep this suggests is a grep for `not in`
over `tests/`, and unlike the sixth member's rhetorical sweep it is mechanical enough to be
worth writing as one; it is not written yet, and it is recorded here as work owed rather
than done.

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

### The impact-scope run of 2026-08-19, and the thirteen hours it declined to spend

**What ran.** 71 of 170 mutations: the four added by the two commits above
(`the_suite_glob_points_one_level_deep`, `the_conftest_suite_glob_points_one_level_deep`,
`the_captured_surface_control_is_scoped_to_the_function`,
`the_captured_surface_check_reads_only_direct_stream_access`) plus every existing mutation
whose kill count could be *moved* by the six test files those commits touched. Baseline 1695
in ~305 s, so ~6 min per mutation; run as eight concurrent shards, each re-measuring the
baseline for itself, since a shard that inherits somebody else's total cannot make check 5.

**Scope was derived, not chosen.** A count can only move if a mutated line is executed by a
changed test, so the scope is the changed test functions' runtime reach: `tools/check_rules.py`
(2), `src/rules.py` (12), `src/eval/run_fold.py` (18), `src/orchestrate.py` (11), the
window/`human_arm`/`sample` group (19), the three cache-boundary mutations, and two whose
target *is* a test file. Two exclusions are worth recording because they look wrong:

- **All 17 scorer mutations are out.** `test_run_fold.py`'s changed test monkeypatches
  `run_fold` with a spy, so the scorer never executes underneath it. Reach, not filename.
- **`test_run_loop_cli.py` moves nothing at all.** It is not in `TEST_FILES`, and neither is
  `test_mutation_harness.py`. Only tests the harness runs can appear in a kill count — which
  is also why the new anchor guard, deliberately outside `TEST_FILES`, changes no number here.

**Result: 72 attempted, 71 caught, one survived at 0, and no count fell.** 72 rather than 71
because `absent_token_counts_default_to_zero` was pulled in by having had its anchor repaired
that morning — a mutation whose anchor moved has to be re-measured whether or not it is in
scope, since the repair is a change to the mutation. Of the 70 the shards measured, 40 came
back byte-identical to the recorded figure, 13 rose, and 17 had no recorded count to compare
against, because their sections state `min_kills` in prose rather than a measured number in a
table. The two re-anchored ones ran separately: `run_fold_skips_axis_validation` at 1, and the
survivor below. Every table cell touched by this run was marked `(2026-08-19)`, so that an
unmarked cell would mean *not re-measured under the current 27-file suite* rather than
*measured and unchanged*.

**Those markers are gone, and the day after is why.** The full run of 2026-08-20 measured all
170 under one list, so every cell now carries the same date and a per-cell marker distinguishes
nothing — the scheme existed to mark a minority, and once the minority is everyone it is noise.
The date is stated once at the top instead. Worth keeping in view that the markers were right
for the day they were written and became redundant rather than wrong; and worth keeping this
table, which is the 2026-08-19 measurement of the 17 mutations whose sections state `min_kills`
in prose rather than a number in a table:

| mutation | measured | `min_kills` |
|---|---|---|
| `the_folds_seconds_go_to_the_round_and_not_the_arm` | 76 | 1 |
| `only_the_score_is_scoped_to_the_round` | 26 | 1 |
| `the_suite_glob_points_one_level_deep` | 5 | 1 |
| `the_conftest_suite_glob_points_one_level_deep` | 5 | 1 |
| `the_arms_total_is_the_last_rounds_cost` | 3 | 1 |
| `the_round_s_files_are_written_by_every_arm` | 3 | 2 |
| `the_rule_authors_prompt_is_cached_too` | 2 | 2 |
| `the_cache_boundary_crosses_onto_the_masked_document` | 2 | 1 |
| `caching_is_inferred_from_the_prompt_carrying_a_boundary` | 2 | 2 |
| `drift_is_checked_against_todays_window_not_the_recorded_one` | 2 | 2 |
| `the_lifecycle_block_moves_into_the_run_block` | 2 | 2 |
| `the_role_is_appended_at_the_end_of_the_line` | 2 | 1 |
| `the_call_role_is_written_without_being_validated` | 1 | 1 |
| `the_final_rounds_duplicate_comes_from_a_second_scoring` | 1 | 1 |
| `the_recorded_files_list_is_ignored_in_favour_of_the_fields_present` | 1 | 1 |
| `the_captured_surface_control_is_scoped_to_the_function` | 1 | 1 |
| `the_captured_surface_check_reads_only_direct_stream_access` | 1 | 1 |

The four largest rises are the ones to read, because none of them is a new assertion about the
mutated guarantee: `spans_file_carries_the_surface` 2 → 34, `run_fold_omits_the_layer` 3 → 32,
`run_fold_writes_a_null_model_id` 21 → 45, and `the_folds_seconds_go_to_the_round_and_not_the_arm`
at 76, the highest count in this file. All four break something every `test_run_fold.py` and
`test_loop.py` test writes through, so they scale with *how much of the suite exercises the
writer*, not with how well the guarantee is pinned. **A large kill count is a statement about
blast radius, and only a small one is a statement about coverage** — which is the same reading
`### The glob that was right and unasserted` used to reject 28 and 9 in favour of 5 and 5.
`non_target_types_hardcoded_not_read_from_config` still sits at 1 and is still the honest number.

**Zero decreases across the 70 in scope, and that was the thing being looked for.** Adding
assertions can only raise a count; a count that fell while the suite grew from 1690 to 1695 would
mean a test stopped being able to see a defect it used to see — most plausibly one of the six
edited files having moved its observation point, which is precisely what `### The split moved
three tests' observation point` records happening before. None of the 70 did. The one decrease
in the run came from outside that scope and is the section after next: a mutation whose anchor
had to be repaired, re-measured for that reason, and found surviving at 0.

**What was not run, and when it gets checked.** 99 mutations: the loader, split-file, seal,
release-screener, vocabulary and `layer_families` groups, all 17 scorer mutations, and the
remaining `bedrock.py` / `prompt.py` / `loop.py` / audit / termination / iteration-path
mutations. None of them is reachable from the six changed files, so the expectation is that
every one is unchanged — but **an expectation is not a measurement, and this is a deferral
rather than an exemption.** They are checked by the next full 170-mutation run, whose cost is
the reason for the deferral: ~4.5 min each is about thirteen hours, and it is the *serial* figure
that has to be paid before the next commit that changes `TEST_FILES` membership, because that is
the change this scope rule cannot bound. Sharding is what makes it affordable and was not
attempted at 170 here. **It was, the next day** — all 170 in 1.87 h across eight shards, which
is where the deferral above was discharged and where the thirteen-hour figure was measured
rather than guessed at fifteen (§"Running all of it").

**Two mutations were stale before this run started**, which means the last time the full gate
could have passed was before the commits that moved their anchors — recorded in "Two stale
anchors, and a check that was one refactor late" above, along with the millisecond check that
now makes the same finding without the thirteen hours.

#### The survivor: an assertion that could not say which guard had refused

`absent_token_counts_default_to_zero` came back **SURVIVED, 0 kills against `min_kills` 1** —
the first mutation in this file to survive since the fifth of the family. The count it replaced
was **1**, so this is the run's one decrease, and it was found only because repairing the anchor
forced a re-measurement of a mutation that was out of scope.

Its only killing test was:

```python
del response["usage"]["outputTokens"]
with pytest.raises(BedrockError) as e:
    _usage(response)
assert "outputTokens" in str(e.value)
```

Under the mutation `completion_tokens` defaults to `0`, so the `isinstance(..., int)` refusal
this test is about does not fire. The call is still refused — the `totalTokens` cross-check
added on 2026-08-16 catches the same input one guard later — and that message reads
`… + outputTokens 0 = 67, and totalTokens says 1169`. **The asserted substring is in both
messages.** Measured, not reasoned: the mutated module raises `the response's token counts do
not agree with its own total: …` and `"outputTokens" in str(e.value)` is `True`.

Three things worth separating, because the comfortable reading of this is wrong in one direction
and the alarmed reading is wrong in the other:

- **No guarantee regressed.** A partial `usage` block is refused today, mutated or not. What was
  lost is the ability of the suite to notice *this* guard going away.
- **The guard is not redundant.** The cross-check only fires when the numbers disagree with
  `totalTokens`. A partial block whose remaining numbers happen to *agree* — the absent key
  genuinely contributing zero to a total that also omits it — passes the cross-check, and with a
  defaulted `0` a cost block of zeros gets recorded as a measurement. That case is exactly what
  the mutation names and exactly what nothing was checking.
- **The defect was in the assertion, not in the code.** Same as `familiares_as_other` and the
  loader fixture: a test that passes for a reason other than the one it names. Here the reason is
  a substring shared by two error messages, which is the cheapest possible way for an assertion
  to stop discriminating — nothing about the test's text looks loose.

Fixed by asserting the refusal rather than the absent key (`"A partial cost block is refused"`,
which only this branch emits) and by parametrising over both keys, since the mutation edits both
reads and only `outputTokens` had ever been deleted. Baseline goes 1695 → 1696. Re-measured after
the fix: **2**.

The general form to check the rest of this file against: **an absence-of-key test must assert
which guard refused, not that some guard did.** A `pytest.raises(SomeError)` with a substring
that appears in a neighbouring message is a test of the exception type, and every guard in
`bedrock.py` raises `BedrockError`.

## Running all of it: eight shards, five invariants, and when the gate is owed a full run

Everything above is a per-mutation argument. This section is about the run — what it costs,
what makes eight parallel shards a legitimate way to pay it, and the rule that decides
whether a change owes a full run or an impact-scope one. The imperative form of that rule is
in `CLAUDE.md`, because it fires at commit time and a rule nobody consults at commit time is
not a rule; the reasoning is here, with the measurements it rests on.

### What it actually costs, measured

| what | figure | how it was measured |
|---|---|---|
| one `TEST_FILES` suite run, alone | 271.5 s | timed in a `make_tree` copy, nothing else running |
| the same, 2 concurrent | 276.7 s | two copies at once |
| the same, 8 concurrent | 305.5 s | eight copies at once, 10-core machine |
| a *mutation* run that kills 142 | ~45 s | `utf8_sig`, 2026-08-20 validation |
| 170 mutations serial | ~12.9 h | 171 × 271.5 s, baseline included |
| 170 mutations, 8 shards | **1.87 h** | measured, 2026-08-20 |
| 176 mutations, 8 shards | **2.02 h** | measured, 2026-08-25 |

The speedup is **6.9× on 8 shards**, and the shortfall from 8× is almost exactly the
contention: a perfect split would be 12.9 h / 8 = 1.61 h, the run took 1.87 h, and 1.87 / 1.61
= 1.16 against a measured per-suite slowdown of 316 s / 271.5 s = 1.16. The eight per-shard
baselines cost 316 s of wall clock rather than eight times that, because they run concurrently
— which is the answer to "wouldn't one shared baseline be cheaper": it would save five minutes
of a two-hour run and give up the only check a shard has on its own copy.

**The 2026-08-25 run is the second data point and it behaves as the model says.** 2.02 h over 176
mutations is 41.3 s of wall clock each at 8-way, against 39.6 s over 170 five days earlier — a
factor of 1.043 while the suite each run pays for grew 1696 → 1804 tests, a factor of 1.064. So
the cost per mutation tracks the size of `TEST_FILES` and not the number of mutations, which is
the reason the trigger in `CLAUDE.md` is written about the *denominator*. Split out per shard: one
suite run went 302 s → 315 s, the six new mutations are 0.75 extra per shard and cost about four
minutes of the 8.75-minute rise, and the 108 tests added inside the existing files cost the rest.

Two of those numbers correct things this file said earlier. **The serial figure is thirteen
hours, not fifteen.** Fifteen came from the whole-repo suite — 1875 tests, 368 s — and the
harness does not run the whole repo, it runs the 27 files in `TEST_FILES`. The heading above
that says "the fifteen hours it declined to spend" is left as written, dated, with this
correction beside it, on the same principle the rest of the file follows: an amended record
shows the amendment.

The second is more interesting, because it changes what parallelism buys. A mutation that
breaks a lot finishes *fast* — 45 s against a 271 s baseline for `utf8_sig` — because 142
tests fail early instead of doing their work. So the run's cost is dominated by the mutations
with **small** kill counts, the ones whose suites run almost to completion. That inverts the
intuition: the cheap mutations to measure are the ones already well covered, and the
expensive ones are the thin spots. It also means 8-way contention (305 s vs 271 s, +12.5%)
is a worse deal than it looks on the well-covered mutations and a better one than it looks
overall.

### Why eight shards is not a shortcut

Four things had to be true before parallelism could be trusted, and they were checked in that
order because each later one assumes the earlier.

**Do the trees interfere?** No, and not because of the marker. Each shard is a separate
`run.py` process with its own `tempfile.TemporaryDirectory`, its own `pristine` copy, and a
fresh `copytree` per mutation — shards never share a tree, so `.mutations-applied` is not
doing any work across shards. It remains what it was: the refusal of a *second* edit to *one*
tree, which is a within-shard property and unchanged by there being eight of them. Saying
"the marker works in parallel" would be claiming a guarantee from a mechanism that is not
positioned to give it, and the tree contamination that prompted this review came from a
hand-built tree in a shell, not from the loop.

**Is there a shared filesystem resource?** All 27 files in `TEST_FILES` derive their `ROOT`
from `__file__`, so a test running inside a copy addresses that copy — this is the mechanical
property the whole scheme rests on, and it is worth naming as such rather than treating as
incidental. `results/` writes go to the copy's `results/`, `tmp_path` is per-test as always,
and the two things that *are* shared, `data/raw` and `sealed/`, are symlinks that no mutation
edits and no test writes through. Two concurrent suites in two copies were run against a
183-entry before/after manifest of the real repository: nothing outside the copies changed
except `.claude/settings.local.json`, which is this session's own editor permissions file,
gitignored, and referenced by no test — a false positive of the checker, named here because
"one file changed and I decided it didn't count" is exactly the kind of judgement that should
be on the record rather than in someone's head.

**Per-shard baseline or one shared one?** Per shard: each measures its own, which costs 8 ×
271 s ≈ 36 min of the run and is worth every second, because a baseline is the only thing
that can tell a shard its copy is sound. What then needs detecting is disagreement *between*
them, and the naive check — do all shards report the same test count — is not sufficient.
**Two different trees can collect the same number of tests and behave differently**, and a
kill count is a statement about behaviour, not about collection. So each shard also hashes
its pristine copy (`tree_fingerprint`: every non-symlinked, non-`__pycache__` file's path and
content, `.git` and the marker excluded) and the driver refuses the run unless all eight
agree. Symlinks are skipped rather than followed, which is a data rule and not an
optimisation: hashing through `sealed/` would read the sealed test fold for the sake of a
checksum.

**What happens when a shard fails?** This is the question the whole driver is built around,
because the answer "record what we got" is how a partial run becomes a full-run record. A
shard killed by the OOM reaper prints no failures. Eight shards that between them skipped a
mutation print no failures either. Both read as green — the same ambiguity as the loader
fixture that turned a bug into `pytest.skip`, one level up: *the mechanism cannot distinguish
"the check passed" from "the check did not run", and resolves it in the reassuring
direction.* So `parallel.py` writes the word "full run" only if five invariants hold:

| invariant | what it catches | what it does *not* claim |
|---|---|---|
| exact partition — each of the 170 names appears exactly once across shards | a name in two shards (two runs, one guarantee) and a name in none (silence that reads as a pass) | that the counts are right, only that each was taken once |
| one tree — identical fingerprint *and* identical baseline total | shards launched across an edit, a copy that failed halfway | nothing about the run's own duration |
| every shard finished — `complete: true`, written last on purpose, and an exit status of 0 or 1 | a shard killed mid-run whose 21 good measurements would otherwise read as 22 | that the shard's verdicts are correct |
| verdicts account for everyone — caught + survived + stale + broken + dirty equals the number registered | a result dropped between shard and aggregate, which leaves the totals internally consistent | anything the partition check does not already cover |
| the tree did not move — `git rev-parse HEAD` and `git status --porcelain` compared start to end | an edit at minute thirty | *that the counts are wrong* — see below |

The last one deserves its distinction from the second, because it is easy to read them as one
check. Fingerprints are taken at shard **start**, so they certify that all eight *copied* the
same tree; they say nothing about the two hours after. An edit at minute thirty leaves every
fingerprint identical and every kill count valid — the shards genuinely all measured the
pre-edit tree. What it destroys is the record's **provenance**: the claim that re-running at
the named commit reproduces these numbers. That is worth refusing a full-run label over and
not worth calling the counts wrong, and the refusal text says so in those terms.

A failed invariant does not delete the run. It writes an `INCOMPLETE` entry naming what was
missing, leaves the counts sidecar alone so a partial run cannot become the next comparison's
baseline, and exits 2 — distinct from exit 1, which means the run was complete and something
survived.

### The controls were tested before being relied on — 2026-08-20

Relaxing "do not touch the repository for two hours" into "the driver will notice" is only
safe if the driver actually notices, and that had never been demonstrated. Two smoke runs
(`--smoke 1 --shards 2`, ~5 min each) were staged against a throwaway untracked file, run
sequentially so attribution was unambiguous:

| control | how it was provoked | result |
|---|---|---|
| one tree (invariant 2) | shards staggered 6 s; the probe file existed only across shard 1's tree copy | fingerprints `efa4a93b7aa4` vs `9a74da2d7a67`, **baselines identical at 1696**, `INCOMPLETE`, exit 2 |
| tree did not move (invariant 5) | no stagger; the probe appeared 40 s in, well after both copies, and stayed to the end | fingerprints identical, porcelain differed, `INCOMPLETE`, exit 2 |

Each fired exactly one invariant and left the other silent, which is the part that makes them
controls rather than two ways of failing. The first is the demonstration that the fingerprint
earns its keep: **both shards collected 1696 tests and the run was still refused.** A
baseline-count check alone would have called that a full run. Afterwards `git status
--porcelain` was byte-identical to before.

Also worth recording is which control does the work the reader probably means. "The
fingerprint will catch it if the repository changes" is not quite right — the fingerprint
catches shards that *started* on different trees, a window a second or two wide. What catches
an edit during the run is invariant 5. They were validated separately for that reason.

Two affordances exist for this and are honest about it: `run.py --limit N` and `parallel.py
--smoke N` cap the work so the driver can be exercised end to end in minutes. `--smoke`
refuses to write the full-run log at all, prefixes its record `SMOKE`, and never touches the
counts sidecar, because a five-minute run sitting in the full-run log is precisely the
confusion the log exists to prevent.

### When a full run is required

The trigger is in `CLAUDE.md`; what follows is why it is drawn where it is.

**One condition is categorical, and it is not "a lot changed".** Every kill count is a
fraction of `TEST_FILES`. Change which files are in that list and the recorded counts are not
*stale*, they are **about a different denominator** — this file went 11 files/531 tests → 27
files/1696 tests, and a hundred-odd table cells silently became incomparable. So a change to
`TEST_FILES` membership is the one thing impact scope cannot cover, because the change is to
the denominator of all 170 counts rather than to any one of them. That is decidable rather
than a judgement call, which is why it is a test:
`test_the_full_run_covered_the_current_test_files` compares the current list against the one
recorded in the sidecar and fails when they differ. Deliberately a failure and not a skip —
a skip here would be the vacuous absence this file documents twice — and safe to be hard
because the test lives outside `TEST_FILES` and so cannot redden a mutation baseline.

The other full-run conditions are broader and admit judgement: two or more `src/` modules, a
path several mutations share, or a change to the harness's own measuring apparatus
(`run_suite`, `make_tree`, `kills`, `outcomes`, `Mutation.apply`, `tree_fingerprint`) — that
last one because a harness change alters *how* every count is taken, which is the same
argument as the denominator in a different coat. Plus two calendar-ish triggers: no full run
on record within 60 days, and immediately before quoting kill counts in a paper or release.

**Everything else is served by impact scope**, whose scope is the changed tests' runtime
reach and not filename overlap. The list goes up before the run starts, and what was not run
is recorded as *deferred to the next full run* — never as an exemption. That phrasing is not
politeness: a partial run described without its gap reads as full coverage, which is the
failure this file is about, arriving through the write-up instead of through the code.

**Where the last full run is recorded: `docs/notes/mutation-full-runs.md`, and nowhere else.**
Not in `CLAUDE.md` beside the trigger, not in this section, not in `DESIGN.md`. A date written
in two places has one place that is out of date, and nothing on the page says which. The
sidecar `mutation-full-runs.counts.json` holds the machine half — every count, the commit, the
baseline, and the `TEST_FILES` list the counts are counts of — so the next run's diff is against
a measurement rather than against a scrape of these tables.

**Why the trigger sits in `CLAUDE.md` and not `DESIGN.md`.** `DESIGN.md` governs the
experiment: pipeline, agents, axes, matching policy, and the rule that it must be edited
before any of those change. Whether a test gate ran in full changes no experimental claim,
and putting engineering process there would weaken that rule by making the file a place where
process also lives. `CLAUDE.md` already carries exactly this shape of thing — "커밋 전
`python tools/release_screen.py` 를 실행한다" is a pre-commit gate stated as an imperative
with its detail elsewhere — and it is the file that is in context when the commit is being
made. A rule that has to be looked up is consulted by someone already thinking about
mutations, which is precisely not the state of the person who just added a file to
`TEST_FILES`.

### The first full run — 2026-08-20

**170 attempted, 170 caught, 0 survived, 0 STALE, 0 BROKEN, in 1.87 h across 8 shards.**
Commit `50ea5e2`, working tree dirty (the driver and its tests were the uncommitted change,
and the run measured the tree that this commit then froze byte-for-byte). Tree fingerprint
`55708073957b6e6f` on all eight shards, baseline 1696 on all eight. Full record, with the
per-mutation table and the `TEST_FILES` list the counts are counts of:
`docs/notes/mutation-full-runs.md`.

The gate had never been run in full at 170 before this. That is the debt the run paid, and it
is worth being plain about what it means: every count in this file older than 2026-08-19 was a
number nobody had re-checked under the suite it now belongs to, and the claim "all mutations
are caught" had never been true of the whole set at one commit.

**Sixteen counts rose. None fell. Every single rise was against a number measured before the
suite grew from 11 files to 27 — and all 72 figures measured on 2026-08-19 came back
identical, 72 of 72.** The second clause is the one worth having: those 72 were measured a day
earlier by eight shards in a different arrangement, and reproducing them exactly is evidence
that the sharding, the per-shard baselines and the fingerprint are doing what they claim
rather than that they happen to agree with themselves.

It is *not* a vindication of the deferral's wording, and the difference matters. That section
predicted the 99 unmeasured mutations were "unchanged"; 16 of them rose. None of the 16 rose
because coverage moved — their recorded numbers were 11-file-era numbers, so "unchanged" was
never a testable claim about them, and writing it as an expectation blurred *not re-measured
under this suite* into *measured and unchanged*, which is the very distinction the
`(2026-08-19)` markers had been introduced to keep. The deferral was right that nothing was
lost and wrong about what it was in a position to expect.

| rise | was | now | reading |
|---|---|---|---|
| `an_unreadable_tree_state_reads_as_clean` | 5 | 107 | the largest, and pure blast radius: an unreadable git state makes every screener test that asks git a question fail |
| `type_in_both_lists` | 78 | 150 | the vocabulary check runs in the fixture of most of the added files |
| `unsealed_load_filters_instead_of_not_reaching` | 73 | 145 | the seal is asserted by every suite that loads a corpus |
| `utf8_sig`, `no_bom_shift` | 70 | 142 | the BOM shift is upstream of every offset in every test |
| `the_reply_text_is_taken_from_the_first_block` | 16 | 85 | every test whose arm makes a model call reads the reply |
| `arm_rules_path_drops_the_axes` | 6 | 74 | the arm's rules path is where the added loop tests write |
| `rule_id_vocabulary_not_checked` | 8 | 20 | " |
| `arm_rules_path_drops_the_iteration` | 4 | 10 | " |
| `filled_prompt_paths_allowed` | 3 | 4 | see the note below — this one was recorded and my scrape missed it |
| `arm_rules_path_loses_the_rules_component` | 3 | 7 | |
| `the_history_is_pre_seeded_with_this_rounds_rate` | 3 | 5 | |
| `fully_covered_is_relaxed` | 11 | 14 | |
| `leak_rate_from_assignment` | 9 | 10 | |
| `greedy_allows_reuse` | 8 | 9 | |
| `a_mismatched_model_is_recorded_rather_than_refused` | 2 | 3 | |

Every one of them is the reading `### The impact-scope run` already argued for: **a large kill
count is a statement about blast radius and only a small one is a statement about coverage.**
Nothing in this list is a new assertion about the mutated guarantee — they are all cases where
sixteen more test files now pass through the mutated line on their way somewhere else. The
counts to read for coverage are still the small ones: **62 mutations are caught by exactly one
test, and 98 by two or fewer.** Those are the thin spots, and the full run's contribution is
that the number is now measured across the whole set rather than across the part somebody
happened to re-run.

**A correction the run produced about its own comparison.** The previous figures were scraped
out of this file's tables, and the scraper mis-parsed one row: `filled_prompt_paths_allowed`'s
cell contains a literal `|` inside `prompts/(filled|rendered)/`, so the count column was read
from the wrong side of it. The consequence is small and worth naming exactly — the machine
record in `docs/notes/` says "15 increases, 35 first measured here" where the truth is 16 and
34, because that mutation had a recorded count of 3 and was compared against nothing. The
sidecar written by this run has no such problem: it is measurements, not a parse of prose,
which is the reason the next comparison is against it and not against these tables.

**48 counts matched an 11-file-era number exactly**, which is its own small finding: for those
mutations the sixteen added files contribute no kills at all. The guarantee is pinned by the
original eleven and by nothing since.

Thirty-four mutations had no recorded count anywhere in this file, their sections stating
`min_kills` in prose. Measured here, so that the file's coverage claim is complete:

| mutation | measured | `min_kills` |
|---|---|---|
| `the_per_iteration_key_replaces_the_arm_level_one` | 110 | 2 |
| `the_mask_tags_are_emitted_in_the_order_they_were_applied` | 85 | 3 |
| `the_audit_report_is_read_as_the_previous_rounds_file` | 69 | 1 |
| `k_drops_to_one_so_consecutive_means_nothing` | 11 | 3 |
| `the_writer_adds_the_rounds_up_itself` | 10 | 1 |
| `an_unknown_flag_field_is_ignored_instead_of_refused` | 8 | 1 |
| `delta_reverts_to_the_constant_half_point` | 6 | 2 |
| `overlapping_mask_tags_are_accepted` | 5 | 3 |
| `a_ceiling_stop_is_recorded_as_converged` | 4 | 2 |
| `a_heterogeneous_union_prints_one_of_its_types` | 4 | 3 |
| `both_halves_of_the_export_use_one_mode` | 4 | 2 |
| `prompt_tokens_is_what_the_invoice_was_computed_on` | 4 | 3 |
| `round_one_ignores_the_feedback_it_was_handed` | 4 | 1 |
| `a_flag_overlapping_a_mask_tag_is_kept_when_it_is_not_contained` | 3 | 2 |
| `missed_is_the_unmatched_gold_rather_than_the_uncovered` | 3 | 3 |
| `the_iteration_allow_pattern_covers_the_whole_directory` | 3 | 1 |
| `the_probe_error_carries_the_exception_message` | 3 | 1 |
| `a_total_below_its_round_is_published` | 2 | 2 |
| `an_out_of_range_column_is_snapped_to_the_line` | 2 | 2 |
| `tags_out_of_order_are_sorted_instead_of_refused` | 2 | 2 |
| `the_audit_report_is_allowed_instead_of_denied` | 2 | 2 |
| `the_export_index_is_the_in_scope_position` | 2 | 1 |
| `the_lifecycle_probe_can_abort_the_arm` | 2 | 2 |
| `an_empty_lifecycle_mapping_is_written_as_no_probe` | 1 | 1 |
| `an_undefined_rate_prints_as_zero` | 1 | 1 |
| `only_the_round_the_report_names_is_checked` | 1 | 1 |
| `round_one_reassembles_the_baselines_prompt` | 1 | 1 |
| `summing_carries_an_undeclared_key_into_the_total` | 1 | 1 |
| `summing_takes_the_longest_call_as_the_wall_time` | 1 | 1 |
| `the_assembled_total_is_trusted_rather_than_checked` | 1 | 1 |
| `the_audit_report_gets_a_second_path_key` | 1 | 1 |
| `the_export_reads_the_missing_index_as_zero` | 1 | 1 |
| `the_report_reads_its_own_round_as_the_masked_one` | 1 | 1 |
| `the_score_block_carries_the_run_and_cost_blocks_too` | 1 | 1 |

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
