"""Tests for src/rules.py — the rule loader and matcher, the `R` arm's detector.

Three things are load-bearing here and each fails quietly if it breaks.

**Provenance.** Every span carries the layer of the rule that matched, copied and not
derived (DESIGN §3). A derivation from the rule's name or the pattern's shape would put
plausible values on every span and DESIGN §7's per-layer comparison would be measuring
the derivation.

**Refusal over silence.** A rule that loads and matches nothing is indistinguishable
from a phenomenon that does not occur — and that is the exact conclusion §7 reports. So
a missing lexicon, an unimplemented checksum, a misdeclared layer and a duplicate id all
raise at load, not at first use.

**The two regex-free layers.** `gazetteer` and the common `context_cue` case are
authorable without regex, deliberately: an author who can only express themselves in
regex writes regex-shaped rules, and then a layer looks weak for a reason that has
nothing to do with the phenomenon.

No corpus text in this file. The patterns and terms here are invented shapes
(`Zzyzxville`, `ZZZNEVERMATCHZZZ`) and the texts are constructed, so nothing tested here
is a surface form from anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.corpora.base import axis, family_of                          # noqa: E402
from src.rules import (                                               # noqa: E402
    CHECKSUMS, FLAGS, THEN_SHORTHAND, RuleError, RuleSet, load_for_corpus,
    load_rules, rule_layers,
)


def write(tmp_path: Path, rules: list[dict], *, lang: str = "es",
          version: int = 1, **extra) -> Path:
    path = tmp_path / f"{lang}.yaml"
    body = {"version": version, "lang": lang, "rules": rules, **extra}
    path.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    return path


def one(tmp_path: Path, rule: dict, **kw) -> RuleSet:
    lang = kw.get("lang", "es")
    return load_rules(lang, path=write(tmp_path, [rule], **kw))


# ─── the layers come from the config ─────────────────────────────────────────

def test_the_rule_layers_are_the_rules_family_from_naming_yaml():
    """Derived from `layer_families`, not listed here — the same rule as everywhere.

    A hardcoded triple in `src/rules.py` beside a config that names the family is the
    drift naming.yaml exists to prevent, moved one level up (base.py's own comment on
    `layer_families` says exactly this).
    """
    assert rule_layers() == frozenset(l for l in axis("layer")
                                      if family_of(l) == "rules")
    assert rule_layers() == {"gazetteer", "context_cue", "regex_checksum"}


def test_a_tagger_layer_is_refused_in_a_rule_file(tmp_path):
    tagger = [l for l in axis("layer") if family_of(l) != "rules"]
    assert tagger, "naming.yaml has no non-rules layer to test against"
    with pytest.raises(RuleError, match="layer must be one of"):
        one(tmp_path, {"rule_id": "x", "layer": tagger[0], "phi_type": "NAME",
                       "pattern": "Zzyzx"})


def test_an_invented_layer_is_refused(tmp_path):
    with pytest.raises(RuleError, match="not one"):
        one(tmp_path, {"rule_id": "x", "layer": "heuristic", "phi_type": "NAME",
                       "pattern": "Zzyzx"})


# ─── provenance is copied from the rule, never derived ───────────────────────

def test_every_span_carries_the_layer_of_the_rule_that_matched(tmp_path):
    rs = load_rules("es", path=write(tmp_path, [
        {"rule_id": "g", "layer": "gazetteer", "phi_type": "ORGANISATION",
         "terms": ["Zzyzxville Clinic"]},
        {"rule_id": "p", "layer": "regex_checksum", "phi_type": "ID",
         "pattern": r"REF-\d{4}"},
    ]))
    spans = rs.detect("Zzyzxville Clinic, REF-1234.")
    by_id = {s.rule_id: s for s in spans}
    assert by_id["es:g"].layer == "gazetteer"
    assert by_id["es:p"].layer == "regex_checksum"


def test_a_rules_layer_span_is_in_the_rules_family(tmp_path):
    """The property the complementarity breakdown reads (DESIGN §5)."""
    rs = one(tmp_path, {"rule_id": "g", "layer": "gazetteer",
                        "phi_type": "ORGANISATION", "terms": ["Zzyzxville"]})
    for span in rs.detect("At Zzyzxville."):
        assert family_of(span.layer) == "rules"


def test_the_layer_does_not_follow_the_rule_id(tmp_path):
    """A rule named after one layer but declaring another keeps its declaration.

    The rule id is a mechanism vocabulary, so a name like `gazetteer_ish` is plausible;
    if the layer followed the name the declaration would be decoration.
    """
    rs = one(tmp_path, {"rule_id": "gazetteer_like", "layer": "regex_checksum",
                        "phi_type": "ID", "pattern": r"REF-\d+"})
    assert rs.detect("REF-9")[0].layer == "regex_checksum"


def test_the_rule_id_is_prefixed_with_the_files_language(tmp_path):
    path = write(tmp_path, [{"rule_id": "inst", "layer": "gazetteer",
                             "phi_type": "ORGANISATION", "terms": ["Zzyzxville"]}],
                 lang="cat")
    rs = load_rules("cat", path=path)
    assert rs.rules[0].rule_id == "cat:inst"
    assert rs.detect("Zzyzxville")[0].rule_id == "cat:inst"


def test_a_prefix_written_in_the_file_is_refused(tmp_path):
    """Writing it produces `es:es:inst`, which no by_rule block would attribute."""
    with pytest.raises(RuleError, match="carries a prefix"):
        one(tmp_path, {"rule_id": "es:inst", "layer": "gazetteer",
                       "phi_type": "ORGANISATION", "terms": ["Zzyzxville"]})


def test_the_detector_is_recorded_on_every_span(tmp_path):
    rs = one(tmp_path, {"rule_id": "p", "layer": "regex_checksum",
                        "phi_type": "ID", "pattern": r"REF-\d+"})
    assert rs.detect("REF-1", detector="RT")[0].detector == "RT"


# ─── gazetteer: authorable with no regex at all ──────────────────────────────

def test_a_term_list_needs_no_regex(tmp_path):
    rs = one(tmp_path, {"rule_id": "inst", "layer": "gazetteer",
                        "phi_type": "ORGANISATION",
                        "terms": ["Zzyzxville Clinic", "Qqqq Centre"]})
    text = "seen at Zzyzxville Clinic and at Qqqq Centre today"
    assert len(rs.detect(text)) == 2


def test_regex_metacharacters_in_a_term_are_literal(tmp_path):
    """The point of the layer: a term is a string, not a pattern.

    `C.S. (Norte)` is a perfectly ordinary institution name and a broken regex. If terms
    were interpolated raw, the rule would fail to compile — or worse, compile and match
    something else.
    """
    rs = one(tmp_path, {"rule_id": "inst", "layer": "gazetteer",
                        "phi_type": "ORGANISATION", "terms": ["C.S. (Zzyzx)"]})
    assert len(rs.detect("at C.S. (Zzyzx) yesterday")) == 1
    assert rs.detect("at CxSy zZzyzx. yesterday") == []


def test_a_longer_term_wins_over_a_shorter_prefix(tmp_path):
    """`regex` alternation is first-match, not longest-match.

    With the shorter term first, the longer one is unreachable and the rule still fires —
    a span that is silently too short, which loses `fully_covered` while passing
    `relaxed`. The engine sorts by length so the author needs no ordering discipline.
    """
    rs = one(tmp_path, {"rule_id": "inst", "layer": "gazetteer",
                        "phi_type": "ORGANISATION",
                        "terms": ["Zzyzx", "Zzyzx General Clinic"]})
    spans = rs.detect("the Zzyzx General Clinic")
    assert [(s.start, s.end) for s in spans] == [(4, 24)]


def test_terms_are_case_folded_by_default(tmp_path):
    """naming.yaml defines the layer as membership *under case folding*."""
    rs = one(tmp_path, {"rule_id": "inst", "layer": "gazetteer",
                        "phi_type": "ORGANISATION", "terms": ["Zzyzxville"]})
    assert len(rs.detect("ZZYZXVILLE and zzyzxville")) == 2


def test_case_sensitive_opts_out(tmp_path):
    rs = one(tmp_path, {"rule_id": "inst", "layer": "gazetteer",
                        "phi_type": "ORGANISATION", "terms": ["Zzyzxville"],
                        "case_sensitive": True})
    assert len(rs.detect("ZZYZXVILLE and Zzyzxville")) == 1


def test_a_term_ending_in_punctuation_can_still_match(tmp_path):
    r"""The defect this file caught on its first run, kept as a test.

    A `\b` after `)` requires a word character on the inside of the boundary, so a term
    ending in punctuation could never match — the rule loaded, compiled, fired nowhere,
    and read as an institution name that does not occur in the corpus. "Matched nothing"
    is also what DESIGN §7 reports as a negative result, which is why this one had to
    become an assertion rather than a fix.
    """
    for term in ["C.S. (Zzyzx)", "Zzyzx.", "(Zzyzx)", "Zzyzx-", "«Zzyzx»"]:
        rs = one(tmp_path, {"rule_id": "inst", "layer": "gazetteer",
                            "phi_type": "ORGANISATION", "terms": [term]})
        assert rs.detect(f"seen at {term} today"), f"{term!r} matched nothing"


def test_a_term_matches_a_word_and_not_a_substring(tmp_path):
    rs = one(tmp_path, {"rule_id": "inst", "layer": "gazetteer",
                        "phi_type": "ORGANISATION", "terms": ["Zzyzx"]})
    assert rs.detect("unZzyzxable") == []


def test_an_empty_term_list_is_refused(tmp_path):
    """An empty list is one matcher form present and zero terms — silently no matches."""
    with pytest.raises(RuleError, match="exactly one matcher form"):
        one(tmp_path, {"rule_id": "inst", "layer": "gazetteer",
                       "phi_type": "ORGANISATION", "terms": []})


# ─── the lexicon file ────────────────────────────────────────────────────────

def test_a_lexicon_is_one_term_per_line_with_hash_comments(tmp_path, monkeypatch):
    lex = tmp_path / "lexicons" / "es"
    lex.mkdir(parents=True)
    (lex / "insts.txt").write_text("# a comment\nZzyzxville\n\n  Qqqq Centre  \n",
                                   encoding="utf-8")
    monkeypatch.setattr("src.rules.ROOT", tmp_path)
    rs = one(tmp_path, {"rule_id": "inst", "layer": "gazetteer",
                        "phi_type": "ORGANISATION", "lexicon": "es/insts"})
    assert len(rs.detect("Zzyzxville, Qqqq Centre, # a comment")) == 2


def test_a_missing_lexicon_is_refused_rather_than_matching_nothing(tmp_path,
                                                                  monkeypatch):
    monkeypatch.setattr("src.rules.ROOT", tmp_path)
    with pytest.raises(RuleError, match="no lexicon at"):
        one(tmp_path, {"rule_id": "inst", "layer": "gazetteer",
                       "phi_type": "ORGANISATION", "lexicon": "es/absent"})


def test_an_empty_lexicon_is_refused(tmp_path, monkeypatch):
    lex = tmp_path / "lexicons" / "es"
    lex.mkdir(parents=True)
    (lex / "empty.txt").write_text("# only comments\n", encoding="utf-8")
    monkeypatch.setattr("src.rules.ROOT", tmp_path)
    with pytest.raises(RuleError, match="is empty"):
        one(tmp_path, {"rule_id": "inst", "layer": "gazetteer",
                       "phi_type": "ORGANISATION", "lexicon": "es/empty"})


def test_a_lexicon_name_cannot_traverse_out_of_the_lexicon_directory(tmp_path):
    """`../../sealed/es-meddocan/test` is a valid-looking lexicon name.

    A rule file is written by an agent, and this is the one place a rule file names a
    path. The sealing rule (CLAUDE.md) is not a thing to enforce by hoping nobody
    composes that string.
    """
    with pytest.raises(RuleError, match=r"\[a-z0-9_\]"):
        one(tmp_path, {"rule_id": "inst", "layer": "gazetteer",
                       "phi_type": "ORGANISATION",
                       "lexicon": "es/../../sealed/es-meddocan/test"})


def test_a_lexicon_reference_needs_a_language_component(tmp_path):
    with pytest.raises(RuleError, match="must be"):
        one(tmp_path, {"rule_id": "inst", "layer": "gazetteer",
                       "phi_type": "ORGANISATION", "lexicon": "insts"})


def test_a_lexicon_language_must_be_in_the_lang_axis(tmp_path):
    with pytest.raises(RuleError, match="not a lang"):
        one(tmp_path, {"rule_id": "inst", "layer": "gazetteer",
                       "phi_type": "ORGANISATION", "lexicon": "xx/insts"})


# ─── context_cue: two halves, joined by the engine ───────────────────────────

def test_a_cue_rule_needs_no_lookbehind(tmp_path):
    rs = one(tmp_path, {"rule_id": "titled", "layer": "context_cue",
                        "phi_type": "NAME", "cue": ["Dr."],
                        "then": "capitalised_words"})
    spans = rs.detect("seen by Dr. Zzyzx Qqqq today")
    assert len(spans) == 1
    assert (spans[0].start, spans[0].end) == (12, 22)


def test_the_span_excludes_the_cue(tmp_path):
    """The cue is the evidence, not the identifier.

    A rule that swallowed `Dr. ` would be scored against gold starting at the name: a
    miss under `fully_covered` and a hit under `relaxed`, which reads as a scoring
    artefact rather than as the boundary error it is.
    """
    rs = one(tmp_path, {"rule_id": "titled", "layer": "context_cue",
                        "phi_type": "NAME", "cue": ["Dr."],
                        "then": "capitalised_word"})
    text = "by Dr. Zzyzx"
    span = rs.detect(text)[0]
    assert text[span.start:span.end].startswith("Z")
    assert "Dr." not in text[span.start:span.end]


def test_every_then_shorthand_compiles_and_can_match(tmp_path):
    """A shorthand that compiles but matches nothing is the silent case."""
    samples = {
        "capitalised_word": "Zzyzx",
        "capitalised_words": "Zzyzx Qqqq",
        "number": "42",
        "digits": "01/02/2020",
        "word": "zzyzx",
        "rest_of_line": "zzyzx qqqq 42",
    }
    assert set(samples) == set(THEN_SHORTHAND)
    for name, sample in samples.items():
        rs = one(tmp_path, {"rule_id": "c", "layer": "context_cue",
                            "phi_type": "NAME", "cue": ["Cue:"], "then": name})
        got = rs.detect(f"Cue: {sample}\n")
        assert got, f"{name} matched nothing"


def test_a_then_that_is_not_a_shorthand_is_a_regex(tmp_path):
    rs = one(tmp_path, {"rule_id": "c", "layer": "context_cue", "phi_type": "ID",
                        "cue": ["Ref"], "then": r"[A-Z]{2}\d{3}"})
    assert len(rs.detect("Ref: AB123")) == 1


def test_a_cue_may_be_a_bare_string(tmp_path):
    rs = one(tmp_path, {"rule_id": "c", "layer": "context_cue", "phi_type": "NAME",
                        "cue": "Dr.", "then": "capitalised_word"})
    assert len(rs.detect("Dr. Zzyzx")) == 1


def test_a_cue_rule_without_then_is_refused(tmp_path):
    """Without it the rule would match the cue words themselves."""
    with pytest.raises(RuleError, match="needs `then:`"):
        one(tmp_path, {"rule_id": "c", "layer": "context_cue", "phi_type": "NAME",
                       "cue": ["Dr."]})


def test_the_gap_bounds_what_may_sit_between_cue_and_target(tmp_path):
    rs = one(tmp_path, {"rule_id": "c", "layer": "context_cue", "phi_type": "NAME",
                        "cue": ["Dr"], "then": "capitalised_word", "gap": 1})
    assert len(rs.detect("Dr Zzyzx")) == 1
    assert rs.detect("Dr     Zzyzx") == []


def test_an_out_of_range_gap_is_refused(tmp_path):
    for bad in (-1, 41, 2.5, True):
        with pytest.raises(RuleError, match="gap must be"):
            one(tmp_path, {"rule_id": "c", "layer": "context_cue",
                           "phi_type": "NAME", "cue": ["Dr"],
                           "then": "capitalised_word", "gap": bad})


def test_a_longer_cue_wins_over_a_shorter_one(tmp_path):
    """Same first-match property as the term list, same fix."""
    rs = one(tmp_path, {"rule_id": "c", "layer": "context_cue", "phi_type": "NAME",
                        "cue": ["Dr", "Dra"], "then": "capitalised_word"})
    assert len(rs.detect("Dra Zzyzx")) == 1


# ─── regex_checksum ──────────────────────────────────────────────────────────

def test_the_dialect_is_the_regex_module_not_re(tmp_path):
    r"""`\p{Lu}` appears in rule_author.md §2's own example and `re` has no such class.

    Under `re` a rule written to the documented dialect raises at load — or matches
    something unintended if the author "simplified" it to get past the error.
    """
    rs = one(tmp_path, {"rule_id": "p", "layer": "regex_checksum",
                        "phi_type": "NAME", "pattern": r"\p{Lu}\p{L}+"})
    assert len(rs.detect("Zzyzx and Ñandú")) == 2


def test_a_checksum_filters_matches_the_pattern_accepted(tmp_path):
    """The layer's whole claim: shape plus arithmetic, not shape alone."""
    rs = one(tmp_path, {"rule_id": "dni", "layer": "regex_checksum",
                        "phi_type": "ID", "pattern": r"\b\d{8}[A-Z]\b",
                        "checksum": "dni_mod23"})
    # 00000000 % 23 == 0 -> 'T'; 'A' is the wrong letter for it.
    assert len(rs.detect("id 00000000T here")) == 1
    assert rs.detect("id 00000000A here") == []


def test_every_declared_checksum_is_implemented_and_discriminates():
    """A checksum that accepts everything is a checksum nobody notices is broken."""
    cases = {
        "dni_mod23": ("00000000T", "00000000A"),
        "nie_mod23": ("X0000000T", "X0000000A"),
        "luhn": ("18", "19"),
        "mod10": ("55", "54"),
    }
    assert set(cases) == set(CHECKSUMS)
    for name, (good, bad) in cases.items():
        assert CHECKSUMS[name](good), f"{name} rejected its own valid case"
        assert not CHECKSUMS[name](bad), f"{name} accepted an invalid case"


def test_an_unimplemented_checksum_is_refused(tmp_path):
    with pytest.raises(RuleError, match="not an implemented checksum"):
        one(tmp_path, {"rule_id": "p", "layer": "regex_checksum", "phi_type": "ID",
                       "pattern": r"\d+", "checksum": "verhoeff"})


def test_a_checksum_on_another_layer_is_refused(tmp_path):
    """The layer names the mechanism a span came from (DESIGN §3, §7).

    A check-digit validation declared under `gazetteer` would attribute the per-layer
    result to the wrong mechanism, which is the one thing §7 is about.
    """
    with pytest.raises(RuleError, match="only meaningful on a regex_checksum"):
        one(tmp_path, {"rule_id": "c", "layer": "context_cue", "phi_type": "ID",
                       "cue": ["Ref"], "then": "digits", "checksum": "luhn"})


def test_a_pattern_that_does_not_compile_is_refused_at_load(tmp_path):
    with pytest.raises(RuleError, match="does not compile"):
        one(tmp_path, {"rule_id": "p", "layer": "regex_checksum", "phi_type": "ID",
                       "pattern": r"REF-(\d{4}"})


def test_the_compile_error_does_not_quote_the_pattern_body(tmp_path):
    """rule_author.md §2 allows cue words in patterns, and an author debugging against a
    real note is one paste away from a pattern that holds a surface form. The rule id
    identifies the rule, which is what a reader needs (CLAUDE.md)."""
    with pytest.raises(RuleError) as e:
        one(tmp_path, {"rule_id": "p", "layer": "regex_checksum", "phi_type": "ID",
                       "pattern": r"Zzyzxville-(\d{4}"})
    assert "Zzyzxville" not in str(e.value)
    assert "es:p" in str(e.value)


# ─── flags are an allowlist ──────────────────────────────────────────────────

def test_the_allowed_flags_are_the_allowlist(tmp_path):
    for name in FLAGS:
        one(tmp_path, {"rule_id": "p", "layer": "regex_checksum", "phi_type": "ID",
                       "pattern": r"REF-\d+", "flags": [name]})


def test_dotall_is_not_an_allowed_flag(tmp_path):
    """It would let `.` cross a note's line boundaries and widen every rule silently."""
    assert "dotall" not in FLAGS
    with pytest.raises(RuleError, match="not an allowed flag"):
        one(tmp_path, {"rule_id": "p", "layer": "regex_checksum", "phi_type": "ID",
                       "pattern": r"REF-.+", "flags": ["dotall"]})


def test_ignorecase_takes_effect(tmp_path):
    rs = one(tmp_path, {"rule_id": "p", "layer": "regex_checksum", "phi_type": "ID",
                        "pattern": r"ref-\d+", "flags": ["ignorecase"]})
    assert len(rs.detect("REF-12")) == 1


# ─── one matcher form per rule ───────────────────────────────────────────────

def test_two_matcher_forms_in_one_rule_are_refused(tmp_path):
    """Which one fired would go unrecorded, and by_rule attributes to a rule."""
    with pytest.raises(RuleError, match="exactly one matcher form"):
        one(tmp_path, {"rule_id": "x", "layer": "gazetteer", "phi_type": "NAME",
                       "terms": ["Zzyzx"], "pattern": r"Zzyzx"})


def test_no_matcher_form_is_refused(tmp_path):
    with pytest.raises(RuleError, match="exactly one matcher form"):
        one(tmp_path, {"rule_id": "x", "layer": "gazetteer", "phi_type": "NAME"})


# ─── phi_type ────────────────────────────────────────────────────────────────

def test_an_undeclared_phi_type_is_refused(tmp_path):
    with pytest.raises(RuleError, match="phi_type axis"):
        one(tmp_path, {"rule_id": "x", "layer": "gazetteer", "phi_type": "PATIENT",
                       "terms": ["Zzyzx"]})


def test_a_non_target_type_is_refused(tmp_path):
    """OTHER is a residual bucket a corpus ships, not a phenomenon (Prohibition 4)."""
    from src.sample import non_target_types
    for blocked in non_target_types():
        with pytest.raises(RuleError, match="no rule may target"):
            one(tmp_path, {"rule_id": "x", "layer": "gazetteer",
                           "phi_type": blocked, "terms": ["Zzyzx"]})


def test_every_targetable_phi_type_is_accepted(tmp_path):
    from src.sample import non_target_types
    for phi_type in set(axis("phi_type")) - non_target_types():
        one(tmp_path, {"rule_id": "x", "layer": "gazetteer", "phi_type": phi_type,
                       "terms": ["Zzyzx"]})


# ─── the file's own header ───────────────────────────────────────────────────

def test_the_declared_lang_is_checked_against_the_load(tmp_path):
    """The prefix comes from the file's language; a mismatch misattributes every span."""
    path = write(tmp_path, [{"rule_id": "x", "layer": "gazetteer",
                             "phi_type": "NAME", "terms": ["Zzyzx"]}], lang="cat")
    with pytest.raises(RuleError, match="declares lang"):
        load_rules("es", path=path)


def test_a_version_is_required_and_must_be_an_integer(tmp_path):
    for bad in (None, "1", 0, True):
        with pytest.raises(RuleError, match="version"):
            one(tmp_path, {"rule_id": "x", "layer": "gazetteer", "phi_type": "NAME",
                           "terms": ["Zzyzx"]}, version=bad)


def test_the_version_is_reported_with_the_ruleset(tmp_path):
    rs = load_rules("es", path=write(tmp_path, [], version=7))
    assert rs.versions == {"es": 7}


def test_a_duplicate_rule_id_is_refused(tmp_path):
    """Two rules in one bucket, and by_rule cannot tell them apart afterwards."""
    with pytest.raises(RuleError, match="duplicate rule_id"):
        load_rules("es", path=write(tmp_path, [
            {"rule_id": "x", "layer": "gazetteer", "phi_type": "NAME",
             "terms": ["Zzyzx"]},
            {"rule_id": "x", "layer": "regex_checksum", "phi_type": "ID",
             "pattern": r"\d+"},
        ]))


def test_an_absent_rule_file_loads_as_no_rules(tmp_path):
    """The state iteration 1 starts from — zero rules, not an error."""
    rs = load_rules("es", path=tmp_path / "nothing.yaml")
    assert rs.rules == []


def test_an_unknown_lang_is_refused():
    with pytest.raises(RuleError, match="not a lang"):
        load_rules("xx")


def test_an_empty_rules_list_is_allowed(tmp_path):
    assert load_rules("es", path=write(tmp_path, [])).rules == []


def test_a_score_out_of_range_is_refused(tmp_path):
    for bad in (-0.1, 1.1, "high", True):
        with pytest.raises(RuleError, match="score must be"):
            one(tmp_path, {"rule_id": "x", "layer": "gazetteer", "phi_type": "NAME",
                           "terms": ["Zzyzx"], "score": bad})


def test_a_score_is_carried_onto_the_span(tmp_path):
    rs = one(tmp_path, {"rule_id": "x", "layer": "gazetteer", "phi_type": "NAME",
                        "terms": ["Zzyzx"], "score": 0.5})
    assert rs.detect("Zzyzx")[0].score == 0.5


# ─── the detector resolves nothing on its own ────────────────────────────────

def test_overlapping_matches_from_two_rules_are_both_returned(tmp_path):
    """Deduplication and merge policy belong elsewhere (DESIGN §4, §9.3).

    A detector that resolved its own overlaps would make fixed-priority, union and
    agent-arbiter produce the same spans, and the merge-policy comparison would be
    measuring nothing.
    """
    rs = load_rules("es", path=write(tmp_path, [
        {"rule_id": "a", "layer": "gazetteer", "phi_type": "ORGANISATION",
         "terms": ["Zzyzx Clinic"]},
        {"rule_id": "b", "layer": "gazetteer", "phi_type": "ORGANISATION",
         "terms": ["Zzyzx"]},
    ]))
    spans = rs.detect("Zzyzx Clinic")
    assert len(spans) == 2
    assert {s.rule_id for s in spans} == {"es:a", "es:b"}


def test_a_zero_width_match_produces_no_span(tmp_path):
    """`Span.__post_init__` refuses an empty span, so it must not reach it."""
    rs = one(tmp_path, {"rule_id": "c", "layer": "context_cue", "phi_type": "NAME",
                        "cue": ["Dr"], "then": r"\p{Lu}*"})
    for span in rs.detect("Dr "):
        assert span.end > span.start


# ─── the corpus-level load ───────────────────────────────────────────────────

def test_a_corpus_loads_every_lang_it_declares(tmp_path, monkeypatch):
    """es-carmen loads es and cat (DESIGN §5.2), unioned with no per-doc selection."""
    from src.corpora.base import rule_langs
    langs = rule_langs("es-carmen")
    assert set(langs) == {"es", "cat"}
    paths = {}
    for lang in langs:
        paths[lang] = write(tmp_path, [
            {"rule_id": "inst", "layer": "gazetteer", "phi_type": "ORGANISATION",
             "terms": [f"Zzyzx{lang}"]}], lang=lang)
    rs = load_for_corpus("es-carmen", paths=paths)
    assert {r.rule_id for r in rs.rules} == {"es:inst", "cat:inst"}
    assert set(rs.versions) == {"es", "cat"}


def test_the_same_rule_id_in_two_langs_stays_distinct(tmp_path):
    """The prefixes are what make ids unique across files, so both may define it."""
    paths = {lang: write(tmp_path, [
        {"rule_id": "doctor_prefix", "layer": "context_cue", "phi_type": "NAME",
         "cue": ["Dr"], "then": "capitalised_word"}], lang=lang)
        for lang in ("es", "cat")}
    rs = load_for_corpus("es-carmen", paths=paths)
    assert len({r.rule_id for r in rs.rules}) == 2


# ─── the committed example file ──────────────────────────────────────────────

def test_the_example_rule_file_loads_if_it_exists():
    """`rules/es.yaml` is a schema example during the rehearsal and deleted after it.

    Whether it is present is not asserted — iteration 1 requires its absence and the
    rehearsal requires its presence, so a test that demanded either would fail on one
    side of the practice session. That it *loads* is asserted, because a schema example
    that does not is worse than none.
    """
    path = ROOT / "rules" / "es.yaml"
    if not path.exists():
        pytest.skip("no example file — the state iteration 1 starts from")
    rs = load_rules("es", path=path)
    assert {r.layer for r in rs.rules} == rule_layers(), (
        "the example is meant to show all three layers")


def test_the_example_rule_file_matches_nothing_in_the_corpus():
    """It is a schema, not content: obvious dummy patterns and no corpus coupling.

    An example file that matched real text would be rule development done outside an
    iteration, which §8 forbids and which the frozen window makes uninterpretable.
    """
    path = ROOT / "rules" / "es.yaml"
    if not path.exists():
        pytest.skip("no example file")
    rs = load_rules("es", path=path)
    text = ("Paciente Zzyzx Qqqq, DNI 00000000T, atendido en el Hospital Zzyzx "
            "por el Dr. Zzyzx el 01/02/2020. Ref AB123.")
    assert rs.detect(text) == [], (
        "the example file matched constructed clinical-shaped text; it is supposed to "
        "demonstrate the schema and match nothing")
