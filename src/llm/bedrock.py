"""The one path from a `FilledPrompt` to a model, and the things it refuses to do.

**One entry point.** `invoke(prompt, model_id=...)` takes a `FilledPrompt` — not a `str` —
and returns a `Response`. The type is the argument type rather than a convention because
§5.4 already decided that: a caller holding the text has already lost the protection, so the
transport is handed the object and calls `for_transport()` itself, once, at the moment of the
call.

**Nothing here logs the prompt or the completion.** No `logging` import, no `print`, no
file write, and `tools/check_bedrock_logging.py` covers the half of the same problem that
lives in the account rather than in this file — Bedrock's own model-invocation logging, which
persists full prompts and completions to S3 or CloudWatch if it is enabled. Both halves are
the same exposure and only one of them is visible from inside the code.

**The logging gate.** `invoke()` refuses to call unless `compliance.md` §3 records a check
for the current date. This inverts the usual order deliberately: the gate exists before the
first call, so the first call is blocked until the record is produced. A check whose result
is a terminal line nobody kept is not evidence, and a re-check instruction in prose is the
kind of control DESIGN §5.4 measured the cost of.

**Tokens come from the `usage` block and are never estimated.** `usage` is what the provider
charged, and a local tokeniser is a different number computed by different code — CLAUDE.md
requires cost reported beside quality, and a cost column mixing measured and estimated values
compares two arms on two definitions. A response without `usage` is refused rather than
filled in.

**No retry parameter, and botocore's transport retries are pinned to one attempt.** §10 A2
fixed format-compliance retries at zero on both arms, before either ran, because no *k* has a
basis and a format failure is itself a reportable result. That argument is about the model's
output, and this is the transport underneath it — but a `max_attempts` of 3 (botocore's
default) would silently make three calls out of one, so `llm_calls` would undercount, the
cost column would be wrong, and a throttled run would differ from an unthrottled one in ways
the record does not show. One call is one call. A caller who wants a second one makes it, and
it appears in the count.

**`model_id` is a required keyword argument with no default.** Every rung's model is passed
from the top (`src/orchestrate.py`) so that A2's two-family comparison is a parameter and not
a code path. A default here would be the one place the parameter could stop being one.

**What the response records about which model answered.** Measured 2026-08-08
(`docs/notes/baseline-model-family.md`): a `converse` response does not name the model it was
served by. `additionalModelResponseFieldPaths=["/model"]` does return one, but it is never
more specific than the request — a dated id comes back dated, an undated alias comes back
undated. The field is still read on every call, because a *disagreement* between what was
asked for and what answered is a finding, and not reading the field is how that finding is
missed. `Response.model_id_resolution` says how far the identification got, in
`naming.yaml`'s closed vocabulary; a `mismatch` is refused.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

from ..corpora.base import CorpusError, check_model_resolution
from .prompt import FilledPrompt

#: The response field paths asked for on every call. `/model` is Anthropic's own field,
#: which Bedrock's envelope drops unless it is requested by path. Asked for always rather
#: than optionally: a caller who could turn it off is a caller who could stop noticing a
#: mismatch, and the measurement it enables costs nothing.
MODEL_FIELD_PATHS = ("/model",)

#: Transport attempts. One, and named here so the reason is attached to the number
#: rather than living in a config dict — see the module docstring.
MAX_ATTEMPTS = 1

#: Default output budget. Generous on purpose, and the reason is measured rather than
#: guessed: reasoning tokens are drawn from this same budget (2026-08-08 — at
#: `maxTokens=32` the entire budget went to reasoning and the reply held no text at all).
#: The `port-oneshot` artefact is a whole `rules/{lang}.yaml` file, so the budget has to
#: cover a rule file *plus* however much the model thinks first. Too low does not degrade
#: gracefully — it truncates the artefact, and a truncated rule file is a format failure
#: that §10 A2 would then report as a capability result. That is a wrong number in the
#: paper, so the default errs high; a caller that wants a smaller one passes it.
DEFAULT_MAX_TOKENS = 32768

#: Resolution kinds, spelled once. Validated against `naming.yaml` at use, so a typo here
#: raises rather than being written to metrics.json.
DATED = "dated"
UNRESOLVED = "alias-unresolved"
MISMATCH = "mismatch"


class BedrockError(CorpusError):
    """A call that could not be made, or a response that cannot be recorded.

    `CorpusError` for `PromptError`'s reason: every case means stop and tell a person. A
    response that arrived but cannot be accounted for is not a result — the alternative is
    a metrics.json whose cost block is a guess.
    """


@dataclass(frozen=True)
class Response:
    """One model reply, with everything the record needs and nothing it must not hold.

    `text` is here because the caller has to parse a rule file out of it, and unlike a
    prompt a completion is the model's own words rather than corpus context. That is not a
    licence to log it: a completion echoing part of its prompt would carry the corpus with
    it, so nothing in this module writes it either.

    The token counts are the provider's, from `usage`. `prompt_tokens` and
    `completion_tokens` are named for `scorer.REQUIRED_COST` rather than for Bedrock's
    `inputTokens`/`outputTokens`, so the cost block is assembled by renaming nothing at the
    call site.

    `model_id` is what was asked for; `model_id_reported` is what the response said, or
    `None` if the field did not come back at all. Both are kept, because their agreement is
    the only check available and a single field cannot express it.
    """

    text: str
    model_id: str
    model_id_reported: str | None
    model_id_resolution: str
    prompt_tokens: int
    completion_tokens: int
    wall_seconds: float
    stop_reason: str
    request_id: str

    def cost(self) -> dict:
        """This call as a `scorer.REQUIRED_COST` block: one call, measured tokens, time.

        `llm_calls` is 1 because this type is one call. A caller summing several
        responses adds these dicts; nothing here guesses at a total it did not make.
        """
        return {
            "llm_calls": 1,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "wall_seconds": round(self.wall_seconds, 3),
        }

    def model_record(self) -> dict:
        """The `run`-block fields describing which model answered.

        Three fields rather than one, for the reason `docs/notes/baseline-model-family.md`
        gives: the alias is what can be recorded, and the fact that it is only an alias is
        the part a reader six months from now needs.
        """
        return {
            "model_id": self.model_id,
            "model_id_reported": self.model_id_reported,
            "model_id_resolution": self.model_id_resolution,
        }


def _client(region: str | None):
    """A `bedrock-runtime` client with transport retries pinned to one attempt.

    `boto3` is imported here rather than at module scope so that importing this module —
    which the tests and the AST checks do — needs no AWS dependency at all.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(retries={"max_attempts": MAX_ATTEMPTS, "mode": "standard"}),
    )


def _require_logging_check() -> None:
    """Refuse to call unless today's Bedrock-logging check is on record.

    Imports the tool rather than re-reading `compliance.md` here. Two readers of the same
    file are two parsers of the same format, and the `check_rules`/`run_fold` merge was
    about exactly that: the tool decides what counts as a record, and this asks it.
    """
    import importlib.util
    from pathlib import Path

    tool = Path(__file__).resolve().parents[2] / "tools" / "check_bedrock_logging.py"
    if not tool.exists():
        raise BedrockError(
            f"the Bedrock logging gate is missing ({tool.name} not found). It is what "
            "records that model-invocation logging was off, and a call without that "
            "record cannot be defended under the DUA (docs/notes/compliance.md §3)."
        )
    spec = importlib.util.spec_from_file_location("_check_bedrock_logging", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not module.checked_today():
        raise BedrockError(
            "refusing to call Bedrock: no model-invocation logging check is recorded for "
            "today. If logging is enabled, Bedrock persists the full prompt — which "
            "carries dev-fold corpus context — to S3 or CloudWatch in this account "
            "(docs/notes/compliance.md §1, §3). It is a mutable account setting, so "
            "yesterday's check is evidence about yesterday.\n"
            "  run: python3 tools/check_bedrock_logging.py"
        )


def _usage(response: Mapping[str, Any]) -> tuple[int, int]:
    """`(prompt_tokens, completion_tokens)` from the response's `usage` block.

    Raises if the block is absent or either count is missing. Not estimated, and not
    defaulted to zero: a zero would be a measurement claiming no tokens were consumed,
    and it would sit in the cost column beside real ones.
    """
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        raise BedrockError(
            "the response carries no `usage` block, so the token counts cannot be "
            "recorded. They are not estimated here — a locally counted token is a "
            "different number from a billed one, and the cost column has to mean one "
            "thing (CLAUDE.md)."
        )
    prompt_tokens = usage.get("inputTokens")
    completion_tokens = usage.get("outputTokens")
    if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
        raise BedrockError(
            "the response's `usage` block is missing `inputTokens` or `outputTokens`. "
            f"Present keys: {sorted(usage)}. A partial cost block is refused rather than "
            "completed by inference."
        )
    return prompt_tokens, completion_tokens


def _text(response: Mapping[str, Any]) -> str:
    """The reply text, concatenated over the content blocks that carry text.

    A reply is a *list* of blocks and only some of them are text. Measured 2026-08-08: this
    model returns `reasoningContent` followed by `text`, so taking `content[0]["text"]` —
    the obvious reading of the shape — raises `KeyError` on a perfectly good response. The
    reasoning blocks are dropped rather than concatenated: what the caller parses a rule
    file out of is the answer, and a model's thinking is neither part of the artefact nor
    something this module has any reason to carry further.

    Raises on a reply with no text at all, and distinguishes *why*. An empty string would
    be indistinguishable from a model that answered with nothing, and those are different
    results.
    """
    message = response.get("output", {}).get("message", {})
    blocks = message.get("content")
    if not isinstance(blocks, list):
        raise BedrockError(
            "the response has no `output.message.content` list; its shape is not one this "
            f"client understands (top-level keys: {sorted(response)})."
        )
    parts = [b["text"] for b in blocks if isinstance(b, Mapping) and "text" in b]
    if not parts:
        # Truncation is named separately because it is the one cause with an action. It was
        # met on the first real call: at `maxTokens=32` the whole budget went to reasoning,
        # `stopReason` came back `max_tokens`, and the reply held one `reasoningContent`
        # block and no text. Reported as an unrecognised shape, that reads as a client bug.
        if response.get("stopReason") == "max_tokens":
            raise BedrockError(
                "the model hit the token limit before emitting any text — the whole budget "
                "went to reasoning, so the reply carries reasoning blocks only. Raise "
                "`max_tokens`. This is not a retry: nothing about the call is repeated on "
                "the same budget (DESIGN §10 A2 fixes format retries at zero, and this is "
                "a limit the caller sets rather than an outcome to retry past)."
            )
        kinds = sorted({k for b in blocks if isinstance(b, Mapping) for k in b})
        raise BedrockError(
            f"the response's {len(blocks)} content block(s) carry no text "
            f"(block kinds: {kinds}, stopReason: {response.get('stopReason')!r}). No "
            "completion text is quoted in this message."
        )
    return "".join(parts)


def _resolution(requested: str, reported: str | None) -> str:
    """How far the model identification got. Raises on a mismatch.

    The comparison strips the cross-region inference-profile prefix, because that is the
    one difference measured to be systematic: `us.anthropic.claude-opus-5` is answered by
    `claude-opus-5`. Anything else that differs is a genuine disagreement — the response
    naming a model other than the one asked for — and it is refused. A call that succeeded
    while nobody can say which model answered is not a result an experiment can use.

    `dated` versus `alias-unresolved` is decided on the requested id. See the comment at
    the decision itself for what that rests on and what it does not.
    """
    if reported is None:
        # The field was asked for and did not come back. Recorded as unresolved rather
        # than as a mismatch — nothing disagreed, the platform simply said less than it
        # was asked to. Treating silence as a mismatch would block a run over a shape
        # change in someone else's envelope.
        return check_model_resolution(UNRESOLVED)

    bare = requested.split(".", 1)[1] if requested.startswith(("us.", "eu.", "apac.")) \
        else requested
    # Bedrock's provider prefix and version suffix are envelope, not identity:
    # `anthropic.claude-opus-4-5-20251101-v1:0` is reported as `claude-opus-4-5-20251101`.
    # `rsplit` rather than `split`, because the suffix is at the end — a model whose name
    # contains `-v` elsewhere would otherwise be truncated to its first component.
    without_provider = bare.split(".", 1)[1] if "." in bare else bare
    without_version = without_provider.rsplit("-v", 1)[0] if "-v" in without_provider \
        else without_provider

    if reported not in (requested, bare, without_provider, without_version):
        raise BedrockError(
            f"the response reports model {reported!r} for a request naming "
            f"{requested!r}. The call succeeded, but the record cannot say which model "
            "produced the output, so it is refused rather than written "
            f"(config/naming.yaml model_id_resolution: {MISMATCH})."
        )

    # A snapshot date is what makes an id reproducible, and an eight-digit run is how a
    # dated Bedrock id carries one. Read off the requested id, and the reason that is
    # enough is **a measurement from 2026-08-08 and not a property of the platform**:
    # three ids sent that day came back with the date preserved when it was asked for and
    # never added when it was not (`docs/notes/baseline-model-family.md` §"측정 결과" 3).
    # A response that resolved an alias to a snapshot would be recorded
    # `alias-unresolved` while being resolvable, and nothing here would notice.
    #
    # **That measurement's sibling was already wrong once.** `_text`'s first version took
    # the reply off `blocks[0]` on the same kind of reading of the same envelope, and it
    # failed on the first real call because this model returns `reasoningContent` first
    # (mutation `the_reply_text_is_taken_from_the_first_block`). So the claim is written
    # here as dated and unchecked rather than as settled.
    #
    # **The measurement is not testable here; the property that makes its failure loud
    # is, and it is tested.** No fake response can establish what Bedrock does, but the
    # refusal three lines up decides what happens if the measurement stops holding:
    # `reported` is accepted only when it equals `requested` or one of three *strippings*
    # of it — prefix, provider, version suffix — and stripping cannot add a component. So
    # a response resolving an alias to a snapshot is not a quiet `alias-unresolved`, it is
    # a `mismatch` and the run stops. `test_no_accepted_report_adds_a_date_the_request_did_
    # not_have` enumerates the accept set and asserts exactly that, and
    # `test_a_response_that_adds_a_date_is_a_mismatch_and_not_a_quiet_unresolved` pins the
    # one case.
    #
    # Two things that enumeration turned up, recorded because the tidier claim is wrong.
    # The reverse direction *does* occur: `rsplit("-v", 1)` on a body like
    # `claude-v2-20251101` yields `claude`, so an accepted report can be undated while the
    # request is dated — harmless, since the date is read off the request, but it means
    # the accept set is looser than the stripping list reads. And what stays unchecked is
    # not the mechanism but the *policy*: on the day Bedrock does resolve aliases, this
    # refuses, so a platform improvement arrives as a blocked run. Only a real call
    # surfaces that, which is why the datedness claim is dated in this comment.
    dated = any(part.isdigit() and len(part) == 8 for part in requested.split("-"))
    return check_model_resolution(DATED if dated else UNRESOLVED)


def invoke(
    prompt: FilledPrompt,
    *,
    model_id: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    region: str | None = None,
    client: Any | None = None,
) -> Response:
    """Send one prompt to one model and return one `Response`.

    `prompt` is a `FilledPrompt` and `for_transport()` is called here, once. `model_id` is
    keyword-only and has no default, so no rung's model is decided inside this file.

    `client` exists for the tests, which must be able to exercise the response handling
    without an AWS call. It is not a caller-facing knob — a caller passing one is
    responsible for its retry configuration, which is why the default path constructs its
    own with `MAX_ATTEMPTS`.

    No retry parameter. One call, and the outcome — including a throttle — is the result.
    """
    if not isinstance(prompt, FilledPrompt):
        raise BedrockError(
            "invoke() takes a FilledPrompt and not a string. The type is what keeps the "
            "rendered prompt from being written down (DESIGN §5.4); a caller holding the "
            f"text has already left that guarantee. Got {type(prompt).__name__}."
        )
    if not isinstance(model_id, str) or not model_id:
        raise BedrockError(
            "model_id is required and must be a non-empty string. It is passed from the "
            "top so that A2's two-family comparison is a parameter (DESIGN §10 A2), and a "
            "default here would be the place it stopped being one."
        )

    _require_logging_check()
    runtime = client if client is not None else _client(region)

    started = time.monotonic()
    response = runtime.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt.for_transport()}]}],
        inferenceConfig={"maxTokens": max_tokens},
        additionalModelResponseFieldPaths=list(MODEL_FIELD_PATHS),
    )
    elapsed = time.monotonic() - started

    prompt_tokens, completion_tokens = _usage(response)
    reported = response.get("additionalModelResponseFields", {}).get("model")
    return Response(
        text=_text(response),
        model_id=model_id,
        model_id_reported=reported,
        model_id_resolution=_resolution(model_id, reported),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        wall_seconds=elapsed,
        stop_reason=response.get("stopReason", ""),
        request_id=response.get("ResponseMetadata", {}).get("RequestId", ""),
    )
