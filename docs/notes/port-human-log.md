# port-human log

Time and judgement record for the `port-human` arm. This is the baseline the
agent arms are compared against, so the record matters as much as the artifacts.

Wall-clock times are measured, not estimated (`date -u` before and after each
step). All timestamps 2026-08-05 UTC.

---

## Session 1 — acquisition and inventory of es-meddocan, de-grascco

Commit at start: `cb093d5`.

| Step | Span (UTC) | Elapsed |
|------|-----------|---------|
| Resolve DOIs, identify the correct GraSCCo record | 08:37:46 → 08:40:02 | **2m 16s** |
| Write both fetch scripts, download, extract | 08:40:02 → 08:47:37 | **7m 35s** |
| Mechanical inventory (write scripts, count, verify offsets) | 08:47:37 → 08:53:33 | **5m 56s** |
| Observation note and this log | 08:53:33 → ~09:05 | **~11m** |
| **Total** | | **~27m** |

Note on interpreting these numbers: the elapsed time is an LLM agent doing the
work, not a human. For the `port-human` baseline the useful quantity is probably
the *decision count* in §3 below rather than wall clock, since a human would
spend their time differently (slower to write the counting scripts, faster to
recognise that `TERRITORIO` mixes cities and postcodes). Recorded as-is and
flagged rather than adjusted.

## 1. Where I got stuck, and how it resolved

**The MEDDOCAN DOI in the task description is a concept DOI.**
`10.5281/zenodo.4279322` returned HTTP 302, not JSON. It is the concept DOI
(always-latest); the actual record is `4279323`, v1.0, single file
`meddocan.zip`. Resolved by following redirects with `curl -L` and reading
`conceptdoi` from the API response. The fetch script pins the **version** DOI,
because a concept DOI can start resolving to different content after a new
upload — which would silently invalidate a frozen split.

**"GraSCCo" is two different Zenodo records and the obvious one is useless to us.**
Searching Zenodo for GraSCCo returns 7 hits. The record actually titled
`GraSCCo` (concept `10.5281/zenodo.6539130`, latest `18874981`) is 63 plain
`.txt` files with **no PHI annotations at all**. The annotated release is a
separate record, `GraSCCo_PII_V2` (concept `10.5281/zenodo.11502328`, version
`15747389`). A de-identification experiment needs the second one. Resolved by
listing the file names of each candidate before choosing — the un-annotated
record's file list is all `.txt`, which gave it away.

I fetched **both** records: the annotations for gold spans, and the plain text
because the `.txt` files are the authoritative newline/encoding form. Then
verified this was not wasted effort *and* not a discrepancy risk: the CAS
`sofaString` is byte-identical to the corresponding `.txt` for **all 63
documents**. Had they differed, offsets would have applied to only one of them.

**The GraSCCo annotation format was not any of the formats I expected.**
Not BRAT, not i2b2 XML — UIMA CAS JSON exported from INCEpTION, with the document
text embedded as the `sofaString` and PHI spans as `webanno.custom.PHI` feature
structures. Resolved by dumping `%TYPES` / `%FEATURE_STRUCTURES` / `%VIEWS` and
counting `%TYPE` values until the annotation layer was obvious (223 structures in
a small document: 188 Token, 17 Sentence, 13 PHI).

**A near-miss that cost the most careful minutes: BOM and offsets.**
The encoding probe reported "mixed" for MEDDOCAN, which turned out to be 32 files
with a UTF-8 BOM. That could have been a footnote. Instead I checked whether the
gold offsets count the BOM, and they do: with `utf-8-sig` (BOM stripped) **761 of
761 spans in those files fail**; read as plain `utf-8`, all 761 match. The
idiomatic-looking choice is the wrong one. GraSCCo has the same issue in 5 files,
and worse — two documents have a gold span starting at index 0, so the annotated
surface *itself* begins with U+FEFF. Recorded in both profiles and in
`corpus-observations.md` §5.

**Two shell-environment annoyances**, noted only because they cost real time:
`cd` did not persist between tool calls, so a couple of commands failed with
`no such file or directory` and had to be re-run with absolute paths; and one
`find`-based listing produced 110 KB of output (MEDDOCAN's 3,751-file background
directory) which had to be re-scoped.

## 2. What I verified rather than trusted

Every count in `profiles/*.raw.json` was measured. Where the corpus documentation
also states a number, both are recorded:

- MEDDOCAN split sizes: documented 500/250/250 + 3,751 background — **measured
  identical**.
- GraSCCo size: documented 63 — **measured 63** text, 63 JSON, 63 XMI.
- Offset convention: not taken from documentation. **All 22,795 MEDDOCAN gold
  spans** were sliced out of their `.txt` and compared to the surface string in
  the `.ann`; 22,795 matched, 0 mismatched. Conclusion: character-based, 0-based,
  end-exclusive. Same check on all 1,436 GraSCCo spans: all in-bounds, 0
  zero-length.
- GraSCCo overlap structure: **0 nested, 0 crossing** span pairs — measured, not
  assumed, because it determines whether the union merge needs containment
  handling in German.
- GraSCCo `documentId`: found to be the constant `CURATION_USER` for every
  document, i.e. an INCEpTION curation artifact and **not** a usable key. Easy to
  mistake for a document ID.

## 3. Items that needed human judgement

Listed because the count and character of these is the real `port-human` baseline.
The eight numbered decisions are deferred to the user in
`docs/notes/corpus-observations.md` §6; the rest I made and am flagging.

Deferred (user's call — they change the reported numbers):

1. `SEXO_SUJETO_ASISTENCIA`, 1,841 spans / 8.1% of Spanish gold — canonical or not
2. `NAME_TITLE` — the two corpora annotate titles incompatibly
3. `TERRITORIO` — split city/ZIP heuristically, or merge GraSCCo's two types
4. `FAMILIARES_*` — 416 spans of common nouns (`madre`, `familia`) as PHI
5. Split unit — article stem vs document; filename stem vs document
6. Reuse MEDDOCAN's official split, re-split, or report both
7. BOM policy — strip and shift, or retain U+FEFF inside gold surfaces
8. Rare types (`OTROS_*` n=22, twelve GraSCCo types with n ≤ 8) — in the
   denominator as an irreducible floor, or excluded

Decided by me while working (reversible, but they are choices, not facts):

- **Pin version DOIs, not concept DOIs**, in both fetch scripts. Reason: a concept
  DOI can change content under a frozen split.
- **Fetch the GraSCCo plain-text record in addition to the annotated one**, then
  verify byte-identity with the CAS sofa rather than assuming either is canonical.
- **Use whitespace tokenisation for the length distribution**, and label it as
  mechanical. GraSCCo also ships a dkpro `Token` layer, so both counts are
  recorded; MEDDOCAN has no token layer, and a real tokeniser would have made the
  two corpora incomparable.
- **Count MEDDOCAN types from the flat brat labels**, and record the XML's
  two-level category/TYPE pairs separately. Both are in the profile because the
  brat and XML views of the same annotations differ in structure, not content.
- **Name the files `profiles/{corpus}.raw.json`** and put a `_what_this_is` field
  at the top of each, so they cannot be mistaken for Profiler agent output.

## 4. Deliberately not done

- **Did not read `sealed/`** — it does not exist yet; no split has been made.
- **Did not read the GraSCCo annotation guide PDF**
  (`data/raw/de-grascco/schema/GeMTeX_Annoguide_DeID_2_202509.pdf`). It would
  likely resolve what `NAME_EXT` (n=1) means and may settle the `NAME_TITLE`
  question in §2.6 — but reading a 700 KB guide is a rule-development activity,
  and the canonical type set is not decided yet.
- **Did not check whether MEDDOCAN's official split is article-disjoint.** It bears
  on open question 6 and takes about ten minutes, but the answer only matters once
  the split unit is chosen.
- **Did not check whether the 3,751 background documents overlap the annotated
  1,000.** Relevant to `sup-free` later.
- **Wrote no rules and no mappings.** This session is inventory only.
