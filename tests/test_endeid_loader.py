"""Tests for the `en-deid` loader and its split file.

Same two kinds as `tests/test_meddocan_loader.py` and `tests/test_grascco_loader.py`:

  - **Recount tests** re-derive the totals from the shipped bytes by a method that shares
    no code with the loader — a regex sweep over the three files that never enters the
    framing logic, the positional cross-check or the type map.
  - **Constant tests** pin the numbers DESIGN §9.0 reports, so a re-released corpus fails
    here instead of quietly moving every published figure.

**What is different here, and it is why this file is not a copy of either.**

  - A document is a *record inside a file*, so there is nothing to hash per document and
    nothing to move per fold. `source_files()` refuses; `digest_parts()` answers, and the
    test below recomputes the frozen split file's digests through it.
  - `id-phi.phrase` records the surface beside the offsets, so unlike GraSCCo's CAS this
    corpus supports MEDDOCAN's real offset assertion. It also **breaks** GraSCCo's
    whitespace-edge invariant: four reachable gold surfaces begin or end on whitespace.
    That counter-fact is asserted, not worked around — a copied invariant would have
    rejected the corpus.
  - Nine records carry no `id.deid` header at all. They are dropped at document level and
    counted in `uncovered`, which is a different thing from a record with zero spans
    (1,353 reachable records assert no PHI), and the difference is asserted both ways.

**These tests see 1,941 documents, not 2,425.** The test fold was sealed on 2026-09-21
after the split file was frozen (DESIGN §6.2), so the division the other two loader files
make applies here too: the recount covers what the loader can reach, and the corpus-wide
figures are `FULL_*`, read from `splits/en-deid.json`.
`test_the_split_file_accounts_for_the_seal` is what keeps those credible rather than
merely unfalsifiable — the visible constants plus the split file's test block must equal
them, per type as well as in total.

One of the nine uncovered records belongs to a patient in the test fold, so it went behind
the seal with its patient's other notes even though it carries no gold. Eight are
reachable. `test_the_ninth_uncovered_record_went_with_its_patient` states that as an
arithmetic identity against the frozen file rather than by naming the sealed record.

    python3 -m pytest tests/test_endeid_loader.py -q
"""
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import split  # noqa: E402
from src.corpora import CorpusError, base  # noqa: E402
from src.corpora.base import Document  # noqa: E402
from src.corpora.endeid import (  # noqa: E402
    EXCLUDED_TYPES,
    FILES,
    OFFSETS_FILE,
    TEXT_FILE,
    TYPE_MAP,
    TYPES_FILE,
    EndeidLoader,
)

# ─── expected values (DESIGN §9.0) ──────────────────────────────────────────

# What the loader can see: train+dev. Measured on 2026-09-21 with the seal in place,
# which is the only state these numbers describe.
N_DOCS = 1941
N_RECORDS_IN_TEXT = 1949  # loaded, plus the eight reachable records with no reference
N_SPANS = 1419
N_IN_SCOPE = 1419  # §9.1 has no counterpart in this release, so these are equal
N_DOCS_WITH_SPANS = 588
N_PATIENTS = 129
CANONICAL_COUNTS = {
    "NAME": 656,
    "DATE": 420,
    "LOCATION_AREA": 290,
    "CONTACT": 46,
    "AGE": 4,
    "OTHER": 3,
}
SUBTYPE_COUNTS = {
    "HCPName": 485,
    "RelativeProxyName": 122,
    "PTName": 48,
    "PTNameInitial": 1,
    "Date": 381,
    "DateYear": 39,
    "Location": 290,
    "Phone": 46,
    "Age": 4,
    "Other": 3,
}
#: Records with a body and no `id.deid` header, reachable after the seal. The ninth is
#: behind it; see the module docstring and the arithmetic test below.
UNCOVERED = [
    "107_8",
    "110_8",
    "136_20",
    "146_8",
    "147_8",
    "59_10",
    "94_1",
    "9_2",
]

# The whole corpus, which is what DESIGN §9.0 reports and the paper prints. Read from the
# frozen split file rather than recomputed: 484 documents are sealed, and a test that
# recomputed these would be reading the test fold to do it.
FULL_N_DOCS = 2425
FULL_N_RECORDS = 2434
FULL_N_SPANS = 1779
FULL_CANONICAL_COUNTS = {
    "NAME": 824,
    "DATE": 528,
    "LOCATION_AREA": 367,
    "CONTACT": 53,
    "AGE": 4,
    "OTHER": 3,
}
FULL_N_UNCOVERED = 9
FULL_N_PATIENTS = 163
CONSTRUCTED_SPLIT = {"train": 1456, "dev": 485, "test": 484}

#: The counter-fact to GraSCCo's invariant: gold surfaces that begin or end on
#: whitespace. Five corpus-wide, four reachable. Pinned by coordinate — offsets only,
#: never the surface (CLAUDE.md).
WHITESPACE_EDGED = [("33_14", 164, 174), ("41_12", 574, 578), ("48_2", 5, 14), ("8_1", 981, 986)]


# ─── fixtures ───────────────────────────────────────────────────────────────
# `endeid_present` / `endeid_sealed` / `endeid_loader` / `endeid_unsplit_loader` are in
# tests/conftest.py and are defined nowhere else. A local availability check here is the
# defect that shipped four times; see that file's header. The fixtures below construct
# from those and decide nothing about availability.


@pytest.fixture(scope="module")
def docs(endeid_unsplit_loader):
    """Every loaded record, once for this module.

    `load()` parses three files and asserts 1,419 offsets, and most tests here want the
    same result. The unsplit loader, so that a test about the split file is not reading
    the split file to ask its question.
    """
    return endeid_unsplit_loader.load()


@pytest.fixture(scope="module")
def record(endeid_present):
    """The frozen split file, parsed. No `try`: a file that does not parse is a defect."""
    return split.read(endeid_present)


@pytest.fixture(scope="module")
def reachable_files(endeid_unsplit_loader):
    """The three files under each root this loader may open, by name.

    Through `source_roots()` rather than a literal path, so this cannot reach the sealed
    root that an ordinary load refuses to open.
    """
    out: dict[str, list[Path]] = {name: [] for name in FILES}
    for root in endeid_unsplit_loader.source_roots():
        for name in FILES:
            out[name].append(root / name)
    return out


@pytest.fixture(scope="module")
def recount(reachable_files):
    """Re-derive the totals from the raw bytes, sharing no code with the loader.

    Three independent regex sweeps, no framing state machine and no positional
    cross-check: record openings in `id.text`, `Patient … Note …` headers in `id.deid`,
    and type counts from field 5 of `id-phi.phrase`. The phrase field is never split off
    — this file reads the same bytes the loader does and prints counts, so the maxsplit
    discipline of `tools/gold_provenance_check.py` applies here for the same reason.

    `test_the_recount_saw_the_corpus` asserts the totals, so a pattern that silently
    matched nothing fails there rather than passing here.
    """
    start_re = re.compile(r"^START_OF_RECORD=", re.M)
    header_re = re.compile(r"^Patient\s+\S+\s+Note\s+\S+\s*$", re.M)
    out = {"records": 0, "headers": 0, "spans": 0, "by_subtype": Counter()}
    for path in reachable_files[TEXT_FILE]:
        out["records"] += len(start_re.findall(path.read_text(encoding="utf-8")))
    for path in reachable_files[OFFSETS_FILE]:
        out["headers"] += len(header_re.findall(path.read_text(encoding="utf-8")))
    for path in reachable_files[TYPES_FILE]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            out["spans"] += 1
            out["by_subtype"][line.split(None, 5)[4]] += 1
    out["by_subtype"] = dict(out["by_subtype"])
    return out


# ─── the loader agrees with an independent recount ──────────────────────────


def test_loader_matches_independent_recount(docs, recount):
    assert len(docs) == recount["headers"]
    assert base.count_spans(docs) == recount["spans"]
    assert base.count_by_type(docs, canonical=False) == recount["by_subtype"]


def test_the_recount_saw_the_corpus(recount):
    """The three sweeps must have matched, or every comparison to them is vacuous."""
    assert recount["records"] == N_RECORDS_IN_TEXT
    assert recount["headers"] == N_DOCS
    assert recount["spans"] == N_SPANS


def test_the_text_file_holds_more_records_than_the_reference_covers(recount):
    """The nine-record gap is what `uncovered` is about, seen from the raw bytes."""
    assert recount["records"] - recount["headers"] == len(UNCOVERED)


# ─── the counts DESIGN §9.0 reports ─────────────────────────────────────────


def test_document_count(docs):
    assert len(docs) == N_DOCS


def test_document_ids_are_unique(docs):
    ids = [d.doc_id for d in docs]
    assert len(set(ids)) == len(ids)


def test_total_span_count(docs):
    assert base.count_spans(docs) == N_SPANS


def test_nothing_is_excluded(docs):
    """§9.1 has no counterpart in this release, so in-scope equals total.

    Asserted rather than assumed: `EXCLUDED_TYPES` being empty is a claim about the
    release's categories, and a type map that quietly gained an exclusion would change
    every denominator in the results.
    """
    assert EXCLUDED_TYPES == frozenset()
    assert sum(len(d.in_scope_spans) for d in docs) == N_IN_SCOPE == N_SPANS


def test_canonical_type_counts_match_design_table(docs):
    assert base.count_by_type(docs) == CANONICAL_COUNTS


def test_subtype_counts_match_design_table(docs):
    assert base.count_by_type(docs, canonical=False) == SUBTYPE_COUNTS


def test_every_mapped_source_type_occurs(docs):
    """All ten of the release's types are reachable, so none of the mapping is untested."""
    assert set(base.count_by_type(docs, canonical=False)) == set(TYPE_MAP)


def test_four_canonical_types_have_no_source_type(docs):
    """§9.0: silence, not zero. LOCATION_STREET, ORGANISATION, ID and PROFESSION.

    The consequence is recorded in DESIGN §9.0 and matters for how the arm is reported:
    a correct street or organisation detection here is a false positive by construction,
    so this corpus's aggregate precision is a lower bound. Pinned so that a later widening
    of the map has to change this test and read that paragraph.
    """
    absent = {"LOCATION_STREET", "ORGANISATION", "ID", "PROFESSION"}
    assert absent & set(TYPE_MAP.values()) == set()
    assert absent & set(base.count_by_type(docs)) == set()


def test_only_the_release_s_own_residual_maps_to_other(docs):
    """`OTHER` comes from `Other` and nothing else — no type is quietly parked there."""
    assert [k for k, v in TYPE_MAP.items() if v == "OTHER"] == ["Other"]


def test_date_year_is_counted_as_date_and_kept_as_a_subtype(docs):
    """§9.0's measured decision: 39 reachable standalone years, mapped rather than dropped.

    Both halves are asserted, because the decision is that the DATE denominator matches
    MEDDOCAN's FECHAS and GraSCCo's DATE *while* the distinction survives in `subtype`.
    Dropping either half silently changes what a cross-corpus DATE row means.
    """
    years = [s for d in docs for s in d.spans if s.subtype == "DateYear"]
    assert len(years) == SUBTYPE_COUNTS["DateYear"]
    assert {s.phi_type for s in years} == {"DATE"}


def test_the_undifferentiated_location_label_is_merged_down(docs):
    """§7.2 ground 2: one gold `Location` becomes `LOCATION_AREA` for this corpus only."""
    locations = [s for d in docs for s in d.spans if s.phi_type == "LOCATION_AREA"]
    assert {s.subtype for s in locations} == {"Location"}
    assert len(locations) == CANONICAL_COUNTS["LOCATION_AREA"]


# ─── offsets, and the invariant that does *not* hold here ────────────────────


def test_every_span_slices_back_to_its_recorded_surface(docs):
    """The check GraSCCo's CAS cannot support, re-run outside the loader.

    `assert_offsets()` already does this on every load; asserting it here as well is not
    redundant, because a mutation that weakened the loader's comparison would leave the
    load green and has to be caught by something that does not use it.
    """
    for doc in docs:
        for index, span in enumerate(doc.spans):
            assert doc.text[span.start : span.end] == span.surface, (
                f"{doc.doc_id} span {index} at [{span.start}, {span.end})"
            )


def test_every_span_lies_inside_its_body(docs):
    for doc in docs:
        for index, span in enumerate(doc.spans):
            assert 0 <= span.start < span.end <= len(doc.text), (
                f"{doc.doc_id} span {index} at [{span.start}, {span.end}) "
                f"in a body of {len(doc.text)} characters"
            )


def test_some_gold_surfaces_are_whitespace_edged_and_are_accepted(docs):
    """GraSCCo's invariant is false here, and that is the reason it is not asserted.

    Four reachable gold spans begin or end on whitespace (five corpus-wide). Copying
    GraSCCo's `test_no_gold_span_is_whitespace_edged` would have made the loader reject a
    correct reference — so the counter-fact is pinned instead, by offset. If a re-release
    trims them this test fails and the loader can then adopt the stricter check
    deliberately.
    """
    edged = sorted(
        (d.doc_id, s.start, s.end)
        for d in docs
        for s in d.spans
        if s.surface != s.surface.strip()
    )
    assert edged == sorted(WHITESPACE_EDGED)


# ─── coverage: no reference is not an empty reference ────────────────────────


def test_records_without_a_reference_are_dropped_and_counted(endeid_sealed, endeid_unsplit_loader):
    loaded = endeid_unsplit_loader.load()
    assert sorted(endeid_unsplit_loader.uncovered) == sorted(UNCOVERED)
    assert set(UNCOVERED).isdisjoint({d.doc_id for d in loaded})


def test_records_with_zero_spans_are_loaded(docs):
    """The other half of the distinction: an empty reference is an assertion of no PHI.

    1,353 reachable records assert that and are loaded. Dropping them too would remove
    every true negative from the corpus and make precision unmeasurable.
    """
    empty = [d for d in docs if not d.spans]
    assert len(empty) == N_DOCS - N_DOCS_WITH_SPANS
    assert empty, "a corpus with no PHI-free record cannot measure precision"


def test_documents_with_spans(docs):
    assert sum(1 for d in docs if d.spans) == N_DOCS_WITH_SPANS


# ─── the patient key (DESIGN §9.5's first branch) ────────────────────────────


def test_the_loader_declares_a_patient_key():
    assert EndeidLoader.has_patient_key is True
    assert EndeidLoader.patient_key_source


def test_the_patient_key_comes_from_meta_and_not_from_the_document_id(endeid_loader, docs):
    """Both halves: the key is the header's field, and it agrees with the composed id.

    The agreement is checked *here* rather than relied on in the loader — `patient_key`
    reads `meta` on purpose, so that the composition `{patient}_{note}` has one author.
    A test is the right place to confirm the two agree; a second decomposition in `src/`
    would be a second author.
    """
    for doc in docs:
        key = endeid_loader.patient_key(doc)
        assert key == doc.meta["patient_id"]
        assert doc.doc_id.startswith(f"{key}_")
        assert doc.doc_id.rsplit("_", 1)[0] == key


def test_patient_count(endeid_loader, docs):
    assert len({endeid_loader.patient_key(d) for d in docs}) == N_PATIENTS


def test_a_document_without_a_patient_id_is_refused(endeid_loader):
    """No fallback to the document id. A fallback is a document-random split announced
    as patient-disjoint, which is the partition CLAUDE.md forbids outright."""
    stray = Document(
        doc_id="1_1",
        corpus_id="en-deid",
        text="no meta",
        spans=[],
        split=None,
        had_bom=False,
        meta={},
    )
    with pytest.raises(CorpusError) as exc:
        endeid_loader.patient_key(stray)
    assert "patient_id" in str(exc.value)


def test_no_patient_has_notes_in_two_folds(record):
    """Patient-disjointness, read off the frozen file — the sealed fold included.

    This is the one property the seal makes uncheckable from the corpus and the whole
    point of the grouping, so it is asserted against the file, which covers all three
    folds. Decomposing the ids is admissible in a test for
    `test_the_patient_key_comes_from_meta_and_not_from_the_document_id`'s reason.
    """
    folds: dict[str, set[str]] = {}
    for doc_id, fold in split.fold_of(record).items():
        folds.setdefault(doc_id.rsplit("_", 1)[0], set()).add(fold)
    straddling = {p: sorted(f) for p, f in folds.items() if len(f) > 1}
    assert straddling == {}
    assert len(folds) == FULL_N_PATIENTS


# ─── what this loader refuses, and why ──────────────────────────────────────


def test_source_files_is_refused_and_names_the_alternative(endeid_loader):
    with pytest.raises(CorpusError) as exc:
        endeid_loader.source_files("1_1")
    assert "digest_parts" in str(exc.value)


def test_fold_directories_are_empty():
    """No directory per fold, because there is no file per document."""
    assert EndeidLoader.fold_dirs == {}


def test_digest_parts_names_all_three_files(endeid_loader, docs):
    parts = endeid_loader.digest_parts(docs[0])
    assert [name for name, _ in parts] == list(FILES)
    assert all(isinstance(payload, bytes) for _, payload in parts)


def test_two_records_have_different_digests(endeid_loader, docs):
    """The property hashing the three files would have destroyed.

    A digest over the shared files is identical for every document, which would make the
    split file's per-document hashes 1,941 copies of one number and unable to detect a
    re-release that moved a single note's annotations.
    """
    with_spans = [d for d in docs if d.spans][:2]
    a, b = (split.digest_material(endeid_loader.digest_parts(d)) for d in with_spans)
    assert a != b


def test_the_frozen_digests_match_the_records_on_disk(endeid_loader, docs, record):
    """Recompute every reachable document's digest and compare to the frozen file.

    This is also the check that the seal's rewrite of `id.text` preserved the unsealed
    records byte for byte: the digests were recorded before `tools/prepare_endeid.py seal`
    ran, over the bodies as they then were.
    """
    recorded = record["source"]["documents"]
    assert len(recorded) == FULL_N_DOCS
    for doc in docs:
        assert split.digest_material(endeid_loader.digest_parts(doc)) == recorded[
            doc.doc_id
        ], f"{doc.doc_id}'s bytes differ from the frozen split file"


def test_the_default_digest_hook_still_hashes_files_the_old_way(unsplit_loader):
    """MEDDOCAN's digests must not have moved when the hook was introduced.

    `digest_document(source_files(...))` is what produced the digests in
    `splits/es-meddocan.json`; `digest_parts`' default has to reproduce it exactly, or two
    frozen split files stop verifying.
    """
    doc = unsplit_loader.load()[0]
    assert split.digest_material(
        unsplit_loader.digest_parts(doc)
    ) == split.digest_document(unsplit_loader.source_files(doc.doc_id))


# ─── the split file and the seal ─────────────────────────────────────────────


def test_the_split_route_is_declared():
    assert split.SPLIT_ORIGIN["en-deid"] == "constructed"


def test_only_the_unsealed_folds_load(endeid_sealed, endeid_loader):
    """An ordinary load reaches train+dev and never the sealed fold."""
    folds = {d.split for d in endeid_loader.load()}
    assert folds == {"train", "dev"}


def test_the_sealed_root_is_not_reachable_from_an_ordinary_call(endeid_sealed, endeid_loader):
    assert endeid_loader.sealed_reachable() is None


def test_fold_sizes_are_the_constructed_split(record):
    assert {f: b["n_documents"] for f, b in record["folds"].items()} == CONSTRUCTED_SPLIT


def test_the_split_file_accounts_for_the_seal(endeid_sealed, record, docs):
    """Visible + sealed = the corpus-wide figures DESIGN §9.0 reports, per type.

    Without this the `FULL_*` constants would be unfalsifiable: nothing reachable could
    contradict them. With it, the sealed block is pinned by arithmetic — a stale figure
    there would have to be compensated elsewhere in the file to survive.
    """
    sealed = record["folds"]["test"]
    assert len(docs) + sealed["n_documents"] == FULL_N_DOCS
    assert base.count_spans(docs) + sealed["n_spans"] == FULL_N_SPANS
    assert record["totals"]["n_spans"] == FULL_N_SPANS
    assert record["totals"]["spans_by_phi_type"] == FULL_CANONICAL_COUNTS
    visible = base.count_by_type(docs)
    for phi_type, total in FULL_CANONICAL_COUNTS.items():
        assert visible.get(phi_type, 0) + sealed["spans_by_phi_type"].get(
            phi_type, 0
        ) == total, phi_type


def test_the_ninth_uncovered_record_went_with_its_patient(endeid_sealed, record, recount):
    """One record with no reference is behind the seal, and it is not named here.

    Stated as arithmetic against the frozen file: the release has 2,434 records and the
    reference covers 2,425, the split file holds 2,425 documents, and the corpus root now
    holds 1,949 records for 1,941 documents. So eight of the nine are reachable and one
    left with the test fold — which is the intended behaviour, because leaving a sealed
    patient's note text in the corpus root would put it where rule development reads it.
    """
    assert record["corpus_specific"]["n_records_without_reference"] == FULL_N_UNCOVERED
    assert len(record["corpus_specific"]["records_without_reference"]) == FULL_N_UNCOVERED
    assert recount["records"] - recount["headers"] == len(UNCOVERED)
    assert FULL_N_UNCOVERED - len(UNCOVERED) == 1
    assert FULL_N_RECORDS - FULL_N_DOCS == FULL_N_UNCOVERED


def test_the_grouping_audit_records_the_patient_branch(record):
    audit = record["group_key"]["grouping_audit"]
    assert audit["branch"].startswith("patient key")
    assert audit["n_patients"] == FULL_N_PATIENTS
    assert audit["n_documents"] == FULL_N_DOCS
    assert audit["key_source"]
    assert "did not run" in audit["identifying_surface_rule"]
    # The fields the surface rule fills are absent rather than empty: an empty
    # `candidate_stems` would read as "the rule ran and found nothing".
    for key in ("step_1_pattern", "step_2_types", "candidate_stems"):
        assert key not in audit


def test_the_grouping_audit_is_a_partition_of_the_corpus(record):
    audit = record["group_key"]["grouping_audit"]
    ids = [doc_id for unit in audit["patients"].values() for doc_id in unit]
    assert sorted(ids) == sorted(split.fold_of(record))
    assert len(ids) == FULL_N_DOCS


def test_no_group_crosses_the_split(record):
    crossing = record["group_key"]["crosses_split"]
    assert crossing["n_groups_crossing"] == 0
    assert "No patient crosses the split" in crossing["note"]


def test_the_stratification_reports_what_it_achieved(record):
    achieved = record["provenance"]["stratification"]["achieved"]
    assert set(achieved) == set(CONSTRUCTED_SPLIT)
    for fold, block in achieved.items():
        assert block["n_documents"] == CONSTRUCTED_SPLIT[fold]
        assert block["n_units"] > 0
    assert sum(b["n_units"] for b in achieved.values()) == FULL_N_PATIENTS


def test_the_seed_is_recorded_with_the_split(record):
    assert record["provenance"]["seed"] == split.construction_params("en-deid")["seed"]


#: Keys whose values are prose written in this repository — the only strings in the split
#: file that are neither an id nor a digest. Listed exhaustively, so a new field carrying
#: free text has to be added here and read while it is added.
PROSE_KEYS = frozenset(
    {
        "basis",
        "bom_note",
        "branch",
        "commit",
        "corpus",
        "fold_directories_note",
        "generated",
        "generated_by",
        "hash_algorithm",
        "hashed",
        "identifying_surface_rule",
        "key_source",
        "note",
        "origin",
        "rationale_ref",
        "reading",
        "records_without_reference_note",
        "rule_ref",
        "sparsity_note",
        "surfaces_recorded",
        "tokenizer",
        "unit",
        "variable",
    }
)


def test_every_string_in_the_split_file_is_an_id_a_digest_or_prose(record):
    """The structural half: a surface has nowhere in this file to be.

    The file is committed to a public repository and the corpus is DUA-restricted, so the
    check is over the shape of the file rather than a search for particular strings: every
    string value is a document id, a patient id, a hex digest, or sits under one of the
    `PROSE_KEYS` above. A surface written anywhere else fails here whether or not any test
    knows what that surface is — which is the property a substring search does not have.
    """
    doc_id = re.compile(r"^\d+_\d+$")
    patient_id = re.compile(r"^\d+$")
    digest = re.compile(r"^[0-9a-f]{64}$")
    offenders: list[tuple[str, int]] = []

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and not (doc_id.match(k) or patient_id.match(k)):
                    assert k.isidentifier() or k in {"p50", "p90"}, k
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            if doc_id.match(node) or patient_id.match(node) or digest.match(node):
                return
            if key not in PROSE_KEYS:
                offenders.append((str(key), len(node)))

    walk(record)
    assert offenders == [], f"free text under unexpected keys: {offenders}"


def test_no_prose_in_the_split_file_carries_a_gold_surface(record):
    """The other half, over the prose that `PROSE_KEYS` permits.

    Only surfaces that cannot be an English coincidence are searched for: one that
    contains a digit or a space. A single common word — one reachable gold LOCATION is
    `cross` — occurs inside this repository's own prose about crossing folds, and a test
    that failed on that would be testing English rather than the file.
    """
    prose = "\n".join(
        value
        for value in _strings_under(record, PROSE_KEYS)
    )
    loader = EndeidLoader(use_split_file=False)
    for doc in loader.load():
        for span in doc.spans:
            surface = span.surface.strip()
            if len(surface) < 4 or not (any(c.isdigit() for c in surface) or " " in surface):
                continue
            assert surface not in prose, (
                f"{doc.doc_id} span at [{span.start}, {span.end}) is in the split file"
            )


def _strings_under(node, keys, key=None):
    """Every string value in `node` whose key is in `keys`."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _strings_under(v, keys, k)
    elif isinstance(node, list):
        for v in node:
            yield from _strings_under(v, keys, key)
    elif isinstance(node, str) and key in keys:
        yield node


# ─── failure modes, on synthetic files ──────────────────────────────────────
# Written into tmp_path, never into the corpus. The text is invented English, so these
# tests carry no corpus text either — which is also what makes the "no surface in the
# message" tests below meaningful rather than accidental.

SURFACE = "Wilhelmina Featherstonehaugh"
BODY = f"Pt seen by {SURFACE} at noon.\nStable overnight."
START = BODY.index(SURFACE)
END = START + len(SURFACE)


def write_corpus(
    tmp_path: Path,
    *,
    text: str | None = None,
    deid: str | None = None,
    phrase: str | None = None,
) -> Path:
    """A one-record corpus root, with any of the three files overridable."""
    default_text = f"START_OF_RECORD=1||||1||||\n{BODY}\n||||END_OF_RECORD\n\n"
    default_deid = f"Patient 1  Note 1\n{START}  {START}  {END}\n"
    default_phrase = f"1 1 {START} {END} PTName {SURFACE}\n"
    (tmp_path / TEXT_FILE).write_text(
        default_text if text is None else text, encoding="utf-8"
    )
    (tmp_path / OFFSETS_FILE).write_text(
        default_deid if deid is None else deid, encoding="utf-8"
    )
    (tmp_path / TYPES_FILE).write_text(
        default_phrase if phrase is None else phrase, encoding="utf-8"
    )
    return tmp_path


def load_synthetic(root: Path):
    return EndeidLoader(root=root, use_split_file=False).load()


def test_a_synthetic_corpus_loads(tmp_path):
    """The harness below must be able to produce a *valid* corpus, or every negative
    test could be passing for the wrong reason."""
    docs = load_synthetic(write_corpus(tmp_path))
    assert len(docs) == 1
    assert docs[0].doc_id == "1_1"
    assert docs[0].meta["patient_id"] == "1"
    assert [(s.start, s.end, s.subtype, s.phi_type) for s in docs[0].spans] == [
        (START, END, "PTName", "NAME")
    ]


def test_an_offset_mismatch_raises_and_quotes_no_surface(tmp_path):
    """The rule CLAUDE.md states, tested the way `test_meddocan_loader.py` tests it.

    An exception message travels into terminals, CI logs and issue trackers, where
    `tools/release_screen.py` cannot follow it. So the message may carry the span index
    and the offsets and nothing else — and a test has to hold that, or the next loader
    puts the surface back because it is convenient while debugging.
    """
    root = write_corpus(tmp_path, phrase=f"1 1 {START} {END} PTName Someone Else\n")
    with pytest.raises(CorpusError) as exc:
        load_synthetic(root)
    message = str(exc.value)
    assert SURFACE not in message
    assert "Someone Else" not in message
    assert BODY not in message


def test_a_span_past_the_end_of_the_body_raises_with_offsets_only(tmp_path):
    root = write_corpus(
        tmp_path,
        deid=f"Patient 1  Note 1\n{START}  {START}  {len(BODY) + 50}\n",
        phrase=f"1 1 {START} {len(BODY) + 50} PTName {SURFACE}\n",
    )
    with pytest.raises(CorpusError) as exc:
        load_synthetic(root)
    assert SURFACE not in str(exc.value)


def test_a_start_that_is_not_repeated_raises(tmp_path):
    """The duplicated start is what identifies the format (DESIGN §9.2)."""
    root = write_corpus(tmp_path, deid=f"Patient 1  Note 1\n{START}  {END}  {END}\n")
    with pytest.raises(CorpusError) as exc:
        load_synthetic(root)
    assert "repeat its start" in str(exc.value)


def test_the_two_reference_files_must_agree_on_every_span(tmp_path):
    root = write_corpus(tmp_path, phrase=f"1 1 {START + 1} {END} PTName {SURFACE}\n")
    with pytest.raises(CorpusError) as exc:
        load_synthetic(root)
    message = str(exc.value)
    assert "span 0" in message
    assert SURFACE not in message


def test_a_type_list_of_the_wrong_length_raises(tmp_path):
    root = write_corpus(tmp_path, phrase="")
    with pytest.raises(CorpusError) as exc:
        load_synthetic(root)
    assert "same spans in the same order" in str(exc.value)


def test_an_unknown_source_type_raises(tmp_path):
    root = write_corpus(tmp_path, phrase=f"1 1 {START} {END} Invented {SURFACE}\n")
    with pytest.raises(CorpusError):
        load_synthetic(root)


def test_a_phrase_line_with_too_few_fields_raises_without_quoting_it(tmp_path):
    root = write_corpus(tmp_path, phrase=f"1 1 {START} {END} PTName\n")
    with pytest.raises(CorpusError) as exc:
        load_synthetic(root)
    assert "fields" in str(exc.value)
    assert "PTName" not in str(exc.value)


def test_an_annotation_for_a_record_that_has_no_text_raises(tmp_path):
    root = write_corpus(
        tmp_path,
        deid=f"Patient 1  Note 1\n{START}  {START}  {END}\nPatient 2  Note 9\n",
    )
    with pytest.raises(CorpusError) as exc:
        load_synthetic(root)
    assert "2_9" in str(exc.value)


def test_a_record_opened_inside_another_raises(tmp_path):
    root = write_corpus(
        tmp_path,
        text="START_OF_RECORD=1||||1||||\nSTART_OF_RECORD=1||||2||||\n||||END_OF_RECORD\n",
    )
    with pytest.raises(CorpusError) as exc:
        load_synthetic(root)
    assert "inside an open one" in str(exc.value)


def test_a_file_that_ends_inside_a_record_raises(tmp_path):
    root = write_corpus(tmp_path, text=f"START_OF_RECORD=1||||1||||\n{BODY}\n")
    with pytest.raises(CorpusError) as exc:
        load_synthetic(root)
    assert "ends inside an open record" in str(exc.value)


def test_the_end_mark_with_text_beside_it_raises(tmp_path):
    """Accepting it as a suffix would shorten the body and move every offset in it."""
    root = write_corpus(
        tmp_path, text=f"START_OF_RECORD=1||||1||||\n{BODY} ||||END_OF_RECORD\n"
    )
    with pytest.raises(CorpusError) as exc:
        load_synthetic(root)
    message = str(exc.value)
    assert "end mark with text beside it" in message
    assert BODY not in message


def test_a_line_with_content_outside_every_record_raises(tmp_path):
    """It would be note text in no record: scored against nothing, so never a leak.

    It shifts no offset, which is exactly why nothing else would notice.
    """
    root = write_corpus(
        tmp_path,
        text=(
            f"START_OF_RECORD=1||||1||||\n{BODY}\n||||END_OF_RECORD\n"
            "Pt ambulated in hall.\n"
        ),
    )
    with pytest.raises(CorpusError) as exc:
        load_synthetic(root)
    message = str(exc.value)
    assert "outside every record" in message
    assert "ambulated" not in message


def test_the_blank_separator_between_records_is_accepted(tmp_path):
    """The release writes exactly one, 2,434 times. It is a separator, not content."""
    docs = load_synthetic(
        write_corpus(
            tmp_path,
            text=(
                f"START_OF_RECORD=1||||1||||\n{BODY}\n||||END_OF_RECORD\n\n"
                f"START_OF_RECORD=1||||2||||\n{BODY}\n||||END_OF_RECORD\n\n"
            ),
            deid=(
                f"Patient 1  Note 1\n{START}  {START}  {END}\n"
                f"Patient 1  Note 2\n{START}  {START}  {END}\n"
            ),
            phrase=(
                f"1 1 {START} {END} PTName {SURFACE}\n"
                f"1 2 {START} {END} PTName {SURFACE}\n"
            ),
        )
    )
    assert sorted(d.doc_id for d in docs) == ["1_1", "1_2"]


def test_a_record_with_no_reference_is_dropped_not_emptied(tmp_path):
    loader = EndeidLoader(
        root=write_corpus(
            tmp_path,
            text=(
                f"START_OF_RECORD=1||||1||||\n{BODY}\n||||END_OF_RECORD\n\n"
                f"START_OF_RECORD=1||||2||||\n{BODY}\n||||END_OF_RECORD\n\n"
            ),
        ),
        use_split_file=False,
    )
    docs = loader.load()
    assert [d.doc_id for d in docs] == ["1_1"]
    assert loader.uncovered == ["1_2"]


def test_a_record_with_a_header_and_no_spans_is_loaded(tmp_path):
    loader = EndeidLoader(
        root=write_corpus(
            tmp_path,
            text=(
                f"START_OF_RECORD=1||||1||||\n{BODY}\n||||END_OF_RECORD\n\n"
                f"START_OF_RECORD=1||||2||||\n{BODY}\n||||END_OF_RECORD\n\n"
            ),
            deid=(
                f"Patient 1  Note 1\n{START}  {START}  {END}\nPatient 1  Note 2\n"
            ),
        ),
        use_split_file=False,
    )
    docs = loader.load()
    assert sorted(d.doc_id for d in docs) == ["1_1", "1_2"]
    assert loader.uncovered == []
    assert [len(d.spans) for d in sorted(docs, key=lambda d: d.doc_id)] == [1, 0]


def test_a_missing_file_names_it_without_the_path(tmp_path):
    root = write_corpus(tmp_path)
    (root / TYPES_FILE).unlink()
    with pytest.raises(CorpusError) as exc:
        load_synthetic(root)
    message = str(exc.value)
    assert TYPES_FILE in message
    assert str(root) not in message


def test_an_empty_corpus_root_raises(tmp_path):
    with pytest.raises(CorpusError):
        load_synthetic(tmp_path)


# ─── the two readers of the release ─────────────────────────────────────────


def test_the_two_readers_of_the_release_agree(reachable_files, docs):
    """`tools/gold_provenance_check.py` parses the same framing for a different question.

    Kept separate rather than merged — that tool prints, so it never binds the phrase
    field, and this loader must bind it because `Span.surface` is what every loader here
    holds. Two readers of one format need a cross-check, or they drift and the provenance
    note stops describing the corpus the results came from.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import gold_provenance_check as gpc

    by_doc = {d.doc_id: d for d in docs}
    total = 0
    for offsets, types in zip(
        reachable_files[OFFSETS_FILE], reachable_files[TYPES_FILE]
    ):
        table = gpc.parse_reference(offsets, types)
        assert table.n_unparsed == 0
        for doc_id, spans in table.spans.items():
            assert doc_id in by_doc, doc_id
            mine = by_doc[doc_id].spans
            assert [(s.start, s.end) for s in spans] == [
                (s.start, s.end) for s in mine
            ], doc_id
            for index, span in enumerate(mine):
                assert table.types[(doc_id, index)] == span.subtype
            total += len(spans)
    assert total == N_SPANS


# ─── the registry ───────────────────────────────────────────────────────────


def test_the_registry_resolves_this_corpus():
    """One answer to "which loader reads this corpus" (DESIGN §3)."""
    assert type(base.loader_for("en-deid")) is EndeidLoader
