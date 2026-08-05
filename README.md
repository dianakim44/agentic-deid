# agentic-deid

Multi-agent porting of a clinical de-identification pipeline to new languages
and note types, with annotation-free supervision.

> Status: under development. Nothing here is a released artifact yet.

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
| 1. Layered detection | Checksum-validated rules, context cues, gazetteer, and a neural tagger emit typed spans |
| 2. Recall-first merging | Overlap clusters collapse to the union of their boundaries, so a multi-token entity is never shortened by a competing detector |
| 3. Pseudonymization | Date offsets preserve intervals; surrogates stay consistent within a document; high-risk identifiers become type tags |
| 4. Annotation-free supervision | Placeholder positions supply the training labels |

Stages 1–4 are language-independent. The rules, gazetteers, and encoder are not
— they are per-language instances, and producing them is what the agents do.

## The agents

| Agent | Produces |
|-------|----------|
| Corpus Profiler | Tag conventions, PHI type inventory, document structure |
| Schema Mapper | Corpus taxonomy → canonical type set |
| Rule Author | `rules/*.yaml` for the target language |
| Lexicon Builder | Institution, department, and region gazetteers |
| Leak Auditor | Residual PHI in de-identified output, fed back to the Rule Author |

Every agent runs at build time. Their output is human-readable and committed;
the deployed pipeline is deterministic.

## Evaluation

Arms compared on identical sealed folds:

```
1  rule set only
2  neural tagger only
3  rule set + tagger        (fixed-priority merge)
4  rule set + tagger + agent arbitration
H  human-authored port      vs   A  agent-authored port
```

Reported quantities are the **leak rate** (share of PHI spans with zero
coverage) and the **complementarity breakdown** (found by rules only / tagger
only / both / neither) — not F1 alone, since a recall-first merge dominates on
recall by construction.

## Layout

```
rules/          detection rules as data (YAML), versioned, per language
src/            detectors, merge strategies, agents, evaluation
tools/          release_screen.py and other repo hygiene scripts
splits/         split.json — patient-disjoint folds, frozen before development
sealed/         test fold. not read during development
results/        aggregate metrics only, no source text
```

## Data

No corpus is distributed with this repository. Acquisition scripts and licence
terms live under `data/README.md`.

## License

MIT for code and aggregate results. See LICENSE for the scope note on corpora.
