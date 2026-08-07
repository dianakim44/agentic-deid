"""Tests for src/porting/human_arm.py — the `port-human` harness.

The harness writes two things: a log line that gets committed, and a rendered window
that must not be. Most of what is checked here is that boundary. `summarise()` is what
travels into a terminal or a commit message, so it is tested for what it does *not*
contain — no surface form and no document offset — and that is a test about a public
artefact rather than about a data structure.

The fixtures are constructed. `initial_error_pool()` is exercised against documents
built here rather than against MEDDOCAN on disk: the property under test is "dev fold
only, in-scope only", and a real corpus exercises the common case and neither boundary.
The one surface form in this file (`SURFACE`) exists so the summary can be checked for
its absence; it is invented, not corpus text.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora.base import Document, Span                     # noqa: E402
from src.porting import human_arm                              # noqa: E402
from src.corpora.base import axis                               # noqa: E402
from src.porting.human_arm import (                            # noqa: E402
    CONSULTED_AXIS, EVENTS, FIELDS, SCOPES, VIOLATION, PortHumanError, append,
    draw_iteration, freeze_path, freeze_window, initial_error_pool, log_line, log_path,
    render_for_author, summarise, window_drift,
)
from src.sample import (                                       # noqa: E402
    MISSED, WINDOW_FILES, ErrorSpan, window_hashes,
)

#: Invented, not from any corpus. Long and distinctive so a test asserting its absence
#: cannot pass by coincidence.
SURFACE = "Zzyzx Quinbolt"


def doc(doc_id: str, split: str, *, excluded: bool = False) -> Document:
    """One document with one span, at a distinctive offset."""
    text = "." * 1000 + SURFACE + "." * 1000
    span = (Span(start=1000, end=1000 + len(SURFACE), surface=SURFACE,
                 subtype="SEXO_SUJETO_ASISTENCIA", excluded=True)
            if excluded else
            Span(start=1000, end=1000 + len(SURFACE), surface=SURFACE,
                 subtype="NOMBRE_SUJETO_ASISTENCIA", phi_type="NAME"))
    return Document(doc_id=doc_id, corpus_id="es-meddocan", text=text,
                    spans=[span], split=split)


def err(doc_id: str, index: int, phi_type: str = "NAME",
        start: int = 1000) -> ErrorSpan:
    return ErrorSpan(doc_id=doc_id, span_index=index, phi_type=phi_type,
                     kind=MISSED, start=start, end=start + 6)


# ─── the initial pool is the dev fold, in scope, and nothing else ───────────

def test_the_initial_pool_is_every_in_scope_dev_gold_span(monkeypatch):
    docs = [doc("dev1", "dev"), doc("dev2", "dev"), doc("tr1", "train")]
    monkeypatch.setattr(human_arm, "load", lambda corpus: docs)
    pool = initial_error_pool("es-meddocan")
    assert sorted(e.doc_id for e in pool) == ["dev1", "dev2"]
    assert {e.kind for e in pool} == {MISSED}


def test_the_initial_pool_never_reaches_the_test_fold(monkeypatch):
    """Not a redundant check of the dev filter: this is the seal (CLAUDE.md), and the
    filter is one line away from `!= "train"` at any point."""
    docs = [doc("dev1", "dev"), doc("te1", "test")]
    monkeypatch.setattr(human_arm, "load", lambda corpus: docs)
    assert [e.doc_id for e in initial_error_pool("es-meddocan")] == ["dev1"]


def test_excluded_spans_are_not_in_the_pool(monkeypatch):
    """DESIGN §9.1: they carry no canonical type, so they cannot be stratified by one."""
    docs = [doc("dev1", "dev"), doc("dev2", "dev", excluded=True)]
    monkeypatch.setattr(human_arm, "load", lambda corpus: docs)
    assert [e.doc_id for e in initial_error_pool("es-meddocan")] == ["dev1"]


def test_span_index_indexes_the_documents_own_span_list(monkeypatch):
    """The referent DESIGN §11.2 fixes. An index into a filtered list would resolve to
    the wrong span for any document holding an excluded span before an in-scope one."""
    text = "." * 200
    spans = [
        Span(start=10, end=16, surface="aaaaaa", subtype="SEXO_SUJETO_ASISTENCIA",
             excluded=True),
        Span(start=30, end=36, surface="bbbbbb", subtype="NOMBRE_SUJETO_ASISTENCIA",
             phi_type="NAME"),
    ]
    d = Document(doc_id="dev1", corpus_id="es-meddocan", text=text, spans=spans,
                 split="dev")
    monkeypatch.setattr(human_arm, "load", lambda corpus: [d])
    (only,) = initial_error_pool("es-meddocan")
    assert only.span_index == 1
    assert d.spans[only.span_index].start == only.start


def test_an_empty_dev_fold_raises_rather_than_sampling_nothing(monkeypatch):
    monkeypatch.setattr(human_arm, "load", lambda corpus: [doc("tr1", "train")])
    with pytest.raises(PortHumanError) as e:
        initial_error_pool("es-meddocan")
    assert "split file" in str(e.value)


def test_the_empty_pool_message_quotes_no_surface(monkeypatch):
    monkeypatch.setattr(human_arm, "load", lambda corpus: [doc("tr1", "train")])
    with pytest.raises(PortHumanError) as e:
        initial_error_pool("es-meddocan")
    assert SURFACE not in str(e.value)


# ─── the freeze record ──────────────────────────────────────────────────────

def test_the_freeze_record_holds_both_hashes_and_the_arm(tmp_path, monkeypatch):
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    record = freeze_window("es-meddocan", "R", "sup-free")
    assert record["prompt_sha256"] == window_hashes()["prompt_sha256"]
    assert record["sampling_sha256"] == window_hashes()["sampling_sha256"]
    assert record["porting"] == "port-human"
    assert record["files"] == list(WINDOW_FILES)


def test_freezing_twice_returns_the_first_record_and_does_not_rewrite(tmp_path,
                                                                     monkeypatch):
    """The one question a rewritable freeze record cannot answer is the only one it is
    for: what the window was when the run *started*."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    first = freeze_window("es-meddocan", "R", "sup-free")
    path = human_arm.freeze_path("es-meddocan", "R", "sup-free")
    tampered = dict(first, prompt_sha256="sha256:" + "0" * 64)
    path.write_text(json.dumps(tampered), encoding="utf-8")
    again = freeze_window("es-meddocan", "R", "sup-free")
    assert again["prompt_sha256"] == "sha256:" + "0" * 64


def test_no_drift_on_a_freshly_frozen_window(tmp_path, monkeypatch):
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    freeze_window("es-meddocan", "R", "sup-free")
    assert window_drift("es-meddocan", "R", "sup-free") == []


def test_drift_names_the_field_that_moved(tmp_path, monkeypatch):
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    record = freeze_window("es-meddocan", "R", "sup-free")
    path = human_arm.freeze_path("es-meddocan", "R", "sup-free")
    path.write_text(json.dumps(dict(record, sampling_sha256="sha256:" + "0" * 64)),
                    encoding="utf-8")
    assert window_drift("es-meddocan", "R", "sup-free") == [
        "sampling_sha256"]


def test_drift_on_an_unfrozen_arm_raises_rather_than_reporting_none(tmp_path,
                                                                   monkeypatch):
    """Returning [] for a missing record would read as "the window is intact"."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    with pytest.raises(PortHumanError) as e:
        window_drift("es-meddocan", "R", "sup-free")
    assert "freeze_window()" in str(e.value)


def test_the_paths_follow_the_config_rather_than_a_copy_of_it(monkeypatch, tmp_path):
    """The check DESIGN §11.2's requirement actually needs. A literal in this module that
    happens to equal the config passes every path-shape assertion — the defect is not a
    wrong path but a second authority on where the arm writes, and it only becomes a
    wrong path on the day the config moves. Redirecting the config is what tells the two
    apart."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    monkeypatch.setattr(human_arm, "path_template",
                        lambda key: "elsewhere/{corpus}/{detector}/{supervision}/" + key)
    assert log_path("es-meddocan", "R", "sup-free") == (
        tmp_path / "elsewhere/es-meddocan/R/sup-free/humanlog")
    assert freeze_path("es-meddocan", "R", "sup-free") == (
        tmp_path / "elsewhere/es-meddocan/R/sup-free/humanfreeze")


def test_an_undeclared_path_key_is_refused():
    """A caller asking for a path the config does not declare has invented an artifact,
    and a default would put it somewhere plausible."""
    from src.corpora.base import CorpusError, path_template
    with pytest.raises(CorpusError) as e:
        path_template("humanlogs")
    assert "paths.humanlogs" in str(e.value)


def test_the_freeze_path_is_not_the_log_path(monkeypatch, tmp_path):
    """They share a directory, and a template collapse would make the freeze record
    append-target the log — silently, since both are written with the same encoding."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    assert (log_path("es-meddocan", "R", "sup-free")
            != freeze_path("es-meddocan", "R", "sup-free"))


def test_both_paths_come_from_naming_yaml_and_share_a_directory(monkeypatch, tmp_path):
    """DESIGN §11.2 requires paths.humanlog in the config rather than as a literal, for
    the reason axis() exists: two copies of a path are two places it can change."""
    from src.corpora.base import path_template
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    log = log_path("es-meddocan", "R", "sup-free")
    freeze = freeze_path("es-meddocan", "R", "sup-free")
    assert log.parent == freeze.parent
    assert log.name == "human_log.jsonl"
    assert freeze.name == "window_freeze.json"
    assert "{" not in path_template("humanlog").format(
        corpus="es-meddocan", detector="R", supervision="sup-free")


# ─── the draw and its provenance travel together ────────────────────────────

def test_draw_iteration_returns_the_sample_with_its_provenance():
    pool = [err("d1", i, start=100 + i * 10) for i in range(20)]
    sample, prov = draw_iteration(pool, "es-meddocan", 1, n=5)
    assert len(sample) == 5
    assert prov["iteration"] == 1
    assert prov["n_error_spans"] == 5
    assert prov["seed"] > 0


def test_draw_iteration_is_the_shared_draw_not_a_second_one():
    """The premise DESIGN §11.1 rests the arm on. If this harness sampled for itself,
    the two arms' windows would differ for a reason that is not the experiment."""
    from src.sample import draw
    pool = [err("d1", i, start=100 + i * 10) for i in range(20)]
    sample, _ = draw_iteration(pool, "es-meddocan", 3, n=7)
    assert sample == draw(pool, "es-meddocan", 3, n=7)


# ─── the summary is safe to say out loud ────────────────────────────────────

def test_the_summary_is_counts_only():
    pool = [err("d1", i, start=1000 + i * 10) for i in range(20)]
    pool += [err("d2", i, "DATE", start=2000 + i * 10) for i in range(5)]
    sample, _ = draw_iteration(pool, "es-meddocan", 1, n=6)
    s = summarise(sample, pool)
    assert s["pool_size"] == 25
    assert s["sample_size"] == 6
    assert set(s["by_type"]) == {"NAME", "DATE"}
    assert sum(v["drawn"] for v in s["by_type"].values()) == 6
    assert s["by_kind"] == {MISSED: 6}


def test_the_summary_carries_no_offsets():
    """An offset is not text, but a (doc_id, offset) pair beside a type is a pointer
    into the corpus. That is the right referent for the committed log and the wrong one
    for a summary read aloud (docstring on summarise)."""
    pool = [err("d1", 0, start=743197)]
    sample, _ = draw_iteration(pool, "es-meddocan", 1, n=1)
    blob = json.dumps(summarise(sample, pool))
    assert "743197" not in blob
    assert "743203" not in blob
    assert "d1" not in blob


def test_the_summary_names_types_that_are_in_the_pool_but_undrawn():
    """A type with errors and no slot is a fact about the window, so it appears with
    drawn 0 rather than vanishing — the alternative reads as "no such errors"."""
    pool = [err("d1", i, start=1000 + i * 10) for i in range(60)]
    pool += [err("d2", 0, "PROFESSION", start=2000)]
    sample, _ = draw_iteration(pool, "es-meddocan", 1, n=1)
    s = summarise(sample, pool)
    assert set(s["by_type"]) == {"NAME", "PROFESSION"}
    assert s["by_type"]["PROFESSION"]["in_pool"] == 1
    assert sum(v["drawn"] for v in s["by_type"].values()) == 1


# ─── the rendered window is what the author reads ───────────────────────────

def test_the_render_offsets_are_within_the_context_not_the_document():
    d = doc("dev1", "dev")
    block = render_for_author([err("dev1", 0, start=1000)], {"dev1": d}, 120)
    assert "(120, 126)" in block
    assert "(1000," not in block


def test_the_render_clips_the_window_to_the_document():
    text = "abcdefghij"
    d = Document(doc_id="dev1", corpus_id="es-meddocan", text=text, split="dev",
                 spans=[Span(start=2, end=5, surface="cde", subtype="X",
                             phi_type="NAME")])
    block = render_for_author([err("dev1", 0, start=2)], {"dev1": d}, 120)
    assert "(2, 8)" in block          # left clipped at 0, so window offset == document


def test_the_render_contains_the_context_and_the_summary_does_not():
    """The two views, side by side. The rendered block is the only place the corpus
    text appears, and it is never written to disk (rule_author.md §6)."""
    d = doc("dev1", "dev")
    sample = [err("dev1", 0, start=1000)]
    assert SURFACE in render_for_author(sample, {"dev1": d}, 120)
    assert SURFACE not in json.dumps(summarise(sample, sample))


def test_the_render_flattens_newlines():
    """One span per block, so a context holding a newline would otherwise be
    indistinguishable from the start of the next field."""
    text = "aaa\nbbb\nccc"
    d = Document(doc_id="dev1", corpus_id="es-meddocan", text=text, split="dev",
                 spans=[Span(start=4, end=7, surface="bbb", subtype="X",
                             phi_type="NAME")])
    block = render_for_author([err("dev1", 0, start=4)], {"dev1": d}, 120)
    assert "aaa bbb ccc" in block


# ─── the log line ───────────────────────────────────────────────────────────

def test_the_line_has_every_field_in_order():
    record = log_line(1, "read_sample", "none")
    assert tuple(record) == FIELDS


def test_absent_values_are_written_as_null_not_omitted():
    """An absent key and a key whose value is unknown are different facts, and only
    one of them survives into an aggregation."""
    record = log_line(1, "read_sample", "none")
    assert record["human_minutes"] is None
    assert record["actually_reused"] is None
    assert json.loads(json.dumps(record)).keys() == record.keys()
    assert '"human_minutes": null' in json.dumps(record)


def test_the_window_hashes_are_filled_by_the_line_not_the_caller():
    """A caller that has to remember them is a caller that forgets on the line that
    matters."""
    record = log_line(1, "read_sample", "none")
    assert record["prompt_sha256"] == window_hashes()["prompt_sha256"]
    assert record["sampling_sha256"] == window_hashes()["sampling_sha256"]
    assert record["prompt_sha256"].startswith("sha256:")


def test_the_two_hashes_are_of_different_files():
    record = log_line(1, "read_sample", "none")
    assert record["prompt_sha256"] != record["sampling_sha256"]


def test_an_unknown_event_is_refused():
    with pytest.raises(PortHumanError) as e:
        log_line(1, "read-sample", "none")
    assert "DESIGN" in str(e.value)


@pytest.mark.parametrize("event", EVENTS)
def test_every_declared_event_is_accepted(event):
    assert log_line(1, event, "none")["event"] == event


@pytest.mark.parametrize("scope", SCOPES)
def test_every_declared_scope_is_accepted(scope):
    assert log_line(1, "decision", "none", predicted_scope=scope)["predicted_scope"] == scope


def test_an_unknown_scope_is_refused():
    with pytest.raises(PortHumanError):
        log_line(1, "decision", "none", predicted_scope="general")


def test_actually_reused_is_three_valued():
    for value in (True, False, None):
        assert log_line(1, "decision", "none", actually_reused=value)[
            "actually_reused"] is value
    with pytest.raises(PortHumanError) as e:
        log_line(1, "decision", "none", actually_reused="yes")
    assert "second corpus" in str(e.value)


def test_negative_minutes_are_refused():
    with pytest.raises(PortHumanError):
        log_line(1, "rule_edit", "none", human_minutes=-5)
    assert log_line(1, "rule_edit", "none", human_minutes=0)["human_minutes"] == 0


# ─── the §8 self-report ─────────────────────────────────────────────────────
#
# `docs/prompts/rule_author.md` §8 forbids asking a language model what a rule should
# be during a port-human iteration, because an author who transcribes a model's answer
# has run port-oneshot with a slower interface and the control no longer holds. The
# clause has no enforcement beyond this field, which is what these tests are about:
# the field cannot be left unfilled, cannot hold an invented value, and — the one that
# matters most — is not allowed to refuse the violation it exists to record.

def test_the_self_report_is_required_and_has_no_default():
    """A default of "none" would record "no model was consulted" for every caller who
    did not think about the question, which is the answer the field exists to stop
    being free."""
    with pytest.raises(TypeError):
        log_line(1, "read_sample")


def test_null_is_not_an_accepted_self_report():
    """An unfilled field is indistinguishable from an unproblematic one."""
    with pytest.raises(PortHumanError) as e:
        log_line(1, "read_sample", None)
    assert "no default" in str(e.value)


@pytest.mark.parametrize("value", sorted(axis(CONSULTED_AXIS)))
def test_every_declared_self_report_value_is_accepted(value):
    assert log_line(1, "decision", value)["model_consulted"] == value


def test_the_violation_value_is_recorded_and_not_refused():
    """The test this section exists for. A self-report field that rejects the answer it
    exists to capture collects only the other answers, and the arm's integrity is then
    documented by a file that could not have recorded its absence (§8.2)."""
    record = log_line(4, "rule_edit", VIOLATION, human_minutes=12,
                      decision="asked a model which pattern fits")
    assert record["model_consulted"] == VIOLATION
    assert record["iteration"] == 4


def test_the_violation_survives_a_round_trip_to_the_log(tmp_path, monkeypatch):
    """Refusing to *write* it would be the same defect one layer down."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    path = append(log_line(4, "rule_edit", VIOLATION), "es-meddocan", "R", "sup-free")
    written = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert written["model_consulted"] == VIOLATION


def test_an_invented_self_report_value_is_refused():
    for bad in ("no", "none_at_all", "rule-content", "clean", True, 0):
        with pytest.raises(PortHumanError):
            log_line(1, "read_sample", bad)


def test_the_vocabulary_comes_from_naming_yaml_and_not_from_a_copy(monkeypatch):
    """A fifth value added to the axis has to reach this validation without an edit to
    the module — two copies of a vocabulary agree until the day they do not."""
    fake = dict(axis(CONSULTED_AXIS))
    fake["asked_a_colleague"] = "invented for this test"
    monkeypatch.setattr(human_arm, "axis",
                        lambda name: fake if name == CONSULTED_AXIS else axis(name))
    assert log_line(1, "decision", "asked_a_colleague")[
        "model_consulted"] == "asked_a_colleague"


def test_the_self_report_is_on_every_line_not_only_on_rule_edits():
    """The obligation is per event, so that it is in front of the author each time
    rather than once at the start of the run."""
    for event in EVENTS:
        assert log_line(1, event, "none")["model_consulted"] == "none"


def test_the_field_sits_before_the_window_hashes_in_the_record():
    """Judgement fields first, then the mechanically filled ones — the hashes are the
    two the caller never supplies, and keeping them last keeps that visible."""
    order = list(FIELDS)
    assert order.index("model_consulted") < order.index("prompt_sha256")
    assert order[-2:] == ["prompt_sha256", "sampling_sha256"]


# ─── appending ──────────────────────────────────────────────────────────────

def test_append_writes_one_json_line_and_keeps_the_previous_ones(tmp_path,
                                                                monkeypatch):
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    for i in (1, 2):
        path = append(log_line(i, "read_sample", "none"), "es-meddocan", "R",
                      "sup-free")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["iteration"] for x in lines] == [1, 2]


def test_an_unknown_path_component_is_refused(monkeypatch, tmp_path):
    """A typo mints a cell of the experiment instead of failing, and the aggregation
    that walks these directories would report it as a real one."""
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    for bad in (("es-meddocan", "rules-only", "sup-free"),
                ("es-meddocan", "R", "annotation-free"),
                ("es-carmen-typo", "R", "sup-free")):
        with pytest.raises(PortHumanError) as e:
            log_path(*bad)
        assert "naming.yaml" in str(e.value)


def test_the_log_path_carries_the_three_axes_and_the_arm(monkeypatch, tmp_path):
    monkeypatch.setattr(human_arm, "ROOT", tmp_path)
    path = log_path("es-meddocan", "R", "sup-free")
    assert path.relative_to(tmp_path).as_posix() == (
        "results/es-meddocan/R/sup-free/port-human/human_log.jsonl")
