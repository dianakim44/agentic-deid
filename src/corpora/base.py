"""Corpus-agnostic loading interface.

One `Document` per unit, one `Span` per annotation. What is corpus-specific lives
in a `CorpusLoader` subclass; everything here holds for every corpus.

Three rules this module exists to enforce, all from CLAUDE.md and DESIGN.md:

  - **No path is hardcoded.** Corpus roots come from
    `config/data_paths.local.yaml`, so code never knows whether a corpus sits
    inside the repository or outside it (DUA corpora are outside).
  - **No vocabulary is hardcoded.** Canonical types, layers and split names are
    read from `config/naming.yaml`. A value not defined there is an error, not a
    new value.
  - **Gold offsets are asserted, not trusted.** Every span is sliced out of the
    loaded text and compared to the surface the annotation recorded
    (DESIGN §9.7). A mismatch raises.

Usage:

    from src.corpora import load
    docs = load("es-meddocan")
"""
from __future__ import annotations

import os
import string
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[2]
NAMING = ROOT / "config" / "naming.yaml"
DATA_PATHS = ROOT / "config" / "data_paths.local.yaml"
DATA_PATHS_EXAMPLE = ROOT / "config" / "data_paths.example.yaml"

BOM = "﻿"

#: The only module allowed to load a sealed fold. Checked by import identity, not
#: by inspecting the call stack: a stack walk can be satisfied by naming a
#: function or a file the same thing, and the point of the gate is that satisfying
#: it takes a deliberate edit to a committed file rather than a clever caller.
SEALED_CALLER = "src.eval.run_sealed_eval"


class CorpusError(Exception):
    """Anything wrong with a corpus on disk, its config, or its annotations.

    Deliberately one exception type: every case here is "stop and tell a human",
    and callers have no recovery path that differs by cause.
    """


class SealError(CorpusError):
    """An attempt to reach a sealed fold from somewhere that may not.

    The one exception here with its own type, because it is the one failure whose
    right response is never "handle it and continue". It subclasses CorpusError so
    existing handlers still stop — but a handler that means to swallow a corpus
    problem has to name this type explicitly to swallow a seal breach too.
    """


# ─── configuration ──────────────────────────────────────────────────────────
# Read once and cached. These files are the single definition site for
# identifiers (naming.yaml) and locations (data_paths.local.yaml); nothing below
# may fall back to a literal if a lookup misses.


@lru_cache(maxsize=1)
def naming() -> dict:
    """The contents of config/naming.yaml."""
    with open(NAMING, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def axis(name: str) -> dict:
    """One axis of naming.yaml, e.g. `axis("phi_type")`.

    Raises rather than returning empty, because an absent axis means the code and
    the config have diverged — exactly the drift naming.yaml exists to prevent.
    """
    axes = naming()["axes"]
    if name not in axes:
        raise CorpusError(
            f"config/naming.yaml has no {name!r} axis (has: {sorted(axes)}). "
            "Add the axis there rather than hardcoding values."
        )
    return axes[name]


def corpus_ids() -> list[str]:
    return sorted(axis("corpus"))


def canonical_types() -> list[str]:
    """The canonical PHI types, from naming.yaml (see DESIGN §9.0)."""
    return sorted(axis("phi_type"))


def split_names() -> list[str]:
    return sorted(axis("split"))


@lru_cache(maxsize=1)
def layer_families() -> dict[str, tuple[str, ...]]:
    """Family name -> the layers in it, validated. See DESIGN §3.

    The complementarity breakdown (DESIGN §5) reports rules only / tagger only /
    both / neither, which requires knowing which layers are the rules ones. That
    grouping lives in `config/naming.yaml`, not here: a layer whose value is read
    from the config while its family is hardcoded in Python is the same drift the
    "never derive a layer from a detector name" rule exists to prevent, just moved
    one level up.

    This validates rather than merely reads, and the union check is the load-bearing
    one. **Every layer must be in exactly one family, and no family may name a layer
    that does not exist.** A subset check would pass when a new layer is added to the
    axis and forgotten here — and then every span that layer emits falls into
    `neither`, which reads as "nothing found it" rather than as a configuration gap.
    That is a wrong number with no symptom, so it is refused at load time.

    Families and layers are different levels of description, and a family name that
    is also a layer name opens the way to filling a span's `layer` with a family.
    `tagger` is both, which is permitted under one condition: **a family may share a
    layer's name only if that layer is its sole member.** Then the two readings of the
    value agree — a span from the `tagger` family has `layer="tagger"` either way — so
    the ambiguity cannot produce a wrong value.

    The condition is not decoration. Add a second learned layer to the `tagger` family
    and the readings diverge: `layer="tagger"` would then be a valid value meaning
    "some learned layer", the provenance DESIGN §3 requires would be unrecoverable for
    those spans, and nothing would fail. This raises at that moment and says to rename
    the family, which is the edit that keeps the two namespaces separable.
    """
    families = naming().get("layer_families")
    if not families:
        raise CorpusError(
            "config/naming.yaml has no layer_families block. The complementarity "
            "breakdown (DESIGN §5) needs it, and deriving families in code would "
            "put the grouping somewhere naming.yaml cannot be checked against."
        )

    layers = set(axis("layer"))
    assigned: dict[str, str] = {}
    for family, members in families.items():
        if not isinstance(members, list):
            raise CorpusError(
                f"layer_families[{family!r}] is not a list. A family of one is "
                "written as a list too, so that it is not a special case."
            )
        if family in layers and members != [family]:
            raise CorpusError(
                f"family {family!r} shares its name with a layer but is not that "
                f"layer alone (members: {members}). Sharing a name is only safe for "
                "a family of one, where the family reading and the layer reading of "
                "the value agree. With more members, `layer=\"{0}\"` becomes a valid "
                "value meaning 'some layer of this family' and the per-layer "
                "provenance DESIGN §3 requires is unrecoverable. Rename the "
                "family.".format(family)
            )
        for layer in members:
            if layer in assigned:
                raise CorpusError(
                    f"layer {layer!r} is in both the {assigned[layer]!r} and "
                    f"{family!r} families. The complementarity breakdown counts "
                    "each layer once, so the families must partition the axis."
                )
            assigned[layer] = family

    missing = sorted(layers - set(assigned))
    if missing:
        raise CorpusError(
            f"layers {missing} are in the `layer` axis but in no family of "
            "layer_families. Add them to a family: an unfamilied layer's spans "
            "would be counted as `neither` in the complementarity breakdown, which "
            "reads as 'nothing found it' rather than as a missing declaration."
        )
    unknown = sorted(set(assigned) - layers)
    if unknown:
        raise CorpusError(
            f"layer_families names {unknown}, which are not values of the `layer` "
            f"axis (have: {sorted(layers)}). A family entry for a layer that does "
            "not exist is a leftover from a rename, and it silently contributes "
            "nothing to any count."
        )
    return {f: tuple(m) for f, m in families.items()}


def family_of(layer: str) -> str:
    """Which family a layer belongs to. Raises for an unknown layer.

    Never guesses from the name: `layer_families` is the only answer, so a layer
    added to the axis without a family is an error here rather than a silent
    `neither` in the complementarity breakdown.
    """
    for family, members in layer_families().items():
        if layer in members:
            return family
    raise CorpusError(
        f"{layer!r} is not a layer in config/naming.yaml "
        f"(have: {sorted(axis('layer'))})"
    )


def model_id_absent() -> str:
    """The `model_id` value an arm that called no language model records.

    From `config/naming.yaml`'s `model_id_absent`, not a literal here. `model_id` is
    not an axis — it holds the exact identifier a call was made with, which is an
    observation and not a controlled vocabulary (DESIGN §4) — but *this* value is
    vocabulary, and CLAUDE.md's rule applies to it like any other: a value that lands
    in a published results file is defined in the config.

    A string rather than `None` for the reason the cost block writes zeros: `None`
    cannot distinguish "not applicable" from "not recorded", and distinguishing them
    is the entire purpose of the field. The `R` arm runs no model and says so.
    """
    value = naming().get("model_id_absent")
    if not isinstance(value, str) or not value:
        raise CorpusError(
            "config/naming.yaml has no `model_id_absent` string. It is what an arm "
            "that called no model records in metrics.json's run block, and it lives "
            "in the config so that no module spells it as a literal."
        )
    return value


def model_id_resolution() -> dict[str, str]:
    """The kinds of `model_id` resolution, from `config/naming.yaml`.

    Three values, measured rather than assumed (`docs/notes/baseline-model-family.md`,
    2026-08-08): a `converse` response does not name the model it was served by, and the
    `/model` field it can be asked for is never more specific than the request. So
    `model_id` alone cannot answer "which weights produced this number", and what has to
    be recorded is that it cannot — `dated`, `alias-unresolved`, `mismatch`.

    A **closed** vocabulary, unlike `model_id` itself. `model_id` is an observation and so
    could not be validated against a list without rejecting real Bedrock identifiers; this
    field is the *kind* of observation, and there are three kinds. It is not an axis for
    `model_id`'s reason — it does not appear in a results path — which is why it is here
    rather than under `axes`.
    """
    value = naming().get("model_id_resolution")
    if not isinstance(value, dict) or not value:
        raise CorpusError(
            "config/naming.yaml has no `model_id_resolution` mapping. It is the closed "
            "vocabulary for how far a recorded `model_id` was resolved, it lands in "
            "metrics.json, and CLAUDE.md keeps such values out of the modules."
        )
    bad = [key for key in value if not isinstance(key, str) or not key]
    if bad:
        raise CorpusError(
            f"config/naming.yaml `model_id_resolution` has {len(bad)} non-string or "
            "empty key(s). Each key is a value written to metrics.json."
        )
    return dict(value)


def check_model_resolution(value: str) -> str:
    """Return `value` if it is a declared resolution kind; raise otherwise.

    A checked accessor rather than a bare lookup, for the reason `axis()` raises on an
    unknown name: a resolution kind invented at a call site would be written to
    metrics.json and read by nobody, and the field's whole purpose is to be read.
    """
    kinds = model_id_resolution()
    if value not in kinds:
        raise CorpusError(
            f"{value!r} is not a model_id resolution kind in config/naming.yaml "
            f"(have: {sorted(kinds)}). Add it there before a module writes it."
        )
    return value


def termination_reasons() -> dict[str, str]:
    """The ways an iterating arm can stop, from `config/naming.yaml`.

    Three values, and the reason there are three rather than two is DESIGN §3's
    requirement that a ceiling-terminated run may not be described as converged: an arm
    that ran out of iterations has *not* satisfied the convergence test, and a run that
    stopped at 8 with the leak rate still falling is a different claim from one that
    stopped at 5 having converged. `not_applicable` is the third because an arm that does
    not iterate still has to record that fact — `model_id_absent`'s argument one field
    over, and the cost block's zeros one further over.

    A **closed** vocabulary and not an axis, for `model_id_resolution`'s reason: it does
    not name a cell of the experiment, it names the kind of ending observed. This function
    only says which words exist. Which word attaches to which branch is
    `src/termination.py`'s job, and keeping that here would put the §3 prohibition in the
    module every caller reads for vocabulary rather than in the one function that decides.
    """
    value = naming().get("termination_reason")
    if not isinstance(value, dict) or not value:
        raise CorpusError(
            "config/naming.yaml has no `termination_reason` mapping. It is the closed "
            "vocabulary for how an iterating arm stopped, it lands in metrics.json, and "
            "CLAUDE.md keeps such values out of the modules."
        )
    bad = [key for key in value if not isinstance(key, str) or not key]
    if bad:
        raise CorpusError(
            f"config/naming.yaml `termination_reason` has {len(bad)} non-string or "
            "empty key(s). Each key is a value written to metrics.json."
        )
    return dict(value)


def check_termination_reason(value: str) -> str:
    """Return `value` if it is a declared termination reason; raise otherwise.

    A checked accessor for `check_model_resolution`'s reason: a reason invented at a call
    site would be written to metrics.json and compared against nothing. Here the stakes
    are one step higher, because the vocabulary's whole content is a distinction §3
    forbids collapsing — a caller that could write `"converged (at the cap)"` would have
    reinstated the collapse in a field that looks validated.
    """
    reasons = termination_reasons()
    if value not in reasons:
        raise CorpusError(
            f"{value!r} is not a termination reason in config/naming.yaml "
            f"(have: {sorted(reasons)}). Add it there before a module writes it."
        )
    return value


@lru_cache(maxsize=1)
def termination_params() -> dict[str, int | float]:
    """The pre-registered δ/k/ceiling constants, from `config/naming.yaml`. DESIGN §3.

    **δ is not among them, and its absence is the design.** What is stored is
    `delta_spans` and `delta_floor` — the two constants δ is *computed* from — because δ
    itself is per-corpus (`max(delta_floor, delta_spans / n_dev)`) and `n_dev` comes from
    `splits/{corpus}.json`. A δ written here would be a number that does not say which
    corpus's fold it was evaluated on, and §3's per-corpus block exists because that
    number silently demanded 26 spans on one corpus and 1.6 on another.

    Validated on read for `sample.config()`'s reason: these are experimental parameters
    that reach `metrics.json`, so a `k` arriving as the string `"2"` would compare
    unequal to every integer and stop nothing, in a way no caller can notice. `delta_spans`
    and `k` must be positive integers — a `k` of 0 stops on the first iteration and a
    `delta_spans` of 0 makes every iteration productive; `ceiling` likewise, since a
    ceiling of 0 is an arm that cannot run. `delta_floor` is a rate and so may be a float,
    and must be positive: at 0 the floor branch never binds and a large fold gets a δ
    below its own one-span noise, which is the thing the floor was derived to prevent.
    """
    value = naming().get("termination")
    if not isinstance(value, dict) or not value:
        raise CorpusError(
            "config/naming.yaml has no `termination` block. It holds the pre-registered "
            "δ/k/ceiling constants (DESIGN §3), they are recorded in metrics.json, and "
            "CLAUDE.md keeps such values out of the modules."
        )
    for key in ("delta_spans", "k", "ceiling"):
        got = value.get(key)
        if not isinstance(got, int) or isinstance(got, bool) or got <= 0:
            raise CorpusError(
                f"config/naming.yaml `termination.{key}` must be a positive integer, got "
                f"{got!r}. It is a pre-registered experimental parameter recorded in the "
                "results (DESIGN §3), so it is validated here rather than coerced at the "
                "point of use."
            )
    floor = value.get("delta_floor")
    if isinstance(floor, bool) or not isinstance(floor, (int, float)) or floor <= 0:
        raise CorpusError(
            f"config/naming.yaml `termination.delta_floor` must be a positive number, got "
            f"{floor!r}. It is δ's lower bound and the reason a fold larger than "
            "`delta_spans / delta_floor` does not get a δ beneath its own noise floor "
            "(DESIGN §3)."
        )
    extra = sorted(set(value) - {"delta_spans", "delta_floor", "k", "ceiling"})
    if extra:
        raise CorpusError(
            f"config/naming.yaml `termination` has unexpected key(s) {extra}. The block is "
            "closed: a fifth parameter would be read by nothing and would look "
            "pre-registered."
        )
    return {"delta_spans": value["delta_spans"], "delta_floor": float(floor),
            "k": value["k"], "ceiling": value["ceiling"]}


def agent_roles() -> dict[str, str]:
    """Which agent made a call, from `config/naming.yaml`. DESIGN §5.5.

    Two values today, `rule_author` and `auditor`, and the field exists because they share
    one `agent_calls.jsonl`. `llm_calls` sums their lines, which is right for cost — it is
    what a round spent — and useless for attribution: without the role, "the Auditor
    accounts for half this arm's spend" is unverifiable from the log that holds the spend.

    A closed vocabulary and **not** an axis, like `termination_reason` and
    `model_id_resolution`. It is here rather than as a module literal because CLAUDE.md's
    rule covers values that never reach a results path, and this is one.

    The values are spelled like the prompt templates they correspond to
    (`docs/prompts/rule_author.md`, `auditor.md`), which makes §3's "an agent is defined by
    the file it produces" checkable at the log layer. **No code derives one from the
    other.** That is the `layer`-from-detector-name prohibition in a second place: the
    caller states its role, and a mapping from filename to role would be a component whose
    mistakes look like data.
    """
    value = naming().get("agent_role")
    if not isinstance(value, dict) or not value:
        raise CorpusError(
            "config/naming.yaml has no `agent_role` mapping. It is the closed vocabulary "
            "for which agent made a call, it lands on every agent_calls.jsonl line, and "
            "CLAUDE.md keeps such values out of the modules."
        )
    bad = [key for key in value if not isinstance(key, str) or not key]
    if bad:
        raise CorpusError(
            f"config/naming.yaml `agent_role` has {len(bad)} non-string or empty key(s). "
            "Each key is a value written to the call log."
        )
    return dict(value)


def check_agent_role(value: str) -> str:
    """Return `value` if it is a declared agent role; raise otherwise.

    A checked accessor for `check_model_resolution`'s reason. The specific failure it
    closes: a caller writing `"RuleAuthor"` or `"rule-author"` produces a log in which one
    agent's calls are split across two spellings, and every per-role cost figure computed
    from it is wrong in a direction nothing in the file reveals.
    """
    roles = agent_roles()
    if value not in roles:
        raise CorpusError(
            f"{value!r} is not an agent role in config/naming.yaml "
            f"(have: {sorted(roles)}). Add it there before a module writes it."
        )
    return value


def caching_boundaries() -> dict[str, str]:
    """Where a prompt's cached block ends, from `config/naming.yaml`. DESIGN §5.4.

    **One value, and the singleton is the point.** `after_audit_frame` says the cached side
    is the Auditor's template, the input banner and §1.1's frame — committed bytes and
    `naming.yaml` values — and that §1.2's masked document is on the far side. That is the
    boundary `docs/prompts/auditor.md` §6's third bullet declares, and the reason it is a
    *value* rather than a literal in the transport is that the bullet's claim has to be
    checkable: a free string would let `"after_frame"` and `"after_the_frame"` land in two
    arms' files with nothing able to group them, and a reader could not tell whether two
    arms cached the same bytes.

    **Moving the boundary is an edit here, which is what makes the prompt's guarantee
    binding.** A value that put the masked document on the cached side is not in this
    mapping and must not be added without editing that bullet first — the largest corpus
    exposure in the project (`auditor.md` §6) would otherwise be retained by a service for
    five minutes because a keyword argument changed.

    A closed vocabulary and **not** an axis, like `agent_role` and `audit_refusal`: it lands
    in `metrics.json`'s content rather than in a path.
    """
    value = naming().get("caching_boundary")
    if not isinstance(value, dict) or not value:
        raise CorpusError(
            "config/naming.yaml has no `caching_boundary` mapping. It is the closed "
            "vocabulary for where a prompt's cached block ends, it lands in metrics.json's "
            "`caching` block, and CLAUDE.md keeps such values out of the modules."
        )
    bad = [key for key in value if not isinstance(key, str) or not key]
    if bad:
        raise CorpusError(
            f"config/naming.yaml `caching_boundary` has {len(bad)} non-string or empty "
            "key(s). Each key is a value written to metrics.json."
        )
    return dict(value)


def check_caching_boundary(value: str) -> str:
    """Return `value` if it is a declared cache boundary; raise otherwise.

    `check_agent_role`'s reason, with the sharpest stake in this file. The other checked
    accessors close a *grouping* failure — two spellings of one value split a count. This one
    closes that too, and underneath it something else: the boundary decides **which bytes a
    third party retains**, so a caller free to invent one is a caller that can move the masked
    document onto the cached side and record a name for it that no vocabulary refused. The
    refusal is what keeps `auditor.md` §6's bullet a property rather than a sentence.
    """
    boundaries = caching_boundaries()
    if value not in boundaries:
        raise CorpusError(
            f"{value!r} is not a cache boundary in config/naming.yaml "
            f"(have: {sorted(boundaries)}). Add it there before a module writes it — and "
            "read docs/prompts/auditor.md §6's third bullet first: it declares that the "
            "masked document is on the far side of the boundary and is never cached, so a "
            "new value that moves the boundary past §1.2 contradicts a committed prompt."
        )
    return value


def caching_ttls() -> dict[str, str]:
    """The prompt-cache TTLs `naming.yaml` declares. DESIGN §5.4, measured 2026-08-16.

    Closed for `caching_boundaries()`'s reason plus one this vocabulary has on its own: the
    string is the same one Bedrock returns in `cacheDetails` (`{"ttl": "5m", ...}`), so a
    declared value and a reported value are comparable. A TTL invented at a call site would
    be a number that could not be checked against the envelope that produced it.
    """
    value = naming().get("caching_ttl")
    if not isinstance(value, dict) or not value:
        raise CorpusError(
            "config/naming.yaml has no `caching_ttl` mapping. It is the closed vocabulary "
            "for a prompt cache's lifetime, it lands in metrics.json's `caching` block, and "
            "CLAUDE.md keeps such values out of the modules."
        )
    bad = [key for key in value if not isinstance(key, str) or not key]
    if bad:
        raise CorpusError(
            f"config/naming.yaml `caching_ttl` has {len(bad)} non-string or empty key(s). "
            "Each key is a value written to metrics.json."
        )
    return dict(value)


def check_caching_ttl(value: str) -> str:
    """Return `value` if it is a declared cache TTL; raise otherwise."""
    ttls = caching_ttls()
    if value not in ttls:
        raise CorpusError(
            f"{value!r} is not a cache TTL in config/naming.yaml (have: {sorted(ttls)}). "
            "Add it there before a module writes it. The value is also what Bedrock reports "
            "in `cacheDetails`, so an undeclared one would be a lifetime this project claims "
            "and the envelope never confirmed."
        )
    return value


def audit_refusals() -> dict[str, str]:
    """Why an Auditor flag was refused, from `config/naming.yaml`. `auditor.md` §2.3.

    Five values, and the vocabulary exists because the validator **refuses rather than
    repairs**. A validator that snapped an out-of-range column to the end of its line would
    produce a flag at a position the agent never claimed, with nothing in the file saying
    so; one that dropped silently would make the report shorter for a reason no reader
    could see. So a refusal is recorded with its reason and counted, and a round in which
    the model lost the coordinate scheme is a number rather than a thin report.

    A closed vocabulary and **not** an axis, like `agent_role` and `termination_reason`.
    Unlike those two it lands in the *content* of a file under `results/` rather than in a
    log line, which CLAUDE.md's rule covers equally.
    """
    value = naming().get("audit_refusal")
    if not isinstance(value, dict) or not value:
        raise CorpusError(
            "config/naming.yaml has no `audit_refusal` mapping. It is the closed "
            "vocabulary for why an Auditor flag was refused, it lands in "
            "audit_report.json, and CLAUDE.md keeps such values out of the modules."
        )
    bad = [key for key in value if not isinstance(key, str) or not key]
    if bad:
        raise CorpusError(
            f"config/naming.yaml `audit_refusal` has {len(bad)} non-string or empty "
            "key(s). Each key is a value written to the audit report."
        )
    return dict(value)


def check_audit_refusal(value: str) -> str:
    """Return `value` if it is a declared refusal reason; raise otherwise.

    `check_agent_role`'s reason, one file over. The failure it closes is specific to this
    field: the refusal reasons are what a reader consults to decide whether a thin report
    means clean text or a broken call, so a reason invented at a call site would be a
    diagnosis nothing can group by — and the diagnosis is the only thing a refused flag
    carries.
    """
    reasons = audit_refusals()
    if value not in reasons:
        raise CorpusError(
            f"{value!r} is not an audit refusal reason in config/naming.yaml "
            f"(have: {sorted(reasons)}). Add it there before a module writes it."
        )
    return value


def excluded_types() -> dict[str, str]:
    """The types DESIGN §9.1 excluded from the canonical set, with a reason each.

    Read by `docs/prompts/auditor.md` §1.1, which requires them **named as out of scope
    rather than left to be inferred**: an Auditor that flags `madre` is not wrong about the
    text, it is answering a question this project does not ask, and every such flag lands in
    the least actionable category of the report (§4's case 2). A frame that listed the ten
    canonical types and said nothing about the three would leave the agent to work out from
    an absence whether sex is a type it should be reporting.

    **Not the `phi_type` axis and not a subset of it.** Putting them there would make them
    scoreable, which is the decision §9.1 took the other way. This is a sibling block, and
    the axis's own comment points at it.

    **Not derived from a loader's `excluded_types`, and not the source of it.** That
    attribute holds one corpus's *own* type names (`SEXO_SUJETO_ASISTENCIA`) and drives the
    `excluded` flag at load; it is corpus-specific and known only to the code that reads
    that corpus. This is the corpus-independent concept, in the spelling an agent is shown.
    Deriving this from the loaders would silently shorten the list on a corpus whose loader
    is not written yet — `de-grascco` contributes `NAME_TITLE` and has no loader today — and
    a silently shortened list is indistinguishable from "nothing is excluded".
    """
    value = naming().get("excluded_types")
    if not isinstance(value, dict) or not value:
        raise CorpusError(
            "config/naming.yaml has no `excluded_types` mapping. It is the DESIGN §9.1 "
            "exclusions, it lands in the Auditor's task frame (auditor.md §1.1), and "
            "CLAUDE.md keeps such values out of the modules."
        )
    bad = [key for key in value if not isinstance(key, str) or not key]
    if bad:
        raise CorpusError(
            f"config/naming.yaml `excluded_types` has {len(bad)} non-string or empty "
            "key(s). Each key is a type name shown to an agent."
        )
    overlap = sorted(set(value) & set(axis("phi_type")))
    if overlap:
        raise CorpusError(
            f"config/naming.yaml declares {overlap} both as a phi_type and as an excluded "
            "type. A type is one or the other (DESIGN §9.0, §9.1): a value in the axis is "
            "scored, and the exclusion decision was that these are not."
        )
    for key, reason in value.items():
        if not isinstance(reason, str) or not reason.strip():
            raise CorpusError(
                f"config/naming.yaml `excluded_types`[{key!r}] carries no reason. §9.1 "
                "excludes for two different reasons — not a Safe Harbor identifier, and "
                "incompatible annotation — and a list without them invites the agent to "
                "guess which applies."
            )
    return dict(value)


def masked_tag_heterogeneous() -> str:
    """The mask tag for a union of overlapping spans whose types disagree. DESIGN §3.

    The masker masks the union of overlapping extents and prints a `phi_type` only where
    that union is type-homogeneous. Where the types disagree it prints this value, which
    names no type — because **naming one would give the masker a merge policy**, and merge
    policy is a replaceable strategy that must not be baked into a component every arm runs
    through (§4, §9.3). Declining to state a type is the only answer here that is not a
    tie-break.

    In the config rather than in the masker for `model_id_absent`'s reason: it is a single
    value rather than a vocabulary, but it lands in the content of a prompt and in the
    masker's output, so a literal in the module would be a vocabulary item invented in code
    (CLAUDE.md).

    **Refused if it is a `phi_type`.** Spelling it `[NAME]` would make a heterogeneous union
    indistinguishable from a homogeneous one, which restores at the notation layer exactly
    the arbitrary choice the rule exists to avoid — and it would do so while every other
    check still passed.
    """
    value = naming().get("masked_tag_heterogeneous")
    if not isinstance(value, str) or not value:
        raise CorpusError(
            "config/naming.yaml has no `masked_tag_heterogeneous` string. It is the mask "
            "tag for a union of overlapping spans whose types disagree (DESIGN §3), it "
            "lands in the Auditor's prompt, and CLAUDE.md keeps such values out of the "
            "modules."
        )
    bare = value.strip("[]")
    if bare in axis("phi_type"):
        raise CorpusError(
            f"`masked_tag_heterogeneous` is {value!r}, which names the phi_type {bare!r}. "
            "It must name no type: the tag marks a union whose spans disagreed, and "
            "spelling it as a type makes that union indistinguishable from a homogeneous "
            "one — which is the arbitrary choice DESIGN §3 refuses, reappearing in the "
            "notation."
        )
    return value


def path_template(key: str) -> str:
    """One `paths` template from naming.yaml, e.g. `path_template("humanlog")`.

    Templates live in the config and not in the modules that write to them, for the
    reason `axis()` exists: two modules holding the same literal are two literals, and
    the day one of them changes is the day the results are in two places. Raises on an
    unknown key rather than returning a default — a caller asking for a path the config
    does not declare has invented an artifact.

    The template is returned unformatted. Filling it is the caller's job, and the caller
    is what knows which components need checking against which axis (a `{porting}` value
    is an axis value, `{lang}` is not a corpus).
    """
    templates = naming().get("paths", {})
    if key not in templates:
        raise CorpusError(
            f"config/naming.yaml has no paths.{key} (has: {sorted(templates)}). "
            "Declare the path there rather than writing it as a literal — CLAUDE.md's "
            "rule that a new value goes into the config first applies to output paths, "
            "which is where the results of an arm are found."
        )
    return templates[key]


#: The four axes every results path is scoped by, in template order. `PATH_AXES` one level
#: down: `scorer.PATH_AXES` is the subset of a *run block* that reaches a path, and this is
#: the same four read as an argument list by a caller who holds no run block — the loop
#: driver, which has the axes and a round and nothing else (`run_fold.errors_path`).
ROUND_AXES = ("corpus", "detector", "supervision", "porting")


def round_path(
    key: str, *, iteration: int, artefact: str, error: type[Exception],
    root: Path | None = None, **components: str,
) -> Path:
    """One iteration-scoped results path from naming.yaml, with every component checked.

    `key` is the `paths` key, `iteration` the round, `artefact` what the round's file is
    called in a refusal, and `error` the exception type to raise. `components` are the four
    `ROUND_AXES` values plus whatever else the template names (`{lang}`, for `armrules`).

    **Why this is shared and why `error` is a parameter.** Four functions implemented this
    check independently — `run_fold._round_path` for two keys, `scorer.iter_metrics_path`,
    `rules.arm_rules_path` — and each said in its docstring that the repetition was the
    module boundary rather than an oversight, because each raises the type its own callers
    catch. That reasoning holds for the *type* and was doing the work of an argument. Adding
    a fifth copy for the Auditor's report is what made the cost legible: four copies of "an
    unknown axis mints a cell" drift on the day one of them learns something the others do
    not, and the thing they would fail to learn is a check, so the drift is silent by
    construction. The type stays each caller's, as a parameter; the check becomes one.

    **What is not shared: the template lookup, which was already single.** Each `paths` key
    has exactly one reader, and that was true before this function existed. This changes
    where the *validation* lives, not where a path is defined.

    Every component is checked against `naming.yaml` because a results path names the cell of
    the experiment an artefact belongs to: an unknown value mints a cell instead of failing,
    and `results/es-meddocan/rules-only/` sitting beside `results/es-meddocan/R/` reads to an
    aggregation as a second detector. The round is checked for being a round because `iter0/`
    or `iter1.0/` puts a record where nothing looks for it — and `True` passes `isinstance(…,
    int)`, so a caller who passed a flag would silently name round 1.
    """
    fields = {name for _, name, _, _ in string.Formatter().parse(path_template(key)) if name}
    missing = fields - set(components) - {"iteration"}
    if missing or (set(components) - fields):
        raise error(
            f"paths.{key} names {sorted(fields)} and was given "
            f"{sorted(set(components) | {'iteration'})}. A component the template does not "
            "name is silently dropped, and one it names and nobody passed raises a KeyError "
            f"deep in `.format()` — either way the round's {artefact} lands somewhere its "
            "writer did not choose."
        )
    for name, value in sorted(components.items()):
        if value not in axis(name):
            raise error(
                f"{value!r} is not a value of the {name!r} axis in config/naming.yaml "
                f"(have: {sorted(axis(name))}). This path names the cell of the experiment "
                f"the round's {artefact} belongs to, so an unknown component would create a "
                "cell rather than fail (DESIGN §5.3, §5.5)."
            )
    if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 1:
        raise error(
            f"iteration must be an integer >= 1, got {iteration!r}. It is a path component "
            f"(paths.{key}), and the sequence of an iterating arm's {artefact}s is the "
            f"experimental record — a round's {artefact} written to iter0/ or iter1.0/ is a "
            "round nothing looks for afterwards (DESIGN §5.5)."
        )
    return (root or ROOT) / path_template(key).format(iteration=iteration, **components)


def rule_langs(corpus_id: str) -> list[str]:
    """Rule-file languages this corpus loads (DESIGN §5.2).

    A list even when it has one element: monolingual is not a special case.
    """
    mapping = naming()["corpus_rule_langs"]
    if corpus_id not in mapping:
        raise CorpusError(
            f"no corpus_rule_langs entry for {corpus_id!r} in config/naming.yaml"
        )
    return list(mapping[corpus_id])


def corpus_root(corpus_id: str) -> Path:
    """Where this corpus lives on this machine.

    From config/data_paths.local.yaml only. The path may be inside or outside the
    repository and callers must not care — DUA corpora are kept outside, where
    tools/release_screen.py cannot see them.
    """
    if corpus_id not in axis("corpus"):
        raise CorpusError(
            f"{corpus_id!r} is not a corpus in config/naming.yaml "
            f"(have: {corpus_ids()})"
        )
    if not DATA_PATHS.exists():
        raise CorpusError(
            f"{DATA_PATHS} does not exist. Copy the template:\n"
            f"    cp {DATA_PATHS_EXAMPLE.relative_to(ROOT)} "
            f"{DATA_PATHS.relative_to(ROOT)}"
        )
    with open(DATA_PATHS, encoding="utf-8") as fh:
        mapping = (yaml.safe_load(fh) or {}).get("corpora") or {}
    raw = mapping.get(corpus_id)
    if not raw:
        raise CorpusError(
            f"config/data_paths.local.yaml has no path for {corpus_id!r}. "
            "See data/acquire/ for how to obtain it."
        )
    return _resolve(raw, corpus_id)


def _resolve(raw: str, corpus_id: str) -> Path:
    """Expand and validate a configured path.

    Never echoes the resolved path. For a DUA corpus that is a data location, and
    this message travels into logs and issues.
    """
    path = Path(os.path.expanduser(str(raw)))
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_dir():
        raise CorpusError(
            f"the configured path for {corpus_id!r} is not a directory. "
            "Check config/data_paths.local.yaml and the acquisition script."
        )
    return path


def sealed_root(corpus_id: str) -> Path | None:
    """Where this corpus's sealed test fold lives, or None if it is not sealed.

    Reads the `sealed:` block **only**, which is why that block is separate from
    `corpora:` in the config. A single mapping would mean any code iterating over
    corpus paths reaches the sealed fold as a matter of course; the seal has to be
    "the path is not known here", not "the path is known and politely avoided".

    Returns None rather than raising when a corpus has no entry: not-yet-sealed is
    a real and distinct state (the split file has to be frozen first), and
    conflating it with a misconfiguration would push someone towards adding an
    entry that points at unsealed data.
    """
    if corpus_id not in axis("corpus"):
        raise CorpusError(
            f"{corpus_id!r} is not a corpus in config/naming.yaml "
            f"(have: {corpus_ids()})"
        )
    if not DATA_PATHS.exists():
        return None
    with open(DATA_PATHS, encoding="utf-8") as fh:
        mapping = (yaml.safe_load(fh) or {}).get("sealed") or {}
    raw = mapping.get(corpus_id)
    if not raw:
        return None
    return _resolve(raw, corpus_id)


# ─── data model ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Span:
    """One annotated or detected span of text.

    Character offsets into `Document.text`, 0-based, `end` exclusive, after any
    BOM shift (DESIGN §9.7). `surface` is the text itself, kept so the offsets
    can be re-asserted at any later point without re-reading the corpus.

    Type fields, and why there are three:

      - `phi_type` — the canonical type, a value of naming.yaml's `phi_type`
        axis. Cross-corpus scoring uses this and only this. `None` when the span
        is excluded (below), because no canonical type honestly applies.
      - `subtype` — the corpus's own type, preserved verbatim. Needed for the
        per-type reporting DESIGN §5.1 requires, and never used in cross-corpus
        scoring: the corpora do not partition the space the same way, so forcing
        agreement would measure the annotation schema rather than the detector
        (DESIGN §9.0).
      - `excluded` — set when the corpus type is out of scope per DESIGN §9.1
        (`SEXO_*`, `FAMILIARES_*`, `NAME_TITLE`). The span is **kept**, not
        dropped: the excluded volume is a reported limitation, so it has to be
        countable. Anything scoring spans must filter on this flag.

    Provenance fields (DESIGN §3) are empty on gold spans. `layer` is a value of
    naming.yaml's `layer` axis, filled in by the detector that emitted the span
    and never derived from a detector name. `rule_id` carries the rule file's
    language as a prefix (`es:doctor_prefix`), so precision is attributable to
    the file that produced the match (DESIGN §5.2). Agents do not get a `layer`:
    they do not create spans, and their interventions go in `agent_actions`.
    """

    start: int
    end: int
    surface: str
    subtype: str
    phi_type: str | None = None
    excluded: bool = False

    # provenance — empty for gold, filled by whatever emitted a prediction
    layer: str | None = None
    detector: str | None = None
    rule_id: str | None = None
    score: float | None = None
    agent_actions: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise CorpusError(f"empty or inverted span: [{self.start}, {self.end})")
        if self.start < 0:
            raise CorpusError(f"negative span start: {self.start}")
        if self.excluded and self.phi_type is not None:
            raise CorpusError(
                f"excluded span {self.subtype!r} must not carry a canonical type "
                f"(got {self.phi_type!r})"
            )
        if not self.excluded and self.phi_type is None:
            raise CorpusError(
                f"span {self.subtype!r} has no canonical type and is not marked "
                "excluded — every gold span is either mapped or explicitly "
                "excluded (DESIGN §9.0)"
            )
        if self.phi_type is not None and self.phi_type not in axis("phi_type"):
            raise CorpusError(
                f"{self.phi_type!r} is not a phi_type in config/naming.yaml "
                f"(have: {canonical_types()})"
            )
        if self.layer is not None and self.layer not in axis("layer"):
            raise CorpusError(
                f"{self.layer!r} is not a layer in config/naming.yaml "
                f"(have: {sorted(axis('layer'))}). Layers are read from the "
                "config, never derived from a detector name (DESIGN §3)."
            )

    @property
    def is_gold(self) -> bool:
        return self.layer is None and self.detector is None

    @property
    def in_scope(self) -> bool:
        """True for spans that count towards a metric (DESIGN §9.1)."""
        return not self.excluded


@dataclass(slots=True)
class Document:
    """One unit of a corpus, with its gold spans.

    `doc_id` is the corpus's own identifier. `split` is a value of naming.yaml's
    `split` axis, or `None` for a corpus that ships no split (one is then built
    per DESIGN §9.5 and frozen before any rule is written).

    `text` has had a leading BOM stripped, and every offset in `spans` has been
    shifted to match (DESIGN §9.7). `had_bom` records that this happened, so the
    correction can be counted rather than assumed.
    """

    doc_id: str
    corpus_id: str
    text: str
    spans: list[Span] = field(default_factory=list)
    split: str | None = None
    had_bom: bool = False
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.split is not None and self.split not in axis("split"):
            raise CorpusError(
                f"{self.split!r} is not a split in config/naming.yaml "
                f"(have: {split_names()})"
            )
        if self.text.startswith(BOM):
            raise CorpusError(
                f"{self.doc_id}: text still begins with U+FEFF. The loader must "
                "strip the BOM and shift offsets (DESIGN §9.7)."
            )

    @property
    def in_scope_spans(self) -> list[Span]:
        return [s for s in self.spans if s.in_scope]

    def assert_offsets(self) -> None:
        """Slice every span out of the text and compare it to its surface.

        DESIGN §9.7: the loader asserts, it does not trust. Raises on the first
        mismatch, naming the span's index within the document so the failure is
        locatable without re-deriving it.

        No span surface appears in the message. Some corpora are DUA-restricted
        real clinical text (CARMEN-I), an exception message travels into logs and
        issues, and a check that is safe for one corpus and not another is a
        check nobody can trust. Lengths and offsets locate the fault.
        """
        for i, span in enumerate(self.spans):
            if span.end > len(self.text):
                raise CorpusError(
                    f"{self.corpus_id}/{self.doc_id}: span {i} "
                    f"({span.subtype}) ends at {span.end} but the document is "
                    f"{len(self.text)} characters"
                )
            sliced = self.text[span.start : span.end]
            if sliced != span.surface:
                raise CorpusError(
                    f"{self.corpus_id}/{self.doc_id}: span {i} ({span.subtype}) "
                    f"at [{span.start}, {span.end}) does not match its recorded "
                    f"surface — sliced {len(sliced)} chars, annotation recorded "
                    f"{len(span.surface)}"
                    + (
                        " (document carried a BOM; check the §9.7 offset shift)"
                        if self.had_bom
                        else ""
                    )
                )


# ─── loader interface ───────────────────────────────────────────────────────


class CorpusLoader:
    """Base class for one corpus's loader.

    A subclass sets `corpus_id` and implements `_read()`. Everything shared —
    resolving the root, stripping BOMs, mapping types, asserting offsets — is
    here, so a new corpus cannot quietly skip a step that every corpus needs.
    """

    corpus_id: str = ""
    #: corpus type -> canonical type. See DESIGN §9.0.
    type_map: dict[str, str] = {}
    #: corpus types kept but not scored. See DESIGN §9.1.
    excluded_types: frozenset[str] = frozenset()
    #: Folds that live behind `sealed/`. A tuple rather than the literal "test" so
    #: that a corpus needing a second held-out fold does not invite a string
    #: comparison somewhere else in the code.
    sealed_splits: tuple[str, ...] = ("test",)

    #: Fold directory name -> naming.yaml split value, in load order. A mapping
    #: rather than a list of names because MEDDOCAN's directories happen to be
    #: named after the folds and no other corpus is promised to be — a corpus whose
    #: `held_out/` directory holds the test fold must not be able to reach it just
    #: because the string differs from `"test"`.
    fold_dirs: dict[str, str] = {}

    def __init__(self, root: Path | None = None, use_split_file: bool = True) -> None:
        """`use_split_file=False` loads the corpus without `splits/{corpus}.json`.

        The default is True: for every ordinary caller the frozen split file is
        the authority on which fold a document is in, and a loader that produced
        folds from anywhere else would make the seal unenforceable.

        The False path exists for exactly two callers — the generator that writes
        the split file (it cannot read what it is about to create) and the test
        that checks the file against a recount. Both are in this repository and
        neither is a way to load data for an experiment.
        """
        if not self.corpus_id:
            raise CorpusError(f"{type(self).__name__} does not set corpus_id")
        self.root = root if root is not None else corpus_root(self.corpus_id)
        self.use_split_file = use_split_file
        #: Set for the duration of an authorised sealed read, and only there. An
        #: attribute rather than an argument threaded through `_read()` because
        #: `_read()` is a subclass hook and a flag it had to remember to honour
        #: would be a flag a new loader could forget.
        self._sealed_ok = False
        self._check_type_map()

    def fold_roots(self) -> dict[str, Path]:
        """Fold directory -> the root it lives under. The reachability decision.

        This is the single place that answers "which folds can be read", and it
        answers by returning paths rather than by filtering documents afterwards. A
        sealed fold that is not in this mapping is not skipped downstream — its
        directory is never looked at, so there is no later step that could forget.

        A sealed fold appears here only when `_sealed_ok` is set, which only
        `_authorise_sealed()` does, and only after the access has been logged.
        """
        if not self.fold_dirs:
            raise CorpusError(f"{type(self).__name__} does not set fold_dirs")
        unknown = sorted(set(self.fold_dirs.values()) - set(axis("split")))
        if unknown:
            raise CorpusError(
                f"{self.corpus_id}: fold_dirs maps to {unknown}, which are not "
                f"split values in config/naming.yaml (have: {split_names()})"
            )
        sealed = sealed_root(self.corpus_id)
        roots: dict[str, Path] = {}
        for fold_dir, fold in self.fold_dirs.items():
            if fold in self.sealed_splits and sealed is not None:
                if not self._sealed_ok:
                    continue
                roots[fold_dir] = sealed
            else:
                roots[fold_dir] = self.root
        if self._sealed_ok:
            # An authorised sealed read must actually reach every sealed fold. If
            # `fold_dirs` has no entry for one, the read would return the unsealed
            # folds alone — while the log records a test evaluation that happened.
            # A run that is counted but did not read the fold is worse than a
            # refusal: it spends a row and produces numbers from the wrong data.
            reachable = {self.fold_dirs[d] for d in roots}
            unreachable = sorted(set(self.sealed_splits) - reachable)
            if unreachable:
                raise SealError(
                    f"{self.corpus_id}: a sealed read was authorised but "
                    f"{unreachable} has no entry in fold_dirs, so the fold would "
                    "not be read at all. The log has already recorded this access; "
                    "note in results/sealed_eval_log.md that the run did not "
                    "complete, and fix the loader before running again."
                )
        return roots

    def _check_type_map(self) -> None:
        """Fail at construction if the mapping disagrees with naming.yaml."""
        allowed = axis("phi_type")
        unknown = sorted(set(self.type_map.values()) - set(allowed))
        if unknown:
            raise CorpusError(
                f"{self.corpus_id}: type_map targets {unknown}, which are not "
                f"phi_type values in config/naming.yaml (have: "
                f"{canonical_types()}). Add them there first."
            )
        both = sorted(set(self.type_map) & set(self.excluded_types))
        if both:
            raise CorpusError(
                f"{self.corpus_id}: {both} are both mapped and excluded — a type "
                "is one or the other (DESIGN §9.0, §9.1)"
            )

    # -- subclass hook --

    def _read(self) -> Iterator[Document]:
        raise NotImplementedError

    # -- shared machinery --

    def load(
        self,
        sealed: bool = False,
        *,
        purpose: str | None = None,
        arm: object | None = None,
        iteration: int | None = None,
    ) -> list[Document]:
        """Read the corpus, apply the frozen split, then assert every offset.

        Returns the **unsealed** folds only. For a corpus whose test fold has been
        moved to `sealed/`, that is train and dev; the sealed documents are not on
        disk under the corpus root at all, so this is not a filter that could be
        forgotten — there is nothing there to filter.

        `sealed=True` additionally reads the sealed fold, and only
        `src/eval/run_sealed_eval.py` may pass it. It logs the access before
        reading anything and refuses to proceed if the log cannot be written.
        `purpose`, `arm` and `iteration` go into that log row and are ignored
        otherwise.

        `arm` is typed `object` rather than `sealed_log.Arm` because that import is
        deliberately local to `_authorise_sealed` (a corpus loader that imported the
        evaluation package at module scope would invert the dependency). It is
        validated there, by `record_access`, which is the only thing that consumes it.
        """
        if sealed:
            self._authorise_sealed(purpose=purpose, arm=arm, iteration=iteration)
        try:
            docs = list(self._read())
        finally:
            # Cleared even on failure: a half-read sealed fold must not leave the
            # loader in a state where the next ordinary `load()` reaches it.
            self._sealed_ok = False
        if not docs:
            raise CorpusError(f"{self.corpus_id}: no documents found under the root")
        seen: set[str] = set()
        for doc in docs:
            if doc.doc_id in seen:
                raise CorpusError(f"{self.corpus_id}: duplicate doc_id {doc.doc_id!r}")
            seen.add(doc.doc_id)
            doc.assert_offsets()
        if self.use_split_file:
            self._apply_split_file(docs)
        if not sealed:
            self._assert_no_sealed_fold(docs)
        return docs

    def _authorise_sealed(
        self,
        purpose: str | None = None,
        arm: object | None = None,
        iteration: int | None = None,
    ) -> None:
        """Permit a sealed read, or raise. Called before anything is opened.

        Two conditions, both required:

          - the calling module is `SEALED_CALLER`. Checked by walking the frames'
            `__name__`, which cannot be satisfied by naming a local file
            suggestively — it requires the real module to be on the stack.
          - the access has been appended to `results/sealed_eval_log.md`. The
            append happens **here**, before the read, and a failure to append
            aborts the read. An evaluation that ran without being logged is worse
            than one that did not run, because it leaves the log looking complete.
        """
        import inspect

        callers = set()
        frame = inspect.currentframe()
        try:
            while frame is not None:
                callers.add(frame.f_globals.get("__name__", ""))
                frame = frame.f_back
        finally:
            del frame
        if SEALED_CALLER not in callers:
            raise SealError(
                f"{self.corpus_id}: the sealed fold may only be read from "
                f"{SEALED_CALLER}, and it is not on the call stack. The test fold "
                "is sealed (CLAUDE.md): rule development, agent iteration and "
                "checkpoint selection use dev. If a test evaluation is genuinely "
                "intended, run that script — it records the run in "
                "results/sealed_eval_log.md, which is the number the paper has to "
                "report."
            )

        if sealed_root(self.corpus_id) is None:
            raise SealError(
                f"{self.corpus_id}: a sealed read was requested but the corpus has "
                "no `sealed:` entry in config/data_paths.local.yaml, so no fold of "
                "it is sealed. Do not add one pointing at the unsealed corpus — "
                "seal the fold first (DESIGN §6: generate, freeze, seal)."
            )

        from ..eval.sealed_log import record_access

        # Logged before the flag is set, so a failed append leaves the sealed fold
        # unreachable rather than merely unread. record_access raises SealError.
        # This is the only call site: a second one would put two rows in the log for
        # one read, and the row count is the number the paper reports.
        record_access(self.corpus_id, purpose=purpose, arm=arm, iteration=iteration)
        self._sealed_ok = True

    def _assert_no_sealed_fold(self, docs: list[Document]) -> None:
        """No sealed fold may appear in an ordinary load.

        Belt and braces on top of the physical move: if a corpus is configured as
        sealed but its sealed documents are still reachable under the corpus root,
        the move did not happen or was undone, and every downstream result would be
        computed on data the seal claims is untouched.
        """
        if sealed_root(self.corpus_id) is None:
            return
        leaked = sorted(d.doc_id for d in docs if d.split in self.sealed_splits)
        if leaked:
            raise SealError(
                f"{self.corpus_id}: {len(leaked)} documents of the sealed fold(s) "
                f"{sorted(self.sealed_splits)} loaded from the unsealed corpus "
                f"root (first: {leaked[:3]}). The fold is configured as sealed but "
                "its documents are still present outside sealed/ — the move did "
                "not happen, or was undone."
            )

    def _apply_split_file(self, docs: list[Document]) -> None:
        """Set every document's fold from `splits/{corpus}.json`.

        The split file is the authority, not the directory layout — MEDDOCAN
        happens to encode the fold in its path and no other corpus here does, so
        making the path authoritative would produce a rule that works once. Where
        both exist they are cross-checked and a disagreement raises: a corpus
        re-release that moved a document between folds must stop the run, because
        silently honouring either source would move a document across the seal.

        `verify()` is deliberately not called here. It recounts every span in the
        corpus and load() is on the path of every experiment; the check runs in
        the test suite and in `python3 -m src.split --check`, where its cost is
        paid once rather than on every load.
        """
        from ..split import fold_of, read

        record = read(self.corpus_id)
        assigned = fold_of(record)
        missing = sorted({d.doc_id for d in docs} - set(assigned))
        if missing:
            raise CorpusError(
                f"{self.corpus_id}: {len(missing)} loaded documents are in no "
                f"fold of splits/{self.corpus_id}.json (first: {missing[:3]}). "
                "The corpus on disk and the frozen split disagree; resolve that "
                "before loading — an unfolded document is one the seal does not "
                "cover."
            )

        # A document the split file assigns but that did not load is normally a
        # corpus/file disagreement — except for a sealed fold on an ordinary load,
        # where its absence is exactly what the seal means.
        absent = sorted(set(assigned) - {d.doc_id for d in docs})
        unexplained = [
            doc_id
            for doc_id in absent
            if assigned[doc_id] not in self.sealed_splits
            or sealed_root(self.corpus_id) is None
        ]
        if unexplained:
            raise CorpusError(
                f"{self.corpus_id}: splits/{self.corpus_id}.json assigns "
                f"{len(unexplained)} documents that did not load (first: "
                f"{unexplained[:3]})"
            )
        for doc in docs:
            fold = assigned[doc.doc_id]
            if doc.split is not None and doc.split != fold:
                raise CorpusError(
                    f"{self.corpus_id}/{doc.doc_id}: the corpus places this "
                    f"document in {doc.split!r} but the frozen split file says "
                    f"{fold!r}. Do not proceed: one of the two moved a document "
                    "across the seal."
                )
            doc.split = fold

    def strip_bom(self, text: str) -> tuple[str, int]:
        """Remove a leading BOM. Returns the text and the offset shift.

        DESIGN §9.7, applied identically to every corpus. Note that reading with
        `encoding='utf-8-sig'` instead would be wrong: the shipped offsets count
        the BOM, so decoding it away without shifting breaks every span in the
        file. Read as plain utf-8 and shift here.
        """
        if text.startswith(BOM):
            return text[len(BOM) :], len(BOM)
        return text, 0

    def classify(self, corpus_type: str) -> tuple[str | None, bool]:
        """corpus type -> (canonical type, excluded).

        Every type must be either mapped or explicitly excluded. An unrecognised
        type raises rather than being dropped or bucketed into `OTHER`: a silently
        discarded gold span makes recall look better than it is, and the release
        added a type we have not decided about.
        """
        if corpus_type in self.type_map:
            return self.type_map[corpus_type], False
        if corpus_type in self.excluded_types:
            return None, True
        raise CorpusError(
            f"{self.corpus_id}: annotation type {corpus_type!r} is neither mapped "
            "nor excluded. Decide it in DESIGN §9.0/§9.1 and add it to the "
            "loader; do not drop it."
        )


# ─── registry ───────────────────────────────────────────────────────────────


def _loaders() -> dict[str, type[CorpusLoader]]:
    """Imported lazily so one broken loader cannot break the others."""
    from . import meddocan

    return {meddocan.MeddocanLoader.corpus_id: meddocan.MeddocanLoader}


def load(corpus_id: str, root: Path | None = None) -> list[Document]:
    """Load one corpus by its naming.yaml id."""
    loaders = _loaders()
    if corpus_id not in loaders:
        known = sorted(loaders)
        if corpus_id in axis("corpus"):
            raise CorpusError(
                f"{corpus_id!r} is a known corpus but has no loader yet "
                f"(implemented: {known})"
            )
        raise CorpusError(
            f"{corpus_id!r} is not a corpus in config/naming.yaml "
            f"(have: {corpus_ids()})"
        )
    return loaders[corpus_id](root=root).load()


# ─── counting helpers ───────────────────────────────────────────────────────
# Used by tests and by profile reconciliation. Kept here so that "how many spans
# are in scope" has one implementation rather than one per caller.


def count_by_split(docs: Sequence[Document]) -> dict[str | None, int]:
    counts: dict[str | None, int] = {}
    for doc in docs:
        counts[doc.split] = counts.get(doc.split, 0) + 1
    return counts


def count_spans(docs: Sequence[Document], *, in_scope_only: bool = False) -> int:
    return sum(
        len(doc.in_scope_spans if in_scope_only else doc.spans) for doc in docs
    )


def count_by_type(
    docs: Sequence[Document], *, canonical: bool = True
) -> dict[str, int]:
    """Span counts by canonical type, or by the corpus's own type.

    Excluded spans have no canonical type, so they are absent from the canonical
    tally and present in the subtype one. That asymmetry is the point: the two
    views should not silently reconcile.
    """
    counts: dict[str, int] = {}
    for doc in docs:
        for span in doc.spans:
            key = span.phi_type if canonical else span.subtype
            if key is None:
                continue
            counts[key] = counts.get(key, 0) + 1
    return counts
