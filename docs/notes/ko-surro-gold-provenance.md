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
