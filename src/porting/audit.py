"""Validate the Auditor's flags, and translate them into document coordinates.

`docs/prompts/auditor.md` §2 specifies a return the agent controls and a file it does not.
This module is the gap between them: it takes what one call returned, refuses what cannot
be a flag, and translates `(line, column)` in the *masked* text into `(start, end)` in the
document. The report is then the validated, translated claim.

**Why the translation is here and not in the prompt.** The mask tags change lengths, so
column arithmetic across a tag does not give a document offset. An agent asked to compute
across a tag boundary would be asked for the one arithmetic task it is worst at, and a
wrong offset is a flag that points at the wrong words while looking exactly like a right
one — the same objection DESIGN §3 makes to a masker that made judgements. The masker built
the map; this module reads it.

**Refuses rather than repairs, and counts what it refused** (`auditor.md` §2.3). Snapping an
out-of-range column to the end of its line would invent a position the agent never claimed;
dropping it silently would shorten the report for a reason no reader could see. Both leave a
file that cannot be distinguished from one where the call went well. So every refusal
carries a reason from `config/naming.yaml`'s `audit_refusal` vocabulary and `counts.refused`
carries the total, which is what makes "the model lost the coordinate scheme at iteration 5"
a number instead of a thin report.

**A refused flag keeps its `doc_id` and its reason, and nothing else — not even its
position.** Half of these refusals *are* the judgement that the position is not
trustworthy, and recording an untrustworthy position would let it pass for part of the map
of residual identifiers that `paths.auditreport` is deny-listed for being.

**No surface form, anywhere, on any path** (`auditor.md` §3, CLAUDE.md). The flag schema has
no free-text field, `_refuse()` quotes no text, and every error message here names a line
number, a column, a length or a type — never a slice of the document. This module holds
masked *and* unmasked text at once, which makes it the one place where a debugging `print`
would publish both, so `tests/test_audit.py` asserts structurally that it writes nothing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from ..corpora.base import CorpusError, axis, check_audit_refusal

#: The five refusal reasons, spelled once so this module's branches cannot drift from the
#: vocabulary, and each checked against `config/naming.yaml` at use rather than at import —
#: `src/termination.py`'s treatment of `termination_reason`, for its reason.
OUT_OF_RANGE = "out_of_range"
INSIDE_A_MASK_TAG = "inside_a_mask_tag"
UNDECLARED_PHI_TYPE = "undeclared_phi_type"
CROSSES_A_LINE = "crosses_a_line"
MALFORMED = "malformed"

#: Exactly the fields a flag may carry (`auditor.md` §2.1). A whitelist rather than a
#: required-subset check, and this is the enforcement half of §3's prohibition: the field
#: most likely to be added to a flag is a `reason` or a `snippet`, so an unknown key is
#: refused on the day it appears rather than written into a file under `results/`.
FLAG_FIELDS = frozenset({"line", "start", "end", "phi_type", "score"})

#: Types no flag may carry, for `sample.non_target_types()`'s reason and read the same way —
#: from naming.yaml's own gloss rather than as a second copy of `{"OTHER"}` here. A flag
#: the RuleAuthor is forbidden to act on (`rule_author.md` Prohibition 4) is prompt space
#: spent for nothing.
def _non_target_types() -> frozenset[str]:
    return frozenset(
        name for name, gloss in axis("phi_type").items()
        if isinstance(gloss, str) and "not a rule-development target" in gloss)


class AuditError(CorpusError):
    """A malformed call to this module — not a refused flag.

    The distinction is the module's shape: a flag the agent got wrong is *data*, recorded
    in `refused` and counted, because the report's job includes saying that it happened. A
    caller passing a mask map that disagrees with the text it masked is a *bug*, and
    turning it into a refusal would file it under the agent's mistakes.
    """


@dataclass(frozen=True, slots=True)
class MaskedLine:
    """One line of masked text, with what it takes to translate a column back.

    `offset` is where the line starts in the masked text and is what the prompt prints as
    its prefix. `tags` are the mask tags on this line as `(column, length, doc_start,
    doc_end)`: a flag overlapping one is refused, and the columns before and after are
    translated by walking them.

    `doc_offset` is where the line starts in the **document**. Both offsets are carried
    because neither is derivable from the other without the tags, and a line with no tags
    is the case where they happen to agree — which is exactly the case a test would pass
    while the arithmetic was wrong.
    """

    text: str
    offset: int
    doc_offset: int
    tags: tuple[tuple[int, int, int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class Flag:
    """One validated flag, in document coordinates.

    No text field, by construction and for `ErrorSpan`'s reason (DESIGN §5.5.1): the
    guarantee this type gives is that no surface form exists in the object, and a `surface`
    field added later would satisfy every annotation here while breaking the fact. What
    survives is a reference — resolvable by whoever holds the corpus, inert to anyone else
    (DESIGN §11.2).
    """

    doc_id: str
    phi_type: str
    start: int
    end: int
    score: float

    @property
    def key(self) -> tuple[str, int, int, str]:
        return (self.doc_id, self.start, self.end, self.phi_type)


@dataclass(frozen=True, slots=True)
class Refusal:
    """One refused flag: which document, and why. No position — see the module docstring."""

    doc_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class DocumentAudit:
    """One call's outcome. `flags` may be empty, and empty is a measurement.

    `{"flags": []}` from the agent means *this document was audited and nothing survived*;
    a document with no `DocumentAudit` at all means *this document was not audited*. The
    report distinguishes them, which is the rule this project applies everywhere: zero is a
    measurement, absent is not.
    """

    doc_id: str
    flags: tuple[Flag, ...]
    refused: tuple[Refusal, ...]


def _refuse(doc_id: str, reason: str) -> Refusal:
    return Refusal(doc_id=doc_id, reason=check_audit_refusal(reason))


def validate_flags(
    payload: object,
    *,
    doc_id: str,
    lines: Sequence[MaskedLine],
) -> DocumentAudit:
    """One call's return value -> validated flags in document coordinates.

    `payload` is what the agent returned, already JSON-decoded (`parse_response` does the
    decoding and refuses non-JSON as `malformed`). Everything refusable is refused with a
    reason and nothing is repaired.

    **Order here is the agent's; order in the file is sorted.** This value is the record of
    one call, so reordering it would be this function editing the call. `report()` sorts,
    because a file two rounds get diffed against needs an order that does not move when the
    model's does — the same split `write_errors()` makes for `errors.jsonl`.

    Raises `AuditError` only for caller bugs — an empty `lines`, or a `doc_id` that is not
    a non-empty string. A refused flag is never an exception, because the report has to be
    able to record that it happened.
    """
    if not isinstance(doc_id, str) or not doc_id:
        raise AuditError(
            f"doc_id must be a non-empty string, got {type(doc_id).__name__}. It is the "
            "orchestrator's — the agent never supplies it (auditor.md §1.3), so an "
            "absent one is a harness bug rather than a refusable flag."
        )
    if not lines:
        raise AuditError(
            "no masked lines were supplied for the document. An Auditor call over zero "
            "lines is a call that should not have been made; refusing its flags instead "
            "would record an agent mistake for a harness one."
        )

    if not isinstance(payload, Mapping) or set(payload) != {"flags"}:
        # One object with one key. A payload carrying prose beside its flags is the
        # fenced-response failure of `rule_author.md` §2 in this file's shape, and it is
        # one refusal for the call rather than one per flag — there are no flags to count.
        return DocumentAudit(doc_id, (), (_refuse(doc_id, MALFORMED),))
    raw = payload["flags"]
    if not isinstance(raw, list):
        return DocumentAudit(doc_id, (), (_refuse(doc_id, MALFORMED),))

    declared = set(axis("phi_type")) - _non_target_types()
    flags: list[Flag] = []
    refused: list[Refusal] = []
    for item in raw:
        outcome = _one_flag(item, doc_id=doc_id, lines=lines, declared=declared)
        (flags if isinstance(outcome, Flag) else refused).append(outcome)
    return DocumentAudit(doc_id, tuple(flags), tuple(refused))


def _one_flag(
    item: object,
    *,
    doc_id: str,
    lines: Sequence[MaskedLine],
    declared: set[str],
) -> Flag | Refusal:
    """One flag, validated in the order that makes each refusal mean what it says.

    Shape first, then the coordinate scheme, then the type, then the mask. The order
    matters for the *reason* recorded rather than for whether the flag survives: a flag
    with a string `line` and an `OTHER` type is `malformed`, because until the shape holds
    nothing else can be evaluated, and reporting it as `undeclared_phi_type` would send a
    reader looking at the vocabulary for a broken response.
    """
    if not isinstance(item, Mapping):
        return _refuse(doc_id, MALFORMED)
    if set(item) - FLAG_FIELDS or not {"line", "start", "end", "phi_type"} <= set(item):
        return _refuse(doc_id, MALFORMED)
    line_no, start, end = item["line"], item["start"], item["end"]
    if any(not isinstance(v, int) or isinstance(v, bool) for v in (line_no, start, end)):
        return _refuse(doc_id, MALFORMED)
    phi_type = item["phi_type"]
    if not isinstance(phi_type, str):
        return _refuse(doc_id, MALFORMED)
    score = item.get("score", 1.0)
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return _refuse(doc_id, MALFORMED)
    score = float(score)
    if not 0.0 <= score <= 1.0:
        # Refused rather than clamped: `score` is recorded and never thresholded
        # (`auditor.md` §2.1), so a clamped 1.7 would be an agent's confidence this
        # project made up.
        return _refuse(doc_id, MALFORMED)

    if not 0 <= line_no < len(lines):
        return _refuse(doc_id, OUT_OF_RANGE)
    line = lines[line_no]
    if end <= start:
        return _refuse(doc_id, CROSSES_A_LINE)
    if end > len(line.text):
        # Past the end of its own line. `crosses_a_line` rather than `out_of_range`, and
        # the distinction is what a reader does about it: the line existed and was sent, so
        # this is a flag whose span the line cannot contain (auditor.md §1.3 — a flag does
        # not cross a line boundary), not a coordinate outside the document.
        return _refuse(doc_id, CROSSES_A_LINE)
    if start < 0:
        return _refuse(doc_id, OUT_OF_RANGE)

    if phi_type not in declared:
        return _refuse(doc_id, UNDECLARED_PHI_TYPE)

    for col, length, _, _ in line.tags:
        if start < col + length and col < end:
            # Overlaps a replacement: a detection reported back to its own detector
            # (auditor.md §1.2). Refused even where the overlap is one character, because
            # a flag partly over a tag has no boundary the corpus can resolve.
            return _refuse(doc_id, INSIDE_A_MASK_TAG)

    return Flag(
        doc_id=doc_id, phi_type=phi_type, score=score,
        start=_to_document(line, start), end=_to_document(line, end),
    )


def _to_document(line: MaskedLine, column: int) -> int:
    """A column in the masked line -> an offset in the document.

    Walks the line's tags left to right, adding each tag's document length and subtracting
    the tag's own. Only tags that end at or before `column` are applied: a flag overlapping
    a tag was refused before this is reached, so a column inside a tag cannot arrive here.

    Not the inverse of the masker's own arithmetic re-derived — the tag carries its document
    span, so this reads the map rather than reconstructing it. A reconstruction would be a
    second implementation of the correspondence, and the two would disagree first on
    whichever document nobody checked (DESIGN §5.3's one-writer argument, applied to a
    coordinate).
    """
    offset = line.doc_offset
    consumed = 0
    for col, length, doc_start, doc_end in line.tags:
        if col + length > column:
            break
        offset += (col - consumed) + (doc_end - doc_start)
        consumed = col + length
    return offset + (column - consumed)


def parse_response(text: str, *, doc_id: str, lines: Sequence[MaskedLine]) -> DocumentAudit:
    """The agent's raw response -> a `DocumentAudit`. Non-JSON is `malformed`.

    `auditor.md` §2.1 requires the response to be one JSON object with no fence and no
    preamble, and nothing here strips a fence or repairs a key — for `rule_author.md` §2's
    reason: a response this module quietly repaired would make the format instruction
    advisory, and the arm would stop being able to report a format failure as one.

    The response text is not quoted in any exception or refusal. It contains the agent's
    own words about a masked document, and a `json.JSONDecodeError` re-raised with its
    context would carry a slice of that response into a log (CLAUDE.md).
    """
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return DocumentAudit(doc_id, (), (_refuse(doc_id, MALFORMED),))
    return validate_flags(payload, doc_id=doc_id, lines=lines)


def report(
    audits: Iterable[DocumentAudit],
    *,
    corpus: str,
    iteration: int,
    masked_from_iteration: int,
) -> dict:
    """The `audit_report.json` content — `auditor.md` §2.2's shape.

    Assembled here and written by the loop driver, which is `run_fold`'s division for
    `errors.jsonl`: one module decides what the record says, one place writes it.

    `masked_from_iteration` is on the file rather than left to a convention, because the
    report is an input to round *n* derived from round *n−1*'s predictions, and a directory
    listing does not say that. Validated as `iteration - 1` — the only value it can take
    (`auditor.md` banner), so a caller that passed the current round would be recording
    that the arm audited its own unwritten output.
    """
    if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 2:
        raise AuditError(
            f"iteration must be an integer >= 2, got {iteration!r}. The Auditor is called "
            "from round 2 onward (config/naming.yaml, agent_role): round 1 is shown the "
            "same blocks as port-oneshot (DESIGN §4) and has no predictions to mask."
        )
    if masked_from_iteration != iteration - 1:
        raise AuditError(
            f"masked_from_iteration must be {iteration - 1} at iteration {iteration}, got "
            f"{masked_from_iteration!r}. The report is an input to this round derived from "
            "the previous round's spans.jsonl, and a record that got this wrong would "
            "attribute a round's flags to the wrong predictions."
        )
    if corpus not in axis("corpus"):
        raise AuditError(
            f"{corpus!r} is not a corpus in config/naming.yaml (have: "
            f"{sorted(axis('corpus'))})."
        )

    audits = list(audits)
    seen: set[str] = set()
    for a in audits:
        if a.doc_id in seen:
            raise AuditError(
                f"two audits for one document ({a.doc_id!r}). One call per document "
                "(auditor.md §1.3), so a second is either a retry that should have "
                "replaced the first or two calls whose flags would be double-counted."
            )
        seen.add(a.doc_id)

    flags = [f for a in audits for f in a.flags]
    refused = [r for a in audits for r in a.refused]
    by_type: dict[str, int] = {}
    for f in flags:
        by_type[f.phi_type] = by_type.get(f.phi_type, 0) + 1
    by_reason: dict[str, int] = {}
    for r in refused:
        by_reason[r.reason] = by_reason.get(r.reason, 0) + 1

    return {
        "iteration": iteration,
        "masked_from_iteration": masked_from_iteration,
        "corpus": corpus,
        "documents_audited": len(audits),
        # Counted, not inferred from an empty `flags` list downstream: a document with no
        # flags and a document that was never audited are different facts, and this is the
        # number that says the first happened.
        "documents_with_no_flags": sum(1 for a in audits if not a.flags),
        "flags": [
            {"doc_id": f.doc_id, "phi_type": f.phi_type,
             "start": f.start, "end": f.end, "score": f.score}
            for f in sorted(flags, key=lambda f: f.key)
        ],
        "refused": [{"doc_id": r.doc_id, "reason": r.reason} for r in refused],
        "counts": {
            "flags": len(flags),
            "refused": len(refused),
            "by_phi_type": dict(sorted(by_type.items())),
            "by_refusal": dict(sorted(by_reason.items())),
        },
    }
