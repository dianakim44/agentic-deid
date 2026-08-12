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

**`assemble_task_prompt()` is the §§1.1–1.2 half, and it is in this module for the same
reason the renderer is.** It fills the two blocks a first call carries — the task frame and
the current rule file — and it returns a `FilledPrompt` rather than a `str`, even though
neither block quotes corpus text. Two reasons. First, a call's prompt is one value: an
assembler returning a string and a renderer returning a type would leave the caller
concatenating them, and the concatenation is where the protection would be re-established
by hand for `port-loop`'s iteration 2. Second, "this block happens to be safe" is the
reasoning CLAUDE.md refuses about corpora and it is no better about blocks — the rule file
is screened for surface forms by a positive vocabulary rather than known to be clean
(`rule_author.md` Prohibition 2), and a prompt whose safety depends on that screener having
worked is not a prompt to write to disk.

It does not call `render_window()` and it does not draw anything. DESIGN §4 defines the
first call as carrying §§1.1–1.2 with §§1.3–1.4 empty, in `port-oneshot` and in
`port-loop`'s iteration 1 alike, so the two arms' call 1 is shown the same thing; the
error-span block enters from iteration 2 through the renderer above.

**`mask_document()` is the masker, and it is in this module because the discipline is
per-module and not per-type.** `docs/prompts/auditor.md` §6 calls it "the second function in
the project that slices document text for a prompt — `render_window()` is the first — so it
lives inside the same discipline rather than beside it". A new module would have satisfied
the *type* half of that sentence and not the structural half: `tests/test_prompt.py` asserts
over this file's syntax tree that no function in it writes, logs or prints, and those checks
walk every function here, including ones added later. A masker in its own file would need
its own copy of them — "a second place the non-recording discipline has to be re-established
by hand", which is the cost the paragraph above gives for a second renderer. So the masker
is here, and the checks it needs already exist and already cover it.

The masked document is the **largest** corpus exposure in the project — about 40× §1.4's
window, and mostly *unmasked* identifiers, because unmasked is what "leaked" means
(DESIGN §3, `auditor.md` §6). It is the strongest case for the type rather than an exception
to it: `MaskedDocument` carries a `FilledPrompt` and a geometry, and the masked text exists
nowhere else.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Mapping, Sequence

from .. import sample as sample_module
from ..corpora.base import (
    CorpusError, Document, axis, corpus_ids, masked_tag_heterogeneous, rule_langs,
)
from ..porting.audit import MaskedLine
from ..rules import rule_layers
from ..sample import ErrorSpan, WINDOW_FILES, file_hash, non_target_types

#: The §1 blocks a first call carries, and the blocks it leaves empty (DESIGN §4).
#: Named here because `assemble_task_prompt()` states both in the prompt and records both
#: in the reference form, and `src/orchestrate.py` checks them against the sections its
#: freeze record claims were shown. A prompt and a freeze record that disagree about the
#: window is the failure §6.3 exists to prevent, and the only way to notice it is to have
#: both say so in a form that can be compared.
TASK_FRAME = "1.1"
CURRENT_RULES = "1.2"
FILLED_SECTIONS = (TASK_FRAME, CURRENT_RULES)
EMPTY_SECTIONS = ("1.3", "1.4")

#: Where the committed template ends and the filled blocks begin. A visible line rather
#: than a blank one: the template is sent verbatim and the model has to be able to tell
#: the specification it is reading from the input it is answering about.
INPUT_BANNER = "=" * 12 + " INPUT FOR THIS CALL " + "=" * 12


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


# ─── §§1.1–1.2: the blocks a first call carries ──────────────────────────────


def _template() -> str:
    """`docs/prompts/rule_author.md` as it stands on disk, sent verbatim.

    Resolved through `src.sample`'s module globals rather than through a local copy of the
    path, because `window_hashes()` resolves it that way and `src/orchestrate.py`'s
    `prompt_blocks()` resolves it that way. The file is hashed into `window_freeze.json`,
    so two resolutions of it is how a freeze record comes to hash one file while the call
    was shown another — and under a redirected root that divergence appears only in the
    tests, which is the one place the record is not what is being read.

    Sent verbatim rather than summarised or excerpted. §2 fixes the output schema, §3 the
    tool boundary and §4 the prohibitions, and an assembler that forwarded only §1 would be
    sending the input without the specification the input is an instance of.
    """
    return (sample_module.ROOT / sample_module.PROMPT_TEMPLATE).read_text(
        encoding="utf-8")


def _task_frame(lang: str, corpus: str) -> str:
    """§1.1: the target file, the canonical types, the writable layers, and `OTHER`.

    Every value is read from `config/naming.yaml` — the type names with the one-line gloss
    each carries there, the layers from the rules family, the corpora that load this file
    from `corpus_rule_langs`. Nothing is spelled here. That is CLAUDE.md's rule about
    vocabulary applied to the place it matters most: this block is what tells the agent
    which values exist, so a hardcoded list would teach it an axis that has drifted from the
    config and the rules it wrote would then be refused at load by the same config.

    `OTHER` is named as excluded rather than silently omitted from the type list, and it is
    identified by `non_target_types()` — the gloss in `naming.yaml`, not the string
    `"OTHER"`. Omitting it would leave an agent that has seen `OTHER` in a corpus's own
    output wondering whether the list was complete; `rule_author.md` §1.1 requires the
    prohibition to be stated, because an agent given a residual bucket writes rules into it.
    """
    langs = axis("lang")
    if lang not in langs:
        raise PromptError(
            f"{lang!r} is not a value of the lang axis in config/naming.yaml (have: "
            f"{sorted(langs)}). The task frame tells the agent which file it is writing, "
            "and a language the config does not declare would be a file no corpus loads."
        )
    if corpus not in corpus_ids():
        raise PromptError(
            f"{corpus!r} is not a corpus in config/naming.yaml (have: {corpus_ids()})."
        )
    if lang not in rule_langs(corpus):
        raise PromptError(
            f"{corpus} does not load a {lang!r} rule file (corpus_rule_langs: "
            f"{rule_langs(corpus)}). An invocation targets one file and the corpus being "
            "run has to be one that loads it, or the arm would author rules nothing reads "
            "(DESIGN §5.2)."
        )

    loaders = [c for c in corpus_ids() if lang in rule_langs(c)]
    blocked = non_target_types()
    types = axis("phi_type")
    layers = axis("layer")
    writable = sorted(rule_layers())

    lines = [
        f"### {TASK_FRAME} Task frame",
        "",
        f"Target file: rules/{lang}.yaml — the `{lang}` rule file. `{lang}` is the "
        "language of the file, not of the corpus or of any document.",
        f"Corpora that load this file (config/naming.yaml corpus_rule_langs): "
        f"{', '.join(loaders)}.",
        f"This call is being run for {corpus}.",
        "",
        "Canonical `phi_type` values, verbatim from config/naming.yaml with its own gloss:",
        "",
    ]
    for name in sorted(types):
        # The gloss is quoted as it stands and nothing is appended to it, including for an
        # excluded type. `non_target_types()` detects exclusion *from* that gloss, so a
        # marker beside it would restate the sentence it was derived from; the prohibition
        # gets its own paragraph below, where §1.1 requires it stated outright.
        lines.append(f"  {name:<16} {types[name]}")
    # The layers a rule may not declare, by difference rather than by name. `tagger` is the
    # one today and writing it here would be a naming.yaml value living in Python
    # (CLAUDE.md) — and a second learned layer would then be omitted from this sentence
    # while the rules the agent writes were still refused at load for declaring it.
    others = sorted(set(layers) - set(writable))
    lines += [
        "",
        f"`layer` values this agent may write ({len(writable)} of the layer axis — the "
        "rules family, which is what a rule file produces):",
        "",
    ]
    for name in writable:
        lines.append(f"  {name:<16} {layers[name]}")
    lines.append("")
    if others:
        lines.append(
            f"Not writable here: {', '.join(others)}. Those layers are not produced by a "
            "rule file, and a rule declaring one is refused at load."
        )
    lines += [
        "The layer is declared by the rule and copied onto every span it emits; it is "
        "never inferred from the rule's name or its matcher (DESIGN §3).",
        "",
    ]
    for name in sorted(blocked):
        lines.append(
            f"{name} is not a rule-development target. config/naming.yaml declares it "
            f"({types[name]}), and a rule targeting it is refused at load "
            "(rule_author.md Prohibition 4). Do not write one."
        )
    return "\n".join(lines)


def _current_rules(lang: str, rules_path: Path | None) -> tuple[str, dict]:
    """§1.2: the full current rule file, or the statement that there is none yet.

    Returns the block and what the reference form records about it. Full text rather than a
    summary, per `rule_author.md` §1.2: the agent emits the complete file, and an agent
    editing a summary of a file produces a diff that does not apply.

    A path that does not exist is iteration 1's ordinary state, not an error — an arm's rule
    files live under the arm (DESIGN §5.3) and the first call is what creates the first one.
    It is reported as empty *and named as such in the block*, because an agent shown nothing
    where a file was promised cannot tell "no rules yet" from "the harness failed to load
    them", and the two call for opposite behaviour.

    The file's bytes are hashed into the reference form. That is the field that answers
    "which rule file did this call actually see" without holding the file, and it is the
    §1.2 analogue of `window_files` — the block is the input the emitted diff applies to,
    so a record naming only the path would agree with any revision of it.
    """
    heading = f"### {CURRENT_RULES} Current rule file — rules/{lang}.yaml"
    if rules_path is None or not rules_path.exists():
        block = "\n".join([
            heading, "",
            "EMPTY. There is no current rule file: this is the first iteration and the "
            "file starts at zero rules. Emit a complete file, not a patch.",
        ])
        return block, {"rules_source": None, "rules_chars": 0, "rules_sha256": None,
                       "rules_empty": True}
    text = rules_path.read_text(encoding="utf-8")
    block = "\n".join([
        heading, "",
        "The complete current file follows. Emit the complete revised file, not a patch.",
        "", "```yaml", text.rstrip("\n"), "```",
    ])
    return block, {
        # The filename alone when the file is outside the repository, for
        # `rules._relative()`'s reason: this record is published and an absolute path names
        # a home directory. Composed here rather than imported, because that helper is
        # about a run block's `rules_source` field and this is about a prompt's input.
        "rules_source": str(rules_path.name if not _inside_repo(rules_path)
                            else rules_path.resolve().relative_to(
                                sample_module.ROOT.resolve())),
        "rules_chars": len(text),
        "rules_sha256": _digest(text),
        "rules_empty": not text.strip(),
    }


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(sample_module.ROOT.resolve())
    except (ValueError, OSError):
        return False
    return True


def assemble_task_prompt(
    *,
    lang: str,
    corpus: str,
    rules_path: Path | None = None,
) -> FilledPrompt:
    """The first call's prompt: the template, then §1.1 and §1.2, with §§1.3–1.4 empty.

    This is what DESIGN §4 defines `port-oneshot` as being shown — and `port-loop`'s
    iteration 1 with it, so that call 1 is the same in both arms and feedback begins at
    iteration 2. Nothing here draws or renders error spans: §§1.3 and 1.4 are stated as
    empty and the reason is given in the prompt, because an agent shown a block with no
    content cannot otherwise tell an absent block from a harness that dropped one.

    **The empty blocks are stated, not omitted.** The alternative — send §1.1 and §1.2 and
    say nothing about the rest — was the obvious shape and is not what this does. The
    template the agent is reading describes four blocks; two arriving with no explanation
    is a discrepancy the agent has to resolve, and the resolutions available to it are all
    worse than being told (that it should ask for the scores, that the harness is broken,
    that it should infer errors from the corpus it cannot read).

    Returns a `FilledPrompt`. It carries no corpus text today — §1.4 is where that lives —
    and it is still not written down, for the reason in the module docstring: a prompt whose
    safety rests on the `rule_id` screener having worked is not one to put on disk.
    """
    frame = _task_frame(lang, corpus)
    rules_block, rules_ref = _current_rules(lang, rules_path)
    empty = "\n".join([
        f"### {section} — EMPTY for this call"
        for section in EMPTY_SECTIONS
    ])
    text = "\n\n".join([
        _template(),
        INPUT_BANNER,
        frame,
        rules_block,
        empty,
        "There is no previous iteration, so there are no scores and no error spans: "
        f"§{' and §'.join(EMPTY_SECTIONS)} of the template above are empty for this call "
        "rather than withheld. This is the arm's definition and not a gap in the harness "
        "(DESIGN §4). Do not ask for them and do not substitute anything for them — a "
        "profile summary or a type inventory standing in for the score block would make "
        "this call something other than the no-feedback baseline it is.",
        f"Emit the complete rules/{lang}.yaml and nothing else.",
    ])
    return FilledPrompt(text, {
        "block": "task_frame",
        "lang": lang,
        "corpus": corpus,
        # What the call carried and what it did not, in the same shape
        # `window_freeze.json` records it (`sections_shown` / `sections_empty`). Written
        # in both places on purpose: the freeze record is the arm's claim about its window
        # and this is the prompt's own account of itself, and a comparison needs two
        # statements rather than one restated.
        "sections_filled": list(FILLED_SECTIONS),
        "sections_empty": list(EMPTY_SECTIONS),
        **rules_ref,
        "text_chars": len(text),
        "text_sha256": _digest(text),
        "window_files": {name: file_hash(name) for name in WINDOW_FILES},
    })


# ─── the masker: the Auditor's §1.2 block (DESIGN §3) ────────────────────────


#: The mask tag for a union whose spans agree on the type. The `phi_type` comes from
#: `config/naming.yaml`'s axis and only the brackets are spelled here — the notation
#: `auditor.md` §1.1 tells the agent to recognise as a tag.
#:
#: **Not derived from `masked_tag_heterogeneous()` and not the source of it.** Deriving
#: either from the other would make an edit to one silently agree with the other, and the
#: property that has to hold between them is that they are *distinguishable*: the
#: heterogeneous tag names no type (`src/corpora/base.py` refuses one that does), and that
#: check is what keeps this form and that value apart.
TAG_FORM = "[{phi_type}]"

#: `auditor.md` §1.3's line prefix: the line's start offset **in the masked text**,
#: zero-padded to a fixed width, then a separator. The prompt's example is `0000000 | …`,
#: and the width is fixed rather than computed per document so that two documents' blocks
#: look the same to the agent and a column is countable by eye.
#:
#: **The prefix is not part of the line** (`auditor.md` §1.3) — column 0 is the character
#: after the separator, and neither the offset nor the separator is in `MaskedLine.length`.
LINE_OFFSET_WIDTH = 7
LINE_SEPARATOR = " | "

#: What `MaskedDocument.counts` publishes, and the reason it is a named subset rather than
#: the whole reference form: these are the numbers DESIGN §3 promises to report when the
#: first `port-loop` arm runs — the overlapping-pair count and the heterogeneous-union
#: count, which are currently vacuous (0 predictions on es-meddocan dev, §3). A caller
#: putting them in `metrics.json` should not have to pick them out of a dict of hashes.
COUNT_KEYS = (
    "n_input_spans", "n_tags", "n_heterogeneous_tags", "n_overlapping_pairs",
)


@dataclass(frozen=True, slots=True)
class _Union:
    """One masked extent: the union of a transitive overlap chain, and its tag.

    `phi_types` is what the spans in the chain claimed, so `len(phi_types) == 1` is the
    homogeneous case. Kept rather than reduced to a boolean because the tag is chosen from
    it and the count of heterogeneous unions is reported from it.
    """

    start: int
    end: int
    tag: str
    phi_types: frozenset[str]

    @property
    def heterogeneous(self) -> bool:
        return len(self.phi_types) > 1


@dataclass(frozen=True, slots=True)
class _Piece:
    """One stretch of the masked text: kept document characters, or one tag.

    **`from_right` is how many masked characters follow this piece**, and it is what makes
    the right-to-left walk self-sufficient. A piece's offset from the *left* depends on
    every replacement to its left, which a right-to-left walk has not made yet; its offset
    from the right depends only on what the walk has already done. So the walk records
    distances from the right and one subtraction at the end converts them, rather than a
    second left-to-right pass that would be a second implementation of the same arithmetic
    (`audit._to_document`'s reason for reading the map instead of rebuilding it).

    No text: a piece is a length and a document extent. The characters are in the string
    being assembled and nowhere else.
    """

    length: int
    doc_start: int
    doc_end: int
    is_tag: bool
    from_right: int


@dataclass(frozen=True, slots=True)
class MaskedDocument:
    """One masked document: the block to send, and the geometry to translate flags with.

    `block` is the `FilledPrompt` — **the only place the masked text exists.** A `text`
    field here would be the `MaskedLine.text` mistake at the level above it
    (`src/porting/audit.py`): a dataclass field holding masked corpus text, on a type whose
    `repr` renders it. `FilledPrompt`'s own `repr` is its reference form, so a
    `MaskedDocument` in a traceback prints counts and a hash.

    `lines` is what `validate_flags()` takes: one `MaskedLine` per line of the masked text,
    with the tags on it ascending by column. No masked offsets on those — the line's own
    offset is the prompt's line prefix and belongs to the renderer (that type's docstring),
    which is this function.

    The counts are in the block's `reference()` and reachable through `counts`, rather than
    duplicated as fields here: one storage site, so the number in `metrics.json` and the
    number in a log line cannot disagree.
    """

    doc_id: str
    block: FilledPrompt
    lines: tuple[MaskedLine, ...]

    @property
    def counts(self) -> dict:
        """The measurement DESIGN §3 pre-registered, for `metrics.json`."""
        reference = self.block.reference()
        return {key: reference[key] for key in COUNT_KEYS}


def _check_input_span(index: int, span: object, doc_chars: int) -> tuple[int, int, str]:
    """One prediction, checked into `(start, end, phi_type)`. No surface form in any message.

    Reads three attributes and no more — the three `spans.jsonl` carries (DESIGN §3), so
    the masker's input is a prediction and not a `Document`'s gold. Checked rather than
    trusted even though `corpora.base.Span` validates most of it at construction: the
    masker's caller is a loop driver that may have read rows back from a file, and a span
    past the end of the text would make every column after it wrong rather than absent.
    """
    start, end, phi_type = (
        getattr(span, "start", None), getattr(span, "end", None),
        getattr(span, "phi_type", None),
    )
    if any(not isinstance(v, int) or isinstance(v, bool) for v in (start, end)):
        raise PromptError(
            f"prediction {index} has non-integer offsets. The masker reads `start`, `end` "
            "and `phi_type` and nothing else (DESIGN §3)."
        )
    if end <= start or start < 0:
        raise PromptError(
            f"prediction {index} spans [{start}, {end}), which is empty, inverted or "
            "negative. A masked extent stands for at least one document character."
        )
    if end > doc_chars:
        raise PromptError(
            f"prediction {index} ends at {end} in a document of {doc_chars} characters. "
            "The predictions and the document disagree, which means they came from "
            "different folds — no offset in this document can be trusted."
        )
    if not isinstance(phi_type, str) or phi_type not in axis("phi_type"):
        raise PromptError(
            f"prediction {index} carries phi_type {phi_type!r}, which is not in "
            f"config/naming.yaml's phi_type axis (have: {sorted(axis('phi_type'))}). The "
            "tag is printed into the Auditor's prompt, so it is a value of that axis or it "
            "is a vocabulary item invented in code (CLAUDE.md)."
        )
    return start, end, phi_type


def _unions(spans: Sequence, doc_chars: int) -> tuple[list[_Union], int]:
    """Overlapping predictions -> masked extents, plus the overlapping-pair count.

    **DESIGN §3's rule, both halves.** The extent is the union of a *transitive* chain of
    pairwise overlaps, so masking does not depend on the order the spans arrive in; the tag
    prints a `phi_type` only where every span in the chain agrees, and otherwise prints
    `masked_tag_heterogeneous()`, which names no type. Nothing here ranks types, lengths or
    scores: a masker that picked a winner would hold a merge policy, and merge policy is a
    replaceable strategy that must not be baked into a component every arm runs through
    (§4, §9.3).

    Touching is not overlapping. Adjacency is the ordinary case — 393 gold pairs within one
    character on es-meddocan dev (§3) — so a span starting exactly where the previous one
    ends is two tags, and `[NAME][DATE]` is a correct rendering of two abutting detections.

    The pair count is every pair that genuinely overlaps, including two byte-identical
    predictions from different rules: that pair is not degenerate for this purpose, it is
    precisely the case where two detectors disagree about the type of one extent.
    """
    checked = sorted(
        (_check_input_span(i, span, doc_chars) for i, span in enumerate(spans)),
        key=lambda s: (s[0], s[1]),
    )
    pairs = 0
    for i, (start, end, _) in enumerate(checked):
        for other_start, _, _ in checked[i + 1:]:
            if other_start >= end:
                break
            pairs += 1

    unions: list[_Union] = []
    start = end = -1
    types: set[str] = set()
    for span_start, span_end, phi_type in checked:
        if span_start < end:
            # Transitive: the chain extends as long as the next span starts inside what the
            # chain covers so far, so A-B and B-C become one tag without C ever being
            # compared to A.
            end = max(end, span_end)
            types.add(phi_type)
            continue
        if end >= 0:
            unions.append(_close(start, end, types))
        start, end, types = span_start, span_end, {phi_type}
    if end >= 0:
        unions.append(_close(start, end, types))
    return unions, pairs


def _close(start: int, end: int, types: set[str]) -> _Union:
    """One chain -> one `_Union`, choosing the tag and nothing else."""
    phi_types = frozenset(types)
    tag = (TAG_FORM.format(phi_type=next(iter(phi_types))) if len(phi_types) == 1
           else masked_tag_heterogeneous())
    return _Union(start=start, end=end, tag=tag, phi_types=phi_types)


def mask_document(document: Document, spans: Sequence) -> MaskedDocument:
    """The Auditor's input: this document with every detected span replaced by its tag.

    **Contains corpus text, and more of it than anything else here** — DESIGN §3 puts the
    masked dev fold at about 40× §1.4's window, and most of the identifiers in it are
    *unmasked*, because unmasked is what "leaked" means. Returns a `FilledPrompt` inside a
    `MaskedDocument`, never a `str`, writes nothing, and quotes nothing in any message. It
    is the §1.2 block rather than a whole prompt, which is `render_window()`'s shape: the
    call's other blocks are assembled by the driver.

    `spans` are the arm's own predictions — the previous round's, for the loop
    (`auditor.md` banner). **`document.spans` is never read**, and that is not a detail:
    those are gold, and masking gold would hand the Auditor the answer it exists not to
    have (DESIGN §3).

    Applied **right-to-left** (DESIGN §3, `auditor.md` §1.2), and the consequence is the
    one thing to be careful about here: the walk visits the last union first, so its
    natural emission order is *descending*, which is the order `audit._check_tags` refuses.
    The pieces are reversed once — reversed and not sorted, for the reason that check gives
    for not sorting either. A reverse of a descending walk is provably ascending, and if
    the walk were ever not monotone, `MaskedLine` raises; a sort would accept any order
    forever and the defect would ship hidden.

    No inference, so no error rate to measure: the extent is the union of what the arm
    detected and the tag states a type only where the arm's own detectors agreed. It also
    makes no decision about *which* types deserve masking — every prediction is masked,
    including one the RuleAuthor may not act on, because a tag is a statement that
    something was detected and not a statement that it mattered.
    """
    text = document.text
    unions, pairs = _unions(spans, len(text))

    # ── right to left ────────────────────────────────────────────────────────
    chunks: list[str] = []          # masked text, suffix first
    walk: list[_Piece] = []         # descending, with distances from the right
    from_right = 0
    cursor = len(text)
    for union in reversed(unions):
        kept = cursor - union.end
        if kept:
            chunks.append(text[union.end:cursor])
            walk.append(_Piece(kept, union.end, cursor, False, from_right))
            from_right += kept
        chunks.append(union.tag)
        walk.append(
            _Piece(len(union.tag), union.start, union.end, True, from_right))
        from_right += len(union.tag)
        cursor = union.start
    if cursor:
        chunks.append(text[:cursor])
        walk.append(_Piece(cursor, 0, cursor, False, from_right))
        from_right += cursor

    masked = "".join(reversed(chunks))
    if from_right != len(masked):
        raise PromptError(
            f"the mask walk accounted for {from_right} characters and the masked text has "
            f"{len(masked)}. Every column the Auditor returns is translated through those "
            "distances, so a disagreement here makes each translated offset a plausible "
            "wrong number rather than a failure."
        )

    # **The one reversal.** `walk` is descending because the application is right-to-left;
    # every tag below is emitted from this list, so this is where ascending order comes
    # from and `MaskedLine` is what refuses if it does not.
    pieces = [(piece, len(masked) - piece.from_right - piece.length)
              for piece in reversed(walk)]

    # ── lines ────────────────────────────────────────────────────────────────
    raw_lines = masked.split("\n")
    line_starts: list[int] = []
    at = 0
    for raw in raw_lines:
        line_starts.append(at)
        at += len(raw) + 1

    doc_offsets: list[int | None] = [None] * len(raw_lines)
    line_tags: list[list[tuple[int, int, int, int]]] = [[] for _ in raw_lines]
    index = 0
    for piece, masked_start in pieces:
        masked_end = masked_start + piece.length
        while index < len(line_starts) and line_starts[index] < masked_end:
            # A line starting inside this piece takes its document offset from it. Inside a
            # tag that offset is the tag's own start: the tag replaced those characters, so
            # every column on such a line is measured from where the replacement began.
            inside = line_starts[index] - masked_start
            doc_offsets[index] = piece.doc_start + (0 if piece.is_tag else inside)
            index += 1
        if piece.is_tag:
            # `index - 1` is this tag's line: a tag holds no newline, so no line can start
            # inside one, and the line it sits on was consumed either just now (a tag at
            # column 0) or by an earlier piece.
            line = index - 1
            line_tags[line].append(
                (masked_start - line_starts[line], piece.length,
                 piece.doc_start, piece.doc_end))
    while index < len(line_starts):
        # Only a line start at the very end of the masked text can be left, and only
        # because the document ends with a newline: the pieces tile [0, len(masked))
        # contiguously, and a tag cannot end a line.
        if line_starts[index] != len(masked):
            raise PromptError(
                f"line {index} starts at masked offset {line_starts[index]} and no piece "
                f"of the masked text covers it ({len(masked)} characters, "
                f"{len(pieces)} pieces). The coordinate map is incomplete, so the columns "
                "on that line would translate to document offsets nothing stands at."
            )
        doc_offsets[index] = len(text)
        index += 1

    lines = tuple(
        MaskedLine(length=len(raw), doc_offset=offset, tags=tuple(tags))
        for raw, offset, tags in zip(raw_lines, doc_offsets, line_tags)
    )

    # ── the block ────────────────────────────────────────────────────────────
    rendered = "\n".join(
        f"{start:0{LINE_OFFSET_WIDTH}d}{LINE_SEPARATOR}{raw}"
        for start, raw in zip(line_starts, raw_lines)
    )
    by_type: dict[str, int] = {}
    for union in unions:
        if not union.heterogeneous:
            by_type[next(iter(union.phi_types))] = (
                by_type.get(next(iter(union.phi_types)), 0) + 1)
    return MaskedDocument(
        doc_id=document.doc_id,
        block=FilledPrompt(rendered, {
            "block": "masked_document",
            "doc_id": document.doc_id,
            "corpus": document.corpus_id,
            "n_input_spans": len(spans),
            "n_lines": len(lines),
            "n_tags": len(unions),
            "n_heterogeneous_tags": sum(1 for u in unions if u.heterogeneous),
            # Every pair that overlaps, which is what DESIGN §3 said it would measure once
            # an arm predicts anything. `n_tags` below it is the count after unioning, so
            # the two together say how much collapsing happened.
            "n_overlapping_pairs": pairs,
            "tags_by_phi_type": dict(sorted(by_type.items())),
            "document_chars": len(text),
            "masked_chars": len(masked),
            # The rendered block, which is what was sent: the line prefixes are part of it.
            # `masked_chars` beside it is the corpus-exposure number §3 quotes.
            "text_chars": len(rendered),
            "text_sha256": _digest(rendered),
            "window_files": {name: file_hash(name) for name in WINDOW_FILES},
        }),
        lines=lines,
    )
