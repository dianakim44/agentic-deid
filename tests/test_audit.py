"""The Auditor's flag validator (`docs/prompts/auditor.md` §2, DESIGN §5.5).

The prompt promised five refusal reasons and a coordinate translation before any code
existed. This file is what makes the promise checkable, and it is written now rather than
with the loop driver for the reason the vocabulary is in `naming.yaml` rather than in the
module: a specification running ahead of its implementation is a state this project has
been bitten by, and the window is frozen against `auditor.md`'s bytes — so what the prompt
says the validator does has to be what the validator does.

Three properties dominate the file:

  - **Refused, not repaired, and counted.** Every refusal is a reason from the config and a
    flag that does not appear in `flags`. Nothing is snapped, clamped or dropped silently.
  - **The translation is the masker's map, read, not re-derived.** A column before a tag,
    after a tag, and between two tags each translate differently, and a test that only used
    a tag-free line would pass while the arithmetic was wrong.
  - **No surface form on any path.** Asserted over the syntax tree as well as over values,
    because this module holds the masked and the unmasked coordinates at once and is the one
    place a debugging write would publish both.

    python3 -m pytest tests/test_audit.py -q
"""
from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest

from src.corpora import base
from src.corpora.base import CorpusError, audit_refusals, check_audit_refusal
from src.porting import audit
from src.porting.audit import (
    CROSSES_A_LINE, INSIDE_A_MASK_TAG, MALFORMED, OUT_OF_RANGE, UNDECLARED_PHI_TYPE,
    AuditError, DocumentAudit, Flag, MaskedLine, parse_response, report, report_path,
    validate_flags,
)

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "porting" / "audit.py"


@pytest.fixture(autouse=True)
def _clear_caches():
    base.naming.cache_clear()
    yield
    base.naming.cache_clear()


# ─── the vocabulary ──────────────────────────────────────────────────────────


def test_the_five_reasons_are_the_declared_ones():
    """Written out rather than read from the config, which would compare it to itself.

    Five because `auditor.md` §2.3 tabulates five, and the prompt is hashed into the freeze
    record: a sixth reason appearing in code without a prompt edit would be a refusal the
    frozen window does not describe.
    """
    assert set(audit_refusals()) == {
        "out_of_range", "inside_a_mask_tag", "undeclared_phi_type", "crosses_a_line",
        "malformed",
    }


def test_every_reason_carries_a_description():
    for reason, description in audit_refusals().items():
        assert isinstance(description, str) and description.strip(), reason


def test_the_module_constants_are_the_declared_reasons():
    """The module spells each reason once; this is what keeps that spelling honest.

    `src/termination.py` does the same with `termination_reason`, for the same failure: a
    branch holding `"out-of-range"` writes a value no reader can group by, and the file is
    well-formed JSON throughout.
    """
    assert {OUT_OF_RANGE, INSIDE_A_MASK_TAG, UNDECLARED_PHI_TYPE, CROSSES_A_LINE,
            MALFORMED} == set(audit_refusals())


@pytest.mark.parametrize("bad", ["out-of-range", "OUT_OF_RANGE", "outofrange", "", "other"])
def test_a_reason_outside_the_vocabulary_is_refused(bad):
    with pytest.raises(CorpusError, match="not an audit refusal reason"):
        check_audit_refusal(bad)


def test_a_missing_block_is_refused_rather_than_defaulted(monkeypatch):
    base.naming.cache_clear()
    monkeypatch.setattr(base, "naming", lambda: {})
    with pytest.raises(CorpusError, match="no `audit_refusal` mapping"):
        audit_refusals()


@pytest.mark.parametrize("block", [{}, {"": "empty key"}, {1: "not a string"}])
def test_a_malformed_block_is_refused(monkeypatch, block):
    base.naming.cache_clear()
    monkeypatch.setattr(base, "naming", lambda: {"audit_refusal": block})
    with pytest.raises(CorpusError):
        audit_refusals()


# ─── helpers ─────────────────────────────────────────────────────────────────


def line(shape: int | str, *, doc_offset: int = 0, tags=()) -> MaskedLine:
    """A `MaskedLine` from a length, or from a string that is **measured and discarded**.

    `MaskedLine` carries no text (see the module and the structural tests), and a bare
    integer is what the masker will pass. The string form exists only so that a test showing
    `"ab [NAME] cd"` can show where its tag sits; `len()` is taken here and the characters go
    no further, which is the same reduction the masker performs at its own boundary.
    """
    length = shape if isinstance(shape, int) else len(shape)
    return MaskedLine(length=length, doc_offset=doc_offset, tags=tuple(tags))


def flag(**kw) -> dict:
    base_flag = {"line": 0, "start": 0, "end": 4, "phi_type": "NAME", "score": 0.5}
    base_flag.update(kw)
    return base_flag


def one(payload_flag: dict, lines=None) -> DocumentAudit:
    return validate_flags(
        {"flags": [payload_flag]}, doc_id="d1",
        lines=lines or [line("0123456789")],
    )


def refusals(result: DocumentAudit) -> list[str]:
    return [r.reason for r in result.refused]


# ─── what survives ───────────────────────────────────────────────────────────


def test_a_well_formed_flag_survives_and_carries_document_offsets():
    result = one(flag(start=2, end=6), [line("0123456789", doc_offset=100)])
    assert result.refused == ()
    assert result.flags == (Flag(doc_id="d1", phi_type="NAME", start=102, end=106,
                                score=0.5),)


def test_an_empty_flag_list_is_a_measurement_not_an_absence():
    """`{"flags": []}` is *audited and nothing survived*, which is not *not audited*.

    The distinction is the report's, and it is only available if this function accepts an
    empty list rather than treating it as a defective response — the same rule as
    `model_id_absent`'s `none` and the cost block's zeros.
    """
    result = validate_flags({"flags": []}, doc_id="d1", lines=[line("abc")])
    assert result == DocumentAudit("d1", (), ())


def test_score_is_recorded_and_never_thresholded():
    """Every flag travels; the number travels with it (`auditor.md` §2.1).

    A build-time threshold would be a tuned parameter on an unlabelled signal that silently
    changed what the RuleAuthor is shown, so a 0.01 flag has to arrive intact.
    """
    result = validate_flags(
        {"flags": [flag(score=0.01), flag(start=5, end=7, score=1.0)]},
        doc_id="d1", lines=[line("0123456789")])
    assert [f.score for f in result.flags] == [0.01, 1.0]
    assert result.refused == ()


def test_a_missing_score_defaults_and_the_default_is_not_a_refusal():
    """`score` is the one optional field (`auditor.md` §2.1 lists four required plus it)."""
    result = one({"line": 0, "start": 0, "end": 3, "phi_type": "NAME"})
    assert result.refused == ()
    assert result.flags[0].score == 1.0


def test_the_order_of_one_call_is_the_agent_s():
    """This value is the record of a call, so reordering it would be code editing the call.

    `report()` sorts instead — the same split `write_errors()` makes, and the reason the two
    are different functions.
    """
    result = validate_flags(
        {"flags": [flag(start=6, end=9), flag(start=0, end=3)]},
        doc_id="d1", lines=[line("0123456789")])
    assert [f.start for f in result.flags] == [6, 0]


# ─── the coordinate translation ──────────────────────────────────────────────


def test_a_column_before_a_tag_translates_by_the_line_offset_alone():
    masked = line("ab [NAME] cd", doc_offset=50, tags=[(3, 6, 53, 61)])
    result = one(flag(start=0, end=2), [masked])
    assert (result.flags[0].start, result.flags[0].end) == (50, 52)


def test_a_column_after_a_tag_translates_by_the_tag_s_document_length():
    """The case a tag-free test cannot see.

    `[NAME]` is 6 characters standing for 8 in the document, so a column after it is 2
    further along than the masked text suggests. A translation that ignored the tag would
    be short by exactly the difference — and would be right on every line with no tags,
    which is most lines.
    """
    masked = line("ab [NAME] cd", doc_offset=50, tags=[(3, 6, 53, 61)])
    result = one(flag(start=10, end=12), [masked])
    assert (result.flags[0].start, result.flags[0].end) == (52 + 10, 52 + 12)


def test_a_column_between_two_tags_applies_only_the_tags_before_it():
    """Two tags, and a column after each. The masked line is

        col   0-----5 6 7 8 9----14 15 16
              [NAME]  _ x _ [DATE]   _ y

    against a document where `[NAME]` stands for 20 characters (0-20) and `[DATE]` for 10
    (23-33). So `x` at column 7 is document 21, and `y` at column 16 is document 34 — the
    tags before a column are applied and the ones after it are not.
    """
    masked = line("[NAME] x [DATE] y", doc_offset=0,
                  tags=[(0, 6, 0, 20), (9, 6, 23, 33)])
    # column 7 ('x'): 0 + (20-0) + (7-6) = 21
    assert one(flag(start=7, end=8), [masked]).flags[0].start == 21
    # column 16 ('y'): 21 walked on through the second tag — 20 + (9-6) + (33-23) + 1 = 34
    assert one(flag(start=16, end=17), [masked]).flags[0].start == 34


def test_the_translation_uses_the_document_offset_and_not_the_masked_one():
    """`doc_offset` is carried and the masked-text offset is not carried at all.

    The masked offset is what the prompt prints as a line's prefix, so it belongs to the
    renderer. The two differ by every tag on every earlier line, and a translation that used
    the printed prefix would drift further down the document with each tag — which is why the
    field this module needs is the document one, and why the other is absent rather than
    present and unread.
    """
    masked = line("abcd", doc_offset=40)
    assert one(flag(start=1, end=3), [masked]).flags[0].start == 41
    assert set(MaskedLine.__slots__) == {"length", "doc_offset", "tags"}


def test_a_flag_on_a_later_line_uses_that_line_s_offsets():
    """The third line's tag fills it, which is why its text is `[NAME]` and not `third`.

    Written as `line("third", tags=[(0, 6, 20, 30)])` when the tag map was unchecked — a
    6-character tag on a 5-character line, wrong and inert, since nothing translated a column
    on that line. `_check_tags` refuses it now, and this is the class of latent inconsistency
    the check exists for: it did no harm here and would have on the line a flag landed on.
    """
    lines = [line("first", doc_offset=0), line("second", doc_offset=6),
             line("[NAME]", doc_offset=20, tags=[(0, 6, 20, 30)])]
    result = validate_flags({"flags": [flag(line=1, start=0, end=6)]},
                            doc_id="d1", lines=lines)
    assert (result.flags[0].start, result.flags[0].end) == (6, 12)


# ─── what is refused ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad_line", [1, 2, 99, -1])
def test_a_line_outside_what_was_sent_is_out_of_range(bad_line):
    assert refusals(one(flag(line=bad_line))) == [OUT_OF_RANGE]


def test_a_negative_column_is_out_of_range():
    assert refusals(one(flag(start=-1, end=2))) == [OUT_OF_RANGE]


def test_a_span_past_the_end_of_its_line_crosses_a_line():
    """`crosses_a_line`, not `out_of_range`, and the difference is what a reader does.

    The line existed and was sent, so this is a flag whose span that line cannot contain —
    `auditor.md` §1.3's "a flag does not cross a line boundary" — rather than a coordinate
    outside the document.
    """
    assert refusals(one(flag(start=8, end=20), [line("0123456789")])) == [CROSSES_A_LINE]


@pytest.mark.parametrize("start,end", [(5, 5), (6, 3)])
def test_an_empty_or_inverted_span_crosses_a_line(start, end):
    assert refusals(one(flag(start=start, end=end))) == [CROSSES_A_LINE]


def test_a_flag_inside_a_mask_tag_is_refused():
    """A detection reported back to its own detector (`auditor.md` §1.2)."""
    masked = line("ab [NAME] cd", tags=[(3, 6, 3, 11)])
    assert refusals(one(flag(start=3, end=9), [masked])) == [INSIDE_A_MASK_TAG]


@pytest.mark.parametrize("start,end", [(2, 5), (8, 11), (0, 12), (4, 5)])
def test_any_overlap_with_a_tag_is_refused_not_only_containment(start, end):
    """One character of overlap is enough, and that is deliberate.

    A flag partly over a tag has no boundary the corpus can resolve — the part inside the
    tag corresponds to a span that was replaced, so translating its end would give an
    offset inside the *detected* span. Refusing is the only answer that does not invent a
    boundary.
    """
    masked = line("ab [NAME] cd", tags=[(3, 6, 3, 11)])
    assert refusals(one(flag(start=start, end=end), [masked])) == [INSIDE_A_MASK_TAG]


def test_a_flag_touching_a_tag_boundary_is_not_refused():
    """Half-open, so ending exactly where a tag starts is not overlap.

    The complement of the test above: a rule that refused adjacency would refuse the
    common case of a missed identifier sitting immediately before a masked one.
    """
    masked = line("ab [NAME] cd", tags=[(3, 6, 3, 11)])
    assert refusals(one(flag(start=0, end=3), [masked])) == []
    assert refusals(one(flag(start=9, end=12), [masked])) == []


def test_an_undeclared_phi_type_is_refused():
    assert refusals(one(flag(phi_type="PATIENT_NAME"))) == [UNDECLARED_PHI_TYPE]


def test_other_is_refused_and_is_read_from_the_config_gloss():
    """`OTHER` is declared in `naming.yaml` as not a rule-development target.

    Read from the gloss for `sample.non_target_types()`'s reason — a second copy of
    `{"OTHER"}` in Python is a second thing to keep in sync, and a corpus shipping another
    residual bucket declares it the same way and is excluded without an edit here. A flag
    the RuleAuthor may not act on (`rule_author.md` Prohibition 4) is prompt space spent
    for nothing.
    """
    assert refusals(one(flag(phi_type="OTHER"))) == [UNDECLARED_PHI_TYPE]


@pytest.mark.parametrize("payload", [
    "not a mapping", 42, None, [],
    {},                                       # no `flags` key
    {"flags": []} | {"note": "looks clean"},   # prose beside the flags
    {"flags": "NAME at line 3"},
])
def test_a_payload_that_is_not_one_object_with_one_key_is_malformed(payload):
    result = validate_flags(payload, doc_id="d1", lines=[line("abc")])
    assert result.flags == ()
    assert refusals(result) == [MALFORMED]


@pytest.mark.parametrize("item", [
    "NAME at 3", 7, None, [0, 1, 4, "NAME"],
    {"line": 0, "start": 0, "end": 4},                        # no phi_type
    {"line": 0, "start": 0, "phi_type": "NAME"},              # no end
    {"start": 0, "end": 4, "phi_type": "NAME"},               # no line
    {"line": "0", "start": 0, "end": 4, "phi_type": "NAME"},  # line as a string
    {"line": 0, "start": 0.5, "end": 4, "phi_type": "NAME"},  # column as a float
    {"line": True, "start": 0, "end": 4, "phi_type": "NAME"},  # bool is not an int here
    {"line": 0, "start": 0, "end": 4, "phi_type": 5},
    {"line": 0, "start": 0, "end": 4, "phi_type": "NAME", "score": "high"},
    {"line": 0, "start": 0, "end": 4, "phi_type": "NAME", "score": 1.7},
    {"line": 0, "start": 0, "end": 4, "phi_type": "NAME", "score": -0.1},
])
def test_a_flag_of_the_wrong_shape_is_malformed(item):
    assert refusals(one(item)) == [MALFORMED]


@pytest.mark.parametrize("extra", ["reason", "note", "evidence", "snippet", "context",
                                   "surface", "text", "comment"])
def test_an_unknown_field_is_refused_rather_than_ignored(extra):
    """**§3's prohibition, enforced.** The field most likely to be added carries the text.

    `auditor.md` §3 removes every free-text field because any justification for a span is a
    description of that span's text and the shortest honest one is a quotation. An ignored
    key would let the next version of the prompt — or a model being helpful — put the
    surface form of a residual identifier into a file under `results/` in a public
    repository. Refused on the day it appears, which is `write_errors()`'s whitelist rule
    (DESIGN §5.5.1) in the place where it matters most.
    """
    assert refusals(one(flag(**{extra: "Dr. Ejemplo Apellido"}))) == [MALFORMED]


def test_a_refused_flag_records_no_position():
    """Not even the coordinates it claimed. See the module docstring.

    Half of these refusals *are* the judgement that the position cannot be trusted, and a
    recorded untrustworthy position would pass for part of the residual-identifier map
    `paths.auditreport` is deny-listed for being.
    """
    result = one(flag(line=99, start=1841, end=1859))
    (refusal,) = result.refused
    assert set(vars(type(refusal))["__slots__"]) == {"doc_id", "reason"}
    body = json.dumps({"doc_id": refusal.doc_id, "reason": refusal.reason})
    assert "1841" not in body and "1859" not in body


def test_the_good_flags_in_a_mixed_payload_survive():
    """One bad flag does not lose the call. Per-flag refusal is why `refused` is a list."""
    result = validate_flags(
        {"flags": [flag(start=0, end=3), flag(line=42), flag(start=5, end=8)]},
        doc_id="d1", lines=[line("0123456789")])
    assert [f.start for f in result.flags] == [0, 5]
    assert refusals(result) == [OUT_OF_RANGE]


def test_nothing_is_repaired():
    """The refusals above are the whole answer: no clamping, no snapping, no coercion.

    Stated as its own test because each individual refusal above is also consistent with a
    validator that repaired *some* other case. A flag that arrives out of range must not
    come back in range.
    """
    result = validate_flags(
        {"flags": [flag(start=8, end=99), flag(line=5), flag(score=2.0)]},
        doc_id="d1", lines=[line("0123456789")])
    assert result.flags == ()
    assert len(result.refused) == 3


# ─── caller bugs are exceptions, not refusals ────────────────────────────────


@pytest.mark.parametrize("bad", ["", None, 7])
def test_a_missing_doc_id_is_a_harness_bug(bad):
    """`doc_id` is the orchestrator's — the agent never supplies it (`auditor.md` §1.3).

    So an absent one cannot be an agent mistake, and filing it under `refused` would record
    a harness failure as one of the model's.
    """
    with pytest.raises(AuditError, match="doc_id"):
        validate_flags({"flags": []}, doc_id=bad, lines=[line("abc")])


def test_a_call_over_zero_lines_is_a_harness_bug():
    with pytest.raises(AuditError, match="zero lines"):
        validate_flags({"flags": []}, doc_id="d1", lines=[])


# ─── the mask map is a checked contract, not a sorted one ────────────────────


def test_tags_out_of_order_are_refused_and_not_sorted():
    """**The mutation-worthy one.** A sort here would hide the masker's likeliest bug.

    The masker applies replacements right-to-left (DESIGN §3), so descending emission is its
    *natural* order and precisely the one `_to_document`'s single left-to-right pass reads
    wrongly. Measured before the check existed: the same two tags reversed translated column
    5 to 5 instead of 12, with no error and no symptom.

    A sort would have made that call correct and every future one silently uncontracted — the
    masker could emit in any order forever and nothing would say so. A caller bug goes back
    to the caller.
    """
    ascending = [(0, 3, 0, 10), (8, 3, 15, 40)]
    assert MaskedLine(length=13, doc_offset=0, tags=tuple(ascending)).tags[0][0] == 0
    with pytest.raises(AuditError, match="ascending by column"):
        MaskedLine(length=13, doc_offset=0, tags=tuple(reversed(ascending)))


def test_the_refusal_says_why_it_is_not_a_sort():
    """The message carries the argument, because the fix a reader reaches for is `sorted()`.

    A message naming only the symptom ("tags out of order") invites exactly the repair that
    disables the check.
    """
    with pytest.raises(AuditError) as excinfo:
        MaskedLine(length=13, doc_offset=0, tags=((8, 3, 15, 40), (0, 3, 0, 10)))
    message = str(excinfo.value)
    assert "Not sorted here" in message
    assert "right-to-left" in message


@pytest.mark.parametrize("tags", [
    ((0, 5, 0, 10), (3, 4, 12, 20)),      # second starts inside the first
    ((0, 5, 0, 10), (4, 4, 12, 20)),      # one column of overlap
    ((0, 5, 0, 10), (0, 5, 12, 20)),      # identical columns
])
def test_overlapping_tags_are_refused(tags):
    """Two tags cannot share a column: the masked text has one character there.

    Overlapping tags make `_to_document` double-count the shared columns, so the offsets it
    returns are wrong by the overlap — a number, not a failure. And the input that produces
    them is a masker that emitted a tag per overlapping *span* instead of one per union
    (DESIGN §3), which is the mistake this check will actually meet.
    """
    with pytest.raises(AuditError, match="before tag"):
        MaskedLine(length=20, doc_offset=0, tags=tags)


def test_adjacent_tags_are_not_refused():
    """The guard from the other side, so the fix cannot become "refuse anything touching".

    Adjacency is the common case, not the exotic one: es-meddocan's dev fold has 393 gold
    pairs separated by one character or none (DESIGN §3), so tag-abutting-tag is ordinary and
    a check that refused it would refuse ordinary documents.
    """
    masked = MaskedLine(length=11, doc_offset=100,
                        tags=((0, 3, 100, 110), (3, 3, 110, 125)))
    result = one(flag(line=0, start=6, end=9), [masked])
    assert result.refused == ()
    assert result.flags[0].start == 125


@pytest.mark.parametrize("tags", [
    ((0, 6, 0, 10),),                     # ends exactly one past
    ((3, 3, 0, 10),),                     # ends past
    ((5, 1, 0, 10),),                     # starts at the end
])
def test_a_tag_past_the_end_of_its_line_is_refused(tags):
    """A tag the line cannot contain, which `_to_document` reads as columns to consume.

    Latent rather than loud: it does nothing until a flag lands on that line, and then it
    returns an offset that is wrong rather than absent. One of the fixtures in this file was
    this shape before the check existed.
    """
    with pytest.raises(AuditError, match="on a line of"):
        MaskedLine(length=5, doc_offset=0, tags=tags)


@pytest.mark.parametrize("tags", [
    ((0, 3, 10, 10),),                    # empty document extent
    ((0, 3, 20, 10),),                    # inverted
    ((0, 3, -1, 10),),                    # negative
])
def test_a_tag_standing_for_no_document_text_is_refused(tags):
    """A tag replaces at least one document character — that is what makes it a replacement.

    An empty extent contributes 0 to the walk, so every column after it translates short by
    the tag's own width, and `Span.__post_init__` refuses the same shape one file over.
    """
    with pytest.raises(AuditError, match="document"):
        MaskedLine(length=10, doc_offset=0, tags=tags)


@pytest.mark.parametrize("tags", [
    ((0, 3),),                            # too short
    ((0, 3, 0, 10, 99),),                 # too long
    ([0, 3, 0, 10],),                     # a list, not a tuple
    (("0", 3, 0, 10),),                   # a string column
    ((0, 3, 0, True),),                   # a bool, which is an int in Python
])
def test_a_malformed_tag_is_refused(tags):
    with pytest.raises(AuditError, match="mask tag 0"):
        MaskedLine(length=10, doc_offset=0, tags=tags)


@pytest.mark.parametrize("kwargs", [
    {"length": -1, "doc_offset": 0},
    {"length": 5, "doc_offset": -1},
    {"length": "5", "doc_offset": 0},
    {"length": 5, "doc_offset": None},
    {"length": True, "doc_offset": 0},
])
def test_a_malformed_line_geometry_is_refused(kwargs):
    with pytest.raises(AuditError):
        MaskedLine(tags=(), **kwargs)


def test_a_tagless_line_needs_no_tags():
    """Most lines have none, and the empty tuple is the default rather than a special case."""
    masked = MaskedLine(length=10, doc_offset=7)
    assert masked.tags == ()
    assert one(flag(start=1, end=3), [masked]).flags[0].start == 8


# ─── the raw response ────────────────────────────────────────────────────────


def test_a_plain_json_response_parses():
    text = json.dumps({"flags": [flag(start=1, end=3)]})
    result = parse_response(text, doc_id="d1", lines=[line("0123456789")])
    assert len(result.flags) == 1


@pytest.mark.parametrize("text", [
    "```json\n{\"flags\": []}\n```",
    "Here are the flags:\n{\"flags\": []}",
    "{\"flags\": []} \n\nLet me know if you need more detail.",
    "", "not json at all", "{unclosed",
])
def test_a_fenced_or_prefaced_response_is_malformed_and_is_not_repaired(text):
    """Nothing strips a fence or repairs a key, for `rule_author.md` §2's reason.

    A response this module quietly repaired would make the format instruction advisory, and
    the arm would lose the ability to report a format failure as one — which is a thing
    `port-oneshot-nofence` exists to have measured.
    """
    result = parse_response(text, doc_id="d1", lines=[line("abc")])
    assert result.flags == ()
    assert refusals(result) == [MALFORMED]


def test_a_decode_failure_quotes_no_part_of_the_response():
    """The response is the agent's words about a masked document (CLAUDE.md).

    A `JSONDecodeError` re-raised with its context would carry a slice of it into a
    terminal, a CI log or an issue, and `release_screen.py` reaches none of those.
    """
    result = parse_response("{ Dr. Ejemplo Apellido", doc_id="d1", lines=[line("abc")])
    assert refusals(result) == [MALFORMED]
    assert all("Ejemplo" not in r.reason for r in result.refused)


# ─── the report ──────────────────────────────────────────────────────────────


def audits(n_flags=1, n_refused=0, doc_id="d1") -> DocumentAudit:
    return DocumentAudit(
        doc_id,
        tuple(Flag(doc_id=doc_id, phi_type="NAME", start=i, end=i + 4, score=0.5)
              for i in range(n_flags)),
        tuple(audit._refuse(doc_id, OUT_OF_RANGE) for _ in range(n_refused)),
    )


def test_the_report_carries_what_the_prompt_says_it_carries():
    out = report([audits(n_flags=2), audits(n_flags=0, doc_id="d2")],
                 corpus="es-meddocan", iteration=4, masked_from_iteration=3)
    assert out["iteration"] == 4
    assert out["masked_from_iteration"] == 3
    assert out["documents_audited"] == 2
    assert out["documents_with_no_flags"] == 1
    assert out["counts"]["flags"] == 2


def test_documents_with_no_flags_is_counted_and_not_inferred():
    """A document with no flags and a document never audited are different facts.

    Downstream cannot recover the first from an empty `flags` list, because the report does
    not list the documents that produced nothing. So the count is in the file.
    """
    out = report([audits(n_flags=0), audits(n_flags=0, doc_id="d2")],
                 corpus="es-meddocan", iteration=2, masked_from_iteration=1)
    assert out["counts"]["flags"] == 0
    assert out["documents_audited"] == 2 and out["documents_with_no_flags"] == 2


def test_the_report_sorts_its_flags():
    """The file's order does not move when the model's does (`auditor.md` §2.2)."""
    unsorted = DocumentAudit("d1", (
        Flag("d1", "NAME", 90, 95, 0.5), Flag("d1", "DATE", 10, 15, 0.5)), ())
    out = report([unsorted], corpus="es-meddocan", iteration=2, masked_from_iteration=1)
    assert [f["start"] for f in out["flags"]] == [10, 90]


def test_refusals_are_counted_by_reason():
    """A round where the model lost the coordinate scheme is a number, not a thin report."""
    out = report([audits(n_flags=1, n_refused=3)],
                 corpus="es-meddocan", iteration=2, masked_from_iteration=1)
    assert out["counts"]["refused"] == 3
    assert out["counts"]["by_refusal"] == {OUT_OF_RANGE: 3}


@pytest.mark.parametrize("iteration", [1, 0, -1, 1.5, True, "2", None])
def test_the_report_refuses_a_round_before_two(iteration):
    """The Auditor is called from round 2 onward (`config/naming.yaml`, `agent_role`).

    Round 1 is shown the same blocks as `port-oneshot` (DESIGN §4) and has no predictions
    to mask, so an audit report at iteration 1 is a record of a call that cannot have
    happened.
    """
    with pytest.raises(AuditError, match="iteration"):
        report([audits()], corpus="es-meddocan", iteration=iteration,
               masked_from_iteration=1)


@pytest.mark.parametrize("masked_from", [4, 2, 0, None])
def test_masked_from_iteration_must_be_the_previous_round(masked_from):
    """The report is an input to round n derived from round n−1's predictions.

    A record that got this wrong would attribute a round's flags to the wrong `spans.jsonl`,
    and the file exists partly so that a directory listing does not have to be trusted for
    this (`auditor.md` banner).
    """
    with pytest.raises(AuditError, match="masked_from_iteration"):
        report([audits()], corpus="es-meddocan", iteration=4,
               masked_from_iteration=masked_from)


def test_two_audits_for_one_document_are_refused():
    """One call per document (`auditor.md` §1.3), so a second is a retry or a double count.

    Raised rather than deduplicated: which of the two is the real audit is not this
    function's to decide, and silently keeping one would make `documents_audited` disagree
    with the number of calls the log records.
    """
    with pytest.raises(AuditError, match="two audits for one document"):
        report([audits(), audits()], corpus="es-meddocan", iteration=2,
               masked_from_iteration=1)


def test_an_undeclared_corpus_is_refused():
    with pytest.raises(AuditError, match="not a corpus"):
        report([audits()], corpus="es-nope", iteration=2, masked_from_iteration=1)


def test_the_report_is_json_serialisable_and_holds_no_text():
    """Offsets, types and scores only (`auditor.md` §2.2, CLAUDE.md).

    Serialised here because the check that matters is over the bytes that reach the file,
    not over the objects: a field holding a surface form would survive every assertion
    above about `Flag`'s slots.
    """
    out = report([audits(n_flags=2, n_refused=1)],
                 corpus="es-meddocan", iteration=3, masked_from_iteration=2)
    body = json.dumps(out)
    json.loads(body)
    for key in ("surface", "text", "context", "snippet", "phrase", "line"):
        assert f'"{key}"' not in body, key


# ─── where the report goes (paths.auditreport, DESIGN §5.5) ──────────────────
#
# The round's fourth file, and the fourth builder of a round-scoped path. `orchestrate.
# _arm_path` cannot produce it: that helper formats the four axes, `{iteration}` is not an
# axis (DESIGN §4 refused a fifth path component and §5.5 put the round in a directory), so a
# round-scoped path is a different template rather than a wider call.

ROUND_AXES = dict(corpus="es-meddocan", detector="RT", supervision="sup-free",
                  porting="port-loop")


def test_the_report_path_is_the_rounds_directory():
    """`paths.auditreport`' shape: four axes above, `iter{N}/` below, the report inside."""
    assert report_path(**ROUND_AXES, iteration=3, root=Path("/r")) == Path(
        "/r/results/es-meddocan/RT/sup-free/port-loop/iter3/audit_report.json")


def test_the_report_sits_with_the_rounds_other_three_files():
    """One directory per round, which is the property that makes the round one record.

    The report is an input to round n derived from round n−1's predictions, and the three
    files round n produces are its output. All four are named from the same four axes plus the
    same round, so the report cannot land in another arm's directory from the spans it was
    built against (DESIGN §5.5).
    """
    from src.eval.run_fold import errors_path, iter_spans_path
    from src.eval.scorer import iter_metrics_path

    root = Path("/r")
    here = report_path(**ROUND_AXES, iteration=3, root=root)
    for other in (iter_spans_path(**ROUND_AXES, iteration=3, root=root),
                  errors_path(**ROUND_AXES, iteration=3, root=root),
                  iter_metrics_path(**ROUND_AXES, iteration=3, root=root)):
        assert other.parent == here.parent
    assert here.name == "audit_report.json"


@pytest.mark.parametrize("key,bad", [
    ("corpus", "es-nope"), ("detector", "R+T"), ("supervision", "supfree"),
    ("porting", "port-agentic"),
])
def test_the_report_path_refuses_an_axis_value_naming_no_cell(key, bad):
    """A typo mints a cell rather than failing — and the file it would mint one for is the
    round's map of residual identifiers, which `paths.auditreport` is deny-listed for being.
    A report under `results/es-meddocan/rules-only/` is a deny pattern's near miss.
    """
    with pytest.raises(AuditError, match="naming.yaml"):
        report_path(**{**ROUND_AXES, key: bad}, iteration=2)


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "2", None])
def test_the_report_path_refuses_a_round_that_is_not_a_round(bad):
    """`iter0/` and `iter1.0/` put a round's report where nothing looks for it.

    `True` is in the list because `isinstance(True, int)` holds: a caller passing a flag
    would silently name round 1 — which for this file is also the round the Auditor never
    runs in.
    """
    with pytest.raises(AuditError, match="iteration"):
        report_path(**ROUND_AXES, iteration=bad)


def test_the_path_builder_does_not_enforce_the_auditors_schedule():
    """**Iteration 1 is a valid path and an invalid report**, and the split is deliberate.

    `report()` refuses a round before 2 because that is a fact about the Auditor's schedule:
    round 1 is shown the same blocks as `port-oneshot` (DESIGN §4) and has no predictions to
    mask. This function answers "where does round N's report go", and duplicating the ≥ 2
    check here would put a rule about one agent's schedule inside a path builder, where the
    next round-scoped file inherits it.
    """
    assert report_path(**ROUND_AXES, iteration=1, root=Path("/r")).parent.name == "iter1"
    with pytest.raises(AuditError, match="iteration"):
        report([audits()], corpus="es-meddocan", iteration=1, masked_from_iteration=0)


def test_the_report_path_raises_this_modules_error_type():
    """`AuditError`, not the shared builder's own type — which is why `round_path` takes the
    exception class as an argument. A caller catching this module's errors must not have to
    also catch `run_fold`'s to build a path.
    """
    with pytest.raises(AuditError):
        report_path(**{**ROUND_AXES, "porting": "nope"}, iteration=2)
    assert issubclass(AuditError, CorpusError)


def test_the_report_path_is_denied_by_the_screener():
    """The other half of the defence, on the path this module builds rather than on the
    pattern's text. `paths.auditreport` is deny-listed and not ALLOW-listed with a content
    sniffer (`config/naming.yaml`): on a DUA corpus this file is the map of the identifiers a
    round did not catch, which is the most concentrated form of what the loop produces.

    A pattern that matched nothing would be a rule reported as present and never run, so the
    assertion goes through `deny()`.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_screen_probe_audit", ROOT / "tools" / "release_screen.py")
    screen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(screen)

    rel = str(report_path(**ROUND_AXES, iteration=3, root=ROOT).relative_to(ROOT))
    assert screen.deny(rel)
    assert not any(__import__("re").search(p, rel) for p in screen.ALLOW_PATTERNS), (
        "the report path is both denied and allowed. `deny()` is consulted first, so this "
        "does not change today's verdict — it leaves the two lists disagreeing, one "
        "deny-rule deletion away from publishing the file."
    )


# ─── a round's draws (paths.auditdraw, DESIGN §5.5.2) ────────────────────────
#
# An incomplete round may be re-run, so one round can be audited M times and the log holds
# 250 × M Auditor lines against one report. These tests are about the two properties that make
# that accountable: no draw's report is overwritten, and nothing about M is inferred.


def test_a_draws_report_goes_in_a_numbered_subdirectory():
    assert audit.draw_path(**ROUND_AXES, iteration=5, draw=2, root=Path("/r")) == Path(
        "/r/results/es-meddocan/RT/sup-free/port-loop/iter5/draw2/audit_report.json")


def test_the_draw_keeps_the_canonical_filename():
    """**The name is `audit_report.json` and the number is a directory, not a suffix.**

    The screener's deny rule is `(^|/)audit_report\\.json$` — by name, deliberately, so that
    `metrics.json` and `spans.jsonl` in the same round directory stay publishable. An
    `audit_report.draw2.json` would slip past it, and the repair would be to widen a deny rule
    to cover a name invented under time pressure. A subdirectory inherits the protection with
    no screener edit at all, which is what the next test measures.
    """
    drawn = audit.draw_path(**ROUND_AXES, iteration=5, draw=3, root=Path("/r"))
    assert drawn.name == "audit_report.json"
    assert drawn.parent.name == "draw3"


def test_the_draw_path_is_denied_by_the_screener():
    """The claim above, asserted through `deny()` rather than by reading the pattern.

    A preserved draw is the same map of residual identifiers the canonical copy is, and there
    are now M of them per round. If the subdirectory ever stopped inheriting the deny rule,
    this is the test that fails rather than a release that ships.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_screen_probe_draw", ROOT / "tools" / "release_screen.py")
    screen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(screen)

    rel = str(audit.draw_path(**ROUND_AXES, iteration=5, draw=2,
                              root=ROOT).relative_to(ROOT))
    assert screen.deny(rel)
    assert not any(__import__("re").search(p, rel) for p in screen.ALLOW_PATTERNS)


def test_the_draw_sits_under_the_round_the_canonical_copy_is_in():
    """One cell, two templates, and this is what keeps them from disagreeing about which.

    `draw_path` calls `report_path` for its refusals rather than reimplementing the axis and
    round checks, and the structural consequence is this identity: a draw is always exactly one
    directory below the canonical report it supersedes.
    """
    for iteration in (2, 5, 8):
        drawn = audit.draw_path(**ROUND_AXES, iteration=iteration, draw=4, root=Path("/r"))
        canonical = report_path(**ROUND_AXES, iteration=iteration, root=Path("/r"))
        assert drawn.parent.parent == canonical.parent


@pytest.mark.parametrize("key,bad", [
    ("corpus", "es-nope"), ("detector", "R+T"), ("supervision", "supfree"),
    ("porting", "port-agentic"),
])
def test_the_draw_path_refuses_an_axis_value_naming_no_cell(key, bad):
    """Inherited from `report_path`, and asserted here because inheritance by delegation is a
    property of this function's body that a later edit could remove.
    """
    with pytest.raises(AuditError, match="naming.yaml"):
        audit.draw_path(**{**ROUND_AXES, key: bad}, iteration=5, draw=2)


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "2", None])
def test_the_draw_path_refuses_a_draw_that_is_not_a_draw(bad):
    """`draw0/` is an audit nothing counts, and `True` is an `int` that would name draw 1 —
    silently reusing the first attempt's directory, which is the overwrite this path prevents.
    """
    with pytest.raises(AuditError, match="draw"):
        audit.draw_path(**ROUND_AXES, iteration=5, draw=bad)


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "2", None])
def test_the_draw_path_refuses_a_round_that_is_not_a_round(bad):
    with pytest.raises(AuditError, match="iteration"):
        audit.draw_path(**ROUND_AXES, iteration=bad, draw=1)


def test_the_first_audit_of_a_round_is_draw_one(tmp_path):
    """No directory, so no attempt — and the answer is 1 rather than an error, because every
    round in the record before 2026-08-24 was audited exactly once.
    """
    assert audit.next_draw(**ROUND_AXES, iteration=5, root=tmp_path) == 1


def test_the_next_draw_is_one_past_the_highest_that_exists(tmp_path):
    round_dir = audit.draw_path(**ROUND_AXES, iteration=5, draw=1,
                                root=tmp_path).parent.parent
    for name in ("draw1", "draw2"):
        (round_dir / name).mkdir(parents=True)
    assert audit.next_draw(**ROUND_AXES, iteration=5, root=tmp_path) == 3


def test_a_gap_in_the_draws_is_left_as_a_gap(tmp_path):
    """**One past the highest, not how many exist.**

    The two agree until a draw directory is missing, and there the count would hand back 2 while
    `draw2/` already holds a preserved report — reusing a number and overwriting the one file
    this whole path exists to keep. A gap stays visible in the listing and in the mismatch
    against `draws_total`, which is the honest outcome rather than the tidy one.
    """
    round_dir = audit.draw_path(**ROUND_AXES, iteration=5, draw=1,
                                root=tmp_path).parent.parent
    (round_dir / "draw2").mkdir(parents=True)
    assert audit.next_draw(**ROUND_AXES, iteration=5, root=tmp_path) == 3


def test_the_next_draw_ignores_directories_that_are_not_draws(tmp_path):
    """The round directory also holds this round's other files, and a sibling named `drawing/`
    or a stray `draws/` must not become a draw number.
    """
    round_dir = audit.draw_path(**ROUND_AXES, iteration=5, draw=1,
                                root=tmp_path).parent.parent
    round_dir.mkdir(parents=True)
    for name in ("draw1", "drawings", "draw", "drawX"):
        (round_dir / name).mkdir()
    (round_dir / "draw9").write_text("not a directory", encoding="utf-8")
    assert audit.next_draw(**ROUND_AXES, iteration=5, root=tmp_path) == 2


def test_the_next_draw_is_the_number_that_does_not_overwrite(tmp_path):
    """The property, stated as the property rather than as an arithmetic identity: whatever
    `next_draw` returns, no report is already there.
    """
    for _ in range(4):
        n = audit.next_draw(**ROUND_AXES, iteration=5, root=tmp_path)
        drawn = audit.draw_path(**ROUND_AXES, iteration=5, draw=n, root=tmp_path)
        assert not drawn.exists()
        drawn.parent.mkdir(parents=True)
        drawn.write_text("{}", encoding="utf-8")


# ─── draw_index and draws_total on the report ────────────────────────────────


def test_a_report_says_which_attempt_wrote_it():
    out = report([audits()], corpus="es-meddocan", iteration=5, masked_from_iteration=4,
                 draw_index=3, draws_total=3)
    assert out["draw_index"] == 3 and out["draws_total"] == 3


def test_a_round_audited_once_says_so_without_being_told():
    """The default is 1, and 1 is the true state of every round audited once — which is every
    round in the record before this field existed. A default here is a fact, not a placeholder,
    which is why the field is unconditionally written rather than omitted at draw 1.
    """
    out = report([audits()], corpus="es-meddocan", iteration=3, masked_from_iteration=2)
    assert out["draw_index"] == 1


def test_a_preserved_draw_carries_no_total():
    """**Absence is a record.** At the moment draw 2's report is written nobody knows whether
    there will be a draw 3, so the preserved copy omits the total — and its presence therefore
    means "this report is the latest draw", which is a readable fact rather than a convention.
    """
    out = report([audits()], corpus="es-meddocan", iteration=5, masked_from_iteration=4,
                 draw_index=2)
    assert "draws_total" not in out


def test_the_two_fields_are_adjacent_in_the_file():
    """Neither is readable without the other, so they are written next to each other.

    Asserted over key order because that is what a human opening a 250-document report sees:
    `draws_total` appended at the end would sit several hundred flags away from the only field
    it means anything beside.
    """
    out = report([audits()], corpus="es-meddocan", iteration=5, masked_from_iteration=4,
                 draw_index=2, draws_total=4)
    keys = list(out)
    assert keys[keys.index("draw_index") + 1] == "draws_total"


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "2", None])
def test_the_report_refuses_a_draw_index_that_is_not_a_draw(bad):
    with pytest.raises(AuditError, match="draw_index"):
        report([audits()], corpus="es-meddocan", iteration=5, masked_from_iteration=4,
               draw_index=bad)


@pytest.mark.parametrize("bad", [1.5, True, "3"])
def test_the_report_refuses_a_total_that_is_not_a_count(bad):
    with pytest.raises(AuditError, match="draws_total"):
        report([audits()], corpus="es-meddocan", iteration=5, masked_from_iteration=4,
               draw_index=1, draws_total=bad)


def test_a_report_cannot_be_draw_three_of_two():
    """The one inconsistency visible from inside a single file, so it is refused rather than
    left for a reader to catch — the reader who would catch it is reading to reconcile the
    report against the call log, and this is the field pair they are reconciling with.
    """
    with pytest.raises(AuditError, match="draw_index"):
        report([audits()], corpus="es-meddocan", iteration=5, masked_from_iteration=4,
               draw_index=3, draws_total=2)


def test_the_canonical_copy_differs_from_the_preserved_one_by_exactly_one_key():
    """What the loop driver writes twice, compared as bytes-worth-of-difference.

    The preserved draw and the canonical report are the same payload; a second `report()` call
    for the canonical copy could drift from the first while both looked right, which is why the
    driver derives one from the other.
    """
    preserved = report([audits(n_flags=2)], corpus="es-meddocan", iteration=5,
                       masked_from_iteration=4, draw_index=2)
    canonical = audit.with_draws_total(preserved, 2)
    assert set(canonical) - set(preserved) == {"draws_total"}
    assert {k: v for k, v in canonical.items() if k != "draws_total"} == preserved


def test_stamping_the_total_does_not_mutate_the_preserved_payload():
    """The preserved copy is written first and never touched again. If this returned the same
    object the driver would be writing the canonical payload to both paths, and the draw would
    claim a total it cannot know.
    """
    preserved = report([audits()], corpus="es-meddocan", iteration=5,
                       masked_from_iteration=4, draw_index=2)
    audit.with_draws_total(preserved, 2)
    assert "draws_total" not in preserved


def test_stamping_refuses_what_the_report_would_refuse():
    """Same rule, checked in both places, so this function cannot be the way to construct a
    payload `report()` rejects.
    """
    preserved = report([audits()], corpus="es-meddocan", iteration=5,
                       masked_from_iteration=4, draw_index=3)
    for bad in (2, 0, -1, 1.5, True, None, "3"):
        with pytest.raises(AuditError, match="draw"):
            audit.with_draws_total(preserved, bad)


def test_stamping_a_payload_with_no_draw_index_is_refused():
    """A `dict` that never went through `report()` — the total would land nowhere, and a
    silently unstamped payload written as the canonical copy is a lost count.
    """
    with pytest.raises(AuditError, match="draw_index"):
        audit.with_draws_total({"iteration": 5}, 2)


def test_the_draw_fields_hold_no_text():
    """The report's standing guarantee, re-asserted on the payload the new fields are on."""
    body = json.dumps(audit.with_draws_total(
        report([audits(n_flags=2, n_refused=1)], corpus="es-meddocan", iteration=5,
               masked_from_iteration=4, draw_index=2), 2))
    for key in ("surface", "text", "context", "snippet", "phrase", "line"):
        assert f'"{key}"' not in body, key


# ─── structure: no surface form leaves this module ───────────────────────────


def tree() -> ast.Module:
    return ast.parse(MODULE.read_text(encoding="utf-8"), filename=str(MODULE))


def functions(module: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in ast.walk(module) if isinstance(n, ast.FunctionDef)}


def calls_named(node: ast.AST) -> set[str]:
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            if isinstance(fn, ast.Attribute):
                found.add(fn.attr)
            elif isinstance(fn, ast.Name):
                found.add(fn.id)
    return found


WRITE_NAMES = {"open", "write", "writelines", "writestr", "write_text", "write_bytes",
               "mkdir", "touch"}
LOG_NAMES = {"log", "debug", "info", "warning", "error", "exception", "critical",
             "getLogger", "basicConfig", "print"}


def test_no_function_in_the_module_writes_or_logs():
    """**This module holds masked and unmasked coordinates at once.**

    `src/llm/prompt.py` has the same check for the renderer, and here the argument is one
    step sharper: a debug write from this file would publish the flagged positions *and*
    the map that resolves them. Structural, because a write added "to check a boundary"
    behaves identically on every machine where anyone would notice.
    """
    for name, fn in sorted(functions(tree()).items()):
        stray = calls_named(fn) & (WRITE_NAMES | LOG_NAMES)
        assert not stray, (
            f"src/porting/audit.py::{name} calls {sorted(stray)}. The audit report is "
            "written by the loop driver, and no line of a masked document or a flagged "
            "position reaches a terminal or a log (CLAUDE.md, auditor.md §3)."
        )


def test_the_module_never_reads_a_document_s_text():
    """It is given `MaskedLine`s and never the corpus.

    The validator's whole input is coordinates plus the tag map. A `load()` here would make
    it a component that could resolve its own flags to text, which is the property the
    report's deny rule exists because it does *not* have.
    """
    source = MODULE.read_text(encoding="utf-8")
    for forbidden in ("from ..corpora import load", "load(", ".surface", "doc.text"):
        assert forbidden not in source, forbidden


def test_no_type_in_the_module_has_a_text_field():
    """`ErrorSpan`'s guarantee, extended to **every** type here (DESIGN §5.5.1, §3).

    The guarantee is *no surface form exists in the object*, and it is a property of the
    schema rather than of a wrapper — so it is asserted over the fields rather than trusted
    to the annotations that say so.

    **`MaskedLine` was the exception and is not one any more.** It carried `text: str` — a
    slice of the masked document, on a dataclass whose generated `repr` renders it, which is
    the state DESIGN §3's "the masker returns `FilledPrompt` and never a `str`" exists to
    prevent. What it bought was one `len()`. The exception is gone rather than documented,
    because a documented exception is what the next type copies.

    Asserted over every dataclass in the module rather than a list written out here, so a
    type added later is covered on the day it is added.
    """
    forbidden = {"text", "surface", "snippet", "context", "phrase", "line", "excerpt"}
    types = [obj for obj in vars(audit).values()
             if isinstance(obj, type) and dataclasses.is_dataclass(obj)]
    assert {t.__name__ for t in types} == {"MaskedLine", "Flag", "Refusal", "DocumentAudit"}
    for cls in types:
        fields = {f.name for f in dataclasses.fields(cls)}
        assert not fields & forbidden, (cls.__name__, sorted(fields & forbidden))
    assert set(MaskedLine.__slots__) == {"length", "doc_offset", "tags"}
