"""The guideline's A and C. DESIGN §12.5.

The Jaccard empty-set test the v24 instruction requires is the centre of this file. It is
the one definitional choice in C, it is the case an inline expression raises on, and it is
the case the five corpora supply in quantity — a dev fold where most documents carry no PHI
of a given type is the normal state, not an edge.

    python3 -m pytest tests/test_guideline.py -q
"""
from __future__ import annotations

import pytest

from src.corpora.base import CorpusError, Span
from src.eval import guideline as g
from src.eval import scorer


# ─── A ──────────────────────────────────────────────────────────────────────

def test_accuracy_is_tp_over_tp_plus_fp_plus_fn():
    assert g.accuracy(8, 1, 1) == pytest.approx(0.8)
    assert g.accuracy(10, 0, 0) == 1.0
    assert g.accuracy(0, 5, 5) == 0.0


def test_accuracy_counts_no_true_negatives_so_it_is_not_symmetric_with_f1():
    """A is strictly below F1 whenever there is any error at all.

    Worth pinning because the two are easy to confuse in a table: F1 is the harmonic mean
    of two ratios and A is one ratio over the union of all three error terms, so A ≤ F1
    always, with equality only when FP = FN = 0.
    """
    tp, fp, fn = 90, 10, 10
    a = g.accuracy(tp, fp, fn)
    f1 = scorer._f1(tp / (tp + fp), tp / (tp + fn))
    assert a < f1
    assert g.accuracy(100, 0, 0) == pytest.approx(
        scorer._f1(1.0, 1.0))


def test_a_zero_denominator_gives_zero_and_the_reason_is_recorded():
    """Not `None`: a blank would read as a failure in the RFP's O/X column."""
    assert g.accuracy(0, 0, 0) == 0.0


def test_a_is_read_off_the_existing_relaxed_matching_and_not_a_new_one():
    """The guideline's matching *is* `scorer.RELAXED`, so A shares its TP/FP/FN.

    A second matching would let A and F1 answer to different assignments in one table.
    """
    gold = [scorer.Mark(start=0, end=10, phi_type="NAME"),
            scorer.Mark(start=20, end=30, phi_type="DATE")]
    pred = [scorer.Mark(start=2, end=12, phi_type="NAME", layer="tagger"),
            scorer.Mark(start=50, end=60, phi_type="DATE", layer="tagger")]
    pairs = [scorer.DocPair(doc_id="d", gold=tuple(gold), pred=tuple(pred))]
    scored = scorer.score(pairs)
    block = scored["modes"][scorer.RELAXED]
    out = g.from_mode_block(block)
    assert (out["tp"], out["fp"], out["fn"]) == (1, 1, 1)
    assert out["A"] == pytest.approx(1 / 3)
    assert out["f1"] == pytest.approx(block["overall"]["f1"])


# ─── Jaccard, and the empty-set rule ────────────────────────────────────────

def test_two_empty_sets_score_one():
    """DESIGN §12.5. A document two runs agree has no PHI is perfect agreement.

    The alternatives are worse in opposite directions: 0 punishes the system for the
    thing it got right, and dropping the document makes C a metric over the PHI-bearing
    documents only — on folds where the clean documents are a large and varying share.
    """
    assert g.jaccard(set(), set()) == 1.0


def test_an_inline_jaccard_would_have_raised_on_that_case():
    """Why it is a named function. `len(a & b) / len(a | b)` is ZeroDivisionError here."""
    left, right = set(), set()
    with pytest.raises(ZeroDivisionError):
        len(left & right) / len(left | right)
    assert g.jaccard(left, right) == 1.0


def test_one_empty_and_one_not_scores_zero():
    """The asymmetric case is **not** 1. Only *both* empty is agreement."""
    assert g.jaccard(set(), {(0, 5, "NAME")}) == 0.0
    assert g.jaccard({(0, 5, "NAME")}, set()) == 0.0


def test_jaccard_is_the_ordinary_ratio_when_both_are_populated():
    a = {(0, 5, "NAME"), (10, 15, "DATE")}
    b = {(0, 5, "NAME"), (20, 25, "AGE")}
    assert g.jaccard(a, b) == pytest.approx(1 / 3)
    assert g.jaccard(a, a) == 1.0


def test_the_span_key_ignores_layer_and_score():
    """Two runs that found the same typed extent agree, whatever found it.

    A consistency metric that counted `score` would report a tagger as inconsistent for
    returning 0.81 where it returned 0.80.
    """
    one = Span(start=0, end=4, surface="xxxx", subtype="S", phi_type="NAME",
               layer="tagger", detector="T", score=0.80)
    two = Span(start=0, end=4, surface="xxxx", subtype="S", phi_type="NAME",
               layer="regex_checksum", detector="R", rule_id="es:a", score=None)
    assert g.spans_to_keys([one]) == g.spans_to_keys([two]) == [(0, 4, "NAME")]
    assert g.jaccard(set(g.spans_to_keys([one])),
                     set(g.spans_to_keys([two]))) == 1.0


def test_a_span_with_no_type_cannot_be_a_consistency_key():
    excluded = Span(start=0, end=4, surface="xxxx", subtype="SEXO",
                    phi_type=None, excluded=True)
    with pytest.raises(CorpusError, match="no phi_type"):
        g.spans_to_keys([excluded])


# ─── C ──────────────────────────────────────────────────────────────────────

def test_c_is_one_when_every_run_agrees_everywhere():
    run = {"d1": [(0, 5, "NAME")], "d2": []}
    out = g.consistency([run, dict(run), dict(run)])
    assert out["C"] == 1.0
    assert out["runs"] == 3 and out["documents"] == 2
    assert out["documents_empty_in_every_run"] == 1


def test_c_averages_per_document_then_over_documents_and_not_over_spans():
    """The order matters: a long document cannot dominate a short one.

    Here `d1` has four spans and perfect agreement, `d2` has one span and none. Averaging
    per document gives 0.5; pooling the spans would give 0.8.
    """
    a = {"d1": [(0, 1, "NAME"), (2, 3, "NAME"), (4, 5, "NAME"), (6, 7, "NAME")],
         "d2": [(0, 1, "DATE")]}
    b = {"d1": list(a["d1"]), "d2": [(9, 9 + 1, "DATE")]}
    out = g.consistency([a, b])
    assert out["C"] == pytest.approx(0.5)
    assert out["per_document_min"] == 0.0
    assert out["identical_in_every_run"] == 1


def test_c_averages_all_run_pairs_and_not_just_consecutive_ones():
    """Three runs give three pairs. Comparing only (1,2) and (2,3) would miss (1,3)."""
    one = {"d": [(0, 1, "NAME")]}
    two = {"d": [(0, 1, "NAME")]}
    three = {"d": []}
    out = g.consistency([one, two, three])
    # pairs: (1,2) = 1.0, (1,3) = 0.0, (2,3) = 0.0  ->  1/3
    assert out["C"] == pytest.approx(1 / 3)


def test_a_single_run_is_refused_rather_than_scoring_one():
    """1.0 for one run would report a single run as perfectly reproducible — which is
    the claim C exists to test rather than assume."""
    with pytest.raises(CorpusError, match="at least 2 runs"):
        g.consistency([{"d": []}])


def test_runs_covering_different_documents_are_refused():
    """An absent document is a run that did not happen, not a run that predicted nothing.

    Treating it as empty would score two crashed runs as perfectly consistent.
    """
    with pytest.raises(CorpusError, match="Refused rather than treating"):
        g.consistency([{"d1": [], "d2": []}, {"d1": []}])


def test_the_empty_everywhere_count_travels_with_c():
    """A C of 0.97 on a fold that is 60% empty is a different claim from the same number
    on a fold where nothing is empty, and the rule that makes the first possible is the
    both-empty rule. So the count that makes it readable is reported beside it."""
    runs = [{f"d{i}": [] for i in range(9)} | {"d9": [(0, 1, "NAME")]}
            for _ in range(2)]
    out = g.consistency(runs)
    assert out["C"] == 1.0
    assert out["documents_empty_in_every_run"] == 9
    assert out["documents"] == 10


# ─── the layer vocabulary and the RFP column ────────────────────────────────

def test_the_three_consistency_layers_come_from_naming():
    layers = g.consistency_layers()
    assert set(layers) == {"run_repeat", "author_repeat", "train_repeat"}
    for name in layers:
        assert g.check_consistency_layer(name) == name


def test_an_undeclared_consistency_layer_is_refused():
    with pytest.raises(CorpusError, match="not a consistency layer"):
        g.check_consistency_layer("institution_repeat")


def test_the_rfp_column_marks_a_missing_quantity_as_dash_and_not_x():
    """A quantity that was not computed is not a quantity that failed, and the column is
    read by someone counting failures."""
    row = g.RfpRow(arm="T", corpus="ko-surro", accuracy=0.95,
                   consistency=None, recall=0.80)
    marks = row.as_record()["O_X"]
    assert marks == {"accuracy": "O", "consistency": "-", "recall": "X"}


def test_the_rfp_row_records_that_it_is_not_a_project_claim():
    """DESIGN §12.5: the threshold is someone else's, over a type set that is not ours."""
    row = g.RfpRow(arm="RT", corpus="es-meddocan", accuracy=0.9,
                   consistency=0.9, recall=0.9)
    record = row.as_record()
    assert record["is_a_project_claim"] is False
    assert record["O_X"] == {"accuracy": "O", "consistency": "O", "recall": "O"}
    assert record["threshold"] == 0.90
