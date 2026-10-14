"""Drive the v24 hybrid rung. DESIGN §12.

    python3 tools/run_hybrid.py predict   --seed 1
    python3 tools/run_hybrid.py score-dev --seed 1
    python3 tools/run_hybrid.py table     --seed 1
    python3 tools/run_hybrid.py arbiter   --seed 1 --draw 1
    python3 tools/run_hybrid.py consistency --seed 1

The driver `config/naming.yaml`'s `porting_rungs` names for the `hybrid` rung. It is not
`tools/run_loop.py`: nothing here calls a RuleAuthor, freezes a prompt window or iterates,
and the one agent it does call (the Arbiter) is not a porting role (DESIGN §12.3).

What each subcommand is for:

  - `predict` runs the trained tagger over the five dev folds and **caches** the spans.
    Cached because every later step reads them and inference is the expensive part;
    regenerable, so the cache is gitignored.
  - `score-dev` scores T and RT and writes their dev `metrics.json`.
  - `table` builds the per-type candidate table, runs RT-Sel, and writes both.
  - `arbiter` runs one draw of the Arbiter loop and writes its rounds.
  - `consistency` computes C's `run_repeat` layer by re-applying everything three times.

**No subcommand reads the sealed fold.** Opening it is `src/eval/run_sealed_eval.py`'s and
nothing here imports it.

Data handling: every path in this file writes offsets, types, counts and rates. No span
surface and no document text is written or printed, and the cached prediction files follow
`paths.spans`' schema for that reason rather than carrying a convenience field.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.corpora import base                                      # noqa: E402
from src.eval import guideline, scorer, sealed_log                # noqa: E402
from src.eval.run_fold import read_arm_spans                      # noqa: E402
from src.merge import arbiter as arb                              # noqa: E402
from src.merge import policy as pol                               # noqa: E402
from src.merge import selector as sel                             # noqa: E402
from src.termination import not_applicable                        # noqa: E402

OUT = ROOT / "experiments" / "hybrid_v24"
REGISTRATION = OUT / "registration.yaml"
SUPERVISION = "sup-human"
PORTING = "port-hybrid"


def registration() -> dict:
    return yaml.safe_load(REGISTRATION.read_text(encoding="utf-8"))


def corpora() -> list[str]:
    return list(registration()["corpora"])


def rules_arm(corpus: str) -> str:
    return registration()["rules_arm"][corpus]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_block(*, corpus: str, detector: str, model_id: str) -> dict:
    commit, tree = sealed_log.tree_state()
    return {
        "corpus": corpus, "detector": detector, "supervision": SUPERVISION,
        "porting": PORTING, "split": "dev", "model_id": model_id,
        "generated": now(), "commit": commit, "tree": tree,
    }


# ─── the tagger's dev predictions, cached ───────────────────────────────────

def pred_path(corpus: str, seed: int, split: str = "dev") -> Path:
    return OUT / "preds" / f"{corpus}.seed{seed}.{split}.jsonl"


def write_preds(path: Path, spans_by_doc: dict[str, list]) -> None:
    """One row per span, `paths.spans`' nine fields and no others.

    The same schema as a results `spans.jsonl` deliberately: a cache with a convenience
    field is how a surface gets written somewhere nobody screens.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for doc_id in sorted(spans_by_doc):
            for span in sorted(spans_by_doc[doc_id], key=lambda s: (s.start, s.end)):
                fh.write(json.dumps({
                    "doc_id": doc_id, "start": span.start, "end": span.end,
                    "phi_type": span.phi_type, "layer": span.layer,
                    "detector": span.detector, "rule_id": span.rule_id,
                    "score": span.score, "agent_actions": [],
                }, sort_keys=True) + "\n")


class CachedSpan:
    """A prediction read back from the cache. No surface, by construction."""

    __slots__ = ("start", "end", "phi_type", "layer", "detector", "rule_id", "score")

    def __init__(self, row: dict) -> None:
        self.start = row["start"]
        self.end = row["end"]
        self.phi_type = row["phi_type"]
        self.layer = row["layer"]
        self.detector = row["detector"]
        self.rule_id = row["rule_id"]
        self.score = row["score"]


def read_preds(path: Path) -> dict[str, list]:
    out: dict[str, list] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                out.setdefault(row["doc_id"], []).append(CachedSpan(row))
    return out


def cmd_predict(args) -> int:
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer
    from src.tagger.train import predict

    weights = OUT / "weights" / f"seed{args.seed}"
    if not weights.is_dir():
        raise SystemExit(
            f"no weights at {weights.relative_to(ROOT)}. Train the seed first: "
            f"python3 -m src.tagger.train --seed {args.seed}"
        )
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(weights)
    model = AutoModelForTokenClassification.from_pretrained(weights).to(device)

    for corpus in corpora():
        docs = [d for d in base.load(corpus) if d.split == args.split]
        started = time.time()
        preds = predict(model, tokenizer, docs, device, detector="T")
        path = pred_path(corpus, args.seed, args.split)
        write_preds(path, preds)
        total = sum(len(v) for v in preds.values())
        print(f"{corpus:12s} {args.split} docs {len(docs):5d} spans {total:6d} "
              f"({time.time() - started:.0f}s) -> {path.relative_to(ROOT)}",
              flush=True)
    return 0


# ─── scoring one policy map ─────────────────────────────────────────────────

def load_halves(corpus: str, seed: int, split: str = "dev"):
    """`(docs, rules_by_doc, tagger_by_doc)` for one corpus."""
    docs = [d for d in base.load(corpus) if d.split == split]
    rules_by_doc: dict[str, list] = {}
    for span in read_arm_spans(corpus=corpus, detector="R",
                               supervision="sup-free", porting=rules_arm(corpus)):
        rules_by_doc.setdefault(span.doc_id, []).append(span)
    path = pred_path(corpus, seed, split)
    if not path.exists():
        raise SystemExit(
            f"no cached tagger predictions at {path.relative_to(ROOT)}. Run "
            f"`predict --seed {seed} --split {split}` first."
        )
    return docs, rules_by_doc, read_preds(path)


def score_policy(docs, rules_by_doc, tagger_by_doc, policies) -> tuple[dict, dict]:
    """Score one policy map over one corpus. Returns `(scored, merged_by_doc)`."""
    merged = {}
    for doc in docs:
        merged[doc.doc_id] = pol.apply_map(
            list(rules_by_doc.get(doc.doc_id, ()))
            + list(tagger_by_doc.get(doc.doc_id, ())),
            policies)
    pairs, excluded = scorer.from_documents(docs, merged)
    scored = scorer.score(pairs, excluded_gold=excluded)
    scored["non_phi_masking"] = scorer.non_phi_masking(
        pairs, {d.doc_id: len(d.text) for d in docs})
    return scored, merged


def headline(scored: dict) -> dict:
    relaxed = scored["modes"][scorer.RELAXED]
    full = scored["modes"][scorer.FULLY_COVERED]
    out = {
        "L_full": scored["headline"]["leak_rate"]["value"],
        "L_rel": scored["headline"]["leak_rate_lower_bound"]["value"],
        **guideline.from_mode_block(relaxed),
        "complementarity_full": full["complementarity"]["families"],
        "D_full": full["by_document"]["rate"],
        "gold": scored["counts"]["gold"]["in_scope"],
        "pred": scored["counts"]["pred"],
    }
    masking = scored.get("non_phi_masking")
    if masking:
        out["nongold_masked_rate"] = masking.get("character_rate")
    return out


def write_arm(corpus: str, detector: str, scored: dict, *, model_id: str,
              cost: dict, extra: dict | None = None) -> Path:
    run = run_block(corpus=corpus, detector=detector, model_id=model_id)
    if extra:
        run.update(extra)
    return scorer.write_metrics(
        scored, run=run, cost=cost,
        termination=not_applicable(corpus).record(),
    )


def zero_cost(seconds: float) -> dict:
    """The no-LLM arms' cost. Zero is a measurement and absent is not."""
    return {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "wall_seconds": round(seconds, 1)}


def rules_source(corpus: str) -> dict[str, str]:
    """The rule files this arm's rules half came from, by language.

    Named in the run block because `run_sealed_eval.plan_arm` refuses an arm that does
    not name them, and because the claim "these are the R arm's own committed rules,
    unmodified" should be checkable from the record rather than taken from a commit
    message. The paths are the registered R arm's own, which is what `port-hybrid`'s
    gloss says the rules half is.
    """
    arm = rules_arm(corpus)
    final = _final_round(corpus, arm)
    out = {}
    for lang in base.rule_langs(corpus):
        path = base.round_path(
            "armrules", iteration=final, artefact="rules",
            error=SystemExit, corpus=corpus, detector="R",
            supervision="sup-free", porting=arm, lang=lang)
        out[lang] = str(path.relative_to(ROOT))
    return out


def _final_round(corpus: str, porting: str) -> int:
    record = json.loads((ROOT / scorer.path_template("metrics").format(
        corpus=corpus, detector="R", supervision="sup-free", porting=porting)
    ).read_text(encoding="utf-8"))
    return record["termination"]["iterations"]


# ─── score-dev: T and RT ────────────────────────────────────────────────────

def cmd_score_dev(args) -> int:
    absent = base.model_id_absent()
    summary = {}
    for corpus in corpora():
        docs, rules_by_doc, tagger_by_doc = load_halves(corpus, args.seed)
        row = {}
        for detector, policies in (
                ("T", {t: pol.Choice("T", 0.5) for t in base.canonical_types()}),
                ("RT", pol.baseline_map())):
            started = time.time()
            scored, _ = score_policy(docs, rules_by_doc, tagger_by_doc, policies)
            row[detector] = headline(scored)
            if args.write:
                extra = ({"rules_source": rules_source(corpus)}
                         if detector != "T" else None)
                write_arm(corpus, detector, scored, model_id=absent,
                          cost=zero_cost(time.time() - started), extra=extra)
            print(f"{corpus:12s} {detector:3s} L_full {row[detector]['L_full']:.4f} "
                  f"L_rel {row[detector]['L_rel']:.4f} A {row[detector]['A']:.4f} "
                  f"R {row[detector]['recall']:.4f} F1 {row[detector]['f1']:.4f}",
                  flush=True)
        # R is already scored and committed; read its headline rather than re-deriving.
        row["R"] = _committed_r(corpus)
        summary[corpus] = row
    path = OUT / f"dev_RT_seed{args.seed}.json"
    path.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(f"-> {path.relative_to(ROOT)}")
    return 0


def _committed_r(corpus: str) -> dict:
    """The R arm's published dev headline, read from its own metrics.json.

    Read rather than recomputed. The number in the manuscript's R column has to be the
    one that arm published, and a re-derivation here would be a second scoring of the
    same spans by a different caller — which is the thing that cannot be allowed to
    disagree quietly.
    """
    record = json.loads((ROOT / scorer.path_template("metrics").format(
        corpus=corpus, detector="R", supervision="sup-free",
        porting=rules_arm(corpus))).read_text(encoding="utf-8"))
    relaxed = record["modes"][scorer.RELAXED]
    full = record["modes"][scorer.FULLY_COVERED]
    return {
        "L_full": record["headline"]["leak_rate"]["value"],
        "L_rel": record["headline"]["leak_rate_lower_bound"]["value"],
        **guideline.from_mode_block(relaxed),
        "complementarity_full": full["complementarity"]["families"],
        "D_full": full["by_document"]["rate"],
        "gold": record["counts"]["gold"]["in_scope"],
        "pred": record["counts"]["pred"],
        "porting": rules_arm(corpus),
    }


# ─── table and RT-Sel ───────────────────────────────────────────────────────

def cmd_table(args) -> int:
    absent = base.model_id_absent()
    out = {}
    for corpus in corpora():
        docs, rules_by_doc, tagger_by_doc = load_halves(corpus, args.seed)
        table = sel.build_table(docs, rules_by_doc, tagger_by_doc)
        n_dev = sum(row.n_gold for row in table.values())
        delta = sel.delta_for(n_dev)
        chosen = sel.select(table, delta=delta)
        baseline = sel.combine(table, pol.baseline_map())
        achieved = sel.combine(table, chosen)
        started = time.time()
        scored, _ = score_policy(docs, rules_by_doc, tagger_by_doc, chosen)
        if args.write:
            write_arm(corpus, "RT-Sel", scored, model_id=absent,
                      cost=zero_cost(time.time() - started),
                      extra={"rules_source": rules_source(corpus),
                             "merge_policy": pol.as_record(chosen)})
        out[corpus] = {
            "n_dev_gold": n_dev,
            "delta": delta,
            "table": sel.table_record(table),
            "rt_baseline": baseline,
            "rt_sel": {"policies": pol.as_record(chosen), "achieved": achieved,
                       "headline": headline(scored)},
            "constraint_satisfied": achieved["L_full"] <= baseline["L_full"] + delta,
        }
        differs = [t for t in base.canonical_types()
                   if str(chosen[t]) != str(pol.rt_baseline())]
        print(f"{corpus:12s} delta {delta:.4f}  RT L_full {baseline['L_full']:.4f} "
              f"A {baseline['A']:.4f} -> RT-Sel L_full {achieved['L_full']:.4f} "
              f"A {achieved['A']:.4f}  ({len(differs)} types off baseline)",
              flush=True)
    path = OUT / f"dev_table_seed{args.seed}.json"
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"-> {path.relative_to(ROOT)}")
    return 0


# ─── the Arbiter ────────────────────────────────────────────────────────────

def cmd_arbiter(args) -> int:
    from src.llm import bedrock, envelope

    reg = registration()
    model_id = reg["llm"]["model_id"]
    max_tokens = reg["llm"]["max_tokens"]
    table_file = OUT / f"dev_table_seed{args.seed}.json"
    if not table_file.exists():
        raise SystemExit(f"run `table --seed {args.seed}` first")
    cached = json.loads(table_file.read_text(encoding="utf-8"))

    log = OUT / "arbiter_calls.jsonl"
    out = {}
    for corpus in corpora():
        docs, rules_by_doc, tagger_by_doc = load_halves(corpus, args.seed)
        table = sel.build_table(docs, rules_by_doc, tagger_by_doc)
        shown = sorted(t for t, row in table.items() if row.n_gold)
        delta = cached[corpus]["delta"]
        baseline = sel.combine(table, pol.baseline_map())
        baseline_view = dict(baseline)
        baseline_view["nongold_masked_rate"] = (
            cached[corpus]["rt_sel"]["headline"].get("nongold_masked_rate") or 0.0)

        rounds: list[dict] = []
        previous = None
        last_valid = None
        for number in range(1, reg["rt_arb"]["loop"]["max_rounds_per_corpus"] + 1):
            prompt = arb.assemble(corpus=corpus, n_dev_docs=len(docs), delta=delta,
                                  table=table, baseline=baseline_view,
                                  previous=previous)
            allowed = arb.frame_numbers(arb.build_frame(
                corpus=corpus, n_dev_docs=len(docs), delta=delta, table=table,
                baseline=baseline_view, previous=previous))
            record = {"corpus": corpus, "draw": args.draw, "round": number}
            proposal = None
            for attempt in (1, 2):
                response = bedrock.invoke(prompt, model_id=model_id,
                                          max_tokens=max_tokens)
                read = envelope.unwrap(response.text, parses=envelope.parses_json)
                record.setdefault("attempts", []).append({
                    "attempt": attempt,
                    "envelope": read.record(),
                    "cost": response.cost(),
                    "model": response.model_record(),
                })
                _append(log, {**record["attempts"][-1], "corpus": corpus,
                              "draw": args.draw, "round": number,
                              "prompt": prompt.reference()})
                if not read.accepted:
                    record["attempts"][-1]["error"] = "envelope refused"
                    continue
                try:
                    proposal = arb.parse(read.payload, expected=shown,
                                         allowed_numbers=allowed)
                    break
                except arb.ArbiterError as exc:
                    # The message carries no surface by construction — the guard's
                    # refusals name offsets and lengths only.
                    record["attempts"][-1]["error"] = str(exc)
            if proposal is None:
                record["outcome"] = "format_failure"
                rounds.append(record)
                print(f"{corpus:12s} draw {args.draw} round {number}: "
                      "format failure twice; falling back", flush=True)
                break
            filled = arb.fill_absent(proposal.choices, table)
            achieved = sel.combine(table, filled)
            satisfied = achieved["L_full"] <= baseline["L_full"] + delta
            record.update({
                "outcome": "scored",
                **proposal.as_record(),
                "achieved": achieved,
                "constraint_satisfied": satisfied,
            })
            rounds.append(record)
            last_valid = filled
            print(f"{corpus:12s} draw {args.draw} round {number}: "
                  f"L_full {achieved['L_full']:.4f} A {achieved['A']:.4f} "
                  f"constraint {'ok' if satisfied else 'VIOLATED'}", flush=True)
            improved = (previous is None or achieved["A"] > previous["A"] + 1e-12)
            if satisfied and not improved:
                break
            previous = {
                "round": number, "L_full": achieved["L_full"], "A": achieved["A"],
                "constraint": satisfied,
                "per_type_change": {
                    t: (table[t].candidates[str(filled[t])].A
                        - table[t].candidates[str(pol.rt_baseline())].A)
                    for t in shown
                },
            }

        final, fallback = _final_policy(rounds, table, last_valid)
        started = time.time()
        scored, _ = score_policy(docs, rules_by_doc, tagger_by_doc, final)
        cost = scorer.sum_costs(
            a["cost"] for r in rounds for a in r.get("attempts", []))
        cost["wall_seconds"] = round(cost.get("wall_seconds", 0)
                                     + time.time() - started, 1)
        if args.write and args.draw == reg["rt_arb"]["primary_draw"]:
            write_arm(corpus, "RT-Arb", scored, model_id=model_id, cost=cost,
                      extra={"rules_source": rules_source(corpus),
                             "merge_policy": pol.as_record(final)})
        out[corpus] = {
            "rounds": rounds,
            "final_policy": pol.as_record(final),
            "fell_back_to_RT": fallback,
            "headline": headline(scored),
            "cost": cost,
        }
    path = OUT / f"dev_arbiter_seed{args.seed}_draw{args.draw}.json"
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"-> {path.relative_to(ROOT)}")
    return 0


def _final_policy(rounds, table, last_valid):
    """The constraint-satisfying round with the best dev A, or RT.

    DESIGN §12.3. The fallback is reported as the result rather than hidden: an arm that
    could not satisfy its own pre-registered constraint is a finding about the agent.
    """
    satisfying = [r for r in rounds
                  if r.get("constraint_satisfied") and r.get("outcome") == "scored"]
    if not satisfying:
        return pol.baseline_map(), True
    best = max(satisfying, key=lambda r: r["achieved"]["A"])
    return arb.fill_absent(pol.from_record(best["policies"]), table), False


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


# ─── consistency: the run_repeat layer ──────────────────────────────────────

def cmd_consistency(args) -> int:
    """C's `run_repeat` layer: the same weights and policy re-applied three times.

    DESIGN §12.5 says rules expect 1.0 and the tagger may not reach it under MPS
    non-determinism, and that the measured value is reported either way. That sentence
    was written before this ran, which is why re-defining "the same run" after seeing a
    number below 1.0 is not available.
    """
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer
    from src.tagger.train import predict

    weights = OUT / "weights" / f"seed{args.seed}"
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(weights)
    model = AutoModelForTokenClassification.from_pretrained(weights).to(device)

    out = {}
    for corpus in corpora():
        docs = [d for d in base.load(corpus) if d.split == "dev"]
        rules_by_doc: dict[str, list] = {}
        for span in read_arm_spans(corpus=corpus, detector="R",
                                   supervision="sup-free",
                                   porting=rules_arm(corpus)):
            rules_by_doc.setdefault(span.doc_id, []).append(span)

        tagger_runs = []
        for repeat in range(3):
            preds = predict(model, tokenizer, docs, device, detector="T")
            tagger_runs.append(preds)
        rules_runs = [
            {d.doc_id: list(rules_by_doc.get(d.doc_id, ())) for d in docs}
            for _ in range(3)
        ]

        def keys(runs):
            return [{d.doc_id: guideline.spans_to_keys(run.get(d.doc_id, []))
                     for d in docs} for run in runs]

        rt = [
            {d.doc_id: pol.apply_map(
                list(rules_by_doc.get(d.doc_id, ())) + list(t.get(d.doc_id, ())),
                pol.baseline_map()) for d in docs}
            for t in tagger_runs
        ]
        out[corpus] = {
            "R": guideline.consistency(keys(rules_runs)),
            "T": guideline.consistency(keys(tagger_runs)),
            "RT": guideline.consistency(keys(rt)),
        }
        print(f"{corpus:12s} C(run_repeat)  R {out[corpus]['R']['C']:.6f}  "
              f"T {out[corpus]['T']['C']:.6f}  RT {out[corpus]['RT']['C']:.6f}",
              flush=True)
    path = OUT / f"consistency_run_repeat_seed{args.seed}.json"
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"-> {path.relative_to(ROOT)}")
    return 0


def cmd_author_repeat(args) -> int:
    """C's `author_repeat` layer, for the part that retained its artefacts.

    DESIGN §12.5 registers two groups: MEDDOCAN round 1's five draws of one prompt (R=5)
    and the three `port-loop-noaudit` repetitions' final rules (R=3).

    **Only R=3 is computable, and the reason is a property of the earlier probe rather
    than of this metric.** `tools/probe_call_variance.py` writes each draw to a temporary
    directory and says so in its own help text — "results/ is not touched" — so the five
    draws of 2026-08-21 left a measured summary (rule counts, response digests, pairwise
    Jaccard over `rule_id`s) and no rule files. Span-level C needs the predictions, and
    the predictions cannot be regenerated from a digest.

    What is on record for those five is a Jaccard over **rule ids**, which is a different
    object from C: it compares which rules were written, not which spans they found, and
    two rule sets sharing no ids can still mask the same characters. It is reported beside
    C as context and is not reported as C.

    The three repetitions are a different case: each published its own `spans.jsonl`, so
    the predictions exist and C is a direct computation. Note what it measures — the
    repetitions differ only in when the call was made (DESIGN §6.10), so this is the
    `author_repeat` layer in its cleanest form.
    """
    reg = registration()
    group = reg["guideline"]["consistency"]["layers"]["author_repeat"]
    arms = ["port-loop-noaudit", "port-loop-noaudit-rep2", "port-loop-noaudit-rep3"]
    corpus = "es-meddocan"
    docs = [d for d in base.load(corpus) if d.split == "dev"]

    runs = []
    for arm in arms:
        by_doc: dict[str, list] = {}
        for span in read_arm_spans(corpus=corpus, detector="R",
                                   supervision="sup-free", porting=arm):
            by_doc.setdefault(span.doc_id, []).append(span)
        runs.append({d.doc_id: guideline.spans_to_keys(by_doc.get(d.doc_id, []))
                     for d in docs})
    out = {
        "noaudit_final_rules": {
            "arms": arms,
            "R": len(arms),
            **guideline.consistency(runs),
        },
        "meddocan_round1_draws": {
            "R": group["meddocan_round1_draws"],
            "C": None,
            "not_computable": (
                "probe_call_variance.py wrote each draw to a temporary directory and "
                "left no rule files, so the predictions span-level C needs do not "
                "exist and cannot be regenerated from a response digest"
            ),
            "on_record_instead": {
                "measured": "2026-08-21, docs/notes/call-variance.md",
                "object": "pairwise Jaccard over rule_id sets, not over predicted spans",
                "pairs": 10,
                "range": [0.098, 0.4286],
                "mean": 0.2417,
            },
        },
    }
    print(f"C(author_repeat) noaudit R=3: {out['noaudit_final_rules']['C']:.6f} "
          f"over {out['noaudit_final_rules']['documents']} documents")
    print("C(author_repeat) round-1 draws R=5: not computable "
          "(draws left no rule files); rule_id Jaccard mean 0.2417 is on record")
    path = OUT / "consistency_author_repeat.json"
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"-> {path.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    one = sub.add_parser("predict")
    one.add_argument("--seed", type=int, required=True)
    one.add_argument("--split", default="dev", choices=("train", "dev"))
    one.set_defaults(fn=cmd_predict)

    two = sub.add_parser("score-dev")
    two.add_argument("--seed", type=int, required=True)
    two.add_argument("--write", action="store_true")
    two.set_defaults(fn=cmd_score_dev)

    three = sub.add_parser("table")
    three.add_argument("--seed", type=int, required=True)
    three.add_argument("--write", action="store_true")
    three.set_defaults(fn=cmd_table)

    four = sub.add_parser("arbiter")
    four.add_argument("--seed", type=int, required=True)
    four.add_argument("--draw", type=int, required=True, choices=(1, 2, 3))
    four.add_argument("--write", action="store_true")
    four.set_defaults(fn=cmd_arbiter)

    five = sub.add_parser("consistency")
    five.add_argument("--seed", type=int, required=True)
    five.set_defaults(fn=cmd_consistency)

    six = sub.add_parser("author-repeat")
    six.set_defaults(fn=cmd_author_repeat)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
