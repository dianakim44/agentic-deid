"""The sealed scoring path, tested on dev — because on test it runs once.

`tests/test_seal.py` covers the gate (who may open the fold) and
`tests/test_seal_internals.py` the log's own functions. This file covers what happens
*between* them: which arm and which round may be opened (DESIGN §6.4), whether the
scoring path computes what `run_fold` computes, and where the result is written.

**The organising rule is that every refusal is upstream of the append.** A test that
finds a refusal firing after the log row exists has found a real defect, not a cosmetic
one: the row is the count the paper reports and it cannot be withdrawn. So the plan tests
below all assert against `plan_arm`, which reads committed dev artefacts only, and the
one test that reaches `load_sealed` goes through the synthetic loader
`tests/test_seal.py` built for that purpose.

Two rules from `test_seal.py` hold unchanged: **no test reads a real sealed fold**, and
**no test writes the real log**. A third is added here: **no test writes into the results
tree.** Everything that writes goes to `tmp_path`, because a test that wrote
`results/.../test/metrics.json` would leave a file the screener publishes and a reader
would take for a sealed evaluation that never happened.

    python3 -m pytest tests/test_sealed_scoring.py -q
"""
from __future__ import annotations

import inspect
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.corpora.base import SealError, axis  # noqa: E402
from src.eval import run_sealed_eval, sealed_log  # noqa: E402
from src.eval.run_fold import DEFAULT_SPLIT, run_fold  # noqa: E402
from src.eval.scorer import (  # noqa: E402
    ScorerError, arm_metrics_path, sealed_metrics_path,
)

# `temp_log` comes with `ARM`/`ROUND` rather than being redeclared: it is the fixture
# that keeps a test from appending to the real log, and a second copy of it is a second
# chance for one of them to point at the wrong file.
from test_seal import ARM, ROUND, a_plan, temp_log  # noqa: E402,F401

CORPUS = "es-meddocan"

#: The arm this file plans against, and the reason it is the real one. `port-loop` is the
#: only arm in the tree that has terminated with a reason and a round count, which is
#: exactly what DESIGN §6.4 requires before an opening — so a synthetic stand-in would be
#: testing the checks against a record built to pass them. The two arms that *cannot* be
#: opened are used for the refusals, also real: `port-oneshot` has no dev record at all
#: and `port-oneshot-nofence`'s predates the termination block.
TERMINATED = dict(detector="R", supervision="sup-free", porting="port-loop")
NO_RECORD = dict(detector="R", supervision="sup-free", porting="port-oneshot")
NO_TERMINATION = dict(detector="R", supervision="sup-free", porting="port-oneshot-nofence")


@pytest.fixture
def arm_record(terminated_arm_record):
    """The terminated arm's committed dev `metrics.json`, from `tests/conftest.py`.

    The availability check that answers this — the record's existence, resolved from the
    path — is in conftest and not here, which is `tests/test_conftest.py`'s rule 1: a
    fixture that skips is a fixture that can delete tests, and this one gates most of the
    file. It read `arm_metrics_path(...).exists()` locally until 2026-08-26, and the local
    version was caught by that rule rather than by review.

    A one-line alias rather than a rename at 30 call sites, and it earns its place: the
    name says *which* record, and `test_the_fixture_names_the_arm_this_file_plans_against`
    ties conftest's coordinate to this file's `TERMINATED`.
    """
    return terminated_arm_record


@pytest.fixture
def plan(arm_record):
    """A real plan for the terminated arm at its real final round."""
    return run_sealed_eval.plan_arm(
        corpus=CORPUS, **TERMINATED, iteration=arm_record["termination"]["iterations"]
    )


# ─── which round: DESIGN §6.4 ───────────────────────────────────────────────


def test_the_final_round_plans(plan, arm_record):
    """The permitted case, so the refusals below are not vacuously green."""
    assert plan.iteration == arm_record["termination"]["iterations"]
    assert plan.arm.cell == "R/sup-free/port-loop"


def test_the_fixture_names_the_arm_this_file_plans_against(arm_record):
    """`conftest.TERMINATED_ARM` and this file's `TERMINATED` are one coordinate.

    The record arrives from conftest and the plans are built from `TERMINATED` here, so
    nothing but this assertion stops the two from naming different arms — after which every
    refusal below would be checked against a record belonging to some other arm and would
    fire for the wrong reason. Asserted through the record's own `run` block, so it is the
    file on disk that adjudicates rather than a third copy of the literal.
    """
    assert {k: arm_record["run"][k] for k in TERMINATED} == TERMINATED


@pytest.mark.parametrize("offset", [-1, -3, 1])
def test_a_round_that_is_not_the_final_round_is_refused(arm_record, offset):
    """§6.4's substance. Both directions, and not only "one less".

    An earlier round is the case the protocol is actually about — dev-best is an earlier
    round — and a *later* one is refused too, because a round the arm never ran would
    otherwise be planned against rule files that happen to exist from somewhere else.

    The message must name the cost argument, which is the reason for the rule: a
    round-5-of-8 headline is five rounds of quality beside eight rounds of spend, and no
    run exists that both cost that and scored that. A refusal that said only "not the
    final round" would be re-litigated the first time someone wanted the better number.
    """
    final = arm_record["termination"]["iterations"]
    requested = final + offset
    if requested < 1:
        pytest.skip("not a round")
    with pytest.raises(SealError, match="not this arm's final round"):
        run_sealed_eval.plan_arm(
            corpus=CORPUS, **TERMINATED, iteration=requested
        )


def test_the_refusal_names_the_cost_argument(arm_record):
    """The grounds travel with the refusal, not only with DESIGN §6.4."""
    final = arm_record["termination"]["iterations"]
    with pytest.raises(SealError) as exc:
        run_sealed_eval.plan_arm(corpus=CORPUS, **TERMINATED, iteration=1)
    message = str(exc.value)
    assert "cost_to_date" in message
    assert f"{final} rounds of spend" in message


@pytest.mark.parametrize("bad", [0, -1, 1.0, True, "8", None])
def test_a_round_that_is_not_a_round_is_refused(bad):
    """Rounds are 1-indexed positive integers. `True` is refused explicitly.

    `isinstance(True, int)` is true in Python, so a bool would otherwise plan as round 1 —
    which is the kind of accident that gets a *different* round scored than the one
    anybody named.
    """
    with pytest.raises(SealError, match="positive integer"):
        run_sealed_eval.plan_arm(corpus=CORPUS, **TERMINATED, iteration=bad)


def test_an_arm_that_has_not_terminated_is_refused(tmp_path, arm_record):
    """`reason: null` is the record of a running arm (src/termination.py).

    Built by copying the real record and nulling one field, so what is under test is the
    check and not a hand-built file that differs in other ways too.
    """
    record = json.loads(json.dumps(arm_record))
    record["termination"]["reason"] = None
    _plant(tmp_path, record, **TERMINATED)
    with pytest.raises(SealError, match="has not stopped"):
        run_sealed_eval.plan_arm(
            corpus=CORPUS, **TERMINATED,
            iteration=record["termination"]["iterations"], root=tmp_path,
        )


def test_an_arm_with_no_termination_block_is_refused():
    """The real `port-oneshot-nofence` case: a record that predates schema 6.

    Refused rather than defaulted to round 1. A default would be this module deciding the
    arm's final round, and the whole point of §6.4 is that the arm's own committed record
    decides it.
    """
    if not arm_metrics_path(corpus=CORPUS, **NO_TERMINATION).exists():
        pytest.skip("the nofence arm's record is not on this machine")
    with pytest.raises(SealError, match="no termination block"):
        run_sealed_eval.plan_arm(corpus=CORPUS, **NO_TERMINATION, iteration=1)


def test_an_arm_with_no_dev_record_is_refused():
    """A failed arm has no final round, so it has no opening (DESIGN §6.4).

    `port-oneshot` died of format failure and wrote `format_failure.json` and no metrics.
    Its result is the failure, reported with its cost; the seal is not involved.
    """
    assert not arm_metrics_path(corpus=CORPUS, **NO_RECORD).exists(), (
        "this test is about an arm with no dev record; if one appeared, the arm was "
        "re-run and the test needs re-pointing rather than deleting"
    )
    with pytest.raises(SealError, match="no committed dev record"):
        run_sealed_eval.plan_arm(corpus=CORPUS, **NO_RECORD, iteration=1)


def test_an_undeclared_axis_is_refused():
    """CLAUDE.md's naming rule, at the one row that is appended and never corrected."""
    with pytest.raises(SealError, match="not a value of the 'porting' axis"):
        run_sealed_eval.plan_arm(
            corpus=CORPUS, detector="R", supervision="sup-free",
            porting="port-does-not-exist", iteration=1,
        )


def test_a_test_split_in_the_dev_record_is_refused(tmp_path, arm_record):
    """A `paths.metrics` file saying `test` means a sealed score overwrote the headline.

    The state `config/naming.yaml`'s `sealedmetrics` key exists to prevent. If it is ever
    reached, the arm's dev number is gone and nothing downstream can tell.
    """
    record = json.loads(json.dumps(arm_record))
    record["run"]["split"] = "test"
    _plant(tmp_path, record, **TERMINATED)
    with pytest.raises(SealError, match="records split='test'"):
        run_sealed_eval.plan_arm(
            corpus=CORPUS, **TERMINATED,
            iteration=record["termination"]["iterations"], root=tmp_path,
        )


# ─── which rule files ───────────────────────────────────────────────────────


def test_the_rule_files_come_from_the_committed_record(plan, arm_record):
    """Read from `rules_source`, never reconstructed from the axes (DESIGN §5.3).

    A reconstruction can agree with the path template and disagree with what actually
    ran — and on test there is no second run to notice.
    """
    assert set(plan.rules) == set(arm_record["run"]["rules_source"])
    for lang, rel in arm_record["run"]["rules_source"].items():
        assert plan.rules[lang] == run_sealed_eval.base.ROOT / rel
        assert plan.rules[lang].exists()


def test_a_missing_rule_file_is_refused(tmp_path, arm_record):
    """No substitute — not the bootstrap file, not an adjacent round."""
    record = json.loads(json.dumps(arm_record))
    record["run"]["rules_source"] = {"es": "rules/no-such-file.yaml"}
    _plant(tmp_path, record, **TERMINATED)
    with pytest.raises(SealError, match="not on disk"):
        run_sealed_eval.plan_arm(
            corpus=CORPUS, **TERMINATED,
            iteration=record["termination"]["iterations"], root=tmp_path,
        )


def test_a_record_naming_no_rules_source_is_refused(tmp_path, arm_record):
    record = json.loads(json.dumps(arm_record))
    record["run"]["rules_source"] = {}
    _plant(tmp_path, record, **TERMINATED)
    with pytest.raises(SealError, match="names no rules_source"):
        run_sealed_eval.plan_arm(
            corpus=CORPUS, **TERMINATED,
            iteration=record["termination"]["iterations"], root=tmp_path,
        )


# ─── the scoring path reproduces run_fold ───────────────────────────────────


def test_the_scoring_path_reproduces_run_fold_on_dev(corpus_present, plan, tmp_path):
    """The headline test of this file, and the reason the sealed run is defensible.

    `run_fold` is run into `tmp_path` — never the results tree — over the dev fold with
    the plan's own rule files, and its `scored` is compared against what the sealed
    module's `score_fold` produces from the same fold. Agreement means the two paths
    differ in which documents they are handed and in nothing else, which is the only form
    the claim can take before the irreversible run rather than after it.

    Compared key by key over everything `score()` returns, not on a hand-listed subset:
    the first version of `--verify-dev` listed five keys of which three were nested one
    level down, so it compared two and reported five. See
    `run_sealed_eval.UNCOMPARED_KEYS`.
    """
    docs = run_sealed_eval.load_fold(CORPUS, DEFAULT_SPLIT)
    mine, _ = run_sealed_eval.score_fold(plan, docs, split=DEFAULT_SPLIT)
    _, _, theirs = run_fold(
        corpus=CORPUS, **TERMINATED, split=DEFAULT_SPLIT,
        rules=plan.rules, root=tmp_path,
    )
    for key in run_sealed_eval.compared_keys(mine):
        assert mine[key] == theirs[key], f"{key} differs from run_fold's"


def test_verify_dev_agrees_with_the_committed_record(corpus_present, plan):
    """The CLI's own check, against the file `run_fold` actually wrote.

    Distinct from the test above, which re-runs `run_fold` now. This one compares against
    the record that was committed when the arm closed, so it also catches a rule file
    edited since — which is what makes it the pre-flight check
    (`docs/notes/sealed-eval-preflight.md`) and not only a unit test.
    """
    agrees, compared, differing = run_sealed_eval.verify_dev(plan)
    assert agrees, f"{differing} differ from the arm's committed metrics.json"
    assert compared, "a comparison over no keys is not a comparison"


def test_verify_dev_compares_everything_score_produces(plan, monkeypatch):
    """A key the record lacks counts as differing, rather than being skipped.

    The failure mode a narrowing edit produces is silence: fewer keys compared, the same
    "reproduces" line printed. So the set is derived from `score()`'s output and this test
    checks the derivation rather than a list.
    """
    from src.eval import scorer

    keys = set(scorer.score([])) - set(run_sealed_eval.UNCOMPARED_KEYS)
    assert set(run_sealed_eval.compared_keys(scorer.score([]))) == keys
    assert "headline" in keys and "counts" in keys
    assert "scorer_version" not in keys, (
        "the scorer's version is folded into the run block, so the record has nowhere "
        "for it to be compared against"
    )


def test_verify_dev_writes_nothing_and_opens_nothing(corpus_present, plan, tmp_path):
    """It is the rehearsal. A rehearsal that consumed an opening would be the run.

    **Asserted as "unchanged", not as "absent"** (2026-08-29). This test required the
    sealed metrics file not to exist, which was true until an arm was genuinely evaluated
    and then false forever — it went red on 2026-08-28 with nothing wrong. Absence was
    never the property anyway: what the rehearsal must not do is *write*, and a run that
    overwrote the one irreversible record with a dev score would satisfy the old assertion
    on the way past. Comparing the bytes catches that and keeps working after the file
    exists.
    """
    sealed = sealed_metrics_path(corpus=CORPUS, **TERMINATED)
    log_before = sealed_log.LOG.read_text(encoding="utf-8")
    record_before = sealed.read_bytes() if sealed.exists() else None

    run_sealed_eval.verify_dev(plan)

    assert sealed_log.LOG.read_text(encoding="utf-8") == log_before
    after = sealed.read_bytes() if sealed.exists() else None
    assert after == record_before, (
        "a dev verification changed the sealed metrics file — created it, or overwrote the "
        "record of an opening that cannot be repeated"
    )


def test_the_sealed_path_and_run_fold_resolve_the_same_loader(corpus_present):
    """One registry answers "which loader reads this corpus", for both paths.

    `_loader_for` named `MeddocanLoader` itself until 2026-08-28 and agreed with
    `base._loaders()` because both held one entry. What the duplication permitted has no
    symptom until a second loader exists: `verify_dev` scores dev through `run_fold`, which
    goes through the registry, so a sealed run resolving its own loader would have been
    rehearsed against a different reader of the same corpus. Asserted as identity of the
    class rather than by behaviour, because the behaviours are equal today — that is
    exactly why the divergence would be quiet.
    """
    registry = run_sealed_eval.base._loaders()
    assert type(run_sealed_eval._loader_for(CORPUS)) is registry[CORPUS]
    with pytest.raises(run_sealed_eval.CorpusError, match="no loader yet"):
        run_sealed_eval._loader_for("de-grascco")


def test_the_documented_entry_point_puts_the_real_module_on_the_stack(monkeypatch):
    """`python -m src.eval.run_sealed_eval` must satisfy the loader's identity check.

    The check walks the frames' `__name__` looking for `base.SEALED_CALLER`. Under
    `python -m` the file executes as `__main__`, so until 2026-08-28 the module's own
    documented invocation put no frame carrying the real name on the stack and the seal
    gate refused it — the refusal is upstream of the log append, so the first attempt to
    open a fold opened nothing, but it could not open one either. The `__main__` block now
    imports the module and calls *that* copy's `main`, whose frame carries the real name.

    Nothing caught this before because every test imports the module, which makes
    `__name__` right for free, and `--verify-dev` never reaches `load_sealed`. So the
    entry point is exercised the way the shell exercises it: `runpy.run_module` with
    `run_name="__main__"` is what `-m` does.

    The assertion is on the stack at the point `main` starts real work, which is the whole
    content of the fix. `plan_arm` stands in for that point because it is `main`'s first
    call and needs no corpus; the recorder's own frame belongs to this test module, and
    what is being asserted is the frame *below* it.
    """
    import runpy

    seen: dict[str, set[str]] = {}

    def recorder(**kwargs):
        frame = inspect.currentframe()
        names = set()
        try:
            while frame is not None:
                names.add(frame.f_globals.get("__name__", ""))
                frame = frame.f_back
        finally:
            del frame
        seen["names"] = names
        raise SystemExit(0)

    monkeypatch.setattr(run_sealed_eval, "plan_arm", recorder)
    monkeypatch.setattr(
        sys, "argv",
        ["run_sealed_eval.py", "--corpus", CORPUS, "--arm", ARM.cell,
         "--iteration", str(ROUND), "--purpose", "entry point test"],
    )

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("src.eval.run_sealed_eval", run_name="__main__")

    assert exc.value.code == 0, "the recorder should have been reached"
    assert run_sealed_eval.base.SEALED_CALLER in seen["names"], (
        "the documented entry point must put the real module on the call stack, or the "
        "loader's seal gate refuses the only invocation permitted to open the fold"
    )


# ─── the two spellings of the arm ───────────────────────────────────────────


def test_the_arm_cell_round_trips_through_the_flag(plan):
    """`--arm` takes exactly what the log's arm column prints.

    That is the whole reason the compact form exists: a cell read off a committed row, or
    out of an arm's `metrics.json`, is passed back without transcribing three values. A
    parser that split on some other character would still work on hand-typed input and
    would lose that property silently, so the round trip is what is asserted.
    """
    assert run_sealed_eval.axes_from_arm(plan.arm.cell) == TERMINATED
    assert set(run_sealed_eval.ARM_AXES) == set(TERMINATED)


def test_both_spellings_plan_the_same_arm(arm_record):
    """Not two ways in — one, reached by two spellings.

    Compared as plans rather than as parsed axes: the plan is what every step downstream
    reads, so a spelling that resolved correctly and then planned differently would pass a
    check on `resolve_axes` alone.
    """
    round_ = arm_record["termination"]["iterations"]
    by_flags = run_sealed_eval.plan_arm(corpus=CORPUS, iteration=round_, **TERMINATED)
    by_cell = run_sealed_eval.plan_arm(
        corpus=CORPUS, iteration=round_,
        **run_sealed_eval.resolve_axes(
            arm=by_flags.arm.cell, detector=None, supervision=None, porting=None
        ),
    )
    assert by_cell == by_flags


@pytest.mark.parametrize(
    "cell",
    ["R/sup-free", "R/sup-free/port-loop/extra", "R//port-loop", "",
     "R sup-free port-loop", "/R/sup-free"],
)
def test_a_malformed_arm_cell_is_refused(cell):
    with pytest.raises(SealError, match="is not an arm cell"):
        run_sealed_eval.axes_from_arm(cell)


def test_the_two_spellings_may_not_be_mixed():
    """Because a disagreement between them has no right answer.

    Merging would settle `--arm A/b/c --porting d` by whichever the code read second, and
    the result is a fold opened on an arm nobody typed.
    """
    with pytest.raises(SealError, match="together with"):
        run_sealed_eval.resolve_axes(
            arm="R/sup-free/port-loop", detector=None, supervision=None,
            porting="port-oneshot",
        )


def test_an_incompletely_specified_arm_is_refused():
    """Two of the three axis flags is not two thirds of an arm."""
    with pytest.raises(SealError, match="incompletely specified"):
        run_sealed_eval.resolve_axes(
            arm=None, detector="R", supervision="sup-free", porting=None
        )
    with pytest.raises(SealError, match="incompletely specified"):
        run_sealed_eval.resolve_axes(
            arm=None, detector=None, supervision=None, porting=None
        )


def test_the_cell_form_is_not_a_way_past_the_vocabulary():
    """`axes_from_arm` checks the shape and deliberately nothing else.

    The values are checked where they were always checked — `sealed_log.Arm`, against
    `naming.yaml`, inside `plan_arm`. A second check inside the parser would be a second
    place that knows the vocabulary, which is what CLAUDE.md's naming rule forbids; what
    must hold instead is that the new spelling reaches the existing one.
    """
    with pytest.raises(SealError):
        run_sealed_eval.plan_arm(
            corpus=CORPUS, iteration=1,
            **run_sealed_eval.axes_from_arm("R/sup-free/port-invented"),
        )


# ─── where the sealed record is written ─────────────────────────────────────


def test_the_sealed_path_is_not_the_arms_dev_path():
    """Two files, and the dev one is what the test score is compared against.

    If these were one path, the last act of the one irreversible run in this project
    would be to destroy the number it is reported beside.
    """
    dev = arm_metrics_path(corpus=CORPUS, **TERMINATED)
    sealed = sealed_metrics_path(corpus=CORPUS, **TERMINATED)
    assert dev != sealed
    assert sealed.parent.name == "test"
    assert sealed.parent.parent == dev.parent


def test_the_sealed_path_carries_no_round_component():
    """§6.4 permits one round per arm, so a round component asserts a forbidden state."""
    sealed = sealed_metrics_path(corpus=CORPUS, **TERMINATED)
    assert "iter" not in str(sealed.relative_to(run_sealed_eval.base.ROOT))


def test_write_metrics_refuses_a_test_split_without_the_flag(tmp_path, arm_record):
    """Otherwise the test score lands on `paths.metrics` and overwrites the headline."""
    run = {**arm_record["run"], "split": "test"}
    with pytest.raises(ScorerError, match="require each other"):
        _write(tmp_path, arm_record, run=run, sealed=False)


def test_write_metrics_refuses_the_flag_without_a_test_split(tmp_path, arm_record):
    """The other direction: a dev score filed under `test/` contradicts its own block."""
    with pytest.raises(ScorerError, match="require each other"):
        _write(tmp_path, arm_record, run=dict(arm_record["run"]), sealed=True)


def test_write_metrics_refuses_sealed_together_with_a_round(tmp_path, arm_record):
    """There is no second round on test for a round component to tell apart."""
    run = {**arm_record["run"], "split": "test"}
    with pytest.raises(ScorerError, match="one opening per arm"):
        _write(tmp_path, arm_record, run=run, sealed=True, iteration=1)


def test_the_sealed_record_differs_from_the_dev_one_in_split_and_scores_only(
    tmp_path, arm_record
):
    """Cost, termination and the rule provenance travel verbatim (`COPIED_BLOCKS`).

    Written with the *dev* scores on purpose: this test is about which fields the writer
    changes, so holding the scores equal isolates them. The four that differ are
    `run_sealed_eval.FRESH_RUN_FIELDS`, and `scorer_version`/`generated` are excluded
    because the writer stamps them.
    """
    run = {**arm_record["run"], "split": "test"}
    path = _write(tmp_path, arm_record, run=run, sealed=True)
    written = json.loads(path.read_text(encoding="utf-8"))
    for block in run_sealed_eval.COPIED_BLOCKS:
        if block in arm_record:
            assert written[block] == arm_record[block], f"{block} was not copied verbatim"
    differing = {
        key for key in set(written["run"]) | set(arm_record["run"])
        if written["run"].get(key) != arm_record["run"].get(key)
    }
    assert differing <= set(run_sealed_eval.FRESH_RUN_FIELDS) | {"scorer_version"}, (
        f"{sorted(differing)} differ; only FRESH_RUN_FIELDS may"
    )


def test_the_recorded_tree_state_is_the_one_the_log_row_saw(
    tmp_path, arm_record, plan, monkeypatch
):
    """`run.tree` must be sampled before the run writes anything. Its first run was not.

    The 2026-08-28 opening of `es-meddocan/test` — the only one this arm gets — recorded
    `tree: dirty` in `metrics.json` while its log row recorded `clean`, for one run on a
    tree that `git status --porcelain` had just reported empty. `sealed_run_block` sampled
    the state itself, which put the sample *after* `load_sealed` had appended the row (a
    tracked file, modified) and after the output directory existed (untracked). The run
    read its own footprints as contamination. `record_access` samples before it writes,
    which is why the row is the one that is right.

    `tree` means "the commit hash does not describe the code that ran", and a run's own
    outputs cannot make that true of the run producing them. So the property is about
    *when*, and the fake below is a clock: `tree_state` reports `clean` until the load
    happens and `dirty` from then on, which is what the real repository did. A sample
    taken at the wrong moment therefore reaches the record as `dirty` and this test fails;
    the sample taken where `record_access` takes its own reaches it as `clean`.

    Everything downstream of the load is stubbed because none of it is the subject — the
    scoring is `test_dev_scoring_reproduces_the_arms_committed_metrics`'s job, and the
    writer's is the two tests above. What is *not* stubbed is `evaluate`, because the
    ordering being asserted is entirely a fact about the order of its statements.
    """
    commit = "b" * 40
    dirty_from_now_on = {"tree": "clean"}
    samples: list[str] = []

    def fake_tree_state():
        samples.append(dirty_from_now_on["tree"])
        return commit, dirty_from_now_on["tree"]

    def fake_load_sealed(_plan, *, purpose, allow_dirty=False, observed=None):
        # Standing in for the append and the mkdir, in the one respect that matters here.
        dirty_from_now_on["tree"] = "dirty"
        return []

    scored = {
        k: v for k, v in arm_record.items()
        if k not in ("run", "schema_version", "headline_mode", *run_sealed_eval.COPIED_BLOCKS)
    }
    monkeypatch.setattr(sealed_log, "tree_state", fake_tree_state)
    monkeypatch.setattr(run_sealed_eval, "load_sealed", fake_load_sealed)
    monkeypatch.setattr(
        run_sealed_eval, "score_fold", lambda _p, _docs, split: (scored, 0.0)
    )

    path, _ = run_sealed_eval.evaluate(plan, purpose="tree sampling order", root=tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))

    assert written["run"]["tree"] == "clean", (
        "the record reports a tree state observed after the run had already written to the "
        "tree, so it describes the run's own output and not the code that ran"
    )
    assert written["run"]["commit"] == commit
    assert samples and samples[0] == "clean", (
        "nothing sampled the tree before the load; the first observation of the run must "
        "be the one the record and the log row share"
    )
    assert dirty_from_now_on["tree"] == "dirty", (
        "the fake never went dirty, so this test would pass on the defect it is for"
    )


def test_the_tree_state_is_not_sampled_where_it_was_sampled_wrongly(plan):
    """`sealed_run_block` takes the state and has no way to obtain one.

    The above fails if the sampling moves back; this fails if a default is added, which is
    the same defect arriving by omission rather than by edit. A keyword with a default
    would let a future caller reintroduce the late sample without touching `evaluate`, and
    the symptom would again be a provenance field that is wrong in the safe-looking
    direction on a run that cannot be repeated.
    """
    observed = inspect.signature(run_sealed_eval.sealed_run_block).parameters["observed"]
    assert observed.default is inspect.Parameter.empty
    assert observed.kind is inspect.Parameter.KEYWORD_ONLY
    block = run_sealed_eval.sealed_run_block(plan, observed=("c" * 40, "clean"))
    assert (block["commit"], block["tree"]) == ("c" * 40, "clean")


def test_a_sealed_run_writes_no_spans_and_no_errors():
    """Checked against the module's imports, which is where it would come back.

    `write_spans`' consumers are the masker and the loop driver and a sealed fold has no
    next round; `errors.jsonl` is a map of the residual identifiers in the test fold and
    the input to the practice CLAUDE.md forbids. Not writing the file is stronger than
    denying it.
    """
    source = (run_sealed_eval.base.ROOT / "src/eval/run_sealed_eval.py").read_text(
        encoding="utf-8")
    for forbidden in ("write_spans", "write_errors", "error_spans"):
        assert f"{forbidden}(" not in source, (
            f"{forbidden} is reachable from the sealed driver"
        )


# ─── the log row's arm and round ────────────────────────────────────────────


def test_the_arm_cell_cannot_be_a_string(temp_log):
    """The defect this closed: the cell was the literal `none (access check)`.

    A string is refused rather than published, so a hardcoded stand-in fails at the
    append instead of appearing in the row that carries the only test score.
    """
    with pytest.raises(SealError, match="takes an `Arm`"):
        sealed_log.record_access(
            CORPUS, arm="none (access check)", iteration=ROUND, purpose="a string arm"
        )


@pytest.mark.parametrize("bad", [0, -1, "8", None, 1.5, True])
def test_the_round_cell_must_be_a_round(temp_log, bad):
    with pytest.raises(SealError, match="positive integer"):
        sealed_log.record_access(
            CORPUS, arm=ARM, iteration=bad, purpose="not a round"
        )


def test_the_row_carries_the_arm_and_the_round(temp_log):
    row = sealed_log.record_access(
        CORPUS, arm=ARM, iteration=ROUND, purpose="arm and round"
    )
    cells = [c.strip() for c in row.split("|")[1:-1]]
    assert cells[6] == ARM.cell
    assert cells[7] == str(ROUND)
    assert cells[4] == CORPUS, "the corpus column did not move; count_runs keys on it"


def test_count_runs_still_keys_on_the_corpus_column(temp_log):
    """The `round` column went in at cell 7 so this index did not move.

    `+ 1` rather than `== 1`: `temp_log` copies the real log, so an absolute count here
    asserts that no fold has ever been opened. That became false on 2026-08-28. The
    zero for the unopened corpus stays absolute, because that is the claim being made —
    rows are attributed to the corpus in their own column and not to every corpus.
    """
    before = sealed_log.count_runs(CORPUS)
    sealed_log.record_access(CORPUS, arm=ARM, iteration=ROUND, purpose="one")
    assert sealed_log.count_runs(CORPUS) == before + 1
    assert sealed_log.count_runs("de-grascco") == 0


def test_the_committed_log_has_nine_columns():
    """The header and the placeholder row must match what `record_access` writes."""
    lines = [
        line.strip() for line in sealed_log.LOG.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("|")
    ]
    for line in lines:
        assert len(line.split("|")[1:-1]) == 9, (
            "a row with a different width shifts every column silently"
        )


def test_no_step_in_the_chain_defaults_the_arm_or_the_round(temp_log):
    """The signatures are the control, at all three layers.

    `load_sealed` takes a plan, `base.load` threads `arm`/`iteration`, `_authorise_sealed`
    passes them on, and `record_access` requires both. A default anywhere in that chain is
    a value somebody defaults — which is how the arm cell came to be the literal
    `none (access check)` for the whole life of the log. Checked by inspection rather than
    by behaviour because the behaviour under test is "nobody can omit it", and an omission
    has no call to make.

    **`_authorise_sealed` was the gap, and it stayed open after `record_access` closed**
    (fixed 2026-08-28). It defaulted both to `None`, so the refusal was real but lived one
    call further down: the signature said the fields were optional at the step that decides
    whether to open the fold. `base.load` keeps its defaults and must — every ordinary load
    omits all three — so the guarantee there is a refusal instead, which
    `tests/test_seal.py` covers.
    """
    import inspect

    from src.corpora.base import CorpusLoader

    assert list(inspect.signature(run_sealed_eval.load_sealed).parameters)[0] == "plan"
    authorise = inspect.signature(CorpusLoader._authorise_sealed).parameters
    for name in ("arm", "iteration"):
        appended = inspect.signature(sealed_log.record_access).parameters[name]
        assert appended.default is inspect.Parameter.empty, (
            f"record_access defaults {name}; the row is appended once and never corrected"
        )
        assert name in inspect.signature(CorpusLoader.load).parameters, (
            f"the loader does not thread {name} to the append"
        )
        assert authorise[name].default is inspect.Parameter.empty, (
            f"_authorise_sealed defaults {name}, so an opening with no {name} is "
            "expressible at the step that authorises the read"
        )
        assert authorise[name].kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{name} is positional in _authorise_sealed, so two of the three could be "
            "swapped at a call site and the log row would name the wrong thing"
        )


def test_a_plan_shaped_wrongly_never_reaches_the_append(temp_log, monkeypatch):
    """And if one does get past the signature, no row is written.

    `load_sealed`'s first argument is positional, so a corpus id can be passed where a
    plan belongs. What must not happen is a row: the failure has to come before the
    append, not from a half-written log.
    """
    monkeypatch.setattr(sealed_log, "tree_state", lambda: ("a" * 40, "clean"))
    monkeypatch.setattr(
        run_sealed_eval, "_verify_frozen_split", lambda loader, corpus_id: None
    )
    before = temp_log.read_text(encoding="utf-8")
    with pytest.raises((AttributeError, SealError)):
        run_sealed_eval.load_sealed(CORPUS, purpose="a corpus id where a plan goes")
    assert temp_log.read_text(encoding="utf-8") == before


def test_the_sealed_split_is_a_naming_axis_value():
    assert run_sealed_eval.SEALED_SPLIT in axis("split")
    assert run_sealed_eval.SEALED_SPLIT != DEFAULT_SPLIT


# ─── helpers ────────────────────────────────────────────────────────────────


def _plant(root, record, *, detector, supervision, porting):
    """Write `record` as an arm's dev metrics under `root`, for the planner to read."""
    path = arm_metrics_path(
        corpus=CORPUS, detector=detector, supervision=supervision, porting=porting,
        root=root,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _write(root, arm_record, *, run, sealed, iteration=None):
    """`write_metrics` with the arm's own blocks, into `root`."""
    from src.eval.scorer import write_metrics

    blocks = {
        k: arm_record[k] for k in run_sealed_eval.COPIED_BLOCKS if k in arm_record
    }
    scored = {
        k: v for k, v in arm_record.items()
        if k not in ("run", "schema_version", "headline_mode", *blocks)
    }
    return write_metrics(
        scored, run=run, sealed=sealed, iteration=iteration, root=root, **blocks
    )
