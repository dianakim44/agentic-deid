"""Does scoring dev in a child process give the same numbers? DESIGN §13.6.

    python3 tools/verify_child_scoring.py --weights experiments/hybrid_v24/weights/seed1

The v24b training path was switched to `--dev-in-subprocess` after holdout
`es-meddocan` died of an MPS out-of-memory in epoch 7's backward pass. The switch is
only admissible if the child computes what the in-process path computed, so this script
runs both over the same weights and the same dev folds and compares them field by field.

**It is run against a *completed* model, and the completed model available is v24's
seed 1** — the in-domain tagger whose dev headline is already published
(`experiments/hybrid_v24/tagger_seed1.json`, `results/{corpus}/T/sup-human/
port-hybrid/metrics.json`). No LOCO model had finished when the switch was made, which
is the whole reason the switch was needed, so verifying against one was not available.

Equality is expected to be **exact**, not approximate: DESIGN §12.7 measured tagger
inference as deterministic on MPS (three repeat runs byte-identical, C = 1.000000). A
tolerance here would hide the one thing that could go wrong — a child that loads
different weights, or a different corpus list, than the parent would have scored.

This opens nothing, trains nothing and calls no LLM. Counts and rates only; no surface.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tagger.train import (CORPORA, score_dev, score_dev_in_child)  # noqa: E402

#: Keys the comparison ignores, and why each is not a number the two paths disagree on.
#: `memory` and `mps_driver_gb` are measurements *of the process that took them*, so a
#: parent and a child are supposed to differ there; that is the point of the change.
IGNORED = ("memory", "mps_driver_gb")


def flatten(dev: dict) -> dict:
    """`{corpus.field: value}` plus the summed criterion, with `IGNORED` dropped."""
    out = {"summed_L_full": dev["summed_L_full"]}
    for corpus, values in dev["per_corpus"].items():
        for field, value in values.items():
            out[f"{corpus}.{field}"] = value
    return out


def main(argv: list[str] | None = None) -> int:
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    from src.tagger.train import build

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weights", required=True,
                        help="a completed model's weights directory")
    parser.add_argument("--corpora", nargs="*", default=list(CORPORA),
                        help="which dev folds to score through both paths")
    parser.add_argument("--detector", default="T")
    args = parser.parse_args(argv)

    weights = Path(args.weights)
    if not (weights / "model.safetensors").is_file():
        raise SystemExit(f"no model.safetensors under {weights}")

    print(f"weights  {weights}")
    print(f"corpora  {' '.join(args.corpora)}")

    started = time.time()
    child = score_dev_in_child(weights, list(args.corpora), detector=args.detector)
    child_seconds = time.time() - started

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(weights)
    model = AutoModelForTokenClassification.from_pretrained(weights).to(device)
    _, dev_docs = build(tokenizer, "dev", list(args.corpora))
    started = time.time()
    parent = score_dev(model, tokenizer, dev_docs, device, detector=args.detector)
    parent_seconds = time.time() - started

    left, right = flatten(parent), flatten(child)
    missing = sorted(set(left) ^ set(right))
    differing = sorted(k for k in set(left) & set(right) if left[k] != right[k])

    print(f"child    {len(right)} values in {child_seconds:.0f}s")
    print(f"parent   {len(left)} values in {parent_seconds:.0f}s")
    if missing:
        print(f"FAILED   {len(missing)} key(s) present on one side only: {missing}",
              file=sys.stderr)
        return 1
    if differing:
        for key in differing:
            print(f"  {key}: parent {left[key]!r} child {right[key]!r}",
                  file=sys.stderr)
        print(f"FAILED   {len(differing)} of {len(left)} values differ. Do not switch "
              "the training path: the child would be selecting epochs on different "
              "numbers than the in-process path published.", file=sys.stderr)
        return 1

    print(f"verify   all {len(left)} values identical "
          f"(exact equality, no tolerance)")
    out = ROOT / "experiments" / "loco_v24b" / "child_scoring_verified.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "weights": str(weights),
        "corpora": list(args.corpora),
        "values_compared": len(left),
        "identical": True,
        "ignored_keys": list(IGNORED),
        "child_seconds": round(child_seconds, 1),
        "parent_seconds": round(parent_seconds, 1),
        "values": left,
    }, indent=1) + "\n", encoding="utf-8")
    print(f"-> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
