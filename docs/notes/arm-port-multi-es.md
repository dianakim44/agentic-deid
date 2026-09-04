# `port-multi` / es-meddocan — 첫 실행 기록

축: `es-meddocan` / `R` / `sup-free` / `port-multi`, split `splits/es-meddocan.json`,
`--split dev` (형식 실패에 기록되는 값이고 이 세 호출은 어느 fold 도 읽지 않는다).
실행 2026-09-04.

명령:

```
python3 tools/run_multi.py --corpus es-meddocan \
  --model-id us.anthropic.claude-opus-4-5-20251101-v1:0 --step profile
```

> **결과: 형식 실패 (format failure).** Profiler 의 응답이 JSON 으로 파싱되지 않아
> `profile.json` 은 쓰이지 않고 `format_failure.json` 이 쓰였다. Mapper 와
> LexiconBuilder 는 호출되지 않았다 — 다음 호출의 입력이 방금 실패한 산출물이므로
> 드라이버가 첫 정지에서 멈춘다. **이 셀은 소진됐고 수리하지 않는다.**
>
> 이것은 사고가 아니라 **DESIGN §10 A2 가 미리 등록한 결과 중 하나**다.
> `arm-port-oneshot-es.md` 가 같은 문장으로 시작하는 것은 우연이 아니고, §4 가
> 그것을 다룬다.

아래 §1 이 arm 의 결과이고, §2 는 arm 의 결과가 아닌 별도 관찰이다. **두 절을 섞어
읽으면 안 된다** — 선례가 그 분리를 왜 그렇게 강하게 두는지 §2.1 에 다시 적었다.

---

## 1. arm 의 결과

이 절만이 arm 의 결과다. 실험에 인용할 수 있는 것은 여기까지다.

### 무엇이 일어났는가

`multi` 의 첫 저술 단계에서 걸렸다. 그 앞은 모두 정상이었다: 로깅 게이트를 통과하고
(2026-09-04 당일 기록), 창을 얼리고, 인벤토리를 필터링하고, 프롬프트를 조립하고,
호출하고, 호출을 로그에 적고, 그다음 `parse_object` 가 거부했다.

```
the profile response is not JSON (JSONDecodeError at position 0 of 1358
characters). §2.1 asks for one object and no fence; nothing is stripped here,
because stripping is a repair with the failure count still reading zero. No part
of the response is quoted in this message (CLAUDE.md); the response verbatim is
in format_failure.json.
```

위치 0 — 응답의 첫 문자가 백틱이다. 재시도는 없다 (§10 A2, 형식 준수 재시도 0 회).
그래서 `profile.json` 이 없다. **0 키의 profile 이 아니라 없음이다** — 파싱에
성공하고 필드가 비어 있었다는 것과 구별되어야 하기 때문이고, 선례가 "파일 이름이
답이고 `status` 필드가 아니다" 로 적은 것과 같은 판단이다.

### 디스크에 있는 것

| 경로 | bytes | 무엇인가 |
|---|---|---|
| `window_freeze.json` | 1076 | 여섯 창 해시, `revision: 1`, 호출 **전에** 취득 |
| `agent_calls.jsonl` | 2424 | 한 줄 — `iteration 0`, `role profiler`, `outcome called` |
| `format_failure.json` | 4674 | 응답 원문 1358자, `failure_schema` 3 |

`profile.json`, `mapping.yaml`, `lexicons/es/`, `lexicon_manifest.json`,
`artefact_freeze.json` 은 **없다.** `artefact_freeze.json` 이 없는 것은
`freeze_artefacts()` 의 "셋 아니면 없음" 규율이 작동한 것이지 누락이 아니다.

호출 계수는 `profiler: 1  mapper: 0  lexicon_builder: 0` 이다.

#### `agent_calls.jsonl` 은 더 이상 디스크에 없고 복구할 수 없다 (2026-09-04)

위 표의 두 번째 행은 이 arm 이 돌았을 때의 상태다. 그 파일은 **지워졌고 되돌릴 수
없다.** `tests/test_run_fold.py` 가 `run_fold` CLI 를 실제 results 루트에
`porting=port-multi` 로 돌리고 `finally` 에서 그 디렉토리를 지웠다 — 그 정리의 안전
근거는 "다른 무엇도 이 arm 을 쓰지 않는다" 였고, 이 arm 의 기록이 커밋된 날 만료되었다.
`window_freeze.json` 과 `format_failure.json` 은 추적되므로 git 에서 복구했다.
`agent_calls.jsonl` 은 deny 대상이고 gitignore 되어 있다 — §1.4 가 dev 코퍼스 텍스트를
담기 때문이다 — 그래서 **arm 디렉토리의 사본이 존재한 유일한 사본이었다.**

**살아 있는 것을 잃지는 않았다.** 이 arm 은 형식 실패로 끝났고 종료된 상태이며, 새
실행은 새 arm 이므로(아래 §3) 이 파일이 앞으로 무엇을 위해 읽힐 일은 없었다. 잃은
것은 기록의 완전성이다: 그 한 줄이 이 arm 의 **유일한 호출 기록**이었고,
`role profiler` · `outcome called` · `iteration 0` 이라는 판정이 남아 있던 곳이었다.

**고유하게 잃은 사실은 없고, 그것은 설계가 아니라 운이다.** `format_failure.json` 이
같은 비용 블록과 응답 해시, 창 해시 여섯 개를 우연히 중복 보유한다. 어떤 규약도 그
필드들을 두 파일에 두라고 하지 않았으므로, 다음에 호출 로그를 잃는 arm 에는 같은
사실을 담은 두 번째 파일이 없을 수 있다. 위 표의 bytes 값과 그 한 줄의 내용은 이
파일에 적힌 것이 전부이고, 이제 그것이 일차 출처다.

재발 방지는 세 갈래로 넣었다 (`tests/mutations/README.md` §"The eighth of the family"):
테스트가 돌지 않은 arm 을 쓰고 쓰기 전에 부재를 단언한다, `tests/conftest.py` 가
`results/` 스냅샷을 세션 시작·종료에 비교하고 삭제가 있으면 종료 코드를 1 로 만든다,
그리고 그 비교는 `git ls-files` 가 아니라 스냅샷이다 — git 에게 물어보는 가드는 복구
가능한 두 파일을 보고 복구 불가능한 하나를 놓친다.

### 비용과 모델 식별

| | |
|---|---|
| `llm_calls` | 1 |
| `prompt_tokens` | 11,108 |
| `completion_tokens` | 537 |
| `wall_seconds` | 8.312 |
| `model_id` | `us.anthropic.claude-opus-4-5-20251101-v1:0` |
| `model_id_reported` | `claude-opus-4-5-20251101` |
| `model_id_resolution` | `dated` |

`dated` 는 2026-08-11 의 핀 결정이 의도한 값이다 (`baseline-model-family.md`).
`port-oneshot` 의 첫 실행과 같은 세 값이고, 그것이 이 실행을 그 실행과 비교
가능하게 만드는 부분이다.

### 생애주기 기록

`GetFoundationModel` 메타데이터가 형식 실패 분기에서도 기록되었다.

| | |
|---|---|
| `model_name` | Claude Opus 4.5 |
| `status` | ACTIVE |
| `start_of_life_time` | 2025-11-24T00:00:00+00:00 |

### 프로파일 필드

**없다.** 적재된 것이 없으므로 이 셀의 profile 은 존재하지 않는다. §6.7.6 의 P1 은
"refusal count, 0 이 통과" 이고 이 실행은 **refusal 을 세는 지점에 도달하지 못했다**
— P1 의 값은 0 이 아니라 미측정이다. P2·P3·P4·P5 도 같다. 이 arm 은 Profiler 에
대한 §6.7.6 의 어느 칸도 채우지 않았다.

---

## 2. 별도 관찰 — arm 의 결과가 아니다

**아래는 실행 후 응답 파일을 따로 조사한 것이고, arm 이 산출한 것이 아니다.**
어느 표에도 실험 수치로 들어가지 않는다.

### 2.1 이 구분을 왜 이렇게 강하게 두는가 — 먼저 적는다

선례(`arm-port-oneshot-es.md` §2)는 이 문단을 관찰 뒤에 두었다. 여기서는 앞에 둔다.
"펜스만 벗기면 통과했다" 는 문장이 재시도 한 번의 논거처럼 읽히고, §10 A2 가 거부한
것이 정확히 그 논거이기 때문이다. 두 진술은 다르다:

- **arm 의 결과**: 이 모델은 이 프롬프트로 한 번 호출해서 사용 가능한 profile 을
  내지 못했다.
- **별도 관찰**: 실패가 형식 층 한 곳에서 왔고 profile 의 내용 층에서 오지 않았다.

두 번째가 첫 번째를 취소하지 않는다. 그러나 두 번째를 적어두지 않으면 다음 판단이
근거를 잃는다 — 프롬프트를 고칠 것인지, 무엇을 고칠 것인지는 실패가 어느 층에서
왔는지에 달려 있고, 창이 얼었으므로 이 arm 을 다시 돌려 확인할 방법이 없다 (§6.3).

**그리고 이번에는 그 판단이 선례보다 강한 근거를 요구한다.** 같은 실패의 두 번째이고
(§4), 조치가 프롬프트 한 곳이 아니라 규약과 검사로 가야 하는지가 여기 달려 있다.

### 2.2 응답의 형태

응답은 29행이고 1행이 ` ```json `, 29행이 ` ``` ` 이며 **펜스 밖에 비어 있지 않은
줄이 하나도 없다.** 서문도 맺음말도 없다. 즉 §2.1 이 금지한 네 가지 중 세 가지
— 서문·맺음말·언어 태그 없는 펜스 — 는 지켜졌고, 어긴 것은 펜스와 `json` 태그다.

### 2.3 펜스 안쪽만 떼어 검증한 결과 — 임시 프로세스에서, 아무것도 쓰지 않았다

펜스를 벗겨 **실제** `validate_profile()` 을 같은 필터 인벤토리에 대해 돌렸다.
인벤토리의 `inventory_filtered_sha256` 이 호출 시 기록된 값과 동일함을 먼저 확인했다.

- **refusals 0**
- `unresolved: []`
- 13 키, §2.1 스키마와 일치
- `cites` **11개 전부** 필터된 인벤토리에 실존하는 경로로 해소된다. §2.1 이
  "이 산출물에서 정직함이 *검증 가능한* 유일한 부분" 이라고 부른 것이 이것이다
- 관례 필드 **8개 전부** 가 각자 인용한 경로에 인벤토리가 담고 있는 값과 일치한다

**인벤토리와 어긋난 항목은 없다.** `type_inventory` 는 22개 라벨이고 **인벤토리에
없는 것이 0개**다. 인벤토리가 허용하는 라벨은 30개(brat-flat 22 ∪ XML 2단 coarse 8)
이며, 빠뜨린 8개는 *다른 인코딩*의 coarse 층이다. 스스로 선언한
`annotation_encoding: brat_standoff` 와 `type_system_level: flat` 과 일관되므로
이것은 누락이 아니다. 세 type-count 경로 중 맞는 것(`phi_types_brat_flat`)을
인용했다.

**산출물은 손대지 않았다.** 펜스는 그대로 있고 `format_failure.json` 도 그대로다.
진단은 사본에서 했고 디스크에 쓴 것은 없다.

### 2.4 `group_key` — 유일한 내용 층 관찰이고, 입력 설계에 대한 증거다

`patient_key_available: false` 는 인벤토리와 얼린 split 양쪽과 일치한다.
`group_key: "document_id_stem"` 은 **일치하지 않는다**: `splits/es-meddocan.json` 은
`unit: document`, `n_groups: 1000`, 근거 "no patient key exists; no grouping confirmed
by identifier" 를 기록한다 — §9.5 step 2 가 stem 그룹을 기각했고, 48개 stem 이 문서를
둘 이상 담지만 통과한 것이 하나도 없다. `group_key` 어휘에는 바로 이 경우를 위한
`filename` ("문서 하나가 곧 그룹인 경우") 이 있었고 선택 가능했다.

**그러나 이것을 에이전트의 판단 오류로 적으면 틀린다.** stem 그룹을 기각하는 추론은
DESIGN §9.5 와 split 파일에 있고 **에이전트가 본 인벤토리에는 없다.** 인벤토리가
그 자리에 담고 있는 것은 `identifiers.stem_parse_rule` — stem 을 어떻게 파싱하는지에
대한 규칙이고, 그것을 그룹 키로 읽는 것은 그 입력이 지지하는 답이다. 에이전트는
자기가 본 것과 어긋나지 않는 답을 냈다. 그래서 이것은 **에이전트에 대한 증거가
아니라 입력 설계에 대한 증거**이고, §6.7.6 의 P4 문면이 그렇게 고쳐져야 한다.

같은 관찰의 다른 반쪽: `unresolved: []` 는 **유일하게 틀린 그 필드에 불확실성 0 을
주장한다.** §2.1 은 `unresolved` 를 "에이전트가 모른다고 말하는 방법"으로 두고
"침묵과 추측이 같은 바이트가 되지 않게" 한다고 적었다. 이 실행은 그 장치가 그
목적을 달성하지 못한 한 사례다 — 값을 인벤토리에서 직접 읽을 수 있었으므로
에이전트에게는 추측이 아니었고, 추측이 아니라는 판단 자체가 틀릴 수 있다는 것을
이 필드는 표현하지 못한다.

---

## 3. 조치는 프롬프트 수리가 아니다

**이 실행은 폐기하지 않는다.** 위의 모든 것이 기록된 결과다. `format_failure.json`
과 `window_freeze.json` 은 그대로 있고, 이 셀은 영구히 소진됐다. 재호출하지 않는다.

무엇을 고치는지는 §4 가 정한다.

---

## 4. 같은 실패의 두 번째다 — 그리고 첫 조치가 규약이 아니라 파일 하나였다

`port-oneshot` 의 첫 실행(2026-08-11)이 같은 실패였다. 그쪽은 YAML 을 ` ```yaml `
펜스로 감쌌고 `load_rules` 가 1행 1열의 백틱에서 거부했다. 이쪽은 JSON 을
` ```json ` 펜스로 감쌌고 `parse_object` 가 위치 0 에서 거부했다. **같은 원인, 다른
직렬화, 다른 검증기.**

### 첫 조치가 무엇이었는가

`d44bd14` 가 `rule_author.md` §2 에 출력 형식을 명시했다. 그 커밋과
`arm-port-oneshot-es.md` §5 는 **최소 변경을 의도적으로 선택했다**, 그리고 그 이유는
당시로서 옳았다: 형식 지시 외의 것을 함께 고치면 두 실행의 차이가 형식 하나로
귀속되지 않는다. 함께 넣은 것은 한 문장이었다 —

> The fenced block below is an *example inside these instructions*, and the fence is
> how this document quotes it.

### 그 조치가 왜 이 실패를 막지 못했는가

**완화책이 문장이었고, 그 문장은 자기가 무력화하려는 펜스와 함께 복사됐다.**
그 뒤에 쓰인 네 프롬프트 — `auditor.md`(8d3d595), `profiler.md`(020abfa),
`mapper.md`(f6d8ed2), `lexicon_builder.md`(911bc07) — 는 전부 같은 형태를 물려받았다:
펜스를 굵은 글씨로 금지하는 문단, 그 바로 다음에 펜스가 이 문서의 인용 방식이라고
설명하는 문장, 그 바로 다음에 ```json 펜스로 감싼 스키마 예시. 다섯 파일 전부가
지금 그 형태다.

즉 첫 조치는 **한 파일의 문면**을 고쳤고 **인용 규약**은 고치지 않았다. 규약이
그대로였으므로 다음에 쓰인 프롬프트들은 결함을 상속했고, 그 상속을 막는 검사는
없었다. `port-oneshot-nofence` 가 통과했다는 사실이 이것을 가렸다 — 고쳐진 그
한 파일이 그 뒤로 문제를 일으키지 않았으므로, 조치가 충분했다는 증거처럼 읽혔다.

### 그래서 이번 조치는 구조여야 한다

지시와 시연이 어긋난 문서에서 모델이 시연을 따르는 것은 이 저장소에서 두 번
관찰된 일이다. 문장을 더 강하게 쓰는 것은 세 번째를 막지 못한다 — 첫 번째 조치가
이미 문장이었기 때문이다. 필요한 것은 세 가지이고 순서가 있다:

1. **다섯 프롬프트의 예시 인용 방식을 펜스가 아닌 것으로 바꾼다.** 어느 것으로
   바꿀지는 규약으로 정하고 한 파일의 선택으로 두지 않는다.
2. **펜스를 금지하는 프롬프트가 펜스를 포함하지 않는지 검사하는 테스트를 둔다.**
   `rule_author.md` 를 포함해 전부에 적용하고, 파일 목록을 열거하지 않고 성질로
   고른다 — 열거하면 여섯 번째 프롬프트가 조용히 빠진다.
3. **그 검사를 제거하는 뮤테이션과, 특정 파일만 면제하는 뮤테이션을 둔다.**
   두 번째가 중요하다: 이 사건의 실제 형태가 "한 파일만 고쳐졌다" 이므로,
   면제를 허용하는 검사는 이 사건을 다시 통과시킨다.

`tests/mutations/README.md` 가 모으는 계열 — 어떤 장치가 두 경우를 구별하지 못하고
안심되는 쪽으로 해소한 사건들 — 에 이것도 속한다. 여기서 구별되지 않은 두 경우는
*이 파일의 형식 지시가 고쳐졌다* 와 *이 저장소의 인용 규약이 고쳐졌다* 이고,
`port-oneshot-nofence` 의 통과가 둘을 같은 것으로 읽게 했다.

### 새 실행은 새 arm 이고 재시도가 아니다

`port-oneshot-nofence` 의 선례가 그대로 적용된다 (DESIGN §4, §10 A2): 개정된
프롬프트는 새 `porting` 값으로 돈다. 이름은 시도 횟수가 아니라 프롬프트의 성질을
가리켜야 한다. 이 arm 의 경로는 덮이지 않고, 새 arm 은 자기 창을 새로 얼린다 —
이미 호출한 arm 의 기록을 다시 얼리는 일은 없다. `profiler_sha256` 을 포함해 여섯
해시 중 다섯이 움직일 것이므로, 그 편집은 `docs/notes/window-freeze-history.md` 가
세는 프롬프트 개정에도 해당한다.

새 식별자와 그 근거는 naming.yaml 과 DESIGN §4 에 적는다.
