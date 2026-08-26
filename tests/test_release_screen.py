"""Regression tests for tools/release_screen.py.

The screener is the last thing standing between DUA-restricted note text and a
public repository, so its holes get tests rather than fixes-and-hope.

The named case here is `disguised.sh`: a file under a denied prefix
(`data/acquire/`) that was published by a path exception keyed on extension, and
whose content was never read because `.sh` was absent from TEXT_EXT. It passed
both layers at once. Both layers were changed; both directions are tested.

    python3 -m pytest tests/ -q
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import release_screen as rs  # noqa: E402

NOTE = "Admission Date: [**2101-1-1**]\nCHIEF COMPLAINT: pain\n"


# ─── the original bypass ────────────────────────────────────────────────────

def test_disguised_sh_is_denied_by_path():
    """A .sh under data/acquire/ that is not fetch_*.sh must be denied outright."""
    assert rs.deny("data/acquire/disguised.sh")


def test_disguised_sh_content_is_caught_even_if_path_were_allowed():
    """Second layer, independent of the first: the note text itself is detected.

    Written so that widening DENY_EXCEPTIONS again cannot silently reopen the hole
    — the content sniffer has to catch it too.
    """
    assert rs.sniff("disguised.sh", blob=NOTE.encode(), force=True) is not None


def test_disguised_sh_is_flagged_end_to_end(tmp_path):
    """Full screen_tree run: the file appears in BLOCKED or SUSPECT, not in neither.

    This is the exact reproduction of the reported defect. Before the fix this
    assertion failed: the file was in no output list at all.
    """
    acq = tmp_path / "data" / "acquire"
    acq.mkdir(parents=True)
    (acq / "disguised.sh").write_text(NOTE, encoding="utf-8")

    blocked, sealed, quarantined, suspect, _ = rs.screen_tree(str(tmp_path))
    named = (set(blocked) | set(sealed) | set(quarantined)
             | {p for p, _ in suspect})
    assert "data/acquire/disguised.sh" in named


# ─── the exception must still work, and only for what it names ──────────────

@pytest.mark.parametrize("path", [
    "data/acquire/fetch_meddocan.sh",
    "data/acquire/fetch_grascco.sh",
    "data/README.md",
    "data/es-meddocan/README.md",
])
def test_allowed_paths_are_not_denied(path):
    assert not rs.deny(path)


@pytest.mark.parametrize("path", [
    "data/acquire/notes.sh",            # plausible name, not fetch_*
    "data/acquire/anything.sh",
    "data/acquire/patient notes.sh",    # space in name
    "data/acquire/fetch_notes.py",      # .py is no longer an exception at all
    "data/acquire/sub/fetch_x.sh",      # exception is top level only
    "data/loose.sh",
    "data/raw/es-meddocan/leak.txt",
    "data/derived/notes.jsonl",
    "sealed/ko-surro/note.txt",
    # filled prompt instances — see the section below
    "docs/prompts/filled/rule_author_3.md",
    "prompts/rendered/rule_author.md",
    "rule_author.filled.md",
    "logs/rule_author_filled_prompt_3.txt",
    "tmp/prompt_iter3.md",
])
def test_denied_paths(path):
    assert rs.deny(path)


# ─── filled prompts never touch disk ───────────────────────────────────────
#
# The template is committed and the filled instance is not: the RuleAuthor prompt's
# error-span block carries ±120 characters of dev text (docs/prompts/rule_author.md
# §1.4, §7), which is corpus text. On a DUA corpus it may travel to Bedrock and must
# not be written anywhere.


@pytest.mark.parametrize("path", [
    "docs/prompts/rule_author.md",
    "docs/prompts/auditor.md",
    "prompts/rule_author.md",
])
def test_prompt_templates_are_publishable(path):
    """The templates are the artifact. Denying them would defeat the point."""
    assert not rs.deny(path)


def test_a_filled_prompt_on_disk_is_blocked_not_quarantined(tmp_path):
    """It must be BLOCKED, which is why the pattern is NOT in .gitignore.

    The convention is "never written to disk", not "never committed". A gitignored
    path is counted as Quarantined — expected, a summary line, exit 0 — and that is
    the right reading for a downloaded corpus, which is supposed to exist. A filled
    prompt is not supposed to exist, so it has to reach the line that gates the
    commit. This is the opposite call from `sealed/` for a different reason: the
    sealed fold must be on disk and this must not.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    d = tmp_path / "docs" / "prompts" / "filled"
    d.mkdir(parents=True)
    (d / "rule_author_3.md").write_text("context: some dev text\n", encoding="utf-8")

    blocked, sealed, quarantined, _suspect, _ = rs.screen_tree(str(tmp_path))
    assert "docs/prompts/filled/rule_author_3.md" in blocked
    assert not quarantined and not sealed


def test_the_filled_prompt_patterns_are_not_gitignored():
    """Stated as a test because adding them to .gitignore looks like an improvement.

    It would silently downgrade every one of them from BLOCKED to Quarantined.
    """
    ignored = subprocess.run(
        ["git", "-C", ROOT, "check-ignore", "-q", "--",
         "docs/prompts/filled/x.md"], capture_output=True)
    assert ignored.returncode != 0, (
        "a filled-prompt path is gitignored, so it would be reported as expected "
        "rather than as a violation"
    )


# ─── rule_id may not carry a surface form ──────────────────────────────────
#
# `rules/*.yaml` is public, and every rule_id is published a second time through
# metrics.json's by_rule block (DESIGN §9.3). Forbidding surfaces in patterns while
# leaving names free is a bypass, and the name is free text an agent writes.


@pytest.mark.parametrize("rule_id", [
    "es:doctor_prefix",
    "es:nhc_checksum",
    "cat:street_type_particle",
    "es:dob_cue",
    "es:cp_5digit",
    "de:hospital_gazetteer",
    "es:patient_given_name_cue",
    "es:record_number_checksum",
    "es:title_abbrev_context_window",
])
def test_mechanism_names_pass(rule_id):
    """A false positive here costs a real rule its name, so the vocabulary is wide."""
    assert rs.rule_id_findings(f"  - rule_id: {rule_id}\n") == []


@pytest.mark.parametrize("rule_id", [
    "es:jperez",                 # a surname, lower case, one token
    "es:perez_ruiz",             # two surnames — the case shape alone cannot catch
    "es:calle_mayor",            # a street, and the DESIGN §9.3 example
    "es:born_1978",              # a value
    "es:García",                 # capitalised
    "ko:김철수",                  # non-ASCII
    "es:nacido_el_3_de_mayo",    # a phrase from the corpus language
    "es:matches_juan_perez",
])
def test_names_carrying_a_surface_are_flagged(rule_id):
    found = rs.rule_id_findings(f"  - rule_id: {rule_id}\n")
    assert found, f"{rule_id} passed the rule_id screen"


def test_the_check_is_a_vocabulary_not_a_blacklist():
    """The design decision, asserted because a blacklist is the obvious alternative.

    `perez_ruiz` and `street_type` have identical shape: lower case, ASCII, two
    tokens, no digits. No shape rule separates them. A blacklist would separate them
    by listing the names it objects to — which means storing surface forms in the
    repository, the exact thing being prevented. A positive vocabulary works because
    a name assembled only from mechanism words *cannot* designate an individual.
    """
    assert rs.rule_id_findings("  - rule_id: es:street_type\n") == []
    assert rs.rule_id_findings("  - rule_id: es:perez_ruiz\n")
    # And the vocabulary itself holds no corpus content: English structural terms.
    assert all(t.isascii() and t.islower() for t in rs.RULE_ID_VOCAB)


# ─── the language layer ─────────────────────────────────────────────────────
# The English-only vocabulary rejected 23 of the 28 names in the first port-oneshot
# output, every one of them for naming a clinical formula in the corpus language.
# Prohibition 2 permits exactly that, so the vocabulary was wrong and not the names.
# What must not move is the line itself: a formula is allowed, designating an
# individual is not, in every language.


@pytest.mark.parametrize("rule_id", [
    "paciente_cue",           # 환자 — a role word, the formula Prohibition 2 allows
    "firmado_cue",            # signed by — document boilerplate
    "atendido_por_cue",       # attended by
    "calle_cue",              # street as a *type*, not a street name
    "avenida_cue",
    "centro_salud_cue",       # a kind of institution, not one institution
    "domicilio_cue",
    "profesion_cue",
    "don_dona_prefix",        # honorifics: markers that precede a name
])
def test_spanish_formula_names_pass_under_the_spanish_layer(rule_id):
    """These are the names the English-only vocabulary rejected.

    Unprefixed on purpose: that is how the arm actually wrote them, and how the
    committed `rules/es.yaml` writes them.
    """
    assert rs.rule_id_findings(f"  - rule_id: {rule_id}\n", lang="es") == []


@pytest.mark.parametrize("rule_id", [
    "perez_ruiz",             # two surnames
    "garcia_lopez",
    "maria_carmen",           # given names
    "calle_mayor",            # `calle` is vocabulary; the street's *name* is not
    "hospital_clinic_barcelona",   # a specific institution
    "paciente_perez",         # a formula token used to smuggle one that is not
])
def test_the_spanish_layer_does_not_pass_a_person_or_place(rule_id):
    """The layer widens the categories and not the line.

    Each of these is inside the language the layer opened, so nothing about being
    Spanish is what gets them through — membership in a closed set of formulae is,
    and a name is not a formula. `calle_mayor` and `paciente_perez` are the pointed
    cases: one token from the layer does not license the rest of the name.
    """
    assert rs.rule_id_findings(f"  - rule_id: {rule_id}\n", lang="es"), (
        f"{rule_id} passed under the es layer")


def test_a_layer_is_scoped_to_its_own_language():
    """Kills `an_unknown_language_gets_every_layer` and the cross-language widening.

    A Spanish formula in a German rule name is not a German formula. If the layers
    were unioned — the tempting simplification, since it makes the lookup
    language-independent — every layer's words would be sayable in every language's
    files, and the widest vocabulary in the tool would be the only one that exists.
    """
    assert rs.rule_id_findings("  - rule_id: paciente_cue\n", lang="es") == []
    assert rs.rule_id_findings("  - rule_id: paciente_cue\n", lang="de")
    assert rs.rule_id_findings("  - rule_id: strasse_cue\n", lang="de") == []
    assert rs.rule_id_findings("  - rule_id: strasse_cue\n", lang="es")


def test_an_unknown_language_gets_no_layer_at_all():
    """Not the union, and not an error. The pre-layer behaviour."""
    assert rs.rule_id_findings("  - rule_id: paciente_cue\n", lang="xx")
    assert rs.rule_id_findings("  - rule_id: paciente_cue\n", lang=None)
    # An English mechanism name is unaffected by any of this.
    assert rs.rule_id_findings("  - rule_id: doctor_prefix\n", lang="xx") == []


def test_the_layer_is_keyed_on_the_path_not_on_the_id():
    """Kills `the_language_layer_is_keyed_on_the_id_the_model_wrote`.

    The path comes from the arm's configuration; the id prefix is free text in a file
    the model wrote. Keyed on the prefix, the screened text chooses its own
    vocabulary. Asserted through `sniff`, which is where the path is available.
    """
    surname = "  - rule_id: es:perez_ruiz\n"
    # The prefix claims Spanish. The name is still a surname, under any layer.
    assert rs.sniff("rules/es.yaml", blob=surname.encode()) is not None
    # And a Spanish formula in a German file does not become sayable by saying `es:`.
    smuggled = "  - rule_id: es:paciente_cue\n"
    assert rs.sniff("rules/de.yaml", blob=smuggled.encode()) is not None
    # Same bytes, the file the harness would actually have written them to: clean.
    assert rs.sniff("rules/es.yaml", blob="  - rule_id: paciente_cue\n".encode()) is None


def test_a_prefix_disagreeing_with_the_path_drops_the_layer():
    """Kills `a_disagreeing_prefix_still_opens_the_layer`.

    Disagreement between the harness's path and the model's prefix resolves to the
    narrower vocabulary. Checked at the function, so the branch is exercised directly
    rather than through whichever way `sniff` happens to derive the language.
    """
    assert rs.rule_id_findings("  - rule_id: de:paciente_cue\n", lang="es")
    assert rs.rule_id_findings("  - rule_id: paciente_cue\n", lang="es") == []
    assert rs.rule_id_findings("  - rule_id: es:paciente_cue\n", lang="es") == []


def test_layer_membership_is_exact_not_substring():
    """Kills `the_language_layer_is_a_substring_test`.

    A closed set decided by containment is not closed: every fragment of every listed
    word joins it, and fragments are what names are made of.
    """
    assert "anos" in rs.RULE_ID_VOCAB_BY_LANG["es"]
    assert rs.rule_id_findings("  - rule_id: ana_cue\n", lang="es"), (
        "a fragment of a listed word passed the layer")
    assert rs.rule_id_findings("  - rule_id: ano_cue\n", lang="es")


def test_every_layer_language_is_a_declared_lang_axis_value():
    """A layer for a language the experiment does not have is a vocabulary nobody
    reviewed against a corpus. CLAUDE.md: naming.yaml is the only vocabulary."""
    import yaml
    with open(os.path.join(ROOT, "config", "naming.yaml"), encoding="utf-8") as fh:
        langs = set(yaml.safe_load(fh)["axes"]["lang"])
    assert set(rs.RULE_ID_VOCAB_BY_LANG) <= langs, (
        f"layers for undeclared langs: {set(rs.RULE_ID_VOCAB_BY_LANG) - langs}")


def test_no_layer_word_could_be_a_surface_form_by_shape():
    """The shape rules, re-applied to the layers themselves.

    The exclusion categories — no personal names, no place names, nothing that can
    designate one individual or institution — are held by review and cannot be
    asserted. What *can* be asserted is that no layer word has the shape of a quoted
    surface: a capital, a digit run, a non-ASCII character, or the length of a phrase.
    That closes the crudest way a surface form enters through a layer.
    """
    for lang, words in rs.RULE_ID_VOCAB_BY_LANG.items():
        for w in words:
            assert w == w.lower(), f"{lang}: {w!r} is not lower case"
            assert w.isascii(), f"{lang}: {w!r} is not ASCII"
            assert w.isalpha(), f"{lang}: {w!r} is not purely alphabetic"
            assert len(w) <= rs.RULE_ID_VOCAB_LANG_MAX_LEN, f"{lang}: {w!r} is too long"
            for pattern, _why in rs.RULE_ID_RULES:
                assert not pattern.search(w), f"{lang}: {w!r} matches a shape rule"


def test_the_arm_rule_file_lang_comes_from_the_filename():
    """Both `rules/es.yaml` and an arm's `rules/iter3/es.yaml` carry {lang}."""
    assert rs.rule_file_lang("rules/es.yaml") == "es"
    assert rs.rule_file_lang(
        "results/c/R/sup-free/port-oneshot/rules/iter3/de.yaml") == "de"
    assert rs.rule_file_lang("rules/unknown.yaml") is None
    assert rs.rule_file_lang("rules/ES.yaml") == "es"


def test_the_finding_does_not_quote_the_id():
    """The id may *be* the surface form, and this message reaches a CI log.

    CLAUDE.md: no corpus text in messages, logs or warnings — and release_screen.py
    does not run on its own output.
    """
    text = "  - rule_id: es:jperez_1978\n"
    why = rs.sniff("rules/es.yaml", blob=text.encode())
    assert why is not None
    assert "jperez" not in why and "1978" not in why


def test_a_rules_file_with_a_bad_id_is_suspect_end_to_end(tmp_path):
    """`rules/` is in ALLOW_HINTS, so this checks the sniff wins over the hint."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "es.yaml").write_text(
        "version: 1\nlang: es\nrules:\n  - rule_id: es:perez_ruiz\n"
        "    layer: gazetteer\n    phi_type: NAME\n",
        encoding="utf-8")
    blocked, _sealed, _quar, suspect, allowed = rs.screen_tree(str(tmp_path))
    assert not blocked
    assert "rules/es.yaml" in {p for p, _ in suspect}
    assert "rules/es.yaml" not in allowed


def test_a_clean_rules_file_is_allowed_not_suspect(tmp_path):
    """A screener that flags every rule file trains people to ignore it."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "es.yaml").write_text(
        "version: 1\nlang: es\nrules:\n  - rule_id: doctor_prefix\n"
        "    layer: context_cue\n    phi_type: NAME\n"
        "    pattern: '(?<=\\bDr\\.\\s)\\p{Lu}\\p{L}+'\n",
        encoding="utf-8")
    _blocked, _sealed, _quar, suspect, allowed = rs.screen_tree(str(tmp_path))
    assert "rules/es.yaml" not in {p for p, _ in suspect}
    assert "rules/es.yaml" in allowed


def _arm_dir(tmp_path):
    d = tmp_path / "results" / "es-meddocan" / "R" / "sup-free" / "port-human"
    d.mkdir(parents=True)
    return d


def test_the_port_human_result_files_are_allowed(tmp_path):
    """Both hold offsets, types and hashes and no corpus text (DESIGN §11.2), so the
    screener should say so rather than leaving them in neither category — an unclassified
    file reads as reviewed to anyone scanning the summary."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    d = _arm_dir(tmp_path)
    (d / "human_log.jsonl").write_text(
        '{"iteration": 1, "event": "read_sample", "evidence": null}\n', encoding="utf-8")
    (d / "window_freeze.json").write_text(
        '{"corpus": "es-meddocan", "prompt_sha256": "sha256:00"}\n', encoding="utf-8")
    _blocked, _sealed, _quar, suspect, allowed = rs.screen_tree(str(tmp_path))
    assert not suspect
    assert "results/es-meddocan/R/sup-free/port-human/human_log.jsonl" in allowed
    assert "results/es-meddocan/R/sup-free/port-human/window_freeze.json" in allowed


def test_the_human_log_under_another_arm_is_not_allowed(tmp_path):
    """`human_log.jsonl`'s {porting} component is the literal `port-human`, not a
    wildcard. Nothing writes this file under another arm — `human_minutes` and a free-text
    `decision` are a person's fields — so its presence there means something unreviewed
    did."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    d = tmp_path / "results" / "es-meddocan" / "R" / "sup-free" / "port-loop"
    d.mkdir(parents=True)
    (d / "human_log.jsonl").write_text('{"iteration": 1}\n', encoding="utf-8")
    _blocked, _sealed, _quar, _suspect, allowed = rs.screen_tree(str(tmp_path))
    assert "results/es-meddocan/R/sup-free/port-loop/human_log.jsonl" not in allowed


def test_every_arms_freeze_record_is_allowed(tmp_path):
    """`window_freeze.json` is the other way round, and DESIGN §6.3 is why.

    Every arm freezes its own window at first use, under its own {porting} value
    (`paths.armfreeze`). Pinning this pattern to `port-human` would leave each agent
    arm's freeze record in no category at all — and an unclassified file reads as
    reviewed to whoever scans the summary, which is the failure the allowlist exists to
    prevent. The record holds hashes, a revision and axis values; there is no corpus
    text in it by construction.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    frozen = '{"corpus": "es-meddocan", "prompt_sha256": "sha256:00", "revision": 7}\n'
    for arm in ("port-human", "port-oneshot", "port-loop", "port-multi",
                "port-selfdesign"):
        d = tmp_path / "results" / "es-meddocan" / "R" / "sup-free" / arm
        d.mkdir(parents=True)
        (d / "window_freeze.json").write_text(frozen, encoding="utf-8")
    _blocked, _sealed, _quar, suspect, allowed = rs.screen_tree(str(tmp_path))
    assert not suspect
    for arm in ("port-human", "port-oneshot", "port-loop", "port-multi",
                "port-selfdesign"):
        assert f"results/es-meddocan/R/sup-free/{arm}/window_freeze.json" in allowed


def test_the_allowed_freeze_path_is_the_one_naming_yaml_declares(tmp_path):
    """The pattern and `paths.armfreeze` must describe the same file.

    Two places state where a freeze record lives, and a screener allowing a path nothing
    writes — or refusing one an arm does — is the shape of failure that only shows up on
    the day a commit is being prepared.
    """
    from src.corpora.base import path_template
    template = path_template("armfreeze")
    rel = template.format(corpus="es-meddocan", detector="R", supervision="sup-free",
                          porting="port-oneshot")
    assert any(re.search(p, rel) for p in rs.ALLOW_PATTERNS), rel

    human = path_template("humanfreeze").format(
        corpus="es-meddocan", detector="R", supervision="sup-free")
    assert any(re.search(p, human) for p in rs.ALLOW_PATTERNS), human
    assert "port-human" in human, (
        "humanfreeze must stay pinned to the retired arm (DESIGN §6.3): a templated "
        "humanfreeze would let a later arm overwrite port-human's record")


def test_the_allowed_arm_rule_path_is_the_one_naming_yaml_declares():
    """`paths.armrules` and the ALLOW pattern must describe the same file (DESIGN §5.3).

    Same requirement as the freeze path one above, and it matters more here because two
    separate patterns in this file have to follow `paths.armrules`: this ALLOW entry, so
    an arm's rule file is not an uncategorised path, and `sniff()`'s rule_id check, so the
    file is screened at all. `tests/test_arm_rules_path.py` covers the second through
    `sniff()` on a real file; this covers the first.
    """
    from src.corpora.base import path_template
    rel = path_template("armrules").format(
        corpus="es-meddocan", detector="R", supervision="sup-free",
        porting="port-oneshot", iteration=1, lang="es")
    assert any(re.search(p, rel) for p in rs.ALLOW_PATTERNS), rel
    # And the bootstrap example is allowed by the `rules/` hint rather than by this
    # pattern — two locations, two reasons, and neither standing in for the other.
    assert not any(re.search(p, "rules/es.yaml") for p in rs.ALLOW_PATTERNS)
    assert any("rules/es.yaml".startswith(h) for h in rs.ALLOW_HINTS)


def test_the_four_iteration_scoped_paths_split_two_and_two():
    """**One directory, four files, and the screener must put them in two classes.**

    DESIGN §5.5 puts a round's whole record under `iter{n}/`: the predictions and the score
    (`paths.iterspans`, `paths.itermetrics`) which hold what the four-deep `spans.jsonl` and
    `metrics.json` hold and are allowed for the same reason, and beside them the audit
    report and the per-span error export, which are lists of the positions of residual
    identifiers and are denied.

    Asserted as one test rather than four because the property is the *split*, and the way
    it breaks is a pattern anchored on the directory instead of the filename. A
    `results/.../iter[0-9]+/` ALLOW entry would publish all four; the same shape on the deny
    side would suppress the arm's own scores. Neither mistake is visible from either side
    alone, which is why both directions are checked here against the four real templates.
    """
    from src.corpora.base import path_template
    axes = dict(corpus="es-meddocan", detector="R", supervision="sup-free",
                porting="port-loop", iteration=3, draw=2)
    allowed = {"itermetrics", "iterspans"}
    denied = {"auditreport", "itererrors", "auditdraw"}
    for key in allowed | denied:
        rel = path_template(key).format(**axes)
        assert f"/iter{axes['iteration']}/" in rel, (
            f"paths.{key} must be iteration-scoped (DESIGN §5.5): {rel}")
        if key in allowed:
            assert any(re.search(p, rel) for p in rs.ALLOW_PATTERNS), rel
            assert not rs.deny(rel), rel
        else:
            assert rs.deny(rel), (
                f"paths.{key} is not denied: {rel}. DESIGN §5.5 deny-lists it — on a DUA "
                "corpus it is a map of the identifiers the rules did not remove.")
            assert not any(re.search(p, rel) for p in rs.ALLOW_PATTERNS), rel


def test_no_two_path_keys_name_one_file():
    """**Two keys formatting to one path is worse than the axis-free path §5.3 rejected.**

    DESIGN §5.5 (2026-08-13) records the near-miss this is written from: the loop's
    implementation order called for a `paths.leakreport` for the Auditor's report, and
    `paths.auditreport` — declared and screened in `c998610` — is already that file. Both
    keys would have resolved correctly and both would have screened correctly, so nothing
    at the path layer would have complained; the defect only appears when two writers
    disagree about which name they hold, and §3's "two agents never write the same file"
    stops being checkable because the file has two names and neither is wrong.

    Asserted over the whole `paths` block and not over that pair, because the next
    near-duplicate will arrive with its own good reason. Two templates that differ only in
    a placeholder's *name* would still be one file for a given set of values, so the
    comparison is on the formatted result under one concrete assignment rather than on the
    template strings — which is also what catches a key added as a copy with `{iteration}`
    renamed to `{round}`.

    `paths.metrics` / `paths.itermetrics` and `paths.spans` / `paths.iterspans` are the pairs
    this must *not* flag: they differ by `iter{iteration}/`, which is a real difference at
    every round, and §5.5's duplication rule is about their contents coinciding on the final
    round rather than about their paths coinciding ever. Filling `iteration` proves that.
    """
    from src.corpora.base import naming
    values = dict(corpus="es-meddocan", detector="R", supervision="sup-free",
                  porting="port-loop", iteration=3, lang="es", draw=2)
    seen: dict[str, str] = {}
    for key, template in naming()["paths"].items():
        try:
            rel = template.format(**values)
        except KeyError as exc:  # a placeholder this test does not know
            raise AssertionError(
                f"paths.{key} uses the placeholder {exc.args[0]!r}, which this test cannot "
                "fill. Add it to `values` — an unfillable template is a template this "
                "duplicate check silently skips."
            ) from None
        assert "{" not in rel, f"paths.{key} left a placeholder unfilled: {rel}"
        if rel in seen:
            raise AssertionError(
                f"paths.{key} and paths.{seen[rel]} both name {rel}. One artefact, one key "
                "(DESIGN §3, §5.5): a second name for a file that has one screens and "
                "resolves correctly, so nothing fails until two writers hold different keys "
                "for it — and then the agent-to-file correspondence cannot be checked, "
                "because neither name is wrong."
            )
        seen[rel] = key


def test_the_iteration_scoped_score_is_allowed_under_every_arm_and_round():
    """A pattern that matched only `iter1/` would leave every later round uncategorised,
    and uncategorised reads as reviewed to whoever scans the summary."""
    from src.corpora.base import path_template
    for arm in ("port-loop", "port-multi", "port-selfdesign"):
        for iteration in (1, 2, 8, 903):
            for key in ("itermetrics", "iterspans"):
                rel = path_template(key).format(
                    corpus="es-meddocan", detector="R", supervision="sup-free",
                    porting=arm, iteration=iteration)
                assert any(re.search(p, rel) for p in rs.ALLOW_PATTERNS), rel


def test_the_un_iterated_result_paths_are_still_allowed():
    """**The reason §5.5 added a second key instead of widening `paths.metrics`.**

    `port-oneshot-nofence`'s `metrics.json` and `spans.jsonl` are committed at four axes
    deep. Had the existing key gained `{iteration}`, they would be matched by no ALLOW entry
    and reachable from no `metrics_path()` call — and §4 refused exactly that migration for
    a freeze record. This pins the four-deep case so a later edit to the patterns above
    cannot quietly take it away.
    """
    from src.corpora.base import path_template
    for key in ("metrics", "spans"):
        rel = path_template(key).format(
            corpus="es-meddocan", detector="R", supervision="sup-free",
            porting="port-oneshot-nofence")
        assert "iter" not in rel, (
            f"paths.{key} must stay un-iterated (DESIGN §5.5): {rel}")
        assert any(re.search(p, rel) for p in rs.ALLOW_PATTERNS), rel


def test_the_allowed_format_failure_path_is_the_one_naming_yaml_declares():
    """`paths.formatfailure` and the ALLOW pattern must describe the same file.

    Same requirement as the freeze and rule-path entries above. It carries an extra
    consequence here: DESIGN §10 A2 makes a format failure the arm's *result*, so a
    screener that left this path uncategorised would put the appendix's evidence in the
    class that reads as reviewed to whoever scans the summary — and a failure nobody may
    publish cannot support the sentence "this model could not do it".
    """
    from src.corpora.base import path_template
    rel = path_template("formatfailure").format(
        corpus="es-meddocan", detector="R", supervision="sup-free",
        porting="port-oneshot")
    assert any(re.search(p, rel) for p in rs.ALLOW_PATTERNS), rel
    # Under every arm, for `window_freeze.json`'s reason: the file belongs to whichever
    # {porting} value made the call, and a pattern pinned to one of them would leave the
    # others uncategorised.
    for arm in ("port-oneshot", "port-loop", "port-multi", "port-selfdesign"):
        other = path_template("formatfailure").format(
            corpus="es-meddocan", detector="R", supervision="sup-free", porting=arm)
        assert any(re.search(p, other) for p in rs.ALLOW_PATTERNS), other


def test_a_format_failure_record_is_allowed_end_to_end(tmp_path):
    """Through `screen_tree`, so the pattern is exercised where it is applied."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    d = tmp_path / "results" / "es-meddocan" / "R" / "sup-free" / "port-oneshot"
    d.mkdir(parents=True)
    (d / "format_failure.json").write_text(
        '{"model_id": "us.anthropic.claude-opus-5", "error": "es.yaml: version must be '
        'an integer", "response": "version: three\\nlang: es\\n"}\n', encoding="utf-8")
    _blocked, _sealed, _quar, suspect, allowed = rs.screen_tree(str(tmp_path))
    assert not suspect
    assert "results/es-meddocan/R/sup-free/port-oneshot/format_failure.json" in allowed


def test_a_format_failure_carrying_note_text_is_still_suspect(tmp_path):
    """The sniffer decides the content, and here that ordering does real work.

    The other allowed results files hold offsets and hashes by construction. This one holds
    a raw model response, and "the first call is shown §§1.1-1.2 only" is a fact about
    today's arm rather than a property of the path — a completion that echoed a prompt
    carrying §1.4 would carry the corpus with it. So the file is on the ALLOW list and the
    content check still runs first.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    d = tmp_path / "results" / "es-meddocan" / "R" / "sup-free" / "port-oneshot"
    d.mkdir(parents=True)
    (d / "format_failure.json").write_text(
        '{"response": "Admission Date: [**2151-7-16**] Discharge Date: [**2151-8-4**] '
        'CHIEF COMPLAINT: ..."}\n', encoding="utf-8")
    _blocked, _sealed, _quar, suspect, allowed = rs.screen_tree(str(tmp_path))
    rel = "results/es-meddocan/R/sup-free/port-oneshot/format_failure.json"
    assert any(rel == p for p, _kind in suspect), (
        f"the sniffer did not reach {rel}; ALLOW is a statement about the path and the "
        "content check runs first")
    assert rel not in allowed


def test_a_log_line_carrying_note_text_is_still_suspect(tmp_path):
    """Being on the ALLOW list is a statement about the path, never about the content —
    the same order the rules-file tests above establish. A `decision` field is free text
    written by a person, which is exactly where a surface form gets pasted."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    d = _arm_dir(tmp_path)
    (d / "human_log.jsonl").write_text(
        '{"iteration": 1, "decision": "Admission Date: [**2151-7-16**] '
        'CHIEF COMPLAINT: chest pain, HISTORY OF PRESENT illness"}\n', encoding="utf-8")
    blocked, _sealed, _quar, suspect, allowed = rs.screen_tree(str(tmp_path))
    assert not blocked
    path = "results/es-meddocan/R/sup-free/port-human/human_log.jsonl"
    assert path in {p for p, _ in suspect}
    assert path not in allowed


def test_the_pattern_field_is_not_screened_for_vocabulary():
    """Prohibition 2 allows cue words in a pattern; that is what the layer is.

    Only `rule_id` is screened this way. A screener that applied the vocabulary to
    patterns would reject every context_cue rule ever written.
    """
    text = ("rules:\n  - rule_id: doctor_prefix\n"
            "    pattern: '(?<=\\bDr\\.\\s)\\p{Lu}\\p{L}+'\n"
            "    comment: cue is the title, not the name\n")
    assert rs.sniff("rules/es.yaml", blob=text.encode()) is None


def test_unprefixed_ids_are_screened_too():
    """The loader adds the prefix; the file on disk does not have it.

    So the check must work on both forms, or it screens only what it never sees.
    """
    assert rs.rule_id_findings("  - rule_id: perez_ruiz\n")
    assert rs.rule_id_findings("  - rule_id: doctor_prefix\n") == []


# ─── the axis words are in the vocabulary by definition ────────────────────

def test_every_phi_type_and_layer_token_is_in_the_vocabulary():
    """`naming.yaml`'s own category names cannot be outside the mechanism vocabulary.

    This is an invariant rather than a list, and it exists because the list version
    failed twice. `gaz` was admitted on 2026-08-12 partly on the grounds that it is
    also a `layer` axis value — the right argument, applied to one word. `tagger`, the
    remaining `layer` value, stayed out; so did `location`, `area`, `id` and `other`
    from `phi_type`, until `en_location_cue` tripped the screener in `port-loop` round 3
    and made it the fifth widening (DESIGN §6.1).

    The argument does not depend on observing a rule name. An axis value is a category
    this project defined, a category name designates a class and not a member, and a
    rule author naming the type a rule targets is doing the ordinary thing. So the
    whole class closes here: add a `phi_type` or a `layer` and this fails until the
    vocabulary follows in the same commit.

    Only these two axes. `corpus`, `detector`, `porting`, `split`, `supervision` and
    `lang` name the experiment rather than what a rule does, they never appear in a rule
    name, and admitting them would put `es-meddocan` in the vocabulary for nothing.
    """
    sys.path.insert(0, ROOT)
    from src.corpora import base

    tokens = set()
    for name in ("phi_type", "layer"):
        values = base.axis(name)
        assert values, f"{name} axis is empty — naming.yaml did not load"
        for value in values:
            tokens.update(str(value).lower().split("_"))

    missing = sorted(t for t in tokens if t not in rs.RULE_ID_VOCAB)
    assert not missing, (
        f"phi_type/layer tokens outside RULE_ID_VOCAB: {missing}. These are "
        "naming.yaml's own category names; add them to the vocabulary in the same "
        "commit that adds the axis value.")


# ─── proposed vocabulary entries (DESIGN §6.1, option 3) ───────────────────
#
# Four widenings in, three of them made with `test_every_current_false_positive_is_covered`
# failing and the mutation harness refusing to run on a red baseline. The proposal lines
# exist so the reviewer judges a category instead of reconstructing one from a count. What
# these tests pin is the part that is easy to lose: it is off by default, because an
# unrecognised token is the best candidate in the file for being a surface form and
# `sniff()`'s message reaches CI logs.

_PROPOSE_FILE = ("rules:\n"
                 "  - rule_id: es:hospital_org\n"
                 "  - rule_id: es:zzqq_widget_cue\n"
                 "  - rule_id: es:widget_pattern\n")


def test_a_proposal_names_the_token_and_the_rule_it_came_from():
    """The two things the reviewer needs: which word, and what reached for it."""
    got = rs.rule_id_proposals(_PROPOSE_FILE, "es")
    assert got == [("zzqq", "es:zzqq_widget_cue"),
                   ("widget", "es:zzqq_widget_cue"),
                   ("widget", "es:widget_pattern")]


def test_one_token_in_two_rules_is_two_proposals():
    """Deduplicated on the pair, not on the token.

    Which rule reached for a word is the evidence the category judgement runs on:
    `widget` inside a `_cue` name and inside a `_pattern` name are different claims
    about what box it belongs in. Collapsing to one line would throw that away, and
    the collapse is the obvious tidying.
    """
    got = rs.rule_id_proposals(_PROPOSE_FILE, "es")
    assert [t for t, _ in got].count("widget") == 2
    dup = rs.rule_id_proposals("  - rule_id: es:widget_widget_cue\n", "es")
    assert dup == [("widget", "es:widget_widget_cue")], (
        "the same token twice in one rule is one proposal")


@pytest.mark.parametrize("rule_id", [
    "es:García",              # capitalised — a shape finding
    "es:born_1978",           # a 3+ digit run
    "ko:김철수",               # non-ASCII
])
def test_a_shape_finding_proposes_nothing(rule_id):
    """A shape finding is about the id as a whole and has no offending token.

    Proposing one would invite adding a capitalised surname to the vocabulary as
    though it were a missing mechanism word, which is the one outcome this machinery
    must not make convenient.
    """
    text = f"  - rule_id: {rule_id}\n"
    assert rs.rule_id_findings(text, lang="es"), f"{rule_id} should be a finding"
    assert rs.rule_id_proposals(text, "es") == []


def test_every_proposed_token_is_genuinely_outside_all_three_homes():
    """The proposal and the finding come from one computation (`_rule_id_scan`).

    A second implementation of the lookup would drift, and the drift a reader would
    not notice is a proposal for a token the check does not object to — an entry
    added for nothing, widening the vocabulary on a misreading.
    """
    for token, _ in rs.rule_id_proposals(_PROPOSE_FILE, "es"):
        low = token.lower()
        assert low not in rs.RULE_ID_VOCAB
        assert low not in rs.RULE_ID_ALLOWED_TOKENS
        assert low not in rs.RULE_ID_VOCAB_BY_LANG["es"]
        assert not rs.RULE_ID_CODE_TOKEN.match(low)


def test_a_clean_file_proposes_nothing():
    assert rs.rule_id_proposals("  - rule_id: es:hospital_org\n", "es") == []
    assert rs.format_proposals("rules/es.yaml", "es", []) == []


def test_the_proposal_respects_the_language_layer():
    """`localidad` is Spanish vocabulary now, so it is a proposal only outside `es`.

    The same property `rule_id_findings` has, asserted on the proposals because they
    are what a reviewer acts on: proposing a token the file's own layer already holds
    would send someone to add a duplicate entry.
    """
    text = "  - rule_id: localidad_cue\n"
    assert rs.rule_id_proposals(text, "es") == []
    assert rs.rule_id_proposals(text, "de") == [("localidad", "localidad_cue")]


def test_proposals_are_off_by_default_and_printed_on_request(tmp_path):
    """The correction option 3 needed, pinned at the CLI.

    `sniff()` refuses to quote a `rule_id` because it may be a surface form and its
    message reaches terminals and CI logs (CLAUDE.md). An unrecognised token is the
    likeliest surface form in the file, so the proposal lines cannot be default
    output. Both halves are asserted: silence without the flag, and the token itself
    with it.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "es.yaml").write_text(_PROPOSE_FILE, encoding="utf-8")
    script = os.path.join(ROOT, "tools", "release_screen.py")
    empty = tmp_path / "allow.json"
    empty.write_text('{"version": 1, "entries": []}\n', encoding="utf-8")

    def run(*extra):
        return subprocess.run(
            [sys.executable, script, "--root", str(tmp_path),
             "--allowlist", str(empty), *extra],
            capture_output=True, text=True).stdout

    quiet = run()
    assert "zzqq" not in quiet, "the default report quoted an unrecognised token"
    assert "PROPOSED" not in quiet
    loud = run("--propose")
    assert "zzqq" in loud and "es:zzqq_widget_cue" in loud
    assert "rules/es.yaml" in loud
    # The banner is the reason the flag exists, so it is not optional decoration.
    assert "CI log" in loud


def test_propose_says_so_when_there_is_nothing_to_propose(tmp_path):
    """Zero is a measurement. A section that vanishes reads as a section that failed."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "es.yaml").write_text("rules:\n  - rule_id: es:hospital_org\n",
                                   encoding="utf-8")
    empty = tmp_path / "allow.json"
    empty.write_text('{"version": 1, "entries": []}\n', encoding="utf-8")
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "release_screen.py"),
         "--root", str(tmp_path), "--allowlist", str(empty), "--propose"],
        capture_output=True, text=True).stdout
    assert "nothing to propose" in out


def test_real_fetch_scripts_are_clean_under_forced_sniff():
    """The committed acquisition scripts must survive the stricter sniff.

    They are exceptions, so they are now read unconditionally. If one of them
    trips the sniffer the screener exits 1 on a clean tree, which trains people to
    ignore it.
    """
    for name in ("fetch_meddocan.sh", "fetch_grascco.sh"):
        p = os.path.join(ROOT, "data", "acquire", name)
        if not os.path.exists(p):
            pytest.skip(f"{name} not present")
        assert rs.sniff(p, force=True) is None


# ─── the extension filter is no longer load-bearing ────────────────────────

def test_sh_is_in_text_ext():
    """.sh was the gap. Its absence is what made the bypass invisible."""
    assert ".sh" in rs.TEXT_EXT


def test_exception_paths_are_sniffed_regardless_of_extension(tmp_path):
    """force=True must defeat the extension filter for an unlisted type."""
    assert rs.sniff("x.unknownext", blob=NOTE.encode()) is None
    assert rs.sniff("x.unknownext", blob=NOTE.encode(), force=True) is not None


def test_is_exception_marks_exactly_the_exception_paths():
    assert rs.is_exception("data/acquire/fetch_meddocan.sh")
    assert rs.is_exception("data/README.md")
    assert not rs.is_exception("data/acquire/disguised.sh")


# ─── the seal is never downgraded ──────────────────────────────────────────

def test_sealed_content_is_never_read():
    """deny() must be decidable from the path alone for sealed/.

    Reading the test fold to classify it would break the seal that CLAUDE.md
    forbids touching.
    """
    assert rs.deny("sealed/ko-surro/any.txt")


def _sealed_repo(tmp_path):
    """A git repository with a gitignored sealed fold. Nothing is ever read."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("sealed/\n", encoding="utf-8")
    s = tmp_path / "sealed" / "ko-surro" / "test"
    s.mkdir(parents=True)
    (s / "leak.txt").write_text("whatever\n", encoding="utf-8")
    return "sealed/ko-surro/test/leak.txt"


def test_sealed_is_reported_on_its_own_line_not_as_blocked(tmp_path):
    """A sealed fold git cannot see is SEALED: expected, and not a commit blocker.

    Previously it was reported as BLOCKED, which was defensible in isolation and
    unusable in practice: 'BLOCKED must be 0' became permanently false the moment a
    fold was sealed, and a gate that can never pass stops being read. The reminder
    survives as its own line; what it no longer does is block every commit.
    """
    path = _sealed_repo(tmp_path)
    blocked, sealed, quarantined, _, _ = rs.screen_tree(str(tmp_path))
    assert path in sealed
    assert path not in blocked
    assert path not in quarantined, (
        "sealed/ must not be folded into the corpus count either — the point of the "
        "separate line is that it stays visible"
    )
    assert not blocked


def test_a_staged_sealed_file_is_blocked_not_sealed(tmp_path):
    """The real violation: the fold is on its way into a commit.

    `git add -f` is what it takes, and it is the one case where the reassuring line
    would be the wrong one. This is the assertion that has to hold; which of the two
    checks inside `visible()` produces it is not this test's business.
    """
    path = _sealed_repo(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "--", path], check=True)

    blocked, sealed, _, _, _ = rs.screen_tree(str(tmp_path))
    assert path in blocked, "a staged sealed file must block the commit"
    assert path not in sealed


def test_git_tracked_sees_a_staged_sealed_file(tmp_path):
    """The index question, asked on its own.

    `screen_tree` currently reaches the same verdict twice over — on git 2.54
    check-ignore consults the index too, so a force-added file is already 'visible'
    without this. Tested separately because that is a property of one git version and
    escalation must not depend on it.
    """
    path = _sealed_repo(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "--", path], check=True)
    assert path in rs.git_tracked([path], str(tmp_path))


def test_git_tracked_is_empty_for_an_unstaged_file(tmp_path):
    path = _sealed_repo(tmp_path)
    assert rs.git_tracked([path], str(tmp_path)) == set()


def test_sealed_exits_zero_and_blocked_exits_one(tmp_path):
    """End to end through the CLI, because the exit code is what CI reads."""
    _sealed_repo(tmp_path)
    script = os.path.join(ROOT, "tools", "release_screen.py")
    clean = subprocess.run([sys.executable, script, "--root", str(tmp_path)],
                           capture_output=True, text=True)
    assert clean.returncode == 0, clean.stdout
    assert "SEALED (expected, exit 0) : 1" in clean.stdout
    assert "BLOCKED by path rule      : 0" in clean.stdout

    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "--",
                    "sealed/ko-surro/test/leak.txt"], check=True)
    staged = subprocess.run([sys.executable, script, "--root", str(tmp_path)],
                            capture_output=True, text=True)
    assert staged.returncode == 1, staged.stdout
    assert "SEALED (expected, exit 0) : 0" in staged.stdout


def test_the_sealed_line_is_printed_even_when_zero(tmp_path):
    """Zero-suppressing it would make its absence ambiguous.

    A run with no SEALED line could mean 'nothing sealed' or 'this screener predates
    the seal'. The line is always there so the reader knows which.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    script = os.path.join(ROOT, "tools", "release_screen.py")
    out = subprocess.run([sys.executable, script, "--root", str(tmp_path)],
                         capture_output=True, text=True).stdout
    assert "SEALED (expected, exit 0) : 0" in out


def test_gitignored_corpus_is_quarantined_not_blocked(tmp_path):
    """A downloaded corpus git cannot see is expected, and must not block a commit."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("data/*\n", encoding="utf-8")
    d = tmp_path / "data" / "raw" / "es-meddocan"
    d.mkdir(parents=True)
    (d / "doc.txt").write_text(NOTE, encoding="utf-8")

    blocked, sealed, quarantined, _, _ = rs.screen_tree(str(tmp_path))
    assert "data/raw/es-meddocan/doc.txt" in quarantined
    assert not blocked
    assert not sealed


def test_a_staged_corpus_file_is_blocked(tmp_path):
    """The same index-beats-ignore rule, on the path that is not sealed.

    Kept separate from the sealed case so that a change to the seal reporting cannot
    quietly weaken this one — they share `visible()` and must both keep holding.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("data/*\n", encoding="utf-8")
    d = tmp_path / "data" / "raw" / "es-meddocan"
    d.mkdir(parents=True)
    (d / "doc.txt").write_text(NOTE, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "--",
                    "data/raw/es-meddocan/doc.txt"], check=True)

    blocked, _, quarantined, _, _ = rs.screen_tree(str(tmp_path))
    assert "data/raw/es-meddocan/doc.txt" in blocked
    assert not quarantined


# ─── gitignore and the screener must agree ─────────────────────────────────

#: One representative path per deny pattern, plus the variants that a single
#: hand-written .gitignore line is most likely to miss. Keyed by the pattern itself so
#: that adding a deny rule without a sample fails `test_every_deny_pattern_has_a_sample`
#: below — a sample list maintained by hand goes stale in the direction that passes.
#:
#: Corpus-agnostic on purpose, and this is the whole lesson of the gap it was written
#: for. The .gitignore said `*ko_tagged*` and `*ko_surrogate*` while DENY_PATTERNS said
#: `_tagged` and `surrogate`, so `es_tagged.jsonl` was denied and git could see it. The
#: sample corpus below is a letter, not a real corpus id: a sample naming es-meddocan
#: would pass on a gitignore line naming es-meddocan.
DENY_SAMPLES = {
    "^" + rs.SEALED_PREFIX: ["sealed/c/test/note.txt"],
    r"^data/": ["data/x.jsonl", "data/c/notes.csv"],
    r"(^|/)data/(source|derived|raw|interim)/": [
        "sub/data/source/a.txt", "sub/data/derived/a.txt",
        # raw/ and interim/ were in the deny rule and not in .gitignore.
        "sub/data/raw/a.txt", "sub/data/interim/a.txt",
    ],
    r"(^|/)[^/]*surrogate[^/]*\.(jsonl|json|csv|tsv|txt)$": [
        "out/c_surrogate.jsonl", "out/c_surrogate.csv", "out/c_surrogate.txt",
        "out/surrogate_registry.json",
    ],
    r"(^|/)[^/]*value_map[^/]*\.(jsonl|json|csv|tsv)$": [
        "out/value_map.json", "out/c_value_map.csv", "results/value_map_iter1.jsonl",
    ],
    r"(^|/)[^/]*_tagged[^/]*\.(jsonl|json|csv|tsv|txt)$": [
        "out/c_tagged.jsonl", "out/de_tagged.csv", "out/es_tagged.txt",
    ],
    r"(^|/)[^/]*_with_text[^/]*": [
        "out/spans_with_text.jsonl", "out/c_with_text.json", "out/preds_with_text.md",
    ],
    r"(^|/)[^/]*_raw_llm[^/]*": [
        # The old line was `**/*_raw_llm*.jsonl` — extension-bound, so .txt escaped.
        "out/c_raw_llm.jsonl", "out/c_raw_llm.txt", "results/x_raw_llm_iter2.json",
    ],
    r"(^|/)call_logs?/": [
        "results/call_logs/a.jsonl", "results/call_log/a.jsonl", "x/y/call_logs/b.json",
    ],
    r"(^|/)raw_responses[^/]*": [
        "results/raw_responses_iter1.json", "out/raw_responses/a.txt",
    ],
    r"(^|/)critic_log\.jsonl$": [
        "results/critic_log.jsonl", "results/a/b/critic_log.jsonl",
    ],
    r"(^|/)agent_calls\.jsonl$": [
        # The arm that produced the first one of these was port-oneshot, 2026-08-11:
        # denied since the rule was written, gitignored by nothing, and BLOCKED the
        # moment the file appeared. See docs/notes/arm-port-oneshot-es.md.
        "results/a/b/c/d/agent_calls.jsonl", "agent_calls.jsonl",
    ],
    r"(^|/)audit_report\.json$": [
        "results/a/b/c/d/iter3/audit_report.json", "audit_report.json",
    ],
    r"(^|/)errors\.jsonl$": [
        "results/a/b/c/d/iter3/errors.jsonl", "errors.jsonl",
    ],
    # `paths.armlexicon`. Anchored at the results tree rather than written `**/lexicons/`,
    # so the sample is four components deep and the .gitignore line has to be too — a
    # hand-written `**/lexicons/` would pass this sample and also swallow the top-level
    # human path, which `test_the_hand_written_lexicon_path_is_not_denied` refuses.
    r"^results/[^/]+/[^/]+/[^/]+/[^/]+/lexicons/": [
        "results/a/b/c/d/lexicons/e/terms.txt",
    ],
}

#: The deny patterns that must NOT be gitignored, and the test that says why is
#: `test_the_filled_prompt_patterns_are_not_gitignored`. Listed here so the sync test
#: below covers every pattern and skips these by name rather than by falling through.
DENY_NOT_IGNORED = {
    r"(^|/)prompts?/(filled|rendered)/",
    r"(^|/)[^/]*\.(filled|rendered)\.[^/]+$",
    r"(^|/)[^/]*_(filled|rendered)_prompt[^/]*",
    r"(^|/)[^/]*prompt[^/]*_iter[0-9]+[^/]*",
}


@pytest.fixture(scope="module")
def gitignore_probe(tmp_path_factory):
    """Ask the repository's .gitignore about a path, in a tree with nothing else in it.

    A scratch repository holding a copy of the real `.gitignore` and no files. Asking
    git in the working copy instead looks simpler and is not: `git check-ignore` needs
    to resolve the leading directories, so the answer depends on what happens to be on
    disk. Under the mutation harness (`tests/mutations/run.py`) `sealed/` is a symlink
    into the real tree, and git refuses any path beyond a symlink with a fatal error —
    which this test then reads as "not ignored" and reports as a leak that is not there.

    The rules are what is under test, so the probe holds the rules and nothing else.
    """
    d = tmp_path_factory.mktemp("gitignore_probe")
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    shutil.copyfile(os.path.join(ROOT, ".gitignore"), os.path.join(d, ".gitignore"))

    def ignored(path):
        r = subprocess.run(
            ["git", "-C", str(d), "check-ignore", "-q", "--no-index", "--", path],
            capture_output=True)
        assert r.returncode in (0, 1), (
            f"git could not answer for {path!r}: {r.stderr.decode().strip()}")
        return r.returncode == 0

    return ignored


def test_every_deny_pattern_has_a_sample():
    """A new deny rule must come with samples or be declared deliberately visible.

    Without this the sync test below is only as good as whoever last remembered to
    extend it, and a forgotten pattern shows up as a pass.
    """
    covered = set(DENY_SAMPLES) | DENY_NOT_IGNORED
    missing = [p for p in rs.DENY_PATTERNS if p not in covered]
    assert not missing, (
        f"deny patterns with no gitignore sample and no exemption: {missing}. Add "
        "samples to DENY_SAMPLES, or add the pattern to DENY_NOT_IGNORED with the "
        "reason it must stay visible to git."
    )
    stale = [p for p in covered if p not in rs.DENY_PATTERNS]
    assert not stale, f"samples for deny patterns that no longer exist: {stale}"


def test_the_samples_match_the_pattern_they_are_filed_under():
    """Guards the sync test's premise: a sample that matches nothing proves nothing.

    A typo'd sample would be denied by no rule, be ignored by no rule, and pass the
    sync test by agreeing with it — vacuously.
    """
    for pattern, paths in DENY_SAMPLES.items():
        for path in paths:
            assert re.search(pattern, path), f"{path!r} does not match {pattern!r}"
            assert rs.deny(path), f"{path!r} is not denied by the screener"


@pytest.mark.parametrize("path", sorted(
    p for paths in DENY_SAMPLES.values() for p in paths))
def test_every_deny_listed_path_is_also_gitignored(path, gitignore_probe):
    """A deny rule with no .gitignore counterpart is half a convention.

    The file still cannot be committed — it reports BLOCKED, which gates the commit —
    but it sits in the working tree where one `git add` reaches it, and BLOCKED is a
    number someone has to read. Being gitignored as well means git will not stage it
    by accident in the first place, and the screener reports it as Quarantined.

    Asked of the rules rather than of this disk — see `gitignore_probe`. The four
    filled-prompt patterns are the deliberate exception, excluded by DENY_NOT_IGNORED.
    """
    assert gitignore_probe(path), (
        f"{path} is denied by tools/release_screen.py and is not gitignored. Add a "
        "pattern to .gitignore's deny-list section — matching the deny rule's shape, "
        "not this one filename."
    )


@pytest.mark.parametrize("path", [
    "lexicons/es/terms.txt",
    "lexicons/ko/terms.txt",
    "profiles/es-meddocan.raw.json",
    "mappings/es-meddocan.yaml",
])
def test_the_hand_written_auxiliary_inputs_are_not_denied(path, gitignore_probe):
    """The agent-scoped rule must not reach the hand-written paths it was added beside.

    `paths.armlexicon` is denied and `paths.lexicon` is not, and the two differ only in
    a prefix — so the rule's anchor is the whole of the distinction. Written `**/lexicons/`
    it would also catch the top-level directory, which is the position a person writes and
    which `src/rules.py` reads when a rule declares a term list by name: the loader would
    go on working while the input it reads became unpublishable and, once gitignored, also
    unstageable. `profiles/` is the same failure already realised — files are tracked
    there today, so an ignore pattern reaching them would make a committed input invisible
    to a fresh clone (`test_no_tracked_file_is_gitignored` is the other half of that).

    Asserted over both halves because they fail independently: a deny pattern with no
    gitignore line and a gitignore line with no deny pattern are each one edit away.
    """
    assert not rs.deny(path), (
        f"{path} is a hand-written auxiliary input and the screener denies it. The "
        "agent-scoped rule is meant to be anchored at `^results/`; a `(^|/)lexicons/` "
        "shape reaches the position a person writes and that src/rules.py reads."
    )
    assert not gitignore_probe(path), (
        f"{path} is gitignored. The agent-scoped ignore line must stay anchored under "
        "results/ — the hand-written inputs are committed, or will be."
    )


def test_no_tracked_file_is_gitignored():
    """The other direction: a new ignore pattern must not shadow a committed file.

    A tracked file that is also ignored keeps working — the index wins — so the
    mistake is invisible until someone re-clones or the file is removed and cannot be
    re-added. Committed rule files and result records live under paths the deny-list
    section's globs come close to.
    """
    out = subprocess.run(
        ["git", "-C", ROOT, "ls-files", "--cached", "--ignored", "--exclude-standard"],
        capture_output=True, text=True).stdout.split()
    assert not out, f"tracked files matched by a .gitignore pattern: {out}"


def test_history_reports_blobs_not_trees():
    """screen_history() must not report tree objects.

    `git rev-list --objects` names a tree by its directory path, so the tree for
    `data/acquire` matched `^data/` and triggered the "do NOT make this repository
    public" warning even though the only committed files there are the fetch
    scripts. Every reported sha must be a blob.
    """
    for sha, path in rs.screen_history():
        kind = subprocess.run(["git", "-C", ROOT, "cat-file", "-t", sha],
                              capture_output=True, text=True).stdout.strip()
        assert kind == "blob", f"{path} reported as {kind}, not a blob"


def test_this_repository_has_no_denied_blobs_in_history():
    """The live invariant: nothing DUA-restricted was ever committed here."""
    assert rs.screen_history() == []


@pytest.mark.parametrize("path,should_be_ignored", [
    ("data/acquire/fetch_meddocan.sh", False),
    ("data/acquire/disguised.sh", True),
    ("data/acquire/notes.sh", True),
    ("data/README.md", False),
])
def test_gitignore_matches_deny_exceptions(path, should_be_ignored):
    """.gitignore and DENY_EXCEPTIONS encode the same whitelist in two languages.

    They drift silently: the .sh bypass existed in both at once. Checked against
    the real repository rules with `git check-ignore --no-index`, so the file need
    not exist on disk.
    """
    r = subprocess.run(
        ["git", "-C", ROOT, "check-ignore", "-q", "--no-index", "--", path],
        capture_output=True)
    assert (r.returncode == 0) is should_be_ignored, (
        f"{path}: git ignored={r.returncode == 0}, expected {should_be_ignored}")
    assert rs.deny(path) is should_be_ignored, (
        f"{path}: screener denied={rs.deny(path)}, expected {should_be_ignored}")


# ─── the known-false-positive allowlist ────────────────────────────────────
# Five files trip the sniffer on every run for reasons that are not note text.
# Printed in full they were five permanent lines nobody read, so a sixth would have
# arrived among them unnoticed. The allowlist turns the expected ones into a count
# and prints only what is new — the same treatment BLOCKED got with SEALED.


def _allowlist(tmp_path, entries):
    p = tmp_path / "tools" / "screen_allowlist.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
    return str(p)


GOOD = {"path": "docs/x.md", "sniff": "Korean prose",
        "why": "a reason long enough to be evaluated later"}


def test_the_real_allowlist_loads_and_validates():
    """The committed list must satisfy its own rules, or the screener refuses to run.

    Both lists, and each against its own required fields: a false positive says why it
    is harmless, an acknowledged violation says why it is real and when it goes. The
    path rules are asserted over both together, because that is the one guarantee that
    must not differ between them.
    """
    entries = rs.load_allowlist()
    assert entries
    for path, entry in entries.items():
        assert entry["sniff"]
        assert not rs.deny(path)
        if entry["kind"] == rs.ACKNOWLEDGED:
            assert entry["why_real"] and entry["fixed_when"]
            assert "why" not in entry
        else:
            assert entry["kind"] == rs.FALSE_POSITIVE
            assert entry["why"]


def test_the_real_allowlist_has_no_stale_entries():
    """Every entry names a file that exists. A pruned list is the point of having one."""
    missing = [p for p in rs.load_allowlist()
               if not os.path.exists(os.path.join(ROOT, p))]
    assert not missing, f"stale allowlist entries: {missing}"


def test_every_current_false_positive_is_covered():
    """The live invariant: this repository screens with zero unexpected hits.

    If a change introduces a new sniffer hit, this fails — which is the whole purpose
    of the allowlist. Adding the file to the list is a deliberate, reviewable act.

    "Covered" means covered by one of the two lists, and an acknowledged hit satisfies
    this test without being harmless. That is deliberate: a permanently-failing test is
    not a stricter project, it is an unmeasurable one — `tests/mutations/run.py` aborts
    on a non-green baseline, so the two round-4/round-5 rule_id violations were taking
    170 mutations down with them. The claim this test makes is "every current hit has
    been looked at and written down", not "every current hit is fine".
    """
    _, _, _, suspect, _ = rs.screen_tree(ROOT)
    _, _, unexpected, _, _ = rs.partition_suspect(suspect, rs.load_allowlist(), ROOT)
    assert not unexpected, (
        "unexpected sniffer hits: "
        f"{[(p, why) for p, why, _ in unexpected]}")


def test_every_acknowledged_entry_is_a_hit_that_actually_happens():
    """The other direction, and the one that keeps the list from going stale quietly.

    `test_the_real_allowlist_has_no_stale_entries` catches an entry whose *file* is
    gone. This catches an entry whose file is present and no longer trips the sniffer
    the way the entry says — a violation that was fixed, or a `sniff` pin that has
    drifted off the finding it was written for. Either way the entry is now excusing
    nothing, and an acknowledged violation nobody can reproduce is indistinguishable
    from one nobody fixed.
    """
    _, _, _, suspect, _ = rs.screen_tree(ROOT)
    _, acknowledged, _, _, _ = rs.partition_suspect(
        suspect, rs.load_allowlist(), ROOT)
    listed = {p for p, e in rs.load_allowlist().items()
              if e["kind"] == rs.ACKNOWLEDGED}
    matched = {p for p, _, _ in acknowledged}
    assert listed == matched, (
        f"acknowledged entries that no longer match a hit: {sorted(listed - matched)}")


# ─── what the allowlist may not contain ────────────────────────────────────


@pytest.mark.parametrize("path", [
    "sealed/es-meddocan/test/brat/doc.txt",
    "sealed/anything.txt",
    "data/raw/es-meddocan/note.txt",
    "data/derived/spans.jsonl",
    "data/README.md",
])
def test_a_corpus_or_sealed_entry_is_refused(tmp_path, path):
    """The one thing this list must never be able to do.

    An allowlist that can silence a sniffer hit on a corpus or sealed path is a way
    to publish note text with a one-line diff to a JSON file. `data/README.md` is
    refused too, despite being publishable by path: it is the single file published
    out of a denied prefix, so it is the last one that should also be exempt from the
    content check.
    """
    p = _allowlist(tmp_path, [{**GOOD, "path": path}])
    with pytest.raises(rs.AllowlistError, match="data/|sealed/|denied"):
        rs.load_allowlist(p)


@pytest.mark.parametrize("path", [
    "docs/*.md",
    "docs/",
    "config/?.yaml",
    "docs/[a-z].md",
])
def test_a_pattern_entry_is_refused(tmp_path, path):
    """Literal paths only — the `data/acquire/*.sh` lesson, restated.

    That whitelist was keyed on an extension and published a file holding a clinical
    note header. Any entry broader than a filename stops naming what it permits.
    """
    p = _allowlist(tmp_path, [{**GOOD, "path": path}])
    with pytest.raises(rs.AllowlistError, match="pattern or a directory"):
        rs.load_allowlist(p)


def test_an_entry_without_a_reason_is_refused(tmp_path):
    p = _allowlist(tmp_path, [{"path": "docs/x.md", "sniff": "Korean prose",
                               "why": "noise"}])
    with pytest.raises(rs.AllowlistError, match="why"):
        rs.load_allowlist(p)


def test_an_entry_without_a_sniff_kind_is_refused(tmp_path):
    p = _allowlist(tmp_path, [{"path": "docs/x.md", "why": GOOD["why"]}])
    with pytest.raises(rs.AllowlistError, match="sniff"):
        rs.load_allowlist(p)


ACK = {"path": "docs/x.md", "sniff": "Korean prose",
       "why_real": "the sniffer is right and this really is a violation",
       "fixed_when": "the arm that produced it is superseded by another"}


def _acklist(tmp_path, acknowledged, entries=()):
    p = tmp_path / "ack.json"
    p.write_text(json.dumps({"version": 1, "entries": list(entries),
                             "acknowledged": list(acknowledged)}), encoding="utf-8")
    return str(p)


def test_an_acknowledged_entry_loads_and_carries_its_kind(tmp_path):
    entries = rs.load_allowlist(_acklist(tmp_path, [ACK]))
    assert entries["docs/x.md"]["kind"] == rs.ACKNOWLEDGED


@pytest.mark.parametrize("missing", ["why_real", "fixed_when"])
def test_an_acknowledged_entry_needs_both_fields(tmp_path, missing):
    """Two fields, both mandatory, and the reasons are different.

    Without `why_real` the entry is indistinguishable from a false positive, which
    defeats the point of having a second list. Without `fixed_when` it is a permanent
    exemption labelled as a temporary one, which is the only way this list can do harm.
    """
    entry = {k: v for k, v in ACK.items() if k != missing}
    with pytest.raises(rs.AllowlistError, match=missing):
        rs.load_allowlist(_acklist(tmp_path, [entry]))


def test_an_acknowledged_entry_may_not_carry_a_why(tmp_path):
    """Refused rather than ignored. The way this list gets misused is by copy-pasting an
    entry across from `entries`, where `why` would be silently dropped and the two
    required fields silently absent."""
    with pytest.raises(rs.AllowlistError, match="acknowledged entry takes"):
        rs.load_allowlist(_acklist(tmp_path, [{**ACK, "why": GOOD["why"]}]))


@pytest.mark.parametrize("path", [
    "sealed/es-meddocan/test/brat/doc.txt",
    "data/raw/es-meddocan/note.txt",
    "data/README.md",
    "docs/*.md",
])
def test_the_acknowledged_list_obeys_the_same_path_rules(tmp_path, path):
    """The sharper case, because acknowledging is the one act that concedes the sniffer
    was right — so it is the first thing an author wanting a quiet run reaches for. It
    still cannot name a corpus path, a sealed path or a pattern: this list can only ever
    describe a file the path rules already publish."""
    with pytest.raises(rs.AllowlistError,
                       match="data/|sealed/|denied|pattern or a directory"):
        rs.load_allowlist(_acklist(tmp_path, [{**ACK, "path": path}]))


def test_a_path_in_both_lists_is_refused(tmp_path):
    """A file cannot be both a false positive and a real violation. Refused rather than
    resolved by precedence — a precedence rule would silently pick one of two
    contradictory statements about the same file."""
    with pytest.raises(rs.AllowlistError, match="listed twice"):
        rs.load_allowlist(_acklist(tmp_path, [ACK], entries=[GOOD]))


def test_a_duplicate_path_within_one_list_is_refused(tmp_path):
    """Previously the second entry silently overwrote the first, so a stricter `sniff`
    could be cancelled by a looser copy further down the file."""
    with pytest.raises(rs.AllowlistError, match="listed twice"):
        rs.load_allowlist(_acklist(tmp_path, [], entries=[GOOD, GOOD]))


def test_a_missing_or_broken_allowlist_is_refused(tmp_path):
    """Not "carry on without it": every known hit would then read as new."""
    with pytest.raises(rs.AllowlistError, match="missing"):
        rs.load_allowlist(str(tmp_path / "nope.json"))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(rs.AllowlistError, match="valid JSON"):
        rs.load_allowlist(str(bad))


def test_a_rejected_allowlist_screens_nothing(tmp_path):
    """Exit 2 and no report. "SUSPECT 5 (all known)" from a rejected list would be
    the most misleading line this tool could print."""
    p = _allowlist(tmp_path, [{**GOOD, "path": "sealed/leak.txt"}])
    script = os.path.join(ROOT, "tools", "release_screen.py")
    r = subprocess.run([sys.executable, script, "--root", str(tmp_path),
                        "--allowlist", p], capture_output=True, text=True)
    assert r.returncode == 2
    assert "ALLOWLIST REJECTED" in r.stderr
    assert "SUSPECT" not in r.stdout


# ─── how hits are partitioned ──────────────────────────────────────────────


def test_a_known_hit_is_counted_not_printed(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text("x", encoding="utf-8")
    known, acknowledged, unexpected, _, _ = rs.partition_suspect(
        [("docs/x.md", "Korean prose (9 runs)")], {"docs/x.md": GOOD}, str(tmp_path))
    assert known and not unexpected
    assert not acknowledged, "an entry with no `kind` is a false positive, as it was"


def test_a_different_sniff_kind_on_a_known_file_is_unexpected(tmp_path):
    """The subtle one. A file excused for Korean prose that starts matching the
    clinical-header pattern is a new fact about it, and being previously excused for
    another reason is not a reason to excuse this."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    known, _, unexpected, _, _ = rs.partition_suspect(
        [("docs/x.md", "clinical note header")], {"docs/x.md": GOOD}, str(tmp_path))
    assert not known
    assert unexpected[0][0] == "docs/x.md"
    assert unexpected[0][2] is GOOD, "the report should say what it WAS excused for"


def test_an_unlisted_visible_hit_is_unexpected(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _, _, unexpected, _, _ = rs.partition_suspect(
        [("docs/new.md", "clinical note header")], {}, str(tmp_path))
    assert len(unexpected) == 1


def test_a_gitignored_hit_is_unpublishable_not_unexpected(tmp_path):
    """Machine-local files (data_paths.local.yaml, editor settings) cannot be pushed.

    Counted rather than allowlisted on purpose: an entry for one would read as stale
    on every other machine, which is the noise this change removes.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("local.yaml\n", encoding="utf-8")
    (tmp_path / "local.yaml").write_text("x", encoding="utf-8")
    _, _, unexpected, unpublishable, _ = rs.partition_suspect(
        [("local.yaml", "Korean prose (9 runs)")], {}, str(tmp_path))
    assert not unexpected
    assert unpublishable == [("local.yaml", "Korean prose (9 runs)")]


def test_an_acknowledged_hit_is_its_own_category_and_does_not_gate(tmp_path):
    """Not `known` — the count would hide it — and not `unexpected` — the exit code
    would make the gate unpassable, which is what took the mutation harness down."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text("x", encoding="utf-8")
    entries = rs.load_allowlist(_acklist(tmp_path, [ACK]))
    known, acknowledged, unexpected, _, _ = rs.partition_suspect(
        [("docs/x.md", "Korean prose (9 runs)")], entries, str(tmp_path))
    assert not known and not unexpected
    assert [p for p, _, _ in acknowledged] == ["docs/x.md"]


def test_a_different_hit_on_an_acknowledged_file_still_gates(tmp_path):
    """The property that stops an entry becoming a blanket pass on its file. The entry
    covers the hit it names; a second, different violation in the same file is still
    UNEXPECTED and still fails."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    entries = rs.load_allowlist(_acklist(tmp_path, [ACK]))
    _, acknowledged, unexpected, _, _ = rs.partition_suspect(
        [("docs/x.md", "clinical note header")], entries, str(tmp_path))
    assert not acknowledged
    assert unexpected[0][0] == "docs/x.md"


def test_an_acknowledged_hit_is_reported_even_when_gitignored(tmp_path):
    """Publishability is not the question here. Someone wrote down that the hit is real,
    and that statement outranks the accident of git not seeing the file today."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("docs/x.md\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text("x", encoding="utf-8")
    entries = rs.load_allowlist(_acklist(tmp_path, [ACK]))
    _, acknowledged, _, unpublishable, _ = rs.partition_suspect(
        [("docs/x.md", "Korean prose (9 runs)")], entries, str(tmp_path))
    assert [p for p, _, _ in acknowledged] == ["docs/x.md"]
    assert not unpublishable


def test_an_acknowledged_violation_is_printed_in_full_and_exits_zero(tmp_path):
    """The reporting inversion, end to end. `known` is counted and not printed; this is
    printed with both fields on every run, because it is a debt line and a debt line
    that stops appearing is a debt nobody pays."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text(NOTE, encoding="utf-8")
    p = _acklist(tmp_path, [{**ACK, "sniff": "clinical note header"}])
    script = os.path.join(ROOT, "tools", "release_screen.py")
    r = subprocess.run([sys.executable, script, "--root", str(tmp_path),
                        "--allowlist", p], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    assert "ACKNOWLEDGED violations   : 1" in r.stdout
    assert ACK["why_real"] in r.stdout
    assert ACK["fixed_when"] in r.stdout


def test_the_acknowledged_line_is_printed_when_there_are_none(tmp_path):
    """Zero is worth printing: it is the difference between "nothing outstanding" and
    "the list is not being consulted", and those look identical if the line vanishes."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    p = _acklist(tmp_path, [])
    script = os.path.join(ROOT, "tools", "release_screen.py")
    r = subprocess.run([sys.executable, script, "--root", str(tmp_path),
                        "--allowlist", p], capture_output=True, text=True)
    assert r.returncode == 0
    assert "ACKNOWLEDGED violations   : 0" in r.stdout


def test_a_stale_entry_is_reported(tmp_path):
    """A list nobody prunes eventually permits something by accident, and a renamed
    file loses its exemption silently — the orphaned entry is the clue."""
    p = _allowlist(tmp_path, [{**GOOD, "path": "docs/gone.md"}])
    _, _, _, _, stale = rs.partition_suspect(
        [], rs.load_allowlist(p), str(tmp_path), p)
    assert stale == ["docs/gone.md"]


def test_staleness_is_not_reported_against_another_tree(tmp_path):
    """Screening a scratch directory must not call every entry stale.

    A check that cries wolf on a temporary tree is one people learn to skip on the
    tree that matters.
    """
    _, _, _, _, stale = rs.partition_suspect([], rs.load_allowlist(), str(tmp_path))
    assert stale == []


def test_stale_entries_do_not_fail_the_run(tmp_path):
    """Exit 0. Failing on staleness would pressure someone into deleting an entry to
    get a green run, and deleting entries is how a list stops describing reality."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    p = _allowlist(tmp_path, [{**GOOD, "path": "docs/gone.md"}])
    script = os.path.join(ROOT, "tools", "release_screen.py")
    r = subprocess.run([sys.executable, script, "--root", str(tmp_path),
                        "--allowlist", p], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    assert "STALE allowlist entries   : 1" in r.stdout


def test_a_clean_tree_exits_zero_and_an_unexpected_hit_exits_one(tmp_path):
    """The habit this protects: a screener that exits 1 on a clean tree gets ignored.

    Before the allowlist it exited 1 on every run because of those five files.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    script = os.path.join(ROOT, "tools", "release_screen.py")
    p = _allowlist(tmp_path, [])

    clean = subprocess.run([sys.executable, script, "--root", str(tmp_path),
                            "--allowlist", p], capture_output=True, text=True)
    assert clean.returncode == 0, clean.stdout

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "leak.md").write_text(NOTE, encoding="utf-8")
    dirty = subprocess.run([sys.executable, script, "--root", str(tmp_path),
                            "--allowlist", p], capture_output=True, text=True)
    assert dirty.returncode == 1
    assert "UNEXPECTED" in dirty.stdout
    assert "docs/leak.md" in dirty.stdout


def test_the_allowlist_reasons_contain_no_korean(tmp_path):
    """The list would otherwise trip HANGUL_PROSE and need an entry for itself."""
    assert rs.sniff(rs.ALLOWLIST, force=True) is None
