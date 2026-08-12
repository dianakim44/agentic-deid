"""The window became three files on 2026-08-12, and the arms already frozen did not move.

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
    AUDITOR_TEMPLATE, PROMPT_TEMPLATE, WINDOW_FILES, WINDOW_HASH_FIELDS,
    recorded_window_fields, window_hashes,
)

#: The window as it was before 2026-08-12, in order. Written out rather than sliced from
#: `WINDOW_FILES`, because a slice would follow the constant and this is the fact the
#: constant moved away from.
OLD_WINDOW = ["docs/prompts/rule_author.md", "config/sampling.yaml"]

#: The arms frozen under it. All three, by name: a test that discovered them by walking
#: `results/` would pass on an empty tree.
FROZEN_ARMS = ("port-oneshot", "port-oneshot-nofence", "port-human")

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

def test_todays_window_is_the_old_one_plus_the_auditor():
    """The widening is an addition, in order. Nothing was replaced or reordered.

    Order matters because `recorded_window_fields()` returns fields in window order and
    `human_arm.FIELDS` fixes the tail of every log line; a reordering would make two lines
    differ in a diff without differing in content.
    """
    assert list(WINDOW_FILES) == [PROMPT_TEMPLATE, AUDITOR_TEMPLATE,
                                  "config/sampling.yaml"]
    assert [f for f in WINDOW_FILES if f != AUDITOR_TEMPLATE] == OLD_WINDOW


def test_every_window_file_has_a_field_and_every_field_a_file():
    """A file in the window with no field is hashed into `files` and compared against
    nothing; a field with no file is a name a reader greps for and never finds."""
    assert set(WINDOW_HASH_FIELDS) == set(WINDOW_FILES)
    assert len(set(WINDOW_HASH_FIELDS.values())) == len(WINDOW_FILES)


def test_every_window_file_is_on_disk_where_the_hash_looks():
    for name in WINDOW_FILES:
        assert (ROOT / name).is_file(), name
