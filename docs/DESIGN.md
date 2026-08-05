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

- **Leak rate** — share of gold PHI spans with zero coverage. This is the only
  operationally meaningful number; a missed identifier is a disclosure.
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
| GraSCCo | German | GP letters | Zenodo, open |
| CARMEN-I | Spanish / Catalan | discharge, referral, radiology | PhysioNet, request pending |
| ko-surro | Korean | nursing notes | derived from PhysioNet; DUA |
| n2c2 2014 | English | longitudinal progress notes | portal unavailable; on hold |

Selection is not opportunistic. The corpora span a spectrum of **orthographic
cue reliability**:

```
English discharge / radiology   capitalisation marks names        rules strong
Spanish clinical cases          capitalisation, compound names    middle
German GP letters               all nouns capitalised             cue uninformative
English nursing notes           capitalisation breaks down        cue unreliable
Korean                          no case distinction at all        cue absent
```

Hypothesis: the rule layer's contribution scales with cue reliability, and the
learned tagger fills the gap. This unifies the language axis and the note-type
axis under one mechanism — English nursing notes sit near Korean, not near
English discharge summaries.

---

## 8. What is outside the system

The paper's system is `src/orchestrate.py` and the agents it calls. Everything
recorded in `agent_calls.jsonl` is inside.

Claude chat sessions and Bedrock Claude Code are research tools, not system
components. They are disclosed in the AI-use statement, not in Methods. Design
conversations that shape the role set are, however, the record of the
`port-human` arm and the source material for `port-selfdesign` prompts — worth
keeping.
