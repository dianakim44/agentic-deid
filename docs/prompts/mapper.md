# Mapper prompt — `mapping.yaml`

> **This file is part of the frozen window, from `port-multi` onward.** `WINDOW_FILES` gains
> `profiler.md`, `mapper.md` and `lexicon_builder.md` in the commit that implements this arm, so
> this file's bytes are hashed into `window_freeze.json` and onto every `agent_calls.jsonl` line
> of an arm that calls it.
>
> **`port-oneshot` and `port-loop` never call the Mapper and their freeze records do not name this
> file.** Those records are not re-hashed (DESIGN §5.5, §6.3), for the reason `profiler.md`'s
> header states: each attests to the files that existed when its calls were made.
>
> **The Mapper is called once per arm, before iteration 1, as the second of three out-of-loop
> calls** (Profiler → Mapper → LexiconBuilder; `config/naming.yaml`, axis `agent_role`). Its
> artefact is `paths.armmapping` —
> `results/{corpus}/{detector}/{supervision}/{porting}/mapping.yaml` — four arm axes and **no
> `{iteration}` component**, for the reason `profiler.md` gives: a round number would make it an
> artefact the loop revises, and `port-multi`'s capability is that an agent authors what the loop
> *consumes but does not produce* (DESIGN §4).
>
> **It is called second because it reads the Profiler's output.** A refused profile stops the arm
> (`profiler.md` §2.3), so the Mapper is only ever called on a validated profile.

The Mapper is one agent with one artefact: the type-system mapping (DESIGN §3, `paths.armmapping`).
It maps the corpus's own type labels onto the canonical ten of DESIGN §9.0. It does not detect,
does not score, does not write rules, does not choose format or offset conventions — that is the
Profiler's file — and does not decide when to stop.

**It never sees gold spans and never sees corpus text.** It is shown a list of type labels and a
list of canonical types, and asked to pair them.

**Read §4 before §2.** On any corpus DESIGN §9.0 already covers, this artefact is *recorded and
not applied*, and that fact narrows what the Mapper can contribute to almost nothing. Writing the
schema first and the consumption last would read as though the mapping loads. It does not.

---

## 1. Input — what the agent is shown

Three blocks, in this order, and nothing else. No document, no span, no score, no count.

### 1.1 The corpus's own type labels

`profile.type_inventory` from `paths.armprofile`, as the Profiler wrote it, plus
`profile.type_system_level` (`flat` or `two_level`) so that the agent knows which of a corpus's
two label systems the list is drawn from. On `es-meddocan` those are different lists — brat is
flat and 22 labels, the XML is two-level — and a mapping over the wrong one maps labels the
loader will never see.

**Nothing else from the profile is shown.** Not `bom`, not the offset block, not `group_key`.
Those configure loading and the Mapper has no use for them; a field shown to an agent that cannot
use it is a field it can contradict.

**So the Mapper inherits the Profiler's errors and cannot detect them.** If `type_inventory` is
short a label, the Mapper maps a short list, and the missing label surfaces as an
`unmapped_source_type` refusal *against the inventory it was given* — which is a check that the
Mapper copied its input, not a check that the input was right. The exhaustiveness check in §2.3
is therefore weaker than it looks, and this is the arm's dependency chain rather than a defect in
either agent: `profiler.md` §1.1 records that the type counts come from a hand-written inventory
script that slices every span, so the list's correctness is a property of that script.

### 1.2 The canonical ten, and the exclusions, read from `config/naming.yaml`

The `phi_type` axis with its glosses, verbatim, and the `excluded_types` block with its glosses.
Both are tracked, committed, published files this repository authored — they are not corpus text
and there is no filtering question of the kind `profiler.md` §1.2 settles.

The glosses are shown because they carry the decisions: `LOCATION_AREA` says "place names and
postcodes, merged per DESIGN §9.2", `OTHER` says "residual bucket shipped by a corpus; not a
rule-development target", `NAME` says role stays in `subtype`. A bare list of ten names would make
the agent guess at the merge.

### 1.3 What is deliberately withheld: DESIGN §9.0's table

**The agent is not shown the human-authored mapping, and this is the whole design of the input.**
§9.0 already states `es-meddocan`'s mapping and `de-grascco`'s, source type by source type. An
agent shown that table and asked for a mapping would be asked to copy one, and §4's agreement
figure would measure reading comprehension. This is `profiler.md` §5's argument about `splits/`,
in the one place where it decides the entire value of the artefact rather than one field of it.

Two consequences, both mechanical rather than trusted:

- **§4's disagreements are the only evidence this arm produces, so the withholding has to hold in
  code.** The filtering happens before the call and the tool list is empty (§5); `DESIGN.md` is
  not readable and `mappings/{corpus}.yaml` is not readable.
- **`inventory_filtered_sha256` has no analogue here and does not need one.** The Mapper's input
  is assembled from two tracked files whose bytes are already in the window
  (`config/naming.yaml`) or hashed into the artefact it reads (`profile.json`, whose own
  `inventory_filtered_sha256` attests to its source). The orchestrator records
  `profile_sha256` instead — the hash of the profile object the mapping was built from — so that
  a mapping and the type inventory it mapped cannot be separated after the fact.

### 1.4 One call

One corpus, one mapping, an input of a few hundred tokens. The Mapper's `llm_calls` for an arm is
1, charged to `role: mapper` on the call line (DESIGN §5.5). There is no batching question:
`auditor.md` §1.3 has one because it is shown documents, and this agent is shown two lists.

---

## 2. Output — the mapping

### 2.1 From the agent

One JSON object. **Emit the JSON and nothing else. No code fence, no triple-backtick line, no
`json` language tag, no preamble, no closing remark.** The first character of the response is `{`
and the last is `}`.

**This document shows no example of that object.** The schema is described below in prose, and
§2.4 says why that is the form the specification takes here.

**The agent emits JSON and the orchestrator writes YAML.** `paths.armmapping` fixes the extension
and that key is committed; it is not edited here. The divergence from `rule_author.md`, which has
its agent emit YAML directly, is deliberate: a rule file needs block structure, comments and
multi-line patterns, and its schema is worth that parse surface. A mapping is one flat
label→label table, and YAML from a model brings anchors, tags, implicit typing and duplicate keys
for no gain. Serialising a validated object is the narrower path.

**Three keys, no others, all three present.**

- **`map` takes an object.** Each key is one source label, copied from the `type_inventory` list
  in the input. Each value is an object with exactly two keys: `canonical`, one member of the
  `phi_type` axis, and `basis`, one member of `mapping_basis`.
- **`excluded` takes an object of the same outer shape but a different inner one.** Each key is
  again a source label; each value is an object with exactly two keys: `excluded_type`, one
  member of `excluded_types`, and `basis`. **`excluded` entries take `excluded_type` and never
  `canonical`; `map` entries take `canonical` and never `excluded_type`.** That is the one place
  in this schema where the two halves differ, and it is the mistake to avoid.
- **`unresolved` takes a list of strings**, each a key that appears in `map` or in `excluded`. A
  label named there still carries its full assignment; the list says the assignment is a guess.
  An empty list is a claim: it says every pairing is known.

Every source label in the input appears **exactly once** across `map` and `excluded`: a label in
both is refused, and a label in neither is refused as `unmapped_source_type`. The permitted values
of `phi_type`, `excluded_types` and `mapping_basis` are appended to this prompt from
`config/naming.yaml` as it stands for the run, with their glosses, and are not listed in this
file — so the specification cannot drift from the axes it describes. `unresolved` needs no such
list: its admissible entries are the source labels the response itself assigned.

**Every value is a member of a closed vocabulary declared in `config/naming.yaml`**, and the keys
are labels copied from `type_inventory`. `canonical` draws from the `phi_type` axis,
`excluded_type` from `excluded_types`, `basis` from a new `mapping_basis` vocabulary, and
`unresolved` entries are keys of `map` or `excluded`. `mapping_basis`, `mapping_refusal` and
`mapping_unresolved` are added to `config/naming.yaml` by the commit that implements the
validator — not coined in the validator, and not coined here. CLAUDE.md's vocabulary rule covers
values written into files under `results/` as much as it covers axis names.

**There is no free-text field. None.** Not `note`, not `comment`, not `rationale`, not
`evidence`, not `example`. The argument is `profiler.md` §2.1's and is not weakened by this
agent's smaller exposure: prose about a type system is not inherently a leak, it is a channel,
and the cheapest way to argue that `SOURCE_LABEL_C` is coarser than a canonical type is to show
two surfaces it covers.

### 2.2 `basis` is the `cites` field, and it is not derivable from the value

`profiler.md`'s `cites` maps each field to a dotted path into the inventory, and the check is that
the path exists. That shape does not transfer: the Mapper's input is two lists, so a path-style
citation for every entry would be `phi_type.<the value the agent just wrote>` — derivable from the
assignment, and therefore evidence of nothing.

`basis` names **why the pairing holds**, from a closed set, and the check is that the basis and the
assignment are *consistent*:

| basis | the claim | checkable pairing |
|---|---|---|
| `canonical_gloss` | the canonical type's own gloss names this kind of thing | must appear on a `map` entry |
| `source_label_family` | the source label's own stem or prefix names the kind, and the corpus has a family of such labels sharing it | must appear on a `map` entry, and at least two inventory labels must share the prefix |
| `source_type_is_coarser` | the source type's extension is wider than any one canonical type, and the coarse canonical is taken | must appear on a `map` entry whose target is a canonical type whose gloss declares a merge |
| `residual_bucket` | the source type is the corpus's own residual bucket | must appear on a `map` entry whose target is `OTHER` |
| `design_exclusion` | the type is one the project excludes from scoring | must appear on an `excluded` entry, and nowhere else |

`basis_mismatch` refuses a violated pairing (§2.3). So `basis` is the one part of this artefact
whose honesty is *verifiable* rather than declared — the property `profiler.md` §2.1 claims for
`cites`, obtained by a different mechanism because the input is a different shape.

**`source_type_is_coarser` and `unresolved` are where this agent can say something a lookup table
cannot**, and §4 argues they are close to the whole of it. A mapping that flags a granularity
mismatch is reporting a property of the corpus's taxonomy; §9.2 records exactly one such finding
(`TERRITORIO` mixing place names and postcodes) and it took a human sampling 2,862 spans to state
it. **The agent is shown no counts and cannot reproduce that measurement** — it can only claim the
mismatch from the label and the gloss, which is a weaker claim from cheaper evidence, and that is
the claim `source_type_is_coarser` records.

**`unresolved` is how the agent says it does not know.** An entry must still carry a full
assignment; the list says the assignment is a guess. Silence and a guess would otherwise be the
same bytes.

### 2.3 The file, from the orchestrator

`paths.armmapping`. The orchestrator adds `corpus`, the profile hash, the `refused` list, the
`disagreements` list (§4), and counts. It adds no mapping claim.

Nine top-level keys, and the agent writes none of them. `corpus` and `porting`, the two axes that
say which cell wrote the file. `profile_sha256`. `design_mapping_source`, naming the section of
DESIGN the comparison was made against. `mapping` and `excluded`, holding the agent's two objects
validated and unchanged. `refused`, a list whose entries carry a `source_type` and a `reason` from
the table below and **not** the value that was refused. `disagreements`, a list whose entries carry
a `source_type`, the `agent`'s canonical type and the `design`'s (§4). `applied`, one of `design`
or `agent`. And `counts`, with `source_types`, `mapped`, `excluded`, `unresolved`, `refused`, a
`by_refusal` breakdown keyed by reason, `disagreements`, and `compared_against_design`.

A worked instance is not shown here for §2.4's reason; `docs/prompts/examples/` holds one for a
reader, outside this file and outside the call.

**`applied` is a required field and its value is not the agent's to set.** §4 is what it
records. On a corpus DESIGN §9.0 does not cover the value is `agent`, and the field exists so that
a reader of one file can tell which without knowing which corpora §9.0 lists.

**The agent's object is written through unchanged**, like `rules/{lang}.yaml` and unlike
`audit_report.json`. Nothing here needs the coordinate translation the Auditor's flags need.
Validation removes whole entries or nothing.

**A refused entry keeps its source label and its reason and not its value.** A value outside a
declared vocabulary is a value the project has no meaning for, and recording it would put an
undeclared string into a file under `results/`.

| refusal | what it catches |
|---|---|
| `undeclared_value` | a `canonical`, `excluded_type` or `basis` outside its vocabulary in `config/naming.yaml` |
| `unknown_field` | a key not in §2.1's schema — including any prose key |
| `missing_field` | an entry without `canonical`/`excluded_type`, or without `basis` |
| `type_not_in_inventory` | a key absent from `profile.type_inventory` — an invented corpus label |
| `unmapped_source_type` | an inventory label appearing in neither `map` nor `excluded` |
| `duplicate_source_type` | a label in both `map` and `excluded` |
| `basis_mismatch` | a `basis` whose pairing in §2.2 does not hold |
| `malformed` | not one JSON object; an entry that is not an object; `unresolved` naming a label outside `map` and `excluded` |

**`unmapped_source_type` is the exhaustiveness check, and §9.0's exhaustiveness claim is what it
enforces.** §9.0 says both corpora map in exhaustively and that no gold span is silently dropped.
An unmapped label is exactly a silently dropped set of gold spans, so partial coverage is a
refusal and not a thin mapping. Note the limit stated in §1.1: this checks the mapping against the
inventory, not the inventory against the corpus.

**Refused, not repaired, and counted.** A validator that sent an unmapped label to `OTHER` would
produce an assignment the agent never made, and `OTHER` is the one canonical type where that would
look plausible and change the leak-rate denominator (§9.4). `refused` carries the reason and
`counts.refused` the total.

**A mapping with any refusal does not start the loop** — and this holds on every corpus, including
the ones where §4 means the mapping is not loaded. Making the failure semantics depend on whether
§9.0 happens to cover the corpus would be a rule that branches on which corpus is running, which
is the shape CLAUDE.md rejects: a discipline that holds only where someone remembered it fails on
the day the corpora are swapped. The orchestrator writes the file with its `refused` list, records
the arm as a format failure in the shape `port-oneshot`'s `format_failure.json` already has, and
stops. **It does not retry with a repaired object and it does not fall back to
`mappings/{corpus}.yaml`.** A fallback would report a result obtained from the human artefact under
the label of the agent one — and here that fallback is especially tempting, because §4 means the
human mapping is what loads anyway. It is still forbidden: §4 records a *disagreement between two
mappings*, and an arm whose agent produced no mapping has nothing to disagree.

### 2.4 Why this file contains no example, and no fenced block of any kind

**This prompt is sent to the model verbatim.** `assemble_mapper_prompt` joins this template, the
type-label frame, and one closing instruction; the template is not summarised, excerpted, or
stripped on the way. So every block in this file reaches the agent, and a path this file merely
names does not.

**The first run of `port-multi` on `es-meddocan` never reached this call, because the Profiler's
response wrapped its object in the fence that prompt used to quote its own example**
(`docs/notes/arm-port-multi-es.md`). That was the second occurrence in this repository:
`port-oneshot`'s first run wrapped its YAML the same way, and the fix then was one file's wording
plus a sentence saying the fence was this document's quoting convention. The sentence was inherited
by the four prompts written afterwards **together with the fenced block it failed to neutralise** —
this file among them.

So the convention is now the absence of the demonstration, for three reasons in order:

1. **A demonstration that reaches the model reproduces the failure in a different form.** The
   observed failure is *the demonstration was copied*. Changing the delimiter — indentation, a
   different marker, a nested block — leaves a format in the prompt to be copied and changes only
   which characters get copied with it. Indentation is the worst of those for a YAML artefact
   specifically, because in YAML indentation *is* the format, so an indented example is more
   copyable than a fenced one rather than less.
2. **An example file is for people.** `docs/prompts/examples/` holds instances as validator
   fixtures and as what a reviewer reads. They are not sent to any agent, so they do not determine
   what a run does, and there is no argument from "the prompt names the path" — naming a path
   transmits no bytes.
3. **Therefore the example files are not in `WINDOW_FILES`.** The frozen window is the record of
   *what decided this run* (DESIGN §5.5, §6.3). A file the model never saw is not in that
   definition, and adding it would grow every freeze record with a hash that attests to nothing the
   call could have read.

**And the absence is checked rather than remembered.** `tests/test_prompt.py` refuses any markdown
file under `docs/prompts/` that contains a fence line, with no per-file exemption, because a
per-file exemption is precisely the path by which this defect was inherited. Two mutations in
`tests/mutations/run.py` — one removing that check, one exempting a single file from it — keep the
test load-bearing.

---

## 3. Prohibition — no corpus surface form in the output

**Do not quote, transcribe, paraphrase, or reconstruct any text from the corpus.** Not an
annotation line, not a surface, not a document id, not a fragment of one.

**The type labels are the exception, and they are the exception in `profiler.md` §3 for the same
reason**: a type system cannot be described without its type names, they are the annotation schema
rather than the annotated content, and §9.0 already publishes both corpora's label sets in DESIGN.
The rule is therefore not "no strings from the corpus":

- **Source labels: copied as keys, and only from `profile.type_inventory`.**
  `type_not_in_inventory` enforces the source. A label the agent invented would be a mapping for a
  type no corpus file carries.
- **Everything else: enumerated.** Canonical types, excluded types and bases are vocabulary
  members, which are strings this repository authored.
- **No counts.** The agent is shown none (§1.1), so a span count appearing in this artefact would
  be invented. There is no field for one.

**And the prohibition is enforced outside the prompt, because a prohibition that exists only in a
prompt is a request.** `unknown_field` refuses the field a surface form would have to arrive in;
`undeclared_value` refuses one placed in an enumerated field. Every key is checked against
`type_inventory` and every value against a vocabulary, so no field accepts arbitrary text.

`mapping.yaml` is **not** deny-listed in `tools/release_screen.py` — `config/naming.yaml` records
the reason at `paths.armmapping`: authorship moving from a human to an agent does not change the
file's content risk, and the human version of this mapping is already public as §9.0's table. So
the path is sniffed rather than blocked, **and that classification was decided on the strength of
this section.** A prose field added here later would not merely widen the schema; it would
invalidate the screener decision that lets this file be committed at all.

### Other prohibitions

1. **No claim about the test fold.** The Mapper is shown no per-fold measurement and no counts at
   all, and `unresolved` has no entry for a fold.
2. **Only `config/naming.yaml` vocabulary.** A canonical type this corpus needs and the axis lacks
   is added to that file by a human in a commit — never coined in a mapping. The correct behaviour
   when no declared value fits is `undeclared_value` on a refused entry: the arm fails loudly and a
   human extends the axis. **A near-miss assignment chosen because it is the closest available is
   worse than a refusal**, and worse here than for the Profiler, because a near-miss profile field
   fails at load (`profiler.md` §4) and a near-miss mapping scores.
3. **No new canonical types, no new exclusions.** Adding a type to the canonical set changes what
   every arm on the ladder is scored on. `OTHER` is the residual bucket and its gloss says it is
   not a rule-development target; it is not a place to put a type the agent found awkward, which is
   what `unresolved` is for.
4. **No detection, no scoring, no rules, no profiling.** The type *inventory* is the Profiler's
   file; the mapping from those types to the canonical ten is this one. One agent, one output file
   (DESIGN §3), and two agents never write the same file.
5. **The mapping is never compared to gold**, and on the corpora §9.0 covers it does not enter
   `metrics.json` at all (§4). No termination decision reads it.

---

## 4. §9.0 wins: the mapping is recorded, not applied — and what that leaves the Mapper

### 4.1 The decision

**On any corpus DESIGN §9.0 states a mapping for, §9.0's mapping is what the loader uses. The
Mapper's mapping is written to `paths.armmapping`, compared against §9.0 entry by entry, and every
disagreement is recorded in `disagreements` with `applied: design`. Nothing is overwritten and no
disagreement fails the arm.** As of today that is `es-meddocan` and `de-grascco`.

**DESIGN §9.0 states this decision and DESIGN §6.7 pre-registers its consequences**, both dated
2026-09-01 and both written before the first call. This section is the operative statement of the
comparison procedure; it does not decide anything §9.0 does not.

The premise is that this arm's result cannot change §9.0. §9.0 says so in its own words — the
mapping is "a design input shared by every arm, not one arm's output" — and the reason is that the
mapping decides **which canonical type each gold span gets**, which is to say it decides the
evaluation labels. Three things follow, and only the first is about tidiness:

- **Applying the agent's mapping would score `port-multi` against different gold from every arm
  below it.** DESIGN §4's ladder isolates one capability per rung; a rung that also relabelled the
  gold would leave nothing for its leak rate to be compared to. The `port-loop` figures already
  committed would not be a baseline for it.
- **It would let an agent redefine the evaluation labels, which is the project's central claim
  collapsing.** §9.2 refuses to split `TERRITORIO` with a `^\d{5}$` heuristic on the ground that "a
  label produced by inference is not gold", and that the `sup-free` arm's claim about
  annotation-free *supervision* is only meaningful if the *evaluation* labels stay untouched. A
  mapping authored by a model is inference. The `sup-free` axis is about where a detector's
  supervision comes from and has never been about where the gold's labels come from.
- **It would move the leak-rate denominator.** §9.4 puts sparse types in the denominator and §9.1
  states the excluded share to two decimals per fold. An agent excluding one more type, or one
  fewer, changes the denominator by a measurable amount — and a leak rate is not comparable across
  denominators.

**Why not refuse the arm on disagreement.** Refusing would make the arm run only when the agent
reproduces the human table, which converts the one measurement the Mapper can produce into a gate
and destroys it: a disagreement rate is not observable if disagreement is fatal. It would also make
`port-multi`'s existence contingent on the outcome of one call.

**Why not overwrite and report both.** Because "report both" is not available. Scoring is one pass
over one gold labelling; two mappings mean two leak rates, two complementarity decompositions, and
two per-type tables per arm, and `paths.metrics` has four axes with no room for a mapping component
— §5.3's argument against parsing filenames and §4's refusal of a fifth path component both land on
the same answer. One mapping loads, and it is the one that is invariant across the ladder.

**Recording only is therefore not the weak option; it is the only one that leaves anything
measurable.** What it measures is stated in §4.3, and it is small.

### 4.2 What is compared, and how

Entry by entry over `profile.type_inventory`, against §9.0's table and §9.1's exclusion list, both
transcribed into the validator's fixture from DESIGN by the implementing commit. Two labels agree
when the agent's `canonical` equals §9.0's canonical, or when both place the label in `excluded`
with the same `excluded_type`. A label the agent maps and §9.0 excludes is a disagreement, and so
is the converse — the excluded/mapped boundary is where a disagreement costs the most (§9.1's
9.90% and 9.68%), so it is not a separate category.

`counts.compared_against_design` records how many labels the comparison covered, so that a mapping
compared against a stale transcription of §9.0 is visible as a count that does not equal
`counts.source_types`.

**`basis` is not compared.** §9.0 gives no basis for its assignments — the table is a table — so
there is nothing to compare against, and inventing one to compare against would be writing the
answer key after seeing the answer.

### 4.3 What this leaves the Mapper, stated as a limitation

**The Mapper has no causal path to this arm's leak rate on `es-meddocan` or `de-grascco`. Not one
field: the whole artefact.**

This is the position `profiler.md` §4 records for `group_key` and `patient_key_available`, and
**it is strictly worse here, which is the fact this section exists to record.** For the Profiler
the frozen split disables two fields of eleven, and the other nine — encoding, offset base, offset
end, newline, BOM, type system level, text location, offset unit, type inventory — configure the
load and are falsified at load time with a count. For the Mapper there is no such remainder. The
artefact is the mapping; the mapping is what §9.0 supplies; so `port-multi` on these two corpora
exercises the Mapper's *authorship* and consumes none of its *output*.

**And there is no load-time falsifier to substitute for the missing causal path.** `profiler.md`
§4 can argue that handing format decisions to an agent is tolerable because a wrong `bom` produces
0 of 761 matching spans and stops the arm. A wrong mapping produces no exception: every span still
has a canonical type, the load succeeds, and the file is well-formed. The checks in §2.3 catch an
*incoherent* mapping — invented labels, unmapped labels, mismatched bases — and none of them catch
a *wrong* one. The only detector of a wrong assignment is §4.2's comparison with §9.0, i.e. the
human artefact, plus the per-type recall collapse a mis-assignment would show in §5.1's per-type
table — which is a signal after the fact and only if the mis-assignment is large.

So what does the Mapper contribute on these corpora? Four things, all of them evidence about the
agent rather than results from the arm. **DESIGN §6.7.6 fixes them as five named figures
(M1–M5) with no thresholds and an explicit prohibition on summing them into a score**; the four
below are what those figures are for:

1. **A disagreement rate against a human mapping**, per source type, on two corpora whose type
   systems differ in size by a factor of two. That is the arm's finding.
2. **Where the disagreements fall.** A disagreement inside the canonical ten and a disagreement
   across the excluded/mapped boundary are different failures, and §4.2 keeps them distinguishable.
3. **`source_type_is_coarser` flags**, which are the agent's claims about the corpus's taxonomy
   rather than about the canonical set, and are checkable against §9.2 for `es-meddocan` — the one
   place a human already wrote the finding down.
4. **`unresolved` entries**, which say where an agent given the glosses and the labels and nothing
   else could not decide. That is a statement about how much of §9.0's table is derivable from
   published vocabulary, which is worth knowing before the next corpus.

**On a corpus §9.0 does not cover, the same artefact is load-bearing and `applied` reads `agent`.**
`ko-surro`, `es-carmen` and `en-n2c2` are in the `corpus` axis with no §9.0 row. There the mapping
decides the gold labels, the comparison in §4.2 does not run, `counts.compared_against_design` is
0, and every limitation above inverts into a risk: an unfalsifiable artefact that sets the
evaluation labels. **That is a larger claim than this arm makes today and it needs a decision
before it runs**, not a prompt that quietly permits it. DESIGN §6.7 records it as an open question
rather than a permission; the shape of an answer is a human-authored §9.0 row for that corpus,
drafted before the arm runs, which is `es-meddocan`'s situation and returns the Mapper to §4.1.

### 4.4 What does not change

**Nothing in `docs/prompts/rule_author.md` or `docs/prompts/auditor.md` changes.** The mapping
reaches the loop through code: Mapper → `mapping.yaml` → recorded → the loader uses §9.0's mapping
→ gold in canonical types → the scorer → the per-type numbers `rule_author.md` §1.3 already
describes. On these corpora it does not even change what those numbers count. Adding a mapping
block to `rule_author.md` §1.1 would edit a hashed file, put every earlier arm's freeze record into
drift, and buy nothing — `docs/notes/window-freeze-history.md` is the record of what editing hashed
files late costs (DESIGN §6.3).

**A mapping is not a result, and a well-formed mapping is not a correct one.** No refusals means
the agent covered the inventory exhaustively with declared vocabulary and paired each assignment
with a consistent basis. Whether the assignments are *right* is answered by §4.2's comparison, and
on a corpus with no §9.0 row it is not answered at all.

---

## 5. Tools — none

Not a shortened list: an empty one, and each entry a call would need contradicts §1.3.

- **No `DESIGN.md`, no `mappings/{corpus}.yaml`.** This is the one that matters most and it is
  the reason the list is empty rather than short. Either would hand the agent the answer, and
  §4.2's disagreement count — the arm's whole finding on these corpora — would become a
  measurement of how well a model copies a table it was given.
- **No corpus access.** The one thing a search tool would return is corpus text. A Mapper that can
  open a `.txt` is a Mapper that can quote one, and it would also let the agent measure the
  granularity claim §2.2 deliberately leaves as a claim from the label.
- **No filesystem.** It cannot re-read the unfiltered profile or `config/naming.yaml` beyond the
  two blocks §1.2 sends. The filtering happens before the call and there must be no path back.
- **No gold, no `rules/`, no `results/`, no `splits/`.** `results/` matters specifically: another
  arm's `mapping.yaml` is a previous agent's answer, and an arm that read one would be measuring
  agreement between two calls of the same prompt while reporting it as agreement with §9.0.
- **No writing.** The mapping is assembled from the call's return value.
- **No network, no other agent.** The orchestrator sequences the three out-of-loop calls; there is
  no manager agent (DESIGN §3).

The agent reads two lists and returns one JSON object.

---

## 6. The filled prompt is never written down

Same rule as `profiler.md` §6, `rule_author.md` §6 and `auditor.md` §6: only this template is
committed. A filled instance is not committed, not logged, and not written to disk.

**This prompt's corpus exposure is the Profiler's and no larger — the type labels and nothing
else.** `auditor.md` §6 measures its masked-document prompt at about 210k tokens per dev round, a
majority of whose in-scope identifiers are unmasked, because unmasked is what leaked means. This
prompt carries two lists of labels.

Three consequences:

- **The filtering happens in code, before the call, and returns the filtered blocks** — not the
  whole profile with instructions to ignore parts of it, and not `DESIGN.md` with instructions not
  to read §9.0. An instruction to ignore is not a filter, and here it would be an instruction not
  to read the answer key, which is the weakest possible form of §1.3.
- **Caching is not used for this call and the block is absent from `metrics.json`.** One call per
  arm has no second call to read a cached prefix, and `caching_boundary` is a closed vocabulary
  whose absence is recorded as the block's absence rather than as a value (`config/naming.yaml`).
- **This prompt is available on every corpus, including a corpus whose text may not leave the
  machine.** The Mapper is shown 0 characters of corpus text by design, so the DUA restriction that
  makes `port-loop` unavailable (`auditor.md` §6) does not reach it. It is, with the Profiler, one
  of the two parts of `port-multi` that would survive onto a corpus where the rest of the arm
  cannot run — worth recording as a portability fact, and worth reading against §4.3: on such a
  corpus there is also no §9.0 row, so the part that survives is the part with no falsifier.

**The rule does not branch on which corpus is running.** MEDDOCAN and GraSCCo are synthetic and are
not exceptions (CLAUDE.md): a discipline that holds only where someone remembered it fails on the
day the corpora are swapped.

**And the same principle in one line, which is where all three prompts land:** the artefact that
survives contains references, and the text exists only in transit. `mapping.yaml` is type labels,
vocabulary members and bases. It has no transit.
