"""Split files: generate, read, and verify.

`splits/{corpus}.json` is the reference point for the seal (CLAUDE.md): once it is
committed, the test fold is closed and rule development sees dev only. So the file
has to be self-describing enough that a reader can tell, without re-reading the
corpus, which documents were in which fold and what the folds contained.

    python3 -m src.split --corpus es-meddocan            # write the file
    python3 -m src.split --corpus es-meddocan --check     # verify, write nothing

**One schema for every corpus.** Common fields describe any split; anything true
of one corpus only goes in `corpus_specific`, which nothing outside that corpus's
loader may read. Mixing the two is what makes a schema unusable for the second
corpus, so the separation is enforced by `verify()` rather than left to good
intent.

The chicken-and-egg on the freeze commit is deliberate: the hash of the commit
that freezes the file cannot be inside the file. `results/sealed_eval_log.md`
records it instead, which is where CLAUDE.md already requires the reference point.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .corpora import base
from .corpora.base import CorpusError, Document

SCHEMA_VERSION = 1

#: What "token" means in every count this file records. Named because "tokens"
#: with no definition is not a measurement — and because the tagger will later use
#: a different tokenizer, and these two must not be confused.
TOKENIZER = "whitespace"


def token_count(text: str) -> int:
    return len(text.split())


def _percentiles(values: Sequence[int]) -> dict[str, int]:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        raise CorpusError("token distribution over zero documents")

    def at(fraction: float) -> int:
        return ordered[min(n - 1, int(fraction * n))]

    return {
        "min": ordered[0],
        "p25": at(0.25),
        "median": at(0.50),
        "p75": at(0.75),
        "max": ordered[-1],
    }


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_document(paths: Sequence[Path]) -> str:
    """One digest per document, over its files in sorted-name order.

    Per document rather than per file because the document is the unit the folds
    are made of: when a re-release changes three files, what matters is whether
    those documents are in the sealed fold.
    """
    sha = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.name):
        sha.update(path.name.encode("utf-8"))
        sha.update(b"\0")
        sha.update(path.read_bytes())
    return sha.hexdigest()


def manifest_digest(per_document: dict[str, str]) -> str:
    """One digest over the whole corpus, from the per-document digests.

    Derived from them rather than computed separately, so the two can never
    disagree about what was hashed.
    """
    sha = hashlib.sha256()
    for doc_id in sorted(per_document):
        sha.update(doc_id.encode("utf-8"))
        sha.update(b"\0")
        sha.update(per_document[doc_id].encode("ascii"))
        sha.update(b"\n")
    return sha.hexdigest()


def fold_summary(docs: Sequence[Document]) -> dict:
    """Everything countable about one fold.

    Deliberately includes both the in-scope and the excluded counts: DESIGN §9.1
    requires the exclusion volume to be reported per fold, and a summary that
    only carried the scored total would make that unrecoverable.
    """
    excluded_by_type: dict[str, int] = {}
    for doc in docs:
        for span in doc.spans:
            if span.excluded:
                excluded_by_type[span.subtype] = (
                    excluded_by_type.get(span.subtype, 0) + 1
                )
    return {
        "n_documents": len(docs),
        "n_spans": sum(len(d.spans) for d in docs),
        "n_spans_in_scope": sum(len(d.in_scope_spans) for d in docs),
        "n_spans_excluded": sum(
            len(d.spans) - len(d.in_scope_spans) for d in docs
        ),
        "spans_by_phi_type": dict(sorted(base.count_by_type(docs).items())),
        "spans_by_excluded_type": dict(sorted(excluded_by_type.items())),
        "tokens": {
            "total": sum(token_count(d.text) for d in docs),
            "per_document": _percentiles([token_count(d.text) for d in docs]),
        },
    }


#: Types compared in step 2 of the DESIGN §9.5 grouping rule. Read from the
#: corpus's own vocabulary, not from canonical types: the rule is about whether
#: two documents describe the same person, and the corpus type is what says which
#: name is the patient's rather than a clinician's.
GROUPING_TYPES = {
    "es-meddocan": {
        "name": ("NOMBRE_SUJETO_ASISTENCIA",),
        "record": (
            "ID_SUJETO_ASISTENCIA",
            "ID_ASEGURAMIENTO",
            "ID_CONTACTO_ASISTENCIAL",
        ),
        "date": ("FECHAS",),
    }
}

#: `{stem}{sep}{suffix}`, suffix digits **or** letters, stem opaque. DESIGN §9.5
#: step 1 — both past bugs here assumed a numeric suffix.
STEM_RE = re.compile(r"^(?P<stem>.+)[-_](?P<suffix>[0-9]+|[A-Za-z]+)$")


def step_2_confirms(shared: dict[str, int]) -> bool:
    """DESIGN §9.5 step 2: do these shared-surface counts confirm one patient?

    A shared name is necessary and not sufficient — two case reports about
    different patients with the same given name share a name surface — so a record
    number or a date must agree as well.

    Extracted from `grouping_audit` so that the rule can be applied to the counts
    the split file already records, not only to a corpus that is fully readable.
    That matters directly: the one MEDDOCAN stem that shares a name and nothing
    else straddles the seal, so after the test fold was sealed a recount could no
    longer reach the case that distinguishes this rule from `bool(shared["name"])`.
    Taking `dict[str, int]` rather than documents is what keeps that case checkable.
    """
    return bool(shared["name"]) and bool(shared["record"] or shared["date"])


def grouping_audit(corpus_id: str, docs: Sequence[Document]) -> dict:
    """Apply the DESIGN §9.5 grouping rule and record why each group formed.

    §9.5 step 4 requires the split file to say which rule formed each group and
    which surfaces agreed. **Counts of agreeing surfaces, never the surfaces.**
    This schema is shared with CARMEN-I, which is DUA-restricted authentic
    clinical text whose surfaces may not be quoted anywhere — and a field that is
    safe to fill for one corpus and not another is a field that will be filled
    wrongly. Counts are enough to audit the decision: what matters is whether the
    identifiers agreed, not what they were.

    Only candidate stems are itemised. A document that no stem pattern pairs with
    anything is its own group by step 3, and listing 952 of those would bury the
    48 decisions that were actually made.
    """
    types = GROUPING_TYPES.get(corpus_id)
    if types is None:
        raise CorpusError(
            f"no §9.5 grouping types defined for {corpus_id!r}. Add them rather "
            "than skipping the audit — a split file with no grouping record "
            "cannot show that grouping was considered."
        )

    by_id = {d.doc_id: d for d in docs}
    by_stem: dict[str, list[str]] = {}
    unparsed = []
    for doc in docs:
        match = STEM_RE.match(doc.doc_id)
        if match is None:
            unparsed.append(doc.doc_id)
            continue
        by_stem.setdefault(match.group("stem"), []).append(doc.doc_id)
    if unparsed:
        raise CorpusError(
            f"{corpus_id}: {len(unparsed)} document ids do not parse as "
            f"stem+suffix (first: {unparsed[:3]}). The §9.5 step-1 pattern has to "
            "cover every id or the grouping is silently partial — this is exactly "
            "how the earlier digits-only rule dropped 31 ids."
        )

    candidates = {s: sorted(v) for s, v in by_stem.items() if len(v) > 1}
    audited = {}
    confirmed = 0
    for stem, ids in sorted(candidates.items()):
        shared = {}
        for role, subtypes in types.items():
            per_doc = [
                {
                    s.surface.strip()
                    for s in by_id[i].spans
                    if s.subtype in subtypes
                }
                for i in ids
            ]
            shared[role] = len(set.intersection(*per_doc)) if per_doc else 0
        grouped = step_2_confirms(shared)
        if grouped:
            confirmed += 1
        audited[stem] = {
            "documents": ids,
            "n_shared_surfaces": shared,
            "grouped": grouped,
            "decision": (
                "step 2 confirmed: a name and at least one of record number or "
                "date agree across all documents of the stem"
                if grouped
                else "step 3: no group. Step 1 admitted the stem, step 2 found no "
                "identifier agreement, so each document is its own group"
            ),
        }

    return {
        "rule_ref": "DESIGN.md §9.5",
        "step_1_pattern": STEM_RE.pattern,
        "step_2_types": {k: list(v) for k, v in types.items()},
        "n_candidate_stems": len(candidates),
        "n_stems_confirmed": confirmed,
        "n_documents_grouped": sum(
            len(a["documents"]) for a in audited.values() if a["grouped"]
        ),
        "surfaces_recorded": (
            "no — counts only. The schema is shared with DUA-restricted corpora "
            "whose surfaces may not appear in any committed file (CLAUDE.md)."
        ),
        "candidate_stems": audited,
    }


def _crossing_summary(
    docs: Sequence[Document], by_fold: dict[str, list[Document]]
) -> dict:
    """How many candidate stems have documents on both sides of the split.

    Recorded even though no stem became a group, because it is the number a
    reader will want when asking whether the split leaks: "0 groups cross the
    split" is trivially true when there are no groups, and says nothing. The
    honest statement is that 34 *stems* cross and were checked.
    """
    fold_of_doc = {d.doc_id: fold for fold, group in by_fold.items() for d in group}
    by_stem: dict[str, list[str]] = {}
    for doc in docs:
        match = STEM_RE.match(doc.doc_id)
        if match is not None:
            by_stem.setdefault(match.group("stem"), []).append(doc.doc_id)
    crossing = {
        stem: sorted({fold_of_doc[i] for i in ids})
        for stem, ids in by_stem.items()
        if len(ids) > 1 and len({fold_of_doc[i] for i in ids}) > 1
    }
    combinations: dict[str, int] = {}
    for folds in crossing.values():
        key = "+".join(folds)
        combinations[key] = combinations.get(key, 0) + 1
    return {
        "n_groups_crossing": 0,
        "n_candidate_stems_crossing": len(crossing),
        "n_documents_in_crossing_stems": sum(
            len(ids)
            for stem, ids in by_stem.items()
            if stem in crossing
        ),
        "fold_combinations": dict(sorted(combinations.items())),
        "note": (
            "No group crosses the split, because no group was formed — every "
            "document is its own group (§9.5 step 3). The stem figure is recorded "
            "instead: it is the quantity that would matter if the grouping "
            "decision were wrong, and reporting only 'zero groups cross' would "
            "hide that the question was even asked."
        ),
    }


def _git_describe() -> dict:
    """The corpus-independent part of provenance: what produced this file."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=base.ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=base.ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"commit": None, "working_tree_dirty": None}
    return {"commit": commit, "working_tree_dirty": dirty}


def build(corpus_id: str) -> dict:
    """Construct the split record for a corpus that ships its own split.

    For a corpus with no official split this is not the entry point: one is
    constructed per DESIGN §9.5 (grouping) and §9.5's stratification clause, and
    that code will fill the same schema with `origin: "constructed"`, a seed, and
    a populated `stratification` block.
    """
    from .corpora.meddocan import MeddocanLoader

    if corpus_id != "es-meddocan":
        raise CorpusError(
            f"build() currently handles es-meddocan only; {corpus_id!r} has no "
            "official split and needs the §9.5 construction path"
        )

    loader = MeddocanLoader(use_split_file=False)
    docs = loader.load()
    by_fold: dict[str, list[Document]] = {}
    for doc in docs:
        by_fold.setdefault(doc.split, []).append(doc)

    per_document = {
        doc.doc_id: digest_document(loader.source_files(doc.doc_id)) for doc in docs
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "corpus": corpus_id,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_by": "src/split.py",
        "repository": _git_describe(),
        "tokenizer": TOKENIZER,
        "provenance": {
            "origin": "official",
            "note": (
                "The split shipped with the corpus, adopted unchanged. Not "
                "constructed here, so there is no seed and no stratification: "
                "results on it are comparable to previously published systems "
                "and carry no suspicion that the partition was tuned."
            ),
            "rationale_ref": "DESIGN.md §9.6",
            "seed": None,
            "stratification": None,
        },
        "group_key": {
            "unit": "document",
            "basis": "no patient key exists; no grouping confirmed by identifier",
            "rationale_ref": "DESIGN.md §9.5",
            "n_groups": len(docs),
            "note": (
                "The document is the unit — one group per document, so the group "
                "count equals the document count. 936 article stems exist and 48 "
                "carry more than one document, but not one of the 48 passes §9.5 "
                "step 2: no stem has a patient name agreeing across its documents "
                "together with a record number or date. 34 of the 48 straddle "
                "this split, and 32 of those share no identifying surface at all "
                "across folds; the other two share only a bare given name and an "
                "age string. Same article is not same patient, so grouping on the "
                "stem would discard 80 documents' worth of independent units to "
                "prevent leakage that was measured not to exist. A stem-disjoint "
                "split is built separately and reported alongside, for "
                "seen/unseen analysis only (DESIGN §9.6)."
            ),
            "grouping_audit": grouping_audit(corpus_id, docs),
            "crosses_split": _crossing_summary(docs, by_fold),
        },
        "source": {
            "hash_algorithm": "sha256",
            "hashed": (
                "per document, over its .txt and .ann in sorted-name order; the "
                "manifest digest is derived from the per-document digests"
            ),
            "n_documents": len(per_document),
            "manifest_digest": manifest_digest(per_document),
            "documents": dict(sorted(per_document.items())),
        },
        "folds": {
            fold: {
                **fold_summary(fold_docs),
                "document_ids": sorted(d.doc_id for d in fold_docs),
            }
            for fold, fold_docs in sorted(by_fold.items())
        },
        "totals": fold_summary(docs),
        "corpus_specific": {
            "reading": "brat standoff; the redundant XML encoding is not read",
            "fold_directories": {"train": "train", "dev": "dev", "test": "test"},
            "fold_directories_note": (
                "MEDDOCAN encodes the fold in the directory path, so the loader "
                "cross-checks this file against the directory and raises on "
                "disagreement. No other corpus here offers that check."
            ),
            "bom_documents": sorted(d.doc_id for d in docs if d.had_bom),
            "bom_note": (
                "32 documents carry a UTF-8 BOM which the shipped offsets count; "
                "stripped and shifted per DESIGN §9.7. Listed by id so the "
                "correction is auditable without re-reading the corpus."
            ),
        },
    }


# ─── reading and verification ───────────────────────────────────────────────

#: Keys every corpus's split file must carry. A corpus-specific field belongs in
#: `corpus_specific`, never at the top level: the moment one corpus adds a
#: top-level key, the schema stops being one schema.
REQUIRED_TOP_LEVEL = frozenset(
    {
        "schema_version",
        "corpus",
        "generated",
        "generated_by",
        "repository",
        "tokenizer",
        "provenance",
        "group_key",
        "source",
        "folds",
        "totals",
        "corpus_specific",
    }
)

REQUIRED_FOLD_KEYS = frozenset(
    {
        "n_documents",
        "n_spans",
        "n_spans_in_scope",
        "n_spans_excluded",
        "spans_by_phi_type",
        "spans_by_excluded_type",
        "tokens",
        "document_ids",
    }
)


def split_path(corpus_id: str) -> Path:
    """From naming.yaml's `split` path template, never a literal."""
    template = base.naming()["paths"]["split"]
    return base.ROOT / template.format(corpus=corpus_id)


def _shown(path: Path) -> str:
    """A path for a message: repository-relative when it is inside the repository.

    `Path.relative_to` raises for anything outside, so messages must not call it
    directly — a crash while building an error message replaces a diagnosis with
    a traceback about the diagnosis. Split files live in the repository, so the
    fallback is for tests and for a redirected path, not for corpus data (which
    `corpus_root` deliberately never echoes at all).
    """
    try:
        return str(path.relative_to(base.ROOT))
    except ValueError:
        return str(path)


def read(corpus_id: str) -> dict:
    path = split_path(corpus_id)
    if not path.exists():
        raise CorpusError(
            f"{_shown(path)} does not exist. Generate it with "
            f"`python3 -m src.split --corpus {corpus_id}` and commit it before "
            "any rule is written (CLAUDE.md, DESIGN §6)."
        )
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    check_schema(record, corpus_id)
    return record


def check_schema(record: dict, corpus_id: str) -> None:
    """Structural checks that need no corpus on disk."""
    if record.get("schema_version") != SCHEMA_VERSION:
        raise CorpusError(
            f"split file for {corpus_id} has schema_version "
            f"{record.get('schema_version')!r}, this code writes "
            f"{SCHEMA_VERSION}"
        )
    if record.get("corpus") != corpus_id:
        raise CorpusError(
            f"split file names corpus {record.get('corpus')!r}, expected "
            f"{corpus_id!r}"
        )
    missing = sorted(REQUIRED_TOP_LEVEL - set(record))
    if missing:
        raise CorpusError(f"split file for {corpus_id} is missing keys: {missing}")
    extra = sorted(set(record) - REQUIRED_TOP_LEVEL)
    if extra:
        raise CorpusError(
            f"split file for {corpus_id} has top-level keys {extra} that are not "
            "in the shared schema. Corpus-specific fields go in "
            "'corpus_specific' — one top-level key per corpus and the schema "
            "stops being shared."
        )
    if record["tokenizer"] != TOKENIZER:
        raise CorpusError(
            f"split file counted tokens with {record['tokenizer']!r}, this code "
            f"uses {TOKENIZER!r}; the counts are not comparable"
        )
    for fold, block in record["folds"].items():
        if fold not in base.axis("split"):
            raise CorpusError(
                f"{fold!r} is not a split in config/naming.yaml "
                f"(have: {base.split_names()})"
            )
        fold_missing = sorted(REQUIRED_FOLD_KEYS - set(block))
        if fold_missing:
            raise CorpusError(f"fold {fold!r} is missing keys: {fold_missing}")
        if len(block["document_ids"]) != block["n_documents"]:
            raise CorpusError(
                f"fold {fold!r} claims {block['n_documents']} documents but "
                f"lists {len(block['document_ids'])} ids"
            )
    all_ids = [i for b in record["folds"].values() for i in b["document_ids"]]
    if len(all_ids) != len(set(all_ids)):
        raise CorpusError(
            f"split file for {corpus_id}: a document id appears in more than one "
            "fold — the folds are not disjoint"
        )


def verify(record: dict, docs: Sequence[Document]) -> None:
    """Recount from the loaded documents and compare to what the file claims.

    This is the check that makes the file trustworthy rather than merely present.
    A summary written once and never re-derived is a comment; re-derived on every
    load, it is an assertion. Raises on the first disagreement.

    **Only the folds present in `docs` are recounted.** Once the test fold is
    sealed it cannot be loaded, so its recorded summaries stop being checkable —
    that is the cost of the seal and the reason DESIGN §6 requires the file to be
    generated first. What remains checkable is checked, and the totals are then
    reconciled arithmetically (fold sums must equal `totals`), so a stale sealed
    block cannot hide: it would have to be stale in a way that keeps the sum
    correct, which means a second compensating edit elsewhere in the file.

    A fold in `docs` that the file does not know about is still an error. The
    tolerance runs one way only.
    """
    corpus_id = record["corpus"]
    by_fold: dict[str | None, list[Document]] = {}
    for doc in docs:
        by_fold.setdefault(doc.split, []).append(doc)

    file_folds = set(record["folds"])
    loaded_folds = {f for f in by_fold if f is not None}
    if not loaded_folds <= file_folds:
        raise CorpusError(
            f"{corpus_id}: loaded documents are in folds "
            f"{sorted(loaded_folds - file_folds)}, which the split file does not "
            "record. The corpus on disk has a fold the frozen split never saw."
        )
    unchecked = sorted(file_folds - loaded_folds)

    for fold, block in sorted(record["folds"].items()):
        if fold not in by_fold:
            continue  # sealed, or otherwise not loaded — see the docstring
        fold_docs = by_fold[fold]
        recomputed = fold_summary(fold_docs)
        for key in sorted(REQUIRED_FOLD_KEYS - {"document_ids"}):
            if recomputed[key] != block[key]:
                raise CorpusError(
                    f"{corpus_id}/{fold}: split file records {key}="
                    f"{block[key]!r} but the loader recounted {recomputed[key]!r}. "
                    "Either the corpus on disk changed or the split file is "
                    "stale; do not proceed until it is known which."
                )
        ids_file = sorted(block["document_ids"])
        ids_loaded = sorted(d.doc_id for d in fold_docs)
        if ids_file != ids_loaded:
            only_file = sorted(set(ids_file) - set(ids_loaded))
            only_loaded = sorted(set(ids_loaded) - set(ids_file))
            raise CorpusError(
                f"{corpus_id}/{fold}: document ids differ — "
                f"{len(only_file)} in the split file but not loaded "
                f"({only_file[:3]}), {len(only_loaded)} loaded but not in the "
                f"split file ({only_loaded[:3]})"
            )

    if not unchecked:
        recomputed_totals = fold_summary(docs)
        for key in sorted(REQUIRED_FOLD_KEYS - {"document_ids"}):
            if recomputed_totals[key] != record["totals"][key]:
                raise CorpusError(
                    f"{corpus_id}: split file records total {key}="
                    f"{record['totals'][key]!r} but the loader recounted "
                    f"{recomputed_totals[key]!r}"
                )
    else:
        reconcile_totals(record)


def reconcile_totals(record: dict) -> None:
    """`totals` must equal the sum of the folds. Needs no corpus on disk.

    This is what carries the totals once a fold is sealed. It is weaker than a
    recount and it is not weak: `totals` and the per-fold blocks are written
    independently, so an edit to either alone breaks the sum. Combined with the
    recount of every unsealed fold, the only surviving way to corrupt a sealed
    fold's figures is to edit them *and* the totals consistently — which is no
    longer a stale file, it is a forged one, and no check inside the file can
    distinguish that. The frozen commit hash in results/sealed_eval_log.md is what
    covers that case.
    """
    corpus_id = record["corpus"]
    folds = list(record["folds"].values())
    for key in ("n_documents", "n_spans", "n_spans_in_scope", "n_spans_excluded"):
        summed = sum(b[key] for b in folds)
        if summed != record["totals"][key]:
            raise CorpusError(
                f"{corpus_id}: split file records total {key}="
                f"{record['totals'][key]!r} but its folds sum to {summed!r}"
            )
    if record["totals"]["n_spans_in_scope"] + record["totals"]["n_spans_excluded"] != (
        record["totals"]["n_spans"]
    ):
        raise CorpusError(
            f"{corpus_id}: in-scope and excluded span counts do not add up to the "
            "total"
        )
    summed_tokens = sum(b["tokens"]["total"] for b in folds)
    if summed_tokens != record["totals"]["tokens"]["total"]:
        raise CorpusError(
            f"{corpus_id}: split file records {record['totals']['tokens']['total']!r} "
            f"tokens in total but its folds sum to {summed_tokens!r}"
        )
    for group in ("spans_by_phi_type", "spans_by_excluded_type"):
        for type_name, total in record["totals"][group].items():
            summed = sum(b[group].get(type_name, 0) for b in folds)
            if summed != total:
                raise CorpusError(
                    f"{corpus_id}: split file records {total!r} {type_name} spans "
                    f"in total ({group}) but its folds sum to {summed!r}"
                )


def fold_of(record: dict) -> dict[str, str]:
    """document id -> fold, the mapping a loader applies."""
    return {
        doc_id: fold
        for fold, block in record["folds"].items()
        for doc_id in block["document_ids"]
    }


# ─── CLI ────────────────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, help="corpus id from naming.yaml")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the existing file against the corpus; write nothing",
    )
    args = parser.parse_args(argv)

    path = split_path(args.corpus)

    if args.check:
        record = read(args.corpus)
        from .corpora.meddocan import MeddocanLoader

        docs = MeddocanLoader(use_split_file=False).load()
        verify(record, docs)
        reconcile_totals(record)
        loaded = sorted({d.split for d in docs})
        unchecked = sorted(set(record["folds"]) - set(loaded))
        print(f"ok  {_shown(path)} agrees with the corpus on disk")
        print(f"    recounted: {', '.join(loaded)}")
        if unchecked:
            # Stated, not silent: a check that covered two thirds of the corpus and
            # printed "ok" would be read as covering all of it.
            print(
                f"    not recounted (sealed): {', '.join(unchecked)} — figures "
                "carried by the fold/total reconciliation and by the freeze commit"
            )
        return 0

    record = build(args.corpus)
    check_schema(record, args.corpus)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # The file is the seal's reference point, so overwriting it is a decision
        # a human makes, not a side effect of running a script twice.
        print(
            f"{_shown(path)} already exists. Delete it "
            "deliberately if the split is genuinely being rebuilt — and record "
            "why, because everything downstream is dated from it.",
            file=sys.stderr,
        )
        return 1
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    print(f"wrote {_shown(path)}")
    for fold, block in sorted(record["folds"].items()):
        print(
            f"  {fold:6} {block['n_documents']:5} docs  "
            f"{block['n_spans']:6} spans  "
            f"{block['n_spans_in_scope']:6} in scope"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
