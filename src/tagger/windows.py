"""Text to 512-token windows to BIO, and predictions back to character spans.

DESIGN §12.1. Four things happen here and each is a registered decision:

  1. **Windowing** — 512 tokens, stride 128. A document longer than one window becomes
     several, overlapping by 128 tokens.
  2. **Labelling** — gold spans to BIO per token. Tokens overlapping a §9.1 excluded span
     are masked out of the loss rather than labelled `O`.
  3. **Window reconciliation** — a token in the overlap of two windows has two predictions,
     and they are reconciled **per token by maximum probability**, before any span exists.
  4. **Decoding** — BIO back to character spans, with a confidence per span.

Step 3 is per token and not per span on purpose. Reconciling spans would leave the case
where a window boundary cuts a span in two and the two halves decode to different types
with no rule for which survives; reconciling tokens has no such case, because a token is
in the overlap or it is not.

**No surface, token string or corpus text appears in any message raised here.** Window
indices, token indices, character offsets and lengths locate every fault this module can
report. `tests/test_tagger_windows.py` asserts it.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..corpora.base import CorpusError, Document, Span
from .labels import IGNORE_INDEX, OUTSIDE, label_ids, label_names, phi_type_of

#: The registered window geometry (DESIGN §12.1). Defaults rather than constants at the
#: call sites: `registration.yaml` is the authority and these are what it says, so a
#: caller that wants something else has to say so and show up in a diff.
MAX_TOKENS = 512
STRIDE = 128


@dataclass(slots=True)
class Window:
    """One 512-token view of a document.

    `offsets[i]` is the `(start, end)` character extent of token `i`, or `(0, 0)` for a
    special token. `is_token[i]` is the thing to branch on — a real token may legitimately
    start at character 0, so `(0, 0)` is ambiguous on the first token of a document and
    `offsets` alone cannot be the test. That ambiguity is a one-character bug that only
    shows on documents whose first token is PHI, which is a population this project's
    corpora are full of.
    """

    doc_id: str
    index: int
    input_ids: list[int]
    attention_mask: list[int]
    offsets: list[tuple[int, int]]
    is_token: list[bool]

    def __post_init__(self) -> None:
        n = len(self.input_ids)
        for name, value in (("attention_mask", self.attention_mask),
                            ("offsets", self.offsets),
                            ("is_token", self.is_token)):
            if len(value) != n:
                raise CorpusError(
                    f"{self.doc_id}: window {self.index} has {n} input_ids and "
                    f"{len(value)} {name} — the four lists are one row per token "
                    "and a mismatch means the encoder's outputs were assembled "
                    "from different calls"
                )


def encode(tokenizer, doc: Document, *, max_tokens: int = MAX_TOKENS,
           stride: int = STRIDE) -> list[Window]:
    """Tokenise one document into overlapping windows.

    Requires a *fast* tokenizer: the character offsets are the whole point, and the slow
    one does not produce them. Refused rather than worked around — a fallback that
    reconstructed offsets by re-matching token strings against the text would be a second
    offset implementation, and DESIGN §9.7's rule is that offsets are asserted rather than
    trusted.
    """
    if not getattr(tokenizer, "is_fast", False):
        raise CorpusError(
            "the tagger needs a fast tokenizer for character offsets; got a slow one. "
            "Reconstructing offsets from token strings would be a second offset "
            "implementation and DESIGN §9.7 asserts offsets rather than deriving them."
        )
    encoded = tokenizer(
        doc.text,
        truncation=True,
        max_length=max_tokens,
        stride=stride,
        return_overflowing_tokens=True,
        return_offsets_mapping=True,
        add_special_tokens=True,
    )
    windows: list[Window] = []
    for index in range(len(encoded["input_ids"])):
        offsets = [tuple(pair) for pair in encoded["offset_mapping"][index]]
        special = encoded.sequence_ids(index)
        windows.append(Window(
            doc_id=doc.doc_id,
            index=index,
            input_ids=list(encoded["input_ids"][index]),
            attention_mask=list(encoded["attention_mask"][index]),
            offsets=offsets,
            #: `sequence_ids()` is None exactly on special tokens, which is the
            #: unambiguous test. See `Window.is_token`'s note on why `(0, 0)` is not.
            is_token=[sid is not None for sid in special],
        ))
    if not windows:
        raise CorpusError(
            f"{doc.doc_id}: tokenising a {len(doc.text)}-character document produced "
            "no windows"
        )
    return windows


def bio_labels(window: Window, spans: list[Span]) -> list[int]:
    """BIO label ids for one window's tokens.

    `spans` are the document's gold spans — **all** of them, excluded ones included,
    because the excluded ones are what drives the masking. Passing only the in-scope spans
    would silently relabel an excluded extent `O`, which trains the model that it is not
    PHI; DESIGN §12.1 says it is neither a positive nor a negative.

    Precedence when a token overlaps two spans: the first in-scope span wins, and an
    excluded span masks only a token no in-scope span claimed. The corpora do ship
    overlapping annotations (DESIGN §3's masker note), and the alternative — masking a
    token an in-scope span covers — would drop real positives in favour of a type the
    project does not score.
    """
    ids = label_ids()
    in_scope = [s for s in spans if s.in_scope]
    excluded = [s for s in spans if not s.in_scope]
    labels: list[int] = []
    for position, (is_token, (start, end)) in enumerate(
            zip(window.is_token, window.offsets)):
        if not is_token or end <= start:
            labels.append(IGNORE_INDEX)
            continue
        claimed = _first_overlapping(in_scope, start, end)
        if claimed is not None:
            prefix = "B" if start <= claimed.start else "I"
            labels.append(ids[f"{prefix}-{claimed.phi_type}"])
            continue
        if _first_overlapping(excluded, start, end) is not None:
            labels.append(IGNORE_INDEX)
            continue
        labels.append(ids[OUTSIDE])
    return labels


def _first_overlapping(spans: list[Span], start: int, end: int) -> Span | None:
    for span in spans:
        if span.start < end and start < span.end:
            return span
    return None


@dataclass(slots=True)
class TokenPrediction:
    """One reconciled token: where it is, and the distribution over labels.

    `probs` is the full distribution and not just the argmax, because the span confidence
    (DESIGN §12.1) is a mean over the probability of the *decoded* label, and the decoder
    can pick a label that is not the token's own argmax when it repairs a BIO sequence.
    Carrying only the argmax would make the confidence of such a span unavailable, and the
    available substitute — the argmax probability — would be the confidence of a label the
    span does not have.
    """

    start: int
    end: int
    probs: list[float]


def reconcile(windows: list[Window],
              window_probs: list[list[list[float]]]) -> list[TokenPrediction]:
    """Per token, keep the window whose prediction for it is most confident.

    DESIGN §12.1's `per_token_max_probability`. A token is identified by its character
    extent, which is what makes "the same token" well defined across two windows.

    The comparison is on `max(probs)` — the confidence of whatever that window thinks the
    token is. Not on the probability of a particular label: the overlap exists because a
    token near a window edge has less context on one side, and the window that is more
    sure is the one with more context, whatever it is sure of.
    """
    if len(windows) != len(window_probs):
        raise CorpusError(
            f"reconcile got {len(windows)} windows and {len(window_probs)} "
            "probability blocks"
        )
    best: dict[tuple[int, int], list[float]] = {}
    for window, probs in zip(windows, window_probs):
        if len(probs) != len(window.input_ids):
            raise CorpusError(
                f"{window.doc_id}: window {window.index} has "
                f"{len(window.input_ids)} tokens and {len(probs)} probability rows"
            )
        for position, (is_token, (start, end)) in enumerate(
                zip(window.is_token, window.offsets)):
            if not is_token or end <= start:
                continue
            row = probs[position]
            key = (start, end)
            current = best.get(key)
            if current is None or max(row) > max(current):
                best[key] = list(row)
    return [TokenPrediction(start=s, end=e, probs=p)
            for (s, e), p in sorted(best.items())]


def decode_spans(tokens: list[TokenPrediction], text: str, *,
                 detector: str) -> list[Span]:
    """BIO over reconciled tokens to character spans, each with a confidence.

    **A stray `I-X` opens a span.** The alternative is to drop it, and dropping is the
    worse error for this project: a tagger that emits `I-NAME` with no `B-NAME` before it
    has found a name and mis-signalled its first token, and discarding that is a leak. The
    headline metric is the leak rate (CLAUDE.md), so a decoder whose failure mode is
    "finds nothing" is the wrong failure mode to choose.

    **Span extents are whitespace-trimmed against `text`.** XLM-R's sentencepiece offsets
    can include the space before a word, so an untrimmed span is off by one at its left
    edge against every gold annotation that does not include it. Trimming is done against
    the text rather than by adjusting the offset, because the tokenizer's convention is
    not ours to assume and the text is the authority (DESIGN §9.7).

    Confidence is the mean probability **of the label the span was decoded as**, over the
    span's tokens — `registration.yaml`'s `mean_token_probability`.

    Every span returned carries `layer="tagger"` and **no `rule_id`**: DESIGN §9.3 requires
    a rules-family span to carry one and a tagger span not to, and that asymmetry is what
    keeps per-rule precision attributable.
    """
    names = label_names()
    spans: list[Span] = []
    open_type: str | None = None
    members: list[tuple[int, int, float]] = []

    def close() -> None:
        nonlocal open_type, members
        if open_type is not None and members:
            start = members[0][0]
            end = members[-1][1]
            start, end = _trim(text, start, end)
            if end > start:
                spans.append(Span(
                    start=start,
                    end=end,
                    surface=text[start:end],
                    subtype=open_type,
                    phi_type=open_type,
                    layer="tagger",
                    detector=detector,
                    rule_id=None,
                    score=sum(m[2] for m in members) / len(members),
                ))
        open_type = None
        members = []

    for token in tokens:
        index = max(range(len(token.probs)), key=lambda i: token.probs[i])
        label = names[index]
        phi_type = phi_type_of(label)
        if phi_type is None:
            close()
            continue
        if label.startswith("B-") or phi_type != open_type:
            close()
            open_type = phi_type
        members.append((token.start, token.end, token.probs[index]))
    close()
    return spans


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end
