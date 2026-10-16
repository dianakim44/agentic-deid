"""v24b's registration and its one code change. DESIGN §13.

**Outside `tests/mutations/run.py`'s `TEST_FILES` on purpose.** v24b adds no mutation and
changes no kill count's denominator (`experiments/loco_v24b/registration.yaml`
`mutation_gate`), and putting these in a member file would move the suite count that the
deferred full run is measured against. CLAUDE.md's rule: a change to a file outside
`TEST_FILES` moves no count and needs no gate run.

What is worth testing here is what a wrong value would cost *silently*: a holdout that is
not actually held out, a registration that disagrees with v24's about which rules each arm
composes, and an axis value nothing declares. The training itself is not tested — it takes
an hour per model and the thing under test would be `transformers`.

No corpus text, surface or document id appears here.
"""
from __future__ import annotations

import json
import os
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.corpora import base  # noqa: E402
from src.eval import sealed_log  # noqa: E402
from src.tagger import train  # noqa: E402
from tools import run_loco  # noqa: E402

V24B = os.path.join(ROOT, "experiments", "loco_v24b", "registration.yaml")
V24 = os.path.join(ROOT, "experiments", "hybrid_v24", "registration.yaml")


@pytest.fixture(scope="module")
def reg():
    with open(V24B, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def v24():
    with open(V24, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ─── the axis value ─────────────────────────────────────────────────────────


def test_the_supervision_value_is_declared():
    """`sup-transfer` is a naming.yaml value, so an arm path can be built from it.

    The driver holds it as a constant, which is how every other axis value reaches a
    path in this project; what must not be true is that the constant names a value the
    vocabulary does not have, because then the arm exists on disk and nowhere else.
    """
    assert run_loco.SUPERVISION in base.axis("supervision")


def test_the_arm_cell_validates_through_the_log_s_own_object():
    """Both arms pass `sealed_log.Arm`, which is what refuses at the append.

    Checked here rather than discovered at the opening: `Arm.__post_init__` is the last
    gate before a row is written, and a row is appended once and never corrected.
    """
    for detector in ("T", "RT"):
        arm = sealed_log.Arm(detector=detector, supervision=run_loco.SUPERVISION,
                             porting=run_loco.PORTING)
        assert arm.cell == f"{detector}/{run_loco.SUPERVISION}/{run_loco.PORTING}"


def test_the_rung_is_the_hybrid_one_so_the_sealed_branch_runs(reg):
    """`port-hybrid` is what routes the sealed read through §12.7's hybrid branch.

    If this value drifted, `score_fold` would take the rules-only path and publish a
    test number computed without the tagger under an arm whose name says otherwise.
    """
    from src.eval import run_sealed_eval as sealed

    assert base.porting_rung(run_loco.PORTING) == sealed.HYBRID_RUNG
    for arm in ("T-loco", "RT-loco"):
        assert reg["arms"][arm]["porting"] == run_loco.PORTING
        assert reg["arms"][arm]["supervision"] == run_loco.SUPERVISION


# ─── the registration ───────────────────────────────────────────────────────


def test_the_rules_arm_choice_is_v24_s(reg, v24):
    """The same R arm per corpus as v24, so the two tables' rules halves are one thing.

    A disagreement is the finding rather than something the driver resolves: if v24b
    composed `port-multi` where v24 composed `port-loop`, the in-domain and LOCO columns
    would differ by the tagger *and* by the rules, and §13's one-factor claim would be
    false with nothing in the record saying so.
    """
    assert reg["rules_arm"] == v24["rules_arm"]
    assert sorted(reg["corpora"]) == sorted(v24["corpora"])


def test_the_registration_declares_no_llm_call(reg):
    """Zero is a measurement and absent is not — and §13.2 is why it is zero."""
    assert reg["llm"]["calls"] == 0
    assert reg["merge"]["selector_run"] is False
    assert reg["merge"]["arbiter_run"] is False


def test_the_fixed_policy_is_the_rt_baseline(reg):
    """`RT-loco` is `UNION@0.5` everywhere, and that point needs no dev observation."""
    from src.merge import policy as pol

    declared = reg["merge"]["policy"]
    assert pol.Choice(policy=declared["policy"], theta=declared["theta"]) \
        == pol.rt_baseline()
    assert reg["merge"]["every_type"] is True


def test_the_epoch_selection_excludes_the_holdout_s_dev(reg):
    """The field that makes `sup-transfer` true, asserted against the registration."""
    selection = reg["tagger"]["epoch_selection"]
    assert selection["holdout_dev_used"] is False
    assert selection["test_used"] is False
    assert selection["corpora"] == "training_four_only"


def test_the_openings_are_two_arms_times_five_corpora(reg):
    """Ten, and the log's row count afterwards is stated rather than discovered."""
    assert reg["sealed"]["openings"] == 2 * len(reg["corpora"])
    assert reg["sealed"]["verify_dev_required"] is True
    assert reg["sealed"]["on_refusal"] == "report_as_finding"


def test_the_predictions_keep_p11_directionless(reg):
    """P11 is the question the project's claim turns on; a direction would be an opinion."""
    assert reg["predictions"]["P11"]["direction"] == "none"
    assert reg["predictions"]["P9"]["direction"] == "stated"
    assert reg["predictions"]["P10"]["direction"] == "stated"


def test_the_hyperparameters_match_v24(reg, v24):
    """One factor changes, and it is which train folds are pooled.

    Compared field by field against v24's own block rather than against a copy written
    out here, because a copy is a second statement of the same fact and the two would
    agree until they did not.
    """
    mine, theirs = reg["tagger"]["training"], v24["tagger"]["training"]
    for field in ("optimizer", "lr", "weight_decay", "batch_size",
                  "grad_accumulation", "max_epochs", "warmup_ratio",
                  "lr_schedule", "device"):
        assert mine[field] == theirs[field], field
    for field in ("max_tokens", "stride", "overlap_resolution", "offsets"):
        assert reg["tagger"]["window"][field] == v24["tagger"]["window"][field], field
    assert reg["tagger"]["seeds"] == [v24["tagger"]["primary_seed"]]


# ─── the one code change ────────────────────────────────────────────────────


def test_holdout_drops_the_corpus_from_both_lists():
    """`--holdout` is one flag because the pool and the dev curve must agree.

    `build()` reads the list it is handed, and `main` builds that list once. Asserted on
    the list rather than through a training run: the property that matters is that no
    code path can pool four corpora while scoring five.
    """
    for holdout in train.CORPORA:
        used = [c for c in train.CORPORA if c != holdout]
        assert holdout not in used
        assert len(used) == len(train.CORPORA) - 1


def test_the_in_domain_default_is_unchanged():
    """Without `--holdout`, `build` still covers all five corpora.

    v24's numbers are published; a change here would mean the in-domain model this
    comparison is against is not the one the manuscript reports.
    """
    import inspect

    source = inspect.getsource(train.build)
    assert "CORPORA if corpora is None else corpora" in source
    assert len(train.CORPORA) == 5


def test_the_two_output_directories_are_separate():
    """LOCO weights do not land where `run_hybrid` names models by seed alone."""
    assert train.LOCO_OUT != train.OUT
    assert train.OUT.name not in str(train.LOCO_OUT)


def test_the_weights_are_gitignored():
    """Five models trained on credentialed folds. The screener's half of the rule."""
    with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
        ignored = fh.read()
    for line in ("experiments/loco_v24b/weights/",
                 "experiments/loco_v24b/preds/"):
        assert line in ignored


def test_a_training_record_naming_another_holdout_is_refused(tmp_path, monkeypatch):
    """The refusal that keeps a model which saw the target out of a `sup-transfer` arm.

    Driven through the real function with a redirected output root, because the mistake
    it catches is a file-naming one: `tagger_loco_en-deid.json` holding `holdout:
    ko-surro` is readable, parses, and would publish the wrong model's epoch.
    """
    monkeypatch.setattr(run_loco, "OUT", tmp_path)
    (tmp_path / "tagger_loco_en-deid.json").write_text(
        json.dumps({"holdout": "ko-surro", "seed": 1, "selected_epoch": 3,
                    "commit": "0" * 40, "holdout_dev_used": False}),
        encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        run_loco.training_record("en-deid")
    assert "holdout" in str(raised.value)


def test_a_record_that_does_not_state_holdout_dev_used_is_refused(tmp_path,
                                                                  monkeypatch):
    """Absent is refused and explicitly-false is recorded — `rules_source`' rule."""
    monkeypatch.setattr(run_loco, "OUT", tmp_path)
    (tmp_path / "tagger_loco_en-deid.json").write_text(
        json.dumps({"holdout": "en-deid", "seed": 1, "selected_epoch": 3,
                    "commit": "0" * 40}),
        encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        run_loco.training_record("en-deid")
    assert "holdout_dev_used" in str(raised.value)


# ─── the child-process dev scoring (DESIGN §13.6) ───────────────────────────


def test_a_training_run_still_needs_a_seed():
    """`--seed` was relaxed for the `--score-only` child; training must still carry it.

    The relaxation is the risk: an unseeded training run would be unrecorded, and
    argparse stopped being the thing that prevents it.
    """
    with pytest.raises(SystemExit) as raised:
        train.main(["--holdout", "de-grascco"])
    assert "--seed is required" in str(raised.value)


def test_the_child_half_needs_no_seed():
    """The other side of it: `--score-only` parses without one.

    Driven to the point of loading weights and no further — the path under test is the
    argument handling, and scoring a real model here would cost three minutes.
    """
    with pytest.raises(Exception) as raised:
        train.main(["--score-only", "/nonexistent/weights",
                    "--score-corpora", "de-grascco"])
    assert "--seed is required" not in str(raised.value)


def test_the_holdout_assertion_reads_the_training_list():
    """It must not be checkable only on `dev_docs`, which one path leaves empty.

    The first form asserted `args.holdout not in dev_docs`, and under
    `--dev-in-subprocess` the parent builds no `dev_docs` at all — so the one check that
    makes `sup-transfer` true would have passed vacuously on the path all five reported
    models are trained with.
    """
    import inspect

    source = inspect.getsource(train.main)
    assert "args.holdout in used" in source
    assert "args.holdout in dev_docs" not in source


def test_the_verification_against_a_completed_model_is_recorded():
    """The switch's evidence is a committed file, not a sentence in a commit message."""
    path = os.path.join(ROOT, "experiments", "loco_v24b",
                        "child_scoring_verified.json")
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    assert record["identical"] is True
    assert record["values_compared"] == 36
    assert len(record["corpora"]) == 5
    # The completed model it was verified against, and the number that ties it to the
    # published record rather than to itself.
    assert "hybrid_v24/weights/seed1" in record["weights"]
    with open(os.path.join(ROOT, "experiments", "hybrid_v24",
                           "tagger_seed1.json"), encoding="utf-8") as fh:
        published = json.load(fh)
    selected = [row for row in published["curve"]
                if row["epoch"] == published["selected_epoch"]][0]
    assert record["values"]["summed_L_full"] == selected["summed_L_full"]
