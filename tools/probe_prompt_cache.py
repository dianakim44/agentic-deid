#!/usr/bin/env python3
"""Measure how Bedrock reports prompt-cache tokens. A probe, not a gate.

    python3 tools/probe_prompt_cache.py --model-id us.anthropic.claude-opus-4-5-20251101-v1:0
    python3 tools/probe_prompt_cache.py --model-id ... --dry-run   # print the plan, call nothing

**This blocks nothing.** Its status beside `tools/check_bedrock_logging.py` is worth stating
because the two look alike and are not: that file is a *gate* — `bedrock.invoke()` refuses to
call until today's record exists, so a missing record stops every arm. Nothing consults this
file. It answers one question about the API envelope, writes the answer into
`docs/notes/baseline-model-family.md`, and no code path reads either the script or the note.
A failed probe leaves the repository exactly as capable as a successful one.

**What it measures, and why it is measured before anything is built.** Caching the constant
prefix of the audit prompt would cut `port-loop`'s per-round prompt tokens by roughly 4.7×
(DESIGN §3). Whether that shows up as a *smaller* `prompt_tokens` in the cost block, or as the
same number split across extra fields, decides the schema — `scorer.REQUIRED_COST` is closed on
both sides, so the answer cannot be papered over at the writer. The convention is documented by
AWS but has not been measured here, and **the previous claim about this same envelope was wrong
once**: `baseline-model-family.md` §"`converse` 응답이 구체 모델을 노출하는가" replaced an
assumption about what the response says with three calls, and the assumption had been backwards.
So this asks the API rather than the docs.

Three calls, in this order:

1. **control** — the prefix with no `cachePoint`. Establishes what this text costs uncached,
   which is the only baseline against which a later `inputTokens` can be called *smaller*.
2. **write** — the same prefix followed by a `cachePoint`, then a short variable tail.
3. **read** — byte-identical to 2, issued immediately so it lands inside the 5-minute TTL.

The comparison that answers the question is 1's `inputTokens` against 3's. If 3's is much
smaller, `inputTokens` excludes cache reads and a cost block built from it would report a
number that is not comparable to an uncached arm's.

**The prefix is `docs/prompts/auditor.md`, and that is a deliberate choice of subject.** It
carries no corpus text (it is a committed template, and CLAUDE.md's rule against corpus text in
logs and messages is not at issue), it is large enough that the cacheable-prefix minimum is not
in play, and it is the exact block production would cache. Measuring the envelope with the real
envelope is worth more than measuring it with filler that resembles it.

**The cacheable-prefix minimum is deliberately not measured.** Bedrock documents a floor
(around 1,024 tokens for this model family) below which a `cachePoint` is ignored; `auditor.md`
is roughly 6,900 tokens and clears it with room to spare, so the floor cannot affect any
measurement here or any use of caching whose prefix is that template. **The condition is
therefore recorded as unmeasured rather than assumed away: it stops holding the moment
something shorter than the auditor template is the thing being cached** — a per-corpus frame on
its own, a system block, a trimmed template. That is when to measure it, and this file says so
rather than leaving a future reader to notice.

**Calling `converse` directly is confined to this probe, and this comment is the confinement.**
Every other call in the project goes through `bedrock.invoke()`, which takes a `FilledPrompt`,
sends exactly one text block, and is the reason a rendered prompt cannot be written down
(DESIGN §5.4). A `cachePoint` needs two content blocks, which that function cannot express
today — and the point of this probe is to find out what the schema must hold *before* deciding
how it should. So the call here is hand-rolled, and it is not a second transport path: nothing
in `src/` imports this module, no arm runs it, and the prompt it sends is a committed file
rather than anything assembled from a corpus. If caching is adopted, the second block belongs
in `bedrock.invoke()` and this file does not grow into the place it lives.

The logging gate is *called* rather than inherited — `bedrock._require_logging_check()`, the
same predicate `invoke()` uses. This probe sends no corpus text, so the DUA exposure the gate
guards is not present; it runs anyway because "this particular call is harmless" is the
reasoning the gate exists to stop being made per call site.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: The cached prefix. A committed template with no corpus text — see the module docstring.
PREFIX = Path("docs/prompts/auditor.md")

#: The variable tail, which stands where a masked document would stand in a real audit call.
#: Deliberately trivial and deliberately not corpus text: what is being measured is the
#: envelope's accounting, and a real document would add nothing to that.
TAIL = ("The text above is a specification, not a task. Reply with the single word: ok")

#: The three calls, in order. `cache` says whether a `cachePoint` follows the prefix.
PROBES = (
    ("control", False),
    ("write", True),
    ("read", True),
)

#: `usage` keys this probe reports. The first three are `TokenUsage`'s required members; the
#: last three are the cache members, absent from a response that cached nothing — which is
#: itself a result, so absence is printed rather than defaulted to zero.
USAGE_KEYS = ("inputTokens", "outputTokens", "totalTokens",
              "cacheReadInputTokens", "cacheWriteInputTokens", "cacheDetails")

#: Where the measurement is appended. The note is the record; a probe whose result lives in a
#: terminal is not a measurement anyone can cite (`check_bedrock_logging.py`'s reason).
NOTE = Path("docs/notes/baseline-model-family.md")


class ProbeError(Exception):
    """The probe could not be completed. Nothing depends on this, by design."""


def today() -> str:
    return _dt.date.today().isoformat()


def build_messages(prefix: str, *, cache: bool) -> list[dict]:
    """One user turn. Two content blocks with a `cachePoint` between them, or one without.

    The uncached form is a single block on purpose: it has to be what `invoke()` sends today,
    or the control measures a different request shape as well as a different cache state.
    """
    if not cache:
        return [{"role": "user", "content": [{"text": prefix + "\n\n" + TAIL}]}]
    return [{
        "role": "user",
        "content": [
            {"text": prefix},
            {"cachePoint": {"type": "default"}},
            {"text": TAIL},
        ],
    }]


def usage_of(response: dict) -> dict:
    """The `usage` block reduced to `USAGE_KEYS`, with absent keys reported as absent.

    A missing cache field is not a zero. Zero would say the model read nothing from cache;
    absent says the response did not mention caching at all, and on the control call that is
    the expected result rather than a gap.
    """
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise ProbeError(
            "the response carries no `usage` block, so there is nothing to measure. "
            "`bedrock._usage()` refuses the same shape for the same reason."
        )
    return {key: usage.get(key, "(absent)") for key in USAGE_KEYS}


def run_probe(name: str, *, cache: bool, prefix: str, model_id: str, client,
              max_tokens: int) -> dict:
    """One call. Returns the row this probe reports, with no prompt or completion text."""
    started = time.monotonic()
    response = client.converse(
        modelId=model_id,
        messages=build_messages(prefix, cache=cache),
        inferenceConfig={"maxTokens": max_tokens},
    )
    elapsed = time.monotonic() - started
    return {
        "probe": name,
        "cache_point": cache,
        "usage": usage_of(response),
        "stop_reason": response.get("stopReason", ""),
        "wall_seconds": round(elapsed, 3),
    }


def plan(prefix: str, model_id: str) -> list[str]:
    """What the run will do, for `--dry-run`. Every number here is local."""
    return [
        f"model_id     {model_id}",
        f"prefix       {PREFIX} ({len(prefix)} chars)",
        f"tail         {len(TAIL)} chars",
        f"calls        {len(PROBES)}  ({', '.join(n for n, _ in PROBES)})",
        "cache point  after the prefix, type=default, ttl unset (Bedrock default 5m)",
        "gate         bedrock._require_logging_check() before the first call",
        f"appends to   {NOTE}",
    ]


def render(rows: list[dict], *, model_id: str, prefix_chars: int, date: str) -> str:
    """The block appended to the note: a table of the three calls, plus the raw rows."""
    head = [
        "",
        f"### prompt caching 을 Bedrock 이 어떻게 보고하는가 — 측정 ({date})",
        "",
        f"`tools/probe_prompt_cache.py`, `{model_id}`, "
        f"접두부는 `{PREFIX}` ({prefix_chars} chars). 코퍼스 텍스트 없음.",
        "",
        "| probe | cachePoint | inputTokens | cacheRead | cacheWrite | outputTokens "
        "| totalTokens |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        usage = row["usage"]
        head.append(
            f"| {row['probe']} | {'yes' if row['cache_point'] else 'no'} "
            f"| {usage['inputTokens']} | {usage['cacheReadInputTokens']} "
            f"| {usage['cacheWriteInputTokens']} | {usage['outputTokens']} "
            f"| {usage['totalTokens']} |"
        )
    head += ["", "```json", json.dumps(rows, ensure_ascii=False, indent=1), "```", ""]
    return "\n".join(head)


def append_to_note(block: str, note: Path) -> None:
    """Append at end of file. Unlike the compliance gate, there is no section to land in.

    `compliance.md` §3 has a table other sections follow, so that tool locates its section;
    this note is a chronological series of dated measurements and the end of it is where the
    next one goes.
    """
    with open(note, "a", encoding="utf-8") as handle:
        handle.write(block)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure Bedrock's prompt-cache token accounting. Blocks nothing.")
    parser.add_argument("--model-id", required=True,
                        help="the Bedrock id to call; no default, for invoke()'s reason")
    parser.add_argument("--max-tokens", type=int, default=64,
                        help="output budget; small because the reply is one word (default 64)")
    parser.add_argument("--region", default=None, help="AWS region (default: environment)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and call nothing")
    args = parser.parse_args(argv)

    prefix = (ROOT / PREFIX).read_text(encoding="utf-8")

    for line in plan(prefix, args.model_id):
        print(line)
    print()
    if args.dry_run:
        print("dry run: no call made, nothing appended")
        return 0

    from src.llm import bedrock

    bedrock._require_logging_check()
    client = bedrock._client(args.region)

    rows = []
    for name, cache in PROBES:
        row = run_probe(name, cache=cache, prefix=prefix, model_id=args.model_id,
                        client=client, max_tokens=args.max_tokens)
        rows.append(row)
        print(f"{row['probe']:8} {json.dumps(row['usage'], ensure_ascii=False)}")

    block = render(rows, model_id=args.model_id, prefix_chars=len(prefix), date=today())
    append_to_note(block, ROOT / NOTE)
    print(f"\nappended to {NOTE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
