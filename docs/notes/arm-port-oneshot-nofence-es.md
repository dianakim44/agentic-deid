# `port-oneshot-nofence` / es-meddocan — 실행 기록

축: `es-meddocan` / `R` / `sup-free` / `port-oneshot-nofence`, split
`splits/es-meddocan.json`, dev fold. 실행 2026-08-11 (UTC 21:33).

명령:

```
python3 tools/run_arm.py --corpus es-meddocan --lang es \
  --model-id us.anthropic.claude-opus-4-5-20251101-v1:0 \
  --porting port-oneshot-nofence
```

> **결과: 채점됨 (`outcome: scored`).** 종료 코드 0. `metrics.json` 과 `spans.jsonl`
> 이 쓰였고 `format_failure.json` 은 쓰이지 않았다. 형식 절을 고친 것이 목표한 바를
> 정확히 했다 — 응답은 펜스 없는 순수 YAML 이고, `load_rules` 가 받아들였다.

이 arm 이 무엇이고 왜 `port-oneshot` 과 다른 경로인지는
`docs/notes/arm-port-oneshot-es.md` §5 와 DESIGN §4 에 있다. 요약하면: 같은 rung,
다른 프롬프트. 재시도가 아니다 (§10 A2).

---

## 1. 창 (window)

새 arm 이므로 자기 창을 새로 얼렸다. `revision: 1`.

| | |
|---|---|
| `prompt_sha256` | `ddc48bdb0a0c…` |
| `sampling_sha256` | `4c0e2cc725d3…` |
| `sections_shown` | 1.1, 1.2 |
| `sections_empty` | 1.3, 1.4 |
| `sampling_applied` | `false` |

`window_drift()` 가 `[]` 를 돌려준다 — **이 arm 은 자신이 호출한 것을 정확히
얼렸다.** `port-oneshot` 쪽은 여전히 `['prompt_sha256']` 을 보고하고, 그것도
올바르다: §2 가 그 arm 이 호출한 **뒤에** 움직였고, 이미 호출한 arm 의 기록을 다시
얼리는 일은 없다 (DESIGN §6.3). 두 기록이 서로 불일치하는 것이 규율이 작동하는
모습이다.

§§1.3·1.4 가 비어 있는 것은 이 rung 의 정의다 (DESIGN §4). 첫 실행과 동일하므로
두 실행의 차이는 형식 지시 하나로 귀속된다.

---

## 2. 산출물 — 규칙 27개

`version: 1`, `lang: es`. 층별 분포:

| layer | 개수 |
|---|---|
| `context_cue` | 14 |
| `regex_checksum` | 12 |
| `gazetteer` | 1 |

`phi_type` 축 10개 중 9개를 건드렸다. `OTHER` 에는 규칙이 없고, `PROFESSION` 에는
하나 있으나 아무것도 잡지 못했다.

`rule_id` 는 전부 접두어 없이 왔고 로더가 `es:` 를 붙였다 — 지난 라운드의 수정이
유지된 것이다. 규칙 이름을 층까지만 적는다 (표면형 인용 금지):

| `rule_id` | layer | `phi_type` |
|---|---|---|
| `doctor_prefix` | `context_cue` | NAME |
| `don_dona_prefix` | `context_cue` | NAME |
| `paciente_cue` | `context_cue` | NAME |
| `familiar_cue` | `context_cue` | NAME |
| `firmado_cue` | `context_cue` | NAME |
| `dni_checksum` | `regex_checksum` | ID |
| `nie_checksum` | `regex_checksum` | ID |
| `nuss_pattern` | `regex_checksum` | ID |
| `nhc_cue` | `context_cue` | ID |
| `nass_cue` | `context_cue` | ID |
| `date_dmy_slash` | `regex_checksum` | DATE |
| `date_dmy_dash` | `regex_checksum` | DATE |
| `date_dmy_dot` | `regex_checksum` | DATE |
| `date_spanish_month` | `regex_checksum` | DATE |
| `date_abbrev_month` | `regex_checksum` | DATE |
| `nacido_cue` | `context_cue` | DATE |
| `phone_pattern` | `regex_checksum` | CONTACT |
| `phone_cue` | `context_cue` | CONTACT |
| `email_pattern` | `regex_checksum` | CONTACT |
| `calle_cue` | `context_cue` | LOCATION_STREET |
| `domicilio_cue` | `context_cue` | LOCATION_STREET |
| `postal_code` | `regex_checksum` | LOCATION_AREA |
| `hospital_gaz` | `gazetteer` | ORGANISATION |
| `servicio_cue` | `context_cue` | ORGANISATION |
| `profesion_cue` | `context_cue` | PROFESSION |
| `edad_cue` | `context_cue` | AGE |
| `age_pattern` | `regex_checksum` | AGE |

---

## 3. 채점 — dev fold

문서 250개, 범위 내 gold 스팬 5,254개 (제외 547개, 9.4%), 예측 3,420개.

### 누출률 (headline)

| mode | 누출 | 분모 | 비율 |
|---|---|---|---|
| `fully_covered` (**headline**) | 2,942 | 5,254 | **0.560** |
| `relaxed` (하한) | 2,548 | 5,254 | **0.485** |

문서 단위 누출률은 두 mode 모두 **1.000** — 250개 문서 전부에 최소 한 건의 누출이
있다. 한 번의 호출로 만든 규칙집합에 대해 이것은 예상되는 값이고, 그래서 headline 이
F1 이 아니다 (CLAUDE.md).

### P/R/F1 (1:1 배정)

| mode | P | R | F1 |
|---|---|---|---|
| `relaxed` (**headline**) | 0.792 | 0.515 | **0.624** |
| `fully_covered` | 0.677 | 0.440 | 0.533 |

`assignment_slack` 은 두 mode 모두 0.

### 상보성 분해 (예측 합집합)

| family | `fully_covered` | `relaxed` |
|---|---|---|
| `rules_only` | 2,312 | 2,706 |
| `tagger_only` | 0 | 0 |
| `both` | 0 | 0 |
| `joint_only` | 0 | 0 |
| `neither` | 2,942 | 2,548 |

`tagger_only`·`both` 가 0 인 것은 by construction 이다 — `R` arm 이고 tagger 가 없다.
`joint_only` 가 0 인 것은 규약이 요구하는 바다 (`fully_covered` 에서만 생길 수 있고
여기서는 병합할 다른 구성요소가 없다). 층별 coverage (`fully_covered`):
`regex_checksum` 1,639 · `context_cue` 914 · `gazetteer` 6, 그리고 두 층이 함께
덮은 스팬 247개. `covered_by_union_only` 는 0.

### 유형별 누출률 (`fully_covered`)

| `phi_type` | 분모 | 누출 | 비율 |
|---|---|---|---|
| CONTACT | 272 | 14 | 0.051 |
| AGE | 521 | 63 | 0.121 |
| DATE | 724 | 196 | 0.271 |
| LOCATION_STREET | 434 | 184 | 0.424 |
| ID | 745 | 495 | 0.664 |
| LOCATION_AREA | 1,334 | 937 | 0.702 |
| NAME | 1,000 | 837 | 0.837 |
| ORGANISATION | 214 | 206 | 0.963 |
| OTHER | 6 | 6 | 1.000 |
| PROFESSION | 4 | 4 | 1.000 |

**구조형이 잘하고 정서형이 못한다.** 형태로 검증되는 유형(CONTACT·AGE·DATE)은
누출률이 낮고, 주변 정서법에 의존하는 유형(NAME·ORGANISATION)은 높다. DESIGN §7 의
층별 예측이 가리키는 방향과 같지만 **이것은 그 예측의 검증이 아니다** — §7 은 코퍼스
간 정서법 실현도 차이에 대한 예측이고, 여기는 코퍼스 하나의 한 실행이다. 층별 손실의
귀속은 두 번째 코퍼스가 돌아야 가능하다.

`macro` 는 `fully_covered` 에서 P 0.527 / R 0.397 / F1 0.423, 누출률 0.603
(`n_types` 10). micro 보다 나쁘다 — 작은 유형에서 더 못한다는 뜻이다.

`false_positive_opportunity` 는 두 필드 모두 0: gold PHI 가 없는 문서가 dev 에 없어서
이 지표가 잴 것이 없다.

---

## 4. 비용과 모델 식별

| | |
|---|---|
| `llm_calls` | 1 |
| `prompt_tokens` | 14,071 |
| `completion_tokens` | 2,325 |
| `wall_seconds` | 32.5 |
| `model_id` | `us.anthropic.claude-opus-4-5-20251101-v1:0` |
| `model_id_reported` | `claude-opus-4-5-20251101` |
| `model_id_resolution` | `dated` |

생애주기 기록: `Claude Opus 4.5`, `ACTIVE`, `start_of_life_time`
2025-11-24T00:00:00+00:00, ARN 은 `us-east-1` 의 foundation-model.

첫 실행과 비교하면 프롬프트 토큰이 13,914 → 14,071 (+157) 로 늘었다 — §2 에 더한
문단이다. completion 은 2,559 → 2,325 로 줄었고 wall time 은 33.3s → 32.5s 다.
**형식 지시를 더한 비용은 프롬프트 157 토큰이고, 그것으로 실패한 실행이 채점되는
실행이 됐다.**

---

## 5. 이 실행이 드러낸 것

### 5.1 `rule_id` 어휘에 두 낱말이 더 없었다

`nass_cue` 와 `hospital_gaz` 가 스크리너의 SUSPECT 로 올라왔다. 둘 다 알려진 계열의
오탐이다:

- `nass` — NASS (número de afiliación a la Seguridad Social). `dni`·`nie`·`nuss` 와
  **같은 범주**인 국가 식별자 약어다. `nuss` 항목이 "어떤 규칙도 필요로 하지 않아서
  빠져 있었다" 고 적어둔 그 자리에 같은 이유로 빠져 있었다.
- `gaz` — `gazetteer` 의 약어이고, 그 낱말은 이미 어휘에 있다. `layer` 축의 값
  이름이기도 하다. `abbrev`/`abbreviation` 이 둘 다 있는 것과 같은 형태.

**파일을 allowlist 에 올리는 것이 아니라 어휘를 넓혔다.** 경로를 allowlist 에 올리면
그 파일의 *진짜* 히트도 앞으로 조용해진다 — allowlist 는 경로에 대한 진술이고
내용에 대한 진술이 아니기 때문이다. 지난 라운드의 영어 전용 어휘 수정과 같은 판단이다.

이것은 **세 번째 arm 에서도 같은 일이 일어날 것**을 뜻한다. 어휘는 폐쇄 집합이고
모델은 매번 새 낱말을 쓴다. 지금까지 두 번 모두 정당한 확장이었으나, 그 사실이
자동으로 세 번째도 정당하게 만들지는 않는다 — 확장은 커밋되는 변경이고 리뷰가
그것을 지킨다.

### 5.2 arm 이 자기 트리를 dirty 로 만든다

`metrics.json` 의 run 블록이 `"tree": "dirty"` 를 적었다. 그런데 직전 dry-run 은
`tree clean` 을 출력했고, `git diff --name-only` 는 비어 있다. 원인은
`tree_state()` 가 `git status --porcelain` 을 쓰고 그것이 **추적되지 않는 파일을
센다**는 것이다. run 블록이 쓰이는 시점에 arm 은 이미 자기 출력 디렉토리를 만들었다.

**이것은 결함이 아니라 의도된 동작이다** — 뮤테이션
`only_tracked_modifications_count_as_dirty` 가 정확히 이 성질을 지킨다. 추적되지 않는
파일이 clean 으로 읽히면 사람이 손으로 확인하는 유일한 경우가 무력해진다.

그러나 결과적으로 **모든 에이전트 arm 이 `dirty` 를 기록한다.** DESIGN §10 은
`tree` 를 "recorded 하고 correct 하지 않는다" 고 정하고 `commit` 이 실행된 코드를
가리키는지 판단하는 근거로 쓴다. 여기서 `dirty` 는 "추적 파일이 수정됐다" 가 아니라
"arm 이 자기 산출물을 썼다" 를 뜻하고, 읽는 사람이 전자로 읽을 것이다. 필드의 값이
틀린 것은 아니고 **의미가 독자의 기대와 다르다.** 코드는 건드리지 않았다 — 무엇을
고칠지는 DESIGN 의 결정이고, 그 결정 없이 채점기를 만지면 봉인 평가의 게이트를
같이 움직인다.

---

## 6. 사다리에서 이 숫자의 위치

`port-loop` 이 이겨야 할 대상은 이 실행이다. `port-oneshot` 의 형식 실패는 비교
대상이 될 수 없다 (`arm-port-oneshot-es.md` §5). 두 arm 의 호출 1 이 보여지는
바이트가 동일하려면 `port-loop` 도 `ddc48bdb0a0c…` 프롬프트를 읽어야 하고,
그것이 이 편집을 `port-loop` **전에** 한 이유다.

`port-loop` 이 이 숫자를 넘지 못하면 agentic framing 은 얻어지지 않는다 (DESIGN §4).
비용 기준도 함께 적용된다: 1 호출 / 16,396 토큰 / 32.5s 가 기준선이고, §11.3 의
1.9× 기준이 rung 마다 적용된다.
