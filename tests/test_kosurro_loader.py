"""The `ko-surro` loader: the gold-support filter, the closed schema, and the seal.

Written with `src/corpora/kosurro.py` rather than after it, and so are its mutations
(`tests/mutations/run.py`, the `kosurro_*` block). The two loaders before it arrived with no
mutation anchored on them and were measured weeks later; the leak rates published from them
in the meantime were published over an unverified reading of the corpus. Nothing here is a
new kind of test — it is the third loader's tests, on the day the loader lands.

Most of it runs on **synthetic roots** built in `tmp_path`: two-file derived roots holding
ASCII bodies, so the refusals can be provoked without the corpus and without writing a single
character of clinical text into this file. Three tests at the end need the corpus, take it
from `conftest`'s fixtures, and assert the counts DESIGN §9.0 pre-registered — 2,158 silver
spans, 1,614 supported, 1,611 in scope, 3 excluded — which is the one thing a synthetic root
cannot check.

The valid roots are written through `tools/prepare_kosurro.count_records`, deliberately: the
loader recounts what that tool wrote, so using the tool's own counter here means a drift
between the two shows up as a failure rather than as two consistent halves of a wrong number.

    python3 -m pytest tests/test_kosurro_loader.py -q
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from src import split
from src.corpora import base
from src.corpora.base import CorpusError, SealError
from src.corpora.kosurro import (
    CORPUS_FILE,
    REFERENCE_BASIS,
    REFERENCE_FILE,
    TYPE_MAP,
    KosurroLoader,
)
from tools.prepare_kosurro import count_records

#: Two markers that stand in for the two things that may never reach a message: a surrogate
#: value and a source placeholder literal. Distinctive strings rather than realistic ones, so
#: "is it in the message" is a question about the loader and not about how a realistic value
#: happens to be spelled.
SURFACE = "Sxxxxx"
LITERAL = "[**Pxxxxx**]"

#: A body with `SURFACE` at 4..10. Ordinary ASCII: the point of a synthetic root is that a
#: refusal can be provoked without clinical text of any language in this file.
BODY = f"aaa {SURFACE} bbb"


def span(
    start: int = 4,
    end: int = 10,
    type_: str = "LAST_NAME",
    surface: str = SURFACE,
    gold_supported: bool = True,
) -> dict:
    return {
        "start": start,
        "end": end,
        "type": type_,
        "surface": surface,
        "gold_supported": gold_supported,
    }


def record(
    uid: str = "1_1",
    patient: str = "1",
    note: str = "1",
    has_reference: bool = True,
    text: str = BODY,
    spans: list[dict] | None = None,
) -> dict:
    return {
        "uid": uid,
        "patient": patient,
        "note": note,
        "has_reference": has_reference,
        "text": text,
        "spans": [span()] if spans is None else spans,
    }


def write_root(
    path: Path,
    records: list[dict],
    *,
    counts: dict | None = None,
    reference: str = REFERENCE_BASIS,
) -> Path:
    """A derived root holding `records`, with a sidecar that describes them.

    `counts` and `reference` override what the sidecar says, which is how the two tests
    about the sidecar make it disagree with its own data.
    """
    path.mkdir(parents=True, exist_ok=True)
    (path / CORPUS_FILE).write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    (path / REFERENCE_FILE).write_text(
        json.dumps(
            {
                "corpus": "ko-surro",
                "reference": reference,
                "counts": count_records(records) if counts is None else counts,
            }
        ),
        encoding="utf-8",
    )
    return path


def loader_on(path: Path) -> KosurroLoader:
    """A loader reading one synthetic root and no split file."""
    return KosurroLoader(root=path, use_split_file=False)


# ─── the gold-support filter, which is this corpus's scoring decision ─────────


def test_a_span_the_reference_does_not_support_is_not_loaded_and_is_counted(tmp_path):
    """DESIGN §6.5 (v), and the third removal mechanism (§9.0).

    Two assertions in one test because they are one decision: the span does not reach the
    gold list, *and* the loader reports how many did not. Dropping it silently would make
    the corpus as published and the corpus as scored differ by a number nobody could see —
    544 of 2,158 on the real root.
    """
    root = write_root(
        tmp_path / "root",
        [record(spans=[span(), span(start=11, end=14, surface="bbb", gold_supported=False)])],
    )
    loader = loader_on(root)
    docs = loader.load()

    assert [s.surface for s in docs[0].spans] == [SURFACE]
    assert loader.not_gold_supported == 1
    assert docs[0].meta["n_spans_not_gold_supported"] == 1


def test_the_denied_count_is_per_record_and_not_a_running_total(tmp_path):
    """The second record's count is its own, not the corpus's so far.

    It goes into that record's digest (`digest_parts`), so a running total would make every
    document's digest depend on the documents before it and put file order into the split
    file's manifest with nothing saying so.
    """
    denied = span(start=11, end=14, surface="bbb", gold_supported=False)
    root = write_root(
        tmp_path / "root",
        [
            record(uid="1_1", spans=[span(), denied]),
            record(uid="1_2", patient="1", note="2", spans=[span()]),
        ],
    )
    docs = {doc.doc_id: doc for doc in loader_on(root).load()}

    assert docs["1_1"].meta["n_spans_not_gold_supported"] == 1
    assert docs["1_2"].meta["n_spans_not_gold_supported"] == 0


def test_the_denied_count_enters_the_documents_digest(tmp_path):
    """Two roots whose loaded spans are identical and whose denied counts are not.

    The digest is what `splits/ko-surro.json` records per document, and the denied spans are
    part of what `prepare_kosurro.py` wrote. Leaving them out entirely would mean the filter
    could move without any frozen file noticing.
    """
    with_denied = write_root(
        tmp_path / "a",
        [record(spans=[span(), span(start=11, end=14, surface="bbb", gold_supported=False)])],
    )
    without = write_root(tmp_path / "b", [record(spans=[span()])])

    digests = []
    for root in (with_denied, without):
        loader = loader_on(root)
        doc = loader.load()[0]
        digests.append(
            [name for name, _ in loader.digest_parts(doc)]
            + [payload for _, payload in loader.digest_parts(doc)]
        )
    assert digests[0] != digests[1]


def test_the_type_map_is_checked_against_the_denied_spans_too(tmp_path):
    """Classification happens before the filter, so the map covers all 2,158 tags.

    Three of this corpus's tags occur only among the spans the filter denies (§9.0). If the
    filter ran first they would never be classified, and the type map's exhaustiveness would
    quietly become a property of the reference's verdicts rather than of the corpus.
    """
    root = write_root(
        tmp_path / "root",
        [record(spans=[span(type_="NO_SUCH_TAG", gold_supported=False)])],
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "NO_SUCH_TAG" in str(exc.value)


def test_a_non_boolean_verdict_is_refused(tmp_path):
    """`gold_supported` decides whether the span is gold at all; a truthy value is not a verdict.

    The string `"false"` is the case that matters: it is truthy, so a loader that tested it
    for truth instead of for being a boolean would load a span the reference denies and count
    it as gold.
    """
    root = write_root(tmp_path / "root", [record(spans=[span(gold_supported="false")])])
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "gold_supported" in str(exc.value)


# ─── the exclusion, which uses §9.1's mechanism and not §9.1's list ──────────


def test_the_restored_tag_is_excluded_by_the_mechanism(tmp_path):
    """Kept, flagged, and carrying no `phi_type` — §9.1's mechanism exactly."""
    root = write_root(tmp_path / "root", [record(spans=[span(type_="NOT_PHI_RESTORED")])])
    doc = loader_on(root).load()[0]

    assert len(doc.spans) == 1
    assert doc.spans[0].excluded is True
    assert doc.spans[0].phi_type is None
    assert doc.in_scope_spans == []


def test_the_restored_tag_is_not_on_the_cross_corpus_exclusion_list():
    """It uses the mechanism and stays off `config/naming.yaml`'s three-name list.

    That list is the concept vocabulary an Auditor is shown; this is a provenance tag of one
    corpus's producing project, which no detector can emit. `test_excluded_types.py` pins the
    list at three and this test is the other half of the same decision, stated where the
    loader that needs the mechanism is (DESIGN §9.1, the 2026-09-22 note).
    """
    assert "NOT_PHI_RESTORED" not in base.excluded_types()
    assert "NOT_PHI_RESTORED" not in TYPE_MAP


# ─── the closed schema, and the two keys that carry corpus text ───────────────


@pytest.mark.parametrize("field", ["src_tag", "surrogate"])
def test_a_record_carrying_a_text_bearing_key_is_refused(tmp_path, field):
    """The source derivation's two text-bearing keys do not enter this repository's reach.

    30.5% of placeholder payloads are values rather than type names, so the literal is corpus
    text as much as the body is (`ko-surro-gold-provenance.md` §10.7). The rule is enforced
    where the data enters rather than remembered at every message: `prepare_kosurro.py` does
    not write either key, and a root that carries one is refused instead of read.
    """
    item = record()
    item["spans"][0][field] = LITERAL if field == "src_tag" else SURFACE
    root = write_root(tmp_path / "root", [item])

    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    message = str(exc.value)
    assert field in message
    assert "corpus text" in message
    assert LITERAL not in message and SURFACE not in message


def test_an_unexpected_key_is_refused_rather_than_ignored(tmp_path):
    """A field the loader does not know is a field nothing checks.

    The reason the schema is closed rather than a minimum: the two keys above are keys of the
    source files, and a loader that read what it wanted and ignored the rest would read a root
    that still carried them without anyone noticing.
    """
    item = record()
    item["layer"] = "rules"
    root = write_root(tmp_path / "root", [item])
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "layer" in str(exc.value)


def test_a_missing_key_is_refused(tmp_path):
    """The sidecar counts have to be passed in here: `count_records` cannot count this record.

    Which is the schema doing its job one layer earlier — the tool that writes a root reads
    the same keys the loader does, so a record missing one does not get counted into a sidecar
    that would then describe it.
    """
    item = record()
    counts = count_records([item])
    del item["has_reference"]
    root = write_root(tmp_path / "root", [item], counts=counts)
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "has_reference" in str(exc.value)


def test_no_refusal_quotes_the_surface_or_the_literal(tmp_path):
    """The rule CLAUDE.md states about messages, asserted rather than remembered.

    `tests/test_meddocan_loader.py`'s `test_offset_mismatch_message_quotes_no_surface` is the
    same test for the first loader, and it exists because the convention came back the moment
    it was only written down. An exception message travels into terminals, CI logs and issues,
    and `tools/release_screen.py` reaches none of those.
    """
    root = write_root(
        tmp_path / "root",
        [record(spans=[span(start=0, end=6)])],  # offsets that do not hold the surface
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    message = str(exc.value)
    assert SURFACE not in message
    assert "LAST_NAME" in message and "1_1" in message


# ─── records the reference does not speak about ───────────────────────────────


def test_a_record_outside_the_reference_is_loaded_into_no_fold(tmp_path):
    """Absence of coverage is not a claim of PHI-freeness (DESIGN §9.0).

    The nine records on the real root. They are reported rather than dropped silently,
    because `src/split.py`'s derived route refuses unless this list is exactly the one
    `splits/en-deid.json` leaves outside every fold — one reference serves both halves of the
    pair.
    """
    root = write_root(
        tmp_path / "root",
        [record(uid="1_1"), record(uid="1_2", note="2", has_reference=False, spans=[])],
    )
    loader = loader_on(root)
    docs = loader.load()

    assert [doc.doc_id for doc in docs] == ["1_1"]
    assert loader.uncovered == ["1_2"]


def test_a_record_outside_the_reference_may_not_carry_a_verdict(tmp_path):
    """A record the reference never mentions cannot have gold-support verdicts."""
    root = write_root(
        tmp_path / "root", [record(has_reference=False, spans=[span()])]
    )
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "has_reference" in str(exc.value)


# ─── the sidecar describes the root it sits in ────────────────────────────────


def test_a_root_built_on_another_span_set_is_refused(tmp_path):
    """The pre-registered reference, checked on every load (DESIGN §6.5 (v)).

    A root built against raw silver, or against the human reference, is a different
    experiment with the same file names. Refusing beats loading it and reporting a leak rate
    that is not on the scale the other three corpora's are.
    """
    root = write_root(tmp_path / "root", [record()], reference="silver as shipped")
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert REFERENCE_BASIS in str(exc.value)


def test_the_sidecar_counts_are_recounted_and_not_trusted(tmp_path):
    """The split file's denominators come from the file that was read, not from the sidecar."""
    counts = count_records([record()])
    counts["gold_supported"] += 1
    root = write_root(tmp_path / "root", [record()], counts=counts)
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert "gold_supported" in str(exc.value)


def test_both_files_are_required(tmp_path):
    root = write_root(tmp_path / "root", [record()])
    (root / REFERENCE_FILE).unlink()
    with pytest.raises(CorpusError) as exc:
        loader_on(root).load()
    assert REFERENCE_FILE in str(exc.value)


# ─── the layout, and the seal ────────────────────────────────────────────────


def test_the_layout_encodes_no_fold(tmp_path):
    """A document is a record inside a file, so there is no directory per fold.

    `fold_roots()` refuses rather than answering, which is what keeps the frozen split file
    the only authority on which fold a note is in.
    """
    loader = loader_on(write_root(tmp_path / "root", [record()]))
    assert loader.fold_dirs == {}
    with pytest.raises(CorpusError):
        loader.fold_roots()
    with pytest.raises(CorpusError):
        loader.source_files("1_1")


def test_each_roots_sidecar_is_checked_against_that_root(tmp_path):
    """Both roots hold a record outside the reference, and the read succeeds.

    `reference.json` is written per root with that root's own counts, so the cross-check has
    to be per root too. It was not: the expected `records_without_reference` came from
    `len(self.uncovered)`, which is the corpus-wide list and already holds the corpus root's
    records by the time the sealed root's sidecar is read. A sealed read of the real corpus
    would have refused — the nine reference-less records are routed to folds by patient like
    every other record, so both roots have some — and it would have refused *after* the access
    was logged, for having found exactly what the seal put there.
    """
    corpus = write_root(
        tmp_path / "root",
        [record(uid="1_1"), record(uid="1_2", note="2", has_reference=False, spans=[])],
    )
    sealed = write_root(
        tmp_path / "sealed",
        [
            record(uid="9_1", patient="9"),
            record(uid="9_2", patient="9", note="2", has_reference=False, spans=[]),
        ],
    )
    loader = loader_on(corpus)
    loader._sealed_ok = True

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(base, "sealed_root", lambda corpus_id: sealed)
        docs = loader.load()

    assert [doc.doc_id for doc in docs] == ["1_1", "9_1"]
    assert loader.uncovered == ["1_2", "9_2"]


def test_an_empty_sealed_read_raises_rather_than_returning_the_rest(tmp_path):
    """A sealed read that reached no sealed record is a failure, not a smaller corpus.

    The access is in `results/sealed_eval_log.md` before anything is opened, so a read that
    quietly returned the unsealed records would produce numbers from the wrong data under a
    log row saying the test fold was evaluated. The sealed root here holds one record the
    reference does not speak about, which is the shape that gets past "the file is empty" and
    still yields nothing.

    `_sealed_ok` is set directly and `sealed_root` is patched for this test only: the subject
    is `_read`'s invariant, not the authorisation that precedes it, and authorising properly
    would mean appending to the real log.
    """
    corpus = write_root(tmp_path / "root", [record()])
    sealed = write_root(
        tmp_path / "sealed",
        [record(uid="9_1", patient="9", has_reference=False, spans=[])],
    )
    loader = loader_on(corpus)
    loader._sealed_ok = True

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(base, "sealed_root", lambda corpus_id: sealed)
        with pytest.raises(SealError) as exc:
            loader.load()
    assert "did not complete" in str(exc.value)


# ─── the corpus itself, against the pre-registered counts ────────────────────


def test_the_corpus_reproduces_the_pre_registered_span_set(kosurro_docs, kosurro_unsplit_loader):
    """DESIGN §9.0's `ko-surro` table, recounted from the corpus root.

    Written before the first Korean arm runs and asserting the numbers that were
    pre-registered rather than the numbers a run happens to produce. If the derived root is
    rebuilt and any of these moves, the scoring basis moved and the pre-registration is what
    says so.
    """
    loader = kosurro_unsplit_loader
    spans = [s for doc in kosurro_docs for s in doc.spans]
    by_type = Counter(s.phi_type for s in spans if not s.excluded)

    assert (len(kosurro_docs), len(loader.uncovered)) == (2425, 9)
    assert loader.not_gold_supported == 544
    assert len(spans) == 1614
    assert sum(1 for s in spans if s.excluded) == 3
    assert sum(by_type.values()) == 1611
    assert by_type == {
        "NAME": 806,
        "DATE": 468,
        "ORGANISATION": 242,
        "LOCATION_AREA": 45,
        "CONTACT": 44,
        "AGE": 3,
        "LOCATION_STREET": 2,
        "ID": 1,
    }


def test_every_loaded_span_agrees_with_the_slice(kosurro_docs):
    """`assert_offsets()` over the whole corpus, with the surfaces read independently.

    The surface is the surrogate string `prepare_kosurro.py` read from the Korean derivation
    and the slice comes from the body, so this compares two readings rather than a value with
    itself — the property GraSCCo's loader cannot have and this one can.
    """
    for doc in kosurro_docs:
        doc.assert_offsets()


def test_the_patient_key_is_read_from_meta_and_never_split_out_of_the_id(
    kosurro_docs, kosurro_unsplit_loader
):
    """163 patients over 2,425 notes, and the key comes from the record's own field.

    The composition `{patient}_{note}` is the release's and is made in one direction only, so
    a loader that recovered the patient by splitting `doc_id` would be a second answer to
    which half is the patient — on a corpus whose folds are patient-disjoint.
    """
    keys = {kosurro_unsplit_loader.patient_key(doc) for doc in kosurro_docs}
    assert len(keys) == 163
    for doc in kosurro_docs[:50]:
        assert doc.doc_id == f"{doc.meta['patient_id']}_{doc.meta['note_index']}"


# ─── the frozen split file ───────────────────────────────────────────────────
#
# `splits/ko-surro.json` was frozen on 2026-09-23 and these tests arrived with it, in this
# file rather than in `tests/test_split_file.py` — that file is MEDDOCAN's, and each later
# corpus's split file is checked beside its own loader (`test_endeid_loader.py` §"the split
# file and the seal" is the shape being followed).
#
# The figures below are written out again rather than imported from
# `tests/test_derive_aligned_split.py`, which pins the same numbers on the *tool*. Two copies
# of a number that must agree is the point: one edit cannot move both, so a drift between the
# derivation and the file it produced shows up as a failure instead of as two consistent
# halves of a wrong split.

#: DESIGN §9.6. Not sampled here — this is `splits/en-deid.json`'s split, per document.
DERIVED_SPLIT = {"train": 1456, "dev": 485, "test": 484}

#: The split this one is derived from, pinned in the file's provenance. A resampling of
#: `en-deid` would change its manifest digest and this corpus's file would no longer describe
#: the split it claims to follow.
SOURCE_CORPUS = "en-deid"
SOURCE_MANIFEST_DIGEST = "11849ae2911b4e32b368fb3ae9cd45b4c2db73b97e089cf1b5ce341114469a0e"
SOURCE_FREEZE_COMMIT = "25c56cbe1bb46ffb6efe5aa835dcde94db4b99c0"


@pytest.fixture(scope="module")
def split_record(kosurro_present):
    """The frozen split file, parsed. No `try`: a file that does not parse is a defect."""
    return split.read(kosurro_present)


def test_the_split_route_is_declared():
    """The third route, and the only corpus that takes it (DESIGN §6.5 option B)."""
    assert split.SPLIT_ORIGIN["ko-surro"] == "derived"


def test_the_fold_sizes_are_the_derived_split(split_record):
    assert {f: b["n_documents"] for f, b in split_record["folds"].items()} == DERIVED_SPLIT
    assert sum(DERIVED_SPLIT.values()) == split_record["totals"]["n_documents"]


def test_the_provenance_says_nothing_was_sampled(split_record):
    """No seed and no stratification, because there was no draw to record.

    A seed here would be the strongest possible evidence that a second sampling happened:
    the fold of a `ko-surro` note is a fact about `splits/en-deid.json`, and anything this
    file could seed would be a way of disagreeing with it.
    """
    provenance = split_record["provenance"]
    assert provenance["origin"] == "derived"
    assert provenance["seed"] is None
    assert provenance["stratification"] is None


def test_the_file_pins_the_split_it_was_derived_from(split_record):
    """The derivation names its source and pins the version of it, by digest and by commit."""
    derived = split_record["provenance"]["derived_from"]
    assert derived["corpus"] == SOURCE_CORPUS
    assert derived["derivation"] == "tools/derive_aligned_split.py"
    assert derived["source_manifest_digest"] == SOURCE_MANIFEST_DIGEST
    assert derived["source_freeze_commit"] == SOURCE_FREEZE_COMMIT
    source = split.read(SOURCE_CORPUS)
    assert source["source"]["manifest_digest"] == SOURCE_MANIFEST_DIGEST


def test_every_note_is_in_the_fold_its_source_note_is_in(split_record):
    """The claim the freeze makes, checked against the other file rather than restated.

    This is what the derived route is *for*: a note and its Korean surrogate carry the same
    id, and a note whose surrogate sat in another fold would put one corpus's dev text behind
    the other corpus's seal — which is a leak with a report saying the folds are disjoint. The
    comparison is document by document, not fold size by fold size: three folds of the right
    sizes can still be three wrong folds.
    """
    ours = split.fold_of(split_record)
    theirs = split.fold_of(split.read(SOURCE_CORPUS))
    assert set(ours) == set(theirs)
    assert {doc_id: fold for doc_id, fold in ours.items() if theirs[doc_id] != fold} == {}


def test_the_scoring_basis_is_recorded_with_the_split(split_record):
    """What the leak rate will be scored against, written into the file being frozen.

    DESIGN §6.5 (v) chose human-verified silver over raw silver and over the human reference,
    and §9.3 records that this is the one corpus of four whose reference is not purely human.
    Recording the choice in the split file is what makes it a pre-registration rather than a
    decision available for revision once the first Korean numbers are in: 1,611 spans is the
    denominator, and 544 silver spans the human reference does not support are not in it.
    """
    specific = split_record["corpus_specific"]
    assert specific["reference"] == "human-verified silver"
    assert specific["n_spans_not_gold_supported"] == 544
    assert split_record["totals"]["n_spans_in_scope"] == 1611
    assert split_record["totals"]["n_spans"] == 1614
    assert split_record["totals"]["n_spans_excluded"] == 3
    assert split_record["totals"]["spans_by_excluded_type"] == {"NOT_PHI_RESTORED": 3}
    for section in ("§6.5", "§9.3"):
        assert section in specific["reference_note"], section


def test_the_token_counts_are_marked_as_not_comparable_across_the_pair(split_record):
    """Both corpora record a token total and the two are not on one scale.

    The shared tokenizer splits on whitespace, which counts eojeol in Korean and words in
    English, so spans-per-1,000-tokens across the pair would be a ratio of two different
    units. The note is in the file because the numbers are in the file: a reader who has the
    totals will divide them unless the file says not to.
    """
    note = split_record["corpus_specific"]["tokenizer_note"]
    assert split_record["tokenizer"] == "whitespace"
    assert "eojeol" in note
    assert split_record["totals"]["tokens"]["total"] == 293263


def test_the_group_key_is_the_patient_carried_with_the_fold(split_record):
    """163 patients, none crossing, and the §9.5 surface rule recorded as not having run."""
    group = split_record["group_key"]
    assert group["n_groups"] == 163
    assert group["unit"].startswith("patient")
    assert group["crosses_split"]["n_groups_crossing"] == 0
    audit = group["grouping_audit"]
    assert audit["n_patients"] == 163
    assert audit["n_documents"] == split_record["totals"]["n_documents"]
    assert "did not run" in audit["identifying_surface_rule"]
    for key in ("step_1_pattern", "step_2_types", "candidate_stems"):
        assert key not in audit


def test_the_grouping_audit_is_a_partition_of_the_split(split_record):
    ids = [doc_id for unit in split_record["group_key"]["grouping_audit"]["patients"].values() for doc_id in unit]
    assert sorted(ids) == sorted(split.fold_of(split_record))
    assert len(ids) == split_record["totals"]["n_documents"]


def test_the_records_without_a_reference_are_in_no_fold(split_record):
    """The nine, held as the same nine on both sides of the pair.

    `src/split.py`'s derived route refuses unless this list is exactly what
    `splits/en-deid.json` leaves outside every fold, so the assertion is about the two files
    agreeing rather than about nine ids being spelled correctly twice.
    """
    specific = split_record["corpus_specific"]
    assert specific["n_records_without_reference"] == 9
    listed = specific["records_without_reference"]
    assert len(listed) == 9
    placed = split.fold_of(split_record)
    assert [doc_id for doc_id in listed if doc_id in placed] == []
    source = split.read(SOURCE_CORPUS)
    assert sorted(listed) == sorted(source["corpus_specific"]["records_without_reference"])


def test_the_totals_are_a_recount_of_the_whole_corpus(split_record, kosurro_docs):
    """Every summary in the file, re-derived from the corpus while the corpus is readable.

    This test can only exist before the seal, which is why it is written on the day of the
    freeze: the test fold is still in the corpus root, so all 2,425 documents recount and the
    totals block is a claim that can be falsified outright. Once `sealed/` holds the test
    fold, this becomes the arithmetic form `test_endeid_loader.py` uses — visible recount plus
    the file's sealed block equals the totals — which is weaker, and is all that is left.
    """
    in_scope = [s for doc in kosurro_docs for s in doc.spans if not s.excluded]
    excluded = [s for doc in kosurro_docs for s in doc.spans if s.excluded]
    totals = split_record["totals"]
    assert len(kosurro_docs) == totals["n_documents"]
    assert len(in_scope) + len(excluded) == totals["n_spans"]
    assert len(in_scope) == totals["n_spans_in_scope"]
    assert dict(Counter(s.phi_type for s in in_scope)) == totals["spans_by_phi_type"]
    # An excluded span has no canonical type — `phi_type` is None and the source tag it was
    # excluded for is its `subtype`, which is what §9.1's volume is reported by.
    assert dict(Counter(s.subtype for s in excluded)) == totals["spans_by_excluded_type"]
    with_spans = sum(1 for doc in kosurro_docs if any(not s.excluded for s in doc.spans))
    assert with_spans == split_record["corpus_specific"]["n_documents_with_spans"]


def test_each_folds_summaries_are_a_recount_of_that_fold(split_record, kosurro_docs):
    """And per fold, which the totals cannot check: one fold's spans could sit in another.

    Same window as the test above — before the seal every fold is reachable, so the sealed
    fold's block is recounted here once and never again.
    """
    by_id = {doc.doc_id: doc for doc in kosurro_docs}
    for fold, block in split_record["folds"].items():
        docs = [by_id[doc_id] for doc_id in block["document_ids"]]
        in_scope = [s for doc in docs for s in doc.spans if not s.excluded]
        excluded = [s for doc in docs for s in doc.spans if s.excluded]
        assert len(docs) == block["n_documents"], fold
        assert len(in_scope) == block["n_spans_in_scope"], fold
        assert len(excluded) == block["n_spans_excluded"], fold
        assert len(in_scope) + len(excluded) == block["n_spans"], fold
        assert dict(Counter(s.phi_type for s in in_scope)) == block["spans_by_phi_type"], fold


def test_the_folds_partition_the_documents(split_record):
    """No document in two folds and none in none, from the file alone."""
    ids = [doc_id for block in split_record["folds"].values() for doc_id in block["document_ids"]]
    assert len(ids) == len(set(ids)) == split_record["totals"]["n_documents"]
    assert set(ids) == set(split_record["source"]["documents"])


def test_the_loader_takes_every_fold_from_the_file(kosurro_loader, split_record):
    """The fold on a `Document` is the file's, for every document, with nothing left `None`.

    The layout encodes no fold (`test_the_layout_encodes_no_fold`), so this is the only route
    a fold can arrive by — and a loader that silently left `split=None` would hand the
    dev-only rule development the whole corpus.
    """
    folds = split.fold_of(split_record)
    docs = kosurro_loader.load()
    assert {doc.doc_id: doc.split for doc in docs} == {
        doc_id: fold for doc_id, fold in folds.items() if doc_id in {d.doc_id for d in docs}
    }
    assert None not in {doc.split for doc in docs}


def test_the_split_file_records_the_bytes_it_hashed(split_record, kosurro_docs, kosurro_unsplit_loader):
    """Every reachable document's digest recomputes to what the frozen file recorded.

    Through `digest_parts` — a `ko-surro` document is a record inside a file, so
    `source_files()` refuses and the default file-hashing hook cannot be used here. The digest
    covers the Korean body and the loaded spans' offsets and source tags, which is what makes
    it a check on the corpus as scored rather than on a file's mtime.
    """
    recorded = split_record["source"]["documents"]
    assert len(recorded) == split_record["source"]["n_documents"]
    for doc in kosurro_docs:
        assert split.digest_material(kosurro_unsplit_loader.digest_parts(doc)) == recorded[
            doc.doc_id
        ], f"{doc.doc_id}'s bytes differ from the frozen split file"


#: What `split.build()` cannot reproduce: when it ran and what the tree looked like then.
#: Listed rather than skipped by prefix, so a new volatile field has to be named here.
NOT_REPRODUCIBLE = ("generated", "repository")


def test_the_frozen_file_is_what_the_builder_produces_today(split_record):
    """Rebuild the record from the corpus and require every non-volatile field to agree.

    The recount tests above check the file against the corpus; this one checks it against the
    *code*, which is the other half and the half that nothing else in the suite had. The four
    narrative builders in `src/split.py` are reached by no other test: `build()` is called by
    the CLI and by nothing in `tests/`, so every figure only that code produces — the sparsity
    count, the type-map notes, the per-fold token percentiles — was unfalsifiable until the
    file was regenerated by hand and diffed.

    That is not hypothetical. `n_documents_with_spans` was written as 727 beside prose saying
    "at least one in-scope span", and 725 notes carry one: the builder counted `doc.spans`,
    which includes the three §9.1-excluded spans that are nobody's gold. It was found on the
    day of the freeze by the recount above, one commit before the file became the
    pre-registered artefact. This test is what makes the *next* such figure fail immediately,
    and `sparsity_counts_excluded_spans` in `tests/mutations/run.py` is the check that this
    test does its job.

    The build is cheap here — one JSONL file, no per-file hashing — so there is no reason for
    the file to be reproducible only by hand.
    """
    built = split.build("ko-surro")
    for key in NOT_REPRODUCIBLE:
        assert key in split_record and key in built, key
        built[key] = split_record[key]
    assert built == split_record, (
        "the frozen split file is not what src/split.py produces from the corpus today. "
        "If the change to the builder is intended, the file is pre-registered: say so and "
        "regenerate it deliberately (src/split.py refuses to overwrite)."
    )


#: Keys whose values are prose written in this repository — the only strings in this split
#: file that are neither an id nor a digest. Listed exhaustively and separately from
#: `test_endeid_loader.py`'s copy: a corpus adding a field that carries free text has to add
#: it here, and reading it while adding it is the check.
PROSE_KEYS = frozenset(
    {
        "basis",
        "bom_note",
        "branch",
        "commit",
        "corpus",
        "derivation",
        "fold_directories_note",
        "generated",
        "generated_by",
        "hash_algorithm",
        "hashed",
        "identifying_surface_rule",
        "key_source",
        "not_gold_supported_note",
        "note",
        "origin",
        "rationale_ref",
        "reading",
        "records_without_reference_note",
        "reference",
        "reference_note",
        "rule_ref",
        "source_freeze_commit",
        "sparsity_note",
        "surfaces_recorded",
        "tokenizer",
        "tokenizer_note",
        "unit",
    }
)


def test_every_string_in_the_split_file_is_an_id_a_digest_or_prose(split_record):
    """The structural half: a Korean surrogate surface has nowhere in this file to be.

    The file is committed to a public repository, so the check is over the shape of the file
    rather than a search for particular strings — every string value is a document id, a
    patient id, a hex digest, or sits under one of the `PROSE_KEYS` above. A surface written
    anywhere else fails here whether or not any test knows what that surface is.
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

    walk(split_record)
    assert offenders == [], f"free text under unexpected keys: {offenders}"


def test_no_prose_in_the_split_file_carries_a_surrogate_surface(split_record, kosurro_docs):
    """The other half, over the prose that `PROSE_KEYS` permits.

    1,109 of the 1,614 loaded surfaces are searched for. The 505 that are not are one
    character long, or two or three digits: this file's prose legitimately contains numbers —
    section references, counts, a timestamp — and a two-digit DATE surrogate matches nine
    times inside them. Failing on that would be testing arithmetic in English, not the file.
    No surface is named in a message; a hit reports the document id and the offsets.
    """
    prose = "\n".join(_strings_under(split_record, PROSE_KEYS))
    assert len(prose) > 1000, "the prose search found almost nothing to search"
    checked = 0
    for doc in kosurro_docs:
        for span_ in doc.spans:
            surface = span_.surface.strip()
            if len(surface) < 2 or (surface.isdigit() and len(surface) < 4):
                continue
            checked += 1
            assert surface not in prose, (
                f"{doc.doc_id} span at [{span_.start}, {span_.end}) is in the split file"
            )
    assert checked == 1109


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
