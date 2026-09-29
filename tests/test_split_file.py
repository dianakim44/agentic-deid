"""Tests for `splits/{corpus}.json` — the seal's reference point.

Two jobs, and the second is the one that matters:

  - **Schema tests** hold the shape that every corpus will reuse. They need no
    corpus on disk, so they run everywhere and they fail loudly if a later corpus
    adds a top-level field instead of using `corpus_specific`.
  - **Recount tests** re-derive every summary figure the file records from the
    corpus on disk and require agreement. A summary written once and never
    re-derived is a comment; re-derived on every run, it is a claim that can be
    falsified — which is the only reason to record it at all.

    python3 -m pytest tests/test_split_file.py -q
"""
import copy
import json
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import split  # noqa: E402
from src.corpora import CorpusError, base  # noqa: E402
from src.corpora.meddocan import MeddocanLoader  # noqa: E402

CORPUS = "es-meddocan"

# From DESIGN §9.6 and §9.0/§9.1. Duplicated from test_meddocan_loader.py on
# purpose: if the split file and the loader tests are checked against the same
# constant object, one edit moves both and the agreement stops being evidence.
OFFICIAL_SPLIT = {"train": 500, "dev": 250, "test": 250}
N_DOCS = 1000
N_SPANS = 22795
N_CANONICAL = 20538
N_EXCLUDED = 2257
# DESIGN §9.5 / §9.6, and docs/notes/corpus-observations.md §3
N_CANDIDATE_STEMS = 48
N_CROSSING_STEMS = 34
N_CROSSING_DOCS = 80

#: DESIGN §9.5's small-cell rule, pre-registered 2026-09-29. The threshold is **derived**
#: from `config/split.yaml`'s proportions and is deliberately not stored beside them, so
#: the constant here is a claim about the formula's value at those proportions rather than
#: a second copy of a setting. `N_CARMEN_STRATA` is the post-collapse count DESIGN §9.5
#: item 3 states and `config/split.yaml` declares; the code refuses a derived count that
#: disagrees with the declared one.
CARMEN = "es-carmen"
CARMEN_VARIABLE = "filename_doctype_x_language_label"
SMALL_CELL_THRESHOLD = 5
N_CARMEN_STRATA = 11
CARMEN_DOCTYPES = {"IR", "IA", "IT", "CC", "IE"}
CARMEN_COLLAPSED = ["CC"]

#: The measured cross-tabulation, duplicated from `tests/test_carmen_loader.py` on
#: purpose, for the reason `OFFICIAL_SPLIT` above is duplicated: `test_carmen_loader.py`
#: recounts this table against the corpus, and this file checks that the rule applied to
#: *this shape* yields 11 strata. Sharing one object would make one edit move both and
#: the agreement would stop being evidence. It also survives the seal — after the test
#: fold moves out of the corpus root the 12 cells are no longer recountable here, and the
#: chain corpus → `CROSS` → collapse → 11 is what holds the claim together.
CARMEN_CROSS = {
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

#: The folds still readable after the seal (DESIGN §6). Tests that recount against
#: the corpus can only cover these; tests about the corpus as a whole read the file,
#: which was generated before the seal for exactly this reason.
UNSEALED = ("train", "dev")
N_UNSEALED_DOCS = 750


# ─── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def record():
    """The committed split file. A missing file fails — it is version-controlled."""
    return split.read(CORPUS)


@pytest.fixture(scope="module")
def docs(unsplit_loader):
    """The corpus loaded *without* the split file, so the recount is independent.

    `use_split_file=False` is the point, and it is why `unsplit_loader` exists in
    `tests/conftest.py` as a second construction fixture rather than as a `try` here:
    loading with the file and then checking the file against the result would be
    circular. Availability was decided by `corpus_present`, upstream of both.

    This is train+dev — 750 documents. The sealed fold is not reachable from a test
    and no fixture here tries: what the file claims about `test` is carried by the
    fold/total reconciliation and by the freeze commit, not by a recount.
    """
    return unsplit_loader.load()


# ─── the recorded summaries are recomputable ────────────────────────────────


def test_summaries_match_an_independent_recount(record, docs):
    """The check the brief asks for: recompute, compare, raise on mismatch."""
    split.verify(record, docs)


def test_verify_raises_when_a_summary_is_wrong(record, docs):
    """`verify()` must actually be capable of failing.

    Without this, `test_summaries_match_an_independent_recount` passing would be
    consistent with `verify()` having been quietly reduced to a no-op — the same
    class of defect the mutation harness exists to catch.
    """
    tampered = copy.deepcopy(record)
    tampered["folds"]["dev"]["n_spans"] += 1
    with pytest.raises(CorpusError, match="recounted"):
        split.verify(tampered, docs)


def test_verify_notices_a_document_moved_between_folds(record, docs):
    """Moving one id from dev to test must be caught — this is the seal.

    A split file that recorded the right counts with the wrong membership would
    let a test document be developed against while every total still reconciled.
    """
    tampered = copy.deepcopy(record)
    moved = tampered["folds"]["dev"]["document_ids"].pop()
    tampered["folds"]["test"]["document_ids"].append(moved)
    with pytest.raises(CorpusError):
        split.verify(tampered, docs)


def test_fold_sizes_are_the_official_split(record):
    sizes = {f: b["n_documents"] for f, b in record["folds"].items()}
    assert sizes == OFFICIAL_SPLIT


def test_totals_are_the_sum_of_the_folds(record):
    """`totals` is recorded, so it can disagree with the folds. It must not."""
    for key in ("n_documents", "n_spans", "n_spans_in_scope", "n_spans_excluded"):
        assert record["totals"][key] == sum(
            b[key] for b in record["folds"].values()
        ), key
    assert record["totals"]["tokens"]["total"] == sum(
        b["tokens"]["total"] for b in record["folds"].values()
    )
    for phi_type, total in record["totals"]["spans_by_phi_type"].items():
        assert total == sum(
            b["spans_by_phi_type"].get(phi_type, 0) for b in record["folds"].values()
        ), phi_type


def test_totals_match_the_design_figures(record):
    totals = record["totals"]
    assert totals["n_documents"] == N_DOCS
    assert totals["n_spans"] == N_SPANS
    assert totals["n_spans_in_scope"] == N_CANONICAL
    assert totals["n_spans_excluded"] == N_EXCLUDED
    assert N_CANONICAL + N_EXCLUDED == N_SPANS


def test_every_document_appears_in_exactly_one_fold(record):
    ids = [i for b in record["folds"].values() for i in b["document_ids"]]
    assert len(ids) == N_DOCS
    assert len(set(ids)) == N_DOCS


def test_hashes_cover_every_document(record):
    """One digest per document, and the manifest digest derived from them."""
    documents = record["source"]["documents"]
    assert len(documents) == N_DOCS
    assert record["source"]["n_documents"] == N_DOCS
    folded = {i for b in record["folds"].values() for i in b["document_ids"]}
    assert set(documents) == folded
    assert all(len(h) == 64 for h in documents.values())
    assert record["source"]["manifest_digest"] == split.manifest_digest(documents)


def test_source_hashes_match_the_files_on_disk(record, docs):
    """The hashes are a claim about bytes; check it on a sample.

    A sample rather than all 750: hashing every file is a second full read of the
    corpus for a check whose failure mode (a re-release) is corpus-wide, not
    per-document. The manifest digest above already binds all 1,000 hashes
    together, so a single altered document cannot hide behind the sample.

    Sampled from the unsealed folds only. The sealed fold's hashes are exactly the
    part of this file that can no longer be re-derived — which is the point of
    having recorded them before the seal, and the reason the manifest digest
    matters more now than it did then: it is the only remaining evidence that the
    250 sealed hashes were not edited after the fact.
    """
    loader = MeddocanLoader(use_split_file=False)
    recorded = record["source"]["documents"]
    unsealed_ids = sorted(
        doc_id
        for fold in UNSEALED
        for doc_id in record["folds"][fold]["document_ids"]
    )
    assert len(unsealed_ids) == N_UNSEALED_DOCS
    sampled = unsealed_ids[::75]
    assert len(sampled) == 10
    for doc_id in sampled:
        assert split.digest_document(loader.source_files(doc_id)) == recorded[doc_id]


# ─── the schema, which every corpus will reuse ──────────────────────────────


def test_schema_version_is_pinned(record):
    assert record["schema_version"] == split.SCHEMA_VERSION


def test_corpus_specific_fields_are_not_at_the_top_level(record):
    """The separation the brief requires, enforced rather than documented.

    Everything true of MEDDOCAN alone — the brat/XML choice, the fold
    directories, the BOM document list — sits under `corpus_specific`. If a
    second corpus's generator puts its own field at the top level, `check_schema`
    rejects the file.
    """
    assert set(record) == split.REQUIRED_TOP_LEVEL
    for key in ("reading", "fold_directories", "bom_documents"):
        assert key in record["corpus_specific"]
        assert key not in record


def test_an_extra_top_level_key_is_rejected(record):
    tampered = dict(record)
    tampered["bom_documents"] = []
    with pytest.raises(CorpusError, match="not.*in the shared schema"):
        split.check_schema(tampered, CORPUS)


def test_a_missing_common_key_is_rejected(record):
    tampered = {k: v for k, v in record.items() if k != "group_key"}
    with pytest.raises(CorpusError, match="missing keys"):
        split.check_schema(tampered, CORPUS)


def test_fold_names_come_from_naming_yaml(record):
    assert set(record["folds"]) <= set(base.split_names())


def test_an_invented_fold_name_is_rejected(record):
    tampered = copy.deepcopy(record)
    tampered["folds"]["holdout"] = tampered["folds"].pop("test")
    with pytest.raises(CorpusError, match="not a split in config/naming.yaml"):
        split.check_schema(tampered, CORPUS)


def test_phi_types_come_from_naming_yaml(record):
    canonical = set(base.canonical_types())
    for block in list(record["folds"].values()) + [record["totals"]]:
        assert set(block["spans_by_phi_type"]) <= canonical


def test_the_tokenizer_is_named(record):
    """"tokens" with no stated tokenizer is not a measurement (see split.py)."""
    assert record["tokenizer"] == split.TOKENIZER


def test_provenance_records_that_the_split_was_not_constructed(record):
    """§9.6: adopted, not built — so there is no seed and no stratification."""
    provenance = record["provenance"]
    assert provenance["origin"] == "official"
    assert provenance["seed"] is None
    assert provenance["stratification"] is None
    assert "§9.6" in provenance["rationale_ref"]


def test_group_key_states_that_the_document_is_the_unit(record):
    """DESIGN §9.5. The field exists for corpora that group; MEDDOCAN does not,
    and the file has to say so rather than leave the reader to infer it."""
    group_key = record["group_key"]
    assert group_key["unit"] == "document"
    assert group_key["n_groups"] == N_DOCS
    assert "§9.5" in group_key["rationale_ref"]
    assert "936" in group_key["note"]


# ─── the §9.5 grouping audit ────────────────────────────────────────────────


def test_grouping_audit_records_every_candidate_stem(record):
    """§9.5 step 4: the file says which rule formed each group and what agreed.

    48 stems carry more than one document, so 48 decisions were made and 48 are
    recorded. The 888 single-document stems are step-3 groups by definition and
    listing them would bury the decisions in the defaults.
    """
    audit = record["group_key"]["grouping_audit"]
    assert audit["n_candidate_stems"] == N_CANDIDATE_STEMS
    assert len(audit["candidate_stems"]) == N_CANDIDATE_STEMS
    assert "§9.5" in audit["rule_ref"]
    for stem, entry in audit["candidate_stems"].items():
        assert len(entry["documents"]) > 1, stem
        assert set(entry["n_shared_surfaces"]) == {"name", "record", "date"}
        assert entry["decision"]


def test_no_meddocan_stem_passes_step_two(record):
    """The measured result: not one of the 48 is confirmed as the same patient."""
    audit = record["group_key"]["grouping_audit"]
    assert audit["n_stems_confirmed"] == 0
    assert audit["n_documents_grouped"] == 0
    assert not any(e["grouped"] for e in audit["candidate_stems"].values())


def test_the_step_two_rule_reproduces_every_recorded_decision(record):
    """Re-apply §9.5 step 2 to the recorded counts, for all 48 stems.

    This works after the seal where a recount cannot: the counts are in the file, so
    the rule can be checked against every stem including those with a document
    behind `sealed/`. That is not a convenience — the single MEDDOCAN stem that
    shares a name surface and nothing else straddles the split, so it is the only
    evidence that step 2 requires more than a name match, and a check restricted to
    the readable folds would not contain it.
    """
    stems = record["group_key"]["grouping_audit"]["candidate_stems"]
    for stem, entry in stems.items():
        assert split.step_2_confirms(entry["n_shared_surfaces"]) == entry["grouped"], (
            stem
        )


def test_a_shared_name_alone_does_not_confirm_a_group(record):
    """The rule's discriminating case, stated as a property of the rule.

    A shared given name across different surnames is not one patient. Asserted
    directly on `step_2_confirms` as well as against the recorded stem, so the
    guarantee does not depend on that stem continuing to exist in the corpus.
    """
    assert not split.step_2_confirms({"name": 1, "record": 0, "date": 0})
    assert split.step_2_confirms({"name": 1, "record": 1, "date": 0})
    assert split.step_2_confirms({"name": 1, "record": 0, "date": 1})
    assert not split.step_2_confirms({"name": 0, "record": 1, "date": 1})

    # And the corpus actually contains the case, so the rule is not defending
    # against a hypothetical. If a re-release removes it, this fails and the
    # §9.5 rationale needs rewriting rather than the test relaxing.
    stems = record["group_key"]["grouping_audit"]["candidate_stems"]
    name_only = [
        stem
        for stem, entry in stems.items()
        if entry["n_shared_surfaces"]["name"] > 0
        and not entry["n_shared_surfaces"]["record"]
        and not entry["n_shared_surfaces"]["date"]
    ]
    assert len(name_only) == 1, (
        f"expected exactly one name-only stem in MEDDOCAN, found {len(name_only)}"
    )
    assert not stems[name_only[0]]["grouped"]


def test_grouping_audit_is_reproducible_from_the_corpus(record, docs):
    """Recompute the audit and require an exact match where it can be recomputed.

    This is the check that keeps §9.5 from drifting: if the step-1 pattern or the
    step-2 types change, the recorded decisions and the code's decisions part
    company here.

    Restricted to stems whose documents are all in the unsealed folds. A stem with
    one document behind `sealed/` cannot be re-decided — step 2 compares surfaces
    across the stem's documents, so a partial recomputation would be comparing a
    subset and would disagree with the record for a legitimate reason. Those stems
    are counted rather than skipped silently, so this test cannot pass by finding
    nothing to check.
    """
    unsealed_ids = {
        doc_id
        for fold in UNSEALED
        for doc_id in record["folds"][fold]["document_ids"]
    }
    recorded = record["group_key"]["grouping_audit"]["candidate_stems"]
    recomputed = split.grouping_audit(CORPUS, docs)["candidate_stems"]

    checkable = {
        stem: entry
        for stem, entry in recorded.items()
        if set(entry["documents"]) <= unsealed_ids
    }
    assert len(checkable) >= 20, (
        f"only {len(checkable)} of {len(recorded)} candidate stems are wholly "
        "unsealed; this check has stopped covering enough to be evidence"
    )
    for stem, entry in checkable.items():
        assert stem in recomputed, f"{stem} was a candidate then and is not now"
        assert recomputed[stem] == entry, stem

    # And the rule itself, which is corpus-independent and must not drift.
    full = split.grouping_audit(CORPUS, docs)
    assert full["rule_ref"] == record["group_key"]["grouping_audit"]["rule_ref"]
    assert full["step_1_pattern"] == (
        record["group_key"]["grouping_audit"]["step_1_pattern"]
    )
    assert full["step_2_types"] == record["group_key"]["grouping_audit"]["step_2_types"]


def test_grouping_audit_records_no_surfaces(record):
    """Counts, never surfaces — the schema is shared with CARMEN-I (CLAUDE.md).

    Checked structurally rather than by searching for known strings: a test that
    looks for particular surfaces would itself have to contain them.
    """
    audit = record["group_key"]["grouping_audit"]
    for entry in audit["candidate_stems"].values():
        assert set(entry) == {
            "documents",
            "n_shared_surfaces",
            "grouped",
            "decision",
        }
        assert all(isinstance(v, int) for v in entry["n_shared_surfaces"].values())
    assert audit["surfaces_recorded"].startswith("no")


def test_the_committed_file_contains_no_span_surface(record, docs):
    """No gold surface appears anywhere in the split file (CLAUDE.md).

    MEDDOCAN is synthetic, so this file is not itself a disclosure — the check is
    here because the schema and the generator are shared with CARMEN-I, where the
    same code path would be writing DUA-restricted clinical text into a committed
    file that `release_screen.py` reports as allowed.

    Short surfaces are skipped: `1`, `45`, `Sr.` and the like occur in document
    ids and in sha256 hex by coincidence, and a test that failed on those would be
    turned off rather than fixed. Any hit at 6+ characters is reported with its
    context located by offset, never printed.
    """
    text = json.dumps(record, ensure_ascii=False)
    surfaces = {
        s.surface.strip()
        for d in docs
        for s in d.spans
        if len(s.surface.strip()) >= 6
    }
    assert len(surfaces) > 1000, "sanity: the surface set should not be tiny"
    hits = sorted(text.index(s) for s in surfaces if s in text)
    # Coincidental collisions are expected and identified by where they land:
    # inside a document id or inside a hex digest, never in a note or a key.
    unexplained = []
    for offset in hits:
        window = text[max(0, offset - 100) : offset]
        if '": "' in window and window.rsplit('": "', 1)[-1].isalnum():
            continue  # inside a hash value
        if '"S0' in window or '"S1' in window:
            continue  # inside a document id
        unexplained.append(offset)
    assert not unexplained, (
        f"{len(unexplained)} span surfaces appear in the split file at offsets "
        f"{unexplained[:5]} — surfaces must never be written to a committed file"
    )


def test_a_document_id_that_does_not_parse_stops_the_audit(docs):
    """§9.5 step 1 must cover every id.

    The earlier digits-only pattern silently dropped 31 ids from the grouping,
    which is how a partial audit reports itself as complete. An id the pattern
    cannot parse now raises.
    """
    import dataclasses

    broken = list(docs[:2])
    broken[0] = dataclasses.replace(docs[0], doc_id="no_suffix_at_all_")
    with pytest.raises(CorpusError, match="stem\\+suffix"):
        split.grouping_audit(CORPUS, broken)


def test_a_corpus_without_grouping_types_raises():
    """A new corpus must define its §9.5 comparison types, not skip the audit.

    `en-n2c2` is the corpus that has none yet. This test named `de-grascco` until
    2026-09-21 and `es-carmen` until 2026-09-29, when each got its types and the test
    began asserting that the corpus it was written for still had none — a test whose
    subject is "whichever corpus is next" has to be moved on, not deleted.

    `en-n2c2` is declared in naming.yaml and on hold (DESIGN §11), and it is the last id
    this test can move to. `tests/test_meddocan_loader.py`'s
    `test_the_registry_implements_every_declared_corpus_but_one` is the one place that
    says so, and it is where a reader finds the next subject if there ever is one.
    """
    with pytest.raises(CorpusError, match="grouping types"):
        split.grouping_audit("en-n2c2", [])


def test_unstructured_document_ids_do_not_stop_the_audit():
    """The converse of the test above it, and the reason that one is per-corpus.

    47 of GraSCCo's 63 ids contain no separator at all. For MEDDOCAN an id that does
    not parse means the step-1 pattern missed documents; for GraSCCo it means the file
    names carry no sibling structure, which is what step 3 is for. Declaring which
    corpus is which is the only way one rule can serve both — inferring it from how
    many ids failed would accept a broken pattern on any corpus where most ids parse.
    """
    from src.corpora.base import Document

    docs = [
        Document(
            doc_id=doc_id,
            corpus_id="de-grascco",
            text="x",
            spans=[],
            split=None,
            had_bom=False,
            meta={},
        )
        for doc_id in ("Kolkhorst", "Baastrup", "Tupolev_1", "Tupolev_2")
    ]
    audit = split.grouping_audit("de-grascco", docs)
    assert set(audit["candidate_stems"]) == {"Tupolev"}
    assert audit["n_candidate_stems"] == 1
    # No identifiers at all in these stubs, so step 2 cannot confirm.
    assert audit["candidate_stems"]["Tupolev"]["grouped"] is False


def test_dates_are_compared_after_normalisation():
    """§9.5 step 2 normalises dates, and `Tupolev_1..4` is why.

    Its one birth date ships as `21/06/1967`, `21.06.67` and `21.06.1967`, so a raw
    comparison finds no agreement and the only confirmed group in three corpora
    dissolves. Two-digit years stay two digits: expanding them needs a century cutoff
    that no evidence here supports, and the question is only whether two surfaces
    denote the same day.
    """
    assert (
        split.normalise_date("21/06/1967")
        == split.normalise_date("21.06.67")
        == split.normalise_date("21-6-67")
    )
    assert split.normalise_date("1967-06-21") is None  # ISO order is not this pattern
    assert split.normalise_date("32.06.67") is None  # not a day
    assert split.normalise_date("21.13.67") is None  # not a month
    assert split.normalise_date("Juni 1967") is None


def test_normalisation_only_adds_agreement():
    """The union is what makes adding normalisation safe for an already-frozen file.

    A surface set contains the raw surfaces *and* the normalised dates, so a pair that
    agreed before still agrees: the count can only rise, never fall. That is the
    property that lets `splits/es-meddocan.json` stay frozen — the check that its
    recorded counts are unchanged is `test_grouping_audit_is_reproducible_from_the_corpus`
    above, and this is the argument for the 18 stems that check can no longer reach.
    """
    from src.corpora.base import Span

    spans = [
        Span(
            start=0,
            end=10,
            surface="21.06.67",
            subtype="FECHAS",
            phi_type="DATE",
            excluded=False,
        )
    ]
    raw = split.comparable_surfaces(spans, ("FECHAS",), as_dates=False)
    with_dates = split.comparable_surfaces(spans, ("FECHAS",), as_dates=True)
    assert raw == {"21.06.67"}
    assert raw < with_dates


def test_stem_crossings_are_recorded_even_though_no_group_crosses(record):
    """"0 groups cross the split" is vacuous when there are no groups.

    So the stem figure is recorded: 34 of the 48 candidate stems straddle the
    split, covering 80 documents. That is the number that would matter if the
    grouping decision were wrong, and DESIGN §9.6 cites it as the reason the
    stem-disjoint split is reported alongside.
    """
    crossing = record["group_key"]["crosses_split"]
    assert crossing["n_groups_crossing"] == 0
    assert crossing["n_candidate_stems_crossing"] == N_CROSSING_STEMS
    assert crossing["n_documents_in_crossing_stems"] == N_CROSSING_DOCS
    assert sum(crossing["fold_combinations"].values()) == N_CROSSING_STEMS


def test_token_distribution_is_recorded_per_fold(record):
    for fold, block in record["folds"].items():
        per_document = block["tokens"]["per_document"]
        assert set(per_document) == {"min", "p25", "median", "p75", "max"}
        ordered = [per_document[k] for k in ("min", "p25", "median", "p75", "max")]
        assert ordered == sorted(ordered), fold
        assert per_document["min"] > 0


# ─── the loader applies the file ────────────────────────────────────────────


def test_the_loader_gets_its_folds_from_the_split_file(record):
    """Loading normally must reproduce the folds the file records — for what loads.

    The default path reads the file; `use_split_file=False` (used by the fixtures
    above) is only for the generator and for these tests.

    The sealed fold is absent from the load and that absence is checked positively:
    every document the file assigns to `test` must be missing, and every document
    the file assigns to train or dev must be present with the fold the file gives
    it. Asserting only `len(loaded) == 750` would also pass if the loader had
    dropped 250 arbitrary documents.
    """
    loader = MeddocanLoader()
    assert loader.use_split_file
    loaded = loader.load()
    from_file = split.fold_of(record)
    got = {d.doc_id: d.split for d in loaded}

    expected = {
        doc_id: fold for doc_id, fold in from_file.items() if fold in UNSEALED
    }
    assert got == expected
    sealed_ids = {
        doc_id for doc_id, fold in from_file.items() if fold not in UNSEALED
    }
    assert len(sealed_ids) == OFFICIAL_SPLIT["test"]
    assert not sealed_ids & set(got)
    assert base.count_by_split(loaded) == {f: OFFICIAL_SPLIT[f] for f in UNSEALED}


def test_the_loader_rejects_a_split_file_that_moves_a_document(monkeypatch, docs):
    """A disagreement between the corpus and the frozen file must stop the load.

    MEDDOCAN encodes the fold in its directory path, so both sources exist here
    and can be cross-checked. Honouring either one silently would move a document
    across the seal.
    """
    tampered = copy.deepcopy(split.read(CORPUS))
    moved = tampered["folds"]["dev"]["document_ids"].pop()
    tampered["folds"]["train"]["document_ids"].append(moved)
    monkeypatch.setattr(split, "read", lambda corpus_id: tampered)
    with pytest.raises(CorpusError, match="across the seal"):
        MeddocanLoader().load()


def test_the_loader_rejects_a_split_file_missing_a_document(monkeypatch):
    tampered = copy.deepcopy(split.read(CORPUS))
    tampered["folds"]["dev"]["document_ids"].pop()
    monkeypatch.setattr(split, "read", lambda corpus_id: tampered)
    with pytest.raises(CorpusError, match="did not load|in no fold"):
        MeddocanLoader().load()


def test_a_missing_split_file_is_a_clear_error(monkeypatch, tmp_path):
    """The message has to say what to run, because this is the first thing a new
    checkout hits and the fix is not guessable."""
    monkeypatch.setattr(split, "split_path", lambda corpus_id: tmp_path / "gone.json")
    with pytest.raises(CorpusError, match="python3 -m src.split"):
        split.read(CORPUS)


# ─── the small-cell rule and the cross stratification (DESIGN §9.5) ──────────


def _carmen_units():
    """One document per unit, laid out to match the measured cross-tab exactly.

    Synthetic ids, not the corpus: the numbers this exercises are the *cell sizes*, and
    `tests/test_carmen_loader.py` is where they are checked against the corpus. Building
    them here keeps this section runnable with no corpus on disk and after the seal.
    """
    units, keys = [], {}
    for (doctype, lang), n in sorted(CARMEN_CROSS.items()):
        for i in range(n):
            doc_id = f"{doctype}-{lang}-{i:04d}"
            units.append([doc_id])
            keys[doc_id] = (doctype, lang)
    return units, keys


def test_the_cross_tab_used_here_sums_to_the_corpus():
    """A transcription error in the table above would make every test below vacuous.

    The first draft of DESIGN §9.5's table had `IA` summing to 517 against a doctype
    total of 617, and it read as plausible. Both margins are checked because one alone
    does not catch a figure moved from one cell to another in the same row.
    """
    assert sum(CARMEN_CROSS.values()) == 2000
    by_doctype = {}
    by_lang = {}
    for (doctype, lang), n in CARMEN_CROSS.items():
        by_doctype[doctype] = by_doctype.get(doctype, 0) + n
        by_lang[lang] = by_lang.get(lang, 0) + n
    assert by_doctype == {"IR": 1201, "IA": 617, "IT": 172, "CC": 5, "IE": 5}
    assert by_lang == {"es": 1697, "bi": 264, "cat": 39}
    assert set(by_doctype) == CARMEN_DOCTYPES


# ─── the threshold is derived from the proportions ───────────────────────────


def test_the_threshold_comes_out_of_the_committed_proportions():
    """§9.5 item 1's value of 5, computed from `config/split.yaml` rather than asserted.

    And the entry must not carry a threshold of its own: two copies of one number can
    disagree, and the config comment promises this one is not there.
    """
    params = split.construction_params(CARMEN)
    assert split.small_cell_threshold(params["proportions"]) == SMALL_CELL_THRESHOLD
    assert "small_cell_threshold" not in params
    assert "threshold" not in params


@pytest.mark.parametrize(
    "proportions, expected",
    [
        ({"train": 0.60, "dev": 0.20, "test": 0.20}, 5),
        ({"train": 0.70, "dev": 0.15, "test": 0.15}, 7),
        ({"train": 0.80, "dev": 0.10, "test": 0.10}, 10),
        ({"train": 0.50, "dev": 0.30, "test": 0.20}, 5),
    ],
)
def test_the_formula_gives_the_values_design_pre_registers(proportions, expected):
    """§9.5 item 1: 5 here, 7 at 70/15/15, 10 at 80/10/10 — derived, not a constant.

    The last row is de-grascco's proportions, which also give 5. §9.5 calls that a
    coincidence of the shared 0.20 smallest fold, and it is in the table so that a
    reading of 5 as a project-wide constant has to survive the 7 and the 10 next to it.
    """
    assert split.small_cell_threshold(proportions) == expected


@pytest.mark.parametrize("smallest", [0.20, 0.15, 0.10, 0.30, 0.05])
def test_the_threshold_is_where_the_smallest_folds_share_reaches_one_document(smallest):
    """The property §9.5 item 1 defines, not the arithmetic that implements it.

    `threshold × smallest ≥ 1` and one document fewer falls short. This is what separates
    `ceil` from `floor`: at 0.20 both give 5, because 1/0.20 is exactly 5 — 0.15 is the
    case where rounding down would declare a cell of 6 large enough for a fold that gets
    0.9 of a document out of it.
    """
    proportions = {"train": 1 - 2 * smallest, "dev": smallest, "test": smallest}
    threshold = split.small_cell_threshold(proportions)
    assert threshold * smallest >= 1.0
    assert (threshold - 1) * smallest < 1.0


# ─── the collapse, on the shape the corpus actually has ──────────────────────


def test_the_measured_cross_tab_collapses_to_eleven_strata():
    """§9.5 item 3, re-derived: 12 non-empty cells, one collapse, 11 strata.

    The count is the whole reason `config/split.yaml` can declare `n_strata: 11` as a
    check rather than as an instruction.
    """
    units, keys = _carmen_units()
    cells = split.cross_cells(units, keys)
    assert len(cells) == len(CARMEN_CROSS) == 12
    strata, collapsed = split.collapse_small_cells(cells, SMALL_CELL_THRESHOLD)
    assert len(strata) == N_CARMEN_STRATA
    assert collapsed == CARMEN_COLLAPSED


def test_the_collapse_fires_on_cc_and_on_nothing_else():
    """`CC`'s 4 and 1 are the cells below 5; `IE`'s single cell of 5 is exactly at it.

    `IE` is the boundary case and it is asserted separately: a `<=` in place of the `<`
    would collapse a stratum that needs no collapsing, and since `IE` has one non-empty
    cell the *count* of strata would not move — 11 either way. Only the stratum's name
    changes, from `IE/es` to `IE/*`, and that name is what the split file records.
    """
    units, keys = _carmen_units()
    strata, _ = split.collapse_small_cells(
        split.cross_cells(units, keys), SMALL_CELL_THRESHOLD
    )
    assert ("CC", "*") in strata
    assert sum(len(unit) for unit in strata[("CC", "*")]) == 5
    assert ("IE", "es") in strata
    assert ("IE", "*") not in strata
    # And the three large document types keep all three languages.
    for doctype in ("IR", "IA", "IT"):
        assert {
            secondary for primary, secondary in strata if primary == doctype
        } == {"es", "bi", "cat"}


def test_no_stratum_is_still_below_the_threshold_on_this_release():
    """§9.5 item 4's terminal case is unreachable here, and this is what says so.

    `CC` collapses to exactly 5. If a re-release made the collapsed stratum smaller the
    split is still valid — item 4 keeps it as it is — but the shortfall has to be
    recorded, and the honest way to discover that is here rather than in a reader's
    reading of the achieved composition.
    """
    units, keys = _carmen_units()
    strata, _ = split.collapse_small_cells(
        split.cross_cells(units, keys), SMALL_CELL_THRESHOLD
    )
    small = {
        name: sum(len(unit) for unit in unit_list)
        for name, unit_list in strata.items()
        if sum(len(unit) for unit in unit_list) < SMALL_CELL_THRESHOLD
    }
    assert small == {}


def test_language_folds_and_document_type_never_merges():
    """§9.5 item 2: the secondary dimension is the one that collapses.

    Merging two document types would also reduce the count to 11 from some shapes, so the
    count alone does not distinguish the two rules. What distinguishes them is that every
    stratum names exactly one document type and all five are still present.
    """
    units, keys = _carmen_units()
    strata, _ = split.collapse_small_cells(
        split.cross_cells(units, keys), SMALL_CELL_THRESHOLD
    )
    assert {primary for primary, _ in strata} == CARMEN_DOCTYPES
    for (primary, _), unit_list in strata.items():
        doctypes = {keys[doc_id][0] for unit in unit_list for doc_id in unit}
        assert doctypes == {primary}


def test_a_collapsed_stratum_below_the_threshold_keeps_its_shape():
    """§9.5 item 4, on a synthetic shape because es-carmen cannot reach it.

    Two cells of one each collapse to a stratum of two, which is still below 5, and it is
    left alone rather than merged into the neighbouring document type. The neighbour is
    there to be dragged in if the rule ever starts merging primaries.
    """
    units = [["a"], ["b"]] + [[f"c{i:02d}"] for i in range(9)]
    keys = {"a": ("XX", "es"), "b": ("XX", "bi")}
    keys.update({f"c{i:02d}": ("YY", "es") for i in range(9)})
    strata, collapsed = split.collapse_small_cells(
        split.cross_cells(units, keys), SMALL_CELL_THRESHOLD
    )
    assert collapsed == ["XX"]
    assert sum(len(unit) for unit in strata[("XX", "*")]) == 2
    assert ("YY", "es") in strata
    assert len(strata) == 2


def test_a_unit_holding_two_cells_is_refused():
    """A §9.5 group split across cells would be recorded in a cell it is not all in.

    Unreachable on es-carmen, where every unit is one document. It is checked because the
    next corpus to want a cross stratification may have confirmed groups, and the failure
    is silent: the composition would simply be wrong for some of the documents.
    """
    keys = {"a": ("IR", "es"), "b": ("IR", "bi")}
    with pytest.raises(CorpusError, match="different stratification cells"):
        split.cross_cells([["a", "b"]], keys)


# ─── the declared count, and the dispatch ────────────────────────────────────


def test_config_declares_the_post_collapse_count_and_the_crossed_keys():
    """`config/split.yaml`'s es-carmen entry, against §9.5 and against the code's map."""
    params = split.construction_params(CARMEN)
    assert params["stratify_by"] == CARMEN_VARIABLE
    assert params["n_strata"] == N_CARMEN_STRATA
    assert split.CROSS_VARIABLES[CARMEN_VARIABLE] == (
        "filename_doctype",
        "language_label",
    )
    assert params["proportions"] == {"train": 0.60, "dev": 0.20, "test": 0.20}


def test_the_declared_count_is_checked_against_the_derived_one():
    """A release that gained a label must not be recorded under the old count.

    The declared 11 passes; 12 fails. Which way round matters: the check exists because
    the strata come from the corpus's labels, so the config is the assertion and the data
    is the answer.
    """
    units, keys = _carmen_units()
    params = split.construction_params(CARMEN)
    ordered = split.strata(
        units,
        {},
        variable=CARMEN_VARIABLE,
        n_strata=N_CARMEN_STRATA,
        proportions=params["proportions"],
        keys=keys,
    )
    assert len(ordered) == N_CARMEN_STRATA
    with pytest.raises(CorpusError, match="n_strata=12"):
        split.strata(
            units,
            {},
            variable=CARMEN_VARIABLE,
            n_strata=12,
            proportions=params["proportions"],
            keys=keys,
        )


def test_the_strata_are_ordered_largest_first():
    """A fixed order makes the split deterministic; this one is chosen (see the code).

    Asserted because the order is an input to the assignment: two orders give two splits,
    and a split file that does not fix it is not reproducible from the seed alone.
    """
    units, keys = _carmen_units()
    params = split.construction_params(CARMEN)
    ordered = split.strata(
        units,
        {},
        variable=CARMEN_VARIABLE,
        n_strata=N_CARMEN_STRATA,
        proportions=params["proportions"],
        keys=keys,
    )
    sizes = [sum(len(u) for u in unit_list) for unit_list in ordered.values()]
    assert sizes == sorted(sizes, reverse=True)
    assert list(ordered)[0] == "IR/es"
    assert list(ordered)[-1] == "IE/es"


def test_a_cross_variable_without_keys_does_not_fall_back():
    """No silent band stratification under a cross variable's name."""
    units, _ = _carmen_units()
    with pytest.raises(CorpusError, match="cell keys"):
        split.strata(
            units,
            {},
            variable=CARMEN_VARIABLE,
            n_strata=N_CARMEN_STRATA,
            proportions={"train": 0.60, "dev": 0.20, "test": 0.20},
        )


def test_an_unimplemented_stratification_variable_is_refused():
    """The dispatch is a choice between declared variables, not a default.

    A typo in `stratify_by` must not deliver span-count terciles under another name.
    """
    with pytest.raises(CorpusError, match="which is neither"):
        split.strata(
            [["a"]],
            {"a": 1},
            variable="document_type",
            n_strata=3,
            proportions={"train": 0.60, "dev": 0.20, "test": 0.20},
        )


def test_stratum_keys_are_none_for_a_band_variable():
    """The two constructed splits that are already frozen take the other branch."""
    assert split.stratum_keys(split.SPAN_COUNT_VARIABLE, []) is None


# ─── the band path is unchanged by all of the above ──────────────────────────


#: `assign_folds` was refactored to take strata instead of computing them, and the two
#: frozen constructed splits cannot be rebuilt to prove it — their test folds are sealed
#: and `_build_constructed` refuses when a sealed root is declared. So the assignment was
#: digested on synthetic input *before* the refactor and this is that digest. It is a
#: golden value with no independent derivation: its only job is to fail if the band path's
#: output ever moves, whoever moves it.
GOLDEN_BAND_DIGEST = (
    "381c0fd35c892261285484584897eb8309b15867acb5071bf10a4dd61ca2564b"
)


def test_the_band_assignment_is_byte_identical_to_before_the_cross_refactor():
    """300 singletons and 10 pairs, span counts from a fixed seed, de-grascco's shape.

    Pairs are in the input because the multi-document unit is where `assign_folds`'s
    largest-first rule earns its docstring; a digest over singletons alone would not
    notice if that rule were dropped.
    """
    import hashlib
    import random as _random

    units = [[f"d{i:04d}"] for i in range(300)]
    units += [[f"g{i}_1", f"g{i}_2"] for i in range(10)]
    rng = _random.Random(7)
    sizes = {doc_id: rng.randrange(0, 60) for unit in units for doc_id in unit}
    proportions = {"train": 0.5, "dev": 0.3, "test": 0.2}

    bands = split.strata(
        units,
        sizes,
        variable=split.SPAN_COUNT_VARIABLE,
        n_strata=3,
        proportions=proportions,
    )
    assert [len(band) for band in bands.values()] == [104, 103, 103]
    assert list(bands) == ["band_1", "band_2", "band_3"]

    assigned = split.assign_folds(bands, proportions=proportions, seed=20260921)
    digest = hashlib.sha256(
        json.dumps(assigned, sort_keys=True).encode()
    ).hexdigest()
    assert digest == GOLDEN_BAND_DIGEST
    counts = {fold: 0 for fold in base.split_names()}
    for fold in assigned.values():
        counts[fold] += 1
    assert counts == {"train": 160, "dev": 96, "test": 64}


# ─── es-carmen's §9.5 step-1 key (the section token is not part of it) ────────


def _carmen_doc(doc_id, doctype, section, index):
    """A stand-in carrying only what §9.5 step 1 reads: the id, the two meta fields.

    `types.SimpleNamespace` rather than a `Document`: the key is defined as a function of
    `meta`, and a stub is what says so. A real document would also carry text, and no
    es-carmen text may enter a committed file or a test fixture (CLAUDE.md).
    """
    return SimpleNamespace(
        doc_id=doc_id,
        corpus_id=CARMEN,
        spans=[],
        meta={
            "filename_doctype": doctype,
            "filename_section": section,
            "filename_index": index,
            "language_label": "es",
        },
    )


CARMEN_STEP_1_DOCS = [
    _carmen_doc("CARMEN-I_IA_ANTECEDENTES_7", "IA", "ANTECEDENTES", 7),
    _carmen_doc("CARMEN-I_IA_PROCESO_ACTUAL_7", "IA", "PROCESO_ACTUAL", 7),
    _carmen_doc("CARMEN-I_IA_ANTECEDENTES_8", "IA", "ANTECEDENTES", 8),
    _carmen_doc("CARMEN-I_IR_7", "IR", None, 7),
]


def test_the_carmen_candidate_key_pairs_sections_of_one_letter():
    """§9.5 step 1 for es-carmen: `(doctype, trailing number)`, section token ignored.

    `IA_ANTECEDENTES_7` and `IA_PROCESO_ACTUAL_7` are the pair the rule asks about. The
    same trailing number under a different document type is not a candidate, and the same
    section under a different number is not either.
    """
    by_stem, unparsed = split.stem_index(CARMEN_STEP_1_DOCS, CARMEN)
    assert unparsed == []
    assert by_stem == {
        "IA_7": ["CARMEN-I_IA_ANTECEDENTES_7", "CARMEN-I_IA_PROCESO_ACTUAL_7"],
        "IA_8": ["CARMEN-I_IA_ANTECEDENTES_8"],
        "IR_7": ["CARMEN-I_IR_7"],
    }


def test_stem_re_would_ask_a_different_question_on_these_ids():
    """Why the key is declared rather than left to `STEM_RE`, asserted rather than said.

    `STEM_RE` pairs `ANTECEDENTES_7` with `ANTECEDENTES_8` — the *same section of
    different letters*, which no identifier could ever confirm or refute. Both groupings
    are plausible-looking and they are not the same grouping, so the split file records
    which one was asked.
    """
    by_stem, _ = split.stem_index(CARMEN_STEP_1_DOCS)
    assert "CARMEN-I_IA_ANTECEDENTES" in by_stem
    assert "IA_7" not in by_stem


def test_the_carmen_audit_records_the_declared_key_not_the_regex():
    """§9.5 step 4: the file says which question was asked.

    Recording `STEM_RE.pattern` here would describe a pattern that was not applied, and
    the audit's own candidate count would be the evidence against it — which is why both
    are checked in one test.
    """
    audit = split.grouping_audit(CARMEN, CARMEN_STEP_1_DOCS)
    assert "section token ignored" in audit["step_1_pattern"]
    assert audit["step_1_pattern"] != split.STEM_RE.pattern
    assert audit["step_2_types"]["name"] == ["NOMBRE_SUJETO_ASISTENCIA"]
    assert audit["n_candidate_stems"] == 1
    assert audit["n_stems_confirmed"] == 0
    assert audit["n_documents_grouped"] == 0


def test_a_missing_stratum_label_stops_the_split_and_names_no_text():
    """The label is the stratum, so a missing one is an unstratified split.

    The message names the document's *index* and the meta key. CLAUDE.md's rule that no
    corpus text reaches an exception message applies to every corpus, and this is the
    es-carmen path where a helpful message would most obviously have quoted the id.
    """
    docs = [
        SimpleNamespace(
            doc_id="CARMEN-I_IR_1",
            corpus_id=CARMEN,
            spans=[],
            meta={"filename_doctype": "IR"},
        )
    ]
    with pytest.raises(CorpusError) as excinfo:
        split.stratum_keys(CARMEN_VARIABLE, docs)
    message = str(excinfo.value)
    assert "language_label" in message
    assert "document index 0" in message
