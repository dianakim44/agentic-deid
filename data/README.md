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
| `es-meddocan` | Zenodo (MEDDOCAN shared task) | Spanish | clinical case studies | open | pending download |
| `de-grascco` | Zenodo (GraSCCo) | German | GP letters | open | pending download |
| `es-carmen` | PhysioNet (CARMEN-I) | Spanish / Catalan | discharge, referral, radiology | PhysioNet credentialed + DUA | request pending |
| `en-n2c2` | n2c2 / DBMI data portal | English | longitudinal progress notes | DUA | on hold, portal unavailable |

`ko-surro` is a surrogate corpus derived from a PhysioNet source release. It is cited
as a data source; the code lineage is a separate project.

## Licence terms

| Corpus ID | Terms | Redistribution |
|-----------|-------|----------------|
| `ko-surro` | PhysioNet DUA, inherited from the source release | forbidden |
| `es-meddocan` | open, per the Zenodo record | permitted by the licence, **not done here** |
| `de-grascco` | open, per the Zenodo record | permitted by the licence, **not done here** |
| `es-carmen` | PhysioNet DUA | forbidden |
| `en-n2c2` | n2c2 DUA | forbidden |

Openly licensed corpora are not committed either. One acquisition path for every
corpus is simpler to keep honest than a mixed policy, and it removes the chance of a
DUA-restricted file landing in a directory that looks publishable.

## Local layout

Populated by the acquisition scripts. None of these paths are committed.

```
data/
  README.md              this file — the only committed thing here
  source/{corpus}/       as downloaded, unmodified
  derived/{corpus}/      surrogate substitution, tagging, offset extraction
sealed/{corpus}/         test fold. not read during development
splits/{corpus}.json     patient-disjoint folds, frozen and committed
```

`splits/{corpus}.json` is the exception: it holds group IDs and fold assignments,
no note text, and is committed before any rule is written.

## Before publishing anything

```
python tools/release_screen.py            # working tree
python tools/release_screen.py --history  # every blob ever committed
```

A BLOCKED count above zero means stop. See CLAUDE.md.
