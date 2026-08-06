# Corpus observations — es-meddocan and de-grascco

Written while doing the `port-human` inventory (2026-08-05). Counts come from
`profiles/es-meddocan.raw.json` and `profiles/de-grascco.raw.json`.

**This note deliberately reaches no conclusions.** Options and evidence only. The
standard type set is the user's decision; the open questions are collected in §6.

---

## 1. The two type systems side by side

MEDDOCAN: 22 types, 22,795 spans over 1,000 documents.
GraSCCo: 20 types, 1,436 spans over 63 documents.

Coincidentally identical span density: **22.8 spans per document** in both.

| MEDDOCAN | n | GraSCCo | n | relationship |
|---|---|---|---|---|
| `NOMBRE_SUJETO_ASISTENCIA` | 2014 | `NAME_PATIENT` | 166 | same concept, different name |
| `NOMBRE_PERSONAL_SANITARIO` | 1998 | `NAME_DOCTOR` | 154 | same concept |
| `FAMILIARES_SUJETO_ASISTENCIA` | 416 | `NAME_RELATIVE` | 1 | **same name, different meaning** — see §2.1 |
| `FECHAS` | 2566 | `DATE` + `DATE_BIRTH` | 632 + 61 | MEDDOCAN does not separate birth dates |
| `CALLE` | 1709 | `LOCATION_STREET` | 36 | same concept |
| `TERRITORIO` | 3818 | `LOCATION_CITY` + `LOCATION_ZIP` | 59 + 38 | **one type vs two** — see §2.2 |
| `PAIS` | 1423 | `LOCATION_COUNTRY` | 2 | same concept |
| `HOSPITAL` | 525 | `LOCATION_HOSPITAL` | 35 | same concept, different parent |
| `INSTITUCION` + `CENTRO_SALUD` | 237 + 14 | `LOCATION_ORGANIZATION` | 2 | 2→1 |
| `CORREO_ELECTRONICO` | 959 | `CONTACT_EMAIL` | 1 | same concept |
| `NUMERO_TELEFONO` | 109 | `CONTACT_PHONE` | 19 | same concept |
| `NUMERO_FAX` | 28 | `CONTACT_FAX` | 8 | same concept |
| `EDAD_SUJETO_ASISTENCIA` | 2074 | `AGE` | 19 | same concept |
| `PROFESION` | 37 | `PROFESSION` | 2 | same concept |
| `ID_SUJETO_ASISTENCIA` | 1142 | `ID` | 59 | **4 subtypes vs 1** — see §2.3 |
| `ID_ASEGURAMIENTO` | 783 | — | | no counterpart |
| `ID_TITULACION_PERSONAL_SANITARIO` | 931 | — | | no counterpart |
| `ID_CONTACTO_ASISTENCIAL` | 148 | — | | no counterpart |
| `ID_EMPLEO_PERSONAL_SANITARIO` | 1 | — | | no counterpart, n=1 |
| `SEXO_SUJETO_ASISTENCIA` | 1841 | — | | **no counterpart at all** — see §2.4 |
| `OTROS_SUJETO_ASISTENCIA` | 22 | — | | catch-all, see §2.5 |
| — | | `NAME_TITLE` | 139 | **no counterpart** — see §2.6 |
| — | | `NAME_USERNAME` | 2 | no counterpart |
| — | | `NAME_EXT` | 1 | no counterpart, meaning unclear |

MEDDOCAN additionally ships a **two-level** view in its XML format that brat
flattens: coarse element (`NAME`, `LOCATION`, `ID`, `DATE`, `CONTACT`, `AGE`,
`OTHER`, `PROFESSION`) plus the fine `TYPE` attribute. GraSCCo encodes its two
levels *inside one string* (`NAME_PATIENT` = `NAME` + `PATIENT`). So both corpora
are two-level; only the encoding differs. That is convenient and probably not a
coincidence — both descend from i2b2/HIPAA-era category lists.

## 2. Where merging into one standard set gets ambiguous

### 2.1 `FAMILIARES_SUJETO_ASISTENCIA` vs `NAME_RELATIVE` — a false friend

These look like the same type and are not.

- GraSCCo `NAME_RELATIVE` (n=1): the *name* of a relative.
- MEDDOCAN `FAMILIARES_SUJETO_ASISTENCIA` (n=416): the *relationship word*, not a
  name. Actual surfaces: `madre` (38), `familia` (36), `padres` (31), `padre` (19),
  `hijo` (13), `hermano` (10).

MEDDOCAN is annotating "mother" as PHI. Under HIPAA that is not an identifier at
all. Options:

- **(a)** map `FAMILIARES_*` to a standard `RELATIVE_MENTION` type and keep it out
  of the leak-rate denominator, since a rule that must find the word "madre" is
  measuring something different from disclosure risk;
- **(b)** map it to `NAME` and accept that 416 spans of common nouns will dominate
  Spanish name recall;
- **(c)** drop the type from the canonical set and record it as corpus-specific.

This choice moves the Spanish numbers materially: 416 of 22,795 spans is 1.8%.

### 2.2 `TERRITORIO` mixes city and postcode

`TERRITORIO` (3,818 spans, the single largest MEDDOCAN type) contains both place
names and postcodes: `Madrid` (430), `Barcelona` (65), but also `28029` (52),
`31008` (31). **1,226 of 2,862 sampled `TERRITORIO` spans are purely numeric.**

GraSCCo separates `LOCATION_CITY` from `LOCATION_ZIP`.

If the canonical set keeps them separate (following GraSCCo), the MEDDOCAN gold
has to be split by a heuristic — and a heuristic applied to gold labels is no
longer gold. If the canonical set merges them (following MEDDOCAN), GraSCCo's
distinction is discarded and a checksum-style rule for postcodes cannot be
scored separately. Options:

- **(a)** canonical `LOCATION_CITY` + `LOCATION_ZIP`, and derive MEDDOCAN's split
  mechanically (`^\d{5}$` → ZIP), documenting it as a mapping decision with a
  measured error rate;
- **(b)** canonical single `LOCATION_AREA`, losing granularity in both;
- **(c)** keep both granularities and evaluate at the coarse level only.

### 2.3 ID granularity: 4 vs 1

MEDDOCAN distinguishes patient record number, insurance number, physician licence
number, and contact number. GraSCCo has one flat `ID`.

Worse, GraSCCo's `ID` surfaces are not all identifier-shaped: `'II'` (3),
`'B'` (3), `'119'` (3), `'A31'` (2), `'O-11'` (2), `'3 Süd'` (1) — these look like
ward or room designations. A single-character `'B'` cannot be found by any rule
without context, and will hurt precision if a rule tries.

Option: canonical coarse `ID` with an optional subtype field that MEDDOCAN
populates and GraSCCo leaves null — subtypes then only participate in per-corpus
analysis, never in the cross-corpus leak rate.

### 2.4 `SEXO_SUJETO_ASISTENCIA` — 1,841 spans with no counterpart

MEDDOCAN annotates sex as PHI: `H` (410), `M` (284), `Varón` (201), `Mujer` (157),
`varón` (119), plus `Niña`/`niño`/`niña` (74) and `Hombre` (16). GraSCCo does not
annotate sex at all.

This is 8.1% of all MEDDOCAN spans. Under HIPAA Safe Harbor, sex is not one of the
18 identifiers. Including it means Spanish recall is partly a measurement of
finding the single letter `H`, which any rule can do and which tells us nothing
about porting difficulty. Excluding it means the Spanish leak rate is computed on
a different denominator than MEDDOCAN's own published numbers, so our figures will
not be comparable to the shared-task leaderboard.

Both choices are defensible; they cannot both be made. **This is the single
largest decision in the mapping.**

### 2.5 `OTROS_SUJETO_ASISTENCIA` is unlearnable as a type

All 22 spans, verbatim: `síndrome de Down` (2), `materno` (2), `negro`,
`raza negroide`, `raza mestiza`, `origen español`, `casada`, `abuelo materno`,
`tatuaje en la región pectoral izquierda`, `fusil de pesca submarina en su casa`,
`Fusil Automático Liviano -FAL`, `modelo PARA Nº 50-63`,
`tarjeta amarilla al Sistema Español de Farmacovigilancia`.

This is a residual bucket (ethnicity, marital status, a tattoo, a spearfishing
gun). No rule and no tagger will learn it from 22 examples. Options: keep it in
the denominator as an honest "irreducible" floor on leak rate, or exclude it and
say so. Either way it should never be a target for rule development.

### 2.6 `NAME_TITLE` — the boundary question, and it is decided differently

The example in the task prompt ("should DOCTOR sit under NAME") turns out to have
a sharper version here: **whether the title is part of the name span at all.**

- GraSCCo: `Dr.` is its own span, `kind=NAME_TITLE` (139 spans). Values include
  `Dr.` (34), `Dr. med.` (21), `Prof. Dr.` (12), `PD Dr.` (5), `Dr.med.` (3),
  `Univ.-Prof. Dr.`, `Doz. Dr.`, `Mag.`, `MBA`.
- MEDDOCAN: **0 of 1,000** sampled `NOMBRE_PERSONAL_SANITARIO` spans begin with
  `Dr`/`Dra`/`Prof`. Titles are excluded from the name span and are not annotated.

So a detector ported to German must emit a span the Spanish gold would count as a
false positive, and vice versa. Options:

- **(a)** canonical set has no `TITLE`; strip GraSCCo's 139 `NAME_TITLE` spans from
  gold (drops 9.7% of German spans, and note two of them are `MBA`/`Mag.` which
  are credentials, not honorifics);
- **(b)** canonical `NAME_TITLE` exists; MEDDOCAN simply has zero instances, and
  German precision is measured against it while Spanish is not — asymmetric but
  lossless;
- **(c)** treat title+name as one span in both, requiring re-annotation of GraSCCo
  boundaries (merging adjacent `NAME_TITLE`+`NAME_*` pairs), which changes gold.

On the original DOCTOR-under-NAME question: both corpora already answer it the
same way (`NOMBRE_PERSONAL_SANITARIO`/`NAME_DOCTOR` are distinct from the patient
name type), so a canonical `NAME` with a `role` attribute (patient / clinician /
relative) fits both without loss. That part looks safe.

## 3. Split unit

Facts, from the inventory:

- **Neither corpus has a patient identifier.** MEDDOCAN has no patient field; its
  documents are published clinical case reports, and the annotated patient names
  are synthetic replacements, so they cannot link documents. GraSCCo's
  `documentId` field is the constant string `CURATION_USER` (an INCEpTION curation
  artifact, not a key) — the filename is the only document identifier.
- MEDDOCAN document IDs are `{SciELO-article-id}-{n}`. **48 of 936 article stems
  carry more than one document**, i.e. multiple cases from the same journal
  article. (These figures were 47/906 in the first draft; the stem regex assumed
  digits after the leading `S####` and dropped 31 ids whose journal prefix
  contains a letter. Corrected — see §7.1.)
- GraSCCo filenames are surnames/eponyms. `Tupolev_1..4` (4 files) and
  `Colon_Fake_A..K` (11 files) share a stem.

CLAUDE.md requires patient-disjoint splits; DESIGN.md §6 softens this to "the
largest natural group available (patient where patients exist, document
otherwise)". Neither corpus has patients, so the operative question is what the
largest *defensible* group is.

Options for MEDDOCAN:

- **(a)** split by article stem (936 groups), keeping same-article cases together.
  Rationale: cases from one article share an author, an institution, formatting
  habits, and sometimes a surrogate name pool. Two cases from the same article in
  train and test is the closest thing to patient leakage this corpus can have.
- **(b)** split by document (1,000 groups). Simpler, and matches how the
  shared task itself was split — but see §4.

Cost of (a) is low: 48 stems are affected, so group-level and document-level
splits differ by ~6% of documents (80 documents, measured in §7.1).

Options for GraSCCo:

- **(a)** split by filename stem, keeping `Tupolev_*` and `Colon_Fake_*` together
  (48 groups instead of 63 documents). Rationale: the shared stem suggests a
  deliberate relationship — possibly the same fictional patient across visits, or
  systematic variants of one document. **Whether they are the same patient is not
  recorded in the data**; grouping them is the conservative reading.
  *Superseded by §7.3:* it is not recorded in the *filename*, but it is decidable
  from the annotations, and the two groups differ. `Tupolev_*` is one patient;
  `Colon_Fake_*` is 11 patients. Grouping the latter is not conservative, it is
  wrong.
- **(b)** split by document (63 groups), gaining ~24% more independent units,
  which matters a lot at n=63.

At n=63, a test fold of 20% is 12–13 documents and ~290 spans. Any per-type
figure for the 12 rare types (n ≤ 8 corpus-wide) will be zero or one instance.
This is a real limit on what German numbers can claim, independent of the split
unit.

## 4. Reuse the corpus split, or make our own?

MEDDOCAN ships train 500 / dev 250 / test 250 (measured; matches its
documentation) plus 3,751 unannotated background documents. GraSCCo ships **no
split at all** (63 flat files, verified — no manifest in either Zenodo record).

Arguments for reusing MEDDOCAN's split:

- Our numbers become directly comparable to the published shared-task results,
  which is a free external validity check on the whole pipeline.
- The split is already frozen and public, so there is no suspicion that we tuned
  it.

Arguments for re-splitting:

- The shared-task split is document-level; if we adopt article-stem grouping (§3)
  for consistency with GraSCCo, MEDDOCAN's own split may put same-article cases on
  both sides. **This is checkable and has not been checked yet** — see §6.
- Using MEDDOCAN's split but a self-made GraSCCo split makes the two corpora
  methodologically different, and the whole point of the porting axis is that the
  recipe is the same.

A middle option: reuse MEDDOCAN's split as-is, report against the leaderboard,
**and** report a second article-disjoint split, with the difference between them
as evidence about how much article-level leakage matters. Costs one extra
evaluation run per arm and answers the question rather than assuming it.

For GraSCCo there is no choice — a split must be made. It should be created and
committed before any German rule is written, per DESIGN.md §6.

## 5. Obstacles to rule writing

**Both corpora, and this one is a trap:** BOM interacts with offsets.
32 of 1,000 MEDDOCAN `.txt` files and 5 of 63 GraSCCo files carry a UTF-8 BOM,
and in both corpora **the gold offsets count the BOM as a character**. Reading
MEDDOCAN with `encoding='utf-8-sig'` silently shifts 761 gold spans by one. In
GraSCCo it is worse: in `Baastrup.txt` and `Dupuytren.txt` the first gold span
starts at index 0, so the annotated surface itself begins with U+FEFF (e.g.
`'﻿ARCOS-KLINIK FLENSBURG…'`). Any loader must decide this explicitly.

**Date formats are wildly non-uniform.** GraSCCo: 61 distinct digit shapes over
632 spans — `DD.DD.DDDD` (200), bare `DDDD` (56), `DD/DD` (54), `DD.DD.DD` (47),
`D.D.DDDD` (30), `DDDD-DD-DD` (5), and truncated forms like `D.DD.` (6) and
`DD.D.` (3) where the year is simply absent. MEDDOCAN: 108 distinct shapes, but
far more concentrated — `DD/DD/DDDD` alone covers 1,470 of ~2,000 sampled, plus
spelled-out months (`febrero de DDDD` etc., ~150 spans total). So the Spanish date
rule is mostly one regex; the German date rule is a long tail, and a bare `2035`
being a date at all is context-dependent.

**GraSCCo `ID` is partly unmatchable.** Surfaces include `'B'`, `'II'`, `'119'`,
`'3 Süd'`. Single letters and ward numbers cannot be recovered by pattern; they
will show up as irreducible recall loss, and any rule aggressive enough to catch
them will destroy precision.

**German capitalisation is uninformative, as predicted.** All nouns are
capitalised, so the orthographic cue that carries English and (partly) Spanish
name detection is absent. This is the DESIGN.md §7 hypothesis, and GraSCCo is the
intended test of it — worth noting that only 6 of 63 documents have an ALL-CAPS
header line, so even that weak structural cue is rare.

**MEDDOCAN is heavily templated, which will flatter rule performance.** Header
lines appear in nearly every document: `Nombre:` (750), `Apellidos:` (750),
`Domicilio:` (750), `Médico:` (751), `Fecha de Ingreso` (750), `NHC:` (747),
`NASS:` (580), `Datos del paciente` (446), `Remitido por:` (438). A `Nombre:\s*(…)`
rule will score very well on Spanish for reasons that have nothing to do with
Spanish. Any claim about rule-layer strength on MEDDOCAN should separate templated
header fields from free-narrative mentions — otherwise the cue-reliability
hypothesis in DESIGN.md §7 is confounded by document structure.

**Language mixing in MEDDOCAN.** `Informe clínic` (401) and `Pacient` (172) are
Catalan, not Spanish, appearing inside a corpus labelled `es`. Low volume but real,
and relevant because `es-carmen` is explicitly Spanish/Catalan.

**Formatting noise is mild.** GraSCCo: 128 tabs, 215 runs of 3+ spaces, 10
bullets, no pipe tables, no form feeds. Nothing that needs a layout parser.

**Structural note in our favour:** GraSCCo has **zero nested and zero crossing
span pairs**, and its CAS text is byte-identical to the plain `.txt` for all 63
documents (verified). So the union merge has no containment cases to resolve in
German, and annotations can be applied directly to the raw text.

## 6. Open questions for the user

Ordered by how much they change the numbers.

1. **`SEXO_SUJETO_ASISTENCIA` (1,841 spans, 8.1% of Spanish gold): in or out of
   the canonical set?** Out = not HIPAA-comparable to the leaderboard. In =
   Spanish recall partly measures finding the letter `H`. (§2.4)
2. **`NAME_TITLE`: does the canonical set have a title type?** The two corpora
   annotate this incompatibly, so some gold has to change or some asymmetry has to
   be accepted. (§2.6)
3. **`TERRITORIO`: split into city/ZIP or merge GraSCCo's two into one?** Splitting
   means deriving gold labels heuristically. (§2.2)
4. **`FAMILIARES_*` (416 spans of common nouns like "madre"): keep as PHI?** (§2.1)
5. **Split unit: article stem vs document for MEDDOCAN; filename stem vs document
   for GraSCCo?** Grouping `Tupolev_*`/`Colon_Fake_*` assumes a relationship the
   data does not state. (§3)
6. **Reuse MEDDOCAN's official split, make our own, or report both?** (§4)
7. **BOM policy: strip and shift offsets, or retain U+FEFF inside gold surfaces?**
   Needs one answer applied identically to every corpus loader. (§5)
8. **`OTROS_SUJETO_ASISTENCIA` (22 spans) and the 12 GraSCCo types with n ≤ 8:
   in the denominator as an irreducible floor, or excluded as unlearnable?** (§2.5)

Unchecked things I stopped short of, because they need a decision above first:

- What `NAME_EXT` (n=1) means; the annotation guide PDF in
  `data/raw/de-grascco/schema/` presumably says, and I did not read it.
- Whether the `background` set's 3,751 unannotated documents overlap the
  annotated 1,000 (relevant to `sup-free`, since placeholder-derived labels would
  come from there).

(The article-disjointness of MEDDOCAN's official split was on this list and has
since been measured — see §7.1.)

---

## 7. 추가 조사 (2026-08-05, follow-up)

Three questions asked separately from the eight above. Facts only; the decisions
in §6 are unaffected except where a §3 statement is corrected by measurement.

### 7.1 Is MEDDOCAN's official split document-level, and can one patient or one source case straddle folds?

**The split is document-level.** Measured file counts per fold: train 500, dev
250, test 250, summing to 1,000 with 1,000 distinct document ids and no id in more
than one fold. Span counts per fold: train 11,333 / dev 5,801 / test 5,661.

**No patient concept exists to straddle.** MEDDOCAN's XML carries exactly six
attribute names (`id`, `start`, `end`, `text`, `TYPE`, `comment`) on annotation
elements and two child elements of `<MEDDOCAN>` (`<TEXT>` CDATA, `<TAGS>`). There
is no patient field, case field, or episode field anywhere in the distribution.
Patient names present in the text are annotated as `NOMBRE_SUJETO_ASISTENCIA` and
are synthetic substitutions, so they do not function as keys.

**The source *article* can and does straddle folds.** Document ids are
`{SciELO-article-id}-{n}`:

- 936 distinct article stems over 1,000 documents.
- Documents per stem: 888 stems have 1, 35 have 2, 11 have 3, one has 4, one has 5
  → **48 stems carry more than one document**.
- **34 of those 48 stems have their documents split across folds**, affecting 80
  documents (8.0% of the corpus). Fold combinations: test+train 15, dev+train 12,
  dev+test 4, all three 3.
- Example: `S0004-06142009000300014-1` is in test while
  `S0004-06142009000300014-4` is in train.

*Correction, since applied:* `profiles/es-meddocan.raw.json` recorded
`distinct_article_stems: 906` and `stems_with_more_than_one_document: 47`. Both
were wrong. The stem regex was `^(S\d{4}-\d+)-\d+$`, which assumes the article
part is all digits after the leading `S####`; 31 of 1,000 SciELO ids carry a
letter in the journal prefix (`S0465-546X`, `S1138-123X`, `S1579-699X`,
`S1699-695X`, `S1889-836X`), so those ids were dropped from the grouping rather
than counted. The correct figures are **936 stems and 48 multi-document stems**,
with the rule "stem = everything before the final hyphen, article part opaque"
parsing 1,000 of 1,000. Only one of the 31 dropped ids belonged to a
multi-document stem, which is why the stem count moved by 30 but the
multi-document count moved by 1.

Both profiles and §3 above have been corrected; each profile keeps a
`_corrected_2026_08_05` block with the old value and the cause. The same numeric
assumption was present in `profiles/de-grascco.raw.json`, whose stem rule matched
only digit suffixes and so grouped `Tupolev_1..4` but missed `Colon_Fake_A..K`
(11 documents) — also corrected.

**Whether cross-fold siblings are the same patient — checked, they are not.** For
each of the 34 cross-fold stems, the sets of `NOMBRE_SUJETO_ASISTENCIA`,
`ID_SUJETO_ASISTENCIA`, `FECHAS` and `EDAD_SUJETO_ASISTENCIA` surfaces were
intersected across folds. Two stems share anything at all:

- `S0004-06142009000300014` shares the given name `Antonio`, but the surnames
  differ: `Moreno Flores` (test) vs `Machado Briceño` (train).
- `S1137-66272011000100013` shares the age string `45 años`; its three documents
  carry `Cruz Andrade, Martin` (train), `Massotti Fernandez, Emiliano` (test),
  `ColmenerO Rodriguez, Esteban` (test).

The other 32 share no identifying surface across folds. So same-article documents
are distinct synthetic patients; what they share is the article, not the patient.

**No duplicated case text.** 0 exact-duplicate document texts corpus-wide, and 0
duplicated narrative prefixes (first 1,500 chars after the templated header block)
— so the cross-fold article relationship is authorship/formatting, not a repeated
case write-up.

### 7.2 Rate at which the same surface form recurs across splits

MEDDOCAN, exact-string match, test spans whose surface also occurs anywhere in
train. "distinct" columns count unique surfaces rather than spans.

| type | test spans | seen in train | rate | distinct test | distinct seen |
|---|---|---|---|---|---|
| `SEXO_SUJETO_ASISTENCIA` | 461 | 459 | **99.6%** | 19 | 17 |
| `PAIS` | 363 | 351 | **96.7%** | 20 | 13 |
| `EDAD_SUJETO_ASISTENCIA` | 518 | 475 | **91.7%** | 125 | 93 |
| `FAMILIARES_SUJETO_ASISTENCIA` | 81 | 62 | 76.5% | 37 | 18 |
| `TERRITORIO` | 956 | 702 | 73.4% | 390 | 186 |
| `HOSPITAL` | 130 | 59 | 45.4% | 105 | 41 |
| `PROFESION` | 9 | 4 | 44.4% | 8 | 3 |
| `NOMBRE_SUJETO_ASISTENCIA` | 502 | 199 | **39.6%** | 410 | 119 |
| `FECHAS` | 611 | 137 | 22.4% | 562 | 106 |
| `CORREO_ELECTRONICO` | 249 | 48 | 19.3% | 236 | 44 |
| `INSTITUCION` | 67 | 12 | 17.9% | 58 | 12 |
| `NOMBRE_PERSONAL_SANITARIO` | 501 | 77 | 15.4% | 247 | 35 |
| `NUMERO_TELEFONO` | 26 | 4 | 15.4% | 26 | 4 |
| `NUMERO_FAX` | 7 | 1 | 14.3% | 7 | 1 |
| `ID_TITULACION_PERSONAL_SANITARIO` | 234 | 25 | 10.7% | 217 | 8 |
| `CALLE` | 413 | 34 | 8.2% | 404 | 28 |
| `ID_SUJETO_ASISTENCIA` | 283 | 5 | 1.8% | 259 | 5 |
| `ID_ASEGURAMIENTO` | 198 | 0 | 0.0% | 198 | 0 |
| `ID_CONTACTO_ASISTENCIAL` | 39 | 0 | 0.0% | 39 | 0 |
| `OTROS_SUJETO_ASISTENCIA` | 7 | 0 | 0.0% | 7 | 0 |
| `CENTRO_SALUD` | 6 | 0 | 0.0% | 6 | 0 |
| **all types** | **5,661** | **2,654** | **46.9%** | | |

GraSCCo has no split, so the analogue is cross-*document* repetition: spans whose
exact surface also appears in at least one other document, out of 63 documents.

| kind | spans | distinct surfaces | surfaces in >1 doc | spans carrying such a surface |
|---|---|---|---|---|
| `NAME_TITLE` | 139 | 52 | 12 | 97 (**69.8%**) |
| `AGE` | 19 | 14 | 4 | 8 (42.1%) |
| `ID` | 59 | 44 | 9 | 22 (37.3%) |
| `LOCATION_CITY` | 59 | 32 | 4 | 20 (33.9%) |
| `LOCATION_HOSPITAL` | 35 | 28 | 4 | 9 (25.7%) |
| `LOCATION_ZIP` | 38 | 28 | 1 | 5 (13.2%) |
| `DATE` | 632 | 491 | 23 | 53 (8.4%) |
| `LOCATION_STREET` | 36 | 32 | 1 | 2 (5.6%) |
| `NAME_DOCTOR` | 154 | 145 | 4 | 8 (5.2%) |
| `DATE_BIRTH` | 61 | 60 | 1 | 2 (3.3%) |
| `NAME_PATIENT` | 166 | 102 | 1 | 5 (**3.0%**) |
| 10 kinds with n ≤ 19 (`CONTACT_PHONE`, `CONTACT_FAX`, `PROFESSION`, `LOCATION_ORGANIZATION`, `LOCATION_COUNTRY`, `NAME_USERNAME`, `NAME_RELATIVE`, `NAME_EXT`, `CONTACT_EMAIL`) | 38 | | 0 | 0 (0.0%) |
| **all kinds** | **1,436** | | | **231 (16.1%)** |

The single repeated `NAME_PATIENT` surface is `Konstantin Tupolev`, appearing in
`Tupolev_1..4`. **101 of 102 distinct patient-name surfaces occur in exactly one
document.** GraSCCo's high-repetition kinds are the ones whose surface inventory is
small by nature (`NAME_TITLE` = 52 distinct strings, mostly `Dr.`; `ID` includes
`B`, `119`, `II`).

Scale figures for judging whether a seen/unseen split is measurable: at a 20% test
fold GraSCCo yields 12–13 documents and roughly 290 spans, of which the
unseen-name portion of `NAME_PATIENT` would be about 30 spans. MEDDOCAN's test
fold has 502 `NOMBRE_SUJETO_ASISTENCIA` spans, 303 of them with surfaces absent
from train.

### 7.3 Document-type distribution of GraSCCo's 63 documents

Three measurements, since the corpus ships no document-type field.

**(i) Explicit type line in the header (first 600 chars, on its own line).** Present
in **13 of 63**:

| header type line | docs | files |
|---|---|---|
| `Arztbrief` / `Definitiver Arztbrief` | 5 | `Colon_Fake_H`, `Dewald`, `Koenig`, `Utz`, `Ypsilanti` |
| `Befund` / `Befund:` | 4 | `Beuerle`, `Joubert`, `Sudeck`, `Tupolev_3` |
| `Befund - Ambulanz für …` | 3 | `Dupuytren` (Pigmentveränderungen), `Ehrenberger` (Melanomnachsorge), `Schnitzler` (Melanomnachsorge) |
| `Entlassungsbrief` | 1 | `Osler` |

`AMBULANZKARTE` appears as the first line of `Recklinghausen.txt` but outside the
600-char window used above; counting it makes 14.

**(ii) Letter form, by salutation and closing.** Salutation patterns searched:
`Sehr geehrte`, `Sehr verehrte`, `Werte Frau Kollegin`, `werte(r) geehrter Herr
Kollege`, `Liebe(r) Frau/Herr Kolleg…`. Closing: `Mit kollegialen/freundlichen/
vorzüglicher …`.

| | docs |
|---|---|
| both salutation and closing | 39 |
| salutation or closing, not both | 13 |
| **letter-form total** | **52 / 63** |
| neither | **11 / 63** — `Ehrenberger`, `Joubert`, `Koenig`, `Meulengracht`, `Recklinghausen`, `Schielaug`, `Tupolev_1`, `Tupolev_2`, `Tupolev_3`, `Utz`, `Ypsilanti` |

The 11 non-letter documents overlap heavily with group (i): 7 of them carry an
explicit `Arztbrief`/`Befund`/`AMBULANZKARTE` header instead.

**(iii) Content cues, non-exclusive, whole document.** A document can hit several.

| cue (regex over full text) | docs |
|---|---|
| collegial letter frame | 46 |
| radiology / imaging (`MRT`, `Sonographie`, `Röntgen`, `Kontrastmittel`, `CT …Befund`) | 30 |
| pathology / histology (`Histologie`, `Immunhistochemie`, `Makroskopie`) | 25 |
| outpatient (`Ambulanz`, `ambulante Vorstellung`, `Sprechstunde`) | 24 |
| laboratory (`Laborwerte`, `Leukozyten`, `CRP`, `Hämoglobin`) | 22 |
| progress note (`Verlaufsnotiz`, `Visite`, `Verlauf:`) | 16 |
| discharge (`Entlassungsbericht/-brief`, `entlassen wir`) | 4 |
| tumour board (`Tumorboard`, `Tumorkonferenz`) | 3 |
| operation report (`OP-Bericht`, `Hautschnitt`, `intraoperativ`) | 1 |

**Department named in the header: 20 of 63.** Distinct units include
`Universitätsklinik für Innere Medizin`, `Abteilung für Onkologie` (4 documents,
spelled `O N K O L O G I E` letterspaced in `Colon_Fake_B`), `Abteilung für
Neurologie`, `Klinik für Allgemeinchirurgie`, `Klinik für Chirurgie`, `Klinik für
Ophthalmologie`, `Klinik für Orthopädie und Traumatologie`,
`Hals-Nasen-Ohren-Klinik`, `Institut für Röntgendiagnostik`, `Epilepsie-Einheit`
(2 documents), `Röntgenabteilung`, `Ambulanz für Endoskopie`, `Ambulanz für
Melanomnachsorge` (2), `Abteilungfür Innere Medizin` (missing space, in
`Tupolev_1` and `Tupolev_2`).

Document length ranges from 922 chars (`Sudeck`) to 12,214 (`Obradovic`).

**Stem groups, re-examined.** §3 offers `Tupolev_*` and `Colon_Fake_*` as one
option for the split unit and says "whether they are the same patient is not
recorded in the data". That is now measurable from the annotations, and the two
groups behave oppositely:

`Tupolev_1..4` — one patient across four documents:

| file | `NAME_PATIENT` | `DATE_BIRTH` | `ID` |
|---|---|---|---|
| `Tupolev_1` | `Konstantin Tupolev` | `21/06/1967` | `1933309807`, `B`, `119`, `A31` |
| `Tupolev_2` | `Konstantin Tupolev` | `21/06/1967` | `1933309807`, `2111`, `B`, `119` |
| `Tupolev_3` | `Konstantin Tupolev` | `21.06.67` | `1933309807`, `B`, `119`, `A31` |
| `Tupolev_4` | `Tupolev, Konstantin` ×4 forms | `21.06.1967` | `I` |

Same name, same birth date in three different formats, same record number
`1933309807` in three of four. `Tupolev_1`/`_2` are Innere Medizin, `_3` is
Röntgenabteilung — consistent with one episode documented by different units.

`Colon_Fake_A..K` — eleven different patients:

| file | `NAME_PATIENT` | `DATE_BIRTH` |
|---|---|---|
| `A` | `Antonia Anderer` | `21/3/2017` |
| `B` | `Beatrice DE BEAUHARNAIS` | `24.04.1987` |
| `C` | `CHRIST, Charlotte` | `24.12.1972` |
| `D` | `DAMARIS, Dyonisia` | `11.2.1967` |
| `E` | `Euripedes Erler` | `30.12.1987` |
| `F` | `FRITZLE, Fridolin` | `6/7/1980` |
| `G` | `GERODLSAUER, Gerli` | `12.4.1977` |
| `H` | `Huberta Hotzenplotz` | `3.6.1942` |
| `I` | `Iris Iselin` | `12.12.1932` |
| `J` | `Jakob Jockel` | `1.1.1992` |
| `K` | `Katharina Korsakoff` | `13.5.1981` |

Eleven distinct names alliterating with the suffix letter, eleven distinct birth
dates, no shared `ID`. The shared stem marks a shared *scenario* (colon
carcinoma), not a shared patient. So the §3 remark that grouping both stem
families is "the conservative reading" holds for `Tupolev_*` and does not hold for
`Colon_Fake_*`; grouping the latter would cost 10 of 63 independent units for a
relationship the annotations contradict. Stated as measurement — the split unit
remains open question 5.

---

## 8. CARMEN-I (`es-carmen`), inventoried 2026-08-06

**DUA notice.** CARMEN-I is real clinical text from the Hospital Clínic of
Barcelona under the PhysioNet Contributor Review Health Data License. **No span
surface form appears in this section.** Where a surface had to be characterised,
only its length and charset class are given. This is stricter than the treatment of
MEDDOCAN and GraSCCo above, where sample surfaces are quoted because those corpora
are synthetic. Mechanical detail is in `profiles/es-carmen.raw.json`.

2,000 documents, 8,231 PHI spans, 18 PHI types observed of 28 declared. Two
anonymisation variants (`masked`, `replaced`), brat standoff, no BOM anywhere, LF
only, no official split.

### 8.1 Mapping CARMEN-I's types onto the §9.0 canonical set

**CARMEN-I's PHI schema is a superset of MEDDOCAN's observed type set.** Measured, not
assumed: CARMEN-I's `ann/annotation.conf` declares 35 entity types, of which 28 are
PHI and 7 are the medical-concept layer (`SINTOMA`, `PROCEDIMIENTO`, `ENFERMEDAD`,
`FARMACO`, `SPECIES`, `HUMANO`, `ENTIDAD_OBSERVABLE`). All **22** of MEDDOCAN's
observed types appear among those 28, and the 6 PHI types CARMEN-I declares beyond
them are `NUMERO_IDENTIF`, `URL_WEB`, `DIREC_PROT_INTERNET`,
`IDENTIF_VEHICULOS_NRSERIE_PLACAS`, `IDENTIF_DISPOSITIVOS_NRSERIE`,
`IDENTIF_BIOMETRICOS`.

Stated carefully, because the comparison is asymmetric: **the MEDDOCAN release ships
no `annotation.conf`** (verified — no `.conf` or `.dtd` anywhere in
`data/raw/es-meddocan/`), so MEDDOCAN's *declared* schema is not observable from the
data we hold and only its *observed* 22 types can be compared. The two type systems
are clearly the same lineage — identical names, identical `_SUJETO_ASISTENCIA` /
`_PERSONAL_SANITARIO` role suffixes — but "identical schema" is not something the
releases let us verify, and this section does not claim it.

The practical consequence still holds: the mapping is mostly not a new design problem
but the §9.0 MEDDOCAN column restricted to the 18 types CARMEN-I actually uses, with
two exceptions treated below.

**Maps by the existing §9.0 rows, no new decision required — 16 of the 18 types:**

| canonical (§9.0) | CARMEN-I source types | n |
|---|---|---|
| `NAME` | `NOMBRE_PERSONAL_SANITARIO` | 151 |
| `DATE` | `FECHAS` | 5,386 |
| `AGE` | `EDAD_SUJETO_ASISTENCIA` | 815 |
| `LOCATION_AREA` | `TERRITORIO` 90, `PAIS` 118 | 208 |
| `LOCATION_STREET` | `CALLE` | 22 |
| `ORGANISATION` | `HOSPITAL` 316, `INSTITUCION` 129, `CENTRO_SALUD` 52 | 497 |
| `CONTACT` | `NUMERO_TELEFONO` | 22 |
| `ID` | `ID_SUJETO_ASISTENCIA` 14, `ID_CONTACTO_ASISTENCIAL` 2 | 16 |
| `PROFESSION` | `PROFESION` | 91 |
| `OTHER` | `OTROS_SUJETO_ASISTENCIA` | 38 |
| **mapped by existing rows** | | **7,246** |

**Not settled by the existing table — the remaining 2 types, decided below or not at
all:**

| source type | n | status |
|---|---|---|
| `NUMERO_IDENTIF` | 227 | no §9.0 row covers it; **undecided**, see (i) |
| `URL_WEB` | 1 | no §9.0 row covers it; **undecided**, see (ii) |
| `SEXO_SUJETO_ASISTENCIA` | 458 | §9.1 excluded, same type name as MEDDOCAN's exclusion |
| `FAMILIARES_SUJETO_ASISTENCIA` | 299 | §9.1 excluded, same type name as MEDDOCAN's exclusion |

7,246 + 227 + 1 + 458 + 299 = 8,231. Reconciles against the corpus gold total, so no
span is unaccounted for either way the two open types are decided.

**Types that do not map cleanly. Options only; no conclusion drawn.**

**(i) `NUMERO_IDENTIF` — 227 spans, no MEDDOCAN counterpart.** All five ID subtypes
MEDDOCAN actually uses name *a role* (`ID_SUJETO_ASISTENCIA` patient,
`ID_ASEGURAMIENTO` insurance, `ID_TITULACION_PERSONAL_SANITARIO` clinician licence,
`ID_EMPLEO_PERSONAL_SANITARIO` clinician employment, `ID_CONTACTO_ASISTENCIAL`
contact), which is why §9.0 could collapse them into one `ID` without losing anything
recoverable. `NUMERO_IDENTIF` names a *number* with no role,
and it is CARMEN-I's largest ID type by far (227 against 14 for
`ID_SUJETO_ASISTENCIA`), so it is not a residual bucket. MEDDOCAN has zero instances
of it, and since MEDDOCAN ships no schema file we cannot tell whether it was declared
there and unused or never existed in that guideline at all.
- **(a)** add a canonical `ID_UNSPECIFIED`. Honest, but it exists to hold one
  corpus's habit and would be empty for MEDDOCAN and GraSCCo, so per-type tables
  would carry a column that is structurally absent elsewhere.
- **(b)** merge into `ID`. §9.0 already collapses five MEDDOCAN ID subtypes into one
  `ID`, and role survives as `subtype`, so this is the consistent move — the cost is
  that `subtype` becomes non-comparable in a new way: MEDDOCAN's subtypes name roles
  and CARMEN-I's does not. Note that §9.0's stated justification for the collapse is
  that "the two corpora do not partition the space the same way", which this case fits
  exactly; whether that argument extends to a subtype carrying *no* role information
  is the open part.
- **(c)** exclude. Not defensible: an unqualified identifier number in a hospital
  record is a HIPAA identifier and 227 spans is 2.8% of the corpus.

**(ii) `URL_WEB` — 1 span, no MEDDOCAN instance.** CARMEN-I declares two
internet-address types, `DIREC_PROT_INTERNET` (line 38 of `annotation.conf`) and
`URL_WEB` (line 39), and uses only the latter, once. Neither is observed in MEDDOCAN.
- **(a)** fold into `CONTACT`, which already holds email, phone and fax — a URL is
  the same kind of thing (a channel), and this keeps the canonical set at ten types.
- **(b)** own canonical type. At n=1 it cannot be scored either way, so this buys
  nothing measurable.
- **(c)** exclude as n=1. Conflicts with §9.4, which deliberately keeps n≤8 types in
  the leak-rate denominator on the grounds that a leak is a leak.

**(iii) `NOMBRE_SUJETO_ASISTENCIA` — declared, ZERO instances.** This is the
patient-name type. MEDDOCAN has 2,014; GraSCCo has `NAME_PATIENT`. CARMEN-I's only
name type is the clinician one. Not a mapping problem but an evaluation problem:
- **(a)** report patient-name recall as **undefined** for `es-carmen` rather than
  as a number. There is no gold, so any figure would be an artefact of the
  denominator being zero.
- **(b)** treat the absence as informative and report it: a detector that fires on
  patient names here produces only false positives, which makes CARMEN-I a
  precision-only probe for that type.
- **(c)** merge `NAME` across roles and report one figure, accepting that the
  `es-carmen` `NAME` number is a clinician-name number and the MEDDOCAN one is a
  mixture. This is the option that quietly breaks cross-corpus comparability, so it
  needs stating either way.

**(iv) Ten declared types with zero instances.** `NOMBRE_SUJETO_ASISTENCIA`,
`CORREO_ELECTRONICO`, `NUMERO_FAX`, `DIREC_PROT_INTERNET`, `ID_ASEGURAMIENTO`,
`ID_TITULACION_PERSONAL_SANITARIO`, `ID_EMPLEO_PERSONAL_SANITARIO`,
`IDENTIF_VEHICULOS_NRSERIE_PLACAS`, `IDENTIF_DISPOSITIVOS_NRSERIE`,
`IDENTIF_BIOMETRICOS`. Declared-but-unused is not the same as absent-from-schema,
and the distinction matters for the Mapper agent: a mapping built from
`annotation.conf` alone would produce ten dead entries, and one built from observed
data alone would silently fail on a future release that uses them. Options: build
from the schema and mark observed counts; build from observations and validate
against the schema; or require both and fail on disagreement.

### 8.2 CARMEN-I vs MEDDOCAN — same type names, very different usage

Both Spanish, and every MEDDOCAN type name recurs in CARMEN-I's declared set (§8.1),
so the comparison is direct with no name translation in between.

| type | MEDDOCAN | CARMEN-I | note |
|---|---|---|---|
| `FECHAS` | 2,566 | 5,386 | CARMEN-I's dominant type: 65.4% of its spans vs 11.3% in MEDDOCAN |
| `NOMBRE_SUJETO_ASISTENCIA` | 2,014 | **0** | patient names absent from CARMEN-I |
| `NOMBRE_PERSONAL_SANITARIO` | 1,998 | 151 | |
| `TERRITORIO` | 3,818 | 90 | MEDDOCAN's largest type is nearly absent here |
| `PAIS` | 1,423 | 118 | |
| `CALLE` | 1,709 | 22 | |
| `CORREO_ELECTRONICO` | 959 | **0** | |
| `ID_TITULACION_PERSONAL_SANITARIO` | 931 | **0** | |
| `ID_ASEGURAMIENTO` | 783 | **0** | |
| `ID_SUJETO_ASISTENCIA` | 1,142 | 14 | |
| `NUMERO_IDENTIF` | **0** | 227 | present only in CARMEN-I |
| `NUMERO_TELEFONO` | 109 | 22 | |
| `NUMERO_FAX` | 28 | **0** | |
| `HOSPITAL` | 525 | 316 | |
| `INSTITUCION` | 237 | 129 | |
| `CENTRO_SALUD` | 14 | 52 | the one type CARMEN-I uses more |
| `PROFESION` | 37 | 91 | also more here |
| `EDAD_SUJETO_ASISTENCIA` | 2,074 | 815 | |
| `SEXO_SUJETO_ASISTENCIA` | 1,841 | 458 | §9.1 excluded |
| `FAMILIARES_SUJETO_ASISTENCIA` | 416 | 299 | §9.1 excluded |
| `OTROS_SUJETO_ASISTENCIA` | 22 | 38 | |
| `URL_WEB` | 0 | 1 | |
| `ID_CONTACTO_ASISTENCIAL` | 148 | 2 | |
| `ID_EMPLEO_PERSONAL_SANITARIO` | 1 | 0 | |

**Same name, different concept — none found.** No type name appears to mean something
different between the two corpora; the shared naming and shared role suffixes come
with shared meaning as far as the type list shows. The false-friend problem of §2.1
(`FAMILIARES_SUJETO_ASISTENCIA` vs GraSCCo's `NAME_RELATIVE`) does not recur here.
One caveat worth stating rather than assuming away: this was checked at the level of
type names and counts, **not** by reading annotated spans, which the DUA rule for this
corpus rules out. A guideline divergence that leaves the type names intact — for
instance a different convention on whether a date range is one span or two — would not
be visible to this comparison. The span-length distributions in the profile are the
only proxy available for that.

**Present in one only.** `NUMERO_IDENTIF` (CARMEN-I only, 227). Absent from
CARMEN-I but substantial in MEDDOCAN: patient names 2,014, email 959, clinician
licence 931, insurance 783, fax 28.

**The interesting asymmetry is not the type list but the distribution.** MEDDOCAN is
synthetic case reports with fabricated administrative blocks — addresses, emails,
insurance numbers, licence numbers — because its generator inserted them. CARMEN-I
is real hospital text where those elements simply are not written into the clinical
narrative; what survives is dates, ages and institution names. So MEDDOCAN
over-represents exactly the types that checksum and regex rules find easily
(email, phone, structured IDs), and CARMEN-I over-represents the type whose
surface form is most variable (dates: 5,386 spans across 3,071 distinct surfaces).
A rule set tuned on MEDDOCAN should be expected to transfer badly here, and this
is a confound in any MEDDOCAN→CARMEN-I porting result: the corpora differ in
authenticity and in type mix at the same time.

**How large the confound is, in recall points.** Computed on the §9.1-excluded scope
(MEDDOCAN 20,538 spans; CARMEN-I 7,474, counting the two undecided types of §8.1):

| canonical type (§9.0) | MEDDOCAN 20,538 | CARMEN-I 7,246 |
|---|---|---|
| `DATE` | 12.5% | **74.3%** |
| `CONTACT` + `ID` — email, fax, phone, all ID subtypes | 20.0% | **0.5%** |
| `NAME` | 19.5% | 2.1% |
| `LOCATION_AREA` | 25.5% | 2.9% |
| `AGE` | 10.1% | 11.2% |
| `ORGANISATION` | 3.8% | 6.9% |

CARMEN-I's total here is 7,246 rather than 7,474 because the two types §9.0 does not yet
place (`NUMERO_IDENTIF` 227, `URL_WEB` 1) are left out; folding both into `ID` and
`CONTACT` would move that row to 3.6%, still far below MEDDOCAN's 20.0%, so the
conclusion does not depend on how §8.1 is decided.

So **a detector that found nothing but dates, perfectly, would score 12.5% recall on
MEDDOCAN and 74.3% on CARMEN-I** — a 62-point difference produced entirely by type mix,
with detector quality held identical by construction. In the other direction, the
regex-and-checksum-friendly types that carry 20.0% of MEDDOCAN's spans carry 0.5% of
CARMEN-I's, so a rule set whose strength is exactly there has almost nothing to find in
CARMEN-I. `AGE` is the only type whose weight is close in both (10.1% vs 11.2%).
Aggregate recall cannot separate any of this from genuine porting difficulty, which is
why DESIGN.md §5.1 requires per-type reporting.

**The common-type subset, and why it does not help this pair.** §5.1 requires a
cross-corpus figure restricted to the types both corpora observe. Which types those are
depends on the level:

- **Source-type level** — 14 of the shared names are nonzero in both, covering **77.0%**
  of MEDDOCAN's in-scope spans and **96.9%** of CARMEN-I's. The restriction costs
  MEDDOCAN 4,716 spans (patient names 2,014, email 959, licence 931, insurance 783) and
  CARMEN-I 228, so it is strongly asymmetric.
- **Canonical level (§9.0), which is the level §5.1 scores at** — **all ten types are
  nonzero in both corpora**. `NOMBRE_PERSONAL_SANITARIO` keeps `NAME` alive for
  CARMEN-I even with zero patient names; `NUMERO_TELEFONO` keeps `CONTACT` alive with 22
  spans; `ID_SUJETO_ASISTENCIA` keeps `ID` alive with 14. So the restriction **drops
  nothing and changes no number**.

That is the substantive point rather than a technicality. The confound is not "the two
corpora annotate different types" — it is that they weight the *same* ten types
completely differently. A common-subset check passes here and the pair is still
incomparable in aggregate, so passing that check must not be read as evidence of
comparability. Per-type reporting is the requirement that actually bites.

### 8.3 §9.1 exclusions in CARMEN-I

| §9.1 excluded type | CARMEN-I | share of corpus |
|---|---|---|
| `SEXO_SUJETO_ASISTENCIA` | 458 | 5.56% |
| `FAMILIARES_SUJETO_ASISTENCIA` | 299 | 3.63% |
| `NAME_TITLE` equivalent | **0** | — |
| **total excluded** | **757** | **9.20%** |

Both Spanish exclusions recur here at a proportion close to MEDDOCAN's 9.90%
(2,257/22,795), so §9.1 costs the two Spanish corpora about the same fraction —
which is convenient for the comparison and is a coincidence worth naming as one.

`NAME_TITLE` has **no counterpart**: no title/honorific type is declared in
`annotation.conf` and none is observed. The boundary question of §2.6 (whether
`Dr.` belongs inside the name span) therefore does not arise for CARMEN-I, so the
argument in §9.3 for relaxed matching rests on GraSCCo alone among the three
corpora. That does not weaken the decision — one corpus with the conflict is enough
to make exact-boundary scoring guideline-dependent — but it does mean the evidence
base for it is narrower than three corpora. §9.3 now carries that argument further
than demotion: the exact-boundary mode is not computed at all, since a figure that
moves with the annotation guideline does so wherever it appears and not only when it
leads. Worth noting that this single-corpus evidence base is what a fourth corpus
could change, in either direction.

### 8.4 Catalan mixing and the `rules/{lang}.yaml` convention

Measured from the corpus's own per-document language label
(`CARMEN1_mappings.tsv`, cross-checked against the file list — all 2,000 keys match):

| label | documents | share |
|---|---|---|
| `es` Spanish | 1,697 | 84.9% |
| `bi` mixed Spanish + Catalan **within one document** | 264 | 13.2% |
| `cat` Catalan | 39 | 2.0% |

**`rules/{lang}.yaml` as currently specified does not survive this.** The convention
assumes one rule file per language and one language per corpus. Here 13.2% of
documents are internally mixed, so there is no document-level language assignment
that makes a single file correct — and 221 of the 264 bilingual documents are one
document type (`IR`), so the mixing is not evenly spread either.

What the options cost:

- **(a) One `rules/es.yaml`, treat Catalan as noise.** 2.0% Catalan-only documents
  is small enough to absorb, but the 13.2% bilingual documents are not: the Catalan
  passages inside them are where context-cue rules will fail, and the failures land
  inside documents counted as Spanish. Cheapest, and it hides the failure in the
  aggregate.
- **(b) `rules/es.yaml` + `rules/cat.yaml`, select per document.** Works for the
  1,736 monolingual documents and is undefined for the 264 mixed ones. Needs a
  tie-break rule, and the tie-break is where the errors go.
- **(c) Load both files for every document; union the matches.** No language
  identification needed at all, which removes a failure mode rather than adding one.
  Costs precision wherever a Catalan trigger word is a Spanish word, and makes the
  per-layer attribution of §7 harder because a match may come from either file.
- **(d) Sub-document language segmentation.** Most faithful to the data and the most
  machinery; also introduces a component whose own errors are not measured
  anywhere in the current metric set.

Note that (c) is the only option that does not require language identification, and
that the `bi` label is the corpus's own — we did not have to infer it, which means
option (b)'s selector could be *evaluated* against a gold label here. That is a
reason to prefer measuring before choosing. **No decision drawn.**

Consequence for `config/naming.yaml`: if `rules/{lang}.yaml` stays as the path
convention, `{lang}` needs a defined value for a bilingual corpus. `es` would be
wrong for 13.2% of documents and `es+cat` is not a language code. This is a naming
decision, so per CLAUDE.md it belongs in `naming.yaml` before any rule file exists.

### 8.5 Split unit candidates

**No patient key exists.** Filenames are
`CARMEN-I_{doctype}_{section}_{n}`; there is no patient, encounter, or record
identifier in the filename, in `CARMEN1_mappings.tsv`, or anywhere else in the
release.

Two candidate groupings were tested against the §9.5 rule (identifier-surface
agreement, not filename structure), and **both fail**:

1. **Same `(doctype, number)` across different section tokens** — 189 candidate
   groups covering 775 documents. This looks compelling: `IA_ANTECEDENTES_7` and
   `IA_PROCESO_ACTUAL_7` read like two sections of one patient's letter. Measured:
   **0 of 189 groups share a single identifier surface**. And the numbering is
   contiguous `1..N` within every one of the 20 `(doctype, section)` pairs, so the
   number is a per-section index and the co-numbering is arithmetic, not linkage.
   This is the MEDDOCAN article-stem error in a new costume — filename structure
   raising the question and being mistaken for the answer.
2. **Transitive grouping on shared identifier surfaces** (`ID_SUJETO_ASISTENCIA`,
   `NUMERO_IDENTIF`) — yields 1,944 groups, of which 1,941 are singletons, plus one
   group of 52, one of 5, one of 2. The three linking surfaces are 7, 3 and 9
   characters, **letters only, zero digits** — not identifier values. Grouping on
   them would merge 52 unrelated patients on what is most likely a generic word from
   the surrogate generator.

So under CLAUDE.md's rule — largest natural group available, §9.5 identifier
agreement where no patient key exists — the largest *defensible* unit is the
**document**. Candidates and their costs:

- **(a) Document-disjoint, 2,000 units.** The only grouping the data supports.
  Risk: if two documents really are the same patient and we cannot tell, they can
  land on opposite sides of the split, which inflates results. Unmeasurable here,
  and it must be stated as a limitation rather than assumed away.
- **(b) Document-disjoint, stratified by document type and language.** Same units,
  but controls two large confounds: PHI density varies 4× by document type (`IR`
  8.1 spans per 1,000 tokens vs `IA` 32.0) and the bilingual documents are 84%
  concentrated in `IR`. Without stratification a random split can hand one fold a
  materially different PHI density and language mix.
- **(c) Group by `(doctype, number)` anyway, as the conservative reading.**
  Rejected by measurement above, and the cost is concrete: 775 documents collapse
  to 189 units, losing 586 independent units for a relationship the annotations
  contradict. This is exactly the `Colon_Fake_*` decision from §7.3.

Two further facts bear on any split:

- **462 documents have zero PHI spans** in the masked variant (`IR` 389, `IA` 66,
  `IT` 7) and 461 in the replaced variant (`IR` 388), the difference being the one
  `CARMEN-I_IR_746` discrepancy recorded under `span_count_reconciliation` in the
  profile. They are not empty — median 96 tokens — so
  they contribute false-positive opportunity but nothing to the leak-rate numerator or
  denominator. A fold's effective size is not its document count, and 23% of documents
  cannot be assigned a per-document leak rate at all.
- **789 of 2,000 units are clinical *sections*, not whole notes** (`IA` and `IT`
  filenames name `ANTECEDENTES`, `PROCESO_ACTUAL`, `EXPLORACION_*`,
  `PLAN_TERAPEUTICO`, `SEGUIMIENTO`, `EVOL`). A section is not a note type, so
  CARMEN-I cannot straightforwardly supply the note-type axis the way §7 describes
  for GraSCCo — and the `IR` documents, which have no section token, are a
  different kind of unit from the `IA`/`IT` ones in the same corpus.

**No official split ships** (verified: no manifest, index, or split-named file
anywhere in the release). Unlike MEDDOCAN there is no external comparability to
inherit, and unlike MEDDOCAN's frozen 500/250/250 the split must be made here and
frozen before any rule is written.
