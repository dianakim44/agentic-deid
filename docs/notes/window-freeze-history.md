# Window freeze history — `es-meddocan / R / sup-free / port-human`

Every value the freeze record has held, and why it changed. This file exists because
the record was rewritten three times before iteration 1 despite `freeze_window()`
documenting that it "refuses to overwrite an existing record", and because a corrected
guard is not a substitute for the history it failed to prevent. A fourth revision follows
those three and is listed with them: it is the one the corrected guard evaluated and
allowed, and it belongs in the same list precisely so that "the guard permitted it" is not
a reason a change goes unrecorded.

The record itself carries only the current window. That is correct — it is the fixed
point the log lines are compared against, and a record that accumulated its own
revisions would be answering a different question. So the revisions go here, in a file
whose whole purpose is to be a list.

## What the guard actually guaranteed

`freeze_window()` refused to write when `freeze_path().exists()`. Nothing more. The
sequence that ran three times was:

```
rm results/es-meddocan/R/sup-free/port-human/window_freeze.json
python -c "from src.porting.human_arm import freeze_window; freeze_window(...)"
```

`exists()` is `False` after the `rm`, so the function took its write branch and reported
a successful freeze. No exception, no warning, and — the part that matters — nothing in
the resulting file distinguishes it from the original. It hashed the files as they stood
at that moment and presented that as the opening window.

**A refusal conditioned on the presence of the thing being protected is not a refusal.**
It is a request, addressed to exactly the person in a position to remove the evidence.
Worth stating plainly because the docstring's reasoning was sound and its conclusion was
false: it argued correctly that a rewritable freeze record answers the wrong question,
and then implemented a check that a rewrite trivially steps around.

This was not an attack, and framing it as one would miss the lesson. Each `rm` was
deliberate, each was reported in the commit message and the session notes as a re-freeze,
and each was justified by `rule_author.md` §7's allowance for a pre-start revision. The
justification was even correct. The defect is that the code's *only* enforcement was a
condition the operator controlled, so the correctness of every re-freeze rested on the
operator's own judgement about whether §7 applied — which is what the record was supposed
to remove from the operator's hands.

## The six revisions

All six are before iteration 1's rule work — which, after revision 6, is where they will stay. At every one of them:

- `rules/es.yaml` did not exist — no rule had been written for this arm,
- `human_log.jsonl` held exactly one line, `event: read_sample`,
- that line's `human_minutes` was `null`, and every other judgement field was `null`,
- `rules_commit` was `null`.

So no human attention had been spent under any of these windows, which is why
`rule_author.md` §7 permits the revision and why the arm is not invalidated. `n = 40`,
`context_chars = 120`, `min_per_type = 1`, `base_seed`, and `seed_scheme` are unchanged
throughout, and **the drawn sample for iteration 1 is byte-identical across all five** —
which is the property that matters, and it is not the same claim as "`sampling_sha256`
never moved". At revisions 1–4 it did not move. At revision 5 it did, and the sample
still did not, for the reason given below.

| # | commit | `prompt_sha256` | what moved it |
|---|---|---|---|
| 1 | `173935a` | `5f72f7694c3e…` | the original freeze, before any `port-human` work |
| 2 | `aa3b066` | `53319281f8ad…` | §8 (the arm-contamination clause) added to `rule_author.md` |
| 3 | `f51aa9c` | `493effc2b9fc…` | a prose edit to §8.1's boundary-case table, and a paragraph added to §7 |
| 4 | `bc83e2c` era (`fix: make the window freeze actually immutable`) | `bc83e2c7126b…` | §7 gained the paragraph describing the corrected guard, and the freeze-last lesson |
| 5 | this commit | `5786260c6d93…` | `config/sampling.yaml` gained `practice_iteration_min: 900` and §2 of the prompt gained the two regex-free matcher forms — **both** hashes moved, the first time either has moved together with the other |

| 6 | this commit (`docs: retire port-human, adopt port-oneshot as the baseline`) | `558bfe3fe86f…` | the arm was **retired**; §§7–8 of the prompt gained dormancy banners and the header was rewritten |

`sampling_sha256` = `fbfbbe107e2e…` at revisions 1–4 and `4c0e2cc725d3…` at revisions 5–6.
Revision 6 has no commit hash of its own here because it is the commit that adds this row;
it is the one whose two hashes match the record on disk.

Revision 3 is the one with no excuse. Revisions 1→2 added a clause the arm needed and
the arm had not started. Revision 3 was a *prose* edit to a section written minutes
earlier, and it happened because the freeze was taken before the prompt had stopped
moving. The lesson is an ordering one and it costs nothing to follow: freeze last.

Revision 4 is the corrected guard's own cost, and it is worth being precise about why it
is legitimate rather than a fourth instance of the thing this file is about. §7 needed a
paragraph saying what now enforces its allowance — a prompt that describes a discipline
the code has since taken over would be wrong about its own subject — and writing it moved
`prompt_sha256`. The re-freeze went through the *new* guard: `arm_has_started()` returned
`False`, because the single log line still carries a null `human_minutes`. That is the
design working as intended rather than being stepped around, and after iteration 1 records
its first minute the same edit would have cost a restart.

Revision 4's entry originally ended with the sentence "It is also the last revision this
window can absorb for free." That was wrong within days, and it is corrected here rather
than deleted, because a note about a repeated mistake that quietly edits out its own
prediction is a worse document than one that records the prediction failing. What it got
wrong was not the arithmetic — no minutes have been recorded, so revision 5 is as free as
revision 4 — but the confidence. "Last" was a forecast about what else would need to
change before iteration 1, made by the same process that had already been surprised three
times.

### Revision 5, the first one to move both hashes

Two changes, one revision, because they are one uncommitted change and splitting them
across two rows would record a revision 6 that never existed on disk.

`config/sampling.yaml` gained one key, `practice_iteration_min: 900`, reserving the 900s
for practice runs so that a rehearsal cannot draw a real iteration's sample (see
`docs/notes/port-human-practice.md`, DESIGN §11.1). `docs/prompts/rule_author.md` §2 gained
the two regex-free matcher forms — `terms:` and `cue:`/`then:` — which the schema had
supported since the loader was written and the prompt had never mentioned. Both files are
hashed by the freeze, so `window_drift()` reported first `['sampling_sha256']` and then
`['prompt_sha256']`, and the arm was re-frozen once, at the end, with `started_where()`
returning `None`.

**The config half is different in kind from revisions 1–4 and the difference should be
stated rather than absorbed.** Those four moved `prompt_sha256` — prose in the prompt — and
left every number the draw depends on alone. This one also moves the *config* hash, which
is the hash that exists to catch a change to `n`, `context_chars`, `min_per_type`,
`base_seed` or `seed_scheme`. So the honest question is whether iteration 1's sample
changed, and the answer is verifiable rather than argued: the new key is read only by
`check_iteration()` / `is_practice()` / `practice_pool()`, none of which is on the path
from `(corpus, iteration, error list)` to a sample, and `sample_seed()` hashes a fixed
scheme string with `base_seed`, the corpus and the iteration. The drawn forty are the same
forty.

**The prompt half is a documentation gap and not a change of instruction**, which is worth
distinguishing because a prompt revision that alters what the arm is asked to do would
invalidate the comparison rather than cost a re-freeze. §2 previously listed `pattern` as
the matcher for every rule and mentioned `lexicon:` for gazetteers, so an agent reading it
would author gazetteer and context_cue rules as regexes — which they compile to anyway. The
rules it can express are unchanged; what changed is whether it knows the shorter forms
exist. That distinction matters specifically for §7: its prediction is per layer, and an
author who can only express itself in regex writes regex-shaped rules, at which point a
layer looks weak for reasons that have nothing to do with the phenomenon. Leaving §2 as it
was would have been the more conservative act on the freeze and the less conservative one on
the finding.

That the hash cannot distinguish "the window changed" from "an unrelated key was added to
a file the window hashes" is not a defect to fix. A per-key hash would be a record that
agrees with any edit to a key nobody thought to enumerate, and the whole point of hashing
the file is that it notices what nobody anticipated. The cost is exactly this: a paragraph,
once, saying which kind of change it was. `window_drift()` is documented as reporting
drift rather than refusing on it for this reason — "only a person can tell them apart".

The practice band could have gone in a second file to keep `sampling_sha256` still. That
would have bought a cleaner history at the price of two files defining the sampling window,
which is the arrangement §11.1's "the values are in a config file" sentence exists to
prevent, and the arrangement that makes a future reader ask which file was authoritative.

One ordering lesson repeats here for the fourth time and is now cheap to state: **freeze
last.** Revision 5 moved `sampling_sha256`, was re-frozen, and then moved `prompt_sha256`,
requiring a second re-freeze in the same uncommitted change. Nothing was lost — the guard
was consulted both times and returned `None` both times — but the first re-freeze was
wasted work that a moment's thought about what else the practice session still needed would
have avoided. The freeze is a record of a settled state, so writing it while the state is
still moving records a state that never mattered.

### Revision 6, and why a retired arm was re-frozen at all

`port-human` was withdrawn on 2026-08-07 for want of a human author (DESIGN §11, §4.1).
The prompt is one of the two hashed files, and retiring the arm meant editing it — a new
header, dormancy banners on §§7–8 — so `prompt_sha256` moved one last time.

**Re-freezing an arm that will not run looks like pointless bookkeeping, and the argument
for doing it is about what a revival inherits.** The alternative was to leave the record
showing revision 5's hash while the prompt on disk hashes to something else. `arm_has_started()`
is still `False`, so `window_drift()` would report `['prompt_sha256']` to whoever next ran
the tooling, and they would have to reconstruct from git whether that drift was the
retirement edit or a real change to the window. A record that disagrees with the files is
not a safer record for being older; it is a record that needs a person to interpret it,
which is what §11's freeze exists to avoid.

So the honest statement is: **this window has been frozen six times and never used.** The
guard was consulted at every one and returned `None` at every one, which is correct and
also the reason none of them cost anything — the arm never recorded a minute. What the
six revisions actually demonstrate is the thing revision 4 got wrong when it predicted it
was the last: **a freeze taken before the surrounding work is settled will be retaken.**
Five of these are prompt edits during a period when the prompt was still being written,
and the sixth is the arm being cancelled. If a human arm is revived, the lesson to carry
is not "freeze more carefully" but "freeze when the author is about to start", because
that is the only moment at which the freeze's claim — *this is the window the run began
with* — is a claim about anything.

The record now on disk is what a revival inherits: revision 6's two hashes, one
`human_log.jsonl` row with a null `human_minutes`, and an iteration-1 window that has
never been drawn for rule-writing.

## What is guaranteed now

`freeze_window()` keeps the `exists()` branch and gains a second condition that a
deletion cannot reach — `arm_has_started()`, which reads the **append-only log** for a
non-null `human_minutes` on any line:

- **Before any minutes are recorded:** re-freezing is permitted, including after a
  deliberate delete. §11.1 puts the revision window exactly here, and a guard that
  refused would be enforcing a rule the design does not have.
- **After the first minute:** `freeze_window()` raises, whether or not the record is
  present. If the record is missing it says so explicitly and says *restore it from git*,
  because a re-created record hashes today's files and then claims to be the opening
  window — confidently wrong is worse than absent.
- **The only remaining path to a different window is re-running the arm from
  iteration 1 with a different author.** That is §11.1's ordering cost paid rather than
  avoided: the same person re-porting the same corpus is not a fresh trial, so there is
  no cheap version of this and the code no longer pretends there is.

One honest limit remains, and it is stated below rather than here, because the first
version of this section had a second one that has since been closed: it read the working
tree's `human_log.jsonl` only, so `rm human_log.jsonl` re-opened the freeze. That was the
same defect one level out — the guard's input was a single file the operator could remove
— and it is now fixed. See the next section.

Pinned by `test_deleting_the_record_and_re_freezing_is_refused_after_minutes_are_logged`
and by the mutations `freeze_guard_only_checks_the_file` and
`arm_started_reads_the_last_line_only`.

## The log's deletion, and why git history is the second source

The paragraph above used to end by admitting that deleting `human_log.jsonl` would re-open
the freeze, and calling that acceptable because deleting the log is a louder act than
deleting the freeze record. It is louder — the log is the arm's only record of what a
person did and how long it took, so its absence is itself a finding. But louder is not
prevented, and an admitted hole in a guard is still a hole: one file, one command, and the
guard's own input is gone.

So `started_where()` has two sources, in this order:

1. **The working tree.** A read of `human_log.jsonl` for a non-null `human_minutes` on any
   line. Cheap, and the answer in every run where nothing has been deleted.
2. **Git history.** `git log --all --format=%H -- <that one path>`, then `git show` on each
   commit's blob, scanned with the same function. If any commit's version of this arm's log
   ever held a non-null `human_minutes`, the arm has started.

Four choices inside that, each with a failure mode behind it:

- **`--all`, not `git log`.** A branch is not a different history of what a person did, and
  checking out an earlier state is not a way to un-spend a minute.
- **Every commit that touched the file, not the newest.** The newest is the one an edit
  would have changed, so reading only it would answer the question a rewriter prefers. This
  is what catches the subtler edit: the file stays in place and the lines carrying minutes
  are removed from it, so the working tree says *not started* and an older commit says
  otherwise.
- **The working tree first.** Both sources can be true at once, and the ordinary case must
  not be reported as the deleted-log one — the `IN_HISTORY` message tells the reader to
  restore a file, which is wrong advice when the file is sitting there.
- **`None` for every `git` failure.** No git, no repository, an unknown revision, a
  timeout: all mean "history says nothing" rather than a traceback out of a guard. This
  fails in the *unsafe* direction, which is why the working tree is consulted first and
  why the limits below are stated plainly instead of being argued away. A guard that
  crashes where it cannot answer is a guard someone deletes rather than fixes; a `git`
  call with no timeout is the same thing more slowly.

**The message names the case.** When the minutes are in history but the log is not in the
working tree, the refusal says so — the log is in git history but NOT in the working tree,
restore it from the same commit — because a refusal that only said "this arm has started"
would leave the reader with a missing log and no reason to notice. Nothing from `git`'s
output reaches the message. Log lines carry no surface forms today, but CLAUDE.md's rule
about exception text does not branch on which file happens to be safe, and a guard that
pastes `git` output into a message is one schema change away from that mattering.

**Local history only, deliberately.** Consulting a remote would put a network call inside a
guard that runs before every freeze and would make the answer depend on connectivity —
which fails in the unsafe direction. It is also unnecessary: this repository is public, so
rewriting the history that holds the minutes leaves a divergence anyone who has fetched it
can see, and a force-push is not a quiet act. The point of the second source is not that
the remote is authoritative; it is that removing the evidence now requires rewriting
history rather than deleting a file.

### What is still not prevented

Two things, and neither is presented as covered:

- **A rewritten history.** `git filter-branch`, an interactive rebase, or a fresh clone
  with the commits dropped would remove the minutes from both sources, and this guard
  would then answer *not started* honestly. Nothing inside a Python function can prevent
  that, and pretending otherwise is the failure mode this whole note is about.
- **A log never committed and then deleted.** Minutes that only ever existed in an
  uncommitted working-tree file are unrecoverable, and the guard reads *not started*. This
  one is asserted as a test
  (`test_a_log_never_committed_and_then_deleted_reads_as_not_started`) rather than left
  implied, so that a future reader finds the boundary stated rather than discovering it.
  The practical consequence: commit the log.

**The purpose of this guard is to prevent an accident and to make a deliberate change
conspicuous, not to make one physically impossible.** Those are different goals and only
the first is achievable in code. The three re-freezes this note exists for were accidents
in the relevant sense — each was deliberate as an action and each was reported honestly,
and none of them was a decision anybody made *about the experiment's validity*, because
the code offered no moment at which that decision had to be made. That is what a guard can
fix. A person who decides to rewrite the arm's history has made the decision, in the open,
and the record of the choice is what the guard buys.

Pinned by `test_minutes_in_a_commit_count_even_with_the_log_deleted`,
`test_minutes_deleted_from_a_committed_log_still_count`,
`test_minutes_on_another_branch_count`, and the mutation
`started_where_reads_the_worktree_only`.

## The family this belongs to

`tests/mutations/README.md` collects the incidents where a mechanism could not
distinguish two cases and resolved the ambiguity in the reassuring direction: a `skip`
reported as a pass, a collection error reported as thirty-seven kills, a rule-violating
sample reported as well-formed. This is the same shape with the ambiguity moved into a
guard's precondition — `exists()` could not distinguish *no freeze has been taken* from
*the freeze was just deleted*, and both readings produced a cheerful successful write.
