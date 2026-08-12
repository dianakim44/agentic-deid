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
import json
from pathlib import Path

import pytest

from src.corpora import base
from src.corpora.base import CorpusError, audit_refusals, check_audit_refusal
from src.porting import audit
from src.porting.audit import (
    CROSSES_A_LINE, INSIDE_A_MASK_TAG, MALFORMED, OUT_OF_RANGE, UNDECLARED_PHI_TYPE,
    AuditError, DocumentAudit, Flag, MaskedLine, parse_response, report, validate_flags,
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


def line(text: str, *, offset: int = 0, doc_offset: int = 0, tags=()) -> MaskedLine:
    return MaskedLine(text=text, offset=offset, doc_offset=doc_offset, tags=tuple(tags))


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


def test_the_line_s_own_offset_is_not_the_document_offset():
    """Two offsets on `MaskedLine`, and this is why neither is derivable from the other.

    `offset` is where the line starts in the *masked* text — what the prompt prints as its
    prefix — and `doc_offset` is where it starts in the document. They differ by every tag
    on every earlier line, so a translation that used the printed prefix would drift down
    the document.
    """
    masked = line("abcd", offset=10, doc_offset=40)
    assert one(flag(start=1, end=3), [masked]).flags[0].start == 41


def test_a_flag_on_a_later_line_uses_that_line_s_offsets():
    lines = [line("first", doc_offset=0), line("second", doc_offset=6),
             line("third", doc_offset=20, tags=[(0, 6, 20, 30)])]
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


def test_no_flag_type_has_a_text_field():
    """`ErrorSpan`'s guarantee, one type over (DESIGN §5.5.1).

    The guarantee is *no surface form exists in the object*, and it is a property of the
    schema rather than of a wrapper — so it is asserted over the fields rather than trusted
    to the annotations that say so.
    """
    for cls in (Flag, audit.Refusal, MaskedLine):
        fields = set(getattr(cls, "__slots__", ()))
        assert not fields & {"surface", "snippet", "context", "phrase"}, cls
    # `MaskedLine.text` is the exception and it is why the type is not written to disk:
    # it is a slice of the masked document, held in memory for the length of one call.
    assert "text" in set(MaskedLine.__slots__)
    assert "text" not in set(Flag.__slots__)
