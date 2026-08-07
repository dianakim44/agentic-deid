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

## The four revisions

All four are before iteration 1's rule work. At every one of them:

- `rules/es.yaml` did not exist — no rule had been written for this arm,
- `human_log.jsonl` held exactly one line, `event: read_sample`,
- that line's `human_minutes` was `null`, and every other judgement field was `null`,
- `rules_commit` was `null`.

So no human attention had been spent under any of these windows, which is why
`rule_author.md` §7 permits the revision and why the arm is not invalidated. `n = 40`,
`context_chars = 120`, `min_per_type = 1`, `base_seed`, and `seed_scheme` were unchanged
throughout — `sampling_sha256` never moved, and the drawn sample is byte-identical across
all three.

| # | commit | `prompt_sha256` | what moved it |
|---|---|---|---|
| 1 | `173935a` | `5f72f7694c3e…` | the original freeze, before any `port-human` work |
| 2 | `aa3b066` | `53319281f8ad…` | §8 (the arm-contamination clause) added to `rule_author.md` |
| 3 | `f51aa9c` | `493effc2b9fc…` | a prose edit to §8.1's boundary-case table, and a paragraph added to §7 |
| 4 | this commit (`fix: make the window freeze actually immutable`) | `bc83e2c7126b…` | §7 gained the paragraph describing the corrected guard, and the freeze-last lesson |

`sampling_sha256` = `fbfbbe107e2e…` at all four. Revision 4 has no hash of its own here
because it is the commit that adds this row; it is the one whose `prompt_sha256` matches
the record on disk.

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
its first minute the same edit would have cost a restart. It is also the last revision
this window can absorb for free.

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

Two honest limits. `git checkout` or an editor can still change the file — this is a
guard on the module's own write path, not on the filesystem, and nothing in a Python
function can be otherwise. What it does buy is that no *code path in this repository*
produces a rewritten freeze after the arm starts, and that a rewrite is therefore a
manual act leaving a diff rather than a function call reporting success. And it depends
on the log: deleting `human_log.jsonl` would re-open the freeze. That is a much louder
act — the log is the arm's only record of what a person did and how long it took — and it
is the reason the guard reads the log rather than the record it is protecting.

Pinned by `test_deleting_the_record_and_re_freezing_is_refused_after_minutes_are_logged`
and by the mutations `freeze_guard_only_checks_the_file` and
`arm_started_reads_the_last_line_only`.

## The family this belongs to

`tests/mutations/README.md` collects the incidents where a mechanism could not
distinguish two cases and resolved the ambiguity in the reassuring direction: a `skip`
reported as a pass, a collection error reported as thirty-seven kills, a rule-violating
sample reported as well-formed. This is the same shape with the ambiguity moved into a
guard's precondition — `exists()` could not distinguish *no freeze has been taken* from
*the freeze was just deleted*, and both readings produced a cheerful successful write.
