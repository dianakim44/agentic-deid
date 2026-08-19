#!/usr/bin/env python3
"""Run every mutation across N shards and refuse to call the result a full run unless it is.

    python3 tests/mutations/parallel.py                    # 8 shards, append the record
    python3 tests/mutations/parallel.py --shards 4
    python3 tests/mutations/parallel.py --out /tmp/x.md --dry-record   # validation runs

Why this exists rather than eight shells and a person reading eight logs: that is how the
run of 2026-08-19 was done, and the compilation was a throwaway script whose correctness
nobody could check afterwards. A record in `docs/notes/` outlives the shell it came from,
so the thing that writes it has to be the thing that checks it.

**The failure this file is built around is a partial run recorded as a full one.** It is the
same shape as every other defect in `README.md`: a mechanism that cannot tell "the check
passed" from "the check did not run" resolves the ambiguity in the reassuring direction. A
shard killed by the OOM reaper prints no failures. Eight shards that between them skipped a
mutation print no failures either. Both read as green.

So five invariants must hold before the word "full" is written, and each one catches a
different way of being short:

1. **Exact partition.** Every registered mutation name appears exactly once across the
   shards. Catches both directions at once — a name in two shards is two counts for one
   guarantee, a name in none is silence that looks like a pass.
2. **One tree.** All shards report the same pristine fingerprint *and* the same baseline
   total. The fingerprint is the load-bearing half: two trees can collect the same number of
   tests and behave differently, and a kill count is about behaviour.
3. **Every shard finished.** Each shard's JSON carries `complete: true`, written last on
   purpose, and exited with a status this driver understands.
4. **Verdicts account for everyone.** caught + survived + stale + broken + dirty equals the
   number registered. A result silently dropped between the shard and the aggregate would
   otherwise leave the totals looking consistent.
5. **The tree did not move under the run.** `git rev-parse HEAD` and
   `git status --porcelain` are compared start to end. This is *not* what invariant 2
   covers: fingerprints are taken at shard start, so an edit at minute thirty leaves every
   shard's copy identical and every count valid — what it breaks is the record's
   provenance, the claim that re-running at this commit reproduces these numbers.

Any of them failing writes an INCOMPLETE record naming what is missing, and exits non-zero.
The record always carries the shard count, the fingerprint and the per-shard baselines, so a
partial run is visibly partial to somebody reading the page a year later.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(__file__).resolve().parent / "run.py"
RECORD = ROOT / "docs" / "notes" / "mutation-full-runs.md"
COUNTS = ROOT / "docs" / "notes" / "mutation-full-runs.counts.json"


def _harness():
    """Import run.py for MUTATIONS. Registered in sys.modules first because `dataclass`
    reads the module back out of it while building `Mutation`, and gets `None` otherwise."""
    spec = importlib.util.spec_from_file_location("mutations_run", RUN)
    module = importlib.util.module_from_spec(spec)
    sys.modules["mutations_run"] = module
    spec.loader.exec_module(module)
    return module


def load_previous(path: Path):
    """Kill counts from an earlier run, accepting the sidecar's shape or a bare mapping.

    The bare shape is what the first comparison had available — counts scraped out of
    `README.md`'s tables — and keeping it readable means the first sidecar is diffed against
    real history instead of starting from nothing."""
    if not path or not path.exists():
        return None
    blob = json.loads(path.read_text(encoding="utf-8"))
    return blob.get("counts", blob) if isinstance(blob, dict) else None


def git_state() -> dict:
    def out(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True).stdout.strip()
    return {"head": out("rev-parse", "HEAD"), "porcelain": out("status", "--porcelain")}


def launch(shards: int, logdir: Path, limit=None, stagger=0.0) -> list[dict]:
    """Start all shards at once and wait. `-u` because the run of 2026-08-19 produced eight
    empty logs for four hours: Python block-buffers stdout when it is a file, so progress was
    invisible and had to be inferred from temp-directory names."""
    procs = []
    for i in range(shards):
        if stagger and i:
            time.sleep(stagger)
        log = (logdir / f"shard{i}.log").open("w", encoding="utf-8")
        procs.append((i, log, subprocess.Popen(
            [sys.executable, "-u", str(RUN), "--shard", f"{i}/{shards}",
             "--json", str(logdir / f"shard{i}.json")]
            + (["--limit", str(limit)] if limit else []),
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        )))
    out = []
    for i, log, proc in procs:
        code = proc.wait()
        log.close()
        path = logdir / f"shard{i}.json"
        record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        out.append({"shard": i, "exit": code, "record": record,
                    "log": str(logdir / f"shard{i}.log")})
    return out


def check(shards: list[dict], registered: list[str], before: dict, after: dict) -> list[str]:
    """The five invariants. Returns the reasons the run is not a full run; empty means it is."""
    broken = []

    missing_json = [s["shard"] for s in shards if s["record"] is None]
    if missing_json:
        broken.append(f"shards wrote no JSON at all: {missing_json}")
    live = [s for s in shards if s["record"]]

    # 3. every shard finished
    unfinished = [s["shard"] for s in live if not s["record"].get("complete")]
    if unfinished:
        detail = ", ".join(
            f"{s['shard']} ({s['record'].get('aborted') or 'no terminal record'}, "
            f"{len(s['record']['results'])}/{len(s['record']['selected'])} done)"
            for s in live if not s["record"].get("complete")
        )
        broken.append(f"shards did not finish: {detail}")
    bad_exit = [(s["shard"], s["exit"]) for s in shards if s["exit"] not in (0, 1)]
    if bad_exit:
        broken.append(f"shards exited with an unrecognised status: {bad_exit}")

    # 1. exact partition
    seen = [r["name"] for s in live for r in s["record"]["results"]]
    duplicated = sorted({n for n in seen if seen.count(n) > 1})
    absent = sorted(set(registered) - set(seen))
    unknown = sorted(set(seen) - set(registered))
    if duplicated:
        broken.append(f"measured more than once: {duplicated}")
    if absent:
        broken.append(f"never measured ({len(absent)} of {len(registered)}): {absent}")
    if unknown:
        broken.append(f"results for unregistered names: {unknown}")

    # 2. one tree
    prints = {s["record"]["fingerprint"] for s in live}
    if len(prints) > 1:
        broken.append(
            "shards ran against different trees — fingerprints " +
            ", ".join(f"{s['shard']}:{s['record']['fingerprint'][:12]}" for s in live)
        )
    baselines = {s["record"]["baseline_outcomes"] for s in live}
    if len(baselines) > 1:
        broken.append(f"shards disagree on the baseline total: {sorted(baselines)}")

    # 4. verdicts account for everyone
    if not broken and len(seen) != len(registered):
        broken.append(f"verdicts cover {len(seen)} of {len(registered)} mutations")

    # 5. the tree did not move under the run
    if before != after:
        moved = [k for k in before if before[k] != after[k]]
        broken.append(
            f"the working tree changed while the run was in flight ({', '.join(moved)}); "
            "the counts are still about one tree — every shard copied before the edit — but "
            "the record could no longer say which tree, so it is not written as a full run"
        )
    return broken


def render(status, broken, shards, registered, wall, before, after, previous):
    live = [s for s in shards if s["record"]]
    results = [r for s in live for r in s["record"]["results"]]
    by_verdict = {}
    for r in results:
        by_verdict.setdefault(r["verdict"], []).append(r)
    fingerprints = {s["record"]["fingerprint"] for s in live}
    baselines = {s["record"]["baseline_outcomes"] for s in live}
    stamp = datetime.date.today().isoformat()

    lines = [f"## {stamp} — {status}", ""]
    if broken:
        lines += ["**This is not a full-run record.** It is kept because a refused run is "
                  "evidence too, and deleting it would leave the log looking like the run "
                  "never happened.", ""]
        lines += [f"- {reason}" for reason in broken] + [""]

    dirty = "dirty" if before["porcelain"] else "clean"
    lines += [
        f"- commit `{before['head'][:12]}` ({dirty} working tree)",
        f"- {len(shards)} shards, {len(results)} of {len(registered)} mutations measured",
        f"- wall clock **{wall / 3600:.2f} h** ({wall:.0f} s)",
        f"- tree fingerprint " + (f"`{sorted(fingerprints)[0][:16]}` (all shards agree)"
                                  if len(fingerprints) == 1 else
                                  f"**disagrees**: {sorted(p[:12] for p in fingerprints)}"),
        f"- baseline " + (f"{sorted(baselines)[0]} tests (all shards agree)"
                          if len(baselines) == 1 else f"**disagrees**: {sorted(baselines)}"),
        "",
        "| verdict | n |",
        "|---|---|",
    ]
    for verdict in ("caught", "survived", "stale", "broken", "dirty"):
        lines.append(f"| {verdict} | {len(by_verdict.get(verdict, []))} |")
    lines.append("")

    for verdict in ("survived", "stale", "broken", "dirty"):
        rows = by_verdict.get(verdict, [])
        if not rows:
            continue
        lines += [f"### {verdict}", ""]
        for r in sorted(rows, key=lambda r: r["name"]):
            kills = "—" if r["kills"] is None else r["kills"]
            lines.append(f"- `{r['name']}` — {kills} kills, expected >= {r['min_kills']}"
                         + (f". {r['message']}" if r["message"] else ""))
        lines.append("")

    measured = {r["name"]: r["kills"] for r in results if r["kills"] is not None}
    if previous:
        rose = {n: (previous[n], k) for n, k in measured.items()
                if n in previous and k > previous[n]}
        fell = {n: (previous[n], k) for n, k in measured.items()
                if n in previous and k < previous[n]}
        fresh = sorted(n for n in measured if n not in previous)
        lines += ["### Kill counts that differ from the previous record", ""]
        if fell:
            lines += ["**Decreases — each one is a finding.** A count can only fall if a "
                      "test stopped being able to see a defect it used to see.", ""]
            for n, (was, now) in sorted(fell.items()):
                lines.append(f"- `{n}` **{was} → {now}**")
            lines.append("")
        else:
            lines += ["No decreases.", ""]
        if rose:
            lines += ["Increases:", ""]
            for n, (was, now) in sorted(rose.items()):
                lines.append(f"- `{n}` {was} → {now}")
            lines.append("")
        if fresh:
            lines += [f"First measured here ({len(fresh)}): "
                      + ", ".join(f"`{n}`" for n in fresh), ""]
        unchanged = sum(1 for n, k in measured.items()
                        if n in previous and k == previous[n])
        lines += [f"Unchanged: {unchanged}.", ""]

    lines += ["<details><summary>All measured counts</summary>", ""]
    lines += ["| mutation | kills | min_kills |", "|---|---|---|"]
    for r in sorted(results, key=lambda r: r["name"]):
        kills = "—" if r["kills"] is None else r["kills"]
        lines.append(f"| `{r['name']}` | {kills} | {r['min_kills']} |")
    lines += ["", "</details>", ""]
    return "\n".join(lines), measured


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shards", type=int, default=8,
                        help="default 8: measured 305 s per suite run at 8-way against "
                             "271 s alone on a 10-core box, and two cores left over for "
                             "whatever else is running")
    parser.add_argument("--out", type=Path, default=RECORD,
                        help="record to append to (default docs/notes/mutation-full-runs.md)")
    parser.add_argument("--counts", type=Path, default=COUNTS)
    parser.add_argument("--previous", type=Path,
                        help="JSON of {name: kills} to diff against; defaults to --counts "
                             "if it exists")
    parser.add_argument("--logs", type=Path, help="keep shard logs here instead of a tempdir")
    parser.add_argument("--dry-record", action="store_true",
                        help="print the record instead of appending it, and do not touch "
                             "the counts file. For validating the driver itself")
    parser.add_argument(
        "--smoke", metavar="N", type=int,
        help="measure only the first N mutations per shard, for exercising this driver end "
             "to end in minutes. Never a full run: the status is written SMOKE, the counts "
             "file is left alone, and the default record refuses the write — a short run in "
             "the full-run log is the exact confusion this file exists to prevent",
    )
    parser.add_argument(
        "--stagger", metavar="SECONDS", type=float, default=0.0,
        help="delay each shard's start by this much. Only reason to want it: the "
             "fingerprint refusal is about shards copying *different* trees, which can only "
             "happen inside the second or two while the copies run, and separating the "
             "windows is how that refusal gets validated instead of assumed",
    )
    args = parser.parse_args()

    harness = _harness()
    if args.smoke:
        # The registered set has to be what the shards will actually select, or the
        # partition check would report the unrun remainder as "never measured" — a true
        # statement that would drown the thing being validated.
        registered = [m.name for i in range(args.shards)
                      for m in harness.MUTATIONS[i::args.shards][:args.smoke]]
        if args.out == RECORD and not args.dry_record:
            print("--smoke refuses to write the full-run log; pass --out or --dry-record",
                  file=sys.stderr)
            return 2
    else:
        registered = [m.name for m in harness.MUTATIONS]
    print(f"{len(registered)} mutations, {args.shards} shards, "
          f"{-(-len(registered) // args.shards)} per shard at most")

    previous_path = args.previous or args.counts
    previous = load_previous(previous_path)
    print(f"previous counts: {len(previous) if previous else 0} from "
          f"{previous_path if previous else '(none)'}")

    before = git_state()
    print(f"tree at start: {before['head'][:12]}"
          f"{' DIRTY' if before['porcelain'] else ' clean'}")

    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        logdir = args.logs or Path(tmp)
        logdir.mkdir(parents=True, exist_ok=True)
        print(f"logs: {logdir}\n")
        shards = launch(args.shards, logdir, limit=args.smoke, stagger=args.stagger)
        wall = time.monotonic() - started
        after = git_state()
        broken = check(shards, registered, before, after)
        # Smoke prefixes rather than replaces: the 2026-08-20 validation runs came out
        # headed plain "INCOMPLETE" over a table of 2 mutations, and "2 of 2 measured" is
        # true of a smoke run and of a catastrophe, which is the ambiguity this file is for.
        prefix = f"SMOKE ({args.smoke} per shard) — " if args.smoke else ""
        status = prefix + ("INCOMPLETE" if broken else
                           "not a full run" if args.smoke else "full run")
        text, measured = render(status, broken, shards, registered, wall,
                                before, after, previous)

    print(text)
    if args.dry_record:
        print("(--dry-record: nothing written)")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        if not args.out.exists():
            args.out.write_text(HEADER, encoding="utf-8")
        with args.out.open("a", encoding="utf-8") as fh:
            fh.write("\n" + text)
        print(f"appended to {args.out}")
        # Counts advance only on a full run. Letting a partial one overwrite them would
        # make the next run's "unchanged" a comparison against a subset.
        if not broken and not args.smoke:
            args.counts.write_text(json.dumps({
                "date": datetime.date.today().isoformat(),
                "commit": before["head"],
                "dirty": bool(before["porcelain"]),
                "baseline_outcomes": sorted(
                    {s["record"]["baseline_outcomes"] for s in shards if s["record"]})[0],
                # The suite the counts are counts *of*. Every number here is a fraction of
                # this list, so a changed list makes them uncomparable rather than merely
                # older — which is what `test_the_full_run_covered_the_current_test_files`
                # reads it for.
                "test_files": list(harness.TEST_FILES),
                "counts": measured,
            }, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            print(f"counts updated: {args.counts}")
        else:
            print("counts NOT updated — a partial run must not become the baseline")

    if broken:
        print(f"\nINCOMPLETE: {len(broken)} invariant(s) failed")
        return 2
    label = "smoke run" if args.smoke else "full run"
    failed = [r for s in shards for r in s["record"]["results"]
              if r["verdict"] != "caught"]
    if failed:
        print(f"\n{label}, {len(failed)} not caught")
        return 1
    print(f"\n{label}, all {len(registered)} caught")
    return 0


HEADER = """# Full mutation-run log

Every run of *all* registered mutations, appended, newest at the bottom. Written by
`tests/mutations/parallel.py`, which refuses to write "full run" unless five invariants
hold — see that file's docstring for what each one catches and why.

**This file is the answer to "when was the gate last run in full".** The trigger — when a
full run is required and when an impact-scope run suffices — is in `CLAUDE.md`, because it
fires at commit time and a rule nobody consults at commit time is not a rule. Its reasoning,
and the cost measurements that force it, are in `tests/mutations/README.md`, which is also
where the per-mutation tables and the incidents behind them live. This file is dates,
totals and deltas.

An `INCOMPLETE` entry is kept rather than deleted. A refused run is evidence about the
harness, and removing it would leave the log reading as though the run never happened.
"""


if __name__ == "__main__":
    sys.exit(main())
