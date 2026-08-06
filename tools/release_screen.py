#!/usr/bin/env python3
"""Screen a working tree for material that the PhysioNet DUA does not allow to be redistributed.

Run from the repository root before making anything public:

    python3 release_screen.py                 # screen the working tree
    python3 release_screen.py --history       # also screen every blob in git history

Two separate risks are checked. The first is the obvious one: files that hold note text or the
surrogate values themselves. The second is the one people miss: a file deleted today is still in
the git history, and making the repository public exposes the history along with the tip.

What is reported, and what gates a commit:

    BLOCKED       denied AND visible to git. Must be 0. Gates the commit.
    SEALED        the sealed test fold — denied, and git cannot see it. Expected, printed
                  every run, exit 0. A sealed path git CAN see is BLOCKED, not SEALED.
    Quarantined   any other denied path git cannot see, e.g. a downloaded corpus.
    SUSPECT       content sniff, split three ways: entries in tools/screen_allowlist.json
                  are counted as known, gitignored hits are counted as unpublishable,
                  and anything left is printed individually and gates the commit.
    STALE         allowlist entries whose file no longer exists. Reported, exit 0.

The design principle behind the last three: a finding that appears on every run is not
a finding. Five permanent SUSPECT lines meant a sixth would have arrived among them
unread, and a screener that exits 1 on a clean tree teaches people to ignore it.
Everything expected is summarised as a count; only what is new is printed.

Nothing is deleted. The script reports; the decisions stay with the author.
"""
import argparse, json, os, re, subprocess, sys
from collections import Counter

#: The sealed test fold. Named once and used by the deny rule, the reporting split
#: and the allowlist validator, so those three cannot drift apart.
SEALED_PREFIX = "sealed/"

# Paths that carry note text or generated surrogate values. Never publish.
#
# Corpus-agnostic by design: the rules must hold for a corpus that does not exist yet.
# A pattern naming one corpus ("ko_") silently stops protecting the next one.
DENY_PATTERNS = [
    # ─── 봉인된 test fold ───────────────────────────────────────
    # 무조건 deny. 여기 걸리면 내용을 읽지 않으므로 봉인 규율과 충돌하지 않는다.
    # 보고는 SEALED 줄로 분리한다 (screen_tree 참조). git 에 보이게 되면 BLOCKED.
    "^" + SEALED_PREFIX,

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

    # ─── 채워진 프롬프트 인스턴스 ───────────────────────────────
    # 템플릿(docs/prompts/*.md)은 공개된다. 값이 채워진 인스턴스는 dev 원문을
    # 담는다 — RuleAuthor 프롬프트의 오류 스팬 블록은 ±120자 문맥을 포함하고,
    # 그것은 코퍼스 원문이다 (docs/prompts/rule_author.md §1.4, §7).
    #
    # 이 경로들을 .gitignore 에 넣지 않는 것은 의도적이다. gitignore 하면
    # 디스크에 생긴 인스턴스가 BLOCKED 대신 Quarantined 로 조용히 집계된다.
    # 규약은 "커밋 금지"가 아니라 "디스크에 남기지 않는다" 이므로, 파일이
    # 존재하는 것 자체가 잡혀야 한다. sealed/ 와 반대 판단이고, 이유가 다르다:
    # 봉인 폴드는 디스크에 있어야 하고 이것은 있어서는 안 된다.
    r"(^|/)prompts?/(filled|rendered)/",
    r"(^|/)[^/]*\.(filled|rendered)\.[^/]+$",
    r"(^|/)[^/]*_(filled|rendered)_prompt[^/]*",
    r"(^|/)[^/]*prompt[^/]*_iter[0-9]+[^/]*",
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

# ─── rule_id 검사 ───────────────────────────────────────────────────────────
# rules/*.yaml 은 공개되고, 규칙 이름은 metrics.json 의 by_rule 블록을 통해
# 한 번 더 공개된다 (DESIGN §9.3). 패턴에는 표면형을 금지하면서 이름은 열어두면
# 우회 경로가 된다 — 이름은 자유 텍스트이고 에이전트가 쓴다.
#
# 기준은 docs/prompts/rule_author.md Prohibition 2 와 같아야 한다: 임상 상용구
# (`Dr.`, `nacido el`, `Hospital`)는 허용, 개인을 지목하는 것은 금지.
# 규칙 이름은 기제를 서술한다 — `doctor_prefix`, `nhc_checksum`, `street_type`.
# 개인을 지목하는 이름은 그 대신 값을 담고, 값을 담은 이름은 표면적으로
# 다음 셋 중 하나로 나타난다.
RULE_ID_KEY = re.compile(r"^\s*(?:-\s*)?rule_id\s*:\s*(.+?)\s*(?:#.*)?$", re.M)

# 기제 어휘. 규칙 이름의 각 토큰은 여기 있거나 아래 코드형 토큰이어야 한다.
#
# **화이트리스트인 이유.** 형태 검사만으로는 `perez_ruiz` 와 `street_type` 을
# 가를 수 없다 — 둘 다 소문자 ASCII 두 토큰이다. 금지 목록으로 이 경계를 그리려면
# 금지할 이름을 적어야 하고, 그것은 표면형 목록을 저장소에 두는 것이므로 막으려는
# 바로 그 일이 된다. 반대로 **기제 어휘만으로 조립된 이름은 개인을 지목할 수 없다** —
# 이것이 Prohibition 2 의 기준(임상 상용구 허용 / 개인 지목 금지)을 상관관계가
# 아니라 성질로 구현하는 유일한 방법이다.
#
# 어휘는 영어·구조 용어이고 코퍼스 텍스트가 아니므로 커밋해도 안전하다.
# 목록에 없는 낱말이 필요하면 여기 추가한다 — naming.yaml 이 모든 축 값에 이미
# 부과하는 것과 같은 비용이고, 누군가 이의를 제기할 수 있는 diff 가 된다.
RULE_ID_VOCAB = {
    # 대상
    "name", "given", "surname", "patient", "doctor", "clinician", "staff",
    "relative", "date", "dob", "birth", "death", "admission", "discharge",
    "age", "year", "month", "day", "time", "place", "city", "town", "province",
    "region", "country", "postcode", "street", "road", "avenue", "square",
    "address", "building", "floor", "hospital", "clinic", "centre", "center",
    "ward", "unit", "service", "department", "institution", "company",
    "insurer", "phone", "fax", "email", "url", "profession", "job",
    "occupation", "record", "episode", "licence", "license", "policy",
    "account", "number", "code",
    # 기제
    "prefix", "suffix", "cue", "trigger", "context", "window", "pattern",
    "regex", "checksum", "validate", "check", "digit", "digits", "format",
    "gazetteer", "lexicon", "list", "dictionary", "lookup", "membership",
    "title", "abbrev", "abbreviation", "initial", "initials", "token",
    "boundary", "case", "fold", "upper", "lower", "numeric", "alpha",
    "alphanumeric", "separator", "delimiter", "range", "span", "line",
    "header", "field", "label", "keyword", "term", "type", "form", "strict",
    "loose", "narrow", "wide", "generic", "compound", "hyphen", "particle",
    "preposition", "article", "ordinal", "roman", "written", "spelled",
    "slash", "dash", "dot", "colon", "paren", "bracket", "quote", "space",
    "long", "short", "full", "partial", "left", "right", "before", "after",
    "with", "without", "and", "or", "not", "any", "all", "only",
}
#: 코드형 토큰. 국가별 식별자 약어와 자릿수 표기는 기제 어휘가 아니지만
#: 규칙 이름에 정상적으로 나타난다 (`nhc_checksum`, `cp_5digit`).
RULE_ID_ALLOWED_TOKENS = {
    "id", "nhc", "cip", "ss", "dni", "nie", "nif", "cp", "iban", "uuid",
    "ssn", "mrn", "kvnr", "nhs", "curp", "rut",
}
#: 자릿수·버전 표기. `cp_5digit` 의 `5digit`, `v2`.
RULE_ID_CODE_TOKEN = re.compile(r"^(?:v[0-9]{1,2}|[0-9]{1,2}[a-z]{0,8})$")
RULE_ID_RULES = (
    # 사람 이름은 대문자로 시작한다. 규칙 이름은 기제 서술이므로 소문자다.
    (re.compile(r"[A-ZÁÉÍÓÚÜÑ]"),
     "capitalised token — a rule name describes a mechanism in lower case; a "
     "capital is how a proper noun enters one"),
    # 연도·생년·번호는 값이다. 기제에는 붙을 이유가 없다.
    (re.compile(r"[0-9]{3,}"),
     "3+ digit run — that is a value, not a mechanism"),
    # 비ASCII 문자는 코퍼스 언어의 낱말이다. 규칙 이름은 ASCII 식별자다.
    (re.compile(r"[^\x00-\x7f]"),
     "non-ASCII character — a rule name is an ASCII identifier; a word from the "
     "corpus language is a quoted surface"),
)
#: 이름 전체 길이 상한. 기제 서술은 짧다. 긴 이름은 문구를 담고 있다.
RULE_ID_MAX_LEN = 40
RULE_ID_MAX_PARTS = 5


def rule_id_findings(text):
    """rule_id 값 중 표면형을 담은 것으로 보이는 것을 (id, 이유) 로 돌려준다.

    Shape first, then a *positive* vocabulary — never a list of names to reject. A
    screener holding the names it objects to would be a file of surface forms in the
    repository, which is the thing being prevented. The vocabulary inverts that: it
    lists what a mechanism name may be built from, and a name assembled only from
    mechanism words cannot designate an individual.

    Shape alone is not enough and it is worth being explicit about why, because shape
    is the obvious design: `perez_ruiz` and `street_type` are both two lowercase ASCII
    tokens with no digits. No property of the string separates them. Only membership
    does.

    False positives are the acceptable direction. A rejected name is renamed at no
    cost; a name carrying a patient's surname into `metrics.json` cannot be unpublished
    (CLAUDE.md: the repository is public and a push is irreversible).
    """
    out = []
    for raw in RULE_ID_KEY.findall(text):
        value = raw.strip().strip("'\"")
        if not value:
            continue
        body = value.split(":", 1)[1] if ":" in value else value
        parts = [p for p in re.split(r"[_\-]", body) if p]

        # Shape first: these say something specific about *how* the name is wrong,
        # and a caller renaming it is better served by "that is a value" than by
        # "unknown word".
        shape = next((why for pattern, why in RULE_ID_RULES
                      if pattern.search(body)), None)
        if shape:
            out.append((value, shape))
            continue
        if len(body) > RULE_ID_MAX_LEN:
            out.append((value, f"longer than {RULE_ID_MAX_LEN} characters — a "
                               "mechanism description is short; a phrase is not"))
            continue
        if len(parts) > RULE_ID_MAX_PARTS:
            out.append((value, f"more than {RULE_ID_MAX_PARTS} parts — that is a "
                               "phrase rather than a name"))
            continue

        # Then the vocabulary. This is the check that implements Prohibition 2's
        # actual criterion, and the one shape cannot: `perez_ruiz` and `street_type`
        # have the same shape.
        unknown = [p for p in parts
                   if p.lower() not in RULE_ID_VOCAB
                   and p.lower() not in RULE_ID_ALLOWED_TOKENS
                   and not RULE_ID_CODE_TOKEN.match(p.lower())]
        if unknown:
            out.append((value, f"{len(unknown)} token(s) outside the mechanism "
                               "vocabulary — a name assembled only from mechanism "
                               "words cannot designate an individual, which is why "
                               "the check is a vocabulary and not a blacklist"))
    return out

# ─── known false positives ──────────────────────────────────────────────────
# Five files trip the content sniffer for reasons that are not note text, on every
# single run. Printed in full they were five lines nobody read, which meant a sixth
# — a real one — would have arrived unnoticed. Same problem the SEALED line solved
# for BLOCKED: a permanent finding is not a finding.
#
# The list is data rather than code, and committed, so adding an entry is a diff
# someone can object to. Its rules are enforced in load_allowlist(), not documented
# and hoped for.
ALLOWLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "screen_allowlist.json")


class AllowlistError(Exception):
    """The allowlist itself is not acceptable. Screening does not continue.

    Refusing to run is the only safe response: the alternative is to drop the bad
    entry and keep screening, which produces a clean-looking report from a file that
    was tampered with. A screener that reports 'all known' on the strength of a list
    it also rejected would be worse than one that never had a list.
    """


def load_allowlist(path=None):
    """Read the allowlist and validate every entry. Returns {path: entry}.

    Four rules, each with a specific abuse in mind:

      - **Literal paths only.** A wildcard entry stops naming what it permits. This
        is the `data/acquire/*.sh` lesson: that whitelist was keyed on an extension
        and published a file holding a clinical note header.
      - **Nothing denied, nothing under data/ or sealed/.** A sniffer hit on a corpus
        or sealed path is the alarm this tool exists for, and an allowlist that can
        silence it is a way to publish note text with a one-line diff. `data/README.md`
        is refused too, despite being publishable by path: it is the one file
        published out of a denied prefix, so it is the last one that should also be
        exempt from the content check.
      - **A stated sniff kind.** Pinning it means a file allowlisted for Korean prose
        that starts matching the clinical-header pattern is reported, because that is
        a new fact about the file rather than the known one.
      - **A real reason.** Short or absent text produces entries nobody can evaluate
        later, which get renewed forever.
    """
    path = path or ALLOWLIST
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise AllowlistError(
            f"{path} is missing. It is committed; if it was deleted, restore it "
            "rather than screening without it — every known false positive would "
            "otherwise be reported as new and the report becomes unreadable.")
    except json.JSONDecodeError as exc:
        raise AllowlistError(f"{path} is not valid JSON: {exc}")

    entries = {}
    for i, entry in enumerate(data.get("entries", [])):
        p = entry.get("path", "")
        where = f"{path} entry {i}"
        if not p:
            raise AllowlistError(f"{where} has no path")
        if any(c in p for c in "*?[]") or p.endswith("/"):
            raise AllowlistError(
                f"{where}: {p!r} is a pattern or a directory. Literal file paths "
                "only — an entry has to name what it permits.")
        if p.startswith("data/") or p.startswith(SEALED_PREFIX):
            raise AllowlistError(
                f"{where}: {p!r} is under data/ or {SEALED_PREFIX} and must not be "
                "allowlisted. A sniffer hit there is the alarm, not noise.")
        if deny(p):
            raise AllowlistError(
                f"{where}: {p!r} is denied by a path rule and must not be "
                "allowlisted. Denied paths are never published, so a content "
                "exemption for one can only serve to publish it.")
        if not entry.get("sniff"):
            raise AllowlistError(
                f"{where}: {p!r} has no `sniff`. State which hit is expected, so a "
                "different one is still reported.")
        if len(entry.get("why", "").split()) < 5:
            raise AllowlistError(
                f"{where}: {p!r} needs a `why` that a reader can evaluate later.")
        entries[p] = entry
    return entries


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

    # rules/*.yaml only. The check is about rule *names*, and a rule_id appears in
    # other files (spans.jsonl, metrics.json) as a value copied from here — screening
    # the origin is what stops it, and screening the copies would report one mistake
    # many times over.
    if re.search(r"(^|/)rules/[^/]+\.ya?ml$", path.replace(os.sep, "/")):
        found = rule_id_findings(text)
        if found:
            why = found[0][1]
            # The id is NOT quoted: it may be the surface form itself, and this
            # message goes to a terminal and a CI log where nothing screens it
            # (CLAUDE.md). The rule file is small and the shape is enough to find it.
            return (f"rule_id shape ({len(found)} of them): {why}")

    if CLINICAL_HEADER.search(text):
        return "clinical note header"
    n_mark = len(NOTE_MARKER.findall(text))
    if n_mark >= NOTE_MARKER_MIN:
        return f"source-note markers x{n_mark}"
    hits = HANGUL_PROSE.findall(text)
    if len(hits) >= 3:
        return f"Korean prose ({len(hits)} runs)"
    return None


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


def partition_suspect(suspect, allowlist, root=".", allowlist_path=None):
    """Sort sniffer hits into (known, unexpected, unpublishable, stale).

      - 'known' matches an allowlist entry, *and* matches the kind of hit that entry
        expects. Reported as a count.
      - 'unexpected' is everything else git can see. This is the only category that
        gates a commit, and it is the reason the other three exist: five permanent
        lines meant a sixth arrived among them unread.
      - 'unpublishable' is a hit on a file git cannot see. It cannot reach the public
        repository, so it is a count rather than a finding — the same reasoning that
        separates `quarantined` from `blocked`. Not allowlisted instead, because these
        files are machine-local (`config/data_paths.local.yaml`, editor settings) and
        an entry for one would read as stale on every other machine, which is the
        noise this whole change is removing.
      - 'stale' is an allowlist entry whose file is gone. Reported because an
        allowlist nobody prunes is one that eventually permits something by accident,
        and because a renamed file silently loses its exemption — the hit comes back
        under the new name as unexpected, and the old entry is the clue.

    A path in the allowlist whose sniff kind has *changed* is unexpected, not known.
    That is a new fact about the file, and being previously excused for a different
    reason is not a reason to excuse it.
    """
    paths = [p for p, _ in suspect]
    ignored = git_ignored(paths, root)
    tracked = git_tracked(paths, root)

    known, unexpected, unpublishable = [], [], []
    for path, why in suspect:
        entry = allowlist.get(path)
        if entry and why.startswith(entry["sniff"]):
            known.append((path, why))
        elif path in tracked or path not in ignored:
            unexpected.append((path, why, entry))
        else:
            unpublishable.append((path, why))

    # Staleness is only meaningful against the tree the allowlist describes: entries
    # are relative to the repository containing the list. Screening some other tree
    # would otherwise report every entry as stale, and a check that cries wolf on a
    # scratch directory is one people learn to skip on the tree that matters. Derived
    # from the list's own location rather than from this file's, so a caller that
    # supplies its own allowlist still gets the check.
    describes = os.path.dirname(os.path.dirname(
        os.path.abspath(allowlist_path or ALLOWLIST)))
    stale = sorted(p for p in allowlist
                   if os.path.realpath(root) == os.path.realpath(describes)
                   and not os.path.exists(os.path.join(root, p)))
    return known, unexpected, unpublishable, stale


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
    ap.add_argument("--allowlist", default=None,
                    help="path to the known-false-positive list "
                         "(default: tools/screen_allowlist.json)")
    a = ap.parse_args()

    # Before anything is screened. A rejected allowlist must not produce a report at
    # all: "SUSPECT 5 (all known)" computed from a list this tool refused is the most
    # misleading line it could print.
    try:
        allowlist = load_allowlist(a.allowlist)
    except AllowlistError as exc:
        print(f"ALLOWLIST REJECTED — nothing was screened.\n\n   {exc}",
              file=sys.stderr)
        sys.exit(2)

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
    known, unexpected, unpublishable, stale = partition_suspect(
        suspect, allowlist, a.root, a.allowlist)

    summary = f"{len(known)} known"
    if unpublishable:
        summary += f", {len(unpublishable)} gitignored"
    if unexpected:
        summary += f", {len(unexpected)} UNEXPECTED"
    print(f"\nSUSPECT by content sniff  : {len(suspect)}   ({summary})")

    # Only the unexpected ones are printed. The known five were five permanent lines
    # that nobody read, which is how a sixth would have gone unnoticed.
    if unexpected:
        print("   ┌─ NOT IN THE ALLOWLIST — read these before committing ─────────")
        for p, why, entry in sorted(unexpected):
            print(f"   │  {p}  <- {why}")
            if entry:
                print(f"   │     allowlisted for {entry['sniff']!r}, so this is a "
                      "different hit and is not covered")
        print("   └───────────────────────────────────────────────────────────────")
        print("   If one is genuinely harmless, add it to "
              f"{os.path.basename(a.allowlist or ALLOWLIST)} with a reason.")
    if unpublishable:
        print(f"   {len(unpublishable)} more are gitignored and cannot be published "
              "(machine-local config, editor settings).")

    # A list nobody prunes eventually permits something by accident, and a renamed
    # file loses its exemption silently — the hit reappears as unexpected under the
    # new name, and the orphaned entry is the clue to what happened.
    if stale:
        print(f"\nSTALE allowlist entries   : {len(stale)}   "
              "(listed, but no such file — prune or fix the path)")
        for p in stale:
            print(f"   {p}")

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

    # Exit status is BLOCKED plus unexpected SUSPECT hits. Never SEALED, never the
    # known false positives, never a gitignored one. A gate that can never pass stops
    # being read — the screener used to exit 1 on every clean tree because of those
    # five files, which trains exactly the habit that makes the tool useless. Stale
    # entries do not fail either: they are a housekeeping signal, and failing on them
    # would put pressure on someone to delete an entry to get a green run.
    if blocked or unexpected:
        sys.exit(1)
