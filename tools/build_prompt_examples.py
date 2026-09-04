#!/usr/bin/env python3
"""Write `docs/prompts/examples/` — the instances the prompts no longer contain.

    python3 tools/build_prompt_examples.py            # write
    python3 tools/build_prompt_examples.py --check    # exit 1 if the files are stale

**These files are for people and they are not sent to any agent.** The three
`port-multi` prompts used to quote a schema example inside the template, the template
is sent to the model verbatim, and the first `port-multi` run on `es-meddocan` failed
because the demonstration was copied — the second such failure in this repository
(`docs/notes/arm-port-multi-es.md`). So the examples moved out of the prompts, and the
prompts describe their schemas in prose and name this directory rather than showing
anything. `profiler.md` §2.4 carries the full argument; the short form is that a
demonstration reaching the model reproduces the failure whatever delimiter it wears.

Two consequences this file exists to make true rather than to assert:

- **They are generated from the validators**, not typed out, so an example cannot
  drift from the schema it illustrates. Each object is passed through
  `validate_profile` / `validate_mapping` / `validate_lexicon` here and the build
  fails on any refusal, and each record through the real `*_record` builder.
- **They are not in `WINDOW_FILES`.** The window is the record of what decided a run
  (DESIGN §5.5, §6.3) and a file no call could read is not in that definition. This
  script is not in the window either, for the same reason.

`--check` is what `tests/test_prompt.py` runs, so a schema change that leaves the
examples behind is a red test rather than a stale file nobody reads.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.porting.artefacts import (  # noqa: E402
    filter_inventory, lexicon_manifest, mapping_record, profile_record, read_inventory,
    validate_lexicon, validate_mapping, validate_profile,
)
from src.porting.audit import MaskedLine, parse_response, report  # noqa: E402
from src.rules import load_rules  # noqa: E402

#: Where the examples live. Deliberately *not* `docs/prompts/*.md`: these are `.json` so that
#: they parse rather than illustrate, and a markdown file here would be one more document under
#: `docs/prompts/` that a reader has to decide is not a template.
EXAMPLES = ROOT / "docs" / "prompts" / "examples"

#: The corpus the examples are built for. One corpus rather than a synthetic one, because a
#: fixture that no validator accepts is a fixture that cannot go stale visibly: `cites` paths
#: have to resolve in a real filtered inventory and `type_inventory` labels have to be real
#: labels, so an invented corpus would only exercise the shape checks.
CORPUS = "es-meddocan"

#: The arm the records name. `port-multi` is the arm these three agents belong to; the value
#: appears in the record's own `porting` field and nowhere else.
PORTING = "port-multi"

#: The rule file's language. Separate from `CORPUS` because a rule file's language is not a
#: corpus's — `rule_author.md`'s opening says so, and `es-carmen` loading both `es` and `cat`
#: is why the two cannot be one value here either.
LANG = "es"

#: The one example that is not JSON, because the artefact it illustrates is not. Kept out of
#: `build()`'s mapping for that reason: everything there goes through `json.dumps`, and routing
#: a YAML file through a JSON renderer to satisfy a type would produce a fixture of the wrong
#: format. `main()` writes and checks it beside the six, and `_validate_rules()` loads it.
RULES_FILE = "rule_author_rules_es.yaml"


def _profile(labels: list[str]) -> dict:
    """The Profiler's object: `profiler.md` §2.1's thirteen keys.

    `group_key` is `filename` and not `document_id_stem`, which is the value the first
    `port-multi` call chose. DESIGN §9.5 step 2 rejected stem grouping on this corpus — 48
    stems hold more than one document and none passed — so `splits/es-meddocan.json` records
    `unit: document`. The example carries the answer the frozen split records, because an
    example that a reader compares against the split should agree with it.

    Every field is cited. `cites` need not be exhaustive (§2.1), and here it is: an example of
    a schema whose one verifiable field is `cites` is more useful with the field exercised.
    """
    return {
        "annotation_encoding": "brat_standoff",
        "text_location": "separate_file",
        "offset_unit": "character",
        "offset_base": "zero",
        "offset_end": "exclusive",
        "newline": "lf_only",
        "bom": "counted_as_one_character",
        "type_system_level": "flat",
        "type_inventory": labels,
        "group_key": "filename",
        "patient_key_available": False,
        "cites": {
            "annotation_encoding": "pairing.brat",
            "text_location": "pairing.brat",
            "offset_unit": "offsets.unit",
            "offset_base": "offsets.base",
            "offset_end": "offsets.end",
            "newline": "encoding.conclusion",
            "bom": "encoding.bom_offset_interaction.finding",
            "type_system_level": "annotation_format.type_system_depth",
            "type_inventory": "phi_types_brat_flat",
            "group_key": "identifiers.unique_unit",
            "patient_key_available": "identifiers.patient_identifier_present",
        },
        "unresolved": [],
    }


def _mapping() -> dict:
    """The Mapper's object: `mapper.md` §2.1's three keys, over all 22 brat labels.

    The pairings are DESIGN §9.0's, which is the table the Mapper is *not* shown (§1.3). That
    is safe here and would not be safe in a prompt: nothing sends this file, and
    `_check_design_withheld()` in `src/llm/prompt.py` refuses an assembled Mapper prompt that
    shows a source label beside its §9.0 target — so an attempt to inline this example back
    into the template fails that check rather than silently teaching the answer.

    Each `basis` is exercised at least once, because `basis_mismatch` is the pairing check and
    an example that used one basis everywhere would leave four of the five unillustrated.
    """
    family, gloss = "source_label_family", "canonical_gloss"
    return {
        "map": {
            "NOMBRE_SUJETO_ASISTENCIA": {"canonical": "NAME", "basis": family},
            "NOMBRE_PERSONAL_SANITARIO": {"canonical": "NAME", "basis": family},
            "FECHAS": {"canonical": "DATE", "basis": gloss},
            "EDAD_SUJETO_ASISTENCIA": {"canonical": "AGE", "basis": gloss},
            "TERRITORIO": {"canonical": "LOCATION_AREA", "basis": "source_type_is_coarser"},
            "PAIS": {"canonical": "LOCATION_AREA", "basis": gloss},
            "CALLE": {"canonical": "LOCATION_STREET", "basis": gloss},
            "HOSPITAL": {"canonical": "ORGANISATION", "basis": gloss},
            "INSTITUCION": {"canonical": "ORGANISATION", "basis": gloss},
            "CENTRO_SALUD": {"canonical": "ORGANISATION", "basis": gloss},
            "CORREO_ELECTRONICO": {"canonical": "CONTACT", "basis": gloss},
            "NUMERO_TELEFONO": {"canonical": "CONTACT", "basis": family},
            "NUMERO_FAX": {"canonical": "CONTACT", "basis": family},
            "ID_SUJETO_ASISTENCIA": {"canonical": "ID", "basis": family},
            "ID_ASEGURAMIENTO": {"canonical": "ID", "basis": family},
            "ID_TITULACION_PERSONAL_SANITARIO": {"canonical": "ID", "basis": family},
            "ID_CONTACTO_ASISTENCIAL": {"canonical": "ID", "basis": family},
            "ID_EMPLEO_PERSONAL_SANITARIO": {"canonical": "ID", "basis": family},
            "PROFESION": {"canonical": "PROFESSION", "basis": gloss},
            "OTROS_SUJETO_ASISTENCIA": {"canonical": "OTHER", "basis": "residual_bucket"},
        },
        "excluded": {
            "SEXO_SUJETO_ASISTENCIA": {"excluded_type": "SEXO", "basis": "design_exclusion"},
            "FAMILIARES_SUJETO_ASISTENCIA": {
                "excluded_type": "FAMILIARES", "basis": "design_exclusion"},
        },
        "unresolved": ["CENTRO_SALUD"],
    }


def _lexicon() -> dict:
    """The LexiconBuilder's object: `lexicon_builder.md` §2.1's two keys, three levels deep.

    **The entries are real terms and that is the point of the file.** The removed example used
    `LEXICON_ENTRY_A`-style placeholders with a sentence saying they were not content; a model
    that copied it would have written placeholders into a gazetteer, which loads and matches
    nothing. Real terms make the file a fixture and make the failure mode obvious.

    Each of the three names and each of the three bases appears once, and `unresolved` is
    non-empty, so the `{lang}/{name}` slash form is exercised rather than described — that
    convention was the one thing the removed example carried and prose did not.
    """
    return {
        "lexicons": {
            "es": {
                "institutions": {
                    "basis": "general_knowledge_named_entities",
                    "entries": ["Hospital Clínic",
                                "Instituto Nacional de la Seguridad Social",
                                "Centro de Salud"],
                },
                "regions": {
                    "basis": "administrative_enumeration",
                    "entries": ["Andalucía", "Cataluña", "Comunidad de Madrid"],
                },
                "departments": {
                    "basis": "morphological_class",
                    "entries": ["Servicio de Cardiología", "Servicio de Urología"],
                },
            }
        },
        "unresolved": ["es/institutions"],
    }



#: The RuleAuthor's artefact, as YAML because that is what it is. **This is the sharpest of the
#: five examples to remove from its template**, and `profiler.md` §2.4 gives the reason: in YAML
#: indentation *is* the format, so an indented example is more copyable than a fenced one rather
#: than less, and there is no delimiter that makes a demonstration of this artefact safe inside
#: a prompt. That removal is not in this commit; the fixture it needs is.
#:
#: Written as a literal string rather than dumped from an object, and that is deliberate: the
#: point of this fixture is the *file*, comments and layout included, and `yaml.dump` would
#: produce a normalised form that no agent emits and that answers a different question. It is
#: validated by the real `load_rules`, so its being a literal buys no exemption from the schema.
RULES_YAML = """\
# An example rules/es.yaml. Generated by tools/build_prompt_examples.py.
# Nothing sends this file to any agent — see docs/prompts/examples/README.md.
version: 3
lang: es
rules:
  - rule_id: doctor_prefix
    layer: context_cue
    phi_type: NAME
    cue: ["Dr.", "Dra."]
    then: capitalised_words
    score: 0.8
    comment: >
      Title-prefixed clinician name. The cue is the title, not the name.
  - rule_id: patient_label
    layer: context_cue
    phi_type: NAME
    cue: ["Paciente:", "Nombre:"]
    then: capitalised_words
    score: 0.9
  - rule_id: dni_checksum
    layer: regex_checksum
    phi_type: ID
    pattern: '\\b\\d{8}[A-Z]\\b'
    checksum: dni_mod23
    flags: [unicode]
  - rule_id: hospital_gaz
    layer: gazetteer
    phi_type: ORGANISATION
    lexicon: es/institutions
    score: 0.7
"""


def _audit() -> tuple[dict, dict]:
    """The Auditor's object and the orchestrator's report, over an invented geometry.

    **The document is invented and there is no document.** Only a geometry is needed —
    `validate_flags` translates columns through `MaskedLine`s and never sees text — so this
    builds the geometry directly and no corpus is read, which also means the example carries no
    position from any real fold. `auditor.md` §2.2 deny-lists the real file for being a map of
    the identifiers a round did not catch, and an example of that file has to be one that maps
    nothing.

    `line` is **0-based**, which is the fact this example exists to pin down. `auditor.md`'s own
    fenced example shows `"line": 3` beside three displayed lines and so establishes no base at
    all, and the prompt states none either way. What that omission costs is measured in the
    commit that removes the example.
    """
    lines = (
        MaskedLine(length=50, doc_offset=0, tags=((34, 6, 34, 53),)),
        MaskedLine(length=39, doc_offset=64, tags=()),
        MaskedLine(length=47, doc_offset=104, tags=((10, 6, 114, 124),)),
    )
    agent_object = {
        "flags": [
            {"line": 2, "start": 18, "end": 34, "phi_type": "NAME", "score": 0.8},
            {"line": 2, "start": 0, "end": 8, "phi_type": "PROFESSION", "score": 0.4},
            {"line": 1, "start": 11, "end": 27, "phi_type": "LOCATION_STREET", "score": 0.7},
        ]
    }
    audit = parse_response(json.dumps(agent_object), doc_id="doc-example-0001", lines=lines)
    _refuse(list(audit.refused), "audit")
    record = report([audit], corpus=CORPUS, iteration=4, masked_from_iteration=3)
    return agent_object, record


def _validate_rules() -> None:
    """Load `RULES_YAML` through the real `load_rules`, from a temporary path.

    Temporary and not `rules/es.yaml`: that file is the committed bootstrap every arm's round 1
    reads as its §1.2 block, and a build script that wrote to it would change what an arm is
    shown in order to check an example.

    An empty rule list is refused separately from a load error. `load_rules` accepting a file
    whose `rules` block went missing would mean the schema still exists and the fixture
    illustrates nothing — which is the failure this whole directory is generated to prevent, in
    the one file that is a literal rather than a dumped object.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / f"{LANG}.yaml"
        path.write_text(RULES_YAML, encoding="utf-8")
        # A `lexicon:` rule needs the collection it names, and `load_rules` has no default for
        # it on purpose (`_read_lexicon`: defaulting would make an arm's number a claim about
        # whichever author's lists were on disk). So the example's list is built here, from the
        # same terms `lexicon_builder_agent_object.json` carries — one source for the fixture
        # pair, rather than two that can drift apart.
        lexicons = Path(raw) / "lexicons"
        (lexicons / LANG).mkdir(parents=True)
        entries = _lexicon()["lexicons"][LANG]["institutions"]["entries"]
        (lexicons / LANG / "institutions.txt").write_text(
            "\n".join(entries) + "\n", encoding="utf-8")
        ruleset = load_rules(LANG, path=path, lexicons=lexicons)
    rules = list(getattr(ruleset, "rules", ()))
    if not rules:
        raise SystemExit(
            f"{RULES_FILE} loaded with no rules. `load_rules` accepted the file, so the schema "
            "still exists; an empty rule list means the literal lost its `rules` block and the "
            "fixture illustrates nothing."
        )


def build() -> dict[str, dict]:
    """The six examples, keyed by filename. Every one validated on the way through.

    A refusal raises rather than being written: an example the validator refuses would
    document a schema that does not exist, which is worse than no example — a reader would
    trust it and a fixture would enshrine it.
    """
    inventory = filter_inventory(read_inventory(CORPUS))
    labels = list(inventory["phi_types_brat_flat"])

    profile_obj = _profile(labels)
    kept_profile, refused_profile = validate_profile(profile_obj, inventory=inventory)
    _refuse(refused_profile, "profile")

    mapping_obj = _mapping()
    kept_map, kept_excluded, refused_mapping = validate_mapping(
        mapping_obj, type_inventory=labels)
    _refuse(refused_mapping, "mapping")

    lexicon_obj = _lexicon()
    kept_lexicon, refused_lexicon = validate_lexicon(lexicon_obj, langs=["es"])
    _refuse(refused_lexicon, "lexicon")

    agent_flags, audit_record = _audit()
    _validate_rules()

    return {
        "profiler_agent_object.json": profile_obj,
        "profiler_profile_json.json": profile_record(
            kept_profile, refused_profile, corpus=CORPUS, porting=PORTING,
            inventory=inventory),
        "mapper_agent_object.json": mapping_obj,
        "mapper_mapping_yaml.json": mapping_record(
            kept_map, kept_excluded, refused_mapping, corpus=CORPUS, porting=PORTING,
            profile=kept_profile, type_inventory=labels,
            unresolved=mapping_obj["unresolved"]),
        "lexicon_builder_agent_object.json": lexicon_obj,
        "lexicon_builder_manifest_json.json": lexicon_manifest(
            kept_lexicon, refused_lexicon, corpus=CORPUS, porting=PORTING,
            unresolved=lexicon_obj["unresolved"]),
        "auditor_agent_object.json": agent_flags,
        "auditor_audit_report.json": audit_record,
    }


def _refuse(refused: list, what: str) -> None:
    """Stop the build on any refusal, naming the field and the reason and not the value.

    CLAUDE.md's rule about exception messages applies to this script like anywhere else: the
    refusals carry field names and reason names, both of which this repository authored, and
    the rejected value is not printed.
    """
    if refused:
        # Two shapes reach here. `validate_profile` and its siblings return dicts; `audit`'s
        # `Refusal` is a frozen dataclass. Both carry a `reason` this project authored, and
        # neither carries the rejected value — which is the property that matters, so both are
        # read rather than one being converted to the other.
        reasons = sorted({
            r.get("reason", "?") if isinstance(r, dict) else getattr(r, "reason", "?")
            for r in refused
        })
        raise SystemExit(
            f"the {what} example was refused by its own validator "
            f"({len(refused)} refusal(s), reasons: {reasons}). The example is generated from "
            "the schema, so a refusal means the schema moved and this script did not."
        )


def _render(obj: dict) -> str:
    """Two-space indent, keys in the order they were built, trailing newline.

    `sort_keys=False` on purpose: the schema's own order is part of what the example shows,
    and sorting would put `unresolved` in the middle of the profile's conventions.
    """
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def expected_files() -> dict[str, str]:
    """Every generated file mapped to its exact expected content, JSON and YAML alike.

    **One definition site, because there are two readers.** `main()` uses it for `--check` and
    for the write path, and `tests/test_prompt.py` uses it to fail when a file on disk has gone
    stale. A test that rebuilt this mapping itself would be checking its own copy of the
    convention — and the file it would silently stop covering is `RULES_FILE`, the one that is
    not JSON and therefore the one a JSON-shaped reimplementation drops.

    Kept out of `build()` because that function returns objects and this is their serialisation,
    which is the split `_render`'s docstring describes.
    """
    expected = {name: _render(obj) for name, obj in build().items()}
    expected[RULES_FILE] = RULES_YAML
    return expected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="do not write; exit 1 if any file is missing or stale")
    args = parser.parse_args(argv)

    expected = expected_files()

    if args.check:
        stale = []
        for name, content in expected.items():
            path = EXAMPLES / name
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(name)
        if stale:
            print("stale or missing: " + ", ".join(sorted(stale)))
            print("run: python3 tools/build_prompt_examples.py")
            return 1
        print(f"{len(expected)} example files current")
        return 0

    EXAMPLES.mkdir(parents=True, exist_ok=True)
    for name, content in expected.items():
        (EXAMPLES / name).write_text(content, encoding="utf-8")
    print(f"wrote {len(expected)} files to {EXAMPLES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
