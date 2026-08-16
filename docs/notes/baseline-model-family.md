# `port-oneshot` 을 어느 모델 계열로 돌릴 것인가

`port-human` 철회로 `port-oneshot` 이 기준선이 되었다 (DESIGN §4, §4.1). 기준선을
Claude 가 아닌 계열로 돌리는 선택지를 검토한 기록이다. 날짜: 2026-08-07.

> **결정 (2026-08-07): D.** 사다리 전 칸은 Claude 로 돌리고, 타 계열 단발을 외부
> 눈금으로 추가한다. 부록 모델은
> `us.meta.llama4-maverick-17b-instruct-v1:0` — 가중치와 벤치마크가 공개되어 부록
> 수치가 Bedrock 밖에서 검증 가능하다. B 가 탈락한 이유는 아래 B 절이 이미
> 적어둔 것이고, 그것이 결정 근거 그대로다. DESIGN §4 가 계열 고정을,
> §10 A2 가 부록의 사전 등록을 담는다. 형식 준수 재시도는 **양쪽 0 회로 확정**
> (2026-08-07, 두 arm 실행 전). 0 이 아닌 k 는 어떤 값을 골라도 근거가 없고,
> 형식 실패 자체가 능력에 대한 보고할 결과다. 실패 시 metrics.json 을 쓰지 않고
> 검증기 오류를 그대로 기록한다. 근거는 §10 A2.

---

## 왜 질문이 생겼는가

철회 전 사다리는 `port-human`(사람) → `port-oneshot` → `port-loop` → `port-multi` →
`port-selfdesign` 이었고, 맨 아래 칸이 사람이었으므로 그 위 네 칸이 모두 같은 모델일
때 "에이전트 구성" 의 효과가 사람 대비로 읽혔다. 철회 후 사다리는 전부 같은 모델
계열이고, 그러면 논문의 세 비교가 모두 **자기 비교**다: Claude 한 번 호출 vs Claude
반복 vs Claude 역할 분화.

자기 비교 자체가 결함은 아니다 — 세 비교는 "반복이/역할 분화가/역할 설계 위임이
값을 하는가" 를 묻고, 그 질문에는 모델을 고정하는 것이 오히려 옳다. 문제는 기준선의
**해석**에 있다. 독자가 물을 수 있는 것은 두 가지고 서로 다르다:

1. 이 하네스에서 반복이 단발보다 나은가? → 모델 고정이 정답이다.
2. 이 에이전트 파이프라인이 "LLM 에 한 번 물어보기" 보다 나은가? → 이때 단발
   호출이 같은 계열이면, 그 계열이 유난히 강하거나 약한 만큼 답이 흔들린다.

2번을 위해 기준선을 다른 계열로 돌리는 것이 검토 대상이다.

---

## Bedrock 에서 실제로 호출된 계열

`list_foundation_models` 는 권한 부여 여부를 말해주지 않으므로, 코퍼스 텍스트가
들어가지 않는 최소 프롬프트(`"Reply with the single word: ok"`)로 `converse` 를
직접 호출해 확인했다. 2026-08-07, `AWS_REGION` 은 환경변수 설정값.

**호출 성공:**

| 계열 | model id | 비고 |
|---|---|---|
| Anthropic | `us.anthropic.claude-opus-5` | 2026-08-07 시점의 최신 별칭. 무날짜 — 아래 "날짜 있는 id 로 고정한다" 참조 |
| Anthropic | `us.anthropic.claude-opus-4-5-20251101-v1:0` | **사다리 전 칸이 쓰는 id** (2026-08-11 결정). dated |
| OpenAI | `openai.gpt-oss-120b-1:0` | open-weight 계열. `us.` 접두 프로파일은 없음 |
| Meta | `us.meta.llama4-maverick-17b-instruct-v1:0` | Llama 4 |
| Meta | `us.meta.llama3-3-70b-instruct-v1:0` | Llama 3.3 70B |
| Mistral | `us.mistral.pixtral-large-2502-v1:0` | Mistral 계열 중 유일하게 호출됨 |
| DeepSeek | `us.deepseek.r1-v1:0` | reasoning 계열. v3.2 는 접근 불가 |
| Amazon | `us.amazon.nova-premier-v1:0`, `amazon.nova-pro-v1:0` | |
| AI21 | `ai21.jamba-1-5-large-v1:0` | |
| Cohere | `cohere.command-r-plus-v1:0` | |
| Writer | `us.writer.palmyra-x5-v1:0` | |

**목록에는 있으나 호출 실패** (프로파일 미존재 또는 미승인): Qwen 3 전체, Z.AI GLM,
MiniMax, Moonshot Kimi, Google Gemma, Mistral Magistral/Devstral, NVIDIA Nemotron,
DeepSeek v3.2. 즉 카탈로그가 넓어 보이는 것과 실제 쓸 수 있는 것이 다르며, 선택은
위 표 안에서 이루어진다.

**규모가 비교 가능한 후보로 좁히면** — 이 과업은 임상 노트에서 PHI 정규식·사전을
작성하는 것이고 지시 준수와 스페인어/독일어 처리가 필요하다 — 현실적인 것은
`llama4-maverick`, `pixtral-large`, `deepseek.r1`, `nova-premier`, `gpt-oss-120b`
정도다. Command R+ 와 Jamba 1.5 는 세대가 이르고, Palmyra 는 이 과업에 대한 공개
평가가 희박하다.

---

## 선택지

### A. 기준선도 Claude (현행)

- **이득**: 세 비교 전부에서 모델이 상수다. `port-loop` vs `port-oneshot` 의 차이는
  하네스의 차이이고 다른 어떤 것도 아니다 — DESIGN §4 가 "인접 칸은 한 가지
  능력만 다르다" 로 사다리를 정당화하는 근거가 그대로 유지된다.
- **비용**: 위 2번 질문에 답할 수 없다. "우리 파이프라인이 LLM 한 번 호출보다 낫다"
  가 "우리 파이프라인이 *우리가 고른* LLM 한 번 호출보다 낫다" 로만 읽힌다. 기준선이
  약하면 사다리 전체가 쉬워 보이고, 강하면 사다리가 값을 못 하는 것처럼 보인다.
  어느 쪽인지 이 실험 안에서는 알 수 없다.

### B. 기준선을 다른 계열로 교체

- **이득**: 자기 비교를 벗어난다. `port-loop` 이 다른 계열의 단발 호출을 이기면
  독자가 "같은 모델이니 당연" 이라고 말할 수 없다.
- **비용, 그리고 이것이 핵심이다**: `port-loop` vs `port-oneshot` 이 **두 가지가
  동시에 다른 비교**가 된다 — 하네스(반복 유무)와 모델 계열. 차이가 나와도 어느
  쪽에서 온 것인지 분해할 수 없고, DESIGN §4 의 사다리 논리("인접 칸은 한 가지만
  다르다")가 맨 아래 칸에서 깨진다. 나머지 두 비교는 영향받지 않지만, 논문이
  "agentic 이 정당한가" 로 내세우는 비교가 바로 그 깨진 칸이다.
- 부수 비용: `rules/{lang}.yaml` 형식 준수와 `regex` 방언 사용을 다른 계열이 얼마나
  지키는지 알 수 없다. 형식 위반으로 낮은 점수가 나오면 그것은 모델의 이식 능력이
  아니라 프롬프트 적합성 측정이고, 그 구분은 사후에 하기 어렵다.

### C. 둘 다 — `port-oneshot` 을 두 계열로 돌린다

- 같은 프롬프트, 같은 표본 없음 조건, 두 모델. 하나는 Claude(사다리 안의 정직한
  기준선), 하나는 타 계열(외부 참조점).
- **이득**: A 의 분해 가능성과 B 의 외부 타당성을 동시에 얻는다. 사다리는 Claude
  기준선으로 읽고, 타 계열 수치는 "이 과업에서 단발 LLM 이 대략 어디쯤인가" 의
  참조로 별도 보고한다.
- **비용**: `port-oneshot` 이 축의 한 값인데 결과가 둘이 된다. 지금 스키마로는
  `results/{corpus}/{detector}/{supervision}/{porting}/` 에 둘을 담을 자리가 없다 —
  모델을 축에 넣거나(모든 경로가 바뀐다), 타 계열을 부차 분석(§10 A1 처럼)으로
  빼거나 해야 한다. 후자가 변경이 작지만, "부차 분석" 은 곧 "본문에서 안 읽히는
  숫자" 이기도 하다.
- 비용은 단발 호출 한 번이므로 금전적으로는 무시할 만하다. 비용은 스키마와 보고
  구조에 있다.

### D. 기준선은 Claude, 상한 참조로 타 계열 단발을 추가

C 의 변형. 타 계열을 "기준선" 이 아니라 "이 과업 난이도의 외부 눈금" 으로 위치시킨다.
사다리 논리는 온전하고, 타 계열 수치는 §4.1 이 잃었다고 적은 것 중 **두 번째**
(품질의 외부 기준점) 를 부분적으로 메운다 — 사람은 아니지만 이 프로젝트가 고르지
않은 시스템이다. 첫 번째(노동 비용)는 메우지 못한다.

---

## 결정에 필요한 것 하나

어느 선택지든, **모델 id·버전·추론 파라미터를 `metrics.json` 의 cost 블록 옆에
기록해야 한다.** 현재 스키마에 모델 필드가 없다 (`REQUIRED_RUN` 은 corpus·detector·
supervision·porting·split). 계열을 하나만 쓰더라도 Bedrock 의 모델은 조용히
갱신되므로, 기록 없는 실행은 6개월 뒤 재현되지 않는다. 이것은 선택지와 무관하게
필요한 변경이고, B·C·D 를 고르면 필수가 된다. **D 를 골랐으므로 필수다** —
`model_id` 를 `REQUIRED_RUN` 에 넣는 작업이 여기서 나온다.

> **2026-08-09 후속.** 위 문단의 `REQUIRED_RUN` 목록은 이 조사 시점(2026-08-08)의
> 상태다. 그 뒤 `model_id` 가 들어갔고, schema 4 에서 `generated`·`commit`·`tree` 가
> 더해져 없으면 거부된다 (DESIGN §10 A2). 여기 문장을 고치지 않고 덧붙이는 이유는
> 이 파일이 그날 측정한 것의 기록이고, 기록을 나중 상태로 덮으면 "언제 무엇을
> 알았는가" 가 사라지기 때문이다.

---

## 왜 OpenAI 직접 API 는 후보가 아닌가

위 표는 Bedrock 안에서만 조사했다. 프로젝트 밖의 API 를 쓰는 선택지가 검토되지
않은 것처럼 보이지 않도록 이유를 적어둔다. PhysioNet 의 지침은 credentialed 데이터를
OpenAI API 로 보내는 것을 금지하고 Bedrock·Azure 를 허용 경로로 지목한다.
`rule_author.md` §1.4 의 오류 스팬 블록은 ±120자의 dev 문맥을 프롬프트에 넣으므로,
문맥을 빼면 §1.4 가 존재하는 이유가 없어지고 넣으면 지침 위반이다. 회피 경로가 없다.

Azure OpenAI 는 허용 경로다. 그러나 별도 계정, human review opt-out 설정,
IRB 문구 확인이 필요하고, 그 세 가지는 부록 숫자 하나를 위해 규정 준수 표면을
넓히는 일이다. 계열 거리를 조금 더 벌기 위해 데이터 거버넌스 경로를 하나 더
여는 교환은 하지 않는다.

---

## `converse` 응답이 구체 모델을 노출하는가 — 측정 (2026-08-08)

[2] Bedrock 클라이언트를 쓰기 전에 확인해야 했던 것. 위 §"결정에 필요한 것 하나"가
`model_id` 를 기록해야 한다고 적었지만, **무엇을** 기록할 수 있는지는 응답이 무엇을
말해주는지에 달려 있다. 코퍼스 텍스트가 없는 최소 프롬프트
(`"Reply with the single word: ok"`) 로 실제 호출해 확인했다. `AWS_REGION=us-east-1`,
boto3/botocore 1.43.54, `retries={"max_attempts": 1}`.

### 측정 결과

**1. `converse` 기본 응답은 모델을 전혀 말하지 않는다.** 최상위 키는
`ResponseMetadata` · `output` · `stopReason` · `usage` · `metrics` 다. 본문에도
HTTP 헤더에도 모델 필드가 없다. 헤더는 `Date` · `Content-Type` ·
`Content-Length` · `Connection` · `x-amzn-RequestId` 뿐이다.

**2. 그러나 요청하면 나온다.** `additionalModelResponseFieldPaths=["/model"]` 를
넘기면 응답에 `additionalModelResponseFields: {"model": "claude-opus-5"}` 가 붙는다.
`invoke_model` 로 Anthropic 원본 페이로드를 보내면 본문에 `"model": "claude-opus-5"`
가 그대로 온다 — 같은 값이다. 즉 필드는 존재하고, `converse` 가 기본 응답에서
감추고 있을 뿐이다.

**3. 그 필드는 요청보다 더 구체적이지 않다.** 이것이 결정적이다. 세 개의 서로 다른
id 로 같은 호출을 해서 비교했다:

| 요청한 id | 응답이 보고한 `model` |
|---|---|
| `us.anthropic.claude-opus-5` | `claude-opus-5` |
| `us.anthropic.claude-opus-4-5-20251101-v1:0` | `claude-opus-4-5-20251101` |
| `us.anthropic.claude-opus-4-6-v1` | `claude-opus-4-6` |

날짜가 있는 id 를 보내면 날짜가 보존되어 돌아온다. 그러므로 필드는 날짜를 깎아내는
정규화기가 **아니다** — 요청이 날짜를 말했으면 응답도 말한다. 그러나 **요청이 말하지
않은 것을 응답이 채워주는 경우는 없다.** 지역 접두어(`us.`)를 떼는 것 외에 필드는
요청받은 id 를 되돌려준다.

**4. `GetInferenceProfile` 도 해상도를 올려주지 못한다.** 별칭은
`arn:aws:bedrock:{us-east-1,us-east-2,us-west-2}::foundation-model/anthropic.claude-opus-5`
세 개로 풀리는데, 셋 다 같은 무날짜 id 다. `GetFoundationModel` 은
`modelLifecycle.startOfLifeTime = 2026-07-23T17:00Z` 를 준다 — 이것은 **이 id 가 언제
등장했는지**이고, 오늘 그 id 뒤에 어느 가중치가 있는지가 아니다.

### 따라서 무엇을 기록하는가

**노출하지 않는 경우로 처리한다.** 응답이 별칭을 확인해줄 뿐 해소해주지 않으므로,
`model_id` 에 적을 수 있는 것은 요청한 별칭과, 응답이 그것을 확인했다는 사실이다.
확인은 공짜가 아니다 — 요청 id 와 응답 id 가 어긋나면 그것은 발견이고, 필드를 안
읽으면 그 발견이 없다. 그래서 클라이언트는 `additionalModelResponseFieldPaths=["/model"]`
를 **항상** 붙이고 돌아온 값을 대조한다.

기록 형태 (`metrics.json` 의 run 블록). 2026-08-11 이후 사다리 전 칸은 위쪽,
A2 의 Llama 팔은 아래쪽이다 (아래 "날짜 있는 id 로 고정한다" 절):

```
"model_id":            "us.anthropic.claude-opus-4-5-20251101-v1:0"
"model_id_reported":   "claude-opus-4-5-20251101"     # 응답이 확인한 것
"model_id_resolution": "dated"                        # 아래 어휘

"model_id":            "us.meta.llama4-maverick-17b-instruct-v1:0"
"model_id_reported":   null                           # 또는 응답이 확인한 무날짜 id
"model_id_resolution": "alias-unresolved"
```

`model_id_resolution` 은 축이 아니라 3값 어휘다 (`config/naming.yaml`):

- `alias-unresolved` — 요청 id 에 스냅샷 날짜가 없고 응답도 더 주지 않았다. A2 의
  Llama 팔이 이 경우이고, 날짜 있는 Llama 4 id 가 없으므로 선택이 아니다.
- `dated` — 요청 id 가 날짜를 담고 응답이 그것을 확인했다. 재현 가능성이 가장 높은
  상태이고, 2026-08-11 이후 사다리 전 칸이 이 경우다.
- `mismatch` — 응답이 요청과 다른 모델을 보고했다. 호출은 성공했어도 기록은
  신뢰할 수 없다. 클라이언트는 이 경우 **거부한다** — 어느 모델이 답했는지 모르는
  응답으로 실험 결과를 만들 수 없다.

`null` 이 아니라 문자열인 이유는 `model_id_absent` 와 같다. "모르겠다" 와 "알아봤고
알 수 없었다" 는 다르고, 후자만이 측정 결과다.

### `GetFoundationModel` 메타데이터도 함께 기록한다 — 2026-08-11

위 측정 4 는 `GetFoundationModel` 이 별칭을 해상해주지 못한다고 적었고 그 판단은
그대로다. 그럼에도 기록하는 이유는 다른 데 있다. `startOfLifeTime` 은 **id 가 언제
등장했는지**를 말하므로, "이 arm 이 부른 스냅샷은 실행 시점에 이미 공개되어 있었는가"
라는 순서 질문에 답한다. 비용은 제어 평면 호출 하나이고 추론이 아니므로 cost 블록에
들어가지 않는다 (`llm_calls` 에 넣으면 이 arm 의 비용이 `port-loop` 과 비교 불가능해지고,
그 이유는 두 arm 어느 쪽과도 무관하다).

기록 위치는 셋이다 — `agent_calls.jsonl`, 그리고 `metrics.json` 과
`paths.formatfailure` 중 그 arm 이 쓰는 것. 셋인 이유는 두 가지다. 호출 로그는
`tools/release_screen.py` 의 deny 목록에 있어 커밋되지 않고 git 에서 복구할 방법이
영영 없다. 그리고 나머지 두 파일은 arm 당 **정확히 하나만** 쓰이므로 (DESIGN §10 A2),
metrics 쪽에만 두면 형식 실패한 arm 은 이 기록을 전부 잃는다.

**run 블록에는 넣지 않는다.** 이것이 이 절의 결정 사항이다. `startOfLifeTime` 은
id 의 등장 시점이고 그날 무엇이 답했는지가 아닌데, run 블록에서
`model_id_resolution` 옆에 놓이면 그 판정의 증거로 읽힌다. 필드 이름이
`model_resolved` 나 `weights_id` 가 아닌 것도 같은 이유다 — 해상하지 않는 것에
해상을 뜻하는 이름을 붙이면 위 측정 4 를 읽지 않은 사람에게는 그것이 측정 결과가 된다.
`tests/mutations/README.md` 의 여섯 번째 계열(주석이 없는 보증을 단언한 사례)이 이번엔
**데이터로** 재발할 수 있는 자리이고, 주석과 달리 데이터는 코드를 안 읽는 독자에게
직접 도달한다.

실패했을 때는 `status: unavailable` 과 예외의 **타입 이름만** 남긴다 (`str(exc)` 는
쓰지 않는다 — botocore 메시지는 실패한 요청을 인용할 수 있고, 이 dict 는 로그로
간다). 그리고 이 조회는 어떤 경우에도 예외를 올리지 않는다: arm 의 단 한 번의 호출은
재실행할 수 없으므로 (DESIGN §6.3), 보조 메타데이터가 그 호출을 막을 수 있으면 안 된다.

### A2 재현성이 어디까지 유지되는가 — 그리고 어디서 끊기는가

**유지되는 것.** 같은 별칭으로 다시 돌리면 같은 코드·같은 프롬프트·같은 표본이
재현된다. 별칭이 어느 계열인지도 확실하다 — Anthropic 대 Meta 라는 A2 의 축은
별칭만으로 명확하므로, **A2 의 주장 자체는 이 한계에 영향받지 않는다.** A2 는
"Claude 한 번 호출 대 Llama 한 번 호출" 이고 두 별칭은 서로 다른 계열을 가리킨다.

**아래 "끊기는 것" 은 2026-08-11 이후 A2 의 Llama 팔에만 적용된다.** 사다리 전 칸과
A2 의 Claude 팔은 dated id 로 핀되었으므로 이 절이 기술하는 실패가 닫혔다 (이 문서
아래 "날짜 있는 id 로 고정한다"). 원문을 고치지 않고 이 문장을 앞에 붙인다 — 무날짜
별칭에 대한 기술은 여전히 정확하고, 달라진 것은 그것이 적용되는 범위다.

**끊기는 것, 그리고 이것을 논문에 명시해야 한다.** Bedrock 이 `claude-opus-5` 뒤의
가중치를 갱신하면 기록으로는 알 수 없다. 응답이 확인해주는 것은 별칭이고, 별칭은
같은 채로 다른 모델을 가리킬 수 있다. 6개월 뒤 같은 명령을 돌려 다른 숫자가 나오면,
**모델이 바뀐 것인지 우리 코드가 바뀐 것인지 이 기록으로는 구분되지 않는다.**
`startOfLifeTime` 은 id 의 등장 시점이므로 이 질문에 답하지 않는다.

부분적으로 메우는 것 둘, 그리고 각각이 못 하는 것:

- **실행 날짜와 커밋 해시.** 언제·어느 코드로 돌았는지는 남는다. 그 날 그 별칭이
  무엇이었는지는 남지 않는다.
- **`usage` 블록의 토큰 수와 `metrics.latencyMs`.** 모델이 조용히 교체되면 토큰
  회계가 달라질 수 있어 사후에 정황 증거가 된다. 증거이지 식별자가 아니다.

메울 수 없는 것: **그 날 그 별칭 뒤에 있던 가중치.** Bedrock 은 그것을 노출하지
않고, 우회 경로는 위 측정 4개 항목이 모두 닫혀 있음을 보인다. 논문은 "모델 별칭을
기록했다"고 써야 하고 "모델을 기록했다"고 쓰면 안 된다. 이 문단이 그 구분의 근거다.

### 날짜 있는 id 로 고정한다 — 2026-08-11 결정, 이전 판단의 번복

**이전에 이 자리에 있던 결론.** "`anthropic.claude-opus-4-5-20251101-v1:0` 은 `dated` 를
얻지만 사다리를 한 세대 뒤진 모델로 돌리는 것이고, DESIGN §4 는 계열 고정을 요구하되
세대를 낮추라고 요구하지 않는다. 재현성을 위해 능력을 내리는 교환은 하지 않되, 내리지
않은 대가를 위 문단으로 적어둔다." 지우지 않고 남긴다 — 무엇을 저울에 올렸고 무엇을
빼먹었는지가 번복의 내용이기 때문이다.

**저울에 없던 항: arm 의 창은 첫 호출부터 구속된다** (DESIGN §6.3). 위 판단은 세대와
재현성을 비교했고, **이 arm 을 다시 돌릴 수 없다는 사실**을 비교에 넣지 않았다. 일반적인
실행에서 `alias-unresolved` 는 "오늘은 확정할 수 없고, 다시 돌려 좁힐 수 있다" 를
뜻한다. 창이 얼어붙은 arm 에서는 **이 arm 이 무엇으로 돌았는지 나중에 확정할 방법이
누구에게도 영영 없다** 를 뜻한다. 위 "부분적으로 메우는 것 둘" 은 별칭이 *언제* 해소
되었는지를 묶을 뿐 *무엇으로* 해소되었는지를 말하지 않고, 얼어붙은 시점 이전으로
거슬러 올라가는 측정은 없다. 교환한 줄 알았던 것과 다른 양이고, 회수되지 않는다.

**결정적인 것: 사다리 전 칸을 핀하면 불일치가 거래되는 게 아니라 소멸한다.** 위 반대
논거는 *부록만* 다른 스냅샷에 핀하는 것에 대한 반대였다 — 즉 **부분 핀**에 대한
반대였고, 핀 자체에 대한 반대로 읽혔다. `port-oneshot`·`port-loop`·`port-multi`(그리고
나중의 `port-selfdesign`)가 모두 `us.anthropic.claude-opus-4-5-20251101-v1:0` 이면
어긋날 두 스냅샷이 애초에 없다. 남는 차이는 *에이전트 자신의 하네스* (Claude Code 가
opus-5 로 돈다) 와 arm 이 호출하는 모델 사이의 차이인데, 이것은 어느 비교의 교란도
아니다 — 하네스는 규칙을 쓰지 않고 채점되지 않는다.

**세대 차이가 무엇을 약화시키는가: §4 의 어떤 주장도 아니다.** 세 비교는 반복·역할
분화·역할 설계 위임이 값을 하는가를 묻는다. 셋 다 *능력을 고정한 상태에서의 하네스
구조*에 대한 질문이고, 능력 수준은 측정 대상이 아니라 통제 대상이다. 오히려 포화가
덜한 지점에서는 칸 사이 차이가 드러날 여지가 더 크다.

**재검토 조건, 그리고 그것이 좁은 이유.** dated `opus-5` 가 제공되면 **아직 얼지 않은**
사다리에만 적용한다. 이미 창이 구속된 arm 은 다시 핀하지 않는다 — 다시 핀하는 것이
불가능하기 때문이다. 그리고 사다리 중간에 모델을 바꾸면 `port-multi` 대 `port-loop` 가
역할 분화와 모델 둘에서 갈리고, 그것이 §4 가 기준선 계열 교체를 거부한 바로 그 2축
실패다. 그래서 규칙은 **사다리는 한 번 핀하고, 새 dated id 는 어느 칸도 얼지 않은
사다리에만 적용한다** 이다. 중간 교체는 새 사다리이고 새 사다리로 보고한다.

**A2 의 Llama 쪽은 핀할 수 없다.** Bedrock 에 날짜 있는 Llama 4 id 가 없다
(`meta.llama4-maverick-17b-instruct-v1:0`, `meta.llama4-scout-17b-instruct-v1:0` — 둘 다
무날짜). 그래서 A2 두 팔의 `model_id_resolution` 은 서로 다르고, 그 비대칭은 선택이
아니라 플랫폼의 것이다. 위 "끊기는 것" 문단은 이제 **A2 의 한쪽 팔에만** 적용되고
사다리의 어느 칸에도 적용되지 않는다.

### prompt caching 을 Bedrock 이 어떻게 보고하는가 — 측정 (2026-08-16)

[3] 캐싱을 구현하기 **전에** 확인해야 했던 것. DESIGN §3 이 감사 접두부 캐싱으로 회차당
프롬프트 토큰이 ~4.7배 줄어든다고 적었지만, 그 절감이 `prompt_tokens` 를 *작게* 만드는
형태로 나타나는지 아니면 같은 총량이 필드로 쪼개지는 형태인지가 스키마를 결정한다 —
`scorer.REQUIRED_COST` 는 양방향으로 닫혀 있어서 쓰는 쪽에서 덮을 수 없다. 배제 관례는
AWS 문서에 있으나 여기서 측정된 바 없었고, **같은 봉투에 대한 이전 주장이 한 번 틀렸다** —
위 측정 1–4 가 그 사례다 (응답이 구체 모델을 말해줄 것이라는 가정, 그리고 별칭이 어딘가에서
해상될 것이라는 가정이 둘 다 틀렸고 호출로 교체됐다). 그래서 문서가 아니라 API 에 물었다.

`tools/probe_prompt_cache.py`, `us.anthropic.claude-opus-4-5-20251101-v1:0`,
접두부는 `docs/prompts/auditor.md` (26060 chars). 코퍼스 텍스트 없음.

| probe | cachePoint | inputTokens | cacheRead | cacheWrite | outputTokens | totalTokens |
|---|---|---|---|---|---|---|
| control | no | 7193 | 0 | 0 | 4 | 7197 |
| write | yes | 21 | 0 | 7172 | 4 | 7197 |
| read | yes | 21 | 7172 | 0 | 4 | 7197 |

```json
[
 {
  "probe": "control",
  "cache_point": false,
  "usage": {
   "inputTokens": 7193,
   "outputTokens": 4,
   "totalTokens": 7197,
   "cacheReadInputTokens": 0,
   "cacheWriteInputTokens": 0,
   "cacheDetails": "(absent)"
  },
  "stop_reason": "end_turn",
  "wall_seconds": 2.293
 },
 {
  "probe": "write",
  "cache_point": true,
  "usage": {
   "inputTokens": 21,
   "outputTokens": 4,
   "totalTokens": 7197,
   "cacheReadInputTokens": 0,
   "cacheWriteInputTokens": 7172,
   "cacheDetails": [
    {
     "ttl": "5m",
     "inputTokens": 7172
    }
   ]
  },
  "stop_reason": "end_turn",
  "wall_seconds": 1.582
 },
 {
  "probe": "read",
  "cache_point": true,
  "usage": {
   "inputTokens": 21,
   "outputTokens": 4,
   "totalTokens": 7197,
   "cacheReadInputTokens": 7172,
   "cacheWriteInputTokens": 0,
   "cacheDetails": "(absent)"
  },
  "stop_reason": "end_turn",
  "wall_seconds": 1.589
 }
]
```

**1. `inputTokens` 는 캐시 읽기를 제외한다 — 확정.** control 의 7,193 이 read 에서 **21**
로 떨어진다. 같은 텍스트, 같은 모델, 차이는 `cachePoint` 하나다. 21 은 가변 꼬리(77 chars)
와 대화 오버헤드이고, 접두부는 `inputTokens` 에서 완전히 사라져 `cacheReadInputTokens`
7,172 로 옮겨간다. 즉 **캐싱을 켠 arm 의 `prompt_tokens` 를 `inputTokens` 로 채우면 그
숫자는 캐싱을 안 켠 arm 의 같은 이름 필드와 같은 것을 세지 않는다.** 회차당 2.12M 이
~257k 로 보이게 되고, 그것은 전송 최적화이지 루프가 덜 읽은 것이 아니다.

**2. `totalTokens` 는 세 경우 모두 7,197 로 동일하다 — 이것이 결정적 증거다.** control 은
7,193+4, read 는 21+4 인데 `totalTokens` 는 변하지 않는다. 그러므로 `totalTokens` 는
`inputTokens + outputTokens` 가 **아니고** 캐시 읽기·쓰기를 포함한 총량이다. 즉
**서비스가 raw 총량을 이미 보고하고 있다** — 우리가 세 필드를 더해 조립할 필요가 없고,
조립하더라도 `totalTokens` 로 교차검증할 수 있다. `prompt_tokens` 를 raw 총량으로 유지하는
결정(§5.5)은 이 필드 위에서 확인 가능하다.

**3. write 에서 read 가 실제로 발생한다 — TTL 5분 안에서.** 두 호출 사이 간격은 1.6초였고
(위 `wall_seconds`), read 의 `cacheReadInputTokens` 가 7,172 로 write 가 쓴 값과 정확히
같다. write 프리미엄이 붙는 것은 첫 호출뿐이므로, 250 회 순차 호출은 write 1회 + read 249회다.
회차 간 40–80분 공백은 5분 TTL 을 넘으므로 **회차마다 write 1회**가 맞는 모델이다.

**4. `cacheDetails` 는 write 에만 나오고 read 에는 없다.** 형태는
`[{"ttl": "5m", "inputTokens": 7172}]` — TTL 별 *쓰기* 내역이고 서비스 모델의 문서
("Empty if no cache creation occurred") 그대로다. read 응답에서는 키 자체가 없다(빈 리스트도
아니다). 그래서 프로브는 없는 키를 0 으로 채우지 않고 `(absent)` 로 적는다 — 0 은
"캐시에서 아무것도 읽지 않았다"는 측정이고 부재는 응답이 캐싱을 언급하지 않았다는 뜻이며,
control 행에서는 후자가 기대되는 결과다 (`bedrock._usage()` 의 부분 블록 거부와 같은 규율).

**5. 캐시 가능 접두부의 최소 길이는 재지 않았다 — 조건과 함께 미측정으로 기록한다.**
`auditor.md` 는 7,193 tokens 로 문서상 하한(이 계열 ~1,024)을 넉넉히 넘고, 위 세 호출에서
`cachePoint` 가 무시된 흔적이 없으므로 하한은 어떤 측정에도 영향을 주지 않았다. **조건은
"캐싱하는 접두부가 auditor.md 인 한"이고, 그 조건이 깨지는 시점이 재야 할 시점이다** —
템플릿보다 짧은 것을 캐싱하려 할 때(코퍼스별 frame 단독, system 블록, 축약된 템플릿).
가정으로 치우는 것과 범위가 붙은 누락은 다르고, 이것은 후자다.

**부수적으로: 이 파일의 문자/토큰 비율은 코퍼스와 다르다.** `auditor.md` 는 26,060 chars ÷
7,193 tokens = **3.62 chars/token** 이고, DESIGN §3 의 교정값 3.8124 는 스페인어 임상 노트가
섞인 프롬프트에서 나온 값이다. 영어 산문이 더 빽빽하다는 뜻이며, §3 의 감사 호출 추정치는
이 방향으로 약간(수 %) 낮게 잡혀 있다. 재계산하면 상수 접두부는 ~7,834 tokens, 회차당 raw
~2.24M, 캐싱 시 과금 환산 ~485k (**4.6배**), 상한 8 은 raw ~15.7M 대 과금 ~3.4M 이다.
§3 의 표(~2.22M / ~15.6M)는 이 오차 범위 안이라 고치지 않는다 — 정정할 값이 아니라
정밀도의 한계이고, 여기 적어 두는 것으로 족하다.
