# `ko-surro`: the 659 placeholders that mark a position and name no type

Opened 2026-08-27. **Resolved 2026-08-28 by measurement, not by choosing an option.** The
human reference was obtained (`ko-surro-gold-provenance.md`) and it types these spans
directly, so five of the six options below exist only to work around a file that turned out
to be open access. They are kept because the measurement changed which question is open, and
the record of what was considered is worth more than a tidy note.

**What the gold says about the 659:**

| In the human gold | Count | Share |
|---|---|---|
| `Date` | 439 | 66.6% |
| `DateYear` | 29 | 4.4% |
| **no gold span at all — not PHI** | **191** | **29.0%** |

So the type question is answered for 468 of them, and for the remaining 191 it dissolves:
there is nothing to type, because the human reference does not mark PHI there. Every one of
the 468 is in the DATE family, which is what the shape rule (option 1) would have guessed —
but option 1 would also have assigned DATE to all 191 non-PHI spans, and that is the error
the option's "against" row anticipated without being able to size it.

**By payload shape, the over-marking is concentrated exactly where the note predicted:**

| Payload shape | Total | Gold-supported | No gold span | Over-marked |
|---|---|---|---|---|
| month–day | 550 | 374 | 176 | **32.0%** |
| year–month–day | 67 | 64 | 3 | 4.5% |
| two digits | 28 | 20 | 8 | 28.6% |
| four digits, year-like | 11 | 10 | 1 | 9.1% |
| other digits, or empty | 3 | 0 | 3 | 100% |

The month–day shape is 83.5% of the set and a third of it is not PHI. The note said this
shape "is precisely the shape that collides with clinical values" and cited the producing
project's 142 not-PHI relabels as circumstantial support. The census puts it at 176.

**What is still open.** Not the type of these spans, but whether this reference may be used
at all for a Korean arm: the offsets index the English `id.text`, and the Korean corpus was
built from `id.res`, so 59 gold spans have no Korean counterpart. That is DESIGN §7's
question, not this note's. See `ko-surro-gold-provenance.md` §7.

Everything below is the 2026-08-27 note, unchanged, except that the closing section now
records what actually happened.

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

## What dissolved the question — 2026-08-28

The 2026-08-27 version of this section said the release ships `id.types` and that acquiring
it would give the 659 human types, so no option should be chosen before that was attempted.
The instinct was right and two of the particulars were wrong. `id.types` does not exist in
any distribution — it is named in a README manifest and nowhere else. And the files that do
carry the reference are not in the DUA-restricted release at all; they are in the
de-identification software package, which is **open access**. There was no acquisition to
attempt. There was a download, and it had been available from the start.

`id-phi.phrase` field 5 supplies the types, and the numbers are at the top of this note.
Options 1, 4 and 5 are unnecessary, for the reason this section already gave. Option 3
(exclude all 659) is now clearly wrong rather than merely costly: 468 of them are gold PHI,
so excluding them would drop real spans from the reference to avoid 191 spurious ones, when
the reference distinguishes the two exactly. Options 2 and 6 are moot for these spans, and
the `OTHER` vocabulary question they raised does not need answering on their account.

**The residue is not a type question.** It is the 191 placeholders the gold does not support.
They are in the corpus as PHI and the human reference says they are not PHI, so whichever way
they are treated, the choice is now visible and sized rather than inherited. That belongs
with the other 550 unsupported placeholders in `ko-surro-gold-provenance.md` §6, not here.
