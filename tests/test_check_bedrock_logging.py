"""Tests for `tools/check_bedrock_logging.py` — the gate's record, and where it lands.

No AWS call. `check_region` is patched; what is tested is everything around it, which is
where the failure modes are: an unreadable setting read as a clean one, a record appended
under the wrong section, a record written when logging was found *enabled*.

The last is the one worth stating. The gate keys on a date, so a row saying "enabled"
would open the gate it was supposed to shut. The tool therefore writes nothing on a
positive finding, and that is asserted here rather than trusted.

    python3 -m pytest tests/test_check_bedrock_logging.py -q
"""
from __future__ import annotations

import ast
import datetime
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOOL = ROOT / "tools" / "check_bedrock_logging.py"

SECTION = "## 3. Bedrock model-invocation logging — measured state"

TODAY = datetime.date.today().isoformat()


def load_in(tmp_path: Path, body: str = "existing prose.\n"):
    """The real tool, in a miniature tree whose `compliance.md` we control.

    Copied rather than stubbed: `ROOT` comes from the module's own `__file__`, so a copy in
    a temporary tree reads that tree's files and the code under test is the code that runs.
    """
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "notes").mkdir(parents=True, exist_ok=True)
    shutil.copy(TOOL, tmp_path / "tools" / "check_bedrock_logging.py")
    (tmp_path / "docs" / "notes" / "compliance.md").write_text(
        f"# Compliance\n\n{SECTION}\n\n{body}\n## 4. What still has to hold\n\nbody\n",
        encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        f"_tool_{tmp_path.name}", tmp_path / "tools" / "check_bedrock_logging.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compliance_of(tmp_path: Path) -> str:
    return (tmp_path / "docs" / "notes" / "compliance.md").read_text(encoding="utf-8")


def clean(regions):
    return [(r, "none") for r in regions]


# ─── the record lands inside §3 ──────────────────────────────────────────────

def test_a_clean_check_appends_a_dated_block_inside_section_three(tmp_path,
                                                                 monkeypatch):
    tool = load_in(tmp_path)
    monkeypatch.setattr(tool, "check_all", lambda: clean(tool.REGIONS))
    assert tool.main([]) == 0
    text = compliance_of(tmp_path)
    start = text.index(SECTION)
    end = text.index("## 4.")
    assert f"**Gate check {tool.today()}**" in text[start:end]


def test_the_record_is_not_appended_at_the_end_of_the_file(tmp_path, monkeypatch):
    """A block after the last section would satisfy a date search while sitting under
    whatever section happens to be last."""
    tool = load_in(tmp_path)
    monkeypatch.setattr(tool, "check_all", lambda: clean(tool.REGIONS))
    tool.main([])
    text = compliance_of(tmp_path)
    assert text.index(f"**Gate check {tool.today()}**") < text.index("## 4.")


def test_every_region_appears_in_the_record(tmp_path, monkeypatch):
    tool = load_in(tmp_path)
    monkeypatch.setattr(tool, "check_all", lambda: clean(tool.REGIONS))
    tool.main([])
    text = compliance_of(tmp_path)
    for region in tool.REGIONS:
        assert f"| {region} |" in text


def test_existing_rows_are_not_overwritten(tmp_path, monkeypatch):
    """compliance.md §3: the paper's claim is about the state during the runs, so a single
    current value is not sufficient evidence."""
    tool = load_in(tmp_path, "**Gate check 2026-08-06** (`x`):\n\nolder table here.\n")
    monkeypatch.setattr(tool, "check_all", lambda: clean(tool.REGIONS))
    tool.main([])
    text = compliance_of(tmp_path)
    assert "2026-08-06" in text
    assert tool.today() in text


def test_running_twice_appends_once(tmp_path, monkeypatch):
    tool = load_in(tmp_path)
    monkeypatch.setattr(tool, "check_all", lambda: clean(tool.REGIONS))
    tool.main([])
    tool.main([])
    assert compliance_of(tmp_path).count(f"**Gate check {tool.today()}**") == 1


# ─── enabled logging writes nothing ──────────────────────────────────────────

def test_enabled_logging_fails_and_appends_no_record(tmp_path, monkeypatch):
    """The load-bearing one. A dated row saying "enabled" would open the gate that keys on
    the date, so the finding has to be the *absence* of a record."""
    tool = load_in(tmp_path)
    before = compliance_of(tmp_path)
    monkeypatch.setattr(tool, "check_all", lambda: [
        ("us-east-1", "enabled: s3Config, textDataDeliveryEnabled"),
        *clean(tool.REGIONS[1:]),
    ])
    assert tool.main([]) == 1
    assert compliance_of(tmp_path) == before


def test_enabled_logging_leaves_the_gate_shut(tmp_path, monkeypatch):
    tool = load_in(tmp_path)
    monkeypatch.setattr(tool, "check_all",
                        lambda: [("us-east-1", "enabled: s3Config"), *clean(tool.REGIONS[1:])])
    tool.main([])
    assert not tool.checked_today()


def test_one_enabled_region_out_of_six_is_enough_to_fail(tmp_path, monkeypatch):
    """Per-region setting: a clean us-east-1 says nothing about eu-central-1."""
    tool = load_in(tmp_path)
    monkeypatch.setattr(tool, "check_all", lambda: [
        *clean(tool.REGIONS[:-1]), (tool.REGIONS[-1], "enabled: cloudWatchConfig"),
    ])
    assert tool.main([]) == 1


# ─── an unreadable setting is not a clean one ────────────────────────────────
#
# These drive `check_region` through a fake `boto3.client` rather than patching
# `check_region` or `check_all` out. The distinction is not stylistic: the first version of
# this file patched `check_all`, so the region-level error path — the one place an IAM
# denial is turned into a refusal — was never executed by any test. The mutation
# `the_logging_check_reports_an_unreadable_setting_as_clean` SURVIVED, which is how that
# was found. A test that patches the function containing the guarantee cannot test it.


class FakeBedrock:
    """A `bedrock` client returning one canned answer, or raising one canned error."""

    def __init__(self, response=None, error=None):
        self._response = response if response is not None else {}
        self._error = error

    def get_model_invocation_logging_configuration(self):
        if self._error is not None:
            raise self._error
        return self._response


def denial():
    """The real `ClientError` an IAM denial raises. `cloudtrail:DescribeTrails` already
    returns exactly this for this principal (`compliance.md` §3), so it is not imagined."""
    from botocore.exceptions import ClientError

    return ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}},
        "GetModelInvocationLoggingConfiguration",
    )


def serving(monkeypatch, fake):
    """Point `boto3.client` at `fake`. `check_region` imports boto3 inside the function, so
    the attribute on the real module is what it resolves."""
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: fake)


def test_an_unreadable_region_raises_rather_than_reporting_it_clean(tmp_path, monkeypatch):
    """The load-bearing refusal. A caller who cannot read the setting cannot report that it
    is off, and an unknown state is not an absent one."""
    tool = load_in(tmp_path)
    serving(monkeypatch, FakeBedrock(error=denial()))
    with pytest.raises(tool.LoggingCheckError) as e:
        tool.check_region("us-east-1")
    assert "us-east-1" in str(e.value)


def test_an_unreadable_region_fails_the_run_and_appends_no_record(tmp_path, monkeypatch):
    """Failing the run and still writing the row would leave the gate open anyway."""
    tool = load_in(tmp_path)
    before = compliance_of(tmp_path)
    serving(monkeypatch, FakeBedrock(error=denial()))
    assert tool.main([]) == 2
    assert compliance_of(tmp_path) == before
    assert not tool.checked_today()


def test_a_transport_failure_is_also_not_a_clean_result(tmp_path, monkeypatch):
    """`BotoCoreError` as well as `ClientError`: an endpoint that could not be reached told
    us nothing about the setting either."""
    from botocore.exceptions import EndpointConnectionError

    tool = load_in(tmp_path)
    serving(monkeypatch,
            FakeBedrock(error=EndpointConnectionError(endpoint_url="https://bedrock")))
    with pytest.raises(tool.LoggingCheckError):
        tool.check_region("eu-west-1")


def test_a_region_with_no_logging_config_reads_clean(tmp_path, monkeypatch):
    """Bedrock omits the key entirely when nothing is configured, which is why absence
    rather than a falsy value is the test."""
    tool = load_in(tmp_path)
    serving(monkeypatch, FakeBedrock(response={"ResponseMetadata": {}}))
    assert tool.check_region("us-east-1") == ("us-east-1", tool.CLEAN)


def test_a_configured_destination_is_reported_enabled_and_named(tmp_path, monkeypatch):
    """The person reading this has to go and find the bucket, so the finding names it. No
    prompt text is involved in any of these fields."""
    tool = load_in(tmp_path)
    serving(monkeypatch, FakeBedrock(response={"loggingConfig": {
        "s3Config": {"bucketName": "b"}, "textDataDeliveryEnabled": True}}))
    region, state = tool.check_region("us-east-1")
    assert state.startswith("enabled:")
    assert "s3Config" in state and "textDataDeliveryEnabled" in state


def test_check_all_stops_at_the_first_unreadable_region(tmp_path, monkeypatch):
    """A partial sweep is not a clean sweep — the remaining regions are unmeasured, not
    fine."""
    tool = load_in(tmp_path)
    seen = []

    def client_for(region):
        # The error goes on the API call, not on client construction — construction happens
        # outside `check_region`'s try block, so raising there would escape untranslated and
        # this test would pass on the wrong exception.
        seen.append(region)
        bad = region == tool.REGIONS[1]
        return FakeBedrock(error=denial()) if bad else FakeBedrock(response={})

    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: client_for(k["region_name"]))
    with pytest.raises(tool.LoggingCheckError):
        tool.check_all()
    assert seen == list(tool.REGIONS[:2]), "the sweep continued past an unreadable region"


# ─── --status reads and never writes ─────────────────────────────────────────

def test_status_reports_missing_without_writing(tmp_path):
    tool = load_in(tmp_path)
    before = compliance_of(tmp_path)
    assert tool.main(["--status"]) == 1
    assert compliance_of(tmp_path) == before


def test_status_reports_present_after_a_check(tmp_path, monkeypatch):
    tool = load_in(tmp_path)
    monkeypatch.setattr(tool, "check_all", lambda: clean(tool.REGIONS))
    tool.main([])
    assert tool.main(["--status"]) == 0


def test_status_makes_no_api_call(tmp_path, monkeypatch):
    """It must work on a machine with no credentials; the gate's own query is a file read."""
    tool = load_in(tmp_path)

    def fail(*a, **k):
        raise AssertionError("--status called the API")

    monkeypatch.setattr(tool, "check_region", fail)
    monkeypatch.setattr(tool, "check_all", fail)
    tool.main(["--status"])


# ─── what counts as a record ─────────────────────────────────────────────────

def test_a_date_outside_section_three_is_not_a_record(tmp_path):
    tool = load_in(tmp_path)
    path = tmp_path / "docs" / "notes" / "compliance.md"
    path.write_text(
        path.read_text(encoding="utf-8")
        + f"\n## 5. Elsewhere\n\n**Gate check {tool.today()}** (`x`):\n",
        encoding="utf-8")
    assert not tool.checked_today()


def test_a_bare_date_is_not_a_record(tmp_path):
    """§2 of the real file quotes guidance with dates in it. A date is not a measurement, and
    the gate keying on a loose date search would be opened by prose."""
    tool = load_in(tmp_path, f"Measured {TODAY}: all clear.\n")
    assert not tool.checked_today()


def test_a_missing_section_is_refused_rather_than_appended_at_the_end(tmp_path):
    """If §3 is gone, the block has nowhere correct to go — and EOF is not a fallback."""
    tool = load_in(tmp_path)
    path = tmp_path / "docs" / "notes" / "compliance.md"
    path.write_text("# Compliance\n\n## 1. Something else\n\nbody\n", encoding="utf-8")
    with pytest.raises(tool.LoggingCheckError) as e:
        tool.append_record(clean(tool.REGIONS), tool.today())
    assert "nowhere to go" in str(e.value)


def test_recorded_dates_is_empty_rather_than_raising_on_a_malformed_file(tmp_path):
    """`--status` and the client's gate both call this, and neither has a recovery path for
    a parse error — an empty list is "no record", which is the right answer."""
    tool = load_in(tmp_path)
    (tmp_path / "docs" / "notes" / "compliance.md").write_text("nothing", encoding="utf-8")
    assert tool.recorded_dates() == []


# ─── the tool reads and does not configure ───────────────────────────────────

def test_the_tool_only_reads_the_logging_configuration():
    """A repository script that could turn logging off could also turn it on."""
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.AST)
             and getattr(n.func, "attr", None)}
    assert "put_model_invocation_logging_configuration" not in calls
    assert "delete_model_invocation_logging_configuration" not in calls
    assert "get_model_invocation_logging_configuration" in calls


def test_the_regions_include_every_member_of_the_inference_profile(tmp_path):
    """Measured by `GetInferenceProfile`, not inferred from the `us.` prefix
    (`docs/notes/baseline-model-family.md`, 2026-08-08). A cross-region-routed call can be
    served from any of the three, so a clean us-east-1 is not an answer about the other two."""
    tool = load_in(tmp_path)
    assert {"us-east-1", "us-east-2", "us-west-2"} <= set(tool.REGIONS)


def test_no_corpus_path_or_surface_appears_in_the_tool():
    """CLAUDE.md: this file's output is committed evidence."""
    src = TOOL.read_text(encoding="utf-8")
    for token in ("data/raw", "sealed/", "surrogate"):
        assert token not in src
