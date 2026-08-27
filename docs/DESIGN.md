# DESIGN

Decisions already made, and the reasoning behind them. Read this before
proposing changes to the pipeline, the agents, or the experiment matrix.
If a decision here turns out to be wrong, change it here first — do not
work around it in code.

Last updated: 2026-08-07

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

**"N hours" is now agent wall time and LLM cost, not human hours.** With `port-human`
retired (§11, §4.1) there is no arm in which a person ports the pipeline, so the hours
this project measures are the ones the arms actually spend: wall time, calls and tokens
per arm, which CLAUDE.md already requires beside every quality number. The human-hours
reading of the same sentence is argued indirectly from published annotation- and
rule-authoring-cost literature and must be labelled as external evidence. Both readings
are worth stating; conflating them would let a measured agent cost be read as a measured
saving against a person, which is the one claim retirement removed.

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

#### The Auditor reads *masked* text, and stage 3 is not a prerequisite — decided 2026-08-12

**The requirement is "does not see gold", not "went through the full pipeline".** Planning
`port-loop` surfaced §2's stage 3 (pseudonymization: date offsets, within-document surrogate
consistency, type tags for high-risk identifiers) as an apparent blocker — the Auditor reads
"the de-identified output", and nothing in this repository produces one. The reading is
wrong, and the correction matters because it was about to make an unbuilt stage a
prerequisite for the arm the ladder's lead comparison rests on.

**Masking is sufficient, and it is sufficient for a structural reason rather than as an
approximation.** The Auditor's input is the dev text with **every detected span replaced by
its type tag** (`[NAME]`, `[DATE]`), and nothing else changed. What the Auditor is asked to
find is *residual* PHI — identifiers the rules **missed**. A missed identifier is by
definition not in the detected set, so it is **not masked, and stands in the text verbatim**.
The signal the Auditor exists to find is therefore fully present in masked text; stage 3 would
change only the *replacements*, which are the spans the Auditor is not being asked about.

**A surrogate is not merely unnecessary here, it is worse for this role.** Stage 3 substitutes
realistic replacements — a plausible name for a name, a shifted date for a date. For the
build-time Auditor that means the text it reads is seeded with name-shaped and date-shaped
strings that are *not* PHI, and it will flag them. Those flags are neither gold misses nor
real leaks: they are artefacts of the surrogate generator, and they land in the
"flagged, not in gold" case of the prompt's three-case reading — the case already documented
as the one the RuleAuthor may not act on. So pseudonymised input would inflate exactly the
least actionable category of the report, and §9.2's caution about a surface agreeing with the
surrogate generator without being an identifier is the same observation from the scoring side.
A type tag is unambiguous: it is not a name and cannot be mistaken for one.

**Masking is deterministic code, not an agent.** It consumes the arm's own `spans.jsonl`,
which already carries `start`/`end`/`phi_type` per span and no text, and applies replacements
right-to-left. It makes no inference, so it is not a component whose error rate needs
measuring — and it must not be, because a masker that made judgements would be a second
detector inside the loop.

##### Overlapping spans: the masker masks the union of extents and never picks a winning type

Asked and answered 2026-08-12, while checking the validator's contract and **before the
masker was written**, because the masker's input makes this unavoidable and the obvious
implementation resolves it by accident.

**The input has overlaps by design.** `RuleSet.detect` returns every rule's match including
overlapping ones, deliberately: "a detector that resolved its own overlaps would make every
merge policy produce the same spans" (§4, §9.3). So `spans.jsonl` may carry a `NAME` at
[10, 25) and an `ORGANISATION` at [20, 34), and the masker must produce non-overlapping tags
from them. Union of the *extents* is mechanical — [10, 34) — but **which type tag prints is
not**, and that is the whole difficulty.

**The rule, in two parts:**

1. **The masked extent is the union of the overlapping spans' extents.** Transitively: a
   chain of pairwise overlaps is one tag, so masking cannot depend on the order the spans
   arrive in.
2. **The type tag prints a `phi_type` only where the union is type-homogeneous.** Where every
   span in the union carries the same `phi_type`, that type is printed (`[NAME]`). Where the
   types differ, a distinct tag is printed that names no type. Its spelling is
   `masked_tag_heterogeneous` in `config/naming.yaml` — a value in the config for CLAUDE.md's
   reason, since it lands in the content of a prompt and in the masker's output, and a literal
   in the masker would be a vocabulary item invented in code.

**Why not a precedence order.** Deciding that `NAME` beats `ORGANISATION` — by type, by span
length, by rule score, by anything — makes the masker hold a **merge policy**. Merge policy is
a replaceable strategy whose variants are supposed to be comparable on identical detections
(§4), and a policy baked into the masker would be applied inside `port-loop` to every arm
regardless of which policy the arm was configured with. That is exactly what §9.3 refuses for
the scorer, arriving through a different component: the masker would silently resolve the
overlaps `RuleSet.detect` was built to preserve, and the reason those overlaps survive
detection is that resolving them is somebody else's decision to make and to be measured on.

**This rule is the only one available that makes no judgement.** Union-of-extents is forced —
every character in it was detected by something, so masking all of them reports no more and no
less than the arm found. Homogeneous-type-or-nothing is forced in the other direction — where
the arm's own detectors agree on the type there is nothing to decide, and where they disagree,
*declining to state a type* is the only answer that is not a tie-break. Every alternative
(pick one, print both, print the longer span's) adds information the detection did not
contain.

**From the Auditor's side this costs nothing, which is what makes the choice affordable.** The
heterogeneous tag means "masked, type unresolved", and the property the role depends on is
untouched: a **tag is not a candidate** (`docs/prompts/auditor.md` §1.2) whatever its
spelling, so the agent's instruction — flag the words that were *not* replaced — reads the same
over `[NAME]` and over the heterogeneous tag. The Auditor is looking for residual identifiers,
and a residual identifier is by definition outside every tag. Two smaller consequences follow
and both are already the specified behaviour: the validator refuses a flag overlapping *any*
tag without inspecting its type (`src/porting/audit.py`), and a mistyped mask is the arbiter's
question and out of scope here (that section's third consequence) — so a union the masker
declined to type is not a defect the Auditor is being asked to report.

**How often this fires is unmeasured, and the reason it is unmeasured is worth recording.**
The intended measurement is the overlapping-pair count on the dev fold, and it cannot be taken
yet: `rules/es.yaml` currently holds 3 rules and predicts **0 spans on es-meddocan dev**, so
the overlap count is 0 for want of predictions rather than for want of overlaps. The number is
therefore vacuous, not reassuring, and quoting it as evidence that the case is rare would be
the worse error. What *is* measured is the shape of the input this will meet: the dev fold has
**0 gold spans crossing a newline** and **393 gold pairs separated by ≤1 character**, so
adjacency is the common case and tag-abutting-tag is ordinary rather than exotic. **When the
first `port-loop` arm runs, the overlapping-pair count and the heterogeneous-union count are
measured on its own predictions and reported** — the second is the number that says how often
this rule actually fires, and a rule pre-registered without that follow-up is a rule nobody
ever learns the cost of.

**Confirmed at the masker's implementation (2026-08-12), in both directions.** `detect_fold`
over the 250 es-meddocan dev documents with the committed `rules/es.yaml` returns **0 predicted
spans and 0 overlapping pairs** — vacuous exactly as pre-registered above, so the paragraph
stands rather than being replaced by a number. **Gold cannot substitute for it either**, and
that was worth checking rather than assuming: 5254 in-scope dev spans, **0 overlapping pairs,
0 type disagreements**. Annotations do not overlap by construction, so gold overlap is a
property of the annotation guidelines and carries no information about what a union of
*predictions* from two independent layers will look like. There is therefore no proxy available
today, and the honest state is "unmeasured", not "measured as rare". `MaskedDocument.counts`
exposes `n_overlapping_pairs` and `n_heterogeneous_tags` per document for that reason — the
first arm to run measures them as a side effect of masking, rather than a later reader
inferring the rate from an empty dev fold. The two mutations
`a_heterogeneous_union_prints_one_of_its_types` and
`the_mask_tags_are_emitted_in_the_order_they_were_applied` are consequently killed by fixtures
alone until then (`tests/mutations/README.md`).

**Masked text is a *larger* corpus exposure than §1.4, not a smaller one, and it inherits
`FilledPrompt`'s treatment.** This is the part that is easy to get backwards on the intuition
that masked text is safe text. On es-meddocan's dev fold the masked input is **about 210k
tokens** — 810,499 characters of masked transport text over the 250 documents, at the 3.8124
characters/token calibration below — against §1.4's 40-span window at roughly 2,700, so about
**77×**. (This paragraph read `splits/es-meddocan.json`'s `tokens.total: 110601` as a model
token count until 2026-08-14; it is a whitespace count, and the correction makes the exposure
*larger*, not smaller. See the cost-structure bullet under "Ceiling = 8" for the measurement
and for the second error it shared with.) Under the leak rates this arm actually produces a
majority of in-scope
gold identifiers are unmasked, because unmasked is precisely what "leaked" means. So the
Auditor's prompt carries more corpus text, containing more intact identifiers, than any other
prompt in the project. Two consequences, both binding:

- **The masker returns `FilledPrompt` and never a `str`,** for the reason `render_window()`
  does (`src/llm/prompt.py`'s module docstring): it is the second function in the project that
  slices document text for a prompt, so it belongs inside the same discipline rather than
  beside it. Nothing writes the masked text to disk, no exception message quotes it
  (CLAUDE.md), and `reports/leaks_{iter}.json` holds offsets, types and scores only — never
  the flagged surface.
- **Where §1.4's `n` is 0 for DUA reasons, the Auditor cannot run at all.** §1.4 records that
  a corpus whose text may not be sent to an external API sets `n` = 0 and the arm runs on a
  local model or is reported as not run. That constraint applies here **with more force**,
  and it is not degradable: an Auditor shown 0 characters produces no report, and `port-loop`
  without an Auditor is a different arm. So the DUA case is not "`port-loop` with a smaller
  window" — it is `port-loop` unavailable on that corpus, recorded per corpus rather than
  worked around.

**What is deferred, and when it becomes due.** Stage 3 proper — one date offset per document
preserving intervals, surrogates consistent within a document, type tags for high-risk
identifiers — is **not built and is not scheduled by this decision.** Nothing in the ladder
needs it: CLAUDE.md fixes evaluation as detection-only, so every arm's metrics are computed
from spans against gold and no arm's score depends on a replacement ever being generated. It
becomes due at exactly three points, none of which is `port-loop`: **(1)** any claim about the
*utility* of the pipeline's output, since utility is a property of the replacements and not of
the detection; **(2)** releasing de-identified text from any corpus, where a type tag destroys
readability that a surrogate preserves; **(3)** the `RT-Aud` arm's **runtime** role, if that
arm is specified as auditing pipeline output rather than masked output — a decision that
should be taken with that arm and not inherited from this one. Recording the trigger
conditions is the point: a deferral with no stated due date is how a pipeline stage silently
becomes out of scope.

**Loop termination** is explicit: dev leak rate improves by less than δ for k
consecutive iterations, or the call budget is exhausted. Never "until it looks
good enough".

#### The termination rule, pre-registered — δ = `max(0.005, 26/n_dev)`, k = 2, ceiling = 8 iterations (2026-08-12)

**Pre-registered before `port-loop`'s first call, and that timing is the whole point.** δ
and k decide how many iterations the arm runs, hence its cost, hence whether it clears
§11.3's 1.9× standard against a baseline whose number is already known. A δ chosen with that
number in view is a δ chosen to make the rung win or lose, which is the same objection §11.3
records against picking a cost threshold after the fact. So the values are fixed here, with
their derivation, and the derivation deliberately uses no `port-oneshot` result.

**It is a difference rule, not a level rule, and the distinction was nearly lost.** The rule
is *the dev leak rate **improves by less than** δ*, i.e. δ is a threshold on the
iteration-to-iteration **first difference**. It was at one point restated as "dev leak rate
**below** δ", which is a level rule and a different criterion — recorded because the
restatement is easy to make and the two fail in opposite directions:

- **A level rule cannot terminate on a plateau.** An arm that converges at a leak rate above
  δ never satisfies it, so termination falls through to the budget on every run where the
  rule matters most, and "the arm ran to convergence" becomes "the arm ran out of money" with
  nothing in the record distinguishing them.
- **A difference rule can terminate at any level, including a bad one.** Its failure is the
  mirror: an arm that stalls early stops early and reports a converged-looking result at a
  high leak rate. This is the failure this project prefers, because it is **visible in the
  headline** — the leak rate is reported next to the iteration count, so a stall shows up as
  a high number reached in few iterations. A budget exhaustion shows up as nothing.
- **The level rule also embeds a target the experiment has no basis for.** Choosing "below
  δ" means naming an acceptable absolute leak rate, which is a deployment decision about a
  corpus and a threat model, not a convergence test. §9.3's leak rate is a comparative
  measure here; treating it as a pass mark would be this project asserting a safety
  threshold it has not established.

The difference rule is kept, and the ceiling below is what closes its remaining gap.

**δ = 0.005 (half a percentage point of dev leak rate).** Derived from the measurement floor
upward, not from any observed value:

- **The noise floor is one span: `1/5254` = 0.019 percentage points** on es-meddocan's dev
  fold (`splits/es-meddocan.json`: 5,254 in-scope gold spans). A δ at or below that is
  meaningless — every iteration that moves one span clears it — so the floor sets a hard
  lower bound and δ must be some multiple of it.
- **δ = 0.005 is ~26 spans, which is 26× the noise floor.** The multiple is chosen so that
  "improved" means *a measurable amount of gold moved*, not *the fold twitched*: a threshold
  in the low single-digit spans would let ordinary iteration-to-iteration variation read as
  progress. 26 spans on a 250-document fold is about a tenth of a span per document — small
  enough that a genuinely productive iteration clears it easily, far enough above one span
  that clearing it is a statement about the fold rather than about the draw.
- **Correction — δ is not, and cannot be, the guard against memorisation.** As first written
  this bullet claimed 26 spans was "large enough that clearing it is not achievable by
  enumeration within one 40-span window". That is false, including on the corpus it was
  derived from: the §1.4 window is **40 spans on every corpus**, so an iteration that wrote
  one rule per span it had just been shown would move `40/5254` = **0.76 percentage points**
  on es-meddocan, which clears δ = 0.005 with room to spare. Memorisation-proofing would
  require δ > `40/n_dev` — 0.0076 here, and **12.3 points** on a 1,297-span fold, a
  threshold no honest iteration could ever clear. The guard against memorisation is
  Prohibition 2 and §9.4's screening of rule *shape*, which is where it belongs; δ's job is
  only to sit clear of the measurement floor. The rest of the derivation is unaffected —
  0.005 was fixed by the floor, not by this clause — but the clause was load-bearing in the
  wrong direction and would have been carried into the per-corpus rule below.
- **Smaller dev folds get a larger δ by formula, not by exception.** The δ has to be
  *derived the same way* across corpora or the arms are not comparable, which is not the
  same as being the same number: a fixed 0.005 collides with the noise floor of any fold
  under about 500 spans. The rule is therefore `δ_corpus = max(0.005, 26/n_dev)`, specified
  immediately below.
- **It is not tuned to a leak-rate level.** 0.005 is a distance, and the same distance is
  demanded of an iteration moving from 0.60 to 0.595 as from 0.20 to 0.195. That is the
  intended reading: the rule asks whether the loop is still finding things, not whether it
  has reached anywhere in particular.

#### δ is per-corpus: `δ_corpus = max(0.005, 26 / n_dev)` — pre-registered 2026-08-12

**The problem, computed before any arm ran on the corpus that has it.** GraSCCo ships 1,436
gold entities over 63 documents, of which 139 `NAME_TITLE` are out of scope (§9.1), leaving
**1,297 in-scope canonical spans**. A dev fold is a fraction of that, so δ = 0.005 there is
**1.62 spans** at a 25% dev fraction — below the two-span mark, i.e. inside the fold's own
noise. Any dev fraction up to about 38% leaves the fold under 500 spans, which is exactly the
collision the derivation above flagged as needing a recorded exception. It is recorded **now**,
before `de-grascco`'s split exists and long before its first call, because deciding it when
that arm runs would be choosing a stopping rule with that arm's numbers in view — the same
post-hoc selection §11.3 and the block above exist to prevent.

**The rule.** For every corpus, `δ_corpus = max(0.005, 26 / n_dev)`, where `n_dev` is the
in-scope canonical gold span count of that corpus's dev fold as recorded in
`splits/{corpus}.json` (`folds.dev.n_spans_in_scope`). The floor branch binds above
`n_dev` = 5,200; the ratio branch binds below it.

| Corpus | `n_dev` | δ_corpus | as pp | as spans |
|--------|---------|----------|-------|----------|
| es-meddocan | 5,254 (`splits/es-meddocan.json`) | **0.005** (floor branch) | 0.50 | 26.3 |
| de-grascco | 324 (25% of 1,297) | **0.0802** | 8.02 | 26.0 |
| de-grascco | 432 (⅓) | **0.0602** | 6.02 | 26.0 |
| de-grascco | 519 (40%) | **0.0501** | 5.01 | 26.0 |
| de-grascco | 648 (50%) | **0.0401** | 4.01 | 26.0 |
| es-carmen · ko-surro · en-n2c2 | not yet split | computed at split time | — | 26.0 |

**de-grascco appears at four fractions because its split does not exist yet, and the fraction
is not this block's decision.** Nothing in this document or `src/split.py` fixes fold
proportions — es-meddocan's came from that corpus's own official split, adopted unchanged
(`splits/es-meddocan.json`: `seed: None`, `stratification: None`). Whichever fraction the
GraSCCo split takes, δ follows from the formula, and **the binding value is the one computed
from the split file, not a number chosen alongside it.** The rows above are the formula
evaluated in advance so that no fraction can later be picked for the δ it produces. The same
holds for es-carmen, ko-surro and en-n2c2: their δ is computed **at split time** from
`n_spans_in_scope`, recorded in the run's `metrics.json` beside the leak rate, and not
selected.

**26 spans is the invariant; the rate is the derived value.** Every ratio-branch row above
lands on exactly 26.0 spans — that is the point of the formula rather than a coincidence of
the numbers. The standard being held constant across corpora is *how much gold has to move
for an iteration to count as productive*, and that is a count. A rate is that count divided
by a fold size, so a fold five times smaller must show a rate five times larger to represent
the same amount of found PHI. **A single fixed rate would be the thing that wobbles**: it
would silently demand 26 spans on one corpus and 1.6 on another, i.e. a strict standard on
the large fold and no standard at all on the small one. The per-corpus rates differ because
the folds differ, not because the criterion does.

**Where 26 comes from, restated because the formula hides it.** 26 is not an independent
quantity. It is `0.005 × 5254` — δ = 0.005 evaluated on **es-meddocan's dev fold** — so the
formula has that corpus baked in as its reference fold, and this must be acknowledged rather
than presented as a derivation from first principles. **A different reference would not have
given 26.** Had the ladder started on GraSCCo, the same reasoning (some tens of times the
one-span noise floor, small enough that a productive iteration clears it) would plausibly have
produced a different count, and every other corpus's δ would differ accordingly. What is
defensible is narrower than "26 is the right number": es-meddocan is the corpus whose split
was fixed first and whose derivation was done from its measured noise floor, so it is the one
fold on which the count has an argument behind it, and holding the count constant from there
is at least a *stated* choice with a traceable origin. The alternative — re-deriving a fresh
count per corpus — would let each corpus's threshold be chosen with that corpus in view,
which is the failure mode this whole block guards against. **The floor branch is the second
place the reference shows through:** `max(0.005, …)` keeps 0.005 from shrinking on folds
larger than 5,200, because below the noise-floor argument there is nothing left to justify a
smaller distance.

**k = 2 and the ceiling of 8 stay corpus-independent, and only δ is per-corpus because only
δ has a fold size in its denominator.** k counts consecutive draws of the §1.4 error sample,
which is 40 spans on every corpus (`config/sampling.yaml: n_error_spans`) — the sampling
variance k is meant to average over does not change with `n_dev`, so neither does k. The
ceiling is derived from cost (tokens per iteration against the call budget), and cost per
iteration is a property of the prompt and the masked fold, not of the gold count. δ alone
converts a span count into a rate, and a rate is the one quantity here that fold size can
distort.

**Three alternatives rejected, recorded so the choice is not re-opened as if it were open.**

- **Terminate on a span count directly** ("fewer than 26 newly-covered spans for k
  iterations"). Substantively identical — it is the same invariant stated in its own unit,
  and arguably the more honest statement of it. Rejected on blast radius rather than on
  substance: the termination rule is written in leak-rate terms in §3, referenced in those
  terms by §11.3, and `metrics.json` reports a leak rate, so switching units moves the spec,
  the cross-reference and the recorded fields together, for a result obtainable with no spec
  change at all. `max(0.005, 26/n)` yields the span-count criterion exactly, in the unit
  everything downstream already speaks.
- **Give GraSCCo a larger dev fraction so 0.005 stays viable.** Reaching 26 spans at
  δ = 0.005 needs `n_dev` ≥ 5,200, which GraSCCo cannot supply at any fraction — 1,297 spans
  in total. Even setting that aside, `splits/{corpus}.json` is a **shared schema read by one
  loader**, and one corpus at a different ratio means a smaller test fold on the corpus that
  singly carries the document-type axis, weakening the evaluation the split exists to
  protect. Bending the split to preserve a threshold's numeral inverts the dependency: the
  split is the measurement apparatus and δ is a parameter of the stopping rule.
- **Do not run `port-loop` on GraSCCo.** The cheapest option and the most damaging.
  GraSCCo **alone** carries the note-type axis (§7 — 63 documents across radiology,
  pathology, outpatient, progress and discharge material), and German is the **only filled
  cell** in §7's language × note-type product hypothesis. Dropping it leaves the whole ladder
  standing on Spanish, so "the port generalises across languages and note types" would rest
  on es-meddocan and es-carmen — one language, and CARMEN-I explicitly does not supply the
  note-type axis either (§7). A missing arm would not be a smaller claim; it would be the
  claim with its evidence removed.

**k = 2 consecutive iterations.** One below-δ iteration is not evidence of convergence — the
error sample is a seeded draw of 40 spans stratified by type (§1.4 of the prompt), so a
single iteration can land on a stratum the current rules already cover and produce a small
improvement for a reason unrelated to the arm having run out of ideas. Two consecutive
below-δ iterations draw two independent samples and require both to come back thin. k = 3
was considered and rejected on cost: each additional k is a full iteration of RuleAuthor plus
Auditor plus a scorer pass, spent purely to confirm a stop, and at k = 2 the confirmation
already costs one iteration in every run. k = 2 is also the smallest value for which
"consecutive" means anything at all, and a stopping rule whose k could have been 1 is a rule
that will be read as arbitrary.

**Ceiling = 8 iterations** — §11.3's neutral third option (the convergence test plus an
independent cap), adopted here for the agent arm as well as the human one. The ceiling is in
**iterations**, for §11.3's reason: it is the unit the call budget and the human arm share,
and the unit in which "the agent ran longer" is a statement rather than an assertion.

- **Cost structure, measured (2026-08-14) — the estimate this paragraph used to carry was low
  by about 16× per iteration and 14× at the ceiling.** The `port-oneshot` call's *token counts* — 14,071 prompt, 2,325 completion —
  are cost measurements rather than quality findings, so using them here does not smuggle
  0.560 in; they also calibrate the corpus at **3.8124 characters per prompt token** (53,644
  characters of assembled prompt against 14,071 recorded tokens), which is what turns the
  assembler's byte counts below into token counts. Measured on today's tree, by assembling the
  prompts without calling:

  | | calls | prompt | completion | total | × `port-oneshot` |
  |---|---|---|---|---|---|
  | round 1 (RuleAuthor only) | 1 | 14,071 | 2,325 | 16.4k | 1.0× |
  | round ≥2, RuleAuthor | 1 | 23,678 | ~3k | ~27k | |
  | round ≥2, Auditor | 250 | ~2.12M | ~75k | ~2.20M | |
  | **round ≥2, total** | **251** | **~2.15M** | **~78k** | **~2.22M** | **~135×** |
  | ceiling of 8 (1 + 7 iterating) | 1,758 | ~15.1M | ~0.5M | **~15.6M** | **~950×** |

  Prompt figures are measured; completion figures are estimates scaled from `port-oneshot`'s
  2,325 and are the only estimated column. **Two errors produced the old 135k-per-iteration
  and 1.1M-at-the-ceiling numbers, and both were in the derivation rather than in the
  measurements:**

  - **`splits/es-meddocan.json`'s `tokens.total: 110601` is a whitespace count, not a model
    token count.** `src/split.py` declares `TOKENIZER = "whitespace"` and computes
    `len(text.split())`; the field is honest about what it is and the paragraph read it as
    something else. The dev fold's 755,319 characters are ≈198k model tokens — 1.8× the
    whitespace figure. Any future reader of a `tokens` field in a split file owes it the same
    check.
  - **`docs/prompts/auditor.md` is re-sent with every one of the 250 calls, and was counted
    zero times.** The template is 26,135 bytes ≈ 6,855 tokens and is **80.7% of an average
    audit call** (audit prompts measured over the 250 dev documents: 8,093,249 characters
    total, mean 32,373, min 30,289, max 36,475). The dev fold's own text is the *minority* of
    what an iteration pays for; 1.71M of the 2.12M prompt tokens per round are the same
    template transmitted 250 times. The old paragraph modelled the Auditor as reading the fold
    once.

  Wall clock is a separate estimate and not measured: at `port-oneshot`'s observed latency,
  251 sequential calls put a round at roughly 40–80 minutes and seven of them at 5–10 hours.
  The loop makes the audit calls sequentially (`loop._audit_fold()`), so that figure is a
  property of the current implementation, not of the design.
- **So §11.3's 1.9× standard cannot be met by any iterating arm, and that is a finding about
  the standard rather than a reason to lower the ceiling.** 1.9× was set for `port-loop` vs
  `port-multi` — two arms of the same shape — and against a *one-call* baseline no loop of
  any length comes close. **The comparison `port-loop` vs `port-oneshot` is therefore not
  cost-thresholded; it is cost-*reported*,** and what the ceiling buys is that the reported
  figure is bounded in advance rather than being whatever the loop happened to spend. §11.3's
  threshold continues to apply where it was written, one rung up.
- **Eight rather than four or twelve.** Four would make the ceiling, not δ/k, the operative
  stopping rule in most runs — a cap that binds routinely is a budget masquerading as a
  convergence test, and the arm's claim would be about eight calls rather than about
  iteration. Twelve spends about **8.9M more tokens** (four further rounds at ~2.22M) on
  iterations that δ/k is very unlikely to reach. Eight leaves k = 2's confirmation cost
  affordable (six productive iterations before the two that stop it), which is the property
  that decides it.
- **The ceiling stays at 8 on the measured figures, and the correction is not a reason to move
  it.** The measurement multiplied every candidate ceiling by the same ~14×, so it does not
  reorder 4, 8 and 12 — the shape of the argument above is untouched, only its numerals. What
  the correction does change is that 4 now *saves* something substantial (~8.9M tokens against
  8), which makes the temptation to lower it real rather than notional. It is still refused,
  for the reason that was written before the cost was known: the objection to 4 was never that
  4 is cheap, it was that a cap which binds in most runs converts the arm's claim from "the
  loop stopped improving" into "the loop ran out of budget", and no change in the price of a
  round makes that conversion acceptable. Symmetrically, if 15.6M were genuinely unaffordable
  the honest response would be to say so and report the arm as budget-capped, not to relabel a
  budget as a convergence test. It is affordable, so 8 stands. **The levers that reduce cost
  without changing what the arm claims are elsewhere** — caching the constant audit prefix, or
  auditing a stratified sample instead of the full fold — and those are the places to look
  first, because unlike the ceiling they do not alter the stopping rule. Either would need to
  be pre-registered here before the arm runs, on the same footing as δ, k and the ceiling
  itself.
- **Why correcting this now is not post-hoc selection, and why the same edit later would
  be.** No `port-loop` result exists: `results/es-meddocan/R/sup-free/port-loop/` is empty and
  the arm has made no call. There is therefore nothing to select on — the correction cannot be
  serving a leak rate, a δ crossing, or a termination reason, because none has been observed.
  What is being fixed is an arithmetic error in the *derivation* (a whitespace token count read
  as model tokens, and a per-call template counted zero times), found by measuring the prompts
  the loop would send rather than by reading anything the loop produced. **After the first
  audit call the identical edit becomes post-hoc**, and not because the numbers would be less
  true: from that point a cost revision arrives with a partial result attached, and "we
  re-derived the ceiling" is indistinguishable from "we re-derived the ceiling having seen
  round 1". So the pre-registration rule for this section is a rule about *timing*: revisions
  to δ, k, the ceiling, or the cost model that grounds them are permitted while the arm's
  results directory is empty, and after that they are a new pre-registration for a new arm
  rather than a correction to this one.
- **Hitting the ceiling is a reported outcome, not a failure to record.** An arm that
  terminates on the cap has *not* satisfied the convergence test, and the two must be
  distinguishable in `metrics.json` — a run that stopped at 8 with the leak rate still
  falling is a different claim from one that stopped at 5 having converged. The termination
  reason is therefore recorded, and a ceiling-terminated run may not be described as
  converged.

  **Where that is enforced, now that the loop runs (2026-08-14).** In three places, none of
  which is a validator, because a validator implies the bad state exists and is caught. The
  reason is `ceiling` exactly when the cap is reached without k consecutive below-δ rounds,
  since `should_stop()` evaluates convergence first and independently — an arm whose k-th
  below-δ round happens to be its 8th is `converged`, and checking the cap first would
  reclassify it and understate the rule. `Termination.converged` is a *property* derived from
  the reason rather than a stored field, so `reason: ceiling` beside `converged: true` is
  unconstructable rather than rejected. And the block is written into every round's
  `metrics.json` by the writer that scored it, so the ending is in the published file and not
  only in a driver's return value. `loop.run_iteration()` reads the block back out of that file
  instead of recomputing it, and refuses to run a round after a stop of either kind — a ceiling
  stop ends the arm exactly as a convergence stop does, and the difference is what the record
  says about it, not whether it binds.

**What this licenses:** a stopping rule fixed before the arm ran, applied to `port-loop` and
to any later iterating arm, with the termination reason and the iteration count reported
beside the leak rate. **What it does not:** a claim that δ = 0.005 is where returns actually
diminish. It is a threshold chosen from a measurement floor and a cost structure, which is
the best available basis before the arm has run and is not the same as a measured
inflection point. If a later corpus shows the rule stopping arms that were still improving
steeply, that is a finding to report — not a value to retune mid-ladder. **"Fixed" means the
formula and its 26-span invariant are fixed, not that every arm sees the same numeral:** δ
varies across corpora by `max(0.005, 26/n_dev)` and only by that, so two rungs on the same
corpus always face the same threshold, and a rung on a smaller corpus faces the same
underlying standard expressed in its own fold's units. Editing the 26, the floor, or a
corpus's δ by hand after a run makes the rungs incomparable exactly as a mid-ladder model
change would (§4).

#### Call-to-call variance, and how a δ crossing is to be read against it — 2026-08-21

δ answers "is this round's improvement small enough to stop". It does not answer "is this
round's improvement larger than what the same prompt would have produced twice", and those are
different questions with different instruments. The first is a pre-registered threshold. The
second is a property of the *measuring apparatus* — how much of a round-to-round difference is
the model rather than the rules — and until 2026-08-21 the only figure available for it was an
accident: `port-loop` round 1 sent a prompt byte-identical to `port-oneshot-nofence`'s (same
`text_sha256`, same 14,071 prompt tokens, same model id) and got 31 rules against 27, 14
`rule_id`s in common, and leak-rate `fully_covered` 0.596 against 0.560. **Δ 0.0361 at a fixed
input, from n = 2.** That is more than seven times δ's floor.

**The interpretation rule.** A round-to-round change smaller than the measured call-to-call
variance is not an improvement, and may not be reported as one. Where a report says an
iteration improved, it says so against this figure; where the change is inside it, the honest
statement is that the round did not move the leak rate detectably. And **when termination fires
on δ, the report states whether the crossing is inside the measured variance** — a run that
stopped because two consecutive rounds moved less than δ, where δ is well below the noise the
instrument produces at rest, converged on the threshold rather than on the corpus, and a reader
cannot tell those apart from `reason: converged` alone.

**δ is not touched, and that is the point rather than a caveat.** Raising δ to sit above the
measured variance would be exactly the mid-ladder retune the timing rule above forbids: a
`port-loop` result now exists, so a revision to δ, k or the ceiling arrives with partial results
attached and is a new pre-registration for a new arm. What this clause adds is a sentence the
report must carry, not a number the harness compares against. Pre-registration constrains the
*decision procedure* — δ = `max(0.005, 26/n_dev)`, k = 2, ceiling 8, evaluated the same way it
was before any call was made — and it does not and cannot forbid learning more about the
instrument afterwards. A measurement that changes what a stop *means* while leaving untouched
when a stop *happens* adds grounds for interpretation. One that moved the threshold would be
choosing the threshold with the arm's numbers in view, which is the thing being prevented.
Anyone checking this can check it mechanically: no δ, k or ceiling value changes in this
section, and `src/porting/termination.py` is not edited by the commit that adds this clause.

**Measured by `tools/probe_call_variance.py`, recorded in `docs/notes/call-variance.md`.** The
probe sends round 1's prompt n times and reports the spread of the rule sets — rule count range,
`rule_id` overlap, layer distribution range. It is a probe in `tools/probe_prompt_cache.py`'s
sense: nothing imports it, no arm runs it, it creates no arm and no result directory, and its
draws are loaded out of a temporary directory so `results/` is untouched.

**The probe does not score, and the reason is a rule rather than a cost.** Scoring five draws
on dev would give the leak-rate spread directly — the figure this clause actually wants, instead
of a rule-set spread that is a lower bound on it. It is still not done. Five dev scores sitting
in a note become a channel: a person reads which draw scored better on dev, and what they
learned reaches the next prompt. Nothing in the harness carries it, so nothing in the harness
can refuse it. **That is dev overfitting, not a sealed-fold violation** — the test fold is not
touched and §6's seal is not at issue — and it is the same *kind* of thing as §6's ban on
choosing a dev checkpoint with test numbers: a selection made on data the selection was not
supposed to see, laundered through a human rather than through code. The rules the arms are
held to are permitted to see dev; a *measurement of the instrument* is not an arm and has no
such licence, and taking one anyway would make "dev is what rules are developed against"
quietly mean "dev is what prompts are developed against" as well.

So the leak-rate variance stays **unmeasured** and is not inferred from the rule-set spread.
The two are not proportional in any direction anyone has established: a draw could differ by
eight `rule_id`s that all target the same surfaces and score identically, or agree on every id
and differ in one regex that costs a hundred spans. Writing "≈0.03" from a Jaccard would be an
estimate presented as a measurement, which is the failure `docs/notes/mutation-full-runs.md`
exists to prevent one directory over. The one real figure — 0.0361 from n = 2 — is cited as
what it is: a single pair, at the same prompt, and the only leak-rate variance measured.

**Two bounds, in opposite directions, and they are about different quantities.** They are easy
to run together and the consequence of doing so is a misreading in the direction of
reassurance, so both are stated here and argued in the note. (a) The rule-set spread is a
**lower** bound on *what varies* — it shows the model does not answer the same question the
same way, and says nothing about how much leak rate that costs. (b) The same spread is an
**upper** bound on *round-to-round `rule_id` divergence*, because the probe's five draws all
carried round 1's §1.2 — the block is present and declares itself empty (`rules_empty: true`),
so the model invented 28–31 names with none of its own in view. From round 2 on, §1.2 carries
the previous round's complete file and the prompt asks for a complete file back, so naming is
continuation rather than invention and there is no reason to expect the same spread. So a
round-to-round `by_rule` overlap may be *compared* against these figures as a ceiling and never
as an expectation. Reading round 3 sharing half its rules with round 2 as "inside the measured
variance, therefore normal" is the inversion: an anchored round reaching an unanchored ceiling
is an event to explain, not a result to accept. The anchored spread is unmeasured and cannot be
measured until a round-2 prompt exists to repeat.

**If the five-draw leak-rate spread is later needed, the clause comes first.** It is a
foreseeable need — a second corpus, or a δ crossing that has to be defended — and the order is
not negotiable: a DESIGN clause explicitly permitting a *non-arm run* to score dev, naming what
may be recorded from it and what may not reach a prompt, is written and committed before any
such run is made. Not afterwards with the numbers in hand, for the reason the timing rule above
gives about δ.

#### Predicted improvements for rounds 4–8, and the reporting obligation when termination fires — written 2026-08-24, before round 4 runs

**Why this is written now.** Two `port-loop` improvements exist and they decay steeply. The
extrapolation below says the loop will keep running for three or four more rounds and that none
of those rounds' improvements will be distinguishable from the variance the clause above
measured. That is a claim about rounds that have not happened, and it is worth nothing unless it
is on the record before they do — written afterwards it is not a prediction but a description,
and a description of a decay one has already seen is unfalsifiable. So it is recorded here,
`results/es-meddocan/R/sup-free/port-loop/iter4/` empty, with the arithmetic exposed so that a
reader can check both the prediction and its failure.

**It is not a pre-registration and changes no decision procedure.** δ, k and the ceiling are
untouched, for the reason the clause immediately above gives and does not need restating; nothing
in `src/termination.py` is edited by the commit that adds this clause, and it can be checked the
same mechanical way. What this adds is a number to be wrong about and one sentence a report must
carry.

**The observed decay, from the two improvements that exist.** Both are `fully_covered`, which is
the mode `loop._leak_rates()` reads and therefore the mode the stopping rule runs on.

| | dev leak `fully_covered` | improvement | as spans of 5,254 | vs δ = 0.005 | vs measured variance 0.0361 |
|---|---|---|---|---|---|
| round 1 | 0.596117 | — | — | — | — |
| round 2 | 0.307195 | **0.288923** | 1,518 | 57.8× | 8.0× |
| round 3 | 0.228588 | **0.078607** | 413 | 15.7× | 2.18× |

The ratio of the second improvement to the first is **0.27207**, i.e. a decay of about **1/3.68
per round**. That is one ratio from two gains — n = 1 — and a geometric model fitted to it has
exactly as much support as that sentence implies. It is used because it is the only decay the arm
has shown and because a prediction has to be committed to a functional form to be wrong; it is
not used because there is evidence the decay is geometric.

**The prediction: gain(n) = 0.078607 × 0.27207^(n−3).**

| round | predicted improvement | as spans | predicted leak | > δ = 0.005 | inside variance 0.0361 |
|---|---|---|---|---|---|
| 4 | **0.021386** | 112.4 | 0.2072 | yes (4.3×) | **yes** (0.59× of it) |
| 5 | **0.005819** | 30.6 | 0.2014 | yes, by 1.16× | **yes** (0.16×) |
| 6 | **0.001583** | 8.3 | 0.1998 | no | **yes** (0.04×) |
| 7 | 0.000431 | 2.3 | 0.1994 | no | yes |
| 8 | 0.000117 | 0.6 | 0.1993 | no | yes |

**The headline prediction, stated so that it can fail.** Round 6 is the first round below δ,
round 7 is the second, so k = 2 is satisfied at round 7 and the arm stops with
`reason: converged`, `iterations: 7`, one round short of the ceiling. And **every improvement
from round 4 onward is inside the measured call-to-call variance** — round 4's predicted gain is
0.59× of 0.0361, and rounds 5–8 are an order of magnitude below it. So the predicted ending is a
loop that runs four more rounds, spends roughly 4 × 2.22M ≈ 8.9M tokens doing it, and produces
no round whose improvement the interpretation rule above permits calling an improvement. The
termination rule keeps it running; the variance figure says the running is not visible.

**Where the prediction is fragile, computed rather than hedged.** The whole thing turns on one
ratio r, so the round at which δ is first crossed is a function of r alone:

Gains fall monotonically for any r < 1, so convergence at round n is bound by one condition —
gain(n−1) < δ — and the boundaries are exact:

- r < 0.0636 → round 4 already below δ, k = 2 at **round 5** (a collapse, not a decay).
- r < 0.2522 → round 5 below δ, so k = 2 is satisfied at **round 6**. Observed r is 0.2721, only
  **7.9% above** this boundary. Round 5's predicted 0.005819 is 1.16× δ, i.e. 30.6 spans against
  δ's 26 — under five spans of headroom on a fold of 5,254. Convergence at round 6 is therefore
  inside the prediction's own slack and would not count as a miss.
- r < 0.3992 → k = 2 at **round 7**. This is the prediction.
- r < 0.5022 → k = 2 at **round 8**, converged on its last permitted round.
- r ≥ 0.5022 → no two consecutive rounds below δ within the cap, and the arm terminates
  `ceiling` at 8. That needs the decay to be **1.85× weaker** than observed.

So the prediction in its falsifiable form is: **`converged` at round 6, 7 or 8 — not `ceiling`.**
A `ceiling` stop falsifies it, and so does any single round 4–8 whose improvement exceeds 0.0361,
because such a round would be both a real improvement and evidence the decay is not geometric.
Either outcome is a result and is to be reported as one, not absorbed.

**The relaxed lower bound is already past this point and predicted to fall below δ at round 4.**
Its gains are 0.256757 (round 2) and 0.030453 (round 3) — a ratio of 0.11861, decaying nearly
2.3× faster than the headline — putting round 4 at **0.003612**, below δ. Round 3's relaxed gain
was already inside the variance and was reported as not an improvement. The two modes are
therefore predicted to disagree about round 4 in the way that matters: the headline gain clears
δ while the lower bound does not, and neither clears the noise. CLAUDE.md makes `fully_covered`
the headline and the stopping rule follows it, so the arm continues on the headline's arithmetic
while its lower bound has stopped moving.

**The reporting obligation, in the same form as the ceiling/converged distinction.** §3 already
requires that a ceiling stop be distinguishable from a convergence stop in `metrics.json`, and
`Termination.converged` makes the contradictory record unconstructable rather than rejected. This
is that requirement's counterpart one level up, on the report rather than on the record, and it
is **mandatory in the same way**: whenever termination fires, the report that announces it
**must** state, for both kinds of stop,

1. **which ending it was** — `converged` or `ceiling`, taken from the `termination` block and not
   from the shape of the numbers; and
2. **whether the final improvements are inside the measured call-to-call variance**, with the
   figure cited and the comparison shown — for a `converged` stop, the k improvements that
   satisfied the rule; for a `ceiling` stop, the last k improvements, which by construction did
   *not* satisfy it and whose size is exactly what tells a reader whether the cap cut off a
   productive arm or an already-flat one.

A report that gives the reason and omits the comparison is incomplete, and so is one that says
"the improvement was small" without the variance figure beside it. The reason this is an
obligation on prose and not a field in `metrics.json` is the one the clause above states: the
variance figure is a single measured pair from a note, not a quantity the harness holds, and
writing it into the record would give a published file a number with no measurement behind it.
Prose can carry "0.0361, n = 2, one pair" honestly; a JSON field cannot.

**And the reason the obligation binds `ceiling` too, though the clause above only named δ.** A
δ-fired stop inside the variance band converged on the threshold rather than on the corpus, which
is why that case was named first. But the mirror case is just as unreadable from the reason
alone: an arm capped at 8 with its last two improvements at 0.03 was still moving in a way the
instrument cannot resolve, and an arm capped at 8 with its last two at 0.15 was cut off
mid-descent. `reason: ceiling` says the same thing about both. So the comparison is owed on every
stop, and a stop is the one moment at which the arm's result becomes the thing that gets cited.

#### Step or decay: the criterion for reading round 5, and why no second prediction is made — written 2026-08-24, before round 5 runs

**No numeric prediction is made for round 5.** The clause above predicted rounds 4–8 from a
geometric decay and round 4 came in at 0.033498 against 0.021386 predicted — 1.57× high, and the
diagnosis (§7, this date) is that the functional form was wrong about the **kind** of move a round
can make, not about its size: a layer that had covered nothing woke up and supplied 89% of the
round. Re-fitting the same exponential to three points and predicting round 5 from it would fail
for the identical reason, and it would fail *less visibly*, because a re-fit after one miss looks
like it learned something. So what is fixed in advance instead is only the rule for classifying
the round after it runs — which is the part that can be gamed by choosing it afterwards, and
therefore the part that has to be written down first.

**The criterion.** Let `L` be the round's net reduction in leaked spans (`fully_covered`,
`modes.fully_covered.leak.leaked`), and let `t` be the `phi_type` with the largest reduction of
its own `by_type.leaked`. Round 5 is a **step** if all three hold:

- **S1 — concentration.** `t`'s reduction is ≥ 50% of `L`.
- **S2 — novelty.** Some layer `ℓ` has `complementarity.by_type[t].layers.covered[ℓ]` in round 4
  at ≤ 10% of its round-5 value. Written as `prev ≤ 0.1 × now` rather than as a ratio so that
  `0 → n` is included rather than undefined.
- **S3 — sufficiency.** That same `ℓ`'s round-5 covered count within `t` is ≥ `t`'s reduction.
  Without this a layer contributing three spans could claim a hundred-span gain.

If S1 holds but S2 or S3 does not, the round is a **decay**: the gain came from mechanisms that
were already contributing. If S1 does not hold, the round is a **decay** by dispersion — no single
type carried it. If `L ≤ 0`, or if `t`'s share falls in 45–55%, the round is **unclassifiable**
and is reported as that. A binary that always answers is a binary that answers when it shouldn't.

**Calibrated against the three transitions that already exist, which is the only reason to trust
the thresholds at all.** Applying it backwards: round 2 → decay (top type `NAME`, share 0.346, S1
fails); round 3 → decay (top type `ID`, share 0.516, S1 **passes**, S2 fails because
`regex_checksum` went 180 → 394 and was already active); round 4 → **step** (top type
`LOCATION_AREA`, share 0.892, `gazetteer` 0 → 284 with 284 ≥ 157). Two decays and one step, and
the one step is the round independently diagnosed as one before this criterion was written.

**The load-bearing condition is S2, not S1, and this is worth stating because the 50% threshold
looks like the substance.** Round 3 clears S1 and round 4's margin over it is enormous, so
concentration on its own separates nothing — it is novelty that does the work. That also means the
45–55% abstention band is a guard on the weakest condition rather than on the decisive one, and a
round rejected only by S1 while passing S2 and S3 should be reported with all three values shown
rather than filed as a decay on a single number.

**This classification is orthogonal to the variance band and does not replace it.** A step can be
smaller than 0.0361 and a decay can be larger; round 4 was a step whose gain was inside the band
(0.928×). Both readings are owed on every round: whether the gain is resolvable by the instrument,
and whether it came from a new mechanism or an old one. Neither answers the other, and the
termination rule reads neither — it reads the first difference and nothing else.

**What may not happen after round 5 runs.** These three conditions and the two thresholds are not
revised in light of round 5's numbers. If round 5 comes out unclassifiable, that is the recorded
outcome and not an occasion to move a boundary; if the criterion turns out to be the wrong
instrument, the finding is recorded and a replacement is written before round 6, in that order.

#### The ceiling of 8 was not reachable, and the thing that bounded it was a botocore default — found 2026-08-24, round 5's two failed attempts

Round 5 was attempted twice and both attempts died in the same place: `ReadTimeoutError` on the
single RuleAuthor call, after all 250 Auditor calls had completed. The cause is not a network
fault, and the numbers say so — that call took **37.0s, 39.8s, 51.0s, 56.1s** in rounds 1 through
4, against botocore's default `read_timeout` of **60s**.

**The climb is structural, not incidental.** §1.2 of each round's prompt carries the previous
round's entire rule file, and the prompt asks for a complete file rather than a patch, so every
round's reply must restate everything before it and add to it. Response size went 7209 → 8738 →
10959 → 11985 chars over four rounds. Generation time rises with it, monotonically, by
construction of the loop. **So the transport's patience was a ceiling on the number of rounds an
arm could run, and it sat at round 5 — three rounds below the pre-registered ceiling of 8.**

**This is a result about the prediction written above, of a kind that prediction could not have.**
That clause predicted `converged` at round 7 and listed gains for rounds 4 through 8. Rounds 6, 7
and 8 were not reachable when it was written; it was predicting the behaviour of rounds the
harness could not execute, and no amount of care about δ, k or the decay constant would have
surfaced that, because the limit was in neither the rule nor the corpus. The termination rule
itself is unaffected — it reads first differences and its ceiling is a policy about how many rounds
*may* run, not a claim about how many *can*. What is affected is any reading of `reason: ceiling`
as evidence that an arm was still improving at 8: an arm could equally have stopped at 5 with a
traceback. There is now a third way for a loop to end, alongside `converged` and `ceiling`, and it
is not in the `termination` block because it produces no metrics file at all.

**The fix and its cost.** `READ_TIMEOUT_SECONDS = 300` is now set explicitly in
`src/llm/bedrock.py`, chosen against the projection (+6s/round from round 4 puts rounds 5–8 near
62/68/74/80s, so ~4× the worst case an eight-round arm reaches) rather than against the one
observation. It is a transport change and not a call change — no prompt byte moves, the file is
not in the window (§6.3), and rounds either side of it are the same arm. Each failed attempt cost
**250 Auditor calls, 2,318,577 prompt tokens, 26,085 completion tokens, 980.6s** and produced
nothing — those are the first attempt's figures, and splitting the round's log lines by attempt
(2026-08-25, for the arm's wrap-up) shows the calls and the prompt tokens are identical across the
two while the second attempt's completion tokens and wall are 25,472 and 1,135.4s, so "each" is
exact for the first two figures and approximate for the last two; the round's abandoned totals are
500 calls, 4,637,154 prompt tokens, 51,557 completion tokens and 2,116.1s — and `run_iteration` computes a round's cost from its own in-process calls, so those two
attempts appear in `agent_calls.jsonl` and in no `metrics.json`. The arm's published
`cost_to_date` therefore understates true spend by two such attempts. Recorded here because
CLAUDE.md makes cost a headline alongside quality, and a spend that produced nothing is exactly
the figure a cost column is tempted to lose.

**A second defect, found while fixing the first, and worse in kind.** `MAX_ATTEMPTS = 1` was being
passed to botocore's `retries={"max_attempts": ...}`, which counts retries *after* the initial
request — so the setting that exists to guarantee "one call is one call" was permitting **two**.
The key that means what this project claims is `total_max_attempts`. The test that should have
caught it asserted `MAX_ATTEMPTS == 1`, a fact about a constant rather than about the transport,
and it passed throughout. **Every call in every arm run before this date went out under a
transport permitting two HTTP attempts.** `llm_calls` remains a truthful count of the inferences
the loop *intended*; what is unknowable is whether any were retried underneath, because a botocore
retry leaves no line in `agent_calls.jsonl`. For those rounds the HTTP request count is a lower
bound. No correction is possible after the fact and none is invented.

**Where that spend now goes, and why round 5's is not being put there.** The figures above are the
reason `metrics.json` schema 9 has an `abandoned_spend` block (`scorer.REQUIRED_ABANDONED`,
2026-08-24): a round's `cost` is measured around the attempt that succeeded, so an arm that burned
two complete audit passes publishes the figure of an arm that burned none, and the block is what
ends that. Round 5's own two attempts are **recoverable** — every one of their 500 Auditor calls
logged its cost to `agent_calls.jsonl` — but they are **not backfilled**, and the decision is
§5.5's one-writer rule rather than a difficulty: editing `iter5/metrics.json` now would make
something a second writer of an already published file, which is the property that makes the two
copies of a round's score trustworthy in the first place. So `iter5/metrics.json` carries no
`abandoned_spend` block, its absence there means "a writer that had no such field" rather than
"nothing was abandoned", and `schema_version: 8` on that file is what says which of the two it is.
The numbers stay here, where they were written before the field existed.

Two of the six figures the block would have held are not merely unrecorded but **unmeasurable**,
and this is the same hole as the paragraph above rather than a new one: each attempt's RuleAuthor
call died in `ReadTimeoutError`, so it returned no usage report at all and its prompt tokens were
spent and cannot be recovered. That is what `calls_unmeasured` counts — for round 5 it would read
2 — and it is why the four token and time totals in any such block are lower bounds whenever it is
above zero.

**Round 6's first attempt, and the gate that was wrong until it fired — 2026-08-24.** The first
attempt at round 6 took a Bedrock `500 Internal Server Error` on Auditor call 123 of 250. Nothing
retried it and nothing was supposed to: `total_max_attempts=1` is the pin above, and a botocore
retry would make `llm_calls` a count of intentions rather than of requests. So the round died with
122 calls paid for — 1,133,206 prompt tokens and 490.9 s — and re-running it is the recovery
§5.5.2 sanctioned.

What that exposed is that the new block's *gate* was the draw count, and the draw count counts
**preserved audit reports**. An attempt that dies partway through the audit writes no report, so
`next_draw` returned 1, `draws_before` was 0, and `abandoned_spend` came back `None` — publishing,
under the absent-means-unrecorded convention, a round that looked untouched. Worse, it was
undetectable in the file: absence is *defined* to carry no claim, so nothing about the record would
have looked wrong. The gate is now the union of the two records — a preserved draw, **or** a logged
line, is enough — and one consequence is worth stating plainly: `attempts_abandoned` is a **lower
bound** where `calls_abandoned` is not. Two attempts that both die mid-audit still read 1, because
nothing in the log marks where one attempt ended and the next began, and recovering the true count
would mean dividing lines by the fan-out and assuming the fan-out never changed. A visible lower
bound is preferred to an invisible guess; the exact count is in the `draw*/` listing and the round's
lines in the call log, both of which a reader has.

Round 6's abandoned spend therefore **is** recorded, in the round that finally scores — which is
the difference from round 5's, two paragraphs up. Round 5's attempts predate the field and their
figures stay in this document; round 6's attempt is contemporaneous with it, so the block is written
by the first writer of that file rather than backfilled by a second.

**The generalisation worth keeping.** Both defects are inherited defaults meeting a documented
intention that was never checked against behaviour. The retry pin was audited because it would
corrupt a published number; the timeout was not audited because it corrupts nothing and merely
stops the work — and it is the one that cost a round. **An inherited default is not a decision,
and the defaults that bound an experiment are worth enumerating before one of them fires.**

#### Three ways a loop ends, and only two of them are in the `termination` block — 2026-08-24

The vocabulary had two endings. There are three, and the third was discovered by hitting it.

| ending | recorded where | what it says about the arm |
|---|---|---|
| `converged` | `termination.reason`, with `converged: true` | k consecutive improvements below δ. A statement about the arm. |
| `ceiling` | `termination.reason`, with `converged: false` | the cap was reached without that happening. A statement about the budget. |
| **incomplete** | **nowhere** — no `metrics.json` exists for the round | the round did not finish. A statement about the harness. |

**Incomplete is neither of the other two, and the distinction is not a technicality.** Both recorded
endings mean the loop *decided* to stop: one because the rule fired, one because the budget ran
out, and §3 already forbids describing the second as the first. An incomplete round is a round
whose leak rate does not exist, so there is nothing for the rule to read and nothing for the cap
to count. It cannot be filed under `ceiling` — the cap was not reached — and it obviously cannot be
filed under `converged`. `should_stop` never sees it: the exception is raised inside
`run_iteration` before any rate is produced, so `Termination` is never constructed and no
`termination` block is written. **An arm that ends this way ends with its last completed round's
metrics as the newest file in the tree, which is indistinguishable from an arm that is simply
paused.** That is the reporting hazard: the two other endings announce themselves in a published
file, and this one is visible only as an absence.

Nothing is added to `termination_reasons` in `config/naming.yaml` for it. A reason value would have
to be written into a `metrics.json`, and the defining property of this ending is that there is no
`metrics.json` to write it into; inventing a round-6 metrics file whose only content is "round 6
did not happen" would make an absent measurement look like a present one. The record belongs in
prose and in `agent_calls.jsonl`, which does carry the calls the failed attempts made.

**The period the pre-registered ceiling of 8 was unreachable, and why.** From `68484be`
(2026-08-08), the commit that introduced the Bedrock client, to `55e7b35` (2026-08-24), the client
inherited botocore's default `read_timeout` of 60s. §1.2 of each round's prompt carries the
previous round's whole rule file and asks for a complete file rather than a patch, so RuleAuthor
wall time rises monotonically **by construction of the loop** — 37.0s, 39.8s, 51.0s, 56.1s across
rounds 1–4. Round 5 crossed 60s and failed twice in the same place. So for the whole of that
period the reachable number of rounds was bounded by the transport rather than by δ, k or the cap,
and **for this arm the bound sat at 5 — three rounds below the pre-registered ceiling of 8.** The
bound was never visible in any config: it was the interaction of a default with a growth rate, and
neither of those is a parameter of the experiment.

**Round 4's prediction forecast rounds that could not have run.** The clause above predicted
`converged` at round 7 and listed expected gains for rounds 4 through 8. Rounds 6, 7 and 8 were
not executable when it was written. This is not a flaw in the prediction's reasoning and no
re-derivation of δ, k or the decay constant would have exposed it, because the limit was in neither
the rule nor the corpus. It is a demonstration that a pre-registration can be internally sound and
still range over states the apparatus cannot reach — which is an argument for enumerating the
apparatus's defaults alongside the rule's constants, not for weakening the rule.

**The pre-registration stands, and the reason is that the ceiling policy was not touched.** δ, k
and the ceiling of 8 are unchanged; the fix was `READ_TIMEOUT_SECONDS` in `src/llm/bedrock.py`, a
transport constant that moves no prompt byte and is not a window file (§6.3). §3's prohibition on
revising the decision procedure mid-arm is therefore not engaged: nothing in the decision procedure
moved, and rounds either side of the fix are the same arm. What does change is the *reading* of one
of its outcomes. `reason: ceiling` may no longer be taken as evidence that an arm was still
improving when its budget ran out, because until this date an arm could equally have ended at 5
with a traceback — and if it had, the `termination` block would have said nothing at all.

#### The two-HTTP-attempts defect, stated as a limitation — 2026-08-24

Recorded as a limitation rather than a fixed bug, because the fix does not reach backwards. From
`68484be` (2026-08-08) to `55e7b35` (2026-08-24), `MAX_ATTEMPTS = 1` was passed to botocore's
`retries={"max_attempts": ...}`, which counts retries *after* the initial request. Every call in
every arm run in that window went out under a transport permitting **two** HTTP attempts.

**What is unaffected, stated precisely, because this is where a limitation gets overstated.** The
*content* of every response is unaffected. A botocore retry re-sends the same request and returns
one response to the caller; the loop received exactly one reply per `invoke`, and `response_sha256`,
`response_chars`, the rule files, the audit reports, every span and every leak rate are what they
would have been under a single-attempt transport. `llm_calls` is likewise unaffected: it counts the
inferences the loop *intended* and it counted them correctly. **No published measurement of quality
is in question.**

**What is unknowable, also stated precisely: the number of HTTP requests actually issued, and
therefore the true spend.** A botocore retry leaves no line in `agent_calls.jsonl` — the log is
written by `invoke` once per successful call — so for every round run before this date the recorded
request count is a **lower bound**, and the token and dollar figures derived from it are lower
bounds too. Note what does *not* follow: a retried request is billed, so an undetected retry
inflates real cost above the recorded figure. The direction of the error is known even though its
size is not. No correction is possible after the fact and none is invented.

**The retry was firing, and this is measured rather than inferred.** Round 5's two failed attempts
give the direct evidence. In each, the last Auditor call is timestamped in `agent_calls.jsonl` and
the traceback is timestamped by the run's output file:

| attempt | last Auditor logged | `ReadTimeoutError` written | elapsed |
|---|---|---|---|
| 1 | 04:09:49Z | 04:11:51Z | **122s** |
| 2 | 04:57:17Z | 04:59:19Z | **122s** |

Two 60s read timeouts plus ~2s of local prompt construction. A single attempt would have shown
~62s. Identical to the second across two independent runs, which is what makes it evidence rather
than a coincidence — and it is a rare case where a *failure* measured the transport more precisely
than any success could, because a success reveals only that the response arrived in time.

**The consequence for wall time, which is the figure most likely to be quoted without this
caveat.** `wall_time` is measured around `invoke` and therefore includes any retry botocore
performed inside it. Every per-round and per-arm wall time recorded before this date may contain
retried requests; a round whose wall time looks anomalous against its neighbours may be recording
one retry rather than one slow generation, and the two are indistinguishable from the log. This
matters for the cost-per-arm comparison CLAUDE.md requires, because it means wall time is not a
clean proxy for compute in those rounds. It does not matter for the *quality* comparison, and the
two should not be caveated together.

**Scope.** Every arm, every corpus, every round before `55e7b35` — `port-oneshot`,
`port-oneshot-nofence`, the variance probe of `docs/notes/call-variance.md`, the model-family probe
of `docs/notes/baseline-model-family.md`, and `port-loop` rounds 1–4. Round 5 is the first round
run under `total_max_attempts`, so it is the first round whose recorded request count is exact.

#### The stopping rule is unsigned, so a regression counts toward convergence — recorded 2026-08-24, before it can fire

**Round 5's result first, because it is what makes this reachable rather than hypothetical.** Dev
leak `fully_covered` went 0.195089 → **0.233536** (1,025 → 1,227 leaked spans), a first difference
of **−0.038447**: the arm's first regression. Relaxed went 0.154739 → 0.192234. F1 moved +0.0014 and
would have called the round neutral, which is CLAUDE.md's headline rule earning its keep on a real
round rather than in the abstract. Under the criterion fixed before the round ran, round 5 is
**unclassifiable** — its own `L ≤ 0` clause fires at `L = −202` — and that is the recorded outcome,
per the clause above that says it would be. The regression's magnitude is 1.065× the measured
call-to-call variance of 0.0361 (n = 2, one pair), so it is at the edge of what the instrument
resolves rather than clearly outside it.

**The mechanical fact.** `should_stop` evaluates `all(g < d for g in gains[-k:])`. That comparison
is **signed**: there is no `abs()`, and none was intended — the rule as pre-registered asks whether
the leak rate *improved by at least δ*, and a round that got worse did not. So −0.038447 < 0.005 is
a below-δ round, and the improvements list now reads `[0.288923, 0.078607, 0.033498, -0.038447]`
with one of k = 2 already satisfied.

**What follows, stated before the round that would do it.** If round 6's first difference is also
below δ — of *either* sign, including a second regression — the arm records
`reason: converged, iterations: 6` while its final leak rate is worse than round 4's. That record
would be correct against the rule and misleading against the word. The rule's own defence of this
is already in §3: a difference rule "can terminate at any level, including a bad one", and this is
that failure mode with the sign flipped, which the clause anticipated in level and not in
direction.

**The rule is not being changed.** For the reason the pre-registration clause gives at the top of
§3 and the reason `#### Step or decay` gives about its own thresholds — neither is restated here.
Nothing in `src/termination.py` is edited by the commit that adds this clause, checkable the same
mechanical way.

**The reporting obligation, fixed now so it is not composed after the fact.** This is a third
requirement on the report alongside the two in `#### Predicted improvements`, and it binds only
when `converged` fires with any negative value among the k improvements that satisfied the rule.
When it does, the report **must** state, in addition to the two obligations already standing:

1. **That the final round was not the arm's best round**, naming the best round by dev leak
   `fully_covered` and giving both figures. Not "the arm converged at 0.23" but "the arm converged
   at 0.234 at round 6, having reached 0.195 at round 4".
2. **The whole trajectory**, every round's leak rate and every first difference, in the report
   itself and not by reference to a directory. A converged-on-regression arm cannot be read from
   its endpoint, and a reader given only the endpoint has been given the one number that misleads.
3. **That the published result is nonetheless the final round**, per §5.5, with the reason — so
   that the gap between "best" and "published" is visible as a design choice and not as an
   oversight.

This does not license publishing the best round instead. Choosing a round after seeing the leak
rates is choosing a result, which §5.5 refuses; the obligation is to make the cost of not choosing
visible.

**Candidates for the next arm's pre-registration — recorded as candidates and applied to nothing.**
Listed so that the choice is made against a menu written while the problem was fresh, and none of
these is in force for any arm now running:

- **Absolute-value rule.** `all(abs(g) < d ...)`, so a regression is a *change* rather than a
  non-improvement and does not count toward convergence. Cost: an arm oscillating by more than δ
  never converges and always ends at the ceiling.
- **Non-negativity guard.** Keep the signed comparison but require every one of the k rounds to
  have `g ≥ 0` for `converged`; a negative gain in the window forces the reason to something else.
  Needs a fourth reason value in `naming.yaml` (`regressed`, say), which is the honest cost — a new
  reason is a new outcome the paper has to report on.
- **Monotone-best rule.** Terminate on k below-δ rounds *measured against the best round so far*
  rather than against the immediately previous one. Cost: it changes what `improvements` means, and
  the list stops being first differences, which §3 requires it to be for checkability.
- **Do nothing and report it.** Keep the rule and rely on the obligation above. Cost: the record
  says `converged` on an arm that got worse, and every reader who sees the field and not the prose
  is misled. This is the current state and it is a candidate rather than a default.

The choice between them is a decision about the *next* arm and it is not made here, because making
it here while `port-loop` is mid-flight is what §3's pre-registration exists to prevent.

#### Round 6 did not fire it — recorded 2026-08-24, after the round

The obligation above did not bind, and the clause is left standing because whether it binds is a
fact about round 7 as much as it was about round 6. Dev leak `fully_covered` went 0.233536 →
**0.176247** (1,227 → 926 leaked spans), a first difference of **+0.057290**; relaxed went 0.192234
→ 0.133422. `improvements` reads `[0.288923, 0.078607, 0.033498, -0.038447, 0.057290]`, so the k = 2
window holds one below-δ round and one clear improvement, `all(g < d ...)` is false, and
`termination.reason` is `null`. The arm continues to round 7 with the ceiling at 8.

Two consequences worth stating because they are easy to leave implicit. **The regression did not
compound**, so nothing was published under a `converged` that contradicted its own trajectory —
the three-part obligation above was written for a round that did not happen, which is the outcome
a pre-registration wants and not evidence it was unnecessary. And **round 6 is now the arm's best
round as well as its latest** (0.176247 against round 4's 0.195089), so the gap between "best" and
"published" that §5.5 makes visible is currently zero. Neither fact retires anything: the window
still contains a negative gain, and a below-δ round 7 fires `converged` on a two-round window whose
other member is round 6's improvement, which is the rule working as written.

**The classification, under the criterion fixed before round 5 and not revised since.** Round 6 is
a **decay**. `L = 301`; the top type is `AGE`, whose leaked count went 294 → 84, a reduction of 210
and a share of **0.698**, so S1 passes and passes well outside the 45–55% abstention band. S2 and
S3 cannot be satisfied by the same layer: within `AGE` the only layer covering anything is
`regex_checksum`, which went 227 → 437 — a real contribution, easily sufficient for S3, but nowhere
near S2's `prev ≤ 0.1 × now` (227 against 43.7), because it was already the layer doing the work.
The three layers that do satisfy S2 do so vacuously at 0 → 0 and fail S3 by covering nothing. So
the gain came from a mechanism already contributing, which is what decay names. All three values are
shown per the clause's own instruction, even though the rejection here is by S2 and S3 rather than
by S1.

**And the variance reading, which is owed separately.** The gain is 1.587× the measured
call-to-call variance of 0.0361 (n = 2), so unlike round 4's 0.928× it sits outside what the
instrument resolves. Both readings are given because neither answers the other: round 6 is a gain
the instrument can see, produced by a mechanism that was already running.

#### The arm ends at round 8, and round 7 cannot end it — recorded 2026-08-25, before round 7

This is arithmetic on the rule as pre-registered, not a forecast, and it is written down before the
round because a ceiling ending recorded only after it fires is indistinguishable from a stopping
condition noticed on the arm's own data. δ is `max(0.005, 26/5254 = 0.004948) = 0.005` — the floor
binds and the span rule does not, which is worth stating once because it means δ is a constant here
and not a function of `n_dev`.

**Round 7 cannot fire `converged`.** At k = 2 the window is `gains[-2:]`, which for round 7 is
`[+0.057290, g₇]` — round 6's improvement is *in* the window, and 0.057290 > δ, so
`all(g < d for g in gains[-k:])` is false whatever g₇ turns out to be. And `iterations >= ceiling`
is 7 ≥ 8, false. So `termination.reason` is `null` at round 7 by construction: **no round-7 result
stops the arm, including a leak rate that does not move at all.** The one-below-δ-round window the
note above describes is the reason — round 6 improved, and an improvement in the window is what
makes the next round unable to converge on its own.

**Round 8 ends it, one way or the other.** The window is `[g₇, g₈]`, and `converged` needs *both*
below δ. Anything else reaches `iterations >= ceiling` with 8 ≥ 8 and is `ceiling`. Since the
convergence test is evaluated first and never overridden by the cap (`src/termination.py`), a round
8 that satisfies it is recorded as `converged` even though the cap also became true — that ordering
is §3's and is not affected by any of this. **The arm's length is therefore already determined at
eight rounds; only the reason is open.**

**A ceiling ending is the expected ending, and this is where that is recorded.** Five gains in,
one is below δ and it is the negative one; the other four are 0.289, 0.079, 0.033 and 0.057, none
of them within an order of magnitude of 0.005. Convergence at round 8 requires two consecutive
gains below 0.005 from a trajectory that has not produced one non-negative gain that small, so the
honest expectation is `ceiling`. §3 already forbids calling that convergence and
`Termination.converged` is derived from `reason` rather than settable, so the prohibition is
mechanical; what is added here is that the outcome was **anticipated in writing beforehand**, so
the paper's account of it cannot be read as a rationalisation composed after the fact.

**One thing becomes decidable a round early, and it is worth reading off round 7 when it scores.**
If g₇ ≥ δ then round 8's window contains a member above δ before round 8 runs, and a ceiling ending
is *certain* rather than expected — knowable a full round before it fires. If g₇ < δ then round 8
is the arm's one genuine chance to converge, and it turns on g₈ alone. Either way round 7's own
number, not round 8's, is what settles which of the two endings is still available. That reading is
owed with round 7's report.

#### Round 7 paid that reading: g₇ = +0.007042, so the ceiling ending is now certain — recorded 2026-08-25, after the round

Dev leak `fully_covered` went 0.176247 → **0.169204** (926 → 889 leaked of 5,254 in-scope gold
spans), a first difference of **+0.007042**; relaxed went 0.133422 → **0.131329** (701 → 690).
`improvements` reads `[0.288923, 0.078607, 0.033498, -0.038447, 0.057290, 0.007042]` and
`termination.reason` is `null` — which the subsection above established was the only possible value,
so the round confirms the arithmetic rather than testing it. Round 7 is also the arm's best round as
well as its latest, so the best-versus-published gap §5.5 exposes is still zero. Rules version 7,
66 rules against round 6's 56 (13 added, 3 removed), `results/.../rules/iter7/es.yaml`.

**The owed reading, and it lands on the certain side.** g₇ = 0.007042 ≥ δ = 0.005, so round 8's
window `[g₇, g₈]` already contains a member above δ before round 8 runs: `converged` is unreachable
at round 8, `iterations >= ceiling` is 8 ≥ 8, and **the arm's ending is `ceiling` — determined now,
a full round before it fires.** Round 8 still runs (the ceiling is where the arm stops, not where it
stops mattering), but no result it can produce changes the recorded reason.

**That certainty rests on eleven spans, and the honest thing is to say so.** A gain below δ needs a
reduction of at most 26 spans (27/5254 = 0.005139 ≥ δ, 26/5254 = 0.004948 < δ); round 7 closed 37.
So the difference between "ceiling is certain" and "round 8 is the arm's one genuine chance" is
eleven leaked spans — an order of magnitude *inside* the ±190-span call-to-call variance measured in
§3. The rule's verdict is certain by arithmetic and the quantity it turns on is not resolvable by
the instrument. Both halves are true and neither cancels the other: the pre-registration is being
honoured exactly as written, and what it happens to be reading is noise-sized. That is an argument
about which termination rule the *next* arm should pre-register, not a reason to touch this one.

**The variance reading, which is owed separately.** The gain is **0.195×** the measured call-to-call
variance of 0.0361 (n = 2) — deep inside what the instrument cannot resolve, where round 6's 1.587×
was outside it. Round 7 is a movement in the right direction that a re-run of the same rules could
plausibly erase.

**The 2026-08-24 prediction is now falsified on both of the falsifiers it named for itself, and
this is where that is stated rather than absorbed.** Its falsifiable form was "`converged` at round
6, 7 or 8 — not `ceiling`", with a second falsifier for any round 4–8 whose improvement exceeds
0.0361. The second fired at round 6 (0.057290, 1.587×) and was reported there only as a variance
reading; the first is now certain by the paragraph above. Both are recorded here together because
the obligation was to report them as a result, and one of them was a round late. **The direction of
the error is the interesting part: the prediction was too pessimistic, not too optimistic.** Round
7's predicted gain was 0.000431 (2.3 spans) against an actual 0.007042 (37 spans) — **16.3×** — and
the predicted leak of 0.1994 against an actual 0.169204. The geometric form is what failed, not the
decay: consecutive gain ratios read 0.272, 0.426, −1.148, −1.490, 0.123, so the gains oscillate
rather than decay, and a model committed to r < 1 cannot express a negative round followed by a
recovery. The cost side of the prediction fared better and still under-read: it budgeted roughly
4 × 2.22M ≈ 8.9M prompt tokens for the rounds after 3, and rounds 4–7 have cost **9.40M** with a
round 8 the prediction said would not be needed, which puts the arm at about **11.75M — 1.32× the
budgeted figure**, the extra round being most of the gap.

**The classification is `unclassifiable`, and it is the abstention clause that fires — the first
time in the arm.** `L = 37`. The top type is `AGE`, 84 → 65 leaked, a reduction of 19 and a share of
**0.5135**, which falls inside the 45–55% band, so the criterion's own clause returns
*unclassifiable* and that is the recorded outcome. All three values are shown per the clause's
instruction: **S1** 0.5135, in the band; **S2 fails** — within `AGE` the only layer covering anything
is `regex_checksum`, 437 → 456, nowhere near `prev ≤ 0.1 × now` (437 against 45.6); **S3 passes** for
that same layer, 456 ≥ 19. The three layers that satisfy S2 do so vacuously at 0 → 0 and fail S3 by
covering nothing. So had the band not fired the verdict would have been **decay**, by exactly round
6's shape and on the same layer.

**Which is a finding about the criterion, recorded and not acted on.** §3 already said the 45–55%
band "is a guard on the weakest condition rather than on the decisive one"; round 7 is that sentence
happening. The band withheld a verdict that S2 would have decided without ambiguity, on a round
whose `L` of 37 makes every share a small-integer ratio — 19 of 37 is one span from 0.4865 and two
from 0.5405. The clause forbids revising the thresholds in light of the numbers, so nothing moves
here; the replacement criterion, if one is written, is written before an arm and not inside one.

**The round moved backwards on one type.** `ID` went 62 → 69, a regression of 7 spans inside a net
reduction of 37; `AGE` (+19) and `LOCATION_AREA` (301 → 292, +9) together account for 28 of the 37,
and `CONTACT` and `OTHER` did not move. 243 of 250 dev documents still leak at least one span.

**Cost.** 251 calls (1 RuleAuthor + 250 Auditor), 2,348,676 prompt + 29,597 completion tokens,
1,077.1 s wall, of which 1,997,229 prompt tokens were cache reads and 8,021 cache writes. Arm to
date: 1,507 calls, 14,123,779 prompt + 246,044 completion, 6,612.2 s. Auditor format compliance
continues its slow drift the wrong way — 132 flags against 270 refusals of 402 proposals, malformed
137 = **34.1%** (round 5 31.3%, round 6 33.7%) — and `auditor.md` stays frozen, per §6.3 and the
decision recorded there against editing it for exactly this reason.

**The draw and abandoned-spend paths recorded nothing, which is the correct output for this round.**
Round 7 was audited once: `draw_index: 1`, no prior attempt, no calls spent before the one that
completed. The canonical `iter7/audit_report.json` carries `draw_index: 1` and `draws_total: 1`; the
preserved copy at `iter7/draw1/audit_report.json` carries `draw_index` and, by design, no
`draws_total` — the per-draw copy is written before the total is knowable, and that one key is the
only difference between the two files. `metrics.json` carries no `abandoned_spend` block, and the
absent-means-unrecorded convention above means that absence *by itself* says nothing — what licenses
"nothing was abandoned" here is `schema_version: 9` (a writer that had the field), `draw_index: 1`
with no `draw2/` beside it, and the union gate that returns `None` only when both the draw record and
the round's lines in the call log are empty. There is no `format_failure.json`. The six mutations
added for these paths on 2026-08-25 are what make this paragraph checkable rather than a claim about
code nobody exercised.

#### Round 8 ended the arm on `ceiling`, and it was still improving when it did — recorded 2026-08-25, after the round

`termination.reason` is **`ceiling`**, `converged` is **`false`**, `iterations` 8. That is what the
two subsections above said it would be, and the arm is over. Dev leak `fully_covered` 0.169204 →
**0.137229** (889 → 721 leaked of 5,254); relaxed 0.131329 → **0.119338** (690 → 627). g₈ =
**+0.031976**. Rules version 8, and for the first time in the arm the file **shrank** — 66 → 60
rules, 9 added and 15 removed.

**What `ceiling` means here, stated because the word invites the other reading.** It does **not**
mean the loop stopped improving. It means **the arm used up the number of rounds pre-registered for
it on 2026-08-12.** Round 8's gain is 6.4× δ and the second largest of the last four rounds; the
trajectory at the moment of the stop was going down, not flat. The honest sentence is: *this arm was
truncated by its own budget, and what it would have done at round 9 is unmeasured.* §3 already
forbids reading `ceiling` as evidence of exhausted improvement — the botocore note says an arm
"could equally have stopped at 5 with a traceback" — and this round is the positive case of the same
warning: a `ceiling` stop is a statement about the policy, never about the curve.

**The arm's published result is round 8, and here the final round is also the best round.** §5.5's
rule is that the arm publishes its final round and never its best; the two coincide (0.137229 is
the lowest leak rate the arm produced), so the gap §5.5 exists to expose is zero for the second
round running. That is luck rather than design, and the rule is what makes it checkable: had round
8 regressed, 0.169204 would still not have been the published number. **The whole trajectory is
published with it** — every round's leak rate, gain, verdict and cost are in this section and in
`iterN/metrics.json`, because a single final figure from a loop that oscillated (+0.289, +0.079,
+0.033, **−0.038**, +0.057, +0.007, +0.032) would misrepresent how it got there.

**Step or decay: a decay, and the same shape as round 6.** `L = 168`; the top type is
`LOCATION_AREA`, 292 → 188, a reduction of 104 and a share of **0.6190** — S1 passes, well outside
the 45–55% band. **S2 fails for every layer that contributes**: within `LOCATION_AREA`,
`context_cue` went 371 → 475, `gazetteer` 352 → 371 and `regex_checksum` 594 → 594, none of them
close to `prev ≤ 0.1 × now`; `tagger` satisfies S2 vacuously at 0 → 0 and fails S3 at zero coverage.
**S3 passes** for all three real layers (each covers ≥ 104). So the largest gain of the arm's second
half came entirely from machinery that was already running — the arm's fourth decay against one
step (round 4) and two unclassifiable rounds.

**The variance reading: 0.886× of 0.0361, inside the band.** The arm's last gain is not resolvable
by the instrument, so of the seven gains only four (rounds 2, 3, 5 and 6) are larger than what a
re-run of the same configuration would move on its own. Round 8 closed 168 spans and the honest
statement remains that the round did not move the leak rate detectably.

**Two things moved in opposite directions and the leak rate still fell, which is worth recording.**
Precision went 0.8546 → **0.9070** while recall fell 0.8668 → **0.8517**, and predictions dropped
5,994 → 5,500. The round pruned fifteen rules and added nine, so it traded coverage for correctness
— and leak `fully_covered` improved by 168 spans anyway, because leak is computed on the prediction
union and asks only whether a gold span was covered at all. `AGE` (−15) and `NAME` (−17) regressed
inside that net. This is the clearest case in the arm of why CLAUDE.md makes leak the headline and
not F1: F1 rose 0.8606 → 0.8785 and would have told a much duller story than "recall fell and the
leak rate improved sharply".

**Cost, and the abandoned attempt this round actually had.** Round 8's first attempt died on Auditor
call 157 of 250 with a Bedrock `500` — `reached max retries: 0` in the traceback, which is the
`total_max_attempts` pin visible in the failure itself. It wrote no report and no metrics file, so
the recovery §5.5.2 sanctions applied and the round was re-run. **This is the first live use of the
union gate** (`c55a8eb`): with no preserved draw, `next_draw` returned 1, and the gate found the
round's 156 logged lines anyway. The plan block said so before the re-run — "no preserved report,
but this round already has 156 logged calls" — and `iter8/metrics.json` carries
`abandoned_spend` = 156 calls, 1,448,683 prompt + 17,058 completion tokens, 650.4 s,
`attempts_abandoned: 1`, `calls_unmeasured: 1`. Under the old draw-count gate this round would have
published as untouched. The published round cost 251 calls, 2,355,561 prompt + 32,023 completion
tokens, 1,117.6 s; the arm's `cost_to_date` is 1,758 calls, 16,479,340 prompt + 278,067 completion,
7,730.2 s. One incidental observation from the caching block: round 8 records 2,005,250 cache-read
tokens and **zero** cache writes, where every other round wrote 8,021 — the entry the successful
attempt read was the one the abandoned attempt paid to write, inside the 5-minute TTL. It is the
only respect in which an abandoned attempt was not a total loss, and it is inferred from those two
numbers rather than from a bill.

#### The next arm's termination rule, in candidate form — recorded 2026-08-25, and not applied to this arm

**The form, and only the form.**

```
converged  ⟺  all(abs(g) < δ for g in gains[-k:])
δ          =  c × σ        σ = measured SD of the leak rate at a fixed configuration
                           c = pre-registered constant, dimensionless
ceiling    kept as is
```

`k` and the ceiling carry over unchanged; what changes is the comparison (`abs(g)` for `g`) and
where δ comes from (a measurement times a constant, for a constant with a floor). **This is written
for the arm after this one and is not applied here** — this arm ends under the rule pre-registered
on 2026-08-12, and the last subsection of this section is where that ending is recorded.

**The absolute value closes the sign hole, which is the defect this section already records.** The
shipped rule is `all(g < d ...)` on signed first differences, so a round that made the leak rate
*worse* satisfies the convergence test — round 5's −0.038447 counted as evidence the loop had
stopped moving, and the note above records that as a live defect rather than a curiosity. `abs(g)`
states what convergence was always meant to mean: **this round changed nothing the instrument can
see, in either direction.** A −0.038 round has moved a great deal; it has simply moved the wrong
way, and no reading of "converged" should cover it. The cost is real and is the reason the ceiling
stays: an arm that oscillates with amplitude above δ never converges under this rule, and bounding
that is the ceiling's job rather than an accident of it.

**Tying δ to the instrument's resolution closes the other half — judging on a difference nobody can
see.** δ = 0.005 was a number chosen a priori with a span rule (26/n_dev) that never bound on this
corpus, and the consequence is on the record one subsection up: **this arm's ending turns on eleven
spans against a resolution of about 190.** Under the candidate that cannot happen by construction.
δ is denominated in units of σ, so "converged" reads "the last k rounds are inside what a re-run of
the same configuration would move anyway", which is the claim a stopping rule should be making. It
also fixes a comparability problem the floor created: an absolute 0.005 means something different on
n_dev 5,254 than on n_dev 800, whereas c × σ is the same statement about resolution on both, and
cross-corpus comparison of *where* an arm stopped becomes meaningful instead of coincidental.

**What the form demands that this arm could not supply.** σ needs an estimate, and this arm has one
pair — Δ 0.0361 at a fixed configuration, n = 2 — from which σ̂ = 0.0361/√2 = **0.025527** with a
single degree of freedom. That is enough to compute with and not enough to pre-register against. So
the next arm owes, before its round 1: **m ≥ 3 re-runs at a fixed configuration**, σ estimated from
them, δ computed once and then **frozen for the arm**. δ is not re-estimated per round — a threshold
that moves with the data is a threshold that can be steered, and the point of pre-registration is
that the rule is not a function of the results it judges.

**The order is part of the pre-registration, and it is this.** The form above is fixed now. **`c` is
not chosen now.** `c` is fixed after σ is measured and before the arm's first round, in a commit that
names the value, the measurement it was computed against, and the resulting δ. The reason for this
particular order is that the two are independent and each is unsafe in the other's absence: choosing
`c` after seeing an arm's gains is precisely the failure the discipline exists to prevent, while
choosing `c` before knowing σ's magnitude is choosing δ blind. Splitting them is what lets both be
honest, and the split is only sound because `c` is dimensionless — a claim about how many
resolutions of headroom count as "stopped", which is answerable without knowing the resolution.

**Applied backwards to this arm — reference figures, not a verdict.** The rule did not govern these
rounds and no result of this arm is restated under it. What the numbers are for is the next arm's
choice of `c`.

| round | g | \|g\| | `c` at which \|g\| < δ |
|---|---|---|---|
| 2 | +0.288923 | 0.288923 | > 11.319 |
| 3 | +0.078607 | 0.078607 | > 3.079 |
| 4 | +0.033498 | 0.033498 | > 1.312 |
| 5 | −0.038447 | 0.038447 | > 1.506 |
| 6 | +0.057290 | 0.057290 | > 2.244 |
| 7 | +0.007042 | 0.007042 | > 0.276 |

With k = 2 and σ̂ = 0.025527, the arm's ending under the candidate is a step function of `c`:

- **c ≤ 1.506** — no two consecutive rounds in 2–7 are below δ (round 7 is, round 6 is not), so the
  ending would depend on g₈: rounds 7 and 8 both below δ needs `c` > 0.276 *and* |g₈| < δ.
- **1.507 ≤ c ≤ 11.31** — rounds 4 and 5 are both below δ, and **the arm would have stopped at
  round 5**, three rounds earlier than it does, on a pair one of whose members is a regression that
  the absolute value correctly counts as small rather than as convergent.
- **c ≥ 11.32** — δ exceeds every gain the arm produced and it stops at round 3, which is a
  reductio on large `c` rather than a candidate.

**The sensitivity is the finding, and it argues for measuring σ rather than for picking `c` well.**
Between c = 1.506 and c = 1.507 the arm's length changes by three rounds and roughly 7M prompt
tokens. A rule that brittle around its constant is not fixed by choosing the constant carefully; it
is fixed by having σ estimated well enough that δ is a real quantity, and by the ceiling continuing
to bound the case where nothing converges. Both of those are in the form above. What is *not*
claimed here is that round 5 was the right place to stop — that is exactly the kind of retrospective
verdict this subsection refuses to make.

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

**Which layers form the rules family is declared, not inferred** —
`layer_families` in `config/naming.yaml`, mapping `rules` to `regex_checksum` ·
`context_cue` · `gazetteer` and `tagger` to itself. §5's complementarity breakdown is
a rules/tagger dichotomy, so it needs that grouping; and a grouping hardcoded in
Python while the layer values are read from the config is the same drift the
paragraph above forbids, moved up one level. Both facts about a layer come from the
same file or the arrangement is only half enforced.

### The rule file, and why the three layers have three syntaxes

`src/rules.py` loads `rules/{lang}.yaml` and runs it. One file, one list of rules, and
each rule **declares** its layer — the field is copied onto every span it emits and is
never derived from the rule's name, the detector, or the pattern's shape.

What differs between the layers is only how a rule says *what to match*. Everything after
the match — the span, its provenance, the language prefix on its `rule_id` — is one code
path, because a per-layer emit path is three places the provenance can drift apart.

| layer | how a rule specifies its matcher | regex needed |
|---|---|---|
| `gazetteer` | `terms:` (a list of literal strings) or `lexicon: {lang}/{name}` naming a one-per-line text file | **no** |
| `context_cue` | `cue:` + `then:`, two halves the engine joins into a pattern | **no**, for the ordinary case |
| `regex_checksum` | `pattern:` (the `regex` module's dialect) + optional `checksum:` | yes |

**Two of the three layers are authorable without regex, deliberately.** §7 predicts that
the tagger's advantage is concentrated in `context_cue`, and that prediction is only
testable if the layers are equally easy to write. An author who can only express
themselves in regex writes regex-shaped rules, and then a layer looks weak for a reason
that has nothing to do with the phenomenon being measured. Three specific consequences:

- **A gazetteer term is a string, not a pattern.** `C.S. (Norte)` is an ordinary
  institution name and a broken regex; terms are escaped, and the engine sorts them
  longest-first because `regex`'s alternation is first-match rather than longest-match
  (with `Hospital` before `Hospital Clínic` the longer term is unreachable and the span
  is silently short — a `fully_covered` miss that passes `relaxed`).
- **A cue rule's span excludes the cue.** The generated pattern puts the identifier in a
  capture group, so `Dr.` is matched as evidence and not covered as PHI. A span that
  swallowed the title would be scored against gold starting at the name.
- **A checksum is named, not written.** The rule says `checksum: dni_mod23` and the
  arithmetic lives in Python. A YAML file expressing a check digit would be a small
  programming language with no tests, and an unimplemented name is refused at load rather
  than raising mid-detection.

**The loader refuses rather than matching nothing.** A missing lexicon, an unimplemented
checksum, a checksum declared on a non-`regex_checksum` layer, a duplicate `rule_id`, a
`lang:` that disagrees with the file it was loaded as, an `OTHER` target (§9.1), a flag
outside the allowlist, or a lexicon name that could traverse out of `lexicons/` — each
raises at load. The reason is specific to this project: a rule that loads and matches
nothing is indistinguishable from a phenomenon that does not occur, and "the phenomenon
does not occur" is exactly what §7 reports as a negative result.

**The feedback path is `tools/check_rules.py`** — the closing step of §11.1's iteration.
It runs a rule file over the dev fold and prints, per rule, how many of that iteration's
drawn window the rule covers and how many false positives it bought, plus the same
coverage dev-wide. The two are reported separately because a rule's effect on spans the
author never saw is real but is not feedback. It prints no precision or F1: those come
from the scorer over a merged prediction set (§9.3), and a second number with the same
name and a different value is worse than no number. The fold is not selectable — there is
no `--split` — because a tool invoked forty times an evening with a fold argument is a
sealing violation with a countdown on it.

**The author-facing copy of all of this is `docs/prompts/rule_author.md` §2**, which is the
document the agent arm actually reads; this section is the rationale and that one is the
reference. Keeping the syntax in two places would be the ordinary mistake here, so §2 states
the forms and this section states why there are three of them. Note the ordering cost: the
prompt is one of the two files `window_freeze.json` hashes, so extending §2 moves
`prompt_sha256` and is only free before the arm records its first minute (§11.1,
`docs/notes/window-freeze-history.md`).

**The validation lives in `src/corpora/base.py`**, next to `axis()` — the one module
that reads `naming.yaml`. Putting it in the scorer would be the obvious placement,
since the scorer is the first consumer, but a family is a property of span provenance
rather than of scoring: the merge policies of §8 and any per-layer report are equally
entitled to ask. Validation that sits with the first consumer gets duplicated by the
second and diverges at the third, and the version that diverges is the one that
stops rejecting things.

What it validates is that the families **partition** the layer axis: the union is
equal, not merely contained, and the intersection is empty. The asymmetry matters. A
subset check in the natural direction — every declared member is a real layer — sounds
like the whole job and is half of it: a layer added to the axis and left out of every
family would validate, and every span it emits would be counted as `neither` in the
breakdown, indistinguishable from spans that genuinely nothing found. The arithmetic
still reconciles and nothing looks wrong, which is why this is refused at load time
rather than reviewed. `layer_family_union_becomes_subset` is the mutation that keeps
the check from being weakened into the plausible half.

One collision is permitted: a family may share a layer's name **only when that layer
is its sole member**, which is why `tagger` can be both. The two readings of
`layer: tagger` then name the same thing, so the ambiguity cannot yield a wrong value.
Add a second learned layer to that family and they diverge — `layer: tagger` would
mean "some learned layer" and the per-layer provenance this section requires would be
gone, with nothing failing. The check raises at exactly that edit and says to rename
the family.

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

#### That ablation is new arms, and what to buy is open until `port-multi` calls — 2026-08-26

The paragraph above is a **build order**, and it was written as though the ablation it names
were available for free. It is not, and the correction has two halves: what the ablation is,
and when it has to be decided.

**It is new arms on the porting axis.** Not a re-analysis of a completed arm and not a way to
ship `port-multi` with fewer roles. §4 prices it — these three artefacts are the loop's
*inputs*, so taking one away changes what the loop is shown from its first call and the rule
file has to be authored again, at the lead comparison's measured order of 1,758 calls per arm,
times draws to clear the 0.0361 call-to-call variance. Nothing in a finished arm's record can
be re-read into the answer, because the record's spans name the detector that emitted them and
never the auxiliary input upstream of the rule.

**So `port-multi`'s role set is not conditional on it.** `config/naming.yaml` declares the arm
as `port-loop` plus all three authors and §4 enumerates it; the sentence above, read as an arm
definition rather than as an implementation sequence, would contradict both. The staging it
describes is the order in which the *code* is written, which is settled by the day the arm
calls and is not an experimental result. The version of it that *is* an experimental question
is the affordable one §4 names: a mixed arm differing from `port-multi` in one artefact's
authorship.

**What is left open, and the deadline.** Whether any of those arms is bought, and if so which
artefact and how many draws, is **not decided here**. It is the same shape as the `port-oneshot`
× N clause's open item (§4): a parameter deliberately unfixed, recorded with the reason and with
the moment it stops being allowed to be open. **It closes before `port-multi`'s first call.**

The deadline is what makes leaving it open honest, and the reason is specific to an ablation
rather than general. Choosing *after* `port-multi` has a number means choosing which artefact
to interrogate in the light of how the arm did — and a leave-one-out selected that way answers
"which artefact can be made to look load-bearing" instead of "which artefact was". The
direction of the bias is not even predictable, which is worse than a known one: an arm that
beat expectations invites ablating the artefact most likely to have caused it, and one that
disappointed invites ablating the artefact most likely to have hurt. Fixing the set of ablation
arms beforehand costs nothing today, because `port-multi` has not called and there is no result
to be tempted by.

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
porting      port-oneshot · port-loop · port-multi · port-selfdesign
             port-oneshot-nofence (the baseline's prompt revised — see below)
             port-human (RETIRED 2026-08-07 — §11, retained in naming.yaml)
```

The path is the identifier:

```
results/{corpus}/{detector}/{supervision}/{porting}/metrics.json
```

The porting axis is a ladder of autonomy. **The baseline is `port-oneshot`**, and three
comparisons carry the paper — one per rung, each isolating a single added capability:

| comparison | question | what it isolates |
|---|---|---|
| `port-loop` vs `port-oneshot` | does iteration justify calling this agentic? | feedback against dev |
| `port-multi` vs `port-loop` | do agent-authored auxiliary inputs justify *multi*-agent? | who authors the loop's inputs |
| `port-selfdesign` vs `port-multi` | is delegating role design worth it? | who designs the roles |

Each pair differs in one capability and shares everything else — same pipeline, same dev
fold, same rule schema, same feedback tool (`tools/check_rules.py`, §3). That is what makes
the ladder readable as a ladder: a difference between adjacent rungs is attributable,
whereas a difference between `port-oneshot` and `port-selfdesign` is a difference between
two bundles.

#### `port-oneshot` is `port-loop` truncated after the first call — decided 2026-08-09

The lead comparison needs this stated as a definition rather than left to each arm's
implementation, because "differs in one capability" is a property of what the two arms are
*shown*, and that is decided in code both arms will be written from.

**The definition.** `port-oneshot` is `port-loop` stopped after call 1. Not a separate
procedure that also makes one call — the same procedure, cut. Three consequences, and the
second is the one that costs something:

1. **`port-oneshot` reads `docs/prompts/rule_author.md` with §§1.3 and 1.4 empty.** No score
   block and no error-span block, because there is no previous iteration to draw either
   from. The banner at the top of that file already says so; this is where the reason lives.
2. **`port-loop` reads iteration 1 with §§1.3 and 1.4 empty too.** Its feedback begins at
   iteration 2. This is the clause that does work: it is a constraint on the *iterating* arm,
   adopted so that call 1 of both arms is byte-identical in what it is shown.
3. **`port-loop` uses `draw` and `render_window` from iteration 2 onward**, and that plumbing
   is built when `port-loop` is built. `src/orchestrate.py` does not call either, and
   `tests/test_orchestrate.py` asserts it does not — see the mutation
   `the_baseline_draws_error_spans`.

**Why, and it is the ladder's readability at the point the paper leads with.** §1.4 is 40
dev error spans with ±120 characters of context each. At iteration 1 those errors come from
an empty rule file, so `initial_error_pool()` derives them from the loader alone: **they are
dev gold spans, not model output being fed back.** Give them to `port-oneshot` and the two
arms differ in *two* things — whether 40 dev gold spans were seen, and whether the arm
continues. When `port-loop` then wins, nothing in the record attributes the win to
iteration rather than to the 40 spans, and "does iteration justify calling this agentic?"
is the question the paper is built to answer. Worse, the arm that would look unfairly
strong is the baseline, so the failure runs in the direction that flatters the rung above
it.

**The two rejected readings, recorded because each looked reasonable.**

- **Draw and render for `port-oneshot`, and edit the banner (rejected).** This makes "the
  baseline's whole definition is one call with no feedback" false as written, and the
  falsehood does not stay in prose: `rule_author.md` is hashed into `window_freeze.json`
  (§6.3, §11.2), so the edit moves the hash and every arm's freeze record thereafter attests
  to a window whose defining claim about the baseline is untrue. A supplied gold sample is
  feedback with the loop removed, not the absence of feedback.
- **Draw and render with `n`=0 (rejected).** Mechanically identical to an empty block while
  keeping the plumbing present, which was its appeal. It buys a degenerate path — a
  stratified seeded draw over zero slots — that no arm uses on purpose: `port-oneshot` would
  be its only caller and `port-loop` would never pass 0. An untaken branch that exists to
  make a call site look uniform is a branch nobody tests and everybody trusts. The DUA case
  in `rule_author.md` §1.4 does set `n`=0 for a real reason, and that is a corpus-level
  decision about a running arm, not this arm's steady state.

What `port-oneshot` is shown, then, is §1.1 (task frame) and §1.2 (the current rule file,
empty at iteration 1) and nothing else. It still freezes both window files, because
`config/sampling.yaml` holds §1.4's parameters and the freeze record has to attest to the
window the arm committed to — including that its §1.4 block was empty. See §6.3.

If `port-loop` does not beat `port-oneshot`, the agentic framing is not earned. That is a
real possible outcome and the experiment is designed to detect it. The same holds one rung
up, and CLAUDE.md's cost requirement plus §11.3's pre-registered 1.9× standard apply at
every rung — a rung that wins only by spending more has not earned its name.

#### A revised prompt gets a new `porting` value — `port-oneshot-nofence`, 2026-08-11

The baseline's first run on `es-meddocan` ended in a format failure: the model wrapped the
file in a markdown code fence, the schema refused it, and `format_failure.json` was written
instead of `metrics.json` (`docs/notes/arm-port-oneshot-es.md`). §2 of `rule_author.md` now
states that the emission is the file's content alone. The prompt is hashed into
`window_freeze.json` (§6.3), so the edit moves `prompt_sha256` and the next run freezes its
own window rather than inheriting one.

**The run gets a new axis value rather than the baseline's directory.** `port-oneshot` on
this corpus has called: `called_where()` reads its `agent_calls.jsonl` line and git history
holds its artefacts, so the cell is spent and `tools/run_arm.py` refuses it before the plan
is printed. That is the freeze working, and it is also what makes a field-level distinction
unimplementable — recording "which prompt" in the run block presumes a second run to record
it on, and there is no directory for one.

**The name states a property of the prompt, because the alternative reads as a retry.**
§10 A2 fixed format retries at zero, on the argument that a format failure is a finding
about capability rather than an accident on the way to one. An ordinal suffix (`-r2`, `-p2`)
would put an attempt count in the path and undo that in the one place a reader looks first;
it is separately forbidden by `naming.yaml`'s ban on ordinals. `-nofence` names what the
revised prompt specifies, so the two directories read as two prompts. The general
convention, available to `port-loop` on the day a hashed window file moves after that arm
has called, is `{rung}-{what the prompt now specifies}`.

**A fifth path component was rejected.** `.../{porting}/{prompt}/…` is the more literal
encoding of "two prompts", and it breaks what is already on disk: `format_failure.json` and
two `window_freeze.json` records are committed at four axes deep, and a required fifth
component leaves them matched by no `ALLOW_PATTERNS` entry and unreachable from
`metrics_path()`. Migrating them is not open either — a relocated freeze record would sit at
a five-deep path while its content names four axes, and editing a frozen record to agree is
what §6.3 forbids.

**The ladder still has four rungs, and this value is not a fifth.** It is the same rung
under a different prompt, so it enters no comparison in the table above as a rung of its
own; which value supplies the baseline number for `port-loop` to be read against is settled
where the results are reported, on the ordinary rule that a rung is compared against a run
that produced a `metrics.json`. A format failure produced none.

**Every rung runs on the same model family, and the baseline is not exempted.** This was
decided on 2026-08-07 against the alternative of swapping `port-oneshot` to a different
family for external validity (`docs/notes/baseline-model-family.md` records the options and
the Bedrock families actually callable). The reason is the table above: swapping only the
baseline makes `port-loop` vs `port-oneshot` differ on **two** axes at once — harness
(iteration or not) and model family — and a difference between them is then no longer
attributable to either. The ladder's whole readability rests on adjacent rungs differing in
exactly one capability, and the rung that would break is the one the paper leads with. A
secondary cost points the same way: a different family's compliance with the
`rules/{lang}.yaml` schema and regex dialect is unknown, so a low score could be format
failure rather than porting ability, and after the fact the two are hard to separate.

The honest objection to holding the model fixed is that all three comparisons are then
**self-comparisons** — one Claude call against Claude iterating against Claude with
differentiated roles. That objection is answered by an appendix experiment rather than by
weakening the ladder: `port-oneshot` is run once more on a different family as an external
reference point (**§10 A2**, pre-registered there now). It is a robustness check and not a
main result — it anchors roughly where a single LLM call sits on this task, and it partially
fills the second thing §4.1 records as lost. It does not adjudicate any rung.

`model_id` is recorded in every arm's `metrics.json` beside the cost block (§5), because
Bedrock model aliases are updated silently and an unrecorded run does not reproduce six
months later. That is required whether or not the appendix runs.

#### The lead comparison's result on es-meddocan — the rung is earned on quality, and the cost is three orders of magnitude (2026-08-25)

`port-loop` completed on `es-meddocan / R / sup-free` at round 8. The full trajectory, every
round's verdict, the defect list and the cost arithmetic are in
**`docs/notes/arm-port-loop-es.md`**; this clause records only what §4 asks of the pair, so that the
ladder's leading row is readable here without the note.

The baseline is `port-oneshot-nofence`, because `port-oneshot` on this corpus produced only
`format_failure.json` and a rung is compared against a run that produced a `metrics.json`. The
one-capability condition held: consequence 2 above kept `port-loop`'s round 1 byte-identical in what
it was shown, which is what makes round 1 usable as the **cost-matched point**.

| | baseline, 1 call | `port-loop` round 1, 1 call | `port-loop` round 8, 8 rounds |
|---|---|---|---|
| leak `fully_covered` | 0.559954 | 0.596117 | **0.137229** |
| leak `relaxed` | 0.484964 | 0.471641 | 0.119338 |
| documents with leak | 250/250 | 250/250 | 223/250 |
| prompt + completion | 16,396 | 16,862 | 16,757,407 published / 24,059,438 true |

1. **Quality: earned.** −0.422725 absolute, **11.7× the measured 0.0361 call-to-call variance**;
   2,221 more gold spans covered; 27 documents with no leak where the baseline had none. Per
   canonical type (§5.1, `OTHER` n=6 and `PROFESSION` n=4 omitted as sparse and the omission
   stated), **seven of eight improved** and `AGE` alone regressed, +0.0326.
2. **At matched cost the two arms are indistinguishable.** One call against one call, the loop's
   round 1 is 0.036 *worse*, which is the variance itself. The entire gain was bought by rounds
   2–8 — which is how "feedback against dev" is isolated on this corpus.
3. **Cost is reported, not thresholded** (§3): **1,022× the baseline's tokens published, 1,467×
   true**, 1,758 / 2,536 calls, 238× / 338× wall. §11.3's 1.9× standard applies one rung up, where
   the two arms have the same shape.

**Three limits travel with the conclusion.** The arm was truncated by its ceiling while still
improving, so this is eight rounds against one call and not a converged loop against one call. The
variance is one pair (n = 2, 1 dof), and the aggregate figure *understates* per-type variance — the
two draws of the identical configuration differ by 63 vs 506 leaked on `AGE` and 837 vs 587 on
`NAME` inside an aggregate gap of 0.036, so per-type comparisons carry more noise than the headline.
And the whole arm ran with 55–60% of Auditor responses refused as `malformed`, which §6.3 records as
this arm's result rather than something to repair mid-arm.

#### `port-oneshot` × N, a union control — **pre-registered as a candidate 2026-08-25, and not run**

Conclusion 2 above says the entire gain was bought by rounds 2–8. That sentence bundles two
things the record cannot separate: **feedback against dev**, and **1,757 more calls**. The
ladder isolates the first only if the second is held constant, and it is not — the baseline
made one call. This clause writes down the arm that holds it constant, as a candidate whose
parameters are fixed later, on a schedule stated at the end.

**What it asks.** Spend the loop's budget on *independent* single calls instead of on a loop:
N runs of `port-oneshot-nofence`, identical prompts, differing only in the sampling of the
model, their N rule files combined by a stated rule, scored once. If that reaches
`port-loop`'s leak rate, the lead comparison measured **sampling volume** and called it
iteration. If it does not, rounds 2–8 did something a pile of independent draws cannot, and
"feedback against dev" survives with a control behind it. The arm is worth pre-registering
because this project has already measured the effect it would exploit: two byte-identical
calls differ by 0.036 in leak rate (§3, 2026-08-21), so N draws have a spread to mine, and
the union of N draws mines it by construction.

**It is not a rung.** It enters no row of the table above and adjudicates none. Its status is
`port-oneshot-nofence`'s and §10 A2's — a run that exists to make one of the three rows
readable. The autonomy ladder still has four rungs.

**N is a formula, not a number, and the basis is not obvious.** Four bases for "the same
budget" give four values on `es-meddocan`, against published cost (`port-oneshot-nofence`: 1
call, 14,071 prompt, 2,325 completion, 32.542 s; `port-loop`: 1,758 / 16,479,340 / 278,067 /
7,730.2 s):

| basis | N | what is wrong with it |
|---|---|---|
| LLM calls | 1,758 | over-funds the control by **1.72×** — the loop's average call is an Auditor call on one masked document, cheaper than a whole `rule_author.md`, so 1,758 baseline calls cost 28,824,168 tokens against the loop's 16,757,407 |
| prompt tokens | 1,171 | ignores the completion side, where the two arms differ most in shape |
| completion tokens | 120 | the loop is prompt-heavy by design (250 documents re-sent per round), so this basis funds the control at a tenth of the loop and answers a different question |
| prompt + completion, raw | **1,022** | weights a completion token like a prompt token, which no price list does |

**Pre-registered: prompt + completion, raw, so N = round(loop published total ÷ baseline
published total) for the same corpus.** The last basis's flaw is a 1:1 weighting with no
price behind it; the alternative is to introduce a price, and a price is a knob that moves
N. It is also the number §4 already reports (1,022× in conclusion 3), so the control's cost
column reads 1.00× by construction and no reader has to check the matching. **Raw, never
cache-discounted** — §11.3: caching changes no byte of a prompt, and a control funded on
effective tokens would be handed N inflated by the loop's 84.87% cache-read share, which is
a transport fact.

Fixing the *formula* rather than the number is what keeps N off the list of things that
could be tuned: it is computed from two cost blocks, and **no leak rate enters it**. It is
also per-corpus by necessity, since both inputs are corpus-specific.

**How the N rule files combine is the decision that has to be made, and every option costs
something.**

| option | what it costs |
|---|---|
| **union** — every rule from every draw | precision falls, and the leak improvement is *by construction*: leak asks only whether a gold span was covered by the prediction union, so adding rules can never raise it. Direction is uninformative; only the *rate* of fall and the precision paid for it are findings. At N = 1,022 it may be degenerate — a rule file that flags everything has leak 0 and no precision |
| **best on dev** — score N, publish the winner | a max-of-N statistic, biased upward by exactly the spread the arm exists to probe (0.036 across one pair), and it is dev selection: the baseline's own number was a single draw and not a max, so the comparison stops being like-for-like on both sides at once |
| **vote at threshold m** — keep a rule appearing in ≥ m of N | m is a free knob. Worse, it needs a rule-identity relation across draws that the record does not supply: `rule_id` is a mechanism+content name from a vocabulary that widened six times in this arm alone (§6.1), two draws can express one regex under two ids and two regexes under one id. Voting on *spans* instead sidesteps identity but changes what the arm emits from a rule file to an ensemble detector, and every cell on the porting axis emits a rule file |
| **reuse a merge policy** — fixed-priority or agent-arbiter over the draws (§4, CLAUDE.md) | fixed-priority needs a priority order over draws, which is arbitrary (draw index) or dev-selected (disguised selection); agent-arbiter adds an agent, which moves the arm to a different rung and destroys it as a control |

**Pre-registered: union, over the draws in call order, reported as a curve at dyadic N (1,
2, 4, … up to the budget-matched N) with the budget-matched point as the headline.** Union
is the only option with no knob and no selection in it; its by-construction direction is a
statable property rather than a hidden one, and CLAUDE.md already requires precision and the
complementarity decomposition beside the leak rate, which is exactly where the cost of a
union shows up. The curve is free — unions are nested, so it is re-scoring and not more
calls — and it makes the degeneracy at large N *visible* instead of fatal: if precision
collapses on the way to N = 1,022, the curve says where. **Call order, not any other order**,
because a nested union over "the first N" depends on the ordering, and choosing an ordering
after seeing the scores is the selection this whole clause is trying to keep out.

**One mechanical thing must be settled with it: `rule_id` collisions across draws.** N draws
will emit the same id with different bodies, and §9.3 computes per-rule attribution inside
the scorer, so a collision does not merely look untidy — it makes attribution ill-defined and
silently pools two rules. Suffixing the draw index into the id puts an ordinal inside an
identifier and would have to pass the screener's mechanism/content vocabulary; loading the N
files as N files leans on §5.2, which loads per language and has never been asked to load N
per language. Whichever is chosen, the requirement is that per-rule attribution stays
well-defined for every rule in the union, and the choice is recorded with the rest.

**The order, and it is not negotiable: N's formula, the combination rule, the draw order,
the collision resolution and the `{porting}` value are all fixed *before the next corpus is
touched*, and nothing runs until they are.** The reason is that `es-meddocan`'s answer is
already published: 0.137229. Any parameter still free today is freedom to hit or miss a
number that is known, and after the fact a reader cannot distinguish a control that was
designed from a control that was tuned until the loop kept winning. That is why this clause
is a candidate and not an arm — running it now would be arm selection after the result, which
is the failure §6.3 refused in the Auditor's case for the same reason. If the arm is ever run
on `es-meddocan` as well, that run is reported as a check against a known target and its
first evidential use is a corpus where the loop's number is not yet in hand.

**The name enters `naming.yaml` at decision time, not now.** The candidate is
`port-oneshot-fanout` — the `-nofence` convention names what changed, and here the harness
changed rather than the prompt, so the suffix names the harness. N is not in the path: it
varies per corpus by the formula above and belongs in the run record beside `model_id`. A
value in `naming.yaml` is a cell the tooling will plan, so it is added when what it names is
settled.

#### `port-multi` differs from `port-loop` in one capability, and it is not role differentiation — written 2026-08-26, before `port-multi` runs

The table above gives the second comparison's isolated capability as "role differentiation".
**That name is wrong, and it is wrong in the direction that makes the rung uncheckable:
`port-loop` already has differentiated roles.** The completed arm ran a RuleAuthor and an
Auditor, `port-loop`'s frozen window hashes both prompts (§5.5), and the Auditor's refusal
rate is reported as one of that arm's results. So an added capability spelled "more than one
role" is a capability the rung below already has, and the pair would differ in nothing.

The correction is worth making here rather than in the results because the arm has not run:
naming the capability after the fact is choosing what the comparison was about once its
number is known.

**The capability, stated so that it takes one of two values.** Whether **an agent authors the
auxiliary artefacts the loop consumes but does not produce.** `port-loop` runs its loop on
auxiliary artefacts supplied by hand; `port-multi` is the same loop with the *authorship* of
those artefacts moved to agents. Nothing else moves — same pipeline, same dev fold, same rule
schema, same feedback tool, same termination rule.

**The role sets, enumerated, because the one-capability condition cannot be checked against a
role set no section of this document states.** `config/naming.yaml` has carried the answer all
along — its `porting` axis glosses `port-multi` as "`port-loop` plus Profiler, Mapper,
LexiconBuilder" — and §8 refers to "the fixed role set of `port-multi`" as settled. DESIGN never
wrote it out, which is how the table's capability name stayed wrong: the config said what the
arm *has* and this section said what the comparison *isolates*, and nobody had to read both at
once. The table below is the config's gloss with the artefacts named, and it is here so that
the two can no longer disagree silently.

| arm | roles | authorship of profile · mapping · lexicon |
|---|---|---|
| `port-oneshot` | RuleAuthor | by hand |
| `port-loop` | RuleAuthor, Auditor | by hand |
| `port-multi` | RuleAuthor, Auditor, **Profiler, Mapper, LexiconBuilder** | by agent |

`port-selfdesign` is the same five as a *starting* set, since what that rung delegates is the
composition of the set itself (§8).

**"Consumed but not produced" is exact, and it is worth being exact about, because the three
artefacts are supplied in three different ways today.** In each case the loop *reads* the
artefact's content and no arm on the porting axis *writes* it:

- **profile** — format, offset convention, type inventory, group key (§3). The loader embodies
  these per corpus; the tracked raw profiles are the observations they were derived from.
  `paths.profile` names a file that does not exist and no code reads that key.
- **mapping** — corpus taxonomy → canonical type set. §9.0 states this outright: the mapping is
  "a design input shared by every arm, not one arm's output", it lives in DESIGN as a
  human-authored table *because* only `port-multi` and above have a Mapper, and no code reads
  `paths.mapping` either.
- **lexicon** — institutions, regions, departments. This is the one with a live consumer:
  `src/rules.py` resolves a rule's `lexicon` form through `paths.lexicon`. **The completed
  `port-loop` arm used it zero times** — no rule in any of its eight rule files takes the
  lexicon form — so the loop's consumption of this artefact is currently a capability of the
  loader rather than an observed dependency, and that is stated rather than smoothed over.

The uneven supply is the point. All three are inputs the pipeline needs and that no rung below
`port-multi` produces, which is why they are what an agent can be given to write and why
handing them over is a single change of authorship.

**Why the three are one capability, and it is not to keep the rung count down.** They are the
same *kind* of thing: the loop's inputs rather than its outputs. A rung on this ladder names a
harness shape, and "the auxiliary inputs are authored by agents" is one shape whether it is
three files or thirty — the alternative would be to make each artefact its own rung, which
would say that authoring a profile and authoring a lexicon are different *capabilities* rather
than the same capability applied to different files. They are not: in each case an agent is
given a corpus and asked to produce an input the loop will read and never revise, and the
loop's relation to the artefact is identical in all three. Bundling by kind is also what keeps
the bundle principled — a bundle chosen by count could be split or grown to suit whatever a
result needed, while this one has a membership test: does the loop read it without writing it?
Something the loop writes (a rule file, an audit report) cannot join, and something no agent
authors cannot either.

**What the ladder therefore does not answer, stated so that nothing later has to claim it
did.** The per-artefact question — what an agent-written profile contributes, whether a
generated lexicon earns its calls — **is not answered by any rung of this ladder, and no
re-reading of the arms' records will answer it.** `port-multi` emits one rule file per language
like every other cell on the porting axis (§5.3), and its `spans.jsonl` carries `layer`,
detector and `rule_id` per span (§3, "Span provenance") — a span's provenance names the
*detector* that emitted it, never the artefact upstream of the rule that detected it. So even
per-rule attribution inside a completed `port-multi` cannot be lifted to per-artefact
attribution: the rule file is the whole arm's artefact and its rules do not record which
auxiliary input shaped them. §3's `agent_actions` list records agent intervention on a span,
and the two agents it was built for (Arb, Aud) are runtime agents on the *detector* axis, which
is a different axis answering a different question.

This is the grain the whole ladder works at, not a concession made for this rung: rung 1 asked
whether there is feedback against dev, not which of §1.3's audit block and §1.4's error spans
supplied it, and it answered at that grain — the result clause above attributes the gain to
rounds 2–8 as a block, and could not do otherwise.

**Two costs travel with reading the rung this way, and both are real.**

1. **A conclusion arrives without an address.** If `port-multi` wins, the paper can say that
   agent-authored auxiliary inputs pay and cannot say *which* input to stop writing by hand —
   which is the sentence a practitioner wants. If it loses, the loss is equally unaddressed: a
   set with one inert member and one harmful one loses in the same direction as a set that is
   uniformly useless, and the record does not separate them. §3 saw this coming — "building
   five at once makes it impossible to tell which one works" — and this clause accepts it as a
   cost rather than avoiding it, because the alternative is priced in item 2. It is a
   limitation of the design and not of the analysis, so it belongs beside §4.1's two losses
   rather than in a results caveat.
2. **The ablation that would answer it is not a re-scoring. It is buying the calls again.**
   This is the cost that decides the clause. Several of this project's comparisons are free
   because the calls already ran and their artefacts are committed — §11.3's two stopping-rule
   readings cost "one extra scorer run against dev and **no extra agent calls**", and the ×N
   control's dyadic curve is free because unions are nested. A per-artefact ablation is not in
   that family, and the reason is exactly the property that defines the capability: these
   artefacts are the loop's *inputs*. Take the agent-written profile away and the loop runs on
   a different input from its first call onward, so it must author its rule file again — and
   authoring a rule file is what the calls are spent on. Nothing downstream can be re-scored,
   because nothing downstream is the same. At the lead comparison's measured scale one arm was
   1,758 calls and 16.76M published tokens, so a leave-one-out over the three artefacts is
   three further arms at that order, plus their own windows to freeze (§6.3) and their own
   cells on the axis. The arithmetic is what makes this a pre-registration decision rather
   than an analysis to be planned later: nothing in the record can be re-read into it, and
   each answer is bought at the price of an arm.

   Two further things make it more expensive than the multiplication suggests. Call-to-call
   variance is 0.0361 at aggregate and larger per type (§3, 2026-08-21), so a per-artefact
   effect smaller than a whole rung's effect needs repeated draws per cell to be readable at
   all — the ablation is three arms *times* draws. And a leave-one-out cell is not
   `port-multi` minus one agent: the artefact still has to come from somewhere, so the cell
   either falls back to the hand-written input (which makes it a *mixed* arm, differing from
   `port-multi` in one artefact's authorship — the one thing the design can actually ask, and
   worth noting as the only affordable version) or does without the input entirely (which
   changes what the loop is shown and reintroduces the two-axis confound the one-capability
   rule exists to prevent).

**Why this is writable today, and would not be later.** `port-multi` has never run. No cell
under that value holds a `metrics.json`, a `format_failure.json` or a `window_freeze.json`, and
`called_where()` reports it unspent — so this clause fixes how the comparison is to be read
*before* there is a number to read, which is the only condition under which fixing it is not
selection. **After the arm's first call the identical edit becomes a post-hoc adjustment**: a
narrowing of what the rung claims, written by someone who has seen what the rung produced, and
indistinguishable in the record from a narrowing chosen because the broader claim did not
survive. §6.3 refused exactly this move in the Auditor's case, and the ×N clause above refuses
it for a parameter rather than for a reading. This is the same refusal applied to the *scope of
a conclusion*, which is the thing a reader is least able to audit after the fact.

**What this changes and what it does not.** No rung is added or removed and the
one-capability condition is untouched — it is now *checkable*, which it was not while one of
the two arms' role sets was unwritten. The table above is corrected in place: its second row
read "role specialisation" and "role differentiation", and now names authorship of the loop's
inputs. **The axis value keeps its name.** `port-multi` is a `naming.yaml` value written into
paths, results and prose, and renaming it to describe the capability would rewrite the
identifier of a cell to fit a correction to its description — the ordinal-and-status-word ban
at the top of this section exists because names that track a current understanding stop
matching the record. So `port-multi` names the rung, this clause names the capability, and the
"multi" in it is read as historical: `port-loop` is already multi-agent, which is what made the
original phrase wrong. §3's "start with three — Profiler, RuleAuthor, Auditor" is a
build-order rule about what to implement first and is not a statement of any arm's role set;
read as one it would contradict the table above. The ablation that sentence names is priced in
item 2, and scheduling it belongs in §3 beside the sentence that raises it rather than here. Whether the per-artefact question is worth three arms is a scoping
decision for later, and if it is ever taken, the arms it needs are new cells on the porting
axis and not a re-analysis of this one.

#### Every rung runs on one **dated** id: `us.anthropic.claude-opus-4-5-20251101-v1:0` — decided 2026-08-11

The paragraph above fixes the model *family* across the ladder. This fixes the *snapshot*,
and it is the same rule taken to the point where it actually binds: **one capability rule
requires the model to be pinned too, and an alias is not a pin.**

`us.anthropic.claude-opus-5` is an undated alias. Bedrock updates the weights behind an
alias silently, and nothing on the wire says which weights answered — four probes establish
this and none of them leaves a route open (§10 A2; `docs/notes/baseline-model-family.md`).
So two rungs run a month apart on that alias may have run on two models, and the record
cannot distinguish "iteration helped" from "the model behind the alias changed between the
two calls." That is the same two-axis failure this section rejects for the baseline family
swap, arriving through a channel the family argument does not close. **`port-oneshot`,
`port-loop` and `port-multi` therefore all take the same dated id**, and `port-selfdesign`
too when it is built.

Pinning is possible only downward: there is no dated `opus-5` on this account, so the pin is
one generation below the newest available alias. **That costs this section's argument
nothing.** The three comparisons ask whether iteration, agent-authored auxiliary inputs and
delegated role design pay for themselves — each is a question about *harness structure at a fixed
capability*, and the capability level is the thing held constant rather than the thing
measured. A rung that beats the rung below it on opus-4-5 has demonstrated that the added
capability does work; nothing in that inference requires the model to be the best one
obtainable. If anything the lower generation reads better: at a less saturated capability
level there is more headroom for a rung to show an effect, and a ladder whose rungs all
score near ceiling is a ladder whose differences are inside the noise.

The reproducibility side is what the pin buys, and it is worth more here than in an ordinary
run because **each arm's window is binding from its first call** (§6.3). A rung that recorded
`alias-unresolved` could never afterwards be pinned down — not by re-running it, since
re-running is what the freeze forbids. §10 A2 records the reversal in full, including the
term that was missing from the original trade and why pinning the whole ladder makes the
snapshot discrepancy vanish rather than merely relocating it.

**The catalogue timestamp is recorded beside the call, and it is not part of the pin.** Each
arm probes `GetFoundationModel` once, before its call, and files the result — ARN, display
name, status, `startOfLifeTime` — in the call log and in whichever of `metrics.json` or
`paths.formatfailure` it writes. Three homes because the call log is deny-listed by
`tools/release_screen.py` and no git copy of it can ever exist, and because exactly one of the
other two files is written per arm. It costs one control-plane call, which makes no inference
and is therefore not in the cost block (CLAUDE.md's cost-beside-quality figures stay
comparable to `port-loop`'s).

What the timestamp is worth: it orders the arm against the id's publication, so "this arm
called a snapshot that had already been out for nine months" is answerable from the record.
What it is **not**: it does not resolve an alias, and it is not evidence that anything was
resolved. `startOfLifeTime` is when the *id* appeared in the catalogue, not which weights
served it on the day — probe 4 of `docs/notes/baseline-model-family.md`. So it lives at the
top level of those files and never in the run block, where it would sit beside
`model_id_resolution` and be read as corroborating a verdict it cannot support. That
separation is a decision and not an implementation detail: the same failure once appeared here
as a comment overstating a guarantee (`tests/mutations/README.md`, the sixth family), and a
field is the version of it that reaches a reader who never opens the code.

### 4.1 What retiring `port-human` costs, stated as a limitation

`port-human` was the baseline until 2026-08-07 and was withdrawn for resourcing: no human
author could be secured (§11). `port-oneshot` replaces it. The substitution is sound for
the ladder — a single LLM call with no iteration is the right floor for a claim about
*iteration*, and it is arguably the better floor, since `port-oneshot` shares the schema,
the prompt and the tool with the rungs above it, while a human shares only the schema. But
two claims are lost and neither is recoverable by any analysis of the remaining arms:

**1. No measured labour-cost claim.** The project can no longer say what porting this
pipeline costs a person, and therefore cannot report the ratio a practitioner actually
wants: agent dollars against human hours. Every comparison that survives is
agent-versus-agent, where cost is LLM calls, tokens and wall time — commensurable, but all
on one side of the question. **The paper argues this indirectly, by citing published
annotation- and rule-authoring-cost literature**, and must mark it as external evidence
rather than a measurement of this pipeline. An indirect argument from another project's
corpus, language and annotation guideline is weaker than a measurement here, and the
sentence that says so belongs in the limitations section rather than in a footnote.

**2. No human ceiling on quality.** `port-human` was also the answer to "is the whole
pipeline any good, or are all the arms bad together?" Agent-versus-agent comparisons are
internally valid and jointly unanchored: `port-multi` beating `port-loop` by six points is
the same number whether both are near a competent human's rule set or far below it. The
partial substitutes are external — MEDDOCAN's published shared-task results for the
Spanish arm, and the corpora's own inter-annotator agreement where reported — and both
compare against a different system rather than against a person doing this job. What
cannot be substituted is the counterfactual: a person given the same window, the same
schema and the same stopping rule.

§10 A2's cross-family `port-oneshot` run is a third partial substitute and is weaker than
both: it holds the harness and the window fixed, which the published shared-task results do
not, but it is another system rather than a person, so it says where a single LLM call sits
and not where a competent human rule set sits. It narrows loss 2 and does nothing for
loss 1.

The one thing retirement does **not** cost is the pre-registration. §11's protocol was
fixed before any dev document was read for rule-writing and is retained in full, so a
revived human arm inherits decided rules rather than re-deriving them after the agent
results are visible.

---

## 5. Metrics

Headline quantities:

- **Leak rate** — share of gold PHI spans not fully covered by some prediction.
  This is the only operationally meaningful number; a missed identifier is a
  disclosure, and a partially redacted one can still be. The looser
  any-overlap variant is reported beside it as a lower bound. Exact definitions
  and the reason `fully_covered` is the headline are in §9.3.
- **Complementarity breakdown** — found by rules only / tagger only / both /
  joint only / neither. This is what shows whether the neural layer earns its cost.
  The fifth category is forced by the `fully_covered` definition and is explained in
  §9.3; under `relaxed` it is always zero, so the familiar four-way reading of this
  breakdown is intact wherever `joint_only` is absent.

Both are computed against the **union** of same-type predictions rather than against
a one-to-one assignment, for reasons given in §9.3 — coverage answers "is this
identifier hidden", which does not depend on which detector gets the credit, and a
one-to-one matching would undercount `both` exactly where the layers agree.

Precision, recall, and F1 are reported but are not the headline, for the
reason in §2.

Per-arm cost is reported alongside quality: LLM calls, tokens, wall time.
A quality gain that costs 2× is a different result from one that costs 1.05×.

### 5.0 What closing an arm writes

`src/eval/run_fold.py` is the execution path: it applies the arm's rule set over one
unsealed fold, scores it, and writes two files into the arm's directory. `metrics.json` alone
would leave the numbers unauditable, and `spans.jsonl` alone would leave them uncomputed.

```
results/{corpus}/{detector}/{supervision}/{porting}/spans.jsonl
results/{corpus}/{detector}/{supervision}/{porting}/metrics.json
```

**`spans.jsonl` — one predicted span per line, with §3's provenance in full and no surface
form.** `doc_id`, `start`, `end`, `phi_type`, then `layer` · `detector` · `rule_id` · `score`
and the `agent_actions` list. The text is dropped deliberately: this file is publishable
(`tools/release_screen.py` allows `results/**/spans.jsonl`) and CLAUDE.md permits offsets,
types and verdicts with the text left out. The fields are enumerated rather than serialised
wholesale, so a field added to `Span` does not reach a public file the day it is added.
Lines are sorted, so re-running identical rules produces no diff and a reviewer can tell a
reordering from a change in what was detected.

**The run block records what the numbers are premised on.** The four axes plus `split`, the
per-file `rules_version` and the rule ids that ran, the commit and working-tree state, and
`model_id`. Every field is a premise: the axes name the cell, `split` names the fold, and
`model_id` names what was actually called (§4 — Bedrock aliases move silently). A missing
field is refused rather than defaulted, and **an arm that called no model records the
explicit `none` from `config/naming.yaml` (`model_id_absent`) rather than `null`** — `null`
cannot be told apart from a field nobody filled in, so it would leave the record unable to
say whether the `R` arm used no model or the run forgot to write down which one it used. Same
principle as the cost block: absent is refused, explicitly-absent is recorded. The `R` arm's
`llm_calls`, `prompt_tokens` and `completion_tokens` are therefore zeros, which are
measurements; `wall_seconds` is measured for real, because a rule pass does take time and a
reader comparing arms on cost needs the compute side of `R` to be honest.

**Detection lives in exactly one function, and `tools/check_rules.py` calls it.** The author's
feedback tool is a *sample view* of this path, not a second runner. Two implementations of
"run these rules over these documents" drift, and the drift's shape is the worst available:
the sample says a rule fires and the fold-wide score says it does not, with nothing in either
output identifying which is wrong. The author then tunes against whichever number is lying,
and a reader comparing the tool's counts to `metrics.json` cannot notice. So the two differ in
which spans they *show* and never in which spans exist.

**The fold is a parameter here and a literal in the tool**, and the asymmetry is the point.
`check_rules.py` is typed by a person forty times in an evening, so a `--split` flag on it is
a sealing violation with a countdown; this module is called by the orchestrator and has to be
told which fold. It defaults to dev and refuses `test` **by name**, pointing at
`src/eval/run_sealed_eval.py` — the only importer the loader's gate accepts and the one that
appends to `results/sealed_eval_log.md` before anything is read. Refusing rather than
returning an empty fold matters: an empty fold reads as a corpus problem and sends whoever
hit it looking in the wrong place, which is how someone ends up pointing a loader at
`sealed/` to check.

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

### 5.3 An arm's rule files live under the arm, and `rules/{lang}.yaml` is the format example

**Decided 2026-08-08, before the first agent arm was run.** `rules/{lang}.yaml` keeps
exactly two jobs — the committed schema example, and the bootstrap state a first
iteration starts from — and every rule file an arm actually produces is written to

```
results/{corpus}/{detector}/{supervision}/{porting}/rules/iter{iteration}/{lang}.yaml
```

declared as `paths.armrules` in `config/naming.yaml`. `paths.rules` stays, unchanged and
un-widened, for the two jobs it already had.

**The problem is that `rules/{lang}.yaml` erases four axes and the iteration number.**
Every other artefact in this project carries the cell of the experiment it belongs to in
its own path: `metrics.json`, `spans.jsonl`, `window_freeze.json`, `human_log.jsonl`. The
rule file is the one that does not, and it is an *input* rather than an output, which is
what makes the omission easy to miss — nobody looks for an arm's identity in the thing it
reads. But `port-oneshot` and `port-loop` differ in nothing except how the rule file was
produced. With one path they produce it into the same file, and the second arm to run
overwrites the first.

**This is `paths.armfreeze` again, one level down, and the recurrence is the argument.**
§6.3 split `armfreeze` off from `humanfreeze` rather than widening the existing key,
because widening it would have let an agent arm write to `port-human`'s path and silently
overwrite a retired arm's record. The same collision is available here, and it is worse in
one specific way: **an overwritten record is visibly gone, and an overwritten input leaves
a plausible output behind.** After the overwrite, `port-oneshot`'s `metrics.json` still
sits in its own directory with its own `rules_version` integer and its own sorted `rules`
list — a complete, well-formed, internally consistent record of a run whose input no longer
exists anywhere. Nothing in the file is wrong. Nothing in it is checkable either. The
failure mode of the freeze-path collision was "the evidence is missing"; the failure mode
of this one is "the evidence is intact and unverifiable", and the second is the one a
reader cannot detect.

**`port-loop` is why the iteration number is in the path and not only in a log.** An
iterating arm rewrites its rule file every round, and *the sequence of those files is the
experimental record* — it is what the δ/k termination criterion (§3) was computed over, and
the only thing that can answer "which rules existed at iteration 4" after the fact. One
path per arm would keep the last iteration and discard every earlier one, which reduces the
arm's history to its final state. That is precisely the information §5.1 argues aggregates
cannot carry: a porting claim is about a *process*, and a process observed only at its end
is a number without a mechanism.

Two structural details, each for a reason:

- **`{iteration}` is a directory, not a filename suffix.** `es-carmen` emits `es` and `cat`
  in one round (§5.2), and one round's rule state is both files together. Under
  `iter3/{es,cat}.yaml` loading a round is listing a directory; under
  `{es,cat}_iter3.yaml` it is parsing filenames, and a parser is a component whose own
  error rate nothing here measures — the same objection §5.2 raised against a language
  selector.
- **The `rules/` component stays in the path.** `tools/release_screen.py` applies the
  `rule_id` mechanism-vocabulary check to files matching `rules/*.yaml`, and that check is
  the only enforcement `docs/prompts/rule_author.md` Prohibition 2 has — a surname in a
  rule name reaches a public `metrics.json` through the `by_rule` block by the intended
  path, with nothing else in the way. A path without a `rules/` component would leave every
  agent-written file unscreened, and unscreened here does not mean rejected: it means the
  file passes without the check ever running, which is the same "silently matches nothing"
  defect the shared-fixture control (`tests/test_conftest.py`) was built to prevent. The
  screener's pattern is widened to reach the arm-scoped path in the same commit that
  declares the key.

**`run_fold` is told which rule files to load and never infers them.** It takes the paths
as an argument. This is the §5.0 fold-as-parameter asymmetry applied one field over, and for
the same reason: a module that derived its input path from its own axis arguments would have
one code path for "the arm I am closing" and no way to be pointed at anything else, so
`tools/check_rules.py`'s trial runs and the bootstrap file would each need a special case.
Inference would also make the input path a function of the run block, which is the coupling
that lets an arm read its own results directory by accident.

### 5.4 A filled prompt is a type whose text-bearing exits are named and enumerable, and there is one renderer

**Decided 2026-08-08, before the first agent arm was run.** `docs/prompts/rule_author.md` §6
already fixed the rule: only the template is committed, and a filled instance is not
committed, not logged, and not written to disk at all. What is decided here is *how* that
rule is held, because §6 as written is a rule each call site obeys, and this repository has
a measurement of what that costs. The availability defect in `tests/conftest.py` shipped
four times, three of them after it had been written up in `tests/mutations/README.md`. A
written warning is not a control.

So the filled prompt is not a `str`. `src/llm/prompt.py` defines `FilledPrompt`, which has
no public accessor that is not named for a destination:

| exit | destination | declared | written | check |
|---|---|---|---|---|
| `to_terminal(stream)` | a person reading a screen | 2026-08-08 | 2026-08-08 | refuses a stream that is not a terminal |
| `for_transport()` | the model call | 2026-08-08 | 2026-08-08 | none; the transport must not log, and `tools/check_bedrock_logging.py` is what checks that |
| `reference()` | a run block or a log line | 2026-08-08 | 2026-08-08 | returns references, counts and hashes; no text |
| `for_transport_blocks(cache_after=...)` | the model call, split at a cache boundary | 2026-08-16 | 2026-08-18 | same text as `for_transport()`, in the two content blocks a `cachePoint` needs |

**The table carries two dates per exit, and the fourth row is why.** *Declared* is when this
table and the criterion below admitted the exit; *written* is when `src/llm/prompt.py` had it.
For the three original exits the two coincide — one commit added the type and all three methods
(2026-08-08) — and the columns are filled in anyway rather than left blank for them, because a
blank would read as "unknown" and the coincidence is a fact that was checked. The fourth row is
the case that needs the distinction: it was declared on 2026-08-16 by the restatement below, and
between then and 2026-08-18 this table said `added 2026-08-16` while the module said "decided in
DESIGN §5.4 and not yet written". **A declaration and an implementation diverging quietly is a
recurring failure in this repository** — `tests/mutations/README.md` collects the family, and the
`auditor.md` §6 correction two paragraphs down is the same shape caught one day later — so the
state is recorded in the table rather than smoothed over. Collapsing the fourth row to one date
would also erase the event this row is evidence of: the restatement of the guarantee is *what*
admitted a fourth exit, and it happened before the exit existed. The question a later reader
asks is not "when did this method appear" but **"when did this exit pass the admissibility
criterion, and was it written under the criterion that admitted it"**, and two dates are the
smallest record that answers it.

**The guarantee is enumeration, not cardinality** (restated 2026-08-16, before the fourth exit
was written). What the type promises is that **every path by which the text can leave is named
for a destination, and the set of such paths is written down and checked** — not that the set
has three members. Three was the count on 2026-08-08 and the table above was read as if the
numeral were the property; it is not. A count cannot be the guarantee, because the question a
reader has to answer about a new method is *what kind of exit is this*, and a cardinality test
answers that question by refusing every fourth method regardless of kind — which is a test that
fails on the safe change and passes on the dangerous one if it arrives as an edit to an existing
exit.

**Why this restatement does not weaken anything.** `for_transport_blocks()` is the *same kind*
of exit as `for_transport()`: the same destination (the model call, through
`src/llm/bedrock.py`), the same text, the same absence of any logging path, and it is named for
that destination. It splits the text at a declared offset because a Bedrock `cachePoint` needs
two content blocks (`docs/notes/baseline-model-family.md`, 2026-08-16) — a framing difference in
one request, not a new place the text can go. The three original exits are unchanged, and
nothing about which callers may hold text changed with it.

**And the criterion that would have refused it.** An exit is admissible only if it is (a) named
for a destination that already exists in this table's terms, (b) incapable of reaching a file, a
log or a `repr`, and (c) enumerated here and in the structural test. A `text` property, a
`__str__` that returned the text, a `to_file()`, a `debug()` — each fails (a) or (b), and the
restatement rejects them exactly as the old wording did; what the old wording *also* rejected
was a second transport-shaped exit, and that rejection was an artefact of counting. Had caching
needed a method that handed the text to something other than the transport — a cache client of
its own, say, or a serializer — the answer would have been no, and the reason would be (a) and
(b) rather than the arithmetic.

Everything else — `str()`, `repr()`, `json.dumps`, an f-string, a `print` of the object, a
traceback that renders locals — reaches the reference form. That last case is the one worth
naming, because it is nobody's decision: an exception raised while a filled prompt is in
scope travels to a terminal, a CI log, an issue and a stack trace, and CLAUDE.md's rule
about exception messages exists precisely because `release_screen.py` reaches none of those.
A type whose `__repr__` carries the text makes that leak the default behaviour of an error
path nobody wrote.

**The reference form is `human_log.jsonl`'s principle applied to the artefact that cannot
use it directly.** DESIGN §11.2 records a decision as `(doc_id, span_index)` — resolvable by
anyone holding the corpus, inert to anyone who does not. A rule author genuinely needs the
words, so for the prompt itself there is no safer representation available; the answer is a
shorter lifetime instead. `reference()` records the span references, the counts, the
`text_sha256`, the rendered length, and the hashes of both window files. It answers "was
this the prompt that ran" without holding the prompt.

**One renderer, and it lives inside the discipline rather than upstream of it.**
`render_window()` is the only function in the project that slices document text for a
prompt, and it returns `FilledPrompt` and never a string. It was previously
`render_for_author()` in `src/porting/human_arm.py`, returning a `str` that
`tools/show_human_window.py` printed; that implementation is gone rather than wrapped, and
the merge was made now rather than left for later. Three reasons, and the first is *not* the
detection-merge argument:

- **It is not the `check_rules`/`run_fold` case.** That merge rested on undiagnosable
  disagreement — two implementations producing comparable published claims, so drift appears
  as "the sample says the rule fires and the fold-wide run says it does not" with nothing to
  adjudicate. Rendered windows are never published and nobody diffs two of them. Stating
  this is the point: the conclusion is the same and the reasoning is not, and a merge
  justified by the wrong argument is one that gets reverted when that argument stops
  applying.
- **The prompt is hashed into `window_freeze.json` (§6.3), and the hash pins a
  specification.** `rule_author.md`'s banner makes the `port-oneshot`/`port-loop` comparison
  interpretable only if both arms are shown the same blocks from the same code path. Two
  implementations under one hash means the freeze record attests to a template while two
  renderers sit beneath it, which is the same "the record is intact and unverifiable" failure
  §5.3 was decided against.
- **The convention is per-implementation.** A renderer outside the type is a renderer where
  the non-recording discipline has to be re-established by hand, which is the thing the type
  was introduced to stop. Merging later would also mean merging while an arm is running.

The direction of the merge is deliberate: the renderer moved into `src/llm/`, and the
retired arm's harness became a consumer. The reverse — the live agent path importing its
renderer from a retired arm's module — inverts the dependency, and §11's retirement banner
would then bound live code.

**The enforcement is structural, for the reason it always is here.** A renderer that also
wrote a debug copy behaves identically to a correct one on every machine where anyone would
be looking, so `tests/test_prompt.py` checks the syntax tree: no function in the
module writes, logs or prints; the module imports nothing that could; `render_window`'s
return statements construct a `FilledPrompt`; **the public method set equals the enumerated
exits** — the set, asserted by name, and not its size, so adding a method is a change to a
declared list rather than a number nobody can interpret. The renderer's *interior* is in that
set and not only its signature, because the
mutation `renderer_writes_a_debug_copy` leaves the type entirely intact and defeats it
completely — a type protecting a value that has already reached `/tmp` protects nothing.
`release_screen.py` blocks the committed paths a filled instance would land under, and `/tmp`
is not one of them, which is why the convention is "never written" rather than "never
committed" and why a path pattern cannot be the check.

**`docs/prompts/auditor.md` §6 and `src/porting/audit.py` said "the two named exits", and both
were corrected on 2026-08-18 — the first judgement here was that they could stand, and it was
wrong on the facts.** The reasoning was that neither sentence is about how many methods the
type has. Reading them again with the fourth exit in hand, `auditor.md` §6's is: the same
bullet's next clause is **"not a cached prompt"**, and the fourth exit exists precisely so that
part of this prompt *is* cached. So the sentence would have become false in a way that matters
— not a stale numeral but an instruction contradicting what the harness does with the very
prompt the instruction governs. `audit.py`'s sentence is the same phrase naming the same two
methods, and the argument that it "is about `MaskedLine`" does not survive the phrase itself:
it says the text stays behind *two* exits, and after [2] it stays behind three, with a
`FilledPrompt` that is the masker's own return.

Both now name the enumeration rather than a count, and `auditor.md` §6 gains a third bullet
stating the cache boundary positively: the template, the banner and §1.1's frame are on the
cached side and are committed bytes and `naming.yaml` values; **the masked document is on the
far side and is never cached.** That is stronger than the absence it replaces, because a
boundary is checkable and "not cached" was only true while nothing cached.

**One producer for the boundary, and an explicit consumer — `invoke(..., cache=True)` and
never an inference from the prompt's shape** (decided 2026-08-18). The boundary is computed in
exactly one place, `assemble_audit_prompt()`, which is the function that joined the pieces;
that much follows from every other single-producer decision here. The consumption side is a
separate question and it was nearly answered the wrong way. The tidy-looking option is for
`invoke()` to cache whenever the prompt's reference form carries a `cache_after` — no keyword,
no call site to keep in step, the transport simply doing the right thing for whatever it is
handed. It is refused, and the reason is a shape rather than a preference: **caching would then
begin the moment any reference form grows a boundary**, and the RuleAuthor's assembler grows
one the first time someone finds a plausible prefix in it. That is the mutation "the RuleAuthor
prompt is split too" arriving as an *omission* instead of an edit — no line says cache the
RuleAuthor, so no reviewer sees one, and §4's byte-identical claim about round 1 fails without
anything having been written down. A keyword-only `cache=True`, passed by `_audit_fold()` and by
nothing else, makes "this call is cached" a statement at the one call site entitled to make it;
`invoke()` refuses `cache=True` when the reference form has no boundary, so the two halves
cannot silently disagree in the other direction either. `tests/mutations/run.py` carries the
inference form as a mutation for this reason — the argument above is only a control while
something kills the code that would replace it.

**The cost of this edit is zero, and that is a fact about today rather than a principle.**
`auditor.md` is hashed into `WINDOW_FILES`, but **no arm has ever hashed it**: the three frozen
records (`port-oneshot`, `port-oneshot-nofence`, `port-human`) name two files, `auditor.md`
joined the window on 2026-08-12, and `port-loop` has not run. So this is not
`window-freeze-history.md`'s revision 3 — that was prose edited into a file *after* the
surrounding work had settled and, in the revision-8 case, after an arm had spent its call. This
edit precedes every call that will hash the file. The moment `port-loop` freezes, a false
sentence freezes with it and correcting it afterwards costs a re-freeze that
`freeze_window()` refuses outright — the shape `rule_author.md` has already produced twice
(revisions 7 and 8, where the edit was followed by no record and a new arm had to be named).
Logged as the 2026-08-18 entry in `docs/notes/window-freeze-history.md`.

**`rule_author.md` §6 is deliberately left as it was, and this section is the cross-reference
it does not contain.** The prompt is one of the two files hashed into `window_freeze.json`
(§6.3), so a paragraph added to §6 naming this module would move `prompt_sha256` and put the
retired `port-human` arm's freeze record into drift — a seventh revision of a window that has
been frozen six times and never used. `docs/notes/window-freeze-history.md` calls revision 3
"the one with no excuse" precisely because it was a prose edit to a section that changed no
instruction, and a cross-reference to an implementing module is that same class of edit: it
alters nothing about what any agent is shown. The decision belongs in DESIGN, which is not
hashed, and §6 continues to state the rule while this section states how the rule is held.
The general form is worth keeping: **a hashed file is edited when the instruction changes, not
when the implementation of an unchanged instruction moves.**

### 5.5 What `port-loop` adds, decided before it is built — 2026-08-12

Seven decisions taken at planning time so that none of them is taken by whoever is mid-way
through the implementation. The termination rule is §3's; the Auditor's input is §3's; these
are the artefact and plumbing questions the two of those leave open.

- **The freeze record hashes three prompts, not two.** `WINDOW_FILES` becomes
  `rule_author.md`, `auditor.md`, `config/sampling.yaml`. The Auditor's prompt decides what
  the RuleAuthor is shown at §1.3's audit block, so it is part of the window by the same
  argument that puts `sampling.yaml` there — a record naming only the RuleAuthor's template
  would agree with a rewritten Auditor as readily as with this one. `port-loop` is unfrozen,
  so this costs nothing there. **The frozen arms are not touched and not re-hashed**: their
  records attest to the two files that existed when their calls were made, and a third hash
  added retroactively would be a claim about a window that never applied
  (§6.3, `docs/notes/window-freeze-history.md`).
- **`reports/leaks_{iter}.json` is deny-listed, not allowed-and-sniffed.** The report is a
  list of positions an agent believes are surviving PHI, which is a map of the residual
  identifiers in a DUA corpus — the most concentrated such artefact the loop produces. Deny
  is the safe default and the direction §6.1's allowlist argument runs: a path may be excused
  from the *content sniffer* only if the path rules already publish it, and nothing here
  requires publishing this. `metrics.json` and `spans.jsonl` already carry what a reader needs
  about detection; the audit report is an internal input. If a later analysis needs its
  aggregate, that is a derived count added to `metrics.json`, not a re-classification of this
  path.
- **The report path carries the four axes and the iteration.** `reports/leaks_{iter}.json` as
  §3 writes it has no axes at all, which is §5.3's defect exactly: two arms, one file, the
  second overwrites the first and leaves a plausible record behind. The key declared in
  `config/naming.yaml` is axis-scoped and iteration-scoped like `armrules`.

  **That key is `paths.auditreport`, declared and screened in `c998610`, and the two bullets
  above are satisfied by it. There is no second key** — recorded here on 2026-08-13 because
  the implementation order called for one under the name `leakreport` and there is nothing
  left for it to denote. The two bullets are requirements *on a path*: deny rather than
  allow-and-sniff, and axis- plus iteration-scoped. `auditreport` is both, at
  `…/{porting}/iter{iteration}/audit_report.json`, with its `DENY_PATTERNS` entry and its
  `.gitignore` line in the commit that declared it. `docs/prompts/auditor.md` §2.2 names it
  as the Auditor's one artefact and `src/porting/audit.py` assembles its content, so the
  agent-to-file correspondence §3 requires is already single-valued.

  **A second key would have been the failure it was meant to prevent, one layer up.** Two
  keys formatting to one file is worse than the axis-free path §5.3 rejected: there, two arms
  overwrite each other and the surviving file is at least the one path a reader looks at.
  Here both keys resolve correctly, both screen correctly, and the defect is invisible until
  two writers disagree about which name they hold — and §3's "two agents never write the same
  file" becomes uncheckable, because the file has two names and neither is wrong.
  `test_no_two_path_keys_name_one_file` pins this over the whole `paths` block rather than
  over this pair, since the next near-duplicate will be proposed for its own good reason too.

  `reports/leaks_{iter}.json` stays in §3's table, in this section's two bullets above, and
  in `rule_author.md` §5 and §7 as the **prose name of the artefact**, which is what those
  places are naming. It is not a path any code builds; `naming.yaml`'s comment on
  `auditreport` already says so, and the prompt files are hashed into `window_freeze.json`
  and are not edited to say it again (§6.3 — `port-oneshot-nofence` reports zero drift and
  an edit there would be a claim about a call already made).
  **The report is round *n*'s file and it audits round *n−1*'s output, and both numbers are
  read by the round that consumes it.** `auditor.md`'s banner already fixes this — the Auditor
  runs as round *n*'s first step, so its report is written under `iter{n}/` with
  `iteration: n` and carries `masked_from_iteration: n−1` for the predictions it read — and it
  is restated here because `assemble_iteration_prompt()` shipped on 2026-08-13 with the
  relation inverted, demanding `iteration == n−1`. That check refuses the correct report and
  accepts the round-old one, and its message reads plausibly enough that a driver written
  against it would be written to satisfy it.

  So the reader checks **both** numbers against its own round, and this is the part that is
  not derivable from `audit.report()`'s validation: that function verifies the pair agrees
  with itself, which a consistently off-by-one driver satisfies, because it records the round
  it was told. Only the consumer knows which round it is. The heading the agent reads is
  rendered from `masked_from_iteration` rather than from `iteration`, for the same reason —
  the flags describe predictions, and `iter{n}/` holding round *n*'s audit *of* *n−1* is a
  fact about the directory layout that no prompt sentence should depend on. Both mutations
  are in `tests/mutations/README.md`; the loop driver takes the same pair of numbers and must
  not recompute either from a directory listing.
- **`call_line()` gains a `role` field.** RuleAuthor and Auditor both call a model and both
  lines land in one `agent_calls.jsonl`, so `llm_calls` counts them together — which is
  correct for cost and useless for attribution unless the line says which agent spent it.
  Written on every line including `port-oneshot`'s, per the rule that a key some arms omit
  cannot be compared across arms; the frozen arms' existing lines are not rewritten.
- **The window is frozen once and the log carries drift.** `freeze_window()` already refuses
  after the first call and already takes `sections=`, and the temptation is a per-iteration
  re-freeze because `sections_shown` grows at iteration 2. Rejected: a record that can be
  rewritten mid-run is not a freeze, and §6.3's whole finding is that a guard conditioned on
  the record rather than on the call log is not a guard. So `port-loop` freezes once, naming
  the full window it will use across the run, and **every `agent_calls.jsonl` line's
  `window_files` hash is the mid-run drift detector** — which is what that field was added
  for and what `port-oneshot` could not exercise with one line. `sections_shown` on the freeze
  record describes the arm, and the per-iteration truth of which blocks were filled is the
  prompt's own `reference()` on each line.
- **The per-iteration score gets a second key, `paths.itermetrics` — not a widened
  `paths.metrics`.** δ/k is computed over the sequence of per-iteration dev leak rates, so
  that sequence *is* the experimental record, and one `metrics.json` per arm keeps only the
  last, reducing the arm to its final state. This is §5.3's argument about rule files applied
  to scores, and the two must move together: a rule file at `iter3/` whose score was
  overwritten is a rule set nobody can price.

  **The bullet this replaces said "`paths.metrics` gains `{iteration}`" and also "the
  un-iterated path stays valid", and those two cannot both hold of one template.** A template
  is either formatted with an `iteration` or it is not. Corrected on 2026-08-12, before
  either was implemented, because the contradiction resolves in a direction with a
  precedent: this is the fifth-path-component rejection of §4 and the
  `armfreeze`/`humanfreeze` split of §6.3, arriving a third time. `port-oneshot-nofence`'s
  `metrics.json` and `spans.jsonl` are **committed** at four axes deep. A widened key makes
  them matched by no `ALLOW_PATTERNS` entry and unreachable from `metrics_path()` — and
  migrating them is not open, for §4's reason: a relocated result would sit at a deeper path
  while nothing in its content records the move.

  So `paths.metrics` stays exactly as it is, at four axes, and is what an arm's **final**
  score is written to by every arm including `port-loop`. `paths.itermetrics` adds
  `iter{iteration}/` beneath the same directory, in `armrules`'s shape and for its reason —
  `{iteration}` a directory rather than a filename suffix (§5.3) — and only the iterating
  arms write it. The end state of `port-loop` is therefore readable at the same path as every
  other rung, which is what makes the ladder's table a table, and its history is beside it.

  **The duplication is deliberate and it is the cheaper error.** The final iteration's
  score exists twice, at `iterN/metrics.json` and at `metrics.json`. The alternative — final
  score only at `iterN/` — makes reading any arm's headline number a directory listing plus a
  max, which is the filename-parsing objection of §5.3 relocated rather than answered, and it
  makes the un-iterated arms and `port-loop` incomparable at the path layer for a reason that
  has nothing to do with either. Two identical files whose `termination` blocks agree are
  checkable; a headline that has to be computed is not. `run_fold` writes both from one
  scoring pass, so they cannot disagree by construction.

  **Both keys carry the same `ALLOW_PATTERNS` treatment,** since both hold what
  `metrics.json` holds — offsets, types and scores — and the screener's existing entry is
  widened to reach the iteration-scoped path in the commit that declares the key. That is the
  same coupling §5.3 fixed for `armrules`: a path declared in one commit and screened in a
  later one is a path that goes unscreened in between, and unscreened here means the file
  passes without the check ever running.

  **`paths.spans` moves with it — `paths.iterspans`, same shape, same reason.** Noticed while
  implementing the two keys above and added here rather than decided in code. `run_fold`
  writes `spans.jsonl` and `metrics.json` from one pass, so an iterating arm that scoped only
  the second would overwrite its predictions every round while keeping every round's score.
  That is worse than losing both: the per-iteration error export beneath is *derived* from
  that round's predictions against gold, so an `iter3/errors.jsonl` whose `spans.jsonl` has
  been overwritten by iteration 8 is a list nothing can re-derive or check. The three files a
  round produces — predictions, score, errors — are one record and they are scoped together
  or the record has a hole in it. Same duplication rule as `metrics`: the final round's spans
  exist at both paths, written from one pass.

  ##### What a non-iterating arm writes, and how the final round's duplicate is reached

  Two questions the writer forced, settled while implementing it (2026-08-12) and recorded
  here because both have a defensible other answer.

  **A non-iterating arm writes the un-iterated pair and no `iter1/` at all.** The uniform
  alternative — every arm writes `iter1/` too, so the tree has one shape — is refused for
  three costs that point the same way. `port-oneshot-nofence`'s `metrics.json` and
  `spans.jsonl` are **committed** at four axes, so it would gain an `iter1/` duplicate of a
  published result beside them, created by a feature that arm does not have. `iter1/` under
  an arm with no rounds is a false statement about the arm: the directory answers "what did
  round *n* look like", and an arm with one pass has no round 1 to distinguish from a round
  2. And `iter1/errors.jsonl` would then be written by every arm on every corpus — a map of
  the residual identifiers in the fold as a by-product of a feature only the iterating arms
  use, which is the objection above to widening `score()`'s return, arriving at the file
  layer.

  This does not weaken the duplication rule, because that rule runs in one direction: the
  final round's score is *also* at `paths.metrics`, so every arm's headline is at one path.
  A non-iterating arm already satisfies it — its single pass writes there. The rule asks
  that `paths.metrics` hold every arm's final score, not that `iter{N}/` hold every arm's
  only score.

  **The un-iterated pair is rewritten every round, not written once at the end.** The
  obvious implementation of "the final round is duplicated" is a `final=True` argument, and
  no caller can be given a correct value for it: whether round *n* is the last is
  `should_stop(corpus, leak_rates)`'s verdict, and that verdict needs round *n*'s leak rate,
  which does not exist until the round has been scored and written. The flag would therefore
  carry a guess or a re-derivation of the stopping rule, and a wrong guess leaves the arm's
  headline at round *n − 1* with nothing anywhere saying so — §3's objection to a second
  implementation of the rule, in the writer. Rewriting each round reaches the same end state
  without anyone knowing the future. Mid-run the file then holds an unfinished arm's latest
  round, which is legible rather than misleading: its `termination` block says
  `reason: null`, and a run in progress has no final score to hold instead.

  What both answers rest on is that **`run_fold` scores once**. The round's copy and the
  un-iterated copy are written from the same `predictions` and the same `scored` object, so
  the agreement is a property of the code path and not of a convention. Two scoring passes
  would agree today — detection is deterministic — and would diverge the day a rule file is
  edited mid-run or a non-deterministic detector joins the ladder, with *neither file looking
  wrong*: each internally consistent, run and cost and termination blocks identical, nothing
  recording which pass produced which. So the property is tested as one call
  (`test_the_fold_is_detected_once_and_scored_once`) and not only as byte equality of the two
  copies, which a second deterministic pass satisfies.
- **The loop driver is a new module, not a widened `orchestrate.py`.** That file's
  `PORTING = "port-oneshot"` and `ITERATION = 1` are module-level constants and its
  `run_arm()` is one call from start to finish. Widening it would put both arms' control flow
  in one function whose branches differ in everything, and — the decisive part — it would make
  the baseline's driver a file that changes while `port-loop` is developed. The baseline has
  run; its driver should stop changing. Second module, shared helpers (`freeze_window`,
  `call_line`, `append_call`, `called_where`), same reasoning that made `armfreeze` a second
  key rather than a widened one.
- **The scorer's per-span error export goes in `run_fold`, beside `spans.jsonl`, and not in
  `score()`.** Iterations ≥2 need `ErrorSpan`s — `(doc_id, span_index, phi_type, kind, start,
  end)` — and today `score()` returns aggregates while the per-gold verdicts live in
  `_records()`/`_GoldRecord`, which is private and carries no `span_index`.

  Three placements were available. **Widening `score()`'s return** is wrong because that
  return is `metrics.json`'s content: a per-span error list inside it would be published by
  every arm that scores, on every corpus, as a permanent by-product of a feature only the
  iterating arms use — and it is a list of the positions of every missed identifier in the
  fold, which is the artefact the previous bullet deny-lists when an agent writes it. **A new
  public function in `scorer.py`** returning the list without writing it was the close
  second, and is what should be added *inside* the module: the matching must not be
  recomputed outside the scorer, for §9.3's reason that a second matching makes every merge
  policy score the same. **The write belongs to `run_fold`**, which is where §5.0 already puts
  the decision about what closing an arm writes, and which already owns the one write of
  `spans.jsonl` and `metrics.json`.

  So: `scorer` exposes the per-gold verdicts as data — one matching, the same one the metrics
  came from, with `span_index` carried through `_GoldRecord` — and `run_fold` writes them to
  an axis- and iteration-scoped path beside the other two, deny-listed for the same reason as
  the audit report. The loop driver reads that file to build its pool, exactly as
  `initial_error_pool()` reads the loader at iteration 1. What this buys over threading the
  list through in memory is that the pool at iteration *n* is on disk and checkable: "which
  errors was the agent shown at iteration 4" is answerable after the run, which is the same
  property §5.3 wanted from per-iteration rule files.
- **The masker's input is read back from `iter{n−1}/spans.jsonl`, not threaded through the
  driver in memory** (`run_fold.read_spans`, 2026-08-13). Same argument as the bullet above,
  one file over: predictions that exist only while the process lives make "what did the
  Auditor read at iteration 4" answerable only during the run, and the round's record is
  supposed to be checkable after it. The reader takes a required `iteration` — the un-iterated
  copy is whichever round ran last, so a reader pointed there answers "which round is this"
  with "the most recent one", which is the in-memory defect at the file layer. It returns a
  `PredictedSpan` and not a `corpora.base.Span`, because `Span` requires `surface` and
  `subtype` and this file drops both: a reader that filled them would fabricate the field
  whose purpose is re-asserting offsets against real text.
- **The audit report gets its own path builder, and the round check the five builders shared
  four times over becomes one function** (`audit.report_path`, `corpora.base.round_path`,
  2026-08-13). `orchestrate._arm_path` cannot produce `iter{n}/audit_report.json`: it formats
  the four axes, and `{iteration}` is not an axis — §4 refused a fifth path component and this
  section put the round in a *directory*, so a round-scoped path is a different template rather
  than a wider call. The builder lives in `porting/audit.py`, beside the `report()` that
  produces the content, which is the division `run_fold` already has for `errors.jsonl`: one
  module decides what the record says and where it goes, one place writes it. A driver that
  built the path itself would be the second definition site of a location, and the file it
  would misplace is the one deny-listed for being a map of the identifiers a round missed.

  Writing it exposed the larger thing. Four functions — `run_fold._round_path` for two keys,
  `scorer.iter_metrics_path`, `rules.arm_rules_path` — each validated the axes and the round
  independently, and each documented the repetition as the module boundary rather than an
  oversight: **each raises the type its own callers catch.** That is right about the type and
  was doing the work of an argument. Five copies is where the cost stops being hypothetical,
  because what copies drift on is what one of them learns and the others do not — and every
  line of this is a *check*, so the drift is silent by construction: a builder that stopped
  validating its axis raises nothing, it writes a results directory naming a cell nothing
  defines. So `round_path(key, *, iteration, artefact, error, **components)` holds the check
  and takes the exception class as a parameter; each builder keeps its own type and its own
  message subject (`artefact`, which is why a shared refusal still names which of the round's
  files was about to be misplaced). It validates whatever the *template* names rather than a
  fixed four, which is what makes `armrules`' fifth component (`{lang}`) checked rather than
  formatted in unvalidated. What was never duplicated and stays single is the template lookup:
  one `paths` key, one reader (`tests/test_round_path.py`).

  `report_path` validates the round for being a round and **not** for being ≥ 2. That
  constraint stays in `report()`, where it is a fact about the Auditor's schedule rather than
  about a location; duplicating it in a path builder would put one agent's schedule where the
  next round-scoped file inherits it.
- **`cost` becomes the round's and `metrics.json` gains a required `cost_to_date`; the
  addition is `scorer.sum_costs` and the driver holds the accumulator** (schema 7,
  2026-08-13). An iteration of `port-loop` makes **1 + N** calls — RuleAuthor once, the Auditor
  once per dev document — because `Response.cost()` reports `llm_calls: 1` per response and the
  Auditor reads the masked fold a document at a time (§3, `auditor.md`). So for the first time
  in this project a per-round spend and an arm total are different numbers, and three things
  had to be decided: where they are added, which of the two each block holds, and what the
  non-iterating arms write.

  **The addition is the scorer's.** `bedrock` cannot do it — its `cost()` note already says a
  caller summing several responses adds these dicts and *nothing there guesses at a total it
  did not make*, which is the same rule that keeps the lifecycle probe out of `llm_calls`. The
  driver could, and that is the one placement worth refusing on principle: §11.3's 1.9×
  standard is a judgment on a rung's cost, and a rung whose driver both decides how many calls
  to make and computes the total is a rung pricing itself. `scorer` is agent-free and arm-free
  by construction and already publishes and validates the block, so `sum_costs` lives there and
  the driver calls it. It is closed to `REQUIRED_COST` on both sides — a fifth key is refused
  rather than carried, because a token count this project never declared would be summed into a
  published total under a name no reader can place, which is the `termination` block's rule one
  field over. Every key adds, `wall_seconds` included: the Auditor's N calls are sequential, so
  their seconds are additive in the sense `run_fold`'s detection pass and the caller's call
  time already are, and a driver that ever issues them concurrently owes a statement here
  rather than a quiet change in what the field means.

  **Two blocks and not one, because either alone loses something.** Only the round's figure
  says which iteration got expensive, and only the total is what §11.3 compares — and the
  rounds' own files cannot substitute for the total, since the audit report and the error list
  make `iter{N}/` a directory nobody publishes. So `cost` is what the scoring pass's round
  spent and `cost_to_date` is what the arm has spent through it, side by side in one file.

  **`cost_to_date` defaults to `cost`, and for the non-iterating arms that default is the
  measurement.** `R` and the `port-oneshot` rungs run one round, so the round's cost *is* the
  arm's total; requiring them to pass it twice would be a call-site ritual whose only failure
  mode is passing something else. But the key is written **unconditionally**, for the reason
  schema 6 made `termination` required and schema 3 made `model_id`: a block present only when
  it differs from `cost` would be absent for every arm except `port-loop` past iteration 1 —
  a field that cannot be compared across arms, at precisely the number the comparison is about.
  `run_fold` adds the detection pass's seconds to **both**, since this round's detection is
  part of this round and part of the arm, and a total missing them would be smaller than the
  sum of the rounds it contains.

  **The writer validates the relation and never derives the total.** `check_cost_to_date`
  refuses a total below the round it contains, key by key — that state is a reset accumulator
  or the two arguments passed the other way round, and a reader holding one file cannot check
  it. What the writer must not do is add the rounds up itself: that would be a second
  accumulator beside the driver's, and its file would agree with itself while disagreeing with
  the run, with nothing recording which of the two was the arm's cost. Same shape as §5.5's
  duplication rule and §3's stopping rule — one producer per number.

  §11.3's arithmetic is therefore read off `cost_to_date` and its per-iteration breakdown off
  the rounds, and `metrics.json` says which is which without a convention.
- **`paths.formatfailure` stays arm-scoped, because a format failure ends the arm**
  (2026-08-14, decided while implementing round 2). The question was left open by round 1's
  docstring — a round-scoped path (`iter{n}/format_failure.json`) would let a later round fail
  without overwriting an earlier one — and the answer is that there is no later round to
  overwrite it from.

  **A failed round cannot be iterated from, so no round follows one.** Round *n*+1's §1.2 is
  round *n*'s rule file and its §1.3 is round *n*'s score. A format failure produced neither:
  the rule file is on disk but does not load (that is what the failure *is*), and
  `metrics.json` is deliberately unwritten, since `orchestrate._write_failure` is the branch
  taken *instead of* the scoring pass. So the state a round-scoped path is designed for —
  two failures in one arm — is unreachable, and a path built to hold the second would be a
  location that never has an occupant. Same shape as §5.0's argument for exactly one of the
  two files per arm.

  **The enforcement is structural and not a flag.** `loop.run_iteration_2()` reads
  `iter{n−1}/metrics.json` before it does anything else and refuses when it is absent, so a
  round after a failed round stops on a missing input rather than on a check that remembers
  the failure. A boolean recording "the arm has failed" would be a second copy of a fact the
  score file already states by not existing — the cost bullet's "one producer per number" rule
  one field over, since a flag and an absent file are two records of one state and nothing
  says which is authoritative when they disagree.

  **What this closes and what it leaves.** It closes the path question for every rung: one
  `format_failure.json` per arm, at four axes deep, matched by the four-deep `ALLOW_PATTERNS`
  entry that `port-oneshot`'s committed record already relies on (§4's refusal of a fifth path
  component is the same argument at a different depth, and a round-scoped path would have
  needed the second, five-deep entry `itermetrics` and `iterspans` each got). It does **not** claim a failed arm is a finished arm — §10 A2
  fixes format retries at zero and calls the failure a finding about capability, so the arm's
  record is the failure, and `port-loop` ending at round 1 is a result about round 1 rather
  than a run to be resumed.

  **One thing it leaves open, stated rather than fixed here: `format_failure.json` carries
  `cost` and not `cost_to_date`.** `FAILURE_SCHEMA` 2 predates the two-block cost model, and a
  round-2 failure's spend is real — the Auditor's N calls were made and paid for whether or
  not the RuleAuthor's file parsed — so an arm that fails at round 2 has its total split across
  two files: `iter1/metrics.json`'s `cost_to_date` plus this file's `cost`. That is recoverable
  and it is not what §5.5's "two blocks and not one" asked for, which said the total should not
  need a convention to reconstruct. Adding the key is a schema bump on a file a committed
  record already conforms to, so it is deferred to whoever raises the failure schema next
  rather than done in passing; the driver already computes the value it would write
  (`loop.run_iteration` returns `cost_to_date` on the failure branch too).
- **The stopping rule's missing argument travels to the writer, and the verdict does not
  travel back: `PendingTermination`** (2026-08-14, decided while implementing rounds 3+). The
  problem is the one round 2 recorded and left open. A round's `termination` block is a
  statement about *that* round, so it needs that round's dev leak rate; that rate is produced
  by the scoring pass the driver asks `run_fold` for, so at the moment the driver has to supply
  the block it cannot yet compute it. This is the `final=True` impossibility (above) arriving at
  a different argument.

  **Three shapes were refused before this one, each by a rule already in force.** Scoring the
  fold twice — once for the rate, once to write — is two passes that could differ with neither
  file looking wrong, which is exactly why both copies of the round's files come from one
  `score()` call. Letting the driver patch `metrics.json` after the write makes a published file
  have two writers, and §5.5's one-writer rule exists because two writers agree with themselves
  while disagreeing with the run. Letting `run_fold` call `should_stop()` itself puts a
  pre-registered decision inside the code it decides about, which is `src/termination.py`'s own
  argument for being a separate module: a stopping rule living in the thing it stops gets
  adjusted while that thing is debugged, and the adjustment looks like ordinary iteration.

  **The fourth shape sends the argument rather than the answer.** The driver builds
  `PendingTermination(corpus, previous_leak_rates)` — rounds 1..*N*−1's rates, each read from
  that round's own `metrics.json` — and `run_fold` calls `resolve(leak_rate)` with the rate it
  just measured, which calls `should_stop()` and nothing else. None of the three constraints is
  loosened by it: one scoring pass, because the rate comes from that pass; one writer, because
  the block is completed before the file is written rather than edited after; one implementation
  of the rule, because `run_fold` never imports `should_stop` and holds no history. What crosses
  the boundary is one float in one direction.

  **The precedent is in the same function's same argument list.** `cost` is already a partial
  block that the writer completes with `elapsed` — the one quantity only it measures — for the
  identical reason. So this is not a new kind of coupling; it is the existing one at a second
  field, and stating it that way is what stops the next such field from inventing a fifth shape.
  `run_fold` still accepts an already-resolved `Termination`, which is what `not_applicable` is
  and what a non-iterating arm passes.

  **The rate the rule sees is the `fully_covered` headline, read by key.** `resolve`'s caller
  reads `scored["headline"]["leak_rate"]["value"]` rather than naming a mode, so §3's threshold
  is on the quantity CLAUDE.md calls the headline and a mode rename moves both together. A rule
  fed the relaxed lower bound would stop on differences of a different quantity while every
  field in the record still looked right.

  **Two consequences for the driver, and they are the reason the block is returned as well as
  written.** `loop.run_iteration()` reads the resolved block back out of the file `run_fold`
  wrote rather than calling the rule a second time — a second call would be a second answer to
  "did the arm stop", and the two could disagree while each was internally consistent. And it
  refuses to run a round the rule has already stopped, because `should_stop` raises above the
  ceiling: an arm that ran on would be one whose next round cannot evaluate its own stopping
  rule, so continuing past a stop is not a looser reading of §3 but a state §3 does not cover.

  **The `final=True` paragraph above needs one correction, and it makes a weaker claim rather
  than a different decision.** With the block resolved inside `run_fold`, "is this the last
  round" *is* computable there — it is `resolved.stop` — so the flag is no longer impossible,
  only unnecessary. It stays refused because rewriting the un-iterated pair every round reaches
  the required state with no branch at all, and a `final` branch would make that pair's content
  depend on the stopping rule: a δ edit would then change which round the arm's published
  headline came from, and the two files would disagree about the arm while each stayed
  internally consistent. Fewer states beats a correctly-computed flag.
- **Round *N* reads round *N*−1, and that is the loop rather than a fact about round 2**
  (2026-08-14, same commit). `src/porting/loop.py` has two functions and not eight:
  `run_iteration_1()` for the arm's first call, and `run_iteration(iteration, …)` for every
  round after it. Round 3 is round 2 with a longer history, and the history is read from disk.

  All four feedback inputs come from the immediately preceding round and from nowhere else:
  §1.2 is its rule file (`paths.armrules`), §1.3 is its `metrics.json` reduced plus the audit of
  its predictions, §1.4 is a seeded draw over its `errors.jsonl`. So the generalisation is
  total, and a `run_iteration_3()` would be a second copy of one body whose drift from the first
  is undetectable — the two would assemble different prompts under the same `porting` value and
  every result would still look right. The round number is a positional first argument with no
  default, because a default round number is a round chosen by whichever caller forgot.

  **Rounds are contiguous from 1 by construction, and nothing separate enforces it.** The
  leak-rate sequence the rule needs is read one round at a time through the same reader that
  refuses a missing score, so an arm with a gap cannot assemble a history that silently omits
  it. That is the same mechanism the format-failure decision above relies on, used for a second
  purpose rather than duplicated: one absent `metrics.json` stops the next round whether it is
  absent because the round failed or because it never ran.

#### The arm's published result is its final round, never its best — stated explicitly 2026-08-24

**This was already true of the implementation and was nowhere stated as a policy.** The un-iterated
pair is rewritten every round (above), so after the last round `paths.metrics` holds that round's
score and `paths.spans` its predictions. Nothing chooses. That is the intended behaviour and it is
recorded here as a decision rather than left as a consequence, because a consequence is something a
later commit can change without noticing it was load-bearing, and a decision is not.

**Choosing the best round is forbidden, and the reason is the one §3 gives about δ.** The best round
is identifiable only from the leak rates, i.e. only after the arm has run. Picking it is selecting a
result with the result in view — the same objection §3 records against choosing δ with
`port-oneshot`'s number known, and against §11.3's cost threshold picked after the fact. It would
also make the reported figure depend on a choice no other arm in the ladder makes: `port-oneshot`
has one round and cannot select, so a selecting `port-loop` would be compared against a
non-selecting baseline and would win partly by the selection.

This is not hypothetical for `port-loop` on es-meddocan. Round 4 reached 0.195089 and round 5
0.233536, so the arm's best round is not its last, and if it terminates now the published headline
is the worse number. That is the cost of the rule and it is paid rather than avoided.

**Publishing the whole trajectory is therefore an obligation, not a courtesy.** A final-round
headline is only honest if the rounds behind it are visible, so every report of a `port-loop` result
publishes, for every round, the dev leak rate in both modes and the first difference. The per-round
paths already make this possible without any new artefact — `iter{N}/metrics.json` exists for every
round and each carries its own `termination` block, which is exactly what §5.5's per-round
duplication was for. The obligation is on the report, because a reader who has to reconstruct the
trajectory from a directory listing will not.

The `converged`-on-a-regression case adds two further requirements to the same report (§3, this
date): name the best round explicitly with both figures, and say that the final round is what is
published and why. Those are stated there rather than repeated here.

#### 5.5.1 `errors.jsonl` is not a `FilledPrompt`, and where that stops being true

Asked and answered while implementing the export, 2026-08-12, and recorded because the
question is going to be asked again: `errors.jsonl` becomes the input to `rule_author.md`
§1.4's error block, so it looks like it should inherit §5.4's discipline. It does not, and
leaving that as a judgement in a commit message means the next person re-decides it.

**The rule.** `FilledPrompt` is for functions that **slice document text**. `render_window()`
cuts the ±120-character contexts and the masker (`port-loop`'s stage 4) produces masked text;
those functions could return `str`, a `str` goes anywhere, and the type is what forces the two
named exits. `ErrorSpan` has **no text field by construction** (`src/sample.py`) and this file
carries six values — `doc_id`, `span_index`, `phi_type`, `kind`, `start`, `end`. There is
nothing to wrap, because there is no slice.

**Wrapping it anyway would weaken the convention, which is the load-bearing half of this
decision.** The guarantee here is *no surface forms exist in the object*. The guarantee
`FilledPrompt` gives is *text that does exist leaves only through named, enumerated exits*.
Wrapping the first in the second substitutes "it is a `FilledPrompt`, so it is safe" for "it
has no surface form, so it is safe" — and the second is the true reason. Once the weaker claim is what the code asserts, a
later field addition satisfies the type and breaks the fact.

**The path rules carry the defence instead, and they are not redundant with the absence of
text.** `paths.itererrors` is deny-listed in `config/naming.yaml` and matched by
`tools/release_screen.py`'s `DENY_PATTERNS`, with the paired `.gitignore` entry. The reason is
independent of surface forms: a list of the offsets of **every** missed identifier in a DUA
fold is a map of the residual identifiers in that fold, drawn from gold, and **offsets plus
the corpus resolve to the text.** §11.2's referent property is exactly this — resolvable by
whoever holds the corpus, inert to anyone else — and "inert to anyone else" is a statement
about the reader, not about the file. So the file is safe to *exist* because it has no text,
and it is denied *even so* because of what it is. Two guarantees, two mechanisms, neither
standing in for the other. `tests/test_run_fold.py` asserts both on the real path
(`test_the_export_path_is_denied_by_the_screener`, `test_the_export_path_is_gitignored`),
through `deny()` rather than against the pattern's text, because a pattern that matched
nothing would be a rule reported as present and never run.

**The boundary, stated so it is not mistaken for a migration path.** The loop driver reads
this file and hands **references** to `render_window()`, which slices the text itself, from
the corpus, inside the type. That separation is what makes the whole arrangement hold. So:

> The moment anything adds a `text`, `surface`, `context` or `snippet` field to
> `ErrorSpan` or to a row of `errors.jsonl` — including "just for debugging" — that is
> **not** the signal to move the file under `FilledPrompt`. It is the signal to refuse the
> field.

Moving it under the type at that point would be answering the wrong question: the file's
safety would then depend on a wrapper rather than on a schema, and a deny-listed path holding
window text is one `ALLOW_PATTERNS` edit away from publication, where a path holding
references is not. The renderer already exists and already has the discipline; there is no
work the field would save that is not already done one layer up. `write_errors()` enumerates
its six fields rather than dumping the object for this reason — a whitelist refuses the new
field on the day it is added, and this file is the one where "the day it is added" means
publishing it into the window §1.4 builds.

#### 5.5.2 Re-running an incomplete round overwrites its audit report, and the log and the report then disagree — decided 2026-08-24 (1 + 2, not 3)

**The retry is legitimate. The record is the problem.** Round 5 was attempted three times: two
attempts died on the RuleAuthor call after their 250 Auditor calls had completed, and the third
succeeded. Re-running was the right action — the round had produced no leak rate, so nothing was
scored, nothing was selected, and no result was seen and re-rolled. §6's prohibition is on choosing
between outcomes, and there were no outcomes to choose between.

What the three attempts left behind is inconsistent. `run_iteration` runs the Auditor
unconditionally and writes `iter5/audit_report.json` with no reuse path, so:

- `agent_calls.jsonl` holds **750** Auditor lines for iteration 5 — three complete draws, all
  logged, correctly.
- `iter5/audit_report.json` holds **one** draw, the third. The first two were overwritten, and the
  first was overwritten unseen.

So the two files disagree about how many times the arm audited, and neither is wrong on its own
terms: the log records calls and the report records the latest artefact. The disagreement is only
visible to a reader who counts, and the count is 3× what a reader would expect from the round's
`llm_calls`. **The substantive fact is that round 5's prompt was built from a second redraw of the
Auditor** — not a selected one, but not the first one either, and the sampling that fed §1.4 is
therefore a draw whose two predecessors are unrecoverable.

**Options as they were framed, before the choice.** Kept in the words they were written in, because
the decision below reads as obvious once made and the record of what it was weighed against is the
part that does not survive paraphrase:

1. **Preserve every draw.** Write `audit_report.json` per attempt — `audit_report.attempt2.json`, or
   a directory of draws — so the log's 750 lines have 750 lines' worth of artefact behind them.
   Strongest record and the only option under which the overwritten draws are recoverable at all.
   Costs a path convention, a `release_screen.py` deny rule per new name (the report is denied
   today by an exact-name pattern, so a new name is unscreened until the pattern is widened — see
   the `the_audit_report_is_allowed_instead_of_denied` mutation), and it makes an arm's directory
   listing carry its failure history, which no other artefact does.
2. **Stamp the draw on the report.** Keep one file, add `attempt: 3` and the count of prior
   discarded draws. Cheapest, and it removes the *silent* disagreement without removing the
   disagreement: the reader learns that two draws existed and are gone. Requires deciding where the
   attempt counter lives, since the failing process cannot increment anything durable — it dies
   before writing — so the count would have to be recovered from `agent_calls.jsonl`, which makes
   the report's honesty depend on a log it does not read.
3. **Refuse to re-run an incomplete round.** Make `run_iteration` detect that iteration *n* already
   has Auditor lines in `agent_calls.jsonl` with no `metrics.json`, and require an explicit flag to
   proceed. Turns a silent overwrite into a decision at the point it is made, which is the only
   option that catches the case where re-running is *not* legitimate — a round that failed after
   scoring, where a re-run would be a second draw with the first one's result already known. Costs
   the most: a resume/force story, and a failure mode where a genuinely stuck arm needs a flag to
   move, which is exactly when nobody wants to be reading documentation.

The three are not exclusive; 2 and 3 compose, and 1 subsumes 2's information at higher cost. What
they trade against each other is *record completeness* versus *the number of things that must be
right for the record to be true*.

**Decided: 1 and 2 together, and 3 refused.** Implemented 2026-08-24 in `src/porting/audit.py`
(`draw_path`, `next_draw`, `with_draws_total`) and `src/porting/loop.py`.

**3 is refused, and the hole it would have closed stays open. Named here, not glossed.** A re-run of
an *incomplete* round is explicitly allowed and needs no flag: refusing it means a transport timeout
ends the arm, which is precisely what happened at round 5 and is a harness failure recorded as a
result. But the case option 3 was written for is **not** unreachable. `run_iteration(N)` reads
rounds 1..*N−1* and consults the stopping rule on their rates; nothing in it looks at whether round
*N* already has a `metrics.json`. So re-running a round that **did** score is permitted today, and
it would overwrite that round's result with a second draw taken in knowledge of the first — which
is §6's prohibition, not a bookkeeping problem. `tests/test_loop.py` demonstrates the mechanism
directly by running round 2 twice.

What the decision buys against that case is **detectability, not prevention**, and the two must not
be confused. Before this change a re-run of a scored round left no trace at all: one report, one
metrics file, and a call log whose count nobody reconciles. After it, the same act leaves a
`draw2/` directory, a `draw_index: 2` in the canonical report, and an `abandoned_spend` block whose
`attempts_abandoned` is above zero — so a reader who checks can see that it happened, and the
sealed-eval discipline that exists for exactly this class of act (§6) has something to check. A
guard is still the right thing and is not written here, because the flag it needs is a resume story
and this change was scoped to the record rather than to the control flow. **Anyone re-running a
round must confirm it produced no `metrics.json` first**; that is a rule about conduct until it is a
rule in code, and it is written down so that it is one or the other rather than neither.

**1 is implemented as a subdirectory and not a filename suffix, and that detail is the whole of the
screener cost the option was charged for.** `paths.auditdraw` is
`…/iter{iteration}/draw{draw}/audit_report.json`. `tools/release_screen.py` denies this file by name
— `(^|/)audit_report\.json$`, so that `metrics.json` and `spans.jsonl` in the same round directory
stay publishable — and a `audit_report.draw2.json` would escape that pattern, making the fix a
widening of a deny rule to cover a newly invented name, under time pressure, on the rule protecting
a map of the identifiers a round failed to catch. A subdirectory inherits the protection with **no
screener edit at all**; `tests/test_audit.py` asserts the deny through `deny()` rather than by
reading the pattern. `next_draw` is one past the highest existing `draw{M}/` rather than a count of
them, so a gap stays a gap instead of causing a number to be reused and a preserved report
overwritten — the one failure this path exists to prevent.

**2 is implemented as `draw_index` plus an optional `draws_total`, and the option's stated
weakness is answered rather than accepted.** The objection was that the failing process cannot
increment anything durable, so the count would have to come from `agent_calls.jsonl` and the
report's honesty would depend on a log it does not read. It comes from the directory listing
instead: `next_draw` reads the state the previous attempts left on disk, which is exactly the
record that survives a process dying. `draw_index` is always written because it is always knowable
— the driver is the thing that re-ran. `draws_total` follows `caching`'s convention: present where
knowable, absent where not, so **its presence means this report is the latest draw**. The preserved
copy at `paths.auditdraw` is written first and without the total, the canonical copy at
`paths.auditreport` is rewritten at every draw with it, and a reader then holds two independent
counts to check against each other — the field, and the number of `draw{M}/` directories. Writing
`draws_total: 2` into a round that turns out to have three draws would publish a false count, and
going back to re-stamp preserved reports would make something a second writer of an already
published file, which §5.5's one-writer rule refuses.

**And the spend of the abandoned attempts is accounted separately, which neither option covered.**
The draws answer "how many times was this round audited"; they do not answer "what did the attempts
that produced nothing cost". `metrics.json` schema 9's `abandoned_spend` block does, measured off
the call log at the moment the attempt begins — see §3's clause on round 5's two failed attempts for
the figures, for why round 5's own are not backfilled, and for the two of them that are
unmeasurable rather than merely unrecorded.

**The draw count is a record of completed audits, and not of spend — round 6 is the case that made
the difference matter.** An attempt that dies partway through the audit writes no report, so it
leaves no draw directory to number: `draw_index` stays 1 while the round has already paid for
however many calls it got through. The block therefore gates on either record, a draw **or** a
logged line, and `attempts_abandoned` is a lower bound in consequence (§3's round-6 clause states
both the fix and the bound). Two things follow for the paragraph above. A re-run of a *scored* round
is still detectable, because a scored round completed its audit and so does have a draw to number.
And the plan `tools/run_loop.py` prints before any call is paid for shows the round's logged-call
count whenever the two records disagree, since `draw 1` on its own would read as a round that had
spent nothing.

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

**The `rule_id` vocabulary has been widened twice, and a third time triggers a review of the
mechanism rather than a third widening — recorded 2026-08-12.** Both widenings were made
against an agent arm's output, and both were judged correct on their merits at the time. The
commitment here is not to stop widening; it is that **the third occurrence is evidence about
the design and has to be treated as such**, because two data points cannot distinguish "a
closed set being completed" from "a closed set that cannot be completed", and three begin to.

> **The third arrived on 2026-08-21, and the prediction this section recorded about it was
> wrong.** What was written was: *"it predicts the third occurrence will again be a spelling
> variant rather than a new language."* It was neither. The review it triggered is below,
> after the record of the first two that the prediction was drawn from.

**Were the two the same cause? Partly — and the difference is the useful part.**

- **First widening (`ebac362`, 2026-08-11, first `port-oneshot` run).** 23 of 28 rule names
  were SUSPECT, every one for a single reason: the agent named rules after **target-language
  clinical formulae** (`paciente_cue`, `calle_cue`, `firmado_cue`) against a vocabulary that
  was English-only. Prohibition 2 permits clinical formulae and forbids designating an
  individual, so the names were compliant and the vocabulary was wrong. The fix was structural
  — `RULE_ID_VOCAB_BY_LANG` (es · cat · de · ko · en), keyed on `lang` from the *file path*
  rather than the id prefix, with three categories excluded in every language. That commit
  also placed `dmy`/`postal`/`years`/`months`/`spanish` in the English vocabulary as mechanism
  and structure words, and `nuss` in `RULE_ID_ALLOWED_TOKENS` as a national identifier
  abbreviation.
- **Second widening (`cca0cf3`, 2026-08-12, `port-oneshot-nofence`).** Two names, both
  **abbreviations**: `nass` — the same Spanish social-security scheme as `nuss`, the other
  spelling in circulation, i.e. the identical category to an entry the *first* widening had
  itself just added — and `gaz`, the short form of `gazetteer`, a word already in the English
  vocabulary and also a value on the `layer` axis.
- **Third widening (2026-08-21, `port-loop` round 1).** Three names, three tokens:
  `title_licenciado_prefix`, `date_iso_pattern`, `phone_landline`. Split across the same two
  homes as the first widening — `iso` and `landline` into the English mechanism vocabulary,
  `licenciado` into the Spanish layer — and **not one of them a variant of anything already
  there.** `licenciado` is a degree-title honorific, the same category as `don` and `doctor`,
  which the layer had and which `licenciado` was simply missing from. `iso` names a date
  *notation* where the set had component *orders* (`dmy`/`mdy`/`ymd`). `landline` names a kind
  of `phone`, where the set had the channels (`phone`/`fax`/`email`/`url`) and not their kinds.
  Each was answered by filling the category rather than adding the token: degree honorifics
  with both genders and their abbreviations plus the signing roles beside them; notation names
  and locale markers beside `iso`; the telephone kinds and `contact` beside `landline`.

So the shared cause is narrower than "target-language tokens": it is **abbreviation and
inflection of concepts the vocabulary already contains**. `nuss`/`nass` and
`gazetteer`/`gaz` are the same relation as `year`/`years` and `abbreviation`/`abbrev`, both
of which the first widening had to repair in the same way. The genuinely
target-language-specific failure happened once and was answered structurally; what has now
recurred is the *closed-set-of-surface-forms* failure, which the per-language layers did not
address because they are also closed sets of surface forms. That is the pattern to watch, and
it predicts the third occurrence will again be a spelling variant rather than a new language.

**That prediction was wrong, and it is left standing above with the correction here — 2026-08-21.**
Not one of `iso`, `landline`, `licenciado` is a spelling variant, an abbreviation or an
inflection of an entry the set already held. All three are **absent members of categories the
set already had**, which is a third possibility the dichotomy above did not contain: it offered
"a derivational variant" or "a new language", and the observation was neither. The error was
generalising from two events that shared a mechanism (`nuss`→`nass`, `gazetteer`→`gaz`) to a
claim about the *next* event, when what those two established was only that derivational
variants are *one* of the ways the set is incomplete.

**What that costs, stated plainly: the fix this section had pre-chosen would have prevented
none of it.** Option 1 below — normalise plural and prefix relations before matching — is
aimed precisely at `gaz` ⊂ `gazetteer`. `iso`, `landline` and `licenciado` are not prefixes or
plurals of any entry, so a normalising matcher screens them exactly as the current one does.
The pre-chosen minimum response was therefore a correct response to the *observed past* and
not to the recurrence, which is the specific way a prediction recorded as a decision rule can
mislead: it made a fix feel ready.

**The review this section committed to, conducted 2026-08-21.** The question it was to decide
was whether "a closed vocabulary of mechanism words, maintained by hand, per language" is a
check that converges. On three data points the answer is **no, it does not converge on its own
— and it does not have to, because what it costs is now measurable and small.** Each new
rule-authoring call is a fresh sample of natural mechanism vocabulary, so each one can name a
category member the set lacks; that source is not exhausted by any amount of hand-filling. But
the post-layer rate is 2 tokens (`port-oneshot-nofence`) and 3 tokens (`port-loop` round 1) per
rule file, against 23 of 28 names before the layer existed — the structural fix did the work,
and what remains is a per-call trickle. So the mechanism stays, and the response is to make the
trickle cheap rather than to replace the check. **Option 3 is the chosen response for the fourth
occurrence** — have the screener print a proposed vocabulary entry for each unrecognised token,
with the file and rule it came from — and it is chosen *and not implemented here*, because
implementing it inside the widening it was diagnosed by is how a change arrives without its own
review. Options 2 and 4 are rejected on the reasoning already recorded below: 2 weakens the
guarantee the check exists to make, and 4 relocates enforcement away from the pre-commit gate.

**A fourth prediction, and the grounds for it.** Two post-layer rule files have been screened
and both carried unrecognised tokens (2 and 3). That is the whole of the evidence, so the
prediction is restricted to what it supports: **round 2's rule file will very likely carry
between one and four unrecognised tokens, and they will be absent members of existing
categories rather than derivational variants.** Grounds: 2 of 2 post-layer files did this, the
generator is the same model on a prompt that differs only in §§1.2–1.4, and the categories most
recently found incomplete (notation names, channel kinds, role honorifics) are the ones a rule
author reaches for when it renames a mechanism. What would falsify it: a clean round 2, or a
token that *is* a variant of an existing entry. **No prediction is made about rounds 3–8** —
the widening just made covers three categories at once, so the next file is screened against a
different set than the last two were, and there is no measurement of what that changes.

**One more thing this occurrence repeated, and it is procedural.** All three widenings were
made with `test_every_current_false_positive_is_covered` failing. This time it was deliberate
and is on the record: commit `e6a0097` published the arm's rule file with the finding reported
in its message rather than folding the vocabulary change into the results commit, which keeps
the widening reviewable as its own diff at the cost of one red commit in between. That is the
trade the fourth option under "make the widening cheap" is about, and it is the reason the
proposed-entry line is worth having: it moves the reviewer's work off reconstructing a category
under a red suite.

**Options for a root fix — recorded 2026-08-12 before the third arrived, and the choice among
them is stated above.** The constraints any of them must respect: the screener imports only the standard
library and runs before every commit (so no YAML, no model call, no network); the check is the
only enforcement Prohibition 2 has (§5.3); and the vocabulary must not be bound into
`docs/prompts/rule_author.md`, because that changes call 1's bytes and makes every existing
arm a different arm (§6.3), besides testing naming compliance instead of rule authoring.

- **Normalise before matching.** Strip a small set of derivational relations — plural `-s`,
  and a prefix-of-a-listed-word rule with a minimum length (`gaz` ⊂ `gazetteer`, `abbrev` ⊂
  `abbreviation`) — so one entry covers its own variants. Cheapest, and it removes the class
  that has actually recurred. The cost is real and is the reason it was not done first:
  prefix matching is *substring* membership, and `the_language_layer_is_a_substring_test` is
  already a mutation on this file — its whole point is that tolerance for inflection is what a
  containment test looks like from the outside, while `ana` then passes on `anos` and `mar` on
  `marzo`. A prefix rule is a deliberately narrowed version of what that mutation forbids, so
  it needs an anchor at the start, a minimum length, and a mutation of its own proving the
  narrowing is what holds.
- **Invert the check for a named class: an identifier-abbreviation *shape* instead of a
  list.** `nuss`/`nass`/`dni`/`nie`/`nhc` are all 3–4 letter uppercase administrative
  acronyms; a rule admitting short all-consonant-ish tokens in that shape would cover the
  whole class without enumerating it. This is the option that most reduces future widening and
  the one that most weakens the guarantee — a surname initialism has the same shape, and the
  check's entire premise is that a name assembled only from mechanism words cannot designate
  an individual. Would need the exclusion categories restated as a positive test.
- **Make the widening cheap and visible instead of rare.** Keep the closed set, but have the
  screener emit, for an unrecognised token, a one-line proposed entry with the file and rule
  it came from, so the reviewer's job is to judge a category rather than to reconstruct one.
  Does not reduce the number of widenings at all; reduces the chance that a widening is done
  carelessly under a red baseline, which is the actual risk. The second widening was made with
  `test_every_current_false_positive_is_covered` failing and the mutation harness refusing to
  run on a red baseline; the first was too, by inference rather than observation — that test
  already existed at `ebac362^` and the arm's rule file was in the working tree, so the arm's
  output turns the suite red the moment it lands, and the fix and the green suite are the same
  commit. A widening under a red baseline is a change made while the harness that would check
  it is unavailable, which is the least favourable moment to be judging a category.
- **Move the check off names and onto the thing it is protecting.** The concern is a surname
  reaching a public `metrics.json` through `by_rule`. A check on the published *keys* at
  publication time, rather than on the ids at authoring time, would not need a vocabulary of
  mechanism words at all. Largest change, and it relocates enforcement from the pre-commit
  gate to the writer, which is the direction §6.1 argues against (a gate people run vs. a
  property a writer must remember).

**What the review would actually decide** is whether "a closed vocabulary of mechanism words,
maintained by hand, per language" is a check that converges. If widening three is again a
spelling variant, the answer is no and the first option is the minimum response. If it is a
genuinely new category, the vocabulary is doing its job and the entry is just an entry.

> **Neither branch fired.** Widening three was not a spelling variant and not a new category —
> it was three absent members of categories already present, so the two-branch rule above had
> no answer for what happened. The review was conducted anyway and its outcome is recorded
> above; this sentence stays as written because a decision rule that did not cover the case is
> worth being able to read afterwards.

**Fourth widening (2026-08-23, `port-loop` round 2), and the fourth prediction was also
wrong.** Seven tokens across nine names: `text`, `standalone`, `org` into the English
mechanism vocabulary; `localidad`, `instituto` into the Spanish layer; `cipa`, `ncol` into
`RULE_ID_ALLOWED_TOKENS`. Against the prediction recorded above — *one to four unrecognised
tokens, and absent members of existing categories rather than derivational variants* — both
halves failed, and both of the falsifiers that prediction named its own name fired:

- **The count was 7, outside the predicted 1–4.** The grounds were two post-layer files at 2
  and 3 tokens; the third data point is more than double the largest of them. The rule file
  also grew from 31 rules to 37, so a per-file count was being predicted for a file of
  unpredicted size — a flaw in the *quantity chosen*, not just in the estimate, and the same
  flaw would recur for rounds 3–8 since nothing bounds how many rules a round writes.
- **Two of the seven are derivational variants of entries already present**, which the
  prediction said would not happen. `ncol` is the abbreviation of `número de colegiado` and
  `colegiado` was already in the Spanish layer — the exact `gazetteer`/`gaz` relation.
  `cipa` reads as the four-letter form of `cip`, which `RULE_ID_ALLOWED_TOKENS` already held —
  the exact `nuss`/`nass` relation. The other five are absent members as predicted
  (`org` is the general word for a category whose members `hospital`/`clinic`/`institution`
  were all present; `text` names a rendering where `numeric`/`written`/`spelled` were present;
  `localidad`/`instituto` are Spanish kinds beside `provincia` and `hospital`).
- **One of the seven is neither**, and this is a third thing the dichotomy did not contain:
  `standalone` names a **box the vocabulary did not have**. The set held `cue`, `trigger`,
  `context`, `window`, `with` and `without` — every word for a cue *being there*, and
  `without` only as a modifier. No word named the contextless variant of a rule as a thing,
  which is what `date_year_standalone` and `postal_code_standalone` needed. So the fourth
  occurrence produced all three kinds at once: variants, absent members, and a missing
  category.

**The observation worth keeping is about the predictions rather than the tokens: they were
wrong in opposite directions.** The third prediction said *it will be a derivational variant*
and got none. The fourth said *it will not be a derivational variant* and got two. A single
generator produced both outcomes under prompts differing only in §§1.2–1.4, which is the
strongest available evidence that the kind of the next unrecognised token is not the sort of
thing these four observations can predict — each prediction was drawn from the immediately
preceding widening and each was refuted by the one immediately after. **So no fifth prediction
is made, and that abstention is the finding rather than a gap in the record.** What can be said
without predicting: the per-file count has been 2, 3 and 7, all after the structural fix and
all far below the pre-layer 23 of 28, so the trickle is real and the mechanism holds. What
cannot: which box the next token will come from, or whether there will be a box.

**The response was the same as the third: fill the categories, not the names.** Fourteen tokens
were added for the institution box, nine for the rendering box, nine for the contextless box,
thirteen and sixteen for the two Spanish boxes, six for the identifier abbreviations — against
seven observed. Most were not seen in any run, which is the point (`ebac362`'s reasoning): a
vocabulary that grows one observation at a time is a denylist wearing a positive face. Two of
the Spanish additions (`barrio`, `sala`) are also Spanish surnames, and they were included
rather than skipped because the guarantee is about the *assembled* name — `salud` (a given
name), `alta` and `consulta` have all been in the set since the first widening for the same
reason, and `barrio_lopez` is still refused, on `lopez`.

**Option 3 is implemented in the commit after this one, and it needed a correction option 3
did not anticipate.** As recorded above, the option was to have the screener "print a proposed
vocabulary entry for each unrecognised token, with the file and rule it came from". That
conflicts with a rule this file's own `sniff()` already follows and states in a comment: the
`rule_id` is **not** quoted in the finding, because it may be the surface form itself and the
message reaches terminals, CI logs and issues that nothing screens (CLAUDE.md). An
unrecognised token is by construction the best candidate in the file for being a surname, so
printing it by default would route the least screened value onto the least screened path — the
option as written would have been a small leak channel added in the name of convenience. The
resolution keeps both properties: `rule_id_proposals()` and `format_proposals()` compute the
lines, and they are printed only under an explicit `--propose` whose banner says what the
operator is about to read. The default output is byte-identical to before. Nothing the option
was for is lost, because the reviewer who runs `--propose` is reading a rule file that is
published by path anyway; what is kept is that the automatic pre-commit path stays quiet.

**And it is implemented now rather than on the fifth occurrence.** The purpose is to make a
widening cheap and visible *before* the next one, and an option chosen on the third occurrence,
deferred on the fourth, would be a tool that arrives after every event it was meant to help
with. Its own review is the paragraph above and the split diff: the vocabulary change and the
proposal machinery are two commits, so the widening is still reviewable as its own diff and
the new code is not hidden inside it.

#### Fifth widening (2026-08-23, `port-loop` round 3) — the mechanism worked, and one of the three categories should not have been a category at all

Three tokens, `motivo`, `parenthetical` and `location`, and this is the first widening whose
tokens were not reconstructed by hand: `--propose`, committed one commit before round 3 ran,
printed all three with the rules that reached for them on its first live use. That is the
whole return on option 3, and it is worth stating plainly because the four preceding widenings
each cost a manual reconstruction from a count and were each judged under a red suite. No
prediction was made about this widening and so none was missed — the abstention recorded above
held, and what follows is a description rather than a scoring.

**Two of the three are ordinary, and the third is a missing invariant wearing a category's
clothes.** `motivo` is an empty slot in an open box: the Spanish layer already held `ingreso`,
`alta`, `consulta` and `informe`, which are clinical-note *section headings*, and a section
heading is where a rule decides to put its window. `parenthetical` is the same shape as the
fourth widening's derivational pairs, in the direction the third widening's rejected option 1
was aimed at — `paren` was already in the set, and the word for the *form* produced by that
delimiter was not. Option 1's normalisation would have caught this token and would still not
have caught `ncol` or `cipa`, which is the same verdict as before with one more data point
rather than a reason to revisit it.

**`location` is different, and the difference is that no observation was needed.** It is a
value of this project's own `phi_type` axis in `config/naming.yaml`, as are `area`, `id` and
`other`; `tagger` is the one remaining value of the `layer` axis. An axis value is a category
*this repository defined*, a category name designates a class rather than a member, and a rule
author naming the type a rule targets is doing the ordinary thing (`en_location_cue`). So these
words were never candidates for exclusion, and their absence was an oversight rather than a
judgement — demonstrably so: `gaz` was admitted on 2026-08-12 with "it is also a `layer` axis
value" as part of the argument. The right argument was already written down and was applied to
one word instead of to the axis.

**The response is therefore not a longer list.**
`test_every_phi_type_and_layer_token_is_in_the_vocabulary` reads both axes from
`naming.yaml` and requires every token, so adding a `phi_type` or a `layer` fails the suite
until the vocabulary follows in the same commit. This closes the category rather than filling
it: there is no sixth widening of this kind. The two axes are named explicitly and the other
six are not — `corpus`, `detector`, `porting`, `split`, `supervision` and `lang` name the
experiment rather than what a rule does, they do not appear in rule names, and admitting them
would put `es-meddocan` in the mechanism vocabulary for nothing.

The screener stays dependency-free (stdlib only, because it runs before every commit), so the
invariant lives in the test rather than in a `naming.yaml` read inside `release_screen.py`.
That is the same trade as `RULE_ID_VOCAB_BY_LANG` keying on the file path rather than on the
id prefix: the check itself stays cheap and the thing that could drift is pinned by a test.

**What this says about the four widenings before it.** Three of the five have now produced a
token that was a generic of kinds already present — `contact` (third), `org` (fourth),
`location` (fifth) — and in the fifth case the generic was sitting in `naming.yaml` the whole
time. The pattern is not "the vocabulary is short of words" but "the vocabulary was assembled
from kinds and the names of the kinds' categories were left out". `phi_type` and `layer` are
the two axes where that omission is now impossible. The other two categories this widening
touched, section headings and enclosure forms, are not axes and remain open, which is the
honest state: the class that could be closed was closed, and the classes that cannot be are
still one observation at a time.

#### Sixth widening (2026-08-24, `port-loop` round 4) — two of four tokens are rejected, and the criterion that rejects them is now written down

Four tokens were proposed and **two were admitted**: `responsable` into the Spanish layer,
`infant` into the English mechanism vocabulary. `viena` and `espana` were **refused**. This is
the first widening that refuses anything, and the refusal is the substance of the entry — the
two admissions are ordinary category-filling of the kind the four preceding entries describe.

**The criterion, stated generally for the first time.** A `rule_id` names **what the rule works
by**, not what it contains. Content lives in the `terms` list and inside `pattern`; it does not
live in the name. `spanish_city_gaz` is a mechanism name and `madrid_gaz` is a content name, and
the two rules can hold the identical gazetteer — what differs is whether the name states the
list's *organising principle* or one of its *members*.

**Why the criterion the check already had is not enough.** That criterion is "can the assembled
name designate an individual", and under it a place name passes: neither `madrid` nor `espana`
designates a person. Admit them and the vocabulary drifts towards "everything that is not a
person's name", which dissolves the boundary the check actually defends — a surname entering the
repository through a rule name. The category words are already present (`city`, `town`,
`province`, `region`, `country`, and in the Spanish layer `ciudad`, `pais`, `provincia`,
`localidad`, `municipio`); a name that needs a *member* of one of those categories is evidence
that the name failed to describe the mechanism, so the repair is to the name and not to the
vocabulary. The two criteria are ordered: content name → refuse; mechanism name → then ask
whether it can designate an individual.

Both refused tokens illustrate that the repair costs nothing. `date_viena_pattern` matches a
city name followed by a four-digit year, so its mechanism name is `city_year_pattern` and every
token of that is already admitted. `pais_espana`'s cue is the label `País:` — the rule **never
matches `España` at all**, so the content name is not merely prohibited, it is inaccurate about
the rule it names. Its mechanism name is `pais_cue`, and `pais` has been admitted since the
fourth widening. Neither refusal asks the model for a vocabulary it does not have.

**Which of the five preceding widenings this reverses: none.** The audit was run token by token
against the new axis and no admitted entry is a content name. Four findings are worth recording
because three of them are the distinction already being applied locally, under other names:

- **The distinction is not new; its scope was.** The Spanish layer's header has excluded place
  names since the first widening, and argues it in exactly this form — `calle` is a *kind* of
  street and not a street's name, `centro`/`salud` are kinds of institution and not one
  institution. The fourth widening wrote `institute` 는 기관의 종류이고 `Instituto Cajal` 은
  기관의 이름이다. `RULE_ID_ALLOWED_TOKENS` closes with "an abbreviation names the scheme, and
  the number it abbreviates never appears in a rule name". Three statements of the same rule, each
  scoped to the category that provoked it. This entry promotes them to the general criterion and
  changes no admission.
- **Three admissions keep their place but lose their recorded reason.** The language names
  (`spanish`, `catalan`, `german`, `korean`, `english`, first widening) and `iso` (third) were
  each argued on "개인을 지목할 수 없다" alone — the criterion this entry demotes to second.
  Both survive re-argument on mechanism grounds: a language name states which lexicon the pattern
  is written against, and `iso` states which ordering-and-delimiter convention it follows. Both
  are properties of the mechanism, and neither is a member of the class it names — `spanish` is
  not a Spanish month, `iso` is not a date. `roman`, in the original set and so not from any
  widening, is the same case. **Re-argument, not reversal**: nothing moves, and what is wrong is
  a comment rather than a set.
- **`nhs` and `nuhsa` are where the distinction will be misapplied.** They derive from an
  institution and a region respectively, so a reader holding only the new axis will reach to pull
  them. They stay: the token names an identifier *scheme*, a scheme is a validation procedure, and
  a validation procedure is the mechanism. The set already says so.
- **One structural collision, currently latent.**
  `test_every_phi_type_and_layer_token_is_in_the_vocabulary` admits tokens by axis membership,
  which is a *different* warrant from the mechanism/content judgement. Every current `phi_type`
  and `layer` value is a category name, so the two agree today; a future axis value that is not
  would be forced into the vocabulary by the test, over this criterion. Recorded rather than
  fixed — the fifth widening's argument for closing that category is that axis values are
  categories *by definition*, and if that ever stops holding the axis is the thing to fix.

Per instruction nothing on that list was changed, here or in the screener.

**The screener stays red on this arm, and that is an observation rather than a defect.** After
the widening, `iter4/es.yaml` still reports two `rule_id` findings and will keep reporting them
for as long as the file exists. Nothing is broken: the model named two rules by their content,
the check caught both, and the check is correct to refuse. `BLOCKED` is 0 and `SUSPECT` does not
stop a commit (CLAUDE.md), so the red is visible without being load-bearing. What it records is
the first case of the vocabulary being used as designed — four proposals, two categories filled,
two refusals — instead of as a list that grows to whatever arrived.

**And it cannot be fixed in this arm.** The place to teach the model this distinction is
Prohibition 2 in `docs/prompts/rule_author.md`, which is a frozen window file for
`R / sup-free / port-loop` (§6.3) — editing it changes the bytes of every subsequent call and
makes rounds 5+ a different arm from rounds 1–4. That is the same constraint recorded above for
why the vocabulary must not be bound into the prompt, arriving from the other direction. So the
criterion goes into the screener and into this section, `window_drift` stays clean, and the arm
finishes with a prompt that does not contain the rule its own output motivated. The prompt edit
belongs to the next arm, and until it is made, this refusal is enforced after the fact rather
than requested in advance.

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

### 6.3 A window is frozen at the moment it is first used, and every arm freezes its own

This is the general convention. §11.2 states the mechanism — content hashes of
`docs/prompts/rule_author.md` and `config/sampling.yaml`, on every log line and once more in
an immutable record, guarded by `arm_has_started()` — and states it for `port-human`, which
is the arm it was built for and the arm that is now retired. Two things generalise out of it,
and they are recorded here because `port-oneshot` is the baseline and inherits the discipline
without inheriting the arm.

**1. Freeze last: the freeze goes where the work starts, not where the planning ends.**
`docs/notes/window-freeze-history.md` is the evidence and the reason this is a rule rather
than a preference. That record was written **six times and used zero times**. All six were
before iteration 1, all six were permitted by the guard and correctly so — no minute of
attention had been spent under any of those windows — and all six were wasted, because five
were prompt edits taken while the prompt was still being written and the sixth was the arm
being cancelled. The generalisation the note draws is the one that belongs in a design
document: a freeze taken before the surrounding work is settled will be retaken, and its
claim — *this is the window the run began with* — is a claim about nothing until there is a
run. So the freeze is written at the first invocation that consumes the window, not when the
window is thought to be final. What made this cheap to learn is that the guard binds on
*use* (a recorded minute) rather than on the record's existence; the same property is what
makes it cheap to follow.

**2. A freeze record belongs to an arm, and `port-oneshot` does not inherit
`port-human`'s.** The existing record at
`results/es-meddocan/R/sup-free/port-human/window_freeze.json` hashes revision 6 of the
prompt and is the property of a retired arm. `port-oneshot` freezes anew, under its own
`{porting}` value, at its first call.

The tempting alternative is to treat the hashes as global — the prompt and the sampling
config are one pair of files, so one record could stand for every arm reading them. It fails
on what the record is *for*. The freeze answers "what was the window **this arm** committed
to at its start", and arms start at different times: `port-oneshot` will run against a prompt
that has moved since revision 6 (§§7–8 gained dormancy banners on retirement alone), and a
shared record would either be that arm's window or `port-human`'s, never both. Worse, a
shared record is rewritten by whichever arm starts second, which reintroduces exactly the
defect `arm_has_started()` closed: an arm whose window record was silently replaced by a
later arm's, with nothing on disk showing it happened. Per-arm records disagree with each
other instead, and disagreement is readable.

Two consequences follow and are stated so they are not discovered later:

- **A templated freeze path is required, and it is a second key rather than a widened one.**
  `paths.humanfreeze` is deliberately fixed to `port-human` (§11.2: this file exists for
  exactly one value of that axis, and a template would invite a second arm to write it). That
  reasoning was right for the file it names and does not extend to a convention every arm
  follows, so the agent arms get a *separate*, `{porting}`-templated key:
  **`paths.armfreeze`, declared in `config/naming.yaml` on 2026-08-07**, alongside
  `paths.humanfreeze` and not in place of it. Widening the existing key would have let an
  agent arm write to `port-human`'s path, where a retired arm's record would be silently
  overwritten by a later-starting arm — `arm_has_started()`'s defect coming back at the path
  layer, one level below where the guard can see it. Two keys, and the retired arm's record is
  unreachable from any arm but its own.
- **`port-oneshot`'s freeze record carries the window claim alone**, and this is the one
  place that is acceptable. The arm has no iteration log to put per-line hashes on —
  `agent_calls.jsonl` holds calls, and for this rung one call *is* the arm — so the
  redundancy §11.2 relies on is absent: there are no per-line hashes to disagree with each
  other, and disagreeing hashes are the whole mid-run drift detector. Nothing needs doing,
  for a reason specific to this rung and not transferable: **a one-call arm cannot drift
  mid-run**, because there is no "mid". The window that was frozen is the window the single
  call ran under, and one record is a complete account of it.

  **In `port-loop` the same absence is a real gap, and it must not be copied from here.**
  An iterating arm runs *n* calls across a span of wall-clock time in which
  `docs/prompts/rule_author.md` and `config/sampling.yaml` are ordinary editable files. A
  freeze record alone would then attest only to what was true at call 1, and an edit landing
  between call 3 and call 4 — which is exactly what happened six times before iteration 1
  (see 1 above) — would leave nothing on disk that contradicts it. The record would still
  read as a valid window claim for the whole run, and be false for most of it. So every
  iterating arm writes the pair of hashes on **every** log line, as §11.2 specifies, and the
  freeze record is the anchor those lines are checked against rather than a substitute for
  them. `port-oneshot` gets to skip the per-line hashes because *n*=1 makes them literally
  the same line; nothing else does.

**The writer is the orchestrator's, and the refusal is conditioned on the call log.**
`src/porting/human_arm.freeze_window()` cannot serve: it is pinned to `paths.humanfreeze` and
writes `"porting": "port-human"` as a literal, and widening it is what the two-key split
above refuses. So `src/orchestrate.py` has its own `freeze_window()`, and the condition it
refuses on is the one this section's history makes non-negotiable — **not `path.exists()`**.
A guard conditioned on the presence of the thing it protects is a request addressed to
whoever can delete the evidence, and `docs/notes/window-freeze-history.md` records that being
stepped around three times with `rm` before iteration 1. `port-human`'s binding condition is
a non-null `human_minutes` on an append-only log; the agent arm has no minutes, so the
equivalent is **whether this arm has already made its call** — a line in
`agent_calls.jsonl` (`paths.agentlog`). Before the call the record is a proposal and
re-freezing is free; after it the window is what the call ran under and cannot be rewritten.

**And the record says its §1.4 block was empty.** §4 defines `port-oneshot` as `port-loop`
truncated after call 1, so this arm hashes `config/sampling.yaml` while using none of the
parameters in it — *n*, `min_per_type` and `context_chars` all describe a block the prompt
did not carry. A record that hashed the file silently would attest to a window nobody could
reconstruct from it: a reader would find `sampling_sha256` and reasonably conclude 40 spans
at ±120 characters were shown. So the record carries the fact as a field. The hash stays,
because §1.4's parameters are still part of what the *template* specifies and a later arm's
record has to be comparable to this one; what is added is the statement that this arm read
that section empty.

#### `auditor.md` is not edited to raise the Auditor's format-compliance rate — decided 2026-08-23

`port-loop` round 2 on `es-meddocan` made 250 Auditor calls and **206 of them returned a
response the validator refused as `malformed` before any flag in it could be read** — 82.4%.
The remaining 44 documents emitted 316 flag items, of which 167 survived and 149 were refused
individually. The prompt is explicit about the envelope (§2.1: one JSON object, no fence, no
preamble, no extra field) and `src/porting/audit.py` accepts exactly what §2.1 specifies, so
the two do not disagree: the failure is the model departing from an instruction it was given,
at a rate of four calls in five.

**That number is a result of this arm and is reported as one.** The obvious repair — restate
§2.1 more forcefully, add a worked negative example, move the envelope rule to the end of the
prompt where recency helps — would very likely work, and taking it now is the exact shape of
post-hoc adjustment this section exists to forbid. The sequence would be: run the arm, read
the arm's score, find the arm's weakest component, change the arm's inputs, re-run, report the
better number. Every step is defensible in isolation and the composite is a result tuned on
the thing it is reporting. §6.3's freeze mechanism already refuses it mechanically — the
window is hashed on all 252 `agent_calls.jsonl` lines and `arm_has_started()` refuses a
re-freeze — but the mechanism only stops a *silent* edit. What stops a declared one is the
argument, and the argument is here so it does not have to be reconstructed under the pressure
of a bad number.

**The pressure is specifically that the result was good.** Round 2's leak rate improved 0.289,
which is 8× the measured call-to-call spread (§3). A failing round invites debugging and gets
scrutiny; a round that improved a lot *while wasting 82% of its calls* invites the thought
that the same design would do even better with one small prompt fix, and that thought is the
dangerous one, because the improvement makes the edit feel like unlocking the design rather
than tuning the report. Both readings are available and only one is testable, so the untested
one does not get to change the inputs.

**What a prompt revision is, if it is wanted, is a new arm.** §4's `port-oneshot-nofence`
already set this precedent for exactly this class of change: a revised prompt got a new
`{porting}` value rather than a re-run of the old one, because a run under different prompt
bytes is not the same experiment and averaging it with the old one would be comparing two
things under one name. So a format-hardened Auditor is a new `{porting}` value, run from round
1 on its own rules and scored against this arm rather than replacing it — and the comparison it
then supports is worth more than the repaired number would have been, since it measures what
envelope compliance is worth in leak rate instead of assuming it. The value has to be added to
`config/naming.yaml` before it exists.

**What may change without touching the prompt, and what that is not.** `malformed` is one
reason covering three distinct call-level branches in `validate_flags()`/`parse_response`
(the response is not JSON; it is JSON but not a one-key `flags` object; `flags` is not a
list), and the report records none of the three. So round 2's record cannot say *how* the
envelope broke — the measurements available rule out truncation (max completion 735 tokens
against a 32,768 limit), bulk prose (characters-per-completion-token is 2.085 on the parsed
responses and 2.089 on the refused ones, a 0.2% difference), harder documents (identical
median `n_input_spans`, `n_lines` and `n_tags` in both groups) and any drift over the run
(the 44 parsed calls are scattered across all ten 25-call buckets), and they cannot
distinguish the three branches. Splitting the reason is a *recording* change in the harness,
not a change to what the agent is shown, and it is therefore not the edit this section
forbids. It is also not free: it changes `config/naming.yaml`'s `audit_refusal` vocabulary
mid-arm, so round 3 would run under different harness code than round 2, which the `commit`
field records but the freeze does not cover. That trade is a separate decision and is not
taken here.

**A reporting hazard this uncovered, recorded because the field name invites it.**
`audit_report.json`'s `documents_with_no_flags` was 206 in round 2 — and it is the *same 206
documents* as the malformed set, exactly. The field reads as a fact about the corpus ("206
documents had no residual PHI") and in this round it is a fact about the envelope ("206
responses could not be parsed"). §2.1's own distinction — `{"flags": []}` means audited and
clean, an absent entry means not audited — held for zero documents this round: not one call
returned a well-formed empty list. Any report that cites `documents_with_no_flags` without the
malformed count beside it is stating the opposite of what happened.

### 6.4 Which arm and which round open the seal — pre-registered 2026-08-26, before any sealed value exists

§6.1 makes a sealed read recordable and §6.2 makes the fold verifiable. Neither says *which* read
is supposed to happen, and until this section that was the one decision about the seal nobody had
written down. It has to be written before the first row, because after the first row every version
of it can be read as the version that fits the number.

**The rule, in three parts.**

1. **One opening per reported arm.** An arm that appears in the paper opens the test fold exactly
   once. An arm that does not appear in the paper does not open it at all. The ladder (§4) fixes
   which arms those are before any of them runs, so the set is not chosen after the fact.
2. **After the arm has terminated by its own pre-registered rule.** §3's stopping rule must already
   have fired — `converged`, `ceiling`, or one of the other endings §3 enumerates — and its verdict
   must already be committed. An arm still running has no final round to evaluate.
3. **The round scored is the arm's final round.** Not its best round, and not a round chosen on any
   other ground. For a non-iterating arm this is its only round and the rule is vacuous, which is
   the point: every rung is scored under one rule rather than under the rule its shape allows.

**Why the final round, in one sentence: dev-best makes the headline a maximum over eight chances
while the cost column still bills eight rounds, and two numbers that describe different experiments
cannot be reported as one result.** `cost_to_date` is the arm's total through its last round,
because that is what the arm spent. A headline taken from round 5 of 8 is a quality figure produced
by five rounds sitting beside a cost figure produced by eight. Neither number is wrong on its own
and the pair is a fiction — no run exists that both cost that and scored that. Scoring the last
round is what makes the two columns describe the same run.

**This is §5.5's rule applied at the seal, not a second rule.** §5.5 already states that an arm's
published result is its final round and never its best, on two grounds: the best round is
identifiable only from the leak rates, i.e. only with the results in view; and `port-oneshot` has
one round and cannot select, so a selecting `port-loop` would beat a non-selecting baseline partly
by the selection. Both carry over unchanged, and the second gets sharper here — on test the
comparison between rungs *is* the claim, so a selection only one rung can make would sit inside the
headline result.

**Written after `port-loop` ended, and that is recorded rather than smoothed over.** This section is
dated 2026-08-26 and `port-loop` on es-meddocan terminated on 2026-08-25 at round 8, so the protocol
was written by someone who had seen an arm's entire dev trajectory. Claiming otherwise would be
false. The claim that matters is narrower and it is checkable: **no sealed value existed when this
was written.** `results/sealed_eval_log.md` held no run rows, so `count_runs()` was 0 — and that is
not a recollection, it is a committed file whose rows are only ever appended. Selection on dev is
permitted by CLAUDE.md and always was; selection on test is what this section forecloses, and it was
written at the only moment when the count was still zero.

Two further facts about the timing, both cutting against the convenient reading:

- The rule was adopted at a moment when it **does not bite**. Round 8 was also `port-loop`'s best
  round (§3, 2026-08-25), so final-round and best-round name the same file today. A rule chosen to
  flatter the arm would have been the other one — and the other one would have been
  indistinguishable from this one on the only arm that has finished.
- The rule is **older than this section**. §5.5 stated it for dev on 2026-08-24, before round 5 ran
  and before the trajectory's shape was known. This section extends a rule; it does not pick one.

**An arm that failed does not open the seal, and cannot be repaired into one that does.** The
`port-oneshot` rungs can die on format failure (§4, `paths.formatfailure`), and an iterating arm can
end in one of §3's non-convergent endings.

- **No final round, no opening.** An arm whose failure left it without a complete final-round record
  — the arm-level `metrics.json` and `spans.jsonl` of §5.5 — has nothing that could be scored, and
  test cannot say anything about it dev has not already said. A format failure is legible on dev;
  opening test would not make it more legible, and would spend an opening on a diagnosis.
- **The failure is the result and is reported as one**, with its cost. An arm that spent N calls and
  produced no scorable round is a finding about that rung, not a gap in the table (§4.1's form).
- **A repair is a new arm, not a retry of this one.** §4's `port-oneshot-nofence` precedent: a
  revised prompt gets a new `porting` value. The new arm gets its own single opening under this
  section, and the failed arm's dev record is not retracted. No failure yields two attempts at one
  cell of the ladder.
- **A failed attempt inside a surviving arm changes nothing.** Round 5's two abandoned attempts (§3,
  2026-08-24) are recorded in `abandoned_spend` and the arm still terminated by its own rule, so it
  opens the seal normally. What disqualifies an arm is the absence of a final round, never the
  presence of a failed attempt.

**What the row has to carry, and the two accounting rules that follow.** The row records the arm and
the round that was scored, not only the corpus: an opening whose row cannot be checked against this
section is an opening this section does not govern. So `arm(s)` and `round` are separate columns
(`src/eval/sealed_log.py`), the round is verified against the arm's own committed record *before*
the read, and a round that is not the arm's final round is refused rather than logged.

- **A row stands even if the run that wrote it crashed.** The append precedes the read (§6.1), so a
  crash mid-evaluation leaves a row and no numbers. That row is neither deleted nor amended: the
  fold was opened, and what the count bounds is how much the fold could have informed a decision —
  not how many scores came out.
- **A re-run is therefore a second opening and is logged as one**, including a re-run after a crash
  and a re-run whose only difference is a bug fix. If the paper has to say the fold was opened twice
  for one arm, that is the true sentence, and this section grants no exemption from it.

### 6.5 Two corpora that share a source release — **decided 2026-08-27: D + A; dormant from 2026-08-27**

> **Correction, 2026-08-27, same day as the decision.** This section was written on the
> premise that `ko-surro` is *not yet acquired*, and that premise is false: the corpus
> exists, it was built by the separate surrogate project, and its records already carry
> the source record identifier. **Link 1 is established, not open** — so option A is
> already paid rather than "free only now", and the sentences that said otherwise were
> wrong on a matter of fact and have been fixed below rather than annotated. A
> pre-registration may keep refuted alternatives; it may not keep false statements. The
> decision itself is unaffected: D still needs only link 2, and A being already satisfied
> strengthens D's audit trail rather than changing it. Separately, the MIMIC-III
> application is **deferred** (`docs/notes/mimic-iii-acquisition.md` §0), so this
> section's policy is decided and **dormant** — it binds the moment a MIMIC-III arm
> exists and needs no re-deciding then.

Everything above defines the seal **per corpus**: `splits/{corpus}.json`, one `sealed/`
tree per corpus, a log keyed on the corpus cell. Nothing in §6.1–6.4 or in CLAUDE.md
says what happens when two corpora in the same paper contain the same underlying
documents. That case is now live: `ko-surro` is a Korean surrogate corpus derived from
the PhysioNet de-identified nursing-note release, and MIMIC-III is where those notes
came from. A document sealed in one corpus can sit in the other's dev fold, and the
per-corpus seal will report itself intact while it does.

**The decision is D + A, taken 2026-08-27, before the MIMIC-III application and before
any split file for it exists.** The six options and the grounds each stands on are kept
below, unedited, because a decision whose alternatives have been deleted cannot be
audited. The application's own preparation — requirements, the δ arithmetic for the
nursing subset, and the DATE question — is in `docs/notes/mimic-iii-acquisition.md`, and
the three things that must freeze together before any sampling are in §6.6.

**The correspondence question is two links, not one, and they fail independently.**

| Link | What it asserts | Status |
|---|---|---|
| 1. `ko-surro` → source release | which of the 2,434 notes each `ko-surro` document came from | **established.** Every `ko-surro` document carries `{patient identifier}_{note index within patient}`, read from the source release's own record header by the producing project's record parser. Option A is therefore already satisfied, not a requirement to impose |
| 2. source release → MIMIC-III | which MIMIC-III note each of the 2,434 is | the release's own patient and record numbering may not be MIMIC's; the source page is internally inconsistent about MIMIC-II vs MIMIC-III parentage |

Exclusion or alignment by document needs **both**. Link 1 was written here as the one
still open to influence, on the premise that a requirement could be imposed on an
acquisition that had not happened yet; the correction above records that it had. **Link 1
is closed and closed favourably.** Link 2 is a fact about two released datasets and can
only be established, not arranged — and probably only with both in hand. So the whole
question now rests on link 2 alone, and every option that needed only link 1 has no
remaining obstacle.

**What is actually at risk is narrower than "the same text in two folds," and how narrow
is itself one of the disputed grounds.** `ko-surro` is a surrogate corpus: PHI surface
forms are replaced and the text is in another language, so byte offsets do not
correspond and the sealed annotation is not recoverable from the source document. One
reading is that a seal protects the annotation, and this seal therefore does not leak.
The other is that what the porting loop consumes is not annotations but *where PHI
occurs and in what context*, and that survives both substitution and translation intact
— which is exactly the signal a rule-development loop is fitted on. These two readings
disagree about whether there is a problem at all, and the options below are not
comparable until that is settled.

**The options, with the ground each stands on.**

| Option | Ground for | Ground against |
|---|---|---|
| **A. Exclude by document** — corresponding documents are dropped from the second corpus | exact, and the only option that leaves both folds clean under either reading above | needs links 1 and 2; shrinks whichever corpus yields, and `ko-surro` is the one that cannot afford it (§7.1 route (b): δ already fails there by 2×–9×) |
| **B. Align rather than exclude** — corresponding documents are forced into the *same* fold role in both corpora | preserves the seal without discarding data, and keeps the paired English/Korean contrast that §7.1 calls the sharpest test of §7 | needs links 1 and 2, and needs whichever split is defined second to be *derived* from the first — so it expires the moment either split file is committed |
| **C. Structural exclusion** — the MIMIC-III arm uses only non-nursing note categories | needs neither link; checkable from a category field alone | removes the note type the pair is *for*. §7.1's nursing-note prediction is unrunnable under this option, which makes it the most expensive option scientifically and the cheapest procedurally |
| **D. Exclude the superset** — drop all 2,434 source notes from the MIMIC-III arm | needs only link 2; costs almost nothing against MIMIC-III's size | still needs link 2, which may be the harder of the two; and 2,434 nursing notes may be a non-trivial fraction of the nursing subset specifically, which is not yet measured |
| **E. Report as a limitation** — run both, state the overlap and its bound | honest and available with no links at all; rests on the "seal protects the annotation" reading | a limitation whose *size* is unknown is not a bounded limitation. Without link 2 the overlap cannot even be bounded above except by 2,434 |
| **F. Drop one corpus from the paper** | forecloses the question entirely | gives up either the high-baseline cell or the only axis-1 zero, and §7.1 says both are load-bearing |

**Two things that are not options.** Content matching against `sealed/` to discover the
overlap is not available: it would require reading the sealed text, which CLAUDE.md
forbids outright, and cross-lingual matching between a Korean surrogate and an English
source would not work in any case. And deciding this after the fact is not available
either — B is the option that a committed split file destroys, so a decision deferred
past the first split is a decision against B without saying so.

**What settles it.** Link 2 is answerable from the two datasets' identifier fields once
both are held, and it is the pivot: A, B and D all become available if it holds and all
fail if it does not, leaving C, E and F. Whether the surrogate transformation breaks the
leak is not an empirical question about the corpora but a question about what the seal is
for, and §6.1 is where that answer has to be consistent.

#### The decision: D as the policy, A as its premise, C as the fallback

**D is the operative policy: all 2,434 source-release notes are excluded from the
MIMIC-III arm, as a set, without asking which of them are sealed in `ko-surro`.** Three
grounds, in the order they decided it.

1. **D does not expire.** B is the only option that a committed split file destroys, and
   an option that can be lost by inaction is an option that quietly converts a delay into
   a decision. D is available at any point before the MIMIC-III fold is sampled and stays
   available after `ko-surro`'s split is frozen, in either order. Nothing about D has to be
   done first to remain possible.
2. **D keeps both corpora.** F gives up a cell §7.1 says is load-bearing, and A applied to
   `ko-surro` would shrink the corpus that cannot afford to shrink (δ already fails there
   by 2×–9×). D takes its 2,434 out of the corpus with 2,083,180 notes, which is the only
   direction in which the cost is negligible.
3. **D leaves no post-hoc degrees of freedom.** The excluded set is defined by membership
   in a released dataset, fixed before anything is measured, and checkable by anyone
   holding both. There is no threshold to pick, no sample to draw, and no number that could
   later be adjusted in the light of a result. E is the opposite: its size is a free
   parameter that stays free, because a limitation whose magnitude is unknown can be
   restated at will.

**B is refused on a stronger ground than expiry: computing the intersection reads the
seal.** To align corresponding documents into the same fold role, one must know, for each
document, which fold its counterpart occupies — and for the sealed half that is a query
against sealed membership whose answer then determines the other corpus's fold assignment.
The sealed fold would be shaping a design decision in the other corpus, which is what
§6.1 exists to prevent, and it would do so while every per-corpus check reported the seal
intact. D never asks the question: it excludes a **superset** that contains the sealed
documents along with all the others, so no sealed-membership fact is consulted, and the
exclusion is verifiable without opening anything. That B is also the option that a
committed split destroys is the lesser objection and not the reason it is refused.

**A is adopted as D's premise, not as an alternative to it — and it turns out to be
already satisfied.** This paragraph first argued that A was "free only now": that
`ko-surro`'s acquisition could be required to record which source note each document came
from, and that the record would be unrecoverable once the corpus existed without it. The
premise was wrong in the reader's favour. The corpus already exists and every document
already carries its source record identifier, so nothing has to be imposed and nothing
was at risk of expiring. What A buys is unchanged: under D the record is not needed for
the exclusion — D is deliberately blind to it — so A makes the exclusion *auditable at
document level* rather than only at set level, and keeps the tighter document-level
exclusion available if the superset ever turns out to be too coarse. A is not doing work
that D is failing to do. It is simply not owed.

**If link 2 fails, retreat to C, and record that it was a retreat.** D needs link 2 —
without it the 2,434 cannot be located in MIMIC-III at all. C needs no link and is
therefore always reachable, at the price §7.1 names: the nursing-note prediction becomes
unrunnable, which is the sharpest single test §7 offers. So C is the fallback and not the
default, and if it is taken the paper says that a structural exclusion replaced a
document-level one because the identifier correspondence could not be established — not
that non-nursing categories were the plan. Link 2 is checked as an early post-access
measurement, before the fold is sampled, so that the fallback is chosen before anything
depends on it.

### 6.6 The MIMIC-III arm's pre-registration — three things that freeze together, 2026-08-27

> **Status, corrected 2026-08-27: pre-registered and dormant.** The MIMIC-III application
> is deferred, so nothing here runs yet. That does not weaken the block — a
> pre-registration written before acquisition is exactly what this is for, and deferring
> the acquisition is the one event that cannot contaminate it. Two scope corrections were
> needed and are made in place: §2's marker criterion did not cover the case where a
> marker exists but carries no type, and §3's numbers are MIMIC-III sizing and were not
> marked as such. Both are fixed below.

Cross-corpus exclusion, DATE scope and dev subsample size are pre-registered as one block
rather than as three decisions, because they are not independent. **DATE scope changes
`n_spans_in_scope`, `n_spans_in_scope` is `n_dev`, and δ is derived from `n_dev`** (§3).
On `es-meddocan` DATE is 13.8% of the in-scope denominator, so a corpus sampled to
n_dev = 5,300 with DATE in scope holds about 4,570 without it — which moves δ off its
floor, from 0.50 pp to 0.57 pp. The exclusion policy sets which documents may be sampled
at all. Freeze any one of the three after the other two and the termination threshold has
been chosen with a result in view, which is the thing §6.4 forecloses at the seal and §3
forecloses at the stopping rule. This section forecloses it at the sampling.

#### 1. Cross-corpus exclusion — §6.5's D + A

All 2,434 source-release notes are excluded from the MIMIC-III arm as a set. `ko-surro`'s
acquisition records source document identifiers. C is the fallback if link 2 fails, and a
retreat to C is reported as a retreat. Grounds are in §6.5.

#### 2. DATE — the criterion is what the method can see, not how hard the case is

**Annotation-free supervision recovers labels from markers. A shifted date carries no
marker, so it is not recoverable in principle — it is not a hard instance, it is an
instance this method cannot see.** That is the whole ground for the rule below. Difficulty
would be a reason to try harder; invisibility to the supervision signal is a reason to
say what the reference set contains.

**The rule.** Bracketed, typed DATE masks are gold and are scored like any other type.
Shifted dates are outside the reference set, and their share of all dates in the corpus is
reported as a limitation. Two consequences follow and both are stated now:

- **This is not a span exclusion and does not use §9.1's mechanism.** §9.1 flags excluded
  spans and keeps them, so `n_spans_excluded` can report the amount. A shifted date has no
  gold span to flag — there is nothing in the corpus marking its position — so the honest
  accounting is that the DATE reference is *incomplete by an unknown share*, not that some
  spans were excluded. Nothing is added to `naming.yaml`'s `excluded_types`, DATE stays a
  `phi_type` value for every corpus, and cross-corpus scoring stays over the same ten
  types. An earlier draft of `docs/notes/mimic-iii-acquisition.md` had this the other way
  round and was wrong.
- **DATE precision is a lower bound for this corpus, and recall is not comparable across
  corpora.** A detector that correctly finds a shifted date scores as a false positive,
  because no gold span exists there. That cannot be repaired by any definition of gold —
  repairing it would require identifying the shifted dates, which is the circular route
  refused below. So this corpus's DATE precision is reported as a lower bound, and its
  DATE recall is reported over a reference known to be incomplete.

**The fingerprint may measure the limitation and may not produce gold.** MIMIC's date
shift is documented to land years in 2100–2200, and the v1.4 release notes state that the
shift for dates in the noteevents text field was corrected to match the structured data —
so shifted dates in note text are shifted dates, and where they carry a year that year is
out of range. This makes the unbracketed share measurable. It does **not** make it
scorable: a reference produced by a pattern rule is the same kind of object as the
predictions being scored, so rules would be evaluated against rule-generated gold. The
split is pre-registered — the fingerprint is permitted for the limitation figure, barred
from the reference set — and it holds regardless of how well the fingerprint turns out to
work.

**The proportion is not in the documentation and is knowable only after access.** The
PhysioNet project page, `mimic.mit.edu`'s time-types page and its release notes were read
on 2026-08-27: all three describe the shift, none gives any count or proportion of dates
shifted versus bracketed. That is recorded here because the rule above must not be
selected by the number. The rule is fixed now; the number is measured later and reported
whatever it is.

**One measured figure, cited as evidence and not as ground.** On `es-meddocan` dev,
removing DATE moves the aggregate leak rate 13.72% → 15.28%, i.e. 1.56 pp, about 3× δ.
This is why the decision cannot be a footnote. It is not why the decision went this way —
the marker is.

**The criterion has three cases, not two, and this section originally wrote only two.**
The rule above splits markers into *present and typed* (gold) and *absent* (out of
reference). There is a third case and `ko-surro`'s source release is full of it: **the
marker is present and carries a value instead of a type.** 659 of that release's 2,164
masks — 30.5% — bracket a position but name no type, and 658 of the 659 hold nothing but
digits and separators, 83.5% of them in a month-day shape. Splitting the criterion by what
it actually answers:

| Question | What a marker of this third kind answers |
|---|---|
| *Where is the span?* | **answered.** The bracket delimits it; the position is recoverable exactly as for a typed marker |
| *What type is it?* | **not answered.** The payload is a value, and inferring the type from that value is a pattern rule — the same kind of object as the predictions, which is what this section bars for gold |

So the marker criterion resolves position and is **silent on type**, and silence is not a
decision. The consequence for reporting is immediate: leak rate, which needs only
positions, is unaffected by this case, while the per-type decomposition that §7's layer
prediction reads is not defined over these spans until the type question is settled
separately. That settlement is not made here — it is a `ko-surro` scope question, it has
its own option set, and folding it into a MIMIC-III pre-registration would decide it by
adjacency. What is fixed here is only that the two questions are separated and that the
type question is open. It is tracked in `docs/notes/ko-surro-untyped-spans.md`.

#### 3. Dev subsample — a rule, not a number

**Scope: this subsection is MIMIC-III sizing and applies to no other corpus.** It was
written without that marking, which invited it to be read as a general rule; it is not one,
and the reason is that its whole shape comes from one assumption — that the corpus is far
larger than the fold needs, so `n_dev` is a free parameter to be tied down. Where that
assumption fails the procedure below produces nothing useful. `ko-surro` is the case in
point and it fails in the opposite direction: 2,158 gold spans in total, so a 25% dev
fraction gives n_dev ≈ 540 and δ ≈ 4.8 pp — roughly ten times `es-meddocan`'s floor, a
threshold so coarse that a per-round gain would have to be enormous to clear it. That is a
real problem for any iterating arm on `ko-surro` and it is **not** solved by this
subsection; it needs its own treatment, and pre-registering the MIMIC-III rule does not
pre-register an answer for it.

The subset is expected to be large enough that `n_dev` becomes a free parameter, and δ is
derived from `n_dev`, so a free `n_dev` is a free stopping rule (`docs/notes/mimic-iii-acquisition.md` §2).
What is pre-registered is therefore the sampling procedure, which yields a number without
anyone choosing one:

| Parameter | Value |
|---|---|
| Grouping unit | patient-disjoint, on the corpus's own patient key (§9.5 applies only where no key exists) |
| Fractions | 50 / 25 / 25 train / dev / test by patient group, as `es-meddocan` |
| Dev size | whole patient groups added in seeded random order until `n_spans_in_scope` first reaches **≥ 5,300**, then stop |
| Test size | the same target and the same procedure, sampled and sealed before any read |
| δ | computed as `max(0.005, 26 / n_dev)` from the resulting `n_dev` and recorded in `splits/{corpus}.json` |
| Seed | fixed in config and recorded in the results file |

The 5,300 target puts δ on its floor at 0.50 pp, matching `es-meddocan`, so the two
corpora terminate against the same threshold and a difference in stopping behaviour cannot
come from a difference in fold size. The document count is whatever the rule produces and
is reported, not chosen.

#### What freezes when, and what stops being decidable after it

Rows marked **conditional** are frozen as written but cannot be executed while the
MIMIC-III application is deferred. Deferral does not unfreeze them; it only postpones the
step that would produce their numbers, which is the harmless direction.

| Item | Frozen at | Not decidable after that point |
|---|---|---|
| §6.5's exclusion policy | now, 2026-08-27, before the application | whether the 2,434 are excluded, and whether B was available — a committed split file removes B |
| `ko-surro` source-ID record | **already frozen — the record exists** (corrected 2026-08-27) | nothing. Link 1 is closed; this row previously read "at `ko-surro`'s acquisition, whenever that happens", which was false |
| DATE scope rule | now, before the proportion is known | which of the two reporting forms applies — the proportion cannot select it |
| DATE, third case (marker present, type absent) | **not frozen here — deliberately open** | nothing yet. Only the separation of the position and type questions is fixed; the type answer is a `ko-surro` decision and is not pre-registered by this block |
| Dev sampling procedure (MIMIC-III only) | now, before the corpus is in hand | `n_dev`, and therefore δ, and therefore the termination threshold — for MIMIC-III. `ko-surro`'s δ problem is untouched by this row |
| Link 2 check — **conditional** | early post-access, before the fold is sampled | whether the fallback to C was a fallback or a plan |
| `n_dev`, δ, document counts — **conditional** | at sampling | nothing — these are outputs, reported as measured |

The order matters in one direction only: everything in the first column above the sampling
row must be committed before the sampling row runs. After sampling, the remaining numbers
are measurements and no decision is left to make.

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

#### The instrument's first load-bearing use, and what it showed the layer table assumes — 2026-08-24, `port-loop` round 4

The per-layer cut above had never carried a result. Round 4 is the first time it did, and it is
recorded here rather than in §3 because what it revealed is a gap in *this* section's table.

**Attribution works, and it works through the `layer` field rather than through the type table.**
Checked directly rather than assumed: all 5986 predicted spans in `iter4/spans.jsonl` carry a
`layer` value, none is missing, and `modes.*.complementarity.layers.covered` aggregates them —
so the figures below are the provenance field of §3 grouped, not a per-type table read sideways.

| covered (`fully_covered`) | round 1 | round 2 | round 3 | round 4 |
|---|---|---|---|---|
| `context_cue` | 1167 | 1692 | 1879 | 1994 |
| `regex_checksum` | 1203 | 1949 | 2357 | **2324** |
| `gazetteer` | 0 | 0 | 19 | **317** |
| `tagger` | 0 | 0 | 0 | 0 |

**A layer silent for three rounds supplied the fourth round's result.** Within
`LOCATION_AREA` the gazetteer covered 0, 0, 0 and then **284**; the arm's leaked count fell
1201 → 1025 (−176) and `LOCATION_AREA` alone fell 521 → 364 (−157), so **89% of round 4's entire
improvement is one type, reached by a layer that had contributed nothing to it before.** Two
things must be kept apart here: the gazetteer *supplied* 284 covered spans, and the net new
coverage was 157 (`rules_only` 813 → 970). The merge is a union, so a layer's supply and its
contribution differ by exactly the overlap — and `regex_checksum` fell in the same round, 2357 →
2324 overall and 586 → 544 within `LOCATION_AREA`, which is that overlap made visible. Reporting
284 as the gain would double-count; reporting 157 as the supply would hide that a dormant layer
woke up.

**This is not a test of the prediction above, and calling it one would be the easy mistake.** The
prediction is about what happens when *realisation* falls — a note-type or language contrast.
Round 4 holds corpus, fold and language fixed and varies only the rule file. What it tests is the
instrument: that the four rows exist, are populated from provenance, and disagree with the
aggregate. They do — the rules/tagger dichotomy of §5 records round 4 as `rules_only` rising and
nothing else, which is true and says nothing about a gazetteer replacing regex coverage.

**What it does refute is a presupposition of the table.** The four rows are graded by sensitivity
to realisation, and `gazetteer` is graded **none**. That grading silently assumes the layer is
populated: a layer at 0 spans has no sensitivity to measure, and in this arm three of four rounds
had `gazetteer` ≈ 0 while `tagger` was 0 throughout. **Whether a layer is available at all is
decided by the rule author, not by the corpus** — which is a second axis the table does not have,
and it sits upstream of both of the hypothesis's factors. A note-type contrast run against an arm
whose author happened not to write a gazetteer would report "gazetteer recall approximately flat"
and be reading a flat line at zero. The prediction therefore needs a precondition attached: it is
testable only across arms whose rule files populate the layer being compared, and per-layer
availability has to be reported alongside the per-layer recall, not assumed from the fact that the
row exists. Two of the four rows in this arm currently fail that precondition.

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

#### The operative count is 1, not 2 — recorded 2026-08-27

"At most two cells" is an upper bound and stands as written. **Its operative value
today is 1.** The two are not the same number, and reading the bound as a count of
populated cells is a misreading of this section, not an error in it: the paragraphs
above already mark the English rows a projection and already write "(if n2c2 arrives)"
into the conclusion itself. Only the low-baseline cell is populated, by GraSCCo. The
high-baseline cell is contingent on a corpus we do not hold, and the medium cell is
impossible. So the sentence needed no repair; what it needed was the operative value
stated next to the bound, which is what this paragraph is for. The reason to write it
down rather than leave it inferable is that "checkable in at most two cells" reads as
reassurance and "checkable in one" reads as a limitation, and the second is the true
description of the corpus set as held.

**The prescription that follows is a second Spanish register, not an English corpus.**
This is already the conclusion's last sentence, and the operative-value correction does
not move it — it sharpens it. An English corpus takes the operative count from 1 to 2 by
populating an end; a second Spanish register at fixed language is the only acquisition
that touches the interior, which is the cell the product hypothesis is actually untested
in. The two acquisitions are not substitutes and the English one is not the cheaper
version of the Spanish one.

**If MIMIC-III is acquired, three things change and one does not.** (i) The operative
count goes 1 → 2: the high-baseline cell becomes populated by measurement rather than
projection. (ii) English gains *within-corpus* axis-2 variation — MIMIC-III's NOTEEVENTS
carries nursing, physician, discharge-summary, radiology and ECG notes under one
de-identification pass — which is the property that made GraSCCo the only instrument for
"a language is an interval." Whether n2c2 2014 would have supplied that was never
verified and is now moot for this question. (iii) The §7 prediction that English nursing
notes behave like Korean — high baseline, near-zero realisation — becomes directly
testable against `ko-surro` at fixed note type, which is the sharpest single prediction
this section makes. That third item is gated on §6.5: the two corpora share a source
release, and until the cross-corpus seal relation is decided the pair cannot be run.

> **Two corrections, 2026-08-27.** The last sentence of that paragraph used to add
> "`ko-surro` is also not yet acquired (`data/README.md`: held, DUA)". That misread its own
> citation — "held" means held — and the corpus does exist; §6.5's correction records the
> same error and its consequence. And items (i) and (ii) say "measurement rather than
> projection", which needs a qualifier that was missing: **MIMIC-III ships no unmasked
> version and no human reference, so anything measured on it is measured against the
> masking tool's own output.** Placeholder positions taken as gold are a silver standard,
> not a gold one. What survives is narrower and still worth something — a *within-corpus*
> contrast across note types shares one reference and therefore one bias, so the comparison
> can be read even where the absolute leak rate cannot. Items (i) and (ii) should be read
> as "populated by a silver measurement", and the §7.1 bound as a comparison-only claim at
> the high-baseline end. Item (iii) inherits the same qualifier and gains a symmetry:
> `ko-surro`'s own reference descends from the same tool, so the pair contrast is between
> two like-biased references, which is the most favourable form this comparison can take.
> `docs/notes/mimic-iii-acquisition.md` §0 holds the deferral and the one reason that
> survives it.

What does not change is the prescription. The medium cell stays impossible with or
without MIMIC-III, because MIMIC-III is English and adds nothing at Spanish's baseline
value. A second Spanish register remains the acquisition that closes §7.1; MIMIC-III
changes how urgent it is, not whether it is needed.

---

## 8. What is outside the system

The paper's system is `src/orchestrate.py` and the agents it calls. Everything
recorded in `agent_calls.jsonl` is inside.

Claude chat sessions and Bedrock Claude Code are research tools, not system
components. They are disclosed in the AI-use statement, not in Methods. Design
conversations that shape the role set are, however, the source material for
`port-selfdesign` prompts — worth keeping. They were also the record of the
`port-human` arm, which is retired (§11); with that arm gone they document how the
fixed role set of `port-multi` was arrived at, which `port-selfdesign` is measured
against.

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
produces; the table above is the human-authored version of it. It predates the
retirement of `port-human` (§11) and is unaffected by it: this mapping is a design
input shared by every arm, not one arm's output. Only `port-multi` and above have a
Mapper, so the arms below use this table — which is why it is in DESIGN rather than
in an arm's results directory.

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

#### `joint_only`: the fifth complementarity category

The four-way breakdown of §5 — rules only / tagger only / both / neither — does not
partition the gold spans once `fully_covered` is the coverage rule. The gap is the
mirror case above, seen from the complementarity side:

```
gold:  [Calle Mayor 3        ]   one LOCATION_STREET span
pred:  [Calle Mayor ]           gazetteer (rules) finds the street name
                    [3      ]   the tagger finds the number
```

The union covers every character, so the span is not a leak. But no *family* covers it
alone: neither the rules union nor the tagger union spans it by itself. (Two *layers*
of the same family splitting a span this way is not the case at issue — that is
`rules_only`, since the family's own union covers it. The category needs one family on
each side of the split.) Under the
four-way scheme this span has to be filed as `neither`, and that single misfiling
breaks an identity the output depends on — **`neither` must equal the leaked count**.
Once it does not, the complementarity breakdown and the leak rate contradict each other
inside the same `metrics.json`, with both numbers computed correctly and neither one
flagged. A reader comparing them has no way to tell which is wrong, and the natural
guess — that `neither` is the reliable one because it is a count of spans rather than a
ratio — is the wrong guess.

So the category is named rather than absorbed: `joint_only` is "covered, but by no
single family alone". The five categories then partition the denominator and `neither`
is exactly the leaked set, both of which the scorer asserts on every fixture.

**Under `relaxed` this category is always zero**, and that is a theorem rather than an
observation: if any prediction in the union overlaps the gold span, that prediction
belongs to some family, so that family's own union overlaps it too. `joint_only` is
therefore a `fully_covered` phenomenon exclusively, and a non-zero value in a `relaxed`
block would be a scorer bug rather than a finding.

The layer view carries the same case for the same reason, under the name
**`layers.covered_by_union_only`**. There the constraint being protected is that the
empty-set key of the subset distribution stays exactly the leaked set: `sets[""]` reads
as "no layer found this", and a jointly-hidden identifier put in that bucket would be
indistinguishable from a disclosed one — the same wrong-number-with-no-symptom shape as
an unfamilied layer falling into `neither` (§3). Counting it separately keeps the
empty-set key honest and keeps the layer subsets summing, with
`covered_by_union_only`, to the denominator.

#### Identical predictions are collapsed; differently bounded ones are not

Two layers that emit the byte-identical span found one thing, not two. The assignment
matching is one-to-one, so without a collapse the second copy is an unmatched
prediction and scores as a false positive — **precision would fall exactly where the
layers agree.** That is the same pathology this section rules out for complementarity,
reappearing in a different number, and it penalises the agreement that the
complementarity breakdown exists to measure. Predictions identical in
`(start, end, phi_type)` are therefore deduplicated before assignment.

The rule stops there, and the boundary is load-bearing in the other direction.
Merging predictions that *overlap* but disagree on boundaries is the merge policy's
decision, and the merge policy is a replaceable strategy (§4) whose variants —
fixed-priority, union, agent-arbiter — are supposed to be comparable on identical
detections. A scorer that merged overlapping spans on its own authority would apply
one policy's behaviour to all of them, and every `RT`-family arm would score alike no
matter which policy produced it. The comparison the experiment is built to make would
return "no difference" for a reason having nothing to do with the detectors.

Deduplication happens for the credit question only. Coverage and the layer view read
the full prediction set, since "which layers found this" is precisely what they are
asking, so nothing about provenance is lost. The collapsed volume is reported as
**`duplicate_predictions`** per mode: it is the amount of layer agreement that would
otherwise have been counted against precision, and an unreported collapse is
indistinguishable in the output from a detector whose layers never duplicated anything.

#### Per-rule attribution is computed inside the scorer, not joined on afterwards

The RuleAuthor needs to know which of *its own* rules misfired, not only that the arm's
precision fell. An agent shown aggregates alone can add rules; only an agent shown the
per-rule counts can delete one, and a rule file that grows monotonically is the
characteristic failure of the `port-loop` arm. So each mode block carries **`by_rule`**:
per `rule_id`, its declared layer, how many spans it emitted (`fires`), and its `tp` and
`fp`.

**The false positives come from the assignment matching's unmatched predictions.** Not
from coverage, and the difference is the whole point of the block. A rule's span that
overlaps a gold span of the right type but loses the assignment to a
better-overlapping prediction is a false positive *for that rule*: the credit went
elsewhere and cannot be given twice. Under coverage-based attribution that span reads
as a hit, and the rule that never wins anything — the one an author should delete —
reads as harmless. §9.3's two questions separate here for the same reason they separate
for the corpus, and attribution is the credit question, not the disclosure question.

The alternative was to compute these counts outside the scorer, joining `spans.jsonl`
against the assignment result. **Rejected, because that puts the matching logic in two
places.** The join would need its own notion of which prediction won which gold span —
the same greedy pass, the same eligibility rule, the same total-order tie-break — and
the moment the two implementations disagree there are two answers to "which rule fired
here" with nothing in either output to say which is right. The disagreement would not
announce itself either: both files would be internally consistent, and the per-rule
table is exactly the artifact nobody cross-checks against the aggregate. Keeping the
attribution inside the loop that already has `matched` and `fp` in hand means there is
one matching in the system, and the per-rule numbers are a projection of it rather than
a reconstruction.

Two consequences of that placement, stated because they are the kind of thing that
gets summed by accident:

- **`by_rule` totals need not equal the mode's `overall` counts.** Tagger spans carry no
  `rule_id` and are absent from the table, which makes the rule total smaller; a
  byte-identical span emitted by two rules is credited to both while the assignment
  sees it once, which makes it larger. The reliable bound is
  `overall + duplicate_predictions`. Crediting only the copy that survived
  deduplication was the alternative, and it would make the table depend on the order
  the detector emitted spans in — two rules swapping credit between runs with nothing
  else in the output moving.
- **A rule that fired nothing has no row.** The scorer never reads the rule file, so it
  cannot tell a rule that matched nothing from a rule that does not exist. The
  RuleAuthor holds the file and can see which of its ids are missing.

`rule_id` carries the language prefix of the file that produced it (`es:doctor_prefix`),
and the scorer requires it: `es-carmen` loads two rule files (§5.2), and an unprefixed
`doctor_prefix` in each would share one row with the two rules' counts added together.
A rules-family span must carry an id and a tagger span must not — the first would drop
out of the attribution silently, and the second would put a checkpoint name in a table
whose rows are supposed to be deletable rules.

One privacy consequence, since `by_rule` puts rule names in a published file: a
`rule_id` is text an agent chose, it reaches `metrics.json`, and `metrics.json` is
committed to a public repository. The prohibition on surface forms therefore binds
`rule_id` as strictly as it binds a comment (`docs/prompts/rule_author.md`
Prohibition 2), and it is screened where the id is created. The scorer's own error
messages never quote an id for the same reason they never quote a span.

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

### A2. `port-oneshot` on a second model family — pre-registered 2026-08-07

**Pre-registered before any `port-oneshot` result exists, and that timing is the point.**
§4 holds the model family fixed across every rung so that adjacent rungs differ in one
capability, which leaves all three comparisons internally valid and externally unanchored.
This entry is the answer to that, and an answer added *after* seeing the numbers would be a
post-hoc selection — the family, the count, and the standing below would all then be
choices informed by which of them flattered the result. So they are fixed here instead,
while nothing is known.

**The model: `us.meta.llama4-maverick-17b-instruct-v1:0`.** Verified callable on Bedrock on
2026-08-07 (`docs/notes/baseline-model-family.md` records the probe). Chosen on two
properties that follow from what the appendix is *for*:

- **Open weights.** The appendix number is reproducible outside Bedrock, by someone without
  this account. A closed model achieves half the purpose — it shows a second family, but the
  reader has to take the number on trust, and the whole objection being answered is one
  about trusting a self-comparison.
- **Published benchmarks.** The reader can judge the reference point's strength
  independently, rather than being told it is comparable.

The rejections are recorded because "we tried one other model" and "we chose this other
model for stated reasons" are different claims, and only the second survives a reviewer
asking why not another:

- **`us.amazon.nova-premier-v1:0`** — same platform, so the family distance is weakest where
  the appendix needs it to be widest, and the silent-alias risk is identical to Claude's.
- **`openai.gpt-oss-120b-1:0`** — open-weight and a genuinely distant family, but not
  frontier-class, which invites the weak-baseline objection this entry exists to close.
- **`us.deepseek.r1-v1:0`** — reasoning-family output is verbose, and this task demands one
  complete well-formed file, so format failure and capability failure would be mixed
  together in exactly the way the last paragraph below is about.
- **The OpenAI API directly (GPT-5 family)** — excluded on data governance, not capability.
  PhysioNet's guidance prohibits sending credentialed data to the OpenAI API and names
  Bedrock and Azure as permitted routes; §1.4 of `docs/prompts/rule_author.md` puts ±120
  characters of corpus context into the prompt, so there is no version of this that avoids
  the restriction. Azure OpenAI is a permitted route, but it needs a separate account, a
  human-review opt-out, and IRB wording confirmed — and this project does not widen its
  compliance surface for one appendix number.

**Standing: robustness check, not a main result.** It overturns no rung's verdict. If it
comes back strong enough to be interesting — a single Llama call at or above `port-loop`,
say — that is a reportable finding in its own right and is reported as one, and it is still
not grounds for reopening §4. §4's argument is about attributability and does not become
false when a number is surprising. A design decision that gets revisited because an appendix
result came out a certain way is a decision that was made for the wrong reason, which is the
sentence at the top of this section.

**Format-compliance retries: zero, on both sides — decided 2026-08-07, before either arm
was run.** `rules/{lang}.yaml` has a validated schema (§3, `rule_author.md` §2): `lang` must
match, `rule_id` unprefixed, exactly one matcher, `layer` and `phi_type` from their axes. One
call, one file, and whatever comes back is the result.

Two reasons, and the second is the one that decides it.

**There is no basis for choosing a *k*.** A retry budget above zero has to be a number, and
every number is arbitrary — nothing about the task, the schema, or either model makes three
attempts the right count rather than two or ten. An arbitrary parameter set before the run is
still arbitrary; it just looks pre-registered. Worse, it is a parameter whose value changes
the result: at *k*=0 a model that emits invalid YAML reports a failure, at *k*=5 the same
model probably reports a number, and the appendix's conclusion would then rest on a constant
picked for no reason. Zero is the only value that is not a choice among alternatives.

**A format failure is itself a result, and a reportable one.** Producing a valid rule file
from the §1.4 prompt in one call is part of what the rung is being asked to do — the
`port-oneshot` arm *is* "one call, one usable artefact", so an unusable artefact is a
finding about capability and not an accident that happened on the way to the finding.
Retrying hides it: the arm reports a leak rate and the reader never learns it took four
attempts to get a parseable file, which is precisely the fact that distinguishes the two
models. Zero retries is what makes the appendix able to say *this model could not do it*, a
sentence a retry loop is designed to prevent anyone from having to write.

**On failure, the failure is recorded.** Not an empty results directory and not a rerun with
the prompt quietly adjusted:

- The arm's `metrics.json` is **not** written — there are no detections to score, and a
  metrics file with zeros in it would be indistinguishable from a rule set that ran and
  caught nothing.
- The attempt is recorded in the arm's directory with the model id, the raw response, and
  **the validator's own error message verbatim**. The error is the evidence; a paraphrase
  ("the file was malformed") is not something a reader can check.
- The cost block is written regardless, since the call was made and paid for. `llm_calls`
  counts every attempt rather than only an accepted one — a definition that now applies to
  a count of one, and stays correct if a later arm ever does retry.

**Symmetry is the point of fixing this at all.** Claude gets zero retries too, on the same
prompt through the same validator. An asymmetric budget would make the appendix measure
instruction-following rather than rule-writing ability, which is not the quantity it is there
to anchor — and the asymmetry would run in the direction that flatters the model this
project is built on, which is the one direction a reader has every reason to suspect.

**How far the model is identifiable, measured 2026-08-08 — and it is only to the alias.**
The Nova rejection above cites "the silent-alias risk," and that risk was then measured
rather than left as a phrase. It is worse than the rejection implies, because it applies to
the Claude side of this comparison too. `docs/notes/baseline-model-family.md` records four
probes; the finding is that **nothing on the wire identifies the weights that answered**. A
`converse` response names no model at all by default.
`additionalModelResponseFieldPaths=["/model"]` does return one, but across three request ids
it was never more specific than the request — a dated id comes back dated, an undated alias
comes back undated. `GetInferenceProfile` resolves only to undated ids, and its
`startOfLifeTime` answers when an id first appeared, not which weights serve it today.

**The unresolved marker.** Every run records three fields rather than one, from
`Response.model_record()`:

| field | what it holds |
|---|---|
| `model_id` | the id requested — the one thing under this project's control |
| `model_id_reported` | what the response said, or `null` if the field did not come back |
| `model_id_resolution` | how far identification got: `dated`, `alias-unresolved`, or `mismatch` (`config/naming.yaml`) |

Three fields because their *agreement* is the only check available and one field cannot
express it. **The two arms of A2 now differ in this field, and the asymmetry is the
platform's rather than a choice** (revised 2026-08-11, see the reversal below): the Claude
side runs `us.anthropic.claude-opus-4-5-20251101-v1:0` and records `dated`, while
`us.meta.llama4-maverick-17b-instruct-v1:0` carries no snapshot date and records
`alias-unresolved` — Bedrock offers no dated Llama 4 id, so the appendix's second family
cannot be pinned however the ladder is. Everything below about what a stable alias cannot
tell you therefore still holds, and it now describes **one side of A2 and no rung of the
ladder**. `mismatch` is refused rather than recorded: a call that succeeded while nobody can
say which model answered is not a result this experiment can use.

**What survives, and what does not.** The distinction matters because the limitation is
narrower than it first sounds:

- **A2's own claim survives intact.** The comparison is Anthropic versus Meta, and the two
  aliases name distinct *families* unambiguously — no weight update turns a Claude alias
  into a Llama one. The axis the appendix rests on is not the axis that is unresolved.
- **Re-running this appendix later does not — on the Llama side.** A silent weight update
  behind a stable alias is undetectable from here. If someone re-runs the Llama arm in six
  months and gets a different number, the record cannot say whether the model changed or the
  code did — and that is exactly the attribution A1/A2 exist to make possible. **So the paper
  must say it recorded the model *alias*, not the model, for that arm.** The stronger
  sentence is not available there.

  **It is available on the Claude side, since 2026-08-11.** The dated pin closes this bullet
  for every ladder rung and for A2's Claude arm: a re-run names the same snapshot, so a
  changed number is attributable to the code. That is the whole of what the pin buys and it
  is the reason it was adopted — see the reversal below.

Partial mitigations, stated as partial. Neither is an identifier and neither is claimed to
be one:

- **A date and a commit hash beside the alias**, which bounds *when* that alias was resolved
  even though it cannot say to what. **In place since schema 4** (2026-08-09):
  `scorer.REQUIRED_RUN` names `generated`, `commit` and `tree`, and `check_run` refuses a run
  block missing any of them before a results directory exists. This replaces the work-owed
  note that stood here while the mitigation was described in the design and absent from the
  writer — the shape §5.4 is about. `generated` must match a UTC instant to the second
  (`2026-08-09T14:03:22Z`), not a bare date: the question this mitigation answers is which of
  two numbers came from the earlier resolution of an alias, and two runs made on one day
  cannot be ordered by their date. `tree` must be one of `clean`, `dirty`, `unknown` — the
  vocabulary `sealed_log.tree_state()` produces.

  **All three rather than the hash alone**, because the hash is the field that reads as
  sufficient and is not. A commit identifies the code that ran only if the tree was clean, so
  `commit` without `tree` may describe something other than what executed; `unknown` is what
  `tree` says when git could not be reached, and a run recording it has a hash whose meaning
  is unverified rather than a hash that is wrong. `generated` is what remains useful in
  exactly that case. Requiring the hash on its own would have published the most confident of
  the three by itself.

  **`commit` may be null, and only with `tree` of `unknown`** (`scorer.NULLABLE_RUN`). The
  key is still required — a field some arms omit cannot be compared across arms, and a null
  nobody wrote cannot be told from a null that was measured — but `tree_state()` returns
  `(None, "unknown")` when git cannot be read, and a validator demanding a truthy hash there
  leaves the writer two options: refuse to score a real run, or put something in the field.
  The second is what happens, and a hash that reads as identifying the code while nobody
  checked whether it does is the failure this field was added to prevent. The pairing is what
  keeps the exemption from becoming a loophole: `clean` and `dirty` are both read from a git
  command that also produced a revision, so either of them beside a null hash is refused as a
  contradiction rather than accepted as a gap. The first version of this check demanded the
  hash unconditionally, and what caught it was the mutation harness's own tree — a repository
  with no commits, where the writer could no longer write.
- **The token counts**, which are circumstantial: a re-run whose `prompt_tokens` differ on a
  byte-identical prompt met a different tokeniser and therefore probably a different model.
  It can raise suspicion and cannot confirm identity, and equal counts prove nothing.

**Every agent arm records `tree: dirty`, and it means "the arm wrote its own output" —
observed 2026-08-11 on `port-oneshot-nofence`, not fixed.** The run block said `dirty` on a
run made from a committed tree: `git diff --name-only` was empty before the call and the
dry-run printed `tree clean` seconds earlier. `sealed_log.tree_state()` reads
`git status --porcelain`, which **counts untracked files**, and by the time `_run_block()`
is reached the arm has already created its own results directory — `agent_calls.jsonl` is
deny-listed and gitignored, but the window record, the rule file and the directories holding
them are untracked at that instant and become tracked only at the commit afterwards. So the
field is measuring the arm's own artefacts. This is structural, not incidental: an arm that
writes a record before it scores cannot be in a tree that has no new files in it, and every
rung — `port-loop`, `port-multi` — will record the same value for the same reason.

**Untracked-counts-as-dirty is deliberate and stays.** The mutation
`only_tracked_modifications_count_as_dirty` exists precisely to stop this being "fixed" by
switching to `git diff --name-only`: under that reading an untracked file *and* a staged one
both come back `clean`, which silences the one case a person checks by hand. Given a choice
between a field that over-reports and a field that under-reports the state of a tree, this
project takes over-reporting, and §6.1's dirty-tree paragraph is the same choice made about
a different consumer.

**What is actually wrong is the field's readability, and it is a Methods obligation.** §10's
mitigation paragraph above defines `tree` as what says whether `commit` describes the code
that executed. A reader — reasonably, from that definition — reads `dirty` as *someone had
uncommitted source changes when this ran, so the hash may not be the code*. On every agent
arm it means something weaker and unrelated: *the writer had already written its own outputs*.
The value is not false; its meaning is narrower than the definition invites, and the
mitigation is worth less than it appears on exactly the runs the paper's agentic claims rest
on. So **the paper's Methods needs one line**: that agent arms record `tree: dirty` because
the arm creates its untracked output directory before the run block is written, that the
recorded `commit` is the committed state the call was made from, and that the field therefore
does not distinguish a modified source tree on those runs. Saying this in prose is cheap;
having a reader discover it is not, because the natural inference is that the ladder was run
off uncommitted code.

**Why it is not being fixed here.** Every available fix reaches into a component the sealed
evaluation depends on. `tree_state()` is called by `run_sealed_eval.py` (§6.1's gate, which
refuses a dirty tree by default), by `run_fold.py`, by `orchestrate.py` and by
`tools/run_arm.py`; `scorer.REQUIRED_RUN`/`NULLABLE_RUN` pin the vocabulary and the
`commit`-null pairing, and three mutations hold its current behaviour — with the number of
tests that catch each one measuring how far the behaviour has spread:
`an_unreadable_tree_state_reads_as_clean` 58, `a_dirty_tree_reads_as_clean` 7,
`only_tracked_modifications_count_as_dirty` 2. Changing what `dirty` means changes what the seal gate refuses on, and
**moving the sealed-evaluation gate as a side effect of improving a metrics field is the wrong
order of operations** — the gate is the thing with the fewest permitted changes in this
project, and a change to it should be the subject of a decision, not a consequence of one.
A run already recorded under the current semantics is also not re-writable: `port-oneshot-nofence`'s
block is committed and its arm's window is frozen, so any fix produces a ladder whose rungs
disagree about what the field means unless it is applied before the first rung, which it now
cannot be.

**Options, if it is fixed later — recorded, not chosen.** All four are stated with what they
cost, and none is adopted here:

- **Add a fourth vocabulary value, e.g. `dirty-untracked-only`,** distinguishing "untracked
  files exist" from "tracked files are modified", with plain `dirty` reserved for the latter.
  This is the only option that makes the field say what a reader thinks it says. It widens
  `scorer.TREE_STATES`, so it is a schema change with a version bump, and it must decide what
  the seal gate refuses on — the honest answer is probably still "both", which means the gate
  keeps its behaviour while the metrics field gains resolution.
- **Have the arm capture `tree_state()` before it writes anything** and pass it into
  `_run_block()`, so the value describes the tree the call was made from. Cheapest change,
  and it makes `tree` answer the question §10 asks of it. The cost is that the writer then
  reports a state it observed earlier than the write, which is a small lie of a different
  kind — and the scorer would need to accept a run block whose `tree` was not measured at
  scoring time, i.e. the provenance of the field becomes arm-dependent.
- **Exclude the arm's own results directory from the porcelain read.** Rejected on sight for
  the record: a path-scoped exclusion inside the function every gate calls is the shape
  `allowlist_may_name_corpus_paths` is aimed at one level up, and a check that ignores a
  directory is a check somebody will widen.
- **Leave the code and document it only** — the current state, which is a decision and not an
  omission provided the Methods line above is actually written. What makes it defensible is
  that the field over-reports rather than under-reports; what makes it unsatisfying is that a
  permanently-`dirty` field is a field readers stop reading, which is the exact argument §6.1
  used to split the `SEALED` line out of `BLOCKED`. That parallel is the reason this is
  recorded as unfinished business rather than as settled.

**`model_id_reported` and `model_id_resolution` do not join `REQUIRED_RUN` — decided
2026-08-09, with the orchestrator.** The argument for was real and is the one this section
makes everywhere else: a field the writer may omit is a field some arms will lack, and a
resolution recorded for only some runs cannot be compared across them. What decides against
it is that there is exactly one writer today and it cannot observe either field.
`src/eval/run_fold.py` closes the `R` arm, which calls no model; required, the two fields
would be filled with `null` and `none` on every run it makes.

**A required field that one writer fills with a placeholder makes the placeholder the
convention.** The next writer copies the block rather than the reasoning, and by the time a
second model-calling arm exists, `"model_id_resolution": "none"` is what the schema looks
like — a value that reads as a measurement, on runs where nothing was measured. That is the
same failure as a fabricated commit hash one field over, and worse for being invited by the
schema instead of chosen by a writer.

So the requirement lives where the observation does: **`src/orchestrate.py` requires all
three of `Response.model_record()` in its own run block**, and `REQUIRED_RUN` names only
`model_id`, which every arm can answer. The two fields are raised into `REQUIRED_RUN` when a
second model-calling writer exists — at that point two writers can be held to the same
requirement without either inventing a value, and the argument for comparability applies to
runs that can all answer.

**Pin a dated id — reversed 2026-08-11, before the first agent call.** This paragraph
previously declined to pin, and the reversal is recorded rather than the old reasoning
replaced, because the argument that was wrong was wrong in a specific and instructive way.

**What it used to say, and why it was not unreasonable.** A dated Bedrock id
(`claude-opus-4-5-20251101`) comes back `dated`, so the resolution field would read better —
but this project's whole toolchain (`CLAUDE_CODE_USE_BEDROCK=1`, every probe in
`compliance.md` §2) runs on `us.anthropic.claude-opus-5`, and pinning **the appendix** to a
different snapshot than the agents actually used would trade a recorded limitation for an
unrecorded discrepancy. A limitation stated in the paper is cheaper than a mismatch nobody
wrote down.

**The term that was missing from that scale.** The comparison was generation against
reproducibility, and it never weighed **the unrepeatability of the arm itself**. Every rung's
window is binding from the moment its first `agent_calls.jsonl` line lands (§6.3) — not from
a decision to keep it, but as a physical fact about what the record then attests to. So
`alias-unresolved` here is not the ordinary limitation the old paragraph priced. In an
ordinary run it means *we cannot say today, and could re-run to narrow it*; in a frozen arm
it means **there is no later moment at which what this arm ran on can be established, by
anyone, ever.** The mitigations two paragraphs up bound *when* the alias was resolved and
say nothing about *to what*, and no future measurement reaches back past the freeze. That is
a different quantity from the one the old paragraph traded away, and it is not recoverable.

**The decisive point: pin the whole ladder, and the discrepancy does not get traded — it
ceases to exist.** The old objection was to pinning *the appendix* to a snapshot the rungs
did not use. It was an argument against a *partial* pin, and it was read as an argument
against pinning. With every rung on `us.anthropic.claude-opus-4-5-20251101-v1:0` there is no
second snapshot for anything to disagree with: the ladder, the appendix's Claude side, and
each arm's `model_id` all name one dated id, `model_id_resolution` reads `dated` on every
Claude arm, and the mismatch the old paragraph was protecting against has no two things left
to hold apart. What remains is a gap between the *agents' own harness* (Claude Code on
opus-5) and the model the arms call, and that gap is not a confound in any comparison the
paper makes — the harness writes no rules and is scored on nothing. §4 states the ladder-wide
pin and why the generation difference costs the argument nothing.

**What is given up, stated plainly.** The arms run one generation below the frontier
available on this account. Nothing in §4's three comparisons rests on frontier capability
(see §4's paragraph on this), and the direction of the effect, if any, favours readability:
less saturation leaves more room for a rung to show its effect.

**When this is revisited, and the condition is narrower than it looks.** A dated `opus-5`
being offered would be grounds to pin *newly started* arms to it — and **an arm whose window
is already frozen is never re-pinned**, because re-pinning it is impossible: its call is
made. The consequence is the one that decides the shape of the rule. If a dated `opus-5`
appeared mid-ladder and were adopted, the ladder would straddle two models, and
`port-multi` vs `port-loop` would then differ in role specialisation *and* in model —
exactly the two-axis failure §4 rejects for the baseline. So the condition is: **the ladder
is pinned once, and a new dated id applies only to a ladder none of whose rungs has been
frozen.** A mid-ladder model change is a new ladder, reported as one.

---

## 11. `port-human` protocol — **RETIRED 2026-08-07, not deleted**

> **This arm was withdrawn on 2026-08-07, before iteration 1 was run.** The reason is
> resourcing and not a finding: **no human author could be secured** for the volume of
> rule-writing the protocol requires, and the protocol's own §11.3 stopping rule (the
> agent loop's δ and k) makes the required effort open-ended in hours. The baseline moved
> to `port-oneshot`; see §4 for what that costs the paper's claims.
>
> **The section stays because the protocol is the expensive part.** Every decision below
> was settled before any dev document was read for rule-writing purposes, and that
> ordering is the only thing that makes them usable — a protocol re-derived after seeing
> results is a different object with the same text. If a human arm is revived, it starts
> from these rules as written rather than from a fresh round of reasoning that would
> arrive somewhere subtly more convenient. Deleting the section would throw away the
> pre-registration and keep only the inconvenience.
>
> **State at retirement**, so a revival knows what it inherits:
>
> - `results/es-meddocan/R/sup-free/port-human/window_freeze.json` exists — the frozen
>   window at revision 5, both hashes recorded. `human_log.jsonl` has one row with a null
>   `human_minutes`, so `arm_has_started()` is `False` and the freeze is still revisable.
> - The iteration-1 window was **never drawn** for rule-writing. The practice rehearsal
>   (iteration 900, `docs/notes/port-human-practice.md`) drew from a pool with iteration
>   1's spans subtracted, so iteration 1's forty spans remain unread.
> - `port-human` stays in `naming.yaml`'s `porting` axis, marked retired. §4 explains why
>   removing it would be the wrong kind of tidy.
> - The tooling is intact and tested: `tools/show_human_window.py`, `src/porting/`,
>   `src/rules.py`, `tools/check_rules.py`. The last two are not `port-human`-specific and
>   every agent arm uses them.
>
> **What a revival must not do** is treat the retirement as licence to revise the
> protocol. The decisions below were made without knowing any arm's results. By the time
> a human arm is revived, `port-oneshot` and probably `port-loop` will have run, and every
> §11 decision will have a visible consequence for how the comparison lands — which is
> exactly the condition under which pre-registration stops being possible. Re-open a
> decision only with the change and its date recorded here, so a reader can see which
> rules predate the results and which do not.

> **Fixed before the arm runs, and that is the point.** This arm's *procedure is its
> data*: `port-human` is the control, and a control whose protocol was decided while
> running it measures the protocol as much as the human. Every rule below was settled
> before any dev document was read for rule-writing purposes. Deviations are reported
> as deviations rather than folded into the protocol.

Every sealing rule so far has been structural — a directory that is not read, a gate
that refuses, a screener that blocks. Those hold whether or not anyone is paying
attention. `port-human` is the first place a person deliberately reads dev and writes
rules from what they see, and structure cannot enforce what a person does with what
they remember. So the protocol has to be written down in advance and reported as
written, including where it was violated.

**The fairness principle this section serves.** `port-human` exists to answer "does the
agent pipeline beat a person doing the same job?" That question is only answerable if
the person and the agents are given the same job. Every decision below therefore
records **which arm it favours**, because an unfair control does not produce a weaker
claim — it produces an uninterpretable one. A `port-loop` that beats a handicapped
human and a `port-loop` that beats a well-equipped human are the same number and
different results, and nothing in `metrics.json` distinguishes them.

### 11.1 What the human may look at in dev

The constraint is symmetry with the agent arms, in **both** directions: the human must
not get a window the RuleAuthor lacks, and must not be denied one it has. §3 says the
RuleAuthor's `rules/{lang}.yaml` is "iterated against dev" and that its tool use is
"running detection on dev", which fixes less than it appears to — it does not say
whether the agent sees individual error spans with their surface text or only aggregate
scores.

**Decision: (b), a fixed sample.** The human sees **n error spans per iteration**, drawn
by a seeded rule from the previous iteration's scorer output. The alternatives and what
they would have cost:

| option | the human sees | cost | favours |
|---|---|---|---|
| (a) full dev | every error span, every iteration, entire dev fold | Slowest, and the human's advantage compounds in a way that cannot be undone or measured. Also the least reproducible: "the author read the dev fold" is not a specifiable amount of reading. | **the human**, strongly |
| **(b) fixed sample — adopted** | n error spans per iteration, seeded draw | Requires deciding n and the draw (random / stratified by type / worst-types-first); each choice is an experimental parameter that has to be recorded. Reproducible, and symmetric to an agent arm with the same n. | roughly **neutral** — the arm it favours depends on n, which is why n is derived rather than chosen freely |
| (c) aggregates only | per-type leak rate and complementarity, no individual spans | Cheapest and most defensible against contamination, but below what the agent arm gets, and a rule author who never sees a miss cannot write a context cue for it. Handicaps the control. | **the agents** |

**Normative ordering: the RuleAuthor prompt is specified first, and the human's window
is derived from it.** `n` is not chosen for the human's convenience; it is set to the
number of dev error spans the RuleAuthor's prompt actually carries, and the draw rule is
set to whatever selects them there. The reverse order — fixing what the human gets and
then building the agent's prompt around it — takes the human's working conditions as the
reference point, and a control calibrated to itself is not a control. It is also the
direction in which the failure is invisible: an agent prompt quietly enlarged to match
what the author found useful produces a `port-loop` win that reads as an agent result.

Consequences of the ordering, stated so they are not renegotiated later. If the
RuleAuthor prompt turns out to carry no error spans at all, then n = 0 and the human
arm runs under (c) — the human is held to the agent's window even though that window is
poor for rule writing. If it carries a large sample, n is correspondingly large. **The
agent arm's design fixes the human's window in both directions**, and neither outcome is
grounds for revisiting this section; it is grounds for revisiting the RuleAuthor prompt,
which is where the decision belongs.

**The window, as derived.** `docs/prompts/rule_author.md` is written, so this is now a
derivation and not a plan. The prompt carries §1.4's error-span block, so the human's
window is:

| what | value | where it is fixed |
|---|---|---|
| error spans per iteration | n = 40 | `config/sampling.yaml: n_error_spans` |
| context per span | ±120 characters | `config/sampling.yaml: context_chars` |
| stratification | proportional by `phi_type`, at least 1 per type with any error | `config/sampling.yaml: min_per_type`, `src/sample.py: _allocate` |
| which spans | seeded draw, seed = SHA-256 of (scheme, base seed, corpus, iteration) | `src/sample.py: sample_seed`, `draw` |
| score block | the reduced dev `metrics.json` of prompt §1.3, including `by_rule` | §9.3, prompt §1.3 |
| Auditor report | `reports/leaks_{iter}.json` with prompt §5's three-case reading | §3, prompt §5 |

The values are in a config file and the draw is in one function that both arms call,
which is what makes the symmetry checkable rather than asserted. **The seed takes the
corpus and the iteration and not the arm**, so both arms at iteration 3 draw by the same
procedure from their own error pools — and that the pools differ is the experiment, not an
asymmetry. The prompt's §7 carries the full row-by-row comparison of what each side
receives, including the two rows where the human's input is better (the rule file and the
task frame are re-read rather than re-delivered, since an agent call retains nothing
between calls) and the direction each favours. Both favour the human, both are asymmetry
(1) appearing in the input rather than the recollection, and the reading rule below is
what makes them tolerable: `port-human` is an upper bound, so an advantage to the human
weakens no claim this project makes.

**Three asymmetries that (b) does not remove.** They are recorded rather than solved,
and each one carries an explicit instruction for how to read the result — because an
asymmetry that is merely disclosed still gets forgotten by the time the numbers are
discussed.

1. **Memory carry-over — human memory does not reset; agent context does.** An agent
   call sees its prompt. A person who reads a sample at iteration 3 still knows it at
   iteration 9, and still knows it when porting the *second* corpus. Under (b) the
   sample is bounded and logged, so the volume of what was seen is known even though
   what was retained is not. This favours the human, and it specifically contaminates
   the §11.2 scope split: a decision sincerely reported as "general knowledge about
   clinical Spanish" may be a memory of a dev document.

   **Reading rule: `port-human` results are an upper bound on human performance under
   this protocol.** Where an agent arm matches or beats `port-human`, that comparison is
   sound and if anything conservative. Where `port-human` wins, the margin includes an
   unmeasured carry-over component and cannot be reported as the cost of automation.

   **A rehearsal moves this asymmetry's starting point to before iteration 1.** A
   practice session was run on `es-meddocan` before iteration 1, in the reserved
   iteration band (`config/sampling.yaml: practice_iteration_min`), so that the rule-file
   schema, the three layer syntaxes and the feedback command could be learned without
   spending iteration 1's forty spans on tool acquisition. Without it, iteration 1 would
   measure "what a person generalises from forty errors *while learning the tools*",
   which is not a quantity any other arm produces. The fact, the date and the iteration
   number are in `docs/notes/port-human-practice.md`; what was seen and what was written
   are deliberately not, since recording them would make them an input to iteration 1
   that no agent arm receives.

   Two mechanisms keep the rehearsal off iteration 1's sample. The band is refused in
   **both** directions — an arm may not draw a practice number and a rehearsal may not
   draw a real one — and the practice/real distinction is **declared by the caller, never
   inferred from the number**, because inference cannot separate the two cases at all: an
   iteration-1 call with the flag unset is either a rehearsal whose caller forgot or the
   real thing, and since the draw is seeded, the wrong guess prints byte-for-byte the
   window the real run would have shown while leaving nothing behind that says it was
   read early. Separately, the practice pool has iteration 1's drawn spans *subtracted*
   before the practice draw, because a different seed is not a guarantee of a different
   sample and a span read in rehearsal is one iteration 1 no longer measures honestly.

   **What this adds to the reading rule: the sum of `human_minutes` excludes tool
   acquisition.** The rehearsal writes no log line — that is the protocol — so the time
   it took is recorded nowhere, here or in the note. `port-human`'s cost figure is
   therefore "time spent by someone who already knows the protocol", not "time to port
   this pipeline". The second claim would require measuring the rehearsal, and this
   experiment does not measure it.

2. **n = 1 — a person cannot be re-run.** Every other arm can be replayed from
   `agent_calls.jsonl`. `port-human` has one execution, no variance estimate, and no way
   to separate the protocol's effect from this particular author's skill. Which arm this
   favours is not determinable — a single sample lands on either side of the mean with
   no way to know which.

   **Reading rule: every claim is scoped to "compared against one human trial", in that
   wording, not "compared against a human".** Between-author variance is unmeasured, so
   a difference smaller than plausible between-author variance is not a finding. No
   confidence interval is computable for this arm and none is reported for it.

3. **DUA inversion — on some corpora the agent is the more restricted party.** Sending
   CARMEN-I or MIMIC-derived text to an external LLM API is a data-transfer question,
   not a prompt-design question. Where the answer is no, the agent arms cannot receive
   the sample that (b) gives the human, and the asymmetry inverts: the human sees spans
   the agent is forbidden to see.

   **Reading rule: the window is recorded per corpus, not once for the experiment.**
   Each corpus's results state the human's n, the agent's n, and which of the two was
   the binding constraint. A cross-corpus statement about `port-human` is only made
   across corpora where those agree; where they do not, the corpora are reported
   separately and the direction of the asymmetry is named. A corpus on which the agent
   is text-restricted is not evidence about agent capability at all — it is evidence
   about deployability under a DUA, which is a different claim and is labelled as one.

**What this licenses:** a stated, auditable dev window per corpus, derived from the
agent arm's own prompt. **What it does not:** a claim that the control was equally
equipped. Asymmetry (1) is unresolvable in principle, which is why the reading rule
makes `port-human` a bound rather than a point estimate.

### 11.2 What is recorded

The requirement is that `port-human` costs land on the **same axes** as every other
arm, since CLAUDE.md requires cost beside quality and §5's cost block is `llm_calls` /
`prompt_tokens` / `completion_tokens` / `wall_seconds`. A human arm has zero of the
first three and a working-hours figure that is not comparable to an agent's wall time —
one is compute, the other is a person's attention.

**`results/{corpus}/{detector}/{supervision}/port-human/human_log.jsonl`**, one line per
event, deliberately parallel to `agent_calls.jsonl` so the two can be read on one axis
where they are commensurable and visibly not where they are not. The path is declared as
`paths.humanlog` in `config/naming.yaml` alongside `paths.agentlog` when the arm is
implemented — not written as a literal in code, under CLAUDE.md's rule that a new value
goes into the config before it goes into a module. Note that `{porting}` is fixed to
`port-human` here rather than templated: this file exists for exactly one value of that
axis, and a template implying otherwise would invite a second arm to write it.

*Implemented.* The window is handed over by `tools/show_human_window.py`, which prints
it to a terminal and refuses a pipe or a redirect: the rendered contexts are the one
thing in this arm that must exist only in transit (`rule_author.md` §6), and `> window.txt`
is the accident that leaves a DUA corpus's text on disk. It writes no log line — the
author reports `human_minutes`, and a script that logged on every invocation would turn "how
many times did the author look" into a count of terminal commands.

*Implemented.* `paths.humanlog` and `paths.humanfreeze` are declared in
`config/naming.yaml`, read through `base.path_template()`, and filled by
`src/porting/human_arm.py`, which checks each component against its axis before building
a path — a results path names the cell of the experiment a number belongs to, so an
unknown component would mint a cell rather than fail.

| field | meaning |
|---|---|
| `iteration` | integer, matching the agent arms' iteration counter |
| `event` | `read_sample` / `decision` / `rule_edit` / `score_run` |
| `human_minutes` | elapsed human time for this event — **deliberately not `wall_seconds`** |
| `decision` | free text: what was decided and why |
| `predicted_scope` | `global` or `corpus_specific`, recorded when the decision is made |
| `actually_reused` | `true` / `false` / `null`, filled in from the second corpus |
| `evidence` | what prompted it: sample span IDs (never surfaces), a per-type rate, or `prior_knowledge` |
| `model_consulted` | `none` / `mechanical` / `notation` / `rule_content` — the §8 self-report, required on every line |
| `rules_commit` | commit hash of `rules/{lang}.yaml` after this event |
| `prompt_sha256` | SHA-256 of `docs/prompts/rule_author.md` — the window this event was held to |
| `sampling_sha256` | SHA-256 of `config/sampling.yaml` — where n and the context width actually live |

**Human time is `human_minutes`, never `wall_seconds`, and the name is the mechanism.**
A person's working hours and a pipeline's wall clock are different quantities, and the
only thing standing between them and a summed total is that they cannot be summed by
accident. Under a shared field name, some future aggregation over arms adds them —
correctly, as far as the code is concerned, since every arm has a `wall_seconds` and
adding them is what a total does. A distinct name makes that aggregation fail to find
the field instead of silently producing a number. Minutes rather than seconds for the
same reason: false precision in a self-reported duration invites arithmetic it cannot
support. Agent `wall_seconds` is still recorded for `port-human`'s scorer runs, which
genuinely are compute.

**The window is identified by content hash, on every line, and in two places.** §11.1
derives the human's window from the RuleAuthor prompt, so a record of a `port-human` run
that does not identify which version of that prompt it was held to cannot support the
comparison it exists for. Three decisions inside that:

- **Content hash, not commit hash.** The commit says when the tree was; the hash says
  what the file was. An uncommitted edit to the prompt moves the second and not the
  first, and an uncommitted edit is exactly the event this record exists to catch —
  `rules_commit` is already there for the question the commit answers.
- **Two files, because the parameters are not in the prompt.** n = 40 and ±120 characters
  live in `config/sampling.yaml`. A window can be doubled by changing one integer without
  touching the prompt at all, and a record naming only `prompt_sha256` would agree with
  the new window as readily as with the old.
- **On every line, not once per run.** A per-run header records what was frozen at the
  start, which is the wrong end of the question: what a reader needs to know is whether
  the window at iteration 9 was the window at iteration 1. A value repeated on every line
  answers that by disagreeing with itself; a header answers it by construction and
  therefore not at all.

**And once more in a file that cannot be rewritten** — `paths.humanfreeze`, beside the
log, written before iteration 1 and never again. (The convention this generalises to — freeze
at first use, one record per arm — is §6.3, and `port-oneshot` follows it under its own
`{porting}` value rather than inheriting this record.) This is not the header the previous
point rejects, and the difference is which question each answers. Per-line hashes answer
*did the window move during the run*, by disagreeing with each other. They cannot answer
*what was the window this arm committed to*, because every line is honest about its own
event: a run whose `n` was doubled at iteration 5 has internally consistent lines
throughout, and the log alone cannot say which half was the deviation. The freeze record
is the fixed point the lines are compared against, so `freeze_window()` returns an
existing record rather than overwriting it — a freeze record that can be rewritten
records the window a run *ended* with, which is the one thing nobody needs to know.

**And the refusal is conditioned on the log, not on the record.** `freeze_window()`'s
first version refused to write when the record existed, which is a refusal a `rm` steps
around in one command — and did, three times before iteration 1 on `es-meddocan`
(`docs/notes/window-freeze-history.md`). A guard conditioned on the presence of the thing
it protects is a request addressed to whoever can remove the evidence. So the binding
condition is `arm_has_started()`: a non-null `human_minutes` on any line of the
append-only `human_log.jsonl`, which survives the record's deletion.

That places the boundary exactly where §11.1 puts it. Before any minutes are recorded the
window is a proposal and revising it is free, including after a deliberate delete. After
the first minute a person has spent attention on a window, no analysis re-runs that
attention under a different one, and `freeze_window()` raises on every path — if the
record is missing it says *restore it from git* rather than writing, since a re-created
record hashes today's files and then claims to be the opening window. The only remaining
route to a different window is re-running the arm from iteration 1 with a different
author, which is this section's ordering cost paid rather than avoided.

**And the log is read from git history as well as from the working tree**, because the
first version of that condition was itself conditioned on a removable file: `rm
human_log.jsonl` re-opened the freeze. `started_where()` reads the working tree first
(cheap, and the answer in any ordinary run), then `git log --all` over that one path and
`git show` on each commit's blob — any commit, any branch, not only the newest, since the
newest is the one an edit would have changed. When the minutes are in history but the log
is not on disk the refusal says so and says restore the log too, because a refusal that
only reported "this arm has started" would leave a missing log unnoticed. Every `git`
failure — no repository, no git, a timeout — answers "history says nothing" rather than
raising, which fails in the unsafe direction and is why the working tree is consulted
first.

Two holes stay open and are asserted as tests rather than argued away: a rewritten history
defeats the guard, and minutes that were never committed cannot be recovered. Local history
is enough because the repository is public — removing the evidence now takes a history
rewrite, which is visible to anyone who has fetched it. **The purpose is to prevent an
accident and to make a deliberate change conspicuous, not to make one impossible.** No
condition inside a Python function is unremovable; the reachable goal is a condition whose
removal leaves a trace, and `docs/notes/window-freeze-history.md` records both the three
re-freezes and these limits.

`window_drift()` reports which of the two files no longer matches, and reports rather
than raises. The honest response to drift is not always to stop: an edit to the prompt's
prose that leaves §1.4's numbers alone is a different event from a change to `n`, and
only a person can tell them apart. What is not optional is noticing — undetected, a
mid-run change makes the iterations before it and the iterations after it two experiments
reported as one.

**`model_consulted` is the arm's own contamination record.**
`docs/prompts/rule_author.md` §8 forbids asking a language model what a rule should be
during a `port-human` iteration — rendering, scoring, logging and validation may be
delegated, but "which pattern fits this error" is answered by the person. The reason is
that the arm is the control: an author who transcribes a model's answer has run
`port-oneshot` through a slower interface, and every comparison in this section then
reports the difference between two agent arms as the difference between a person and an
agent. That does not weaken the claim, it makes it uninterpretable, which is §11's
fairness principle applied to the arm the principle was written for.

Four values rather than a boolean, because a boolean is answered `false` honestly by an
author who used a model to render the sample — which §8.1 allows — so the field would
stop distinguishing the case it exists for. Naming the allowed uses (`mechanical`,
`notation`) makes `rule_content` a deliberate entry rather than a judgement about
whether "using a model" happened. Required on every line, with no `null`: an unfilled
field is indistinguishable from an unproblematic one, and per-event rather than
per-run so the obligation is in front of the author each time.

**And `rule_content` is accepted by the harness, not refused.** A self-report field that
rejects the answer it exists to capture collects only the other answers, and the arm's
integrity would then be documented by a file that could not have recorded its absence. A
run carrying it is reported with those iterations identified — as a limitation, or re-run
with a different author as a different trial — and either response requires the log to
have said so. Its standing is the same as `predicted_scope`: a self-report whose bias
direction is known, recorded contemporaneously, and reported against the arm it favours.
It cannot detect a violation its author declines to write down and is not presented as
though it could.

**Span IDs, never surfaces.** CLAUDE.md forbids corpus text in logs, and this file is
committed. A decision log that records "added a cue for the phrase X" republishes X.
The referent is `(doc_id, span_index)`, which is resolvable by anyone holding the
corpus and inert to anyone who does not.

**Scope is two fields, not one, and the split is what makes the optimism bias
measurable.** The hypothesis is that human intervention converges to a constant as
corpora are added: global decisions are paid once, corpus-specific ones recur.
Classifying a decision requires the author's own judgement, and that judgement is weak
in a *specific direction* — someone who believes the convergence hypothesis will file
borderline decisions as `global`, which is the flattering direction and the direction of
the paper's own claim.

A single `scope` field cannot survive this, because it stays editable. When the second
corpus reveals that a `global` decision had to be redone, the honest move and the
convenient move are the same edit to the same field, and afterwards nothing in the file
records that a prediction was ever wrong. So:

- **`predicted_scope`** is written when the decision is made, before any evidence about
  reuse exists, and **is never edited afterwards**.
- **`actually_reused`** is written during the *second* corpus's port: `true` if the
  decision carried over unchanged, `false` if it had to be redone or replaced, `null`
  while no second corpus has been ported.

The prediction is therefore recorded before its own test, which is what makes optimism
bias a **measurable quantity rather than a caveat**: the disagreement rate between
`predicted_scope == "global"` and `actually_reused == false` is the calibration error,
it is computable from the log, and it is reported. Retrospective adjustment is not
prevented by trust but by the fact that the two fields disagree permanently — a corrected
prediction still shows as a wrong prediction.

**Favours: the human arm**, since the residual bias runs toward the convergence
hypothesis. The two-field split does not remove that; it converts it from an
unfalsifiable direction of error into a number with a sign, which can then be reported
against the arm that benefits from it.

**Rule snapshots per iteration.** One commit per iteration on a branch, so each
iteration's rule file is recoverable and diffable. This is cheap and there is no
argument against it; the `rules_version` already recorded in `metrics.json` (§5) makes
each scored iteration point at an exact rule file.

**Scorer runs are logged as their own event type.** The human will run the scorer on dev
repeatedly. That cost is the harness's, and every agent arm pays it too, so charging it
to the human would inflate `port-human`; but the person does wait for it, so excluding
it understates what porting actually costs a person. `score_run` events carry both
`human_minutes` (waiting) and `wall_seconds` (compute), so either total is reconstructible
from the log and the choice is a reporting decision rather than a data-collection one.

### 11.3 When the human stops

§3 fixes the agent loop's termination: dev leak rate improves by less than δ for k
consecutive iterations, or the call budget is exhausted. **If the human arm has no
corresponding rule, the two arms are compared under different stopping rules, and the
difference between them is partly a difference in when someone chose to quit.** This is
the most consequential of the three decisions, because a human who stops early loses
and a human who stops late wins, and neither is a fact about human-versus-agent
porting.

**Decision: the same δ and k as the agent loop.** The human iterates until the dev leak
rate improves by less than δ for k consecutive iterations. The alternatives:

| option | how it works | cost | favours |
|---|---|---|---|
| **same δ, same k — adopted** | identical convergence test to §3 | Directly comparable, and the default reading of "same stopping rule". But δ was chosen for a loop that can cheaply run a marginal iteration; a person facing a fifth iteration for a 0.3% gain feels that cost differently, and holding them to it measures protocol compliance as much as judgement. | **the agents** — the rule was designed around their cost structure, and adopting it accepts that cost against the control |
| human-judgement stop | the author stops when they judge returns exhausted, and logs why | Realistic, and this arm's external validity rests on realism. But unfalsifiable as a comparison: any result is explicable by when they chose to stop. | **the human** |
| δ/k with a hard ceiling | the convergence test plus an independent cap | Keeps comparability and bounds the worst case in both directions. More bookkeeping. | roughly **neutral** |

Adopting the same δ and k is a choice that **favours the agent arms**, and the reason to
take it anyway is that the alternative destroys the comparison rather than tilting it. A
`port-human` that stopped on judgement can be dismissed by any reader who prefers the
other result, and there is no analysis that recovers from it. A `port-human` held to a
rule built for a different cost structure yields a comparison that is interpretable and
biased in a **known and stated direction** — which is the trade this project has taken
everywhere else it appears.

**The ceiling is in iterations; hours are recorded but do not bind.** The two units fail
differently. **Iterations** are directly comparable to the agent arms' call budget, are
the unit §3 already uses, and are the unit in which "the agent ran longer" can be stated
at all — but an iteration is not a fixed amount of human effort, so an iteration cap lets
the human spend unbounded time inside a bounded count. That is the direction that
**favours the human**, and it is the accepted cost of this choice. **Hours** cap the
resource actually scarce for a person, but have no agent counterpart: an hours ceiling is
a stopping rule with no mirror in the arm being compared against, and its stringency
relative to the agent's budget could only be asserted, never computed. `human_minutes`
from §11.2 is therefore aggregated and reported per iteration and in total, so the
unbounded-time-inside-bounded-count risk is **visible in the results** even though it is
not capped. A reader who wants the hours-normalised comparison can compute it; a reader
who wants iteration-matched comparison gets it directly.

**The agent arm is scored at two points: the human's stopping iteration, and its own
termination.** Both go in the results. The reason is that "better" and "ran longer" are
two claims that a single number cannot separate:

- At the **human's stopping iteration**, the comparison is iteration-matched. This is
  the figure that answers "with the same number of passes over dev, who is ahead?"
  Scored on its own it would *favour the human*, since a cheap marginal iteration is
  precisely the agent's real advantage and truncation discards it.
- At the agent's **own termination**, the comparison is each-to-its-own-completion. This
  answers the practitioner's question, and scored on its own it favours the agents for
  the mirror reason.

Reporting both makes the stopping-rule confound a **quantity instead of an argument** —
the same move §9.3 made with `assignment_slack`, and for the same reason: a gap between
two defensible numbers is informative, while a single number hiding that gap is not. The
cost is one extra scorer run against dev and **no extra agent calls**, since the
iterations already ran and each one's rule file is committed (§11.2). **Favours neither
arm**, which is the point of doing both.

**The surro 1.9× standard applies to `port-loop` vs `port-multi`, and it is a
pre-registered criterion.** §3's record notes a configuration that scored better while
costing 1.9× budget and 2.5× wall time, and was demoted for it. That standard is adopted
here **before any of these arms have been run**, which is what makes it a criterion
rather than a rationalisation: a cost threshold chosen after seeing the results is chosen
to fit them. Concretely — a `port-multi` that beats `port-loop` while costing on the
order of 1.9× is not accepted as a win for role specialisation, and the same reasoning
applies to `port-loop` beating `port-human` by running several times as many iterations.
CLAUDE.md already requires the cost block for exactly this comparison, and §4's ladder
states that if `port-loop` does not beat `port-oneshot` the agentic framing is not
earned. **Favours neither arm** — it is a reporting requirement that constrains every
arm equally, including the ones this project would prefer to win.

**Prompt caching makes the 1.9× standard readable in two ways, and only one of them is
about the loop — so both numbers are published** (decided 2026-08-18, on the 2026-08-16
measurement in `docs/notes/baseline-model-family.md`). §5.4's fourth exit lets an Auditor call
be split so that Bedrock retains the constant prefix — `auditor.md`, the input banner, §1.1's
frame — for five minutes. The prefix is 80.7% of an average audit call, and a round sends the
same template 250 times, so the effect on what a naive reading of the envelope reports is
large: in the probe, `inputTokens` fell from 7193 on the uncached call to **21** on the cache
read, a factor of 340, while `totalTokens` was **7197 on all three calls**. **The model read the
same number of tokens every time.** What changed was which side of the transport they came
from.

The risk is a sentence nobody would write on purpose but that the numbers write by themselves:
*"`port-loop` clears the 1.9× standard"* — supported by a cost column that fell because a
prefix stopped being retransmitted, in a comparison against arms that were never cached. That
is not a claim about role specialisation, iteration count, or anything §4's ladder is about. It
is a claim about a service's transport optimisation, and it would be sitting in the row of the
table where a reader looks for the other kind of claim. The failure mode is not a wrong number;
it is a correct number answering a question nobody asked, in the place reserved for the answer
to a different one.

**So `prompt_tokens` in every arm's cost block is the raw total — `inputTokens + cacheRead +
cacheWrite` — and the `caching` block publishes the reads and the writes beside it.** The raw
total is the comparable figure: it is what the model processed, it does not move when transport
changes, and it is the number the 1.9× standard was pre-registered against. The billed basis is
recoverable by subtracting `read_tokens`, so a reader who wants the invoice can compute it and a
reader who wants the work does not have to know the invoice exists. Both are reported, per
CLAUDE.md's requirement that cost travel with quality and per this section's own rule that a
confound becomes a quantity rather than an argument. **A cached arm and an uncached arm are
comparable on `prompt_tokens` and are not comparable on anything derived from `inputTokens`
alone** — which is exactly why the envelope's own `totalTokens` is cross-checked against the
assembled figure at the transport (§5.4) rather than trusted at the reporting layer, where the
disagreement would already have become a table.

**What this does not license:** reporting a cache-derived cost reduction as a result of any
kind. Caching changes no prompt — the two content blocks concatenate to the byte-identical text
the uncached call sent, and `tests/test_prompt.py` asserts it — so it cannot appear in the
quality columns at all, and in the cost columns it appears only as `read_tokens`. An arm that
is cheaper *because it was cached* is the same arm.

**What this licenses:** a stated stopping rule applied to both arms, with the
iteration-matched and run-to-completion comparisons both reportable, and a cost threshold
fixed in advance. **What it does not:** a claim that either arm ran to the point of
diminishing returns for *its own* cost structure. δ and k were set for one cost structure
and the arms have two, which is a known limitation of the comparison rather than
something the protocol repairs.
