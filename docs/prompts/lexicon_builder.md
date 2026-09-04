# LexiconBuilder prompt — `lexicons/{lang}/`

> **This file is part of the frozen window, from `port-multi` onward.** `WINDOW_FILES` gains
> `profiler.md`, `mapper.md` and `lexicon_builder.md` in the commit that implements this arm.
> `port-oneshot` and `port-loop` never call the LexiconBuilder and their freeze records do not name
> this file; those records are not re-hashed (DESIGN §5.5, §6.3).
>
> **The LexiconBuilder is called once per arm, before iteration 1, as the third of three
> out-of-loop calls** (Profiler → Mapper → LexiconBuilder; `config/naming.yaml`, axis
> `agent_role`). Its artefact is `paths.armlexicon` —
> `results/{corpus}/{detector}/{supervision}/{porting}/lexicons/{lang}/` — a directory of
> `{name}.txt` files, four arm axes and no `{iteration}` component.
>
> **This is the one of `port-multi`'s three artefacts that enters detection, and the one that is
> deny-listed.** DESIGN §6.7.1: the Profiler configures the load and the Mapper is recorded and not
> applied, so if this arm's leak rate differs from `port-loop`'s, almost all of the difference is
> this file. `config/naming.yaml` explains the deny at `paths.armlexicon`: a lexicon is a list of
> institution, region and department names and nothing else, and **the screener cannot tell from
> the path whether it was built from a public gazetteer or from dev text.** Both arrive by the same
> route. Nothing in this prompt weakens that classification, and §3 does not try to argue its way
> out of it the way `profiler.md` §3 legitimately can.

The LexiconBuilder is one agent with one artefact: the term lists a `gazetteer` rule can reference
(DESIGN §3, `paths.armlexicon`). It does not detect, does not score, does not write rules — a
lexicon is referenced by a rule the RuleAuthor writes — and does not decide when to stop.

**Read §5 before §2.** Whether this artefact is a general lexicon or a transcription of the fold
the agent read is the question the arm turns on (DESIGN §6.7.3, §6.7.5), and §1 answers it in a way
that makes most of §2 follow.

---

## 1. Input — no corpus text, and the reason is structural rather than chosen

Three blocks and nothing else. No document, no span, no error list, no score, no count.

### 1.1 There is nothing corpus-derived that could be shown

**This call happens before iteration 1.** At that moment no rule file exists, no detection has run,
no span has been scored, `errors.jsonl` does not exist, and nothing has been masked because nothing
has been detected. **So the corpus-derived inputs an agent might want are not being withheld — they
have not been produced yet.** This is the same structural fact that gives the Profiler its zero
exposure, and here it decides the arm's central question rather than one field's.

Raw dev documents *could* be shown, and are not. Three reasons, in order of weight:

- **It would make this artefact a dev transcription by construction**, which is the thing DESIGN
  §6.7.3 predicts will fail to transfer and §6.7.5 wants to be able to rule out. An arm that shows
  the builder dev text has answered its own question in advance and in the wrong direction.
- **It would be the largest unmasked corpus exposure in the repository.** `auditor.md` §6 measures
  its prompt at about 210k tokens per dev round of *masked* text. Raw dev documents are unmasked by
  definition, and the artefact they would feed is the one file the screener denies.
- **It would destroy the portability property.** `port-loop` is unavailable under a DUA that keeps
  text on the machine (`auditor.md` §6). The Profiler and the Mapper survive that restriction
  because they see 0 characters; showing this agent dev text would make the LexiconBuilder the
  third of three to fail it, and `port-multi` would have no portable part at all.

### 1.2 What is shown

- **The language list**, from `config/naming.yaml`'s `corpus_rule_langs` for this corpus — `[es]`
  for `es-meddocan`, `[es, cat]` for `es-carmen`. The agent writes one lexicon set per language in
  one call (§1.3).
- **The canonical types a gazetteer can serve**, with their `phi_type` glosses: `ORGANISATION`,
  `LOCATION_AREA`, `LOCATION_STREET`. Not the full ten. `NAME` is excluded deliberately and §3
  states why; the rest are not gazetteer-shaped and a type list longer than the artefact can serve
  invites entries with nowhere to go.
- **The three declared lexicon names**, from a new `lexicon_name` vocabulary: `institutions`,
  `regions`, `departments`. These are DESIGN §3's own three words for this artefact.

**Not shown: the profile, the mapping, or the corpus's own type labels.** The Mapper needs the type
inventory because it maps it; this agent writes term lists against canonical types, and a corpus
label like a Spanish source type name would be a string it has no use for and could emit.

### 1.3 One call, all languages

One call per arm, `llm_calls` 1, charged to `role: lexicon_builder` (DESIGN §5.5). **All of a
corpus's languages in the one call, not one call per language**, because `corpus_rule_langs`
documents that a multilingual corpus's languages co-occur *within* documents and every rule file is
loaded for every document (DESIGN §5.2). Two independent calls would produce two lexicons with no
view of each other and duplicate entries across them, and the duplicate would be invisible: the
`duplicate_entry` check in §2.3 is within a file.

---

## 2. Output — the term lists

### 2.1 From the agent

One JSON object. **Emit the JSON and nothing else. No code fence, no triple-backtick line, no
`json` language tag, no preamble, no closing remark.** The first character of the response is `{`
and the last is `}`.

**This document shows no example of that object.** The schema is described below in prose, and §2.4
says why that is the form the specification takes here.

**Two keys at the top level, `lexicons` and `unresolved`, and no others.** `lexicons` is nested
three deep and the three levels are easy to confuse, so they are named one at a time:

1. **`lexicons` takes an object whose keys are language codes** — members of the `lang` axis, one
   per rule language of this corpus, all of them in this one response.
2. **Each language's value is an object whose keys are lexicon file names** — members of the
   `lexicon_name` vocabulary. Only the names you have content for: a name you would leave empty is
   left out rather than written empty.
3. **Each file name's value is an object with exactly two keys.** `basis`, one member of the
   `lexicon_basis` vocabulary, saying what kind of knowledge the list is. And `entries`, a list of
   strings — the terms themselves, one per element, no comment syntax, no `#`, no newline inside a
   term.

**`unresolved` takes a list of strings, and each string is a language code and a file name joined
by a forward slash** — the language first, then `/`, then the name, matching the `lexicon:`
reference form a rule file uses for the same pair. Every entry must name a pair that is present in
`lexicons`; the file is still written and the list says the content is a guess. An empty list is a
claim: it says every list you wrote is one you stand behind.

The permitted languages, file names and bases are appended to this prompt from
`config/naming.yaml` as it stands for the run, with their glosses, and are not listed in this file
— so the specification cannot drift from the axes it describes.

**Every key and every `basis` is a member of a closed vocabulary declared in
`config/naming.yaml`.** Languages come from the `lang` axis, file names from `lexicon_name`, bases
from a new `lexicon_basis`. `lexicon_name`, `lexicon_basis`, `lexicon_refusal` and
`lexicon_unresolved` are added to that file by the commit that implements the validator — not
coined in the validator, and not coined here.

**`entries` is the one field that holds strings this repository did not author**, and that is not
an oversight in the schema. It is what a lexicon is. §3 is about what follows.

**There is no free-text field, and the file format's own free-text affordance is closed in §2.2.**
Not `note`, not `comment`, not `source`, not `rationale`. `unresolved` names `{lang}/{name}` pairs
the agent could not fill confidently; the file is still written and the list says the content is a
guess.

### 2.2 The files, from the orchestrator

`paths.armlexicon` — `results/{corpus}/{detector}/{supervision}/{porting}/lexicons/{lang}/`, one
`{name}.txt` per declared lexicon, **one term per line and no comment lines.** `src/rules.py`'s
`_read_lexicon` fixes the format: `lexicons/{lang}/{name}.txt`, one term per line, `#` starts a
comment, name matches `[a-z0-9_]+`.

**The agent does not write these files and this is the one of the three artefacts where "written
through unchanged" does not apply.** The reason is specific: **the `.txt` format has a prose
channel the JSON schema does not.** A `#` comment line is arbitrary text in the artefact the
screener denies, and it is exactly the field §2.1 refuses to give the agent. So the agent returns a
validated object with no comment concept in it, and the orchestrator serialises terms only. A term
containing `#` or a newline is refused (§2.3) rather than escaped, because escaping would let the
character into a file whose reader treats it as structure.

Alongside the directory, one `lexicon_manifest.json` at the arm root, carrying what the `.txt`
files cannot:

Five keys, and the agent writes none of them. `corpus` and `porting`, the two axes that say which
cell wrote it. `files`, an object keyed by the same `{lang}/{name}` pairs, each value carrying that
file's `basis`, its `entries` **count**, its `refused` count, and an `unresolved` boolean. `refused`,
a list whose entries carry a `file` and a `reason` from §2.3's table and **not** the rejected
string. And `counts`, with `files`, `entries`, `refused`, and a `by_refusal` breakdown keyed by
reason.

A worked instance is not shown here for §2.4's reason; `docs/prompts/examples/` holds one for a
reader, outside this file and outside the call.

**The manifest carries no entry text**, only per-file counts and bases. A refusal records the file
and the reason and **not the rejected string** — a rejected entry is a surface form of unknown
provenance and putting it in a second file would double the artefact's exposure to buy a debugging
convenience, which is the trade CLAUDE.md rules out for exception messages and rules out here for
the same reason.

### 2.3 What the validator refuses, and why a refusal here does not stop the arm

| refusal | what it catches |
|---|---|
| `undeclared_value` | a language, lexicon name or basis outside its vocabulary |
| `unknown_field` | a key not in §2.1's schema — including any prose key |
| `missing_field` | a declared file without `basis` or without `entries` |
| `malformed` | not one JSON object; `entries` not a list of strings |
| `duplicate_entry` | the same term twice in one file, case-folded — `src/rules.py` matches under case folding, so two casings are one term |
| `entry_contains_newline` | a term that would become two lines |
| `entry_contains_comment_mark` | a term containing `#`, which the reader would truncate |
| `entry_too_short` | a term under 3 characters. A one- or two-character gazetteer term matches everywhere; `city_name_gazetteer` already ran at 0.621 precision with ordinary place names |
| `entry_is_vocabulary_term` | a term equal to a canonical type name, a lexicon name or a layer name — the agent echoing its instructions into the artefact |
| `empty_lexicon` | a declared file with no surviving entries. `_read_lexicon` raises on an empty file at load, so this is caught at write time instead of failing the arm mid-run |

**Refused entries are dropped, counted, and the arm continues.** This diverges from
`profiler.md` §2.3 and `mapper.md` §2.3, which stop the arm on any refusal, and the divergence is
about what each artefact does rather than about how much each is trusted:

- A partial profile is not a degraded profile, it is no profile — it configures the load, and a
  missing `bom` has no default. A partial mapping silently drops gold spans.
- **A lexicon with a term missing is a lexicon with a term missing.** The consequence is a
  detection this arm does not make, which lands in the leak rate and is *reported*. It is the one
  of the three artefacts whose degradation is already visible in the arm's headline number, so it
  does not need a gate to make it visible.

Two things still stop the arm: `malformed` at the top level, because then there is no artefact; and
a `lexicons` object naming no language in `corpus_rule_langs`, because a lexicon under a language
no rule file is loaded for is unreadable by construction. **A run in which every file came back
empty does not stop the arm** — it is recorded in the manifest, and DESIGN §6.7.4's cause 1 is what
reads it.

**No cap is set on entry count**, and the reason is §6.7.6's: there is no prior for how many
institution names a lexicon should hold, and a cap chosen now would be a number with no argument
behind it. `counts.entries` is reported instead, and precision is measured per rule.

### 2.4 Why this file contains no example, and no fenced block of any kind

**This prompt is sent to the model verbatim.** `assemble_lexicon_prompt` joins this template, the
frame of languages, types, file names and bases, and one closing instruction; the template is not
summarised, excerpted, or stripped on the way. So every block in this file reaches the agent, and a
path this file merely names does not.

**The first run of `port-multi` on `es-meddocan` never reached this call, because the Profiler's
response wrapped its object in the fence that prompt used to quote its own example**
(`docs/notes/arm-port-multi-es.md`). That was the second occurrence in this repository:
`port-oneshot`'s first run wrapped its YAML the same way, and the fix then was one file's wording
plus a sentence saying the fence was that document's quoting convention. **This file never even
carried that sentence** — it inherited the fenced example without the mitigation, which is what a
convention fixed in one file's prose rather than in a check comes to.

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

**One extra caution for this prompt, because its example held content rather than shape.** The
removed block used `LEXICON_ENTRY_A`-style placeholders and said in prose that they were not
proposed content. A model that copied the demonstration here would have emitted placeholder terms
into a real gazetteer, which is a worse failure than an unparseable response: it loads, matches
nothing, and reports a lexicon with entries.

---

## 3. Prohibition — the artefact is surface forms, so the rule cannot be "no surface forms"

**`profiler.md` and `mapper.md` both close on one line: the artefact that survives contains
references, and the text exists only in transit. This artefact breaks that line, and pretending
otherwise would be the most dangerous sentence in these three documents.** A lexicon is surface
forms. There is no version of it that is offsets, vocabulary members and field paths.

So the prohibition is not "emit no surface form". It is **emit no surface form taken from the
corpus**, and it holds for a reason that is not compliance:

- **The agent is shown no corpus text (§1), so it cannot take one.** The constraint is discharged
  by the input, not by the instruction. This is the strongest form the guarantee can take and it is
  worth being precise about what it covers: entries cannot be *copied* from the corpus. Whether an
  entry independently *coincides* with a corpus surface is a different question, unavoidable — a
  lexicon of Spanish provinces will coincide with any Spanish corpus, and that coincidence is the
  artefact working — and §5 is where it is measured rather than assumed away.
- **The screener cannot check this and is not asked to.** `config/naming.yaml` states the reason
  the path is denied: a lexicon built from a public gazetteer and one built from dev text arrive by
  the same route, and where provenance cannot be read from the path, the direction is deny. **This
  prompt does not change that classification and no future version of it should try.**
  `profiler.md` §3 could argue `profile.json` into the sniffed category because every field there
  is an enumerated value or a field path. That argument does not exist here.
- **`basis` is the declared provenance, and it is a claim rather than a proof.** It is the only
  machine-readable statement about where entries came from, §5 cross-checks it against measured
  transfer behaviour, and it is not evidence on its own.

### Other prohibitions

1. **No person names, and `NAME` is not a gazetteer target here.** The declared lexicon names are
   `institutions`, `regions`, `departments` (DESIGN §3) and `lexicon_name` has no fourth value. A
   list of real people's names is a categorically more dangerous artefact than a list of hospitals,
   it would be indistinguishable from a re-identification resource, and `port-loop` served `NAME`
   at 0.935 recall with nine `context_cue` rules and no gazetteer at all — so there is no recall
   argument for one either. `undeclared_value` refuses the file name.
2. **No entry from any corpus document**, and none is reachable (§1.1). If a path under `data/` or
   `sealed/` appears in this agent's context, that is a harness bug to report and not a resource
   (DESIGN §6.1).
3. **No comments, no prose, no per-entry annotation.** §2.2 closes the `.txt` channel by having the
   orchestrator write the files. The agent has no field for prose and the file has no line for it.
4. **Only `config/naming.yaml` vocabulary** for languages, file names and bases. A fourth lexicon
   kind this corpus needs is added to that file by a human in a commit — never coined in an
   artefact. The correct behaviour when no declared name fits is `undeclared_value`.
5. **No rules.** A lexicon is referenced by a `gazetteer` rule the RuleAuthor writes. This agent
   does not write the rule, does not choose the `phi_type` the rule will carry, and does not know
   whether any rule will reference its file at all (§4).
6. **No claim about any fold.** The agent is shown no fold, no split and no count.

---

## 4. How the lexicon is consumed — and two things that are true today

### 4.1 The loader could not read an agent-authored lexicon — fixed 2026-09-01, before the first call

~~`src/rules.py`'s `_read_lexicon` resolves a rule's `lexicon:` reference through
`path_template("lexicon")`, which is the **human** key `lexicons/{lang}/` — not `paths.armlexicon`.
So as the code stands, a rule in a `port-multi` rule file that takes the `lexicon` form would read
the human directory, and **that directory is empty on disk.** The implementing commit owes the
redirection: an arm that has its own lexicon resolves against `paths.armlexicon`, and an arm that
does not keeps today's behaviour.~~ **Recorded here because it is the difference between this arm
having a live third artefact and having an unread one**, and because DESIGN §6.7.4's cause 1 — "the
lexicon was never read" — had two possible mechanisms and this was the one that would be our bug
rather than the agent's outcome.

**What was done instead of the redirection, and why it is not the same fix.** The plan above was
"resolve against `paths.armlexicon` for an arm that has a lexicon, keep today's behaviour for an arm
that does not", and that is two defaults where the text asked for one correction. `_read_lexicon`
now takes the collection **from its caller** and refuses a `lexicon:` rule when none was named:
`human_lexicon_root()` is the hand-written lists, `arm_lexicon_root()` is an arm's, and neither is
automatic. The reason is §3's, one artefact over — on the day `lexicons/es/` is not empty, "keep
today's behaviour" reads the human lists into an agent-labelled result, and that is exactly the
substitution `profiler.md` §2.3 and `mapper.md` §4 refuse for the other two artefacts. A rule file
that lists its terms inline never reaches the refusal, which is why every arm frozen before this
date loads identically (`tests/test_rules.py::test_no_frozen_arms_rule_file_needs_a_lexicon_collection`).

`lexicons/` being empty is consistent with DESIGN §4: `port-loop` took the `lexicon` form zero
times across eight rule files, so this artefact form has never had an instance in this repository.
`port-multi` produces the first one.

### 4.2 The RuleAuthor's inline `terms` stay open, and that is the channel this arm does not close

`src/rules.py` gives a `gazetteer` rule two forms: `terms:` (a literal list inside the rule file)
and `lexicon:` (a file reference). **`port-multi` adds the second and does not remove the first.**

`port-loop`'s `ORGANISATION` detection was two `terms` gazetteers written by the RuleAuthor from
dev errors — `hospital_nombre_gazetteer` and `pharma_device_gazetteer` — and DESIGN §6.7.3's
measured +18.35pp dev→test leak-rate move is that artefact's transfer behaviour. **The agent that
can transcribe dev into a gazetteer is therefore the RuleAuthor, not the LexiconBuilder**, and it
still can in this arm: the RuleAuthor is the one agent here that reads dev errors, and its prompt is
frozen and unchanged (`rule_author.md`).

Two consequences, both pre-registered rather than discovered:

- **Attribution requires reading which form each rule takes.** If `port-multi`'s `ORGANISATION`
  improves on dev, the improvement belongs to the LexiconBuilder only for rules that take the
  `lexicon` form. `by_rule` carries `rule_id` and `layer` but not the form, so the check is against
  the rule files themselves. DESIGN §6.7.4's cause 2 check is adjacent and not the same one.
- **A prediction about the LexiconBuilder's own artefact, in the other direction from §6.7.3's.**
  A lexicon built from parametric knowledge cannot be fold-specific, so it should transfer dev→test
  evenly — and on `es-meddocan` it may match almost nothing in either fold, because MEDDOCAN is
  synthetic and its institution names are invented surrogates rather than real Spanish hospitals.
  **The likely outcome for this artefact is low recall with even transfer, not high recall that
  collapses.** `fires` and `tp` per lexicon-backed rule are what report it, and a lexicon with 0
  fires is the null result to state plainly rather than to explain.

### 4.3 What is not affected

Nothing in `rule_author.md` or `auditor.md` changes. The lexicon reaches the loop through
`src/rules.py`'s resolution of a form the rule schema already has, and `rule_author.md` §1.1
already documents both gazetteer forms. Editing a hashed file would put every earlier arm's freeze
record into drift for no gain (`docs/notes/window-freeze-history.md`, DESIGN §6.3).

---

## 5. Transcription versus generalisation — what is settled, what is measured, what is not

DESIGN §6.7.5 asks whether "transcribed dev names" and "a general lexicon" are distinguishable. For
this artefact the answer is better than that section first stated, and for the arm as a whole it is
worse. Both halves belong here.

**Settled by construction, for this artefact.** The LexiconBuilder never sees dev (§1.1), so its
entries cannot be dev transcriptions. This does not need measuring and no measurement could
strengthen it — it is a property of the input, like "the Profiler never sees corpus text".

**Measured anyway, as a check on the construction rather than on the agent.** Per lexicon file, the
fraction of entries that match some dev surface, computed dev-side and needing no seal opening. A
high fraction is *not* evidence of transcription — Spanish provinces appear in every fold, which is
`administrative_enumeration` working exactly as declared. It is a check on the harness: a file with
near-total dev overlap, an open-set basis, and entries a general lexicon would be unlikely to hold
is a signal that dev text reached the call, which is §3's prohibition 2 failing and a bug to
report. **The measurement exists because the guarantee is structural, and a structural guarantee is
worth a tripwire.**

**`basis` × transfer is the cross-check that gives §6.7.5 teeth.** A declared basis predicts
transfer behaviour, so the declaration is falsifiable:

| basis | the claim | predicted fires retained, dev → test |
|---|---|---|
| `administrative_enumeration` | a closed official list (provinces, autonomous communities) | high — a complete closed set has no tail to miss |
| `morphological_class` | the productive naming pattern's head words, not instances | high — a class is fold-independent |
| `general_knowledge_named_entities` | real-world named institutions the agent knows | **not predicted** — an open set, and on a synthetic corpus possibly near zero in both folds |

A file declared `administrative_enumeration` whose retention is low has a falsified declaration, and
that is reportable as a finding about the agent's self-description independent of the arm's leak
rate. The comparison figure is `port-loop`'s measured pair — `hospital_nombre_gazetteer` retaining
46.5% of its fires against `city_name_gazetteer`'s 93.0%, at unchanged precision (DESIGN §6.7.3).

**Aggregate only, from the single sealed opening.** The count of entries with at least one test-fold
match, and the fires-retained ratio. **Per-entry test counts are not recorded**: an entry is an
institution or place name, and per-entry test fires crossed with entry names is a map of which
institution names appear in the sealed fold. Counts and ratios leave the sealed run; entry-level
detail does not.

**Not distinguishable, and this is the limit.** Two things:

1. **Whether a non-transcribed lexicon generalises well, or merely happens to overlap.** Both
   produce high retention, and separating them needs a fold the lexicon's author had no relation
   to — which every fold is here, so the question is really whether the *corpus* and the agent's
   parametric knowledge overlap. That is a fact about MEDDOCAN, not about the agent.
2. **Whether the RuleAuthor's inline `terms` are dev transcriptions.** They are written from dev
   errors, so in the relevant sense they are, and `port-loop` measured what that costs. **This arm
   does not close that channel** (§4.2), so `port-multi`'s gazetteer behaviour is a mixture of one
   artefact that provably cannot transcribe and one that provably can, separable by rule form and
   not by any measurement of the lexicon alone.

**So the honest statement is narrower than "the arm measures whether agent-authored lexicons
generalise".** It measures whether an agent asked for a lexicon without corpus access produces one
that fires at all, and it measures the declared basis against observed transfer. The
transcription-versus-generalisation contrast that §6.7.3's prediction is about lives in the
RuleAuthor's `terms`, which this arm inherits unchanged.

---

## 6. Tools — none, and here the refusal has a cost worth naming

- **No network, no search.** This is the one of the three prompts where a tool would plainly help:
  a search would return real institution lists and raise this artefact's recall. It is refused
  because an entry's provenance would become unverifiable-but-plausible — §3's whole guarantee is
  that entries cannot be corpus-copied *because the agent has no channel*, and a network is a
  channel whose contents nothing in this repository records. It would also make the artefact depend
  on a resource the frozen window cannot hash (DESIGN §6.3), and `lexicon_basis` would need a
  fourth value naming a source that is not reproducible. **The cost is that this lexicon is bounded
  by parametric knowledge, which is a recorded limitation and part of why §4.2 predicts low
  recall.**
- **No corpus access, no `data/`, no `sealed/`.** §1.1's guarantee is the tool list being empty.
- **No filesystem.** It cannot read `lexicons/`, the human directory it is replacing — which is
  empty anyway, and if it were not, reading it would make this a copy task.
- **No `results/`.** Another arm's lexicon is a previous call's answer, and §5's basis cross-check
  would be measuring agreement between two calls of this prompt.
- **No gold, no `rules/`, no `splits/`. No writing.** The files are serialised from the return
  value (§2.2).
- **No other agent.** The orchestrator sequences the three out-of-loop calls; there is no manager
  agent (DESIGN §3).

---

## 7. The filled prompt is never written down

Same rule as the other three prompts: only this template is committed, and a filled instance is not
committed, not logged, and not written to disk.

**The asymmetry here is the reverse of every other prompt in this repository, and it is worth
stating as the closing fact rather than leaving it to be noticed.** `auditor.md` §6 has a large
input and a small artefact: about 210k tokens of masked dev text in transit, and offsets and types
surviving. The Profiler and the Mapper have a small input and a small artefact — counts, schemas,
type labels — and both close on the line that the text exists only in transit. **This prompt has
the smallest input of the four and the most sensitive artefact.** Zero characters of corpus text go
in; a list of institution, region and department names comes out and is written to a path the
screener denies.

Two consequences:

- **This prompt is available on every corpus, including one whose text may not leave the machine.**
  The agent is shown 0 characters by design, so the DUA restriction that makes `port-loop`
  unavailable (`auditor.md` §6) does not reach the call. It reaches the *artefact*: the file is
  deny-listed, so it stays local, and a corpus under such a DUA can run all three out-of-loop calls.
- **Caching is not used for this call and the block is absent from `metrics.json`.** One call per
  arm has no second call to read a cached prefix, and absence is recorded as the block's absence
  rather than as a value (`config/naming.yaml`).

**The rule does not branch on which corpus is running.** MEDDOCAN and GraSCCo are synthetic and are
not exceptions (CLAUDE.md): a discipline that holds only where someone remembered it fails on the
day the corpora are swapped. That applies with unusual force here, because the artefact this prompt
produces is one whose risk a reader can only assess from its provenance — and provenance is exactly
what a path cannot show.

**So the one line the other prompts close on does not close this one.** Their artefacts contain
references and the text exists only in transit. Here nothing is in transit and the artefact is the
text. The guarantee is not that the surface forms are safe; it is that they did not come from the
corpus, and that guarantee rests on the input being empty and on nothing else.
