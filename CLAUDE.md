# agentic-deid

Multi-agent porting of a clinical de-identification pipeline to new languages
and note types, with annotation-free supervision.

평가 코퍼스 중 하나로 한국어 surrogate 코퍼스 `ko-surro` 를 사용한다.
데이터 출처로 인용은 하되, 코드 계보상 별개 프로젝트다.
코퍼스 명칭은 config/naming.yaml 의 ID 를 쓴다. 다른 이름을 만들지 않는다.
(naming.yaml 어휘 전반에 대한 규칙은 "실험 무결성" 절에 있다. 이 문장은
그중 코퍼스에 해당하는 부분이다.)

## 먼저 읽을 것

설계 결정과 그 근거는 docs/DESIGN.md 에 있다. 파이프라인·에이전트·실험 축을
바꾸려면 DESIGN.md 를 먼저 고치고, 코드에서 우회하지 않는다.

---

## 데이터 취급 (최우선)

- `data/` 이하는 MIMIC 파생 DUA 대상이다. **커밋 금지.**
- **`git add -A` 와 `git add .` 를 사용하지 않는다.** 항상 명시적 경로만 스테이징한다.
- 커밋 전 `python tools/release_screen.py` 를 실행한다.
  BLOCKED가 1건이라도 나오면 커밋하지 말고 사용자에게 보고한다.
  **SEALED 는 별개 줄이고 0 이 아닌 것이 정상이다** — 봉인된 test fold 이고
  git 이 볼 수 없으므로 커밋을 막지 않는다. 단 `sealed/` 가 스테이징·추적되면
  SEALED 가 아니라 BLOCKED 로 올라간다. 그게 실제 위반이다.
- SUSPECT 는 알려진 오탐이 `tools/screen_allowlist.json` 로 집계되고, 목록에 없는
  것만 개별 출력된다. **개별 출력된 SUSPECT 는 읽어라** — 매 실행 반복되던 5줄이
  사라진 이유가 그것이다. 새 오탐을 목록에 넣는 것은 커밋되는 변경이고,
  `data/`·`sealed/`·deny 대상 경로는 스크리너가 거부한다.
- 예측 결과를 공개할 때는 원문 텍스트 없이 오프셋·유형·판정만 남긴다.
- **예외 메시지·로그·경고에 코퍼스 텍스트를 넣지 않는다.** 스팬 인덱스·오프셋·
  길이만 쓴다. 예외 메시지는 터미널·CI 로그·이슈·스택트레이스로 흘러나가고,
  그 경로에는 `release_screen.py` 가 닿지 않는다. 합성 코퍼스에서만 안전한
  검사는 아무도 신뢰할 수 없으므로 코퍼스를 구분하지 않고 전부 적용한다
  (MEDDOCAN·GraSCCo 도 예외가 아니다 — 어느 코퍼스를 다루는지에 따라 규칙이
  갈리면 실수가 조용해진다).
- 테스트도 같은 규칙을 따른다. 나아가 **메시지에 표면형이 없는지 검사하는
  테스트를 둔다** — 규약을 코드로 고정하지 않으면 다음 로더에서 "디버깅에
  편하다"는 이유로 되돌아온다. `tests/test_meddocan_loader.py` 의
  `test_offset_mismatch_message_quotes_no_surface` 가 그 형태다.

## Git

- 저장소는 **public**. 푸시된 것은 되돌릴 수 없다고 가정한다.
- 커밋은 자율적으로 해도 된다. **push는 사용자 확인 후에만.**
- 커밋 메시지는 한 줄 요약 + 필요 시 본문. 실험 결과를 담은 커밋은
  어떤 split·규칙 버전으로 돌렸는지 본문에 적는다.

## 실험 무결성 — test fold 봉인 (위반 시 실험 전체가 무효)

- `splits/{corpus}.json` 확정 후 **test fold는 봉인**한다.
  규칙 개발·에이전트 이식은 dev fold만, NER 학습은 train fold만 사용한다.
- test fold 원문은 `sealed/` 에 둔다. **이 디렉토리를 열거나 읽지 않는다.**
  경로가 눈에 띄어도 열지 않고, 필요해 보이면 사용자에게 먼저 묻는다.
- test 평가는 `src/eval/run_sealed_eval.py` 로만 수행하고, 실행할 때마다
  `results/sealed_eval_log.md` 에 날짜·커밋 해시·목적을 append 한다
  (논문에 "the test set was evaluated N times" 로 보고하기 위함).
- 에이전트가 생성한 규칙·사전에도 같은 규율을 적용한다.
  dev 오류를 보고 고치는 것은 허용, test 오류를 보고 고치는 것은 금지.
- 분할 단위는 가용한 최대 자연 그룹이다. 환자 키가 있으면 환자 disjoint,
  없으면 DESIGN.md §9.5 의 식별 표면형 판정으로 그룹을 정한다.
  레코드 단위 랜덤 분할은 어느 경우에도 금지한다.
- dev 체크포인트 선택에 test를 쓰지 않는다.
- 규칙은 코드가 아니라 `rules/*.yaml` 로 관리하고, 규칙 버전을 결과에 기록한다.
- 모든 탐지 스팬에 provenance(layer · 탐지기 · 규칙 ID · 점수)를 기록한다.
  layer 값은 config/naming.yaml 의 layer 축에서 읽는다. 탐지기 이름에서
  유도하지 않는다 — 접두어·부분문자열·수동 대응표 모두 금지. 스팬을 낸
  탐지기가 직접 채운다. 자세한 근거는 DESIGN.md §3.
- 에이전트(Arb/Aud)는 스팬을 만들지 않으므로 layer 값을 갖지 않는다.
  개입은 스팬의 `agent_actions` 리스트에 별도로 기록한다.
- 병합 정책은 교체 가능한 전략으로 구현한다
  (fixed-priority / union / agent-arbiter가 같은 탐지 결과 위에서 갈리도록).
- 탐지(detection)와 가명화(pseudonymization)를 분리한다. 평가는 탐지만 사용한다.
- config/naming.yaml 에 정의된 어휘만 쓴다. 실험 식별자(코퍼스·arm·축)뿐
  아니라 스팬에 붙는 값(layer 등)도 포함한다 — 결과 경로에 들어가지 않는
  값도 예외가 아니다. 새 값이 필요하면 코드에 문자열을 하드코딩하지 말고
  naming.yaml 에 먼저 추가한다. 위 "코퍼스 명칭" 조항은 이 규칙의 한 사례다.

## 뮤테이션 게이트 — 전량 실행을 언제 요구하나

킬 카운트는 `tests/mutations/run.py` 의 `TEST_FILES` 목록 **전체**에 대한 값이다.
전량은 현재 185건이고 8샤드로 약 2시간이다 (`tests/mutations/parallel.py`,
2026-08-28 에 **181건으로** 2.05시간 실측 — 185건 자체는 아직 전량으로 돌리지 않았다),
직렬로는 약 14시간(도출값이고 실측이 아니다)이다.
매 커밋에 낼 수 있는 비용이 아니므로, 매번 판단하지 않도록 기준을 여기 고정한다.
근거와 비용 실측은 `tests/mutations/README.md` §"Running all of it".

- **영향 범위 실행으로 충분한 경우** — 이미 기록된 카운트의 *분모* 가 그대로일 때:
  - `src/` 의 한 함수·한 모듈 변경. 기존 테스트 파일 안에서의 단언 추가·수정.
  - 뮤테이션 추가, 앵커 재조정. 재조정된 뮤테이션은 범위에 들었는지와 무관하게
    재측정한다 — 앵커 수정은 뮤테이션 자체의 변경이다.
  - `TEST_FILES` 밖의 테스트 파일 변경(`tests/test_mutation_harness.py`,
    `tests/test_run_loop_cli.py`)은 어떤 카운트도 움직이지 않는다. 실행이 필요 없다.
  - 범위는 변경된 테스트의 **런타임 도달 범위**로 정한다. 파일명 겹침이 아니다.
    시작 전에 포함 목록을 보여준다. 돌리지 않은 것은 "다음 전량 실행으로 연기"라고
    적는다. 면제라고 적지 않는다. 그리고 **현재 스위트로 재측정되지 않은 값에
    "변화 없음" 을 기대로 적지 않는다** — 비교 가능한 값이 없으면 기대도 없다.
- **전량이 필요한 경우** — 기록된 카운트 전부가 비교 불가능해지는 변경:
  - `TEST_FILES` 멤버십 변경(추가·삭제). 카운트가 낡는 것이 아니라 *다른 분모*에
    대한 값이 된다. `test_the_full_run_covered_the_current_test_files` 가 이것을
    실패로 알린다.
  - `src/` 광범위 변경 — 두 개 이상의 모듈, 또는 여러 뮤테이션이 공통으로 겨냥하는 경로.
  - 하네스의 측정 장치 변경: `run_suite` · `make_tree` · `kills` · `outcomes` ·
    `Mutation.apply` · `tree_fingerprint`.
  - 마지막 전량 실행이 없거나 60일 이상 지났을 때.
  - 킬 카운트를 논문·릴리스에 인용하기 직전.
- **마지막 전량 실행 시점은 `docs/notes/mutation-full-runs.md`** 와 사이드카
  `mutation-full-runs.counts.json` 에만 적는다. 이 파일에 날짜를 쓰지 않는다 —
  두 곳에 적으면 한 곳이 먼저 낡고, 어느 쪽이 낡았는지는 보이지 않는다.
- 부분 실행을 전량으로 기록하지 않는다. `parallel.py` 가 다섯 불변식(분할 정확성 ·
  단일 트리 · 전 샤드 완주 · 판정 합계 · 실행 중 트리 불변)으로 거절하고 INCOMPLETE 를
  남긴다. INCOMPLETE 항목은 지우지 않는다. **전량 실행 중에는 저장소를 편집하지 않는다** —
  다섯째 불변식이 잡고, 잡히면 두 시간이 날아간다.

## 코드 규약

- Python 3.11+. 의존성은 `pyproject.toml`.
- 난수 시드는 config에 고정하고 결과 파일에 함께 기록한다.
- 평가 지표: precision / recall / F1 에 더해 **누출률(leak rate)** 과
  **상보성 분해**(rules only / tagger only / both / joint only / neither)를 항상
  함께 낸다. `joint_only` 는 `fully_covered` 에서만 생기고 relaxed 에서는 항상 0 이다.
  누출률과 상보성 분해가 headline 이고 F1 은 아니다. 병합이 union 이므로
  결합 구성은 recall 에서 구성요소를 by construction 으로 이긴다.
- 누출률의 headline 값은 `fully_covered` 이고, relaxed 값은 하한으로 병기한다.
  두 정의는 DESIGN.md §9.3 에만 두고 여기서 되풀이하지 않는다.
- **누출률·상보성 분해는 예측 합집합으로, P/R/F1 은 1:1 배정으로 낸다.**
  하나로 합치지 않는다 — 합치면 없는 누출이 생기거나 `both` 가 과소평가된다.
  근거는 DESIGN.md §9.3. headline 을 고르는 것은 보고 층이고 채점기가 아니다.
- 채점기는 바이트 동일 스팬만 합친다. 경계가 다른 예측을 합치는 것은 병합 정책의
  일이고 채점기가 하면 모든 병합 정책이 같은 점수를 받는다 (DESIGN §9.3, §4).
- **비용을 품질과 함께 보고한다.** arm 마다 LLM 호출 수 · 토큰 수 ·
  wall time 을 metrics.json 에 같이 기록한다. 2배 비용으로 얻은 향상과
  1.05배 비용으로 얻은 향상은 다른 결과다.
