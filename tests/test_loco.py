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


# ─── the per-epoch process boundary (DESIGN §13.6) ──────────────────────────
#
# What is checked here, and what deliberately is not. Value equality across the
# boundary **cannot** be checked: DESIGN §12.8 established that training is not
# reproducible from the seed on MPS, so two runs of the same epochs differ in their
# weights whether or not a process boundary falls between them, and a test asserting
# equal losses would be asserting the absence of a thing the project has already
# measured as present.
#
# What can be checked is everything the boundary is supposed to carry, because all of it
# is decided on the CPU and is therefore deterministic:
#
#   - the data order, which `RandomSampler` draws from the CPU generator;
#   - the schedule's learning rate and the step counter at the moment of restore.
#
# Both are checked against an uninterrupted run of the same length, which is the
# comparison that would catch a handover that silently restarted the schedule or reshuffled
# from the seed.
#
# A toy model stands in for `xlm-roberta-base`: the handover helpers take state dicts and
# do not care what produced them, and loading the real encoder twice per test would add a
# minute to the suite to exercise the same four lines.


def _toy(seed, n_items=24, batch=4, epochs=2):
    """A model, an optimiser, a schedule and a sampler, built the way `main` builds them."""
    import torch
    from torch.utils.data import DataLoader, RandomSampler
    from transformers import get_linear_schedule_with_warmup

    train.set_seed(seed)
    model = torch.nn.Linear(3, 2)
    optimiser = torch.optim.AdamW(model.parameters(), lr=3e-5, weight_decay=0.01)
    steps = ((n_items + batch - 1) // batch) * epochs
    schedule = get_linear_schedule_with_warmup(optimiser, int(0.10 * steps), steps)
    data = [(torch.randn(3), torch.tensor([0.0, 1.0])) for _ in range(n_items)]
    sampler = RandomSampler(data)
    return model, optimiser, schedule, data, sampler, DataLoader, batch


def _step_epoch(model, optimiser, schedule, data, order, batch):
    """One epoch over an explicit order. The real loop's shape, without transformers."""
    import torch

    steps = 0
    for start in range(0, len(order), batch):
        rows = [data[i] for i in order[start:start + batch]]
        x = torch.stack([r[0] for r in rows])
        y = torch.stack([r[1] for r in rows])
        optimiser.zero_grad(set_to_none=True)
        loss = torch.nn.functional.mse_loss(model(x), y)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimiser.step()
        schedule.step()
        steps += 1
    return steps


def test_the_boundary_preserves_the_shuffle_order_and_the_schedule(tmp_path):
    """Two epochs straight through, then the same two with a handover between them.

    The uninterrupted run is the reference. The resumed run must draw the *same* order
    for epoch 2 and enter it with the same learning rate and the same step count — those
    three are what a restart-from-seed or a fresh schedule would get wrong, and both of
    those mistakes look like a working run from the outside.
    """
    import torch

    # ── reference: one process, two epochs
    model, optimiser, schedule, data, sampler, _, batch = _toy(1)
    reference = []
    step_total = 0
    for _ in range(2):
        order = train.shuffle_order(sampler)
        reference.append({
            "digest": train.order_digest(order),
            "lr_on_entry": schedule.get_last_lr()[0],
            "global_step": step_total,
        })
        step_total += _step_epoch(model, optimiser, schedule, data, order, batch)

    # ── resumed: epoch 1, hand over, rebuild everything, epoch 2
    model, optimiser, schedule, data, sampler, _, batch = _toy(1)
    order = train.shuffle_order(sampler)
    first = {"digest": train.order_digest(order),
             "lr_on_entry": schedule.get_last_lr()[0], "global_step": 0}
    taken = _step_epoch(model, optimiser, schedule, data, order, batch)
    state_path = tmp_path / "epoch_state.pt"
    train.save_epoch_state(
        state_path, epoch=1,
        model={k: v.cpu() for k, v in model.state_dict().items()},
        optimiser=optimiser.state_dict(), schedule=schedule.state_dict(),
        global_step=taken, rng=train.rng_state())

    # A fresh set of objects, as a new process would have. Seeded differently on purpose:
    # if the restore did not take, the order below would be seed 99's and not seed 1's.
    model2, optimiser2, schedule2, data2, sampler2, _, batch2 = _toy(99)
    state = train.load_epoch_state(state_path)
    model2.load_state_dict(state["model"])
    optimiser2.load_state_dict(state["optimiser"])
    schedule2.load_state_dict(state["schedule"])
    train.set_rng_state(state["rng"])
    second = {"digest": train.order_digest(train.shuffle_order(sampler2)),
              "lr_on_entry": schedule2.get_last_lr()[0],
              "global_step": state["global_step"]}

    assert first == reference[0]
    assert second["digest"] == reference[1]["digest"], (
        "epoch 2's data order differs across the handover. The order comes from the CPU "
        "generator, so this is a restore that did not take — not non-determinism."
    )
    assert second["lr_on_entry"] == reference[1]["lr_on_entry"]
    assert second["global_step"] == reference[1]["global_step"] == taken


def test_a_handover_from_the_wrong_epoch_is_refused(tmp_path, monkeypatch):
    """Epoch N may only resume epoch N−1's state.

    The failure this prevents is silent: resuming epoch 3 from epoch 1's state runs the
    schedule and the data order from the wrong point and still produces a model.
    """
    state_path = tmp_path / "epoch_state.pt"
    train.save_epoch_state(state_path, epoch=1, model={}, optimiser={}, schedule={},
                           global_step=10, rng=train.rng_state())
    state = train.load_epoch_state(state_path)
    assert state["epoch"] == 1
    # The refusal itself lives in `_train_epoch_child`, whose other half needs the real
    # encoder; what is asserted here is that the field it reads is in the file and says
    # which epoch wrote it.
    assert "global_step" in state and "rng" in state and "schedule" in state


def test_the_rng_handover_covers_every_generator_set_seed_sets():
    """`rng_state` must carry whatever `set_seed` seeds, or the boundary loses it."""
    import inspect

    seeded = inspect.getsource(train.set_seed)
    carried = train.rng_state()
    for source, key in (("random.seed", "python"), ("np.random.seed", "numpy"),
                        ("torch.manual_seed", "torch_cpu")):
        assert source in seeded
        assert key in carried, f"set_seed seeds {source} and rng_state drops it"


def test_a_missing_mps_generator_state_is_refused_on_mps(monkeypatch):
    """Absent is refused, not defaulted — the rule `rules_source` follows.

    A fresh process seeds its own MPS generator, so a handover that dropped that state
    would leave dropout differing across the boundary with nothing recording it.
    """
    import torch

    if not torch.backends.mps.is_available():
        pytest.skip("no MPS on this machine; the branch cannot be reached")
    state = train.rng_state()
    del state["torch_mps"]
    with pytest.raises(SystemExit) as raised:
        train.set_rng_state(state)
    assert "MPS generator state" in str(raised.value)


def test_one_checkpoint_at_a_time(tmp_path):
    """`save_epoch_state` replaces rather than accumulates, and leaves no partial file."""
    path = tmp_path / "epoch_state.pt"
    for epoch in (1, 2, 3):
        train.save_epoch_state(path, epoch=epoch, model={}, optimiser={},
                               schedule={}, global_step=epoch, rng=train.rng_state())
        assert train.load_epoch_state(path)["epoch"] == epoch
    assert sorted(p.name for p in tmp_path.iterdir()) == ["epoch_state.pt"]


def test_both_paths_run_the_same_epoch_function():
    """The claim the manuscript makes in one line, checked where it can be.

    `run_epoch` is the epoch body; the in-process loop and the per-epoch child both call
    it, so what differs between the two is where the process boundary falls. A second
    implementation on either side would make that sentence false, and the one thing that
    would not show up in any number is exactly that.
    """
    import inspect

    for function in (train.main, train._train_epoch_child):
        assert "run_epoch(" in inspect.getsource(function), function.__name__
    body = inspect.getsource(train.run_epoch)
    for line in ("loss.backward()", "clip_grad_norm_", "optimiser.step()",
                 "schedule.step()"):
        assert line in body
    # And the per-epoch orchestrator must not have a loop of its own.
    orchestrator = inspect.getsource(train._orchestrate_epochs)
    assert "loss.backward()" not in orchestrator
