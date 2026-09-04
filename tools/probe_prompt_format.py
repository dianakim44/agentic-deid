#!/usr/bin/env python3
"""Ask whether a de-fenced prompt still produces a loadable artefact. A probe, not a gate.

    python3 tools/probe_prompt_format.py --model-id us.anthropic.claude-opus-4-5-20251101-v1:0
    python3 tools/probe_prompt_format.py --model-id ... --dry-run   # print the plan, call nothing
    python3 tools/probe_prompt_format.py --model-id ... --only auditor

**Why this exists.** `rule_author.md` and `auditor.md` each carried a fenced example of the
artefact they ask for, and both have a *measured pass record*: eight loadable
`rules/iter*/es.yaml` from `port-loop` plus `port-oneshot-nofence`, and parseable
`audit_report.json` for iterations 2–8. `docs/notes/call-variance.md` adds five more draws on
the RuleAuthor prompt with format failures 0/5. Removing the examples — which the fence rule in
`tests/test_prompt.py` requires, with no per-file exemption — trades that record for a prompt
nothing has ever been measured on. This probe is the measurement, taken before the trade is
committed rather than discovered in an arm.

**This blocks nothing** and its status beside `tools/check_bedrock_logging.py` is worth stating
because the two look alike and are not: that file is a *gate* — `bedrock.invoke()` refuses to
call until today's record exists. Nothing consults this file. Nothing in `src/` imports it, no
arm runs it, no result directory is created, and a failed probe leaves the repository exactly as
capable as a successful one. Its output is a dated block in `docs/notes/prompt-format-probe.md`.

**This is not a second transmission path.** The concern is the one
`tools/probe_prompt_cache.py` states about its hand-rolled `converse` call, and the answer here
is stronger than that file's: this probe does not hand-roll anything. It builds its prompts with
`assemble_task_prompt` and `assemble_audit_prompt` — the same functions the arms call, returning
the same `FilledPrompt` type — and sends them through `bedrock.invoke()`, the one entry point in
the project, which calls `for_transport()` once and is the reason a rendered prompt cannot be
written down (DESIGN §5.4). So there is no second path: there is one path, and this file is
another caller of it. Three consequences, made true here rather than asserted:

- **No prompt text is printed, logged, or written.** The plan and the note carry the reference
  form — char counts, `text_sha256`, section lists — which is what `FilledPrompt.reference()`
  returns and all it returns.
- **No response text is printed, logged, or written**, and this is the sharper half. The
  RuleAuthor's response is a rule file whose `comment` fields an agent may have filled with
  corpus surface forms (`rule_author.md` Prohibition 2), and the Auditor's is a list of
  positions of *surviving* identifiers in a DUA fold — `auditor.md` §2.2 deny-lists the file
  those go into. So the responses are hashed and counted and then dropped, and a refusal is
  recorded by its reason name.
- **The corpus text this probe sends is none.** The RuleAuthor call is round 1's, whose §§1.3–1.4
  are empty by the arm's definition, so it carries no dev text at all. The Auditor call needs a
  masked document, and the one it sends is **invented in this file** — see `_document()`. The
  gate is called anyway (`bedrock._require_logging_check()`, the same predicate `invoke()` uses),
  because "this particular call is harmless" is the reasoning the gate exists to stop being made
  per call site.

**One call per prompt, and there is no retry.** `RETRY_POLICY` below is the reasoning, and it is
load-bearing rather than a comment: this file contains no loop over attempts and
`bedrock.MAX_ATTEMPTS == 1`, so a throttle is fatal and visible rather than smoothed away.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora.base import Document, Span                      # noqa: E402
from src.llm.prompt import (                                     # noqa: E402
    assemble_audit_prompt, assemble_task_prompt, mask_document,
)
from src.porting.audit import parse_response                     # noqa: E402
from src.rules import load_rules                                 # noqa: E402

#: Where the measurement is appended. A probe whose result lives in a terminal is not a
#: measurement anyone can cite — `probe_prompt_cache.py`'s reason for having a note at all.
NOTE = Path("docs/notes/prompt-format-probe.md")

#: The corpus and language the probe runs for. `es-meddocan` / `es` because that is the pair
#: every measured pass record was taken on, and the question is whether the *prompt* changed
#: the outcome: a different corpus would change the frame as well and the comparison would be
#: to nothing.
CORPUS, LANG = "es-meddocan", "es"

#: **One call per prompt, no retry, and this constant is the reasoning rather than a knob.**
#:
#: A2 forbids retrying an arm. The reason is not that a second call is expensive: it is that an
#: arm's outcome is the datum, `format_failure` is one of its pre-registered outcomes, and
#: re-rolling until the artefact loads makes that outcome unobservable — the pre-registration
#: becomes unfalsifiable. The N is fixed in advance and a retry changes it after seeing a result.
#:
#: A probe is not an arm. Nothing records it under `results/`, no metric reads it, and its
#: result enters no F1, leak rate or cost table. So the prohibition does not transfer by
#: definition. It transfers by *form*, and that is the part worth being careful about: retrying
#: here until the YAML loads would let this file report "the de-fenced prompt works" when what
#: it measured was "the de-fenced prompt can work". That is the same substitution A2 forbids,
#: performed on a prompt instead of an arm, and it would be the more damaging one — an arm's
#: format failure is recorded and visible, whereas a laundered probe result is the evidence a
#: prompt gets committed on.
#:
#: So the rule adopted, and its bound:
#:
#: 1. **One call per prompt. Its outcome is the result, reported whatever it says.** There is no
#:    attempt loop in this file and none is to be added. `bedrock.MAX_ATTEMPTS == 1`, so not even
#:    the transport retries: a throttle or a timeout surfaces as a failed probe rather than as a
#:    slower successful one.
#: 2. **A transport failure is not an observation and may be re-run** — a throttle, a socket
#:    timeout, expired credentials. Nothing was sampled, so nothing is being re-rolled. This is
#:    a judgement about whether a datum exists, not about whether it is the wanted one, and the
#:    distinction survives only if it is made *before* the response is looked at: a truncated
#:    response with `stop_reason` `max_tokens` counts as a **failure and not a transport error**,
#:    because the model did answer and the answer did not load.
#: 3. **Additional draws are allowed only when declared before the first call**, by raising
#:    `--draws`, and then every draw is reported in the note in order — the shape
#:    `probe_call_variance.py` already uses for its five. That is a measurement of a
#:    distribution. Choosing a second draw after seeing the first fail is not, whatever it is
#:    called, so `--draws` may not be raised in response to a result: the run is repeated from
#:    the start under the new N and both runs are recorded.
#: 4. **The cap is 3 per prompt.** Beyond that a probe is a search. Three is enough to tell a
#:    reproducible format failure from a single bad draw, which is the only question a larger N
#:    would answer here, and it is small enough that the temptation in 3 has a visible ceiling.
#:
#: **What n = 1 can and cannot support** is then stated in the note rather than left to a reader:
#: a pass at n = 1 is *weaker* evidence than the record it replaces, since the fenced
#: `rule_author.md` has 0/5 format failures in `call-variance.md` plus eight arm artefacts. A
#: single pass does not establish parity. It establishes that the de-fenced prompt is not
#: *categorically* broken, which is the question that blocks the commit.
RETRY_POLICY = "one call per prompt; no retry on a format failure; declared draws capped at 3"

#: `--draws`' ceiling, from `RETRY_POLICY` clause 4. Enforced in `main()` rather than left to the
#: docstring, because a bound that is only documented is a bound the next caller raises.
MAX_DRAWS = 3

#: The invented document the Auditor probe masks. **Not corpus text**: every line is written
#: here, and the identifiers in it are invented — the caveat `rule_author.md` §8.1 attaches to
#: its one example string. Deliberately tiny, per the instruction that corpus text be minimal
#: and the fact that the question is about the *format* of the response and not its recall.
#:
#: Shaped so the call is a real one rather than a degenerate one. It needs at least one detected
#: span (so the masked block carries a tag and the geometry has something to translate through),
#: at least one identifier left unmasked (so there is something to flag and `{"flags": []}` is
#: not the only correct answer), and more than one line (so `line` in the response is a real
#: index rather than trivially 1). A document with nothing to find would make a parseable empty
#: response indistinguishable from a model that ignored the input.
_TEXT = (
    "Servicio de Cardiologia. Paciente: Quilverto Ansbaden, 47 anos.\n"
    "Domicilio: Calle Vermuth 12, Trandavia.\n"
    "Remitido por el Dr. Ovanel Prestomar el 03/04/2019.\n"
)

#: The arm's own predictions for that document, in the shape the loop reads back from
#: `spans.jsonl` (DESIGN §3): the three fields the masker reads, plus the provenance a real
#: detector fills in. The patient name and the date are "found"; the clinician name and the
#: address are not, which is the residual the role exists to flag.
_PREDICTIONS = (
    (34, 53, "NAME"),
    (114, 124, "DATE"),
)


class ProbeError(Exception):
    """The probe could not be set up. Nothing depends on this, by design."""


def today() -> str:
    return _dt.date.today().isoformat()


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _document() -> tuple[Document, list[Span]]:
    """The invented document and the invented predictions over it. No corpus is read.

    `Document.spans` is left empty. Those would be gold, `mask_document` never reads them
    (DESIGN §3), and populating them here would put a second set of offsets in the file for a
    reader to mistake for the one that matters.
    """
    spans = [
        Span(start=start, end=end, surface=_TEXT[start:end], subtype=phi_type,
             phi_type=phi_type, layer="context_cue", detector="probe",
             rule_id=f"{LANG}:probe_{phi_type.lower()}", score=0.8)
        for start, end, phi_type in _PREDICTIONS
    ]
    document = Document(doc_id="probe-invented-0001", corpus_id=CORPUS, text=_TEXT)
    return document, spans


def _rule_author_prompt():
    """Round 1's RuleAuthor prompt, byte for byte what `port-oneshot` sends.

    §§1.3–1.4 are empty because there is no previous iteration, which is the arm's definition
    and also why this call carries no corpus text (`rule_author.md` §1.4's first consequence).
    `rules_path` is left at its default so the §1.2 block is the committed bootstrap file — the
    same block the eight measured artefacts were produced from.
    """
    return assemble_task_prompt(lang=LANG, corpus=CORPUS)


def _auditor_prompt():
    """One Auditor call's prompt over the invented document. Returns the prompt and the geometry.

    The geometry is `masked.lines`, which is what `parse_response` translates the response's
    columns through. It is returned rather than recomputed at verdict time because a second
    masking would be a second chance for the two to disagree.
    """
    document, spans = _document()
    masked = mask_document(document, spans)
    return assemble_audit_prompt(corpus=CORPUS, masked=masked), masked


def _verdict_rule_author(text: str) -> dict:
    """Does a loadable `rules/es.yaml` come out? `load_rules` is the whole answer.

    The response is written to a temporary file and not into the repository: it is a rule file
    an agent wrote and no arm asked for, and `rules/es.yaml` is a committed bootstrap that a
    probe must not touch. The temporary directory is removed on the way out, so the artefact
    exists for exactly as long as the loader needs it.

    Nothing about the response text reaches the return value. The rule count and the rule ids
    do — `rule_id`s are the agent's own coinages and `rule_author.md` Prohibition 2 requires
    them to carry no surface form, which `load_rules` is what enforces — and `comment` fields,
    which are where a surface form would be, are not read here at all.
    """
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / f"{LANG}.yaml"
        path.write_text(text, encoding="utf-8")
        try:
            ruleset = load_rules(LANG, path=path)
        except Exception as exc:                      # the verdict, not an error to propagate
            return {"outcome": "format_failure", "error_type": type(exc).__name__,
                    "rules": None, "layers": None}
    rules = list(getattr(ruleset, "rules", ()))
    layers: dict[str, int] = {}
    for rule in rules:
        layers[rule.layer] = layers.get(rule.layer, 0) + 1
    return {"outcome": "loaded", "error_type": None, "rules": len(rules),
            "layers": dict(sorted(layers.items()))}


def _verdict_auditor(text: str, masked) -> dict:
    """Does parseable flag JSON come out? `parse_response` is the whole answer.

    A `malformed` refusal is what an unparseable or fenced response becomes — that function
    refuses rather than repairing, for `rule_author.md` §2's reason — so the outcome is read off
    the refusal reasons and not off an exception.

    The flags themselves are counted and their types tallied. **No offset is recorded**, even
    though these are invented: a probe that reported positions would be a probe whose output
    format is the deny-listed one, and a habit formed on invented text is a habit.
    """
    audit = parse_response(text, doc_id=masked.doc_id, lines=masked.lines)
    reasons = sorted({r.reason for r in audit.refused})
    types: dict[str, int] = {}
    for flag in audit.flags:
        types[flag.phi_type] = types.get(flag.phi_type, 0) + 1
    malformed = "malformed" in reasons
    return {
        "outcome": "format_failure" if malformed else "parsed",
        "error_type": "malformed" if malformed else None,
        "flags": len(audit.flags),
        "refused": len(audit.refused),
        "refusal_reasons": reasons,
        "by_phi_type": dict(sorted(types.items())),
    }


#: The two probes. `build` returns `(FilledPrompt, extra)` and `verdict` takes
#: `(response_text, extra)`, so the Auditor's geometry travels from one to the other without a
#: global and without being rebuilt.
PROBES = {
    "rule_author": {
        "prompt": "docs/prompts/rule_author.md",
        "question": "does a loadable rules/es.yaml come out?",
        "build": lambda: (_rule_author_prompt(), None),
        "verdict": lambda text, extra: _verdict_rule_author(text),
    },
    "auditor": {
        "prompt": "docs/prompts/auditor.md",
        "question": "does parseable flag JSON come out?",
        "build": _auditor_prompt,
        "verdict": _verdict_auditor,
    },
}


def plan(name: str, prompt, *, model_id: str, draws: int) -> list[str]:
    """What the run will do, for `--dry-run`. Every value here is local or a reference form."""
    spec = PROBES[name]
    reference = prompt.reference()
    return [
        f"probe        {name}",
        f"template     {spec['prompt']}",
        f"question     {spec['question']}",
        f"model_id     {model_id}",
        f"prompt       {reference['text_chars']} chars, {reference['text_sha256'][:23]}…",
        f"sections     filled={reference.get('sections_filled')} "
        f"empty={reference.get('sections_empty')}",
        f"calls        {draws}  (policy: {RETRY_POLICY})",
        "gate         bedrock._require_logging_check() before the first call",
        f"appends to   {NOTE}",
    ]


def run_draw(name: str, draw: int, *, model_id: str, region: str | None, max_tokens: int) -> dict:
    """One call. Returns the row the note reports — no prompt text and no response text.

    The prompt is rebuilt per draw rather than reused, so each row's `prompt_sha256` is measured
    for the call it describes. They are expected to be identical and that is the point of
    recording each one: a differing hash would mean the template moved mid-run.
    """
    from src.llm import bedrock

    spec = PROBES[name]
    prompt, extra = spec["build"]()
    reference = prompt.reference()

    started = time.monotonic()
    response = bedrock.invoke(prompt, model_id=model_id, region=region, max_tokens=max_tokens)
    elapsed = time.monotonic() - started

    row = {
        "probe": name,
        "draw": draw,
        "prompt_chars": reference["text_chars"],
        "prompt_sha256": reference["text_sha256"],
        "response_chars": len(response.text),
        "response_sha256": _digest(response.text),
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
        "wall_seconds": round(elapsed, 3),
        "stop_reason": response.stop_reason,
        "model_id_reported": response.model_id_reported,
    }
    if response.stop_reason == "max_tokens":
        # Clause 2 of `RETRY_POLICY`: the model answered and the answer was cut off. That is a
        # failure of this draw and not a transport error, so it is not re-run — and it is named
        # distinctly, because "the artefact did not load" and "the artefact was truncated" call
        # for different edits and a shared label would hide which one happened.
        row.update({"outcome": "truncated", "error_type": "max_tokens"})
        return row
    row.update(spec["verdict"](response.text, extra))
    return row


def render(rows: list[dict], *, model_id: str, date: str, reason: str) -> str:
    """The block appended to the note. Tables first, then the rows, and no text anywhere.

    **`reason` is required and it is the honesty mechanism of this whole file.** A second run
    of the same probe is either a new measurement or a retry, the difference is entirely in why
    it was started, and nothing in the numbers records it. So the caller states it and the note
    carries it beside the result — which is what makes a later reader able to check
    `RETRY_POLICY` against what actually happened rather than against what it says.

    The prompt hash in each row is the other half: a run whose reason claims the template
    changed, beside a `prompt_sha256` equal to the previous run's, is a claim the record itself
    refutes.
    """
    out = [
        "",
        f"## 예시를 제거한 `rule_author.md` · `auditor.md` — 형식 프로브 ({date})",
        "",
        f"**이 실행의 이유:** {reason}",
        "",
        f"`tools/probe_prompt_format.py`, `{model_id}`, {CORPUS} / {LANG}. "
        f"정책: {RETRY_POLICY}. 코퍼스 텍스트 없음 — RuleAuthor 는 회차 1 프롬프트라 "
        "§§1.3–1.4 가 비어 있고, Auditor 는 이 파일에서 만든 문서를 마스킹한다.",
        "",
        "| probe | draw | outcome | detail | completion tokens | wall s | prompt | response |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        if row["probe"] == "rule_author":
            detail = (f"rules={row['rules']}, layers={row['layers']}"
                      if row.get("rules") is not None else f"`{row.get('error_type')}`")
        else:
            detail = (f"flags={row.get('flags')}, refused={row.get('refused')}"
                      if row.get("flags") is not None else f"`{row.get('error_type')}`")
        out.append(
            f"| {row['probe']} | {row['draw']} | **{row['outcome']}** | {detail} "
            f"| {row['completion_tokens']} | {row['wall_seconds']} "
            f"| {row['prompt_sha256'][7:19]} | {row['response_sha256'][7:19]} |"
        )
    out += [
        "",
        "<details><summary>draw 별 기록</summary>",
        "",
        "    " + json.dumps(rows, ensure_ascii=False, indent=1).replace("\n", "\n    "),
        "",
        "</details>",
        "",
    ]
    return "\n".join(out)


def append_to_note(block: str, note: Path) -> None:
    """Append at end of file. A chronological series of dated measurements; the end is where
    the next one goes — `probe_prompt_cache.py`'s reason for not locating a section."""
    with open(note, "a", encoding="utf-8") as handle:
        handle.write(block)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe whether the de-fenced prompts still produce loadable artefacts. "
                    "Blocks nothing.")
    parser.add_argument("--model-id", required=True,
                        help="the Bedrock id to call; no default, for invoke()'s reason")
    parser.add_argument("--only", choices=sorted(PROBES), default=None,
                        help="run one probe instead of both")
    parser.add_argument("--draws", type=int, default=1,
                        help=f"calls per prompt, declared before the run and capped at "
                             f"{MAX_DRAWS} (default 1). Raising this after seeing a result is "
                             "not a draw count, it is a retry — see RETRY_POLICY.")
    parser.add_argument("--max-tokens", type=int, default=8192,
                        help="output budget; a rule file is a few thousand tokens (default 8192)")
    parser.add_argument("--region", default=None, help="AWS region (default: environment)")
    parser.add_argument("--reason", required=True,
                        help="why this run is being made, recorded verbatim in the note. "
                             "Required because a second run is either a new measurement or a "
                             "retry and only the reason distinguishes them — see RETRY_POLICY.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and call nothing")
    args = parser.parse_args(argv)

    if not 1 <= args.draws <= MAX_DRAWS:
        print(f"--draws must be between 1 and {MAX_DRAWS}, got {args.draws}. "
              f"The cap is RETRY_POLICY clause 4: beyond it a probe is a search.",
              file=sys.stderr)
        return 2

    names = [args.only] if args.only else sorted(PROBES)

    for name in names:
        prompt, _ = PROBES[name]["build"]()
        for line in plan(name, prompt, model_id=args.model_id, draws=args.draws):
            print(line)
        print()
    if args.dry_run:
        print("dry run: no call made, nothing appended")
        return 0

    from src.llm import bedrock

    bedrock._require_logging_check()

    rows = []
    for name in names:
        for draw in range(1, args.draws + 1):
            row = run_draw(name, draw, model_id=args.model_id, region=args.region,
                           max_tokens=args.max_tokens)
            rows.append(row)
            print(f"{name:12} draw {draw}  {row['outcome']:15} "
                  f"{row.get('error_type') or ''}")

    block = render(rows, model_id=args.model_id, date=today(), reason=args.reason)
    append_to_note(block, ROOT / NOTE)
    print(f"\nappended to {NOTE}")
    # The exit code reports the measurement. Nothing consults it, but a probe that returned 0 on
    # a format failure would be one more thing reading green when it is not.
    return 0 if all(r["outcome"] in ("loaded", "parsed") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
