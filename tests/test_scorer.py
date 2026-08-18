"""Scorer tests against hand-designed boundary cases (DESIGN §9.3).

Every expected number in this file was computed by hand from the fixture geometry
*before* the scorer was run, and is written out as a literal. None of it was copied
from the scorer's output. That order matters: a test whose expectation came from the
code under test proves the code is deterministic and nothing else, and the two
matchings of §9.3 are precisely the kind of thing where a plausible implementation
gives a plausible wrong number.

The fixtures are constructed, not sampled. Real corpus documents would exercise the
common case densely and the boundaries not at all, and the boundaries are the whole
question here: adjacency, partial overlap in both directions, one prediction over two
gold spans and two predictions over one, type mismatch, PHI-free documents, sparse
types, layers that agree, and empty sides.

No fixture carries note text — `Mark` has no field for it (see
`test_mark_refuses_a_surface`). Offsets here are arbitrary integers chosen to make the
geometry legible. Rule ids are invented names that describe a mechanism (`cue_person`)
rather than anything matched, which is the rule the RuleAuthor prompt's Prohibition 2
puts on real ones: an id reaches `metrics.json` and `metrics.json` is published.

The fixture set, and the geometry each one exists to pin down:

  D1 adjacent-gold-one-wide-pred   §9.3's two-adjacent-NAME illustration: one wide
                                   prediction over two adjacent gold spans. Coverage
                                   says both hidden; assignment must give credit for
                                   one. The case that forces the bifurcation.
  D2 one-gold-split-by-two-preds   The mirror: fully hidden by the union, covered by
                                   no single prediction.
  D3 partial-overlap-both-ways     Prediction runs off the right end of one gold span
                                   and off the left end of another.
  D4 layers-agree                  Three gold spans: one found by a rule layer only,
                                   one by both, one by the tagger only. The fixture
                                   `complementarity.sets` has to tell apart.
  D5 type-mismatch                 Right offsets, wrong type. Not a detection.
  D6 no-gold-with-preds            PHI-free document that got predictions: false
                                   positive opportunity, out of the leak denominator.
  D7 gold-no-preds                 Nothing predicted at all.
  D8 no-gold-no-preds              A clean note nothing fired on. Still a document.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

import pytest

from src.corpora.base import ROOT, Document, Span, axis, family_of
from src.eval import scorer
from src.eval.scorer import (
    FULLY_COVERED,
    RELAXED,
    DocPair,
    Mark,
    ScorerError,
    assign,
    check_cost_to_date,
    coverage,
    dedupe,
    error_spans,
    from_documents,
    iter_metrics_path,
    metrics_path,
    score,
    sum_caching,
    sum_costs,
    write_metrics,
)
from src.sample import FALSE_POSITIVE, MISSED, ErrorSpan

# ─── fixtures ───────────────────────────────────────────────────────────────

#: Fixture rule ids. Rules-family spans must carry one and tagger spans must not, so
#: these are part of the geometry rather than decoration. `es:cue_person` fires in two
#: documents on purpose: a per-rule count that only ever aggregated within a document
#: would look correct on every single-document fixture.
CUE = "es:cue_person"
DATE_RULE = "es:date_numeric"
ID_RULE = "es:id_checksum"
GAZ = "es:area_gazetteer"
AGE_RULE = "es:age_cue"
RULE_IDS = {CUE, DATE_RULE, ID_RULE, GAZ, AGE_RULE}

#: Every fixture mark carries its `span_index` — the position in the list it would have
#: arrived in, as `from_documents` fills it. Written out rather than left None because
#: `error_spans()` requires it (a reference that resolves to the wrong span is wrong
#: silently), and a fixture corpus that could not be exported would make the export tests
#: run on two hand-built documents instead of on the eight cases the rest of this file is
#: built from. Nothing in `score()` reads the field; the aggregate assertions below are
#: unchanged by its presence, which `test_the_index_does_not_enter_any_metric` asserts.
D1 = DocPair(
    doc_id="adjacent-gold-one-wide-pred",
    gold=(Mark(0, 4, "NAME", span_index=0), Mark(5, 10, "NAME", span_index=1)),
    pred=(Mark(0, 10, "NAME", "tagger", span_index=0),),
)
D2 = DocPair(
    doc_id="one-gold-split-by-two-preds",
    gold=(Mark(0, 10, "NAME", span_index=0),),
    # Contiguous, not overlapping: 0-4 and 4-10 leave no uncovered character.
    pred=(Mark(0, 4, "NAME", "context_cue", CUE, span_index=0),
          Mark(4, 10, "NAME", "tagger", span_index=1)),
)
D3 = DocPair(
    doc_id="partial-overlap-both-ways",
    gold=(Mark(100, 110, "DATE", span_index=0), Mark(200, 210, "ID", span_index=1)),
    pred=(Mark(105, 115, "DATE", "regex_checksum", DATE_RULE, span_index=0),
          Mark(195, 205, "ID", "regex_checksum", ID_RULE, span_index=1)),
)
D4 = DocPair(
    doc_id="layers-agree",
    gold=(Mark(0, 5, "NAME", span_index=0), Mark(10, 15, "NAME", span_index=1),
          Mark(20, 25, "NAME", span_index=2)),
    pred=(Mark(0, 5, "NAME", "context_cue", CUE, span_index=0),
          Mark(10, 15, "NAME", "context_cue", CUE, span_index=1),
          # same span, second layer — index 2 of the prediction list
          Mark(10, 15, "NAME", "tagger", span_index=2),
          Mark(20, 25, "NAME", "tagger", span_index=3)),
)
D5 = DocPair(
    doc_id="type-mismatch",
    gold=(Mark(0, 5, "NAME", span_index=0),),
    pred=(Mark(0, 5, "LOCATION_AREA", "gazetteer", GAZ, span_index=0),),
)
D6 = DocPair(
    doc_id="no-gold-with-preds",
    gold=(),
    pred=(Mark(0, 5, "NAME", "tagger", span_index=0),
          Mark(10, 12, "AGE", "context_cue", AGE_RULE, span_index=1)),
)
D7 = DocPair(doc_id="gold-no-preds", gold=(Mark(0, 5, "PROFESSION", span_index=0),),
             pred=())
D8 = DocPair(doc_id="no-gold-no-preds", gold=(), pred=())

CORPUS = [D1, D2, D3, D4, D5, D6, D7, D8]

RUN = {
    "corpus": "es-meddocan", "detector": "RT", "supervision": "sup-free",
    "porting": "port-loop", "split": "dev", "model_id": "us.anthropic.claude-opus-5",
    "rules_version": "es@test", "seed": 20260805,
    # Required since schema 4 (DESIGN §10 A2). A fixed instant rather than `now()`:
    # a test whose expected output depends on the clock is a test that fails at
    # midnight, and nothing here asserts on the value beyond its shape.
    "generated": "2026-08-09T12:00:00Z", "commit": "0000000", "tree": "clean",
}
COST = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
        "wall_seconds": 0.0}

#: Required since schema 6 (DESIGN §3). A literal rather than
#: `src.termination.not_applicable(...)`, and the reason is this file's subject: the scorer
#: validates a block another module produced, so building it from that module here would
#: make every test pass or fail on the two agreeing rather than on the scorer's own checks.
#: The rule itself is tested in `tests/test_termination.py`, which is where the numbers are
#: derived rather than written down.
TERMINATION = {
    "reason": "not_applicable", "converged": False, "iterations": 1,
    "delta": 0.005, "delta_spans": 26, "delta_floor": 0.005, "k": 2, "ceiling": 8,
    "n_dev": 5254, "improvements": [],
}


@pytest.fixture(scope="module")
def scored():
    return score(CORPUS, excluded_gold=3)


def approx(value):
    return pytest.approx(value, abs=1e-9)


# ─── the no-surface decision, enforced ──────────────────────────────────────


def test_mark_refuses_a_surface():
    """The absent field is a guarantee, not a habit — so it is asserted.

    A comment saying "we do not pass the text" is undone by one caller passing the
    text. A constructor that raises is not.
    """
    with pytest.raises(TypeError):
        Mark(0, 4, "NAME", "tagger", surface="whatever")  # type: ignore[call-arg]
    assert not hasattr(Mark(0, 4, "NAME"), "surface")
    assert "surface" not in Mark.__slots__


def test_from_documents_drops_the_surface_it_is_given():
    """Loader Spans carry a surface; the scorer boundary is where it stops."""
    doc = Document(
        doc_id="d", corpus_id="es-meddocan", text="0123456789",
        spans=[Span(start=0, end=4, surface="0123", subtype="NOMBRE_SUJETO",
                    phi_type="NAME")],
    )
    pairs, excluded = from_documents([doc], {})
    assert excluded == 0
    assert pairs[0].gold == (Mark(0, 4, "NAME", span_index=0),)
    assert not hasattr(pairs[0].gold[0], "surface")


def test_no_output_field_holds_a_string_from_a_span(scored):
    """Nothing in the metrics payload could be note text.

    Every string in the output has to be a doc_id, an axis value, a metric name, a
    layer-set key built from layer names, or a `rule_id` the caller declared. Checked
    structurally rather than by eye, because the output grows.

    `rule_id` is the one string here that the scorer takes on trust: it comes from
    `rules/*.yaml`, an agent writes it, and a rule *name* can contain a surface form.
    That is screened where the id is created rather than here (CLAUDE.md, and the
    RuleAuthor prompt's Prohibition 2) — this test can only check that no *other*
    string appears, which is what it does.
    """
    allowed = (
        set(scorer.MODES) | {p.doc_id for p in CORPUS}
        | {"", "rules", "tagger"} | set(scorer.HEADLINE_MODE) | RULE_IDS
    )
    from src.corpora.base import axis
    for name in ("phi_type", "layer", "corpus", "detector", "supervision",
                 "porting", "split"):
        allowed |= set(axis(name))
    allowed |= {"|".join(sorted(s)) for s in _powerset(axis("layer"))}

    def walk(node, path):
        if isinstance(node, dict):
            for key, val in node.items():
                assert isinstance(key, str)
                walk(val, f"{path}.{key}")
        elif isinstance(node, list):
            for i, val in enumerate(node):
                walk(val, f"{path}[{i}]")
        elif isinstance(node, str):
            assert node in allowed or node in RUN.values(), (
                f"{path} holds an unrecognised string; if a span's surface can reach "
                "the metrics file this is where it shows up"
            )

    walk(scored, "$")


def _powerset(items):
    items = sorted(items)
    for mask in range(1 << len(items)):
        yield [x for i, x in enumerate(items) if mask >> i & 1]


# ─── D1: the case that forces two matchings ─────────────────────────────────
#
# Hand computation. gold [0,4) and [5,10), one prediction [0,10) of the same type.
#
#   coverage, both modes:   union = [(0,10)]. Gold 0 has 4 of 4 characters covered,
#                           gold 1 has 5 of 5. Both covered. Leaked 0.
#   assignment, both modes: both gold spans are eligible for the one prediction;
#                           overlaps 4 and 5, so the larger (gold 1) wins it and gold
#                           0 is a false negative. tp 1, fn 1, fp 0.
#   slack:                  gold 0 is covered and unmatched → 1.


def test_d1_coverage_hides_both_spans():
    for mode in (FULLY_COVERED, RELAXED):
        assert coverage(D1.gold, D1.pred, mode) == [True, True]


def test_d1_assignment_gives_credit_for_one():
    for mode in (FULLY_COVERED, RELAXED):
        matched, fn, fp = assign(D1.gold, D1.pred, mode)
        assert matched == {1: 0}, mode
        assert fn == [0] and fp == [], mode


def test_d1_coverage_and_assignment_disagree_and_slack_is_the_difference():
    """The verification the bifurcation exists for.

    A leak rate read off the assignment would report one leaked identifier in a
    document where every character of both identifiers is hidden.
    """
    one = score([D1])
    for mode in (FULLY_COVERED, RELAXED):
        block = one["modes"][mode]
        assert block["leak"] == {"leaked": 0, "denominator": 2, "rate": 0.0}
        assert block["overall"]["tp"] == 1
        assert block["overall"]["fn"] == 1
        assert block["overall"]["recall"] == approx(0.5)
        # 2 covered vs 1 matched, and the gap is exactly the recorded slack.
        assert block["assignment_slack"] == 1
        covered = block["leak"]["denominator"] - block["leak"]["leaked"]
        assert covered - block["overall"]["tp"] == block["assignment_slack"]


# ─── D2: the mirror case ────────────────────────────────────────────────────
#
# Hand computation. gold [0,10); predictions [0,4) and [4,10), same type, adjacent.
#
#   coverage fully_covered: union = [(0,10)] → 10 of 10 covered → not a leak.
#   assignment fully_covered: neither prediction contains the gold span on its own,
#                           so there is no eligible pair. tp 0, fn 1, fp 2.
#   slack fully_covered:    covered and unmatched → 1.
#   assignment relaxed:     [4,10) overlaps 6 and wins; [0,4) is a false positive.
#                           tp 1, fn 0, fp 1. slack 0.


def test_d2_union_of_two_predictions_is_not_a_leak():
    """A jointly hidden identifier is hidden. `fully_covered` is a union statement."""
    assert coverage(D2.gold, D2.pred, FULLY_COVERED) == [True]
    one = score([D2])
    assert one["modes"][FULLY_COVERED]["leak"]["leaked"] == 0
    # And no single prediction covers it, which is what makes it a false negative.
    assert assign(D2.gold, D2.pred, FULLY_COVERED) == ({}, [0], [0, 1])
    assert one["modes"][FULLY_COVERED]["overall"]["tp"] == 0
    assert one["modes"][FULLY_COVERED]["assignment_slack"] == 1


def test_d2_relaxed_assigns_the_larger_overlap():
    matched, fn, fp = assign(D2.gold, D2.pred, RELAXED)
    assert matched == {0: 1} and fn == [] and fp == [0]


def test_d2_fully_covered_families_are_joint_not_neither():
    """Covered by the union, by neither family alone.

    Calling this `neither` would break `neither == leaked`, and the breakdown would
    contradict the leak rate printed in the same file.
    """
    fam = score([D2])["modes"][FULLY_COVERED]["complementarity"]["families"]
    assert fam == {"rules_only": 0, "tagger_only": 0, "both": 0,
                   "joint_only": 1, "neither": 0, "denominator": 1}
    layers = score([D2])["modes"][FULLY_COVERED]["complementarity"]["layers"]
    assert layers["covered_by_union_only"] == 1
    assert layers["sets"] == {}          # not {"": 1}: nothing leaked here
    # Relaxed sees both families, since any overlap of the union is some single
    # prediction's overlap.
    rel = score([D2])["modes"][RELAXED]["complementarity"]
    assert rel["families"]["both"] == 1
    assert rel["families"]["joint_only"] == 0
    assert rel["layers"]["sets"] == {"context_cue|tagger": 1}


# ─── D3: partial overlap in both directions ─────────────────────────────────
#
# gold [100,110) with prediction [105,115) — 5 of 10 covered, off the right end.
# gold [200,210) with prediction [195,205) — 5 of 10 covered, off the left end.
#
#   fully_covered: neither covered → 2 leaks; no eligible pair → tp 0, fn 2, fp 2.
#   relaxed:       both covered → 0 leaks; both matched → tp 2, fn 0, fp 0.


def test_d3_direction_of_the_miss_does_not_matter():
    assert coverage(D3.gold, D3.pred, FULLY_COVERED) == [False, False]
    assert coverage(D3.gold, D3.pred, RELAXED) == [True, True]
    one = score([D3])
    assert one["modes"][FULLY_COVERED]["leak"]["rate"] == approx(1.0)
    assert one["modes"][FULLY_COVERED]["overall"]["tp"] == 0
    assert one["modes"][RELAXED]["leak"]["rate"] == approx(0.0)
    assert one["modes"][RELAXED]["overall"] == {
        "tp": 2, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0}
    # Nothing is covered under fully_covered, so nothing can be slack.
    assert one["modes"][FULLY_COVERED]["assignment_slack"] == 0


# ─── D4: layers that agree ──────────────────────────────────────────────────
#
# gold [0,5) found by context_cue only; [10,15) by context_cue and tagger (identical
# spans); [20,25) by tagger only. All exact, so both modes agree.
#
#   sets: "context_cue" 1, "context_cue|tagger" 1, "tagger" 1
#   families: rules_only 1, both 1, tagger_only 1
#   dedupe: the duplicated [10,15) collapses → 3 distinct predictions, 3 gold, tp 3,
#           fp 0. Without the collapse the second copy would be a false positive.


def test_d4_sets_distinguish_only_from_also():
    """DESIGN §7 needs "context_cue only" told apart from "context_cue also"."""
    for mode in (FULLY_COVERED, RELAXED):
        comp = score([D4])["modes"][mode]["complementarity"]
        assert comp["layers"]["sets"] == {
            "context_cue": 1, "context_cue|tagger": 1, "tagger": 1}, mode
        assert comp["layers"]["covered"] == {
            "context_cue": 2, "gazetteer": 0, "regex_checksum": 0, "tagger": 2}, mode
        assert comp["families"] == {
            "rules_only": 1, "tagger_only": 1, "both": 1, "joint_only": 0,
            "neither": 0, "denominator": 3}, mode


def test_d4_layer_agreement_is_not_a_false_positive():
    """Two layers finding one span found one thing.

    Under a raw 1:1 matching the second copy is unmatched and scores as a false
    positive, so precision would fall exactly where the layers agree — the same
    pathology §9.3 rules out for complementarity, in a different number.
    """
    kept, dupes = dedupe(D4.pred)
    assert dupes == 1 and len(kept) == 3
    block = score([D4])["modes"][FULLY_COVERED]
    assert block["overall"] == {"tp": 3, "fp": 0, "fn": 0,
                               "precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert block["duplicate_predictions"] == 1
    # The collapse is for credit only; the layer view still sees both layers.
    assert block["complementarity"]["layers"]["covered"]["tagger"] == 2


def test_dedupe_leaves_differently_bounded_predictions_alone():
    """Merging overlapping predictions is the merge policy's job (DESIGN §4).

    A scorer that merged on its own would make every merge policy score alike.
    """
    kept, dupes = dedupe([Mark(0, 5, "NAME", "context_cue", CUE),
                          Mark(0, 6, "NAME", "tagger")])
    assert dupes == 0 and len(kept) == 2


# ─── D5, D6, D7, D8: type mismatch and the empty sides ──────────────────────


def test_d5_type_mismatch_is_a_leak_and_a_false_positive():
    """Right offsets, wrong type: the identifier is not hidden and the guess is wrong."""
    for mode in (FULLY_COVERED, RELAXED):
        assert coverage(D5.gold, D5.pred, mode) == [False], mode
        assert assign(D5.gold, D5.pred, mode) == ({}, [0], [0]), mode
    block = score([D5])["modes"][RELAXED]
    assert block["leak"]["leaked"] == 1
    assert block["by_type"]["NAME"]["fn"] == 1
    assert block["by_type"]["NAME"]["fp"] == 0        # the fp belongs to the type
    assert block["by_type"]["LOCATION_AREA"]["fp"] == 1   # the detector claimed
    assert block["by_type"]["LOCATION_AREA"]["gold"] == 0


def test_d6_phi_free_document_is_out_of_the_leak_denominator():
    one = score([D6])
    assert one["modes"][RELAXED]["leak"]["denominator"] == 0
    assert one["modes"][RELAXED]["leak"]["rate"] is None      # not 0.0
    assert one["modes"][RELAXED]["by_document"]["denominator"] == 0
    assert one["false_positive_opportunity"] == {
        "documents_without_gold_phi": 1, "predictions_in_those_documents": 2}
    # It is still an opportunity to be wrong, and both predictions are.
    assert one["modes"][RELAXED]["overall"]["fp"] == 2


def test_d7_empty_predictions_leak_everything():
    for mode in (FULLY_COVERED, RELAXED):
        assert coverage(D7.gold, D7.pred, mode) == [False], mode
    block = score([D7])["modes"][FULLY_COVERED]
    assert block["leak"] == {"leaked": 1, "denominator": 1, "rate": 1.0}
    assert block["overall"] == {"tp": 0, "fp": 0, "fn": 1,
                                "precision": 0.0, "recall": 0.0, "f1": 0.0}
    assert block["complementarity"]["families"]["neither"] == 1
    assert block["complementarity"]["layers"]["sets"] == {"": 1}


def test_d8_empty_document_is_still_counted():
    one = score([D8])
    assert one["counts"]["documents"] == {
        "total": 1, "with_gold_phi": 0, "without_gold_phi": 1}
    assert one["modes"][RELAXED]["overall"]["fp"] == 0


def test_an_empty_corpus_produces_no_rates():
    """Nothing scored is not a perfect score."""
    one = score([])
    assert one["modes"][RELAXED]["leak"]["rate"] is None
    assert one["modes"][RELAXED]["macro"]["n_types"] == 0
    assert one["modes"][RELAXED]["macro"]["f1"] is None
    assert one["counts"]["gold"]["excluded_share"] is None


# ─── whole-corpus totals, hand-computed ─────────────────────────────────────
#
# Gold: D1 2 NAME, D2 1 NAME, D3 1 DATE + 1 ID, D4 3 NAME, D5 1 NAME,
#       D7 1 PROFESSION → 10 in scope. Predictions: 1+2+2+4+1+2+0+0 = 12.
# Documents: 8 total, 6 with gold PHI, 2 without.


def test_counts(scored):
    assert scored["counts"] == {
        "documents": {"total": 8, "with_gold_phi": 6, "without_gold_phi": 2},
        "gold": {"in_scope": 10, "excluded": 3,
                 "excluded_share": approx(3 / 13)},
        "pred": 12,
    }


# fully_covered, per document:
#   leaks   D1 0, D2 0, D3 2, D4 0, D5 1, D7 1            → 4 of 10 = 0.4
#   tp      D1 1, D2 0, D3 0, D4 3, D5 0, D6 0, D7 0      → 4
#   fp      D2 2, D3 2, D5 1, D6 2                        → 7
#   fn      10 − 4                                        → 6
#   precision 4/11, recall 4/10, f1 8/(8+7+6) = 8/21
#   slack   D1 1, D2 1                                    → 2
#   leaked documents D3, D5, D7                           → 3 of 6 = 0.5


def test_fully_covered_totals(scored):
    block = scored["modes"][FULLY_COVERED]
    assert block["leak"] == {"leaked": 4, "denominator": 10, "rate": approx(0.4)}
    assert block["overall"] == {
        "tp": 4, "fp": 7, "fn": 6,
        "precision": approx(4 / 11), "recall": approx(0.4),
        "f1": approx(8 / 21)}
    assert block["assignment_slack"] == 2
    assert block["by_document"] == {
        "with_leak": 3, "denominator": 6, "rate": approx(0.5)}
    assert block["duplicate_predictions"] == 1


# relaxed, per document:
#   leaks   D5 1, D7 1                                    → 2 of 10 = 0.2
#   tp      D1 1, D2 1, D3 2, D4 3                        → 7
#   fp      D2 1, D5 1, D6 2                              → 4
#   fn      10 − 7                                        → 3
#   precision 7/11, recall 0.7, f1 14/(14+4+3) = 14/21
#   slack   D1 1                                          → 1
#   leaked documents D5, D7                               → 2 of 6


def test_relaxed_totals(scored):
    block = scored["modes"][RELAXED]
    assert block["leak"] == {"leaked": 2, "denominator": 10, "rate": approx(0.2)}
    assert block["overall"] == {
        "tp": 7, "fp": 4, "fn": 3,
        "precision": approx(7 / 11), "recall": approx(0.7),
        "f1": approx(14 / 21)}
    assert block["assignment_slack"] == 1
    assert block["by_document"] == {
        "with_leak": 2, "denominator": 6, "rate": approx(1 / 3)}


# by_type, fully_covered. NAME gold 7 (D1 2, D2 1, D4 3, D5 1):
#   tp 4 (D1 1, D4 3), fn 3, fp 3 (D2 2, D6 1) → P = R = F1 = 4/7
#   leaked 1 (D5) → 1/7
# DATE gold 1: tp 0, fp 1, leaked 1.   ID gold 1: tp 0, fp 1, leaked 1.
# PROFESSION gold 1: tp 0, fp 0, leaked 1.
# LOCATION_AREA and AGE: gold 0, fp 1 each — in micro, out of macro.


def test_by_type_fully_covered(scored):
    by_type = scored["modes"][FULLY_COVERED]["by_type"]
    assert by_type["NAME"] == {
        "gold": 7, "tp": 4, "fp": 3, "fn": 3,
        "precision": approx(4 / 7), "recall": approx(4 / 7), "f1": approx(4 / 7),
        "leaked": 1, "leak_rate": approx(1 / 7), "sparse": True}
    assert by_type["DATE"]["leak_rate"] == approx(1.0)
    assert by_type["ID"]["fp"] == 1
    assert by_type["PROFESSION"] == {
        "gold": 1, "tp": 0, "fp": 0, "fn": 1,
        "precision": 0.0, "recall": 0.0, "f1": 0.0,
        "leaked": 1, "leak_rate": approx(1.0), "sparse": True}
    assert by_type["LOCATION_AREA"]["gold"] == 0
    assert by_type["LOCATION_AREA"]["leak_rate"] is None
    assert by_type["LOCATION_AREA"]["sparse"] is False
    assert sum(t["fp"] for t in by_type.values()) == 7


def test_by_type_relaxed(scored):
    by_type = scored["modes"][RELAXED]["by_type"]
    # NAME tp 5 (D1 1, D2 1, D4 3), fp 2 (D2 1, D6 1), fn 2, leaked 1.
    assert by_type["NAME"] == {
        "gold": 7, "tp": 5, "fp": 2, "fn": 2,
        "precision": approx(5 / 7), "recall": approx(5 / 7), "f1": approx(5 / 7),
        "leaked": 1, "leak_rate": approx(1 / 7), "sparse": True}
    assert by_type["DATE"]["f1"] == approx(1.0)
    assert by_type["DATE"]["leak_rate"] == approx(0.0)
    assert sum(t["fp"] for t in by_type.values()) == 4


# macro is over the four types that have gold: DATE, ID, NAME, PROFESSION.
#   fully_covered  P = R = F1 = (0 + 0 + 4/7 + 0)/4;  leak = (1+1+1/7+1)/4
#   relaxed        P = R = F1 = (1 + 1 + 5/7 + 0)/4;  leak = (0+0+1/7+1)/4


def test_macro_excludes_types_with_no_gold(scored):
    macro = scored["modes"][FULLY_COVERED]["macro"]
    assert macro["n_types"] == 4
    assert macro["f1"] == approx((4 / 7) / 4)
    assert macro["precision"] == approx((4 / 7) / 4)
    assert macro["leak_rate"] == approx((3 + 1 / 7) / 4)
    rel = scored["modes"][RELAXED]["macro"]
    assert rel["n_types"] == 4
    assert rel["f1"] == approx((2 + 5 / 7) / 4)
    assert rel["leak_rate"] == approx((1 + 1 / 7) / 4)


# Complementarity, fully_covered, gold span by gold span:
#   D1 g0 tagger_only  D1 g1 tagger_only  D2 joint_only  D3 DATE neither
#   D3 ID neither      D4 g0 rules_only   D4 g1 both     D4 g2 tagger_only
#   D5 neither         D7 neither
# → rules_only 1, tagger_only 3, both 1, joint_only 1, neither 4; sum 10.
# Relaxed reclassifies D2 (both) and both D3 spans (rules_only):
# → rules_only 3, tagger_only 3, both 2, joint_only 0, neither 2.


def test_complementarity_families(scored):
    assert scored["modes"][FULLY_COVERED]["complementarity"]["families"] == {
        "rules_only": 1, "tagger_only": 3, "both": 1, "joint_only": 1,
        "neither": 4, "denominator": 10}
    assert scored["modes"][RELAXED]["complementarity"]["families"] == {
        "rules_only": 3, "tagger_only": 3, "both": 2, "joint_only": 0,
        "neither": 2, "denominator": 10}


def test_complementarity_partitions_the_denominator(scored):
    """The five categories add up, and `neither` is exactly the leaked set.

    Both are structural: if either breaks, the breakdown and the headline leak rate
    in the same file disagree, and nothing else in the output would say so.
    """
    for mode in (FULLY_COVERED, RELAXED):
        block = scored["modes"][mode]
        fam = dict(block["complementarity"]["families"])
        denominator = fam.pop("denominator")
        assert sum(fam.values()) == denominator == 10, mode
        assert fam["neither"] == block["leak"]["leaked"], mode


def test_complementarity_layer_sets(scored):
    fc = scored["modes"][FULLY_COVERED]["complementarity"]["layers"]
    assert fc["sets"] == {
        "": 4,                       # the leaked four
        "context_cue": 1,            # D4 g0 — context_cue *only*
        "context_cue|tagger": 1,     # D4 g1 — context_cue *also*
        "tagger": 3,                 # D1 g0, D1 g1, D4 g2
    }
    assert fc["covered_by_union_only"] == 1          # D2
    assert sum(fc["sets"].values()) + fc["covered_by_union_only"] == 10
    assert fc["covered"] == {"context_cue": 2, "gazetteer": 0,
                             "regex_checksum": 0, "tagger": 4}

    rel = scored["modes"][RELAXED]["complementarity"]["layers"]
    assert rel["sets"] == {
        "": 2, "context_cue": 1, "context_cue|tagger": 2, "regex_checksum": 2,
        "tagger": 3}
    assert rel["covered_by_union_only"] == 0
    assert rel["covered"] == {"context_cue": 3, "gazetteer": 0,
                              "regex_checksum": 2, "tagger": 5}


def test_the_empty_set_key_is_the_leaked_set(scored):
    for mode in (FULLY_COVERED, RELAXED):
        layers = scored["modes"][mode]["complementarity"]["layers"]
        assert layers["sets"].get("", 0) == \
            scored["modes"][mode]["leak"]["leaked"], mode


def test_complementarity_by_type(scored):
    """Per type, over the same five categories."""
    name = scored["modes"][FULLY_COVERED]["complementarity"]["by_type"]["NAME"]
    # NAME's 7: D1 g0/g1 tagger_only, D2 joint_only, D4 rules_only/both/tagger_only,
    # D5 neither.
    assert name["families"] == {
        "rules_only": 1, "tagger_only": 3, "both": 1, "joint_only": 1,
        "neither": 1, "denominator": 7}
    date = scored["modes"][RELAXED]["complementarity"]["by_type"]["DATE"]
    assert date["families"]["rules_only"] == 1
    assert date["layers"]["sets"] == {"regex_checksum": 1}


def test_the_invariants_hold_on_random_geometries():
    """DESIGN §9.3 states four identities and one theorem. Checked by search.

    The hand-designed fixtures above are the cases I thought of. These identities are
    what the output's internal consistency rests on — if the five categories stop
    partitioning, or `neither` stops equalling the leaked count, then the
    complementarity breakdown and the leak rate contradict each other inside one
    `metrics.json` with both numbers looking reasonable. That failure has no symptom,
    so it gets a search over geometries I did not design rather than only the ones I
    did.

    Seeded, so a failure is reproducible: this is a property check, not a fuzz run
    whose counterexample is gone by the time it is read.
    """
    from src.corpora.base import axis

    rng = random.Random(7)
    layers = sorted(axis("layer"))
    types = sorted(axis("phi_type"))[:3]

    def _pred(start, end, phi_type, layer, rng):
        rule_id = (f"es:r{rng.randrange(3)}_{layer}"
                   if family_of(layer) == "rules" else None)
        return Mark(start, end, phi_type, layer, rule_id)

    for _ in range(600):
        gold = tuple(
            Mark(a, b, rng.choice(types))
            for a, b in sorted({(s, s + rng.randrange(1, 8))
                                for s in (rng.randrange(0, 40)
                                          for _ in range(rng.randrange(0, 4)))})
        )
        pred = tuple(
            _pred(s, s + rng.randrange(1, 9), rng.choice(types),
                  rng.choice(layers), rng)
            for s in (rng.randrange(0, 42) for _ in range(rng.randrange(0, 5)))
        )
        result = score([DocPair("d", gold, pred)])
        for mode in (FULLY_COVERED, RELAXED):
            block = result["modes"][mode]
            leaked = block["leak"]["leaked"]
            fam = dict(block["complementarity"]["families"])
            denominator = fam.pop("denominator")
            layers_block = block["complementarity"]["layers"]

            assert sum(fam.values()) == denominator, (mode, gold, pred)
            assert fam["neither"] == leaked, (mode, gold, pred)
            assert (sum(layers_block["sets"].values())
                    + layers_block["covered_by_union_only"]) == denominator
            assert layers_block["sets"].get("", 0) == leaked, (mode, gold, pred)

            # Per-rule attribution: every rules-family emission is attributed, and
            # the rules' totals exceed the mode's only by spans two rules both
            # emitted — bounded by the collapsed volume (see `_rule_tally`).
            rules = block["by_rule"].values()
            slack = block["duplicate_predictions"]
            assert sum(r["fires"] for r in rules) == sum(
                1 for p in pred if p.rule_id is not None), (mode, gold, pred)
            assert sum(r["tp"] for r in rules) <= block["overall"]["tp"] + slack
            assert sum(r["fp"] for r in rules) <= block["overall"]["fp"] + slack

        # A theorem, not an observation: any overlap by the union is some single
        # prediction's overlap, and that prediction has a family.
        relaxed = result["modes"][RELAXED]["complementarity"]
        assert relaxed["families"]["joint_only"] == 0, (gold, pred)
        assert relaxed["layers"]["covered_by_union_only"] == 0, (gold, pred)


def test_sparse_types_are_flagged_not_dropped(scored):
    """DESIGN §9.4: they stay in every denominator; the reporting layer omits rows."""
    block = scored["modes"][FULLY_COVERED]
    assert block["sparse"]["types"] == ["DATE", "ID", "NAME", "PROFESSION"]
    assert block["sparse"]["gold"] == 10
    assert sum(block["by_type"][t]["gold"] for t in block["sparse"]["types"]) == \
        block["leak"]["denominator"]


# ─── per-rule attribution ───────────────────────────────────────────────────
#
# Hand computation, from the fixture geometry. Rule emissions, of the 12 predictions:
#   es:cue_person     D2 [0,4), D4 [0,5), D4 [10,15)      → fires 3
#   es:date_numeric   D3 [105,115)                        → fires 1
#   es:id_checksum    D3 [195,205)                        → fires 1
#   es:area_gazetteer D5 [0,5) LOCATION_AREA              → fires 1
#   es:age_cue        D6 [10,12) AGE                      → fires 1
# The remaining 5 predictions are tagger spans and carry no rule_id.
#
# fully_covered, from the assignment in each document:
#   D2  no prediction contains the gold span → cue_person's span is unmatched  → fp
#   D3  neither prediction contains its gold span → both rules                 → fp
#   D4  all three distinct spans matched → cue_person [0,5) and [10,15)        → tp 2
#   D5  type mismatch, no eligible pair → area_gazetteer                       → fp
#   D6  no gold at all → age_cue                                              → fp
#   → cue_person tp 2 fp 1, date_numeric 0/1, id_checksum 0/1,
#     area_gazetteer 0/1, age_cue 0/1.  Rule tp 2 of the mode's 4; fp 5 of 7.
#
# relaxed:
#   D2  the tagger's [4,10) overlaps 6 and wins; cue_person's [0,4) is unmatched → fp
#   D3  both predictions overlap and match                                  → tp each
#   D4, D5, D6 unchanged
#   → cue_person tp 2 fp 1, date_numeric 1/0, id_checksum 1/0,
#     area_gazetteer 0/1, age_cue 0/1.  Rule tp 4 of the mode's 7; fp 3 of 4.


def test_by_rule_fully_covered(scored):
    assert scored["modes"][FULLY_COVERED]["by_rule"] == {
        AGE_RULE: {"layer": "context_cue", "fires": 1, "tp": 0, "fp": 1},
        GAZ: {"layer": "gazetteer", "fires": 1, "tp": 0, "fp": 1},
        CUE: {"layer": "context_cue", "fires": 3, "tp": 2, "fp": 1},
        DATE_RULE: {"layer": "regex_checksum", "fires": 1, "tp": 0, "fp": 1},
        ID_RULE: {"layer": "regex_checksum", "fires": 1, "tp": 0, "fp": 1},
    }


def test_by_rule_relaxed(scored):
    assert scored["modes"][RELAXED]["by_rule"] == {
        AGE_RULE: {"layer": "context_cue", "fires": 1, "tp": 0, "fp": 1},
        GAZ: {"layer": "gazetteer", "fires": 1, "tp": 0, "fp": 1},
        CUE: {"layer": "context_cue", "fires": 3, "tp": 2, "fp": 1},
        DATE_RULE: {"layer": "regex_checksum", "fires": 1, "tp": 1, "fp": 0},
        ID_RULE: {"layer": "regex_checksum", "fires": 1, "tp": 1, "fp": 0},
    }


def test_by_rule_false_positives_come_from_the_assignment_not_coverage():
    """The distinction the block exists for, in the one fixture that separates them.

    D2's `es:cue_person` span [0,4) overlaps the gold NAME [0,10) — under coverage it
    contributed to hiding the identifier, and a coverage-based attribution would call
    it a hit in both modes. But the assignment gives the credit to the tagger's
    [4,10), which overlaps more, and credit is not given twice. So it is a false
    positive for that rule under `relaxed` as well as under `fully_covered`.

    This is what makes the block actionable. A rule whose spans always lose the
    assignment to a better one is contributing nothing but noise, and it is exactly
    the rule an author should delete — under coverage-based attribution it would read
    as harmless and the file would only ever grow (§1.3 of the RuleAuthor prompt).
    """
    one = score([D2])
    for mode in (FULLY_COVERED, RELAXED):
        assert one["modes"][mode]["by_rule"] == {
            CUE: {"layer": "context_cue", "fires": 1, "tp": 0, "fp": 1}}, mode
    # And coverage really does disagree: the span is part of what hides the gold.
    assert coverage(D2.gold, D2.pred, FULLY_COVERED) == [True]
    assert one["modes"][FULLY_COVERED]["leak"]["leaked"] == 0


def test_by_rule_counts_a_rule_across_documents(scored):
    """`es:cue_person` fires in two documents; the tally is corpus-wide.

    A per-document block would be right on every single-document fixture and wrong on
    the corpus, which is the failure this asserts against.
    """
    entry = scored["modes"][FULLY_COVERED]["by_rule"][CUE]
    assert entry["fires"] == 3          # D2 once, D4 twice
    assert entry["tp"] + entry["fp"] == 3


def test_by_rule_totals_do_not_exceed_the_mode_totals(scored):
    """Rules account for part of the mode's tp/fp; the tagger accounts for the rest.

    Not an equality, for two reasons that pull in opposite directions: tagger spans
    carry no rule_id and are missing from the table, while a span two rules both
    emitted is counted for both and the mode's totals see it once. So the bound a
    reader may rely on is `overall + duplicate_predictions`, and nothing stronger —
    summing `by_rule` and expecting `overall` is the mistake this pins down.
    """
    for mode in (FULLY_COVERED, RELAXED):
        block = scored["modes"][mode]
        rules = block["by_rule"].values()
        slack = block["duplicate_predictions"]
        assert sum(r["tp"] for r in rules) <= block["overall"]["tp"] + slack, mode
        assert sum(r["fp"] for r in rules) <= block["overall"]["fp"] + slack, mode
    fc = scored["modes"][FULLY_COVERED]
    assert sum(r["tp"] for r in fc["by_rule"].values()) == 2      # of 4
    assert sum(r["fp"] for r in fc["by_rule"].values()) == 5      # of 7
    rel = scored["modes"][RELAXED]
    assert sum(r["tp"] for r in rel["by_rule"].values()) == 4     # of 7
    assert sum(r["fp"] for r in rel["by_rule"].values()) == 3     # of 4


def test_by_rule_fires_sum_to_the_rule_layer_predictions(scored):
    """Every rules-family prediction is attributed to exactly one rule.

    A span that fell out of the table would make a rule look quieter than it is, and
    the total is the only thing that would notice.
    """
    emitted = sum(1 for p in CORPUS for p in p.pred
                  if p.rule_id is not None)
    for mode in (FULLY_COVERED, RELAXED):
        rules = scored["modes"][mode]["by_rule"].values()
        assert sum(r["fires"] for r in rules) == emitted == 7, mode


def test_a_rules_family_span_without_a_rule_id_is_refused():
    """It would drop out of the attribution silently (DESIGN §3, §9.3)."""
    with pytest.raises(ScorerError, match="no rule_id"):
        Mark(0, 4, "NAME", "context_cue")
    for layer in ("regex_checksum", "gazetteer"):
        with pytest.raises(ScorerError, match="no rule_id"):
            Mark(0, 4, "NAME", layer)


def test_a_tagger_span_may_not_carry_a_rule_id():
    """No rule fired, so there is nothing to attribute.

    A checkpoint or model name in this field would put two kinds of thing in one
    table, and `by_rule`'s rows would stop being deletable objects.
    """
    with pytest.raises(ScorerError, match="only rules-family"):
        Mark(0, 4, "NAME", "tagger", "es:whatever")


def test_gold_carries_no_rule_id():
    assert Mark(0, 4, "NAME").rule_id is None
    with pytest.raises(ScorerError, match="only rules-family"):
        Mark(0, 4, "NAME", None, "es:whatever")


def test_an_unprefixed_rule_id_is_refused():
    """DESIGN §5.2: one corpus loads several rule files.

    `es-carmen` loads `es` and `cat`; an unprefixed `doctor_prefix` from each would
    share one row and the two rules' counts would be added together.
    """
    with pytest.raises(ScorerError, match="prefixed"):
        Mark(0, 4, "NAME", "context_cue", "doctor_prefix")
    with pytest.raises(ScorerError, match="prefixed"):
        Mark(0, 4, "NAME", "context_cue", "sv:doctor_prefix")   # not a lang value
    with pytest.raises(ScorerError, match="prefixed"):
        Mark(0, 4, "NAME", "context_cue", "es:")
    # Both files' rules coexist, which is the case the prefix exists for.
    assert Mark(0, 4, "NAME", "context_cue", "cat:doctor_prefix").rule_id \
        == "cat:doctor_prefix"


def test_one_rule_id_may_not_span_two_layers():
    """A rule declares its layer in the rule file (DESIGN §3).

    Two layers under one id means either two rules sharing a name or a layer that
    changed mid-run; both would attribute §7's per-layer results to the wrong
    mechanism.
    """
    pair = DocPair(
        "d", gold=(Mark(0, 4, "NAME"),),
        pred=(Mark(0, 4, "NAME", "context_cue", CUE),
              Mark(10, 14, "NAME", "gazetteer", CUE)),
    )
    with pytest.raises(ScorerError, match="two different layers"):
        score([pair])


def test_rule_id_messages_carry_no_surface():
    """A rule name can contain corpus text, so no rejection message quotes one.

    This is the same rule as `test_error_messages_carry_no_surface` applied to the
    field most likely to hold a memorised span: an agent writes these ids.
    """
    secret = "es:jperez_1978"
    cases = [
        lambda: Mark(0, 4, "NAME", "tagger", secret),
        lambda: Mark(0, 4, "NAME", "context_cue", secret.removeprefix("es:")),
        lambda: score([DocPair(
            "d", (Mark(0, 4, "NAME"),),
            (Mark(0, 4, "NAME", "context_cue", secret),
             Mark(10, 14, "NAME", "gazetteer", secret)))]),
    ]
    for call in cases:
        with pytest.raises(ScorerError) as exc:
            call()
        msg = str(exc.value)
        assert "jperez" not in msg and secret not in msg, msg


def test_two_rules_emitting_one_span_are_both_credited():
    """The collapse is about the span's credit, not about which rule gets named.

    Hand computation: gold [0,5) NAME, two rules emitting the byte-identical [0,5).
    `dedupe` collapses one, so the assignment sees one prediction and gives tp 1,
    fp 0, `duplicate_predictions` 1. Both rules fired and both found it, so each gets
    tp 1 — and the `by_rule` tp total is 2 against the mode's 1.

    The alternative is to credit whichever copy survived deduplication, and that makes
    the table depend on the order the detector emitted spans in: the same two rules
    would swap credit between runs with nothing in the output moving. The cost of
    avoiding it is the inequality above, which is why it is documented rather than
    asserted away.
    """
    pair = DocPair(
        "two-rules-one-span", gold=(Mark(0, 5, "NAME"),),
        pred=(Mark(0, 5, "NAME", "context_cue", CUE),
              Mark(0, 5, "NAME", "gazetteer", GAZ)),
    )
    block = score([pair])["modes"][RELAXED]
    assert block["overall"]["tp"] == 1 and block["overall"]["fp"] == 0
    assert block["duplicate_predictions"] == 1
    assert block["by_rule"] == {
        CUE: {"layer": "context_cue", "fires": 1, "tp": 1, "fp": 0},
        GAZ: {"layer": "gazetteer", "fires": 1, "tp": 1, "fp": 0},
    }
    # Order-independent, which is the property the double credit buys.
    flipped = DocPair("two-rules-one-span", pair.gold, pair.pred[::-1])
    assert score([flipped])["modes"][RELAXED]["by_rule"] == block["by_rule"]


def test_by_rule_is_absent_for_a_rule_that_fired_nothing():
    """The scorer never read the rule file, so it cannot list a silent rule.

    Stated as a test because the alternative reading — "the rule does not exist" — is
    the one an author would draw from an absent row, and the RuleAuthor holds the file
    and can tell the two apart itself.
    """
    assert score([D1])["modes"][RELAXED]["by_rule"] == {}      # tagger only


# ─── headline and determinism ───────────────────────────────────────────────


def test_headline_records_the_mode_per_metric(scored):
    """Leak rate leads with fully_covered, P/R/F1 with relaxed (DESIGN §9.3)."""
    assert scored["headline"]["leak_rate"] == {
        "value": approx(0.4), "mode": FULLY_COVERED}
    assert scored["headline"]["leak_rate_lower_bound"] == {
        "value": approx(0.2), "mode": RELAXED}
    assert scored["headline"]["f1"] == {
        "value": approx(14 / 21), "mode": RELAXED}
    # The lower bound is a lower bound.
    assert scored["headline"]["leak_rate_lower_bound"]["value"] <= \
        scored["headline"]["leak_rate"]["value"]


def test_the_scorer_does_not_choose(scored):
    """Both modes are complete and symmetric, so a later change of judgement about
    which figure leads edits the presentation instead of recomputing results."""
    assert set(scored["modes"]) == set(scorer.MODES)
    assert set(scored["modes"][FULLY_COVERED]) == set(scored["modes"][RELAXED])


def test_scoring_is_order_independent():
    """Shuffle the documents, the gold spans and the predictions; get the same file.

    This is what the total-order tie-break in `assign` buys, and the only way to see
    it is to score the same input twice in different orders. A tie broken by list
    position produces a scorer whose numbers move when a detector's output happens to
    come back in another order.
    """
    baseline = score(CORPUS)
    rng = random.Random(20260805)
    for _ in range(12):
        shuffled = []
        for pair in CORPUS:
            gold = list(pair.gold)
            pred = list(pair.pred)
            rng.shuffle(gold)
            rng.shuffle(pred)
            shuffled.append(DocPair(pair.doc_id, tuple(gold), tuple(pred)))
        rng.shuffle(shuffled)
        assert score(shuffled) == baseline


def test_ties_are_broken_by_the_total_order():
    """Two predictions with equal overlap on one gold span.

    [0,10) and [5,15) both overlap gold [5,10) by 5. The key orders by gold start,
    gold end, then prediction start — so [0,10) wins regardless of input order.
    """
    gold = (Mark(5, 10, "NAME"),)
    a = Mark(0, 10, "NAME", "tagger")
    b = Mark(5, 15, "NAME", "context_cue", CUE)
    assert assign(gold, (a, b), RELAXED)[0] == {0: 0}
    assert assign(gold, (b, a), RELAXED)[0] == {0: 1}    # same Mark, index 1


def test_no_agent_and_no_clock(scored):
    """Pure: the result is a function of the spans. Nothing here can vary between
    runs, which is why `metrics.json` is comparable across arms at all."""
    assert score(CORPUS, excluded_gold=3) == scored


# ─── input validation ───────────────────────────────────────────────────────


def test_an_unknown_type_is_refused():
    with pytest.raises(ScorerError, match="phi_type"):
        Mark(0, 4, "PATIENT_NAME")


def test_an_unknown_layer_is_refused():
    with pytest.raises(ScorerError, match="layer"):
        Mark(0, 4, "NAME", "crf")


def test_an_empty_or_inverted_span_is_refused():
    with pytest.raises(ScorerError, match=r"\[4, 4\)"):
        Mark(4, 4, "NAME")
    with pytest.raises(ScorerError, match=r"\[9, 4\)"):
        Mark(9, 4, "NAME")


def test_a_prediction_without_a_layer_is_refused():
    """DESIGN §3: the detector that emitted the span fills the layer in.

    Without one the span has nowhere to go in the complementarity breakdown, and the
    tempting default — treat it as a rule — is the kind of guess §3 forbids.
    """
    pair = DocPair("d", gold=(Mark(0, 4, "NAME"),), pred=(Mark(0, 4, "NAME"),))
    with pytest.raises(ScorerError, match="no layer"):
        score([pair])


def test_error_messages_carry_no_surface():
    """CLAUDE.md: offsets and lengths in messages, never corpus text.

    Exception text reaches terminals, CI logs and stack traces, and
    `tools/release_screen.py` does not run on any of those.
    """
    from src.corpora.base import axis

    # What a message may legitimately quote: config vocabulary, and the rejected
    # value itself — which came from the caller's arguments, not from a note.
    vocabulary = set()
    for name in ("phi_type", "layer"):
        vocabulary |= set(axis(name))

    cases = [
        (lambda: Mark(4, 4, "NAME"), set()),
        (lambda: Mark(0, 4, "NOPE"), {"NOPE"}),
        (lambda: Mark(0, 4, "NAME", "crf"), {"crf"}),
        (lambda: score([DocPair("d", (Mark(0, 4, "NAME"),),
                                (Mark(0, 4, "NAME"),))]), {"d"}),
    ]
    for call, own in cases:
        with pytest.raises(ScorerError) as exc:
            call()
        msg = str(exc.value)
        quoted = {w.strip("'\",.()[]") for w in msg.split() if w.startswith("'")}
        unexplained = quoted - vocabulary - own
        assert not unexplained, (
            f"message quotes {unexplained}, which is neither config vocabulary nor "
            "the rejected value — that is the shape a leaked surface would take"
        )


# ─── §9.1 excluded spans ────────────────────────────────────────────────────


def test_from_documents_filters_excluded_and_counts_them():
    """The scorer drops them, not the caller.

    A caller that forgets inflates the denominator with spans DESIGN §9.1 keeps out
    of every metric, and the resulting number looks fine.
    """
    doc = Document(
        doc_id="d", corpus_id="es-meddocan", text="x" * 40,
        spans=[
            Span(start=0, end=4, surface="xxxx", subtype="NOMBRE_SUJETO",
                 phi_type="NAME"),
            Span(start=10, end=14, surface="xxxx", subtype="OTROS_SUJETO_ASISTENCIA",
                 excluded=True),
            Span(start=20, end=24, surface="xxxx", subtype="NOMBRE_PERSONAL_SANITARIO",
                 phi_type="NAME"),
        ],
    )
    pairs, excluded = from_documents([doc], {"d": [Mark(0, 4, "NAME", "tagger")]})
    assert excluded == 1
    assert len(pairs[0].gold) == 2
    scored = score(pairs, excluded_gold=excluded)
    assert scored["counts"]["gold"] == {
        "in_scope": 2, "excluded": 1, "excluded_share": approx(1 / 3)}
    assert scored["modes"][RELAXED]["leak"]["denominator"] == 2


def test_from_documents_accepts_a_document_with_no_predictions():
    """A detector finding nothing in a note is a result, not a missing entry."""
    doc = Document(doc_id="d", corpus_id="es-meddocan", text="x" * 10)
    pairs, excluded = from_documents([doc], {})
    assert pairs == [DocPair("d", (), ())] and excluded == 0


# ─── error_spans: the per-gold verdicts as references (DESIGN §5.5) ───────────
#
# The list an iterating arm's next window is drawn from. Everything here is about two
# properties: which matching each verdict comes from, and that a reference means one thing.


def test_error_spans_reports_the_leak_set_as_missed():
    """`missed` is coverage under `fully_covered` — a leak. §1.4 promises the author that
    word means exactly that, and §3's stopping rule watches the same number.

    D3 is the case: two gold spans, each half-covered by an off-by-five prediction. Both
    leak, and both predictions are also unmatched under the credit question.
    """
    errors = error_spans([D3])
    missed = [e for e in errors if e.kind == MISSED]
    assert {(e.start, e.end) for e in missed} == {(100, 110), (200, 210)}
    assert {e.doc_id for e in missed} == {D3.doc_id}


def test_a_covered_but_unmatched_gold_span_is_not_reported_as_missed():
    """**The distinction the whole function turns on.** D1's gold [0,4) is covered by the
    one wide prediction and loses the assignment to gold [5,10) — `assignment_slack` 1.

    Every character of it is hidden. Reported as missed, it would ask a rule author for a
    rule against text that is already masked, and the arm would spend its rounds moving a
    number nobody publishes as the headline. The scorer already computes both verdicts; this
    asserts the export reads the right one.
    """
    assert score([D1])["modes"][FULLY_COVERED]["assignment_slack"] == 1
    assert score([D1])["modes"][FULLY_COVERED]["overall"]["fn"] == 1
    assert [e for e in error_spans([D1]) if e.kind == MISSED] == []


def test_a_jointly_covered_gold_span_is_not_missed_either():
    """D2: one gold span, two adjacent predictions, no uncovered character. `joint_only` in
    the complementarity breakdown and a false negative in the credit numbers. Not a leak,
    so not the author's problem."""
    assert score([D2])["modes"][FULLY_COVERED]["complementarity"]["families"][
        "joint_only"] == 1
    assert [e for e in error_spans([D2]) if e.kind == MISSED] == []


def test_false_positives_come_from_the_relaxed_assignment():
    """The mode that produces the published precision (`HEADLINE_MODE`).

    D3's two predictions each overlap a gold span of the right type. Under `fully_covered`
    they are unmatched — two false positives — and under `relaxed` both are matched and
    precision is 1.0. Exported from the strict mode, an author would be shown two spans as
    wrong that the reported figure counts as right.
    """
    assert score([D3])["modes"][RELAXED]["overall"] == {
        "tp": 2, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert score([D3])["modes"][FULLY_COVERED]["overall"]["fp"] == 2
    assert [e for e in error_spans([D3]) if e.kind == FALSE_POSITIVE] == []


def test_a_prediction_of_the_wrong_type_is_a_false_positive():
    """D5: gold NAME, prediction LOCATION_AREA over the same bytes. Unmatched in either
    mode, since assignment is within a type."""
    fps = [e for e in error_spans([D5]) if e.kind == FALSE_POSITIVE]
    assert [(e.phi_type, e.start, e.end) for e in fps] == [("LOCATION_AREA", 0, 5)]
    # And the gold span leaks: no same-type prediction covers it.
    assert [(e.kind, e.phi_type) for e in error_spans([D5]) if e.kind == MISSED] == [
        (MISSED, "NAME")]


def test_the_two_modes_are_derived_from_the_headline_and_not_written_down():
    """`ERROR_MODE` is built from `HEADLINE_MODE`. Spelling the modes as literals would let
    the reported headline move while the window kept showing the old one — an arm optimising
    something nobody publishes, with no symptom."""
    assert scorer.ERROR_MODE == {
        MISSED: scorer.HEADLINE_MODE["leak_rate"],
        FALSE_POSITIVE: scorer.HEADLINE_MODE["precision"],
    }
    src = (ROOT / "src" / "eval" / "scorer.py").read_text(encoding="utf-8")
    block = src.split("ERROR_MODE = ")[1].split("\n\n")[0]
    assert "HEADLINE_MODE" in block and '"fully_covered"' not in block


def test_the_export_carries_no_text_and_could_not():
    """Two guarantees, both structural. `Mark` has no surface field and neither does
    `ErrorSpan`, so there is no path by which this list holds note text."""
    for e in error_spans(CORPUS):
        assert not hasattr(e, "text") and not hasattr(e, "surface")
        assert isinstance(e.span_index, int) and isinstance(e.start, int)
    assert "text" not in ErrorSpan.__dataclass_fields__


def test_span_index_is_the_documents_own_list_and_not_the_in_scope_subset():
    """**The referent, and the one that is easy to get wrong** (DESIGN §11.2).

    The excluded span sits *first*, so a filtered index would name span 0 for a gold span
    that is span 1 of the document. `initial_error_pool()` enumerates the unfiltered list
    for iteration 1, so a filtered index here would make iteration 1 and iteration 4 mean
    different things by `(doc_id, span_index)` while both looked correct — and the wrongness
    is only visible to someone holding the corpus.
    """
    doc = Document(
        doc_id="d", corpus_id="es-meddocan", text="x" * 40,
        spans=[
            Span(start=0, end=4, surface="xxxx", subtype="SEXO_SUJETO_ASISTENCIA",
                 excluded=True),
            Span(start=10, end=14, surface="xxxx", subtype="NOMBRE_SUJETO",
                 phi_type="NAME"),
        ],
    )
    pairs, excluded = from_documents([doc], {})
    assert excluded == 1
    (only,) = error_spans(pairs)
    assert only.kind == MISSED and (only.start, only.end) == (10, 14)
    assert only.span_index == 1, (
        "the index is into the document's own span list, counting the excluded spans "
        "from_documents drops"
    )


def test_from_documents_replaces_an_index_a_mark_arrived_with():
    """`from_documents` is reading the list the reference is into, so it is authoritative.

    An index already on an incoming `Mark` came from some other list, and the two cannot
    both be the referent §11.2 fixes. This is also what makes the export testable at all: a
    `Mark` passed through unchanged carries no index for any caller that builds `Mark`s
    rather than `Span`s.
    """
    pairs, _ = from_documents(
        [Document(doc_id="d", corpus_id="es-meddocan", text="x" * 40)],
        {"d": [Mark(0, 4, "NAME", "tagger", span_index=97),
               Mark(20, 24, "NAME", "tagger")]},
    )
    assert [m.span_index for m in pairs[0].pred] == [0, 1]


def test_a_false_positives_index_is_into_the_prediction_list():
    """The other half of `ErrorSpan`'s documented referent. Prediction 1 is the unmatched
    one here, so an index counted over exported errors rather than over the input would say
    0."""
    doc = Document(
        doc_id="d", corpus_id="es-meddocan", text="x" * 40,
        spans=[Span(start=0, end=4, surface="xxxx", subtype="NOMBRE_SUJETO",
                    phi_type="NAME")],
    )
    pairs, _ = from_documents([doc], {"d": [Mark(0, 4, "NAME", "tagger"),
                                            Mark(20, 24, "NAME", "tagger")]})
    (fp,) = [e for e in error_spans(pairs) if e.kind == FALSE_POSITIVE]
    assert (fp.start, fp.end) == (20, 24) and fp.span_index == 1


def test_a_mark_without_an_index_is_refused_rather_than_defaulted():
    """A reference that resolves to the wrong span is wrong silently, in the hands of the
    only person who can resolve it. There is nothing to substitute — the position in the
    in-scope subset is a real index and the wrong one."""
    pair = DocPair(doc_id="d", gold=(Mark(0, 4, "NAME"),), pred=())
    with pytest.raises(ScorerError, match="span_index"):
        error_spans([pair])
    # And the message locates the span without quoting it (CLAUDE.md).
    try:
        error_spans([pair])
    except ScorerError as exc:
        assert "[0, 4)" in str(exc)


@pytest.mark.parametrize("bad", [-1, 1.5, True, "0"])
def test_a_span_index_that_is_not_a_position_is_refused(bad):
    """`True` is in the list on purpose: it is an `int` in Python and would index element 1
    of every document, which resolves to a real span and to the wrong one."""
    with pytest.raises(ScorerError, match="span_index"):
        Mark(0, 4, "NAME", span_index=bad)


def test_error_spans_is_deterministic_and_order_independent():
    """Same fold, shuffled, same list — for `write_spans`'s reason: this list becomes a
    file, and stability inherited from an upstream iteration order is not stability. It is
    also the seeded draw's premise (`src/sample.py`: sorting before drawing is what makes
    the sample independent of the order the scorer emitted errors in)."""
    rng = random.Random(20260812)
    shuffled = list(CORPUS)
    rng.shuffle(shuffled)
    assert error_spans(CORPUS) == error_spans(shuffled)
    assert error_spans(CORPUS) == sorted(error_spans(CORPUS), key=lambda e: e.key)


def test_the_export_and_the_metrics_agree_on_the_counts_they_share():
    """One matching, so the two views cannot disagree (DESIGN §9.3). This is the assertion
    that would fail if the verdicts were ever recomputed outside the scorer.

    `false_positive` is compared against the *relaxed* `fp` and `missed` against the
    *fully_covered* `leaked` — each half against the mode it is drawn from. The
    false-positive count is compared with `duplicate_predictions` allowed for, since the
    export is of deduplicated predictions and so is `fp`.
    """
    scored = score(CORPUS)
    errors = error_spans(CORPUS)
    assert sum(1 for e in errors if e.kind == MISSED) == \
        scored["modes"][FULLY_COVERED]["leak"]["leaked"]
    assert sum(1 for e in errors if e.kind == FALSE_POSITIVE) == \
        scored["modes"][RELAXED]["overall"]["fp"]


def test_two_layers_emitting_one_span_export_one_false_positive():
    """`dedupe`'s consequence, carried into the export. D4's duplicate pair is matched, so
    this builds the unmatched case: two rules on the same bytes, no gold at all.

    Two entries would spend two of the author's 40 slots on one error, and the second is not
    a second thing found."""
    pair = DocPair(
        doc_id="d", gold=(),
        pred=(Mark(0, 5, "ORGANISATION", "gazetteer", GAZ, span_index=0),
              Mark(0, 5, "ORGANISATION", "gazetteer", GAZ, span_index=1)),
    )
    assert score([pair])["modes"][RELAXED]["duplicate_predictions"] == 1
    fps = [e for e in error_spans([pair]) if e.kind == FALSE_POSITIVE]
    assert len(fps) == 1 and fps[0].span_index == 0


def test_a_prediction_without_a_layer_is_refused_here_too():
    """`score()` checks it and so does this: two entry points, one precondition. Grouping by
    layer puts a `None` in no group, silently."""
    pair = DocPair(doc_id="d", gold=(), pred=(Mark(0, 5, "NAME", span_index=0),))
    with pytest.raises(ScorerError, match="no layer"):
        error_spans([pair])


def test_the_index_does_not_enter_any_metric():
    """`Mark.span_index` is a reference and never a quantity. Scoring the same fold with the
    field stripped must give byte-identical numbers, or the field has become an input to a
    measurement and two callers who filled it differently would get different scores."""
    bare = [DocPair(doc_id=p.doc_id,
                    gold=tuple(Mark(m.start, m.end, m.phi_type, m.layer, m.rule_id)
                               for m in p.gold),
                    pred=tuple(Mark(m.start, m.end, m.phi_type, m.layer, m.rule_id)
                               for m in p.pred))
            for p in CORPUS]
    assert score(bare) == score(CORPUS)


def test_score_does_not_carry_the_error_list():
    """**The return is `metrics.json`'s content** (DESIGN §5.5). A per-span error list there
    would be published by every arm that scores, on every corpus, as a by-product of a
    feature only the iterating arms use — and it is the positions of every missed identifier
    in the fold.

    Checked on the payload rather than on the source, because what must not carry the list
    is the published file.
    """
    scored = score(CORPUS)
    flat = json.dumps(scored)
    assert "span_index" not in flat
    # No error *kind* is written anywhere. `false_positive_opportunity` is a key of the
    # payload and contains the string, so the check is on whole JSON tokens rather than on
    # substrings — a substring test here would have to be weakened to pass and would then
    # stop noticing a `"kind": "false_positive"` on a per-span row.
    tokens = set(re.findall(r'"([^"]*)"', flat))
    assert MISSED not in tokens and FALSE_POSITIVE not in tokens
    for key in ("errors", "error_spans", "leaks", "missed"):
        assert key not in scored


# ─── output ─────────────────────────────────────────────────────────────────


def test_metrics_path_follows_the_naming_template(tmp_path):
    assert metrics_path(RUN, root=tmp_path) == (
        tmp_path / "results/es-meddocan/RT/sup-free/port-loop/metrics.json")


@pytest.mark.parametrize("key,bad", [
    ("corpus", "meddocan"), ("detector", "R+T"), ("supervision", "supfree"),
    ("porting", "port-agentic"), ("split", "validation"),
])
def test_metrics_path_refuses_an_undefined_axis_value(key, bad, tmp_path):
    """A typo would create a sibling directory that looks like another arm."""
    with pytest.raises(ScorerError, match="axis"):
        metrics_path({**RUN, key: bad}, root=tmp_path)


@pytest.mark.parametrize("key", ["corpus", "detector", "supervision", "porting",
                                 "split", "model_id"])
def test_metrics_path_refuses_a_partly_specified_arm(key, tmp_path):
    with pytest.raises(ScorerError, match=key):
        metrics_path({**RUN, key: ""}, root=tmp_path)


# ─── the round's score (paths.itermetrics, DESIGN §5.5) ─────────────────────


ROUND_AXES = dict(corpus="es-meddocan", detector="RT", supervision="sup-free",
                  porting="port-loop")


def test_the_round_is_a_directory_beneath_the_arm(tmp_path):
    """`paths.itermetrics`' shape: the four axes above, `iter{N}/` below (DESIGN §5.3, §5.5).

    A filename suffix — `metrics_iter3.json` — was the alternative and §5.3 refused it for
    the rule files: reading which rounds exist becomes parsing filenames, and the round's
    three files stop being one directory.
    """
    assert iter_metrics_path(**ROUND_AXES, iteration=3, root=tmp_path) == (
        tmp_path / "results/es-meddocan/RT/sup-free/port-loop/iter3/metrics.json")


def test_the_round_scoped_score_sits_under_the_un_iterated_one(tmp_path):
    """Both from `PATH_AXES`, so a round's score cannot land in another arm's directory.

    This is the property that makes §5.5's duplication checkable rather than a convention:
    the two paths differ in exactly one component, and it is the round.
    """
    plain = metrics_path(RUN, root=tmp_path)
    scoped = iter_metrics_path(**ROUND_AXES, iteration=3, root=tmp_path)
    assert scoped.parent.parent == plain.parent
    assert scoped.name == plain.name == "metrics.json"


def test_the_run_block_can_ask_for_a_round(tmp_path):
    """`metrics_path(run, iteration=N)` routes to the same place, from a validated block.

    The routing exists so `write_metrics` has one path builder rather than a branch of its
    own, and it must agree with the keyword form — two ways to name one file that could
    differ is the defect the single template lookup exists to prevent.
    """
    assert metrics_path(RUN, root=tmp_path, iteration=3) == iter_metrics_path(
        **ROUND_AXES, iteration=3, root=tmp_path)


@pytest.mark.parametrize("key,bad", [
    ("corpus", "meddocan"), ("detector", "R+T"), ("supervision", "supfree"),
    ("porting", "port-agentic"),
])
def test_the_round_scoped_path_refuses_an_undefined_axis_value(key, bad, tmp_path):
    with pytest.raises(ScorerError, match="axis"):
        iter_metrics_path(**{**ROUND_AXES, key: bad}, iteration=1, root=tmp_path)


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "1", None])
def test_the_round_scoped_path_refuses_a_round_that_is_not_a_round(bad, tmp_path):
    """`iter0/` puts a round's score where the stopping rule cannot find it.

    δ/k is computed over the sequence of per-round dev leak rates (`src/termination.py`), so
    a score written outside the sequence is a round the rule reads as absent. `True` is in
    the list because `isinstance(True, int)` holds — a caller that passed a flag would
    silently name round 1.
    """
    with pytest.raises(ScorerError, match="iteration"):
        iter_metrics_path(**ROUND_AXES, iteration=bad, root=tmp_path)


def test_the_round_number_reaches_no_payload(scored, tmp_path):
    """**The round is a path component and never a field**, which is what lets §5.5's two
    copies be byte-identical.

    A round inside the run block would also be a premise `metrics_path` could later be asked
    to format — §4's fifth-path-component rejection arriving from the other side. Asserted by
    comparing the two files the same call would write, so a field added anywhere in the
    payload fails here rather than only where a reader thought to look.
    """
    plain = write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION,
                          root=tmp_path / "a")
    scoped = write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION,
                           root=tmp_path / "b", iteration=3)
    assert scoped.parent.name == "iter3"
    assert plain.read_bytes() == scoped.read_bytes()


# ─── model_id: recorded, required, and not an axis (DESIGN §4) ───────────────


def test_model_id_is_required(scored, tmp_path):
    """Bedrock aliases move under a stable name; an unrecorded run does not reproduce.

    Refused rather than defaulted. A default would put a plausible model name in a
    published file for a run that used a different one, which is worse than no field.
    """
    with pytest.raises(ScorerError, match="model_id"):
        write_metrics(scored, run={k: v for k, v in RUN.items() if k != "model_id"},
                      cost=COST, termination=TERMINATION, root=tmp_path)


def test_model_id_is_not_an_axis_and_is_not_validated_as_one():
    """A raw Bedrock identifier must pass. It is an observation, not a vocabulary.

    This is the check that keeps the two sets apart. If `model_id` were validated like
    the other run fields, `axis("model_id")` would raise for an axis nobody declared —
    and declaring one would refuse the true identifier while accepting a stand-in.
    """
    from src.corpora.base import CorpusError, naming
    assert "model_id" not in naming()["axes"]
    with pytest.raises(CorpusError):
        axis("model_id")
    assert "model_id" not in scorer.AXIS_VALUED
    assert "model_id" in scorer.REQUIRED_RUN
    # An identifier no closed vocabulary would hold, accepted because it is the truth
    # about what was called.
    scorer.check_run({**RUN, "model_id": "us.meta.llama4-maverick-17b-instruct-v1:0"})


def test_model_id_is_not_in_the_results_path(tmp_path):
    """Two models on one arm is §10 A2's appendix analysis, not a second cell."""
    a = metrics_path({**RUN, "model_id": "us.anthropic.claude-opus-5"}, root=tmp_path)
    b = metrics_path({**RUN, "model_id": "openai.gpt-oss-120b-1:0"}, root=tmp_path)
    assert a == b
    assert "claude" not in str(a) and "gpt" not in str(b)
    for part in metrics_path(RUN, root=tmp_path).relative_to(tmp_path).parts:
        assert part in set(RUN.values()) | {"results", "metrics.json"}


def test_a_rule_only_arm_records_the_explicit_absent_value(scored, tmp_path):
    """The R arm calls no model and says so — the cost block's zeros, one field over."""
    from src.corpora.base import model_id_absent
    run = {**RUN, "detector": "R", "model_id": model_id_absent()}
    path = write_metrics(scored, run=run, cost=COST, termination=TERMINATION, root=tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["run"]["model_id"] == "none"
    # And the string is the config's, not this test's or the scorer's.
    assert model_id_absent() not in scorer.__dict__.values()


def test_the_absent_value_comes_from_naming_yaml():
    """CLAUDE.md: a value that lands in a results file is defined in the config."""
    import re
    from src.corpora.base import model_id_absent
    absent = model_id_absent()
    for module in (Path("src/eval/scorer.py"), Path("src/eval/run_fold.py")):
        source = (ROOT / module).read_text(encoding="utf-8")
        code = re.sub(r'"""(?:.|\n)*?"""', "", source)
        code = re.sub(r"#.*", "", code)
        assert f'"{absent}"' not in code and f"'{absent}'" not in code, (
            f"{module} spells the absent model value as a literal")


# ─── the date and the commit hash: §10 A2's mitigation, as a property ────────
#
# A2 records that `model_id` cannot say which weights answered — an undated Bedrock alias
# in gives an undated alias back — and names a date and a commit hash as the partial
# mitigation. Until schema 4 that paragraph described something no writer produced and no
# reader could rely on. These tests are what makes the difference checkable.


@pytest.mark.parametrize("key", ["generated", "commit", "tree"])
def test_a_run_without_the_provenance_fields_is_refused(scored, tmp_path, key):
    """Presence first: the mitigation is worth nothing if a run block may omit it."""
    run = {k: v for k, v in RUN.items() if k != key}
    with pytest.raises(ScorerError, match=key):
        write_metrics(scored, run=run, cost=COST, termination=TERMINATION, root=tmp_path)


@pytest.mark.parametrize("bad", ["2026-08-09", "today", "2026-08-09 12:00:00",
                                 "2026-08-09T12:00:00+00:00", ""])
def test_generated_must_be_a_utc_instant(scored, tmp_path, bad):
    """A required field with no validation gets filled with anything once.

    `2026-08-09` is the interesting case and the reason for the check: it looks like an
    answer and cannot order two runs made on one day, which is the comparison A2 wants. The
    `+00:00` case is refused for a duller reason — one format, so the three records that
    carry instants sort against each other as text.
    """
    with pytest.raises(ScorerError, match="generated|no 'generated'"):
        write_metrics(scored, run={**RUN, "generated": bad}, cost=COST, termination=TERMINATION, root=tmp_path)


def test_generated_accepts_the_format_the_other_writers_produce():
    """Asserted against the real function rather than against a literal, so that changing
    the format in one place fails here instead of producing two conventions."""
    from src.eval import sealed_log
    stamp = sealed_log.datetime.now(sealed_log.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    scorer.check_run({**RUN, "generated": stamp})


@pytest.mark.parametrize("bad", ["probably fine", "CLEAN", "modified", "none"])
def test_tree_must_be_one_of_the_three_documented_states(scored, tmp_path, bad):
    """`tree` is what makes `commit` mean anything, so an unreadable value is worse than
    an absent one — a reader cannot act on a fourth possibility."""
    with pytest.raises(ScorerError, match="tree"):
        write_metrics(scored, run={**RUN, "tree": bad}, cost=COST, termination=TERMINATION, root=tmp_path)


def test_the_tree_vocabulary_is_the_one_tree_state_produces():
    """`TREE_STATES` is written out in the scorer to avoid an import cycle, which means it
    can drift from the function that produces the values. This is the check that it has
    not: `sealed_log.tree_state`'s docstring is the contract and its three values are
    asserted in `tests/test_seal_internals.py`."""
    from src.eval import sealed_log
    documented = {"clean", "dirty", "unknown"}
    assert set(scorer.TREE_STATES) == documented
    assert all(state in sealed_log.tree_state.__doc__ for state in scorer.TREE_STATES)


def test_an_unreadable_repository_can_still_be_recorded(scored, tmp_path):
    """`tree_state()` returns `(None, "unknown")` when git cannot be read, and that pair
    has to be writable.

    This is the case the first draft of the check got wrong: it demanded a truthy hash, so
    a run in a tree without commits could not be scored at all. That leaves a writer two
    options — refuse a real run, or put something in the field — and the second is what
    happens. A fabricated hash is exactly what `tree` was added to make impossible, so the
    validator must not be what forces one.
    """
    path = write_metrics(scored, run={**RUN, "commit": None, "tree": "unknown"},
                         cost=COST, termination=TERMINATION, root=tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["run"]["commit"] is None


def test_the_null_hash_is_accepted_only_with_an_unknown_tree(scored, tmp_path):
    """Null is permitted *in company*, never on its own.

    `clean` and `dirty` are both read from the output of a git command that also produced a
    revision, so either of them beside no hash is a contradiction rather than a gap. If the
    pairing went unchecked, the nullable field would become the way to omit the hash on a
    tree that could be read perfectly well — which is the loophole and not the exemption.
    """
    for state in ("clean", "dirty"):
        with pytest.raises(ScorerError, match="commit"):
            write_metrics(scored, run={**RUN, "commit": None, "tree": state},
                          cost=COST, termination=TERMINATION, root=tmp_path)


def test_the_commit_key_is_required_even_though_its_value_may_be_null():
    """Nullable is not optional. A key some arms omit cannot be compared across arms, and
    an absent key reads the same as a measured null to every consumer downstream."""
    assert "commit" in scorer.NULLABLE_RUN
    with pytest.raises(ScorerError, match="commit"):
        scorer.check_run({k: v for k, v in RUN.items() if k != "commit"})


@pytest.mark.parametrize("key", ["generated", "tree"])
def test_only_the_hash_is_nullable(scored, tmp_path, key):
    """The exemption is one field wide. `generated` comes from the clock and `tree` always
    has one of three answers — neither has a state in which it cannot be measured, so a
    null in either is an unwritten field rather than a recorded absence."""
    assert key not in scorer.NULLABLE_RUN
    with pytest.raises(ScorerError, match=key):
        write_metrics(scored, run={**RUN, key: None}, cost=COST, termination=TERMINATION, root=tmp_path)


def test_all_three_are_required_and_not_just_the_hash():
    """The hash is the most confident of the three and the least meaningful alone.

    Requiring `commit` without `tree` would publish a revision that may not describe what
    ran; requiring both without `generated` would leave nothing when `tree` is `dirty` or
    `unknown`, which are exactly the runs where the hash says least.
    """
    for field in ("generated", "commit", "tree"):
        assert field in scorer.REQUIRED_RUN


def test_the_provenance_fields_are_not_in_the_results_path(tmp_path):
    """Same rule as `model_id`: the path names the cell, and a re-run at a new instant is
    not a new cell. Otherwise every run would mint its own directory and no arm would ever
    be overwritten — which sounds safe and means results/ stops being a coordinate space."""
    a = metrics_path(RUN, root=tmp_path)
    b = metrics_path({**RUN, "generated": "2027-01-01T00:00:00Z", "commit": "deadbee",
                      "tree": "dirty"}, root=tmp_path)
    assert a == b


def test_the_schema_version_moved_with_the_new_required_fields():
    """A shape change moves `SCHEMA_VERSION` and leaves `SCORER_VERSION` alone — no
    existing field means anything different.

    Schema 4 was three new required fields; schema 5 is one new *optional* block
    (`model_lifecycle`). The optional case is the one worth a tripwire: it is the change
    most easily made without touching the counter, and then an absent block cannot be told
    apart from a writer that never had one. Failing here is the reminder to bump, not a
    reason to edit the number until the constant's own note says what changed.

    Schema 6 is a new *required* block (`termination`, DESIGN §3) — the opposite call from 5,
    and the constant's note says why: an absent `model_lifecycle` is itself a fact ("no probe
    was made"), and there is no analogous state for a stopping rule, because an arm that does
    not iterate records `not_applicable`.

    Schema 7 is a new required block (`cost_to_date`) and is the case the counter is least
    obviously needed for, which is why it is worth naming: the block's *value* for every arm
    written before it equals that arm's `cost`, so a reader diffing a schema-6 file against a
    schema-7 one sees a key appear whose content they could have computed. The counter is what
    says they could — without it, an absent `cost_to_date` is either "one round, so it is the
    cost" or "a writer that had no such field", and the two answers differ for exactly the
    arms §11.3 compares.

    Schema 8 is a new *optional* block (`caching`) and takes schema 5's call rather than
    schema 7's: an absent block is the record that the round was not cached, and a block of
    zeros would say "cached and never hit" instead. Every arm but `port-loop` is absent by
    construction (DESIGN §4), so unlike `cost_to_date` this block's value cannot be computed
    from a schema-7 file at all — which is the reason the counter has to move, since the same
    key will be absent from a schema-7 `port-loop` file for a different reason.
    """
    assert scorer.SCHEMA_VERSION == 8
    assert scorer.SCORER_VERSION == 1


def test_run_fold_writes_all_three(tmp_path):
    """The writer, not just the checker. `run_fold` already wrote `commit` and `tree`
    before schema 4, which is why the interesting one here is `generated`."""
    import re
    source = (ROOT / "src" / "eval" / "run_fold.py").read_text(encoding="utf-8")
    body = source[source.index("    run = {"):source.index("spans_file = write_spans")]
    for field in ("generated", "commit", "tree"):
        assert re.search(rf'"{field}":', body), f"run_fold's run block has no {field}"


def test_write_metrics_requires_cost(scored, tmp_path):
    """Cost beside quality (CLAUDE.md) — as an argument, not a convention."""
    with pytest.raises(TypeError):
        write_metrics(scored, run=RUN, root=tmp_path)      # type: ignore[call-arg]


def test_write_metrics_requires_run(scored, tmp_path):
    with pytest.raises(TypeError):
        write_metrics(scored, cost=COST, termination=TERMINATION, root=tmp_path)    # type: ignore[call-arg]


@pytest.mark.parametrize("key", ["llm_calls", "prompt_tokens", "completion_tokens",
                                 "wall_seconds"])
def test_write_metrics_refuses_a_partial_cost_block(scored, tmp_path, key):
    with pytest.raises(ScorerError, match=key):
        write_metrics(scored, run=RUN, cost={k: v for k, v in COST.items()
                                            if k != key},
                      termination=TERMINATION, root=tmp_path)


def test_zero_cost_is_accepted_and_absent_cost_is_not(scored, tmp_path):
    """The R arm makes no LLM calls and says so. Zero is a measurement."""
    path = write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION, root=tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["cost"] == {"llm_calls": 0, "prompt_tokens": 0,
                              "completion_tokens": 0, "wall_seconds": 0.0}
    with pytest.raises(ScorerError):
        write_metrics(scored, run=RUN, cost={**COST, "llm_calls": None},
                      termination=TERMINATION, root=tmp_path)


# ─── the round's cost and the arm's total (schema 7, DESIGN §11.3) ───────────
# One iteration of `port-loop` is 1 + N calls — RuleAuthor once, the Auditor once per dev
# document — so a per-round figure and an arm total became different numbers. The summing
# lives here rather than in the driver, because a rung whose cost decides whether it clears
# §11.3's standard must not also own the arithmetic. Two things are checked: that adding
# blocks is closed and total, and that the file says which of the two numbers is which.

#: A RuleAuthor call and two Auditor calls, as `Response.cost()` would report them. The token
#: figures are `port-oneshot-nofence`'s real ones scaled down, so the sums below are checkable
#: by hand rather than only against the function.
CALLS = [
    {"llm_calls": 1, "prompt_tokens": 21000, "completion_tokens": 2325,
     "wall_seconds": 32.542},
    {"llm_calls": 1, "prompt_tokens": 55300, "completion_tokens": 400, "wall_seconds": 11.5},
    {"llm_calls": 1, "prompt_tokens": 55301, "completion_tokens": 401, "wall_seconds": 11.25},
]


def test_summing_adds_every_key_including_wall_seconds():
    """`llm_calls` is the one §11.3 is read on and it is not the only one that must add.

    A sum that added calls and tokens while taking, say, the maximum wall time would be a
    defensible-sounding choice and it would silently change what the field means — see
    `sum_costs` on why sequential calls make the seconds additive, and on what a concurrent
    driver would owe.
    """
    assert sum_costs(CALLS) == {
        "llm_calls": 3, "prompt_tokens": 131601, "completion_tokens": 3126,
        "wall_seconds": 55.292,
    }


def test_an_iterations_total_is_one_rule_author_call_plus_one_per_document():
    """The shape the summing exists for, asserted as the count rather than as the tokens.

    §11.3's judgment is on `llm_calls` before it is on anything else, and the number that has
    to come out is 1 + N. Written with N documents' worth of Auditor calls so the assertion is
    about the relation and not about three.
    """
    n_documents = 12
    calls = [CALLS[0]] + [dict(CALLS[1]) for _ in range(n_documents)]
    assert sum_costs(calls)["llm_calls"] == 1 + n_documents


def test_summing_nothing_gives_the_zeros_and_not_an_empty_block():
    """A round with no calls is `NO_LLM_COST`, which the `R` arm's block already establishes:
    zero is a measurement and absent is not, and `write_metrics` refuses a partial block, so
    an empty dict here would fail one call site later with a message about the wrong thing."""
    assert sum_costs([]) == {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                             "wall_seconds": 0.0}


@pytest.mark.parametrize("key", list(scorer.REQUIRED_COST))
def test_summing_refuses_a_partial_block(key):
    """The failure mode is a total that looks like a measurement. A block missing
    `prompt_tokens` summed with two that have it gives a smaller number with nothing marking
    the gap, and the arm it belongs to then looks cheaper than it was."""
    partial = {k: v for k, v in CALLS[0].items() if k != key}
    with pytest.raises(ScorerError, match=key):
        sum_costs([CALLS[1], partial])


def test_summing_names_which_block_was_partial():
    """Index, not just the key. The driver passes a round's worth of Auditor calls and one of
    them is the caller's bug; a message naming only the field sends a reader through N of
    them. No corpus text is involved — this is a position in a list of cost dicts."""
    with pytest.raises(ScorerError, match=r"costs\[2\]"):
        sum_costs([CALLS[0], CALLS[1], {k: v for k, v in CALLS[2].items()
                                        if k != "wall_seconds"}])


def test_summing_refuses_a_key_the_cost_block_does_not_declare():
    """Closed on both sides, for the `termination` block's reason one field over: a fifth key
    would be added into a published total under a name this project never declared, and a
    reader cannot tell it from part of the cost model. `input_tokens` is the plausible one —
    it is Bedrock's own name for what `REQUIRED_COST` calls `prompt_tokens`."""
    with pytest.raises(ScorerError, match="input_tokens"):
        sum_costs([{**CALLS[0], "input_tokens": 21000}])


def test_summing_refuses_something_that_is_not_a_mapping():
    with pytest.raises(ScorerError, match="mapping"):
        sum_costs([CALLS[0], 32.5])          # type: ignore[list-item]


def test_the_written_file_carries_both_blocks(scored, tmp_path):
    """The relation §11.3 is read off, present in one file rather than reconstructed.

    Only the round's block says which iteration got expensive and only the total is what the
    1.9× standard compares, so a file with one of them loses something no aggregation can
    recover — the rounds' own files are deny-listed as a directory nobody publishes.
    """
    path = write_metrics(scored, run=RUN, cost=CALLS[0], cost_to_date=sum_costs(CALLS),
                         termination=TERMINATION, root=tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["cost"] == CALLS[0]
    assert written["cost_to_date"] == sum_costs(CALLS)
    # Beside `cost` rather than nested inside it: a total under a key whose name is the
    # round's figure is the ambiguity this block exists to remove.
    assert "cost_to_date" not in written["cost"]
    assert list(written).index("cost_to_date") == list(written).index("cost") + 1


def test_an_arm_with_one_round_writes_its_cost_as_its_total(scored, tmp_path):
    """The default, and it is the arm's true state rather than a fallback. `R` and the
    `port-oneshot` rungs run one round, so their round cost *is* their total; making them pass
    it twice would be a call-site ritual whose only failure mode is passing something else."""
    path = write_metrics(scored, run=RUN, cost=CALLS[0], termination=TERMINATION,
                         root=tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["cost_to_date"] == written["cost"] == CALLS[0]


def test_the_total_is_written_even_when_it_equals_the_round(scored, tmp_path):
    """Unconditional, for the reason schema 6 made `termination` required. A key present only
    when it differs from `cost` would be absent for every arm on the ladder except `port-loop`
    past iteration 1 — a field that cannot be compared across arms, at the one number the
    comparison is about."""
    path = write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION, root=tmp_path)
    assert "cost_to_date" in json.loads(path.read_text(encoding="utf-8"))


def test_a_total_below_the_round_it_contains_is_refused(scored, tmp_path):
    """The check that makes the two blocks' names mean something.

    A reader holding one file cannot verify that the total includes this round, so the writer
    does it. A total *below* the part it contains is two blocks built from different histories
    — the driver's accumulator reset, or the arguments passed the other way round, which is the
    likelier of the two and the one this message names.
    """
    with pytest.raises(ScorerError, match="cost_to_date is below cost"):
        write_metrics(scored, run=RUN, cost=sum_costs(CALLS), cost_to_date=CALLS[0],
                      termination=TERMINATION, root=tmp_path)


@pytest.mark.parametrize("key", list(scorer.REQUIRED_COST))
def test_the_relation_is_checked_key_by_key(key):
    """Not on `llm_calls` alone. A total whose call count is right and whose token count is
    below the round's is the state a partially-reset accumulator produces, and it is the one
    that would survive a check on the headline field."""
    with pytest.raises(ScorerError, match=key):
        check_cost_to_date(CALLS[0], {**CALLS[0], key: CALLS[0][key] - 1})


def test_equality_is_allowed_in_the_relation():
    """Iteration 1 of `port-loop` and every non-iterating arm. The check refuses a total that
    is smaller, not one that has not grown yet."""
    check_cost_to_date(CALLS[0], dict(CALLS[0]))


@pytest.mark.parametrize("key", list(scorer.REQUIRED_COST))
def test_a_partial_total_is_refused(scored, tmp_path, key):
    """`cost`'s rule, applied to the block §11.3 is actually read off."""
    with pytest.raises(ScorerError, match=key):
        write_metrics(scored, run=RUN, cost=CALLS[0],
                      cost_to_date={k: v for k, v in sum_costs(CALLS).items() if k != key},
                      termination=TERMINATION, root=tmp_path)


def test_the_writer_does_not_derive_the_total_from_anything(scored, tmp_path):
    """It writes what it is given. A writer that could compute the arm's total would be a
    second accumulator beside the driver's, and its file would agree with itself while
    disagreeing with the run — no reader could tell which of the two was the arm's cost.

    Asserted by handing it a total that is larger than any sum of this round: if the writer
    recomputed anything, this number could not survive to the file.
    """
    absurd = {"llm_calls": 97, "prompt_tokens": 1_100_000, "completion_tokens": 20_000,
              "wall_seconds": 4200.0}
    path = write_metrics(scored, run=RUN, cost=CALLS[0], cost_to_date=absurd,
                         termination=TERMINATION, root=tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["cost_to_date"] == absurd


def test_the_total_does_not_reach_the_path(scored, tmp_path):
    """Same rule as `cost` and `model_lifecycle`: the path names the cell of the experiment,
    and a number formatted into it would mint one."""
    a = write_metrics(scored, run=RUN, cost=CALLS[0], termination=TERMINATION, root=tmp_path)
    b = write_metrics(scored, run=RUN, cost=CALLS[0], cost_to_date=sum_costs(CALLS),
                      termination=TERMINATION, root=tmp_path)
    assert a == b


# ─── the caching block: optional, and absent is a state (schema 8, DESIGN §11.3) ──
# The only optional block in the file, and the reason is the one schema 5 had rather than the
# one schema 6 had: caching is a property of `port-loop`'s Auditor calls and of nothing else on
# the ladder, so "this arm did not cache" is a real state that a block of zeros would spell as
# "cached and never hit". Both numbers are published — the raw total in `cost.prompt_tokens`
# and the reads here — because a billed figure standing alone in the column §11.3's 1.9×
# standard is read off would be a claim about a transport optimisation wearing the clothes of a
# claim about role specialisation.

#: One Auditor write and two reads, in `Response.caching()`'s shape. 7172 is the measured
#: 2026-08-16 figure for `auditor.md` plus the banner plus §1.1's frame.
CACHED = [
    {"enabled": True, "boundary": "after_audit_frame", "ttl": "5m",
     "read_tokens": 0, "write_tokens": 7172},
    {"enabled": True, "boundary": "after_audit_frame", "ttl": "5m",
     "read_tokens": 7172, "write_tokens": 0},
    {"enabled": True, "boundary": "after_audit_frame", "ttl": "5m",
     "read_tokens": 7172, "write_tokens": 0},
]


def test_summing_caching_adds_the_counts_and_carries_the_split():
    """One write and N−1 reads per round is the model the 5m TTL rests on, and this is it.

    The counts add for `sum_costs`'s reason. The boundary and the TTL do not add — they are
    carried through, because every call in the round was split at the same place.
    """
    assert sum_caching(CACHED) == {
        "enabled": True, "boundary": "after_audit_frame", "ttl": "5m",
        "read_tokens": 14344, "write_tokens": 7172,
    }


def test_a_round_that_cached_nothing_produces_no_block_rather_than_zeros():
    """**The signature's whole point.** Every arm but `port-loop` lands here (DESIGN §4).

    Zeros would be a false statement, not a conservative one: a round that cached and never hit
    reports `read_tokens: 0` too, and the two are the difference between a transport
    optimisation that failed and one that was never asked for.
    """
    assert sum_caching([]) is None
    assert sum_caching([None]) is None
    assert sum_caching([None, None, None]) is None


def test_the_uncached_rule_author_call_does_not_suppress_the_round_s_block():
    """A round is one uncached RuleAuthor call and N cached Auditor ones (DESIGN §4).

    `None` mixed with blocks is the normal case rather than an inconsistency, so it is skipped
    rather than refused — and the round still reports what the Auditor calls retained.
    """
    assert sum_caching([None, *CACHED])["read_tokens"] == 14344


@pytest.mark.parametrize("key", list(scorer.REQUIRED_CACHING))
def test_a_partial_caching_block_is_refused(key):
    """`cost`'s rule. A block naming a retention without saying where it was split, or for how
    long, records that a third party held some bytes and leaves a reader unable to say which."""
    with pytest.raises(ScorerError, match=key):
        sum_caching([{k: v for k, v in CACHED[0].items() if k != key}])


def test_an_undeclared_key_in_the_caching_block_is_refused():
    """Closed on both sides, for `REQUIRED_COST`'s reason: a field this project never declared
    would be published in `results/` beside the ones it did, indistinguishable from them."""
    with pytest.raises(ScorerError, match="hit_rate"):
        sum_caching([{**CACHED[0], "hit_rate": 0.99}])


def test_two_different_splits_under_one_record_are_refused_rather_than_reconciled():
    """The disagreement is the finding.

    `naming.yaml` declares one boundary, so two values in one round means two different prompts
    were sent under one record. Picking either would publish a claim about which bytes were
    retained that is wrong for half the calls — and that claim is the only thing the block is
    for (`docs/prompts/auditor.md` §6).
    """
    with pytest.raises(ScorerError, match="cannot describe two splits"):
        sum_caching([CACHED[0], {**CACHED[1], "boundary": "after_auditor_template"}])
    with pytest.raises(ScorerError, match="cannot describe two splits"):
        sum_caching([CACHED[0], {**CACHED[1], "ttl": "1h"}])


def test_a_caching_block_that_is_not_a_mapping_is_refused():
    """`None` is the one non-mapping this function accepts, and it means something specific."""
    with pytest.raises(ScorerError, match="not a mapping or None"):
        sum_caching([CACHED[0], 7172])            # type: ignore[list-item]


def test_the_written_file_carries_the_caching_block_beside_the_cost_ones(scored, tmp_path):
    """Top-level and after the two cost blocks: it is what tells a reader how to read them.

    Not nested inside `cost`, because it is not a cost — the tokens in `cost.prompt_tokens` are
    the raw total the model read and these are how many of them were served from a cache.
    """
    path = write_metrics(scored, run=RUN, cost=CALLS[0], cost_to_date=sum_costs(CALLS),
                         caching=sum_caching(CACHED), termination=TERMINATION, root=tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["caching"] == sum_caching(CACHED)
    assert "caching" not in written["cost"]
    assert list(written).index("caching") == list(written).index("cost_to_date") + 1


def test_an_uncached_arm_writes_no_caching_key_at_all(scored, tmp_path):
    """The absence *is* the record (schema 8). Read the block's presence, not its contents."""
    path = write_metrics(scored, run=RUN, cost=CALLS[0], termination=TERMINATION,
                         root=tmp_path)
    assert "caching" not in json.loads(path.read_text(encoding="utf-8"))


def test_the_raw_total_stays_in_the_cost_block_and_the_reads_stay_out_of_it(scored, tmp_path):
    """**§11.3's arithmetic, as a property of the file.** Both numbers, and the billed basis
    derivable from them rather than substituted for one.

    `prompt_tokens` is what the model read; `caching.read_tokens` is what was served from the
    cache; the difference is what the invoice is computed on. A file publishing only the third
    would report a 340× reduction in the column the 1.9× standard is read off — and the loop
    would not have done any less work than it did before.
    """
    path = write_metrics(scored, run=RUN, cost=CALLS[0], cost_to_date=sum_costs(CALLS),
                         caching=sum_caching(CACHED), termination=TERMINATION, root=tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["cost"]["prompt_tokens"] == CALLS[0]["prompt_tokens"]
    assert set(written["cost"]) == set(scorer.REQUIRED_COST)
    billed = written["cost_to_date"]["prompt_tokens"] - written["caching"]["read_tokens"]
    assert billed == 131601 - 14344


@pytest.mark.parametrize("key", list(scorer.REQUIRED_CACHING))
def test_the_writer_refuses_a_partial_block_it_did_not_assemble(scored, tmp_path, key):
    """`sum_caching` is not the only door: a caller can pass a hand-built block, and
    `_write_failure` in `src/orchestrate.py` writes one into a different file. Both blocks are
    read as the same record, so both are validated at the writer."""
    with pytest.raises(ScorerError, match=key):
        write_metrics(scored, run=RUN, cost=CALLS[0],
                      caching={k: v for k, v in CACHED[0].items() if k != key},
                      termination=TERMINATION, root=tmp_path)


def test_a_caching_block_saying_it_is_disabled_is_refused(scored, tmp_path):
    """`enabled: False` and no block are the same claim written two ways, and only one of them
    is this schema's. Accepting both would make the absence unreadable — a reader finding no
    block could no longer conclude the arm did not cache, only that nobody said."""
    with pytest.raises(ScorerError, match="enabled"):
        write_metrics(scored, run=RUN, cost=CALLS[0], caching={**CACHED[0], "enabled": False},
                      termination=TERMINATION, root=tmp_path)


def test_the_caching_block_does_not_reach_the_path(scored, tmp_path):
    """`cost`'s rule: the path names the cell of the experiment and this is not one of its
    axes. An arm that cached and the same arm that did not would be one comparison, not two."""
    a = write_metrics(scored, run=RUN, cost=CALLS[0], termination=TERMINATION, root=tmp_path)
    b = write_metrics(scored, run=RUN, cost=CALLS[0], caching=sum_caching(CACHED),
                      termination=TERMINATION, root=tmp_path)
    assert a == b


# ─── the termination block (DESIGN §3) ──────────────────────────────────────
# The rule itself is `tests/test_termination.py`'s subject. What is checked here is the
# writer's side: the block is required, its shape is validated, and the one property §3
# forbids violating is refused at the boundary a hand-assembled block would come through.


def test_write_metrics_requires_termination(scored, tmp_path):
    """Required, not optional — the opposite call from `model_lifecycle` (schema 6's note).

    An arm that does not iterate passes `not_applicable`, so there is no state this block
    could be legitimately absent for, and a block some arms carried would be uncomparable
    across arms.
    """
    with pytest.raises(TypeError):
        write_metrics(scored, run=RUN, cost=COST, root=tmp_path)  # type: ignore[call-arg]


@pytest.mark.parametrize("key", list(scorer.REQUIRED_TERMINATION))
def test_write_metrics_refuses_a_partial_termination_block(scored, tmp_path, key):
    with pytest.raises(ScorerError, match=key):
        write_metrics(scored, run=RUN, cost=COST,
                      termination={k: v for k, v in TERMINATION.items() if k != key},
                      root=tmp_path)


def test_a_ceiling_stop_may_not_be_recorded_as_converged(scored, tmp_path):
    """**DESIGN §3's prohibition, at the writer.**

    `Termination.converged` is a property, so the contradiction cannot be constructed
    upstream — but `write_metrics` takes a mapping, and a hand-assembled block is exactly
    the path around the dataclass. A run that stopped at 8 with the leak rate still falling
    is a different claim from one that stopped at 5 having converged, and this is where the
    file is stopped from saying both.
    """
    forged = {**TERMINATION, "reason": "ceiling", "converged": True}
    with pytest.raises(ScorerError, match="may not be described as converged"):
        write_metrics(scored, run=RUN, cost=COST, termination=forged, root=tmp_path)


def test_a_converged_run_may_not_record_converged_false(scored, tmp_path):
    """The same check in the other direction — the flag is derived, so disagreement either
    way means the block was not built by the module that decides."""
    forged = {**TERMINATION, "reason": "converged", "converged": False}
    with pytest.raises(ScorerError, match="may not be described as converged"):
        write_metrics(scored, run=RUN, cost=COST, termination=forged, root=tmp_path)


def test_a_running_arm_records_a_null_reason_and_is_not_converged(scored, tmp_path):
    path = write_metrics(scored, run=RUN, cost=COST,
                         termination={**TERMINATION, "reason": None, "converged": False},
                         root=tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["termination"]["reason"] is None


def test_a_null_reason_with_converged_true_is_refused(scored, tmp_path):
    with pytest.raises(ScorerError, match="may not be described as converged"):
        write_metrics(scored, run=RUN, cost=COST,
                      termination={**TERMINATION, "reason": None, "converged": True},
                      root=tmp_path)


def test_a_reason_outside_the_vocabulary_is_refused(scored, tmp_path):
    """naming.yaml is the definition site; a reason invented at a call site is refused
    before it reaches a published file."""
    with pytest.raises(Exception, match="not a termination reason"):
        write_metrics(scored, run=RUN, cost=COST,
                      termination={**TERMINATION, "reason": "gave_up", "converged": False},
                      root=tmp_path)


def test_an_extra_termination_field_is_refused(scored, tmp_path):
    """A field this module does not know would be published unvalidated, and a reader
    cannot tell such a field from part of the pre-registration."""
    with pytest.raises(ScorerError, match="unexpected key"):
        write_metrics(scored, run=RUN, cost=COST,
                      termination={**TERMINATION, "patience": 3}, root=tmp_path)


def test_a_non_boolean_converged_is_refused(scored, tmp_path):
    with pytest.raises(ScorerError, match="not a bool"):
        write_metrics(scored, run=RUN, cost=COST,
                      termination={**TERMINATION, "converged": "false"}, root=tmp_path)


def test_the_termination_block_is_top_level_and_not_in_the_run_block(scored, tmp_path):
    """Beside `cost`, for `model_lifecycle`'s reason turned around: a threshold is a
    property of how the arm was run, not a coordinate of which arm it is. `run` is what
    `metrics_path` formats, so δ there would be one edit away from minting a cell."""
    path = write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION,
                         root=tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["termination"] == TERMINATION
    for key in scorer.REQUIRED_TERMINATION:
        assert key not in written["run"]


def test_the_termination_block_does_not_change_the_path(scored, tmp_path):
    """The path names the cell. Two δ values on one corpus are not two cells."""
    a = write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION,
                      root=tmp_path)
    b = write_metrics(scored, run=RUN, cost=COST,
                      termination={**TERMINATION, "delta": 0.08, "n_dev": 324},
                      root=tmp_path)
    assert a == b


def test_the_written_delta_is_recoverable_from_the_block(scored, tmp_path):
    """§3 requires the threshold to travel with the result: a run whose δ nobody can
    recover is a run whose stopping point cannot be checked."""
    path = write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION,
                         root=tmp_path)
    block = json.loads(path.read_text(encoding="utf-8"))["termination"]
    assert block["delta"] == max(block["delta_floor"],
                                 block["delta_spans"] / block["n_dev"])


def test_written_file_records_the_run_and_the_versions(scored, tmp_path):
    path = write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION, root=tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["schema_version"] == scorer.SCHEMA_VERSION
    assert written["run"]["scorer_version"] == scorer.SCORER_VERSION
    # CLAUDE.md: the seed and the rules version travel with the result.
    assert written["run"]["seed"] == RUN["seed"]
    assert written["run"]["rules_version"] == RUN["rules_version"]
    assert written["run"]["split"] == "dev"
    assert written["headline_mode"]["leak_rate"] == FULLY_COVERED
    assert written["modes"][FULLY_COVERED]["leak"]["leaked"] == 4


def test_the_written_file_is_the_scored_result(scored, tmp_path):
    """Nothing is recomputed on the way out."""
    path = write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION, root=tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    for key in ("counts", "headline", "modes", "false_positive_opportunity"):
        assert written[key] == json.loads(json.dumps(scored[key]))


# ─── model_lifecycle: beside the claims, never inside them ───────────────────

LIFECYCLE = {
    "model_arn": "arn:aws:bedrock:us-east-1::foundation-model/anthropic.x",
    "model_name": "Claude Opus 4.5", "status": "ACTIVE",
    "start_of_life_time": "2025-11-24T00:00:00+00:00",
}


def test_the_lifecycle_block_is_top_level_and_not_in_the_run_block(scored, tmp_path):
    """The run block is what the paper's premises are read off. A lifecycle timestamp
    beside `model_id_resolution` would read as corroborating a verdict it cannot support —
    `start_of_life_time` is when the *id* appeared, not what answered (DESIGN §4)."""
    path = write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION, model_lifecycle=LIFECYCLE,
                         root=tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["model_lifecycle"] == LIFECYCLE
    assert "model_lifecycle" not in written["run"]
    for field in written["run"]:
        assert "lifecycle" not in field and "start_of_life" not in field


def test_an_absent_probe_omits_the_block_rather_than_nulling_it(scored, tmp_path):
    """`model_lifecycle()` returns an `unavailable` record for every failure, so the two
    states here are "a probe happened" and "there was nothing to probe" — the R arm calls
    no model. A null would be a third state no reader can act on."""
    path = write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION, root=tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert "model_lifecycle" not in written


def test_an_empty_lifecycle_mapping_is_refused(scored, tmp_path):
    """An empty dict would be written as absent — that is, as "this arm called no model",
    which is the opposite of what a caller passing one meant. Refused rather than
    normalised, because the caller that built it lost a distinction on the way."""
    with pytest.raises(ScorerError, match="empty"):
        write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION, model_lifecycle={}, root=tmp_path)


def test_the_lifecycle_block_does_not_change_the_path(scored, tmp_path):
    """Same rule as `model_id` and `generated`: the path names the cell of the experiment,
    and anything formatted into it mints a cell."""
    a = write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION, root=tmp_path)
    b = write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION, model_lifecycle=LIFECYCLE,
                      root=tmp_path)
    assert a == b


def test_the_scorer_does_not_import_the_llm_client():
    """This module is agent-free and arm-free by construction (its own docstring). A
    dependency on the Bedrock client for one constant would end that, which is why
    `LIFECYCLE_UNAVAILABLE` is spelled out in the message rather than imported.

    Imports, not prose: the docstrings here name `bedrock.model_lifecycle` on purpose, and
    a substring search would forbid explaining the boundary in order to enforce it.
    """
    import ast
    tree = ast.parse((ROOT / "src" / "eval" / "scorer.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not any("llm" in m or "bedrock" in m for m in imported), sorted(imported)


def test_nothing_in_the_written_file_derives_a_resolution_from_the_lifecycle(scored,
                                                                            tmp_path):
    """The one thing this block must never become. `model_id_resolution` comes from the
    response the call returned; a writer that could compute it from a catalogue timestamp
    would be asserting the causal link the sixth mutation family is about."""
    path = write_metrics(scored, run=RUN, cost=COST, termination=TERMINATION, model_lifecycle=LIFECYCLE,
                         root=tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    # RUN carries no resolution field, and writing a lifecycle block does not invent one.
    assert "model_id_resolution" not in written["run"]
    assert "resolution" not in written["model_lifecycle"]
