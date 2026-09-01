"""The three `port-multi` artefacts: filter the input, validate the response, write the file.

`port-multi` adds three agents that run **once each, before iteration 1**, and each produces
one file the loop then consumes without revising (DESIGN §4, §6.7.1). This module is the
validating and writing half of all three. The calling half is `src/porting/multi.py`, the
prompt-assembling half is `src/llm/prompt.py`, and the reason the three halves are three
modules is `orchestrate`'s: one author per record shape.

**Three artefacts, three failure semantics, and the differences are the design rather than
an inconsistency.** `docs/prompts/profiler.md` §2.3, `mapper.md` §2.3 and
`lexicon_builder.md` §2.3 each state their own, and they do not agree:

- A **profile** with any refusal does not start the loop. It configures the load — a missing
  `bom` has no default, and `stripped` where the corpus counts the BOM shifts every span in
  32 files so that 0 of 761 match (DESIGN §9.7). A partial profile is not a degraded profile.
- A **mapping** with any refusal does not start the loop either, and this holds on every
  corpus *including the ones where the mapping is recorded and never loaded* (§4). Making
  the failure semantics depend on whether DESIGN §9.0 covers the corpus would be a rule that
  branches on which corpus is running, which is the shape CLAUDE.md rejects.
- A **lexicon** refusal drops the entry, counts it, and the arm continues. A lexicon with a
  term missing is a lexicon with a term missing; the consequence is a detection this arm does
  not make, which lands in the leak rate and is *reported*. It is the one of the three whose
  degradation is already visible in the headline number, so it needs no gate to make it
  visible. Two things still stop it: a top-level `malformed`, because then there is no
  artefact at all, and a language outside `corpus_rule_langs`, because a term list under a
  language no rule file is loaded for is unreadable by construction.

**Every validator refuses and none repairs.** That is `audit.parse_response`'s rule at three
more files, and each has a worked case in its prompt: a validator that filled a missing
`offset_base` with `zero` would produce a load convention the agent never claimed and the
whole arm would run under it; one that sent an unmapped label to `OTHER` would move the
leak-rate denominator (DESIGN §9.4); one that escaped a `#` inside a term would put the
character into a file whose reader treats it as structure. So a refusal keeps the field's
name and its reason and **not its value**, and the counts are published beside it — an arm in
which the agent lost the schema is a number rather than a thin artefact.

**No refusal carries a surface form, and on the lexicon that is the whole of the discipline.**
A rejected term is a surface form of unknown provenance; recording it would double the one
artefact the screener denies, to buy a debugging convenience. That is the trade CLAUDE.md
rules out for exception messages, and it is ruled out here for the same reason and without
regard to which corpus is running. The refusal records the file and the reason. `entries` is
counted, never echoed — not into a record, not into an exception message, not into a log line.

**The vocabularies are `config/naming.yaml`'s and this module coins none of them.** All ten
are read through `src/corpora/base.py`'s accessors, which the three prompts each promised the
implementing commit would add. What this module contributes is the part a vocabulary cannot
state: the *pairings* `mapping_basis`'s glosses describe (§2.2's table), the schema shape, and
the structural checks — `uncited_field` against the inventory that was actually sent,
`type_not_in_inventory` against the inventory's own type counts, `unmapped_source_type`
against the type inventory the profile published.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..corpora.base import (
    ROOT, CorpusError, axis, canonical_types, corpus_ids, excluded_types, lexicon_bases,
    lexicon_names, layer_families, mapping_bases, path_template, profile_schema_fields,
    profile_vocabulary, rule_langs, PROFILE_VOCABULARY_FIELDS,
)


class ArtefactError(CorpusError):
    """An out-of-loop artefact that cannot be validated at all.

    Raised where there is **no artefact**, as distinct from an artefact with refusals: a
    response that is not one JSON object, a `lexicons` block naming a language no rule file
    is loaded for, an inventory that is not on disk. Its callers in `src/porting/multi.py`
    turn it into `format_failure.json` with the response verbatim — `orchestrate._write_failure`'s
    record, which is the shape `port-oneshot` already has.

    A subclass of `CorpusError` for `RuleError`'s reason (`rules._lexicon_root`): each of
    the four path builders raises the type its own callers catch, and a driver that had to
    catch three unrelated types per call would catch the wrong one on the day a fourth was
    added.

    **Refusals are not raised.** They are returned beside the artefact, because a refusal is
    a fact about the artefact that has to be *written into it* — `refused` and
    `counts.by_refusal` are published fields, and an exception carries nothing to a file.
    Whether a refusal stops the arm is the driver's call and differs per artefact; see the
    module docstring.
    """


def _digest(text: str) -> str:
    """`sha256:`-prefixed, as `orchestrate._digest` and `prompt._digest` both are."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json(obj: object) -> str:
    """The bytes a hash of a parsed object is taken over. Sorted keys, no spare space.

    `inventory_filtered_sha256` and `profile_sha256` both attest to an *object* rather than
    to a file, because the object is what was sent and the file may not exist yet. So the
    serialisation has to be fixed here: two hashes of the same object taken with different
    separators disagree, and the disagreement reads as two different inventories.

    `ensure_ascii=False` so that a corpus label outside ASCII hashes as itself rather than as
    its escape, and `sort_keys=True` so that a dict built in a different order hashes the
    same. Neither is a preference — they are the two ways this could silently differ between
    the run that wrote the hash and the reader that checks it.
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def render_json(obj: object) -> str:
    """The same object for an agent to read: `_canonical_json` with an indent.

    **Public, and it exists so that `src/llm/prompt.py` does not import `json`.** That module's
    import set is closed by `test_the_module_imports_nothing_that_writes`, for a reason that
    holds here too: a filled prompt carries corpus text, and `json.dump(prompt, fh)` is one line
    from any module that can name `json`. So the two prompts with a structured input — the
    Profiler's inventory and the Mapper's profile — render it through this function, and the
    hashes they record come from `_canonical_json` rather than from a second spelling of it.

    Sorted and non-escaping for `_canonical_json`'s reasons, so that the rendered form and the
    hashed form differ only in the indent. That is what lets the prompt's docstring say the
    hash attests to what the agent read: with a different key order they would be two objects.
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def _write_json(path: Path, payload: dict) -> Path:
    """One JSON artefact, parents made, trailing newline. Returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    return path


def kept_unresolved(obj: dict, admissible: object) -> list[str]:
    """The agent's `unresolved` entries that survived validation, in the order it wrote them.

    Both `validate_mapping` and `validate_lexicon` *refuse* an `unresolved` entry naming
    something they did not keep, and neither returns a cleaned list — so this is the complement
    of those refusals and is deliberately computed from the same two inputs they used, rather
    than by the driver re-deriving what "survived" means. Two artefacts would otherwise each
    have a definition of it, and `mapping_record`/`lexicon_manifest` would be filled from the
    looser of the two.

    `admissible` is whatever the validator kept — mapped-or-excluded source labels for a
    mapping, `{lang}/{name}` keys for a lexicon collection. Deduplicated, because an agent that
    listed a label twice made one claim of uncertainty and `counts.unresolved` is a count of
    claims. A malformed block (not a list of strings) yields the empty list; the refusal for it
    is already recorded, and there is nothing in it to keep.
    """
    raw = obj.get("unresolved")
    if not isinstance(raw, list):
        return []
    allowed = set(admissible)
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item in allowed and item not in out:
            out.append(item)
    return out


def parse_object(text: str, *, what: str) -> dict:
    """The one JSON object an out-of-loop agent returned, or `ArtefactError`.

    All three prompts say the same thing in §2.1 — "Emit the JSON and nothing else. No code
    fence, no ``` line, no `json` language tag, no preamble, no closing remark" — so all three
    parse through one function, and **it strips nothing**. A fence-stripping step is a repair
    with the format-failure count still reading zero (DESIGN §10 A2), which is the judgement
    `orchestrate` already made for `rules/{lang}.yaml`; the three prompts are explicit enough
    that a fence is a response that did not follow them.

    **The message quotes no part of the response.** It reports the length and the type it got.
    A response that is prose rather than JSON is a response whose first hundred characters
    could be anything, including a transcribed annotation line — and an exception message goes
    to a terminal and a CI log, where `release_screen.py` does not reach (CLAUDE.md). The
    response itself is written verbatim to `format_failure.json`, which is a screened path.
    """
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ArtefactError(
            f"the {what} response is not JSON ({type(exc).__name__} at position "
            f"{getattr(exc, 'pos', -1)} of {len(text) if isinstance(text, str) else 0} "
            f"characters). §2.1 asks for one object and no fence; nothing is stripped here, "
            "because stripping is a repair with the failure count still reading zero. No part "
            "of the response is quoted in this message (CLAUDE.md); the response verbatim is "
            "in format_failure.json."
        ) from exc
    if not isinstance(parsed, dict):
        raise ArtefactError(
            f"the {what} response parsed to a {type(parsed).__name__} and §2.1 asks for one "
            f"JSON object. This is the `malformed` refusal's first clause, raised rather than "
            "recorded because a non-object leaves no field for a refusal to name."
        )
    return parsed


# ─── the Profiler's input: the mechanical inventory, filtered ────────────────
#
# `profiler.md` §1.2 removes four blocks, and for each the argument is that the profile's
# fields can be filled without it. The filtering happens **in code, before the call, and it
# returns the filtered object** — not the full inventory with instructions to ignore parts of
# it (§6). An instruction to ignore is not a filter, and `inventory_filtered_sha256` would
# then attest to the wrong bytes.

#: Dotted paths removed from `profiles/{corpus}.raw.json` before the Profiler sees it, in
#: `profiler.md` §1.2's table order. Each is stated rather than derived, because the argument
#: for removing it is specific to it:
#:
#: - `annotation_format.samples_verbatim` — three annotation lines quoted exactly off disk,
#:   containing a date surface and a given name. Justified in the inventory by a human's
#:   screened commit; that justification does not transfer to a file this agent writes. §1.3
#:   supplies an invented worked example instead.
#: - `phi_types_by_split`, `provided_splits.spans_by_split` — per-fold measurements of the
#:   annotations, one fold being sealed. Withholding only the test rows would not work:
#:   total − train − dev **is** test, so the exclusion takes the whole decomposition.
#: - `identifiers.example_multi_document_stems` — named document groups, i.e. which specific
#:   documents share an article.
#: - `identifiers._corrected_2026_08_05` — provenance of a bug in the inventory *script*, and
#:   it names one document id. Not about the corpus.
INVENTORY_REMOVED: tuple[tuple[str, ...], ...] = (
    ("annotation_format", "samples_verbatim"),
    ("phi_types_by_split",),
    ("provided_splits", "spans_by_split"),
    ("identifiers", "example_multi_document_stems"),
    ("identifiers", "_corrected_2026_08_05"),
)

#: `length_distribution.by_split*` — §1.2's one wildcard, and it is a wildcard in the prompt
#: too. The inventory has `by_split_tokens` today and a `by_split_characters` would be the
#: same measurement one unit over; an exact-path list would let that key in silently, which is
#: the whole per-fold decomposition arriving under a name nobody listed. `(parent, prefix)`
#: rather than a bare prefix, so it cannot reach a `by_split` key somewhere else in the file.
INVENTORY_REMOVED_PREFIXES: tuple[tuple[str, str], ...] = (
    ("length_distribution", "by_split"),
)

#: Kept, and named here because §1.2 argues for keeping it: the whole-corpus type counts.
#: An aggregate over all three folds is the corpus's published description of itself; a
#: per-fold figure for the sealed fold is a measurement of the sealed fold. With the per-fold
#: decomposition gone the test rows are not recoverable from what is shown.
#:
#: These are the blocks **whose keys are corpus type labels**, and they are dotted paths
#: rather than top-level names because `phi_types_xml_two_level` is not a label→count map: it
#: holds `coarse_categories`, `fine_types` and `category_to_type_pairs`. A generic walk over
#: it — every key at every depth — was the first version of this and it was wrong in a way
#: worth recording: it made `fine_types` and `LOCATION/TERRITORIO` into corpus type labels, so
#: a `type_inventory` listing either would have passed `type_not_in_inventory` and the Mapper
#: would have been asked to map a sub-block name. Naming the two blocks that do hold labels is
#: the check the refusal claims to be.
#:
#: `category_to_type_pairs` is deliberately absent: its keys are `COARSE/FINE` pair strings,
#: which are two labels joined and not a label. The pairing it records is a fact about the
#: type system that `type_system_level` is the profile's field for.
#:
#: A path missing from a corpus's inventory is skipped rather than refused — a corpus with a
#: flat type system has no two-level block, and requiring both would make this the one check
#: that fails on the simpler corpus. `inventory_type_labels()` requires the union to be
#: non-empty, which is the condition that actually matters.
INVENTORY_TYPE_COUNT_PATHS: tuple[tuple[str, ...], ...] = (
    ("phi_types_brat_flat",),
    ("phi_types_xml_two_level", "coarse_categories"),
    ("phi_types_xml_two_level", "fine_types"),
)


def inventory_path(corpus: str, *, root: Path | None = None) -> Path:
    """`paths.inventory` for one corpus — the hand-written inventory script's output.

    `paths.inventory` and not a literal, for `profile.json`'s sake: that file records the
    sha256 of the object it was sent, and what the hash refers to is decided by the path. A
    literal here would put that answer in a module and leave the published record pointing at
    nothing a reader can name (CLAUDE.md).
    """
    if corpus not in corpus_ids():
        raise ArtefactError(
            f"{corpus!r} is not a corpus in config/naming.yaml (have: {corpus_ids()}). The "
            "inventory is the Profiler's only input and a corpus the config does not declare "
            "would be a file no arm can be run for."
        )
    return (root or ROOT) / path_template("inventory").format(corpus=corpus)


def read_inventory(corpus: str, *, root: Path | None = None) -> dict:
    """The unfiltered inventory, parsed. **Not what the agent is shown** — `filter_inventory`.

    Two functions rather than one so that the unfiltered object exists as a named value the
    filter is applied to: a single `read_filtered()` would make "the Profiler was not shown
    the per-fold decomposition" a claim about the inside of a function, and the test that
    checks it would have nothing to compare against.
    """
    path = inventory_path(corpus, root=root)
    if not path.is_file():
        raise ArtefactError(
            f"{corpus}: no mechanical inventory at {path.relative_to(root or ROOT)}. It is "
            "produced by a hand-written inventory script and committed; the Profiler has no "
            "other input (docs/prompts/profiler.md §1.1) and cannot be called without it."
        )
    with open(path, encoding="utf-8") as fh:
        parsed = json.load(fh)
    if not isinstance(parsed, dict):
        raise ArtefactError(
            f"{corpus}: the inventory parsed to a {type(parsed).__name__} and the filter, "
            "the field-path index and the type counts all read it as an object."
        )
    return parsed


def filter_inventory(raw: dict) -> dict:
    """What the Profiler is shown: the inventory with §1.2's four blocks removed.

    **Removals are refused when the key is not there.** A filter that tolerated a missing key
    is a filter that keeps working after the inventory renames the block it was removing, and
    the block then arrives in the prompt under its new name with nothing failing. So a path in
    `INVENTORY_REMOVED` that is absent raises — the inventory and this list are two halves of
    one decision and they are supposed to be edited together.

    The prefix rule is the exception and deliberately so: `INVENTORY_REMOVED_PREFIXES` may
    match zero keys, because it exists to catch a key that does not exist *yet*. It is not
    doing the work of an exact path; `length_distribution.by_split_tokens` is also not in the
    exact list, so a `length_distribution` block that lost every `by_split*` key would pass —
    which is the state the prompt wants, one measurement fewer to withhold.

    A deep copy in the shape of a re-parse, so that the returned object shares no mutable
    substructure with the caller's. The alternative — deleting from `raw` — would make the
    unfiltered object unavailable after the filter ran, and `read_inventory`'s docstring is
    about why it has to stay available.
    """
    filtered = json.loads(_canonical_json(raw))
    for dotted in INVENTORY_REMOVED:
        node = filtered
        for step in dotted[:-1]:
            if not isinstance(node, dict) or step not in node:
                raise ArtefactError(
                    f"the inventory has no {'.'.join(dotted)} to remove (missing at "
                    f"{step!r}). docs/prompts/profiler.md §1.2 removes it before the call; a "
                    "filter that tolerated the absence would keep passing after the "
                    "inventory renamed the block, and the block would then be shown."
                )
            node = node[step]
        if not isinstance(node, dict) or dotted[-1] not in node:
            raise ArtefactError(
                f"the inventory has no {'.'.join(dotted)} to remove. §1.2 removes it before "
                "the call; a filter that tolerated the absence would keep passing after a "
                "rename, and the block would then be shown."
            )
        del node[dotted[-1]]
    for parent, prefix in INVENTORY_REMOVED_PREFIXES:
        node = filtered.get(parent)
        if isinstance(node, dict):
            for key in [k for k in node if isinstance(k, str) and k.startswith(prefix)]:
                del node[key]
    return filtered


def inventory_field_paths(obj: object, *, _prefix: str = "") -> frozenset[str]:
    """Every dotted field path in the filtered inventory. `uncited_field`'s authority.

    `profiler.md` §2.1: each `cites` entry is a dotted path into the inventory the agent was
    shown, and a path that does not exist there is a refusal — "which makes `cites` the one
    part of this artefact whose honesty is *verifiable*, rather than declared". So the index is
    built from the object that was actually sent, not from the file on disk: a run that
    filtered differently has a different index, and a citation checked against the unfiltered
    file would accept a path to a block §1.2 removed.

    Every prefix of a path is included, so `cites: {"annotation_encoding": "pairing.brat"}`
    and a citation of `pairing` alone both resolve. Lists are indexed by position
    (`offsets.mismatch_examples.0`), because a citation of an element is a citation of
    something in the object.
    """
    found: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{_prefix}{key}"
            found.add(path)
            found |= inventory_field_paths(value, _prefix=f"{path}.")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            path = f"{_prefix}{index}"
            found.add(path)
            found |= inventory_field_paths(value, _prefix=f"{path}.")
    return frozenset(found)


def inventory_type_labels(obj: dict) -> frozenset[str]:
    """The corpus's own type labels, from the inventory's type counts. §2.3's authority.

    `type_not_in_inventory` refuses a `type_inventory` label absent from here — an invented
    corpus label, which the Mapper would then map to a type no gold span carries.

    **Every count block, unioned, and not the one the profile's `type_system_level` names.**
    The flat and two-level blocks are the same annotations twice (`profiler.md` §1.1), so a
    label is the corpus's whether the agent read it off one or the other, and refusing a
    two-level label because the profile said `flat` would refuse for a *disagreement between
    two fields* under a reason that says the label is invented. That disagreement is real and
    the place it is visible is the load: the loader reads brat, so a two-level inventory paired
    with `brat_standoff` produces types the type map has no row for.

    The coarse and the fine level are both included, because a mapping may legitimately be
    written over either — which level it is written over is `type_system_level`'s claim rather
    than this function's.
    """
    labels: set[str] = set()
    for dotted in INVENTORY_TYPE_COUNT_PATHS:
        block: object = obj
        for step in dotted:
            block = block.get(step) if isinstance(block, dict) else None
        if isinstance(block, dict):
            labels |= {name for name in block if isinstance(name, str)}
    if not labels:
        raise ArtefactError(
            "the filtered inventory carries no type counts under any of "
            f"{['.'.join(p) for p in INVENTORY_TYPE_COUNT_PATHS]}, so `type_not_in_inventory` "
            "has nothing to check a label against. §1.2 keeps the whole-corpus counts "
            "deliberately — with them gone every label the agent copied would be refused as "
            "invented."
        )
    return frozenset(labels)


# ─── the Profiler's output: profile.json ─────────────────────────────────────

#: The two keys in the profile that are metadata about the fields rather than fields.
#: Neither may be listed in `unresolved` — a claim of uncertainty about `cites` is not a claim
#: about a load convention — and `profile_schema_fields()` therefore excludes both.
PROFILE_META: tuple[str, ...] = ("cites", "unresolved")

#: `patient_key_available` is the one schema field whose value is neither a vocabulary member
#: nor a corpus label. Named rather than special-cased inline, so that the schema's three
#: kinds of field are readable in one place: nine vocabularies
#: (`PROFILE_VOCABULARY_FIELDS`), one corpus-label list (`type_inventory`), one boolean.
PROFILE_BOOLEAN_FIELD = "patient_key_available"
PROFILE_LABEL_FIELD = "type_inventory"


def validate_profile(obj: dict, *, inventory: dict) -> tuple[dict, list[dict]]:
    """`(profile, refused)` for one Profiler response. `docs/prompts/profiler.md` §2.3.

    The agent's object is returned **unchanged except that refused fields are absent** — like
    `rules/{lang}.yaml` and unlike `audit_report.json`, whose flags need a coordinate
    translation the agent must not perform. Nothing here needs translating, so validation
    removes whole fields or nothing.

    `refused` is a list of `{"field": …, "reason": …}` in the order the checks ran, with the
    reason drawn from `profile_refusal`. **A refused field keeps its name and its reason and
    not its value**: a value outside a declared vocabulary is a value the project has no
    meaning for, and recording it would put an undeclared string into a file under `results/`,
    which is what the vocabulary rule exists to prevent.

    Six refusals, and the order they run in is load-bearing at one point. `unknown_field`
    goes first, because a prose key is the field most likely to be added and the one §2.1
    forbids outright; `missing_field` next, so that a response missing eight fields reports
    eight absences rather than eight vocabulary failures on `None`. `uncited_field` runs last
    because it is the only check that reads the inventory, and a `cites` block citing a field
    that was itself refused is still a citation that either resolves or does not — the two
    questions are independent and are kept so.

    **Every refusal a caller can see is in the returned list, and there is no third channel.**
    A profile with any refusal does not start the loop (§2.3), so the driver reads `len(refused)`
    and nothing else; a check that raised for one kind of malformation and returned for another
    would make that read wrong for a reason invisible at the call site. The exceptions this
    can still raise are `parse_object`'s, which run before it.
    """
    schema = profile_schema_fields()
    refused: list[dict] = []
    kept = dict(obj)

    def refuse(field: str, reason: str) -> None:
        refused.append({"field": field, "reason": reason})
        kept.pop(field, None)

    for key in [k for k in kept if k not in schema and k not in PROFILE_META]:
        refuse(key, "unknown_field")
    for field in schema:
        if field not in kept:
            refused.append({"field": field, "reason": "missing_field"})

    for field in PROFILE_VOCABULARY_FIELDS:
        if field not in kept:
            continue
        if kept[field] not in profile_vocabulary(field):
            refuse(field, "undeclared_value")

    if PROFILE_BOOLEAN_FIELD in kept and not isinstance(kept[PROFILE_BOOLEAN_FIELD], bool):
        refuse(PROFILE_BOOLEAN_FIELD, "malformed")

    if PROFILE_LABEL_FIELD in kept:
        labels = kept[PROFILE_LABEL_FIELD]
        if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
            refuse(PROFILE_LABEL_FIELD, "malformed")
        elif not set(labels) <= inventory_type_labels(inventory):
            # The count and not the labels. A refusal message and a refusal record both reach
            # a reader, and the labels absent from the inventory are strings of unknown
            # provenance — the agent may have invented them or copied them from somewhere
            # this project did not show it (CLAUDE.md, and §3's own argument that
            # `type_inventory` is the one field accepting arbitrary text).
            refuse(PROFILE_LABEL_FIELD, "type_not_in_inventory")

    unresolved = kept.get("unresolved")
    if unresolved is not None:
        if not isinstance(unresolved, list) or not all(isinstance(x, str) for x in unresolved):
            refuse("unresolved", "malformed")
        elif not set(unresolved) <= set(schema):
            refuse("unresolved", "malformed")

    cites = kept.get("cites")
    if cites is not None:
        if not isinstance(cites, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in cites.items()):
            refuse("cites", "malformed")
        else:
            available = inventory_field_paths(inventory)
            for field, dotted in sorted(cites.items()):
                if dotted not in available:
                    # `field` and not `dotted`: the refusal names the profile field whose
                    # citation failed. A dotted path is a key rather than corpus text, so
                    # either would be publishable, but the record's `field` column is the
                    # profile's own schema throughout and one row that held an inventory path
                    # instead would not group with the rest.
                    refused.append({"field": field, "reason": "uncited_field"})
    return kept, refused


def profile_counts(profile: dict, refused: list[dict]) -> dict:
    """`counts` for `profile.json` — fields kept, unresolved, refused, and by reason.

    `counts.fields` counts the **schema fields that survived**, not the keys of the object:
    `cites` and `unresolved` are metadata (`PROFILE_META`), and counting them would make a
    profile that lost two conventions and gained a `cites` block read as no shorter.
    """
    schema = set(profile_schema_fields())
    by_refusal: dict[str, int] = {}
    for entry in refused:
        reason = entry["reason"]
        by_refusal[reason] = by_refusal.get(reason, 0) + 1
    unresolved = profile.get("unresolved")
    return {
        "fields": sum(1 for key in profile if key in schema),
        "unresolved": len(unresolved) if isinstance(unresolved, list) else 0,
        "refused": len(refused),
        "by_refusal": {reason: by_refusal[reason] for reason in sorted(by_refusal)},
    }


def profile_record(profile: dict, refused: list[dict], *, corpus: str, porting: str,
                   inventory: dict) -> dict:
    """The `profile.json` object. `docs/prompts/profiler.md` §2.2.

    The orchestrator adds `corpus`, `porting`, the inventory's own `generated` date, the
    sha256 of the **filtered** inventory it sent, and the `refused` list. **It adds no claim
    and removes only claims it can prove impossible.**

    `inventory_filtered_sha256` is what makes §1.2 checkable after the fact: "the Profiler was
    not shown the per-fold decomposition" is otherwise a claim about a prompt that is never
    written down (§6). The hash is of the filtered object — so a later run that filtered
    differently has a different hash and the two profiles are visibly not comparable — and it
    is taken here rather than passed in, because a caller that computed it could compute it
    over the unfiltered object and nothing in the file would say so.
    """
    return {
        "corpus": corpus,
        "porting": porting,
        "inventory_generated": inventory.get("generated"),
        "inventory_filtered_sha256": _digest(_canonical_json(inventory)),
        "profile": profile,
        "refused": refused,
        "counts": profile_counts(profile, refused),
    }


# ─── the Mapper's output: mapping.yaml ───────────────────────────────────────

#: DESIGN §9.0's table, transcribed for the comparison `docs/prompts/mapper.md` §4.2
#: pre-registers: source label → canonical type, per corpus. Keyed by corpus, and a corpus with
#: no entry is a corpus §9.0 has no row for — which is what `applied` reads off (§4.3: on such a
#: corpus the mapping is load-bearing, `applied` is `agent`, and `compared_against_design` is 0).
#:
#: **The mapped half only, and the excluded half is the complement rather than a second list.**
#: The first version of this transcribed §9.1 as well, as `{source label: exclusion concept}` —
#: `SEXO_SUJETO_ASISTENCIA` → `SEXO`. That was wrong twice over and
#: `tests/test_excluded_types.py::test_no_exclusion_name_is_spelled_in_a_module` caught it:
#:
#: - **§9.1 states no such pairing.** It names source labels with span counts
#:   (`SEXO_SUJETO_ASISTENCIA` 1,841, `FAMILIARES_*` 416, `NAME_TITLE` 139) and gives the
#:   reasons per *concept*, not per label. `corpora.meddocan.EXCLUDED_TYPES` is a frozenset of
#:   source labels and carries no concept either. The pairing was invented here, which is
#:   writing the answer key — the thing §4.2 refuses when it declines to compare `basis`.
#: - **A module may not hold an exclusion concept name.** They land in a prompt and are read
#:   from `excluded_types()`; a module holding one is a second place §9.1's list can be
#:   shortened. On `de-grascco` the collision is exact — its source label and the concept are
#:   spelled the same — so the test cannot tell a transcription from a copy, and it is right
#:   not to try.
#:
#: So `compare_with_design()` reads "excluded" as "in `type_inventory` and not in this table",
#: which §9.0's own exhaustiveness claim licenses: "Both corpora map into it exhaustively — no
#: gold span is silently dropped, and the two columns reconcile to each corpus's full span
#: count." §9.0 plus §9.1 partition a corpus's labels, so the complement of one is the other.
#:
#: The hazard the complement carries is the opposite of a stale copy's: a label the table has no
#: row for reads as excluded rather than as a gap. That is closed by a test rather than a
#: convention — `tests/test_artefacts.py` asserts this table equals `corpora.meddocan.TYPE_MAP`
#: and that the complement over the loader's own labels equals `EXCLUDED_TYPES`, so a table
#: that fell behind the loader fails there. `counts.compared_against_design` is the second
#: guard from the other side: a mapping compared against a stale table shows up as a count that
#: does not equal `counts.source_types`.
DESIGN_MAPPINGS: dict[str, dict[str, str]] = {
    "es-meddocan": {
        "NOMBRE_SUJETO_ASISTENCIA": "NAME",
        "NOMBRE_PERSONAL_SANITARIO": "NAME",
        "FECHAS": "DATE",
        "EDAD_SUJETO_ASISTENCIA": "AGE",
        "TERRITORIO": "LOCATION_AREA",
        "PAIS": "LOCATION_AREA",
        "CALLE": "LOCATION_STREET",
        "HOSPITAL": "ORGANISATION",
        "INSTITUCION": "ORGANISATION",
        "CENTRO_SALUD": "ORGANISATION",
        "CORREO_ELECTRONICO": "CONTACT",
        "NUMERO_TELEFONO": "CONTACT",
        "NUMERO_FAX": "CONTACT",
        "ID_SUJETO_ASISTENCIA": "ID",
        "ID_ASEGURAMIENTO": "ID",
        "ID_TITULACION_PERSONAL_SANITARIO": "ID",
        "ID_CONTACTO_ASISTENCIAL": "ID",
        "ID_EMPLEO_PERSONAL_SANITARIO": "ID",
        "PROFESION": "PROFESSION",
        "OTROS_SUJETO_ASISTENCIA": "OTHER",
    },
    "de-grascco": {
        "NAME_PATIENT": "NAME",
        "NAME_DOCTOR": "NAME",
        "NAME_RELATIVE": "NAME",
        "NAME_USERNAME": "NAME",
        "NAME_EXT": "NAME",
        "DATE": "DATE",
        "DATE_BIRTH": "DATE",
        "AGE": "AGE",
        "LOCATION_CITY": "LOCATION_AREA",
        "LOCATION_ZIP": "LOCATION_AREA",
        "LOCATION_COUNTRY": "LOCATION_AREA",
        "LOCATION_STREET": "LOCATION_STREET",
        "LOCATION_HOSPITAL": "ORGANISATION",
        "LOCATION_ORGANIZATION": "ORGANISATION",
        "CONTACT_EMAIL": "CONTACT",
        "CONTACT_PHONE": "CONTACT",
        "CONTACT_FAX": "CONTACT",
        "ID": "ID",
        "PROFESSION": "PROFESSION",
    },
}

#: What §9.0's table is, in the words `mapping.yaml` records it under. A string in the file
#: rather than a path, because §9.0 is a section of a document and not a file this repository
#: can point a reader at by name.
DESIGN_MAPPING_SOURCE = "DESIGN.md §9.0"

#: The two values `mapping.yaml`'s `applied` may take (`mapper.md` §2.3, §4.3). Not a
#: `naming.yaml` vocabulary and deliberately: it is not the agent's to set and not a value
#: anything chooses — it is `corpus in DESIGN_MAPPINGS` in the file's own words, so a config
#: block would be a second place the same predicate is evaluated. The field exists so that a
#: reader of one file can tell which regime it is under without knowing which corpora §9.0
#: lists.
APPLIED_DESIGN = "design"
APPLIED_AGENT = "agent"


def merge_declaring_types() -> frozenset[str]:
    """Canonical types whose `naming.yaml` gloss declares a merge. `mapping_basis`'s pairing.

    `source_type_is_coarser` may appear only on a `map` entry "whose target is a canonical
    type whose gloss declares a merge" (`mapper.md` §2.2), and `LOCATION_AREA` is the one
    today: "place names and postcodes, merged per DESIGN §9.2".

    Read from the gloss rather than hardcoded as `{"LOCATION_AREA"}`, which is
    `sample.non_target_types()`'s judgement at a second field: the fact is one naming.yaml
    already states, a second copy in Python is a second thing to keep in sync, and a corpus
    whose canonical type declares another merge is covered without an edit here.

    **The empty set raises rather than returning.** That is the one place this differs from
    `non_target_types()`, and the reason is what the caller does with it: an empty
    non-target set means no type is excluded from the sample, which is a coherent state, while
    an empty merge set silently converts every valid `source_type_is_coarser` into a
    `basis_mismatch` — a gloss reword failing an arm at a place with no connection to the
    edit. `tests/test_artefacts.py` pins the membership as well, so a reword fails a test
    before it fails an arm.
    """
    found = frozenset(
        name for name, gloss in axis("phi_type").items()
        if isinstance(gloss, str) and "merged per DESIGN" in gloss)
    if not found:
        raise ArtefactError(
            "no phi_type in config/naming.yaml declares a merge in its gloss, so "
            "`source_type_is_coarser` has no admissible target and every use of it would be "
            "refused as a basis_mismatch. DESIGN §9.2 merges place names and postcodes into "
            "LOCATION_AREA and the axis's gloss is where that is stated; a reword that lost "
            "it is the likely cause."
        )
    return found


def _label_family(label: str) -> str:
    """The stem `source_label_family` claims a family shares: everything before the first `_`.

    `mapper.md` §2.2 requires "at least two inventory labels must share the prefix" and does
    not define the prefix, so it is defined here and narrowly. The first underscore-separated
    component is what makes `ID_SUJETO_ASISTENCIA`, `ID_ASEGURAMIENTO` and three more one
    family, and `NOMBRE_SUJETO_ASISTENCIA` / `NOMBRE_PERSONAL_SANITARIO` another.

    **A character-prefix rule was the alternative and it is worse in a specific way**: on any
    prefix length it either splits `ID_*` from `ID_*` or joins `NOMBRE_*` to nothing, and the
    length would be a number with no argument behind it. A label with no underscore is its own
    family of one, which fails the "at least two" test — correctly, since a corpus with one
    such label has no family for the basis to appeal to.
    """
    return label.split("_", 1)[0]


def validate_mapping(obj: dict, *, type_inventory: list[str]) -> tuple[dict, dict, list[dict]]:
    """`(map, excluded, refused)` for one Mapper response. `docs/prompts/mapper.md` §2.3.

    Eight refusals, each recorded as `{"source_type": …, "reason": …}` with the reason from
    `mapping_refusal`. The agent's entries are returned unchanged except that refused ones are
    absent; a refused entry keeps its source label and its reason and not its assignment.

    **`unmapped_source_type` is the exhaustiveness check and it is checked against
    `type_inventory`, which is the profile's claim rather than the corpus.** §1.1 states the
    limit and it is not softened here: if the profile's inventory is short a label, the Mapper
    maps a short list and this check passes. It verifies that the Mapper *copied its input*,
    not that the input was right. What makes partial coverage a refusal rather than a thin
    mapping is DESIGN §9.0's exhaustiveness claim — an unmapped label is exactly a silently
    dropped set of gold spans.

    `type_inventory` is required and has no default. An empty list would make every label
    `type_not_in_inventory` and every inventory label vacuously mapped, i.e. a mapping that
    refuses everything the agent wrote and complains about nothing it omitted — so the caller
    passes the profile's list, and a profile that lost the field never reached this function
    (a refused profile stops the arm).
    """
    if not isinstance(type_inventory, list) or not all(
            isinstance(x, str) for x in type_inventory):
        raise ArtefactError(
            f"type_inventory must be a list of the corpus's own labels, got "
            f"{type_inventory if not isinstance(type_inventory, list) else 'a list of ' + str(sorted({type(x).__name__ for x in type_inventory}))}. "
            "It is `profile.type_inventory` as the Profiler wrote it, and the exhaustiveness "
            "check has nothing to be exhaustive over without it (mapper.md §1.1)."
        )
    inventory = list(dict.fromkeys(type_inventory))
    canonical = set(canonical_types())
    excluded_concepts = set(excluded_types())
    bases = set(mapping_bases())
    merges = merge_declaring_types()
    families = {}
    for label in inventory:
        families[_label_family(label)] = families.get(_label_family(label), 0) + 1

    refused: list[dict] = []
    raw_map = obj.get("map")
    raw_excluded = obj.get("excluded")
    raw_unresolved = obj.get("unresolved")

    for key in obj:
        if key not in ("map", "excluded", "unresolved"):
            refused.append({"source_type": key, "reason": "unknown_field"})
    for name, block in (("map", raw_map), ("excluded", raw_excluded)):
        if block is not None and not isinstance(block, dict):
            raise ArtefactError(
                f"the mapping's {name!r} is a {type(block).__name__} and §2.1 makes it an "
                "object of source label → assignment. This is `malformed`'s first clause, "
                "raised rather than recorded because a non-object leaves no entry for a "
                "refusal to name."
            )

    kept_map: dict[str, dict] = {}
    kept_excluded: dict[str, dict] = {}
    seen: set[str] = set()

    def entry_refuse(label: str, reason: str) -> None:
        refused.append({"source_type": label, "reason": reason})

    for side, raw, kept, value_key, vocabulary in (
        ("map", raw_map or {}, kept_map, "canonical", canonical),
        ("excluded", raw_excluded or {}, kept_excluded, "excluded_type", excluded_concepts),
    ):
        for label in raw:
            entry = raw[label]
            if label in seen:
                entry_refuse(label, "duplicate_source_type")
                continue
            seen.add(label)
            if not isinstance(entry, dict):
                entry_refuse(label, "malformed")
                continue
            unknown = [k for k in entry if k not in (value_key, "basis")]
            if unknown:
                entry_refuse(label, "unknown_field")
                continue
            if value_key not in entry or "basis" not in entry:
                entry_refuse(label, "missing_field")
                continue
            if label not in inventory:
                entry_refuse(label, "type_not_in_inventory")
                continue
            if entry[value_key] not in vocabulary or entry["basis"] not in bases:
                entry_refuse(label, "undeclared_value")
                continue
            basis = entry["basis"]
            # §2.2's table, and the four rows are checked rather than the one that is easy.
            # `design_exclusion` on a `map` entry and any other basis on an `excluded` one are
            # the two halves of the same row; `residual_bucket` and `source_type_is_coarser`
            # constrain the target as well, which is what makes the basis evidence rather
            # than a label the agent chose freely.
            mismatch = (
                (side == "excluded") != (basis == "design_exclusion")
                or (basis == "residual_bucket" and entry.get(value_key) != _residual_type())
                or (basis == "source_type_is_coarser" and entry.get(value_key) not in merges)
                or (basis == "source_label_family" and families.get(_label_family(label), 0) < 2)
            )
            if mismatch:
                entry_refuse(label, "basis_mismatch")
                continue
            kept[label] = dict(entry)

    for label in inventory:
        if label not in kept_map and label not in kept_excluded:
            # Reported once per label and *after* the entry checks, so a label whose entry was
            # refused for a bad basis is not also reported as unmapped: the two readings would
            # double-count one mistake, and `counts.by_refusal` is what a reader totals.
            if label not in seen:
                refused.append({"source_type": label, "reason": "unmapped_source_type"})

    if raw_unresolved is not None:
        assigned = set(kept_map) | set(kept_excluded)
        if not isinstance(raw_unresolved, list) or not all(
                isinstance(x, str) for x in raw_unresolved):
            refused.append({"source_type": "unresolved", "reason": "malformed"})
        else:
            for label in raw_unresolved:
                if label not in assigned:
                    refused.append({"source_type": label, "reason": "malformed"})
    return kept_map, kept_excluded, refused


def _residual_type() -> str:
    """The canonical residual bucket, `residual_bucket`'s only admissible target.

    `sample.non_target_types()` identifies it from the gloss ("residual bucket shipped by a
    corpus; not a rule-development target") rather than from the string `OTHER`, and this
    reads the same gloss for the same reason. A singleton is required: two residual buckets
    would make the pairing ambiguous and one would make `residual_bucket` unusable, and both
    are config states a person should be told about rather than have inferred.
    """
    found = sorted(
        name for name, gloss in axis("phi_type").items()
        if isinstance(gloss, str) and "residual bucket" in gloss)
    if len(found) != 1:
        raise ArtefactError(
            f"config/naming.yaml declares {len(found)} canonical types whose gloss names a "
            f"residual bucket ({found}), and `residual_bucket`'s pairing in mapper.md §2.2 "
            "needs exactly one admissible target. DESIGN §9.4 makes OTHER that type."
        )
    return found[0]


def compare_with_design(corpus: str, kept_map: dict, kept_excluded: dict, *,
                        type_inventory: list[str]) -> tuple[list[dict], int, str]:
    """`(disagreements, compared, applied)` against DESIGN §9.0. `mapper.md` §4.1–4.2.

    On any corpus §9.0 covers, **§9.0's mapping is what loads and this is a recording**: the
    agent's mapping is written, compared entry by entry, and every disagreement is recorded
    with `applied: design`. Nothing is overwritten and **no disagreement fails the arm** —
    refusing on disagreement would make the arm run only when the agent reproduces the human
    table, which converts the one measurement the Mapper can produce into a gate and destroys
    it. A disagreement rate is not observable if disagreement is fatal.

    Compared over `type_inventory`, label by label. A label agrees when the agent's `canonical`
    equals §9.0's, or when both sides exclude it. **A label the agent maps and §9.0 excludes is
    a disagreement, and so is the converse** — the excluded/mapped boundary is where a
    disagreement costs the most (§9.1's 9.90% and 9.68% of gold), so it is not a separate
    category and not omitted.

    **Which exclusion concept the agent chose is not compared, for `basis`'s reason.** §9.1
    excludes three *concepts* and names the source labels they cover; it assigns no concept per
    label, and neither does the loader (`EXCLUDED_TYPES` is a frozenset). There is nothing on
    the design side to compare against, and supplying one would be inventing the answer key —
    see `DESIGN_MAPPINGS`. The concept the agent named is still validated, against
    `excluded_types()`, in `validate_mapping`.

    `compared` is `counts.compared_against_design`: how many labels the comparison covered, so
    that a mapping compared against a stale transcription of §9.0 is visible as a count that
    does not equal `counts.source_types`.

    **`basis` is not compared.** §9.0 gives no basis for its assignments — the table is a
    table — so there is nothing to compare against, and inventing one would be writing the
    answer key after seeing the answer.

    On a corpus with no §9.0 row this returns `([], 0, "agent")`: the comparison does not run,
    and the mapping is load-bearing. §4.3 records that as a larger claim needing a decision
    before it runs, which is why the value is reported rather than defaulted away.
    """
    design = DESIGN_MAPPINGS.get(corpus)
    if design is None:
        return [], 0, APPLIED_AGENT
    disagreements: list[dict] = []
    compared = 0
    for label in dict.fromkeys(type_inventory):
        compared += 1
        agent = (kept_map.get(label) or {}).get("canonical")
        agent_excluded = label in kept_excluded
        theirs = design.get(label)
        # The complement, per `DESIGN_MAPPINGS`: §9.0 maps exhaustively, so a label of this
        # corpus that the table has no row for is one §9.1 excluded.
        design_excluded = theirs is None
        if agent_excluded and design_excluded:
            continue
        if not agent_excluded and not design_excluded and agent == theirs:
            continue
        disagreements.append({
            "source_type": label,
            # Both sides say what they said, including saying nothing: a label the agent left
            # out after a refusal reads `null` here, which is the true statement and is what
            # distinguishes "the agent disagreed" from "the agent produced no entry". The
            # agent's exclusion concept is reported because it is what the agent wrote; it is
            # not what the disagreement was computed from (see the docstring).
            "agent": agent if agent is not None else (
                kept_excluded.get(label) or {}).get("excluded_type"),
            "agent_excluded": agent_excluded,
            "design": theirs,
            "design_excluded": design_excluded,
        })
    return disagreements, compared, APPLIED_DESIGN


def mapping_record(kept_map: dict, kept_excluded: dict, refused: list[dict], *, corpus: str,
                   porting: str, profile: dict, type_inventory: list[str],
                   unresolved: list[str]) -> dict:
    """The `mapping.yaml` object. `docs/prompts/mapper.md` §2.3.

    The orchestrator adds `corpus`, `porting`, the profile hash, the `refused` list, the
    `disagreements` list, `applied` and the counts. **It adds no mapping claim.**

    `profile_sha256` is `inventory_filtered_sha256`'s analogue one artefact along, and §1.3
    says why this artefact needs no hash of its own input: the Mapper's input is assembled
    from two tracked files, one of which is in the frozen window (`config/naming.yaml`) and
    the other of which carries its own attestation (`profile.json`). So what has to be
    unforgeable is the join — that a mapping and the type inventory it mapped cannot be
    separated after the fact — and the profile's hash is that.

    The hash is over the **agent's validated profile object**, not over the whole
    `profile.json` record: the record carries `refused` and `counts`, which are the
    orchestrator's, and a mapping written twice from one profile would then hash differently
    if a count changed. What the Mapper read is the profile.
    """
    by_refusal: dict[str, int] = {}
    for entry in refused:
        by_refusal[entry["reason"]] = by_refusal.get(entry["reason"], 0) + 1
    disagreements, compared, applied = compare_with_design(
        corpus, kept_map, kept_excluded, type_inventory=type_inventory)
    return {
        "corpus": corpus,
        "porting": porting,
        "profile_sha256": _digest(_canonical_json(profile)),
        "design_mapping_source": DESIGN_MAPPING_SOURCE,
        "mapping": kept_map,
        "excluded": kept_excluded,
        "unresolved": list(unresolved),
        "refused": refused,
        "disagreements": disagreements,
        "applied": applied,
        "counts": {
            "source_types": len(dict.fromkeys(type_inventory)),
            "mapped": len(kept_map),
            "excluded": len(kept_excluded),
            "unresolved": len(unresolved),
            "refused": len(refused),
            "by_refusal": {reason: by_refusal[reason] for reason in sorted(by_refusal)},
            "disagreements": len(disagreements),
            "compared_against_design": compared,
        },
    }


# ─── the LexiconBuilder's output: lexicons/{lang}/*.txt and the manifest ─────

#: The shortest term a list may carry. `lexicon_builder.md` §2.3: "a one- or two-character
#: gazetteer term matches everywhere; `city_name_gazetteer` already ran at 0.621 precision
#: with ordinary place names". A number with a measurement behind it, unlike the entry *count*
#: cap the prompt declines to set for want of one.
MIN_ENTRY_CHARS = 3

#: What `entry_contains_comment_mark` and `entry_contains_newline` are about: the `.txt`
#: format's own structure. `src/rules.py`'s `_read_lexicon` reads one term per line and treats
#: `#` as starting a comment, so a term containing either would be read as something other
#: than itself. Refused rather than escaped — escaping would let the character into a file
#: whose reader treats it as structure (§2.2).
_COMMENT_MARK = "#"
_NEWLINES = ("\n", "\r")


def _vocabulary_terms() -> frozenset[str]:
    """Strings `entry_is_vocabulary_term` refuses: the agent echoing its instructions.

    Canonical type names, lexicon names and layer names, case-folded. All three are shown to
    the agent (`lexicon_builder.md` §1.2) and none is a term — an entry equal to one is the
    prompt arriving back in the artefact, which is a failure mode worth a named refusal
    because the resulting rule would fire on the word `institutions`.
    """
    layers = {layer for family in layer_families().values() for layer in family}
    return frozenset(
        term.casefold() for term in
        (*canonical_types(), *lexicon_names(), *layer_families(), *layers))


def validate_lexicon(obj: dict, *, langs: list[str]) -> tuple[dict, list[dict]]:
    """`(lexicons, refused)` for one LexiconBuilder response. `lexicon_builder.md` §2.3.

    Ten refusals, each recorded as `{"file": "{lang}/{name}", "reason": …}` with the reason
    from `lexicon_refusal`. **No refusal carries the rejected term.** A rejected entry is a
    surface form of unknown provenance and putting it in a second file would double this
    artefact's exposure to buy a debugging convenience — CLAUDE.md's trade, refused.

    **Refused entries are dropped and the arm continues**, which is where this parts company
    with the other two validators. Two things still raise: a `lexicons` block that is not an
    object, because then there is no artefact, and a language outside `corpus_rule_langs`,
    because a term list under a language no rule file is loaded for is unreadable by
    construction. **A run in which every file came back empty does not stop the arm** — it is
    recorded in the manifest, and DESIGN §6.7.4's cause 1 is what reads it.

    `duplicate_entry` is case-folded, because `src/rules.py` matches case-folded: two casings
    of one term are one term, and keeping both would put a term in the file twice under a
    reader that cannot tell. The **first** casing survives, so the file's order is the agent's;
    a rule that kept the last would make the surviving casing depend on JSON key order.

    `langs` is required and is `rule_langs(corpus)`. Not defaulted to the `lang` axis: the axis
    has five values and a corpus loads one or two, so an axis-wide check would accept a Korean
    list on a Spanish corpus and the arm would write a directory nothing reads.
    """
    declared_langs = set(langs)
    if not declared_langs:
        raise ArtefactError(
            "no rule languages were given, so every lexicon would be refused for its "
            "language. `langs` is `rule_langs(corpus)` and a corpus that loads no rule file "
            "cannot run this arm (DESIGN §5.2)."
        )
    names = set(lexicon_names())
    bases = set(lexicon_bases())
    reserved = _vocabulary_terms()
    refused: list[dict] = []

    for key in obj:
        if key not in ("lexicons", "unresolved"):
            refused.append({"file": key, "reason": "unknown_field"})
    block = obj.get("lexicons")
    if not isinstance(block, dict) or not block:
        raise ArtefactError(
            f"the lexicon response carries no `lexicons` object (got "
            f"{type(block).__name__}). §2.1 makes it the artefact; without it there is "
            "nothing to write, which is `malformed` at the top level and stops the arm."
        )
    unknown_langs = sorted(k for k in block if k not in declared_langs)
    if unknown_langs:
        raise ArtefactError(
            f"the lexicon response names {unknown_langs} and this corpus loads rule files "
            f"for {sorted(declared_langs)} (config/naming.yaml corpus_rule_langs). A term "
            "list under a language no rule file is loaded for is unreadable by construction, "
            "so this stops the arm rather than being dropped (§2.3)."
        )

    kept: dict[str, dict[str, dict]] = {}
    for lang in block:
        per_lang = block[lang]
        if not isinstance(per_lang, dict):
            refused.append({"file": lang, "reason": "malformed"})
            continue
        for name in per_lang:
            where = f"{lang}/{name}"
            entry = per_lang[name]
            if name not in names:
                refused.append({"file": where, "reason": "undeclared_value"})
                continue
            if not isinstance(entry, dict):
                refused.append({"file": where, "reason": "malformed"})
                continue
            unknown = [k for k in entry if k not in ("basis", "entries")]
            if unknown:
                refused.append({"file": where, "reason": "unknown_field"})
                continue
            if "basis" not in entry or "entries" not in entry:
                refused.append({"file": where, "reason": "missing_field"})
                continue
            if entry["basis"] not in bases:
                refused.append({"file": where, "reason": "undeclared_value"})
                continue
            terms = entry["entries"]
            if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
                refused.append({"file": where, "reason": "malformed"})
                continue
            surviving: list[str] = []
            folded: set[str] = set()
            for term in terms:
                reason = _entry_refusal(term, folded, reserved)
                if reason is not None:
                    refused.append({"file": where, "reason": reason})
                    continue
                folded.add(term.casefold())
                surviving.append(term)
            if not surviving:
                refused.append({"file": where, "reason": "empty_lexicon"})
                continue
            kept.setdefault(lang, {})[name] = {"basis": entry["basis"],
                                              "entries": surviving}
    return kept, refused


def _entry_refusal(term: str, folded: set[str], reserved: frozenset[str]) -> str | None:
    """Why this term is refused, or `None`. The five per-entry rows of §2.3's table.

    Order matters at one point and nowhere else: `duplicate_entry` is checked **last**, so
    that a term refused for a newline is not also the term a later identical one duplicates.
    The others are independent, and each returns the first reason that applies because
    `by_refusal` counts reasons and a term with two problems is one refused term.

    The term is never returned, logged, or interpolated into anything. Only the reason.
    """
    if any(mark in term for mark in _NEWLINES):
        return "entry_contains_newline"
    if _COMMENT_MARK in term:
        return "entry_contains_comment_mark"
    stripped = term.strip()
    if len(stripped) < MIN_ENTRY_CHARS:
        return "entry_too_short"
    if stripped.casefold() in reserved:
        return "entry_is_vocabulary_term"
    if term.casefold() in folded:
        return "duplicate_entry"
    return None


def lexicon_manifest(kept: dict, refused: list[dict], *, corpus: str, porting: str,
                     unresolved: list[str]) -> dict:
    """`lexicon_manifest.json`. `lexicon_builder.md` §2.2.

    **Carries no entry text**, only per-file counts and bases — which is the whole reason
    `paths.armlexiconmanifest` is not deny-listed while `paths.armlexicon` is. A refusal
    records the file and the reason and not the rejected string.

    `refused` per file is the count of that file's refusals, and it is a count of *entries and
    file-level problems together*: a file refused for `empty_lexicon` after four entries were
    dropped reads five. Splitting the two would need a second key whose only reader would be
    someone reconstructing the first, and `counts.by_refusal` already separates them by reason.

    `unresolved` is a list of `{lang}/{name}` and the per-file flag is derived from it, so the
    two cannot disagree. The agent's list may name a file that was refused out of existence;
    the per-file flag then has nowhere to land and the top-level list still records what the
    agent said, which is the honest pair.
    """
    per_file_refusals: dict[str, int] = {}
    by_refusal: dict[str, int] = {}
    for entry in refused:
        per_file_refusals[entry["file"]] = per_file_refusals.get(entry["file"], 0) + 1
        by_refusal[entry["reason"]] = by_refusal.get(entry["reason"], 0) + 1
    files: dict[str, dict] = {}
    total_entries = 0
    for lang in sorted(kept):
        for name in sorted(kept[lang]):
            where = f"{lang}/{name}"
            entries = kept[lang][name]["entries"]
            total_entries += len(entries)
            files[where] = {
                "basis": kept[lang][name]["basis"],
                "entries": len(entries),
                "refused": per_file_refusals.get(where, 0),
                "unresolved": where in set(unresolved),
            }
    return {
        "corpus": corpus,
        "porting": porting,
        "files": files,
        "unresolved": list(unresolved),
        "refused": refused,
        "counts": {
            "files": len(files),
            "entries": total_entries,
            "refused": len(refused),
            "by_refusal": {reason: by_refusal[reason] for reason in sorted(by_refusal)},
        },
    }


# ─── where the three artefacts are written ──────────────────────────────────


def _arm_path(key: str, **components: str) -> Path:
    """One four-axis `paths` key, every component checked against its axis.

    The fifth site in the repository doing this check — after `orchestrate._arm_path`,
    `human_arm._arm_path`, `corpora.base.round_path` and `rules._lexicon_root` — and it is
    not the fourth for `rules._lexicon_root`'s stated reason: each raises the error type its
    own callers catch, and this module's callers catch `ArtefactError`. A results path names
    the cell of the experiment an artefact belongs to, so an unknown component would create a
    cell rather than fail (DESIGN §5.3, §5.5).

    `root` is taken from the components rather than as a keyword, so that one signature covers
    the three writers and the three path builders below.
    """
    root = components.pop("root", None)
    template = path_template(key)
    for field, value in components.items():
        if value not in axis(field):
            raise ArtefactError(
                f"{value!r} is not a {field} in config/naming.yaml (have: "
                f"{sorted(axis(field))}). paths.{key} names the cell this artefact belongs "
                "to, and an unknown component would create a cell rather than fail."
            )
    return Path(root or ROOT) / template.format(**components)


def profile_path(*, corpus: str, detector: str, supervision: str, porting: str,
                 root: Path | None = None) -> Path:
    """`paths.armprofile`. Four axes, **no `{iteration}`** — DESIGN §4, §6.7.1.

    The absence is the capability: `port-multi` is the arm in which an agent authors the
    auxiliary artefacts the loop *consumes but does not produce*, and a path with a round
    number in it would be an artefact the loop revises.
    """
    return _arm_path("armprofile", corpus=corpus, detector=detector,
                     supervision=supervision, porting=porting, root=root)


def mapping_path(*, corpus: str, detector: str, supervision: str, porting: str,
                 root: Path | None = None) -> Path:
    """`paths.armmapping`. `profile_path`'s shape and reasons, one artefact over."""
    return _arm_path("armmapping", corpus=corpus, detector=detector,
                     supervision=supervision, porting=porting, root=root)


def manifest_path(*, corpus: str, detector: str, supervision: str, porting: str,
                  root: Path | None = None) -> Path:
    """`paths.armlexiconmanifest`. No `{lang}`: one call writes every language (§1.3)."""
    return _arm_path("armlexiconmanifest", corpus=corpus, detector=detector,
                     supervision=supervision, porting=porting, root=root)


def write_profile(record: dict, *, corpus: str, detector: str, supervision: str,
                  porting: str, root: Path | None = None) -> Path:
    """Write `profile.json` and return its path. Refuses an existing file.

    **Refused rather than overwritten, and that is where "the loop does not produce this"
    stops being a claim about a driver.** The path has no `{iteration}`, so a second write to
    it is either a re-run of the authoring call or a round that decided to revise the
    artefact, and the two are indistinguishable afterwards. `freeze_window()` refuses a
    re-freeze on the same argument; this is that guard at the three files whose immutability is
    the rung's definition.
    """
    return _write_json(_refuse_existing(profile_path(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        root=root), "profile"), record)


def write_mapping(record: dict, *, corpus: str, detector: str, supervision: str,
                  porting: str, root: Path | None = None) -> Path:
    """Write `mapping.yaml` and return its path. Refuses an existing file.

    **The agent emitted JSON and this writes YAML**, which `mapper.md` §2.1 decides
    deliberately: `paths.armmapping` fixes the extension, and a mapping is one flat
    label→label table, so YAML from a model would bring anchors, tags, implicit typing and
    duplicate keys for no gain. Serialising a validated object is the narrower path.

    `default_flow_style=False`, `sort_keys=False` and `allow_unicode=True`: block style so the
    file is diffable, insertion order so the agent's order survives, and unescaped Unicode so
    a corpus label outside ASCII is readable rather than `\\uXXXX`.
    """
    import yaml

    path = _refuse_existing(mapping_path(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        root=root), "mapping")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(record, fh, default_flow_style=False, sort_keys=False,
                       allow_unicode=True)
    return path


def write_lexicons(kept: dict, manifest: dict, *, corpus: str, detector: str,
                   supervision: str, porting: str,
                   root: Path | None = None) -> tuple[Path, list[Path]]:
    """Write the term lists and the manifest. Returns `(manifest_path, [list paths])`.

    **The orchestrator serialises terms only, and this is the one artefact where "written
    through unchanged" does not apply** (`lexicon_builder.md` §2.2). The reason is specific:
    the `.txt` format has a prose channel the JSON schema does not — a `#` comment line is
    arbitrary text in the artefact the screener denies, and it is exactly the field §2.1
    refuses to give the agent. So the agent returns an object with no comment concept in it and
    this writes one term per line and nothing else. No header, no generated-by line, no count.

    The directory comes from `rules.arm_lexicon_root()`, which is the same function
    `run_sealed_eval._lexicon_root` reconstructs and the same one `load_rules(lexicons=…)`
    reads — so what is written here and what a rule resolves `es/institutions` against are one
    path by construction rather than by two templates agreeing.
    """
    from ..rules import RuleError, arm_lexicon_root

    try:
        collection = arm_lexicon_root(corpus=corpus, detector=detector,
                                      supervision=supervision, porting=porting, root=root)
    except RuleError as exc:
        raise ArtefactError(f"cannot locate the arm's lexicon collection: {exc}") from exc

    written: list[Path] = []
    for lang in sorted(kept):
        directory = collection / lang
        for name in sorted(kept[lang]):
            path = _refuse_existing(directory / f"{name}.txt", "lexicon")
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                for term in kept[lang][name]["entries"]:
                    fh.write(term + "\n")
            written.append(path)
    manifest_file = _write_json(_refuse_existing(manifest_path(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        root=root), "lexicon manifest"), manifest)
    return manifest_file, written


def _refuse_existing(path: Path, what: str) -> Path:
    """Return `path` if nothing is there; raise otherwise. See `write_profile`'s docstring.

    The message names the path and not its contents. A path under `results/` is a cell of the
    experiment and publishable; what is inside an agent-authored lexicon is not (CLAUDE.md,
    and `paths.armlexicon`'s deny classification).
    """
    if path.exists():
        raise ArtefactError(
            f"refusing to overwrite the {what} already at {path}. These three artefacts are "
            "written once per arm and read thereafter: `paths.armprofile`, `paths.armmapping` "
            "and `paths.armlexicon` carry no {iteration} component because DESIGN §4 defines "
            "this rung as the one where an agent authors what the loop consumes and does not "
            "produce. A second write is either a re-run or a round revising an input, and "
            "after the fact the two are the same bytes."
        )
    return path


#: `re` is imported for the lexicon name check `src/rules.py` already documents
#: (`[a-z0-9_]+`), and is not used elsewhere in this module. Kept as one compiled pattern
#: rather than inline, so the two places that constrain a lexicon file name are visibly the
#: same constraint.
_LEXICON_NAME = re.compile(r"^[a-z0-9_]+$")


def check_lexicon_names() -> None:
    """Assert every declared `lexicon_name` is a name `src/rules.py` can resolve.

    `_read_lexicon` matches `[a-z0-9_]+`, so a vocabulary value outside it would be a file
    this module writes and the loader refuses to read — the one failure that would appear
    only after the arm had spent its authoring call. Checked rather than trusted, because the
    vocabulary is a config block a person edits and the loader's pattern is in another module.
    """
    bad = sorted(name for name in lexicon_names() if not _LEXICON_NAME.match(name))
    if bad:
        raise ArtefactError(
            f"config/naming.yaml `lexicon_name` declares {bad}, which src/rules.py's "
            "`_read_lexicon` cannot resolve — it matches [a-z0-9_]+. A name written here and "
            "refused at load would cost the arm its authoring call before anything failed."
        )
