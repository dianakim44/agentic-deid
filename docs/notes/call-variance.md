# 같은 입력에 대한 모델 출력의 변동 — 측정 기록

`tools/probe_call_variance.py` 가 append 하는 파일이다. 프로브이고 게이트가 아니다:
`src/` 의 무엇도 이 도구를 import 하지 않고, arm 도 결과 디렉토리도 만들지 않는다.
해석 규칙은 DESIGN §3 "Call-to-call variance" 절에 있고 여기서 되풀이하지 않는다.
이 파일은 측정치만 담는다.

**왜 재는가.** δ 는 "이번 회차의 개선이 멈출 만큼 작은가" 에 답한다. "이번 회차의
개선이 같은 프롬프트를 두 번 보냈을 때의 차이보다 큰가" 에는 답하지 않는다. 뒤쪽은
임계값이 아니라 계측기의 성질이고, 그것을 모르면 δ 로 발화한 종료가 코퍼스에
수렴한 것인지 임계값에 수렴한 것인지 구분할 수 없다.

**채점하지 않는다.** 규칙 집합의 변동만 잰다. 누출률 변동을 직접 얻으려면 draw 를
dev 에 채점해야 하고, 그러면 "어느 draw 가 dev 에서 더 나았는가" 가 사람을 거쳐 다음
프롬프트로 흐른다 — sealed 위반이 아니라 dev 과적합이다. 근거와, 나중에 5회 채점이
필요해질 때 무엇이 먼저 필요한지는 DESIGN §3 에 있다. 그래서 **누출률의 변동 폭은
미측정이고, 규칙 집합의 변동에서 추론하지 않는다.**

---

## n = 2 에서 이미 있던 값 — `port-oneshot-nofence` 대 `port-loop` 회차 1 (2026-08-20)

이것은 프로브가 만든 것이 아니라 두 arm 이 우연히 같은 입력을 보내서 생긴 값이다.
프로브가 존재하는 이유이므로 먼저 적는다.

| | `port-oneshot-nofence` | `port-loop` iter 1 |
|---|---|---|
| prompt `text_sha256` | `sha256:6528088f2ae3f8da…` | 동일 |
| prompt tokens | 14071 | 14071 |
| model id | `us.anthropic.claude-opus-4-5-20251101-v1:0` | 동일 |
| completion tokens | 2325 | 2791 |
| 규칙 수 | 27 | 31 |
| leak `fully_covered` | 0.5600 | 0.5961 |
| leak `relaxed` | 0.4850 | 0.4716 |
| P / R / F1 (relaxed) | 0.7919 / 0.5150 / 0.6241 | 0.6888 / 0.5270 / 0.5972 |

`rule_id` 는 14개만 공유한다 (nofence 전용 13, port-loop 전용 17). 공유된 14개 중
층이나 유형이 다른 것은 없다.

**Δ leak `fully_covered` = 0.0361, 입력 고정, n = 2.** δ 의 하한 0.005 의 7.2배다.
이것이 지금까지 측정된 유일한 누출률 변동이고, 한 쌍이라는 것이 이 값의 가장 큰
한계다 — 두 draw 의 차이는 분포의 폭에 대한 하한도 상한도 아니다.

---

<!-- 프로브 측정치는 이 아래에 append 된다. -->

## 같은 프롬프트를 5회 호출했을 때의 규칙 집합 변동 — 측정 (2026-08-21)

`tools/probe_call_variance.py`, `us.anthropic.claude-opus-4-5-20251101-v1:0`, es-meddocan / es. 프롬프트 53644 chars, `sha256:6528088f2ae3f8dad88e08468ae86ba2c37edc9ae61b49d270b47366372ba61b` — 회차 1 이 보낸 것과 바이트 동일. 채점하지 않는다 (DESIGN §3).

| draw | outcome | rules | completion tokens | wall s | response |
|---|---|---|---|---|---|
| 1 | loaded | 28 | 2642 | 36.254 | `7c903f234c51` |
| 2 | loaded | 28 | 2423 | 33.275 | `f7607954d542` |
| 3 | loaded | 29 | 2473 | 32.477 | `f1adfc38a8d1` |
| 4 | loaded | 31 | 2942 | 39.131 | `eae9a2cffbdb` |
| 5 | loaded | 28 | 2486 | 30.375 | `43debdedfb66` |

규칙 수 28–31, 서로 다른 rule_id 85개 중 모든 draw 에 등장 5개 · 한 draw 에만 등장 57개. 쌍별 Jaccard 0.098–0.4286 (평균 0.2417, 10쌍). 형식 실패 0/5.

층별 draw 간 범위:

| layer | min | max |
|---|---|---|
| `context_cue` | 14 | 17 |
| `gazetteer` | 0 | 1 |
| `regex_checksum` | 11 | 14 |

<details><summary>draw 별 기록과 쌍별 비교</summary>

```json
{
 "draws": [
  {
   "draw": 1,
   "response_chars": 7069,
   "response_sha256": "sha256:7c903f234c513e3c19bee45a599e33e721cc319040da00c4098eb39a2caa0f3f",
   "completion_tokens": 2642,
   "prompt_tokens": 14071,
   "wall_seconds": 36.254,
   "stop_reason": "end_turn",
   "outcome": "loaded",
   "error_type": null,
   "rules": 28,
   "rule_ids": [
    "es:age_label_cue",
    "es:age_years_pattern",
    "es:birth_date_cue",
    "es:date_dmy_dash",
    "es:date_dmy_dot",
    "es:date_dmy_slash",
    "es:date_month_year_only",
    "es:date_spanish_month",
    "es:date_spanish_month_short",
    "es:dni_checksum",
    "es:domicilio_cue",
    "es:email_pattern",
    "es:firmado_cue",
    "es:hospital_gaz",
    "es:hospital_label_cue",
    "es:medico_label_cue",
    "es:nass_label_cue",
    "es:nhc_label_cue",
    "es:nie_checksum",
    "es:nss_pattern",
    "es:patient_label_cue",
    "es:phone_label_cue",
    "es:phone_pattern",
    "es:postal_code_pattern",
    "es:profession_de_cue",
    "es:street_calle_cue",
    "es:title_don_prefix",
    "es:title_dr_prefix"
   ],
   "layers": {
    "context_cue": 14,
    "gazetteer": 1,
    "regex_checksum": 13
   },
   "phi_types": {
    "AGE": 2,
    "CONTACT": 3,
    "DATE": 7,
    "ID": 5,
    "LOCATION_AREA": 1,
    "LOCATION_STREET": 2,
    "NAME": 5,
    "ORGANISATION": 2,
    "PROFESSION": 1
   }
  },
  {
   "draw": 2,
   "response_chars": 6363,
   "response_sha256": "sha256:f7607954d54240b121f83a2d596a36710cd40143b9f1e7de1dc42e0180c4a5a2",
   "completion_tokens": 2423,
   "prompt_tokens": 14071,
   "wall_seconds": 33.275,
   "stop_reason": "end_turn",
   "outcome": "loaded",
   "error_type": null,
   "rules": 28,
   "rule_ids": [
    "es:age_label",
    "es:age_years",
    "es:born_date_cue",
    "es:calle_prefix",
    "es:centro_salud_prefix",
    "es:clinica_prefix",
    "es:collegiate_number",
    "es:date_dmy_dash",
    "es:date_dmy_dot",
    "es:date_dmy_slash",
    "es:date_month_abbrev",
    "es:date_month_text",
    "es:dni_pattern",
    "es:domicilio_cue",
    "es:email_pattern",
    "es:hospital_prefix",
    "es:locality_cue",
    "es:nass_label",
    "es:nhc_label",
    "es:nie_pattern",
    "es:patient_label",
    "es:phone_es",
    "es:postal_code",
    "es:profession_cue",
    "es:servicio_de_cue",
    "es:signed_by_cue",
    "es:title_don_prefix",
    "es:title_dr_prefix"
   ],
   "layers": {
    "context_cue": 17,
    "regex_checksum": 11
   },
   "phi_types": {
    "AGE": 2,
    "CONTACT": 2,
    "DATE": 6,
    "ID": 5,
    "LOCATION_AREA": 2,
    "LOCATION_STREET": 2,
    "NAME": 4,
    "ORGANISATION": 4,
    "PROFESSION": 1
   }
  },
  {
   "draw": 3,
   "response_chars": 6455,
   "response_sha256": "sha256:f1adfc38a8d106c86f45c9cb5a7070f31d55fef6ad9528e0326a088d3e2c236e",
   "completion_tokens": 2473,
   "prompt_tokens": 14071,
   "wall_seconds": 32.477,
   "stop_reason": "end_turn",
   "outcome": "loaded",
   "error_type": null,
   "rules": 29,
   "rule_ids": [
    "es:age_pattern",
    "es:calle_cue",
    "es:centro_salud_cue",
    "es:cp_cue",
    "es:date_dmy_dash",
    "es:date_dmy_dot",
    "es:date_dmy_slash",
    "es:date_month_abbrev",
    "es:date_spanish_long",
    "es:date_spanish_short",
    "es:dni_checksum",
    "es:domicilio_cue",
    "es:edad_cue",
    "es:email_pattern",
    "es:firmado_cue",
    "es:hijo_de_cue",
    "es:hospital_prefix",
    "es:nacido_en_cue",
    "es:nhc_cue",
    "es:nie_checksum",
    "es:nss_pattern",
    "es:paciente_cue",
    "es:phone_cue",
    "es:phone_spanish",
    "es:postal_code_pattern",
    "es:profesion_cue",
    "es:servicio_cue",
    "es:title_don_prefix",
    "es:title_dr_prefix"
   ],
   "layers": {
    "context_cue": 16,
    "regex_checksum": 13
   },
   "phi_types": {
    "AGE": 2,
    "CONTACT": 3,
    "DATE": 6,
    "ID": 4,
    "LOCATION_AREA": 3,
    "LOCATION_STREET": 2,
    "NAME": 5,
    "ORGANISATION": 3,
    "PROFESSION": 1
   }
  },
  {
   "draw": 4,
   "response_chars": 7440,
   "response_sha256": "sha256:eae9a2cffbdbee069cbebaf338cc9acc6eb0a27e4e4d3a8e568dc3a45a7d5753",
   "completion_tokens": 2942,
   "prompt_tokens": 14071,
   "wall_seconds": 39.131,
   "stop_reason": "end_turn",
   "outcome": "loaded",
   "error_type": null,
   "rules": 31,
   "rule_ids": [
    "es:age_months",
    "es:age_years",
    "es:calle_cue",
    "es:colegiado_cue",
    "es:cp_cue",
    "es:date_abbrev_month",
    "es:date_dmy_dash",
    "es:date_dmy_dot",
    "es:date_dmy_slash",
    "es:date_text_month",
    "es:dni_checksum",
    "es:domicilio_cue",
    "es:email_pattern",
    "es:familiar_cue",
    "es:fax_cue",
    "es:firmado_cue",
    "es:hospital_gazetteer",
    "es:nacido_cue",
    "es:nass_cue",
    "es:nhc_cue",
    "es:nie_checksum",
    "es:nss_pattern",
    "es:numero_street",
    "es:paciente_cue",
    "es:phone_cue",
    "es:phone_pattern",
    "es:postal_code",
    "es:profesion_cue",
    "es:servicio_cue",
    "es:title_don_prefix",
    "es:title_dr_prefix"
   ],
   "layers": {
    "context_cue": 16,
    "gazetteer": 1,
    "regex_checksum": 14
   },
   "phi_types": {
    "AGE": 2,
    "CONTACT": 4,
    "DATE": 6,
    "ID": 6,
    "LOCATION_AREA": 2,
    "LOCATION_STREET": 3,
    "NAME": 5,
    "ORGANISATION": 2,
    "PROFESSION": 1
   }
  },
  {
   "draw": 5,
   "response_chars": 6349,
   "response_sha256": "sha256:43debdedfb66443cc40fa604264dbeb5b144512845e8f5cdf918b53743b8574a",
   "completion_tokens": 2486,
   "prompt_tokens": 14071,
   "wall_seconds": 30.375,
   "stop_reason": "end_turn",
   "outcome": "loaded",
   "error_type": null,
   "rules": 28,
   "rule_ids": [
    "es:calle_cue",
    "es:codigo_postal",
    "es:date_dmy_dash",
    "es:date_dmy_dot",
    "es:date_dmy_slash",
    "es:date_spanish_month",
    "es:dni_checksum",
    "es:domicilio_cue",
    "es:edad_cue",
    "es:edad_pattern",
    "es:email_pattern",
    "es:fecha_alta_cue",
    "es:fecha_ingreso_cue",
    "es:fecha_nacimiento_cue",
    "es:hospital_gazetteer",
    "es:nass_cue",
    "es:nhc_cue",
    "es:nie_checksum",
    "es:nombre_cue",
    "es:nss_pattern",
    "es:paciente_cue",
    "es:profesion_cue",
    "es:servicio_cue",
    "es:telefono_cue",
    "es:telefono_pattern",
    "es:titulo_don",
    "es:titulo_dr",
    "es:titulo_dra"
   ],
   "layers": {
    "context_cue": 16,
    "gazetteer": 1,
    "regex_checksum": 11
   },
   "phi_types": {
    "AGE": 2,
    "CONTACT": 3,
    "DATE": 7,
    "ID": 5,
    "LOCATION_AREA": 1,
    "LOCATION_STREET": 2,
    "NAME": 5,
    "ORGANISATION": 2,
    "PROFESSION": 1
   }
  }
 ],
 "spread": {
  "draws": 5,
  "loaded": 5,
  "format_failures": 0,
  "rule_count_min": 28,
  "rule_count_max": 31,
  "distinct_rule_ids": 85,
  "in_every_draw": 5,
  "in_one_draw_only": 57,
  "jaccard_min": 0.098,
  "jaccard_max": 0.4286,
  "jaccard_mean": 0.2417,
  "layer_ranges": {
   "context_cue": [
    14,
    17
   ],
   "gazetteer": [
    0,
    1
   ],
   "regex_checksum": [
    11,
    14
   ]
  },
  "pairs": [
   {
    "a": 1,
    "b": 2,
    "jaccard": 0.1429,
    "shared": 7,
    "only_a": 21,
    "only_b": 21
   },
   {
    "a": 1,
    "b": 3,
    "jaccard": 0.2667,
    "shared": 12,
    "only_a": 16,
    "only_b": 17
   },
   {
    "a": 1,
    "b": 4,
    "jaccard": 0.2553,
    "shared": 12,
    "only_a": 16,
    "only_b": 19
   },
   {
    "a": 1,
    "b": 5,
    "jaccard": 0.1915,
    "shared": 9,
    "only_a": 19,
    "only_b": 19
   },
   {
    "a": 2,
    "b": 3,
    "jaccard": 0.1875,
    "shared": 9,
    "only_a": 19,
    "only_b": 20
   },
   {
    "a": 2,
    "b": 4,
    "jaccard": 0.18,
    "shared": 9,
    "only_a": 19,
    "only_b": 22
   },
   {
    "a": 2,
    "b": 5,
    "jaccard": 0.098,
    "shared": 5,
    "only_a": 23,
    "only_b": 23
   },
   {
    "a": 3,
    "b": 4,
    "jaccard": 0.4286,
    "shared": 18,
    "only_a": 11,
    "only_b": 13
   },
   {
    "a": 3,
    "b": 5,
    "jaccard": 0.3256,
    "shared": 14,
    "only_a": 15,
    "only_b": 14
   },
   {
    "a": 4,
    "b": 5,
    "jaccard": 0.3409,
    "shared": 15,
    "only_a": 16,
    "only_b": 13
   }
  ]
 }
}
```

</details>

### 위 측정을 읽는 방법 — `rule_id` Jaccard 는 행동 지표가 아니다 (2026-08-21)

프로브가 낸 표에는 없고 기록된 draw 에서 직접 계산한 것이다. 붙여 두는 이유는
0.2417 이라는 평균 Jaccard 를 "모델이 자기 자신과 거의 일치하지 않는다" 로 읽는 것이
틀렸기 때문이고, 숫자를 처음 만나는 곳이 이 파일이기 때문이다.

**모델은 같은 규칙을 다른 이름으로 쓴다.** draw 1 의 `age_label_cue`, draw 2 의
`age_label`, draw 3·5 의 `edad_cue` 는 같은 것을 겨냥한다. `phone_pattern` ·
`phone_es` · `phone_spanish` 도, `postal_code` · `postal_code_pattern` ·
`codigo_postal` 도 그렇다. `rule_id` 집합의 Jaccard 는 이 개명을 불일치로 세므로
**기능적 불일치를 과대평가한다.** 5개 draw 전부에 등장한 5개(`date_dmy_dash` ·
`date_dmy_dot` · `date_dmy_slash` · `domicilio_cue` · `email_pattern`)는 이름까지
안정적이었던 것들이고, 규칙이 안정적이었던 것들의 집합이 아니다.

거친 상한을 하나 대 보면: `rule_id` 에서 `pattern`·`cue`·`prefix`·`label`·`es`·
`spanish`·`checksum`·`gaz`·`gazetteer` 같은 규약 토큰을 떼고 남은 토큰 집합으로
비교하면 쌍별 Jaccard 가 0.3077–0.6000 (평균 **0.4611**) 로, 원래 값의 거의 두 배다.
이 정규화는 즉석에서 만든 stop 목록이고 측정치가 아니다 — 원래 Jaccard 가 행동에
대한 값이 아니라는 것을 보이는 용도이고, 그 자체를 인용하지 않는다.

**이름을 쓰지 않는 값은 훨씬 안정적이다.** 층 분포는 위 표에 있고, 유형 분포는
draw 별로 이렇다:

| phi_type | min | max | draw 1–5 |
|---|---|---|---|
| `AGE` | 2 | 2 | 2 2 2 2 2 |
| `PROFESSION` | 1 | 1 | 1 1 1 1 1 |
| `LOCATION_STREET` | 2 | 3 | 2 2 2 3 2 |
| `NAME` | 4 | 5 | 5 4 5 5 5 |
| `DATE` | 6 | 7 | 7 6 6 6 7 |
| `ID` | 4 | 6 | 5 5 4 6 5 |
| `CONTACT` | 2 | 4 | 3 2 3 4 3 |
| `ORGANISATION` | 2 | 4 | 2 4 3 2 2 |
| `LOCATION_AREA` | 1 | 3 | 1 2 3 2 1 |

`OTHER` 는 어느 draw 에도 없다 — 회차 1 의 규칙 파일과 같고, 그 유형의 누출률
1.000 이 규칙 부재에서 온다는 것을 다섯 번 더 확인한 것이다.

**그래서 계측기의 성질은 이렇게 요약된다: 유형 수준에서 안정적인 포트폴리오를
식별자 수준에서 불안정한 이름으로 낸다.** 어느 쪽도 누출률이 아니다. 규칙 수
28–31 과 `LOCATION_AREA` 1–3 이 누출률을 얼마나 움직이는지는 이 측정이 답하지 않고,
DESIGN §3 이 답하지 않기로 한 것이다.

이 표를 손으로 계산한 것은 이번 한 번이다. `spread()` 가 `phi_type_ranges` 를 함께 내고
`render()` 가 두 표를 나란히 놓도록 프로브를 고쳤으므로, 다음 실행부터는 위의 표에 있고
Jaccard 옆에 이 경고문이 붙는다. 이번 블록에 없는 이유는 이 순서로 알게 되었기
때문이다 — 숫자를 보고 나서 무엇을 재야 하는지 알았고, 5회를 다시 호출해서 표의
모양을 맞추는 것은 측정을 표에 맞추는 것이다.

### 이 프로브가 재지 않은 것 — 닻 없는 변동이다 (2026-08-21)

**5회 모두 회차 1 의 프롬프트다.** §1.2 는 제시되었고 내용이 비어 있다고 선언된 상태다
(`sections_filled` 에 `1.2` 가 있고, `rules_empty: true` · `rules_chars: 0` ·
`rules_sha256: null` — 블록이 생략된 것이 아니라 "EMPTY. There is no current rule file"
이라고 적힌 블록이 갔다). 즉 모델은 참조할 자기 이름을 하나도 받지 않은 상태에서
28–31개의 이름을 **새로 지었다.**

**회차 2 이후는 다른 행위다.** §1.2 가 직전 회차의 규칙 파일 전문을 담고, 프롬프트는
패치가 아니라 완전한 파일을 다시 내라고 요구한다. 그러면 이름을 새로 짓는 것이 아니라
보고 있는 이름을 **이어받는** 것이 된다. 두 행위의 변동 폭이 같다고 볼 근거는 없고,
후자가 더 작을 것으로 기대할 근거는 있다 — 직전 파일이 프롬프트 안에 그대로 있다.

**그래서 이 측정치는 회차 간 비교의 상한으로만 쓴다.** 쌍별 Jaccard 0.2417,
85개 중 한 draw 에만 등장 57개, 규칙 수 28–31 — 이 값들을 회차 N 대 회차 N+1 의
`by_rule` 겹침에 인용할 때는 "닻 없이 재면 이만큼 벌어진다" 는 상한이고, 회차 간에
기대되는 폭이 아니다. 하한은 없다. 이 프로브는 닻이 있는 상태를 재지 않았다.

**구분이 없으면 어떻게 잘못 쓰이는가.** 회차 3 의 규칙이 회차 2 와 절반만 겹치는 것을
보고 "프로브가 잰 변동 안쪽이니 정상" 이라고 결론내는 것이 그것이다. 그것은 **거꾸로
읽은 것이다.** 닻이 없을 때의 상한에 닻이 있는 상태가 도달했다는 관찰이고, 정상이
아니라 설명이 필요한 사건이다 — Auditor 되먹임이 이름을 갈아치우게 만들었거나,
§1.2 가 실제로 채워지지 않았거나, 모델이 이어쓰기를 하지 않고 있다. 세 가지 모두
확인 가능한 것이고, 이 숫자로 덮을 것이 아니다.

**닻이 있는 상태의 변동은 미측정이다.** 재려면 같은 §1.2 를 담은 프롬프트를 n회
보내야 하고, 그 프롬프트는 회차 2 가 실행된 뒤에야 존재한다. 회차 2 의 `rule_id` 가
회차 1 과 얼마나 겹치는지가 이 상한 해석의 첫 데이터이고, n = 1 이다 — 상한을 반증할
수는 있어도(겹침이 낮게 나오면 닻이 이름을 붙들지 못한다는 뜻) 확증하지는 못한다.
