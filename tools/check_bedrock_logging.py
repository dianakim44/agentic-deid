#!/usr/bin/env python3
"""Check that Bedrock model-invocation logging is off, and record the result.

    python3 tools/check_bedrock_logging.py            # check and append the record
    python3 tools/check_bedrock_logging.py --status    # is today's record present?

**Why this is a gate and not a note.** If model-invocation logging is enabled, Bedrock
writes *the full prompt and completion* to an S3 bucket or a CloudWatch Logs group in the
caller's account. `rule_author.md` §1.4 puts ±120 characters of dev-fold corpus context in
every prompt, so an enabled logging destination persists credentialed note text outside the
intended location — the exposure `docs/notes/compliance.md` §1 reads clause 4 of the DUA
against. Nothing else in this repository would notice: the call succeeds, the arm produces
its rule file, and the leak is a bucket nobody opens.

**Why it is checked per run rather than once.** This is a mutable account setting, not a
property of Bedrock. Anyone holding `bedrock:PutModelInvocationLoggingConfiguration` can
turn it on between two runs, so the `None` measured on 2026-08-06 is evidence about that
day. `compliance.md` §3 already says to re-run before any arm that sends credentialed text
and to append rather than overwrite. That instruction is a rule of the kind DESIGN §5.4 is
about — obeyed by whoever remembers it — so `src/llm/bedrock.py` refuses to call until
today's record exists, and this script is what produces it.

**Read-only.** `GetModelInvocationLoggingConfiguration` is the only Bedrock API called.
Nothing is configured, changed, or deleted; a repository script that could turn logging off
could also turn it on.

The record is appended to `docs/notes/compliance.md` §3 as a dated row, because that file
is the paper's evidence and a check whose result lives only in a terminal is not evidence.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The regions a call can actually be served from, plus the ones checked because the
#: setting is per-region and a stale configuration in an unused region is still a finding.
#: The first three are the `us.anthropic.*` inference profile's members — verified by
#: `GetInferenceProfile`, not assumed from the `us.` prefix
#: (`docs/notes/baseline-model-family.md`, 2026-08-08).
REGIONS = (
    "us-east-1",
    "us-east-2",
    "us-west-2",
    "eu-west-1",
    "eu-central-1",
    "ap-northeast-2",
)

#: Where the dated rows go. §3 is the section; the marker is the line the rows follow.
COMPLIANCE = Path("docs/notes/compliance.md")
SECTION = "## 3. Bedrock model-invocation logging — measured state"
NEXT_SECTION = "## 4."

#: The heading each run appends under, so a reader can tell one run's table from another's.
RUN_HEADING = "**Gate check {date}** (`tools/check_bedrock_logging.py`):"

#: What a clean region looks like. Bedrock returns no `loggingConfig` key at all when no
#: destination is configured, which is why absence rather than a falsy value is the test.
CLEAN = "none"


class LoggingCheckError(Exception):
    """The check could not be completed, or it found logging enabled.

    Not a subclass of `CorpusError`: this module deliberately imports nothing from `src/`
    so that the gate cannot be satisfied by a stub added to the package it guards.
    """


def today() -> str:
    """The date the record is stamped with, in the format §3's rows already use."""
    return _dt.date.today().isoformat()


def check_region(region: str) -> tuple[str, str]:
    """Return `(region, state)` where state is `none` or a description of the destination.

    `boto3` is imported here rather than at module scope so that `--status` works on a
    machine without it. The status query reads a file; only the check needs a client.
    """
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    client = boto3.client("bedrock", region_name=region)
    try:
        response = client.get_model_invocation_logging_configuration()
    except (ClientError, BotoCoreError) as exc:
        # An access denial is not a clean result. A caller who cannot read the setting
        # cannot report that it is off, and reporting "unknown" as "none" is the failure
        # this whole file exists to prevent.
        raise LoggingCheckError(
            f"could not read the logging configuration in {region}: "
            f"{type(exc).__name__}. The gate cannot pass on an unreadable setting — "
            "an unknown state is not an absent one."
        ) from exc

    config = response.get("loggingConfig")
    if not config:
        return region, CLEAN

    # Enabled. Name the destinations rather than just failing, because the person reading
    # this has to go and find them. No prompt text is involved in any of these fields.
    destinations = [key for key in sorted(config) if key.endswith("Config")]
    flags = [key for key in ("textDataDeliveryEnabled", "imageDataDeliveryEnabled")
             if config.get(key)]
    return region, "enabled: " + ", ".join(destinations + flags)


def check_all() -> list[tuple[str, str]]:
    """Every region in `REGIONS`, in order. Raises on the first unreadable one."""
    return [check_region(region) for region in REGIONS]


def render(results: list[tuple[str, str]], date: str) -> str:
    """The block appended to §3. A table, matching the rows already there."""
    lines = [
        "",
        RUN_HEADING.format(date=date),
        "",
        "| region | `loggingConfig` |",
        "|---|---|",
    ]
    for region, state in results:
        cell = "`None`" if state == CLEAN else f"**{state}**"
        lines.append(f"| {region} | {cell} |")
    lines.append("")
    return "\n".join(lines)


def section_bounds(text: str) -> tuple[int, int]:
    """Character offsets of §3's body, so the append lands inside it and not at EOF.

    A record appended to the end of the file would still satisfy a naive date search while
    sitting under §5, which is the "the record exists and says nothing" shape §5.4 is
    written against.
    """
    start = text.find(SECTION)
    if start < 0:
        raise LoggingCheckError(
            f"{COMPLIANCE} has no section {SECTION!r}. The record has nowhere to go, and "
            "appending it at the end of the file would put a logging measurement under "
            "whatever section happens to be last."
        )
    end = text.find(NEXT_SECTION, start)
    if end < 0:
        raise LoggingCheckError(
            f"{COMPLIANCE} has section {SECTION!r} but nothing after it; the bounds of the "
            "section cannot be determined."
        )
    return start, end


def recorded_dates() -> list[str]:
    """Every date §3 carries a gate-check record for. Empty if the file has none.

    Deliberately scoped to §3's body. A date elsewhere in the file — §2 quotes guidance
    with dates in it — is not a logging measurement.
    """
    path = ROOT / COMPLIANCE
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    try:
        start, end = section_bounds(text)
    except LoggingCheckError:
        return []
    body = text[start:end]
    pattern = re.escape(RUN_HEADING.split("{date}")[0]) + r"(\d{4}-\d{2}-\d{2})"
    return re.findall(pattern, body)


def checked_today() -> bool:
    """Whether §3 records a gate check for today. This is what the client's gate calls."""
    return today() in recorded_dates()


def append_record(results: list[tuple[str, str]], date: str) -> None:
    """Insert the rendered block at the end of §3's body."""
    path = ROOT / COMPLIANCE
    text = path.read_text(encoding="utf-8")
    _, end = section_bounds(text)
    block = render(results, date)
    path.write_text(text[:end] + block.lstrip("\n") + "\n" + text[end:], encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--status", action="store_true",
        help="report whether today's record exists; make no API call and write nothing",
    )
    args = parser.parse_args(argv)

    if args.status:
        dates = recorded_dates()
        if checked_today():
            print(f"ok: {COMPLIANCE} §3 records a gate check for {today()}")
            return 0
        print(f"missing: no gate check recorded for {today()}")
        print(f"  dates on record: {', '.join(dates) if dates else '(none)'}")
        print("  run: python3 tools/check_bedrock_logging.py")
        return 1

    try:
        results = check_all()
    except LoggingCheckError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 2

    enabled = [(region, state) for region, state in results if state != CLEAN]
    for region, state in results:
        print(f"  {region:<16} {state}")

    if enabled:
        # Nothing is written. A record of an enabled state would let a later reader see a
        # dated row and stop there, and the gate keys on the date.
        print(
            "\nBLOCKED: model-invocation logging is enabled in "
            f"{', '.join(r for r, _ in enabled)}.\n"
            "Bedrock is writing full prompts and completions to a destination in this "
            "account. Do not run any arm that sends corpus context until it is off "
            "(docs/notes/compliance.md §1, §3). No record was appended.",
            file=sys.stderr,
        )
        return 1

    if checked_today():
        print(f"\nalready recorded for {today()}; nothing appended")
        return 0

    append_record(results, today())
    print(f"\nrecorded in {COMPLIANCE} §3 for {today()} — all {len(results)} regions clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
