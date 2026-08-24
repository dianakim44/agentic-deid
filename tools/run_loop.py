#!/usr/bin/env python3
"""Run one round of `port-loop` — `src.porting.loop` behind named flags.

**Why `tools/run_arm.py` cannot do this.** That tool calls `orchestrate.run_arm()`, which
passes **no `iteration=`** to `run_fold` — deliberately, because `iter1/` under an arm with
one pass is a false statement about the arm (DESIGN §5.5, and the comment at that call).
Pointed at `--porting port-loop` it would freeze the window, make one RuleAuthor call and
write the *un-iterated* `metrics.json` and `spans.jsonl`; round 2 would then refuse on "no
score for round 1", and the arm would be spent with nothing at `iter1/` for a later round to
read. So the iterating arm needs its own driver: this one calls
`loop.run_iteration_1()` for round 1 and `loop.run_iteration(N, …)` for every round after it.

**One round per invocation, and no `--through 8`.** The obvious shape — loop here until
`stop` — is refused. A round past round 1 makes **1 + N** calls (RuleAuthor once, Auditor once
per dev document, `auditor.md` §1.3), so a single command with an 8-round ceiling has a blast
radius of about two thousand calls, and the one thing a person cannot do after starting it is
look at round 2 before round 3 is assembled from it. Nothing is lost by stopping between
rounds: the chain is on disk, not in a process — `loop.run_iteration()` reads every earlier
round's `metrics.json`, refuses a gap, and refuses a round the pre-registered rule already
stopped (DESIGN §3, §5.5). The verdict is printed at the end of each round with the next
command to type, so the loop is driven by a person reading the stopping rule's own answer.

**Every check happens before the spend, and for a later round the spend is 1 + N.** DESIGN
§6.3: the window is binding from the moment the first `agent_calls.jsonl` line lands, so a
precondition discovered afterwards is a precondition discovered too late — and at round ≥ 2
"too late" means the Auditor has read the whole fold. `--dry-run` performs every check, prints
the plan, the window hashes and the call count, and makes no call.

**What it checks that `run_arm.py` cannot.** The round number against the ceiling; that round 1
has *not* called and that a later round *has* (`arm_has_called()` read both ways — the freeze is
once per arm, and a round 2 on an arm with no round 1 has no §§1.2–1.4); that every earlier
round left a score, through the same `loop._leak_rates()` the round itself will call, so the
plan cannot disagree with the run; and where the stopping rule stands on those scores, because
that is the fact that decides whether this round should be run at all.

**It duplicates no gate.** The logging gate and the axis refusals are `tools/run_arm.py`'s and
are imported from it by path, the way that tool imports `check_bedrock_logging.py` and the way
`tests/test_structure.py` loads its checker: `tools/` is not a package, and a second copy of
"a mistyped axis mints a cell" is the copy that will not learn what the first one learns.
`bedrock._require_logging_check()` inside `invoke()` is still the guarantee.

**No model id is spelled here**, in the code or in the examples, for `tools/run_arm.py`'s
reason (DESIGN §10 A2): an id written in an example is the id that gets pasted and recorded,
chosen by whoever last edited this docstring.

Usage:
    python3 tools/run_loop.py --corpus es-meddocan --lang es --iteration 1 \\
        --model-id MODEL_ID --dry-run

    python3 tools/run_loop.py --corpus es-meddocan --lang es --iteration 1 \\
        --model-id MODEL_ID

    python3 tools/run_loop.py --corpus es-meddocan --lang es --iteration 2 \\
        --model-id MODEL_ID --dry-run
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import orchestrate, split                                    # noqa: E402
from src.corpora.base import (                                        # noqa: E402
    CorpusError, corpus_root, path_template, termination_params,
)
from src.eval import sealed_log                                       # noqa: E402
from src.eval.run_fold import DEFAULT_SPLIT                           # noqa: E402
from src.llm.bedrock import BedrockError                              # noqa: E402
from src.porting import audit, loop                                   # noqa: E402
from src.rules import RuleError, arm_rules_path                       # noqa: E402
from src.sample import WINDOW_HASH_FIELDS, window_hashes              # noqa: E402
from src.termination import TerminationError, should_stop             # noqa: E402

#: The `paths` keys every round of this arm touches. Arm-scoped: one freeze record, one call
#: log and — because a format failure ends the arm — one failure record (DESIGN §5.5).
#: `metrics`/`spans` are the un-iterated pair, rewritten by every round so the arm's headline
#: is at the same path as every other arm's.
ARM_KEYS = ("armfreeze", "agentlog", "formatfailure", "metrics", "spans")

#: The round-scoped keys, in the order a reader of one round wants them: the score, the
#: predictions the next round masks, the error list the next round's §1.4 is drawn over.
ROUND_KEYS = ("itermetrics", "iterspans", "itererrors")

#: Round ≥ 2 only. The Auditor runs from round 2 on (`config/naming.yaml` agent_role), so
#: round 1's plan must not promise a file it will not write.
AUDIT_KEY = "auditreport"


def _run_arm_tool():
    """`tools/run_arm.py` as a module, for the two checks it owns.

    Imported by path because `tools/` is not a package — `run_arm.py` loads
    `check_bedrock_logging.py` the same way, and for the same reason: the module that owns a
    predicate is where it is asked. `_logging_state()` and `_check_axes()` are private to that
    file and used here anyway, deliberately: the alternative is a second copy of the gate
    loader and of the axis refusals, and the copy is the one that will not be updated when the
    axis list or the gate's advice changes.
    """
    tool = ROOT / "tools" / "run_arm.py"
    spec = importlib.util.spec_from_file_location("_run_arm", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fold_size(corpus: str, fold: str) -> int:
    """How many documents the Auditor will be called on, from `splits/{corpus}.json`.

    Read from the split file rather than by loading the fold: this number goes in the plan,
    the plan is printed before anything is called, and loading the corpus to count it would
    put the fold's text in this process to produce a figure the frozen split already states.
    """
    folds = split.read(corpus).get("folds")
    block = folds.get(fold) if isinstance(folds, dict) else None
    value = block.get("n_documents") if isinstance(block, dict) else None
    if not isinstance(value, int) or value <= 0:
        raise CorpusError(
            f"splits/{corpus}.json: folds.{fold}.n_documents is {value!r}, so the number of "
            "Auditor calls this round would make cannot be stated before it makes them."
        )
    return value


def _check_round(args) -> str | None:
    """The round number, checked against the ceiling. Returns a message or `None`.

    The ceiling is pre-registered (DESIGN §3) and `should_stop()` *raises* above it, so a
    round past the cap is a round whose own termination block the rule cannot evaluate. Asked
    here so the answer is a sentence rather than a traceback from inside the writer, and asked
    before the freeze either way.
    """
    if args.iteration < loop.ITERATION:
        return (f"--iteration {args.iteration} is not a round. Rounds are numbered from "
                f"{loop.ITERATION} (config/naming.yaml paths.itermetrics — `iter{{N}}/`).")
    ceiling = termination_params()["ceiling"]
    if args.iteration > ceiling:
        return (f"--iteration {args.iteration} is past the pre-registered ceiling of "
                f"{ceiling} (DESIGN §3). An arm that ran past the cap has violated the "
                "stopping rule, and `should_stop()` refuses to report a reason for it.")
    return None


def _history(args) -> tuple[list[float], object] | None:
    """Rounds 1..N−1's leak rates and the stopping rule's verdict on them. `None` at round 1.

    Read through `loop._leak_rates()` — the function the round itself calls — so the plan and
    the run cannot disagree about which rounds have scores. A missing one refuses there, which
    is also the format-failure and the no-gaps check: a round that failed validation wrote
    `format_failure.json` and deliberately no `metrics.json` (DESIGN §5.5).
    """
    if args.iteration == loop.ITERATION:
        return None
    rates, _metrics = loop._leak_rates(
        corpus=args.corpus, detector=args.detector, supervision=args.supervision,
        porting=args.porting, through=args.iteration - 1,
    )
    return rates, should_stop(args.corpus, rates)


def _plan(args, history, n_docs: int) -> list[str]:
    """The lines printed before anything is written. Read this, then run without --dry-run.

    Carries three things `run_arm.py`'s plan has no reason to: the **window hashes**, because
    from round 2 the Auditor template is in the window and a round is the first thing that can
    have been assembled under a moved one; the **call count**, because 1 + N is the round's
    cost and a person approving the round is approving that number; and the **history**, since
    whether this round should run at all is a question about the previous rounds' leak rates.

    The tree state is here for `run_arm.py`'s reason: `commit` and `tree` go into the run
    block, a dirty tree means the recorded hash does not describe the code that ran, and this
    is the last moment at which that is fixable.
    """
    from src.llm.bedrock import _resolution

    components = {"corpus": args.corpus, "detector": args.detector,
                  "supervision": args.supervision, "porting": args.porting,
                  "lang": args.lang, "iteration": args.iteration}
    commit, tree = sealed_log.tree_state()
    resolution = _resolution(args.model_id, args.model_id)
    ceiling = termination_params()["ceiling"]
    first = args.iteration == loop.ITERATION

    lines = [
        f"corpus       {args.corpus}",
        f"cell         {args.detector} / {args.supervision} / {args.porting}",
        f"round        {args.iteration} of at most {ceiling}   "
        + ("(no feedback: §§1.3-1.4 are stated empty, which is `port-oneshot`'s one call)"
           if first else f"(§§1.2-1.4 come from round {args.iteration - 1})"),
        f"split        {args.split}   (scored fold)",
        f"lang         {args.lang}",
        f"model_id     {args.model_id}",
        f"resolution   {resolution}   (what the run block will record if the response "
        "agrees)",
        f"commit       {commit or '(unknown)'}  tree {tree}",
        "",
        f"calls        1 {orchestrate.RULE_AUTHOR}"
        + ("   (round 1 calls no Auditor)" if first else
           f" + {n_docs} {loop.AUDITOR} (one per {args.split} document) = {1 + n_docs}"),
    ]

    # The window, field by field, from the window's own definition. Three files today
    # (`sample.WINDOW_FILES`); listing them here would be a second answer to what the window
    # is, and the field names are `WINDOW_HASH_FIELDS`' because that mapping is what the
    # freeze record and every call line are written from.
    hashes = window_hashes()
    lines.append("")
    for name, field in WINDOW_HASH_FIELDS.items():
        lines.append(f"{field:16} {hashes[field]}  ({name})")

    if history is not None:
        rates, verdict = history
        lines.append("")
        lines.append("history      " + "  ".join(
            f"round {i}: {rate:.4f}" for i, rate in enumerate(rates, start=1)))
        lines.append(f"improvements {[round(g, 6) for g in verdict.improvements]}")
        lines.append(f"delta        {verdict.delta:.6f}   k {verdict.k}   "
                     f"n_dev {verdict.n_dev}")
        lines.append(f"stands       {verdict.reason or 'not stopped'}   "
                     f"(the rule's verdict on rounds 1-{len(rates)})")
        drift = orchestrate.window_drift(args.corpus, args.detector, args.supervision,
                                         args.porting)
        lines.append("drift        " + (", ".join(drift) + "  — the frozen window moved; "
                                        "reported, not refused (DESIGN §6.3)"
                                        if drift else "none"))
        # Which attempt at this round the run would be (DESIGN §5.5.2). Shown because the
        # operator's decision differs above 1: re-running an *incomplete* round is allowed and is
        # why the draw mechanism exists, and re-running a *scored* one is §6's prohibition that
        # nothing in the driver refuses. So the plan says the number and says which check the
        # reader owes — this is the last moment before 250 calls are paid for.
        draw = audit.next_draw(corpus=args.corpus, detector=args.detector,
                               supervision=args.supervision, porting=args.porting,
                               iteration=args.iteration, root=ROOT)
        lines.append(f"draw         {draw}" + (
            "   (this round has not been audited before)" if draw == 1 else
            f"   — attempts 1-{draw - 1} left reports under draw*/ and their spend will be "
            "recorded as abandoned. Confirm this round wrote no metrics.json before "
            "proceeding (DESIGN §5.5.2, §6)"))

    keys = (*ARM_KEYS, *ROUND_KEYS) if history is None else \
        (*ARM_KEYS, *ROUND_KEYS, AUDIT_KEY)
    lines.append("")
    lines.append(f"{'armrules':14}->  "
                 f"{arm_rules_path(**components, root=ROOT).relative_to(ROOT)}")
    for key in keys:
        lines.append(f"{key:14}->  {path_template(key).format(**components)}")
    if history is not None:
        lines.append(f"{'auditdraw':14}->  " + path_template("auditdraw").format(
            **components, draw=draw))
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--corpus", required=True, help="corpus id from naming.yaml")
    ap.add_argument("--lang", required=True,
                    help="the rule-file language this round authors; must be one the "
                         "corpus loads (corpus_rule_langs, DESIGN §5.2)")
    ap.add_argument("--iteration", required=True, type=int,
                    help="which round to run. Required and with no default, for "
                         "`loop.run_iteration()`'s reason: a default round number is a "
                         "round chosen by whichever caller forgot")
    ap.add_argument("--model-id", required=True,
                    help="the Bedrock id to call. Required and with no default: the id is "
                         "a parameter end to end (DESIGN §10 A2), and a default here is "
                         "where the record would start describing this file")
    ap.add_argument("--detector", default=loop.DETECTOR,
                    help=f"detector axis value (default {loop.DETECTOR})")
    ap.add_argument("--supervision", default=loop.SUPERVISION,
                    help=f"supervision axis value (default {loop.SUPERVISION})")
    ap.add_argument("--porting", default=loop.PORTING,
                    help=f"porting axis value (default {loop.PORTING})")
    ap.add_argument("--split", default=DEFAULT_SPLIT,
                    help=f"fold to score on (default {DEFAULT_SPLIT}); test is refused")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="override the client's completion budget; left to src/llm/"
                         "bedrock.py's default otherwise, so the budget has one home")
    ap.add_argument("--dry-run", action="store_true",
                    help="run every check, print the plan, make no call and write "
                         "nothing. A later round makes 1 + N calls and the window cannot be "
                         "unfrozen (DESIGN §6.3), so this is the mode to run first")
    args = ap.parse_args(argv)

    tool = _run_arm_tool()
    try:
        problem = tool._check_axes(args) or _check_round(args)
    except CorpusError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    if problem:
        print(problem, file=sys.stderr)
        return 2

    try:
        corpus_root(args.corpus)
        n_docs = _fold_size(args.corpus, args.split)
    except CorpusError as exc:
        # Before the call, for `run_arm.py`'s reason and one more: at round ≥ 2 the Auditor
        # is called once per document, so a corpus that is not on this machine would be
        # discovered after the fold had been loaded — or not at all, if the count came from
        # somewhere else.
        print(f"{exc}", file=sys.stderr)
        return 2

    # `arm_has_called()` read both ways, which is one predicate and not two guards (see
    # `loop.run_iteration()`): round 1 freezes the window and must not run on an arm that has
    # called, and a later round has no §§1.2-1.4 on an arm that has not.
    where = orchestrate.called_where(args.corpus, args.detector, args.supervision,
                                     args.porting)
    cell = f"{args.corpus}/{args.detector}/{args.supervision}/{args.porting}"
    if args.iteration == loop.ITERATION and where is not None:
        print(f"{cell}: this arm has already made its first call (evidence: {where}), so "
              f"round {loop.ITERATION} is spent. `freeze_window()` will refuse it — the "
              "freeze is once per arm and the window is bound from the moment the call log "
              f"line lands (DESIGN §6.3, §5.5). Run --iteration {loop.FIRST_ITERATED} to "
              "continue the arm, or a second arm with a written reason.", file=sys.stderr)
        return 2
    if args.iteration >= loop.FIRST_ITERATED and where is None:
        print(f"{cell}: this arm has made no call, so there is no round "
              f"{args.iteration - 1} for round {args.iteration} to iterate from. Round "
              f"{args.iteration}'s §§1.2-1.4 are the previous round's rule file, score and "
              f"errors. Run --iteration {loop.ITERATION} first (DESIGN §5.5).",
              file=sys.stderr)
        return 2

    try:
        history = _history(args)
    except (CorpusError, orchestrate.OrchestrateError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    if history is not None and history[1].stop:
        verdict = history[1]
        # Obeying the pre-registered rule rather than re-deciding it, and saying so before
        # the money: `loop.run_iteration()` refuses this too, and that refusal is the
        # guarantee. §3's ceiling stop ends the arm exactly as convergence does.
        print(f"{cell}: the arm stopped at round {verdict.iterations} with reason "
              f"{verdict.reason!r} (delta={verdict.delta:.6f}, k={verdict.k}, "
              f"ceiling={verdict.ceiling}), so round {args.iteration} does not run. The "
              "stopping rule is pre-registered (DESIGN §3) and this driver obeys it.",
              file=sys.stderr)
        return 2

    try:
        plan = _plan(args, history, n_docs)
    except (CorpusError, RuleError, BedrockError, TerminationError,
            orchestrate.OrchestrateError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    for line in plan:
        print(line)
    print()

    ok, message = tool._logging_state()
    print(f"logging gate  {'ok' if ok else 'BLOCKED'}: {message}")

    if args.dry_run:
        # Reported and then exited on, in `run_arm.py`'s order and for its reason: a blocked
        # gate still shows the plan, and the exit code carries the readiness so that a script
        # cannot read 0 as permission.
        print()
        print("--dry-run: nothing was frozen, called or written."
              if ok else
              "--dry-run: nothing was frozen, called or written — and the gate above "
              "would refuse the call.")
        return 0 if ok else 2

    if not ok:
        # Refused here as well as in the client. The client's refusal is the guarantee; this
        # one puts the reason before the freeze rather than inside a traceback
        # (docs/notes/compliance.md §1, §3).
        return 2

    print()
    common = dict(corpus=args.corpus, lang=args.lang, model_id=args.model_id,
                  detector=args.detector, supervision=args.supervision,
                  porting=args.porting, split=args.split, max_tokens=args.max_tokens)
    try:
        out = (loop.run_iteration_1(**common) if args.iteration == loop.ITERATION
               else loop.run_iteration(args.iteration, **common))
    except (CorpusError, RuleError, BedrockError, TerminationError,
            orchestrate.OrchestrateError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    cost = out["cost"]
    print(f"round        {out['iteration']}")
    print(f"outcome      {out['outcome']}")
    print(f"cost         {cost['llm_calls']} calls, {cost['prompt_tokens']} prompt + "
          f"{cost['completion_tokens']} completion tokens, {cost['wall_seconds']}s wall")
    if out.get("cost_to_date") is not None:
        total = out["cost_to_date"]
        print(f"cost_to_date {total['llm_calls']} calls, {total['prompt_tokens']} prompt + "
              f"{total['completion_tokens']} completion tokens, "
              f"{total['wall_seconds']}s wall   (the arm)")
    print(f"rules        {Path(out['rules_path']).relative_to(ROOT)}")
    if out.get("audit_report_path"):
        print(f"audit        {Path(out['audit_report_path']).relative_to(ROOT)}")

    if out["outcome"] == orchestrate.FORMAT_FAILURE:
        # Exit 1 and not 0, for `run_arm.py`'s reason, and the arm is over: round N + 1's
        # §1.2 and §1.3 are this round's rule file and score, and neither exists (DESIGN
        # §5.5). `metrics.json` was deliberately not written.
        print(f"failure      {Path(out['failure_path']).relative_to(ROOT)}")
        print("no metrics.json: the response did not load, and zeros there would read as a "
              "rule set that ran and caught nothing (DESIGN §10 A2). A format failure ends "
              "the arm — the next round has no §1.3 to show (DESIGN §5.5).")
        return 1

    headline = out["scored"]["headline"]
    print(f"metrics      {Path(out['metrics_path']).relative_to(ROOT)}")
    print(f"leak rate    {headline['leak_rate']} (fully_covered, the headline) / "
          f"{headline['leak_rate_lower_bound']} (relaxed, the lower bound)")
    print("# Leak rate and the complementarity decomposition are the headline, not F1 "
          "(CLAUDE.md, DESIGN §9.3). Both are in metrics.json.")

    termination = out.get("termination")
    if termination is None:
        # Round 1 writes a `termination` block through `run_fold`'s default and this driver
        # does not read it back; what a caller needs to know is that the arm continues.
        print(f"next         --iteration {args.iteration + 1}")
        return 0
    print(f"termination  {termination['reason'] or 'null'}   "
          f"converged {termination['converged']}   "
          f"improvements {termination['improvements']}")
    if out["stop"]:
        print("the arm is over: this is its last round, and the reason above is what the "
              "record says about it (DESIGN §3). `paths.metrics` holds this round's score.")
    else:
        print(f"next         --iteration {args.iteration + 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
