"""Tests for src/orchestrate.py — the `port-oneshot` window freeze.

Two things are under test and they are different in kind.

**The refusal.** `docs/notes/window-freeze-history.md` is a record of a guard that was
stepped around three times, and the step was always the same: delete the file the guard
looks at. So the tests here do that deliberately — delete the freeze record, delete the
call log — and assert what the guard says afterwards. A test that only froze once and
checked the fields would have passed against the defective version.

**What the record attests to.** This arm hashes `config/sampling.yaml` and uses none of
its parameters, because DESIGN §4 makes `port-oneshot` a truncation of `port-loop` and
call 1 carries §§1.1–1.2 only. A record stating the hash alone is indistinguishable from
one written by an arm that drew 40 spans under it, so `sections_shown` /
`sections_empty` / `sampling_applied` are asserted as *distinguishing* fields rather than
as present ones — the assertions compare a `port-oneshot` record against a record with
§1.4 shown, because "these values did not apply" and "these values applied" must not read
the same.

The fixtures redirect `ROOT` on this module and on `src.sample`, since the window hashes
come from there. `a_repo()` and `commit_all()` mirror `tests/test_human_arm.py`'s, and the
staging in `commit_all()` names `results` explicitly rather than using `-A`, for the
reason that file gives: CLAUDE.md's rule exists so the habit cannot form, and a test file
is where habits are copied from.
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import orchestrate, sample                             # noqa: E402
from src.corpora.base import path_template                      # noqa: E402
from src.orchestrate import (                                   # noqa: E402
    INPUT_BLOCKS, IN_LOG, IN_RESULTS, ONESHOT_SECTIONS, SAMPLING_SECTION,
    OrchestrateError, arm_has_called, called_where, freeze_path, freeze_window,
    log_path, prompt_blocks, window_drift,
)
from src.sample import WINDOW_FILES, window_hashes              # noqa: E402

MODULE = ROOT / "src" / "orchestrate.py"

ARM = ("es-meddocan", "R", "sup-free")


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A redirected root with the two window files in place.

    The real files are copied rather than invented, so `prompt_blocks()` finds the real
    §1.x headings and the hashes are the hashes of the committed window. A stub prompt
    would make every heading assertion a test of the stub.
    """
    for name in WINDOW_FILES:
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, dest)
    monkeypatch.setattr(orchestrate, "ROOT", tmp_path)
    monkeypatch.setattr(sample, "ROOT", tmp_path)
    return tmp_path


def a_repo(tmp_path):
    """A git repository at tmp_path, with identity set so commits succeed anywhere."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"],
                   cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    return tmp_path


def commit_all(tmp_path, message="wip"):
    """Commit the arm's results tree. `results` explicitly, never `-A` (CLAUDE.md)."""
    subprocess.run(["git", "add", "--", "results"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=tmp_path, check=True)


def a_call_was_made(*arm):
    """One line in `agent_calls.jsonl` — the event that fixes this arm's window."""
    path = log_path(*(arm or ARM))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"call_id": "c1"}) + "\n")
    return path


def an_artefact_was_written(*arm, name="metrics.json"):
    """Something only a call can produce, beside the freeze record."""
    path = freeze_path(*(arm or ARM)).parent / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    return path


# ─── the record: this arm's own path, not the retired arm's ─────────────────

def test_the_record_goes_to_armfreeze_and_not_to_humanfreeze(tree):
    """DESIGN §6.3's two-key split, as a property of where this module writes.

    Widening `paths.humanfreeze` would have let an agent arm write to `port-human`'s path,
    where a retired arm's record is silently overwritten by a later-starting arm —
    `arm_has_started()`'s defect returning one level below where that guard can see it.
    """
    freeze_window(*ARM)
    written = freeze_path(*ARM)
    assert written.relative_to(tree).as_posix() == (
        "results/es-meddocan/R/sup-free/port-oneshot/window_freeze.json")
    human = tree / path_template("humanfreeze").format(
        corpus="es-meddocan", detector="R", supervision="sup-free")
    assert not human.exists(), (
        "the agent arm wrote to port-human's freeze path, which is the collision the "
        "second key exists to prevent")


def test_the_writer_reads_the_path_from_the_config_rather_than_a_copy(tree, monkeypatch):
    """The check DESIGN §11.2's requirement actually needs.

    A literal in this module that happens to equal the config passes every path-shape
    assertion; the defect is not a wrong path but a second authority on where the arm
    writes, and it only becomes a wrong path on the day the config moves. Redirecting the
    config is what tells the two apart.
    """
    monkeypatch.setattr(
        orchestrate, "path_template",
        lambda key: "elsewhere/{corpus}/{detector}/{supervision}/{porting}/" + key)
    assert freeze_path(*ARM) == (
        tree / "elsewhere/es-meddocan/R/sup-free/port-oneshot/armfreeze")


def test_the_freeze_key_is_the_arm_key(tree):
    """Named because the whole point of the second key is that this one is not the other.
    `human_arm.FREEZE_KEY` is `humanfreeze` and stays that way."""
    from src.porting.human_arm import FREEZE_KEY as HUMAN_KEY
    assert orchestrate.FREEZE_KEY == "armfreeze"
    assert HUMAN_KEY == "humanfreeze"
    assert "{porting}" in path_template(orchestrate.FREEZE_KEY)
    assert "port-human" in path_template(HUMAN_KEY), (
        "humanfreeze must stay pinned to the retired arm (DESIGN §6.3)")


def test_an_unknown_axis_component_is_refused(tree):
    """A typo mints a cell of the experiment instead of failing, and the aggregation that
    walks these directories would report it as a real one."""
    for bad in (("es-meddocan", "rules-only", "sup-free"),
                ("es-meddocan", "R", "annotation-free"),
                ("es-carmen-typo", "R", "sup-free")):
        with pytest.raises(OrchestrateError) as e:
            freeze_path(*bad)
        assert "naming.yaml" in str(e.value)


def test_an_unknown_porting_value_is_refused(tree):
    with pytest.raises(OrchestrateError) as e:
        freeze_path(*ARM, "port-oneshot-v2")
    assert "naming.yaml" in str(e.value)


def test_the_components_to_check_come_from_the_template(tree, monkeypatch):
    """`_arm_path` reads the field names off the template instead of holding a list.

    The third site in this repository doing this validation. A hand-written list is a
    fourth thing to keep in sync, and the failure it produces is a component nobody
    checks — so a template component with no axis behind it raises here rather than
    passing through.
    """
    monkeypatch.setattr(orchestrate, "path_template",
                        lambda key: "x/{corpus}/{iteration}/" + key)
    with pytest.raises(OrchestrateError) as e:
        freeze_path(*ARM)
    assert "iteration" in str(e.value)


# ─── the record says which blocks the call carried ──────────────────────────

def test_the_record_holds_both_hashes_and_the_arm(tree):
    record = freeze_window(*ARM)
    assert record["prompt_sha256"] == window_hashes()["prompt_sha256"]
    assert record["sampling_sha256"] == window_hashes()["sampling_sha256"]
    assert record["porting"] == "port-oneshot"
    assert record["files"] == list(WINDOW_FILES)


def test_the_record_says_which_sections_were_shown_and_which_were_empty(tree):
    """DESIGN §4: §§1.1–1.2 and nothing else. Both lists, because a reader who has to
    derive one from the other needs to know INPUT_BLOCKS, and a reader with that knowledge
    is not the reader this field is for."""
    record = freeze_window(*ARM)
    assert record["sections_shown"] == ["1.1", "1.2"]
    assert record["sections_empty"] == ["1.3", "1.4"]
    assert sorted(record["sections_shown"] + record["sections_empty"]) == \
        sorted(INPUT_BLOCKS)


def test_the_sampling_hash_is_written_even_though_nothing_used_it(tree):
    """§6.3: the hash stays for comparability with the arms that do use the parameters.
    Dropping it would make this record incomparable to `port-loop`'s."""
    record = freeze_window(*ARM)
    assert record["sampling_sha256"] == window_hashes()["sampling_sha256"]
    assert "config/sampling.yaml" in record["files"]


def test_the_record_says_the_sampling_parameters_did_not_apply(tree):
    record = freeze_window(*ARM)
    assert record["sampling_applied"] is False


def test_an_applied_and_an_unapplied_window_do_not_read_alike(tree):
    """**The property the field exists for**, and the one a presence check cannot see.

    Both records hash the same `config/sampling.yaml`. If the record said only that, a
    reader would find `sampling_sha256` and reasonably conclude 40 spans at ±120
    characters were shown — the same conclusion in both cases, one of them wrong. So the
    two records are compared against each other rather than each being checked for a
    field.
    """
    oneshot = freeze_window(*ARM)
    iterating = freeze_window(*ARM, "port-loop", sections=INPUT_BLOCKS)
    assert oneshot["sampling_sha256"] == iterating["sampling_sha256"]
    assert oneshot["sampling_applied"] is False
    assert iterating["sampling_applied"] is True
    assert oneshot["sections_empty"] and not iterating["sections_empty"]


def test_sampling_applied_is_derived_from_the_sections(tree):
    """Not a second parameter. Two fields a caller sets independently are two fields that
    can disagree, and the disagreement would be a record claiming §1.4 was empty while
    also claiming the parameters governing §1.4 applied."""
    with_block = freeze_window(*ARM, "port-loop", sections=("1.1", SAMPLING_SECTION))
    assert with_block["sampling_applied"] is True
    without = freeze_window(*ARM, "port-multi", sections=("1.1", "1.2", "1.3"))
    assert without["sampling_applied"] is False


def test_the_sampling_section_is_the_one_the_prompt_documents():
    """`SAMPLING_SECTION` must be the block `config/sampling.yaml`'s keys describe. If it
    drifted to another number, `sampling_applied` would answer a question about the wrong
    section and every record would still validate."""
    text = (ROOT / "docs" / "prompts" / "rule_author.md").read_text(encoding="utf-8")
    heading = f"### {SAMPLING_SECTION} Error spans"
    assert heading in text, f"expected a heading {heading!r} in the prompt"
    for key in ("n_error_spans", "context_chars", "min_per_type"):
        assert key in text


def test_an_unknown_section_is_refused(tree):
    with pytest.raises(OrchestrateError) as e:
        freeze_window(*ARM, sections=("1.1", "2.3"))
    assert "2.3" in str(e.value)


def test_an_empty_section_list_is_refused(tree):
    """A record claiming the call was shown nothing is a caller that has not decided."""
    with pytest.raises(OrchestrateError) as e:
        freeze_window(*ARM, sections=())
    assert "nothing" in str(e.value)


def test_a_renumbered_prompt_is_refused_rather_than_recorded(tree):
    """The record's window claim names §1's blocks, and it is checked against the file
    being hashed. A prompt whose sections were renumbered would otherwise produce a record
    attesting to sections that do not exist — and since the hash moves in the same commit,
    the two facts belong to one event."""
    prompt = tree / "docs" / "prompts" / "rule_author.md"
    prompt.write_text(
        prompt.read_text(encoding="utf-8").replace("### 1.4 Error spans",
                                                   "### 2.4 Error spans"),
        encoding="utf-8")
    with pytest.raises(OrchestrateError) as e:
        freeze_window(*ARM)
    assert "1.4" in str(e.value)


def test_the_blocks_are_read_from_the_prompt_that_is_hashed(tree):
    """`prompt_blocks()` must resolve the same file `window_hashes()` does. Two
    resolutions of one path is how a record comes to hash one file and describe another,
    and it is visible only under a redirected root."""
    assert set(INPUT_BLOCKS) <= prompt_blocks()
    (tree / "docs" / "prompts" / "rule_author.md").write_text("# nothing\n",
                                                              encoding="utf-8")
    assert prompt_blocks() == frozenset()


def test_the_shown_sections_are_recorded_in_a_fixed_order(tree):
    """A reader diffing two records sees a change in what was shown rather than a
    reordering."""
    record = freeze_window(*ARM, "port-loop", sections=("1.4", "1.1", "1.3"))
    assert record["sections_shown"] == ["1.1", "1.3", "1.4"]


def test_the_record_carries_an_instant_and_a_revision(tree):
    first = freeze_window(*ARM)
    assert first["revision"] == 1
    assert first["generated"].endswith("Z") and "T" in first["generated"]
    assert freeze_window(*ARM)["revision"] == 2


def test_the_default_sections_are_the_baselines(tree):
    """`ONESHOT_SECTIONS` is the default because this file drives that arm; `port-loop`
    passing its own is how one writer serves both."""
    assert ONESHOT_SECTIONS == ("1.1", "1.2")
    assert freeze_window(*ARM)["sections_shown"] == list(ONESHOT_SECTIONS)


# ─── the refusal: conditioned on the call, not on the record ────────────────

def test_re_freezing_before_the_call_is_permitted(tree):
    """§6.3 puts the revision window here. A guard that refused would be enforcing a rule
    the design does not have."""
    first = freeze_window(*ARM)
    freeze_path(*ARM).unlink()
    again = freeze_window(*ARM)
    assert again["prompt_sha256"] == first["prompt_sha256"]


def test_a_re_freeze_before_the_call_describes_the_files_as_they_are_now(tree):
    """The opposite of `human_arm.freeze_window()`, deliberately.

    That function returns the existing record so a re-run of the setup step gets the
    opening window. Here the freeze is taken immediately before the call and is the only
    thing attesting to the window that call ran under — there are no per-line hashes to
    disagree with it — so a stale proposal handed back would let the prompt move between
    the freeze and the call with nothing on disk showing it.
    """
    before = freeze_window(*ARM)["prompt_sha256"]
    prompt = tree / "docs" / "prompts" / "rule_author.md"
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\n<!-- moved -->\n",
                      encoding="utf-8")
    after = freeze_window(*ARM)
    assert after["prompt_sha256"] != before
    assert after["prompt_sha256"] == window_hashes()["prompt_sha256"]
    assert after["revision"] == 2


def test_a_call_fixes_the_window(tree):
    freeze_window(*ARM)
    a_call_was_made()
    with pytest.raises(OrchestrateError) as e:
        freeze_window(*ARM)
    assert "already made its call" in str(e.value)


def test_deleting_the_record_and_re_freezing_is_refused_after_a_call(tree):
    """**The hole this closes**, and it is not hypothetical: the same sequence — `rm` the
    record, call the writer again — ran three times in this repository before iteration 1,
    entirely outside a guard conditioned on `path.exists()`."""
    first = freeze_window(*ARM)
    a_call_was_made()
    freeze_path(*ARM).unlink()
    with pytest.raises(OrchestrateError) as e:
        freeze_window(*ARM)
    assert "already made its call" in str(e.value)
    assert first["prompt_sha256"]


def test_the_refusal_is_not_conditioned_on_the_records_presence(tree):
    """Stated as its own test because it is the whole design.

    The record present and the record absent must both refuse once the call is made, and a
    version that only checked `path.exists()` would pass the first half and fail the
    second. Both halves are asserted here so a future edit cannot satisfy one and lose the
    other.
    """
    a_call_was_made()
    with pytest.raises(OrchestrateError):
        freeze_window(*ARM)
    assert not freeze_path(*ARM).exists()
    freeze_path(*ARM).parent.mkdir(parents=True, exist_ok=True)
    freeze_path(*ARM).write_text('{"revision": 1}\n', encoding="utf-8")
    with pytest.raises(OrchestrateError):
        freeze_window(*ARM)


def test_a_blank_line_in_the_log_is_not_a_call(tree):
    """The criterion is a line, and an empty file is not one. A trailing newline written
    by something that opened the log and wrote nothing would otherwise fix the window."""
    log = log_path(*ARM)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n\n", encoding="utf-8")
    assert called_where(*ARM) is None
    assert freeze_window(*ARM)["revision"] == 1


def test_a_malformed_log_line_still_counts_as_a_call(tree):
    """The line is not parsed. A guard that refused to see a line because `json.loads`
    raised would fail in the unsafe direction — something wrote to this file during a
    call, whatever shape it left behind."""
    log = log_path(*ARM)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("{not json\n", encoding="utf-8")
    assert called_where(*ARM) == IN_LOG
    with pytest.raises(OrchestrateError):
        freeze_window(*ARM)


def test_the_criterion_is_not_a_field_the_way_port_humans_is(tree):
    """`port-human` reads a non-null `human_minutes` because a line there can precede any
    spent attention (`event: read_sample`). This log has no such line: it is appended to
    when a call is made, so a line's existence is the event. A field-based criterion here
    would be a field this arm never writes."""
    log = log_path(*ARM)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"call_id": "c1", "human_minutes": None}) + "\n",
                   encoding="utf-8")
    assert arm_has_called(*ARM)


def test_another_arms_call_does_not_fix_this_arms_window(tree):
    """Per-arm records, per-arm guards. `port-loop` calling is not a reason
    `port-oneshot`'s window is fixed, and a guard reading a shared path would say it was."""
    a_call_was_made("es-meddocan", "R", "sup-free", "port-loop")
    assert called_where(*ARM) is None
    assert freeze_window(*ARM)["revision"] == 1


def test_another_corpus_call_does_not_fix_this_ones(tree):
    a_call_was_made("de-grascco", "R", "sup-free")
    assert called_where(*ARM) is None


# ─── the second source: a committed artefact, since the log is never committed ──

def test_a_committed_artefact_counts_even_with_the_log_deleted(tree):
    """**The second hole**, and it cannot be closed the way `port-human` closed it.

    That guard reads its log from git history when the working-tree copy is gone.
    `agent_calls.jsonl` is deny-listed by `tools/release_screen.py` — an agent prompt
    quotes dev text — so history never holds a copy of it, ever. What history does hold is
    everything else the arm writes, so that is what is consulted.
    """
    repo = a_repo(tree)
    freeze_window(*ARM)
    log = a_call_was_made()
    an_artefact_was_written()
    commit_all(repo)
    log.unlink()
    assert called_where(*ARM) == IN_RESULTS
    with pytest.raises(OrchestrateError):
        freeze_window(*ARM)


def test_the_committed_freeze_record_alone_is_not_evidence_of_a_call(tree):
    """**The exclusion that keeps this from being `path.exists()` in disguise.**

    The record is written *before* the call, so counting it would make the guard true as
    soon as a window was frozen — with a `git` walk in front to look thorough. A committed
    freeze record and nothing else means a frozen window and no call.
    """
    repo = a_repo(tree)
    freeze_window(*ARM)
    commit_all(repo)
    assert called_where(*ARM) is None
    assert freeze_window(*ARM)["revision"] == 2


def test_the_artefact_check_is_not_a_list_of_filenames(tree):
    """Any artefact of the arm counts, including one this module does not know about.

    Enumerating filenames would silently stop protecting on the day the orchestrator
    gains an output — the shape of failure this repository keeps finding, where a check
    that matches nothing reads as a check that passed.
    """
    repo = a_repo(tree)
    freeze_window(*ARM)
    an_artefact_was_written(name="something_added_later.json")
    commit_all(repo)
    assert called_where(*ARM) == IN_RESULTS


def test_an_artefact_deleted_from_a_commit_still_counts(tree):
    """Every commit that touched the directory is inspected, not the newest. The newest is
    the one a rewrite would have edited, so reading only it answers the question a
    rewriter prefers."""
    repo = a_repo(tree)
    freeze_window(*ARM)
    path = an_artefact_was_written()
    commit_all(repo)
    path.unlink()
    commit_all(repo, "remove it")
    assert called_where(*ARM) == IN_RESULTS


def test_another_arms_committed_artefact_does_not_count(tree):
    repo = a_repo(tree)
    an_artefact_was_written("es-meddocan", "R", "sup-free", "port-loop")
    commit_all(repo)
    assert called_where(*ARM) is None


def test_the_log_is_consulted_before_history(tree):
    """Both can be true at once, and the ordinary case must not be reported as the
    deleted-log one: the `IN_RESULTS` message says the log is unrecoverable, which is
    wrong advice when the file is sitting there."""
    repo = a_repo(tree)
    freeze_window(*ARM)
    a_call_was_made()
    an_artefact_was_written()
    commit_all(repo)
    assert called_where(*ARM) == IN_LOG
    with pytest.raises(OrchestrateError) as e:
        freeze_window(*ARM)
    assert "call log is present" in str(e.value)


def test_the_unrecoverable_case_says_so_rather_than_saying_restore_it(tree):
    """`port-human`'s message tells the reader to restore the log from git. That advice is
    false here and the message must not carry it: the log is deny-listed, so there is no
    commit to restore it from."""
    repo = a_repo(tree)
    freeze_window(*ARM)
    an_artefact_was_written()
    commit_all(repo)
    with pytest.raises(OrchestrateError) as e:
        freeze_window(*ARM)
    message = str(e.value)
    assert "cannot be restored from git" in message
    assert "restore it from git" not in message


def test_a_git_failure_is_not_a_traceback_out_of_the_guard(tree):
    """No repository at all: the guard answers rather than raising. That is the unsafe
    direction, which is why the working tree is consulted first and why the limit is
    asserted below rather than argued away."""
    assert called_where(*ARM) is None
    assert freeze_window(*ARM)["revision"] == 1


def test_a_call_never_committed_and_then_deleted_reads_as_not_called(tree):
    """The honest remaining hole, asserted so a future reader finds the boundary stated
    rather than discovering it. The practical consequence is the same as `port-human`'s
    and stronger: this log can never be committed, so the arm's other artefacts are the
    only durable evidence — commit them."""
    repo = a_repo(tree)
    freeze_window(*ARM)
    a_call_was_made().unlink()
    assert called_where(*ARM) is None


def test_no_git_output_reaches_the_message(tree):
    """CLAUDE.md's rule about exception text does not branch on which file is safe, and a
    guard that pastes `git` output into a message is one schema change away from that
    mattering."""
    repo = a_repo(tree)
    freeze_window(*ARM)
    an_artefact_was_written()
    commit_all(repo, "a commit message that must not be quoted")
    with pytest.raises(OrchestrateError) as e:
        freeze_window(*ARM)
    assert "must not be quoted" not in str(e.value)
    assert str(tree) not in str(e.value)


# ─── drift ─────────────────────────────────────────────────────────────────

def test_no_drift_on_a_freshly_frozen_window(tree):
    freeze_window(*ARM)
    assert window_drift(*ARM) == []


def test_drift_names_the_field_that_moved(tree):
    freeze_window(*ARM)
    path = tree / "config" / "sampling.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# moved\n", encoding="utf-8")
    assert window_drift(*ARM) == ["sampling_sha256"]


def test_drift_on_an_unfrozen_arm_raises_rather_than_reporting_none(tree):
    """Returning [] for a missing record would read as "the window is intact"."""
    with pytest.raises(OrchestrateError) as e:
        window_drift(*ARM)
    assert "freeze_window()" in str(e.value)


def test_the_drift_message_after_a_call_says_restore_rather_than_re_create(tree):
    """A re-created record hashes today's files and then claims to be the window the call
    ran under — confidently wrong is worse than absent."""
    freeze_window(*ARM)
    a_call_was_made()
    freeze_path(*ARM).unlink()
    with pytest.raises(OrchestrateError) as e:
        window_drift(*ARM)
    assert "restore it from git" in str(e.value)


# ─── the baseline does not draw (DESIGN §4) ────────────────────────────────

def functions(module: Path) -> dict[str, ast.FunctionDef]:
    out = {}
    for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
    return out


def called_names(module: Path) -> set[str]:
    """Every name this module calls, as text. AST rather than a grep, so a call inside a
    docstring or a comment is not mistaken for one in the code."""
    names = set()
    for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def test_the_baseline_does_not_draw_or_render_error_spans():
    """**DESIGN §4's ladder condition, as a property of the code.**

    `port-oneshot` is `port-loop` truncated after call 1, so call 1 is shown §§1.1–1.2 in
    both arms and feedback begins at iteration 2. At iteration 1 the §1.4 spans come from
    an empty rule file — `initial_error_pool()` derives them from the loader, so they are
    dev **gold** spans, not model output. An arm shown 40 of them has dev information the
    other does not, and the two arms would then differ in two things at once: whether gold
    spans were seen, and whether the arm continues. A `port-loop` win would no longer be
    attributable to iteration, at the comparison the paper leads with.

    Asserted here rather than trusted to review, because the plumbing is a two-line
    addition that looks like an improvement.
    """
    calls = called_names(MODULE)
    for name in ("draw", "draw_iteration", "render_window", "initial_error_pool",
                 "practice_pool"):
        assert name not in calls, (
            f"src/orchestrate.py calls {name}() — the baseline would then be shown dev "
            "gold error spans, and `port-loop` vs `port-oneshot` would differ in two "
            "things instead of one (DESIGN §4)")


def test_the_module_imports_no_sampler_of_error_spans():
    """One level out from the call check: an import is the edit that precedes the call, and
    it is the one a reader skims past."""
    imported = set()
    for node in ast.walk(ast.parse(MODULE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    for name in ("draw", "draw_iteration", "render_window", "initial_error_pool"):
        assert name not in imported


def test_the_module_hardcodes_no_model_id():
    """`model_id` is a parameter end to end (DESIGN §10 A2). Nothing here may spell one:
    a recorded id that came from a literal is a record of what the code says rather than
    of what was called."""
    text = MODULE.read_text(encoding="utf-8")
    for fragment in ("anthropic.claude", "us.anthropic", "meta.llama", "amazon.nova"):
        assert fragment not in text
