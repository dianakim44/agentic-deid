"""Prompt assembly, and the convention that a filled prompt is never written down.

**The rule.** Only the template `docs/prompts/rule_author.md` is committed. A filled
instance is not committed, not logged, and not written to disk at all (§6 of that file).
The reason is §1.4: the error-span block carries ±120 characters of dev text around every
span in the sample, and that text is the corpus rather than a description of it. The
template is publishable because it says "±120 characters of dev text around the span"
where an instance says the characters.

**Why a type rather than a rule.** A rule of this shape — "remember not to persist this
string" — is enforced at every call site by whoever wrote it, and `tests/test_conftest.py`
records what that costs here: the availability defect shipped four times, three of them
after it was written up, because a written warning is not a control. So the filled prompt
is not a `str`. `FilledPrompt` has no public text attribute and exactly two exits, both
named for where they go: `to_terminal()` for a person reading a screen and
`for_transport()` for the API call. `json.dumps`, `open(...).write`, `print` and f-string
interpolation all reach the reference form instead, because that is what `__str__` and
`__repr__` return. The convention still has to be followed; what changes is that
following it is the path of least resistance and departing from it is visible.

**The renderer is inside the discipline, not upstream of it.** `render_window()` is the
one function that slices document text for a prompt, and it lives here rather than in a
caller for the reason the type exists: a renderer that returned a bare `str` would hand
every caller an unprotected copy, and the type would be a formality over a value that had
already escaped. It returns `FilledPrompt` and never a string, it writes nothing, and
`tests/test_prompt.py` asserts both over the syntax tree — a renderer that opened a
file "to debug a boundary" is the edit this is written against, and it would behave
identically on every machine where anyone would notice.

**This is the single renderer, merged from `port-human`'s.** `render_for_author()` in
`src/porting/human_arm.py` produced this same block for the human arm and
`tools/show_human_window.py` printed it. The merge is not the `check_rules`/`run_fold`
argument — nobody diffs two rendered windows, so there is no undiagnosable disagreement to
prevent. It is two other things. First, `rule_author.md`'s banner makes the agent-arm
comparison interpretable only if both arms are shown the same blocks *from the same code
path*, and the prompt is hashed into `window_freeze.json`: two implementations under one
hash means the record attests to a specification rather than to what ran. Second, this
convention is per-implementation — a second renderer is a second place the non-recording
discipline has to be re-established by hand, which is the thing a type was supposed to
stop. The direction of the merge is deliberate: the renderer moved here and the retired
arm's harness became a consumer, because the live agent path importing from a retired
arm's module would invert the dependency.

**What may be recorded is the reference form.** `FilledPrompt.reference()` is what a run
block or a log line may hold: the span references the prompt was built from
(`doc_id`, `span_index`), the counts, the template hashes, and the length of the rendered
text — no text. This is `human_log.jsonl`'s principle (DESIGN §11.2) applied to the one
artefact that cannot use it directly: a rule author genuinely needs the words and a
reference will not do, so the answer is not a safer representation but a shorter lifetime.
The reference form is what survives; the text exists only in transit.
"""
from __future__ import annotations

import hashlib
from typing import IO, Mapping, Sequence

from ..corpora.base import CorpusError, Document
from ..sample import ErrorSpan, WINDOW_FILES, file_hash


class PromptError(CorpusError):
    """A prompt that cannot be assembled, or an exit that is refused.

    Subclasses `CorpusError` for the reason the other error types here do: every case
    means "stop and tell a person", and no caller has a recovery path. A prompt that
    half-assembled is not a prompt to send.
    """


class FilledPrompt:
    """Rendered prompt text, with no accessor that is not named for a destination.

    Not a `str` and not a dataclass. A dataclass would generate a `__repr__` carrying the
    text and a `.text` field reachable by anything, which is the state this type exists to
    leave. `__slots__` keeps the attribute set closed, so a caller cannot stash a copy on
    the instance either.

    Two exits, and the asymmetry between them is the point:

    - `to_terminal(stream)` — a person reading a screen. Refuses a stream that is not a
      terminal, because `> window.txt` is precisely the file §6 says must not exist and it
      is one keystroke away from the intended use.
    - `for_transport()` — the API call. Returns the text with nothing attached; the caller
      is `src/llm/bedrock.py`, which must not log it.

    Everything else — `str()`, `repr()`, `json.dumps`, an f-string, a `print` of the
    object, a traceback that renders locals — reaches `reference()` instead. That is the
    case worth stating: an exception raised while a filled prompt is in scope is a path to
    a terminal and a CI log that no screener reaches (CLAUDE.md), and it is not a path
    anyone chose.
    """

    __slots__ = ("_text", "_reference")

    def __init__(self, text: str, reference: Mapping) -> None:
        if not isinstance(text, str):
            raise PromptError(
                f"a filled prompt is built from text, got {type(text).__name__}"
            )
        self._text = text
        # Copied rather than held: a caller that kept the mapping could mutate what the
        # record says after the record was taken, and the reference form is the part that
        # gets published.
        self._reference = dict(reference)

    # ── the two exits ────────────────────────────────────────────────────────

    def to_terminal(self, stream: IO[str]) -> None:
        """Write the text to a terminal. Refused for anything else.

        The check is here rather than only at the call site because a guard at a call site
        guards that call site — `src/sample.py`'s argument for putting `check_iteration`
        inside `draw()` rather than in each caller. `tools/show_human_window.py` checks
        `isatty` too, early, so the refusal arrives before a corpus is loaded; that check
        is for the error message and this one is the guarantee.
        """
        if not hasattr(stream, "isatty") or not stream.isatty():
            raise PromptError(
                "refusing to write a filled prompt to a stream that is not a terminal. "
                "The rendered text carries corpus context and may not be redirected to a "
                "file or a pipe (docs/prompts/rule_author.md §6). The reference form "
                "(`reference()`) is what may be captured."
            )
        stream.write(self._text)

    def for_transport(self) -> str:
        """The text, for the model call and for nothing else.

        Named for its destination so that a call to it is a visible claim about where the
        value is going. The transport must not log what it sends; that is
        `src/llm/bedrock.py`'s constraint and `tools/check_bedrock_logging.py` checks it.
        """
        return self._text

    # ── what may be recorded ─────────────────────────────────────────────────

    def reference(self) -> dict:
        """The publishable record of this prompt: references, counts, hashes, length.

        Resolvable by anyone holding the corpus and inert to anyone who does not — the
        referent DESIGN §11.2 fixes for `human_log.jsonl`. `text_sha256` is included
        because it settles "was this the prompt that ran" without holding the prompt, and
        `text_chars` because a length is the one property of the text that a reader
        comparing two runs can act on.
        """
        return dict(self._reference)

    # ── every other path out lands on the reference form ─────────────────────

    def __str__(self) -> str:
        return f"<FilledPrompt {self._reference.get('text_sha256', '?')}>"

    __repr__ = __str__

    def __len__(self) -> int:
        """The rendered length. A number, and the same one `reference()` records."""
        return len(self._text)

    def __eq__(self, other: object) -> bool:
        """Compared by content hash, so a test can assert identity without holding text."""
        if not isinstance(other, FilledPrompt):
            return NotImplemented
        return self._reference.get("text_sha256") == other._reference.get("text_sha256")

    def __hash__(self) -> int:
        return hash(self._reference.get("text_sha256"))


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_window(
    sample: Sequence[ErrorSpan],
    docs_by_id: Mapping[str, Document],
    context_chars: int,
) -> FilledPrompt:
    """The error-span block a rule author reads. **Contains corpus text.**

    `rule_author.md` §1.4's four lines per span: type, error kind, the context window, and
    the span's offsets *within that window*. The offsets are window-relative rather than
    document-relative because a rule author needs to see where the span sits in what they
    are reading, and a document offset would invite them to go and look up the surrounding
    text — the unbounded window §11.1 rejects.

    The context is clipped to the document and newlines are flattened to spaces. Flattened
    because one block is one span: a context containing a newline would otherwise be
    indistinguishable from the start of the next field.

    Returns a `FilledPrompt`. Never a `str`, and it writes nothing — see the module
    docstring, and `tests/test_prompt.py`, which asserts both structurally because
    a renderer that also wrote a debug copy would behave identically on every machine
    where anyone would notice.
    """
    if context_chars < 0:
        raise PromptError(
            f"context_chars must be non-negative, got {context_chars}. It is an "
            "experimental parameter from config/sampling.yaml and is recorded with the "
            "run (rule_author.md §1.4)."
        )
    out = []
    refs = []
    for i, span in enumerate(sample, 1):
        doc = docs_by_id.get(span.doc_id)
        if doc is None:
            raise PromptError(
                f"no document for the span at index {i - 1} of the sample "
                f"(doc_id {span.doc_id!r}). No surface form is quoted here (CLAUDE.md); "
                "the sample and the document set disagree, which means the fold they "
                "were derived from differs."
            )
        left = max(0, span.start - context_chars)
        right = min(len(doc.text), span.end + context_chars)
        window = doc.text[left:right].replace("\n", " ")
        out.append(
            f"[{i:2}] type      {span.phi_type}\n"
            f"     error     {span.kind}\n"
            f"     context   {window}\n"
            f"     offsets   ({span.start - left}, {span.end - left}) within that context\n"
        )
        refs.append({
            "doc_id": span.doc_id,
            "span_index": span.span_index,
            "phi_type": span.phi_type,
            "kind": span.kind,
            "start": span.start,
            "end": span.end,
        })
    text = "\n".join(out)
    return FilledPrompt(text, {
        "block": "error_spans",
        "spans": refs,
        "n_spans": len(refs),
        "context_chars": context_chars,
        "text_chars": len(text),
        "text_sha256": _digest(text),
        # The template and the config that together fix the window (`src/sample.py`),
        # hashed. A record naming only the prompt would agree with a doubled `n` as
        # readily as with 40.
        "window_files": {name: file_hash(name) for name in WINDOW_FILES},
    })
