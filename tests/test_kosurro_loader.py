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
from pathlib import Path

import pytest

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
    from collections import Counter

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
