#!/usr/bin/env python3
"""Screen a working tree for material that the PhysioNet DUA does not allow to be redistributed.

Run from the repository root before making anything public:

    python3 release_screen.py                 # screen the working tree
    python3 release_screen.py --history       # also screen every blob in git history

Two separate risks are checked. The first is the obvious one: files that hold note text or the
surrogate values themselves. The second is the one people miss: a file deleted today is still in
the git history, and making the repository public exposes the history along with the tip.

Four counts are reported and only two of them gate a commit:

    BLOCKED       denied AND visible to git. Must be 0. Exit status is this number.
    SEALED        the sealed test fold — denied, and git cannot see it. Expected, printed
                  every run, exit 0. A sealed path git CAN see is BLOCKED, not SEALED.
    Quarantined   any other denied path git cannot see, e.g. a downloaded corpus.
    SUSPECT       content sniff. Also gates a commit.

Nothing is deleted. The script reports; the decisions stay with the author.
"""
import argparse, json, os, re, subprocess, sys
from collections import Counter

# Paths that carry note text or generated surrogate values. Never publish.
#
# Corpus-agnostic by design: the rules must hold for a corpus that does not exist yet.
# A pattern naming one corpus ("ko_") silently stops protecting the next one.
DENY_PATTERNS = [
    # ─── 봉인된 test fold ───────────────────────────────────────
    # 무조건 deny. 여기 걸리면 내용을 읽지 않으므로 봉인 규율과 충돌하지 않는다.
    # 보고는 SEALED 줄로 분리한다 (screen_tree 참조). git 에 보이게 되면 BLOCKED.
    r"^sealed/",

    # ─── 코퍼스 데이터 전체 ─────────────────────────────────────
    # 취득 스크립트와 문서만 예외 (DENY_EXCEPTIONS).
    r"^data/",
    r"(^|/)data/(source|derived|raw|interim)/",   # 하위 저장소에 중첩된 경우

    # ─── 원문 텍스트를 담은 파생물 (경로 무관) ──────────────────
    r"(^|/)[^/]*surrogate[^/]*\.(jsonl|json|csv|tsv|txt)$",
    r"(^|/)[^/]*value_map[^/]*\.(jsonl|json|csv|tsv)$",
    r"(^|/)[^/]*_tagged[^/]*\.(jsonl|json|csv|tsv|txt)$",
    r"(^|/)[^/]*_with_text[^/]*",
    r"(^|/)[^/]*_raw_llm[^/]*",

    # ─── 모델 호출 로그는 노트를 그대로 인용한다 ────────────────
    r"(^|/)call_logs?/",
    r"(^|/)raw_responses[^/]*",
    r"(^|/)critic_log\.jsonl$",
    r"(^|/)agent_calls\.jsonl$",           # 에이전트 프롬프트에 dev 원문이 들어간다
]

# The only things under a denied data path that may be published: how to obtain the
# corpus, and the licence terms. No note text, no surrogate values.
#
# Filename whitelist, not an extension pass. An earlier version allowed
# `data/acquire/*.sh` and `data/[^/]*.(py|sh)`, which meant ANY file with those
# extensions was publishable regardless of content — `data/acquire/disguised.sh`
# holding a clinical note header was reported clean. Every entry here must name the
# file, not just its type. See tests/test_release_screen.py.
DENY_EXCEPTIONS = [
    r"^data/README\.md$",
    r"^data/[^/]+/README\.md$",            # 코퍼스별 취득 메모
    r"^data/acquire/fetch_[^/]+\.sh$",     # 취득 스크립트, 이름까지 고정
]

# Aggregates, code and public reference material. Safe to publish.
# Result paths follow config/naming.yaml: results/{corpus}/{detector}/{supervision}/{porting}/.
ALLOW_HINTS = [
    "src/", "docs/", "config/", "refs/", "prompts/",
    "rules/", "mappings/", "profiles/", "splits/",
    "data/README.md",
]

# Aggregate result files that carry no source text. Offsets, types and scores only.
ALLOW_PATTERNS = [
    r"^results/[^/]+/[^/]+/[^/]+/[^/]+/metrics\.json$",
    r"^results/[^/]+/[^/]+/[^/]+/[^/]+/spans\.jsonl$",
    r"^results/sealed_eval_log\.md$",
]

# Hangul run long enough to be prose rather than a label.
HANGUL_PROSE = re.compile(r"[가-힣]{2,}[ ,.·][가-힣]{2,}[ ,.·][가-힣]{2,}")

# 실제 노트에는 이 마커가 수십 개 나온다. docstring 예시는 한두 개다. 횟수로 가른다.
NOTE_MARKER = re.compile(r"\[\*\*[^\]*]{1,40}\*\*\]")
NOTE_MARKER_MIN = 5
CLINICAL_HEADER = re.compile(
    r"Admission Date|Discharge Date|CHIEF COMPLAINT|HISTORY OF PRESENT", re.I)

COMMENT_OR_DOCSTRING = re.compile(
    r'("""|\'\'\')(?:.|\n)*?\1'      # docstring
    r'|^[ \t]*#.*$'                   # 줄 전체 주석
    r'|#[^\n]*$',                     # 줄 끝 주석
    re.M)

TEXT_EXT = {".json", ".jsonl", ".csv", ".tsv", ".md", ".txt",
            ".py", ".sh", ".yaml", ".yml"}


def deny(path):
    if any(re.search(p, path) for p in DENY_EXCEPTIONS):
        return False
    return any(re.search(p, path) for p in DENY_PATTERNS)


def is_exception(path):
    """True for a path the deny rules deliberately let through.

    These are the files the screener is least allowed to be wrong about: they sit
    under a denied prefix and are published anyway. Their content is sniffed
    unconditionally, extension ignored.
    """
    return any(re.search(p, path) for p in DENY_EXCEPTIONS)


def strip_code_prose(text):
    """파이썬 소스에서 주석과 docstring을 지운다. 한국어 설명이 여기 들어 있다."""
    return COMMENT_OR_DOCSTRING.sub(" ", text)


def sniff(path, blob=None, force=False):
    """Look for note text inside a file. `force` skips the extension filter.

    The extension filter is a speed optimisation, and it was also a hole: a file
    type absent from TEXT_EXT was never opened, so a denied-path exception with an
    unlisted extension passed both the path rules and the content rules. Anything
    published out of a denied path is sniffed with force=True.
    """
    if not force and os.path.splitext(path)[1].lower() not in TEXT_EXT:
        return None
    try:
        data = blob if blob is not None else open(path, "rb").read(400_000)
        text = data.decode("utf-8", "ignore")
    except OSError:
        return None

    # .py only. Shell scripts are NOT stripped: this file needs the exemption
    # because its own docstring quotes the patterns it searches for, and no
    # acquisition script does. A note pasted after `#` in a .sh should still trip.
    if path.endswith(".py"):
        text = strip_code_prose(text)          # 코드 설명은 검사 대상이 아니다

    if CLINICAL_HEADER.search(text):
        return "clinical note header"
    n_mark = len(NOTE_MARKER.findall(text))
    if n_mark >= NOTE_MARKER_MIN:
        return f"source-note markers x{n_mark}"
    hits = HANGUL_PROSE.findall(text)
    if len(hits) >= 3:
        return f"Korean prose ({len(hits)} runs)"
    return None


SEALED_PREFIX = "sealed/"


def git_ignored(paths, root):
    """Subset of paths that git ignores. Checked one call per path.

    A denied file that git cannot see is quarantined; one that git CAN see is a
    live risk. Conflating the two makes 'BLOCKED must be 0' unusable as soon as a
    corpus is downloaded, which is exactly when the check matters most.

    Deliberately not using `git check-ignore --stdin`: on macOS the filesystem
    hands back NFD-normalised names (Stölzl) that do not match on stdin, and a
    false 'not ignored' here would be reported as a live leak.
    """
    ignored = set()
    for p in paths:
        r = subprocess.run(["git", "-C", root, "check-ignore", "-q", "--", p],
                           capture_output=True)
        if r.returncode == 0:
            ignored.add(p)
    return ignored


def git_tracked(paths, root):
    """Subset of paths git has in its index — tracked, or staged for the next commit.

    This is the check that decides whether `sealed/` is reported as expected or as a
    violation, so it is asked directly rather than inferred. On git 2.54 the inference
    happens to work: `check-ignore` consults the index, so a force-added file comes
    back *not ignored* and the path is already treated as visible. That is a behaviour
    of one command on one version, and the property being enforced — a sealed file on
    its way into a commit must escalate — is too consequential to rest on it. Asking
    the index is redundancy today and the actual answer either way.

    Batched pathspecs rather than one call per path: 750 sealed files is one process,
    not 750. Chunked because a corpus can be large enough to overrun the argument
    limit.
    """
    tracked = set()
    paths = list(paths)
    for i in range(0, len(paths), 400):
        r = subprocess.run(
            ["git", "-C", root, "ls-files", "-c", "-z", "--", *paths[i:i + 400]],
            capture_output=True, text=True)
        tracked.update(p for p in r.stdout.split("\0") if p)
    return tracked


def screen_tree(root):
    """Walk the tree. Denied paths are recorded by name and never opened.

    sealed/ is denied, so the content sniffer never reads the test fold — the guard
    works from filenames alone and does not break the seal.

    Returns (blocked, sealed, quarantined, suspect, allowed).

      - 'blocked' is the number that must be zero before a commit: denied AND visible
        to git.
      - 'sealed' is the sealed test fold, denied and invisible to git. Expected, and
        reported on its own line rather than mixed into either of the others: it is
        not a risk (git cannot see it) and it is not routine either (the seal is the
        one rule where a reminder every single run is worth its noise).
      - 'quarantined' is any other denied path git cannot see — a downloaded corpus,
        expected once the data is on disk, reported as a count.

    A sealed path that git CAN see is counted as blocked, not sealed. That is the
    actual violation the seal exists to prevent, and it is the one case where the
    reassuring line would be the wrong one.
    """
    denied, suspect, allowed = [], [], []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "node_modules"}]
        for f in files:
            full = os.path.join(base, f)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if deny(rel):
                denied.append(rel); continue
            why = sniff(full, force=is_exception(rel))
            if why:
                suspect.append((rel, why))
            elif (any(re.search(p, rel) for p in ALLOW_PATTERNS)
                  or any(rel.startswith(h) or rel == h for h in ALLOW_HINTS)):
                allowed.append(rel)

    ignored = git_ignored(denied, root)
    tracked = git_tracked(denied, root)

    def visible(p):
        """Can git see this file? Either answer for yes; the index is decisive."""
        return p in tracked or p not in ignored

    blocked = [p for p in denied if visible(p)]
    # sealed/ gets its own line rather than being folded into `quarantined`, and it
    # is NOT exempt from `blocked`: a staged or tracked sealed file is caught by
    # `visible()` above before it can be reported as expected.
    sealed = [p for p in denied
              if not visible(p) and p.startswith(SEALED_PREFIX)]
    quarantined = [p for p in denied
                   if not visible(p) and not p.startswith(SEALED_PREFIX)]
    return blocked, sealed, quarantined, suspect, allowed


def screen_history():
    """Every blob ever committed, including ones deleted from the tip.

    Blobs only. `git rev-list --objects` also lists tree objects, and a tree is
    named by its directory path — so the tree for `data/acquire` matched the
    `^data/` deny rule and printed "do NOT make this repository public" on a
    repository whose only committed files there were the acquisition scripts. A
    false positive on that message is expensive: it is the one warning that must
    stay believable.
    """
    out = subprocess.run(["git", "rev-list", "--objects", "--all"],
                         capture_output=True, text=True).stdout.splitlines()
    candidates = []
    for line in out:
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        sha, path = parts
        if deny(path):
            candidates.append((sha, path))
    if not candidates:
        return []

    # One batch call rather than one process per object.
    probe = subprocess.run(["git", "cat-file", "--batch-check"],
                           input="".join(s + "\n" for s, _ in candidates),
                           capture_output=True, text=True).stdout.split("\n")
    kinds = {}
    for line in probe:
        f = line.split()
        if len(f) >= 2:
            kinds[f[0]] = f[1]
    return [(sha[:10], path) for sha, path in candidates
            if kinds.get(sha) == "blob"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--history", action="store_true")
    a = ap.parse_args()

    blocked, sealed, quarantined, suspect, allowed = screen_tree(a.root)
    print(f"BLOCKED by path rule      : {len(blocked)}   (denied AND visible to git — must be 0)")
    for p in sorted(blocked):
        print(f"   {p}")

    # Its own line, always printed, never zero-suppressed, and stated as a count
    # with its folds — a sealed fold is not a finding to be scrolled past, and it is
    # not a reason to fail either. Exit status ignores it (see below).
    print(f"\nSEALED (expected, exit 0) : {len(sealed)}   "
          "(the sealed test fold — denied, gitignored, and not readable from src/)")
    if sealed:
        for prefix, n in sorted(Counter(
                "/".join(p.split("/")[:3]) for p in sealed).items()):
            print(f"   {n:6d}  {prefix}/")
        print("   Do not open these. Test evaluation goes through "
              "src/eval/run_sealed_eval.py (CLAUDE.md, DESIGN §6).")

    print(f"\nQuarantined (gitignored)  : {len(quarantined)}   (denied but git cannot see them — expected once a corpus is on disk)")
    for prefix, n in sorted(Counter("/".join(p.split("/")[:2]) for p in quarantined).items()):
        print(f"   {n:6d}  {prefix}/")
    print(f"\nSUSPECT by content sniff  : {len(suspect)}")
    for p, why in sorted(suspect):
        print(f"   {p}  <- {why}")
    print(f"\nExplicitly allowed        : {len(allowed)}")

    if a.history:
        hits = screen_history()
        print(f"\nIN GIT HISTORY (deleting from the tip does not remove these): {len(hits)}")
        for sha, p in sorted(set(hits), key=lambda x: x[1]):
            print(f"   {sha}  {p}")
        if hits:
            print("\n   If any of these were ever committed, do NOT make this repository public.")
            print("   Start a fresh repository with no history, or rewrite history with git-filter-repo")
            print("   and force-push before publishing.")

    # Exit status is BLOCKED (plus SUSPECT), never SEALED. A gate that can never pass
    # stops being read: once a fold is sealed, folding it into BLOCKED would make
    # "BLOCKED must be 0" permanently false on every machine holding the data, and the
    # first response to a check that always fails is to stop running it.
    if blocked or suspect:
        sys.exit(1)
