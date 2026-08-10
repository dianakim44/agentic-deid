"""The `port-oneshot` orchestrator. This file holds its window freeze.

`port-oneshot` is the baseline rung: one LLM call writes `rules/{lang}.yaml` and the arm
is over (DESIGN §4). Before the call it freezes its window — the RuleAuthor prompt
template and `config/sampling.yaml`, hashed — and that freeze is what this module
implements. The call itself, the schema validation and the scoring follow.

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
"""
from __future__ import annotations

import json
import string
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import regex

from . import sample
from .corpora.base import ROOT, CorpusError, axis, path_template
from .sample import WINDOW_FILES, window_hashes

#: The two `paths` keys this arm writes and reads, both from `config/naming.yaml` and
#: neither as a literal here (DESIGN §11.2 requires it of an output path: a module holding
#: its own copy is a second place the answer can change). `armfreeze` is the agent arms'
#: freeze key and is deliberately *not* `humanfreeze` — see the module docstring.
FREEZE_KEY = "armfreeze"
LOG_KEY = "agentlog"

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
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    return [field for field in ("prompt_sha256", "sampling_sha256")
            if frozen.get(field) != now[field]]
