"""Tests for tools/run_loop.py — the command that spends a `port-loop` round.

What this tool is for, and why `tools/run_arm.py` is not it: that tool routes through
`orchestrate.run_arm()`, which passes no `iteration=` to `run_fold` and so writes the
un-iterated results pair. Pointed at `--porting port-loop` it would freeze the window, make
one call, and leave nothing at `iter1/` — and round 2, which reads `iter1/metrics.json`,
would refuse on an arm whose one freeze was already spent. That failure is not an exception
anywhere: every file written would be internally consistent. So the refusals are what is
tested here, and the round number is the argument they are mostly about.

**A later round makes 1 + N calls, so "before the spend" means more here than it does for the
baseline.** `--dry-run` must reach the plan and write nothing; a check that fires after the
call has fired after the Auditor has read the whole dev fold.

**Two cells and three round numbers, because the correct outcomes are not one shape.** Round 1
of an arm that has not called gets the plan. Round 1 of an arm that has called gets a refusal —
the freeze is once per arm. A later round of an arm that has *not* called gets the opposite
refusal, and that pair is one predicate read two ways (`loop.run_iteration()`'s argument),
which is why both directions are asserted rather than just the one that fires today.

Every test names which case it is about, and the plan tests carry a positive control: a
refusal prints an empty stdout, and `"sealed/" not in ""` is true. `tests/test_run_arm_cli.py`
records what that cost the first time.

Run as subprocesses, for that file's reason: what is under test is what a person types and
what comes back, and an in-process `main()` shares this process's imported modules and cwd.
"""
from __future__ import annotations

import functools
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import orchestrate                                          # noqa: E402
from src.corpora.base import axis, termination_params               # noqa: E402
from src.porting import loop                                        # noqa: E402

TOOL = ROOT / "tools" / "run_loop.py"
CORPUS = "es-meddocan"
LANG = "es"

#: The cell this tool drives, read from the module rather than spelled: two copies of "the
#: loop arm's cell" is two answers the day one of the axes moves (CLAUDE.md's naming rule).
LOOP_CELL = (loop.DETECTOR, loop.SUPERVISION, loop.PORTING)

#: A cell that has spent its first call on this corpus — the baseline. Used for the
#: already-called refusal, which is about round 1 and not about which arm.
CALLED_CELL = (orchestrate.DETECTOR, orchestrate.SUPERVISION, orchestrate.PORTING)

#: An id in the accepted shape, undated, and an id that is dated. Neither is a default
#: anywhere in the tool — that is a test below — so the tests spell them, and the undated one
#: is the default here so the honest `alias-unresolved` line is what gets exercised.
ALIAS = "us.anthropic.claude-opus-5"
DATED = "us.anthropic.claude-opus-4-5-20251101-v1:0"


def run(*args, expect: int | None = None) -> subprocess.CompletedProcess:
    done = subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, cwd=ROOT)
    if expect is not None:
        assert done.returncode == expect, f"stdout:\n{done.stdout}\nstderr:\n{done.stderr}"
    return done


@functools.lru_cache(maxsize=1)
def uncalled_cell() -> tuple[str, str, str]:
    """A `port-loop` cell that has not called, so a round-1 dry run reaches the plan.

    Asked of `called_where()` rather than pinned, which is `tests/test_run_arm_cli.py`'s
    repair: a literal written here would stop being true the day this arm runs, and it would
    fail afterwards on state the arm created rather than at the moment of the change. The
    detector axis is what varies, for that file's reason — the supervision axis names a
    different experiment.
    """
    for detector in axis("detector"):
        cell = (detector, loop.SUPERVISION, loop.PORTING)
        if orchestrate.called_where(CORPUS, *cell) is None:
            return cell
    raise AssertionError(
        f"every {CORPUS} cell of {loop.PORTING} has called, so no round-1 dry run in this "
        "file can reach the plan. Point these tests at a corpus that has not run rather than "
        "asserting the refusal twice — a plan test that only ever sees a refusal passes while "
        "checking nothing."
    )


def dry(*args, iteration: str = "1", expect: int | None = None):
    """A dry run of an uncalled `port-loop` cell at `iteration`, with `args` overriding."""
    detector, supervision, porting = uncalled_cell()
    return run("--corpus", CORPUS, "--lang", LANG, "--model-id", ALIAS,
               "--iteration", iteration, "--dry-run",
               "--detector", detector, "--supervision", supervision, "--porting", porting,
               *args, expect=expect)


# ─── nothing is written, and no call is made ────────────────────────────────


def test_a_dry_run_writes_nothing_under_the_arm():
    """The property the tool rests on, and the reason it is worth more here than for the
    baseline: round 1 freezes the window, and a later round would have called the Auditor
    once per dev document before anything else went wrong (DESIGN §6.3, `auditor.md` §1.3).

    Asserted against the real results tree, deliberately: the tool is not parameterised by a
    root — it writes where the config says — so a redirected test would exercise a path this
    tool cannot take.

    Both branches, because they stop at different points. The uncalled cell runs every check
    the real invocation runs and stops at the freeze, which is the claim; the refused round
    never reaches a write, and a test of that alone would pass on a tool that froze the window
    on every dry run.
    """
    arm = ROOT.joinpath("results", CORPUS, *uncalled_cell())
    for iteration in ("1", "2"):
        before = sorted(p.name for p in arm.iterdir()) if arm.exists() else None
        dry(iteration=iteration)
        after = sorted(p.name for p in arm.iterdir()) if arm.exists() else None
        assert after == before, iteration


def test_a_dry_run_reaches_no_transport():
    """No boto3 client, no network. `bedrock._client()` imports boto3 inside the function for
    exactly this reason, and a dry run that needed credentials to answer "is this command
    right" would be run with the gate open instead.

    The plan assertion is the positive control: `_plan()` is where `bedrock._resolution` is
    called, so a run that stopped short would satisfy all three absences having exercised
    nothing.
    """
    done = dry()
    assert "resolution" in done.stdout, "the plan was not reached, so nothing was tested"
    assert "botocore" not in done.stderr
    assert "NoCredentials" not in done.stderr
    assert "Traceback" not in done.stderr


# ─── the round number is this tool's own argument ───────────────────────────


def test_the_round_number_is_required_and_has_no_default():
    """`loop.run_iteration()` makes `iteration` positional with no default, because a default
    round number is a round chosen by whichever caller forgot. A CLI default would be that
    caller."""
    detector, supervision, porting = uncalled_cell()
    done = run("--corpus", CORPUS, "--lang", LANG, "--model-id", ALIAS, "--dry-run",
               "--detector", detector, "--supervision", supervision, "--porting", porting,
               expect=2)
    assert "--iteration" in done.stderr


def test_a_round_past_the_ceiling_is_refused_and_names_it():
    """The ceiling is pre-registered (DESIGN §3) and `should_stop()` raises above it, so a
    round past the cap is a round whose own termination block the rule cannot evaluate. The
    refusal must arrive as a sentence before the freeze rather than as a traceback from
    inside the writer."""
    ceiling = termination_params()["ceiling"]
    done = dry(iteration=str(ceiling + 1), expect=2)
    assert f"ceiling of {ceiling}" in done.stderr
    assert "DESIGN §3" in done.stderr


def test_round_zero_and_negative_rounds_are_refused():
    """Rounds are numbered from 1 (`paths.itermetrics` — `iter{N}/`). A 0 would fill the
    template and write `iter0/`, which is the shape of failure this whole tool is about: not
    an exception, a directory."""
    for bad in ("0", "-1"):
        done = dry(iteration=bad, expect=2)
        assert "not a round" in done.stderr, bad


def test_the_ceiling_is_read_from_the_config_and_not_spelled():
    """`termination_params()` owns δ, k and the cap (`config/naming.yaml`). A literal 8 here
    would be a second copy of a pre-registered constant, and the copy is what a later δ edit
    would leave behind."""
    text = TOOL.read_text(encoding="utf-8")
    assert "termination_params" in text
    assert "ceiling = 8" not in text


# ─── the two directions of one predicate ────────────────────────────────────


def test_round_one_of_an_arm_that_has_called_is_refused():
    """The freeze is once per arm and the window is bound from the moment the call log line
    lands (DESIGN §6.3, §5.5), so round 1 of a called arm is spent. The message points at the
    later round rather than at a re-run, because continuing the arm is what the person
    actually wants.

    Run against the baseline cell, which has called on this corpus. Exit 2 and no plan: there
    is nothing left to approve.
    """
    detector, supervision, porting = CALLED_CELL
    done = run("--corpus", CORPUS, "--lang", LANG, "--model-id", ALIAS, "--iteration", "1",
               "--dry-run", "--detector", detector, "--supervision", supervision,
               "--porting", porting, expect=2)
    assert "already made its first call" in done.stderr
    assert f"evidence: {orchestrate.called_where(CORPUS, *CALLED_CELL)}" in done.stderr
    assert done.stdout == "", f"a called arm printed a plan:\n{done.stdout}"


def test_a_later_round_of_an_arm_that_has_not_called_is_refused():
    """The complement, and it is the same predicate read the other way rather than a second
    guard: a round 2 on an arm with no round 1 has no §§1.2-1.4 — no rule file to show, no
    score, no error list. `loop.run_iteration()` refuses it too and that is the guarantee;
    this refusal exists so the reason arrives before 1 + N calls are made."""
    done = dry(iteration="2", expect=2)
    assert "has made no call" in done.stderr
    assert "--iteration 1" in done.stderr
    assert done.stdout == "", f"a round with no predecessor printed a plan:\n{done.stdout}"


def test_both_directions_are_present_in_the_source():
    """Kept as a source check as well, because which cases fire depends on what has run.

    Today the loop arm has not called and the baseline has, so the two tests above exercise
    one direction each — from different cells. On a machine where both cells are in the same
    state, one of them would assert the other's refusal or none. What does not move is that
    the tool branches on the round number in both directions, and that is what this reads.
    """
    text = TOOL.read_text(encoding="utf-8")
    assert "called_where" in text
    assert "already made its first call" in text
    assert "has made no call" in text


# ─── the axis refusals, which are run_arm.py's and are not copied ───────────


def test_a_mistyped_axis_is_refused_and_names_the_axis():
    """`RR` is not an exception anywhere downstream until `_arm_path()` fills a template, and
    by then the plan has been made and the round has run. The message names the axis and lists
    its values, because the person who typed it needs the vocabulary (CLAUDE.md)."""
    for flag, bad in (("--detector", "RR"), ("--supervision", "sup-freee"),
                      ("--porting", "port-looop")):
        done = dry(flag, bad, expect=2)
        assert "axis" in done.stderr, flag
        assert bad in done.stderr, flag


def test_the_axis_checks_are_borrowed_and_not_copied():
    """One implementation of "a mistyped axis mints a cell", in the tool that owns it. Two
    copies drift on the day one of them learns something the other does not, and what they
    would fail to learn is a check — so the drift is silent by construction. Same argument
    the tool makes about the logging gate."""
    text = TOOL.read_text(encoding="utf-8")
    assert "_check_axes" in text
    assert "_logging_state" in text
    assert "run_arm.py" in text


def test_a_language_the_corpus_does_not_load_is_refused():
    """One round authors one file, and a file no corpus loads would be scored by nothing
    (DESIGN §5.2). The message quotes `corpus_rule_langs`, which is the authority."""
    done = dry("--lang", "de", expect=2)
    assert "corpus_rule_langs" in done.stderr


def test_an_unknown_corpus_is_refused():
    done = run("--corpus", "es-meddocann", "--lang", LANG, "--model-id", ALIAS,
               "--iteration", "1", "--dry-run", expect=2)
    assert "corpus" in done.stderr


# ─── the sealed fold is not reachable from here ─────────────────────────────


def test_the_test_fold_is_refused_and_points_at_the_sealed_path():
    """`run_fold.load_fold()` refuses it too and that is the guarantee; this refusal puts the
    pointer before the freeze, and keeps this tool from being the place the rule is forgotten
    (CLAUDE.md, DESIGN §6.1)."""
    done = dry("--split", "test", expect=2)
    assert "sealed" in done.stderr
    assert "run_sealed_eval" in done.stderr


def test_no_sealed_path_appears_in_the_output():
    """The plan prints ten filled templates. If any could point at `sealed/`, the tool would
    be the map to a directory nobody is to open. The presence check first, because an absence
    check over an empty stdout is vacuous."""
    done = dry()
    assert "armrules" in done.stdout, "the plan was not reached, so nothing was tested"
    assert "sealed/" not in done.stdout


# ─── what the plan says ─────────────────────────────────────────────────────


def test_the_plan_names_every_path_the_round_writes():
    """A plan that omitted one would let a person approve a round whose output lands somewhere
    they did not look. Round 1's set is the arm-scoped files plus the three round-scoped ones;
    `auditreport` is not among them, which is the next test."""
    done = dry()
    for key in ("armrules", "armfreeze", "agentlog", "formatfailure", "metrics", "spans",
                "itermetrics", "iterspans", "itererrors"):
        assert key in done.stdout, key
    detector, supervision, porting = uncalled_cell()
    stem = f"results/{CORPUS}/{detector}/{supervision}/{porting}"
    assert f"{stem}/rules/iter1/{LANG}.yaml" in done.stdout
    assert f"{stem}/iter1/metrics.json" in done.stdout


def test_round_one_promises_no_audit_report_and_no_auditor_call():
    """The Auditor runs from round 2 (`config/naming.yaml` agent_role), so round 1's plan must
    not promise a file it will not write or a call it will not make. This is the field a cost
    estimate is read off, and a round-1 plan claiming 251 calls would be wrong in the
    direction nobody checks.

    The Auditor's absence is asserted on the `calls` line rather than on the whole plan,
    because the plan names `auditor.md` either way: it is in the window, and its hash is
    printed from round 1 on. That is not a promise of a call — the file is part of what was
    frozen — so a whole-output search here would forbid printing the window.
    """
    done = dry()
    calls = [line for line in done.stdout.splitlines() if line.startswith("calls")]
    assert calls, f"the plan printed no call count:\n{done.stdout}"
    # `calls` above is the presence control for both absences: an empty or truncated plan
    # fails it rather than satisfying `"auditreport" not in ""`. Ordered so the control
    # runs first, which is the whole point of having one.
    assert "auditreport" not in done.stdout
    assert f"1 {orchestrate.RULE_AUTHOR}" in calls[0]
    assert loop.AUDITOR not in calls[0], calls[0]


def test_a_later_rounds_plan_promises_the_audit_report_and_the_fold_of_calls(monkeypatch):
    """The other half of the round-1 test, and the only test here that is not a subprocess.

    A later round's plan cannot be reached from the command line on a machine where the arm
    has not run — `_plan()`'s history comes from `iter{N−1}/metrics.json`, which is the point.
    So this calls `_plan()` directly with a history it fabricates, which makes it a test of
    the plan's *branch* rather than of the tool end to end. The branch is worth a test on its
    own: it is where 1 + N is stated, and 1 + N is the number a person approving a round is
    approving.

    `window_drift()` is patched because it reads the arm's freeze record, which this fabricated
    history does not come with. It is exercised for real in `tests/test_loop.py`.
    """
    import argparse
    import importlib.util

    from src.termination import should_stop

    spec = importlib.util.spec_from_file_location("_run_loop_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    detector, supervision, porting = uncalled_cell()
    args = argparse.Namespace(corpus=CORPUS, lang=LANG, iteration=2, model_id=ALIAS,
                              detector=detector, supervision=supervision, porting=porting,
                              split="dev", max_tokens=None, dry_run=True)
    rates = [0.42]
    history = (rates, should_stop(CORPUS, rates))

    monkeypatch.setattr(orchestrate, "window_drift", lambda *a, **k: [])
    plan = "\n".join(module._plan(args, history, 250))

    assert "auditreport" in plan
    assert f"1 {orchestrate.RULE_AUTHOR} + 250 {loop.AUDITOR}" in plan
    assert "= 251" in plan
    assert "round 1: 0.4200" in plan
    assert "stands       not stopped" in plan
    assert "sealed/" not in plan


def test_the_plan_states_the_round_and_the_ceiling():
    """"Round 3 of at most 8" is the fact that decides whether to run it. A plan that named
    the round without the cap would leave the person counting."""
    done = dry()
    assert f"of at most {termination_params()['ceiling']}" in done.stdout


def test_the_plan_prints_all_three_window_hashes():
    """The window is three files since 2026-08-12 (`sample.WINDOW_FILES`), and from round 2
    the Auditor template is one of them. A round is the first artefact that can have been
    assembled under a moved window, so the hashes belong in the plan and not only in the
    freeze record — and the field names are `WINDOW_HASH_FIELDS`', which is what the record is
    written from."""
    from src.sample import WINDOW_HASH_FIELDS, window_hashes

    done = dry()
    now = window_hashes()
    for name, field in WINDOW_HASH_FIELDS.items():
        assert field in done.stdout, field
        assert now[field] in done.stdout, name


def test_the_plan_reports_the_resolution_the_run_block_would_record():
    """Both directions, because this is the field the choice of id is about: an undated alias
    records `alias-unresolved` and can never be pinned down afterwards, and the round cannot
    be re-run to find out (DESIGN §10 A2)."""
    assert "alias-unresolved" in dry().stdout
    assert "dated" in dry("--model-id", DATED).stdout


def test_the_plan_reports_the_tree_state():
    """`commit` and `tree` go into the run block. A dirty tree means the recorded hash does
    not describe the code that ran, and the dry run is the last moment that is fixable."""
    done = dry()
    assert "commit" in done.stdout
    assert "tree" in done.stdout


# ─── the gate, and the exit code that carries it ────────────────────────────


def test_a_dry_run_prints_the_gate_state_and_exits_on_it():
    """The gate line is printed either way and the exit code follows it. Which branch runs is
    a fact about the account and the day, so what is asserted is the correspondence — the part
    that would break silently."""
    done = dry()
    assert "logging gate" in done.stdout
    if "logging gate  ok" in done.stdout:
        assert done.returncode == 0
    else:
        assert "BLOCKED" in done.stdout
        assert done.returncode == 2
        assert "check_bedrock_logging.py" in done.stdout


def test_a_blocked_gate_still_shows_the_plan():
    """A readiness report whose failure hides the plan makes the person re-run it with the
    gate open to find out what it would have done — which is the run they were checking."""
    done = dry()
    assert loop.PORTING in done.stdout
    assert "model_id" in done.stdout


# ─── the model id is a parameter, here as everywhere ────────────────────────


def test_the_model_id_is_required_and_has_no_default():
    """DESIGN §10 A2: a recorded id that came from a default records what the code says
    rather than what was called, and a CLI default is the most inviting place for one."""
    detector, supervision, porting = uncalled_cell()
    done = run("--corpus", CORPUS, "--lang", LANG, "--iteration", "1", "--dry-run",
               "--detector", detector, "--supervision", supervision, "--porting", porting,
               expect=2)
    assert "--model-id" in done.stderr


def test_the_tool_spells_no_model_id():
    """The whole file and not only its code: a usage example naming an id is the id that gets
    pasted, and the paste is what gets recorded."""
    text = TOOL.read_text(encoding="utf-8")
    for fragment in ("anthropic.claude", "us.anthropic", "meta.llama", "amazon.nova"):
        assert fragment not in text, fragment


def test_the_axis_defaults_come_from_the_loop_module():
    """`--detector R` and the rest are defaults read from `src.porting.loop` rather than
    written here — that module declares this arm's cell, and two copies is two answers the
    day one of them moves (CLAUDE.md's naming rule)."""
    text = TOOL.read_text(encoding="utf-8")
    for name in ("loop.DETECTOR", "loop.SUPERVISION", "loop.PORTING"):
        assert name in text, name
    assert 'default="R"' not in text
    assert 'default="sup-free"' not in text
    assert 'default="port-loop"' not in text


def test_no_path_template_is_spelled_here():
    """The plan prints filled templates and gets them from `path_template()`. A literal
    `results/{corpus}/…` would be a second copy of the config's answer (DESIGN §11.2)."""
    text = TOOL.read_text(encoding="utf-8")
    assert "path_template" in text
    assert "results/{corpus}" not in text
    assert "iter{iteration}" not in text


# ─── one round per invocation ────────────────────────────────────────────────


def test_the_tool_runs_one_round_and_offers_no_run_to_completion_flag():
    """The refused shape, asserted so it is not added later without the argument being met.

    A `--through 8` would make one command about two thousand calls (1 + 250 per round from
    round 2), and the thing it takes away is the chance to read round 2 before round 3 is
    assembled from it. Nothing is lost by stopping between rounds: the chain is on disk, and
    `loop.run_iteration()` refuses a gap and refuses a round the rule already stopped.

    Read off the options section rather than the whole help text: the description above it is
    the module docstring, which *names* `--through` in order to record why there isn't one.
    A search over the whole output would make the explanation the violation.
    """
    done = run("--help", expect=0)
    head, marker, options = done.stdout.partition("options:")
    assert marker, f"argparse printed no options section:\n{done.stdout}"
    assert "--through" in head, (
        "the docstring no longer says why there is no run-to-completion flag. The argument is "
        "in DESIGN §5.5's shape — 1 + N calls per round, and a person reading round N before "
        "round N + 1 is assembled from it — and a tool that dropped it would grow the flag.")
    for flag in ("--through", "--until", "--all", "--rounds", "--max-iterations"):
        assert flag not in options, flag
