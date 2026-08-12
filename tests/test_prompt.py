"""The non-recording convention: a filled prompt is never written down.

`docs/prompts/rule_author.md` §6 fixes the rule and `src/llm/prompt.py` implements it as a
type. This file checks the two halves that a type cannot enforce on its own.

**Behaviour**, for the type's own guarantees: the text is not reachable except through the
two exits, `to_terminal()` refuses a stream that is not a terminal, and `reference()`
carries references and hashes and no text.

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
    Document, Span, axis, masked_tag_heterogeneous,
)
from src.llm import prompt as prompt_module                      # noqa: E402
from src.llm.prompt import (                                     # noqa: E402
    COUNT_KEYS, EMPTY_SECTIONS, FILLED_SECTIONS, LINE_OFFSET_WIDTH, LINE_SEPARATOR,
    FilledPrompt, MaskedDocument, PromptError, assemble_task_prompt, mask_document,
    render_window,
)
from src.porting.audit import MaskedLine, validate_flags         # noqa: E402
from src.rules import rule_layers                                # noqa: E402
from src.sample import (                                         # noqa: E402
    MISSED, WINDOW_FILES, ErrorSpan, non_target_types,
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
    """The two prompt templates and the sampling config, hashed.

    All of them, for `src/sample.py`'s reason: a record naming only the RuleAuthor template
    would agree with a doubled `n` as readily as with 40, and — since 2026-08-12 — with a
    rewritten Auditor as readily as with the frozen one.

    Read off `WINDOW_FILES` rather than listed, deliberately. This assertion is about the
    reference form carrying *the whole window*, not about which files are in it; the window's
    membership is DESIGN §5.5's and is pinned by `tests/test_sample.py`. A literal list here
    would be a second copy that has to be edited on the same day.
    """
    files = a_prompt().reference()["window_files"]
    assert set(files) == set(WINDOW_FILES)
    assert len(files) == 3
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

    The masked dev fold is about 40× §1.4's window and most of the identifiers in it are
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
    """The public surface is closed, as a property of the class rather than of a docstring.

    A `text` property added "for tests" is the plausible edit, and it would reopen every
    path this file closes.
    """
    cls = next(n for n in ast.walk(tree())
               if isinstance(n, ast.ClassDef) and n.name == "FilledPrompt")
    public = {n.name for n in cls.body
              if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}
    assert public == {"to_terminal", "for_transport", "reference"}, (
        f"FilledPrompt's public methods are {sorted(public)}. Exactly three: two exits "
        "named for where the text goes, and the reference form that may be recorded."
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
