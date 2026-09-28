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

**These tests now see 1,941 documents, not 2,425 — the seal ran on 2026-09-28.** Three tests
in this file recounted the whole corpus while the whole corpus was reachable, which was
possible only between the freeze (2026-09-23) and the seal, and they said so when they were
written. They have been moved to the arithmetic form `tests/test_endeid_loader.py` uses, which
is the same shape for the same reason and on the same release: what the loader reaches is
recounted, the sealed fold's figures are read from the frozen split file, and
`test_the_split_file_accounts_for_the_seal` requires *visible recount + sealed block = the
corpus-wide total*, per type as well as in aggregate. That is weaker than the recount it
replaces — the sealed block is pinned rather than re-derived — and it is what is left. The
corpus-wide numbers are `FULL_*` below and are no longer independently checkable from here;
the run that produced them is the one the freeze commit records.

Eight of the nine records with no English reference are reachable; the ninth belongs to a
patient in the test fold and went behind the seal with that patient's other notes.
`test_the_ninth_uncovered_record_went_with_its_patient` states that as arithmetic against the
frozen file rather than by naming the sealed record. The eight are **the same eight
`en-deid` reaches**, which is what DESIGN §6.5 option B's document-level alignment means and
is asserted here rather than left as a coincidence.

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

# ─── expected values, visible and corpus-wide (DESIGN §9.0) ──────────────────
#
# Two blocks, and the split matters. The first is what the loader can reach after the seal of
# 2026-09-28 — train + dev — and was measured with the seal in place, which is the only state
# it describes. The second is the whole corpus, read from the frozen `splits/ko-surro.json`
# rather than recounted, because recounting it would mean reading the test fold.
#
# `en-deid`'s file states the same division for the same corpus; the numbers differ because the
# reference does. `ko-surro` is scored against human-verified silver (DESIGN §6.5 (v)) and
# `en-deid` against the release's human reference, so the two halves of one release carry
# different span sets over the same documents. The document counts agree and are asserted to.

#: What the loader can see: train + dev.
N_DOCS = 1941
N_RECORDS_IN_FILE = 1949  # loaded, plus the eight reachable records with no reference
N_SILVER = 1711  # before the gold-support filter
N_SPANS = 1282  # after it: the spans the loader yields
N_IN_SCOPE = 1279
N_EXCLUDED = 3
N_NOT_GOLD_SUPPORTED = 429
N_DOCS_WITH_SPANS = 580
N_PATIENTS = 129
CANONICAL_COUNTS = {
    "NAME": 639,
    "DATE": 369,
    "ORGANISATION": 200,
    "CONTACT": 37,
    "LOCATION_AREA": 30,
    "AGE": 3,
    "ID": 1,
}
#: `LOCATION_STREET` is absent rather than zero: both of its spans are in the test fold, so
#: the visible corpus has no instance of the type at all. Written as an absence because that
#: is what the recount produces, and a `"LOCATION_STREET": 0` entry here would not compare
#: equal to it.
EXCLUDED_SUBTYPE_COUNTS = {"NOT_PHI_RESTORED": 3}

#: Records with a body and no English reference, reachable after the seal. The ninth is behind
#: it. The same eight `tests/test_endeid_loader.py` lists, and the test below asserts that
#: rather than leaving two copies of one list to drift.
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

#: The whole corpus — DESIGN §9.0's `ko-surro` table and what the paper prints. These are the
#: figures the freeze of 2026-09-23 measured while every fold was reachable; 484 documents are
#: now sealed and a test that recomputed these would be reading the test fold to do it.
FULL_N_DOCS = 2425
FULL_N_RECORDS = 2434
FULL_N_SILVER = 2158
FULL_N_SPANS = 1614
FULL_N_IN_SCOPE = 1611
FULL_N_EXCLUDED = 3
FULL_N_NOT_GOLD_SUPPORTED = 544
FULL_N_UNCOVERED = 9
FULL_N_PATIENTS = 163
FULL_CANONICAL_COUNTS = {
    "NAME": 806,
    "DATE": 468,
    "ORGANISATION": 242,
    "LOCATION_AREA": 45,
    "CONTACT": 44,
    "AGE": 3,
    "LOCATION_STREET": 2,
    "ID": 1,
}


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


def test_the_visible_corpus_reproduces_the_pre_registered_span_set(
    kosurro_docs, kosurro_unsplit_loader
):
    """DESIGN §9.0's `ko-surro` table, recounted over what the seal leaves reachable.

    Until 2026-09-28 this recounted all 2,425 documents and asserted the `FULL_*` figures
    directly, which was possible because the test fold was still in the corpus root. It is not
    any more. What is asserted here is the visible half, measured independently of the split
    file — the loader is the unsplit one, so these counts do not come from the file they are
    later reconciled against. `test_the_split_file_accounts_for_the_seal` is what ties them to
    the corpus-wide table, and without that test these constants and the `FULL_*` ones would be
    two unrelated sets of numbers.

    Still the pre-registration check it was written as: if the derived root is rebuilt and any
    of these moves, the scoring basis moved.
    """
    loader = kosurro_unsplit_loader
    spans = [s for doc in kosurro_docs for s in doc.spans]
    by_type = Counter(s.phi_type for s in spans if not s.excluded)
    by_excluded = Counter(s.subtype for s in spans if s.excluded)

    assert (len(kosurro_docs), len(loader.uncovered)) == (N_DOCS, len(UNCOVERED))
    assert loader.not_gold_supported == N_NOT_GOLD_SUPPORTED
    assert len(spans) == N_SPANS
    assert sum(1 for s in spans if s.excluded) == N_EXCLUDED
    assert sum(by_type.values()) == N_IN_SCOPE
    assert dict(by_type) == CANONICAL_COUNTS
    assert dict(by_excluded) == EXCLUDED_SUBTYPE_COUNTS
    # The filter's two sides plus the spans it kept account for every silver span in this root,
    # so `N_SILVER` is not a fourth independent number that could drift on its own.
    assert len(spans) + loader.not_gold_supported == N_SILVER


def test_the_visible_uncovered_records_are_the_ones_en_deid_reaches(kosurro_unsplit_loader):
    """The eight reachable reference-less records are `en-deid`'s eight, not a second list.

    DESIGN §6.5 option B aligns the two corpora at document level, so which records lack an
    English reference is a property of the release and must be the same on both sides — and
    which of the nine went behind the seal is a property of the shared split. Asserted by
    importing `en-deid`'s list rather than by writing the eight ids out twice: two copies of a
    list that must agree is useful when one is a claim about a *different* artefact
    (`test_derive_aligned_split.py` pins the derivation's numbers that way), but here it would
    just be one list that can drift from itself.
    """
    from test_endeid_loader import UNCOVERED as ENDEID_UNCOVERED

    assert sorted(kosurro_unsplit_loader.uncovered) == sorted(UNCOVERED)
    assert sorted(UNCOVERED) == sorted(ENDEID_UNCOVERED)


def test_the_split_file_accounts_for_the_seal(kosurro_sealed, split_record, kosurro_docs):
    """Visible recount + the file's sealed block = the corpus-wide figures, per type.

    The test this replaces recounted the sealed fold too. Now the sealed block is *pinned by
    arithmetic* instead: a stale figure in it would have to be compensated somewhere else in
    the file to survive, and per type as well as in total, which is a narrow escape route. What
    is given up honestly is that a coordinated error in the file plus a matching error in the
    corpus would pass — the freeze commit is the only record that the numbers were once
    recounted directly.

    Without this the `FULL_*` constants would be unfalsifiable: after the seal, nothing
    reachable could contradict them.
    """
    sealed = split_record["folds"]["test"]
    spans = [s for doc in kosurro_docs for s in doc.spans]
    in_scope = [s for s in spans if not s.excluded]
    excluded = [s for s in spans if s.excluded]

    assert len(kosurro_docs) + sealed["n_documents"] == FULL_N_DOCS
    assert len(spans) + sealed["n_spans"] == FULL_N_SPANS
    assert len(in_scope) + sealed["n_spans_in_scope"] == FULL_N_IN_SCOPE
    assert len(excluded) + sealed["n_spans_excluded"] == FULL_N_EXCLUDED
    assert split_record["totals"]["n_spans"] == FULL_N_SPANS
    assert split_record["totals"]["n_spans_in_scope"] == FULL_N_IN_SCOPE
    assert split_record["totals"]["spans_by_phi_type"] == FULL_CANONICAL_COUNTS

    visible = Counter(s.phi_type for s in in_scope)
    for phi_type, total in FULL_CANONICAL_COUNTS.items():
        assert visible.get(phi_type, 0) + sealed["spans_by_phi_type"].get(phi_type, 0) == total, (
            phi_type
        )
    # No type appears behind the seal that the corpus-wide table does not have. Without this the
    # loop above would pass while the sealed block carried an extra type.
    assert set(sealed["spans_by_phi_type"]) <= set(FULL_CANONICAL_COUNTS)


def test_the_ninth_uncovered_record_went_with_its_patient(
    kosurro_sealed, split_record, kosurro_unsplit_loader
):
    """One reference-less record is behind the seal, and it is not named here.

    Arithmetic against the frozen file, the same statement `tests/test_endeid_loader.py` makes
    about the same record on the other half of the release: the release has 2,434 records and
    the reference covers 2,425, the split file holds 2,425 documents, and the corpus root now
    holds 1,949 records for 1,941 documents. Eight of the nine are reachable and one left with
    the test fold — which is the intended behaviour, because leaving a sealed patient's note
    where rule development reads it is the other half of the seal.
    """
    listed = split_record["corpus_specific"]["records_without_reference"]
    assert len(listed) == FULL_N_UNCOVERED
    assert FULL_N_RECORDS - FULL_N_DOCS == FULL_N_UNCOVERED
    assert len(kosurro_unsplit_loader.uncovered) == len(UNCOVERED)
    assert FULL_N_UNCOVERED - len(UNCOVERED) == 1
    # The sealed one is in the file's list and not reachable, and this says so without asking
    # which id it is: the reachable eight are a strict subset.
    assert set(UNCOVERED) < set(listed)


def test_an_ordinary_load_returns_only_the_unsealed_folds(kosurro_sealed, kosurro_loader):
    """train and dev, and no `test` — the seal as the loader sees it."""
    assert {doc.split for doc in kosurro_loader.load()} == {"train", "dev"}


def test_the_sealed_root_is_not_reachable_from_an_ordinary_call(kosurro_sealed, kosurro_loader):
    assert kosurro_loader.sealed_reachable() is None


def test_every_loaded_span_agrees_with_the_slice(kosurro_docs):
    """`assert_offsets()` over the whole corpus, with the surfaces read independently.

    The surface is the surrogate string `prepare_kosurro.py` read from the Korean derivation
    and the slice comes from the body, so this compares two readings rather than a value with
    itself — the property GraSCCo's loader cannot have and this one can.
    """
    for doc in kosurro_docs:
        doc.assert_offsets()


def test_the_patient_key_is_read_from_meta_and_never_split_out_of_the_id(
    kosurro_docs, kosurro_unsplit_loader, split_record
):
    """129 patients over 1,941 reachable notes, and the key comes from the record's own field.

    The composition `{patient}_{note}` is the release's and is made in one direction only, so
    a loader that recovered the patient by splitting `doc_id` would be a second answer to
    which half is the patient — on a corpus whose folds are patient-disjoint.

    Was 163 over 2,425 until the seal of 2026-09-28. The corpus-wide figure is now read from
    the frozen file rather than recounted, and the two are tied together the only way they can
    be after the seal: the folds are patient-disjoint, so the visible patients and the sealed
    fold's patients partition the 163 and cannot overlap. That disjointness is what
    `test_no_group_crosses_the_split` asserts from the file, so the subtraction below is not a
    second assumption.
    """
    keys = {kosurro_unsplit_loader.patient_key(doc) for doc in kosurro_docs}
    assert len(keys) == N_PATIENTS
    audit = split_record["group_key"]["grouping_audit"]
    assert audit["n_patients"] == FULL_N_PATIENTS
    assert FULL_N_PATIENTS - N_PATIENTS == 34  # the test fold's patients, none of them shared
    assert keys <= set(audit["patients"])
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


def test_the_totals_are_the_visible_recount_plus_the_sealed_block(
    kosurro_sealed, split_record, kosurro_docs
):
    """Every summary in the file, re-derived as far as the seal allows.

    Until 2026-09-28 this recounted all 2,425 documents and compared the totals block
    outright — the strongest form, and available only between the freeze and the seal. The
    totals are now reached by adding the sealed fold's own block to the visible recount, which
    is `test_endeid_loader.py`'s form. The weakening is exactly this: a wrong figure in the
    totals can no longer be caught on its own, only a wrong figure that the sealed block does
    not happen to absorb.

    Two things are recounted here that `test_the_split_file_accounts_for_the_seal` does not
    cover, which is why both exist: the excluded-span accounting by source tag, and
    `n_documents_with_spans`. Neither appears in a fold block, so neither can be reconciled
    per fold — the sealed fold's contribution to each is taken from the totals by subtraction
    and asserted to be the value the frozen file implies.
    """
    in_scope = [s for doc in kosurro_docs for s in doc.spans if not s.excluded]
    excluded = [s for doc in kosurro_docs for s in doc.spans if s.excluded]
    sealed = split_record["folds"]["test"]
    totals = split_record["totals"]

    assert len(kosurro_docs) + sealed["n_documents"] == totals["n_documents"]
    assert len(in_scope) + len(excluded) + sealed["n_spans"] == totals["n_spans"]
    assert len(in_scope) + sealed["n_spans_in_scope"] == totals["n_spans_in_scope"]
    # An excluded span has no canonical type — `phi_type` is None and the source tag it was
    # excluded for is its `subtype`, which is what §9.1's volume is reported by. All three of
    # the corpus's excluded spans are reachable (the test fold has none), so this one summary
    # is still recounted outright rather than by arithmetic, and the split file's own
    # `n_spans_excluded: 0` for the sealed fold is what says so.
    assert sealed["n_spans_excluded"] == 0
    assert dict(Counter(s.subtype for s in excluded)) == totals["spans_by_excluded_type"]

    # `n_documents_with_spans` is corpus-wide and has no per-fold counterpart, so the sealed
    # fold's share is a subtraction. It is bounded rather than asserted equal to a constant: a
    # document behind the seal carries gold or does not, and 484 documents cannot contribute
    # more than 484 or fewer than 0. That is weak, and it is the honest limit of what is
    # checkable — the number was recounted directly once, in the freeze commit.
    with_spans = sum(1 for doc in kosurro_docs if any(not s.excluded for s in doc.spans))
    assert with_spans == N_DOCS_WITH_SPANS
    sealed_with_spans = split_record["corpus_specific"]["n_documents_with_spans"] - with_spans
    assert 0 <= sealed_with_spans <= sealed["n_documents"]
    # And it cannot exceed the number of spans the sealed fold holds: one document needs one.
    assert sealed_with_spans <= sealed["n_spans_in_scope"]


def test_each_visible_folds_summaries_are_a_recount_of_that_fold(
    kosurro_sealed, split_record, kosurro_docs
):
    """And per fold, which the totals cannot check: one fold's spans could sit in another.

    Before the seal this looped over all three folds and recounted the sealed one too. It now
    covers dev and train, and the sealed fold is **skipped by construction** rather than by a
    name check — the documents simply are not in `kosurro_docs`, and the assertion below that
    every unrecounted fold is a sealed one is what keeps that from silently becoming "skipped
    because the ids did not match".
    """
    by_id = {doc.doc_id: doc for doc in kosurro_docs}
    recounted = []
    for fold, block in split_record["folds"].items():
        if not any(doc_id in by_id for doc_id in block["document_ids"]):
            continue
        docs = [by_id[doc_id] for doc_id in block["document_ids"]]
        in_scope = [s for doc in docs for s in doc.spans if not s.excluded]
        excluded = [s for doc in docs for s in doc.spans if s.excluded]
        assert len(docs) == block["n_documents"], fold
        assert len(in_scope) == block["n_spans_in_scope"], fold
        assert len(excluded) == block["n_spans_excluded"], fold
        assert len(in_scope) + len(excluded) == block["n_spans"], fold
        assert dict(Counter(s.phi_type for s in in_scope)) == block["spans_by_phi_type"], fold
        recounted.append(fold)

    # Not `== ["dev", "train"]`: what makes a fold unrecountable is that it is sealed, and
    # `sealed_splits` is where that is declared. A fold that stopped being recountable for any
    # other reason — a renamed id, a dropped record — fails here instead of being skipped.
    assert sorted(recounted) == sorted(
        set(split_record["folds"]) - set(KosurroLoader.sealed_splits)
    )
    assert set(KosurroLoader.sealed_splits) == {"test"}


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


#: What `split.build()` could not reproduce: when it ran and what the tree looked like then.
#: **Dead since the seal of 2026-09-28** — its one reader rebuilt the record and compared every
#: other field, and `build()` now refuses on this corpus (see the test below). Kept rather than
#: deleted for one reason: the next derived corpus's split file needs the same list before its
#: own seal, and a list that was deleted the day it stopped being read is a list the next author
#: writes again from scratch. If it is still unread when that corpus arrives, delete it there.
NOT_REPRODUCIBLE = ("generated", "repository")


def test_the_builder_refuses_to_rebuild_a_sealed_corpus(kosurro_sealed):
    """What became of `test_the_frozen_file_is_what_the_builder_produces_today`.

    That test rebuilt the whole record from the corpus and required every non-volatile field to
    agree — the file checked against the *code* rather than against the corpus, which is the
    half nothing else in the suite had. **It cannot exist after the seal, and not because of a
    fixture: `src/split.py` refuses.** The derived route's membership would survive the seal,
    since it comes from `splits/en-deid.json` rather than from a sample, but the *contents*
    would not — the loader reads only the unsealed root, so the rebuilt file would carry a
    `test` block whose span and token counts were measured from an empty set while every other
    block looked complete. DESIGN §6.2's order (generate → freeze → seal) is what the refusal
    enforces, and the refusal is the thing worth asserting now.

    **What is lost, recorded rather than worked around.** The builder's own figures — the
    per-fold token percentiles, the type-map notes, the narrative prose — are no longer reached
    by any test on this corpus, and `build()` is called by the CLI and by nothing else in
    `tests/`. The one figure that had a mutation anchored on it keeps its catcher through
    `test_the_sparsity_count_is_in_scope_spans_and_not_every_span` below, which tests the
    function directly instead of through a whole-file rebuild. The rest is genuinely
    uncovered here, and the next corpus's split file is where that coverage has to live —
    before *its* seal, which is the window this corpus has now spent.
    """
    with pytest.raises(CorpusError) as exc:
        split.build("ko-surro")
    message = str(exc.value)
    assert "already declares a sealed root" in message
    assert "DESIGN §6.2" in message or "§6.2" in message
    # The refusal names no record, no surface and no path — it is about a config key and a
    # fold. Checked because this message is the one a rebuild attempt puts on a terminal.
    assert SURFACE not in message and LITERAL not in message


def test_the_sparsity_count_is_in_scope_spans_and_not_every_span(tmp_path):
    """`_n_documents_with_gold` directly, which is what keeps one mutation killable.

    The rebuild test above was `sparsity_counts_excluded_spans`'s only catcher, and the seal
    removed it. Without a replacement that mutation would survive — the gate would report a
    surviving mutation on the very figure the freeze commit fixed, which is the outcome
    CLAUDE.md's "돌리지 않은 것은 면제가 아니다" is about. So the guarantee is tested where it
    lives instead of through a 2,434-record rebuild, and on a synthetic root, which means it no
    longer depends on the corpus being present at all.

    The defect being pinned is concrete: `n_documents_with_spans` was written into the frozen
    file as 727 beside prose saying "at least one in-scope span", and 725 notes carry one. The
    builder had counted `doc.spans`, which includes the three §9.1-excluded spans that are
    nobody's gold. Two documents below reproduce that difference in miniature — one whose only
    span is excluded, one whose span is scored.
    """
    from src.split import _n_documents_with_gold

    root = write_root(
        tmp_path / "root",
        [
            record(uid="1_1", patient="1", spans=[span()]),
            record(uid="2_1", patient="2", spans=[span(type_="NOT_PHI_RESTORED")]),
            record(uid="3_1", patient="3", spans=[]),
        ],
    )
    docs = loader_on(root).load()
    assert len(docs) == 3
    # The middle document has a span and no gold. That is the whole distinction: counting
    # `doc.spans` gives 2 here and is the reading that produced the 727.
    assert sum(1 for doc in docs if doc.spans) == 2
    assert _n_documents_with_gold(docs) == 1


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
    # 869, not the 1,109 this asserted before the seal: the surfaces of the test fold's spans
    # are no longer reachable to search for. The figure is here so that a loader change that
    # quietly stopped yielding surfaces turns this test from a check into a tautology and is
    # caught — which is why it is a count and not a `> 0`.
    assert checked == 869


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
