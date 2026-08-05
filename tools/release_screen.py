#!/usr/bin/env python3
"""Screen a working tree for material that the PhysioNet DUA does not allow to be redistributed.

Run from the repository root before making anything public:

    python3 release_screen.py                 # screen the working tree
    python3 release_screen.py --history       # also screen every blob in git history

Two separate risks are checked. The first is the obvious one: files that hold note text or the
surrogate values themselves. The second is the one people miss: a file deleted today is still in
the git history, and making the repository public exposes the history along with the tip.

Nothing is deleted. The script reports; the decisions stay with the author.
"""
import argparse, json, os, re, subprocess, sys

# Paths that carry note text or generated surrogate values. Never publish.
DENY_PATTERNS = [
    r"data/derived/ko_surrogate.*\.jsonl$",
    r"data/derived/ko_tagged.*\.jsonl$",
    r"data/derived/surrogate_registry.*\.jsonl$",
    r"data/derived/.*value_map.*\.json$",
    r"results/.*/ko_surrogate.*\.jsonl$",
    r"results/.*value_map.*\.json$",
    r"results/.*/call_logs?/.*",           # model call-and-response logs quote the notes
    r"results/.*/raw_responses.*",
    r"data/source/.*",                     # the English source release
]

# Aggregates, code and public reference material. Safe to publish.
ALLOW_HINTS = [
    "src/", "paper/NUMBERS.md", "refs/", "prompts/",
    "results/T6/fig9_inputs.json", "results/T7/fig8_inputs.json",
    "results/T2/scores.csv", "results/T2/armP/scores_armP.csv",
    "results/T2/armP/pool_partition.json", "results/T2/armP/fold_composition.json",
    "results/T2/armP/surname_reconciliation.json",
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

TEXT_EXT = {".json", ".jsonl", ".csv", ".tsv", ".md", ".txt", ".py", ".yaml", ".yml"}


def deny(path):
    return any(re.search(p, path) for p in DENY_PATTERNS)


def strip_code_prose(text):
    """파이썬 소스에서 주석과 docstring을 지운다. 한국어 설명이 여기 들어 있다."""
    return COMMENT_OR_DOCSTRING.sub(" ", text)


def sniff(path, blob=None):
    if os.path.splitext(path)[1].lower() not in TEXT_EXT:
        return None
    try:
        data = blob if blob is not None else open(path, "rb").read(400_000)
        text = data.decode("utf-8", "ignore")
    except OSError:
        return None

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


def screen_tree(root):
    blocked, suspect, allowed = [], [], []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "node_modules"}]
        for f in files:
            full = os.path.join(base, f)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if deny(rel):
                blocked.append(rel); continue
            why = sniff(full)
            if why:
                suspect.append((rel, why))
            elif any(rel.startswith(h) or rel == h for h in ALLOW_HINTS):
                allowed.append(rel)
    return blocked, suspect, allowed


def screen_history():
    """Every blob ever committed, including ones deleted from the tip."""
    out = subprocess.run(["git", "rev-list", "--objects", "--all"],
                         capture_output=True, text=True).stdout.splitlines()
    hits = []
    for line in out:
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        sha, path = parts
        if deny(path):
            hits.append((sha[:10], path))
    return hits


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--history", action="store_true")
    a = ap.parse_args()

    blocked, suspect, allowed = screen_tree(a.root)
    print(f"BLOCKED by path rule      : {len(blocked)}")
    for p in sorted(blocked):
        print(f"   {p}")
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

    if blocked or suspect:
        sys.exit(1)
