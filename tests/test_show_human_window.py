"""The hand-off script: `tools/show_human_window.py`.

It is the one place in this repository that puts corpus text on a screen, so its
guarantee is about where the text is *not* allowed to go. The window may not be
redirected into a file (`docs/prompts/rule_author.md` §6), and `--counts-only` must
carry no context and no offsets — that is the mode whose output is safe to paste into a
commit message or a conversation, and the distinction only holds if it is checked.

Run as a subprocess rather than by importing `main()`, because what is being tested is
the behaviour under a redirect, and a redirect is a property of the process's stdout
rather than of any argument.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "show_human_window.py"
CORPUS = "es-meddocan"


def run(*args):
    """The script with stdout captured — which is to say, not a terminal."""
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=ROOT)


# Availability is `corpus_present` from `tests/conftest.py`, requested as an argument by
# the tests that need the corpus. This file used to compute it itself, at import time, by
# calling `load(CORPUS)` inside `except CorpusError` — the same defect as the loader
# fixture's original form: any loader bug read as "corpus not on this machine", these
# tests skipped, and the suite stayed green. The mutation harness caught it here by
# noticing that four loader mutations changed how many tests *exist* — 399 against a
# baseline of 402. It is a fixture argument now rather than a module-level marker so that
# there is one answer to the question in the whole suite and it is computed in one place.
#
# The tests without the argument are deliberate: the redirect refusal must fire before
# the corpus is loaded, so it is checkable on a machine that has no corpus at all.


def test_the_window_refuses_to_be_redirected():
    """`> window.txt` is exactly the file §6 says must not exist, and an author who
    wanted to keep the window for reference would create it by accident.

    Does not request `corpus_present`: the refusal happens before the corpus is loaded,
    which is the order it has to happen in — a check that runs after the text is in
    memory is one exception away from having rendered it.
    """
    result = run("--corpus", CORPUS, "--iteration", "1")
    assert result.returncode == 2
    assert "refusing" in result.stderr
    assert "rule_author.md" in result.stderr
    assert result.stdout == ""


def test_the_refusal_names_the_mode_that_is_safe_to_capture():
    """A refusal that does not say what to do instead gets worked around."""
    assert "--counts-only" in run("--corpus", CORPUS, "--iteration", "1").stderr


def test_counts_only_may_be_captured(corpus_present):
    result = run("--corpus", CORPUS, "--iteration", "1", "--counts-only")
    assert result.returncode == 0, result.stderr
    assert "PROFESSION" in result.stdout


def test_counts_only_carries_no_offsets_and_no_context(corpus_present):
    """The mode that exists to be pasted anywhere. `summarise()` already guarantees
    this; the script is a second place it could be lost, since it formats its own
    output rather than dumping the dict.

    The three absences are preceded by a return code and a presence, because all three
    hold over an empty string: a script that died before printing would satisfy every one
    of them. `run()` does not check the return code, so nothing else here would notice.
    """
    result = run("--corpus", CORPUS, "--iteration", "1", "--counts-only")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "PROFESSION" in out, "the counts were not printed, so the absences prove nothing"
    assert "context" not in out.lower()
    assert "offsets" not in out.lower()
    # The seed and the counts are numbers; a (doc_id, offset) pair would show up as a
    # document identifier, and MEDDOCAN's are all of the form S....-.....
    assert "S0" not in out and "S1" not in out


def test_a_later_iteration_is_refused_rather_than_derived_from_the_loader(
        corpus_present):
    """Only iteration 1's pool can come from the loader alone. From iteration 2 the
    pool is what the scorer found, and a script that silently rebuilt it from gold
    would hand the author the same 5,254 errors every round.

    Asked in `--counts-only`, because under a captured stdout the redirect refusal
    fires first — which is the order the two checks belong in, and worth noting here
    since it means this refusal is unreachable from a non-terminal without the flag.
    """
    result = run("--corpus", CORPUS, "--iteration", "2", "--counts-only")
    assert result.returncode == 2
    assert "scorer" in result.stderr


def test_the_script_writes_no_log_line(tmp_path):
    """`human_minutes` is the author's to report, and a script that logged on every
    invocation would turn "how many times did the author look" into a count of
    terminal commands."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "append(" not in source
    assert "log_line(" not in source
