# `ko-surro`: the 659 placeholders that mark a position and name no type

Opened 2026-08-27. **No decision is recorded here yet.** DESIGN §6.6 §2 fixes only that
the position question and the type question are separate and that the second is open; this
note holds the measurement, the option set, and what each option costs. It is referenced
from §6.6 §2 so that the open question has one location rather than being rediscovered.

Every figure below was produced by `tools/gold_provenance_check.py check`, which reads the
source release's two held files and prints counts only.

---

## The measurement

| Quantity | Value |
|---|---|
| Records | 2,434 |
| Placeholders | 2,164 |
| Payload is a type name | 1,505 (69.5%) |
| **Payload is a value, not a type** | **659 (30.5%)** |
| Placeholders whose span could not be recovered | 0 |
| Distinct payloads, digits normalised | 69 |

Shape of the 659:

| Shape | Count |
|---|---|
| month–day | 550 (83.5%) |
| year–month–day | 67 (10.2%) |
| two digits, bare or apostrophe-prefixed | 28 (4.2%) |
| four digits, year-like | 11 (1.7%) |
| other digits and separators | 2 (0.3%) |
| empty payload | 1 (0.2%) |

**658 of the 659 are nothing but digits and separators.** So this is not a general
"type unknown" case: the placeholder carries a date value where the others carry a type
name, and the shape is date-like in 95.4% of them by inspection of the shape alone.

## Why the marker criterion does not settle it

DESIGN §6.6 §2's criterion is whether the marker carries the label. These placeholders
split the criterion:

| Question | Answer |
|---|---|
| Where is the span? | **recoverable** — the bracket delimits it, exactly as for a typed placeholder. 0 of 2,164 failed |
| What type is it? | **not recoverable from the marker.** The payload is a value, and deriving a type from a value is a pattern rule — the same kind of object as the predictions being scored |

The consequence is asymmetric and worth stating before any option is chosen: **leak rate is
unaffected**, because it needs positions only, and it is the headline. What is not defined
over these spans is the per-type decomposition that §7's layer prediction reads. 30.5% of
spans is a large hole in a per-type table and no hole at all in the headline.

## The options

| Option | Detail |
|---|---|
| **1. Type from the payload's shape** | year–month–day / year / month–day → DATE |
| └ for | deterministic, auditable, free; covers 95.4% by shape alone |
| └ against | a shape rule is a detection rule, so this is rule-generated gold — the thing §6.6 §2 bars for the shifted-date case, applied here under a different name. And month–day, 83.5% of the set, is precisely the shape that collides with clinical values: the producing project relabelled 142 placeholders as not-PHI, concentrated there |
| **2. Position gold, type withheld** | keep the span, leave the type unresolved; count in leak rate, exclude from the per-type decomposition |
| └ for | claims exactly what the marker supports and nothing more. Headline intact |
| └ against | the per-type table loses 30.5% of its mass, and §7's layer prediction is read off that table |
| **3. Out of scope** | mark all 659 excluded under §9.1 |
| └ for | symmetric with the shifted-date decision and the easiest to defend |
| └ against | moves `n_spans_in_scope`, therefore `n_dev`, therefore δ — the §6.6 interlock bites. And it computes leak rate over a reference that omits the most-masked type |
| **4. Inherit the producing project's resolved labels** | 2,016 PHI / 142 not-PHI / 19 unresolved |
| └ for | already exists, already reviewed, already documented; free |
| └ against | mixed provenance — manual override, then a rule, then an LLM context judgement (of the 142: rule 0, human 101, LLM 41). **LLM-assigned labels used as gold to score an LLM-agent pipeline** is a circularity of its own kind. And it imports another project's judgements into our reference, where CLAUDE.md keeps the code lineage separate |
| **5. Adjudicate the 659 here** | annotate them |
| └ for | highest quality, and the only option producing labels of the *same kind* as the typed 69.5% |
| └ against | cost, and it is annotation: the project's premise is annotation-free supervision, so this corpus's `supervision` axis value could no longer be `sup-free` and the change would have to be declared |
| **6. Two-track reporting** | typed 69.5% is the per-type reference; the 659 enter the leak-rate union with the type unresolved |
| └ for | CLAUDE.md already puts leak rate and P/R/F1 on different bases, so this adds no machinery |
| └ against | two denominators to explain, and per-type recall is then over a different span set than the leak rate. Silent otherwise |

**A vocabulary question, not yet answered.** Options 2 and 6 need a value for "position
known, type unresolved". `config/naming.yaml`'s `phi_type` axis already carries `OTHER`,
described as the residual bucket a corpus ships and not a rule-development target. That
description nearly fits, and reusing it would avoid adding a value. Whether it *does* fit —
whether "unresolved" and "residual" are the same thing for scoring — is part of whichever
option is chosen and is not settled here.

## What could dissolve the question entirely

The source release ships `id.types`, a human reference for the gold PHI types, and it is
**not on disk** — only the surrogate text and the tool output are
(`data/acquire/fetch_kosurro_gold.sh`). If the reference files are acquired, the 659 have
human types and options 1, 4 and 5 all become unnecessary: the type comes from the same
place the typed 69.5% does. That is the cheapest resolution available and it is a download
from a project whose DUA is already signed, not a new acquisition. **No option here should
be chosen before that is attempted**, because five of the six exist only to work around a
file that may simply be fetchable.
