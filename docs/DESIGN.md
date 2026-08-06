# DESIGN

Decisions already made, and the reasoning behind them. Read this before
proposing changes to the pipeline, the agents, or the experiment matrix.
If a decision here turns out to be wrong, change it here first — do not
work around it in code.

Last updated: 2026-08-06

---

## 1. What the project claims

A de-identification pipeline whose four stages are language-independent, and
whose language-specific parts are produced without any new manual annotation.

The claim is deliberately *not* "language agnostic". The rules, gazetteers,
and encoder are per-language instances. What is portable is the recipe, and
the measurable claim is:

> a new language or note type can be supported with N hours and zero
> annotated spans, reaching leak rate X

*Porting* means producing those language-specific instances. The term is used
in the software-engineering sense.

---

## 2. The pipeline being ported

| Stage | Function |
|-------|----------|
| 1. Layered detection | Checksum-validated rules, context cues, gazetteer, and a learned span tagger each emit typed spans |
| 2. Recall-first merging | Overlap clusters collapse to the union of their boundaries; the type comes from the longest span, then a priority ranking |
| 3. Pseudonymization | One date offset per document preserves intervals; surrogates stay consistent within a document; high-risk identifiers become type tags |
| 4. Annotation-free supervision | Placeholder positions in an already de-identified corpus supply the training labels |

Rationale for stage 2: a missed identifier is a disclosure, whereas an
over-detection costs only utility. Because the merge is a union, the combined
configuration dominates its components on recall **by construction** — so F1
alone cannot be the headline metric. See §5.

Rationale for stage 4: annotating clinical text requires the same protected
access that de-identification is meant to grant. Corpora with placeholder
markers are common; annotated corpora are not. Substituting a surrogate per
placeholder yields offsets that are exact by construction and free of
inter-annotator disagreement.

---

## 3. Agents

**An agent is defined by the file it produces, not by a persona.**
One agent, one output file. An agent that produces no artifact does not exist.
Two agents never write the same file.

| Agent | Produces |
|-------|----------|
| Profiler | `profiles/{corpus}.json` — format, offset convention, type inventory, group key |
| Mapper | `mappings/{corpus}.yaml` — corpus taxonomy → canonical type set |
| LexiconBuilder | `lexicons/{lang}/` — institutions, regions, departments |
| RuleAuthor | `rules/{lang}.yaml` — iterated against dev |
| Auditor | `reports/leaks_{iter}.json` — suspected residual PHI |

**The orchestrator is deterministic code**, not an agent. Execution order,
retries, and budget live in `src/orchestrate.py`. No manager agent, no planner
agent — an agent that only thinks costs money and produces nothing.

**The Auditor never sees gold.** It reads only the de-identified output and
flags spans that look like surviving PHI. This lets the same component serve
two roles: build-time feedback to the RuleAuthor, and the runtime component of
the `RT-Aud` arm. Scoring against gold is done by a separate deterministic
scorer. Agents and scoring are never mixed.

**Loop termination** is explicit: dev leak rate improves by less than δ for k
consecutive iterations, or the call budget is exhausted. Never "until it looks
good enough".

### Span provenance

Every detected span carries **layer · detector · rule ID · score**.

`layer` is one of `regex_checksum` · `context_cue` · `gazetteer` · `tagger`, read
from the `layer` axis in `config/naming.yaml`. It is **not derived from the detector
name** — no prefix convention, no substring match, no lookup table built by hand.
The detector that emitted the span sets the field explicitly. Deriving it would make
a rename silently re-attribute results, which is the same failure mode the naming
rules in §4 exist to prevent.

`layer` is not the `detector` axis. `detector` names an experimental arm and appears
in the result path; `layer` names the mechanism that produced one span and never
appears in a path. A single arm spans several layers — `R` covers three of them.

**`layer` is fixed before the first span exists**, deliberately. Adding it after
a run would leave already-emitted spans unattributed and force every arm to be
re-run. It is what makes §7's per-layer prediction measurable: the complementarity
breakdown in §5 is a rules/tagger dichotomy and cannot say whether a loss came from
context cues or from regexes, but the same detections grouped by `layer` can.

**Agents do not get a `layer` value.** `RT-Arb` and `RT-Aud` do not create spans —
the arbiter drops or retypes spans that already exist, and the auditor flags
suspected residual PHI in output. Giving them a layer would put a filtering step in
a field that means "what produced this", and would make per-layer recall sums stop
reconciling. Agent involvement is recorded separately, in an
`agent_actions` list on the span: each entry names the agent, the action
(`kept` / `dropped` / `retyped` / `flagged`), and the call ID in
`agent_calls.jsonl`, so an arm's spans can be replayed with and without agent
intervention from one file.

A list rather than a single field because `RT-Arb-Aud` runs both agents over the
same span. Two alternatives were considered and rejected: a single
`agent_action` string cannot represent that arm, and separate `arbiter_action` /
`auditor_action` fields would need a new column per future agent.

**`rule_id` carries the rule file's language as a prefix**: `es:doctor_prefix`,
`cat:doctor_prefix`. Two files can define the same rule name, and after the §5.2
decision a corpus can load several at once, so an unqualified name is ambiguous about
which file produced a match. The prefix is a value from the `lang` axis in
`config/naming.yaml` — the language of the *file*, not of the corpus or the document.
It is what makes the precision cost of loading an extra rule file a measured quantity
rather than an accepted unknown: a false positive is attributable to the file that
produced it.

**Adding that prefix does not break §7's per-layer attribution.** `layer` and
`rule_id` are independent fields answering different questions.
`es:doctor_prefix` and `cat:doctor_prefix` both carry `layer: context_cue`, so the
per-layer grouping §7 depends on — and its reconciliation — are unaffected by how many
files contributed spans. `rule_id` cuts *within* a layer, by language and by individual
rule, which §7's prediction does not need and §5.2's accounting does. What is not
permitted is deriving either field from the other, or from the detector name.

**Start with three.** Profiler, RuleAuthor, Auditor. Add Mapper and
LexiconBuilder only if the ablation shows the first three leave something on
the table. Building five at once makes it impossible to tell which one works.

### Prior evidence that shaped this

An earlier project (surro) used three LLM roles — translation, ContextJudge,
Critic. The Critic contributed nothing measurable to safety or naturalness
while costing 1.9× budget and 2.5× wall time, and was demoted. Diagnosis: those
were single-shot calls in a fixed workflow with no tools, no state, and no
feedback path — a workflow with LLM steps, not agents. The agents here differ
on all four counts: unbounded call count, tool use (running detection on dev),
accumulated state (`rules/{lang}.yaml`), and a feedback path that actually
changes the artifact.

---

## 4. Experiment axes

Identifiers are defined in `config/naming.yaml` and nowhere else. Names are
derived from configuration; ordinals (`arm1`) and status words (`best`,
`final`, `deployed`) are forbidden — both caused sustained confusion in surro.

```
corpus       ko-surro · es-meddocan · de-grascco · es-carmen · en-n2c2
detector     R · T · RT · RT-Arb · RT-Aud · RT-Arb-Aud
supervision  sup-free · sup-human
porting      port-human · port-oneshot · port-loop · port-multi · port-selfdesign
```

The path is the identifier:

```
results/{corpus}/{detector}/{supervision}/{porting}/metrics.json
```

The porting axis is a ladder of autonomy. Two comparisons carry the paper:

- `port-loop` vs `port-oneshot` — does iteration justify calling this agentic?
- `port-multi` vs `port-loop` — does role specialisation justify *multi*-agent?
- `port-selfdesign` vs `port-multi` — can role design itself be delegated?

If `port-loop` does not beat `port-oneshot`, the agentic framing is not earned.
That is a real possible outcome and the experiment is designed to detect it.

---

## 5. Metrics

Headline quantities:

- **Leak rate** — share of gold PHI spans not fully covered by some prediction.
  This is the only operationally meaningful number; a missed identifier is a
  disclosure, and a partially redacted one can still be. The looser
  any-overlap variant is reported beside it as a lower bound. Exact definitions
  and the reason `fully_covered` is the headline are in §9.3.
- **Complementarity breakdown** — found by rules only / tagger only / both /
  neither. This is what shows whether the neural layer earns its cost.

Both are computed against the **union** of same-type predictions rather than against
a one-to-one assignment, for reasons given in §9.3 — coverage answers "is this
identifier hidden", which does not depend on which detector gets the credit, and a
one-to-one matching would undercount `both` exactly where the layers agree.

Precision, recall, and F1 are reported but are not the headline, for the
reason in §2.

Per-arm cost is reported alongside quality: LLM calls, tokens, wall time.
A quality gain that costs 2× is a different result from one that costs 1.05×.

### 5.1 Reporting granularity: aggregates alone cannot carry a porting claim

Both headline quantities are reported **per canonical type as well as in
aggregate**, and every cross-corpus comparison is accompanied by a figure computed
on the types the two corpora share. Three rules, then the measurement that forces
them:

- **Report the complementarity breakdown and the leak rate per canonical type
  (§9.0), not only as corpus totals.** Types with n ≤ 8 are omitted from the
  per-type view under §9.4 while staying in the totals; the omission and the
  omitted count are stated so the rows visibly do not sum.
- **Alongside any cross-corpus comparison, report the same quantities restricted to
  the canonical types both corpora observe.** State which types the restriction drops
  and what share of each corpus's gold they carry — the cost is typically asymmetric,
  and hiding it would replace one distortion with another. When the restriction drops
  nothing, say so: that is a fact about the pair, not a clean bill of health.
- **Do not claim porting difficulty from an aggregate comparison alone.** A
  difference in corpus totals is not evidence about language or note type until the
  type mix is held fixed.

**Why this is not defensive over-reporting.** MEDDOCAN and CARMEN-I are both
Spanish, both clinical, and share every type name — so a naive reading treats a gap
between them as a language-held-constant measurement of note-type or corpus
difficulty. Their type distributions make that reading unsafe. Measured at the canonical level of
§9.0, after the §9.1 exclusions and leaving out the two CARMEN-I types §9.0 does not
yet place (`NUMERO_IDENTIF` 227, `URL_WEB` 1):

| canonical type | MEDDOCAN (20,538) | CARMEN-I (7,246) |
|---|---|---|
| `DATE` | 12.5% | **74.3%** |
| `CONTACT` + `ID` — email, fax, phone, every ID subtype | 20.0% | **0.5%** |
| `NAME` | 19.5% | 2.1% |

MEDDOCAN is synthetic case reports whose generator inserted administrative blocks;
CARMEN-I is authentic hospital narrative, where those elements are simply not
written down. The consequence is arithmetic, not speculative: **a detector that
found nothing but dates, perfectly, would score 12.5% recall on MEDDOCAN and 74.3%
on CARMEN-I** — 62 points of difference with detector quality identical by
construction. Symmetrically, the two types regex and checksum rules are best at
carry 20.0% of MEDDOCAN's spans and 0.5% of CARMEN-I's, so a rule set whose strength
lies exactly there has almost nothing left to find in CARMEN-I.

**And the common-subset rule does not rescue this pair — which is why per-type
reporting is the load-bearing requirement.** All ten canonical types are observed
with nonzero counts in both corpora, so restricting to the shared set drops nothing
and changes no number. The confound lives *inside* a shared type set, as a difference
in the mixing weights rather than in which types exist. The common-subset rule earns
its place for pairs where types genuinely are absent — GraSCCo has no `OTHER`,
CARMEN-I has no patient-name gold at all — but a corpus pair can pass that check and
still be incomparable in aggregate. Passing it is not evidence of comparability.

An aggregate leak-rate gap between these two corpora therefore mixes at least three
effects — porting difficulty, corpus authenticity, and type mix — and the type-mix
term alone is large enough to produce a gap of the size the experiment is trying to
detect. Only the per-type view separates them.

This interacts with the per-layer prediction in §7: the layer whose sensitivity to
orthographic realisation is **none** (regex/checksum) is precisely the layer whose
target types are 20.0% of one corpus and 0.5% of the other. Without per-type
reporting a type-mix shift and a realisation effect are indistinguishable in the
totals, and §7's prediction would not be testable across these two corpora at all.

The same discipline applies to every corpus pair, not just this one; MEDDOCAN and
CARMEN-I are documented here because they are the pair where the confound was
measured rather than anticipated.

**Documents with no PHI are not free.** CARMEN-I has 462 of them (461 in the
`replaced` variant), 23% of the corpus, at a median of 96 tokens — they are not empty
files. They contribute nothing to the leak-rate numerator or denominator, and they do
contribute false-positive opportunity, so they belong in a precision denominator and
not in a recall one. Two consequences for reporting: **a fold's effective size is not
its document count**, and **no per-document leak rate is defined for them** — a
per-document average over the corpus would be an average over 1,538 documents, not
2,000, and must say so. Counts are in `docs/notes/corpus-observations.md` §8.5.

### 5.2 Rule files are loaded per language, not selected per document

**Decision: load every rule file the corpus is configured for and take the union of
their matches. No language identification is performed** — not per document, not per
passage. `config/naming.yaml` holds the mapping (`corpus_rule_langs`): `es-carmen`
loads `[es, cat]`, `es-meddocan` `[es]`, `de-grascco` `[de]`, `ko-surro` `[ko]`.

The problem this settles: CARMEN-I mixes Spanish and Catalan **inside** 264 of its
2,000 documents (13.2%), with only 39 Catalan-only. A one-language-per-corpus
convention has no correct value to put in `{lang}` — `es` is wrong for 13.2% of
documents and `es+cat` is not a language code.

**Why the union, over the three alternatives.** The decisive criterion is that the
rejected options all introduce a component whose own error rate is invisible to the
metric set in §5. A per-document selector needs a tie-break for the 264 mixed
documents, and a sub-document segmenter needs boundaries; when either is wrong, the
resulting miss is scored as a detection failure, and nothing in the leak rate or the
complementarity breakdown separates "the rules lacked the pattern" from "the language
router sent the text to the wrong file". Adding an unmeasured component to answer a
question that can be avoided entirely is the worse trade. Treating Catalan as noise
was also rejected, for a different reason: it hides the failures of the Catalan
passages inside documents counted as Spanish, so the cost would land in the aggregate
where it cannot be seen.

**The cost is precision, and it is paid knowingly.** Loading both files means a
Catalan trigger word that is also a Spanish word can fire where it should not. That
cost is attributed through the `rule_id` prefix convention defined in §3, which also
records why adding the prefix leaves §7's per-layer attribution intact. The point of
the convention here is that the precision cost of adding `cat` is a quantity we
measure, not an unknown we accept.

One consequence is worth measuring but is **not** part of this decision: because
CARMEN-I ships a per-document language label, a per-document selector can be scored
against gold without putting a selector in the pipeline. Recorded as §10 A1.

---

## 6. Experimental integrity

- Split unit is the largest natural group available (patient where patients
  exist, document otherwise). Never record-level random split. What counts as
  a group is decided by identifier agreement, not filename structure — §9.5,
  which also records the per-corpus outcome.
- **Folds are stratified where an unstratified draw would confound composition
  with performance.** Stratification is a sampling constraint, not a grouping
  claim: it never links two units. CARMEN-I is stratified by document type and
  language because PHI density varies 4× across types and the bilingual
  documents concentrate in one of them (§9.5).
- `splits/{corpus}.json` is frozen and committed **before** any rule is
  written. The commit hash is the reference point. Where a corpus ships an
  official split it is kept as the primary result (§9.6); where none ships, as
  in CARMEN-I, the split is constructed here and there is no external
  comparability to inherit.
- The test fold lives in `sealed/` and is not read during development —
  by agents or by people. Rules developed while looking at test are the same
  leakage as training on test.
- Every evaluation on the sealed fold is appended to
  `results/sealed_eval_log.md` with date, commit hash, and purpose, so the
  paper can report how many times the test set was touched.
- The dev fold is where rules are developed, agents iterate, and checkpoints
  are selected.

### 6.1 The seal is physical separation *and* a code gate

Both, because either alone permits a bypass that leaves no trace.

**Physical separation alone.** The test fold sits in `sealed/`, outside the corpus
root, so ordinary code cannot reach it. But nothing stops someone pointing a loader
at that path — in a notebook, in a one-off script, in a rule-tuning loop — and the
only evidence it happened is that person's memory. The fold was read, no row
appears in `results/sealed_eval_log.md`, and the log now reads as a complete
account of a corpus that was looked at more often than it says.

**A code gate alone.** `load(sealed=True)` is accepted only from
`src/eval/run_sealed_eval.py`, checked by module identity on the call stack, and it
appends to the log before opening anything. But if the fold still sits under the
corpus root, a loader that never asks for `sealed=True` reaches it anyway: a glob
over fold directories, a re-release that restores a directory, a loader added for
another purpose. The gate is not bypassed, it is simply not on the path.

Together the two close each other's gap. The fold is somewhere ordinary code cannot
reach, and the one code path that can reach it records the fact first and refuses to
proceed if it cannot. A read therefore requires either a deliberate edit to a
committed file or a deliberate path in a script — both of which are visible in a
diff, which is the property that matters. Neither guard is redundant, and
`tests/mutations/` breaks each one separately to keep that true:
`sealed_callable_from_anywhere` disables the gate while the logging survives (a
bypass with a trace), `log_append_disabled` disables the logging while the gate
survives (a bypass without one).

Three further properties the implementation holds, each of which was a mutation
before it was a paragraph:

- **The seal is a path that is not known, not a filter that is applied.**
  `fold_roots()` is the single place that decides which folds are reachable, and it
  answers by returning paths rather than by loading everything and discarding the
  sealed part. There is no later step that could forget.
- **Fail-closed logging.** The append happens before the read and a failure aborts
  it. An unlogged evaluation is worse than none: the numbers are real and the log
  says the fold was never opened.
- **Not-yet-sealed is a distinct state.** `sealed_root()` returns `None` rather than
  falling back to the corpus root, so a `sealed=True` read of an unsealed corpus is
  refused instead of quietly reading unsealed data and logging it as a test run.
- **Publication is a third path, and `tools/release_screen.py` covers it.** The gate
  stops the fold being read; the screener stops it being published. It reports
  `sealed/` on its own `SEALED` line — expected, always printed, exit 0 — and
  escalates to `BLOCKED` the moment git can see one of those files. Reporting it as
  `BLOCKED` unconditionally was the earlier behaviour and had to change: it made
  "BLOCKED must be 0" permanently false on every machine holding the data, and a gate
  that can never pass is one people stop reading. The `staged_sealed_not_escalated`
  mutation is what keeps the reassuring line from absorbing the real violation.

- **The same reasoning applies to the content sniffer, via `tools/screen_allowlist.json`.**
  Five files trip it on every run for reasons that are not note text — Korean project
  prose, and the synthetic header that `release_screen.py` and its tests necessarily
  quote. Printed in full they were five permanent lines nobody read, so a sixth would
  have arrived among them unnoticed; the allowlist turns the expected hits into a count
  and prints only what is new. Three properties make it a reduction in noise rather
  than in coverage. It is committed and reviewable, so adding an entry is a visible
  act. Each entry pins the *kind* of hit expected, so a file excused for Korean prose
  that starts matching the clinical-header pattern is still reported. And the direction
  is fixed: an entry may only excuse a file the path rules already publish, never widen
  what they publish — nothing under `data/` or `sealed/`, nothing `deny()` covers, no
  patterns, and a rejected list aborts screening entirely rather than producing a
  report that says "all known". `allowlist_may_name_corpus_paths` is the mutation on
  that limit, and it is aimed at `data/README.md` specifically: the one publishable
  path inside a denied prefix, and therefore the gap that the surviving `deny()` check
  does not close. Entries whose files no longer exist are reported as `STALE` but do
  not fail the run — failing would pressure someone into deleting an entry to get a
  green run, and deleting entries is how a list stops describing reality.

**Dirty working tree.** A sealed evaluation run with uncommitted changes produces a
log row whose commit hash does not describe the code that ran. Three options were
considered and the choice is: **refuse by default, `--allow-dirty` proceeds and
records `tree=dirty`.**

The principle is that **the path of least resistance must be the most honest state.**
Whichever option is the default is what almost every row will be produced by, because
the default is what happens when nobody is thinking about this — and the rows are read
years later by someone who was not there. So the question is not which option is
defensible but which one is safe to reach for absent-mindedly.

- **Warn and proceed** fails that test outright. The warning goes to a terminal that
  is not archived; the row that survives is indistinguishable from a clean one. The
  process feels careful and the artefact ends up lying, which is the worst available
  combination — worse than no check, because the check is what supplies the
  confidence.
- **Always record `tree=dirty`** is honest and was the close second. It was not
  chosen because it makes the dirty run frictionless: the row is accurate, and a
  reader still cannot tell what code produced the numbers. Accuracy about an
  unrecoverable fact is not the same as avoiding it.
- **Refuse** costs an inconvenient stop when the uncommitted change is a stray note.
  That cost is paid at the moment the person has full context and can commit in ten
  seconds, which is the cheapest possible time to pay it — and the alternative is
  paid later by someone reconstructing what ran.

`--allow-dirty` exists because refusal alone would be dishonest in a different way:
under deadline pressure the response to a hard block is to stash, run, and unstash,
which produces a *clean-looking* row that is just as wrong and now unmarked. The flag
keeps that case inside the system and labelled. It is deliberately not the default —
`tree=dirty` should be a thing someone chose, which is also what makes it worth
reading when it appears.

`tests/test_seal.py` pins both halves: `test_a_dirty_tree_is_refused_by_default` and
`test_allow_dirty_gets_past_the_tree_check`, the latter written so the override stops
at the *next* guard rather than at the fold, so it cannot become a general bypass.

### 6.2 The split file must be generated before the fold is sealed

The order is **generate, freeze, seal**, and it is a requirement rather than a
convenience.

`splits/{corpus}.json` records, for every fold including test, the document ids,
span counts, per-canonical-type counts, token distribution and per-document
hashes. Every one of those figures is derived by reading the documents. Once the
test fold is behind `sealed/`, none of them can be recomputed — so a file generated
after sealing would contain figures for a third of the corpus that nobody can ever
check, which is indistinguishable from figures that are wrong.

Sealing second means the file was written while the corpus was fully readable, and
the commit that froze it is recorded on the first line of
`results/sealed_eval_log.md`. That commit hash is what carries the sealed fold's
figures from then on.

What this costs, stated plainly because a seal that is described as free will be
applied carelessly:

- `split.verify()` recounts the unsealed folds only. The sealed fold's summaries are
  carried by `reconcile_totals()` — the folds must sum to `totals` — plus the freeze
  commit. Corrupting the sealed figures now takes two consistent edits rather than
  one stale block, which is forgery rather than staleness, and no check inside the
  file can distinguish it.
- `python3 -m src.split --check` prints which folds it recounted and names the one
  it did not, because a check covering two thirds of the corpus and printing `ok`
  would be read as covering all of it.
- Checks that happened to depend on a sealed document must be restated rather than
  dropped. §9.5's step 2 is the example: the one MEDDOCAN stem that shares a name
  surface and nothing else straddles the split, so the rule was extracted to take
  the *counts the file records* instead of documents, keeping the discriminating
  case checkable after the corpus stopped being fully readable.

**GraSCCo and CARMEN-I use the same structure** — same schema, same `sealed:` config
key, same gate, same ordering. Two things differ and neither changes the structure:
their splits are constructed rather than adopted (§9.5), so `provenance` carries a
seed and a stratification that the split file must record and a test must verify was
actually applied; and CARMEN-I is DUA-restricted, so
`test_the_committed_file_contains_no_span_surface` has to pass *before* its split
file is committed. The generator writes into a file the release screener reports as
allowed, which makes that the one check whose failure would be a disclosure rather
than a bug.

---

## 7. Data

No corpus is redistributed in this repository — neither DUA-restricted
material nor openly licensed corpora. Acquisition scripts only.

| Corpus | Language | Note types | Access |
|--------|----------|------------|--------|
| MEDDOCAN | Spanish | clinical case studies | Zenodo, open |
| GraSCCo | German | mixed inpatient/outpatient — see below | Zenodo, open |
| CARMEN-I | Spanish / Catalan / mixed within document | mixed units — 61% whole notes, 39% clinical sections; codes never expanded. Does not supply the note-type axis — see below | PhysioNet, credentialed |
| ko-surro | Korean | nursing notes | derived from PhysioNet; DUA |
| n2c2 2014 | English | longitudinal progress notes | portal unavailable; on hold |

**GraSCCo is not a GP-letter corpus.** An earlier version of this table said "GP
letters", which was wrong and would have mis-stated the note-type axis. The corpus
ships no document-type field, so the distribution below was measured by content
cues over the full text of all 63 documents (`docs/notes/corpus-observations.md`
§7.3). Labels are **multi-label** — one document can be an outpatient letter
reporting an imaging finding — so the counts do not sum to 63:

```
radiology / imaging    30      progress note      16
pathology / histology  25      discharge           4
outpatient             24      tumour board        3
laboratory             22      operation report    1
```

52 of 63 documents carry a collegial letter frame, which is what made "GP letters"
look right; the letter frame is a *register*, not a document type, and it sits on
top of eight different kinds of content. 20 documents name a department in the
header (internal medicine, oncology, neurology, general surgery, ophthalmology,
orthopaedics, ENT, radiology, epilepsy unit, endoscopy and melanoma-follow-up
clinics), which corroborates the spread.

**Consequence for the axes (§4).** The note-type axis was designed to be carried by
cross-corpus contrast — nursing notes vs case studies vs discharge summaries. It
also stands *within GraSCCo alone*: radiology, pathology, and outpatient
subsets of 30 / 25 / 24 documents are large enough to compare against each other at
fixed language, fixed annotation guideline, and fixed corpus. That is a cleaner
note-type contrast than any cross-corpus pair, because it holds everything except
document type constant. It is also small: the subsets overlap, and per-type figures
for the 12 rare PHI types will not survive the split, so this is a secondary
analysis, not a headline arm.

**The two Spanish corpora are not a language-held-constant pair.** MEDDOCAN and
CARMEN-I share a language, a clinical domain, and every type name, which makes it
tempting to read a gap between them as a measurement of note type or of corpus
difficulty at fixed language. Their type distributions rule that out: `DATE` is 12.5%
of MEDDOCAN's in-scope spans and 74.3% of CARMEN-I's, while `CONTACT` + `ID` is 20.0%
against 0.5%. All ten canonical types occur in both corpora, so the mismatch is in the
mixing weights and no subset restriction removes it. §5.1 gives the arithmetic and the
reporting rules that follow; the measurement is in
`docs/notes/corpus-observations.md` §8.2.

A further asymmetry, recorded rather than worked around: **CARMEN-I has no
patient-name gold at all** — the type is declared and has zero instances, against
MEDDOCAN's 2,014 — so the single most-studied PHI type in this literature cannot be
evaluated on it.

**CARMEN-I does not supply the note-type axis.** This is stronger than "its document
types are coded", and it is not a labelling problem that expanding the codes would fix.
**789 of its 2,000 units are clinical sections, not notes** — `ANTECEDENTES`,
`PROCESO_ACTUAL`, `EXPLORACION_*`, `PLAN_TERAPEUTICO`, `SEGUIMIENTO`, `EVOL`. A section
is a different *kind* of unit from a note, not a different value of the note-type
variable: it is one part of a note, chosen by the corpus builders, and its PHI profile
reflects where in a letter it sits rather than what kind of letter it is. The other
1,211 units carry no section token at all (1,201 `IR`, plus 5 `CC` and 5 `IE`), so
**the unit is not one kind even within this single corpus** — 61% whole notes and 39%
section excerpts, with the `IR`/`IA` contrast confounding unit granularity with
everything else it might measure
(and, per §9.5, with PHI density and language mix as well). No grouping recovers the
parent notes: the sections that would compose one letter share no identifier surface,
and their numbering is a per-section index.

So the note-type axis is **GraSCCo's** to carry, on the 8-way content-cue distribution
measured above at fixed language, fixed guideline, and whole documents throughout.
CARMEN-I's value is different and is not substitutable for it: **it is the only
authentic clinical corpus here**, the only one whose narrative was written by
clinicians for care rather than composed for publication or synthesis. That is what
makes it the right corpus for a disclosure-risk claim, and a disclosure-risk claim is
what the leak rate is for. Asking it to also serve as a note-type contrast would trade
the property only it has for one another corpus already provides.

Selection is not opportunistic. The corpora span a spectrum of **orthographic cue
reliability** — but that spectrum is two-dimensional, not one. An earlier version of
this section listed one row per (language × note type) pair, which cannot be right:
English discharge summaries and English nursing notes sat at opposite ends of a
single ordering despite sharing a writing system. The two dimensions are separate.

**Axis 1 — baseline cue availability, fixed by the language.** What the
orthography makes *possible*, before anyone writes anything.

```
English    capitalisation marks proper nouns and little else     high
Spanish    capitalisation present, but compound given names
           and multi-part surnames blur the span boundary        medium
German     every noun is capitalised, so the cue fires on
           almost every token and carries no information         low
Korean     no case distinction exists at all                     none
```

**Axis 2 — realisation, modulated by note type within one language.** Whether the
available cue actually appears in the text.

```
English discharge / radiology   edited, proofread register — the cue is realised
English nursing notes           capitalisation collapses under time pressure;
                                the cue exists in the orthography and not on
                                the page — cue unrealised
German mixed (GraSCCo)          baseline is already low, so there is little for
                                note type to modulate; the 8-way document-type
                                spread in §7 above measures how little
```

Hypothesis: **a detection layer's contribution ≈ f(baseline availability ×
realisation).** The two axes multiply rather than add, which is why English nursing
notes behave like Korean (high baseline, near-zero realisation) while German
behaves uninformatively for a different reason entirely (low baseline, realisation
irrelevant). A one-dimensional ordering conflates those two routes to the same
observed performance; separating them is what makes the language axis and the
note-type axis (§4) predictions of one mechanism rather than two unrelated
comparisons.

**The hypothesis does not apply uniformly to "the rule layer".** §2 stage 1 lists
four detection layers, and they depend on orthography to different degrees — so
stating this at the level of "rules vs tagger" would predict an effect where three
of the four layers should show none:

| layer | depends on | sensitivity to realisation |
|---|---|---|
| regex / checksum | structural form (digit counts, check digits, delimiters) | **none** — a phone number or NIF is shaped the same in a nursing note as in a discharge summary |
| context cues | the orthography *around* a role word (`paciente`, `Dr.`, `Frau`) — whether the following token is capitalised, where the span ends | **high** — this is the layer the cue actually feeds |
| gazetteer | dictionary membership of the surface form | **none** — a lowercase `madrid` still matches under case folding, and case folding is free |
| learned tagger | orthography among many features | **partial** — it learns capitalisation where it is informative and can compensate with position, morphology, and collocation where it is not |

**Prediction: when realisation falls, the loss concentrates in the context-cue
layer**, while regex/checksum and gazetteer recall are approximately flat, and the
tagger degrades but by less than the context-cue layer. This is a sharper and more
falsifiable claim than "rules do worse in nursing notes" — it names which component
should move and which should not, so a result where all four fall together would
refute the mechanism even if the aggregate rule-layer number moved as expected.

**Measuring this requires layer-level provenance, which is why §3 carries a `layer`
field.** §5's complementarity breakdown is a rules/tagger dichotomy: it can show
that the rule layer as a whole lost ground, but it cannot attribute the loss to
context cues rather than to regexes, so on its own it cannot test the prediction
above. The `layer` field on every span (§3, values defined in
`config/naming.yaml`) is what closes that gap — the same detections grouped by layer
give a per-layer complementarity breakdown, and the four values there are exactly
the four rows of the table above. §5 is left as it is: the rules/tagger breakdown
stays the headline decomposition, and the per-layer view is an additional cut over
the same detections, not a replacement.

The consequence for corpus selection is that **a language is an interval, not a
point.** English spans nearly the whole range on its own. German's interval is
narrow, and GraSCCo's measured document-type distribution is what lets us state its
width rather than assume it — the within-corpus note-type contrast described above
is the instrument for that, which is why it is worth running even as a secondary
analysis.

### 7.1 The product hypothesis has an untested cell

The hypothesis above is a product, but it is only tested in the cells the corpora
populate — and after the CARMEN-I correction those cells do not include the middle of
axis 1. Recorded here rather than worked around, because a product claim verified at
its two ends is weaker evidence than the phrasing suggests.

**Axis-2 variation is observable in German, and in English only if n2c2 arrives.**
GraSCCo's 8-way document-type distribution supplies it at low baseline. The two English
rows of the axis-2 table are a projection, not a measurement: n2c2 2014 is on hold with
its portal unavailable, so the high-baseline cell is currently unpopulated too.

**Spanish is the only interior value on axis 1 and supplies no axis-2 variation at
all.** MEDDOCAN is a single register — published case studies, uniformly edited — so
note type does not vary within it. CARMEN-I cannot supply note type either, per the
correction above. Two Spanish corpora, and no way to vary realisation at fixed
language.

**So "a language is an interval" is instrumented for German and, prospectively,
English — not for Spanish.** The MEDDOCAN/CARMEN-I contrast was the only candidate
instrument for Spanish's width, and two independent findings disqualify it: the type-mix
confound of §5.1, and the unit-kind mismatch of the correction above. Either alone would
be enough; they are not the same objection, and neither is repairable by reweighting or
subsetting.

**Conclusion: the multiplicative interaction is checkable in at most two cells, and a
test at medium baseline is impossible with this corpus set.** Not a flaw in the
hypothesis and not a reason to restate it as something weaker — the prediction of §7
remains falsifiable where it is measurable, per layer, within GraSCCo. It is a bound on
what the result can claim: an interaction confirmed at low baseline and (if n2c2
arrives) at high baseline, with the interior left unobserved. A second Spanish register
at fixed language — clinical notes against case studies, or an unedited Spanish
register — is what closes this, and acquiring one is worth more to the argument than a
further arm on the corpora already held.

---

## 8. What is outside the system

The paper's system is `src/orchestrate.py` and the agents it calls. Everything
recorded in `agent_calls.jsonl` is inside.

Claude chat sessions and Bedrock Claude Code are research tools, not system
components. They are disclosed in the AI-use statement, not in Methods. Design
conversations that shape the role set are, however, the record of the
`port-human` arm and the source material for `port-selfdesign` prompts — worth
keeping.

---

## 9. Canonical type set and matching policy

Decided 2026-08-05 on the evidence in `docs/notes/corpus-observations.md`. Each
decision records **why**, because these are the choices a reviewer will question.

Every count below was measured from the corpora at the version DOIs pinned in
`data/acquire/*.sh`, not copied from corpus documentation.

### 9.0 The canonical set

Ten types. Both corpora map into it exhaustively — no gold span is silently
dropped, and the two columns reconcile to each corpus's full span count.

| canonical | es-meddocan source types | es n | de-grascco source types | de n |
|---|---|---|---|---|
| `NAME` | `NOMBRE_SUJETO_ASISTENCIA`, `NOMBRE_PERSONAL_SANITARIO` | 4,012 | `NAME_PATIENT`, `NAME_DOCTOR`, `NAME_RELATIVE`, `NAME_USERNAME`, `NAME_EXT` | 324 |
| `DATE` | `FECHAS` | 2,566 | `DATE`, `DATE_BIRTH` | 693 |
| `AGE` | `EDAD_SUJETO_ASISTENCIA` | 2,074 | `AGE` | 19 |
| `LOCATION_AREA` | `TERRITORIO`, `PAIS` | 5,241 | `LOCATION_CITY`, `LOCATION_ZIP`, `LOCATION_COUNTRY` | 99 |
| `LOCATION_STREET` | `CALLE` | 1,709 | `LOCATION_STREET` | 36 |
| `ORGANISATION` | `HOSPITAL`, `INSTITUCION`, `CENTRO_SALUD` | 776 | `LOCATION_HOSPITAL`, `LOCATION_ORGANIZATION` | 37 |
| `CONTACT` | `CORREO_ELECTRONICO`, `NUMERO_TELEFONO`, `NUMERO_FAX` | 1,096 | `CONTACT_EMAIL`, `CONTACT_PHONE`, `CONTACT_FAX` | 28 |
| `ID` | `ID_SUJETO_ASISTENCIA`, `ID_ASEGURAMIENTO`, `ID_TITULACION_PERSONAL_SANITARIO`, `ID_CONTACTO_ASISTENCIAL`, `ID_EMPLEO_PERSONAL_SANITARIO` | 3,005 | `ID` | 59 |
| `PROFESSION` | `PROFESION` | 37 | `PROFESSION` | 2 |
| `OTHER` | `OTROS_SUJETO_ASISTENCIA` | 22 | — | 0 |
| **canonical total** | | **20,538** | | **1,297** |
| **excluded (§9.1)** | | **2,257** | | **139** |
| **corpus gold total** | | **22,795** | | **1,436** |

Fine-grained source types survive as a `subtype` field on each span for per-corpus
analysis. `subtype` never participates in cross-corpus scoring, because the two
corpora do not partition the space the same way — MEDDOCAN has four ID subtypes
where GraSCCo has one, and forcing agreement there would measure the annotation
schema rather than the detector. Role (patient / clinician / relative) is likewise
an attribute on `NAME`, not a separate type: both corpora already distinguish
patient from clinician names, so this is lossless for both.

`mappings/{corpus}.yaml` holds the mapping and is the artifact the Mapper agent
produces; the table above is the human-authored `port-human` version of it.

### 9.1 Excluded from the canonical set

`SEXO_SUJETO_ASISTENCIA` (1,841 spans), `FAMILIARES_*` (416 spans), `NAME_TITLE`
(139 spans in GraSCCo, 0 in MEDDOCAN).

**Why.** Sex and relationship words are not among the 18 HIPAA Safe Harbor
identifiers — `FAMILIARES_*` surfaces are the common nouns `madre`, `familia`,
`padres`, not names — so a detector scored on them is being measured on something
other than disclosure risk. `NAME_TITLE` is excluded for a different reason: the
two corpora annotate it incompatibly and neither is wrong. GraSCCo makes `Dr.` its
own span; MEDDOCAN excludes titles from the name span entirely (0 of 1,000 sampled
`NOMBRE_PERSONAL_SANITARIO` spans begin with `Dr`/`Dra`/`Prof`). Keeping it would
mean a detector ported to German must emit a span that Spanish gold counts as a
false positive. Excluding all three keeps the cross-language comparison measuring
the porting recipe rather than three annotation-guideline disagreements.

**Measured cost, to be reported as a limitation.**

| | excluded | corpus gold | share |
|---|---|---|---|
| es-meddocan `SEXO_SUJETO_ASISTENCIA` | 1,841 | | 8.08% |
| es-meddocan `FAMILIARES_SUJETO_ASISTENCIA` | 416 | | 1.82% |
| **es-meddocan total** | **2,257** | 22,795 | **9.90%** |
| **de-grascco `NAME_TITLE`** | **139** | 1,436 | **9.68%** |

Per MEDDOCAN fold: train 11,333 → 10,165 (−1,168, 10.31%), dev 5,801 → 5,254
(−547, 9.43%), test 5,661 → 5,119 (−542, 9.57%).

The paper must state that our Spanish figures are computed on 90.1% of MEDDOCAN's
gold and are therefore **not directly comparable to the MEDDOCAN shared-task
leaderboard**, which includes sex and family-relationship spans. This is a real
cost of the decision and is reported as a limitation, not buried in a footnote.
Where a leaderboard comparison is wanted, §9.6 provides the route: the official
split is retained, so a supplementary run on the full MEDDOCAN type set is
possible without redoing the split.

### 9.2 `TERRITORIO` merges into a single `LOCATION_AREA`

MEDDOCAN's `TERRITORIO` (3,818 spans) mixes place names and postcodes; 1,226 of
2,862 sampled spans are purely numeric. GraSCCo separates `LOCATION_CITY` from
`LOCATION_ZIP`. We merge to the coarse type rather than splitting.

**Why.** Splitting MEDDOCAN's gold into city and ZIP requires applying a
heuristic (`^\d{5}$`) to gold labels. A label produced by inference is not gold,
and this project's whole claim rests on not manufacturing ground truth — the
`sup-free` arm exists precisely to test annotation-free *supervision*, which is
only meaningful if the *evaluation* labels stay untouched. Merging loses
granularity; inventing labels loses the ability to make any claim at all. GraSCCo's
finer distinction is preserved in `subtype` for German-only analysis.

### 9.3 Matching: two modes, and two different matchings within each

Two things are decided here and they are independent. **How strict is "covered"** —
answered by two *modes*, `fully_covered` and `relaxed`. **What competes for credit** —
answered by two *matchings*, coverage and assignment, both computed in both modes.
Conflating them is the mistake this section exists to prevent.

#### The two modes

**`relaxed`.** A prediction covers a gold span when their character ranges overlap by
**at least one character** and the **canonical types are equal**.

**`fully_covered`.** Every character of the gold span is covered, by predictions of
the equal canonical type.

**Exact-boundary strict scoring is not computed, and dropping it is a change from an
earlier version of this section.** That version defined a third mode requiring
`begin` and `end` to match exactly, alongside the argument that refutes it: 130 of
GraSCCo's 139 `NAME_TITLE` spans are immediately followed by another `NAME_*` span
(122 `NAME_DOCTOR`, 8 `NAME_PATIENT`), so a detector emitting `Dr. Osler` as one span
is right by MEDDOCAN's convention and boundary-wrong by GraSCCo's. An exact-boundary
figure therefore moves with the annotation guideline rather than with the detector,
which is the opposite of what the porting axis measures — and that is true of the
number wherever it appears, not only when it leads. Having established that, there is
no place left in the paper to cite it: `relaxed` is the boundary-tolerant quality
figure and the i2b2/n2c2 convention a reader expects, `fully_covered` is the
disclosure figure, and a third column nobody can interpret invites exactly the
comparison the paragraph above rules out. Two modes rather than three also means each
reported number has one definition behind it instead of a mode label to check first.

**Why type equality is required in both modes.** Type-agnostic overlap would let one
prediction cover two adjacent gold spans of different types and would make the
complementarity breakdown in §5 uninterpretable — "found by rules only" has to mean
found *as the right kind of thing*. The cost is visible and accepted: a detector that
finds a date but calls it an ID is scored as both a miss and a false positive.

#### The two matchings, and why one is not enough

A single one-to-one assignment cannot serve both headline quantities. The failure is
concrete:

```
gold:  [Juan] [Pérez]        two adjacent NAME spans
pred:  [Juan  Pérez    ]     one NAME span covering both completely
```

One-to-one assignment gives that prediction to one gold span and leaves the other a
false negative — which is *correct* for credit, and the reason the rule exists. But
computing the leak rate from false negatives then reports a leak on an identifier that
is completely covered in the text. The mirror case exists too: a gold span split
between two adjacent predictions is fully covered while no single prediction covers it,
so a per-prediction `fully_covered` test would report a leak on a span that is entirely
redacted. **Both directions invent leaks that do not exist.**

The cause is that two different questions are being asked.

- **"Is this identifier hidden in the text?"** — a disclosure question, and a property
  of the text. Which detector deserves the credit is irrelevant, so nothing needs to
  compete: the answer is taken against the **union** of same-type predictions.
- **"Does the detector get credit for finding it?"** — a quality question. Here one
  wide prediction must not collect several gold spans, so the answer requires a
  **one-to-one assignment**.

Accordingly:

| matching | definition | feeds |
|---|---|---|
| **coverage** | the **union** of same-type predictions, tested against each gold span under the mode's rule | leak rate, complementarity breakdown |
| **assignment** | **one-to-one greedy** by largest overlap; each gold span claimed by at most one prediction and each prediction by at most one gold span | TP / FP / FN → precision, recall, F1 |

Note that `fully_covered` is only meaningful against the union — "every character
covered by *some* prediction" is a union statement — which is a second reason the two
matchings cannot be collapsed.

**The complementarity breakdown uses coverage, and this is load-bearing.** "Found by
rules only" is a claim about what a mechanism found, not about which mechanism won an
assignment. Under one-to-one matching, when two layers both find the same gold span,
one of them loses the assignment and is scored as not having found it — so `both` is
undercounted and `rules only` / `tagger only` are inflated by exactly the cases where
the layers agree. That is the reverse of what §5's complementarity is measuring, and
the error is largest precisely where the layers overlap most, which is the region the
number exists to quantify.

**The greedy assignment must be totally ordered.** Ties are resolved by
`(-overlap, gold.start, gold.end, pred.start, pred.end, pred_index)`, so the result
cannot depend on input order, dictionary iteration, or the order a detector happened to
emit spans in. A scorer whose output moves when the same spans arrive in a different
order is not a measurement, and this is checked by scoring shuffled input twice.

**`assignment_slack` is reported.** It counts gold spans that coverage calls covered
and assignment calls a false negative — the `[Juan] [Pérez]` case above, and nothing
else. It is not an error term to be minimised or corrected for: it is the amount by
which the detector's span boundaries group differently from the gold guideline's. A
large value means the two disagree about where one identifier ends and the next begins,
which is a fact about the porting target worth reading, and the alternative to
reporting it is that the gap between the leak rate and recall looks unexplained.

#### Which figure is the headline is a per-metric decision

- **Leak rate: `fully_covered`.** The `relaxed` figure is reported beside it as a
  **lower bound**. A gold span with any overlap counts as covered under `relaxed` even
  if one character was detected, and partial redaction can still disclose — a surname
  detected with the given name left in place is a disclosure. A disclosure claim must
  not rest on a definition under which one detected character redacts a name.
- **Precision / recall / F1: `relaxed`.** These score detector quality against
  inconsistent boundary guidelines, which is what `relaxed` is for; `fully_covered`
  P/R/F1 is reported alongside.

**The scorer does not choose.** It computes both modes symmetrically, records which
figure is the headline for which metric in its output, and leaves the selection to the
reporting layer. This is why `src/eval/scorer.py` has no notion of a primary mode: if
a later judgement changes which figure leads, that is an edit to how results are
presented, not a recomputation — and no result already written becomes unreadable.

### 9.4 Sparse types are in the leak-rate denominator

Types with n ≤ 8 corpus-wide stay in the denominator for leak rate and overall
P/R/F1. They are omitted only from per-type tables, where a single instance yields
a meaningless 0% or 100%.

**Why.** A leak is a leak regardless of how often its type occurs; dropping rare
types from the denominator would hide real misses and make the headline number
better by definition. The affected volume is small enough that there is no
efficiency argument for excluding them: in GraSCCo, 7 kinds after the §9.1
exclusions, 18 of 1,436 spans (1.3%) — `NAME_EXT` 1, `CONTACT_EMAIL` 1,
`PROFESSION` 2, `LOCATION_ORGANIZATION` 2, `LOCATION_COUNTRY` 2, `NAME_USERNAME`
2, `CONTACT_FAX` 8. MEDDOCAN's `OTHER` (22 spans, a residual bucket holding
ethnicity, marital status, a tattoo and a spearfishing gun) stays in for the same
reason, and is explicitly **not** a rule-development target: it is reported as an
irreducible floor on the leak rate.

Per-type tables state the omission and the omitted count, so a reader can see that
the totals do not sum to the per-type rows.

### 9.5 Grouping requires identifier agreement, not a filename pattern

A split group is formed only when identifier surfaces agree. Filename structure
raises the question; it never answers it.

**Why.** Measured on MEDDOCAN: of 936 article stems, 34 have documents on both
sides of the official split, and **32 of those 34 share no identifying surface
across folds at all**. The two that share anything share only a bare given name
(`Antonio`, with different surnames `Moreno Flores` vs `Machado Briceño`) or an
age string (`45 años`). Same journal article is not the same patient. There are
also 0 exact-duplicate document texts and 0 duplicated narrative prefixes
corpus-wide, so there is no hidden case reuse either. Grouping on the filename
stem would have cost 80 documents' worth of independent units to prevent leakage
that does not exist.

**The rule, as implemented in `src/split.py`:**

1. **Candidate stems.** Parse `{stem}{sep}{suffix}` where the suffix may be digits
   *or* letters, and treat the stem as an opaque string. A stem is a candidate only
   if it occurs with **two or more distinct suffixes**. Without this condition
   `Amanda_Alzheimer.txt` — one document, no sibling — would be split into stem
   `Amanda`. 16 of GraSCCo's 63 filenames contain an underscore; only 15 belong to
   a real group. Both earlier bugs here were the same mistake in different clothes:
   assuming the suffix is numeric (`^(S\d{4}-\d+)-\d+$` dropped 31 MEDDOCAN ids
   whose journal prefix contains a letter; a digits-only GraSCCo suffix rule missed
   all 11 `Colon_Fake_*` documents).
2. **Confirm by identifier.** For each candidate stem, compare the gold surfaces of
   the person-name, birth-date and record-number types across its documents. Group
   them only if they agree on a name **and** at least one of birth date or record
   number. Dates are compared after format normalisation, because the same date
   appears as `21/06/1967`, `21.06.67` and `21.06.1967`.
3. **Otherwise, the document is its own group.** No group is created from a
   filename pattern alone.
4. **Record the decision.** `splits/{corpus}.json` stores, per group, which rule
   formed it and which surfaces agreed, so the grouping is auditable without
   re-reading the notes.

**How the two GraSCCo cases resolve:**

- `Tupolev_1..4` → **one group of 4 documents.** Step 1 admits the stem (four
  distinct suffixes). Step 2 confirms: `Konstantin Tupolev` in all four, one birth
  date in three formats (`21/06/1967`, `21/06/1967`, `21.06.67`, `21.06.1967`), and
  record number `1933309807` shared by `_1`, `_2`, `_3`. Consistent with one
  episode documented by different units — `_1`/`_2` are Innere Medizin, `_3` is
  Röntgenabteilung.
- `Colon_Fake_A..K` → **11 independent groups.** Step 1 admits the stem (11
  distinct letter suffixes; note the earlier digits-only rule missed this
  entirely). Step 2 **rejects** it: 11 distinct patient names, alliterating with
  the suffix letter (`Antonia Anderer`, `Beatrice DE BEAUHARNAIS`, `CHRIST
  Charlotte`, …), 11 distinct birth dates, no shared record number. The stem marks
  a shared clinical scenario (colon carcinoma), not a shared patient. Grouping them
  would discard 10 of 63 independent units — at n=63 that is expensive — on
  evidence the annotations contradict.

This supersedes the conjecture in `corpus-observations.md` §3 that grouping both
stem families is the conservative reading. It is conservative for `Tupolev_*` and
simply wrong for `Colon_Fake_*`.

**How CARMEN-I resolves: document-disjoint, stratified, 2,000 units.** No grouping
survives step 2, so every document is its own group — and because CARMEN-I ships no
official split, the folds are constructed here and frozen before any rule is written
(§6). Both candidate groupings were measured and both fail:

- **Same `(doctype, number)` across section tokens** — 189 candidate groups covering
  775 documents. `IA_ANTECEDENTES_7` and `IA_PROCESO_ACTUAL_7` read like two sections
  of one letter, which is exactly why the filename cannot be trusted to answer it.
  **0 of 189 groups share a single identifier surface.** And the numbering is
  contiguous `1..N` within all 20 `(doctype, section)` pairs, so the number is a
  per-section index and shared numbers are arithmetic, not linkage. Grouping anyway
  would collapse 775 documents into 189 units, discarding **586 independent units** to
  prevent leakage the annotations contradict. Same verdict as `Colon_Fake_*`, and the
  MEDDOCAN article-stem error in a third costume.
- **Transitive grouping on shared identifier surfaces** — also rejected, for a
  different reason. It yields 1,944 groups, 1,941 of them singletons, plus one group
  of 52, one of 5, one of 2. The three linking surfaces are 7, 3 and 9 characters,
  **letters only, zero digits**: not identifier values. The 52-document group would
  merge that many unrelated patients on what is most likely a generic word emitted by
  the surrogate generator. A surface that agrees is not automatically an identifier
  that agrees, which is the part of step 2 this case exercises.

**Stratified by document type and language**, unlike the other corpora, because two
confounds here are large enough to move a fold: PHI density varies **4× by document
type** (`IR` 8.1 spans per 1,000 tokens vs `IA` 32.0), and **84% of bilingual
documents are `IR`** (221 of 264). An unstratified random split over 2,000 documents
can hand one fold a materially different PHI density and language mix from another, and
a leak-rate difference between folds would then be uninterpretable — corpus composition
and detector behaviour would be confounded. Stratification is not a grouping claim: the
unit is still the document, and no document is linked to another.

The cross of the two strata is ragged and the split code must say so rather than
silently round: `CC` and `IE` have 5 documents each, `IE` has no bilingual document at
all, and cells like `CC`/`bi` hold exactly one. Small cells are assigned by a
deterministic rule recorded in `splits/es-carmen.json` alongside the achieved per-fold
density and language mix, so a reader can check what the stratification actually
delivered instead of trusting that it was requested.

**Limitation, stated rather than assumed away:** whether two documents belong to the
same patient is not knowable from this release. There is no patient key, and the
identifier surfaces do not agree, so if two documents *are* the same patient they can
land on opposite sides of the split and inflate the results. This is not an argument
for grouping — grouping on the measurements above would be the larger error — but it is
also not a risk we can bound. It is reported as a limitation of every CARMEN-I number,
and it is one more reason not to read a CARMEN-I / MEDDOCAN difference as a porting
result (§5.1).

Note for CLAUDE.md's "patient-disjoint" requirement: **no corpus here has a patient
key** — not MEDDOCAN, not GraSCCo, not CARMEN-I — so §6's "largest natural group
available" applies to all three. Under this rule the group is a confirmed same-patient
cluster where one is demonstrable (`Tupolev_1..4`, the only case in three corpora) and
the document otherwise. That the rule so far yields the document almost everywhere is
the measurement, not a shortcut: each rejection above cost a specific, counted number
of units that a filename-based grouping would have thrown away.

### 9.6 MEDDOCAN keeps its official split as the primary result

Primary results use the shipped train 500 / dev 250 / test 250 (measured, matching
its documentation). A second article-stem-disjoint split is built and reported
alongside, for seen/unseen analysis only.

**Why.** The official split is public and frozen, so results on it are comparable
to previously published systems and carry no suspicion that we tuned the partition.
The second split answers a question rather than assuming it: §9.5 shows the
official split is not article-disjoint (34 stems straddle it, 80 documents), and
the surface-reuse measurements show how much memorisation is available — 46.9% of
test spans have a surface that also occurs in train, ranging from 99.6%
(`SEXO`, excluded anyway) and 96.7% (`PAIS`) down to 1.8% (`ID_SUJETO_ASISTENCIA`).
Patient-name reuse sits at 39.6%. Reporting both splits costs one extra evaluation
run per arm and turns "does article-level leakage matter?" into a measured
difference.

GraSCCo ships no split, so one is created under §9.5 and committed before any
German rule is written, per §6. At a 20% test fold that is 12–13 documents and
roughly 290 spans, which bounds what German per-type numbers can claim
independently of any of these decisions.

### 9.7 BOM is stripped and offsets are shifted

Every corpus loader strips a leading U+FEFF and subtracts its length from all gold
offsets in that document. One rule, applied identically to every corpus.

**Why.** The gold offsets as shipped count the BOM as a character, so the choice is
not cosmetic — it silently shifts spans by one. Reading MEDDOCAN with
`encoding='utf-8-sig'` breaks **761 of 761 gold spans** in the 32 BOM-carrying
files, while plain `utf-8` matches all 761. GraSCCo has 5 BOM files, and in 2 of
them (`Baastrup.txt`, `Dupuytren.txt`) the first gold span begins at index 0, so
the annotated surface itself starts with U+FEFF — `'﻿ARCOS-KLINIK
FLENSBURG…'` is a `LOCATION_HOSPITAL` span whose first character is a byte-order
mark. Either convention is defensible; what is not defensible is having two of
them, because a loader disagreeing with the evaluator by one character makes every
number in the paper wrong in a way that no aggregate would reveal. Stripping is
chosen over retaining so that a span surface is always the text a human would call
the identifier.

**The loader asserts, it does not trust.** After loading a document, every gold
span is sliced out of the loaded text and compared to the surface string recorded
in the annotation; a mismatch raises rather than warns. This check has already
earned its place — it is what caught the BOM interaction, and it passed on all
22,795 MEDDOCAN spans and all 1,436 GraSCCo spans once the convention was fixed.
The same assertion runs for every corpus added later, which is the point: the next
corpus's encoding surprise should fail loudly on acquisition rather than quietly at
results time.

---

## 10. Secondary analyses

Results worth reporting that are **not** load-bearing for any decision above. Each
entry says what it measures and what it does not license. They are separated from the
main sections deliberately: a design decision that turns out to rest on an appendix
result is a decision that was made for the wrong reason.

### A1. How well would a per-document language selector have done?

§5.2 rejected per-document language identification because a selector's own error
rate is invisible to the metric set in §5. That is an argument about what we can
measure inside the pipeline, not a claim that selectors are inaccurate — and CARMEN-I
lets us check the second question separately, because it ships a per-document language
label (`es` / `bi` / `cat`) as corpus metadata rather than something we would have had
to infer. So the selector can be scored against gold: run a language identifier over
all 2,000 documents, compare to the shipped label, and report accuracy with a
confusion matrix.

The interesting cell is `bi` — 264 documents, 13.2% — since a single-label identifier
has no correct answer available for them and its behaviour there is exactly what
option (b) would have depended on.

**What this licenses:** a statement of how accurate the rejected component would have
been. **What it does not:** reopening §5.2. Even a selector at 100% against the
shipped label leaves the 264 mixed documents needing a tie-break rule that the label
cannot supply, and leaves that rule's error unmeasured in the detection metrics.
High accuracy here is not evidence for routing; it just means the argument for the
union rests on measurability rather than on the selector being bad.
