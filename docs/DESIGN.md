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
that masked text is safe text. On es-meddocan's dev fold the masked input is on the order of
110,601 tokens (`splits/es-meddocan.json`) against §1.4's 40-span window at roughly 2,700 —
about **40×** — and under the leak rates this arm actually produces a majority of in-scope
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

- **Cost structure, from the one measurement available that is not a result.** The
  `port-oneshot` call's *token counts* — 14,071 prompt, 2,325 completion — are cost
  measurements rather than quality findings, so using them here does not smuggle 0.560 in.
  Adding §1.3 (per-type tables plus `by_rule`) and §1.4 (40 spans at ±120 characters) puts a
  RuleAuthor iteration at roughly 21k tokens; the Auditor reads the masked dev fold, whose
  token total is 110,601 (`splits/es-meddocan.json`), so **an iteration costs on the order of
  135k tokens, dominated by the Auditor rather than the RuleAuthor.** Eight iterations is
  therefore around 1.1M tokens against `port-oneshot`'s 16.4k.
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
  iteration. Twelve buys about 500k more tokens for iterations that δ/k is very unlikely to
  reach. Eight leaves k = 2's confirmation cost affordable (six productive iterations before
  the two that stop it), which is the property that decides it.
- **Hitting the ceiling is a reported outcome, not a failure to record.** An arm that
  terminates on the cap has *not* satisfied the convergence test, and the two must be
  distinguishable in `metrics.json` — a run that stopped at 8 with the leak rate still
  falling is a different claim from one that stopped at 5 having converged. The termination
  reason is therefore recorded, and a ceiling-terminated run may not be described as
  converged.

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
| `port-multi` vs `port-loop` | does role specialisation justify *multi*-agent? | role differentiation |
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
nothing.** The three comparisons ask whether iteration, role specialisation and delegated
role design pay for themselves — each is a question about *harness structure at a fixed
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

### 5.4 A filled prompt is a type with two named exits, and there is one renderer

**Decided 2026-08-08, before the first agent arm was run.** `docs/prompts/rule_author.md` §6
already fixed the rule: only the template is committed, and a filled instance is not
committed, not logged, and not written to disk at all. What is decided here is *how* that
rule is held, because §6 as written is a rule each call site obeys, and this repository has
a measurement of what that costs. The availability defect in `tests/conftest.py` shipped
four times, three of them after it had been written up in `tests/mutations/README.md`. A
written warning is not a control.

So the filled prompt is not a `str`. `src/llm/prompt.py` defines `FilledPrompt`, which has
no public accessor that is not named for a destination:

| exit | destination | check |
|---|---|---|
| `to_terminal(stream)` | a person reading a screen | refuses a stream that is not a terminal |
| `for_transport()` | the model call | none; the transport must not log, and `tools/check_bedrock_logging.py` is what checks that |
| `reference()` | a run block or a log line | returns references, counts and hashes; no text |

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
return statements construct a `FilledPrompt`; the public method set is exactly the three
exits. The renderer's *interior* is in that set and not only its signature, because the
mutation `renderer_writes_a_debug_copy` leaves the type entirely intact and defeats it
completely — a type protecting a value that has already reached `/tmp` protects nothing.
`release_screen.py` blocks the committed paths a filled instance would land under, and `/tmp`
is not one of them, which is why the convention is "never written" rather than "never
committed" and why a path pattern cannot be the check.

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
`FilledPrompt` gives is *text that does exist has two exits*. Wrapping the first in the second
substitutes "it is a `FilledPrompt`, so it is safe" for "it has no surface form, so it is
safe" — and the second is the true reason. Once the weaker claim is what the code asserts, a
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

So the shared cause is narrower than "target-language tokens": it is **abbreviation and
inflection of concepts the vocabulary already contains**. `nuss`/`nass` and
`gazetteer`/`gaz` are the same relation as `year`/`years` and `abbreviation`/`abbrev`, both
of which the first widening had to repair in the same way. The genuinely
target-language-specific failure happened once and was answered structurally; what has now
recurred is the *closed-set-of-surface-forms* failure, which the per-language layers did not
address because they are also closed sets of surface forms. That is the pattern to watch, and
it predicts the third occurrence will again be a spelling variant rather than a new language.

**Options for a root fix, if the third arrives — recorded, not chosen, and nothing is changed
now.** The constraints any of them must respect: the screener imports only the standard
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

**What this licenses:** a stated stopping rule applied to both arms, with the
iteration-matched and run-to-completion comparisons both reportable, and a cost threshold
fixed in advance. **What it does not:** a claim that either arm ran to the point of
diminishing returns for *its own* cost structure. δ and k were set for one cost structure
and the arms have two, which is a known limitation of the comparison rather than
something the protocol repairs.
