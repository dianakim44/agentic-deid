"""The `port-loop` orchestrator, rounds 1 and 2.

`port-loop` is the iterating rung: the RuleAuthor writes `rules/{lang}.yaml`, the fold is
scored, and the score and its error spans come back as §§1.3–1.4 of the next call (DESIGN
§4). **This module implements rounds 1 and 2 and nothing else.** Round 3 onwards is not
here and is not stubbed here either. A stub would be a second place a round gets designed,
and the first place is DESIGN.

**What round 2 adds is feedback, and it is four inputs rather than one.** Round 1 is shown
§§1.1–1.2 and states §§1.3–1.4 empty; round 2 fills all four, and `assemble_iteration_prompt`
refuses a round ≥ 2 that is missing any of `metrics`, `errors`, `docs_by_id`,
`context_chars` or `audit_report`. So the round's shape is fixed by that signature rather
than by this file: the Auditor runs first over round 1's predictions masked, its report and
round 1's score become §1.3, a seeded stratified draw over round 1's `errors.jsonl` becomes
§1.4, and only then is the RuleAuthor called. Everything after that call — write, validate by
loading, score or record the failure — is round 1's procedure unchanged, which is why the two
functions below converge on the same six lines.

**Round 2 makes 1 + N calls and this file adds none of them up.** The Auditor is called once
per dev document (`auditor.md` §1.3), so a round's `llm_calls` is the RuleAuthor's one plus
the fold's N, and the round's total comes from `scorer.sum_costs` over the response cost
blocks (DESIGN §5.5, schema 7). The arm total is `sum_costs` again, over round 1's
`cost_to_date` read back from `iter1/metrics.json` and this round's figure. The driver holds
the accumulator and the scorer does the arithmetic: a rung whose driver both decides how many
calls to make and computes the total is a rung pricing itself (§5.5), and `run_fold` never
accumulates either.

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
per arm is right for the reason it is right for the freeze record and the call log. Round
2 does not need a guard for this: the state is unrepresentable, because the first thing it
does is read `iter1/metrics.json` and a failed round 1 wrote none. One consequence recorded
in DESIGN and not fixed here: `FAILURE_SCHEMA` 2 has a `cost` key and no `cost_to_date`, so
a round-2 failure's arm total is this file's `cost` plus `iter1/metrics.json`'s
`cost_to_date`. `run_iteration_2()` returns the value on that branch even though the record
has nowhere to put it, so the caller is not the one left reconstructing it.

**Round 2 does not freeze, and what it asserts instead is the same predicate read the other
way.** `freeze_window()` refuses as soon as `called_where()` finds a call line, and round 1
wrote one — so there is nothing for round 2 to add to that guard and no version of calling
it that succeeds. What round 2 needs is the *complement*: a round 2 on an arm that has never
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

#: The round `run_iteration_2()` runs, and the round its feedback is *from* — two names for
#: two facts rather than one plus arithmetic. `SECOND` is the round being run: the number in
#: the rule path, the results directory, the call lines and the audit report. `FIRST` is where
#: §§1.2–1.4 come from: the rule file shown, the score reduced into §1.3, the predictions the
#: Auditor reads masked and the errors §1.4 draws from. `SECOND - 1` at each of those sites
#: would be the loop's off-by-one convention re-derived nine times, and DESIGN §5.5 records
#: that `assemble_iteration_prompt` shipped with exactly that relation inverted.
SECOND = 2
FIRST = SECOND - 1

#: The arm this file drives beyond `porting`. Defaults rather than pins: `R` because a round
#: authors a rule file and rules are what `R` is, `sup-free` because the labels come from
#: placeholder positions (naming.yaml). Same values as the baseline's, which is the point —
#: the two arms differ in `porting` and in nothing else about the cell.
DETECTOR = "R"
SUPERVISION = "sup-free"

#: Which agent spent a call, at both of round 2's call sites. `RULE_AUTHOR` is imported from
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
    is that `run_iteration_2()` reads `iter1/metrics.json` first and this branch writes none.
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


# ─── round 2: the feedback round ─────────────────────────────────────────────


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
    round was not scoped to touch. Stated rather than done, like `_check_inputs`' duplication:
    whoever needs round 3's leak-rate sequence will read every round's metrics and should move
    it then rather than adding a second copy here.
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
    # because it records what it was told; the numbers are this module's `SECOND`/`FIRST`, and
    # the consumer — `prompt._audit_block` — checks both against its own round.
    return audit.report(audits, corpus=corpus, iteration=iteration,
                        masked_from_iteration=iteration - 1), costs


def run_iteration_2(*, corpus: str, lang: str, model_id: str,
                    detector: str = DETECTOR, supervision: str = SUPERVISION,
                    porting: str = PORTING, split: str = DEFAULT_SPLIT,
                    max_tokens: int | None = None, client=None,
                    control_client=None) -> dict:
    """Round 2 of `port-loop`: audit round 1, show all four blocks, call, score.

    Returns `run_iteration_1()`'s shape with three keys added — `audit_report_path`,
    `window_drift` and `cost_to_date` — so a caller that reads one round reads both and the
    fields that differ are named rather than inferred. Nothing in it is a summary that has to
    be trusted: every value is also on disk.

    The steps, and what each one is not allowed to do:

    1. **Assert the arm has called; do not freeze.** `freeze_window()` refuses as soon as
       `called_where()` finds a call line and round 1 wrote one, so there is no call to it here
       that succeeds — the freeze is once per arm (DESIGN §5.5, §6.3). What this checks is the
       complement: `arm_has_called()` must be **true**, because a round 2 on an arm with no call
       is a round 2 without a round 1. Then `window_drift()`, which **reports rather than
       refuses**: an edit to the prompt's prose and a change to *n* are different events and
       only a person can tell them apart, so the list is returned and this function continues.
       Round 2 is the first round that can report drift at all — `port-oneshot` has one line and
       no "mid" to drift in.
    2. **Read round 1's score.** `iter1/metrics.json`, through `_previous_round()`, and the
       absence of that file is what stops a round after a format failure. Read from the round's
       own path and never the un-iterated copy, which is whichever round ran last.
    3. **Read round 1's predictions and audit the fold.** `read_spans(iteration=1)` from disk
       rather than threaded through memory (§5.5): the masker's input is a file somebody can
       diff against the flags that came back, and what it returns has no gold in it to mask.
       Then one Auditor call per dev document, and the report is written to
       `audit.report_path(iteration=2)` — deny-listed, because it is a map of the identifiers
       round 1 did not catch.
    4. **Read round 1's errors and draw §1.4's sample.** `read_errors(iteration=1)` then
       `sample.draw(..., iteration=2)`. The draw is seeded on (corpus, iteration) and is the
       caller's, not the assembler's: an assembler that drew would be a second place the seed
       is applied, and DESIGN §11.1 rests on both arms drawing through one function.
    5. **Assemble all four blocks and call the RuleAuthor.** `assemble_iteration_prompt`
       requires all five feedback inputs at a round ≥ 2 and refuses a round that silently
       dropped one, because an absent block is an unrecorded change of arm. §1.2 is *round 1's*
       rule file under `paths.armrules` and never `rules/{lang}.yaml`, which is the bootstrap
       format example (§5.3). `role` and `sample_reference` are both passed: the sample's
       reference is `render_window()`'s, which is what makes the line say which 40 spans this
       call was shown.
    6. **Score, or record the failure**, identically to round 1 — `run_fold(iteration=2)` with
       this round's `cost` and the arm's `cost_to_date`. `paths.formatfailure` on a `RuleError`,
       arm-scoped: round 1 cannot have written one and reached here, so there is nothing to
       overwrite (§5.5).

    **One open question this function does not close: it passes no `termination` block, so
    `run_fold` writes `not_applicable` — round 1's record, at a round where it is no longer
    true.** The reason it is not fixed here is the shape of the argument rather than a
    preference. `should_stop(corpus, leak_rates)` needs *this* round's dev leak rate, which does
    not exist until `run_fold` has scored, and `run_fold` takes the block as an argument because
    it cannot know whether the rules it is scoring came from round 1 of 8 or from a converged
    loop. That is the `final=True` impossibility of DESIGN §5.5 arriving at a different
    argument: the caller cannot compute what the writer needs before the writer runs. The three
    ways out — score twice, have the driver patch `metrics.json` afterwards, or have `run_fold`
    call the rule itself — are a second scoring pass, a second writer of a published file, and a
    second implementation of a pre-registered rule, and §5.5, §5.5's one-writer rule and §3
    refuse them respectively. The fourth is a round-scoped `termination` written by the driver
    after the score, which is a new artefact and a decision.

    So it is left stated and unfixed, deliberately, because it is the **round-3 termination
    decision** the loop cannot be built past without: round 3 is the first round at which the
    rule can fire at all (k = 2 needs two improvements, hence three rounds), so it is the round
    that has to answer this, and answering it at round 2 with one round of history would be
    choosing the mechanism where it cannot be exercised. What round 2 leaves on disk is enough
    for any of the answers: both rounds' leak rates are in `iter1/` and `iter2/metrics.json`,
    and δ is in every one of them.

    **The cost is 1 + N and this function adds none of it up.** `sum_costs` totals the
    RuleAuthor's one block and the Auditor's N; `sum_costs` again puts round 1's `cost_to_date`
    beside this round's figure for the arm total. The driver holds the accumulator and the
    scorer does the arithmetic, because a rung whose driver both decides how many calls to make
    and computes its own total is a rung pricing itself (DESIGN §5.5); `run_fold` never
    accumulates either, and `check_cost_to_date` refuses a total below the round.

    **Nothing here decides whether the arm continues.** This function runs round 2 and returns;
    the verdict is `should_stop()`'s, its one implementation is `src/termination.py` (DESIGN §3),
    and no arm can converge at this round anyway — k = 2 needs two improvements and two rounds
    give one. What round 2 owes a caller is both rounds' leak rates on disk, and it writes them.

    `client` and `control_client` are the two transport seams, passed through unexamined, for
    round 1's reason.
    """
    _check_inputs(corpus, lang, model_id)

    # The complement of the freeze guard, not a second copy of it — see the module docstring.
    if not arm_has_called(corpus, detector, supervision, porting):
        raise OrchestrateError(
            f"{corpus}/{detector}/{supervision}/{porting}: this arm has made no call, so "
            f"there is no round {FIRST} for round {SECOND} to iterate from. Round "
            f"{SECOND}'s §§1.2-1.4 are the previous round's rule file, score and errors; "
            "run run_iteration_1() first. This is `freeze_window()`'s condition read the "
            "other way and not a second guard on it: that one refuses a re-freeze after a "
            "call, and this refuses a later round before one (DESIGN §5.5, §6.3)."
        )
    # Reported, never raised (`orchestrate.window_drift`), and on the return value so a reader
    # of the round has it beside the cost. An edit to `rule_author.md`'s prose between round 1
    # and round 2 is a different event from an edit to `n` in `config/sampling.yaml`, and this
    # function cannot tell them apart.
    drift = window_drift(corpus, detector, supervision, porting)

    previous_metrics = _previous_round(corpus=corpus, detector=detector,
                                       supervision=supervision, porting=porting,
                                       iteration=FIRST)

    predictions = read_spans(corpus=corpus, detector=detector, supervision=supervision,
                             porting=porting, iteration=FIRST, root=orchestrate.ROOT)
    documents = load_fold(corpus, split)
    report, audit_costs = _audit_fold(
        documents, predictions, corpus=corpus, iteration=SECOND, model_id=model_id,
        max_tokens=max_tokens, client=client, control_client=control_client,
        detector=detector, supervision=supervision, porting=porting,
    )
    report_file = audit.report_path(corpus=corpus, detector=detector,
                                    supervision=supervision, porting=porting,
                                    iteration=SECOND, root=orchestrate.ROOT)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")

    # §1.4: the previous round's errors, drawn here rather than in the assembler. `practice`
    # is not passed — `check_iteration` refuses a real arm the reserved band and this is a real
    # arm, so the default is the fact rather than a default (`sample.check_iteration`).
    sample = draw(
        read_errors(corpus=corpus, detector=detector, supervision=supervision,
                    porting=porting, iteration=FIRST, root=orchestrate.ROOT),
        corpus, SECOND,
    )

    # §1.2 is *this arm's* round-1 output and never `rules/{lang}.yaml` (DESIGN §5.3). Built
    # through `arm_rules_path` rather than kept from round 1's return value, because a round is
    # runnable on its own and a path passed between processes is a path neither of them checked.
    previous_rules = arm_rules_path(corpus=corpus, detector=detector,
                                    supervision=supervision, porting=porting,
                                    iteration=FIRST, lang=lang, root=orchestrate.ROOT)

    prompt = assemble_iteration_prompt(
        lang=lang, corpus=corpus, iteration=SECOND, rules_path=previous_rules,
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
        call_line(SECOND, prompt_reference=reference, model=model,
                  response_chars=len(response.text),
                  response_sha256=_digest(response.text),
                  outcome=CALLED, cost=response.cost(), model_lifecycle=lifecycle,
                  role=RULE_AUTHOR, sample_reference=reference["error_spans"]),
        corpus, detector, supervision, porting,
    )

    run = _run_block(corpus, detector, supervision, porting, split, model)
    rules_file = _write_rules(response.text, corpus=corpus, detector=detector,
                              supervision=supervision, porting=porting, lang=lang,
                              iteration=SECOND)
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
            "iteration": SECOND,
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
        }

    # **No `termination=`, which is round 1's tail unchanged and is the open question this round
    # leaves — see the docstring.** `run_fold` writes `not_applicable(corpus)` in its place.
    spans_file, metrics_file, scored = run_fold(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        split=split, rules={lang: rules_file}, model_record=model, cost=cost,
        cost_to_date=cost_to_date, model_lifecycle=lifecycle,
        iteration=SECOND, root=orchestrate.ROOT,
    )
    return {
        "iteration": SECOND,
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
    }
