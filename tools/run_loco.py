"""Drive the v24b leave-one-corpus-out arms. DESIGN §13.

    python3 tools/run_loco.py predict
    python3 tools/run_loco.py score-dev [--write]

Two arms, informally `T-loco` and `RT-loco`, which are the coordinates
`T`/`sup-transfer`/`port-hybrid` and `RT`/`sup-transfer`/`port-hybrid`
(`experiments/loco_v24b/registration.yaml`). The rung is `port-hybrid` for the reason it
is in v24: nothing is authored here.

**This is `tools/run_hybrid.py` with one factor changed, and it reuses that driver rather
than restating it.** `score_policy`, `headline`, `rules_fields` and the prediction cache
format are imported, so the two sets of arms are scored by one implementation and a
difference between the v24 table and the v24b table cannot come from the scoring. What is
different is which weights predict on which corpus: v24 has one model for all five, v24b
has five models and corpus `c` is predicted by the model that never saw `c`.

**No subcommand reads the sealed fold, and no subcommand calls an LLM.** Opening the seal
is `src/eval/run_sealed_eval.py`'s; v24b consults no agent at all (§13.2 — the selector
and the Arbiter both read target-corpus dev labels, which is what this arm declares it
does not use).

Data handling: offsets, types, counts and rates only. No span surface and no document
text is written or printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.corpora import base                                       # noqa: E402
from src.eval import guideline, scorer                             # noqa: E402
from src.eval.run_fold import read_arm_spans                       # noqa: E402
from src.merge import policy as pol                                # noqa: E402
from src.tagger.train import LOCO_OUT                              # noqa: E402
from tools import run_hybrid as hybrid                             # noqa: E402

OUT = ROOT / LOCO_OUT
REGISTRATION = OUT / "registration.yaml"

#: DESIGN §13.0. The one axis value v24b added, and the only thing separating these arms
#: from v24's at the same detector.
SUPERVISION = "sup-transfer"
PORTING = "port-hybrid"

#: §13.1: one seed, and it is v24's manuscript seed so that in-domain versus LOCO stays a
#: one-factor comparison. Recorded in each arm's run block as `tagger_seed`.
SEED = 1


def _shown(path: Path) -> str:
    """A path for a message: repository-relative when it is inside the repository.

    `Path.relative_to` raises for anything outside, and a crash while building an error
    message replaces a diagnosis with a traceback about the diagnosis. The same helper
    `src/eval/sealed_log.py` has, for the same reason and found the same way: the two
    refusals below are the ones that keep a model which saw the target out of a
    `sup-transfer` arm, and under a redirected root they were raising `ValueError` from
    the f-string instead of `SystemExit` with the reason.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def registration() -> dict:
    return yaml.safe_load(REGISTRATION.read_text(encoding="utf-8"))


def corpora() -> list[str]:
    return list(registration()["corpora"])


def rules_arm(corpus: str) -> str:
    """The R arm whose rule files `RT-loco` composes with — v24's choice, unchanged.

    Read from v24b's own registration rather than from v24's, because this registration
    is what binds this experiment; that the two agree is checked by
    `tests/test_loco.py`, where a disagreement is the finding rather than something this
    function silently resolves.
    """
    return registration()["rules_arm"][corpus]


# ─── the five models ────────────────────────────────────────────────────────

def weights_dir(corpus: str) -> Path:
    """Where the model that did **not** see `corpus` lives."""
    return OUT / "weights" / f"loco-{corpus}"


def record_path(corpus: str) -> Path:
    return OUT / f"tagger_loco_{corpus}.json"


def weights_sha256(corpus: str) -> str:
    """SHA-256 of `model.safetensors` — the arm's identity (DESIGN §12.7, §12.8)."""
    path = weights_dir(corpus) / "model.safetensors"
    if not path.is_file():
        raise SystemExit(
            f"no weights at {_shown(path)}. Train the holdout first: "
            f"python3 -m src.tagger.train --seed {SEED} --holdout {corpus}"
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def training_record(corpus: str) -> dict:
    path = record_path(corpus)
    if not path.is_file():
        raise SystemExit(
            f"no training record at {_shown(path)}. The selected epoch and the "
            "commit that trained the weights are read from it rather than restated, so "
            "the number in the arm's record is the one the selection wrote."
        )
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("holdout") != corpus:
        raise SystemExit(
            f"{_shown(path)} records holdout={record.get('holdout')!r} and "
            f"this is the {corpus!r} cell. A record naming a different holdout would "
            "put a model that saw this corpus's labels under an arm declaring "
            f"{SUPERVISION!r}."
        )
    if record.get("holdout_dev_used") is not False:
        raise SystemExit(
            f"{_shown(path)} does not record holdout_dev_used=False. That "
            f"field is what makes {SUPERVISION!r} true of this arm (DESIGN §13.1), and "
            "an absent field is not a statement."
        )
    return record


def loco_fields(corpus: str, policies: dict) -> dict:
    """The six run-block fields `run_sealed_eval._hybrid` requires, plus v24b's three.

    The six are v24's and are read the same way. The three extra — `tagger_holdout`,
    `tagger_train_corpora` and `tagger_holdout_dev_used` — are what a reader needs to
    tell this arm's weights from the in-domain ones without re-deriving it from the
    supervision axis, and they are copied from the training record rather than restated
    here for `hybrid_fields`' reason: the record is what the run wrote.
    """
    record = training_record(corpus)
    weights = weights_dir(corpus)
    try:
        relative = weights.relative_to(ROOT)
    except ValueError:
        # `run_sealed_eval._hybrid` resolves this field as `base.ROOT / value`, so a
        # path outside the repository cannot be recorded in it. Refused with the reason
        # rather than raising from the f-string.
        raise SystemExit(
            f"the weights at {_shown(weights)} are outside the repository, and "
            "`tagger_weights` is resolved relative to it when the seal is opened. "
            "Move them under experiments/loco_v24b/weights/ (the off-repo copy is a "
            "backup, not the artefact the record names)."
        ) from None
    return {
        "tagger_weights": str(relative),
        "tagger_weights_sha256": weights_sha256(corpus),
        "tagger_seed": record["seed"],
        "tagger_epoch": record["selected_epoch"],
        "tagger_commit": record["commit"],
        "tagger_holdout": record["holdout"],
        "tagger_train_corpora": record["train_corpora"],
        "tagger_holdout_dev_used": record["holdout_dev_used"],
        "merge_policy": pol.as_record(policies),
    }


# ─── the held-out corpus's dev predictions, cached ──────────────────────────

def pred_path(corpus: str, split: str = "dev") -> Path:
    return OUT / "preds" / f"{corpus}.loco.{split}.jsonl"


def cmd_predict(args) -> int:
    """Each corpus's dev fold, predicted by the model that did not see it.

    Five models and five loads, where v24's `predict` loads one model and sweeps the five
    corpora. That is the whole shape of leave-one-corpus-out and it is why this is a
    separate subcommand rather than a flag on v24's.
    """
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer
    from src.tagger.train import predict

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    for corpus in corpora():
        weights = weights_dir(corpus)
        if not weights.is_dir():
            raise SystemExit(
                f"no weights at {_shown(weights)}. Train it first: "
                f"python3 -m src.tagger.train --seed {SEED} --holdout {corpus}"
            )
        # Verified before inference, not after: the hash is the arm's identity and a
        # mismatch found afterwards means a cache written by weights nobody named.
        digest = weights_sha256(corpus)
        tokenizer = AutoTokenizer.from_pretrained(weights)
        model = AutoModelForTokenClassification.from_pretrained(weights).to(device)
        docs = [d for d in base.load(corpus) if d.split == args.split]
        started = time.time()
        preds = predict(model, tokenizer, docs, device, detector="T")
        path = pred_path(corpus, args.split)
        hybrid.write_preds(path, preds)
        total = sum(len(v) for v in preds.values())
        print(f"{corpus:12s} {args.split} docs {len(docs):5d} spans {total:6d} "
              f"weights {digest[:12]}… ({time.time() - started:.0f}s) -> "
              f"{_shown(path)}", flush=True)
    return 0


def load_halves(corpus: str, split: str = "dev"):
    """`(docs, rules_by_doc, tagger_by_doc)` — v24's loader with the LOCO cache."""
    docs = [d for d in base.load(corpus) if d.split == split]
    rules_by_doc: dict[str, list] = {}
    for span in read_arm_spans(corpus=corpus, detector="R",
                               supervision="sup-free", porting=rules_arm(corpus)):
        rules_by_doc.setdefault(span.doc_id, []).append(span)
    path = pred_path(corpus, split)
    if not path.exists():
        raise SystemExit(
            f"no cached LOCO predictions at {_shown(path)}. Run "
            f"`predict --split {split}` first."
        )
    return docs, rules_by_doc, hybrid.read_preds(path)


# ─── score-dev: T-loco and RT-loco ──────────────────────────────────────────

def _run_block(corpus: str, detector: str) -> dict:
    from src.eval import sealed_log
    commit, tree = sealed_log.tree_state()
    return {
        "corpus": corpus, "detector": detector, "supervision": SUPERVISION,
        "porting": PORTING, "split": "dev", "model_id": base.model_id_absent(),
        "generated": hybrid.now(), "commit": commit, "tree": tree,
    }


def per_type_rows(scored: dict) -> dict:
    """Per-type dev numbers and the four complementarity cells, in one table.

    Both halves come from the record the arm publishes — `modes.relaxed.by_type` for the
    1:1 numbers and `modes.fully_covered.complementarity.by_type` for the cells — because
    CLAUDE.md puts P/R/F1 on the 1:1 assignment and the breakdown on the prediction
    union. Joining them here rather than re-deriving either keeps the report's table and
    the arm's record the same numbers.
    """
    relaxed = scored["modes"][scorer.RELAXED]["by_type"]
    full = scored["modes"][scorer.FULLY_COVERED]
    compl = full["complementarity"]["by_type"]
    leak = full["by_type"]
    out = {}
    for phi_type in base.canonical_types():
        row = relaxed.get(phi_type)
        if row is None and phi_type not in compl:
            continue
        row = row or {}
        cells = (compl.get(phi_type) or {}).get("families", {})
        out[phi_type] = {
            "gold": row.get("gold", 0),
            "tp": row.get("tp", 0), "fp": row.get("fp", 0), "fn": row.get("fn", 0),
            "A": guideline.accuracy(row.get("tp", 0), row.get("fp", 0),
                                    row.get("fn", 0)),
            "precision": row.get("precision"), "recall": row.get("recall"),
            "f1": row.get("f1"),
            "L_full": (leak.get(phi_type) or {}).get("leak_rate"),
            "L_rel": row.get("leak_rate"),
            "sparse": row.get("sparse"),
            "complementarity": {k: cells.get(k, 0) for k in
                                ("rules_only", "tagger_only", "both", "neither")},
        }
    return out


def cmd_score_dev(args) -> int:
    from src.termination import not_applicable

    summary = {}
    for corpus in corpora():
        docs, rules_by_doc, tagger_by_doc = load_halves(corpus)
        row = {}
        for detector, policies in (
                ("T", {t: pol.Choice("T", 0.5) for t in base.canonical_types()}),
                ("RT", pol.baseline_map())):
            started = time.time()
            scored, _ = hybrid.score_policy(docs, rules_by_doc, tagger_by_doc, policies)
            row[detector] = {
                **hybrid.headline(scored),
                "by_type": per_type_rows(scored),
            }
            if args.write:
                extra = loco_fields(corpus, policies)
                if detector == "T":
                    # §12.7's `none_declared`: `T` composes no rule files and says so.
                    extra["rules_source"] = base.rules_source_absent()
                else:
                    extra.update(hybrid.rules_fields(corpus))
                scorer.write_metrics(
                    scored, run={**_run_block(corpus, detector), **extra},
                    cost=hybrid.zero_cost(time.time() - started),
                    termination=not_applicable(corpus).record(),
                )
            print(f"{corpus:12s} {detector:2s}-loco L_full {row[detector]['L_full']:.4f} "
                  f"L_rel {row[detector]['L_rel']:.4f} A {row[detector]['A']:.4f} "
                  f"R {row[detector]['recall']:.4f} F1 {row[detector]['f1']:.4f}",
                  flush=True)
        summary[corpus] = row
    path = OUT / "dev_loco.json"
    path.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(f"-> {_shown(path)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    one = sub.add_parser("predict")
    one.add_argument("--split", default="dev", choices=("train", "dev"))
    one.set_defaults(fn=cmd_predict)

    two = sub.add_parser("score-dev")
    two.add_argument("--write", action="store_true")
    two.set_defaults(fn=cmd_score_dev)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
