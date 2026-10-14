"""Merge policies and the deterministic selector. DESIGN §12.2–12.3.

The policy-application test the v24 instruction requires, plus the one assertion that makes
`selector.combine()` trustworthy: that summing the per-type rows equals scoring the
combination through `scorer.score()`. That separability is a property of the scorer, not of
this package, so it is asserted against the scorer rather than assumed from a comment.

    python3 -m pytest tests/test_merge_policy.py -q
"""
from __future__ import annotations

import pytest

from src.corpora.base import CorpusError, Document, Span, canonical_types
from src.eval import scorer
from src.merge import policy as pol
from src.merge import selector as sel


def rule_span(start: int, end: int, phi_type: str, *, rule_id: str = "es:x",
              layer: str = "regex_checksum") -> Span:
    return Span(start=start, end=end, surface="x" * (end - start),
                subtype="SRC", phi_type=phi_type, layer=layer,
                detector="R", rule_id=rule_id)


def tagger_span(start: int, end: int, phi_type: str, score: float) -> Span:
    return Span(start=start, end=end, surface="x" * (end - start),
                subtype=phi_type, phi_type=phi_type, layer="tagger",
                detector="T", score=score)


# ─── the vocabulary and the space ───────────────────────────────────────────

def test_the_policy_space_is_ten_points_per_type_not_twelve():
    """`R` has no θ, so the space is 1 + 3 × 3 and not 4 × 3.

    The asymmetry is why `candidates()` is built rather than written out — a written-out
    list would have had to encode it, and encoding it wrongly produces three `R` rows that
    differ in a field `R` ignores.
    """
    space = pol.candidates()
    assert len(space) == 10
    assert sum(1 for c in space if c.policy == "R") == 1
    assert {str(c) for c in space} == {
        "R",
        "T@0.5", "T@0.7", "T@0.9",
        "UNION@0.5", "UNION@0.7", "UNION@0.9",
        "AGREE@0.5", "AGREE@0.7", "AGREE@0.9",
    }


def test_r_may_not_carry_a_theta_and_the_others_must():
    with pytest.raises(CorpusError, match="ignores the tagger"):
        pol.Choice(policy="R", theta=0.5)
    with pytest.raises(CorpusError, match="needs a theta"):
        pol.Choice(policy="UNION", theta=None)
    assert pol.Choice(policy="R", theta=None).theta is None


def test_a_theta_off_the_registered_grid_is_refused():
    """DESIGN §12.2 pinned the grid. A tuned θ makes the selection continuous."""
    with pytest.raises(CorpusError, match="pre-registered grid"):
        pol.Choice(policy="T", theta=0.8)
    with pytest.raises(CorpusError, match="pre-registered grid"):
        pol.Choice(policy="UNION", theta=0.6)


def test_an_undeclared_policy_name_is_refused():
    with pytest.raises(CorpusError, match="not a merge policy"):
        pol.Choice(policy="INTERSECT", theta=0.5)


def test_the_rt_baseline_is_union_at_the_lowest_threshold():
    """RT is a point *in* the space; otherwise the δ constraint names an unreachable point."""
    assert str(pol.rt_baseline()) == "UNION@0.5"
    assert pol.rt_baseline() in pol.candidates()
    assert set(pol.baseline_map()) == set(canonical_types())


# ─── applying one policy ────────────────────────────────────────────────────

RULES = [rule_span(0, 5, "NAME"), rule_span(20, 25, "NAME")]
TAGGER = [tagger_span(3, 9, "NAME", 0.95),     # overlaps rules[0]
          tagger_span(40, 46, "NAME", 0.60)]   # overlaps nothing


def test_policy_r_keeps_the_rules_and_ignores_every_tagger_span():
    got = pol.apply_one(pol.Choice("R", None), RULES, TAGGER)
    assert [(s.start, s.end) for s in got] == [(0, 5), (20, 25)]


@pytest.mark.parametrize("theta, expected", [
    (0.5, [(3, 9), (40, 46)]),
    (0.7, [(3, 9)]),
    (0.9, [(3, 9)]),
])
def test_policy_t_keeps_only_tagger_spans_at_or_above_theta(theta, expected):
    got = pol.apply_one(pol.Choice("T", theta), RULES, TAGGER)
    assert [(s.start, s.end) for s in got] == expected


def test_theta_is_inclusive_at_the_boundary():
    """`>= θ`, not `> θ`. A span at exactly 0.9 is kept at θ = 0.9.

    Pinned because the off-by-one here is silent: it moves a handful of spans at each
    threshold and shows up only as a slightly different leak rate.
    """
    exact = [tagger_span(0, 4, "NAME", 0.9)]
    assert pol.apply_one(pol.Choice("T", 0.9), [], exact)
    assert not pol.apply_one(pol.Choice("T", 0.9), [],
                             [tagger_span(0, 4, "NAME", 0.8999)])


@pytest.mark.parametrize("theta, expected", [
    (0.5, [(0, 5), (20, 25), (3, 9), (40, 46)]),
    (0.7, [(0, 5), (20, 25), (3, 9)]),
])
def test_policy_union_keeps_both_sides(theta, expected):
    got = pol.apply_one(pol.Choice("UNION", theta), RULES, TAGGER)
    assert [(s.start, s.end) for s in got] == expected


def test_policy_agree_keeps_both_overlapping_spans_and_not_one():
    """DESIGN §12.2: picking a winner is what §9.3 forbids the scorer from doing.

    `rules[0]` at [0,5) and the tagger span at [3,9) overlap, so **both** survive. The
    rules span at [20,25) and the tagger span at [40,46) have no counterpart and are
    dropped.
    """
    got = pol.apply_one(pol.Choice("AGREE", 0.5), RULES, TAGGER)
    assert sorted((s.start, s.end) for s in got) == [(0, 5), (3, 9)]
    assert len(got) == 2, "AGREE is a filter on agreement, not an intersection"


def test_agree_applies_theta_before_looking_for_agreement():
    """A sub-threshold tagger span cannot vouch for a rules span.

    Otherwise θ would mean something different under `AGREE` than under `T` and `UNION`,
    and the policy table would not be comparable across its own rows.
    """
    rules = [rule_span(0, 5, "NAME")]
    weak = [tagger_span(2, 7, "NAME", 0.60)]
    assert pol.apply_one(pol.Choice("AGREE", 0.5), rules, weak)
    assert not pol.apply_one(pol.Choice("AGREE", 0.7), rules, weak)


def test_agree_requires_the_same_type():
    """Cross-type overlap is not agreement — the scorer never matches across types."""
    rules = [rule_span(0, 5, "NAME")]
    tagger = [tagger_span(2, 7, "DATE", 0.95)]
    # apply_one is already type-filtered by apply_map; called directly with mixed types
    # the two lists are treated as one type's, which is the caller's contract. The
    # type-level guarantee is asserted through apply_map instead:
    doc_spans = rules + tagger
    got = pol.apply_map(doc_spans, {"NAME": pol.Choice("AGREE", 0.5),
                                    "DATE": pol.Choice("AGREE", 0.5)})
    assert got == [], "neither type has a counterpart of its own type"


def test_a_policy_selects_and_never_rewrites_a_span():
    """The returned spans are the input objects. A changed boundary would be a detection
    decision taken by the merge layer."""
    got = pol.apply_one(pol.Choice("UNION", 0.5), RULES, TAGGER)
    assert all(any(g is original for original in RULES + TAGGER) for g in got)


# ─── applying a map ─────────────────────────────────────────────────────────

def test_apply_map_routes_each_type_to_its_own_policy():
    spans = [rule_span(0, 5, "NAME"), tagger_span(0, 5, "NAME", 0.95),
             rule_span(10, 15, "DATE"), tagger_span(30, 35, "DATE", 0.95)]
    got = pol.apply_map(spans, {"NAME": pol.Choice("R", None),
                                "DATE": pol.Choice("T", 0.5)})
    assert sorted((s.start, s.end, s.phi_type) for s in got) == [
        (0, 5, "NAME"), (30, 35, "DATE")]


def test_a_missing_type_is_refused_rather_than_defaulted_to_the_baseline():
    """DESIGN §12.2's note: the obvious default is the RT baseline, so defaulting would
    make the arm partly RT with nothing in the record saying so."""
    spans = [rule_span(0, 5, "NAME"), rule_span(10, 15, "DATE")]
    with pytest.raises(CorpusError, match="Refused rather than defaulted"):
        pol.apply_map(spans, {"NAME": pol.Choice("R", None)})


def test_a_span_with_no_layer_is_refused():
    """Families come from the layer and never from a detector name (DESIGN §3)."""
    orphan = Span(start=0, end=4, surface="xxxx", subtype="SRC", phi_type="NAME")
    with pytest.raises(CorpusError, match="carries no layer"):
        pol.apply_map([orphan], {"NAME": pol.Choice("R", None)})


def test_the_family_of_a_span_is_read_from_naming_not_from_its_detector():
    """A span whose `detector` says R but whose layer is `tagger` counts as tagger.

    The detector string is deliberately misleading here. CLAUDE.md forbids deriving a
    layer from a detector name, and this is the same prohibition one level up.
    """
    liar = Span(start=0, end=4, surface="xxxx", subtype="NAME", phi_type="NAME",
                layer="tagger", detector="R", score=0.95)
    kept = pol.apply_map([liar], {"NAME": pol.Choice("T", 0.5)})
    assert len(kept) == 1, "a tagger-layer span must be visible to policy T"
    assert pol.apply_map([liar], {"NAME": pol.Choice("R", None)}) == []


def test_policies_round_trip_through_their_record_form():
    original = {"NAME": pol.Choice("AGREE", 0.7), "ID": pol.Choice("R", None)}
    assert pol.from_record(pol.as_record(original)) == original


def test_a_record_naming_an_undeclared_type_is_refused():
    with pytest.raises(CorpusError, match="not a phi_type"):
        pol.from_record({"NOT_A_TYPE": {"policy": "R", "theta": None}})


# ─── the table, and the separability it relies on ───────────────────────────

def _two_type_corpus() -> tuple[list[Document], dict, dict]:
    """Two documents, two types, with every complementarity cell populated."""
    text = "x" * 200
    docs = []
    rules: dict[str, list] = {}
    tagger: dict[str, list] = {}
    for index in range(2):
        doc_id = f"d{index}"
        gold = [
            Span(start=0, end=5, surface="xxxxx", subtype="S", phi_type="NAME"),
            Span(start=20, end=25, surface="xxxxx", subtype="S", phi_type="NAME"),
            Span(start=40, end=45, surface="xxxxx", subtype="S", phi_type="NAME"),
            Span(start=60, end=65, surface="xxxxx", subtype="S", phi_type="DATE"),
            Span(start=80, end=85, surface="xxxxx", subtype="S", phi_type="DATE"),
        ]
        docs.append(Document(doc_id=doc_id, corpus_id="es-meddocan", text=text,
                             spans=gold, split="dev"))
        rules[doc_id] = [
            rule_span(0, 5, "NAME"),        # both (with tagger)
            rule_span(20, 25, "NAME"),      # rules only
            rule_span(120, 125, "NAME"),    # false positive
            rule_span(60, 65, "DATE"),      # rules only
        ]
        tagger[doc_id] = [
            tagger_span(0, 5, "NAME", 0.95),    # both
            tagger_span(40, 45, "NAME", 0.60),  # tagger only, below 0.7
            tagger_span(80, 85, "DATE", 0.95),  # tagger only
            tagger_span(150, 155, "DATE", 0.95),  # false positive
            # A type the fold has NO gold for, predicted anyway by both halves. This is
            # what `combine()` dropped until 2026-10-14: no TP, no FN, no leak, and
            # false positives that belong in A's denominator. On en-deid the real
            # version of this was 110 of them.
            tagger_span(170, 175, "LOCATION_STREET", 0.95),
        ]
        rules[doc_id].append(rule_span(180, 185, "LOCATION_STREET"))
        # the gold at [40,45) NAME is found only by a 0.60 tagger span;
        # nothing covers nothing — every cell is exercised.
    return docs, rules, tagger


def test_the_table_counts_match_a_full_scorer_run_on_the_same_policy():
    """The assertion that makes `combine()` exact rather than approximate.

    `selector` sums per-type rows instead of re-scoring each of the 10^k combinations,
    which is sound only because the scorer partitions by type — `assign()` skips pairs of
    differing type and `coverage()` unions per type. That is a property of the scorer and
    could stop holding, so it is checked against the scorer here.
    """
    docs, rules, tagger = _two_type_corpus()
    table = sel.build_table(docs, rules, tagger)
    choices = {**pol.baseline_map(),
               "NAME": pol.Choice("UNION", 0.5), "DATE": pol.Choice("R", None)}
    summed = sel.combine(table, choices)

    merged = {}
    for doc in docs:
        merged[doc.doc_id] = pol.apply_map(
            rules[doc.doc_id] + tagger[doc.doc_id], choices)
    pairs, excluded = scorer.from_documents(docs, merged)
    scored = scorer.score(pairs, excluded_gold=excluded)

    assert summed["L_full"] == pytest.approx(
        scored["headline"]["leak_rate"]["value"])
    assert summed["L_rel"] == pytest.approx(
        scored["headline"]["leak_rate_lower_bound"]["value"])
    overall = scored["modes"][scorer.RELAXED]["overall"]
    assert (summed["tp"], summed["fp"], summed["fn"]) == (
        overall["tp"], overall["fp"], overall["fn"])
    assert summed["A"] == pytest.approx(
        overall["tp"] / (overall["tp"] + overall["fp"] + overall["fn"]))


def test_the_table_separability_holds_for_a_mixed_combination():
    """The same check where the two types take *different* policies.

    The single-policy case could pass even if the rows were entangled, because both types
    would be wrong in the same direction. A mixed combination is the one that fails if
    type `t`'s row depends on type `u`'s policy.
    """
    docs, rules, tagger = _two_type_corpus()
    table = sel.build_table(docs, rules, tagger)
    for name, date in (("AGREE@0.5", "T@0.5"), ("T@0.9", "UNION@0.9"),
                       ("R", "AGREE@0.5")):
        choices = {**pol.baseline_map(),
                   "NAME": _parse(name), "DATE": _parse(date)}
        summed = sel.combine(table, choices)
        merged = {d.doc_id: pol.apply_map(rules[d.doc_id] + tagger[d.doc_id],
                                          choices) for d in docs}
        pairs, excluded = scorer.from_documents(docs, merged)
        scored = scorer.score(pairs, excluded_gold=excluded)
        overall = scored["modes"][scorer.RELAXED]["overall"]
        assert (summed["tp"], summed["fp"], summed["fn"]) == (
            overall["tp"], overall["fp"], overall["fn"]), (name, date)
        assert summed["L_full"] == pytest.approx(
            scored["headline"]["leak_rate"]["value"]), (name, date)


def _parse(name: str) -> pol.Choice:
    if "@" not in name:
        return pol.Choice(name, None)
    policy, theta = name.split("@")
    return pol.Choice(policy, float(theta))


def test_a_type_with_no_gold_still_contributes_its_false_positives():
    """The regression test for the bug the first real-data run found.

    `combine()` skipped types with no dev gold. They contribute no TP, no FN and no
    leak — and they do contribute false positives, which are in A's denominator and in
    precision. On `en-deid` the omission dropped 110 FPs and reported A as 0.7287 where
    `scorer.score()` said 0.5845.

    It was invisible to the original separability test because every type in that
    fixture either had gold or had no predictions at all, and it is invisible to any
    comparison between two numbers from this module: §12.2 fixes the no-gold types at
    the baseline, so their contribution is a constant that cancels in every ranking.
    Only a comparison against the scorer shows it, which is what this asserts.
    """
    docs, rules, tagger = _two_type_corpus()
    table = sel.build_table(docs, rules, tagger)
    assert table["LOCATION_STREET"].n_gold == 0
    assert table["LOCATION_STREET"].candidates["UNION@0.5"].fp > 0, (
        "the fixture must plant false positives on a type with no gold")

    choices = pol.baseline_map()
    summed = sel.combine(table, choices)
    merged = {d.doc_id: pol.apply_map(rules[d.doc_id] + tagger[d.doc_id], choices)
              for d in docs}
    pairs, excluded = scorer.from_documents(docs, merged)
    overall = scorer.score(pairs, excluded_gold=excluded)["modes"][
        scorer.RELAXED]["overall"]
    assert summed["fp"] == overall["fp"]
    assert summed["A"] == pytest.approx(
        overall["tp"] / (overall["tp"] + overall["fp"] + overall["fn"]))


def test_combine_requires_a_choice_for_every_type_including_no_gold_ones():
    docs, rules, tagger = _two_type_corpus()
    table = sel.build_table(docs, rules, tagger)
    partial = {t: c for t, c in pol.baseline_map().items()
               if t != "LOCATION_STREET"}
    with pytest.raises(CorpusError, match="LOCATION_STREET"):
        sel.combine(table, partial)


def test_the_complementarity_cells_partition_the_types_gold():
    docs, rules, tagger = _two_type_corpus()
    table = sel.build_table(docs, rules, tagger)
    for phi_type, row in table.items():
        if not row.n_gold:
            continue
        assert sum(row.complementarity.values()) == row.n_gold, phi_type


def test_sparse_is_the_scorers_threshold_and_not_a_second_one():
    docs, rules, tagger = _two_type_corpus()
    table = sel.build_table(docs, rules, tagger)
    # NAME has 6 gold over two documents, DATE has 4 — both at or below SPARSE_MAX = 8.
    assert scorer.SPARSE_MAX == 8
    assert table["NAME"].sparse and table["DATE"].sparse
    assert not table["AGE"].sparse, "a type with no gold is not 'sparse', it is absent"


# ─── RT-Sel ─────────────────────────────────────────────────────────────────

def test_delta_is_read_from_the_termination_block_and_is_per_corpus():
    """§3's δ, unchanged. A second δ for §12 would be a second noise floor."""
    assert sel.delta_for(5254) == pytest.approx(max(0.005, 26 / 5254))
    assert sel.delta_for(319) == pytest.approx(26 / 319)
    assert sel.delta_for(100000) == pytest.approx(0.005), "the floor binds"
    with pytest.raises(CorpusError, match="no δ|has no"):
        sel.delta_for(0)


def test_the_selector_never_chooses_a_type_policy_that_blows_its_budget():
    docs, rules, tagger = _two_type_corpus()
    table = sel.build_table(docs, rules, tagger)
    delta = sel.delta_for(sum(r.n_gold for r in table.values()))
    chosen = sel.select(table, delta=delta)
    n_total = sum(r.n_gold for r in table.values())
    for phi_type, choice in chosen.items():
        row = table[phi_type]
        if not row.n_gold:
            assert choice == pol.rt_baseline()
            continue
        budget = (row.candidates["UNION@0.5"].L_full
                  + delta * row.n_gold / n_total)
        assert (row.candidates[str(choice)].L_full or 0.0) <= budget + 1e-9


def test_the_selector_picks_the_highest_accuracy_among_the_eligible():
    docs, rules, tagger = _two_type_corpus()
    table = sel.build_table(docs, rules, tagger)
    delta = sel.delta_for(sum(r.n_gold for r in table.values()))
    chosen = sel.select(table, delta=delta)
    n_total = sum(r.n_gold for r in table.values())
    for phi_type, choice in chosen.items():
        row = table[phi_type]
        if not row.n_gold:
            continue
        budget = (row.candidates["UNION@0.5"].L_full
                  + delta * row.n_gold / n_total)
        eligible = [c for c in pol.candidates()
                    if (row.candidates[str(c)].L_full or 0.0) <= budget + 1e-12]
        best = max(row.candidates[str(c)].A for c in eligible)
        assert row.candidates[str(choice)].A == pytest.approx(best), phi_type


def test_ties_break_union_then_r_then_t_then_agree_and_then_lowest_theta():
    """The declared order, exercised on a table where every candidate scores alike.

    With no gold and no predictions every candidate has A = 0 and L_full = None, so the
    tie-break is the only thing deciding — which is exactly when it has to be the declared
    one rather than dictionary order.
    """
    docs = [Document(doc_id="d0", corpus_id="es-meddocan", text="x" * 50,
                     spans=[Span(start=0, end=5, surface="xxxxx", subtype="S",
                                 phi_type="NAME")], split="dev")]
    table = sel.build_table(docs, {"d0": []}, {"d0": []})
    chosen = sel.select(table, delta=1.0)
    assert str(chosen["NAME"]) == "UNION@0.5", (
        "all candidates tie at A = 0, so the declared order decides")


def test_a_type_with_no_dev_gold_is_fixed_at_the_baseline_and_not_selected_over():
    """DESIGN §12.2: nothing to select on, and selecting anyway is selecting on zero
    observations. Fixed before any chooser sees it, so RT-Sel and the Arbiter solve the
    same restricted problem."""
    docs, rules, tagger = _two_type_corpus()
    table = sel.build_table(docs, rules, tagger)
    chosen = sel.select(table, delta=sel.delta_for(10))
    for phi_type in canonical_types():
        if not table[phi_type].n_gold:
            assert chosen[phi_type] == pol.rt_baseline(), phi_type


def test_combine_refuses_a_combination_missing_a_type_with_gold():
    docs, rules, tagger = _two_type_corpus()
    table = sel.build_table(docs, rules, tagger)
    with pytest.raises(CorpusError, match="Refused rather than defaulted"):
        sel.combine(table, {"NAME": pol.rt_baseline()})


def test_the_table_record_carries_no_text():
    docs, rules, tagger = _two_type_corpus()
    record = sel.table_record(sel.build_table(docs, rules, tagger))
    blob = repr(record)
    assert "xxxxx" not in blob and "surface" not in blob
    assert set(record) == set(canonical_types())
