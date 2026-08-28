"""The only way to evaluate on a sealed test fold.

    python3 -m src.eval.run_sealed_eval --corpus es-meddocan \
        --arm R/sup-free/port-loop --iteration 8 \
        --purpose "pre-registered final evaluation of the port-loop arm"

`--arm` is one cell in the spelling the log's arm column uses; `--detector/--supervision/
--porting` is the same thing in `run_fold`'s spelling. Exactly one of the two forms.

The loader's seal gate accepts this module by import identity (`base.SEALED_CALLER`),
so an interactive session cannot reach the sealed fold no matter what it passes. What
this module adds on top of the gate:

  - it plans the run against the arm's **committed dev record** and refuses anything
    DESIGN §6.4 forbids — an arm with no final round, a round that is not the final
    one, a rule file that has moved — all of it before the fold is opened;
  - it verifies the frozen split file against the corpus before reading anything, so
    the fold being evaluated is provably the fold that was frozen;
  - it requires a stated purpose, which goes into `results/sealed_eval_log.md`
    together with the arm and the round;
  - it refuses to run on a dirty working tree unless explicitly overridden, so the
    commit hash in the log describes the code that ran (see `--allow-dirty`).

**The order of the file is the order of the guarantees, and that is the design.**
`plan_arm` reads only committed dev artefacts and can refuse for eight reasons;
`load_sealed` appends the log row and opens the fold; `evaluate` scores. Every
checkable refusal is upstream of the append, because the append is the point past
which the opening has happened whether or not the scoring did.

**Scoring is validated on dev before it is ever run on test** (`--verify-dev`). That
mode plans the arm exactly as a sealed run would, detects and scores over the *dev*
fold with the arm's own rule files, and compares the result against the arm's
committed `metrics.json` — `run_fold`'s output. It writes nothing, opens nothing, and
adds no log row. A scoring path whose first execution is the irreversible one is a
scoring path nobody has tested; this is how it gets tested, and it stays the pre-flight
check afterwards (`docs/notes/sealed-eval-preflight.md`).

**One file is written on a sealed run: `paths.sealedmetrics`.** No `spans.jsonl` — its
consumers are the masker and the loop driver, and a sealed fold has no next round — and
no `errors.jsonl`, which is a map of the residual identifiers in the test fold and the
input to the practice CLAUDE.md forbids. See `config/naming.yaml`'s note on that key.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from ..corpora import base
from ..corpora.base import CorpusError, Document, SealError
from ..rules import RuleError, load_for_corpus
from ..rules import _relative as rules_relative
from . import sealed_log
from .run_fold import DEFAULT_SPLIT, detect_fold, load_fold
from .scorer import (
    PATH_AXES, ScorerError, arm_metrics_path, from_documents, iter_metrics_path,
    score, write_metrics,
)

#: The fold a sealed run scores. Named rather than written inline at four call sites,
#: and deliberately the same shape as `run_fold.DEFAULT_SPLIT`: the two modules run the
#: same detection over different folds, and the fold is the one thing that differs.
SEALED_SPLIT = "test"

#: Top-level metrics blocks a sealed run copies **verbatim** from the arm's dev record.
#:
#: Cost is a property of the porting run, and there is only one porting run per arm — the
#: rules were written once, on dev, at whatever they cost. A sealed evaluation runs those
#: finished rules over a second fold; it buys nothing and it must not appear to. Re-measuring
#: would produce a second, smaller cost figure for the same arm and DESIGN §11.3's comparison
#: would then have two numbers to be read off. `termination` travels for the same reason and
#: one step more strongly: it is the record of *why the arm stopped*, which happened on dev
#: and cannot happen again.
#:
#: Optional members are copied only when present, so an arm that predates a schema addition
#: does not gain a block on the way to the sealed record (`caching`, `abandoned_spend` and
#: `model_lifecycle` all record something by their absence — see `scorer.write_metrics`).
COPIED_BLOCKS = (
    "cost", "cost_to_date", "termination", "model_lifecycle", "caching",
    "abandoned_spend",
)

#: Run-block fields a sealed run **replaces**, and the only ones. Everything else in the
#: block describes the arm and travels unchanged, which is what makes the dev and sealed
#: files diffable: they differ in `split`, in these three, and in the scores.
FRESH_RUN_FIELDS = ("split", "generated", "commit", "tree")


class SealedEvalError(SealError):
    """A refusal that stops the run before the fold is opened.

    A subclass of `SealError` rather than a sibling: `main` catches one type, the
    loader's gate raises the parent, and a caller that distinguished them would be
    deciding that some refusals are less final than others. None of them are.
    """


@dataclass(frozen=True)
class ArmPlan:
    """Everything a sealed run needs, all of it read from committed dev artefacts.

    **Assembled before the fold is opened, and that ordering is the whole point of the
    type.** Every refusal in `plan_arm` is a refusal this object's existence rules out,
    so a plan in hand means the arm exists, has terminated, has the round being asked
    for as its final round, and has rule files still on disk. The alternative shape — a
    single function that opened the fold and then discovered the arm had not terminated —
    would have logged an opening it then declined to use, and the log's count is the
    number the paper reports.

    `dev` is the arm's committed `metrics.json` payload. It is carried rather than
    re-read because two reads of it could disagree, and the second one would be after
    the append.
    """

    corpus: str
    detector: str
    supervision: str
    porting: str
    #: The round being scored — the arm's final round, verified against `dev`.
    iteration: int
    #: `{lang: path}`, taken from the dev record's `rules_source` (DESIGN §5.3: which
    #: rule files to load is never inferred, and here it is not even chosen — it is
    #: whatever the arm's own headline was computed from).
    rules: dict[str, Path]
    #: The arm's committed dev `metrics.json`.
    dev: Mapping

    @property
    def arm(self) -> sealed_log.Arm:
        """The log row's arm cell, validated against `naming.yaml`."""
        return sealed_log.Arm(
            detector=self.detector, supervision=self.supervision, porting=self.porting
        )

    @property
    def axes(self) -> dict[str, str]:
        """The four `scorer.PATH_AXES` values."""
        return {k: getattr(self, k) for k in PATH_AXES}

    def describe(self) -> str:
        """One line for a terminal. Axes, round and file counts — no rule ids.

        Rule ids are not printed here and the omission is deliberate rather than
        tidiness: some of this arm's ids embed place names taken from dev text (the
        entries `tools/release_screen.py` acknowledges), and this line goes to a
        terminal, a shell history and a CI log — the three places CLAUDE.md says
        corpus content must not reach and the screener cannot follow it to.
        """
        return (
            f"{self.corpus} {self.arm.cell} round {self.iteration}: "
            f"{len(self.rules)} rule file(s), "
            f"{len(self.dev['run']['rules'])} rules, "
            f"dev leak rate "
            f"{_pct(self.dev['headline']['leak_rate']['value'])}"
        )


# ─── planning: everything that can refuse, before anything is opened ─────────


def plan_arm(
    *,
    corpus: str,
    detector: str,
    supervision: str,
    porting: str,
    iteration: int,
    root: Path | None = None,
) -> ArmPlan:
    """Plan one opening, or refuse. Reads committed dev artefacts only.

    The refusals, in the order they are made and each for its own reason:

    1. **The axes are `naming.yaml` values** (via `sealed_log.Arm`). A results path
       assembled from an undeclared axis mints a cell rather than failing.
    2. **The arm has a committed dev `metrics.json`.** An arm with no dev headline has
       nothing for a test score to be compared against, and the comparison is the claim.
    3. **That record is a dev record.** `split` is a required run field and not a path
       component, so a file at `paths.metrics` saying `test` is a record that has already
       been through something this protocol forbids.
    4. **It carries a `termination` block.** `port-oneshot-nofence`'s file predates the
       block (schema 6), and an arm whose stopping state is unrecorded has no final round
       to identify — so the round being asked for cannot be checked against anything.
    5. **The arm has terminated.** `reason: null` is the record of an arm still running
       (`termination.Termination.stop`), and DESIGN §6.4 opens the seal after termination.
    6. **The round asked for is the arm's final round.** This is §6.4's substance and the
       one refusal that is about the *number* rather than the arm; see the message.
    7. **The rule files the dev headline was computed from are still on disk**, at the
       paths that record names.
    8. **The round-scoped record agrees with the arm-scoped one** where both exist
       (DESIGN §5.5's duplication rule). Checked rather than assumed: the un-iterated
       file is rewritten every round, so if it were somehow written by a *later* run than
       round N, the two would differ — and the arm-scoped file is where every field this
       function trusts comes from.

    `iteration` is required and has no default. §6.4 permits one opening per arm and
    scores its final round, so "which round" is always answerable and never optional —
    and a default would be a round chosen by this module rather than by the arm's own
    termination record.
    """
    arm = sealed_log.Arm(detector=detector, supervision=supervision, porting=porting)
    if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 1:
        raise SealedEvalError(
            f"--iteration must be a positive integer, got {iteration!r}. Rounds are "
            "1-indexed; an arm that does not iterate has round 1 as its final round "
            "(termination.not_applicable), which is DESIGN §6.4's vacuous case and not "
            "an absent one."
        )

    dev_file = arm_metrics_path(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        root=root,
    )
    if not dev_file.exists():
        raise SealedEvalError(
            f"no committed dev record at {rules_relative(dev_file)}. A sealed evaluation "
            "reports a test score beside the arm's dev headline, and DESIGN §6.4 reads "
            "the arm's final round out of that file's termination block — so an arm with "
            "no dev record cannot be opened at all. Close the arm on dev first "
            "(`python3 -m src.eval.run_fold`)."
        )
    try:
        dev = json.loads(dev_file.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SealedEvalError(
            f"{rules_relative(dev_file)} is not JSON ({exc.__class__.__name__}). It is "
            "the arm's published record and every premise of this run is read from it; a "
            "truncated one is not a record to plan against."
        ) from exc

    run = dev.get("run") or {}
    if run.get("split") != DEFAULT_SPLIT:
        raise SealedEvalError(
            f"{rules_relative(dev_file)} records split={run.get('split')!r}, not "
            f"{DEFAULT_SPLIT!r}. `split` is a required run field and deliberately not a "
            "path component, so this path holds whichever fold was scored last — and a "
            "test value here means a sealed score has already been written over the "
            "arm's dev headline, which is the state config/naming.yaml's `sealedmetrics` "
            "key exists to make impossible. Do not proceed; work out how it got there."
        )

    termination = dev.get("termination")
    if not termination:
        raise SealedEvalError(
            f"{rules_relative(dev_file)} carries no termination block, so this arm has no "
            "recorded final round and DESIGN §6.4 has nothing to check --iteration "
            "against. The block became required at schema 6; a record written before that "
            "predates it rather than omitting it. Re-run the arm's closing step "
            "(`python3 -m src.eval.run_fold`) to regenerate the record — that runs on dev "
            "and changes no published number except by adding the block."
        )
    if termination.get("reason") is None:
        raise SealedEvalError(
            f"{rules_relative(dev_file)} records termination.reason=null, which is the "
            "record of an arm that has not stopped (src/termination.py: `stop` is "
            "`reason is not None`). DESIGN §6.4 opens the seal *after* termination by the "
            "arm's own pre-registered rule, so there is no final round yet — the round "
            "this file describes is simply the most recent one."
        )
    final = termination.get("iterations")
    if not isinstance(final, int) or final < 1:
        raise SealedEvalError(
            f"{rules_relative(dev_file)} records termination.iterations={final!r}, which "
            "is not a round count. It is the number DESIGN §6.4's final round is read "
            "from and there is no second source for it."
        )
    if iteration != final:
        raise SealedEvalError(
            f"--iteration {iteration} is not this arm's final round ({final}, from "
            f"{rules_relative(dev_file)}'s termination block). DESIGN §6.4 scores the "
            "final round and refuses any other, and the reason is the cost column: "
            "`cost_to_date` is the arm's total through its last round, so a round-"
            f"{iteration}-of-{final} headline would be {iteration} rounds of quality "
            f"published beside {final} rounds of spend. No run exists that both cost that "
            "and scored that. If the earlier round is the one worth reporting, that is a "
            "dev result and it is already published under this arm's iter"
            f"{iteration}/ directory."
        )

    sources = run.get("rules_source") or {}
    if not sources:
        raise SealedEvalError(
            f"{rules_relative(dev_file)} names no rules_source. That field is which rule "
            "files the arm's headline was computed from (DESIGN §5.3), and it is where "
            "this run's inputs come from — not reconstructed from the axes, because a "
            "reconstruction can agree with the path template while disagreeing with what "
            "actually ran."
        )
    rules = {lang: (root or base.ROOT) / rel for lang, rel in sorted(sources.items())}
    missing = sorted(lang for lang, path in rules.items() if not path.exists())
    if missing:
        raise SealedEvalError(
            f"the rule file(s) for {missing} named by {rules_relative(dev_file)}'s "
            "rules_source are not on disk. The sealed run scores the arm's own committed "
            "rules and nothing else; a substitute — the bootstrap file, an adjacent "
            "round — would produce a test number for rules whose dev number came from "
            "somewhere else."
        )

    iter_file = iter_metrics_path(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        iteration=final, root=root,
    )
    if iter_file.exists():
        try:
            scoped = json.loads(iter_file.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise SealedEvalError(
                f"{rules_relative(iter_file)} is not JSON ({exc.__class__.__name__}). It "
                f"is round {final}'s own record and DESIGN §5.5 requires it to be "
                "identical to the arm's; an unreadable one cannot be checked."
            ) from exc
        differing = sorted(
            field for field in ("rules_source", "rules_version", "rules")
            if (scoped.get("run") or {}).get(field) != run.get(field)
        )
        if differing:
            raise SealedEvalError(
                f"{rules_relative(dev_file)} and {rules_relative(iter_file)} disagree "
                f"about {differing}. DESIGN §5.5 requires the final round's record and "
                "the arm's to be identical — they are written from one scoring pass — so "
                "a disagreement means one of them was written by a different run than the "
                "other, and this function reads every premise of the sealed evaluation "
                "out of the arm-scoped one. The field names are reported; no rule id is "
                "(CLAUDE.md)."
            )

    return ArmPlan(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        iteration=iteration, rules=rules, dev=dev,
    )


# ─── the sealed read ────────────────────────────────────────────────────────


def load_sealed(
    plan: ArmPlan, *, purpose: str, allow_dirty: bool = False
) -> list[Document]:
    """Every fold including the sealed one, after logging the access.

    Returns all folds rather than the sealed fold alone: a test evaluation reports
    per-fold numbers, and re-loading train+dev separately would mean two reads and
    two chances for them to disagree.

    **Takes a plan rather than a corpus id**, which is the change that closed the log's
    arm cell (2026-08-26). This function used to pass `arms="none (access check)"` — a
    literal, true of the access-path smoke test it then was, and a string the row that
    carried the experiment's only test score would have inherited unchanged. Requiring a
    validated plan makes an opening with no arm unrepresentable rather than merely
    discouraged.
    """
    commit, tree = sealed_log.tree_state()
    if tree != "clean" and not allow_dirty:
        raise SealError(
            f"the working tree is {tree}, so commit {(commit or 'unknown')[:12]} "
            "does not describe the code that would run, and the log row would name "
            "a commit that never produced these numbers. Commit first. If the "
            "change is genuinely irrelevant to the evaluation, pass --allow-dirty: "
            "the run then proceeds and the row records tree=dirty, which is the "
            "honest version of the same claim."
        )

    loader = _loader_for(plan.corpus)
    # Verified before the sealed read, not after: if the frozen split and the
    # corpus disagree, the run must not happen at all — and the check itself must
    # not need the sealed fold, which is why it runs on the unsealed load.
    _verify_frozen_split(loader, plan.corpus)

    # `sealed=True` triggers the gate, which appends to the log and aborts if the
    # append fails. The logging is deliberately *not* done here: one read must
    # produce exactly one row, so the append lives at the gate — the point past
    # which the fold becomes reachable — and nowhere else. The arm and the round go
    # with it, so the row says what was opened and not merely that something was.
    return loader.load(
        sealed=True, purpose=purpose, arm=plan.arm, iteration=plan.iteration
    )


def _loader_for(corpus_id: str) -> base.CorpusLoader:
    """The loader `run_fold` and `--verify-dev` use, resolved from the one registry.

    A one-line delegation and it earns the line: this function used to name
    `MeddocanLoader` itself, so the sealed path and every other path had separate answers
    to "which loader reads this corpus" that happened to agree. See `base.loader_for`.
    """
    return base.loader_for(corpus_id)


def _verify_frozen_split(loader: base.CorpusLoader, corpus_id: str) -> None:
    """The unsealed folds must still match the frozen file.

    Only the unsealed folds can be checked here, and that is enough for what this
    check is for: if train or dev has drifted since the freeze, the corpus on disk
    is not the corpus the split file describes, and the sealed fold's recorded
    summaries are equally suspect.
    """
    from ..split import read

    record = read(corpus_id)
    unsealed = base.CorpusLoader.load(loader)
    assigned = {
        doc_id: fold
        for fold, block in record["folds"].items()
        for doc_id in block["document_ids"]
    }
    for doc in unsealed:
        if assigned.get(doc.doc_id) != doc.split:
            raise SealError(
                f"{corpus_id}/{doc.doc_id}: the frozen split file and the corpus on "
                "disk disagree about this document's fold. The sealed evaluation "
                "does not run."
            )


# ─── scoring ────────────────────────────────────────────────────────────────


def score_fold(
    plan: ArmPlan, docs: Sequence[Document], *, split: str
) -> tuple[dict, float]:
    """Detect with the plan's rule files over `docs`, score, return (scored, seconds).

    **The one scoring implementation for both folds**, which is why `--verify-dev` is
    worth anything at all: a dev run and a sealed run differ in which documents reach
    this function and in nothing else. A separate sealed scorer could pass its dev
    rehearsal and still differ on test, and the difference would be invisible from
    either side — `run_fold`'s argument for one `detect_fold`, one layer up.

    `detect_fold` is imported from `run_fold` for the same reason rather than
    reimplemented here. This module adds the seal's guarantees; it does not add a second
    answer to "did this rule fire".
    """
    subset = [d for d in docs if d.split == split]
    if not subset:
        raise SealedEvalError(
            f"{plan.corpus}: the {split} fold is empty in the documents loaded. The "
            f"split file assigns folds (splits/{plan.corpus}.json); an empty fold means "
            "the corpus on disk and the frozen split disagree, and nothing was scored."
        )
    ruleset = load_for_corpus(plan.corpus, paths=plan.rules)
    started = time.monotonic()
    predictions = detect_fold(subset, ruleset, detector=plan.detector)
    elapsed = time.monotonic() - started
    pairs, excluded = from_documents(subset, predictions)
    return score(pairs, excluded_gold=excluded), elapsed


def sealed_run_block(plan: ArmPlan) -> dict:
    """The sealed record's run block: the arm's, with `FRESH_RUN_FIELDS` replaced.

    Copied rather than reassembled, and the reason is the same one `COPIED_BLOCKS`
    exists for. `model_id`, `rules_version`, `rules_source` and the rule id list are
    facts about the arm that were established when it ran on dev; re-deriving them here
    would make this module a second assembler of the block, and the two assemblers would
    agree until the day they did not. What this run genuinely observed is four fields:
    the fold it scored, when it ran, and against which revision and tree state.
    """
    commit, tree = sealed_log.tree_state()
    return {
        **dict(plan.dev["run"]),
        "split": SEALED_SPLIT,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": commit,
        "tree": tree,
    }


def evaluate(
    plan: ArmPlan, *, purpose: str, allow_dirty: bool = False,
    root: Path | None = None,
) -> tuple[Path, dict]:
    """Open the seal, score the test fold, write `paths.sealedmetrics`. Returns (path, scored).

    Called only after `plan_arm` has returned, so every refusal that can be made from
    committed artefacts has already been made. What can still fail here is a read or a
    write, and by then the log row exists — which is the intended ordering: the opening
    happened, and a crash must not be able to un-record it.
    """
    docs = load_sealed(plan, purpose=purpose, allow_dirty=allow_dirty)
    scored, _ = score_fold(plan, docs, split=SEALED_SPLIT)
    dev = plan.dev
    path = write_metrics(
        scored,
        run=sealed_run_block(plan),
        sealed=True,
        root=root,
        **{k: dev[k] for k in COPIED_BLOCKS if k in dev},
    )
    return path, scored


# ─── dev verification ───────────────────────────────────────────────────────


#: The one key `score()` returns that `--verify-dev` does not compare, and the only
#: exclusion there is: `write_metrics` folds it into the run block rather than writing it
#: at the top level, so the record has nowhere for it to be compared against.
#:
#: **Stated as an exclusion rather than an inclusion, because the inclusion list was
#: wrong the first time it was written** (2026-08-26). It read
#: `("headline", "counts", "complementarity", "per_type", "overall")`, and three of those
#: five are nested inside `modes` rather than top-level — so the comparison skipped them,
#: and the success line printed all five names while checking two. A hand-maintained list
#: of what to compare fails silently in exactly that direction, and this check is the
#: pre-flight for a run that happens once. Derived from `score()`'s own output, it cannot.
UNCOMPARED_KEYS = ("scorer_version",)


def compared_keys(scored: Mapping) -> list[str]:
    """Everything `score()` produced that the record should hold, sorted."""
    return sorted(set(scored) - set(UNCOMPARED_KEYS))


def verify_dev(plan: ArmPlan) -> tuple[bool, list[str], list[str]]:
    """Score the dev fold through `score_fold` and compare with the arm's record.

    Returns `(agrees, compared, differing)`. Writes nothing, opens nothing, adds no log
    row, and never touches `sealed/`.

    **This is the test that the sealed path computes what `run_fold` computed.** The
    arm's `metrics.json` was written by `run_fold` over the dev fold with these rule
    files; this reproduces it through the sealed module's own scoring path. Agreement
    means the two paths differ only in which documents they are handed — which is the
    property that makes a once-only run on test defensible, and the only way to
    establish it before the run rather than after it.

    `compared` is returned alongside the verdict so the caller reports what it actually
    checked instead of what it meant to check. That is not a formality: see
    `UNCOMPARED_KEYS`.

    A key the arm's record does not carry counts as differing rather than being skipped.
    `plan.dev.get(key)` returns `None`, which no scored block equals, so the omission is
    a difference by construction — a comparison that passed over a missing key would get
    weaker as the schema grew.
    """
    docs = load_fold(plan.corpus, DEFAULT_SPLIT)
    scored, _ = score_fold(plan, docs, split=DEFAULT_SPLIT)
    compared = compared_keys(scored)
    differing = [key for key in compared if scored[key] != plan.dev.get(key)]
    return not differing, compared, differing


# ─── cli ────────────────────────────────────────────────────────────────────


#: The three axes `--arm` is the compact spelling of, in the order `Arm.cell` prints them.
#: `corpus` is not among them: it is a separate flag because it is a separate decision —
#: the same arm is run on every corpus, and `Arm` does not carry it either.
ARM_AXES = ("detector", "supervision", "porting")


def axes_from_arm(cell: str) -> dict[str, str]:
    """`"R/sup-free/port-loop"` → the three axis values, or raise.

    The inverse of `sealed_log.Arm.cell`, split on the same character, so the arm column
    of a log row is a valid `--arm` argument without transcription.

    The values themselves are not checked here. `sealed_log.Arm` checks all three against
    `naming.yaml` and `plan_arm` constructs one before anything else happens, so a check
    here would be a second place where the vocabulary is known.
    """
    parts = [part.strip() for part in cell.split("/")]
    if len(parts) != len(ARM_AXES) or not all(parts):
        raise SealedEvalError(
            f"--arm {cell!r} is not an arm cell. The form is "
            f"{'/'.join(ARM_AXES)} — three non-empty values separated by '/', exactly as "
            "the arm column of results/sealed_eval_log.md prints them, so a cell read off "
            "a row can be passed straight back."
        )
    return dict(zip(ARM_AXES, parts))


def resolve_axes(
    *,
    arm: str | None,
    detector: str | None,
    supervision: str | None,
    porting: str | None,
) -> dict[str, str]:
    """The three axis values from whichever spelling was used. Exactly one form.

    Both spellings exist because each is what one would type from a different starting
    point: the axis flags are `run_fold`'s CLI, and `--arm` is what `metrics.json` and the
    log's arm column print. Mixing them is refused rather than merged — an `--arm` and a
    `--porting` that disagreed would otherwise be settled by whichever the code read
    second, and this CLI's arguments select the fold that gets opened once.
    """
    given = {
        name: value
        for name, value in (
            ("detector", detector), ("supervision", supervision), ("porting", porting),
        )
        if value is not None
    }
    if arm is not None and given:
        raise SealedEvalError(
            f"--arm was given together with {sorted('--' + n for n in given)}. They are "
            "two spellings of the same three values; pass one form or the other."
        )
    if arm is not None:
        return axes_from_arm(arm)
    missing = [f"--{name}" for name in ARM_AXES if name not in given]
    if missing:
        raise SealedEvalError(
            f"the arm is incompletely specified: {missing} missing. Give all three axis "
            f"flags, or --arm {'/'.join(ARM_AXES)} as one cell. There is no default for "
            "any of them: the arm is what the log row records as opened."
        )
    return given


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--corpus", required=True, help="corpus id from naming.yaml")
    parser.add_argument(
        "--arm",
        help="the arm as one cell, detector/supervision/porting — the spelling the log's "
             "arm column and metrics.json use. Alternative to the three axis flags below; "
             "give one form or the other.",
    )
    parser.add_argument("--detector", help="detector axis value (or use --arm)")
    parser.add_argument("--supervision", help="supervision axis value (or use --arm)")
    parser.add_argument("--porting", help="porting axis value (or use --arm)")
    parser.add_argument(
        "--iteration", type=int, required=True,
        help="the round to score. DESIGN §6.4 permits only the arm's final round, and "
             "this is checked against the arm's committed termination block before "
             "anything is opened. Required rather than defaulted to that number: the "
             "round is stated by the person opening the seal and then verified, so a "
             "mismatch is a refusal instead of a silent correction.",
    )
    parser.add_argument(
        "--purpose",
        help="why the test fold is being opened; recorded in the log verbatim. "
             "Required unless --verify-dev, which opens nothing.",
    )
    parser.add_argument(
        "--verify-dev", action="store_true",
        help="do not open the seal. Plan the arm, score the *dev* fold through the same "
             "scoring path, and report whether it reproduces the arm's committed "
             "metrics.json. Writes nothing and adds no log row.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help=(
            "proceed with uncommitted changes; the log row records tree=dirty. "
            "Refused by default because the commit hash would otherwise name code "
            "that never ran."
        ),
    )
    args = parser.parse_args(argv)

    if not args.verify_dev and not args.purpose:
        # Checked here rather than made `required=True` on the flag, because
        # `--verify-dev` genuinely has nothing to state a purpose for: no row is added,
        # so a purpose would be a sentence written into no record.
        print("--purpose is required for a sealed evaluation: it is the log row's "
              "account of why the test fold was opened. --verify-dev does not open it "
              "and does not need one.", file=sys.stderr)
        return 2

    try:
        axes = resolve_axes(
            arm=args.arm, detector=args.detector,
            supervision=args.supervision, porting=args.porting,
        )
    except SealedEvalError as exc:
        # 2 rather than 1: this is a usage error in argparse's sense — nothing about the
        # arm was looked up yet — and `--purpose`'s check above already returns 2.
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    try:
        plan = plan_arm(corpus=args.corpus, iteration=args.iteration, **axes)
    except (CorpusError, SealError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(f"plan    {plan.describe()}")

    if args.verify_dev:
        try:
            agrees, compared, differing = verify_dev(plan)
        except (CorpusError, SealError, ScorerError, RuleError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        if agrees:
            # The keys actually compared, not a list of what the check was meant to
            # cover — see UNCOMPARED_KEYS for what went wrong when those differed.
            print("verify  dev scoring reproduces the arm's committed metrics.json "
                  f"({', '.join(compared)})")
            print("nothing was opened and nothing was written")
            return 0
        print(f"verify  FAILED — {differing} differ from the arm's committed "
              "metrics.json. Do not run the sealed evaluation: the two scoring paths "
              "disagree on dev, and on test there would be nothing to compare against.",
              file=sys.stderr)
        return 1

    try:
        path, scored = evaluate(
            plan, purpose=args.purpose, allow_dirty=args.allow_dirty
        )
    except (CorpusError, SealError, ScorerError, RuleError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1

    leak = scored["headline"]["leak_rate"]
    lower = scored["headline"]["leak_rate_lower_bound"]
    counts = scored["counts"]
    print(f"sealed read of {args.corpus} recorded in {sealed_log.LOG.name}")
    print(f"{args.corpus} {SEALED_SPLIT}: {counts['documents']['total']} documents, "
          f"{counts['gold']['in_scope']} in-scope gold spans, "
          f"{counts['pred']} predictions")
    # Leak rate is the headline and F1 is not (CLAUDE.md), and this is the one output
    # where that ordering matters most: it is the number the paper reports off a fold
    # that will not be scored again.
    print(f"leak rate {_pct(leak['value'])} ({leak['mode']}) — headline; "
          f"{_pct(lower['value'])} ({lower['mode']}) as the lower bound")
    print(f"metrics {path.relative_to(base.ROOT)}")
    print(f"this corpus's test fold has now been opened "
          f"{sealed_log.count_runs(args.corpus)}x")
    return 0


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


if __name__ == "__main__":
    # Call the *imported* module's `main`, not this file's copy of it.
    #
    # Under `python -m src.eval.run_sealed_eval` — the invocation this module's own
    # docstring documents and the only one it supports — this file executes as
    # `__main__`. So `main`'s frame carries `__name__ == "__main__"`, the string
    # `base.SEALED_CALLER` appears nowhere on the call stack, and the loader's
    # identity check refuses the run. Importing the module here puts a frame with the
    # real name on the stack below `main`, which is what the check asks for and what
    # it means: the module that vouches for the read is genuinely running the read.
    #
    # Found on 2026-08-28 by the first attempt to open a fold. Nothing caught it
    # earlier because every test imports this module (so `__name__` is already right)
    # and `--verify-dev`, the pre-flight rehearsal, never reaches `load_sealed` —
    # which `docs/notes/sealed-eval-preflight.md` item 4 states as the limit of what
    # that rehearsal establishes. The refusal was upstream of the log append, so the
    # failed attempt opened nothing and added no row.
    from src.eval.run_sealed_eval import main as _main

    raise SystemExit(_main())
