"""Tests for src/orchestrate.py — the `port-oneshot` arm, and its window freeze.

Three things are under test and they are different in kind.

**The arm.** `run_arm()` is freeze → assemble → one call → validate → score-or-record. The
tests drive it end to end through `FakeRuntime` (`tests/test_bedrock.py`'s, imported rather
than re-invented) so that no test in this file needs a network or an AWS account, and they
assert the *order* as much as the outputs: the call is logged before the response is judged,
the cost block is written in both branches, and `metrics.json` is absent on a format failure
rather than present with zeros in it (DESIGN §10 A2). What each of those would look like if
it silently regressed is stated in the test that covers it.

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

The arm tests that reach `run_fold()` ask for `corpus_present` and skip without a corpus,
from `tests/conftest.py` and nowhere else — availability is answered from a path there, so a
skip cannot come to mean "the loader is broken". The format-failure tests do not need it and
do not ask, which is the point of them being separate: a machine with no MEDDOCAN checkout
still runs every assertion about what §10 A2 records.
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

from src import orchestrate, rules as rules_module, sample       # noqa: E402
from src.corpora.base import path_template                      # noqa: E402
from src.llm import bedrock as bedrock_module                   # noqa: E402
from src.orchestrate import (                                   # noqa: E402
    CALLED, FORMAT_FAILURE, INPUT_BLOCKS, IN_LOG, IN_RESULTS, ITERATION,
    ONESHOT_SECTIONS, REQUIRED_MODEL, SAMPLING_SECTION, SCORED,
    OrchestrateError, arm_has_called, call_line, called_where, failure_path,
    freeze_path, freeze_window, log_path, prompt_blocks, run_arm, window_drift,
)
from src.sample import WINDOW_FILES, window_hashes              # noqa: E402

# `FakeRuntime` and `reply()` come from the transport's own suite rather than being written
# again here. Two fakes of one `converse` shape are two shapes, and the one that drifts is
# the copy: `tests/test_bedrock.py`'s is kept honest by the tests that assert what the real
# client sends, and this file inherits that instead of inventing a client no measurement
# stands behind (the same reasoning `tests/conftest.py` gives for one availability check).
# Imported by module name: `tests/` has no `__init__.py`, so pytest's default import mode
# puts this directory on `sys.path` and `test_bedrock` is importable from here.
from test_bedrock import FakeRuntime, reply                      # noqa: E402

MODULE = ROOT / "src" / "orchestrate.py"

ARM = ("es-meddocan", "R", "sup-free")

#: The arm as `run_arm()` takes it, keyword for keyword.
ARM_KW = dict(corpus="es-meddocan", lang="es", detector="R", supervision="sup-free")

#: Passed to `run_arm(model_id=...)`. Undated on purpose — `alias-unresolved` is what the
#: real ladder records (`docs/notes/baseline-model-family.md`), so the arm's records are
#: exercised in the state they will actually be written in. Not a constant in
#: `src/orchestrate.py`: that file spells no model id at all, which is a test below.
MODEL = "us.anthropic.claude-opus-5"

#: A rule file that loads and fires on MEDDOCAN, standing in for what the model returns.
#: It has to actually match something: a file whose rules match nothing would let the
#: success branch pass while scoring an empty prediction set, and `metrics.json` with zeros
#: in it is the artefact the failure branch exists to avoid writing.
GOOD_RULES = """
version: 1
lang: es
rules:
  - rule_id: probe_org
    layer: gazetteer
    phi_type: ORGANISATION
    terms: ["Hospital"]
"""

#: What a model returns when it answers in prose, or fences its YAML, or stops mid-file.
#: Chosen to fail in the *parser* rather than in the schema, because that is the failure
#: `yaml.safe_load` used to raise as itself — out of the failure branch and into a
#: traceback (`src/rules.py`, the `YAMLError` clause).
UNPARSEABLE = "```yaml\nversion: 1\nlang: es\nrules: [\n"

#: Schema-valid YAML that is not a rule file. Fails in `load_rules`' own checks rather than
#: in the parser, so the two failure kinds are both covered by name.
WRONG_SHAPE = "version: 1\nlang: fr\nrules: []\n"


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


@pytest.fixture
def arm(tree, monkeypatch):
    """`tree`, with the Bedrock logging gate patched open and one root everywhere.

    Patched rather than satisfied, for the reason `tests/test_bedrock.py`'s own fixture
    gives: satisfying it means appending a real dated line to `docs/notes/compliance.md`,
    and that file is the project's compliance evidence — a test run must not write it. The
    gate itself is tested there, where the real function is captured at import before this
    kind of patch can reach it.

    `src.rules.ROOT` is redirected too, and only because a redirected root has to be one
    root. `rules._relative()` reduces a path against its own module's, so an unpatched
    `src/rules.py` would report every file this arm writes as `<outside-repo>/es.yaml` —
    true of `tmp_path` and false of the arm, which resolves against the same real root as
    that module in every run that is not a test. Left unpatched, the assertions about
    `rules_source` and `rules_path` would be assertions about the fixture.
    """
    monkeypatch.setattr(bedrock_module, "_require_logging_check", lambda: None)
    monkeypatch.setattr(rules_module, "ROOT", tree)
    return tree


def an_answer(text: str, **kw) -> FakeRuntime:
    """A transport that replies with `text`. Kept to one line at every call site."""
    return FakeRuntime(reply(text, **kw))


def calls(*arm) -> list[dict]:
    """Every line of this arm's `agent_calls.jsonl`, parsed."""
    path = log_path(*(arm or ARM))
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def failure_record(*arm) -> dict:
    return json.loads(failure_path(*(arm or ARM)).read_text(encoding="utf-8"))


def metrics_beside(*arm) -> Path:
    return freeze_path(*(arm or ARM)).parent / "metrics.json"


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


# ─── the arm: freeze → assemble → call → validate → score or record ─────────

def test_the_arm_runs_end_to_end_and_scores(arm, corpus_present):
    """The success path, asserted as artefacts rather than as a return value.

    Everything the mapping reports is also on disk, which is the property that makes the
    return value a convenience instead of the record. So the test reads the disk.
    """
    fake = an_answer(GOOD_RULES)
    out = run_arm(**ARM_KW, model_id=MODEL, client=fake)

    assert out["outcome"] == SCORED
    assert out["failure_path"] is None
    assert not failure_path(*ARM).exists()
    assert out["metrics_path"].exists() and out["spans_path"].exists()
    assert freeze_path(*ARM).exists()
    assert len(fake.calls) == 1, "one call, and `port-oneshot` is the arm that is one call"


def test_the_rule_file_goes_under_the_arm_and_not_to_the_bootstrap_path(arm,
                                                                       corpus_present):
    """DESIGN §5.3. `rules/es.yaml` is the committed format example; an arm writing there
    would overwrite the input of whichever arm ran before it and leave a plausible
    `metrics.json` behind, which is worse than an overwritten record because nothing about
    the numbers looks wrong afterwards."""
    out = run_arm(**ARM_KW, model_id=MODEL, client=an_answer(GOOD_RULES))
    assert out["rules_path"].relative_to(arm).as_posix() == (
        "results/es-meddocan/R/sup-free/port-oneshot/rules/iter1/es.yaml")
    assert not (arm / "rules" / "es.yaml").exists()


def test_the_response_is_written_verbatim(arm, corpus_present):
    """No fence stripping, no newline repair, no YAML round-trip. §10 A2 fixes format
    retries at zero, and a normalisation step is a retry with the count still reading
    zero — the same edit, with nothing in the record to show it happened."""
    text = GOOD_RULES + "\n# trailing comment the model wrote\n"
    out = run_arm(**ARM_KW, model_id=MODEL, client=an_answer(text))
    assert out["rules_path"].read_text(encoding="utf-8") == text


def test_the_arm_scores_the_file_it_just_wrote(arm, corpus_present):
    """`rules_source` in the metrics names the arm's own iteration-1 file. A run that
    scored the bootstrap file instead would publish numbers for rules the model did not
    write, and every other assertion here would still pass."""
    out = run_arm(**ARM_KW, model_id=MODEL, client=an_answer(GOOD_RULES))
    run = json.loads(out["metrics_path"].read_text(encoding="utf-8"))["run"]
    assert run["rules_source"]["es"].endswith(
        "results/es-meddocan/R/sup-free/port-oneshot/rules/iter1/es.yaml")
    assert run["rules"] == ["es:probe_org"]


def test_the_metrics_record_the_model_that_was_called(arm, corpus_present):
    """All three fields, and `model_id` is not the absent value.

    The failure this is against is quiet: `run_fold` fills `model_id` with `none` for the
    `R` arm, so an orchestrator that scored without passing its model record would publish
    an LLM arm's numbers under "no model was involved" — internally consistent, and wrong
    about the one thing DESIGN §10 A2 is for.
    """
    run_arm(**ARM_KW, model_id=MODEL, client=an_answer(GOOD_RULES))
    run = json.loads(metrics_beside().read_text(encoding="utf-8"))["run"]
    assert run["model_id"] == MODEL
    assert run["model_id_resolution"] == "alias-unresolved"
    assert "model_id_reported" in run


def test_the_metrics_cost_block_is_the_calls_and_not_zeros(arm, corpus_present):
    """CLAUDE.md: cost beside quality. An arm reporting `llm_calls: 0` after making one is
    an arm whose improvement looks free."""
    run_arm(**ARM_KW, model_id=MODEL,
            client=an_answer(GOOD_RULES, tokens=(4321, 8765)))
    cost = json.loads(metrics_beside().read_text(encoding="utf-8"))["cost"]
    assert cost["llm_calls"] == 1
    assert (cost["prompt_tokens"], cost["completion_tokens"]) == (4321, 8765)


def test_the_wall_clock_is_the_call_plus_the_detection_pass(arm, corpus_present,
                                                            monkeypatch):
    """Summed, not replaced. Both figures are this arm's compute, which is what makes them
    addable — and `human_minutes` deliberately is not (DESIGN §11.2), so the sum can never
    reach across that boundary.

    Driven from a call time large enough to tell the three candidate implementations apart:
    the caller's figure alone (a fold-wide rule pass reported at zero seconds), the
    detection pass alone (`run_fold`'s own measurement overwriting what it was handed, which
    is what the parameter's default does), and the sum. Only the third is greater than both.
    """
    slow_call = 300.0
    real = orchestrate.invoke

    def slow(*a, **kw):
        response = real(*a, **kw)
        object.__setattr__(response, "wall_seconds", slow_call)
        return response

    monkeypatch.setattr(orchestrate, "invoke", slow)
    out = run_arm(**ARM_KW, model_id=MODEL, client=an_answer(GOOD_RULES))
    reported = json.loads(out["metrics_path"].read_text(encoding="utf-8"))["cost"]
    assert reported["wall_seconds"] > slow_call, (
        "the detection pass's time went missing, so the arm's wall clock is the call's "
        "alone")
    assert out["cost"]["wall_seconds"] == slow_call, (
        "the returned cost is the call's own; the sum belongs to the arm's metrics")


# ─── the call is logged before the response is judged ───────────────────────

def test_the_call_is_logged_even_when_the_response_does_not_load(arm):
    """The ordering, as the property it protects.

    The log line is what fixes the window (`called_where`), so a run that validated first
    would leave the window re-freezable for the duration of a parse — and a parse that
    raised would leave a call made, paid for, and unrecorded.
    """
    run_arm(**ARM_KW, model_id=MODEL, client=an_answer(UNPARSEABLE))
    logged = calls()
    assert len(logged) == 1
    assert logged[0]["outcome"] == CALLED, (
        "the outcome is what was known when the line was written: the response arrived "
        "and nothing had judged it yet")


def test_a_call_fixes_the_window_through_the_arm(arm):
    """Not through the test helper. `a_call_was_made()` writes a line by hand, so every
    freeze-refusal test above would pass against an orchestrator that logged nothing."""
    run_arm(**ARM_KW, model_id=MODEL, client=an_answer(UNPARSEABLE))
    assert called_where(*ARM) == IN_LOG
    assert arm_has_called(*ARM)
    with pytest.raises(OrchestrateError):
        freeze_window(*ARM)


def test_the_log_line_carries_no_prompt_and_no_response(arm):
    """`rule_author.md` §6 names this file: the reference form may be recorded and the text
    may not. The response is reduced to a length and a hash — enough to answer "was this
    the output that was scored", and not enough to reconstitute a completion that echoed
    its own §1.4 block. This log is deny-listed and never committed, so a copy here is a
    copy in the one place no review reaches."""
    fake = an_answer(UNPARSEABLE)
    run_arm(**ARM_KW, model_id=MODEL, client=fake)
    line = calls()[0]
    text = json.dumps(line, ensure_ascii=False)
    sent = fake.calls[0]["messages"][0]["content"][0]["text"]
    assert UNPARSEABLE not in text
    assert sent not in text
    for fragment in sent.split("\n"):
        if len(fragment.strip()) > 40:
            assert fragment not in text
    assert line["response_chars"] == len(UNPARSEABLE)
    assert line["response_sha256"].startswith("sha256:")


def test_the_sample_reference_is_present_and_null(arm):
    """Written as an explicit absence rather than omitted, for `model_id_absent`'s reason
    (DESIGN §4): a key some arms omit cannot be read across arms, and a null nobody wrote
    cannot be told from one that was measured. This arm's §1.4 is empty by definition, so
    the honest record is a null — and `port-loop`'s iteration 2 fills the same key."""
    run_arm(**ARM_KW, model_id=MODEL, client=an_answer(UNPARSEABLE))
    line = calls()[0]
    assert "sample_reference" in line
    assert line["sample_reference"] is None


def test_the_log_line_carries_the_window_hashes_and_the_iteration(arm):
    """DESIGN §11.2 puts the hashes on every line. This arm has one line and its freeze
    record would do — the field is the *iterating* arms' mid-run drift detector, and a
    writer that omitted it here is the writer `port-loop` gets copied from."""
    run_arm(**ARM_KW, model_id=MODEL, client=an_answer(UNPARSEABLE))
    line = calls()[0]
    assert line["iteration"] == ITERATION == 1
    for field, value in window_hashes().items():
        assert line[field] == value


def test_an_unknown_outcome_is_refused_by_the_log_line(arm):
    """The log's own vocabulary rather than an axis — it describes one call's fate instead
    of naming a cell of the experiment — but closed all the same, because a field filled
    with anything once is a field nothing can aggregate."""
    with pytest.raises(OrchestrateError) as e:
        call_line(1, prompt_reference={}, model={}, response_chars=0,
                  response_sha256="sha256:0", outcome="ok", cost={})
    assert "not a call outcome" in str(e.value)


# ─── the format failure is a result, not an accident (DESIGN §10 A2) ────────

def test_an_unparseable_response_is_recorded_and_not_retried(arm):
    """Zero format-compliance retries. One call, and what came back is the finding."""
    fake = an_answer(UNPARSEABLE)
    out = run_arm(**ARM_KW, model_id=MODEL, client=fake)
    assert out["outcome"] == FORMAT_FAILURE
    assert len(fake.calls) == 1, "a second call is a retry however it is spelled"
    assert failure_path(*ARM).exists()


def test_a_format_failure_writes_no_metrics_file(arm):
    """Zeros there would be indistinguishable from a rule set that ran and caught nothing,
    which is the opposite finding. One file or the other, and the filename is the answer —
    which is also why this is not a `status` field inside `metrics.json`: the scorer schema
    would then have to admit a metrics file with no score in it."""
    out = run_arm(**ARM_KW, model_id=MODEL, client=an_answer(UNPARSEABLE))
    assert out["metrics_path"] is None and out["spans_path"] is None
    assert not metrics_beside().exists()
    assert not (metrics_beside().parent / "spans.jsonl").exists()


def test_the_failure_record_holds_the_validators_own_message(arm):
    """§10 A2's third content, verbatim. "The file was malformed" is not something a reader
    can check, and the appendix's claim is that *this model* could not do it."""
    run_arm(**ARM_KW, model_id=MODEL, client=an_answer(WRONG_SHAPE))
    record = failure_record()
    assert "declares lang 'fr'" in record["error"]


def test_the_failure_record_holds_the_raw_response(arm):
    """The one path in this repository where a model's raw output reaches disk. It is on
    the screener's ALLOW list as a *path* and the content sniffer still runs first, because
    "this arm's call carries §§1.1–1.2 only" is a fact about today's arm rather than a
    property of the path."""
    run_arm(**ARM_KW, model_id=MODEL, client=an_answer(UNPARSEABLE))
    record = failure_record()
    assert record["response"] == UNPARSEABLE
    assert record["response_chars"] == len(UNPARSEABLE)


def test_the_failure_record_holds_the_three_model_fields_and_the_cost(arm):
    """The cost block is written in both branches: the call was made and paid for either
    way, and an arm whose failures were free would make a format-failure rate look like a
    saving."""
    run_arm(**ARM_KW, model_id=MODEL,
            client=an_answer(UNPARSEABLE, tokens=(11, 22)))
    record = failure_record()
    for field in REQUIRED_MODEL:
        assert field in record
    assert record["model_id"] == MODEL
    assert record["cost"] == {"llm_calls": 1, "prompt_tokens": 11,
                              "completion_tokens": 22,
                              "wall_seconds": record["cost"]["wall_seconds"]}


def test_the_failure_record_names_the_file_it_could_not_load(arm):
    """The failure is about a file, and a reader who cannot find the file has the message
    and not the artefact. Repo-relative, through `rules._relative` rather than a third
    spelling of it — an absolute path names a home directory and, on a machine where the
    corpus sits beside the repo, a DUA layout."""
    out = run_arm(**ARM_KW, model_id=MODEL, client=an_answer(UNPARSEABLE))
    record = failure_record()
    assert record["rules_path"].endswith("rules/iter1/es.yaml")
    assert not record["rules_path"].startswith("/")
    assert out["rules_path"].read_text(encoding="utf-8") == UNPARSEABLE, (
        "the response is kept at the path the record names, so the artefact and the "
        "message are both available")


def test_a_yaml_parse_error_does_not_escape_as_a_traceback(arm):
    """The specific regression: `yaml.safe_load` raising `YAMLError` out of `load_rules`.
    A fenced code block is the likeliest single format failure, and as an uncaught parser
    exception it would come out as a crash instead of the recorded, reportable result §10
    A2 asks for."""
    out = run_arm(**ARM_KW, model_id=MODEL, client=an_answer(UNPARSEABLE))
    assert out["outcome"] == FORMAT_FAILURE
    assert "not parseable as YAML" in failure_record()["error"]


def test_the_parse_error_message_quotes_no_line_of_the_file(arm):
    """CLAUDE.md, one file format over from the span rule.

    `MarkedYAMLError` renders the offending source line into its own message, and that
    message travels to terminals, CI logs and issues where `release_screen.py` never looks.
    A response can echo its prompt, so a parser message pasted through would be corpus text
    on a path no screening reaches. Position reported, content not.
    """
    marker = "Hospital Universitario de Zzyzx"
    response = f"version: 1\nlang: es\nrules: [{{a: b\nnote: {marker}\n"
    run_arm(**ARM_KW, model_id=MODEL, client=an_answer(response))
    error = failure_record()["error"]
    assert marker not in error
    assert "line" in error and "column" in error


# ─── what the arm refuses ──────────────────────────────────────────────────

def test_an_empty_model_id_is_refused(arm):
    """`model_id` is a parameter the whole way down (DESIGN §10 A2). A default here would
    be the place it stopped being one, and the recorded value would then describe this file
    rather than the call."""
    with pytest.raises(OrchestrateError) as e:
        run_arm(**ARM_KW, model_id="", client=an_answer(GOOD_RULES))
    assert "model_id is required" in str(e.value)


def test_a_lang_the_corpus_does_not_load_is_refused(arm):
    """One call authors one file, and a file no corpus loads would be scored by nothing
    (DESIGN §5.2, `corpus_rule_langs`). Refused before the call, so nothing is paid for a
    result that cannot be read."""
    fake = an_answer(GOOD_RULES)
    with pytest.raises(OrchestrateError) as e:
        run_arm(**{**ARM_KW, "lang": "de"}, model_id=MODEL, client=fake)
    assert "corpus_rule_langs" in str(e.value)
    assert fake.calls == [], "refused after the call is a refusal that costs money"


def test_the_arm_refuses_after_its_call_has_been_made(arm):
    """The freeze guard, reached through the arm rather than through the helper. A second
    `run_arm()` on a called arm would re-freeze the window and then overwrite iteration 1's
    rule file — the first is refused, which is what stops the second."""
    run_arm(**ARM_KW, model_id=MODEL, client=an_answer(UNPARSEABLE))
    with pytest.raises(OrchestrateError):
        run_arm(**ARM_KW, model_id=MODEL, client=an_answer(GOOD_RULES))


def test_the_run_block_requires_all_three_model_fields(arm, monkeypatch):
    """Required *here* and not in `scorer.REQUIRED_RUN`, deliberately (DESIGN §10 A2,
    schema 4).

    `run_fold` closes the `R` arm, which calls no model and cannot observe two of the three;
    requiring them there would have that writer fill them with placeholders on every run,
    and a required field one writer fills with a placeholder makes the placeholder the
    convention. So the requirement lives where the observation does — and it is enforced
    rather than assumed, which is what this drives: a transport returning a response short
    of one field gets a refusal instead of a metrics file whose resolution claim is blank.

    The response is dismantled through `Response` rather than through the run block, so the
    guard is reached the way a shape change in the transport would reach it.
    """
    real = orchestrate.invoke

    def resolution_lost(*a, **kw):
        # `Response` is frozen: this is the state a field lost between the two modules would
        # actually arrive in, and it is unreachable through the constructor's own path.
        response = real(*a, **kw)
        object.__setattr__(response, "model_id_resolution", "")
        return response

    monkeypatch.setattr(orchestrate, "invoke", resolution_lost)
    with pytest.raises(OrchestrateError) as e:
        run_arm(**ARM_KW, model_id=MODEL, client=an_answer(GOOD_RULES))
    assert "model_id_resolution" in str(e.value)
    assert not metrics_beside().exists()


# ─── the arm is shown §§1.1–1.2 and nothing else ────────────────────────────

def test_the_prompt_sent_carries_the_task_frame_and_an_empty_rule_file(arm):
    """DESIGN §4's ladder condition, at the transport rather than in the AST.

    The structural gates below assert that nothing here *calls* a sampler. This asserts
    what actually went over the wire, which is the claim the gates are a proxy for: the
    filled §1.1 and §1.2, §1.2 stating that there is no rule file yet, and §§1.3–1.4
    arriving named and empty.

    **Note what is not asserted.** Not "`### 1.4` does not appear": the whole of
    `rule_author.md` is sent as the template, so its own §1.4 *heading* is in there and
    always will be. A test written that way would pass today and would go on passing after
    a drawn sample was appended under that heading, which is the regression it looked like
    it was for. The claim available at this level is that the block is stated empty, and
    `test_the_freeze_record_and_the_prompt_agree_on_the_sections` is the other half.
    """
    fake = an_answer(UNPARSEABLE)
    run_arm(**ARM_KW, model_id=MODEL, client=fake)
    sent = fake.calls[0]["messages"][0]["content"][0]["text"]
    assert "### 1.1" in sent and "### 1.2" in sent
    assert "EMPTY. There is no current rule file" in sent
    assert "### 1.3 — EMPTY for this call" in sent
    assert "### 1.4 — EMPTY for this call" in sent
    assert "there are no scores and no error spans" in sent


def test_the_arm_does_not_show_the_bootstrap_rule_file(arm):
    """§1.2 is the *arm's* current rule file and this arm has none. Passing
    `rules/es.yaml` would show the committed format example as though it were the current
    rule set — the bootstrap file doing a job DESIGN §5.3 took off it, and the agent would
    then be editing rules nobody in this arm wrote."""
    bootstrap = arm / "rules"
    bootstrap.mkdir(parents=True, exist_ok=True)
    (bootstrap / "es.yaml").write_text(GOOD_RULES, encoding="utf-8")
    fake = an_answer(UNPARSEABLE)
    run_arm(**ARM_KW, model_id=MODEL, client=fake)
    sent = fake.calls[0]["messages"][0]["content"][0]["text"]
    assert "probe_org" not in sent
    assert "EMPTY. There is no current rule file" in sent
    line = calls()[0]["prompt_reference"]
    assert line["rules_empty"] is True and line["rules_source"] is None


def test_the_freeze_record_and_the_prompt_agree_on_the_sections(arm):
    """Two records of one fact, in different words, and the point is that they can be
    compared. The freeze record says which blocks the call carried; the prompt is what it
    carried. A record claiming §§1.1–1.2 beside a prompt holding §1.4 is the state the
    `sampling_applied` field exists to make visible."""
    fake = an_answer(UNPARSEABLE)
    run_arm(**ARM_KW, model_id=MODEL, client=fake)
    record = json.loads(freeze_path(*ARM).read_text(encoding="utf-8"))
    assert record["sections_shown"] == ["1.1", "1.2"]
    assert record["sampling_applied"] is False
    reference = calls()[0]["prompt_reference"]
    assert reference["sections_filled"] == record["sections_shown"]
    assert reference["sections_empty"] == record["sections_empty"]
