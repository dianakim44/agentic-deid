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

The five conditions below are what this file asserts about every row it holds. The
operational version — what to run immediately before an opening, in what order, and
what the answer has to be — is `docs/notes/sealed-eval-preflight.md`, which also
records what happens to a run that fails *after* the row exists.

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
5. The arm and the round are the ones **DESIGN §6.4** permits: one opening per
   reported arm, after that arm has terminated by its own pre-registered rule, and
   the round scored is the arm's **final** round. That section was written on
   2026-08-26, while the run table below was still empty — which is the only
   moment at which it could be written without the numbers in view.

## Runs

| # | timestamp (UTC) | commit | tree | corpus | fold | arm | round | purpose |
|---|---|---|---|---|---|---|---|---|
| 1 | 2026-08-28T07:29:58Z | 496df71476a47c35396491d620119121c238c668 | clean | es-meddocan | test | R/sup-free/port-loop | 8 | port-loop arm terminated at iteration 8 (ceiling); sole pre-registered opening for this arm per DESIGN §6.4 |
| 2 | 2026-10-12T04:08:40Z | aa2ce344c33a17a6646908ddb80a4c8c9f8a11db | clean | es-meddocan | test | R/sup-free/port-loop-noaudit | 8 | pre-registered single test evaluation of the Auditor-ablation arm at its ceiling round (B1a; docs/notes/arm-port-loop-noaudit-es.md §9) |
| 3 | 2026-10-15T00:52:09Z | 68e3e3a3e80dc436ce9b555de6d358ff1b0c27d6 | clean | de-grascco | test | R/sup-free/port-oneshot | 1 | v24 §5: sole pre-registered opening of the single-call rule arm on de-grascco, for the R column of the five-arm comparison (DESIGN §12, experiments/hybrid_v24/registration.yaml). MEDDOCAN R is already open and is not reopened. |
| 4 | 2026-10-15T00:52:25Z | c46ce9f4d3ffdf11531df51bc012ab52bb6ddd7e | clean | es-carmen | test | R/sup-free/port-oneshot | 1 | v24 §5: sole pre-registered opening of the single-call rule arm on es-carmen, for the R column of the five-arm comparison (DESIGN §12, experiments/hybrid_v24/registration.yaml). MEDDOCAN R is already open and is not reopened. |
| 5 | 2026-10-15T00:52:26Z | 363f8191d81b2a6954fe91a87ef04f9dada201ac | clean | en-deid | test | R/sup-free/port-oneshot | 1 | v24 §5: sole pre-registered opening of the single-call rule arm on en-deid, for the R column of the five-arm comparison (DESIGN §12, experiments/hybrid_v24/registration.yaml). MEDDOCAN R is already open and is not reopened. |
| 6 | 2026-10-15T00:52:28Z | 7b91c5c8df1872bf92d04447b9c2c8d2546a92bf | clean | ko-surro | test | R/sup-free/port-oneshot | 1 | v24 §5: sole pre-registered opening of the single-call rule arm on ko-surro, for the R column of the five-arm comparison (DESIGN §12, experiments/hybrid_v24/registration.yaml). MEDDOCAN R is already open and is not reopened. |
| 7 | 2026-10-15T02:37:33Z | 8d4053e9ddc792f7e46c9f92be9f5ea0fcc6542f | clean | es-meddocan | test | RT/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT arm on es-meddocan (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 8 | 2026-10-15T02:37:57Z | 9c649df8aed4760099964a9d06ab830df31c4dc1 | clean | es-meddocan | test | RT-Sel/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT-Sel arm on es-meddocan (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 9 | 2026-10-15T02:38:21Z | f1620f0ec20aad8fafa429940beb91bfe5323544 | clean | es-meddocan | test | RT-Arb/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT-Arb arm on es-meddocan (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 10 | 2026-10-15T02:38:45Z | cecb73f54676fc2dbdcb45f25d02b317cd24afd0 | clean | de-grascco | test | RT/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT arm on de-grascco (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 11 | 2026-10-15T02:38:51Z | 485c5e7d0e9ed7bee03ec5a9d77c9df6339409ab | clean | de-grascco | test | RT-Sel/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT-Sel arm on de-grascco (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 12 | 2026-10-15T02:38:57Z | db8e8ad1a574a0a367cac7bbab45c206b087c095 | clean | de-grascco | test | RT-Arb/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT-Arb arm on de-grascco (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 13 | 2026-10-15T02:39:05Z | 6e0cdcd33de485759ccfe4224cfd182f3641f551 | clean | es-carmen | test | RT/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT arm on es-carmen (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 14 | 2026-10-15T02:39:52Z | 2c350f74050390ff18e5cf01c7dfe4addb5331ef | clean | es-carmen | test | RT-Sel/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT-Sel arm on es-carmen (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 15 | 2026-10-15T02:40:37Z | 58a5746e248db2d98a3f4798d9cbbd78d9d1d770 | clean | es-carmen | test | RT-Arb/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT-Arb arm on es-carmen (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 16 | 2026-10-15T02:41:24Z | 86e86a68b4f72a904294603ecb8943125a28a46f | clean | en-deid | test | RT/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT arm on en-deid (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 17 | 2026-10-15T02:42:26Z | 6f57a4e231d3e922e8d5cb8fde4dd56418fb8f10 | clean | en-deid | test | RT-Sel/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT-Sel arm on en-deid (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 18 | 2026-10-15T02:43:28Z | c4e10687558846572a8615a9b6c3c62b4b8b2a8f | clean | en-deid | test | RT-Arb/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT-Arb arm on en-deid (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 19 | 2026-10-15T02:44:31Z | ada0a31a1258d0386bf64aa6be3a5178bdc3e182 | clean | ko-surro | test | RT/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT arm on ko-surro (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 20 | 2026-10-15T02:45:31Z | 8c7d1a964862a9bc5f09aaebf87d5759ceadb112 | clean | ko-surro | test | RT-Sel/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT-Sel arm on ko-surro (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 21 | 2026-10-15T02:46:31Z | f45f23e4b0b08d3ccc69d57900a42043eed963ec | clean | ko-surro | test | RT-Arb/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the RT-Arb arm on ko-surro (DESIGN §12, registration.yaml). Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 22 | 2026-10-15T02:56:51Z | 6d20cfb48de5f6007cccd5c126a627ef0db94fa0 | clean | es-meddocan | test | T/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the tagger-only arm on es-meddocan (DESIGN §12). rules_source none_declared per the §12.7 amendment; dev reproduction verified byte-identical before opening. |
| 23 | 2026-10-15T02:57:14Z | 940b3ff7881afa16b598e2823e48da7a72b34a33 | clean | de-grascco | test | T/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the tagger-only arm on de-grascco (DESIGN §12). rules_source none_declared per the §12.7 amendment; dev reproduction verified byte-identical before opening. |
| 24 | 2026-10-15T02:57:21Z | bae1342b6afec94c3644f389f2f0e00510e782bd | clean | es-carmen | test | T/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the tagger-only arm on es-carmen (DESIGN §12). rules_source none_declared per the §12.7 amendment; dev reproduction verified byte-identical before opening. |
| 25 | 2026-10-15T02:58:06Z | e262ce8dfe152f6a7cb8fa5b86b8ee5540633a7f | clean | en-deid | test | T/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the tagger-only arm on en-deid (DESIGN §12). rules_source none_declared per the §12.7 amendment; dev reproduction verified byte-identical before opening. |
| 26 | 2026-10-15T02:59:08Z | cd30034549393bf58514a48742b791474e72912b | clean | ko-surro | test | T/sup-human/port-hybrid | 1 | v24 §5: sole pre-registered opening of the tagger-only arm on ko-surro (DESIGN §12). rules_source none_declared per the §12.7 amendment; dev reproduction verified byte-identical before opening. |
| 27 | 2026-10-16T13:31:07Z | 2d27d9552c17fda6cb405b93dc095ff45e78644c | clean | es-meddocan | test | T/sup-transfer/port-hybrid | 1 | v24b §13.5: sole pre-registered opening of the T-loco arm on es-meddocan (DESIGN §13, experiments/loco_v24b/registration.yaml). Leave-one-corpus-out tagger, seed 1, holdout es-meddocan, weights named by SHA-256 in the arm's record; its dev labels were used in neither training nor epoch selection. Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 28 | 2026-10-16T13:32:31Z | 17542151b7271b60f75cd5a3aa2c3386fc204c2a | clean | es-meddocan | test | RT/sup-transfer/port-hybrid | 1 | v24b §13.5: sole pre-registered opening of the RT-loco arm on es-meddocan (DESIGN §13, experiments/loco_v24b/registration.yaml). Leave-one-corpus-out tagger, seed 1, holdout es-meddocan, weights named by SHA-256 in the arm's record; its dev labels were used in neither training nor epoch selection. Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 29 | 2026-10-16T13:32:55Z | 5f54e5fe23c7718294ee6cbdbcd6e4171f627870 | clean | de-grascco | test | T/sup-transfer/port-hybrid | 1 | v24b §13.5: sole pre-registered opening of the T-loco arm on de-grascco (DESIGN §13, experiments/loco_v24b/registration.yaml). Leave-one-corpus-out tagger, seed 1, holdout de-grascco, weights named by SHA-256 in the arm's record; its dev labels were used in neither training nor epoch selection. Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 30 | 2026-10-16T13:33:07Z | 6b066d8c76789859b88a670301a8edf031120432 | clean | de-grascco | test | RT/sup-transfer/port-hybrid | 1 | v24b §13.5: sole pre-registered opening of the RT-loco arm on de-grascco (DESIGN §13, experiments/loco_v24b/registration.yaml). Leave-one-corpus-out tagger, seed 1, holdout de-grascco, weights named by SHA-256 in the arm's record; its dev labels were used in neither training nor epoch selection. Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |
| 31 | 2026-10-16T13:33:14Z | ee5593ea11e99d46f187d8197d533ef5f26b5ddb | clean | es-carmen | test | T/sup-transfer/port-hybrid | 1 | v24b §13.5: sole pre-registered opening of the T-loco arm on es-carmen (DESIGN §13, experiments/loco_v24b/registration.yaml). Leave-one-corpus-out tagger, seed 1, holdout es-carmen, weights named by SHA-256 in the arm's record; its dev labels were used in neither training nor epoch selection. Scored through the §12.7 hybrid branch; dev reproduction verified byte-identical before opening. |

`tree` is `clean` or `dirty`. A dirty tree means the commit hash does not describe
the code that ran, so the row is honest only if it says so.

`arm` is `detector/supervision/porting` — the corpus column completes the four-axis
coordinate — and `round` is the round scored. Neither has a default: until 2026-08-26
the arm cell was a constant in the driver reading `none (access check)`, which was
true of every row this table could then have held and would have stayed true-looking
in the row that carried the experiment's only test score. They are validated against
`config/naming.yaml` and against the arm's committed termination record before the
append, because the append is what makes the read reachable.

## Why the count is the headline

The number of rows here bounds how much the test fold could have informed any
decision. One row supports "evaluated once, as pre-registered". Several rows,
honestly listed, still support a claim — but a different one, and the reader gets
to make that judgement rather than assume the first.
