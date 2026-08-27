"""Tests for `tools/probe_call_variance.py` — the arithmetic, and what the row may carry.

**No AWS call, and no test of one.** The probe's value is a measurement of a live API and a
test against a fake client would assert what the fake was written to say. So what is tested
here is the parts that are ours: the spread statistics (where the interesting case is a layer
that some draws do not mention at all), that a draw which will not load is recorded as variance
rather than aborting the run, that the row carries no prompt or completion text, and that the
expected-prompt check reads the arm's own record instead of a constant.

The probe is not a gate and not a production path (see its module docstring), so there are no
mutations for it and nothing here asserts that an arm refuses. It is deliberately **outside**
`tests/mutations/run.py`'s `TEST_FILES`: that list is the denominator of every kill count and
adding a file to it makes every recorded count a count of something else (CLAUDE.md, mutation
gate). A probe with no mutations has nothing to contribute to it either way.

    python3 -m pytest tests/test_probe_call_variance.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOOL = ROOT / "tools" / "probe_call_variance.py"


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("_probe_call_variance", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class FakeResponse:
    """The four `Response` members `draw_summary()` reads, and nothing else.

    Not `bedrock.Response`: constructing one needs a model id and a resolution, which would put
    a model id in this file for no measurement's sake (DESIGN §10 A2). What is being exercised
    is the reduction of a reply to a publishable row, and a reply is text plus counts.
    """

    text: str
    completion_tokens: int = 100
    prompt_tokens: int = 14071
    wall_seconds: float = 12.3456
    stop_reason: str = "end_turn"


def rule_file(*rules: tuple[str, str, str]) -> str:
    """A loadable `rules/es.yaml` from (rule_id, layer, phi_type) triples.

    The matchers are fixed and trivial. What varies between draws in the real measurement is
    which rules exist, and a test that also varied the patterns would be testing the rule
    loader, which `tests/test_rules.py` already does.
    """
    lines = ["version: 1", "lang: es", "rules:"]
    for rule_id, layer, phi_type in rules:
        lines += [f"  - rule_id: {rule_id}", f"    layer: {layer}",
                  f"    phi_type: {phi_type}"]
        if layer == "context_cue":
            lines += ['    cue: ["Dr."]', "    then: capitalised_words"]
        else:
            lines.append(r"    pattern: '\b\d{4}\b'")
        lines.append("    score: 0.8")
    return "\n".join(lines) + "\n"


# ── the spread statistics ────────────────────────────────────────────────────


def loaded_row(draw: int, ids: list[str], layers: dict[str, int]) -> dict:
    """A `draw_summary()` row, built directly so the statistics can be tested alone."""
    return {"draw": draw, "outcome": "loaded", "error_type": None, "rules": len(ids),
            "rule_ids": sorted(ids), "layers": layers, "phi_types": {},
            "response_chars": 100, "response_sha256": "sha256:" + "0" * 64,
            "completion_tokens": 100, "prompt_tokens": 14071, "wall_seconds": 1.0,
            "stop_reason": "end_turn"}


def test_the_pair_count_is_every_unordered_pair(probe):
    """Five draws give ten comparisons, which is the n the note's Jaccard range is over."""
    rows = [loaded_row(i, [f"es:r{i}"], {"context_cue": 1}) for i in range(1, 6)]
    assert len(probe.spread(rows)["pairs"]) == 10


def test_an_id_in_every_draw_is_separated_from_one_in_a_single_draw(probe):
    """The two counts are the finding: a stable core and a per-call fringe.

    A mean Jaccard alone cannot distinguish "every draw writes the same 20 rules plus 5 of its
    own" from "every draw writes 25 rules chosen from a pool of 50", and those licence
    different readings of an iteration-to-iteration difference.
    """
    rows = [
        loaded_row(1, ["es:core_a", "es:core_b", "es:only_1"], {"context_cue": 3}),
        loaded_row(2, ["es:core_a", "es:core_b", "es:only_2"], {"context_cue": 3}),
        loaded_row(3, ["es:core_a", "es:core_b"], {"context_cue": 2}),
    ]
    summary = probe.spread(rows)
    assert summary["distinct_rule_ids"] == 4
    assert summary["in_every_draw"] == 2
    assert summary["in_one_draw_only"] == 2
    assert (summary["rule_count_min"], summary["rule_count_max"]) == (2, 3)


def test_a_layer_absent_from_a_draw_ranges_from_zero(probe):
    """The range is over all draws, not over the draws that mentioned the layer.

    This is the case the statistic exists for. A draw that writes no `gazetteer` rule at all is
    the strongest evidence of variance in the layer distribution, and ranging only over the
    draws that named the layer would report `gazetteer: 3–3` — a stable layer, from data
    showing the opposite.
    """
    rows = [
        loaded_row(1, ["es:a", "es:b", "es:c"], {"context_cue": 1, "gazetteer": 2}),
        loaded_row(2, ["es:a", "es:b"], {"context_cue": 2}),
    ]
    measured = probe.spread(rows)["layer_ranges"]
    assert measured["gazetteer"] == [0, 2]
    assert measured["context_cue"] == [1, 2]


def test_the_phi_type_range_is_reported_and_does_not_move_on_a_rename(probe):
    """The name-free half. Two draws that name the same portfolio differently agree here.

    This is what the first run's numbers made necessary: a `rule_id` Jaccard of 0.24 alongside
    an `AGE` count of exactly 2 in every draw are both true, and only the second is about what
    the rules do. A summary carrying only the first invites the reading that the model barely
    agrees with itself.
    """
    rows = [
        {**loaded_row(1, ["es:age_label_cue", "es:phone_pattern"], {"context_cue": 2}),
         "phi_types": {"AGE": 1, "CONTACT": 1}},
        {**loaded_row(2, ["es:edad_cue", "es:phone_es"], {"context_cue": 2}),
         "phi_types": {"AGE": 1, "CONTACT": 1}},
    ]
    summary = probe.spread(rows)
    assert summary["jaccard_min"] == 0.0
    assert summary["phi_type_ranges"] == {"AGE": [1, 1], "CONTACT": [1, 1]}


def test_a_phi_type_absent_from_a_draw_ranges_from_zero(probe):
    """`ranges()` is one function for both distributions, so the zero rule holds for both."""
    rows = [
        {**loaded_row(1, ["es:a", "es:b"], {"context_cue": 2}),
         "phi_types": {"NAME": 1, "OTHER": 1}},
        {**loaded_row(2, ["es:a"], {"context_cue": 1}), "phi_types": {"NAME": 1}},
    ]
    assert probe.spread(rows)["phi_type_ranges"] == {"NAME": [1, 1], "OTHER": [0, 1]}


def test_a_format_failure_is_counted_and_kept_out_of_the_set_statistics(probe):
    """It has no rule set, and the denominator it does belong to is reported.

    "Four of five agreed closely" and "five of five did" are different results, so the run's
    total and the loaded count are both in the summary rather than one standing for the other.
    """
    rows = [
        loaded_row(1, ["es:a", "es:b"], {"context_cue": 2}),
        loaded_row(2, ["es:a", "es:b"], {"context_cue": 2}),
        {"draw": 3, "outcome": "format_failure", "error_type": "RuleError", "rules": None,
         "rule_ids": [], "layers": {}, "phi_types": {}, "response_chars": 12,
         "response_sha256": "sha256:" + "1" * 64, "completion_tokens": 4,
         "prompt_tokens": 14071, "wall_seconds": 1.0, "stop_reason": "max_tokens"},
    ]
    summary = probe.spread(rows)
    assert (summary["draws"], summary["loaded"], summary["format_failures"]) == (3, 2, 1)
    assert summary["jaccard_min"] == 1.0
    assert len(summary["pairs"]) == 1
    assert summary["in_every_draw"] == 2


def test_the_jaccard_of_two_empty_sets_is_one(probe):
    """Two identical nothings agree. A zero here would read as maximal disagreement."""
    assert probe.jaccard(set(), set()) == 1.0
    assert probe.jaccard({"a"}, {"a"}) == 1.0
    assert probe.jaccard({"a"}, {"b"}) == 0.0
    assert probe.jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_every_draw_failing_leaves_the_summary_defined(probe):
    """Nothing loaded is a result, and it must not raise while being reported.

    The whole point of not aborting on the first bad draw is to report a run of bad draws, and
    a `min()` over an empty sequence at the end would throw that away at the last step.
    """
    rows = [
        {"draw": i, "outcome": "format_failure", "error_type": "RuleError", "rules": None,
         "rule_ids": [], "layers": {}, "phi_types": {}, "response_chars": 3,
         "response_sha256": "sha256:" + "2" * 64, "completion_tokens": 1,
         "prompt_tokens": 14071, "wall_seconds": 1.0, "stop_reason": "end_turn"}
        for i in (1, 2)
    ]
    summary = probe.spread(rows)
    assert summary["loaded"] == 0
    assert summary["rule_count_min"] is None
    assert summary["in_every_draw"] == 0
    assert summary["layer_ranges"] == {}


# ── one draw ─────────────────────────────────────────────────────────────────


def test_a_loadable_draw_is_summarised_by_ids_and_distributions(probe, tmp_path):
    """The loader the arm uses, so a draw called loadable here is one the arm would score."""
    text = rule_file(("title_dr_prefix", "context_cue", "NAME"),
                     ("date_iso_pattern", "regex_checksum", "DATE"))
    row = probe.draw_summary(1, FakeResponse(text), lang="es", workdir=tmp_path / "1")
    assert row["outcome"] == "loaded"
    assert row["rules"] == 2
    assert row["rule_ids"] == ["es:date_iso_pattern", "es:title_dr_prefix"]
    assert row["layers"] == {"context_cue": 1, "regex_checksum": 1}
    assert row["phi_types"] == {"DATE": 1, "NAME": 1}
    assert row["completion_tokens"] == 100
    assert row["wall_seconds"] == 12.346


def test_an_unloadable_draw_is_variance_rather_than_an_exception(probe, tmp_path):
    """A fenced or truncated reply is a real outcome of a real call.

    Aborting would report the spread of the draws that happened to parse, which is a biased
    sample of the quantity being measured — and a format failure at the arm is a recorded
    outcome (`_write_failure`), not a crash, so the probe agreeing with it is the consistent
    choice as well as the honest one.
    """
    row = probe.draw_summary(2, FakeResponse("```yaml\nversion: 1\n"), lang="es",
                             workdir=tmp_path / "2")
    assert row["outcome"] == "format_failure"
    assert row["error_type"]
    assert row["rules"] is None
    assert row["rule_ids"] == []


def test_the_failure_row_carries_a_type_and_not_the_validators_message(probe, tmp_path):
    """`load_rules()` keeps the offending line out of what it raises; this adds to that care.

    The exception *type* is the publishable part. A message would be a second channel out of
    the loader whose safety rested on the loader having been careful, and CLAUDE.md's rule is
    that a message never carries a surface form regardless of which file assembled it.
    """
    row = probe.draw_summary(3, FakeResponse("not: a rule file\n"), lang="es",
                             workdir=tmp_path / "3")
    assert row["outcome"] == "format_failure"
    assert row["error_type"] == "RuleError"
    assert "message" not in row
    assert all(isinstance(value, (int, float, str, type(None), list, dict))
               for value in row.values())


def test_the_row_holds_no_prompt_or_completion_text(probe, tmp_path):
    """A closed key set, checked by name. The response is a length and a hash.

    Asserted against a literal rather than a `not in` scan, for `call_line()`'s reason: a new
    key is a decision about what may be published, and a test that only forbade the keys
    someone thought of would pass on the one they did not.
    """
    expected = {"draw", "outcome", "error_type", "rules", "rule_ids", "layers", "phi_types",
                "response_chars", "response_sha256", "completion_tokens", "prompt_tokens",
                "wall_seconds", "stop_reason"}
    text = rule_file(("title_dr_prefix", "context_cue", "NAME"))
    loaded = probe.draw_summary(1, FakeResponse(text), lang="es", workdir=tmp_path / "a")
    failed = probe.draw_summary(2, FakeResponse("nope\n"), lang="es", workdir=tmp_path / "b")
    assert set(loaded) == expected
    assert set(failed) == expected
    serialised = json.dumps([loaded, failed], ensure_ascii=False)
    assert "version: 1" not in serialised
    assert "capitalised_words" not in serialised


def test_no_draw_writes_outside_the_directory_it_was_given(probe, tmp_path):
    """`results/` is not involved, and the check is that the draw's file lands where told."""
    text = rule_file(("title_dr_prefix", "context_cue", "NAME"))
    workdir = tmp_path / "draw-1"
    probe.draw_summary(1, FakeResponse(text), lang="es", workdir=workdir)
    assert [p.name for p in workdir.iterdir()] == ["es.yaml"]
    assert list(tmp_path.iterdir()) == [workdir]


# ── the prompt the probe insists on ──────────────────────────────────────────


def write_log(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def test_the_expected_hash_is_read_from_the_arms_round_one_line(probe, tmp_path,
                                                                monkeypatch):
    """From the arm's own record, not a constant in the probe.

    A constant would be a second place the hash lives and the failure it invites is quiet: the
    arm's prompt moves, the constant does not, and the probe reports the variance of a call the
    arm never made while asserting that it did.
    """
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    log = tmp_path / "results" / "es-meddocan" / "R" / "sup-free" / "port-loop" / \
        "agent_calls.jsonl"
    write_log(log, [
        {"iteration": 1, "role": "auditor", "prompt_reference": {"text_sha256": "sha256:aa"}},
        {"iteration": 1, "role": "rule_author",
         "prompt_reference": {"text_sha256": "sha256:bb"}},
        {"iteration": 2, "role": "rule_author",
         "prompt_reference": {"text_sha256": "sha256:cc"}},
    ])
    assert probe.expected_prompt_sha256("es-meddocan") == "sha256:bb"


def test_a_log_with_no_round_one_rule_author_line_is_refused(probe, tmp_path, monkeypatch):
    """No recorded call means nothing to hold the probe to, and no default is invented."""
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    log = tmp_path / "results" / "es-meddocan" / "R" / "sup-free" / "port-loop" / \
        "agent_calls.jsonl"
    write_log(log, [{"iteration": 2, "role": "rule_author",
                     "prompt_reference": {"text_sha256": "sha256:cc"}}])
    with pytest.raises(probe.ProbeError):
        probe.expected_prompt_sha256("es-meddocan")


def test_a_missing_log_is_refused_and_names_the_flag(probe, tmp_path, monkeypatch):
    """The way out is stating the hash, and the refusal says so rather than making one up."""
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    with pytest.raises(probe.ProbeError) as caught:
        probe.expected_prompt_sha256("es-meddocan")
    assert "--expect-prompt-sha256" in str(caught.value)


def test_the_plan_says_whether_the_run_will_refuse(probe):
    """`--dry-run` has to show the mismatch, because that is the check it cannot perform later.

    A dry run that printed the two hashes and left the comparison to the reader would be a plan
    whose most important line is the one a person has to compute.
    """
    matched = "\n".join(probe.plan(corpus="es-meddocan", lang="es", model_id="m", calls=5,
                                   expected="sha256:aa", prompt_chars=53644,
                                   actual="sha256:aa"))
    mismatched = "\n".join(probe.plan(corpus="es-meddocan", lang="es", model_id="m", calls=5,
                                      expected="sha256:aa", prompt_chars=53644,
                                      actual="sha256:bb"))
    assert "prompt match   yes" in matched
    assert "NO — the run will refuse" in mismatched
    assert "does not score" in matched


def test_one_draw_is_refused(probe):
    """A spread needs two. One draw is what every arm already records."""
    with pytest.raises(probe.ProbeError):
        probe.main(["--corpus", "es-meddocan", "--lang", "es", "--model-id", "m",
                    "--calls", "1"])


# ── what the note gets ───────────────────────────────────────────────────────


def test_the_render_carries_the_date_the_hash_and_a_row_per_draw(probe):
    rows = [loaded_row(1, ["es:a", "es:b"], {"context_cue": 2}),
            loaded_row(2, ["es:a"], {"context_cue": 1})]
    block = probe.render(rows, probe.spread(rows), corpus="es-meddocan", lang="es",
                         model_id="test-model", prompt_sha256="sha256:abc",
                         prompt_chars=53644, date="2026-08-21")
    assert "2026-08-21" in block
    assert "test-model" in block
    assert "sha256:abc" in block
    assert block.count("| loaded |") == 2
    assert "1–2" in block


def test_the_render_warns_that_the_jaccard_is_not_behavioural(probe):
    """The caveat travels with the number, in the block, not only in DESIGN.

    A reader meets 0.2417 in this note before meeting anything else, and the sentence that stops
    them concluding "the model barely agrees with itself" has to be in the same paragraph. The
    two name-free tables are named there as the thing to read instead.
    """
    rows = [{**loaded_row(1, ["es:age_label_cue"], {"context_cue": 1}),
             "phi_types": {"AGE": 1}},
            {**loaded_row(2, ["es:edad_cue"], {"context_cue": 1}),
             "phi_types": {"AGE": 1}}]
    block = probe.render(rows, probe.spread(rows), corpus="es-meddocan", lang="es",
                         model_id="test-model", prompt_sha256="sha256:abc",
                         prompt_chars=53644, date="2026-08-21")
    assert "행동 지표가 아니다" in block
    assert "| phi_type | min | max |" in block
    assert "| `AGE` | 1 | 1 |" in block


def test_the_render_states_that_it_did_not_score(probe):
    """The note must not be readable as a leak-rate measurement, because it is not one."""
    rows = [loaded_row(1, ["es:a"], {"context_cue": 1}),
            loaded_row(2, ["es:a"], {"context_cue": 1})]
    block = probe.render(rows, probe.spread(rows), corpus="es-meddocan", lang="es",
                         model_id="test-model", prompt_sha256="sha256:abc",
                         prompt_chars=53644, date="2026-08-21")
    assert "채점하지 않는다" in block
    assert "DESIGN §3" in block


# ── structural ───────────────────────────────────────────────────────────────


def test_nothing_under_src_imports_the_probe():
    """It is not a transport path and it is not an arm, and the check is structural."""
    for path in (ROOT / "src").rglob("*.py"):
        assert "probe_call_variance" not in path.read_text(encoding="utf-8"), path


def test_the_probe_names_itself_as_not_a_gate_and_not_an_arm():
    """Someone finding it beside `run_loop.py` will assume it runs a round unless told."""
    source = TOOL.read_text(encoding="utf-8")
    assert "not a gate" in source
    assert "makes no arm" in source
    assert "probe_prompt_cache.py" in source


def test_the_probe_records_why_it_does_not_score():
    """Not scoring is a rule with a reason, and the reason has to be findable from the file.

    Left only in DESIGN, the omission reads as an unfinished feature to whoever opens the tool
    and the obvious improvement is to add scoring. The file says what that would cost.
    """
    source = TOOL.read_text(encoding="utf-8")
    assert "does not score" in source
    assert "dev overfitting" in source
    assert "unmeasured" in source


def test_the_probe_does_not_touch_delta():
    """δ, k and the ceiling are pre-registered; this measurement is downstream of them.

    Asserted rather than trusted because the file is *about* the threshold's interpretation,
    which is one step from editing it, and DESIGN §3 forbids a change to δ while the arm runs.
    """
    source = TOOL.read_text(encoding="utf-8")
    assert "does not choose δ, does not touch δ" in source
    for name in ("DELTA", "delta", "termination", "converged"):
        assert name not in source.split('"""', 2)[2], name


def test_the_probe_is_outside_the_mutation_test_files():
    """Adding it would make every kill count a count of a different denominator.

    `tests/mutations/run.py`'s list is that denominator (CLAUDE.md, mutation gate). This file
    tests a probe with no mutations, so its absence costs no coverage — and the assertion is
    here so that a later "why isn't this in the list" is answered where it is asked.
    """
    sys.path.insert(0, str(ROOT / "tests" / "mutations"))
    import run as harness

    TEST_FILES = harness.TEST_FILES
    assert "tests/test_probe_call_variance.py" not in TEST_FILES
    assert "tests/test_probe_prompt_cache.py" not in TEST_FILES
