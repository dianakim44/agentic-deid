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
    """The committed list must satisfy its own rules, or the screener refuses to run."""
    entries = rs.load_allowlist()
    assert entries
    for path, entry in entries.items():
        assert entry["sniff"] and entry["why"]
        assert not rs.deny(path)


def test_the_real_allowlist_has_no_stale_entries():
    """Every entry names a file that exists. A pruned list is the point of having one."""
    missing = [p for p in rs.load_allowlist()
               if not os.path.exists(os.path.join(ROOT, p))]
    assert not missing, f"stale allowlist entries: {missing}"


def test_every_current_false_positive_is_covered():
    """The live invariant: this repository screens with zero unexpected hits.

    If a change introduces a new sniffer hit, this fails — which is the whole purpose
    of the allowlist. Adding the file to the list is a deliberate, reviewable act.
    """
    _, _, _, suspect, _ = rs.screen_tree(ROOT)
    _, unexpected, _, _ = rs.partition_suspect(suspect, rs.load_allowlist(), ROOT)
    assert not unexpected, (
        "unexpected sniffer hits: "
        f"{[(p, why) for p, why, _ in unexpected]}")


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
    known, unexpected, _, _ = rs.partition_suspect(
        [("docs/x.md", "Korean prose (9 runs)")], {"docs/x.md": GOOD}, str(tmp_path))
    assert known and not unexpected


def test_a_different_sniff_kind_on_a_known_file_is_unexpected(tmp_path):
    """The subtle one. A file excused for Korean prose that starts matching the
    clinical-header pattern is a new fact about it, and being previously excused for
    another reason is not a reason to excuse this."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    known, unexpected, _, _ = rs.partition_suspect(
        [("docs/x.md", "clinical note header")], {"docs/x.md": GOOD}, str(tmp_path))
    assert not known
    assert unexpected[0][0] == "docs/x.md"
    assert unexpected[0][2] is GOOD, "the report should say what it WAS excused for"


def test_an_unlisted_visible_hit_is_unexpected(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _, unexpected, _, _ = rs.partition_suspect(
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
    _, unexpected, unpublishable, _ = rs.partition_suspect(
        [("local.yaml", "Korean prose (9 runs)")], {}, str(tmp_path))
    assert not unexpected
    assert unpublishable == [("local.yaml", "Korean prose (9 runs)")]


def test_a_stale_entry_is_reported(tmp_path):
    """A list nobody prunes eventually permits something by accident, and a renamed
    file loses its exemption silently — the orphaned entry is the clue."""
    p = _allowlist(tmp_path, [{**GOOD, "path": "docs/gone.md"}])
    _, _, _, stale = rs.partition_suspect(
        [], rs.load_allowlist(p), str(tmp_path), p)
    assert stale == ["docs/gone.md"]


def test_staleness_is_not_reported_against_another_tree(tmp_path):
    """Screening a scratch directory must not call every entry stale.

    A check that cries wolf on a temporary tree is one people learn to skip on the
    tree that matters.
    """
    _, _, _, stale = rs.partition_suspect([], rs.load_allowlist(), str(tmp_path))
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
