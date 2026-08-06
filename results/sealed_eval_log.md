# Sealed test-fold evaluation log

**Split-freeze commit: `30d6188dbefd9ddc11176518160a2b653a831c89`** — the commit
that added `splits/es-meddocan.json`. Everything about the seal is dated from here.

The split file was generated **before** the fold was sealed, and had to be: it
records the test fold's document ids, span counts, per-type counts and token
distribution, and none of those can be recomputed once the text is behind
`sealed/`. Sealing first would have left the file permanently unverifiable for a
third of the corpus. So the order is generate, freeze, seal — and this line is the
record that the reading happened before the seal existed, not around it.

Every evaluation on a sealed fold is appended below, one row per run, so the paper
can state how many times the test set was looked at (CLAUDE.md). A run that is not
in this table did not happen; if the count here disagrees with the paper, this file
is the authority. Rows are only ever appended — deleting one would make the
remaining rows worthless.

## What must be true before a row is added

1. The evaluation ran through `src/eval/run_sealed_eval.py`. It is the only caller
   the loader's sealed gate accepts, and no interactive session is one.
2. The append to this file **succeeded**. If it fails the evaluation does not run:
   an unlogged evaluation is worse than no evaluation, because it leaves the
   remaining rows looking complete.
3. `python3 -m src.split --corpus {corpus} --check` passed, so the fold being
   evaluated is the fold that was frozen at the commit named above.
4. The rules, mappings and checkpoints being evaluated were selected on **dev**.
   A run whose configuration was chosen by looking at test output is not a test
   evaluation; it is training, and reporting it as the former is the failure this
   log exists to make visible.

## Runs

| # | timestamp (UTC) | commit | tree | corpus | fold | arm(s) | purpose |
|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | _no sealed evaluation has been run_ |

`tree` is `clean` or `dirty`. A dirty tree means the commit hash does not describe
the code that ran, so the row is honest only if it says so.

## Why the count is the headline

The number of rows here bounds how much the test fold could have informed any
decision. One row supports "evaluated once, as pre-registered". Several rows,
honestly listed, still support a claim — but a different one, and the reader gets
to make that judgement rather than assume the first.
