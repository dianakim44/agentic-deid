#!/usr/bin/env python3
"""Run one agent porting arm — `src.orchestrate.run_arm()` behind named flags.

**Why this exists rather than a `python -c` one-liner.** `run_arm()` is keyword-only and
takes four axis values, a language and a model id. Invoked through `python3 -c`, every one
of those is a bare string in a shell line, and a single typo does not fail: `--detector RR`
mints a cell. `paths.armfreeze` and `paths.armrules` template all four axes, so the arm
would freeze a window, make its one call and write a complete `metrics.json` under
`results/es-meddocan/RR/…` — internally consistent, sitting beside the real arm, and read
by anything walking those directories as a second detector (CLAUDE.md's naming rule;
`orchestrate._arm_path` refuses values that are not axis values, which is what catches this
one, and that refusal is only useful if it happens before the call). So the axes come from
flags with defaults that are the baseline cell, and everything checkable is checked before
the freeze.

**Everything this checks, it checks before the freeze — and the freeze is the point of no
return.** DESIGN §6.3: the window is binding from the moment the `agent_calls.jsonl` line
lands, so this arm cannot be re-run to fix a mistake, only re-run as a *second* arm with a
note explaining why. A precondition discovered after the call has been made and paid for is
therefore a precondition discovered too late. `--dry-run` performs every check, prints the
plan and the paths, and makes no call.

**It duplicates no gate.** The logging gate, the axis validation, the already-called
refusal and the `test`-fold refusal all live in the modules that own them; this asks the
same predicates early so the message arrives before the money and the window are spent.
`bedrock._require_logging_check()` still runs inside `invoke()` and is the guarantee — the
check here is a courtesy, and if the two ever disagree the one inside the client wins.

**No model id is spelled here.** `--model-id` is required, for the reason `run_arm()` makes
it a required keyword (DESIGN §10 A2): a recorded id that came from a default records what
the code says rather than what was called. The plan block prints which
`model_id_resolution` the run will record, by asking `bedrock` rather than by re-deriving
datedness — a second copy of that rule is a second answer to what `dated` means.

**The usage examples name no id either, and that is the same rule one step further out.**
An example is what gets pasted, so an id written here would be the id that ran, chosen by
whoever last edited this docstring. It would also go stale in the direction that does not
announce itself: `rules/es.yaml` carried a "delete this before iteration 1" instruction for
three days after DESIGN §5.3 made it false, and the reason it was a problem is that a stale
instruction left in place is one the next person follows. So the placeholder stays a
placeholder, and which id an arm ran on is a decision recorded in that arm's `metrics.json`
rather than in this file's prose.

Usage:
    python3 tools/run_arm.py --corpus es-meddocan --lang es \\
        --model-id MODEL_ID --dry-run

    python3 tools/run_arm.py --corpus es-meddocan --lang es --model-id MODEL_ID
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import orchestrate                                          # noqa: E402
from src.corpora.base import (                                       # noqa: E402
    CorpusError, axis, corpus_root, path_template, rule_langs,
)
from src.eval import sealed_log                                      # noqa: E402
from src.eval.run_fold import DEFAULT_SPLIT                          # noqa: E402
from src.llm.bedrock import BedrockError                             # noqa: E402
from src.rules import RuleError, arm_rules_path                      # noqa: E402

#: The `paths` keys this arm touches, printed in the plan so that the cell the run will
#: land in is readable *before* the call. Keys and not templates: the templates are in
#: config/naming.yaml and filling one for display here must not become a second copy of
#: it (CLAUDE.md, DESIGN §11.2).
PLAN_KEYS = ("armfreeze", "agentlog", "metrics", "spans", "formatfailure")


def _logging_state() -> tuple[bool, str]:
    """`(ok, message)` for today's Bedrock logging gate, asked of the tool that owns it.

    Imported by path because `tools/` is not a package, the same way `test_structure.py`
    loads its checker. The answer is not cached and not second-guessed: `checked_today()`
    is the predicate `bedrock._require_logging_check()` calls, so asking it here cannot
    disagree with the gate unless the record changes between the two calls.
    """
    import importlib.util

    tool = ROOT / "tools" / "check_bedrock_logging.py"
    if not tool.exists():
        return False, f"{tool.name} is missing — the gate itself will refuse the call."
    spec = importlib.util.spec_from_file_location("_check_bedrock_logging", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.checked_today():
        return True, f"recorded for {module.today()}"
    dates = module.recorded_dates()
    return False, (
        f"no check recorded for {module.today()} (on record: "
        f"{', '.join(dates) if dates else 'none'}). It is a mutable account setting, so "
        "yesterday's check is evidence about yesterday.\n"
        "  run: python3 tools/check_bedrock_logging.py"
    )


def _check_axes(args) -> str | None:
    """The axis values, checked here so a typo fails before the window is frozen.

    Returns a message or `None`. `orchestrate._arm_path()` performs the same validation
    when it fills a template, and that is the guarantee; this is the same check moved
    earlier, because the first template `run_arm()` fills is the freeze record's.
    """
    for name, value in (("corpus", args.corpus), ("detector", args.detector),
                        ("supervision", args.supervision), ("porting", args.porting),
                        ("split", args.split)):
        allowed = axis(name)
        if value not in allowed:
            return (f"--{name} {value!r} is not a value of the {name} axis in "
                    f"config/naming.yaml (have: {sorted(allowed)}). A value that is not "
                    "an axis value would mint a cell of the experiment rather than name "
                    "one.")
    if args.split == "test":
        return ("--split test is refused. The test fold is sealed; sealed evaluation runs "
                "through `python3 -m src.eval.run_sealed_eval`, which appends the access "
                "to results/sealed_eval_log.md before anything is read (CLAUDE.md, "
                "DESIGN §6.1). An agent arm never reads it.")
    langs = rule_langs(args.corpus)
    if args.lang not in langs:
        return (f"--lang {args.lang!r}: {args.corpus} loads {langs} "
                "(config/naming.yaml corpus_rule_langs). One call authors one file, and a "
                "file no corpus loads would be scored by nothing (DESIGN §5.2).")
    return None


def _plan(args) -> list[str]:
    """The lines printed before anything is written. Read this, then run without --dry-run.

    Deliberately includes the tree state. `commit` and `tree` go into the run block
    (`orchestrate._run_block`), a dirty tree means the commit hash does not describe the
    code that ran, and this is the last moment at which that is fixable — after the call
    it is a recorded fact about an arm that cannot be re-run.
    """
    from src.llm.bedrock import _resolution

    components = {"corpus": args.corpus, "detector": args.detector,
                  "supervision": args.supervision, "porting": args.porting,
                  "lang": args.lang, "iteration": orchestrate.ITERATION}
    commit, tree = sealed_log.tree_state()
    # `_resolution(id, id)` is the real predicate with the response agreeing exactly, which
    # is the accepted form that reaches the datedness line. Asking the module beats copying
    # "an eight-digit component" into this file, where it would be a second definition of
    # `dated` that nothing keeps in step (see the comment at bedrock.py's decision).
    resolution = _resolution(args.model_id, args.model_id)

    lines = [
        f"corpus       {args.corpus}",
        f"cell         {args.detector} / {args.supervision} / {args.porting}",
        f"split        {args.split}   (scored fold)",
        f"lang         {args.lang}    (iteration {orchestrate.ITERATION})",
        f"model_id     {args.model_id}",
        f"resolution   {resolution}   (what the run block will record if the response "
        "agrees)",
        f"commit       {commit or '(unknown)'}  tree {tree}",
        "",
        f"{'armrules':14}->  "
        f"{arm_rules_path(**components, root=ROOT).relative_to(ROOT)}",
    ]
    for key in PLAN_KEYS:
        template = path_template(key)
        lines.append(f"{key:14}->  {template.format(**components)}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--corpus", required=True, help="corpus id from naming.yaml")
    ap.add_argument("--lang", required=True,
                    help="the rule-file language this call authors; must be one the "
                         "corpus loads (corpus_rule_langs, DESIGN §5.2)")
    ap.add_argument("--model-id", required=True,
                    help="the Bedrock id to call. Required and with no default: the id is "
                         "a parameter end to end (DESIGN §10 A2), and a default here is "
                         "where the record would start describing this file")
    ap.add_argument("--detector", default=orchestrate.DETECTOR,
                    help=f"detector axis value (default {orchestrate.DETECTOR})")
    ap.add_argument("--supervision", default=orchestrate.SUPERVISION,
                    help=f"supervision axis value (default {orchestrate.SUPERVISION})")
    ap.add_argument("--porting", default=orchestrate.PORTING,
                    help=f"porting axis value (default {orchestrate.PORTING})")
    ap.add_argument("--split", default=DEFAULT_SPLIT,
                    help=f"fold to score on (default {DEFAULT_SPLIT}); test is refused")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="override the client's completion budget; left to src/llm/"
                         "bedrock.py's default otherwise, so the budget has one home")
    ap.add_argument("--dry-run", action="store_true",
                    help="run every check, print the plan, make no call and write "
                         "nothing. The call freezes the window and cannot be taken back "
                         "(DESIGN §6.3), so this is the mode to run first")
    args = ap.parse_args(argv)

    try:
        problem = _check_axes(args)
    except CorpusError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    if problem:
        print(problem, file=sys.stderr)
        return 2

    try:
        corpus_root(args.corpus)
    except CorpusError as exc:
        # Before the call rather than after. `run_fold` needs the corpus to score, and
        # discovering it is not on this machine after one paid, unrepeatable call would
        # leave a format-failure-shaped hole that was never about the response.
        print(f"{exc}", file=sys.stderr)
        return 2

    where = orchestrate.called_where(args.corpus, args.detector, args.supervision,
                                     args.porting)
    if where is not None:
        print(f"{args.corpus}/{args.detector}/{args.supervision}/{args.porting}: this arm "
              f"has already made its call (evidence: {where}). One call is the whole of "
              "this arm; freeze_window() will refuse, and re-running means running a "
              "second arm with a written reason (DESIGN §6.3, §11.1).", file=sys.stderr)
        return 2

    try:
        plan = _plan(args)
    except (CorpusError, RuleError, BedrockError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    for line in plan:
        print(line)
    print()

    ok, message = _logging_state()
    print(f"logging gate  {'ok' if ok else 'BLOCKED'}: {message}")

    if args.dry_run:
        # Reported and then exited on, in that order, so that a blocked gate still shows
        # the plan above it: "not ready, and here is the cell it would have run in" is the
        # answer a dry run is asked for. The exit code carries the readiness, because a
        # dry run that returned 0 while the gate was shut would be read by a script as
        # permission to run.
        print()
        print("--dry-run: nothing was frozen, called or written."
              if ok else
              "--dry-run: nothing was frozen, called or written — and the gate above "
              "would refuse the call.")
        return 0 if ok else 2

    if not ok:
        # Refused here as well as in the client. The client's refusal is the guarantee;
        # this one exists so that the reason arrives before the freeze rather than from
        # inside a traceback (docs/notes/compliance.md §1, §3).
        return 2

    print()
    try:
        out = orchestrate.run_arm(
            corpus=args.corpus, lang=args.lang, model_id=args.model_id,
            detector=args.detector, supervision=args.supervision, porting=args.porting,
            split=args.split, max_tokens=args.max_tokens,
        )
    except (CorpusError, RuleError, BedrockError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    cost = out["cost"]
    print(f"outcome      {out['outcome']}")
    print(f"cost         {cost['llm_calls']} call, {cost['prompt_tokens']} prompt + "
          f"{cost['completion_tokens']} completion tokens, "
          f"{cost['wall_seconds']}s wall")
    print(f"rules        {Path(out['rules_path']).relative_to(ROOT)}")

    if out["outcome"] == orchestrate.FORMAT_FAILURE:
        # Exit 1 and not 0: the arm ran and this is a result the appendix reports (DESIGN
        # §10 A2, zero format retries), but a caller scripting this must not read it as a
        # scored run — metrics.json was deliberately not written.
        print(f"failure      {Path(out['failure_path']).relative_to(ROOT)}")
        print("no metrics.json: the response did not load, and zeros there would read as "
              "a rule set that ran and caught nothing (DESIGN §10 A2).")
        return 1

    headline = out["scored"]["headline"]
    print(f"metrics      {Path(out['metrics_path']).relative_to(ROOT)}")
    print(f"leak rate    {headline['leak_rate']} (fully_covered, the headline) / "
          f"{headline['leak_rate_lower_bound']} (relaxed, the lower bound)")
    print("# Leak rate and the complementarity decomposition are the headline, not F1 "
          "(CLAUDE.md, DESIGN §9.3). Both are in metrics.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
