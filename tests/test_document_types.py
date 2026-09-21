"""The document-type axis: the vocabulary, the cues that derive it, and the breakdown.

DESIGN §7 measured GraSCCo's document-type distribution as eight content-cue labels and
pre-registered the breakdown as a **secondary** analysis. Three things are checked here, in
the order a number travels:

  1. `config/naming.yaml` declares the eight labels and nothing else names one.
  2. `config/document_types.yaml` derives them from a document's text, per corpus, at a
     recorded version — and refuses every way the declaration can be wrong.
  3. `scorer.score()` breaks a mode's numbers down by label, without a second matching and
     without the rows pretending to sum to the fold.

**The pinned distribution is over 51 documents and not 63.** de-grascco's test fold was
sealed on 2026-09-21, so what is loadable here is train + dev. DESIGN §7's figures are the
pre-seal measurement with patterns that were not kept, and `config/document_types.yaml`
records why the difference between the two cannot be decomposed. One direction *is*
provable and is asserted below: `radiology` is 34 on 51 documents against §7's 30 on 63,
and sealing can only lower a count, so these patterns are wider than that measurement's.

    python3 -m pytest tests/test_document_types.py -q
"""
from __future__ import annotations

import pytest
import yaml

from src.corpora import doctype
from src.corpora.base import CorpusError, axis, document_types
from src.eval import scorer
from src.eval.scorer import DocPair, Mark, ScorerError

#: The measured distribution of the 51 unsealed documents at cue version 1. Written out so
#: a cue edit that shifts a label has to be acknowledged here — the point of the version
#: field is that a label's *name* does not change when its meaning does.
VISIBLE = {
    "radiology": 34,
    "pathology": 18,
    "outpatient": 15,
    "laboratory": 19,
    "progress_note": 11,
    "discharge": 1,
    "tumour_board": 2,
    "operation_report": 0,
    "unlabelled": 8,
}

#: DESIGN §7's pre-seal figures over all 63 documents, for the one comparison that is
#: sound. Not an expectation: the patterns that produced them were not kept.
DESIGN_63 = {
    "radiology": 30, "pathology": 25, "outpatient": 24, "laboratory": 22,
    "progress_note": 16, "discharge": 4, "tumour_board": 3, "operation_report": 1,
}

#: Documents in the unsealed folds, and the split sizes the loader tests pin.
N_VISIBLE = 51


# ─── the vocabulary ──────────────────────────────────────────────────────────


def test_the_vocabulary_is_the_eight_measured_labels():
    """Eight, because §7 measured eight. A ninth would be a label nothing measured."""
    assert set(document_types()) == set(DESIGN_63)


def test_every_label_carries_a_gloss():
    """A bare name leaves a reader guessing whether `progress_note` is content or register."""
    for label, gloss in document_types().items():
        assert gloss.strip(), label


def test_the_labels_are_not_an_axis():
    """In `axes` they would be fillable into a `paths` template — a directory no arm ran."""
    naming_axes = set(yaml.safe_load(
        (doctype.ROOT / "config" / "naming.yaml").read_text(encoding="utf-8"))["axes"])
    assert "document_type" not in naming_axes


def test_no_label_collides_with_a_corpus_id():
    """The two are read side by side in a metrics file (`base.document_types()`)."""
    assert not set(document_types()) & set(axis("corpus"))


def test_unlabelled_is_not_a_label():
    """"No cue matched" is a statement about the cues, not a ninth kind of document."""
    assert "unlabelled" not in document_types()


# ─── the cues ────────────────────────────────────────────────────────────────


def test_de_grascco_declares_cues_for_every_label_in_order():
    """Order is the vocabulary's, so every table built from it comes out the same way."""
    entry = doctype.cues("de-grascco")
    assert [label for label, _ in entry] == list(document_types())
    assert all(patterns for _, patterns in entry)


def test_a_corpus_with_no_measured_distribution_has_no_axis():
    """es-meddocan's document types were never measured, and that is not an empty result."""
    assert doctype.has_axis("es-meddocan") is False
    assert doctype.cues("es-meddocan") is None
    with pytest.raises(doctype.DocTypeError, match="no document-type axis"):
        doctype.labels("es-meddocan", "irgendein Text")


def test_the_version_is_an_integer_and_is_what_a_run_block_records():
    assert isinstance(doctype.version(), int) and doctype.version() >= 1
    assert doctype.SOURCE == "config/document_types.yaml"


def test_labels_are_multi_label_and_in_the_vocabulary_order():
    """One synthetic document with two labels' cues, and the order is not the cues' order."""
    text = "Sprechstunde der Ambulanz. Im MRT mit Kontrastmittel kein Befund."
    assert doctype.labels("de-grascco", text) == ("radiology", "outpatient")


def test_a_document_no_cue_reaches_gets_no_label():
    """An ordinary state, not an error: the cues are not a partition (DESIGN §7)."""
    assert doctype.labels("de-grascco", "Guten Tag. Alles unauffällig.") == ()


def test_the_cues_are_case_sensitive_where_the_pattern_says_so():
    """`\\bCT\\b` case-folded matches word fragments; the all-caps header form is its own
    pattern. Both halves are asserted, because a stray `re.IGNORECASE` would pass the first."""
    assert doctype.labels("de-grascco", "CT des Abdomens") == ("radiology",)
    assert doctype.labels("de-grascco", "ct scan") == ()
    assert doctype.labels("de-grascco", "AMBULANZKARTE") == ("outpatient",)


# ─── the cue file's refusals ─────────────────────────────────────────────────


def _cues(tmp_path, monkeypatch, payload: dict):
    """Point `doctype` at a synthetic cue file and clear both caches.

    Both, and in this order, because `_config` is cached on nothing and `cues` on the corpus
    id: clearing one would leave a test reading the other's answer from the previous test.
    """
    path = tmp_path / "document_types.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(doctype, "CUES", path)
    doctype._config.cache_clear()
    doctype.cues.cache_clear()
    yield
    monkeypatch.undo()
    doctype._config.cache_clear()
    doctype.cues.cache_clear()


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    """A factory for the above, with the caches restored afterwards either way."""
    undo = []

    def make(payload: dict):
        gen = _cues(tmp_path, monkeypatch, payload)
        next(gen)
        undo.append(gen)

    yield make
    for gen in undo:
        next(gen, None)


def test_a_label_the_vocabulary_does_not_declare_is_refused(synthetic):
    synthetic({"version": 1, "corpora": {"de-grascco": {"ward_round": ["Visite"]}}})
    with pytest.raises(doctype.DocTypeError, match="ward_round"):
        doctype.cues("de-grascco")


def test_a_missing_label_is_refused_rather_than_silently_absent(synthetic):
    """The failure this refusal prevents is a table where "not asked" looks like "none"."""
    partial = {label: ["x"] for label in list(document_types())[:-1]}
    synthetic({"version": 1, "corpora": {"de-grascco": partial}})
    with pytest.raises(doctype.DocTypeError, match="declares no cues for"):
        doctype.cues("de-grascco")


def test_an_empty_cue_list_is_refused(synthetic):
    empty = {label: ["x"] for label in document_types()}
    empty["discharge"] = []
    synthetic({"version": 1, "corpora": {"de-grascco": empty}})
    with pytest.raises(doctype.DocTypeError, match="no list of cue patterns"):
        doctype.cues("de-grascco")


def test_a_pattern_that_is_not_a_regex_is_refused(synthetic):
    broken = {label: ["x"] for label in document_types()}
    broken["radiology"] = ["MRT("]
    synthetic({"version": 1, "corpora": {"de-grascco": broken}})
    with pytest.raises(doctype.DocTypeError, match="not a regular"):
        doctype.cues("de-grascco")


def test_cues_for_a_corpus_naming_yaml_does_not_declare_are_refused(synthetic):
    synthetic({"version": 1, "corpora": {"de-graszo": {"radiology": ["MRT"]}}})
    with pytest.raises(doctype.DocTypeError, match="de-graszo"):
        doctype.version()


def test_a_file_without_a_version_is_refused(synthetic):
    synthetic({"corpora": {"de-grascco": {"radiology": ["MRT"]}}})
    with pytest.raises(doctype.DocTypeError, match="integer `version`"):
        doctype.version()


def test_an_empty_corpus_entry_is_refused(synthetic):
    """`{}` would be scored as eight labels that match nothing; absence is the way to say
    a corpus has no axis."""
    synthetic({"version": 1, "corpora": {"de-grascco": {}}})
    with pytest.raises(doctype.DocTypeError, match="is empty"):
        doctype.cues("de-grascco")


# ─── the measurement ─────────────────────────────────────────────────────────


def test_the_visible_distribution_is_pinned(grascco_present, grascco_loader):
    """51 documents at cue version 1. A cue edit that moves a label lands here first."""
    docs = grascco_loader.load()
    assert len(docs) == N_VISIBLE
    labelled = doctype.label_documents("de-grascco", docs)
    assert doctype.distribution(labelled) == VISIBLE


def test_the_patterns_are_wider_than_the_pre_seal_measurement(grascco_present,
                                                              grascco_loader):
    """The one sound comparison with DESIGN §7's 63-document figures.

    Sealing removes documents, so a count taken over 51 cannot exceed the same patterns'
    count over 63. `radiology` does exceed it, which proves these patterns differ from the
    ones that measurement used — they were not kept, and `config/document_types.yaml` says
    so. For the other seven labels no direction is derivable and none is asserted.
    """
    labelled = doctype.label_documents("de-grascco", grascco_loader.load())
    counts = doctype.distribution(labelled)
    assert counts["radiology"] > DESIGN_63["radiology"]


def test_every_document_gets_an_entry_even_with_no_label(grascco_present, grascco_loader):
    """`label_documents` is keyed by every document, so the scorer's own check can rely on
    an absent entry meaning "never asked" (see `scorer._document_type_block`)."""
    docs = grascco_loader.load()
    labelled = doctype.label_documents("de-grascco", docs)
    assert set(labelled) == {d.doc_id for d in docs}
    assert sum(1 for these in labelled.values() if not these) == VISIBLE["unlabelled"]


def test_a_corpus_without_the_axis_labels_no_documents(loader):
    """`None`, not `{}`: an empty mapping is what a fold of zero documents would give."""
    assert doctype.label_documents("es-meddocan", loader.load()[:3]) is None


# ─── the breakdown in the metrics block ──────────────────────────────────────
#
# Constructed pairs, for `tests/test_scorer.py`'s reason: the geometry is the question and
# real documents would exercise it densely in one place and not at all in the others. The
# labels here are assigned by hand so the overlap is visible in the fixture.

RADIO = DocPair(doc_id="d-radio",
                gold=(Mark(0, 4, "NAME", span_index=0),),
                pred=(Mark(0, 4, "NAME", "context_cue", "de:cue_person", span_index=0),))
BOTH = DocPair(doc_id="d-both",
               gold=(Mark(0, 4, "NAME", span_index=0), Mark(10, 20, "DATE", span_index=1)),
               pred=(Mark(0, 4, "NAME", "context_cue", "de:cue_person", span_index=0),))
NONE = DocPair(doc_id="d-none",
               gold=(Mark(0, 4, "AGE", span_index=0),),
               pred=(Mark(50, 60, "NAME", "gazetteer", "de:list_surname", span_index=0),))

PAIRS = (RADIO, BOTH, NONE)
LABELS = {"d-radio": ("radiology",), "d-both": ("radiology", "outpatient"),
          "d-none": ()}


def test_no_mapping_writes_no_block():
    """Absence is the record that the corpus has no measured distribution (schema 10)."""
    scored = scorer.score(PAIRS)
    for mode in scorer.MODES:
        assert "by_document_type" not in scored["modes"][mode]


def test_the_rows_do_not_sum_to_the_fold_and_say_so():
    """Two documents are radiology, one of them is also outpatient, one is neither."""
    scored = scorer.score(PAIRS, document_types=LABELS)
    block = scored["modes"][scorer.RELAXED]["by_document_type"]
    assert block["multi_label"] is True
    assert block["documents"] == 3
    assert block["documents_labelled"] == 2
    assert block["documents_unlabelled"] == 1
    assert block["labels"]["radiology"]["documents"] == 2
    assert block["labels"]["outpatient"]["documents"] == 1
    assert block["labels"]["laboratory"]["documents"] == 0
    # The fold holds 4 gold spans (1 + 2 + 1) and that is the mode's leak denominator.
    # The rows hold 6: `d-both`'s two spans are counted under radiology *and* under
    # outpatient. Exactly the over-count a multi-label breakdown has, asserted rather than
    # avoided — a reader who adds the rows up must find them not adding up to the fold.
    rows = sum(entry["gold"] for entry in block["labels"].values())
    assert rows == 5 and block["unlabelled"]["gold"] == 1
    assert scored["modes"][scorer.RELAXED]["leak"]["denominator"] == 4


def test_every_declared_label_gets_a_row_including_the_absent_ones():
    """A label with no documents is a measurement; a missing row is an unasked question."""
    block = scorer.score(PAIRS, document_types=LABELS)["modes"][scorer.RELAXED]
    assert list(block["by_document_type"]["labels"]) == list(document_types())


def test_the_unlabelled_row_is_its_own_key():
    """`d-none` has one gold span, unmatched, and one false positive."""
    block = scorer.score(PAIRS, document_types=LABELS)["modes"][scorer.RELAXED]
    row = block["by_document_type"]["unlabelled"]
    assert row["documents"] == 1
    assert row["gold"] == 1 and row["leaked"] == 1 and row["leak_rate"] == 1.0
    assert row["fp"] == 1 and row["tp"] == 0


def test_the_breakdown_agrees_with_the_mode_it_sits_in():
    """The radiology subset's numbers are the same matching's verdicts, not a re-scoring.

    `d-radio` and `d-both` hold three gold spans between them and two are found, so the
    subset's tp is 2 — and both modes must say so, because both modes find the same two
    exact-boundary predictions.
    """
    scored = scorer.score(PAIRS, document_types=LABELS)
    for mode in scorer.MODES:
        row = scored["modes"][mode]["by_document_type"]["labels"]["radiology"]
        assert (row["gold"], row["tp"], row["fp"]) == (3, 2, 0)
        assert row["leaked"] == 1


def test_a_document_absent_from_the_mapping_is_refused():
    """An absent entry and an empty tuple are different facts, and only one is a label.

    `d-none` is dropped, not set to `()`: the dropped form is what a mapping derived from a
    different fold looks like, and it would silently score two documents out of three.
    """
    partial = {doc_id: these for doc_id, these in LABELS.items() if doc_id != "d-none"}
    with pytest.raises(ScorerError, match="no entry in the document-type mapping"):
        scorer.score(PAIRS, document_types=partial)


def test_an_undeclared_label_in_the_mapping_is_refused():
    with pytest.raises(ScorerError, match="ward_round"):
        scorer.score(PAIRS, document_types={**LABELS, "d-none": ("ward_round",)})


def test_the_vocabulary_accessor_refuses_a_missing_block(monkeypatch):
    """The config-side failure, so `document_types()` is not trusted to be present."""
    from src.corpora import base

    monkeypatch.setattr(base, "naming", lambda: {"axes": {"corpus": {}}})
    with pytest.raises(CorpusError, match="document_type"):
        base.document_types()
