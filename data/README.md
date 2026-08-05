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
| `es-carmen` | PhysioNet (CARMEN-I) | Spanish / Catalan | discharge, referral, radiology | PhysioNet credentialed + DUA | request pending |
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

## Licence terms

Values below were read from the Zenodo record metadata (`metadata.license.id`) on
2026-08-05, not inferred.

| Corpus ID | Terms | Access right | Redistribution |
|-----------|-------|--------------|----------------|
| `ko-surro` | PhysioNet DUA, inherited from the source release | credentialed | forbidden |
| `es-meddocan` | **CC-BY-4.0** (record `4279323`) | open | permitted by the licence, **not done here** |
| `de-grascco` | **CC-BY-4.0** (both records `15747389`, `18874981`) | open | permitted by the licence, **not done here** |
| `es-carmen` | PhysioNet DUA | credentialed | forbidden |
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

Full inventories, including PHI type counts and the offset convention, are in
`profiles/es-meddocan.raw.json` and `profiles/de-grascco.raw.json`.

**Loader warning.** In both corpora some files carry a UTF-8 BOM and the gold
offsets count it as a character. Read as plain `utf-8`; `utf-8-sig` shifts 761
MEDDOCAN spans by one. See `docs/notes/corpus-observations.md` §5.

## Before publishing anything

```
python tools/release_screen.py            # working tree
python tools/release_screen.py --history  # every blob ever committed
```

A BLOCKED count above zero means stop. See CLAUDE.md.
