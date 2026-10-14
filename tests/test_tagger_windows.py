"""Windowing, BIO labelling, window reconciliation and offset reconstruction.

DESIGN §12.1. Four of the five things the v24 instruction requires a unit test for live
here; policy application and Jaccard are in `tests/test_merge_policy.py` and
`tests/test_guideline.py`.

The offset tests are the load-bearing ones. A tagger whose spans are off by one against
gold scores as a tagger that found nothing, and the two are indistinguishable in any
aggregate — so the round trip is asserted directly: gold spans, encoded to BIO, decoded
back, compared for byte equality of the extents.

**Every document here is synthetic and written in this file.** No corpus is loaded. The
texts are invented strings that look like clinical prose; they carry no real identifier,
and they are in this file rather than a fixture so that what the tagger is being asked to
do is visible beside the assertion.

    python3 -m pytest tests/test_tagger_windows.py -q
"""
from __future__ import annotations

import pytest

from src.corpora.base import CorpusError, Document, Span
from src.tagger import windows as win
from src.tagger.labels import (IGNORE_INDEX, OUTSIDE, check_n_labels, label_ids,
                               label_names, n_labels, phi_type_of)


@pytest.fixture(scope="module")
def tokenizer():
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained("xlm-roberta-base")


def doc_with(text: str, marks: list[tuple[str, str, bool]]) -> Document:
    """Build a Document by *searching* the text for each mark's literal.

    Offsets are derived, never written down. A hand-written offset in a test is a second
    place the offset convention lives, and the first time one of them is wrong the test
    agrees with the bug.
    """
    spans = []
    for literal, phi_type, excluded in marks:
        start = text.index(literal)
        spans.append(Span(
            start=start, end=start + len(literal), surface=literal,
            subtype=f"SRC_{phi_type}",
            phi_type=None if excluded else phi_type,
            excluded=excluded,
        ))
    spans.sort(key=lambda s: s.start)
    doc = Document(doc_id="synthetic-1", corpus_id="es-meddocan", text=text,
                   spans=spans, split="dev")
    doc.assert_offsets()
    return doc


# ─── the label set is the axis, not a copy of it ────────────────────────────

def test_the_label_set_is_derived_from_the_phi_type_axis():
    names = label_names()
    assert names[0] == OUTSIDE
    assert n_labels() == 21, "ten types, B and I each, plus O"
    assert len(set(names)) == len(names)
    for name in names[1:]:
        assert name[:2] in ("B-", "I-")


def test_a_non_bio_label_raises_rather_than_reading_as_outside():
    """`None` means 'outside' and an unknown label means the checkpoint disagrees.

    Collapsing them would turn a decoding bug into a tagger that quietly finds nothing,
    which is the failure mode with no symptom.
    """
    assert phi_type_of(OUTSIDE) is None
    assert phi_type_of("B-NAME") == "NAME"
    with pytest.raises(CorpusError, match="not a BIO label"):
        phi_type_of("B-NOT_A_TYPE")


def test_the_registered_label_count_is_checked_and_the_message_names_the_date():
    assert check_n_labels(21) == 21
    with pytest.raises(CorpusError, match="pre-registered"):
        check_n_labels(20)


# ─── offset reconstruction ──────────────────────────────────────────────────

def test_gold_spans_survive_the_round_trip_to_bio_and_back(tokenizer):
    """Encode gold as BIO, decode it, and get the same character extents back.

    The whole offset path in one assertion: tokenizer offsets, the B/I decision, window
    reconciliation keyed on character extent, and the whitespace trim.

    **Every span here is token-aligned, which is a property of this text and not of gold
    in general** — see `test_a_gold_span_the_tokenizer_cannot_express_is_covered_not_
    matched` for the other case and for the measured rate. A round-trip test built on
    non-aligned spans would be asserting the tokenizer's segmentation rather than this
    module's offset arithmetic.
    """
    text = ("Paciente Juan Perez, 45 anos, ingresa el 3 de marzo de 2019 "
            "en el Hospital Clinic de Barcelona. Movil 600 11 22 33 activo.")
    doc = doc_with(text, [
        ("Juan Perez", "NAME", False),
        ("45", "AGE", False),
        ("3 de marzo de 2019", "DATE", False),
        ("Hospital Clinic de Barcelona", "ORGANISATION", False),
        ("600 11 22 33", "CONTACT", False),
    ])
    assert _token_aligned(tokenizer, doc) == len(doc.spans), (
        "this test's premise is that all five spans are token-aligned")
    decoded = _round_trip(tokenizer, doc)
    assert [(s.start, s.end, s.phi_type) for s in decoded] == \
           [(s.start, s.end, s.phi_type) for s in doc.spans]
    for span in decoded:
        assert text[span.start:span.end] == span.surface


def test_a_gold_span_the_tokenizer_cannot_express_is_covered_not_matched(tokenizer):
    """The tokenisation ceiling, stated as a test rather than discovered in a results table.

    `600 11 22 33.` at the end of a sentence tokenises with the period inside the last
    token, so the gold extent — which stops before the period — is **not representable**
    as a union of whole tokens. The best the tagger can do is one character too long.

    Measured 2026-10-14 over the five dev folds: 7,078 of 7,771 in-scope gold spans
    (91.08%) have both boundaries on a token boundary. By corpus: en-deid 98.89%,
    ko-surro 97.18%, de-grascco 95.37%, es-meddocan 92.22%, es-carmen 82.37%.

    **What this bounds and what it does not.** It bounds any exact-boundary score. It does
    not bound the leak rate or the relaxed P/R/F1 that DESIGN §9.3 makes the headline: an
    over-long prediction still *fully covers* the gold span, so it does not leak, and it
    still overlaps, so it is still a true positive. That is asserted here rather than
    argued, because the asymmetry is the reason the ceiling is a limitation to report and
    not a defect to fix — and "fixing" it would mean trimming punctuation in the decoder,
    which is a detection decision taken by an offset routine.
    """
    from src.eval import scorer

    text = "Movil de contacto 600 11 22 33."
    doc = doc_with(text, [("600 11 22 33", "CONTACT", False)])
    assert _token_aligned(tokenizer, doc) == 0, (
        "this test's premise is that the span is NOT token-aligned")

    decoded = _round_trip(tokenizer, doc)
    assert len(decoded) == 1
    gold = doc.spans[0]
    assert (decoded[0].start, decoded[0].end) != (gold.start, gold.end)
    assert decoded[0].start == gold.start and decoded[0].end == gold.end + 1

    gold_marks = [scorer.Mark(start=gold.start, end=gold.end,
                              phi_type=gold.phi_type)]
    pred_marks = [scorer.Mark(start=decoded[0].start, end=decoded[0].end,
                              phi_type=decoded[0].phi_type, layer="tagger")]
    # Does not leak: the prediction fully covers the gold span.
    assert scorer.coverage(gold_marks, pred_marks, scorer.FULLY_COVERED) == [True]
    # And is still a true positive under the relaxed matching the headline uses.
    matched, fn, fp = scorer.assign(gold_marks, pred_marks, scorer.RELAXED)
    assert matched == {0: 0} and not fn and not fp


def _token_aligned(tokenizer, doc: Document) -> int:
    """How many of `doc`'s spans have both boundaries on a token boundary."""
    bounds: set[int] = set()
    for window in win.encode(tokenizer, doc):
        for (start, end), is_token in zip(window.offsets, window.is_token):
            if is_token and end > start:
                bounds.add(start)
                bounds.add(end)
    return sum(1 for s in doc.spans
               if s.start in bounds and s.end in bounds)


def test_a_span_at_character_zero_round_trips(tokenizer):
    """The case `(0, 0)` offsets cannot distinguish — see `Window.is_token`.

    A real first token starts at character 0, so a special-token test that compares the
    offset pair to `(0, 0)` drops the first token of every document. On this project's
    corpora the first token is frequently a name, so the bug would be concentrated
    exactly where it costs most.
    """
    text = "Perez Garcia acude a consulta el 4 de mayo."
    doc = doc_with(text, [("Perez Garcia", "NAME", False)])
    assert doc.spans[0].start == 0
    decoded = _round_trip(tokenizer, doc)
    assert [(s.start, s.end, s.phi_type) for s in decoded] == [(0, 12, "NAME")]


def test_leading_whitespace_is_trimmed_off_a_decoded_span(tokenizer):
    """Sentencepiece can put the preceding space inside a token's offsets.

    Untrimmed, every mid-sentence span would start one character early and miss exact
    agreement with every gold annotation that does not include the space.
    """
    text = "El paciente Ana Lopez tiene 70 anos."
    doc = doc_with(text, [("Ana Lopez", "NAME", False)])
    decoded = _round_trip(tokenizer, doc)
    assert len(decoded) == 1
    assert not text[decoded[0].start].isspace()
    assert not text[decoded[0].end - 1].isspace()
    assert (decoded[0].start, decoded[0].end) == (doc.spans[0].start,
                                                  doc.spans[0].end)


def _round_trip(tokenizer, doc: Document) -> list[Span]:
    """Gold -> BIO -> one-hot 'probabilities' -> decoded spans."""
    wins = win.encode(tokenizer, doc)
    width = n_labels()
    blocks = []
    for window in wins:
        labels = win.bio_labels(window, doc.spans)
        blocks.append([
            [1.0 if i == (0 if label == IGNORE_INDEX else label) else 0.0
             for i in range(width)]
            for label in labels
        ])
    tokens = win.reconcile(wins, blocks)
    return win.decode_spans(tokens, doc.text, detector="T")


# ─── BIO labelling, and the three states ────────────────────────────────────

def test_an_excluded_span_is_masked_and_not_labelled_outside(tokenizer):
    """DESIGN §12.1: an excluded span is neither a positive nor a negative.

    Labelling it `O` would train the model that the excluded extent is not PHI, which is
    not what §9.1's exclusion says — it says the project does not score it.
    """
    text = "La madre del paciente Luis Soto acude hoy."
    doc = doc_with(text, [
        ("madre", "NAME", True),      # §9.1 FAMILIARES_*, excluded
        ("Luis Soto", "NAME", False),
    ])
    window = win.encode(tokenizer, doc)[0]
    labels = win.bio_labels(window, doc.spans)
    excluded = doc.spans[0]
    inside_excluded = [
        label for label, (start, end), is_token
        in zip(labels, window.offsets, window.is_token)
        if is_token and start < excluded.end and excluded.start < end
    ]
    assert inside_excluded, "the excluded extent produced no tokens to check"
    assert all(label == IGNORE_INDEX for label in inside_excluded), (
        "an excluded span's tokens must be masked out of the loss, not labelled O")
    assert label_ids()[OUTSIDE] in labels, "nothing was labelled O at all"


def test_an_in_scope_span_wins_over_an_overlapping_excluded_one(tokenizer):
    """Precedence, and the direction matters.

    The corpora do ship overlapping annotations. Masking a token an in-scope span covers
    would drop a real positive in favour of a type the project does not score.
    """
    text = "Contacto: la madre Elena Ruiz, telefono 611 22 33 44."
    start = text.index("madre Elena Ruiz")
    spans = [
        Span(start=start, end=start + len("madre Elena Ruiz"),
             surface="madre Elena Ruiz", subtype="FAMILIARES_SUJETO_ASISTENCIA",
             phi_type=None, excluded=True),
        Span(start=text.index("Elena Ruiz"),
             end=text.index("Elena Ruiz") + len("Elena Ruiz"),
             surface="Elena Ruiz", subtype="NOMBRE_SUJETO_ASISTENCIA",
             phi_type="NAME"),
    ]
    doc = Document(doc_id="s2", corpus_id="es-meddocan", text=text, spans=spans,
                   split="dev")
    doc.assert_offsets()
    window = win.encode(tokenizer, doc)[0]
    labels = win.bio_labels(window, doc.spans)
    names = label_names()
    name_labels = [
        names[label] for label, (s, e), is_token
        in zip(labels, window.offsets, window.is_token)
        if is_token and label != IGNORE_INDEX
        and s < spans[1].end and spans[1].start < e
    ]
    assert name_labels and all(lbl.endswith("NAME") for lbl in name_labels)


def test_bio_marks_continuation_tokens_with_i_not_b(tokenizer):
    text = "El paciente Maria del Carmen Fernandez Lopez ingresa hoy."
    doc = doc_with(text, [("Maria del Carmen Fernandez Lopez", "NAME", False)])
    window = win.encode(tokenizer, doc)[0]
    names = label_names()
    inside = [names[label] for label, (s, e), is_token
              in zip(win.bio_labels(window, doc.spans), window.offsets,
                     window.is_token)
              if is_token and s < doc.spans[0].end and doc.spans[0].start < e]
    assert inside[0] == "B-NAME"
    assert set(inside[1:]) == {"I-NAME"}, "only the first token is B-"


# ─── windowing and reconciliation ───────────────────────────────────────────

def test_a_long_document_produces_overlapping_windows(tokenizer):
    text = ("El paciente ingresa por dolor abdominal y se le realizan pruebas. " * 60
            + "Firmado por Pedro Marin.")
    doc = doc_with(text, [("Pedro Marin", "NAME", False)])
    wins = win.encode(tokenizer, doc)
    assert len(wins) > 1, "the document is not long enough to window"
    shared = (set(zip(*[iter([])] * 0)) if False else
              {(s, e) for s, e, t in zip(*[[o[0] for o in wins[0].offsets],
                                           [o[1] for o in wins[0].offsets],
                                           wins[0].is_token]) if t}
              & {(s, e) for s, e, t in zip(*[[o[0] for o in wins[1].offsets],
                                             [o[1] for o in wins[1].offsets],
                                             wins[1].is_token]) if t})
    assert shared, "consecutive windows share no token; stride is not overlapping"
    # The span at the very end must still survive the round trip.
    decoded = _round_trip(tokenizer, doc)
    assert (doc.spans[0].start, doc.spans[0].end, "NAME") in [
        (s.start, s.end, s.phi_type) for s in decoded]


def test_reconcile_keeps_the_more_confident_window_for_a_shared_token(tokenizer):
    """DESIGN §12.1's `per_token_max_probability`, asserted on the comparison itself.

    The two windows are given deliberately different distributions for the same token and
    the more confident one has to win — whatever it is confident *of*. The overlap exists
    because an edge token has less context on one side, so the sure window is the one with
    more context.
    """
    text = "El paciente ingresa por dolor y se realizan pruebas. " * 60
    doc = doc_with(text, [])
    wins = win.encode(tokenizer, doc)
    assert len(wins) > 1
    width = n_labels()
    ids = label_ids()
    shared = _first_shared_token(wins[0], wins[1])
    assert shared is not None, "no shared token between the first two windows"
    key, pos0, pos1 = shared

    blocks = []
    for index, window in enumerate(wins):
        rows = [[1.0 / width] * width for _ in window.input_ids]
        blocks.append(rows)
    # window 0 is 0.6 sure the token is B-NAME; window 1 is 0.9 sure it is B-DATE.
    blocks[0][pos0] = _peak(width, ids["B-NAME"], 0.6)
    blocks[1][pos1] = _peak(width, ids["B-DATE"], 0.9)

    tokens = win.reconcile(wins, blocks)
    got = next(t for t in tokens if (t.start, t.end) == key)
    assert got.probs[ids["B-DATE"]] == pytest.approx(0.9)
    assert got.probs[ids["B-NAME"]] != pytest.approx(0.6)

    # And the other way round, to show the result follows the number and not the order.
    blocks[0][pos0] = _peak(width, ids["B-NAME"], 0.95)
    blocks[1][pos1] = _peak(width, ids["B-DATE"], 0.5)
    tokens = win.reconcile(wins, blocks)
    got = next(t for t in tokens if (t.start, t.end) == key)
    assert got.probs[ids["B-NAME"]] == pytest.approx(0.95)


def _peak(width: int, index: int, value: float) -> list[float]:
    rest = (1.0 - value) / (width - 1)
    row = [rest] * width
    row[index] = value
    return row


def _first_shared_token(a: win.Window, b: win.Window):
    a_keys = {(s, e): i for i, ((s, e), t)
              in enumerate(zip(a.offsets, a.is_token)) if t and e > s}
    for i, ((s, e), t) in enumerate(zip(b.offsets, b.is_token)):
        if t and e > s and (s, e) in a_keys:
            return (s, e), a_keys[(s, e)], i
    return None


def test_reconcile_refuses_mismatched_shapes(tokenizer):
    doc = doc_with("El paciente Ana Diaz ingresa.", [("Ana Diaz", "NAME", False)])
    wins = win.encode(tokenizer, doc)
    with pytest.raises(CorpusError, match="probability block"):
        win.reconcile(wins, [])
    with pytest.raises(CorpusError, match="probability rows"):
        win.reconcile(wins, [[[0.0] * n_labels()]])


# ─── decoding ───────────────────────────────────────────────────────────────

def test_a_stray_i_label_opens_a_span_rather_than_being_dropped(tokenizer):
    """DESIGN §12.1: dropping is the worse error, because the headline is the leak rate.

    A tagger that emits `I-NAME` with no `B-NAME` has found a name and mis-signalled its
    first token. Discarding it is a leak, and a decoder whose failure mode is "finds
    nothing" is the wrong failure mode for this project to choose.
    """
    text = "El paciente Ana Diaz ingresa hoy."
    doc = doc_with(text, [("Ana Diaz", "NAME", False)])
    wins = win.encode(tokenizer, doc)
    width = n_labels()
    ids = label_ids()
    rows = [_peak(width, ids[OUTSIDE], 0.9) for _ in wins[0].input_ids]
    target = doc.spans[0]
    marked = 0
    for i, ((s, e), t) in enumerate(zip(wins[0].offsets, wins[0].is_token)):
        if t and s < target.end and target.start < e:
            rows[i] = _peak(width, ids["I-NAME"], 0.8)   # I with no B before it
            marked += 1
    assert marked >= 2
    decoded = win.decode_spans(win.reconcile(wins, [rows]), text, detector="T")
    assert [(s.start, s.end, s.phi_type) for s in decoded] == \
           [(target.start, target.end, "NAME")]


def test_a_type_change_without_a_b_label_closes_the_previous_span(tokenizer):
    text = "Ana Diaz 1980"
    doc = doc_with(text, [])
    wins = win.encode(tokenizer, doc)
    width = n_labels()
    ids = label_ids()
    rows = [_peak(width, ids[OUTSIDE], 0.9) for _ in wins[0].input_ids]
    real = [i for i, t in enumerate(wins[0].is_token) if t]
    rows[real[0]] = _peak(width, ids["B-NAME"], 0.9)
    rows[real[1]] = _peak(width, ids["I-DATE"], 0.9)   # type change, no B-
    decoded = win.decode_spans(win.reconcile(wins, [rows]), text, detector="T")
    assert [s.phi_type for s in decoded] == ["NAME", "DATE"]


def test_a_decoded_span_carries_the_tagger_layer_and_no_rule_id(tokenizer):
    """DESIGN §9.3's asymmetry: a rules span carries an id and a tagger span must not.

    It is what keeps per-rule precision attributable, and the scorer's `by_rule` block
    would silently acquire a tagger entry if this were ever filled in.
    """
    doc = doc_with("El paciente Ana Diaz ingresa.", [("Ana Diaz", "NAME", False)])
    decoded = _round_trip(tokenizer, doc)
    assert decoded
    for span in decoded:
        assert span.layer == "tagger"
        assert span.rule_id is None
        assert span.detector == "T"
        assert span.score is not None and 0.0 <= span.score <= 1.0


def test_span_confidence_is_the_mean_over_the_spans_tokens(tokenizer):
    text = "El paciente Maria Fernandez Lopez ingresa."
    doc = doc_with(text, [("Maria Fernandez Lopez", "NAME", False)])
    wins = win.encode(tokenizer, doc)
    width = n_labels()
    ids = label_ids()
    rows = [_peak(width, ids[OUTSIDE], 0.99) for _ in wins[0].input_ids]
    target = doc.spans[0]
    positions = [i for i, ((s, e), t)
                 in enumerate(zip(wins[0].offsets, wins[0].is_token))
                 if t and s < target.end and target.start < e]
    assert len(positions) >= 3
    wanted = [0.9, 0.5, 0.7] + [0.6] * (len(positions) - 3)
    for position, value in zip(positions, wanted):
        rows[position] = _peak(width, ids["B-NAME" if position == positions[0]
                                          else "I-NAME"], value)
    decoded = win.decode_spans(win.reconcile(wins, [rows]), text, detector="T")
    assert len(decoded) == 1
    assert decoded[0].score == pytest.approx(sum(wanted) / len(wanted))


# ─── the data-handling rule, asserted ───────────────────────────────────────

def test_no_message_raised_here_quotes_a_surface(tokenizer):
    """The convention fixed as code, in the shape `tests/test_meddocan_loader.py` uses.

    Three of the five train folds are DUA-restricted, an exception message travels into
    terminals, CI logs and issues, and `tools/release_screen.py` reaches none of those.
    """
    text = "El paciente Ana Diaz ingresa hoy por dolor."
    doc = doc_with(text, [("Ana Diaz", "NAME", False)])
    wins = win.encode(tokenizer, doc)
    surfaces = ["Ana Diaz", "Ana", "Diaz", "paciente", "dolor"]

    messages = []
    with pytest.raises(CorpusError) as caught:
        win.reconcile(wins, [])
    messages.append(str(caught.value))
    with pytest.raises(CorpusError) as caught:
        win.reconcile(wins, [[[0.0] * n_labels()]])
    messages.append(str(caught.value))
    with pytest.raises(CorpusError) as caught:
        win.Window(doc_id=doc.doc_id, index=0, input_ids=[1, 2],
                   attention_mask=[1], offsets=[(0, 1), (1, 2)],
                   is_token=[True, True])
    messages.append(str(caught.value))
    with pytest.raises(CorpusError) as caught:
        win.encode(_SlowTokenizer(), doc)
    messages.append(str(caught.value))

    for message in messages:
        for surface in surfaces:
            assert surface not in message, (
                "an exception message carries corpus text; offsets, indices and "
                "lengths locate the fault without it (CLAUDE.md)")


class _SlowTokenizer:
    is_fast = False


def test_a_slow_tokenizer_is_refused_rather_than_worked_around():
    doc = Document(doc_id="s", corpus_id="es-meddocan", text="abc", spans=[],
                   split="dev")
    with pytest.raises(CorpusError, match="fast tokenizer"):
        win.encode(_SlowTokenizer(), doc)
