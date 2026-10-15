"""Where the hybrid arms' dev false positives come from, split at the no-gold line.

    python3 tools/diagnose_nogold.py --seed 1

DESIGN §12.2 fixes a type with **no dev gold** at `UNION@0.5` and does not optimise it:
there is nothing to select on and selecting anyway would be selecting on zero observations.
That rule applies to RT-Sel and to RT-Arb alike, and `T` is not subject to it — `T` is
`T@0.5` on every type by definition. So on a corpus whose rules half is weak, the two
choosers carry rule predictions on the unoptimisable types that `T` does not carry, and the
question this script answers is how much of their FP count that accounts for.

**Post-hoc, and it opens nothing.** Every number here is read from `dev_table_seed{seed}.json`
— the per-type candidate table already committed for the registered arms — so the
counterfactual (no-gold types at `T@0.5` instead) is a re-summation of counts that exist,
not a new scoring and not a new run. DESIGN §12.2's separability argument is what makes the
re-summation exact: type `t`'s TP/FP/FN and leaked count depend on type `t`'s predictions
alone. The script asserts that by reproducing each arm's committed dev headline from the
same sums before it reports anything.

The counterfactual is **not** a selection and licenses nothing: no test fold is read here,
and DESIGN §12.2's rule stands as registered. It is reported as a post-hoc diagnosis of a
registered rule's cost.

No surface and no document id is read or printed. Types, counts and rates only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.corpora.base import canonical_types                       # noqa: E402
from src.eval import scorer                                        # noqa: E402
from src.merge import policy as pol                                # noqa: E402

OUT = ROOT / "experiments" / "hybrid_v24"

#: The arms this diagnosis covers. `RT` is included as the reference point the δ
#: constraint is stated against, so that a reader can see the baseline the two choosers
#: were allowed to move away from.
ARMS = ("T", "RT", "RT-Sel", "RT-Arb")

#: What a no-gold type is replaced with in the counterfactual. `T@0.5` and not `R` or
#: `AGREE`: the comparison the diagnosis is for is against the `T` arm, so the substitute
#: has to be the value `T` already holds on that type.
SUBSTITUTE = pol.Choice(policy="T", theta=0.5)


def arm_policies(corpus: str, detector: str) -> dict[str, pol.Choice]:
    """The policy map the arm actually ran, from its committed dev record."""
    path = ROOT / scorer.path_template("metrics").format(
        corpus=corpus, detector=detector, supervision="sup-human",
        porting="port-hybrid")
    record = json.loads(path.read_text(encoding="utf-8"))
    return pol.from_record(record["run"]["merge_policy"])


def arm_headline(corpus: str, detector: str) -> dict:
    path = ROOT / scorer.path_template("metrics").format(
        corpus=corpus, detector=detector, supervision="sup-human",
        porting="port-hybrid")
    record = json.loads(path.read_text(encoding="utf-8"))
    overall = record["modes"][scorer.RELAXED]["overall"]
    return {
        "L_full": record["headline"]["leak_rate"]["value"],
        "A": accuracy(overall["tp"], overall["fp"], overall["fn"]),
        "fp": overall["fp"],
    }


def accuracy(tp: int, fp: int, fn: int) -> float:
    denominator = tp + fp + fn
    return tp / denominator if denominator else 0.0


def sum_over(table: dict, policies: dict[str, pol.Choice]) -> dict:
    """Whole-corpus counts for a policy map, summed from the cached per-type rows.

    The same arithmetic as `selector.combine`, over the committed record rather than a
    rebuilt table — the point of this script is that it rebuilds nothing.
    """
    tp = fp = fn = leaked_full = n_gold = 0
    for phi_type in canonical_types():
        row = table[phi_type]
        counts = row["candidates"][str(policies[phi_type])]
        tp += counts["tp"]
        fp += counts["fp"]
        fn += counts["fn"]
        leaked_full += counts["leaked_full"]
        n_gold += counts["n_gold"]
    return {
        "tp": tp, "fp": fp, "fn": fn, "n_gold": n_gold,
        "A": accuracy(tp, fp, fn),
        "L_full": leaked_full / n_gold if n_gold else None,
    }


def fp_split(table: dict, policies: dict[str, pol.Choice],
             nogold: list[str]) -> dict:
    """FP in the two buckets: the no-gold types, and every other type."""
    out = {"nogold": 0, "rest": 0, "by_type_nogold": {}}
    for phi_type in canonical_types():
        fp = table[phi_type]["candidates"][str(policies[phi_type])]["fp"]
        if phi_type in nogold:
            out["nogold"] += fp
            out["by_type_nogold"][phi_type] = fp
        else:
            out["rest"] += fp
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--corpora", nargs="*",
                        default=["en-deid", "ko-surro"])
    args = parser.parse_args(argv)

    cached = json.loads(
        (OUT / f"dev_table_seed{args.seed}.json").read_text(encoding="utf-8"))

    out = {}
    for corpus in args.corpora:
        table = cached[corpus]["table"]
        nogold = [t for t in canonical_types() if table[t]["n_gold"] == 0]
        row = {
            "nogold_types": nogold,
            "n_dev_gold": cached[corpus]["n_dev_gold"],
            "delta": cached[corpus]["delta"],
            "arms": {},
        }
        print(f"\n{corpus}  n_dev_gold {row['n_dev_gold']}  "
              f"delta {row['delta']:.4f}  no-gold types {nogold or 'none'}")
        print(f"  {'arm':7s} {'FP no-gold':>10s} {'FP rest':>8s} {'FP':>6s} "
              f"{'A':>7s} {'L_full':>7s}  |  post-hoc no-gold->T@0.5: "
              f"{'A':>7s} {'L_full':>7s}")
        for detector in ARMS:
            policies = arm_policies(corpus, detector)
            as_run = sum_over(table, policies)
            committed = arm_headline(corpus, detector)
            # The separability check. If the per-type sums do not reproduce the arm's
            # own published headline, every number below is a different object and the
            # diagnosis is worthless — so it stops rather than reports.
            for field, left, right in (("A", as_run["A"], committed["A"]),
                                       ("L_full", as_run["L_full"],
                                        committed["L_full"]),
                                       ("fp", as_run["fp"], committed["fp"])):
                if abs(left - right) > 1e-9:
                    raise SystemExit(
                        f"{corpus} {detector}: per-type sum of {field} is {left!r} but "
                        f"the committed dev record says {right!r}. DESIGN §12.2's "
                        "separability does not hold for this arm, so the no-gold "
                        "re-summation below would not be the arm's own number."
                    )
            substituted = {**policies,
                           **{t: SUBSTITUTE for t in nogold}}
            post = sum_over(table, substituted)
            split = fp_split(table, policies, nogold)
            row["arms"][detector] = {
                "policies": pol.as_record(policies),
                "as_run": {**as_run, "fp_nogold": split["nogold"],
                           "fp_rest": split["rest"],
                           "fp_by_nogold_type": split["by_type_nogold"]},
                "post_hoc_nogold_as_T": {
                    **post,
                    "fp_nogold": fp_split(table, substituted, nogold)["nogold"],
                    "changed_types": nogold,
                    "note": "post-hoc; no test fold read; DESIGN §12.2 unchanged",
                },
            }
            print(f"  {detector:7s} {split['nogold']:10d} {split['rest']:8d} "
                  f"{as_run['fp']:6d} {as_run['A']:7.4f} {as_run['L_full']:7.4f}  "
                  f"|{' ':26s}{post['A']:7.4f} {post['L_full']:7.4f}")
        out[corpus] = row

    path = OUT / f"dev_nogold_diagnosis_seed{args.seed}.json"
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"\n-> {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
