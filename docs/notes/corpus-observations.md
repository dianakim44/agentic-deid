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
- MEDDOCAN document IDs are `{SciELO-article-id}-{n}`. **47 of 906 article stems
  carry more than one document**, i.e. multiple cases from the same journal
  article.
- GraSCCo filenames are surnames/eponyms. `Tupolev_1..4` (4 files) and
  `Colon_Fake_A..K` (11 files) share a stem.

CLAUDE.md requires patient-disjoint splits; DESIGN.md §6 softens this to "the
largest natural group available (patient where patients exist, document
otherwise)". Neither corpus has patients, so the operative question is what the
largest *defensible* group is.

Options for MEDDOCAN:

- **(a)** split by article stem (906 groups), keeping same-article cases together.
  Rationale: cases from one article share an author, an institution, formatting
  habits, and sometimes a surrogate name pool. Two cases from the same article in
  train and test is the closest thing to patient leakage this corpus can have.
- **(b)** split by document (1,000 groups). Simpler, and matches how the
  shared task itself was split — but see §4.

Cost of (a) is low: 47 stems are affected, so group-level and document-level
splits differ by ~5% of documents.

Options for GraSCCo:

- **(a)** split by filename stem, keeping `Tupolev_*` and `Colon_Fake_*` together
  (48 groups instead of 63 documents). Rationale: the shared stem suggests a
  deliberate relationship — possibly the same fictional patient across visits, or
  systematic variants of one document. **Whether they are the same patient is not
  recorded in the data**; grouping them is the conservative reading.
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

- Whether MEDDOCAN's official split is already article-disjoint (bears on §4, and
  is a 10-minute check once the split unit is chosen).
- What `NAME_EXT` (n=1) means; the annotation guide PDF in
  `data/raw/de-grascco/schema/` presumably says, and I did not read it.
- Whether the `background` set's 3,751 unannotated documents overlap the
  annotated 1,000 (relevant to `sup-free`, since placeholder-derived labels would
  come from there).
