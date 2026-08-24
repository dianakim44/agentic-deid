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

    # ─── 루프가 만드는 잔존 식별자 목록 ─────────────────────────
    # DESIGN §5.5. 둘 다 표면형을 담지 않도록 쓰는 쪽이 규약을 지키지만, 담는
    # 것은 위치이고 위치의 목록이 문제다 — audit_report.json 은 Auditor 가
    # 잔존 PHI 라고 의심한 지점들이고, errors.jsonl 은 그 fold 에서 실제로
    # 놓친 모든 식별자의 위치다 (gold 에서 나온다). DUA 코퍼스에서 후자는
    # 남아 있는 식별자의 지도 그 자체다.
    #
    # ALLOW 에 올리고 sniffer 에 맡기지 않는 이유: §6.1 의 allowlist 논거는
    # "경로 규칙이 이미 공개하기로 한 파일만 내용 검사를 면제받을 수 있다" 로
    # 가고, 이 둘을 공개해야 할 이유가 없다. metrics.json 과 spans.jsonl 이
    # 탐지에 대해 독자가 필요한 것을 담는다.
    #
    # 이름으로 거는 것은 의도다. 경로 전체(iter{n}/ 아래)로 걸면 그 디렉토리에
    # 함께 있는 itermetrics·iterspans 를 같이 잡는다.
    r"(^|/)audit_report\.json$",
    r"(^|/)errors\.jsonl$",

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
    # The iterating arms' per-round score and predictions (paths.itermetrics,
    # paths.iterspans; DESIGN §5.5). Same content as the two above — offsets, types and
    # scores — so the same treatment, and declared in the same commit as the keys for
    # `armrules`' reason: a path declared in one commit and screened in a later one goes
    # unscreened in between, and unscreened does not mean rejected. It means the file
    # passes without the check ever running.
    #
    # Two entries rather than an optional `(iter[0-9]+/)?` on each of the two above. The
    # optional group is the shorter edit and it makes one pattern answer two questions:
    # a typo inside it would silently widen or narrow both the four-deep and the
    # five-deep case, and the four-deep case is what every committed result matches
    # today. Separate lines fail separately.
    #
    # `audit_report.json` and `errors.jsonl` live in this same directory and are
    # deny-listed by name, which is why these two match on their filenames rather than
    # on the directory (see DENY_PATTERNS).
    r"^results/[^/]+/[^/]+/[^/]+/[^/]+/iter[0-9]+/metrics\.json$",
    r"^results/[^/]+/[^/]+/[^/]+/[^/]+/iter[0-9]+/spans\.jsonl$",
    r"^results/sealed_eval_log\.md$",
    # port-human only, and the {porting} component is the literal rather than [^/]+:
    # DESIGN §11.2 gives this file exactly one value of that axis. `human_minutes` and
    # a free-text `decision` are what a person writes; no agent arm has either. A
    # wildcard would allow the filename under port-loop, where nothing writes it and
    # its presence would mean something unreviewed had.
    r"^results/[^/]+/[^/]+/[^/]+/port-human/human_log\.jsonl$",
    # window_freeze.json is the opposite case and stopped being port-human's in
    # DESIGN §6.3: *every* arm freezes its own window at first use, under its own
    # {porting} value (config/naming.yaml `paths.armfreeze`; `paths.humanfreeze` stays
    # pinned to port-human so a retired arm's record is unreachable from any other).
    # Narrowing this to the literal would leave every agent arm's freeze record in no
    # category at all, which reads as reviewed to anyone scanning the summary. The file
    # holds two content hashes, a revision number and axis values — no corpus text — and
    # being on this list is a statement about the path and never about the content: the
    # sniffer runs first and a freeze record that somehow carried note text is SUSPECT
    # before it is ever matched here.
    r"^results/[^/]+/[^/]+/[^/]+/[^/]+/window_freeze\.json$",
    # An arm's own rule files (paths.armrules, DESIGN §5.3). Publishable for the same
    # reason the committed rules/{lang}.yaml is: patterns, literal term lists and cue
    # words, which rule_author.md Prohibition 2 restricts to clinical formulae. Being on
    # this list is a statement about the path and never about the content — `sniff()` runs
    # first, and it applies the rule_id vocabulary check to this path too, so a name
    # carrying a surface form is SUSPECT before it is ever matched here.
    r"^results/[^/]+/[^/]+/[^/]+/[^/]+/rules/iter[0-9]+/[^/]+\.ya?ml$",
    # The format-failure record (paths.formatfailure, DESIGN §10 A2). Retries are zero, so
    # a file that does not pass the schema is the arm's result and this is where it is
    # written — the model ids, the raw response, and the validator's own error message
    # verbatim. On the list because a failure nobody can read is not a reportable result:
    # the appendix's sentence is "this model could not do it", and it rests on the reader
    # being able to see what came back.
    #
    # It is the one allowed path whose content is a *model's* output rather than this
    # project's, and being on this list is a statement about the path and never about the
    # content — `sniff()` runs first. That ordering is load-bearing here in a way it is not
    # for metrics.json: a completion that echoed its prompt would carry whatever the prompt
    # carried, and "the first call shows §§1.1-1.2 only" is a fact about today's arm rather
    # than a property of the path. So the sniffer is what decides, and a response that came
    # back with corpus text in it is SUSPECT before it is ever matched here.
    r"^results/[^/]+/[^/]+/[^/]+/[^/]+/format_failure\.json$",
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
# **기제와 내용 — 확장을 판정하는 기준이고, 세 집합 전부에 적용된다 (2026-08-24).**
# `rule_id` 는 규칙이 **무엇으로 작동하는지** 이름 짓는다. 무엇을 담는지가 아니다.
# 내용은 `terms` 목록과 `pattern` 안에 있고 이름에는 없다. `spanish_city_gaz` 는
# 기제 이름이고 `madrid_gaz` 는 내용 이름이다 — 두 규칙은 같은 사전을 가질 수 있고,
# 다른 것은 이름이 사전의 *구성 원리*를 말하는지 사전의 *한 항목*을 말하는지다.
#
# **왜 "개인을 지목하는가" 만으로는 부족한가.** 그 기준만 쓰면 지명이 통과한다 —
# `madrid` 도 `espana` 도 개인을 지목하지 않는다. 그러면 어휘는 "사람 이름이 아닌 것
# 전부" 로 번져 나가고, 이 검사가 실제로 막고 있는 것(성씨가 이름을 타고 들어오는 것)
# 의 경계가 사라진다. 범주 이름은 이미 여기 있다 (`city`·`town`·`province`·`region`·
# `country`); 그 범주의 **한 원소**를 요구하는 이름은 기제를 다 서술하지 못했다는
# 표시이므로, 어휘를 넓히는 것이 아니라 이름을 고치는 것이 답이다.
#
# 두 기준은 순서대로 적용된다: 내용 이름이면 기각하고, 기제 이름이면 그 다음에
# 개인 지목 여부를 본다. 아래 언어층 머리말의 배제 범주 2번(`calle` 는 거리의
# 종류이고 거리 이름이 아니다)과 네 번째 확장의 `institute`/`Instituto Cajal` 이
# 이 기준의 두 사례이고, 이 문단은 그 둘을 언어층·기관 범주에서 일반 기준으로
# 올린 것이다. 판정 기록은 DESIGN §6.1 의 여섯 번째 확장 항목에 있다.
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
    # 2026-08-11. 이 넷은 언어별 층이 아니라 여기 들어간다 — 어느 언어의
    # 상용구도 아니고 기제·구조 낱말이기 때문이다. 층에 넣으면 같은 낱말을
    # 언어마다 반복해야 하고, 그것은 범주를 잘못 고른 표시다.
    #
    #   - `dmy`/`mdy`/`ymd`: 날짜 성분의 **순서**. `date_dmy_slash` 는 구분자와
    #     순서로 기제를 다 서술한다.
    #   - `postal`: `postcode`·`code` 가 이미 있는데 `postal_code` 라는 두 낱말
    #     형태가 자연스럽다. 같은 대상의 형용사형이다.
    #   - `years`/`months`: `year`·`month` 의 복수형. 단수만 있는 것은 누락이고
    #     구분이 아니다 (`digit`/`digits`·`initial`/`initials` 는 이미 둘 다 있다).
    "dmy", "mdy", "ymd", "postal", "years", "months", "days",
    # 2026-08-12. `gaz` 는 `gazetteer` 의 약어이고, 그 낱말은 이미 이 어휘에
    # 있다. layer 축의 값 이름이기도 하다 (config/naming.yaml). `abbrev` 가
    # `abbreviation` 과 함께 있는 것과 같은 형태 — 줄임말만 빠진 것은 누락이고
    # 구분이 아니다. 개인을 지목할 수 없다.
    "gaz",
    # 언어 이름. 규칙의 **방언**을 서술한다 (`date_spanish_month_long` = 스페인어
    # 월 이름 표기). 개인을 지목할 수 없고, `config/naming.yaml` 의 lang 축이
    # 아니라 영어 언어명이므로 축 어휘 규약과 충돌하지 않는다.
    "spanish", "catalan", "german", "korean", "english",
    # 2026-08-21, 세 번째 확장. 두 범주이고, 둘 다 이 어휘에 **이미 있는 대상의
    # 하위 종류**다 — 파생형·약어가 아니라 같은 칸의 빈 자리다 (DESIGN §3).
    #
    #   - **표기 규약의 이름.** `dmy`/`mdy`/`ymd` 는 날짜 성분의 순서이고, 순서를
    #     규약으로 고정한 것에도 이름이 있다. `date_iso_pattern` 은 `iso` 하나로
    #     순서·구분자·자릿수를 다 서술한다. 같은 칸: 규약 이름(`iso`), 규약이
    #     아니라 지역 관행이라는 표시(`local`·`locale`), 시각을 수로 적는 형태
    #     (`timestamp`·`epoch`). 어느 것도 개인을 지목할 수 없다.
    #   - **연락 수단의 종류.** `phone`·`fax`·`email`·`url` 은 수단이고, 수단에는
    #     종류가 있다 — `phone_landline` 은 유선이라는 종류를 말한다. 같은 칸:
    #     `landline`·`mobile`·`cell`·`cellular`·`pager`·`extension`·`website`,
    #     그리고 수단 전체를 가리키는 `contact`. `street`/`hospital` 이 거리·기관의
    #     종류인 것과 같은 층위이고, 특정 회선·특정 사람을 가리키지 않는다.
    "iso", "local", "locale", "timestamp", "epoch",
    "landline", "mobile", "cell", "cellular", "pager", "extension",
    "website", "contact",
    # 2026-08-23, 네 번째 확장. 세 범주이고, 세 번째는 앞의 셋과 다른 종류다 —
    # 앞의 것들은 있는 칸의 빈 자리였는데 이것은 **없던 칸**이다 (DESIGN §6.1).
    #
    #   - **기관의 종류, 그리고 그 범주를 가리키는 총칭.** `hospital`·`clinic`·
    #     `centre`·`ward`·`unit`·`service`·`department`·`institution`·`company`·
    #     `insurer` 가 이미 있는데 그 전체를 가리키는 낱말이 없었다 —
    #     `hospital_org` 의 `org` 가 그것이다. 총칭과 그 약어를 넣고, 같은 칸에서
    #     빠져 있던 기관 종류들을 함께 채운다. 어느 것도 한 기관을 지목하지 않는다:
    #     `institute` 는 기관의 종류이고 `Instituto Cajal` 은 기관의 이름이다.
    #   - **성분을 어떤 표기로 적었는가.** `numeric`·`alpha`·`alphanumeric`·
    #     `written`·`spelled`·`roman`·`ordinal`·`long`·`short` 가 있고, 글자로
    #     적었다는 것 자체를 말하는 낱말이 없었다 — `date_month_year_text` 의
    #     `text` 다. 같은 칸: 글자/낱말 표기(`text`·`textual`·`word`·`words`),
    #     섞인 표기(`mixed`), 자리를 채운 표기(`padded`·`unpadded`), 그리고 그
    #     자리가 어느 쪽인지(`leading`·`trailing` — `left`·`right`·`before`·
    #     `after` 와 같은 층위).
    #   - **단서 없이 홀로 성립하는 규칙.** 이것은 빈 자리가 아니라 없던 칸이다.
    #     어휘에는 단서가 **있다는** 쪽의 낱말만 있었다 (`cue`·`trigger`·
    #     `context`·`window`·`with`·`without`) — `without` 은 수식어이고, 단서를
    #     아예 두지 않는 변종 자체를 부르는 이름이 없었다. `date_year_standalone`·
    #     `postal_code_standalone` 이 그 이름을 요구했다. 칸을 만들고 채운다:
    #     `standalone`·`alone`·`bare`·`isolated`·`freestanding`·`inline`, 그리고
    #     그 반대쪽 표기(`anchor`·`anchored`·`unanchored`). 규칙이 어디에 걸리는지에
    #     대한 서술이고 무엇에 걸리는지가 아니므로 개인을 지목할 수 없다.
    "organisation", "organization", "org", "institute", "agency", "facility",
    "laboratory", "lab", "pharmacy", "practice", "foundation", "school",
    "university", "employer",
    "text", "textual", "word", "words", "mixed", "padded", "unpadded",
    "leading", "trailing",
    "standalone", "alone", "bare", "isolated", "freestanding", "inline",
    "anchor", "anchored", "unanchored",
    # 2026-08-23, 다섯 번째 확장. 두 범주이고, 첫째는 **범주가 아니라 누락된 불변식**이다.
    #
    #   - **`config/naming.yaml` 의 `phi_type`·`layer` 축 값.** `location`·`area`·`id`·
    #     `other`·`tagger` 가 빠져 있었다. 이것들이 여기 있어야 하는 이유는 관찰이
    #     아니라 정의다 — 축 값은 이 프로젝트가 스스로 정한 **범주 이름**이고, 범주
    #     이름은 개인을 지목할 수 없다. 규칙 작성자가 규칙의 대상 유형을 이름에 쓰는
    #     것은 자연스럽고(`en_location_cue`), `gaz` 를 넣을 때 이미 "layer 축의 값
    #     이름이기도 하다" 를 근거로 든 바 있다. 그때 낱말 하나만 넣고 축 전체를
    #     보지 않은 것이 이 확장의 원인이다. 그래서 이번에는 목록이 아니라 불변식으로
    #     닫는다: `test_every_phi_type_and_layer_token_is_in_the_vocabulary` 가
    #     naming.yaml 의 두 축을 읽어 전량을 요구하므로, 축에 값이 추가되면 어휘도
    #     같은 변경에서 따라오거나 스위트가 빨개진다. 이 범주의 여섯 번째 확장은 없다.
    #   - **성분을 무엇으로 둘러쌌는가.** `paren`·`bracket`·`quote` 는 구분자 *문자*
    #     이고, 그 문자로 감싼 **형태**를 부르는 낱말이 없었다 — `parenthetical_country`
    #     의 `parenthetical` 이 그것이다. `paren` 이 이미 있으므로 이것은 같은 칸의
    #     파생형이고, 세 번째 확장에서 기각된 option 1(정규화)이 잡았을 두 번째
    #     토큰이다 (DESIGN §6.1). 칸을 채운다: 감싼 형태(`parenthetical`·
    #     `parenthesised`·`parenthesized`·`bracketed`·`quoted`·`unquoted`·`enclosed`·
    #     `wrapped`), 그리고 이미 있는 `prefix`·`suffix`·`delimiter` 의 분사형
    #     (`prefixed`·`suffixed`·`delimited`).
    "location", "area", "id", "other", "tagger",
    "parenthetical", "parenthesised", "parenthesized", "bracketed", "quoted",
    "unquoted", "enclosed", "wrapped", "prefixed", "suffixed", "delimited",
    # 2026-08-24, 여섯 번째 확장. 한 범주이고 **없던 칸**이다. 네 토큰이 제안되었고
    # 둘은 기각되었다 (`viena`·`espana` — 내용 이름이다, 위 머리말과 DESIGN §6.1).
    #
    #   - **나이의 구간.** 어휘에는 나이의 **단위**만 있었다 (`age`·`year`·`month`·
    #     `day` 와 복수형). `age_months_infant` 의 `infant` 는 단위가 아니라 그 단위로
    #     나이를 적는 **구간**이고, 구간이 곧 패턴 계열이다 — 영아 나이는 개월로 적고
    #     성인 나이는 연으로 적으므로, 구간을 말하는 것이 그 규칙의 기제를 말하는
    #     것이다. `landline` 이 회선의 종류를 말해 `phone` 의 하위 기제를 서술하는
    #     것과 같은 층위다. 칸을 만들고 채운다. 구간은 계급이고 원소가 아니므로 —
    #     특정 나이는 내용이고 `infant` 는 범주다 — 위 기제/내용 기준을 통과하고,
    #     어느 개인도 지목하지 않는다.
    "infant", "neonate", "neonatal", "newborn", "perinatal", "gestational",
    "paediatric", "pediatric", "juvenile", "adolescent", "adult", "elderly",
    "geriatric",
}
#: 코드형 토큰. 국가별 식별자 약어와 자릿수 표기는 기제 어휘가 아니지만
#: 규칙 이름에 정상적으로 나타난다 (`nhc_checksum`, `cp_5digit`).
RULE_ID_ALLOWED_TOKENS = {
    "id", "nhc", "cip", "ss", "dni", "nie", "nif", "cp", "iban", "uuid",
    "ssn", "mrn", "kvnr", "nhs", "curp", "rut",
    # NUSS (número de la Seguridad Social). Same category as the rest of this set —
    # a national identifier scheme — and missing only because no rule had needed it.
    "nuss",
    # NASS (número de afiliación a la Seguridad Social). The same scheme's other
    # common abbreviation, and it arrived the way NUSS did: an arm wrote it before
    # this set had it. Both spellings are in circulation in Spanish clinical text,
    # so listing one and not the other is a gap rather than a distinction.
    "nass",
    # 2026-08-23, the fourth widening. `port-loop` round 2 wrote `cipa_cue` and
    # `ncol_cue`, and both are this set's own category — a scheme abbreviation that
    # appears in a rule name because the rule reads that scheme's field label. CIPA
    # is the autonomous-community personal health id, so `cip` was here and its
    # four-letter form was not; NCOL is `número de colegiado`, whose long form
    # `colegiado` the Spanish layer already held. Those two are the first tokens in
    # four widenings that are derivational relations of entries already present —
    # the relation this set was built for (`nuss`/`nass`) — and the response is the
    # same as for those: fill the category rather than the two names, with the
    # Spanish clinical id schemes that a rule author can reach for and this set
    # lacked. None is a value: an abbreviation names the scheme, and the number it
    # abbreviates never appears in a rule name.
    "cipa", "ncol", "naf", "tsi", "nuhsa", "sip",
}

# ─── 언어별 층 ──────────────────────────────────────────────────────────────
# 위 어휘는 영어 전용이었고, 그래서 **영어가 아닌 arm 전부에서 깨진다.** 규칙
# 이름을 짓는 것은 에이전트이고, 스페인어 코퍼스의 규칙에 스페인어 단서어의
# 이름을 붙이는 것은 자연스러운 선택이다 — `paciente_cue` 는 "환자" 를 뜻하는
# 임상 상용구를 가리키는 기제 이름이고, Prohibition 2 가 허용하는 바로 그
# 범주다. 2026-08-11 의 es-meddocan port-oneshot 은 28개 중 23개가 이 이유로
# SUSPECT 였다 (docs/notes/arm-port-oneshot-es.md).
#
# **기준은 움직이지 않는다.** Prohibition 2 는 "임상 상용구는 허용, 개인을
# 지목하는 것은 금지" 이고, 언어가 늘어도 그 선은 그대로다. 바뀌는 것은
# 상용구가 어느 언어로 쓰였는가뿐이다. 그러므로 여기 들어가는 것은
# **그 언어의 임상 상용구·행정 용어의 폐쇄 집합**이고, 개인을 지목할 수 있는
# 낱말 범주는 어느 언어에서도 들어가지 않는다:
#
#   - 사람 이름(성·이름·애칭)은 넣지 않는다. 경칭(`don`, `dona`, `herr`)은
#     상용구이지만 이름 자체는 아니다 — 경칭은 이름 **앞에 오는 표지**이고,
#     그것이 `doctor` 가 이미 위 어휘에 있는 이유와 같다.
#   - 지명(도시·구·병원 고유명)은 넣지 않는다. `calle`·`avenida` 는 거리의
#     **종류**이고 거리 이름이 아니다. `centro`·`salud` 는 기관의 종류이고
#     특정 기관이 아니다. 이 구분이 위 영어 어휘의 `street`/`hospital` 과
#     정확히 같은 구분이다.
#   - 하나의 개인·기관만 가리킬 수 있는 낱말은 넣지 않는다.
#
# **사례를 나열하는 것이 아니라 범주를 넓히는 것이다.** 이번 실행에서 걸린
# 23건을 근거로 삼았지만 그 목록을 그대로 허용하지 않았다: 각 토큰이 어느
# 범주인지 판정했고, 범주를 채울 때 그 실행에 나오지 않은 낱말도 함께 넣었다
# (`apellido`·`nacido`·`ingreso` 등). 나오지 않은 것을 넣는 것이 사례 목록과
# 범주의 차이이고, 다음 arm 이 같은 이유로 또 걸리지 않는 이유다.
#
# 이 낱말들은 사전에 있는 일반명사·행정용어이고 코퍼스에서 인용한 것이 아니다.
# 어느 개인도 지목하지 않으므로 커밋해도 안전하다 — 위 영어 어휘가 안전한 것과
# 같은 근거이고, 그 근거는 언어에 달려 있지 않다.
#
# **새 언어를 추가할 때 해야 하는 일.** (1) `config/naming.yaml` 의 `lang` 축에
# 그 언어가 있어야 한다 — 아래 키는 그 축에 대해 검사된다. (2) 여기 그 언어의
# 키를 추가하고, 위 세 개의 배제 범주를 지키는 상용구만 넣는다. (3) 그 언어의
# 이름이 통과하는 테스트와, 그 언어의 층이 개인 지목 이름을 통과시키지 않는지
# 보는 테스트를 함께 넣는다 (tests/test_release_screen.py). 층을 비워두는 것도
# 유효한 선택이다 — 그러면 그 언어의 규칙 이름은 영어 기제 어휘로만 지어진다.
RULE_ID_VOCAB_BY_LANG = {
    "es": {
        # 경칭·역할. 이름 앞에 오는 표지이고 이름이 아니다.
        "don", "dona", "sr", "sra", "srta", "doctor", "doctora", "dr", "dra",
        "paciente", "medico", "enfermero", "enfermera", "familiar",
        # 2026-08-21, 세 번째 확장. `licenciado` 가 걸렸고, 그것은 이 칸의 빈
        # 자리였다 — 학위·직위 경칭은 `don`·`doctor` 와 같은 범주이고, 스페인어
        # 임상 노트의 서명란에서 이름 앞에 오는 표지다. 걸린 낱말 하나가 아니라
        # 칸을 채운다: 학위 경칭의 남녀형과 약어, 그리고 서명하는 직위의 이름들.
        # 어느 것도 한 사람을 지목할 수 없다 — 배제 범주 셋 그대로다.
        "licenciado", "licenciada", "lic", "profesor", "profesora", "prof",
        "senor", "senora", "senorita", "especialista", "residente", "adjunto",
        "titular", "colegiado", "cirujano", "cirujana", "matrona",
        "auxiliar", "tecnico", "tecnica", "celador", "fisioterapeuta",
        "psicologo", "psicologa", "farmaceutico", "farmaceutica",
        # 문서 상용구. 임상 노트의 서식 낱말.
        "firmado", "atendido", "remitido", "derivado", "ingreso", "alta",
        "consulta", "informe", "servicio", "nacido", "nacida", "fecha",
        "nombre", "apellido", "apellidos", "edad", "anos", "meses",
        "profesion", "ocupacion", "domicilio", "telefono", "correo",
        # 주소·기관의 **종류**. 고유명이 아니다.
        "calle", "avenida", "plaza", "paseo", "carretera", "camino", "via",
        "numero", "piso", "puerta", "codigo", "postal", "provincia",
        "hospital", "centro", "salud", "clinica", "ambulatorio", "servicio",
        # 2026-08-23, 네 번째 확장. `localidad` 와 `instituto` 가 걸렸고, 위 두 칸의
        # 빈 자리다 — 행정 구역의 **종류**와 기관의 **종류**이고, 둘 다 고유명이
        # 아니다. 걸린 두 낱말이 아니라 칸을 채운다.
        #
        # 배제 범주 셋은 그대로다. 이 낱말 중 몇 개는 스페인어 성으로도 쓰인다
        # (`Barrio`·`Sala`) — 그것이 걸림돌이 아닌 이유는 이미 목록에 있는
        # `salud`(María Salud)·`alta`·`consulta` 가 통과하는 이유와 같다: 보증은
        # **조립된 이름이 개인을 지목할 수 없다**는 것이고, 보통명사 한 토큰으로는
        # 지목이 성립하지 않는다. 한 사람만 가리킬 수 있는 낱말은 여전히 들어가지
        # 않고, `barrio_lopez` 는 `lopez` 때문에 그대로 걸린다.
        "localidad", "poblacion", "municipio", "barrio", "distrito",
        "comarca", "ciudad", "pueblo", "region", "pais", "comunidad",
        "autonoma", "autonomica",
        "instituto", "institucion", "fundacion", "laboratorio", "farmacia",
        "consultorio", "residencia", "unidad", "departamento", "seccion",
        "planta", "sala", "urgencias", "mutua", "aseguradora", "organizacion",
        # 2026-08-23, 다섯 번째 확장. `motivo` 가 걸렸고 (`motivo_ingreso_cue`),
        # 이것은 임상 노트의 **절 제목**이라는 칸의 빈 자리다. 그 칸은 이미 열려
        # 있었다 — `ingreso`·`alta`·`consulta`·`informe` 가 그것이고, MEDDOCAN 노트의
        # 절 제목은 규칙이 창을 어디에 걸지 결정하는 가장 흔한 단서다. 걸린 한 낱말이
        # 아니라 칸을 채운다. 절 제목은 서식의 일부이고 개인을 지목하지 않는다.
        "motivo", "antecedentes", "anamnesis", "exploracion", "diagnostico",
        "evolucion", "tratamiento", "juicio", "clinico", "historia", "resumen",
        "epicrisis", "seguimiento", "derivacion", "interconsulta", "pruebas",
        "analitica", "medicacion",
        # 2026-08-24, 여섯 번째 확장. `responsable` 이 걸렸고 (`responsable_clinico_cue`,
        # 단서는 `Responsable clínico:` 라는 서식 표지), 이것은 **문서 상용구** 칸의 빈
        # 자리다 — `firmado`·`atendido`·`remitido`·`derivado` 와 같은 범주이고 같은
        # 형태(과거분사·역할 표지)다. `clinico` 는 다섯 번째 확장에서 이미 들어왔으므로
        # 걸린 토큰은 하나뿐이었다. 걸린 낱말이 아니라 칸을 채운다: 문서에서 책임·요청·
        # 확인의 주체를 지정하는 표지들. 이 낱말들은 규칙이 **무엇으로 창을 여는지**를
        # 말하므로 기제 이름이고 (단서 자체가 기제다), 어느 개인도 지목하지 않는다.
        "responsable", "encargado", "supervisor", "solicitante", "peticionario",
        "informante", "referente", "autorizado", "validado", "revisado",
        "emitido", "dirigido", "elaborado", "cumplimentado", "registrado",
        # 문법어. 복합 이름을 잇는다 (`atendido_por_cue`).
        "por", "del", "de", "la", "el", "los", "las", "en", "y",
    },
    "cat": {
        "sr", "sra", "senyor", "senyora", "doctor", "doctora", "pacient",
        "signat", "ates", "derivat", "ingres", "informe", "naixement",
        "nom", "cognom", "cognoms", "edat", "anys", "mesos", "professio",
        "domicili", "telefon", "adreca",
        "carrer", "avinguda", "placa", "passeig", "carretera", "cami",
        "numero", "pis", "porta", "codi", "postal", "provincia",
        "hospital", "centre", "salut", "clinica",
        "per", "del", "de", "la", "el", "els", "les", "en", "i",
    },
    "de": {
        "herr", "frau", "doktor", "arzt", "arztin", "patient", "patientin",
        "unterschrieben", "aufnahme", "entlassung", "befund", "geboren",
        "geburtsdatum", "name", "vorname", "nachname", "alter", "jahre",
        "monate", "beruf", "anschrift", "telefon",
        "strasse", "str", "gasse", "platz", "weg", "allee", "hausnummer",
        "stock", "postleitzahl", "plz", "stadt", "ort",
        "krankenhaus", "klinik", "praxis", "station", "abteilung",
        "von", "der", "die", "das", "den", "dem", "und", "im", "am",
    },
    "ko": {
        # 로마자 표기. 규칙 이름은 ASCII 식별자이므로 (RULE_ID_RULES 의 비ASCII
        # 규칙) 한글은 애초에 통과하지 못한다.
        "hwanja", "uisa", "seongmyeong", "ireum", "saengnyeonworil", "nai",
        "juso", "jeonhwa", "byeongwon", "uiwon", "jinryo", "gwa",
    },
    # 영어는 위 RULE_ID_VOCAB 자체가 영어이므로 층이 필요 없다. 키를 두는 것은
    # "en 은 잊혀졌다" 와 "en 은 층이 비어 있다" 를 가르기 위해서다.
    "en": set(),
}

#: 어느 언어 층에도 들어갈 수 없는 낱말이 아니라, **층이 지켜야 하는 성질**을
#: 검사에 쓰는 상한. 층은 폐쇄 집합이고 정규식이 아니므로 토큰 하나가
#: 통째로 매치되어야 하며, 위 배제 범주(이름·지명·개인 지목)는 코드가 아니라
#: 리뷰로 지켜진다 — 그리고 tests/test_release_screen.py 가 층 전체에 대해
#: 형태 규칙(대문자·숫자·비ASCII)을 다시 적용해서, 층을 통해 표면형이
#: 들어오는 가장 흔한 형태를 막는다.
RULE_ID_VOCAB_LANG_MAX_LEN = 20
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


def rule_id_findings(text, lang=None):
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

    **The language layer.** `lang` opens that language's clinical-formula set
    (`RULE_ID_VOCAB_BY_LANG`) in addition to the English mechanism vocabulary, because
    the English vocabulary alone rejects every non-English arm's names — and rejects
    them for naming a formula, which Prohibition 2 permits. Four properties, each of
    which is a way this could have become a bypass:

      - **`lang` comes from the caller, which derives it from the file path.** Not from
        the id's own prefix. The path is chosen by the harness from the arm's
        configuration (`paths.armrules`, DESIGN §5.3); the prefix is free text inside a
        file the model wrote. Letting the screened text nominate its own vocabulary is
        the bypass, and it is not a hypothetical distinction: every id in both the
        committed `rules/es.yaml` and the first port-oneshot output is unprefixed, so a
        prefix-keyed layer would have widened nothing at all.
      - **A prefix that disagrees with the path opens no layer.** `de:strasse_cue`
        inside `es.yaml` gets the English vocabulary only. Disagreement is the one case
        where the two sources can be played against each other, so it resolves to the
        narrower treatment.
      - **The layer is additive and per-language.** `es` opens the Spanish set and
        nothing else, so a widening is scoped to the language whose formulae justified
        it and Spanish words never become sayable in a German rule name.
      - **No `lang`, or one not in the table, opens no layer.** A caller that cannot say
        which language a file is in gets the English vocabulary, which is the behaviour
        this function had before the layer existed.
    """
    return [(value, why) for value, why, _ in _rule_id_scan(text, lang)]


def _rule_id_scan(text, lang=None):
    """`(id, why, unknown_tokens)` per finding. The one place the check is computed.

    Split out from `rule_id_findings()` so `rule_id_proposals()` can name the tokens
    without a second implementation of the vocabulary lookup. A second implementation
    is the failure mode this project has already met twice (DESIGN §5.3's one-writer
    argument, and `_to_document()`'s refusal to re-derive the masker's arithmetic):
    the two copies disagree first on whichever file nobody screened, and here that
    disagreement would be a proposal for a token the check does not actually object to
    — or worse, silence about one it does.

    `unknown_tokens` is empty for every finding except the vocabulary one, because the
    shape and length findings are about the id as a whole and have no offending token
    to name.
    """
    lang = (lang or "").strip().lower()
    extra = RULE_ID_VOCAB_BY_LANG.get(lang, frozenset()) if lang else frozenset()
    out = []
    for raw in RULE_ID_KEY.findall(text):
        value = raw.strip().strip("'\"")
        if not value:
            continue
        prefix, sep, rest = value.partition(":")
        body = rest if sep else value
        # A prefix that contradicts the path drops the layer. See the docstring.
        allowed_extra = frozenset() if (sep and prefix.strip().lower() != lang) else extra
        parts = [p for p in re.split(r"[_\-]", body) if p]

        # Shape first: these say something specific about *how* the name is wrong,
        # and a caller renaming it is better served by "that is a value" than by
        # "unknown word".
        shape = next((why for pattern, why in RULE_ID_RULES
                      if pattern.search(body)), None)
        if shape:
            out.append((value, shape, ()))
            continue
        if len(body) > RULE_ID_MAX_LEN:
            out.append((value, f"longer than {RULE_ID_MAX_LEN} characters — a "
                               "mechanism description is short; a phrase is not", ()))
            continue
        if len(parts) > RULE_ID_MAX_PARTS:
            out.append((value, f"more than {RULE_ID_MAX_PARTS} parts — that is a "
                               "phrase rather than a name", ()))
            continue

        # Then the vocabulary. This is the check that implements Prohibition 2's
        # actual criterion, and the one shape cannot: `perez_ruiz` and `street_type`
        # have the same shape.
        unknown = [p for p in parts
                   if p.lower() not in RULE_ID_VOCAB
                   and p.lower() not in RULE_ID_ALLOWED_TOKENS
                   and p.lower() not in allowed_extra
                   and not RULE_ID_CODE_TOKEN.match(p.lower())]
        if unknown:
            out.append((value, f"{len(unknown)} token(s) outside the mechanism "
                               "vocabulary — a rule_id names what the rule works "
                               "by, not what it contains; content belongs in terms "
                               "and pattern, and a name built only from mechanism "
                               "words cannot designate an individual",
                        tuple(unknown)))
    return out


#: Where a proposed entry goes, per token. The screener cannot decide the category —
#: that judgement is the review — so it names the two homes and lets the reviewer pick.
PROPOSAL_HOMES = (
    "RULE_ID_VOCAB            (an English mechanism or structure word)",
    f"RULE_ID_VOCAB_BY_LANG[{{lang!r}}]  (a clinical formula in that language)",
    "RULE_ID_ALLOWED_TOKENS   (a national identifier-scheme abbreviation)",
)


def rule_id_proposals(text, lang=None):
    """`(token, rule_id)` for every token the vocabulary did not recognise.

    DESIGN §6.1's option 3, chosen on the third widening for the fourth: the reviewer's
    job becomes judging a category rather than reconstructing one from a count. Three
    widenings out of four were made under a red baseline with the mutation harness
    refusing to run, and that is when a category is least well judged.

    **This is not printed by default, and the reason is a conflict option 3 did not
    notice.** As recorded, option 3 was to print the proposed entry "with the file and
    rule it came from" — but `sniff()` deliberately does *not* quote the id, because it
    may be the surface form itself and its message reaches terminals, CI logs and
    issues where nothing screens it (CLAUDE.md). An unrecognised token is precisely the
    candidate for being a surname, so printing it by default would put the least
    screened value on the least screened path. The resolution keeps both properties:
    the default output is unchanged, and the proposal lines are emitted only under an
    explicit `--propose`, whose banner says what the operator is about to read. That
    costs nothing the option was for — the reviewer reads the rule file anyway, which is
    published by path — and it keeps the automatic path quiet.

    Order is the file's, deduplicated on `(token, rule_id)`: one token in two rules is
    two lines, because which rule reached for it is what the category judgement needs.
    """
    seen, out = set(), []
    for value, _, unknown in _rule_id_scan(text, lang):
        for token in unknown:
            key = (token.lower(), value)
            if key not in seen:
                seen.add(key)
                out.append((token, value))
    return out


def format_proposals(path, lang, proposals):
    """The `--propose` lines for one file. Returns [] when there is nothing to propose."""
    if not proposals:
        return []
    homes = [h.format(lang=lang or "?") for h in PROPOSAL_HOMES]
    lines = [f"  {path}   (lang {lang or 'none'})"]
    for token, rule_id in proposals:
        lines.append(f'    "{token}",   # from rule_id {rule_id}')
    lines.append("    choose one home per token:")
    lines.extend(f"      - {h}" for h in homes)
    return lines

# ─── listed sniffer hits: false positives, and acknowledged violations ──────
# Five files trip the content sniffer for reasons that are not note text, on every
# single run. Printed in full they were five lines nobody read, which meant a sixth
# — a real one — would have arrived unnoticed. Same problem the SEALED line solved
# for BLOCKED: a permanent finding is not a finding.
#
# The list is data rather than code, and committed, so adding an entry is a diff
# someone can object to. Its rules are enforced in load_allowlist(), not documented
# and hoped for.
#
# `acknowledged` is a second list, added 2026-08-24, and it says the opposite thing
# about its files: the hit is REAL. It exists because a false-positive list is the
# wrong shape for a true positive, and the only two ways to clear one without such a
# list are both worse than the finding. Deleting the file destroys an arm's committed
# artefact to make a checker quiet. Filing it under `entries` writes "known false
# positive" next to something that is not one, which is a lie in the exact place a
# reader would go to check. So the third category is the honest one, and the price of
# using it is stated in the entry: `why_real` says why the hit is genuine, and
# `fixed_when` says what will make it go away. Both are mandatory, because an
# acknowledged violation with no stated exit is a permanent exemption wearing a
# temporary label.
#
# The two lists get opposite reporting, and that asymmetry is the design rather than
# an inconsistency. A false positive is permanent by nature — CLAUDE.md will always be
# Korean — so it is counted and not printed. An acknowledged violation is temporary by
# its own declaration, so it is printed in full on every run, `fixed_when` included.
# One is a status line and the other is a debt line, and a debt line that stops
# appearing is a debt nobody pays.
#
# What this must not become is a way to make findings go away. Three things hold it:
# the same path rules as `entries` (so it can never widen what the path rules
# publish), the same `sniff` pin (so a *different* hit on an acknowledged file is
# still UNEXPECTED and still gates), and a path may appear in only one of the two
# lists — a file cannot be both a false positive and a real violation, and an entry
# that claims both is refused rather than resolved.
ALLOWLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "screen_allowlist.json")

#: Which list an entry came from. Carried on the entry rather than kept in a second
#: mapping, so there is no way to hold an entry without holding what it claims. A dict
#: assembled by hand — every caller outside load_allowlist() — has no `kind` and reads
#: as a false positive, which is what every such caller meant before this existed:
#: acknowledgement is only ever reachable by putting an entry in the acknowledged
#: array of a committed file.
FALSE_POSITIVE = "false_positive"
ACKNOWLEDGED = "acknowledged"


class AllowlistError(Exception):
    """The allowlist itself is not acceptable. Screening does not continue.

    Refusing to run is the only safe response: the alternative is to drop the bad
    entry and keep screening, which produces a clean-looking report from a file that
    was tampered with. A screener that reports 'all known' on the strength of a list
    it also rejected would be worse than one that never had a list.
    """


def load_allowlist(path=None):
    """Read both lists and validate every entry. Returns {path: entry}, `kind` attached.

    One mapping rather than two, keyed on path, because the invariant that matters is
    that a path has exactly one verdict. Two mappings would make a path present in both
    representable, and then something has to decide which wins — at which point the
    honest answer ("the file is described twice and the descriptions contradict") has
    already been thrown away.

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

    All four apply to both lists, and the path rules apply to the acknowledged list for
    a sharper reason than to `entries`: acknowledging a hit is the one act here that
    admits the sniffer was right, so it is the one an author reaching for a quiet run
    would reach for first. It still cannot name a corpus path, a sealed path, a denied
    path or a pattern. The list can only ever describe a file the path rules already
    publish — the same guarantee `entries` has, and it is not weakened by the entry
    saying something worse about the file.

    Two rules are specific to the acknowledged list. It takes `why_real` and
    `fixed_when` instead of `why`: two fields rather than one because "this is a real
    violation" and "this is when it stops being one" are separate claims, and an entry
    with only the first is an exemption with no end. And `why` on an acknowledged entry
    is refused rather than ignored, because the way this list gets misused is by
    copy-pasting an entry across from `entries`, where the reason would be silently
    dropped and the two mandatory fields silently absent.
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

    # Both lists walk the same loop, so the path rules below cannot come to differ
    # between them by editing one branch. Indices are per-list, so an error message
    # names the entry a reader can count to in the file.
    listed = ([(FALSE_POSITIVE, i, e)
               for i, e in enumerate(data.get("entries", []))]
              + [(ACKNOWLEDGED, i, e)
                 for i, e in enumerate(data.get("acknowledged", []))])

    entries = {}
    for kind, i, entry in listed:
        p = entry.get("path", "")
        where = (f"{path} entry {i}" if kind == FALSE_POSITIVE
                 else f"{path} acknowledged entry {i}")
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
        # Before the per-kind fields, so the message for a path listed twice says that
        # rather than complaining about whichever field the second copy is missing.
        if p in entries:
            raise AllowlistError(
                f"{where}: {p!r} is listed twice. A path gets one verdict — and if the "
                "two entries are in different lists, they say the hit is a false "
                "positive and that it is a real violation. Delete the one that is "
                "wrong; there is no reading in which both are right.")
        if kind == FALSE_POSITIVE:
            if len(entry.get("why", "").split()) < 5:
                raise AllowlistError(
                    f"{where}: {p!r} needs a `why` that a reader can evaluate later.")
        else:
            if entry.get("why"):
                raise AllowlistError(
                    f"{where}: {p!r} has a `why`. An acknowledged entry takes "
                    "`why_real` and `fixed_when` instead — it is not claiming the hit "
                    "is harmless, so the field that would say so is refused rather "
                    "than ignored.")
            if len(entry.get("why_real", "").split()) < 5:
                raise AllowlistError(
                    f"{where}: {p!r} needs a `why_real` saying why the hit is a "
                    "genuine violation. Acknowledging one without stating what it is "
                    "makes the list indistinguishable from the false-positive list.")
            if len(entry.get("fixed_when", "").split()) < 5:
                raise AllowlistError(
                    f"{where}: {p!r} needs a `fixed_when` saying what will clear it. "
                    "Without it the entry is a permanent exemption labelled as a "
                    "temporary one, which is the only way this list can do harm.")
        entries[p] = dict(entry, kind=kind)
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


def rule_file_lang(path):
    """Which language's rule file is this? The stem of `rules/{lang}.yaml`, or None.

    Covers both `rules/es.yaml` and an arm's `.../rules/iter3/es.yaml` — the filename
    carries `{lang}` in both (`paths.rules` and `paths.armrules`, DESIGN §5.3).

    Returns None rather than guessing when the stem is not a language this screener has
    a layer for. None means "English vocabulary only", so an unrecognised filename is
    screened exactly as it was before the layer existed. That is the safe direction: the
    failure mode of guessing wrong here is a wider vocabulary for a file nobody
    classified.
    """
    stem = os.path.splitext(os.path.basename(path))[0].strip().lower()
    return stem if stem in RULE_ID_VOCAB_BY_LANG else None


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

    # Rule files only. The check is about rule *names*, and a rule_id appears in other
    # files (spans.jsonl, metrics.json) as a value copied from here — screening the origin
    # is what stops it, and screening the copies would report one mistake many times over.
    #
    # The pattern matches any `rules/` directory, not the top-level one alone, because an
    # arm's rule files are under the arm (paths.armrules, DESIGN §5.3):
    # results/{...}/{porting}/rules/iter3/es.yaml. Anchored to the top level it would pass
    # every agent-written file — and passing here is not rejection, it is the check never
    # running, which is the failure mode a structural check has to be built against
    # (tests/test_conftest.py). Keeping `rules/` in the arm path is what makes one pattern
    # cover both, and DESIGN §5.3 records that as a reason the component stays.
    if re.search(r"(^|/)rules/(iter[0-9]+/)?[^/]+\.ya?ml$", path.replace(os.sep, "/")):
        found = rule_id_findings(text, lang=rule_file_lang(path))
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
    """Sort hits into (known, acknowledged, unexpected, unpublishable, stale).

      - 'known' matches a false-positive entry, *and* matches the kind of hit that
        entry expects. Reported as a count.
      - 'acknowledged' matches an acknowledged entry the same way. Reported in full,
        every run, with the entry — the opposite treatment to 'known' and for the
        opposite reason: this one is a real violation and is supposed to be paid off.
        Tested before publishability on purpose. An acknowledged hit on a gitignored
        file is still reported as acknowledged rather than folded into a count, because
        someone wrote down that it is real and that statement outranks the accident of
        git not seeing the file today.
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
    reason is not a reason to excuse it. This holds for acknowledged entries too, and it
    is what stops one from becoming a blanket pass on its file: the entry covers the hit
    it names and nothing else, so a second, different violation in an already-
    acknowledged file still turns the run red.
    """
    paths = [p for p, _ in suspect]
    ignored = git_ignored(paths, root)
    tracked = git_tracked(paths, root)

    known, acknowledged, unexpected, unpublishable = [], [], [], []
    for path, why in suspect:
        entry = allowlist.get(path)
        if entry and why.startswith(entry["sniff"]):
            if entry.get("kind") == ACKNOWLEDGED:
                acknowledged.append((path, why, entry))
            else:
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
    return known, acknowledged, unexpected, unpublishable, stale


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
    ap.add_argument("--propose", action="store_true",
                    help="for each rule_id token the vocabulary did not recognise, "
                         "print a proposed entry with the rule it came from. QUOTES "
                         "THE TOKEN AND THE ID: run it in a terminal you are willing "
                         "to read a surface form in, never in CI.")
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
    known, acknowledged, unexpected, unpublishable, stale = partition_suspect(
        suspect, allowlist, a.root, a.allowlist)

    summary = f"{len(known)} known"
    if acknowledged:
        summary += f", {len(acknowledged)} acknowledged"
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

    # Its own line, always printed, never zero-suppressed — and printed in full, which
    # is the exact opposite of what happens to `known` twenty lines up. The reason for
    # the inversion is that these are real. A false positive that keeps printing trains
    # people to skip the section; an acknowledged violation that stops printing has been
    # forgotten, which is the only failure mode this category has. Zero is worth
    # printing too: it is the difference between "no outstanding violations" and "the
    # list is not being consulted", and those look identical if the line disappears.
    print(f"\nACKNOWLEDGED violations   : {len(acknowledged)}   "
          "(real, recorded, unfixed — read `fixed when`; does not gate, see below)")
    for p, why, entry in sorted(acknowledged):
        print(f"   {p}  <- {why}")
        print(f"      real because  {entry['why_real']}")
        print(f"      fixed when    {entry['fixed_when']}")

    # DESIGN §6.1's option 3, and off unless asked for — see rule_id_proposals(). The
    # files are taken from the suspect list rather than by re-walking the tree: a
    # vocabulary finding is what put a rule file there, so the list already names every
    # file with something to propose, and re-walking would be a second traversal that
    # could disagree with the one the report was computed from.
    if a.propose:
        blocks = []
        for p, why in sorted(suspect):
            if not why.startswith("rule_id shape"):
                continue
            lang = rule_file_lang(p)
            try:
                text = open(os.path.join(a.root, p), "rb").read(400_000).decode(
                    "utf-8", "ignore")
            except OSError:
                continue
            blocks.extend(format_proposals(p, lang, rule_id_proposals(text, lang)))
        print("\nPROPOSED VOCABULARY ENTRIES (--propose)")
        print("   These lines quote the token and the rule_id. An unrecognised token is "
              "the\n   best candidate for being a surface form, so this output does not "
              "belong in a\n   CI log, an issue or a paste. It is here because judging a "
              "category is cheaper\n   than reconstructing one, and every widening so "
              "far was judged under a red suite.")
        if blocks:
            for line in blocks:
                print(line)
        else:
            print("   nothing to propose — no rule file has an unrecognised token.")

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
    #
    # Acknowledged violations do not fail either, and this is the uncomfortable one,
    # because unlike every other non-gating category these hits are real. The argument
    # is the same argument, and it is about which gate does the work. A red exit code
    # here does not fix a rule_id the model already wrote into a committed artefact; it
    # only stops the exit code from being able to say anything about the *next* change,
    # and it took the mutation gate down with it — `tests/mutations/run.py` aborts on a
    # non-green baseline, so a permanently-red suite does not make the project stricter,
    # it makes 170 mutations unmeasurable. The gate that holds this category is the
    # review gate, not the exit code: a committed diff to a JSON file, literal path,
    # pinned sniff kind, and two prose fields that a reader can disagree with. Cheap to
    # add and expensive to defend is the right shape here. Cheap to add and impossible
    # to distinguish from a false positive was the shape before.
    if blocked or unexpected:
        sys.exit(1)
