"""Train the tagger, and select the epoch. DESIGN §12.1, §13.1.

    python3 -m src.tagger.train --seed 1
    python3 -m src.tagger.train --seed 1 --holdout de-grascco     # v24b, DESIGN §13

One `xlm-roberta-base` over the five train folds pooled. After every epoch the model is
scored on all five dev folds and the **summed** `L_full` is recorded; the checkpoint kept
is the epoch that minimises it. Test is never read, here or anywhere in §12.

**`--holdout` is v24b's one changed factor (DESIGN §13.1).** With it, the named corpus's
train fold is dropped from the pool *and* its dev fold is dropped from the epoch-selection
curve, so neither its labels nor its leak rate can reach the model or the checkpoint
choice. Every hyperparameter is the one above. The two exclusions are deliberately one
flag: a LOCO model whose epoch was chosen on the held-out corpus's dev fold is an
in-domain model with extra steps, and that is the mistake a second flag would make
available.

**Why summed and not pooled.** `es-meddocan` holds 5,254 of the 7,771 in-scope dev spans,
so a pooled leak rate is 68% one corpus and the epoch would effectively be selected on it
alone. Summing the five per-corpus rates weights each corpus equally, which is what a
multilingual model's checkpoint should be chosen on. The consequence is stated rather than
hidden: a corpus with few spans moves the sum as much as one with many, so the selected
epoch is not the one that minimises total leaked identifiers. `registration.yaml` names the
criterion `summed_dev_L_full` and this is what that means.

**Nothing here logs text.** The progress lines carry epoch, step, loss, span counts and
rates. Three of the five train folds are DUA-restricted and a training log is the easiest
place in a project like this for a surface to escape, because the conventional thing to
print is a sample.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

from ..corpora import base
from ..eval import scorer
from . import windows as win
from .labels import IGNORE_INDEX, check_n_labels, n_labels

MODEL_NAME = "xlm-roberta-base"
CORPORA = ["es-meddocan", "de-grascco", "es-carmen", "en-deid", "ko-surro"]

#: Weights and per-epoch curves. Gitignored (.gitignore, v24 block): the weights saw the
#: train folds of three credentialed corpora and are not publishable.
OUT = Path("experiments/hybrid_v24")

#: v24b's own directory (DESIGN §13). Separate from v24's rather than a subdirectory of
#: it: the two are different pre-registrations with different arms, and a LOCO weights
#: file sitting under `hybrid_v24/weights/` would be one `ls` away from being read as a
#: seed of the in-domain model, which `run_hybrid.py` names by seed alone.
LOCO_OUT = Path("experiments/loco_v24b")


def memory_snapshot() -> dict:
    """This process's RSS and the machine's swap, in GB. Recorded per model.

    Asked for after the 2026-10-15 OOM, one line before and one line after each model, so
    that the next run's cost is a number in the record rather than a thing somebody
    remembers. `ps` and `sysctl` rather than a dependency: this is a two-field diagnostic
    and `pyproject.toml` is not the place to grow for it.
    """
    out: dict[str, float | None] = {"rss_gb": None, "swap_used_gb": None}
    try:
        rss = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())],
                             capture_output=True, text=True, check=True).stdout
        out["rss_gb"] = round(int(rss.strip()) / 2**20, 2)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        pass
    try:
        usage = subprocess.run(["sysctl", "-n", "vm.swapusage"],
                               capture_output=True, text=True, check=True).stdout
        # "total = 4096.00M  used = 2434.88M  free = 1661.12M  (encrypted)"
        used = usage.split("used =")[1].split()[0]
        out["swap_used_gb"] = round(float(used.rstrip("M")) / 1024, 2)
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError,
            ValueError):
        pass
    return out


def _memory_line(when: str, tag: str) -> dict:
    snapshot = memory_snapshot()
    print(f"[{tag}] memory {when}: rss {snapshot['rss_gb']} GB  "
          f"swap used {snapshot['swap_used_gb']} GB", flush=True)
    return snapshot


def _tree_state() -> tuple[str | None, str]:
    """`(commit, "clean"|"dirty")` for the training record.

    Imported locally from the one implementation rather than shelling out again: the
    sealed log's row and this record then answer the question the same way, which is the
    property that lets a reader check that an arm's weights and its log row describe one
    commit. `src.eval.sealed_log` imports no torch, so this costs nothing.
    """
    from ..eval.sealed_log import tree_state
    return tree_state()


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.mps.manual_seed(seed) if torch.backends.mps.is_available() else None


class WindowSet(Dataset):
    """Windows with their BIO labels, flattened across documents and corpora.

    The corpus a window came from is **not** a feature and is not carried: one model over
    the pooled folds (DESIGN §12.1), so a corpus id reaching the model would make it five
    models sharing weights. It is kept in `self.origin` for the per-corpus dev scoring,
    which is a different operation on a different split.
    """

    def __init__(self, items: list[tuple[win.Window, list[int]]]):
        self.items = items

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> dict:
        window, labels = self.items[index]
        return {
            "input_ids": torch.tensor(window.input_ids, dtype=torch.long),
            "attention_mask": torch.tensor(window.attention_mask, dtype=torch.long),
            "labels": torch.tensor(labels, dtype=torch.long),
        }


def collate(rows: list[dict]) -> dict:
    """Right-pad to the longest row. Labels pad with `IGNORE_INDEX`, not 0.

    Padding labels with 0 would pad with `O`, and the model would be trained that every
    padded position is reliably not PHI — a large, free, and entirely spurious source of
    `O` supervision that grows with how ragged the batch is.
    """
    width = max(len(row["input_ids"]) for row in rows)
    out: dict[str, torch.Tensor] = {}
    for key, pad in (("input_ids", 1), ("attention_mask", 0),
                     ("labels", IGNORE_INDEX)):
        out[key] = torch.stack([
            torch.cat([row[key],
                       torch.full((width - len(row[key]),), pad, dtype=torch.long)])
            for row in rows
        ])
    return out


def build(tokenizer, split: str,
          corpora: list[str] | None = None) -> tuple[WindowSet, dict[str, list]]:
    """Window and label one split across the corpora named (all five by default).

    Returns the dataset and `{corpus: [Document]}` — the documents come back because dev
    scoring needs them per corpus, and re-loading would read the corpora twice.

    `corpora` is the whole of what `--holdout` changes: the pool and the dev curve are
    both built from this list, so one argument decides both and they cannot drift apart.
    """
    items: list[tuple[win.Window, list[int]]] = []
    docs_by_corpus: dict[str, list] = {}
    for corpus in (CORPORA if corpora is None else corpora):
        docs = [d for d in base.load(corpus) if d.split == split]
        docs_by_corpus[corpus] = docs
        for doc in docs:
            for window in win.encode(tokenizer, doc):
                items.append((window, win.bio_labels(window, doc.spans)))
    return WindowSet(items), docs_by_corpus


def release_cache() -> None:
    """Return MPS's cached blocks to the allocator. Measured 2026-10-15.

    **Why this is here and not a tidiness habit.** Inference batches are padded to
    `max(len(w.input_ids))` over each chunk of windows, so a sweep over a corpus asks for
    a great many distinct tensor shapes; MPS's caching allocator keeps a block per shape
    and never returns it, so the process grows by roughly the dev folds' worth of shapes
    on every epoch. v24's own curve shows it — seed 1's epochs ran 589, 492, 503, 510,
    510, 600, 827, 1169 seconds for identical work, a 2.4× creep by epoch 8 — and it was
    read then as warm-up noise.

    v24b made it unmissable. The first LOCO run (holdout `es-meddocan`, killed) took 497 s
    for epoch 1 and **1594 s** for epoch 2 on a *smaller* pool than v24's; the process was
    at 21.7 GB RSS and climbing 430 MB/min, with 18 of the machine's 20 GB of swap in use.
    Killing it returned the swap to 3.9 GB, which is what identifies the holder.

    No hyperparameter and no numeric is touched: this frees cached blocks, not live
    tensors, so it changes how much memory the run needs and not what it computes.
    DESIGN §13.1's "identical to §12.1 in every hyperparameter" is unaffected, and the
    weights are identified by their hash in any case (§12.8).
    """
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


@torch.no_grad()
def predict(model, tokenizer, docs: list, device, *, batch_size: int = 16,
            detector: str) -> dict[str, list]:
    """Character spans per document, windows reconciled per token by max probability."""
    model.eval()
    out: dict[str, list] = {}
    for doc in docs:
        wins = win.encode(tokenizer, doc)
        probs: list[list[list[float]]] = []
        for start in range(0, len(wins), batch_size):
            chunk = wins[start:start + batch_size]
            width = max(len(w.input_ids) for w in chunk)
            ids = torch.stack([
                torch.cat([torch.tensor(w.input_ids, dtype=torch.long),
                           torch.full((width - len(w.input_ids),), 1,
                                      dtype=torch.long)]) for w in chunk]).to(device)
            mask = torch.stack([
                torch.cat([torch.tensor(w.attention_mask, dtype=torch.long),
                           torch.zeros(width - len(w.attention_mask),
                                       dtype=torch.long)]) for w in chunk]).to(device)
            logits = model(input_ids=ids, attention_mask=mask).logits
            soft = torch.softmax(logits.float(), dim=-1).cpu().tolist()
            for w, row in zip(chunk, soft):
                probs.append(row[:len(w.input_ids)])
        tokens = win.reconcile(wins, probs)
        out[doc.doc_id] = win.decode_spans(tokens, doc.text, detector=detector)
    # Once per sweep and not once per document: the blocks accumulate across documents
    # because each one's windows have their own widths, and a call inside the loop would
    # pay the allocator's cost on every document to reclaim what the next one asks for.
    release_cache()
    return out


def score_dev(model, tokenizer, docs_by_corpus: dict[str, list], device,
              *, detector: str) -> dict:
    """Per-corpus `L_full` and the sum that selects the epoch."""
    per_corpus = {}
    for corpus, docs in docs_by_corpus.items():
        preds = predict(model, tokenizer, docs, device, detector=detector)
        pairs, excluded = scorer.from_documents(docs, preds)
        scored = scorer.score(pairs, excluded_gold=excluded)
        relaxed = scored["modes"][scorer.RELAXED]["overall"]
        per_corpus[corpus] = {
            "L_full": scored["headline"]["leak_rate"]["value"],
            "L_rel": scored["headline"]["leak_rate_lower_bound"]["value"],
            "A": _accuracy(relaxed),
            "recall": relaxed["recall"],
            "f1": relaxed["f1"],
            "pred": scored["counts"]["pred"],
            "gold": scored["counts"]["gold"]["in_scope"],
        }
    return {
        "per_corpus": per_corpus,
        "summed_L_full": sum(v["L_full"] for v in per_corpus.values()),
    }


#: Where an epoch's weights are written so a child process can score them. One directory
#: reused across epochs: it is a scratch copy, and keeping eight of them would be eight
#: 1.1 GB files whose only reader has already exited.
SCRATCH = "epoch_scratch"

#: How often `release_cache()` is called inside the training loop. Measured 2026-10-16.
#:
#: Moving the dev sweeps into a child process (above) bounded the leak on that side and
#: left it on this one: with the child path in place, holdout `es-meddocan` still ran
#: 498, 503, **1268** s, and the *parent* was at 20.6 GB RSS with 53 GB of the machine's
#: memory wired at epoch 3. Killing it returned wired to 2.3 GB, which identifies the
#: holder as the training loop rather than the scoring.
#:
#: `shuffle=True` with per-batch padding means the loop asks for a different padded width
#: almost every step, so the allocator's block set grows across an epoch. Releasing on an
#: interval keeps it bounded. 50 is ~6 calls per epoch here — often enough to hold the
#: pool down, rare enough that the re-allocation it forces is not the loop's cost.
#:
#: The cause is **not** investigated; this is the same mitigation as `release_cache`'s
#: other two call sites, applied to the phase they left out (DESIGN §13.6).
RELEASE_EVERY = 50


def score_dev_in_child(weights: Path, corpora: list[str], *,
                       detector: str) -> dict:
    """Run `score_dev` in a child process and read back its JSON.

    **Why a child process, decided 2026-10-15 after an OOM and not before.** The
    in-process path leaks across epochs — the dev sweeps' cost grew from ~77 s to ~575 s
    over four epochs and then to 6,177 s, and holdout `es-meddocan` died in epoch 7's
    backward pass with 21.5 GB allocated on MPS and 66 GB elsewhere. `release_cache()`
    slowed the growth and did not stop it. The cause is **not investigated** — that is a
    recorded limitation (DESIGN §13.6) rather than a thing this function fixes.

    What it does instead is bound the leak: a process that exits returns everything, so
    the sweep's cost is paid once per epoch rather than accumulated over eight. The
    parent keeps the model and the optimiser and nothing else.

    **It computes the same numbers, and that is checked rather than asserted.** Inference
    is deterministic on MPS (DESIGN §12.7, C = 1.000000), so the child's values are
    byte-identical to the in-process path's on the same weights;
    `tools/verify_child_scoring.py` demonstrates it on v24's committed seed 1 before this
    path trains anything.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "src.tagger.train", "--score-only", str(weights),
         "--score-corpora", *corpora, "--detector", detector],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        # The child's stderr is passed through: it is a traceback about weights and
        # corpora ids, and the alternative is a parent that reports "scoring failed".
        raise SystemExit(
            f"dev scoring child exited {proc.returncode}. Its stderr follows.\n"
            f"{proc.stderr.strip()}"
        )
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise SystemExit(
            f"dev scoring child wrote {len(proc.stdout)} bytes that are not JSON "
            f"({exc.__class__.__name__}). Its stdout is the only channel this value "
            "comes back on, so a print() added to that path breaks it."
        ) from None


def _score_only(args) -> int:
    """The child's whole job: load weights, score the named dev folds, print JSON.

    Nothing else may be printed on stdout from here — the parent parses it.
    """
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(args.score_only)
    model = AutoModelForTokenClassification.from_pretrained(args.score_only).to(device)
    _, dev_docs = build(tokenizer, "dev", list(args.score_corpora))
    dev = score_dev(model, tokenizer, dev_docs, device, detector=args.detector)
    dev["mps_driver_gb"] = (round(torch.mps.driver_allocated_memory() / 2**30, 2)
                            if torch.backends.mps.is_available() else None)
    dev["memory"] = memory_snapshot()
    print(json.dumps(dev))
    return 0


def _accuracy(overall: dict) -> float:
    """A = TP / (TP + FP + FN), the guideline's accuracy (DESIGN §12.5)."""
    denominator = overall["tp"] + overall["fp"] + overall["fn"]
    return overall["tp"] / denominator if denominator else 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="train the v24 tagger (DESIGN §12.1)")
    # Not `required=True`: the `--score-only` child trains nothing, so a seed would be a
    # number it reads and does not use. Required for every other invocation, and that is
    # checked below rather than relaxed — a training run with no seed is unrecorded.
    parser.add_argument("--seed", type=int, choices=(1, 2, 3))
    parser.add_argument(
        "--holdout", choices=tuple(CORPORA), default=None,
        help="train leave-one-corpus-out (DESIGN §13): drop this corpus's train fold "
             "from the pool and its dev fold from the epoch-selection curve. Without "
             "it, the v24 in-domain model over all five.",
    )
    parser.add_argument(
        "--dev-in-subprocess", action="store_true",
        help="score each epoch's dev folds in a child process (DESIGN §13.6). Bounds "
             "the leak that OOM'd the first v24b attempt; computes the same numbers, "
             "which tools/verify_child_scoring.py checks against v24's seed 1.",
    )
    parser.add_argument(
        "--score-only", metavar="WEIGHTS", default=None,
        help="internal: the child half of --dev-in-subprocess. Loads WEIGHTS, scores "
             "the dev folds named by --score-corpora, prints one JSON object on stdout "
             "and nothing else.",
    )
    parser.add_argument("--score-corpora", nargs="*", default=tuple(CORPORA),
                        help="internal: which dev folds --score-only reads.")
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=3e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-ratio", type=float, default=0.10)
    parser.add_argument("--detector", default="T")
    args = parser.parse_args(argv)

    from transformers import (AutoModelForTokenClassification, AutoTokenizer,
                              get_linear_schedule_with_warmup)

    check_n_labels(21)
    if args.score_only:
        # The child half. Before `set_seed` and before any output, because this branch
        # trains nothing and its stdout is a parsed channel.
        return _score_only(args)
    if args.seed is None:
        raise SystemExit(
            "--seed is required to train. It is optional only for the --score-only "
            "child, which trains nothing."
        )
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    set_seed(args.seed)

    # The one list `--holdout` changes, and both calls below read it.
    used = [c for c in CORPORA if c != args.holdout]
    tag = f"seed {args.seed}" if args.holdout is None else f"loco {args.holdout}"
    out_dir = OUT if args.holdout is None else LOCO_OUT
    weights_name = (f"seed{args.seed}" if args.holdout is None
                    else f"loco-{args.holdout}")
    record_name = (f"tagger_seed{args.seed}.json" if args.holdout is None
                   else f"tagger_loco_{args.holdout}.json")

    memory_before = _memory_line("before", tag)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    print(f"[{tag}] windowing train over {len(used)} corpora", flush=True)
    train_set, _ = build(tokenizer, "train", used)
    # The parent loads the dev documents only on the in-process path. Under
    # `--dev-in-subprocess` the child loads them, so the parent holds the model, the
    # optimiser and the train windows and nothing else — which is the point of the flag.
    dev_docs: dict[str, list] = {}
    if not args.dev_in_subprocess:
        _, dev_docs = build(tokenizer, "dev", used)
    print(f"[{tag}] train windows {len(train_set)}  "
          f"dev docs {sum(len(d) for d in dev_docs.values()) or 'in child'}", flush=True)
    if args.holdout is not None and args.holdout in used:
        # Cannot happen through `used`, and asserted anyway: this is the one property of
        # the run that makes `sup-transfer` true, and it is cheaper to check than to
        # argue about afterwards. Asserted on `used` rather than on `dev_docs`, because
        # `used` is what both paths read and `dev_docs` is empty on one of them — the
        # earlier form would have passed vacuously under `--dev-in-subprocess`.
        raise SystemExit(
            f"the held-out corpus {args.holdout!r} is in the training list, so the "
            "epoch would be selected on labels DESIGN §13.1 says are not read."
        )

    model = AutoModelForTokenClassification.from_pretrained(
        MODEL_NAME, num_labels=n_labels()).to(device)
    loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True,
                        collate_fn=collate, drop_last=False)
    steps = len(loader) * args.epochs
    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr,
                                  weight_decay=args.weight_decay)
    schedule = get_linear_schedule_with_warmup(
        optimiser, int(args.warmup_ratio * steps), steps)

    weights = out_dir / "weights" / weights_name
    weights.mkdir(parents=True, exist_ok=True)
    curve: list[dict] = []
    best = {"epoch": None, "summed_L_full": None}
    started = time.time()

    for epoch in range(1, args.epochs + 1):
        model.train()
        epoch_started = time.time()
        total = 0.0
        for step, batch in enumerate(loader, 1):
            batch = {k: v.to(device) for k, v in batch.items()}
            optimiser.zero_grad(set_to_none=True)
            loss = model(**batch).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimiser.step()
            schedule.step()
            total += loss.item()
            if step % RELEASE_EVERY == 0:
                release_cache()
            if step % 100 == 0:
                print(f"[{tag}] epoch {epoch} step {step}/{len(loader)} "
                      f"loss {total / step:.4f}", flush=True)
        train_loss = total / len(loader)
        # Between the two phases: training ran at one shape and dev scoring is about to
        # ask for many, so the blocks held here are the ones least likely to be reused.
        release_cache()
        if args.dev_in_subprocess:
            # Written where the child can read it, then scored by a process that exits.
            # The scratch copy is also what gets promoted below if this epoch is best,
            # so the weights are serialised once per epoch rather than twice.
            scratch = out_dir / "weights" / f"{weights_name}.{SCRATCH}"
            model.save_pretrained(scratch)
            tokenizer.save_pretrained(scratch)
            release_cache()
            dev = score_dev_in_child(scratch, used, detector=args.detector)
        else:
            dev = score_dev(model, tokenizer, dev_docs, device,
                            detector=args.detector)
        row = {
            "epoch": epoch,
            "train_loss": train_loss,
            "train_seconds": round(time.time() - epoch_started, 1),
            # Recorded per epoch so the creep this release exists to stop is visible in
            # the curve rather than only in a killed run's log. Under
            # `--dev-in-subprocess` this is the *parent's* allocation, which is the
            # number the flag is supposed to hold flat.
            "mps_driver_gb": (round(torch.mps.driver_allocated_memory() / 2**30, 2)
                              if torch.backends.mps.is_available() else None),
            "scored_in_child": bool(args.dev_in_subprocess),
            **dev,
        }
        curve.append(row)
        print(f"[{tag}] epoch {epoch} loss {train_loss:.4f} "
              f"summed_L_full {dev['summed_L_full']:.4f} "
              f"({row['train_seconds']:.0f}s)", flush=True)
        for corpus, value in dev["per_corpus"].items():
            print(f"    {corpus:12s} L_full {value['L_full']:.4f}  "
                  f"A {value['A']:.4f}  R {value['recall']:.4f}", flush=True)

        if best["summed_L_full"] is None or dev["summed_L_full"] < best["summed_L_full"]:
            best = {"epoch": epoch, "summed_L_full": dev["summed_L_full"]}
            if args.dev_in_subprocess:
                # Promote the bytes the child scored, rather than re-serialising the
                # model. Copying is what makes the published weights provably the ones
                # the selected epoch's number came from.
                shutil.copytree(scratch, weights, dirs_exist_ok=True)
            else:
                model.save_pretrained(weights)
                tokenizer.save_pretrained(weights)
            print(f"[{tag}] epoch {epoch} is the new best; saved",
                  flush=True)

    if args.dev_in_subprocess:
        shutil.rmtree(out_dir / "weights" / f"{weights_name}.{SCRATCH}",
                      ignore_errors=True)
    memory_after = _memory_line("after", tag)
    commit, tree = _tree_state()
    record = {
        "seed": args.seed,
        "model": MODEL_NAME,
        "device": str(device),
        "n_labels": n_labels(),
        "epochs_run": args.epochs,
        "train_windows": len(train_set),
        "selected_epoch": best["epoch"],
        "selected_summed_L_full": best["summed_L_full"],
        "selection_criterion": "summed_dev_L_full",
        "test_used": False,
        # v24b's three fields (DESIGN §13.1). `holdout` is `None` for the in-domain
        # model, which is a stated absence and not a missing field: the LOCO records and
        # the v24 records are read by the same driver and a key only one of them carries
        # is a key that cannot be compared across them.
        "holdout": args.holdout,
        "train_corpora": used,
        "selection_corpora": used,
        "holdout_dev_used": False,
        # How the dev folds were scored, and what the run cost in memory. Both are in
        # the record because the first v24b attempt died of the second (DESIGN §13.6).
        "dev_scored_in_subprocess": bool(args.dev_in_subprocess),
        "memory_before": memory_before,
        "memory_after": memory_after,
        # The other half of what identifies the weights: DESIGN §12.8 says the seed does
        # not, so the code that produced them is recorded beside their hash. `tree` is
        # `dirty` when the commit does not describe what ran.
        "commit": commit,
        "tree": tree,
        "total_seconds": round(time.time() - started, 1),
        "hyperparameters": {
            "lr": args.lr, "weight_decay": args.weight_decay,
            "batch_size": args.batch_size, "warmup_ratio": args.warmup_ratio,
            "max_tokens": win.MAX_TOKENS, "stride": win.STRIDE,
        },
        "curve": curve,
    }
    # The curve is committable: it is epochs, losses, rates and counts, with no
    # document id, span or surface in it. The weights beside it are not.
    out = out_dir / record_name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(f"[{tag}] selected epoch {best['epoch']} "
          f"(summed L_full {best['summed_L_full']:.4f}) in "
          f"{record['total_seconds'] / 3600:.2f} h -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
