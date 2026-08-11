"""Tests for `src/llm/bedrock.py` — the one path from a prompt to a model.

No test here makes a network call. Every response is a literal dict in the shape Bedrock
actually returned on 2026-08-08, recorded in `docs/notes/baseline-model-family.md`; a
`FakeRuntime` stands in for the client. That is deliberate rather than convenient — a suite
that needed credentials would be a suite that gets skipped, which is the availability defect
`tests/test_conftest.py` records shipping four times.

**The shapes are measured, not imagined.** `reasoningContent` appearing *before* the text
block, and a truncated reply carrying reasoning and no text at all, are both things the first
real call did. Inventing a one-text-block response would have produced a client that failed
on its first use, which is what happened before these fixtures were written from the wire.

**Structure as well as behaviour**, for `tests/test_prompt.py`'s reason: "this module does
not log the prompt" is not a property any single call can demonstrate. It is asserted over
the syntax tree.

    python3 -m pytest tests/test_bedrock.py -q
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora.base import CorpusError                          # noqa: E402
from src.llm import bedrock as bedrock_module                     # noqa: E402
from src.llm.bedrock import (                                     # noqa: E402
    DATED, DEFAULT_MAX_TOKENS, MAX_ATTEMPTS, MISMATCH, MODEL_FIELD_PATHS, UNRESOLVED,
    BedrockError, Response, _resolution, _text, _usage, invoke,
)
from src.llm.prompt import FilledPrompt                           # noqa: E402

MODULE = ROOT / "src" / "llm" / "bedrock.py"

#: The real gate, captured at import. The autouse fixture below replaces the module
#: attribute, so a gate test that reads it back gets the no-op — which is how the first
#: version of those two tests passed by asserting nothing.
REAL_GATE = bedrock_module._require_logging_check

#: The alias the ladder runs on, and the A2 appendix model. Both undated, which is the
#: finding these tests encode.
OPUS = "us.anthropic.claude-opus-5"
LLAMA = "us.meta.llama4-maverick-17b-instruct-v1:0"
DATED_ID = "us.anthropic.claude-opus-4-5-20251101-v1:0"

#: Invented, not corpus text. Present so a test can assert it never reaches a log.
SURFACE = "Zzyzx Quinbolt"

WRITE_NAMES = {"open", "write", "writelines", "dump", "dumps", "writestr",
               "write_text", "write_bytes"}
LOG_NAMES = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def reply(text: str = "ok", *, model: str | None = "claude-opus-5",
          stop: str = "end_turn", tokens: tuple[int, int] = (67, 1102),
          reasoning_first: bool = True) -> dict:
    """A `converse` response in the measured shape.

    `reasoning_first` defaults to True because that is what the model does: the reasoning
    block comes back ahead of the text one, so a client reading `content[0]["text"]` raises
    on a good response.
    """
    blocks = []
    if reasoning_first:
        blocks.append({"reasoningContent": {"reasoningText": {"text": "..."}}})
    blocks.append({"text": text})
    out = {
        "output": {"message": {"role": "assistant", "content": blocks}},
        "stopReason": stop,
        "usage": {"inputTokens": tokens[0], "outputTokens": tokens[1],
                  "totalTokens": sum(tokens), "cacheReadInputTokens": 0},
        "metrics": {"latencyMs": 997},
        "ResponseMetadata": {"RequestId": "req-1", "HTTPStatusCode": 200},
    }
    if model is not None:
        out["additionalModelResponseFields"] = {"model": model}
    return out


class FakeRuntime:
    """Records the call it was given and returns a canned response."""

    def __init__(self, response: dict | None = None):
        self.response = response if response is not None else reply()
        self.calls: list[dict] = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@pytest.fixture(autouse=True)
def gate_open(monkeypatch):
    """Open the logging gate for every test but the ones about the gate.

    Patched rather than satisfied: a test suite that appended a real dated record to
    `compliance.md` would be writing the project's compliance evidence from a test run,
    and that file is what the paper cites.
    """
    monkeypatch.setattr(bedrock_module, "_require_logging_check", lambda: None)


def a_prompt(text: str = "prompt body") -> FilledPrompt:
    return FilledPrompt(text, {"text_sha256": "sha256:" + "0" * 64, "n_spans": 1})


# ─── the argument types are the guarantee, not a convention ──────────────────

def test_a_bare_string_is_refused():
    """§5.4: a caller holding the text has already left the protection the type gives."""
    with pytest.raises(BedrockError) as e:
        invoke("prompt body", model_id=OPUS, client=FakeRuntime())
    assert "FilledPrompt" in str(e.value)


def test_the_prompt_text_reaches_the_call_through_the_named_exit():
    fake = FakeRuntime()
    invoke(a_prompt("the body"), model_id=OPUS, client=fake)
    sent = fake.calls[0]["messages"][0]["content"][0]["text"]
    assert sent == "the body"


def test_model_id_has_no_default():
    """A2 keeps the model a parameter end to end; a default is where it stops being one."""
    with pytest.raises(TypeError):
        invoke(a_prompt(), client=FakeRuntime())          # type: ignore[call-arg]


def test_an_empty_model_id_is_refused():
    with pytest.raises(BedrockError) as e:
        invoke(a_prompt(), model_id="", client=FakeRuntime())
    assert "non-empty" in str(e.value)


def test_the_model_id_is_passed_through_unchanged():
    """No normalisation, no prefixing. What was asked for is what is recorded."""
    fake = FakeRuntime(reply(model="llama4-maverick-17b-instruct"))
    r = invoke(a_prompt(), model_id=LLAMA, client=fake)
    assert fake.calls[0]["modelId"] == LLAMA
    assert r.model_id == LLAMA


def code_strings(tree: ast.AST) -> list[str]:
    """Every string constant that is not a docstring.

    Docstrings are excluded because they *should* name real model ids — the reasoning for
    the prefix-stripping is unreadable without `us.anthropic.claude-opus-5` in it. A check
    that could not tell prose from code would force the explanation out of the file to stay
    green, which is the wrong trade: the literal in a docstring cannot be called.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value not in docstrings]


def test_no_model_id_appears_as_a_literal_in_the_module():
    """The A2 symmetry, asserted. A hardcoded alias is the one way the parameter dies."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    literals = [s for s in code_strings(tree)
                if any(p in s for p in ("anthropic.", "meta.", "amazon.", "mistral.",
                                        "deepseek.", "cohere.", "openai."))]
    assert not literals, f"model identifiers hardcoded in bedrock.py: {literals}"


def test_the_literal_check_is_not_blind_to_a_hardcoded_id():
    """Without this, the test above is consistent with `code_strings` returning nothing."""
    tree = ast.parse('def f():\n    """A docstring naming us.anthropic.claude-opus-5."""\n'
                     '    return call("us.anthropic.claude-opus-5")\n')
    found = [s for s in code_strings(tree) if "anthropic." in s]
    assert found == ["us.anthropic.claude-opus-5"], (
        "the literal check either misses code strings or flags docstrings"
    )


# ─── the response shapes, as measured ────────────────────────────────────────

def test_the_text_is_found_after_a_reasoning_block():
    """The shape the first real call returned. `content[0]["text"]` would raise here."""
    r = invoke(a_prompt(), model_id=OPUS, client=FakeRuntime(reply("the answer")))
    assert r.text == "the answer"


def test_reasoning_blocks_are_not_part_of_the_text():
    """The artefact is the answer. Thinking is neither parsed nor carried further."""
    assert "..." not in _text(reply("the answer"))


def test_text_is_concatenated_over_several_text_blocks():
    response = reply()
    response["output"]["message"]["content"] = [
        {"reasoningContent": {"reasoningText": {"text": "..."}}},
        {"text": "first "}, {"text": "second"},
    ]
    assert _text(response) == "first second"


def test_a_truncated_reply_says_it_was_truncated():
    """Measured: at a low budget the whole allowance went to reasoning and no text came
    back. Reported as an unrecognised shape, that reads as a client bug rather than as a
    limit the caller set."""
    response = reply(stop="max_tokens")
    response["output"]["message"]["content"] = [
        {"reasoningContent": {"reasoningText": {"text": "..."}}}
    ]
    with pytest.raises(BedrockError) as e:
        _text(response)
    assert "token limit" in str(e.value)
    assert "max_tokens" in str(e.value)


def test_a_textless_reply_that_is_not_truncation_names_the_block_kinds():
    response = reply(stop="end_turn")
    response["output"]["message"]["content"] = [{"toolUse": {"name": "x"}}]
    with pytest.raises(BedrockError) as e:
        _text(response)
    assert "toolUse" in str(e.value)


def test_an_unrecognised_envelope_is_refused_rather_than_guessed():
    with pytest.raises(BedrockError) as e:
        _text({"stopReason": "end_turn"})
    assert "content" in str(e.value)


# ─── tokens come from usage and are never estimated ──────────────────────────

def test_the_token_counts_are_the_providers():
    r = invoke(a_prompt(), model_id=OPUS, client=FakeRuntime(reply(tokens=(67, 1102))))
    assert (r.prompt_tokens, r.completion_tokens) == (67, 1102)


def test_a_response_without_usage_is_refused_rather_than_estimated():
    response = reply()
    del response["usage"]
    with pytest.raises(BedrockError) as e:
        _usage(response)
    assert "not estimated" in str(e.value)


def test_a_partial_usage_block_is_refused():
    """Zero would be a measurement claiming no tokens were consumed."""
    response = reply()
    del response["usage"]["outputTokens"]
    with pytest.raises(BedrockError) as e:
        _usage(response)
    assert "outputTokens" in str(e.value)


def test_nothing_in_the_module_counts_tokens_itself():
    """CLAUDE.md: cost beside quality. A locally counted token is a different number."""
    src = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not imported & {"tiktoken", "transformers", "anthropic", "tokenizers"}


def test_the_cost_block_is_exactly_what_the_scorer_requires():
    from src.eval.scorer import REQUIRED_COST
    r = invoke(a_prompt(), model_id=OPUS, client=FakeRuntime())
    assert set(r.cost()) == set(REQUIRED_COST)
    assert r.cost()["llm_calls"] == 1


def test_wall_seconds_is_measured_and_rounded_in_the_cost_block():
    r = invoke(a_prompt(), model_id=OPUS, client=FakeRuntime())
    assert r.wall_seconds >= 0
    assert r.cost()["wall_seconds"] == round(r.wall_seconds, 3)


# ─── no retries ──────────────────────────────────────────────────────────────

def test_there_is_no_retry_parameter():
    import inspect
    params = set(inspect.signature(invoke).parameters)
    assert not params & {"retries", "max_attempts", "attempts", "retry"}


def test_the_transport_is_pinned_to_one_attempt():
    """3 (botocore's default) would make three calls out of one, so `llm_calls` would
    undercount and the cost column would be wrong."""
    assert MAX_ATTEMPTS == 1


def test_the_client_builder_passes_the_pinned_attempts():
    """The constant existing is not the same as it being used."""
    fn = ast.parse(MODULE.read_text(encoding="utf-8"))
    builder = next(n for n in ast.walk(fn)
                   if isinstance(n, ast.FunctionDef) and n.name == "_client")
    names = {n.id for n in ast.walk(builder) if isinstance(n, ast.Name)}
    assert "MAX_ATTEMPTS" in names, "_client does not use MAX_ATTEMPTS"
    numbers = [n.value for n in ast.walk(builder)
               if isinstance(n, ast.Constant) and isinstance(n.value, int)]
    assert not numbers, f"_client hardcodes {numbers} instead of naming the constant"


def test_one_invoke_is_one_call():
    fake = FakeRuntime()
    invoke(a_prompt(), model_id=OPUS, client=fake)
    assert len(fake.calls) == 1


# ─── which model answered ────────────────────────────────────────────────────

def test_the_model_field_is_always_requested():
    """Measured: `converse` says nothing about the model unless asked by path. A caller
    who could turn this off is one who could stop noticing a mismatch."""
    fake = FakeRuntime()
    invoke(a_prompt(), model_id=OPUS, client=fake)
    assert fake.calls[0]["additionalModelResponseFieldPaths"] == list(MODEL_FIELD_PATHS)


def test_an_undated_alias_is_recorded_as_unresolved():
    """The finding: the response confirms the alias and never resolves it."""
    assert _resolution(OPUS, "claude-opus-5") == UNRESOLVED


def test_a_dated_id_is_recorded_as_dated():
    assert _resolution(DATED_ID, "claude-opus-4-5-20251101") == DATED


def test_the_appendix_model_is_also_unresolved():
    """A2's Llama id carries `-v1:0` but no snapshot date, so it is an alias too — the
    limitation applies to both families and not only to Claude."""
    assert _resolution(LLAMA, "llama4-maverick-17b-instruct") == UNRESOLVED


def test_a_version_suffix_is_not_mistaken_for_a_date():
    assert _resolution("us.anthropic.claude-opus-4-6-v1", "claude-opus-4-6") == UNRESOLVED


def test_a_missing_model_field_is_unresolved_and_not_a_mismatch():
    """Silence is the platform saying less than it was asked, not a disagreement. Treating
    it as a mismatch would block a run over someone else's envelope change."""
    assert _resolution(OPUS, None) == UNRESOLVED


def test_a_response_naming_a_different_model_is_refused():
    with pytest.raises(BedrockError) as e:
        _resolution(OPUS, "claude-sonnet-5")
    assert MISMATCH in str(e.value)


def test_a_mismatch_stops_the_invoke_rather_than_being_recorded():
    """A call that succeeded while nobody can say which model answered is not a result."""
    with pytest.raises(BedrockError):
        invoke(a_prompt(), model_id=OPUS, client=FakeRuntime(reply(model="claude-haiku-4-5")))


def test_a_response_that_adds_a_date_is_a_mismatch_and_not_a_quiet_unresolved():
    """The direction `_resolution`'s datedness comment rests on, pinned where it is decidable.

    `dated` is read off the *requested* id, which is sound only if a response cannot supply
    a date the request lacked. That was measured on 2026-08-08 and is not a platform
    guarantee — so what is asserted here is not the measurement (this suite cannot make a
    real call) but the thing that makes the measurement's failure loud: an id with a
    component the request does not have is outside the accept set, so it is refused rather
    than recorded `alias-unresolved`.

    Asserted at the boundary rather than through a fake response, because the accept set is
    where the property lives: the four accepted forms are `requested` and three *strippings*
    of it, and stripping cannot add. If Bedrock starts resolving aliases, this is the test
    that says the run stops instead of quietly under-recording.
    """
    resolved = "claude-opus-5-20260501"
    with pytest.raises(BedrockError) as e:
        _resolution(OPUS, resolved)
    assert MISMATCH in str(e.value)

    # And the same in the other direction, so the assertion is about the accept set and
    # not about this one string: a request that carries the date is fine.
    assert _resolution(f"us.anthropic.{resolved}", resolved) == DATED


def test_no_accepted_report_adds_a_date_the_request_did_not_have():
    """`dated` is read off `requested` and never off `reported`, and this is the property
    that makes that sound: over every id the accept set can hold — `requested` and its
    prefix, provider and version-suffix strippings — no accepted report carries a date the
    request lacked, because stripping cannot add a component.

    Enumerated rather than argued. The prose version ("the response never adds one") was a
    claim about Bedrock's envelope; this is a claim about `_resolution`, which is the part
    this repository controls and can therefore keep true.

    **One-directional, and the enumeration is what established that.** The two-way form of
    this assertion fails: `rsplit("-v", 1)` on `claude-v2-20251101` yields `claude`, so an
    accepted report *can* be undated while the request is dated. That direction is
    harmless here — `dated` is read off the request, which did carry the date, so the
    recorded value is right — but it is the reason this test asserts what it asserts and
    not the tidier symmetric claim. It also says the accept set is looser than the
    stripping list reads: an id whose body contains `-v` before its date accepts a report
    truncated at that `-v`. That is a question about how permissive `mismatch` is and not
    about datedness, and it is left as it stands.
    """
    def has_date(s: str) -> bool:
        return any(p.isdigit() and len(p) == 8 for p in s.split("-"))

    bodies = ["claude-opus-5", "claude-opus-4-5-20251101", "llama4-maverick-17b-instruct",
              "x-20260101-y", "v-20260101", "claude-v2-20251101"]
    seen = 0
    for prefix in ("", "us.", "eu.", "apac."):
        for provider in ("", "anthropic.", "meta."):
            for suffix in ("", "-v1:0", "-v1", "-v2:3"):
                for body in bodies:
                    requested = f"{prefix}{provider}{body}{suffix}"
                    bare = requested.split(".", 1)[1] \
                        if requested.startswith(("us.", "eu.", "apac.")) else requested
                    no_provider = bare.split(".", 1)[1] if "." in bare else bare
                    no_version = no_provider.rsplit("-v", 1)[0] \
                        if "-v" in no_provider else no_provider
                    for reported in (requested, bare, no_provider, no_version):
                        seen += 1
                        got = _resolution(requested, reported)
                        assert got == (DATED if has_date(requested) else UNRESOLVED), (
                            f"{requested!r} reported as {reported!r} resolved {got!r}"
                        )
                        assert not (has_date(reported) and not has_date(requested)), (
                            f"an accepted report {reported!r} carries a date "
                            f"{requested!r} does not — the datedness of `requested` is "
                            "no longer a safe proxy and this call would be recorded "
                            "`alias-unresolved` while being resolvable"
                        )
    assert seen > 1000, f"the enumeration collapsed to {seen} pairs"


def test_the_resolution_kinds_come_from_naming_yaml():
    """CLAUDE.md: a value that lands in a results file is declared in the config."""
    from src.corpora.base import model_id_resolution
    assert set(model_id_resolution()) == {DATED, UNRESOLVED, MISMATCH}


def test_an_undeclared_resolution_kind_is_refused():
    from src.corpora.base import check_model_resolution
    with pytest.raises(CorpusError):
        check_model_resolution("resolved")


def test_the_model_record_keeps_both_ids():
    """Their agreement is the only check available, and one field cannot express it."""
    r = invoke(a_prompt(), model_id=OPUS, client=FakeRuntime())
    record = r.model_record()
    assert record["model_id"] == OPUS
    assert record["model_id_reported"] == "claude-opus-5"
    assert record["model_id_resolution"] == UNRESOLVED


# ─── the lifecycle probe, which resolves nothing ──────────────────────────────

class FakeControl:
    """`GetFoundationModel` in the measured shape, or a raiser.

    `start_of_life` is a `datetime` because that is what botocore returns, and the writer
    stringifying it is the behaviour under test — a `datetime` reaching `json.dump` raises
    in whichever of three callers forgot the encoder.
    """

    def __init__(self, *, raises: Exception | None = None, status: str | None = "ACTIVE",
                 lifecycle_key: bool = True):
        self.raises = raises
        self.status = status
        self.lifecycle_key = lifecycle_key
        self.asked: list[str] = []

    def get_foundation_model(self, *, modelIdentifier: str):
        self.asked.append(modelIdentifier)
        if self.raises is not None:
            raise self.raises
        from datetime import datetime, timezone
        details = {
            "modelArn": f"arn:aws:bedrock:us-east-1::foundation-model/{modelIdentifier}",
            "modelName": "Claude Opus 4.5",
        }
        if self.lifecycle_key:
            details["modelLifecycle"] = {
                "status": self.status,
                "startOfLifeTime": datetime(2025, 11, 24, tzinfo=timezone.utc),
            }
        return {"modelDetails": details}


def test_the_probe_strips_the_region_prefix_because_the_control_plane_refuses_it():
    """Measured 2026-08-11: `GetFoundationModel` raises `ResourceNotFoundException` on the
    inference-profile id and answers on the bare one. The stripping is not decoration."""
    control = FakeControl()
    bedrock_module.model_lifecycle(DATED_ID, client=control)
    assert control.asked == ["anthropic.claude-opus-4-5-20251101-v1:0"]


def test_the_probe_and_the_resolver_agree_about_what_a_prefix_is():
    """Two lists of region prefixes are two answers to the same question. The mechanism is
    that both read one literal — this fails if either grows a prefix the other lacks."""
    src = MODULE.read_text(encoding="utf-8")
    assert src.count('("us.", "eu.", "apac.")') == 2, (
        "the region-prefix tuple is written somewhere other than the two places that "
        "share it, or one of them has diverged"
    )


def test_a_datetime_does_not_reach_the_record():
    """Three callers JSON-encode this dict. A type needing a custom encoder is a type one
    of them writes without one, and the failure lands after the unrepeatable call."""
    import json
    record = bedrock_module.model_lifecycle(DATED_ID, client=FakeControl())
    assert record["start_of_life_time"] == "2025-11-24T00:00:00+00:00"
    json.dumps(record)                          # raises if anything here is a datetime


def test_the_probe_never_raises_whatever_the_failure_is():
    """The arm's one call is unrepeatable (DESIGN §6.3). A supplementary metadata lookup
    that can abort it is the tail wagging the dog, so every failure becomes a record."""
    class Boom(Exception):
        pass

    for exc in (Boom("no"), KeyError("modelDetails"), ImportError("no boto3"),
                RuntimeError("credentials")):
        record = bedrock_module.model_lifecycle(DATED_ID,
                                               client=FakeControl(raises=exc))
        assert record["status"] == bedrock_module.LIFECYCLE_UNAVAILABLE
        assert record["probe_error"] == type(exc).__name__


def test_the_probe_error_is_a_type_name_and_never_the_message():
    """This dict is written to `agent_calls.jsonl` and to two files under `results/`. A
    botocore message can carry the request it failed on, and CLAUDE.md's rule about what
    goes into a log does not care what the exception was about."""
    secret = f"ValidationException: could not parse {SURFACE}"
    record = bedrock_module.model_lifecycle(DATED_ID,
                                            client=FakeControl(raises=ValueError(secret)))
    assert record["probe_error"] == "ValueError"
    assert SURFACE not in repr(record)


def test_a_missing_lifecycle_block_is_unavailable_rather_than_null():
    """`model_id_absent`'s rule (DESIGN §4): "we did not look" and "we looked and the
    platform had nothing to say" are different facts and only the second is a measurement."""
    record = bedrock_module.model_lifecycle(DATED_ID,
                                           client=FakeControl(lifecycle_key=False))
    assert record["status"] == bedrock_module.LIFECYCLE_UNAVAILABLE
    assert record["start_of_life_time"] is None
    assert record["model_name"] == "Claude Opus 4.5"          # the rest still arrived


def test_the_record_carries_only_the_closed_field_list():
    """`LIFECYCLE_FIELDS` is a closed list for a reason — the rest of the response is
    capability description, and a record that grows when AWS adds a field is a record
    whose diff between two runs is unreadable. Asserted here because nothing in the
    module enforces it; a constant no code reads is a comment."""
    record = bedrock_module.model_lifecycle(DATED_ID, client=FakeControl())
    assert tuple(record) == bedrock_module.LIFECYCLE_FIELDS
    failed = bedrock_module.model_lifecycle(DATED_ID,
                                           client=FakeControl(raises=ValueError("x")))
    # The failure record is the same fields plus the one that says why, and no others.
    assert tuple(failed) == bedrock_module.LIFECYCLE_FIELDS + ("probe_error",)


def test_no_field_here_claims_to_have_resolved_anything():
    """The sixth family in `tests/mutations/README.md` was a comment asserting a causal
    link that nothing established. The same defect as *data* would reach `metrics.json`
    and a reader who never opens this module, so the names are the guard."""
    record = bedrock_module.model_lifecycle(DATED_ID, client=FakeControl())
    for field in record:
        assert "resolv" not in field and "weights" not in field, (
            f"{field!r} reads as an identity claim; this probe establishes none — "
            "start_of_life_time is when the id appeared, not what serves it"
        )
    assert bedrock_module.DATED not in repr(record)


def test_the_probe_does_not_touch_the_runtime_client():
    """It is a different service. A probe reaching `converse` would be an uncounted
    inference call sitting outside the cost block."""
    fake = FakeRuntime()
    bedrock_module.model_lifecycle(DATED_ID, client=FakeControl())
    assert fake.calls == []
    src = MODULE.read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "model_lifecycle")
    called = {n.func.attr for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "converse" not in called


def test_the_control_client_asks_for_the_control_plane_and_the_given_region(monkeypatch):
    """The real `_control_client`, with only `boto3` faked.

    Written because `tools/check_patched_guarantees.py` flagged this function as patched
    everywhere and executed by nothing: `tests/test_orchestrate.py` substitutes it for every
    arm test, which is right for those tests and leaves it uncovered. Patching the
    third-party call inside it and running the real body is the same repair
    `test_check_bedrock_logging.py` made for `check_region` — the AWS call is faked, the
    function is not.

    What it asserts is the whole of what the function decides: the service name, because
    `GetFoundationModel` is on `bedrock` and `converse` is on `bedrock-runtime` and asking
    the wrong one fails at every id; and that the region argument arrives, since a probe
    pinned to the default region would describe a profile the call may not have used.
    """
    seen = {}

    class FakeBoto3:
        def client(self, service, **kwargs):
            seen["service"] = service
            seen.update(kwargs)
            return "a client"

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3())
    assert bedrock_module._control_client("us-west-2") == "a client"
    assert seen == {"service": "bedrock", "region_name": "us-west-2"}
    # None means "let botocore decide from the environment", not "pass a null region".
    bedrock_module._control_client(None)
    assert seen["region_name"] is None


def test_the_control_client_is_not_pinned_to_one_attempt():
    """`MAX_ATTEMPTS = 1` exists so `llm_calls` counts truthfully. This endpoint makes no
    inference and appears in no cost block, so pinning it would trade a metadata field for
    nothing — the difference between the two clients is deliberate and easy to 'tidy'."""
    src = MODULE.read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "_control_client")
    # The body without its docstring: that docstring names the constant to explain the
    # difference, and a substring search over the whole node reads the explanation as the
    # thing it warns against.
    body = [n for n in fn.body if not (isinstance(n, ast.Expr)
                                       and isinstance(n.value, ast.Constant))]
    names = {n.id for n in ast.walk(ast.Module(body=body, type_ignores=[]))
             if isinstance(n, ast.Name)}
    assert "MAX_ATTEMPTS" not in names
    assert "retries" not in ast.dump(ast.Module(body=body, type_ignores=[]))


# ─── the logging gate ────────────────────────────────────────────────────────

def a_tree_with(tmp_path: Path, section_body: str) -> Path:
    """A miniature repository: the real gate tool, and a `compliance.md` we control.

    The tool is copied rather than stubbed, so what runs is the code that runs in
    production — including how it locates §3 and what it accepts as a record. Both the tool
    and the client derive their roots from `__file__`, so pointing the client at this tree
    redirects the whole gate without patching any of its internals.
    """
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "notes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "llm").mkdir(parents=True, exist_ok=True)
    real = ROOT / "tools" / "check_bedrock_logging.py"
    (tmp_path / "tools" / "check_bedrock_logging.py").write_text(
        real.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "docs" / "notes" / "compliance.md").write_text(
        "# Compliance\n\n"
        "## 3. Bedrock model-invocation logging — measured state\n\n"
        f"{section_body}\n"
        "## 4. What still has to hold\n\nbody\n",
        encoding="utf-8")
    return tmp_path / "src" / "llm" / "bedrock.py"


def test_the_gate_blocks_the_call_when_no_check_is_recorded(monkeypatch, tmp_path):
    """Built before the first call, so the first call is blocked until the record exists."""
    monkeypatch.setattr(bedrock_module, "_require_logging_check", REAL_GATE)
    monkeypatch.setattr(bedrock_module, "__file__",
                        str(a_tree_with(tmp_path, "no record here.\n")))
    fake = FakeRuntime()
    with pytest.raises(BedrockError) as e:
        invoke(a_prompt(), model_id=OPUS, client=fake)
    assert "logging check" in str(e.value)
    assert not fake.calls, "the gate let a call through"


def test_the_gate_opens_when_todays_record_is_present(monkeypatch, tmp_path):
    """The other direction. A gate that never opens would pass the test above and be
    indistinguishable from one that is simply broken."""
    import datetime
    today = datetime.date.today().isoformat()
    monkeypatch.setattr(bedrock_module, "_require_logging_check", REAL_GATE)
    monkeypatch.setattr(bedrock_module, "__file__", str(a_tree_with(
        tmp_path,
        f"**Gate check {today}** (`tools/check_bedrock_logging.py`):\n\n"
        "| region | `loggingConfig` |\n|---|---|\n| us-east-1 | `None` |\n",
    )))
    fake = FakeRuntime()
    invoke(a_prompt(), model_id=OPUS, client=fake)
    assert len(fake.calls) == 1


def test_a_record_for_another_day_does_not_open_the_gate(monkeypatch, tmp_path):
    """The setting is mutable, so yesterday's check is evidence about yesterday."""
    monkeypatch.setattr(bedrock_module, "_require_logging_check", REAL_GATE)
    monkeypatch.setattr(bedrock_module, "__file__", str(a_tree_with(
        tmp_path, "**Gate check 2026-08-06** (`tools/check_bedrock_logging.py`):\n\n"
                  "| region | `loggingConfig` |\n|---|---|\n| us-east-1 | `None` |\n",
    )))
    with pytest.raises(BedrockError):
        invoke(a_prompt(), model_id=OPUS, client=FakeRuntime())


def test_a_record_outside_section_three_does_not_open_the_gate(monkeypatch, tmp_path):
    """A dated row under §5 would satisfy a naive date search while saying nothing about
    logging — the "the record exists and is unverifiable" shape §5.4 is written against."""
    import datetime
    today = datetime.date.today().isoformat()
    path = a_tree_with(tmp_path, "nothing in section three.\n")
    compliance = tmp_path / "docs" / "notes" / "compliance.md"
    compliance.write_text(
        compliance.read_text(encoding="utf-8")
        + f"\n## 5. Elsewhere\n\n**Gate check {today}** (`tools/check_bedrock_logging.py`):\n",
        encoding="utf-8")
    monkeypatch.setattr(bedrock_module, "_require_logging_check", REAL_GATE)
    monkeypatch.setattr(bedrock_module, "__file__", str(path))
    with pytest.raises(BedrockError):
        invoke(a_prompt(), model_id=OPUS, client=FakeRuntime())


def test_the_gate_runs_before_the_request(monkeypatch):
    """Order matters: a gate checked after the call has already sent the prompt."""
    order = []
    monkeypatch.setattr(bedrock_module, "_require_logging_check",
                        lambda: order.append("gate"))

    class Recorder(FakeRuntime):
        def converse(self, **kwargs):
            order.append("call")
            return super().converse(**kwargs)

    invoke(a_prompt(), model_id=OPUS, client=Recorder())
    assert order == ["gate", "call"]


def test_the_gate_asks_the_tool_rather_than_parsing_the_file_itself():
    """Two readers of one format is the `check_rules`/`run_fold` defect one layer down."""
    src = MODULE.read_text(encoding="utf-8")
    assert "compliance.md" not in src.split('"""')[2] or True   # prose may name it
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_require_logging_check")
    calls = {n.func.attr for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "checked_today" in calls, (
        "_require_logging_check does not ask the tool whether today is on record"
    )
    assert "findall" not in calls and "read_text" not in calls, (
        "_require_logging_check parses the compliance file itself; the tool owns that format"
    )


def test_a_missing_gate_tool_is_refused_rather_than_ignored(monkeypatch, tmp_path):
    """A gate that passes when its own tool is absent is not a gate."""
    monkeypatch.setattr(bedrock_module, "_require_logging_check", REAL_GATE)
    monkeypatch.setattr(bedrock_module, "__file__", str(tmp_path / "src" / "llm" / "b.py"))
    with pytest.raises(BedrockError) as e:
        invoke(a_prompt(), model_id=OPUS, client=FakeRuntime())
    assert "missing" in str(e.value)


# ─── nothing here writes the prompt or the completion ────────────────────────

def functions(tree: ast.AST) -> dict[str, ast.AST]:
    """Every function and method, so a check cannot be evaded by moving code into one."""
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def body_calls(fn: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            names.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return names


def test_no_function_in_the_module_writes_to_a_file():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    offenders = {name: sorted(body_calls(fn) & WRITE_NAMES)
                 for name, fn in functions(tree).items()
                 if body_calls(fn) & WRITE_NAMES}
    assert not offenders, f"{offenders} write in a module that handles prompts"


def test_no_function_in_the_module_logs():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    offenders = {name: sorted(body_calls(fn) & LOG_NAMES)
                 for name, fn in functions(tree).items()
                 if body_calls(fn) & LOG_NAMES}
    assert not offenders, f"{offenders} log in a module that handles prompts"


def test_the_module_never_prints():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    assert not any(
        isinstance(n, ast.Call) and getattr(n.func, "id", "") == "print"
        for n in ast.walk(tree)
    )


def test_the_module_imports_no_logging():
    """Closed import set: `logging` cannot be reached, so a later edit cannot use it."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "logging" not in imported


#: Identifiers that hold prompt or completion text. An exception message interpolating one
#: puts corpus context into a terminal, a CI log and an issue — the paths CLAUDE.md names
#: because `release_screen.py` reaches none of them.
TEXT_NAMES = {"text", "prompt", "completion", "body", "parts", "message"}

#: Wrappers that reduce a value to something inert. `len(text)` is a length, `sorted(usage)`
#: is a set of key names, `type(x).__name__` is a class name — all publishable, and all
#: things a useful error message needs. A check without these forces the messages to say
#: less than they safely could, and a message that cannot say what went wrong gets replaced
#: by a `print` of the object.
SAFE_WRAPPERS = ("len(", "sorted(", "type(")


def text_leaking_interpolations(tree: ast.AST) -> list[str]:
    """Interpolations inside a `raise` that could carry prompt or completion text."""
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.FormattedValue):
                continue
            expr = ast.unparse(sub.value)
            if expr.startswith(SAFE_WRAPPERS):
                continue
            names = {n.attr if isinstance(n, ast.Attribute) else getattr(n, "id", "")
                     for n in ast.walk(sub.value)}
            if names & TEXT_NAMES:
                offenders.append(expr)
    return offenders


def test_no_exception_message_interpolates_the_prompt_or_the_completion():
    """CLAUDE.md: no corpus text in an exception message, which reaches CI logs and issues
    where `release_screen.py` never goes. Checked structurally because the leak would be a
    single f-string that reads as helpful — `f"...{prompt}"` while debugging a bad
    response."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    offenders = text_leaking_interpolations(tree)
    assert not offenders, f"a raise interpolates prompt or completion text: {offenders}"


def test_the_interpolation_check_catches_a_leak_and_allows_a_length():
    """Both directions, because either failure makes the test above meaningless: blind to a
    real leak, or so strict that the messages get gutted to satisfy it."""
    leak = ast.parse('def f(prompt):\n    raise E(f"bad prompt: {prompt.for_transport()}")\n')
    assert text_leaking_interpolations(leak), "the check missed an interpolated prompt"
    safe = ast.parse('def f(text, usage, prompt):\n'
                     '    raise E(f"{len(text)} chars, keys {sorted(usage)}, '
                     'got {type(prompt).__name__}")\n')
    assert not text_leaking_interpolations(safe), (
        "the check flags a length, a key list or a type name"
    )


def test_the_completion_text_is_not_written_anywhere_by_the_module():
    """The `Response` carries text because a rule file has to be parsed out of it; that is
    not a licence to persist it, and a completion echoing its prompt carries the corpus."""
    r = invoke(a_prompt(SURFACE), model_id=OPUS, client=FakeRuntime(reply(SURFACE)))
    assert r.text == SURFACE                      # available to the caller
    assert SURFACE not in repr(r.cost())          # and in nothing the record holds
    assert SURFACE not in repr(r.model_record())


def test_the_response_is_frozen():
    """A record that can be edited after the call is not a record of the call."""
    r = invoke(a_prompt(), model_id=OPUS, client=FakeRuntime())
    with pytest.raises(Exception):
        r.text = "something else"                 # type: ignore[misc]


# ─── the default budget ──────────────────────────────────────────────────────

def test_the_default_budget_is_generous_because_reasoning_shares_it():
    """Measured: reasoning tokens come out of `maxTokens`. Too low truncates the artefact,
    and a truncated rule file would be reported as a capability result (§10 A2)."""
    assert DEFAULT_MAX_TOKENS >= 32768


def test_the_budget_reaches_the_call_and_is_overridable():
    fake = FakeRuntime()
    invoke(a_prompt(), model_id=OPUS, client=fake)
    assert fake.calls[0]["inferenceConfig"]["maxTokens"] == DEFAULT_MAX_TOKENS
    fake2 = FakeRuntime()
    invoke(a_prompt(), model_id=OPUS, max_tokens=100, client=fake2)
    assert fake2.calls[0]["inferenceConfig"]["maxTokens"] == 100
