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
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import yaml

from .corpora import base
from .corpora.base import CorpusError, Document

SCHEMA_VERSION = 1

#: Numeric parameters of a *constructed* split. `config/sampling.yaml`'s argument for
#: living outside naming.yaml applies unchanged: these are values, not vocabulary.
SPLIT_CONFIG = base.ROOT / "config" / "split.yaml"


@lru_cache(maxsize=1)
def split_config() -> dict:
    """The contents of config/split.yaml."""
    with open(SPLIT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def construction_params(corpus_id: str) -> dict:
    """The seed and stratification for one corpus, or an error naming the file.

    A corpus with no entry is not defaulted. A default seed would make the split
    reproducible from code that does not record it, which is the one thing
    CLAUDE.md's "fix the seed in config and record it with the results" rules out.
    """
    entry = (split_config().get("corpora") or {}).get(corpus_id)
    if entry is None:
        raise CorpusError(
            f"config/split.yaml has no entry for {corpus_id!r}, so there is no seed "
            "and no stratification to construct a split with. Add one there rather "
            "than defaulting here — a seed that lives in code is a seed the results "
            "do not record (CLAUDE.md)."
        )
    missing = sorted({"proportions", "stratify_by", "n_strata", "seed"} - set(entry))
    if missing:
        raise CorpusError(
            f"config/split.yaml's {corpus_id!r} entry is missing {missing}"
        )
    folds = set(entry["proportions"])
    if folds != set(base.split_names()):
        raise CorpusError(
            f"config/split.yaml's {corpus_id!r} proportions cover {sorted(folds)} "
            f"but naming.yaml's split axis is {base.split_names()}"
        )
    total = sum(entry["proportions"].values())
    if abs(total - 1.0) > 1e-9:
        raise CorpusError(
            f"config/split.yaml's {corpus_id!r} proportions sum to {total!r}, not 1"
        )
    return entry

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


def digest_material(parts: Sequence[tuple[str, bytes]]) -> str:
    """One digest per document, over its named byte parts in sorted-name order.

    Per document rather than per file because the document is the unit the folds
    are made of: when a re-release changes three files, what matters is whether
    those documents are in the sealed fold.

    Named *parts* rather than files because `en-deid` has no file per document — all
    2,434 of its records live inside three files, and its `digest_parts()` supplies the
    record's own bytes instead. One hashing function for both, so that two corpora's
    digests cannot come to mean different things; `digest_document` below is this
    function applied to files, which is what the two frozen split files recorded.
    """
    sha = hashlib.sha256()
    for name, payload in sorted(parts, key=lambda part: part[0]):
        sha.update(name.encode("utf-8"))
        sha.update(b"\0")
        sha.update(payload)
    return sha.hexdigest()


def digest_document(paths: Sequence[Path]) -> str:
    """`digest_material` over a document's files, named by filename.

    Kept as its own name because it is what `tests/test_split_file.py` recomputes
    against `splits/es-meddocan.json`: the digests in the two frozen files were
    produced by this and must keep verifying byte for byte.
    """
    return digest_material([(path.name, path.read_bytes()) for path in paths])


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


def _n_documents_with_gold(docs: Sequence[Document]) -> int:
    """How many documents carry at least one *scored* span.

    `in_scope_spans` rather than `spans`, which is the difference between this figure and
    what the two narratives that record it say about themselves: a §9.1-excluded span is kept
    and flagged and is nobody's gold, so a note whose only span is excluded is as PHI-free to
    a scorer as a note with no span at all. On `en-deid` the two readings give the same 735
    because nothing there is excluded; on `ko-surro` they give 725 and 727, and the 727 the
    first reading produced was published beside prose that said "at least one in-scope span".
    Shared by both narratives so the pair's sparsity figures cannot drift apart in definition
    — the corpora are the same records and the numbers get compared.
    """
    return sum(1 for doc in docs if doc.in_scope_spans)


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
    },
    "de-grascco": {
        # The patient's name only. `NAME_DOCTOR` agreeing across two documents says
        # the same clinician wrote them, which is not what step 2 asks.
        "name": ("NAME_PATIENT",),
        # One ID subtype here where MEDDOCAN has three, and it is wider than a
        # record number — ward codes are annotated `ID` too. That can only ever
        # *confirm* a stem step 2 already requires a name agreement for, so the
        # width costs nothing; it is recorded because a reader of the audit
        # counts will otherwise read "record" as "record number".
        "record": ("ID",),
        # Birth date only, not `DATE`. Two letters about different patients from
        # the same clinic share their letter dates routinely.
        "date": ("DATE_BIRTH",),
    },
}

#: `{stem}{sep}{suffix}`, suffix digits **or** letters, stem opaque. DESIGN §9.5
#: step 1 — both past bugs here assumed a numeric suffix.
STEM_RE = re.compile(r"^(?P<stem>.+)[-_](?P<suffix>[0-9]+|[A-Za-z]+)$")

#: Corpora where every document id must parse as `{stem}{sep}{suffix}`, so that an
#: id that does not is a bug in the pattern rather than a fact about the corpus.
#:
#: MEDDOCAN's ids are all `{journal}-{n}` and a miss there means step 1 silently
#: skipped documents — which is exactly how the earlier digits-only rule dropped 31.
#: GraSCCo's are patient or disease words (`Kolkhorst.txt`, `Baastrup.txt`) and 47 of
#: 63 contain no separator at all: those ids are not unparsed, they are unstructured,
#: and step 3 is the rule for them. The distinction is declared per corpus rather
#: than inferred from how many ids failed, because "most of them parsed" is precisely
#: the signal a broken pattern also produces.
ALL_IDS_STRUCTURED = frozenset({"es-meddocan"})

#: `d/m/y`, `d.m.y`, `d-m-y`, two- or four-digit year. DESIGN §9.5 step 2 requires
#: dates to be compared after format normalisation: `Tupolev_1..4`'s one birth date
#: ships as `21/06/1967`, `21/06/1967`, `21.06.67` and `21.06.1967`, so the raw
#: intersection across the four documents is empty and the only §9.5 group in three
#: corpora would fail to form.
DATE_RE = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})$")


def normalise_date(surface: str) -> str | None:
    """A comparable form of a date surface, or `None` if it is not one of these.

    Two-digit years are compared as two digits rather than expanded: expanding
    needs a century rule, a century rule needs a cutoff, and a cutoff invented here
    would decide whether `21.06.67` is 1967 or 2067 on no evidence. Nothing here
    needs to know the century — the question is only whether two surfaces denote the
    same day.
    """
    match = DATE_RE.match(surface.strip())
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    return f"{day:02d}-{month:02d}-{year % 100:02d}"


def comparable_surfaces(
    spans, subtypes: Sequence[str], *, as_dates: bool
) -> set[str]:
    """The surface set one document contributes to a step-2 comparison.

    `as_dates` is the `date` role and nothing else, because §9.5 normalises dates and
    nothing else. Widening it to every role would compare a record number that
    happens to be date-shaped against a date, which is a different rule from the one
    written down.

    Raw surfaces **and** their normalised date forms, unioned rather than replaced.
    Union because normalisation can then only add agreement, never remove it: a pair
    that agreed as written still agrees, so adding this cannot dissolve a group a
    previous split file recorded. Measured on the frozen `splits/es-meddocan.json`
    before it was added: over the 30 candidate stems still recountable after the
    seal, 0 change their `n_shared_surfaces.date`, 0 change their step-2 decision,
    and every recorded count still matches. The 18 stems that touch the sealed fold
    cannot be recounted, and do not need to be: all 48 are recorded `grouped: false`,
    union-only agreement can only turn a non-group into a group, and §9.5 records
    that 32 of the 34 straddling stems share **no** identifying surface across folds
    while the other two share a bare given name and an age string — none of which is
    a date, normalised or not.
    """
    out: set[str] = set()
    for span in spans:
        if span.subtype not in subtypes:
            continue
        surface = span.surface.strip()
        out.add(surface)
        if not as_dates:
            continue
        normalised = normalise_date(surface)
        if normalised is not None:
            out.add(normalised)
    return out


def stem_index(docs: Sequence[Document]) -> tuple[dict[str, list[str]], list[str]]:
    """DESIGN §9.5 step 1: document ids grouped by stem, and the ids with no stem.

    One implementation for the audit, the crossing summary and the construction of a
    split, because three callers applying the same pattern separately is how one of
    them ends up applying a different one.
    """
    by_stem: dict[str, list[str]] = {}
    unparsed: list[str] = []
    for doc in docs:
        match = STEM_RE.match(doc.doc_id)
        if match is None:
            unparsed.append(doc.doc_id)
            continue
        by_stem.setdefault(match.group("stem"), []).append(doc.doc_id)
    return {stem: sorted(ids) for stem, ids in by_stem.items()}, sorted(unparsed)


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
    by_stem, unparsed = stem_index(docs)
    if unparsed and corpus_id in ALL_IDS_STRUCTURED:
        raise CorpusError(
            f"{corpus_id}: {len(unparsed)} document ids do not parse as "
            f"stem+suffix (first: {unparsed[:3]}). The §9.5 step-1 pattern has to "
            "cover every id or the grouping is silently partial — this is exactly "
            "how the earlier digits-only rule dropped 31 ids."
        )

    candidates = {s: v for s, v in by_stem.items() if len(v) > 1}
    audited = {}
    confirmed = 0
    for stem, ids in sorted(candidates.items()):
        shared = {}
        for role, subtypes in types.items():
            per_doc = [
                comparable_surfaces(by_id[i].spans, subtypes, as_dates=role == "date")
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


def group_units(corpus_id: str, docs: Sequence[Document], audit: dict) -> list[list[str]]:
    """The §9.5 units: each a sorted list of document ids, ordered by first id.

    Derived from the audit rather than recomputed beside it, so the units a split is
    built from are by construction the units the split file's audit explains. A
    second traversal of the same rule is how the record and the partition come to
    disagree about `Tupolev_1..4`.
    """
    grouped_ids = {
        doc_id
        for entry in audit["candidate_stems"].values()
        if entry["grouped"]
        for doc_id in entry["documents"]
    }
    units = [
        sorted(entry["documents"])
        for entry in audit["candidate_stems"].values()
        if entry["grouped"]
    ]
    units += [[d.doc_id] for d in docs if d.doc_id not in grouped_ids]
    units.sort(key=lambda ids: ids[0])
    flat = [doc_id for unit in units for doc_id in unit]
    if sorted(flat) != sorted(d.doc_id for d in docs):
        raise CorpusError(
            f"{corpus_id}: the §9.5 units cover {len(flat)} document ids and the "
            f"corpus has {len(docs)} documents. A unit list that is not a partition "
            "of the corpus would silently drop or duplicate documents in the split."
        )
    return units


def patient_key_audit(
    corpus_id: str, docs: Sequence[Document], loader: base.CorpusLoader
) -> dict:
    """DESIGN §9.5's *first* branch: the corpus ships a patient key, so group on it.

    Steps 1–3 — the stem pattern, the identifying-surface comparison, one group per
    document — are the fallback for a corpus with no key, and they do not run here. That
    is recorded rather than left implicit: "patient-disjoint" and "no surface agreed, so
    each document stood alone" are different claims that would otherwise produce
    audit blocks distinguishable only by which keys happen to be present.

    The key is read through `loader.patient_key`, which raises rather than falling back
    to the document id, so a loader that declares a key and does not supply one fails
    here instead of producing a document-random split labelled patient-disjoint.

    No surface is compared and none is recorded, so this block is safe for a
    DUA-restricted corpus for the same reason `grouping_audit`'s is — and here trivially,
    since the key is the release's own record field.
    """
    if not loader.has_patient_key:
        raise CorpusError(
            f"{corpus_id}: patient_key_audit called for a loader that declares no "
            "patient key. The branch is chosen from that declaration (DESIGN §9.5)."
        )
    if not loader.patient_key_source:
        raise CorpusError(
            f"{corpus_id}: the loader declares a patient key but no "
            "`patient_key_source`, so the audit could not say what was grouped on. A "
            "split file claiming patient-disjoint folds without naming the key is not "
            "auditable (DESIGN §9.5 step 4)."
        )

    by_key: dict[str, list[str]] = {}
    for doc in docs:
        by_key.setdefault(loader.patient_key(doc), []).append(doc.doc_id)
    per_patient = [len(ids) for ids in by_key.values()]

    return {
        "rule_ref": "DESIGN.md §9.5",
        "branch": "patient key (§9.5 first branch)",
        "key_source": loader.patient_key_source,
        "n_patients": len(by_key),
        "n_documents": len(docs),
        "documents_per_patient": {
            **_percentiles(per_patient),
            "mean": round(len(docs) / len(by_key), 2),
        },
        "n_patients_with_one_document": sum(1 for n in per_patient if n == 1),
        "identifying_surface_rule": (
            "did not run. §9.5 steps 1–3 are the fallback for a corpus with no patient "
            "key; this corpus has one, so the stem pattern and the surface comparison "
            "are not consulted and the fields grouping_audit records for them "
            "(step_1_pattern, step_2_types, candidate_stems) are absent rather than "
            "empty. An empty candidate_stems here would read as 'the rule ran and "
            "found nothing'."
        ),
        "surfaces_recorded": (
            "no, and none was read: the key is the release's own record field, not an "
            "identifier recovered from the text (CLAUDE.md)."
        ),
        "patients": {key: sorted(ids) for key, ids in sorted(by_key.items())},
    }


def patient_key_units(
    corpus_id: str, docs: Sequence[Document], audit: dict
) -> list[list[str]]:
    """The §9.5 units under the patient-key branch: one unit per patient.

    Read back out of the audit for `group_units`' reason — the partition a split is
    built from is by construction the partition its audit explains.
    """
    units = [sorted(ids) for ids in audit["patients"].values()]
    units.sort(key=lambda ids: ids[0])
    flat = [doc_id for unit in units for doc_id in unit]
    if sorted(flat) != sorted(d.doc_id for d in docs):
        raise CorpusError(
            f"{corpus_id}: the patient units cover {len(flat)} document ids and the "
            f"corpus has {len(docs)} documents. A unit list that is not a partition of "
            "the corpus would silently drop or duplicate documents in the split."
        )
    return units


def grouping(
    corpus_id: str, docs: Sequence[Document], loader: base.CorpusLoader
) -> tuple[dict, list[list[str]]]:
    """DESIGN §9.5's two branches, chosen from the loader's declaration.

    One dispatch point, so that "which rule grouped this corpus" is answered once for
    the audit and the units together. A caller that picked the branch itself would be a
    second place where a corpus with a key could be grouped without it.
    """
    if loader.has_patient_key:
        audit = patient_key_audit(corpus_id, docs, loader)
        return audit, patient_key_units(corpus_id, docs, audit)
    audit = grouping_audit(corpus_id, docs)
    return audit, group_units(corpus_id, docs, audit)


def _crossing_summary(
    docs: Sequence[Document],
    by_fold: dict[str, list[Document]],
    units: Sequence[Sequence[str]],
    *,
    branch: str = "surface",
) -> dict:
    """How many groups, and how many candidate stems, straddle the split.

    Both, not either. The group figure is the one that matters and it is vacuous on
    its own for a corpus where every group is one document: "0 groups cross the
    split" is trivially true when there are no multi-document groups, and says
    nothing. The honest statement for MEDDOCAN is that 34 *stems* cross and were
    checked; for a corpus with a real group, the group figure carries it and the
    stem figure still records that the rejected stems were considered.
    """
    fold_of_doc = {d.doc_id: fold for fold, group in by_fold.items() for d in group}
    groups_crossing = sum(
        1 for unit in units if len({fold_of_doc[i] for i in unit}) > 1
    )
    if groups_crossing:
        raise CorpusError(
            f"{groups_crossing} §9.5 groups have documents in more than one fold. "
            "The split is assigned per group, so this cannot happen by accident — "
            "do not freeze this file."
        )
    by_stem, _ = stem_index(docs)
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
        # No key is added for the multi-document group count: it is `group_key`'s
        # `n_groups` against `source.n_documents`, and a second copy of a derivable
        # number is a second thing that can go stale. `splits/es-meddocan.json` was
        # frozen against this schema and is not regenerated (it is the seal's
        # reference point), so a key added here would exist in the code and not in
        # the one committed file — invisible until a reader compares them.
        "note": (
            "No patient crosses the split: folds are assigned per §9.5 unit and a "
            "unit is a patient, so this is a property of the assignment. The stem "
            "figures below are computed from the id pattern for schema uniformity and "
            "carry no decision here — step 1 never ran (§9.5's first branch), and "
            "where a stem coincides with a patient it is the patient figure that is "
            "authoritative."
            if branch == "patient"
            else "No group crosses the split, because no group was formed — every "
            "document is its own group (§9.5 step 3). The stem figure is recorded "
            "instead: it is the quantity that would matter if the grouping "
            "decision were wrong, and reporting only 'zero groups cross' would "
            "hide that the question was even asked."
            if all(len(unit) == 1 for unit in units)
            else (
                "No group crosses the split, and here that is a property of the "
                "assignment rather than of the absence of groups: folds are "
                "assigned per §9.5 unit, so a multi-document group cannot "
                "straddle them. The stem figure counts the stems step 2 rejected "
                "and which therefore may straddle — that is the intended outcome "
                "for them, not a leak."
            )
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


def loader_without_split_file(corpus_id: str) -> base.CorpusLoader:
    """The corpus's loader, resolved through the one registry, reading no split file.

    Building and checking a split file must not read it — building because it does
    not exist yet, checking because a file that supplied the folds it is then
    compared against would verify nothing. `base.loader_for` does not take the flag,
    so the class comes from the registry and is instantiated here: the point of
    routing through it is that "which loader reads this corpus" keeps one answer
    (DESIGN §3, and `loader_for`'s own docstring).
    """
    return type(base.loader_for(corpus_id))(use_split_file=False)


def docs_for_check(corpus_id: str) -> tuple[list[Document], str]:
    """The documents `--check` recounts against the file, and where their folds came from.

    Two cases, and the difference is what the check can prove:

      - **The layout encodes the fold** (MEDDOCAN's `train/`, `dev/`, `test/`). The
        split file is not read, so the fold assignment itself is verified against an
        independent source.
      - **The layout does not** (GraSCCo: one flat directory). There is no second
        source, so the folds are taken from the file being checked and only the
        *counts* are re-derived — span totals, per-type counts, token distributions,
        which ids are in which fold. That is most of the file and it is not all of it,
        which is why the caller prints where the folds came from instead of printing
        the same "ok" for both cases. Loading with no folds at all would be worse than
        either: every fold would land in `verify`'s not-loaded branch and the check
        would silently degrade to the arithmetic reconciliation.
    """
    loader_class = type(base.loader_for(corpus_id))
    if loader_class.fold_dirs:
        return loader_class(use_split_file=False).load(), "the corpus directory layout"
    return loader_class().load(), "this split file (the layout encodes no fold)"


def _record(
    corpus_id: str,
    docs: Sequence[Document],
    by_fold: dict[str, list[Document]],
    per_document: dict[str, str],
    *,
    hashed: str,
    provenance: dict,
    group_key: dict,
    corpus_specific: dict,
) -> dict:
    """The shared schema, assembled in one place for every corpus.

    The official and constructed routes differ in three blocks — `provenance`,
    `group_key`, `corpus_specific` — and in nothing else. Assembling the rest twice
    is how the second corpus's file comes to carry a subtly different schema from
    the first while both pass `check_schema`.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "corpus": corpus_id,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_by": "src/split.py",
        "repository": _git_describe(),
        "tokenizer": TOKENIZER,
        "provenance": provenance,
        "group_key": group_key,
        "source": {
            "hash_algorithm": "sha256",
            "hashed": hashed,
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
        "corpus_specific": corpus_specific,
    }


#: Which route each corpus's split file takes. Declared, not inferred from whether
#: `config/split.yaml` has an entry: a corpus whose entry was forgotten would then
#: silently be treated as shipping an official split, and MEDDOCAN's shipped folds
#: would become something this code claims to have constructed.
SPLIT_ORIGIN = {
    "es-meddocan": "official",
    "de-grascco": "constructed",
    # The release ships no split. It does ship a patient key, so this is the first
    # corpus whose units are patients rather than §9.5 steps 1–3's groups. ko-surro's
    # split is derived from this one, not built beside it (DESIGN §6.5).
    "en-deid": "constructed",
    # A third route, and it samples nothing (DESIGN §6.5 option B, §9.6). ko-surro is the
    # Korean surrogate of en-deid's own records, so a note's fold role is already decided
    # by splits/en-deid.json and choosing again here — with any seed — could put a note in
    # one corpus's dev fold and its surrogate in the other's test fold. Only the fold
    # *contents* are measured here; membership is `tools/derive_aligned_split.py`'s.
    "ko-surro": "derived",
}


def build(corpus_id: str) -> dict:
    """The split record for one corpus, by whichever route DESIGN §9.6 gives it."""
    origin = SPLIT_ORIGIN.get(corpus_id)
    if origin is None:
        raise CorpusError(
            f"{corpus_id!r} has no split route declared in src/split.py's "
            f"SPLIT_ORIGIN (have: {sorted(SPLIT_ORIGIN)}). Whether a corpus ships a "
            "split is a fact about the corpus and a decision in DESIGN §9.6; write "
            "it down there and here before generating a file."
        )
    if origin == "official":
        return _build_official(corpus_id)
    if origin == "derived":
        return _build_derived(corpus_id)
    return _build_constructed(corpus_id)


def _build_official(corpus_id: str) -> dict:
    """The split record for a corpus that ships its own split."""
    if corpus_id != "es-meddocan":
        raise CorpusError(
            f"{corpus_id!r} is declared as shipping an official split but this "
            "function reads MEDDOCAN's fold directories, which no other corpus has"
        )

    loader = loader_without_split_file(corpus_id)
    docs = loader.load()
    by_fold: dict[str, list[Document]] = {}
    for doc in docs:
        by_fold.setdefault(doc.split, []).append(doc)

    # Through `digest_parts` like the constructed route, not `source_files` directly:
    # the default hook is byte-identical to the earlier call, so the digests in the
    # frozen `splits/es-meddocan.json` still verify, and there is one path to change.
    per_document = {
        doc.doc_id: digest_material(loader.digest_parts(doc)) for doc in docs
    }
    audit = grouping_audit(corpus_id, docs)
    units = group_units(corpus_id, docs, audit)

    return _record(
        corpus_id,
        docs,
        by_fold,
        per_document,
        hashed=(
            "per document, over its .txt and .ann in sorted-name order; the "
            "manifest digest is derived from the per-document digests"
        ),
        provenance={
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
        group_key={
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
            "grouping_audit": audit,
            "crosses_split": _crossing_summary(docs, by_fold, units),
        },
        corpus_specific={
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
    )


# ─── constructing a split ───────────────────────────────────────────────────


def _strata(
    units: Sequence[Sequence[str]], sizes: dict[str, int], n_strata: int
) -> list[list[Sequence[str]]]:
    """Units bucketed into `n_strata` bands of equal *unit count* by span count.

    Equal count per band, by rank, rather than equal-width bands of the span count:
    the distribution here is long-tailed (5 to 61 in-scope spans per document, median
    17), and equal-width bands on a long tail put almost every unit in the first band
    and then stratify nothing.

    Ties on the span count are broken by unit id so that the bands do not depend on
    the order the corpus was read in. That is the whole reason a deterministic seed is
    not enough on its own: `random.shuffle` is reproducible, and a list whose order
    came from a directory listing is not.
    """
    ordered = sorted(units, key=lambda unit: (sum(sizes[i] for i in unit), unit[0]))
    bands: list[list[Sequence[str]]] = [[] for _ in range(n_strata)]
    for position, unit in enumerate(ordered):
        bands[min(n_strata - 1, position * n_strata // len(ordered))].append(unit)
    return bands


def assign_folds(
    units: Sequence[Sequence[str]],
    sizes: dict[str, int],
    *,
    proportions: dict[str, float],
    n_strata: int,
    seed: int,
) -> dict[str, str]:
    """DESIGN §9.6's assignment rule: document id -> fold. Deterministic given `seed`.

    Targets are counted in documents and assignment happens per unit, which is the
    one place those two differ (a group of four documents is one unit). Each unit goes
    to the fold it leaves **least over its target share** — `(assigned + size) /
    target`, minimised — and not to the fold currently furthest below target. The
    difference is exactly the multi-document group: the deficit rule offered
    `Tupolev_1..4` to whichever fold had the largest absolute shortfall, which was the
    12.6-document test fold, and four documents into it made test 15 — breaking the
    12–13 the §9.6 bound commits to, by arithmetic rather than by decision. Measured,
    not reasoned about: the deficit rule was run first and produced exactly that.

    Ties break in naming.yaml's fold order, which is stable because it is read from
    the config rather than from a set.
    """
    n_documents = sum(len(unit) for unit in units)
    targets = {fold: proportions[fold] * n_documents for fold in base.split_names()}
    assigned: dict[str, int] = {fold: 0 for fold in targets}
    fold_of_doc: dict[str, str] = {}
    rng = random.Random(seed)
    for band in _strata(units, sizes, n_strata):
        shuffled = list(band)
        rng.shuffle(shuffled)
        # Largest unit first, ties in the shuffled order (`sort` is stable). A group
        # of four assigned late can only be placed badly — every fold is nearly full
        # by then, and the least-bad choice still overshoots by three. Placed first it
        # is absorbed and the singletons balance the folds around it: measured on this
        # corpus, 34/17/12 documents became 32/19/12 against targets of 31.5/18.9/12.6.
        shuffled.sort(key=lambda unit: -len(unit))
        for unit in shuffled:
            fold = min(
                base.split_names(),
                key=lambda f: (
                    (assigned[f] + len(unit)) / targets[f],
                    base.split_names().index(f),
                ),
            )
            assigned[fold] += len(unit)
            for doc_id in unit:
                fold_of_doc[doc_id] = fold
    return fold_of_doc


def _achieved(
    by_fold: dict[str, list[Document]],
    units: Sequence[Sequence[str]],
    fold_of_doc: dict[str, str],
    params: dict,
) -> dict:
    """What the stratification delivered, per fold — not what it was asked for.

    §9.5 requires this for a constructed split: "a reader can check what the
    stratification actually delivered instead of trusting that it was requested".
    Density is in spans per 1,000 whitespace tokens, the same token definition the
    fold summaries use, because two token definitions in one file is one too many.
    """
    return {
        "variable": params["stratify_by"],
        "n_strata": params["n_strata"],
        "target_proportions": dict(params["proportions"]),
        "note": (
            "The variable is the in-scope span count per unit, because the span count "
            "is the denominator of the leak rate and of every per-type figure. "
            "Spans per 1,000 tokens is recorded per fold and is *not* what was "
            "balanced: document lengths vary independently, so the folds differ in "
            "density even though their span shares match their document shares. That "
            "is a limitation of reading one fold's density against another's, stated "
            "here rather than left for a reader to infer from the word 'stratified'."
        ),
        "achieved": {
            fold: {
                "n_units": sum(
                    1 for unit in units if fold_of_doc[unit[0]] == fold
                ),
                "n_documents": len(docs),
                "proportion_of_documents": round(
                    len(docs) / sum(len(d) for d in by_fold.values()), 4
                ),
                "n_spans_in_scope": sum(len(d.in_scope_spans) for d in docs),
                "spans_in_scope_per_1000_tokens": round(
                    1000
                    * sum(len(d.in_scope_spans) for d in docs)
                    / sum(token_count(d.text) for d in docs),
                    2,
                ),
            }
            for fold, docs in sorted(by_fold.items())
        },
    }


def _build_constructed(corpus_id: str) -> dict:
    """The split record for a corpus that ships no split (DESIGN §9.5, §9.6).

    The grouping audit runs first and the units come from it, so the partition and
    the record of why it is shaped that way cannot disagree.
    """
    params = construction_params(corpus_id)
    loader = loader_without_split_file(corpus_id)
    docs = loader.load()
    if base.sealed_root(corpus_id) is not None:
        # A constructed split must be built from the whole corpus. After the seal the
        # test fold is not on disk under the corpus root, so this would silently
        # construct a split of the two remaining folds and hash 80% of the documents
        # — and then be committed as the file that defines the seal (§6.2).
        raise CorpusError(
            f"{corpus_id}: config/data_paths.local.yaml already declares a sealed "
            "root, so the corpus on disk is missing the sealed fold and a split "
            "constructed now would cover only part of it. The split file is built "
            "first and the seal follows from it (DESIGN §6.2)."
        )

    audit, units = grouping(corpus_id, docs, loader)
    sizes = {doc.doc_id: len(doc.in_scope_spans) for doc in docs}
    fold_of_doc = assign_folds(
        units,
        sizes,
        proportions=params["proportions"],
        n_strata=params["n_strata"],
        seed=params["seed"],
    )

    by_fold: dict[str, list[Document]] = {}
    for doc in docs:
        by_fold.setdefault(fold_of_doc[doc.doc_id], []).append(doc)
    per_document = {
        doc.doc_id: digest_material(loader.digest_parts(doc)) for doc in docs
    }

    narrative = CONSTRUCTED_NARRATIVE.get(corpus_id)
    if narrative is None:
        raise CorpusError(
            f"{corpus_id!r} is declared as constructed but src/split.py has no entry in "
            f"CONSTRUCTED_NARRATIVE (have: {sorted(CONSTRUCTED_NARRATIVE)}). The blocks "
            "a reader needs — what was hashed, what the unit is, how the corpus is read "
            "— are corpus facts and are written per corpus rather than defaulted, "
            "because a default would produce a file that describes the wrong corpus "
            "plausibly."
        )
    described = narrative(docs, units, loader)

    return _record(
        corpus_id,
        docs,
        by_fold,
        per_document,
        hashed=described["hashed"],
        provenance={
            "origin": "constructed",
            "note": described["origin_note"],
            "rationale_ref": "DESIGN.md §9.6",
            "seed": params["seed"],
            "stratification": _achieved(by_fold, units, fold_of_doc, params),
        },
        group_key={
            "unit": described["unit"],
            "basis": described["basis"],
            "rationale_ref": "DESIGN.md §9.5",
            "n_groups": len(units),
            "note": described["group_note"],
            "grouping_audit": audit,
            "crosses_split": _crossing_summary(
                docs, by_fold, units, branch=described["branch"]
            ),
        },
        corpus_specific=described["corpus_specific"],
    )


def _build_derived(corpus_id: str) -> dict:
    """The split record for a corpus whose fold membership another corpus already fixed.

    DESIGN §6.5 option B, §9.6. Two halves, and the separation is the whole point of the
    route: **membership** comes from `tools/derive_aligned_split.py` applied to the frozen
    `splits/en-deid.json`, and **contents** — documents, spans, types, tokens per fold — are
    measured here from the Korean corpus on disk. Nothing is sampled, so there is no seed and
    no stratification, and both are written as `null` rather than omitted (§9.6).

    The derivation is re-run here rather than read from a stored artefact. It is a pure
    function of the frozen split and the corpus's own ids, so re-running it costs nothing and
    a stored copy could disagree with the split file — which is the one failure this route
    exists to prevent.
    """
    if corpus_id != "ko-surro":
        raise CorpusError(
            f"{corpus_id!r} is declared as deriving its split but this function derives "
            "from splits/en-deid.json, which is a fact about one pair of corpora (DESIGN "
            "§6.5). A second derived corpus needs its own source declared before it can "
            "reuse this route."
        )
    # Imported here, not at module scope: `tools/` is the layer that calls `src/`, and a
    # top-level import would make `src.split` fail to load whenever a tool did not.
    from tools import derive_aligned_split as alignment

    loader = loader_without_split_file(corpus_id)
    if base.sealed_root(corpus_id) is not None:
        # Unlike the constructed route, membership would survive the seal — it comes from
        # another file. The *contents* would not: the loader reads only the unsealed root
        # here, so the test fold's spans and tokens would be measured from nothing while
        # every other block of the file looked complete.
        raise CorpusError(
            f"{corpus_id}: config/data_paths.local.yaml already declares a sealed root, "
            "so the test fold's text is not under the corpus root and its fold contents "
            "cannot be measured. Fold membership would still be derivable, which is "
            "exactly the danger: the file would carry a test block whose span and token "
            "counts came from an empty set. Build the split file before sealing "
            "(DESIGN §6.2)."
        )
    docs = loader.load()

    source = alignment.source_split()
    alignment.self_check(source)
    # The manifest `read_manifest` parses is for the command line. Here the same records are
    # already loaded, so the notes are built in memory from each document's own meta and
    # `line` carries the position in that list — there is no file for a line number to point
    # into, and the field exists so a refusal can be located.
    notes = [
        alignment.SourceNote(
            document=doc.doc_id,
            patient=doc.meta["patient_id"],
            note=doc.meta["note_index"],
            line=position,
        )
        for position, doc in enumerate(sorted(docs, key=lambda d: d.doc_id), 1)
    ]
    derivation = alignment.derive(source, notes)
    _check_derivation(corpus_id, docs, loader, derivation, source)

    by_fold: dict[str, list[Document]] = {}
    for doc in docs:
        by_fold.setdefault(derivation.assigned[doc.doc_id], []).append(doc)
    per_document = {
        doc.doc_id: digest_material(loader.digest_parts(doc)) for doc in docs
    }
    audit, units = grouping(corpus_id, docs, loader)

    narrative = DERIVED_NARRATIVE.get(corpus_id)
    if narrative is None:
        raise CorpusError(
            f"{corpus_id!r} is declared as derived but src/split.py has no entry in "
            f"DERIVED_NARRATIVE (have: {sorted(DERIVED_NARRATIVE)}). Same reason as the "
            "constructed route: the blocks a reader needs are corpus facts, and a default "
            "would describe the wrong corpus plausibly."
        )
    described = narrative(docs, units, loader, derivation)

    return _record(
        corpus_id,
        docs,
        by_fold,
        per_document,
        hashed=described["hashed"],
        provenance={
            "origin": "derived",
            "note": described["origin_note"],
            "rationale_ref": "DESIGN.md §6.5, §9.6",
            "derived_from": {
                "corpus": alignment.SOURCE_CORPUS,
                "derivation": "tools/derive_aligned_split.py",
                "source_manifest_digest": derivation.source_manifest_digest,
                "source_freeze_commit": derivation.source_commit,
                "note": (
                    "Fold membership is this corpus's source split applied per document, "
                    "not a second sampling. The digest and commit pin which sampling: a "
                    "resampling of the source corpus is a resampling of this one, "
                    "permanently, and a derivation made against an older freeze is "
                    "visible here rather than merely wrong."
                ),
            },
            # Written, not omitted. An absent seed reads as an oversight; `null` beside a
            # `derived` origin is the claim that no randomness entered, which is the
            # property that makes this route safe to pair with the source corpus (§9.6).
            "seed": None,
            "stratification": None,
        },
        group_key={
            "unit": described["unit"],
            "basis": described["basis"],
            "rationale_ref": "DESIGN.md §9.5",
            "n_groups": len(units),
            "note": described["group_note"],
            "grouping_audit": audit,
            "crosses_split": _crossing_summary(
                docs, by_fold, units, branch=described["branch"]
            ),
        },
        corpus_specific=described["corpus_specific"],
    )


def _check_derivation(
    corpus_id: str,
    docs: Sequence[Document],
    loader: base.CorpusLoader,
    derivation,
    source: dict,
) -> None:
    """The derivation placed every loaded document, and left out exactly the right ones.

    Four assertions, each failing for a different reason, and none of them derivable from
    the others. `derive` already raises when one patient's notes land in two folds; these
    are the properties of *this* corpus's side of the alignment.
    """
    if derivation.refused:
        # Every loaded document carries a reference, so its source note is one the frozen
        # split assigns. A refusal here means the two corpora's record sets disagree.
        by_reason = {
            name.split(".", 1)[1]: n
            for name, n in derivation.counts().items()
            if name.startswith("refused.") and n
        }
        raise CorpusError(
            f"{corpus_id}: the derivation refused {len(derivation.refused)} loaded "
            f"document(s), by reason {by_reason}. "
            "Every record this loader yields has a reference on the source side, so its "
            "source note is one splits/en-deid.json places; a refusal means the pair's "
            "record sets have drifted apart and the alignment is not the one DESIGN §6.5 "
            "adopted."
        )
    if derivation.unplaced:
        raise CorpusError(
            f"{corpus_id}: {len(derivation.unplaced)} document(s) have no fold and no "
            "patient that says which side of the seal their text belongs on"
        )
    if derivation.unclaimed:
        raise CorpusError(
            f"{corpus_id}: splits/en-deid.json assigns {len(derivation.unclaimed)} source "
            "note(s) that no document here claims. The pair is 2,434 of 2,434 by "
            "construction (DESIGN §6.5), so a missing document is a truncated corpus root "
            "— and it would produce a split file whose folds are each slightly short with "
            "nothing saying so."
        )
    uncovered = sorted(getattr(loader, "uncovered", []))
    expected = sorted(alignment_unassigned(source))
    if uncovered != expected:
        raise CorpusError(
            f"{corpus_id}: the loader reports {len(uncovered)} record(s) with no "
            f"reference and splits/en-deid.json lists {len(expected)}, and they are not "
            "the same records. The two corpora inherit one reference, so the set outside "
            "every fold has to be identical on both sides; a disagreement means one side "
            "dropped a record for a reason the other never recorded."
        )


def alignment_unassigned(source: dict) -> frozenset[str]:
    """The source split's own list of records in no fold, read through the derivation.

    A one-line wrapper so `_check_derivation` does not reach into the split file's
    `corpus_specific` itself: the derivation module owns the "which notes are unassigned,
    and for which recorded reason" question, including the cross-check that the list and
    its count agree.
    """
    from tools import derive_aligned_split as alignment

    return alignment.unassigned_notes(source)


# ─── what a constructed split file says about its corpus ────────────────────
#
# One function per corpus, returning the blocks `_record` cannot derive: what was
# hashed, what a unit is, how the corpus is read. Separated from `_build_constructed`
# because the machinery is shared and the prose is not — with both in one function the
# second corpus's file inherits the first's sentences, and a split file that says
# "the annotation file carries the text as sofaString" about a corpus with no
# annotation file is worse than one that says nothing.


def _grascco_narrative(
    docs: Sequence[Document], units: Sequence[Sequence[str]], loader: base.CorpusLoader
) -> dict:
    _, unstructured = stem_index(docs)
    grouped = [unit for unit in units if len(unit) > 1]
    return {
        "hashed": (
            "per document, over its CAS JSON annotation file and its .txt in "
            "sorted-name order; the manifest digest is derived from the "
            "per-document digests"
        ),
        "origin_note": (
            "The corpus ships no split, so this one was constructed here and is "
            "frozen before any German rule exists (DESIGN §6.2). Proportions are "
            "counted in documents and assigned per §9.5 unit; the seed below is "
            "config/split.yaml's and is the only source of randomness — no "
            "clock, no directory order, no process state enters the assignment."
        ),
        "branch": "surface",
        "unit": "§9.5 group (a confirmed same-patient cluster, else the document)",
        "basis": (
            "no patient key exists; one grouping confirmed by identifier agreement"
        ),
        "group_note": (
            f"{len(units)} units over {len(docs)} documents. "
            f"{len(grouped)} unit holds more than one document "
            f"({sorted(i for unit in grouped for i in unit)}): a patient name "
            "and a birth date agree across all of its documents, the only §9.5 "
            "group in three corpora. Its birth date ships in three formats, so "
            "the agreement is found after normalisation (§9.5 step 2) and the "
            "record number is shared by only three of the four documents — the "
            "intersection over all four is empty, which is why the rule asks for "
            "a name and *either* a record number or a date. The 11 Colon_Fake_* "
            "documents share a stem and are "
            "11 units: 11 distinct patient names, 11 distinct birth dates, no "
            "shared record number, so the stem marks a shared clinical scenario "
            "and not a shared patient. Folds are assigned to units, so no group "
            "straddles the split."
        ),
        "corpus_specific": {
            "reading": (
                "UIMA CAS JSON; the redundant CAS XMI encoding is not read. The "
                "annotation file carries the text as sofaString and the loader "
                "asserts it equals the .txt on every load."
            ),
            "fold_directories": None,
            "fold_directories_note": (
                "The layout encodes no fold: all documents live in one directory, so "
                "this file is the only authority on which fold a document is in and "
                "there is no second source to cross-check it against. MEDDOCAN's "
                "loader has that check and this one cannot."
            ),
            "n_ids_without_stem_suffix": len(unstructured),
            "ids_note": (
                f"{len(unstructured)} of {len(docs)} document ids contain no "
                "separator, so §9.5 step 1 never considers them and step 3 makes each "
                "its own group. That is a fact about the corpus's file naming, not a "
                "gap in the pattern — which is why the loader does not raise on it "
                "here and does for MEDDOCAN, where every id is {journal}-{n}."
            ),
            "bom_documents": sorted(d.doc_id for d in docs if d.had_bom),
            "bom_note": (
                "5 documents carry a UTF-8 BOM which the shipped offsets count; "
                "stripped and shifted per DESIGN §9.7. In 2 of them the first gold "
                "span starts at index 0, so the annotated surface begins with the "
                "BOM; those spans are clipped to 0 and listed per document in "
                "bom_clipped_spans below."
            ),
            "bom_clipped_spans": {
                doc.doc_id: doc.meta["bom_clipped_spans"]
                for doc in sorted(docs, key=lambda d: d.doc_id)
                if doc.meta.get("bom_clipped_spans")
            },
        },
    }


def _endeid_narrative(
    docs: Sequence[Document], units: Sequence[Sequence[str]], loader: base.CorpusLoader
) -> dict:
    if not hasattr(loader, "uncovered"):
        raise CorpusError(
            "en-deid: the loader did not report which records carry no reference. That "
            "list belongs in the split file — the records are excluded from every fold, "
            "and an exclusion no file records is one nobody can audit."
        )
    uncovered = sorted(loader.uncovered)
    with_spans = _n_documents_with_gold(docs)
    return {
        "hashed": (
            "per document, over the record's own bytes: its body from id.text and its "
            "own lines from id.deid and id-phi.phrase, named by source file and hashed "
            "in sorted-name order. **Not** over the three files, which are shared by "
            "every document and would give all of them the same digest — this corpus "
            "has no file per document, so the loader supplies the parts (digest_parts) "
            "and the hashing itself is the same function every corpus uses."
        ),
        "origin_note": (
            "The corpus ships no split, so this one was constructed here and is frozen "
            "before any rule or prompt has seen an English nursing note (DESIGN §6.2). "
            "Proportions are counted in documents and assigned per §9.5 unit — here a "
            "patient, so the fold sizes in documents can only approximate the requested "
            "proportions. The seed below is config/split.yaml's and is the only source "
            "of randomness. This is also the split ko-surro's is derived from and not "
            "the other way round (DESIGN §6.5): the two corpora are two halves of one "
            "release, and a note in en-deid's dev fold whose Korean surrogate sat in "
            "ko-surro's test fold would be a leak in whichever direction it was built."
        ),
        "branch": "patient",
        "unit": "patient (DESIGN §9.5's first branch — the corpus ships the key)",
        "basis": (
            "the release's own patient field in each record header; the "
            "identifying-surface rule is not consulted"
        ),
        "group_note": (
            f"{len(units)} patients over {len(docs)} notes, so the folds are "
            "patient-disjoint by construction rather than by a surface comparison. "
            "This is the first corpus here to take §9.5's first branch: MEDDOCAN and "
            "GraSCCo ship no patient identifier and fall through to steps 1–3. The "
            "distribution is heavily skewed — the largest patient carries a large "
            "fraction of a fold's worth of notes on its own — which is why assignment "
            "places the largest unit first (see assign_folds) and why the achieved "
            "document proportions are reported next to the requested ones rather than "
            "assumed to match."
        ),
        "corpus_specific": {
            "reading": (
                "Three files, 2,434 records: id.text carries the notes framed by "
                "START_OF_RECORD/END_OF_RECORD markers, id.deid carries per-record "
                "offsets relative to the body, and id-phi.phrase carries the type and "
                "the surface for the same spans. The two reference files are "
                "cross-checked span for span on every load and a disagreement raises "
                "rather than dropping a span. id.res — the release's own automatic "
                "de-identification and the basis of ko-surro — is not read: it is "
                "silver, and a split hashed over it would change if that file did."
            ),
            "fold_directories": None,
            "fold_directories_note": (
                "The layout encodes no fold, and cannot: a document is a record inside "
                "a file, not a file. This split file is the only authority on which "
                "fold a note is in, so the seal rewrites the three files rather than "
                "moving them (tools/prepare_endeid.py)."
            ),
            "n_records_without_reference": len(uncovered),
            "records_without_reference": uncovered,
            "records_without_reference_note": (
                f"{len(uncovered)} of {len(docs) + len(uncovered)} records have no "
                "id.deid header at all, so they carry no reference — not zero spans, "
                "but no statement either way. They are not loaded and so are in no "
                "fold: scoring a note with no reference as 'every prediction is a false "
                "positive' would be a measurement of the reference's absence. This is "
                "not §9.1's mechanism, which keeps a span and flags it; here there are "
                "no spans to keep (DESIGN §9.0, §9.1)."
            ),
            "n_documents_with_spans": with_spans,
            "sparsity_note": (
                f"{with_spans} of {len(docs)} loaded notes carry at least one gold "
                f"span; the other {len(docs) - with_spans} are genuinely PHI-free "
                "according to the reference. Recorded because it is the denominator of "
                "any per-note leak figure, and because a corpus where most notes have "
                "no PHI makes document-level precision look very different from "
                "span-level precision."
            ),
            "bom_documents": sorted(d.doc_id for d in docs if d.had_bom),
            "bom_note": (
                "None: a record is not a file, so there is no per-document byte-order "
                "mark to strip. Recorded as an empty list rather than omitted so that "
                "the §9.7 correction is visibly accounted for on every corpus."
            ),
        },
    }


CONSTRUCTED_NARRATIVE = {
    "de-grascco": _grascco_narrative,
    "en-deid": _endeid_narrative,
}


def _kosurro_narrative(
    docs: Sequence[Document],
    units: Sequence[Sequence[str]],
    loader: base.CorpusLoader,
    derivation,
) -> dict:
    if not hasattr(loader, "not_gold_supported"):
        raise CorpusError(
            "ko-surro: the loader did not report how many silver spans the human reference "
            "fails to support. That count is the size of this corpus's scoring decision "
            "(DESIGN §6.5 (v)) and it belongs in the split file — the spans are in the "
            "text and in no fold's gold, and a filter no file records is one nobody can "
            "audit."
        )
    uncovered = sorted(loader.uncovered)
    with_spans = _n_documents_with_gold(docs)
    return {
        "hashed": (
            "per document, over the record's own bytes: its Korean body, its loaded spans' "
            "offsets and source tags, and the number of spans the gold-support filter "
            "denied — named by part and hashed in sorted-name order. The denied spans "
            "enter as a count and not as offsets, because no measurement depends on where "
            "one was, and the count is there so that moving the filter changes the digest. "
            "**Not** over ko-surro.jsonl, which every document shares and which would give "
            "all 2,425 of them the same digest."
        ),
        "origin_note": (
            "Not sampled. Every fold assignment here is the fold splits/en-deid.json gives "
            "the source note this record's Korean text was derived from (DESIGN §6.5 "
            "option B): the two corpora are two halves of one release, and a note in "
            "en-deid's dev fold whose surrogate sat in ko-surro's test fold would be a leak "
            "in whichever direction it was read. What is measured here is only what a fold "
            "contains. The derivation is re-run at build time against the digest recorded "
            "above and every document it did not place, or placed without this corpus "
            "claiming it, refuses the build."
        ),
        "branch": "patient",
        "unit": "patient (DESIGN §9.5's first branch, inherited with the fold)",
        "basis": (
            "the source release's own patient field, carried through the derived root; the "
            "identifying-surface rule is not consulted, and the grouping is checked here "
            "rather than assumed — a patient whose notes landed in two folds refuses"
        ),
        "group_note": (
            f"{len(units)} patients over {len(docs)} notes, the same patients and the same "
            "partition as splits/en-deid.json. The group figure is recomputed from this "
            "corpus's own records rather than copied from the source file: copying it "
            "would make the disjointness a restatement of the source's claim, where "
            "recomputing it is a check that the derivation reproduced that claim here."
        ),
        "corpus_specific": {
            "reading": (
                "Two files under one derived root: ko-surro.jsonl holds 2,434 records — a "
                "Korean surrogate body per source note, with the silver spans' offsets, "
                "source tags and the per-span verdict of the gold-support filter — and "
                "reference.json holds that root's own counts, which the loader recounts "
                "and cross-checks on every load. The source placeholder literal and the "
                "surrogate value are not written into the derived root at all and the "
                "loader refuses a record that carries either: 30.5% of placeholder "
                "payloads are values rather than type names, so the literal is corpus text "
                "(docs/notes/ko-surro-gold-provenance.md §10.7)."
            ),
            "reference": "human-verified silver",
            "reference_note": (
                "The only one of the four corpora whose reference is not purely human "
                "(DESIGN §6.5 (v), §9.3). A Korean silver span is gold here iff the human "
                "reference of the source release's id.deid supports the English "
                "placeholder it was injected from, so the set has precision 1.000 against "
                "that reference by construction and recall 0.907 one-to-one. Leak rates on "
                "this corpus are on the same scale as the other three; the 3.3% of human "
                "gold the source tool never tagged is translated into the Korean text "
                "unmarked and is charged to any arm that finds it, which no filter can fix."
            ),
            "n_spans_not_gold_supported": loader.not_gold_supported,
            "not_gold_supported_note": (
                f"{loader.not_gold_supported} silver spans are in the Korean text and in "
                "no fold's gold: the human reference does not support the placeholder they "
                "were injected from. They are not loaded, so they are in no fold block "
                "above, and this is a third mechanism — §9.1's exclusion keeps a span and "
                "flags it, and en-deid's reference-less records drop a document. Recorded "
                "here because it is the difference between this file's denominators and "
                "the corpus as published (DESIGN §9.0)."
            ),
            "fold_directories": None,
            "fold_directories_note": (
                "The layout encodes no fold, and cannot: a document is a record inside a "
                "file, not a file. This split file is the only authority on which fold a "
                "note is in, so the seal rewrites the two files rather than moving them "
                "(tools/prepare_kosurro.py) — as on en-deid, whose records these are."
            ),
            "n_records_without_reference": len(uncovered),
            "records_without_reference": uncovered,
            "records_without_reference_note": (
                f"The same {len(uncovered)} records splits/en-deid.json leaves outside "
                "every fold, and the build refuses if the two lists differ: one reference "
                "serves both halves of the pair, so the set it says nothing about has to "
                "be identical on both sides. All of them carry 0 Korean silver spans, "
                "measured — but absence of coverage is not a claim of PHI-freeness, so "
                "they are in no fold on this side either."
            ),
            "n_documents_with_spans": with_spans,
            "sparsity_note": (
                f"{with_spans} of {len(docs)} loaded notes carry at least one in-scope "
                "span. Lower than en-deid's figure over the same records, and the "
                "gold-support filter is the whole reason: a note whose only silver spans "
                "were denied loads with an empty gold list. It is still a note the "
                "reference speaks about, which is what distinguishes it from the records "
                "above."
            ),
            "tokenizer_note": (
                "The shared whitespace tokenizer counts eojeol here and words on en-deid, "
                "so token counts and spans-per-1,000-tokens are not comparable across the "
                "pair even though the records correspond one to one (DESIGN §9.6). "
                "Document counts and span counts are."
            ),
            "bom_documents": sorted(d.doc_id for d in docs if d.had_bom),
            "bom_note": (
                "None: a record is not a file, so there is no per-document byte-order mark "
                "to strip. Recorded as an empty list rather than omitted so that the §9.7 "
                "correction is visibly accounted for on every corpus."
            ),
        },
    }


#: Separate from `CONSTRUCTED_NARRATIVE` because the signature differs — a derived
#: narrative is handed the derivation as well — and because the two routes must not be able
#: to borrow each other's entries: a constructed corpus described as inheriting its folds,
#: or a derived one described as seeded, would be a file that misstates how its own
#: partition was chosen.
DERIVED_NARRATIVE = {
    "ko-surro": _kosurro_narrative,
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
        docs, fold_source = docs_for_check(args.corpus)
        verify(record, docs)
        reconcile_totals(record)
        loaded = sorted(f for f in {d.split for d in docs} if f is not None)
        unchecked = sorted(set(record["folds"]) - set(loaded))
        print(f"ok  {_shown(path)} agrees with the corpus on disk")
        print(f"    recounted: {', '.join(loaded)}  (folds from {fold_source})")
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
