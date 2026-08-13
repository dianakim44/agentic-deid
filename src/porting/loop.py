"""The `port-loop` orchestrator, iteration 1 only.

`port-loop` is the iterating rung: the RuleAuthor writes `rules/{lang}.yaml`, the fold is
scored, and the score and its error spans come back as §§1.3–1.4 of the next call (DESIGN
§4). **This module implements round 1 and nothing else.** Round 2 onwards — the Auditor,
`assemble_iteration_prompt`, the sampled §1.4 window, the termination check — is not here
and is not stubbed here either. A stub would be a second place round 2 gets designed, and
the first place is DESIGN.

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

**One open question this file does not close: `paths.formatfailure` is not
iteration-scoped.** It is one path per arm, which is right for the freeze record and the
call log — one window and one append-only log per arm — and is an open question for a
failure record once round 2 exists, because round 2 failing would overwrite round 1's
record. Round 1 cannot hit it: there is no earlier round to overwrite. It is stated here
rather than fixed here because fixing it means deciding whether a later round's format
failure ends the arm (in which case one path is correct and the last failure is the arm's
outcome) or is recorded per round, and that decision belongs to whoever implements round 2
with DESIGN in front of them.

**The reentry guard is `freeze_window()`'s and is not duplicated.** Calling this function
twice on one arm fails at the freeze, because a line in `agent_calls.jsonl` fixes the window
(`orchestrate.arm_has_called`, `docs/notes/window-freeze-history.md`). That refusal already
says the right thing — the window is what the call ran under, changing it means re-running
the arm from its first call — and a check added in front of it would be a second condition
to keep true, on the exact guard this repository has already watched get stepped around.
Note what it therefore is *not*: a round counter. It refuses a second call in **this** arm,
which is round 1 twice while round 2 does not exist; the round-2 driver will need the freeze
to refuse a re-freeze while permitting its own call, and that is the same open question as
the paragraph above.

`model_id` is a parameter from the top and this file spells none, for `orchestrate`'s
reason (DESIGN §10 A2): a recorded id that came from a literal records what the code says
rather than what was called.
"""
from __future__ import annotations

from .. import orchestrate
from ..corpora.base import rule_langs
from ..eval.run_fold import DEFAULT_SPLIT, run_fold
from ..llm.bedrock import invoke, model_lifecycle
from ..llm.prompt import assemble_task_prompt
from ..orchestrate import (
    CALLED, FORMAT_FAILURE, ONESHOT_SECTIONS, RULE_AUTHOR, SCORED, OrchestrateError,
    _digest, _run_block, _write_failure, _write_rules, append_call, call_line,
    freeze_window,
)
from ..rules import RuleError, load_rules

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

#: The round this module runs, and the only one it runs. Named for `orchestrate.ITERATION`'s
#: reason and one more: it is the value that distinguishes this procedure from the baseline's,
#: so it is the thing a reader of this file is looking for and it should not be a bare `1`
#: inside three call sites.
ITERATION = 1

#: The arm this file drives beyond `porting`. Defaults rather than pins: `R` because a round
#: authors a rule file and rules are what `R` is, `sup-free` because the labels come from
#: placeholder positions (naming.yaml). Same values as the baseline's, which is the point —
#: the two arms differ in `porting` and in nothing else about the cell.
DETECTOR = "R"
SUPERVISION = "sup-free"


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
    loads. It is **not** an implicit terminal state for the arm — whether the loop may
    proceed to round 2 after a failed round, showing §1.3 with nothing scored, is round 2's
    question and is unanswered here. What is on disk after this returns is enough for either
    answer: the response verbatim, the validator's message, and the cost.

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
