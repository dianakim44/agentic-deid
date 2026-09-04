# 프롬프트가 적재 가능한 산출물을 내는가 — 형식 프로브 측정 기록

`tools/probe_prompt_format.py` 가 append 하는 파일이다. **프로브이고 게이트가 아니다**:
`src/` 의 무엇도 이 도구를 import 하지 않고, arm 도 결과 디렉토리도 만들지 않으며,
실패한 프로브는 저장소를 성공한 프로브와 똑같이 유능한 상태로 남긴다. 이 파일은 측정치만
담는다.

**왜 재는가.** 프롬프트 템플릿은 모델에 그대로(verbatim) 전송된다. 그래서 템플릿 안의
예시는 응답이 모방할 수 있는 시연이고, 이 저장소에서 두 번 모방되었다 —
`port-oneshot` 첫 호출은 YAML 을 펜스로 감쌌고 `load_rules` 가 1행 1열에서 거절했으며,
`port-multi` 첫 호출은 JSON 을 펜스로 감쌌고 `parse_object` 가 위치 0 에서 거절했다
(`arm-port-oneshot-es.md`, `arm-port-multi-es.md`). 첫 수정은 한 파일의 문구였고, 그
뒤에 쓰인 네 프롬프트가 그 문구를 **무력화하려던 펜스 블록과 함께** 물려받았다.

그래서 예시를 제거한다. 문제는 `rule_author.md` 와 `auditor.md` 에는 **측정된 통과
기록**이 있다는 것이다:

| 프롬프트 | 측정된 통과 기록 |
|---|---|
| `rule_author.md` | 적재 가능한 `rules/iter1..8/es.yaml` 8건 (`port-loop`) + `port-oneshot-nofence` 1건, 그리고 `call-variance.md` 의 5 draw 에서 형식 실패 0/5 |
| `auditor.md` | 파싱 가능한 `audit_report.json` 7건 (iter2–8, flags 167/185/185/126/174/132/176) |

예시 제거는 그 기록을 **아무도 측정한 적 없는 프롬프트**와 교환하는 일이다. 이 프로브가
그 측정이고, 교환이 커밋되기 전에 — arm 에서 발견되는 대신 — 수행된다.

**재시도 정책은 도구의 `RETRY_POLICY` 상수에 있고 여기서 되풀이하지 않는다.** 요약만:
프롬프트당 1회, 형식 실패에 대한 재시도 없음, 선언된 draw 수는 3 이 상한. 도구에 시도
루프가 존재하지 않으며 `bedrock.MAX_ATTEMPTS == 1` 이므로 전송 계층도 재시도하지 않는다.

**n = 1 이 지지하는 것과 지지하지 않는 것.** 통과해도 위 기록과 대등함(parity)을
세우지 못한다 — 펜스 판본은 형식 실패 0/5 에 arm 산출물 8건을 갖고 있고, 1회 통과는
그보다 약한 증거다. 세우는 것은 "예시를 제거한 프롬프트가 *범주적으로* 고장나지
않았다" 이며, 커밋을 막고 있던 질문은 그것이다.

<!-- 측정치는 이 아래에 append 된다. -->

## 예시를 제거한 `rule_author.md` · `auditor.md` — 형식 프로브 (2026-09-04)

`tools/probe_prompt_format.py`, `us.anthropic.claude-opus-4-5-20251101-v1:0`, es-meddocan / es. 정책: one call per prompt; no retry on a format failure; declared draws capped at 3. 코퍼스 텍스트 없음 — RuleAuthor 는 회차 1 프롬프트라 §§1.3–1.4 가 비어 있고, Auditor 는 이 파일에서 만든 문서를 마스킹한다.

| probe | draw | outcome | detail | completion tokens | wall s | prompt | response |
|---|---|---|---|---|---|---|---|
| auditor | 1 | **parsed** | flags=1, refused=5 | 221 | 5.919 | 3455c516711f | 9f885f05ebdb |
| rule_author | 1 | **loaded** | rules=22, layers={'context_cue': 16, 'gazetteer': 1, 'regex_checksum': 5} | 1651 | 23.649 | fa9502775b3a | 24ab49a17c0e |

<details><summary>draw 별 기록</summary>

    [
     {
      "probe": "auditor",
      "draw": 1,
      "prompt_chars": 33702,
      "prompt_sha256": "sha256:3455c516711f95f22cbdf091506f9cad07a0455e0a88c83a125616e4e27cee25",
      "response_chars": 462,
      "response_sha256": "sha256:9f885f05ebdb3862465b966b48404da6ccd88af7d187afee8287bf5eb4f3bdce",
      "prompt_tokens": 9002,
      "completion_tokens": 221,
      "wall_seconds": 5.919,
      "stop_reason": "end_turn",
      "model_id_reported": "claude-opus-4-5-20251101",
      "outcome": "parsed",
      "error_type": null,
      "flags": 1,
      "refused": 5,
      "refusal_reasons": [
       "crosses_a_line",
       "inside_a_mask_tag"
      ],
      "by_phi_type": {
       "LOCATION_AREA": 1
      }
     },
     {
      "probe": "rule_author",
      "draw": 1,
      "prompt_chars": 56973,
      "prompt_sha256": "sha256:fa9502775b3ac20a0ea81b933a0fa094c731f2d27642212b081ba7a4fc4aa4a6",
      "response_chars": 3977,
      "response_sha256": "sha256:24ab49a17c0e6438b55ee200368d7523bd9a8828f2c9065a65bb94082e26ae79",
      "prompt_tokens": 14874,
      "completion_tokens": 1651,
      "wall_seconds": 23.649,
      "stop_reason": "end_turn",
      "model_id_reported": "claude-opus-4-5-20251101",
      "outcome": "loaded",
      "error_type": null,
      "rules": 22,
      "layers": {
       "context_cue": 16,
       "gazetteer": 1,
       "regex_checksum": 5
      }
     }
    ]

</details>

## 예시를 제거한 `rule_author.md` · `auditor.md` — 형식 프로브 (2026-09-04)

**이 실행의 이유:** auditor.md 를 고친 뒤의 새 측정이고 재시도가 아니다. 1차 측정에서 flag 6건 중 5건이 crosses_a_line·inside_a_mask_tag 로 거절되었고, 원인을 찾아보니 §2.1 에 내가 쓴 `line` 이 1-based 라는 서술이 틀렸다 — validate_flags 는 0 <= line_no < len(lines) 로 0-based 다. 프롬프트가 바뀌었으므로 같은 프롬프트의 두 번째 draw 가 아니라 다른 프롬프트의 첫 draw 다. prompt_sha256 이 1차와 다른 것이 그 주장의 근거다.

`tools/probe_prompt_format.py`, `us.anthropic.claude-opus-4-5-20251101-v1:0`, es-meddocan / es. 정책: one call per prompt; no retry on a format failure; declared draws capped at 3. 코퍼스 텍스트 없음 — RuleAuthor 는 회차 1 프롬프트라 §§1.3–1.4 가 비어 있고, Auditor 는 이 파일에서 만든 문서를 마스킹한다.

| probe | draw | outcome | detail | completion tokens | wall s | prompt | response |
|---|---|---|---|---|---|---|---|
| auditor | 1 | **parsed** | flags=5, refused=1 | 222 | 4.639 | 0549beac752d | b341c0ddf01c |

<details><summary>draw 별 기록</summary>

    [
     {
      "probe": "auditor",
      "draw": 1,
      "prompt_chars": 33974,
      "prompt_sha256": "sha256:0549beac752d36c646c42f26e86032a99c9787322243d77c386381130c6d5f38",
      "response_chars": 467,
      "response_sha256": "sha256:b341c0ddf01cda6ce1d582da38bd3254098fd17683f09be1c75a8c03e04f32dd",
      "prompt_tokens": 9084,
      "completion_tokens": 222,
      "wall_seconds": 4.639,
      "stop_reason": "end_turn",
      "model_id_reported": "claude-opus-4-5-20251101",
      "outcome": "parsed",
      "error_type": null,
      "flags": 5,
      "refused": 1,
      "refusal_reasons": [
       "inside_a_mask_tag"
      ],
      "by_phi_type": {
       "AGE": 1,
       "DATE": 1,
       "LOCATION_AREA": 1,
       "LOCATION_STREET": 1,
       "PROFESSION": 1
      }
     }
    ]

</details>
