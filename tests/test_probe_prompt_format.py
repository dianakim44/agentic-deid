"""The probe's classifiers, and the property that none of them writes a surface form.

**Why a tool gets a test file at all.** `tools/probe_prompt_format.py` gates nothing — nothing in
`src/` imports it and no metric reads its output — so the usual argument for testing a tool does
not apply. Two things here need it anyway:

1. **It appends to a committed note.** Its output is the record a launch decision is made on, and
   the record is a file in a public repository. The rule that no exception message, log or note
   carries a corpus surface form (CLAUDE.md) applies to it exactly as to `src/`, and CLAUDE.md
   also says why the rule needs a test rather than a convention: without one, the next reader adds
   the rejected term back "for debugging". `test_lexicon_verdict_records_no_surface_form` and
   `test_rule_author_failure_records_no_surface_form` are that test, in the shape
   `tests/test_meddocan_loader.py::test_offset_mismatch_message_quotes_no_surface` established.
2. **A classifier can be silently wrong.** `_refusal_class` and `_error_class` turn a validator's
   refusal into one word, and a mapping that quietly answered `schema` for everything would
   produce a plausible table and a false conclusion — which is the failure the `terms` bug in the
   2026-09-17 block already demonstrated: a believable number is not a checked one.

**Deliberately not in `tests/mutations/run.py`'s `TEST_FILES`.** A new member changes the
denominator of every recorded kill count and forces a full run (CLAUDE.md's second trigger,
~2.3 h). Nothing here tests `src/`, so it would buy a re-measurement of all 211 mutations to cover a
file no mutation targets. `tests/test_mutation_harness.py` and `tests/test_run_loop_cli.py` are out
for the same reason and are named in CLAUDE.md as being out.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from src.corpora.base import lexicon_bases, lexicon_names
from src.porting import artefacts

ROOT = Path(__file__).resolve().parents[1]


def _probe():
    """The tool, imported by path. It lives in `tools/` and is not a package."""
    spec = importlib.util.spec_from_file_location(
        "probe_prompt_format", ROOT / "tools" / "probe_prompt_format.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _probe()

#: A term shaped like the thing that must never be written down: not a real name, but the only
#: string in each fixture that is neither vocabulary nor structure, so a `in` test over the
#: serialised row is a real test of the property rather than of the fixture.
SURFACE = "Zzyrbaneth-Kolvassy"

#: The name and basis every valid fixture uses, read from the config rather than spelled, so that
#: a renamed vocabulary value fails these tests instead of making them vacuous.
NAME = sorted(lexicon_names())[0]
BASIS = sorted(lexicon_bases())[0]


def _lexicon(entry: dict, *, name: str = NAME, lang: str = "es") -> dict:
    return {"lexicons": {lang: {name: entry}}, "unresolved": []}


# ─── the three classes the 2026-09-18 question asks for ──────────────────────


@pytest.mark.parametrize("reason,expected", [
    ("malformed", "schema"),
    ("unknown_field", "schema"),
    ("empty_lexicon", "empty"),
    ("entry_contains_newline", "entry_hygiene"),
    ("entry_contains_comment_mark", "entry_hygiene"),
    ("entry_too_short", "entry_hygiene"),
    ("duplicate_entry", "entry_hygiene"),
    ("entry_is_vocabulary_term", "vocabulary"),
])
def test_refusal_class_of_the_unambiguous_reasons(reason, expected):
    """The eight reasons that need no lookup. `missing_field` and `undeclared_value` are below."""
    obj = _lexicon({"basis": BASIS, "entries": [SURFACE]})
    assert probe._refusal_class(obj, {"file": f"es/{NAME}", "reason": reason}) == expected


def test_missing_basis_is_the_discriminator_and_missing_entries_is_the_schema():
    """`missing_field` covers both, and §6.7.5 only cares about one of them.

    This is the split the whole `_refusal_class` lookup exists for. A file with a `basis` and no
    `entries` is a broken shape; a file with `entries` and no `basis` is a file the arm could
    write and then could not cross against transfer, which costs the arm its falsifiable claim.
    Collapsing them into `schema` would report the second as a formatting problem.
    """
    no_basis = _lexicon({"entries": [SURFACE]})
    no_entries = _lexicon({"basis": BASIS})
    refusal = {"file": f"es/{NAME}", "reason": "missing_field"}
    assert probe._refusal_class(no_basis, refusal) == "discriminator"
    assert probe._refusal_class(no_entries, refusal) == "schema"


def test_undeclared_name_and_undeclared_basis_are_both_vocabulary():
    """Both are `config/naming.yaml` values, and the tally may not double-count the basis one.

    An undeclared basis destroys §6.7.5's cross-check as surely as an absent one, so the
    temptation is to call it `discriminator` too — and then the class tally would stop summing to
    `refused`. It is `vocabulary` here and counted a second time by `basis_undeclared`, which is
    over the offer and competes with nothing.
    """
    bad_basis = _lexicon({"basis": "not_a_declared_basis", "entries": [SURFACE]})
    assert probe._refusal_class(bad_basis, {"file": f"es/{NAME}",
                                            "reason": "undeclared_value"}) == "vocabulary"
    bad_name = _lexicon({"basis": BASIS, "entries": [SURFACE]}, name="not_a_declared_name")
    assert probe._refusal_class(bad_name, {"file": "es/not_a_declared_name",
                                           "reason": "undeclared_value"}) == "vocabulary"


def test_every_refusal_reason_the_validator_can_produce_has_a_class():
    """A closed set on both sides. A reason `validate_lexicon` gains and this file does not know
    would otherwise land in `schema` by the fallthrough and be invisible."""
    reasons = {"unknown_field", "malformed", "missing_field", "undeclared_value", "empty_lexicon",
               "entry_contains_newline", "entry_contains_comment_mark", "entry_too_short",
               "entry_is_vocabulary_term", "duplicate_entry"}
    obj = _lexicon({"basis": BASIS, "entries": [SURFACE]})
    for reason in reasons:
        assert probe._refusal_class(obj, {"file": f"es/{NAME}", "reason": reason}) \
            in probe.REFUSAL_CLASSES
    # …and the reverse direction: the source of that set is the validator, so if it grows a
    # reason this assertion is what notices. Read off the module rather than trusted, and sliced
    # to `validate_lexicon` plus `_entry_refusal` — `validate_profile` and `validate_mapping` have
    # refusal vocabularies of their own (`uncited_field`, `unmapped_source_type`) which are not
    # this classifier's business.
    source = (ROOT / "src" / "porting" / "artefacts.py").read_text(encoding="utf-8")
    start = source.index("def validate_lexicon(")
    region = source[start:source.index("def lexicon_manifest(", start)].splitlines()
    found = {line.split('"reason": "')[1].split('"')[0]
             for line in region if '"reason": "' in line}
    found |= {line.split('return "')[1].split('"')[0]
              for line in region if line.strip().startswith('return "')}
    assert found <= reasons, f"validate_lexicon gained refusal reasons: {sorted(found - reasons)}"
    assert found == reasons, f"these are no longer produced: {sorted(reasons - found)}"


def test_class_tally_sums_to_the_refusal_count():
    """The property that makes the two tables in the note readable side by side."""
    obj = _lexicon({"basis": BASIS,
                    "entries": [SURFACE, SURFACE, "a", f"{SURFACE}#x", sorted(lexicon_names())[0]]})
    row = probe._verdict_lexicon(json.dumps(obj), ["es"])
    assert sum(row["by_class"].values()) == row["refused"]
    assert sum(row["by_refusal"].values()) == row["refused"]


# ─── the offer, which is what a survivor count cannot say ────────────────────


def test_offered_counts_files_the_validator_dropped():
    """Nine offered and one kept is `validated`, and the note has to be able to say so."""
    obj = {"lexicons": {"es": {
        NAME: {"basis": BASIS, "entries": [SURFACE]},
        "not_a_declared_name": {"basis": BASIS, "entries": [SURFACE]},
        sorted(lexicon_names())[1]: {"entries": [SURFACE]},
    }}}
    offered = probe._lexicon_offered(obj)
    assert offered["offered_files"] == 3
    assert offered["offered_declared_names"] == 2
    assert offered["basis_absent"] == 1
    assert offered["basis_undeclared"] == 0
    assert offered["offered_bases"] == {BASIS: 2}


def test_offered_bases_records_declared_values_and_counts_the_rest():
    """A basis outside the axis is counted, never written. `offered_bases` is naming.yaml only."""
    obj = _lexicon({"basis": SURFACE, "entries": ["x"]})
    offered = probe._lexicon_offered(obj)
    assert offered["basis_undeclared"] == 1
    assert offered["offered_bases"] == {}
    assert SURFACE not in json.dumps(offered)


def test_offered_survives_a_response_shaped_wrong():
    """`_lexicon_offered` runs before validation, so it meets shapes the validator rejects."""
    for obj in ({}, {"lexicons": None}, {"lexicons": {"es": None}},
                {"lexicons": {"es": {NAME: "a string"}}}, {"lexicons": []}):
        offered = probe._lexicon_offered(obj)
        assert set(offered) == {"offered_files", "offered_declared_names", "basis_absent",
                                "basis_undeclared", "offered_bases"}


def test_terms_counts_entries_and_not_the_two_keys_of_the_entry():
    """The 2026-09-17 bug, pinned. It read `len(entry)` and every file reported 2.

    The number that regression produces is *plausible*, which is why it survived a reading: three
    files and six terms is what a cautious first round would look like. So the test asserts the
    exact count rather than "more than the number of files".
    """
    obj = _lexicon({"basis": BASIS, "entries": ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]})
    row = probe._verdict_lexicon(json.dumps(obj), ["es"])
    assert row["files"] == 1
    assert row["terms"] == 5
    assert row["terms_by_file"] == {f"es/{NAME}": 5}


# ─── no surface form, in any field, on any path ──────────────────────────────


def test_lexicon_verdict_records_no_surface_form():
    """Every entry is a candidate surface form and none of them may reach the row.

    Both paths: the accepted entries (which become a count) and the refused ones (which become a
    reason). `terms_by_file`'s keys are `{lang}/{name}` and both halves are naming.yaml values, so
    the key is safe and the assertion below is about everything else.
    """
    obj = _lexicon({"basis": BASIS, "entries": [SURFACE, f"{SURFACE} 2", SURFACE, "q"]})
    row = probe._verdict_lexicon(json.dumps(obj), ["es"])
    assert row["outcome"] == "validated"
    assert SURFACE not in json.dumps(row, ensure_ascii=False)
    assert "Zzyrbaneth" not in json.dumps(row, ensure_ascii=False)


def test_lexicon_verdict_records_no_surface_form_when_it_refuses():
    """The failure path is the one that historically leaks: a message goes into `error_type`."""
    for text in (f'{{"lexicons": {{"xx": {{"{NAME}": {SURFACE!r}}}}}}}',
                 f'"{SURFACE}"', f'{{"{SURFACE}": 1}}', f'not json at all {SURFACE}'):
        row = probe._verdict_lexicon(text, ["es"])
        assert row["outcome"] == "format_failure"
        assert SURFACE not in json.dumps(row, ensure_ascii=False)


def test_rule_author_failure_records_no_surface_form_and_names_the_check():
    """A `RuleError` message interpolates the agent's `rule_id`; the row records neither.

    `rule_id` is where the release screener's five acknowledged violations live — place names used
    as identifiers — so a recorded message is the path by which corpus-shaped text reaches this
    note. What the row gets instead is a class and a raise site, and the site has to point into
    `src/`: a site in `tools/` would mean the traceback walk stopped at the probe's own `try`.
    """
    rules = {"lang": "es", "version": 1,
             "rules": [{"rule_id": f"gazetteer-{SURFACE}", "layer": "not_in_the_layer_axis",
                        "phi_type": "LOCATION", "terms": [SURFACE]}]}
    row = probe._verdict_rule_author(yaml.safe_dump(rules, allow_unicode=True))
    assert row["outcome"] == "format_failure"
    assert row["error_type"] == "RuleError"
    assert row["error_class"] != "unclassified"
    assert row["error_site"] and row["error_site"].startswith("src/")
    assert SURFACE not in json.dumps(row, ensure_ascii=False)


def test_rule_author_records_the_arm_load_beside_the_bare_one():
    """Two loads, and the difference between them is a harness artefact rather than a result.

    A rule that names a lexicon fails the bare load with `lexicon_collection_not_named`, because
    the probe passes no lexicon directory — the arm does. So the row carries both, and a reader
    who saw only `outcome` would count a failure the arm would not have.
    """
    name = sorted(lexicon_names())[0]
    rules = {"lang": "es", "version": 1,
             "rules": [{"rule_id": "gazetteer-hospital-name", "layer": "gazetteer",
                        "phi_type": "ORGANISATION", "lexicon": f"es/{name}"}]}
    row = probe._verdict_rule_author(yaml.safe_dump(rules))
    assert row["outcome"] == "format_failure"
    assert row["error_class"] == "lexicon_collection_not_named"
    assert row["lexicon_rules"] == 1
    assert row["lexicon_refs"] == [f"es/{name}"]
    assert row["arm_outcome"] == "loaded", row.get("arm_error_class")
    assert row["arm_rules"] == 1


def test_lexicon_refs_counts_an_undeclared_reference_without_recording_it():
    """A lexicon name outside the axis is a count. The name itself is model output."""
    rules = {"lang": "es", "version": 1,
             "rules": [{"rule_id": "gazetteer-hospital-name", "layer": "gazetteer",
                        "phi_type": "ORGANISATION", "lexicon": f"es/{SURFACE}"}]}
    refs = probe._lexicon_refs(yaml.safe_dump(rules, allow_unicode=True))
    assert refs["lexicon_refs_undeclared"] == 1
    assert refs["lexicon_refs"] == []
    assert SURFACE not in json.dumps(refs, ensure_ascii=False)


# ─── the error classes, matched on what the code wrote ───────────────────────


def test_no_error_class_fragment_matches_an_interpolated_value():
    """Each fragment must be literal text in `src/`, or it could be matched by model output.

    The check is that the fragment appears in a source file as characters — which it cannot if it
    spans an `{...}` interpolation, since then the source contains the braces and not the value.
    This is the property that makes `error_class` safe to write into a committed note.
    """
    sources = "\n".join((ROOT / p).read_text(encoding="utf-8")
                        for p in ("src/rules.py", "src/porting/artefacts.py"))
    for fragment, name in probe.ERROR_CLASSES:
        assert fragment in sources, f"{name}: fragment is not literal text in src/"


def test_error_classes_are_unique_and_ordered_where_order_matters():
    """Two pairs share a fragment prefix and the more specific one has to be tested first."""
    names = [name for _, name in probe.ERROR_CLASSES]
    assert len(names) == len(set(names))
    order = {name: i for i, name in enumerate(names)}
    assert order["file_lang_undeclared"] < order["lexicon_lang_undeclared"]
    assert order["not_json"] < order["not_a_json_object"]


def test_parse_object_refusals_classify():
    """Both of `parse_object`'s raises, through the table, on the fragments they actually carry."""
    with pytest.raises(artefacts.ArtefactError) as unparseable:
        artefacts.parse_object("{not json", what="lexicon")
    with pytest.raises(artefacts.ArtefactError) as not_object:
        artefacts.parse_object("[1, 2]", what="lexicon")
    assert probe._error_class(unparseable.value) == "not_json"
    assert probe._error_class(not_object.value) == "not_a_json_object"


def test_error_class_falls_through_to_unclassified_rather_than_guessing():
    assert probe._error_class(ValueError("a message no fragment appears in")) == "unclassified"


def test_error_site_is_none_for_an_exception_with_no_traceback():
    assert probe._error_site(ValueError("never raised")) is None


# ─── the strip, and the interval the decision turns on ───────────────────────


def test_every_probe_declares_a_parses_predicate_agreeing_with_its_loader():
    """`parses` and `shape` are the same format twice, so they may not disagree.

    Checked as behaviour on one object rather than by comparing the two callables, because the
    failure this guards against is a copy-paste: `parses_json` beside `yaml.safe_load` would make
    the strip accept a payload the diagnostic cannot read, and the rate would be over one format
    while the shape table was over another.
    """
    for name, spec in probe.PROBES.items():
        assert callable(spec["parses"]), name
        obj = '{"a": {"b": [1, 2]}}'      # both a JSON object and a YAML mapping
        assert spec["parses"](obj) is True, name
        assert isinstance(spec["shape"](obj), dict), name
        assert spec["parses"]("a bare sentence") is False, name


def test_a_fenced_response_reaches_the_verdict_through_the_strip():
    """The 2026-09-18 change, end to end on the verdict rather than on `unwrap` alone.

    `unwrap` has its own tests; what this asserts is that this file's verdict is reached with the
    payload, so a fenced draw is `validated` here exactly as it is in an arm.
    """
    from src.llm.envelope import unwrap

    obj = _lexicon({"basis": BASIS, "entries": ["Alpha", "Beta"]})
    body = json.dumps(obj)
    env = unwrap(f"```json\n{body}\n```", parses=probe.PROBES["lexicon_builder"]["parses"])
    assert env.kind == "fenced_once"
    assert probe._verdict_lexicon(env.payload, ["es"])["outcome"] == "validated"
    # …and the shape §6.8 leaves as a failure still fails, at the verdict too.
    refused = unwrap(f"Here you go:\n```json\n{body}\n```",
                     parses=probe.PROBES["lexicon_builder"]["parses"])
    assert refused.kind == "refused"
    assert probe._verdict_lexicon(refused.payload, ["es"])["outcome"] == "format_failure"


def test_interval_reproduces_the_one_the_larger_n_was_argued_from():
    """2 of 20 is 1.2%–31.7%, which is the number `RETRY_POLICY` clause 4 cites.

    The pin is the whole reason the interval is Clopper–Pearson: Wilson gives 2.8%–30.1% on the
    same data, and a table reporting that beside a clause arguing from 1.2%–31.7% would be two
    yardsticks in one decision.
    """
    assert probe._interval(2, 20) == "10.0% [1.2–31.7]"


def test_interval_narrows_with_n_and_stays_inside_the_unit_interval():
    """The point of 60 draws: the same rate, a usable interval."""
    def bounds(text):
        lo, hi = text.split("[")[1].rstrip("]").split("–")
        return float(lo), float(hi)

    lo20, hi20 = bounds(probe._interval(2, 20))
    lo60, hi60 = bounds(probe._interval(6, 60))
    assert hi60 - lo60 < hi20 - lo20
    assert bounds(probe._interval(0, 60)) == (0.0, pytest.approx(6.0, abs=0.1))
    assert bounds(probe._interval(60, 60))[1] == 100.0
    assert probe._interval(0, 0) == "—"


def test_interval_is_the_exact_one_at_the_two_hypotheses_the_decision_turns_on():
    """The binomial claims in `RETRY_POLICY` clause 4, checked against the same tail sums.

    Clause 4 says that at p = 3% the chance of 6 or more failures in 60 is 0.9%, and at p = 10%
    the chance of 2 or fewer is 5.3%. Those two numbers are why N is 60, so they are asserted here
    rather than trusted — the clause first said 1.6% and 6.2%, and this assertion is what found it.

    The third line is the limit the clause also states: 2 of 60 does not exclude 10% by the
    interval, only by the one-sided reading. Pinned so that a later rewrite cannot quietly drop
    the caveat and keep the sample size.
    """
    assert probe._tail_at_least(6, 60, 0.03) == pytest.approx(0.009, abs=0.001)
    assert probe._tail_at_most(2, 60, 0.10) == pytest.approx(0.053, abs=0.001)
    assert probe._interval(2, 60) == "3.3% [0.4–11.5]"


def test_pmf_sums_to_one_at_the_endpoints_and_between():
    for p in (0.0, 0.03, 0.5, 1.0):
        assert sum(probe._pmf(i, 60, p) for i in range(61)) == pytest.approx(1.0)


def test_run_draw_records_both_sides_of_the_envelope_and_judges_the_payload(monkeypatch):
    """The wiring, end to end, with the transport stubbed. No call is made and nothing is appended.

    Worth a test rather than a first live draw: this path was rerouted on 2026-09-18 and a mistake
    in it costs a paid run of 40 calls before it shows. What is asserted is the part a unit test of
    `unwrap` cannot reach — that the row carries `envelope` and both hashes, that `parsed_sha256`
    differs from `response_sha256` on a fenced draw, and that the *verdict* saw the payload.
    """
    from types import SimpleNamespace

    from src.llm import bedrock

    body = json.dumps(_lexicon({"basis": BASIS, "entries": ["Alpha", "Beta", "Gamma"]}))
    monkeypatch.setattr(bedrock, "invoke", lambda *a, **k: SimpleNamespace(
        text=f"```json\n{body}\n```", prompt_tokens=9120, completion_tokens=3694,
        stop_reason="end_turn", model_id_reported="claude-opus-4-5-20251101"))

    row = probe.run_draw("lexicon_builder", 1, model_id="stub", region=None, max_tokens=8192)
    assert row["envelope"] == "fenced_once"
    assert row["fence_lines"] == 2
    assert row["parsed_chars"] == len(body) < row["response_chars"]
    assert row["parsed_sha256"] != row["response_sha256"]
    assert row["outcome"] == "validated" and row["terms"] == 3
    assert set(row) >= set(probe.RECORD_FIELDS if hasattr(probe, "RECORD_FIELDS") else
                           ("response_chars", "response_sha256", "envelope", "fence_lines",
                            "parsed_chars", "parsed_sha256"))
    # The note is rendered from rows, so the same call proves the tables survive one.
    block = probe.render([row], model_id="stub", date="2026-09-18", reason="a test")
    assert "fenced_once" in block and SURFACE not in block


def test_run_draw_refuses_a_preamble_and_still_records_the_received_bytes(monkeypatch):
    """§6.8's failure list, here: a preamble is not strippable, and then `parsed_*` is null."""
    from types import SimpleNamespace

    from src.llm import bedrock

    body = json.dumps(_lexicon({"basis": BASIS, "entries": ["Alpha"]}))
    monkeypatch.setattr(bedrock, "invoke", lambda *a, **k: SimpleNamespace(
        text=f"Sure — here it is:\n```json\n{body}\n```", prompt_tokens=1, completion_tokens=1,
        stop_reason="end_turn", model_id_reported="claude-opus-4-5-20251101"))

    row = probe.run_draw("lexicon_builder", 1, model_id="stub", region=None, max_tokens=8192)
    assert row["envelope"] == "refused"
    assert row["parsed_chars"] is None and row["parsed_sha256"] is None
    assert row["outcome"] == "format_failure"
    assert row["error_class"] == "not_json"
    assert row["error_site"].startswith("src/porting/artefacts.py:")


def test_the_draw_cap_is_the_one_retry_policy_states():
    """A cap that only the docstring holds is a cap the next caller raises — so both are read."""
    assert probe.MAX_DRAWS == 60
    assert "60" in probe.RETRY_POLICY
