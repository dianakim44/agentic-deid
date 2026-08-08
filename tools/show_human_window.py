#!/usr/bin/env python3
"""Print the `port-human` window for one iteration, to a terminal and nowhere else.

This is the hand-off script: it renders what the rule author reads, including the
±120 characters of context DESIGN §11.1 fixes, and it is the one place in this
repository that puts corpus text on a screen. Everything about it is arranged so that
the text goes to stdout and stops there.

**Why a script rather than a paste into a conversation.** `render_window()`'s output must
not reach disk, a commit, an issue, or a transcript (`rule_author.md` §6, CLAUDE.md's rule
on logs and messages). A conversation is a transcript. So the author runs this themselves
and the window exists only in their terminal scrollback — which is also the reason the
script refuses to be redirected into a file.

**The window is a `FilledPrompt` and reaches the screen through `to_terminal()`.** This
script does not hold the rendered string and does not `print()` it: the same renderer
serves the agent arms (`src/llm/prompt.py`), and the type is what carries the non-recording
convention across both. The `isatty` check below is kept even though `to_terminal()`
performs its own, because this one runs before the corpus is loaded and can therefore say
what to do instead; the one inside the type is the guarantee.

**It writes no log line.** The `read_sample` event was recorded when the window was
frozen; a script that logged on every invocation would turn "how many times did the
author look" into a count of terminal commands, and `human_minutes` is the author's to
report (DESIGN §11.2). Nothing here knows how long anybody read for.

Usage:
    python tools/show_human_window.py --corpus es-meddocan --iteration 1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora import load                                    # noqa: E402
from src.llm.prompt import render_window                        # noqa: E402
from src.porting.human_arm import (                             # noqa: E402
    draw_iteration, initial_error_pool, practice_pool, summarise, window_drift,
)
from src.sample import is_practice, practice_min                # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--iteration", type=int, default=1)
    ap.add_argument("--detector", default="R")
    ap.add_argument("--supervision", default="sup-free")
    ap.add_argument("--counts-only", action="store_true",
                    help="the summary without the contexts; safe to paste anywhere")
    ap.add_argument("--practice", action="store_true",
                    help="a rehearsal, drawing from the reserved 900+ band; refused "
                         "for any lower iteration and required for any 900+ one")
    args = ap.parse_args()

    # Refuse a pipe or a redirect. The window is for a person reading a screen, and a
    # `> window.txt` is exactly the file rule_author.md §6 says must not exist — an
    # accident that leaves the ±120 characters of a DUA corpus on disk, where
    # release_screen.py would then have to catch them.
    if not args.counts_only and not sys.stdout.isatty():
        print("refusing: stdout is not a terminal. The rendered window may not be "
              "redirected to a file or a pipe (docs/prompts/rule_author.md §6). Use "
              "--counts-only for output that is safe to capture.", file=sys.stderr)
        return 2

    # A rehearsal draws from the reserved band and from a pool with iteration 1's spans
    # removed; a real run is iteration 1 and nothing else, because any later pool must
    # come from the scorer. Both cases end up at initial_error_pool(), which is why the
    # two checks live together: the pool is derivable from the loader alone exactly when
    # the rule file is empty, and a rehearsal keeps it empty by writing elsewhere.
    if args.practice:
        if not is_practice(args.iteration):
            print(f"--practice with iteration {args.iteration}: rehearsals use the "
                  f"reserved band (>= {practice_min()}, config/sampling.yaml). A "
                  "rehearsal drawing a real number consumes that iteration — the draw "
                  "is seeded, so the window it printed is the one the real run would "
                  "have shown, and nothing downstream records that it was read early.",
                  file=sys.stderr)
            return 2
    elif args.iteration != 1:
        print(f"iteration {args.iteration}: the error pool must come from the scorer, "
              "not from initial_error_pool() — only iteration 1 can be derived from "
              "the loader alone (an empty rule file detects nothing). For a rehearsal, "
              f"use --practice with an iteration >= {practice_min()}.", file=sys.stderr)
        return 2

    drift = window_drift(args.corpus, args.detector, args.supervision)
    if drift:
        print(f"WARNING: the frozen window has moved: {drift}. Read §7 of "
              "docs/prompts/rule_author.md before continuing — a mid-run change makes "
              "the iterations before it and after it two experiments.", file=sys.stderr)

    pool = initial_error_pool(args.corpus)
    if args.practice:
        pool = practice_pool(pool, args.corpus)
    sample, prov = draw_iteration(pool, args.corpus, args.iteration,
                                  practice=args.practice)
    summary = summarise(sample, pool)

    kind = "PRACTICE — iteration 1's spans excluded" if args.practice else "iteration"
    print(f"# {args.corpus} / {args.detector} / {args.supervision} / port-human "
          f"— {kind} {args.iteration}")
    print(f"# seed {prov['seed']}  ({prov['seed_scheme']}, base {prov['base_seed']})")
    print(f"# pool {summary['pool_size']}  drawn {summary['sample_size']}  "
          f"documents touched {summary['documents_touched']}")
    print()
    print(f"{'type':16} {'drawn':>5} {'in pool':>8}")
    for phi_type, counts in summary["by_type"].items():
        print(f"{phi_type:16} {counts['drawn']:>5} {counts['in_pool']:>8}")
    print()

    if args.counts_only:
        return 0

    docs = {d.doc_id: d for d in load(args.corpus) if d.split == "dev"}
    # Straight from the renderer to the terminal, with no local name holding the text: a
    # variable here is a value a later edit can print, log, or write, and the type exists
    # so that the only way out is the one named for where it goes.
    render_window(sample, docs, prov["context_chars"]).to_terminal(sys.stdout)
    print()
    print("# End of window. Not written to disk, and not to be pasted into a commit,")
    print("# an issue, or a conversation with a model (rule_author.md §6, §8).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
