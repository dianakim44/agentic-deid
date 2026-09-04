# Profiler prompt — `profile.json`

> **This file is part of the frozen window, from `port-multi` onward.** `WINDOW_FILES` gains
> `profiler.md`, `mapper.md` and `lexicon_builder.md` in the commit that implements this arm, so
> this file's bytes are hashed into `window_freeze.json` and onto every `agent_calls.jsonl` line
> of an arm that calls it.
>
> **`port-oneshot` and `port-loop` never call the Profiler and their freeze records do not name
> this file.** Those records are not re-hashed (DESIGN §5.5, §6.3): each attests to the files
> that existed when its calls were made, and a hash added retroactively would be a claim about a
> window that never applied. `recorded_window_fields()` reads each record's own `files` list for
> exactly this reason, so widening the window here cannot reach backwards.
>
> **The Profiler is called once per arm, before iteration 1, as the first of three
> out-of-loop calls** (Profiler → Mapper → LexiconBuilder; `config/naming.yaml`, axis
> `agent_role`). Its artefact is `paths.armprofile` —
> `results/{corpus}/{detector}/{supervision}/{porting}/profile.json` — which carries the four
> arm axes and **no `{iteration}` component**. That absence is the capability: DESIGN §4 defines
> `port-multi` as the arm in which *an agent authors the auxiliary artefacts the loop consumes
> but does not produce*, and a path with a round number in it would be an artefact the loop
> revises. The orchestrator writes this file once and opens it read-only thereafter.

The Profiler is one agent with one artefact: the corpus profile (DESIGN §3, `paths.armprofile`).
It does not detect, does not score, does not write rules, does not map type systems — that is
the Mapper's file — and does not decide when to stop. The orchestrator is deterministic code and
owns iteration, budget, and termination.

**It never sees gold spans and never sees corpus text.** It is shown a mechanical inventory of
the corpus and asked to choose, among enumerated conventions, the ones the loader should use.
Everything below that says "the profile states X" means "the agent claims X", and §4 is about
what happens when the claim is wrong.

---

## 1. Input — what the agent is shown

One block: the **mechanical inventory**, `profiles/{corpus}.raw.json`, filtered. Then nothing
else. No document, no span, no score.

### 1.1 The mechanical inventory, and why the split of labour runs where it does

`profiles/{corpus}.raw.json` is produced by a **hand-written inventory script** and its own
header says what it is: *"Mechanical inventory of the corpus as it sits on disk. Facts and
counts only, no interpretation."* It reports the directory layout, the file counts, the encoding
probe, the annotation format's schematic shape, the offset verification result, the type
counts, the document-id parse, and the length distribution.

**The inventory measures; the Profiler interprets.** That boundary is the whole design and it is
worth stating as two claims rather than one:

- **Code is better at the measuring.** "32 of 1000 `.txt` files carry a UTF-8 BOM, and in those
  32 the gold offsets count the BOM as one character: 761 of 761 spans match with the BOM kept,
  0 of 761 match with it stripped" is a fact obtained by slicing every span out of every file. A
  model asked to produce that number would produce a plausible one.
- **Code cannot do the interpreting, and that is not a limitation of this script.** The
  inventory reports that brat and XML ship **the same annotations twice** and that the XML type
  system is two-level while brat is flat. *Which encoding the loader should read* is a choice
  with consequences the inventory cannot rank. The `port-human` and `port-loop` arms made that
  choice by hand. Here an agent makes it, and that substitution is the arm.

**So an unanticipated corpus quirk is invisible to this agent, and that is a cost, recorded
here rather than discovered.** The Profiler chooses among conventions the schema in §2
enumerates. It cannot report a phenomenon there is no field for. Discovery belongs to the
inventory script, where a human reading bytes can add a key — `bom_offset_interaction` exists
because someone found it, not because a field was waiting. An arm in which the *agent* profiled
an unseen corpus from its bytes is a different and larger claim, and this is not it.

### 1.2 What is filtered out of the inventory, and that each exclusion is free

The inventory is a tracked, screened file, so nothing here is about publication. It is about
what may enter an arm and what may enter this agent's output. Four blocks are removed, and for
each the argument is that **the profile's fields can be filled without it**:

| removed | why | what the profile loses |
|---|---|---|
| `annotation_format.samples_verbatim` | three annotation lines quoted exactly off disk, containing a date surface and a given name. Justified in the inventory by a human's screened commit and the corpus's synthetic surfaces — a justification that does **not** transfer to a file this agent writes | nothing. `annotation_format.brat` and `.xml` give the schematic forms (`T{n}<TAB>{TYPE} {start} {end}<TAB>{surface}`), and a schema is what §2's `format` block needs. A worked example is supplied in §1.3 instead, invented here |
| `phi_types_by_split`, `provided_splits.spans_by_split`, `length_distribution.by_split*` | per-fold measurements of the annotations, one of the folds being sealed. Withholding only the test rows would not work: total minus train minus dev **is** test, so the exclusion has to take the whole per-fold decomposition or it is arithmetic away | nothing. §2's `type_inventory` is a list of types ordered by whole-corpus frequency. No field is per-fold. `provided_splits.measured` (500 / 250 / 250 / 3751 background) stays, because those are the corpus's documented split sizes and the profile has to be right about them |
| `identifiers.example_multi_document_stems` | named document groups — a pointer to which specific documents share an article, which is the group structure of specific documents | nothing. `documents_per_stem_distribution` (888 / 35 / 11 / 1 / 1) and `stems_with_more_than_one_document` carry what §2's `group_key` needs |
| `identifiers._corrected_2026_08_05` | provenance of a bug in the inventory *script*, and it names one document id | nothing. It is not about the corpus |

**The whole-corpus type counts stay** (`phi_types_brat_flat`, `phi_types_xml_two_level`,
`total_gold_spans`). An aggregate over all three folds is the corpus's published description of
itself; a per-fold figure for the sealed fold is a measurement of the sealed fold. With the
per-fold decomposition gone, the test rows are not recoverable from what is shown.

**Measured, and it is why the second exclusion costs nothing:** on `es-meddocan` the **dev fold
alone exhibits all 22 brat types** — it is the only fold that does, being the one that carries
the singleton `ID_EMPLEO_PERSONAL_SANITARIO`, which train and test both lack. So a type
inventory drawn from the whole-corpus counts is complete, and would still be complete if the
sealed fold were subtracted from it entirely.

**`sealed/` is never read, listed, or referred to.** The Profiler reads one JSON file. A path
under `sealed/` appearing in this agent's context is a harness bug to report, not a resource
(DESIGN §6.1).

### 1.3 One call, and the worked example

**One call per arm.** There is one corpus and one profile, the input is a few thousand tokens,
and there is no batching question of the kind `auditor.md` §1.3 settles. The Profiler's
`llm_calls` for an arm is 1, and it is charged to `role: profiler` on the call line — which is
why `call_line()` carries `role` at all (DESIGN §5.5): `llm_calls` summed over one
`agent_calls.jsonl` is the right answer for what the arm spent and no answer for who spent it.

**A standoff annotation line is named here rather than shown, and §2.4 is why.** Its shape,
in order: a label such as `T1`, a tab, the type name, a space, the start offset, a space, the
end offset, a tab, and then the annotated surface as it stands on disk. That schematic form is
also in the input itself — `annotation_format.brat` states it — so this paragraph is a
restatement and not the only source.

The three fields between the tabs are what the `format` and `offsets` blocks are about: a
label, a start, an end. Whether the end offset is exclusive, whether the start counts a
byte-order mark, and whether the label is the flat one or the coarse one are the questions §2
asks.

---

## 2. Output — the corpus profile

### 2.1 From the agent

One JSON object. **Emit the JSON and nothing else. No code fence, no triple-backtick line, no
`json` language tag, no preamble, no closing remark.** The first character of the response is
`{` and the last is `}`.

**This document shows no example of that object.** The schema is described below in prose, and
§2.4 says why that is the form the specification takes here.

**Thirteen keys, no others, all thirteen present.** Eleven are profile claims and two are about
the claims:

- **Nine take one string each, a member of the closed vocabulary of the same name:**
  `annotation_encoding`, `text_location`, `offset_unit`, `offset_base`, `offset_end`, `newline`,
  `bom`, `type_system_level`, `group_key`. The permitted values are not listed in this file —
  they are appended to this prompt from `config/naming.yaml` as it stands for the run, so the
  specification cannot drift from the axis it describes.
- **`type_inventory` takes a list of strings:** the corpus's own type labels, copied from the
  inventory's type counts. A label absent from those counts is refused as
  `type_not_in_inventory`.
- **`patient_key_available` takes a JSON boolean**, `true` or `false`, and not the strings
  `"true"` or `"false"`.
- **`cites` takes an object.** Each key is one of the field names above; each value is a dotted
  field path into the filtered inventory that was sent, naming where in it the claim was read.
  Not every field need be cited, and a path that does not resolve in what was sent is refused as
  `uncited_field`.
- **`unresolved` takes a list of field names from this schema**, drawn from the
  `profile_unresolved` vocabulary, which is also appended at call time. A field named there
  still carries its value; the list says the value is a guess. An empty list is a claim, not a
  formality: it says every field is known.

**Every value is a member of a closed vocabulary declared in `config/naming.yaml`, except
`type_inventory` (a list of the corpus's own labels, copied from the inventory's type counts)
and `cites`.** The vocabularies — `annotation_encoding`, `text_location`, `offset_unit`,
`offset_base`, `offset_end`, `newline`, `bom`, `type_system_level`, `group_key`,
`profile_unresolved` — are added to that file by the commit that implements the validator, not
coined in the validator, and not coined here. CLAUDE.md's rule covers values written into files
under `results/` as much as it covers axis names.

**There is no free-text field. None.** Not `note`, not `comment`, not `rationale`, not
`evidence`, not `example`. `auditor.md` §3 omits prose because any justification for a span is a
description of that span's text; the reason here is adjacent and weaker, and the omission is
kept anyway:

- A justification for "offsets are zero-based and exclusive" is a statement about a convention
  and not a quotation, so prose here is not *inherently* a leak. It is a **channel**, and the
  cheapest way to argue that a convention holds is to show a line it holds on. An agent asked
  to explain itself about an annotation format reaches for an annotation line.
- **`cites` does the work prose would do, and code can check it.** Each entry is a dotted field
  path into the inventory the agent was shown. Field paths are keys, not corpus text. And a
  cited path that does not exist in the filtered inventory is a refusal (§2.3) — which makes
  `cites` the one part of this artefact whose honesty is *verifiable*, rather than declared.
- **`unresolved` is how the agent says it does not know.** Entries are field names from this
  schema, drawn from `profile_unresolved`. A field listed in `unresolved` must still carry a
  value; the list says the value is a guess. Silence and a guess would otherwise be the same
  bytes.

### 2.2 The file, from the orchestrator

`paths.armprofile` — `results/{corpus}/{detector}/{supervision}/{porting}/profile.json`. Four
axes, no iteration (see the header). The orchestrator adds `corpus`, the inventory's own
`generated` date, the sha256 of the **filtered** inventory it sent, and the `refused` list. It
adds no claim and removes only claims it can prove impossible.

Seven keys, and the agent writes none of them: `corpus` and `porting`, the two axes a reader
needs to know which cell wrote the file; `inventory_generated`, the inventory's own date;
`inventory_filtered_sha256`; `profile`, holding the agent's object validated and unchanged;
`refused`, a list of objects each carrying a `field` and a `reason` from §2.3's table and **not**
the value that was refused; and `counts`, with `fields`, `unresolved`, `refused`, and a
`by_refusal` breakdown keyed by reason. A worked instance is not shown here for §2.4's reason;
`docs/prompts/examples/` holds one for a reader, outside this file and outside the call.

**`inventory_filtered_sha256` is what makes §1.2 checkable after the fact.** The claim "the
Profiler was not shown the per-fold decomposition" is otherwise a claim about a prompt that is
never written down (§6). The hash is of the filtered object, so a later run that filtered
differently has a different hash and the two profiles are visibly not comparable.

**The agent's object is written through unchanged, like `rules/{lang}.yaml` and unlike
`audit_report.json`.** The Auditor's flags need a coordinate translation the agent must not
perform, so its file is a *translated* claim. Nothing here needs translating. Validation removes
whole fields or nothing.

**A refused field keeps its name and its reason and not its value.** A value outside a declared
vocabulary is a value the project has no meaning for; recording it would put an undeclared
string into a file under `results/`, which is the thing the vocabulary rule exists to prevent.

### 2.3 What the validator refuses, and that it refuses rather than repairs

| refusal | what it catches |
|---|---|
| `undeclared_value` | a value outside its field's vocabulary in `config/naming.yaml` |
| `unknown_field` | a key not in §2.1's schema — including any prose key |
| `missing_field` | a schema field absent. There is no default; an absent convention is not a convention |
| `uncited_field` | a `cites` entry whose dotted path is not present in the filtered inventory that was sent |
| `type_not_in_inventory` | a `type_inventory` label absent from the inventory's type counts — an invented corpus label |
| `malformed` | not one JSON object; `type_inventory` not a list of strings; `unresolved` naming a field outside the schema |

**Refused, not repaired, and counted.** A validator that filled a missing `offset_base` with
`zero` would produce a convention the agent never claimed, and nothing in the file would say so.
A validator that dropped silently would make the profile shorter for a reason no reader could
see. `refused` carries the reason and `counts.refused` the total, so an arm in which the agent
lost the schema is visible as a number rather than as a thin profile.

**An unknown field is a refusal, not an ignored key** — the whitelist rule `write_errors()`
follows for `errors.jsonl` (DESIGN §5.5.1), and here for the sharper version of the same reason:
the field most likely to be added is the prose field §2.1 forbids.

**A profile with any refusal does not start the loop.** Unlike an audit report, which can be
thin and still be consumed, this artefact configures loading. A missing `bom` value is not a
degraded profile; it is no profile. The orchestrator writes the file with its `refused` list,
records the arm as a format failure in the shape `port-oneshot`'s `format_failure.json` already
has, and stops. **It does not retry with a repaired object and it does not fall back to the
hand-written `profiles/{corpus}.json`.** A fallback would make the arm report a result obtained
from the human artefact under the label of the agent one, which is the one outcome this rung
cannot survive.

### 2.4 Why this file contains no example, and no fenced block of any kind

**This prompt is sent to the model verbatim.** `assemble_profiler_prompt` joins this template,
the vocabulary frame, and the filtered inventory; the template is not summarised, excerpted, or
stripped on the way. So every block in this file reaches the agent, and a path this file merely
names does not.

**The first run of `port-multi` on `es-meddocan` failed because a demonstration was copied**
(`docs/notes/arm-port-multi-es.md`). The response obeyed three of the four prohibitions above
and wrapped the object in the fence this file used to quote its own example. That was the second
occurrence in this repository: `port-oneshot`'s first run wrapped its YAML the same way, and the
fix then was one file's wording plus a sentence saying the fence was this document's quoting
convention. The sentence was inherited by the four prompts written afterwards **together with
the fenced block it failed to neutralise**.

So the convention is now the absence of the demonstration, for three reasons in order:

1. **A demonstration that reaches the model reproduces the failure in a different form.** The
   observed failure is *the demonstration was copied*. Changing the delimiter — indentation, a
   different marker, a nested block — leaves a format in the prompt to be copied and changes
   only which characters get copied with it. Indentation is the worst of those for a YAML
   artefact specifically, because in YAML indentation *is* the format, so an indented example is
   more copyable than a fenced one rather than less.
2. **An example file is for people.** `docs/prompts/examples/` holds instances as validator
   fixtures and as what a reviewer reads. They are not sent to any agent, so they do not
   determine what a run does, and there is no argument from "the prompt names the path" — naming
   a path transmits no bytes.
3. **Therefore the example files are not in `WINDOW_FILES`.** The frozen window is the record of
   *what decided this run* (DESIGN §5.5, §6.3). A file the model never saw is not in that
   definition, and adding it would grow every freeze record with a hash that attests to nothing
   the call could have read.

**And the absence is checked rather than remembered.** `tests/test_prompt.py` refuses any
markdown file under `docs/prompts/` that contains a fence line, with no per-file exemption,
because a per-file exemption is precisely the path by which this defect was inherited. Two
mutations in `tests/mutations/run.py` — one removing that check, one exempting a single file from
it — keep the test load-bearing.

---

## 3. Prohibition — no corpus surface form in the output

**Do not quote, transcribe, paraphrase, or reconstruct any text from the corpus.** Not an
annotation line, not a surface, not a document id, not a fragment of one.

This agent is not shown corpus text, so the prohibition looks automatic. It is not, in one
specific way that is worth naming: **the inventory contains the corpus's own type labels, and
those labels are text from the corpus.** `type_inventory` copies them deliberately — a type
system cannot be described without its type names, they are the annotation schema rather than
the annotated content, and the Mapper's whole input is that list. So the rule is not "no strings
from the corpus"; it is:

- **Type labels: copied, and only from the inventory's type counts.** `type_not_in_inventory`
  enforces the source. A label the agent invented would be a label no corpus file carries, and
  the Mapper would map a type that does not exist.
- **Everything else: enumerated.** Every other value is a vocabulary member, which is a string
  this repository authored.
- **Field paths in `cites`: keys, not content**, and checked against the inventory.

**And the prohibition is enforced outside the prompt, because a prohibition that exists only in
a prompt is a request.** `unknown_field` refuses the field a surface form would have to arrive
in; `undeclared_value` refuses a surface form placed in an enumerated field. There is no field
left that accepts arbitrary text except `type_inventory`, and that one is checked against a
tracked file.

`profile.json` is **not** deny-listed in `tools/release_screen.py` — only the agent-authored
lexicon is (`config/naming.yaml`, `armlexicon`) — so this path is sniffed rather than blocked.
**That classification was decided on the strength of this section.** A prose field added here
later would not merely widen the schema; it would invalidate the screener decision that lets
this file be committed at all.

### Other prohibitions

1. **No claim about the test fold.** The Profiler is shown no per-fold measurement (§1.2) and
   `unresolved` has no entry for one. Split *sizes* are documented corpus facts and appear in
   the input; nothing else about any fold does.
2. **Only `config/naming.yaml` vocabulary.** A convention this corpus needs and the vocabulary
   lacks is added to that file by a human in a commit — never coined in a profile. The correct
   behaviour when no declared value fits is `undeclared_value` on a refused field, i.e. the arm
   fails loudly and a human extends the vocabulary. **A near-miss value chosen because it is the
   closest available is worse than a refusal**, because it loads.
3. **No detection, no scoring, no rules, no mapping.** The type *inventory* is this file's; the
   mapping from those types to the canonical ten is `mappings/{corpus}.yaml` and the Mapper's.
   One agent, one output file (DESIGN §3), and two agents never write the same file.
4. **The profile is never compared to gold.** It does not enter `metrics.json` and no termination
   decision reads it.

---

## 4. How the profile is consumed — and what happens when it is wrong

**The loader reads the profile, and that is what makes it consumed rather than decorative.** If
`bom` says `stripped`, the arm opens the text with `utf-8-sig`. This is the load path for the
whole arm, so the profile is not advice.

**A wrong profile fails at load with a count, not silently.** The loader already verifies every
gold span against the text it sliced — the inventory reports 22,795 verified and 0 mismatched by
exactly this procedure. Take the BOM field as the worked case: `stripped` shifts every span in
the 32 BOM-carrying files by one character, and the inventory's own measurement is that 0 of 761
spans in those files then match. The arm does not produce a slightly worse leak rate; it
produces a load-time offset-mismatch count and stops. **That is the property that makes handing
this decision to an agent tolerable at all**, and it is a property of the loader, not of the
Profiler's carefulness. Where a profile field has no such check, the field is a risk this arm
takes on the record.

**The offset-mismatch message quotes no surface** — `tests/test_meddocan_loader.py`'s
`test_offset_mismatch_message_quotes_no_surface` fixes that, and a profile-driven load failure
goes through the same message.

**`group_key` is checked, not applied, on a corpus whose split is already frozen.** This is the
sharpest limitation of running `port-multi` after the split exists, and it is stated rather than
worked around:

- `splits/es-meddocan.json` is confirmed and its test fold is sealed. A group key that
  re-partitioned the corpus would unseal it. So on this corpus the Profiler's `group_key` is
  compared against the frozen split's recorded key; **a disagreement is recorded in the profile
  and the split does not move.**
- Therefore **the `group_key` field has no causal path to this arm's leak rate on
  `es-meddocan`.** It is measured as an agreement, not consumed as a decision. On a corpus
  profiled before its split is drawn, the same field would be load-bearing.
- `patient_key_available` is in the same position: the inventory reports no patient field exists
  and `splits/` already records the consequence. An agent claiming otherwise is a disagreement
  to record.

**Nothing in `docs/prompts/rule_author.md` or `docs/prompts/auditor.md` changes, and that is
deliberate rather than incidental.** The profile reaches the loop through code, not through a
prompt block: Profiler → `profile.json` → the Mapper reads it → `mapping.yaml` → the loader
reads that → gold in canonical types → the scorer → the per-type numbers `rule_author.md` §1.3
already describes. The profile changes what those numbers count; it does not change the block's
format. Adding a profile block to `rule_author.md` §1.1 would edit a hashed file, put every
earlier arm's freeze record into drift, and buy nothing —
`docs/notes/window-freeze-history.md` is the record of what editing hashed files late costs
(DESIGN §6.3).

**A profile is not a result, and a well-formed profile is not a good one.** No refusals means
the agent produced every schema field from its declared vocabulary and cited the inventory for
each. Whether the values are the *right* ones is answered by the load, by the arm's leak rate,
and by the `group_key` agreement — all after the fact.

---

## 5. Tools — none

Not a shortened list: an empty one, and each entry a call would need contradicts the input.

- **No corpus access.** The one thing a search tool would return is corpus text, which is what
  §1's design withholds. A Profiler that can open a `.txt` is a Profiler that can quote one.
- **No filesystem.** It cannot re-read the unfiltered inventory, which would undo §1.2 —
  the filtering happens before the call and there must be no path back to what was removed.
- **No gold, no `rules/`, no `results/`, no `splits/`.** `splits/` matters specifically: an agent
  that could read the frozen split would report the group key back to its own source, and §4's
  agreement check would be measuring recall of a file rather than a judgement.
- **No writing.** The profile is assembled from the call's return value.
- **No network, no other agent.** The orchestrator sequences the three out-of-loop calls; there
  is no manager agent (DESIGN §3).

The agent reads one filtered JSON object and returns one JSON object. That narrowness is what
makes "the Profiler never sees corpus text" a fact about the code rather than a promise in a
prompt.

---

## 6. The filled prompt is never written down

Same rule as `rule_author.md` §6 and `auditor.md` §6: only this template is committed. A filled
instance is not committed, not logged, and not written to disk.

**This is the smallest corpus exposure of any prompt in the repository, and the reason is
structural rather than fortunate.** `auditor.md` §6 measures its masked-document prompt at about
210k tokens per dev round, a majority of whose in-scope identifiers are unmasked, because
unmasked is what leaked means. This prompt carries counts and schemas. After §1.2's filtering the
input holds no corpus surface at all.

Three consequences:

- **The filtering happens in code, before the call, and returns the filtered object** — not the
  full inventory with instructions to ignore parts of it. An instruction to ignore is not a
  filter, and `inventory_filtered_sha256` would attest to the wrong bytes.
- **Caching is not used for this call and the block is absent from `metrics.json`.** One call
  per arm has no second call to read a cached prefix, and `caching_boundary` is a closed
  vocabulary whose absence is recorded as the block's absence rather than as a value
  (`config/naming.yaml`).
- **This prompt is available on every corpus, including a corpus whose text may not leave the
  machine.** `auditor.md` §6 records that `port-loop` is *unavailable* under such a DUA, because
  an Auditor shown 0 characters produces no report. The Profiler is shown 0 characters by
  design. So the DUA restriction does not reach this agent — which also means the Profiler is
  the one part of `port-multi` that would survive onto a corpus where the rest of the arm cannot
  run, and that is worth recording as a portability fact rather than left to be noticed.

**The rule does not branch on which corpus is running.** MEDDOCAN and GraSCCo are synthetic and
are not exceptions (CLAUDE.md): a discipline that holds only where someone remembered it fails
on the day the corpora are swapped.

**And the same principle in one line, which is where all three prompts land:** the artefact that
survives contains references, and the text exists only in transit. `profile.json` is
conventions, vocabulary members and field paths. It has no transit.
