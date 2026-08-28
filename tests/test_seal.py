"""Tests for the seal: physical separation plus the code gate (DESIGN §6).

These tests must never read the sealed fold, which makes them unusual — most of
them check that something *cannot* happen. Two rules followed throughout:

  - **No test authorises a sealed read of a real corpus.** Doing so would open the
    test fold and consume a row in `results/sealed_eval_log.md`, which is a reported
    number. The gate is exercised against a synthetic loader over `tmp_path`
    instead, so the authorised path is genuinely tested and the real fold is not
    touched.
  - **The log is never written by a test.** Every test that exercises logging points
    `sealed_log.LOG` at a temporary copy. A suite that appended to the real log
    would make the count meaningless, and the count is the headline.

    python3 -m pytest tests/test_seal.py -q
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.corpora import CorpusError, SealError, base  # noqa: E402
from src.corpora.meddocan import MeddocanLoader  # noqa: E402
from src.eval import sealed_log  # noqa: E402

CORPUS = "es-meddocan"

#: The arm and round every logged row in this file carries. Real `naming.yaml` values,
#: because `sealed_log.Arm` validates them — a placeholder string is exactly what the
#: type was introduced to refuse (2026-08-26).
ARM = sealed_log.Arm(detector="R", supervision="sup-free", porting="port-loop")
ROUND = 8


def a_plan(corpus=CORPUS, *, iteration=ROUND):
    """An `ArmPlan` built by hand, bypassing `plan_arm`.

    Deliberate, and the reason is that these tests are about the *gate* and not the
    planner. `plan_arm` reads a committed dev `metrics.json` and refuses eight ways;
    exercising it here would make every gate test depend on a real arm's results
    directory, and a change to that arm would then break tests about the seal. The
    planner's refusals are tested against the real records in
    `tests/test_sealed_scoring.py`.

    `rules` and `dev` are empty because `load_sealed` reads neither — it needs the
    corpus, the arm and the round, which is precisely what the log row carries.
    """
    from src.eval.run_sealed_eval import ArmPlan

    return ArmPlan(
        corpus=corpus, detector=ARM.detector, supervision=ARM.supervision,
        porting=ARM.porting, iteration=iteration, rules={}, dev={},
    )


# ─── fixtures ───────────────────────────────────────────────────────────────


# `sealed_corpus` — present, and its test fold actually sealed — is in
# `tests/conftest.py` and defined nowhere else. Both halves answer from the path:
# nothing here opens the corpus, and nothing here opens the seal.


@pytest.fixture
def temp_log(tmp_path, monkeypatch):
    """A throwaway copy of the log, so no test writes the real one."""
    target = tmp_path / "sealed_eval_log.md"
    target.write_text(
        sealed_log.LOG.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setattr(sealed_log, "LOG", target)
    return target


# ─── physical separation ────────────────────────────────────────────────────


def test_the_sealed_fold_is_not_under_the_corpus_root(sealed_corpus):
    """The move happened. Checked by directory existence, nothing is opened."""
    root = base.corpus_root(sealed_corpus)
    for candidate in (root / "test", root / "meddocan" / "test"):
        assert not candidate.exists(), (
            "the test fold is still under the corpus root — the move did not "
            "happen, or was undone"
        )


def test_the_sealed_root_is_a_separate_config_key(sealed_corpus):
    """`corpus_root` must not resolve to the sealed root, and vice versa.

    The two paths come from different keys in data_paths.local.yaml. If one key
    served both, code iterating over corpus paths would reach the sealed fold as a
    matter of course (see the comment in data_paths.example.yaml).
    """
    assert base.sealed_root(sealed_corpus) != base.corpus_root(sealed_corpus)
    assert base.sealed_root(sealed_corpus) is not None


def test_an_unconfigured_corpus_is_not_sealed():
    """No `sealed:` entry means not-yet-sealed, which is a real state.

    It must not raise: conflating "not sealed" with "misconfigured" would push
    someone towards adding an entry that points at unsealed data.
    """
    assert base.sealed_root("de-grascco") is None


# ─── the code gate ──────────────────────────────────────────────────────────


def test_an_ordinary_load_returns_only_the_unsealed_folds(sealed_corpus):
    docs = MeddocanLoader().load()
    assert base.count_by_split(docs) == {"train": 500, "dev": 250}


def test_an_ordinary_load_refuses_the_log_rows_own_fields(sealed_corpus):
    """`purpose=`/`arm=`/`iteration=` without `sealed=True` is a refusal, not a no-op.

    They were ignored until 2026-08-28 and the docstring said so. What that permits is
    quiet in the direction that matters: the three are the log row's content, so a call
    site that passes an arm has named the arm it is opening — and on an unsealed load there
    is no row for the answer to go into, which makes the call read as an access that was
    recorded when nothing was.

    The signature keeps its defaults, because every ordinary load omits all three; the
    guarantee is the refusal rather than a required argument. `test_sealed_scoring.py`'s
    `test_no_step_in_the_chain_defaults_the_arm_or_the_round` holds the other half — that
    the steps *below* this one have no defaults at all.
    """
    loader = MeddocanLoader()
    for kwargs in (
        {"purpose": "why"}, {"arm": ARM}, {"iteration": ROUND},
        {"arm": ARM, "iteration": ROUND},
    ):
        with pytest.raises(SealError, match="without sealed=True") as raised:
            loader.load(**kwargs)
        for name in kwargs:
            assert name in str(raised.value), (
                "the refusal names which fields were passed, so the fix is visible from "
                "the message rather than by re-reading the call"
            )


def test_fold_roots_does_not_offer_the_sealed_fold(sealed_corpus):
    """The reachability decision, checked at its source.

    `fold_roots()` is the only place that answers "which folds can be read". A
    sealed fold missing from it is not filtered out later — its directory is never
    looked at, so there is no downstream step that could forget.
    """
    loader = MeddocanLoader()
    assert "test" not in loader.fold_roots()
    assert set(loader.fold_roots()) == {"train", "dev"}


def test_sealed_true_from_a_test_is_refused(sealed_corpus, temp_log):
    """The gate rejects this module, which is the whole point of it.

    `temp_log` is in place so that a *failure* of this test — the gate letting the
    read through — cannot also corrupt the real log on its way out.
    """
    before = temp_log.read_text(encoding="utf-8")
    with pytest.raises(SealError, match="may only be read from"):
        MeddocanLoader().load(sealed=True, purpose="a test trying to get in")
    assert temp_log.read_text(encoding="utf-8") == before, (
        "a refused access must not be logged: the log counts evaluations, and a "
        "row for a read that never happened inflates the number the paper reports"
    )


def test_a_seal_error_is_a_corpus_error():
    """So an existing `except CorpusError` still stops.

    And distinct, so a handler that means to swallow a corpus problem has to name
    SealError explicitly to swallow a seal breach too.
    """
    assert issubclass(SealError, CorpusError)
    assert SealError is not CorpusError


def test_the_gate_checks_module_identity_not_a_name(sealed_corpus, temp_log):
    """Naming a function or a variable after the allowed caller must not work.

    The gate walks the frames' `__name__`, so satisfying it requires the real module
    to be on the stack. This test does what a bypass attempt would do — defines a
    local whose name matches — and requires it to fail.
    """
    run_sealed_eval = MeddocanLoader()  # noqa: F841  — the suggestive name is the point

    def src_eval_run_sealed_eval():
        return MeddocanLoader().load(sealed=True, purpose="via a suggestive name")

    with pytest.raises(SealError, match="may only be read from"):
        src_eval_run_sealed_eval()


# ─── the authorised path, against a synthetic corpus ────────────────────────
# Exercised without touching MEDDOCAN: a real sealed read would open the test fold
# and consume a log row, and the row count is a reported number.
#
# The substitution is deliberately placed at the *data* and never at the frame. The
# gate's decision is about which module is on the stack, so these tests go through
# `run_sealed_eval.load_sealed` for real — its frame is what satisfies the gate —
# and only `_loader_for` is replaced. Faking the frame instead (a local function
# named after the module, a patched attribute) would be testing a mock of the gate.


class SyntheticLoader(MeddocanLoader):
    """A loader with the real gate and no real corpus.

    Inherits `load()`, `_authorise_sealed()` and `fold_roots()` unchanged — those are
    what is under test — and replaces only the reading.
    """

    def __init__(self):
        self.corpus_id = CORPUS
        self.root = None
        self.use_split_file = False
        self._sealed_ok = False
        self.read_folds = None

    def _read(self):
        # Records what `fold_roots()` offered, which is the gate's actual effect,
        # and yields one span-free document per fold so `load()` completes.
        self.read_folds = sorted(self.fold_roots())
        for fold_dir, fold in self.fold_dirs.items():
            if fold_dir not in self.fold_roots():
                continue
            yield base.Document(
                doc_id=f"synthetic-{fold}",
                corpus_id=self.corpus_id,
                text="x" * 10,
                spans=[],
                split=fold,
            )


@pytest.fixture
def synthetic(monkeypatch, tmp_path, temp_log):
    """A synthetic loader wired in as `run_sealed_eval`'s loader, sealed root set."""
    from src.eval import run_sealed_eval

    loader = SyntheticLoader()
    monkeypatch.setattr(base, "sealed_root", lambda corpus_id: tmp_path / "sealed")
    monkeypatch.setattr(run_sealed_eval, "_loader_for", lambda corpus_id: loader)
    monkeypatch.setattr(
        run_sealed_eval, "_verify_frozen_split", lambda loader, corpus_id: None
    )
    monkeypatch.setattr(sealed_log, "tree_state", lambda: ("a" * 40, "clean"))
    return loader


def test_the_authorised_caller_passes_the_gate_and_is_logged(synthetic, temp_log):
    """Through `run_sealed_eval.load_sealed`, the gate opens — and logs first."""
    from src.eval import run_sealed_eval

    docs = run_sealed_eval.load_sealed(a_plan(), purpose="unit test of the gate")

    assert synthetic.read_folds == ["dev", "test", "train"], (
        "an authorised read must reach the sealed fold — that is what it is for"
    )
    assert {d.split for d in docs} == {"train", "dev", "test"}
    text = temp_log.read_text(encoding="utf-8")
    assert "unit test of the gate" in text
    assert sealed_log.PLACEHOLDER not in text, (
        "the first real row must replace the placeholder, not sit beside it"
    )
    assert sealed_log.count_runs(CORPUS) == 1, "one read, one row"


def test_the_flag_is_cleared_after_the_read(synthetic):
    """`_sealed_ok` must not survive the call that set it.

    Otherwise one authorised evaluation leaves the loader permanently able to reach
    the sealed fold, and the next ordinary `load()` on the same object is a silent
    seal breach.
    """
    from src.eval import run_sealed_eval

    run_sealed_eval.load_sealed(a_plan(), purpose="checking the flag is cleared")
    assert not synthetic._sealed_ok
    assert "test" not in synthetic.fold_roots()


def test_a_failed_append_leaves_the_fold_unreachable(synthetic, monkeypatch):
    """Fail-closed: if the log cannot be written, the read does not happen.

    This is the ordering that matters. Logging after the read would mean a crash
    mid-evaluation produced numbers with no row; logging before it, and refusing on
    failure, means the fold is unreachable rather than merely unrecorded.
    """
    from src.eval import run_sealed_eval

    def boom(*args, **kwargs):
        raise SealError("could not append (simulated)")

    monkeypatch.setattr(sealed_log, "record_access", boom)
    with pytest.raises(SealError):
        run_sealed_eval.load_sealed(a_plan(), purpose="append will fail")
    assert not synthetic._sealed_ok
    assert synthetic.read_folds is None, (
        "nothing may be read when the append failed — not even the unsealed folds, "
        "because the caller asked for a logged evaluation and did not get one"
    )


def test_a_read_only_log_refuses_the_run(synthetic, temp_log):
    """The realistic failure: the log exists and cannot be written."""
    from src.eval import run_sealed_eval

    temp_log.chmod(0o444)
    try:
        with pytest.raises(SealError):
            run_sealed_eval.load_sealed(a_plan(), purpose="log is read-only")
    finally:
        temp_log.chmod(0o644)
    assert synthetic.read_folds is None


def test_a_sealed_read_of_an_unsealed_corpus_is_refused(
    synthetic, monkeypatch, temp_log
):
    """`sealed=True` on a corpus with no sealed entry must not fall back.

    Falling back to the corpus root would mean a "sealed evaluation" that read
    unsealed data and logged itself as a test run — the one outcome worse than not
    running, because it is indistinguishable from a real one in the log.
    """
    from src.eval import run_sealed_eval

    monkeypatch.setattr(base, "sealed_root", lambda corpus_id: None)
    before = temp_log.read_text(encoding="utf-8")
    with pytest.raises(SealError, match="no `sealed:` entry"):
        run_sealed_eval.load_sealed(a_plan(), purpose="no sealed entry")
    assert temp_log.read_text(encoding="utf-8") == before
    assert synthetic.read_folds is None


# ─── the log ────────────────────────────────────────────────────────────────


def test_the_log_records_the_freeze_commit():
    """First line of the real log, read-only. The seal's reference point."""
    text = sealed_log.LOG.read_text(encoding="utf-8")
    assert "Split-freeze commit" in text
    assert "30d6188dbefd9ddc11176518160a2b653a831c89" in text


def test_a_run_with_no_stated_purpose_is_refused(temp_log, monkeypatch):
    """"unspecified" would satisfy the format and defeat the point."""
    monkeypatch.delenv(sealed_log.PURPOSE_ENV, raising=False)
    with pytest.raises(SealError, match="needs a stated purpose"):
        sealed_log.record_access(CORPUS, arm=ARM, iteration=ROUND)


def test_a_purpose_with_a_pipe_is_refused(temp_log):
    """It goes into a Markdown cell and would shift the columns silently."""
    with pytest.raises(SealError, match="single line"):
        sealed_log.record_access(CORPUS, arm=ARM, iteration=ROUND, purpose="a | b")


def test_rows_are_numbered_consecutively(temp_log):
    first = sealed_log.record_access(CORPUS, arm=ARM, iteration=ROUND, purpose="first run")
    second = sealed_log.record_access(CORPUS, arm=ARM, iteration=ROUND, purpose="second run")
    assert first.split("|")[1].strip() == "1"
    assert second.split("|")[1].strip() == "2"
    assert sealed_log.count_runs(CORPUS) == 2
    assert sealed_log.count_runs("de-grascco") == 0


def test_an_existing_row_is_never_overwritten(temp_log):
    sealed_log.record_access(CORPUS, arm=ARM, iteration=ROUND, purpose="the run that must survive")
    sealed_log.record_access(CORPUS, arm=ARM, iteration=ROUND, purpose="a later run")
    text = temp_log.read_text(encoding="utf-8")
    assert "the run that must survive" in text
    assert "a later run" in text


def test_a_missing_log_refuses_the_run(tmp_path, monkeypatch):
    """The log is committed. Its absence is not a reason to skip logging."""
    monkeypatch.setattr(sealed_log, "LOG", tmp_path / "nonexistent.md")
    with pytest.raises(SealError, match="does not exist"):
        sealed_log.record_access(CORPUS, arm=ARM, iteration=ROUND, purpose="log is missing")


def test_the_row_records_whether_the_tree_was_dirty(temp_log):
    row = sealed_log.record_access(CORPUS, arm=ARM, iteration=ROUND, purpose="tree state check")
    cells = [c.strip() for c in row.split("|")[1:-1]]
    assert cells[3] in {"clean", "dirty", "unknown"}
    assert len(cells[2]) == 40 or cells[2] == "unknown"  # commit hash


def test_the_log_row_contains_no_corpus_text(temp_log):
    """CLAUDE.md: no corpus text in logs. The row is ids, counts and a purpose."""
    row = sealed_log.record_access(CORPUS, arm=ARM, iteration=ROUND, purpose="offsets and types only")
    assert CORPUS in row
    # The purpose is caller-supplied prose; everything else is structural. Checked
    # by shape rather than by searching for surfaces, which a test would have to
    # contain in order to look for.
    cells = [c.strip() for c in row.split("|")[1:-1]]
    assert len(cells) == 9
    assert cells[0].isdigit()
    assert cells[4] == CORPUS
    assert cells[5] == "test"
    # The arm cell is three axis values and the round is a number. Both are structural
    # too — `Arm` built them from `naming.yaml` — so this stays a shape check.
    assert cells[6] == ARM.cell
    assert cells[7] == str(ROUND)


# ─── run_sealed_eval's own guards ───────────────────────────────────────────


def test_a_dirty_tree_is_refused_by_default(monkeypatch):
    """The commit hash would otherwise name code that never ran.

    Refused rather than warned: see DESIGN §6. `--allow-dirty` records tree=dirty
    instead, which is the honest version of the same run.
    """
    from src.eval import run_sealed_eval

    monkeypatch.setattr(sealed_log, "tree_state", lambda: ("abc123", "dirty"))
    with pytest.raises(SealError, match="working tree is dirty"):
        run_sealed_eval.load_sealed(a_plan(), purpose="on a dirty tree")


def test_allow_dirty_gets_past_the_tree_check(monkeypatch, temp_log):
    """The override exists, and reaches the next guard rather than the fold.

    Stopped here at the split verification, which is the guard immediately after —
    so this test shows `--allow-dirty` is not a bypass of anything else.
    """
    from src.eval import run_sealed_eval

    monkeypatch.setattr(sealed_log, "tree_state", lambda: ("abc123", "dirty"))
    calls = []
    monkeypatch.setattr(
        run_sealed_eval,
        "_verify_frozen_split",
        lambda loader, corpus_id: calls.append(corpus_id),
    )
    monkeypatch.setattr(
        run_sealed_eval,
        "_loader_for",
        lambda corpus_id: (_ for _ in ()).throw(CorpusError("stopped deliberately")),
    )
    with pytest.raises(CorpusError, match="stopped deliberately"):
        run_sealed_eval.load_sealed(
            a_plan(), purpose="dirty but explicit", allow_dirty=True
        )


def test_the_gate_names_the_only_allowed_caller():
    """`SEALED_CALLER` must name a module that exists and is importable.

    A gate whose allowed caller had been renamed would refuse everything, which
    looks like a working seal until someone needs to run a real evaluation and
    "fixes" it under time pressure.
    """
    import importlib

    assert base.SEALED_CALLER == "src.eval.run_sealed_eval"
    module = importlib.import_module(base.SEALED_CALLER)
    assert module.__name__ == base.SEALED_CALLER
    assert hasattr(module, "load_sealed")
