"""`call_line()` gained a `role` field on 2026-08-13, and the frozen arms' lines did not move.

`role` says which agent spent the call. Two agents share one `agent_calls.jsonl` from
`port-loop` round 2 onward and `llm_calls` sums their lines, so cost is right and attribution
is impossible unless each line says whose it is (DESIGN §5.5, `tests/test_agent_role.py`).

**Why this is the same question `WINDOW_FILES`' widening raised, and why it needs the same
shape of test.** Both are additions to a record that already exists on disk for calls that
have already been made. `tests/test_window_widening.py` states the reason a fixture cannot
answer it: *the records that must not move are the real ones, and a fixture cannot fail to be
retroactively rewritten.* So these tests read the two real `agent_calls.jsonl` files off the
working tree and assert on what is in them.

**One way this is stronger than the window case, and one way it is weaker.** Stronger: those
freeze records are committed, so a rewrite is recoverable from git. `agent_calls.jsonl` is
deny-listed by `tools/release_screen.py` and matched by `.gitignore` — an agent prompt carries
dev corpus text in §1.4 — so the file on disk is the **only** copy of what those two calls
were, and a rewrite of it is not recoverable at all. Weaker: for the same reason these tests
cannot demand the file. A fresh clone has no log, and there `skip` is the honest result —
there is no record present to fail to move. See `an_absent_log_skips` for why that skip is
narrow rather than a hole.

**The two ways the field could have reached backwards.**

  1. A migration. Anything that opens a frozen log for writing — even to add the field its
     own writer would now add — replaces a record of what happened with a reconstruction of
     what it would have said. `port-oneshot`'s line was written before this vocabulary
     existed; `rule_author` is the value it *would* carry, which is exactly what makes the
     backfill indistinguishable from the truth. Closed by `append_call()` opening `"a"`, and
     asserted here as the absence of the key on both frozen lines.
  2. A required parameter. `role` with no default makes every existing caller a TypeError,
     and the repair under time pressure is to thread a literal through each one — including
     `run_arm()`, whose window is frozen. The default is what keeps the baseline's driver
     unedited while `port-loop`'s is built.

Neither is a crash. A frozen line that acquired `role` would be well-formed JSONL carrying
the correct value, and the only thing wrong with it would be that nothing observed it.

    python3 -m pytest tests/test_call_role.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import sample                                                # noqa: E402
from src.corpora.base import CorpusError                              # noqa: E402
from src import orchestrate                                           # noqa: E402
from src.orchestrate import (                                         # noqa: E402
    RULE_AUTHOR, append_call, call_line, log_path,
)

#: The arms whose calls were made before `role` existed, by name. A test that discovered
#: them by walking `results/` would pass on an empty tree — `test_window_widening.py`'s
#: `FROZEN_ARMS` is named for the same reason. `port-human` is absent because it wrote
#: `human_log.jsonl` and never called a model (DESIGN §6.3); its lines are not this writer's.
FROZEN_ARMS = ("port-oneshot", "port-oneshot-nofence")

#: The keys those lines hold, in order, written out rather than read from either record.
#: Reading one and comparing it to the other would pass on two lines that were both
#: rewritten. This is the field list as of the calls that produced them.
FROZEN_KEYS = [
    "iteration", "outcome", "model_id", "model_id_reported", "model_id_resolution",
    "model_lifecycle", "prompt_reference", "sample_reference", "response_chars",
    "response_sha256", "cost", "generated", "prompt_sha256", "sampling_sha256",
]

ARM = ("es-meddocan", "R", "sup-free")


def frozen_lines(porting: str) -> list[dict]:
    """This arm's real log, or a skip — see `an_absent_log_skips` for the reasoning."""
    path = log_path(*ARM, porting)
    if not path.is_file():
        pytest.skip(f"{porting}: no call log on this working tree")
    lines = [json.loads(line) for line in
             path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines, (
        f"{porting}: the call log exists and is empty. Truncation is the failure this file "
        "is about, in its most complete form — `append_call()` never opens for writing, so "
        "an empty log is not something the writer can produce."
    )
    return lines


def test_an_absent_log_skips_because_the_log_cannot_be_committed():
    """**Why the skip above is narrow, asserted rather than promised.**

    A skip that can become permanent is a test that measures nothing. This one cannot be
    reached by anything a contributor does to the repository, because the condition is not
    "the log is missing from git" — the log is *never* in git, by a rule tested in
    `tests/test_release_screen.py` — it is "no run has happened in this tree". The two
    frozen arms' lines exist on the machine the calls were made on and nowhere else.

    Which is also why deleting the log is not caught here and does not need to be: DESIGN
    §6.3's window stays fixed through `called_where()`'s second route, the committed
    artefacts (`window_freeze.json`, `format_failure.json`, `spans.jsonl`), and
    `test_a_committed_artefact_counts_even_with_the_log_deleted` is where that is checked.
    What is unrecoverable is the *content* of these lines, and content is what this file
    reads.
    """
    for porting in FROZEN_ARMS:
        assert log_path(*ARM, porting).name == "agent_calls.jsonl"
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "**/agent_calls.jsonl" in ignored, (
        "the call log stopped being gitignored, which makes it committable — and a "
        "committed agent log carries dev corpus text through §1.4 (CLAUDE.md)"
    )


# ─── the real lines carry no role ───────────────────────────────────────────

@pytest.mark.parametrize("porting", FROZEN_ARMS)
def test_a_frozen_arms_lines_carry_no_role(porting):
    """**The failure this file is aimed at**, and it is a correct value rather than a wrong one.

    `rule_author` is what these calls were; a line backfilled with it would be true about the
    call and false about the record, and DESIGN §6.3's rule is about the record. The absence
    is the evidence that nothing reopened the file.
    """
    for index, line in enumerate(frozen_lines(porting)):
        assert "role" not in line, (
            f"{porting} line {index} acquired `role`. The value is right and the line is "
            "still wrong: a field added after the call is a claim about a call already made "
            "(DESIGN §6.3)."
        )


@pytest.mark.parametrize("porting", FROZEN_ARMS)
def test_a_frozen_arms_lines_hold_exactly_the_fields_they_were_written_with(porting):
    """Broader than the key above, and the reason is that `role` is not the last field to be
    proposed. The next addition to `call_line()` gets caught here without a second test."""
    for index, line in enumerate(frozen_lines(porting)):
        assert list(line) == FROZEN_KEYS, (
            f"{porting} line {index}: field list is not the one it was written with. "
            f"Added: {sorted(set(line) - set(FROZEN_KEYS))}; "
            f"missing: {sorted(set(FROZEN_KEYS) - set(line))}."
        )


def test_the_frozen_arms_agree_with_each_other_field_for_field():
    """Two arms, one writer, and the same field list — which is what makes a diff of the two
    logs readable. Asserted after the comparison against `FROZEN_KEYS` rather than instead of
    it: agreeing with each other is also what two rewritten logs would do.
    """
    shapes = {porting: [list(line) for line in frozen_lines(porting)]
              for porting in FROZEN_ARMS}
    first, second = (shapes[porting] for porting in FROZEN_ARMS)
    assert first == second


# ─── the writer, on a log it is allowed to touch ───────────────────────────

@pytest.fixture
def redirected(tmp_path, monkeypatch):
    """A throwaway root. **The one thing the real-file tests above cannot check.**

    Those tests read a log and assert nothing moved, which is the right question and answers
    it only for the writer that has already run. A writer that opens `"w"` would leave the
    two real logs exactly as they are until the next call is made — the defect is latent, and
    it lands on `port-loop`, whose log has more than one line in it. So the mode is checked
    here, on a log this suite is free to write to, and never on the real one: a test that
    appended to `results/es-meddocan/.../agent_calls.jsonl` to prove appending works would
    be modifying the record the tests above exist to protect.

    The window files are copied in rather than stubbed, for `tests/test_orchestrate.py`'s
    reason: `call_line()` hashes them onto every line, and a stub would make the hashes in
    this fixture's lines hashes of the stub.
    """
    import shutil

    for name in sample.WINDOW_FILES:
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, dest)
    monkeypatch.setattr(orchestrate, "ROOT", tmp_path)
    monkeypatch.setattr(sample, "ROOT", tmp_path)
    return tmp_path


def test_a_second_line_leaves_the_first_one_byte_identical(redirected):
    """`port-loop`'s shape: two roles, one file, and the RuleAuthor's line written first.

    Compared as raw bytes rather than as parsed dicts, because the failure includes
    re-serialising a line that was already written — same content, different key order or
    separators — and a dict comparison would call that unchanged.
    """
    first = call_line(2, prompt_reference={}, model={"model_id": "m"}, response_chars=1,
                      response_sha256="sha256:aa", outcome="called", cost={})
    path = append_call(first, *ARM, "port-loop")
    before = path.read_bytes()

    second = call_line(2, prompt_reference={}, model={"model_id": "m"}, response_chars=2,
                       response_sha256="sha256:bb", outcome="called", cost={},
                       role="auditor")
    append_call(second, *ARM, "port-loop")

    after = path.read_bytes()
    assert after.startswith(before), (
        "the first line changed when the second was written. `append_call()` opens \"a\" for "
        "this reason — a rewriting writer is invisible on a one-line log and lands on "
        "`port-loop` (DESIGN §6.3)."
    )
    lines = [json.loads(line) for line in
             after.decode("utf-8").splitlines() if line.strip()]
    assert [line["role"] for line in lines] == [RULE_AUTHOR, "auditor"]


def test_the_two_roles_lines_are_told_apart_only_by_the_field():
    """What `role` is for, stated as the property that makes it necessary.

    Both lines carry the same iteration, the same model and the same window hashes — they are
    one round's two calls — so nothing else in the record distinguishes them, and a per-role
    total computed without this field would have to guess. `sample_reference` is not the
    answer: it is null on both here, and will be null on the Auditor's line in every round.
    """
    kw = dict(prompt_reference={}, model={"model_id": "m"}, response_chars=1,
              response_sha256="sha256:aa", outcome="called", cost={})
    author = call_line(2, **kw)
    auditor = call_line(2, **kw, role="auditor")
    differing = [k for k in author if author[k] != auditor[k]]
    assert differing == ["role"] or differing == ["role", "generated"], (
        f"the two roles' lines differ in {differing}. If they differ in more than `role` "
        "(and the timestamp), this test is comparing two different calls rather than one "
        "round's two."
    )


# ─── the new writer fills it, and validates it ─────────────────────────────

def a_line(**kw) -> dict:
    base = dict(prompt_reference={"sha256": "sha256:aa"}, model={"model_id": "m"},
                response_chars=0, response_sha256="sha256:bb", outcome="called",
                cost={"input_tokens": 0})
    return call_line(1, **{**base, **kw})


def test_a_new_line_carries_the_role():
    """Every line, including this arm's — a field only the Auditor's lines carried would make
    `role`'s absence mean both "written before the field existed" and "written by the
    RuleAuthor", and no reader could tell which log they were holding."""
    assert a_line()["role"] == RULE_AUTHOR


def test_the_default_is_the_role_the_baseline_actually_has():
    """Not merely *a* default. `port-oneshot` drives the RuleAuthor and calls no Auditor
    (DESIGN §4), so the defaulted value is the true one for every existing caller — which is
    what makes defaulting it different from filling a field to satisfy a signature.
    """
    assert RULE_AUTHOR == "rule_author"
    assert a_line()["role"] == a_line(role="rule_author")["role"]


def test_the_auditors_role_is_writable_through_the_same_function():
    """One writer for both agents. A separate `audit_call_line()` would be a second place the
    field order is decided, and the two would drift in a way that only shows up when someone
    totals the file by role."""
    assert a_line(role="auditor")["role"] == "auditor"


def test_a_role_outside_the_vocabulary_is_refused_at_write_time():
    """Through `check_agent_role()`, so the vocabulary is naming.yaml's. The near-spellings
    are covered in `tests/test_agent_role.py`; what this asserts is that `call_line()` is on
    the validated path rather than passing its argument through.
    """
    with pytest.raises(CorpusError, match="not an agent role"):
        a_line(role="RuleAuthor")


def test_the_role_sits_beside_the_iteration():
    """Field order, and it is not cosmetic: `(iteration, role)` is what a per-round per-role
    cost total groups by, and a reader scanning the log reads the first fields. Pinned because
    an append at the end is the path of least resistance for the next field.
    """
    assert list(a_line())[:3] == ["iteration", "role", "outcome"]


# ─── the counterfactual ────────────────────────────────────────────────────

def test_a_backfill_would_have_changed_every_frozen_line():
    """The claim in this file's docstring, measured rather than described.

    Had `role` been added by rewriting the logs, this is what each line would have become —
    and the value written to it would have been the correct one. Computed here so that "the
    backfill is indistinguishable from the truth" is a test result and not prose.
    """
    for porting in FROZEN_ARMS:
        for line in frozen_lines(porting):
            backfilled = {"iteration": line["iteration"], "role": RULE_AUTHOR,
                          **{k: v for k, v in line.items() if k != "iteration"}}
            assert backfilled != line
            assert backfilled["role"] == a_line()["role"], (
                "the backfilled value equals what the writer would produce today, which is "
                "why no assertion on the value can catch this"
            )
