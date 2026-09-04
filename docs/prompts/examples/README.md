# Prompt output examples — for people, and for the validators

**Nothing in this directory is sent to any agent.** These are the schema instances the five
prompts used to carry inside their own text. They were moved here because the
templates are sent to the model verbatim, so an example inside a template is a demonstration
the model can copy — and it did, twice.

`docs/prompts/profiler.md` §2.4 carries the full argument and the same section appears in the
other four prompts. The short form, in three steps:

1. The observed failure is *the demonstration was copied*. A fix that leaves a demonstration in
   the prompt under a different delimiter changes which characters get copied, not whether. For
   a YAML artefact indentation is the worst delimiter of all, because in YAML indentation *is*
   the format.
2. An example file is for people: a validator fixture, and what a reviewer reads. It reaches no
   agent, so it does not determine what a run does. A prompt naming this path transmits no
   bytes.
3. So these files are **not** in `WINDOW_FILES`. The frozen window is the record of what decided
   a run (DESIGN §5.5, §6.3), and a file no call could read is not in that definition.

## What is here

Two files per agent: the object the agent returns, and the file the orchestrator writes around
it. The second is JSON even where the artefact on disk is YAML (`mapper_mapping_yaml.json`),
because these are generated from the record builder's return value and the serialisation is the
orchestrator's business.

| file | what it is | validated by |
|---|---|---|
| `profiler_agent_object.json` | `profiler.md` §2.1 — thirteen keys | `validate_profile` |
| `profiler_profile_json.json` | `profiler.md` §2.2 — `paths.armprofile` | `profile_record` |
| `mapper_agent_object.json` | `mapper.md` §2.1 — all 22 brat labels | `validate_mapping` |
| `mapper_mapping_yaml.json` | `mapper.md` §2.3 — `paths.armmapping` | `mapping_record` |
| `lexicon_builder_agent_object.json` | `lexicon_builder.md` §2.1 — three levels | `validate_lexicon` |
| `lexicon_builder_manifest_json.json` | `lexicon_builder.md` §2.2 — the manifest | `lexicon_manifest` |
| `auditor_agent_object.json` | `auditor.md` §2.1 — `flags`, 0-based `line` | `parse_response` |
| `auditor_audit_report.json` | `auditor.md` §2.2 — `paths.auditreport` | `report` |
| `rule_author_rules_es.yaml` | `rule_author.md` §2 — the rule file itself | `load_rules` |

**`rule_author_rules_es.yaml` is the one file here that is not JSON**, because the artefact it
illustrates is not, and it is a literal in the builder rather than a dumped object: the point of
this fixture is the *file* — comments and layout included — and `yaml.dump` would produce a
normalised form no agent emits. It is still loaded through the real `load_rules`, with a
temporary lexicon collection built for its one `lexicon:` rule, so being a literal buys it no
exemption from the schema.

**The `auditor_*` pair pins down a fact its removed example never stated.** `line` is 0-based.
The fenced example showed `"line": 3` beside three displayed lines, which cannot be 0-based and
so established nothing, and the prompt said nothing either way.
`tools/probe_prompt_format.py` measured what that omission costs: on one invented document the
same model went from 1 flag accepted and 5 refused to **5 accepted and 1 refused** once the
prompt stated the base (`docs/notes/prompt-format-probe.md`, 2026-09-04). The 69–112
`crosses_a_line` refusals per round in `port-loop` iterations 2–8 are consistent with the same
off-by-one, and that is a suspicion rather than a finding: those rounds cannot be re-run.

They are built for `es-meddocan` and for a real corpus rather than an invented one, because a
`cites` path has to resolve in a real filtered inventory and a `type_inventory` label has to be
a real label — an invented corpus would exercise only the shape checks.

## They are generated, not typed

`python3 tools/build_prompt_examples.py` writes them; `--check` reports staleness and `expected_files()` is what
`tests/test_prompt.py` reads, so the YAML file is covered alongside the eight JSON ones. Every object goes through its real validator on the way and the
build fails on any refusal, so an example cannot document a schema that no longer exists.

**Edit the builder, not these files.**

## What this directory is not allowed to become

**It is not a place to put text a prompt then quotes.** The Mapper case is the sharp one:
`mapper_agent_object.json` holds DESIGN §9.0's pairings, which `mapper.md` §1.3 deliberately
withholds from the agent. That is safe only because nothing sends this file.
`_check_design_withheld()` in `src/llm/prompt.py` refuses an assembled Mapper prompt that shows
a source label beside its §9.0 target, so inlining this example back into the template fails a
check rather than silently teaching the answer.
