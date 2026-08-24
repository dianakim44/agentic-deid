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
number, a column, a length or a type — never a slice of the document.

**No corpus text reaches this module at all, and the types are what guarantee it.** The
guarantee used to be about the functions: none of them wrote or logged. That left
`MaskedLine.text` holding masked corpus text on a dataclass whose generated `repr` renders
it — the state DESIGN §3's "the masker returns `FilledPrompt` and never a `str`" exists to
prevent, reintroduced by the type the masker has to hand over. It bought one `len()` call.
So `MaskedLine` carries a geometry (`length`, `doc_offset`, `tags`) and the masked text stays
inside the masker behind the exits `prompt.EXITS` enumerates. `tests/test_audit.py` asserts
this over the *types* and not only over the functions, because a function-level assertion is
satisfied by a module that holds the text and merely has not printed it yet.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..corpora.base import (
    ROOT, CorpusError, axis, check_audit_refusal, path_template, round_path,
)

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
    caller passing a mask map this module cannot read — tags out of order, a tag past the
    end of its line, a document extent that is empty (`_check_tags`) — is a *bug*, and
    turning it into a refusal would file it under the agent's mistakes and leave the
    masker's defect looking like a bad round.
    """


def _check_tags(
    length: int,
    doc_offset: int,
    tags: tuple[tuple[int, int, int, int], ...],
) -> None:
    """Refuse a mask map that `_to_document` would misread. **Never sorts it.**

    `_to_document` walks the tags left to right and stops at the first one ending after the
    column it is translating. That walk is correct for tags ascending by column and
    non-overlapping, and *silently wrong* for any other order — given the same two tags
    reversed it returns a different offset and raises nothing.

    **Sorting here would be the wrong fix, and the reason is the direction the bug travels.**
    The masker applies replacements right-to-left (DESIGN §3), so its natural emission order
    is descending — the order that breaks this. A sort would repair that emission on every
    call and the masker would ship with the defect permanently hidden: a caller could emit
    tags in any order forever and no test, no run and no report would say so. A caller bug
    goes back to the caller, which is `AuditError`'s existing division — a refused *flag* is
    the agent's mistake and is data, a broken *mask map* is the harness's and is an
    exception.

    Every field is checked, not only the order, because each of these makes `_to_document`
    return a plausible number rather than fail: a tag past the end of its line means the walk
    consumes columns the line does not have, and a tag whose document extent is empty or
    inverted means the offset it contributes is a length nothing has.
    """
    if not isinstance(length, int) or isinstance(length, bool) or length < 0:
        raise AuditError(
            f"a masked line's length must be a non-negative integer, got {length!r}."
        )
    if not isinstance(doc_offset, int) or isinstance(doc_offset, bool) or doc_offset < 0:
        raise AuditError(
            f"a masked line's doc_offset must be a non-negative integer, got "
            f"{doc_offset!r}. It is where the line starts in the document."
        )
    previous_end = 0
    for index, tag in enumerate(tags):
        if not isinstance(tag, tuple) or len(tag) != 4:
            raise AuditError(
                f"mask tag {index} is not a 4-tuple (column, length, doc_start, doc_end); "
                f"got {type(tag).__name__} of length "
                f"{len(tag) if isinstance(tag, tuple) else '?'}."
            )
        col, tag_length, doc_start, doc_end = tag
        if any(not isinstance(v, int) or isinstance(v, bool) for v in tag):
            raise AuditError(f"mask tag {index} has a non-integer field.")
        if col < 0 or tag_length <= 0:
            raise AuditError(
                f"mask tag {index} has column {col} and length {tag_length}. A tag occupies "
                "at least one column at a non-negative one."
            )
        if col + tag_length > length:
            raise AuditError(
                f"mask tag {index} ends at column {col + tag_length} on a line of "
                f"{length} characters. A tag past the end of its line makes "
                "_to_document consume columns the line does not have, and the offset it "
                "returns is wrong rather than absent."
            )
        if doc_end <= doc_start or doc_start < 0:
            raise AuditError(
                f"mask tag {index} spans document [{doc_start}, {doc_end}), which is empty, "
                "inverted or negative. It stands for at least one document character — that "
                "is what makes it a replacement."
            )
        if col < previous_end:
            raise AuditError(
                f"mask tag {index} starts at column {col}, before tag {index - 1} ends at "
                f"{previous_end}. Tags must be ascending by column and non-overlapping: "
                "_to_document walks them once and stops at the first tag ending after its "
                "column, so any other order returns a different offset and raises nothing. "
                "Not sorted here — the masker emits right-to-left (DESIGN §3), so a sort "
                "would hide exactly the order this check exists to catch."
            )
        previous_end = col + tag_length


@dataclass(frozen=True, slots=True)
class MaskedLine:
    """One masked line's *geometry*. **No text**, and that is the whole design of the type.

    `length` is how many characters the masked line has, `doc_offset` is where the line
    starts in the **document**, and `tags` are the mask tags on the line as `(column,
    length, doc_start, doc_end)`.

    **The masker returns `FilledPrompt` and never a `str`** (DESIGN §3), and a `text: str`
    field here would have handed masked corpus text to this module through the back door —
    on a dataclass whose generated `repr` renders it, which is precisely the state
    `FilledPrompt` exists to prevent. It bought one `len()`. So the masked text stays inside
    the masker and what crosses the boundary is a geometry: enough to validate coordinates
    and translate them, and nothing that could be quoted.

    The masked-text offset of the line is *not* carried either. It is what the prompt prints
    as a line's prefix, so it belongs to the renderer; this module never needed it, and a
    field nothing reads is a field the next caller populates wrongly with nobody noticing.

    **`tags` must be ascending by column and non-overlapping, and construction refuses
    anything else** — see `_check_tags`. `doc_offset` is carried rather than derived because
    it is not derivable from a masked offset without the tags, and a line with no tags is
    the case where the two would agree, which is exactly the case a test passes while the
    arithmetic is wrong.
    """

    length: int
    doc_offset: int
    tags: tuple[tuple[int, int, int, int], ...] = ()

    def __post_init__(self) -> None:
        _check_tags(self.length, self.doc_offset, self.tags)


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
    if end > line.length:
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

    **The single pass is why `MaskedLine` validates tag order at construction.** The loop
    stops at the first tag ending after `column`, which is correct for ascending
    non-overlapping tags and silently wrong for anything else. That precondition is checked
    by `_check_tags` and not restored by a sort here — see its docstring.

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


def report_path(
    *, corpus: str, detector: str, supervision: str, porting: str, iteration: int,
    root: Path | None = None,
) -> Path:
    """`paths.auditreport` for one round — `iter{N}/audit_report.json`.

    **Why this needs a builder at all, when `orchestrate._arm_path` exists.** That helper
    formats the four axes and nothing else, because `{iteration}` is not an axis — DESIGN §4
    refused a fifth path component and §5.5 put the round in a *directory* instead, so a
    round-scoped path is a different template rather than a wider call. `run_fold.errors_path`
    and `scorer.iter_metrics_path` are the same shape for the same reason, and this is the
    round's fourth file.

    Here rather than in the loop driver, which is the division `report()` already follows: one
    module decides what the record says and where it goes, one place writes it. A driver that
    built this path itself would be the second definition site of the location, and the file
    it would misplace is the one `paths.auditreport` is deny-listed for being — a map of the
    identifiers a round did not catch.

    Keyword axes and no run block, for `errors_path`'s reason. The interested party is the
    driver, which holds four axes and a round; it never assembles a run block, and handing it
    one to fill would make it a second assembler of the record.

    The round is validated for being a round but **not** for being ≥ 2. `report()` does that,
    because that constraint is about the *content* — the Auditor is called from round 2 onward
    and a report claiming round 1 would be attributing flags to predictions that do not exist
    — whereas this function answers "where does round N's report go". Checking it in both
    places would put a rule about the Auditor's schedule in a path builder, where the next
    round-scoped file would inherit it.
    """
    return round_path(
        "auditreport", iteration=iteration, artefact="audit report", error=AuditError,
        root=root, corpus=corpus, detector=detector, supervision=supervision,
        porting=porting,
    )


def draw_path(
    *, corpus: str, detector: str, supervision: str, porting: str, iteration: int, draw: int,
    root: Path | None = None,
) -> Path:
    """`paths.auditdraw` — one *draw*'s audit report, at `iter{N}/draw{M}/audit_report.json`.

    **What a draw is.** An incomplete round may be re-run (DESIGN §5.5.2): round 5 of
    `port-loop` died twice on the RuleAuthor call after all 250 Auditor calls had completed, and
    refusing the re-run would have let a transport timeout end the arm. Each attempt audits the
    fold again, so a round that was attempted three times made 750 Auditor calls and wrote three
    reports to one path — two of which no longer exist. `agent_calls.jsonl` and
    `audit_report.json` then disagree about how many times the round was audited, and the log is
    the one that is right.

    This path is where each draw's report goes so that none of them is overwritten. `report_path`
    keeps its meaning unchanged — the latest draw, which is what the next round reads — and the
    accounting is here.

    **The filename is `audit_report.json` and the round number gets a subdirectory, not a
    suffix.** `tools/release_screen.py` deny-lists this file *by name*, deliberately, so that
    `metrics.json` and `spans.jsonl` in the same directory stay publishable. A draw at
    `audit_report.draw2.json` would not match that pattern, and the fix would be to widen a deny
    rule so that it covers a newly-invented name — an edit in the wrong direction, made under
    time pressure, on the rule protecting a map of the identifiers a round failed to catch. A
    subdirectory inherits the protection with no screener change at all, and
    `tests/test_audit.py` asserts that the screener refuses this path.

    **Built from `paths.auditdraw` and validated by delegating to `report_path` first.** The
    axis and round checks are `round_path`'s and are not reimplemented here; calling
    `report_path` for its refusals is what guarantees the two templates cannot disagree about
    which cell they name. `{draw}` is the one component `round_path` could not check, because it
    is a sequence number and not a closed vocabulary — the same reason `iteration` is exempt
    there — so it is checked here, in the same shape and with `bool` excluded for the same
    reason: `True` is an `int` and would silently name draw 1.
    """
    report_path(corpus=corpus, detector=detector, supervision=supervision,
                porting=porting, iteration=iteration, root=root)
    if not isinstance(draw, int) or isinstance(draw, bool) or draw < 1:
        raise AuditError(
            f"draw must be an integer >= 1, got {draw!r}. It is a path component "
            "(paths.auditdraw) and the sequence of a round's draws is what reconciles the "
            "audit report against the call log — a draw written to draw0/ is an audit nothing "
            "counts (DESIGN §5.5.2)."
        )
    return (root or ROOT) / path_template("auditdraw").format(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        iteration=iteration, draw=draw,
    )


def next_draw(
    *, corpus: str, detector: str, supervision: str, porting: str, iteration: int,
    root: Path | None = None,
) -> int:
    """Which draw the next audit of this round is — 1 if the round has never been audited.

    Counted from the draw directories that exist rather than from a stored counter, for the
    reason `loop.run_iteration` reads its history off disk instead of taking it as an argument:
    an attempt that died left no chance to update a counter, and a counter that is only correct
    when the process exits cleanly is a counter that is wrong exactly when it matters.

    **Counted as "one past the highest existing draw", not as "how many exist".** The two differ
    if a draw directory is ever missing, and in that case the count would silently reuse a number
    and overwrite a preserved report — the one failure this whole path exists to prevent. A gap is
    left as a gap; it is visible in the directory listing and in the mismatch against
    `draws_total`, which is the honest outcome.
    """
    first = draw_path(corpus=corpus, detector=detector, supervision=supervision,
                      porting=porting, iteration=iteration, draw=1, root=root)
    round_dir = first.parent.parent
    if not round_dir.is_dir():
        return 1
    highest = 0
    for child in round_dir.iterdir():
        if child.is_dir() and child.name.startswith("draw"):
            suffix = child.name[len("draw"):]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return highest + 1


def with_draws_total(report: Mapping, total: int) -> dict:
    """`report` plus `draws_total`, placed immediately after `draw_index`.

    The canonical `audit_report.json` carries the total and each preserved `draw{M}/` copy does
    not (`report`'s docstring), so exactly one key separates the two payloads and this is where
    it is added. A `{**report, "draws_total": total}` at the call site would work and would put
    the key last in the file, several hundred flags away from the `draw_index` it is only
    readable beside — these two fields answer one question and a reader should not have to
    scroll between them.

    Validated by rebuilding through `report`'s own rule rather than by repeating it: `total`
    below `draw_index` is the one inconsistency visible inside a single file, and it is refused
    here too so that this function cannot be used to construct what `report` refuses.
    """
    index = report.get("draw_index")
    if not isinstance(total, int) or isinstance(total, bool) or total < 1:
        raise AuditError(
            f"draws_total must be an integer >= 1, got {total!r}. It is how many times this "
            "round was audited, and the count is what reconciles the report against the call "
            "log (DESIGN §5.5.2)."
        )
    if not isinstance(index, int) or total < index:
        raise AuditError(
            f"this report says draw_index={index!r} and was given draws_total={total}: a "
            "report cannot be draw " f"{index!r} of {total}. Pass a report from `report()` and "
            "the total known at the moment the canonical copy is written."
        )
    out: dict = {}
    for key, value in report.items():
        out[key] = value
        if key == "draw_index":
            out["draws_total"] = total
    return out


def report(
    audits: Iterable[DocumentAudit],
    *,
    corpus: str,
    iteration: int,
    masked_from_iteration: int,
    draw_index: int = 1,
    draws_total: int | None = None,
) -> dict:
    """The `audit_report.json` content — `auditor.md` §2.2's shape.

    Assembled here and written by the loop driver, which is `run_fold`'s division for
    `errors.jsonl`: one module decides what the record says, one place writes it.

    `masked_from_iteration` is on the file rather than left to a convention, because the
    report is an input to round *n* derived from round *n−1*'s predictions, and a directory
    listing does not say that. Validated as `iteration - 1` — the only value it can take
    (`auditor.md` banner), so a caller that passed the current round would be recording
    that the arm audited its own unwritten output.

    **`draw_index` and `draws_total` reconcile this file against `agent_calls.jsonl`**
    (DESIGN §5.5.2, 2026-08-24). A re-run round audits the fold again, so the log holds
    250 × M Auditor lines while one report exists; these two fields plus `paths.auditdraw`
    are what account for all M.

    `draw_index` is which attempt produced this report and is always written, because it is
    always knowable: the driver is the thing that re-ran. It defaults to 1 rather than being
    required, and the default is the true state of every round audited once — which is every
    round in the record before this date — so a default here is a fact and not a placeholder.

    **`draws_total` is optional and its absence is a record, not an omission.** At the moment
    draw 2's report is written, nobody knows whether there will be a draw 3, and the three ways
    to make the field always-present are all worse than leaving it out. Writing
    `draws_total: 2` into a round that turns out to have three draws publishes a false count.
    Going back to re-stamp preserved reports makes something a second writer of an already
    published file, which is what DESIGN §5.5's one-writer rule refuses and the reason the two
    copies of a round's score can be trusted. And deriving it at read time from the directory
    listing puts the count somewhere no reader is obliged to look.

    So the convention is `caching`'s, one file over: the field is present where it is
    knowable and absent where it is not, and **its presence means this report is the latest
    draw**. `loop.run_iteration` writes the preserved copy at `paths.auditdraw` without it and
    the canonical copy at `paths.auditreport` with it, rewriting the canonical copy on every
    draw — so the canonical `draws_total` is correct at every instant and is the true total once
    the round completes. A reader then has two independent counts to check against each other:
    this field, and the number of `draw{M}/` directories.

    `draws_total` is refused when it is below `draw_index`, which is the one inconsistency
    checkable from inside a single file — a report claiming to be draw 3 of 2.
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
    if not isinstance(draw_index, int) or isinstance(draw_index, bool) or draw_index < 1:
        raise AuditError(
            f"draw_index must be an integer >= 1, got {draw_index!r}. It is which attempt at "
            "this round produced the report, and it is what reconciles the report against the "
            "250 × M Auditor lines in agent_calls.jsonl (DESIGN §5.5.2)."
        )
    if draws_total is not None:
        if not isinstance(draws_total, int) or isinstance(draws_total, bool):
            raise AuditError(
                f"draws_total must be an integer or None, got {draws_total!r}. None is how a "
                "superseded draw records that the total was not knowable when it was written, "
                "and it is the value every preserved draw carries."
            )
        if draws_total < draw_index:
            raise AuditError(
                f"draws_total is {draws_total} and draw_index is {draw_index}: a report cannot "
                "be draw " f"{draw_index} of {draws_total}. This is the one inconsistency "
                "visible from inside a single file, so it is refused here rather than left for "
                "a reader to notice."
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
        # Which attempt at this round wrote the report, and how many there were in total —
        # omitted when the total was not knowable, which is every preserved draw. See the
        # docstring; the two fields are adjacent because neither is readable without the other.
        "draw_index": draw_index,
        **({"draws_total": draws_total} if draws_total is not None else {}),
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
