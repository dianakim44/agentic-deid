"""The guideline's two metrics: entity-level accuracy A, and repeat consistency C.

Reference: 「보건의료데이터 상호운용성 지원 R&D」 3내역 AI SW 성능평가 가이드라인(안),
2026.9, Ⅴ장 가명처리 SW and 붙임 1–3. DESIGN §12.5 holds the definitions and the document
itself is not in the repository.

**A is not a second headline.** The leak rate and the complementarity breakdown are the
headline (CLAUDE.md); A is what DESIGN §12.2's objective maximises *subject to* the leak
constraint, and it is reported because the funded project asks for it. This module computes
it and takes no view — picking a headline is the reporting layer's job and not the scorer's
(DESIGN §9.3).

**A is read off the existing matching, not a new one.** `scorer.assign()` is the one-to-one
greedy matching the project already uses, and the guideline's requirements — maximum
character overlap, types must agree, exact boundaries not required, no true negatives — are
exactly the `relaxed` mode of it. So A is a different *arithmetic* over the same TP/FP/FN,
and nothing here re-matches. A second matching would make A and F1 answer to different
assignments while sharing a table.

No message or value here carries a surface. Spans are (start, end, type) triples throughout,
which is the span identity the consistency metric is defined on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from ..corpora.base import CorpusError, naming
from . import scorer

#: The span identity C is computed over: start, end, type. DESIGN §12.5.
#:
#: Not the surface, and not a provenance field. Two runs that find the same extent with the
#: same type agree, whatever layer found it and whatever confidence it carried — a
#: consistency metric that counted `score` would report a tagger as inconsistent for
#: returning 0.81 where it had returned 0.80, which is not what anyone means by
#: reproducibility.
SpanKey = tuple[int, int, str]


def accuracy(tp: int, fp: int, fn: int) -> float:
    """A = TP / (TP + FP + FN).

    Zero when the denominator is zero. That case is a fold in which the detector found
    nothing and there was nothing to find, and 0.0 is the wrong answer for it in principle
    — but it is the answer the guideline's formula gives, and the alternative (`None`)
    would propagate into the RFP's O/X column as a blank that reads as a failure. The case
    does not arise on any of these five folds: every dev fold has gold.
    """
    denominator = tp + fp + fn
    return tp / denominator if denominator else 0.0


def from_mode_block(block: Mapping) -> dict:
    """A, P, R and F1 from one of `scorer.score()`'s mode blocks.

    Takes the block rather than the whole metrics record so that the caller states which
    mode it means. §12.5's matching is `relaxed` — boundaries need not be exact — and a
    function that reached into `modes[RELAXED]` itself would make that choice invisible at
    the call site, where it is the one thing worth seeing.
    """
    overall = block["overall"]
    return {
        "A": accuracy(overall["tp"], overall["fp"], overall["fn"]),
        "precision": overall["precision"],
        "recall": overall["recall"],
        "f1": overall["f1"],
        "tp": overall["tp"], "fp": overall["fp"], "fn": overall["fn"],
    }


def jaccard(left: set, right: set) -> float:
    """|A ∩ B| / |A ∪ B|, with **two empty sets scoring 1**.

    DESIGN §12.5, and it is the one definitional choice in C. A document with no PHI that
    two runs agree has no PHI is perfect agreement between them, so 1 is the answer that
    describes what happened. The alternatives are both worse and in opposite ways: 0 would
    punish the system for the thing it got right, and `None` would drop the document from
    the denominator — which silently makes C a metric over the PHI-bearing documents only,
    on corpora where the clean documents are a large and varying share.

    `0/0` is why this is a named function with a test rather than an inline expression
    (`tests/test_guideline.py`). An inline `len(a & b) / len(a | b)` raises
    ZeroDivisionError on the case the definition is most specific about.
    """
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def consistency(runs: Sequence[Mapping[str, Iterable[SpanKey]]]) -> dict:
    """C: mean over documents of the mean pairwise Jaccard across runs.

    `runs` is one mapping per run, `{doc_id: [(start, end, type), ...]}`. Every run must
    cover the same document set — a run missing a document is refused rather than treated
    as a run that predicted nothing there. The two are different claims and only one of
    them is a measurement: an absent document is a run that did not happen, and scoring it
    as an empty prediction would make a crashed run look maximally consistent with another
    crashed run (both empty, Jaccard 1).

    Two runs is the minimum. One run has no pairs, and the mean of no pairs is not 1.0 —
    it is undefined, and returning 1.0 for it would report a single run as perfectly
    reproducible.
    """
    if len(runs) < 2:
        raise CorpusError(
            f"consistency needs at least 2 runs, got {len(runs)}. With one run there "
            "are no pairs to average, and 1.0 would report a single run as perfectly "
            "reproducible — which is the claim C exists to test rather than assume."
        )
    doc_sets = [set(run) for run in runs]
    first = doc_sets[0]
    for index, docs in enumerate(doc_sets[1:], start=1):
        if docs != first:
            raise CorpusError(
                f"run 0 covers {len(first)} documents and run {index} covers "
                f"{len(docs)}; {len(first ^ docs)} differ. Refused rather than "
                "treating an absent document as an empty prediction: that would score "
                "two crashed runs as perfectly consistent with each other."
            )
    per_document: dict[str, float] = {}
    for doc_id in sorted(first):
        sets = [set(run[doc_id]) for run in runs]
        pairs = [jaccard(sets[i], sets[j])
                 for i in range(len(sets)) for j in range(i + 1, len(sets))]
        per_document[doc_id] = sum(pairs) / len(pairs)
    values = list(per_document.values())
    empty_everywhere = sum(
        1 for doc_id in first
        if all(not set(run[doc_id]) for run in runs))
    return {
        "C": sum(values) / len(values) if values else None,
        "runs": len(runs),
        "documents": len(values),
        # Reported because it is the share of the denominator that scored 1 by the
        # both-empty rule. A C of 0.97 on a fold where 60% of documents are empty
        # everywhere is a different claim from the same number on a fold where none are,
        # and the rule that makes the first possible is stated in DESIGN §12.5 — so the
        # count that makes it readable travels with the metric.
        "documents_empty_in_every_run": empty_everywhere,
        "identical_in_every_run": sum(1 for v in values if v == 1.0),
        "per_document_min": min(values) if values else None,
    }


def spans_to_keys(spans: Iterable) -> list[SpanKey]:
    """`[(start, end, phi_type), ...]` from Span or Mark objects."""
    out = []
    for span in spans:
        if span.phi_type is None:
            raise CorpusError(
                f"a span at [{span.start}, {span.end}) has no phi_type and cannot be "
                "a consistency key — C compares typed extents (DESIGN §12.5)."
            )
        out.append((span.start, span.end, span.phi_type))
    return out


def consistency_layers() -> dict[str, str]:
    """The three layers, from `config/naming.yaml`'s `consistency_layer`."""
    value = naming().get("consistency_layer")
    if not isinstance(value, dict) or not value:
        raise CorpusError(
            "config/naming.yaml has no `consistency_layer` mapping. The three layers "
            "of DESIGN §12.5 measure different things and the layer name lands in "
            "every reported C, so CLAUDE.md keeps the value out of the modules."
        )
    return dict(value)


def check_consistency_layer(value: str) -> str:
    layers = consistency_layers()
    if value not in layers:
        raise CorpusError(
            f"{value!r} is not a consistency layer in config/naming.yaml "
            f"(have: {sorted(layers)}). The three measure different things — nothing "
            "is re-drawn, the response is re-drawn, the training is re-drawn — and a "
            "fourth needs a decision in DESIGN §12.5 before a module writes it."
        )
    return value


@dataclass(slots=True)
class RfpRow:
    """One arm's O/X against the RFP's stage-1 thresholds.

    **Carried as a reference column and not as a claim** (DESIGN §12.5). The threshold is
    someone else's, set over a PHI type set that is not this project's, so an X is not a
    finding about the method and an O is not a validation of it. The dataclass exists so
    that the three quantities being compared are named rather than positional — a row that
    said `[True, False, True]` would need the reader to know the order.
    """

    arm: str
    corpus: str
    accuracy: float | None
    consistency: float | None
    recall: float | None
    threshold: float = 0.90

    def _mark(self, value: float | None) -> str:
        if value is None:
            # Not "X". A quantity that was not computed is not a quantity that failed,
            # and the RFP column is read by someone counting failures.
            return "-"
        return "O" if value >= self.threshold else "X"

    def as_record(self) -> dict:
        return {
            "arm": self.arm,
            "corpus": self.corpus,
            "accuracy": self.accuracy,
            "consistency": self.consistency,
            "recall": self.recall,
            "threshold": self.threshold,
            "O_X": {
                "accuracy": self._mark(self.accuracy),
                "consistency": self._mark(self.consistency),
                "recall": self._mark(self.recall),
            },
            "is_a_project_claim": False,
        }
