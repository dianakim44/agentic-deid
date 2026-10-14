"""Apply a merge policy. DESIGN §12.2.

One policy per (corpus, type), from `config/naming.yaml`'s `merge_policy`, with a
threshold θ from `merge_theta`. The unit of application is the type: a document's spans are
partitioned by `phi_type` and each partition gets its type's policy.

**θ filters the tagger and only the tagger.** It is a confidence threshold and the rules do
not emit a confidence — a rule either matched or it did not (DESIGN §3's three rule
syntaxes). `R` therefore carries `theta = None`, and that is a refusal rather than a
default: a θ recorded against `R` is a number with no referent, and the next reader would
reasonably assume it had been applied to something.

**`AGREE` keeps both sides of an agreement, not one.** DESIGN §12.2: picking a winner
between two differently bounded predictions is what §9.3 forbids the *scorer* from doing,
and a merge policy that does it has only moved the violation. Keeping both leaves the
choice to the scorer's 1:1 assignment, and leaves the leak rate's prediction union — which
is a union of extents — exactly where it was.

No message here carries a surface. Spans are reported by index, offset and type.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from ..corpora.base import CorpusError, Span, canonical_types, family_of, naming

#: The one policy that ignores the tagger, and so the one that may not carry a θ.
NO_THETA = "R"


def policy_names() -> list[str]:
    value = naming().get("merge_policy")
    if not isinstance(value, dict) or not value:
        raise CorpusError(
            "config/naming.yaml has no `merge_policy` mapping. It is the closed "
            "vocabulary of DESIGN §12.2's policy space, it lands in every v24 "
            "metrics file, and CLAUDE.md keeps such values out of the modules."
        )
    return sorted(value)


def theta_grid() -> list[float]:
    value = naming().get("merge_theta")
    if not isinstance(value, list) or not value:
        raise CorpusError(
            "config/naming.yaml has no `merge_theta` list. DESIGN §12.2 pre-registered "
            "the grid, and a threshold chosen outside it is a continuous optimisation "
            "the δ constraint cannot be claimed to control."
        )
    bad = [v for v in value if not isinstance(v, (int, float)) or isinstance(v, bool)
           or not 0.0 <= float(v) <= 1.0]
    if bad:
        raise CorpusError(
            f"config/naming.yaml `merge_theta` has {len(bad)} entr(ies) that are not "
            "a number in [0, 1]. θ is a probability threshold on the tagger's span "
            "confidence."
        )
    return [float(v) for v in value]


@dataclass(frozen=True, slots=True)
class Choice:
    """One (policy, θ) pair — the value a single type takes."""

    policy: str
    theta: float | None

    def __post_init__(self) -> None:
        if self.policy not in policy_names():
            raise CorpusError(
                f"{self.policy!r} is not a merge policy in config/naming.yaml "
                f"(have: {policy_names()}). Add it there before a module writes it."
            )
        if self.policy == NO_THETA:
            if self.theta is not None:
                raise CorpusError(
                    f"policy {NO_THETA!r} was given theta={self.theta!r}. "
                    f"{NO_THETA} ignores the tagger, so a threshold on the tagger's "
                    "confidence has nothing to apply to — and a recorded θ would read "
                    "as one that had been applied. Pass None."
                )
        elif self.theta is None:
            raise CorpusError(
                f"policy {self.policy!r} needs a theta; got None. Only {NO_THETA!r} "
                "goes without one."
            )
        elif float(self.theta) not in theta_grid():
            raise CorpusError(
                f"theta={self.theta!r} is not in the pre-registered grid "
                f"{theta_grid()} (DESIGN §12.2). The grid is fixed: a threshold "
                "tuned off it makes the selection a continuous optimisation over dev."
            )

    def __str__(self) -> str:
        return self.policy if self.theta is None else f"{self.policy}@{self.theta}"


#: The RT arm: every type at UNION@0.5 (DESIGN §12.2). Built from the vocabulary rather
#: than written as a literal, so that a change to either name is a change here too.
def rt_baseline() -> Choice:
    return Choice(policy="UNION", theta=0.5)


def candidates() -> list[Choice]:
    """The whole policy space: every policy × every applicable θ.

    `R` appears once because it has no θ. So the space is 1 + 3 × 3 = 10 points per type,
    not 4 × 3 — and that asymmetry is the reason this is built rather than written out.
    """
    out = [Choice(policy=NO_THETA, theta=None)]
    for policy in policy_names():
        if policy == NO_THETA:
            continue
        for theta in theta_grid():
            out.append(Choice(policy=policy, theta=theta))
    return out


def _is_tagger(span: Span) -> bool:
    """Which family a span came from, read off its `layer`, never its detector name.

    `family_of` raises for an undeclared layer, which is the behaviour wanted: a span
    whose layer is not in `layer_families` would otherwise be silently treated as a rules
    span and counted on the wrong side of every complementarity cell (DESIGN §3).
    """
    if span.layer is None:
        raise CorpusError(
            f"a span at [{span.start}, {span.end}) carries no layer. Merge policies "
            "partition predictions by family and the family comes from the layer "
            "(DESIGN §3) — a gold span or an unprovenanced prediction has no side to "
            "be on."
        )
    return family_of(span.layer) == "tagger"


def _overlaps(a: Span, b: Span) -> bool:
    return a.start < b.end and b.start < a.end


def apply_one(choice: Choice, rules: Sequence[Span],
              tagger: Sequence[Span]) -> list[Span]:
    """Apply one policy to one type's spans. Inputs are already type-filtered.

    Returns spans drawn from the inputs — the same objects, not copies — because a merge
    policy selects and does not rewrite. A policy that produced new spans could change a
    boundary, and a changed boundary is a detection decision made by the merge layer.
    """
    kept_tagger = [s for s in tagger
                   if choice.theta is None or (s.score is not None
                                               and s.score >= choice.theta)]
    if choice.policy == "R":
        return list(rules)
    if choice.policy == "T":
        return kept_tagger
    if choice.policy == "UNION":
        return list(rules) + kept_tagger
    if choice.policy == "AGREE":
        return ([r for r in rules if any(_overlaps(r, t) for t in kept_tagger)]
                + [t for t in kept_tagger if any(_overlaps(t, r) for r in rules)])
    raise CorpusError(
        f"{choice.policy!r} is in the merge_policy vocabulary and this function has "
        "no branch for it. A policy nothing applies is a policy that silently drops "
        "every span of every type it governs."
    )


def apply_map(spans: Iterable[Span], policies: dict[str, Choice]) -> list[Span]:
    """Apply a whole per-type policy map to one document's predictions.

    `policies` must cover every type that appears in `spans`. A missing type is refused
    rather than defaulted: the default that suggests itself is `UNION@0.5`, which is the
    RT baseline, so a forgotten type would silently make the arm partly RT and the
    difference would be invisible in the result. DESIGN §12.2 fixes the no-gold types at
    the baseline *explicitly and before the Arbiter is called*, which is a decision with a
    record; this would be the same value with none.
    """
    by_type: dict[str, tuple[list[Span], list[Span]]] = {}
    for span in spans:
        if span.phi_type is None:
            raise CorpusError(
                f"a prediction at [{span.start}, {span.end}) has no phi_type. "
                "Merge policies are per type (DESIGN §12.2)."
            )
        rules, tagger = by_type.setdefault(span.phi_type, ([], []))
        (tagger if _is_tagger(span) else rules).append(span)

    missing = sorted(set(by_type) - set(policies))
    if missing:
        raise CorpusError(
            f"no merge policy for type(s) {missing}, which this document has "
            "predictions for. Refused rather than defaulted: the obvious default is "
            "UNION@0.5, which is the RT baseline, so a forgotten type would make this "
            "arm partly RT with nothing in the record saying so."
        )
    out: list[Span] = []
    for phi_type, (rules, tagger) in by_type.items():
        out.extend(apply_one(policies[phi_type], rules, tagger))
    return out


def baseline_map() -> dict[str, Choice]:
    """Every canonical type at `UNION@0.5` — the RT arm (DESIGN §12.2)."""
    return {phi_type: rt_baseline() for phi_type in canonical_types()}


def as_record(policies: dict[str, Choice]) -> dict[str, dict]:
    """The committable form: `{type: {policy, theta}}`, sorted."""
    return {phi_type: {"policy": choice.policy, "theta": choice.theta}
            for phi_type, choice in sorted(policies.items())}


def from_record(record: dict) -> dict[str, Choice]:
    """The inverse. Validates through `Choice`, so a bad file fails on read."""
    out: dict[str, Choice] = {}
    for phi_type, entry in record.items():
        if phi_type not in canonical_types():
            raise CorpusError(
                f"{phi_type!r} is not a phi_type in config/naming.yaml "
                f"(have: {canonical_types()})"
            )
        out[phi_type] = Choice(policy=entry["policy"], theta=entry.get("theta"))
    return out
