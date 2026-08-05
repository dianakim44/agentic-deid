# agentic-deid

Multi-agent porting of a clinical de-identification pipeline to new languages
and note types, with annotation-free supervision.

평가 코퍼스 중 하나로 한국어 surrogate 코퍼스 `ko-surro` 를 사용한다.
데이터 출처로 인용은 하되, 코드 계보상 별개 프로젝트다.
코퍼스 명칭은 config/naming.yaml 의 ID 를 쓴다. 다른 이름을 만들지 않는다.

## 먼저 읽을 것

설계 결정과 그 근거는 docs/DESIGN.md 에 있다. 파이프라인·에이전트·실험 축을
바꾸려면 DESIGN.md 를 먼저 고치고, 코드에서 우회하지 않는다.

---

## 데이터 취급 (최우선)

- `data/` 이하는 MIMIC 파생 DUA 대상이다. **커밋 금지.**
- **`git add -A` 와 `git add .` 를 사용하지 않는다.** 항상 명시적 경로만 스테이징한다.
- 커밋 전 `python tools/release_screen.py` 를 실행한다.
  BLOCKED가 1건이라도 나오면 커밋하지 말고 사용자에게 보고한다.
- 예측 결과를 공개할 때는 원문 텍스트 없이 오프셋·유형·판정만 남긴다.

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
- 분할 단위는 **환자(patient) disjoint**. 레코드 단위 랜덤 분할 금지.
- dev 체크포인트 선택에 test를 쓰지 않는다.
- 규칙은 코드가 아니라 `rules/*.yaml` 로 관리하고, 규칙 버전을 결과에 기록한다.
- 모든 탐지 스팬에 provenance(탐지기 · 규칙 ID · 점수)를 기록한다.
- 병합 정책은 교체 가능한 전략으로 구현한다
  (fixed-priority / union / agent-arbiter가 같은 탐지 결과 위에서 갈리도록).
- 탐지(detection)와 가명화(pseudonymization)를 분리한다. 평가는 탐지만 사용한다.
- 실험 식별자는 config/naming.yaml 에 정의된 값만 쓴다. 새 코퍼스·arm·축이
  필요하면 코드에 문자열을 하드코딩하지 말고 naming.yaml 에 먼저 추가한다.

## 코드 규약

- Python 3.11+. 의존성은 `pyproject.toml`.
- 난수 시드는 config에 고정하고 결과 파일에 함께 기록한다.
- 평가 지표: precision / recall / F1 에 더해 **누출률(leak rate)** 과
  **상보성 분해**(rule만 / AI만 / 둘 다 / 둘 다 놓침)를 항상 함께 낸다.
