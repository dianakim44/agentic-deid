#!/usr/bin/env python3
"""Measure how much one model's rule file varies across identical calls. A probe, not a gate.

    python3 tools/probe_call_variance.py --corpus es-meddocan --lang es \\
        --model-id us.anthropic.claude-opus-4-5-20251101-v1:0
    python3 tools/probe_call_variance.py ... --dry-run   # print the plan, call nothing

**This blocks nothing and makes no arm.** Its status is `tools/probe_prompt_cache.py`'s, and
that comparison is the whole of the argument: nothing in `src/` imports this module, no arm
runs it, no result path reads it, and a failed run leaves the repository exactly as capable as
a successful one. What it produces is a paragraph in `docs/notes/call-variance.md` and a
sentence a report may cite. It does not choose δ, does not touch δ, and cannot — δ, k and the
ceiling are pre-registered (DESIGN §3) and this file is downstream of them by construction.

**What it measures.** `port-loop` round 1 sends a prompt that is byte-identical to
`port-oneshot-nofence`'s, and the two produced different rule files: 27 rules against 31, 14
`rule_id`s in common, leak-rate `fully_covered` 0.560 against 0.596. That difference of 0.0361
is the *only* figure anyone has for how far this instrument moves when the input does not move
at all — one pair, two draws. A round-to-round improvement smaller than that is not
distinguishable from the model having answered the same question twice. So: send the round-1
prompt n times, and report the spread of the rule sets it comes back with.

**Why the prompt is asserted rather than assembled and trusted.** The measurement is only about
call variance if the prompt is the *same* prompt, so the assembled text's `text_sha256` is
compared against `--expect-prompt-sha256` — or, by default, against the round-1 `rule_author`
line in the arm's own `agent_calls.jsonl` — and a mismatch refuses the run. Two things could
make it drift without anyone noticing: `docs/prompts/rule_author.md` is a window file and a
widening would change it, and `_current_rules()` reads the arm's rule file, so running this
after round 2 has written one would silently measure a *different* call. Refusing is cheap and
a mismatched probe would be a number in a note that nobody could tell was wrong.

**It does not score, and that omission is a rule rather than a shortcut** — DESIGN §3, the
"why this probe does not score" clause. Scoring five draws on dev would give the leak-rate
spread directly, which is the figure a reader actually wants, and it is still not done: five
dev scores in a note become a channel through which "which prompt does better on dev" reaches
the next prompt, through a person rather than through code. That is dev overfitting, not a
sealed violation, and it is the same shape as §6's ban on choosing a dev checkpoint with test
numbers. The rule-set spread measured here is a lower bound on what varies and it is honest
about being one; the leak-rate spread stays **unmeasured** rather than inferred from it, and a
future run that needs it needs a DESIGN clause permitting a non-arm run to score dev first.

**Nothing here touches `results/`.** Each draw is written into a `TemporaryDirectory` and
loaded from there, so the arm's rule files, metrics and call log are not read for anything but
the expected prompt hash and are never written. The arm's window is not frozen, `agent_calls.jsonl`
is not appended to, and `metrics.json` is not produced — a probe that left a round's artefacts
behind would be an arm whatever its docstring said.

**A draw that will not load is variance and not an abort.** `_write_rules()` writes the model's
output verbatim, so a fenced or truncated reply is a real outcome of a real call; recorded as a
format failure with the validator's exception *type* and no text, and the run continues. The
alternative — stop on the first bad draw — would report the spread of the draws that happened
to parse, which is a biased sample of exactly the thing being measured.

**No prompt text and no completion text leaves this process.** Draws are summarised by rule
count, `rule_id` set, layer and `phi_type` distributions, completion tokens and the response's
sha256. `rule_id`s are agent-authored identifiers that `tools/release_screen.py` screens, so
they are publishable in the way a `layer` is; a matcher is not and is never read out of the
loaded rule. That is CLAUDE.md's rule about corpus text in logs applied to the one place a
probe would be tempted to relax it — "these are only rule names" is the reasoning, and the
screener rather than this file is what makes it true.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import itertools
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: Where the measurement is recorded. A probe whose result lives in a terminal is not a
#: measurement anyone can cite (`check_bedrock_logging.py`'s reason).
NOTE = Path("docs/notes/call-variance.md")

#: The arm whose round-1 call this probe reproduces, for the default expected prompt hash.
#: Named here rather than derived from the axes so that pointing the probe at a different
#: arm is an argument change and not an inference.
DEFAULT_ARM = ("R", "sup-free", "port-loop")

#: The call-log role whose line carries the round-1 prompt reference.
RULE_AUTHOR_ROLE = "rule_author"

#: Draws. Five because the figure wanted is a spread rather than a mean, and the pair that
#: exists already (`port-oneshot-nofence` against round 1) is n=2 — five gives ten pairs,
#: which is enough for a range to mean something and cheap enough to be a probe.
DEFAULT_CALLS = 5


class ProbeError(Exception):
    """The probe could not be completed. Nothing depends on this, by design."""


def today() -> str:
    return _dt.date.today().isoformat()


def digest(text: str) -> str:
    """The same form `prompt._digest()` writes, so the two are comparable by eye."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def expected_prompt_sha256(corpus: str, *, arm: tuple[str, str, str] = DEFAULT_ARM) -> str:
    """The `text_sha256` the arm's round-1 RuleAuthor call recorded.

    Read from `agent_calls.jsonl` rather than from a constant in this file. A constant would
    be a second place the hash lives, and the failure it invites is the quiet one: the arm's
    prompt moves, the constant does not, and the probe reports the variance of a call the arm
    never made while asserting that it did not.
    """
    log = ROOT / "results" / corpus / Path(*arm) / "agent_calls.jsonl"
    if not log.exists():
        raise ProbeError(
            f"{log.relative_to(ROOT)} does not exist, so there is no recorded round-1 prompt "
            "to compare against. Pass --expect-prompt-sha256 to state it explicitly."
        )
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("iteration") == 1 and record.get("role") == RULE_AUTHOR_ROLE:
            sha = (record.get("prompt_reference") or {}).get("text_sha256")
            if not sha:
                raise ProbeError(
                    "the round-1 RuleAuthor line carries no prompt_reference.text_sha256, "
                    "so it cannot say which prompt ran."
                )
            return sha
    raise ProbeError(
        f"no iteration-1 {RULE_AUTHOR_ROLE} line in {log.relative_to(ROOT)}. Round 1 has not "
        "run for this arm, or ran without recording its call."
    )


def draw_summary(index: int, response, *, lang: str, workdir: Path) -> dict:
    """One draw reduced to what may be published: counts, ids, distributions, a hash.

    The response text is written into `workdir` and loaded through `rules.load_rules()` — the
    same validator the arm uses, so a draw this probe calls loadable is one the arm would have
    scored. `workdir` is a temporary directory and the arm's `results/` tree is not involved.

    A `RuleError` is caught and its **type** recorded. Not its message: `load_rules()` is
    careful to keep the offending line out of what it raises, and a probe that printed the
    message anyway would be relying on that care rather than adding to it.
    """
    from src import rules as rules_module

    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / f"{lang}.yaml"
    path.write_text(response.text, encoding="utf-8")

    row = {
        "draw": index,
        "response_chars": len(response.text),
        "response_sha256": digest(response.text),
        "completion_tokens": response.completion_tokens,
        "prompt_tokens": response.prompt_tokens,
        "wall_seconds": round(response.wall_seconds, 3),
        "stop_reason": response.stop_reason,
    }
    try:
        ruleset = rules_module.load_rules(lang, path=path)
    except rules_module.RuleError as exc:
        row.update({
            "outcome": "format_failure",
            "error_type": type(exc).__name__,
            "rules": None,
            "rule_ids": [],
            "layers": {},
            "phi_types": {},
        })
        return row
    row.update({
        "outcome": "loaded",
        "error_type": None,
        "rules": len(ruleset.rules),
        "rule_ids": sorted(rule.rule_id for rule in ruleset.rules),
        "layers": dict(sorted(Counter(r.layer for r in ruleset.rules).items())),
        "phi_types": dict(sorted(Counter(r.phi_type for r in ruleset.rules).items())),
    })
    return row


def jaccard(left: set[str], right: set[str]) -> float:
    """Overlap of two `rule_id` sets. An empty pair is 1.0 — two identical nothings agree."""
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def ranges(loaded: list[dict], key: str) -> dict[str, list[int]]:
    """Per-value min and max of one distribution across the draws, **including the zeros**.

    Ranging only over the draws that mention a value would report `gazetteer: 1–1` for a layer
    three of five draws omitted entirely — a stable layer, from the data that most strongly says
    otherwise. So the value set is collected first and every draw contributes a count for every
    member of it.

    `phi_types` is ranged as well as `layers`, and that is the name-free half of the
    measurement. A `rule_id` set counts a rename as a disagreement, so its Jaccard overstates
    how much the model's *behaviour* moves; a count of rules per PHI type does not depend on
    what any of them was called. Added 2026-08-21, after the first run's numbers made the
    distinction the finding rather than a caveat — that run's table was computed by hand from
    the recorded draws and `docs/notes/call-variance.md` says so.
    """
    members = sorted({name for row in loaded for name in row[key]})
    return {
        name: [min(row[key].get(name, 0) for row in loaded),
               max(row[key].get(name, 0) for row in loaded)]
        for name in members
    }


def spread(rows: list[dict]) -> dict:
    """What the draws say about each other. Derived here so the note is not doing arithmetic.

    Only loaded draws enter the set statistics — a format failure has no rule set to compare —
    and the count of them is reported beside, because "three of five agreed closely" and "five
    of five did" are different results and dropping the denominator hides which one happened.
    """
    loaded = [r for r in rows if r["outcome"] == "loaded"]
    sets = [set(r["rule_ids"]) for r in loaded]
    counts = [r["rules"] for r in loaded]
    pairs = [
        {"a": loaded[i]["draw"], "b": loaded[j]["draw"],
         "jaccard": round(jaccard(sets[i], sets[j]), 4),
         "shared": len(sets[i] & sets[j]),
         "only_a": len(sets[i] - sets[j]),
         "only_b": len(sets[j] - sets[i])}
        for i, j in itertools.combinations(range(len(loaded)), 2)
    ]
    seen: Counter[str] = Counter()
    for one in sets:
        seen.update(one)
    return {
        "draws": len(rows),
        "loaded": len(loaded),
        "format_failures": len(rows) - len(loaded),
        "rule_count_min": min(counts) if counts else None,
        "rule_count_max": max(counts) if counts else None,
        "distinct_rule_ids": len(seen),
        "in_every_draw": sum(1 for n in seen.values() if n == len(loaded)) if loaded else 0,
        "in_one_draw_only": sum(1 for n in seen.values() if n == 1),
        "jaccard_min": round(min((p["jaccard"] for p in pairs), default=1.0), 4),
        "jaccard_max": round(max((p["jaccard"] for p in pairs), default=1.0), 4),
        "jaccard_mean": (round(sum(p["jaccard"] for p in pairs) / len(pairs), 4)
                         if pairs else None),
        "layer_ranges": ranges(loaded, "layers"),
        "phi_type_ranges": ranges(loaded, "phi_types"),
        "pairs": pairs,
    }


def plan(*, corpus: str, lang: str, model_id: str, calls: int, expected: str,
         prompt_chars: int, actual: str) -> list[str]:
    """What the run will do, for `--dry-run`. Everything here is local except the hash check."""
    return [
        f"model_id       {model_id}",
        f"corpus / lang  {corpus} / {lang}",
        f"calls          {calls}  (uncached, one content block, the arm's round-1 path)",
        f"prompt         {prompt_chars} chars, {actual}",
        f"expected       {expected}",
        f"prompt match   {'yes' if actual == expected else 'NO — the run will refuse'}",
        "scoring        none (DESIGN §3: this probe does not score dev)",
        "writes         a temporary directory per draw; results/ is not touched",
        "gate           bedrock._require_logging_check() before the first call",
        f"appends to     {NOTE}",
    ]


def render(rows: list[dict], summary: dict, *, corpus: str, lang: str, model_id: str,
           prompt_sha256: str, prompt_chars: int, date: str) -> str:
    """The block appended to the note. Tables first, then the rows, then no conclusion.

    Deliberately no verdict sentence: what the numbers license is an interpretation rule that
    lives in DESIGN §3, and a note that also announced the conclusion would be the second place
    it lived.
    """
    lines = [
        "",
        f"## 같은 프롬프트를 {summary['draws']}회 호출했을 때의 규칙 집합 변동 — 측정 ({date})",
        "",
        f"`tools/probe_call_variance.py`, `{model_id}`, {corpus} / {lang}. "
        f"프롬프트 {prompt_chars} chars, `{prompt_sha256}` — 회차 1 이 보낸 것과 바이트 동일. "
        "채점하지 않는다 (DESIGN §3).",
        "",
        "| draw | outcome | rules | completion tokens | wall s | response |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        rules = row["rules"] if row["rules"] is not None else f"— ({row['error_type']})"
        lines.append(
            f"| {row['draw']} | {row['outcome']} | {rules} | {row['completion_tokens']} "
            f"| {row['wall_seconds']} | `{row['response_sha256'][7:19]}` |"
        )
    lines += [
        "",
        f"규칙 수 {summary['rule_count_min']}–{summary['rule_count_max']}, "
        f"서로 다른 rule_id {summary['distinct_rule_ids']}개 중 "
        f"모든 draw 에 등장 {summary['in_every_draw']}개 · 한 draw 에만 등장 "
        f"{summary['in_one_draw_only']}개. "
        f"쌍별 Jaccard {summary['jaccard_min']}–{summary['jaccard_max']} "
        f"(평균 {summary['jaccard_mean']}, {len(summary['pairs'])}쌍). "
        f"형식 실패 {summary['format_failures']}/{summary['draws']}.",
        "",
        "**위 Jaccard 는 `rule_id` 집합에 대한 값이고 행동 지표가 아니다** — 같은 규칙을 "
        "다른 이름으로 쓴 것이 불일치로 계산된다. 이름을 쓰지 않는 두 분포가 아래에 있고, "
        "그쪽이 모델이 실제로 얼마나 움직였는지에 더 가깝다.",
        "",
        "층별 draw 간 범위 (등장하지 않은 draw 는 0 으로 센다):",
        "",
        "| layer | min | max |",
        "|---|---|---|",
    ]
    for layer, (low, high) in summary["layer_ranges"].items():
        lines.append(f"| `{layer}` | {low} | {high} |")
    lines += [
        "",
        "유형별 draw 간 범위 (같은 규칙, 다른 이름에 영향받지 않는다):",
        "",
        "| phi_type | min | max |",
        "|---|---|---|",
    ]
    for phi_type, (low, high) in summary["phi_type_ranges"].items():
        lines.append(f"| `{phi_type}` | {low} | {high} |")
    lines += [
        "",
        "<details><summary>draw 별 기록과 쌍별 비교</summary>",
        "",
        "```json",
        json.dumps({"draws": rows, "spread": summary}, ensure_ascii=False, indent=1),
        "```",
        "",
        "</details>",
        "",
    ]
    return "\n".join(lines)


def append_to_note(block: str, note: Path) -> None:
    """Append at end of file, `probe_prompt_cache.py`'s reason: a chronological series."""
    with open(note, "a", encoding="utf-8") as handle:
        handle.write(block)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure rule-set variance across identical calls. Blocks nothing.")
    parser.add_argument("--corpus", required=True, help="corpus id from config/naming.yaml")
    parser.add_argument("--lang", required=True, help="language id from config/naming.yaml")
    parser.add_argument("--model-id", required=True,
                        help="the Bedrock id to call; no default, for invoke()'s reason")
    parser.add_argument("--calls", type=int, default=DEFAULT_CALLS,
                        help=f"draws (default {DEFAULT_CALLS})")
    parser.add_argument("--expect-prompt-sha256", default=None,
                        help="the prompt hash to require; default reads the arm's round-1 line")
    parser.add_argument("--max-tokens", type=int, default=None,
                        help="output budget; default is bedrock.DEFAULT_MAX_TOKENS, the arm's")
    parser.add_argument("--region", default=None, help="AWS region (default: environment)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and call nothing")
    args = parser.parse_args(argv)

    if args.calls < 2:
        raise ProbeError(
            f"--calls is {args.calls}; a spread needs at least two draws. One draw is what "
            "every arm already records."
        )

    from src.llm import bedrock, prompt as prompt_module

    filled = prompt_module.assemble_task_prompt(lang=args.lang, corpus=args.corpus)
    reference = filled.reference()
    actual = reference["text_sha256"]
    expected = args.expect_prompt_sha256 or expected_prompt_sha256(args.corpus)

    for line in plan(corpus=args.corpus, lang=args.lang, model_id=args.model_id,
                     calls=args.calls, expected=expected,
                     prompt_chars=reference["text_chars"], actual=actual):
        print(line)
    print()
    if args.dry_run:
        print("dry run: no call made, nothing appended")
        return 0
    if actual != expected:
        raise ProbeError(
            f"the assembled prompt is {actual} and round 1 sent {expected}. This probe "
            "measures variance at a fixed input, so a different prompt makes the measurement "
            "about something else. A window file moved, or the arm now has a rule file that "
            "§1.2 is reading."
        )

    bedrock._require_logging_check()
    invoke_kwargs = {"model_id": args.model_id, "region": args.region}
    if args.max_tokens is not None:
        invoke_kwargs["max_tokens"] = args.max_tokens

    rows = []
    with tempfile.TemporaryDirectory(prefix="probe-call-variance-") as tmp:
        workdir = Path(tmp)
        for index in range(1, args.calls + 1):
            response = bedrock.invoke(filled, **invoke_kwargs)
            row = draw_summary(index, response, lang=args.lang, workdir=workdir / str(index))
            rows.append(row)
            print(f"draw {index}  {row['outcome']:14} rules={row['rules']}  "
                  f"completion={row['completion_tokens']}  "
                  f"{row['response_sha256'][7:19]}")

    summary = spread(rows)
    print()
    print(json.dumps({k: v for k, v in summary.items() if k != "pairs"},
                     ensure_ascii=False))
    block = render(rows, summary, corpus=args.corpus, lang=args.lang,
                   model_id=args.model_id, prompt_sha256=actual,
                   prompt_chars=reference["text_chars"], date=today())
    append_to_note(block, ROOT / NOTE)
    print(f"\nappended to {NOTE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
