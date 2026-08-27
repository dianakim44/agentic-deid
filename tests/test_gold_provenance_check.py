"""`tools/gold_provenance_check.py` — alignment, payload classification, and the rule
that no output may carry a surface form.

The last of those is the one that has to be pinned by a test rather than by a comment.
This tool reads real nursing text under a DUA and prints a report; `tools/release_screen.py`
cannot see terminal output, so the only durable guard is here. Fixtures below are synthetic
and contain no corpus text.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import gold_provenance_check as gpc  # noqa: E402


def _release(tmp_path: Path, masked: str, surrogate: str) -> Path:
    d = tmp_path / "release"
    d.mkdir()
    (d / "id.res").write_text(masked, encoding="utf-8")
    (d / "id.text").write_text(surrogate, encoding="utf-8")
    return d


MASKED = (
    "START_OF_RECORD=1||||1||||\n"
    "seen by [**Doctor Last Name 7**] on [**04-18**] .\n"
    "||||END_OF_RECORD\n"
)
SURROGATE = (
    "START_OF_RECORD=1||||1||||\n"
    "seen by Qqqqq Wwwww on 11-02 .\n"
    "||||END_OF_RECORD\n"
)


def test_records_parse_and_uid_is_patient_and_note_index(tmp_path: Path) -> None:
    recs = gpc.parse_records(_release(tmp_path, MASKED, SURROGATE) / "id.res")
    assert [r.uid for r in recs] == ["1_1"]


def test_alignment_recovers_the_replacement_spans(tmp_path: Path) -> None:
    d = _release(tmp_path, MASKED, SURROGATE)
    masked = gpc.parse_records(d / "id.res")[0]
    surro = gpc.parse_records(d / "id.text")[0]
    aligned = gpc.align(masked, surro)
    assert len(aligned) == 2
    body = surro.body
    assert [body[a.span.start : a.span.end] for a in aligned] == ["Qqqqq Wwwww", "11-02"]


def test_value_payload_is_distinguished_from_a_type_name(tmp_path: Path) -> None:
    d = _release(tmp_path, MASKED, SURROGATE)
    aligned = gpc.align(gpc.parse_records(d / "id.res")[0], gpc.parse_records(d / "id.text")[0])
    assert [a.payload_is_value for a in aligned] == [False, True]


@pytest.mark.parametrize(
    "payload,is_value",
    [
        ("04-18", True),
        ("2019-04-18", True),
        ("'92", True),
        ("1994", True),
        ("", True),
        ("Hospital 1", False),
        ("Known lastname 22", False),
    ],
)
def test_value_shapes(payload: str, is_value: bool) -> None:
    assert bool(gpc._VALUELIKE_RE.match(payload)) is is_value


def test_unrecoverable_span_is_reported_not_guessed(tmp_path: Path) -> None:
    # The surrogate body does not contain the masked body's literal context, so the
    # placeholder cannot be located. A guessed offset would be worse than none.
    d = _release(
        tmp_path,
        "START_OF_RECORD=1||||1||||\nalpha [**Hospital 1**] beta\n||||END_OF_RECORD\n",
        "START_OF_RECORD=1||||1||||\nnothing in common here\n||||END_OF_RECORD\n",
    )
    aligned = gpc.align(gpc.parse_records(d / "id.res")[0], gpc.parse_records(d / "id.text")[0])
    assert [a.span for a in aligned] == [None]


def test_mismatched_record_framing_raises_without_quoting_the_body(tmp_path: Path) -> None:
    d = tmp_path / "r"
    d.mkdir()
    p = d / "id.res"
    p.write_text(
        "START_OF_RECORD=1||||1||||\nsecret patient sentence\nSTART_OF_RECORD=1||||2||||\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as excinfo:
        gpc.parse_records(p)
    msg = str(excinfo.value)
    assert "secret" not in msg and "patient sentence" not in msg
    assert "line 3" in msg


def test_safe_label_withholds_anything_that_is_not_a_bare_identifier() -> None:
    vocab: Counter[str] = Counter({"Hospital": 9})
    assert gpc._safe_label("Hospital", vocab) == "Hospital"
    # A phrase reaching the type column — the misparse this guard exists for.
    assert gpc._safe_label("Jane Q Patient", vocab) == "<label-withheld>"
    assert gpc._safe_label("2019-04-18", vocab) == "<label-withheld>"
    assert gpc._safe_label(None, vocab) == "<none>"


def test_sniff_reports_structure_and_no_field_values(tmp_path: Path) -> None:
    p = tmp_path / "id.deid"
    p.write_text("1||||1||||14||||25||||Doctor||||Jane Q Patient\n", encoding="utf-8")
    info = gpc.sniff(p)
    flat = repr(info)
    assert "Jane" not in flat and "Patient" not in flat and "Doctor" not in flat
    assert info["delimiter"] == "||||"
    assert info["field_counts"] == {6: 1}


def test_describe_output_names_absent_reference_files(tmp_path: Path, capsys) -> None:
    d = _release(tmp_path, MASKED, SURROGATE)
    assert gpc.cmd_describe(d) == 0
    out = capsys.readouterr().out
    for name in gpc.REFERENCE:
        assert f"{name}: ABSENT" in out
    assert "silver" in out


def test_check_stops_cleanly_when_the_reference_is_absent(tmp_path: Path, capsys) -> None:
    d = _release(tmp_path, MASKED, SURROGATE)
    rc = gpc.cmd_check(d, min_land=0.95)
    out = capsys.readouterr().out
    assert rc == 1
    assert "not answerable" in out
    # The silver-side block is still reported: it needs no reference file.
    assert "placeholders               2" in out


def test_check_refuses_a_disagreement_rate_when_the_parse_does_not_land(tmp_path: Path, capsys) -> None:
    d = _release(tmp_path, MASKED, SURROGATE)
    # Offsets far outside the record body: what a wrong column-role inference looks like.
    (d / "id.deid").write_text("1||||1||||90000||||90010\n", encoding="utf-8")
    rc = gpc.cmd_check(d, min_land=0.95)
    out = capsys.readouterr().out
    assert rc == 1
    assert "REFUSED" in out
    assert "disagreeing spans" not in out


def test_check_reports_no_note_text_when_the_reference_lands(tmp_path: Path, capsys) -> None:
    d = _release(tmp_path, MASKED, SURROGATE)
    body = gpc.parse_records(d / "id.text")[0].body
    s = body.index("Qqqqq")
    (d / "id.deid").write_text(f"1||||1||||{s}||||{s + 11}||||Doctor\n", encoding="utf-8")
    assert gpc.cmd_check(d, min_land=0.5) == 0
    out = capsys.readouterr().out
    assert "Qqqqq" not in out and "Wwwww" not in out and "11-02" not in out
    assert "matched (overlapping)      1" in out
