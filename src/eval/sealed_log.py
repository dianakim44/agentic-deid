"""Appending to `results/sealed_eval_log.md`.

Separated from `run_sealed_eval.py` so that the append can be tested without
running an evaluation, and so that the loader's gate imports the logging without
importing the evaluation driver.

The contract, from CLAUDE.md: every read of a sealed fold is recorded with date,
commit hash and purpose, so the paper can report how many times the test set was
evaluated. Two consequences the code has to honour rather than intend:

  - **The append happens before the read.** A log written afterwards is a log that
    is missing precisely when something crashed mid-evaluation.
  - **A failed append aborts the read.** An unlogged evaluation is worse than none,
    because it leaves the remaining rows looking like a complete account.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..corpora.base import ROOT, SealError, axis

LOG = ROOT / "results" / "sealed_eval_log.md"

#: The row marker for "nothing has run yet". Removed by the first real append, so
#: the table never shows a placeholder next to actual runs.
PLACEHOLDER = "_no sealed evaluation has been run_"

#: How `purpose` is supplied. An environment variable rather than a default,
#: because "unspecified" is not an acceptable value in a log whose whole purpose
#: is to say why the test fold was looked at.
PURPOSE_ENV = "SEALED_EVAL_PURPOSE"


@dataclass(frozen=True)
class Arm:
    """Which arm an opening is for, as three `naming.yaml` axis values.

    The corpus is already its own column, so these three finish the four-axis
    coordinate — the same tuple `scorer.PATH_AXES` formats into a results path, minus
    the one that is written beside it.

    **It is a validated object rather than a string, and that is the point.** Until
    2026-08-26 the log's arm cell was whatever the caller passed, and what the caller
    passed was the literal `"none (access check)"` — true at the time, and a string
    that cannot be distinguished from a real evaluation's cell by anything except
    reading it. A cell built here is either three axis values from `config/naming.yaml`
    or an exception, so an opening cannot be recorded against an arm that does not
    exist, and a hardcoded stand-in fails at the append rather than being published.

    There is no `corpus` field: `record_access` takes it separately because
    `count_runs` keys on that column, and a corpus reachable from two places is a
    corpus that can disagree with itself.
    """

    detector: str
    supervision: str
    porting: str

    def __post_init__(self) -> None:
        for name in ("detector", "supervision", "porting"):
            value = getattr(self, name)
            if value not in axis(name):
                raise SealError(
                    f"{value!r} is not a value of the {name!r} axis in "
                    f"config/naming.yaml (have: {sorted(axis(name))}). The log's arm "
                    "cell names a cell of the results tree, and a name nothing "
                    "defines names no cell — CLAUDE.md's naming rule applies here for "
                    "the reason it applies to paths, and more sharply: this row is "
                    "appended once and never corrected."
                )

    @property
    def cell(self) -> str:
        """The arm as it appears in the table."""
        return f"{self.detector}/{self.supervision}/{self.porting}"


def _shown(path: Path) -> str:
    """A path for a message: repository-relative when it is inside the repository.

    `Path.relative_to` raises for anything outside, and a crash while building an
    error message replaces a diagnosis with a traceback about the diagnosis. The log
    lives in the repository, so the fallback is for tests that redirect it.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def tree_state() -> tuple[str | None, str]:
    """(commit, "clean"|"dirty"|"unknown").

    A dirty tree means the commit hash does not describe the code that ran. That
    is recorded rather than corrected: see `run_sealed_eval.py` for why the run is
    refused by default instead.
    """
    commit = _git("rev-parse", "HEAD")
    porcelain = _git("status", "--porcelain")
    if commit is None or porcelain is None:
        return commit, "unknown"
    return commit, "dirty" if porcelain else "clean"


def record_access(
    corpus_id: str,
    *,
    arm: Arm,
    iteration: int,
    fold: str = "test",
    purpose: str | None = None,
) -> str:
    """Append one row and return it. Raises SealError if it cannot.

    Called from the loader's seal gate before the fold is opened, so raising here
    means the fold is never read.

    `arm` and `iteration` are **required**, and there is no default for either
    (2026-08-26, DESIGN §6.4). A default is the state this row spent its whole
    existence in — the arm cell read `none (access check)` because it was a constant in
    `run_sealed_eval`, and the row that recorded the seal machinery working was
    indistinguishable in shape from the row that would record the experiment's only
    test score. §6.4 makes the arm and the round the two facts that decide whether an
    opening was legitimate, so a row that omits them records the count without
    recording what the count is a count of, and the count is the number the paper
    reports.

    `iteration` is the round scored. §6.4 requires it to be the arm's *final* round,
    which cannot be checked here — finality is a property of that arm's committed
    termination record, and this module reads the log and nothing else. It is verified
    in `run_sealed_eval.plan_arm`, before this is called. What is checked here is that
    it is a real round: a positive integer, because rounds are 1-indexed and a 0 would
    be a round nobody ran.
    """
    if not isinstance(arm, Arm):
        raise SealError(
            f"record_access got arm={arm!r} ({type(arm).__name__}). It takes an `Arm`, "
            "whose three fields are checked against config/naming.yaml. A string would "
            "be published verbatim — which is how the arm cell came to read "
            "'none (access check)' for every row this log could have held."
        )
    if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 1:
        raise SealError(
            f"record_access got iteration={iteration!r}. The round scored is a "
            "positive integer — rounds are 1-indexed, so 0 is a round nobody ran, and "
            "an arm that does not iterate still has a round 1. DESIGN §6.4 requires it "
            "to be the arm's final round; that is checked in run_sealed_eval.plan_arm "
            "against the arm's committed termination record, before this is reached."
        )
    if purpose is None:
        purpose = os.environ.get(PURPOSE_ENV, "").strip()
    if not purpose:
        raise SealError(
            "a sealed evaluation needs a stated purpose. Set "
            f"{PURPOSE_ENV} or pass purpose=. The log exists so that a reader can "
            "see why the test fold was opened; a row saying 'unspecified' would "
            "satisfy the format and defeat the point."
        )
    if "|" in purpose or "\n" in purpose:
        raise SealError(
            "the purpose must be a single line without '|' — it goes into a "
            "Markdown table cell and would otherwise silently shift the columns"
        )

    if not LOG.exists():
        raise SealError(
            f"{_shown(LOG)} does not exist. It is committed and it holds "
            "the split-freeze commit; a sealed evaluation does not run without it, "
            "because the run could not then be counted."
        )

    commit, tree = tree_state()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    text = LOG.read_text(encoding="utf-8")
    number = _next_row_number(text)
    row = (
        f"| {number} | {timestamp} | {commit or 'unknown'} | {tree} | "
        f"{corpus_id} | {fold} | {arm.cell} | {iteration} | {purpose} |"
    )

    if PLACEHOLDER in text:
        placeholder_row = next(
            line for line in text.splitlines() if PLACEHOLDER in line
        )
        updated = text.replace(placeholder_row, row, 1)
    else:
        updated = _insert_after_last_row(text, row)

    try:
        # Written whole rather than opened in append mode: the placeholder row has
        # to be replaced on the first run, and a partial write to this file would
        # be worse than a failed one.
        LOG.write_text(updated, encoding="utf-8")
        reread = LOG.read_text(encoding="utf-8")
    except OSError as exc:
        raise SealError(
            f"could not append to {_shown(LOG)}: {exc}. The sealed fold "
            "is not read. An evaluation that is not logged is worse than one that "
            "did not happen."
        ) from exc
    if row not in reread:
        raise SealError(
            f"the row was not present after writing {_shown(LOG)}. The "
            "sealed fold is not read."
        )
    return row


def _next_row_number(text: str) -> int:
    """One more than the highest row number present, or 1."""
    numbers = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        first = stripped.split("|")[1].strip()
        if first.isdigit():
            numbers.append(int(first))
    return max(numbers, default=0) + 1


def _insert_after_last_row(text: str, row: str) -> str:
    lines = text.splitlines()
    last = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.split("|")[1].strip().isdigit():
            last = i
    if last is None:
        raise SealError(
            f"found no run rows in {_shown(LOG)} to append after. Do not "
            "repair this by hand without understanding why — the row count is a "
            "reported number."
        )
    lines.insert(last + 1, row)
    return "\n".join(lines) + "\n"


def count_runs(corpus_id: str | None = None) -> int:
    """How many sealed evaluations the log records. The paper's N."""
    if not LOG.exists():
        return 0
    total = 0
    for line in LOG.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.split("|")[1:-1]]
        if not cells or not cells[0].isdigit():
            continue
        # Cell 4 is the corpus. The `round` column was added at cell 7 (2026-08-26)
        # rather than anywhere left of here, so that this index — and every count
        # already reported off it — did not move.
        if corpus_id is None or (len(cells) > 4 and cells[4] == corpus_id):
            total += 1
    return total
