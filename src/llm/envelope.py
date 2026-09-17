"""One outer code fence, stripped only when what is inside parses. DESIGN §6.8.

Every prompt's §2.1 asks for one object and no fence, and until 2026-09-17 a fenced response
was a format failure at whichever parser received it. That is now the one thing that changed:
**a valid object inside exactly one outer fence is accepted, and nothing else is.** The retry
budget is still zero (DESIGN §10 A2), no key is repaired, no whitespace is normalised, and no
second attempt is made — what moved is the boundary of the format contract, not the number of
tries at it.

**Why the fence and not something else.** Measured, on 100 draws through
`tools/probe_prompt_format.py` (`docs/notes/prompt-format-probe.md`, 2026-09-17): the Mapper's
twenty draws hold **two** distinct responses, and the difference between them is the fence
itself — identical body characters, identical depth, identical key count, five completion
tokens apart. So on that prompt the fence carries no information about the content, and the
arm that died on it (`port-multi-noexample`, 2026-09-17) received the same object as the seven
draws that passed. A boundary that fails a response for the fence is a boundary that fails it
for a property of the envelope, which is what §6.8 pre-registers away.

**Why this is not the repair §10 A2 forbids.** That clause forbids a step that makes a failure
disappear from the count — "make the obvious fix and validate again", with the record still
reading zero failures. Three properties keep this out of that shape and each is enforced here
rather than promised:

1. **The strip is not conditional on wanting the response.** It is one shape, `fence /
   payload / fence`, and if the payload does not parse the response is still a failure. Nothing
   is tried twice, nothing is edited character by character until it loads.
2. **The record says both.** `Envelope.record()` writes the received bytes *and* the parsed
   bytes, so an accepted fence is visible as an accepted fence forever after. A reader can
   still count them, which is what keeps §6.9's per-role rates measurable on arms that pass.
3. **It applies to every role.** One function, called on every response path — RuleAuthor,
   Auditor, Profiler, Mapper, LexiconBuilder. A per-role exemption is the shape the original
   defect was inherited through: three prompts said the same thing in §2.1 and one parser was
   strict about it while the other two were reached by different code.

**What stays a failure**, and the list is the policy rather than an implementation accident:
a response with no fence that does not parse; a nested fence; a fence opened and not closed,
or closed and not opened; any text outside the fence, including a one-word preamble; and a
payload that does not parse after the fence comes off. All five record `refused`, and the
caller's own validator then raises on the **received** bytes, so the message and
`format_failure.json` describe what arrived rather than what this module made of it.

**The trial parse happens before the call is logged, and that is a change in ordering.** Every
driver appends its `agent_calls.jsonl` line before judging the response, because the line is
what fixes the window (`orchestrate.call_line`). The line now carries what was *read*, which
cannot be known without attempting a parse — so `unwrap()` runs first and is built so that
running first is safe: it catches every exception the probe can raise and returns `refused`
rather than propagating. Validation still happens after the line is on disk. What moved is a
bounded, non-raising classification of bytes; what did not move is the judgement.

**No response text leaves this module.** `record()` reduces both sides to a length and a
hash, `fence_lines` is a count, and the fence's language tag is deliberately *not* recorded:
it is response bytes, and a record that carried it would be a second copy of model output in
the one file `tools/release_screen.py` cannot review (CLAUDE.md). The counts answer the
question the tag would have.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Callable

import yaml

from ..corpora.base import check_response_envelope

#: The three envelope kinds, spelled once here so this module's branches cannot drift from
#: the vocabulary. Each is checked against `config/naming.yaml` by `check_response_envelope`
#: at record time, so a value renamed in the config fails loudly rather than being written
#: unvalidated — `termination.CONVERGED`'s arrangement, one vocabulary over.
BARE = "bare"
FENCED_ONCE = "fenced_once"
REFUSED = "refused"

#: The three fields a record adds beyond the two the call log already had. Named here because
#: `orchestrate.call_line()` validates the dict it is handed against the full key set: a
#: caller that assembled four of the six fields by hand would be a role exemption written as
#: a literal, which is the failure mode property 3 of the module docstring is about.
RECEIVED_FIELDS = ("response_chars", "response_sha256")
PARSED_FIELDS = ("envelope", "fence_lines", "parsed_chars", "parsed_sha256")
RECORD_FIELDS = RECEIVED_FIELDS + PARSED_FIELDS

#: A line that opens a fence: three backticks and an optional language tag, alone on the line.
#: The tag is matched and thrown away rather than recorded — see the module docstring.
_OPEN = re.compile(r"^```[A-Za-z0-9_+.#-]*$")

#: What closes one. Compared as a whole line, so ```` ```json ```` cannot close a fence it
#: could have opened.
_CLOSE = "```"


def _digest(text: str) -> str:
    """`sha256:`-prefixed, as `orchestrate._digest` and `artefacts._digest` both are."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def parses_json(text: str) -> bool:
    """True if `text` is one JSON **object**. The probe the four JSON roles unwrap against.

    An object rather than any JSON value, because that is what §2.1 asks for at every one of
    them and because the looser test would make the fence unstrippable in a case that matters:
    a fenced body whose first line is ```` ```json ```` is not JSON, but a *whole* fenced
    response can occasionally load as a JSON string, and a probe that accepted a string would
    call that response `bare` and hand the parser a fence.

    Catches every exception rather than the three `json.loads` documents. This runs before the
    call is logged (module docstring), and a probe that raised would lose the log line for a
    call that was made and paid for.
    """
    try:
        return isinstance(json.loads(text), dict)
    except Exception:  # noqa: BLE001 — see the docstring: this may not raise.
        return False


def parses_yaml(text: str) -> bool:
    """True if `text` is a YAML mapping. The probe the rule file unwraps against.

    A mapping for `parses_json`'s reason and more sharply: `yaml.safe_load` returns a *string*
    for many inputs that are not documents at all, including some fenced blocks, so a probe
    that only asked "did it load" would call a fenced rule file bare and then fail it at
    `load_rules` — the exact outcome this policy exists to stop being about the fence.

    `safe_load` and never `load`, for `rules.load_rules`' reason: the response is model output
    and a loader that can construct Python objects is a loader that can be asked to.
    """
    try:
        return isinstance(yaml.safe_load(text), dict)
    except Exception:  # noqa: BLE001 — see `parses_json`.
        return False


@dataclass(frozen=True)
class Envelope:
    """What arrived, what is to be parsed, and which of the three kinds that makes it.

    `received` and `payload` are the same string for `bare` and for `refused`, and differ by
    exactly the fence for `fenced_once`. They are kept as two fields rather than one plus a
    flag because every writer needs both, and a writer that derived one from the other would
    be a second place the strip is implemented.
    """

    received: str
    payload: str
    kind: str
    fence_lines: int

    @property
    def accepted(self) -> bool:
        """True unless nothing parsed. `refused` is the one kind whose payload is undefined."""
        return self.kind != REFUSED

    def record(self) -> dict:
        """The six fields a record carries about the bytes. See the module docstring.

        Both sides, always, and `bare` writes the parsed pair explicitly rather than leaving a
        reader to infer that it equals the received pair — the inference is exactly what an
        accepted fence would hide, and a field that is present only when it differs is a field
        whose absence has two meanings (`model_id_absent`'s argument, DESIGN §4).

        `parsed_*` is null on `refused` because nothing was parsed. A zero would be a length,
        and a length would say an empty string was read.
        """
        kind = check_response_envelope(self.kind)
        received = self.received if isinstance(self.received, str) else ""
        parsed = self.payload if self.accepted and isinstance(self.payload, str) else None
        return {
            "response_chars": len(received),
            "response_sha256": _digest(received),
            "envelope": kind,
            "fence_lines": self.fence_lines,
            "parsed_chars": None if parsed is None else len(parsed),
            "parsed_sha256": None if parsed is None else _digest(parsed),
        }


def _fence_lines(lines: list[str]) -> int:
    """How many lines of the response are fence lines, counted on the received bytes.

    A line-shape count and not a claim about structure: a JSON string value whose content
    begins a line with three backticks is counted here, and that is preferred to a count that
    parsed the response to decide — the number's job is to say what a reader would see.
    """
    return sum(1 for line in lines if line.strip().startswith(_CLOSE))


def unwrap(text: str, *, parses: Callable[[str], bool]) -> Envelope:
    """Classify one response and hand back what should be parsed. Never raises.

    `parses` is the probe for the format the caller expects — `parses_json` at four roles,
    `parses_yaml` at the rule file. It is a parameter rather than a branch on the role, because
    a branch on the role is the exemption path the module docstring rules out; what differs
    between the roles is the language of the payload and nothing else.

    The order is: parse what arrived, and only if that fails look for the one shape that may
    be stripped. So a bare response is never inspected for a fence, and a response that
    happens to parse *with* its fence — none is known, and JSON cannot — would be recorded as
    `bare`, which is true of it.
    """
    if not isinstance(text, str):
        # The transport hands back a string; this branch is for the caller that passes
        # something else, and it keeps `unwrap` to its promise of not raising. The payload is
        # returned untouched so the caller's own parser raises on the object it was given.
        return Envelope(received="", payload=text, kind=REFUSED, fence_lines=0)

    lines = text.strip().splitlines()
    fences = _fence_lines(lines)
    if parses(text):
        return Envelope(received=text, payload=text, kind=BARE, fence_lines=fences)

    # Exactly one outer fence: two fence lines in the whole response, the first line opening
    # and the last line closing, and therefore nothing outside them. Each conjunct rejects one
    # of the shapes §6.8 leaves as a failure — `fences == 2` the nested and the one-sided
    # fence, the first-and-last test the preamble and the trailing remark.
    if (
        len(lines) >= 3
        and fences == 2
        and _OPEN.match(lines[0].strip())
        and lines[-1].strip() == _CLOSE
    ):
        payload = "\n".join(lines[1:-1])
        if parses(payload):
            return Envelope(received=text, payload=payload, kind=FENCED_ONCE,
                            fence_lines=fences)

    return Envelope(received=text, payload=text, kind=REFUSED, fence_lines=fences)
