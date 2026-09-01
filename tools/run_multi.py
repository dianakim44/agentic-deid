#!/usr/bin/env python3
"""Run `port-multi`'s three authoring calls and freeze what they wrote — `src.porting.multi`.

**This is the whole of what the rung adds, and it all happens before round 1.** The Profiler,
the Mapper and the LexiconBuilder are called once each, in that order, and then the three
artefacts are hashed once. Rounds 1..N are `tools/run_loop.py --porting port-multi`, which is
`port-loop`'s loop with two arguments filled in (DESIGN §6.7.1).

**One process for all three by default, and `--step` for the resume.** The order is a data
dependency — the Mapper's §1.1 input is the Profiler's type inventory — so running the three
together is the shape that needs no intermediate file to be read back. But a stop in the middle
is a real state: the Profiler's artefact is written and its call is spent, and re-running the
sequence from the top would refuse at `write_profile` rather than continue. So each step can be
run alone, and `--step mapping` reads `profile.json` for the object the previous step passed in
memory (`multi.read_profile()`).

**Each step is a separate spend and the driver stops at the first stop.** A refused profile or
mapping ends the arm — nothing further is called, because the next call's input is the artefact
that just failed. A refused lexicon *entry* does not: the collection is written with what
survived and `--step freeze` proceeds. The asymmetry is `src/porting/artefacts.py`'s and the
reason is there: a thin gazetteer's cost lands in the leak rate this experiment reports, and a
wrong offset convention's cost lands nowhere.

**`--dry-run` performs every check, prints the plan, and calls nothing.** Three calls is a small
spend next to a round's 1 + N, but the freeze is once per arm and `write_profile` refuses a
second write, so a mistyped axis here mints a cell that cannot be re-authored — which is the
same reason `tools/run_arm.py` has the flag.

**It duplicates no gate.** The logging gate and the axis refusals are `tools/run_arm.py`'s and
are imported from it by path, exactly as `tools/run_loop.py` does it and for the reason that
file gives: a second copy of "a mistyped axis mints a cell" is the copy that will not learn
what the first one learns.

**No model id is spelled here**, in the code or in the examples (DESIGN §10 A2): an id written
in an example is the id that gets pasted and recorded, chosen by whoever last edited this file.

Usage:
    python3 tools/run_multi.py --corpus es-meddocan --model-id MODEL_ID --dry-run

    python3 tools/run_multi.py --corpus es-meddocan --model-id MODEL_ID

    python3 tools/run_multi.py --corpus es-meddocan --model-id MODEL_ID --step mapping
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import orchestrate                                           # noqa: E402
from src.corpora.base import (                                        # noqa: E402
    CorpusError, corpus_root, path_template, rule_langs,
)
from src.eval import sealed_log                                       # noqa: E402
from src.eval.run_fold import DEFAULT_SPLIT                           # noqa: E402
from src.llm.bedrock import BedrockError                              # noqa: E402
from src.porting import multi                                         # noqa: E402
from src.porting.artefacts import ArtefactError                       # noqa: E402
from src.sample import WINDOW_HASH_FIELDS, window_hashes              # noqa: E402

#: The steps, in the order the sequence runs them. `all` is the default and is every one of
#: them; the individual names are the resume. Named here rather than as literals in `main()`
#: because `--step` and the dispatch below have to be the same list.
STEPS = ("profile", "mapping", "lexicons", "freeze")
ALL = "all"

#: The `paths` keys this driver writes, for the plan. Arm-scoped every one of them: the three
#: artefacts carry no `{iteration}` because the rung is defined by their being inputs the loop
#: does not produce (`config/naming.yaml` armprofile), and the freeze record is one per arm for
#: the same reason.
WRITTEN_KEYS = ("armprofile", "armmapping", "armlexicon", "armlexiconmanifest",
                "armartefactfreeze", "armfreeze", "agentlog")


def _run_arm_tool():
    """`tools/run_arm.py` as a module, for its logging gate and axis refusals.

    Loaded by path for `tools/run_loop.py`'s reason: `tools/` is not a package, and a second
    copy of the gate is the copy that will not be updated when the first one is.
    """
    spec = importlib.util.spec_from_file_location("_run_arm", ROOT / "tools" / "run_arm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plan(args) -> list[str]:
    """The lines printed before anything is called. Read this, then run without --dry-run.

    Carries the **window hashes** for `tools/run_loop.py`'s reason and one that is sharper here:
    three of the six window files are these agents' own prompts, and this is the first moment in
    the arm at which any of them can have moved. It also carries what has already been authored,
    because on a resume that is the fact that decides which steps are left.
    """
    from src.llm.bedrock import _resolution

    components = {"corpus": args.corpus, "detector": args.detector,
                  "supervision": args.supervision, "porting": args.porting,
                  "lang": rule_langs(args.corpus)[0]}
    commit, tree = sealed_log.tree_state()
    steps = STEPS if args.step == ALL else (args.step,)
    counts = orchestrate.roles_called(args.corpus, args.detector, args.supervision,
                                      args.porting)
    calls = sum(1 for step in steps if step != "freeze")

    lines = [
        f"corpus       {args.corpus}",
        f"cell         {args.detector} / {args.supervision} / {args.porting}",
        f"langs        {list(rule_langs(args.corpus))}   "
        "(one LexiconBuilder call writes every one of them)",
        f"split        {args.split}   (recorded on a format failure; these calls score nothing)",
        f"model_id     {args.model_id}",
        f"resolution   {_resolution(args.model_id, args.model_id)}",
        f"commit       {commit or '(unknown)'}  tree {tree}",
        "",
        f"steps        {', '.join(steps)}",
        f"calls        {calls}   (one per authoring step; `freeze` calls nothing)",
        "authoring    " + "  ".join(f"{role}: {counts.get(role, 0)}"
                                    for role in multi.ROLE_ORDER)
        + "   (already spent on this arm; each must end at 1)",
        f"inventory    {path_template('inventory').format(corpus=args.corpus)}   "
        "(the Profiler's input — measured, not authored)",
    ]

    hashes = window_hashes()
    lines.append("")
    for name, field in WINDOW_HASH_FIELDS.items():
        lines.append(f"{field:16} {hashes[field]}  ({name})")

    lines.append("")
    for key in WRITTEN_KEYS:
        lines.append(f"{key:20}->  {path_template(key).format(**components)}")
    return lines


def _report(step: str, out: dict) -> None:
    """One authoring step's outcome, as lines. Counts and paths; no artefact content."""
    cost = out["cost"]
    print(f"{step:12} {out['outcome']}   {cost['llm_calls']} call, "
          f"{cost['prompt_tokens']} prompt + {cost['completion_tokens']} completion tokens, "
          f"{cost['wall_seconds']}s wall")
    if out.get("path"):
        print(f"{'':12} wrote {Path(out['path']).relative_to(ROOT)}")
    if out.get("counts"):
        print(f"{'':12} counts {out['counts']}")
    if out.get("failure_path"):
        print(f"{'':12} FAILURE {Path(out['failure_path']).relative_to(ROOT)}")
    for key in ("applied", "disagreements"):
        if key in out:
            value = out[key]
            print(f"{'':12} {key} "
                  + (f"{len(value)}" if isinstance(value, list) else f"{value}"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--corpus", required=True, help="corpus id from naming.yaml")
    ap.add_argument("--model-id", required=True,
                    help="the model to call, recorded in every artefact's failure record and "
                         "in every call line (DESIGN §10 A2)")
    ap.add_argument("--step", default=ALL, choices=(ALL, *STEPS),
                    help=f"which step to run (default {ALL}: {', '.join(STEPS)}). A single "
                         "step is the resume path after a stop — the earlier artefacts are on "
                         "disk and their calls are spent")
    ap.add_argument("--detector", default=multi.DETECTOR,
                    help=f"detector axis value (default {multi.DETECTOR})")
    ap.add_argument("--supervision", default=multi.SUPERVISION,
                    help=f"supervision axis value (default {multi.SUPERVISION})")
    ap.add_argument("--porting", default=multi.PORTING,
                    help=f"porting axis value (default {multi.PORTING})")
    ap.add_argument("--split", default=DEFAULT_SPLIT,
                    help=f"fold recorded on a format failure (default {DEFAULT_SPLIT}). These "
                         "three calls read no fold — the Profiler reads a mechanical inventory "
                         "and the other two read the previous artefact")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="per-call cap; left to src/llm/bedrock.py's default when omitted")
    ap.add_argument("--dry-run", action="store_true",
                    help="run every check, print the plan, make no call and write nothing. "
                         "The freeze is once per arm and write_profile refuses a second write, "
                         "so this is the mode to run first")
    args = ap.parse_args(argv)

    tool = _run_arm_tool()
    try:
        problem = tool._check_axes(args)
    except CorpusError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    if problem:
        print(problem, file=sys.stderr)
        return 2

    try:
        # Before the call, for `run_arm.py`'s reason: a corpus that is not on this machine
        # would otherwise be discovered after the Profiler had been paid for.
        corpus_root(args.corpus)
        plan = _plan(args)
    except (ArtefactError, BedrockError, CorpusError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    for line in plan:
        print(line)
    print()

    ok, message = tool._logging_state()
    print(f"logging gate  {'ok' if ok else 'BLOCKED'}: {message}")

    if args.dry_run:
        print()
        print("--dry-run: nothing was called, written or frozen."
              if ok else
              "--dry-run: nothing was called, written or frozen — and the gate above would "
              "refuse the calls.")
        return 0 if ok else 2
    if not ok:
        # Refused here as well as in the client, for `tools/run_loop.py`'s reason: the client's
        # refusal is the guarantee and this one puts the reason before the first call rather
        # than inside a traceback (docs/notes/compliance.md §1, §3).
        return 2

    print()
    axes = dict(corpus=args.corpus, detector=args.detector, supervision=args.supervision,
                porting=args.porting)
    common = dict(**axes, model_id=args.model_id, split=args.split,
                  max_tokens=args.max_tokens)
    steps = STEPS if args.step == ALL else (args.step,)
    profile = None
    try:
        for step in steps:
            if step == "profile":
                out = multi.author_profile(**common)
                profile = out.get("profile")
            elif step == "mapping":
                # Read from disk when this invocation did not author it — the resume path.
                out = multi.author_mapping(
                    profile=profile if profile is not None
                    else multi.read_profile(**axes, root=ROOT),
                    **common)
            elif step == "lexicons":
                out = multi.author_lexicons(**common)
            else:
                record = multi.freeze_artefacts(**axes, root=ROOT)
                print(f"{'freeze':12} {len(record['files'])} artefacts, "
                      f"{record['counts']['lexicon_files']} term lists")
                print(f"{'':12} wrote "
                      f"{multi.freeze_path(*axes.values(), root=ROOT).relative_to(ROOT)}")
                continue
            _report(step, out)
            if out["outcome"] == orchestrate.FORMAT_FAILURE:
                # The next step's input is the artefact that just failed, so nothing further
                # is called. The arm ends here and the failure record is the result
                # (DESIGN §10 A2) — a reportable outcome, not an accident.
                print()
                print(f"{args.corpus}/{args.detector}/{args.supervision}/{args.porting}: the "
                      f"{step} step is a format failure, so the arm ends here and no further "
                      "call is made. The failure record above holds the model ids, the "
                      "response and the validator's own message.")
                return 1
    except (ArtefactError, BedrockError, CorpusError,
            orchestrate.OrchestrateError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    print()
    if args.step == ALL:
        print("the three artefacts are authored and frozen. Round 1 is:")
        print(f"  python3 tools/run_loop.py --corpus {args.corpus} "
              f"--lang {rule_langs(args.corpus)[0]} --iteration 1 "
              f"--model-id {args.model_id} --porting {args.porting} --dry-run")
    else:
        remaining = [s for s in STEPS if s not in steps]
        print(f"step {args.step} is done. Remaining: {', '.join(remaining) or 'none'}.")
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
