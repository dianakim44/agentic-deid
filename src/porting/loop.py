"""The `port-loop` orchestrator: round 1, and every round after it.

`port-loop` is the iterating rung: the RuleAuthor writes `rules/{lang}.yaml`, the fold is
scored, and the score and its error spans come back as §§1.3–1.4 of the next call (DESIGN
§4). Two functions, because there are two procedures and not eight: `run_iteration_1()` is
the arm's first call, and `run_iteration()` is every later round — round 3 is round 2 with a
longer history, and the history is a parameter.

**What a later round adds is feedback, and it is four inputs rather than one.** Round 1 is
shown §§1.1–1.2 and states §§1.3–1.4 empty; every round after it fills all four, and
`assemble_iteration_prompt` refuses a round ≥ 2 that is missing any of `metrics`, `errors`,
`docs_by_id`, `context_chars` or `audit_report`. So the round's shape is fixed by that
signature rather than by this file: the Auditor runs first over the previous round's
predictions masked, its report and that round's score become §1.3, a seeded stratified draw
over that round's `errors.jsonl` becomes §1.4, and only then is the RuleAuthor called.
Everything after that call — write, validate by loading, score or record the failure — is
round 1's procedure unchanged, which is why the two functions converge on the same six lines.

**Round *N* reads round *N−1*, and that relation is the loop rather than a fact about round 2.**
Every one of the four feedback inputs comes from the immediately preceding round and from
nowhere else: §1.2 is its rule file, §1.3 is its `metrics.json` reduced plus the audit of its
predictions, §1.4 is a draw over its `errors.jsonl`. Round 2 is that relation at *N* = 2 and
not a special case of it — which is why the round-2 constants `SECOND`/`FIRST` became the
parameter `iteration` and the one local `previous`, computed once at the top of
`run_iteration()` and passed to all six sites that need it. `iteration - 1` written at each of
them would be the loop's off-by-one convention re-derived six times, and DESIGN §5.5 records
`assemble_iteration_prompt` shipping with exactly that relation inverted. Rounds are not
skippable: a round *N* whose *N−1* left no score refuses, so the arm is a chain from round 1
with no gaps by construction.

**A later round makes 1 + N calls and this file adds none of them up.** The Auditor is called
once per dev document (`auditor.md` §1.3), so a round's `llm_calls` is the RuleAuthor's one
plus the fold's N, and the round's total comes from `scorer.sum_costs` over the response cost
blocks (DESIGN §5.5, schema 7). The arm total is `sum_costs` again, over the previous round's
`cost_to_date` read back from `iter{N−1}/metrics.json` and this round's figure. The driver
holds the accumulator and the scorer does the arithmetic: a rung whose driver both decides how
many calls to make and computes the total is a rung pricing itself (§5.5), and `run_fold`
never accumulates either.

**The stopping rule is called here and evaluated in the writer, and neither of those is a
second implementation of it.** A round's `termination` block describes that round, so it needs
that round's dev leak rate — which does not exist until `run_fold` has scored. So this module
assembles `PendingTermination(corpus, previous_leak_rates)` from the rounds' own `metrics.json`
files and hands it over; `run_fold` appends the rate it measured and calls `resolve()`, which
calls `should_stop()` and nothing else. The history is the driver's, the rule is
`src/termination.py`'s, and what crosses the boundary is one float in one direction — the cost
block's arrangement exactly, where the caller assembles and the writer completes with the one
quantity only it has. The decision and the three refused alternatives are DESIGN §5.5's.

**This module obeys the verdict and never adjusts it.** `run_iteration()` returns
`termination` and `stop`, and a round run past `stop` is refused rather than warned about:
`should_stop` itself raises above the ceiling, so an arm that continued would be one whose
next round cannot evaluate its own stopping rule. A ceiling stop is `reason: "ceiling"` and
`converged: false` — that distinction is `Termination.converged`'s, a property derived from
the reason, and nothing here can construct or report the other combination.

**Round 1 is `port-oneshot`'s procedure, and that is a finding rather than a convenience.**
DESIGN §4 defines `port-oneshot` as `port-loop` truncated after call 1, so the two arms'
first calls must be shown the same thing or the truncation is not a truncation and the
comparison "does iterating beat one shot" measures the prompt as well as the loop.
`orchestrate.ONESHOT_SECTIONS` states that in the baseline's own words — "in this arm and in
`port-loop`'s iteration 1 alike" — and this module imports the constant instead of spelling
§§1.1–1.2 again. A local copy is how call 1 of the two arms comes to differ by one block
that nobody chose.

**So the difference is the iteration number, and it goes to two places.** The rule file has
always been iteration-scoped for every arm (`paths.armrules`, DESIGN §5.3), so
`orchestrate.run_arm` already writes `iter1/` and this module writes the same directory
under its own `porting` value. What is new is the *results* path: `run_fold(iteration=1)`
writes an iteration-scoped `metrics.json` and `spans.jsonl` because this arm has rounds, and
DESIGN §5.5 makes that conditional on exactly that — `iter1/` under an arm with one pass is
a false statement about the arm, and un-iterated results under an arm with five rounds are
four results overwritten. `run_arm`'s comment at its `run_fold` call is the other half of
this paragraph and neither file is complete without it.

**What this module reuses from `orchestrate`, and why it imports rather than restates.**
The freeze writer, the call log's line format and appender, the run block, the rule-file
writer and the format-failure record are all `orchestrate`'s, including two private names
(`_write_rules`, `_write_failure`). That follows the judgement `orchestrate` itself makes
when it imports `rules._relative`: a record's *shape* must have one author, because two
writers of `format_failure.json` is two answers to what `FAILURE_SCHEMA` 2 contains and the
schema counter cannot tell you which one you are holding. The alternative — copying the
writers here — would leave the appendix's format-failure story depending on which arm
failed. `paths.armfreeze`, `paths.agentlog` and `paths.formatfailure` all template
`{porting}`, so the imported writers put this arm's records under this arm's directory
without either module knowing about the other's cell.

**`paths.formatfailure` stays arm-scoped, because a format failure ends the arm** (decided
2026-08-14, DESIGN §5.5 — the rationale is there and is not restated in full here). The
short of it: round *n* + 1's §1.2 is round *n*'s rule file and its §1.3 is round *n*'s
score, and a failed round produced neither — the file does not load and `metrics.json` is
deliberately unwritten. So there is no second failure to overwrite the first, and one path
per arm is right for the reason it is right for the freeze record and the call log. A later
round needs no guard for this: the state is unrepresentable, because the first thing
`run_iteration()` reads is every earlier round's `metrics.json` and a failed round wrote
none. One consequence recorded in DESIGN and not fixed here: `FAILURE_SCHEMA` 2 has a `cost`
key and no `cost_to_date`, so a failed round *N*'s arm total is this file's `cost` plus
`iter{N−1}/metrics.json`'s `cost_to_date`. `run_iteration()` returns the value on that branch
even though the record has nowhere to put it, so the caller is not the one left
reconstructing it.

**A later round does not freeze, and what it asserts instead is the same predicate read the
other way.** `freeze_window()` refuses as soon as `called_where()` finds a call line, and round
1 wrote one — so there is nothing for a later round to add to that guard and no version of
calling it that succeeds. What it needs is the *complement*: a round 2 on an arm that has never
called is a round 2 without a round 1, so `arm_has_called()` must be **true** here, and it
is checked. That is not a second condition on the same guard; it refuses a different state,
and the two readings of one predicate are what makes "freeze once per arm" and "round 2 is
not the arm's first call" one fact rather than two.

**Drift is reported and not refused, and round 2 is the first round that can report it.**
DESIGN §5.5 and §6.3: the freeze record is the anchor, the per-line `window_files` hash on
every `agent_calls.jsonl` line is the mid-run drift detector, and `port-oneshot` could not
exercise it because *n* = 1 leaves no "mid". `window_drift()` reports rather than refuses,
for its own reason — a prose edit to the prompt and a change to *n* are different events and
only a person can tell them apart — so its result is returned rather than raised. It is on
round 2's return and not round 1's, for `model_lifecycle`'s reason in `call_line()` rather
than by omission: round 1's freeze is written immediately before its call, so the value
would be an empty list that reports no measurement, and an absent key says "there was no
mid-run to check" where a `[]` would say "the window held".

`model_id` is a parameter from the top and this file spells none, for `orchestrate`'s
reason (DESIGN §10 A2): a recorded id that came from a literal records what the code says
rather than what was called.
"""
from __future__ import annotations

import json

from .. import orchestrate
from ..corpora.base import rule_langs
from ..eval.run_fold import DEFAULT_SPLIT, load_fold, read_errors, read_spans, run_fold
from ..eval.scorer import iter_metrics_path, sum_costs
from ..llm.bedrock import invoke, model_lifecycle
from ..llm.prompt import (
    assemble_audit_prompt, assemble_iteration_prompt, assemble_task_prompt, mask_document,
)
from ..orchestrate import (
    CALLED, FORMAT_FAILURE, ONESHOT_SECTIONS, RULE_AUTHOR, SCORED, OrchestrateError,
    _digest, _run_block, _write_failure, _write_rules, append_call, arm_has_called,
    call_line, freeze_window, window_drift,
)
from ..rules import RuleError, arm_rules_path, load_rules
from ..sample import config as sampling_config, draw
from ..termination import PendingTermination, should_stop
from . import audit

# `ROOT` is deliberately **not** in the import list above and is read as
# `orchestrate.ROOT` at each use. `from ..orchestrate import ROOT` binds the value at import
# time, and every path in this arm is built by the imported writers, which read that module's
# global when they run. The tests redirect it (`monkeypatch.setattr(orchestrate, "ROOT",
# tmp_path)`), so a name bound here would leave the freeze record, the call log and the rule
# file under the redirected root while `run_fold` wrote its metrics into the real `results/`
# tree — one arm split across two roots, and green tests either way. `orchestrate._write_rules`
# passes `root=ROOT` for the same reason stated from the other side.

#: This arm's `porting` value. A literal here and checked against the `porting` axis on use,
#: exactly as `orchestrate.PORTING` is: every path this module builds templates `{porting}`,
#: so the value is a parameter of the path and the literal is the default for the one arm
#: this file drives (CLAUDE.md — naming.yaml's vocabulary, and `_arm_path()` is where the
#: check happens rather than here).
PORTING = "port-loop"

#: The round `run_iteration_1()` runs. Named for `orchestrate.ITERATION`'s reason and one
#: more: it is the value that distinguishes this procedure from the baseline's, so it is the
#: thing a reader of this file is looking for and it should not be a bare `1` inside three
#: call sites.
ITERATION = 1

#: The first round `run_iteration()` can run. Not a parameter's default and not a magic
#: number at the guard: it is `k + 1`-independent and structural — round 1 is
#: `run_iteration_1()`'s, because it freezes the window and shows §§1.3–1.4 empty, and no
#: argument to the general function can make it do that.
FIRST_ITERATED = 2

#: The arm this file drives beyond `porting`. Defaults rather than pins: `R` because a round
#: authors a rule file and rules are what `R` is, `sup-free` because the labels come from
#: placeholder positions (naming.yaml). Same values as the baseline's, which is the point —
#: the two arms differ in `porting` and in nothing else about the cell.
DETECTOR = "R"
SUPERVISION = "sup-free"

#: Which agent spent a call, at both of a round's call sites. `RULE_AUTHOR` is imported from
#: `orchestrate` — its own comment says the constant is a choice of which declared value
#: applies rather than a second definition of it — and the Auditor has no constant there
#: because that module drives an arm that calls no Auditor. So it is declared here, in the one
#: module that calls that agent, and its value is `config/naming.yaml`'s `agent_role` key
#: (CLAUDE.md — the vocabulary is the config's). `call_line()` puts it through
#: `check_agent_role()`, so a rename in the config fails at the write rather than splitting one
#: agent's calls across two spellings in the file every per-role cost figure is computed from.
#: **Nothing derives it** — not from the template filename, not from which assembler produced
#: the prompt (DESIGN §5.5, and §3's layer-from-detector-name prohibition one field over).
AUDITOR = "auditor"


def _check_inputs(corpus: str, lang: str, model_id: str) -> None:
    """Refuse a call that cannot produce a scored file, before the window is frozen.

    The same two conditions `orchestrate.run_arm()` checks inline, and this is a **second
    copy of them**, stated rather than hidden: one function's worth of validation now has two
    homes and a third arm would make three. The right fix is one shared checker, and it is a
    change to `orchestrate.py` — which this task was scoped to leave alone. Whoever adds the
    next arm should do it then rather than adding a fourth copy.

    Both are checked before `freeze_window()` so that a refusal leaves no record behind. A
    freeze taken and then abandoned is a `revision` bump attached to no call, which is
    legible but noisy; a freeze taken and then *called* under a bad `lang` would author a
    rule file no corpus loads, and `revision` would be the only trace.
    """
    if not isinstance(model_id, str) or not model_id:
        raise OrchestrateError(
            "model_id is required and must be a non-empty string. DESIGN §10 A2's "
            "comparison is one arm on two model families, so the id is an argument from the "
            "top down; a default here would be the place it stopped being one, and the "
            "recorded value would then describe this file rather than the call."
        )
    if lang not in rule_langs(corpus):
        raise OrchestrateError(
            f"{corpus} does not load a {lang!r} rule file (config/naming.yaml "
            f"corpus_rule_langs: {rule_langs(corpus)}). One call authors one file, and a "
            "file no corpus loads would be scored by nothing (DESIGN §5.2)."
        )


def run_iteration_1(*, corpus: str, lang: str, model_id: str,
                    detector: str = DETECTOR, supervision: str = SUPERVISION,
                    porting: str = PORTING, split: str = DEFAULT_SPLIT,
                    max_tokens: int | None = None, client=None,
                    control_client=None) -> dict:
    """Round 1 of `port-loop`: freeze, assemble, call once, validate, score or record.

    Returns what happened, as a mapping with `iteration`, `outcome` (`SCORED` or
    `FORMAT_FAILURE`), the cost block, the run block and the paths written — `run_arm()`'s
    shape plus the round number, so a caller that already reads one arm's result reads this
    one, and the field that differs is named rather than inferred from a path. Nothing in it
    is a summary that has to be trusted: every value is also on disk.

    The order is `orchestrate.run_arm()`'s and every step's position is load-bearing there
    for reasons that do not change here:

    1. **Freeze the window** immediately before the call (DESIGN §6.3, "freeze last"), with
       `sections=ONESHOT_SECTIONS` — §§1.1–1.2, imported from the baseline so call 1 of the
       two arms is shown the same blocks by construction. The record states §§1.3–1.4 empty
       and `sampling_applied: false`, which is true of this round: there is no previous
       iteration to draw a score or an error sample from.
    2. **Assemble §§1.1–1.2**, with no `rules_path`. §1.2 is the empty state, for
       `run_arm()`'s reason — passing `rules/{lang}.yaml` would show the agent the committed
       format example as though it were a rule set the arm had authored (DESIGN §5.3). Round
       2 is the first call with a rule file to show, and it will be *this round's output*
       under `paths.armrules`, never the bootstrap file.
    3. **Probe the lifecycle, then call once.** The probe is a control-plane lookup, makes no
       inference and is not in `cost`; counting it in `llm_calls` would make this arm's spend
       incomparable to the baseline's for a reason unrelated to either. It goes first so that
       anything surprising happens while the round can still be rerun.
    4. **Log the call before the response is judged**, with `iteration` and `role` both
       passed explicitly. `call_line()`'s docstring asks the iterating driver for exactly
       that: its defaults are the baseline's one call, and an arm whose later rounds carry an
       Auditor cannot inherit a role default that is right for one of its two agents.
       `sample_reference` is null here because §1.4 is empty in round 1 — the same null the
       baseline writes, and for the same reason rather than by inheriting its default, so it
       is passed rather than omitted.
    5. **Validate by loading**, through the loader `run_fold` will use, so "it validated" and
       "it will load when scored" cannot come apart. Zero format retries and no repair on the
       way in (DESIGN §10 A2): a fence-stripping step is a retry with the count still reading
       zero.
    6. **Score, or record the failure.** `run_fold(iteration=1)` on success — iteration-
       scoped results because this arm has rounds (§5.5) — and `paths.formatfailure` on a
       `RuleError`, with `metrics.json` left unwritten, because zeros there would read as a
       rule set that ran and caught nothing. The cost block is written in **both** branches:
       the call was made and paid for either way.

    A format failure in round 1 ends this function and leaves the arm with no rule file that
    loads, **and as of 2026-08-14 it ends the arm** — the question this docstring left to
    round 2 is answered in the module docstring above and in DESIGN §5.5, and the mechanism
    is that `run_iteration()` reads every earlier round's `metrics.json` first and this branch
    writes none.
    Nothing changes here: what is on disk after this returns is the response verbatim, the
    validator's message and the cost, which is what the appendix reports either way.

    `client` and `control_client` are the two transport seams, passed through unexamined.
    They are separate because `converse` is on `bedrock-runtime` and `GetFoundationModel` is
    not, so one object cannot stand in for both, and they exist so a test of this function
    does not reach AWS.
    """
    _check_inputs(corpus, lang, model_id)

    freeze_window(corpus, detector, supervision, porting, sections=ONESHOT_SECTIONS)

    prompt = assemble_task_prompt(lang=lang, corpus=corpus)
    reference = prompt.reference()

    lifecycle = model_lifecycle(model_id, client=control_client)

    kwargs = {} if max_tokens is None else {"max_tokens": max_tokens}
    response = invoke(prompt, model_id=model_id, client=client, **kwargs)
    cost = response.cost()
    model = response.model_record()

    # Before the response is judged (the module docstring's step 4). `iteration`, `role` and
    # `sample_reference` are all passed rather than defaulted — see the docstring; the values
    # coincide with `call_line()`'s defaults in round 1 and stop coinciding in round 2, which
    # is why the coincidence is not relied on.
    append_call(
        call_line(ITERATION, prompt_reference=reference, model=model,
                  response_chars=len(response.text),
                  response_sha256=_digest(response.text),
                  outcome=CALLED, cost=cost, model_lifecycle=lifecycle,
                  role=RULE_AUTHOR, sample_reference=None),
        corpus, detector, supervision, porting,
    )

    run = _run_block(corpus, detector, supervision, porting, split, model)
    rules_file = _write_rules(response.text, corpus=corpus, detector=detector,
                              supervision=supervision, porting=porting, lang=lang,
                              iteration=ITERATION)
    try:
        load_rules(lang, path=rules_file)
    except RuleError as exc:
        failure = _write_failure(
            corpus=corpus, detector=detector, supervision=supervision, porting=porting,
            split=split, model=model, response=response.text, error=str(exc),
            rules_path=rules_file, cost=cost, prompt_reference=reference,
            model_lifecycle=lifecycle,
        )
        return {
            "iteration": ITERATION,
            "outcome": FORMAT_FAILURE,
            "run": run,
            "cost": cost,
            "rules_path": rules_file,
            "failure_path": failure,
            # Named as absent rather than omitted, for `sample_reference`'s reason: a caller
            # branching on a missing key branches on a typo just as readily.
            "metrics_path": None,
            "spans_path": None,
        }

    # `iteration=ITERATION` — the one line where this arm's procedure differs from the
    # baseline's. `run_arm()` passes no iteration and writes the un-iterated results path,
    # because `iter1/` under an arm with one pass is a false statement about the arm; this arm
    # has rounds, so its results are round-scoped and round 2 does not overwrite round 1
    # (DESIGN §5.5). The rule file above is iteration-scoped in *both* arms and for a
    # different reason — §5.3, so two arms do not share one input file.
    #
    # The model record and the cost go through `run_fold` rather than being written over its
    # metrics afterwards: it owns the one write of `metrics.json`, and a second writer
    # patching that file is a second answer to what the run block contains.
    spans_file, metrics_file, scored = run_fold(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        split=split, rules={lang: rules_file}, model_record=model, cost=cost,
        model_lifecycle=lifecycle, iteration=ITERATION, root=orchestrate.ROOT,
    )
    return {
        "iteration": ITERATION,
        "outcome": SCORED,
        "run": run,
        "cost": cost,
        "rules_path": rules_file,
        "failure_path": None,
        "metrics_path": metrics_file,
        "spans_path": spans_file,
        "scored": scored,
    }


# ─── rounds 2 and up: the feedback rounds ────────────────────────────────────


def _previous_round(*, corpus: str, detector: str, supervision: str, porting: str,
                    iteration: int) -> dict:
    """Read round `iteration`'s `metrics.json` back. Its score is the next round's §1.3.

    **Read from the round's own file and never from the un-iterated copy.** That copy is
    whichever round ran last (DESIGN §5.5), so a reader pointed at it answers "which round's
    score is this" with "the most recent one" — `read_spans`' argument for a required
    `iteration`, at the one file that has no such reader of its own. `iter_metrics_path` is the
    single builder of the path and this function does not format the template again.

    **It is also this arm's format-failure guard, and it is the guard by being the first
    read.** A round that failed validation wrote `format_failure.json` and *not* `metrics.json`
    (`orchestrate._write_failure` — zeros there would read as a rule set that ran and caught
    nothing), so a next round after a failed one stops here, on the absence of the file its
    §1.3 comes from. Nothing separate has to detect it: DESIGN §5.5's decision that a format
    failure ends the arm is not enforced by a flag anywhere, it is the shape of what the failing
    round did and did not write.

    `json` is imported for this and for nothing else in this module. The alternative was a
    reader in `scorer.py` beside `write_metrics`, which is where `read_spans` and `read_errors`
    live relative to their writers and is the better home — and it is a change to a module this
    round was not scoped to touch. Stated rather than done, like `_check_inputs`' duplication;
    `_leak_rates()` below is the second caller that argument predicted, and it goes through this
    function rather than opening the files itself, so there is still one reader in this module.
    """
    path = iter_metrics_path(corpus=corpus, detector=detector, supervision=supervision,
                             porting=porting, iteration=iteration, root=orchestrate.ROOT)
    if not path.exists():
        raise OrchestrateError(
            f"{corpus}/{detector}/{supervision}/{porting}: no score for round {iteration}, so "
            f"round {iteration + 1} has no §1.3 to show. Either that round has not run, or it "
            "ended in a format failure — which writes format_failure.json and no metrics.json, "
            "and ends the arm (DESIGN §5.5). A round assembled without the previous score would "
            "be a weaker arm than the one being reported."
        )
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _leak_rates(*, corpus: str, detector: str, supervision: str, porting: str,
                through: int) -> tuple[list[float], dict]:
    """Rounds 1..`through`'s headline dev leak rates, and round `through`'s whole metrics.

    The stopping rule is a threshold on differences of this sequence (DESIGN §3), so it needs
    every round's rate and not just the last two: `improvements` is published in full in the
    `termination` block, and a sequence assembled from the last k+1 rounds would make the
    record's own audit trail depend on the value of k in force when it was written.

    **Read from each round's own file, one round at a time, through `_previous_round()`.** The
    arm is a chain and every link is on disk, so the history is recoverable after the run and
    is not something a driver has to have carried in memory across N rounds — which is
    `read_spans`' argument (§5.5) at the one artefact whose whole point is a sequence. A round
    missing its score refuses there, which is also what makes this the gap check: rounds are
    contiguous from 1, and an arm that somehow skipped one cannot assemble a history that
    silently omits it.

    **The rate is the `fully_covered` headline, read by key.** CLAUDE.md makes that the
    headline value and the relaxed figure the lower bound; §3's rule is on the headline. Reading
    `headline.leak_rate.value` takes whichever mode the scorer published rather than naming a
    mode here, so the rule's input cannot drift from what the file calls its headline.

    Returns the rates and the last round's metrics together, because the caller needs both and
    reading the file twice would be two answers to what round `through` scored.
    """
    rates: list[float] = []
    metrics: dict = {}
    for previous in range(1, through + 1):
        metrics = _previous_round(corpus=corpus, detector=detector, supervision=supervision,
                                  porting=porting, iteration=previous)
        try:
            rate = metrics["headline"]["leak_rate"]["value"]
        except (KeyError, TypeError) as exc:
            raise OrchestrateError(
                f"{corpus}/{detector}/{supervision}/{porting}: round {previous}'s metrics.json "
                f"has no headline.leak_rate.value ({exc!r}), so the termination rule has no "
                "sequence to difference (DESIGN §3). A round whose leak rate cannot be read is "
                "a round the arm cannot be stopped on."
            ) from exc
        rates.append(float(rate))
    return rates, metrics


def _audit_fold(documents, predictions, *, corpus: str, iteration: int, model_id: str,
                max_tokens: int | None, client, control_client,
                detector: str, supervision: str, porting: str) -> tuple[dict, list[dict]]:
    """The Auditor over one fold: one call per document. Returns (report, cost blocks).

    **One call per document is `auditor.md` §1.3's decision and this function is where the
    fold's N shows up in the arm's cost.** Recall degrades along a very long context, which
    would make the per-document flag rate a function of position in a batch; a failed call
    loses one document rather than the fold; and `doc_id` never comes from the agent, because
    the caller knows which document it sent — `parse_response` takes it as a keyword for that
    reason and this function passes the harness's value.

    **A document with no predictions is still audited.** Masking it produces a document with
    no tags, which is what the arm's round-1 output *was* for that document, and the flags that
    come back are the whole of what leaked there. Skipping it would make `documents_audited`
    a count of the documents the arm happened to fire on, and `documents_with_no_flags` — the
    field that exists so "audited and nothing survived" is distinguishable from "not audited" —
    would be measuring a different denominator than it says.

    **Every response is logged and every cost is returned, including a refused one.** A
    malformed response becomes a `malformed` refusal rather than an exception
    (`audit.parse_response`), so the round continues; the call was made and paid for, so its
    cost is in the round's total and its line is in `agent_calls.jsonl` with
    `outcome=CALLED`. `role=AUDITOR` is passed explicitly and `sample_reference` is null,
    which is this agent's structure rather than the default leaking through: `auditor.md` §5
    gives the Auditor no sample.

    The lifecycle probe is **not** repeated per document. It is a control-plane lookup of a
    model id, the id does not change between calls, and N probes would put N control-plane
    round trips in a round whose cost comparison is about inference. The one record is passed
    in and attached to every line, which is what `model_lifecycle` claims to be — a note about
    the id, attached to the calls that used it.
    """
    lifecycle = model_lifecycle(model_id, client=control_client)
    kwargs = {} if max_tokens is None else {"max_tokens": max_tokens}

    by_doc: dict[str, list] = {}
    for span in predictions:
        by_doc.setdefault(span.doc_id, []).append(span)

    audits = []
    costs = []
    for document in documents:
        masked = mask_document(document, by_doc.get(document.doc_id, []))
        prompt = assemble_audit_prompt(corpus=corpus, masked=masked)
        response = invoke(prompt, model_id=model_id, client=client, **kwargs)
        costs.append(response.cost())
        append_call(
            call_line(iteration, prompt_reference=prompt.reference(),
                      model=response.model_record(),
                      response_chars=len(response.text),
                      response_sha256=_digest(response.text),
                      outcome=CALLED, cost=response.cost(), model_lifecycle=lifecycle,
                      role=AUDITOR, sample_reference=None),
            corpus, detector, supervision, porting,
        )
        audits.append(audit.parse_response(
            response.text, doc_id=masked.doc_id, lines=masked.lines))

    # `masked_from_iteration` is stated, not derived from a directory listing (DESIGN §5.5).
    # `audit.report()` checks the pair agrees with itself, which an off-by-one driver satisfies
    # because it records what it was told; the numbers are the round being run and the round
    # whose predictions were masked, and the consumer — `prompt._audit_block` — checks both
    # against its own round.
    return audit.report(audits, corpus=corpus, iteration=iteration,
                        masked_from_iteration=iteration - 1), costs


def _written_termination(metrics_file) -> dict:
    """The `termination` block out of the file `run_fold` just wrote.

    The round's verdict exists in exactly one place — the metrics file — because `run_fold`
    resolved it there. Reading it back is how this driver reports it without becoming a second
    place it is computed: a `should_stop()` call here over the same rates plus this round's
    would be a second answer to "did the arm stop", and the two could disagree while each was
    internally consistent. Same argument as `check_cost_to_date`'s and §5.5's one-writer rule,
    at the field that decides whether there is a next round.

    A missing block is refused rather than defaulted: `scorer.REQUIRED_TERMINATION` makes it
    mandatory, so its absence means the file is not the one this round wrote.
    """
    with open(metrics_file, encoding="utf-8") as fh:
        written = json.load(fh)
    block = written.get("termination")
    if not isinstance(block, dict) or "reason" not in block:
        raise OrchestrateError(
            f"the metrics file just written carries no usable termination block "
            f"({type(block).__name__}), so this round cannot say whether the arm continues. "
            "The block is required by the scorer (DESIGN §3) and is resolved inside "
            "`run_fold`; its absence means this is not the file this round wrote."
        )
    return block


def run_iteration(iteration: int, *, corpus: str, lang: str, model_id: str,
                  detector: str = DETECTOR, supervision: str = SUPERVISION,
                  porting: str = PORTING, split: str = DEFAULT_SPLIT,
                  max_tokens: int | None = None, client=None,
                  control_client=None) -> dict:
    """Round `iteration` of `port-loop`, for any `iteration` ≥ 2: audit, show four blocks,
    call, score, and record where the stopping rule stands.

    **One function for rounds 2, 3 and 8, because they are one procedure.** What changes
    between them is the round number and the length of the history, and both are arguments or
    read from disk. A `run_iteration_3()` would be a second copy of this body whose drift from
    it is undetectable — the two would produce differently-assembled prompts under the same
    `porting` value, and every result would still look right. `iteration` is positional and
    first: it is the fact about the call a reader needs before any of the axes, and there is no
    default, because a default round number is a round chosen by whichever caller forgot.

    Returns `run_iteration_1()`'s shape with five keys added — `audit_report_path`,
    `window_drift`, `cost_to_date`, `termination` and `stop` — so a caller that reads one round
    reads all of them and the fields that differ are named rather than inferred. Nothing in it
    is a summary that has to be trusted: every value is also on disk.

    The steps, and what each one is not allowed to do:

    1. **Refuse a round that is not a later round, assert the arm has called, do not freeze.**
       `iteration` must be ≥ 2 (`FIRST_ITERATED`): round 1 freezes the window and shows
       §§1.3–1.4 empty, and no argument here makes this function do that. `freeze_window()`
       refuses as soon as `called_where()` finds a call line and round 1 wrote one, so there is
       no call to it here that succeeds — the freeze is once per arm (DESIGN §5.5, §6.3). What
       this checks is the complement: `arm_has_called()` must be **true**, because a later round
       on an arm with no call is a round without a predecessor. Then `window_drift()`, which
       **reports rather than refuses**: an edit to the prompt's prose and a change to *n* are
       different events and only a person can tell them apart, so the list is returned and this
       function continues. Round 2 is the first round that can report drift at all —
       `port-oneshot` has one line and no "mid" to drift in.
    2. **Read every previous round's score.** `iter{1..N−1}/metrics.json` through
       `_leak_rates()`, which returns the leak-rate sequence for the stopping rule and round
       *N−1*'s metrics for §1.3. The absence of any of those files is what stops a round after
       a format failure, and it is also the no-gaps check. Read from each round's own path and
       never the un-iterated copy, which is whichever round ran last.
    3. **Refuse a round the rule has already stopped.** `should_stop()` over that sequence: if
       the arm converged at round *N−1* or hit the ceiling, round *N* does not run. This is
       obeying the pre-registered rule rather than re-deciding it — the verdict comes from
       `src/termination.py`, and `should_stop` itself raises above the ceiling, so an arm that
       ran on would be one whose next round cannot evaluate its own stopping rule.
    4. **Read round *N−1*'s predictions and audit the fold.** `read_spans(iteration=N−1)` from
       disk rather than threaded through memory (§5.5): the masker's input is a file somebody
       can diff against the flags that came back, and what it returns has no gold in it to
       mask. Then one Auditor call per dev document, and the report is written to
       `audit.report_path(iteration=N)` — deny-listed, because it is a map of the identifiers
       the previous round did not catch.
    5. **Read round *N−1*'s errors and draw §1.4's sample.** `read_errors(iteration=N−1)` then
       `sample.draw(..., iteration=N)`. The draw is seeded on (corpus, iteration) and is the
       caller's, not the assembler's: an assembler that drew would be a second place the seed
       is applied, and DESIGN §11.1 rests on both arms drawing through one function.
    6. **Assemble all four blocks and call the RuleAuthor.** `assemble_iteration_prompt`
       requires all five feedback inputs at a round ≥ 2 and refuses a round that silently
       dropped one, because an absent block is an unrecorded change of arm. §1.2 is *round
       N−1's* rule file under `paths.armrules` and never `rules/{lang}.yaml`, which is the
       bootstrap format example (§5.3). `role` and `sample_reference` are both passed: the
       sample's reference is `render_window()`'s, which is what makes the line say which 40
       spans this call was shown.
    7. **Score with a pending termination block, or record the failure.**
       `run_fold(iteration=N)` with this round's `cost`, the arm's `cost_to_date`, and
       `PendingTermination(corpus, rounds 1..N−1's rates)` — which the writer completes with
       the rate it measures. `paths.formatfailure` on a `RuleError`, arm-scoped: no earlier
       round can have written one and reached here, so there is nothing to overwrite (§5.5).

    **How the `termination` block is written, which is the question round 2 left open.** Round
    2 passed nothing and `run_fold` wrote `not_applicable` — round 1's record at a round where
    it was no longer true. The obstacle was real: `should_stop(corpus, leak_rates)` needs *this*
    round's dev leak rate, which does not exist until `run_fold` has scored, and `run_fold`
    takes the block as an argument because it cannot see whether the rules it is scoring came
    from round 1 of 8 or from a converged loop. That is the `final=True` impossibility of DESIGN
    §5.5 at a different argument: the caller cannot compute what the writer needs before the
    writer runs.

    The three ways out stay refused — scoring twice is a second scoring pass, patching
    `metrics.json` afterwards is a second writer of a published file, and having `run_fold` call
    the rule is a second home for a pre-registered decision (§5.5, §5.5's one-writer rule, §3).
    The fourth is to send the **missing argument** instead of the answer:
    `PendingTermination(corpus, previous_leak_rates)` carries everything the driver knows, and
    `run_fold` completes it with the rate it just measured. None of the three constraints is
    relaxed by it. There is one scoring pass, because the rate comes from that pass rather than
    from another one. There is one writer, because the block is completed before the file is
    written rather than edited after. And there is one implementation of the rule, because
    `resolve()` calls `should_stop()` and `run_fold` does not import it — the history stays here
    and one float crosses the boundary. It is the cost block's arrangement, which already sends
    a partial block for the writer to finish with `elapsed`: the precedent is in the same
    function's same argument list.

    **The cost is 1 + N and this function adds none of it up.** `sum_costs` totals the
    RuleAuthor's one block and the Auditor's N; `sum_costs` again puts round *N−1*'s
    `cost_to_date` beside this round's figure for the arm total. The driver holds the
    accumulator and the scorer does the arithmetic, because a rung whose driver both decides how
    many calls to make and computes its own total is a rung pricing itself (DESIGN §5.5);
    `run_fold` never accumulates either, and `check_cost_to_date` refuses a total below the
    round.

    **A ceiling stop is not convergence and this function cannot make it look like one.** The
    returned `termination` is `Termination.record()`'s dict, whose `reason` is `ceiling` when the
    cap was reached without k consecutive below-δ rounds and whose `converged` is a property
    derived from that reason — so there is no state in which the record says `ceiling` and
    `converged: true`, and nothing here computes either field. `stop` is returned beside it so a
    caller loops on the verdict rather than on a round count, and the same block is in
    `iter{N}/metrics.json` and in the un-iterated copy: the reason a run ended is in the
    published file, not only in this return value.

    `client` and `control_client` are the two transport seams, passed through unexamined, for
    round 1's reason.
    """
    _check_inputs(corpus, lang, model_id)

    if not isinstance(iteration, int) or isinstance(iteration, bool) \
            or iteration < FIRST_ITERATED:
        raise OrchestrateError(
            f"iteration is {iteration!r}, and this function runs rounds "
            f"{FIRST_ITERATED} and up. Round {ITERATION} is `run_iteration_1()`'s: it freezes "
            "the window and shows §§1.3-1.4 empty, which is what makes it byte-identical to "
            "`port-oneshot`'s one call (DESIGN §4), and no argument here reproduces that."
        )

    # The complement of the freeze guard, not a second copy of it — see the module docstring.
    if not arm_has_called(corpus, detector, supervision, porting):
        raise OrchestrateError(
            f"{corpus}/{detector}/{supervision}/{porting}: this arm has made no call, so "
            f"there is no round {iteration - 1} for round {iteration} to iterate from. Round "
            f"{iteration}'s §§1.2-1.4 are the previous round's rule file, score and errors; "
            "run run_iteration_1() first. This is `freeze_window()`'s condition read the "
            "other way and not a second guard on it: that one refuses a re-freeze after a "
            "call, and this refuses a later round before one (DESIGN §5.5, §6.3)."
        )
    # Reported, never raised (`orchestrate.window_drift`), and on the return value so a reader
    # of the round has it beside the cost. An edit to `rule_author.md`'s prose between two rounds
    # is a different event from an edit to `n` in `config/sampling.yaml`, and this function
    # cannot tell them apart.
    drift = window_drift(corpus, detector, supervision, porting)

    previous = iteration - 1
    # Every earlier round's rate for the rule, and round N−1's whole score for §1.3. One read
    # per round through `_previous_round`, so a missing or failed round refuses there.
    previous_rates, previous_metrics = _leak_rates(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        through=previous,
    )

    # Obeying the verdict, not re-deciding it: the rule is `src/termination.py`'s and this reads
    # it. A round after a stop would also be a round `should_stop` cannot evaluate — it raises
    # above the ceiling — so continuing would put the arm outside what §3 pre-registered.
    stopped = should_stop(corpus, previous_rates)
    if stopped.stop:
        raise OrchestrateError(
            f"{corpus}/{detector}/{supervision}/{porting}: the arm stopped at round "
            f"{stopped.iterations} with reason {stopped.reason!r} (delta={stopped.delta:.6f}, "
            f"k={stopped.k}, ceiling={stopped.ceiling}), so round {iteration} does not run. "
            "The stopping rule is pre-registered (DESIGN §3) and this driver obeys it; a round "
            "past the stop is a round whose own termination block the rule cannot evaluate."
        )

    predictions = read_spans(corpus=corpus, detector=detector, supervision=supervision,
                             porting=porting, iteration=previous, root=orchestrate.ROOT)
    documents = load_fold(corpus, split)
    report, audit_costs = _audit_fold(
        documents, predictions, corpus=corpus, iteration=iteration, model_id=model_id,
        max_tokens=max_tokens, client=client, control_client=control_client,
        detector=detector, supervision=supervision, porting=porting,
    )
    report_file = audit.report_path(corpus=corpus, detector=detector,
                                    supervision=supervision, porting=porting,
                                    iteration=iteration, root=orchestrate.ROOT)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")

    # §1.4: the previous round's errors, drawn here rather than in the assembler. `practice`
    # is not passed — `check_iteration` refuses a real arm the reserved band and this is a real
    # arm, so the default is the fact rather than a default (`sample.check_iteration`).
    sample = draw(
        read_errors(corpus=corpus, detector=detector, supervision=supervision,
                    porting=porting, iteration=previous, root=orchestrate.ROOT),
        corpus, iteration,
    )

    # §1.2 is *this arm's* round-1 output and never `rules/{lang}.yaml` (DESIGN §5.3). Built
    # through `arm_rules_path` rather than kept from round 1's return value, because a round is
    # runnable on its own and a path passed between processes is a path neither of them checked.
    previous_rules = arm_rules_path(corpus=corpus, detector=detector,
                                    supervision=supervision, porting=porting,
                                    iteration=previous, lang=lang, root=orchestrate.ROOT)

    prompt = assemble_iteration_prompt(
        lang=lang, corpus=corpus, iteration=iteration, rules_path=previous_rules,
        metrics=previous_metrics, errors=sample,
        docs_by_id={d.doc_id: d for d in documents},
        context_chars=sampling_config()["context_chars"],
        # The report as `audit.report()` assembled it, passed through unmodified: both of its
        # numbers are taken as given and `prompt._audit_block` checks both against this round.
        audit_report=report,
    )
    reference = prompt.reference()

    lifecycle = model_lifecycle(model_id, client=control_client)
    kwargs = {} if max_tokens is None else {"max_tokens": max_tokens}
    response = invoke(prompt, model_id=model_id, client=client, **kwargs)
    model = response.model_record()

    # The round's spend: the RuleAuthor's one call and the Auditor's N, added by the scorer.
    # `llm_calls` therefore reads 1 + N, which is what the round cost (DESIGN §5.5).
    cost = sum_costs([response.cost(), *audit_costs])
    # And the arm's, from the previous round's published total plus this round's. The
    # accumulator is the driver's and the addition is still the scorer's; `run_fold` writes
    # both blocks and `check_cost_to_date` refuses a total below the round it contains.
    cost_to_date = sum_costs([previous_metrics["cost_to_date"], cost])

    # Before the response is judged (round 1's step 4). `role` and `sample_reference` are the
    # two values that stop coinciding with `call_line()`'s defaults at this round — the second
    # is `render_window()`'s reference, nested inside the prompt's, which is what says which
    # spans this call was shown. The cost on the line is the RuleAuthor's own call and not the
    # round's total: a log line prices the call it records.
    append_call(
        call_line(iteration, prompt_reference=reference, model=model,
                  response_chars=len(response.text),
                  response_sha256=_digest(response.text),
                  outcome=CALLED, cost=response.cost(), model_lifecycle=lifecycle,
                  role=RULE_AUTHOR, sample_reference=reference["error_spans"]),
        corpus, detector, supervision, porting,
    )

    run = _run_block(corpus, detector, supervision, porting, split, model)
    rules_file = _write_rules(response.text, corpus=corpus, detector=detector,
                              supervision=supervision, porting=porting, lang=lang,
                              iteration=iteration)
    try:
        load_rules(lang, path=rules_file)
    except RuleError as exc:
        failure = _write_failure(
            corpus=corpus, detector=detector, supervision=supervision, porting=porting,
            split=split, model=model, response=response.text, error=str(exc),
            rules_path=rules_file, cost=cost, prompt_reference=reference,
            model_lifecycle=lifecycle,
        )
        return {
            "iteration": iteration,
            "outcome": FORMAT_FAILURE,
            "run": run,
            "cost": cost,
            "cost_to_date": cost_to_date,
            "rules_path": rules_file,
            "failure_path": failure,
            "metrics_path": None,
            "spans_path": None,
            "audit_report_path": report_file,
            "window_drift": drift,
            # `None` and not a verdict: this round produced no leak rate, so the rule has
            # nothing to evaluate about it, and `stop` is `True` because the arm is over —
            # a format failure ends it (DESIGN §5.5) and that is not one of §3's three
            # reasons, which are the *rule's* endings. A caller looping on `stop` halts
            # either way; a caller asking *why* reads `outcome`, and `format_failure.json`
            # is the record. Reporting `ceiling` or a null `reason` here would put a
            # stopping-rule verdict on a round the rule never saw.
            "termination": None,
            "stop": True,
        }

    # The stopping rule, with the one argument the driver cannot have: this round's leak rate,
    # which `run_fold` appends after it scores (DESIGN §5.5, §3). The history is rounds 1..N−1's
    # rates, read from their own files; `resolve()` calls `should_stop` and this module does not
    # compute the verdict twice — `stopped` above is the *previous* rounds' verdict, which is
    # why this round ran at all.
    spans_file, metrics_file, scored = run_fold(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        split=split, rules={lang: rules_file}, model_record=model, cost=cost,
        cost_to_date=cost_to_date, model_lifecycle=lifecycle,
        termination=PendingTermination(corpus=corpus,
                                       previous_leak_rates=tuple(previous_rates)),
        iteration=iteration, root=orchestrate.ROOT,
    )
    # Read back out of what was written rather than recomputed here. `run_fold` resolved the
    # block and put it in both copies of `metrics.json`; a second `should_stop()` call in this
    # function would be a second answer to "did the arm stop", and the two could disagree while
    # each looked right. `stop` is derived from `reason` for `Termination.converged`'s reason —
    # one field, so `reason: ceiling` cannot travel beside a claim of convergence.
    termination = _written_termination(metrics_file)
    return {
        "iteration": iteration,
        "outcome": SCORED,
        "run": run,
        "cost": cost,
        "cost_to_date": cost_to_date,
        "rules_path": rules_file,
        "failure_path": None,
        "metrics_path": metrics_file,
        "spans_path": spans_file,
        "audit_report_path": report_file,
        "window_drift": drift,
        "scored": scored,
        "termination": termination,
        "stop": termination["reason"] is not None,
    }
