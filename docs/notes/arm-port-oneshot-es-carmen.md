# `port-oneshot` / es-carmen — 첫 실행 기록

축: `es-carmen` / `R` / `sup-free` / `port-oneshot`, split `splits/es-carmen.json`
(seed 20260929, 11 strata, dev 400문서 · 1,429 in-scope gold), dev fold.
실행 2026-10-01.

명령:

```
python3 tools/run_arm.py --corpus es-carmen \
  --model-id us.anthropic.claude-opus-4-5-20251101-v1:0
```

`--lang` 이 없다. 이것이 이 기록의 이유다 — **DESIGN §5.6 의 첫 실행이고, 선언된
언어가 둘인 첫 arm 이다.** 앞의 네 arm 기록은 모두 `--lang` 을 썼고
(`arm-port-oneshot-de.md` 의 `--lang de`), 그 플래그는 2026-09-28 에 거절로
바뀌었다: 호출 수를 명령줄이 정하면 `llm_calls` 가 코퍼스 선언과 어긋날 수 있다.

> **결과: 채점됨 (scored).** 종료 코드 0. `metrics.json` · `spans.jsonl` ·
> `rules/iter1/es.yaml` · `rules/iter1/cat.yaml` · `window_freeze.json` ·
> `agent_calls.jsonl` 이 쓰였고 `format_failure.json` 은 쓰이지 않았다.
> 규칙 파일이 **둘**인 것이 앞의 네 arm 과 다른 유일한 산출물 차이다.

회차는 돌리지 않았다. 이 arm 은 정의상 언어당 1회 호출이다(DESIGN §4, §5.6).

---

## 1. §5.6 이 실제로 한 일

| | |
|---|---|
| 선언된 언어 | `es`, `cat` (`config/naming.yaml`, 선언 순서) |
| 창 freeze | **1회** — 두 호출이 같은 창을 본다 |
| 호출 | **2회**, 선언 순서대로 |
| 채점 | **1회**, 두 파일을 함께 올린 상태로 |
| `cost.llm_calls` | **2** |
| `agent_calls.jsonl` | **2줄**, `prompt_reference.lang` 이 `es` → `cat` |

호출별 비용은 14,871 + 2,334 토큰 / 29.126 s 와 14,864 + 1,778 토큰 / 24.971 s 이고,
합이 `cost` 블록과 일치한다 (29,735 + 4,112 / 54.336 s).

### `prompt_sha256` 가 두 줄에서 같은 것은 정상이다

두 줄의 `prompt_sha256` 이 **동일**하다. 결함처럼 보이므로 확인한 뒤 적는다:
`src/sample.py` 의 `WINDOW_HASH_FIELDS` 가 해시하는 것은 **템플릿 파일**
`docs/prompts/rule_author.md` 이고 렌더된 프롬프트가 아니다. 그러므로 한 arm 의 두
호출에서 같은 값이 나오는 것이 옳고, 그 동일성은 **하나의 고정된 템플릿이 두 언어를
모두 처리했다는 증거**다. 호출을 구별하는 것은 `prompt_reference.text_sha256` 이고
그 둘은 **다르다**.

### 두 파일이 실제로 올라갔다는 증거는 네 개다

| 증거 | 값 |
|---|---|
| `run.rules_version` | `{'cat': 1, 'es': 1}` — 두 키가 다 있다 |
| `run.rules_source` | 두 경로를 모두 적는다 |
| 파싱된 규칙 | es **30** (context_cue 16 / regex_checksum 13 / gazetteer 1), cat **23** (14 / 8 / 1) |
| 발화한 규칙 | es **13**, cat **8** → 53 중 21. **32개가 침묵한다** |

버전 맵만으로 추론하지 않는다: 카탈루냐어 23개 중 8개가 스팬을 냈고, 그것은 파일이
파싱되고 실행되지 않았으면 일어날 수 없다.

### 선언된 언어의 규칙 파일이 비면 — 세 변형

| 시도 | 파일 | 결과 |
|---|---|---|
| (i) 빈 파일 | 0바이트 | **거절** — §5.2 언어 선언 검사 |
| (ii) `rules: []`, `lang:` 키 없음 | 불완전 | **거절** — 역시 §5.2, §5.6 이 아니다 |
| (iii) `version: 1 / lang: cat / rules: []` | 형식 정상, 규칙 0개 | **수용** |

(ii) 를 §5.6 의 거절로 읽으면 안 된다 — 더 앞의 검사에 걸린 것이다. (iii) 이 실제
답이고 **설계상 수용**이다: §5.6 의 거절은 *선언됐는데 파일이 없는* 언어를 위한
것이고, `src.rules.load_for_corpus` 가 `RuleSet.versions` 를 보고 없는 언어를 한
번에 모두 이름 지어 거절한다. 선언돼 있고 규칙을 0개 쓴 언어는 **측정값**이다 —
카탈루냐어 규칙만 잡을 수 있는 것에 대해 누출 1.000 을 받고, 설계는 그것이 막히는
것보다 보이는 것을 원한다. 채점 경로 셋(`run_fold` · `run_sealed_eval` ·
`tools/check_rules.py`)이 모두 그 한 거절을 지난다.

---

## 2. arm 의 결과

### 비용과 모델 식별

| | |
|---|---|
| LLM 호출 | **2** |
| prompt tokens | 29,735 |
| completion tokens | 4,112 |
| wall time | 54.336 s |
| `model_id` | `us.anthropic.claude-opus-4-5-20251101-v1:0` (dated) |
| `model_id_reported` | `claude-opus-4-5-20251101` — 응답이 dated id 에 동의했다 |
| 생애주기 | `ACTIVE`, start-of-life 2025-11-24 |

`cost_to_date` 는 `cost` 와 같다 — 회차가 하나이므로 정상이다. **다른 네 arm 과
나란히 읽을 때 호출 수가 2 인 것을 먼저 본다**: 1회 arm 들은 14,000 안팎의 prompt
토큰과 26–37 s 를 썼고, 이 arm 은 그 두 배다. CLAUDE.md 가 비용을 품질과 함께
적으라고 하는 이유가 여기서 작동한다 — 2배 비용으로 얻은 값과 1.05배로 얻은 값은
다른 결과다.

### 누출률 — headline

| | 값 | 모드 |
|---|---|---|
| **누출률 (headline)** | **0.6298** (900 / 1,429) | `fully_covered` |
| 누출률 하한 | 0.6011 (859 / 1,429) | `relaxed` |
| 문서 단위 누출 | 0.6263 (186 / 297) | `fully_covered` |

### 상보성 분해

| | |
|---|---|
| rules only | 529 |
| tagger only | 0 |
| both | 0 |
| joint only | 0 |
| neither | 900 |
| 분모 | 1,429 |

`tagger_only` · `both` · `joint_only` 가 0 인 것은 이 셀에 tagger 가 없기 때문이다
(`layers_covered`: `regex_checksum` 501 · `context_cue` 28 · `gazetteer` **0** ·
`tagger` **0**). `joint_only` 는 `relaxed` 에서는 구성상 항상 0 이다.

**gazetteer 층이 0 인 것은 기록해둔다.** 두 파일이 gazetteer 규칙을 각각 1개 썼고
(`es:hospital_gazetteer`, `cat:hospital_gaz`) 둘 다 발화하지 않았다.

### P/R/F1 — headline 이 아니다

| | precision | recall | f1 |
|---|---|---|---|
| overall | 0.635 | 0.370 | 0.468 |
| macro (10유형) | 0.267 | 0.300 | 0.252 |

누출률·상보성은 **예측 합집합**으로, P/R/F1 은 **1:1 배정**으로 낸다. 분모가 다른
것이 설계이고 불일치가 아니다 (DESIGN §9.3).

### 유형별 누출률 (`fully_covered`)

| 유형 | n (in scope) | 누출률 | recall |
|---|---|---|---|
| `DATE` | 1,013 | 0.639 | 0.361 |
| `AGE` | 159 | 0.157 | 0.843 |
| `ORGANISATION` | 88 | 1.000 | 0.000 |
| `ID` | 57 | 1.000 | 0.000 |
| `LOCATION_AREA` | 51 | 1.000 | 0.000 |
| `NAME` | 35 | 0.200 | 0.800 |
| `PROFESSION` | 12 | 1.000 | 0.000 |
| `OTHER` | 7† | 1.000† | 0.000† |
| `LOCATION_STREET` | 6† | 1.000† | 0.000† |
| `CONTACT` | 1† | 0.000† | 1.000† |

† = 희소, n ≤ 8 (DESIGN §9.4). `sparse` 블록은 세 유형 · gold 14 로 적혀 있다.
희소 유형은 총계에 남아 있으므로 유형별 행이 overall 행으로 합산되지 않는다.

### 중복 예측 441건 — 두 날짜 정규식이 겹친다

`duplicate_predictions: 441`, `assignment_slack: 0`. 원인은 두 파일이 같은 날짜
패턴을 각각 쓴 것이다: `es:date_dmy_slash` 와 `cat:date_dmy_slash` 가 각각 332회
발화하고 323 tp 다. 그래서 `by_rule` 의 tp 합은 919(es 529 + cat 390)이고 overall
tp 는 529 다 — 귀속은 규칙별이고, 채점기는 **바이트 동일 스팬만** 합치므로 headline
은 이중계수하지 않는다 (DESIGN §9.3, §4). 경계가 다른 예측을 합치는 것은 병합
정책의 일이다.

**이것은 §5.6 이 처음 만든 현상이다.** 선언된 언어가 하나인 arm 에서는 한 파일이
한 패턴을 한 번만 쓰므로 441 같은 값이 나올 자리가 없다. 두 언어가 같은 날짜 표기를
쓰는 코퍼스에서 언어당 1회 호출을 하면 **같은 정규식을 두 번 받는 것이 기본값**이고,
병합 정책이 union 인 한 그것은 점수에 해를 주지 않지만 비용에는 준다.

### false positive 기회

| | |
|---|---|
| gold PHI 가 없는 문서 | 103 |
| 그 문서들 안의 예측 | 26 |

103개 문서는 놀고 있지 않다 — gold 스팬 없이 false positive 를 셀 수 있는 유일한
자리다.

### 종료 규칙

| | |
|---|---|
| `iterations` | 1 |
| `delta` | 0.01819454163750875 |
| `delta_spans` | 26 |
| `delta_floor` | 0.005 |
| `k` / `ceiling` | 2 / 8 |
| `n_dev` | 1,429 |

δ_corpus = max(0.005, 26 / 1,429) = **0.0182** (DESIGN §9.4). 1회 arm 이므로 δ 는
여기서 작동하지 않는다 — 기록되는 이유는 이 값이 이후 `port-loop` 의 정지 기준이
되기 때문이다.

### 채점 분모가 동결된 split 과 일치한다

`counts.gold.in_scope` **1,429**, `excluded` 132 (**8.456%**). `splits/es-carmen.json`
의 dev 는 `n_spans` 1,561 · `in_scope` 1,429 · excluded 132 이고 바이트 단위로
같다. 배제 132 는 §9.1 의 두 유형(82 + 50)이고, 스팬은 `excluded=True` 로 남아
`phi_type` 없이 `n_spans_excluded` · `spans_by_excluded_type` 에 계수된다.

봉인 뒤의 dev 구성은 동결된 split 파일에서 복원했다 — **`sealed/` 는 열지 않았다**:
400문서(297 with PHI / 103 without), 토큰 88,211(중위 159), 1,000토큰당 in-scope
16.2, 11 strata, `language_label` es 340 / bi 53 / cat 7,
`filename_doctype` IR 240 / IA 123 / IT 35 / CC 1 / IE 1.

---

## 3. 별도 관찰 — arm 의 결과가 아니다

### 규칙 이름 9개가 기제 어휘 밖이었고, 그 분포가 앞의 arm 들과 다르다

스크리너가 두 파일에 9건을 냈다 (cat 4 / es 5, 토큰 인스턴스 6개를 포함). **9건 중
6건이 "자매 언어 층이 짝을 이미 갖고 있는 자리"** 였다 — 앞의 arm 들에서는 대부분
내용어였으므로 다른 모집단이다. 7개를 들이고 2개를 거부했으며, 판정은
`tools/screen_allowlist.json` 의 두 ACKNOWLEDGED 항목에 있다. **어느 토큰도 사람
이름도 장소 이름도 아니다** — 경칭 · 필드 표지 · 체계 약어뿐이다.

- 들임: 사회보장번호 체계 약어 1개를 `RULE_ID_ALLOWED_TOKENS` 로 (같은 체계의 약어
  셋이 이미 있었다), 카탈루냐어 3개를 `cat` 층으로, 스페인어 2개를 `es` 층으로.
- 거부: 영어 식별자 낱말 1개(step (1), `id` 가 정확히 말한다)와 의사 경칭 약어.

### 경칭 약어 하나에서 어휘 표가 자기와 어긋나고, 그것이 이 arm 안에서 보인다

`es` 층은 스페인어 의사 경칭 약어와 그 여성형을 **창설 때부터** 갖고 있고, `de` 층의
같은 약어는 2026-09-21 에 step (1) 로 거부됐다. 창설 집합은 그 검사를 받은 적이
없다. 이 arm 이 그것을 처음 드러낸 이유는 **같은 실행의 es.yaml 이 그 약어로 규칙
둘을 이름 짓고 통과하는데 cat.yaml 의 같은 약어는 걸린다**는 것이다 — 한 모델, 한
창, 한 프롬프트, 두 언어. 2026-10-01 의 결정은 **기록하고 어느 집합도 바꾸지 않는
것**이고, 근거와 기각된 두 수리안은 cat.yaml 의 allowlist 항목에 있다.

### `by_document_type` 이 이 코퍼스에 없다 — 그리고 그것이 하필 이 코퍼스다

schema 10 은 `by_document_type` 을 **선택적**으로 두고, 쓰이지 않은 것은 "측정되지
않았다" 의 기록이다. `config/document_types.yaml` 의 `corpora` 에는 **de-grascco
하나뿐**이므로 이 arm 의 `metrics.json` 에는 블록이 없다. 설계대로 동작한 것이고
결함이 아니다.

그래도 적어두는 이유는 **es-carmen 이 문서유형으로 층화된 코퍼스**라는 것이다 —
split 이 `filename_doctype_x_language_label` 로 갈렸고 유형이 다섯(IR · IA · IT ·
CC · IE)이다. 그러므로 이 코퍼스에는 문서유형 축이 **있고**, 보고 층에는 없다.
갈라지는 지점은 축의 출처다: `document_types.yaml` 의 기제는 **본문 단서 패턴**이고,
es-carmen 의 라벨은 **파일명**에서 온다. 둘을 잇는 것은 DESIGN §7 의 결정이고 이
arm 의 일이 아니므로 고치지 않고 남긴다.

---

## 4. 이 실행에 없었던 것

- **test fold.** `sealed/es-carmen/` 은 열지 않았다. dev 구성은 동결된 split 파일에서
  나왔다.
- **tagger.** 이 셀은 `R` 이므로 규칙만이다. 상보성 분해의 세 칸이 0 인 이유이고,
  결합 구성이 recall 에서 구성요소를 by construction 으로 이기는 것은 병합이 union
  인 다음 셀에서 볼 일이다.
- **회차.** `port-oneshot` 은 1회 호출 arm 이다. 규칙 이름을 다시 쓸 기회가 없으므로
  9건 중 거부된 2건은 이 arm 안에서 수리될 수 없다.
- **표면형.** `spans.jsonl` 1,274줄에는 텍스트 필드가 없다
  (`agent_actions/detector/doc_id/end/layer/phi_type/rule_id/score/start`).
  `agent_calls.jsonl` 은 `.gitignore:40` 으로 추적되지 않는다.

## 5. 남은 것

- `by_document_type` 과 `filename_doctype` 을 잇는가 — DESIGN §7 의 결정.
- 경칭 약어의 층간 불일치 — `es`/`de` 층의 결정이고 이 arm 의 것이 아니다.
- 두 언어가 같은 패턴을 두 번 받는 것(중복 441)을 §5.6 이 비용 면에서 다룰지.
