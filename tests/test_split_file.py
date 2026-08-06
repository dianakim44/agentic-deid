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


# ─── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def record():
    """The committed split file. A missing file fails — it is version-controlled."""
    return split.read(CORPUS)


@pytest.fixture(scope="module")
def docs():
    """The corpus loaded *without* the split file, so the recount is independent.

    `use_split_file=False` is the point of the fixture: loading with the file and
    then checking the file against the result would be circular.
    """
    try:
        base.corpus_root(CORPUS)
    except CorpusError as exc:
        pytest.skip(f"MEDDOCAN not available on this machine: {exc}")
    return MeddocanLoader(use_split_file=False).load()


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

    A sample rather than all 1,000: hashing every file is a second full read of
    the corpus for a check whose failure mode (a re-release) is corpus-wide, not
    per-document. The manifest digest above already binds all 1,000 hashes
    together, so a single altered document cannot hide behind the sample.
    """
    loader = MeddocanLoader(use_split_file=False)
    recorded = record["source"]["documents"]
    sampled = sorted(recorded)[::100]
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


def test_grouping_audit_is_reproducible_from_the_corpus(record, docs):
    """Recompute the whole audit and require an exact match.

    This is the check that keeps §9.5 from drifting: if the step-1 pattern or the
    step-2 types change, the recorded decisions and the code's decisions part
    company here.
    """
    assert split.grouping_audit(CORPUS, docs) == record["group_key"]["grouping_audit"]


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
    """A new corpus must define its §9.5 comparison types, not skip the audit."""
    with pytest.raises(CorpusError, match="grouping types"):
        split.grouping_audit("de-grascco", [])


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
    """Loading normally must reproduce the folds the file records.

    The default path reads the file; `use_split_file=False` (used by the fixtures
    above) is only for the generator and for these tests.
    """
    loader = MeddocanLoader()
    assert loader.use_split_file
    loaded = loader.load()
    from_file = split.fold_of(record)
    assert {d.doc_id: d.split for d in loaded} == from_file
    assert base.count_by_split(loaded) == OFFICIAL_SPLIT


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
