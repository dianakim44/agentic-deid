# DESIGN

Decisions already made, and the reasoning behind them. Read this before
proposing changes to the pipeline, the agents, or the experiment matrix.
If a decision here turns out to be wrong, change it here first — do not
work around it in code.

Last updated: 2026-08-05

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

**This field is fixed before the first span exists**, deliberately. Adding it after
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
  and the reason the strict form is the headline are in §9.3.
- **Complementarity breakdown** — found by rules only / tagger only / both /
  neither. This is what shows whether the neural layer earns its cost.

Precision, recall, and F1 are reported but are not the headline, for the
reason in §2.

Per-arm cost is reported alongside quality: LLM calls, tokens, wall time.
A quality gain that costs 2× is a different result from one that costs 1.05×.

---

## 6. Experimental integrity

- Split unit is the largest natural group available (patient where patients
  exist, document otherwise). Never record-level random split.
- `splits/{corpus}.json` is frozen and committed **before** any rule is
  written. The commit hash is the reference point.
- The test fold lives in `sealed/` and is not read during development —
  by agents or by people. Rules developed while looking at test are the same
  leakage as training on test.
- Every evaluation on the sealed fold is appended to
  `results/sealed_eval_log.md` with date, commit hash, and purpose, so the
  paper can report how many times the test set was touched.
- The dev fold is where rules are developed, agents iterate, and checkpoints
  are selected.

---

## 7. Data

No corpus is redistributed in this repository — neither DUA-restricted
material nor openly licensed corpora. Acquisition scripts only.

| Corpus | Language | Note types | Access |
|--------|----------|------------|--------|
| MEDDOCAN | Spanish | clinical case studies | Zenodo, open |
| GraSCCo | German | mixed inpatient/outpatient — see below | Zenodo, open |
| CARMEN-I | Spanish / Catalan | discharge, referral, radiology | PhysioNet, request pending |
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

### 9.3 Matching: relaxed is the headline, strict reported alongside

**Relaxed (primary).** A predicted span matches a gold span when their character
ranges overlap by **at least one character** and the **canonical types are equal**.
Matching is a one-to-one assignment: each gold span is claimed by at most one
prediction and vice versa, resolved greedily by largest overlap so that one wide
prediction cannot claim credit for several gold spans.

**Strict (secondary).** Exact `begin` and `end` plus equal canonical type.

**Why relaxed leads.** Boundary conventions differ between corpora — the
`NAME_TITLE` case is the proof: 130 of GraSCCo's 139 `NAME_TITLE` spans are
immediately followed by another `NAME_*` span (122 `NAME_DOCTOR`, 8
`NAME_PATIENT`), so a detector emitting `Dr. Osler` as one span is right by
MEDDOCAN's convention and boundary-wrong by GraSCCo's. Under strict-only scoring
the headline number would move with the annotation guideline rather than with the
detector, which is the opposite of what the porting axis is meant to measure.
Relaxed span scoring is also the i2b2/n2c2 de-identification convention, so it is
what a reader expects.

**Why type equality is still required.** Type-agnostic overlap would let one
prediction cover two adjacent gold spans of different types and would make the
complementarity breakdown in §5 uninterpretable — "found by rules only" has to mean
found *as the right kind of thing*. The cost is visible and accepted: a detector
that finds a date but calls it an ID is scored as both a miss and a false positive.

**Leak rate uses relaxed, and this is deliberately generous to the system.** A
gold span with any overlap at all counts as covered, even if only one character was
detected. Partial redaction can still disclose (a surname detected but the given
name left in place is a disclosure), so the leak rate as defined is a **lower
bound** on true disclosure risk. Stated in the paper as such. A stricter
`fully_covered` variant — every character of the gold span covered by some
prediction — is computed and reported next to it so the gap is visible rather than
argued about.

**The paper's headline leak rate is the `fully_covered` figure**, with the relaxed
figure reported beside it as the lower bound. Relaxed matching is the right
convention for scoring detector *quality* against inconsistent boundary
guidelines, but a disclosure claim must not rest on a definition under which one
detected character redacts a name.

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

Note for CLAUDE.md's "patient-disjoint" requirement: neither corpus has a patient
key, so §6's "largest natural group available" applies. Under this rule the group
is a confirmed same-patient cluster where one is demonstrable and the document
otherwise.

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
