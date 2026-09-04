"""The non-recording convention: a filled prompt is never written down.

`docs/prompts/rule_author.md` §6 fixes the rule and `src/llm/prompt.py` implements it as a
type. This file checks the two halves that a type cannot enforce on its own.

**Behaviour**, for the type's own guarantees: the text is not reachable except through the
exits enumerated in `prompt.EXITS`, `to_terminal()` refuses a stream that is not a terminal,
and `reference()` carries references and hashes and no text. The set is asserted by name
rather than by count — DESIGN §5.4, and the reasoning is in
`test_the_type_has_no_accessor_beyond_the_named_exits`.

**Structure**, for everything else — and this is the half that needs explaining. The
failure mode is a renderer or a caller that also writes the text somewhere: a debug copy,
a log line, a cached prompt. That code behaves *identically* to correct code on every
machine where anyone would notice, exactly as `tests/test_conftest.py`'s availability
defect did, so a behavioural test would pass on both. What separates them is which calls
appear inside which function, so that is what is asserted.

**The renderer is inside the checked set, not upstream of it.** A type protecting a value
the renderer already copied to disk protects nothing, so `render_window()`'s own body is
checked for file writes, for `print`, and for returning anything but a `FilledPrompt`. The
same checks cover every helper in the module: a renderer that delegated its write to a
private function would otherwise satisfy a check that only looked at the public one — the
"silently matches nothing" class of defect this repository has already been bitten by.

    python3 -m pytest tests/test_prompt.py -q
"""
from __future__ import annotations

import ast
import dataclasses
import importlib.util
import io
import itertools
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora.base import (                                   # noqa: E402
    CorpusError, Document, Span, axis, caching_boundaries, excluded_types,
    masked_tag_heterogeneous,
)
from src.llm import prompt as prompt_module                      # noqa: E402
from src.llm.prompt import (                                     # noqa: E402
    AUDIT_SECTIONS, CACHE_BOUNDARY, CACHE_TTL, COUNT_KEYS, EMPTY_SECTIONS, FILLED_SECTIONS,
    INPUT_BANNER, ITERATION_SECTIONS, LINE_OFFSET_WIDTH, LINE_SEPARATOR, MASKED_DOCUMENT,
    TAG_FORM, CacheBlocks, FilledPrompt, MaskedDocument, PromptError,
    assemble_audit_prompt, assemble_iteration_prompt, assemble_task_prompt, mask_document,
    render_window,
)
from src.porting.audit import MaskedLine, validate_flags         # noqa: E402
from src.rules import rule_layers                                # noqa: E402
from src.sample import (                                         # noqa: E402
    FALSE_POSITIVE, MISSED, WINDOW_FILES, ErrorSpan, non_target_types,
)

MODULE = ROOT / "src" / "llm" / "prompt.py"

#: Invented, not from any corpus. Long and distinctive so an assertion of its absence
#: cannot pass by coincidence — the same device `tests/test_human_arm.py` uses.
SURFACE = "Zzyzx Quinbolt"

#: Names that write. `open` covers the builtin; `write`, `writelines`, `dump` and `dumps`
#: cover a handle or a serialiser reached by attribute. `print` is listed separately in the
#: assertions because it has a legitimate destination (a terminal) and an illegitimate one
#: (anything redirected), and the type's `to_terminal` is the distinction.
WRITE_NAMES = {"open", "write", "writelines", "dump", "dumps", "writestr", "write_text",
               "write_bytes", "mkdir", "touch"}

#: Logging, in the spellings a module reaches for.
LOG_NAMES = {"log", "debug", "info", "warning", "error", "exception", "critical",
             "getLogger", "basicConfig"}

#: The one function allowed to call `write`: it is the exit named for a terminal, and the
#: call it makes is the write to that terminal.
TERMINAL_EXIT = "to_terminal"


def doc(doc_id: str = "dev1") -> Document:
    text = "." * 1000 + SURFACE + "." * 1000
    return Document(
        doc_id=doc_id, corpus_id="es-meddocan", text=text, split="dev",
        spans=[Span(start=1000, end=1000 + len(SURFACE), surface=SURFACE,
                    subtype="NOMBRE_SUJETO_ASISTENCIA", phi_type="NAME")])


def err(doc_id: str = "dev1", index: int = 0, start: int = 1000) -> ErrorSpan:
    return ErrorSpan(doc_id=doc_id, span_index=index, phi_type="NAME",
                     kind=MISSED, start=start, end=start + len(SURFACE))


def a_prompt() -> FilledPrompt:
    return render_window([err()], {"dev1": doc()}, 120)


class FakeTerminal(io.StringIO):
    """A stream that says it is a terminal. `StringIO.isatty()` returns False."""

    def isatty(self) -> bool:
        return True


# ─── the renderer returns the type, and the type is the only handle ──────────


def test_the_renderer_returns_a_filled_prompt_and_not_a_string():
    """The premise of everything else here. A `str` return would leave the value loose."""
    assert isinstance(a_prompt(), FilledPrompt)
    assert not isinstance(a_prompt(), str)


def test_the_rendered_window_is_what_the_author_needs_to_see():
    """The context and window-relative offsets `rule_author.md` §1.4 requires.

    Asserted through `to_terminal` — the same exit the tool uses — so the content check and
    the destination check exercise one path rather than two.
    """
    out = FakeTerminal()
    a_prompt().to_terminal(out)
    text = out.getvalue()
    assert SURFACE in text                    # the context is present, which is the point
    assert "(120, 134)" in text               # offsets within the context window
    assert "(1000," not in text               # never the document offset
    assert "type      NAME" in text
    assert f"error     {MISSED}" in text


def test_the_context_is_clipped_to_the_document():
    d = Document(doc_id="dev1", corpus_id="es-meddocan", text="abcdefghij", split="dev",
                 spans=[Span(start=2, end=5, surface="cde", subtype="X",
                             phi_type="NAME")])
    e = ErrorSpan(doc_id="dev1", span_index=0, phi_type="NAME", kind=MISSED,
                  start=2, end=5)
    out = FakeTerminal()
    render_window([e], {"dev1": d}, 120).to_terminal(out)
    assert "(2, 5)" in out.getvalue()         # left clipped at 0, so window == document


def test_newlines_are_flattened_so_one_block_is_one_span():
    d = Document(doc_id="dev1", corpus_id="es-meddocan", text="aaa\nbbb\nccc", split="dev",
                 spans=[Span(start=4, end=7, surface="bbb", subtype="X",
                             phi_type="NAME")])
    e = ErrorSpan(doc_id="dev1", span_index=0, phi_type="NAME", kind=MISSED,
                  start=4, end=7)
    out = FakeTerminal()
    render_window([e], {"dev1": d}, 120).to_terminal(out)
    assert "aaa bbb ccc" in out.getvalue()


def test_a_sample_naming_a_document_that_was_not_supplied_is_refused():
    """And the message carries no surface form (CLAUDE.md)."""
    with pytest.raises(PromptError) as exc:
        render_window([err("dev9")], {"dev1": doc()}, 120)
    assert SURFACE not in str(exc.value)
    assert "dev9" in str(exc.value)


# ─── §§1.1–1.2: what a first call is shown ───────────────────────────────────


def assembled(**kw) -> FilledPrompt:
    kw.setdefault("lang", "es")
    kw.setdefault("corpus", "es-meddocan")
    return assemble_task_prompt(**kw)


def shown(prompt: FilledPrompt) -> str:
    """The filled prompt's text, read through the checked exit."""
    out = FakeTerminal()
    prompt.to_terminal(out)
    return out.getvalue()


def test_the_assembler_returns_a_filled_prompt_and_not_a_string():
    """The same premise as the renderer's.

    This block carries no corpus text today, and that is not the reason the type is used:
    "this block happens to be safe" is the reasoning CLAUDE.md refuses about corpora, and
    §1.2 quotes a rule file whose freedom from surface forms rests on a screener having
    worked (rule_author.md Prohibition 2).
    """
    p = assembled()
    assert isinstance(p, FilledPrompt)
    assert not isinstance(p, str)


def test_the_first_call_carries_the_template_verbatim():
    """§2's schema, §3's tools and §4's prohibitions travel with the input.

    An assembler forwarding §1 alone would send the instance without the specification it
    is an instance of, and the model would be asked for a file whose schema it was never
    given.
    """
    template = (ROOT / "docs" / "prompts" / "rule_author.md").read_text(encoding="utf-8")
    assert template in shown(assembled())


def test_the_task_frame_names_every_canonical_type_with_its_own_gloss():
    """§1.1: verbatim from naming.yaml, glosses included.

    Read from the config rather than compared against a list here, for the reason the
    assembler reads it from there: this block is what tells the agent which values exist,
    so a copy in the test would agree with a prompt that had drifted from the axis.
    """
    text = shown(assembled())
    for name, gloss in axis("phi_type").items():
        assert name in text, f"the task frame omits the {name} phi_type"
        assert gloss in text, f"the task frame omits {name}'s gloss"


def test_the_task_frame_names_the_writable_layers_and_excludes_the_rest():
    """Which layers a rule may declare, and — by difference — which it may not.

    The unwritable set is asserted as `axis("layer") - rule_layers()` rather than as
    `{"tagger"}`: a second learned layer must appear in the prompt's exclusion without an
    edit here, since a rule declaring it would be refused at load either way.
    """
    text = shown(assembled())
    for layer in rule_layers():
        assert layer in text
        assert axis("layer")[layer] in text
    for layer in set(axis("layer")) - set(rule_layers()):
        assert layer in text, (
            f"{layer} is not writable from a rule file and the frame does not say so"
        )


def test_the_task_frame_says_the_residual_bucket_is_not_a_target():
    """§1.1's fourth bullet, and Prohibition 4.

    Stated outright rather than left to the type list's gloss: an agent given a residual
    bucket writes rules into it. The types are found through `non_target_types()`, which
    reads naming.yaml's own gloss, so a corpus shipping a second residual bucket is covered
    without an edit.
    """
    text = shown(assembled())
    assert non_target_types(), "no non-target type to check — the fixture has drifted"
    for name in non_target_types():
        assert f"{name} is not a rule-development target" in text


def test_the_task_frame_names_the_target_file_and_the_corpora_that_load_it():
    """§1.1's first bullet. `es` is loaded by two corpora and both are named."""
    text = shown(assembled())
    assert "rules/es.yaml" in text
    assert "es-meddocan" in text and "es-carmen" in text


def test_the_current_rule_file_is_included_in_full():
    """§1.2: full text, not a summary — the agent edits this file.

    Written into a tmp path rather than read from `rules/es.yaml`, so the assertion is
    about what the assembler does with a file's contents rather than about the contents of
    the committed format example.
    """
    marker = "format_zzyzx_probe"
    path = Path(tempfile.mkdtemp()) / "es.yaml"
    path.write_text(f"version: 7\nlang: es\nrules: []  # {marker}\n", encoding="utf-8")
    text = shown(assembled(rules_path=path))
    assert marker in text
    assert "version: 7" in text


def test_a_missing_rule_file_is_named_as_empty_rather_than_left_blank():
    """Iteration 1's ordinary state, and the agent is told which state it is in.

    An agent shown nothing where a file was promised cannot tell "no rules yet" from "the
    harness failed to load them", and those call for opposite behaviour.
    """
    text = shown(assembled(rules_path=Path("/nonexistent/es.yaml")))
    assert "EMPTY" in text
    assert "first iteration" in text
    ref = assembled(rules_path=Path("/nonexistent/es.yaml")).reference()
    assert ref["rules_empty"] is True
    assert ref["rules_source"] is None
    assert ref["rules_sha256"] is None


def test_the_empty_blocks_are_stated_as_empty_and_not_omitted():
    """DESIGN §4: §§1.3–1.4 are empty *for this call*, and the prompt says why.

    The alternative — send §1.1 and §1.2 and say nothing — leaves the agent to resolve a
    template describing four blocks against two arriving unexplained, and every resolution
    available to it is worse than being told.
    """
    text = shown(assembled())
    for section in EMPTY_SECTIONS:
        assert f"### {section} — EMPTY for this call" in text
    assert "no previous iteration" in text
    assert "do not substitute anything for them" in text.lower()


def test_the_reference_form_records_which_blocks_were_filled():
    """The prompt's own account of its window, in `window_freeze.json`'s vocabulary.

    Two statements rather than one restated: the freeze record is the arm's claim about the
    window it committed to, and this is what the prompt says it carried. A comparison needs
    both.
    """
    ref = assembled().reference()
    assert ref["sections_filled"] == list(FILLED_SECTIONS)
    assert ref["sections_empty"] == list(EMPTY_SECTIONS)
    assert ref["block"] == "task_frame"
    assert ref["lang"] == "es" and ref["corpus"] == "es-meddocan"
    assert ref["text_sha256"].startswith("sha256:")
    json.dumps(ref)                              # a record holds it, so it must serialise


def test_the_reference_form_hashes_the_rule_file_the_call_actually_saw():
    """Which revision of §1.2 the emitted diff applies to, without holding the file."""
    d = Path(tempfile.mkdtemp())
    one, two = d / "a.yaml", d / "b.yaml"
    one.write_text("version: 1\nlang: es\nrules: []\n", encoding="utf-8")
    two.write_text("version: 2\nlang: es\nrules: []\n", encoding="utf-8")
    assert assembled(rules_path=one).reference()["rules_sha256"] != \
        assembled(rules_path=two).reference()["rules_sha256"]


def test_the_rule_file_path_is_not_recorded_absolutely():
    """A published record does not name a home directory (`rules._relative`'s reason)."""
    path = Path(tempfile.mkdtemp()) / "es.yaml"
    path.write_text("version: 1\nlang: es\nrules: []\n", encoding="utf-8")
    assert assembled(rules_path=path).reference()["rules_source"] == "es.yaml"
    committed = ROOT / "rules" / "es.yaml"
    if committed.exists():
        assert assembled(rules_path=committed).reference()["rules_source"] == \
            "rules/es.yaml"


def test_a_language_the_corpus_does_not_load_is_refused():
    """One invocation targets one file, and the corpus has to be one that loads it."""
    with pytest.raises(PromptError) as exc:
        assembled(lang="de")                        # es-meddocan loads [es]
    assert "corpus_rule_langs" in str(exc.value)
    for bad in ({"lang": "klingon"}, {"corpus": "es-nowhere"}):
        with pytest.raises(PromptError) as exc:
            assembled(**bad)
        assert "naming.yaml" in str(exc.value)


def test_the_assembler_does_not_render_or_draw_error_spans():
    """DESIGN §4, consequence 3, at the assembler rather than at the orchestrator.

    Structural: an assembler that reached for the renderer would put 40 dev gold spans into
    call 1, which is the asymmetry the definition exists to prevent — and it would look
    like a more complete implementation.
    """
    fn = functions(tree())["assemble_task_prompt"]
    reached = calls_named(fn)
    for name in ("render_window", "draw", "draw_iteration", "initial_error_pool"):
        assert name not in reached, (
            f"assemble_task_prompt calls {name}. Call 1 carries §§1.1–1.2 and nothing "
            "else, in port-oneshot and in port-loop's iteration 1 alike (DESIGN §4)."
        )


def test_the_assembler_returns_only_a_filled_prompt_call():
    """The renderer's structural check, applied to the other producer of the type."""
    fn = functions(tree())["assemble_task_prompt"]
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert returns, "assemble_task_prompt has no return statement to check"
    for node in returns:
        value = node.value
        assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and \
            value.func.id == "FilledPrompt", (
            "assemble_task_prompt returns something other than a FilledPrompt(...) "
            "construction."
        )


# ─── §§1.3–1.4: what a round from 2 onward adds ──────────────────────────────
#
# The ladder's definition lives in two numbers, and both are asserted here: round 1 is
# `assemble_task_prompt()` exactly, and from round 2 every §1 block is filled. The
# in-between states — a round-1 call holding feedback, a round-3 call missing a block, a
# round-4 call holding round 2's audit — are each refused rather than absorbed, because an
# arm that quietly dropped a block would be a weaker rung reported as a stronger one.


#: The previous round's score, as the scorer writes it. Built by calling `score()` on the
#: eight-case fixture corpus rather than written out as a literal, and the reason is the one
#: `tests/test_scorer.py` gives in reverse: that file's numbers are hand-derived because the
#: scorer is what is under test, and here the scorer is *upstream* — what is under test is
#: whether the block reduces a real `metrics.json` without dropping a key. A hand-written
#: dict would agree with a renderer that had drifted from the scorer's schema, which is the
#: single most likely way this block breaks.
#:
#: The fixture is duplicated from `test_scorer.py` rather than imported, so that a change to
#: the fixture geometry there cannot silently retune the assertions here.
FIX_RULE = "es:cue_person"
FIX_DATE = "es:date_numeric"


def scored_fixture() -> dict:
    """A `modes` block from the scorer itself, on a corpus with every case in it."""
    from src.eval.scorer import DocPair, Mark, score
    pairs = [
        # Two adjacent gold NAMEs under one wide tagger prediction: covered by the union,
        # one credited by the assignment. `assignment_slack` is non-zero here.
        DocPair(doc_id="d-adjacent",
                gold=(Mark(0, 4, "NAME", span_index=0), Mark(5, 10, "NAME", span_index=1)),
                pred=(Mark(0, 10, "NAME", "tagger", span_index=0),)),
        # A rule and the tagger on the same gold span, so `both` is non-zero and `by_rule`
        # has a row with tp.
        DocPair(doc_id="d-agree",
                gold=(Mark(0, 5, "NAME", span_index=0),),
                pred=(Mark(0, 5, "NAME", "context_cue", FIX_RULE, span_index=0),
                      Mark(0, 5, "NAME", "tagger", span_index=1))),
        # A rule that fires and never matches: the deletion candidate §1.3 exists to make
        # visible, so the block has to carry a row whose tp is 0.
        DocPair(doc_id="d-misfire",
                gold=(Mark(100, 110, "DATE", span_index=0),),
                pred=(Mark(300, 310, "DATE", "regex_checksum", FIX_DATE, span_index=0),)),
        # Gold nothing predicted on: a leak, and a type whose leak_rate is 1.0.
        DocPair(doc_id="d-missed",
                gold=(Mark(0, 5, "PROFESSION", span_index=0),), pred=()),
        # A PHI-free document with a prediction: false-positive opportunity, and the
        # document that keeps `by_type` holding a type with gold 0.
        DocPair(doc_id="d-clean", gold=(),
                pred=(Mark(0, 5, "CONTACT", "tagger", span_index=0),)),
    ]
    return score(pairs, excluded_gold=0)


def audit_report(iteration: int = 3, *, flags=None, masked_from=None,
                 documents_audited: int = 4, refused=()) -> dict:
    """A report in `audit.report()`'s shape, built by `audit.report()`.

    Built through the real function for `scored_fixture()`'s reason, and it matters more
    here: the two ends of this handover are the two things being checked against each other,
    so a hand-written dict would let both drift together. `masked_from` is a parameter only
    so that the off-by-one tests can pass a value `report()` would refuse — those construct
    the dict directly.
    """
    from src.porting.audit import DocumentAudit, report
    audits = [
        DocumentAudit(doc_id=f"dev{i}", flags=tuple(flags or ()) if i == 1 else (),
                      refused=tuple(refused) if i == 1 else ())
        for i in range(1, documents_audited + 1)
    ]
    out = report(audits, corpus="es-meddocan", iteration=iteration,
                 masked_from_iteration=iteration - 1)
    if masked_from is not None:
        out["masked_from_iteration"] = masked_from
    return out


def a_flag(doc_id: str = "dev1", phi_type: str = "NAME", start: int = 1000,
           end: int = 1014, score: float = 0.8):
    from src.porting.audit import Flag
    return Flag(doc_id=doc_id, phi_type=phi_type, start=start, end=end, score=score)


def iterated(**kw) -> FilledPrompt:
    """A round-3 call with every block filled, unless a keyword says otherwise."""
    kw.setdefault("lang", "es")
    kw.setdefault("corpus", "es-meddocan")
    kw.setdefault("iteration", 3)
    if kw["iteration"] > 1:
        kw.setdefault("metrics", scored_fixture())
        kw.setdefault("errors", [err()])
        kw.setdefault("docs_by_id", {"dev1": doc()})
        kw.setdefault("context_chars", 120)
        kw.setdefault("audit_report", audit_report(kw["iteration"]))
    return assemble_iteration_prompt(**{k: v for k, v in kw.items() if v is not _ABSENT})


#: Passed for a keyword that must arrive as absent rather than as None, so a test can say
#: "this argument was not given" without `None` meaning two things.
_ABSENT = object()


# ── round 1 is the baseline's prompt, by delegation and not by agreement ──


def test_round_one_is_the_no_feedback_prompt_byte_for_byte():
    """DESIGN §4: `port-oneshot` and `port-loop`'s round 1 are shown the same thing.

    Compared by content hash through `__eq__`, which is the comparison the type allows and
    is the right one here: two prompts that differ anywhere differ in the hash, and neither
    side of the assertion holds text. A reimplementation that agreed today would pass this
    and drift on the next edit to the frame — which is why the delegation is *also* asserted
    structurally below.
    """
    assert assemble_iteration_prompt(lang="es", corpus="es-meddocan", iteration=1) == \
        assemble_task_prompt(lang="es", corpus="es-meddocan")


def test_round_one_delegates_rather_than_reassembling():
    """Structural, because equality today is what a divergent copy also shows.

    The check is that `assemble_iteration_prompt` reaches `assemble_task_prompt` and that
    round 1 does not run the block builders — a function that assembled §§1.1–1.2 itself and
    happened to match would make DESIGN §4's claim rest on two implementations staying equal
    rather than on there being one.
    """
    fn = functions(tree())["assemble_iteration_prompt"]
    reached = calls_named(fn)
    assert "assemble_task_prompt" in reached, (
        "assemble_iteration_prompt does not delegate round 1. Round 1 is the baseline's "
        "prompt (DESIGN §4), and a second assembly of it is a second thing to keep equal."
    )
    early_return = [n for n in ast.walk(fn) if isinstance(n, ast.Return)
                    and isinstance(n.value, ast.Call)
                    and getattr(n.value.func, "id", "") == "assemble_task_prompt"]
    assert early_return, "the delegation is not a return — round 1 falls through"


def test_round_one_carries_the_rule_file_through_the_delegation():
    """The one argument round 1 does take. A loop restarting from a written file passes it.

    Dropped silently, this would send `EMPTY` to a round-1 call that had rules, and the
    agent's own §1.2 instruction ("emit a complete file") would produce a file with the
    existing rules deleted.
    """
    path = Path(tempfile.mkdtemp()) / "es.yaml"
    path.write_text("version: 3\nlang: es\nrules: []  # round_one_probe\n", encoding="utf-8")
    p = assemble_iteration_prompt(lang="es", corpus="es-meddocan", iteration=1,
                                  rules_path=path)
    assert "round_one_probe" in shown(p)
    assert p.reference()["rules_sha256"] is not None


@pytest.mark.parametrize("name,value", [
    ("metrics", {"modes": {}}),
    ("errors", []),
    ("docs_by_id", {}),
    ("context_chars", 0),
    ("audit_report", {}),
])
def test_round_one_refuses_feedback_rather_than_ignoring_it(name, value):
    """Refused, and the distinction is the point.

    Every value here is falsy, which is the trap: `if metrics:` would drop all five and
    produce a correct round-1 prompt from an incorrect call. The prompt would be right and
    the driver would be wrong, and nothing in the run record would say so — a driver that
    computed round 0's score is a driver whose iteration counter is off by one, and that is
    a defect about the whole arm rather than about this call.
    """
    with pytest.raises(PromptError) as exc:
        assemble_iteration_prompt(lang="es", corpus="es-meddocan", iteration=1,
                                  **{name: value})
    assert name in str(exc.value)
    assert "DESIGN §4" in str(exc.value)


@pytest.mark.parametrize("iteration", [0, -1, 1.0, True, "2", None])
def test_a_round_number_that_is_not_a_round_is_refused(iteration):
    """`True` is an `int` and would assemble round 1's prompt while meaning nothing."""
    with pytest.raises(PromptError):
        assemble_iteration_prompt(lang="es", corpus="es-meddocan", iteration=iteration)


# ── from round 2 every block is filled, and a missing one is not absorbed ──


@pytest.mark.parametrize("missing", [
    "metrics", "errors", "docs_by_id", "context_chars", "audit_report",
])
def test_a_later_round_missing_any_block_is_refused(missing):
    """The mirror of round 1's refusal, and the more consequential direction.

    A round-3 prompt assembled without its score block is `port-oneshot`'s prompt with a
    round number on it. It would run, cost what the round costs, and be reported as a rung
    of the ladder it is not on — the unrecorded change of arm.
    """
    with pytest.raises(PromptError) as exc:
        iterated(**{missing: _ABSENT})
    assert missing in str(exc.value)


def test_the_refusal_names_every_missing_block_at_once():
    """One message, not one per fix-and-rerun cycle."""
    with pytest.raises(PromptError) as exc:
        assemble_iteration_prompt(lang="es", corpus="es-meddocan", iteration=2,
                                  metrics=scored_fixture())
    message = str(exc.value)
    for name in ("errors", "docs_by_id", "context_chars", "audit_report"):
        assert name in message


@pytest.mark.parametrize("name,value", [
    ("errors", []),
    ("context_chars", 0),
])
def test_a_later_round_accepts_an_empty_block_that_was_supplied(name, value):
    """The mirror of round 1's refusal, and it turns on the same two characters.

    A round with no error spans is a real state: the sample can come back empty when the
    fold has nothing left to draw, and `context_chars: 0` is a legitimate — if unlikely —
    experimental setting. Both are falsy. A presence check written as `if value` would
    report them as *missing* and refuse a correct call, so `is None` is load-bearing in
    both directions and neither test alone shows it.
    """
    p = iterated(**{name: value})
    assert isinstance(p, FilledPrompt)
    assert p.reference()["sections_filled"] == list(ITERATION_SECTIONS)


def test_round_two_is_the_first_round_with_feedback():
    """The ladder's boundary, from the other side: round 2 fills what round 1 left empty."""
    ref = iterated(iteration=2).reference()
    assert ref["sections_filled"] == list(ITERATION_SECTIONS)
    assert ref["sections_empty"] == []
    assert ref["feedback_from_iteration"] == 1


def test_the_filled_sections_are_the_empty_ones_of_round_one():
    """Stated as a relation between the two constants rather than as two literals.

    A section added to the template has to appear in one of the two lists, and this is what
    makes the pair exhaustive: round 1's empty set and round 2's filled set are the same
    four names, so a new section cannot be filled in one call and unmentioned in the other.
    """
    assert set(ITERATION_SECTIONS) == set(FILLED_SECTIONS) | set(EMPTY_SECTIONS)
    assert list(ITERATION_SECTIONS) == list(FILLED_SECTIONS) + list(EMPTY_SECTIONS)


# ── §1.3, the score half: the numbers a rule author can act on ──


def test_the_score_block_carries_both_modes():
    """CLAUDE.md: the relaxed leak rate is reported beside the headline as a lower bound.

    A block carrying one mode would make the agent's target whichever one it was shown, and
    the two disagree by construction — a span covered by the union of two predictions and by
    neither alone is leaked under `fully_covered` and not under `relaxed`.
    """
    from src.eval.scorer import FULLY_COVERED, MODES, RELAXED
    text = shown(iterated())
    for mode in MODES:
        assert f"mode: {mode}" in text
    assert iterated().reference()["score_modes"] == sorted(MODES)


def test_the_leak_rate_arrives_with_its_numerator_and_denominator():
    """A rate on its own cannot be acted on: 0.5 of four spans is not 0.5 of five thousand."""
    metrics = scored_fixture()
    block = metrics["modes"]["fully_covered"]
    text = shown(iterated(metrics=metrics))
    assert f"{block['leak']['leaked']} leaked of {block['leak']['denominator']}" in text


def test_every_rule_that_fired_has_a_row_with_its_layer():
    """§1.3's revision half. `by_rule` is what makes deletion possible rather than addition.

    Asserted against the scorer's own `by_rule` keys, so a rule the scorer attributes and
    the block drops is a failure here rather than a quiet omission from the prompt.
    """
    metrics = scored_fixture()
    by_rule = metrics["modes"]["fully_covered"]["by_rule"]
    assert by_rule, "the fixture has no rule attribution — the geometry has drifted"
    text = shown(iterated(metrics=metrics))
    for rule_id, row in by_rule.items():
        assert rule_id in text, f"{rule_id} fired and has no row in §1.3"
        assert row["layer"] in text
    assert iterated(metrics=metrics).reference()["score_rules"] == sorted(by_rule)


def test_a_rule_that_fires_and_never_matches_is_visible_as_such():
    """The deletion candidate. `fires` high and `tp` zero has to be readable off the row."""
    metrics = scored_fixture()
    row = metrics["modes"]["fully_covered"]["by_rule"][FIX_DATE]
    assert row["tp"] == 0 and row["fires"] > 0, "the misfire fixture no longer misfires"
    text = shown(iterated(metrics=metrics))
    line = [ln for ln in text.splitlines() if FIX_DATE in ln]
    assert line, "the misfiring rule has no row"
    assert "fires" in text and "tp" in text
    assert "deletion candidate" in text


def test_the_two_readings_the_template_requires_are_stated_beside_the_numbers():
    """§1.3's two cautions, restated where the table is.

    Both are already in the committed template three thousand tokens above this block. They
    are repeated because a caution the reader has already scrolled past is a caution that
    did not arrive — and both change what to conclude: `fp` is unmatched-in-the-assignment
    rather than uncovered, and `by_rule` does not sum to the mode's totals.
    """
    text = shown(iterated())
    assert "unmatched-in-the-assignment" in text
    assert "do not sum" in text.lower() or "does not sum" in text.lower()
    assert "Do not reconcile" in text


def test_a_sparse_type_is_marked_where_its_numbers_are():
    """DESIGN §9.4: flagged, never dropped — and flagged in the prompt too.

    The fixture's types all have single-digit gold, so every row is sparse. An agent shown
    a leak rate of 1.000 over one gold span writes a rule for a type it has one example of,
    which is the behaviour §9.4's flag exists to temper.
    """
    metrics = scored_fixture()
    by_type = metrics["modes"]["fully_covered"]["by_type"]
    assert any(row["sparse"] for row in by_type.values()), "no sparse type in the fixture"
    assert "[sparse]" in shown(iterated(metrics=metrics))


def test_an_undefined_rate_reads_as_not_available_and_not_as_zero():
    """`None` from the scorer means undefined, and `0.000` would mean measured-and-clean.

    The scorer writes `None` for a rate whose denominator is zero (`_prf`, `_mean`), which
    is what a type with false positives and no gold has. Rendered as `0.000` it would tell
    the agent that type leaks nothing — the direction that invites a rule for a type the
    fold cannot score.
    """
    metrics = scored_fixture()
    by_type = metrics["modes"]["fully_covered"]["by_type"]
    undefined = [t for t, row in by_type.items() if row["leak_rate"] is None]
    assert undefined, "no type with an undefined rate in the fixture"
    text = shown(iterated(metrics=metrics))
    for phi_type in undefined:
        # Scoped to the score table: §1.1's type list also has a line starting with the
        # type name, and matching that one would pass while the table read 0.000.
        rows = [ln for ln in text.splitlines()
                if ln.strip().startswith(phi_type) and "leak_rate" in ln]
        assert rows, f"{phi_type} has no row in the score table"
        for row in rows:
            # Positional, not just present: this type has gold 0 and a false positive, so
            # its P/R/F1 are a measured 0.000 and only the rate is undefined. An `n/a`
            # anywhere in the line would also pass if `_num` were applied to the wrong field.
            assert "leak_rate   n/a" in row, (
                f"{phi_type}'s undefined leak_rate does not read as n/a"
            )


def test_the_complementarity_families_arrive_with_the_union_only_count():
    """CLAUDE.md's headline pair, both halves.

    `covered_by_union_only` is the number that says a span was hidden by the union and by no
    single layer, which is the fact a rule author acts on differently from a plain miss.
    """
    families = scored_fixture()["modes"]["fully_covered"]["complementarity"]["families"]
    text = shown(iterated())
    for family in families:
        assert family in text
    assert "covered_by_union_only" in text


def test_the_run_and_cost_blocks_are_not_forwarded():
    """§4's cost structure, and §5's line about what the agent may reason about.

    `model_id`, `commit` and `wall_seconds` are facts about the harness. An agent shown its
    own token cost can reason about the budget, which is the orchestrator's decision and not
    the agent's — and the prompt space is allocated to §1.4.
    """
    metrics = json.loads(json.dumps(scored_fixture()))
    metrics["run"] = {"model_id": "us.anthropic.claude-opus-5", "commit": "deadbee",
                      "tree": "clean"}
    metrics["cost"] = {"llm_calls": 9, "prompt_tokens": 123456, "wall_seconds": 78.9}
    text = shown(iterated(metrics=metrics))
    for leaked in ("us.anthropic.claude-opus-5", "deadbee", "123456", "78.9"):
        assert leaked not in text, f"the score block forwards {leaked!r} from the run block"


def test_metrics_with_no_modes_block_is_refused():
    """A round whose score cannot be read is a round the loop cannot iterate from."""
    for bad in ({}, {"modes": {}}, {"modes": None}, {"modes": []}):
        with pytest.raises(PromptError) as exc:
            iterated(metrics=bad)
        assert "modes" in str(exc.value)


def test_a_fold_on_which_no_rule_fired_says_so_rather_than_showing_an_empty_table():
    """Round 2 after a round-1 file that matched nothing — and the distinction it needs.

    The scorer cannot tell a rule that fired nothing from a rule that does not exist: it
    never reads the rule file. The agent holds the file and can, so it is told which of the
    two facts the empty table is.
    """
    from src.eval.scorer import DocPair, Mark, score
    metrics = score([DocPair(doc_id="d", gold=(Mark(0, 5, "NAME", span_index=0),),
                             pred=(Mark(0, 5, "NAME", "tagger", span_index=0),))])
    assert not metrics["modes"]["fully_covered"]["by_rule"]
    text = shown(iterated(metrics=metrics))
    assert "No rule in the current file fired" in text
    assert iterated(metrics=metrics).reference()["score_rules"] == []


# ── §1.3, the audit half: suspicions, and the round they belong to ──


def test_the_audit_flags_arrive_as_offsets_and_types_and_nothing_else():
    """`audit.py`'s schema has no free-text field, and the block adds none.

    The flag table is the most concentrated residual-PHI artefact the loop produces
    (`auditor.md` §2.2), which is why its file is deny-listed — and why what the prompt
    prints from it is a position and a type.
    """
    flag = a_flag()
    text = shown(iterated(audit_report=audit_report(3, flags=[flag])))
    assert flag.doc_id in text and flag.phi_type in text
    assert f"({flag.start}, {flag.end})" in text
    assert SURFACE not in text.split("### 1.4")[0], (
        "the audit block quotes a surface form"
    )


def test_the_flags_are_framed_as_suspicions_where_they_are_printed():
    """§5's failure mode: an agent writing rules to satisfy a peer instead of the corpus.

    The Auditor never sees gold (DESIGN §3), so a flag is one component's belief. The
    framing sits beside the table rather than only in the template, for the reason the §1.3
    cautions do.
    """
    text = shown(iterated(audit_report=audit_report(3, flags=[a_flag()])))
    assert "suspicions, not errors" in text
    assert "never sees gold" in text


def test_the_three_cases_for_reading_a_flag_are_stated():
    """§5's reading, including the prohibition and the highest-value case.

    The case that must be prohibited is flagged-and-not-in-§1.4: unresolvable from what the
    agent holds, so a rule written on it is a rule written on the Auditor's word. The case
    that must be named is in-§1.4-and-not-flagged, which nothing in the table points at.
    """
    text = shown(iterated(audit_report=audit_report(3, flags=[a_flag()])))
    assert "may not write a rule on the Auditor's word alone" in text
    assert "highest-value" in text
    assert "corroborated by gold" in text


# ── §4's `[nn]` marking: where each of the three cases is visible ──


def marked(flags, sample, iteration: int = 3) -> str:
    """A round-3 block with a chosen flag set and a chosen §1.4 sample.

    `docs_by_id` holds one long document for every `doc_id` the sample names, so a test can
    place a sample span anywhere without the renderer refusing a missing document.
    """
    ids = {span.doc_id for span in sample}
    return shown(iterated(
        audit_report=audit_report(iteration, flags=flags),
        errors=list(sample),
        docs_by_id={doc_id: doc(doc_id) for doc_id in ids},
        iteration=iteration,
    ))


def marks_of(text: str) -> list[str]:
    """The `[nn]` marks on the flag rows, in the order the block prints them.

    Read out of the rendered block rather than from a return value, because the mark's whole
    job is to be a number the agent can look up in §1.4 — a correct index rendered onto the
    wrong row would satisfy any assertion over the computation alone.
    """
    body = text.rsplit("**Corroborated by gold", 1)[-1].split("**How to read a flag", 1)[0]
    return [line.split("]")[0].strip("[ ") for line in body.split("\n")
            if line.startswith("  [")]


def test_a_flag_overlapping_a_sample_span_carries_that_spans_number():
    """§4's case 1, and the number is §1.4's own.

    `render_window` prints `[{i:2}]` over the same sequence, so the mark resolves to a block
    the agent already holds — which is what makes corroboration cost four characters instead
    of ±120 of context.
    """
    sample = [err(index=0, start=1000), err(index=1, start=4000),
              err(index=2, start=7000)]
    text = marked([a_flag(start=4000, end=4014)], sample)
    assert marks_of(text) == ["2"], (
        "the flag overlaps the second sample span and must carry its §1.4 number"
    )


def test_the_mark_is_the_index_the_window_actually_printed():
    """The two enumerations are one walk over one list.

    A sort in either place — the marker's or the renderer's — produces marks that point at
    the wrong rows and a block that reads perfectly. Asserted by placing the sample out of
    offset order, which is the state a sort would silently repair.
    """
    sample = [err(index=0, start=7000), err(index=1, start=1000)]
    text = marked([a_flag(start=1000, end=1014)], sample)
    assert marks_of(text) == ["2"]
    window = text.rsplit("### 1.4 Error spans", 1)[-1]
    assert window.index("[ 1] type") < window.index("[ 2] type")
    # `[2]`'s context is the one holding the flagged offset, which is what the mark claims.
    second = window.split("[ 2] type", 1)[-1]
    assert SURFACE in second.split("offsets", 1)[0]


def test_a_flag_overlapping_nothing_carries_no_mark_and_is_listed_separately():
    """§4's case 2. Position, type and score, in a section that says why.

    The prohibition sits in that section rather than only in the three-case list: the agent
    reading a row is at the point of deciding whether to write a rule for it.
    """
    text = marked([a_flag(phi_type="PROFESSION", start=5000, end=5008)],
                  [err(index=0, start=1000)])
    assert marks_of(text) == []
    assert "(5000, 5008)" in text
    assert "may not write a rule for an individual one" in text


def test_the_unresolved_flags_arrive_with_their_per_type_counts():
    """§4: the per-type count is the only use of case 2 that §5 permits.

    "31 unresolved `PROFESSION` flags" is a type priority; no individual row is anything the
    agent may act on. Both counts are printed and labelled apart — the unresolved share and
    the report's own `by_phi_type` — because one alone is a number the agent reads as the
    other.
    """
    flags = [a_flag(phi_type="PROFESSION", start=5000, end=5008),
             a_flag(phi_type="PROFESSION", start=6000, end=6008),
             a_flag(phi_type="NAME", start=1000, end=1014)]
    text = marked(flags, [err(index=0, start=1000)])
    section = text.rsplit("**Unresolved", 1)[-1]
    assert "unresolved by type   PROFESSION 2" in section
    assert "all flags by type    NAME 1, PROFESSION 2" in section


def test_a_missed_span_with_no_mark_is_named_as_the_highest_value_case():
    """**§4's case 3, and the reason it is a number rather than an absence.**

    Nothing in a table of what *was* flagged points at what was not. §5 calls this the
    highest-value case in the loop and the easiest to skip, and an agent reading only the
    flags would skip it — so the count and the §1.4 numbers are stated outright.
    """
    sample = [err(index=0, start=1000), err(index=1, start=4000),
              err(index=2, start=7000)]
    text = marked([a_flag(start=4000, end=4014)], sample)
    case3 = text.rsplit("In §1.4 and not flagged", 1)[-1]
    assert "2 of the 3 `missed` spans" in case3
    assert "[1], [3]" in case3


def test_a_false_positive_in_the_sample_is_not_a_case_three():
    """**The kind is load-bearing, and this is the defect that would inflate the count.**

    A `false_positive` sample span is one the arm predicted and gold does not have, so it was
    *masked* before the Auditor read the document — there was nothing left for a flag to
    overlap. Counting it as case 3 would report "both mechanisms missed it" about an
    identifier that is not in gold at all, and the number would grow with the arm's false
    positive rate: the direction that makes the highest-value case look common while the real
    ones stay buried in it.
    """
    fp = ErrorSpan(doc_id="dev1", span_index=1, phi_type="NAME", kind=FALSE_POSITIVE,
                   start=4000, end=4014)
    sample = [err(index=0, start=1000), fp]
    text = marked([a_flag(start=1000, end=1014)], sample)
    case3 = text.rsplit("In §1.4 and not flagged", 1)[-1]
    assert "0 of the 1 `missed` span" in case3
    assert "none this round" in case3


def test_overlap_and_not_byte_equality_is_what_marks_a_flag():
    """§4, and the reason equality would mark almost nothing.

    The Auditor's offsets were translated from a masked document; the sample's are gold
    extents from the scorer. Two components measuring one identifier through different
    geometry agree that it is there, not on where it stops — so a one-character overlap is a
    mark, and equality would produce an empty corroborated table that reads as an Auditor
    corroborating nothing.
    """
    sample = [err(index=0, start=1000)]          # gold extent (1000, 1014)
    for start, end in [(995, 1001), (1013, 1020), (1000, 1014), (1002, 1005),
                       (900, 1100)]:
        text = marked([a_flag(start=start, end=end)], sample)
        assert marks_of(text) == ["1"], (start, end)


@pytest.mark.parametrize("start,end", [(986, 1000), (1014, 1028)])
def test_a_flag_touching_a_sample_span_is_not_marked(start, end):
    """Half-open extents sharing only a boundary are adjacent identifiers, `mask_document`'s
    rule.

    A flag on the name must not be marked as corroborating the date that follows it: the mark
    would send the agent to a §1.4 context whose span is not the one it flagged, and case 1
    is supposed to be the case where the context is already there.
    """
    text = marked([a_flag(start=start, end=end)], [err(index=0, start=1000)])
    assert marks_of(text) == []


def test_a_flag_in_another_document_at_the_same_offsets_is_not_marked():
    """`doc_id` before offsets. Two documents share an offset range as a matter of course, and
    a mark computed on offsets alone would corroborate a flag with a gold span it has nothing
    to do with."""
    sample = [err(doc_id="dev1", index=0, start=1000)]
    text = marked([a_flag(doc_id="dev2", start=1000, end=1014)], sample)
    assert marks_of(text) == []


def test_a_flag_over_two_adjacent_sample_spans_takes_the_lower_number():
    """One flag can cover two gold identifiers — the scorer emits a missed span per gold span
    — and the mark must not depend on iteration order.

    The lowest §1.4 index, so the same inputs produce the same block. Both spans still count
    as reached: neither is case 3, because the Auditor did flag the text they sit in.
    """
    sample = [err(index=0, start=1000), err(index=1, start=1020)]
    text = marked([a_flag(start=990, end=1040)], sample)
    assert marks_of(text) == ["1"]
    # **The asymmetry the block depends on**: one index is printed, both are counted as
    # reached. A case-3 count taken from the printed marks alone would report `[2]` as the
    # highest-value case in the loop — "both mechanisms missed it" about a span the Auditor
    # pointed straight at — and nothing in the block would show the agent that it had.
    case3 = text.rsplit("In §1.4 and not flagged", 1)[-1]
    assert "0 of the 2 `missed` spans" in case3
    assert "none this round" in case3


def test_the_marked_and_unmarked_tables_both_say_so_when_empty():
    """Zero is a measurement in both directions.

    No corroborated flag means the two mechanisms are pointing at different places, which is
    information about both; no unresolved flag means every suspicion is backed by gold. An
    empty table with no sentence reads as a harness that produced half a block.
    """
    none_marked = marked([a_flag(start=5000, end=5008)], [err(index=0, start=1000)])
    assert "pointing at different places" in none_marked
    all_marked = marked([a_flag(start=1000, end=1014)], [err(index=0, start=1000)])
    assert "Every flag this round is corroborated" in all_marked


def test_the_marking_adds_no_corpus_text_to_the_block():
    """**§4's bound, and the reason the mark is an index.**

    Rendering ±120 characters around every flag would make the RuleAuthor's window §1.4's 40
    spans *plus* every flag's context — unbounded, growing with the Auditor's false positive
    rate, and outside what `config/sampling.yaml` fixes. A mark is a reference into a block
    the agent already has.
    """
    text = marked([a_flag(start=1000, end=1014),
                   a_flag(phi_type="PROFESSION", start=5000, end=5008)],
                  [err(index=0, start=1000)])
    block = text.rsplit("**Corroborated by gold", 1)[-1].split("### 1.4 Error spans", 1)[0]
    assert SURFACE not in block, "the marking put a surface form in the audit block"


def test_the_reference_form_records_the_three_cases_as_three_numbers():
    """What makes the marking measurable across rounds and across arms.

    Recorded rather than left to a reader holding the report and the sample: the overlap is
    the assembler's arithmetic, and the flag counts and the span count are not
    interchangeable — one flag covering two adjacent gold identifiers is one corroborated
    flag and two reached spans.
    """
    sample = [err(index=0, start=1000), err(index=1, start=4000),
              err(index=2, start=7000)]
    p = iterated(
        audit_report=audit_report(3, flags=[a_flag(start=4000, end=4014),
                                            a_flag(phi_type="PROFESSION", start=5000,
                                                   end=5008)]),
        errors=sample,
        docs_by_id={"dev1": doc()})
    ref = p.reference()
    assert ref["audit_flags_corroborated"] == 1
    assert ref["audit_flags_unresolved"] == 1
    assert ref["audit_unflagged_missed_spans"] == 2
    assert ref["audit_sample_missed_spans"] == 3


def test_the_sample_is_required_rather_than_defaulted_to_empty():
    """**An empty default would delete the marking without a symptom.**

    Every flag would render as case 2, case 1 would not exist and case 3 would be invisible —
    a well-formed block in which §4's whole mechanism is gone. So `_audit_block` has no
    default for it, and the assembler that has the sample is the one that calls it.
    """
    import inspect
    signature = inspect.signature(prompt_module._audit_block)
    assert signature.parameters["sample"].default is inspect.Parameter.empty


def test_the_marked_sample_is_the_rendered_sample():
    """One sequence to both, asserted structurally.

    A copy, a filter or a sort between the two calls is the edit that breaks the
    correspondence, and it breaks it silently: both blocks render, and the marks point at
    rows that describe other spans.
    """
    fn = functions(tree())["assemble_iteration_prompt"]
    audit_call = window_call = None
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else ""
            if name == "_audit_block":
                audit_call = node
            elif name == "render_window":
                window_call = node
    assert audit_call is not None and window_call is not None
    passed = [kw.value for kw in audit_call.keywords if kw.arg == "sample"]
    assert len(passed) == 1 and isinstance(passed[0], ast.Name), (
        "_audit_block's sample is not a bare name — a copy or a sort between the two calls "
        "is what makes the marks point at the wrong §1.4 rows."
    )
    assert isinstance(window_call.args[0], ast.Name)
    assert passed[0].id == window_call.args[0].id, (
        f"_audit_block marks {passed[0].id} and render_window renders "
        f"{window_call.args[0].id}. §4's `[nn]` is the number the window printed."
    )


def test_no_flags_is_reported_as_a_measurement():
    """Zero is a measurement, absent is not (`DocumentAudit`'s docstring).

    An empty table with no sentence would read as a harness that failed to audit. It is
    also where the block has to stop the agent from concluding too much: nothing survived
    *in the Auditor's judgement of the masked text* is not nothing leaked.
    """
    report = audit_report(3, flags=[])
    assert report["counts"]["flags"] == 0
    text = shown(iterated(audit_report=report))
    assert "No flags." in text
    assert "not a statement that nothing" in text


def test_the_counts_line_separates_audited_from_flagged_from_refused():
    """Four numbers that a single "118 flags" would collapse into one.

    A document audited with no flags and a document never audited are different facts, and
    a refused flag is a third — it was returned and dropped, which the agent should not read
    as the Auditor having stayed silent.
    """
    from src.porting.audit import OUT_OF_RANGE, Refusal
    report = audit_report(3, flags=[a_flag()], documents_audited=4,
                          refused=[Refusal(doc_id="dev1", reason=OUT_OF_RANGE)])
    text = shown(iterated(audit_report=report))
    assert "1 flags, 4 documents audited, 3 of them with no flags" in text
    assert "1 returned flags were refused" in text
    ref = iterated(audit_report=report).reference()
    assert (ref["audit_flags"], ref["audit_refused"], ref["audit_documents"]) == (1, 1, 4)


def test_a_refused_flags_position_does_not_reach_the_prompt():
    """`auditor.md` §2.2: a refused flag keeps its doc_id and its reason and nothing else.

    Half of those refusals *are* the judgement that the position cannot be trusted, and a
    printed untrustworthy position would pass for part of the residual map.
    """
    from src.porting.audit import Refusal, CROSSES_A_LINE
    report = audit_report(3, refused=[Refusal(doc_id="dev1", reason=CROSSES_A_LINE)])
    assert all("start" not in r for r in report["refused"])
    text = shown(iterated(audit_report=report))
    assert "and are not shown" in text


def test_the_report_must_be_this_rounds_and_must_audit_the_previous_one():
    """`auditor.md`'s banner, both numbers, checked against *this* call.

    The Auditor runs as round n's first step, so its report is written under round n and
    what it read was round n−1's spans. `audit.report()` validates that the pair agrees with
    itself, which an off-by-one driver satisfies — it records the round it was told. Only the
    reader knows which round it is, so both numbers are checked here.
    """
    good = audit_report(3)
    assert (good["iteration"], good["masked_from_iteration"]) == (3, 2)
    assert iterated(iteration=3, audit_report=good)          # accepted

    stale = audit_report(2)                                  # round 2's report, at round 3
    with pytest.raises(PromptError) as exc:
        iterated(iteration=3, audit_report=stale)
    assert "iteration 2 and this is iteration 3" in str(exc.value)

    # This round's file, but it audited two rounds back — internally consistent to
    # `report()` at the round it was told, and wrong for this call.
    with pytest.raises(PromptError) as exc:
        iterated(iteration=3, audit_report=audit_report(3, masked_from=1))
    assert "audited iteration 1" in str(exc.value)

    # A report that audited its own round: the arm auditing its own unwritten output.
    with pytest.raises(PromptError) as exc:
        iterated(iteration=3, audit_report=audit_report(3, masked_from=3))
    assert "reads an audit of 2" in str(exc.value)


def test_the_heading_names_the_round_the_flags_describe():
    """Which predictions were audited, not which directory the file sits in.

    `iter{n}/audit_report.json` holding round n's audit *of* round n−1 is a fact about the
    layout. The agent is being told what the flags are about, and a heading naming n would
    make it read the flags against the file it is currently editing.
    """
    text = shown(iterated(iteration=4, audit_report=audit_report(4)))
    assert "Auditor report on iteration 3's output" in text


@pytest.mark.parametrize("bad", [[], "", 0, 3, [{"doc_id": "dev1"}]])
def test_a_report_that_is_not_a_mapping_is_refused(bad):
    """The type, separately from the round.

    `None` is not in this list: an absent report is the missing-block refusal above, and a
    parametrisation that mixed the two would pass on the wrong message. A list of flags is
    here because it is the plausible mistake — passing `report["flags"]` instead of the
    report.
    """
    with pytest.raises(PromptError) as exc:
        iterated(audit_report=bad)
    assert "must be a mapping" in str(exc.value)


# ── §1.4: the window, through the renderer and not re-rendered ──


def test_the_error_spans_come_through_the_renderer():
    """Structural. §1.4 is the block with corpus text in it, and it has one renderer.

    A second slicing of `doc.text` inside the assembler would be a second place the
    context-window discipline is established by hand — the state `FilledPrompt` and
    `test_no_other_module_slices_document_text_for_a_prompt` exist to leave. Here the
    renderer is in the same module, so the file-level check cannot see it; the call is what
    is asserted.
    """
    fn = functions(tree())["assemble_iteration_prompt"]
    assert "render_window" in calls_named(fn)
    for form in ast.walk(fn):
        assert not (isinstance(form, ast.Subscript)
                    and isinstance(form.value, ast.Attribute)
                    and form.value.attr == "text"), (
            "assemble_iteration_prompt slices document text itself. §1.4 is rendered by "
            "render_window() and by nothing else."
        )


def test_the_assembler_does_not_draw_the_sample():
    """The seed is applied in one place (`sample.draw()`), and this is not it.

    DESIGN §11.1 rests the arm comparison on both arms drawing through one function. An
    assembler that drew would be a second application of the seed, and the two would agree
    until one of them was edited.
    """
    reached = calls_named(functions(tree())["assemble_iteration_prompt"])
    for name in ("draw", "draw_iteration", "initial_error_pool", "error_spans"):
        assert name not in reached, (
            f"assemble_iteration_prompt calls {name}. The caller draws and this renders "
            "(DESIGN §11.1)."
        )


def test_the_window_reaches_the_prompt_with_its_offsets_and_its_context():
    """§1.4's four lines per span, arriving through the assembled prompt.

    The window is what the rule author reads, so the assertion is that it is *in* the
    prompt — a `render_window()` call whose result was dropped would satisfy the structural
    check above and send a heading with nothing under it.
    """
    text = shown(iterated())
    assert SURFACE in text, "the assembled prompt carries no window"
    assert "type      NAME" in text
    assert "within that context" in text


def test_the_context_width_is_stated_where_the_windows_are():
    """±n characters, and that the offsets are window-relative rather than document ones.

    An agent reading document offsets goes looking for the surrounding text, which is the
    unbounded window DESIGN §11.1 rejects.
    """
    text = shown(iterated(context_chars=40))
    assert "±40 characters" in text
    assert "within its own context window" in text


def test_the_error_span_reference_is_nested_and_not_merged():
    """Two hashes under two keys a reader would have to tell apart, avoided.

    The window's own reference carries `text_sha256` and so does the prompt's. Merged, the
    prompt's record would hold one of them under a name that means the other.
    """
    ref = iterated().reference()
    assert ref["error_spans"]["block"] == "error_spans"
    assert ref["error_spans"]["n_spans"] == 1
    assert ref["error_spans"]["text_sha256"] != ref["text_sha256"]
    assert ref["error_spans"]["context_chars"] == 120


def test_the_window_that_was_rendered_is_the_window_that_was_passed():
    """The reference resolves to the spans the caller drew, by `(doc_id, span_index)`.

    DESIGN §11.2's referent. A block assembled from a different sample than the one the run
    recorded would be undetectable from the record, which is the whole reason the record
    carries the references rather than a count.
    """
    spans = [err(index=0, start=1000), err(index=3, start=1000)]
    ref = iterated(errors=spans, docs_by_id={"dev1": doc()}).reference()
    assert [s["span_index"] for s in ref["error_spans"]["spans"]] == [0, 3]


# ── the assembled prompt is the type, and it records what it carried ──


def test_the_iteration_prompt_is_a_filled_prompt():
    """The premise, and here it is not incidental: this block *does* carry corpus text."""
    p = iterated()
    assert isinstance(p, FilledPrompt)
    assert not isinstance(p, str)


def test_the_iteration_assembler_returns_only_a_filled_prompt_call():
    """Both returns: the delegation and the assembly.

    The delegated one is `assemble_task_prompt`, which its own test pins to a
    `FilledPrompt(...)` construction — so the set of allowed returns is two names and a
    bare-string return is caught either way.
    """
    fn = functions(tree())["assemble_iteration_prompt"]
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert returns
    for node in returns:
        value = node.value
        assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and \
            value.func.id in {"FilledPrompt", "assemble_task_prompt"}, (
            "assemble_iteration_prompt returns something other than a FilledPrompt"
        )


def test_the_reference_form_holds_no_text_and_serialises():
    """What a run record may keep: counts, references, hashes, lengths.

    The window is in this prompt, so this is the reference form where a leak would land.
    Checked against the rendered JSON rather than against the values, because a surface
    form nested three levels down inside `error_spans` is what a per-key assertion misses.
    """
    ref = iterated().reference()
    blob = json.dumps(ref)
    assert SURFACE not in blob
    assert ref["text_chars"] == len(iterated())
    assert ref["text_sha256"].startswith("sha256:")
    assert ref["block"] == "iteration"


def test_the_reference_form_names_the_round_and_the_round_the_feedback_came_from():
    """Arithmetic a reader should not have to do, and a convention they should not have
    to know — `audit.report()`'s `masked_from_iteration`, at the prompt layer."""
    ref = iterated(iteration=5, audit_report=audit_report(5)).reference()
    assert ref["iteration"] == 5
    assert ref["feedback_from_iteration"] == 4
    assert ref["audit_iteration"] == 5
    assert ref["audit_masked_from_iteration"] == 4


def test_the_reference_form_hashes_the_blocks_the_call_actually_saw():
    """The rendered blocks, not the source artefacts — and the reason is this module's.

    Hashing `metrics.json` or the report would need a canonical serialisation, and this
    module does not import `json` (`test_the_module_imports_nothing_that_writes` holds that
    as a closed set). The rendered block is what the call saw, which is the question a
    reference form answers; the sources are at `paths.itermetrics` and `paths.auditreport`
    under the rounds these fields name.
    """
    one, two = scored_fixture(), scored_fixture()
    two["modes"]["fully_covered"]["leak"]["leaked"] += 1
    assert iterated(metrics=one).reference()["score_block_sha256"] != \
        iterated(metrics=two).reference()["score_block_sha256"]
    assert iterated(audit_report=audit_report(3, flags=[a_flag()])) \
        .reference()["audit_block_sha256"] != \
        iterated(audit_report=audit_report(3)).reference()["audit_block_sha256"]


def test_the_prompt_is_hashed_against_the_window_files():
    """The template and the sampling config, as `window_freeze.json` records them.

    An iteration prompt whose record named only the round would agree with a doubled `n`
    as readily as with 40 — `render_window`'s reason, at the level that assembles it.
    """
    ref = iterated().reference()
    assert set(ref["window_files"]) == set(WINDOW_FILES)
    for value in ref["window_files"].values():
        assert value.startswith("sha256:")


def test_the_template_travels_with_every_round_and_not_only_the_first():
    """§2's schema and §4's prohibitions, on round 7 as much as on round 1.

    A later round is where dropping them would look harmless — the agent has answered this
    prompt before — and it is where the emitted file's schema would drift with nothing
    saying why.
    """
    template = (ROOT / "docs" / "prompts" / "rule_author.md").read_text(encoding="utf-8")
    assert template in shown(iterated(iteration=7, audit_report=audit_report(7)))


def test_every_round_says_to_emit_the_whole_file():
    """The characteristic failure of this loop is a file that only grows (§5).

    Round 1 has nothing to delete. From round 2 revision includes deleting and narrowing,
    so the instruction is not the same sentence as round 1's and is asserted separately.
    """
    text = shown(iterated())
    assert "Emit the complete rules/es.yaml" in text
    assert "not a patch" in text
    assert "deleting and narrowing" in text


# ─── the text has no accessor that is not named for a destination ────────────


def test_the_text_is_not_reachable_as_an_attribute():
    """`__slots__` closes the attribute set, so there is no `.text` and no adding one."""
    p = a_prompt()
    assert not hasattr(p, "text")
    assert not hasattr(p, "__dict__")
    with pytest.raises(AttributeError):
        p.text = "anything"                                   # type: ignore[attr-defined]


def test_str_and_repr_do_not_carry_the_text():
    """The case that is nobody's decision: a traceback, an f-string, a `print` of the object.

    An exception raised while a filled prompt is in scope reaches a terminal and a CI log,
    and `tools/release_screen.py` does not go there (CLAUDE.md).
    """
    p = a_prompt()
    for rendered in (str(p), repr(p), f"{p}", f"{p!r}", "{}".format(p)):
        assert SURFACE not in rendered
        assert "FilledPrompt" in rendered


def test_the_object_is_not_json_serialisable_into_its_text():
    """`json.dumps` on the object fails rather than emitting the text.

    Stated as a test because the plausible alternative — a `__json__`-ish convenience or a
    dataclass — would make `json.dumps(record)` on a record holding a prompt write the
    corpus into a log, and that call is the ordinary thing to do with a record.
    """
    with pytest.raises(TypeError):
        json.dumps({"prompt": a_prompt()})
    # The reference form is what a record holds, and it serialises.
    json.dumps({"prompt": a_prompt().reference()})


def test_a_redirected_stream_is_refused():
    """`> window.txt` is the file §6 says must not exist, and it is one keystroke away."""
    with pytest.raises(PromptError) as exc:
        a_prompt().to_terminal(io.StringIO())
    assert "terminal" in str(exc.value)
    assert SURFACE not in str(exc.value)


def test_an_object_that_cannot_answer_isatty_is_refused_rather_than_assumed():
    """A stream with no `isatty` is not a terminal. Refusing is the safe default."""
    class Sink:
        def write(self, _):
            raise AssertionError("written to despite having no isatty")

    with pytest.raises(PromptError):
        a_prompt().to_terminal(Sink())


def test_the_transport_exit_returns_the_text():
    """The other exit does its job — otherwise the type would be unusable and removed."""
    assert SURFACE in a_prompt().for_transport()


# ─── the reference form is what may be recorded ──────────────────────────────


def test_the_reference_form_carries_references_and_no_text():
    ref = a_prompt().reference()
    blob = json.dumps(ref)
    assert SURFACE not in blob
    assert ref["spans"] == [{"doc_id": "dev1", "span_index": 0, "phi_type": "NAME",
                             "kind": MISSED, "start": 1000, "end": 1014}]
    assert ref["n_spans"] == 1
    assert ref["context_chars"] == 120
    assert ref["text_chars"] > 0
    assert ref["text_sha256"].startswith("sha256:")


def test_the_reference_form_records_every_window_file():
    """Every prompt template and the sampling config, hashed — six files since 2026-09-02.

    All of them, for `src/sample.py`'s reason: a record naming only the RuleAuthor template
    would agree with a doubled `n` as readily as with 40, and — since 2026-08-12 — with a
    rewritten Auditor as readily as with the frozen one. `port-multi`'s three prompts joined on
    2026-09-02, and the argument did not change shape: a window naming `rule_author.md` alone is
    equally true of the run that edited `profiler.md` and the run that did not.

    Read off `WINDOW_FILES` rather than listed, deliberately. This assertion is about the
    reference form carrying *the whole window*, not about which files are in it; the window's
    membership is DESIGN §5.5's and is pinned by `tests/test_sample.py`. A literal list here
    would be a second copy that has to be edited on the same day — which is what the count
    below was, at 3, on the day the window went to 6.
    """
    files = a_prompt().reference()["window_files"]
    assert set(files) == set(WINDOW_FILES)
    # Asserted against `WINDOW_FILES` and not against a number, for the reason above. It is not
    # redundant beside the set comparison: `window_files` is built by a comprehension over
    # `WINDOW_FILES`, so a duplicate entry there would collapse into one key and the two sets
    # would still agree.
    assert len(files) == len(WINDOW_FILES)
    assert all(v.startswith("sha256:") for v in files.values())


def test_the_reference_form_cannot_be_mutated_through_the_object():
    """A record taken is a record that stays taken."""
    p = a_prompt()
    ref = p.reference()
    ref["n_spans"] = 999
    assert p.reference()["n_spans"] == 1


def test_two_renders_of_the_same_window_are_equal_without_holding_text():
    """Equality by content hash: a test can assert identity without a text comparison."""
    assert a_prompt() == a_prompt()
    other = render_window([err(start=500)], {"dev1": doc()}, 120)
    assert a_prompt() != other


# ─── the masker: DESIGN §3's union rule, and the geometry `_check_tags` wants ─


def masked(text: str, spans, doc_id: str = "dev1") -> MaskedDocument:
    """Mask a document built from an invented string. No corpus is read here."""
    return mask_document(
        Document(doc_id=doc_id, corpus_id="es-meddocan", text=text, split="dev"), spans)


def pred(start: int, end: int, phi_type: str, text: str) -> Span:
    """One *prediction*, with the provenance a detected span carries (DESIGN §3)."""
    return Span(start=start, end=end, surface=text[start:end], subtype="rule",
                phi_type=phi_type, layer="context_cue", detector="R",
                rule_id=f"es:r{start}")


class _Row:
    """A deserialised `spans.jsonl` row: the three attributes the masker reads.

    Not a `Span`, and that is the point of it. `Span` validates its offsets and its type at
    construction, so a test building one cannot reach the masker's own checks — but the
    caller that *can* is the loop driver, which reads rows back from a file and hands over
    whatever they say. This is that shape.
    """

    def __init__(self, start, end, phi_type):
        self.start, self.end, self.phi_type = start, end, phi_type


def row(start, end, phi_type) -> _Row:
    return _Row(start, end, phi_type)


def block_of(m: MaskedDocument) -> str:
    """The rendered block, through the exit named for a destination.

    `for_transport()` rather than a `FakeTerminal`, because that is the exit the loop
    driver uses for this block and a test asserting over the other one would be asserting
    about the path nothing takes.
    """
    return m.block.for_transport()


def masked_lines(m: MaskedDocument) -> list[str]:
    """The block's lines with `auditor.md` §1.3's prefix stripped — column 0 onward."""
    return [line.split(LINE_SEPARATOR, 1)[1] for line in block_of(m).split("\n")]


def round_trip(m: MaskedDocument, text: str) -> None:
    """Every column outside a tag translates to the document character it stands for.

    **The strongest available check on the map, and it is a check on the *pair* of
    components rather than on the masker alone.** The masker's tags are fed to the real
    `validate_flags()`, which runs `_check_tags` at construction and `_to_document` per
    flag, so a wrong column, a wrong document extent or a wrong order fails here — and
    fails as the arithmetic a flag would actually get, not as a shape assertion.

    One column at a time rather than a whole span: a translation that was wrong by a
    constant would still map some span to some plausible extent, and per-character
    comparison has no room for that.
    """
    lines = masked_lines(m)
    assert len(lines) == len(m.lines)
    for index, (raw, line) in enumerate(zip(lines, m.lines)):
        assert len(raw) == line.length, (
            f"line {index}: the block renders {len(raw)} characters and MaskedLine says "
            f"{line.length}. The Auditor's columns are counted over what it was shown.")
        for column, character in enumerate(raw):
            if any(col <= column < col + length for col, length, _, _ in line.tags):
                continue
            result = validate_flags(
                {"flags": [{"line": index, "start": column, "end": column + 1,
                            "phi_type": "NAME"}]},
                doc_id=m.doc_id, lines=list(m.lines))
            assert not result.refused, (index, column, result.refused)
            flag = result.flags[0]
            assert text[flag.start:flag.end] == character, (
                f"line {index} column {column} translated to document offset "
                f"{flag.start}, which holds a different character.")


def test_the_masker_returns_a_filled_prompt_and_not_a_string():
    """DESIGN §3, `auditor.md` §6 — and here the exposure is the largest in the project.

    The masked dev fold is about 77× §1.4's window and most of the identifiers in it are
    *unmasked*, because unmasked is what "leaked" means. So this is the case the type was
    made for rather than the case that could be excused from it.
    """
    m = masked("Ana vive aqui", [pred(0, 3, "NAME", "Ana vive aqui")])
    assert isinstance(m.block, FilledPrompt)
    assert not isinstance(m.block, str)


def test_the_masked_document_holds_the_text_nowhere_but_the_block():
    """`MaskedLine.text` was this mistake one level down and is gone (`audit.py`).

    Asserted over the dataclass's fields and slots rather than over its docstring: the
    plausible edit is a `masked: str` added "so a test can read it", and it would satisfy
    every annotation here.
    """
    fields = {f.name for f in dataclasses.fields(MaskedDocument)}
    assert fields == {"doc_id", "block", "lines"}
    assert set(MaskedDocument.__slots__) == fields


def test_a_detected_span_becomes_its_type_tag():
    text = "Paciente Ana, 40 anos"
    m = masked(text, [pred(9, 12, "NAME", text), pred(14, 16, "AGE", text)])
    assert masked_lines(m) == ["Paciente [NAME], [AGE] anos"]
    round_trip(m, text)


def test_nothing_else_is_changed():
    """`auditor.md` §1.2: the tags and nothing else.

    Newlines survive — unlike `render_window()`, which flattens them because one block is
    one span there. Here the line structure *is* the coordinate scheme.
    """
    text = "linea uno\n\nAna\tPerez  y  otros\n"
    m = masked(text, [pred(11, 20, "NAME", text)])
    assert masked_lines(m) == ["linea uno", "", "[NAME]  y  otros", ""]
    round_trip(m, text)


def test_a_document_with_no_predictions_is_masked_to_itself():
    """Iteration 2 after a round that predicted nothing — today's rule file on dev, in fact.

    Not an error and not an empty block: the Auditor reads a document with no tags, which
    is the state where *every* identifier in it is residual.
    """
    text = "Ana vive aqui"
    m = masked(text, [])
    assert masked_lines(m) == [text]
    assert m.counts == {"n_input_spans": 0, "n_tags": 0, "n_heterogeneous_tags": 0,
                        "n_overlapping_pairs": 0}
    round_trip(m, text)


# ─── the union rule (DESIGN §3), both halves ─────────────────────────────────


def test_overlapping_spans_of_one_type_are_one_tag_naming_that_type():
    """Homogeneous: nothing is being decided, so the type prints."""
    text = "abcdefghij"
    m = masked(text, [pred(2, 6, "NAME", text), pred(4, 8, "NAME", text)])
    assert masked_lines(m) == ["ab[NAME]ij"]
    assert m.counts["n_tags"] == 1
    assert m.counts["n_heterogeneous_tags"] == 0
    round_trip(m, text)


def test_overlapping_spans_of_different_types_print_no_type():
    """**The load-bearing half.** DESIGN §3's example: `NAME` [10,25) and `ORGANISATION`
    [20,34) mask as one tag over [10,34) that names neither.

    Naming either would give the masker a merge policy, which is the thing `RuleSet.detect`
    preserves overlaps in order *not* to do (§4, §9.3) — and it would make a heterogeneous
    union indistinguishable in the masked text from a homogeneous one.
    """
    text = "." * 40
    m = masked(text, [pred(10, 25, "NAME", text), pred(20, 34, "ORGANISATION", text)])
    tag = masked_tag_heterogeneous()
    assert masked_lines(m) == ["." * 10 + tag + "." * 6]
    assert "NAME" not in block_of(m) and "ORGANISATION" not in block_of(m)
    assert m.counts["n_heterogeneous_tags"] == 1
    round_trip(m, text)


def test_the_heterogeneous_tag_is_read_from_the_config(monkeypatch, request):
    """Not spelled in the masker — CLAUDE.md, and `test_masked_tag.py` asserts it of `src/`.

    Monkeypatched to an unmistakable value so that a literal in the module would leave the
    block showing the old one. The value names no type, or the accessor would refuse it.
    """
    from src.corpora import base

    # `naming()` is `lru_cache`d, so the cache is cleared before the patch and after it —
    # `test_masked_tag.py` does the same through an autouse fixture. Cleared through the
    # captured function rather than through `base.naming`, which is the patched name by then.
    cached = base.naming
    cached.cache_clear()
    request.addfinalizer(cached.cache_clear)
    real = cached()
    invented = "[REDACTED_BY_CONFIG]"
    monkeypatch.setattr(base, "naming",
                        lambda: {**real, "masked_tag_heterogeneous": invented})
    text = "." * 20
    m = masked(text, [pred(2, 8, "NAME", text), pred(6, 12, "DATE", text)])
    assert invented in block_of(m)


def test_a_chain_of_overlaps_is_one_tag_however_the_spans_arrive():
    """Transitive, and order-independent — both halves of DESIGN §3's first clause.

    A-B and B-C overlap while A and C do not touch. Every permutation of the three has to
    give one tag over the whole chain: an implementation that compared each span only to
    the previous one in arrival order would produce two tags for some orderings, and each
    of those maskings is well-formed and wrong.
    """
    text = "." * 30
    spans = [pred(0, 10, "NAME", text), pred(8, 18, "NAME", text),
             pred(16, 26, "NAME", text)]
    seen = set()
    for permutation in itertools.permutations(spans):
        m = masked(text, list(permutation))
        assert m.counts["n_tags"] == 1
        assert masked_lines(m) == ["[NAME]" + "." * 4]
        seen.add(block_of(m))
        round_trip(m, text)
    assert len(seen) == 1, "the masking depended on the order the spans arrived in"


def test_the_type_of_a_chain_is_heterogeneous_if_any_link_disagrees():
    """The chain's types are collected across the whole chain, not pairwise.

    A `NAME`-`NAME`-`DATE` chain is one tag and it is heterogeneous, because the union it
    covers is one extent the arm's detectors gave two types to.
    """
    text = "." * 30
    m = masked(text, [pred(0, 10, "NAME", text), pred(8, 18, "NAME", text),
                      pred(16, 26, "DATE", text)])
    assert m.counts["n_tags"] == 1
    assert m.counts["n_heterogeneous_tags"] == 1
    assert masked_lines(m) == [masked_tag_heterogeneous() + "." * 4]


def test_two_identical_predictions_of_different_types_are_one_heterogeneous_tag():
    """Two rules matching the same extent and disagreeing about it.

    Not a degenerate case to be filtered: it is exactly the disagreement the rule is for,
    and it is what `RuleSet.detect` returns when two rules fire on one string.
    """
    text = "abcdef"
    m = masked(text, [pred(1, 4, "NAME", text), pred(1, 4, "ORGANISATION", text)])
    assert masked_lines(m) == ["a" + masked_tag_heterogeneous() + "ef"]
    assert (m.counts["n_tags"], m.counts["n_heterogeneous_tags"],
            m.counts["n_overlapping_pairs"]) == (1, 1, 1)
    round_trip(m, text)


def test_touching_spans_are_two_tags():
    """Adjacency is the common case and is not overlap.

    393 gold pairs sit within one character on es-meddocan dev (DESIGN §3), so a masker
    that unioned abutting spans would collapse ordinary pairs into one tag and would report
    a type disagreement wherever they differed — inventing heterogeneity out of adjacency.
    """
    text = "abcdefgh"
    m = masked(text, [pred(0, 4, "ID", text), pred(4, 8, "NAME", text)])
    assert masked_lines(m) == ["[ID][NAME]"]
    assert (m.counts["n_tags"], m.counts["n_heterogeneous_tags"],
            m.counts["n_overlapping_pairs"]) == (2, 0, 0)
    round_trip(m, text)


def test_a_span_nested_inside_another_is_one_tag():
    text = "." * 20
    m = masked(text, [pred(2, 14, "NAME", text), pred(5, 8, "NAME", text)])
    assert masked_lines(m) == [".." + "[NAME]" + "." * 6]
    assert m.counts["n_tags"] == 1
    round_trip(m, text)


# ─── the geometry `_check_tags` requires, and the descending emission order ──


def test_the_tags_are_ascending_and_non_overlapping_on_every_line():
    """The contract `audit._check_tags` enforces, asserted through construction.

    `MaskedLine.__post_init__` runs that check, so the masker building one at all is the
    assertion — this test states it in the direction a reader looks for it and covers the
    multi-tag, multi-line case where a descending emission would actually be visible.
    """
    text = "Ana y Beto\nel 3/4 con Caro\n"
    m = masked(text, [pred(0, 3, "NAME", text), pred(6, 10, "NAME", text),
                      pred(14, 17, "DATE", text), pred(22, 26, "NAME", text)])
    for line in m.lines:
        columns = [(col, col + length) for col, length, _, _ in line.tags]
        assert columns == sorted(columns)
        for (_, previous_end), (start, _) in zip(columns, columns[1:]):
            assert start >= previous_end
    round_trip(m, text)


def test_the_masker_emits_more_than_one_tag_per_line_so_the_order_is_testable():
    """A guard on the test above, not on the masker.

    Every ordering assertion in this file passes trivially on lines with one tag or none,
    and one tag per line is what most small fixtures produce. So the fixture that checks
    order has to be known to contain a line with two.
    """
    text = "Ana y Beto\nel 3/4 con Caro\n"
    m = masked(text, [pred(0, 3, "NAME", text), pred(6, 10, "NAME", text),
                      pred(14, 17, "DATE", text), pred(22, 26, "NAME", text)])
    assert max(len(line.tags) for line in m.lines) >= 2


def test_the_tags_are_within_their_line():
    """`_check_tags`'s "a tag past the end of its line" case, from the producing side."""
    text = "Ana\nBeto y Caro\n"
    m = masked(text, [pred(0, 3, "NAME", text), pred(11, 15, "NAME", text)])
    for line in m.lines:
        for col, length, _, _ in line.tags:
            assert 0 <= col and col + length <= line.length


def test_a_tag_carries_the_document_extent_it_replaced():
    """The map `audit._to_document` reads rather than reconstructs.

    Checked against the *document*, which is the one thing the geometry cannot be
    self-consistently wrong about: the extent is where the prediction was.
    """
    text = "Paciente Ana Perez, 40 anos"
    m = masked(text, [pred(9, 18, "NAME", text), pred(20, 22, "AGE", text)])
    extents = [(doc_start, doc_end)
               for line in m.lines for _, _, doc_start, doc_end in line.tags]
    assert extents == [(9, 18), (20, 22)]
    assert text[9:18] == "Ana Perez"


def test_a_prediction_spanning_a_newline_becomes_one_tag_on_one_line():
    """The tag has no newline in it, so the line count *changes* — and that is correct.

    `auditor.md` §1.3 says a flag does not cross a line boundary; a tag that swallowed a
    newline would make the line the tag sits on stand for two document lines, which is
    consistent and is what the coordinate scheme means. The document offsets are what has
    to stay right, and `round_trip` is what says they did.
    """
    text = "Ana\nPerez trabaja aqui"
    m = masked(text, [pred(0, 9, "NAME", text)])
    assert masked_lines(m) == ["[NAME] trabaja aqui"]
    assert m.lines[0].tags == ((0, 6, 0, 9),)
    round_trip(m, text)


@pytest.mark.parametrize("text,spans_at", [
    ("Ana vive", [(0, 3)]),                      # at the very start
    ("vive Ana", [(5, 8)]),                      # at the very end
    ("Ana", [(0, 3)]),                           # the whole document
    ("Ana\n", [(0, 3)]),                         # ending in a newline
    ("\nAna", [(1, 4)]),                         # starting with one
    ("a\n\nb", [(3, 4)]),                        # a blank line before the tag
    ("Ana y Ana", [(0, 3), (6, 9)]),             # two tags, one line
    ("Ana\nAna\nAna", [(0, 3), (4, 7), (8, 11)]),  # one per line
])
def test_the_map_holds_at_the_boundaries(text, spans_at):
    """The offsets where an off-by-one lives, each translated character by character."""
    round_trip(masked(text, [pred(a, b, "NAME", text) for a, b in spans_at]), text)


def test_an_empty_document_is_one_empty_line():
    """Zero is a measurement here too: a document with no characters is still a document.

    The alternative — no lines at all — would make `validate_flags()` raise `AuditError`
    for a caller bug (its "no masked lines" branch) on a document that was simply empty.
    """
    m = masked("", [])
    assert m.lines == (MaskedLine(length=0, doc_offset=0),)
    assert masked_lines(m) == [""]


def test_the_line_prefix_is_the_masked_offset_and_is_not_part_of_the_line():
    """`auditor.md` §1.3's rendering: the offset is in the **masked** text, and column 0 is
    the character after the separator.

    A prefix carrying the *document* offset is the plausible mistake and it would be
    invisible on a document with no tags — where the two agree, which is most fixtures.
    """
    text = "Ana Perez y Beto\nsegunda linea"
    m = masked(text, [pred(0, 9, "NAME", text)])
    lines = block_of(m).split("\n")
    assert lines[0].startswith("0".zfill(LINE_OFFSET_WIDTH) + LINE_SEPARATOR)
    # First line masked: "[NAME] y Beto" — 13 characters, so the second starts at 14,
    # while in the document it starts at 17. The prefix is the masked number.
    assert lines[1].startswith("14".zfill(LINE_OFFSET_WIDTH) + LINE_SEPARATOR)
    assert m.lines[1].doc_offset == 17
    assert m.lines[0].length == len("[NAME] y Beto")


# ─── what the masker refuses, and what it never reads ────────────────────────


def test_gold_is_never_read():
    """**The property the whole role rests on** (DESIGN §3): the Auditor cannot see gold.

    The document carries gold spans and the masker is given a different, smaller set of
    predictions. A masker reading `document.spans` would mask the gold — handing the agent
    the answer by masking exactly what it is being asked to find.
    """
    text = "Ana vive con Beto"
    document = Document(
        doc_id="dev1", corpus_id="es-meddocan", text=text, split="dev",
        spans=[Span(start=0, end=3, surface="Ana", subtype="X", phi_type="NAME"),
               Span(start=13, end=17, surface="Beto", subtype="X", phi_type="NAME")])
    m = mask_document(document, [pred(0, 3, "NAME", text)])
    assert masked_lines(m) == ["[NAME] vive con Beto"]
    assert m.counts["n_input_spans"] == 1


def test_a_prediction_past_the_end_of_the_document_is_refused():
    """Predictions and document from different folds — every offset after it would be wrong.

    The span is well-formed against a longer text, which is how this arrives: the loop
    driver reads `iter{n-1}/spans.jsonl` and pairs it with a document set. Refused rather
    than clipped, because a clipped tag stands for characters the prediction did not cover.
    """
    longer = "Ana Perez"
    with pytest.raises(PromptError, match="different folds"):
        masked("Ana", [pred(0, 9, "NAME", longer)])


def test_a_prediction_with_a_type_outside_the_axis_is_refused():
    """The tag is printed into the prompt, so its type is a `naming.yaml` value.

    `Span` refuses an unknown `phi_type` at construction, so the masker's own check is
    reached by a caller that built its spans some other way — reading `spans.jsonl` back,
    which is exactly what the loop driver does.
    """
    with pytest.raises(PromptError, match="phi_type axis"):
        masked("Ana vive", [row(0, 3, "NOMBRE")])


@pytest.mark.parametrize("start,end", [(3, 3), (5, 2), (-1, 3)])
def test_an_empty_inverted_or_negative_prediction_is_refused(start, end):
    """A masked extent stands for at least one character — `_check_tags`'s rule, upstream.

    Built as a bare object for the reason above: `Span` refuses these, and the caller that
    can produce one is the one deserialising rows.
    """
    with pytest.raises(PromptError, match="empty, inverted or negative"):
        masked("abcdefgh", [row(start, end, "NAME")])


def test_no_message_quotes_the_document():
    """CLAUDE.md, applied to the largest text in the project.

    Every refusal above names an index, an offset or a length. Asserted over the module's
    own source rather than by triggering each branch: the check is that no message *can*
    interpolate a slice, and a triggered-branch test only covers the branches someone
    remembered.
    """
    source = MODULE.read_text(encoding="utf-8")
    for forbidden in ("{text[", "{masked[", "{document.text", "text[:20]", "{raw!r}",
                      "{chunks", "{character"):
        assert forbidden not in source, forbidden


def test_the_counts_are_the_numbers_design_promised_to_report():
    """DESIGN §3 pre-registers the overlapping-pair count and the heterogeneous-union
    count, to be measured when the first `port-loop` arm runs.

    `counts` is the accessor a driver puts in `metrics.json`, so the keys are pinned: a
    renamed key is a number that silently stops being reported.
    """
    text = "." * 40
    m = masked(text, [pred(0, 10, "NAME", text), pred(5, 15, "DATE", text),
                      pred(20, 25, "AGE", text)])
    assert set(COUNT_KEYS) == set(m.counts)
    assert m.counts == {"n_input_spans": 3, "n_tags": 2, "n_heterogeneous_tags": 1,
                        "n_overlapping_pairs": 1}


def test_the_counts_come_from_the_block_and_not_from_a_second_field():
    """One storage site, so a log line and `metrics.json` cannot disagree.

    The reference form is what may be recorded (`FilledPrompt.reference()`), and `counts`
    reads from it — asserted rather than trusted, because the tidier-looking edit is to
    keep the counts as fields on `MaskedDocument` and hand `reference()` a copy.
    """
    text = "." * 20
    m = masked(text, [pred(0, 5, "NAME", text)])
    reference = m.block.reference()
    assert all(reference[key] == m.counts[key] for key in COUNT_KEYS)
    assert {f.name for f in dataclasses.fields(MaskedDocument)}.isdisjoint(COUNT_KEYS)


def test_the_reference_form_carries_no_text():
    """What may be recorded about the largest prompt in the project: counts and hashes.

    Every word of the document is long and invented, for `SURFACE`'s reason: a short word
    like `de` occurs inside a hex digest by coincidence, so an assertion of its absence
    would fail on a correct reference form and would have to be weakened until it measured
    nothing.
    """
    text = f"Zzyzxpaciente {SURFACE} Qxwvunosenta Vurblesmith"
    m = masked(text, [pred(14, 14 + len(SURFACE), "NAME", text)])
    reference = m.block.reference()
    body = json.dumps(reference)
    for word in [SURFACE, *text.split()]:
        assert word not in body, word
    assert reference["document_chars"] == len(text)
    assert reference["masked_chars"] == len(text) - len(SURFACE) + len("[NAME]")
    assert reference["n_tags"] == 1
    assert reference["tags_by_phi_type"] == {"NAME": 1}
    assert reference["text_sha256"].startswith("sha256:")


def test_the_block_is_hashed_against_the_window_files():
    """The Auditor's template is a window file since 2026-08-12 (DESIGN §5.5), so this
    block's record names the same three files every other prompt's does."""
    text = "Ana vive"
    reference = masked(text, [pred(0, 3, "NAME", text)]).block.reference()
    assert set(reference["window_files"]) == set(WINDOW_FILES)


def test_the_masked_text_is_not_reachable_from_the_masked_document():
    """No public accessor beyond the block, whose own exits are the two named ones."""
    text = "Ana vive"
    m = masked(text, [pred(0, 3, "NAME", text)])
    assert text not in str(m.block) and text not in repr(m.block)
    with pytest.raises(PromptError, match="not a terminal"):
        m.block.to_terminal(io.StringIO())


# ─── the Auditor's call: §§1.1–1.2 of `auditor.md` ───────────────────────────


def audited(text: str = "Ana vive en Cadiz", spans=None, corpus: str = "es-meddocan",
            doc_id: str = "dev1") -> FilledPrompt:
    """One Auditor call, from an invented document. No corpus is read here."""
    if spans is None:
        spans = [pred(0, 3, "NAME", text)]
    return assemble_audit_prompt(
        corpus=corpus, masked=masked(text, spans, doc_id=doc_id))


def test_the_audit_prompt_is_a_filled_prompt():
    """**The one assembler where the type is the point rather than the convention.**

    `auditor.md` §6 puts the masked dev fold at about 77× §1.4's window, and unlike the
    RuleAuthor's window a majority of the in-scope identifiers in it are *unmasked* —
    unmasked is what "leaked" means. A string return here would leave the largest corpus
    exposure in the project loose in every caller.
    """
    p = audited()
    assert isinstance(p, FilledPrompt)
    assert not isinstance(p, str)


def test_the_call_carries_the_auditor_template_verbatim():
    """§2's schema, §3's prohibition on quoting, §5's empty tool list.

    Forwarding §1 alone would ask for a JSON object whose schema the agent was never given,
    and `src/porting/audit.py` would then refuse the answer for a shape nobody sent.
    """
    template = (ROOT / "docs" / "prompts" / "auditor.md").read_text(encoding="utf-8")
    assert template in shown(audited())


def test_the_frame_names_every_canonical_type_with_its_own_gloss():
    """§1.1's first element, read from the config for `_task_frame`'s reason: a copy here
    would agree with a prompt that had drifted from the axis."""
    text = shown(audited())
    for name, gloss in axis("phi_type").items():
        assert name in text, f"the audit frame omits the {name} phi_type"
        assert gloss in text, f"the audit frame omits {name}'s gloss"


def test_the_frame_shows_every_mask_tag_and_not_a_sample_of_them():
    """§1.1's second element, and the reason it is every tag.

    A frame showing two examples goes on rendering correctly while the agent meets a third
    form it was never shown — and an unrecognised `[PROFESSION]` reads as text, which is
    exactly the shape of a residual identifier. Every canonical type, including the ones
    that may not be flagged: a tag for `OTHER` still appears in a masked document, because
    masking is what this arm's rules detected and not what the Auditor may report.
    """
    text = shown(audited())
    for name in axis("phi_type"):
        assert TAG_FORM.format(phi_type=name) in text, f"the frame omits {name}'s tag"


def test_the_frame_shows_the_heterogeneous_tag_beside_the_others():
    """§1.2's "a tag is not a candidate" has to cover `[PHI]` without a second clause.

    Read from the config rather than written out — `test_masked_tag.py` owns the value, and
    this asserts the frame carries whatever it is.
    """
    text = shown(audited())
    tag = masked_tag_heterogeneous()
    assert tag in text
    assert "not a candidate" in text


def test_the_frame_says_a_tag_marks_something_already_found():
    """§1.2's sentence, stated rather than implied.

    Without it the masked document reads as a list of candidates, and a flag on a tag
    reports a detection back to the detector that made it — the one flag category that
    cannot teach the RuleAuthor anything.
    """
    text = shown(audited())
    assert "already found" in text


def test_the_frame_says_the_residual_bucket_may_not_be_flagged():
    """§1.1's fourth element. Found through `non_target_types()` for
    `test_the_task_frame_says_the_residual_bucket_is_not_a_target`'s reason.

    Named with the refusal it would meet: `audit.py` rejects such a flag as
    `undeclared_phi_type`, so an agent that emits one has spent a call on a value the
    validator drops.
    """
    text = shown(audited())
    assert non_target_types(), "no non-target type to check — the fixture has drifted"
    for name in non_target_types():
        assert f"{name} may not be flagged" in text
    assert "undeclared_phi_type" in text


def test_the_frame_names_the_section_9_1_exclusions_with_their_reasons():
    """**§1.1's third element, and the one with a cost.**

    Named rather than omitted because inference from an absence goes the wrong way: ten
    types with no mention of sex is equally consistent with "out of scope" and with "the
    list is a summary". The reasons travel with the names because §9.1 excludes for two
    different reasons, and the guess an agent would make for `NAME_TITLE` is the wrong one.
    """
    text = shown(audited())
    for name, reason in excluded_types().items():
        assert name in text, f"the audit frame omits the §9.1 exclusion {name}"
        assert reason in text, f"the audit frame omits {name}'s reason"


def test_an_exclusion_is_not_presented_as_a_type_that_may_be_flagged():
    """The distinction the frame has to carry: `OTHER` is a type nobody may flag, the three
    exclusions are not types of this axis at all.

    Collapsing them into one list would be the plausible tidying — both are "do not flag" —
    and it would tell the agent that sex is a `phi_type`, which is the state DESIGN §9.1
    decided against.
    """
    text = shown(audited())
    assert "not types of this axis" in text
    for name in excluded_types():
        assert TAG_FORM.format(phi_type=name) not in text, (
            f"the frame shows {name} as a mask tag, which presents an §9.1 exclusion as a "
            "type of the axis"
        )


def test_the_frame_does_not_index_into_the_axis_for_its_examples():
    """Structural, because the defect is invisible on today's config.

    `sorted(...)[0]` and `[1]` render two plausible tags and raise `IndexError` on an axis
    with fewer than two flaggable types — a crash in the assembler, reached only by an
    edit to `naming.yaml`, which is the wrong place to discover it.
    """
    fn = functions(tree())["_audit_frame"]
    for node in ast.walk(fn):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and \
                isinstance(node.slice.value, int):
            raise AssertionError(
                "_audit_frame indexes a sorted axis by position. The frame shows every "
                "tag; a positional example is one an axis of one value turns into a crash."
            )


def test_the_masked_document_arrives_through_the_maskers_own_block():
    """§1.2, and DESIGN §3's division of labour.

    The masker is the function that slices the document and it has already done it. A second
    rendering here would be a second place the geometry — the prefixes, the line starts, the
    tag extents — is established by hand, and `validate_flags()` translates against the
    masker's `MaskedLine`s, not against this function's idea of them.
    """
    text = "Ana vive en Cadiz"
    m = masked(text, [pred(0, 3, "NAME", text)])
    assert m.block.for_transport() in shown(assemble_audit_prompt(
        corpus="es-meddocan", masked=m))


def test_the_coordinate_convention_is_stated_where_the_document_is():
    """§1.3: columns from 0 after the separator, half-open, and never across a line.

    Stated in the prompt rather than left to the template, because the block the agent is
    looking at is the one with the prefixes on it and the prefix is the thing most easily
    counted by mistake. `_to_document` translates as this paragraph describes, so a prompt
    that omitted it would produce flags whose arithmetic the code does not share.
    """
    text = shown(audited())
    assert "not part of the line" in text
    assert "(line, start, end)" in text
    assert "half-open" in text
    assert "does not cross a line" in text


def test_the_heading_counts_the_lines_and_the_tags_it_is_about():
    """The geometry the agent is counting against, stated as a number it can check.

    And singular where the number is one: "1 mask tags" is a small thing to a reader and a
    different thing to an agent being told the shape of what it is reading.
    """
    text = "Ana"
    one = shown(assemble_audit_prompt(
        corpus="es-meddocan", masked=masked(text, [pred(0, 3, "NAME", text)])))
    assert "1 line, 1 mask tag" in one
    two = "Ana vive\nBea vive"
    both = shown(assemble_audit_prompt(
        corpus="es-meddocan",
        masked=masked(two, [pred(0, 3, "NAME", two), pred(9, 12, "NAME", two)])))
    assert "2 lines, 2 mask tags" in both


def test_an_empty_flag_list_is_required_rather_than_permitted():
    """§2.1. A document the Auditor cleared is a measurement — it is a leak rate of zero on
    that document — and an agent that answers nothing has produced no measurement at all."""
    text = shown(audited())
    assert '{"flags": []}' in text
    assert "is a measurement" in text


def test_the_agent_is_told_not_to_describe_what_it_flags():
    """§3, restated in the input rather than left in the template.

    The template says it once, several hundred lines above the document; this is the
    instruction the flags are emitted against, and CLAUDE.md's rule is that the surface form
    does not travel — a paraphrase in a JSON field is the surface form travelling.
    """
    text = shown(audited())
    for word in ("quote", "transcribe", "paraphrase", "describe"):
        assert word in text, word


def test_a_document_from_another_corpus_is_refused():
    """The frame names one corpus and the block belongs to another.

    Not a cosmetic disagreement: every offset the agent returns is translated against the
    document that was sent while the report records the corpus that was not, so the audit
    report names a fold whose text it never saw.
    """
    text = "Ana vive"
    m = masked(text, [pred(0, 3, "NAME", text)])          # corpus_id es-meddocan
    with pytest.raises(PromptError, match="es-carmen"):
        assemble_audit_prompt(corpus="es-carmen", masked=m)


def test_a_corpus_outside_the_config_is_refused():
    with pytest.raises(PromptError, match="not a corpus"):
        audited(corpus="es-invented")


@pytest.mark.parametrize("bad", ["Ana vive", None, 42, [], {"lines": []}])
def test_a_masked_document_that_is_not_the_type_is_refused(bad):
    """A string here is masked corpus text outside the type that exists to hold it, and it
    would be assembled into a prompt with no geometry to translate a flag against."""
    with pytest.raises(PromptError, match="MaskedDocument"):
        assemble_audit_prompt(corpus="es-meddocan", masked=bad)


@pytest.mark.parametrize("name", ["metrics", "errors", "rules_path", "audit_report",
                                  "spans", "gold", "scores", "iteration"])
def test_the_signature_has_nowhere_to_put_the_withheld_inputs(name):
    """**§1.2's withheld table, enforced by the signature rather than by a check.**

    Gold, the score block, `rules/{lang}.yaml`, the previous round's report: each is withheld
    for its own reason, and the strongest form of the refusal is that no parameter accepts
    it. A keyword that raised would be a keyword a later edit can start honouring.
    """
    text = "Ana vive"
    m = masked(text, [pred(0, 3, "NAME", text)])
    with pytest.raises(TypeError, match="keyword argument"):
        assemble_audit_prompt(corpus="es-meddocan", masked=m, **{name: object()})


def test_the_reference_form_carries_the_document_id_and_no_text():
    """What may be recorded about the largest prompt in the project.

    The `doc_id` is the harness's — §1.3's third reason for one call per document is that it
    never has to come from the agent — so a call line can be matched to the document it
    audited without the response having named one.
    """
    text = f"Zzyzxpaciente {SURFACE} Qxwvunosenta Vurblesmith"
    p = assemble_audit_prompt(
        corpus="es-meddocan",
        masked=masked(text, [pred(14, 14 + len(SURFACE), "NAME", text)], doc_id="dev7"))
    ref = p.reference()
    body = json.dumps(ref)
    for word in [SURFACE, *text.split()]:
        assert word not in body, word
    assert ref["block"] == "audit"
    assert ref["doc_id"] == "dev7"
    assert ref["corpus"] == "es-meddocan"
    assert ref["text_sha256"].startswith("sha256:")


def test_the_reference_form_records_both_blocks_as_filled():
    """The Auditor's template has two §1 blocks and every call fills both.

    `sections_empty` is present and empty rather than absent, for `assemble_task_prompt`'s
    reason: a key some calls omit is one no reader can compare across calls.
    """
    ref = audited().reference()
    assert ref["sections_filled"] == list(AUDIT_SECTIONS)
    assert ref["sections_empty"] == []


def test_the_masked_reference_is_nested_and_not_merged():
    """Two `text_sha256` values under two names a reader has to tell apart.

    The block's hash is over what the masker rendered; the prompt's is over the whole call.
    Merged, the record would hold one under a name that means the other — the same defect
    `error_spans` avoids one assembler over.
    """
    ref = audited().reference()
    assert ref["masked_document"]["block"] == "masked_document"
    assert ref["masked_document"]["text_sha256"] != ref["text_sha256"]
    assert ref["masked_document"]["n_tags"] == 1


def test_the_prompt_is_hashed_against_the_window_files():
    """`auditor.md` is a window file (DESIGN §5.5), so this call's record names the same
    three files every other prompt's does — and the freeze record cannot agree with a
    rewritten Auditor."""
    ref = audited().reference()
    assert set(ref["window_files"]) == set(WINDOW_FILES)


def test_the_template_is_resolved_through_the_module_that_hashes_it():
    """One path resolution, for `_template()`'s reason.

    A second literal path is how a record comes to hash one file while the call was shown
    another — and this file is in `WINDOW_FILES` precisely so that cannot happen.
    """
    fn = functions(tree())["_auditor_template"]
    # The docstring names the file it sends, which is documentation and not a resolution;
    # the statements are what would hold a second path. `ast.get_docstring` is not used to
    # strip it, because a function whose body is only a docstring must not read as clean.
    statements = [n for n in fn.body
                  if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    assert statements, "_auditor_template has no body but a docstring"
    source = "\n".join(
        ast.get_source_segment(MODULE.read_text(encoding="utf-8"), n) for n in statements)
    assert "AUDITOR_TEMPLATE" in source
    assert "auditor.md" not in source, (
        "_auditor_template spells the template path itself. It is resolved through "
        "src.sample, which is the module that hashes it into the freeze record."
    )


def test_the_audit_assembler_returns_only_a_filled_prompt_call():
    """Structural, for `render_window`'s reason and with more at stake: this is the assembler
    that carries unmasked corpus text, so a bare-string return is the one edit that would
    put the whole masked document into a caller's local variable."""
    fn = functions(tree())["assemble_audit_prompt"]
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert returns, "assemble_audit_prompt has no return statement to check"
    for node in returns:
        value = node.value
        assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and \
            value.func.id == "FilledPrompt", (
            "assemble_audit_prompt returns something other than a FilledPrompt(...) "
            "construction."
        )


def test_the_assembler_does_not_mask_the_document_itself():
    """It takes a `MaskedDocument`; it does not build one.

    An assembler that called `mask_document()` would be one a caller could hand a raw
    `Document` to, and the masking would then happen inside the call whose prompt is hashed
    — with the arm's predictions arriving from wherever the assembler chose to look for
    them, rather than from the round that produced them.
    """
    calls = body_calls(functions(tree())["assemble_audit_prompt"])
    assert "mask_document" not in calls


# ─── the cache boundary: `auditor.md` §6's third bullet, pinned to the code ──
#
# The bullet is a claim about which bytes a third party retains for five minutes, and it is
# only a *property* while the code splits where it says. These tests are the join between the
# two. If they are deleted, the bullet becomes a sentence — which is the state DESIGN §5.4
# calls a written warning rather than a control.


AUDITOR_MD = ROOT / "docs" / "prompts" / "auditor.md"


def audit_bullet() -> str:
    """`auditor.md` §6's bullet about the boundary, located by its subject and not by index.

    By subject because "the third bullet" is a position: a bullet inserted above it would
    leave this reading a different claim while every assertion still passed. The subject is
    the word `cachePoint`'s prose form — the one bullet in §6 that is about caching at all —
    and a §6 with no such bullet, or with two, fails here rather than being guessed at.

    Whitespace is collapsed because the file is hard-wrapped at 90 columns: a phrase this test
    looks for can be split across a line break by a reflow that changed no words, and a test
    that failed on a reflow would be one the next person deletes.
    """
    section = AUDITOR_MD.read_text(encoding="utf-8").split("## 6.", 1)
    assert len(section) == 2, "docs/prompts/auditor.md has no §6"
    bullets = [b for b in section[1].split("\n- ")[1:] if "cached" in b]
    assert len(bullets) == 1, (
        f"§6 has {len(bullets)} bullets about caching and this test pins exactly one. Two "
        "bullets making the claim would be two places it could be edited apart."
    )
    return " ".join(bullets[0].split())


def split_audit(text: str = "Ana vive en Cadiz", spans=None) -> tuple[str, str, dict]:
    """One audit prompt as the two blocks the transport sends, plus its reference form.

    Split through `for_transport_blocks()` at the offset the assembler recorded — the same
    two values the transport uses — so that what is asserted here is the split that ships and
    not a second one computed by the test.
    """
    p = audited(text, spans) if spans is not None else audited(text)
    ref = p.reference()
    blocks = p.for_transport_blocks(
        cache_after=ref["cache_after"], boundary=ref["cache_boundary"], ttl=CACHE_TTL,
    ).blocks()
    assert [sorted(b) for b in blocks] == [["text"], ["cachePoint"], ["text"]]
    return blocks[0]["text"], blocks[2]["text"], ref


def test_the_prompt_declares_where_it_is_split_and_names_the_boundary():
    """The offset and the boundary's name are on the reference form of every audit prompt.

    On *every* one, whether or not the transport caches: where §1.1 ends is a fact about this
    text, true independently of the call. Whether caching happened is `metrics.json`'s
    `caching` block, and its absence there is what records "unused" (DESIGN §5.4).
    """
    ref = audited().reference()
    assert ref["cache_boundary"] == CACHE_BOUNDARY == "after_audit_frame"
    assert isinstance(ref["cache_after"], int)
    assert 0 < ref["cache_after"] < ref["text_chars"]


def test_the_cached_side_is_the_three_things_the_bullet_names():
    """§6's bullet says the cached block is the template, the input banner and §1.1's frame.

    Asserted as *contents of the cached block* rather than as an offset arithmetic, because
    the offset is meaningless on its own: what the bullet promises a reader is which bytes
    Bedrock holds, and this is that list read back out of the block that goes to Bedrock.
    """
    bullet = audit_bullet()
    for phrase in ("the template above", "the input banner", "§1.1's frame"):
        assert phrase in bullet, f"§6's caching bullet no longer names {phrase!r}"
    cached, _, _ = split_audit()
    assert AUDITOR_MD.read_text(encoding="utf-8") in cached
    assert INPUT_BANNER in cached
    # §1.1's frame, by a sentence `_audit_frame` assembles and the template does not carry.
    assert "it marks something already found" in cached


def test_the_masked_document_is_on_the_far_side_and_is_never_cached():
    """The sentence the whole boundary exists to keep true, as a check over the bytes.

    "**The masked document is on the far side of the boundary and is never in the cached
    block.**" — every character of the masked block, and the invented surface inside it, is
    absent from the cached side and present in the tail. This is the assertion that fails if
    the boundary is moved one join along, which is the failure the bullet's last sentence
    names ("if the boundary ever moved past the document heading, this bullet is what it
    would contradict").
    """
    assert "far side" in audit_bullet()
    text = f"Zzyzxpaciente {SURFACE} Qxwvunosenta"
    m = masked(text, [pred(14, 14 + len(SURFACE), "NAME", text)])
    p = assemble_audit_prompt(corpus="es-meddocan", masked=m)
    ref = p.reference()
    blocks = p.for_transport_blocks(
        cache_after=ref["cache_after"], boundary=ref["cache_boundary"], ttl=CACHE_TTL,
    ).blocks()
    cached, tail = blocks[0]["text"], blocks[2]["text"]
    document = m.block.for_transport()
    assert document in tail
    assert document not in cached
    for word in [SURFACE, *text.split()]:
        assert word not in cached, word
    # The heading too — the bullet's failure mode is the boundary sliding *past* it. The
    # *filled* heading, with its counts: `### 1.2` alone appears in the committed template,
    # which is on the cached side by design, so a bare-heading assertion would fail on the
    # correct split and prove nothing about the wrong one.
    heading = f"### {MASKED_DOCUMENT} The masked document — 1 line,"
    assert heading in tail
    assert heading not in cached


def test_the_boundary_is_the_end_of_the_frame_and_not_one_join_short_or_long():
    """The cached side ends exactly where §1.1 ends, to the character.

    A boundary right about the blocks and wrong by the two separator characters is a cache
    that never hits — the cached bytes would differ from the previous call's, so every call
    pays a write and the arm's cost record would show caching enabled and no reads. So the
    check is equality against the joined prefix, not a containment.
    """
    cached, tail, _ = split_audit()
    assert cached.endswith("\n\n"), (
        "the cached block does not end with the join that separated it from §1.2. The "
        "boundary's own two characters belong to the cached side; on the far side they make "
        "`cache_after` name an offset one join short of the frame's end."
    )
    assert not cached.endswith("\n\n\n\n")
    assert not tail.startswith("\n")
    assert tail.startswith("### ")


def test_the_split_is_the_same_bytes_as_the_unsplit_call():
    """§4's byte-identical claim: caching is a framing of one request, not a second prompt.

    The two blocks concatenated are `for_transport()`, character for character. Without this
    the cached arm and the uncached arm would be running different prompts under one
    `window_freeze.json` hash (DESIGN §6.3) — and the comparison DESIGN §11.3 draws between
    their cost records would be between two experiments.
    """
    p = audited()
    ref = p.reference()
    cached, tail = (b["text"] for b in p.for_transport_blocks(
        cache_after=ref["cache_after"], boundary=ref["cache_boundary"], ttl=CACHE_TTL,
    ).blocks() if "text" in b)
    assert cached + tail == p.for_transport()
    assert len(cached) + len(tail) == ref["text_chars"]


def test_the_boundary_offset_is_not_found_by_searching_the_finished_text():
    """Structural: the assembler computes the offset from the pieces it joined.

    A `text.index("### 1.2")` would pass every assertion above on the day it was written and
    go on succeeding one block further along if a heading were reworded — the failure mode
    being that §1.2 slides onto the cached side while every check still passes. So the search
    forms are refused in the source rather than argued about in a comment.
    """
    source = MODULE.read_text(encoding="utf-8")
    fn = functions(tree())["assemble_audit_prompt"]
    segment = "\n".join(
        ast.get_source_segment(source, n) or "" for n in fn.body)
    for form in (".index(", ".find(", ".split(", "search(", "partition("):
        assert form not in segment, (
            f"assemble_audit_prompt calls {form} — the boundary must come from the pieces "
            "as joined, not from a search of the finished prompt."
        )


def test_the_type_refuses_a_boundary_the_vocabulary_does_not_declare():
    """`config/naming.yaml` is where a boundary moves, and nowhere else.

    A caller free to invent a name is a caller that can put the masked document on the cached
    side and record a word for it that no vocabulary refused — at which point §6's bullet is
    contradicted by a call whose own record says it was not.
    """
    p = audited()
    ref = p.reference()
    with pytest.raises(CorpusError, match="not a cache boundary"):
        p.for_transport_blocks(cache_after=ref["cache_after"],
                               boundary="after_masked_document", ttl=CACHE_TTL)
    with pytest.raises(CorpusError, match="not a cache TTL"):
        p.for_transport_blocks(cache_after=ref["cache_after"],
                               boundary=CACHE_BOUNDARY, ttl="1h")


def test_no_declared_boundary_puts_the_masked_document_on_the_cached_side():
    """The vocabulary itself, not a call: **there is one boundary and it is before §1.2.**

    This is the check that survives a rewrite of the transport. `caching_boundaries()` is a
    singleton by decision (`src/corpora/base.py`), so a value admitting a later split does
    not exist — and adding one is an edit to a committed file next to a gloss that says the
    masked document is on the far side.
    """
    boundaries = caching_boundaries()
    assert set(boundaries) == {"after_audit_frame"}, (
        f"config/naming.yaml declares cache boundaries {sorted(boundaries)}. A second value "
        "is admissible only after docs/prompts/auditor.md §6's third bullet is rewritten, "
        "and this test is where that shows up."
    )


@pytest.mark.parametrize("offset", [0, -1])
def test_an_offset_at_or_before_the_start_is_refused(offset):
    """`cachePoint` sits between two non-empty blocks, and an empty cached side is not one."""
    p = audited()
    with pytest.raises(PromptError, match="cache_after"):
        p.for_transport_blocks(cache_after=offset, boundary=CACHE_BOUNDARY, ttl=CACHE_TTL)


def test_an_offset_at_the_end_would_cache_the_masked_document_and_is_refused():
    """The one arithmetic mistake that is also the §6 violation.

    An offset at the end caches the whole prompt, and on an audit call the whole prompt is
    the masked document. Refused at the exit rather than trusted to the caller: the caller
    that would make this mistake is the caller that computed the offset wrongly.
    """
    p = audited()
    whole = p.reference()["text_chars"]
    for offset in (whole, whole + 1):
        with pytest.raises(PromptError, match="cache_after"):
            p.for_transport_blocks(cache_after=offset, boundary=CACHE_BOUNDARY,
                                   ttl=CACHE_TTL)


def test_the_blocks_carry_no_text_into_a_record_or_a_repr():
    """`CacheBlocks` holds prompt text, so it is under `FilledPrompt`'s discipline too.

    A dataclass or a `NamedTuple` here would generate a `__repr__` rendering both blocks —
    the largest corpus exposure in the project, reachable from any traceback that has this
    object in a local (`auditor.md` §6, CLAUDE.md on exception text).
    """
    text = f"Zzyzxpaciente {SURFACE} Qxwvunosenta"
    m = masked(text, [pred(14, 14 + len(SURFACE), "NAME", text)])
    p = assemble_audit_prompt(corpus="es-meddocan", masked=m)
    ref = p.reference()
    blocks = p.for_transport_blocks(
        cache_after=ref["cache_after"], boundary=ref["cache_boundary"], ttl=CACHE_TTL)
    assert not dataclasses.is_dataclass(blocks)
    for rendering in (str(blocks), repr(blocks), f"{blocks}"):
        assert SURFACE not in rendering
        assert "Zzyzxpaciente" not in rendering
    record = blocks.reference()
    assert json.dumps(record)
    assert SURFACE not in json.dumps(record)
    assert record["boundary"] == CACHE_BOUNDARY
    assert record["ttl"] == CACHE_TTL
    assert record["cached_chars"] + record["tail_chars"] == ref["text_chars"]
    assert not hasattr(blocks, "text")
    with pytest.raises(AttributeError):
        blocks.stashed = "a copy"       # __slots__ keeps the attribute set closed


def test_the_blocks_are_built_only_by_the_exit():
    """`CacheBlocks` refuses an empty side, whichever side it is.

    Constructed directly here — the one place that is done — because the refusal has to hold
    for a caller that bypassed `for_transport_blocks()`, and a type whose invariant lives in
    its caller has no invariant.
    """
    with pytest.raises(PromptError, match="empty block"):
        CacheBlocks("frame", "", boundary=CACHE_BOUNDARY, ttl=CACHE_TTL)
    with pytest.raises(PromptError, match="empty block"):
        CacheBlocks("", "document", boundary=CACHE_BOUNDARY, ttl=CACHE_TTL)
    with pytest.raises(PromptError, match="text on both sides"):
        CacheBlocks("frame", None, boundary=CACHE_BOUNDARY, ttl=CACHE_TTL)


# ─── structure: the module writes nothing, and that includes the renderer ────


def tree() -> ast.Module:
    return ast.parse(MODULE.read_text(encoding="utf-8"), filename=str(MODULE))


def functions(module: ast.Module) -> dict[str, ast.FunctionDef]:
    """Every function and method in the module, by name.

    Methods included, and that is the point of walking rather than reading `module.body`:
    a write added to a method would sit outside a check that only saw module-level
    functions.
    """
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


def body_calls(fn: ast.FunctionDef) -> set[str]:
    found: set[str] = set()
    for stmt in fn.body:
        found |= calls_named(stmt)
    return found


def test_no_function_in_the_module_writes_to_a_file():
    """Every function, not only the renderer.

    A private helper doing the write would satisfy a check aimed at the public function,
    and a structural check that silently matches nothing is the defect class
    `tests/test_conftest.py` was written for.
    """
    for name, fn in sorted(functions(tree()).items()):
        stray = body_calls(fn) & WRITE_NAMES
        if name == TERMINAL_EXIT:
            stray -= {"write"}          # its whole purpose, to a stream it has checked
        assert not stray, (
            f"src/llm/prompt.py::{name} calls {sorted(stray)}. A filled prompt is not "
            "written to disk at all (docs/prompts/rule_author.md §6) — not a debug copy, "
            "not a cache. The reference form is what may be recorded."
        )


def test_no_function_in_the_module_logs():
    for name, fn in sorted(functions(tree()).items()):
        stray = body_calls(fn) & LOG_NAMES
        assert not stray, (
            f"src/llm/prompt.py::{name} calls {sorted(stray)}. A log line reaches a "
            "terminal, a CI log and an issue, and release_screen.py reaches none of them "
            "(CLAUDE.md)."
        )


def test_the_module_never_prints():
    """`print` writes to whatever stdout happens to be, which may be a file.

    `to_terminal` is the distinction: it checks the destination first. A bare `print`
    anywhere in this module would be the same text with the check skipped.
    """
    for name, fn in sorted(functions(tree()).items()):
        assert "print" not in body_calls(fn), (
            f"src/llm/prompt.py::{name} calls print. stdout may be a file; the checked "
            "exit is `to_terminal`."
        )


def test_the_module_imports_nothing_that_writes():
    """No `logging`, no `json` at module scope: the write cannot be reached at all.

    `hashlib` and the typing names are what this module needs. Asserted as a closed set so
    that adding an import is a decision recorded here rather than a line nobody reviewed.
    """
    imported = set()
    for node in ast.walk(tree()):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert not (imported & {"logging", "json", "pickle", "shutil", "tempfile", "os"}), (
        f"src/llm/prompt.py imports {sorted(imported)}. A module that cannot reach a "
        "writer cannot be edited into one by accident."
    )


def test_the_renderer_returns_only_a_filled_prompt_call():
    """Structural, because a `str` return is behaviourally fine everywhere it is wrong.

    `render_window` handing back a bare string would satisfy every content assertion above
    if they were rewritten against it, and the type would then be an optional wrapper a
    caller could skip. So the return statements themselves are checked: each one either
    raises or constructs a `FilledPrompt`.
    """
    fn = functions(tree())["render_window"]
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert returns, "render_window has no return statement to check"
    for node in returns:
        value = node.value
        assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and \
            value.func.id == "FilledPrompt", (
            "render_window returns something other than a FilledPrompt(...) construction. "
            "A bare string return would leave the text loose in every caller."
        )


def test_the_type_has_no_accessor_beyond_the_named_exits():
    """The public surface equals `prompt_module.EXITS` — the enumeration, not its size.

    A `text` property added "for tests" is the plausible edit, and it would reopen every
    path this file closes.

    **Asserted against the declared set and not against a count** (DESIGN §5.4, restated
    2026-08-16). The earlier form of this test compared the method set to a literal of three
    names, which reads as a cardinality check and was cited as one. The difference matters in
    both directions: a count refuses a fourth exit of the same kind as an existing one — the
    safe change — while saying nothing about a dangerous exit that arrives as an edit to a
    method already inside the count. Comparing against `EXITS` makes adding an exit a change
    to a named list in the module that owns the type, which is where the admissibility
    criterion is written down.
    """
    cls = next(n for n in ast.walk(tree())
               if isinstance(n, ast.ClassDef) and n.name == "FilledPrompt")
    public = {n.name for n in cls.body
              if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}
    assert public == set(prompt_module.EXITS), (
        f"FilledPrompt's public methods are {sorted(public)}, and prompt_module.EXITS enumerates "
        f"{sorted(prompt_module.EXITS)}. Every text-bearing exit is named for a destination and "
        "listed in EXITS; a method outside that list is a path out of the type that nothing "
        "declared."
    )


def test_every_enumerated_exit_exists_on_the_type():
    """The other direction: `EXITS` may not name a method that is not there.

    Without this, the check above is satisfied by deleting an exit and its entry together —
    and by an `EXITS` that drifts into being a wish list. The enumeration is only a guarantee
    if it is exact in both directions.
    """
    for name in prompt_module.EXITS:
        assert callable(getattr(prompt_module.FilledPrompt, name, None)), (
            f"prompt_module.EXITS names {name!r}, which FilledPrompt does not define. An "
            "enumeration that overstates the surface cannot be used to check it."
        )


def test_the_enumeration_is_declared_in_the_module_that_owns_the_type():
    """`EXITS` lives beside `FilledPrompt`, not in this test file.

    A list of exits maintained in the tests is a list the module can be edited without
    touching — which is the arrangement DESIGN §5.4 calls a written warning rather than a
    control.
    """
    module_tree = tree()
    assigned = {target.id for node in module_tree.body if isinstance(node, ast.Assign)
                for target in node.targets if isinstance(target, ast.Name)}
    assert "EXITS" in assigned, (
        "src/llm/prompt.py does not define EXITS at module level. The enumeration is the "
        "guarantee (DESIGN §5.4) and it belongs with the type it describes."
    )


def test_the_structural_checks_would_catch_the_defective_form():
    """The checks must be capable of failing, on the exact edit they are written against.

    Without this they are consistent with `body_calls` returning nothing at all — a check
    that silently matches nothing, which is the failure this file's own reasoning rejects.
    Parsed from a string; nothing on disk changes.
    """
    defective = ast.parse(
        "def render_window(sample, docs, n):\n"
        "    text = build(sample, docs, n)\n"
        "    open('/tmp/last_prompt.txt', 'w').write(text)   # 'just for debugging'\n"
        "    return text\n"
    )
    fn = functions(defective)["render_window"]
    assert body_calls(fn) & WRITE_NAMES == {"open", "write"}, (
        "the write check did not see a write it was pointed at"
    )
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert not any(isinstance(r.value, ast.Call) for r in returns), (
        "the return check did not see a bare-string return"
    )


# ─── the renderer is the only one ────────────────────────────────────────────


def test_no_other_module_slices_document_text_for_a_prompt():
    """One renderer, so the convention has one place to hold.

    `render_for_author()` used to do this in `src/porting/human_arm.py` and
    `tools/show_human_window.py` printed the string it returned. Two renderers is two
    places the non-recording discipline is established by hand, which is what a type was
    supposed to stop — and `rule_author.md`'s banner makes the arm comparison interpretable
    only if both arms are shown the same blocks from the same code path.

    `src/corpora/base.py` is exempt: it slices to *validate* a span against its recorded
    surface, which is the loader's own integrity check and reaches no prompt.
    """
    exempt = {ROOT / "src" / "corpora" / "base.py", MODULE}
    offenders = []
    for path in sorted((ROOT / "src").rglob("*.py")) + \
            sorted((ROOT / "tools").glob("*.py")):
        if path in exempt:
            continue
        src = path.read_text(encoding="utf-8")
        for form in (".text[", "text[left", "text[span.start"):
            if form in src:
                offenders.append(f"{path.relative_to(ROOT)} ({form})")
    assert not offenders, (
        f"{offenders} slice document text outside src/llm/prompt.py. Rendering a window "
        "happens in one place (rule_author.md §6, and the prompt is hashed into "
        "window_freeze.json — two implementations under one hash means the record attests "
        "to a specification rather than to what ran)."
    )


def test_the_retired_arm_no_longer_carries_its_own_renderer():
    """The merge, asserted rather than assumed.

    A wrapper left behind in `human_arm.py` would be the second implementation the merge
    removed, and it would pass the slicing check above by delegating.
    """
    src = (ROOT / "src" / "porting" / "human_arm.py").read_text(encoding="utf-8")
    assert "def render_for_author" not in src
    assert "render_window" not in functions(
        ast.parse(src)).keys(), "human_arm defines its own render_window"


def test_the_window_tool_hands_the_prompt_straight_to_the_terminal():
    """No local name holds the text, and nothing prints it.

    A variable holding the rendered string is a value a later edit can log or write, which
    is the whole reason the exit is named for its destination.
    """
    tool = ROOT / "tools" / "show_human_window.py"
    fn = functions(ast.parse(tool.read_text(encoding="utf-8")))["main"]
    calls = body_calls(fn)
    assert "to_terminal" in calls, (
        "show_human_window.py does not use the checked exit"
    )
    assert "render_for_author" not in calls
    # The rendered value is never bound to a name in this function.
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fname = node.value.func
            name = fname.attr if isinstance(fname, ast.Attribute) else \
                getattr(fname, "id", "")
            assert name != "render_window", (
                "show_human_window.py binds the rendered window to a name. A named value "
                "is one a later edit can print, log or write."
            )


# --------------------------------------------------------------------------------------
# The fence convention: a prompt that forbids fences contains none.
#
# `port-oneshot`'s first run wrapped its YAML in a ```yaml fence and `load_rules` refused at
# line 1 column 1. `d44bd14` fixed the format instruction in `rule_author.md` §2 and added one
# sentence saying the fence was that document's quoting convention. The four prompts written
# afterwards inherited the sentence **together with the fenced example it was meant to
# neutralise**, and fourteen days later `port-multi`'s first run wrapped its JSON in a ```json
# fence and `parse_object` refused at position 0 (`docs/notes/arm-port-multi-es.md`).
#
# So the fix this time is not wording. The templates carry no demonstration at all, the
# instances live in `docs/prompts/examples/` where no call can read them, and these tests are
# what keeps the sixth prompt from inheriting the defect the way the second through fifth did.
# --------------------------------------------------------------------------------------


def test_no_prompt_template_contains_a_fence_line():
    """Zero fence lines under `docs/prompts/`, with no per-file exemption.

    **The exemption is the defect.** What went wrong was not that a fence is hard to notice;
    it is that the fix applied to one file and the convention stayed. A checker with a skip
    list would have let `rule_author.md` pass while `profiler.md` shipped the same block, which
    is the state the repository was actually in for fourteen days.

    Every offender is named at once rather than the first — a failure that reports one file
    invites a one-file fix, which is the mistake this test exists to prevent.
    """
    found = prompt_module.fenced_prompt_lines()
    assert found == {}, (
        "fenced block(s) in prompt template(s): "
        + "; ".join(f"{name} at line(s) {lines}" for name, lines in sorted(found.items()))
        + ". The template is sent to the model verbatim, so a fence in it is a demonstration "
        "the response can copy — twice now. Move the instance to docs/prompts/examples/ and "
        "describe the schema in prose (profiler.md §2.4)."
    )


def test_the_fence_check_reports_a_fence_when_there_is_one():
    """The positive control, in a temporary tree: a checker that found nothing may find nothing.

    `test_no_prompt_template_contains_a_fence_line` passes both when the templates are clean
    and when `fenced_prompt_lines` has been reduced to returning an empty mapping. Only this
    test separates the two, which is why the function takes a root at all — the alternative is
    putting a fence in the repository to see whether it is noticed.
    """
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        directory = root / prompt_module.PROMPT_DIR
        directory.mkdir(parents=True)
        (directory / "clean.md").write_text(
            "No code fence, no triple-backtick line. Inline `x` is fine.\n", encoding="utf-8")
        fence = "`" * 3
        (directory / "fenced.md").write_text(
            f"before\n{fence}json\n{{}}\n{fence}\nafter\n", encoding="utf-8")
        found = prompt_module.fenced_prompt_lines(root)
    relative = str(Path(prompt_module.PROMPT_DIR) / "fenced.md")
    assert found == {relative: [2, 4]}, found


def test_the_fence_check_covers_every_prompt_file_including_nested_ones():
    """The selection is the directory, so exempting a file shortens it and this fails.

    Two claims, and the second is the one a mutation attacks. That every real template is in
    the list — so a skip list for one file is visible here rather than in a green suite. And
    that a markdown file in a subdirectory is reached, so filing a template one level down is
    not a way out.
    """
    covered = prompt_module.prompt_markdown_files()
    on_disk = sorted(
        str(path.relative_to(ROOT))
        for path in (ROOT / prompt_module.PROMPT_DIR).rglob("*.md"))
    assert covered == on_disk, (
        "the fence check does not cover every markdown file under docs/prompts/. Missing: "
        f"{sorted(set(on_disk) - set(covered))}. A per-file exemption is how this defect was "
        "inherited once already."
    )
    assert len(covered) >= 5, covered
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        nested = root / prompt_module.PROMPT_DIR / "sub"
        nested.mkdir(parents=True)
        (nested / "deep.md").write_text("no fence here\n", encoding="utf-8")
        assert prompt_module.prompt_markdown_files(root) == [
            str(Path(prompt_module.PROMPT_DIR) / "sub" / "deep.md")]


def test_every_prompt_that_forbids_a_fence_says_so_in_the_same_words():
    """The prohibition is present in all five, and `lexicon_builder.md` is why this exists.

    That file forbade the fence and — alone among the five — never carried even the mitigating
    sentence, because the sentence was copied by hand from file to file and one copy was
    missed. A prohibition propagated by copying is a prohibition with a hole in it, so its
    presence is asserted rather than assumed.

    Matched on "triple-backtick" rather than on the delimiter itself: the templates spell it
    out precisely so that no line of a template can begin with one.
    """
    missing = []
    for relative in prompt_module.prompt_markdown_files():
        if Path(relative).name == "README.md":
            continue
        text = (ROOT / relative).read_text(encoding="utf-8")
        if "no triple-backtick line" not in text:
            missing.append(relative)
    assert missing == [], (
        f"prompt template(s) without the fence prohibition: {missing}. Every template that "
        "asks for a structured response carries it in the same words."
    )


# --------------------------------------------------------------------------------------
# The example files: generated, validated, and outside the frozen window.
#
# The templates are sent to the model verbatim, so an example inside a template is a
# demonstration the model can copy — and it did, twice (`docs/notes/arm-port-oneshot-es.md`,
# `docs/notes/arm-port-multi-es.md` §4). So the instances live in `docs/prompts/examples/`,
# where no call can read them, and the prompts name the path instead. These two tests are what
# make that a real substitution rather than a move: an example nobody validates goes stale
# silently, and an example file inside `WINDOW_FILES` would put a file no call reads into the
# record of what decided a run.
# --------------------------------------------------------------------------------------


def test_the_prompt_examples_are_current_and_pass_their_own_validators():
    """`docs/prompts/examples/` is generated, so a schema change cannot leave it behind.

    The examples are the only demonstration of these three schemas that exists anywhere now.
    A stale one is worse than none: a reader would trust it, and the prompt names its
    directory. `tools/build_prompt_examples.py` validates every object it writes, so this
    test's `--check` covers both staleness and validity.
    """
    spec = importlib.util.spec_from_file_location(
        "build_prompt_examples", ROOT / "tools" / "build_prompt_examples.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = module.expected_files()
    # Asserted before the loop, because a loop over a mapping that lost an entry passes. The
    # entry most likely to be lost is the YAML one — it is the only non-JSON file here and the
    # only one a JSON-shaped rewrite of `expected_files()` would drop.
    assert module.RULES_FILE in expected, sorted(expected)
    assert len(expected) >= 9, sorted(expected)
    for name, content in expected.items():
        path = module.EXAMPLES / name
        assert path.exists(), f"{name} is missing; run tools/build_prompt_examples.py"
        assert path.read_text(encoding="utf-8") == content, (
            f"{name} is stale. Edit tools/build_prompt_examples.py and re-run it; the file is "
            "generated so that an example cannot document a schema that no longer exists."
        )


def test_the_example_directory_is_not_in_the_frozen_window():
    """The window is what decided a run, and no call can read these files.

    Stated as a test because the argument runs the other way at first glance: the prompts name
    the directory, and a file a prompt names looks like a file that decided the run. It is not
    — naming a path transmits no bytes, and hashing a file the model never saw would grow every
    freeze record with an attestation to nothing (DESIGN §6.3, profiler.md §2.4).
    """
    for name in WINDOW_FILES:
        assert "examples" not in name, name
    assert len(WINDOW_FILES) == 6, WINDOW_FILES
