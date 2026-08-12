"""Deterministic detection scoring (DESIGN §5, §5.1, §9.3, §9.4).

Corpus-agnostic, arm-agnostic, and agent-free. Given gold spans and predicted spans
as offsets and canonical types, it produces the metrics block that
`results/{corpus}/{detector}/{supervision}/{porting}/metrics.json` records.

Three properties this module is built around.

**It never sees text.** `Mark` has no `surface` field — not "does not use one", does
not have one. The scorer must run unchanged on a DUA corpus, and a field that could
hold note text is a field that eventually holds note text in a metrics file that gets
committed. Offsets, canonical types and provenance layers are sufficient to compute
every number here, so nothing else is accepted.

**It computes two matchings, not one** (DESIGN §9.3). `coverage` tests each gold span
against the *union* of same-type predictions and feeds the leak rate and the
complementarity breakdown; `assignment` is a one-to-one greedy matching and feeds
TP/FP/FN. A single matching cannot serve both: one wide prediction over two adjacent
gold spans must cost a false negative (credit) while leaking nothing (disclosure), and
a gold span split between two adjacent predictions is fully hidden while no single
prediction covers it. Both directions invent leaks that do not exist.

**It does not choose the headline.** Both modes are computed symmetrically; which
figure leads is recorded in the `headline` block and decided by the reporting layer.

Per-rule attribution (`by_rule`) is computed here rather than joined on afterwards,
for the reason DESIGN §9.3 records: a join outside the scorer needs its own copy of
the matching, and the moment the two copies disagree there are two answers to "which
rule fired" with nothing to say which is right. `error_spans()` is the same argument
applied to the per-span error list an iterating arm draws its next window from — it is
here, beside the matchings, and not in the loop driver.

Usage:

    pairs, excluded = from_documents(docs, predictions)
    scored = score(pairs, excluded_gold=excluded)
    write_metrics(scored, run={...}, cost={...})
    errors = error_spans(pairs)      # iterating arms only; `run_fold` writes them
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..corpora.base import (
    ROOT, axis, check_termination_reason, family_of, layer_families, model_id_absent,
    naming,
)
#: `ErrorSpan` and the two error kinds, from the module that defines them. Imported
#: rather than re-spelled, and the direction is deliberate: this module *produces* errors
#: and `src/sample.py` consumes them, while the vocabulary is declared there ("fixed here
#: so a caller cannot introduce a third kind"). A `"missed"` written as a literal in this
#: file would be a second definition site of a closed vocabulary, which is the rule
#: CLAUDE.md states for `naming.yaml` values applied to one that is deliberately not in it.
#:
#: The type, not a dict, for the reason `Mark` has no `surface` field: `ErrorSpan` cannot
#: hold a surface form, and the export is what an iterating arm's prompt window is built
#: from. A mapping with the same six keys would put that guarantee back in the hands of
#: whoever writes the next writer.
#:
#: This does not make the scorer arm-aware. `sample` is the *procedure* both arms share
#: (DESIGN §11.1), nothing here calls its config, and no function in this module knows
#: which arm asked.
from ..sample import FALSE_POSITIVE, MISSED, ErrorSpan

#: Bumped when the meaning of an output field changes, so a results directory holding
#: two versions is detectable rather than silently mixed.
SCORER_VERSION = 1
#: 2 adds `by_rule` to each mode block. The shape changed and no field changed
#: meaning, so this moves and SCORER_VERSION does not.
#: 3 adds `model_id` to the required run block (DESIGN §4). Same reasoning: a new
#: required field is a shape change, and no existing field means anything different.
#: 4 adds `generated`, `commit` and `tree`. §10 A2 named a date and a commit hash beside
#: the alias as its partial mitigation and recorded that neither was required — a
#: mitigation described in the design and absent from the writer. This is that mitigation
#: becoming a property. `run_fold` already wrote `commit` and `tree`; what changes is that
#: a run block without them is now refused rather than accepted and unnoticed.
#: 5 adds an optional top-level `model_lifecycle` block (DESIGN §4's dated pin,
#: 2026-08-11). A new *optional* block is still a shape change, because a reader diffing
#: two files needs to know whether an absent block means "this writer did not have one" or
#: "this version had no such field". Top-level and not inside `run` on purpose — see
#: `write_metrics`.
#: 6 adds a **required** top-level `termination` block (DESIGN §3's stopping rule, per-corpus
#: δ, 2026-08-12). Required and not optional, which is the opposite call from 5 and for a
#: reason that distinguishes the two: an absent `model_lifecycle` means "no probe was made",
#: a real state a reader needs to tell from "this writer had no such field". There is no
#: corresponding state here — every arm either iterated or did not, and the one that did not
#: records `not_applicable`. A block some arms carried and others omitted would be a field
#: that cannot be compared across arms, which is `model_id`'s argument at schema 3.
SCHEMA_VERSION = 6

FULLY_COVERED = "fully_covered"
RELAXED = "relaxed"
MODES = (FULLY_COVERED, RELAXED)

#: DESIGN §9.4: types with n <= 8 corpus-wide stay in every denominator and are
#: omitted only from per-type tables. The scorer flags them and omits nothing — a
#: reporting layer cannot restore a row the scorer deleted.
SPARSE_MAX = 8

#: Which mode is the headline for which metric (DESIGN §9.3). Recorded in the output
#: rather than acted on: no code path here treats one mode as primary.
HEADLINE_MODE = {
    "leak_rate": FULLY_COVERED,
    "leak_rate_lower_bound": RELAXED,
    "precision": RELAXED,
    "recall": RELAXED,
    "f1": RELAXED,
}

#: Which mode each half of `error_spans()` is read from, **derived from `HEADLINE_MODE`
#: rather than written down**. An iterating arm is trying to move two numbers — the leak
#: rate and precision — and the errors it is shown at the next round have to be the errors
#: those two numbers are made of. Spelling the two modes as literals here would let the
#: headline choice move (it is the reporting layer's, per this module's docstring) while
#: the window kept showing the old one, and the symptom would be an arm optimising
#: something nobody reports.
#:
#: `missed` is therefore the `fully_covered` leak set, which is what
#: `docs/prompts/rule_author.md` §1.4 already tells the author it is ("missed = leaked
#: under fully_covered"), and what DESIGN §3's stopping rule is computed on. `false_positive`
#: is the `relaxed` assignment's unmatched predictions: under `fully_covered` a prediction
#: that covers most of a gold span is unmatched and would be shown to an author as a false
#: positive, while the precision figure that gets published counts it as a hit.
ERROR_MODE = {MISSED: HEADLINE_MODE["leak_rate"],
              FALSE_POSITIVE: HEADLINE_MODE["precision"]}

#: The components of `paths.metrics`, in the template's own order. Each is an axis
#: value and each is validated against its axis, because a typo here mints a directory
#: that looks like another arm.
PATH_AXES = ("corpus", "detector", "supervision", "porting")

#: Run fields that are axis values and are checked against their axis. `split` is here
#: and not in `PATH_AXES`: it is drawn from a closed vocabulary but does not name a cell.
AXIS_VALUED = PATH_AXES + ("split",)

#: Required in the run block. A superset of `PATH_AXES`, and the difference is the
#: point: `split` and `model_id` must be recorded and must *not* be in the path.
#:
#: `split` because a results directory holds one arm's numbers and the fold they were
#: computed on is a property of the run, not a second arm.
#:
#: `model_id` because Bedrock model aliases are updated silently (DESIGN §4,
#: `docs/notes/baseline-model-family.md`), so an unrecorded run does not reproduce six
#: months later. It is deliberately **not** an axis and deliberately not a path
#: component:
#:
#:   - Not an axis. `axis()` is a closed vocabulary and this field holds an observation
#:     made at call time — the exact identifier the API was invoked with. A closed
#:     vocabulary would refuse the true value and reward writing down an approximation
#:     of it, which is the opposite of what the field is for. CLAUDE.md's rule that new
#:     vocabulary enters naming.yaml first still binds on the one value that *is*
#:     vocabulary: `none`, read from `naming.yaml`'s `model_id_absent`.
#:   - Not in the path. The path names the cell of the experiment (DESIGN §4). Running
#:     one arm on a second model is §10 A2's appendix analysis, not a new cell, and
#:     putting the model in the path would silently make it one.
#:
#: `generated`, `commit` and `tree` are §10 A2's stated mitigation for what `model_id`
#: cannot do. The measurement behind that (`docs/notes/baseline-model-family.md`,
#: 2026-08-08) is that a Bedrock alias does not resolve to a snapshot: an undated alias in
#: gives an undated alias back. So `model_id` records *what was asked for* and can never
#: record what answered. These three bound that gap from the outside — they cannot say
#: which weights ran, and they do fix the instant at which the alias meant whatever it
#: meant, and which revision of this repository was asking.
#:
#: All three, not the hash alone. A commit hash identifies code only if the tree was
#: clean, so `commit` without `tree` is a hash that may describe something other than what
#: ran; and `generated` is what remains useful when `tree` says `dirty` or `unknown`,
#: because a wall-clock instant is still comparable against the alias's own history.
#: Requiring the hash and not the state would publish the most confident of the three on
#: its own — the shape `### Unreadable state, twice` in `tests/mutations/README.md` is
#: about.
REQUIRED_RUN = ("corpus", "detector", "supervision", "porting", "split", "model_id",
                "generated", "commit", "tree")
REQUIRED_COST = ("llm_calls", "prompt_tokens", "completion_tokens", "wall_seconds")

#: Required in the `termination` block — `src.termination.Termination.record()`'s keys.
#: Checked for presence and not for content: this module validates the *shape* of a record
#: another module produced, and re-deriving the verdict here would be a second implementation
#: of the stopping rule. §3's substantive prohibition — a ceiling stop is not convergence — is
#: enforced where the verdict is made (`Termination.converged` is a property, so the
#: contradictory state cannot be constructed), and the one cross-check this module *can* make
#: without reimplementing anything is that the two agree, which `check_termination` does.
#:
#: `improvements` is here because §3's difference-versus-level distinction is invisible in a
#: leak rate alone: a file recording only the final rate and a reason cannot be checked
#: against the rule that produced it. `delta` and `n_dev` both, for the reason `rules_version`
#: and `rules_source` are both required — the rate is what the rule compared against and the
#: count is what makes it comparable across corpora, and neither can be recovered from the
#: other without the corpus's split file in hand.
REQUIRED_TERMINATION = ("reason", "converged", "iterations", "delta", "delta_spans",
                        "delta_floor", "k", "ceiling", "n_dev", "improvements")

#: Required to be *written* and permitted to be null — the key must be in the block and
#: its value may be `None`, under the condition `check_run` enforces.
#:
#: `commit` is here because `sealed_log.tree_state()` returns `(None, "unknown")` when git
#: cannot be read, and that pair is the honest record of an unreadable revision. A
#: validator demanding a truthy hash there leaves a writer two options: refuse to score a
#: real run, or put something in the field. The second is what actually happens, and it is
#: the failure this field was added to prevent — a hash that reads as identifying the code
#: while nobody checked whether it does. So null is accepted **and only in company**: null
#: with `tree` of `clean` or `dirty` is refused, because a tree state that was readable and
#: a revision that was not is a contradiction rather than a missing measurement.
#:
#: Absent is still refused, for `model_id`'s reason: a key that may be omitted is a key
#: some arms will lack, and a null nobody wrote cannot be told from a null that was
#: measured.
NULLABLE_RUN = ("commit",)

#: `tree`'s vocabulary, from `sealed_log.tree_state`'s three documented values. Written
#: out rather than imported to avoid a cycle, and asserted against that function in
#: `tests/test_scorer.py` so the two cannot drift.
#:
#: Not an axis, for `model_id`'s reason turned around: this *is* a closed vocabulary, but
#: it is an observation about the working tree rather than a coordinate of the experiment,
#: and naming.yaml's axes are what name cells. The check lives here because this is the
#: only writer.
TREE_STATES = ("clean", "dirty", "unknown")

#: `generated` must be an instant, not a date. A `YYYY-MM-DD` string would make two runs
#: on one day indistinguishable in ordering, and A2's question is which of two numbers came
#: from the earlier resolution of an alias. UTC and `Z`-suffixed, matching `src/split.py`
#: and `src/eval/sealed_log.py` so the three records sort against each other.
GENERATED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ScorerError(Exception):
    """Anything wrong with the input to, or configuration of, the scorer.

    One type: every case is "stop and tell a human", and a caller has no recovery
    path that differs by cause. A silently degraded metric is the failure mode this
    module exists to avoid.
    """


# ─── data model ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Mark:
    """One span as the scorer sees it: offsets, canonical type, provenance layer.

    Frozen because scoring must not be able to adjust its own inputs, and because a
    hashable mark makes the assignment bookkeeping obvious.

    There is no `surface` field and this is load-bearing, not tidiness. Every number
    in this module is computable from offsets and types; adding the text would make
    the scorer unrunnable on a DUA corpus under CLAUDE.md's rules, and would put note
    text one careless `json.dump` away from a committed metrics file. Constructing a
    `Mark` with a surface raises `TypeError` from the dataclass itself, which is why
    `tests/test_scorer.py` asserts it rather than trusting this paragraph.

    `layer` is None on gold and a value of naming.yaml's `layer` axis on a
    prediction. It is never inferred from anything (DESIGN §3).

    `rule_id` is None on gold and on tagger spans, and the emitting rule's
    prefixed id (`es:doctor_prefix`) on a rule-layer span. The prefix is required
    and checked against the `lang` axis, because `es-carmen` loads two rule files
    (DESIGN §5.2) and an unprefixed `doctor_prefix` from each would land in one
    `by_rule` bucket — two rules' counts added together with nothing in the output
    saying so.

    `span_index` is the span's position **in the list it arrived in** — the document's own
    `Span` list for a gold mark, the document's prediction list for a predicted one. It is
    the reference DESIGN §11.2 fixes, and it is a field rather than something a caller
    recovers by enumeration because the information is destroyed at this boundary: gold
    marks are the *in-scope* subset (§9.1), so a position in `DocPair.gold` is not a
    position in `Document.spans` for any document holding an excluded span before an
    in-scope one — and MEDDOCAN's excluded types are common enough that the two disagree
    on most documents. `src.porting.human_arm.initial_error_pool` produces the same
    reference for iteration 1 by enumerating the unfiltered list; the two producers have to
    mean one thing by `(doc_id, span_index)` or an iteration-4 reference resolves to the
    wrong span in anyone's hands.

    None when nothing filled it — a mark built directly rather than through
    `from_documents`. Every number in this module is computable without it, so it is
    optional here and *required* by `error_spans()`, which is the one consumer that
    exports references (see its message). It is an integer and never text.
    """

    start: int
    end: int
    phi_type: str
    layer: str | None = None
    rule_id: str | None = None
    span_index: int | None = None

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ScorerError(f"empty or inverted span: [{self.start}, {self.end})")
        if self.start < 0:
            raise ScorerError(f"negative span start: {self.start}")
        if self.phi_type not in axis("phi_type"):
            raise ScorerError(
                f"{self.phi_type!r} is not a phi_type in config/naming.yaml. "
                "Excluded spans (DESIGN §9.1) carry no canonical type and are "
                "filtered out before scoring, not passed in with a placeholder."
            )
        if self.layer is not None and self.layer not in axis("layer"):
            raise ScorerError(
                f"{self.layer!r} is not a layer in config/naming.yaml "
                f"(have: {sorted(axis('layer'))})"
            )
        if self.span_index is not None and (
            not isinstance(self.span_index, int) or isinstance(self.span_index, bool)
            or self.span_index < 0
        ):
            # A position in a list, checked for what a position can be. `True` is refused
            # explicitly: it is an `int` in Python and it would index element 1 of every
            # document, which resolves to a real span and to the wrong one.
            raise ScorerError(
                f"span_index must be a non-negative integer or None, got "
                f"{self.span_index!r}. It is a reference into a document's own span list "
                "(DESIGN §11.2) and is resolved by whoever holds the corpus, so a value "
                "that indexes the wrong element is wrong silently."
            )
        self._check_rule_id()

    def _check_rule_id(self) -> None:
        """`rule_id` is present exactly on rules-family spans, and prefixed.

        None of these messages quote the id. A rule name can contain corpus text —
        that is what `rules/*.yaml`'s screening exists for — and an exception
        message goes to terminals, CI logs and stack traces, which
        `tools/release_screen.py` does not reach (CLAUDE.md).
        """
        family = family_of(self.layer) if self.layer is not None else None
        if family == "rules":
            if self.rule_id is None:
                raise ScorerError(
                    f"a {self.layer!r} span carries no rule_id. The per-rule "
                    "attribution block is what makes a rule file shrinkable rather "
                    "than only growable, and a rules-family span with no id would "
                    "drop out of it silently — the fire counts would stop summing "
                    "to the prediction count with nothing in the output saying so."
                )
            prefix, _, rest = self.rule_id.partition(":")
            if not rest or prefix not in axis("lang"):
                raise ScorerError(
                    "a rule_id must be prefixed with the language of the rule file "
                    f"that produced it (one of {sorted(axis('lang'))}, DESIGN §5.2). "
                    "The id is not quoted here because a rule name can contain "
                    "corpus text. One corpus loads several rule files, so two files' "
                    "same-named rules would otherwise share one attribution bucket."
                )
        elif self.rule_id is not None:
            raise ScorerError(
                f"a span with layer {self.layer!r} carries a rule_id, but only "
                "rules-family layers come from rules/*.yaml. A learned span has no "
                "rule that fired, and putting a model or checkpoint name in this "
                "field would make the per-rule table mix two kinds of thing."
            )

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class DocPair:
    """One document's in-scope gold spans and its predictions.

    `doc_id` and nothing else about the document: no text, and deliberately not even
    its length. A length would make the metrics file carry a fact about the note, and
    nothing here needs it.
    """

    doc_id: str
    gold: tuple[Mark, ...] = ()
    pred: tuple[Mark, ...] = ()


def _mark(obj, span_index: int | None = None) -> Mark:
    """Adapt anything with the four attributes into a Mark.

    Accepts `corpora.base.Span`, which *does* carry a surface — and drops it here.
    That is the point of the conversion: the surface stops existing at the scorer
    boundary rather than being carried along unused.

    `span_index` is supplied by the caller and never read off `obj`, because a `Span` does
    not know where in its document's list it sits — the position is the caller's knowledge
    and this is the only point where it is still available.

    **A `Mark` that arrives already carrying an index has it replaced, not kept.** The
    caller is `from_documents`, which is reading the list the index refers to; an index
    already on the object came from some other list, and the two cannot both be the
    reference §11.2 fixes. Passing the object through unchanged was the first
    implementation and it made the field silently absent for every caller that builds
    `Mark`s rather than `Span`s — which is the test suites, so the export's own tests would
    have run on two hand-built documents.
    """
    if isinstance(obj, Mark):
        return obj if obj.span_index == span_index else replace(
            obj, span_index=span_index)
    try:
        return Mark(
            start=obj.start, end=obj.end, phi_type=obj.phi_type,
            layer=getattr(obj, "layer", None),
            rule_id=getattr(obj, "rule_id", None),
            span_index=span_index,
        )
    except AttributeError as exc:
        raise ScorerError(
            "a span must expose start, end, phi_type and layer to be scored "
            f"({exc})"
        ) from exc


def from_documents(
    docs: Iterable, predictions: Mapping[str, Sequence]
) -> tuple[list[DocPair], int]:
    """Build DocPairs from loader Documents plus per-document predictions.

    Returns `(pairs, excluded_gold)`.

    **Excluded spans are filtered here, not by the caller.** DESIGN §9.1 keeps them
    in the corpus and out of every metric, so somebody has to drop them; a caller
    that forgets is far more likely than a scorer that forgets, and the resulting
    error inflates the denominator silently. The count comes back because §9.1
    requires the excluded volume to be reported as a limitation, which means it has
    to be countable at the point where it is dropped.

    A document with no predictions is a document with no predictions — absent from
    the mapping and present in the output with an empty tuple. Not an error: a
    detector that finds nothing in a note is a result.

    **Each mark carries its position in the list it came from** (`Mark.span_index`), and
    for gold that is the position in `doc.spans` — the unfiltered list, counting the
    excluded spans this function is dropping. The filtered position is the one that is easy
    to produce here and it is the wrong referent: `initial_error_pool()` enumerates the
    unfiltered list for iteration 1, so a filtered index would make iteration 1 and
    iteration 4 mean different things by `(doc_id, span_index)` while both looked correct.
    """
    pairs: list[DocPair] = []
    excluded = 0
    for doc in docs:
        gold = []
        for i, span in enumerate(doc.spans):
            if not span.in_scope:
                excluded += 1
                continue
            gold.append(_mark(span, i))
        pred = tuple(_mark(p, i)
                     for i, p in enumerate(predictions.get(doc.doc_id, ())))
        pairs.append(DocPair(doc_id=doc.doc_id, gold=tuple(gold), pred=pred))
    return pairs, excluded


# ─── interval arithmetic ────────────────────────────────────────────────────


def _union(marks: Iterable[Mark]) -> list[tuple[int, int]]:
    """Merged, sorted intervals. The scorer's answer to "what is hidden"."""
    spans = sorted((m.start, m.end) for m in marks)
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _covered_length(mark: Mark, union: Sequence[tuple[int, int]]) -> int:
    """How many of `mark`'s characters the union covers."""
    total = 0
    for start, end in union:
        total += max(0, min(mark.end, end) - max(mark.start, start))
    return total


def _covers(mark: Mark, union: Sequence[tuple[int, int]], mode: str) -> bool:
    """Does the union cover `mark` under this mode's rule?

    `fully_covered` is a statement about the union — "every character covered by some
    prediction" — which is a second reason coverage and assignment cannot be collapsed
    into one matching (DESIGN §9.3).
    """
    if mode == FULLY_COVERED:
        return _covered_length(mark, union) == mark.length
    return _covered_length(mark, union) > 0


def _overlap(a: Mark, b: Mark) -> int:
    return max(0, min(a.end, b.end) - max(a.start, b.start))


def _eligible(gold: Mark, pred: Mark, mode: str) -> bool:
    """May this single prediction be assigned to this gold span?

    Assignment is pairwise, so `fully_covered` here means *this* prediction covers
    the whole gold span. Under that mode a gold span covered jointly by two adjacent
    predictions is a false negative — which is correct for a credit question and
    exactly why the leak rate is not computed from these numbers.
    """
    if mode == FULLY_COVERED:
        return pred.start <= gold.start and pred.end >= gold.end
    return _overlap(gold, pred) > 0


# ─── the two matchings ──────────────────────────────────────────────────────


def coverage(gold: Sequence[Mark], pred: Sequence[Mark], mode: str) -> list[bool]:
    """Per gold span: is it covered by the union of same-type predictions?

    Feeds the leak rate and the complementarity breakdown. No competition for credit
    happens here — whether an identifier is hidden does not depend on which detector
    would get the point for it (DESIGN §9.3).
    """
    unions: dict[str, list[tuple[int, int]]] = {}
    for phi_type in {p.phi_type for p in pred}:
        unions[phi_type] = _union(p for p in pred if p.phi_type == phi_type)
    return [_covers(g, unions.get(g.phi_type, ()), mode) for g in gold]


def dedupe(pred: Sequence[Mark]) -> tuple[list[Mark], int]:
    """Collapse predictions identical in (start, end, phi_type). Returns (kept, n).

    Two layers emitting the same span found the same thing once, not two things. The
    assignment matching is one-to-one, so without this collapse the second copy is an
    unmatched prediction and becomes a false positive — precision would fall exactly
    where the layers agree, which is the same pathology DESIGN §9.3 identified for
    complementarity, reappearing in a different number.

    Only *identical* spans collapse. Merging overlapping-but-differently-bounded
    predictions is the merge policy's decision (DESIGN §4, a replaceable strategy)
    and belongs upstream of scoring; a scorer that merged on its own would make every
    merge policy score alike. The layer view is computed from the full prediction set,
    not this one, so nothing about provenance is lost here.
    """
    kept: list[Mark] = []
    seen: set[tuple[int, int, str]] = set()
    for p in pred:
        key = (p.start, p.end, p.phi_type)
        if key in seen:
            continue
        seen.add(key)
        kept.append(p)
    return kept, len(pred) - len(kept)


def assign(
    gold: Sequence[Mark], pred: Sequence[Mark], mode: str
) -> tuple[dict[int, int], list[int], list[int]]:
    """One-to-one greedy matching. Returns (gold index -> pred index, fn, fp).

    Greedy by largest overlap, so one wide prediction cannot claim credit for several
    gold spans. Ties are resolved by a **total order** — `(-overlap, gold.start,
    gold.end, pred.start, pred.end, pred_index)` — so the result cannot depend on the
    order a detector happened to emit spans in. A scorer whose output moves when the
    same spans arrive shuffled is not a measurement, which is why
    `test_scoring_is_order_independent` shuffles and re-scores rather than trusting
    this comment.
    """
    candidates = []
    for gi, g in enumerate(gold):
        for pi, p in enumerate(pred):
            if g.phi_type != p.phi_type or not _eligible(g, p, mode):
                continue
            candidates.append(
                (-_overlap(g, p), g.start, g.end, p.start, p.end, pi, gi)
            )
    candidates.sort()

    matched: dict[int, int] = {}
    used: set[int] = set()
    for *_, pi, gi in candidates:
        if gi in matched or pi in used:
            continue
        matched[gi] = pi
        used.add(pi)

    fn = [gi for gi in range(len(gold)) if gi not in matched]
    fp = [pi for pi in range(len(pred)) if pi not in used]
    return matched, fn, fp


# ─── scoring ────────────────────────────────────────────────────────────────


def _rule_tally(
    pred: Sequence[Mark],
    matched_keys: frozenset[tuple[int, int, str]],
    fp_keys: frozenset[tuple[int, int, str]],
    into: dict[str, dict],
) -> None:
    """Accumulate one document's per-rule tp / fp / fires into `into`.

    **False positives come from the assignment matching's unmatched predictions**
    (`fp_keys`), never from coverage. A rule's span that overlaps a gold span of the
    right type but lost the assignment to a better-overlapping prediction is a false
    positive for that rule — the credit was given elsewhere and cannot be given twice.
    Computed from coverage instead, that span would look like a hit, and a rule with no
    unique contribution would read as harmless: precisely the rule an author should
    delete. The two questions §9.3 separates for the corpus separate here too, and
    attribution is the credit question.

    `fires` counts emissions; `tp` and `fp` count *distinct* spans. Per rule,
    `tp + fp` is the number of distinct spans it emitted, and `fires` exceeds that when
    the rule matched one span more than once. Both are kept because they answer
    different questions: fires is what the rule did, tp + fp is what it was scored on.

    Duplicates *across* rules are counted for both rules. Two rules emitting the
    byte-identical span both found it, and the assignment's one-to-one collapse
    (`dedupe`) is about the span's credit, not about which rule gets named. Attributing
    to whichever copy survived deduplication would make the table depend on the order
    the detector emitted spans in. The consequence, stated because it is the kind of
    thing that gets summed by accident: **`by_rule` totals need not equal the mode's
    `overall` counts**, and the difference is bounded by `duplicate_predictions`.
    """
    per_rule_keys: dict[str, set[tuple[int, int, str]]] = {}
    for p in pred:
        if p.rule_id is None:
            continue
        entry = into.setdefault(
            p.rule_id, {"layer": p.layer, "fires": 0, "tp": 0, "fp": 0}
        )
        if entry["layer"] != p.layer:
            raise ScorerError(
                "one rule_id emitted spans under two different layers "
                f"({entry['layer']!r} and {p.layer!r}). A rule declares its layer in "
                "rules/*.yaml (DESIGN §3), so this is either two rules sharing an id "
                "or a rule whose layer changed mid-run; either way the per-layer "
                "results of §7 would be attributed to the wrong mechanism. The id is "
                "not quoted here because a rule name can contain corpus text."
            )
        entry["fires"] += 1
        per_rule_keys.setdefault(p.rule_id, set()).add(
            (p.start, p.end, p.phi_type)
        )

    for rule_id, keys in per_rule_keys.items():
        entry = into[rule_id]
        for key in keys:
            if key in matched_keys:
                entry["tp"] += 1
            elif key in fp_keys:
                entry["fp"] += 1
            else:
                # Unreachable while `dedupe` keys on the same triple as this tally.
                # Kept because the two are separate functions: if one starts keying
                # on something else, the counts would silently stop summing.
                raise ScorerError(
                    "a predicted span is neither matched nor unmatched in this "
                    f"document's assignment (span [{key[0]}, {key[1]})). The two sets "
                    "partition the deduplicated predictions by construction, so this "
                    "means `dedupe` and `assign` disagree about the key of a span."
                )


@dataclass(frozen=True, slots=True)
class _GoldRecord:
    """One gold span's verdict under one mode. The unit everything aggregates from.

    `span_index`, `start` and `end` are carried so that `error_spans()` reads its
    references off *these* records rather than re-deriving them from a second pass over
    the pairs. Nothing in the aggregation uses the three, and that is the point: DESIGN
    §9.3's rule is that the matching is computed once, so the object that holds a verdict
    also has to hold enough to say which span the verdict is about. A caller that had the
    verdict and had to look the span up would be joining on the scorer's output, and a
    join is where the second matching gets written.
    """

    doc_id: str
    phi_type: str
    covered: bool                   # coverage matching
    matched: bool                   # assignment matching
    families: frozenset[str]        # families whose own union covers it
    layers: frozenset[str]          # layers whose own union covers it
    span_index: int | None          # position in the document's own span list (§11.2)
    start: int
    end: int


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall, "f1": _f1(precision, recall),
    }


def _records(
    pairs: Sequence[DocPair], mode: str
) -> tuple[list[_GoldRecord], dict, int, dict, list[tuple[str, Mark]]]:
    """Per-gold verdicts, per-type FPs, duplicate count, per-rule tally, FP marks. One mode.

    The last element is what `error_spans()` needs and no aggregate can supply: *which*
    predictions were unmatched, not how many. It is produced here, from this loop's own
    assignment result, for DESIGN §9.3's reason — the alternative is a caller that re-runs
    `assign()` to find out, and two copies of a matching are two matchings.

    The marks are the **deduplicated** predictions (`dedupe`), so two rules emitting the
    byte-identical span contribute one false positive and one entry here. `span_index` on a
    surviving mark is the first-kept copy's position, which is a real reference into the
    document's prediction list; the collapsed copy's position is not recorded because the
    two name one span, and showing an author the same span twice in a 40-span window would
    spend two slots on one error.
    """
    families = layer_families()
    records: list[_GoldRecord] = []
    fp_by_type: dict[str, int] = {}
    duplicates = 0
    by_rule: dict[str, dict] = {}
    fp_marks: list[tuple[str, Mark]] = []

    for pair in pairs:
        cov = coverage(pair.gold, pair.pred, mode)
        # Assignment scores distinct spans; coverage and the layer view read the full
        # prediction set, since "which layers found it" is the question there.
        distinct, dupes = dedupe(pair.pred)
        duplicates += dupes
        matched, _fn, fp = assign(pair.gold, distinct, mode)

        for pi in fp:
            key = distinct[pi].phi_type
            fp_by_type[key] = fp_by_type.get(key, 0) + 1
            fp_marks.append((pair.doc_id, distinct[pi]))

        # Per-rule attribution rides on the same assignment result — it is not a
        # second matching and there is no join outside this loop (DESIGN §9.3).
        fp_keys = frozenset(
            (distinct[pi].start, distinct[pi].end, distinct[pi].phi_type)
            for pi in fp
        )
        matched_keys = frozenset(
            (distinct[pi].start, distinct[pi].end, distinct[pi].phi_type)
            for pi in matched.values()
        )
        _rule_tally(pair.pred, matched_keys, fp_keys, by_rule)

        by_family = {
            fam: [p for p in pair.pred if family_of(p.layer) == fam]
            for fam in families
        }
        by_layer = {
            layer: [p for p in pair.pred if p.layer == layer]
            for layer in axis("layer")
        }

        for gi, g in enumerate(pair.gold):
            fams = frozenset(
                fam for fam, marks in by_family.items()
                if _covers(g, _union(m for m in marks if m.phi_type == g.phi_type),
                           mode)
            )
            layers = frozenset(
                layer for layer, marks in by_layer.items()
                if _covers(g, _union(m for m in marks if m.phi_type == g.phi_type),
                           mode)
            )
            records.append(_GoldRecord(
                doc_id=pair.doc_id, phi_type=g.phi_type,
                covered=cov[gi], matched=gi in matched,
                families=fams, layers=layers,
                # `g.span_index`, not `gi`. `gi` is the position in the in-scope subset and
                # the reference is into the document's own list — see `from_documents`.
                span_index=g.span_index, start=g.start, end=g.end,
            ))
    return records, fp_by_type, duplicates, by_rule, fp_marks


def _complementarity(records: Sequence[_GoldRecord]) -> dict:
    """rules only / tagger only / both / joint only / neither, and the layer view.

    `joint_only` is not in DESIGN §5's four-category scheme and is forced by
    `fully_covered`: a gold span whose first half is found by a rule and second half
    by the tagger is covered, while neither family covers it alone. Under the
    four-category scheme it would have to be called `neither`, and `neither` would
    then stop equalling the leaked count — a breakdown that contradicts the headline
    leak rate in the same file. With `joint_only` split out, the five categories
    partition the denominator and `neither` is exactly the leaked set. Under
    `relaxed` it is always 0, since any overlap by the union is an overlap by some
    single prediction and therefore by its family.
    """
    families = sorted(layer_families())
    if len(families) != 2:
        raise ScorerError(
            f"the complementarity breakdown of DESIGN §5 is a two-family scheme "
            f"(rules / tagger) and config/naming.yaml declares {families}. Decide "
            "in DESIGN §5 what the categories are for a third family before adding "
            "one — 'both' has no meaning until then."
        )

    out = {f"{fam}_only": 0 for fam in families}
    out.update({"both": 0, "joint_only": 0, "neither": 0,
                "denominator": len(records)})
    for rec in records:
        if not rec.covered:
            out["neither"] += 1
        elif not rec.families:
            out["joint_only"] += 1
        elif len(rec.families) == len(families):
            out["both"] += 1
        else:
            out[f"{next(iter(rec.families))}_only"] += 1

    # The layer view of the same question: which layers cover this gold span *on
    # their own*. `sets` is the subset distribution, because DESIGN §7 predicts
    # which layer finds what and that needs "context_cue only" told apart from
    # "context_cue also" — a per-layer count alone cannot do it.
    covered_by_layer = {layer: 0 for layer in sorted(axis("layer"))}
    sets: dict[str, int] = {}
    union_only = 0
    for rec in records:
        for layer in rec.layers:
            covered_by_layer[layer] += 1
        if rec.covered and not rec.layers:
            # Covered by the union of predictions but by no single layer alone —
            # only reachable under `fully_covered`. Counted apart from the empty
            # subset so that `sets[""]` stays exactly the leaked set, the way
            # `families.neither` does. Folding the two together would put a
            # jointly-hidden identifier in the same bucket as a disclosed one and
            # make the breakdown contradict the leak rate in the same file.
            union_only += 1
            continue
        key = "|".join(sorted(rec.layers))
        sets[key] = sets.get(key, 0) + 1

    return {
        "families": out,
        "layers": {
            "covered": covered_by_layer,
            "sets": dict(sorted(sets.items())),
            "covered_by_union_only": union_only,
        },
    }


def _mode_block(records: Sequence[_GoldRecord], fp_by_type: dict,
                pairs: Sequence[DocPair], duplicates: int,
                by_rule: dict) -> dict:
    leaked = [r for r in records if not r.covered]
    denominator = len(records)

    tp = sum(1 for r in records if r.matched)
    fn = denominator - tp
    fp = sum(fp_by_type.values())

    types = sorted({r.phi_type for r in records} | set(fp_by_type))
    by_type = {}
    for phi_type in types:
        rows = [r for r in records if r.phi_type == phi_type]
        t_tp = sum(1 for r in rows if r.matched)
        t_leaked = sum(1 for r in rows if not r.covered)
        entry = {"gold": len(rows)}
        entry.update(_prf(t_tp, fp_by_type.get(phi_type, 0), len(rows) - t_tp))
        entry.update({
            "leaked": t_leaked,
            "leak_rate": t_leaked / len(rows) if rows else None,
            # DESIGN §9.4: flagged, never dropped. The reporting layer omits these
            # rows and states the omitted count; the scorer cannot make that
            # decision because a deleted row cannot be restored.
            "sparse": 0 < len(rows) <= SPARSE_MAX,
        })
        by_type[phi_type] = entry

    scored_types = [t for t in types if by_type[t]["gold"]]
    macro = {
        "precision": _mean(by_type[t]["precision"] for t in scored_types),
        "recall": _mean(by_type[t]["recall"] for t in scored_types),
        "f1": _mean(by_type[t]["f1"] for t in scored_types),
        "leak_rate": _mean(by_type[t]["leak_rate"] for t in scored_types),
        # Types with gold 0 contribute false positives to micro and nothing to
        # macro: a recall average over types that have no gold is undefined, not 0.
        "n_types": len(scored_types),
    }

    docs_with_gold = [p for p in pairs if p.gold]
    leaked_docs = {r.doc_id for r in leaked}
    return {
        "leak": {
            "leaked": len(leaked),
            "denominator": denominator,
            "rate": len(leaked) / denominator if denominator else None,
        },
        "overall": _prf(tp, fp, fn),
        "macro": macro,
        "by_type": by_type,
        "by_document": {
            # Documents with no gold PHI are out of this denominator (they cannot
            # leak) and counted as false-positive opportunity instead.
            "with_leak": sum(1 for p in docs_with_gold if p.doc_id in leaked_docs),
            "denominator": len(docs_with_gold),
            "rate": (sum(1 for p in docs_with_gold if p.doc_id in leaked_docs)
                     / len(docs_with_gold)) if docs_with_gold else None,
        },
        # Gold spans coverage calls covered and assignment calls a false negative.
        # Not an error term: it measures how differently the detector groups span
        # boundaries from the gold guideline (DESIGN §9.3).
        "assignment_slack": sum(1 for r in records if r.covered and not r.matched),
        "complementarity": {
            **_complementarity(records),
            "by_type": {
                phi_type: _complementarity(
                    [r for r in records if r.phi_type == phi_type])
                for phi_type in sorted({r.phi_type for r in records})
            },
        },
        "sparse": {
            "types": [t for t in scored_types if by_type[t]["sparse"]],
            "gold": sum(by_type[t]["gold"] for t in scored_types
                        if by_type[t]["sparse"]),
        },
        # Per-rule attribution (DESIGN §9.3). Sorted for a stable file. Rules that
        # fired nothing are absent — the scorer never read the rule file and cannot
        # tell a rule that matched nothing from a rule that does not exist; the
        # RuleAuthor holds the file and can see which of its ids are missing.
        "by_rule": {
            rule_id: {
                "layer": entry["layer"],
                "fires": entry["fires"],
                "tp": entry["tp"],
                "fp": entry["fp"],
            }
            for rule_id, entry in sorted(by_rule.items())
        },
        # Predictions collapsed as identical before assignment (see `dedupe`).
        # Reported because it is the volume of layer agreement that would otherwise
        # have been counted as false positives, and a silent collapse is
        # indistinguishable from a detector that never duplicated anything.
        "duplicate_predictions": duplicates,
    }


def _mean(values: Iterable[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _check_layers(pairs: Sequence[DocPair]) -> None:
    """Every predicted span carries its provenance layer (DESIGN §3).

    Factored out of `score()` when `error_spans()` arrived, so the two entry points check
    the same precondition rather than one of them relying on the other having been called.
    `error_spans()` needs it for a reason of its own: it runs `_records()`, which groups
    predictions by layer, and a `None` layer there falls silently into no group.
    """
    for pair in pairs:
        for p in pair.pred:
            if p.layer is None:
                raise ScorerError(
                    f"{pair.doc_id}: a predicted span has no layer. Every detected "
                    "span carries its provenance layer, set by the detector that "
                    "emitted it (DESIGN §3) — without it the complementarity "
                    "breakdown has nowhere to put the span."
                )


def score(pairs: Sequence[DocPair], *, excluded_gold: int = 0) -> dict:
    """The metrics block: counts, headline, and both modes in full.

    Pure and deterministic. No agent is called, no file is read beyond
    `config/naming.yaml`, and the result depends on the input spans only — not on
    their order (see `assign`).

    `excluded_gold` is the §9.1 volume from `from_documents`. Passed in rather than
    recomputed because by this point the excluded spans are gone, and a number that
    cannot be recomputed is a number that has to be carried.

    **The return is `metrics.json`'s content and is deliberately not widened.** The
    per-span error list an iterating arm needs comes from `error_spans()` instead
    (DESIGN §5.5): put here, a list of the positions of every missed identifier in the fold
    would be published by every arm that scores, on every corpus, as a permanent by-product
    of a feature only the iterating arms use.
    """
    _check_layers(pairs)

    modes = {}
    for mode in MODES:
        records, fp_by_type, duplicates, by_rule, _fp_marks = _records(pairs, mode)
        modes[mode] = _mode_block(records, fp_by_type, pairs, duplicates, by_rule)

    gold_total = sum(len(p.gold) for p in pairs)
    no_gold = [p for p in pairs if not p.gold]
    return {
        "scorer_version": SCORER_VERSION,
        "counts": {
            "documents": {
                "total": len(pairs),
                "with_gold_phi": len(pairs) - len(no_gold),
                "without_gold_phi": len(no_gold),
            },
            "gold": {
                "in_scope": gold_total,
                "excluded": excluded_gold,
                "excluded_share": (excluded_gold / (gold_total + excluded_gold)
                                   if gold_total + excluded_gold else None),
            },
            "pred": sum(len(p.pred) for p in pairs),
        },
        "headline": {
            "leak_rate": {
                "value": modes[FULLY_COVERED]["leak"]["rate"],
                "mode": FULLY_COVERED,
            },
            "leak_rate_lower_bound": {
                "value": modes[RELAXED]["leak"]["rate"], "mode": RELAXED,
            },
            "precision": {
                "value": modes[RELAXED]["overall"]["precision"], "mode": RELAXED,
            },
            "recall": {
                "value": modes[RELAXED]["overall"]["recall"], "mode": RELAXED,
            },
            "f1": {"value": modes[RELAXED]["overall"]["f1"], "mode": RELAXED},
        },
        "modes": modes,
        "false_positive_opportunity": {
            # Documents with no gold PHI cannot contribute to recall, and dropping
            # them entirely would discard the cleanest evidence about precision.
            "documents_without_gold_phi": len(no_gold),
            "predictions_in_those_documents": sum(len(p.pred) for p in no_gold),
        },
    }


def error_spans(pairs: Sequence[DocPair]) -> list[ErrorSpan]:
    """Every error as an `ErrorSpan`, from the same matchings the metrics came from.

    The per-span half of what `score()` aggregates, and the input an iterating arm's next
    window is drawn from (`src.sample.draw`, `docs/prompts/rule_author.md` §1.4). Returned
    as data and written by nobody here — `run_fold` owns the write, which is where DESIGN
    §5.0 already puts the decision about what closing an arm produces.

    **`missed` is the coverage verdict and `false_positive` is the assignment verdict**
    (`ERROR_MODE`, derived from `HEADLINE_MODE`). A missed span is one the union of
    same-type predictions did not cover under `fully_covered` — a *leak*, which is the
    number §3's stopping rule watches and what §1.4 promises the author the word means. It
    is emphatically not "the assignment found no partner for it": `assignment_slack` counts
    the gold spans that are covered and unmatched, and every one of those is an identifier
    that is hidden. Shown to a rule author as missed, each would ask for a rule against
    text that is already masked, and the arm would spend its iterations moving a number
    nobody publishes as the headline.

    **This is inside the scorer and not in the loop driver** (DESIGN §9.3). Computing the
    verdicts outside would need a second copy of both matchings, and a merge policy is
    scored on precisely the difference between "one wide prediction" and "two adjacent
    ones" — a recomputation that got either matching subtly different would make every
    merge policy score the same, which is the whole comparison of §4.

    `span_index` is required on every mark this touches, and refused rather than defaulted:
    an `ErrorSpan` whose index is a guess is a reference that resolves, in the corpus
    holder's hands, to the wrong span. Build pairs with `from_documents`, which fills it.

    No text, by construction twice over — `Mark` has no surface field and neither does
    `ErrorSpan`. Sorted by `ErrorSpan.key` so two runs over the same fold produce the same
    list, for the reason `write_spans` sorts: stability that comes from an upstream
    iteration order is not stability.
    """
    _check_layers(pairs)
    out: list[ErrorSpan] = []
    for kind, mode in ERROR_MODE.items():
        records, _fp_by_type, _dupes, _by_rule, fp_marks = _records(pairs, mode)
        source = ([(r.doc_id, r) for r in records if not r.covered]
                  if kind == MISSED else fp_marks)
        for doc_id, item in source:
            if item.span_index is None:
                raise ScorerError(
                    f"{doc_id}: a {kind} span at [{item.start}, {item.end}) has no "
                    "span_index, so it cannot be exported as a reference (DESIGN §11.2). "
                    "`from_documents` fills it from the document's own span list; a Mark "
                    "built directly carries None and there is nothing here to substitute "
                    "— an index guessed from position in the in-scope subset resolves to a "
                    "real span and to the wrong one."
                )
            out.append(ErrorSpan(
                doc_id=doc_id, span_index=item.span_index, phi_type=item.phi_type,
                kind=kind, start=item.start, end=item.end,
            ))
    return sorted(out, key=lambda e: e.key)


# ─── output ─────────────────────────────────────────────────────────────────


def check_run(run: Mapping[str, str]) -> None:
    """Every required run field is present, and every *axis-valued* one is a real value.

    Two checks, deliberately not one. Presence covers all of `REQUIRED_RUN`: a metrics
    file that omits a required field is a published number with a missing premise, and
    the omission is silent afterwards.

    Axis membership covers `AXIS_VALUED` only. `model_id` is exempt because it is not a
    closed vocabulary — see the constant's own note. Checking it against an axis would
    raise from `base.axis()` for the absent axis, and adding one would refuse the true
    Bedrock identifier while accepting a stand-in for it.

    `model_id` still gets the one check it can have: an empty string is a missing value
    wearing a present value's clothes, and an arm that used no model writes the explicit
    `naming.yaml` value for that (`model_id_absent`, currently `none`) rather than "".
    Absent is refused and explicitly-absent is recorded, which is the same rule the cost
    block's zeros follow.

    `generated` and `tree` get the checks *they* can have, and the reason to bother is that
    a required field with no validation is a field that gets filled with anything once. A
    `generated` of `"today"` and a `tree` of `"probably fine"` would both satisfy presence
    while making the run block say less than an absent field would — an absent field is at
    least legible as missing.

    `commit` is the one field whose value may be null, and never on its own: its shape
    varies by abbreviation so there is nothing to match, and `tree_state()` genuinely
    returns no hash when git cannot be read. What is checked instead is the *pair* — a null
    hash is accepted only with `tree` of `unknown`, which is the state that says the
    repository was unreadable. `clean`/`dirty` with no hash is refused, because those two
    values report the output of a command that also produced a revision.
    """
    for key in REQUIRED_RUN:
        if key in NULLABLE_RUN:
            if key not in run:
                raise ScorerError(
                    f"run block has no {key!r} key. Its value may be null — see "
                    "`NULLABLE_RUN` — but the key is required, because a field some arms "
                    "omit is a field that cannot be compared across arms, and a null "
                    "nobody wrote cannot be told apart from one that was measured "
                    "(DESIGN §10 A2)."
                )
            continue
        if not run.get(key):
            raise ScorerError(
                f"run block has no {key!r}. Every field here is a premise of the "
                "numbers beside it — the arm's four axes name the cell, `split` names "
                "the fold, `model_id` names what was actually called, and `generated`, "
                "`commit` and `tree` bound when that call happened and what code made "
                f"it. An arm that used no model records {model_id_absent()!r} "
                "(config/naming.yaml model_id_absent), because absent and "
                "not-applicable are different facts and this refuses to conflate them. "
                "There is no equivalent for the other three: every run has an instant, a "
                "revision and a tree state, so an absent one is unmeasured rather than "
                "inapplicable (DESIGN §10 A2)."
            )
    for key in AXIS_VALUED:
        if run[key] not in axis(key):
            raise ScorerError(
                f"{run[key]!r} is not a value of the {key!r} axis in "
                f"config/naming.yaml (have: {sorted(axis(key))}). Add it there "
                "before using it, rather than writing to a path nothing defines."
            )
    if not GENERATED_RE.match(str(run["generated"])):
        raise ScorerError(
            f"run['generated'] is {run['generated']!r}, which is not a UTC instant of "
            "the form 2026-08-09T14:03:22Z. A date alone cannot order two runs made on "
            "one day, and §10 A2's question is which of two numbers came from the "
            "earlier resolution of a model alias. src/split.py and "
            "src/eval/sealed_log.py write this format; matching them is what lets the "
            "three records be read against each other."
        )
    if run["tree"] not in TREE_STATES:
        raise ScorerError(
            f"run['tree'] is {run['tree']!r}, not one of {list(TREE_STATES)}. This "
            "field is what makes `commit` mean something: on a dirty tree the hash "
            "names a revision that is not what ran, and on an unknown one nobody could "
            "read the repository at all. A value outside the vocabulary is a third "
            "possibility that no reader can act on — sealed_log.tree_state() produces "
            "exactly these three."
        )
    if not run["commit"] and run["tree"] != "unknown":
        raise ScorerError(
            f"run['commit'] is {run['commit']!r} while run['tree'] is {run['tree']!r}. A "
            "null hash is the record of a repository that could not be read, and "
            "`unknown` is the only tree state that says so — `clean` and `dirty` both "
            "report the output of a git command that also produced a revision, so one "
            "without the other is a contradiction rather than a missing measurement. "
            "sealed_log.tree_state() returns the two together for this reason "
            "(DESIGN §10 A2)."
        )


def check_termination(termination: Mapping) -> None:
    """The `termination` block is complete, and its reason and `converged` flag agree.

    **What this does not do: re-decide the stopping rule.** The verdict is
    `src.termination.should_stop()`'s, and recomputing it here would be a second
    implementation of a pre-registered rule — two implementations of one rule are two rules,
    and the day they disagree the published file is whichever one wrote it. So this checks
    shape, vocabulary, and one consistency property, and nothing about whether the arm
    *should* have stopped.

    **The consistency property is DESIGN §3's prohibition, checked at the boundary it
    crosses.** A ceiling-terminated run may not be described as converged. That is made
    unconstructible upstream — `Termination.converged` is a property derived from `reason`,
    so no dataclass instance can hold the contradiction — but `write_metrics` takes a
    mapping, and a caller that assembled the block by hand is exactly the path around the
    dataclass. Checking here means the guarantee holds for the file rather than for one
    code path to it. This is the shape `tests/mutations/README.md` calls a guard whose
    precondition was never asked: the property was true of the producer and unchecked at the
    writer.

    `reason` may be null, and only with `converged` false — an arm still running has no
    reason, and `should_stop()` returns that state. Null with `converged` true is the same
    contradiction from the other side.
    """
    if not isinstance(termination, Mapping):
        raise ScorerError(
            f"termination must be a mapping, got {type(termination).__name__}. Pass "
            "`src.termination.Termination.record()`, or `not_applicable(corpus).record()` "
            "for an arm that does not iterate (DESIGN §3)."
        )
    missing = [k for k in REQUIRED_TERMINATION if k not in termination]
    if missing:
        raise ScorerError(
            f"termination block is missing {missing}. Every field is a premise of the "
            "stopping point: δ and `n_dev` are the threshold and the fold it was derived "
            "from, `improvements` is what makes §3's difference rule auditable from the "
            "file, and `reason` is what distinguishes a converged run from one that hit "
            "the cap. Build it with `src.termination.Termination.record()` rather than by "
            "hand."
        )
    extra = sorted(set(termination) - set(REQUIRED_TERMINATION))
    if extra:
        raise ScorerError(
            f"termination block has unexpected key(s) {extra}. The block is closed: a field "
            "this module does not know about would be published unvalidated, and a reader "
            "cannot tell such a field from part of the pre-registration."
        )
    reason = termination["reason"]
    converged = termination["converged"]
    if not isinstance(converged, bool):
        raise ScorerError(
            f"termination['converged'] is {converged!r}, not a bool. It is derived from "
            "`reason` and exists so a reader need not know the vocabulary; a non-boolean "
            "there means the block was assembled by hand."
        )
    if reason is not None:
        check_termination_reason(str(reason))
    if converged != (reason == "converged"):
        raise ScorerError(
            f"termination['reason'] is {reason!r} but ['converged'] is {converged!r}. "
            "DESIGN §3: an arm that stopped at the iteration ceiling has not satisfied the "
            "convergence test and may not be described as converged — a run that stopped at "
            "8 with the leak rate still falling is a different claim from one that stopped "
            "at 5 having converged. `Termination.converged` is a property precisely so the "
            "two cannot disagree; a block where they do was not built by it."
        )


def metrics_path(
    run: Mapping[str, str], root: Path | None = None, *, iteration: int | None = None
) -> Path:
    """The results path for this arm, from naming.yaml's `paths.metrics`.

    Validates the whole run block, then formats **only** `PATH_AXES` into the template.
    The two sets differ (`split`, `model_id`), and keeping them separate is what lets a
    field be required without becoming part of the arm's identity: a path is the cell of
    the experiment, so anything formatted into it mints a cell.

    `iteration` routes to `paths.itermetrics` — one round's score beneath the same
    directory (DESIGN §5.5). Routed rather than formatted here: `iter_metrics_path` is the
    single builder of that path, and a second `.format()` of the same template in this
    function would be two definition sites for one location.
    """
    check_run(run)
    if iteration is not None:
        return iter_metrics_path(
            **{k: run[k] for k in PATH_AXES}, iteration=iteration, root=root)
    template = naming()["paths"]["metrics"]
    return (root or ROOT) / template.format(**{k: run[k] for k in PATH_AXES})


def iter_metrics_path(
    *, corpus: str, detector: str, supervision: str, porting: str, iteration: int,
    root: Path | None = None,
) -> Path:
    """`paths.itermetrics` for one round — `iter{N}/metrics.json` beside that round's spans.

    **Keyword axes rather than a run block, unlike `metrics_path`, and for
    `run_fold.errors_path`'s reason: two interested parties.** This module writes the file
    from a run block it was handed, and the loop driver *reads* it — the sequence of
    per-round dev leak rates is what δ/k is computed over (`src/termination.py`), so the
    driver holding four axes and no run block has to be able to name the path. Handing it a
    run block to assemble would make it a second assembler of the record that one writer
    per record exists to protect.

    Every component is validated, the axes against `naming.yaml` and the round for being a
    round: a results path names the cell of the experiment an artefact belongs to, so an
    unknown component mints a cell instead of failing, and `iter0/` or `iter1.0/` puts a
    round's score somewhere nothing looks for it.

    The same shape of validation lives in `run_fold._round_path` for the two keys that
    module builds, and the repetition is the module boundary rather than an oversight: this
    module raises `ScorerError` and that one `FoldRunError`, each is the type its own
    callers already catch, and `src.rules.arm_rules_path` is a third instance for the same
    reason. What must not be repeated is the *template lookup*, and each key has exactly
    one.
    """
    for value, ax in ((corpus, "corpus"), (detector, "detector"),
                      (supervision, "supervision"), (porting, "porting")):
        if value not in axis(ax):
            raise ScorerError(
                f"{value!r} is not a value of the {ax!r} axis in config/naming.yaml "
                f"(have: {sorted(axis(ax))}). Add it there before using it, rather than "
                "writing to a path nothing defines."
            )
    if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 1:
        raise ScorerError(
            f"iteration must be an integer >= 1, got {iteration!r}. It is a path "
            "component (paths.itermetrics), and the sequence of an iterating arm's scores "
            "is what δ/k is computed over — a round's score written to iter0/ is a leak "
            "rate the stopping rule cannot find (DESIGN §3, §5.5)."
        )
    return (root or ROOT) / naming()["paths"]["itermetrics"].format(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        iteration=iteration,
    )


def write_metrics(
    scored: Mapping,
    *,
    run: Mapping,
    cost: Mapping,
    termination: Mapping,
    model_lifecycle: Mapping | None = None,
    root: Path | None = None,
    iteration: int | None = None,
) -> Path:
    """Assemble and write metrics.json. `run`, `cost` and `termination` are required.

    CLAUDE.md requires cost beside quality — LLM calls, tokens, wall time per arm —
    because a gain that costs 2x is a different result from one that costs 1.05x. It
    is a required argument rather than an optional one so that a missing cost is a
    failure at the call site instead of an absent key in a published file. The `R`
    arm passes zeros: **zero is a measurement and absent is not**, and a default
    would make them the same thing.

    `run["model_id"]` follows the same rule one field over (DESIGN §4). The `R` arm
    calls no model and records `naming.yaml`'s `model_id_absent` rather than being
    allowed to omit the field; an LLM arm records the exact identifier it called,
    because Bedrock aliases move under a stable name and a run recorded by alias is a
    run nobody can reproduce.

    `model_lifecycle` is `bedrock.model_lifecycle()`'s record, and three things about it
    are deliberate.

    **It does not resolve the alias, and it is not evidence that anything was resolved.**
    `start_of_life_time` is when the *id* appeared, not what the id pointed at on the day
    of the call — the measurement is `docs/notes/baseline-model-family.md` §"측정 결과" 4.
    Anyone reading this block as identification is reading it as the opposite of what it
    is, which is why the writer never derives `model_id_resolution` from it and why no
    field here is named `model_resolved`.

    **It sits at the top level and not in the run block**, which is where `model_id` and
    `model_id_resolution` live. The run block is what the paper's premises are read off;
    a lifecycle timestamp beside a resolution verdict would read as corroborating it. The
    separation is the same one `cost` gets, and for the same reason: adjacent to the claims
    rather than inside them.

    **Absent means no probe, not an older writer.** The block is omitted rather than
    nulled when there is nothing to probe (the `R` arm calls no model), and
    `SCHEMA_VERSION` was bumped for an *optional* addition precisely so that absence is
    legible — without the bump, a reader diffing two files cannot tell "this arm made no
    call" from "this writer had no such field".

    **`termination` is required, and the contrast with `model_lifecycle` is the reasoning.**
    DESIGN §3's stopping rule decides how many iterations an arm runs and hence its cost, so
    the threshold and the reason it stopped are premises of the numbers in the same way
    `split` and `model_id` are. `model_lifecycle` is optional because its absence is itself a
    fact — no probe was made — and there is no analogous state here: an arm either iterated
    or did not, and one that did not passes `termination.not_applicable(corpus).record()`,
    which is a measurement rather than a gap. A block some arms carried and others omitted
    would be uncomparable across arms, which is exactly `model_id`'s argument.

    It sits at the top level beside `cost` rather than inside `run`, for `model_lifecycle`'s
    reason turned around. `run` is what the paper's premises are read off and it is what
    `metrics_path` formats — putting δ there would make a threshold look like a coordinate of
    the arm, and the day someone adds it to `PATH_AXES` the same corpus at two δ values
    becomes two cells. `cost` is the right neighbour: both are properties of how the arm was
    run rather than of what it is, and §3 asks for the reason and the iteration count to be
    reported *beside* the leak rate.

    This function does not evaluate the rule. It validates the block's shape and the one
    property §3 forbids violating (`check_termination`), and the verdict itself comes from
    `src/termination.py` — see that module's note on why the rule is not in the loop driver.

    **`iteration` chooses the path and changes nothing about the payload** (DESIGN §5.5).
    Given a round, this writes `paths.itermetrics`; omitted, `paths.metrics`. It is
    deliberately not a field of any block: a round number in the run block would be a
    premise of the numbers that `metrics_path` could later be asked to format, and §5.5's
    duplication rule requires the final round's two files to be *identical*, which they
    cannot be if one of them names its own path. The round is recoverable from the path,
    and the `termination` block already carries `iterations`.
    """
    missing = [k for k in REQUIRED_COST if cost.get(k) is None]
    if missing:
        raise ScorerError(
            f"cost block is missing {missing}. Report cost with quality (CLAUDE.md). "
            "An arm that makes no LLM calls passes 0 — a zero is a measurement and "
            "an absent key is not, and this refuses to conflate them."
        )
    if model_lifecycle is not None and not model_lifecycle:
        raise ScorerError(
            "model_lifecycle is an empty mapping. Pass None for 'no probe was made' and "
            "bedrock.model_lifecycle()'s record otherwise — that function returns an "
            "`unavailable` record for every failure rather than an empty one, so an empty "
            "mapping here is a caller that built the block itself and lost the "
            "distinction. An empty dict would be written as absent, which says the arm "
            "called no model. (The constant is not imported: this module is agent-free "
            "and arm-free by construction, and a dependency on the LLM client for one "
            "word would end that.)"
        )
    check_termination(termination)
    path = metrics_path(run, root=root, iteration=iteration)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run": {**dict(run), "scorer_version": SCORER_VERSION},
        "cost": dict(cost),
        # DESIGN §3. Top level beside `cost` and never inside `run` — see the docstring:
        # a threshold is a property of how the arm was run, not a coordinate of which arm
        # it is, and `run` is what gets formatted into the results path.
        "termination": dict(termination),
        # Top level, not inside `run` — see the docstring. Omitted when there is nothing
        # to probe rather than written as null.
        **({"model_lifecycle": dict(model_lifecycle)} if model_lifecycle else {}),
        "headline_mode": dict(HEADLINE_MODE),
        **{k: v for k, v in scored.items() if k != "scorer_version"},
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    return path
