"""Run a rule set over one fold and score it — the arm's closing step.

    python3 -m src.eval.run_fold --corpus es-meddocan \
        --detector R --supervision sup-free --porting port-oneshot

Writes two files under `results/{corpus}/{detector}/{supervision}/{porting}/`:
`spans.jsonl` (the predictions, with DESIGN §3 provenance and no surface forms) and
`metrics.json` (the scorer's output). Until this existed no arm could be closed: rules
could be written and counted, but a dev-wide score — the number every comparison in
DESIGN §4 is made of — had nowhere to come from.

An iterating arm passes `iteration=N` and gets the round's whole record: `iter{N}/` holds
that round's `spans.jsonl` and `metrics.json` (`paths.iterspans`, `paths.itermetrics`) plus
`errors.jsonl`, the per-span error list the next round's §1.4 window is drawn from —
deny-listed, and written from the same scoring pass as the other two. The un-iterated pair
is written as well, every round, so `paths.metrics` holds every arm's latest and finally its
final score without anyone having to know which round is last (DESIGN §5.5). One argument
for all three files because they are one record: a round whose score is scoped and whose
predictions are not leaves an error list nothing can re-derive.

The iteration-scoped paths are not returned. Each is a pure function of the four axes and
the round (`iter_spans_path`, `errors_path`, `scorer.iter_metrics_path`), so the driver that
asked for them can name them, and the return stays the pair every caller reads.

**This is the execution path, and `tools/check_rules.py` is a sample view of it.**
The tool calls `detect_fold()` from here rather than iterating rules itself. Two
implementations of detection drift, and the shape the drift takes is the worst
available: *the sample says a rule fires and the fold-wide run says it does not*, or
the reverse, with no way to tell which is right. An author cannot act on that and
neither can a reader. So there is one detection function, one place where a `Span`
becomes a `Mark`, and the difference between the tool and this module is which spans
they show, never which spans exist.

**The fold is an argument here and hardcoded in the tool**, and that asymmetry is
deliberate. `check_rules.py` is typed by a person forty times in an evening, so a
`--split` flag on it is a sealing violation with a countdown (CLAUDE.md). This module
is called by the orchestrator, defaults to dev, and refuses `test` outright: sealed
evaluation goes through `src.eval.run_sealed_eval`, which is the only importer the
loader's gate accepts, and which appends a row to `results/sealed_eval_log.md` before
anything is read. Passing `--split test` here is refused with that pointer rather
than quietly reading whatever the loader happens to return.

**Which rule files to load is an argument, never inferred** (DESIGN §5.3). An arm's rule
files live under the arm — `paths.armrules`, with the four axes and the iteration number
in the path — and this module is told which ones to read. `--iteration N` builds those
paths for the corpus's languages; `rules/{lang}.yaml` is the format example and the
bootstrap state, not a location any arm writes to. Deriving the input path from the axis
arguments would make the input a function of the run block, which is how a run ends up
reading its own results directory.

**`model_id` is `none` for a rule-only arm** (DESIGN §4). The `R` arm calls no
language model, and the scorer requires the field: an absent value cannot be told
apart from an unrecorded one, which is the same reason cost is zeros rather than
missing. The value is read from `config/naming.yaml`, not written here as a literal.
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

from ..corpora import load
from ..corpora.base import (
    ROOT, CorpusError, Document, axis, model_id_absent, path_template, round_path,
)
from ..rules import RuleError, RuleSet, load_for_corpus
# `_relative` for `src/orchestrate.py`'s reason, one message-shape over: `src/rules.py`
# decides what a path looks like in something a person reads, repo-relative where it can be
# and filename-with-marker where it cannot, because an absolute path names a home directory
# and — on a machine where the corpus sits beside the repo — a DUA layout. A refusal about
# `errors.jsonl` goes to a terminal and a CI log, which is exactly where CLAUDE.md says a
# corpus path must not appear.
from ..rules import _relative as rules_relative
from ..sample import ErrorSpan, SamplingError
from ..termination import PendingTermination, Termination, not_applicable
from . import sealed_log
from .scorer import (
    PATH_AXES, REQUIRED_COST, ScorerError, check_run, error_spans, from_documents,
    iter_metrics_path, score, write_metrics,
)

#: The fold this runs on unless told otherwise. Named rather than defaulted inline so
#: the one place it is decided is visible.
DEFAULT_SPLIT = "dev"

#: Cost for an arm that calls no model. Zeros, not absent (CLAUDE.md, and the scorer
#: refuses a partial block): a rule run genuinely made zero LLM calls, and that is a
#: measurement about the arm rather than a gap in the record. `wall_seconds` is the
#: exception — it is measured, because a rule pass does take time and a reader
#: comparing arms on cost needs the compute side of `R` to be real.
NO_LLM_COST = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

#: The only run-block fields `model_record` may carry — `bedrock.Response.model_record()`'s
#: three. A closed set rather than a general override channel: a caller that could put any
#: key into the run block from here would be a second assembler of it, and the reason this
#: function assembles it is that one writer per record is what makes the record checkable.
#: `scorer.REQUIRED_RUN` asks only for `model_id`; the other two are required by
#: `src/orchestrate.py` of its own runs, because that is the writer that can observe them
#: (DESIGN §10 A2).
MODEL_FIELDS = ("model_id", "model_id_reported", "model_id_resolution")

#: `errors.jsonl`'s six fields, in the order they are written — DESIGN §5.5.1's list, and
#: `ErrorSpan`'s own fields (`src/sample.py`).
#:
#: **Named once because two functions depend on it being closed.** `write_errors` whitelists
#: rather than dumping the object, so a field added to `ErrorSpan` is not published; and
#: `read_errors` refuses a row carrying anything else, so a field added to the *file* does not
#: pass silently. Two copies of this tuple is how the writer stops publishing a field while
#: the reader starts accepting it — and §5.5.1's rule is that a `text`, `surface`, `context`
#: or `snippet` field here is the signal to refuse the field, which is a rule with one place
#: to hold.
ERROR_FIELDS = ("doc_id", "span_index", "phi_type", "kind", "start", "end")

#: `spans.jsonl`'s nine fields, in the order they are written — the geometry, then DESIGN §3's
#: four provenance values, then `agent_actions`.
#:
#: Named for `ERROR_FIELDS`' reason and one that is sharper here: `write_spans` whitelists so a
#: field added to `Span` is not published, and `read_spans` refuses a row carrying anything
#: else. Two copies of this tuple is how the writer stops publishing a field while the reader
#: starts accepting it — and this path is on the screener's ALLOW list, so an added field is
#: published rather than merely written.
#:
#: `surface` and `subtype` are absent and cannot be added: the first is the text this file
#: exists not to carry, and the second is a corpus's own vocabulary that no prediction has.
SPAN_FIELDS = ("doc_id", "start", "end", "phi_type", "layer", "detector", "rule_id",
               "score", "agent_actions")


class FoldRunError(Exception):
    """Anything that stops this run before it writes.

    One type, for `scorer.ScorerError`'s reason: every case is "stop and tell a
    person", and no caller has a recovery path that differs by cause. A partially
    written results directory is worse than no results directory.
    """


# ─── detection ──────────────────────────────────────────────────────────────


def detect_fold(
    docs: Sequence[Document], ruleset: RuleSet, *, detector: str = "R"
) -> dict[str, list]:
    """Every rule's spans per document: `{doc_id: [Span, ...]}`.

    **The single detection implementation.** `tools/check_rules.py` calls this and
    then samples the result; this module calls it and then scores the result. The
    alternative — each iterating `rule.finditer()` itself — produces two answers to
    "did this rule fire" whose disagreement is undiagnosable from either side.

    Overlaps are not resolved and duplicates are not collapsed. `RuleSet.detect`
    returns every match from every rule by design (DESIGN §4, §9.3): merge policy is
    a replaceable strategy and a detector that resolved its own overlaps would make
    every merge policy score alike. The scorer collapses byte-identical spans for the
    assignment matching and counts what it collapsed.

    `detector` is the arm's `detector` axis value and is copied onto every span. It is
    not derived from the ruleset, and the span's `layer` comes from the rule that
    matched (DESIGN §3) — neither is inferred from the other.
    """
    return {doc.doc_id: ruleset.detect(doc.text, detector=detector) for doc in docs}


def load_fold(corpus: str, split: str) -> list[Document]:
    """The documents of one unsealed fold.

    Refuses `test` before the loader is reached rather than after. The loader's gate
    would refuse it anyway — the sealed fold is not under the corpus root, so there is
    nothing there to return — but a caller that got an empty list back would read it as
    "the fold has no documents" and go looking for a corpus problem. Naming the actual
    rule costs one branch.
    """
    if split not in axis("split"):
        raise FoldRunError(
            f"{split!r} is not a value of the split axis in config/naming.yaml "
            f"(have: {sorted(axis('split'))})."
        )
    if split == "test":
        raise FoldRunError(
            "the test fold is sealed and this is not the path to it. Sealed "
            "evaluation runs through `python3 -m src.eval.run_sealed_eval`, which is "
            "the importer the loader's gate accepts and which appends the access to "
            "results/sealed_eval_log.md before anything is read (CLAUDE.md, "
            "DESIGN §6.1). This module cannot reach it and does not try."
        )
    docs = [d for d in load(corpus) if d.split == split]
    if not docs:
        raise FoldRunError(
            f"{corpus}: the {split} fold is empty. The split file assigns folds "
            f"(splits/{corpus}.json); an empty one means the corpus on disk and the "
            "frozen split disagree."
        )
    return docs


# ─── output ─────────────────────────────────────────────────────────────────


def spans_path(
    run: Mapping[str, str], root: Path | None = None, *, iteration: int | None = None
) -> Path:
    """`paths.spans` for this arm, from naming.yaml.

    Beside `metrics.json` by construction: both format the same `PATH_AXES` from the
    same validated run block, so a spans file cannot end up in a different arm's
    directory from the metrics computed on it.

    `iteration` routes to `paths.iterspans` — that round's predictions beneath the same
    directory (DESIGN §5.5). Routed through `iter_spans_path` rather than formatted here,
    so the template has one reader, and the same argument holds one module over for
    `scorer.metrics_path`. The pairing survives the widening: at a given round, this and
    `scorer.metrics_path(run, iteration=…)` still land in one directory, because both
    format `PATH_AXES` from the same validated block plus the same round.
    """
    check_run(run)
    if iteration is not None:
        return iter_spans_path(
            **{k: run[k] for k in PATH_AXES}, iteration=iteration, root=root)
    template = path_template("spans")
    return (root or ROOT) / template.format(**{k: run[k] for k in PATH_AXES})


def _round_path(
    key: str, *, corpus: str, detector: str, supervision: str, porting: str,
    iteration: int, artefact: str, root: Path | None = None,
) -> Path:
    """This module's two round-scoped paths (`iterspans`, `itererrors`), through `round_path`.

    Kept as a wrapper rather than dissolved into its two callers, because what it holds is
    this module's *error type* and this module's positional shape: `corpora.base.round_path`
    takes the four axes as keywords and the exception class as an argument, and repeating
    `error=FoldRunError` at each call site is a second place the type is decided.

    `artefact` is the subject of the refusal — the message names which of the round's files
    was about to be misplaced, because "iteration must be an integer >= 1" with two callers
    is a message that does not say which call to look at.

    **The check itself is no longer here** (2026-08-13). It was, in four modules, each
    documenting that the repetition was the module boundary and not an oversight because each
    raises the type its callers catch. That reasoning was right about the type and it is now
    a parameter; the fifth copy the loop driver's audit-report path would have needed is what
    settled it. See `round_path`.
    """
    return round_path(
        key, iteration=iteration, artefact=artefact, error=FoldRunError, root=root,
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
    )


def iter_spans_path(
    *, corpus: str, detector: str, supervision: str, porting: str, iteration: int,
    root: Path | None = None,
) -> Path:
    """`paths.iterspans` for one round — `iter{N}/spans.jsonl`, that round's predictions.

    **Scoped with the score and not left arm-wide** (DESIGN §5.5). An iterating arm that
    scoped only its `metrics.json` would keep every round's number and overwrite its
    predictions every round, which is worse than losing both: `iter{N}/errors.jsonl` is
    *derived* from that round's predictions against gold, so a round-3 error list whose
    predictions round 8 overwrote is a list nothing can re-derive or check. The three files
    a round produces are one record and they are scoped together.

    Keyword axes, for `errors_path`'s reason: `spans_path` has one interested party and the
    iteration-scoped paths have two, the second being the loop driver, which holds four
    axes and a round and no run block.
    """
    return _round_path(
        "iterspans", corpus=corpus, detector=detector, supervision=supervision,
        porting=porting, iteration=iteration, artefact="prediction list", root=root)


def errors_path(
    *, corpus: str, detector: str, supervision: str, porting: str, iteration: int,
    root: Path | None = None,
) -> Path:
    """`paths.itererrors` for one round — `iter{N}/errors.jsonl` beside that round's spans.

    Iteration-scoped and not arm-scoped, which is the whole point of the file: "which
    errors was the agent shown at iteration 4" has to be answerable after the run, and one
    path per arm leaves only the last round's list (DESIGN §5.5, §5.3's argument for
    per-iteration rule files).

    **Keyword axes rather than a run block, and the difference is the number of interested
    parties.** The un-iterated `spans.jsonl` has one — this module writes it and nobody
    looks it up. This file has two: `run_fold` writes it from a run block it assembled, and
    the loop driver *reads* it to build the next round's pool, holding the four axes and no
    run block. Handing the driver a run block to assemble would make it a second assembler
    of the thing one-writer-per-record exists to prevent, so the signature is
    `rules.arm_rules_path()`'s — the other iteration-scoped path, for the same reason.

    Every component is validated (`_round_path`), axes against `naming.yaml` and the round
    for being a round, because a results path names the cell an artefact belongs to: an
    unknown component mints a cell instead of failing, and `iter1.0/` or `iter0/` puts a
    round's record somewhere nothing looks for it.
    """
    return _round_path(
        "itererrors", corpus=corpus, detector=detector, supervision=supervision,
        porting=porting, iteration=iteration, artefact="error list", root=root)


def write_errors(
    errors: Sequence, run: Mapping[str, str], iteration: int,
    root: Path | None = None,
) -> Path:
    """The round's per-span error list: offsets, types and verdicts. One JSON object each.

    **Deny-listed, and that is not in tension with carrying no surface forms** — both hold,
    for different reasons. `ErrorSpan` has no text field by construction (`src/sample.py`),
    which is what makes the file safe to *exist* on a DUA corpus. The deny rule is about
    what it is even so: a list of the offsets of every missed identifier in the fold, drawn
    from gold, is a map of residual identifiers, and offsets plus a corpus resolve to the
    text. `config/naming.yaml` records the classification and `tools/release_screen.py`
    enforces it; this function just writes what it was given.

    Fields are enumerated rather than dumped from `asdict()`, for `write_spans`'s reason: a
    whitelist stays correct when `ErrorSpan` gains a field, and a dump would publish the
    new field the day it is added — and on this file that field would be published into the
    window that §1.4 builds.

    Sorted by `ErrorSpan.key`, which `error_spans()` has already done; done again here
    because this writer's output is the file, and a caller that assembled a list itself
    would otherwise decide the file's byte content by its iteration order.

    `run` is validated before the path is built (`check_run`), as `write_spans` does through
    `spans_path`. The axes then go to `errors_path` individually, which is the signature the
    loop driver needs — see that function.
    """
    check_run(run)
    path = errors_path(
        corpus=run["corpus"], detector=run["detector"],
        supervision=run["supervision"], porting=run["porting"],
        iteration=iteration, root=root,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {name: getattr(e, name) for name in ERROR_FIELDS}
        for e in sorted(errors, key=lambda e: e.key)
    ]
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n")
    return path


def read_errors(
    *, corpus: str, detector: str, supervision: str, porting: str, iteration: int,
    root: Path | None = None,
) -> list:
    """One round's `errors.jsonl` back as `ErrorSpan`s — the loop driver's next-round pool.

    The other half of the handover `errors_path`'s signature exists for: `run_fold` writes the
    file from a run block it assembled, and the driver reads it holding the four axes and no
    run block. Here rather than in `src/llm/prompt.py` because that module may not import
    `json` at all (`tests/test_prompt.py` asserts the closed import set), and beside
    `write_errors` because a reader that lives away from its writer is a second declaration of
    the schema.

    **The fields are a closed set and an extra one is refused, not ignored** (DESIGN §5.5.1).
    The writer whitelists six; a reader that skipped unknown keys would make an added
    `context` field harmless on the way in and published on the way out — the file is
    deny-listed, and the row it wrote would already be in the window §1.4 builds. §5.5.1's
    sentence is that such a field is the signal to refuse the field, so this is where a file
    carrying one stops.

    **The rows go through `ErrorSpan.__post_init__`, which is the point of returning the type
    rather than the dicts.** The type is what checks `phi_type` against `naming.yaml` and
    `kind` against the two kinds, and the driver's next act is to draw a stratified sample by
    exactly those values. A dict from a hand-edited file would stratify on a type that is not
    a type, and the sample would report it as one.

    Order is the file's, which `write_errors` sorted by `ErrorSpan.key`. Not re-sorted here:
    the file's byte content is the record of what the round was shown, and a reader that
    re-sorted would hide a writer that had stopped.
    """
    path = errors_path(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        iteration=iteration, root=root,
    )
    if not path.exists():
        raise FoldRunError(
            f"no error list at {rules_relative(path)}. Round {iteration}'s pool is drawn from it, "
            "so an absent file is a round that cannot be assembled — and the round before it "
            "either did not run or did not finish writing."
        )

    out = []
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise FoldRunError(
                    f"{rules_relative(path)} line {number} is not JSON ({exc.__class__.__name__}). "
                    "One object per line is the file's whole shape, and a line that does not "
                    "parse is a truncated write — the round's list is incomplete either way. "
                    "No content is quoted here (CLAUDE.md)."
                ) from exc
            if not isinstance(row, dict):
                raise FoldRunError(
                    f"{rules_relative(path)} line {number} holds a {type(row).__name__} and every "
                    "line is one object of six fields (DESIGN §5.5.1)."
                )
            keys = set(row)
            missing = [name for name in ERROR_FIELDS if name not in keys]
            extra = sorted(keys - set(ERROR_FIELDS))
            if missing or extra:
                raise FoldRunError(
                    f"{rules_relative(path)} line {number}: missing {missing}, unexpected "
                    f"{extra}. The six fields are the schema `write_errors` whitelists and "
                    "this refuses an extra one rather than skipping it — a `text`, `surface`, "
                    "`context` or `snippet` field here is the signal to refuse the field, not "
                    "to tolerate it (DESIGN §5.5.1). The field names are reported; no value "
                    "is (CLAUDE.md)."
                )
            try:
                out.append(ErrorSpan(**{name: row[name] for name in ERROR_FIELDS}))
            except (SamplingError, TypeError) as exc:
                raise FoldRunError(
                    f"{rules_relative(path)} line {number} is not a valid error reference: {exc}. "
                    "The row is validated on the way in because the driver's next act is to "
                    "stratify by `phi_type` and `kind`, and an undeclared value would form a "
                    "stratum and be reported as a type."
                ) from exc
    return out


def write_spans(
    predictions: Mapping[str, Sequence], run: Mapping[str, str],
    root: Path | None = None, *, iteration: int | None = None,
) -> Path:
    """One JSON object per predicted span, with full provenance and no text.

    **The surface is dropped here and the drop is the point.** `corpora.base.Span`
    carries one so offsets can be re-asserted against the corpus; this file is
    publishable (`tools/release_screen.py` allows `results/**/spans.jsonl`), and
    CLAUDE.md permits offsets, types and verdicts with the text left out. The fields
    are enumerated explicitly rather than serialised from `__dict__` or `asdict()`:
    a whitelist stays correct when `Span` gains a field, and a dump of everything
    would publish the new field the day it is added.

    Provenance is DESIGN §3's four values in full — `layer`, `detector`, `rule_id`,
    `score`. `agent_actions` is written too, empty for a rule arm, because an arm where
    an agent did intervene records it on the span and a reader must not have to know
    which arms have the key.

    The keys are spelled out here and are pinned against `SPAN_FIELDS`, which `read_spans`
    refuses a row for departing from. Written rather than generated from the tuple: this is
    the place a field's *value* is decided, and a loop over field names would need a
    per-field lookup anyway. The agreement is a test, not a shared expression, for
    `ERROR_FIELDS`' reason one file over — the two directions of the schema fail
    independently and the test is what catches either.

    Ordering is (doc_id, start, end, rule_id) so two runs of the same rules over the
    same fold produce byte-identical files. `RuleSet.detect` iterates rules in file
    order, which is stable, but "stable because of an implementation detail upstream"
    is not the same claim as "sorted here".

    `iteration` chooses the path (`paths.iterspans`) and changes nothing about the rows —
    §5.5's duplication rule needs the final round's two files to be identical, and a row
    that named its own round could not be.
    """
    path = spans_path(run, root=root, iteration=iteration)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for doc_id in sorted(predictions):
        for span in predictions[doc_id]:
            rows.append({
                "doc_id": doc_id,
                "start": span.start,
                "end": span.end,
                "phi_type": span.phi_type,
                # provenance, DESIGN §3
                "layer": span.layer,
                "detector": span.detector,
                "rule_id": span.rule_id,
                "score": span.score,
                "agent_actions": list(span.agent_actions),
            })
    rows.sort(key=lambda r: (r["doc_id"], r["start"], r["end"], r["rule_id"] or ""))
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n")
    return path


@dataclass(frozen=True, slots=True)
class PredictedSpan:
    """One row of `spans.jsonl`, read back. A prediction by reference, with no text.

    **Not a `corpora.base.Span`, and the difference is that this type cannot be given one.**
    `Span` requires `surface` and `subtype`, and this file dropped both — the surface because
    the file is publishable and CLAUDE.md permits offsets, types and verdicts without text,
    the subtype because it is the corpus's own vocabulary and no prediction has one. A reader
    that returned `Span`s would have to invent two values, and the first invention is the
    dangerous one: `surface` exists so offsets can be re-asserted against the corpus, so a
    fabricated one is a span that *claims* to have been checked against text it never saw.

    The nine fields are the file's, in its order. `layer`, `detector`, `rule_id` and `score`
    are DESIGN §3's provenance in full and they are carried rather than dropped, because the
    masker's caller may want to know which detector's prediction it masked and a reader that
    kept only the geometry would make that unanswerable from the round's own record.

    No `text`, no `surface`, and §5.5.1's rule applies here for `ErrorSpan`'s reason: a field
    that could hold a surface form gets filled with one, and this object travels into the
    masker, which is the largest corpus exposure in the project.
    """

    doc_id: str
    start: int
    end: int
    phi_type: str
    layer: str
    detector: str
    rule_id: str | None
    score: float | None
    agent_actions: tuple[dict, ...] = ()

    @property
    def key(self) -> tuple:
        """`write_spans`' sort order, so a re-sorted list is byte-reproducible."""
        return (self.doc_id, self.start, self.end, self.rule_id or "")


def read_spans(
    *, corpus: str, detector: str, supervision: str, porting: str, iteration: int,
    root: Path | None = None,
) -> list[PredictedSpan]:
    """One round's `spans.jsonl` back as `PredictedSpan`s — what the masker masks.

    `read_errors`' other half, and the same handover: `run_fold` writes the file from a run
    block it assembled, and the loop driver reads it holding the four axes and a round. Here
    rather than in the driver or in `src/llm/prompt.py` for that function's two reasons — the
    prompt module may not import `json` (`tests/test_prompt.py` asserts the closed import
    set), and a reader that lives away from its writer is a second declaration of the schema.

    **Read from the file rather than passed in memory, which is the decision this function
    is.** Round *n*'s Auditor masks round *n−1*'s predictions, and threading them through the
    driver as objects would work and would leave "what was masked at iteration 4" answerable
    only while the process lived. That is the property DESIGN §5.5 wanted from per-iteration
    rule files and §5.5.1 from the error list, arriving a third time: the round's record is on
    disk and checkable, and the masker's input is a file somebody can diff against the flags
    that came back. It also closes a failure the in-memory version cannot even express — the
    masker reading `document.spans`, i.e. gold — because what this returns has no gold in it
    to begin with.

    **`iteration` is required, and the un-iterated `spans.jsonl` is deliberately unreachable
    from here.** That copy is whichever round ran last (§5.5's duplication rule), so a reader
    pointed at it answers "which round did I just read" with "the most recent one", which is
    the in-memory defect at the file layer. Naming a round is the whole point of the argument.

    **The fields are a closed set and an extra one is refused, not ignored** — `read_errors`'
    rule, and the file this one reads is allowed rather than denied, so an added field here
    would be published on the next screener run rather than merely written.

    **A null `layer` or `detector` is refused, and that check is the one worth stating.**
    Gold spans carry neither (`corpora.base.Span`: provenance is empty on gold) and every
    prediction carries both, filled by the detector that emitted it (DESIGN §3, CLAUDE.md). So
    a row without them is gold that reached a prediction file — and the consumer of this
    function hands its result to the masker, where masking gold would give the Auditor the
    answer it exists not to have. `rule_id` and `score` are nullable: a tagger span has no
    rule id and the rule arm's scores are null in the file today.
    """
    path = iter_spans_path(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        iteration=iteration, root=root,
    )
    if not path.exists():
        raise FoldRunError(
            f"no prediction list at {rules_relative(path)}. Round {iteration}'s output is "
            "what the next round's Auditor is shown masked, so an absent file is a round "
            "that cannot be audited — and the round that should have written it either did "
            "not run or did not finish writing."
        )

    out = []
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise FoldRunError(
                    f"{rules_relative(path)} line {number} is not JSON "
                    f"({exc.__class__.__name__}). One object per line is the file's whole "
                    "shape, and a line that does not parse is a truncated write — the "
                    "round's predictions are incomplete either way. No content is quoted "
                    "here (CLAUDE.md)."
                ) from exc
            if not isinstance(row, dict):
                raise FoldRunError(
                    f"{rules_relative(path)} line {number} holds a {type(row).__name__} and "
                    f"every line is one object of {len(SPAN_FIELDS)} fields (DESIGN §3)."
                )
            keys = set(row)
            missing = [name for name in SPAN_FIELDS if name not in keys]
            extra = sorted(keys - set(SPAN_FIELDS))
            if missing or extra:
                raise FoldRunError(
                    f"{rules_relative(path)} line {number}: missing {missing}, unexpected "
                    f"{extra}. The fields are the schema `write_spans` whitelists, and an "
                    "extra one is refused rather than skipped: this path is on the "
                    "screener's ALLOW list, so a `text`, `surface`, `context` or `snippet` "
                    "field here would be published rather than merely written (DESIGN "
                    "§5.5.1). The field names are reported; no value is (CLAUDE.md)."
                )
            out.append(_one_prediction(row, path=path, number=number))
    return out


def _one_prediction(row: Mapping, *, path: Path, number: int) -> PredictedSpan:
    """One validated row. Every message names a line, a field or an offset — never a value.

    Checked here rather than in `PredictedSpan.__post_init__` so the message can say which
    line of which file, which is what a person needs to fix it; the type stays a record of
    the row rather than a validator that has to be constructed to be consulted. That is the
    opposite division from `ErrorSpan`, deliberately — that type is built by the scorer from
    objects it already holds, and this one is built from a file's bytes.
    """
    def refuse(detail: str) -> FoldRunError:
        return FoldRunError(f"{rules_relative(path)} line {number}: {detail}")

    doc_id = row["doc_id"]
    if not isinstance(doc_id, str) or not doc_id:
        raise refuse(
            "`doc_id` is not a non-empty string. It is the document a prediction is an "
            "offset into, and a row without one cannot be matched to any text."
        )
    start, end = row["start"], row["end"]
    if any(not isinstance(v, int) or isinstance(v, bool) for v in (start, end)):
        raise refuse("`start` and `end` must be integers — character offsets (DESIGN §9.7).")
    if start < 0 or end <= start:
        raise refuse(
            f"the span [{start}, {end}) is empty, inverted or negative. A prediction stands "
            "for at least one document character."
        )
    for name, ax in (("phi_type", "phi_type"), ("layer", "layer"),
                     ("detector", "detector")):
        value = row[name]
        if not isinstance(value, str) or value not in axis(ax):
            raise refuse(
                f"`{name}` is not a {ax} in config/naming.yaml (have: "
                f"{sorted(axis(ax))}). Null is refused too: gold spans carry no provenance "
                "and every prediction carries it, filled by the detector that emitted the "
                "span (DESIGN §3), so a row without it is gold in a prediction file — and "
                "these rows are handed to the masker, which must never mask gold."
            )
    rule_id = row["rule_id"]
    if rule_id is not None and (not isinstance(rule_id, str) or not rule_id):
        raise refuse(
            "`rule_id` is a non-empty string or null. Null is the tagger's case — a learned "
            "span has no rule to attribute — and an empty string is a rule whose id was lost."
        )
    score = row["score"]
    if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float))):
        raise refuse("`score` is a number or null (DESIGN §3's fourth provenance value).")
    actions = row["agent_actions"]
    if not isinstance(actions, list) or any(not isinstance(a, dict) for a in actions):
        raise refuse(
            "`agent_actions` is a list of objects, empty where no agent intervened. Empty "
            "and absent are different states and the writer never omits it (CLAUDE.md: "
            "agents hold no layer, and their interventions are recorded here)."
        )
    return PredictedSpan(
        doc_id=doc_id, start=start, end=end, phi_type=row["phi_type"],
        layer=row["layer"], detector=row["detector"], rule_id=rule_id,
        score=None if score is None else float(score),
        agent_actions=tuple(dict(a) for a in actions),
    )


# ─── the run ────────────────────────────────────────────────────────────────


def run_fold(
    *,
    corpus: str,
    detector: str,
    supervision: str,
    porting: str,
    split: str = DEFAULT_SPLIT,
    rules: dict[str, Path] | None = None,
    root: Path | None = None,
    model_record: Mapping[str, str | None] | None = None,
    model_lifecycle: Mapping[str, str | None] | None = None,
    cost: Mapping[str, float] | None = None,
    cost_to_date: Mapping[str, float] | None = None,
    termination: Termination | PendingTermination | None = None,
    iteration: int | None = None,
) -> tuple[Path, Path, dict]:
    """Detect over the fold, score it, write both files. Returns (spans, metrics, scored).

    *Which* languages are loaded comes from `corpus_rule_langs` — all of them, unioned,
    with no per-document selection (DESIGN §5.2). *Where* each one's file is comes from
    `rules`, and **this function is told rather than inferring it** (DESIGN §5.3). An arm
    passes `src.rules.arm_rules_path()` per language; a language absent from `rules` falls
    back to `paths.rules`, the committed format example and bootstrap state.

    Deriving the path from the axis arguments instead was the obvious alternative and is
    refused. It would give this module one input location — the arm being closed — and no
    way to be pointed at anything else, so a trial file and the bootstrap file would each
    need a special case, and it would make the input a function of the run block, which is
    the coupling that lets an arm read its own results directory by accident.

    The run block is assembled here and validated by the scorer before anything is
    written, so an arm named wrong fails before it produces a directory. `rules_version`
    carries `RuleSet.versions` — the per-file integer each rule file declares — because
    CLAUDE.md requires the rule version to travel with the result and a metrics file
    naming no rule version cannot be re-run. `rules_source` carries the paths, which is
    what a version integer cannot: DESIGN §5.3's objection to a shared rule path is that
    an overwrite leaves a plausible metrics file behind, and only the path says which
    arm's file and which iteration the numbers were computed from.

    **`model_record` and `cost` are how an arm that called a model reports it**, and they
    are arguments rather than something this module could work out. It runs rules over a
    fold; whether those rules were written by a person, by one LLM call or by twelve is
    invisible from here, and inventing an answer is what `model_id_absent()` exists to
    avoid. Omitted, they give the `R` arm's record: `none` for the model and zeros for the
    three LLM counts, both explicit and neither a default standing in for a measurement.
    `model_record` may carry only `MODEL_FIELDS`, so this stays a report of a call and does
    not become a second way to write the run block.

    **`model_lifecycle` is passed through and never merged into the run block.** It is
    `bedrock.model_lifecycle()`'s record, and it is a separate argument from `model_record`
    for the reason `MODEL_FIELDS` is a closed set: the two say different kinds of thing.
    `model_record` is what the response confirmed about the id that answered, and it is a
    premise of the numbers. The lifecycle record is when the id *appeared* in the catalogue,
    which **does not resolve the alias** — see `scorer.write_metrics` and
    `docs/notes/baseline-model-family.md` §"측정 결과" 4. Merging them would put a timestamp
    that identifies nothing next to `model_id_resolution`, where it would read as evidence
    for it. Nothing here derives one from the other in either direction.

    **`wall_seconds` is the arm's total and the two parts are summed.** The caller's figure
    is the call's, this function measures the detection pass, and both are compute time for
    the same arm — which is precisely what makes them addable, and the property
    `human_minutes` deliberately lacks (DESIGN §11.2: a person's attention and a pipeline's
    wall clock are different quantities, and the distinct name is what stops an aggregation
    summing them). Reporting only the call would put a rule pass's cost at zero seconds in
    an arm whose comparison is against `port-loop`'s many calls.

    **`cost` is this round's and `cost_to_date` is the arm's total through it** (schema 7,
    2026-08-13). Until `port-loop` those were one number, because an arm was one round.
    An iteration of the loop makes 1 + N calls — RuleAuthor once, the Auditor once per dev
    document — so the driver adds them with `scorer.sum_costs` and passes both: the round's
    figure here as `cost`, and its own accumulator as `cost_to_date`. DESIGN §11.3's 1.9×
    comparison is against the total, and which iteration got expensive is only in the rounds.

    Omitted, `cost_to_date` becomes `cost`, which is the non-iterating arms' true state
    rather than a fallback — one round is the whole arm. **The detection pass's seconds are
    added to both**, and that is deliberate: this round's detection is part of this round and
    part of the arm, and a total that omitted it would be a total missing the compute the
    round's own block reports.

    What this function does **not** do is accumulate. It is given one round's numbers and one
    total and writes them; a running sum kept here would be a second accumulator beside the
    driver's, and this function is called once per round by a caller that already has the
    history. The relation between the two blocks is checked by the writer
    (`scorer.check_cost_to_date`).

    **`termination` is how an iterating arm reports where it stopped**, and it is an argument
    for `cost`'s and `model_record`'s reason: this function scores one fold, and whether that
    fold's rules came from iteration 1 of 8 or from a converged loop is invisible from here.
    Omitted, it gives the record of an arm the stopping rule does not apply to —
    `termination.not_applicable(corpus)`, which is `R`'s and the `port-oneshot` rungs' true
    state and not a placeholder for a measurement.

    **`port-loop` passes a `PendingTermination` instead, and this function completes it with
    the one number the caller cannot have** (2026-08-14). A round's verdict is about that
    round, so it needs that round's dev leak rate — which is `scored["headline"]["leak_rate"]`,
    produced by the `score()` call below, after the caller has already handed off. So the
    driver passes the corpus and every *earlier* round's rate, and this function calls
    `resolve(leak_rate)` on the block it is about to write. Structurally identical to the cost
    block one argument over: the caller assembles what it knows and this function supplies the
    quantity only it measured (there, `elapsed`; here, the rate). It is **not** this function
    deciding anything — `should_stop` is never imported here, the history belongs to the
    driver, and the rate is passed rather than the verdict, so §3's rule keeps its one
    implementation and this module still cannot tell a converged loop from round 1 of 8. An
    already-resolved `Termination` is still accepted, which is what `not_applicable` is and
    what a caller that has a verdict for its own reasons passes.

    The mode is the headline's, `fully_covered` (CLAUDE.md, DESIGN §9.3), read from `scored`
    by key rather than chosen here: §3's rule is a threshold on the headline leak rate, and a
    stopping rule fed the relaxed lower bound would stop on differences of a different
    quantity while every field in the record still looked right.

    The default is built here rather than left to the scorer because the scorer *requires* the
    block: an arm that does not iterate still records that fact, for the reason the cost block
    writes zeros. That default reads `splits/{corpus}.json` for `n_dev`, so δ appears in a
    non-iterating arm's file too — deliberately, so a reader comparing `port-oneshot`'s single
    leak rate against `port-loop`'s stopping point finds the threshold in both files.

    **`iteration` says which round this is, and it scopes all three of the round's files
    together** (DESIGN §5.5). Given a round number, this writes `iter{N}/spans.jsonl`,
    `iter{N}/metrics.json` and `iter{N}/errors.jsonl`; omitted, it writes the un-iterated
    `spans.jsonl` and `metrics.json` and no error list. One argument and not three, because
    the three files are one record: a round whose score is scoped and whose predictions are
    not keeps every number and overwrites the spans the errors were derived from, so
    `iter3/errors.jsonl` becomes a list nothing can re-derive. A separate flag per file is a
    way to reach that state, and the reason this argument replaced
    `export_errors_for_iteration` — which could scope the error list *alone* — is that the
    old signature made the hole expressible.

    **Every round writes the un-iterated pair too, so the final round's duplicate needs no
    decision from anybody.** §5.5 requires the last round's score and spans to exist at
    `paths.metrics`/`paths.spans` as well, and the obvious implementation — a `final=True`
    argument — cannot be given a correct value by the caller: whether round *n* is the last
    is `should_stop(corpus, leak_rates)`'s verdict, and that needs round *n*'s leak rate,
    which does not exist until this function has scored and written. So the flag would be
    passed either a guess or a re-derivation, and a wrong guess leaves the arm's headline at
    round *n − 1* with nothing anywhere saying so.

    Instead the un-iterated pair is rewritten each round from that round's `predictions` and
    `scored`, so it holds the latest round throughout and the final round when the loop
    stops. That is exactly what §5.5 asks for, and it is reached without anyone knowing the
    future.

    One correction to the paragraph above, since `PendingTermination` arrived after it: with a
    pending block resolved here, "is this the last round" *is* computable inside this function
    — it is `resolved.stop`. So the flag is no longer impossible; it is unnecessary, which is a
    weaker claim and the honest one. It stays refused because rewriting the pair every round
    reaches the required state with no branch at all, and a `final` branch would make the
    un-iterated pair's content depend on the stopping rule: a δ edit would then change which
    round the arm's headline came from, and the two files would disagree about the arm while
    each stayed internally consistent. Fewer states beats a correctly-computed flag.

    **Both copies come from the one `score()` call in this function**, which is the
    property that matters: two calls could differ — a rule file edited between them, or a
    non-deterministic detector added later — and *neither file would look wrong*, because
    each would be internally consistent with the pass that produced it. So the agreement is
    a property of this code path rather than of a convention a caller follows.

    Mid-run, `paths.metrics` therefore holds an unfinished arm's most recent round. That is
    legible rather than misleading: the `termination` block in it says `reason: null`, which
    is the record of an arm that has not stopped, and a run in progress has no final score to
    hold instead.

    It is a **number and not a flag**, for the reason `arm_rules_path` takes one: the round is
    a path component, and a boolean here would need the iteration from somewhere else — the
    `termination` block's `iterations` is the nearest candidate and it is the wrong one, since
    it counts the rounds *so far* and these files belong to the round being scored. Two
    quantities that coincide until an arm resumes.

    **A non-iterating arm passes nothing and writes only the un-iterated pair.** Not
    `iteration=1`, and the choice is recorded here because both readings are defensible.
    Writing `iter1/` for every arm would make the tree uniform, and it is refused for three
    reasons that point the same way. First, `port-oneshot-nofence`'s `metrics.json` and
    `spans.jsonl` are committed at four axes and would gain an `iter1/` duplicate beside
    them on the next run — a second copy of a published result, created by a feature that
    arm does not have. Second, `iter1/` under an arm with no rounds is a false statement
    about the arm: the directory exists to answer "what did round *n* look like", and an arm
    with one pass has no round 1 to distinguish from a round 2. Third, `iter1/errors.jsonl`
    would then be written by every arm on every corpus — a map of the residual identifiers
    in the fold as a by-product of a feature only the iterating arms use, which is exactly
    what the previous signature's opt-in existed to prevent.

    This does not conflict with the duplication rule, because that rule is stated in one
    direction: the final round's score is *also* at `paths.metrics`, so every arm's headline
    is at the same path. A non-iterating arm already satisfies it — its one pass writes
    there. The rule asks that `paths.metrics` hold every arm's final score, not that
    `iter{N}/` hold every arm's only score.
    """
    docs = load_fold(corpus, split)
    ruleset = load_for_corpus(corpus, paths=rules)

    started = time.monotonic()
    predictions = detect_fold(docs, ruleset, detector=detector)
    elapsed = time.monotonic() - started

    pairs, excluded = from_documents(docs, predictions)
    scored = score(pairs, excluded_gold=excluded)

    if model_record is not None:
        unknown = [k for k in model_record if k not in MODEL_FIELDS]
        if unknown:
            raise FoldRunError(
                f"model_record carries {sorted(unknown)}, which is not one of "
                f"{list(MODEL_FIELDS)}. This argument reports what a caller observed about "
                "the model it called; a run block assembled from two places is a run block "
                "no single reader can check (DESIGN §10 A2)."
            )
    if cost is not None:
        missing = [k for k in REQUIRED_COST if k not in cost]
        if missing:
            raise FoldRunError(
                f"the cost block passed in is missing {missing}. CLAUDE.md requires cost "
                "beside quality and the scorer refuses a partial block: an arm reporting "
                "tokens without calls, or calls without time, is an arm whose cost cannot "
                "be compared to another's."
            )
    if cost_to_date is not None:
        missing = [k for k in REQUIRED_COST if k not in cost_to_date]
        if missing:
            raise FoldRunError(
                f"the cost_to_date block passed in is missing {missing}. It is the arm's "
                "running total through this round and carries `cost`'s four keys — DESIGN "
                "§11.3's comparison is read off it. An arm with one round omits the argument "
                "rather than passing a partial block; that writes the round's own cost, which "
                "is its total."
            )
        if cost is None:
            raise FoldRunError(
                "cost_to_date was passed without cost. A total with no round beside it is "
                "the arm's figure with nothing saying which iteration produced these numbers, "
                "and this round's block is what makes the total's growth readable (DESIGN "
                "§5.5, §11.3). An arm that made no calls passes neither and gets zeros for "
                "both."
            )

    if iteration is not None:
        # All three round paths, validated before the first write rather than at the third.
        # Each builder refuses a round that is not a round, and a run that had written
        # `iter{N}/spans.jsonl` and then raised on the error list would leave the round's
        # directory holding predictions with no score beside them — the partially written
        # results directory `FoldRunError` exists to avoid. Every path here is a pure
        # function of its arguments, so building them now and again at the write cannot give
        # two answers. All three and not just one: they share the round component, so any
        # of them can be the one that refuses, and a check on a single path would leave the
        # other two validated after the first write for no reason.
        axes = dict(corpus=corpus, detector=detector, supervision=supervision,
                    porting=porting, iteration=iteration, root=root)
        iter_spans_path(**axes)
        errors_path(**axes)
        iter_metrics_path(**axes)

    commit, tree = sealed_log.tree_state()
    run = {
        "corpus": corpus,
        "detector": detector,
        "supervision": supervision,
        "porting": porting,
        "split": split,
        # DESIGN §4: required, and `none` rather than absent for an arm that calls no
        # model. Read from naming.yaml, never spelled here. A caller that *did* call a
        # model passes its `Response.model_record()` and it replaces this — the absent
        # value is the record of an arm with no model, not a placeholder for one.
        "model_id": model_id_absent(),
        **dict(model_record or {}),
        "rules_version": {lang: v for lang, v in sorted(ruleset.versions.items())},
        # Where each file was read from (DESIGN §5.3). Beside the version rather than
        # instead of it: the version says which revision the author declared and the
        # path says which arm and iteration produced it, and neither implies the other.
        "rules_source": {lang: p for lang, p in sorted(ruleset.sources.items())},
        "rules": sorted(r.rule_id for r in ruleset.rules),
        # DESIGN §10 A2: the instant, the revision, and whether the revision describes
        # what ran. Required by `scorer.REQUIRED_RUN` since schema 4 — `commit` and `tree`
        # were already written here, and what changed is that omitting them is now refused
        # instead of unnoticed. This arm calls no model, so the mitigation they provide is
        # not needed for its own numbers; it is written anyway because a field that only
        # some arms carry cannot be compared across arms. `commit` is passed through as
        # `tree_state()` returned it, including `None` — the scorer accepts a null hash with
        # `tree` of `unknown` and refuses it with any other state, so there is nothing to
        # substitute here and substituting would be the bug (scorer.NULLABLE_RUN).
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": commit,
        "tree": tree,
    }
    # The detection pass's own time, added to whatever the caller spent calling a model.
    # Both are this arm's compute; see the docstring on why they are summable and
    # `human_minutes` is not.
    seconds = round(elapsed + float((cost or {}).get("wall_seconds", 0.0)), 3)
    # And into the arm's total as well. This round's detection is part of this round and part
    # of the arm; a total carrying the calls' seconds but not the fold's would be smaller than
    # the sum of the rounds it contains (see the docstring).
    to_date_seconds = round(elapsed + float((cost_to_date or cost or {})
                                            .get("wall_seconds", 0.0)), 3)
    # A pending block is completed with the round's own leak rate, which is the number the
    # caller could not have had: it comes from the `score()` call above. The mode is read by
    # key from `HEADLINE_MODE`'s field rather than named here, so the rule's input is the same
    # quantity CLAUDE.md calls the headline (DESIGN §9.3) and a mode rename moves both together.
    # `resolve()` calls `should_stop` and this module does not — the rate travels, not the
    # verdict (see the docstring).
    if isinstance(termination, PendingTermination):
        termination = termination.resolve(scored["headline"]["leak_rate"]["value"])
    # Assembled once and passed to both writes of each file, so the round-scoped copy and
    # the un-iterated one cannot be built from two different cost or termination blocks
    # (DESIGN §5.5). `write_metrics` copies what it is given; nothing below re-derives.
    metrics_args = dict(
        run=run,
        cost={**NO_LLM_COST, **dict(cost or {}), "wall_seconds": seconds},
        # The arm's running total through this round (schema 7). `cost_to_date or cost` and
        # not a second default: an arm with one round has one round's cost as its total, and
        # writing zeros here for a caller that passed a cost would publish a total below the
        # part it contains — which `scorer.check_cost_to_date` refuses.
        cost_to_date={**NO_LLM_COST, **dict(cost_to_date or cost or {}),
                      "wall_seconds": to_date_seconds},
        # An arm that does not iterate records that it does not, rather than omitting the
        # block (DESIGN §3, and the cost block's zeros one argument over).
        termination=(termination or not_applicable(corpus)).record(),
        model_lifecycle=model_lifecycle,
        root=root,
    )

    if iteration is not None:
        # The round's three files: predictions, score, errors. Written together because
        # they are one record (DESIGN §5.5) — the error list is derived from this round's
        # predictions against gold, so a round whose spans a later round overwrote holds an
        # error list nothing can re-derive or check.
        #
        # From `pairs` — the same objects `score()` read, so the exported list and the
        # published counts come from one scoring of one set of spans (DESIGN §9.3). The
        # matchings are inside `error_spans`; nothing here recomputes one.
        write_spans(predictions, run, root=root, iteration=iteration)
        write_errors(error_spans(pairs), run, iteration, root=root)
        write_metrics(scored, iteration=iteration, **metrics_args)

    # And the un-iterated pair, from the *same* `predictions` and the *same* `scored` as
    # the block above — §5.5's duplication rule, and the reason the two copies cannot
    # disagree is that this function scores once. Written every round rather than only on
    # the last, because which round is last is `should_stop()`'s verdict on a leak rate
    # this pass has not yet produced; see the docstring.
    spans_file = write_spans(predictions, run, root=root)
    metrics_file = write_metrics(scored, **metrics_args)
    return spans_file, metrics_file, scored


# ─── cli ────────────────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--corpus", required=True, help="corpus id from naming.yaml")
    parser.add_argument("--detector", required=True,
                        help="detector axis value; R for rules only")
    parser.add_argument("--supervision", required=True,
                        help="supervision axis value")
    parser.add_argument("--porting", required=True, help="porting axis value")
    parser.add_argument(
        "--split", default=DEFAULT_SPLIT,
        help=f"fold to run on (default {DEFAULT_SPLIT}). `test` is refused: sealed "
             "evaluation runs through src.eval.run_sealed_eval, which logs the access.",
    )
    parser.add_argument(
        "--iteration", type=int, default=None,
        help="this is round N of the arm. Reads round N's rule files from under the arm "
             "(paths.armrules, DESIGN §5.3): results/.../{porting}/rules/iter{N}/"
             "{lang}.yaml, one per language the corpus loads — and writes round N's "
             "record: iter{N}/spans.jsonl, iter{N}/metrics.json, iter{N}/errors.jsonl, "
             "plus the un-iterated pair (DESIGN §5.5). Without it the bootstrap "
             "rules/{lang}.yaml is read, which is the format example and the state a "
             "first iteration starts from, and only the un-iterated pair is written.",
    )
    parser.add_argument(
        "--rules", type=Path, default=None,
        help="one explicit rule file, for a trial run — needs --lang when the corpus "
             "loads more than one file. Mutually exclusive with --iteration.",
    )
    parser.add_argument("--lang", default=None,
                        help="the language --rules declares")
    args = parser.parse_args(argv)

    if args.rules and args.iteration is not None:
        # Refused rather than given a precedence order. Both name where the rules come
        # from, and a silent winner means a trial file scored under an arm's iteration
        # number, or the reverse — the run block would record one path and the reader
        # would have the other command in their shell history (DESIGN §5.3).
        print("--rules and --iteration both say where the rule files are: pass one. "
              "--iteration reads the arm's own files (paths.armrules); --rules reads a "
              "single explicit file for a trial.", file=sys.stderr)
        return 2

    override: dict[str, Path] | None = None
    if args.iteration is not None:
        from ..corpora.base import rule_langs
        from ..rules import arm_rules_path
        try:
            override = {
                lang: arm_rules_path(
                    corpus=args.corpus, detector=args.detector,
                    supervision=args.supervision, porting=args.porting,
                    iteration=args.iteration, lang=lang,
                )
                for lang in rule_langs(args.corpus)
            }
        except (CorpusError, RuleError) as exc:
            print(f"{exc}", file=sys.stderr)
            return 2
    elif args.rules:
        from ..corpora.base import rule_langs
        try:
            langs = rule_langs(args.corpus)
        except CorpusError as exc:
            print(f"{exc}", file=sys.stderr)
            return 2
        lang = args.lang or (langs[0] if len(langs) == 1 else None)
        if lang is None:
            print(f"--rules needs --lang: {args.corpus} loads {langs}, and the rule_id "
                  "prefix comes from the file's language rather than the corpus's "
                  "(DESIGN §5.2).", file=sys.stderr)
            return 2
        override = {lang: args.rules}

    try:
        spans_file, metrics_file, scored = run_fold(
            corpus=args.corpus, detector=args.detector,
            supervision=args.supervision, porting=args.porting,
            split=args.split, rules=override,
            # One flag for reading round N's rules and writing round N's record. Splitting
            # it into `--iteration` and a second `--write-iteration` would make the state
            # this module refuses expressible from the command line: round 4's rules scored
            # into round 3's directory, or into no round's at all — which is the arm-wide
            # overwrite §5.5 corrected. The round is one fact about the run.
            iteration=args.iteration,
        )
    except (FoldRunError, ScorerError, RuleError, CorpusError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    leak = scored["headline"]["leak_rate"]
    lower = scored["headline"]["leak_rate_lower_bound"]
    counts = scored["counts"]
    print(f"{args.corpus} {args.split}: {counts['documents']['total']} documents, "
          f"{counts['gold']['in_scope']} in-scope gold spans, "
          f"{counts['pred']} predictions")
    # Leak rate is the headline and F1 is not (CLAUDE.md). Printing F1 here beside it
    # would put the two on one line as though the choice were the reader's.
    print(f"leak rate {_pct(leak['value'])} ({leak['mode']}) — headline; "
          f"{_pct(lower['value'])} ({lower['mode']}) as the lower bound")
    print(f"spans   {spans_file.relative_to(ROOT)}")
    print(f"metrics {metrics_file.relative_to(ROOT)}")
    if args.iteration is not None:
        # The round's directory, named once rather than its three files listed. `errors.jsonl`
        # is deny-listed and this output goes to a terminal and into shell history, so the
        # directory is what a reader needs and the filenames are in `naming.yaml`.
        round_dir = iter_metrics_path(
            corpus=args.corpus, detector=args.detector, supervision=args.supervision,
            porting=args.porting, iteration=args.iteration, root=ROOT).parent
        print(f"round   {round_dir.relative_to(ROOT)}/")
    return 0


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


if __name__ == "__main__":
    raise SystemExit(main())
