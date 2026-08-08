#!/usr/bin/env python3
"""Run a rule file against the dev fold and print what it caught and what it cost.

This is the feedback path: write one rule, run this, see how many of the window's spans
it now covers and how many false positives it bought. Without it an author writes rules
into a file and learns nothing until a scoring run, which is not a loop.

**This is a sample view of `src/eval/run_fold.py`, not a second detector.** Detection
happens in exactly one function (`run_fold.detect_fold`) and this tool calls it. The
alternative was rejected on the shape its failure takes rather than on tidiness: two
implementations of "run these rules over these documents" drift, and the drift shows up
as *the sample says this rule fires and the fold-wide score says it does not*, with
nothing to say which is right. An author cannot act on that, and a reader comparing this
tool's counts to `metrics.json` cannot either. So the tool differs from the run path in
which spans it *shows* and never in which spans exist.

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

**An arm's rule file is passed with `--rules`, and there is no axis flag here.** Arm rule
files live under the arm (`paths.armrules`, DESIGN §5.3), and reaching one from this tool
means naming the path. That is deliberate: this tool takes `--corpus` and nothing else
about the experiment, for the same reason it has no `--split` — flags on a tool a person
runs forty times in an evening are typo surface, and four axis flags to locate an input
would be four chances to score one arm's rules under another's name. The orchestrator
knows its axes and builds the path; a person points at a file.

Usage:
    python tools/check_rules.py --corpus es-meddocan
    python tools/check_rules.py --corpus es-meddocan --rules /tmp/practice.yaml
    python tools/check_rules.py --corpus es-meddocan --rule-id es:doctor_prefix
    python tools/check_rules.py --corpus es-meddocan \
        --rules results/es-meddocan/R/sup-free/port-oneshot/rules/iter1/es.yaml
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora.base import CorpusError, rule_langs                 # noqa: E402
from src.eval.run_fold import (                                      # noqa: E402
    FoldRunError, detect_fold, load_fold,
)
from src.porting.human_arm import (                                  # noqa: E402
    draw_iteration, initial_error_pool, practice_pool,
)
from src.rules import RuleError, RuleSet, load_for_corpus, load_rules  # noqa: E402
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
                    help="a single rule file to run instead of the bootstrap "
                         "rules/{lang}.yaml — a practice file at /tmp/practice.yaml, or "
                         "an arm's own file under results/.../rules/iterN/ (DESIGN §5.3)")
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
    ap.add_argument("--audit", action="store_true",
                    help="list every match as doc_id, offsets and rule_id — no text. "
                         "This is the tool's detection made comparable to the run "
                         "path's spans.jsonl, line for line")
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

    # "dev" is a literal argument and there is no path by which it becomes anything
    # else. `load_fold` refuses `test` outright, but that refusal is not what protects
    # this tool — the absence of a flag is (see the module docstring and
    # `test_there_is_no_split_flag`).
    try:
        docs = load_fold(args.corpus, "dev")
    except (FoldRunError, CorpusError) as exc:
        print(f"{exc}", file=sys.stderr)
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
    # One detection pass, through the run path's own function. `--rule-id` narrows the
    # RuleSet rather than filtering the output, so what is detected here is exactly what
    # `run_fold` would detect from a file holding those rules — an after-the-fact filter
    # would have been a second detection semantics hiding behind the same flag.
    predictions = detect_fold(docs, RuleSet(rules=rules, versions=ruleset.versions))
    # Every match, in the same four fields spans.jsonl carries — so the claim that this
    # tool and `run_fold` detect identically is checkable from the outside instead of by
    # reading both for a shared import. Offsets and rule ids only, like every other line
    # this tool prints.
    audit = [(doc.doc_id, p.start, p.end, p.rule_id)
             for doc in docs for p in predictions[doc.doc_id]]
    for doc in docs:
        gold = gold_by_doc[doc.doc_id]
        for pred in predictions[doc.doc_id]:
            start, end, rid = pred.start, pred.end, pred.rule_id
            total_matches[rid] += 1
            touched = False
            for span in gold:
                if _covers((start, end), span, RELAXED):
                    touched = True
                    key = (doc.doc_id, span.start, span.end)
                    if _covers((start, end), span, FULL):
                        dev_full[rid].add(key)
                        if key in in_window:
                            hit_full[rid].add(key)
                    if key in in_window:
                        hit_relaxed[rid].add(key)
            if not touched:
                fp[rid].append((doc.doc_id, start, end))

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

    if args.audit:
        print()
        print("# every match — doc_id, character range, rule_id. No text, and not "
              "truncated: this listing is compared line for line against the run "
              "path's spans.jsonl (test_run_fold.py).")
        for doc_id, start, end, rid in audit:
            print(f"  {doc_id}  [{start}, {end})  {rid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
