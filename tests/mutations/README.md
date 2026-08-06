# Loader mutation checks

A test suite that always passes proves nothing. These checks break one loader
guarantee at a time and confirm the suite notices — so the claim "the loader is
tested" rests on evidence rather than on test count.

```
python3 tests/mutations/run.py            # all mutations
python3 tests/mutations/run.py --list     # names and expected kills
python3 tests/mutations/run.py utf8_sig   # one mutation
```

Exit 0 when every mutation is caught by at least its expected number of tests.
Not collected by pytest: it runs pytest itself, and each mutation costs a full
suite run.

## Why these particular mutations

Each one corresponds to a decision in DESIGN.md that a plausible "simplification"
would silently undo. What they have in common is the property that makes them
dangerous: **eleven of the sixteen change no total.** The corpus still loads, the
document count is still 1,000, the span count is still 22,795 — and every
downstream number is wrong. Those are the errors a reviewer cannot catch and an
aggregate cannot reveal, which is why they get a harness rather than trust.

## The loader mutations

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `utf8_sig` | `meddocan.py` reads the text with `encoding="utf-8-sig"` | BOM removed at decode time, so `strip_bom` finds nothing and applies no shift; all 761 spans in the 32 BOM files are off by one. DESIGN §9.7 | **23** |
| `no_bom_shift` | offsets are not decremented by the BOM length | same one-character error, reached from the other direction | **23** |
| `assert_offsets_noop` | `Document.assert_offsets` returns immediately | the §9.7 assertion stops asserting; counts are unaffected, so only tests that slice spans themselves can notice | **3** |
| `drop_excluded` | `load()` filters out `excluded` spans | §9.1 spans discarded instead of flagged; the canonical count stays a correct 20,538 while the reported exclusion volume becomes unmeasurable | **11** |
| `familiares_as_other` | `FAMILIARES_SUJETO_ASISTENCIA` moves from `EXCLUDED_TYPES` into `TYPE_MAP` as `OTHER` | an excluded type is scored; every span still loads and the total still reconciles to 22,795, so the corruption is entirely in *which* spans count | **7** |
| `type_in_both_lists` | the same type is added to `TYPE_MAP` while left in `EXCLUDED_TYPES` | `_check_type_map` must reject it at construction. See "What this found", below | **28** |
| `missing_test_fold` | `SPLIT_DIRS` loses its `test` entry | 750 documents load instead of 1,000; the suite must not be satisfiable by a silently truncated corpus | **16** |
| `bucket_unknown_types` | `classify()` returns `("OTHER", False)` instead of raising | an unmapped type is scored as a residual bucket. Invisible on today's corpus and waiting for the day a re-release adds a type | **1** |

## The split-file mutations

`splits/es-meddocan.json` is the seal's reference point (CLAUDE.md), so the checks
around it get the same treatment. These eight are what make the file a claim rather
than a comment — every one of them leaves all 22,795 spans loading correctly.

| mutation | changes | breaks | tests that catch it |
|---|---|---|---|
| `split_verify_noop` | `split.verify()` returns immediately | the recorded summaries stop being compared to the corpus, so a stale split file passes | **2** |
| `split_ignores_membership` | `verify()` checks the counts but not `document_ids` | a file that swapped one dev document for one test document of equal span count would verify. This is the seal violation the aggregates cannot see | **1** |
| `fold_from_directory_not_file` | `load()` skips `_apply_split_file` | folds come from the directory layout instead of the frozen file. No count changes, because the two agree today; what is lost is that the *file* decides what is sealed | **2** |
| `split_disagreement_ignored` | the corpus-vs-file fold cross-check becomes `if False` | the file silently overrides the disk, so a re-release that moved a document out of `test` is accepted without a word | **1** |
| `top_level_leak_allowed` | `check_schema` stops rejecting unknown top-level keys | corpus-specific fields may then sit beside the common ones. Nothing fails today; the schema stops being shared the first time GraSCCo's generator adds a key | **1** |
| `grouping_numeric_suffix_only` | `STEM_RE` becomes the old `^(S\d{4}-\d+)-(\d+)$` | reinstates the §9.5 bug that dropped the 31 ids with a letter in the journal prefix, so the grouping audit covers 969 of 1,000 documents and calls itself complete | **1** |
| `grouping_name_only` | §9.5 step 2 accepts a name match without a record number or date | the one stem sharing a bare given name across different surnames becomes a group, and two independent units stop being independent | **1** |
| `split_file_span_count` | `"n_spans": 5801` → `5800` **in the committed JSON** | a stale summary — which is what a re-released corpus actually produces. Direction reversed from every other mutation here: the artefact is the suspect and the code is the check | **3** |

Counts are the number of tests that fail or error, from
`tests/test_meddocan_loader.py` and `tests/test_split_file.py` (70 tests). Errors
count as kills: a mutation that breaks the module-scoped fixture takes whole tests
out, and those are caught, not uncounted.

`bucket_unknown_types` is caught by exactly one test, which is the honest number
and not a comfortable one — the guarantee has a single point of failure. It is
listed at 1 rather than padded, because the value of this table is that it says
where the coverage is thin. Seven of the split-file mutations are in the same
position.

The loader counts rose when the split tests joined the run (23→33, 11→12, 7→8,
16→29): a corrupted load fails the recount too. The `min_kills` thresholds were
left at the original figures rather than raised to match. The threshold states what
the check requires; pinning it to today's incidental total would turn every
unrelated new test into a reason to edit this file.

## Code, not just prose

The mutations are executable. The alternative — a README describing seven edits
and the counts a past run produced — was rejected for one reason: **a documented
mutation is not a check, it is a claim about a check.** Anchors drift as the
loader is refactored, and prose cannot notice when a described edit no longer
applies to the code. Two safeguards follow from that:

- **Stale anchors are reported, not skipped.** Every mutation names text that
  must exist in the target file. If a refactor removes it, the run reports
  `STALE` and exits non-zero. Without this the harness degrades into a file of
  no-ops that reports every mutation as caught — worse than no harness, because
  it produces a green result.
- **Mutations never touch the working copy.** `src/`, `tests/`, `config/` and
  `splits/` are copied to a temporary directory; `data/` is symlinked, because it
  is DUA-restricted and large, and no mutation touches a data path. An interrupted
  run cannot leave a mutated file behind. `splits/` is copied rather than
  symlinked precisely so that `split_file_span_count` can edit the committed JSON
  without touching the real one.

The maintenance cost is real but bounded: sixteen anchors, each a line or two,
and a refactor that breaks one gets a `STALE` message naming the file. That is
cheaper than the failure mode it prevents.

## What this found

The harness earned its place on the first run. `familiares_as_other` was expected
to be caught by 7 tests and was caught by 1 — because the `loader` fixture wrapped
construction in `except CorpusError: pytest.skip(...)`, so a real loader bug
(`_check_type_map` correctly rejecting a type that is both mapped and excluded)
was reported as "MEDDOCAN not available on this machine". 27 tests skipped and the
suite stayed green.

Two fixes: the fixture now resolves corpus availability *before* constructing the
loader and only skips on that, so a skip means one thing; and the two faults are
now separate mutations, since being in both lists (rejected at construction) and
being moved between them (accepted, and silently wrong) fail in completely
different ways.

Worth stating plainly: this was a defect in the tests, found by testing the tests.
The loader was correct throughout.

## Applied to every loader

**GraSCCo and CARMEN-I get the same treatment.** A loader is not finished when its
tests pass; it is finished when its tests are shown to fail on the mutations that
matter for that corpus. Both have known encoding and offset hazards already
measured in `docs/notes/corpus-observations.md`, so the mutations are largely
predictable in advance:

- **GraSCCo** — 5 BOM files, and in `Baastrup.txt` and `Dupuytren.txt` the first
  gold span starts at index 0, so the annotated surface itself begins with U+FEFF
  (DESIGN §9.7). That is a sharper version of `utf8_sig` than MEDDOCAN offers,
  where no BOM-file span starts at 0. Also `NAME_TITLE`, its §9.1 exclusion, for
  `drop_excluded` and `familiares_as_other`.
- **CARMEN-I** — the masked and replaced variants have different offsets for
  1,533 of 2,000 documents, so a mutation that loads the annotations of one
  variant against the text of the other belongs in the list. So do the two
  undecided type mappings, for `bucket_unknown_types`. And because it is
  DUA-restricted, add one mutation asserting the reverse direction: that putting a
  span surface into an exception message is caught (CLAUDE.md forbids it, and
  `test_offset_mismatch_message_quotes_no_surface` is the check that must fail
  when it happens).

The split-file mutations transfer with more force, not less. Both corpora need
constructed splits rather than adopted ones (§9.5), which adds failure modes
MEDDOCAN does not have: a seed that is recorded but not used, stratification that
is claimed but not achieved, and — for CARMEN-I — a grouping rule that must reject
189 candidate groups on measured grounds. `grouping_name_only` and
`grouping_numeric_suffix_only` are the templates for those, and
`test_the_committed_file_contains_no_span_surface` is the one that must be run
against CARMEN-I before its split file is committed, because that is the corpus
where the generator would be writing restricted text into a file the release
screener reports as allowed.

Mutation counts will differ per corpus and the table is expected to grow a column
rather than be replaced.
