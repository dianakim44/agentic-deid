"""Tests for src/termination.py — the pre-registered stopping rule (DESIGN §3).

What is load-bearing here is not that the rule is *right* — §3 says plainly that δ is a
threshold chosen from a measurement floor and a cost structure, and not a measured
inflection point. What is checkable, and what this file checks, is that the code computes
the rule §3 pre-registered rather than a plausible neighbour of it. Four neighbours in
particular, each a change nobody would notice from an output file:

  1. **δ is per-corpus, and the invariant is a span count.** A δ pinned to the constant
     0.005 gives the identical number on es-meddocan — the only corpus with a split file
     today — so every test that exercises the real corpus passes under that regression.
     `test_delta_is_the_span_count_on_every_fold_size` is the one that sees it, and it works
     on synthetic fold sizes for exactly that reason.
  2. **The rule is on differences, not levels.** §3 records the level-rule restatement as a
     near-miss with opposite failure modes, so both directions get a test.
  3. **k means consecutive.** A k of 1, or a k that counts non-adjacent below-δ iterations,
     stops arms early and the metrics file looks the same.
  4. **A ceiling stop is not convergence.** §3's one prohibition. Checked here, at the
     scorer boundary in `test_scorer.py`, and made unconstructible by
     `Termination.converged` being a property — three places because the mutation
     `a_ceiling_stop_is_recorded_as_converged` has to be caught by something that reads
     the *published block*, not only by something that reads the dataclass.

Fold sizes are synthetic wherever the point is the formula, and real (`es-meddocan`,
5,254) wherever the point is the wiring to `splits/{corpus}.json`. No corpus text is
touched: the module reads a split file's counts and takes a list of floats.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora.base import (                                   # noqa: E402
    CorpusError, check_termination_reason, termination_params, termination_reasons,
)
from src.termination import (                                    # noqa: E402
    CEILING, CONVERGED, DELTA_FOLD, NOT_APPLICABLE, Termination, TerminationError,
    delta, improvements, n_dev, not_applicable, should_stop,
)

#: The corpus whose split file exists. The only one, today.
CORPUS = "es-meddocan"

#: Its dev fold's in-scope gold count, from `splits/es-meddocan.json`. Written out so a
#: split file regenerated with a different fold size fails here rather than quietly
#: changing what δ means.
N_DEV = 5254


@pytest.fixture
def params():
    return termination_params()


def fake_split(tmp_path: Path, corpus: str, n: int, *, fold: str = DELTA_FOLD) -> Path:
    """A split file holding only what `n_dev()` reads, at a synthetic fold size.

    Synthetic because the formula's whole content is how δ responds to `n_dev`, and the
    repository has one real fold size. This writes the minimum `n_dev()` looks at and
    monkeypatches the read — it deliberately does *not* build a schema-valid split file,
    because a test that had to keep one valid would be testing `src/split.py`.
    """
    path = tmp_path / f"{corpus}.json"
    path.write_text(json.dumps({"folds": {fold: {"n_spans_in_scope": n}}}),
                    encoding="utf-8")
    return path


@pytest.fixture
def at_size(monkeypatch):
    """Point `termination` at a synthetic dev fold size.

    Patches `src.split.read`, the function `n_dev()` calls, rather than patching `n_dev`
    itself — patching `n_dev` would leave the split-file plumbing untested while every δ
    test passed, which is the shape `tests/test_structure.py` exists to catch.
    """
    def set_size(n: int, *, fold: str = DELTA_FOLD, key: str = "n_spans_in_scope"):
        record = {"folds": {fold: {key: n}}} if fold else {"folds": {}}
        monkeypatch.setattr("src.termination.split.read", lambda corpus: record)
    return set_size


# ─── δ is per-corpus, and the invariant is a count ───────────────────────────

def test_n_dev_reads_the_dev_fold_of_the_real_split_file():
    """The wiring, on the one corpus that has a split file."""
    assert n_dev(CORPUS) == N_DEV


def test_delta_on_the_reference_fold_is_the_pre_registered_floor():
    """es-meddocan is above the floor's crossover, so it gets 0.005 exactly. §3's table."""
    assert delta(CORPUS) == 0.005


def test_delta_is_the_span_count_on_every_fold_size(at_size, params):
    """**The test a constant δ survives on es-meddocan and dies on here.**

    Every ratio-branch fold gets a δ worth exactly `delta_spans` of its own gold — that is
    the formula's point, not a coincidence of §3's four de-grascco rows. A regression to a
    fixed 0.005 gives the right answer at 5,254 and the wrong one at all four of these, and
    on the corpus that exists today only this test can tell the two apart.
    """
    for size in (324, 432, 519, 648, 1297):
        at_size(size)
        assert delta(CORPUS) * size == pytest.approx(params["delta_spans"])


def test_the_four_pre_registered_grascco_rows(at_size):
    """§3's table, evaluated. The rows are the formula's commitment before the split exists.

    Pinned to two decimal places in percentage points, as the table states them, so an edit
    to `delta_spans` or `delta_floor` fails against the document rather than against itself.
    """
    for size, pp in ((324, 8.02), (432, 6.02), (519, 5.01), (648, 4.01)):
        at_size(size)
        assert round(delta(CORPUS) * 100, 2) == pp


def test_the_floor_binds_above_the_crossover_and_the_ratio_below(at_size, params):
    """`delta_spans / delta_floor` = 5,200 is where the two branches meet."""
    crossover = params["delta_spans"] / params["delta_floor"]
    at_size(int(crossover) + 1000)
    assert delta(CORPUS) == params["delta_floor"]
    at_size(int(crossover) - 1000)
    assert delta(CORPUS) > params["delta_floor"]


def test_a_fold_larger_than_the_crossover_does_not_get_a_smaller_delta(at_size, params):
    """The floor's reason: below the noise-floor argument nothing justifies less. §3."""
    at_size(100_000)
    assert delta(CORPUS) == params["delta_floor"]
    # And the ratio branch would have given something far beneath one span's worth.
    assert params["delta_spans"] / 100_000 < params["delta_floor"]


def test_delta_is_never_below_one_span_of_its_own_fold(at_size):
    """The property the floor and the ratio branch jointly exist to guarantee.

    A δ beneath `1/n_dev` is meaningless — every iteration that moves one span clears it —
    and §3 derives the whole threshold upward from that floor.
    """
    for size in (200, 1297, 5254, 20_000, 100_000):
        at_size(size)
        assert delta(CORPUS) > 1 / size


# ─── the rule is on differences, not levels ─────────────────────────────────

def test_improvement_is_a_decrease_in_leak_rate():
    """Lower is better, so `improvement[i] = leak[i-1] - leak[i]`."""
    assert improvements([0.60, 0.55]) == pytest.approx([0.05])


def test_a_rising_leak_rate_is_a_negative_improvement():
    """Not an absolute difference — §3's note on an oscillating arm looking productive."""
    assert improvements([0.50, 0.55]) == pytest.approx([-0.05])


def test_an_arm_that_got_worse_twice_stops(at_size):
    """A negative improvement is below δ, so it counts toward stopping. Deliberate.

    The alternative — absolute difference — would let an arm swinging between two bad
    states run to the ceiling while every iteration looked like movement.
    """
    at_size(N_DEV)
    assert should_stop(CORPUS, [0.50, 0.55, 0.60]).reason == CONVERGED


def test_a_plateau_at_a_high_leak_rate_terminates(at_size):
    """A difference rule can stop at any level, including a bad one. §3 prefers this.

    A *level* rule ("leak rate below δ") could never satisfy itself here and would fall
    through to the budget on exactly the runs where the stopping rule matters most.
    """
    at_size(N_DEV)
    verdict = should_stop(CORPUS, [0.92, 0.9199, 0.9198])
    assert verdict.reason == CONVERGED
    assert verdict.converged


def test_a_large_improvement_at_a_low_leak_rate_does_not_terminate(at_size):
    """The mirror: δ is a distance and asks the same of 0.60→0.55 as of 0.20→0.15."""
    at_size(N_DEV)
    assert should_stop(CORPUS, [0.20, 0.15, 0.10]).reason is None


def test_the_first_iteration_has_no_improvement(at_size):
    """One observation has no first difference, so no arm converges at iteration 1."""
    at_size(N_DEV)
    assert improvements([0.5]) == []
    assert should_stop(CORPUS, [0.5]).reason is None


def test_an_arm_that_has_not_run_is_neither_converged_nor_capped(at_size):
    at_size(N_DEV)
    verdict = should_stop(CORPUS, [])
    assert verdict.reason is None
    assert not verdict.stop
    assert verdict.iterations == 0


def test_a_leak_rate_outside_the_unit_interval_is_refused(at_size):
    """A percentage passed where a fraction belongs makes every difference 100× too large."""
    at_size(N_DEV)
    with pytest.raises(TerminationError, match=r"outside \[0, 1\]"):
        should_stop(CORPUS, [56.0, 55.0])


def test_a_non_numeric_leak_rate_is_refused(at_size):
    at_size(N_DEV)
    with pytest.raises(TerminationError, match="not a number"):
        should_stop(CORPUS, [0.5, "0.4"])


# ─── k means consecutive ────────────────────────────────────────────────────

def test_one_below_delta_iteration_is_not_convergence(at_size, params):
    """k = 2's reason: a single thin draw is not evidence the arm ran out of ideas. §3."""
    at_size(N_DEV)
    assert params["k"] == 2
    assert should_stop(CORPUS, [0.60, 0.5999]).reason is None


def test_below_delta_iterations_must_be_adjacent(at_size):
    """**A k that counted non-adjacent thin iterations would stop this arm.**

    Thin, productive, thin. Under the pre-registered rule the arm keeps going, because the
    last k improvements are not all below δ — the productive iteration resets the count.
    """
    at_size(N_DEV)
    assert should_stop(CORPUS, [0.60, 0.5999, 0.55, 0.5499]).reason is None


def test_the_count_resets_after_a_productive_iteration(at_size):
    """The same property from the other side: two thin ones *after* the reset do stop it."""
    at_size(N_DEV)
    rates = [0.60, 0.5999, 0.55, 0.5499, 0.5498]
    assert should_stop(CORPUS, rates).reason == CONVERGED


def test_convergence_needs_k_plus_one_iterations(at_size, params):
    """A consequence of the difference rule, not a separate minimum. §3."""
    at_size(N_DEV)
    flat = [0.5 - 0.0001 * i for i in range(params["k"] + 1)]
    assert should_stop(CORPUS, flat[:-1]).reason is None
    assert should_stop(CORPUS, flat).reason == CONVERGED


def test_an_improvement_exactly_at_delta_does_not_count_as_below(at_size):
    """The rule is "improves by **less than** δ", so δ itself is still productive."""
    at_size(N_DEV)
    d = delta(CORPUS)
    assert should_stop(CORPUS, [0.60, 0.60 - d, 0.60 - 2 * d]).reason is None


# ─── a ceiling stop is not convergence (DESIGN §3) ──────────────────────────

def test_the_ceiling_terminates_an_arm_that_is_still_improving(at_size, params):
    at_size(N_DEV)
    steep = [0.90 - 0.05 * i for i in range(params["ceiling"])]
    verdict = should_stop(CORPUS, steep)
    assert verdict.reason == CEILING
    assert verdict.stop


def test_a_ceiling_stop_is_not_converged(at_size, params):
    """§3's prohibition, on the verdict."""
    at_size(N_DEV)
    steep = [0.90 - 0.05 * i for i in range(params["ceiling"])]
    verdict = should_stop(CORPUS, steep)
    assert verdict.reason == CEILING
    assert not verdict.converged
    assert verdict.record()["converged"] is False


def test_converged_cannot_be_set(at_size, params):
    """**The mechanism, not just the value.** `converged` is a property, so there is no
    state in which a record says `ceiling` and `converged: true` — not a validator that
    rejects it, which would imply the state exists and is caught.
    """
    at_size(N_DEV)
    verdict = should_stop(CORPUS, [0.90 - 0.05 * i for i in range(params["ceiling"])])
    with pytest.raises((AttributeError, TypeError)):
        verdict.converged = True          # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        verdict.reason = CONVERGED        # type: ignore[misc]


def test_converged_is_not_a_field_and_no_caller_can_supply_one(at_size, params):
    """**The half `test_converged_cannot_be_set` cannot see: frozen makes a *stored* boolean
    unassignable too.**

    `Termination` is frozen, so `verdict.converged = True` raises whether `converged` is a
    property or a second constructor argument — which means the test above passes on a version
    that stores it, and so does every value assertion, because `should_stop` would pass
    `reason == CONVERGED` when it built the record. The contradiction becomes constructible
    only at the constructor, by a caller assembling a `Termination` by hand, and that caller is
    the one §3's prohibition is about: `scorer.check_termination` exists because
    `write_metrics` takes a mapping and a hand-assembled block is the path around the dataclass.

    So this asserts the shape rather than a value: `converged` is not in the field list, it is
    a `property` on the class, and passing it to the constructor is refused. A stored field
    would satisfy every other test in this file.
    """
    at_size(N_DEV)
    verdict = should_stop(CORPUS, [0.90 - 0.05 * i for i in range(params["ceiling"])])
    assert "converged" not in {f.name for f in dataclasses.fields(verdict)}
    assert isinstance(inspect.getattr_static(Termination, "converged"), property)
    with pytest.raises(TypeError):
        Termination(**{**{f: getattr(verdict, f) for f in verdict.__slots__},
                       "converged": True})                      # type: ignore[call-arg]


def test_convergence_wins_when_both_are_true(at_size, params):
    """An arm whose k-th thin iteration is its last one converged; the cap merely also hit.

    §3's ordering: the convergence test is a statement about the arm and the cap is a
    statement about the budget, so checking the cap first would reclassify a real
    convergence as a budget exhaustion and understate the rule.
    """
    at_size(N_DEV)
    rates = [0.90 - 0.05 * i for i in range(params["ceiling"] - 2)]
    rates += [rates[-1] - 0.0001, rates[-1] - 0.0002]
    assert len(rates) == params["ceiling"]
    verdict = should_stop(CORPUS, rates)
    assert verdict.reason == CONVERGED
    assert verdict.converged


def test_running_past_the_ceiling_is_refused(at_size, params):
    """An arm already in violation of the rule gets no reason, because §3 does not cover it."""
    at_size(N_DEV)
    too_many = [0.5] * (params["ceiling"] + 1)
    with pytest.raises(TerminationError, match="ceiling"):
        should_stop(CORPUS, too_many)


# ─── the record ─────────────────────────────────────────────────────────────

def test_the_record_carries_the_threshold_and_the_fold_it_came_from(at_size):
    at_size(N_DEV)
    record = should_stop(CORPUS, [0.60, 0.55]).record()
    assert record["delta"] == 0.005
    assert record["n_dev"] == N_DEV
    assert record["delta_spans"] == 26
    assert record["improvements"] == pytest.approx([0.05])


def test_the_record_carries_the_constants_in_force(at_size, params):
    """So an edit to naming.yaml after a run is visible in the published file. §3, §4."""
    at_size(N_DEV)
    record = should_stop(CORPUS, [0.60, 0.55]).record()
    for key in ("delta_spans", "delta_floor", "k", "ceiling"):
        assert record[key] == params[key]


def test_a_running_arm_records_a_null_reason(at_size):
    at_size(N_DEV)
    record = should_stop(CORPUS, [0.60, 0.55]).record()
    assert record["reason"] is None
    assert record["converged"] is False


def test_every_reason_the_module_writes_is_in_the_vocabulary():
    """The three constants are naming.yaml values, not literals that resemble them."""
    for reason in (CONVERGED, CEILING, NOT_APPLICABLE):
        assert check_termination_reason(reason) == reason
    assert set(termination_reasons()) == {CONVERGED, CEILING, NOT_APPLICABLE}


def test_a_reason_outside_the_vocabulary_is_refused():
    with pytest.raises(CorpusError, match="not a termination reason"):
        check_termination_reason("converged-ish")


def test_the_record_validates_its_reason_on_the_way_out(at_size, monkeypatch):
    """Checked where it would be written to a published file, not at construction."""
    at_size(N_DEV)
    verdict = should_stop(CORPUS, [0.60, 0.55])
    forged = Termination(**{**{f: getattr(verdict, f) for f in verdict.__slots__},
                            "reason": "nearly_converged"})
    with pytest.raises(CorpusError, match="not a termination reason"):
        forged.record()


# ─── the non-iterating arms ─────────────────────────────────────────────────

def test_a_non_iterating_arm_records_that_rather_than_omitting_it():
    """`model_id_absent`'s argument and the cost block's zeros, one field over."""
    record = not_applicable(CORPUS).record()
    assert record["reason"] == NOT_APPLICABLE
    assert record["converged"] is False
    assert record["iterations"] == 1
    assert record["improvements"] == []


def test_a_non_iterating_arm_still_records_delta():
    """So a reader comparing `port-oneshot`'s leak rate to `port-loop`'s stopping point
    finds the threshold in both files rather than in one."""
    assert not_applicable(CORPUS).record()["delta"] == 0.005
    assert not_applicable(CORPUS).record()["n_dev"] == N_DEV


def test_not_applicable_is_not_converged():
    assert not not_applicable(CORPUS).converged


# ─── the split file is the only source of n_dev ──────────────────────────────

def test_n_dev_uses_the_in_scope_count_and_not_the_total():
    """§9.1's excluded spans are flagged and kept but never scored, so a δ derived from
    `n_spans` would be a threshold on a denominator no metric uses."""
    record = json.loads((ROOT / "splits" / f"{CORPUS}.json").read_text(encoding="utf-8"))
    dev = record["folds"][DELTA_FOLD]
    assert dev["n_spans"] != dev["n_spans_in_scope"]
    assert n_dev(CORPUS) == dev["n_spans_in_scope"]


def test_delta_is_computed_from_the_dev_fold_and_not_another(at_size):
    """CLAUDE.md: rule development and agent iteration are dev-only. There is no `fold=`
    argument, so no caller can point this at `test`."""
    assert DELTA_FOLD == "dev"
    at_size(N_DEV, fold="test")
    with pytest.raises(TerminationError, match="has no 'dev' fold"):
        delta(CORPUS)


def test_a_split_file_with_no_usable_fold_size_is_refused(at_size):
    for bad in (0, -1, None, "5254", True):
        at_size(bad)
        with pytest.raises(TerminationError, match="cannot be a fold size"):
            delta(CORPUS)


# ─── the pre-registered constants ───────────────────────────────────────────

def test_the_constants_are_the_pre_registered_ones(params):
    """The values DESIGN §3 fixed on 2026-08-12, before `port-loop`'s first call.

    Written out rather than read from the config on both sides, which would compare the
    file to itself. An edit to naming.yaml fails here, which is the point: §3 says editing
    the 26, the floor, or a corpus's δ after a run makes the rungs incomparable.
    """
    assert params == {"delta_spans": 26, "delta_floor": 0.005, "k": 2, "ceiling": 8}


def test_delta_spans_is_the_floor_evaluated_on_the_reference_fold(params):
    """26 is `0.005 × 5254` and so has es-meddocan baked in as reference fold. §3 states
    this rather than presenting the count as a first-principles derivation."""
    assert params["delta_spans"] == round(params["delta_floor"] * N_DEV)


def test_delta_is_not_stored_as_a_value_in_the_config(params):
    """A δ in the config would be a number that does not say which fold it came from."""
    assert "delta" not in params


def test_a_malformed_constant_is_refused(monkeypatch):
    """Validated on read, for `sample.config()`'s reason: a `k` of `"2"` compares unequal
    to every integer and stops nothing, in a way no caller can notice."""
    import src.corpora.base as base
    for block in ({"delta_spans": 26, "delta_floor": 0.005, "k": "2", "ceiling": 8},
                  {"delta_spans": 0, "delta_floor": 0.005, "k": 2, "ceiling": 8},
                  {"delta_spans": 26, "delta_floor": 0, "k": 2, "ceiling": 8},
                  {"delta_spans": 26, "delta_floor": 0.005, "k": 2, "ceiling": 0},
                  {"delta_spans": 26, "delta_floor": 0.005, "k": 2}):
        base.termination_params.cache_clear()
        monkeypatch.setattr(base, "naming", lambda: {"termination": block})
        with pytest.raises(CorpusError):
            base.termination_params()
    base.termination_params.cache_clear()


def test_an_extra_constant_is_refused(monkeypatch):
    """A fifth parameter would be read by nothing and would look pre-registered."""
    import src.corpora.base as base
    base.termination_params.cache_clear()
    monkeypatch.setattr(base, "naming", lambda: {"termination": {
        "delta_spans": 26, "delta_floor": 0.005, "k": 2, "ceiling": 8, "patience": 3}})
    with pytest.raises(CorpusError, match="unexpected key"):
        base.termination_params()
    base.termination_params.cache_clear()
