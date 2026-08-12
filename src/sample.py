"""Deterministic error-span sampling for the RuleAuthor prompt and `port-human`.

One function decides which errors a rule author — agent or person — is shown at a
given iteration, and it is here rather than in the prompt renderer or the
orchestrator because DESIGN §11.1 makes the *drawing procedure* the thing the two
arms must share. A procedure implemented twice is two procedures.

**The seed is derived from the iteration number and nothing else that varies.**
`sample_seed()` hashes a fixed scheme string, the base seed from
`config/sampling.yaml`, the corpus, and the iteration. Not the wall clock, not the
call order, not a module-level `Random` whose state depends on what ran before. Those
three are the failure this module exists to prevent, and they fail quietly: a sample
drawn from a shared RNG is perfectly reproducible in isolation and irreproducible in
the run that matters, because the run that matters called something else first.

**What differs between arms and what may not.** The `port-loop` and `port-human`
arms draw from *their own* current errors, so the spans they see at iteration 3 differ
— that is the experiment. The procedure that turns an error list into a sample must
be byte-identical, and the seed must not depend on the arm. Both arms at iteration 3
call `sample_seed(corpus, 3)` and get the same number, so every difference in the
resulting sample is traceable to the error lists and to nothing else. An arm-dependent
seed would leave a difference nobody could attribute, and it would look like an
ordinary implementation detail (`hash(arm) ^ iteration` reads as good practice).

Sampling never touches corpus text. It works on `(doc_id, span_index)` references and
offsets, the same referent DESIGN §11.2 fixes for `human_log.jsonl`; the ±120
characters of context are attached later, by the renderer, and never persist to disk
(docs/prompts/rule_author.md §7).
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Sequence

import yaml

from .corpora.base import ROOT, CorpusError, axis, canonical_types, corpus_ids

CONFIG = ROOT / "config" / "sampling.yaml"

#: The two error kinds the scorer distinguishes. Not a `naming.yaml` axis: these are
#: names for scorer outcomes, in the same register as `tp` / `fp` / `leaked`, not
#: experiment identifiers or span provenance. They are fixed here so a caller cannot
#: introduce a third kind that silently forms its own stratum.
MISSED = "missed"
FALSE_POSITIVE = "false_positive"
ERROR_KINDS = (MISSED, FALSE_POSITIVE)


class SamplingError(CorpusError):
    """A sampling request that cannot be honoured as specified.

    Subclasses CorpusError so the existing "stop and tell a human" handling applies:
    every case here means the caller and the config disagree, and there is no sample
    that is a defensible substitute for the one that was asked for.
    """


@lru_cache(maxsize=1)
def config() -> dict:
    """The contents of config/sampling.yaml, validated.

    Validated on read rather than at use sites: `n_error_spans` reaching a prompt as
    a string or a float would produce a sample of the wrong size in a way the prompt
    cannot notice, and the number is reported as an experimental parameter.
    """
    with open(CONFIG, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    for key in ("n_error_spans", "context_chars", "min_per_type", "base_seed",
                "practice_iteration_min"):
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SamplingError(
                f"config/sampling.yaml: {key!r} must be a non-negative integer, "
                f"got {type(value).__name__}. It is an experimental parameter and "
                "is recorded in the results, so it is validated here rather than "
                "coerced at the point of use."
            )
    scheme = raw.get("seed_scheme")
    if not isinstance(scheme, str) or not scheme:
        raise SamplingError(
            "config/sampling.yaml: 'seed_scheme' must be a non-empty string. It "
            "enters the seed derivation, so an absent value would make every seed "
            "collide with whatever scheme comes next."
        )
    return raw


def practice_min() -> int:
    """The lowest iteration number reserved for practice. From the config, not here.

    A rehearsal is necessary — an author has to learn the rule-file schema, the layer
    syntaxes, and the feedback command before iteration 1, and learning those on
    iteration 1's sample spends the sample. But a rehearsal that reuses a real iteration
    number is not a rehearsal: iteration 1 drawn "just to look" *is* iteration 1, and the
    window is then open before the arm has started.

    So the band is declared, and both directions are refused (`check_iteration`). Two
    numbers with two meanings — a real iteration and a practice one — that a caller can
    typo between is the kind of ambiguity this repository has already been bitten by.
    """
    return config()["practice_iteration_min"]


def is_practice(iteration: int) -> bool:
    """Whether `iteration` is in the reserved practice band."""
    return iteration >= practice_min()


def check_iteration(iteration: int, *, practice: bool) -> None:
    """Refuse an iteration number that disagrees with what the caller says it is.

    Both directions, and the second is the one that matters. Refusing a real arm the
    900 band stops a rehearsal's numbers being reported as a run. Refusing a practice
    caller a number *below* the band stops the opposite and more likely accident: a
    rehearsal that draws iteration 1 has consumed iteration 1's sample, which is the one
    thing a rehearsal exists not to do, and nothing downstream would show it — the draw
    is seeded, so the sample is byte-identical to the real one and looks perfectly
    correct.

    The flag is passed by the caller rather than inferred from the number, because
    inferring it is what makes the mistake unobservable: a function that decides "900,
    so this must be practice" cannot tell a rehearsal from a real arm that typed 900,
    and a function that decides "1, so this must be real" cannot tell iteration 1 from a
    rehearsal aimed at it. The caller knows which it is; the number cannot.
    """
    if not isinstance(iteration, int) or isinstance(iteration, bool):
        raise SamplingError(f"iteration must be an integer, got {iteration!r}")
    low = practice_min()
    if practice and iteration < low:
        raise SamplingError(
            f"iteration {iteration} is not in the practice band (>= {low}, "
            "config/sampling.yaml: practice_iteration_min). A rehearsal drawing a real "
            "iteration's number consumes that iteration's sample: the draw is seeded, "
            "so what it prints is byte-identical to the real window and nothing "
            "afterwards can show it was read early. Practice runs use the reserved "
            "band."
        )
    if not practice and iteration >= low:
        raise SamplingError(
            f"iteration {iteration} is in the band reserved for practice (>= {low}, "
            "config/sampling.yaml: practice_iteration_min), so an arm may not draw it. "
            "A rehearsal's numbers must not be reportable as a run — DESIGN §11.1's "
            "memory carry-over asymmetry is what a practice session adds to, and it is "
            "recorded in docs/notes/port-human-practice.md rather than in the arm's log."
        )


@dataclass(frozen=True)
class ErrorSpan:
    """One scorer error, by reference. No corpus text, by construction.

    `span_index` is the index within the document's gold span list for a missed span
    and within its prediction list for a false positive — the referent DESIGN §11.2
    fixes for `human_log.jsonl`, resolvable by anyone holding the corpus and inert to
    anyone who does not. Offsets are carried so the renderer can cut the context
    window without re-deriving them; they are not text either.

    There is deliberately no `text` field. A field that could hold a surface form gets
    filled with one, and this object is what a sample is made of — it travels into
    prompt assembly, into logs, and into whatever someone writes while debugging.
    """

    doc_id: str
    span_index: int
    phi_type: str
    kind: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.phi_type not in canonical_types():
            raise SamplingError(
                f"{self.phi_type!r} is not a phi_type in config/naming.yaml. "
                "Stratification is by canonical type, so an undeclared value would "
                "form a stratum of its own and be reported as a type."
            )
        if self.kind not in ERROR_KINDS:
            raise SamplingError(
                f"{self.kind!r} is not an error kind (expected one of "
                f"{list(ERROR_KINDS)})."
            )
        if self.span_index < 0 or self.start < 0 or self.end < self.start:
            raise SamplingError(
                f"malformed span reference in {self.doc_id!r} at index "
                f"{self.span_index}: offsets ({self.start}, {self.end}). "
                "No surface form is quoted here (CLAUDE.md)."
            )

    @property
    def key(self) -> tuple:
        """Canonical sort key. Total over any set of distinct references.

        Sorting before drawing is what makes the sample independent of the order the
        scorer happened to emit errors in. Without it the seed fixes the *choice of
        indices* and the caller's iteration order fixes which spans those indices
        land on, so a dict rebuild reshuffles the sample with the seed unchanged —
        reproducible in the log and different in fact.
        """
        return (self.doc_id, self.start, self.end, self.phi_type, self.kind,
                self.span_index)


def sample_seed(corpus: str, iteration: int) -> int:
    """The seed for `corpus` at `iteration`. A pure function of its arguments.

    SHA-256 of a delimited scheme string rather than Python's `hash()`: `hash()` is
    salted per process for strings, so a seed derived from it is stable within one run
    and different in the next — the exact defect this is written against, and one that
    a same-session test cannot see.

    The parts are joined with a delimiter that cannot occur in them, so
    (`es-carmen`, 12) and (`es-carmen1`, 2) cannot collide into one seed.
    """
    cfg = config()
    if corpus not in corpus_ids():
        raise SamplingError(
            f"{corpus!r} is not a corpus in config/naming.yaml (have: "
            f"{corpus_ids()}). The corpus enters the seed, so an unknown value "
            "would silently produce a valid-looking seed for a corpus that does "
            "not exist."
        )
    if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 1:
        raise SamplingError(
            f"iteration must be an integer >= 1, got {iteration!r}. Iteration 1 has "
            "no previous scorer output to sample, so it is the first value that can "
            "appear here and 0 would mean the caller is off by one."
        )
    material = "\x00".join([
        cfg["seed_scheme"], str(cfg["base_seed"]), corpus, str(iteration),
    ])
    return int.from_bytes(
        hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")


def _allocate(counts: dict[str, int], n: int, min_per_type: int) -> dict[str, int]:
    """Split `n` draws across types in proportion to `counts`. Deterministic.

    Largest-remainder apportionment after reserving `min_per_type` for every type
    that has any error at all. Proportional-only allocation rounds a type holding 1%
    of the errors to zero, and a type never shown is a type never fixed (DESIGN §9.4
    on sparse types); reserving first is what puts it in the sample.

    Ties in the remainder are broken by type name, not by iteration order over
    `counts`. A tie is not an edge case here — equal error counts across types are
    common at small n — so leaving it to dict order would make the allocation depend
    on how the caller built the dict.
    """
    types = sorted(t for t, c in counts.items() if c > 0)
    if not types:
        return {}
    quota = {t: min(min_per_type, counts[t]) for t in types}
    # A floor that cannot be met: the reserve alone exceeds n. Drop types rather than
    # returning a sample larger than asked for, and drop the rarest first so what
    # survives is what the aggregate scores already show.
    while sum(quota.values()) > n:
        victim = min(types, key=lambda t: (counts[t], t))
        types.remove(victim)
        del quota[victim]
        if not types:
            return {}

    remaining = n - sum(quota.values())
    available = {t: counts[t] - quota[t] for t in types}
    total = sum(available.values())
    if remaining <= 0 or total <= 0:
        return {t: q for t, q in quota.items() if q > 0}

    exact = {t: remaining * available[t] / total for t in types}
    for t in types:
        quota[t] += min(int(exact[t]), available[t])
    leftover = n - sum(quota.values())
    order = sorted(types, key=lambda t: (-(exact[t] - int(exact[t])), t))
    while leftover > 0:
        progressed = False
        for t in order:
            if leftover == 0:
                break
            if quota[t] < counts[t]:
                quota[t] += 1
                leftover -= 1
                progressed = True
        if not progressed:      # every type exhausted; the corpus has fewer than n
            break
    return {t: q for t, q in quota.items() if q > 0}


@lru_cache(maxsize=1)
def non_target_types() -> frozenset[str]:
    """Types no rule may target, read from `naming.yaml`'s own gloss.

    `OTHER` is declared there as "residual bucket shipped by a corpus; not a
    rule-development target", and `docs/prompts/rule_author.md` Prohibition 4 forbids
    writing a rule for it. A sample slot spent on such a type is a slot the author is
    forbidden to act on — with `min_per_type` guaranteeing one, `OTHER` would appear in
    every single iteration of every arm.

    Detected from the gloss rather than hardcoded as `{"OTHER"}`, because the
    prohibition is a fact naming.yaml already states and a second copy in Python is a
    second thing to keep in sync. A corpus that ships another residual bucket declares
    it the same way and is excluded without an edit here.

    This filters the *sample*, not the scoring: DESIGN §9.4 keeps every type in the
    leak-rate denominator, and a type nobody may write a rule for still counts against
    the arm. Excluding it from the window and excluding it from the metrics would be
    two very different claims.
    """
    return frozenset(
        name for name, gloss in axis("phi_type").items()
        if isinstance(gloss, str) and "not a rule-development target" in gloss)


def draw(errors: Iterable[ErrorSpan], corpus: str, iteration: int,
         *, n: int | None = None, practice: bool = False) -> list[ErrorSpan]:
    """The `n` error spans shown at `iteration`. Stratified by `phi_type`, seeded.

    Same errors, same corpus, same iteration -> same list, in the same order, in any
    process. Different arms at the same iteration run this same code with the same
    seed on their own error lists, which is the premise DESIGN §11.1 rests
    `port-human` on: at a given iteration the two arms drew by the same procedure, so
    a difference in what they saw is a difference in what they had got wrong.

    `n` overrides `config()["n_error_spans"]` for tests and for the DUA case where the
    agent's window is 0 on a corpus (rule_author.md §1.4); it is not a knob for a
    caller that wants a bigger sample, and the value used is recorded with the run.

    Errors on a non-rule-development type are dropped before drawing
    (`non_target_types()`), so `n` slots all go to types a rule may target.

    Returns fewer than `n` only when there are fewer eligible errors than that.

    `practice` is the caller's declaration that this is a rehearsal, and it is checked
    against the iteration number rather than derived from it (`check_iteration`). It
    reaches this function rather than only the caller because this is the one place both
    arms pass through: a guard at a call site guards that call site.
    """
    check_iteration(iteration, practice=practice)
    if n is None:
        n = config()["n_error_spans"]
    if n < 0:
        raise SamplingError(f"n must be non-negative, got {n}")
    blocked = non_target_types()
    pool = sorted((e for e in set(errors) if e.phi_type not in blocked),
                  key=lambda e: e.key)
    if not pool or n == 0:
        return []

    by_type: dict[str, list[ErrorSpan]] = {}
    for span in pool:
        by_type.setdefault(span.phi_type, []).append(span)

    quota = _allocate({t: len(v) for t, v in by_type.items()},
                      n, config()["min_per_type"])

    # One RNG per stratum, seeded from the run seed and the type name. A single RNG
    # walked across strata would make each type's draw depend on how many were drawn
    # before it, so adding an error to NAME would change which DATE spans appear —
    # a coupling that shows up as unexplained churn between iterations.
    out: list[ErrorSpan] = []
    for phi_type in sorted(quota):
        candidates = by_type[phi_type]
        take = min(quota[phi_type], len(candidates))
        material = f"{sample_seed(corpus, iteration)}\x00{phi_type}"
        rng = random.Random(
            int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8],
                           "big"))
        out.extend(rng.sample(candidates, take))
    return sorted(out, key=lambda e: e.key)


def provenance(corpus: str, iteration: int, *, n: int | None = None,
               practice: bool = False) -> dict:
    """What the run records about how the sample was drawn.

    Written beside the metrics so a sample can be reconstructed from the results
    alone. The seed is included as a value even though it is derivable, because
    checking a recorded number against a recomputed one is how a change of derivation
    scheme gets noticed; a record holding only the inputs agrees with any scheme.

    `practice` is recorded as a field rather than left implicit in the iteration number.
    A reader of a provenance block should not have to know the band's lower bound to
    know what they are looking at, and the bound is a config value that can move.
    """
    check_iteration(iteration, practice=practice)
    cfg = config()
    return {
        "practice": practice,
        "seed_scheme": cfg["seed_scheme"],
        "base_seed": cfg["base_seed"],
        "corpus": corpus,
        "iteration": iteration,
        "seed": sample_seed(corpus, iteration),
        "n_error_spans": cfg["n_error_spans"] if n is None else n,
        "context_chars": cfg["context_chars"],
        "min_per_type": cfg["min_per_type"],
        "stratified_by": "phi_type",
        "rationale_ref": "docs/prompts/rule_author.md §1.4, DESIGN.md §11.1",
    }



#: The files that together fix an agent's dev window. Hashed onto every
#: `human_log.jsonl` and `agent_calls.jsonl` line (DESIGN §11.2, §5.5). All of them,
#: because the prompts describe the window in prose and the config holds the numbers —
#: a record naming only the first would agree with a doubled `n` as readily as with 40.
#:
#: **Three since 2026-08-12, and `auditor.md` is the third** (DESIGN §5.5). The Auditor's
#: prompt decides what the RuleAuthor is shown in `rule_author.md` §1.3's audit block, so
#: it is part of the window by the same argument that puts `sampling.yaml` there: a record
#: naming only the RuleAuthor's template would agree with a rewritten Auditor as readily as
#: with this one.
#:
#: **The frozen arms are not re-hashed.** `port-oneshot`, `port-oneshot-nofence` and
#: `port-human` recorded two files, which is what existed when their calls were made, and a
#: third hash added retroactively would be a claim about a window that never applied
#: (DESIGN §6.3, `docs/notes/window-freeze-history.md`). That is why `window_drift()` reads
#: the field list off the record it is checking instead of off this tuple — see there.
PROMPT_TEMPLATE = "docs/prompts/rule_author.md"
AUDITOR_TEMPLATE = "docs/prompts/auditor.md"
WINDOW_FILES = (PROMPT_TEMPLATE, AUDITOR_TEMPLATE, "config/sampling.yaml")

#: `WINDOW_FILES` entry -> the field its hash is written to. A mapping rather than a
#: derivation from the filename: the two existing field names (`prompt_sha256`,
#: `sampling_sha256`) predate the third file and do not follow one rule, and inventing a
#: rule that reproduced them would be a rule with two exceptions. The names are what a
#: reader greps for, so they are declared.
WINDOW_HASH_FIELDS = {
    PROMPT_TEMPLATE: "prompt_sha256",
    AUDITOR_TEMPLATE: "auditor_sha256",
    "config/sampling.yaml": "sampling_sha256",
}


def file_hash(path: str) -> str:
    """SHA-256 of a repository file, labelled with its algorithm.

    Content hash rather than the repository's commit hash: the commit says when the
    tree was and this says what the file was. An uncommitted edit moves the second and
    not the first, and that edit is exactly the event the record exists to catch —
    `rules_commit` already answers the question a commit answers.
    """
    return "sha256:" + hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def prompt_hash(path: str | None = None) -> str:
    """SHA-256 of the RuleAuthor prompt template, for `human_log.jsonl`.

    DESIGN §11.1 freezes this file for a corpus before `port-human` starts on it and
    records the identity of what was frozen.
    """
    return file_hash(path or PROMPT_TEMPLATE)


def window_hashes(files: Sequence[str] | None = None) -> dict:
    """One hash field per window file, for a log line or a freeze record.

    Returned as the fields rather than as a list, so a caller writes them onto the
    line without choosing names — the names are what a reader greps for, and two
    callers inventing two spellings would be two logs.

    Three fields by default since 2026-08-12 (`auditor_sha256`). Built from `WINDOW_FILES`
    rather than written out, so widening the window is one edit: a fourth file listed there
    and not here would be hashed into `files` and compared against nothing.

    **`files` exists for an arm whose window is not today's window**, which is
    `port-human`: it is retired, its window was two files, and hashing a third onto a
    revived line would claim a window that never applied (DESIGN §6.3). The parameter makes
    that arm state its own window instead of inheriting a constant that moved underneath it.
    """
    return {WINDOW_HASH_FIELDS[name]: file_hash(name)
            for name in (WINDOW_FILES if files is None else files)}


def recorded_window_fields(record: dict) -> list[str]:
    """Which hash fields a freeze record or log line actually carries, in window order.

    **Read off the record, not off `WINDOW_FILES`, and that is what keeps a widening from
    reaching backwards.** `port-oneshot`, `port-oneshot-nofence` and `port-human` froze two
    files; comparing them against today's three-field tuple would report a permanent drift
    on `auditor_sha256` — a file those calls never saw — and the honest reading of that
    report is that the record is wrong, which it is not.

    A record's `files` list is the authority for what its window *was*. Falls back to the
    field names present when `files` is absent, which is `port-human`'s early shape.
    """
    files = record.get("files")
    if isinstance(files, list):
        return [WINDOW_HASH_FIELDS[name] for name in files
                if name in WINDOW_HASH_FIELDS]
    return [field for field in WINDOW_HASH_FIELDS.values() if field in record]
