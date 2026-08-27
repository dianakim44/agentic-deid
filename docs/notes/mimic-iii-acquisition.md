# MIMIC-III acquisition — prepared 2026-08-27, **not applied for**

The route is chosen: MIMIC-III NOTEEVENTS, for the nursing-note subset. This note is the
preparation. **No application has been submitted and none should be until DESIGN §6.5 is
decided**, because the cross-corpus seal decision can change what is applied for — option
C there narrows the request to non-nursing categories, which is a different corpus.

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

**Compliance carries over but one check does not.** `docs/notes/compliance.md` already
argues the Bedrock path for MIMIC-derived data, and PhysioNet's own 2023-04-18 guidance
on online services is written about MIMIC-III/IV/CXR, so it applies directly rather than
by analogy — Bedrock is named acceptable there. What does not carry over is the
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

| Quantity | Value | Where from |
|---|---|---|
| PHI spans per nursing note | ≈ 0.73 | measured on the source release: ~1,779 reference PHI over 2,434 notes |
| In-scope fraction of gold spans | ≈ 0.91 | `splits/es-meddocan.json`: 5,254 of 5,801 |
| In-scope spans per note | ≈ 0.66 | the two above |
| Dev notes needed for δ at the floor | ≈ **7,900** | 5,200 ÷ 0.66 |
| Corpus notes needed at a 25% dev fraction | ≈ **31,400** | `es-meddocan`'s 50/25/25 shape |

**So the subset must clear roughly 31,000 notes, i.e. about 1.5% of the note table.** That
is a low bar and the nursing subset is very likely well past it, but "very likely" is what
route (b) also looked like before the arithmetic was done, so the count is the **first
measurement after access** and is recorded as owed rather than assumed.

**The inverse finding, which matters more than the threshold.** If the subset is large,
δ stops being a constraint and becomes a *choice* — and that is a hazard, not a relief.
`n_dev` is a free parameter when the corpus is 30× larger than the fold needs to be, and δ
is derived from `n_dev`, and δ is the termination threshold. A dev subsample chosen after
the first round's gain is visible is a stopping rule chosen after the fact, which is
exactly what DESIGN §3 and §6.4 exist to prevent, arrived at from the other direction.

**Therefore the dev subsample size is pre-registered before it is sampled.** Two
candidate forms, both stated now so that neither can be selected later on the strength of
a result:

- **Match `es-meddocan`**: sample to n_dev ≈ 5,200–5,300 in-scope spans, putting δ at the
  floor and making the two corpora's termination thresholds identical. Comparability
  across corpora is the argument.
- **Fix the document count** at a round number and let n_dev fall where it falls, then
  report δ as computed. Simpler to state, but produces a δ that differs from
  `es-meddocan`'s and makes per-corpus stopping behaviour differ for a reason unrelated to
  the porting.

The first is preferable and the choice is not made here.

**What actually binds is per-round cost, and it binds hard.** From `port-loop` on
`es-meddocan`, measured: 250 dev documents, 251 LLM calls in the final round, 1,758 calls
and 16.5 M prompt tokens and 7,730 s wall over the whole arm. Calls run at almost exactly
one per dev document, so scaling to ≈7,900 dev documents is close to linear:

| Scaled to ≈7,900 dev documents | Derived value |
|---|---|
| One round | ≈ 7,900 calls, ≈ 74 M prompt tokens, ≈ 9.8 h wall |
| An eight-round arm | ≈ 63,000 calls, ≈ 0.6 B prompt tokens, ≈ 78 h wall |

Derived, not measured, and an upper bound in one respect: a minority of each round's calls
are per-round rather than per-document and do not scale. Even discounted, this is the real
constraint on the arm, and it means the corpus's size buys δ headroom that cannot be spent
in full.

---

## 3. DATE — tagged in some places, shifted in others

**The problem, from two public sources.** The `deid` v1.1 manual states that the tool
"replaces dates with shifted dates, and other PHI with their PHI types" — dates are moved,
not marked. But the tag vocabulary observed in MIMIC preprocessing code contains 39
distinct typed labels including year, month, day, date-range and holiday, so MIMIC's
variant *does* bracket some dates. Both cannot be true of every date, so the corpus mixes
two treatments of one type.

**Why that specifically corrupts leak rate, and in the dangerous direction.**
Annotation-free supervision recovers references from placeholder positions. A shifted date
left unbracketed in the text is a real-looking date with no placeholder, so it is not in
the reference set at all. It is not a hard case; it is invisible. Consequences, in the
metric definitions of DESIGN §9.3:

| Effect | Direction |
|---|---|
| A detector misses an unbracketed shifted date | not counted as a leak — **leak rate biased low** |
| A detector finds one | counted as a false positive — **precision biased low** |
| Reported DATE recall | computed over a reference that omits an unknown share of the type |

Leak rate is the headline (CLAUDE.md), and the bias is optimistic. MIMIC's shifting is
also patient-consistent — offsets preserve intervals — so a shifted date is
indistinguishable from an unshifted one by surface form, and no detector-side heuristic
recovers the distinction.

**How much this can move, measured on a corpus where DATE is clean.** On `es-meddocan`
dev, DATE is 724 of 5,254 in-scope gold spans (13.8% of the leak-rate denominator) and
contributes 29 of 721 leaks (4.0% of the numerator) — a large, well-detected type.
Removing it moves the aggregate leak rate from 13.72% to 15.28%, i.e. **1.56 pp, about 3×
δ**. So whichever way DATE is handled, the handling is worth several times the
termination threshold and cannot be a footnote.

**The decisive measurement, and the reporting form each answer implies.** Both are
pre-registered here so that the answer does not select the reporting.

Measurement **M**, first thing after access, on the dev fold only: count date-like surface
forms and the share of them falling inside a bracketed tag. Call the unbracketed share
`u`. Report `u` regardless of outcome.

| If | Then |
|---|---|
| `u` ≈ 0 | DATE is in scope and reported like any other type. No exclusion, no separate section. |
| `u` is material | DATE spans in this corpus are marked `excluded` at load, the corpus's headline leak rate is over nine types and says so, and `u` is **reported as a corpus property** — not omitted. The all-types figure is still given, labelled contaminated and unusable as a headline. |

Not an option: dropping DATE quietly, or reporting a DATE figure without `u` beside it.

**Two structural consequences that need settling before M is run, not after.**

- **The exclusion mechanism is per-span but the vocabulary is global.** `Span.excluded`
  is per-span, so a loader marking this corpus's DATE spans works and `n_spans_excluded`
  reports it. But `config/naming.yaml`'s `excluded_types` block is a single global list
  that the Auditor prompt reads, so adding DATE there would tell the Auditor to ignore
  dates in **every** corpus. There is currently no per-corpus exclusion vocabulary, and
  the two existing entries are excluded on cross-corpus principle (not a Safe Harbor
  identifier; incompatible annotation conventions) — neither ground applies to DATE, which
  *is* a Safe Harbor identifier. So a corpus-specific DATE exclusion needs a mechanism
  that does not exist yet.
- **Cross-corpus comparability breaks silently.** The `phi_type` axis exists so that
  cross-corpus scoring happens over one set of ten types. A leak rate over nine types for
  one corpus and ten for the others is not comparable, and nothing in the metric would say
  so. If DATE is excluded here, a DATE-excluded aggregate should be reported for **all**
  corpora alongside the ten-type figures, so that at least one comparable pair of columns
  exists.

---

## 4. Order of operations

1. **DESIGN §6.5 decided** — before the application, because option C changes what is
   requested.
2. Check CITI currency and MIMIC-III's own page requirements while logged in.
3. Sign the DUA. Add the corpus ID, `corpus_rule_langs` entry and type mapping to
   `config/naming.yaml` and `mappings/`.
4. Measure the nursing subset's document and span counts against §2's ≈31,000 threshold.
   Pre-register the dev subsample size **before sampling it**.
5. Run measurement M and record `u`. Apply §3's pre-registered reporting form.
6. Re-run the six-region invocation-logging check and append to
   `docs/notes/compliance.md` before any arm sends credentialed text.
7. Generate `splits/{corpus}.json` and seal — after 1, never before, since §6.5 option B
   expires at the first committed split file.
