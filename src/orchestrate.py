"""The `port-oneshot` orchestrator. This file holds its window freeze.

`port-oneshot` is the baseline rung: one LLM call writes `rules/{lang}.yaml` and the arm
is over (DESIGN §4). `run_arm()` is the whole arm — freeze the window, assemble §§1.1–1.2,
call once, validate what came back against the rule schema, and then either score the fold
or record the format failure. The freeze is written first and everything after it is
described below in the order it happens.

**Why the freeze is not `human_arm.freeze_window()`.** That function is pinned to
`paths.humanfreeze` and writes `"porting": "port-human"` as a literal, and DESIGN §6.3
refuses to widen it: a templated `humanfreeze` would let an agent arm write to the retired
arm's path, where its record would be silently overwritten by whichever arm started later.
Two keys, `humanfreeze` and `armfreeze`, and the retired arm's record is unreachable from
any arm but its own. So the agent arms get a writer of their own rather than a widened one,
and this is it.

**Why the refusal is not `path.exists()`.** `docs/notes/window-freeze-history.md` records
`freeze_window()`'s first version being stepped around three times with `rm` before
iteration 1: a refusal conditioned on the presence of the thing being protected is not a
refusal but a request, addressed to exactly whoever can remove the evidence. `port-human`'s
binding condition is a non-null `human_minutes` on an append-only log — a *use* of the
window rather than the record's existence. This arm records no minutes, so the equivalent
use is **the call**: a line in `agent_calls.jsonl`. Before it the record is a proposal and
re-freezing is free; after it the window is what the call ran under and no path through
this module rewrites it.

**And the record says what it does not cover.** This arm reads §§1.1–1.2 of the prompt and
leaves §§1.3–1.4 empty, because §4 defines it as `port-loop` truncated after call 1 and
call 1 of an iterating arm has no previous iteration to draw either block from. It hashes
`config/sampling.yaml` anyway, and that hash on its own would mislead: a reader finding
`sampling_sha256` would reasonably conclude 40 error spans at ±120 characters were shown.
So the record states which blocks the call carried and whether the sampling parameters
governed anything, and a record where those two disagree is refused.

**The call is logged before the result is judged.** `run_arm()` appends to
`agent_calls.jsonl` as soon as the transport returns, and only then parses what came back.
That order is the freeze guard's premise read forwards: the log line is what fixes the
window, so a run that validated first would leave a window re-freezable for the duration of
a parse, and a parse that raised would leave a call made and no record that it was. The
line carries the prompt's reference form and never its text (`rule_author.md` §6 names this
file), the response's length and hash and never the response, and **the §1.4 sample
reference as explicitly absent** — the key is written with a null value rather than left
out, for `model_id_absent`'s reason: a field some arms omit cannot be read across arms, and
this arm's absence of a sample is the arm's definition rather than a gap.

**Zero format-compliance retries, so a failure is written down rather than retried**
(DESIGN §10 A2). What comes back is written to the arm's rule file and loaded through
`src/rules.py`; if it does not load, `metrics.json` is not written — zeros there would read
as a rule set that ran and caught nothing — and `paths.formatfailure` gets the model ids,
the raw response and the validator's own message instead. **The cost block is written in
both branches**, because the call was made and paid for either way. Nothing repairs the
response on the way in: no fence-stripping, no key-fixing. A repair step is where a retry
budget hides when the retry count is zero.

**`model_id` is a parameter from the top and this file spells none.** DESIGN §10 A2's
comparison is one arm on two model families, so the id is an argument at every level down to
`bedrock.invoke()`, and `tests/test_orchestrate.py` asserts no identifier appears in this
file's text. All three of `Response.model_record()` are required in the run block this
module assembles, which `scorer.REQUIRED_RUN` does not require of every arm: the `R` arm
cannot observe two of them, and a required field one writer fills with a placeholder makes
the placeholder the convention (DESIGN §10 A2, the note on schema 4).

**The lifecycle record has three homes and none of them is the run block.** The arm probes
`GetFoundationModel` once before the call and the record goes to the call log, and then to
whichever of `metrics.json` / `paths.formatfailure` this arm writes — three places because
the call log is deny-listed by `tools/release_screen.py` and can never be recovered from
git, and because exactly one of the other two exists per arm. Not the run block, and not
`MODEL_FIELDS`: `start_of_life_time` is when the *id* appeared in the catalogue and says
nothing about what answered, so beside `model_id_resolution` it would read as evidence for a
verdict it cannot support. `bedrock.model_lifecycle`'s docstring is where that is argued.
"""
from __future__ import annotations

import hashlib
import json
import string
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import regex

from . import sample
from .corpora.base import (
    ROOT, CorpusError, axis, check_agent_role, path_template, rule_langs,
)
from .eval.run_fold import DEFAULT_SPLIT, run_fold
from .eval.scorer import check_run
from .eval import sealed_log
from .llm.bedrock import invoke, model_lifecycle
from .llm.prompt import assemble_task_prompt
# `_relative` rather than a third spelling of the same reduction. `src/rules.py` decides
# what a rule file's location looks like in a published record — repo-relative where it
# can be, filename-with-marker where it cannot, because an absolute path names a home
# directory and, on a machine where the corpus sits beside the repo, a DUA layout. The
# format-failure record answers the same question about the same kind of path, and a
# private name imported across two modules of one package is cheaper than two answers.
from .rules import RuleError, _relative as rules_relative, arm_rules_path, load_rules
from .sample import WINDOW_FILES, recorded_window_fields, window_hashes

#: The three `paths` keys this arm writes and reads, all from `config/naming.yaml` and none
#: as a literal here (DESIGN §11.2 requires it of an output path: a module holding its own
#: copy is a second place the answer can change). `armfreeze` is the agent arms' freeze key
#: and is deliberately *not* `humanfreeze` — see the module docstring.
FREEZE_KEY = "armfreeze"
LOG_KEY = "agentlog"
FAILURE_KEY = "formatfailure"

#: This arm's `porting` value. A literal here and checked against the axis on use, unlike
#: `human_arm`'s: the difference is that `paths.armfreeze` templates `{porting}`, so the
#: value is a *parameter* of every path this module builds and the literal is a default
#: for the one arm this file drives rather than a pin on the path.
PORTING = "port-oneshot"

#: Seconds any single `git` call here may take. A guard that can hang is a guard someone
#: removes (`human_arm.GIT_TIMEOUT`, same reasoning).
GIT_TIMEOUT = 10

#: The four input blocks of `docs/prompts/rule_author.md` §1, which is what an arm's
#: window is made of. Stated once and checked against the prompt's own headings at write
#: time, so a renumbering of that file is caught here rather than recorded as a window
#: claim about sections that no longer exist. §1.5 is not in the list: it documents how
#: the §1.4 sample is drawn and is not itself a block the agent is shown.
INPUT_BLOCKS = ("1.1", "1.2", "1.3", "1.4")

#: What `port-oneshot` is shown. DESIGN §4: the task frame and the current rule file, and
#: nothing else — no score block and no error-span block, in this arm and in `port-loop`'s
#: iteration 1 alike, so that call 1 of both arms is shown the same thing.
ONESHOT_SECTIONS = ("1.1", "1.2")

#: The block `config/sampling.yaml`'s parameters govern. `n_error_spans`, `min_per_type`
#: and `context_chars` all describe §1.4 and nothing else, so whether the file's contents
#: applied to a call is decided by whether that block was carried. Named rather than
#: inlined because the freeze record's `sampling_applied` field is derived from it, and a
#: derivation whose input is a bare string in an expression is a derivation nobody finds.
SAMPLING_SECTION = "1.4"

#: Headings of the form `### 1.4 …` in the prompt. Used to check `INPUT_BLOCKS` against
#: the file being hashed rather than against this module's memory of it.
_HEADING = regex.compile(r"^#{2,4}\s+(\d+\.\d+)\s", regex.MULTILINE)

#: Where `called_where()` found the evidence. Not booleans, because the two "yes" cases
#: need different messages — see the refusal in `freeze_window()`.
IN_LOG = "log"
IN_RESULTS = "results"

#: This arm's iteration number, which is 1 and is the whole of the sequence. Named because
#: it is a path component (`paths.armrules` puts it in a directory) and a bare `1` threaded
#: through three calls is three places `port-loop` would have to find when it counts.
ITERATION = 1

#: The arm this file drives, beyond `porting`. Defaults rather than pins, for `PORTING`'s
#: reason: every path built here templates all four axes, so these are parameters with a
#: default and the default is the cell the baseline occupies. `R` because a `port-oneshot`
#: run authors a rule file and rules are what `R` is, and `sup-free` because the labels come
#: from placeholder positions (naming.yaml).
DETECTOR = "R"
SUPERVISION = "sup-free"

#: What the log line says happened to a call. The log's own vocabulary and **not an axis**:
#: it describes one call's fate rather than naming a cell of the experiment, and
#: naming.yaml's axes are what name cells (`scorer.TREE_STATES` is the same call).
#: `CALLED` is what is known at the moment the line is written — the response arrived and
#: nothing has judged it yet, which is the ordering the module docstring is about.
CALLED = "called"
SCORED = "scored"
FORMAT_FAILURE = "format_failure"
OUTCOMES = (CALLED, SCORED, FORMAT_FAILURE)

#: The key names that would put corpus text in `sample_reference`, refused by `call_line()`.
#: DESIGN §5.5.1's list, and the same four names it uses: an added `text`/`surface`/`context`/
#: `snippet` field is the signal to *refuse the field*, not to move the record under
#: `FilledPrompt`. Named keys rather than a scan of the values, because a reference legitimately
#: holds long strings (`text_sha256` is 71 characters) and a length heuristic would either pass
#: a 30-character context window or refuse a hash.
#:
#: `text_sha256` and `text_chars` are absent from this list and must stay absent — they are what
#: a reference says *about* text without carrying it, and `render_window().reference()` writes
#: both. A guard that matched on the `text` prefix would refuse the only value that settles
#: "was this the block that ran".
TEXT_KEYS = frozenset({"text", "surface", "context", "snippet"})

#: The role `port-oneshot`'s one call carries. A default on `call_line()` rather than a
#: literal at the call site, and it is the value the whole arm has: this file drives the
#: RuleAuthor and calls no Auditor (DESIGN §4 — one call, and the Auditor enters at
#: `port-loop`'s round 2). Read through `check_agent_role()` at write time, so the vocabulary
#: is naming.yaml's and this constant is a choice of which declared value applies rather than
#: a second place the value is defined.
RULE_AUTHOR = "rule_author"

#: The three fields `bedrock.Response.model_record()` returns, all required in the run block
#: this module writes. `scorer.REQUIRED_RUN` names only `model_id` — see `_run_block()` and
#: DESIGN §10 A2's note on schema 4 for why the requirement lives here instead.
REQUIRED_MODEL = ("model_id", "model_id_reported", "model_id_resolution")

#: The one of them whose value may be null. `model_id_reported` is `None` when the response
#: did not carry the field at all, which is what Bedrock does by default
#: (`docs/notes/baseline-model-family.md`) — the honest record of a platform that said less
#: than it was asked to, and `model_id_resolution` is where that fact is named
#: (`alias-unresolved`). `scorer.NULLABLE_RUN` makes the same distinction for `commit`:
#: the key is required, the value may be null, and absent is still refused.
NULLABLE_MODEL = ("model_id_reported",)

#: Schema version of the format-failure record. Its own counter rather than the scorer's:
#: this file is not a metrics file and DESIGN §10 A2's whole point is that the two are
#: distinguishable by name, so a shared version would make a change to one imply a change
#: to the other.
#: 2 adds `model_lifecycle` (2026-08-11). It moves in step with `scorer.SCHEMA_VERSION` 5
#: and not because of it: exactly one of the two files is written per arm, so a record the
#: metrics file carries and this one does not would be a record the failing arms lose.
FAILURE_SCHEMA = 2


class OrchestrateError(CorpusError):
    """A `port-oneshot` step that cannot be taken as specified.

    Subclasses `CorpusError` so the existing "stop and tell a person" handling applies.
    An arm that half runs is worse than one that refuses: it produces a scored number
    under a window nobody can reconstruct.
    """


def _arm_path(key: str, **components) -> Path:
    """Fill a `paths` template, checking every component against its axis.

    Checked rather than trusted, for `human_arm._arm_path()`'s reason: a results path is
    the record of which cell of the experiment an artefact belongs to, so a typo mints a
    cell instead of failing. `results/es-meddocan/rules-only/…` would sit beside
    `results/es-meddocan/R/…` looking like a second detector, and an aggregation walking
    these directories would report it as one (CLAUDE.md: only naming.yaml vocabulary,
    including values that never reach a results path — these do).

    **Which components to check is read off the template rather than listed here.** This is
    the third site in the repository doing this validation (`human_arm._arm_path` checks
    three components, `rules.arm_rules_path` five) and a hand-written fourth copy of the
    axis list is a fourth thing to keep in sync. Every field name in the two templates this
    module fills is also an axis name, so the rule is simply: every field must be an axis,
    and a template that grows a non-axis component (`{iteration}`) fails here until someone
    decides explicitly what checks it. That is the general form the other two sites should
    collapse into.
    """
    template = path_template(key)
    fields = [f for _, f, _, _ in string.Formatter().parse(template) if f]
    for field in fields:
        if field not in components:
            raise OrchestrateError(
                f"paths.{key} needs a {field!r} component and none was given. The "
                "template is the authority on what a path is made of; filling it from a "
                "shorter set would produce a path with a literal brace in it."
            )
        try:
            allowed = axis(field)
        except CorpusError as exc:
            raise OrchestrateError(
                f"paths.{key} has a {field!r} component and naming.yaml has no {field!r} "
                "axis, so nothing here can check it. Decide explicitly what validates it "
                f"rather than letting it through unchecked ({exc})."
            ) from exc
        if components[field] not in allowed:
            raise OrchestrateError(
                f"{components[field]!r} is not a {field} in config/naming.yaml (have: "
                f"{sorted(allowed)}). A results path names the cell of the experiment an "
                "artefact belongs to, so an unknown component would create a cell rather "
                "than fail."
            )
    return ROOT / template.format(**{f: components[f] for f in fields})


def freeze_path(corpus: str, detector: str, supervision: str,
                porting: str = PORTING) -> Path:
    """Where this arm's freeze record lives (`paths.armfreeze`, DESIGN §6.3).

    One record per arm per corpus. `port-oneshot` does not inherit `port-human`'s: the
    freeze answers "what was the window **this arm** committed to at its start", arms start
    at different times, and a shared record would be one arm's window or the other's and
    never both.
    """
    return _arm_path(FREEZE_KEY, corpus=corpus, detector=detector,
                     supervision=supervision, porting=porting)


def log_path(corpus: str, detector: str, supervision: str,
             porting: str = PORTING) -> Path:
    """Where this arm's call log lives (`paths.agentlog`).

    Deny-listed by `tools/release_screen.py` and therefore never committed: an agent
    prompt carries dev corpus text in §1.4 for the arms that show that block. That has a
    consequence for the guard below and it is stated there rather than here.
    """
    return _arm_path(LOG_KEY, corpus=corpus, detector=detector,
                     supervision=supervision, porting=porting)


def _git(args: list[str], cwd: Path) -> str | None:
    """`git` output, or `None` for anything that went wrong.

    Failure is not an error: this runs under `monkeypatch.setattr(ROOT, tmp_path)` in the
    tests and on machines where a checkout may be an export rather than a repository, so
    "no git, no repository, unknown revision" all have to mean "history says nothing"
    rather than a traceback out of a guard. That direction of failure is the unsafe one,
    which is why the working tree is consulted first and why the limits are stated in the
    docstrings instead of argued away. Nothing from the output reaches an exception
    message (CLAUDE.md, and the rule does not branch on which file happens to be safe).
    """
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                              text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _call_in_worktree(path: Path) -> bool:
    """Whether `agent_calls.jsonl` holds at least one line.

    One line is the criterion, not a field's value. `port-human`'s guard reads a non-null
    `human_minutes` because a log line there can be written before any attention is spent
    — `event: read_sample` is exactly that. This log has no such line: it is appended to
    when a call is made, so its existence as a non-empty file *is* the event. Blank lines
    do not count, and the line is not parsed: a malformed line still means something wrote
    to this file during a call, and a guard that refused to see it because `json.loads`
    raised would fail in the unsafe direction.
    """
    if not path.exists():
        return False
    with open(path, encoding="utf-8") as fh:
        return any(line.strip() for line in fh)


def _artefacts_in_git_history(corpus: str, detector: str, supervision: str,
                              porting: str) -> bool:
    """Whether any commit holds an artefact of this arm that only a call can produce.

    **This is the second source, and it exists because the first one is a single file the
    operator can delete.** `port-human`'s guard reads its log from the working tree and
    then from git history, because deleting one file must not re-open a freeze. The same
    hole is here — and it cannot be closed the same way: `agent_calls.jsonl` is
    deny-listed by `tools/release_screen.py` (an agent prompt quotes dev text), so it is
    never committed and history holds no copy of it, ever.

    What history *does* hold is everything else the arm writes: `metrics.json`,
    `spans.jsonl`, its rule files under `rules/iter{N}/`, and whatever a later step adds.
    So the question asked here is "has this arm ever committed an output", and it is asked
    by listing the arm's results directory in every commit that touched it rather than by
    enumerating filenames. Enumeration would silently stop protecting on the day the
    orchestrator gains an artefact, which is the shape of failure this repository keeps
    finding: a check that matches nothing reads as a check that passed.

    **The freeze record itself is excluded, and that exclusion is the point.** It is
    written *before* the call, so counting it would make this guard true as soon as a
    window was frozen — `path.exists()` arriving by the back door, with a `git` walk in
    front of it to look thorough.
    """
    top = _git(["rev-parse", "--show-toplevel"], ROOT)
    if top is None:
        return False
    root = Path(top.strip())
    try:
        armdir = freeze_path(corpus, detector, supervision, porting).parent
        rel = armdir.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    frozen = Path(path_template(FREEZE_KEY)).name
    commits = _git(["log", "--all", "--format=%H", "--", str(rel)], root)
    if not commits:
        return False
    for sha in commits.split():
        listing = _git(["ls-tree", "-r", "--name-only", sha, "--", str(rel)], root)
        if listing is None:
            continue
        for line in listing.splitlines():
            if line.strip() and Path(line.strip()).name != frozen:
                return True
    return False


def called_where(corpus: str, detector: str, supervision: str,
                 porting: str = PORTING) -> str | None:
    """`IN_LOG`, `IN_RESULTS`, or `None` — where the evidence of a call was found.

    Two sources, in this order, for `human_arm.started_where()`'s reasons:

    1. **The working tree.** A line in `agent_calls.jsonl`. Cheap, and the answer in every
       run where nothing has been deleted.
    2. **Git history.** A committed artefact of this arm other than the freeze record.

    Working tree first, because both can be true at once and the ordinary case must not be
    reported as the deleted-log one: the `IN_RESULTS` message tells the reader the call log
    is gone, which is wrong advice when the file is sitting there.

    `None` is not a proof that no call was made. A call whose log was deleted before
    anything was committed reads as *not called*, and so does a rewritten history. Both
    are asserted as tests rather than left implied, because the purpose here is to prevent
    an accident and make a deliberate change conspicuous — the second is the only one code
    can reach.
    """
    if _call_in_worktree(log_path(corpus, detector, supervision, porting)):
        return IN_LOG
    if _artefacts_in_git_history(corpus, detector, supervision, porting):
        return IN_RESULTS
    return None


def arm_has_called(corpus: str, detector: str, supervision: str,
                   porting: str = PORTING) -> bool:
    """Whether this arm has already made its LLM call on this corpus.

    The criterion is **a line in `agent_calls.jsonl`, or a committed artefact of the arm
    other than its freeze record**. That is the point the window stops being a proposal:
    before the call, re-freezing costs nothing and the earlier record described a window
    nothing ran under; after it, the frozen window is the one the call ran under, and no
    analysis re-runs that call against a different one.

    Deliberately *not* "does the freeze record exist" (DESIGN §6.3,
    `docs/notes/window-freeze-history.md`). A guard whose condition is the presence of the
    file it protects is satisfied by deleting that file, which is what happened three
    times here before iteration 1.
    """
    return called_where(corpus, detector, supervision, porting) is not None


def prompt_blocks() -> frozenset[str]:
    """The `§1.x` headings present in the RuleAuthor prompt as it stands on disk.

    Read from the file that is being hashed, so `INPUT_BLOCKS` is checked against the
    window rather than against this module's memory of it. A renumbering of
    `docs/prompts/rule_author.md` then fails at freeze time instead of producing a record
    that attests to sections which no longer exist — and since that file's hash is in the
    record, the two facts belong to the same event.

    Resolved through `src.sample`'s own module globals rather than this module's `ROOT` and
    a local copy of the filename, because `window_hashes()` resolves it that way. Two
    resolutions of one path is how a record comes to hash one file and describe another —
    and it would show up only under a redirected root, i.e. only in the tests, where the
    record is not the thing being read.
    """
    text = (sample.ROOT / sample.PROMPT_TEMPLATE).read_text(encoding="utf-8")
    return frozenset(_HEADING.findall(text))


def _check_sections(sections: Sequence[str]) -> tuple[str, ...]:
    """Validate what the caller says the call carried; return it in `INPUT_BLOCKS` order."""
    unknown = [s for s in sections if s not in INPUT_BLOCKS]
    if unknown:
        raise OrchestrateError(
            f"{unknown} are not input blocks of the RuleAuthor prompt (have: "
            f"{list(INPUT_BLOCKS)}). The freeze record's window claim is about §1's "
            "blocks, and a section named here that is not one of them would be recorded "
            "as shown while nothing was."
        )
    if not sections:
        raise OrchestrateError(
            "no sections were given, so the record would claim the call was shown "
            "nothing at all. An arm reads at least the task frame; an empty list is a "
            "caller that has not decided rather than a window."
        )
    missing = [b for b in INPUT_BLOCKS if b not in prompt_blocks()]
    if missing:
        raise OrchestrateError(
            f"docs/prompts/rule_author.md has no §{', §'.join(missing)} heading(s), so "
            "this module's INPUT_BLOCKS no longer describes the file it hashes. The "
            "record would attest to a window made of sections that do not exist — "
            "renumber INPUT_BLOCKS in the same commit as the prompt."
        )
    return tuple(b for b in INPUT_BLOCKS if b in sections)


def freeze_window(corpus: str, detector: str, supervision: str,
                  porting: str = PORTING, *,
                  sections: Sequence[str] = ONESHOT_SECTIONS) -> dict:
    """Record the window this arm's call will run under. Refuses once the call is made.

    Returns the record it wrote, or the record already on disk when the call has been
    made — except that the second case raises instead, because a caller re-freezing after
    the call has either lost track of the arm's state or is trying to change the window,
    and neither is served by handing back a record as though nothing happened.

    **Before the call this overwrites, and that is deliberate.** `human_arm.freeze_window()`
    returns the existing record instead, so a re-run of the setup step gets the opening
    window rather than today's. The opposite is right here for a reason specific to this
    rung: `port-oneshot` writes no per-line hashes — §6.3 permits that because *n*=1 makes
    the freeze and the call the same moment — so the record is the *only* thing attesting
    to the window the call ran under. Returning a stale proposal would let the prompt move
    between the freeze and the call with nothing on disk disagreeing, which is precisely
    the mid-run drift §6.3 says must not be copied from this arm to `port-loop`. So the
    freeze is taken immediately before the call and it describes the files as they are
    then. `revision` counts the overwrites, so re-freezing is visible in the record rather
    than only in a note somebody remembered to write — six revisions of `port-human`'s
    window needed a hand-maintained file to be legible at all.

    `sections` is what the call carries, and the caller states it rather than this
    function assuming it. `ONESHOT_SECTIONS` is the default because this file drives that
    arm, and `port-loop` passing its own is how the same writer serves an arm whose
    iteration 2 carries §§1.3–1.4.
    """
    where = called_where(corpus, detector, supervision, porting)
    if where is not None:
        raise OrchestrateError(
            f"{corpus}/{detector}/{supervision}/{porting}: this arm has already made its "
            "call, so the frozen window is the one that call ran under and "
            "freeze_window() will not write another. "
            + ("The call log is present." if where == IN_LOG else
               "The evidence is a COMMITTED ARTEFACT of this arm and the call log is not "
               "in the working tree — it is deny-listed and never committed, so it cannot "
               "be restored from git. Whatever it recorded about the call is gone; treat "
               "the arm as run and do not re-freeze to make this error go away, because a "
               "new record would hash today's files and claim to be the window the call "
               "ran under.")
            + " Changing the window now means re-running the arm from its first call "
            "(DESIGN §6.3, §11.1). See docs/notes/window-freeze-history.md for what a "
            "re-freeze conditioned on the record's existence cost this repository."
        )

    shown = _check_sections(sections)
    empty = tuple(b for b in INPUT_BLOCKS if b not in shown)
    path = freeze_path(corpus, detector, supervision, porting)
    previous = 0
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            previous = json.load(fh).get("revision", 0)
    record = {
        "corpus": corpus,
        "detector": detector,
        "supervision": supervision,
        "porting": porting,
        # An instant, not a date: two freezes of the same arm on one day are ordered by
        # this field and by nothing else, and `revision` says only how many there were.
        # Same format as `src/split.py`, `src/eval/sealed_log.py` and the run block.
        "generated": _now(),
        # How many times this window has been re-frozen before the call. Free, per §6.3,
        # and free is not the same as unrecorded: `port-human`'s six pre-start revisions
        # were each legitimate and none of them was visible in the record.
        "revision": previous + 1,
        "files": list(WINDOW_FILES),
        **window_hashes(),
        # What the call carries, and what it does not. Both written out rather than one
        # implied by the other, because a reader who has to know that §1.4 is the section
        # `config/sampling.yaml` governs is a reader who will not know it.
        "sections_shown": list(shown),
        "sections_empty": list(empty),
        # Whether the hashed sampling parameters governed anything in this call. Derived
        # from the sections rather than passed in, so the two cannot disagree — this arm
        # hashes `config/sampling.yaml` while using none of it, and a record that stated
        # the hash alone would read exactly like one from an arm that drew 40 spans under
        # it. DESIGN §6.3: the hash stays for comparability with the arms that do.
        "sampling_applied": SAMPLING_SECTION in shown,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    return record


def window_drift(corpus: str, detector: str, supervision: str,
                 porting: str = PORTING) -> list[str]:
    """Which frozen files no longer hash to what was recorded. Empty when none.

    Reported rather than refused, for `human_arm.window_drift()`'s reason: an edit to the
    prompt's prose that leaves §1.4's numbers alone is a different event from a change to
    `n`, and only a person can tell them apart. For this arm the window can only have
    moved *after* the call — `freeze_window()` writes immediately before it — so any drift
    here means the record and the files disagree about a call that has already happened.
    """
    path = freeze_path(corpus, detector, supervision, porting)
    if not path.exists():
        raise OrchestrateError(
            f"{corpus}/{detector}/{supervision}/{porting}: no freeze record for this arm, "
            "so there is nothing to compare against. "
            + ("This arm has made its call, so the record existed and is now gone — "
               "restore it from git. Do NOT call freeze_window() to make this error go "
               "away: it would hash today's files and the result would claim to be the "
               "window the call ran under."
               if arm_has_called(corpus, detector, supervision, porting) else
               "freeze_window() runs immediately before the call (DESIGN §6.3).")
        )
    with open(path, encoding="utf-8") as fh:
        frozen = json.load(fh)
    now = window_hashes()
    # The compared fields come from the record, not from `WINDOW_FILES` — see
    # `recorded_window_fields()`. This arm's two frozen records name two files, and
    # checking them against today's three would report a permanent drift on a file their
    # calls never saw.
    return [field for field in recorded_window_fields(frozen)
            if frozen.get(field) != now[field]]


# ─── the call log ───────────────────────────────────────────────────────────


def call_line(iteration: int, *, prompt_reference: dict, model: dict,
              response_chars: int, response_sha256: str, outcome: str,
              cost: dict, model_lifecycle: dict | None = None,
              role: str = RULE_AUTHOR, sample_reference: dict | None = None) -> dict:
    """One `agent_calls.jsonl` line: what was sent, what answered, what it cost.

    **`role` says which agent spent the call** (DESIGN §5.5). RuleAuthor and Auditor both
    call a model and both lines land in one `agent_calls.jsonl`, so `llm_calls` sums them —
    which is what a round cost and is the right total, and which makes "the Auditor accounts
    for half this arm's spend" unanswerable from the file that holds the spend. Written on
    **every** line including this arm's, per the rule `model_id_absent` and `sample_reference`
    both follow: a key some arms omit cannot be compared across arms, and a reader who has to
    know which arms have the field is a reader who will read a `port-oneshot` log as an
    unattributed one rather than as a one-agent one.

    Validated through `check_agent_role()`, so `"RuleAuthor"` or `"rule-author"` is refused
    here instead of splitting one agent's calls across two spellings in a file every per-role
    cost figure is computed from. **Nothing derives it** — not from the prompt reference, not
    from `porting`, not from the template filename. That is DESIGN §3's layer-from-detector-
    name prohibition one field over, and `test_no_module_derives_a_role_from_a_filename` is
    the structural half of it.

    Defaulted rather than required, and the default is this file's own arm. `port-oneshot`
    makes one call and it is the RuleAuthor's; `port-loop`'s driver passes both values
    explicitly, which is the asymmetry §5.5 wanted from a shared helper — the baseline's
    driver does not change while the iterating arm is built, and the iterating arm cannot
    inherit a default that happens to be right for one of its two agents.

    **The frozen arms' existing lines are not rewritten.** This function writes what a new
    line carries; nothing here or in `append_call()` reads or edits a line already on disk,
    and `append_call()` opens for append for exactly that reason. `tests/test_call_role.py`
    asserts it against the two real logs rather than against a fixture, in the shape
    `tests/test_window_widening.py` used for `WINDOW_FILES`: a record that must not move is a
    committed one, and a fixture cannot fail to be retroactively rewritten.

    **No prompt text and no response text.** The prompt's `reference()` is what may be
    recorded (`rule_author.md` §6, DESIGN §11.2) and the response is reduced to a length and
    a hash here — enough to answer "was this the output that was scored" against the rule
    file beside it, and not enough to reconstitute a completion that echoed its prompt. The
    raw response does reach disk on a format failure, at `paths.formatfailure`, which is a
    path the screener allows and sniffs; this log is deny-listed and never committed, so a
    response here would be a copy in the one place no review reaches.

    **`sample_reference` is written as null rather than omitted.** This arm's §1.4 block is
    empty, so there is no drawn sample to point at — and the honest record of that is an
    explicit absence, not a missing key. `model_id_absent` settles the same question one
    field over (DESIGN §4): a key some arms omit cannot be compared across arms, and a null
    nobody wrote cannot be told from a null that was measured. `port-loop`'s iteration 2
    fills this key with `render_window()`'s reference; that the two arms' lines differ in a
    value rather than in a shape is what makes them one log.

    **It is a parameter as of 2026-08-13, defaulted to the null this function used to hardcode,
    and it is the one place `port-loop`'s driver reaches into the baseline's module.** The
    docstring above promised the value from the day the field existed and there was no way to
    pass it, so an iterating driver had two options and both were worse than this: write the
    key itself after the call, which makes the line's shape decided in two places, or leave it
    null on every round, which turns a field that says *which 40 spans this call was shown*
    into one that says nothing on the only arm that has an answer. Defaulted rather than
    required for `role`'s reason and with the same consequence — the null is *true* of every
    existing caller, so `run_arm()` is not edited and the frozen arms' lines are byte-identical
    across this change (`tests/test_call_role.py`). The Auditor's lines are null too, and that
    is not the default leaking through: `auditor.md` §5 gives that agent no sample, so its
    absence is the arm's structure rather than an unfilled argument.

    Nested rather than merged, like `assemble_iteration_prompt`'s `error_spans` key: what goes
    here is `render_window().reference()` — span references, counts, `context_chars` and the
    block's own hash — and merging it would put a block's `text_sha256` beside the prompt's
    under two names a reader has to tell apart. **No window text**, and that is checked rather
    than documented: this log is deny-listed because §1.4 carries dev corpus text, so it is the
    one file `tools/release_screen.py` cannot review, and a §1.4 context window written into it
    is a leak nothing downstream would catch. DESIGN §5.5.1 settles what to do with a `text` or
    `context` key offered to a reference — refuse the field, not widen the type — and this is
    that rule at the writer.

    The window hashes go on every line, per DESIGN §11.2. `port-oneshot` has one line and
    the freeze record would do, but the field is the iterating arms' mid-run drift detector
    and a writer that omitted it here would be the writer `port-loop` is copied from.

    **`model_lifecycle` is supplementary and lives here rather than in the run block.** It is
    `GetFoundationModel`'s metadata for the id that was called — an ARN, a display name, a
    status and a `startOfLifeTime` — and it **does not resolve an alias**: the timestamp says
    when the *id* first appeared and not which weights served it (DESIGN §10 A2, measurement 4
    of `docs/notes/baseline-model-family.md`). The reason it is a log field and not a run
    field is that distinction. `metrics.json`'s run block is what the paper's claims are read
    off, and a per-call metadata block sitting there beside `model_id_resolution` would read
    as strengthening it. In the call log it is what it is: a note about the id, attached to
    the call that used it.

    Optional, and `None` gives a line without the key rather than a null. That is the
    opposite of `sample_reference` one line down, deliberately: an empty §1.4 block is a fact
    about this arm and must be legible across arms, whereas a metadata probe is a convenience
    whose absence claims nothing. `port-loop` may pass it or not, and neither reading of its
    log becomes wrong.
    """
    if outcome not in OUTCOMES:
        raise OrchestrateError(
            f"{outcome!r} is not a call outcome (have: {list(OUTCOMES)}). The log's own "
            "vocabulary, not an axis: it describes what happened to one call rather than "
            "naming a cell of the experiment."
        )
    carries_text = sorted(set(sample_reference or ()) & TEXT_KEYS)
    if carries_text:
        raise OrchestrateError(
            f"sample_reference carries {carries_text}, which is text and not a reference. "
            "The keys are named and no value is quoted (CLAUDE.md). `render_window()` renders "
            "§1.4's context windows and hands back a `FilledPrompt`; `reference()` is the way "
            "out of it, and this log is deny-listed precisely because that block is corpus "
            "text (DESIGN §5.5.1: refuse the field, do not widen the record)."
        )
    return {
        "iteration": iteration,
        # Which agent spent this call. Beside `iteration` rather than at the end, because the
        # two together are what a per-round per-role cost sum groups by, and a reader scanning
        # the file reads the first fields.
        "role": check_agent_role(role),
        "outcome": outcome,
        **model,
        # A note about the id and not a resolution of it — see the docstring, and the field
        # name says so. Omitted entirely when absent, unlike `sample_reference`.
        **({"model_lifecycle": dict(model_lifecycle)} if model_lifecycle else {}),
        "prompt_reference": dict(prompt_reference),
        # Explicitly null when absent, and see the docstring. `port-loop` fills it from
        # iteration 2; every other caller leaves it, and gets the byte this line used to
        # hardcode. Copied rather than kept, like `prompt_reference` above.
        "sample_reference": dict(sample_reference) if sample_reference else None,
        "response_chars": response_chars,
        "response_sha256": response_sha256,
        "cost": dict(cost),
        "generated": _now(),
        **window_hashes(),
    }


def append_call(record: dict, corpus: str, detector: str, supervision: str,
                porting: str = PORTING) -> Path:
    """Append one line to `agent_calls.jsonl`. Creates the directory, rewrites nothing.

    Append-only for `human_arm.append()`'s reason and one more of its own: this file is what
    `called_where()` reads, so a writer that truncated it would un-fix a window that a call
    had already fixed.
    """
    path = log_path(corpus, detector, supervision, porting)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


# ─── the arm ────────────────────────────────────────────────────────────────


def failure_path(corpus: str, detector: str, supervision: str,
                 porting: str = PORTING) -> Path:
    """Where a format failure is recorded (`paths.formatfailure`, DESIGN §10 A2)."""
    return _arm_path(FAILURE_KEY, corpus=corpus, detector=detector,
                     supervision=supervision, porting=porting)


def _digest(text: str) -> str:
    """`sha256:…` over a string, in `prompt._digest`'s form.

    The same labelled shape `src/sample.py` and `src/llm/prompt.py` write, so a hash in this
    module's records can be compared to one in theirs without a reader working out which
    algorithm each meant. Re-derived rather than imported for one line, since importing a
    private name across modules is worth it for `_relative`'s judgement about published
    paths and not for two calls to `hashlib`.
    """
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    """The current instant in `scorer.GENERATED_RE`'s form.

    One helper for the freeze record, the log line and the run block, because three
    spellings of a timestamp is three records that do not sort against each other
    (DESIGN §10 A2 requires the instant, not the date).
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_block(corpus: str, detector: str, supervision: str, porting: str,
               split: str, model: dict) -> dict:
    """The run block this arm publishes, validated before anything is written.

    `scorer.REQUIRED_RUN`'s fields plus **all three of `Response.model_record()`**. The
    scorer requires only `model_id`, and the difference is deliberate (DESIGN §10 A2, schema
    4): `run_fold` closes the `R` arm, which calls no model and cannot observe the other two,
    so requiring them there would have them filled with placeholders on every run that
    writer makes — and a required field one writer fills with a placeholder makes the
    placeholder the convention. The requirement therefore lives where the observation does,
    which is here, and it is enforced rather than assumed: a caller passing a `model` mapping
    short of one field gets a refusal instead of a metrics file whose resolution claim is
    missing.
    """
    missing = [k for k in REQUIRED_MODEL if not model.get(k) and k not in NULLABLE_MODEL]
    absent = [k for k in REQUIRED_MODEL if k not in model]
    if missing or absent:
        raise OrchestrateError(
            f"the model record is missing {sorted(set(missing + absent))}. This arm calls a "
            "model, so it can observe all three fields and it records all three "
            f"({list(REQUIRED_MODEL)}, from bedrock.Response.model_record()). "
            "scorer.REQUIRED_RUN asks only for model_id because run_fold closes an arm that "
            "calls none; a placeholder written here would become what the schema looks like "
            "by the time a second model-calling arm exists (DESIGN §10 A2)."
        )
    commit, tree = sealed_log.tree_state()
    run = {
        "corpus": corpus,
        "detector": detector,
        "supervision": supervision,
        "porting": porting,
        "split": split,
        **{k: model[k] for k in REQUIRED_MODEL},
        "generated": _now(),
        "commit": commit,
        "tree": tree,
    }
    # Validated here rather than only at write time, so the refusal arrives before a
    # results directory exists — `run_fold` does the same for the same reason.
    check_run(run)
    return run


def _write_rules(text: str, *, corpus: str, detector: str, supervision: str,
                 porting: str, lang: str, iteration: int) -> Path:
    """Write the model's output to the arm's rule file, verbatim.

    `paths.armrules` and never `rules/{lang}.yaml` (DESIGN §5.3): the committed file is the
    format example and the bootstrap state, and two arms writing one path means the second to
    run overwrites the first's input while leaving a plausible `metrics.json` behind.

    **Verbatim, and that is the whole of it.** No fence stripping, no trailing-newline
    repair, no YAML round-trip. DESIGN §10 A2 fixes format retries at zero because a format
    failure is a result the appendix reports, and a normalisation step is a retry with the
    count still reading zero — it is the same edit ("make the obvious fix and validate
    again") with nothing in the record to show it happened.
    """
    # `root=ROOT` rather than letting `arm_rules_path` default to its own module's: this
    # module has one root, `_arm_path()` builds every other path from it, and a writer that
    # read a different one would put the rule file somewhere the freeze record and the call
    # log are not. It is also what makes the arm testable — the tests redirect this module's
    # `ROOT`, and a default resolved in `src/rules.py` would ignore that and write into the
    # real `results/` tree.
    path = arm_rules_path(corpus=corpus, detector=detector, supervision=supervision,
                          porting=porting, iteration=iteration, lang=lang, root=ROOT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_failure(*, corpus: str, detector: str, supervision: str, porting: str,
                   split: str, model: dict, response: str, error: str,
                   rules_path: Path, cost: dict, prompt_reference: dict,
                   model_lifecycle: dict | None = None,
                   caching: dict | None = None) -> Path:
    """Record a format failure. Written instead of `metrics.json`, never beside it.

    DESIGN §10 A2's three contents: the model ids, the raw response, and **the validator's
    own error message verbatim**. The message is not paraphrased — "the file was malformed"
    is not something a reader can check, and the appendix's sentence is "this model could
    not do it".

    The cost block is here too, and `metrics.json` is absent. A metrics file with zeros in
    it would be indistinguishable from a rule set that ran and caught nothing, which is the
    opposite finding; a results directory holding one file or the other is what makes the
    filename the answer.

    This is the one path in this repository where a model's raw output reaches disk. It is on
    the screener's ALLOW list as a path and the content sniffer still runs first, because a
    completion that echoed a §1.4 prompt would carry the corpus with it and "this arm's call
    carries §§1.1–1.2 only" is a fact about today's arm rather than a property of the path.

    `model_lifecycle` is carried here for the reason the first paragraph of this docstring
    gives: this file is written **instead of** `metrics.json`, so a record only that file
    carries is a record every failing arm loses, and a failing arm is exactly the appendix
    case §10 A2 cares most about. It is a sibling of the model ids rather than merged into
    them, and it **does not resolve the alias** — `start_of_life_time` is when the id
    appeared in the catalogue, not what answered on the day (`bedrock.model_lifecycle`,
    `docs/notes/baseline-model-family.md` §"측정 결과" 4).

    `caching` is carried for that same reason and is omitted rather than nulled when the round
    was not cached (schema 8, DESIGN §5.4). A `port-loop` round that fails on format still made
    its 1 + N calls and still paid for them, so the cost block is here — and the transport record
    that makes the billed basis recoverable from that cost block has to be here with it, or the
    one arm whose cost is least interpretable is the one arm missing the number that interprets
    it. Absence means the round was not cached, which is the same convention `metrics.json` uses.
    """
    path = failure_path(corpus, detector, supervision, porting)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": FAILURE_SCHEMA,
        "corpus": corpus,
        "detector": detector,
        "supervision": supervision,
        "porting": porting,
        "split": split,
        **{k: model[k] for k in REQUIRED_MODEL},
        # Omitted when there was nothing to probe, never nulled — the same choice
        # `call_line` makes and the deliberate opposite of `sample_reference`'s. Here the
        # two states are "no probe" and "a probe that failed", and the second already has
        # its own record (`status: unavailable`), so a null would be read as the first.
        **({"model_lifecycle": dict(model_lifecycle)} if model_lifecycle else {}),
        "generated": _now(),
        # Where the response was written before it was loaded. The failure is about a file,
        # and a reader who cannot find the file has the message and not the artefact.
        "rules_path": rules_relative(rules_path),
        # Verbatim, per §10 A2. `str(exc)` and nothing more.
        "error": error,
        "response": response,
        "response_chars": len(response),
        "response_sha256": _digest(response),
        "prompt_reference": dict(prompt_reference),
        "cost": dict(cost),
        # Beside the cost block it interprets, and absent when the round was not cached — the
        # same convention `scorer.write_metrics` follows. `is not None` rather than a truthiness
        # test, unlike `model_lifecycle` above: a caching block is never falsy when it exists
        # (`enabled` is always True), so the two forms agree, and the explicit one says which
        # state is being tested.
        **({"caching": dict(caching)} if caching is not None else {}),
        **window_hashes(),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    return path


def run_arm(*, corpus: str, lang: str, model_id: str,
            detector: str = DETECTOR, supervision: str = SUPERVISION,
            porting: str = PORTING, split: str = DEFAULT_SPLIT,
            max_tokens: int | None = None, client=None, control_client=None) -> dict:
    """The whole arm: freeze, assemble, call once, validate, then score or record failure.

    Returns what happened, as a mapping with `outcome` (`SCORED` or `FORMAT_FAILURE`), the
    cost block, the run block, and the paths written. Nothing about it is a summary the
    caller has to trust: every value in it is also on disk.

    The order is fixed and each step's position is load-bearing:

    1. **Freeze the window** (DESIGN §6.3, "freeze last"). Immediately before the call and
       not at the top of a setup script, because a freeze taken before the surrounding work
       is settled gets retaken — six times, before `port-human`'s iteration 1.
    2. **Assemble §§1.1–1.2** through `assemble_task_prompt()`, which draws nothing. §§1.3
       and 1.4 are stated empty in the prompt (DESIGN §4), and the freeze record says the
       same thing in its own words so the two can be compared.
    3. **Probe the model's lifecycle**, then **call once.** `bedrock.invoke()` makes one
       attempt and this makes one call; there is no loop here to bound. The probe is a
       control-plane lookup that makes no inference, so it is not in `cost` — counting it in
       `llm_calls` would make this arm's cost incomparable to `port-loop`'s for a reason
       having nothing to do with either — and it goes before the call rather than after so
       that anything surprising it does happens while the arm can still be rerun.
    4. **Log the call**, before the response is judged. See the module docstring: the log
       line is what fixes the window, so it is written while the only thing known about the
       response is that it arrived.
    5. **Validate by loading.** The response is written to `paths.armrules` and read back
       through `src/rules.py` — the same loader `run_fold` will use, so "it validated" and
       "it will load when scored" cannot come apart.
    6. **Score, or record the failure.** `run_fold` on success; `paths.formatfailure` on a
       `RuleError`, with `metrics.json` left unwritten (DESIGN §10 A2).

    `model_id` is required and keyword-only. It is a parameter the whole way down for A2's
    two-family comparison, and nothing in this file spells one — a recorded id that came from
    a literal is a record of what the code says rather than of what was called.

    `client` is the transport seam the tests use, passed through to `bedrock.invoke()`
    unexamined. `max_tokens` is left to that module's default unless given, so the budget is
    decided in one place.

    `control_client` is the *second* seam, for the lifecycle probe, and it is separate
    because the probe is a different service — `converse` is on `bedrock-runtime` and
    `GetFoundationModel` is not, so one object cannot stand in for both. It is a seam at all
    for a reason worth stating: without it a test of this function reaches AWS, and a suite
    that makes network calls is a suite that gets a `--no-network` flag and then stops
    covering the probe at all.
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

    freeze_window(corpus, detector, supervision, porting, sections=ONESHOT_SECTIONS)

    # No `rules_path`: this arm's §1.2 is the empty state by definition (DESIGN §4 — one
    # call, and there is no iteration before it to have written a file). Passing
    # `rules/{lang}.yaml` would show the agent the committed format example as though it
    # were the current rule set, which is the bootstrap file doing a job §5.3 took off it.
    prompt = assemble_task_prompt(lang=lang, corpus=corpus)
    reference = prompt.reference()

    # Before the call and not after, so that if this probe ever does something surprising it
    # does so while the arm is still repeatable. It cannot raise (`model_lifecycle` returns an
    # `unavailable` record for every failure) and it is not counted in `cost` — a control-plane
    # lookup makes no inference and consumes no tokens, and putting it in `llm_calls` would
    # make this arm's cost incomparable to `port-loop`'s for a reason unrelated to either.
    lifecycle = model_lifecycle(model_id, client=control_client)

    kwargs = {} if max_tokens is None else {"max_tokens": max_tokens}
    response = invoke(prompt, model_id=model_id, client=client, **kwargs)
    cost = response.cost()
    model = response.model_record()

    # Before the response is judged. The `text` never enters the line — a length and a
    # hash do (`call_line`).
    append_call(
        call_line(ITERATION, prompt_reference=reference, model=model,
                  response_chars=len(response.text),
                  response_sha256=_digest(response.text),
                  outcome=CALLED, cost=cost, model_lifecycle=lifecycle),
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
            "outcome": FORMAT_FAILURE,
            "run": run,
            "cost": cost,
            "rules_path": rules_file,
            "failure_path": failure,
            # Named as absent rather than omitted, for `sample_reference`'s reason: a
            # caller branching on a missing key branches on a typo just as readily.
            "metrics_path": None,
            "spans_path": None,
        }

    # The model record and the cost go through `run_fold` rather than being written
    # over its metrics afterwards: it owns the one write of metrics.json, and a second
    # writer patching that file is a second answer to what the run block contains.
    #
    # **No `iteration=` here, and `ITERATION` above is not it.** This arm writes its rule
    # file to `iter1/` (`paths.armrules`) and its results to the un-iterated paths, and the
    # asymmetry is not an oversight. The rule path is iteration-scoped for *every* arm
    # because `port-oneshot` and `port-loop` would otherwise share one file and the second
    # to run would overwrite the first's input (DESIGN §5.3). The results path is
    # iteration-scoped only for arms that have rounds, because `iter1/` under an arm with
    # one pass is a false statement about the arm and would put a second copy of a
    # committed result beside it — DESIGN §5.5's "what a non-iterating arm writes". One
    # path answers "which arm's input is this", the other "which round is this", and this
    # arm has an answer to the first question only.
    spans_file, metrics_file, scored = run_fold(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        split=split, rules={lang: rules_file}, model_record=model, cost=cost,
        model_lifecycle=lifecycle, root=ROOT,
    )
    return {
        "outcome": SCORED,
        "run": run,
        "cost": cost,
        "rules_path": rules_file,
        "failure_path": None,
        "metrics_path": metrics_file,
        "spans_path": spans_file,
        "scored": scored,
    }
