# MIMIC-III acquisition — prepared 2026-08-27, **not applied for**

The route is chosen: MIMIC-III NOTEEVENTS, for the nursing-note subset. This note is the
preparation, and it is now complete. **No application has been submitted.** The order is
that DESIGN §6.5 and §6.6 are committed first and the application follows, because the
application's own description has to state what those sections decided.

Nothing here was obtained by downloading data. Sources are the public PhysioNet and
`mimic.mit.edu` pages, the public `deid` v1.1 user manual, and this repository's own
measured records.

---

## 1. What the application needs, and what this project already holds

PhysioNet gates MIMIC-III in two stages, and only the second is specific to it.

| Stage | Requirement | State here |
|---|---|---|
| Credentialing (once per person) | CITI *Data or Specimens Only Research* + *Conflicts of Interest*, taken through the MIT Affiliates portal; the training **report** is uploaded, not the completion certificate; a supervisor reference is required for students and postdocs, and the page says explicitly "Do not list yourself as reference" | **held** — `es-carmen` was acquired under PhysioNet credentialed access on 2026-08-06 (`data/README.md`) |
| Per-project DUA | sign the data use agreement on the MIMIC-III project page | **not done** |

**So the likely remaining step is the DUA signature alone. That is an inference and it
needs one check before it is relied on.** The evidence is that a credentialed download
already succeeded in this project four weeks ago; what is *not* verified is whether the
CITI training's completion date is still inside whatever currency window PhysioNet
applies, and whether MIMIC-III's page asks for anything CARMEN-I's did not. Both are
visible on the project page while logged in, which is a thirty-second check and is worth
doing before assuming the fast path.

**Approval time has no published SLA.** The getting-started page says: "Approval may take
several business days, and will be delayed if your request is missing any required
information." That sentence covers credentialing. A per-project DUA on an
already-credentialed account is usually immediate, which is again an inference from the
same 2026-08-06 precedent and not something the page states.

### What the application has to say, given §6.5 and §6.6

Three things belong in the stated intended use, and all three are consequences of
decisions already committed rather than descriptions of intent that could later drift.

| Item | Why it is in the application and not only in the repo |
|---|---|
| The nursing-note subset is the target, and all 2,434 notes of the PhysioNet de-identified nursing-note release are excluded from it | §6.5's D. It is a use restriction the project imposes on itself, and stating it is how the restriction becomes checkable by the data holder rather than only by us |
| A Korean surrogate corpus derived from the same source release is used in the same study | the shared parentage is the reason the exclusion exists. Declaring the relationship is more honest than declaring the exclusion without its cause, and PhysioNet is the party best placed to say if link 2 is establishable |
| Note text is sent to Amazon Bedrock under PhysioNet's own 2023-04-18 guidance on online services | `docs/notes/compliance.md`. The guidance is written about MIMIC-III/IV/CXR and names Bedrock acceptable, so this is a declaration of conformance and not a request for an exception |

**Compliance carries over but one check does not.** `docs/notes/compliance.md` already
argues the Bedrock path for MIMIC-derived data. What does not carry over is the
invocation-logging check: it was measured `None` across six regions on 2026-08-06, and
that section requires re-running it and **appending** the date and result before any arm
that sends credentialed text. That re-run is owed, and it is owed after acquisition, not
before.

**A corpus ID has to exist first.** `config/naming.yaml` declares `en-n2c2` as the only
English corpus, and CLAUDE.md forbids hardcoding a new one in code. The ID, the
`corpus_rule_langs` entry and the `mappings/{corpus}.yaml` type mapping are all
prerequisites to the first load, not to the application.

---

## 2. Document and span counts, and whether δ holds

This is the question that eliminated the other candidate: route (b), the PhysioNet source
release on its own, fails δ by 2×–9×. So it is asked first here.

**Verified:** NOTEEVENTS holds **2,083,180 rows** in total. **Not verified:** the exact
row count for the nursing categories. Three attempts at that number from public sources
failed, and the open demo ships NOTEEVENTS as a header-only 95-byte file, so it cannot be
counted without access. What follows is therefore a *threshold*, not an estimate — the
number the subset has to clear, so that the first post-access measurement has something
to be compared against.

**The δ arithmetic.** DESIGN §3: `δ = max(0.005, 26 / n_dev)` over
`folds.dev.n_spans_in_scope`. The floor binds at **n_dev ≥ 5,200 in-scope spans**, which
is where `es-meddocan` sits (5,254 → δ = 0.50 pp).

| Quantity | Value |
|---|---|
| PHI spans per nursing note (measured on the source release: ~1,779 over 2,434 notes) | ≈ 0.73 |
| In-scope fraction of gold spans (`splits/es-meddocan.json`: 5,254 of 5,801) | ≈ 0.91 |
| In-scope spans per note | ≈ 0.66 |
| Dev notes needed for δ at the floor | ≈ **7,900** |
| Corpus notes needed at a 25% dev fraction | ≈ **31,400**, i.e. 1.5% of the note table |

**So the subset must clear roughly 31,000 notes.** That is a low bar and the nursing
subset is very likely well past it, but "very likely" is what route (b) also looked like
before the arithmetic was done, so the count is the **first measurement after access** and
is recorded as owed rather than assumed. Subtract the 2,434 excluded by §6.5's D before
comparing — a subtraction that is negligible at this scale but should be visible in the
recorded figure rather than folded into it.

**The inverse finding, which matters more than the threshold.** If the subset is large, δ
stops being a constraint and becomes a *choice*, and that is a hazard rather than a
relief: `n_dev` is a free parameter when the corpus is 30× larger than the fold needs to
be, δ is derived from `n_dev`, and δ is the termination threshold. A dev subsample chosen
after the first round's gain is visible is a stopping rule chosen after the fact.

**This is why the sampling procedure is pre-registered rather than the size.** DESIGN §6.6
holds the rule: patient-disjoint groups, 50/25/25, groups added in seeded random order
until `n_spans_in_scope` first reaches 5,300, δ computed from the result. The target puts δ
on `es-meddocan`'s floor so the two corpora terminate against the same threshold. The
document count is an output.

**What actually binds is per-round cost, and it binds hard.** From `port-loop` on
`es-meddocan`, measured: 250 dev documents, 251 LLM calls in the final round, 1,758 calls
and 16.5 M prompt tokens and 7,730 s wall over the whole arm. Calls run at almost exactly
one per dev document, so scaling to ≈7,900 dev documents is close to linear:

| Scaled to ≈7,900 dev documents (derived, not measured) | Value |
|---|---|
| One round | ≈ 7,900 calls, ≈ 74 M prompt tokens, ≈ 9.8 h wall |
| An eight-round arm | ≈ 63,000 calls, ≈ 0.6 B prompt tokens, ≈ 78 h wall |

An upper bound in one respect: a minority of each round's calls are per-round rather than
per-document and do not scale. Even discounted, this is the real constraint on the arm,
and it means the corpus's size buys δ headroom that cannot be spent in full.

---

## 3. DATE — decided on what the method can see

The rule and its grounds are DESIGN §6.6 §2. What belongs here is the evidence behind it
and the one question that had to be checked before it could be written.

**The criterion is the marker, not the difficulty.** Annotation-free supervision recovers
labels from placeholder positions. A date that was shifted rather than bracketed carries
no placeholder, so there is nothing to recover a label from — it is not a hard instance,
it is an instance this method cannot see. Bracketed typed DATE masks are gold; shifted
dates are outside the reference set; the share is reported as a limitation.

**What the documentation says, read 2026-08-27.**

| Source | Statement |
|---|---|
| PhysioNet MIMIC-III project page | "dates were shifted into the future by a random offset for each individual patient in a consistent manner" to "preserve intervals"; "Time of day, day of the week, and approximate seasonality were conserved" |
| v1.4 release notes, same page | "The date shift for dates in the text field of noteevents has been corrected" and "It now matches the dates in the structured data" |
| `mimic.mit.edu` time-types page | "Year - The year is randomly distributed between 2100 - 2200" |
| `deid` v1.1 manual | the tool "replaces dates with shifted dates, and other PHI with their PHI types" |
| Observed MIMIC tag vocabulary (public preprocessing code) | 39 distinct typed labels, including year, month, day, date-range and holiday |

Two conclusions. The release note is decisive on the mixture existing: dates in the
noteevents **text field** are shifted, explicitly, while the observed tag vocabulary shows
some dates are also bracketed. And the year range makes the shifted ones fingerprintable
where they carry a year.

**The proportion is knowable only after access, and that is recorded so the rule cannot be
selected by the number.** None of the three pages above gives any count or proportion of
dates shifted versus bracketed. The rule is fixed now; the proportion is measured later
and reported whatever it is. This is the same discipline §6.4 applies at the seal, one
stage earlier.

**The fingerprint measures the limitation and does not produce gold** (§6.6). A reference
produced by a pattern rule is the same kind of object as the predictions being scored, so
using the year-range fingerprint as gold would evaluate rules against rule-generated gold.
Permitted for the limitation figure, barred from the reference set, and the split holds
however well the fingerprint works.

**Two corrections to the first draft of this note.**

- The first draft said DATE spans would be marked `excluded` at load and worried about
  `naming.yaml`'s `excluded_types` being a global list. Both are void. §9.1's exclusion
  flags a span and keeps it; a shifted date has **no span to flag**. The reference is
  incomplete, which is a different fact from some spans being excluded, and it needs no
  vocabulary change: DATE stays a `phi_type` value for every corpus and cross-corpus
  scoring stays over the same ten types.
- What survives from that worry, in a different form: **DATE precision here is a lower
  bound** — a detector that correctly finds a shifted date scores as a false positive
  because no gold span exists there — and **DATE recall is over a reference known to be
  incomplete**, so the per-type DATE comparison against the other corpora is not a
  like-for-like comparison and is reported with that stated.

**One measured figure, as evidence and not as ground.** On `es-meddocan` dev, removing
DATE moves the aggregate leak rate 13.72% → 15.28% — 1.56 pp, about 3× δ. That is why
this decision cannot be a footnote. It is not why it went this way.

---

## 4. Order of operations

1. **Commit DESIGN §6.5 and §6.6.** Done before the application, because the application's
   stated use has to say what they decided.
2. **Apply.** Check CITI currency and MIMIC-III's own page requirements while logged in,
   then sign the DUA, with the three declarations of §1 in the stated use.
3. Add the corpus ID, `corpus_rule_langs` entry and type mapping to `config/naming.yaml`
   and `mappings/`.
4. **Check link 2** — whether the 2,434 source-release notes are locatable in MIMIC-III by
   identifier. Before the fold is sampled, so that a retreat to §6.5's C is chosen before
   anything depends on it, and is reported as a retreat.
5. Measure the nursing subset's document and span counts against §2's ≈31,000 threshold,
   net of the 2,434.
6. Measure the shifted-versus-bracketed date proportion with the year-range fingerprint and
   record it as the limitation figure. The reporting form is already fixed.
7. Re-run the six-region invocation-logging check and append to `docs/notes/compliance.md`
   before any arm sends credentialed text.
8. Sample by §6.6's procedure, generate `splits/{corpus}.json`, seal. Everything in steps
   1–4 is frozen by now; after this step the remaining numbers are measurements.
