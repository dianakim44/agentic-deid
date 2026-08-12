"""Run a rule set over one fold and score it — the arm's closing step.

    python3 -m src.eval.run_fold --corpus es-meddocan \
        --detector R --supervision sup-free --porting port-oneshot

Writes two files under `results/{corpus}/{detector}/{supervision}/{porting}/`:
`spans.jsonl` (the predictions, with DESIGN §3 provenance and no surface forms) and
`metrics.json` (the scorer's output). Until this existed no arm could be closed: rules
could be written and counted, but a dev-wide score — the number every comparison in
DESIGN §4 is made of — had nowhere to come from.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from ..corpora import load
from ..corpora.base import (
    ROOT, CorpusError, Document, axis, model_id_absent, path_template,
)
from ..rules import RuleError, RuleSet, load_for_corpus
from ..termination import Termination, not_applicable
from . import sealed_log
from .scorer import (
    PATH_AXES, REQUIRED_COST, ScorerError, check_run, from_documents, score,
    write_metrics,
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


def spans_path(run: Mapping[str, str], root: Path | None = None) -> Path:
    """`paths.spans` for this arm, from naming.yaml.

    Beside `metrics.json` by construction: both format the same `PATH_AXES` from the
    same validated run block, so a spans file cannot end up in a different arm's
    directory from the metrics computed on it.
    """
    check_run(run)
    template = path_template("spans")
    return (root or ROOT) / template.format(**{k: run[k] for k in PATH_AXES})


def write_spans(
    predictions: Mapping[str, Sequence], run: Mapping[str, str],
    root: Path | None = None,
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

    Ordering is (doc_id, start, end, rule_id) so two runs of the same rules over the
    same fold produce byte-identical files. `RuleSet.detect` iterates rules in file
    order, which is stable, but "stable because of an implementation detail upstream"
    is not the same claim as "sorted here".
    """
    path = spans_path(run, root=root)
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
    termination: Termination | None = None,
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

    **`termination` is how an iterating arm reports where it stopped**, and it is an argument
    for `cost`'s and `model_record`'s reason: this function scores one fold, and whether that
    fold's rules came from iteration 1 of 8 or from a converged loop is invisible from here.
    Omitted, it gives the record of an arm the stopping rule does not apply to —
    `termination.not_applicable(corpus)`, which is `R`'s and the `port-oneshot` rungs' true
    state and not a placeholder for a measurement. `port-loop` will pass
    `termination.should_stop(corpus, leak_rates)`.

    The default is built here rather than left to the scorer because the scorer *requires* the
    block: an arm that does not iterate still records that fact, for the reason the cost block
    writes zeros. That default reads `splits/{corpus}.json` for `n_dev`, so δ appears in a
    non-iterating arm's file too — deliberately, so a reader comparing `port-oneshot`'s single
    leak rate against `port-loop`'s stopping point finds the threshold in both files.
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
    spans_file = write_spans(predictions, run, root=root)
    # The detection pass's own time, added to whatever the caller spent calling a model.
    # Both are this arm's compute; see the docstring on why they are summable and
    # `human_minutes` is not.
    seconds = round(elapsed + float((cost or {}).get("wall_seconds", 0.0)), 3)
    metrics_file = write_metrics(
        scored, run=run,
        cost={**NO_LLM_COST, **dict(cost or {}), "wall_seconds": seconds},
        # An arm that does not iterate records that it does not, rather than omitting the
        # block (DESIGN §3, and the cost block's zeros one argument over).
        termination=(termination or not_applicable(corpus)).record(),
        model_lifecycle=model_lifecycle,
        root=root,
    )
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
        help="load this iteration's rule files from under the arm (paths.armrules, "
             "DESIGN §5.3): results/.../{porting}/rules/iter{N}/{lang}.yaml, one per "
             "language the corpus loads. This is what an arm's own run uses. Without it "
             "the bootstrap rules/{lang}.yaml is read, which is the format example and "
             "the state a first iteration starts from.",
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
    return 0


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


if __name__ == "__main__":
    raise SystemExit(main())
