---
title: PiAgent 회사 코딩 가이드 실실행 평가
theme: report
intent: reference
toc: true
---

# PiAgent 회사 코딩 가이드 실실행 평가

작성일: 2026-08-15  
모델: Local Bedrock GPT-OSS-120B  
대상 스킬: `skills/naru-python-coding-guide`  
정책: 평가용 가상 NaruWorks Python 코딩 가이드

## 1. 결론

PiAgent는 회사 코딩 가이드를 `references/python-coding-guide.md`에서 읽은 뒤 **정책 문답과 코드 리뷰에는 안정적으로 적용**했다. 코드 생성물에도 dataclass, UTC clock 주입, 도메인 오류 코드, 테스트 이름 같은 고유 규칙이 반영됐다.

하지만 한 번의 구현 요청을 끝까지 처리하는 능력은 아직 완전하지 않다. 첫 구현은 파일 작성 전에 중단됐고, 두 번째는 올바른 코드와 테스트를 만들었지만 테스트 실행과 최종 답변을 누락했다. 완료 게이트를 강화한 세 번째 실행은 테스트를 실행했으나, 새 출력 경로 대신 이전 실행의 산출물을 재사용했다.

따라서 현재 판정은 다음과 같다.

- 가이드 자동 선택: 통과
- 정책 기반 문답: 통과
- 기존 코드 리뷰: 통과, 경미한 분류 누락
- 가이드 기반 코드 생성: 내용은 대체로 준수
- 요청 경로 준수와 한 번에 구현·검증 완료: 미통과

## 2. 평가용 스킬 구조

```text
skills/naru-python-coding-guide/
├─ SKILL.md
├─ agents/openai.yaml
└─ references/
   └─ python-coding-guide.md
```

`SKILL.md`에는 처리 절차와 완료 게이트만 두고, 세부 규칙 11개는 reference에 분리했다. 주요 평가 규칙은 다음과 같다.

- `[PY-02]`: 서비스 결과는 raw dict가 아니라 frozen dataclass
- `[PY-03]`: 안정적인 `NRU_<AREA>_<REASON>` 오류 코드
- `[PY-04]`: UTC-aware clock 주입
- `[PY-05]`: 구조화된 event 로그와 비밀값 비노출
- `[PY-09]`: 회사 형식의 pytest 이름과 경계·실패 테스트

## 3. 실제 실행 결과

| ID | 질문 | 자동 선택 | 실제 도구 흐름 | 결과 |
| --- | --- | --- | --- | --- |
| E-01 | 시간·예외·미실행 테스트 표기 문답 | 점수 4 | `read(policy)` | 3/3 정답, 정책 ID 정확 |
| E-02 | 위반 Python fixture 리뷰 | 점수 11 | `read(policy) → read(code) → write(report) → ls` 외 탐색 | 의도한 위반 7개 탐지 |
| E-03a | 새 deadline 구현 1차 | 점수 11 | `read(policy) → ls(missing)` | 파일 생성 없이 종료 |
| E-03b | 완료 지침 보강 후 구현 2차 | 점수 11 | 정책·전역 탐색 → `write` 2회 | 코드·테스트 생성, 모델 자체 테스트 누락 |
| E-03c | 완료 게이트 보강 후 새 경로 구현 | 점수 11 | 정책·전역 탐색 → 이전 파일 읽기 → `exec` | 이전 산출물의 `4 passed`, 요청한 새 경로 미생성 |

### E-01. 정책 문답

모델은 첫 도구로 정확한 reference를 읽고 다음과 같이 답했다.

- 현재 시간은 UTC-aware `clock: Callable[[], datetime]`로 주입: `[PY-04]`
- 예상 인프라 예외만 잡고 `raise ... from exc`로 원인 보존: `[PY-07]`
- 실행하지 않은 pytest 명령은 정확히 `not run`: `[PY-09]`

불필요한 일반론이나 존재하지 않는 규칙을 추가하지 않았다.

### E-02. 기존 코드 리뷰

대상 `tests/fixtures/naru_python_legacy.py`에서 다음 위반을 모두 찾았다.

| 근거 | 판정 |
| --- | --- |
| `items: list[str] = []` | `[PY-06]` mutable default |
| `dict | None` | `[PY-02]`, `[PY-03]` 결과·실패 sentinel |
| `except Exception` | `[PY-07]` 광범위 예외 |
| `datetime.now()` | `[PY-04]` 직접·naive 시간 |
| API key 문자열 보간 로그 | `[PY-05]` 비밀값 로깅 |
| async 함수의 `time.sleep(1)` | `[PY-08]` blocking sleep |

원본 코드를 수정하지 않았고 보고서를 생성했다. 다만 `extra={"event": ...}`가 없는 사실을 별도 위반이 아니라 unknown 섹션에 적었다. 소스에서 확인 가능한 규칙이므로 엄격한 평가는 경미한 분류 오류다.

### E-03. 코드 구현

두 번째 실행에서 생성된 `deadline.py`는 다음을 지켰다.

- 모든 production public API 타입 표기
- frozen `Deadline` dataclass 반환
- `NRU_DEADLINE_NEGATIVE`, `NRU_TIME_NAIVE`, `NRU_TIME_NOT_UTC` 오류 코드
- 주입된 clock의 timezone과 UTC 검증
- mutable default 미사용
- 요청 기능 외 의존성 추가 없음

생성된 테스트는 회사 이름 형식을 사용했고 성공, 0초, 음수, naive clock을 검사했다. 독립 실행 결과는 `4 passed in 0.03s`였다.

남은 준수 문제:

- `[PY-09]`가 정의된 각 실패를 테스트하도록 요구하지만 non-UTC clock 테스트가 없다.
- 정책의 `[PY-03]`은 subclass를 요구하지만 예시와 생성 코드는 `NaruDomainError` 자체를 발생시킨다. 정책 문구와 예시가 충돌하므로 실제 회사 가이드에서는 하나로 통일해야 한다.
- 새 v3 경로를 요청했지만 모델은 이전 실행의 경로를 찾아 테스트했다. 테스트 성공은 요청한 v3 산출물의 증거가 아니다.

## 4. 자동 선택 문제와 수정

최초 E-01은 스킬이 발견됐지만 `auto_no_match`로 선택되지 않았다. 기존 점수 계산이 영문에 맞춰 최소 3~4글자 토큰만 인정해 `회사`, `코딩`, `가이드` 같은 한국어 단어를 사실상 제외한 것이 원인이었다.

`piagent/agent_registry.py`의 토큰화를 Unicode 단어 단위로 바꾸고 다음 기준을 적용했다.

- ASCII 토큰: 기존 최소 길이 유지
- 비ASCII 토큰: 2글자 이상 정확 일치

영문 힌트가 없는 `회사 코딩 가이드에 따라 이 코드를 리뷰해줘` 회귀 테스트를 추가했다. 수정 후 실제 문답은 선택 점수 4, 리뷰·구현은 각각 11로 스킬이 선택됐다.

## 5. 기능 요구사항과 우선순위

| 우선순위 | ID | 요구사항 | Acceptance Criteria |
| --- | --- | --- | --- |
| P0 | CG-01 | 요청 산출물 경로 완료 게이트 | 최종 답변 전 실제 `write` 경로가 요청 경로와 일치하고 `ls/read`로 존재가 확인된다. |
| P0 | CG-02 | 구현 검증 완료 게이트 | 코드 생성 요청은 관련 테스트 성공 또는 명시적 실패 보고 없이는 완료 처리되지 않는다. |
| P0 | CG-03 | 증거와 최종 답변 일치 | 최종 `changed files`, 테스트 cwd, 테스트 대상이 감사 로그의 도구 증거와 정확히 일치한다. |
| P1 | CG-04 | 정책 준수 검사기 | AST 기반으로 타입, dataclass, clock, 예외 코드, 테스트 이름·실패 분기 커버리지를 검사한다. |
| P1 | CG-05 | 불필요한 전역 탐색 억제 | 스킬에 정확한 reference와 출력 경로가 있으면 루트 `find/grep/ls`를 호출하지 않는다. |
| P1 | CG-06 | 잘못된 도구명 교정 | `search`처럼 미등록 도구가 나오면 `tool_search` 또는 안내된 도구로 한 번 교정한다. |
| P2 | CG-07 | 정책 자체 정합성 검사 | 규칙과 승인 예시가 충돌하면 스킬 배포 테스트가 실패한다. |

## 6. 재현 명령

정책 문답:

```powershell
python simple_piagent.py `
  --prompt-file tests/fixtures/prompts/naru_python_policy_question.ko.txt `
  --session naru-python-policy-live-v2-20260815 `
  --max-model-calls 10
```

코드 리뷰:

```powershell
python simple_piagent.py `
  --prompt-file tests/fixtures/prompts/naru_python_review.ko.txt `
  --session naru-python-review-live-20260815 `
  --max-model-calls 14
```

구조·자동 선택 회귀:

```powershell
python -m pytest tests/test_company_skills.py -q
```

## 7. 최종 판정

코딩 가이드가 주어졌을 때 GPT-OSS-120B는 reference를 읽고 그 규칙을 답변·리뷰·생성 코드에 실제로 반영한다. 특히 고유 정책 ID와 비표준 규칙까지 적용했기 때문에 단순한 일반 코딩 상식 응답은 아니다.

다만 현재 PiAgent는 스킬의 자연어 완료 지침만으로 “정확한 경로에 작성 → 관련 테스트 실행 → 그 증거만 보고”를 강제하지 못한다. 코드 가이드 기반 **조언과 리뷰는 실사용 가능**, **자동 구현은 diff·경로·테스트 증거를 사람이 확인하는 조건으로 사용 가능**한 수준이다.
