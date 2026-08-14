"""The pre-registered loop-termination rule: per-corpus δ, k consecutive, and a ceiling.

DESIGN §3 fixes the rule before `port-loop`'s first call, and this module is that rule as
code. Nothing here runs an iteration, calls a model, or reads a rule file — it is given a
sequence of dev leak rates and answers whether the arm should stop and why.

**Why this is its own module and not part of the loop driver.** The rule decides how many
iterations an arm runs, hence its cost, hence whether the rung clears §11.3's standard
against a baseline whose number is already known. A stopping rule that lives inside the
thing it stops is a stopping rule that gets adjusted while the loop is being debugged, and
the adjustment looks like ordinary iteration on the driver. Separated, δ and k are
importable, testable and mutable only by editing a file whose whole content is the
pre-registration — which is what makes "the values were fixed in advance" checkable rather
than asserted. The driver will call `should_stop()` and obey it; it will not compute it.

**Three facts the rule turns on, and where each comes from.**

  - `n_dev` — the dev fold's in-scope canonical gold count, read from
    `splits/{corpus}.json` (`folds.dev.n_spans_in_scope`). Read, never passed: a caller
    that supplied it could supply a different one, and δ would then be a function of the
    call site.
  - `delta_spans` and `delta_floor` — the two pre-registered constants, from
    `config/naming.yaml`. δ is derived from them and `n_dev`, and is not stored anywhere.
  - `k` and `ceiling` — also from the config, and corpus-independent (§3: only δ has a
    fold size in its denominator).

**The difference rule, and the direction that matters.** The criterion is on the
iteration-to-iteration *first difference* of the dev leak rate, not on its level (§3
records the restatement as a level rule as a near-miss with opposite failure modes). Leak
rate is a quantity where lower is better, so an *improvement* is a decrease:
`improvement[i] = leak[i-1] - leak[i]`. A rise in leak rate is a negative improvement and
therefore below δ — an iteration that made things worse counts toward stopping, which is
correct and worth stating, because the alternative (absolute difference) would let an arm
oscillating badly run to the ceiling while looking productive.

**A ceiling stop is not convergence, and this module is where that is enforced.**
`should_stop()` returns the reason, and the ceiling branch cannot return `converged`
because the convergence test is evaluated first and independently: `reason` is
`ceiling` exactly when the cap is reached without k consecutive below-δ iterations. §3
requires the two to be distinguishable in `metrics.json`; `Termination.converged` is a
property derived from the reason rather than a second field a caller could set, so there
is no state in which a record says `ceiling` and `converged: true`.

**`pending()` is the same rule with one argument still missing, and it is why the driver
does not have to know the future** (2026-08-14, DESIGN §5.5). A round's `termination` block
describes that round, so it needs that round's dev leak rate — which does not exist until
the fold has been scored, by which time the writer is already running. So the driver
assembles everything it *does* know (the corpus, and every earlier round's rate) into a
`PendingTermination`, and `run_fold` completes it with the rate it just measured, exactly as
it completes the cost block with the detection seconds only it timed. `resolve()` calls
`should_stop()` and nothing else, so the rule still has one implementation and the writer
still holds no history: what crosses the boundary is one number in one direction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from . import split
from .corpora.base import (
    CorpusError, check_termination_reason, termination_params,
)

#: The fold δ is computed over. Not a parameter: §3's rule names the *dev* fold, and rule
#: development, agent iteration and checkpoint selection are dev-only (CLAUDE.md). A
#: `fold=` argument would be the one way this module could be pointed at `test`.
DELTA_FOLD = "dev"

#: The three reasons, spelled once here so the module's own branches cannot drift from the
#: vocabulary. Each is checked against `config/naming.yaml` at import-independent call
#: time by `check_termination_reason`, so a value renamed in the config fails loudly
#: rather than being written unvalidated.
CONVERGED = "converged"
CEILING = "ceiling"
NOT_APPLICABLE = "not_applicable"


class TerminationError(CorpusError):
    """A termination question that cannot be answered as asked.

    Subclasses `CorpusError` so the existing "stop and tell a human" handling applies. Every
    case is a caller and a pre-registered rule disagreeing, and there is no degraded answer
    that is a defensible substitute — an arm that cannot evaluate its stopping rule must not
    fall through to running one more iteration.
    """


def n_dev(corpus: str) -> int:
    """The dev fold's in-scope canonical gold span count, from `splits/{corpus}.json`.

    `split.read()` validates the schema, so a stale or malformed split file fails here
    rather than yielding a δ computed from a number that means something else. The field is
    `n_spans_in_scope` and not `n_spans`: §9.1's excluded spans are flagged and kept, and
    they are not scored, so a δ derived from the larger count would be a threshold on a
    denominator no metric uses.
    """
    record = split.read(corpus)
    folds = record.get("folds")
    if not isinstance(folds, dict) or DELTA_FOLD not in folds:
        raise TerminationError(
            f"splits/{corpus}.json has no {DELTA_FOLD!r} fold, so δ cannot be computed. "
            "The termination rule is defined on the dev fold (DESIGN §3, CLAUDE.md)."
        )
    value = folds[DELTA_FOLD].get("n_spans_in_scope")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise TerminationError(
            f"splits/{corpus}.json: folds.{DELTA_FOLD}.n_spans_in_scope is {value!r}, "
            "which cannot be a fold size. δ is a count divided by this number "
            "(DESIGN §3)."
        )
    return value


def delta(corpus: str) -> float:
    """δ for this corpus: `max(delta_floor, delta_spans / n_dev)`. DESIGN §3.

    **The invariant is the span count and the rate is derived.** Every ratio-branch corpus
    gets a δ that is exactly `delta_spans` worth of gold on its own dev fold, which is the
    point of the formula rather than an accident: the standard held constant across corpora
    is how much gold has to move for an iteration to count as productive, and that is a
    count. A fold five times smaller must show a rate five times larger to represent the
    same amount of found PHI, so a single fixed rate is the quantity that would wobble —
    strict on the large fold and no standard at all on the small one.

    **The floor branch binds above `delta_spans / delta_floor`** (5,200 spans as
    pre-registered), and it is there because below the noise-floor argument there is
    nothing left to justify a smaller distance.

    Computed rather than stored, and computed from the split file rather than from an
    argument, so that the number cannot be chosen alongside a fold fraction. §3's table of
    de-grascco rows is this function evaluated in advance at four candidate fractions
    precisely so no fraction can later be picked for the δ it produces.
    """
    params = termination_params()
    return max(params["delta_floor"], params["delta_spans"] / n_dev(corpus))


def improvements(leak_rates: Sequence[float]) -> list[float]:
    """First differences, oriented so that a positive value is an improvement.

    Leak rate is lower-is-better, so `improvement[i] = leak[i-1] - leak[i]`. An iteration
    that raised the leak rate yields a negative improvement, which is below δ and counts
    toward stopping — deliberately, because an absolute difference would let an arm
    oscillating between two bad states look productive indefinitely.

    Returns one fewer element than it is given: the first iteration has no predecessor and
    so has no improvement, which is why `should_stop` cannot converge before iteration
    `k + 1`.
    """
    for i, rate in enumerate(leak_rates):
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            raise TerminationError(
                f"leak_rates[{i}] is {type(rate).__name__}, not a number. The termination "
                "rule is a threshold on differences of the dev leak rate (DESIGN §3)."
            )
        if not 0.0 <= rate <= 1.0:
            raise TerminationError(
                f"leak_rates[{i}] = {rate!r} is outside [0, 1]. §9.3's leak rate is a "
                "proportion of in-scope gold; a value outside the interval means a "
                "percentage was passed where a fraction was expected, which would make "
                "every difference 100× too large and stop nothing."
            )
    return [float(leak_rates[i - 1] - leak_rates[i]) for i in range(1, len(leak_rates))]


@dataclass(frozen=True, slots=True)
class Termination:
    """The verdict on one arm's iteration history, and the record written beside it.

    Frozen because the verdict must not be adjustable after the fact by the code that acted
    on it, and because `converged` is derived rather than stored — see below.

    `reason` is `None` while the arm should keep going. It is one of the three vocabulary
    values once it stops, and `stop` is `reason is not None` rather than an independent
    flag, so there is no state in which an arm is told to stop for no recorded reason.
    """

    #: `None` until the arm stops; then a `termination_reason` value from naming.yaml.
    reason: str | None
    #: How many iterations were run — i.e. how many leak rates were observed.
    iterations: int
    #: δ as computed for this corpus, recorded because §3 requires the threshold to travel
    #: with the result: a run whose δ nobody can recover is a run whose stopping point
    #: cannot be checked.
    delta: float
    #: The pre-registered constants in force, so a later edit to naming.yaml is detectable
    #: in a published file rather than silent.
    delta_spans: int
    delta_floor: float
    k: int
    ceiling: int
    #: The dev fold size δ was derived from. Beside δ rather than instead of it: the rate is
    #: what the rule compares against and the count is what makes it comparable across
    #: corpora, and neither implies the other without this number.
    n_dev: int
    #: The first differences, oriented as improvements. Recorded so the stop is auditable
    #: from the file — §3's difference-versus-level distinction is invisible in a leak rate
    #: alone.
    improvements: tuple[float, ...]

    @property
    def stop(self) -> bool:
        return self.reason is not None

    @property
    def converged(self) -> bool:
        """True only for `converged`. DESIGN §3's prohibition, as one line of code.

        A property and not a field, which is the whole mechanism: a stored boolean is a
        second place the ending is recorded, and the two places can disagree. §3 says a
        ceiling-terminated run may not be described as converged, so there must be no way
        to construct a record that says `ceiling` and `converged: true` — not a validator
        that rejects it, which would imply the state exists and is caught, but no such
        state at all.
        """
        return self.reason == CONVERGED

    def record(self) -> dict:
        """The `termination` block for metrics.json. Validated on the way out.

        Every reason is checked against naming.yaml here rather than at construction, so a
        vocabulary value renamed in the config is caught at the point it would have been
        written to a published file. `converged` is included as a derived convenience for
        readers and is computed from `reason` at write time; it cannot be set.
        """
        return {
            "reason": check_termination_reason(self.reason) if self.reason else None,
            "converged": self.converged,
            "iterations": self.iterations,
            "delta": self.delta,
            "delta_spans": self.delta_spans,
            "delta_floor": self.delta_floor,
            "k": self.k,
            "ceiling": self.ceiling,
            "n_dev": self.n_dev,
            "improvements": list(self.improvements),
        }


def should_stop(corpus: str, leak_rates: Sequence[float]) -> Termination:
    """Whether an arm that has observed `leak_rates` should stop, and why. DESIGN §3.

    `leak_rates[0]` is iteration 1's dev leak rate, `leak_rates[1]` iteration 2's, and so
    on — the rate *after* each iteration's rules were scored. An empty sequence is an arm
    that has not run, which is neither converged nor at the ceiling.

    **The convergence test is evaluated first and independently of the cap**, which is what
    makes the two endings distinguishable rather than ordered by luck. An arm whose k-th
    consecutive below-δ iteration happens to be its 8th is `converged`: it satisfied the
    test, and the cap merely also became true. An arm that reaches 8 with its last two
    improvements above δ is `ceiling`, and §3 forbids calling it converged. Checking the
    cap first would reclassify the former as the latter and understate the rule's
    effectiveness; checking convergence first and never overriding it is the honest
    ordering, because the convergence test is a statement about the arm and the cap is a
    statement about the budget.

    Convergence needs `k` improvements, hence `k + 1` iterations — the first iteration has
    no predecessor. So no arm can converge before iteration 3 at k = 2, which is a
    consequence of the difference rule rather than a separate minimum.
    """
    params = termination_params()
    k = params["k"]
    ceiling = params["ceiling"]
    size = n_dev(corpus)
    d = max(params["delta_floor"], params["delta_spans"] / size)

    gains = improvements(leak_rates)
    iterations = len(leak_rates)
    if iterations > ceiling:
        raise TerminationError(
            f"{iterations} leak rates were passed but the pre-registered ceiling is "
            f"{ceiling} iterations (DESIGN §3). An arm that ran past the cap has already "
            "violated the stopping rule, and reporting a reason for it would describe a "
            "run the pre-registration does not cover."
        )

    reason = None
    if len(gains) >= k and all(g < d for g in gains[-k:]):
        reason = CONVERGED
    elif iterations >= ceiling:
        reason = CEILING

    return Termination(
        reason=reason,
        iterations=iterations,
        delta=d,
        delta_spans=params["delta_spans"],
        delta_floor=params["delta_floor"],
        k=k,
        ceiling=ceiling,
        n_dev=size,
        improvements=tuple(gains),
    )


def not_applicable(corpus: str) -> Termination:
    """The record for an arm that does not iterate — `R`, and the `port-oneshot` rungs.

    A function rather than letting such an arm omit the block, for the reason the cost
    block writes zeros and `model_id` writes `none`: a field some arms carry and others
    lack cannot be compared across arms, and an absent block cannot be told from a writer
    that had no such field. This arm ran one scoring pass and no stopping rule applied to
    it, and that is a measurement.

    δ is still computed and recorded. It costs a split-file read and it makes the
    non-iterating arms' files say what threshold *would* have applied — which is what lets
    a reader compare `port-oneshot`'s single leak rate against `port-loop`'s stopping point
    without going to another file for the number.

    `iterations` is 1 and not 0: the arm produced one scored rule set. `improvements` is
    empty, because one observation has no first difference — the same reason `should_stop`
    cannot converge at iteration 1.
    """
    params = termination_params()
    size = n_dev(corpus)
    return Termination(
        reason=NOT_APPLICABLE,
        iterations=1,
        delta=max(params["delta_floor"], params["delta_spans"] / size),
        delta_spans=params["delta_spans"],
        delta_floor=params["delta_floor"],
        k=params["k"],
        ceiling=params["ceiling"],
        n_dev=size,
        improvements=(),
    )


@dataclass(frozen=True, slots=True)
class PendingTermination:
    """The stopping rule with every argument but the current round's leak rate.

    **What this type exists to prevent.** A round's `termination` block is a statement about
    *that* round, so it needs that round's dev leak rate — and the driver cannot have it,
    because it comes from the scoring pass the driver is about to ask for. The three ways
    around that are each refused elsewhere: scoring twice gives two passes that could differ
    with neither file looking wrong (DESIGN §5.5), patching `metrics.json` after the fact
    makes a published file have two writers (§5.5's one-writer rule), and letting the writer
    call the rule itself puts a pre-registered decision inside the thing it decides about
    (§3, and this module's own docstring on why it is separate).

    **So the missing argument travels instead of the answer.** The driver holds the history —
    every earlier round's rate, read from the rounds' own `metrics.json` files — and passes
    it here; `run_fold` appends the rate it just measured and calls `resolve()`. That is the
    cost block's arrangement exactly: the caller assembles what it knows and the writer
    completes it with the one quantity only it has (there, `elapsed`). The rule stays in this
    module, the history stays with the driver, and what crosses the boundary is one float.

    Frozen for `Termination`'s reason, and it holds no verdict at all: there is nothing here
    to adjust, because `reason` does not exist until `resolve()` computes it.
    """

    #: The corpus δ is derived for. Carried rather than re-derived at the writer, so the
    #: verdict is about the fold the driver ran.
    corpus: str
    #: Rounds 1..n−1's dev leak rates, in round order. **Not including this round's** — that
    #: is `resolve()`'s argument, and the whole point of the type. Empty at round 1, which is
    #: an arm whose first round cannot stop for a difference it has not made yet.
    previous_leak_rates: tuple[float, ...]

    def resolve(self, leak_rate: float) -> Termination:
        """The verdict, once the round being scored has a leak rate. Calls `should_stop`.

        One line of substance on purpose. Any test, threshold or branch added here would be
        a second implementation of §3's rule reachable only through the writer, which is the
        arrangement this type was built to avoid rather than a shortcut it enables.
        """
        return should_stop(self.corpus, (*self.previous_leak_rates, leak_rate))
