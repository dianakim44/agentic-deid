# Before a sealed evaluation is run

One list, to be worked top to bottom immediately before the run, because the run happens
once per arm (DESIGN §6.4) and nothing in it can be withdrawn. The append to
`results/sealed_eval_log.md` is the point of no return: after it, the fold has been opened
whether the scoring finished, crashed, or produced a number nobody likes.

**Read this together with `sealed_eval_log.md`'s own "What must be true before a row is
added".** That section states the five conditions the *log* asserts about every row. This
file is the operational version — what to run, in what order, and what the answer has to
be — plus the items no code can check.

## What the code already refuses

Written down so the human items below are the ones actually left. `plan_arm` reads only
committed dev artefacts and refuses, before the fold is reachable: an undeclared axis
value; a round that is not a positive integer; a missing or unparseable dev
`metrics.json`; a dev record whose `run.split` is not `dev`; an arm with no `termination`
block or a `null` reason; **a round that is not the arm's final round**; a record naming no
`rules_source`; a rule file that has moved; and a round-scoped record that disagrees with
the arm-scoped one about rules, `rules_source` or `rules_version`. `load_sealed` then
refuses a dirty tree, appends the row, and verifies the frozen split — in that order, and
a failed append aborts the run. `write_metrics` refuses `sealed=True` with a round, and
refuses either of `sealed` / `split == "test"` without the other.

So the failure modes left are the ones about *judgement and state*: the wrong arm, a stale
tree, an unmeasured suite, a purpose nobody can interpret later, and a decision made after
the number exists.

## The list

1. **The user has said to run it.** Not implied by the checklist being complete, and not
   implied by an earlier "yes" to something else. There is one opening per arm.

2. **The arm is a *reported* arm and it terminated by its own pre-registered rule.**
   `docs/notes/arm-*.md` names the rule and the round it fired at. A failed arm gets no
   opening (DESIGN §6.4): `port-oneshot` died of format failure and is reported from
   `format_failure.json`. A repair is a new arm with a new `porting` value, not a second
   opening of this one.

3. **The round is the arm's last.** Read `termination.iterations` from the arm's
   `metrics.json` and pass that as `--iteration`. `plan_arm` enforces it; the reason it is
   also on this list is that the argument for an earlier round arrives *after* the dev
   numbers do, and the checklist is where it should already have been settled: an
   earlier-round headline is fewer rounds of quality beside the arm's full `cost_to_date`,
   and no run exists that both cost that and scored that.

4. **`--verify-dev` agrees.** Same plan, same rule files, dev fold, compared key by key
   against the arm's committed `metrics.json`:

       python3 -m src.eval.run_sealed_eval --corpus es-meddocan \
           --arm R/sup-free/port-loop --iteration 8 --verify-dev

   It writes nothing, opens nothing and adds no row. A disagreement here is not a
   formality — it means the scoring path and `run_fold` compute different things, and the
   sealed number would be the first and only observation of the difference.

   **Run for the first time on 2026-08-28, and it agrees.** Output, in full:

       plan    es-meddocan R/sup-free/port-loop round 8: 1 rule file(s), 60 rules,
               dev leak rate 13.72%
       verify  dev scoring reproduces the arm's committed metrics.json
               (counts, false_positive_opportunity, headline, modes)
       nothing was opened and nothing was written

   0.86 s. The four names in the parentheses are the keys actually compared — everything
   `score()` returns except `scorer_version`, which the record folds into the run block
   (`run_sealed_eval.UNCOMPARED_KEYS`). `complementarity` and `per_type` are inside `modes`
   and are compared with it.

   **What it establishes:** the sealed module's `score_fold` reproduces, key for key, the
   number `run_fold` committed for this arm — so the two paths differ in which documents
   they are handed and in nothing else. That is the property a once-only run needs and the
   only way to have it before the run.

   **What it does not:** it never calls `load_sealed`, so the dirty-tree refusal, the
   append, the frozen-split verification and the loader's gate itself are all unexercised by
   it. Those have tests (`tests/test_seal.py`, `tests/test_sealed_scoring.py`) and no
   rehearsal — the first genuine execution of that path is the run. It is also silent about
   *whether the fold is worth opening*, which is items 1–3 and 9–12 here.

   Re-run it on the day of the opening regardless of this record: it is the check that
   catches a rule file edited since the arm closed, which is a state that can arise between
   any two commits. It was re-run on 2026-08-28 before the opening and agreed again.

   **What "unexercised" cost, the same day it was written.** The first attempt to open the
   fold — correct arm, correct round, correct purpose, every item on this list green — was
   refused by the loader's gate, because `python3 -m src.eval.run_sealed_eval` executes the
   file as `__main__` and so put no frame named `src.eval.run_sealed_eval` on the call stack.
   The documented invocation could not pass its own check. `--verify-dev` had just agreed,
   twice, and could not have caught it: the paragraph above is the reason, in writing, before
   the fact.

   Nothing was lost. The identity check runs before the append, so no row was added and
   `count_runs` stayed 0 — the ordering guarantee doing precisely the job it is for, on the
   first occasion it was ever load-bearing. The entry point was fixed, given a test that
   drives it through `runpy.run_module(run_name="__main__")` because no import-based test can
   reproduce `-m`, and given the mutation `the_entry_point_calls_its_own_copy_of_main`
   (caught, 1) so that the fix cannot be quietly undone. The run then proceeded on the
   corrected commit.

   The item this changes for next time is not item 4 but the reading of it: a rehearsal that
   stops short of the gate leaves the gate's *admit* path untested, and a gate that refuses
   everything looks exactly like a gate that works. If a second corpus is ever sealed, run
   its first opening as a deliberate rehearsal against a fold you are willing to spend before
   the one you are not.

5. **The suite is green and the gate is current.** `python3 -m pytest -q` with no
   failures, and `docs/notes/mutation-full-runs.md`'s last full run covering the current
   `TEST_FILES` (`test_the_full_run_covered_the_current_test_files`). CLAUDE.md requires a
   full run before a kill count is cited in a paper or release; a sealed evaluation is that
   moment for the seal's own mutations, because the count is what licenses the claim that
   the fold was reachable only this way.

6. **`python3 -m src.split --corpus {corpus} --check` passes.** The fold about to be scored
   is the fold frozen at the commit named at the top of `sealed_eval_log.md`.
   `load_sealed` verifies it too, but *after* the append — so checking here is the
   difference between not running and having run.

7. **`python3 tools/release_screen.py` reports no BLOCKED.** Read the individually printed
   SUSPECT lines. `results/{...}/test/metrics.json` is on the ALLOW list, declared in the
   commit that built the scoring path rather than after the file exists.

8. **The tree is clean and committed.** No `--allow-dirty`: the row records a commit hash,
   and on a dirty tree that hash does not describe the code that ran. If the tree is dirty,
   commit first — the run is cheap to postpone by one commit and impossible to postpone
   afterwards.

9. **`count_runs()` is what you expect it to be.**

       python3 -c "from src.eval.sealed_log import count_runs; print(count_runs('es-meddocan'))"

   Before the first opening this is `0`, and that zero is also the evidence that DESIGN
   §6.4 was pre-registered without the numbers in view. Any other value means an opening
   happened; find its row and read it before adding another.

10. **The purpose string is decided in advance and says which claim the run supports.**
    It goes in the row verbatim and is the only free text there. "test" or "final run" is
    not interpretable a year later; "pre-registered final evaluation of the port-loop arm"
    is.

11. **The arm's dev headline is already written down** — in `docs/notes/arm-*.md` and the
    committed `metrics.json`. After the sealed number exists the two are easy to conflate,
    and the dev value is what every design decision was actually made on.

12. **Nothing downstream is waiting on the answer to choose anything.** No rule edit, no
    threshold, no checkpoint, no merge policy. Fixing a dev error is allowed; fixing a test
    error is forbidden (CLAUDE.md), and that prohibition binds from the moment the row is
    appended, not from the moment the paper is written.

13. **No repository edits while it runs**, for the reason a full mutation run has the same
    rule: the commit hash in the row must keep describing the code that produced the number.

## If it fails after the append

The row stays. The opening happened, the count includes it, and the honest report is a row
whose purpose says what was attempted plus the failure. What must **not** happen is a
second run described as the first, or a deleted row: the count is the paper's N, and a log
that can lose a row supports no claim at all. Diagnose from the traceback and the arm's dev
record — both are outside the seal — and if a fix genuinely requires another opening, that
is a decision for the user with the failed row in front of them.
