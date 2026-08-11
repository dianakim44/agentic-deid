"""Tests for tools/run_arm.py — the one command that spends the arm's single call.

What this tool is for: `run_arm()` is keyword-only over four axis values, a language and a
model id, and the arm it drives makes one call that freezes the window permanently
(DESIGN §6.3). Through `python3 -c` every argument is a bare string, and the failure that
matters is not an exception — it is a typo that *succeeds*, minting `results/…/RR/…` as a
second detector. So what is tested here is the refusals, and the order they happen in.

**Every check must fire before the call, and `--dry-run` must make no call.** A precondition
discovered after the call is a precondition discovered too late: the window is bound from
the moment the log line lands, the tokens are paid for, and the arm cannot be re-run. The
tests therefore assert that a bad invocation exits non-zero having written nothing at all —
`results/` is checked for absence, not just the exit code.

**A dry run with a shut gate exits non-zero.** It prints the plan, because "not ready, and
here is the cell it would have run in" is the answer a dry run is asked for; but a script
reading 0 from it would read that as permission.

**Two cells, because a dry run has two correct outcomes and they are not the same test.**
An arm that has not called yet gets the plan; an arm that has gets a refusal, and the
refusal happens *before* the plan (DESIGN §6.3 — the window is bound from the moment the
`agent_calls.jsonl` line lands, so there is nothing left to approve). Every test below
therefore names which cell it is about. `dry()` runs a cell that has not called, asked of
`called_where()` rather than pinned to a value, and `called()` runs the baseline cell,
which has.

This split is a repair. Every test here used to run the baseline cell, on the presumption
that no arm had run — and the first `port-oneshot` run made that false. Five tests then
failed, which was the visible half; the invisible half is that three others went on passing
while asserting nothing, because a refusal prints an empty stdout and `"sealed/" not in ""`
is true. So the three carry a positive control now: each states that the plan was printed
before it says what is absent from it. `tests/mutations/README.md` records the shape.

Run as subprocesses. What is being tested is what a person types and what comes back, and
an in-process `main()` call shares this process's imported modules and its cwd — the tool
inserts `ROOT` on `sys.path` and reads the real `config/naming.yaml`, which is the thing
whose vocabulary the refusals are about.
"""
from __future__ import annotations

import functools
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import orchestrate                                          # noqa: E402
from src.corpora.base import axis                                    # noqa: E402

TOOL = ROOT / "tools" / "run_arm.py"
CORPUS = "es-meddocan"
LANG = "es"

#: The baseline cell, which on this corpus has already spent its one call. Read from the
#: orchestrator rather than spelled, for the reason the tool's own defaults are
#: (`test_the_axis_defaults_come_from_the_orchestrator`): two copies of "the baseline cell"
#: is two answers the day the baseline moves.
CALLED_CELL = (orchestrate.DETECTOR, orchestrate.SUPERVISION, orchestrate.PORTING)

#: An id in the accepted shape, undated. Not a default anywhere in the tool — that is a
#: test below — so the tests spell one, and they spell an *undated* one so that the
#: `alias-unresolved` line in the plan is exercised as the honest case it is.
ALIAS = "us.anthropic.claude-opus-5"

#: A dated id, to check that the plan reports the resolution the run block would record
#: rather than a guess. Which dated ids exist is an account fact and changes; what is
#: asserted is the mapping from shape to resolution, which is `bedrock`'s and not this
#: tool's.
DATED = "us.anthropic.claude-opus-4-5-20251101-v1:0"


def run(*args, expect: int | None = None) -> subprocess.CompletedProcess:
    done = subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, cwd=ROOT)
    if expect is not None:
        assert done.returncode == expect, f"stdout:\n{done.stdout}\nstderr:\n{done.stderr}"
    return done


@functools.lru_cache(maxsize=1)
def uncalled_cell() -> tuple[str, str, str]:
    """A cell of this corpus that has not made its call, so a dry run reaches the plan.

    **Asked of `called_where()` rather than pinned to a value, and that is the repair.**
    The version of this file that presumed the baseline was uncalled did not fail when the
    baseline ran — it failed *afterwards*, on state the arm had created, and the same thing
    would happen to any literal written here the day that cell runs. So the cell is
    discovered, from the same predicate the tool branches on.

    Detector first because `port-oneshot` is the arm under test and the supervision axis is
    not a place to wander: `sup-human` names a different experiment. The order is
    naming.yaml's, so the answer moves only when the axis does.
    """
    for detector in axis("detector"):
        cell = (detector, orchestrate.SUPERVISION, orchestrate.PORTING)
        if orchestrate.called_where(CORPUS, *cell) is None:
            return cell
    raise AssertionError(
        f"every {CORPUS} cell of {orchestrate.PORTING} has called, so no dry run in this "
        "file can reach the plan. Point these tests at a corpus that has not run rather "
        "than asserting the refusal twice — a plan test that only ever sees a refusal "
        "passes while checking nothing, which is the defect this helper exists to end."
    )


def dry(*args, expect: int | None = None) -> subprocess.CompletedProcess:
    """A dry run of an uncalled cell, with `args` appended or overriding.

    The plan branch. Everything about what the tool *prints* is asserted through this, and
    an arm that has already called is `called()` below.
    """
    detector, supervision, porting = uncalled_cell()
    return run("--corpus", CORPUS, "--lang", LANG, "--model-id", ALIAS, "--dry-run",
               "--detector", detector, "--supervision", supervision, "--porting", porting,
               *args, expect=expect)


def called(*args, expect: int | None = None) -> subprocess.CompletedProcess:
    """A dry run of the baseline cell, which has spent its one call on this corpus.

    Separate from `dry()` because the refusal is not a degraded plan — it is the other
    correct outcome, and it is reached before the plan is made.
    """
    detector, supervision, porting = CALLED_CELL
    return run("--corpus", CORPUS, "--lang", LANG, "--model-id", ALIAS, "--dry-run",
               "--detector", detector, "--supervision", supervision, "--porting", porting,
               *args, expect=expect)


# ─── nothing is written, and no call is made ────────────────────────────────


def test_a_dry_run_writes_nothing_under_the_arm():
    """The property the whole tool rests on. `--dry-run` is what a person runs first, and
    it is only useful if running it cannot cost the arm its window: `paths.armfreeze` is
    the first thing `run_arm()` writes, and once written with a log line beside it the
    window is bound (DESIGN §6.3).

    Asserted against the real results tree rather than a temporary one, deliberately. The
    tool is not parameterised by a root — it writes where the config says — so a test that
    redirected it would be testing a path this tool cannot take.

    Both cells, because they stop at different points and only one of them is the
    interesting case. The refused cell never reaches a write; the uncalled one runs every
    check the real invocation runs and stops at the freeze, which is the property being
    claimed. A test that checked only the refused cell would pass on a tool that froze the
    window on every dry run of every arm that had not yet called.
    """
    for cell, invoke in ((uncalled_cell(), dry), (CALLED_CELL, called)):
        arm = ROOT.joinpath("results", CORPUS, *cell)
        before = sorted(p.name for p in arm.iterdir()) if arm.exists() else None
        invoke()
        after = sorted(p.name for p in arm.iterdir()) if arm.exists() else None
        assert after == before, cell


def test_a_dry_run_reaches_no_transport():
    """No boto3 client, no network. The tool imports `bedrock` for its error type and its
    resolution predicate, and neither of those constructs a client — `bedrock._client()`
    imports boto3 inside the function for exactly this reason. A dry run that opened a
    session would be a dry run that needed credentials to answer "is this command right".

    The plan assertion is the positive control and is not decoration. `_plan()` is where
    `bedrock._resolution` is called, so a run that stopped before it would satisfy all
    three absences having exercised nothing — which is what this test did while the
    baseline cell was refused.
    """
    done = dry()
    assert "resolution" in done.stdout, "the plan was not reached, so nothing was tested"
    assert "botocore" not in done.stderr
    assert "NoCredentials" not in done.stderr
    assert "Traceback" not in done.stderr


# ─── the axis refusals: a typo must not mint a cell ─────────────────────────


def test_a_mistyped_detector_is_refused_and_names_the_axis():
    """`RR` is the failure this tool exists for: it is not an exception anywhere
    downstream until `_arm_path()` fills a template, and by then the plan has been made.
    The message names the axis and lists its values, because the person who typed `RR`
    needs the vocabulary rather than a verdict (CLAUDE.md: only naming.yaml values).
    """
    done = dry("--detector", "RR", expect=2)
    assert "detector axis" in done.stderr
    assert "config/naming.yaml" in done.stderr
    assert "'R'" in done.stderr


def test_each_axis_flag_is_checked_and_not_only_the_detector():
    """One flag validated and three trusted is the shape this would rot into. All four
    path-templated axes plus `split` are checked, so each is exercised."""
    for flag, bad in (("--detector", "RR"), ("--supervision", "sup-freee"),
                      ("--porting", "port-oneshoot")):
        done = dry(flag, bad, expect=2)
        assert "axis" in done.stderr, flag
        assert bad in done.stderr, flag


def test_an_unknown_corpus_is_refused():
    done = run("--corpus", "es-meddocann", "--lang", LANG, "--model-id", ALIAS,
               "--dry-run", expect=2)
    assert "corpus" in done.stderr


def test_a_language_the_corpus_does_not_load_is_refused():
    """`--lang de` against a Spanish corpus. One call authors one file, and a file no
    corpus loads would be scored by nothing (DESIGN §5.2). The message quotes
    `corpus_rule_langs` rather than the corpus's name, because the mapping is the
    authority."""
    done = dry("--lang", "de", expect=2)
    assert "corpus_rule_langs" in done.stderr
    assert "['es']" in done.stderr


# ─── the sealed fold is not reachable from here ─────────────────────────────


def test_the_test_fold_is_refused_and_points_at_the_sealed_path():
    """`--split test` exits 2 naming `run_sealed_eval`. `run_fold.load_fold()` refuses it
    too and that is the guarantee; this refusal exists so that the pointer arrives before
    a window is frozen, and so that the tool cannot be the place the rule is forgotten
    (CLAUDE.md, DESIGN §6.1)."""
    done = dry("--split", "test", expect=2)
    assert "sealed" in done.stderr
    assert "run_sealed_eval" in done.stderr


def test_no_sealed_path_appears_in_the_output():
    """Neither the plan nor an error may name the sealed directory. The plan prints five
    filled templates and a rule path; if any of them could be pointed at `sealed/`, the
    tool would be the map to a directory nobody is to open.

    The six paths are asserted present first. An absence check over an empty stdout is
    vacuously true, and that is not hypothetical here: this test passed unchanged while the
    baseline cell printed nothing but a refusal.
    """
    done = dry()
    assert "armrules" in done.stdout, "the plan was not reached, so nothing was tested"
    assert "sealed/" not in done.stdout


# ─── the gate, and the exit code that carries it ────────────────────────────


def test_a_dry_run_prints_the_gate_state_and_exits_on_it():
    """The gate line is printed either way, and the exit code follows it. Which branch runs
    depends on whether today's logging check is on record, which is a fact about the
    account and the day — so the test asserts the *correspondence* rather than one
    outcome, which is the part that would break silently."""
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
    gate open to find out what it would have done, which is the run they were trying to
    check first."""
    done = dry()
    assert orchestrate.PORTING in done.stdout
    assert "model_id" in done.stdout


# ─── the plan says which cell, and which resolution ─────────────────────────


def test_the_plan_names_every_path_the_arm_writes():
    """Six paths: the rule file and the five `paths` keys. A plan that omitted one would
    let a person approve a run whose output lands somewhere they did not look."""
    done = dry()
    for key in ("armrules", "armfreeze", "agentlog", "metrics", "spans", "formatfailure"):
        assert key in done.stdout, key
    detector, supervision, porting = uncalled_cell()
    assert (f"results/{CORPUS}/{detector}/{supervision}/{porting}/rules/iter1/{LANG}.yaml"
            in done.stdout)


def test_the_plan_reports_the_resolution_the_run_block_would_record():
    """Both directions, because this is the field the choice of id is *about*: an undated
    alias records `alias-unresolved` and can never be pinned down afterwards, and the arm
    cannot be re-run to find out (DESIGN §10 A2, docs/notes/baseline-model-family.md)."""
    assert "alias-unresolved" in dry().stdout
    assert "dated" in dry("--model-id", DATED).stdout


def test_the_plan_reports_the_tree_state():
    """`commit` and `tree` go into the run block. A dirty tree means the recorded hash does
    not describe the code that ran, and the dry run is the last moment that is fixable."""
    done = dry()
    assert "commit" in done.stdout
    assert "tree" in done.stdout


# ─── the arm that has already called: the refusal, not a degraded plan ──────


def test_a_dry_run_of_a_called_arm_is_refused_and_prints_no_plan():
    """The baseline cell on this corpus has spent its one call, and this is what a dry run
    of it must do (DESIGN §6.3, §11.1).

    **The refusal replaces the plan rather than preceding it, and that ordering is the
    claim.** `--dry-run` normally prints the plan even when the logging gate is shut, on
    the reasoning that "not ready, and here is the cell it would have run in" is what a dry
    run is asked for. This case is different in kind: the window is bound from the moment
    the `agent_calls.jsonl` line lands, so there is no run left to approve and a plan would
    describe an invocation the tool will not make. It goes to stderr and exit 2, where a
    script cannot read it as a readiness report.
    """
    done = called(expect=2)
    assert "already made its call" in done.stderr
    assert "DESIGN §6.3" in done.stderr
    assert done.stdout == "", (
        "a called arm printed a plan. The refusal is asked before `_plan()` because there "
        f"is nothing to approve:\n{done.stdout}"
    )


def test_the_refusal_names_the_cell_and_its_evidence():
    """Which arm, and how the tool knows — the two things the person needs.

    The evidence word is `called_where()`'s (`log` / `results`, `config`-free literals in
    `src/orchestrate.py`) and it is the actionable half: `log` means the call log is sitting
    there, `results` means it is gone and a committed artefact is what remains. Advice that
    did not distinguish them would send someone looking for a file that is not there.
    """
    detector, supervision, porting = CALLED_CELL
    done = called(expect=2)
    assert f"{CORPUS}/{detector}/{supervision}/{porting}" in done.stderr
    assert orchestrate.called_where(CORPUS, *CALLED_CELL) in (
        orchestrate.IN_LOG, orchestrate.IN_RESULTS)
    assert f"evidence: {orchestrate.called_where(CORPUS, *CALLED_CELL)}" in done.stderr


def test_the_refusal_outranks_the_logging_gate():
    """Both are exit 2, and the message must be the one that cannot be fixed today.

    A shut logging gate is a thing to go and do; a spent call is not, and reporting the
    gate would send the person to `check_bedrock_logging.py` and back for a second refusal
    they could have had first. So this asserts the gate line is absent rather than merely
    that the refusal is present — the two coexisting in one output is the failure that
    reads as helpful.
    """
    done = called(expect=2)
    assert "already made its call" in done.stderr
    assert "logging gate" not in done.stdout
    assert "logging gate" not in done.stderr


# ─── the model id is a parameter, here as everywhere ────────────────────────


def test_the_model_id_is_required_and_has_no_default():
    """`argparse` refuses the invocation with exit 2 and its own message. The rule is
    DESIGN §10 A2's: a recorded id that came from a default records what the code says
    rather than what was called, and a CLI default is the most inviting place for one."""
    done = run("--corpus", CORPUS, "--lang", LANG, "--dry-run", expect=2)
    assert "--model-id" in done.stderr


def test_the_tool_spells_no_model_id():
    """The same check `tests/test_orchestrate.py` makes of `src/orchestrate.py`, applied
    one level out. A literal id in a `--model-id` help string or an example would be the
    value a person copies, and the copy is what gets recorded.

    The docstring's usage examples are the exception this cannot allow: an example naming
    an id would be pasted, so the examples name a placeholder instead — which is why this
    reads the whole file and not only its code.
    """
    text = TOOL.read_text(encoding="utf-8")
    for fragment in ("anthropic.claude", "us.anthropic", "meta.llama", "amazon.nova"):
        assert fragment not in text, fragment


def test_the_axis_defaults_come_from_the_orchestrator():
    """`--detector R` and the rest are defaults, and they are read from
    `src.orchestrate` rather than written here. Two copies of "the baseline cell" is two
    answers the day the baseline moves (CLAUDE.md's naming rule; DESIGN §11.2)."""
    text = TOOL.read_text(encoding="utf-8")
    for name in ("orchestrate.DETECTOR", "orchestrate.SUPERVISION", "orchestrate.PORTING"):
        assert name in text, name
    # The values themselves must not be spelled as defaults. `"R"` and `"sup-free"` as
    # literals here would pass the check above while ignoring it.
    assert 'default="R"' not in text
    assert 'default="sup-free"' not in text
    assert 'default="port-oneshot"' not in text


def test_no_path_template_is_spelled_here():
    """The plan prints filled templates and gets them from `path_template()`. A literal
    `results/{corpus}/…` in this file would be a second copy of the config's answer, which
    is what `paths` in naming.yaml exists to prevent (DESIGN §11.2)."""
    text = TOOL.read_text(encoding="utf-8")
    assert "path_template" in text
    assert "results/{corpus}" not in text


# ─── the already-called refusal ─────────────────────────────────────────────


def test_the_already_called_refusal_is_asked_before_the_plan():
    """This arm makes one call. The tool asks `called_where()` — the same predicate
    `freeze_window()` conditions its refusal on, which is the call log and not
    `path.exists()` (DESIGN §6.3) — so that a second invocation is refused by name rather
    than by an exception from inside the freeze.

    **Kept as a source check even though the behaviour is now covered above, because the
    two assert different things.** The tests above observe that *this corpus's* baseline
    cell is refused, which they can only do because it has run; this one asserts that the
    refusal is conditioned on the call log at all, and it goes on holding on a machine where
    no arm has ever run. Which predicate the branch reads is not visible in the output —
    `path.exists()` would produce a byte-identical refusal here — and that substitution is
    the fourth member of `tests/mutations/README.md`'s family, made once already in
    `freeze_window()`.
    """
    text = TOOL.read_text(encoding="utf-8")
    assert "called_where" in text
    assert "already made its call" in text
