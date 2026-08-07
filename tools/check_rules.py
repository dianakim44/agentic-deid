#!/usr/bin/env python3
"""Run a rule file against the dev fold and print what it caught and what it cost.

This is the feedback path: write one rule, run this, see how many of the window's spans
it now covers and how many false positives it bought. Without it an author writes rules
into a file and learns nothing until a scoring run, which is not a loop.

**What it prints, and why exactly this.** Two numbers per rule and two overall:

    caught      how many of the drawn window's spans this rule covers
    false pos   how many of its matches hit no in-scope gold span at all

Both restricted to the dev fold, always (DESIGN §11.1, CLAUDE.md's sealing rule) — the
fold is not selectable and there is no flag for it, because a `--split` argument on a
tool an author runs forty times is a sealing violation waiting for a tired evening.

**`caught` is against the window, not against all of dev.** The window is what the
author read; a rule's effect on spans they never saw is real but is not feedback, and
reporting one number for both would let a rule that generalises broadly hide one that
does not generalise at all. The dev-wide recall figure is printed separately, below, and
labelled as such.

**Offsets, types, counts. No surface forms.** Not in the table, not in `--verbose`, not
in an error. A false positive is reported as a document id and a character range, which
is what CLAUDE.md permits and what an author needs to go and look at the note
themselves. The one exception in this file's *inputs* is the rule file's own patterns and
terms, which are already committed and public.

Usage:
    python tools/check_rules.py --corpus es-meddocan
    python tools/check_rules.py --corpus es-meddocan --rules /tmp/practice.yaml
    python tools/check_rules.py --corpus es-meddocan --rule-id es:doctor_prefix
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora import load                                         # noqa: E402
from src.corpora.base import CorpusError, rule_langs                 # noqa: E402
from src.porting.human_arm import (                                  # noqa: E402
    draw_iteration, initial_error_pool, practice_pool,
)
from src.rules import RuleError, load_for_corpus, load_rules         # noqa: E402
from src.sample import is_practice, practice_min                     # noqa: E402

#: A match counts as covering a gold span when it covers every byte of it — the
#: `fully_covered` definition (DESIGN §9.3), which is the headline definition for leak
#: rate. Using `relaxed` here would make a rule that clips a surname's last character
#: look like a hit, and the author would stop working on the boundary. The relaxed count
#: is printed alongside as the lower bound, exactly as the metrics do.
FULL, RELAXED = "fully_covered", "relaxed"


def _covers(pred, gold, mode: str) -> bool:
    if mode == FULL:
        return pred[0] <= gold.start and pred[1] >= gold.end
    return pred[0] < gold.end and pred[1] > gold.start


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--rules", type=Path, default=None,
                    help="a single rule file to run instead of rules/{lang}.yaml for "
                         "the corpus's langs — this is how a practice file at "
                         "/tmp/practice.yaml is tested without touching rules/")
    ap.add_argument("--lang", default=None,
                    help="the language --rules declares; required with --rules when the "
                         "corpus loads more than one")
    ap.add_argument("--rule-id", default=None,
                    help="report only this rule (prefixed, e.g. es:doctor_prefix)")
    ap.add_argument("--iteration", type=int, default=1)
    ap.add_argument("--practice", action="store_true",
                    help="score against a practice window, with iteration 1's spans "
                         "excluded from the pool")
    ap.add_argument("--verbose", action="store_true",
                    help="list each false positive as doc_id and offsets — no text")
    args = ap.parse_args()

    if args.practice and not is_practice(args.iteration):
        print(f"--practice with iteration {args.iteration}: rehearsals use the reserved "
              f"band (>= {practice_min()}).", file=sys.stderr)
        return 2

    try:
        if args.rules:
            langs = rule_langs(args.corpus)
            lang = args.lang or (langs[0] if len(langs) == 1 else None)
            if lang is None:
                print(f"--rules needs --lang: {args.corpus} loads {langs}, and the "
                      "rule_id prefix comes from the file's language, not the corpus's "
                      "(DESIGN §5.2).", file=sys.stderr)
                return 2
            ruleset = load_rules(lang, path=args.rules)
        else:
            ruleset = load_for_corpus(args.corpus)
    except (RuleError, CorpusError) as exc:
        print(f"rule file: {exc}", file=sys.stderr)
        return 2

    rules = [r for r in ruleset.rules
             if args.rule_id is None or r.rule_id == args.rule_id]
    if args.rule_id and not rules:
        print(f"no rule with id {args.rule_id!r} in the loaded file(s). Ids carry the "
              "file's language as a prefix.", file=sys.stderr)
        return 2
    if not rules:
        where = args.rules or f"rules/{{lang}}.yaml for {rule_langs(args.corpus)}"
        print(f"no rules loaded from {where}. That is the correct state at the start of "
              "iteration 1; write one and run this again.")
        return 0

    docs = [d for d in load(args.corpus) if d.split == "dev"]
    if not docs:
        print(f"{args.corpus}: the dev fold is empty.", file=sys.stderr)
        return 2

    pool = initial_error_pool(args.corpus)
    if args.practice:
        pool = practice_pool(pool, args.corpus)
    window, _ = draw_iteration(pool, args.corpus, args.iteration,
                               practice=args.practice)
    in_window = {(e.doc_id, e.start, e.end) for e in window}

    # gold, in scope, per document — the denominators
    gold_by_doc = {d.doc_id: [s for s in d.spans if s.in_scope] for d in docs}
    window_gold = [s for d in docs for s in gold_by_doc[d.doc_id]
                   if (d.doc_id, s.start, s.end) in in_window]

    hit_full: dict[str, set] = defaultdict(set)      # rule_id -> window gold keys
    hit_relaxed: dict[str, set] = defaultdict(set)
    dev_full: dict[str, set] = defaultdict(set)      # rule_id -> all dev gold keys
    fp: dict[str, list] = defaultdict(list)          # rule_id -> (doc_id, s, e)
    total_matches: dict[str, int] = defaultdict(int)

    subset = {r.rule_id for r in rules}
    for doc in docs:
        gold = gold_by_doc[doc.doc_id]
        for rule in rules:
            for start, end in rule.finditer(doc.text):
                total_matches[rule.rule_id] += 1
                touched = False
                for span in gold:
                    if _covers((start, end), span, RELAXED):
                        touched = True
                        key = (doc.doc_id, span.start, span.end)
                        if _covers((start, end), span, FULL):
                            dev_full[rule.rule_id].add(key)
                            if key in in_window:
                                hit_full[rule.rule_id].add(key)
                        if key in in_window:
                            hit_relaxed[rule.rule_id].add(key)
                if not touched:
                    fp[rule.rule_id].append((doc.doc_id, start, end))

    n_window = len(window_gold)
    n_dev = sum(len(v) for v in gold_by_doc.values())
    label = "practice window" if args.practice else f"iteration {args.iteration} window"

    print(f"# {args.corpus} dev — {len(docs)} documents, {n_dev} in-scope gold spans")
    print(f"# {label}: {n_window} spans  |  rule file version(s) {ruleset.versions}")
    print()
    print(f"{'rule_id':30} {'layer':15} {'caught':>20} {'matches':>8} "
          f"{'false pos':>10}")
    for rule in rules:
        rid = rule.rule_id
        caught = f"{len(hit_full[rid])}/{n_window}"
        partial = len(hit_relaxed[rid]) - len(hit_full[rid])
        if partial:
            # A rule that touches a span without covering it is the boundary error worth
            # showing separately: under the headline definition it is a miss, and an
            # author told only "0 caught" would rewrite a rule whose only fault is an
            # accent or a final character.
            caught += f" (+{partial} partial)"
        print(f"{rid:30} {rule.layer:15} {caught:>20} "
              f"{total_matches[rid]:>8} {len(fp[rid]):>10}")

    union_full = set().union(*(hit_full[r] for r in subset)) if subset else set()
    union_rel = set().union(*(hit_relaxed[r] for r in subset)) if subset else set()
    union_dev = set().union(*(dev_full[r] for r in subset)) if subset else set()
    all_fp = [x for r in subset for x in fp[r]]
    print()
    print(f"window   {len(union_full)}/{n_window} covered  "
          f"({len(union_rel)}/{n_window} touched, the relaxed lower bound)")
    print(f"dev-wide {len(union_dev)}/{n_dev} covered  — the same rules on spans the "
          "window did not show")
    print(f"false positives {len(all_fp)} across {len({d for d, _, _ in all_fp})} "
          "documents")
    # No precision figure. Precision is a 1:1 assignment over a merged prediction set
    # (CLAUDE.md, DESIGN §9.3); this tool runs one unmerged rule file and counting a
    # ratio here would be a number that disagrees with metrics.json for a reason nobody
    # would find. The scorer owns P/R/F1. This owns "did my rule fire, and where".
    print("# Counts, not metrics: P/R/F1 come from the scorer over a merged prediction")
    print("# set (DESIGN §9.3). Coverage here is fully_covered, relaxed in brackets.")

    if args.verbose and all_fp:
        print()
        print("# false positives — doc_id and character range, no text (CLAUDE.md)")
        for doc_id, start, end in sorted(all_fp)[:200]:
            print(f"  {doc_id}  [{start}, {end})  len {end - start}")
        if len(all_fp) > 200:
            print(f"  ... and {len(all_fp) - 200} more (not truncated in the counts "
                  "above)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
