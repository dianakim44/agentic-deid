"""Regression tests for tools/check_manuscript_numbers.py.

The checker reads a file this repository does not own and must not keep: an
unsubmitted manuscript, which may quote the corpora it reports on. Two of its
rules are therefore conventions rather than conveniences, and conventions that
are not pinned by a test come back as "it was easier to debug this way":

- a manuscript path inside this public repository is refused, because a draft
  committed here is a draft published (CLAUDE.md);
- a cell the parser cannot read is reported by its length and never by its
  content, for the same reason exception messages carry offsets and not
  surface forms (CLAUDE.md).

The third thing tested here is the rounding, which is the checker's one real
piece of arithmetic: the draft rounds and the files do not, so a disagreement
has to be the draft's and not the tool's convention.

The fourth is Table 4's source. The Auditor's reports are a **denied** path and
are not in this repository, so the checker reads them where they were produced
and withholds their values: the tests below hold it to both halves of that —
nothing from a report reaches the printed line, and nothing is opened for
writing anywhere.

    python3 -m pytest tests/ -q
"""
import builtins
import os
import sys
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import check_manuscript_numbers as cmn  # noqa: E402

# A cell with a plausible surface form in it. Invented, not from any corpus —
# the point is only that the checker must not echo whatever this says.
OPAQUE_CELL = "0.52 (Dr. Mustermann, Musterstadt)"


# ─── the draft stays outside the repository ─────────────────────────────────

def test_a_manuscript_path_inside_the_repository_is_refused():
    inside = os.path.join(ROOT, "docs", "draft.md")
    with pytest.raises(SystemExit) as caught:
        cmn.manuscript_path(inside)
    assert "public" in str(caught.value)


def test_a_manuscript_path_outside_the_repository_is_accepted(tmp_path):
    draft = tmp_path / "draft.md"
    draft.write_text("## Figure captions\n")
    assert cmn.manuscript_path(str(draft)) == draft.resolve()


def test_a_missing_draft_is_refused_by_name_and_not_by_traceback(tmp_path):
    with pytest.raises(SystemExit) as caught:
        cmn.manuscript_path(str(tmp_path / "absent.md"))
    assert "no such file" in str(caught.value)


# ─── no manuscript prose reaches the output ─────────────────────────────────

def test_an_unreadable_cell_is_reported_by_length_only():
    rep = cmn.Report()
    rep.opaque("Table 6", "rate and n", OPAQUE_CELL)
    line = rep.unchecked[0]
    assert f"{len(OPAQUE_CELL)} chars" in line
    for fragment in OPAQUE_CELL.split():
        assert fragment not in line


def test_a_row_label_that_names_no_corpus_is_reported_by_length_only():
    """The row label is as untrusted as the cell: it comes out of the draft."""
    rep = cmn.Report()
    s = cmn.subject()
    text = (
        "**Table 5.** t\n\n"
        "| Corpus | Calls | Reference | Dev gold | Leak `fully_covered` | "
        "Leak `relaxed` | F1 (`relaxed`) |\n"
        "|---|---|---|---|---|---|---|\n"
        f"| {OPAQUE_CELL} | 1 | human | 410 | 0.4146 | 0.3683 | 0.590 |\n"
    )
    cmn.check_table_five(text, s, rep)
    reported = "\n".join(rep.unchecked)
    assert f"{len(OPAQUE_CELL)} chars" in reported
    for fragment in OPAQUE_CELL.split():
        assert fragment not in reported


def test_every_reported_line_is_built_from_labels_the_repository_owns():
    """An end-to-end run over the real records prints no draft text.

    The draft here states one wrong number and one wrong marker, both in cells
    whose neighbours carry invented surface forms.
    """
    s = cmn.subject()
    draft = (
        "**Table 7.** t\n\n"
        "| Corpus | Context cues | Gazetteer | Pattern rules |\n"
        "|---|---|---|---|\n"
        "| de | 999999 | 0 | 173 |\n"
        f"| {OPAQUE_CELL} | 1 | 2 | 3 |\n"
    )
    rep = cmn.Report()
    cmn.check_table_seven(draft, s, rep)
    assert rep.mismatches, "the wrong layer count must be reported"
    for line in rep.mismatches + rep.unchecked:
        for fragment in OPAQUE_CELL.split():
            assert fragment not in line


# ─── the rounding is the draft's convention, not the tool's ─────────────────

def test_an_exact_half_rounds_the_way_a_typeset_table_rounds():
    """`round()` would send 0.3125 to 0.312 and report the draft's 0.313."""
    assert cmn.half_up(0.3125, 3) == pytest.approx(0.313)
    assert round(0.3125, 3) == pytest.approx(0.312)


def test_a_cell_is_compared_at_its_own_precision():
    rep = cmn.Report()
    rep.number("where", "what", "0.154", 0.15355086372360843)
    rep.number("where", "what", "0.1536", 0.15355086372360843)
    assert rep.mismatches == []
    assert rep.checks == 2


def test_double_rounding_is_a_mismatch_and_not_a_tolerance():
    """0.051470… is 0.0515 at four places and 0.051 at three, never 0.052."""
    rep = cmn.Report()
    rep.number("Table 6", "leak rate", "0.052", 0.051470588235294115)
    assert len(rep.mismatches) == 1
    assert "draft 0.052" in rep.mismatches[0]
    assert "files 0.051" in rep.mismatches[0]


def test_thousands_separators_and_minus_signs_parse():
    assert cmn.parse_number("3,132") == (3132.0, None)
    assert cmn.parse_number(cmn.normalise("−0.0384")) == (-0.0384, 4)
    assert cmn.parse_number("—") is None


# ─── silence means agreement, not an unread table ───────────────────────────

def test_a_table_the_parser_cannot_find_is_unchecked_and_not_silent():
    rep = cmn.Report()
    cmn.check_table_one("no tables here", cmn.subject(), rep)
    assert rep.checks == 0
    assert any("table not found" in line for line in rep.unchecked)


def test_a_reworded_caption_is_unchecked_and_not_silent():
    rep = cmn.Report()
    draft = (
        "## Figure captions\n\n"
        "**Fig. 1.** Leak rate of the iterative arm, round by round.\n"
    )
    cmn.check_caption_one(draft, cmn.subject(), rep)
    assert rep.unchecked, "a caption with no numbers left in it must be reported"
    assert any("phrase that states it" in line for line in rep.unchecked)


def test_the_layer_columns_name_declared_layers():
    """The one hand-written mapping in the checker is held to naming.yaml."""
    cmn.check_layer_columns()
    declared = set(cmn.naming()["axes"]["layer"])
    assert set(cmn.LAYER_COLUMN.values()) <= declared


# ─── Table 4 comes from a denied path, and stays there ──────────────────────

def first_report(s):
    """A round's audit report, or a skip: a clone of the repo has none."""
    for index in range(1, len(s.rounds) + 1):
        report = cmn.audit_report(s, index)
        if report is not None:
            return index, report
    pytest.skip("no audit report on this filesystem; the path is denied")


def test_the_audit_columns_name_fields_the_report_has():
    """The one hand-written mapping for Table 4, against a real report.

    Presence only: the values are what the checker refuses to publish, and a
    test that asserted them would publish them in its own source.
    """
    _, report = first_report(cmn.subject())
    for keys in cmn.AUDIT_COLUMN.values():
        assert cmn.dig(report, keys) is not None, f"no {'.'.join(keys)} in the report"


def test_a_withheld_mismatch_states_the_disagreement_and_not_the_value():
    rep = cmn.Report()
    rep.number("Table 4 · round 3", "counts.refused", "999", 351, withhold=True)
    assert len(rep.mismatches) == 1
    line = rep.mismatches[0]
    assert "withheld" in line
    assert "999" in line          # the draft's own number, which the draft owns
    assert "351" not in line


def test_a_withheld_agreement_is_counted_like_any_other():
    rep = cmn.Report()
    rep.number("Table 4 · round 3", "counts.refused", "351", 351, withhold=True)
    assert rep.mismatches == []
    assert rep.checks == 1


def test_table_four_puts_no_number_from_the_reports_in_its_output():
    """Every cell is wrong, so every column reports — and none of them tells."""
    s = cmn.subject()
    index, report = first_report(s)
    columns = list(cmn.AUDIT_COLUMN)
    draft = (
        "**Table 4.** t\n\n"
        f"| Round | {' | '.join(columns)} |\n"
        "|---|" + "---|" * len(columns) + "\n"
        f"| {index} | " + " | ".join("999999" for _ in columns) + " |\n"
    )
    rep = cmn.Report()
    cmn.check_table_four(draft, s, rep)

    assert len(rep.mismatches) == len(columns)
    truth = [str(cmn.dig(report, keys)) for keys in cmn.AUDIT_COLUMN.values()]
    for line in rep.mismatches:
        assert "withheld" in line
        for value in truth:
            assert value not in line


def test_a_round_without_an_audit_report_is_unchecked_and_not_silent(monkeypatch):
    """What a clone of this public repository sees: gaps, not agreement."""
    monkeypatch.setattr(cmn, "audit_report", lambda s, i: None)
    s = cmn.subject()
    draft = (
        "**Table 4.** t\n\n"
        "| Round | Flags kept | Refused | Malformed | Documents with no flags |\n"
        "|---|---|---|---|---|\n"
        "| 3 | 185 | 351 | 141 | 160 |\n"
    )
    rep = cmn.Report()
    cmn.check_table_four(draft, s, rep)
    assert rep.checks == 0
    assert any("denied" in line for line in rep.unchecked)


def test_the_checker_opens_nothing_for_writing(monkeypatch):
    """Rule 4 is about what is printed; a written file is the other way out.

    The reports are read on a filesystem that holds DUA-covered material, so
    "withheld from the output" is only half of it: a cache, a scratch file or a
    rewritten draft would carry the same values somewhere `release_screen.py`
    does not look.
    """
    real_open, real_path_open = builtins.open, Path.open

    def read_only(real, name):
        def wrapper(*args, **kwargs):
            mode = kwargs.get("mode", args[1] if len(args) > 1 else "r")
            if set(str(mode)) & set("wxa+"):
                raise AssertionError(f"{name} was called with mode {mode!r}")
            return real(*args, **kwargs)
        return wrapper

    def refuse(name):
        def wrapper(*args, **kwargs):
            raise AssertionError(f"{name} was called")
        return wrapper

    monkeypatch.setattr(builtins, "open", read_only(real_open, "open"))
    monkeypatch.setattr(Path, "open", read_only(real_path_open, "Path.open"))
    monkeypatch.setattr(Path, "write_text", refuse("Path.write_text"))
    monkeypatch.setattr(Path, "write_bytes", refuse("Path.write_bytes"))

    s = cmn.subject()
    index, _ = first_report(s)
    draft = (
        "**Table 4.** t\n\n"
        "| Round | Flags kept | Refused | Malformed | Documents with no flags |\n"
        "|---|---|---|---|---|\n"
        f"| {index} | 999999 | 999999 | 999999 | 999999 |\n"
    )
    rep = cmn.Report()
    cmn.check_table_four(draft, s, rep)
    assert rep.mismatches, "the run has to have read the reports to prove anything"


# ─── Table 2's two rows of rows, and the layers no table has a column for ───

def test_the_row_table_two_starts_from_is_found_by_not_naming_a_round():
    """The baseline row is identified by shape, so the draft may name it freely."""
    s = cmn.subject()
    draft = (
        "**Table 2.** t\n\n"
        "| Round | Context cues | Gazetteer | Pattern rules |\n"
        "|---|---|---|---|\n"
        "| Where it started | 999999 | 0 | 0 |\n"
    )
    rep = cmn.Report()
    cmn.check_table_two(draft, s, rep)
    assert any("started from" in line for line in rep.mismatches)


def test_two_rows_that_name_no_round_are_unchecked_rather_than_guessed_between():
    s = cmn.subject()
    draft = (
        "**Table 2.** t\n\n"
        "| Round | Context cues | Gazetteer | Pattern rules |\n"
        "|---|---|---|---|\n"
        "| Baseline | 914 | 6 | 1,639 |\n"
        "| Also baseline | 914 | 6 | 1,639 |\n"
    )
    rep = cmn.Report()
    cmn.check_table_two(draft, s, rep)
    assert rep.checks == 0
    assert any("name no round" in line for line in rep.unchecked)


def test_a_layer_with_no_column_is_checked_as_having_covered_nothing():
    """Dropping a column is a claim, and a non-zero layer contradicts it."""
    rep = cmn.Report()
    cells = {"Context cues": "1", "Gazetteer": "0", "Pattern rules": "0"}
    cmn.check_covered_by_layer("somewhere", cells, {
        "context_cue": 1, "gazetteer": 0, "regex_checksum": 0, "tagger": 5,
    }, rep)
    assert len(rep.mismatches) == 1
    assert "tagger" in rep.mismatches[0]


def test_a_marker_is_checked_against_the_scorer_and_not_assumed():
    """`sparse` in a cell is a claim about the flag in the file."""
    s = cmn.subject()
    draft = (
        "**Table 6.** t\n\n"
        "| Type | es |\n"
        "|---|---|\n"
        "| DATE | sparse |\n"
    )
    rep = cmn.Report()
    cmn.check_table_six(draft, s, rep)
    assert len(rep.mismatches) == 1
    assert "sparse marker" in rep.mismatches[0]
