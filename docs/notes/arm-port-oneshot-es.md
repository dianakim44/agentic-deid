# `port-oneshot` / es-meddocan — 첫 실행 기록

축: `es-meddocan` / `R` / `sup-free` / `port-oneshot`, split `splits/es-meddocan.json`,
dev fold. 실행 2026-08-11.

명령:

```
python3 tools/run_arm.py --corpus es-meddocan --lang es \
  --model-id us.anthropic.claude-opus-4-5-20251101-v1:0
```

> **결과: 형식 실패 (format failure).** 종료 코드 1. `metrics.json` 은 쓰이지 않았고
> `format_failure.json` 이 쓰였다. 이것은 사고가 아니라 **DESIGN §10 A2 가 미리
> 등록한 결과 중 하나**다. 아래 §1 이 그것이고, §2 는 arm 의 결과가 아닌 별도
> 관찰이다. 두 절을 섞어 읽으면 안 된다.

---

## 1. arm 의 결과

이 절만이 arm 의 결과다. 실험에 인용할 수 있는 것은 여기까지다.

### 무엇이 일어났는가

`run_arm` 의 5단계 — 적재로 검증하기(validate-by-loading) — 에서 걸렸다. 그 앞은
모두 정상이었다: 창을 얼리고, 프롬프트를 조립하고, 호출하고, 호출을 로그에 적고,
응답을 `rules/iter1/es.yaml` 에 **그대로** 쓴 다음, `load_rules` 가 거부했다.

재시도는 없다 (§10 A2, 형식 준수 재시도 0 회). 그래서 `metrics.json` 도
`spans.jsonl` 도 없다 — 0 이 적힌 metrics 는 "돌았고 아무것도 못 잡은 규칙 집합" 과
구별되지 않기 때문이다. **파일 이름이 답이고 `status` 필드가 아니다.**

### 검증기 오류, 그대로

```
results/es-meddocan/R/sup-free/port-oneshot/rules/iter1/es.yaml: not parseable
as YAML — found character '`' that cannot start any token while while scanning
for the next token at line 1, column 1. The offending line is not quoted here:
an exception message reaches logs the release screener does not (CLAUDE.md), so
the position is reported and the content is not.
```

`while while` 는 pyyaml 의 것이고 그대로 옮겼다. 1행 1열의 백틱 — 모델이 YAML 을
마크다운 코드 펜스로 감쌌다. 테스트 스위트의 `UNPARSEABLE` 이 모델링하는 실패가
바로 이것이고, 무재시도 규칙이 덮기를 거부하는 것도 이것이다.

### 비용과 모델 식별

| | |
|---|---|
| `llm_calls` | 1 |
| `prompt_tokens` | 13,914 |
| `completion_tokens` | 2,559 |
| `wall_seconds` | 33.312 |
| `model_id` | `us.anthropic.claude-opus-4-5-20251101-v1:0` |
| `model_id_reported` | `claude-opus-4-5-20251101` |
| `model_id_resolution` | `dated` |

`dated` 는 2026-08-11 의 핀 결정이 의도한 값이다
(`baseline-model-family.md`, "날짜 있는 id 로 고정한다").

### 생애주기 기록

`GetFoundationModel` 메타데이터가 **형식 실패 분기에서도** 기록되었다
(`failure_schema` 2). 그 필드가 추가된 이유가 바로 이 경우다 — 기록 위치 셋 중
metrics 쪽만 두면 형식 실패한 arm 은 이 기록을 전부 잃는다.

| | |
|---|---|
| `model_name` | Claude Opus 4.5 |
| `status` | ACTIVE |
| `start_of_life_time` | 2025-11-24T00:00:00+00:00 |

스냅샷의 등장 시점이 실행 시점보다 앞선다. **이것이 이 필드가 답하는 유일한
질문이고**, 그날 무엇이 답했는지는 여전히 말하지 않는다 (측정 4).

### 규칙 수·층별 분포

**없다.** 적재된 것이 없으므로 셀 규칙이 존재하지 않는다. 0 이 아니라 없음이다 —
"0개 규칙" 은 파싱에 성공하고 규칙이 비어 있었다는 뜻이고, 그런 일은 없었다.

---

## 2. 별도 관찰 — arm 의 결과가 아니다

**아래는 실행 후 응답 파일을 따로 조사한 것이고, arm 이 산출한 것이 아니다.**
어느 표에도 실험 수치로 들어가지 않는다. 여기 적는 이유는 §3 이 설명한다.

응답은 230행이고 1행이 ` ```yaml `, 230행이 ` ``` ` 이며 펜스 밖에 비어 있지 않은
줄이 없다. 펜스 안쪽만 떼어 **임시 디렉토리에서** 파싱하면 유효하다: 28개 규칙,
`context_cue` 15 / `regex_checksum` 13. `examples` 류 필드가 없고, 리스트는 단서어와
정규식 플래그뿐이며 이름 목록(gazetteer)은 없다.

**arm 의 산출물은 손대지 않았다.** 펜스는 그대로 있고 `format_failure.json` 도
그대로다. 진단은 사본에서 했다.

### 이 구분을 왜 이렇게 강하게 두는가

"펜스 하나만 고치면 28개 규칙이 나왔다" 는 문장은 재시도 한 번의 논거처럼 읽히고,
§10 A2 가 거부한 것이 바로 그 논거다. 두 진술은 다르다:

- **arm 의 결과**: 이 모델은 이 프롬프트로 한 번 호출해서 사용 가능한 규칙 파일을
  내지 못했다. `port-oneshot` 이 요구하는 것이 "한 호출, 한 사용 가능한 산출물"
  이므로 이것은 능력에 대한 발견이다.
- **별도 관찰**: 실패가 형식 층 한 곳에서 왔고 규칙 내용 층에서 오지 않았다.

두 번째가 첫 번째를 취소하지 않는다. 그러나 두 번째를 적어두지 않으면 다음 판단이
근거를 잃는다 — 프롬프트의 형식 절을 고칠 것인지 말 것인지는 실패가 형식에서 왔는지
내용에서 왔는지에 달려 있고, 그것은 지금 기록해두지 않으면 나중에 알 수 없다
(창이 얼었으므로 이 arm 을 다시 돌려 확인할 방법이 없다, DESIGN §6.3).

---

## 3. 이 실행이 드러낸 저장소 결함 둘

arm 과 무관하게, 이 실행이 처음 만든 파일들이 두 가지를 드러냈다. 둘 다 이번에
고쳤고 별도 커밋이다.

**1. deny-list 인데 gitignore 가 아니었다.** `results/.../agent_calls.jsonl` 은
이 실행이 처음 만든 파일이고, deny 규칙에는 있었으나 gitignore 에는 없어서 BLOCKED
로 올라왔다. 훑어보니 같은 공백이 9개 deny 패턴에 있었다 — gitignore 쪽 항목이
`*ko_tagged*` 처럼 그때 디스크에 있던 파일 이름이었기 때문이다.
커밋 `fix: gitignore every deny-listed path`.

**2. rule_id 어휘가 영어 전용이었다.** 28개 중 23개가 SUSPECT 였고, 모두 코퍼스
언어의 임상 상용구를 이름에 쓴 것이었다 — Prohibition 2 가 허용하는 범주다.
어휘를 언어별 층으로 넓혔다. **프롬프트로 묶지 않았다**: 그렇게 하면 call 1 의
바이트가 바뀌어 이 arm 이 새 arm 이 되고, 모델이 규칙 작성이 아니라 명명 규칙
준수를 시험받는다. 커밋
`fix: rule_id vocabulary is English-only and breaks on every other arm`.

두 결함 모두 **첫 에이전트 arm 이 처음 만든 파일** 때문에 드러났다. 그 전까지
`results/` 에 있던 것은 `port-human` 의 산출물뿐이었고, 그것은 `human_log.jsonl` 을
쓰고 `agent_calls.jsonl` 을 쓰지 않으며 규칙 이름도 사람이 지었다.

---

## 4. 남아 있는 것 — dry-run 이 이제 거부한다

`tools/run_arm.py --dry-run` 은 이제 계획을 출력하지 않고 거부한다:

```
es-meddocan/R/sup-free/port-oneshot: this arm has already made its call
(evidence: log). One call is the whole of this arm; freeze_window() will refuse,
and re-running means running a second arm with a written reason (DESIGN §6.3, §11.1).
```

이것은 창 동결 규율이 설계대로 작동하는 것이다 (DESIGN §6.3 — 구속은
`agent_calls.jsonl` 줄이 떨어진 순간부터). 부수 효과로
`tests/test_run_arm_cli.py` 의 5개 테스트가 실패한다: 그것들은 dry-run 이 계획을
출력한다고 가정하고 쓰였고, 이제 계획 대신 거부가 나온다. **이 실행 전에는
통과했고 이 실행이 만든 상태 때문에 실패한다** — 코드 변경 때문이 아니다.
어떻게 할지는 아직 정하지 않았다: 테스트를 고치는 것과 arm 을 다시 돌리는 것은
다른 일이고, 후자는 §6.3 이 금지한다.
