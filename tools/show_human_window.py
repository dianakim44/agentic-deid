#!/usr/bin/env python3
"""Print the `port-human` window for one iteration, to a terminal and nowhere else.

This is the hand-off script: it renders what the rule author reads, including the
±120 characters of context DESIGN §11.1 fixes, and it is the one place in this
repository that puts corpus text on a screen. Everything about it is arranged so that
the text goes to stdout and stops there.

**Why a script rather than a paste into a conversation.** `render_for_author()`'s
output must not reach disk, a commit, an issue, or a transcript (`rule_author.md` §6,
CLAUDE.md's rule on logs and messages). A conversation is a transcript. So the author
runs this themselves and the window exists only in their terminal scrollback — which
is also the reason the script refuses to be redirected into a file.

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
from src.porting.human_arm import (                             # noqa: E402
    draw_iteration, initial_error_pool, render_for_author, summarise, window_drift,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--iteration", type=int, default=1)
    ap.add_argument("--detector", default="R")
    ap.add_argument("--supervision", default="sup-free")
    ap.add_argument("--counts-only", action="store_true",
                    help="the summary without the contexts; safe to paste anywhere")
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

    if args.iteration != 1:
        print(f"iteration {args.iteration}: the error pool must come from the scorer, "
              "not from initial_error_pool() — only iteration 1 can be derived from "
              "the loader alone (an empty rule file detects nothing).", file=sys.stderr)
        return 2

    drift = window_drift(args.corpus, args.detector, args.supervision)
    if drift:
        print(f"WARNING: the frozen window has moved: {drift}. Read §7 of "
              "docs/prompts/rule_author.md before continuing — a mid-run change makes "
              "the iterations before it and after it two experiments.", file=sys.stderr)

    pool = initial_error_pool(args.corpus)
    sample, prov = draw_iteration(pool, args.corpus, args.iteration)
    summary = summarise(sample, pool)

    print(f"# {args.corpus} / {args.detector} / {args.supervision} / port-human "
          f"— iteration {args.iteration}")
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
    print(render_for_author(sample, docs, prov["context_chars"]))
    print("# End of window. Not written to disk, and not to be pasted into a commit,")
    print("# an issue, or a conversation with a model (rule_author.md §6, §8).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
