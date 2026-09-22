# `ko-surro`: where the human reference is, and how it was looked for in the wrong place

Written 2026-08-28, after a claim recorded on 2026-08-27 turned out to be false and the
correction turned out to be better news than the claim. Kept because the *shape* of the
error is reusable: a documentation manifest was read as a distribution inventory, and an
access-control response was read as an existence signal.

---

## 1. The claim that was wrong

Recorded on 2026-08-27, in `data/acquire/fetch_kosurro_gold.sh`, `docs/DESIGN.md` §6.6,
`docs/notes/ko-surro-untyped-spans.md`, and `tools/gold_provenance_check.py`:

> The source release ships five files. Two are held (`id.text`, `id.res`). Three carry the
> human reference (`id.deid`, `id.types`, `id-phi.phrase`) and need a credentialed download.

Four claims, and three of them are false.

| Claim | Verdict |
|---|---|
| The release ships five files | **False.** It ships two. Its project page describes exactly `id.text` and `id.res` |
| The reference files need a credentialed download | **False.** They are in the de-identification *software package*, which is open access — Open Data Commons Attribution v1.0, "Anyone can access the files" |
| `id.types` exists | **False.** The package README names it; no distribution contains it. Its content is `id-phi.phrase` field 5 |
| `id.deid` and `id-phi.phrase` carry the human reference | **True**, and they were downloadable the whole time |

## 2. Where the five-file list came from

Not from an inference, and not from the release. From a **document**: the de-identification
software package's `README.txt`, lines 61–65, which lists five corpus-related files in one
manifest —

| Line | Names |
|---|---|
| 61 | `id.text` — the gold standard corpus |
| 62 | `id.deid` — PHI locations in it |
| 63 | **`id.types`** — categories of those PHI |
| 64 | `id-phi.phrase` — locations and the PHI terms |
| 65 | `shift.txt` — per-patient date shifts |

That manifest is a description of the *gold standard corpus as an object*, spread across two
distributions and one file that was never released. It was read as an inventory of one
directory. Nothing in it says which distribution ships which file, and the release's own page
does say — "For other gold standard corpus related files (such as the detected PHI location),
please see the associated software package" — which was not read closely enough at the time.

So the failure was not hallucination and not pure inference. It was **a real document, read
for a question it does not answer.** That is harder to catch than an invented fact, because
checking the source confirms the string is there.

## 3. Why three 403s did not falsify it, and one 404 did

The three URLs were probed unauthenticated on 2026-08-27 and returned 403. That was recorded
as consistent with "present but restricted". It is consistent with that. It is equally
consistent with absent, and the probe that shows this takes one line:

| URL under `/files/deidentifiedmedicaltext/1.0/` | Status, unauthenticated |
|---|---|
| `id.text` (exists) | 403 |
| `id.deid` (does not exist) | 403 |
| `nonexistent-control.xyz` (certainly does not exist) | **403** |

PhysioNet applies access control before existence checking, so under `/files/` a 403 carries
**zero bits** about existence. The 2026-08-27 report presented "403 confirmed" as evidence;
it was evidence of nothing. The user's 404s, obtained while logged in, were decisive precisely
because authentication moves the response past the gate.

**The reusable rule: when probing for existence behind an auth gate, include a control that
certainly does not exist.** If the control returns what the target returns, the probe is
uninformative and must not be reported as confirmation.

## 4. What the reference actually is

Fetched 2026-08-28 by `data/acquire/fetch_kosurro_gold.sh` (now an actual fetch, because
open access removed the reason it could not be one).

| File | Content | Format |
|---|---|---|
| `id.deid` | 1,779 gold PHI locations over 735 records, framed by `Patient <pid>  Note <n>` | `start  start  end` — the start is written twice; the span is fields 1 and 3 |
| `id-phi.phrase` | the same 1,779 instances with types | `pid note start end type <phrase>` — whitespace-separated, **field 6 is the PHI phrase** |
| `shift.txt` | per-patient date shifts | not yet used |

The two files agree on all 1,779 spans, in the same order, per record. Offsets are
body-relative and **100% of them land inside their record body** — which is what makes the
parse trustworthy rather than merely plausible.

`id.deid` contains no text at all, so it is safe to print. `id-phi.phrase` field 6 is the one
genuinely dangerous field in the whole release, and `parse_reference` splits that file with
`maxsplit=5` so the phrase is never bound to a name. This replaced a print-time guard that
did not hold: the earlier parser scanned every field for something label-shaped, and a
one-word phrase — a surname — is label-shaped.

## 5. The parse is externally validated

The package ships `runStat.pl` and publishes its output against this same reference. Our
decomposition reproduces it:

| Quantity | Published | Measured here |
|---|---|---|
| False negatives | 59 | **59** |
| Recall | 0.967 | **0.967** |
| PPV / precision | 0.748 | 0.746 |

The recall and false-negative figures match exactly. Precision differs in the third decimal
because the denominators differ: `runStat.pl` counts 2,266 detected instances where `id.res`
carries 2,164 placeholders, since adjacent detections render as one placeholder. That same
merging is why one-to-one matching leaves 106 gold spans unmatched that a neighbour's
placeholder does cover; `check` reports them as a separate row rather than as misses, and the
two recall figures it prints bracket the truth at 0.907 and 0.967.

A parse that had guessed the column roles wrong could not land on 59 and 0.967.

## 6. What this establishes about `ko-surro`'s reference

The corpus's gold is the tool's placeholder set. That was already known and is unchanged. What
is new is that the gap is now **measured** rather than argued:

| Relation between the tool's 2,164 placeholders and the 1,779 gold spans | Count |
|---|---|
| matched one-to-one | 1,614 |
| gold span covered by a placeholder already matched to a neighbour | 106 |
| **placeholder with no gold span at all** | **550** |
| **gold span no placeholder covers** | **59** |

So the silver reference over-marks by 550 and misses 59. The disagreement is asymmetric by an
order of magnitude, and the direction matters for how the corpus can be used — see
`ko-surro-untyped-spans.md` §"Resolved" and DESIGN §6.6 §2.

## 7. What it does not establish

- **That the gold is a human annotation in the strict sense.** The release describes review
  by three or more independent experts and `id.deid` is the file the tool is *scored against*,
  which a tool's own output cannot be (546 false positives and 59 false negatives against
  itself is impossible). So it is independent of the tool output. Whether the experts
  annotated from scratch or reviewed a first pass is not stated, and this note does not assume.
- **That the offsets transfer to `ko-surro`.** They index `id.text`, which is English. The
  Korean corpus was built from `id.res`, so a gold span the tool never tagged has no
  counterpart position in the Korean text at all. Using this reference to score a Korean
  arm needs a mapping that does not exist yet, and 59 spans have no image under it.

## 8. The producing project's own corrections, scored against this reference

Measured 2026-08-28. The producing project relabels 142 of the tool's month–day placeholders
as not-PHI. All 550 month–day placeholders join to this reference by record × placeholder
value multiplicity, **with 0 ambiguous and 550 of 550 joined**, so this is a census and not a
sample.

| 550 month–day placeholders | gold: not PHI | gold: PHI | total |
|---|---|---|---|
| relabelled not-PHI | **139** | **3** | 142 |
| kept as PHI | 37 | 371 | 408 |
| total | **176** | 374 | 550 |

| Quantity | Value |
|---|---|
| relabel precision | 139/142 = **0.979** |
| relabel recall | 139/176 = **0.790** |
| the human-adjudicated subset alone (101) | 101/101 = **1.000** |
| therefore the model-assisted subset (41) | 38/41 = **0.927** |
| over-tag prevalence, census | 176/550 = **32.0%** |

The last row is the one worth keeping: that project reports 32.7% with a 95% CI of
[25.5, 43.3] from a 50-item sample over the same 550, and the census lands 0.7 pp away.

**Two different 550s, and they must not be conflated.** §6's 550 is placeholders the gold does
not support, over all 2,164. This section's 550 is the month–day subset. §6's set contains
this section's 176; its other 374 are 359 type-name payloads and 15 value payloads of another
shape. The coincidence is arithmetically unrelated.

**What the 3 wrong relabels are, stated at their actual size.** They restore a placeholder's
inner value into the published corpus where the human reference says the span is a date. That
is a label-fidelity defect and not a disclosure: of the 550 month–day placeholders, **0** carry
an inner value that occurs anywhere in their own record's surrogate body, so the tool wrote
shifted values, and it did so over a corpus whose dates were already surrogates. Three spans
out of 2,016 carry the wrong label; no real date is published by it.

## 9. Open — whether this corpus can occupy DESIGN §7's English cell

**Not decided here.** The measurements above change the question's terms, so the options are
recorded with their grounds and no verdict. Two facts bound every option:

- A human reference now exists and is held, so "silver only" is no longer forced.
- Its offsets index the English `id.text`. `ko-surro` is Korean text derived from `id.res`.
  59 gold spans have no Korean counterpart at all, so the reference does not transfer to the
  Korean side without a mapping that does not exist (§7).

| Option | Ground for | Ground against |
|---|---|---|
| **A. The English pair scored against the human gold** — run the arm on `id.text` itself, score on `id.deid` | it is a human reference of the strength §7 assumes; 1,779 spans, expert-adjudicated, and our parse is externally validated. Comparable with `es-meddocan` and GraSCCo on the same footing | the 2,434-note release cannot iterate: δ ≈ 6.4 pp, so only a one-shot arm fits. `id.text` is DUA-restricted where the reference is open, so the two halves of the cell have different access terms |
| **B. `ko-surro` scored against its silver reference, as before** | it is the note-type-matched Korean side of the axis-1 contrast, which is what §7 wants the cell for | its reference has precision 0.746 and recall 0.907–0.967 against human gold. A leak rate on it is not on the same scale as one measured against `es-meddocan`'s gold, and the headline metric of this project is leak rate |
| **C. Both, as a pair, with the gap reported as the calibration** | the gap is now measured rather than argued, so the silver-scored number can be published with a known bias direction: the reference over-marks by 550 and misses 59 | two cells for one axis position costs a run each, and the calibration is measured on English while the claim it would license is about Korean |
| **D. Neither — the English cell stays a projection** | §7.1 already writes the English rows as a projection and names a second Spanish register, not an English corpus, as the acquisition that closes the section | it leaves the axis-1 contrast with one operative end, which is the weakness §7.1 already concedes |

The decision belongs in DESIGN §7 and is pre-registered there before any arm runs, not
chosen after a number is visible.

## 10. A third option for decision 5: human-verified silver (2026-09-22)

Decision 5 is *which span set the Korean fold counts*. §9 recorded two candidates — the
silver reference the corpus ships with, and the human reference of `id.deid`. A third was
asked for on 2026-09-22: **keep only those Korean silver spans whose source English
placeholder is supported by the human gold.** This section measures whether that set can be
constructed, how large it is, and what it cannot see. It does not choose.

### 10.1 Can a Korean surrogate be traced back to its source English mask?

Asked and answered **from key names alone**, no file body opened for the judgement. The
counts in §10.2 then come from programmatic joins whose output is integers only.

| file | keys | what the keys carry |
|---|---|---|
| `ko_surrogate.jsonl` · `ko_placeholder.jsonl` | record: `uid`, `spans`; span: `start`, `end`, `type`, `src_tag`, `surrogate` | per span, the source placeholder **literal** (`src_tag`) and the normalised type |
| `surrogate_registry.jsonl` | `instance`, `normalized_key`, `note_uids`, `scope`, `scope_key`, `src_tags_seen`, `surrogate` | per *surrogate value*, the source identity at a scope, and the set of notes it appears in |
| `ko_tagged.jsonl` | `ko`, `uid` | no span structure |

So the link exists at **identity and type level** and is absent at **instance level**:

- `src_tag` names the source placeholder, so every Korean span knows *what kind of mask*
  it replaced, and `surrogate_registry` knows *which source identity* a surrogate stands for
  across notes (`note_uids`, `scope_key`).
- **No key anywhere is a source offset.** There is no `src_start`, `src_end` or `res_offset`
  in any of the five files. Nothing records *which occurrence* of a repeated placeholder in
  `id.res` a given Korean span came from.
- The registry is keyed by surrogate *value*, not by occurrence: grouping the Korean spans by
  `(uid, src_tag)` and by `(uid, src_tag, surrogate)` yields identical group counts (1,967)
  and identical size histograms. Within a note the surrogate is a function of the literal, so
  it adds no disambiguation the literal did not already give.

### 10.2 The instance join can be reconstructed anyway, and the count is exact

`id.res`'s placeholders are ordered; so are the Korean spans. Comparing the two sequences
per record (`gold_provenance_check.align` indexes placeholders by `tag_index`; Korean spans
sorted by `start`):

| relation, per record | records | Korean spans |
|---|---|---|
| literal sequences identical, position for position | 2,402 | 1,962 |
| Korean an ordered subsequence of the source | 6 | — (drops exactly the 6 `gold_excluded_spans`) |
| same multiset, reordered by translation | 26 | 186 |

In the 26 reordered records, 140 of the 186 spans are pinned by literal uniqueness and 46
(2.13% of all spans) are not. **All 21 repeated-literal groups containing those 46 carry a
uniform verdict** — every candidate in the group is `matched`, or every candidate is
`unsupported` — so no span's verdict depends on which assignment is chosen. And because the
within-record literal multisets are equal everywhere, the *count* was already exact before
that check.

### 10.3 The size of the set

| | spans |
|---|---|
| `id.res` placeholders | 2,164 |
| less `gold_excluded_spans` (all 6 `unsupported`) | −6 |
| Korean silver spans | **2,158** |
| of which gold-**supported** | **1,614** |
| of which gold-**unsupported** | 544 |
| gold-supported minus the 3 `NOT_PHI_RESTORED` relabels (§8) | 1,611 |

1,614 is the §6 `matched` count unchanged: the 6 dropped placeholders were all
over-marks, so the exclusion cost the human-verified set nothing. Beside the alternatives —
2,158 silver spans, 2,016 PHI-labelled silver, 1,779 human gold spans.

By construction the set has **precision 1.000** against the human gold. Its recall is
1,614/1,779 = **0.907** one-to-one, or 1,720/1,779 = **0.967** counting the 106 gold spans a
neighbour's placeholder also covers.

### 10.4 The limitation, which is the reason not to read precision 1.000 as "clean"

**59 gold spans — 3.3% — have no placeholder at all.** The tool never tagged them, so
`id.res` carries their source text unmasked, and the Korean corpus was built from `id.res`.
That PHI was therefore **translated into Korean with no marking of any kind**. It is not a
span the reference omits; it is a span that exists in the published Korean text and that no
Korean-side reference can point at.

Two consequences, both independent of which option decision 5 takes:

- Any arm scored against this set is charged a **false positive** for correctly detecting one
  of those 59. The number is a floor, not an estimate: 3.3% of the human gold, 2.7% of the
  1,614 + 59 that a perfect Korean detector would find.
- The floor cannot be lowered by filtering, because filtering only removes spans. It can be
  lowered only by annotating the Korean text, which is the annotation this project is built
  to avoid.

### 10.5 The three options, compared

| | **Silver as shipped** (2,158, or 2,016 PHI-labelled) | **Human gold** (1,779) | **Human-verified silver** (1,614) |
|---|---|---|---|
| where the spans are | Korean text, offsets valid | English `id.text`, offsets do not transfer | Korean text, offsets valid |
| precision vs human gold | 0.746 | — (it *is* the reference) | 1.000 by construction |
| recall vs human gold | 0.907 / 0.967 | — | 0.907 / 0.967 (identical: filtering removes no true positive) |
| unmarked PHI in the scored text | 59 spans (3.3%) | 0 | 59 spans (3.3%) |
| for | the corpus as published; no construction step; largest denominator, so per-type cells stay above `SPARSE_MAX` in more types | a human reference of the strength §7 assumes, expert-adjudicated, externally validated parse | removes 544 over-marks, so a leak rate on it is on the same scale as one measured against `es-meddocan`'s gold |
| against | 544 over-marks are charged as misses against any arm that correctly ignores them; a leak rate on it is not comparable across corpora | it scores the **English** cell, not the Korean one — §7's axis-1 contrast needs the Korean side | 25% smaller denominator, so more per-type cells fall sparse; needs a construction step nobody has written and a derived artefact to version; the 3.3% floor survives intact, so precision 1.000 is against the reference and not against the text |
| what it does not fix | — | — | the 3.3%. All three share it, because all three inherit `id.res` |

**No conclusion here.** Decision 5 belongs in DESIGN (§6.5 / §6.6) and is pre-registered
there before any Korean arm runs.

### 10.6 Decision 5 comes before the other four

The remaining `ko-surro` decisions — the 26-type map onto §9.0's canonical set, the loader,
the third `SPLIT_ORIGIN` route, and the prepare/seal step — all depend on which span set is
counted, because that set is the scoring basis and the loader is what produces it. They are
held until 5 is decided rather than worked in parallel.

### 10.7 A disclosure, recorded because the rule is about messages and not about intent

While distinguishing `type` from `src_tag` on 2026-09-22 I printed **eight `src_tag`
literals** to the terminal. One of them was a *value* payload — real source date content
from `id.res`. CLAUDE.md forbids corpus text in messages, logs and warnings, and a terminal
transcript is exactly the path that rule names. Nothing of the kind reached any file in this
repository or the report; every subsequent measurement in this section printed integers only.

The design consequence outlives the slip: **`src_tag` is text-bearing.** 30.5% of placeholder
payloads are values rather than type names (1,125 distinct `src_tag` values over 2,158 spans;
`type == src_tag` in 0 of them). So any loader, prepare tool or split file that handles
`ko-surro` must treat `src_tag` as corpus text — never in an exception message, never in a
log line, and never written into `splits/ko-surro.json`. `type`, the normalised 26-value
field, is the one safe to name.

## 11. The build, 2026-09-22: the join, the filter, and what each stage counted

Sections 1–10 argue what the reference is. This section records what was built on it and what
each stage measured, so that a rebuild can be checked rather than trusted. Every number here
was produced by running the tool named beside it; none is derived from another.

**The join is exact and it is not positional.** `tools/prepare_kosurro.py stage` pairs a
Korean surrogate span with the English placeholder it was injected from, per record: spans
sorted by `start`, each claiming the first unclaimed `id.res` placeholder whose normalised
payload matches (`[**payload**]` or `[**payload **]`, stripped). Position alone would be
wrong — translation reorders the placeholders in **26** records and drops one in **6** — and
the payload alone would be ambiguous within a record, so it is the two together. `MIN_LAND =
0.95` guards the reference parse, and a record where the greedy pass fails to consume every
span is a refusal rather than a partial join. There were **0** refusals over the whole corpus.

| stage | what it counted | value |
|---|---|---|
| `stage` | records written | 2,434 |
| | records with no English reference header (§9.0's absence-is-not-emptiness) | 9 |
| | silver spans | 2,158 |
| | placeholders in `id.res` available to claim | 2,164 |
| | spans the human reference supports → **gold** | **1,614** |
| | spans it does not support → dropped by the loader, counted | 544 |
| | join refusals | 0 |
| `load` | documents | 2,425 |
| | spans loaded | 1,614 |
| | in scope after §9.1's mechanism | **1,611** |
| | excluded and flagged (`NOT_PHI_RESTORED`) | 3 |
| | `assert_offsets()` failures | 0 |
| `split` | train / dev / test documents | 1,456 / 485 / 484 |
| | patient groups, and groups crossing a fold | 163, **0** |

The per-type table for the 1,611 is in DESIGN §9.0 and is asserted in
`tests/test_kosurro_loader.py` rather than only written down: NAME 806, DATE 468,
ORGANISATION 242, LOCATION_AREA 45, CONTACT 44, AGE 3, LOCATION_STREET 2, ID 1. Two rows
carry a caveat that belongs with the reference rather than with the loader. **ORGANISATION
242 against 0 on the English side** is a vocabulary difference and not a disagreement: the
producing tool separates hospital, ward and company where the human reference has one
`Location`, and they are largely the same physical spans, which is why §7's location and
organisation rows are not comparable in either direction for this pair. And the single **ID**
is a span the human reference types `Date`; the row exists for exhaustiveness and carries no
claim.

**What the loader refuses, and why §10.7 is enforced rather than remembered.** The derived
root's schema is closed — exactly six record keys and five span keys — and `src_tag` and
`surrogate` are refused by name with a message that says *corpus text* and quotes neither. A
closed schema rather than a minimum is the point: the two forbidden keys are keys of the
source files, so a loader that read what it wanted and ignored the rest would read a root
that still carried them without anyone noticing. `tests/test_kosurro_loader.py` asserts that
no refusal in the module quotes a surface or a placeholder literal, which is the form
`tests/test_meddocan_loader.py` uses for the same rule.

**Two defects the tests found while they were being written**, recorded because both would
have been invisible in every number the corpus produces. The per-record count of denied spans
was taken from a corpus-wide running total, and it is part of each record's digest — so every
document's digest would have depended on every earlier document and file order would have
entered `splits/ko-surro.json` with nothing saying so. And each root's `reference.json` was
checked against the corpus-wide uncovered list rather than that root's own count, which would
have made a sealed evaluation refuse — *after* `results/sealed_eval_log.md` recorded the
access — for having found exactly the records the seal put there. Both are now mutations
(`kosurro_denied_count_is_a_running_total`, `kosurro_reference_counts_corpus_wide`), because a
fixed defect with no anchor is a defect that returns.
