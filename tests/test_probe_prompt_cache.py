"""Tests for `tools/probe_prompt_cache.py` — response parsing and the request shape.

**No AWS call, and no test of one.** The probe's value is a measurement of a live API, and a
test against a fake client would assert what the fake was written to say — a test of the
fixture. So what is tested here is the two things that are ours: that a recorded `usage` block
is read correctly (including the case where the cache fields are *absent*, which is what a
control call returns), and that the request the probe builds is the shape the measurement
assumes — one content block uncached, and prefix/cachePoint/tail cached.

The probe is not a gate and not a production path (see its module docstring), so there are no
mutations for it and nothing here asserts that anything refuses.

    python3 -m pytest tests/test_probe_prompt_cache.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOOL = ROOT / "tools" / "probe_prompt_cache.py"


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("_probe_prompt_cache", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# A control response: the three required `TokenUsage` members and no cache fields, which is
# what Bedrock returns when nothing was cached.
CONTROL = {
    "usage": {"inputTokens": 6900, "outputTokens": 5, "totalTokens": 6905},
    "stopReason": "end_turn",
}

# A write response and a read response, in the shape the service model declares:
# `cacheWriteInputTokens` on the call that created the entry, `cacheReadInputTokens` on the
# one that hit it, and `cacheDetails` as a list of per-TTL write records.
WRITE = {
    "usage": {"inputTokens": 25, "outputTokens": 5, "totalTokens": 6905,
              "cacheWriteInputTokens": 6875, "cacheReadInputTokens": 0,
              "cacheDetails": [{"ttl": "5m", "inputTokens": 6875}]},
    "stopReason": "end_turn",
}
READ = {
    "usage": {"inputTokens": 25, "outputTokens": 5, "totalTokens": 6905,
              "cacheWriteInputTokens": 0, "cacheReadInputTokens": 6875,
              "cacheDetails": []},
    "stopReason": "end_turn",
}


def test_an_absent_cache_field_is_reported_absent_and_not_zero(probe):
    """A control call mentions no caching, and that is not the same as caching nothing.

    Zero would be a measurement — "the model read no cached tokens". Absent says the response
    did not speak to caching at all. `bedrock._usage()` refuses to default a missing count for
    the same reason, and the note this probe writes is read by someone deciding a schema, so
    the distinction has to survive into the table.
    """
    usage = probe.usage_of(CONTROL)
    assert usage["inputTokens"] == 6900
    assert usage["cacheReadInputTokens"] == "(absent)"
    assert usage["cacheWriteInputTokens"] == "(absent)"
    assert usage["cacheDetails"] == "(absent)"


def test_the_cache_fields_are_read_off_a_write_and_a_read_response(probe):
    """Both directions, from the fields the service model declares."""
    write = probe.usage_of(WRITE)
    assert write["cacheWriteInputTokens"] == 6875
    assert write["cacheReadInputTokens"] == 0
    assert write["cacheDetails"] == [{"ttl": "5m", "inputTokens": 6875}]

    read = probe.usage_of(READ)
    assert read["cacheReadInputTokens"] == 6875
    assert read["cacheWriteInputTokens"] == 0


def test_every_usage_key_appears_in_the_row(probe):
    """The row carries all six keys whatever the response held.

    A table with a column missing on some rows is a table whose absences are invisible, and
    absence is one of the findings.
    """
    for response in (CONTROL, WRITE, READ):
        assert set(probe.usage_of(response)) == set(probe.USAGE_KEYS)


def test_a_response_with_no_usage_block_is_refused(probe):
    """Nothing to measure, and no number invented to stand in for it."""
    with pytest.raises(probe.ProbeError):
        probe.usage_of({"stopReason": "end_turn"})


def test_the_uncached_request_is_one_text_block(probe):
    """The control has to be what `invoke()` sends, or it measures two differences at once.

    `bedrock.invoke()` sends `content=[{"text": ...}]`. If the control sent two blocks without
    a cache point, the comparison against the cached call would confound the block split with
    the caching, and the probe's one conclusion would not follow.
    """
    messages = probe.build_messages("PREFIX", cache=False)
    assert len(messages) == 1
    content = messages[0]["content"]
    assert len(content) == 1
    assert list(content[0]) == ["text"]
    assert content[0]["text"].startswith("PREFIX")
    assert probe.TAIL in content[0]["text"]


def test_the_cached_request_puts_the_cache_point_between_prefix_and_tail(probe):
    """Three blocks in order: the cached prefix, the point, the variable tail.

    The order is the measurement. A cache point *before* the prefix would cache nothing, and a
    point after the tail would cache the part that changes every call — which is the mistake
    the production design has to avoid, so the probe cannot be making it either.
    """
    content = probe.build_messages("PREFIX", cache=True)[0]["content"]
    assert [list(block)[0] for block in content] == ["text", "cachePoint", "text"]
    assert content[0]["text"] == "PREFIX"
    assert content[1]["cachePoint"] == {"type": "default"}
    assert content[2]["text"] == probe.TAIL


def test_the_two_forms_send_the_same_text(probe):
    """Cached and uncached carry identical text, so only the framing differs."""
    uncached = probe.build_messages("PREFIX", cache=False)[0]["content"]
    cached = probe.build_messages("PREFIX", cache=True)[0]["content"]
    joined = "".join(b["text"] for b in cached if "text" in b)
    assert uncached[0]["text"].replace("\n\n", "") == joined


def test_the_tail_and_prefix_carry_no_corpus_text(probe):
    """The subject of the probe is a committed template and a fixed sentence.

    `PREFIX` names a path under `docs/prompts/` and the tail is a literal in the file. Neither
    is assembled from a corpus, which is why this probe needs no masking discipline — asserted
    rather than left to the docstring, since a later edit that reached for a real document
    would make this the second transport path the file says it is not.
    """
    assert str(probe.PREFIX).startswith("docs/prompts/")
    assert "data/" not in str(probe.PREFIX)
    assert probe.TAIL.isascii()


def test_the_render_puts_three_rows_and_a_date_in_the_block(probe):
    """What lands in the note: a row per call, the model id, and the date."""
    rows = [probe.usage_of(CONTROL), probe.usage_of(WRITE), probe.usage_of(READ)]
    wrapped = [{"probe": name, "cache_point": cache, "usage": usage,
                "stop_reason": "end_turn", "wall_seconds": 1.0}
               for (name, cache), usage in zip(probe.PROBES, rows)]
    block = probe.render(wrapped, model_id="test-model", prefix_chars=26060,
                         date="2026-08-16")
    assert "2026-08-16" in block
    assert "test-model" in block
    for name in ("control", "write", "read"):
        assert f"| {name} |" in block
    assert "(absent)" in block


def test_the_probe_names_itself_as_not_a_gate(probe):
    """The distinction from `check_bedrock_logging.py` is in the file, not only in a commit.

    Someone finding this script next to the gate will assume it is one unless the file says
    otherwise, and the consequence of the assumption is a probe treated as a precondition.
    """
    source = TOOL.read_text(encoding="utf-8")
    assert "not a gate" in source
    assert "check_bedrock_logging.py" in source


def test_the_probe_says_that_converse_direct_is_confined_to_it(probe):
    """And that the second content block belongs in `bedrock.invoke()` if caching is adopted."""
    source = TOOL.read_text(encoding="utf-8")
    assert "confined to this probe" in source
    assert "bedrock.invoke()" in source


def test_the_unmeasured_minimum_is_recorded_with_its_condition(probe):
    """The cacheable-prefix floor is not measured, and the file says when it must be.

    An unmeasured quantity recorded without its condition is an assumption; recorded with the
    condition that makes it irrelevant, it is a scoped omission. The condition is that the
    cached prefix is the auditor template, and it breaks for anything shorter.
    """
    source = TOOL.read_text(encoding="utf-8")
    assert "deliberately not measured" in source
    assert "shorter than the auditor template" in source


def test_nothing_under_src_imports_the_probe(probe):
    """It is not a transport path, and the check is structural rather than a promise."""
    for path in (ROOT / "src").rglob("*.py"):
        assert "probe_prompt_cache" not in path.read_text(encoding="utf-8"), path
