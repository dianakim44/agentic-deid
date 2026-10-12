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

---

## 10. 실행 기록 — 2026-10-11 의 1차 실행은 **무효**다

이 절은 실행 **뒤에** 추가됐다. §6 이 요구하는 것을 요구된 자리에 적는다:
"아래 넷 중 하나라도 어긋나면 그 run 은 무효로 기록하고 보고한다. 결과 수치를
읽기 전에 한다." 검사 1 이 어긋났고, **그 run 의 점수는 읽지 않았다.** 이 절에
점수가 없는 것은 누락이 아니다.

### 10.1 어긋난 것

| | §6 검사 1 이 등록한 값 | 실제 |
|---|---|---|
| 회차 1 프롬프트 `text_sha256` | `sha256:6528088f2ae3f8da…` | `sha256:fa9502775b3ac20a…` |
| `text_chars` | 53,644 | 56,973 |

차이 3,329자는 `ddc48bdb` 핀과 현행 `docs/prompts/rule_author.md` 의 차이다.
**선언한 판본이 아니라 현행 파일이 갔다.** 검사 2–4(§1.2 의 세 자리 · Auditor 호출 0 ·
`agents_absent`)는 어긋나지 않았다.

### 10.2 기록이 왜 그것을 말하지 않았나

`window_freeze.json.files[0]` · 같은 파일의 `prompt_sha256` · 호출 줄의 `prompt_sha256`
세 자리는 전부 **선언**에서 쓰이므로 선언을 되풀이한다. 읽은 파일을 말하는 자리는
`prompt_reference.window_files` 하나이고, 그 키는 `docs/prompts/rule_author.md` 였다.
기록은 자기 프롬프트에 대한 거짓 진술이었고, 거짓인 것을 보려면 3단 안쪽을 열어야 했다.

입력 토큰 14,874 도 신호였다 — 짝의 회차 1 실측이 14,071 이고 두 arm 의 회차 1 은
바이트 동일해야 하므로 같아야 했다. 그러나 그것을 보려면 짝의 비용표와 비교해야 하고,
검사 1 은 그 비교 없이 잡히도록 해시로 적어둔 것이었다. 설계대로 해시가 먼저 잡았다.

### 10.3 원인과 수정

`src/porting/loop.py` 의 회차 1 이 `assemble_task_prompt(...)` 에 `porting` 을
넘기지 않았다(회차 ≥ 2 는 넘기고 있었다). `porting` 은 어느 판본을 보내는지를 고르는
인자다(DESIGN §6.3). 조립기는 옳았고 `tests/test_pinned_prompts.py` 가 그것을 단언하고
있었다 — 단언되지 않은 것은 드라이버가 그 인자를 넘기는지였다. 수정은 kwarg 하나이고,
드라이버 수준 테스트 3건과 뮤테이션 `round_one_is_not_told_which_arm_it_is` 를 함께 넣었다.

### 10.4 소진된 셀과, 재실행에 필요한 결정

봉인은 열지 않았다. 쓴 것은 1 호출 · 14,874 + 2,213 토큰 · 27.248초(약 $0.13,
§7 기준선의 6%)다. `results/es-meddocan/R/sup-free/port-loop-noaudit/` 는 이미 기록을
가지므로 `freeze_window()` 가 두 번째 run 을 거절한다(freeze-last). **재실행 경로는
사용자 결정이고, 이 노트는 결정이 내려질 때까지 그것을 비워 둔다** — 무효 산출물을
지우는 것은 되돌릴 수 없고, 새 axis 값을 만드는 것은 어휘 변경이며 이 노트의 등록
해시가 가리키는 arm 이름을 바꾼다.

이 무효 run 은 §9 의 "봉인 1회" 예산을 쓰지 않았다. 재실행은 여전히 1회를 갖는다.

### 10.5 사용량 — 재실행의 비용 보고에 **abandoned spend** 로 합산한다

| 항목 | 값 | 출처 |
|---|---|---|
| LLM 호출 | 1 (`role: rule_author`, `outcome: called`) | `agent_calls.jsonl` |
| input 토큰 | 14,874 | 같은 줄 `cost.prompt_tokens` |
| output 토큰 | 2,213 | 같은 줄 `cost.completion_tokens` |
| cache 토큰 | **필드가 없다** | 같은 줄 — `cost` 는 세 키(`llm_calls`·`prompt_tokens`·`completion_tokens`)와 `wall_seconds` 뿐이다 |
| 호출 wall time | 27.248초 | 같은 줄 |
| arm wall time | 27.397초 | `metrics.json.cost.wall_seconds` |
| 모델 | `us.anthropic.claude-opus-4-5-20251101-v1:0` | 같은 줄 `model_id` |
| 생성 시각 | 2026-10-11T04:28:36Z | 같은 줄 `generated` |
| 추산 비용 | 약 $0.13 (§7 기준선의 6%) | §10.4 |

cache 토큰이 **0 이라고 적지 않는다** — 기록에 그 필드가 없으므로 0 인지 미측정인지
이 기록은 말하지 않는다. 재실행의 비용표는 이 줄을 `abandoned_spend` 로 따로 세우고,
cache 열은 "무효 run 에 대해 미측정" 으로 남긴다. DESIGN §3 의 `abandoned_spend` 가
회차 5 의 버려진 두 시도에 쓰인 것과 같은 자리이고, 다른 점은 이것이 **회차가 아니라
run 전체**라는 것이다 — 그래서 합계에 들어가되 어느 회차에도 귀속되지 않는다.

### 10.6 dev 점수는 **계산됐고**, 누출률은 읽지 않았다

파이프라인이 자동으로 채점하므로 `metrics.json` 은 `modes.fully_covered` ·
`modes.relaxed` 를 둘 다 갖고 있고 각각 `leak` 을 갖는다(키 존재만 확인했다).
**즉 "계산되지 않았다" 가 아니라 "계산됐고 읽지 않았다" 가 맞는 진술이다.**

열람에 대해 정확히 적는다. 누출률·F1·상보성 분해는 열지 않았다. 그러나 **비헤드라인
수치 두 조각이 pytest 의 assertion repr 로 화면에 나왔다**: `tools/make_figures.py` 의
`one()` 이 SystemExit 을 내면서 pytest 가 `arms` 리스트의 첫 원소를 repr 로 찍었고,
그 잘린 꼬리에 이 arm 의 `false_positive_opportunity` (두 값 모두 0)와
`…dictions: 3` 조각이 보였다. 둘 다 headline 이 아니고 선택에 쓸 수 있는 값도 아니지만,
"점수를 전혀 보지 않았다" 는 문장은 거짓이므로 쓰지 않는다. 그리고 이것이 §6 의 절차가
막지 못하는 경로다 — **검사를 통과하기 전에는 수치를 열지 않는다는 규율은 내가 여는
것만 통제하고, 실패한 테스트가 찍는 것은 통제하지 못한다.**

### 10.7 test fold 는 열리지 않았다 — 기록으로 확인했다

| 확인 | 결과 |
|---|---|
| `results/sealed_eval_log.md` 에 이 arm 을 적은 행 | **없다** (grep `noaudit` → 0건) |
| `src/eval/sealed_log.count_runs()` | **1** — 발행분 `port-loop` 의 1회이고, 이 arm 의 것이 아니다 |

그러므로 §9 의 "arm 당 1회" 예산은 소진되지 않았고, 재실행은 1회를 온전히 갖는다.
이것을 먼저 확인한 이유는 결정에 들어가기 때문이다: 열렸다면 재실행은 arm 이름을
바꾸는 것(= 새 셀) 외에 선택지가 없었다.

### 10.8 처분 — 저장소 밖으로 **옮겼다** (삭제하지 않았다)

2026-10-12, 디렉토리 전체를 다음으로 이동했다:

```
~/agentic-deid-archive/invalid-runs/2026-10-11-port-loop-noaudit/
```

바탕화면·공유 폴더에 두지 않는다. MEDDOCAN 은 공개 코퍼스지만 `agent_calls.jsonl` 의
`prompt_reference` 와 규칙 파일은 프롬프트·규칙 문자열을 담을 수 있고, 그 경로에는
`release_screen.py` 가 닿지 않는다.

이동 전에 계산한 sha256 (이동 후 같은 값으로 재확인했다):

```
2bb122e14e841fd4a7e7596868295d6f68dd4efb2d24b9b9bf2b55b627c3bf26  agent_calls.jsonl
25598d353b1ef7ee295170616f780679042e32bb1d7b39a27c3af8d8d2906316  iter1/errors.jsonl
1fd28b5f1a41481cff192b58aacad0db71e3caab3f79f505691d2ab8d609e78c  iter1/metrics.json
c1be15ce477f7e275b6ede48254049b0109ad26403567e8cf1d828ff126cb4a3  iter1/spans.jsonl
1fd28b5f1a41481cff192b58aacad0db71e3caab3f79f505691d2ab8d609e78c  metrics.json
a39c89205ddc2769bd96bd091b1ebabacf3a6ec6c231e768f5f9f373f834f372  rules/iter1/es.yaml
c1be15ce477f7e275b6ede48254049b0109ad26403567e8cf1d828ff126cb4a3  spans.jsonl
228440c35c9429feb12056428ac19f0911aa9465cb8b807475329c6d19d621c5  window_freeze.json
```

두 쌍이 같은 해시인 것은 회차 1 이 유일한 회차이기 때문이다 — arm 수준 `metrics.json` ·
`spans.jsonl` 은 `iter1/` 의 것과 바이트 동일하다. 이것도 §10.2 의 모양과 같은 성질이다:
**회차 1 만 있는 arm 의 산출물은 단일 호출 arm 의 산출물과 구별되지 않는다.**

이동으로 셀이 비었으므로 `freeze_window()` 는 재실행을 거절하지 않고, 등록 해시가
가리키는 arm 이름(`port-loop-noaudit`)도 바뀌지 않는다. 재실행은 §2 의 창을 그대로
다시 동결한다.

---

## 11. 재등록 — **이 절을 담은 커밋이 재실행의 등록 기준이다**

§10 의 무효 판정과 그 처분 뒤에 쓴다. 등록은 **갱신**이고 §§1–9 의 어느 항목도
철회하지 않는다 — 아래 표에 그것을 실행 **전에** 확인한 결과를 적는다.

### 11.1 원인 수정이 freeze 대상을 바꿨는가 — **값은 바꾸지 않았고, 값을 읽는 쪽을 바꿨다**

이 구분을 먼저 적는다. 수정(`src/porting/loop.py` 의 kwarg 하나, 커밋 `2f2522a`)은
**템플릿·sampling·선언 중 어느 것의 값도 건드리지 않았다.** 건드린 것은 회차 1 이 그
선언을 조립기에 전달하는지였다. 그러므로

- **§2 의 등록 값은 그대로다** — 아래 11.2 가 전부 재확인한다.
- **그런데 그 수정은 freeze 대상이 *적용되는 경로* 를 바꿨다.** 수정 전에는 선언이
  기록에만 있고 프롬프트에는 없었으므로, 같은 등록 값이 수정 전후에 **다른 바이트**를
  만든다. 등록 값이 불변인 것과 등록이 무의미한 것은 다르다 — 1차 실행이 무효인 이유가
  바로 그 경로였다.

사용자 지시의 "원인 수정이 B1a 의 freeze 대상(템플릿·sampling·선언)을 바꾸면 재등록
커밋에 그 사실을 적는다" 에 대한 답은 그래서 **"값은 아니고 경로는 그렇다"** 이고,
두 쪽을 한 문장으로 줄이지 않는다.

### 11.2 실행 전 재확인 — 등록 값 전부

| 확인 | 등록 값 | 실측 (실행 전) |
|---|---|---|
| 회차 1 프롬프트 `text_sha256` | `sha256:6528088f2ae3f8da…` | **일치** |
| 회차 1 `text_chars` | 53,644 | **일치** |
| `sections_filled` / `sections_empty` | 1.1 · 1.2 / 1.3 · 1.4 | **일치** |
| `prompt_sha256` (고정 판본 파일) | `sha256:ddc48bdb0a0c…` | **일치** |
| `sampling_sha256` | `sha256:4c0e2cc725d3…` | **일치** |
| `auditor_sha256` (§1.2) | `sha256:a49739eec170…` | **일치** |
| 창 파일 6개와 그 순서 | §2 의 `files[0..5]` | **일치** |
| `agents_absent` | `[auditor]` | `('auditor',)`, `arm_calls_agent(…, auditor)` = `False` |
| `templates` | `rule_author.md` → 고정 판본 | **일치** |
| rung 이 반복하는가 | 예 | `porting_iterates` = `True` |
| 반복 선언 | 없다 (반복 1 은 base) | `porting_repetition_of` = `None` |

**회차 1 검증은 §6 검사 1 을 실행 전으로 한 번 당겨 한 것이다.** 무효 run 에서 이 자리의
값이 `fa950277…` · 56,973자였고, 지금은 등록 값이 나온다. 당겨서 하는 이유는 명백하다 —
검사 1 은 호출 **뒤에** 걸리므로, 1차 실행에서 그것이 잡았을 때 돈은 이미 쓰였다.
**그렇다고 §6 을 면제하지 않는다**: 실행 뒤에 네 검사를 규정대로 다시 한다. 당긴 검사는
조립기의 출력을 보고, §6 의 검사는 **기록에 남은 것**을 보므로 같은 검사가 아니다.

| 그 밖의 전제 | 결과 |
|---|---|
| 셀이 비어 있다 | `results/es-meddocan/R/sup-free/` 에 6개 arm, 이 arm 없음 |
| 기존 freeze record 10개 | 이 수정 계열의 어느 커밋도 건드리지 않았다 (`git log --name-only` 로 확인) |
| `window_drift` | 창 파일 6개가 §2 와 같으므로 드리프트 없음 |
| 봉인 예산 | 미소진 (`count_runs()` = 1 은 발행분 `port-loop` 의 것) |
| Bedrock invocation logging | 실행 당일 6리전 전부 `none` (`docs/notes/compliance.md` §3) |
| 뮤테이션 게이트 | 314건 전량 · 생존 0 · 연기분 0 (수정 커밋을 포함한 트리에서) |

### 11.3 비용 — 무효 run 을 합산해 보고한다

§7 의 추정은 그대로다(1회 기준선 $2.25, 상한 $4.51, 상한 $30 아래). 재실행의 비용표는
거기에 §10.5 의 **`abandoned_spend`** 를 **별도 줄로** 더한다: 1 호출 · input 14,874 ·
output 2,213 · cache **미측정** · 27.248초. 합산하되 어느 회차에도 귀속시키지 않는다.

### 11.4 바뀌지 않은 것

§§1–9 는 전부 유효하다. 특히 **§8 의 예측 네 개를 고치지 않았다** — 1차 실행에서
점수를 읽지 않았으므로 고칠 근거가 없고, 근거가 있었다면 그것은 예측을 고치면 안 되는
이유였다. §5(형식 실패가 나면 고치지 않는다)도 그대로다.
