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

**`prompt_tokens` is the raw total and the envelope's own total is what checks it.**
`inputTokens + cacheReadInputTokens + cacheWriteInputTokens`, cross-validated against
`totalTokens` and refused on disagreement (`_usage()`, DESIGN §5.4 §11.3, measured 2026-08-16).
Bedrock's `inputTokens` excludes what a cache served, so recording it as `prompt_tokens` would
make a cached arm look like it did less work than an uncached one running the identical loop —
340× less, in the probe, on a call where the model read the same 7,197 tokens. The raw total is
the figure two arms can be compared on; the billed basis is `prompt_tokens - read_tokens`.

**Caching is opt-in per call, and the opt-in is a keyword rather than an inference.**
`invoke(..., cache=True)` splits the prompt at the boundary `assemble_audit_prompt()` recorded
and sends the two content blocks a `cachePoint` sits between; `_audit_fold()` is the only caller
that passes it. Inferring it from the prompt's shape was considered and refused — see
`_content()`.

**No retry parameter, and botocore's transport retries are pinned to one attempt.** §10 A2
fixed format-compliance retries at zero on both arms, before either ran, because no *k* has a
basis and a format failure is itself a reportable result. That argument is about the model's
output, and this is the transport underneath it — but a `max_attempts` of 3 (botocore's
default) would silently make three calls out of one, so `llm_calls` would undercount, the
cost column would be wrong, and a throttled run would differ from an unthrottled one in ways
the record does not show. One call is one call. A caller who wants a second one makes it, and
it appears in the count.

**And the pin did not do that until 2026-08-24, which is the more useful half of this
paragraph.** The config said `retries={"max_attempts": 1}`, and botocore's `max_attempts`
counts *retries on top of* the initial request — "setting this value to 2 will result in the
request being retried at most two times after the initial request", its own documentation says,
and 0 is what means no retries. So the setting that existed to stop one call becoming several
permitted two, and `total_max_attempts` is the key that means what this module claims. The
error was invisible in the only place anyone looked: `test_the_transport_is_pinned_to_one_attempt`
asserted `MAX_ATTEMPTS == 1`, which is a fact about a constant and not about the transport. A
number can be pinned, named, documented, tested and still be handed to the wrong key. The test
now reads the built client's effective config instead.

**What that means for the arms already run is not repairable and is therefore reported.** Every
call before this date — all `port-oneshot` arms and `port-loop` rounds 1–5 — went out under a
transport permitting 2 attempts. `llm_calls` counts calls the loop *made*, which is still
correct as a count of intended inferences; what is not knowable is whether any of them were
retried underneath, because a botocore retry leaves no trace in `agent_calls.jsonl`. So for
those rounds the HTTP request count is a lower bound and the token figures may omit a retried
attempt's share. No correction is possible after the fact, and inventing one would be worse
than the gap.

**The read timeout is set explicitly, and the reason it is worth a paragraph is that it was
not.** The paragraph above reasons about `max_attempts` because 3 was the wrong default; the
timeout sat at botocore's default of 60s and was never a decision at all, which is how it came
to end an arm. `port-loop` round 5's RuleAuthor call timed out twice at 60s on 2026-08-24 after
that call had taken 37.0s, 39.8s, 51.0s and 56.1s in rounds 1–4 — a monotone climb, because
each round's prompt carries the previous rule file and each reply must restate it. So the
transport's patience was a hidden ceiling on the number of rounds an arm could run, sitting
below the pre-registered ceiling of 8 and invisible until it fired. With `MAX_ATTEMPTS = 1`
there is no second chance: the timeout discarded the 250 Auditor calls the round had already
paid for. `READ_TIMEOUT_SECONDS` carries the projection it was chosen against.

The general lesson, which is why this is here and not only at the constant: **an inherited
default is not a decision, and the ones that bound an experiment are worth finding before they
fire.** `max_attempts` was audited because it would have corrupted a number that gets
published. This one corrupted nothing and merely stopped the work, which is why nobody looked
at it — and it still cost a round.

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
from .prompt import CACHE_TTL, FilledPrompt

#: The response field paths asked for on every call. `/model` is Anthropic's own field,
#: which Bedrock's envelope drops unless it is requested by path. Asked for always rather
#: than optionally: a caller who could turn it off is a caller who could stop noticing a
#: mismatch, and the measurement it enables costs nothing.
MODEL_FIELD_PATHS = ("/model",)

#: Transport attempts. One, and named here so the reason is attached to the number
#: rather than living in a config dict — see the module docstring.
#:
#: Passed as botocore's `total_max_attempts`, which counts the initial request, and *not*
#: as `max_attempts`, which counts retries on top of it. That distinction was got wrong
#: here until 2026-08-24: `retries={"max_attempts": 1}` permitted two HTTP attempts per
#: call, which is the exact undercount the module docstring says the pin exists to
#: prevent. `total_max_attempts=1` is what "one call is one call" actually spells.
MAX_ATTEMPTS = 1

#: How long a single `converse` may take to answer. Set explicitly because the value
#: this replaces was botocore's inherited default of 60s, and that default ended a
#: round: `port-loop` round 5's RuleAuthor call timed out twice at 60s on 2026-08-24,
#: after the same call took 37.0s, 39.8s, 51.0s and 56.1s in rounds 1 through 4. The
#: growth is structural rather than incidental — each round's §1.2 carries the previous
#: round's whole rule file and the reply must restate it plus the new rules, so response
#: size rises every round (7209 → 8738 → 10959 → 11985 chars) and generation time with
#: it. A 60s ceiling therefore terminated the arm four rounds below its pre-registered
#: ceiling of 8, for a reason that is a property of the transport and not of the loop.
#:
#: 300s is chosen against the projection rather than the observation: +6s per round
#: from round 4 puts rounds 5–8 at roughly 62/68/74/80s, so this is about 4× the worst
#: case an eight-round arm can reach. Generous on purpose and for the same reason
#: `DEFAULT_MAX_TOKENS` is — with `MAX_ATTEMPTS = 1` a timeout is fatal to the round and
#: costs the 250 Auditor calls already spent, so the asymmetry is total: too high wastes
#: wall time on a call that was going to fail anyway, too low discards a round's work.
#: The Auditor calls are nowhere near it (mean 4.3s, max 22.5s over 1000 calls).
#:
#: This is a change to the transport and not to the call: no prompt byte moves, so it is
#: not a window file (§6.3) and rounds either side of it are the same arm.
READ_TIMEOUT_SECONDS = 300

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

#: The `modelLifecycle` fields `model_lifecycle()` keeps, plus the two identity fields that
#: say which record they came from. A closed list rather than the whole `GetFoundationModel`
#: response: the rest is capability description (modalities, streaming support) that belongs
#: to a model card and not to a run record, and a record that grows whenever AWS adds a field
#: is a record whose diff between two runs is unreadable.
LIFECYCLE_FIELDS = ("model_arn", "model_name", "status", "start_of_life_time")

#: What `model_lifecycle()` puts in `status` when the probe could not answer. Explicit, for
#: `model_id_absent`'s reason (DESIGN §4): "we did not look" and "we looked and the platform
#: had nothing to say" are different facts, and only the second is a measurement. A null
#: nobody wrote cannot be told from a null that was measured.
LIFECYCLE_UNAVAILABLE = "unavailable"


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
    call site. **`prompt_tokens` is the raw total** — `_usage()`'s docstring is where that is
    argued and DESIGN §11.3 is where its consequence for the 1.9× standard is.

    `cache_boundary` and `cache_ttl` are `None` on a call made without a `cachePoint`, and
    that is the record of "caching was not used": the whole `caching` block is then absent from
    `metrics.json` rather than present with zeros (DESIGN §5.4). `cache_read_tokens` and
    `cache_write_tokens` are 0 on such a call too, but they are not what says so — a cached call
    served cold also reads 0, and those are different facts.

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
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cache_boundary: str | None = None
    cache_ttl: str | None = None

    def cost(self) -> dict:
        """This call as a `scorer.REQUIRED_COST` block: one call, measured tokens, time.

        `llm_calls` is 1 because this type is one call. A caller summing several
        responses adds these dicts; nothing here guesses at a total it did not make.

        The cache counts are **not** here, and the omission is structural rather than an
        oversight: `REQUIRED_COST` is closed on both sides (`src/eval/scorer.py`), so a fifth
        key would be refused by `sum_costs()`. They travel in `caching()` instead, which is the
        separation DESIGN §11.3 asks for — the comparable figure in the cost block, the
        transport's contribution beside it.
        """
        return {
            "llm_calls": 1,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "wall_seconds": round(self.wall_seconds, 3),
        }

    def caching(self) -> dict | None:
        """The `caching` block for this call, or `None` if the call was not cached.

        `None` and not a block of zeros. DESIGN §5.4: absence of the block is how absence of
        caching is recorded, because a block reading `read_tokens: 0` is what a *cached* call
        served cold looks like, and an arm that never cached would then be indistinguishable
        from an arm whose cache never hit — which is the difference between "we did not try"
        and "we tried and it did not work", the distinction `model_id_absent` and
        `LIFECYCLE_UNAVAILABLE` exist for elsewhere in this file.

        `enabled` is `True` whenever the block exists, which reads redundant and is not: the
        block is nested inside a round's record, and a reader holding one round cannot see that
        another round has no block at all. The key states the fact locally.
        """
        if self.cache_boundary is None:
            return None
        return {
            "enabled": True,
            "boundary": self.cache_boundary,
            "ttl": self.cache_ttl,
            "read_tokens": self.cache_read_tokens,
            "write_tokens": self.cache_write_tokens,
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
    """A `bedrock-runtime` client with retries pinned to one attempt and an explicit read timeout.

    Both numbers are named constants because both defaults were wrong for this use in
    opposite directions: botocore retries 3 times, which would make `llm_calls` lie, and
    it waits 60s, which ended `port-loop` round 5 four rounds short of its ceiling. The
    connect timeout is left at the default deliberately — an unreachable endpoint should
    fail fast, and it is a different failure from a model that is still writing.

    `boto3` is imported here rather than at module scope so that importing this module —
    which the tests and the AST checks do — needs no AWS dependency at all.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(retries={"total_max_attempts": MAX_ATTEMPTS, "mode": "standard"},
                      read_timeout=READ_TIMEOUT_SECONDS),
    )


def _control_client(region: str | None):
    """A `bedrock` (control-plane) client, for `model_lifecycle()` only.

    A different service from `bedrock-runtime` and therefore a different client: `converse`
    is on the runtime and `GetFoundationModel` is not. Retries are left at botocore's default
    here, unlike `_client()`, and the difference is the point — `MAX_ATTEMPTS = 1` exists so
    that `llm_calls` counts calls truthfully, and this endpoint makes no inference, consumes
    no tokens and appears in no cost block. Pinning it to one attempt would trade a metadata
    field for nothing.
    """
    import boto3

    return boto3.client("bedrock", region_name=region)


def model_lifecycle(model_id: str, *, region: str | None = None,
                    client: Any | None = None) -> dict:
    """`GetFoundationModel`'s lifecycle block for one id, or an explicit unavailable record.

    **This does not resolve an alias, and saying so is the whole reason the field is named
    the way it is.** `startOfLifeTime` is when the *id* first appeared, not which weights
    serve it today — measurement 4 of `docs/notes/baseline-model-family.md` establishes that,
    and `GetInferenceProfile` closes the other route. So this adds a timestamp about an
    identifier and no information about a model. It is recorded because the timestamp is
    genuinely useful for ordering ("was the snapshot this arm called already published when
    the arm ran") and because it costs one API call, and it is recorded under a name that
    cannot be read as an identity claim.

    That naming is not cosmetic. `tests/mutations/README.md` writes up a comment in this file
    that asserted a measurement as though it were a platform property; the sixth of that
    family. A field called `model_resolved` or `weights_id` here would be the same defect
    reintroduced as *data*, where it would reach `metrics.json` and a reader who never opens
    this file. Hence `LIFECYCLE_FIELDS`, and hence the docstring saying what it is not.

    **Never raises, and that is deliberate.** This is a supplementary record, so a probe
    failure must not be able to stop a call that is otherwise ready to make — the arm's one
    call is unrepeatable (DESIGN §6.3) and losing it to a metadata lookup would be the
    tail wagging the dog. Every failure returns `status` of `LIFECYCLE_UNAVAILABLE` with the
    exception's type name, which is more than a null and less than a guess.

    **Two ids, and the conversion is measured rather than assumed** (2026-08-11).
    `GetFoundationModel` refuses an inference-profile id: `us.anthropic.claude-opus-4-5-…`
    raises `ResourceNotFoundException` and the bare `anthropic.claude-opus-4-5-…` succeeds.
    The region prefix is stripped with the same three-prefix list `_resolution()` uses, so
    the two functions cannot disagree about what the prefix is.
    """
    bare = model_id.split(".", 1)[1] if model_id.startswith(("us.", "eu.", "apac.")) \
        else model_id
    try:
        bedrock = client if client is not None else _control_client(region)
        details = bedrock.get_foundation_model(modelIdentifier=bare)["modelDetails"]
        lifecycle = details.get("modelLifecycle") or {}
        start = lifecycle.get("startOfLifeTime")
        return {
            "model_arn": details.get("modelArn"),
            "model_name": details.get("modelName"),
            "status": lifecycle.get("status") or LIFECYCLE_UNAVAILABLE,
            # Stringified here rather than left as botocore's `datetime`, because this dict
            # is written to JSON by three callers and a type that needs a custom encoder is
            # a type one of them will forget to give one.
            "start_of_life_time": start.isoformat() if hasattr(start, "isoformat")
            else start,
        }
    except Exception as exc:                                        # noqa: BLE001
        # Bare `Exception` on purpose. The failure modes are botocore's `ClientError`, a
        # credentials error, a missing key in a changed envelope and an ImportError with no
        # boto3 installed, and every one of them means the same thing here: no metadata, and
        # the call proceeds. Naming the subset would leave the next new failure raising out
        # of a supplementary probe into an arm that was ready to run.
        return {
            "model_arn": None,
            "model_name": None,
            "status": LIFECYCLE_UNAVAILABLE,
            # The type name and never `str(exc)`. A botocore message can carry the request
            # it failed on, and this dict is written to `agent_calls.jsonl` — CLAUDE.md's
            # rule about what goes into a log applies to an exception's text whatever the
            # exception is about.
            "start_of_life_time": None,
            "probe_error": type(exc).__name__,
        }


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


def _cache_tokens(usage: Mapping[str, Any]) -> tuple[int, int]:
    """`(read_tokens, write_tokens)` from a `usage` block, zero when the keys are absent.

    Zero here and **absent** in the record are different things, and this function is the
    reason they can be told apart. A call made without a `cachePoint` gets no cache keys at
    all; a call made with one and served cold gets `cacheWriteInputTokens` and no read. Both
    read as 0 in the arithmetic below — the raw total is right either way — but only the second
    is a measurement, and `metrics.json` records the difference by omitting the whole `caching`
    block when caching was not used (DESIGN §5.4).
    """
    # `.get(key, 0)` on the two token counts and **not** on `cacheDetails`. The counts are
    # quantities that a call without a `cachePoint` genuinely has none of, and 0 is their
    # arithmetic identity in the raw total. `cacheDetails` is a *record* — measured 2026-08-16:
    # it appears on the write call as `[{"ttl": "5m", "inputTokens": 7172}]` and the key is
    # missing entirely on the read, not empty — so `_cache_details_ttl()` returns `None` for it
    # rather than a zero-shaped stand-in. An absent record and a record of nothing are
    # different, which is `LIFECYCLE_UNAVAILABLE`'s distinction one field over.
    read = usage.get("cacheReadInputTokens", 0)
    write = usage.get("cacheWriteInputTokens", 0)
    if not isinstance(read, int) or isinstance(read, bool) or \
            not isinstance(write, int) or isinstance(write, bool):
        raise BedrockError(
            "the response's `usage` block has a non-integer cache count "
            f"({type(read).__name__}, {type(write).__name__}). These are summed into "
            "`prompt_tokens`, so a value that is not a token count is refused rather than "
            "coerced."
        )
    if read < 0 or write < 0:
        raise BedrockError(
            f"the response reports {read} cache-read and {write} cache-write tokens, and a "
            "negative token count is not a measurement."
        )
    return read, write


def _usage(response: Mapping[str, Any]) -> tuple[int, int, int, int]:
    """`(prompt_tokens, completion_tokens, cache_read, cache_write)` from `usage`.

    Raises if the block is absent or either required count is missing. Not estimated, and not
    defaulted to zero: a zero would be a measurement claiming no tokens were consumed,
    and it would sit in the cost column beside real ones.

    **`prompt_tokens` is the raw total: `inputTokens + cacheRead + cacheWrite`** (DESIGN §5.4,
    §11.3, measured 2026-08-16). Bedrock's `inputTokens` *excludes* what a cache served, so on
    a cache read it is the size of the tail block and not of the prompt — 21 against 7193 in the
    probe, a factor of 340 with the model having read the same text both times. Recording that
    figure as `prompt_tokens` would put a transport optimisation into the column §11.3's 1.9×
    standard is read off, and a cached arm would appear to have done less work than an uncached
    one running the identical loop. The raw total does not move when transport changes, which is
    what makes two arms comparable; the billed basis is recoverable as `prompt_tokens -
    read_tokens` from the `caching` block.

    **The assembled figure is cross-checked against `totalTokens`, and a disagreement is
    refused.** The envelope publishes the same quantity by two independent paths — the three
    components, and its own total — and in the probe `totalTokens` was 7197 on all three calls
    while the components moved between them. That is the whole value of the measurement: two
    paths to one number means the arithmetic here can be *checked* rather than asserted. If
    Bedrock changes what any of these keys mean, this is where it surfaces, on the first call,
    instead of arriving as a cost column nobody can reproduce.
    """
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        raise BedrockError(
            "the response carries no `usage` block, so the token counts cannot be "
            "recorded. They are not estimated here — a locally counted token is a "
            "different number from a billed one, and the cost column has to mean one "
            "thing (CLAUDE.md)."
        )
    input_tokens = usage.get("inputTokens")
    completion_tokens = usage.get("outputTokens")
    if not isinstance(input_tokens, int) or not isinstance(completion_tokens, int):
        raise BedrockError(
            "the response's `usage` block is missing `inputTokens` or `outputTokens`. "
            f"Present keys: {sorted(usage)}. A partial cost block is refused rather than "
            "completed by inference."
        )
    read, write = _cache_tokens(usage)
    prompt_tokens = input_tokens + read + write

    total = usage.get("totalTokens")
    if not isinstance(total, int) or isinstance(total, bool):
        raise BedrockError(
            "the response's `usage` block carries no integer `totalTokens`. It is the second "
            f"path to a number this client also assembles (present keys: {sorted(usage)}), and "
            "the cross-check is the only thing that would catch Bedrock redefining one of the "
            "three components (DESIGN §5.4, measured 2026-08-16). Without it the cost column "
            "would be a single unverified sum."
        )
    if prompt_tokens + completion_tokens != total:
        raise BedrockError(
            f"the response's token counts do not agree with its own total: "
            f"inputTokens {input_tokens} + cacheRead {read} + cacheWrite {write} + "
            f"outputTokens {completion_tokens} = {prompt_tokens + completion_tokens}, and "
            f"totalTokens says {total}. Refused rather than recorded under either figure — "
            "these are two independent paths to one quantity (measured 2026-08-16: 7197 by "
            "both paths on a control, a cache write and a cache read), so a disagreement means "
            "a component no longer means what this client sums it as, and the cost block "
            "§11.3 is read off would be wrong in an unknown direction."
        )
    return prompt_tokens, completion_tokens, read, write


def _reported_ttl(response: Mapping[str, Any]) -> str | None:
    """The TTL Bedrock reported in `cacheDetails`, or `None` if it said nothing.

    **`None` means the response did not mention a TTL, and it is never a stand-in for one.**
    Measured 2026-08-16: `cacheDetails` is `[{"ttl": "5m", "inputTokens": 7172}]` on the call
    that *wrote* the cache and the key is absent — not empty — on the calls that read it. So a
    round's 250 calls report a TTL once and stay silent 249 times, and silence is the expected
    case rather than a fault.

    Read at all because it is the one place the platform states the lifetime this project
    declares (`config/naming.yaml` `caching_ttl`), which makes the declared value checkable
    against the reported one exactly when the reported one exists. `invoke()` refuses a
    disagreement; it does not refuse silence.
    """
    details = response.get("usage", {}).get("cacheDetails")
    if not isinstance(details, list) or not details:
        return None
    ttls = {entry["ttl"] for entry in details
            if isinstance(entry, Mapping) and isinstance(entry.get("ttl"), str)}
    if len(ttls) != 1:
        # Two TTLs in one response would mean two cache points with different lifetimes, which
        # this client never sends: `CacheBlocks.blocks()` emits exactly one `cachePoint`. Read as
        # "the platform said something this client cannot reconcile" rather than picking one.
        raise BedrockError(
            f"the response's `cacheDetails` reports {len(ttls)} distinct TTLs for a request "
            "carrying one cachePoint. One block boundary produces one lifetime; a response "
            "describing more is an envelope this client does not understand."
        )
    return ttls.pop()


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


def _content(prompt: FilledPrompt, cache: bool) -> tuple[list[dict], str | None, str | None]:
    """The `converse` content list, and the boundary this call was cached at.

    **One block unless `cache=True`, and the decision is the caller's rather than the prompt's**
    (DESIGN §5.4, decided 2026-08-18). The alternative was for this function to cache whenever
    the reference form carries a `cache_after`, which needs no keyword and no call site kept in
    step. It is refused because caching would then begin the moment *any* assembler grows a
    boundary — the mutation "the RuleAuthor prompt is split too" arriving as an omission rather
    than an edit, with no line to review and §4's byte-identical claim about round 1 failing
    silently. `tests/mutations/run.py` carries the inference form as a mutation so that this
    argument is enforced rather than recorded.

    The other direction is refused here too: `cache=True` on a prompt whose reference form has
    no boundary raises. `assemble_audit_prompt()` is the only producer of the offset, so a
    caller asking for a cached call on some other prompt is a caller that would have this module
    inventing one.
    """
    if not cache:
        return [{"text": prompt.for_transport()}], None, None

    reference = prompt.reference()
    missing = [key for key in ("cache_after", "cache_boundary") if key not in reference]
    if missing:
        raise BedrockError(
            f"cache=True was passed for a prompt whose reference form carries no {missing}. "
            "The boundary is computed by the one function that joined the prompt's pieces "
            "(src/llm/prompt.py, assemble_audit_prompt) and this module does not compute one: a "
            "transport that found its own boundary would be a second place the masked document "
            "could end up on the cached side (docs/prompts/auditor.md §6, DESIGN §5.4)."
        )
    blocks = prompt.for_transport_blocks(
        cache_after=reference["cache_after"],
        boundary=reference["cache_boundary"],
        ttl=CACHE_TTL,
    )
    record = blocks.reference()
    return blocks.blocks(), record["boundary"], record["ttl"]


def invoke(
    prompt: FilledPrompt,
    *,
    model_id: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    region: str | None = None,
    client: Any | None = None,
    cache: bool = False,
) -> Response:
    """Send one prompt to one model and return one `Response`.

    `prompt` is a `FilledPrompt` and `for_transport()` is called here, once. `model_id` is
    keyword-only and has no default, so no rung's model is decided inside this file.

    `client` exists for the tests, which must be able to exercise the response handling
    without an AWS call. It is not a caller-facing knob — a caller passing one is
    responsible for its retry configuration, which is why the default path constructs its
    own with `MAX_ATTEMPTS`.

    **`cache` defaults to False and only `_audit_fold()` passes True** (DESIGN §5.4, §11.3).
    The default is the uncached path because that is the path every existing arm took and
    round 1's RuleAuthor prompt must keep taking byte for byte (§4). See `_content()` for why
    this is a keyword rather than something inferred from the prompt. A cached call sends the
    same bytes in two content blocks with a `cachePoint` between them, and `Response.caching()`
    records what was retained; an uncached call's `caching()` is `None`, which is how absence of
    caching is recorded rather than as a block of zeros.

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
    if not isinstance(cache, bool):
        raise BedrockError(
            f"cache must be a bool, got {type(cache).__name__}. It decides whether a third "
            "party retains part of this prompt for five minutes, and a truthy string is not a "
            "decision anyone made."
        )

    _require_logging_check()
    runtime = client if client is not None else _client(region)
    content, boundary, ttl = _content(prompt, cache)

    started = time.monotonic()
    response = runtime.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": content}],
        inferenceConfig={"maxTokens": max_tokens},
        additionalModelResponseFieldPaths=list(MODEL_FIELD_PATHS),
    )
    elapsed = time.monotonic() - started

    prompt_tokens, completion_tokens, read, write = _usage(response)
    reported_ttl = _reported_ttl(response)
    if reported_ttl is not None and ttl is not None and reported_ttl != ttl:
        # The declared lifetime and the reported one are the same vocabulary
        # (`config/naming.yaml` `caching_ttl`), which is what makes them comparable at all.
        # Refused rather than recorded: the record would name a lifetime the service did not
        # grant, and `_reported_ttl`'s silence already covers the ordinary case where the
        # platform says nothing.
        raise BedrockError(
            f"this call declared a {ttl} cache lifetime and Bedrock reported {reported_ttl!r} "
            "in `cacheDetails`. The declared value is config/naming.yaml's and the reported "
            "one is the platform's; recording the first while the second held would make "
            "metrics.json state a lifetime nothing granted."
        )
    if not cache and (read or write):
        # No `cachePoint` was sent, so no cache could have been read or written. If the envelope
        # says otherwise, the request this client thinks it made is not the request that was
        # served — and the uncached arms' cost columns are what that would corrupt.
        raise BedrockError(
            f"a call sent without a cachePoint reports {read} cache-read and {write} "
            "cache-write tokens. The request carried one content block, so the response "
            "describes a call this client did not make."
        )
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
        cache_read_tokens=read,
        cache_write_tokens=write,
        cache_boundary=boundary,
        cache_ttl=ttl,
    )
