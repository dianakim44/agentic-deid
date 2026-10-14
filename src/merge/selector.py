"""The dev aggregate table, and RT-Sel's deterministic solve of it. DESIGN §12.2–12.3.

Two things live here because they are the same computation read twice: the table of what
each candidate policy does to each type, and the arithmetic that picks the best combination
from it. The Arbiter reads the same table (`src/llm/arbiter.py`) and picks differently,
which is what makes RT-Arb and RT-Sel comparable at all.

**Why a per-type table is exact and not an approximation.** The scorer partitions by type
at every point that matters: `assign()` skips any (gold, pred) pair whose types differ, and
`coverage()` unions predictions per type before asking whether a gold span is hidden. So
type `t`'s TP/FP/FN and leaked count depend on type `t`'s predictions and on nothing else,
and a combination's whole-corpus numbers are sums over the per-type rows. No combination
has to be re-scored, which is what makes a 10-candidates-per-type space searchable without
10^10 scorings.

That separability is a property of the scorer and could stop holding. `tests/test_merge_
selector.py` asserts it directly — it scores a two-type combination through `scorer.score()`
and checks the whole-corpus numbers against the sums this module produces.

No message or record here carries a surface. Types, counts, rates and offsets only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..corpora.base import CorpusError, canonical_types
from ..eval import scorer
from . import policy as pol


@dataclass(slots=True)
class Counts:
    """What one candidate does to one type, in both modes."""

    n_gold: int
    tp: int
    fp: int
    fn: int
    leaked_full: int
    leaked_rel: int
    n_pred: int

    @property
    def A(self) -> float:
        """TP / (TP + FP + FN) — the guideline's accuracy (DESIGN §12.5)."""
        denominator = self.tp + self.fp + self.fn
        return self.tp / denominator if denominator else 0.0

    @property
    def L_full(self) -> float | None:
        return self.leaked_full / self.n_gold if self.n_gold else None

    @property
    def L_rel(self) -> float | None:
        return self.leaked_rel / self.n_gold if self.n_gold else None

    def as_record(self) -> dict:
        return {
            "n_gold": self.n_gold, "tp": self.tp, "fp": self.fp, "fn": self.fn,
            "n_pred": self.n_pred,
            "leaked_full": self.leaked_full, "leaked_rel": self.leaked_rel,
            "L_full": self.L_full, "L_rel": self.L_rel, "A": self.A,
        }


@dataclass(slots=True)
class TypeRow:
    """One type's gold count, sparsity, candidate rows and complementarity."""

    phi_type: str
    n_gold: int
    candidates: dict[str, Counts] = field(default_factory=dict)
    #: The four-cell breakdown, measured at the RT baseline. It describes how the two
    #: *detectors* overlap on this type and is not a property of a candidate policy, so
    #: one reading at `UNION@0.5` is the honest place to take it — under `AGREE` a
    #: `rules_only` gold span is simply discarded, and reporting that as "rules only
    #: fell" would be reporting the policy back to whoever is choosing it.
    complementarity: dict[str, int] = field(default_factory=dict)

    @property
    def sparse(self) -> bool:
        return 0 < self.n_gold <= scorer.SPARSE_MAX


def _marks(spans, *, gold: bool) -> list[scorer.Mark]:
    return [scorer.Mark(start=s.start, end=s.end, phi_type=s.phi_type,
                        layer=None if gold else s.layer,
                        rule_id=None if gold else s.rule_id,
                        span_index=i)
            for i, s in enumerate(spans)]


def _count(gold: list[scorer.Mark], pred: list[scorer.Mark]) -> Counts:
    distinct, _ = scorer.dedupe(pred)
    matched, fn, fp = scorer.assign(gold, distinct, scorer.RELAXED)
    cov_full = scorer.coverage(gold, pred, scorer.FULLY_COVERED)
    cov_rel = scorer.coverage(gold, pred, scorer.RELAXED)
    return Counts(
        n_gold=len(gold),
        tp=len(matched), fp=len(fp), fn=len(fn),
        leaked_full=sum(1 for c in cov_full if not c),
        leaked_rel=sum(1 for c in cov_rel if not c),
        n_pred=len(distinct),
    )


def build_table(docs, rules: dict[str, list], tagger: dict[str, list]) -> dict[str, TypeRow]:
    """The per-type candidate table for one corpus's dev fold.

    `rules` and `tagger` are `{doc_id: [Span]}` — the two detectors' raw output, with no
    threshold applied. θ is applied here, inside the candidate loop, because θ is part of
    the candidate and pre-filtering would make the table a table of one θ.
    """
    types = canonical_types()
    rows = {t: TypeRow(phi_type=t, n_gold=0) for t in types}

    gold_by_type: dict[str, list[scorer.Mark]] = {t: [] for t in types}
    rules_by_type: dict[str, dict[str, list]] = {t: {} for t in types}
    tagger_by_type: dict[str, dict[str, list]] = {t: {} for t in types}

    for doc in docs:
        for span in doc.in_scope_spans:
            gold_by_type[span.phi_type].append(
                scorer.Mark(start=span.start, end=span.end, phi_type=span.phi_type))
        for source, holder in ((rules.get(doc.doc_id, ()), rules_by_type),
                               (tagger.get(doc.doc_id, ()), tagger_by_type)):
            for span in source:
                holder[span.phi_type].setdefault(doc.doc_id, []).append(span)

    for phi_type in types:
        rows[phi_type].n_gold = len(gold_by_type[phi_type])

    doc_ids = [d.doc_id for d in docs]
    for phi_type in types:
        gold_per_doc = {d.doc_id: [] for d in docs}
        for doc in docs:
            gold_per_doc[doc.doc_id] = _marks(
                [s for s in doc.in_scope_spans if s.phi_type == phi_type], gold=True)
        for choice in pol.candidates():
            total = Counts(0, 0, 0, 0, 0, 0, 0)
            for doc_id in doc_ids:
                kept = pol.apply_one(
                    choice,
                    rules_by_type[phi_type].get(doc_id, []),
                    tagger_by_type[phi_type].get(doc_id, []),
                )
                counts = _count(gold_per_doc[doc_id], _marks(kept, gold=False))
                total = Counts(
                    n_gold=total.n_gold + counts.n_gold,
                    tp=total.tp + counts.tp, fp=total.fp + counts.fp,
                    fn=total.fn + counts.fn,
                    leaked_full=total.leaked_full + counts.leaked_full,
                    leaked_rel=total.leaked_rel + counts.leaked_rel,
                    n_pred=total.n_pred + counts.n_pred,
                )
            rows[phi_type].candidates[str(choice)] = total
        rows[phi_type].complementarity = _complementarity_at_baseline(
            docs, rules_by_type[phi_type], tagger_by_type[phi_type], phi_type)
    return rows


def _complementarity_at_baseline(docs, rules: dict, tagger: dict,
                                 phi_type: str) -> dict[str, int]:
    """rules only / tagger only / both / neither for one type, at `UNION@0.5`.

    `joint_only` is folded into `both`'s sibling position rather than reported, because
    the Arbiter's frame (§1.4 of the instruction, `docs/prompts/arbiter.md` §2) names four
    cells and a fifth would be a cell the prompt does not explain. The fifth is reported
    in the manuscript's own breakdown, where there is room to say what it means — here it
    would be an unexplained number in an agent's input, which is the thing the prompt's
    whitelist exists to prevent.
    """
    baseline = pol.rt_baseline()
    out = {"rules_only": 0, "tagger_only": 0, "both": 0, "neither": 0}
    for doc in docs:
        gold = [s for s in doc.in_scope_spans if s.phi_type == phi_type]
        if not gold:
            continue
        rule_marks = _marks(rules.get(doc.doc_id, []), gold=False)
        tag_marks = _marks(
            [s for s in tagger.get(doc.doc_id, [])
             if s.score is not None and s.score >= baseline.theta], gold=False)
        gold_marks = _marks(gold, gold=True)
        by_rules = scorer.coverage(gold_marks, rule_marks, scorer.FULLY_COVERED)
        by_tagger = scorer.coverage(gold_marks, tag_marks, scorer.FULLY_COVERED)
        for r, t in zip(by_rules, by_tagger):
            if r and t:
                out["both"] += 1
            elif r:
                out["rules_only"] += 1
            elif t:
                out["tagger_only"] += 1
            else:
                out["neither"] += 1
    return out


def combine(table: dict[str, TypeRow],
            choices: dict[str, pol.Choice]) -> dict:
    """Whole-corpus numbers for a combination, by summing the per-type rows.

    Exact, not approximate — see this module's own note on why. A type absent from
    `choices` is refused for `policy.apply_map`'s reason: the obvious default is the RT
    baseline, and defaulting would make the combination partly RT silently.
    """
    missing = sorted(t for t, row in table.items()
                     if row.n_gold and t not in choices)
    if missing:
        raise CorpusError(
            f"combine() has no choice for type(s) {missing}, which have dev gold. "
            "Refused rather than defaulted to the RT baseline: a defaulted type would "
            "make this combination partly RT with nothing recording it."
        )
    tp = fp = fn = leaked_full = leaked_rel = n_gold = n_pred = 0
    for phi_type, row in table.items():
        if not row.n_gold:
            continue
        counts = row.candidates[str(choices[phi_type])]
        tp += counts.tp
        fp += counts.fp
        fn += counts.fn
        leaked_full += counts.leaked_full
        leaked_rel += counts.leaked_rel
        n_gold += counts.n_gold
        n_pred += counts.n_pred
    denominator = tp + fp + fn
    return {
        "tp": tp, "fp": fp, "fn": fn, "n_gold": n_gold, "n_pred": n_pred,
        "L_full": leaked_full / n_gold if n_gold else None,
        "L_rel": leaked_rel / n_gold if n_gold else None,
        "A": tp / denominator if denominator else 0.0,
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
    }


def delta_for(n_dev: int) -> float:
    """δ = max(delta_floor, delta_spans / n_dev), from naming.yaml's `termination`.

    §3's δ, unchanged. Read from the config rather than restated: a second δ for §12
    would be a second noise floor over the same folds, and the one place it is allowed
    to differ is `n_dev`, which is the corpus.
    """
    params = __import__("src.corpora.base", fromlist=["x"]).termination_params()
    if n_dev <= 0:
        raise CorpusError(
            f"delta_for got n_dev={n_dev}. δ is per corpus and divides by the dev "
            "gold count (DESIGN §3), so a fold with no gold has no δ."
        )
    return max(float(params["delta_floor"]), params["delta_spans"] / n_dev)


def select(table: dict[str, TypeRow], *, delta: float) -> dict[str, pol.Choice]:
    """RT-Sel. DESIGN §12.3.

    Per type, among candidates meeting that type's share of the leak budget, take the
    highest A; break ties `UNION` > `R` > `T` > `AGREE`, then lowest θ.

    **δ is apportioned by each type's share of dev gold**, δ_t = δ · n_t / n. Each type
    spending the whole budget would compose into a combination that misses the
    whole-corpus constraint by up to (number of types) × δ, which is the constraint not
    holding at all.

    A type with no dev gold is fixed at the RT baseline and is not selected over — DESIGN
    §12.2, and the point is that it is fixed *here*, before any chooser sees it, so that
    both RT-Sel and the Arbiter are solving the same restricted problem.
    """
    n_total = sum(row.n_gold for row in table.values())
    baseline = {t: pol.rt_baseline() for t in table}
    order = {name: i for i, name in enumerate(["UNION", "R", "T", "AGREE"])}
    out: dict[str, pol.Choice] = {}

    for phi_type, row in sorted(table.items()):
        if not row.n_gold:
            out[phi_type] = pol.rt_baseline()
            continue
        budget = (table[phi_type].candidates[str(baseline[phi_type])].L_full or 0.0) \
            + delta * row.n_gold / n_total
        eligible = [
            choice for choice in pol.candidates()
            if (row.candidates[str(choice)].L_full or 0.0) <= budget + 1e-12
        ]
        if not eligible:
            # Cannot happen — the baseline is in the space and meets its own budget —
            # but a silent empty max() would return the baseline by accident rather
            # than by rule, and the two are indistinguishable in the output.
            raise CorpusError(
                f"type {phi_type!r}: no candidate meets the apportioned leak budget, "
                "which is impossible because the RT baseline meets its own budget by "
                "construction. The table and the budget disagree about the baseline."
            )
        out[phi_type] = min(
            eligible,
            key=lambda c: (-row.candidates[str(c)].A, order[c.policy],
                           1.0 if c.theta is None else c.theta),
        )
    return out


def table_record(table: dict[str, TypeRow]) -> dict:
    """The committable form of the table: types, counts, rates. No text."""
    return {
        phi_type: {
            "n_gold": row.n_gold,
            "sparse": row.sparse,
            "complementarity": row.complementarity,
            "candidates": {name: counts.as_record()
                           for name, counts in sorted(row.candidates.items())},
        }
        for phi_type, row in sorted(table.items())
    }
