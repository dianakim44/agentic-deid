#!/usr/bin/env python3
"""Ask whether a de-fenced prompt still produces a loadable artefact. A probe, not a gate.

    python3 tools/probe_prompt_format.py --model-id us.anthropic.claude-opus-4-5-20251101-v1:0
    python3 tools/probe_prompt_format.py --model-id ... --dry-run   # print the plan, call nothing
    python3 tools/probe_prompt_format.py --model-id ... --only auditor
    python3 tools/probe_prompt_format.py --model-id ... --draws 20 --json /tmp/probe.json

**Two questions, and the second one arrived on 2026-09-17.** The first is the verdict question
this file was written for: does a de-fenced prompt produce a loadable artefact *at all* — a
pass/fail asked once, before the trade is committed. The second is a **rate**: `port-multi`'s
Profiler and `port-multi-noexample`'s Mapper both died at a fence, on two different roles and two
different prompt revisions, so what the fence rate of each envelope *is* became the thing worth
knowing before any prompt is edited again. A rate needs an N declared in advance and it needs the
five prompts measured the same way, which is what the three authoring probes and `--draws` are
for. The distinction matters for one reason and it is recorded in `RETRY_POLICY` clause 4: the
draw cap that is right for a verdict is not the one that is right for a rate.

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
import math
import re
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora.base import (                                   # noqa: E402
    Document, Span, lexicon_bases, lexicon_names, rule_langs,
)
from src.llm.envelope import parses_json, parses_yaml, unwrap     # noqa: E402
from src.llm.prompt import (                                     # noqa: E402
    assemble_audit_prompt, assemble_lexicon_prompt, assemble_mapper_prompt,
    assemble_profiler_prompt, assemble_task_prompt, mask_document,
)
from src.porting import artefacts                                # noqa: E402
from src.porting.audit import parse_response                     # noqa: E402
from src.porting.multi import read_profile                       # noqa: E402
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
#: 4. **The cap is per question, and there are two.** Three was the cap while the only question
#:    was a verdict — "is this prompt categorically broken" — because three tells a reproducible
#:    format failure from a single bad draw and nothing larger answers that question. It does not
#:    answer a *rate*: 0 of 3 is consistent with a fence rate of 40%, so a cap of 3 would have
#:    made the fence rate of an envelope unmeasurable, and two arms have now died on it. So the
#:    cap is **20**, and what keeps 20 from being a search is clause 3, unchanged: N is declared
#:    before the first call and may not be raised in response to a result. A rate measurement
#:    also has no "did it work" to be tempted by — every draw is reported, a fence is a datum
#:    rather than a setback, and the number that comes out is the answer whatever it is.
#:
#:    **Raised 2026-09-17, for that reason, and the amendment is recorded rather than silent**:
#:    the sentence "the only question a larger N would answer here" stopped being true when the
#:    question changed. 20 is chosen in the note that run appends, not here, because the argument
#:    for a particular N belongs beside the numbers it produced.
#:
#:    **Raised again 2026-09-18, 20 → 60, and the third question is the reason.** A fence rate
#:    of 65% or 95% is measurable at N = 20; a *low* rate is not, and the RuleAuthor's
#:    `RuleError` came back 2 of 20 — an exact interval of 1.2% to 31.7%. That width is the
#:    whole problem, because the arm needs eight RuleAuthor rounds and (1 − p)⁸ is 78% at p =
#:    3% and 43% at p = 10%. Both ends are inside the interval, so the interval does not decide
#:    whether to spend the arm and no re-reading of it will.
#:
#:    What 60 buys, stated as tests rather than as a width, because the width alone would
#:    overpromise. At n = 60: if the true rate is 3%, seeing **6 or more** failures has
#:    probability **0.9%**; if it is 10%, seeing **2 or fewer** has probability **5.3%**. So an
#:    outcome at either end rejects the other hypothesis, at about the 1% level one way and the
#:    5% level the other. `_tail_at_least` and `_tail_at_most` compute these and
#:    `tests/test_probe_prompt_format.py` pins both, because a sample size argued from numbers
#:    nothing checks is an argument that can be off by a factor and still read well — the first
#:    version of this paragraph said 1.6% and 6.2% and was wrong on both.
#:
#:    **And the honest limit**: an outcome of 2 of 60 gives 0.4%–11.5%, which still contains
#:    10%. 60 draws sharpen the decision without settling it from the interval alone, and the
#:    thing that would settle it is the one-sided reading above. A middling outcome — 3, 4 or 5
#:    — rejects neither, and the answer then is that the measurement did not decide, not a
#:    larger N chosen after seeing it. Clause 3 is what keeps this from being a search and it is
#:    unchanged: the new N was declared before the first call of the new run, and the run is
#:    taken **from the start** rather than by adding 40 draws to the 20 already recorded. The
#:    old 20 are a separate measurement and are reported as one.
#:
#: **What n = 1 can and cannot support** is then stated in the note rather than left to a reader:
#: a pass at n = 1 is *weaker* evidence than the record it replaces, since the fenced
#: `rule_author.md` has 0/5 format failures in `call-variance.md` plus eight arm artefacts. A
#: single pass does not establish parity. It establishes that the de-fenced prompt is not
#: *categorically* broken, which is the question that blocks the commit.
RETRY_POLICY = ("no retry on a format failure; draws declared before the first call and capped "
                "at 60 (3 was the cap while the question was a verdict rather than a rate, and "
                "20 while a high rate was the only rate worth resolving)")

#: `--draws`' ceiling, from `RETRY_POLICY` clause 4. Enforced in `main()` rather than left to the
#: docstring, because a bound that is only documented is a bound the next caller raises.
MAX_DRAWS = 60

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


# ─── which check refused, named without quoting anything ─────────────────────
#
# **Added 2026-09-18, because `error_type` alone stopped being enough.** The RuleAuthor's two
# failures on 2026-09-17 read `RuleError` and nothing more, and `src/rules.py` raises `RuleError`
# at 33 places — so the record said the rule file did not load and could not say what about it did
# not. A rate without a cause cannot be acted on: "the fence sentence needs work" and "the model
# writes a layer name that is not in the axis" are the same cell of that table and different
# repairs.
#
# **The message is not recorded, and this is not a preference.** Most of those 33 messages
# interpolate the agent's own `rule_id`, and a `rule_id` can be content-named — the release
# screener's five acknowledged violations are exactly that, place names inside rule ids. So a
# recorded message is a path by which a corpus-shaped string reaches a committed note, which
# CLAUDE.md forbids without regard to corpus. Two things are recorded instead and neither is
# response bytes:
#
# 1. **The raise site**, taken off the traceback: file, line and function. It identifies the check
#    exactly, including checks this file has never seen, and it is the reason an unrecognised
#    failure is still a legible failure rather than an `unclassified` dead end.
# 2. **A class name from a closed table below**, matched on the *invariant* part of the message —
#    the part the code wrote, never an interpolated value. The class survives the line numbers
#    moving; the raise site survives the wording changing. Neither alone is enough and together
#    they cost nothing.

#: `(fragment, class)` in priority order. The fragment is text `src/rules.py` and
#: `src/porting/artefacts.py` write literally; nothing here matches an interpolated value, so no
#: match can be influenced by the response. Order matters at one place and it is marked.
ERROR_CLASSES = (
    # `src/rules.py`, per-rule checks
    ("does not compile", "regex_does_not_compile"),
    ("a rule must be a mapping", "rule_not_a_mapping"),
    ("has no rule_id", "rule_id_absent"),
    ("carries a prefix", "rule_id_carries_lang_prefix"),
    ("layer must be one of", "layer_undeclared"),
    ("is not in config/naming.yaml's phi_type", "phi_type_undeclared"),
    ("no rule may target", "phi_type_not_a_rule_target"),
    ("is not an allowed flag", "flag_not_allowed"),
    ("exactly one matcher form per rule", "matcher_form_count"),
    ("pattern must be a string", "pattern_not_a_string"),
    ("terms must be a list of non-empty strings", "terms_not_a_string_list"),
    ("cue must be a string or a list of strings", "cue_not_a_string_list"),
    ("a cue rule needs", "then_absent"),
    ("gap must be an integer", "gap_out_of_range"),
    ("checksum is only meaningful", "checksum_on_wrong_layer"),
    ("is not an implemented checksum", "checksum_unimplemented"),
    ("score must be a number", "score_out_of_range"),
    ("duplicate rule_id in one file", "duplicate_rule_id"),
    # `src/rules.py`, the lexicon reference. `lexicon_collection_not_named` is the one this
    # project has a live hazard about: DESIGN §6.7.1 and the readiness check of 2026-09-17.
    ("lexicon must be", "lexicon_ref_shape"),
    ("lexicon name must be", "lexicon_name_shape"),
    ("was given no lexicon", "lexicon_collection_not_named"),
    ("no lexicon at", "lexicon_file_absent"),
    ("the lexicon at", "lexicon_file_empty"),
    # `src/rules.py`, whole-file checks. **Order:** the file-language message and the lexicon
    # reference's language message share the fragment "is not a lang in config/naming.yaml", and
    # only the first names the *file*, so it is tested first and the general one last.
    ("the language of the rule *file*", "file_lang_undeclared"),
    ("not parseable as YAML", "yaml_not_parseable"),
    ("the file must be a mapping with", "file_not_a_mapping"),
    ("the file declares lang", "file_lang_mismatch"),
    ("'version' must be an integer", "version_shape"),
    ("is not a lang in config/naming.yaml", "lexicon_lang_undeclared"),
    # `src/porting/artefacts.py`, the three refusals that stop an arm rather than dropping an
    # entry. `parse_object`'s own refusals come first because they precede validation.
    # `parse_object` raises twice: unparseable, and parseable-but-not-an-object. Both contain
    # "§2.1 asks for one", so the first is named by the fragment only it has and the second by
    # the shared one, which is why these two are in this order.
    ("response is not JSON", "not_json"),
    ("§2.1 asks for one ", "not_a_json_object"),
    ("no `lexicons` object", "lexicons_object_absent"),
    # Cut before the interpolation: the message reads "…and this corpus loads rule files "
    # `f"for {sorted(declared_langs)}"`, so the literal ends where the list begins.
    ("corpus loads rule files", "lexicon_lang_not_this_corpus"),
    ("no rule languages were given", "no_rule_langs"),
)


def _error_class(exc: BaseException) -> str:
    """A name from `ERROR_CLASSES` for this exception, or `unclassified`.

    `unclassified` is not a failure of the probe: the raise site beside it says which check
    fired, so a new class can be added afterwards from the record rather than by re-running the
    calls. What would be a failure is a table that matched an interpolated value, and no entry
    does.
    """
    message = str(exc)
    for fragment, name in ERROR_CLASSES:
        if fragment in message:
            return name
    return "unclassified"


def _error_site(exc: BaseException) -> str | None:
    """`file:line in function` for the deepest frame inside this repository, or `None`.

    The deepest rather than the shallowest, because the shallowest is always this file's `try`.
    Frames outside the repository are skipped so that a `yaml` or `re` internal does not become
    the answer — what is wanted is the project's own check, and `src/rules.py:225` re-raises the
    `regex` failure as its own for exactly that reason.

    No response bytes. A line number is a fact about the repository at this commit, which is why
    the note that carries it also carries the commit.
    """
    site = None
    tb = exc.__traceback__
    while tb is not None:
        path = Path(tb.tb_frame.f_code.co_filename).resolve()
        if path.is_relative_to(ROOT) and ROOT / "tools" not in path.parents:
            site = f"{path.relative_to(ROOT)}:{tb.tb_lineno} in {tb.tb_frame.f_code.co_name}"
        tb = tb.tb_next
    return site


def _failure(exc: BaseException, **extra) -> dict:
    """The row fields every verdict writes when its loader refused. One shape, five roles."""
    return {"outcome": "format_failure", "error_type": type(exc).__name__,
            "error_class": _error_class(exc), "error_site": _error_site(exc), **extra}


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

    **Two loads, and the second one is what the arm would do** (added 2026-09-18). This function
    called `load_rules` with no `lexicons` directory, which is right for the question it was
    written for — the twenty draws of 2026-09-17 are that number and stay comparable to it — and
    wrong for the question the arm asks. `port-multi`'s RuleAuthor is loaded against
    `arm_lexicon_root()`, which by then holds whatever the LexiconBuilder wrote; so a draw that
    fails here for `lexicon_collection_not_named` is a failure of *this harness* and not of the
    response, and counting it against the arm would overstate the risk it is being run to measure.

    So `outcome` is the no-collection load, unchanged, and `arm_outcome` is the same bytes loaded
    against a collection containing exactly the lists the response references — one invented term
    each, written here (`_LEXICON_STUB`), never corpus text. Neither launders the other: both are
    recorded, they are named differently, and the second is *not* a retry, because nothing about
    the response is edited and its own failures are reported whatever they are.

    **What `arm_outcome` still cannot tell you** is stated here rather than left to a reader. In
    an arm the referenced list exists only if the LexiconBuilder produced a file of that name, so
    a draw that loads here can still meet `lexicon_file_absent` in the arm. That is a joint
    property of two calls and no single-role probe can measure it; `lexicon_refs` below is what a
    later joint reading would need, and it is why the refs are recorded per draw.
    """
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / f"{LANG}.yaml"
        path.write_text(text, encoding="utf-8")
        refs = _lexicon_refs(text)
        try:
            ruleset = load_rules(LANG, path=path)
        except Exception as exc:                      # the verdict, not an error to propagate
            return {**_failure(exc, rules=None, layers=None), **refs,
                    **_arm_load(path, refs["lexicon_refs"])}
        row = {"outcome": "loaded", "error_type": None, "error_class": None, "error_site": None,
               "rules": len(list(getattr(ruleset, "rules", ()))),
               **refs, **_arm_load(path, refs["lexicon_refs"])}
    rules = list(getattr(ruleset, "rules", ()))
    layers: dict[str, int] = {}
    for rule in rules:
        layers[rule.layer] = layers.get(rule.layer, 0) + 1
    row["layers"] = dict(sorted(layers.items()))
    return row


#: The one line a stubbed lexicon file holds. Invented here, three characters or more so it is not
#: refused as too short, and never a term from anywhere — `_TEXT`'s caveat, one artefact over.
_LEXICON_STUB = "Zorbanel\n"


def _lexicon_refs(text: str) -> dict:
    """Which `{lang}/{name}` lists this rule file references, read off the YAML. Names only.

    A reference whose name is not in `config/naming.yaml`'s `lexicon_names` is **counted and not
    recorded**: a declared name is project vocabulary and safe to write down, an undeclared one is
    a string the model chose and this file does not put those in a note.

    Returns `{}`-safe values on an unparseable response, because this runs before the load and a
    response that does not parse has no references rather than an error.
    """
    declared = set(lexicon_names())
    try:
        parsed = yaml.safe_load(text)
    except Exception:                                 # noqa: BLE001 — see the docstring
        return {"lexicon_refs": [], "lexicon_refs_undeclared": 0, "lexicon_rules": 0}
    rules = (parsed or {}).get("rules") if isinstance(parsed, dict) else None
    refs, undeclared, count = set(), 0, 0
    for rule in rules if isinstance(rules, list) else ():
        ref = rule.get("lexicon") if isinstance(rule, dict) else None
        if not isinstance(ref, str):
            continue
        count += 1
        lang, _, name = ref.partition("/")
        if name in declared and lang in set(rule_langs(CORPUS)) | {LANG}:
            refs.add(f"{lang}/{name}")
        else:
            undeclared += 1
    return {"lexicon_refs": sorted(refs), "lexicon_refs_undeclared": undeclared,
            "lexicon_rules": count}


def _arm_load(path: Path, refs: list[str]) -> dict:
    """Load the same rule file against a collection holding exactly `refs`. See `_verdict_...`.

    An empty `refs` means the file takes no `lexicon:` form, and then this load and the other are
    the same load by construction — `load_rules`' own docstring says so. It is run anyway rather
    than skipped, so that `arm_outcome` is a measured value on every draw and not a field whose
    absence a reader has to interpret (`model_id_absent`'s argument, DESIGN §4).
    """
    with tempfile.TemporaryDirectory() as collection:
        for ref in refs:
            lang, _, name = ref.partition("/")
            leaf = Path(collection) / lang / f"{name}.txt"
            leaf.parent.mkdir(parents=True, exist_ok=True)
            leaf.write_text(_LEXICON_STUB, encoding="utf-8")
        try:
            ruleset = load_rules(LANG, path=path, lexicons=Path(collection))
        except Exception as exc:
            return {"arm_outcome": "format_failure", "arm_error_type": type(exc).__name__,
                    "arm_error_class": _error_class(exc), "arm_error_site": _error_site(exc),
                    "arm_rules": None}
    return {"arm_outcome": "loaded", "arm_error_type": None, "arm_error_class": None,
            "arm_error_site": None, "arm_rules": len(list(getattr(ruleset, "rules", ())))}


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


# ─── the three authoring prompts, added 2026-09-17 for the rate question ─────
#
# The envelopes `port-multi` and `port-multi-noexample` spent their calls on. Each is built by
# the function the arm calls, so what is measured is the arm's prompt and not a reconstruction
# of it: a run's note carries each row's `prompt_sha256`, which is checkable against the
# `prompt_reference.text_sha256` in the arm's own `agent_calls.jsonl` or `format_failure.json`.
#
# **None of these carries corpus text.** The Profiler's input is the filtered inventory
# (`filter_inventory`, profiler.md §1.2), the Mapper's is two label lists, and the
# LexiconBuilder's input contains nothing from the corpus at all (`assemble_lexicon_prompt`'s
# docstring says so and DESIGN §4 is why). The Mapper's prompt needs a profile, and the one it
# is given is the arm's committed `profile.json` — read, never written.


def _profiler_prompt():
    """The Profiler's call, with the inventory filtered exactly as `author_profile()` filters it.

    Returns the filtered inventory as the extra, because `validate_profile()` needs it and a
    second `filter_inventory()` at verdict time would be a second chance for the two to differ.
    """
    inventory = artefacts.filter_inventory(artefacts.read_inventory(CORPUS))
    return assemble_profiler_prompt(corpus=CORPUS, inventory=inventory), inventory


def _mapper_prompt():
    """The Mapper's call, over `port-multi-noexample`'s profile. Read-only, and no arm is touched.

    The extra is the profile's `type_inventory`, which is what `validate_mapping()` takes — the
    same value `author_mapping()` passes it.
    """
    profile = read_profile(corpus=CORPUS, detector="R", supervision="sup-free",
                           porting="port-multi-noexample", root=ROOT)
    return (assemble_mapper_prompt(corpus=CORPUS, profile=profile),
            profile[artefacts.PROFILE_LABEL_FIELD])


def _lexicon_prompt():
    """The LexiconBuilder's call. `langs` is `rule_langs(corpus)`, which the assembler re-checks."""
    langs = rule_langs(CORPUS)
    return assemble_lexicon_prompt(corpus=CORPUS, langs=langs), langs


def _verdict_profiler(text: str, inventory) -> dict:
    """Does a validating profile come out? `parse_object` then `validate_profile`, unrepaired."""
    try:
        obj = artefacts.parse_object(text, what="profile")
        profile, refused = artefacts.validate_profile(obj, inventory=inventory)
    except Exception as exc:                          # the verdict, not an error to propagate
        return _failure(exc, fields=None, refused=None)
    return {"outcome": "validated" if not refused else "refused",
            "error_type": None, "error_class": None, "error_site": None,
            "fields": len(profile), "refused": len(refused),
            "unresolved": len(profile.get("unresolved") or []),
            "by_refusal": _tally(r.get("reason") for r in refused)}


def _verdict_mapper(text: str, type_inventory) -> dict:
    """Does a validating mapping come out, and what does it say against §9.0?

    The §9.0 comparison is included because it costs nothing once the object is in hand and it
    is the only thing that distinguishes "the envelope leaks fences" from "the envelope leaks
    fences *and* the content underneath varies". **It is not an M-row value**: M2 and M3 are
    filled from an arm's `mapping.yaml` (DESIGN §6.7.6) and a probe writes none.
    """
    try:
        obj = artefacts.parse_object(text, what="mapping")
        kept_map, kept_excluded, refused = artefacts.validate_mapping(
            obj, type_inventory=type_inventory)
    except Exception as exc:
        return _failure(exc, mapped=None, refused=None)
    disagreements, compared, applied = artefacts.compare_with_design(
        CORPUS, kept_map, kept_excluded, type_inventory=type_inventory)
    return {"outcome": "validated" if not refused else "refused", "error_type": None,
            "error_class": None, "error_site": None,
            "mapped": len(kept_map), "excluded": len(kept_excluded), "refused": len(refused),
            "by_refusal": _tally(r.get("reason") for r in refused),
            "unresolved": len(artefacts.kept_unresolved(
                obj, set(kept_map) | set(kept_excluded))),
            "disagreements": len(disagreements), "compared": compared, "applied": applied,
            "disagreeing_types": sorted(d["source_type"] for d in disagreements),
            "by_basis": _tally(
                e.get("basis") for e in list(kept_map.values()) + list(kept_excluded.values()))}


def _verdict_lexicon(text: str, langs) -> dict:
    """Does a validating lexicon set come out? Counts only — **no term is recorded**.

    `validate_lexicon` drops refused entries and continues, so `refused` here is a count of
    entries and not a verdict on the response; the outcome is about the format.

    **`outcome` is not the pass rate this arm turns on, and 2026-09-18 is when that stopped being
    a quibble.** `validated` here means the block parsed and at least one file survived; the arm
    needs the files it *asked for*, with the `basis` §6.7.5 cross-checks against transfer. So the
    fields below split what one word was carrying: `offered_*` is what the response contained,
    `by_class` is why anything was dropped, and `basis_*` is whether §6.7.5's discriminator
    survived. A response that offers eight files and keeps one is `validated` and is not a pass.

    **`terms` was wrong in the 2026-09-17 block of the note and is fixed here.** It read
    `len(kept[lang][name])`, and that value is a `{"basis": …, "entries": [...]}` mapping, so the
    count was its two keys: every file reported 2 terms and every draw reported `terms == 2 ×
    files`. Which is exactly what that block shows — `files: 3, terms: 6` — and the shape of the
    error is why nobody read it as one: 2 per file is a plausible number for a first-round
    lexicon, so the bug produced a believable answer rather than an absurd one. The only surviving
    trace of the real magnitude in those rows is `list_items`, which the diagnostic path computed
    independently and which reads 303 for that same draw. The rows are not edited — a recorded
    measurement stays as recorded — and the 2026-09-18 block says this in prose instead.
    """
    try:
        obj = artefacts.parse_object(text, what="lexicon")
    except Exception as exc:
        return _failure(exc, files=None, terms=None)
    offered = _lexicon_offered(obj)
    try:
        kept, refused = artefacts.validate_lexicon(obj, langs=list(langs))
    except Exception as exc:
        return {**_failure(exc, files=None, terms=None), **offered}
    files = {f"{lang}/{name}": len(entry["entries"])
             for lang, block in sorted(kept.items())
             for name, entry in sorted(block.items())}
    return {"outcome": "validated", "error_type": None, "error_class": None, "error_site": None,
            "files": len(files),
            "terms": sum(files.values()), "terms_by_file": files, **offered,
            "kept_bases": _tally(kept[lang][name]["basis"]
                                 for lang in kept for name in kept[lang]),
            "refused": len(refused), "by_refusal": _tally(r.get("reason") for r in refused),
            "by_class": _tally(_refusal_class(obj, r) for r in refused)}


#: The five classes a per-file or per-entry lexicon refusal falls into. The first three are the
#: three questions asked of this artefact on 2026-09-18 — is the *shape* wrong, is a *value*
#: outside `config/naming.yaml`, or is the field §6.7.5's discriminator needs missing — and the
#: last two are the residue, named rather than folded into "schema" so that a response whose only
#: problem is a duplicate term does not read as a response with a broken shape.
#:
#: A refusal has exactly one class, so the tally sums to `refused`. **An undeclared `basis` is
#: therefore counted as `vocabulary` and not as `discriminator`**, even though it destroys §6.7.5's
#: cross-check just as an absent one does: the two facts are wanted separately, and the second is
#: `basis_undeclared`, which is a count over what the response *offered* and does not compete with
#: this tally.
REFUSAL_CLASSES = ("schema", "vocabulary", "discriminator", "entry_hygiene", "empty")


def _refusal_class(obj: dict, refusal: dict) -> str:
    """Which of `REFUSAL_CLASSES` this one refusal is. Reads key names and declared values only.

    `validate_lexicon` records `{"file": …, "reason": …}` and two of its reasons cover more than
    one question — `missing_field` can be `basis` or `entries`, `undeclared_value` can be the
    file's name or its basis — so the entry is looked up again here to tell them apart. Looking it
    up rather than changing what the validator records is deliberate: the validator's refusal
    shape is read by `lexicon_manifest` and written into an arm's artefact, and a probe is not a
    reason to move an arm's record.
    """
    reason = refusal.get("reason")
    if reason in ("entry_contains_newline", "entry_contains_comment_mark", "entry_too_short",
                  "duplicate_entry"):
        return "entry_hygiene"
    if reason == "entry_is_vocabulary_term":
        return "vocabulary"
    if reason == "empty_lexicon":
        return "empty"
    entry = _lexicon_entry(obj, refusal.get("file"))
    if reason == "missing_field":
        return "discriminator" if not isinstance(entry, dict) or "basis" not in entry else "schema"
    if reason == "undeclared_value":
        if isinstance(entry, dict) and entry.get("basis") not in set(lexicon_bases()):
            return "vocabulary"          # the basis is the undeclared value
        return "vocabulary"              # …or the file name is; both are naming.yaml values
    return "schema"                      # malformed, unknown_field, and anything added later


def _lexicon_entry(obj: dict, where) -> object:
    """The `{lang}/{name}` entry a refusal names, or `None`. No value is returned to a caller
    that records it — `_refusal_class` reads keys and the `basis` value, and nothing else."""
    if not isinstance(where, str) or "/" not in where:
        return None
    lang, _, name = where.partition("/")
    block = obj.get("lexicons") if isinstance(obj, dict) else None
    per_lang = block.get(lang) if isinstance(block, dict) else None
    return per_lang.get(name) if isinstance(per_lang, dict) else None


def _lexicon_offered(obj: dict) -> dict:
    """What the response contained, before any of it was refused. Counts and declared values.

    The point of counting the offer separately is that `files` counts survivors, and a survivor
    count cannot distinguish "the model wrote two lists" from "the model wrote nine and seven were
    dropped". §6.7.5's `basis` × transfer cross-check needs a declared basis per file, so
    `basis_absent` and `basis_undeclared` are the two ways that check goes missing and they are
    counted over the offer for the same reason.

    A file name outside `lexicon_names()` is counted, never recorded; a `basis` outside
    `lexicon_bases()` likewise. Both are `config/naming.yaml` vocabularies, so the *declared*
    ones are safe to write down and are — `offered_bases` is how a reader sees which bases the
    model reaches for at all.
    """
    names, bases = set(lexicon_names()), set(lexicon_bases())
    block = obj.get("lexicons") if isinstance(obj, dict) else None
    offered = declared = absent = undeclared = 0
    seen_bases: list[str] = []
    for lang, per_lang in (block or {}).items() if isinstance(block, dict) else ():
        if not isinstance(per_lang, dict):
            continue
        for name, entry in per_lang.items():
            offered += 1
            declared += 1 if name in names else 0
            if not isinstance(entry, dict) or "basis" not in entry:
                absent += 1
            elif entry["basis"] in bases:
                seen_bases.append(entry["basis"])
            else:
                undeclared += 1
    return {"offered_files": offered, "offered_declared_names": declared,
            "basis_absent": absent, "basis_undeclared": undeclared,
            "offered_bases": _tally(seen_bases)}


def _tally(values) -> dict:
    out: dict[str, int] = {}
    for value in values:
        if value is not None:
            out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))


# ─── the fence, and what a fenced draw looks like underneath ─────────────────
#
# **The outcome above came off the response as it arrived until 2026-09-18, and now comes off
# `envelope.unwrap()`'s payload.** The reason is not that fenced draws were inconvenient: it is
# that `src/llm/envelope.py` went live for all five roles on 2026-09-17, so from that date the
# unrepaired text is no longer what any arm parses, and a probe reporting a rate the arm cannot
# have is measuring a code path that no longer exists. The paragraph this replaces argued the
# opposite and was right at the time — what changed is `src/`, not the standard.
#
# The separation it protected is still needed and has simply moved one step out. `outcome` now
# comes from the payload through the real loader, and `unwrap` is not a repair: one shape, no
# retry, and a payload that does not parse is `refused` with the received bytes handed on
# unchanged. So a nested fence, a one-sided fence and a preamble are still `format_failure`.
#
# `_fence_scan` and `_shape` stay, and stay *more lenient than the strip*, which is now their
# whole point. `unwrap` refuses a preamble; `_shape` peels a leading fence off whatever it is
# handed and tries the loader anyway. That is what tells "the model wrote a different object"
# from "the model wrote the same object and wrapped it badly" — the distinction that identified
# the Mapper's two-response population — and it is available only on a *refused* draw, since an
# accepted one has a payload that by definition loads. A diagnostic never becomes an outcome,
# and DESIGN §10 A2 is about what an arm may do with a response, not what a probe may measure.
#
# `_fence_scan` also survives for a narrower reason: `Envelope.record()` deliberately omits the
# fence's language tag (it is response bytes, DESIGN §6.8), and `fence_tag` is the one field
# here that `record()` does not supply. `fenced` and `fence_lines` are now redundant with it and
# are kept so the pre-2026-09-18 rows in the note stay comparable column for column.

_FENCE_OPEN = re.compile(r"^```([A-Za-z0-9_+-]*)\s*$", re.M)


def _fence_scan(text: str) -> dict:
    """Whether the response is fenced, how many fence lines it has, and the language tag.

    The tag is recorded because "```" and "```json" are different imitations — one is a code
    block and the other names the artefact's format — and a rate that merged them would lose the
    only clue about what is being imitated. A tag is a language name, never corpus text.
    """
    tags = _FENCE_OPEN.findall(text)
    return {"fenced": text.lstrip().startswith("```"),
            "fence_lines": len(tags),
            "fence_tag": (tags[0] or "(none)") if tags else None}


def _shape(text: str, loader) -> dict:
    """The structure under any fence: chars, depth, key counts. Counts only, no values."""
    body = text.strip()
    body = _FENCE_OPEN.sub("", body).strip() if body.startswith("```") else body
    try:
        parsed = loader(body)
    except Exception as exc:
        return {"body_chars": len(body), "body_loads": False,
                "body_error": type(exc).__name__, "depth": None, "top_keys": None,
                "total_keys": None, "list_items": None}
    depth, keys, items = _walk(parsed)
    return {"body_chars": len(body), "body_loads": True, "body_error": None, "depth": depth,
            "top_keys": len(parsed) if isinstance(parsed, (dict, list)) else 0,
            "total_keys": keys, "list_items": items}


def _walk(node, level: int = 1) -> tuple[int, int, int]:
    """`(max depth, dict keys, list items)` over a loaded object. Values are never read."""
    if isinstance(node, dict):
        depth, keys, items = level, len(node), 0
        for value in node.values():
            d, k, i = _walk(value, level + 1)
            depth, keys, items = max(depth, d), keys + k, items + i
        return depth, keys, items
    if isinstance(node, list):
        depth, keys, items = level, 0, len(node)
        for value in node:
            d, k, i = _walk(value, level + 1)
            depth, keys, items = max(depth, d), keys + k, items + i
        return depth, keys, items
    return level - 1, 0, 0


#: The five probes. `build` returns `(FilledPrompt, extra)` and `verdict` takes
#: `(response_text, extra)`, so the Auditor's geometry travels from one to the other without a
#: global and without being rebuilt. `shape` is the loader the diagnostics use — the artefact's
#: own format, which is YAML for exactly one of the five.
#:
#: `parses` is the same artefact's format again, as `envelope.unwrap()` wants it: a predicate
#: rather than a loader, because `unwrap` asks whether the payload *is* the expected shape and
#: not what it contains. The two fields agree by construction and are both written out rather
#: than one derived from the other, because deriving would mean a table from loader to predicate
#: living here — and a mapping from one vocabulary to another, maintained by hand, in the file
#: whose subject is a role-by-role exemption. `envelope.py` takes `parses` as a parameter for
#: exactly that reason and this is the caller honouring it.
PROBES = {
    "rule_author": {
        "prompt": "docs/prompts/rule_author.md",
        "question": "does a loadable rules/es.yaml come out?",
        "build": lambda: (_rule_author_prompt(), None),
        "verdict": lambda text, extra: _verdict_rule_author(text),
        "shape": yaml.safe_load,
        "parses": parses_yaml,
    },
    "auditor": {
        "prompt": "docs/prompts/auditor.md",
        "question": "does parseable flag JSON come out?",
        "build": _auditor_prompt,
        "verdict": _verdict_auditor,
        "shape": json.loads,
        "parses": parses_json,
    },
    "profiler": {
        "prompt": "docs/prompts/profiler.md",
        "question": "does a validating profile.json come out?",
        "build": _profiler_prompt,
        "verdict": _verdict_profiler,
        "shape": json.loads,
        "parses": parses_json,
    },
    "mapper": {
        "prompt": "docs/prompts/mapper.md",
        "question": "does a validating mapping come out?",
        "build": _mapper_prompt,
        "verdict": _verdict_mapper,
        "shape": json.loads,
        "parses": parses_json,
    },
    "lexicon_builder": {
        "prompt": "docs/prompts/lexicon_builder.md",
        "question": "does a validating lexicon set come out?",
        "build": _lexicon_prompt,
        "verdict": _verdict_lexicon,
        "shape": json.loads,
        "parses": parses_json,
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
    try:
        response = bedrock.invoke(prompt, model_id=model_id, region=region,
                                  max_tokens=max_tokens)
    except Exception as exc:
        # `RETRY_POLICY` clause 2: a transport failure is not an observation. It is recorded and
        # the run continues, because a run of 100 draws that lost the other 99 to one throttle
        # would be a measurement nobody can take. The row is marked so it cannot be counted as a
        # draw — `_observations()` filters on it — and it is not retried inside this file.
        return {"probe": name, "draw": draw, "outcome": "transport_error",
                "error_type": type(exc).__name__,
                "prompt_chars": reference["text_chars"],
                "prompt_sha256": reference["text_sha256"],
                "wall_seconds": round(time.monotonic() - started, 3)}
    elapsed = time.monotonic() - started

    # The strip, on the same terms as every driver in `src/`: before anything is judged, once,
    # with the artefact's own predicate. `record()` supplies `response_chars`/`response_sha256`
    # as well as the four parsed fields, so those two are no longer computed here — one writer
    # for the received bytes, and `_digest` above is the same function `record()` calls.
    env = unwrap(response.text, parses=spec["parses"])

    row = {
        "probe": name,
        "draw": draw,
        "prompt_chars": reference["text_chars"],
        "prompt_sha256": reference["text_sha256"],
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
        "wall_seconds": round(elapsed, 3),
        "stop_reason": response.stop_reason,
        "model_id_reported": response.model_id_reported,
    }
    row.update(env.record())
    row.update(_fence_scan(response.text))
    row.update(_shape(response.text, spec["shape"]))
    if response.stop_reason == "max_tokens":
        # Clause 2 of `RETRY_POLICY`: the model answered and the answer was cut off. That is a
        # failure of this draw and not a transport error, so it is not re-run — and it is named
        # distinctly, because "the artefact did not load" and "the artefact was truncated" call
        # for different edits and a shared label would hide which one happened.
        row.update({"outcome": "truncated", "error_type": "max_tokens"})
        return row
    # `env.payload`, not `response.text`: what the arm parses. On `bare` and on `refused` the two
    # are the same string, so this differs only on `fenced_once` — which is the whole measurement.
    row.update(spec["verdict"](env.payload, extra))
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
    probes = list(dict.fromkeys(r["probe"] for r in rows))
    out = [
        "",
        f"## 펜스 발생률 — {' · '.join(f'`{p}`' for p in probes)} ({date})",
        "",
        f"**이 실행의 이유:** {reason}",
        "",
        f"`tools/probe_prompt_format.py`, `{model_id}`, {CORPUS} / {LANG}. "
        f"정책: {RETRY_POLICY}. 코퍼스 텍스트 없음 — RuleAuthor 는 회차 1 프롬프트라 "
        "§§1.3–1.4 가 비어 있고, Auditor 는 이 파일에서 만든 문서를 마스킹하며, 저술 세 프롬프트는 "
        "필터된 인벤토리·라벨 두 목록·아무 코퍼스 입력도 없는 목록을 받는다.",
        "",
    ]
    out += _rate_table(rows)
    out += _class_table(rows)
    out += _lexicon_table(rows)
    out += _shape_table(rows)
    out += [
        "",
        "| probe | draw | outcome | fence | detail | completion | wall s | prompt | response |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        fence = ("**펜스**" if row.get("fenced") else "—")
        if row.get("fence_tag"):
            fence += f" `{row['fence_tag']}`"
        out.append(
            f"| {row['probe']} | {row['draw']} | **{row['outcome']}** | {fence} "
            f"| {_detail(row)} | {row.get('completion_tokens')} | {row.get('wall_seconds')} "
            f"| {(row.get('prompt_sha256') or '')[7:19]} "
            f"| {(row.get('response_sha256') or '')[7:19]} |"
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


def _observations(rows: list[dict]) -> list[dict]:
    """The rows that are draws. A `transport_error` is not one — `RETRY_POLICY` clause 2."""
    return [r for r in rows if r["outcome"] != "transport_error"]


def _detail(row: dict) -> str:
    """One cell per probe, counts only."""
    if row["outcome"] == "transport_error":
        return f"`{row.get('error_type')}`"
    if row.get("error_type") and row["outcome"] in ("format_failure", "truncated"):
        # The class before the exception type, because `RuleError` is 33 different failures and
        # the type name says which module raised rather than what it refused. The site is the
        # class's own audit trail: an `unclassified` row still names a file and a line.
        cell = f"`{row.get('error_class') or row['error_type']}`"
        return f"{cell} @ {row['error_site']}" if row.get("error_site") else cell
    if row["probe"] == "rule_author":
        return (f"rules={row.get('rules')}, layers={row.get('layers')}, "
                f"lex={row.get('lexicon_rules')}, arm=**{row.get('arm_outcome')}**")
    if row["probe"] == "auditor":
        return f"flags={row.get('flags')}, refused={row.get('refused')}"
    if row["probe"] == "profiler":
        return (f"fields={row.get('fields')}, refused={row.get('refused')}, "
                f"unresolved={row.get('unresolved')}")
    if row["probe"] == "mapper":
        return (f"map={row.get('mapped')}, excl={row.get('excluded')}, "
                f"refused={row.get('refused')}, §9.0 불일치={row.get('disagreements')}")
    return (f"files={row.get('files')}/{row.get('offered_files')}, terms={row.get('terms')}, "
            f"refused={row.get('refused')}, basis 없음={row.get('basis_absent')}")


def _rate_table(rows: list[dict]) -> list[str]:
    """Per probe: what the envelope was, and whether the artefact then validated.

    **The headline moved on 2026-09-18 and the old one is kept.** Until the strip went live the
    fence rate *was* the failure rate, so one column answered both. It no longer is: a
    `fenced_once` draw passes. So the table now reads left to right as the arm experiences the
    response — the three envelope kinds, then `format_failure`, then the pass rate — and the
    fence rate stays beside them because it is the number the 2026-09-17 blocks in this note
    report and a column that vanished would make those blocks incomparable rather than historical.

    `n` counts observations, so a probe whose draws included a transport error reports the N it
    actually got rather than the N it was asked for. The interval is on the *failure* rate, not
    the pass rate, and it is here because a rate without one is what made a 2-of-20 reading
    unusable for the decision this run exists to settle: 1.2%–31.7% spans both hypotheses.
    Reported to one decimal because the whole question is where the low end sits.
    """
    out = ["| probe | n | bare | fenced_once | refused | 펜스율 | format_failure | 실패율 "
           "(exact 95%) | 통과율 | 전송 실패 | 응답 문자수 중앙값 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in dict.fromkeys(r["probe"] for r in rows):
        mine = [r for r in rows if r["probe"] == name]
        seen = _observations(mine)
        kinds = _tally(r.get("envelope") for r in seen)
        fenced = [r for r in seen if r.get("fenced")]
        failed = [r for r in seen if r["outcome"] in ("format_failure", "truncated")]
        passed = [r for r in seen if r["outcome"] in PASSED]
        chars = sorted(r.get("response_chars") or 0 for r in seen)
        median = chars[len(chars) // 2] if chars else 0
        rate = f"{100 * len(fenced) / len(seen):.0f}%" if seen else "—"
        out.append(
            f"| {name} | {len(seen)} | {kinds.get('bare', 0)} | {kinds.get('fenced_once', 0)} "
            f"| {kinds.get('refused', 0)} | {rate} | {len(failed)} "
            f"| {_interval(len(failed), len(seen))} "
            f"| **{f'{100 * len(passed) / len(seen):.0f}%' if seen else '—'}** "
            f"| {len(mine) - len(seen)} | {median} |")
    return out


#: The two words a passing draw's `outcome` can be. `loaded` is the RuleAuthor's, because what it
#: produces is a rule file and `load_rules` either loads it or raises; the other four validate an
#: artefact. Spelled once because `_rate_table`'s pass rate is the number the launch decision reads
#: and a rate that silently omitted one role's success would read as a 0% pass rate.
PASSED = ("validated", "loaded")


def _interval(k: int, n: int) -> str:
    """The Clopper–Pearson exact interval at 95%, as `p% [lo–hi]`. `—` at n = 0.

    **Exact rather than Wilson, and the reason is continuity of the record.** The interval this
    run exists to narrow was quoted as 1.2%–31.7% for 2 of 20, and that is Clopper–Pearson;
    Wilson gives 2.8%–30.1% for the same data. Both are defensible and the difference is not the
    point — the point is that the decision was argued from one of them, the binomial reasoning in
    `RETRY_POLICY` clause 4 is stated against it, and a table that quietly reported the other
    would make the new numbers look narrower than they are by the yardstick already in use.

    No dependency for it: the bounds are Beta quantiles, obtained here by bisecting the binomial
    tail, which is exact to the printed decimal at n ≤ 60 and adds nothing to `pyproject.toml`.
    Conservative — coverage is at least 95% — which is the direction to err in when the question
    is whether a low failure rate is low enough to bet eight rounds on.
    """
    if n <= 0:
        return "—"
    lo = 0.0 if k == 0 else _bisect(lambda p: _tail_at_least(k, n, p) - 0.025)
    hi = 1.0 if k == n else _bisect(lambda p: 0.025 - _tail_at_most(k, n, p))
    return f"{100 * k / n:.1f}% [{100 * lo:.1f}–{100 * hi:.1f}]"


def _bisect(f, lo: float = 0.0, hi: float = 1.0) -> float:
    """The root of a monotone increasing `f` on `[0, 1]`. Sixty halvings, so ~1e-18."""
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
    return (lo + hi) / 2


def _log_choose(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _pmf(i: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 1.0 if i == 0 else 0.0
    if p >= 1.0:
        return 1.0 if i == n else 0.0
    return math.exp(_log_choose(n, i) + i * math.log(p) + (n - i) * math.log1p(-p))


def _tail_at_least(k: int, n: int, p: float) -> float:
    """P(X ≥ k). Increasing in `p`, which is what `_bisect` needs for the lower bound."""
    return sum(_pmf(i, n, p) for i in range(k, n + 1))


def _tail_at_most(k: int, n: int, p: float) -> float:
    """P(X ≤ k). Decreasing in `p`, so the caller negates it before bisecting."""
    return sum(_pmf(i, n, p) for i in range(0, k + 1))


def _class_table(rows: list[dict]) -> list[str]:
    """Which check refused, per probe. Empty when nothing failed — the good case has no table.

    This is the half of the 2026-09-18 question that a rate cannot answer. "10% of RuleAuthor
    draws fail" and "10% fail because a regex does not compile" call for different decisions: the
    first is a reason not to spend eight rounds, the second is a one-line prompt edit. The site
    column is what keeps the class honest, since a class is a substring match on a message and a
    message can be reworded; a site is a file and a line.
    """
    failed = [r for r in _observations(rows)
              if r["outcome"] in ("format_failure", "truncated")]
    if not failed:
        return []
    out = ["", "실패한 draw 의 분류 — 어떤 검사가 거절했는가 (표면형 없음):", "",
           "| probe | n | error_class | 건수 | raise 위치 |", "|---|---|---|---|---|"]
    for name in dict.fromkeys(r["probe"] for r in failed):
        mine = [r for r in failed if r["probe"] == name]
        classes = _tally(r.get("error_class") or r.get("error_type") for r in mine)
        for cls, count in classes.items():
            sites = _tally(r.get("error_site") for r in mine
                           if (r.get("error_class") or r.get("error_type")) == cls)
            out.append(f"| {name} | {len(mine)} | `{cls}` | {count} "
                       f"| {' · '.join(f'`{s}` ×{c}' for s, c in sites.items()) or '—'} |")
    return out


def _lexicon_table(rows: list[dict]) -> list[str]:
    """What the LexiconBuilder offered against what survived, and why the rest did not.

    Separate from `_rate_table` because this role's `validated` is the weakest of the five: the
    validator drops refused files and entries and returns what is left, so one surviving list out
    of nine reads as a pass. The columns are the three questions asked of this artefact on
    2026-09-18 — the shape, the vocabulary, and whether §6.7.5's `basis` is there — plus the
    residue classes, and `basis 없음`/`basis 미선언` are counted over the *offer* so that a file
    dropped for another reason still reports whether it carried a discriminator.
    """
    mine = [r for r in _observations(rows) if r["probe"] == "lexicon_builder"]
    if not mine:
        return []
    keys = ("offered_files", "offered_declared_names", "files", "terms",
            "refused", "basis_absent", "basis_undeclared")
    out = ["", "LexiconBuilder — 제시된 파일과 살아남은 파일 (draw 별 중앙값, n=%d):" % len(mine),
           "",
           "| 제시 | 이름 선언됨 | 통과 | 항목 수 | 거절 | basis 없음 | basis 미선언 |",
           "|---|---|---|---|---|---|---|",
           "| " + " | ".join(_median(mine, key) for key in keys) + " |"]
    classes = _tally(cls for r in mine for cls, n in (r.get("by_class") or {}).items()
                     for _ in range(n))
    bases = _tally(b for r in mine for b, n in (r.get("offered_bases") or {}).items()
                   for _ in range(n))
    out += ["",
            "거절 분류 합산: " + (" · ".join(f"`{c}` {n}" for c, n in classes.items()) or "없음"),
            "",
            "제시된 basis 값 합산 (naming.yaml 어휘만): "
            + (" · ".join(f"`{b}` {n}" for b, n in bases.items()) or "없음")]
    return out


def _shape_table(rows: list[dict]) -> list[str]:
    """Fenced draws against clean ones, on the four things a correlate could live in.

    Empty when no probe produced both kinds — a table of one column invites a comparison that
    was not made. The medians are over the *body* (any fence stripped), which is the only way
    the two groups are comparable at all: a fenced response is longer by the fence.
    """
    seen = _observations(rows)
    groups = {True: [r for r in seen if r.get("fenced")],
              False: [r for r in seen if not r.get("fenced")]}
    if not groups[True] or not groups[False]:
        return []
    out = ["", "펜스가 난 draw 와 안 난 draw (모든 probe 합산, 본문 기준 중앙값):", "",
           "| | n | body 문자수 | 깊이 | dict 키 수 | 리스트 항목 수 | completion 토큰 |",
           "|---|---|---|---|---|---|---|"]
    for fenced, label in ((True, "펜스"), (False, "펜스 없음")):
        mine = groups[fenced]
        out.append(f"| {label} | {len(mine)} | " + " | ".join(
            _median(mine, key) for key in
            ("body_chars", "depth", "total_keys", "list_items", "completion_tokens")) + " |")
    out.append("")
    out.append("probe 별로도 갈라 보려면 아래 draw 기록을 읽어라 — 합산 표는 역할 간 차이를 "
               "역할 내 차이로 보이게 할 수 있다.")
    return out


def _median(rows: list[dict], key: str) -> str:
    values = sorted(r[key] for r in rows if r.get(key) is not None)
    if not values:
        return "—"
    return str(values[len(values) // 2])


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
    parser.add_argument("--only", choices=sorted(PROBES), nargs="+", default=None,
                        help="run these probes instead of all five")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="write the rows to this path after every draw, so a run of a "
                             "hundred draws that dies at ninety keeps the ninety. The note is "
                             "still appended at the end, and only on a run that finished.")
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

    names = list(dict.fromkeys(args.only)) if args.only else list(PROBES)

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
            if args.json_path:
                Path(args.json_path).write_text(
                    json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{name:16} draw {draw:>3}  {row['outcome']:15} "
                  f"{'fenced ' + (row.get('fence_tag') or '') if row.get('fenced') else '':12} "
                  f"{row.get('error_type') or ''}")

    block = render(rows, model_id=args.model_id, date=today(), reason=args.reason)
    append_to_note(block, ROOT / NOTE)
    print(f"\nappended to {NOTE}")
    # The exit code reports the measurement. Nothing consults it, but a probe that returned 0 on
    # a format failure would be one more thing reading green when it is not. A rate run is
    # expected to be non-zero as soon as one draw fences, which is the point of running it.
    return 0 if all(r["outcome"] in ("loaded", "parsed", "validated")
                    for r in _observations(rows)) else 1


if __name__ == "__main__":
    sys.exit(main())
