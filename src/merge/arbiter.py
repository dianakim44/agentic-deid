"""The Arbiter: an agent that picks a merge policy from dev aggregates. DESIGN §12.3.

The project's third LLM agent, after the RuleAuthor and the Auditor, and the first that
never sees a document. Its input is the per-type table `selector.build_table()` produces;
its output is one `merge_policy` per type.

**The text-blocking guard is the substance of this module, not a precaution attached to
it.** Every other agent in this project is sent corpus text on purpose — the RuleAuthor
gets error samples, the Auditor gets masked documents — and the protocols around them are
about what may be sent. The Arbiter's protocol is that *nothing* may be. That makes the
guard checkable in a way the others' are not: the prompt's data half is assembled from
numbers, the ten type names and fixed strings, so a whitelist over its alphabetic tokens is
a complete check rather than a heuristic.

The guard runs on both paths:

  - **Outbound**, before any call. `check_frame()` refuses an unknown alphabetic token and
    `assemble()` refuses a template whose hash is not the registered one. If either
    refuses, no call is made (DESIGN §12.3) — not "the call is logged and flagged".
  - **Inbound**, on the `rationale` field. It is the one free-text field in the protocol
    and so the one place a surface could enter the record.

Nothing here logs the filled prompt. It is a `FilledPrompt`, whose text-bearing exits are
named (DESIGN §5.4), and the call log gets the reference form.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from ..corpora.base import CorpusError, canonical_types, corpus_ids, naming
from ..llm.prompt import FilledPrompt
from . import policy as pol
from . import selector as sel

TEMPLATE = Path("docs/prompts/arbiter.md")

#: The registered SHA-256 of the template (experiments/hybrid_v24/registration.yaml).
#: Pinned here as well as there so the check runs without reading the registration, and
#: `tests/test_arbiter.py` asserts the two agree — a hash in one place is a hash nobody
#: compares.
TEMPLATE_SHA256 = "eaa0ded90e1ab9b54bb8b089a1d0854d140867c75bee5424d71b23aefd251147"

INPUT_BANNER = "--- input: dev aggregates for one corpus ---"

#: Every alphabetic token the assembled data half is allowed to contain, beyond the ten
#: type names, the four policy names and the corpus ids (which are read from the axes).
#:
#: **Written out, and that is the point.** A guard whose vocabulary is derived from the
#: frame it checks passes by construction. These are the field labels this module emits,
#: so adding a row to the frame means adding its label here, in a diff, which is where a
#: decision to transmit a new kind of value should be visible.
#:
#: Secondary to `FRAME_LINES` below, which is the complete check. Kept because it gives a
#: better message for the common case — a stray word — than "this line matches no form".
FRAME_WORDS = frozenset("""
corpus documents delta baseline type gold sparse policy theta candidates
tp fp fn leaked pred rate masked nongold complementarity rules tagger both neither only
round previous achieved constraint satisfied change improved
true false none null yes no na
L_full L_rel A precision recall
""".split())

#: The **complete** grammar of the data half: every line this module emits matches exactly
#: one of these, and a line matching none is refused.
#:
#: This replaced a token whitelist plus a "no run of four or more digits" heuristic, and
#: the heuristic is worth recording because it was wrong in both directions. It was
#: defeated by this module's own output — `delta 0.005000` contains a six-digit run — and
#: it would also have refused legitimate data, because a per-type gold count on
#: `es-meddocan` reaches four digits. A check that both passes what it should catch and
#: catches what it should pass is not a weak check, it is a check whose result means
#: nothing.
#:
#: A grammar has neither failure. The frame is assembled by this module from numbers it
#: formats itself, so the set of line forms is closed and small, and any injected text
#: would have to match a declared form — which admits only declared labels, axis values
#: and numbers. That is why there is no digit-run rule here at all: a number in a numeric
#: position is data, and a number anywhere else does not match a form.
_INT = r"\d+"
_NUM = r"-?\d+\.\d+"
_SIGNED = r"[+-]\d+\.\d+"
_YESNO = r"(?:yes|no)"


def _frame_line_patterns() -> list[str]:
    """Built from the axes so a new corpus or type needs no edit here."""
    types = "|".join(re.escape(t) for t in canonical_types())
    corpora = "|".join(re.escape(c) for c in corpus_ids())
    policies = "|".join(re.escape(p) for p in pol.policy_names())
    thetas = "|".join(re.escape(f"{t}") for t in pol.theta_grid())
    choice = rf"(?:{policies})(?:@(?:{thetas}))?"
    return [
        rf"corpus (?:{corpora})",
        rf"documents {_INT}",
        rf"delta {_NUM}",
        rf"baseline L_full {_NUM} L_rel {_NUM} A {_NUM}",
        rf"nongold masked rate {_NUM}",
        rf"previous round {_INT} achieved L_full {_NUM} A {_NUM} "
        rf"constraint satisfied {_YESNO}",
        rf"previous change (?:{types}) A {_SIGNED}",
        rf"type (?:{types}) gold {_INT} sparse {_YESNO}",
        rf"  complementarity rules only {_INT} tagger only {_INT} both {_INT} "
        rf"neither {_INT}",
        rf"  policy {choice} tp {_INT} fp {_INT} fn {_INT} L_full {_NUM} "
        rf"L_rel {_NUM} A {_NUM}",
        r"",
    ]


class ArbiterError(CorpusError):
    """A guard refused, or a response could not be read as a policy map."""


def template_text() -> str:
    """The template bytes, hash-checked against the registration.

    Checked on every read rather than once at import: the check's purpose is that the
    bytes sent are the bytes registered, and an import-time check leaves a window in which
    the file is edited between import and call.
    """
    if not TEMPLATE.is_file():
        raise ArbiterError(
            f"{TEMPLATE} is not on disk. The Arbiter's template is committed and its "
            "SHA-256 is registered (DESIGN §12.3); there is no fallback template, "
            "because a call under an unregistered frame is not the pre-registered arm."
        )
    raw = TEMPLATE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != TEMPLATE_SHA256:
        raise ArbiterError(
            f"{TEMPLATE} hashes to {digest[:16]}… and the registration pins "
            f"{TEMPLATE_SHA256[:16]}…. The template moved after 2026-10-14. Refused "
            "rather than sent: the arm's identity is the frame it was called under, so "
            "amend DESIGN §12.3 and the registration together, or restore the file."
        )
    return raw.decode("utf-8")


#: Alphabetic tokens. **Hyphens are inside a token**, so `es-meddocan` is one token and
#: not `es` plus `meddocan` — which is what it was until the guard's first test run, and
#: the effect was that every real frame was refused for carrying its own corpus id. A
#: hyphen-joined surface (`Hospital-Clinic`) is still one undeclared token and still
#: refused, so widening the token does not weaken the check.
_WORD = r"[A-Za-z][A-Za-z_-]*"


def allowed_words() -> frozenset[str]:
    return (FRAME_WORDS
            | frozenset(canonical_types())
            | frozenset(pol.policy_names())
            | frozenset(corpus_ids()))


def check_frame(frame: str) -> None:
    """Refuse a data half that is anything but this module's own declared line forms.

    Three checks, in the order that gives the most specific message first:

      1. **Non-ASCII is refused outright.** Four of the five corpora are written in
         Spanish, German, Korean and English, and three of those scripts are not ASCII.
         A frame of numbers and declared labels has no reason to contain a non-ASCII
         character, so this needs no judgement about which characters are suspicious.
      2. **Every alphabetic token must be declared** — the ten type names, the policy
         names, the corpus ids, or a `FRAME_WORDS` label. A Latin-script surface is ASCII
         and passes check 1; it does not pass this.
      3. **Every line must match a `FRAME_LINES` form.** This is the complete check and
         the other two are for the message: a line that matches no form is refused
         whatever its tokens, so a surface assembled entirely out of declared words
         (`type gold both neither`) is caught here and nowhere else.

    Raises on the first failure, naming the offending token's *position and length* and
    never the token itself. A guard that quotes what it caught publishes it into the log,
    which is the exact harm it exists to prevent.
    """
    for index, char in enumerate(frame):
        if ord(char) > 127:
            raise ArbiterError(
                f"the Arbiter frame has a non-ASCII character at offset {index} "
                f"(Unicode category {unicodedata.category(char)}). The frame is numbers "
                "and declared labels; a non-ASCII character in it is corpus text or a "
                "label nobody declared. No call was made."
            )
    permitted = allowed_words()
    for match in re.finditer(_WORD, frame):
        if match.group() not in permitted:
            raise ArbiterError(
                f"the Arbiter frame has an undeclared alphabetic token at offset "
                f"{match.start()}, length {len(match.group())}. Only the ten phi_type "
                "names, the merge_policy names, the corpus ids and the declared field "
                "labels (FRAME_WORDS) may appear. The token is not quoted here on "
                "purpose. No call was made."
            )
    forms = [re.compile(f"^{pattern}$") for pattern in _frame_line_patterns()]
    for number, line in enumerate(frame.split("\n"), start=1):
        if not any(form.match(line) for form in forms):
            raise ArbiterError(
                f"line {number} of the Arbiter frame ({len(line)} characters) matches "
                "none of the declared line forms. The frame's grammar is closed "
                "(FRAME_LINES): every line is a declared label followed by numbers, "
                "axis values or yes/no. The line is not quoted here on purpose. "
                "No call was made."
            )


def frame_numbers(frame: str) -> frozenset[str]:
    """Every numeric literal in the frame, as written.

    The permitted vocabulary for a rationale's numbers (`check_rationale`). "Numbers
    only" is only a checkable rule if *which* numbers is answerable, and the answer is
    the ones the agent was given.
    """
    return frozenset(re.findall(r"\d+(?:\.\d+)?", frame))


def build_frame(*, corpus: str, n_dev_docs: int, delta: float,
                table: dict[str, sel.TypeRow],
                baseline: dict | None = None,
                previous: dict | None = None) -> str:
    """The data half: the per-type table as declared labels and numbers.

    Types with no dev gold are **absent**, because DESIGN §12.2 fixes them at the RT
    baseline before any chooser is called. Showing them with `n_gold 0` would invite a
    policy for a type the harness is not going to take one for.
    """
    lines = [
        f"corpus {corpus}",
        f"documents {n_dev_docs}",
        f"delta {delta:.6f}",
    ]
    if baseline is not None:
        lines.append(
            f"baseline L_full {baseline['L_full']:.6f} L_rel {baseline['L_rel']:.6f} "
            f"A {baseline['A']:.6f}")
    if baseline is not None and "nongold_masked_rate" in baseline:
        lines.append(f"nongold masked rate {baseline['nongold_masked_rate']:.6f}")
    if previous is not None:
        lines.append(
            f"previous round {previous['round']} achieved L_full "
            f"{previous['L_full']:.6f} A {previous['A']:.6f} constraint satisfied "
            f"{'yes' if previous['constraint'] else 'no'}")
        for phi_type, change in sorted(previous.get("per_type_change", {}).items()):
            lines.append(f"previous change {phi_type} A {change:+.6f}")

    for phi_type, row in sorted(table.items()):
        if not row.n_gold:
            continue
        lines.append("")
        lines.append(f"type {phi_type} gold {row.n_gold} sparse "
                     f"{'yes' if row.sparse else 'no'}")
        comp = row.complementarity
        lines.append(
            f"  complementarity rules only {comp['rules_only']} tagger only "
            f"{comp['tagger_only']} both {comp['both']} neither {comp['neither']}")
        for name, counts in sorted(row.candidates.items()):
            lines.append(
                f"  policy {name} tp {counts.tp} fp {counts.fp} fn {counts.fn} "
                f"L_full {counts.L_full:.6f} L_rel {counts.L_rel:.6f} "
                f"A {counts.A:.6f}")
    return "\n".join(lines)


def assemble(*, corpus: str, n_dev_docs: int, delta: float,
             table: dict[str, sel.TypeRow], baseline: dict | None = None,
             previous: dict | None = None) -> FilledPrompt:
    """Template plus banner plus checked frame. Refuses rather than sends.

    The frame is checked **before** it is joined to the template, so a refusal happens
    while the offending text is still a local and has never been part of a prompt object.
    """
    frame = build_frame(corpus=corpus, n_dev_docs=n_dev_docs, delta=delta,
                        table=table, baseline=baseline, previous=previous)
    check_frame(frame)
    text = "\n\n".join([template_text(), INPUT_BANNER, frame])
    return FilledPrompt(text, {
        "block": "arbiter",
        "corpus": corpus,
        "round": None if previous is None else previous["round"] + 1,
        "types_shown": sorted(t for t, row in table.items() if row.n_gold),
        "types_fixed_at_baseline": sorted(
            t for t, row in table.items() if not row.n_gold),
        "template_sha256": TEMPLATE_SHA256,
        "frame_chars": len(frame),
        "frame_lines": frame.count("\n") + 1,
        # The guard's verdict, recorded on the prompt that passed it. A guard whose
        # result is not in the record is a guard a reader has to take on trust.
        "frame_guard": "passed",
    })


#: Words a one-sentence rationale may use beyond the type names, policy names and numbers.
#:
#: The instruction's rule is that a rationale carries "type names and numbers only", which
#: **no English sentence satisfies literally** — and the protocol asks for a sentence. So
#: the operative rule is the one that rule is for: nothing that could have come from a
#: document. These are ordinary function, comparison and measurement words with no referent
#: in any corpus.
#:
#: A closed list for a free-text field is a strong constraint and it will reject some
#: legitimate phrasings. That is the intended direction: a rejected rationale is a format
#: failure, the protocol retries the round once (DESIGN §12.3), and the cost of being wrong
#: this way is one call. The cost of being wrong the other way is a surface in a committed
#: record.
RATIONALE_WORDS = frozenset("""
a an and are as at be been being below both but by can cannot could did do does
each either else even every few for from had has have here how however if in into is it
its just least less many may might more most much must neither no none nor not now of
off on once one only or other others our out over own per rather same she so some such
than that the their them then there these they this those though three through thus to
too two under unless until up upon very was were what when where whether which while who
whom whose why will with within without would yet
above absolute absolutely across after again against almost along already also always
among amount another any anything apart appear appears applied applies apply approach
because become becomes before behind best better beyond bring brings
accuracy agree agreement agrees baseline both_sources budget candidate candidates
ceiling choice choose chooses chose chosen cell cells coarse combination complement
complementarity confidence conservative constraint contribute contributes contribution
correct cost costs count counts cover coverage covered covers
decision decrease decreased decreases degrade degrades delta detector detectors
difference differences different discard discarded discards distribution drop dropped
drops effect enough entity equal equally error errors evidence exact exceed exceeds
expect expected
fail fails false favour favours fewer filter filtered filters fine fires floor
gain gains gold greater
half header high higher highest hold holds
identical improve improved improvement improves increase increased increases
indistinguishable informative instead insufficient
keep keeping keeps
large larger largest leak leaked leaks level limit limited little long loss low lower
lowest
margin marginal match matched matches measure measured measurement minimal minor
miss missed misses mode modest move moves
negative negatives neither_source noise noisy noticeable
observation observations outcome outperform outperforms outside overlap overlapping
overlaps
pair pairs partial pay pays penalty policies policy poor positive positives precision
precise prefer preferable preferred prefers present preserve preserves produce produces
proportion
quality quantity
raise raises rate rates ratio recall reduce reduced reduces regime reliable
remain remains remove removed removes residual retain retained retains risk
safe safer same_as score scores second select selected selects sensitive share
signal significant single small smaller smallest source sources span spans sparse
sparsity stable strict strong stronger substantial sufficient support supports suppress
suppresses
tagger_only rules_only threshold thresholds tie tighter tiny trade tradeoff
unchanged unclear undercount undercounts uninformative union unreliable unstable
unsupported useful
value values volume weak weaker worse worst
zero
L_full L_rel A theta TP FP FN
""".split())


#: The longest rationale accepted. One sentence (DESIGN §12.3, template §3), and a cap on
#: a free-text field is a cap on how much could leave through it. Generous rather than
#: tight: the purpose is to make a paragraph of prose a refusal, not to police style.
RATIONALE_MAX_CHARS = 300


def check_rationale(text: str, *, phi_type: str,
                    allowed_numbers: frozenset[str] | None = None) -> set[str]:
    """Check a rationale for surface risk. Returns the words outside the declared set.

    **Two kinds of finding, and they are deliberately not treated alike.** The first
    version of this function refused on all of them, and the word list rejected the word
    "kept" on its first run. In the live loop that behaviour costs the arm rather than the
    sentence: a rejected rationale is a format failure, the protocol retries the round
    once, and a second rejection falls back to the previous policy (DESIGN §12.3). Burning
    an arm's rounds on English morphology would be a guard that damages the experiment it
    protects while catching nothing — so what refuses is now only what indicates a
    surface.

    **Refused** — each is a positive signal that text got in, and the Arbiter was shown
    none, so each is a protocol violation rather than a stylistic one:

      1. **Non-ASCII.** Three of the five corpora are written in non-ASCII scripts, and
         Spanish and German surfaces carry accents. Nothing in a rationale about types and
         rates needs a non-ASCII character.
      2. **A capitalised word that is not an axis value and not sentence-initial.** The
         strongest single signal of a person, hospital or place name. Checked
         independently of the vocabulary because the dangerous case *is* in the
         vocabulary: `Low`, `Best` and `Bell` are surnames whose lower-case forms are
         ordinary words, so a case-folded list alone would pass them.
      3. **A number the frame did not supply.** `allowed_numbers` is `frame_numbers()` of
         the frame this call sent, which is what makes "numbers only" checkable — *which*
         numbers is answerable, and the answer is the ones the agent was given. Record
         numbers, dates, postcodes and phone numbers are the numeric surfaces, and none
         of them is in the input. `None` skips this check, for a caller testing a
         rationale in isolation; it is a parameter rather than a default so that a skipped
         check does not look like a passed one.
      4. **Length.** Over `RATIONALE_MAX_CHARS` is not one sentence.

    **Recorded, not refused** — words outside `RATIONALE_WORDS`. A lower-case ASCII
    English word that is not in the list is almost always a morphological variant of one
    that is, and it cannot be a copied surface because there was nothing to copy from.
    They come back so the caller can log them, and a rationale whose undeclared-word
    count is high is worth a human's eye without being worth an arm's round.

    The offending token is never quoted. Offset and length locate it.
    """
    if len(text) > RATIONALE_MAX_CHARS:
        raise ArbiterError(
            f"the rationale for {phi_type} is {len(text)} characters and the protocol "
            f"asks for one sentence (limit {RATIONALE_MAX_CHARS}). A free-text field's "
            "length is how much can leave through it. Not quoted."
        )
    for index, char in enumerate(text):
        if ord(char) > 127:
            raise ArbiterError(
                f"the rationale for {phi_type} has a non-ASCII character at offset "
                f"{index} (category {unicodedata.category(char)}). Refused: the "
                "rationale is the one free-text field in this protocol and the "
                "Arbiter was shown no text to copy, so a non-ASCII character in it "
                "is invented or leaked. Not quoted here."
            )
    axis_words = (set(canonical_types()) | set(pol.policy_names())
                  | set(corpus_ids()))
    declared = ({w.lower() for w in RATIONALE_WORDS}
                | {w.lower() for w in axis_words})
    undeclared: set[str] = set()
    for match in re.finditer(_WORD, text):
        word = match.group()
        if (word[0].isupper() and match.start() != 0
                and word not in axis_words):
            raise ArbiterError(
                f"the rationale for {phi_type} has a capitalised word at offset "
                f"{match.start()}, length {len(word)}, which is not a type name, a "
                "policy name or a corpus id. A capitalised word is the strongest "
                "single signal of a person, place or institution name. Not quoted."
            )
        if word.lower() not in declared:
            undeclared.add(word.lower())
    if allowed_numbers is not None:
        for match in re.finditer(r"\d+(?:\.\d+)?", text):
            if match.group() not in allowed_numbers:
                raise ArbiterError(
                    f"the rationale for {phi_type} cites a "
                    f"{len(match.group())}-character number at offset "
                    f"{match.start()} that does not appear in the frame this call "
                    "sent. A figure the agent was not given was not computed from "
                    "the input — it is invented or copied from elsewhere. Not quoted."
                )
    return undeclared


@dataclass(slots=True)
class Proposal:
    """One parsed, validated response."""

    choices: dict[str, pol.Choice]
    rationales: dict[str, str]
    #: Words in the rationales outside `RATIONALE_WORDS` — recorded, not refused
    #: (`check_rationale`). Published so that "the guard passed" is a statement with a
    #: number beside it rather than a bare claim.
    undeclared_words: set[str] = field(default_factory=set)

    def as_record(self) -> dict:
        return {
            "policies": pol.as_record(self.choices),
            "rationales": dict(sorted(self.rationales.items())),
            "rationale_undeclared_words": sorted(self.undeclared_words),
        }


def parse(payload: str, *, expected: list[str],
          allowed_numbers: frozenset[str] | None = None) -> Proposal:
    """Read a response into a `Proposal`, or raise `ArbiterError`.

    `expected` is the list of types the frame actually showed. Both directions are
    refused: a missing type and an extra one. A missing type is not defaulted for
    `policy.apply_map`'s reason, and an extra one is a type the agent invented — which
    on this protocol means it is answering about something it was not shown.
    """
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ArbiterError(
            f"the response is not JSON (line {exc.lineno}, column {exc.colno}). "
            f"{len(payload)} characters received. Format failure."
        ) from None
    if not isinstance(obj, dict):
        raise ArbiterError(
            f"the response parsed as {type(obj).__name__} and the protocol is one "
            "JSON object keyed by type name. Format failure."
        )
    missing = sorted(set(expected) - set(obj))
    extra = sorted(set(obj) - set(expected))
    if missing or extra:
        raise ArbiterError(
            f"the response names {len(obj)} types and the frame showed "
            f"{len(expected)}. Missing: {missing}. Not shown: {extra}. Both are "
            "refused — a missing type would be silently defaulted to the RT baseline, "
            "and a type that was not shown is one the agent was not asked about."
        )
    choices: dict[str, pol.Choice] = {}
    rationales: dict[str, str] = {}
    undeclared: set[str] = set()
    for phi_type, entry in obj.items():
        if not isinstance(entry, dict):
            raise ArbiterError(
                f"the entry for {phi_type} is {type(entry).__name__} and the protocol "
                "is an object with policy, theta and rationale."
            )
        unknown = sorted(set(entry) - {"policy", "theta", "rationale"})
        if unknown:
            raise ArbiterError(
                f"the entry for {phi_type} carries unknown field(s) {unknown}. The "
                "protocol names three and the response may not widen it."
            )
        for required in ("policy", "theta", "rationale"):
            if required not in entry:
                raise ArbiterError(
                    f"the entry for {phi_type} has no {required!r}. All three are "
                    "required; `theta` is null for policy R and absent for nothing."
                )
        if not isinstance(entry["rationale"], str):
            raise ArbiterError(
                f"the rationale for {phi_type} is "
                f"{type(entry['rationale']).__name__} and must be a string."
            )
        undeclared |= check_rationale(entry["rationale"], phi_type=phi_type,
                                      allowed_numbers=allowed_numbers)
        # `Choice` validates the policy name, the grid membership and the R/θ pairing,
        # and raises CorpusError. Re-raised as ArbiterError so the loop's one retry
        # covers a bad policy the same way it covers bad JSON — both are the response
        # failing to meet the protocol, and the loop's rule is about the response.
        try:
            choices[phi_type] = pol.Choice(policy=entry["policy"],
                                           theta=entry["theta"])
        except CorpusError as exc:
            raise ArbiterError(f"{phi_type}: {exc}") from None
        rationales[phi_type] = entry["rationale"]
    return Proposal(choices=choices, rationales=rationales,
                    undeclared_words=undeclared)


def fill_absent(choices: dict[str, pol.Choice],
                table: dict[str, sel.TypeRow]) -> dict[str, pol.Choice]:
    """Add the no-gold types back at the RT baseline (DESIGN §12.2).

    The frame does not show them and the agent does not answer for them, so this is where
    they re-enter — explicitly, after the response, rather than as a default inside
    `apply_map`. The distinction is the whole of §12.2's note: the same value, with a
    record of the decision instead of without one.
    """
    out = dict(choices)
    for phi_type, row in table.items():
        if not row.n_gold:
            out[phi_type] = pol.rt_baseline()
    return out
