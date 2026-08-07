"""Tests for src/porting/human_arm.py — the `port-human` harness.

The harness writes two things: a log line that gets committed, and a rendered window
that must not be. Most of what is checked here is that boundary. `summarise()` is what
travels into a terminal or a commit message, so it is tested for what it does *not*
contain — no surface form and no document offset — and that is a test about a public
artefact rather than about a data structure.

The fixtures are constructed. `initial_error_pool()` is exercised against documents
built here rather than against MEDDOCAN on disk: the property under test is "dev fold
only, in-scope only", and a real corpus exercises the common case and neither boundary.
The one surface form in this file (`SURFACE`) exists so the summary can be checked for
its absence; it is invented, not corpus text.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora.base import Document, Span                     # noqa: E402
from src.porting import human_arm                              # noqa: E402
from src.corpora.base import axis                               # noqa: E402
from src.porting.human_arm import (                            # noqa: E402
    CONSULTED_AXIS, EVENTS, FIELDS, IN_HISTORY, IN_WORKTREE, SCOPES, VIOLATION,
    PortHumanError, append, arm_has_started, draw_iteration, freeze_path, freeze_window,
    initial_error_pool, log_line, log_path, render_for_author, started_where, summarise,
    window_drift,
)
from src.sample import (                                       # noqa: E402
    MISSED, WINDOW_FILES, ErrorSpan, window_hashes,
)

#: Invented, not from any corpus. Long and distinctive so a test asserting its absence
#: cannot pass by coincidence.
SURFACE = "Zzyzx Quinbolt"


def doc(doc_id: str, split: str, *, excluded: bool = False) -> Document:
    """One document with one span, at a distinctive offset."""
    text = "." * 1000 + SURFACE + "." * 1000
    span = (Span(start=1000, end=1000 + len(SURFACE), surface=SURFACE,
                 subtype="SEXO_SUJETO_ASISTENCIA", excluded=True)
            if excluded else
            Span(start=1000, end=1000 + len(SURFACE), surface=SURFACE,
                 subtype="NOMBRE_SUJETO_ASISTENCIA", phi_type="NAME"))
    return Document(doc_id=doc_id, corpus_id="es-meddocan", text=text,
                    spans=[span], split=split)


def err(doc_id: str, index: int, phi_type: str = "NAME",
        start: int = 1000) -> ErrorSpan:
    return ErrorSpan(doc_id=doc_id, span_index=index, phi_type=phi_type,
                     kind=MISSED, start=start, end=start + 6)


# ─── the initial pool is the dev fold, in scope, and nothing else ───────────

def test_the_initial_pool_is_every_in_scope_dev_gold_span(monkeypatch):
    docs = [doc("dev1", "dev"), doc("dev2", "dev"), doc("tr1", "train")]
    monkeypatch.setattr(human_arm, "load", lambda corpus: docs)
    pool = initial_error_pool("es-meddocan")
    assert sorted(e.doc_id for e in pool) == ["dev1", "dev2"]
    assert {e.kind for e in pool} == {MISSED}


def test_the_initial_pool_never_reaches_the_test_fold(monkeypatch):
    """Not a redundant check of the dev filter: this is the seal (CLAUDE.md), and the
    filter is one line away from `!= "train"` at any point."""
    docs = [doc("dev1", "dev"), doc("te1", "test")]
    monkeypatch.setattr(human_arm, "load", lambda corpus: docs)
    assert [e.doc_id for e in initial_error_pool("es-meddocan")] == ["dev1"]


def test_excluded_spans_are_not_in_the_pool(monkeypatch):
    """DESIGN §9.1: they carry no canonical type, so they cannot be stratified by one."""
    docs = [doc("dev1", "dev"), doc("dev2", "dev", excluded=True)]
    monkeypatch.setattr(human_arm, "load", lambda corpus: docs)
    assert [e.doc_id for e in initial_error_pool("es-meddocan")] == ["dev1"]


def test_span_index_indexes_the_documents_own_span_list(monkeypatch):
    """The referent DESIGN §11.2 fixes. An index into a filtered list would resolve to
    the wrong span for any document holding an excluded span before an in-scope one."""
    text = "." * 200
    spans = [
        Span(start=10, end=16, surface="aaaaaa", subtype="SEXO_SUJETO_ASISTENCIA",
             excluded=True),
        Span(start=30, end=36, surface="bbbbbb", subtype="NOMBRE_SUJETO_ASISTENCIA",
             phi_type="NAME"),
    ]
    d = Document(doc_id="dev1", corpus_id="es-meddocan", text=text, spans=spans,
                 split="dev")
    monkeypatch.setattr(human_arm, "load", lambda corpus: [d])
    (only,) = initial_error_pool("es-meddocan")
    assert only.span_index == 1
    assert d.spans[only.span_index].start == only.start


def test_an_empty_dev_fold_raises_rather_than_sampling_nothing(monkeypatch):
    monkeypatch.setattr(human_arm, "load", lambda corpus: [doc("tr1", "train")])
    with pytest.raises(PortHumanError) as e:
        initial_error_pool("es-meddocan")
    assert "split file" in str(e.value)


def test_the_empty_pool_message_quotes_no_surface(monkeypatch):
    monkeypatch.setattr(human_arm, "load", lambda corpus: [doc("tr1", "train")])
    with pytest.raises(PortHumanError) as e:
        initial_error_pool("es-meddocan")
    assert SURFACE not in str(e.value)


# ─── the freeze record ──────────────────────────────────────────────────────

def test_the_freeze_record_holds_both_hashes_and_the_arm(tmp_path, monkeypatch):
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    record = freeze_window("es-meddocan", "R", "sup-free")
    assert record["prompt_sha256"] == window_hashes()["prompt_sha256"]
    assert record["sampling_sha256"] == window_hashes()["sampling_sha256"]
    assert record["porting"] == "port-human"
    assert record["files"] == list(WINDOW_FILES)


def test_freezing_twice_returns_the_first_record_and_does_not_rewrite(tmp_path,
                                                                     monkeypatch):
    """The one question a rewritable freeze record cannot answer is the only one it is
    for: what the window was when the run *started*."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    first = freeze_window("es-meddocan", "R", "sup-free")
    path = human_arm.freeze_path("es-meddocan", "R", "sup-free")
    tampered = dict(first, prompt_sha256="sha256:" + "0" * 64)
    path.write_text(json.dumps(tampered), encoding="utf-8")
    again = freeze_window("es-meddocan", "R", "sup-free")
    assert again["prompt_sha256"] == "sha256:" + "0" * 64


# ─── the freeze is immutable once the arm has started ───────────────────────
#
# The `path.exists()` refusal above says only "I will not overwrite". It says nothing
# about a caller who deletes the file first, and in this repository that is exactly what
# happened: the window was re-frozen three times before iteration 1 by `rm` followed by a
# second call, each time outside the guard (docs/notes/window-freeze-history.md). A
# refusal conditioned on the presence of the thing being protected is not a refusal.
#
# These tests are about the second condition, which is not reachable by deleting a file:
# a non-null `human_minutes` on any line of the append-only log.

def a_minute_was_spent(corpus="es-meddocan", detector="R", supervision="sup-free"):
    """One log line recording human effort — the event that fixes the window."""
    return append(log_line(1, "rule_edit", "none", human_minutes=15),
                  corpus, detector, supervision)


def test_deleting_the_record_and_re_freezing_is_refused_after_minutes_are_logged(
        tmp_path, monkeypatch):
    """**The hole this closes.** Not a hypothetical: this is the sequence that ran three
    times in this repository's own history, and the guard as written had nothing to say
    about it."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    first = freeze_window("es-meddocan", "R", "sup-free")
    a_minute_was_spent()
    human_arm.freeze_path("es-meddocan", "R", "sup-free").unlink()
    with pytest.raises(PortHumanError) as e:
        freeze_window("es-meddocan", "R", "sup-free")
    assert "human_minutes" in str(e.value)
    assert first["prompt_sha256"]


def test_the_message_for_a_deleted_record_says_restore_rather_than_re_create(
        tmp_path, monkeypatch):
    """A re-created record hashes today's files and then claims to be the opening
    window, which is worse than a missing one: it is confidently wrong."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    freeze_window("es-meddocan", "R", "sup-free")
    a_minute_was_spent()
    human_arm.freeze_path("es-meddocan", "R", "sup-free").unlink()
    with pytest.raises(PortHumanError) as e:
        freeze_window("es-meddocan", "R", "sup-free")
    assert "MISSING" in str(e.value)
    assert "git" in str(e.value)


def test_re_freezing_over_an_existing_record_is_also_refused_after_minutes(
        tmp_path, monkeypatch):
    """Not just the delete path. Once minutes exist the function does not write at all,
    so the two refusals are not each other's special case."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    freeze_window("es-meddocan", "R", "sup-free")
    a_minute_was_spent()
    with pytest.raises(PortHumanError) as e:
        freeze_window("es-meddocan", "R", "sup-free")
    assert "unchanged" in str(e.value)


def test_re_freezing_is_permitted_before_any_minutes_are_recorded(tmp_path,
                                                                 monkeypatch):
    """The other half, and it has to be tested or the guard is untestably strict.
    §11.1 permits a revision before the arm starts, and a `read_sample` line with a null
    `human_minutes` is not a start — the window is still a proposal at that point."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    freeze_window("es-meddocan", "R", "sup-free")
    append(log_line(1, "read_sample", "none"), "es-meddocan", "R", "sup-free")
    human_arm.freeze_path("es-meddocan", "R", "sup-free").unlink()
    again = freeze_window("es-meddocan", "R", "sup-free")
    assert again["prompt_sha256"] == window_hashes()["prompt_sha256"]


def test_zero_minutes_counts_as_started(tmp_path, monkeypatch):
    """`0` is a recorded measurement and `null` is the absence of one. A truthiness test
    would read a logged zero as "nothing happened", and the field is validated to accept
    0 precisely because an event can take under a minute."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    freeze_window("es-meddocan", "R", "sup-free")
    append(log_line(1, "rule_edit", "none", human_minutes=0),
           "es-meddocan", "R", "sup-free")
    assert arm_has_started("es-meddocan", "R", "sup-free") is True
    with pytest.raises(PortHumanError):
        freeze_window("es-meddocan", "R", "sup-free")


def test_minutes_on_any_line_fix_the_window_not_only_the_last(tmp_path, monkeypatch):
    """Reading only the final line would let an appended null-minutes event re-open the
    freeze — and appending a line is the one operation this arm does constantly."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    freeze_window("es-meddocan", "R", "sup-free")
    append(log_line(1, "rule_edit", "none", human_minutes=20),
           "es-meddocan", "R", "sup-free")
    append(log_line(2, "read_sample", "none"), "es-meddocan", "R", "sup-free")
    assert arm_has_started("es-meddocan", "R", "sup-free") is True


def test_an_arm_with_no_log_at_all_has_not_started(tmp_path, monkeypatch):
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    assert arm_has_started("es-meddocan", "R", "sup-free") is False


def test_the_guard_reads_the_log_and_not_the_freeze_record(tmp_path, monkeypatch):
    """The property that makes it hold. A guard reading the freeze record is a guard
    arguing with whoever just deleted it; the log is append-only and carries the evidence
    in its own values, so `rm window_freeze.json` does not reach this."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    freeze_window("es-meddocan", "R", "sup-free")
    a_minute_was_spent()
    human_arm.freeze_path("es-meddocan", "R", "sup-free").unlink()
    assert arm_has_started("es-meddocan", "R", "sup-free") is True


def test_another_arms_minutes_do_not_fix_this_arms_window(tmp_path, monkeypatch):
    """The guard is per cell of the experiment. A shared reading would make the first
    corpus to record a minute freeze every other arm's window."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    freeze_window("es-meddocan", "R", "sup-free")
    a_minute_was_spent("es-meddocan", "R", "sup-free")
    assert arm_has_started("es-meddocan", "RT", "sup-free") is False
    assert freeze_window("es-meddocan", "RT", "sup-free")["detector"] == "RT"


def test_drift_on_a_deleted_record_after_minutes_says_restore(tmp_path, monkeypatch):
    """`window_drift()` is the function an author actually runs, so it is where the
    advice has to appear — and its old message told the reader to call freeze_window(),
    which is now the one thing that must not be done."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    freeze_window("es-meddocan", "R", "sup-free")
    a_minute_was_spent()
    human_arm.freeze_path("es-meddocan", "R", "sup-free").unlink()
    with pytest.raises(PortHumanError) as e:
        window_drift("es-meddocan", "R", "sup-free")
    assert "restore it from git" in str(e.value)
    assert "Do NOT call freeze_window()" in str(e.value)


def test_a_blank_line_in_the_log_does_not_crash_the_guard(tmp_path, monkeypatch):
    """The log is appended to by hand as well as by code, and a guard that raises on a
    stray newline is a guard someone will route around."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    freeze_window("es-meddocan", "R", "sup-free")
    path = a_minute_was_spent()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n")
    assert arm_has_started("es-meddocan", "R", "sup-free") is True


# ─── and the minutes survive the log's deletion, via git history ────────────
#
# The first version of `arm_has_started()` read the working tree only, so `rm
# human_log.jsonl` re-opened the freeze: one file, one command, and the guard's own input
# gone. Deleting the log is a much louder act than deleting the freeze record — it is the
# arm's only record of what a person did — but "louder" is not "prevented", and the guard
# was documented as having exactly that hole.
#
# So the second source is git history: any commit, any branch. These tests build a real
# repository in tmp_path rather than mocking `subprocess`, because what is under test is
# whether the `git` invocations are right, and a mock of `git show` would assert that the
# arguments match the arguments.

def a_repo(tmp_path):
    """A git repository at tmp_path, with identity set so commits succeed anywhere."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"],
                   cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    return tmp_path


def commit_all(tmp_path, message="wip"):
    """Commit the arm's results tree in the fixture repository.

    `results` explicitly rather than `.` or `-A`, even though this is a throwaway repo in
    tmp_path: CLAUDE.md's staging rule exists so that a habit cannot form, and a test file
    is where habits are copied from.
    """
    subprocess.run(["git", "add", "--", "results"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=tmp_path, check=True)


def test_minutes_in_a_commit_count_even_with_the_log_deleted(tmp_path, monkeypatch):
    """**The hole this closes.** The record and the log both removed, and the arm still
    reads as started, because the evidence is in a commit."""
    monkeypatch.setattr(human_arm, "ROOT", a_repo(tmp_path))
    freeze_window("es-meddocan", "R", "sup-free")
    path = a_minute_was_spent()
    commit_all(tmp_path)
    path.unlink()
    human_arm.freeze_path("es-meddocan", "R", "sup-free").unlink()
    assert started_where("es-meddocan", "R", "sup-free") == IN_HISTORY
    with pytest.raises(PortHumanError):
        freeze_window("es-meddocan", "R", "sup-free")


def test_the_message_says_the_log_is_in_history_but_not_in_the_worktree(tmp_path,
                                                                       monkeypatch):
    """A refusal that only said "this arm has started" would leave the reader with a
    missing log and no idea it was missing. The one action that helps is naming it."""
    monkeypatch.setattr(human_arm, "ROOT", a_repo(tmp_path))
    freeze_window("es-meddocan", "R", "sup-free")
    path = a_minute_was_spent()
    commit_all(tmp_path)
    path.unlink()
    with pytest.raises(PortHumanError) as e:
        freeze_window("es-meddocan", "R", "sup-free")
    message = str(e.value)
    assert "LOG is in git history but NOT in the working tree" in message
    assert "restore" in message


def test_minutes_deleted_from_a_committed_log_still_count(tmp_path, monkeypatch):
    """The subtler edit: the file stays, the lines that fix the window are removed from
    it. The working tree then says "not started" and the commit says otherwise."""
    monkeypatch.setattr(human_arm, "ROOT", a_repo(tmp_path))
    freeze_window("es-meddocan", "R", "sup-free")
    path = a_minute_was_spent()
    commit_all(tmp_path)
    path.write_text(json.dumps(log_line(1, "read_sample", "none")) + "\n",
                    encoding="utf-8")
    assert started_where("es-meddocan", "R", "sup-free") == IN_HISTORY


def test_the_worktree_answer_is_reported_as_the_worktree(tmp_path, monkeypatch):
    """Both sources can be true at once, and the ordinary case must not be reported as
    the deleted-log one — the message for `IN_HISTORY` tells the reader to restore a file
    that is sitting right there."""
    monkeypatch.setattr(human_arm, "ROOT", a_repo(tmp_path))
    freeze_window("es-meddocan", "R", "sup-free")
    a_minute_was_spent()
    commit_all(tmp_path)
    assert started_where("es-meddocan", "R", "sup-free") == IN_WORKTREE
    with pytest.raises(PortHumanError) as e:
        freeze_window("es-meddocan", "R", "sup-free")
    assert "LOG is in git history but NOT" not in str(e.value)


def test_minutes_on_another_branch_count(tmp_path, monkeypatch):
    """`git log --all`, not `git log`. A branch is not a different history of what a
    person did, and checking out an earlier state is not a way to un-spend a minute."""
    repo = a_repo(tmp_path)
    monkeypatch.setattr(human_arm, "ROOT", repo)
    freeze_window("es-meddocan", "R", "sup-free")
    commit_all(tmp_path, "freeze")
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=repo, check=True)
    path = a_minute_was_spent()
    commit_all(tmp_path, "minutes")
    subprocess.run(["git", "checkout", "-q", "-"], cwd=repo, check=True)
    assert not path.exists()
    assert started_where("es-meddocan", "R", "sup-free") == IN_HISTORY


def test_an_older_commit_counts_not_only_the_newest(tmp_path, monkeypatch):
    """Every commit touching the file is inspected. The newest is the one a rewrite would
    have edited, so reading only it would answer the question a rewriter prefers."""
    monkeypatch.setattr(human_arm, "ROOT", a_repo(tmp_path))
    freeze_window("es-meddocan", "R", "sup-free")
    path = a_minute_was_spent()
    commit_all(tmp_path, "with minutes")
    path.write_text(json.dumps(log_line(1, "read_sample", "none")) + "\n",
                    encoding="utf-8")
    commit_all(tmp_path, "minutes removed")
    assert started_where("es-meddocan", "R", "sup-free") == IN_HISTORY


def test_a_committed_log_with_no_minutes_does_not_count_as_started(tmp_path,
                                                                  monkeypatch):
    """The other half again, now for the history source: committing a `read_sample` line
    is not starting the arm, and re-freezing before any minutes stays permitted."""
    monkeypatch.setattr(human_arm, "ROOT", a_repo(tmp_path))
    freeze_window("es-meddocan", "R", "sup-free")
    append(log_line(1, "read_sample", "none"), "es-meddocan", "R", "sup-free")
    commit_all(tmp_path)
    assert started_where("es-meddocan", "R", "sup-free") is None
    human_arm.freeze_path("es-meddocan", "R", "sup-free").unlink()
    assert freeze_window("es-meddocan", "R", "sup-free")["corpus"] == "es-meddocan"


def test_another_arms_committed_minutes_do_not_fix_this_arms_window(tmp_path,
                                                                   monkeypatch):
    """The history lookup is per path, so it is per cell of the experiment — the same
    property the working-tree read has, and it is asserted separately because `git log`
    takes a pathspec and a pathspec is easy to widen."""
    monkeypatch.setattr(human_arm, "ROOT", a_repo(tmp_path))
    a_minute_was_spent("es-meddocan", "R", "sup-free")
    commit_all(tmp_path)
    log_path("es-meddocan", "R", "sup-free").unlink()
    assert started_where("es-meddocan", "RT", "sup-free") is None


def test_a_log_never_committed_and_then_deleted_reads_as_not_started(tmp_path,
                                                                    monkeypatch):
    """The remaining hole, asserted rather than left implied. Nothing can recover minutes
    that were never written anywhere durable, and a guard claiming otherwise would be
    claiming to read a deleted file. `docs/notes/window-freeze-history.md` records it."""
    monkeypatch.setattr(human_arm, "ROOT", a_repo(tmp_path))
    freeze_window("es-meddocan", "R", "sup-free")
    a_minute_was_spent().unlink()
    assert started_where("es-meddocan", "R", "sup-free") is None


def test_outside_a_repository_the_guard_answers_from_the_worktree_alone(tmp_path,
                                                                       monkeypatch):
    """No repository, no traceback. Every other test in this file runs in exactly this
    condition — a bare tmp_path — so a `git` failure that raised would take the whole
    file with it, and a guard that crashes where it cannot answer is a guard that gets
    removed rather than fixed."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    assert started_where("es-meddocan", "R", "sup-free") is None
    a_minute_was_spent()
    assert started_where("es-meddocan", "R", "sup-free") == IN_WORKTREE


def test_the_guard_does_not_hang_when_git_does(tmp_path, monkeypatch):
    """A guard with no timeout is a guard that can stop the arm indefinitely, and the
    fix someone reaches for then is deleting the guard."""
    monkeypatch.setattr(human_arm, "ROOT", a_repo(tmp_path))
    path = a_minute_was_spent()
    commit_all(tmp_path)
    path.unlink()
    calls = []

    def slow(args, **kwargs):
        calls.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))

    monkeypatch.setattr(human_arm.subprocess, "run", slow)
    assert started_where("es-meddocan", "R", "sup-free") is None
    assert calls and all(t == human_arm.GIT_TIMEOUT for t in calls)


def test_no_test_fold_path_is_ever_passed_to_git(tmp_path, monkeypatch):
    """The pathspec is built from `log_path()`, which is a results path. Asserted because
    a widened pathspec is how a `git show` starts reading things it should not, and
    `sealed/` is the thing this repository must not read (CLAUDE.md)."""
    monkeypatch.setattr(human_arm, "ROOT", a_repo(tmp_path))
    a_minute_was_spent()
    commit_all(tmp_path)
    seen = []
    real = human_arm.subprocess.run

    def record(args, **kwargs):
        seen.append(args)
        return real(args, **kwargs)

    monkeypatch.setattr(human_arm.subprocess, "run", record)
    log_path("es-meddocan", "R", "sup-free").unlink()
    assert started_where("es-meddocan", "R", "sup-free") == IN_HISTORY
    flat = " ".join(part for call in seen for part in call)
    assert "sealed" not in flat
    assert "human_log.jsonl" in flat


def test_a_git_error_message_never_reaches_the_exception(tmp_path, monkeypatch):
    """CLAUDE.md's rule about exception text does not branch on which file is safe. Log
    lines hold no surface forms today, and a guard that pastes `git` output into a
    message is one schema change away from that stopping being true."""
    monkeypatch.setattr(human_arm, "ROOT", a_repo(tmp_path))
    freeze_window("es-meddocan", "R", "sup-free")
    path = a_minute_was_spent()
    commit_all(tmp_path)
    path.unlink()
    with pytest.raises(PortHumanError) as e:
        freeze_window("es-meddocan", "R", "sup-free")
    assert SURFACE not in str(e.value)
    assert "human_minutes" in str(e.value)


def test_no_drift_on_a_freshly_frozen_window(tmp_path, monkeypatch):
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    freeze_window("es-meddocan", "R", "sup-free")
    assert window_drift("es-meddocan", "R", "sup-free") == []


def test_drift_names_the_field_that_moved(tmp_path, monkeypatch):
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    record = freeze_window("es-meddocan", "R", "sup-free")
    path = human_arm.freeze_path("es-meddocan", "R", "sup-free")
    path.write_text(json.dumps(dict(record, sampling_sha256="sha256:" + "0" * 64)),
                    encoding="utf-8")
    assert window_drift("es-meddocan", "R", "sup-free") == [
        "sampling_sha256"]


def test_drift_on_an_unfrozen_arm_raises_rather_than_reporting_none(tmp_path,
                                                                   monkeypatch):
    """Returning [] for a missing record would read as "the window is intact"."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    with pytest.raises(PortHumanError) as e:
        window_drift("es-meddocan", "R", "sup-free")
    assert "freeze_window()" in str(e.value)


def test_the_paths_follow_the_config_rather_than_a_copy_of_it(monkeypatch, tmp_path):
    """The check DESIGN §11.2's requirement actually needs. A literal in this module that
    happens to equal the config passes every path-shape assertion — the defect is not a
    wrong path but a second authority on where the arm writes, and it only becomes a
    wrong path on the day the config moves. Redirecting the config is what tells the two
    apart."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    monkeypatch.setattr(human_arm, "path_template",
                        lambda key: "elsewhere/{corpus}/{detector}/{supervision}/" + key)
    assert log_path("es-meddocan", "R", "sup-free") == (
        tmp_path / "elsewhere/es-meddocan/R/sup-free/humanlog")
    assert freeze_path("es-meddocan", "R", "sup-free") == (
        tmp_path / "elsewhere/es-meddocan/R/sup-free/humanfreeze")


def test_an_undeclared_path_key_is_refused():
    """A caller asking for a path the config does not declare has invented an artifact,
    and a default would put it somewhere plausible."""
    from src.corpora.base import CorpusError, path_template
    with pytest.raises(CorpusError) as e:
        path_template("humanlogs")
    assert "paths.humanlogs" in str(e.value)


def test_the_freeze_path_is_not_the_log_path(monkeypatch, tmp_path):
    """They share a directory, and a template collapse would make the freeze record
    append-target the log — silently, since both are written with the same encoding."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    assert (log_path("es-meddocan", "R", "sup-free")
            != freeze_path("es-meddocan", "R", "sup-free"))


def test_both_paths_come_from_naming_yaml_and_share_a_directory(monkeypatch, tmp_path):
    """DESIGN §11.2 requires paths.humanlog in the config rather than as a literal, for
    the reason axis() exists: two copies of a path are two places it can change."""
    from src.corpora.base import path_template
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    log = log_path("es-meddocan", "R", "sup-free")
    freeze = freeze_path("es-meddocan", "R", "sup-free")
    assert log.parent == freeze.parent
    assert log.name == "human_log.jsonl"
    assert freeze.name == "window_freeze.json"
    assert "{" not in path_template("humanlog").format(
        corpus="es-meddocan", detector="R", supervision="sup-free")


# ─── the draw and its provenance travel together ────────────────────────────

def test_draw_iteration_returns_the_sample_with_its_provenance():
    pool = [err("d1", i, start=100 + i * 10) for i in range(20)]
    sample, prov = draw_iteration(pool, "es-meddocan", 1, n=5)
    assert len(sample) == 5
    assert prov["iteration"] == 1
    assert prov["n_error_spans"] == 5
    assert prov["seed"] > 0


def test_draw_iteration_is_the_shared_draw_not_a_second_one():
    """The premise DESIGN §11.1 rests the arm on. If this harness sampled for itself,
    the two arms' windows would differ for a reason that is not the experiment."""
    from src.sample import draw
    pool = [err("d1", i, start=100 + i * 10) for i in range(20)]
    sample, _ = draw_iteration(pool, "es-meddocan", 3, n=7)
    assert sample == draw(pool, "es-meddocan", 3, n=7)


# ─── the summary is safe to say out loud ────────────────────────────────────

def test_the_summary_is_counts_only():
    pool = [err("d1", i, start=1000 + i * 10) for i in range(20)]
    pool += [err("d2", i, "DATE", start=2000 + i * 10) for i in range(5)]
    sample, _ = draw_iteration(pool, "es-meddocan", 1, n=6)
    s = summarise(sample, pool)
    assert s["pool_size"] == 25
    assert s["sample_size"] == 6
    assert set(s["by_type"]) == {"NAME", "DATE"}
    assert sum(v["drawn"] for v in s["by_type"].values()) == 6
    assert s["by_kind"] == {MISSED: 6}


def test_the_summary_carries_no_offsets():
    """An offset is not text, but a (doc_id, offset) pair beside a type is a pointer
    into the corpus. That is the right referent for the committed log and the wrong one
    for a summary read aloud (docstring on summarise)."""
    pool = [err("d1", 0, start=743197)]
    sample, _ = draw_iteration(pool, "es-meddocan", 1, n=1)
    blob = json.dumps(summarise(sample, pool))
    assert "743197" not in blob
    assert "743203" not in blob
    assert "d1" not in blob


def test_the_summary_names_types_that_are_in_the_pool_but_undrawn():
    """A type with errors and no slot is a fact about the window, so it appears with
    drawn 0 rather than vanishing — the alternative reads as "no such errors"."""
    pool = [err("d1", i, start=1000 + i * 10) for i in range(60)]
    pool += [err("d2", 0, "PROFESSION", start=2000)]
    sample, _ = draw_iteration(pool, "es-meddocan", 1, n=1)
    s = summarise(sample, pool)
    assert set(s["by_type"]) == {"NAME", "PROFESSION"}
    assert s["by_type"]["PROFESSION"]["in_pool"] == 1
    assert sum(v["drawn"] for v in s["by_type"].values()) == 1


# ─── the rendered window is what the author reads ───────────────────────────

def test_the_render_offsets_are_within_the_context_not_the_document():
    d = doc("dev1", "dev")
    block = render_for_author([err("dev1", 0, start=1000)], {"dev1": d}, 120)
    assert "(120, 126)" in block
    assert "(1000," not in block


def test_the_render_clips_the_window_to_the_document():
    text = "abcdefghij"
    d = Document(doc_id="dev1", corpus_id="es-meddocan", text=text, split="dev",
                 spans=[Span(start=2, end=5, surface="cde", subtype="X",
                             phi_type="NAME")])
    block = render_for_author([err("dev1", 0, start=2)], {"dev1": d}, 120)
    assert "(2, 8)" in block          # left clipped at 0, so window offset == document


def test_the_render_contains_the_context_and_the_summary_does_not():
    """The two views, side by side. The rendered block is the only place the corpus
    text appears, and it is never written to disk (rule_author.md §6)."""
    d = doc("dev1", "dev")
    sample = [err("dev1", 0, start=1000)]
    assert SURFACE in render_for_author(sample, {"dev1": d}, 120)
    assert SURFACE not in json.dumps(summarise(sample, sample))


def test_the_render_flattens_newlines():
    """One span per block, so a context holding a newline would otherwise be
    indistinguishable from the start of the next field."""
    text = "aaa\nbbb\nccc"
    d = Document(doc_id="dev1", corpus_id="es-meddocan", text=text, split="dev",
                 spans=[Span(start=4, end=7, surface="bbb", subtype="X",
                             phi_type="NAME")])
    block = render_for_author([err("dev1", 0, start=4)], {"dev1": d}, 120)
    assert "aaa bbb ccc" in block


# ─── the log line ───────────────────────────────────────────────────────────

def test_the_line_has_every_field_in_order():
    record = log_line(1, "read_sample", "none")
    assert tuple(record) == FIELDS


def test_absent_values_are_written_as_null_not_omitted():
    """An absent key and a key whose value is unknown are different facts, and only
    one of them survives into an aggregation."""
    record = log_line(1, "read_sample", "none")
    assert record["human_minutes"] is None
    assert record["actually_reused"] is None
    assert json.loads(json.dumps(record)).keys() == record.keys()
    assert '"human_minutes": null' in json.dumps(record)


def test_the_window_hashes_are_filled_by_the_line_not_the_caller():
    """A caller that has to remember them is a caller that forgets on the line that
    matters."""
    record = log_line(1, "read_sample", "none")
    assert record["prompt_sha256"] == window_hashes()["prompt_sha256"]
    assert record["sampling_sha256"] == window_hashes()["sampling_sha256"]
    assert record["prompt_sha256"].startswith("sha256:")


def test_the_two_hashes_are_of_different_files():
    record = log_line(1, "read_sample", "none")
    assert record["prompt_sha256"] != record["sampling_sha256"]


def test_an_unknown_event_is_refused():
    with pytest.raises(PortHumanError) as e:
        log_line(1, "read-sample", "none")
    assert "DESIGN" in str(e.value)


@pytest.mark.parametrize("event", EVENTS)
def test_every_declared_event_is_accepted(event):
    assert log_line(1, event, "none")["event"] == event


@pytest.mark.parametrize("scope", SCOPES)
def test_every_declared_scope_is_accepted(scope):
    assert log_line(1, "decision", "none", predicted_scope=scope)["predicted_scope"] == scope


def test_an_unknown_scope_is_refused():
    with pytest.raises(PortHumanError):
        log_line(1, "decision", "none", predicted_scope="general")


def test_actually_reused_is_three_valued():
    for value in (True, False, None):
        assert log_line(1, "decision", "none", actually_reused=value)[
            "actually_reused"] is value
    with pytest.raises(PortHumanError) as e:
        log_line(1, "decision", "none", actually_reused="yes")
    assert "second corpus" in str(e.value)


def test_negative_minutes_are_refused():
    with pytest.raises(PortHumanError):
        log_line(1, "rule_edit", "none", human_minutes=-5)
    assert log_line(1, "rule_edit", "none", human_minutes=0)["human_minutes"] == 0


# ─── the §8 self-report ─────────────────────────────────────────────────────
#
# `docs/prompts/rule_author.md` §8 forbids asking a language model what a rule should
# be during a port-human iteration, because an author who transcribes a model's answer
# has run port-oneshot with a slower interface and the control no longer holds. The
# clause has no enforcement beyond this field, which is what these tests are about:
# the field cannot be left unfilled, cannot hold an invented value, and — the one that
# matters most — is not allowed to refuse the violation it exists to record.

def test_the_self_report_is_required_and_has_no_default():
    """A default of "none" would record "no model was consulted" for every caller who
    did not think about the question, which is the answer the field exists to stop
    being free."""
    with pytest.raises(TypeError):
        log_line(1, "read_sample")


def test_null_is_not_an_accepted_self_report():
    """An unfilled field is indistinguishable from an unproblematic one."""
    with pytest.raises(PortHumanError) as e:
        log_line(1, "read_sample", None)
    assert "no default" in str(e.value)


@pytest.mark.parametrize("value", sorted(axis(CONSULTED_AXIS)))
def test_every_declared_self_report_value_is_accepted(value):
    assert log_line(1, "decision", value)["model_consulted"] == value


def test_the_violation_value_is_recorded_and_not_refused():
    """The test this section exists for. A self-report field that rejects the answer it
    exists to capture collects only the other answers, and the arm's integrity is then
    documented by a file that could not have recorded its absence (§8.2)."""
    record = log_line(4, "rule_edit", VIOLATION, human_minutes=12,
                      decision="asked a model which pattern fits")
    assert record["model_consulted"] == VIOLATION
    assert record["iteration"] == 4


def test_the_violation_survives_a_round_trip_to_the_log(tmp_path, monkeypatch):
    """Refusing to *write* it would be the same defect one layer down."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    path = append(log_line(4, "rule_edit", VIOLATION), "es-meddocan", "R", "sup-free")
    written = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert written["model_consulted"] == VIOLATION


def test_an_invented_self_report_value_is_refused():
    for bad in ("no", "none_at_all", "rule-content", "clean", True, 0):
        with pytest.raises(PortHumanError):
            log_line(1, "read_sample", bad)


def test_the_vocabulary_comes_from_naming_yaml_and_not_from_a_copy(monkeypatch):
    """A fifth value added to the axis has to reach this validation without an edit to
    the module — two copies of a vocabulary agree until the day they do not."""
    fake = dict(axis(CONSULTED_AXIS))
    fake["asked_a_colleague"] = "invented for this test"
    monkeypatch.setattr(human_arm, "axis",
                        lambda name: fake if name == CONSULTED_AXIS else axis(name))
    assert log_line(1, "decision", "asked_a_colleague")[
        "model_consulted"] == "asked_a_colleague"


def test_the_self_report_is_on_every_line_not_only_on_rule_edits():
    """The obligation is per event, so that it is in front of the author each time
    rather than once at the start of the run."""
    for event in EVENTS:
        assert log_line(1, event, "none")["model_consulted"] == "none"


def test_the_field_sits_before_the_window_hashes_in_the_record():
    """Judgement fields first, then the mechanically filled ones — the hashes are the
    two the caller never supplies, and keeping them last keeps that visible."""
    order = list(FIELDS)
    assert order.index("model_consulted") < order.index("prompt_sha256")
    assert order[-2:] == ["prompt_sha256", "sampling_sha256"]


# ─── appending ──────────────────────────────────────────────────────────────

def test_append_writes_one_json_line_and_keeps_the_previous_ones(tmp_path,
                                                                monkeypatch):
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    for i in (1, 2):
        path = append(log_line(i, "read_sample", "none"), "es-meddocan", "R",
                      "sup-free")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["iteration"] for x in lines] == [1, 2]


def test_an_unknown_path_component_is_refused(monkeypatch, tmp_path):
    """A typo mints a cell of the experiment instead of failing, and the aggregation
    that walks these directories would report it as a real one."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    for bad in (("es-meddocan", "rules-only", "sup-free"),
                ("es-meddocan", "R", "annotation-free"),
                ("es-carmen-typo", "R", "sup-free")):
        with pytest.raises(PortHumanError) as e:
            log_path(*bad)
        assert "naming.yaml" in str(e.value)


def test_the_log_path_carries_the_three_axes_and_the_arm(monkeypatch, tmp_path):
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    path = log_path("es-meddocan", "R", "sup-free")
    assert path.relative_to(tmp_path).as_posix() == (
        "results/es-meddocan/R/sup-free/port-human/human_log.jsonl")
