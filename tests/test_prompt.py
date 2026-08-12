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
import io
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora.base import Document, Span, axis                # noqa: E402
from src.llm import prompt as prompt_module                      # noqa: E402
from src.llm.prompt import (                                     # noqa: E402
    EMPTY_SECTIONS, FILLED_SECTIONS, FilledPrompt, PromptError, assemble_task_prompt,
    render_window,
)
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
