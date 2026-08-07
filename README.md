# agentic-deid

Multi-agent porting of a clinical de-identification pipeline to new languages
and note types, with annotation-free supervision.

> Status: under development. Nothing here is a released artifact yet.

Design decisions and their rationale live in [docs/DESIGN.md](docs/DESIGN.md).
Experiment identifiers are defined in [config/naming.yaml](config/naming.yaml)
and nowhere else.

## The problem

Supervised PHI taggers need annotated clinical text — but annotating clinical
text requires the same protected access that de-identification is meant to
grant. Annotated corpora therefore stay rare, and each new language, note type,
or institution starts from zero.

De-identified corpora with placeholder markers, on the other hand, are common.
This project turns the second kind into the first: substituting a surrogate for
each placeholder yields gold offsets that are exact by construction and free of
inter-annotator disagreement.

## The pipeline being ported

| Stage | What it does |
|-------|--------------|
| 1. Layered detection | Checksum-validated rules, context cues, gazetteer, and a learned span tagger each emit typed spans |
| 2. Recall-first merging | Overlap clusters collapse to the union of their boundaries, so a multi-token entity is never shortened by a competing detector |
| 3. Pseudonymization | One date offset per document preserves intervals; surrogates stay consistent within a document; high-risk identifiers become type tags |
| 4. Annotation-free supervision | Placeholder positions supply the training labels |

The four stages are language-independent. The rules, gazetteers, and encoder are
not — they are per-language instances, and producing them is what the agents do.
*Porting* means producing those instances, in the software-engineering sense.

The claim is deliberately not "language agnostic". What is portable is the
recipe: a new language or note type can be supported with N hours and zero
annotated spans, reaching leak rate X.

## The agents

**An agent is defined by the file it produces, not by a persona.** One agent, one
output file. An agent that produces no artifact does not exist, and two agents
never write the same file.

| Agent | Produces |
|-------|----------|
| Profiler | `profiles/{corpus}.json` — format, offset convention, type inventory, group key |
| Mapper | `mappings/{corpus}.yaml` — corpus taxonomy → canonical type set |
| LexiconBuilder | `lexicons/{lang}/` — institutions, regions, departments |
| RuleAuthor | `rules/{lang}.yaml` — iterated against dev |
| Auditor | `reports/leaks_{iter}.json` — suspected residual PHI |

**Start with three:** Profiler, RuleAuthor, Auditor. Mapper and LexiconBuilder
are added only if the ablation shows the first three leave something on the
table — building five at once makes it impossible to tell which one works.

**The orchestrator is deterministic code**, not an agent. Execution order,
retries, and budget live in `src/orchestrate.py`. There is no manager agent and
no planner agent: an agent that only thinks costs money and produces nothing.
Loop termination is explicit — dev leak rate improves by less than δ for k
consecutive iterations, or the call budget is exhausted.

**The Auditor never sees gold.** It reads only the de-identified output and flags
spans that look like surviving PHI, which lets one component serve both as
build-time feedback to the RuleAuthor and as the runtime component of the
`RT-Aud` detector. Scoring against gold is a separate deterministic scorer;
agents and scoring are never mixed.

Every agent runs at build time. Their output is human-readable and committed;
the deployed pipeline is deterministic.

## Evaluation

Four independent axes, with the values defined in `config/naming.yaml`:

```
corpus       ko-surro · es-meddocan · de-grascco · es-carmen · en-n2c2
detector     R · T · RT · RT-Arb · RT-Aud · RT-Arb-Aud
supervision  sup-free · sup-human
porting      port-oneshot · port-loop · port-multi · port-selfdesign
             port-human — RETIRED 2026-08-07 (DESIGN §11, §4.1)
```

| Detector | Meaning |
|----------|---------|
| `R` | rule set only (regex, checksum, context cues, gazetteer) |
| `T` | learned span tagger only |
| `RT` | rules + tagger, recall-first union merge |
| `RT-Arb` | `RT` with an agent arbitrating disagreeing spans |
| `RT-Aud` | `RT` with a leak auditor re-reading the de-identified output |
| `RT-Arb-Aud` | `RT` with both |

The porting axis is a ladder of autonomy, and two comparisons carry the paper:
`port-loop` vs `port-oneshot` asks whether iteration justifies calling this
agentic at all, and `port-multi` vs `port-loop` asks whether role specialisation
justifies *multi*-agent. If `port-loop` does not beat `port-oneshot`, the agentic
framing is not earned — a real possible outcome the experiment is designed to
detect.

The path is the identifier:

```
results/{corpus}/{detector}/{supervision}/{porting}/metrics.json
```

Ordinals (`arm1`) and status words (`best`, `final`, `deployed`) are not used.

### Reported quantities

- **Leak rate** — share of gold PHI spans with zero coverage. The only
  operationally meaningful number: a missed identifier is a disclosure.
- **Complementarity breakdown** — found by rules only / tagger only / both /
  neither. This is what shows whether the neural layer earns its cost.

Precision, recall, and F1 are reported but are not the headline. Because the
merge is a union, a combined detector dominates its components on recall *by
construction*, so F1 alone would be uninformative.

Per-arm cost is reported alongside quality — LLM calls, tokens, wall time. A
quality gain that costs 2× is a different result from one that costs 1.05×.

## Layout

```
docs/           DESIGN.md — decisions and rationale, read before proposing changes
config/         naming.yaml — the only definition of experiment identifiers
rules/          detection rules as data (YAML), versioned, per language
src/            detectors, merge strategies, agents, evaluation, orchestrate.py
tools/          release_screen.py and other repo hygiene scripts
splits/         {corpus}.json — patient-disjoint folds, frozen before development
sealed/         test fold. not read during development
results/        aggregate metrics only, no source text
```

## Data

No corpus is distributed with this repository — neither DUA-restricted material
nor openly licensed corpora. Acquisition scripts and licence terms live in
[data/README.md](data/README.md).

## License

MIT for code and aggregate results. See LICENSE for the scope note on corpora.
