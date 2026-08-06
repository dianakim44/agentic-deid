# data/

**No corpus is redistributed in this repository** — neither DUA-restricted material
nor openly licensed corpora. This directory holds acquisition scripts and this
document. Everything else under `data/` is ignored by git and blocked by
`tools/release_screen.py`.

Corpus IDs are the ones defined in `config/naming.yaml`. Do not invent new ones here.

---

## Acquisition

| Corpus ID | Source | Language | Note types | Access | Status |
|-----------|--------|----------|------------|--------|--------|
| `ko-surro` | derived from PhysioNet nursing notes | Korean | nursing notes | PhysioNet credentialed + DUA | held, DUA |
| `es-meddocan` | Zenodo | Spanish | clinical case reports | open | **acquired 2026-08-05** |
| `de-grascco` | Zenodo (two records) | German | GP letters | open | **acquired 2026-08-05** |
| `es-carmen` | PhysioNet (CARMEN-I) | Spanish / Catalan / mixed | three coded types, never expanded in the release — see below | PhysioNet credentialed + DUA | **acquired 2026-08-06, held outside the repo** |
| `en-n2c2` | n2c2 / DBMI data portal | English | longitudinal progress notes | DUA | on hold, portal unavailable |

`ko-surro` is a surrogate corpus derived from a PhysioNet source release. It is cited
as a data source; the code lineage is a separate project.

### Acquired corpora — exact records

Run `data/acquire/fetch_meddocan.sh` and `data/acquire/fetch_grascco.sh`. Both pin
**version** DOIs rather than concept DOIs: a concept DOI always resolves to the
latest upload, which would let the corpus change under a frozen split.

| Corpus ID | Record | Version DOI | Concept DOI | Contents |
|-----------|--------|-------------|-------------|----------|
| `es-meddocan` | MEDDOCAN corpus: gold standard annotations… (Marimon et al. 2020, v1.0) | `10.5281/zenodo.4279323` | `10.5281/zenodo.4279322` | `meddocan.zip`, 11,738,792 B — train/dev/test in both BRAT and i2b2-style XML, plus 3,751 unannotated background documents |
| `de-grascco` | GraSCCo_PII_V2 — …with PII Annotations (2025-09-07) | `10.5281/zenodo.15747389` | `10.5281/zenodo.11502328` | UIMA CAS JSON + XMI annotations, type system, annotation guide |
| `de-grascco` | GraSCCo v1.1 (2026-03-05) | `10.5281/zenodo.18874981` | `10.5281/zenodo.6539130` | 63 plain `.txt` documents, **no annotations** |

Two Zenodo records carry the name "GraSCCo" and they are not interchangeable. The
record titled simply *GraSCCo* has no PHI annotations; the annotated release is
*GraSCCo_PII_V2*. Both are fetched — the annotations supply the gold spans, and the
`.txt` files are the authoritative newline/encoding form. Their document text was
verified byte-identical to the CAS `sofaString` for all 63 documents.

The DOI given for MEDDOCAN in the project brief, `10.5281/zenodo.4279322`, is the
concept DOI; it resolves to record `4279323`.

### `es-carmen` — credentialed, and the only corpus held outside the repository

Run `data/acquire/fetch_carmen.sh`. It **downloads nothing**: a PhysioNet
credentialed download is bound to one approved individual, and a script carrying a
working credential or session cookie would be a way to share access — which clause 3
of the licence forbids. The script states the procedure and then verifies whatever
the human has already done.

**Access conditions**, read from `LICENSE.txt` and the release `README.md` in the
downloaded corpus on 2026-08-06:

- **PhysioNet Contributor Review Health Data License v1.5.0** governs *access*.
  Clause 1 forbids attempting to identify any individual or institution; clause 3
  forbids sharing access with anyone else; clause 4 requires physical and electronic
  security; clause 5 requires reporting any identifying information found
  (`PHI-report@physionet.org`, and the CARMEN-I authors at `infosic@clinic.cat`).
- **CC-BY-SA-4.0** governs the *corpus content*. This is more permissive than the
  MEDDOCAN and GraSCCo CC-BY-4.0 in one respect (ShareAlike) and does **not** relax
  the access agreement above. A permissive content licence layered under a
  credentialed access licence is still credentialed: the two apply together.
- **Separate registration** stating intended use is required by the authors, so that
  patients can be informed how shared data is used. This is an obligation the other
  corpora do not carry.

**Why it is held outside the repository, unlike every other corpus.** The other
corpora sit under `data/raw/`, where `.gitignore` and `tools/release_screen.py`
protect them. That protection is real but it is in-band: it depends on the ignore
rules staying correct and on the screener being run. CARMEN-I is **authentic patient
narrative** — the sensitive items were annotated and replaced with synthetic
surrogates, but the clinical text itself was written about real people at the
Hospital Clínic of Barcelona, whereas MEDDOCAN and GraSCCo are synthetic
constructions throughout. For that difference in kind, an out-of-band guarantee is
worth the inconvenience: with the corpus outside the tree, `git add` cannot reach it
and the screener cannot even see it, so no staging accident is possible regardless of
what the ignore rules say. The path lives in `config/data_paths.local.yaml`, which is
gitignored; only `config/data_paths.example.yaml` is committed.

**The note-type column above is deliberately vague.** The release README says the
corpus consists of discharge letters, referrals and radiology reports, but the
filenames carry five opaque codes (`IR` 1,201 · `IA` 617 · `IT` 172 · `CC` 5 · `IE` 5)
and **no file in the release expands them** — five codes against three named genres,
so the correspondence is not even one-to-one. Any assignment of code to genre is inference, so it is not
recorded as fact here. Worse for the note-type axis of `docs/DESIGN.md` §7: 789 of the
2,000 units are named for a clinical *section* (history, current illness,
examination, therapeutic plan, follow-up, progress) rather than a whole note, and a
section is not a document type. See `docs/notes/corpus-observations.md` §8.

**Handling rule, stricter than for the other corpora.** No CARMEN-I span surface form
is quoted anywhere — not in `profiles/`, not in `docs/notes/`, not in a commit
message, not in an agent prompt. Counts, offsets, lengths and type labels only.
Sample surfaces are quoted for MEDDOCAN and GraSCCo because those are synthetic; that
allowance does not extend here. Two anonymisation variants ship: `txt/masked/`
contains bracketed type labels and no surrogate values at all and is the safer
variant to read while developing; `txt/replaced/` reads like a real note and should
be treated as if it were identifying.

## Licence terms

Values below were read from the Zenodo record metadata (`metadata.license.id`) on
2026-08-05, not inferred.

| Corpus ID | Terms | Access right | Redistribution |
|-----------|-------|--------------|----------------|
| `ko-surro` | PhysioNet DUA, inherited from the source release | credentialed | forbidden |
| `es-meddocan` | **CC-BY-4.0** (record `4279323`) | open | permitted by the licence, **not done here** |
| `de-grascco` | **CC-BY-4.0** (both records `15747389`, `18874981`) | open | permitted by the licence, **not done here** |
| `es-carmen` | **CC-BY-SA-4.0** content licence **under** PhysioNet Contributor Review Health Data License v1.5.0 access conditions, plus a use-registration obligation | credentialed | forbidden — clause 3 forbids sharing access, and the content licence does not override it |
| `en-n2c2` | n2c2 DUA | credentialed | forbidden |

CC-BY requires attribution. Cite:

- **MEDDOCAN** — Marimon M, Gonzalez-Agirre A, Intxaurrondo A, Rodríguez H, Lopez
  Martin JA, Villegas M, Krallinger M. *Automatic De-identification of Medical Texts
  in Spanish: the MEDDOCAN Track.* IberLEF/SEPLN 2019.
- **GraSCCo (text)** — Modersohn L, Schulz S, Lohr C, Hahn U. *GraSCCo — the first
  publicly shareable, multiply-alienated German clinical text corpus.* GMDS 2022.
- **GraSCCo (PII annotations)** — Lohr C, Faller J, Riedel A, et al. *GeMTeX's
  De-Identification in Action: Lessons Learned & Devil's Details.* Stud Health
  Technol Inform. 2025;331:274–282.

CC-BY-SA likewise requires attribution, and CARMEN-I additionally requires stating
that modifications were made. The release credits the Barcelona Supercomputing
Center's NLP4BIA team with Hospital Clínic de Barcelona and the Universitat de
Barcelona CLiC group; contacts are Martin Krallinger, Salvador Lima-López and Xavier
Borrat. **The downloaded release carries no DOI** — neither `LICENSE.txt`,
`SHA256SUMS.txt`, nor `CARMEN-I/README.md` states one, so unlike the two Zenodo
corpora there is no version DOI to pin here. Take the citation and DOI from the
PhysioNet project page when writing the paper; do not reconstruct it from memory.

Openly licensed corpora are not committed either. One acquisition path for every
corpus is simpler to keep honest than a mixed policy, and it removes the chance of a
DUA-restricted file landing in a directory that looks publishable.

## Local layout

Populated by the acquisition scripts. None of these paths are committed.

```
data/
  README.md              committed
  acquire/*.sh           committed — the fetch scripts
  raw/{corpus}/          as downloaded and extracted, unmodified
  raw/.download/         scratch space for in-flight archives
  derived/{corpus}/      surrogate substitution, tagging, offset extraction
sealed/{corpus}/         test fold. not read during development
splits/{corpus}.json     group-disjoint folds, frozen and committed
profiles/{corpus}.raw.json   mechanical inventory — offsets and counts, no note text
```

Only `data/README.md` and `data/acquire/*.sh` are committed; everything else under
`data/` is ignored by git and blocked by `tools/release_screen.py`.

`splits/{corpus}.json` and `profiles/{corpus}.raw.json` live outside `data/` because
they hold group IDs, offsets and counts rather than note text. `splits/` is committed
before any rule is written.

### As acquired (measured 2026-08-05)

| Corpus ID | On-disk layout | Files | Size |
|-----------|----------------|-------|------|
| `es-meddocan` | `raw/es-meddocan/meddocan/{train,dev,test}/{brat,xml}/` + `background/` | 1,000 `.txt` + 1,000 `.ann` + 1,000 `.xml`, plus 3,751 background `.txt` | 33 MiB |
| `de-grascco` | `raw/de-grascco/{annotations/grascco_pii_2_{json,xmi},schema,text}/` | 63 `.json` + 63 `.xmi` + 63 `.txt` + 3 schema files | 12 MiB |

`es-carmen` is not in this table because it is not under `data/`. Measured
2026-08-06 at the path in `config/data_paths.local.yaml`: `CARMEN-I/txt/{masked,replaced}/`
2,000 `.txt` each, `CARMEN-I/ann/{masked,replaced}/{anon,ner}/` 2,000 `.ann` each,
`CARMEN-I/tsv/`, plus `CARMEN1_mappings.tsv` (language and concept-annotation flags
per document), `README.md`, and brat `annotation.conf` / `visual.conf`; 60 MiB total,
`SHA256SUMS.txt` covering 14,015 files. There is **no official split** and no
manifest.

Full inventories, including PHI type counts and the offset convention, are in
`profiles/es-meddocan.raw.json`, `profiles/de-grascco.raw.json` and
`profiles/es-carmen.raw.json`.

**Loader warning.** In MEDDOCAN and GraSCCo some files carry a UTF-8 BOM and the gold
offsets count it as a character. Read as plain `utf-8`; `utf-8-sig` shifts 761
MEDDOCAN spans by one. See `docs/notes/corpus-observations.md` §5. CARMEN-I was
checked for the same defect and has **no BOM in any of its 8,000 files**, LF newlines
throughout — but read it as plain `utf-8` anyway, so that one loader is correct for
all three. Two further CARMEN-I cautions, both in `docs/notes/corpus-observations.md`
§8 and the profile: the `masked` and `replaced` offsets differ in 1,533 of 2,000
documents, so the two variants are **not interchangeable** and an `.ann` must be read
against the matching `.txt`; and the number in a filename is a per-section index
running `1..N`, **not** a record or patient identifier.

## Before publishing anything

```
python tools/release_screen.py            # working tree
python tools/release_screen.py --history  # every blob ever committed
```

A BLOCKED count above zero means stop. See CLAUDE.md.
