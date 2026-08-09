"""What the seal's own functions do, as opposed to whether they are called.

`tests/test_seal.py` covers the call sites: the gate refuses an unauthorised caller, a
failed append leaves the fold unreachable, a dirty tree is refused. To do that it patches
`sealed_log.tree_state` and `run_sealed_eval._verify_frozen_split` — which is the right
substitution for what those tests are about, and it leaves the patched functions
themselves untested. An audit found:

  - `tree_state` was patched four times and executed by nothing. Feeding it
    `("abc123", "dirty")` proves the *refusal* works; it says nothing about whether a
    dirty tree is ever reported as dirty.
  - `_verify_frozen_split` appeared in the suite **only** as a patch target. Never
    executed, and no mutation aimed at it.
  - `src/eval/sealed_log.py` and `src/eval/run_sealed_eval.py` had no mutation at all.
    Both seal mutations were in `src/corpora/base.py`, the call sites.

`test_seal.py:152` already states the principle these tests restore — the substitution
belongs at the *data* and never at the guarantee. This file is that principle applied to
the two functions where it had lapsed, and the general form is
`test_patch_targets.py`.

**The same shape as `tools/check_bedrock_logging.py`.** `tree_state`'s `unknown` branch
and that tool's `except ClientError` are one question: *what happens when the state
cannot be read?* Both answer it in a line nothing executed. See
`tests/mutations/README.md`, "Unreadable state, twice".

Two rules from `test_seal.py` hold here unchanged: **no test reads a real sealed fold**,
and **no test writes the real log**. Everything below runs against a git repository and a
corpus built in `tmp_path`.

    python3 -m pytest tests/test_seal_internals.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.corpora import base  # noqa: E402
from src.corpora.base import CorpusError, Document, SealError  # noqa: E402
from src.eval import run_sealed_eval, sealed_log  # noqa: E402

CORPUS = "es-meddocan"


# ─── a real repository, because that is what is under test ──────────────────


def a_repo(path):
    """A git repository with one commit, and identity set so commits work anywhere.

    Real `git` rather than a fake: `tree_state` shells out, and the thing being tested is
    what it makes of the output. A fake `subprocess.run` would be a test of the fake.
    """
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(a, cwd=path, check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.invalid")
    run("git", "config", "user.name", "test")
    (path / "tracked.txt").write_text("committed content\n", encoding="utf-8")
    run("git", "add", "tracked.txt")
    run("git", "commit", "-q", "-m", "initial")
    return path


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """`sealed_log.ROOT` pointed at a fresh repository.

    The patch is on the *directory* `_git` runs in, which is data. `tree_state`,
    `_git`, and the branch that decides clean/dirty/unknown all execute for real —
    that distinction is the whole point of this file.
    """
    path = a_repo(tmp_path / "repo")
    monkeypatch.setattr(sealed_log, "ROOT", path)
    return path


# ─── tree_state: clean ──────────────────────────────────────────────────────


def test_a_clean_repository_reports_clean_and_a_real_commit(repo):
    commit, tree = sealed_log.tree_state()
    assert tree == "clean"
    assert commit is not None and len(commit) == 40
    assert all(c in "0123456789abcdef" for c in commit)


def test_the_commit_is_the_repositorys_head(repo):
    """Not merely well-formed — the actual HEAD. A hash from anywhere else would name
    code that did not run, which is the failure the `dirty` refusal exists to prevent."""
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
        check=True).stdout.strip()
    assert sealed_log.tree_state()[0] == expected


# ─── tree_state: dirty, in each of the three ways ───────────────────────────
#
# Three separate cases because `git status --porcelain` reports them with different
# markers and the code's `if porcelain:` is a truth test on the whole output. A version
# that only noticed modifications would pass a one-case test.


def test_a_modified_tracked_file_makes_the_tree_dirty(repo):
    (repo / "tracked.txt").write_text("edited after the commit\n", encoding="utf-8")
    assert sealed_log.tree_state()[1] == "dirty"


def test_an_untracked_file_makes_the_tree_dirty(repo):
    """`--porcelain` lists untracked files as `??`, and an untracked source file is code
    that would run and is in no commit."""
    (repo / "new_module.py").write_text("x = 1\n", encoding="utf-8")
    assert sealed_log.tree_state()[1] == "dirty"


def test_a_staged_but_uncommitted_change_makes_the_tree_dirty(repo):
    """Staged is not committed. The hash still describes the code before the change."""
    (repo / "tracked.txt").write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True,
                   capture_output=True)
    assert sealed_log.tree_state()[1] == "dirty"


def test_a_dirty_tree_still_reports_its_commit(repo):
    """The row records both. A dirty tree does not mean an unknown commit — it means the
    known commit does not describe the code, which is a different sentence."""
    (repo / "tracked.txt").write_text("edited\n", encoding="utf-8")
    commit, tree = sealed_log.tree_state()
    assert tree == "dirty"
    assert commit is not None and len(commit) == 40


def test_committing_the_change_makes_it_clean_again(repo):
    """Both directions from one repository: a test that only ever saw `dirty` could be
    passing on a function that returns `dirty` unconditionally."""
    (repo / "tracked.txt").write_text("edited\n", encoding="utf-8")
    assert sealed_log.tree_state()[1] == "dirty"
    subprocess.run(["git", "commit", "-qam", "the change"], cwd=repo, check=True,
                   capture_output=True)
    assert sealed_log.tree_state()[1] == "clean"


# ─── tree_state: unknown — the branch nothing executed ──────────────────────
#
# The audit's sharpest finding, and the same question `check_bedrock_logging.py` answers
# for an IAM denial: when the state cannot be read, what is reported? A `clean` here would
# be the manufactured-evidence failure — a log row asserting the tree was clean because
# git could not be asked.


def test_a_directory_that_is_not_a_repository_is_unknown(tmp_path, monkeypatch):
    plain = tmp_path / "not_a_repo"
    plain.mkdir()
    monkeypatch.setattr(sealed_log, "ROOT", plain)
    commit, tree = sealed_log.tree_state()
    assert tree == "unknown"
    assert commit is None


def test_a_repository_with_no_commits_is_unknown(tmp_path, monkeypatch):
    """`rev-parse HEAD` fails before the first commit, so there is no hash to record."""
    fresh = tmp_path / "empty_repo"
    fresh.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=fresh, check=True, capture_output=True)
    monkeypatch.setattr(sealed_log, "ROOT", fresh)
    assert sealed_log.tree_state() == (None, "unknown")


def test_git_being_absent_is_unknown_and_not_clean(repo, monkeypatch):
    """`_git` catches `FileNotFoundError` for a missing git binary. Reporting that as
    `clean` would be the same defect as reading an IAM denial as "logging is off"."""
    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(sealed_log.subprocess, "run", no_git)
    assert sealed_log.tree_state() == (None, "unknown")


def test_unknown_is_not_clean_and_is_therefore_refused(tmp_path, monkeypatch):
    """The consequence, end to end. `load_sealed` refuses anything that is not `clean`,
    so `unknown` must be refused — and this is the test that would fail if the
    `commit is None or porcelain is None` branch ever returned `clean`."""
    plain = tmp_path / "not_a_repo"
    plain.mkdir()
    monkeypatch.setattr(sealed_log, "ROOT", plain)
    with pytest.raises(SealError, match="the working tree is unknown"):
        run_sealed_eval.load_sealed(CORPUS, purpose="unreadable tree state")


def test_the_three_states_are_exactly_the_documented_ones(repo):
    """The row's `tree` cell is read by whoever counts the paper's N, and the docstring
    promises three values. A fourth would be undocumented vocabulary in a results file."""
    states = set()
    states.add(sealed_log.tree_state()[1])
    (repo / "tracked.txt").write_text("edited\n", encoding="utf-8")
    states.add(sealed_log.tree_state()[1])
    assert states <= {"clean", "dirty", "unknown"}
    assert states == {"clean", "dirty"}, "the repository fixture produced neither state"


# ─── _verify_frozen_split: executed, at last ────────────────────────────────
#
# Zero executions and zero mutations before this section. What it guards is real: if the
# corpus on disk has moved a document between folds since the freeze, then the split file
# no longer describes the data, and a document could have crossed the seal.


class TinyLoader(base.CorpusLoader):
    """A loader over documents held in memory, with the real `load()` machinery.

    Subclasses `CorpusLoader` so `load()`, `_apply_split_file` and the offset assertions
    all run; only `_read()` is replaced, which is the data.
    """

    corpus_id = CORPUS
    fold_dirs = {"train": "train", "dev": "dev", "test": "test"}

    def __init__(self, docs):
        self.docs = docs
        self.root = None
        self.use_split_file = False
        self._sealed_ok = False

    def _read(self):
        return iter(self.docs)


def a_doc(doc_id, fold):
    return Document(doc_id=doc_id, corpus_id=CORPUS, text="xxxxx", spans=[], split=fold)


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """A schema-valid split file on disk, redirected there rather than faked.

    `_verify_frozen_split` calls `src.split.read`, so the substitution is the *path* the
    split file is read from — data again. Everything downstream of the read runs for real,
    including `check_schema`, which is why the record is built with the module's own
    `fold_summary` rather than hand-written: a hand-written stub fails schema validation,
    and the version of this fixture that skipped the schema would have been testing
    `_verify_frozen_split` against input the real `read()` would never hand it.
    """
    from src import split as split_mod

    docs = {
        "train": [a_doc("d1", "train"), a_doc("d2", "train")],
        "dev": [a_doc("d3", "dev")],
        "test": [a_doc("d4", "test")],
    }
    record = {
        "schema_version": split_mod.SCHEMA_VERSION,
        "corpus": CORPUS,
        "generated": "2026-01-01T00:00:00Z",
        "generated_by": "tests/test_seal_internals.py",
        "repository": "agentic-deid",
        "tokenizer": split_mod.TOKENIZER,
        "provenance": {"note": "synthetic, for testing the drift check"},
        "group_key": "document",
        "source": {"note": "in-memory documents"},
        "folds": {
            fold: {
                **split_mod.fold_summary(fold_docs),
                "document_ids": sorted(d.doc_id for d in fold_docs),
            }
            for fold, fold_docs in docs.items()
        },
        "totals": split_mod.fold_summary([d for ds in docs.values() for d in ds]),
        "corpus_specific": {},
    }
    path = tmp_path / f"{CORPUS}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(split_mod, "split_path", lambda corpus_id: path)
    return record


def test_a_corpus_matching_the_frozen_file_passes(frozen):
    loader = TinyLoader([a_doc("d1", "train"), a_doc("d2", "train"), a_doc("d3", "dev")])
    run_sealed_eval._verify_frozen_split(loader, CORPUS)


def test_a_document_that_moved_folds_is_refused(frozen):
    """The drift this guard exists for: `d3` was dev at the freeze and is train on disk.
    Either the corpus was re-released or the split file was edited, and in both cases the
    sealed fold's contents are no longer the ones that were sealed."""
    loader = TinyLoader([a_doc("d1", "train"), a_doc("d3", "train")])
    with pytest.raises(SealError, match="disagree about this document's fold"):
        run_sealed_eval._verify_frozen_split(loader, CORPUS)


def test_a_document_absent_from_the_frozen_file_is_refused(frozen):
    """`assigned.get()` returns None for an unknown id, which cannot equal a fold. A new
    document appearing after the freeze is drift in the other direction."""
    loader = TinyLoader([a_doc("d1", "train"), a_doc("d99", "dev")])
    with pytest.raises(SealError, match="disagree about this document's fold"):
        run_sealed_eval._verify_frozen_split(loader, CORPUS)


def test_the_check_never_reads_the_sealed_fold(frozen):
    """It calls `CorpusLoader.load(loader)` — the unsealed load — by construction. If it
    ever authorised a sealed read, the verification would consume a log row every time,
    and the row count is the paper's N."""
    loader = TinyLoader([a_doc("d1", "train"), a_doc("d3", "dev")])
    run_sealed_eval._verify_frozen_split(loader, CORPUS)
    assert loader._sealed_ok is False


def test_the_refusal_message_carries_no_document_text(frozen):
    """CLAUDE.md: ids and folds, never surfaces. This message names a doc_id, which is an
    identifier in the split file rather than corpus content."""
    surface = "Zzyzx Quinbolt"
    doc = Document(doc_id="d3", corpus_id=CORPUS, text=surface, spans=[], split="train")
    loader = TinyLoader([doc])
    with pytest.raises(SealError) as e:
        run_sealed_eval._verify_frozen_split(loader, CORPUS)
    assert surface not in str(e.value)


def test_the_check_runs_before_the_sealed_read(frozen, monkeypatch, tmp_path):
    """Order is the guarantee, not presence: verifying after the read would mean the fold
    was opened and a row spent before anyone noticed the corpus had drifted."""
    order = []
    monkeypatch.setattr(sealed_log, "tree_state", lambda: ("a" * 40, "clean"))
    monkeypatch.setattr(run_sealed_eval, "_loader_for", lambda corpus_id: TinyLoader([]))
    monkeypatch.setattr(
        run_sealed_eval, "_verify_frozen_split",
        lambda loader, corpus_id: order.append("verify"))

    class Refusing(TinyLoader):
        def load(self, *a, **k):
            order.append("load")
            raise CorpusError("stopped deliberately")

    monkeypatch.setattr(run_sealed_eval, "_loader_for", lambda corpus_id: Refusing([]))
    with pytest.raises(CorpusError, match="stopped deliberately"):
        run_sealed_eval.load_sealed(CORPUS, purpose="ordering check")
    assert order == ["verify", "load"]


# ─── _loader_for, the one the structural check found ─────────────────────────


def test_an_unknown_corpus_has_no_loader():
    """`_loader_for` is patched in five tests above and, until this one, executed by none —
    which is exactly what `tools/check_patched_guarantees.py` reported on its first run.

    Substituting it is right in those tests: a loader is *data* to the function under test,
    and a real MEDDOCAN loader would need the corpus on the machine. But its own refusal
    branch is the difference between an unimplemented corpus failing here, before the seal
    is touched, and failing somewhere further in. It needs one test that runs it, and no
    corpus is required to reach the branch that says there is no loader.
    """
    with pytest.raises(CorpusError, match="has no loader yet"):
        run_sealed_eval._loader_for("de-grascco")


def test_the_no_loader_message_names_what_does_exist():
    """A refusal that lists the implemented corpora answers the next question too. Corpus
    *ids* are naming.yaml vocabulary, not corpus content."""
    with pytest.raises(CorpusError) as e:
        run_sealed_eval._loader_for("not-a-corpus")
    assert "es-meddocan" in str(e.value)


# ─── sealed_log's row construction, run rather than patched ─────────────────


@pytest.fixture
def temp_log(tmp_path, monkeypatch):
    """A throwaway log. Never the real one: its row count is a reported number."""
    target = tmp_path / "sealed_eval_log.md"
    target.write_text(sealed_log.LOG.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(sealed_log, "LOG", target)
    return target


def test_the_row_records_the_repositorys_real_tree_state(temp_log, repo):
    """`test_seal.py` asserts the cell is one of three values. This asserts it is the
    *right* one, against a repository whose state the test controls."""
    (repo / "tracked.txt").write_text("edited\n", encoding="utf-8")
    row = sealed_log.record_access(CORPUS, purpose="dirty on purpose")
    cells = [c.strip() for c in row.split("|")[1:-1]]
    assert cells[3] == "dirty"


def test_the_row_records_clean_when_the_tree_is_clean(temp_log, repo):
    row = sealed_log.record_access(CORPUS, purpose="clean on purpose")
    assert [c.strip() for c in row.split("|")[1:-1]][3] == "clean"


def test_an_unknown_tree_state_is_recorded_as_unknown(temp_log, tmp_path, monkeypatch):
    """Written down rather than defaulted. A row claiming `clean` because git could not
    be reached is the failure; a row saying `unknown` is the honest version."""
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setattr(sealed_log, "ROOT", plain)
    row = sealed_log.record_access(CORPUS, purpose="no repository here")
    cells = [c.strip() for c in row.split("|")[1:-1]]
    assert cells[2] == "unknown"  # the commit column
    assert cells[3] == "unknown"  # the tree column


def test_count_runs_counts_rows_and_not_lines(temp_log):
    """The paper's N. Header rows, separators and the freeze-commit prose are all lines
    beginning with something other than a digit."""
    before = sealed_log.count_runs()
    sealed_log.record_access(CORPUS, purpose="one")
    sealed_log.record_access(CORPUS, purpose="two")
    assert sealed_log.count_runs() == before + 2


def test_count_runs_is_zero_for_a_missing_log(tmp_path, monkeypatch):
    """Zero rather than raising: "the log is gone" and "nothing has run" differ, and
    `record_access` is what refuses the former. Counting must not crash a report."""
    monkeypatch.setattr(sealed_log, "LOG", tmp_path / "gone.md")
    assert sealed_log.count_runs() == 0


def test_the_placeholder_is_replaced_by_the_first_row(tmp_path, monkeypatch):
    """Otherwise the table shows "no sealed evaluation has been run" beside a run."""
    target = tmp_path / "log.md"
    target.write_text(
        "| # | when | commit | tree | corpus | fold | arms | purpose |\n"
        "|---|---|---|---|---|---|---|---|\n"
        f"| {sealed_log.PLACEHOLDER} | | | | | | | |\n",
        encoding="utf-8")
    monkeypatch.setattr(sealed_log, "LOG", target)
    sealed_log.record_access(CORPUS, purpose="the first run")
    text = target.read_text(encoding="utf-8")
    assert sealed_log.PLACEHOLDER not in text
    assert "the first run" in text
    assert sealed_log.count_runs() == 1


def test_a_log_with_no_run_rows_is_refused_rather_than_guessed(tmp_path, monkeypatch):
    """`_insert_after_last_row` has nowhere to insert. Refused, because the alternative
    is appending somewhere plausible in the file that holds the reported count."""
    target = tmp_path / "log.md"
    target.write_text("# Sealed evaluation log\n\nno table at all\n", encoding="utf-8")
    monkeypatch.setattr(sealed_log, "LOG", target)
    with pytest.raises(SealError, match="found no run rows"):
        sealed_log.record_access(CORPUS, purpose="nowhere to append")


def test_the_row_is_verified_present_after_writing(temp_log, monkeypatch):
    """`record_access` re-reads the file and raises if the row is not there. A write that
    reported success without persisting would leave the fold reachable and unlogged."""
    row = sealed_log.record_access(CORPUS, purpose="written and confirmed")
    assert row in temp_log.read_text(encoding="utf-8")


def test_a_write_failure_raises_and_does_not_return_a_row(temp_log, monkeypatch):
    """The append is what stands between the caller and the fold, so its failure has to
    be an exception rather than a falsy return anybody could ignore."""
    def refuse(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(type(temp_log), "write_text", refuse)
    with pytest.raises(SealError, match="could not append"):
        sealed_log.record_access(CORPUS, purpose="unwritable log")


def test_the_row_number_continues_the_existing_table(temp_log):
    """Numbers are how a reader cross-references the paper's N against the log. Restarting
    at 1 after an edit would make two rows share a number."""
    target = temp_log
    target.write_text(
        "| # | when | commit | tree | corpus | fold | arms | purpose |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| 7 | 2026-01-01T00:00:00Z | abc | clean | es-meddocan | test | none | old |\n",
        encoding="utf-8")
    row = sealed_log.record_access(CORPUS, purpose="the eighth")
    assert row.split("|")[1].strip() == "8"
