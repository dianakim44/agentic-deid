"""Tests for tools/check_rules.py — the rule author's feedback path.

What this tool is for: write one rule, run it, see how many of the window's spans it now
covers and how many false positives it bought. Without it the loop has no closing step
and iteration 1 cannot end (the author would be guessing).

What has to hold, and why each is a test rather than a convention:

**The fold is not selectable.** Every run is dev. A `--split` argument on a tool an
author invokes forty times an evening is a sealing violation with a countdown on it
(CLAUDE.md), so the absence of the flag is asserted, not just observed.

**No surface forms in the output.** Not in the table, not under `--verbose`, not in an
error path. A false positive is reported as a document id and a character range.

**Counts, not metrics.** P/R/F1 come from the scorer over a merged prediction set
(DESIGN §9.3, CLAUDE.md). A precision figure printed here would disagree with
metrics.json for a reason nobody would find, so it is not printed at all.

The tests run the tool as a subprocess, because what is being checked is what reaches a
terminal — an in-process call to `main()` would test the function and not the output.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOOL = ROOT / "tools" / "check_rules.py"
CORPUS = "es-meddocan"


def run(*args, expect: int | None = 0) -> subprocess.CompletedProcess:
    done = subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, cwd=ROOT)
    if expect is not None:
        assert done.returncode == expect, done.stderr
    return done


def rule_file(tmp_path: Path, body: str, name: str = "es.yaml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


CUE_RULE = """
version: 1
lang: es
rules:
  - rule_id: probe_cue
    layer: context_cue
    phi_type: NAME
    cue: ["Dr.", "Dra."]
    then: capitalised_words
"""

BROAD_RULE = """
version: 1
lang: es
rules:
  - rule_id: probe_broad
    layer: gazetteer
    phi_type: ORGANISATION
    terms: ["de", "la"]
"""


# `corpus_present` comes from `tests/conftest.py`. This file defined its own once, and
# defined it wrong: `except Exception` around a `load()` turns every loader bug into "the
# corpus is not on this machine" and every test below into a skip. That is the fourth
# occurrence recorded in `tests/mutations/README.md`, and it got here by being copied.


# ─── the loop closes: a rule produces numbers ────────────────────────────────

def test_a_rule_reports_what_it_caught_and_what_it_cost(tmp_path, corpus_present):
    out = run("--corpus", CORPUS, "--rules", str(rule_file(tmp_path, CUE_RULE))).stdout
    assert "es:probe_cue" in out
    assert "context_cue" in out
    assert "false positives" in out
    assert "window" in out


def test_the_two_numbers_an_author_needs_are_both_present(tmp_path, corpus_present):
    """"몇 개를 잡았고 오탐이 몇 개인지" — the count out of the window, and the FPs."""
    out = run("--corpus", CORPUS, "--rules", str(rule_file(tmp_path, CUE_RULE))).stdout
    import re
    assert re.search(r"\d+/\d+ covered", out), out
    assert re.search(r"false positives \d+", out), out


def test_a_broad_rule_shows_more_false_positives_than_a_narrow_one(tmp_path,
                                                                  corpus_present):
    """The loop has to *discriminate*, or it is a loop that always says the same thing."""
    import re

    def fps(body, name):
        out = run("--corpus", CORPUS, "--rules",
                  str(rule_file(tmp_path, body, name))).stdout
        return int(re.search(r"false positives (\d+)", out).group(1))

    assert fps(BROAD_RULE, "broad.yaml") > fps(CUE_RULE, "cue.yaml")


def test_an_empty_rule_file_says_so_and_succeeds(tmp_path, corpus_present):
    """Iteration 1 starts here, and the correct response is not an error."""
    done = run("--corpus", CORPUS, "--rules",
               str(rule_file(tmp_path, "version: 1\nlang: es\nrules: []\n")))
    assert "no rules loaded" in done.stdout
    assert "iteration 1" in done.stdout


def test_the_window_count_and_the_dev_wide_count_are_reported_separately(
        tmp_path, corpus_present):
    """A rule's effect on spans the author never saw is real but is not feedback.

    One number for both would let a rule that generalises broadly conceal one that does
    not generalise at all, which is the comparison DESIGN §11.1 is set up to make.
    """
    out = run("--corpus", CORPUS, "--rules", str(rule_file(tmp_path, CUE_RULE))).stdout
    assert "dev-wide" in out
    assert "the window did not show" in out


def test_a_single_rule_can_be_isolated(tmp_path, corpus_present):
    body = CUE_RULE + """
  - rule_id: probe_other
    layer: gazetteer
    phi_type: ORGANISATION
    terms: ["ZZZNEVERMATCHZZZ"]
"""
    out = run("--corpus", CORPUS, "--rules", str(rule_file(tmp_path, body)),
              "--rule-id", "es:probe_cue").stdout
    assert "es:probe_cue" in out
    assert "es:probe_other" not in out


def test_an_unknown_rule_id_is_refused(tmp_path, corpus_present):
    done = run("--corpus", CORPUS, "--rules", str(rule_file(tmp_path, CUE_RULE)),
               "--rule-id", "es:absent", expect=2)
    assert "no rule with id" in done.stderr


def test_a_broken_rule_file_is_reported_and_not_run(tmp_path, corpus_present):
    body = """
version: 1
lang: es
rules:
  - rule_id: bad
    layer: regex_checksum
    phi_type: ID
    pattern: 'REF-(\\d{4}'
"""
    done = run("--corpus", CORPUS, "--rules", str(rule_file(tmp_path, body)), expect=2)
    assert "does not compile" in done.stderr


# ─── the fold is not selectable ──────────────────────────────────────────────

def test_there_is_no_split_flag(corpus_present):
    """Asserted on the parser's own option list, because an absent option is the guarantee.

    A tool run forty times in an evening with a `--split` argument is one tired keystroke
    from evaluating the sealed fold (CLAUDE.md). The dev restriction is hardcoded and this
    test is what keeps it that way.

    Checked against `--help` rather than against the source text: the source *mentions*
    `--split` in prose explaining why it does not exist, and a grep for the string cannot
    tell an explanation from an implementation.

    The second assertion is on the call, and it matters more now that detection is
    shared with `src/eval/run_fold.py`. That module takes the fold as a parameter,
    because the orchestrator needs to name one; this tool must pass the literal. A tool
    that forwarded an argument into that parameter would have re-introduced the flag
    through the shared path with nothing appearing in `--help`.
    """
    for flag in ("--split", "--fold", "--test", "--sealed"):
        done = run("--corpus", CORPUS, flag, "dev", expect=2)
        assert "unrecognized arguments" in done.stderr or "invalid" in done.stderr
    src = TOOL.read_text(encoding="utf-8")
    assert 'load_fold(args.corpus, "dev")' in src
    assert "load_fold(args.corpus, args" not in src


def test_the_help_text_says_dev_only(corpus_present):
    out = run("--corpus", CORPUS, "--help").stdout
    assert "dev" in out.lower()


def test_no_sealed_path_is_constructed(tmp_path, corpus_present):
    src = TOOL.read_text(encoding="utf-8")
    # `sealed` appears only in prose about not touching it, never as a path join.
    assert "sealed_root" not in src
    out = run("--corpus", CORPUS, "--rules", str(rule_file(tmp_path, CUE_RULE)))
    # `run()` pins the return code; the presence check pins the surface. Without it the
    # absence below holds over any output at all, including none.
    assert "es:probe_cue" in out.stdout, "the tool printed no rule line, so nothing was read"
    assert "sealed" not in out.stdout


# ─── no surface forms anywhere in the output ──────────────────────────────────

def test_verbose_false_positives_are_offsets_and_not_text(tmp_path, corpus_present):
    """The one place surface forms would be most useful, and therefore most tempting."""
    out = run("--corpus", CORPUS, "--rules", str(rule_file(tmp_path, BROAD_RULE)),
              "--verbose").stdout
    import re
    lines = [l for l in out.splitlines() if l.startswith("  ") and "[" in l]
    assert lines, "no false positives listed to check"
    for line in lines:
        # doc_id, a half-open range, a length. Nothing else may be on the line: the
        # whole line is matched, so a stray slice of text could not hide at the end.
        assert re.fullmatch(r"  \S+  \[\d+, \d+\)  len \d+", line), line


def test_the_matched_text_is_never_printed(tmp_path, corpus_present):
    """A rule whose term is a distinctive invented string: it must not come back out.

    The term is in the *rule file*, which is committed and public — but the tool must not
    echo what it matched *in the corpus*, and a term that appears in both would make the
    two indistinguishable. So the assertion is on the tool's own source: no slice of
    document text reaches a print.
    """
    src = TOOL.read_text(encoding="utf-8")
    assert "doc.text[" not in src
    assert "d.text[" not in src


def test_the_output_holds_no_corpus_text_beyond_ids_and_numbers(tmp_path,
                                                               corpus_present):
    """A structural check: every non-comment output line is ids, labels and integers."""
    import re
    out = run("--corpus", CORPUS, "--rules", str(rule_file(tmp_path, BROAD_RULE)),
              "--verbose").stdout
    for line in out.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        # No character outside the set that ids, labels, offsets and counts need.
        assert re.fullmatch(r"[\w\s:/,.()+\[\]<>—'\-]*", line), line


# ─── metrics stay with the scorer ────────────────────────────────────────────

def test_no_precision_or_f1_is_printed(tmp_path, corpus_present):
    """CLAUDE.md: P/R/F1 come from a 1:1 assignment over a merged prediction set.

    A ratio computed here over one unmerged rule file would be a second number with the
    same name as the real one and a different value.

    The `es:probe_cue` pin is the presence control. Three absences over `data` are all
    satisfied by an empty string, and the filter below narrows the surface further, so a run
    that printed nothing — or printed only `#` lines — would pass this test having measured
    nothing at all.
    """
    out = run("--corpus", CORPUS, "--rules", str(rule_file(tmp_path, CUE_RULE))).stdout
    # The `#` lines are the tool saying where the metrics *do* come from, which is the
    # opposite of printing one; the assertion is about the data lines.
    data = "\n".join(l for l in out.splitlines() if not l.startswith("#")).lower()
    assert "es:probe_cue" in data, "no data line was printed, so the absences prove nothing"
    for name in ("f1", "precision", "recall"):
        assert name not in data, f"{name} printed on a data line"


def test_coverage_is_reported_under_both_definitions(tmp_path, corpus_present):
    """fully_covered as the headline, relaxed as the lower bound — as the metrics do."""
    out = run("--corpus", CORPUS, "--rules", str(rule_file(tmp_path, CUE_RULE))).stdout
    assert "relaxed lower bound" in out
    assert "fully_covered" in out


def test_the_rule_file_version_is_reported(tmp_path, corpus_present):
    """Recorded with results (rule_author.md §2); shown here so it can be recorded."""
    out = run("--corpus", CORPUS, "--rules", str(rule_file(tmp_path, CUE_RULE))).stdout
    assert "version" in out and "'es': 1" in out


# ─── the practice band ───────────────────────────────────────────────────────

def test_practice_requires_a_banded_iteration(tmp_path, corpus_present):
    done = run("--corpus", CORPUS, "--rules", str(rule_file(tmp_path, CUE_RULE)),
               "--practice", "--iteration", "1", expect=2)
    assert "reserved band" in done.stderr


def test_a_practice_run_scores_against_a_practice_window(tmp_path, corpus_present):
    from src.sample import practice_min
    out = run("--corpus", CORPUS, "--rules", str(rule_file(tmp_path, CUE_RULE)),
              "--practice", "--iteration", str(practice_min())).stdout
    assert "practice window" in out


def test_this_tool_writes_nothing(tmp_path, corpus_present):
    """It reads the corpus and prints. No log line, no results file, no cache.

    `human_minutes` is the author's to report and the read_sample event was logged when
    the window froze (DESIGN §11.2); a tool that logged per invocation would turn "how
    many times did the author look" into a count of shell commands.
    """
    src = TOOL.read_text(encoding="utf-8")
    for forbidden in ("open(", "write_text", "append(", "write_metrics", "json.dump"):
        if forbidden == "append(":
            continue        # list.append is used for accumulating counts
        assert forbidden not in src, f"{forbidden} in a tool that should only print"
