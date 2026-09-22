"""Tests for `tools/derive_aligned_split.py` — `ko-surro`'s fold assignment, DESIGN §6.5.

The derivation is the thing committed in place of a second split file, so what these
tests hold is that it is a *function* of `splits/en-deid.json` and not a second sampling:

  - the direction is fixed and the key is composed, never decomposed;
  - a source note the frozen split does not assign is refused with a declared reason,
    and the nine reference-less records are that population;
  - a patient whose notes would straddle two folds raises rather than being counted;
  - nothing reads corpus text, and no message carries a manifest field.

**No corpus is needed.** `splits/en-deid.json` is committed, which is the whole point of
§6.5's ordering, so every test here runs on any checkout.

    python3 -m pytest tests/test_derive_aligned_split.py -q
"""
import copy
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.corpora import base  # noqa: E402
from tools import derive_aligned_split as derive_tool  # noqa: E402

#: DESIGN §6.5 and `splits/en-deid.json`. Written here rather than read from the file, so
#: that a test can fail when the file changes — which for these figures is the event the
#: derivation exists to notice.
N_ASSIGNABLE = 2425
N_WITHOUT_REFERENCE = 9
N_KEY_SPACE = 2434
N_PATIENTS = 163
FOLD_SIZES = {"train": 1456, "dev": 485, "test": 484}


@pytest.fixture(scope="module")
def record():
    return derive_tool.source_split()


def _manifest(tmp_path, lines):
    path = tmp_path / "ko-surro.manifest"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _a_note(record, fold):
    """One source note the frozen split places in `fold`, as (document id, patient, note)."""
    doc_id = sorted(record["folds"][fold]["document_ids"])[0]
    patient, note = doc_id.rsplit("_", 1)
    return doc_id, patient, note


# ─── the direction, and what it is pinned to ───────────────────────────────────


def test_the_direction_is_declared_and_not_an_argument():
    # §6.5 decides which split is defined first. A --from/--to pair would make that
    # decision look like something a caller chooses at run time.
    assert derive_tool.SOURCE_CORPUS == "en-deid"
    assert derive_tool.DERIVED_CORPUS == "ko-surro"


def test_the_derivation_records_the_split_it_was_made_against(record, tmp_path):
    doc_id, patient, note = _a_note(record, "dev")
    manifest = _manifest(tmp_path, [f"ko_{doc_id} {patient} {note}"])
    derivation = derive_tool.derive(record, derive_tool.read_manifest(manifest))
    assert derivation.source_manifest_digest == record["source"]["manifest_digest"]
    assert derivation.source_commit == record["repository"]["commit"]


# ─── the key is composed, never decomposed ─────────────────────────────────────


def test_the_key_is_composed_from_two_fields():
    note = derive_tool.SourceNote(document="ko_42_7", patient="42", note="7", line=1)
    assert note.key() == "42_7"


def test_the_manifest_carries_patient_and_note_separately(tmp_path):
    # Three fields, because composing `{patient}_{note}` is done here in the same
    # direction as the loader. A composed id in the manifest would have to be taken
    # apart, which is a second answer to which half is the patient.
    manifest = _manifest(tmp_path, ["ko_42_7 42 7"])
    notes = derive_tool.read_manifest(manifest)
    assert [(n.document, n.patient, n.note) for n in notes] == [("ko_42_7", "42", "7")]


def test_no_module_source_line_splits_a_composed_document_id():
    """The prohibition, held against the file's own text rather than its behaviour.

    A `rsplit("_")` that appeared here would pass every behavioural test above while
    introducing the second answer this module's docstring forbids. `_a_note` in this file
    does decompose, and that is the test harness reading the split file's ids, not the
    derivation.
    """
    source = open(derive_tool.__file__, encoding="utf-8").read()
    for forbidden in ("rsplit(", "partition(", 'split("_"'):
        assert forbidden not in source, (
            f"tools/derive_aligned_split.py contains {forbidden!r}: the key is composed "
            "in one direction only (DESIGN §6.5, this module's docstring)"
        )


# ─── what the frozen split fixes before the corpus arrives ─────────────────────


def test_self_check_reports_the_assignable_notes_and_the_key_space(record):
    report = derive_tool.self_check(record)
    assert report["assignable"] == N_ASSIGNABLE
    assert report["refusable_now"] == N_WITHOUT_REFERENCE
    assert report["key_space"] == N_KEY_SPACE
    assert report["n_groups"] == N_PATIENTS
    assert report["groups_crossing"] == 0
    assert report["by_fold"] == FOLD_SIZES


def test_the_nine_records_without_reference_are_in_no_fold(record):
    unassigned = derive_tool.unassigned_notes(record)
    folds = derive_tool.assigned_folds(record)
    assert len(unassigned) == N_WITHOUT_REFERENCE
    assert not (unassigned & set(folds))


def test_self_check_refuses_a_split_file_that_both_places_and_disowns_a_record(record):
    broken = copy.deepcopy(record)
    placed = broken["folds"]["dev"]["document_ids"][0]
    broken["corpus_specific"]["records_without_reference"].append(placed)
    broken["corpus_specific"]["n_records_without_reference"] += 1
    with pytest.raises(derive_tool.AlignmentError, match="carrying no"):
        derive_tool.self_check(broken)


def test_self_check_refuses_a_split_whose_groups_cross(record):
    broken = copy.deepcopy(record)
    broken["group_key"]["crosses_split"]["n_groups_crossing"] = 1
    with pytest.raises(derive_tool.AlignmentError, match="crossing"):
        derive_tool.self_check(broken)


def test_self_check_refuses_a_split_whose_fold_counts_do_not_match_its_ids(record):
    broken = copy.deepcopy(record)
    broken["folds"]["dev"]["document_ids"].pop()
    with pytest.raises(derive_tool.AlignmentError, match="list"):
        derive_tool.self_check(broken)


def test_an_unlisted_reference_set_is_refused_rather_than_computed(record):
    broken = copy.deepcopy(record)
    del broken["corpus_specific"]["records_without_reference"]
    with pytest.raises(derive_tool.AlignmentError, match="records_without_reference"):
        derive_tool.unassigned_notes(broken)


def test_a_miscounted_reference_set_is_refused(record):
    broken = copy.deepcopy(record)
    broken["corpus_specific"]["n_records_without_reference"] += 1
    with pytest.raises(derive_tool.AlignmentError, match="n_records_without_reference"):
        derive_tool.unassigned_notes(broken)


# ─── assignment ────────────────────────────────────────────────────────────────


def test_a_document_takes_its_source_notes_fold(record, tmp_path):
    lines, expected = [], {}
    for fold in FOLD_SIZES:
        doc_id, patient, note = _a_note(record, fold)
        lines.append(f"ko_{doc_id} {patient} {note}")
        expected[f"ko_{doc_id}"] = fold
    derivation = derive_tool.derive(record, derive_tool.read_manifest(_manifest(tmp_path, lines)))
    assert derivation.assigned == expected
    assert derivation.refused == {}


def test_the_test_fold_transfers_like_any_other(record, tmp_path):
    """The point of option B: a sealed document's counterpart is sealed too.

    With independent splits about three quarters of each corpus's sealed documents would
    sit in the other's dev fold while both per-corpus checks reported the seal intact
    (§6.5). This is the assertion that the alignment is what prevents that.
    """
    doc_id, patient, note = _a_note(record, "test")
    manifest = _manifest(tmp_path, [f"ko_{doc_id} {patient} {note}"])
    derivation = derive_tool.derive(record, derive_tool.read_manifest(manifest))
    assert derivation.assigned == {f"ko_{doc_id}": "test"}


def test_every_patient_in_the_manifest_gets_one_fold(record, tmp_path):
    fold = "dev"
    ids = sorted(record["folds"][fold]["document_ids"])[:20]
    lines = [f"ko_{i} {i.rsplit('_', 1)[0]} {i.rsplit('_', 1)[1]}" for i in ids]
    derivation = derive_tool.derive(record, derive_tool.read_manifest(_manifest(tmp_path, lines)))
    assert set(derivation.patient_folds.values()) == {fold}


def test_source_notes_no_manifest_claims_are_counted_not_dropped(record, tmp_path):
    doc_id, patient, note = _a_note(record, "dev")
    manifest = _manifest(tmp_path, [f"ko_{doc_id} {patient} {note}"])
    derivation = derive_tool.derive(record, derive_tool.read_manifest(manifest))
    assert len(derivation.unclaimed) == N_ASSIGNABLE - 1
    assert doc_id not in derivation.unclaimed


# ─── refusal ───────────────────────────────────────────────────────────────────


def test_a_reference_less_record_is_refused_with_the_declared_reason(record, tmp_path):
    without = sorted(derive_tool.unassigned_notes(record))[0]
    patient, note = without.rsplit("_", 1)
    manifest = _manifest(tmp_path, [f"ko_{without} {patient} {note}"])
    derivation = derive_tool.derive(record, derive_tool.read_manifest(manifest))
    assert derivation.assigned == {}
    assert derivation.refused == {f"ko_{without}": "source_note_unassigned"}


def test_a_note_the_release_never_had_is_a_different_refusal(record, tmp_path):
    manifest = _manifest(tmp_path, ["ko_99999_1 99999 1"])
    derivation = derive_tool.derive(record, derive_tool.read_manifest(manifest))
    assert derivation.refused == {"ko_99999_1": "source_note_unknown"}


def test_two_documents_claiming_one_source_note_are_both_refused(record, tmp_path):
    doc_id, patient, note = _a_note(record, "dev")
    manifest = _manifest(
        tmp_path, [f"ko_a {patient} {note}", f"ko_b {patient} {note}"]
    )
    derivation = derive_tool.derive(record, derive_tool.read_manifest(manifest))
    assert derivation.assigned == {}
    assert derivation.refused == {
        "ko_a": "duplicate_source_note",
        "ko_b": "duplicate_source_note",
    }


def test_every_refusal_reason_is_declared_in_naming_yaml(record, tmp_path):
    declared = set(base.alignment_refusals())
    without = sorted(derive_tool.unassigned_notes(record))[0]
    patient, note = without.rsplit("_", 1)
    dev_id, dev_patient, dev_note = _a_note(record, "dev")
    manifest = _manifest(
        tmp_path,
        [
            f"ko_{without} {patient} {note}",
            "ko_99999_1 99999 1",
            f"ko_a {dev_patient} {dev_note}",
            f"ko_b {dev_patient} {dev_note}",
        ],
    )
    derivation = derive_tool.derive(record, derive_tool.read_manifest(manifest))
    assert set(derivation.refused.values()) == declared
    assert set(derivation.counts()) >= {f"refused.{r}" for r in declared}


def test_an_undeclared_reason_cannot_be_written():
    with pytest.raises(base.CorpusError, match="alignment refusal reason"):
        base.check_alignment_refusal("source_note_looked_wrong")


# ─── refusal is not the same thing as the derivation failing ───────────────────


def test_a_patient_straddling_two_folds_raises_rather_than_refusing(record, tmp_path):
    """The failure §6.5 opens with: half-aligned, and every per-corpus check content."""
    broken = copy.deepcopy(record)
    moved = broken["folds"]["dev"]["document_ids"][0]
    patient = moved.rsplit("_", 1)[0]
    same_patient = [
        i for i in broken["folds"]["dev"]["document_ids"] if i.rsplit("_", 1)[0] == patient
    ]
    if len(same_patient) < 2:
        pytest.skip("this patient has one note in dev, so it cannot straddle")
    broken["folds"]["dev"]["document_ids"].remove(same_patient[1])
    broken["folds"]["train"]["document_ids"].append(same_patient[1])
    lines = [f"ko_{i} {i.rsplit('_', 1)[0]} {i.rsplit('_', 1)[1]}" for i in same_patient[:2]]
    with pytest.raises(derive_tool.AlignmentError, match="two folds"):
        derive_tool.derive(broken, derive_tool.read_manifest(_manifest(tmp_path, lines)))


def test_a_refused_documents_text_side_comes_from_its_patient(record, tmp_path):
    """`tools/prepare_endeid.py` places the same nine records the same way.

    A refused document is in no fold and still has text. Deciding its side by its patient
    is what keeps a sealed patient's Korean text out of where rule development reads it.
    """
    without = sorted(derive_tool.unassigned_notes(record))[0]
    patient, note = without.rsplit("_", 1)
    sibling = next(
        (
            fold_name,
            i,
        )
        for fold_name, block in record["folds"].items()
        for i in block["document_ids"]
        if i.rsplit("_", 1)[0] == patient
    )
    fold, sibling_id = sibling
    manifest = _manifest(
        tmp_path,
        [
            f"ko_{without} {patient} {note}",
            f"ko_{sibling_id} {patient} {sibling_id.rsplit('_', 1)[1]}",
        ],
    )
    derivation = derive_tool.derive(record, derive_tool.read_manifest(manifest))
    assert derivation.refused == {f"ko_{without}": "source_note_unassigned"}
    assert derivation.text_side == {f"ko_{without}": fold}
    assert derivation.unplaced == []


def test_a_refused_document_with_no_other_note_of_its_patient_is_unplaced(record, tmp_path):
    manifest = _manifest(tmp_path, ["ko_99999_1 99999 1"])
    derivation = derive_tool.derive(record, derive_tool.read_manifest(manifest))
    assert derivation.unplaced == ["ko_99999_1"]
    assert derivation.text_side == {}


def test_an_unplaced_document_makes_the_cli_fail(record, tmp_path, capsys):
    manifest = _manifest(tmp_path, ["ko_99999_1 99999 1"])
    assert derive_tool.main(["--manifest", str(manifest)]) == 1
    assert "UNKNOWN" in capsys.readouterr().out


# ─── the manifest parser ───────────────────────────────────────────────────────


def test_comments_and_blank_lines_are_skipped(tmp_path):
    manifest = _manifest(tmp_path, ["# a comment", "", "ko_42_7 42 7  # trailing"])
    assert [n.key() for n in derive_tool.read_manifest(manifest)] == ["42_7"]


def test_a_line_with_the_wrong_field_count_is_refused(tmp_path):
    manifest = _manifest(tmp_path, ["ko_42_7 42"])
    with pytest.raises(derive_tool.AlignmentError, match="line 1"):
        derive_tool.read_manifest(manifest)


def test_the_field_count_message_carries_no_manifest_field(tmp_path):
    """CLAUDE.md: a message carries indices and counts, never corpus content.

    The release is authentic clinical text under a DUA, and the rule does not branch on
    whether the field in question happens to be an identifier rather than a phrase.
    """
    manifest = _manifest(tmp_path, ["ko_42_7 42"])
    with pytest.raises(derive_tool.AlignmentError) as caught:
        derive_tool.read_manifest(manifest)
    message = str(caught.value)
    for field in ("ko_42_7", "42_7"):
        assert field not in message
    assert "line 1" in message and "2 whitespace" in message


# ─── the seal, and the artefact this tool does not write ───────────────────────


def test_the_tool_writes_no_split_file(record, tmp_path, capsys):
    from src import split as split_module

    before = split_module.split_path(derive_tool.DERIVED_CORPUS).exists()
    doc_id, patient, note = _a_note(record, "dev")
    manifest = _manifest(tmp_path, [f"ko_{doc_id} {patient} {note}"])
    assert derive_tool.main(["--manifest", str(manifest)]) == 0
    assert split_module.split_path(derive_tool.DERIVED_CORPUS).exists() == before
    assert "is not written by this tool" in capsys.readouterr().out


def test_check_runs_with_no_manifest(capsys):
    assert derive_tool.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "no ko-surro manifest read" in out
    assert str(N_KEY_SPACE) in out


def test_neither_argument_is_an_error():
    with pytest.raises(SystemExit):
        derive_tool.main([])


def test_the_module_never_names_the_sealed_root():
    """It reads one split file and one manifest. `sealed/` is not one of its paths."""
    source = open(derive_tool.__file__, encoding="utf-8").read()
    body = source.split('"""', 2)[2]  # the docstring does discuss the seal, by design
    for forbidden in ("sealed_root", "sealed/", "corpus_root"):
        assert forbidden not in body, (
            f"tools/derive_aligned_split.py resolves {forbidden!r}: the derivation "
            "consults no text and opens no corpus path (DESIGN §6.5)"
        )
