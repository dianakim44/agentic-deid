"""The only way to evaluate on a sealed test fold.

    python3 -m src.eval.run_sealed_eval --corpus es-meddocan \
        --purpose "final evaluation of the four arms, pre-registered"

The loader's seal gate accepts this module by import identity (`base.SEALED_CALLER`),
so an interactive session cannot reach the sealed fold no matter what it passes. What
this module adds on top of the gate:

  - it verifies the frozen split file against the corpus before reading anything,
    so the fold being evaluated is provably the fold that was frozen;
  - it requires a stated purpose, which goes into `results/sealed_eval_log.md`;
  - it refuses to run on a dirty working tree unless explicitly overridden, so the
    commit hash in the log describes the code that ran (see `--allow-dirty`).

Scoring is not here yet. This is the access path and its guarantees; the metrics go
behind it once the detection layers exist (DESIGN §6). It is written first on
purpose — the seal has to be enforceable before there is anything worth evaluating,
because afterwards there is a reason to want it not to be.
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from ..corpora import base
from ..corpora.base import CorpusError, Document, SealError
from . import sealed_log


def load_sealed(corpus_id: str, *, purpose: str, allow_dirty: bool = False) -> list[Document]:
    """Every fold including the sealed one, after logging the access.

    Returns all folds rather than the sealed fold alone: a test evaluation reports
    per-fold numbers, and re-loading train+dev separately would mean two reads and
    two chances for them to disagree.
    """
    commit, tree = sealed_log.tree_state()
    if tree != "clean" and not allow_dirty:
        raise SealError(
            f"the working tree is {tree}, so commit {(commit or 'unknown')[:12]} "
            "does not describe the code that would run, and the log row would name "
            "a commit that never produced these numbers. Commit first. If the "
            "change is genuinely irrelevant to the evaluation, pass --allow-dirty: "
            "the run then proceeds and the row records tree=dirty, which is the "
            "honest version of the same claim."
        )

    loader = _loader_for(corpus_id)
    # Verified before the sealed read, not after: if the frozen split and the
    # corpus disagree, the run must not happen at all — and the check itself must
    # not need the sealed fold, which is why it runs on the unsealed load.
    _verify_frozen_split(loader, corpus_id)

    # `sealed=True` triggers the gate, which appends to the log and aborts if the
    # append fails. The logging is deliberately *not* done here: one read must
    # produce exactly one row, so the append lives at the gate — the point past
    # which the fold becomes reachable — and nowhere else.
    return loader.load(sealed=True, purpose=purpose, arms="none (access check)")


def _loader_for(corpus_id: str) -> base.CorpusLoader:
    if corpus_id == "es-meddocan":
        from ..corpora.meddocan import MeddocanLoader

        return MeddocanLoader()
    raise CorpusError(
        f"{corpus_id!r} has no loader yet (implemented: ['es-meddocan'])"
    )


def _verify_frozen_split(loader: base.CorpusLoader, corpus_id: str) -> None:
    """The unsealed folds must still match the frozen file.

    Only the unsealed folds can be checked here, and that is enough for what this
    check is for: if train or dev has drifted since the freeze, the corpus on disk
    is not the corpus the split file describes, and the sealed fold's recorded
    summaries are equally suspect.
    """
    from ..split import read

    record = read(corpus_id)
    unsealed = base.CorpusLoader.load(loader)
    assigned = {
        doc_id: fold
        for fold, block in record["folds"].items()
        for doc_id in block["document_ids"]
    }
    for doc in unsealed:
        if assigned.get(doc.doc_id) != doc.split:
            raise SealError(
                f"{corpus_id}/{doc.doc_id}: the frozen split file and the corpus on "
                "disk disagree about this document's fold. The sealed evaluation "
                "does not run."
            )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, help="corpus id from naming.yaml")
    parser.add_argument(
        "--purpose",
        required=True,
        help="why the test fold is being opened; recorded in the log verbatim",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help=(
            "proceed with uncommitted changes; the log row records tree=dirty. "
            "Refused by default because the commit hash would otherwise name code "
            "that never ran."
        ),
    )
    args = parser.parse_args(argv)

    try:
        docs = load_sealed(
            args.corpus, purpose=args.purpose, allow_dirty=args.allow_dirty
        )
    except (CorpusError, SealError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1

    by_fold: dict[str | None, int] = {}
    for doc in docs:
        by_fold[doc.split] = by_fold.get(doc.split, 0) + 1
    print(f"sealed read of {args.corpus} recorded in {sealed_log.LOG.name}")
    for fold, n in sorted(by_fold.items(), key=lambda kv: str(kv[0])):
        print(f"  {fold:6} {n:5} documents")
    print(f"this corpus's test fold has now been opened {sealed_log.count_runs(args.corpus)}x")
    print("scoring is not implemented yet — this run exercised the access path only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
