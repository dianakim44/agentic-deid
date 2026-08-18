# Auditor prompt — the residual-PHI report

> **This file is part of the frozen window.** `WINDOW_FILES` is `rule_author.md`,
> `auditor.md`, `config/sampling.yaml` (DESIGN §5.5), so this file's bytes are hashed into
> `window_freeze.json` and onto every `agent_calls.jsonl` line. The reason it belongs there
> is not that the Auditor is important: it is that **this file decides what the RuleAuthor
> is shown at its audit block**, and a freeze record naming only `rule_author.md` would
> agree with a rewritten Auditor as readily as with this one.
>
> **`port-oneshot` never calls the Auditor** and its freeze record does not name this file.
> That record is not re-hashed (DESIGN §5.5, §6.3): it attests to the two files that existed
> when its one call was made, and a third hash added retroactively would be a claim about a
> window that never applied.
>
> **The Auditor is called at iteration n ≥ 2 of `port-loop`, as that round's first step**
> (`config/naming.yaml`, axis `agent_role`). It reads iteration n−1's predictions, masked;
> its report is written to `iter{n}/audit_report.json` and consumed by the RuleAuthor call
> in the same round. Two consequences, both deliberate:
>
> - **`iter{n}/` holds what round n was shown and what round n produced.** The report is an
>   *input* to round n derived from round n−1's `spans.jsonl`, which is why the file records
>   `masked_from_iteration` — the derivation is on the file rather than in a convention
>   someone has to remember while reading a directory listing.
> - **An arm that terminates at iteration 1 never pays for an audit.** Auditing at the *end*
>   of round n would spend the largest single cost in the loop (DESIGN §3: ~110k tokens, more
>   than the RuleAuthor by 5×) on a report no call ever reads.

The Auditor is one agent with one artefact: the residual-PHI report (DESIGN §3, `paths.auditreport`).
It does not detect, does not score, does not write rules, does not arbitrate, and does not
decide when to stop — the orchestrator is deterministic code and owns iteration, budget, and
termination.

**It never sees gold** (DESIGN §3). Everything it produces is a *suspicion*, and every
sentence below that says "flag" means "the agent believes this is an identifier", never "this
is an identifier".

---

## 1. Input — what the agent is shown

Two blocks, and then one document. One call per document, and §1.3 says why.

### 1.1 Task frame

- the canonical `phi_type` values, verbatim from `config/naming.yaml` with the one-line gloss
  each carries there — the report's `phi_type` is a value of that axis and nothing else
- the mask tag form (`[NAME]`, `[DATE]`, …), so a tag is recognisable as a tag
- the types **excluded from the canonical set** (DESIGN §9.1: sex, family relationship,
  `NAME_TITLE`), named as out of scope rather than left to be inferred. An Auditor that flags
  `madre` is not wrong about the text; it is answering a question this project does not ask,
  and every such flag lands in the least actionable category of the report (§4 below).
- **that `OTHER` may not be flagged.** `naming.yaml` declares it a residual bucket and not a
  rule-development target, and a flag nothing can be written against is a flag that costs the
  RuleAuthor prompt space and returns nothing.

### 1.2 The masked document

The dev document with **every span the arm detected replaced by its type tag**, and nothing
else changed (DESIGN §3, decided 2026-08-12). Produced by deterministic code from that
round's own `spans.jsonl`, right-to-left, making no inference.

**The consequence that makes the whole role work, stated to the agent directly:** what the
Auditor is asked to find is *residual* PHI — an identifier the rules **missed**. A missed
identifier is by definition not in the detected set, so it was **not masked and stands in the
text verbatim.** The words the agent is looking for are the untouched ones. Nothing has been
substituted for them, nothing has been shifted, and no surrogate has been invented in their
place.

Three things follow, and each is in the prompt because leaving it out changes what the agent
does:

- **A tag is not a candidate.** `[NAME]` marks something already found. Flagging it reports
  a detection back to its own detector. The validator refuses flags landing inside a tag
  (§2.3), but the agent is told first, because a refusal is a cost paid after the tokens.
- **Masking is what makes this a residual list rather than a second detector's output.** An
  Auditor given unmasked text would flag every identifier in the document, most of them
  already caught, and its report would be dominated by what the rules already have. The tags
  remove exactly the part the RuleAuthor does not need.
- **A mistyped mask is out of scope.** A date masked as `[NAME]` is a retyping question and
  belongs to the arbiter (`RT-Arb`, DESIGN §3), not here. Mixing the two claims into one list
  would make `flags` stop being one thing, and per-type flag counts would stop meaning
  anything.

**The tags are the arm's own output and are not gold.** They tell the agent what the current
rules found, which is unavoidable — masking is what the input *is* — and it is worth naming as
the one thing the Auditor knows about the detector: it can see where the rules have been
firing and may be biased toward looking nearby. Accepted, and preferred to the alternative,
because the alternative is unmasked text and a report of things already detected.

**What the Auditor is *not* shown, each for its own reason:**

| withheld | why |
|---|---|
| gold, in any form | DESIGN §3. The report's whole value is that it comes from a component that cannot see the answer. |
| the score block (`metrics.json`) | Per-type leak rates are **derived from gold**. Showing them would tell the Auditor which types gold says are being missed, which is gold entering through an aggregate. This is the subtlest of the four and the easiest to add while believing it is only a summary. |
| `rules/{lang}.yaml` | An Auditor holding the rule file predicts the rules' blind spots instead of reading the text, and its independence from the RuleAuthor is the entire reason there are two agents rather than one prompt. |
| the previous iteration's report | It would make the agent consistent with itself rather than with the text, and a repeated flag would read as corroboration when it is recall of its own last answer. Each round's report is drawn independently. The mechanism by which the report shrinks across rounds is that a residual flagged at round 3 and fixed at round 4 is **masked** at round 4 and cannot be flagged again — and that mechanism works only on an unprimed agent. |
| any other document | §1.3. |

### 1.3 One call per document, and how positions are given

**One document per call, not the fold in one context.** Three reasons, in the order they
decide it:

1. **Recall degrades along a very long context**, and a report whose last documents were read
   at 100k tokens of depth is a report whose per-document flag rate is a function of
   position in the batch. That would be a property of the batching, indistinguishable in the
   file from a property of the documents.
2. **A failed or malformed call loses one document, not the fold**, and the loss is recorded
   per document rather than as a missing report.
3. **`doc_id` never has to come from the agent.** The orchestrator knows which document it
   sent; a `doc_id` the agent typed is a field it can get wrong and nobody can check.

The token total is the same either way, which is what the cost estimate in DESIGN §3 is
about. The **call count** is not: the Auditor's `llm_calls` for one iteration is the dev
document count, not 1, and this is precisely why `call_line()` carries `role`
(DESIGN §5.5) — `llm_calls` summed over one `agent_calls.jsonl` is the right answer for
what the round spent and no answer at all for who spent it.

**The document is rendered one line per line, prefixed with that line's start offset in the
masked text.** The three lines below are **invented for this document and quoted from no
corpus** — the same caveat `rule_author.md` §8.1 attaches to its one example string, and it
applies here because an illustration of a residual identifier has to be name-shaped to
illustrate anything:

```
0000000 | Servicio de Cardiología. Paciente: [NAME], [AGE].
0000051 | Domicilio: [LOCATION_STREET], [LOCATION_AREA].
0000098 | Remitido por el Dr. Ejemplo Apellido el [DATE].
```

The third line is the case the role exists for: the clinician's name was missed, so it is not
masked and it is sitting there in the input, while the date beside it was found and is a tag.

**Coordinates are `(line, start, end)` where `start`/`end` are columns within that line**,
half-open, counted from 0 at the character after `| `. Not document offsets, and the choice
is about where arithmetic is done rather than about convenience:

- **Column-within-line is a bounded count; offset-within-document is not.** Asking a model
  for a character offset 4,000 characters into a note is asking for the one arithmetic task
  it is worst at, and a wrong offset is a flag that points at the wrong words while looking
  exactly like a right one.
- **Masked coordinates, not document coordinates.** The tags change lengths, so column
  arithmetic across a tag does not give a document offset. The masker built the map and
  translates; the agent is never asked to compute across a tag boundary. **A translation the
  agent performed would be a place a wrong answer looks right**, which is the same objection
  DESIGN §3 makes to a masker that made judgements.
- **The line prefix is not part of the line.** It is stripped before column 0.

**A flag does not cross a line boundary.** The cost is real and is recorded here rather than
discovered: an identifier split by a line break can only be flagged on one of its lines, so
its flagged boundary is short. Nothing downstream needs it to be exact — §4 matches flags to
gold by **overlap**, not byte equality, because the byte-equality rule of DESIGN §9.3 governs
merging *detectors'* predictions and this report is not a detector's output and never enters
detection.

---

## 2. Output — the residual-PHI report

### 2.1 Per call, from the agent

One JSON object. **Emit the JSON and nothing else. No code fence, no ``` line, no `json`
language tag, no preamble, no closing remark.** The fenced block below is an example inside
these instructions and the fence is how this document quotes it.

```json
{
  "flags": [
    {"line": 3, "start": 20, "end": 38, "phi_type": "NAME", "score": 0.8},
    {"line": 3, "start": 16, "end": 19, "phi_type": "PROFESSION", "score": 0.4}
  ]
}
```

Four required fields per flag plus `score`, and **no others**. §3 is why there is no field
for a reason, a note, or a quotation.

**An empty list is required where nothing was found.** `{"flags": []}` means *this document
was audited and nothing survived*; a document with no entry in the report means *this
document was not audited*. Those are different facts and the report distinguishes them — the
same rule as everywhere else here: zero is a measurement, absent is not.

**`score` is the agent's own confidence in [0, 1], recorded and never thresholded.** The
orchestrator drops nothing on score. A build-time threshold would be a tuned parameter on an
unlabelled signal, it would silently change what the RuleAuthor is shown, and calibration is
not a property this project has established for this agent. Every flag travels; the number
travels with it.

### 2.2 The file, from the orchestrator

`paths.auditreport` — `results/{corpus}/{detector}/{supervision}/{porting}/iter{iteration}/audit_report.json`,
axis- and iteration-scoped, and **deny-listed** in `config/naming.yaml`: a list of positions
an agent believes are surviving PHI is a map of the residual identifiers in a DUA corpus, the
most concentrated such artefact the loop produces (DESIGN §5.5).

```json
{
  "iteration": 4,
  "masked_from_iteration": 3,
  "corpus": "es-meddocan",
  "documents_audited": 250,
  "documents_with_no_flags": 96,
  "flags": [
    {"doc_id": "doc-0007", "phi_type": "NAME",
     "start": 1841, "end": 1859, "score": 0.8}
  ],
  "refused": [{"doc_id": "…", "reason": "inside_a_mask_tag"}],
  "counts": {"flags": 118, "refused": 3, "by_phi_type": {"NAME": 61, "DATE": 22},
             "by_refusal": {"inside_a_mask_tag": 2, "out_of_range": 1}}
}
```

`start`/`end` are **document** offsets, translated from the agent's `(line, start, end)` by
the masker's map. `doc_id` is the orchestrator's. Both are code's contribution, and neither is
a contribution to the *content*: the orchestrator adds no flag and removes only flags it can
prove impossible.

**A refused flag keeps its `doc_id` and its reason and nothing else — not even its
position.** Half of these refusals *are* the judgement that the position cannot be trusted
(§2.3's `out_of_range`, `crosses_a_line`), and a recorded untrustworthy position would pass
for part of the residual-identifier map this path is deny-listed for being.

**`flags` is sorted in the file; the order of one call's return is not.** `validate_flags()`
keeps what the agent returned, because that value is the record of a call and reordering it
would be code editing the call. The file sorts, because two rounds get diffed and an order
that moves when the model's does makes every diff a rewrite. Same split as `errors.jsonl`
(DESIGN §5.5).

**Why the file is not the agent's bytes verbatim, unlike `rules/{lang}.yaml`.** The rule file
*is* the RuleAuthor's artefact and is written through unchanged. Here the artefact needs a
coordinate translation that the agent must not perform (§1.3), so the agent's output is a
claim and the file is the validated, translated claim. That is a mechanical step, and DESIGN
§3's "one agent, one artefact" is unaffected: the flags are the Auditor's, and `refused`
records exactly what code took out.

### 2.3 What the validator refuses, and that it refuses rather than repairs

| refusal | what it catches |
|---|---|
| `out_of_range` | a line number or column past the end of what was sent |
| `inside_a_mask_tag` | a flag overlapping a replacement — a detection reported back to its detector (§1.2) |
| `undeclared_phi_type` | a value outside the `phi_type` axis, including `OTHER` (§1.1) |
| `crosses_a_line` | `end` before `start`, or a span the line cannot contain (§1.3) |
| `malformed` | not one JSON object, an unknown field, a missing field, a `score` outside [0, 1] |

**Refused, not repaired, and counted.** A validator that snapped an out-of-range column to
the end of the line would produce a flag at a position the agent never claimed, and nothing
in the file would say so. A validator that dropped silently would make the report shorter for
a reason no reader could see. `refused` carries the reason and `counts.refused` carries the
total, so a round in which the agent lost the coordinate scheme is visible as a number rather
than as a thin report.

**An unknown field is a refusal, not an ignored key.** This is the whitelist rule
`write_errors()` follows for `errors.jsonl` (DESIGN §5.5.1), and it is here for the sharper
version of the same reason: the field most likely to be added to a flag is the one §3
forbids.

**The refusal reasons are a closed vocabulary declared in `config/naming.yaml`** by the
commit that implements the validator — not coined in the validator. They are written into a
file under `results/`, and CLAUDE.md's rule covers values in results as much as axis names.

---

## 3. Prohibition — no corpus surface form in the output, and there is nowhere to put one

This is `rule_author.md` Prohibition 2 at the point where it is hardest to hold, so it is
stated as a property of the schema rather than as an instruction to be careful.

**The natural output for this task is "I found the name *…* at line 3", with the name in it.**
That sentence is a residual identifier from a DUA fold written into a file under `results/`,
and `tools/release_screen.py` does not reach terminal logs, exception messages, issue text, or
a prompt response on its way there. The repository is public.

**So the flag has no free-text field at all.** No `reason`, no `note`, no `evidence`, no
`snippet`, no `context`. The omission is the mechanism:

- **Any justification for a span is a description of that span's text, and the shortest
  honest justification is a quotation.** A field for prose is a field the surface form
  arrives in, written by an agent trying to be helpful — which is the same way
  `rule_author.md` Prohibition 2 identified `rule_id` as the bypass: *this prohibition is
  breached most easily by an author trying to be clear.*
- **Little is lost.** The RuleAuthor cannot act on an unresolved flag anyway (§4, case 2),
  and for the cases it *can* act on it has the §1.4 window's ±120 characters from the
  sampler. A justification would buy the actionable cases nothing and would publish the
  unactionable ones.
- **`phi_type` carries what the RuleAuthor needs.** "A `NAME` at this position, score 0.8" is
  the whole actionable content of a flag.

The agent is told this in the prompt, in these words: **do not quote, transcribe, paraphrase,
or describe the text you flag. Emit its position and its type.** And it is enforced outside
the prompt, because a prohibition that exists only in a prompt is a request: the validator
refuses unknown fields (§2.3), so the field a future version adds is refused on the day it is
added rather than published.

**The same rule binds everything else the run touches.** Nothing writes the masked text to
disk; no exception message quotes it (CLAUDE.md, applied to every corpus and not only the DUA
ones); `audit_report.json` holds offsets, types and scores only.

### Other prohibitions

1. **`sealed/` is never read, listed, or referred to.** The Auditor reads dev. The test fold
   is physically outside the corpus root and `load()` returns unsealed folds only; a path
   under `sealed/` appearing in this agent's context is a harness bug to report, not a
   resource (DESIGN §6.1).
2. **Only `config/naming.yaml` vocabulary** — `phi_type` values come from the axis, and a new
   one is added to that file by a human in a commit, never coined in a report.
3. **No claim about the test fold, and no statement about how well the pipeline performs.**
   The Auditor sees one masked document and no scores; anything it says about performance is
   unfalsifiable from its own input.
4. **The report is never scoring.** It does not enter `metrics.json`, it is not compared to
   gold during the run, and no termination decision reads it — §5.

---

## 4. How the report is consumed, and the block the RuleAuthor is shown

`rule_author.md` §5's three-case reading is unchanged and is the normative statement:
**flagged and gold-missed** corroborates a real miss; **flagged, not in gold** is either a
gold annotation gap or an Auditor false positive, which the agent may not resolve and may not
write a rule for on the Auditor's word alone; **not flagged but gold-missed** is the
highest-value case in the loop and the easiest to skip, since nothing in the report points at
it.

What this file adds is **where each case is visible**, because that is a property of the block
and the block is this file's decision (DESIGN §5.5).

**The RuleAuthor knows gold membership only for the §1.4 sample.** Those 40 spans are drawn
from scorer output, so a `missed` span there *is* a gold identifier the arm did not cover.
Outside that sample the RuleAuthor has no gold at all. So the audit block is assembled in two
parts:

- **Flags that overlap a §1.4 sample span are marked with that span's `[nn]` index.** A
  marked `missed` span is case 1 — corroborated, and the RuleAuthor already has its context.
  A `missed` span with no mark is case 3, and the mark's absence is what makes case 3 visible
  at all.
- **Flags that overlap nothing in the sample are listed as positions, types and scores, plus
  `counts.by_phi_type`.** This is case 2 territory and it is where the per-type counts do the
  work: "31 unresolved `PROFESSION` flags this round" is a pattern the RuleAuthor can act on
  as a *type* priority without acting on any individual flag, which is the only use of case 2
  that `rule_author.md` §5 permits.

**Overlap, computed by deterministic code, not byte equality and not by the agent** — §1.3's
reason, and the marking is code's because a correspondence the RuleAuthor was asked to compute
is one it could get wrong in a direction that favours its own rules.

**The audit block carries no corpus text, and that bound is the point.** Rendering ±120
characters around every flag would make the RuleAuthor's dev window §1.4's 40 spans *plus*
every flag's context — unbounded, growing with the Auditor's false positive rate, and outside
the window `config/sampling.yaml` fixes. That would break `rule_author.md` §1.4's bound, and
with it §7's derivation of the human window from this same specification. The flags are
references (DESIGN §11.2's referent property): resolvable by whoever holds the corpus, inert
to anyone who does not.

**No edit to `rule_author.md` §1.4 is required by any of this,** and that is deliberate rather
than incidental. The marking lives in the audit block, which `rule_author.md` §5 already
describes and whose content this file decides. Adding a `flagged` line to §1.4's per-span
format would edit a hashed file for a change no arm before `port-loop` could have seen, and
`docs/notes/window-freeze-history.md` is a record of what editing hashed files late costs
(DESIGN §6.3).

**The report is not a leak rate and a shrinking report is not convergence.** δ/k is computed
over the dev leak rate from the scorer (DESIGN §3), which is measured against gold. Flag
counts fall when the Auditor gets tired of a document as readily as when the rules improve,
and an arm that terminated on its own auditor's agreement would be an arm whose stopping rule
was an unlabelled agent's opinion.

**The Auditor may be scored against gold *after* the run, as an analysis of the Auditor,
never during it.** Its precision against dev gold is a legitimate question — it decides
whether the `RT-Aud` arm has a component worth running — and answering it inside the loop
would mix agents and scoring, which DESIGN §3 forbids. If an aggregate is wanted in the
record, it is a derived count added to `metrics.json`, not a re-classification of this path
(DESIGN §5.5).

---

## 5. Tools — none

Not a shortened list: an empty one, and each entry a call would need is a contradiction of the
input.

- **No corpus access.** The one thing a search tool would return is the *unmasked* document,
  which is the negation of §1.2. An Auditor that can read the original is an Auditor that can
  see what the mask hid.
- **No gold, no `rules/`, no `results/`** — §1.2's table, and an agent that can read the
  scores has been shown gold through an aggregate.
- **No writing.** The report is assembled from the call's return value.
- **No network, no other agent.** The orchestrator sequences; there is no manager agent.

The agent reads one masked document and returns one JSON object. That is the whole interface,
and its narrowness is what makes "the Auditor never sees gold" a fact about the code rather
than a promise in a prompt.

---

## 6. The filled prompt is never written down — and this is the largest instance of it

Same rule as `rule_author.md` §6: only this template is committed. A filled instance is not
committed, not logged, and not written to disk at all. **Here it matters more than anywhere
else in the project.**

**The masked input is a *larger* corpus exposure than §1.4, not a smaller one**
(DESIGN §3, decided 2026-08-12). On es-meddocan's dev fold it is **about 210k tokens**
(810,499 characters of masked transport text over 250 documents, measured 2026-08-14) against
§1.4's roughly 2,700 — about **77×** — and under the leak rates this arm
actually produces a majority of in-scope gold identifiers in it are *unmasked*, because
unmasked is what "leaked" means. So this prompt carries more corpus text, containing more
intact identifiers, than any other prompt in this repository. The intuition that masked text
is safe text is exactly backwards, and it is the reason this section exists rather than a
cross-reference.

Three consequences, all binding:

- **The masker returns `FilledPrompt` and never a `str`** (DESIGN §3, §5.4). It is the second
  function in the project that slices document text for a prompt — `render_window()` is the
  first — so it lives inside the same discipline rather than beside it: assembled in memory,
  sent, discarded, through the exits that type enumerates and nothing else. Not `--debug`
  output, not a copy on disk, not a line in `agent_calls.jsonl`, which is deny-listed for
  this reason. What may be recorded is `reference()`: document id, counts, template hashes,
  rendered length. No text.
- **One part of this prompt *is* cached, and the boundary is why that is not an exception**
  (DESIGN §5.5, §11.3, decided 2026-08-18). The call splits at a declared offset and Bedrock
  retains the first block for five minutes: the template above, the input banner, and §1.1's
  frame. Those bytes are committed files and `config/naming.yaml` values — already public,
  and identical for every document in a round. **The masked document is on the far side of
  the boundary and is never in the cached block.** So the sentence that used to read "not a
  cached prompt" was true of the whole prompt and is now true of the half that carries the
  corpus, which is the half it was about; stating it as a boundary rather than as an absence
  is what keeps it checkable. If the boundary ever moved past the document heading, this
  bullet is what it would contradict.
- **Where a corpus's text may not leave the machine, the Auditor cannot run at all.**
  `rule_author.md` §1.4 sets `n` = 0 on such a corpus and the RuleAuthor arm degrades to a
  smaller window. This does not degrade. An Auditor shown 0 characters produces no report,
  and `port-loop` without an Auditor is a different arm — so the DUA case is not "`port-loop`
  with a smaller window", it is **`port-loop` unavailable on that corpus**, recorded per
  corpus rather than worked around (DESIGN §3, §11.1's DUA inversion).

**The rule does not branch on which corpus is running.** MEDDOCAN and GraSCCo are synthetic
and are not exceptions. CLAUDE.md's reasoning applies unchanged: a discipline that holds only
where someone remembered it fails on the day the corpora are swapped, and a check that is safe
only on the synthetic corpora is one nobody can trust.

**And the same principle in one line, which is where both prompts land:** the artefact that
survives contains references, and the text exists only in transit. `audit_report.json` is
references. The masked document is transit.
