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
    RULE_AUTHOR, TEXT_KEYS, OrchestrateError, append_call, call_line, log_path,
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


# ─── the role and the iteration are checked against each other ─────────────
#
# 2026-09-01, and it is the third addition this file's reasoning covers. `port-multi` calls
# three agents once each before round 1 (DESIGN §6.7.1), and the sentence that has to survive
# into the paper is "they ran outside the loop". That sentence is about a driver, and a driver
# is not what a reader of `results/` has — so `call_line()` refuses the pairs that would make
# the log ambiguous, and the sentence becomes readable off the file.
#
# **The failure without the rule is not a wrong log, it is two indistinguishable logs.** A
# driver that re-profiled at every round would write `profiler` lines at 1..8 and a driver that
# profiled once would write one at 0; a driver that re-profiled and passed *the iteration it
# had* would write lines nobody could separate from the compliant case after the fact, since
# `profile.json` on disk is the last one written either way.

def test_an_out_of_loop_role_is_refused_at_a_round_number():
    """The half that catches re-profiling mid-run."""
    for role in sorted(orchestrate.OUT_OF_LOOP_ROLES):
        with pytest.raises(OrchestrateError, match="out-of-loop agents write iteration"):
            a_line(role=role)          # `a_line` writes iteration 1


@pytest.mark.parametrize("iteration", [1, 2, 8, -1])
def test_no_round_number_at_all_admits_an_out_of_loop_role(iteration):
    """Including a negative, which is the shape a sentinel-minded caller would try next."""
    for role in sorted(orchestrate.OUT_OF_LOOP_ROLES):
        with pytest.raises(OrchestrateError):
            call_line(iteration, role=role, prompt_reference={}, model={"model_id": "m"},
                      response_chars=0, response_sha256="sha256:aa", outcome="called",
                      cost={})


def test_an_out_of_loop_role_is_accepted_at_iteration_zero():
    """The three roles the rule exists for, at the one number it permits them."""
    for role in sorted(orchestrate.OUT_OF_LOOP_ROLES):
        line = call_line(orchestrate.AUTHORING_ITERATION, role=role, prompt_reference={},
                         model={"model_id": "m"}, response_chars=0,
                         response_sha256="sha256:aa", outcome="called", cost={})
        assert (line["iteration"], line["role"]) == (0, role)


@pytest.mark.parametrize("role", sorted(orchestrate.LOOP_ROLES))
def test_a_loop_role_is_refused_at_iteration_zero(role):
    """**The load-bearing half.**

    Without it, "three lines at iteration 0" stops being the same statement as "the three
    artefacts were authored before the loop" — a RuleAuthor line could sit there too, and the
    log would have to be read against a driver again, which is the situation the rule was
    added to end.
    """
    with pytest.raises(OrchestrateError, match="rounds are 1-based"):
        call_line(0, role=role, prompt_reference={}, model={"model_id": "m"},
                  response_chars=0, response_sha256="sha256:aa", outcome="called", cost={})


def test_the_two_halves_partition_the_vocabulary():
    """Every declared role is judged by the rule, and no role is judged by both halves.

    Read off `agent_roles()` rather than written out, because the failure this guards is a
    *sixth* role added to naming.yaml — and a test naming five could not see it. The written-out
    version of this assertion is in `tests/test_agent_role.py`; this is the half that says the
    enforcer's two sets and the vocabulary are the same five values.
    """
    from src.corpora.base import agent_roles
    assert orchestrate.LOOP_ROLES | orchestrate.OUT_OF_LOOP_ROLES == set(agent_roles())
    assert not (orchestrate.LOOP_ROLES & orchestrate.OUT_OF_LOOP_ROLES)


def test_the_iteration_refusal_fires_before_the_outcome_refusal():
    """Order, and it is a decision rather than an accident of where the code sits.

    A line whose role and iteration disagree is a line about the wrong *call*. Reporting its
    `outcome` spelling first would send a reader to fix the spelling of a call that should not
    have been made — so the pair is checked first, and this pins it against the next refactor
    that groups the validations by what they read.
    """
    with pytest.raises(OrchestrateError, match="out-of-loop agents write iteration"):
        call_line(3, role="profiler", prompt_reference={}, model={"model_id": "m"},
                  response_chars=0, response_sha256="sha256:aa",
                  outcome="not_an_outcome", cost={})


def test_the_refusals_name_no_corpus_text():
    """CLAUDE.md, at a message that names a role and a number and must name nothing else."""
    for kwargs in ({"iteration": 3, "role": "profiler"}, {"iteration": 0, "role": "auditor"}):
        with pytest.raises(OrchestrateError) as excinfo:
            call_line(kwargs["iteration"], role=kwargs["role"], prompt_reference={},
                      model={"model_id": "m"}, response_chars=0,
                      response_sha256="sha256:aa", outcome="called", cost={})
        assert "DESIGN §6.7.1" in str(excinfo.value)


def test_the_frozen_arms_lines_all_sit_at_round_numbers():
    """The rule is not retroactive, and it does not need to be — it is already true.

    Every committed line is a `rule_author` or `auditor` line at iteration ≥ 1, so the new
    refusal would have accepted all of them. Checked rather than assumed: a rule that would
    have rejected history is a rule whose history has to be explained, and the honest time to
    find that out is before it is enforced rather than the first time a log is replayed.
    """
    seen = 0
    for porting in FROZEN_ARMS + ("port-loop",):
        for line in frozen_lines(porting):
            seen += 1
            assert line["iteration"] >= 1, f"{porting}: iteration {line['iteration']}"
            assert line.get("role", RULE_AUTHOR) in orchestrate.LOOP_ROLES
    if not seen:
        pytest.skip("no committed call log on this tree — see `an_absent_log_skips`")


# ─── `sample_reference` became a parameter, and the frozen lines did not move ───
#
# 2026-08-13, the second addition this file's reasoning covers and the smaller one: the field
# already existed and already held null. What changed is that a caller can now fill it, and the
# default is the null `call_line()` used to hardcode.
#
# **Why that is still worth a test, when nothing about the written line changed.** Because the
# thing being asserted is that nothing about the written line changed, and the two ways it could
# have are the two from this file's docstring. A migration is the same failure as before. The
# other is subtler here than it was for `role`: a *required* `sample_reference` would make
# `run_arm()` a TypeError, and the repair is to pass something — and the only value at hand in
# an arm with an empty §1.4 block is `{}`, which is not the same record as `null`. An empty dict
# says a sample was drawn and had no spans in it. `port-oneshot` drew no sample.
#
# The reference is `port-oneshot-nofence`'s committed line, per the instruction that added the
# parameter. `port-oneshot`'s is checked alongside it wherever the assertion is per-arm, since a
# change that moved one arm's lines and not the other's is not a shape a writer can produce.

#: The fields today's writer produces that the frozen lines do not carry, each added after
#: those two calls were made and each with a file arguing why it did not reach backwards:
#: `role` here, the four window hashes in `tests/test_window_widening.py`. `generated` is the
#: instant of the call and differs from itself on every run — the one difference that is not an
#: addition.
#:
#: Written out because a replay test whose permitted differences were computed from the diff it
#: is checking would permit anything it found. Five since 2026-09-01: a *replay* of a frozen
#: line does pick up today's six window hashes, because `call_line()` writes the current window
#: and that is its job. What must not move is the line **on disk**, which is
#: `test_no_frozen_record_acquired_a_port_multi_hash` and the byte-identity assertions in
#: `tests/test_window_widening.py`. The distinction is the whole of why this list is permitted
#: to grow and those are not.
ADDED_SINCE = ("role", "auditor_sha256", "profiler_sha256", "mapper_sha256",
               "lexicon_builder_sha256")


def replay(line: dict, **kw) -> dict:
    """What today's writer produces from the frozen line's own inputs.

    The inputs are read back out of the record rather than restated here, so this is the call
    that was made and not a call resembling it — the window hashes in particular are the real
    files', because the arm's window is the one on disk (`test_the_replay_is_of_the_real_call`
    asserts that rather than assuming it).
    """
    base = dict(
        prompt_reference=line["prompt_reference"],
        model={k: line[k] for k in
               ("model_id", "model_id_reported", "model_id_resolution")},
        model_lifecycle=line["model_lifecycle"],
        response_chars=line["response_chars"],
        response_sha256=line["response_sha256"],
        outcome=line["outcome"],
        cost=line["cost"],
    )
    return call_line(line["iteration"], **{**base, **kw})


@pytest.mark.parametrize("porting", FROZEN_ARMS)
def test_a_frozen_arms_sample_reference_is_still_an_explicit_null(porting):
    """The value the parameter defaults to, read off the record it must not have changed.

    Both halves matter and they are different claims: the key is *present*, which is what makes
    the field comparable across arms, and it is *null*, which is what it means for an arm whose
    §1.4 block is empty. A line that lost the key or gained a `{}` would fail here.
    """
    for index, line in enumerate(frozen_lines(porting)):
        assert "sample_reference" in line, (
            f"{porting} line {index} lost `sample_reference`. Making the field passable is "
            "not license to omit it when nothing was passed — a key some arms omit cannot be "
            "compared across arms (DESIGN §4, `model_id_absent`)."
        )
        assert line["sample_reference"] is None, (
            f"{porting} line {index}: sample_reference is "
            f"{type(line['sample_reference']).__name__} and not null. An empty §1.4 block is "
            "an explicit absence; `{}` would say a sample was drawn and was empty."
        )


def test_the_reference_arms_line_is_what_the_default_still_writes():
    """`port-oneshot-nofence`'s committed line, replayed through the parameterised writer.

    The direct form of "identical before and after the change": the differences between the
    recorded line and today's output are exactly the fields added since the call, and
    `sample_reference` is not among them. Stronger than asserting the value is null, because it
    also catches the default moving the field, renaming it, or changing what else the line says.
    """
    line, = frozen_lines("port-oneshot-nofence")
    today = replay(line)
    differing = [k for k in today if k not in line or today[k] != line[k]]
    assert sorted(differing) == sorted([*ADDED_SINCE, "generated"]), (
        f"the replay differs in {sorted(differing)}. Expected only the fields added since "
        f"the call ({list(ADDED_SINCE)}) and the timestamp. `sample_reference` in that list "
        "means the default is not the null this line carries."
    )
    assert not set(line) - set(today), (
        f"the replay dropped {sorted(set(line) - set(today))} — a field the frozen line "
        "carries and today's writer does not is the log becoming unreadable as one file."
    )


def test_the_replay_is_of_the_real_call_and_not_a_resembling_one():
    """What makes the test above an assertion about this arm rather than about a fixture.

    The window hashes are not passed in — `call_line()` computes them from the files on disk —
    so their agreeing with the recorded ones is evidence the replay reproduces the call that
    was made. If §1.1–1.2's template or `config/sampling.yaml` had changed, this fails, and
    the failure is real: the frozen arm's window moved (DESIGN §11.2).
    """
    line, = frozen_lines("port-oneshot-nofence")
    today = replay(line)
    for field in ("prompt_sha256", "sampling_sha256"):
        assert today[field] == line[field], (
            f"{field} no longer matches the frozen call's. The replay is of a different "
            "window than the one that ran, which makes every other comparison here weaker "
            "than it reads."
        )


def test_the_field_order_the_frozen_line_has_survives_a_filled_reference():
    """One order for both values of the field, so the two arms' logs diff against each other.

    Checked with the reference *filled*, which is the case that did not exist before: a writer
    that appended a filled `sample_reference` at the end — the path of least resistance, since
    the value arrives last in the signature — would leave the null case in position 8 and put
    the filled one in position 14, and both logs would be well-formed.
    """
    line, = frozen_lines("port-oneshot-nofence")
    filled = replay(line, sample_reference={"block": "error_spans", "n_spans": 40})
    assert list(filled) == list(replay(line)), (
        "filling `sample_reference` changed the line's field order. The two arms' lines are "
        "supposed to differ in a value rather than in a shape."
    )
    # Only `role` was inserted *before* this field; the four window hashes go at the end. So
    # the shift is one, and it is written as one rather than as `len(ADDED_SINCE) - 1` — that
    # arithmetic gave the right answer only while `ADDED_SINCE` had two entries, which made it
    # a coincidence that read as a derivation. The 2026-09-01 widening added three more fields
    # after this one and the expected shift did not change.
    assert list(filled).index("sample_reference") == \
        list(line).index("sample_reference") + 1, (
        "the field moved relative to the frozen line by more than the one field inserted "
        "ahead of it (`role`). The window hashes are appended and must stay appended."
    )


def test_only_passing_a_reference_changes_the_value():
    """The parameter is the whole mechanism — nothing infers a sample from the other arguments.

    `prompt_reference` carries `sections_filled`, which on `port-loop`'s iteration 2 includes
    §1.4, so a writer that wanted to be helpful could read a sample's presence out of it. That
    is DESIGN §3's derive-nothing rule at this field: the driver that drew the sample is the
    one that says which spans it was, and a log that guessed would be right until the guess and
    the draw disagreed.
    """
    line, = frozen_lines("port-oneshot-nofence")
    with_section = dict(line["prompt_reference"], sections_filled=["1.1", "1.2", "1.4"])
    assert replay(line, prompt_reference=with_section)["sample_reference"] is None


def test_a_filled_reference_is_written_whole():
    """What `port-loop`'s iteration 2 passes: `render_window().reference()`, nested under the
    one key. Nested rather than merged, so a reader is never asked to tell the block's
    `text_sha256` from the prompt's.
    """
    line, = frozen_lines("port-oneshot-nofence")
    reference = {"block": "error_spans", "n_spans": 40, "context_chars": 120,
                 "text_sha256": "sha256:cc", "spans": [{"doc_id": "d", "span_index": 0}]}
    written = replay(line, sample_reference=reference)
    assert written["sample_reference"] == reference
    for key in reference:
        assert key not in written, (
            f"{key!r} sits at the top level of the line as well as inside the reference — the "
            "reference was merged rather than nested, and a `text_sha256` at the top level is "
            "the prompt's."
        )


def test_the_reference_is_copied_and_not_held():
    """`prompt_reference`'s treatment one field over. A caller that mutated its dict after the
    call — `port-loop` reuses one round's structures across two agents — would otherwise edit a
    line already handed to `append_call()`, and on the Auditor's turn that is a line already on
    disk agreeing with a mutation made after it was written.
    """
    line, = frozen_lines("port-oneshot-nofence")
    reference = {"n_spans": 40}
    written = replay(line, sample_reference=reference)
    reference["n_spans"] = 1
    assert written["sample_reference"] == {"n_spans": 40}


def test_an_empty_reference_writes_the_null_and_not_an_empty_object():
    """The value a *required* parameter would have been given, and why it is folded to null.

    `{}` and `null` are different records: the first says a sample was drawn and had nothing in
    it, the second that no sample was drawn. `port-oneshot` is the second. Folding them means
    the one line a hurried caller might write is the one the frozen arms already carry.
    """
    line, = frozen_lines("port-oneshot-nofence")
    assert replay(line, sample_reference={})["sample_reference"] is None
    assert replay(line, sample_reference=None)["sample_reference"] is None


# ─── the reference is a reference: no §1.4 text reaches this log ────────────

@pytest.mark.parametrize("key", sorted(TEXT_KEYS))
def test_a_reference_carrying_text_is_refused_at_write_time(key):
    """**The leak this field is one keystroke away from.** `render_window()` renders §1.4's
    context windows — real dev corpus text — and hands back a `FilledPrompt` whose only
    publishable exit is `reference()`. A driver that reached past it into the rendered block, or
    a reference that grew a `context` field for debugging, would put corpus text in the one file
    `tools/release_screen.py` is configured never to look at (this log is deny-listed, and
    `test_an_absent_log_skips_because_the_log_cannot_be_committed` is why).

    Refused rather than stripped: a writer that silently dropped the key would leave the caller
    believing it recorded something, and the next person adds it back under another name.
    DESIGN §5.5.1 states the rule — an added `text`/`surface`/`context`/`snippet` field is the
    signal to refuse the field.
    """
    line, = frozen_lines("port-oneshot-nofence")
    with pytest.raises(OrchestrateError, match="text and not a reference"):
        replay(line, sample_reference={"n_spans": 40, key: "Paciente Juan"})


def test_the_text_refusal_names_the_key_and_not_the_value():
    """CLAUDE.md's rule about exception messages, and this is the case it was written for: the
    value being refused is corpus text, so a message quoting it would leak through the very
    path — terminal, CI log, stack trace — that `release_screen.py` does not reach.
    """
    line, = frozen_lines("port-oneshot-nofence")
    with pytest.raises(OrchestrateError) as caught:
        replay(line, sample_reference={"context": "Paciente Juan Gómez, NHC 12345"})
    message = str(caught.value)
    assert "context" in message
    for fragment in ("Juan", "Gómez", "12345", "Paciente"):
        assert fragment not in message, (
            f"the refusal quotes {fragment!r} from the value it is refusing (CLAUDE.md)"
        )


def test_the_hashes_and_counts_a_reference_is_made_of_are_not_refused():
    """The guard is a list of key names and must stay one. `text_sha256` is what settles "was
    this the block that ran" without holding the block, and `text_chars` is the one property of
    the text a reader comparing two runs can act on — both are `render_window().reference()`'s
    own fields. A guard matching the `text` prefix would refuse the record it exists to protect.
    """
    line, = frozen_lines("port-oneshot-nofence")
    reference = {"block": "error_spans", "n_spans": 40, "context_chars": 120,
                 "text_chars": 4096, "text_sha256": "sha256:dd", "window_files": {}}
    assert replay(line, sample_reference=reference)["sample_reference"] == reference


# ─── the counterfactual ────────────────────────────────────────────────────

def test_a_required_sample_reference_would_have_broken_the_baselines_driver():
    """The second of this file's two backwards routes, measured for the new parameter.

    `run_arm()` calls `call_line()` and passes no sample, because `port-oneshot` draws none.
    Had the parameter been required, that call would raise — and the repair under time pressure
    is to edit the frozen arm's driver, which is the thing the default exists to avoid. Shown
    by removing the argument the same way a required parameter would demand it be supplied.
    """
    line, = frozen_lines("port-oneshot-nofence")
    assert replay(line)["sample_reference"] is None
    with pytest.raises(TypeError):
        call_line(1, prompt_reference={}, model={"model_id": "m"}, response_chars=0,
                  response_sha256="sha256:aa", outcome="called")  # `cost`, which *is* required


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
