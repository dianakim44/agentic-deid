"""Tests for the GraSCCo loader.

Same two kinds as `tests/test_meddocan_loader.py`, and for the same reason — the
expected counts are what DESIGN §9.0 reports and the paper will print:

  - **Recount tests** re-derive the totals from the shipped bytes by a method that
    shares no code with the loader: a regex over the raw JSON text rather than
    `json.load` plus the loader's feature-structure walk.
  - **Constant tests** pin the numbers in DESIGN.md, so a re-released corpus fails
    here instead of quietly moving every published figure.

**What is different here, and it is the whole reason this file is not a copy.** brat
records the surface beside the offsets, so MEDDOCAN's loader can compare them and
these tests can check that comparison. A CAS records offsets only, so slicing them
and calling the result the surface proves nothing. The two checks that replace it are
asserted directly below: `sofaString` against the `.txt` (63 of 63), and the measured
invariant that no gold span begins or ends on whitespace (1,436 of 1,436).

**These tests see 51 documents, not 63.** The test fold was sealed on 2026-09-21, after
the split file was written and its per-document hashes recorded (DESIGN §6.2), so the
same division the MEDDOCAN file makes applies here:

  - The recount over raw CAS JSON covers train+dev. It stays an independent check of the
    loader over what the loader can reach; extending it over the sealed fold would be a
    check of the sealed fold, which no test may be.
  - The corpus-wide figures DESIGN §9.0 reports are `FULL_*` and come from
    `splits/de-grascco.json`, which is frozen. `test_the_split_file_accounts_for_the_seal`
    is what keeps them credible rather than merely unfalsifiable: the visible constants
    plus the split file's test fold must equal them, per type as well as in total.

Three of the five BOM documents went to the test fold, so `BOM_DOCS` here is two names
and §9.7's second clipped span is no longer reachable. That is recorded rather than
worked around — a corpus fact about the sealed fold, held by the split file.

    python3 -m pytest tests/test_grascco_loader.py -q
"""
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.corpora import CorpusError, base  # noqa: E402
from src.corpora.grascco import (  # noqa: E402
    ANNOTATION_DIR,
    ANNOTATION_SUFFIX,
    EXCLUDED_TYPES,
    TEXT_DIR,
    TYPE_MAP,
    GrasccoLoader,
)

# ─── expected values (DESIGN §9.0, §9.1, §9.7) ──────────────────────────────

# What the loader can see: train+dev. Measured on 2026-09-21 with the seal in place,
# which is the only state these numbers describe.
N_DOCS = 51
N_SPANS = 1122
N_CANONICAL = 1015
N_EXCLUDED = 107
CANONICAL_COUNTS = {
    "NAME": 265,
    "DATE": 548,
    "AGE": 14,
    "LOCATION_AREA": 64,
    "LOCATION_STREET": 22,
    "ORGANISATION": 29,
    "CONTACT": 21,
    "ID": 51,
    "PROFESSION": 1,
}
EXCLUDED_COUNTS = {"NAME_TITLE": 107}
UNSEALED_SPLIT = {"train": 32, "dev": 19}
SPANS_BY_SPLIT = {"train": 669, "dev": 453}
IN_SCOPE_BY_SPLIT = {"train": 605, "dev": 410}

# The whole corpus, which is what DESIGN §9.0 reports and the paper prints. Read from
# the frozen split file rather than from the corpus: 12 documents are sealed, so a test
# that recomputed these would be reading the test fold to do it.
FULL_N_DOCS = 63
FULL_N_SPANS = 1436
FULL_N_CANONICAL = 1297
FULL_N_EXCLUDED = 139
FULL_CANONICAL_COUNTS = {
    "NAME": 324,
    "DATE": 693,
    "AGE": 19,
    "LOCATION_AREA": 99,
    "LOCATION_STREET": 36,
    "ORGANISATION": 37,
    "CONTACT": 28,
    "ID": 59,
    "PROFESSION": 2,
}
CONSTRUCTED_SPLIT = {"train": 32, "dev": 19, "test": 12}

#: §9.7: five BOM documents corpus-wide, two of them reachable. `Boeck`,
#: `Dupuytren` and `Waldenström` are in the sealed fold, which is also where the
#: second of the two clipped spans went.
BOM_DOCS = ["Baastrup", "Stölzl"]
BOM_CLIPPED = {"Baastrup": [0]}
FULL_BOM_DOCS = 5
FULL_BOM_CLIPPED = 2

BOM = "﻿"


# ─── fixtures ───────────────────────────────────────────────────────────────
# `grascco_loader` / `grascco_unsplit_loader` / `grascco_present` are in
# tests/conftest.py and are defined nowhere else. A local availability check here is
# the defect that shipped four times; see that file's header.


@pytest.fixture(scope="module")
def docs(grascco_unsplit_loader):
    return grascco_unsplit_loader.load()


@pytest.fixture(scope="module")
def annotation_paths(grascco_unsplit_loader):
    """Every reachable annotation file, found without the loader's iteration.

    Through `source_roots()` rather than a literal path, so this cannot reach a
    sealed root the loader would refuse to open.
    """
    paths = []
    for root in grascco_unsplit_loader.source_roots():
        paths.extend(sorted((root / ANNOTATION_DIR).glob(f"*{ANNOTATION_SUFFIX}")))
    return paths


@pytest.fixture(scope="module")
def recount(annotation_paths):
    """Re-derive the totals from the raw JSON text, with no JSON parser at all.

    A regex over each file's bytes counting `webanno.custom.PHI` feature structures
    and their `kind` values. Shares no code with `_read_cas`, so a bug in one cannot
    hide in the other. The pattern is anchored on the two keys appearing in the order
    the exporter writes them, and the test below asserts the totals it produces — a
    regex that silently matched nothing would fail there rather than pass here.
    """
    phi_re = re.compile(
        r'\{\s*"%ID":\s*\d+,\s*"%TYPE":\s*"webanno\.custom\.PHI",'
        r'\s*"kind":\s*"(?P<kind>[A-Z_]+)"'
    )
    out = {"docs": 0, "spans": 0, "by_subtype": {}, "bom_docs": 0}
    for path in annotation_paths:
        out["docs"] += 1
        raw = path.read_text(encoding="utf-8")
        text_path = path.parent.parent.parent / TEXT_DIR / (
            path.name[: -len(ANNOTATION_SUFFIX)] + ".txt"
        )
        if text_path.read_bytes().startswith(b"\xef\xbb\xbf"):
            out["bom_docs"] += 1
        for match in phi_re.finditer(raw):
            kind = match.group("kind")
            out["spans"] += 1
            out["by_subtype"][kind] = out["by_subtype"].get(kind, 0) + 1
    return out


# ─── the loader agrees with an independent recount ──────────────────────────


def test_loader_matches_independent_recount(docs, recount):
    assert len(docs) == recount["docs"]
    assert base.count_spans(docs) == recount["spans"]
    assert base.count_by_type(docs, canonical=False) == recount["by_subtype"]


def test_the_recount_saw_the_corpus(recount):
    """The regex above must have matched, or every comparison to it is vacuous.

    Against the unsealed figures, because `annotation_paths` goes through
    `source_roots()` and an ordinary read reaches train+dev only. A recount that
    produced 63 documents here would mean the seal is not holding, which is why this is
    an equality and not a lower bound.
    """
    assert recount["docs"] == N_DOCS
    assert recount["spans"] == N_SPANS
    assert recount["bom_docs"] == len(BOM_DOCS)


# ─── the counts DESIGN §9.0 reports ─────────────────────────────────────────


def test_document_count(docs):
    assert len(docs) == N_DOCS


def test_document_ids_are_unique(docs):
    ids = [d.doc_id for d in docs]
    assert len(set(ids)) == len(ids)


def test_total_span_count(docs):
    assert base.count_spans(docs) == N_SPANS


def test_canonical_span_count(docs):
    assert sum(len(d.in_scope_spans) for d in docs) == N_CANONICAL


def test_the_three_totals_reconcile(docs):
    in_scope = sum(len(d.in_scope_spans) for d in docs)
    excluded = base.count_spans(docs) - in_scope
    assert in_scope == N_CANONICAL
    assert excluded == N_EXCLUDED
    assert in_scope + excluded == N_SPANS


def test_canonical_type_counts_match_design_table(docs):
    assert base.count_by_type(docs) == CANONICAL_COUNTS


def test_excluded_type_counts_match_design_table(docs):
    counts: dict[str, int] = {}
    for doc in docs:
        for span in doc.spans:
            if span.excluded:
                counts[span.subtype] = counts.get(span.subtype, 0) + 1
    assert counts == EXCLUDED_COUNTS


def test_every_mapped_source_type_occurs(docs):
    """All 19 mapped types plus the excluded one are present, so the map is exercised.

    A mapping entry for a type the corpus does not contain is untested wiring, and a
    type the corpus contains with no entry raises — so this asserts the two sets are
    the same set rather than merely compatible.
    """
    present = set(base.count_by_type(docs, canonical=False))
    assert present == set(TYPE_MAP) | set(EXCLUDED_TYPES)


def test_no_type_maps_to_other(docs):
    """§9.0: GraSCCo ships no residual bucket, and OTHER must not be invented for it.

    MEDDOCAN's `OTHER` holds 15 spans of a type that genuinely has no counterpart.
    Mapping any German type there would put spans in the leak-rate denominator that
    the corpus never marked as residual (§9.4).
    """
    assert "OTHER" not in set(TYPE_MAP.values())
    assert "OTHER" not in base.count_by_type(docs)


# ─── the two checks that replace brat's surface comparison ───────────────────


def test_every_span_slices_back_to_its_surface(docs):
    """What `load()` already asserts, asserted again where a reader can see it.

    Circular for this corpus *on its own* — the surface was sliced out of the same
    text — which is exactly why the two tests below exist. It still catches a span
    whose offsets were shifted after construction.
    """
    for doc in docs:
        doc.assert_offsets()


def test_the_sofa_string_equals_the_plain_text_file(annotation_paths):
    """51 of 51 reachable documents, re-derived here without the loader.

    It was 63 of 63 when it was first measured, on 2026-09-21 before the seal. The
    sealed twelve are not re-checked here and cannot be: `source_roots()` does not hand
    them out, and the check is not a reason to ask it to.

    This is what licenses applying CAS offsets to the `.txt`: the offsets index the
    sofaString, and the document text is read from the file. If the two ever diverge,
    every offset in that document is unverifiable rather than merely suspect.
    """
    checked = 0
    for path in annotation_paths:
        cas = json.loads(path.read_text(encoding="utf-8"))
        sofas = [
            fs
            for fs in cas["%FEATURE_STRUCTURES"]
            if fs.get("%TYPE") == "uima.cas.Sofa"
        ]
        assert len(sofas) == 1, path.name
        text_path = path.parent.parent.parent / TEXT_DIR / (
            path.name[: -len(ANNOTATION_SUFFIX)] + ".txt"
        )
        assert sofas[0]["sofaString"] == text_path.read_text(encoding="utf-8"), (
            f"{path.name}: the CAS text and the .txt differ"
        )
        checked += 1
    assert checked == N_DOCS


def test_no_gold_span_is_whitespace_edged(docs):
    """The invariant that stands in for a surface comparison.

    Measured 1,436 of 1,436 on the whole corpus before the seal; 1,122 of them are
    reachable now, and the count of spans actually examined is asserted so that a
    fixture returning nothing cannot pass this as an absence.

    A one-character offset slip is the most likely offset error and the most likely
    to break this. Reported as a count so the message quotes no text.
    """
    examined = 0
    edged = 0
    for doc in docs:
        for span in doc.spans:
            examined += 1
            if span.surface != span.surface.strip():
                edged += 1
    assert examined == N_SPANS
    assert edged == 0


# ─── BOM (DESIGN §9.7) ──────────────────────────────────────────────────────


def test_bom_documents_are_found_and_flagged(docs):
    """Two reachable, and the other three named by the split file rather than by nothing.

    The corpus fact is five, which is what §9.7 reports; three of the five are sealed.
    Asserting only the reachable two would leave the sealed three unrecorded anywhere a
    reader of this file can see, and §9.7's figure would look wrong.
    """
    record = json.loads(
        (Path(ROOT) / "splits" / "de-grascco.json").read_text(encoding="utf-8")
    )
    sealed_facts = record["corpus_specific"]
    assert len(sealed_facts["bom_documents"]) == FULL_BOM_DOCS
    assert len(sealed_facts["bom_clipped_spans"]) == FULL_BOM_CLIPPED
    assert set(BOM_DOCS) <= set(sealed_facts["bom_documents"])
    assert sorted(d.doc_id for d in docs if d.had_bom) == BOM_DOCS


def test_no_loaded_text_starts_with_a_bom(docs):
    for doc in docs:
        assert not doc.text.startswith(BOM)


def test_the_span_inside_the_bom_is_clipped_and_recorded(docs):
    """§9.7's decision, as a fact about the loaded documents.

    Two documents had this — `Baastrup` and `Dupuytren` — and `Dupuytren` is sealed, so
    one is reachable. In both, the first gold span begins at index 0, so the annotated
    extent includes the byte-order mark. The span is clipped to 0 and keeps its shifted
    end — it loses exactly the BOM — and the clip is recorded per document so that the
    cases stay countable rather than becoming an invisible property of the loader.
    """
    clipped = {
        d.doc_id: d.meta["bom_clipped_spans"]
        for d in docs
        if d.meta.get("bom_clipped_spans")
    }
    assert clipped == BOM_CLIPPED
    for doc_id in BOM_CLIPPED:
        doc = next(d for d in docs if d.doc_id == doc_id)
        assert doc.spans[0].start == 0
        assert not doc.spans[0].surface.startswith(BOM)


def test_utf_8_sig_would_move_every_span_in_a_bom_document(grascco_unsplit_loader):
    """Why the loader reads plain utf-8 and shifts, rather than decoding the BOM away.

    `utf-8-sig` removes the BOM without telling the offsets, so every span in those
    five documents lands one character early. Demonstrated on the shipped bytes rather
    than asserted in prose: the whitespace-edge invariant is what catches it, and the
    count of spans it would move is the size of the error.
    """
    moved = 0
    for root in grascco_unsplit_loader.source_roots():
        for path in sorted((root / ANNOTATION_DIR).glob(f"*{ANNOTATION_SUFFIX}")):
            doc_id = path.name[: -len(ANNOTATION_SUFFIX)]
            if doc_id not in BOM_DOCS:
                continue
            text_path = root / TEXT_DIR / f"{doc_id}.txt"
            stripped = text_path.read_text(encoding="utf-8-sig")
            cas = json.loads(path.read_text(encoding="utf-8"))
            for fs in cas["%FEATURE_STRUCTURES"]:
                if fs.get("%TYPE") != "webanno.custom.PHI":
                    continue
                unshifted = stripped[fs["begin"]: fs["end"]]
                shifted = stripped[fs["begin"] - 1: fs["end"] - 1]
                if unshifted != shifted:
                    moved += 1
    assert moved > 0, "the BOM documents have no spans, so this proves nothing"


# ─── type map and classification ────────────────────────────────────────────


def test_mapped_and_excluded_are_disjoint():
    assert not set(TYPE_MAP) & set(EXCLUDED_TYPES)


def test_type_map_targets_are_all_canonical():
    assert set(TYPE_MAP.values()) <= set(base.canonical_types())


def test_unknown_annotation_type_raises(grascco_loader):
    with pytest.raises(CorpusError, match="neither mapped nor excluded"):
        grascco_loader.classify("KIND_DIE_ES_NICHT_GIBT")


def test_excluded_spans_are_kept_and_carry_no_canonical_type(docs):
    """§9.1: `NAME_TITLE` is reported as a limitation, so the spans must survive.

    9.68% of gold. Dropping them at load time would make the exclusion volume
    unrecoverable, and it is a number the paper prints.
    """
    excluded = [s for d in docs for s in d.spans if s.excluded]
    assert len(excluded) == N_EXCLUDED
    assert all(s.phi_type is None for s in excluded)
    assert {s.subtype for s in excluded} == set(EXCLUDED_TYPES)
    # Both percentages, because the reported limitation is about GraSCCo and not about
    # the part of GraSCCo that is still readable. 9.68% is the corpus-wide figure §9.1
    # prints; the reachable folds are slightly leaner.
    assert round(100 * FULL_N_EXCLUDED / FULL_N_SPANS, 2) == 9.68
    assert round(100 * len(excluded) / N_SPANS, 2) == 9.54


def test_gold_spans_have_empty_provenance(docs):
    """Gold is not a detection. A layer on a gold span would make it one (DESIGN §3)."""
    for doc in docs:
        for span in doc.spans:
            assert span.layer is None
            assert span.detector is None
            assert span.rule_id is None
            assert span.agent_actions == []


# ─── the split file is the only authority on the fold ───────────────────────


def test_the_layout_encodes_no_fold(docs):
    """`fold_dirs` is empty and every document loads with `split=None` without the file.

    The corpus fact this loader is built around: MEDDOCAN cross-checks the split file
    against the directory and this one has nothing to cross-check against, which is
    recorded in the split file's `corpus_specific` rather than left implicit.
    """
    assert GrasccoLoader.fold_dirs == {}
    assert {d.split for d in docs} == {None}


def test_calling_fold_roots_is_refused():
    """A corpus with no fold directories must not answer a question about them.

    The alternative — returning `{}` — would make `_read` iterate over nothing and
    load an empty corpus, which is the failure that looks like a clean run.
    """
    with pytest.raises(CorpusError, match="does not encode the fold"):
        GrasccoLoader(use_split_file=False).fold_roots()


def test_only_the_unsealed_folds_load(grascco_loader):
    """An ordinary load is train+dev and nothing else (DESIGN §6).

    Not a filter that was applied afterwards: the sealed documents are under a second
    root, `source_roots()` does not return it without `sealed_reachable()`, and so the
    twelve files are never opened. Every document that does load carries a fold, because
    the split file is the only thing that knows which one.
    """
    docs = grascco_loader.load()
    assert len(docs) == N_DOCS
    assert base.count_by_split(docs) == UNSEALED_SPLIT
    assert "test" not in base.count_by_split(docs)


def test_the_split_file_accounts_for_the_seal():
    """The corpus-wide figures, and the arithmetic that keeps them checkable.

    `FULL_*` cannot be recomputed from the corpus any more, which is exactly the state
    that lets a published number drift unnoticed. What is still checkable without
    opening anything is the accounting: the frozen split file's totals must match the
    §9.0 table, its folds must sum to those totals, and what the loader can see must be
    the totals minus the sealed fold — per canonical type as well as in aggregate.
    """
    record = json.loads(
        (Path(ROOT) / "splits" / "de-grascco.json").read_text(encoding="utf-8")
    )
    totals, folds = record["totals"], record["folds"]

    assert totals["n_documents"] == FULL_N_DOCS
    assert totals["n_spans"] == FULL_N_SPANS
    assert totals["n_spans_in_scope"] == FULL_N_CANONICAL
    assert totals["n_spans_excluded"] == FULL_N_EXCLUDED
    assert totals["spans_by_phi_type"] == FULL_CANONICAL_COUNTS
    assert {f: v["n_documents"] for f, v in folds.items()} == CONSTRUCTED_SPLIT

    sealed = folds["test"]
    assert FULL_N_DOCS - sealed["n_documents"] == N_DOCS
    assert FULL_N_SPANS - sealed["n_spans"] == N_SPANS
    assert FULL_N_CANONICAL - sealed["n_spans_in_scope"] == N_CANONICAL
    assert FULL_N_EXCLUDED - sealed["n_spans_excluded"] == N_EXCLUDED
    assert {
        phi_type: count - sealed["spans_by_phi_type"].get(phi_type, 0)
        for phi_type, count in FULL_CANONICAL_COUNTS.items()
    } == CANONICAL_COUNTS

    # And the unsealed half of the file agrees with what actually loads, which is what
    # makes the sealed figures credible rather than merely unfalsifiable.
    for fold, documents in UNSEALED_SPLIT.items():
        assert folds[fold]["n_documents"] == documents
        assert folds[fold]["n_spans"] == SPANS_BY_SPLIT[fold]
        assert folds[fold]["n_spans_in_scope"] == IN_SCOPE_BY_SPLIT[fold]


# ─── failure modes, on synthetic files ──────────────────────────────────────
# Written into tmp_path, never into the corpus. The text is invented German, so these
# tests carry no corpus text either — which is also what makes the "no surface in the
# message" test below meaningful rather than accidental.

SYNTHETIC_TEXT = "Frau Musterfrau wurde am 01.01.2000 geboren.\n"


def _write_corpus(root, text=SYNTHETIC_TEXT, annotations=None, doc_id="Synthetisch"):
    """One synthetic document in the layout the loader expects."""
    (root / ANNOTATION_DIR).mkdir(parents=True, exist_ok=True)
    (root / TEXT_DIR).mkdir(parents=True, exist_ok=True)
    (root / TEXT_DIR / f"{doc_id}.txt").write_text(text, encoding="utf-8")
    cas = {
        "%TYPES": {},
        "%FEATURE_STRUCTURES": [
            {"%ID": 1, "%TYPE": "uima.cas.Sofa", "sofaString": text},
            *(annotations or []),
        ],
        "%VIEWS": {},
    }
    (root / ANNOTATION_DIR / f"{doc_id}{ANNOTATION_SUFFIX}").write_text(
        json.dumps(cas, ensure_ascii=False), encoding="utf-8"
    )
    return root


def _phi(begin, end, kind="NAME_PATIENT", sofa=1, fs_id=2):
    return {
        "%ID": fs_id,
        "%TYPE": "webanno.custom.PHI",
        "@sofa": sofa,
        "begin": begin,
        "end": end,
        "kind": kind,
    }


def test_a_synthetic_document_loads(tmp_path):
    """The positive control: without it, every failure test below could pass vacuously."""
    root = _write_corpus(tmp_path, annotations=[_phi(5, 15)])
    docs = GrasccoLoader(root=root, use_split_file=False).load()
    assert len(docs) == 1
    assert len(docs[0].spans) == 1
    assert docs[0].spans[0].phi_type == "NAME"


def test_a_whitespace_edged_span_raises_and_quotes_no_surface(tmp_path):
    """The offset check, and CLAUDE.md's message rule, in one test.

    The surface here is a name invented for this test, so if it appears in the message
    the loader is quoting span text — and the loader that does this for a synthetic
    corpus does it for a DUA-restricted one. Lengths and offsets only.
    """
    root = _write_corpus(tmp_path, annotations=[_phi(4, 15)])  # leading space
    with pytest.raises(CorpusError) as exc:
        GrasccoLoader(root=root, use_split_file=False).load()
    message = str(exc.value)
    assert "whitespace" in message
    assert "Musterfrau" not in message
    assert "[4, 15)" in message  # the offsets, not the text


def test_an_offset_past_the_end_of_the_document_raises(tmp_path):
    root = _write_corpus(tmp_path, annotations=[_phi(5, 9999)])
    with pytest.raises(CorpusError, match="characters"):
        GrasccoLoader(root=root, use_split_file=False).load()


def test_a_missing_begin_raises_rather_than_defaulting_to_zero(tmp_path):
    """CAS JSON omits a feature at its default value, so an absent `begin` reads as 0.

    A span silently starting at 0 is an offset error the whitespace check would not
    always catch, so the loader requires all three features to be present.
    """
    annotation = _phi(5, 15)
    del annotation["begin"]
    root = _write_corpus(tmp_path, annotations=[annotation])
    with pytest.raises(CorpusError, match="missing \\['begin'\\]"):
        GrasccoLoader(root=root, use_split_file=False).load()


def test_an_annotation_on_another_sofa_raises(tmp_path):
    root = _write_corpus(tmp_path, annotations=[_phi(5, 15, sofa=7)])
    with pytest.raises(CorpusError, match="points at sofa"):
        GrasccoLoader(root=root, use_split_file=False).load()


def test_a_text_file_that_differs_from_the_sofa_raises_with_lengths_only(tmp_path):
    """The equality that licenses using CAS offsets against the `.txt`.

    The message reports two lengths and no text: a corpus where the two encodings
    disagree is a corpus where quoting either one in a log is quoting unverified PHI.
    """
    root = _write_corpus(tmp_path, annotations=[_phi(5, 15)])
    (root / TEXT_DIR / "Synthetisch.txt").write_text(
        SYNTHETIC_TEXT + "zusätzlich\n", encoding="utf-8"
    )
    with pytest.raises(CorpusError) as exc:
        GrasccoLoader(root=root, use_split_file=False).load()
    message = str(exc.value)
    assert "sofaString is" in message
    assert "Musterfrau" not in message
    assert "zusätzlich" not in message


def test_a_document_with_no_text_file_raises(tmp_path):
    root = _write_corpus(tmp_path, annotations=[_phi(5, 15)])
    (root / TEXT_DIR / "Synthetisch.txt").unlink()
    with pytest.raises(CorpusError, match="has no text/"):
        GrasccoLoader(root=root, use_split_file=False).load()


def test_a_broken_json_file_names_the_line_and_column(tmp_path):
    root = _write_corpus(tmp_path, annotations=[_phi(5, 15)])
    path = root / ANNOTATION_DIR / f"Synthetisch{ANNOTATION_SUFFIX}"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CorpusError, match="line 1, column"):
        GrasccoLoader(root=root, use_split_file=False).load()


def test_two_sofas_raise(tmp_path):
    root = _write_corpus(tmp_path, annotations=[_phi(5, 15)])
    path = root / ANNOTATION_DIR / f"Synthetisch{ANNOTATION_SUFFIX}"
    cas = json.loads(path.read_text(encoding="utf-8"))
    cas["%FEATURE_STRUCTURES"].append(
        {"%ID": 9, "%TYPE": "uima.cas.Sofa", "sofaString": "etwas anderes"}
    )
    path.write_text(json.dumps(cas, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CorpusError, match="2 sofas"):
        GrasccoLoader(root=root, use_split_file=False).load()


def test_an_empty_annotation_directory_raises(tmp_path):
    (tmp_path / ANNOTATION_DIR).mkdir(parents=True)
    (tmp_path / TEXT_DIR).mkdir(parents=True)
    with pytest.raises(CorpusError, match="no .* files"):
        GrasccoLoader(root=tmp_path, use_split_file=False).load()


# ─── the seal ───────────────────────────────────────────────────────────────


def test_the_sealed_root_is_unreachable_without_authorisation(
    grascco_sealed, grascco_unsplit_loader
):
    """One gate for two layouts. `sealed_reachable()` is the only permission.

    GraSCCo's seal is a second *root* rather than a directory the loader declines to
    enter, and this is the assertion that the flat layout did not acquire a second
    answer to "may this read open the seal" (DESIGN §3's argument about layers,
    applied to the seal).
    """
    sealed = base.sealed_root(grascco_sealed)
    assert grascco_unsplit_loader.sealed_reachable() is None
    assert sealed not in grascco_unsplit_loader.source_roots()
    assert grascco_unsplit_loader.source_roots() == [grascco_unsplit_loader.root]
