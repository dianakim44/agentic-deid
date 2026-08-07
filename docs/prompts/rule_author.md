# RuleAuthor prompt — `rules/{lang}.yaml`

> **`port-human` is retired (2026-08-07, DESIGN §11) and the baseline is now
> `port-oneshot`.** §§7–8 below bound that arm and are therefore dormant, not deleted:
> they record what this file decided for the human window, fixed before any dev document
> was read for rule-writing. Read them as the pre-registration a revived human arm
> inherits, not as live constraints.
>
> **This file still fixes every agent arm's dev window,** which is the part that was never
> about `port-human`. §§1.3–1.5 specify what one RuleAuthor invocation is shown, and the
> `port-oneshot` / `port-loop` comparison is only interpretable if both arms are shown the
> same blocks from the same code path (DESIGN §4). Changing §1 changes what the ladder is
> measuring. The prompt is hashed into `window_freeze.json`, so an edit is visible as drift
> rather than as nothing.
>
> **`port-oneshot` reads this file with §§1.3 and 1.4 empty**, because it has no previous
> iteration to draw scores or error spans from. That is the arm, not a gap in the prompt:
> the baseline's whole definition is one call with no feedback. What it must not do is
> silently receive a *substitute* for those blocks — a profile summary or a type
> inventory standing in for the score block would make the baseline something other than
> the no-feedback floor the ladder needs.

The RuleAuthor is one agent with one artifact: `rules/{lang}.yaml` (DESIGN §3). It does
not score, does not arbitrate, does not audit, and does not decide when to stop — the
orchestrator is deterministic code and owns iteration, budget, and termination.

`{lang}` is a value of the `lang` axis in `config/naming.yaml`, and it is the language of
the **rule file**, not of the corpus or of any document. One corpus may load several
files (DESIGN §5.2: `es-carmen` loads `es` and `cat`), so an invocation of this agent
targets exactly one file and never edits another.

---

## 1. Input — what the agent is shown

Four blocks, in this order. Everything here is derived from the **dev fold only**; the
loader physically cannot supply anything else (DESIGN §6.1, and `load()` returns only
unsealed folds).

### 1.1 Task frame

- the target `{lang}` and the corpora that load this file (from `corpus_rule_langs`)
- the canonical `phi_type` values, verbatim from `config/naming.yaml`, with the
  one-line gloss each carries there
- the `layer` values this agent may write, and what distinguishes them
- **that `OTHER` is not a rule-development target** — `naming.yaml` says so, and an
  agent given a residual bucket will write rules into it

### 1.2 Current rule file

The full current `rules/{lang}.yaml`, or a statement that it is empty on iteration 1.
Full text rather than a summary: the agent edits this file, and an agent editing a
summary of a file produces a diff that does not apply.

### 1.3 Scores from the previous iteration

The dev `metrics.json` block for the arm, reduced to what a rule author can act on:

- per-`phi_type` leak rate and `precision` / `recall` / `f1`, both modes
- the complementarity breakdown per type — including `joint_only` and, in the layer
  view, `covered_by_union_only` (DESIGN §9.3), because a type covered only jointly is a
  different problem from a type covered by nothing
- `duplicate_predictions`, so the agent can see when it is re-finding what another layer
  already found
- the `by_rule` block: per `rule_id` in this file, its declared layer, how many spans it
  emitted (`fires`), and its `tp` and `fp`

Per-rule counts are the block that makes revision possible rather than only addition. An
agent that sees only aggregates can add rules; an agent that sees which of *its own*
rules misfires can delete one. Rule files that only grow are the failure mode here.

Two properties of that block the prompt states outright, because both change what the
agent should conclude from it:

- **`fp` is unmatched-in-the-assignment, not uncovered.** A rule's span that overlaps a
  gold identifier but loses the assignment to a better-overlapping prediction is a false
  positive for that rule. So a rule can show `fp` on spans that did help hide something,
  and that is the intended reading: the rule contributed nothing the arm did not already
  have. A rule whose `tp` stays near zero across iterations while `fires` is high is a
  deletion candidate, not a near miss. (DESIGN §9.3 records why this is computed inside
  the scorer rather than joined on afterwards.)
- **`by_rule` totals do not sum to the arm's `tp`/`fp`.** Tagger spans have no `rule_id`
  and are absent; a span two rules both emitted is credited to both. The agent is told
  not to reconcile the two, because an agent that tries will conclude one of them is
  wrong.

A rule that fired nothing has no row at all — the scorer never reads the rule file. The
agent holds the file, so it is the one component that can tell "matched nothing" from
"does not exist", and a missing row for a rule it wrote last iteration means that rule is
inert.

### 1.4 Error spans — `n` per iteration

**This is the block that fixes `port-human`'s window, and it is the decision this file
exists to make.**

`n` error spans drawn from the previous iteration's scorer output, by the **seeded**
selection §1.5 fixes, recorded with the run. Each span is shown as:

```
type      NAME                      canonical phi_type of the gold span
error     missed | false_positive    missed = leaked under fully_covered
context   …±120 characters of dev text around the span…
offsets   (start, end) within that context window
detected  es:doctor_prefix (context_cue)   present only for false positives
```

**The surrounding text is included, and that is a deliberate cost.** A rule author who
cannot see the words around a missed identifier cannot write a context cue for it — the
`context_cue` layer is defined by trigger words in the surrounding text, so withholding
that text would make one of the four layers unauthorable and quietly convert the `R` arm
into a weaker thing than it claims to be. The cost is that dev text enters an LLM prompt,
which has two consequences taken knowingly:

1. **DUA corpora may not permit it.** Where corpus text cannot be sent to an external
   API, `n` for the agent is 0 on that corpus and the arm either runs on a local model or
   is reported as not run. DESIGN §11.1's "DUA inversion" asymmetry is exactly this case,
   and it is recorded per corpus rather than assumed away.
2. **`n` is bounded, not unbounded.** The bound is what makes the human's window
   specifiable at all. A prompt carrying "all dev errors" would give `port-human` an
   unspecifiable window and destroy the control.

**`n` = 40 per iteration** — `config/sampling.yaml: n_error_spans`, which is the value
and not a description of it — stratified by `phi_type` in proportion to each
type's share of current errors, with at least 1 per type that has any error, and a
seeded draw within each stratum. Rationale: 40 spans at ±120 characters is a few thousand
tokens — a sample large enough that a per-type pattern is visible, small enough to leave
prompt room for §1.2 and §1.3, and small enough that a person can read it in bounded time
across the iterations the arm will run. Stratification rather than a uniform draw because
a uniform draw over an error distribution dominated by `NAME` and `DATE` would show a
sparse type (DESIGN §9.4) zero times in most iterations, and a type never seen is a type
never fixed. **`n` and the stratification rule are experimental parameters and are
recorded in the results, not left implicit in this file's revision history.** They live in
`config/sampling.yaml`, and §1.5 is what makes them binding rather than descriptive.

### 1.5 How the sample is drawn — the seed

**The seed is derived from the iteration number, deterministically, and from nothing that
varies between runs.** `src/sample.py`:

```python
seed = sha256(seed_scheme || base_seed || corpus || iteration)   # first 8 bytes
```

with `seed_scheme` and `base_seed` from `config/sampling.yaml`, the four parts joined by a
delimiter that cannot occur in them. Three inputs are **forbidden**, and each is forbidden
because it fails without a symptom:

- **execution time.** A clock-derived seed makes every run's sample a new sample, and the
  recorded seed then documents a draw nobody can repeat. This is the failure that looks
  most like working code, because the sample is recorded and the record is accurate.
- **call order.** A module-level `Random`, or one RNG walked across strata, makes each
  draw depend on what was drawn before it. Adding one `NAME` error would change which
  `DATE` spans appear — churn between iterations that reads as the sampler doing its job.
  `sample.py` seeds one RNG per stratum from `(seed, phi_type)` for exactly this reason.
- **process RNG state.** `random.seed()` called anywhere earlier in the process, or
  Python's per-process string hash salt. A seed built on `hash()` is stable within a run
  and different in the next, so *every same-process test of determinism passes*. That is
  why `tests/test_sample.py` checks the seed from a **fresh subprocess** rather than
  twice in one.

A fourth input is forbidden for a different reason: **the seed does not depend on the
arm.** `port-loop` and `port-human` at iteration 3 call `sample_seed(corpus, 3)` and get
the same number.

The error *pool* differs between arms — each draws from its own current errors, and at
iteration 3 the two arms have got different things wrong. That is the experiment, not a
flaw in it. **What must be identical is the drawing procedure**: the same stratification,
the same `min_per_type`, the same ordering of the pool before drawing, the same seed for
the same iteration. **That the two arms drew by the same procedure at the same iteration
is the premise this whole comparison rests on** (DESIGN §11.1). If the procedures differ,
then a difference in what the two arms saw is not attributable to a difference in what
they had got wrong, and there is no analysis that separates the two afterwards.

One consequence worth stating because it is easy to implement wrongly: **the pool is
sorted before drawing.** Without it the seed pins the *indices* drawn and the caller's
iteration order pins which spans those indices land on — so rebuilding a dict reshuffles
the sample while the recorded seed is unchanged. Reproducible in the log, different in
fact, which is the worst of the available outcomes.

The draw is fixed in code, not described here and reimplemented per arm: one procedure
implemented twice is two procedures. `provenance()` writes the seed, the scheme, `n`,
`context_chars` and `min_per_type` beside the metrics — the seed **as a value**, even
though it is derivable, because a record holding only the inputs agrees with any
derivation scheme and would not notice the scheme changing.

---

## 2. Output — `rules/{lang}.yaml`

The agent's entire output is this file. It emits the complete file, not a patch: a patch
requires the agent to have tracked line numbers it cannot see, and a malformed patch
fails in a way that looks like a bad rule rather than a bad edit.

```yaml
version: 3                    # integer, incremented on every emission
lang: es                      # must equal {lang}; a mismatch is rejected
rules:
  - rule_id: doctor_prefix    # unprefixed here; loader prepends "es:" (DESIGN §3)
    layer: context_cue        # a `layer` axis value — required, never inferred
    phi_type: NAME            # a `phi_type` axis value — the span's canonical type
    cue: ["Dr.", "Dra."]      # the matcher: cue words, then what follows them
    then: capitalised_words   # no regex here — see the table below
    score: 0.8                # optional confidence, recorded on the span
    comment: >                # optional rationale; no corpus surface forms
      Title-prefixed clinician name. Cue is the title, not the name.
  - rule_id: dni
    layer: regex_checksum
    phi_type: ID
    pattern: '\b\d{8}[A-Z]\b'   # the regex form
    checksum: dni_mod23       # arithmetic in the engine, named here
    flags: [unicode]          # optional; from a fixed allowlist
```

**Required fields per rule: `rule_id`, `layer`, `phi_type`, and one matcher.** Each is
required for a reason that is not stylistic:

- **`layer`** — DESIGN §3 requires the detector that emits a span to set its layer
  explicitly, with no derivation from names, prefixes, or lookup tables. The rule
  declares it; the engine copies it onto the span. A rule without a layer would force the
  engine to guess, and a guessed layer silently re-attributes DESIGN §7's per-layer
  results.
- **`rule_id`** — written **without** the language prefix. The loader prepends `es:` /
  `cat:` from the file it read, because the prefix identifies the *file* and a rule cannot
  know which file it will be loaded from. An agent that writes `es:doctor_prefix` into an
  `es` file produces `es:es:doctor_prefix`, so this is validated rather than trusted.
- **`phi_type`** — a value of the `phi_type` axis. Not free text, and not a
  corpus-specific tag: mapping corpus taxonomies to canonical types is the Mapper's
  artifact, and a RuleAuthor inventing a type would put an undeclared value into a
  results field.
- **a matcher** — exactly one per rule, and *which* form depends on the layer. See the
  table below. `pattern` is the regex form and is not required when another form is used;
  two forms in one rule is rejected, because which one fired would go unrecorded and
  §1.3's `by_rule` attributes to a rule rather than to a form.

### The three matcher forms, and which layers need regex

| layer | form | example |
|---|---|---|
| `gazetteer` | `terms:` — a list of **literal strings**, or `lexicon: {lang}/{name}` for a long list | `terms: ["Hospital Clínic", "C.S. Norte"]` |
| `context_cue` | `cue:` + `then:` — the words before, and what follows | `cue: ["Dr.", "Dra."]` / `then: capitalised_words` |
| `regex_checksum` | `pattern:` + optional `checksum:` | `pattern: '\b\d{8}[A-Z]\b'` / `checksum: dni_mod23` |

**`gazetteer` needs no regex, and `context_cue` needs none for the ordinary case.** This is
a property of the schema rather than a convenience: §7's prediction is about layers, so a
layer that is harder to write is a layer that looks weaker for a reason unrelated to the
phenomenon.

- **`terms:` are literal.** They are escaped, so `C.S. (Norte)` — an ordinary institution
  name and a broken regex — is a valid term. Order does not matter: the engine sorts
  longest-first, because regex alternation is first-match and `Hospital` placed before
  `Hospital Clínic` would make the longer term unreachable while the rule still fired
  with a span that is silently too short. Matching is case-folded by default (that is what
  the layer *is*, per `naming.yaml`); `case_sensitive: true` opts out.
- **`lexicon: es/institutions`** reads `lexicons/es/institutions.txt`, one term per line,
  `#` for comments. Use it when the list is long; it is the LexiconBuilder's artifact and
  duplicating it inline would give two files one job. The `{lang}` in a lexicon reference
  is the lexicon's own language and need not equal the rule file's.
- **`cue:` + `then:`** replaces a hand-written lookbehind. The two halves are given in
  reading order and the engine assembles them, putting the match group on `then` alone —
  so the emitted span covers the identifier and **not** the cue. That matters for scoring:
  a span starting at `Dr.` is compared against gold starting at the name, and misses under
  `fully_covered` while passing `relaxed`.

  `then:` takes either a shorthand or a regex. The shorthands: `capitalised_word`,
  `capitalised_words` (one to four, for full names), `number`, `digits` (digits with
  `.`/`-`/`/`, for dates and identifiers), `word`, `rest_of_line`. `gap:` bounds the
  whitespace and punctuation permitted between cue and target (default 3, max 40).
- **`checksum:`** names an algorithm; the arithmetic is in the engine, not in YAML.
  Implemented: `dni_mod23`, `nie_mod23`, `luhn`, `mod10`. A match failing the check does
  not become a span. Declaring a checksum on a layer other than `regex_checksum` is
  rejected — the layer names the mechanism a span came from, so a check digit under
  another layer would send §7's per-layer result to the wrong mechanism.
- **`pattern:`** is the **`regex` module's** dialect, not `re`'s. `\p{Lu}`, `\p{L}` and
  variable-length lookbehind are available. `flags:` is an allowlist — `unicode`,
  `ignorecase`, `multiline`. There is no `dotall`: it would let `.` cross a note's line
  boundaries and widen every rule in the file at once.

**The loader refuses rather than matching nothing.** A missing lexicon, an unimplemented
checksum, a duplicate `rule_id`, a mismatched `lang:`, an `OTHER` target, or a lexicon name
that could leave `lexicons/` — each raises at load, with the rule id and no quoted pattern
body. The reason is specific to this project: a rule that loads and matches nothing is
indistinguishable from a phenomenon that does not occur, and that is exactly what §7
reports as a negative result.

### Checking a rule's effect

    python tools/check_rules.py --corpus es-meddocan

Per rule: how many of this iteration's drawn window it covers, how many times it matched,
and how many of those matches hit no gold span. Then the same coverage dev-wide, reported
separately — a rule's effect on spans the sample never showed is real but is not feedback.
`--rule-id es:doctor_prefix` isolates one rule; `--verbose` lists each false positive as a
document id and a character range, with no text. `--rules PATH` runs a file elsewhere,
which is how a rule is tried without writing to `rules/{lang}.yaml`.

No precision or F1 is printed. Those come from the scorer over a merged prediction set
(DESIGN §9.3); a ratio computed here over one unmerged rule file would carry the same name
as the real number and a different value. The fold is always dev and there is no flag to
change it.

`rule_id` must be unique within the file, and stable across iterations: the per-rule
counts in §1.3 and the `rule_id` on every emitted span are the same identifier, so
renaming a rule silently breaks the attribution history. A changed rule keeps its ID; a
genuinely different rule gets a new one.

---

## 3. Tools

Allowed:

| tool | why |
|---|---|
| **run detection on dev** (`tools/check_rules.py`, §2) | DESIGN §3 names this as the RuleAuthor's tool use, and it is what distinguishes these agents from the single-shot LLM steps §3's prior-evidence note demoted. The same command the `port-human` author runs, so the feedback loop is one of the things held constant across the two arms rather than one of the things that differs. |
| **read the scorer's dev output** | The same `metrics.json` block as §1.3, on demand rather than only as given. |
| **read its own current rule file** | So a long iteration does not depend on prompt recall. |
| **regex compile / dry-run against provided sample text** | Catches a malformed pattern before it costs a scoring round. |

Not allowed, and each for a distinct reason:

- **Reading the corpus directly.** Dev text arrives only as the §1.4 sample. Direct
  corpus access would make the agent's window unbounded and unspecifiable — the same
  property that would destroy `port-human`'s comparability, applied to the agent side.
- **Writing any file other than `rules/{lang}.yaml`.** DESIGN §3: one agent, one
  artifact, and two agents never write the same file.
- **Reading or writing `results/`.** Scoring is deterministic code and an agent that can
  edit its own scores is not measurable.
- **Network access.** A rule file that depends on a fetched resource is not reproducible
  from the repository.
- **Calling another agent.** There is no manager agent; the orchestrator sequences.

---

## 4. Prohibitions

These are stated to the agent directly, and each is also enforced outside the agent —
because a prohibition that exists only in a prompt is a request.

1. **`sealed/` is never read, listed, or referred to.** The test fold is not reachable:
   it is physically outside the corpus root, `load()` returns unsealed folds only, and
   `_assert_no_sealed_fold` raises if sealed documents appear in an ordinary load. If a
   path under `sealed/` appears in this agent's context, that is a harness bug to report,
   not a resource to use. Rules developed against test are the same leakage as training
   on test (DESIGN §6).

2. **No corpus surface form in any output.** Not in `comment`, not in `rule_id`, not in
   any log line. This is the rule with the most tempting exception, so it is stated in the
   form that survives it: a comment reading "matches the phrase *…*" republishes that
   phrase into a **public repository**, and `tools/release_screen.py` does not reach
   prompt output, terminal logs, or issue text. Patterns are a partial exception by
   necessity — a `context_cue` regex contains the cue words, which is the point of the
   layer — but the cue is a clinical formula (`Dr.`, `nacido el`, `Hospital`), never a
   patient identifier. **A pattern that would only match one individual is a memorised
   span, not a rule**, and it is rejected as both a privacy violation and a broken
   generalisation.

   **`rule_id` is covered by this, and it is screened.** A rule name is published twice:
   once in `rules/{lang}.yaml`, and again in the `by_rule` block of every `metrics.json`
   the arm produces (§1.3, DESIGN §9.3) — and `metrics.json` is on the screener's
   *allow* list, since it is meant to be publishable. So a surname in a rule name reaches
   a public results file by the intended path, with nothing in the way.

   Forbidding surface forms in the pattern while leaving the name open is a bypass, not
   an oversight in the drafting. The name is free text, an agent writes it, and naming a
   rule after the thing it was written from is the natural thing to do — `es:perez_ruiz`
   for a rule that came from one hard case is a *helpful* name. That is the whole
   difficulty: this prohibition is bypassed most easily by an author trying to be clear.

   `tools/release_screen.py` therefore checks the `rule_id` values in `rules/*.yaml`, and
   the check is a **positive mechanism vocabulary**, not a list of names to reject. The
   criterion is this prohibition's own: a clinical formula is allowed, designating an
   individual is not. Two things follow from trying to implement it any other way —

   - **Shape cannot do it.** `perez_ruiz` and `street_type` are both two lowercase ASCII
     tokens with no digits. No property of the string separates them. Shape rules catch
     the crude cases (a capital, a run of digits, a non-ASCII word) and are kept for the
     better error message, but they are not the check.
   - **A blacklist would be self-defeating.** Drawing this boundary by listing the names
     it objects to means storing surface forms in the repository, which is the thing
     being prevented.

   The vocabulary inverts that: it lists the words a mechanism name may be built from —
   targets (`name`, `date`, `street`, `hospital`) and mechanisms (`prefix`, `cue`,
   `checksum`, `gazetteer`) — so **a name assembled only from mechanism words cannot
   designate an individual.** That makes the prohibition a property of the name rather
   than a correlation the screener happens to notice, which is the only version of it
   worth relying on. A name whose tokens are all in the vocabulary is fine; anything else
   is renamed. Rejection is cheap and irreversible publication is not, so the check is
   deliberately biased toward rejecting. `rules/{lang}.yaml` needing a word the
   vocabulary lacks is a one-line addition to a committed file — the same cost
   `naming.yaml` already imposes on every axis value, and it produces a diff someone can
   object to.

   The mutation `rule_id_vocabulary_not_checked` removes the vocabulary and leaves the
   shape rules, which is the version this screener was first written as: it passes every
   legitimate name, and it also passes `es:perez_ruiz`.

3. **Only `config/naming.yaml` vocabulary.** `layer`, `phi_type`, and `lang` values come
   from the axes. A new value is added to `naming.yaml` first, by a human, in a commit —
   never coined in a rule file. This is validated on load rather than trusted, since an
   undeclared `phi_type` would otherwise reach a results path.

4. **No rule targeting `OTHER`.** It is a residual bucket a corpus ships, declared in
   `naming.yaml` as not a rule-development target.

5. **No claim about the test fold, and no prediction of final performance.** The agent
   sees dev; anything it says about generalisation is unfalsifiable from its own inputs.

---

## 5. Iteration protocol

The orchestrator drives the loop; the agent is invoked once per iteration and returns one
file. Per iteration:

1. Orchestrator runs detection on dev with the current rules and scores it.
2. Orchestrator runs the Auditor on the **de-identified output** — the Auditor never sees
   gold (DESIGN §3), so its flags are suspicions, not errors.
3. Orchestrator assembles this prompt: task frame, current file, scores, `n` sampled
   error spans, and the Auditor's report.
4. Agent emits the complete revised `rules/{lang}.yaml`.
5. Orchestrator validates, commits the file, and scores the next iteration.

### How the Auditor's report is consumed

`reports/leaks_{iter}.json` is a list of spans the Auditor believes are surviving PHI in
the output. It is a **second opinion from a component that cannot see the answer**, and
the prompt says so plainly, because the failure mode is an agent treating audit flags as
ground truth and writing rules to satisfy a peer rather than the corpus.

Three cases, and the third is the one worth naming:

- **Flagged and gold-missed** — corroborates a real miss; strongest signal available.
- **Flagged, not in gold** — either a gold annotation gap or an Auditor false positive.
  The agent may not resolve this and may not write a rule for it on the Auditor's word
  alone; it is logged for human review. DESIGN §9.1's excluded types make this genuinely
  ambiguous rather than merely uncertain.
- **Not flagged but gold-missed** — both mechanisms missed it. These are the highest-value
  cases in the whole loop and the easiest to skip, since nothing in the Auditor's report
  points at them. They come from the §1.4 sample, which is why the sample is drawn from
  scorer output rather than from audit flags.

### What the agent is instructed to change

In priority order, with the reasoning given so the agent can depart from it when the
scores say to:

1. **Types with the highest `fully_covered` leak rate**, weighted by gold count. Leak
   rate is the headline (DESIGN §5) and a missed identifier is a disclosure.
2. **`joint_only` cases** — the identifier is covered but no single family covers it
   alone, which usually means a boundary disagreement rather than a missing pattern. The
   fix is usually widening one rule, not adding one.
3. **Rules with the worst `fp`-to-`tp` ratio in `by_rule`** — deletion and narrowing are
   revisions, and a file that only grows is the characteristic failure of this loop. High
   `fires` with near-zero `tp` is the clearest case: the rule is producing spans the arm
   gets no credit for.
4. **Sparse types** (DESIGN §9.4) — they stay in every denominator, so they cannot be ignored,
   but a rule written from one or two examples is a memorised span. Prohibition 2 binds
   over this priority.

Not the agent's decision: when to stop, how many iterations remain, whether the arm is
converged. Termination is δ/k and budget in deterministic code (DESIGN §3), and an agent
that could declare itself finished would be optimising the stopping rule.

---

## 6. The filled prompt is never written down

**Only this template is committed. A filled instance is not committed, not logged, and not
written to disk at all.**

The reason is §1.4: the error-span block carries ±120 characters of dev text around every
span in the sample. That text is **the corpus**, not a description of it. This file is
publishable because it says "±120 characters of dev text around the span" where an
instance says the characters.

So the rule for a filled prompt, and the reason each half is stated separately:

- **On a DUA corpus it travels the Bedrock path and nowhere else.** Assembled in memory,
  sent, discarded. Not `--debug` output, not a cached prompt, not an entry in
  `agent_calls.jsonl` — that file is already deny-listed for this reason. Where the
  answer to "may this text leave the machine" is no, `n` for the agent is 0 on that
  corpus (§1.4, DESIGN §11.1's DUA inversion) and the prompt has no such block to leak.
- **It is not persisted on any corpus, DUA or not.** The rule does not branch on which
  corpus is being run. CLAUDE.md's reasoning applies unchanged: a discipline that holds
  only where someone remembered it is a discipline that fails on the day the corpora get
  swapped, and a check that is safe only on the synthetic corpora is one nobody can
  trust. MEDDOCAN and GraSCCo are synthetic and are not exceptions.

**This is the same principle as `human_log.jsonl` recording only `(doc_id, span_index)`**
(DESIGN §11.2). That file needs to say which span a decision was about, and it does it
with a reference: resolvable by anyone holding the corpus, inert to anyone who does not.
The filled prompt is the case where the same requirement has no such form — a rule author
genuinely needs the words, and a reference will not do — so the answer is not a safer
representation but a shorter lifetime. Both come out of one rule: **the artifact that
survives contains references, and the text exists only in transit.** `src/sample.py`
implements the sampling side of it, on `ErrorSpan` objects that have no text field at all;
the renderer attaches context and hands it to the transport.

`tools/release_screen.py` blocks the file patterns a filled instance would land under
(`prompts/filled/`, `*.filled.*`, `*_rendered_prompt*`, `*prompt*_iter{N}*`), and the
mutation `filled_prompt_paths_allowed` confirms it — removing the directory pattern makes
an instance at `prompts/filled/iter03.md` read as an ordinary publishable file under
`prompts/`, which is an ALLOW_HINTS prefix, so it is not merely unblocked but reported
clean.

**Those patterns are deliberately absent from `.gitignore`,** which is worth stating
because adding them looks like an improvement. Gitignoring them would downgrade an
instance on disk from BLOCKED to Quarantined — expected, one summary line, exit 0 — and
the convention here is "never written to disk", not "never committed". The file's
existence has to stop the commit. That is the opposite call from `sealed/`, for a reason
that does not generalise: the sealed fold must be on disk, and this must not.

---

## 7. What this file decides for `port-human` — **dormant, arm retired 2026-08-07**

> Retained as pre-registration (DESIGN §11). The table below was checkable before any arm
> ran, and that is what makes it worth keeping: a revived human arm starts from these rows
> rather than re-deriving them once the agent results are visible. Nothing here binds the
> agent arms — but the left column does describe what they are shown, so it remains the
> record of what `port-oneshot` and `port-loop` receive.

DESIGN §11.1 fixes `port-human`'s dev window by **derivation from this file**, and the
ordering is normative: this prompt is specified first, and the human's window follows.
The reverse order would calibrate the control to the author's convenience, and the
resulting `port-loop` win would read as an agent result.

So, concretely, item by item from §1.3 through §1.5. The point of the table is that it
is checkable: each row names where the human's side comes from, and any row where the two
columns differ has to name **which arm the difference favours** (DESIGN §11's fairness
principle — an unfair control does not produce a weaker claim, it produces an
uninterpretable one).

| what | RuleAuthor gets | the human gets | difference |
|---|---|---|---|
| error spans per iteration | `n` = 40, `config/sampling.yaml: n_error_spans` (§1.4) | the same 40, from the same key | none |
| which spans | `draw()` in `src/sample.py`, stratified by `phi_type` | the same call, same code path | none |
| context around each span | ±120 characters, `config/sampling.yaml: context_chars` | the same ±120 | none |
| span fields | type, error kind, context, offsets within the window, `detected` for false positives (§1.4) | the same five fields | none |
| stratification | proportional with `min_per_type` = 1 (§1.4) | the same allocation | none |
| the seed | `sample_seed(corpus, iteration)` (§1.5) | the same function, same arguments | none — the seed does not take the arm |
| score block | the reduced dev `metrics.json`: per-type leak rate and P/R/F1 both modes, complementarity, `duplicate_predictions`, `by_rule` (§1.3) | the same block, same reduction | none |
| Auditor report | `reports/leaks_{iter}.json`, with the three-case reading (§5) | the same file, the same three cases | none |
| the current rule file | full text of `rules/{lang}.yaml` (§1.2) | the file itself, in an editor | **the human's is better** — see below |
| the task frame | §1.1, in the prompt | §1.1, read once at the start | **the human's is better** — see below |
| iteration count, budget, termination | not the agent's decision (§5) | not the human's decision either; the orchestrator runs both | none |
| error pool the sample is drawn from | that arm's own current errors | that arm's own current errors | none — and this difference is the experiment |

**The two differences, and which arm each favours.** Both favour the human, both are
unavoidable, and neither is in the sample:

1. **The rule file and the task frame are re-read, not re-delivered.** An agent call is
   given both afresh in each prompt and retains nothing between calls; a person keeps
   them open. This is asymmetry (1) of DESIGN §11.1 — memory carry-over — appearing in the
   input rather than in the recollection, and it favours the human. It cannot be equalised
   downward without deliberately withholding a file the person is editing, which would be
   a handicap invented for symmetry's sake, and DESIGN §11.1 already rejects that
   direction: the reading rule makes `port-human` an **upper bound** on human performance
   precisely so that these do not have to be argued away one at a time.
2. **Nothing else.** The list is short because §1.4 and §1.5 were written to make it
   short: one config file holds the parameters, one function performs the draw, and both
   arms call it.

**Where a DUA corpus prevents sending text to the agent**, `n` for the agent is 0 on that
corpus (§1.4, §6) and the human's window becomes the binding asymmetry — recorded per
corpus, with the direction named, and that corpus reported separately (DESIGN §11.1, "DUA
inversion"). That is the one case where the table above does not hold, and it is the case
where the inequality runs the other way.

**And the price of that ordering: changing this file invalidates any completed
`port-human` run on the same corpus.** Not "weakens" — invalidates. If `n` becomes 60, or
the context window widens, or error spans are removed from the prompt altogether, then the
human's window as run no longer matches the agent's, the two arms were given different
jobs, and the comparison measures the prompt revision as much as the difference between
human and agent. There is no analysis that repairs it after the fact, because the human
cannot be re-run under the old window with the new knowledge (§11.1, memory carry-over) —
the same person re-porting the same corpus is not a fresh trial.

Practical consequence, stated so it is not discovered later: **this file is frozen for a
corpus before `port-human` begins on that corpus, and its identity is recorded in
`human_log.jsonl` alongside `rules_commit`.** Two fields, because they answer different
questions:

- `prompt_sha256` — `sample.prompt_hash()`, the SHA-256 of **this file's bytes**. The
  content, not the commit: an uncommitted edit moves this and leaves the repository commit
  where it was, and that edit is exactly the event the record exists to catch.
- `sampling_sha256` — the same over `config/sampling.yaml`, since §1.4's parameters live
  there. A window can be widened without touching this file at all, by changing one
  integer, and a record naming only the prompt would agree with the new window as readily
  as the old.

Both are written on every `human_log.jsonl` line rather than once per run. A per-run
header would record what was frozen at the start, which is the wrong end: the question a
reader has is whether the window was the same at iteration 9 as at iteration 1, and a
value repeated on every line answers it by disagreeing with itself. A revision is permitted before the arm
starts or after it finishes on all corpora — and in the second case the `port-human`
result belongs to the old prompt and is reported against it, or the arm is re-run with a
different author, which is a different trial and labelled as one.

**One thing the table does not cover, and §8 does.** Every row above is about what the
human is *shown*. None of them says anything about where the human's answers come from,
and the table would read exactly as it does now for an author who pasted each rendered
span into a language model. That is a larger asymmetry than any row here — it would make
the two columns the same arm — so it is stated separately, as §8, and reported per
iteration in `human_log.jsonl`'s `model_consulted`.

**When the revision is permitted, and what enforces it.** The allowance above — before
the arm starts on a corpus, or after it finishes on all of them — is now a condition in
code rather than a rule the author applies to themselves. `freeze_window()` refuses to
write once any `human_log.jsonl` line carries a non-null `human_minutes`, and it refuses
whether or not the freeze record is still on disk, because its first version refused only
to *overwrite* and a `rm` stepped around that three times before iteration 1 on
`es-meddocan` (`docs/notes/window-freeze-history.md`). After the first recorded minute the
only route to a different window is re-running the arm from iteration 1 with a different
author — the paragraph above, with the cheap version removed.

One ordering lesson from those three, since it costs nothing to follow: **freeze last.**
Two of the three re-freezes were prose edits to sections written minutes earlier, and they
happened because the freeze was taken while this file was still moving.

**This section is the expensive half of DESIGN §11.1's ordering.** It is written out here,
in the file the ordering constrains, because the cost is only payable in advance — by the
time someone wants to widen the prompt mid-experiment, the alternatives are re-running a
human arm or publishing an incomparable one.

---

## 8. `port-human` may not ask a model what a rule should be — **dormant, arm retired**

> Retained for the same reason as §7, and it is the clause a revival would be most tempted
> to soften. Note what its own argument now says out loud: an author who asks a model what
> pattern fits an error "has run `port-oneshot` with a slower interface" — and
> `port-oneshot` is now the baseline. The two arms it warns about collapsing are no longer
> a control and a treatment; one of them is simply gone. A revived human arm faces exactly
> the same contamination risk against a baseline that has by then been *measured*, which
> makes the temptation stronger rather than weaker.

This section binds a **person**, which is why it is not in §4. §4's prohibitions are
given to the agent and enforced around it; this one has no enforcement mechanism at all
beyond the author's own honesty and a self-report field, and saying so plainly is part of
the clause.

**The rule.** During a `port-human` iteration, the *content* of a rule is not obtained
from a language model. Not the pattern, not the layer it belongs in, not which error to
address, not whether a rule should be deleted. Rendering, scoring, logging, aggregation,
file validation, and every other mechanical step may be delegated to tools — including
tools that are themselves models — but the question **"what pattern fits this error?"** is
answered by the person.

**Why, in one sentence:** an author who shows an error span to a model and writes down its
answer has run `port-oneshot` with a slower interface, and the `port-human` column of every
comparison in DESIGN §11 then reports the difference between two agent arms as the
difference between a person and an agent. The arm is the control. A contaminated control
does not weaken the paper's claim; it makes the claim unfalsifiable, which is the failure
mode DESIGN §11 exists to prevent and the reason its fairness principle is stated as
"an unfair control produces an uninterpretable result, not a weaker one."

Note that this cuts in the direction *against* convenience and *against* the human arm's
measured performance. That is the point: `port-human` is specified as an upper bound on
human performance (§11.1), and an upper bound obtained with model help is an upper bound
on nothing.

### 8.1 Where the line is, and why there

The general criterion, from which every case below follows: **a model may be asked about
the language it is written in, and not about the corpus or the errors.** Regex is a formal
notation with a specification; the question "what does `(?<=\bDr\.\s)` match" has an
answer that does not depend on this experiment existing. "Which pattern catches these
spans" is a question *about the dev fold*, and answering it is the work the arm measures.

A second way to state the same line, useful when the first is ambiguous: **could the
question have been asked, word for word, before any corpus was loaded?** If yes, it is
about the notation. If it needs a span, a per-type rate, an error listing, or a sentence
from the sample to make sense, it is the work.

| case | verdict | why |
|---|---|---|
| "What is the Python `re` syntax for a negative lookbehind?" | **allowed** | Notation. Askable before the corpus existed; the answer is in the language reference and does not mention the dev fold. |
| "Why does this pattern raise `look-behind requires fixed-width pattern`?" | **allowed** | A defect in the author's own artifact, diagnosed against the language's rules. Equivalent to running the interpreter, which §3 already allows the agent. |
| "Is `[[:alpha:]]` supported in Python `re`?" | **allowed** | Notation. |
| "Here are eight missed `NAME` spans. What pattern catches them?" | **forbidden** | This is the arm's entire task, delegated. The clearest case, and it stays clear only if the borderline ones below are also settled. |
| "Here is one missed span. What pattern catches it?" | **forbidden** | Eight and one differ in efficiency, not in kind. Quantity thresholds are unenforceable and invite salami-slicing — the author who asks about one span at a time has asked about all of them. |
| "For Spanish clinical notes generally, is a `Dr.` prefix cue a good approach for clinician names?" | **forbidden** | The hard case, and it is forbidden. It reads as a question about the language, but the answer is a rule design decision — which is exactly what the arm measures — and it is being asked *because the author is looking at spans that prompted it*. Nothing in the sentence carries corpus text, so no privacy rule stops it; the arm-integrity rule does. |
| "Which of these two patterns I wrote is faster?" | **allowed** | A property of the notation, decidable without the corpus. But if "faster" is standing in for "which one should I keep", that is a design decision and the answer is the author's. |
| "Summarise the per-type leak rates in this `metrics.json` block." | **allowed** | Reading an artifact the scorer produced, in the form it produced it. No judgement about what to do next. Note this is a *reduction*, not an interpretation: the moment the summary is asked to say which type to work on, it has crossed. |
| "Which `phi_type` has the worst leak rate?" | **allowed** | Arithmetic over a committed file. The answer is determined by the data, and a person with a calculator gets the same one. |
| "Which `phi_type` should I work on next?" | **forbidden** | Not determined by the data. Prioritisation is the author's judgement, and DESIGN §11.2's `decision` field exists to record that it was theirs. |
| "Render the sample for me / count the spans by type / append this line to the log." | **allowed** | Mechanical, deterministic, and already implemented as `src/porting/human_arm.py`. If a model does it instead of the module, the output is the same or it is a bug. |
| "Does this rule file parse, and do the `rule_id`s pass the vocabulary check?" | **allowed** | Validation. The criterion is in §4's Prohibition 2 and in `tools/release_screen.py`; a model applying it is a slower linter. |
| "I think the issue is dates written `12-ENE-2093`. Is that a common Spanish clinical format?" (the string is invented for this table, not quoted from any corpus) | **forbidden, with a caveat** | The caveat is that this is genuine prior knowledge the author might have, and §11.1 permits prior knowledge — from the author's own head. Sourcing it from a model at the moment a span prompted it is the model contributing rule content. The honest move is to write the rule from what you know, log `evidence: prior_knowledge`, and accept being wrong. Also: quoting that string to a model publishes corpus text, so on a DUA corpus it is a second violation. |

**Two asymmetries in how the borderline cases resolve.** First, they resolve *against* the
author, because the cost is asymmetric: an unasked question costs the author some minutes,
and an asked one costs the experiment its control, with no analysis that repairs it
afterwards. Second, "I only used it for X" is not available as a defence after the fact,
since nothing in the artifact distinguishes a rule the author designed from one they
transcribed — which is why the self-report is per iteration and written while the memory
is fresh, rather than as a declaration at the end of the run.

**What is *not* forbidden**, stated so the clause is not read as wider than it is: the
author may read documentation, the language reference, published papers, this repository,
prior de-identification rule sets, and their own notes; may use an editor with completion
for the notation; and may use models freely on work that is not `port-human` — the agent
arms are literally made of them. The clause is scoped to rule content during this arm's
iterations, and it ends when the arm ends.

### 8.2 The self-report field

`human_log.jsonl` carries **`model_consulted`** on every line, from a fixed vocabulary
(`config/naming.yaml`, axis `model_consulted`), and it is required — there is no default
and no `null`:

| value | meaning |
|---|---|
| `none` | No model was involved in this event at all. |
| `mechanical` | A model performed a §8.1-allowed mechanical step: rendering, counting, reduction, validation, logging. |
| `notation` | A model was asked about the notation — regex syntax, an error message from the regex engine, a language reference question. |
| `rule_content` | **A violation.** A model was asked about a rule's content, or the author is unsure whether a question crossed the line. |

Four values rather than a boolean, and the reason is that a boolean would be answered
`false` honestly by an author who used a model to render the sample, which is allowed —
so the field would stop distinguishing the case it exists for. Naming the allowed uses
explicitly is what makes `rule_content` a deliberate entry rather than a judgement call
about whether "using a model" happened.

**`rule_content` is a value the field can hold, not an error the harness refuses.** A
self-report field that rejects the answer it exists to capture collects only the other
answers, and the arm's integrity is then documented by a file that could not have recorded
its absence. A run with `rule_content` on some lines is reported with those iterations
identified and, depending on how many, either reported as a limitation or re-run with a
different author — and either outcome requires the log to have said so. `null` is refused
for the same reason `human_minutes` is validated: an unfilled field is indistinguishable
from an unproblematic one.

**Unsure resolves to `rule_content`.** The value is not "I judge that I crossed the line"
but "this event is not certainly clean", which is what a reader of the log needs. The
bias is toward reporting contamination that may not have occurred, because the opposite
bias is unrecoverable.

**What this is worth, stated honestly.** It is a self-report, on the honour system, by the
person whose arm the report reflects on. It cannot detect a violation its author declines
to write down, and it should not be presented in the paper as though it could. What it
does buy is threefold: the obligation is in front of the author at every event rather than
once at the start; the value is committed alongside `rules_commit`, so a later claim of
cleanliness is checkable against a contemporaneous record rather than a recollection; and
if the arm's provenance is ever questioned, the answer is a file rather than an assurance.
That is the same standing as `predicted_scope` (DESIGN §11.2) — a judgement whose bias
direction is known, recorded before its own test, and reported against the arm it favours.
