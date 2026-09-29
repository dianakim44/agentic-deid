"""Tests for the CARMEN-I loader.

The same two kinds as the four loader files before it, because the counts these tests
pin are the ones DESIGN §9.0 reports and the paper prints:

  - **Recount tests** re-derive the totals by a route that shares no code with the
    loader. Here that route is unusually good: the release ships an *aggregated* TSV
    (`tsv/{variant}/CARMEN-I_replaced_anon.tsv`, one row per span) which the loader
    deliberately does not read — §9.7 records why, it disagrees with the text in 38 of
    8,231 rows against the standoff's 1 — so it is an independent encoding of the same
    annotations rather than a second pass over the same bytes.
  - **Constant tests** pin §9.0's published figures, so a re-released corpus fails here
    instead of quietly moving every number in the paper.

**No surface form appears in this file, and that is a rule rather than a habit.** These
are real hospital records under a PhysioNet DUA (CLAUDE.md, "실제 병원 기록"), so no test
here writes a slice of the corpus into an assertion, a parametrisation or a failure
message. That constrains how the tests below are written:

  - Every assertion about a span is over offsets, lengths, counts and type names.
  - The failure-mode tests build synthetic brat trees in `tmp_path` with invented
    Spanish text, which is also what makes `test_a_malformed_offset_field_is_not_quoted`
    meaningful rather than accidental — the token it looks for exists only in this file.
  - The one span whose recorded surface this loader overwrites (§9.7) is asserted by
    document id, span index, type and offsets. Its text is not in this file, not in the
    loader, and not in DESIGN.

**These tests see all 2,000 documents.** `splits/es-carmen.json` is not frozen yet, so
nothing is sealed and the split-file half of the GraSCCo and ko-surro files has no
counterpart here — the constants below are the corpus-wide ones. The three tests about
the composition the split will stratify on (§9.5) are here *before* the freeze on
purpose: they are what the deterministic small-cell rule will be written against.

    python3 -m pytest tests/test_carmen_loader.py -q
"""
import collections
import csv
import os
import sys
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.corpora import CorpusError, base  # noqa: E402
from src.corpora.base import SealError  # noqa: E402
from src.corpora.carmen import (  # noqa: E402
    ANN_ROOT,
    CONCEPT_LAYER,
    CONCEPT_TYPES,
    DOCTYPE_TOKENS,
    EXCLUDED_TYPES,
    KNOWN_SURFACE_DEFECTS,
    LANGUAGE_LABELS,
    MAPPINGS,
    MAPPINGS_FIELDS,
    PHI_LAYER,
    RELEASE,
    SCHEMA,
    SCHEMA_SECTION,
    TYPE_MAP,
    UNREAD_TEXT_DIR,
    VARIANT,
    CarmenLoader,
)

# ─── expected values (DESIGN §9.0, §9.1, §9.5, §9.7) ────────────────────────
# Measured 2026-09-28 over the whole release. Nothing is sealed yet, so unlike the
# GraSCCo and ko-surro files there is no visible/corpus-wide split in these constants.

N_DOCS = 2000
N_SPANS = 8231
N_CANONICAL = 7473
N_EXCLUDED = 758

#: §9.0's `es-carmen` table, canonical column.
CANONICAL_COUNTS = {
    "DATE": 5386,
    "AGE": 815,
    "ORGANISATION": 497,
    "ID": 243,
    "LOCATION_AREA": 208,
    "NAME": 151,
    "PROFESSION": 91,
    "OTHER": 38,
    "CONTACT": 22,
    "LOCATION_STREET": 22,
}

#: The same table's source-type column, which is what the aggregated TSV counts by.
SUBTYPE_COUNTS = {
    "FECHAS": 5386,
    "EDAD_SUJETO_ASISTENCIA": 815,
    "SEXO_SUJETO_ASISTENCIA": 458,
    "HOSPITAL": 316,
    "FAMILIARES_SUJETO_ASISTENCIA": 299,
    "NUMERO_IDENTIF": 227,
    "NOMBRE_PERSONAL_SANITARIO": 151,
    "INSTITUCION": 129,
    "PAIS": 118,
    "PROFESION": 91,
    "TERRITORIO": 90,
    "CENTRO_SALUD": 52,
    "OTROS_SUJETO_ASISTENCIA": 38,
    "CALLE": 22,
    "NUMERO_TELEFONO": 22,
    "ID_SUJETO_ASISTENCIA": 14,
    "ID_CONTACTO_ASISTENCIAL": 2,
    "URL_WEB": 1,
}
EXCLUDED_COUNTS = {
    "SEXO_SUJETO_ASISTENCIA": 458,
    "FAMILIARES_SUJETO_ASISTENCIA": 299,
    "URL_WEB": 1,
}

#: §9.0: the schema declares 28 PHI types and 18 are used. The ten unused ones are
#: mapped or excluded anyway, which is the property `test_every_declared_type_is_decided`
#: is about; nine map and `DIREC_PROT_INTERNET` is the tenth and is excluded (§9.1).
N_DECLARED_PHI = 28
N_OBSERVED_TYPES = 18
UNUSED_TYPES = {
    "NOMBRE_SUJETO_ASISTENCIA",
    "CORREO_ELECTRONICO",
    "NUMERO_FAX",
    "DIREC_PROT_INTERNET",
    "ID_ASEGURAMIENTO",
    "ID_TITULACION_PERSONAL_SANITARIO",
    "ID_EMPLEO_PERSONAL_SANITARIO",
    "IDENTIF_VEHICULOS_NRSERIE_PLACAS",
    "IDENTIF_DISPOSITIVOS_NRSERIE",
    "IDENTIF_BIOMETRICOS",
}

#: §9.5's strata, measured. `LANGUAGE_DOCS` is the composition the user's instruction
#: names (es 1,697 / bi 264 / cat 39) and `DOCTYPE_DOCS` the other dimension. `CROSS`
#: is the cross-tabulation the split has to stratify on, and the reason it needs a
#: deterministic small-cell rule: 12 of the 15 possible cells are non-empty, the
#: smallest holds **one** document, and a three-way split of one document is not a
#: thing. Recorded here rather than only in the split file, because the rule is written
#: against these numbers and a test is what notices when they move.
LANGUAGE_DOCS = {"es": 1697, "bi": 264, "cat": 39}
DOCTYPE_DOCS = {"IR": 1201, "IA": 617, "IT": 172, "CC": 5, "IE": 5}
CROSS = {
    ("IR", "es"): 961,
    ("IA", "es"): 573,
    ("IR", "bi"): 221,
    ("IT", "es"): 154,
    ("IA", "bi"): 31,
    ("IR", "cat"): 19,
    ("IA", "cat"): 13,
    ("IT", "bi"): 11,
    ("IT", "cat"): 7,
    ("IE", "es"): 5,
    ("CC", "es"): 4,
    ("CC", "bi"): 1,
}
SMALLEST_CELL = ("CC", "bi")

#: §8.5: 789 of the 2,000 units are clinical sections rather than whole notes, which is
#: why `meta` says `filename_doctype` and not `document_type`. Nine section tokens plus
#: the sectionless form.
N_SECTION_UNITS = 789
N_SECTION_TOKENS = 9

#: The `ner_annotations` column, and the only claim the mappings file makes that can be
#: checked against the tree.
N_CONCEPT_FLAGGED = 500

#: Sparsity, both readings. `src/split.py`'s narrative uses the in-scope one — the
#: distinction `sparsity_counts_excluded_spans` exists to keep (9 documents have spans
#: and none in scope).
N_DOCS_WITH_NO_SPAN = 461
N_DOCS_WITH_NO_IN_SCOPE_SPAN = 470

#: §9.7: no document in this release carries a BOM, and exactly one span's recorded
#: surface disagrees with the text at its offsets.
N_BOM_DOCS = 0
DEFECT_DOC = "CARMEN-I_IA_EVOL_112"
DEFECT_INDEX = 4
DEFECT_PIN = ("FECHAS", 153, 157)
N_TSV_DISAGREEMENTS = 38


# ─── fixtures ───────────────────────────────────────────────────────────────
# `carmen_present` / `carmen_unsplit_loader` / `carmen_docs` are in tests/conftest.py
# and are defined nowhere else. A local availability check here is the defect that
# shipped four times; see that file's header.
#
# `carmen_docs` is session-scoped and shared, so nothing below mutates it. That matters
# more here than elsewhere: `_apply_known_defects` rewrites one span in place at a known
# *index*, so a test that re-sorted a document's span list would move what the pin names.


@pytest.fixture(scope="module")
def phi_dir(carmen_unsplit_loader):
    """The PHI layer, found without the loader's iteration.

    Through `source_roots()` rather than a literal path, so this cannot reach a sealed
    root the loader would refuse to open.
    """
    roots = carmen_unsplit_loader.source_roots()
    assert roots == [carmen_unsplit_loader.root]
    return roots[0] / ANN_ROOT / VARIANT / PHI_LAYER


@pytest.fixture(scope="module")
def recount(carmen_unsplit_loader):
    """Re-derive the span totals from the release's aggregated TSV.

    The independent encoding: one row per span, written by the corpus's own tooling,
    and an encoding this loader never opens (§9.7 — it disagrees with the text in 38
    rows where the standoff disagrees in 1, which is why the standoff is what is read).
    Parsed with `csv` on the `name`/`tag` columns only. The `text` column is not read at
    all and the `span` column is not needed: what this recount is for is *how many spans
    of which type each document has*, which is the claim §9.0's table makes.

    Document count cannot come from here — 461 documents have no span and so no row —
    so it comes from the directory listing and the mappings file, in the test below.
    """
    root = carmen_unsplit_loader.root
    path = root / RELEASE / "tsv" / VARIANT / f"CARMEN-I_{VARIANT}_{PHI_LAYER}.tsv"
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        assert reader.fieldnames == ["name", "tag", "span", "text"]
        rows = [(row["name"], row["tag"]) for row in reader]
    out = {
        "spans": len(rows),
        "by_subtype": dict(collections.Counter(tag for _, tag in rows)),
        "by_doc": dict(collections.Counter(name for name, _ in rows)),
    }
    return out


# ─── the loader agrees with an independent recount ──────────────────────────


def test_loader_matches_the_aggregated_tsv(carmen_docs, recount):
    """Totals, per-type counts, and per-document counts against the other encoding."""
    assert base.count_spans(carmen_docs) == recount["spans"]
    assert base.count_by_type(carmen_docs, canonical=False) == recount["by_subtype"]
    loaded = {
        doc.doc_id: len(doc.spans) for doc in carmen_docs if doc.spans
    }
    assert loaded == recount["by_doc"]


def test_the_recount_saw_the_corpus(recount):
    """The TSV must have parsed, or every comparison to it is vacuous."""
    assert recount["spans"] == N_SPANS
    assert recount["by_subtype"] == SUBTYPE_COUNTS
    assert len(recount["by_doc"]) == N_DOCS - N_DOCS_WITH_NO_SPAN


def test_the_document_count_comes_from_two_independent_listings(carmen_docs, phi_dir):
    """2,000, counted twice without the loader: the `.ann` files and the mappings rows.

    Neither route can come from the TSV, because a document with no span has no row
    there. Both are asserted because they are the two things the loader requires to
    agree (`_read`'s `stems != set(labels)` check), and a test that only counted one
    would pass on a release where the other had drifted.
    """
    stems = {path.stem for path in phi_dir.glob("*.ann")}
    mappings = phi_dir.parent.parent.parent.parent / MAPPINGS
    with open(mappings, encoding="utf-8", newline="") as fh:
        labelled = {row["filename"] for row in csv.DictReader(fh, delimiter="\t")}

    assert len(stems) == N_DOCS
    assert len(labelled) == N_DOCS
    assert stems == labelled
    assert {doc.doc_id for doc in carmen_docs} == stems


# ─── the counts DESIGN §9.0 reports ─────────────────────────────────────────


def test_document_count(carmen_docs):
    assert len(carmen_docs) == N_DOCS


def test_document_ids_are_unique(carmen_docs):
    ids = [doc.doc_id for doc in carmen_docs]
    assert len(set(ids)) == len(ids)


def test_total_span_count(carmen_docs):
    assert base.count_spans(carmen_docs) == N_SPANS


def test_canonical_span_count(carmen_docs):
    assert base.count_spans(carmen_docs, in_scope_only=True) == N_CANONICAL


def test_the_three_totals_reconcile(carmen_docs):
    """7,473 + 758 = 8,231: the mapping is exhaustive and no span is dropped silently."""
    excluded = sum(1 for doc in carmen_docs for span in doc.spans if span.excluded)
    assert excluded == N_EXCLUDED
    assert N_CANONICAL + N_EXCLUDED == N_SPANS
    assert base.count_spans(carmen_docs, in_scope_only=True) + excluded == N_SPANS


def test_canonical_type_counts_match_design_table(carmen_docs):
    assert base.count_by_type(carmen_docs) == CANONICAL_COUNTS


def test_excluded_type_counts_match_design_table(carmen_docs):
    counts = collections.Counter(
        span.subtype for doc in carmen_docs for span in doc.spans if span.excluded
    )
    assert dict(counts) == EXCLUDED_COUNTS


def test_all_ten_canonical_types_have_gold(carmen_docs):
    """§9.0's claim that no other corpus in this project manages.

    Asserted as a set equality against `naming.yaml`'s axis rather than as a count, so
    a canonical type added to the vocabulary makes this fail rather than silently
    leaving the claim describing nine types.
    """
    assert set(CANONICAL_COUNTS) == set(base.canonical_types())
    assert all(count > 0 for count in CANONICAL_COUNTS.values())


def test_the_excluded_share_is_what_section_9_1_prints(carmen_docs):
    """9.21% of gold, and the two components §9.1's table breaks it into.

    §9.1's `es-carmen` row is a published limitation, so the share is pinned here the
    way GraSCCo's 9.68% is. `DIREC_PROT_INTERNET` contributes nothing — that is the
    point of §9.1's note that adding it left the total at 758.
    """
    assert round(100 * N_EXCLUDED / N_SPANS, 2) == 9.21
    assert round(100 * 458 / N_SPANS, 2) == 5.56
    assert round(100 * 299 / N_SPANS, 2) == 3.63
    assert "DIREC_PROT_INTERNET" not in EXCLUDED_COUNTS


def test_the_largest_type_share_is_what_section_5_1_prints(carmen_docs):
    """72.1% of canonical gold is `DATE` — §5.1's concentration figure for this corpus.

    Over the in-scope total, which is the denominator §5.1 states, and recomputed from
    the loaded spans rather than from `CANONICAL_COUNTS` so that this is a check of the
    corpus and not of the constant above it.

    §5.1 also prints **74.3%**, and this is the test that says which is which: 74.3% is
    the same 5,386 spans over the 7,246 canonical total that stood before §9.0 placed
    `NUMERO_IDENTIF` and `URL_WEB` on 2026-09-28. The denominator moved, not the corpus.
    Both are asserted so that neither can be quoted as the other.
    """
    counts = base.count_by_type(carmen_docs)
    largest = max(counts.values())
    assert largest == counts["DATE"]
    assert round(100 * largest / N_CANONICAL, 1) == 72.1
    assert round(100 * largest / (N_CANONICAL - 227), 1) == 74.3
    assert round(100 * largest / N_SPANS, 1) == 65.4


def test_patient_name_gold_is_empty_and_clinician_name_gold_is_not(carmen_docs):
    """§9.0/§5.1: the patient-name type is declared and has zero instances.

    An assertion rather than a gap, so it is asserted. `NAME`'s 151 spans are all the
    clinician type — which is what makes patient-name recall *undefined* here rather
    than zero, and makes this corpus a precision-only probe for that role.
    """
    subtypes = collections.Counter(
        span.subtype
        for doc in carmen_docs
        for span in doc.spans
        if span.phi_type == "NAME"
    )
    assert dict(subtypes) == {"NOMBRE_PERSONAL_SANITARIO": 151}
    assert TYPE_MAP["NOMBRE_SUJETO_ASISTENCIA"] == "NAME"


def test_numero_identif_is_most_of_the_id_row(carmen_docs):
    """§9.0's `NUMERO_IDENTIF` → `ID` decision, with the numbers that argued for it.

    227 of 243 is 93% of the `ID` row and 3.0% of corpus gold, which is what the block
    weighs against excluding the type. `subtype` keeps it recoverable, so the
    role-bearing subset is asserted too — that is the analysis the decision promises
    remains available.
    """
    ids = [
        span for doc in carmen_docs for span in doc.spans if span.phi_type == "ID"
    ]
    subtypes = collections.Counter(span.subtype for span in ids)
    assert len(ids) == 243
    assert subtypes["NUMERO_IDENTIF"] == 227
    assert round(100 * 227 / 243) == 93
    assert round(100 * 227 / N_SPANS, 1) == 2.8
    role_bearing = len(ids) - subtypes["NUMERO_IDENTIF"]
    assert role_bearing == 16


# ─── the schema, and the ten types with no instances ────────────────────────


def test_the_declared_schema_is_exactly_what_the_loader_knows(carmen_unsplit_loader):
    """`annotation.conf` recounted independently, against the three constant sets.

    Parsed here rather than calling `_check_schema`, which would be the loader checking
    itself. The three-way partition is asserted as well as the union: a PHI type that
    drifted into `CONCEPT_TYPES` would keep the union intact while removing the type
    from the gold set entirely.
    """
    path = carmen_unsplit_loader.root / SCHEMA
    declared, in_section = set(), False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == SCHEMA_SECTION
            continue
        if in_section and stripped and not stripped.startswith("#"):
            declared.add(stripped)

    assert declared == set(TYPE_MAP) | set(EXCLUDED_TYPES) | set(CONCEPT_TYPES)
    assert len(set(TYPE_MAP) | set(EXCLUDED_TYPES)) == N_DECLARED_PHI
    assert not set(TYPE_MAP) & set(EXCLUDED_TYPES)
    assert not set(TYPE_MAP) & set(CONCEPT_TYPES)
    assert not set(EXCLUDED_TYPES) & set(CONCEPT_TYPES)


def test_every_declared_type_with_no_instances_is_still_decided(carmen_docs):
    """§9.0: the map is built from the schema, so the ten empty types are mapped anyway.

    The observed set is measured from the corpus and the unused set is the difference,
    which is the direction that matters: a release that started annotating one of the
    ten must fail `test_the_declared_schema...` at load time, not be discovered later
    when a count moves. Nine of the ten map; `DIREC_PROT_INTERNET` is excluded (§9.1).
    """
    observed = {span.subtype for doc in carmen_docs for span in doc.spans}
    assert len(observed) == N_OBSERVED_TYPES

    declared_phi = set(TYPE_MAP) | set(EXCLUDED_TYPES)
    assert observed <= declared_phi
    assert declared_phi - observed == UNUSED_TYPES
    assert UNUSED_TYPES - set(TYPE_MAP) == {"DIREC_PROT_INTERNET"}
    assert "DIREC_PROT_INTERNET" in EXCLUDED_TYPES


def test_the_variant_and_the_layer_are_the_ones_section_9_0_counted():
    """Which of the release's four annotation sets every number above is over.

    Pinned as constants because the alternative is not an error anywhere: the `masked`
    variant has the same layout and loads, and its texts substitute placeholders for the
    identifiers — so a detector evaluated against it would be scored on how well it
    finds the masking convention, and the counts would be 8,230 rather than 8,231. The
    `ner` layer has the same layout again and holds 26,360 medical-concept spans. Both
    substitutions produce a corpus that reads cleanly and answers a different question.
    """
    assert VARIANT == "replaced"
    assert PHI_LAYER == "anon"
    assert CONCEPT_LAYER == "ner"
    assert UNREAD_TEXT_DIR == RELEASE / "txt"


def test_type_map_targets_are_all_canonical():
    assert set(TYPE_MAP.values()) <= set(base.canonical_types())


def test_unknown_annotation_type_raises(carmen_unsplit_loader):
    with pytest.raises(CorpusError, match="neither mapped nor excluded"):
        carmen_unsplit_loader.classify("TIPO_QUE_NO_EXISTE")


def test_a_concept_type_is_not_classified_as_phi(carmen_unsplit_loader):
    """The concept layer's types are known to the schema check and to nothing else.

    `classify()` must refuse them rather than map them: 26,360 concept spans against a
    gold set of 8,231 is not a mapping error that anyone would catch downstream.
    """
    for concept_type in sorted(CONCEPT_TYPES):
        with pytest.raises(CorpusError, match="neither mapped nor excluded"):
            carmen_unsplit_loader.classify(concept_type)


def test_excluded_spans_are_kept_and_carry_no_canonical_type(carmen_docs):
    """§9.1: the exclusion volume is a published figure, so the spans must survive."""
    excluded = [span for doc in carmen_docs for span in doc.spans if span.excluded]
    assert len(excluded) == N_EXCLUDED
    assert all(span.phi_type is None for span in excluded)
    # Three of the four excluded types occur; `DIREC_PROT_INTERNET` has no instances.
    assert {span.subtype for span in excluded} == set(EXCLUDED_COUNTS)


def test_gold_spans_have_empty_provenance(carmen_docs):
    """Gold is not a detection. A layer on a gold span would make it one (DESIGN §3)."""
    for doc in carmen_docs:
        for span in doc.spans:
            assert span.layer is None
            assert span.detector is None
            assert span.rule_id is None
            assert span.agent_actions == []


# ─── offsets, and the one span §9.7 pins ────────────────────────────────────


def test_every_span_slices_back_to_its_surface(carmen_docs):
    """`assert_offsets()` ran during the load; this asserts it over what came out.

    The comparison is `==` on the slice and the recorded surface, and no branch of it
    prints either. For 8,230 of the 8,231 spans the two readings are independent — the
    standoff's surface field was written by the corpus's tooling and the slice comes
    from the `.txt` — and the 8,231st is the span below.
    """
    for doc in carmen_docs:
        for span in doc.spans:
            assert doc.text[span.start : span.end] == span.surface


def test_exactly_one_document_carries_a_corrected_surface(carmen_docs):
    """§9.7: one span in 8,231, and the correction is recorded on the document.

    Recorded rather than silent for the reason GraSCCo's `bom_clipped_spans` is: a
    correction only the loader's source states is one no result file can be audited
    against.
    """
    corrected = {
        doc.doc_id: doc.meta["surface_corrected_spans"]
        for doc in carmen_docs
        if "surface_corrected_spans" in doc.meta
    }
    assert corrected == {DEFECT_DOC: [DEFECT_INDEX]}
    assert set(KNOWN_SURFACE_DEFECTS) == {(DEFECT_DOC, DEFECT_INDEX)}
    assert KNOWN_SURFACE_DEFECTS[(DEFECT_DOC, DEFECT_INDEX)] == DEFECT_PIN


def test_the_pinned_span_is_the_span_the_pin_describes(carmen_docs):
    """The pin's type and both offsets, against the loaded document.

    The offsets are what §9.7 decided to keep, so they are what is asserted; the text
    at them is not quoted here or anywhere. Length only, because the length is the one
    thing that makes "a four-character date field" checkable without the characters.
    """
    doc = next(d for d in carmen_docs if d.doc_id == DEFECT_DOC)
    span = doc.spans[DEFECT_INDEX]
    subtype, start, end = DEFECT_PIN
    assert (span.subtype, span.start, span.end) == (subtype, start, end)
    assert span.phi_type == "DATE"
    assert end - start == 4
    assert len(span.surface) == 4


def test_the_other_spans_in_the_pinned_document_are_untouched(carmen_docs):
    """The correction is one index, not a document-wide tolerance."""
    doc = next(d for d in carmen_docs if d.doc_id == DEFECT_DOC)
    assert doc.meta["surface_corrected_spans"] == [DEFECT_INDEX]
    assert len(doc.spans) > DEFECT_INDEX


def test_no_document_carries_a_bom(carmen_docs):
    """§9.7: 0 of 2,000. The arithmetic stays in the loader; the corpus does not need it.

    `test_a_bom_shifts_every_offset_in_the_document` below is what keeps that arithmetic
    from being dead code, because this corpus cannot exercise it.
    """
    assert sum(1 for doc in carmen_docs if doc.had_bom) == N_BOM_DOCS
    assert not any(doc.text.startswith("﻿") for doc in carmen_docs)


def test_the_unread_text_copy_is_byte_identical_to_the_one_that_is_read(
    carmen_unsplit_loader, phi_dir
):
    """What licenses reading only the `.txt` beside the `.ann` (2,000 of 2,000).

    The release ships the same texts twice. The loader reads the copy brat pairs with
    the annotations and states this measurement as the reason; if the two copies ever
    diverged, an offset would index one text while a detector was pointed at the other.
    Compared as bytes and never decoded into an assertion.
    """
    other = carmen_unsplit_loader.root / UNREAD_TEXT_DIR / VARIANT
    compared = 0
    for path in sorted(phi_dir.glob("*.txt")):
        twin = other / path.name
        assert twin.is_file(), path.name
        assert path.read_bytes() == twin.read_bytes(), path.name
        compared += 1
    assert compared == N_DOCS


# ─── the strata the frozen split will be built on (§9.5) ────────────────────


def test_language_labels_are_the_corpus_composition(carmen_docs):
    """es 1,697 / bi 264 / cat 39, and the label is `language_label` and not `lang`.

    The name is load-bearing: `bi` is not a `naming.yaml` language and there is no
    `rules/bi.yaml`, so a document handing this value to a rule path would ask for a
    file that cannot exist (§5.6, §8.4). Asserting the key is asserting that.
    """
    counts = collections.Counter(doc.meta["language_label"] for doc in carmen_docs)
    assert dict(counts) == LANGUAGE_DOCS
    assert set(counts) == set(LANGUAGE_LABELS)
    assert sum(counts.values()) == N_DOCS
    assert all("lang" not in doc.meta for doc in carmen_docs)


def test_document_type_tokens_are_the_corpus_composition(carmen_docs):
    """IR 1,201 / IA 617 / IT 172 / CC 5 / IE 5, under `filename_doctype`.

    Not `document_type`: that axis is derived from text cues by `doctype.py` and
    `es-carmen` declares none, and 789 of these units are clinical sections rather than
    whole notes (§8.5). Two different things with one plausible name is how a
    stratification ends up over the wrong variable.
    """
    counts = collections.Counter(doc.meta["filename_doctype"] for doc in carmen_docs)
    assert dict(counts) == DOCTYPE_DOCS
    assert set(counts) == set(DOCTYPE_TOKENS)
    assert sum(counts.values()) == N_DOCS
    assert all("document_type" not in doc.meta for doc in carmen_docs)


def test_the_unit_is_sometimes_a_section_and_the_count_is_known(carmen_docs):
    """§8.5: 789 sections, 9 section tokens, and 1,211 whole notes.

    The figure §9.5's group decision rests on — a *document*-disjoint split over units
    that are partly sections needs the section-bearing count to be a measured number
    rather than an impression.
    """
    sections = [doc.meta["filename_section"] for doc in carmen_docs]
    named = [token for token in sections if token is not None]
    assert len(named) == N_SECTION_UNITS
    assert len(set(named)) == N_SECTION_TOKENS
    assert len(sections) - len(named) == N_DOCS - N_SECTION_UNITS


def test_the_doctype_by_language_cross_tabulation_has_a_single_document_cell(
    carmen_docs,
):
    """The measurement the split's small-cell rule exists for (§9.5).

    Twelve of the fifteen possible cells are non-empty and the smallest holds **one**
    document, so a proportional three-way split of every cell is not available: at
    0.60/0.20/0.20 a cell needs five documents before each fold can receive one. This
    is asserted before `splits/es-carmen.json` is frozen, on purpose — the deterministic
    rule is written against these numbers, and the achieved composition recorded in the
    file is only meaningful if the requested one is pinned somewhere that fails when the
    corpus moves.
    """
    cross = collections.Counter(
        (doc.meta["filename_doctype"], doc.meta["language_label"])
        for doc in carmen_docs
    )
    assert dict(cross) == CROSS
    assert sum(cross.values()) == N_DOCS
    assert len(cross) == 12
    assert min(cross, key=lambda cell: (cross[cell], cell)) == SMALLEST_CELL
    assert cross[SMALLEST_CELL] == 1
    # And the margins agree with the two one-dimensional tests above, so the three
    # cannot drift into describing different corpora.
    for doctype, total in DOCTYPE_DOCS.items():
        assert sum(n for (dt, _), n in cross.items() if dt == doctype) == total
    for language, total in LANGUAGE_DOCS.items():
        assert sum(n for (_, lang), n in cross.items() if lang == language) == total


def test_sparsity_has_two_readings_and_both_are_measured(carmen_docs):
    """461 documents with no span, 470 with no *in-scope* span.

    The nine-document difference is the distinction `sparsity_counts_excluded_spans`
    exists to protect: a note whose only span is §9.1-excluded carries no gold, and
    counting it as though it did is what put 727 into `splits/ko-surro.json`.
    """
    assert sum(1 for doc in carmen_docs if not doc.spans) == N_DOCS_WITH_NO_SPAN
    assert (
        sum(1 for doc in carmen_docs if not doc.in_scope_spans)
        == N_DOCS_WITH_NO_IN_SCOPE_SPAN
    )
    assert N_DOCS_WITH_NO_IN_SCOPE_SPAN > N_DOCS_WITH_NO_SPAN


def test_the_concept_layer_flag_describes_the_directory(carmen_docs, phi_dir):
    """500 flagged, and the same 500 files under `ner/` — recounted here.

    This is the one claim `CARMEN1_mappings.tsv` makes that is checkable against the
    tree, which makes it the evidence that the file describes *this* release rather
    than another one. The language label in the same rows is what the frozen split
    stratifies on, so that evidence is not incidental.
    """
    flagged = {doc.doc_id for doc in carmen_docs if doc.meta["has_concept_layer"]}
    present = {
        path.stem
        for path in (phi_dir.parent / CONCEPT_LAYER).glob("*.ann")
    }
    assert len(flagged) == N_CONCEPT_FLAGGED
    assert flagged == present


def test_the_digest_covers_the_language_label(carmen_docs, carmen_unsplit_loader):
    """The split's stratum is hashed, not just the two files a detector would read.

    A release that reshuffled `CARMEN1_mappings.tsv` would leave every `.ann` and `.txt`
    untouched, so the default digest would verify while the composition recorded in the
    split file became false. Asserted by naming the part, not by hashing twice: the
    payload is a canonical `key=value` rendering so that what is pinned is the two
    values the split reads and not the file's column order.
    """
    doc = carmen_docs[0]
    parts = dict(carmen_unsplit_loader.digest_parts(doc))
    key = f"{MAPPINGS.name}:{doc.doc_id}"
    assert set(parts) == {f"{doc.doc_id}.ann", f"{doc.doc_id}.txt", key}
    assert parts[key] == (
        f"language={doc.meta['language_label']}\n"
        f"concept_layer={doc.meta['has_concept_layer']}\n"
    ).encode("utf-8")


def test_source_files_returns_the_pair_and_nothing_else(carmen_docs, carmen_unsplit_loader):
    doc = carmen_docs[0]
    paths = carmen_unsplit_loader.source_files(doc.doc_id)
    assert [path.name for path in paths] == [f"{doc.doc_id}.ann", f"{doc.doc_id}.txt"]


def test_an_unknown_doc_id_has_no_files(carmen_unsplit_loader):
    with pytest.raises(CorpusError, match="no files for doc_id"):
        carmen_unsplit_loader.source_files("CARMEN-I_IR_999999")


# ─── the split file is the only authority on the fold ───────────────────────


def test_the_layout_encodes_no_fold(carmen_docs):
    """Nothing in the tree says which fold a document is in, and no key pretends to."""
    assert CarmenLoader.fold_dirs == {}
    assert {doc.split for doc in carmen_docs} == {None}


def test_calling_fold_roots_is_refused():
    """A corpus with no fold directories must not answer a question about them.

    Returning `{}` would make a fold-directory read iterate over nothing and load an
    empty corpus, which is the failure that looks like a clean run.
    """
    with pytest.raises(CorpusError, match="does not encode the fold"):
        CarmenLoader(use_split_file=False).fold_roots()


def test_there_is_no_patient_key(carmen_docs, carmen_unsplit_loader):
    """§8.5: no patient, encounter or record identifier exists in the release.

    So §9.5's first branch does not apply and the group is the document. Asserted as a
    refusal rather than as a `None`: a `patient_key()` that returned something would let
    a caller build patient-disjoint groups out of a value that is not a patient.
    """
    assert CarmenLoader.has_patient_key is False
    assert not CarmenLoader.patient_key_source
    with pytest.raises(CorpusError):
        carmen_unsplit_loader.patient_key(carmen_docs[0])


def test_the_split_file_is_not_frozen_yet():
    """The state these constants describe, asserted so it cannot change silently.

    When `splits/es-carmen.json` is frozen this test fails, and what it is asking for is
    the work the other four loader files carry and this one does not: a `carmen_loader`
    fixture, a `test_only_the_unsealed_folds_load`, and corpus-wide figures read from
    the file instead of recounted. Until then these tests see all 2,000 documents, and a
    reader needs to know that from the tests rather than from the git log.
    """
    assert not (Path(ROOT) / "splits" / "es-carmen.json").exists()


# ─── failure modes, on synthetic trees ──────────────────────────────────────
# Written into `tmp_path`, never into the corpus, with invented Spanish text. That is
# what lets these tests run on a machine with no CARMEN-I checkout, and it is also what
# makes the "no surface in the message" tests below meaningful rather than accidental:
# the tokens they look for exist only in this file.

SYNTHETIC_TEXT = "Nota inventada: fecha 01/01/2000 y edad 40 anos.\n"
DATE = "01/01/2000"
DATE_START = SYNTHETIC_TEXT.index(DATE)
DATE_END = DATE_START + len(DATE)


def ann_line(tag_id, tag, start, end, surface):
    return f"{tag_id}\t{tag} {start} {end}\t{surface}"


def date_line(tag_id="T1", tag="FECHAS", start=DATE_START, end=DATE_END, surface=None):
    """One well-formed annotation over the synthetic date, or a variant of it."""
    if surface is None:
        surface = SYNTHETIC_TEXT[start:end]
    return ann_line(tag_id, tag, start, end, surface)


def schema_text(types=None):
    """An `annotation.conf` declaring exactly the types this loader knows.

    Generated from the loader's own constants, which is fine here and would not be in
    `test_the_declared_schema_is_exactly_what_the_loader_knows`: the subject of these
    trees is some other behaviour, and a hand-written copy of 35 type names would fail
    for a reason that has nothing to do with the test it is in.
    """
    if types is None:
        types = set(TYPE_MAP) | set(EXCLUDED_TYPES) | set(CONCEPT_TYPES)
    body = "\n".join(sorted(types))
    return f"# synthetic\n{SCHEMA_SECTION}\n{body}\n\n[relations]\n"


def write_root(
    path,
    docs=(("CARMEN-I_IR_1", "es", False, None),),
    *,
    types=None,
    text=SYNTHETIC_TEXT,
    mappings_rows=None,
    concept_stems=None,
    fields=MAPPINGS_FIELDS,
):
    """A synthetic release holding `docs`, in the layout the loader expects.

    `docs` is a sequence of `(doc_id, language, has_concepts, lines)`, where `lines` is
    `None` for the one well-formed date annotation. `mappings_rows`, `fields`, `types`
    and `concept_stems` override what the release *declares*, which is how the tests
    below make the declarations disagree with the tree.
    """
    phi = path / ANN_ROOT / VARIANT / PHI_LAYER
    concept = path / ANN_ROOT / VARIANT / CONCEPT_LAYER
    phi.mkdir(parents=True, exist_ok=True)
    concept.mkdir(parents=True, exist_ok=True)
    (path / SCHEMA).write_text(schema_text(types), encoding="utf-8")

    for doc_id, _language, _flag, lines in docs:
        (phi / f"{doc_id}.txt").write_text(text, encoding="utf-8")
        body = [date_line()] if lines is None else list(lines)
        (phi / f"{doc_id}.ann").write_text(
            "".join(line + "\n" for line in body), encoding="utf-8"
        )

    if concept_stems is None:
        concept_stems = [doc_id for doc_id, _, flag, _ in docs if flag]
    for stem in concept_stems:
        (concept / f"{stem}.ann").write_text("", encoding="utf-8")
        (concept / f"{stem}.txt").write_text(text, encoding="utf-8")

    if mappings_rows is None:
        mappings_rows = [
            (doc_id, language, str(flag)) for doc_id, language, flag, _ in docs
        ]
    rows = ["\t".join(fields)] + ["\t".join(row) for row in mappings_rows]
    (path / MAPPINGS).write_text(
        "".join(row + "\n" for row in rows), encoding="utf-8"
    )
    return path


def loader_on(path):
    """A loader reading one synthetic root and no split file."""
    return CarmenLoader(root=path, use_split_file=False)


def test_a_synthetic_document_loads(tmp_path):
    """The positive control: without it every failure test below could pass vacuously."""
    docs = loader_on(write_root(tmp_path / "root")).load()
    assert len(docs) == 1
    assert docs[0].doc_id == "CARMEN-I_IR_1"
    assert len(docs[0].spans) == 1
    assert docs[0].spans[0].phi_type == "DATE"
    assert docs[0].meta["language_label"] == "es"
    assert docs[0].meta["filename_doctype"] == "IR"
    assert docs[0].meta["filename_section"] is None
    assert "surface_corrected_spans" not in docs[0].meta


def test_a_surface_that_does_not_match_the_text_raises_and_quotes_neither(tmp_path):
    """The offset check, and CLAUDE.md's message rule, in one test.

    Both strings here are invented in this file. If either reaches the message, the
    loader quotes span text — and a loader that does that for a synthetic corpus does it
    for the DUA-restricted one. Offsets and lengths only.
    """
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, [date_line(surface="99/99/9999")])],
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    message = str(exc.value)
    assert "99/99/9999" not in message
    assert DATE not in message
    assert f"[{DATE_START}, {DATE_END})" in message


def test_an_offset_past_the_end_of_the_document_raises(tmp_path):
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, [date_line(end=9999, surface=DATE)])],
    )
    with pytest.raises(CorpusError, match="characters"):
        loader_on(root).load()


def test_a_malformed_offset_field_is_not_quoted(tmp_path):
    """A multi-fragment or otherwise malformed second field can hold document text.

    brat's `start end;start end` form does not occur in this release and has no scoring
    rule, so it raises. The message reports how many parts the field had and how long it
    was; `meddocan.py` prints the field itself, which is safe there because MEDDOCAN is
    synthetic and is not safe here.
    """
    root = write_root(
        tmp_path / "root",
        [
            (
                "CARMEN-I_IR_1",
                "es",
                False,
                [f"T1\tFECHAS {DATE_START} 26;28 {DATE_END}\t{DATE}"],
            )
        ],
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    message = str(exc.value)
    assert "Multi-fragment" in message
    assert "26;28" not in message
    assert DATE not in message
    assert "4 space-separated parts" in message


def test_a_line_with_the_wrong_field_count_raises_without_quoting_it(tmp_path):
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, [f"T1\tFECHAS {DATE_START} {DATE_END}"])],
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "2 tab-separated fields" in str(exc.value)
    assert DATE not in str(exc.value)


def test_a_non_text_bound_annotation_raises(tmp_path):
    """Relation and attribute lines need a scoring decision before they can be loaded."""
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, [date_line(tag_id="R1")])],
    )
    with pytest.raises(CorpusError, match="'R' annotation"):
        loader_on(root).load()


def test_non_integer_offsets_raise(tmp_path):
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, [f"T1\tFECHAS x y\t{DATE}"])],
    )
    with pytest.raises(CorpusError, match="non-integer offsets"):
        loader_on(root).load()


def test_a_concept_type_in_the_phi_layer_raises(tmp_path):
    """The layer boundary, enforced at the line rather than at the directory.

    The two layers are declared in one `annotation.conf` with no marker between them, so
    "this file is the PHI layer" is a claim about the directory and nothing else checks
    it. Loading a concept span would add 26,360 spans to a gold set of 8,231.
    """
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, [date_line(tag="ENFERMEDAD")])],
    )
    with pytest.raises(CorpusError, match="medical-concept layer's type"):
        loader_on(root).load()


def test_a_declared_type_the_loader_does_not_know_raises(tmp_path):
    """The schema check's first direction: a release adding an undecided type."""
    types = set(TYPE_MAP) | set(EXCLUDED_TYPES) | set(CONCEPT_TYPES) | {"TIPO_NUEVO"}
    root = write_root(tmp_path / "root", types=types)
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "Declared but not decided: ['TIPO_NUEVO']" in str(exc.value)


def test_a_type_the_loader_knows_and_the_release_does_not_declare_raises(tmp_path):
    """The quieter direction: the map was built against a different release.

    Every count in §9.0's block would belong to that other release, and nothing in a
    load would say so — the type has no instances here either way.
    """
    types = (set(TYPE_MAP) | set(EXCLUDED_TYPES) | set(CONCEPT_TYPES)) - {"NUMERO_FAX"}
    root = write_root(tmp_path / "root", types=types)
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "Known but not declared: ['NUMERO_FAX']" in str(exc.value)


def test_a_missing_schema_raises(tmp_path):
    root = write_root(tmp_path / "root")
    (root / SCHEMA).unlink()
    with pytest.raises(CorpusError, match="declares no schema"):
        loader_on(root).load()


def test_a_document_with_no_mappings_row_raises(tmp_path):
    """No default language label: a document with no row has no stratum.

    Inventing one would put a stratification the split file asserts on top of a label
    nobody recorded.
    """
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, None), ("CARMEN-I_IR_2", "es", False, None)],
        mappings_rows=[("CARMEN-I_IR_1", "es", "False")],
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    message = str(exc.value)
    assert "1 documents have no row" in message
    assert "no default for it" in message


def test_a_mappings_row_with_no_document_raises(tmp_path):
    root = write_root(
        tmp_path / "root",
        mappings_rows=[("CARMEN-I_IR_1", "es", "False"), ("CARMEN-I_IR_2", "es", "False")],
    )
    with pytest.raises(CorpusError, match="1 rows have no document"):
        loader_on(root).load()


def test_a_repeated_mappings_row_raises(tmp_path):
    """Two rows for one document is two language labels, and `csv` would keep the last."""
    root = write_root(
        tmp_path / "root",
        mappings_rows=[("CARMEN-I_IR_1", "es", "False"), ("CARMEN-I_IR_1", "cat", "False")],
    )
    with pytest.raises(CorpusError, match="repeats a document id"):
        loader_on(root).load()


def test_an_unknown_language_label_raises(tmp_path):
    """A fourth label is a stratum the frozen split does not describe."""
    root = write_root(
        tmp_path / "root", mappings_rows=[("CARMEN-I_IR_1", "eu", "False")]
    )
    with pytest.raises(CorpusError, match="not one of"):
        loader_on(root).load()


def test_a_non_boolean_concept_flag_raises(tmp_path):
    root = write_root(
        tmp_path / "root", mappings_rows=[("CARMEN-I_IR_1", "es", "true")]
    )
    with pytest.raises(CorpusError, match="ner_annotations"):
        loader_on(root).load()


def test_reordered_mappings_columns_raise(tmp_path):
    """Read by name, and the names are checked: a reordered release is a new file."""
    root = write_root(
        tmp_path / "root",
        fields=("filename", "ner_annotations", "language"),
        mappings_rows=[("CARMEN-I_IR_1", "False", "es")],
    )
    with pytest.raises(CorpusError, match="has columns"):
        loader_on(root).load()


def test_a_missing_mappings_file_raises(tmp_path):
    root = write_root(tmp_path / "root")
    (root / MAPPINGS).unlink()
    with pytest.raises(CorpusError, match="stratification has no variable"):
        loader_on(root).load()


def test_a_concept_flag_the_directory_does_not_support_raises(tmp_path):
    """The flag is not read for anything, which is exactly why it is checked.

    It is the one claim the mappings file makes that can be verified against the tree,
    so it is the evidence that the file and the release are the same version — and the
    language label in the same rows is what the split rests on.
    """
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", True, None)],
        concept_stems=[],
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "1 flagged documents have no file" in str(exc.value)


def test_a_concept_file_no_row_flags_raises(tmp_path):
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IR_1", "es", False, None)],
        concept_stems=["CARMEN-I_IR_1"],
    )
    with pytest.raises(CorpusError, match="1 files are unflagged"):
        loader_on(root).load()


def test_a_missing_concept_directory_raises(tmp_path):
    root = write_root(tmp_path / "root")
    for path in (root / ANN_ROOT / VARIANT / CONCEPT_LAYER).iterdir():
        path.unlink()
    (root / ANN_ROOT / VARIANT / CONCEPT_LAYER).rmdir()
    with pytest.raises(CorpusError, match="describes nothing"):
        loader_on(root).load()


def test_a_document_with_no_text_file_raises(tmp_path):
    root = write_root(tmp_path / "root")
    (root / ANN_ROOT / VARIANT / PHI_LAYER / "CARMEN-I_IR_1.txt").unlink()
    with pytest.raises(CorpusError, match="no matching .txt"):
        loader_on(root).load()


def test_an_empty_phi_layer_raises_and_points_at_the_configuration(tmp_path):
    root = write_root(tmp_path / "root")
    for path in (root / ANN_ROOT / VARIANT / PHI_LAYER).iterdir():
        path.unlink()
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "data_paths.local.yaml" in str(exc.value)


def test_an_unparsable_doc_id_raises(tmp_path):
    """No residual stratum. A document in an `unparsed` band would be stratified on a
    label meaning "this code did not understand the id", and the split file would record
    that as a composition."""
    root = write_root(tmp_path / "root", [("CARMEN_1", "es", False, None)])
    with pytest.raises(CorpusError, match="does not parse as"):
        loader_on(root).load()


def test_an_unknown_document_type_token_raises(tmp_path):
    root = write_root(tmp_path / "root", [("CARMEN-I_ZZ_1", "es", False, None)])
    with pytest.raises(CorpusError, match="document-type token"):
        loader_on(root).load()


def test_a_section_bearing_doc_id_parses_into_three_parts(tmp_path):
    """The section tokens contain `_`, so the id is matched and not split on `_`.

    All three parts, because `filename_index` is what §9.5 step 1's candidate key for this
    corpus pairs with the doctype (`src/split.py`'s `_carmen_candidate_key`), and a
    section token that swallowed the trailing number would make every document its own
    candidate group without the audit's count changing shape.
    """
    root = write_root(
        tmp_path / "root",
        [("CARMEN-I_IA_EXPLORACION_COMPLEMENTARIA_7", "cat", False, None)],
    )
    doc = loader_on(root).load()[0]
    assert doc.meta["filename_doctype"] == "IA"
    assert doc.meta["filename_section"] == "EXPLORACION_COMPLEMENTARIA"
    assert doc.meta["filename_index"] == 7


def test_a_sectionless_doc_id_still_carries_the_index(tmp_path):
    """`filename_section` is `None` and the index is the number, not a re-parse of the id.

    The two id shapes have to agree on what `filename_index` means, or the candidate key
    pairs a sectionless letter with nothing.
    """
    root = write_root(tmp_path / "root", [("CARMEN-I_IR_413", "es", False, None)])
    doc = loader_on(root).load()[0]
    assert doc.meta["filename_section"] is None
    assert doc.meta["filename_index"] == 413


def test_a_bom_shifts_every_offset_in_the_document(tmp_path):
    """The arithmetic the real corpus cannot exercise (0 of 2,000 documents).

    Kept in the loader and tested here for MEDDOCAN's and GraSCCo's reason: a release
    that gained a BOM would otherwise move every span in that document by one with
    nothing saying so. The correction is arithmetic and uniform rather than a re-search
    for the surface, which is what stops it from silently repairing genuinely wrong
    offsets.
    """
    root = write_root(
        tmp_path / "root",
        [
            (
                "CARMEN-I_IR_1",
                "es",
                False,
                [date_line(start=DATE_START + 1, end=DATE_END + 1, surface=DATE)],
            )
        ],
        text="﻿" + SYNTHETIC_TEXT,
    )
    doc = loader_on(root).load()[0]
    assert doc.had_bom is True
    assert not doc.text.startswith("﻿")
    assert (doc.spans[0].start, doc.spans[0].end) == (DATE_START, DATE_END)


# ─── the pin, on synthetic trees ────────────────────────────────────────────
# §9.7's entry excuses one span in one document. Its two refusals are what make it a
# pin rather than a tolerance, and both are exercised here — on a synthetic tree, by
# patching the table, so that neither test depends on the real document's text.


def defect_root(tmp_path, *, lines, doc_id=DEFECT_DOC):
    return write_root(tmp_path / "root", [(doc_id, "es", False, lines)])


def test_a_pinned_span_whose_surface_now_matches_raises(tmp_path, monkeypatch):
    """The release fixed the defect: the entry must be removed, not left standing.

    A standing permission to overwrite a surface corrects the *next* data error in that
    document instead of reporting it, which is the failure this refusal is for.
    """
    monkeypatch.setattr(
        "src.corpora.carmen.KNOWN_SURFACE_DEFECTS",
        {(DEFECT_DOC, 0): ("FECHAS", DATE_START, DATE_END)},
    )
    root = defect_root(tmp_path, lines=[date_line()])
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "release has fixed the defect" in str(exc.value)


def test_a_pin_that_names_a_different_span_raises(tmp_path, monkeypatch):
    """The pin must not slide onto another span if the release is reordered."""
    monkeypatch.setattr(
        "src.corpora.carmen.KNOWN_SURFACE_DEFECTS",
        {(DEFECT_DOC, 0): ("EDAD_SUJETO_ASISTENCIA", DATE_START, DATE_END)},
    )
    root = defect_root(tmp_path, lines=[date_line(surface="99/99/9999")])
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "must not be allowed to slide onto another" in str(exc.value)


def test_a_pin_past_the_end_of_the_document_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.corpora.carmen.KNOWN_SURFACE_DEFECTS",
        {(DEFECT_DOC, 7): ("FECHAS", DATE_START, DATE_END)},
    )
    root = defect_root(tmp_path, lines=[date_line()])
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "span that no longer exists" in str(exc.value)


def test_the_pin_corrects_the_named_span_and_records_it(tmp_path, monkeypatch):
    """The positive case, and the only span the surface is taken from the text for.

    The offsets are kept — §9.7's three witnesses say the surface field is what this
    release got wrong — so the span that comes out spans the same characters and the
    document records which index was corrected.
    """
    monkeypatch.setattr(
        "src.corpora.carmen.KNOWN_SURFACE_DEFECTS",
        {(DEFECT_DOC, 1): ("FECHAS", DATE_START, DATE_END)},
    )
    root = defect_root(
        tmp_path,
        lines=[
            ann_line("T1", "EDAD_SUJETO_ASISTENCIA", 38, 40, SYNTHETIC_TEXT[38:40]),
            date_line(tag_id="T2", surface="99/99/9999"),
        ],
    )
    doc = loader_on(root).load()[0]
    assert doc.meta["surface_corrected_spans"] == [1]
    assert (doc.spans[1].start, doc.spans[1].end) == (DATE_START, DATE_END)
    assert doc.spans[1].surface == doc.text[DATE_START:DATE_END]
    assert doc.spans[0].surface == doc.text[38:40]


def test_a_mismatch_the_pin_does_not_name_raises(tmp_path, monkeypatch):
    """One span is excused; the second mismatch in the same document is still an error."""
    monkeypatch.setattr(
        "src.corpora.carmen.KNOWN_SURFACE_DEFECTS",
        {(DEFECT_DOC, 1): ("FECHAS", DATE_START, DATE_END)},
    )
    root = defect_root(
        tmp_path,
        lines=[
            ann_line("T1", "EDAD_SUJETO_ASISTENCIA", 38, 40, "77"),
            date_line(tag_id="T2", surface="99/99/9999"),
        ],
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    message = str(exc.value)
    assert "[38, 40)" in message
    assert "77" not in message


def test_the_pinned_table_names_one_span(tmp_path):
    """The table itself, unpatched: one entry, and it is §9.7's.

    Asserted separately from the real-corpus test so that a second entry appearing is a
    failure even on a machine with no CARMEN-I checkout — an added pin is a decision
    DESIGN has to carry, and the cheapest place for it to go unnoticed is a tree nobody
    can load.
    """
    assert KNOWN_SURFACE_DEFECTS == {(DEFECT_DOC, DEFECT_INDEX): DEFECT_PIN}


# ─── the seal ───────────────────────────────────────────────────────────────


def test_the_sealed_root_is_unreachable_without_authorisation(carmen_unsplit_loader):
    """`sealed_reachable()` is the only permission, and an ordinary load has none.

    Asserted without requiring a sealed root, because there is not one yet: whatever
    `sealed_root()` answers, an unauthorised loader's `source_roots()` is its own root
    and nothing else.
    """
    assert carmen_unsplit_loader.sealed_reachable() is None
    assert carmen_unsplit_loader.source_roots() == [carmen_unsplit_loader.root]


def test_an_empty_sealed_read_raises_rather_than_returning_the_rest(tmp_path):
    """A sealed read that reached no sealed document is a failure, not a smaller corpus.

    The access is in `results/sealed_eval_log.md` before anything is opened, so a read
    that quietly returned the unsealed documents would produce numbers from the wrong
    data under a log row saying the test fold was evaluated. This is the guard
    `kosurro.py` gained on 2026-09-22 and that `base.fold_roots`, `grascco.py` and
    `endeid.py` still owe; it is written here with the loader rather than after it.

    The sealed root is a well-formed release with an empty PHI layer, which is the shape
    a broken seal actually produces. `_sealed_ok` is set directly and `sealed_root` is
    patched for this test only: the subject is `_read`'s invariant, not the
    authorisation that precedes it, and authorising properly would mean appending to the
    real log.
    """
    corpus = write_root(tmp_path / "root")
    sealed = write_root(tmp_path / "sealed", docs=())
    loader = loader_on(corpus)
    loader._sealed_ok = True

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(base, "sealed_root", lambda corpus_id: sealed)
        assert loader.sealed_reachable() == sealed
        with pytest.raises(SealError) as exc:
            loader.load()
    assert "did not complete" in str(exc.value)


def test_a_sealed_root_that_holds_documents_is_read(tmp_path):
    """The positive control for the guard above, and for `source_files` across roots.

    Without it, `test_an_empty_sealed_read...` would pass on a loader that never opened
    a sealed root at all. The second assertion is the other half of the same property:
    the permission is spent by the read, so `source_files` can no longer reach the
    sealed root afterwards — which is why §6.2 records the per-document hashes *before*
    the seal and not after.
    """
    corpus = write_root(tmp_path / "root")
    sealed = write_root(tmp_path / "sealed", [("CARMEN-I_IR_2", "cat", False, None)])
    loader = loader_on(corpus)
    loader._sealed_ok = True

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(base, "sealed_root", lambda corpus_id: sealed)
        docs = loader.load()
        assert [doc.doc_id for doc in docs] == ["CARMEN-I_IR_1", "CARMEN-I_IR_2"]
        assert loader.sealed_reachable() is None
        with pytest.raises(CorpusError, match="no files for doc_id"):
            loader.source_files("CARMEN-I_IR_2")


def test_a_document_under_two_roots_is_refused(tmp_path):
    """The same id in the corpus and the sealed root is a seal that copied instead of
    moving, and §12 requires a rewrite rather than a copy. `source_files` refuses it
    because a digest over four files is not the digest the split file recorded."""
    corpus = write_root(tmp_path / "root")
    sealed = write_root(tmp_path / "sealed")
    loader = loader_on(corpus)
    loader._sealed_ok = True

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(base, "sealed_root", lambda corpus_id: sealed)
        with pytest.raises(CorpusError, match="more than one root"):
            loader.source_files("CARMEN-I_IR_1")
