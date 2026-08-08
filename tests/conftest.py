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

#: The corpus these fixtures are about. Every one of them is MEDDOCAN-specific today
#: because it is the only corpus with a loader (`src/corpora/base._loaders`). When a
#: second one lands, the shape to add is another named fixture here — not a per-file
#: availability check, which is the thing this file exists to stop.
CORPUS = "es-meddocan"


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
def unsplit_loader(corpus_present: str):
    """The loader with `use_split_file=False`, for the tests that check the split file.

    Loading with the file and then checking the file against the result would be
    circular, so `test_split_file.py` needs a loader that has not read it. Same rule as
    `loader`: availability first, construction bare.
    """
    from src.corpora.meddocan import MeddocanLoader

    return MeddocanLoader(use_split_file=False)
