"""The Arbiter's text-blocking guard, and its response protocol. DESIGN §12.3.

The guard test the v24 instruction requires. It is the one component in this project whose
correctness is a DUA question rather than a measurement question: the Arbiter is the only
agent that must be sent **no** corpus text, so the check over its prompt is a complete
check rather than a heuristic, and the thing worth testing is that it refuses.

Both directions are exercised. The negative cases plant something surface-shaped in a
frame or a rationale and require a refusal; the positive case requires a real assembled
frame to pass, because a guard that refuses everything also passes every refusal test.

    python3 -m pytest tests/test_arbiter.py -q
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from src.corpora.base import CorpusError, Document, Span, canonical_types
from src.merge import arbiter as arb
from src.merge import policy as pol
from src.merge import selector as sel

REGISTRATION = Path("experiments/hybrid_v24/registration.yaml")


# ─── the template is the registered one ─────────────────────────────────────

def test_the_template_hash_matches_the_registration():
    """Pinned in two files, so the two are compared. A hash in one place is a hash
    nobody checks."""
    registered = yaml.safe_load(REGISTRATION.read_text(encoding="utf-8"))
    assert registered["rt_arb"]["prompt_sha256"] == arb.TEMPLATE_SHA256
    on_disk = hashlib.sha256(arb.TEMPLATE.read_bytes()).hexdigest()
    assert on_disk == arb.TEMPLATE_SHA256, (
        "docs/prompts/arbiter.md moved after 2026-10-14; amend DESIGN §12.3 and the "
        "registration together, or restore the file")


def test_a_moved_template_is_refused_rather_than_sent(monkeypatch, tmp_path):
    moved = tmp_path / "arbiter.md"
    moved.write_text("a different frame\n", encoding="utf-8")
    monkeypatch.setattr(arb, "TEMPLATE", moved)
    with pytest.raises(arb.ArbiterError, match="registration pins"):
        arb.template_text()


def test_an_absent_template_has_no_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(arb, "TEMPLATE", tmp_path / "nothing.md")
    with pytest.raises(arb.ArbiterError, match="no fallback template"):
        arb.template_text()


def test_the_template_carries_no_code_fence():
    """It is a live template under the fence policy (DESIGN §6.3, §12.3).

    Two calls in this project have already died on a fenced response that copied a fenced
    example, fourteen days apart. `tests/test_prompt.py` governs the directory; this
    asserts it for the one file this module sends.
    """
    text = arb.TEMPLATE.read_text(encoding="utf-8")
    assert not any(line.startswith("```") for line in text.splitlines())


# ─── the outbound guard ─────────────────────────────────────────────────────

def _table() -> dict[str, sel.TypeRow]:
    text = "x" * 120
    docs = [Document(doc_id="d0", corpus_id="es-meddocan", text=text, split="dev",
                     spans=[Span(start=0, end=5, surface="xxxxx", subtype="S",
                                 phi_type="NAME"),
                            Span(start=20, end=25, surface="xxxxx", subtype="S",
                                 phi_type="DATE")])]
    rules = {"d0": [Span(start=0, end=5, surface="xxxxx", subtype="S",
                         phi_type="NAME", layer="regex_checksum", detector="R",
                         rule_id="es:a")]}
    tagger = {"d0": [Span(start=20, end=25, surface="xxxxx", subtype="DATE",
                          phi_type="DATE", layer="tagger", detector="T",
                          score=0.95)]}
    return sel.build_table(docs, rules, tagger)


def test_a_real_assembled_frame_passes_the_guard():
    """The positive control. A guard that refuses everything passes every refusal test."""
    frame = arb.build_frame(corpus="es-meddocan", n_dev_docs=250, delta=0.005,
                            table=_table())
    arb.check_frame(frame)      # must not raise
    assert "NAME" in frame and "UNION@0.5" in frame
    assert "xxxxx" not in frame, "the frame must carry no surface at all"


def test_the_whole_prompt_assembles_and_records_the_guards_verdict():
    prompt = arb.assemble(corpus="es-meddocan", n_dev_docs=250, delta=0.005,
                          table=_table())
    reference = prompt.reference()
    assert reference["block"] == "arbiter"
    assert reference["frame_guard"] == "passed"
    assert reference["template_sha256"] == arb.TEMPLATE_SHA256
    assert set(reference["types_shown"]) == {"NAME", "DATE"}
    assert "OTHER" in reference["types_fixed_at_baseline"]
    # The reference form is what gets logged, and it carries no text.
    assert "xxxxx" not in repr(reference)


def test_a_non_ascii_character_in_the_frame_is_refused():
    """Three of the five corpora are written in non-ASCII scripts, so this check needs
    no judgement about which characters are suspicious."""
    with pytest.raises(arb.ArbiterError, match="non-ASCII"):
        arb.check_frame("type NAME gold 5 sparse yes\n  note 김환자")


def test_an_undeclared_alphabetic_token_is_refused():
    """The load-bearing check: a Latin-script surface is ASCII and passes the first one."""
    with pytest.raises(arb.ArbiterError, match="undeclared alphabetic token"):
        arb.check_frame("type NAME gold 5 sparse yes example Hospital Clinic")


def test_a_line_matching_no_declared_form_is_refused():
    """The complete check. A line built entirely from declared words still fails.

    This is the case the token whitelist cannot catch and is why the grammar exists:
    every word below is in `FRAME_WORDS` or an axis, and the line is still not a form
    this module emits.
    """
    with pytest.raises(arb.ArbiterError, match="matches\nnone|matches none"):
        arb.check_frame("type gold both neither tagger rules only")


def test_a_four_digit_gold_count_is_accepted():
    """The direction the replaced heuristic got wrong.

    A per-type gold count on `es-meddocan` reaches four digits, so a "no run of four or
    more digits" rule would have refused real data. The grammar accepts a number in a
    numeric position and refuses one anywhere else, which is the distinction that
    actually matters.
    """
    arb.check_frame("type NAME gold 2134 sparse no")


def test_the_modules_own_six_decimal_output_is_accepted():
    """The other direction the heuristic got wrong: it was defeated by this module's own
    formatting, because `delta 0.005000` contains a six-digit run."""
    arb.check_frame("delta 0.005000")
    arb.check_frame("  policy UNION@0.5 tp 10 fp 2 fn 1 L_full 0.000000 "
                    "L_rel 0.000000 A 1.000000")


def test_the_corpus_id_is_one_token_and_not_two():
    """`es-meddocan` split on the hyphen into `es` plus `meddocan` until the guard's
    first test run, and the effect was that every real frame was refused for carrying
    its own corpus id. A hyphen-joined surface is still one undeclared token."""
    arb.check_frame("corpus es-meddocan")
    with pytest.raises(arb.ArbiterError, match="undeclared alphabetic token"):
        arb.check_frame("corpus Hospital-Clinic")


def test_the_guard_never_quotes_what_it_caught():
    """A guard that prints the offending token publishes it into the log — the exact harm
    it exists to prevent. Offsets and lengths locate it instead."""
    planted = ["Hospital", "김환자", "Perez", "Clinic"]
    frames = [
        "type NAME gold 5 sparse yes name Perez",
        "type NAME gold 5 sparse yes 김환자",
        "type DATE gold 5 sparse no 1987",
        "type ORGANISATION gold 5 sparse no Hospital Clinic",
    ]
    for frame in frames:
        with pytest.raises(arb.ArbiterError) as caught:
            arb.check_frame(frame)
        message = str(caught.value)
        for token in planted:
            assert token not in message, message
        # located, never quoted: an offset+length for a token, a line number +
        # character count for a line that matches no form.
        assert "offset" in message or "line 1" in message
        assert "No call was made." in message


def test_the_guard_refuses_before_the_frame_is_joined_to_the_template(monkeypatch):
    """A refusal must happen while the text is still a local and has never been a prompt.

    Asserted by making the template unreadable: if `assemble()` checked the frame after
    reading the template, this would raise the template's error instead of the guard's.
    """
    monkeypatch.setattr(arb, "build_frame", lambda **_: "bad Hospital token")
    calls = []
    monkeypatch.setattr(arb, "template_text",
                        lambda: calls.append(1) or "template")
    with pytest.raises(arb.ArbiterError, match="undeclared alphabetic token"):
        arb.assemble(corpus="es-meddocan", n_dev_docs=1, delta=0.005, table={})
    assert not calls, "the template was read before the frame was checked"


def test_the_allowed_words_come_from_the_axes_plus_a_written_out_list():
    """A guard whose vocabulary is derived from the frame it checks passes by
    construction, so the field labels are written out and the rest is read from axes."""
    allowed = arb.allowed_words()
    assert set(canonical_types()) <= allowed
    assert set(pol.policy_names()) <= allowed
    assert "es-meddocan".replace("-", "") not in allowed  # ids are not split
    assert "Hospital" not in allowed and "Perez" not in allowed


def test_a_type_with_no_dev_gold_is_absent_from_the_frame():
    """DESIGN §12.2 fixes them at the baseline before any chooser is called, so showing
    them would invite a policy the harness is not going to take."""
    frame = arb.build_frame(corpus="es-meddocan", n_dev_docs=250, delta=0.005,
                            table=_table())
    shown = [line for line in frame.splitlines() if line.startswith("type ")]
    assert {line.split()[1] for line in shown} == {"NAME", "DATE"}
    assert "PROFESSION" not in frame


# ─── the inbound guard, on the rationale ────────────────────────────────────

def test_the_metric_name_A_does_not_trip_the_capitalisation_check():
    """The defect that cost ten Bedrock calls on 2026-10-14.

    `A` is the quantity the Arbiter is asked to maximise, so nearly every rationale
    mentions it — and it is a single capital letter that is not a type name, a policy
    name or a corpus id. The capitalisation check consulted the axes only, so all ten
    round-1 responses were refused. Every one of them was well-formed bare JSON with
    zero fence lines: the model complied and the guard did not.
    """
    arb.check_rationale("chose T@0.5 because A is higher and the leak rate holds",
                        phi_type="AGE")
    for metric in ("A", "TP", "FP", "FN", "L_full", "L_rel"):
        arb.check_rationale(f"kept UNION since {metric} is unchanged", phi_type="NAME")


def test_a_declared_word_may_open_a_second_sentence():
    """Capitalised for grammar, not for reference. Granted only to declared words."""
    arb.check_rationale(
        "kept UNION for the leak rate. Sparse gold makes the rest unreliable",
        phi_type="AGE")
    arb.check_rationale("A is higher under T. Both cells are small", phi_type="NAME")


def test_capitalisation_is_a_signal_mid_sentence_and_not_at_a_sentence_opening():
    """The narrowing made after the guard refused `Tagger` at a rationale's start.

    Capitalisation only carries information mid-sentence: at an opening the capital is
    grammar and says nothing about whether the word is a name. A conditional exemption —
    only declared words may open a sentence — turns an English vocabulary gap into a
    refused response, and that cost real Bedrock calls twice on 2026-10-14.

    The residual risk, a surname opening a sentence, is accepted because `check_frame`
    makes a real leak structurally impossible: the frame's grammar is closed, so corpus
    text never reaches this agent, and anything name-shaped here is invented rather than
    copied. It is recorded as undeclared rather than ignored.
    """
    # mid-sentence: refused
    with pytest.raises(arb.ArbiterError, match="capitalised"):
        arb.check_rationale("the span Perez was missed twice", phi_type="NAME")
    # sentence opening: allowed, and recorded
    assert "perez" in arb.check_rationale("Perez was missed twice", phi_type="NAME")
    assert "tagger" not in arb.check_rationale(
        "Tagger finds all of them", phi_type="NAME"), "tagger is declared vocabulary"


def test_a_long_integer_the_frame_did_not_supply_is_refused_but_a_decimal_is_not():
    """The other narrowing of 2026-10-14, and the reason for the asymmetry.

    The agent *derives* figures — it rounds, and it subtracts one rate from another — so
    requiring every decimal to appear verbatim refuses a correct rationale for being
    arithmetic. A long integer is different: a year, a record number, a postcode and a
    phone number are all four-or-more-digit integers and none is derivable from a table
    of rates.
    """
    allowed = arb.frame_numbers("type NAME gold 2134 sparse no A 0.004525")
    # a derived decimal that is not in the frame: allowed
    arb.check_rationale("the gap is 0.0045 which fits", phi_type="NAME",
                        allowed_numbers=allowed)
    # a supplied integer: allowed
    arb.check_rationale("gold 2134 is large", phi_type="NAME",
                        allowed_numbers=allowed)
    # an identifier-shaped integer nobody supplied: refused
    with pytest.raises(arb.ArbiterError, match="digit integer"):
        arb.check_rationale("the record 1234567 leaked", phi_type="NAME",
                            allowed_numbers=allowed)
    with pytest.raises(arb.ArbiterError, match="digit integer"):
        arb.check_rationale("seen in 1987 only", phi_type="NAME",
                            allowed_numbers=allowed)


#: Rationales the Arbiter actually returned on 2026-10-14 (draw 1, de-grascco and
#: en-deid), copied from `experiments/hybrid_v24/dev_arbiter_seed1_draw1.json`.
#:
#: A regression fixture of real output, because this guard's two defects were both found
#: by spending Bedrock calls rather than by a test. These twenty strings are what
#: on-protocol output looks like, and a future tightening that refuses one of them is a
#: tightening that would have cost an arm.
REAL_RATIONALES = [
    ("AGE", "Sparse type with 5 gold spans; tagger only finds all 5 with T@0.5 "
            "achieving A 1.000000 and L_full 0.000000 versus UNION@0.5 A 0.833333."),
    ("CONTACT", "T@0.5 achieves A 1.000000 with L_full 0.000000 versus UNION@0.5 "
                "A 0.733333; tagger finds all 11 gold spans with zero FP."),
    ("DATE", "T@0.9 achieves A 0.977578 with L_full 0.009050 versus UNION@0.5 "
             "A 0.862205 L_full 0.004525; delta 0.063415 accommodates the 0.004525 "
             "increase."),
    ("ID", "T@0.7 achieves A 0.727273 with L_full 0.272727 versus UNION@0.5 "
           "A 0.615385 L_full 0.272727; same leak rate with higher accuracy."),
    ("LOCATION_AREA", "T@0.9 achieves A 0.862069 with L_full 0.076923 versus "
                      "UNION@0.5 A 0.135870 L_full 0.076923; same leak rate with far "
                      "higher accuracy."),
    ("LOCATION_STREET", "T@0.7 achieves A 1.000000 with L_full 0.111111 versus "
                        "UNION@0.5 A 0.562500 L_full 0.111111; same leak rate with "
                        "perfect accuracy."),
    ("NAME", "T@0.5 achieves A 0.950820 with L_full 0.025862 versus UNION@0.5 "
             "A 0.703030 L_full 0.025862; same leak rate with much higher accuracy."),
    ("ORGANISATION", "T@0.9 achieves A 0.750000 with L_full 0.545455 versus "
                     "UNION@0.5 A 0.476190 L_full 0.545455; same leak rate with "
                     "higher accuracy."),
    ("AGE", "Sparse type with 4 gold spans; UNION@0.5 matches baseline and rules "
            "capture 3 of 4."),
    ("DATE", "UNION@0.5 yields lowest L_full 0.049505 and A 0.813559; tagger found "
             "88 spans rules missed."),
    ("OTHER", "Sparse type with 1 gold span found by neither detector; UNION@0.5 is "
              "default."),
]


@pytest.mark.parametrize("phi_type, rationale", REAL_RATIONALES)
def test_real_arbiter_output_passes_the_guard(phi_type, rationale):
    """None of these may refuse. See `REAL_RATIONALES` for why they are pinned."""
    arb.check_rationale(rationale, phi_type=phi_type)


def test_an_ordinary_rationale_passes():
    """The positive control for the return path."""
    arb.check_rationale(
        "UNION keeps both sources because the tagger only cell is large and the "
        "leak rate is unchanged", phi_type="NAME")
    arb.check_rationale("sparse gold count of 6 so the baseline is kept",
                        phi_type="AGE")


@pytest.mark.parametrize("rationale, fragment", [
    ("chose UNION because Hospital Clinic appears often", "capitalised"),
    ("the patient 김환자 was missed", "non-ASCII"),
    ("kept R because Perez was found", "capitalised"),
    ("x" * 400, "one sentence"),
])
def test_a_rationale_that_could_carry_a_surface_is_refused(rationale, fragment):
    with pytest.raises(arb.ArbiterError, match=fragment) as caught:
        arb.check_rationale(rationale, phi_type="NAME")
    assert "Not quoted" in str(caught.value)


def test_a_rationale_may_only_cite_numbers_the_frame_supplied():
    """What makes "numbers only" checkable: *which* numbers is answerable, and the
    answer is the ones the agent was given."""
    allowed = arb.frame_numbers("type NAME gold 2134 sparse no")
    arb.check_rationale("gold 2134 is large", phi_type="NAME",
                        allowed_numbers=allowed)
    with pytest.raises(arb.ArbiterError, match="does not appear in the frame"):
        arb.check_rationale("the record 1234567 leaked", phi_type="NAME",
                            allowed_numbers=allowed)


def test_the_number_check_is_skipped_when_no_frame_is_supplied():
    """Recorded rather than silently defaulted — a skipped check that looks like a
    passed one is the failure this module is organised against."""
    arb.check_rationale("gold 9999 is large", phi_type="NAME")
    with pytest.raises(arb.ArbiterError, match="does not appear"):
        arb.check_rationale("gold 9999 is large", phi_type="NAME",
                            allowed_numbers=frozenset({"5"}))


def test_an_undeclared_lower_case_word_is_recorded_and_not_refused():
    """The deliberate asymmetry. DESIGN §12.3 and `check_rationale`'s own note.

    The first version of the guard refused on the word list and rejected "kept" on its
    first run. In the live loop that costs the arm rather than the sentence — one retry,
    then a fallback to the previous policy — so a guard that fires on English morphology
    damages the experiment it protects while catching nothing. A lower-case ASCII word
    cannot be a copied surface, because the Arbiter was shown nothing to copy.
    """
    undeclared = arb.check_rationale(
        "kept UNION since the tagger rediscovers most spans", phi_type="NAME")
    assert "rediscovers" in undeclared
    # and it is published, so "the guard passed" has a number beside it
    payload = json.dumps({"NAME": {"policy": "R", "theta": None,
                                   "rationale": "kept because of flibbertigibbet"}})
    record = arb.parse(payload, expected=["NAME"]).as_record()
    assert "flibbertigibbet" in record["rationale_undeclared_words"]


def test_the_rationale_guard_never_quotes_what_it_caught():
    cases = [
        ("chose UNION because Hospital Clinic appears", "Hospital", None),
        ("the patient 김환자 was missed", "김환자", None),
        ("the record 1234567 leaked", "1234567", frozenset({"5"})),
    ]
    for rationale, planted, allowed in cases:
        with pytest.raises(arb.ArbiterError) as caught:
            arb.check_rationale(rationale, phi_type="NAME",
                                allowed_numbers=allowed)
        assert planted not in str(caught.value)


def test_a_capitalised_type_or_policy_name_is_allowed_mid_sentence():
    """The capitalisation check must not reject the vocabulary the prompt asked for."""
    arb.check_rationale("kept UNION for NAME because both is large",
                        phi_type="NAME")
    arb.check_rationale("the ORGANISATION cell favours AGREE", phi_type="NAME")


def test_a_surname_that_is_an_english_common_word_is_still_caught():
    """A capitalised word that case-folds into the vocabulary is caught anyway.

    `Low` and `Best` are real surnames and both are in `RATIONALE_WORDS` in lower case,
    so a case-folded vocabulary check alone would pass them. That is why capitalisation
    is a separate check and why it runs first.
    """
    for surname in ("Low", "Best", "Sparse"):
        with pytest.raises(arb.ArbiterError, match="capitalised"):
            arb.check_rationale(f"the span {surname} was missed", phi_type="NAME")
        # the same word in lower case is ordinary vocabulary and passes
        arb.check_rationale(f"the {surname.lower()} cell is large", phi_type="NAME")
        # and capitalised at a sentence opening it passes, because it is declared —
        # which is exactly what distinguishes it from a surname (test above).
        arb.check_rationale(f"kept UNION. {surname} values decide it", phi_type="NAME")


# ─── the response protocol ──────────────────────────────────────────────────

def _response(**types) -> str:
    return json.dumps({
        name: {"policy": policy, "theta": theta,
               "rationale": "kept because the leak rate is unchanged"}
        for name, (policy, theta) in types.items()})


def test_a_well_formed_response_parses_into_choices():
    payload = _response(NAME=("UNION", 0.7), DATE=("R", None))
    proposal = arb.parse(payload, expected=["NAME", "DATE"])
    assert str(proposal.choices["NAME"]) == "UNION@0.7"
    assert str(proposal.choices["DATE"]) == "R"
    assert set(proposal.rationales) == {"NAME", "DATE"}


def test_a_missing_type_is_refused_rather_than_defaulted():
    with pytest.raises(arb.ArbiterError, match="Missing"):
        arb.parse(_response(NAME=("R", None)), expected=["NAME", "DATE"])


def test_a_type_that_was_not_shown_is_refused():
    """On this protocol it means the agent answered about something it was not shown."""
    with pytest.raises(arb.ArbiterError, match="Not shown"):
        arb.parse(_response(NAME=("R", None), AGE=("R", None)),
                  expected=["NAME"])


def test_an_unparseable_response_is_a_format_failure_and_quotes_nothing():
    with pytest.raises(arb.ArbiterError, match="not JSON"):
        arb.parse("here is the answer: {NAME: R}", expected=["NAME"])


def test_a_fenced_response_is_a_format_failure_here_too():
    """The envelope policy (DESIGN §6.8) strips one fence for the authoring roles. This
    parser is handed whatever the envelope reader accepted, so a fence reaching it is a
    failure rather than something to strip a second time."""
    with pytest.raises(arb.ArbiterError, match="not JSON"):
        arb.parse('```json\n{"NAME": {"policy": "R"}}\n```', expected=["NAME"])


def test_an_extra_field_in_an_entry_is_refused():
    payload = json.dumps({"NAME": {"policy": "R", "theta": None,
                                   "rationale": "kept", "confidence": 0.9}})
    with pytest.raises(arb.ArbiterError, match="unknown field"):
        arb.parse(payload, expected=["NAME"])


@pytest.mark.parametrize("field", ["policy", "theta", "rationale"])
def test_every_one_of_the_three_fields_is_required(field):
    entry = {"policy": "UNION", "theta": 0.5, "rationale": "kept"}
    del entry[field]
    with pytest.raises(arb.ArbiterError, match="has no"):
        arb.parse(json.dumps({"NAME": entry}), expected=["NAME"])


def test_a_theta_on_policy_r_is_refused_through_the_choice_validator():
    payload = json.dumps({"NAME": {"policy": "R", "theta": 0.5,
                                   "rationale": "kept"}})
    with pytest.raises(arb.ArbiterError, match="ignores the tagger"):
        arb.parse(payload, expected=["NAME"])


def test_a_theta_off_the_grid_is_refused_through_the_choice_validator():
    payload = json.dumps({"NAME": {"policy": "T", "theta": 0.65,
                                   "rationale": "kept"}})
    with pytest.raises(arb.ArbiterError, match="pre-registered grid"):
        arb.parse(payload, expected=["NAME"])


def test_an_invented_policy_is_refused():
    payload = json.dumps({"NAME": {"policy": "INTERSECT", "theta": 0.5,
                                   "rationale": "kept"}})
    with pytest.raises(arb.ArbiterError, match="not a merge policy"):
        arb.parse(payload, expected=["NAME"])


def test_the_no_gold_types_re_enter_after_the_response_and_not_as_a_default():
    """DESIGN §12.2: the same value, with a record of the decision instead of without."""
    table = _table()
    proposal = arb.parse(_response(NAME=("R", None), DATE=("T", 0.9)),
                         expected=["NAME", "DATE"])
    filled = arb.fill_absent(proposal.choices, table)
    assert set(filled) == set(canonical_types())
    assert str(filled["NAME"]) == "R"
    for phi_type in canonical_types():
        if not table[phi_type].n_gold:
            assert filled[phi_type] == pol.rt_baseline(), phi_type


def test_the_proposal_record_carries_rationales_and_no_text():
    proposal = arb.parse(_response(NAME=("UNION", 0.5)), expected=["NAME"])
    record = proposal.as_record()
    assert record["policies"]["NAME"] == {"policy": "UNION", "theta": 0.5}
    assert record["rationales"]["NAME"]
