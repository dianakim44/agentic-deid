# `port-loop-noaudit` / es-meddocan — 사전등록 (실행 전)

**이 arm 은 `port-loop` 의 결과를 본 뒤에 추가한 사후 분석이다.** 원래의 실험 축에
없었고, `port-loop` 의 궤적(8회차, 천장 종료, 최종 `L_full` 0.1372)을 읽은 뒤에
"그 향상에서 Auditor 의 몫은 얼마인가" 를 묻기 위해 설계했다. 그러므로 이 arm 의
결과는 **사후 분석으로 보고한다** — 사전등록된 축의 결과와 같은 지위로 적지 않는다.
사후라는 것이 변명이 아니라 분류이므로, 보고서에서도 이 문장이 먼저 온다.

이 파일은 **실행 전에** 커밋된다. **이 파일을 담은 커밋의 해시가 등록 기준이다** —
그 뒤에 이 파일이 서술하는 어느 항목이라도 바뀌면 그것은 등록의 수정이고, 커밋
이력에 그렇게 보인다.

축: `es-meddocan` / `R` / `sup-free` / `port-loop-noaudit`, split
`splits/es-meddocan.json`, dev fold 250 문서 · in-scope 5,254 스팬.

---

## 1. 비교의 형태 — 한 성분만 다르다

짝은 `results/es-meddocan/R/sup-free/port-loop/` 이고, DESIGN §4 가 요구하는 "한 가지만
다른 비교" 를 만들기 위해 아래 왼쪽 열 전부를 고정한다.

| 고정하는 것 | 값 | 출처 |
|---|---|---|
| 드라이버 | `src/porting/loop.py` (`port-loop` 과 같은 rung) | `config/naming.yaml` `porting_rungs.loop` |
| RuleAuthor 템플릿 | `docs/prompts/pinned/rule_author.ddc48bdb.md` = `sha256:ddc48bdb0a0c…` | `porting_arms['port-loop-noaudit'].templates` |
| 표본 추출 | `config/sampling.yaml` = `sha256:4c0e2cc725d3…`, `sampling_applied: false` | `src/sample.py` |
| 모델 | `us.anthropic.claude-opus-4-5-20251101-v1:0` (`model_id_resolution: dated`) | `port-loop` 의 `run.model_id` |
| 종료 규칙 | δ_floor 0.005 · δ_spans 26 · k 2 · 천장 8 | `config/naming.yaml` `termination_params` |
| 채점기 | `src/eval/scorer.py`, headline `fully_covered` + relaxed 하한 | DESIGN §9.3 |
| §1.4 오류 표본 | **그대로 둔다** (아래 §1.1) | `src/llm/prompt.py` |

| 다른 것 하나 | |
|---|---|
| Auditor | 호출하지 않는다. `porting_arms['port-loop-noaudit'].agents_absent = [auditor]` |

### 1.1 이 arm 이 측정하는 것은 "피드백 대 무피드백" 이 아니다

§1.4 의 오류 표본은 **남아 있다.** 회차 N 의 프롬프트는 회차 N−1 규칙의 dev 오류
표본을 계속 받고, 없어지는 것은 §1.3 의 Auditor 요약뿐이다. 그래서 이 arm 이 재는
것은

> **dev 오류 피드백 위에 Auditor 를 얹어서 추가로 얻는 몫**

이고, "피드백이 있는 루프 대 없는 루프" 가 아니다. 이 구분을 여기 적는 이유는 결과를
읽는 쪽이 후자로 읽기 쉽기 때문이다 — Auditor 를 뺀 arm 이 `port-loop` 에 거의
근접하면 그것은 "루프가 쓸모없다" 가 아니라 "오류 표본이 이미 그 일을 하고 있다" 로
읽어야 한다.

### 1.2 창 기록이 짝과 세 자리에서 다를 것이고, 셋 다 드리프트가 아니다

freeze record 를 나란히 놓으면 세 자리가 다르다. 실행 뒤에 이 세 자리를 드리프트로
읽지 않기 위해 **실행 전에** 적어 둔다. 같은 규약이 이미 `port-oneshot` 의
`sampling_sha256` 에 적용돼 있다 — 이미 호출한 arm 의 기록은 다시 얼리지 않으므로
두 기록이 서로 불일치하는 것이 규율이 작동하는 모습이다(DESIGN §6.3).

| 자리 | `port-loop` 의 기록 | 이 arm 이 쓸 값 | 이유 |
|---|---|---|---|
| `files` | 3개 (`rule_author.md` · `auditor.md` · `sampling.yaml`) | **6개** | 창이 `port-multi` 의 세 역할 때문에 넓어졌다. 넓어진 뒤의 arm 은 넓은 창을 얼린다 |
| `auditor_sha256` | `b7e983a4…` | **`a49739ee…`** | 이 arm 은 Auditor 를 **보내지 않지만** 파일을 창에서 빼지 않는다 — `files` 가 arm 마다 달라지면 두 기록을 나란히 읽을 수 없다. 그러므로 오늘의 값이 들어간다 |
| `agents_absent` | **없는 필드** | `[auditor]` | 선언 축이 `port-loop` 이후에 생겼다 (DESIGN §6.3). 기록에 없는 것과 `[]` 는 구분되며, `port-loop` 쪽은 전자다 |

---

## 2. 사전등록하는 창 (freeze record 는 실행 시점에 쓰인다)

`freeze_window()` 는 **창을 처음 소비하는 호출에서** 기록을 쓴다 (DESIGN §6.3 "freeze
last"). 그래서 실행 전에 freeze record 파일을 손으로 커밋할 수 없다 — 그렇게 하면
`revision: 2` 를 만들어 "이 arm 이 시작한 창" 이라는 주장을 거짓으로 만든다. 대신
**실행이 쓸 값을 여기 등록한다.** 실행 뒤 기록이 아래와 다르면 그 run 은 무효다.

| 필드 | 등록 값 |
|---|---|
| `revision` | 1 |
| `files[0]` | `docs/prompts/pinned/rule_author.ddc48bdb.md` |
| `files[1..5]` | `docs/prompts/auditor.md` · `config/sampling.yaml` · `docs/prompts/profiler.md` · `docs/prompts/mapper.md` · `docs/prompts/lexicon_builder.md` |
| `prompt_sha256` | `sha256:ddc48bdb0a0c286ab8194ba38f49e97b3cf95f6463e6e69c93d30915eef52cfa` |
| `sampling_sha256` | `sha256:4c0e2cc725d357a1bdfa5c718d243c850ae52f5f236543bbac45b5d8e6ecb615` |
| `auditor_sha256` | `sha256:a49739eec170f4e2fc8751b977b23e5aab18dd3e647a3f505f79f4f642135b26` (§1.2) |
| `sections_shown` / `sections_empty` | 1.1 · 1.2 / 1.3 · 1.4 (회차 1) |
| `sampling_applied` | `false` |
| `agents_absent` | `[auditor]` |

고정 판본 파일의 sha256 은 `tests/test_pinned_prompts.py::PINNED_SHA256` 에 상수로
들어가 있고, 그 테스트가 파일의 바이트를 그 상수에 대고 검사한다 — 어느 방향의
편집이든 실패한다.

---

## 3. `ddc48bdb` 를 보내는 이유, 그리고 그것이 되돌림이 아닌 이유

오늘 디스크의 `docs/prompts/rule_author.md` 는 `bcef6f39…` 이고 DESIGN §6.8 의
example-free 정책이 적용된 판본이다. `port-loop` 이 8회차를 돌린 판본은 `ddc48bdb…`
다. 짝지은 비교에서 템플릿이 다르면 "Auditor 없음" 과 "프롬프트 판본 다름" 이
교락되므로, 이 arm 은 `ddc48bdb` 를 보낸다.

**이것은 example-free 정책의 되돌림이 아니다.** 되돌림이라면 `docs/prompts/` 의
라이브 템플릿이 움직여야 하고, 움직이지 않았다 — 고정 판본은
`docs/prompts/pinned/` 아래의 별도 파일이고, 그 디렉토리는 fence 검사가 아니라
sha256 고정의 지배를 받는다. 정책이 적용되는 파일은 그대로 `bcef6f39` 이고,
**그 판본을 보내는 arm 도 따로 돈다**: `port-loop-noaudit-current` (B1b) 가 그것이고,
두 rung 의 차이는 템플릿 하나뿐임을 `test_the_two_ablation_rungs_differ_only_in_the_template`
이 고정한다.

| rung | 보내는 템플릿 | 바이트 동일 검사 | test fold |
|---|---|---|---|
| `port-loop-noaudit` (B1a) | `pinned/rule_author.ddc48bdb.md` | **적용** | 최종 회차 1회 개봉 |
| `port-loop-noaudit-current` (B1b) | 디스크의 `rule_author.md` (`bcef6f39…`) | 면제 | 열지 않는다 — dev 만 |

---

## 4. 반복 — B2 의 두 회차 (DESIGN §6.10)

같은 설정의 독립 반복 2회를 더 돌려 총 3회로 만든다. 반복은 `porting_repetitions`
선언으로 주소를 갖는다.

| 값 | `of` | `index` | test fold |
|---|---|---|---|
| `port-loop-noaudit` | — (반복 1) | 1 | 최종 회차 1회 개봉 |
| `port-loop-noaudit-rep2` | `port-loop-noaudit` | 2 | **열지 않는다** |
| `port-loop-noaudit-rep3` | `port-loop-noaudit` | 3 | **열지 않는다** |

**이 규칙은 반복을 돌리기 전에 여기 적는다.** 반복 번호는 설정에서 유도할 수 없는
유일한 서수이고, 반복은 rung 이 아니며 래더 비교·종료 판정·test 개봉의 대상이 아니다
(DESIGN §6.10). 세 번째 항목은 `src/eval/run_sealed_eval.py` 의 `plan_arm` 이 거절로
집행한다: 반복 선언이 붙은 arm 은 다른 어떤 검사에 닿기 전에 거절된다.

보고는 **셋을 함께** 한다 — 회차별 `L_full` 을 나란히 적고, 최종 회차 값의 범위와
평균을 함께 적는다. 셋 중 하나의 궤적만 인용하는 것은 결과 경로를 쓴 draw 선택이다.

---

## 5. 형식 실패가 나면 — 고치지 않는다

`port-oneshot` 이 형식 실패로 죽었고(DESIGN §10 A2), `port-loop` 회차 5 는 RuleAuthor
포기 2회를 기록했다. 이 arm 에서 같은 일이 나면:

- **실패로 기록한다.** `format_failure.json` 과 그 비용이 결과다.
- **프롬프트를 고치지 않는다.** 템플릿은 `ddc48bdb` 로 고정돼 있고, 고치면 짝지은
  비교가 사라진다.
- **재시도는 기존 규칙만 쓴다** — 새 재시도 예산을 만들지 않는다.

이것을 여기 적는 이유는 실패를 본 뒤에 정하면 그 결정이 결과를 보고 내린 것이 되기
때문이다.

---

## 6. 실행 뒤, 결과를 보기 전에 검증하는 네 가지

아래 넷 중 하나라도 어긋나면 **그 run 은 무효로 기록하고 보고한다.** 결과 수치를
읽기 전에 한다.

1. **회차 1 프롬프트 `sha256` 가 `port-loop` 회차 1 의 것과 같다** —
   `sha256:6528088f2ae3f8dad88e08468ae86ba2c37edc9ae61b49d270b47366372ba61b`,
   `text_chars 53644`. 출처는 `port-loop` 의 `agent_calls.jsonl` 1행
   `prompt_reference` 이고, 같은 값이
   `tests/test_pinned_prompts.py::PORT_LOOP_ROUND_1_SHA256` 에 상수로 있다.
2. **회차 2–8 프롬프트에서 §1.3 의 Auditor 반쪽이 비어 있고, 나머지 바이트가
   `port-loop` 과 같은 자리에 있다.** 블록을 **생략하지 않는 것**이 요점이다 —
   위치와 남는 바이트 수가 그대로여야 한다.
3. **Auditor 호출 수 0** — `agent_calls.jsonl` 에서 `role: auditor` 인 행이 없다.
4. **창 기록이 §2 의 표와 일치한다** — `revision: 1` 을 포함해서.

반복 2·3 도 같은 네 검증을 거친다.

### 기대하는 회차 기록의 모양

Auditor 가 없는 회차는 `port-loop` 의 회차 기록과 다음에서 다르다. 다른 자리가
**이것뿐**이어야 한다.

| 필드 | 이 arm | `port-loop` |
|---|---|---|
| `audit_report_path` · `audit_draw_path` · `draw_index` | `null` | 경로와 번호 |
| `caching` | 회차 반환값은 `None`, `metrics.json` 에는 **블록이 없다** — Auditor 호출이 캐시 경계를 만들던 유일한 호출이다. 블록의 부재가 "캐싱을 쓰지 않았다" 는 기록이다 (schema 8) | 블록이 있다 |
| `cost.llm_calls` | **1** (RuleAuthor 1회) | 1 + 250 |
| `abandoned_spend` | **게이트가 살아 있다** — 보존된 draw 가 아니라 **호출 로그**에서 나오므로, draw 가 영구히 없는 이 arm 에서도 버려진 지출이 기록된다 (DESIGN §5.5.2, 회차 6 의 교훈) | 같음 |

---

## 7. 비용 추정 (실행 전, 상한 $30)

LLM 비용은 RuleAuthor 8 호출 전부다. `port-loop` 의 실측 RuleAuthor 토큰이 상한이다
(§1.3 이 비므로 입력은 `port-loop` 보다 **작다**).

| | 호출 | input | output |
|---|---|---|---|
| `port-loop` RuleAuthor 회차 1 (실측) | 1 | 14,071 | 2,791 |
| `port-loop` RuleAuthor 회차 2–8 (실측) | 7 | 238,239 | 36,941 |
| **1회 실행의 기준선** | **8** | **252,310** | **39,732** |

**단가는 이 저장소에 없다** — 비용은 호출 수·토큰·wall time 으로만 기록한다
(CLAUDE.md). 달러 환산은 외부 가정(Opus 4.5 공개 단가 input $5/MTok, output
$25/MTok)이고, 그 가정 아래 1회 기준선 **$2.25**, 재시도·규칙 증가 여유를 2배로 둔
상한 **$4.51**. B1a + B1b + 반복 2 + 반복 3 = 4회이므로 기준선 **$9.00**, 상한
**$18.05**. **상한 $30 아래다.**

비용은 품질과 함께 보고한다 — 짝인 `port-loop` 의 `cost_to_date` 는 1,758 호출 ·
16.48M input · 278K output · 7,730초이고, 이 arm 은 그 **약 2%** 다. 향상이 있든
없든 그 비율과 함께 읽어야 한다.

---

## 8. 사전등록하는 예측

DESIGN §3 의 전례대로 **발화 전에** 적는다. 반증되면 그렇게 기록한다 — `port-loop`
에서 이미 두 번 반증됐다(`docs/notes/arm-port-loop-es.md` §5.8).

| | 예측 | 근거 |
|---|---|---|
| P1 | 최종 회차 `L_full` 이 `port-loop` 의 0.1372 보다 **크다** (누출이 더 많다) | Auditor 는 회차마다 dev 전체를 읽고 요약을 주므로 0 의 가치일 이유가 없다 |
| P2 | 그 차이는 **0.05 미만** 이다 | §1.4 오류 표본이 남아 있고, 그것이 Auditor 가 주던 정보의 상당 부분과 겹친다 (§1.1) |
| P3 | 종료는 **천장** 이다 (8회차) | `port-loop` 의 개선 수열이 δ_floor 0.005 아래로 두 번 연속 내려간 적이 없고, 정보를 줄이면 수렴이 빨라질 이유보다 늦어질 이유가 많다 |
| P4 | 세 반복의 최종 `L_full` 범위가 **0.02 미만** 이다 | 측정된 적 없는 양이다 — 이 arm 이 재는 두 번째 것이고, 반증되면 "1회 실행으로 arm 을 보고한다" 는 이 저장소의 관행 전체에 대한 결과다 |

P4 가 반증되면(범위 ≥ 0.02) 그것은 이 arm 의 결론보다 큰 결과이므로, 그 경우
보고서에 **단독 항목으로** 적는다.

---

## 9. 봉인 규율

- test fold 는 **B1a 의 최종 회차에서 1회** 연다. `src/eval/run_sealed_eval.py` 로만
  열고, 열 때마다 `results/sealed_eval_log.md` 에 날짜·커밋 해시·목적을 append 한다.
- B1b 와 반복 2·3 은 **열지 않는다.** dev 만 채점한다.
- dev 오류를 보고 고치는 것은 허용, test 오류를 보고 고치는 것은 금지. 이 arm 이
  만드는 규칙·사전에도 같은 규율을 적용한다.
- `sealed/` 를 직접 열지 않는다.
