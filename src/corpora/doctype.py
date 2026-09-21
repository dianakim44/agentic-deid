"""Document-type labels, derived from a document's own text by configured cues.

DESIGN §7 measured GraSCCo's document-type distribution as eight content-cue labels and
recorded what follows from it: the note-type axis stands *within* GraSCCo alone, because
radiology, pathology and outpatient subsets hold everything except document type constant.
That is a reporting breakdown — `by_document_type` in `metrics.json` — and a secondary
analysis rather than a headline (DESIGN §7, CLAUDE.md's headline rule is unaffected).

**Why this is derived at scoring time instead of stored on the corpus.** GraSCCo ships no
document-type field, so the label is a measurement over the text. The 12 documents of
de-grascco's test fold are sealed and cannot be labelled from here — so a label table
written by hand today would be a table with twelve holes, and the holes would be filled by
whoever next ran the sealed evaluation. Deriving from text means `run_sealed_eval` computes
the sealed fold's labels inside the read it is already authorised to make, with the same
cues at the same version, and nothing outside that module ever needs the twelve names.

**The vocabulary and the derivation are two files.** `config/naming.yaml` holds the label
names (`base.document_types()`); `config/document_types.yaml` holds each corpus's cues and
a version. The version lands in the run block, because a cue edit changes what a label
means while leaving the label's name identical — the sort of drift a results directory
cannot show.

**No corpus text enters an exception here** (CLAUDE.md). The cues are config and are named
freely; a document is named by its `doc_id`, and nothing in this module has a reason to
quote what a cue matched.

    from src.corpora import doctype
    doctype.labels("de-grascco", text)          # ('outpatient', 'radiology')
    doctype.label_documents("de-grascco", docs) # {doc_id: (label, ...)}
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable, Mapping, Sequence

import yaml

from .base import ROOT, CorpusError, corpus_ids, document_types

#: The cue file. Beside `naming.yaml` rather than under `rules/`: these are not detection
#: rules, they produce no span, and a `rules/` entry would be loaded by `load_for_corpus`.
CUES = ROOT / "config" / "document_types.yaml"

#: What the run block records as the source of a `by_document_type` breakdown. Repository
#: relative, so a published record names no home directory.
SOURCE = str(CUES.relative_to(ROOT))


class DocTypeError(CorpusError):
    """A document-type cue file that cannot be used as written.

    A `CorpusError` because the causes are the same kind: a config that says something the
    code cannot act on, whose right response is to stop. It has its own type so a caller
    can tell "this corpus declares no document types" — which is `None`, not an error —
    from "this corpus declares them and the declaration is broken".
    """


@lru_cache(maxsize=1)
def _config() -> dict:
    """`config/document_types.yaml`, parsed and checked as a whole.

    Checked here rather than at each accessor: `version` and `corpora` are the two keys
    every caller depends on, and a file missing one of them is broken for all of them.
    """
    if not CUES.exists():
        raise DocTypeError(
            f"{SOURCE} is missing. It holds the cue patterns that derive a document-type "
            "label from a document's text (DESIGN §7), and its `version` is written into "
            "every run block that reports a breakdown — so an absent file would make the "
            "breakdown unattributable rather than merely unavailable."
        )
    with open(CUES, encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    if not isinstance(loaded, dict):
        raise DocTypeError(f"{SOURCE} does not parse as a mapping.")
    version = loaded.get("version")
    if not isinstance(version, int) or version < 1:
        raise DocTypeError(
            f"{SOURCE} has no integer `version`. The version is what tells two metrics "
            "files apart when the same label was derived by two different cue sets, and "
            "that difference is invisible in the label name."
        )
    corpora = loaded.get("corpora")
    if not isinstance(corpora, dict) or not corpora:
        raise DocTypeError(
            f"{SOURCE} has no `corpora` mapping. A corpus with no document-type axis is "
            "recorded by having no entry under it, which is a different statement from "
            "the file having no such block at all."
        )
    unknown = sorted(set(corpora) - set(corpus_ids()))
    if unknown:
        raise DocTypeError(
            f"{SOURCE} declares cues for {unknown}, which config/naming.yaml does not "
            "declare as a corpus. Cues for a corpus that does not exist are cues nothing "
            "runs, and the name would still be written into a results file if one did."
        )
    return loaded


def version() -> int:
    """The cue file's version, as recorded in a run block beside `SOURCE`."""
    return int(_config()["version"])


@lru_cache(maxsize=None)
def cues(corpus: str) -> tuple[tuple[str, tuple[re.Pattern, ...]], ...] | None:
    """One corpus's cues as `((label, (compiled, ...)), ...)`, or `None` if it declares none.

    A tuple of pairs and not a dict, so the order is the config's order and every table
    built from it comes out in the same order — the labels are in descending corpus-wide
    frequency in `naming.yaml`, which is the order DESIGN §7 prints them in.

    **`None` is a real answer and not an error.** es-meddocan's document-type distribution
    has not been measured, so it declares no cues and its metrics carry no breakdown; an
    empty breakdown would read as "measured and found nothing".

    **The label set must be exactly the declared vocabulary.** A corpus that listed six of
    the eight would produce a table in which the two missing labels are indistinguishable
    from labels whose cues matched no document — "we did not ask" and "we asked and the
    answer was none" are different facts, and only the second is a measurement. A label that
    genuinely cannot occur in a corpus still gets its cues and a count of zero.
    """
    entry = _config()["corpora"].get(corpus)
    if entry is None:
        return None
    if not isinstance(entry, dict) or not entry:
        raise DocTypeError(
            f"{SOURCE}: the entry for {corpus} is empty. A corpus with no document-type "
            "axis has no entry at all — an empty one would be scored as eight labels that "
            "match nothing."
        )
    declared = document_types()
    missing = [label for label in declared if label not in entry]
    extra = sorted(set(entry) - set(declared))
    if extra:
        raise DocTypeError(
            f"{SOURCE}: {corpus} declares cues for {extra}, which is not a "
            "`document_type` value in config/naming.yaml (have: "
            f"{sorted(declared)}). Add the label to the vocabulary first — a label only "
            "this file knows would land in metrics.json naming nothing."
        )
    if missing:
        raise DocTypeError(
            f"{SOURCE}: {corpus} declares no cues for {missing}. Every declared label is "
            "asked of every corpus that has the axis, because a label omitted here reads "
            "in the breakdown exactly like a label whose cues matched no document, and "
            "those are different facts. A label that cannot occur in this corpus keeps its "
            "cues and gets a count of zero."
        )
    compiled: list[tuple[str, tuple[re.Pattern, ...]]] = []
    for label in declared:
        patterns = entry[label]
        if not isinstance(patterns, list) or not patterns:
            raise DocTypeError(
                f"{SOURCE}: {corpus}/{label} has no list of cue patterns. A label with an "
                "empty cue list can never match, which is a silent way of dropping the "
                "label from the breakdown."
            )
        here: list[re.Pattern] = []
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                raise DocTypeError(
                    f"{SOURCE}: {corpus}/{label} has an empty or non-string cue pattern."
                )
            try:
                here.append(re.compile(pattern))
            except re.error as exc:
                # The pattern is config and is named; `exc` describes the pattern, not any
                # document. Nothing here has read a corpus yet.
                raise DocTypeError(
                    f"{SOURCE}: {corpus}/{label} cue {pattern!r} is not a regular "
                    f"expression ({exc})."
                ) from exc
        compiled.append((label, tuple(here)))
    return tuple(compiled)


def has_axis(corpus: str) -> bool:
    """Whether this corpus has a document-type axis at all. Cheap and side-effect free."""
    return cues(corpus) is not None


def labels(corpus: str, text: str) -> tuple[str, ...]:
    """Every label whose cues appear in `text`, in the vocabulary's order.

    Multi-label: a document can return several, and an empty tuple is the ordinary state
    for a document none of the cues reach (11 of GraSCCo's 63 carry no letter frame, and
    the content cues are not a partition either — DESIGN §7).

    Case-sensitive, because the cues are German nouns and uppercase abbreviations and a
    case-folded `\\bCT\\b` matches word fragments. `config/document_types.yaml` states this
    and spells the all-caps header forms as their own patterns.

    Raises `DocTypeError` if the corpus declares no cues. Callers that do not know ask
    `has_axis()` first — returning `()` here would make "no axis" and "no label" one value.
    """
    entry = cues(corpus)
    if entry is None:
        raise DocTypeError(
            f"{corpus} declares no document-type cues in {SOURCE}, so it has no "
            "document-type axis. `()` is not the answer: it is what a corpus *with* the "
            "axis returns for a document no cue reached, and the two cannot share a value."
        )
    return tuple(label for label, patterns in entry
                 if any(p.search(text) for p in patterns))


def label_documents(corpus: str, docs: Iterable) -> dict[str, tuple[str, ...]] | None:
    """`{doc_id: (label, ...)}` for every document, or `None` if the corpus has no axis.

    Takes the loader's `Document` objects and reads `doc_id` and `text` off them. `None`
    rather than `{}` for a corpus without the axis, for `cues()`'s reason: an empty mapping
    is what a fold of zero documents would give.

    The mapping it returns is the only thing that crosses into the scorer, and it carries
    document ids and labels — no text, no offsets. That is what lets `scorer.score()` stay
    a function of spans (see its docstring) while reporting a breakdown over documents.
    """
    if not has_axis(corpus):
        return None
    labelled: dict[str, tuple[str, ...]] = {}
    for doc in docs:
        if doc.doc_id in labelled:
            raise DocTypeError(
                f"{corpus}: two documents share the id {doc.doc_id!r}. The breakdown is "
                "keyed by id, so a duplicate would silently take one document's labels "
                "for the other's."
            )
        labelled[doc.doc_id] = labels(corpus, doc.text)
    return labelled


def distribution(labelled: Mapping[str, Sequence[str]]) -> dict[str, int]:
    """Document count per label, plus `unlabelled`, from a `label_documents()` result.

    Every declared label appears, including the ones with a count of zero, for `cues()`'s
    reason. The counts do not sum to the number of documents — the labels overlap and some
    documents carry none — and `unlabelled` is kept under its own key rather than as a
    ninth label, per `base.document_types()`.

    Used by the notes and by the reporting layer; the scorer builds its own richer block
    because it also has gold and predictions to put beside the count.
    """
    counts = {label: 0 for label in document_types()}
    unlabelled = 0
    for these in labelled.values():
        if not these:
            unlabelled += 1
        for label in these:
            counts[label] = counts.get(label, 0) + 1
    return {**counts, "unlabelled": unlabelled}
