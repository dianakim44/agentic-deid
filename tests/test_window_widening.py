"""The window widened twice, and neither widening moved an arm that was already frozen.

Two events, and the second one is why this file's structure is worth keeping. On
2026-08-12 the window went from two files to three (`auditor.md`); on 2026-09-01 it went
from three to six (`port-multi`'s `profiler.md`, `mapper.md`, `lexicon_builder.md`, DESIGN
§6.7.1). The first widening left three arms behind at two files; the second left those
three *and* `port-loop` behind at three. So "the frozen window" is not one width, and a
test written as "the old window is two files" would have quietly stopped covering the arm
that needed it most — `port-loop`, whose 8-round call log is the thing `port-multi` is
compared against.

The mechanism is the same both times and is tested once: `recorded_window_fields()` reads
the field list off the record. What is added per widening is the *evidence* that the
records on disk did not move — see "the second widening" at the end of this file.

─── the first widening, 2026-08-12 ─────────────────────────────────────────

`docs/prompts/auditor.md` joined `WINDOW_FILES` when DESIGN §5.5 made the Auditor prompt
part of what a run is held to. Three arms had already frozen a two-file window
(`port-oneshot`, `port-oneshot-nofence`, `port-human`), and the requirement on the widening
is that none of their records change and none of them acquire a third hash.

**Why this needs its own file, against the real records rather than fixtures.** Every other
test in this suite builds its freeze record under a redirected `ROOT`, which is right for
testing the writer and useless for this question: the records that must not move are the
committed ones, and a fixture cannot fail to be retroactively rewritten. So these tests read
`results/.../window_freeze.json` off the working tree and assert on what is there. The cost
is that they are coupled to committed data; that coupling is the test.

**The two ways the widening could have reached backwards, both of them silent.**

  1. `window_drift()` comparing a two-file record against a three-field tuple. Every frozen
     arm reports permanent drift on `auditor_sha256` — a file its call never saw — and the
     honest reading of that report is that the record is wrong. Closed by
     `recorded_window_fields()`, which reads the field list off the record's own `files`.
  2. `human_arm.log_line()` writing `**window_hashes()` with no argument. A revived
     `port-human` line would carry three hashes beside a two-hash freeze record, claiming a
     window that never applied (DESIGN §6.3). Closed by `HUMAN_WINDOW_FILES`.

The second one surfaced as 39 failing tests rather than as a silent record change, which is
only because `log_line()` asserts its own field order against `FIELDS`. That assertion was
written for a different reason and caught this; a guard that catches the thing it was not
aimed at is not a guard to rely on, hence these tests.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.orchestrate import window_drift                          # noqa: E402
from src.porting.human_arm import HUMAN_WINDOW_FILES              # noqa: E402
from src.porting.human_arm import window_drift as human_drift     # noqa: E402
from src.sample import (                                          # noqa: E402
    AUDITOR_TEMPLATE, LEXICON_BUILDER_TEMPLATE, MAPPER_TEMPLATE, PROFILER_TEMPLATE,
    PROMPT_TEMPLATE, WINDOW_FILES, WINDOW_HASH_FIELDS,
    recorded_window_fields, window_hashes,
)

#: The window as it was before 2026-08-12, in order. Written out rather than sliced from
#: `WINDOW_FILES`, because a slice would follow the constant and this is the fact the
#: constant moved away from.
OLD_WINDOW = ["docs/prompts/rule_author.md", "config/sampling.yaml"]

#: The arms frozen under it. All three, by name: a test that discovered them by walking
#: `results/` would pass on an empty tree.
FROZEN_ARMS = ("port-oneshot", "port-oneshot-nofence", "port-human")

#: The window between 2026-08-12 and 2026-09-01, in order. Written out for the same reason
#: `OLD_WINDOW` is: it is what a record claims, not what the constant now says.
THREE_FILE_WINDOW = ["docs/prompts/rule_author.md", "docs/prompts/auditor.md",
                     "config/sampling.yaml"]

#: The arm frozen under it, by name. One arm, and it is the comparison baseline for
#: `port-multi` — which is why "only one arm, not worth parametrising" is the wrong reading.
THREE_FILE_ARMS = ("port-loop",)

#: The three files added on 2026-09-01, and their fields. Listed so a record can be checked
#: for their *absence*; deriving them as `set(WINDOW_FILES) - set(THREE_FILE_WINDOW)` would
#: make the check follow the next widening and stop being about this one.
NEW_FILES = (PROFILER_TEMPLATE, MAPPER_TEMPLATE, LEXICON_BUILDER_TEMPLATE)
NEW_FIELDS = ("profiler_sha256", "mapper_sha256", "lexicon_builder_sha256")

ARM = ("es-meddocan", "R", "sup-free")


def freeze_record(porting: str) -> dict:
    path = ROOT / "results" / "es-meddocan" / "R" / "sup-free" / porting / \
        "window_freeze.json"
    assert path.is_file(), f"{porting}: no committed freeze record at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


# ─── the committed records still name two files ─────────────────────────────

@pytest.mark.parametrize("porting", FROZEN_ARMS)
def test_a_frozen_arm_still_names_the_window_it_was_frozen_under(porting):
    """The record is the claim, and the claim was about two files."""
    assert freeze_record(porting)["files"] == OLD_WINDOW


@pytest.mark.parametrize("porting", FROZEN_ARMS)
def test_a_frozen_arm_carries_no_auditor_hash(porting):
    """DESIGN §6.3, one file over: a hash added after the fact attests to nothing.

    There was no Auditor when these calls were made. A record carrying `auditor_sha256`
    would say the run was held to a prompt that did not exist, and it would say it in the
    same shape as a record that is true.
    """
    record = freeze_record(porting)
    assert "auditor_sha256" not in record
    assert AUDITOR_TEMPLATE not in record["files"]


@pytest.mark.parametrize("porting", FROZEN_ARMS)
def test_the_fields_compared_are_the_two_the_record_holds(porting):
    """The unit under test. `recorded_window_fields()` is what makes the widening local."""
    assert recorded_window_fields(freeze_record(porting)) == [
        "prompt_sha256", "sampling_sha256"]


def test_the_files_list_outranks_the_hash_fields_present():
    """`files` is the authority, and on the committed records nothing distinguishes the two.

    Every record on disk names two files *and* holds exactly two hash fields, so reading the
    `files` list and reading the keys give the same answer — which is why a mutation removing
    the `files` branch survived the whole suite (`tests/mutations/README.md`). This is the
    synthetic record where they differ: two files named, three hashes present, which is what a
    line written by a widened writer against an old freeze record would look like. The `files`
    list is what the record *claims*; a stray field is not a claim.
    """
    record = {
        "files": OLD_WINDOW,
        "prompt_sha256": "sha256:aa",
        "auditor_sha256": "sha256:bb",
        "sampling_sha256": "sha256:cc",
    }
    assert recorded_window_fields(record) == ["prompt_sha256", "sampling_sha256"]


def test_a_record_with_no_files_list_falls_back_to_the_fields_it_holds():
    """`port-human`'s early shape, before the `files` key existed.

    The fallback is not equivalent to the `files` branch — see the test above — it is what
    there is when the record makes no claim at all. Returning today's three fields for such a
    record would compare it against a file it cannot possibly have hashed.
    """
    record = {"prompt_sha256": "sha256:aa", "sampling_sha256": "sha256:cc"}
    assert recorded_window_fields(record) == ["prompt_sha256", "sampling_sha256"]


# ─── drift is reported against the recorded window, not today's ─────────────

@pytest.mark.parametrize("porting", ("port-oneshot", "port-oneshot-nofence"))
def test_no_frozen_agent_arm_drifts_on_a_file_it_never_saw(porting):
    """**The failure this is aimed at**, and it is a false alarm rather than a crash.

    `auditor.md` is on disk and hashes to something; a comparison against today's three
    fields reports `['auditor_sha256']` for every frozen arm, forever. The report reads as
    "the record and the files disagree about a call that has already happened", which is
    `window_drift()`'s documented meaning and is false here.

    Asserted as the absence of one field rather than as an empty list, because empty is not
    true of this tree: `port-oneshot` does drift on `prompt_sha256`, since the prompt was
    revised for `port-oneshot-nofence` and only that arm's record holds the newer hash
    (`docs/notes/window-freeze-history.md`). A test demanding emptiness would be deleted
    rather than fixed.
    """
    assert "auditor_sha256" not in window_drift(*ARM, porting)


def test_the_retired_arm_drifts_on_the_prompt_and_on_nothing_else():
    """`port-human`'s own drift check, which reads its own two-file window.

    The `prompt_sha256` drift is real and is not this widening's doing — the RuleAuthor
    prompt has moved seven times since this arm was frozen. It is asserted exactly so that
    a third field appearing beside it fails here rather than being read as one more prose
    revision.
    """
    assert human_drift(*ARM) == ["prompt_sha256"]


def test_the_drift_check_would_have_reported_the_third_file_before_the_fix():
    """The counterfactual, measured rather than described.

    Without `recorded_window_fields()` the comparison is against `window_hashes()`' keys,
    and this is what every frozen arm would have reported from 2026-08-12 onward. Computed
    here so the claim in this file's docstring is a test result.
    """
    for porting in FROZEN_ARMS:
        frozen = freeze_record(porting)
        naive = [field for field in window_hashes()
                 if frozen.get(field) != window_hashes()[field]]
        assert "auditor_sha256" in naive


# ─── the retired arm's window is its own, and stays two files ───────────────

def test_the_human_arm_names_the_window_its_record_names():
    """Named in the module rather than inherited, because inheriting fails silently.

    The widening would have added a third hash to this arm's lines with no edit to
    `human_arm.py` and nothing to review.
    """
    assert HUMAN_WINDOW_FILES == tuple(OLD_WINDOW)
    assert freeze_record("port-human")["files"] == list(HUMAN_WINDOW_FILES)


def test_the_human_arms_lines_would_carry_two_hashes_and_not_three():
    """Checked through `window_hashes()` rather than through `log_line()`.

    `tests/test_human_arm.py` covers the line; what is checked here is that the arm's own
    window resolves to the two fields its freeze record holds, which is the property the
    line depends on.
    """
    assert set(window_hashes(HUMAN_WINDOW_FILES)) == {
        "prompt_sha256", "sampling_sha256"}


def test_the_human_window_is_a_strict_subset_of_todays():
    """Not merely different. A file dropped from `WINDOW_FILES` would leave this arm hashing
    something no current arm hashes, and the two records would stop being comparable at
    all — which is the reason §6.3 keeps `sampling_sha256` on an arm that uses none of it.
    """
    assert set(HUMAN_WINDOW_FILES) < set(WINDOW_FILES)


# ─── the widening itself ───────────────────────────────────────────────────

def test_both_widenings_added_and_neither_replaced_or_reordered():
    """Order matters, and the two widenings did not have the same shape.

    `recorded_window_fields()` returns fields in window order and `human_arm.FIELDS` fixes
    the tail of every log line, so a reordering would make two lines differ in a diff without
    differing in content.

    The first widening *inserted* — `auditor.md` went between the prompt and the config, so
    `OLD_WINDOW` is not a prefix of the three-file window and is asserted as a deletion
    instead. The second *appended*, which is why the three-file window is a prefix of today's
    six. Both are written out rather than described, because "the window only grows" is the
    claim every frozen record depends on and it is not true in the same way twice.
    """
    assert THREE_FILE_WINDOW[:1] + THREE_FILE_WINDOW[2:] == OLD_WINDOW
    assert THREE_FILE_WINDOW[1] == AUDITOR_TEMPLATE
    assert list(WINDOW_FILES[:3]) == THREE_FILE_WINDOW
    assert list(WINDOW_FILES[3:]) == list(NEW_FILES)


def test_every_window_file_has_a_field_and_every_field_a_file():
    """A file in the window with no field is hashed into `files` and compared against
    nothing; a field with no file is a name a reader greps for and never finds."""
    assert set(WINDOW_HASH_FIELDS) == set(WINDOW_FILES)
    assert len(set(WINDOW_HASH_FIELDS.values())) == len(WINDOW_FILES)


def test_every_window_file_is_on_disk_where_the_hash_looks():
    for name in WINDOW_FILES:
        assert (ROOT / name).is_file(), name


# ─── the second widening, 2026-09-01: port-loop's three files stay three ────

#: SHA-256 of `port-loop`'s committed freeze record. Pinned rather than described.
#:
#: Every other assertion in this file is about a *property* of a record, and a property is
#: what a careless rewrite preserves: a script that reformatted the JSON, re-sorted the keys,
#: or refreshed `generated` would keep `files` at three names and pass every test above. The
#: bytes are the claim — DESIGN §6.3 — so the bytes are what is checked. If this fails, the
#: question is not "which field moved" but "why was a frozen record written to at all".
PORT_LOOP_FREEZE_SHA256 = \
    "de3c96781155a733f1072b00a7b047ef961436c5b4ebd7b83b5eee237e78b6de"


@pytest.mark.parametrize("porting", THREE_FILE_ARMS)
def test_a_three_file_arm_still_names_the_window_it_was_frozen_under(porting):
    """`port-loop` ran under three files and its record must keep saying three."""
    assert freeze_record(porting)["files"] == THREE_FILE_WINDOW


@pytest.mark.parametrize("porting", THREE_FILE_ARMS + FROZEN_ARMS)
@pytest.mark.parametrize("field", NEW_FIELDS)
def test_no_frozen_record_acquired_a_port_multi_hash(porting, field):
    """None of the four frozen arms ever read a `port-multi` prompt.

    Parametrised over both widths together because the requirement does not distinguish
    them: two files or three, a record carrying `profiler_sha256` says the run was held to a
    prompt whose agent it never called, and says it in the same shape as a record that is
    true.
    """
    record = freeze_record(porting)
    assert field not in record


@pytest.mark.parametrize("porting", THREE_FILE_ARMS)
def test_the_fields_compared_are_the_three_the_record_holds(porting):
    """The unit under test, at the second width. `recorded_window_fields()` again."""
    assert recorded_window_fields(freeze_record(porting)) == [
        "prompt_sha256", "auditor_sha256", "sampling_sha256"]


@pytest.mark.parametrize("porting", THREE_FILE_ARMS)
def test_a_three_file_arm_drifts_on_no_file_it_never_saw(porting):
    """The false alarm this widening could have produced, asserted field by field.

    All three new prompts are on disk and hash to something, so a comparison against today's
    six fields reports three drifts for `port-loop` forever, and the report reads as "the
    record and the files disagree about calls that have already happened" — which is
    `window_drift()`'s documented meaning and is false here.

    Not asserted as an empty list: `port-loop` may legitimately drift on `prompt_sha256`, for
    the same reason `port-oneshot` does. A test demanding emptiness would be deleted rather
    than fixed the first time the RuleAuthor prompt was revised.
    """
    drift = window_drift(*ARM, porting)
    for field in NEW_FIELDS:
        assert field not in drift


@pytest.mark.parametrize("porting", THREE_FILE_ARMS)
def test_the_drift_check_would_have_reported_all_three_before_the_fix(porting):
    """The counterfactual, measured. Three fields, not one — the failure got wider too."""
    frozen, now = freeze_record(porting), window_hashes()
    naive = [field for field in now if frozen.get(field) != now[field]]
    assert set(NEW_FIELDS) <= set(naive)


def test_the_port_loop_freeze_record_is_byte_identical():
    """The bytes, not the fields. See `PORT_LOOP_FREEZE_SHA256`."""
    path = ROOT / "results" / "es-meddocan" / "R" / "sup-free" / "port-loop" / \
        "window_freeze.json"
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    assert got == PORT_LOOP_FREEZE_SHA256


def test_no_port_loop_call_line_carries_a_port_multi_hash():
    """The 8-round call log, every line, against the three new fields.

    The freeze record is one file and a widened writer touching it would be conspicuous; the
    call log is 6 MB and appended to by the same `call_line()` the new driver calls. A
    `port-multi` run that appended to the wrong arm's log, or a repair script that rewrote
    old lines through today's `window_hashes()`, would show up here and nowhere else.

    Checked as substring absence over raw lines rather than by parsing each one: the question
    is whether the field name appears at all, and 6 MB of `json.loads` to answer it is a cost
    with no matching gain in what is learned.
    """
    path = ROOT / "results" / "es-meddocan" / "R" / "sup-free" / "port-loop" / \
        "agent_calls.jsonl"
    lines = 0
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            lines += 1
            for field in NEW_FIELDS:
                assert f'"{field}"' not in line, f"line {lineno}: {field}"
    assert lines > 0, "an empty log would pass every assertion above"
