"""Fixtures shared by the whole suite — and the only place corpus availability is decided.

**Why this is one file rather than one fixture per test file.** The same defect shipped
four times: an availability check written as

    try:
        docs = load(CORPUS)          # or MeddocanLoader(), or .load()
    except Exception:
        pytest.skip("corpus not on this machine")

turns every *loader bug* into "this machine has no corpus". The tests that would have
caught the bug skip, and a skipped test is green. `tests/mutations/README.md` records all
four occurrences and the mechanism that produced them: a new test file copies its fixture
from the nearest similar file, so a defect in one propagates at the rate test files are
added. Three of the four arrived after the incident was written up, which is what settled
that a warning in a README is not a control.

So the rule is structural, and it comes in two halves:

  - **Availability is answered from the path.** `corpus_root()` resolves a directory and
    does nothing else; it cannot fail for any reason except the corpus being absent. That
    makes a skip mean exactly one thing.
  - **Construction is a separate fixture with no `try` in it.** `loader` and
    `unsplit_loader` request `corpus_present` first and then construct. If construction
    raises — a type in both the map and the excluded set, a missing brat directory, a
    parser bug — it raises, the tests fail, and the failure says what happened.

`tests/test_conftest.py` enforces both halves against this file's own syntax tree, and
forbids any other test file from defining a corpus fixture or calling `pytest.skip` from
inside a fixture at all. The mutation `conftest_availability_from_a_load` reverts this
file to the defective form and is caught by those checks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: The corpus most of these fixtures are about. Unqualified names (`corpus_present`,
#: `loader`) mean MEDDOCAN, because it is the corpus the suite was written around;
#: the second loader's fixtures are named for it below. That is the shape this file
#: asked for when there was one loader — another named fixture here, never a per-file
#: availability check, which is the thing this file exists to stop.
CORPUS = "es-meddocan"

#: The second corpus with a loader, since 2026-09-21 (`src/corpora/grascco.py`).
GRASCCO = "de-grascco"

#: The third, since 2026-09-21 (`src/corpora/endeid.py`). Its root is *derived* — built
#: from the shared PhysioNet release by `tools/prepare_endeid.py` — so on a machine that
#: has the release and has not run that tool, these fixtures skip. That is the same
#: "corpus not here" the other two mean, and it is still answered from the path alone.
ENDEID = "en-deid"

#: The fourth, since 2026-09-22 (`src/corpora/kosurro.py`). Derived like `en-deid`'s and
#: built by `tools/prepare_kosurro.py`, which needs *two* read-only directories — the shared
#: release and the producing project's Korean files — so "absent" covers one more case here
#: and is still answered from the one configured path.
KOSURRO = "ko-surro"

#: The arm whose dev record `terminated_arm_record` answers for: the only one in the tree
#: that has terminated with a reason and a round count, which is what DESIGN §6.4 requires
#: before a sealed opening. Kept beside the fixture rather than in the test file, because a
#: second copy of the coordinate is a second chance to point at a different arm.
TERMINATED_ARM = dict(detector="R", supervision="sup-free", porting="port-loop")


@pytest.fixture(scope="session")
def corpus_present() -> str:
    """Skip when this machine has no corpus checkout. Availability, and nothing else.

    Resolved from the path. `corpus_root()` reads `config/data_paths.local.yaml`,
    resolves a directory and returns it; the only `CorpusError` it can raise is "unknown
    corpus id", "not configured" or "not there". Nothing about parsing, offsets, type maps
    or folds is reachable from here, which is precisely the property that makes the skip
    trustworthy.

    Returns the corpus id so a test can name it without repeating the literal.
    """
    from src.corpora.base import CorpusError, corpus_root

    try:
        corpus_root(CORPUS)
    except CorpusError as exc:
        pytest.skip(f"{CORPUS} not on this machine: {exc}")
    return CORPUS


@pytest.fixture(scope="session")
def sealed_corpus(corpus_present: str) -> str:
    """Present *and* its test fold actually sealed (DESIGN §6).

    A second question, asked separately: a machine can have the corpus and not have run
    the seal, and the seal tests are about the seal rather than about the corpus. Nothing
    is opened — `sealed_root()` answers from the path, like `corpus_root()`.
    """
    from src.corpora import base

    if base.sealed_root(corpus_present) is None:
        pytest.skip(f"{corpus_present} is not sealed on this machine")
    return corpus_present


@pytest.fixture(scope="session")
def terminated_arm_path(corpus_present: str):
    """Where the terminated arm's dev `metrics.json` is, or skip (DESIGN §6.4).

    A third availability question, and it belongs here for the reason the two above do
    rather than for a corpus-specific one: `results/` is not committed, so a fresh checkout
    has the code and none of the arm records, while the sealed-scoring tests are about
    which arm and round may be opened. Same shape as `corpus_present` — `arm_metrics_path`
    composes a path and reads nothing, so `.exists()` is the whole question and the skip
    means one thing.
    """
    from src.eval.scorer import arm_metrics_path

    path = arm_metrics_path(corpus=corpus_present, **TERMINATED_ARM)
    if not path.exists():
        pytest.skip(f"no dev record for the terminated arm under results/{corpus_present}")
    return path


@pytest.fixture(scope="session")
def terminated_arm_record(terminated_arm_path) -> dict:
    """That record, parsed. Construction, so the split above applies unchanged.

    **No `try` around the `json.loads`.** A record that exists and does not parse is a
    defect in whatever wrote it and has to reach the test as an error; wrapping it would
    report a corrupt headline as an absent one, which is the four-times defect in a new
    costume.

    `tests/test_sealed_scoring.py` asserts that the arm named by `TERMINATED_ARM` is the arm
    it plans against, so the coordinate is not silently two coordinates.
    """
    import json

    return json.loads(terminated_arm_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def loader(corpus_present: str):
    """The MEDDOCAN loader, constructed.

    **No `try` here, deliberately.** Availability was settled by `corpus_present`; a
    failure at this point is a loader bug and must reach the test as an error. The
    original defect was exactly this construction wrapped in `except CorpusError:
    pytest.skip(...)`, which reported `_check_type_map`'s refusal as an absent corpus
    while 27 tests skipped and the suite stayed green.
    """
    from src.corpora.meddocan import MeddocanLoader

    return MeddocanLoader()


@pytest.fixture(scope="session")
def grascco_present() -> str:
    """Availability of the second corpus, asked the same way and asked separately.

    Not folded into `corpus_present` with a parameter: the two corpora are on this
    machine independently, and a shared availability fixture would skip the MEDDOCAN
    tests on a machine that has only GraSCCo. Same rule as above — `corpus_root()`
    resolves a path and does nothing else, so the skip means one thing.
    """
    from src.corpora.base import CorpusError, corpus_root

    try:
        corpus_root(GRASCCO)
    except CorpusError as exc:
        pytest.skip(f"{GRASCCO} not on this machine: {exc}")
    return GRASCCO


@pytest.fixture(scope="session")
def grascco_sealed(grascco_present: str) -> str:
    """Present *and* sealed, asked separately for `sealed_corpus`'s reason exactly.

    The second corpus reached this state on 2026-09-21. A machine can have GraSCCo and
    not have moved the test fold, and the seal tests are about the seal — so the skip
    has to distinguish the two, or a suite on an unsealed checkout would pass tests
    asserting that a seal holds.
    """
    from src.corpora import base

    if base.sealed_root(grascco_present) is None:
        pytest.skip(f"{grascco_present} is not sealed on this machine")
    return grascco_present


@pytest.fixture(scope="session")
def grascco_loader(grascco_present: str):
    """The GraSCCo loader, constructed. No `try`, for `loader`'s reason exactly."""
    from src.corpora.grascco import GrasccoLoader

    return GrasccoLoader()


@pytest.fixture(scope="session")
def grascco_unsplit_loader(grascco_present: str):
    """The GraSCCo loader with no split file, for the tests that check the split file."""
    from src.corpora.grascco import GrasccoLoader

    return GrasccoLoader(use_split_file=False)


@pytest.fixture(scope="session")
def endeid_present() -> str:
    """Availability of the third corpus, asked separately for `grascco_present`'s reason.

    One extra thing is true here and is deliberately *not* checked: this root is derived,
    so "absent" can mean either the release is not on this machine or `prepare_endeid.py`
    has not been run. Distinguishing those would mean resolving the release path too, and
    the whole value of this fixture is that it resolves one path and does nothing else.
    The message says which tool builds the root instead.
    """
    from src.corpora.base import CorpusError, corpus_root

    try:
        corpus_root(ENDEID)
    except CorpusError as exc:
        pytest.skip(
            f"{ENDEID} not on this machine: {exc} "
            "(the root is built by tools/prepare_endeid.py stage)"
        )
    return ENDEID


@pytest.fixture(scope="session")
def endeid_sealed(endeid_present: str) -> str:
    """Present *and* sealed, asked separately for `sealed_corpus`'s reason exactly."""
    from src.corpora import base

    if base.sealed_root(endeid_present) is None:
        pytest.skip(f"{endeid_present} is not sealed on this machine")
    return endeid_present


@pytest.fixture(scope="session")
def endeid_loader(endeid_present: str):
    """The en-deid loader, constructed. No `try`, for `loader`'s reason exactly."""
    from src.corpora.endeid import EndeidLoader

    return EndeidLoader()


@pytest.fixture(scope="session")
def endeid_unsplit_loader(endeid_present: str):
    """The en-deid loader with no split file, for the tests that check the split file."""
    from src.corpora.endeid import EndeidLoader

    return EndeidLoader(use_split_file=False)


@pytest.fixture(scope="session")
def kosurro_present() -> str:
    """Availability of the fourth corpus, asked separately for `grascco_present`'s reason."""
    from src.corpora.base import CorpusError, corpus_root

    try:
        corpus_root(KOSURRO)
    except CorpusError as exc:
        pytest.skip(
            f"{KOSURRO} not on this machine: {exc} "
            "(the root is built by tools/prepare_kosurro.py stage)"
        )
    return KOSURRO


#: There is deliberately no `kosurro_sealed` and no split-file-reading `kosurro_loader` yet.
#: Both would be fixtures nothing requests, which is the state
#: `test_every_shared_fixture_is_used_by_something` exists to refuse — a fixture added ahead of
#: its user is indistinguishable from one whose user was reverted. They arrive with the acts
#: they are about: `splits/ko-surro.json` being frozen (then `tests/test_split_file.py` wants
#: the loader that reads it) and the test fold being sealed (then `tests/test_seal.py` wants
#: the availability fixture). Until then the corpus is read through the unsplit loader below.


@pytest.fixture(scope="session")
def kosurro_unsplit_loader(kosurro_present: str):
    """The ko-surro loader with no split file, for the tests that check the split file."""
    from src.corpora.kosurro import KosurroLoader

    return KosurroLoader(use_split_file=False)


@pytest.fixture(scope="session")
def kosurro_docs(kosurro_present: str, kosurro_unsplit_loader):
    """The corpus, loaded once. 2,434 records is too many to re-read per test.

    Session-scoped and shared, which means no test may mutate what it is handed — the same
    rule the other corpora's document lists follow. Loaded through the unsplit loader so a
    test about the split file is not handed documents that read it.

    `kosurro_present` is requested directly as well as transitively, because the rule
    `test_conftest.py` enforces is about this function's own arguments: a construction
    fixture that reached availability only through another construction fixture would be one
    step from the form where the chain is quietly broken.
    """
    return kosurro_unsplit_loader.load()


@pytest.fixture(scope="session")
def unsplit_loader(corpus_present: str):
    """The loader with `use_split_file=False`, for the tests that check the split file.

    Loading with the file and then checking the file against the result would be
    circular, so `test_split_file.py` needs a loader that has not read it. Same rule as
    `loader`: availability first, construction bare.
    """
    from src.corpora.meddocan import MeddocanLoader

    return MeddocanLoader(use_split_file=False)


# ─── the suite does not delete an arm's record ───────────────────────────────
#
# Hooks rather than a fixture, so `test_conftest.py`'s availability/construction
# classification stays a statement about corpus fixtures — this is not one of those.
#
# **What happened.** `tests/test_run_fold.py` ran the `run_fold` CLI into the real results
# root (the CLI has no `--root`) under `porting=port-multi`, on the stated grounds that
# nothing else wrote that arm, and removed the directory in a `finally` with
# `ignore_errors=True`. The grounds expired on 2026-09-04, when `port-multi` ran and
# committed `window_freeze.json` and `format_failure.json` under exactly that path. From
# then until it was found, every `pytest` run deleted an arm's committed record and said
# nothing. That test now borrows an arm that has not run and asserts the directory is
# absent before it writes; this is the check that does not depend on the next test author
# knowing which arms exist.
#
# **Why a snapshot and not `git ls-files`.** The two files above are recoverable from git.
# `agent_calls.jsonl` is not — it is deny-listed and gitignored because §1.4 carries dev
# corpus text, so the copy in the arm's directory is the only copy there has ever been
# (`tests/test_call_role.py`'s header states this as the reason its assertions read the
# working tree). A guard that asked git would see the two recoverable files and miss the
# irreplaceable one. Comparing the listing to itself needs no git, and it also works in the
# copied trees `tests/mutations/run.py` builds, which have no `.git` at all.
#
# Deletion only. A test that *appends* to a record is a different failure and this is not
# the check for it; naming what a check covers is cheaper than a check nobody can bound.

#: Every file under `results/` at session start. Populated by the hook below, compared by
#: the one after it. A module global rather than a fixture because both ends of the
#: comparison are session events and neither belongs to a test.
_RESULTS_AT_START: set[str] = set()


def _results_files() -> set[str]:
    """Paths under `results/`, relative to the repository root. Absent directory → empty."""
    root = ROOT / "results"
    if not root.is_dir():
        return set()
    return {str(path.relative_to(ROOT)) for path in root.rglob("*") if path.is_file()}


def pytest_sessionstart(session):
    _RESULTS_AT_START.clear()
    _RESULTS_AT_START.update(_results_files())


def pytest_sessionfinish(session, exitstatus):
    missing = sorted(_RESULTS_AT_START - _results_files())
    if not missing:
        return
    session.exitstatus = 1
    print(
        "\nTHE SUITE DELETED FILES UNDER results/ THAT EXISTED BEFORE IT RAN:\n"
        + "".join(f"  {path}\n" for path in missing)
        + "These are arm records. Some are committed and recoverable from git; "
        "agent_calls.jsonl is neither, and the copy in the arm's directory is the only "
        "one there has ever been. A test that needs a results directory writes into an "
        "arm that has not run, asserts it is absent first, and removes only what it "
        "created — see test_the_cli_reports_the_leak_rate_and_not_f1."
    )
