"""Tests for the MEDDOCAN loader.

The expected counts are the ones DESIGN §9.0/§9.1 report and the paper will
print, so these tests are the thing standing between a quiet loader bug and a
table of wrong numbers. They come in two kinds, deliberately:

  - **Recount tests** re-derive the totals from the files on disk by a method
    that shares no code with the loader — a regex over the raw `.ann` bytes
    rather than the loader's parser — and then check the loader against that.
    Asserting the loader against `profiles/es-meddocan.raw.json` alone would only
    prove the loader agrees with whoever wrote the profile.
  - **Constant tests** pin the numbers that appear in DESIGN.md. If the corpus is
    re-released with different content these fail, which is the intent: a changed
    corpus must be a decision, not a silent shift in every published figure.

**These tests see 750 documents, not 1,000.** The test fold is sealed (DESIGN §6),
so it is not under the corpus root and no test here can reach it. Two consequences
worth stating, because both look like a weakening and neither is:

  - The corpus-wide totals (1,000 / 22,795 / 20,538) are still asserted, but
    against `splits/es-meddocan.json` — the figures were recorded from the corpus
    *before* the seal, which is precisely why the ordering in DESIGN §6 is a
    requirement rather than a convenience. See `test_split_file.py`.
  - The recount over raw `.ann` bytes now covers train+dev. It remains an
    independent check of the loader's parser on 17,134 spans; what it no longer is
    is a check of the sealed fold, and a test claiming otherwise would be claiming
    to have read what it must not.

    python3 -m pytest tests/ -q
"""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.corpora import CorpusError, Document, Span, base  # noqa: E402
from src.corpora.meddocan import EXCLUDED_TYPES, TYPE_MAP, MeddocanLoader  # noqa: E402

# ─── expected values ────────────────────────────────────────────────────────
# What the loader can see: train+dev. Measured from the corpus before the seal and
# re-derived by the recount fixture below, so these are checked, not transcribed.

N_DOCS = 750
N_SPANS = 17134
N_CANONICAL = 15419
N_EXCLUDED = 1715
UNSEALED_SPLIT = {"train": 500, "dev": 250}
N_BOM_DOCS = 22
N_BOM_SPANS = 524
CANONICAL_COUNTS = {
    "NAME": 3009,
    "DATE": 1955,
    "AGE": 1556,
    "LOCATION_AREA": 3922,
    "LOCATION_STREET": 1296,
    "ORGANISATION": 573,
    "CONTACT": 814,
    "ID": 2251,
    "PROFESSION": 28,
    "OTHER": 15,
}
EXCLUDED_COUNTS = {
    "SEXO_SUJETO_ASISTENCIA": 1380,
    "FAMILIARES_SUJETO_ASISTENCIA": 335,
}
SPANS_BY_SPLIT = {"train": 11333, "dev": 5801}
IN_SCOPE_BY_SPLIT = {"train": 10165, "dev": 5254}

# ─── the corpus-wide figures DESIGN §9.0/§9.1 report ────────────────────────
# Kept here because they are the published numbers, and asserted against the
# frozen split file rather than the corpus: the test fold is sealed, so the only
# remaining evidence for them is the file that was generated before the seal.
# A test that recomputed them would have to read the sealed fold to do it.
FULL_N_DOCS = 1000
FULL_N_SPANS = 22795
FULL_N_CANONICAL = 20538
FULL_N_EXCLUDED = 2257
OFFICIAL_SPLIT = {"train": 500, "dev": 250, "test": 250}

BOM = "﻿"


# ─── fixtures ───────────────────────────────────────────────────────────────


# `loader` — the corpus checkout, then the constructed loader — is in
# `tests/conftest.py`, along with `corpus_present`, and is defined nowhere else.
# It used to live here, wrapping `MeddocanLoader()` in
# `except CorpusError: pytest.skip(...)`, which also swallowed real loader bugs: a
# type both mapped and excluded raises from `_check_type_map` at construction, and
# the broad except reported that as "corpus not available" while 27 tests skipped
# and the suite stayed green. The mutation harness caught it — and then caught the
# same defect three more times in files that had copied the fixture from here.
# `tests/test_conftest.py` is what now stops the copy. A skip must mean one thing.


@pytest.fixture(scope="module")
def docs(loader):
    return loader.load()


@pytest.fixture(scope="module")
def ann_paths(loader):
    """Every reachable .ann file, found independently of the loader's iteration.

    The fold list comes from `fold_roots()` rather than from a literal tuple, so a
    test cannot reach a fold the loader would refuse — hardcoding `"test"` here
    would be a way of reading the sealed fold from the test suite, which is the
    thing the seal forbids.
    """
    paths = []
    for split_dir, root in loader.fold_roots().items():
        paths.extend(sorted(loader._brat_dir(split_dir, root).glob("*.ann")))
    return paths


@pytest.fixture(scope="module")
def recount(ann_paths):
    """Re-derive the totals from disk without using the loader's parser.

    A regex over each raw line, rather than `split("\\t")` and `split()`. Shares
    no code with `_parse_line`, so a bug in one does not hide in the other.
    """
    line_re = re.compile(r"^(T\d+)\t([A-Z_]+) (\d+) (\d+)\t(.*)$")
    out = {
        "docs": 0,
        "spans": 0,
        "by_type": {},
        "by_split": {},
        "spans_by_split": {},
        "bom_docs": 0,
        "bom_spans": 0,
    }
    for path in ann_paths:
        split = path.parent.parent.name
        out["docs"] += 1
        out["by_split"][split] = out["by_split"].get(split, 0) + 1
        has_bom = path.with_suffix(".txt").read_bytes().startswith(b"\xef\xbb\xbf")
        if has_bom:
            out["bom_docs"] += 1
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            match = line_re.match(raw_line)
            assert match, f"{path.name}: unparsed annotation line"
            corpus_type = match.group(2)
            out["spans"] += 1
            out["spans_by_split"][split] = out["spans_by_split"].get(split, 0) + 1
            out["by_type"][corpus_type] = out["by_type"].get(corpus_type, 0) + 1
            if has_bom:
                out["bom_spans"] += 1
    return out


# ─── the loader agrees with an independent recount ──────────────────────────


def test_loader_matches_independent_recount(docs, recount):
    assert len(docs) == recount["docs"]
    assert base.count_spans(docs) == recount["spans"]
    assert base.count_by_type(docs, canonical=False) == recount["by_type"]
    assert base.count_by_split(docs) == recount["by_split"]


def test_recount_matches_the_documented_totals(recount):
    """The recount, not the loader, against the DESIGN.md figures.

    If this fails and the loader tests pass, the corpus on disk differs from the
    one DESIGN.md was written against.
    """
    assert recount["docs"] == N_DOCS
    assert recount["spans"] == N_SPANS
    assert recount["by_split"] == UNSEALED_SPLIT
    assert recount["bom_docs"] == N_BOM_DOCS
    assert recount["bom_spans"] == N_BOM_SPANS


# ─── counts ─────────────────────────────────────────────────────────────────


def test_document_count(docs):
    assert len(docs) == N_DOCS


def test_document_ids_are_unique(docs):
    assert len({d.doc_id for d in docs}) == N_DOCS


def test_total_span_count(docs):
    assert base.count_spans(docs) == N_SPANS


def test_canonical_span_count(docs):
    assert base.count_spans(docs, in_scope_only=True) == N_CANONICAL


def test_excluded_span_count(docs):
    excluded = base.count_spans(docs) - base.count_spans(docs, in_scope_only=True)
    assert excluded == N_EXCLUDED


def test_the_three_totals_reconcile(docs):
    """20,538 + 2,257 = 22,795. The reconciliation DESIGN §9.0 promises."""
    assert N_CANONICAL + N_EXCLUDED == N_SPANS
    assert (
        base.count_spans(docs, in_scope_only=True) + N_EXCLUDED
        == base.count_spans(docs)
    )


def test_canonical_type_counts_match_design_table(docs):
    assert base.count_by_type(docs) == CANONICAL_COUNTS


def test_excluded_type_counts_match_design_table(docs):
    subtypes = base.count_by_type(docs, canonical=False)
    assert {t: subtypes[t] for t in EXCLUDED_COUNTS} == EXCLUDED_COUNTS


def test_all_twenty_two_source_types_are_present(docs):
    """22 source types, and every one is either mapped or excluded."""
    subtypes = base.count_by_type(docs, canonical=False)
    assert len(subtypes) == 22
    assert set(subtypes) == set(TYPE_MAP) | set(EXCLUDED_TYPES)


def test_ten_canonical_types_all_occur(docs):
    """All ten canonical types are observed — which is what makes the common-type
    subset check of DESIGN §5.1 vacuous for the MEDDOCAN/CARMEN-I pair."""
    assert set(base.count_by_type(docs)) == set(base.canonical_types())


# ─── the official split ─────────────────────────────────────────────────────


def test_only_the_unsealed_folds_load(docs):
    """An ordinary load is train+dev and nothing else (DESIGN §6).

    Not a filter that was applied: the sealed fold's directory is not under the
    corpus root, so `fold_roots()` never offers a path to it. This test is what
    makes that structural fact a checked one.
    """
    assert base.count_by_split(docs) == UNSEALED_SPLIT
    assert "test" not in base.count_by_split(docs)


def test_the_full_official_split_is_recorded_in_the_frozen_file():
    """The 1,000-document figures survive the seal in the split file, not here.

    The test fold's contribution can no longer be recomputed, which is the reason
    DESIGN §6 requires the split file to be generated before the seal. Asserted
    against the file so that the published totals still have a check standing over
    them after the corpus stopped being fully readable.
    """
    from src import split

    record = split.read(MeddocanLoader.corpus_id)
    assert {f: b["n_documents"] for f, b in record["folds"].items()} == OFFICIAL_SPLIT
    assert record["totals"]["n_documents"] == FULL_N_DOCS
    assert record["totals"]["n_spans"] == FULL_N_SPANS
    assert record["totals"]["n_spans_in_scope"] == FULL_N_CANONICAL
    assert record["totals"]["n_spans_excluded"] == FULL_N_EXCLUDED
    # And the unsealed part of the file agrees with what actually loads, which is
    # what keeps the sealed figures credible rather than merely unfalsifiable.
    for fold, expected in UNSEALED_SPLIT.items():
        assert record["folds"][fold]["n_documents"] == expected


def test_official_split_is_read_not_constructed(docs):
    """Every document carries a split, and the values come from naming.yaml.

    MEDDOCAN's split is public and frozen (DESIGN §9.6), so a document without a
    fold, or with an invented fold name, means the shipped partition was lost.
    """
    assert all(d.split is not None for d in docs)
    assert set(base.count_by_split(docs)) <= set(base.split_names())


def test_span_counts_per_split(docs):
    per_split = {}
    for doc in docs:
        per_split[doc.split] = per_split.get(doc.split, 0) + len(doc.spans)
    assert per_split == SPANS_BY_SPLIT


def test_in_scope_span_counts_per_split(docs):
    """The per-fold exclusion cost DESIGN §9.1 reports."""
    per_split = {}
    for doc in docs:
        per_split[doc.split] = per_split.get(doc.split, 0) + len(doc.in_scope_spans)
    assert per_split == IN_SCOPE_BY_SPLIT


# ─── offsets ────────────────────────────────────────────────────────────────


def test_every_span_slices_back_to_its_surface(docs):
    """The §9.7 assertion, re-run here over all 22,795 spans.

    `load()` already asserts this and would have raised, so this test is a
    tripwire on the assertion itself: if `assert_offsets` were weakened to a
    warning or an early return, every other test would still pass.
    """
    checked = 0
    for doc in docs:
        for span in doc.spans:
            assert doc.text[span.start : span.end] == span.surface, (
                f"{doc.doc_id}: {span.subtype} at "
                f"[{span.start}, {span.end}) does not slice back"
            )
            checked += 1
    assert checked == N_SPANS


def test_offsets_are_within_the_document(docs):
    for doc in docs:
        for span in doc.spans:
            assert 0 <= span.start < span.end <= len(doc.text)


# ─── BOM handling (DESIGN §9.7) ─────────────────────────────────────────────


def test_bom_documents_are_found_and_flagged(docs):
    bom_docs = [d for d in docs if d.had_bom]
    assert len(bom_docs) == N_BOM_DOCS
    assert sum(len(d.spans) for d in bom_docs) == N_BOM_SPANS


def test_no_loaded_text_starts_with_a_bom(docs):
    assert not any(d.text.startswith(BOM) for d in docs)


def test_bom_handling_actually_affects_761_spans(ann_paths):
    """The measurement behind §9.7, done from the raw files.

    Three conventions, over the same 761 spans in the same 32 files:

      - plain utf-8, BOM retained as a character  -> all 761 match
      - BOM stripped AND offsets shifted (ours)   -> all 761 match
      - BOM stripped, offsets NOT shifted         -> all 761 break

    The third is what `encoding='utf-8-sig'` silently does. The test's point is
    that the difference is not cosmetic: it is 761 spans, every one of them
    wrong, in a way no aggregate count would reveal — the totals are identical
    under all three conventions.
    """
    kept_ok = shifted_ok = unshifted_ok = total = 0
    bom_docs = 0
    for ann_path in ann_paths:
        raw = ann_path.with_suffix(".txt").read_text(encoding="utf-8")
        if not raw.startswith(BOM):
            continue
        bom_docs += 1
        stripped = raw[len(BOM) :]
        for line in ann_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            _, middle, surface = line.split("\t")
            _, start_s, end_s = middle.split()
            start, end = int(start_s), int(end_s)
            total += 1
            kept_ok += raw[start:end] == surface
            shifted_ok += stripped[start - 1 : end - 1] == surface
            unshifted_ok += stripped[start:end] == surface

    assert bom_docs == N_BOM_DOCS
    assert total == N_BOM_SPANS
    assert kept_ok == N_BOM_SPANS, "offsets as shipped count the BOM"
    assert shifted_ok == N_BOM_SPANS, "strip-and-shift is offset-preserving"
    assert unshifted_ok == 0, (
        "stripping the BOM without shifting must break every span in these "
        "files — if any match, the premise of DESIGN §9.7 has changed"
    )


def test_utf_8_sig_would_break_the_loader(loader, ann_paths):
    """Direct evidence for the encoding choice, on one BOM document.

    Reading with utf-8-sig removes the BOM at decode time, so the loader's
    `strip_bom` finds nothing to strip and applies no shift — leaving every
    offset in the file one character too high. Asserted here as a raising
    behaviour so the choice of `encoding='utf-8'` in the loader cannot be
    "simplified" without a test failing.
    """
    bom_ann = next(
        p
        for p in ann_paths
        if p.with_suffix(".txt").read_bytes().startswith(b"\xef\xbb\xbf")
    )
    sig_text = bom_ann.with_suffix(".txt").read_text(encoding="utf-8-sig")
    text, shift = loader.strip_bom(sig_text)
    assert shift == 0, "utf-8-sig already removed the BOM, so no shift is applied"

    doc = Document(
        doc_id=bom_ann.stem,
        corpus_id=loader.corpus_id,
        text=text,
        spans=[
            Span(
                start=int(middle.split()[1]),
                end=int(middle.split()[2]),
                surface=surface,
                subtype=middle.split()[0],
                phi_type=loader.classify(middle.split()[0])[0],
                excluded=loader.classify(middle.split()[0])[1],
            )
            for middle, surface in (
                (line.split("\t")[1], line.split("\t")[2])
                for line in bom_ann.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        ],
    )
    with pytest.raises(CorpusError, match="does not match its recorded surface"):
        doc.assert_offsets()


# ─── exclusions are flagged, not dropped (DESIGN §9.1) ──────────────────────


def test_excluded_spans_are_kept_in_the_document(docs):
    """Excluded spans must survive loading: their volume is a reported number."""
    excluded = [s for d in docs for s in d.spans if s.excluded]
    assert len(excluded) == N_EXCLUDED
    assert {s.subtype for s in excluded} == set(EXCLUDED_TYPES)


def test_excluded_spans_carry_no_canonical_type(docs):
    for doc in docs:
        for span in doc.spans:
            if span.excluded:
                assert span.phi_type is None
                assert not span.in_scope
            else:
                assert span.phi_type is not None
                assert span.in_scope


def test_excluded_spans_keep_their_source_type(docs):
    """subtype is preserved for the per-type reporting of DESIGN §5.1."""
    subtypes = base.count_by_type(docs, canonical=False)
    assert sum(subtypes[t] for t in EXCLUDED_TYPES) == N_EXCLUDED


def test_exclusion_share_matches_the_reported_limitation():
    """9.90% of gold, the figure the paper must state (DESIGN §9.1).

    Corpus-wide, so it uses the frozen figures: the reported limitation is about
    MEDDOCAN, not about the part of MEDDOCAN that is still readable. The train+dev
    share (10.01%) is asserted separately below, because a reader who saw only one
    of the two numbers would be entitled to assume it was the other.
    """
    assert round(100 * FULL_N_EXCLUDED / FULL_N_SPANS, 2) == 9.90
    assert round(100 * N_EXCLUDED / N_SPANS, 2) == 10.01


# ─── gold spans carry no provenance (DESIGN §3) ─────────────────────────────


def test_gold_spans_have_empty_provenance(docs):
    """Gold is not a detection, so layer/detector/rule_id/score stay unset."""
    for doc in docs:
        for span in doc.spans:
            assert span.layer is None
            assert span.detector is None
            assert span.rule_id is None
            assert span.score is None
            assert span.agent_actions == []
            assert span.is_gold


def test_span_can_carry_provenance_when_a_detector_fills_it():
    """The dataclass supports the §3 fields; gold simply leaves them empty."""
    span = Span(
        start=0,
        end=4,
        surface="test",
        subtype="FECHAS",
        phi_type="DATE",
        layer="regex_checksum",
        detector="R",
        rule_id="es:date_numeric",
        score=1.0,
    )
    assert not span.is_gold
    assert span.rule_id.startswith("es:")


def test_layer_must_come_from_naming_yaml():
    """A layer value not in the config is an error, not a new layer (§3)."""
    with pytest.raises(CorpusError, match="not a layer"):
        Span(
            start=0,
            end=4,
            surface="test",
            subtype="FECHAS",
            phi_type="DATE",
            layer="regex",  # close to regex_checksum, and not it
        )


def test_phi_type_must_come_from_naming_yaml():
    with pytest.raises(CorpusError, match="not a phi_type"):
        Span(start=0, end=4, surface="test", subtype="FECHAS", phi_type="DATES")


# ─── failure modes ──────────────────────────────────────────────────────────


def test_unknown_annotation_type_raises(loader):
    """An unmapped type must stop the load rather than be dropped or bucketed.

    A silently discarded gold span makes recall look better than it is.
    """
    with pytest.raises(CorpusError, match="neither mapped nor excluded"):
        loader.classify("NUEVO_TIPO_INVENTADO")


def test_mapped_and_excluded_are_disjoint():
    assert not set(TYPE_MAP) & set(EXCLUDED_TYPES)


def test_type_map_targets_are_all_canonical():
    assert set(TYPE_MAP.values()) <= set(base.canonical_types())


def test_offset_mismatch_names_the_span_index():
    """A mismatch must say which span failed, per the brief."""
    doc = Document(
        doc_id="synthetic",
        corpus_id="es-meddocan",
        text="hello world",
        spans=[
            Span(start=0, end=5, surface="hello", subtype="FECHAS", phi_type="DATE"),
            Span(start=6, end=11, surface="WRONG", subtype="FECHAS", phi_type="DATE"),
        ],
    )
    with pytest.raises(CorpusError, match=r"span 1 \(FECHAS\)"):
        doc.assert_offsets()


def test_offset_mismatch_message_quotes_no_surface():
    """The failure message must not contain span text.

    CARMEN-I is DUA-restricted authentic clinical text and exception messages
    travel into logs and issues. A check that is safe for MEDDOCAN and unsafe for
    CARMEN-I is a check nobody can rely on, so no loader quotes a surface — the
    message reports lengths and offsets instead.
    """
    secret = "Ernesto Rivera Bueno"
    doc = Document(
        doc_id="synthetic",
        corpus_id="es-meddocan",
        text="x" * 40,
        spans=[
            Span(
                start=0,
                end=len(secret),
                surface=secret,
                subtype="NOMBRE_SUJETO_ASISTENCIA",
                phi_type="NAME",
            )
        ],
    )
    with pytest.raises(CorpusError) as exc:
        doc.assert_offsets()
    message = str(exc.value)
    assert secret not in message
    assert "x" * 10 not in message
    assert str(len(secret)) in message  # the length, not the text


def test_document_rejects_unstripped_bom():
    with pytest.raises(CorpusError, match="U\\+FEFF"):
        Document(doc_id="d", corpus_id="es-meddocan", text=BOM + "text")


def test_unknown_corpus_id_raises():
    with pytest.raises(CorpusError, match="not a corpus"):
        base.load("es-nonexistent")


def test_known_corpus_without_a_loader_says_so():
    """de-grascco is a real corpus id with no loader yet — a distinguishable
    failure from a typo, because the fix is different."""
    with pytest.raises(CorpusError, match="no loader yet"):
        base.load("de-grascco")
