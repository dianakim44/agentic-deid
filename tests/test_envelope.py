"""DESIGN §6.8's strip policy: one outer fence, only if what is inside parses, recorded both ways.

Pre-registered 2026-09-17, before the next arm, on the measurement in
`docs/notes/prompt-format-probe.md` rather than under the pressure of the three arms it would
have saved. What changed is one boundary — whether a valid object inside a code fence is a format
failure — and this file is the statement of what did *not* change with it.

**The four properties, and which test holds each.**

1. *Exactly one outer fence, and nothing else is stripped.* Five shapes stay failures: a nested
   fence, an opened-and-not-closed fence, a closed-and-not-opened fence, text outside the fence,
   and a fence around something that does not parse. `the_five_shapes_that_stay_failures`.
2. *Accept only if the stripped text parses.* The strip is not attempted before the received
   bytes have failed to parse, and its result is thrown away unless it parses.
   `a_fence_around_junk_is_refused` and `the_received_bytes_are_tried_first`.
3. *The record says both.* Six fields, always, with `parsed_*` null only when nothing parsed.
   `the_record_carries_both_sides` and the required-block tests in `tests/test_call_role.py`.
4. *Uniform across roles.* One function, and the structural test is
   `every_response_path_goes_through_the_one_unwrapper` — an AST walk, because uniformity is a
   property of the call graph and a behavioural test can only see the paths it thinks to drive.

**Why the counterfactual is a test and not a paragraph.** §6.8's record has to say that three
committed `format_failure.json` records would not have been written under this policy, and a
sentence saying so is a claim about code that has since changed. The three responses are on disk
in a committed, screened path, so `the_three_dead_arms_would_have_passed_the_format_gate` runs
them through today's unwrapper and today's validator and asserts what each does. If a later edit
narrows the policy so that one of them fails again, the paper's sentence about them becomes false
and this is where that is discovered.

**Those arms are not revived by any of this.** Nothing here writes into `results/`, and no arm is
re-run: the record says they would have passed, and they stay failed (DESIGN §6.8, and CLAUDE.md's
sealed-fold discipline for why a re-run to improve a recorded outcome is not available). The
counterfactual is about the *format gate* only. Whether the two `port-multi` arms' validators
would then have refused a field is a different stage and is not measured here.

**No corpus text and no response text is written by these tests.** The three responses are read
out of committed records into memory; what is asserted about them is a kind, a count, a length and
a hash.

    python3 -m pytest tests/test_envelope.py -q
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora.base import CorpusError, response_envelopes                    # noqa: E402
from src.llm import envelope as env                                             # noqa: E402
from src.llm.envelope import (                                                  # noqa: E402
    BARE, FENCED_ONCE, RECORD_FIELDS, REFUSED, Envelope, parses_json, parses_yaml, unwrap,
)

#: A JSON object and a YAML mapping, minimal and real. Both are what §2.1 asks the agents for.
JSON_OBJECT = '{"map": {"NOMBRE": "PERSON"}, "unresolved": []}'
YAML_MAPPING = "version: 1\nlang: es\nrules: []\n"

#: The arms whose responses are the counterfactual, with the probe each one's response was judged
#: by and what the surviving payload has to satisfy. Written out rather than discovered by walking
#: `results/`, for `test_call_role.FROZEN_ARMS`' reason: a test that discovered its own subjects
#: would pass on an empty tree.
#:
#: `port-oneshot` is in this list and was not in the instruction that pre-registered the policy —
#: it was found while writing the tests. Its record is `FAILURE_SCHEMA` 2, its response is one
#: fenced YAML document, and the payload loads as 28 rules. So the count is three arms and not
#: two, and the honest place to fix the number was here rather than in the sentence.
DEAD_ARMS = (
    ("port-oneshot", "yaml"),
    ("port-multi", "json"),
    ("port-multi-noexample", "json"),
)

ARM = ("es-meddocan", "R", "sup-free")


def fenced(body: str, tag: str = "") -> str:
    """`body` inside exactly one outer fence, with an optional language tag."""
    return f"```{tag}\n{body}\n```"


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def failure_record(porting: str) -> dict:
    """One arm's committed `format_failure.json`, or a skip.

    A skip rather than a failure when the file is absent, and narrowly: these records *are*
    committed (the path is on the screener's ALLOW list, unlike `agent_calls.jsonl`), so on this
    repository they are present. The skip is for a tree where `results/` has been cleared, which
    is the same honest result `test_call_role.frozen_lines` gives for the same situation.
    """
    path = ROOT.joinpath("results", *ARM, porting, "format_failure.json")
    if not path.is_file():
        pytest.skip(f"{porting}: no format-failure record on this working tree")
    return json.loads(path.read_text(encoding="utf-8"))


# ─── the classifier ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,parses", [(JSON_OBJECT, parses_json),
                                         (YAML_MAPPING, parses_yaml)])
def test_a_bare_response_is_bare_and_is_not_touched(text, parses):
    """The common case, and the one the policy must leave exactly as it was."""
    result = unwrap(text, parses=parses)
    assert result.kind == BARE
    assert result.payload == result.received == text
    assert result.fence_lines == 0
    assert result.accepted


@pytest.mark.parametrize("tag", ["", "json", "JSON", "yaml", "yml", "c++", "objective-c"])
def test_one_outer_fence_comes_off_whatever_the_tag_says(tag):
    """The tag is matched and discarded. A model that labels a JSON object `yaml` has still
    emitted the object, and the policy is about the wrapper — reading the tag as a claim about
    the content would make the strip conditional on the model being right about its own output.
    """
    result = unwrap(fenced(JSON_OBJECT, tag), parses=parses_json)
    assert result.kind == FENCED_ONCE
    assert result.payload == JSON_OBJECT
    assert result.fence_lines == 2


def test_the_tag_is_not_recorded_anywhere():
    """CLAUDE.md at the record: the tag is response bytes. The counts answer what it would.

    Not a style point. This record lands in `agent_calls.jsonl`, which is deny-listed and
    therefore the one file `tools/release_screen.py` cannot review, so every field on it has to
    be a number or a closed vocabulary value.
    """
    record = unwrap(fenced(JSON_OBJECT, "objective-c"), parses=parses_json).record()
    assert set(record) == set(RECORD_FIELDS)
    assert "objective-c" not in json.dumps(record)
    for key, value in record.items():
        assert isinstance(value, (int, str, type(None))), key
        if isinstance(value, str) and key != "envelope":
            assert value.startswith("sha256:"), key


def test_the_received_bytes_are_tried_first():
    """Order, and it is what keeps a bare response from ever being inspected for a fence.

    A response that parses is `bare` by definition, so the fence search runs only after a parse
    has already failed. Asserted with three backticks *inside* a JSON string value, which is a
    valid object whose first line looks like a fence to a line-shape scan: the policy calls it
    `bare`, which is true of it, and a scanner that looked for fences first would strip a
    response that never had one.
    """
    text = json.dumps({"note": "```"})
    result = unwrap(text, parses=parses_json)
    assert result.kind == BARE
    assert result.payload == text


@pytest.mark.parametrize("name,text", [
    ("nested", "```\n```json\n" + JSON_OBJECT + "\n```\n```"),
    ("opened_never_closed", "```json\n" + JSON_OBJECT),
    ("closed_never_opened", JSON_OBJECT + "\n```"),
    ("preamble_outside_the_fence", "Here is the mapping:\n" + fenced(JSON_OBJECT, "json")),
    ("remark_after_the_fence", fenced(JSON_OBJECT, "json") + "\nLet me know if anything is off."),
])
def test_the_five_shapes_that_stay_failures(name, text):
    """§6.8's scope, one case per clause, named after the shape rather than the mechanism.

    Every one of these could be handled by a slightly wider strip, and that is the point: the
    policy is narrow because the argument for it is narrow. The measurement says a *single* outer
    fence carries no information about the content (the Mapper's fenced and unfenced responses
    were body-byte-identical); it says nothing about a model that wrote a sentence outside the
    fence, and a response with a preamble is a response that did not follow §2.1 in a way nobody
    has measured the consequences of.
    """
    result = unwrap(text, parses=parses_json)
    assert result.kind == REFUSED, f"{name} was accepted"
    assert not result.accepted
    assert result.payload == result.received == text, (
        f"{name}: the payload was edited on a refusal. The caller's validator has to raise on "
        "the bytes that arrived, or the message describes what this module made of the response "
        "rather than what the model sent."
    )


def test_a_fence_around_junk_is_refused():
    """Property 2 on its own: the strip's result is thrown away unless it parses.

    This is the clause that keeps the policy from being a repair. A step that removed the fence
    and handed on whatever was left would be trying to make the response work; a step that
    removes it and then still fails is only correcting for the wrapper.
    """
    result = unwrap(fenced("rules: [\nversion", "yaml"), parses=parses_yaml)
    assert result.kind == REFUSED
    assert result.payload == result.received


@pytest.mark.parametrize("text", ["", "   ", "\n\n", "```\n```", "not a response at all"])
def test_nothing_parseable_is_refused_without_raising(text):
    """`unwrap()` runs before the call is logged, so it may not raise — the log line would be
    lost for a call that was already made and paid for. Includes the empty response and a fence
    with nothing in it, which are the shapes a truncated transport produces."""
    result = unwrap(text, parses=parses_json)
    assert result.kind == REFUSED


def test_a_non_string_response_is_refused_rather_than_raising():
    """The same promise for a caller that hands over something that is not text. The payload is
    passed through untouched so the caller's own parser raises on the object it was given."""
    result = unwrap(None, parses=parses_json)
    assert result.kind == REFUSED
    assert result.payload is None
    assert result.record()["response_chars"] == 0


def test_a_yaml_document_that_is_a_string_does_not_count_as_parsed():
    """`parses_yaml` demands a mapping, and this is the case that makes the demand necessary.

    `yaml.safe_load` returns a *string* for a fenced block — so a probe that only asked "did it
    load" would call a fenced rule file `bare`, hand the fence to `load_rules`, and produce the
    exact failure this policy exists to stop being about the fence.
    """
    assert not parses_yaml(fenced(YAML_MAPPING, "yaml"))
    assert not parses_yaml("just a sentence")
    assert parses_yaml(YAML_MAPPING)
    result = unwrap(fenced(YAML_MAPPING, "yaml"), parses=parses_yaml)
    # The payload is the lines between the fences, verbatim — trailing blank line and all. The
    # strip removes the fence and not whitespace: a policy that also tidied the body would be
    # editing the response, and the parsed hash would stop being a hash of anything the model sent.
    assert (result.kind, result.payload) == (FENCED_ONCE, YAML_MAPPING)


def test_a_json_array_is_not_an_object():
    """`parses_json` demands a dict for §2.1's reason: all five prompts ask for one object."""
    assert not parses_json("[1, 2, 3]")
    assert not parses_json('"a string"')
    assert parses_json(JSON_OBJECT)


# ─── the record ────────────────────────────────────────────────────────────

def test_the_record_carries_both_sides():
    """Property 3, which is the property that keeps acceptance from destroying observability.

    An accepted fence has to stay visible as one, because §6.9's per-role rates (0% / 65% / 95%
    on 2026-09-17) are read off exactly this field on every later arm. Both hashes are checked
    against independently computed digests rather than against each other.
    """
    received = fenced(JSON_OBJECT, "json")
    record = unwrap(received, parses=parses_json).record()
    assert record == {
        "response_chars": len(received),
        "response_sha256": digest(received),
        "envelope": FENCED_ONCE,
        "fence_lines": 2,
        "parsed_chars": len(JSON_OBJECT),
        "parsed_sha256": digest(JSON_OBJECT),
    }
    assert record["response_sha256"] != record["parsed_sha256"]


def test_a_bare_record_states_the_parsed_side_explicitly():
    """Rather than leaving a reader to infer that it equals the received side. That inference is
    precisely what an accepted fence would hide, and a field present only when it differs is a
    field whose absence has two meanings (`model_id_absent`, DESIGN §4)."""
    record = unwrap(JSON_OBJECT, parses=parses_json).record()
    assert record["parsed_chars"] == record["response_chars"] == len(JSON_OBJECT)
    assert record["parsed_sha256"] == record["response_sha256"]
    assert record["envelope"] == BARE


def test_a_refusal_nulls_the_parsed_side_rather_than_zeroing_it():
    """Nothing was parsed, and `0` would be a length — a length says an empty string was read."""
    record = unwrap("not json", parses=parses_json).record()
    assert record["parsed_chars"] is None and record["parsed_sha256"] is None
    assert record["response_chars"] == len("not json")


def test_the_record_holds_no_response_text():
    """Every value is a count, a hash or a vocabulary word. The one file this lands in is the one
    the screener never reads, so this is checked rather than reviewed (CLAUDE.md)."""
    secret = '{"surface": "Paciente Juan Gómez, NHC 12345"}'
    record = unwrap(fenced(secret, "json"), parses=parses_json).record()
    blob = json.dumps(record, ensure_ascii=False)
    for fragment in ("Juan", "Gómez", "12345", "Paciente", "surface"):
        assert fragment not in blob


def test_the_kind_goes_through_the_closed_vocabulary():
    """`config/naming.yaml` owns the three values, so a kind this module invented is refused at
    record time rather than written. The vocabulary rule is CLAUDE.md's and it covers values that
    never reach a path, which this one does not."""
    assert set(response_envelopes()) == {BARE, FENCED_ONCE, REFUSED}
    with pytest.raises(CorpusError):
        Envelope(received="x", payload="x", kind="stripped", fence_lines=0).record()


def test_the_envelope_is_frozen():
    """A writer that mutated one after the line was logged would edit a record already on disk —
    `orchestrate.append_call`'s copy-rather-than-hold argument, one object over."""
    result = unwrap(JSON_OBJECT, parses=parses_json)
    with pytest.raises(Exception):
        result.kind = REFUSED                                    # type: ignore[misc]


# ─── uniformity across the roles ───────────────────────────────────────────
#
# The clause this section is about is the one the instruction was most explicit on: "모든 역할에
# 균일하게 적용한다. 특정 역할 면제는 이번 결함이 상속된 경로다." The defect *was* inherited that
# way — three prompts said the same thing in §2.1, one parser was strict about it, and the two
# that mattered were reached by different code — so the check has to be structural. A behavioural
# test covers the roles somebody thought to drive, which is exactly the set that does not include
# the next one.

#: The modules that turn a response into something a validator sees. Written out, because the
#: failure being guarded is a *sixth* path added later — and a test that discovered its subjects
#: by walking `src/` would happily report that all zero remaining paths comply.
RESPONSE_PATHS = ("src/orchestrate.py", "src/porting/loop.py", "src/porting/multi.py")


def calls_named(tree: ast.AST, name: str) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and (getattr(node.func, "id", None) == name
                 or getattr(node.func, "attr", None) == name)]


@pytest.mark.parametrize("path", RESPONSE_PATHS)
def test_every_response_path_goes_through_the_one_unwrapper(path):
    """Each module that logs a call imports `unwrap` and calls it, and spells no strip of its own.

    The second half is what makes this more than an import check: a module that called `unwrap`
    *and* had its own `lstrip("`")` somewhere would satisfy uniformity's letter while carrying
    the second implementation the clause forbids.
    """
    source = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("envelope")
                for alias in node.names}
    assert "unwrap" in imported, f"{path} logs calls and does not import the unwrapper"
    assert calls_named(tree, "unwrap"), f"{path} imports the unwrapper and never calls it"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in \
                ("strip", "lstrip", "rstrip", "removeprefix", "removesuffix", "replace"):
            for arg in node.args:
                assert not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                            and "`" in arg.value), (
                    f"{path}:{node.lineno} strips a backtick outside `llm/envelope.py`. One "
                    "implementation, reached by every role (DESIGN §6.8)."
                )


@pytest.mark.parametrize("path", RESPONSE_PATHS)
def test_no_call_line_is_written_without_an_envelope(path):
    """Every `call_line(...)` in the source passes `envelope=`, and every `_write_failure(...)`
    does too. The writer refuses a missing block at runtime (`tests/test_call_role.py`); this is
    the same claim made where a reader can see it, so a new call site is caught by the suite
    rather than by the arm that spends money to reach it.
    """
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    for name in ("call_line", "_write_failure"):
        for node in calls_named(tree, name):
            keywords = {kw.arg for kw in node.keywords}
            assert "envelope" in keywords, (
                f"{path}:{node.lineno}: {name}() is called without `envelope=`. A role that can "
                "be logged without saying what wrapped its response is the exemption §6.8 "
                "forbids."
            )
            assert not keywords & {"response_chars", "response_sha256"}, (
                f"{path}:{node.lineno}: {name}() is passed the byte fields directly. They come "
                "out of the envelope block, or the two records of one call can disagree."
            )


@pytest.mark.parametrize("path", RESPONSE_PATHS)
def test_no_validator_is_handed_the_bytes_that_arrived(path):
    """`response.text` reaches exactly two places: `unwrap()`, and the `response=` field of a
    failure record. Anywhere else it is a role that skipped the policy.

    This is the check that catches the exemption in the form it would actually be written. The
    tempting version is not a branch inside `unwrap` — it is one author, at one call site,
    passing the received bytes to its validator with a comment about that role's measured 0/20
    fence rate. Such a site imports `unwrap`, calls `unwrap`, strips no backtick, and logs a
    complete envelope block; every other test in this file passes against it. What gives it away
    is that a validator is reading `response.text` while the record says something was parsed.

    The failure record's `response=` field is the one legitimate use: that record has to hold
    the bytes that arrived, and holding the payload instead is what
    `test_the_record_carries_both_sides` is about one layer down.
    """
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if called == "unwrap":
                allowed.update(id(arg) for arg in node.args)
            elif called in ("_write_failure", "_failed"):
                allowed.update(id(kw.value) for kw in node.keywords if kw.arg == "response")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "text" \
                and getattr(node.value, "id", None) == "response" and id(node) not in allowed:
            raise AssertionError(
                f"{path}:{node.lineno} reads `response.text` outside `unwrap()` and outside a "
                "failure record's `response=`. A validator handed the received bytes is the "
                "role exemption DESIGN §6.8 forbids, written as one call site rather than as a "
                "branch."
            )


def test_the_five_roles_all_reach_the_unwrapper():
    """Named one by one, against `naming.yaml`'s vocabulary, so the count is the vocabulary's.

    The structural tests above are per-module; this is the per-*role* statement, which is how
    §6.8's clause is phrased. `rule_author` is reached twice (round 1 and round N) and that is
    counted as one role.
    """
    from src.corpora.base import agent_roles

    source = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in RESPONSE_PATHS)
    for role in agent_roles():
        assert role in source, f"{role} is not driven from any module that unwraps a response"
    # Five roles, three modules, and the number of unwrap calls is what says none of them shares
    # a code path that skips it: rule_author twice in `loop`, auditor once, `multi._call` once for
    # the three authoring roles, and `orchestrate.run_arm` once.
    counts = {path: len(calls_named(ast.parse((ROOT / path).read_text(encoding="utf-8")),
                                    "unwrap"))
              for path in RESPONSE_PATHS}
    assert counts == {"src/orchestrate.py": 1, "src/porting/loop.py": 3,
                      "src/porting/multi.py": 1}, counts


# ─── the counterfactual: three arms this policy would not have failed ──────
#
# [1]'s last clause: "port-multi 와 port-multi-noexample 이 이 정책 아래서는 통과했을 것이라는
# 사실을 기록해라. 되살릴 수는 없지만 기록은 그렇게 말해야 한다." Recorded here as a test rather
# than as a sentence, because a sentence about code cannot fail when the code moves. The count is
# three: `port-oneshot`'s record says the same thing and was found while writing this file.

@pytest.mark.parametrize("porting,language", DEAD_ARMS)
def test_the_three_dead_arms_would_have_passed_the_format_gate(porting, language):
    """Each committed response, through today's unwrapper and today's validator.

    What is asserted per arm: the response classifies as `fenced_once`, exactly two fence lines,
    and the payload satisfies the validator that refused the arm. That is the whole of the claim —
    the *format gate* would not have failed. For the two `port-multi` arms the next stage is field
    validation against a profile and an inventory, and whether that stage would have refused
    anything is not measured here and is not implied by this test passing.

    **Nothing is revived.** These records stay on disk as failures, no arm is re-run, and this
    test writes nothing outside a temporary directory. It is the record saying what the record has
    to say.
    """
    record = failure_record(porting)
    response = record["response"]
    result = unwrap(response, parses=parses_yaml if language == "yaml" else parses_json)
    assert result.kind == FENCED_ONCE, (
        f"{porting}: the committed response is {result.kind} and the counterfactual in DESIGN "
        "§6.8 says it would have passed. Either the policy narrowed or the record changed; "
        "either way the paper's sentence about this arm is now wrong."
    )
    assert result.fence_lines == 2
    assert len(result.payload) < len(result.received)

    if language == "yaml":
        from src.rules import load_rules

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "es.yaml"
            path.write_text(result.payload, encoding="utf-8")
            rules = load_rules("es", path=path)
        assert rules.rules, f"{porting}: the payload loads and carries no rules"
    else:
        from src.porting.artefacts import parse_object

        assert isinstance(parse_object(result.payload, what=porting), dict)


def test_the_dead_arms_records_are_left_as_failures():
    """The other half of "되살릴 수는 없지만": the counterfactual does not edit the record.

    Asserted rather than trusted, because the tempting repair is exactly the reachable one — the
    responses are on disk, the policy now accepts them, and re-running the arm would produce a
    `metrics.json`. What forbids it is not this test but DESIGN §6.8 and §10 A2; what this test
    does is make the state visible, so a `metrics.json` appearing beside one of these records is
    a suite failure rather than a discovery in six months.
    """
    for porting, _ in DEAD_ARMS:
        record = failure_record(porting)
        assert record["error"], f"{porting}: the record lost the validator's message"
        arm = ROOT.joinpath("results", *ARM, porting)
        assert not (arm / "metrics.json").exists(), (
            f"{porting} has both a format-failure record and a metrics file. One arm writes one "
            "of the two (DESIGN §10 A2); a metrics file here means the arm was re-run after "
            "§6.8, which the policy's own record says did not happen."
        )


def test_the_older_records_carry_no_envelope_block():
    """`FAILURE_SCHEMA` 4 adds the block, and these three predate it.

    The bump is the readable kind precisely because of this: a record at 3 or 2 measured nothing
    about the wrapper, and a record at 4 always does. Had the field been backfilled onto these —
    with the right value, since it is recoverable here — the version would no longer say which
    records were measured and which were reconstructed.
    """
    for porting, _ in DEAD_ARMS:
        record = failure_record(porting)
        assert record["schema_version"] < 4
        for field in ("envelope", "fence_lines", "parsed_chars", "parsed_sha256"):
            assert field not in record, f"{porting} acquired `{field}`"


def test_todays_writer_would_have_recorded_the_fence():
    """The pair to the test above: the value is absent from the old records and *present* on
    anything written now. Together they are what makes §6.9's rates countable going forward —
    absent means "before the policy", `fenced_once` means "measured and accepted".
    """
    record = failure_record("port-multi-noexample")
    envelope = unwrap(record["response"], parses=parses_json).record()
    assert envelope["envelope"] == FENCED_ONCE
    assert envelope["response_sha256"] == digest(record["response"])
    assert envelope["response_chars"] == len(record["response"])
    assert envelope["parsed_chars"] < envelope["response_chars"]
    # And the old record's own byte count agrees with the received side, which is what makes the
    # two comparable at all: the field that survived the schema bump means the same thing.
    assert record["response_chars"] == envelope["response_chars"]


def test_the_probe_measured_the_same_response_the_arm_died_on():
    """`docs/notes/prompt-format-probe.md`'s decisive fact, re-derived rather than cited.

    The note records that `port-multi-noexample`'s dead Mapper response is byte-identical to one
    of the probe's two draws and that the other draw is that response minus the fence. This
    asserts the half that lives on this tree: de-fencing the committed response yields a body
    whose hash is what the note records for the clean draw. That is the measurement §6.8 rests
    on — the fence carried no information about the content — and if it stops being true, the
    justification for the policy is what changed.
    """
    note = (ROOT / "docs" / "notes" / "prompt-format-probe.md").read_text(encoding="utf-8")
    record = failure_record("port-multi-noexample")
    result = unwrap(record["response"], parses=parses_json)
    clean = result.payload
    assert result.kind == FENCED_ONCE
    for value in (digest(record["response"]), digest(clean)):
        short = value.split(":", 1)[-1][:12]
        assert short in note, (
            f"{short}… is not in prompt-format-probe.md. The policy is pre-registered on that "
            "measurement, so a response whose hash the note does not carry means the note and "
            "the record are about different bytes."
        )
