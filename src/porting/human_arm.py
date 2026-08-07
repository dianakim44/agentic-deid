"""The `port-human` harness: freeze the window, draw a sample, append to the log.

This arm is a person writing `rules/{lang}.yaml`, so what is automated here is
everything *except* the rule writing: the window is frozen and hashed, the sample is
drawn by the same code the agent arm calls, and the log line is written in a fixed
shape. DESIGN §11.1's ordering is what makes that the right division — the human's
window is derived from the RuleAuthor prompt, so a person choosing what to look at
would be choosing the control's own protocol.

**Two views of the sample, and the split is the point.** `render_for_author()` produces
what the rule author reads, including the ±120 characters of context §1.4 requires.
`summarise()` produces what goes anywhere else — a terminal, a commit message, a
conversation, this repository — and it holds counts only. On MEDDOCAN the corpus is
synthetic and the context would be harmless, but the procedure does not branch on the
corpus: CLAUDE.md's rule is that a check safe only on the synthetic corpora is a check
nobody can trust, and CARMEN-I is the corpus this arm runs on second.

Iteration 1 needs no detection run. With an empty rule file nothing is detected, so
every in-scope dev gold span is a `missed` error by construction, and
`initial_error_pool()` builds that pool from the loader alone. From iteration 2 the
pool comes from the scorer, which is why `draw_iteration()` takes a pool rather than
building one.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..corpora import load
from ..corpora.base import ROOT, CorpusError, axis, path_template
from ..sample import (
    MISSED, WINDOW_FILES, ErrorSpan, draw, provenance, window_hashes,
)

#: The two files this arm writes, both from `config/naming.yaml`'s `paths` block and
#: neither as a literal here. DESIGN §11.2 requires it of `paths.humanlog` by name: an
#: output path is where an arm's results are found, so a module holding its own copy is
#: a second place the answer can change. In both templates the porting axis is fixed
#: rather than templated — these files exist for exactly one value of it, and a template
#: implying otherwise would invite a second arm to write them.
LOG_KEY = "humanlog"
FREEZE_KEY = "humanfreeze"

#: The fields every line carries, in this order. DESIGN §11.2's table plus the two
#: window hashes. Order is fixed so a reader diffing two lines sees field changes
#: rather than reordering, and `null` is written rather than omitted — an absent key
#: and a key whose value is unknown are different facts, and only one of them is
#: recoverable later.
FIELDS = (
    "iteration",
    "event",
    "human_minutes",
    "decision",
    "predicted_scope",
    "actually_reused",
    "evidence",
    "rules_commit",
    "prompt_sha256",
    "sampling_sha256",
)

EVENTS = ("read_sample", "decision", "rule_edit", "score_run")
SCOPES = ("global", "corpus_specific")



class PortHumanError(CorpusError):
    """A `port-human` step that cannot be taken as specified."""


def _arm_path(key: str, corpus: str, detector: str, supervision: str) -> Path:
    """Fill a `paths` template for this arm. Every component checked against naming.yaml.

    Checked rather than trusted, because a results path is not just a location: it is the
    record of which cell of the experiment a number belongs to, and a typo in a component
    mints a new cell instead of failing. `results/es-meddocan/rules-only/...` would sit
    beside `results/es-meddocan/R/...` looking like a second detector, and an aggregation
    walking these directories would report it as one (CLAUDE.md: only naming.yaml
    vocabulary, including values that never reach a results path — these do).
    """
    for value, ax in ((corpus, "corpus"), (detector, "detector"),
                      (supervision, "supervision")):
        if value not in axis(ax):
            raise PortHumanError(
                f"{value!r} is not a {ax} in config/naming.yaml (have: "
                f"{sorted(axis(ax))}). A results path names the cell of the experiment "
                "a number belongs to, so an unknown component would create a cell "
                "rather than fail."
            )
    return ROOT / path_template(key).format(
        corpus=corpus, detector=detector, supervision=supervision)


def log_path(corpus: str, detector: str, supervision: str) -> Path:
    return _arm_path(LOG_KEY, corpus, detector, supervision)


def freeze_path(corpus: str, detector: str, supervision: str) -> Path:
    """Where the freeze record lives. One per arm per corpus, written once.

    Every log line already carries the two hashes, so this file adds the one thing they
    cannot: what the window was *at the start*. A run whose window changed midway has
    consistent lines throughout — each line honestly records the window its own event was
    held to — and only a comparison against the opening record shows the change.
    """
    return _arm_path(FREEZE_KEY, corpus, detector, supervision)


def freeze_window(corpus: str, detector: str, supervision: str) -> dict:
    """Record the frozen window. Refuses to overwrite an existing record.

    DESIGN §11.1 freezes the prompt template and `config/sampling.yaml` before the arm
    starts on a corpus. Refusing rather than overwriting is the whole value of the file:
    a freeze record that can be rewritten records the window a run *ended* with, which
    is the one question nobody needs answered.

    Returns the record on the file — the existing one when there is one, so a caller
    that re-runs the setup step gets the opening window rather than today's.
    """
    path = freeze_path(corpus, detector, supervision)
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    record = {
        "corpus": corpus,
        "detector": detector,
        "supervision": supervision,
        "porting": "port-human",
        "files": list(WINDOW_FILES),
        **window_hashes(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    return record


def window_drift(corpus: str, detector: str, supervision: str) -> list[str]:
    """Which frozen files no longer hash to what was recorded. Empty when none.

    Checked rather than asserted, because the honest response to drift is not always to
    stop: an edit to the prompt's prose that leaves §1.4's numbers alone is a different
    event from a change to `n`, and only a person can tell them apart. What is not
    optional is *noticing* — an undetected mid-run change to the window makes every
    iteration before it and every one after it two different experiments reported as
    one.
    """
    path = freeze_path(corpus, detector, supervision)
    if not path.exists():
        raise PortHumanError(
            f"{corpus}: no freeze record for this arm, so there is nothing to compare "
            "against. freeze_window() runs before iteration 1 (DESIGN §11.1)."
        )
    with open(path, encoding="utf-8") as fh:
        frozen = json.load(fh)
    now = window_hashes()
    return [field for field in ("prompt_sha256", "sampling_sha256")
            if frozen.get(field) != now[field]]


def initial_error_pool(corpus: str) -> list[ErrorSpan]:
    """Every in-scope dev gold span, as a `missed` error. Iteration 1 only.

    With an empty rule file the detector emits nothing, so the leak set is the whole
    in-scope gold set — no detection run and no scorer pass is needed to know that.
    Later iterations must come from the scorer, since only it knows what was found.

    `excluded` spans are left out (DESIGN §9.1): they carry no canonical type, so they
    cannot be stratified by one, and they are not rule-development targets.
    """
    pool = []
    for doc in load(corpus):
        if doc.split != "dev":
            continue
        for i, span in enumerate(doc.spans):
            if span.excluded:
                continue
            pool.append(ErrorSpan(
                doc_id=doc.doc_id, span_index=i, phi_type=span.phi_type,
                kind=MISSED, start=span.start, end=span.end))
    if not pool:
        raise PortHumanError(
            f"{corpus}: the dev fold has no in-scope gold spans, so there is "
            "nothing to sample. Check the split file rather than proceeding with an "
            "empty window."
        )
    return pool


def draw_iteration(pool, corpus: str, iteration: int, *, n: int | None = None):
    """The sample and its provenance record, as one pair.

    Returned together because they are only meaningful together: a sample without its
    provenance cannot be reproduced, and a provenance record without its sample
    describes a draw nobody performed.
    """
    return draw(pool, corpus, iteration, n=n), provenance(corpus, iteration, n=n)


def summarise(sample, pool) -> dict:
    """Counts only. Safe for a terminal, a commit message, or this repository.

    No offsets either, and that is deliberate rather than cautious. An offset is not
    text, but a (doc_id, offset) pair published beside a type is a pointer into the
    corpus for anyone holding it — which is exactly the property that makes it the
    right referent for a *committed log* and the wrong one for a summary that exists
    to be read aloud. The log's audience holds the corpus; a summary's does not.
    """
    types = sorted({e.phi_type for e in pool})
    return {
        "pool_size": len(pool),
        "sample_size": len(sample),
        "by_type": {
            t: {"drawn": sum(1 for e in sample if e.phi_type == t),
                "in_pool": sum(1 for e in pool if e.phi_type == t)}
            for t in types
        },
        "by_kind": {k: sum(1 for e in sample if e.kind == k)
                    for k in sorted({e.kind for e in sample})},
        "documents_touched": len({e.doc_id for e in sample}),
    }


def render_for_author(sample, docs_by_id, context_chars: int) -> str:
    """The block the rule author reads. **Contains corpus text.**

    Never returned to a caller that logs, prints to a shared terminal, or writes to
    disk. The prompt's §6 rule is that this text exists only in transit; the same rule
    applies to the human arm, because a rendered window saved "for reference" is the
    same file the screener denies for the agent arm.

    The context window is clipped to the document, and the span's offsets are given
    *within the window* rather than within the document — a rule author needs to see
    where the span sits in what they are reading, and document offsets would invite
    them to go and look up the surrounding text, which is the unbounded window §11.1
    rejects.
    """
    out = []
    for i, e in enumerate(sample, 1):
        doc = docs_by_id[e.doc_id]
        left = max(0, e.start - context_chars)
        right = min(len(doc.text), e.end + context_chars)
        window = doc.text[left:right].replace("\n", " ")
        out.append(
            f"[{i:2}] type      {e.phi_type}\n"
            f"     error     {e.kind}\n"
            f"     context   {window}\n"
            f"     offsets   ({e.start - left}, {e.end - left}) within that context\n"
        )
    return "\n".join(out)


def log_line(iteration: int, event: str, *, human_minutes=None, decision=None,
             predicted_scope=None, actually_reused=None, evidence=None,
             rules_commit=None) -> dict:
    """One `human_log.jsonl` record, validated and in field order.

    The window hashes are filled in here rather than by the caller, so a line cannot
    be written without them. They are the record of which window this event was held
    to (DESIGN §11.2), and a caller that has to remember to add them is a caller that
    will forget on the line that matters.
    """
    if event not in EVENTS:
        raise PortHumanError(
            f"{event!r} is not a port-human event (expected one of {list(EVENTS)}). "
            "The set is fixed so the log can be aggregated; a new kind of event goes "
            "into DESIGN §11.2 first.")
    if predicted_scope is not None and predicted_scope not in SCOPES:
        raise PortHumanError(
            f"{predicted_scope!r} is not a scope (expected one of {list(SCOPES)}).")
    if actually_reused not in (True, False, None):
        raise PortHumanError(
            "actually_reused is true / false / null — null until the second corpus "
            "has been ported, which is the whole point of the field.")
    if human_minutes is not None and (
            not isinstance(human_minutes, (int, float)) or human_minutes < 0):
        raise PortHumanError("human_minutes must be a non-negative number of minutes")

    record = {
        "iteration": iteration,
        "event": event,
        "human_minutes": human_minutes,
        "decision": decision,
        "predicted_scope": predicted_scope,
        "actually_reused": actually_reused,
        "evidence": evidence,
        "rules_commit": rules_commit,
        **window_hashes(),
    }
    assert tuple(record) == FIELDS, "field order drifted from FIELDS"
    return record


def append(record: dict, corpus: str, detector: str, supervision: str) -> Path:
    """Append one line. Creates the directory but never rewrites an existing line.

    Append-only by construction rather than by convention: this file is the arm's
    only record of what a person did and when, and a rewritten line is
    indistinguishable from one that was always there.
    """
    path = log_path(corpus, detector, supervision)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path
