"""Regression tests for tools/release_screen.py.

The screener is the last thing standing between DUA-restricted note text and a
public repository, so its holes get tests rather than fixes-and-hope.

The named case here is `disguised.sh`: a file under a denied prefix
(`data/acquire/`) that was published by a path exception keyed on extension, and
whose content was never read because `.sh` was absent from TEXT_EXT. It passed
both layers at once. Both layers were changed; both directions are tested.

    python3 -m pytest tests/ -q
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

    blocked, quarantined, suspect, _ = rs.screen_tree(str(tmp_path))
    named = set(blocked) | {p for p, _ in suspect} | set(quarantined)
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
])
def test_denied_paths(path):
    assert rs.deny(path)


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


def test_sealed_stays_blocked_even_when_gitignored(tmp_path):
    """sealed/ is the one denied prefix that is not downgraded to quarantined."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("sealed/\n", encoding="utf-8")
    s = tmp_path / "sealed" / "ko-surro"
    s.mkdir(parents=True)
    (s / "leak.txt").write_text("whatever\n", encoding="utf-8")

    blocked, quarantined, _, _ = rs.screen_tree(str(tmp_path))
    assert "sealed/ko-surro/leak.txt" in blocked
    assert "sealed/ko-surro/leak.txt" not in quarantined


def test_gitignored_corpus_is_quarantined_not_blocked(tmp_path):
    """A downloaded corpus git cannot see is expected, and must not block a commit."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("data/*\n", encoding="utf-8")
    d = tmp_path / "data" / "raw" / "es-meddocan"
    d.mkdir(parents=True)
    (d / "doc.txt").write_text(NOTE, encoding="utf-8")

    blocked, quarantined, _, _ = rs.screen_tree(str(tmp_path))
    assert "data/raw/es-meddocan/doc.txt" in quarantined
    assert not blocked


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
