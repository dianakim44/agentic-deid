# `port-oneshot` / de-grascco — 첫 실행 기록

축: `de-grascco` / `R` / `sup-free` / `port-oneshot`, split `splits/de-grascco.json`
(seed 20260921, dev 19문서 · 410 in-scope gold), dev fold. 실행 2026-09-21.

명령:

```
python3 tools/run_arm.py --corpus de-grascco --lang de \
  --model-id us.anthropic.claude-opus-4-5-20251101-v1:0
```

> **결과: 채점됨 (scored).** 종료 코드 0. `metrics.json` · `spans.jsonl` ·
> `rules/iter1/de.yaml` · `window_freeze.json` 이 쓰였다. **es-meddocan 의 첫
> `port-oneshot` 과 달리 형식 실패가 아니다** — 2026-09-17 의 외곽 펜스 1겹 제거
> 정책(DESIGN §6.8)이 적용된 뒤 처음 도는 새 코퍼스이고, 응답은 `load_rules` 를
> 통과했다. 그 정책이 이 arm 에서 무엇을 했는지는 §4 에 적는다.

회차는 돌리지 않았다. 이 arm 은 정의상 1회 호출이다(DESIGN §4).

---

## 1. arm 의 결과

### 비용과 모델 식별

| | |
|---|---|
| LLM 호출 | 1 |
| prompt tokens | 14,868 |
| completion tokens | 2,212 |
| wall time | 28.801 s |
| `model_id` | `us.anthropic.claude-opus-4-5-20251101-v1:0` (dated) |
| `model_id_reported` | `claude-opus-4-5-20251101` — 응답이 dated id 에 동의했다 |
| 생애주기 | `ACTIVE`, start-of-life 2025-11-24 |

`cost_to_date` 는 `cost` 와 같다 — 회차가 하나이므로 그것이 정상이고, 스키마 7 이
그 둘을 갈라 적는 이유이기도 하다(DESIGN §5.0).

### 누출률 — headline

| | 값 | 모드 |
|---|---|---|
| **누출률 (headline)** | **0.4146** (170 / 410) | `fully_covered` |
| 누출률 하한 | 0.3683 (151 / 410) | `relaxed` |
| precision / recall / F1 | 0.5548 / 0.6293 / 0.5897 | `relaxed`, 1:1 배정 |

CLAUDE.md 의 규약대로 누출률·상보성 분해가 headline 이고 F1 은 아니다. 두 모드의
차이 19 스팬은 경계가 어긋난 예측이 `relaxed` 에서만 덮인 것이다.

**이 0.4146 은 뮤테이션 검증이 없는 로더 위에서 나왔다 (2026-09-21 실행 시점).**
`src/corpora/grascco.py` 에는 앵커된 뮤테이션이 한 건도 없었다 — 로더 테스트 38건은
통과했지만, 그 테스트들이 실패할 수 있다는 것은 어디서도 확인되지 않은 상태였다.
분모 410 은 그 로더가 읽은 gold 다. **2026-09-22 에 8건을 붙여 측정했고 8/8 caught,
survived 0 이다** (`tests/mutations/README.md` §"The other two loaders"). 그래서 이 값은
소급해서 es-meddocan 결과가 처음부터 갖고 있던 지위를 얻는다. 하나라도 생존했다면
그 뮤테이션이 닿는 칸부터 다시 봐야 했다. 아직 남은 구멍 하나는 기록해 둔다: 봉인
루트가 비었는데 봉인 읽기가 허가된 경우의 `SealError` 는 테스트도 뮤테이션도 없다.
**2026-09-22 에 부채로 등록했다** — `docs/notes/sealed-eval-preflight.md`
§"Open debt", 항목 14. 이 코퍼스의 봉인 개방 전에 닫는다. 단발 arm 에는 무관하다
(이 실행은 dev 를 읽고 `sealed/` 를 열지 않는다).

### 상보성 분해

태거가 없는 arm 이므로 `tagger_only` · `both` · `joint_only` 는 구조적으로 0 이다.
정보가 있는 칸은 `rules_only` 와 `neither` 두 개다.

| | `fully_covered` | `relaxed` |
|---|---|---|
| `rules_only` | 240 | 259 |
| `neither` | 170 | 151 |
| `tagger_only` · `both` · `joint_only` | 0 | 0 |

층별로는 `regex_checksum` 173 · `context_cue` 170 (`fully_covered`), 두 층이 함께
덮은 것이 103 이다. `gazetteer` 는 0 — 모델이 사전층 규칙을 하나도 쓰지 않았다.
규칙 파일 27개 규칙의 층 분포가 `context_cue` 18 · `regex_checksum` 9 이므로,
사전층이 비어 있는 것은 채점의 결과가 아니라 작성의 결과다.

### 유형별 누출률 (`fully_covered`)

| 유형 | gold | 누출 | 누출률 | tp | fp |
|---|---|---|---|---|---|
| DATE | 221 | 57 | 0.258 | 163 | 18 |
| NAME | 116 | 64 | 0.552 | 52 | 43 |
| LOCATION_AREA | 26 | 11 | 0.423 | 15 | **150** |
| CONTACT | 11 | 7 | 0.636 | 4 | 3 |
| ID | 11 | 9 | 0.818 | 2 | 0 |
| ORGANISATION | 11 | 11 | **1.000** | 0 | 5 |
| LOCATION_STREET | 9 | 6 | 0.667 | 3 | 6 |
| AGE | 5 | 5 | **1.000** | 0 | 1 |

AGE 는 `sparse`(gold 5)로 표시되지만 DESIGN §9.4 대로 누출률 분모에 남는다.
ORGANISATION 은 sparse 가 아니면서 전량 누출이다 — 규칙 2개가 이 유형을 겨냥했고
둘 다 tp 0 이다.

### 정밀도를 삼킨 규칙 하나

226 개 오탐 중 **144 개가 규칙 하나**에서 나왔다.

| 규칙 | tp | fp |
|---|---|---|
| `de:aus_cue_location` | 5 | 144 |
| `de:herr_frau_prefix_name` | 36 | 24 |
| `de:date_vom_cue` | 97 | 14 |
| `de:dr_prefix_name` | 16 | 13 |

`de:aus_cue_location` 은 독일어에서 가장 흔한 전치사 중 하나를 단서로 삼아 그 뒤를
지명으로 표시한다. 전치사가 지명 앞에만 오지 않으므로 오탐이 문서 전체에 퍼진다.
이것이 DESIGN §7 의 언어축 예측 — **독일어는 baseline cue 가 낮다** — 가 규칙층에서
구체적으로 어떻게 나타나는지의 한 사례다. 대문자는 모든 명사에 붙으므로 고유명사를
가리지 못하고, 그래서 작성자는 전치사·경칭 같은 문맥 단서로 옮겨가며, 그 단서는
독일어에서 훨씬 빈번하다. 중복 예측 102 건도 같은 원인 쪽이다(여러 문맥 단서 규칙이
같은 스팬을 낸다).

이것은 **관찰이고 수정 대상이 아니다.** 이 arm 은 1회 호출이므로 회차 2 가 없고,
dev 오류를 보고 규칙을 손으로 고치는 것은 arm 의 결과를 바꾸는 일이다.

### 문서유형별 (schema 10, DESIGN §7)

이 arm 이 새 축의 첫 사용처다. 다중 라벨이므로 행의 합이 fold 가 아니다 — dev 19문서
중 17문서가 라벨을 받았고 문서당 평균 2.0개다. gold 합계가 980 인 것은 겹침이고
모순이 아니다(fold 는 410).

| 라벨 | 문서 | gold | 누출 | 누출률 (`fully_covered`) | (`relaxed`) |
|---|---|---|---|---|---|
| `progress_note` | 7 | 171 | 84 | **0.491** | 0.439 |
| `outpatient` | 6 | 156 | 72 | 0.462 | 0.410 |
| `laboratory` | 7 | 185 | 69 | 0.373 | 0.351 |
| `radiology` | 13 | 286 | 99 | 0.346 | 0.297 |
| `tumour_board` | 2 | 83 | 21 | 0.253 | 0.229 |
| `pathology` | 3 | 99 | 19 | **0.192** | 0.182 |
| `discharge` | 0 | 0 | 0 | — | — |
| `operation_report` | 0 | 0 | 0 | — | — |
| *(unlabelled)* | 2 | 29 | 9 | 0.310 | 0.310 |

**가장 높은 칸과 가장 낮은 칸이 30 포인트 차이다** (progress_note 0.491 대 pathology
0.192). 축이 작동한다는 것까지가 이 arm 이 말할 수 있는 것이고, **그 차이가 문서유형
때문이라는 것은 말할 수 없다** — 세 가지가 막는다:

1. 라벨이 겹친다. `laboratory` 7문서와 `radiology` 13문서는 별개 표본이 아니다.
2. 칸이 작다. `pathology` 는 3문서, `tumour_board` 는 2문서다.
3. 유형별 PHI 구성이 다르다. 유형별 차이와 PHI 유형 구성의 차이가 갈리지 않는다.

DESIGN §7 이 이 분해를 **secondary** 로 사전등록한 이유가 1·2 다. 사전등록된 대로
headline 에 닿지 않는다.

### 종료 규칙 — δ 는 여기서 작동하지 않고, 작동할 필요도 없다

`termination` 은 `not_applicable` 이다: `iterations: 1`, `improvements: []`,
`converged: false`. 관측이 하나면 1차 차분이 없으므로 어떤 문턱도 조회되지 않는다.

δ 는 그래도 기록된다 — **0.063415** (= max(0.005, 26/410)), `n_dev: 410`, k 2,
ceiling 8. `not_applicable()` 이 생략이 아니라 함수인 이유이고(DESIGN §3), 이 값은
이 코퍼스에서 반복 arm 이 마주할 문턱이 6.34 포인트라는 뜻이다. es-meddocan 의
0.005 의 12.7 배다. 사용자가 실행 전에 물은 것이 이것이고, 답은 **단발에는 무관,
반복에는 큰 문제**다. DESIGN §3 의 2026-09-21 블록이 그 귀결을 arm 이전에 적었다.

---

## 2. 별도 관찰 — arm 의 결과가 아니다

### 규칙 이름 8개가 기제 어휘 밖이다

`tools/release_screen.py` 가 27개 규칙 중 8개의 이름에서 어휘 밖 토큰을 하나씩
찾았다. `tools/screen_allowlist.json` 의 `acknowledged` 에 8 로 핀 되어 있고, 판정은
그 항목에 있다 — 여섯은 기각(표현가능성 검사 단계 (1)), 하나는 근사 동의어 기각,
하나는 **미판정**이다. 미판정 하나는 독일어 층이 이미 약어쌍을 두 개 가지고 있다는
사실(`str`/`strasse`, `plz`/`postleitzahl`)과 충돌하므로, 일곱 번째 확장은 그
약어쌍 질문으로 논의되어야 하고 이 파일로 논의되어서는 안 된다.

**es-meddocan 계열과 다른 모집단이다.** 저쪽 다섯 항목은 사람 이름·지명이
규칙 이름에 들어간 경우다. 여기 여덟은 전치사·역할어·식별자 스킴 이름이고, 개인을
지목할 수 있는 토큰이 하나도 없다. 그래서 상속하지 않고 2026-09-21 에 개별 판정했다.

### `tree: dirty` — dry-run 은 clean 이었다

`run` 블록은 `tree: dirty` 를 적었고, 직전 `--dry-run` 은 clean 을 보고했다. 모순이
아니다: 드라이버가 `results/` 밑에 파일을 쓴 뒤에 run 블록을 만들므로, 그 시점의
트리에는 이 arm 자신의 산출물이 있다. 커밋 해시는 `e6b5a51` 로 정확하다.

---

## 3. 이 실행에 없었던 것

- **Profiler · Mapper · LexiconBuilder 호출 없음.** `port-oneshot` 은 §1.1(과제
  프레임)과 §1.2(현재 규칙 파일, 회차 1 에서 빈 상태)만 본다(DESIGN §4). 호출 1회가
  그것이다.
- **`rules/de.yaml` 없음.** 형식 예시로서의 `rules/{lang}.yaml` 은 필요하지 않았다
  (DESIGN §5.3) — `--lang de` 는 `corpus_rule_langs` 에 이미 선언돼 있다.
- **`mappings/de-grascco.yaml` 없음.** 유형 대응은 로더가 갖는다.
- **`annotation_encoding` 축 값 없음.** GraSCCo 는 UIMA CAS JSON 으로 읽히지만
  naming.yaml 에 그 값이 없고, 오늘 필요하지도 않다 — Profiler 가 돌지 않았으므로
  인코딩 이름이 어떤 결과 경로·스팬 값에도 들어가지 않는다. Profiler 를 이 코퍼스에
  돌리는 arm 이 먼저 그 값을 naming.yaml 에 넣어야 한다.
- **sealed fold 접근 없음.** test 12문서는 열리지 않았고 이 실행의 어느 경로도
  `sealed/` 를 읽지 않는다. `results/sealed_eval_log.md` 에 append 할 것이 없다.

---

## 4. 외곽 펜스 정책이 여기서 한 일

DESIGN §6.8 의 "외곽 코드펜스 1겹 제거" 는 2026-09-17 에 사전등록됐고 이 arm 이
그 뒤 처음 도는 새 코퍼스다. `agent_calls.jsonl` 에 도착한 것과 읽힌 것이 함께
기록되며(§6.8 "The record says both what arrived and what was read"), 이 호출은
`load_rules` 를 통과했다. **이 하나로 정책의 효과를 주장할 수 없다** — 1 회 호출은
비율이 아니고, §6.9 가 측정한 역할별 펜스율은 별도 프로브의 값이다. 기록으로만 남긴다.

---

## 5. 남은 것

- 뮤테이션 전량 실행은 끝났다(`src/` 가 네 모듈 + 신규 둘이었던 그 변경들에 대해).
  2026-09-22 의 전량은 `418e17ccde77` 에서 **211/211 caught, survived 0**, 8샤드
  2.83시간, 스위트 2171. 위 §1 의 8건이 그 안에 들어 있고 scope 실행과 같은 값이다.
  킬 카운트를 인용할 수 있게 된 것이 이 실행의 내용이다.
- 일곱 번째 어휘 확장: 위 §2 의 약어쌍 질문. 이 arm 의 결과를 보면서 결정하지
  않는다.
- `de-grascco` 의 다음 rung(`port-loop`)은 δ = 0.0634 를 마주한다. DESIGN §3 의
  2026-09-21 블록이 그 귀결(이르게 `converged` 로 끝날 가능성, 그리고 그것이
  보고 대상이라는 것)을 arm 이전에 적어 두었다.
