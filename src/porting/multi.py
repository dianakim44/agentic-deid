"""`port-multi`: three authoring calls outside the loop, then `port-loop`'s loop unchanged.

DESIGN §6.7.1. The rung adds one capability and it is stated as a *call order*: the Profiler,
the Mapper and the LexiconBuilder run **once each, before iteration 1**, and what they write is
what every later round reads. Rounds 1..N are `loop.run_iteration_1()` and `loop.run_iteration()`
with two arguments filled in — `lexicons` and `already_frozen` — and nothing else.

**What this module enforces, and why each guard is where it is.**

The claim "the three artefacts are inputs, not outputs" has to survive three different attacks,
and no single guard covers them:

1. *A round rewriting one.* Closed by the paths themselves: `paths.armprofile`, `armmapping`
   and `armlexicon` carry no `{iteration}` component, so there is no per-round file to write —
   and `artefacts._refuse_existing` turns a second write to the un-iterated path into a refusal
   rather than a silent overwrite.
2. *A module in the loop's import graph acquiring a writer.* Closed structurally, by a test
   rather than by a convention: nothing `loop.py` imports may reach `write_profile`,
   `write_mapping` or `write_lexicons`. The functions live here and here is not on that path.
3. *An artefact edited between rounds by something that is not a writer at all* — a hand fix
   after round 3's leak rate came in, the case a `_refuse_existing` cannot see because an edit
   is not an overwrite. Closed by `freeze_artefacts()` / `artefact_drift()`, and this is the
   guard that made a new path key necessary: **`tools/run_loop.py` runs one round per process**
   ("the chain is on disk, not in a process"), so an in-memory freeze held by a driver would
   attest to nothing beyond the round that took it.

The three failure semantics are `artefacts`'s and this module carries them out; the module
docstring there is the statement of them. In one line each: a refused **profile** stops the arm
because it configures loading; a refused **mapping** stops the arm on *every* corpus including
the ones where §9.0 already fixes the answer, because a rule that branches on corpus is the
shape CLAUDE.md rejects; a refused **lexicon** entry is dropped and counted and the arm
continues, because its degradation already lands in the headline leak rate.

**A stop is a `format_failure.json`, and the message is composed here.** DESIGN §10 A2 wants the
validator's error verbatim, and a refusal *list* has no exception to quote — so `_stop_message()`
builds one sentence from `counts.by_refusal`, which is reasons and counts and no surface form.
The raw response reaches that file, as it does for every arm; the artefact is written first and
`rules_path` points at it. That field name is `_write_failure`'s and it is not renamed for this
rung: its own schema gloss is "where the response was written before it was loaded", which is
exactly true of `profile.json`, and a per-caller field name would make the failing arm the one
whose record has a different shape.

**No arm-wide driver here, deliberately.** `port-loop` has none either — `tools/run_loop.py`
runs a round and exits, and the chain is the files. A `run_arm()` in this module would be the
only place in the repository where an eight-round arm was a single process, and the first thing
it would need is a resume path for the round it died on.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..llm.bedrock import invoke, model_lifecycle
from ..llm.prompt import (
    assemble_lexicon_prompt, assemble_mapper_prompt, assemble_profiler_prompt,
)
from ..orchestrate import (
    AUTHORING_ITERATION, CALLED, FORMAT_FAILURE, ONESHOT_SECTIONS, OUT_OF_LOOP_ROLES,
    _digest, _now, _write_failure, append_call, call_line, freeze_window, roles_called,
)
from ..corpora.base import rule_langs
from ..eval.run_fold import DEFAULT_SPLIT
from ..rules import arm_lexicon_root
from . import artefacts
from .artefacts import ArtefactError
from .loop import DETECTOR, SUPERVISION

#: The arm this module drives. `loop.PORTING` is `port-loop` and the loop functions take it as
#: an argument, which is what lets this rung reuse them — see `loop.run_iteration`'s docstring.
PORTING = "port-multi"

#: The three roles, in call order. `orchestrate.OUT_OF_LOOP_ROLES` is the *set* that
#: `check_agent_role` admits at `AUTHORING_ITERATION`; ordering is this module's, because the
#: order is a fact about the arm (the Mapper reads the profile, so it cannot go first) and not
#: about the vocabulary. Checked against that set below rather than trusted: a fourth
#: out-of-loop role added to `config/naming.yaml` and to `orchestrate` but not called here
#: would otherwise be a role the log admits and the arm never spends.
PROFILER = "profiler"
MAPPER = "mapper"
LEXICON_BUILDER = "lexicon_builder"
ROLE_ORDER: tuple[str, ...] = (PROFILER, MAPPER, LEXICON_BUILDER)

if set(ROLE_ORDER) != set(OUT_OF_LOOP_ROLES):        # pragma: no cover - import-time guard
    raise ImportError(
        f"src/porting/multi.py calls {sorted(ROLE_ORDER)} out of loop and "
        f"orchestrate.OUT_OF_LOOP_ROLES admits {sorted(OUT_OF_LOOP_ROLES)}. The two lists are "
        "one arm's structure written twice; a role admitted at AUTHORING_ITERATION that this "
        "module never calls is a call the log allows and the arm never makes."
    )

#: The freeze record's schema. Bumped when a field's meaning changes, per the convention
#: `orchestrate.FAILURE_SCHEMA` and `scorer.SCHEMA_VERSION` follow.
FREEZE_SCHEMA = 1

#: The three artefacts' keys in the freeze record, and the whole of what it covers. Names
#: rather than paths: the paths are four templates in `config/naming.yaml` and the axes are
#: already fields of the record, so writing them out again would be a second place a path
#: could be wrong.
PROFILE_KEY = "profile"
MAPPING_KEY = "mapping"
MANIFEST_KEY = "lexicon_manifest"


# ─── the three authoring calls ───────────────────────────────────────────────


def _call(role: str, prompt, *, corpus: str, detector: str, supervision: str, porting: str,
          model_id: str, client, control_client, max_tokens: int | None):
    """Probe, call once, log the line. Returns `(response, model, cost, lifecycle, reference)`.

    The order is `run_arm()`'s and each position is load-bearing there for reasons that hold
    here unchanged: the lifecycle probe goes **before** the call so that anything surprising it
    does happens while the arm can still be re-run, and the log line is appended **before** the
    response is judged, because the line is what fixes the window and at that moment the only
    thing known about the response is that it arrived.

    `role` and `AUTHORING_ITERATION` are passed together and `call_line` checks them against
    each other, which is what makes "out of loop" a property of the log rather than of this
    module (DESIGN §6.7.1). `sample_reference` is left at its null default: none of these three
    agents is shown a drawn sample, and that is the arm's structure rather than an unfilled
    argument — the same reading `auditor.md` §5's null carries.
    """
    reference = prompt.reference()
    lifecycle = model_lifecycle(model_id, client=control_client)
    kwargs = {} if max_tokens is None else {"max_tokens": max_tokens}
    response = invoke(prompt, model_id=model_id, client=client, **kwargs)
    cost = response.cost()
    model = response.model_record()
    append_call(
        call_line(AUTHORING_ITERATION, prompt_reference=reference, model=model,
                  response_chars=len(response.text),
                  response_sha256=_digest(response.text),
                  outcome=CALLED, cost=cost, model_lifecycle=lifecycle, role=role),
        corpus, detector, supervision, porting,
    )
    return response, model, cost, lifecycle, reference


def _stop_message(what: str, counts: dict, path: Path) -> str:
    """The `error` string for an artefact whose refusals stop the arm.

    **Reasons and counts, and no surface form.** CLAUDE.md's rule is unconditional and this
    string is the exact shape it is about: it lands in `format_failure.json`, which is
    committed, and in whatever terminal ran the round. A refused lexicon entry, a refused
    source label and a refused field value are all *values from the response*, so none of them
    is quoted here — the artefact beside this record carries the reason per item, and the item
    is named by its key (a field name, a source label, a `{lang}/{name}`), never by its value.

    Composed rather than quoted because there is no exception to quote (see the module
    docstring): §10 A2 asks for the validator's own message and the validator returned a list.
    """
    by_refusal = counts.get("by_refusal", {})
    breakdown = ", ".join(f"{reason} {n}" for reason, n in sorted(by_refusal.items()))
    return (
        f"the {what} carries {counts.get('refused', 0)} refusal(s) ({breakdown}), and this "
        f"artefact's refusals stop the arm. The per-item reasons are in {path.name} beside "
        "this record; they are not repeated here, and no rejected value is. A refusal is not "
        "repaired — DESIGN §10 A2 makes a malformed artefact a reportable outcome and not an "
        "accident, so the arm ends here rather than running on a partial input."
    )


def _failed(*, what: str, error: str, artefact: Path, written: bool, response, model, cost,
            lifecycle, reference: dict, corpus: str, detector: str, supervision: str,
            porting: str, split: str, role: str, counts: dict | None = None) -> dict:
    """Write `format_failure.json` and return the step result. See the module docstring.

    **`written=False` is the one case in this repository where `rules_path` names a path that
    does not exist**, and it is worth being explicit about rather than papering over. A response
    that is not a JSON object never reaches an artefact — unlike `run_arm`, which writes the raw
    text to `paths.armrules` and *then* fails to load it, so its `rules_path` always exists. The
    field is filled with the path the artefact would have occupied because the two alternatives
    are worse: a null would change `FAILURE_SCHEMA`'s shape so that the arms with the least
    interpretable records are the ones with a different one, and a second field name for this
    rung would make "where is the response" a question whose answer depends on which arm failed.
    The response itself is in this record's own `response` field, which is where §10 A2 puts it.
    The step result's `path` is `None` in this case, so a caller can tell the two apart without
    reading the file.
    """
    failure = _write_failure(
        corpus=corpus, detector=detector, supervision=supervision, porting=porting,
        split=split, model=model, response=response.text, error=error,
        rules_path=artefact, cost=cost, prompt_reference=reference,
        model_lifecycle=lifecycle,
    )
    return {
        "role": role,
        "outcome": FORMAT_FAILURE,
        "path": artefact if written else None,
        "failure_path": failure,
        "cost": cost,
        "counts": counts,
    }


def author_profile(*, corpus: str, model_id: str, detector: str = DETECTOR,
                   supervision: str = SUPERVISION, porting: str = PORTING,
                   split: str = DEFAULT_SPLIT, max_tokens: int | None = None,
                   client=None, control_client=None) -> dict:
    """Call 1 of 3: the Profiler writes `profile.json`. `docs/prompts/profiler.md`.

    **This is the call the window is frozen before**, which is the whole of why
    `loop.run_iteration_1()` needed an `already_frozen` flag. DESIGN §6.3's "freeze last" means
    immediately before the arm's *first* call, and in this rung that is this one and not the
    RuleAuthor's. `sections=ONESHOT_SECTIONS` because what the record's `sections_shown`
    describes is the loop's prompt — round 1 carries §§1.1–1.2 here exactly as in `port-oneshot`
    — while these three prompts' own blocks are attested by their reference forms instead.

    Returns the step result with the validated `profile` object under `profile`, which is the
    Mapper's input. **A refusal stops the arm**: this artefact fixes the encoding, the offset
    convention and the group key, so a profile short a field is a corpus that cannot be loaded,
    and a wrong field is a corpus loaded wrongly and scored anyway.
    """
    freeze_window(corpus, detector, supervision, porting, sections=ONESHOT_SECTIONS)

    raw = artefacts.read_inventory(corpus)
    inventory = artefacts.filter_inventory(raw)
    prompt = assemble_profiler_prompt(corpus=corpus, inventory=inventory)
    response, model, cost, lifecycle, reference = _call(
        PROFILER, prompt, corpus=corpus, detector=detector, supervision=supervision,
        porting=porting, model_id=model_id, client=client, control_client=control_client,
        max_tokens=max_tokens)

    would_be = artefacts.profile_path(corpus=corpus, detector=detector,
                                      supervision=supervision, porting=porting)
    common = dict(response=response, model=model, cost=cost, lifecycle=lifecycle,
                  reference=reference, corpus=corpus, detector=detector,
                  supervision=supervision, porting=porting, split=split, role=PROFILER)
    try:
        obj = artefacts.parse_object(response.text, what="profile")
        profile, refused = artefacts.validate_profile(obj, inventory=inventory)
    except ArtefactError as exc:
        return _failed(what="profile", error=str(exc), artefact=would_be, written=False,
                       **common)

    # The **filtered** inventory, which is what `inventory_filtered_sha256` has to be taken
    # over — the hash is what makes profiler.md §1.2's withholding claim checkable, and one
    # taken over `raw` would attest to the file rather than to what the prompt carried.
    record = artefacts.profile_record(profile, refused, corpus=corpus, porting=porting,
                                      inventory=inventory)
    path = artefacts.write_profile(record, corpus=corpus, detector=detector,
                                   supervision=supervision, porting=porting)
    if refused:
        # Written first, then the failure beside it. The artefact holds the reason per field
        # and the failure holds the response; neither is derivable from the other.
        return _failed(what="profile", error=_stop_message("profile", record["counts"], path),
                       artefact=path, written=True, counts=record["counts"], **common)
    return {
        "role": PROFILER,
        "outcome": CALLED,
        "path": path,
        "failure_path": None,
        "cost": cost,
        "counts": record["counts"],
        "profile": profile,
    }


def author_mapping(*, corpus: str, profile: dict, model_id: str, detector: str = DETECTOR,
                   supervision: str = SUPERVISION, porting: str = PORTING,
                   split: str = DEFAULT_SPLIT, max_tokens: int | None = None,
                   client=None, control_client=None) -> dict:
    """Call 2 of 3: the Mapper writes `mapping.yaml`. `docs/prompts/mapper.md`.

    `profile` is the **validated object** `author_profile()` returned, not the record around it
    and not the committed `profiles/{corpus}.json` — `mapping_record` hashes what it is given
    into `profile_sha256`, and that hash is what makes the join between a mapping and the type
    inventory it mapped unforgeable after the fact.

    **A refusal stops the arm on every corpus, including the two §9.0 already has a table for.**
    Where the design fixes the mapping, `applied` is `design` and the agent's answer is recorded
    and compared rather than used — and it stops the arm all the same. The alternative is a rule
    that branches on corpus, and CLAUDE.md rejects that shape for the reason it gives about
    surface forms in messages: a check whose strictness depends on which corpus is in hand is a
    check nobody can reason about, and the corpus where it is loose is the corpus with no table
    to catch the error.
    """
    type_inventory = profile[artefacts.PROFILE_LABEL_FIELD]
    prompt = assemble_mapper_prompt(corpus=corpus, profile=profile)
    response, model, cost, lifecycle, reference = _call(
        MAPPER, prompt, corpus=corpus, detector=detector, supervision=supervision,
        porting=porting, model_id=model_id, client=client, control_client=control_client,
        max_tokens=max_tokens)

    would_be = artefacts.mapping_path(corpus=corpus, detector=detector,
                                      supervision=supervision, porting=porting)
    common = dict(response=response, model=model, cost=cost, lifecycle=lifecycle,
                  reference=reference, corpus=corpus, detector=detector,
                  supervision=supervision, porting=porting, split=split, role=MAPPER)
    try:
        obj = artefacts.parse_object(response.text, what="mapping")
        kept_map, kept_excluded, refused = artefacts.validate_mapping(
            obj, type_inventory=type_inventory)
    except ArtefactError as exc:
        return _failed(what="mapping", error=str(exc), artefact=would_be, written=False,
                       **common)

    unresolved = artefacts.kept_unresolved(obj, set(kept_map) | set(kept_excluded))
    record = artefacts.mapping_record(kept_map, kept_excluded, refused, corpus=corpus,
                                     porting=porting, profile=profile,
                                     type_inventory=type_inventory, unresolved=unresolved)
    path = artefacts.write_mapping(record, corpus=corpus, detector=detector,
                                   supervision=supervision, porting=porting)
    if refused:
        return _failed(what="mapping", error=_stop_message("mapping", record["counts"], path),
                       artefact=path, written=True, counts=record["counts"], **common)
    return {
        "role": MAPPER,
        "outcome": CALLED,
        "path": path,
        "failure_path": None,
        "cost": cost,
        "counts": record["counts"],
        # Not a stop. `disagreements` is what §4.2 pre-registers as a *measurement* — how far an
        # agent's mapping departs from the one §9.0 fixed — and a measurement that halted the
        # arm would be a measurement whose value was always zero on the arms that finished.
        "disagreements": record["disagreements"],
        "applied": record["applied"],
    }


def author_lexicons(*, corpus: str, model_id: str, detector: str = DETECTOR,
                    supervision: str = SUPERVISION, porting: str = PORTING,
                    split: str = DEFAULT_SPLIT, max_tokens: int | None = None,
                    client=None, control_client=None) -> dict:
    """Call 3 of 3: the LexiconBuilder writes `lexicons/{lang}/*.txt` and the manifest.

    `docs/prompts/lexicon_builder.md`. **The only one of the three with a causal path into
    detection**, and the only one whose refusals do not stop the arm: a rejected entry is
    dropped and counted, and the round runs on the terms that survived. That asymmetry is not
    leniency. A thinner gazetteer is a *weaker detector*, and a weaker detector's cost is
    already the headline number this experiment reports — it shows up in the leak rate, on the
    arm that spent the call. A profile's error does not show up anywhere, which is why that one
    stops.

    The extreme case is deliberate and not special-cased: if every file is refused, the
    collection is empty, the arm continues, and a `lexicon:` rule the RuleAuthor then writes
    fails at load in round 1 as a format failure. That is the honest consequence — an empty
    collection is a real state of this arm — and a guard here that stopped the arm instead
    would be this function deciding that no lexicon is worse than a bad one on the agent's
    behalf, which is the judgement the leak rate is there to make.
    """
    langs = list(rule_langs(corpus))
    artefacts.check_lexicon_names()
    prompt = assemble_lexicon_prompt(corpus=corpus, langs=langs)
    response, model, cost, lifecycle, reference = _call(
        LEXICON_BUILDER, prompt, corpus=corpus, detector=detector, supervision=supervision,
        porting=porting, model_id=model_id, client=client, control_client=control_client,
        max_tokens=max_tokens)

    would_be = artefacts.manifest_path(corpus=corpus, detector=detector,
                                       supervision=supervision, porting=porting)
    common = dict(response=response, model=model, cost=cost, lifecycle=lifecycle,
                  reference=reference, corpus=corpus, detector=detector,
                  supervision=supervision, porting=porting, split=split,
                  role=LEXICON_BUILDER)
    try:
        obj = artefacts.parse_object(response.text, what="lexicon")
        kept, refused = artefacts.validate_lexicon(obj, langs=langs)
    except ArtefactError as exc:
        # The only stop this agent has, and it is not a refusal: `validate_lexicon` raises when
        # the response carries no `lexicons` object at all, which leaves nothing to write. Entry
        # and file refusals below are dropped and counted, and the arm continues on what
        # survived — see this function's docstring for why the two are not the same case.
        return _failed(what="lexicon collection", error=str(exc), artefact=would_be,
                       written=False, **common)

    admissible = {f"{lang}/{name}" for lang in kept for name in kept[lang]}
    unresolved = artefacts.kept_unresolved(obj, admissible)
    manifest = artefacts.lexicon_manifest(kept, refused, corpus=corpus, porting=porting,
                                         unresolved=unresolved)
    manifest_file, written = artefacts.write_lexicons(
        kept, manifest, corpus=corpus, detector=detector, supervision=supervision,
        porting=porting)
    return {
        "role": LEXICON_BUILDER,
        "outcome": CALLED,
        "path": manifest_file,
        "failure_path": None,
        "cost": cost,
        "counts": manifest["counts"],
        "lexicon_paths": written,
        "lexicons": lexicon_collection(corpus=corpus, detector=detector,
                                       supervision=supervision, porting=porting),
    }


# ─── the freeze: what the rounds may read and may not change ─────────────────


def lexicon_collection(*, corpus: str, detector: str, supervision: str,
                       porting: str = PORTING, root: Path | None = None) -> Path:
    """The collection root a round passes as `loop.run_iteration(lexicons=…)`.

    `rules.arm_lexicon_root()` and nothing else, re-exported here so that the round driver has
    one import for the rung. That function is also what `artefacts.write_lexicons()` writes
    into and what `run_sealed_eval._lexicon_root` reconstructs, so what was written and what a
    rule resolves `es/institutions` against are one path by construction.
    """
    return arm_lexicon_root(corpus=corpus, detector=detector, supervision=supervision,
                            porting=porting, root=root)


def freeze_path(corpus: str, detector: str, supervision: str,
                porting: str = PORTING, *, root: Path | None = None) -> Path:
    """`paths.armartefactfreeze` for this arm."""
    return artefacts._arm_path("armartefactfreeze", corpus=corpus, detector=detector,
                               supervision=supervision, porting=porting, root=root)


def _file_digest(path: Path) -> str:
    """sha256 of a file's bytes as text. Same digest `orchestrate.window_hashes()` takes."""
    with open(path, encoding="utf-8") as fh:
        return _digest(fh.read())


def artefact_hashes(*, corpus: str, detector: str, supervision: str, porting: str = PORTING,
                    root: Path | None = None) -> dict:
    """`{files, lexicons, lexicon_collection_sha256}` as the three artefacts are right now.

    `files` is the three single files by key (`PROFILE_KEY`, `MAPPING_KEY`, `MANIFEST_KEY`);
    `lexicons` is `{lang}/{name}.txt` → sha256 for every term list in the collection. A file
    that is absent is absent from the mapping rather than nulled, so a freeze taken before the
    LexiconBuilder ran and a freeze taken after it wrote nothing are two different records —
    `artefact_drift()` reports an added or removed key, which is the case a null would hide.

    `lexicon_collection_sha256` is the digest of the canonical JSON of `lexicons`, so a round
    that added a term list has one field to compare rather than a set difference to compute.
    Names and hashes only: no entry text, which is what keeps this record outside the deny
    classification `paths.armlexicon` carries.
    """
    kwargs = dict(corpus=corpus, detector=detector, supervision=supervision, porting=porting,
                  root=root)
    files: dict[str, str] = {}
    for key, path in (
            (PROFILE_KEY, artefacts.profile_path(**kwargs)),
            (MAPPING_KEY, artefacts.mapping_path(**kwargs)),
            (MANIFEST_KEY, artefacts.manifest_path(**kwargs)),
    ):
        if path.exists():
            files[key] = _file_digest(path)

    collection = lexicon_collection(**kwargs)
    lexicons: dict[str, str] = {}
    if collection.is_dir():
        for path in sorted(collection.glob("*/*.txt")):
            lexicons[f"{path.parent.name}/{path.name}"] = _file_digest(path)
    return {
        "files": files,
        "lexicons": lexicons,
        "lexicon_collection_sha256": _digest(artefacts._canonical_json(lexicons)),
    }


def freeze_artefacts(*, corpus: str, detector: str, supervision: str, porting: str = PORTING,
                     root: Path | None = None) -> dict:
    """Hash the three artefacts once, after authoring and before round 1. Refuses a re-freeze.

    Returns the record it wrote. **Refused rather than overwritten, which is the opposite of
    `orchestrate.freeze_window()`'s choice, and the difference follows from what each record
    attests.** A window freeze describes files that exist independently of the arm and may
    legitimately be re-taken up to the first call, so it counts `revision`s. This one describes
    files *this arm just wrote and will not write again*; there is no state in which re-taking
    it is right, and the one reason to want to is the reason it must not be possible — an
    artefact edited between rounds, re-frozen, and thereby made to have always looked that way.

    All three must be present. A freeze covering two of them would pass on an arm whose
    LexiconBuilder was never called, and the rounds would then run with `lexicons=` pointing at
    a directory nothing attests to.
    """
    kwargs = dict(corpus=corpus, detector=detector, supervision=supervision, porting=porting,
                  root=root)
    path = freeze_path(corpus, detector, supervision, porting, root=root)
    if path.exists():
        raise ArtefactError(
            f"refusing to re-freeze the artefacts already attested at {path}. This record says "
            "the three inputs had these hashes when round 1 started; a second one would say "
            "they had today's, and an artefact edited mid-arm would be attested as though it "
            "had always been that way. Nothing legitimate needs this — the artefacts are "
            "written once per arm (artefacts._refuse_existing) and read thereafter. If the arm "
            "is genuinely being started over, the arm's results directory is what to remove, "
            "not this file."
        )

    hashes = artefact_hashes(**kwargs)
    missing = [key for key in (PROFILE_KEY, MAPPING_KEY, MANIFEST_KEY)
               if key not in hashes["files"]]
    if missing:
        raise ArtefactError(
            f"cannot freeze the artefacts: {missing} not written yet. The freeze covers all "
            "three or none — one taken after two authoring calls would let round 1 run with a "
            "`lexicons` collection no record attests to, which is the state this file exists "
            "to make impossible."
        )
    record = {
        "schema_version": FREEZE_SCHEMA,
        "corpus": corpus,
        "detector": detector,
        "supervision": supervision,
        "porting": porting,
        # An instant, not a date, for `freeze_window`'s reason: this record and the round-1
        # window freeze are ordered by this field and by nothing else.
        "generated": _now(),
        **hashes,
        "counts": {"lexicon_files": len(hashes["lexicons"])},
    }
    artefacts._write_json(path, record)
    return record


def read_profile(*, corpus: str, detector: str, supervision: str, porting: str = PORTING,
                 root: Path | None = None) -> dict:
    """The validated profile object out of this arm's written `profile.json`.

    **The `profile` block and not the record around it**, which is what `author_mapping()` takes
    and what `mapping_record`'s `profile_sha256` is a hash of. The record also carries `refused`
    and `counts`, which are the orchestrator's; hashing those would make one profile produce two
    mapping hashes if a count changed.

    Exists so the Mapper can be re-run in a separate process after the Profiler has succeeded —
    the resume path a three-call sequence needs, and the reason the authoring driver has a
    `--step` flag at all. It reads what was written rather than re-deriving it: a second
    validation pass here could accept something the first refused and nothing would say so.
    """
    path = profile_path_for(corpus=corpus, detector=detector, supervision=supervision,
                           porting=porting, root=root)
    if not path.exists():
        raise ArtefactError(
            f"no profile at {path}. The Mapper's §1.1 input is the Profiler's type inventory, "
            "so the first authoring call comes first — this is the call order the rung is "
            "(DESIGN §6.7.1) and not an ordering this driver chose."
        )
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    profile = record.get("profile")
    if not isinstance(profile, dict) or not profile:
        raise ArtefactError(
            f"{path.name} carries no `profile` object (found a {type(profile).__name__}). It is "
            "written by `artefacts.profile_record()` and nothing else writes that path, so this "
            "is a truncated or hand-edited file rather than a shape to tolerate."
        )
    return profile


def profile_path_for(*, corpus: str, detector: str, supervision: str, porting: str = PORTING,
                     root: Path | None = None) -> Path:
    """`artefacts.profile_path()` with this rung's `porting` default. See `read_profile()`."""
    return artefacts.profile_path(corpus=corpus, detector=detector, supervision=supervision,
                                 porting=porting, root=root)


def check_ready_for_round(iteration: int, *, corpus: str, detector: str, supervision: str,
                          porting: str = PORTING, root: Path | None = None) -> str | None:
    """`None` when round `iteration` may run on this arm; a problem string when it may not.

    The one question the round driver asks this module, and it has three parts:

    1. **Each of the three roles called exactly once.** Read from the call log by
       `orchestrate.roles_called()`, not from the artefacts on disk — an artefact is what a call
       *produced*, and a hand-written `mapping.yaml` would satisfy a check that looked at files.
       Exactly once and not at least once: `AUTHORING_ITERATION`'s guarantee is one per arm, and
       a second Profiler call means two profiles were authored and one of them is what the rounds
       have been reading.
    2. **The freeze exists**, so the inputs are attested (`freeze_artefacts()`).
    3. **Nothing has drifted** since it. This is the part no path rule and no `_refuse_existing`
       can cover, because an edit is not an overwrite — see the module docstring.

    A string rather than an exception, matching `tools/run_arm.py`'s `_check_axes` and the rest
    of that driver's pre-flight: these are all "do not spend the money" answers, they are printed
    together before anything is frozen, and a traceback is the wrong shape for a checklist.

    Checked for **every** round including round 1, where the drift half is vacuous by
    construction — the freeze is taken moments before it. The branch that skipped it would be
    the branch that got the round numbering wrong.
    """
    cell = f"{corpus}/{detector}/{supervision}/{porting}"
    counts = roles_called(corpus, detector, supervision, porting)
    wrong = {role: counts.get(role, 0) for role in ROLE_ORDER if counts.get(role, 0) != 1}
    if wrong:
        return (
            f"{cell}: round {iteration} cannot run — the three out-of-loop calls are not one "
            f"each ({wrong}, expected 1). DESIGN §6.7.1 makes this rung a call *order*: the "
            "Profiler, Mapper and LexiconBuilder run once each before round 1 and the loop "
            "reads what they wrote. A missing role is a round with an unauthored input; a "
            "doubled one means two artefacts were authored and the rounds read whichever won."
        )
    path = freeze_path(corpus, detector, supervision, porting, root=root)
    if not path.exists():
        return (
            f"{cell}: round {iteration} cannot run — the three artefacts are written but not "
            f"frozen ({path.name} is absent). The freeze is taken once, after the third "
            "authoring call and before round 1, and it is what makes 'the loop did not change "
            "its inputs' checkable across rounds that run in separate processes."
        )
    drift = artefact_drift(corpus=corpus, detector=detector, supervision=supervision,
                           porting=porting, root=root)
    if drift:
        return (
            f"{cell}: round {iteration} cannot run — {len(drift)} of the frozen artefacts have "
            f"moved since round 1 ({', '.join(drift)}). These three files are the rung's "
            "*inputs* (DESIGN §4) and the arm's earlier rounds ran against the frozen bytes. "
            "Restoring them is not a fix either: rounds 1..n were scored against one input and "
            "would be reported beside rounds n+1.. scored against another. The arm restarts, "
            "with a written reason."
        )
    return None


def artefact_drift(*, corpus: str, detector: str, supervision: str, porting: str = PORTING,
                   root: Path | None = None) -> list[str]:
    """The frozen artefact keys whose bytes have moved since the freeze. `[]` when none.

    Keys are `files.{key}` and `lexicons.{lang}/{name}.txt`, and a key present in one of the two
    states and not the other is reported too — an added term list and a deleted one are both
    drift, and a comparison over the intersection would call neither.

    Shaped after `orchestrate.window_drift()`: it returns the fields rather than raising, so the
    caller decides what a drift means. Here the caller is the round driver and the answer is
    always "stop" — but the round driver is also what writes the round's record, and a function
    that raised would take that decision out of the place that has to report it.
    """
    path = freeze_path(corpus, detector, supervision, porting, root=root)
    if not path.exists():
        raise ArtefactError(
            f"no artefact freeze at {path}, so there is nothing to compare the three inputs "
            "against. In `port-multi` the freeze is taken after the third authoring call and "
            "before round 1 (freeze_artefacts()); a round running without it is a round whose "
            "inputs are unattested. Do NOT freeze now to make this error go away: it would "
            "hash the artefacts as they are today and claim they were round 1's."
        )
    with open(path, encoding="utf-8") as fh:
        frozen = json.load(fh)
    now = artefact_hashes(corpus=corpus, detector=detector, supervision=supervision,
                          porting=porting, root=root)
    drift: list[str] = []
    for block, prefix in (("files", "files."), ("lexicons", "lexicons.")):
        was, is_now = frozen.get(block, {}), now[block]
        for key in sorted(set(was) | set(is_now)):
            if was.get(key) != is_now.get(key):
                drift.append(prefix + key)
    return drift
