---
title: PiAgent 현재 수준과 대화형 에이전트 사용법
theme: report
intent: reference
toc: true
---

# PiAgent 현재 수준과 대화형 에이전트 사용법

작성일: 2026-08-15  
실행 모델: Local Bedrock GPT-OSS-120B

## 1. 현재 에이전트 수준

PiAgent는 단순 챗봇을 넘어 파일, 코드, 도구, 세션, 메모리, 스킬을 조합하는 **L4 실험형 에이전트 런타임**이다. 다만 장시간 작업의 완료 신뢰도는 아직 **L3 수준**이다.

| 단계 | 의미 | 현재 상태 |
| --- | --- | --- |
| L1 | 단일 질문과 텍스트 답변 | 안정적 |
| L2 | 로컬 파일 읽기와 도구 호출 | 구현·검증됨 |
| L3 | 코드 수정, 명령 실행, 테스트, 세션 지속 | 작은 범위에서 실사용 가능 |
| L4 | 스킬, 회사 규칙, 메모리, 산출물, 읽기 전용 서브에이전트 | 구현됨, 모델 준수 편차 존재 |
| L5 | 긴 작업을 정확한 경로·근거·검증까지 자율 완료 | 미완료 |
| L6 | 프로덕션 멀티유저 서비스와 강한 보안·관측성 | 비목표 |

구조적으로는 LangChain `create_agent` 위에 PiAgent가 세션·파일·메모리·스킬·todo·work note·서브에이전트를 직접 구현했다. 별도의 `deep-agents` 패키지를 사용하지는 않지만 제공 기능은 Deep Agent 계층과 유사하다.

실용적인 판정:

- 일반 질문과 제약 문제: 안정적
- 같은 세션의 후속 질문: 안정적
- 회사 가이드 기반 문답과 코드 리뷰: 사용 가능
- 작은 코드 수정: 관련 테스트가 있을 때 사용 가능
- 긴 조사·문서·다중 파일 구현: 중간 결과와 최종 증거를 사람이 확인해야 함
- 자율 완료를 그대로 신뢰하는 무인 실행: 아직 부적합

## 2. 대화형 진입점

`chat.py`는 PiAgent의 정식 터미널 대화 진입점이다. 런타임을 다시 구현하지 않고 `OpenClawPiLangChain`과 `PiAgentSession`을 사용한다.

주요 기능:

- OpenAI 또는 Local Bedrock 모델 자동 연결
- 프로세스를 다시 실행해도 같은 session 히스토리 복원
- 자동 또는 고정 스킬 선택
- Plan 모드 전환
- 사용자별 artifact/session/audit/memory 격리
- 활성 도구 목록과 실행 과정 표시
- API 키·token·password가 포함된 도구 인자 표시 시 값 제거
- UTF-8 prompt 파일 단발 실행
- 모델 호출 없는 진단

## 3. 빠른 시작

현재 폴더를 워크스페이스로 명시해 먼저 진단한다.

```powershell
python chat.py --check --workspace . --session chat-main --no-mcp
```

대화형 실행:

```powershell
python chat.py --workspace . --session chat-main --no-mcp
```

Local Bedrock을 사용하면 진단 결과의 `model_route`가 `local-bedrock`이어야 한다.

> `.env`의 `PI_WORKSPACE`가 Docker용 경로라면 Windows 호스트에서 다른 위치로 해석될 수 있다. 로컬 실행에서는 `--workspace .`를 명시하거나 `PI_WORKSPACE=.`로 설정한다.

## 4. 주요 CLI 옵션

| 옵션 | 기능 |
| --- | --- |
| `--workspace <path>` | 파일과 스킬을 읽을 작업 루트 |
| `--session <id>` | 영속 대화 세션 ID |
| `--user-id <id>` | 사용자별 상태와 산출물 격리 |
| `--skill <name>` | 특정 스킬 고정 |
| `--skill-mode auto|manual|off` | 스킬 선택 방식 |
| `--plan-mode on|off` | 읽기 전용 계획 모드 |
| `--allow-tool <name>` | 허용 도구 지정, 반복 가능 |
| `--deny-tool <name>` | 차단 도구 지정, 반복 가능 |
| `--workspace-extensions` | 신뢰하는 `.piagent` 확장 로딩 |
| `--no-mcp` | MCP 연결 비활성화 |
| `--no-memory` | 장기 메모리 비활성화 |
| `--no-write`, `--no-shell` | 파일 변경 또는 셸 도구 비활성화 |
| `--max-model-calls <n>` | 한 요청의 모델 호출 상한 |
| `--check` | 모델 호출 없이 도구·스킬·설정 진단 |
| `--once <prompt>` | 한 질문만 실행 후 종료 |
| `--prompt-file <path>` | 워크스페이스 안 UTF-8 질문 파일 실행 후 종료 |

Windows에서 긴 한국어 질문은 `--prompt-file`을 권장한다.

```powershell
python chat.py `
  --workspace . `
  --session coding-review `
  --prompt-file tests/fixtures/prompts/naru_python_policy_question.ko.txt `
  --skill naru-python-coding-guide `
  --skill-mode manual `
  --no-mcp
```

## 5. 대화 중 명령

| 명령 | 동작 |
| --- | --- |
| `/help` | 명령 도움말 |
| `/status` | model route, workspace, session, memory, skill 상태 |
| `/skills` | 발견된 스킬 목록 |
| `/tools` | 활성 도구 목록 |
| `/skill <name>` | 스킬 고정 |
| `/skill auto`, `/skill off` | 자동 선택 또는 중지 |
| `/plan on`, `/plan off` | Plan 모드 전환 |
| `/session <name>` | 다른 영속 세션으로 전환 |
| `/last` | 현재 세션의 직전 최종 답변 표시 |
| `exit`, `quit`, `:q` | 종료 |

## 6. 권장 실행 예시

### 코드 분석 전용

```powershell
python chat.py `
  --workspace . `
  --session code-analysis `
  --plan-mode on `
  --permission-mode plan `
  --no-mcp
```

### 회사 코딩 가이드 리뷰

```powershell
python chat.py `
  --workspace . `
  --session company-code-review `
  --skill naru-python-coding-guide `
  --skill-mode manual `
  --no-mcp
```

### 사용자별 격리

```powershell
python chat.py `
  --workspace . `
  --session main `
  --user-id alice `
  --no-mcp
```

### 자동화에서 한 번만 실행

```powershell
python chat.py `
  --workspace . `
  --session nightly-check `
  --prompt-file prompts/nightly-check.ko.txt `
  --max-model-calls 12 `
  --no-mcp
```

종료 코드는 정상 `0`, 실행 실패 `1`, 구성·입력 실패 `2`, 사용자 추가 입력 대기 `3`이다.

## 7. 실제 검증 결과

### 모델 없는 진단

- Local Bedrock 경로 인식
- 기본 도구 31개 로드
- 번들 스킬 5개 로드
- `.env` 키 값 출력 없음

### 프로세스 간 세션 기억

첫 실행:

```text
프로젝트 코드명은 AURORA-17이라고 기억해.
```

두 번째 프로세스를 같은 session ID로 실행한 결과:

```text
AURORA-17
```

장기 메모리와 파일·셸 도구를 끈 상태였으므로 `FlatSessionStore`의 대화 히스토리 복원 결과다.

### 스킬과 도구 사용

`naru-python-coding-guide`를 고정한 정책 질문에서 첫 도구로 다음 reference를 읽었다.

```text
skills/naru-python-coding-guide/references/python-coding-guide.md
```

이후 `[PY-04]`, `[PY-07]`, `[PY-09]`를 정확히 인용했고 도구 오류는 없었다.

## 8. 남은 한계

- 모델이 새 출력 경로 대신 과거의 유사 artifact를 선택할 수 있다.
- 파일 작성 후 테스트·최종 보고 전에 멈추는 경우가 있다.
- 스킬의 자연어 완료 게이트는 런타임 강제 검증이 아니다.
- 검색과 광범위한 파일 탐색은 의존성 디렉터리에서 불필요한 오류·비용을 만들 수 있다.
- 위험한 실제 운영 저장소에서는 `--plan-mode on`, `--no-write`, `--no-shell`, 사용자 격리를 먼저 적용해야 한다.

즉 `chat.py`는 대화형 코딩 보조 에이전트로 사용할 수 있지만, 중요한 변경은 최종 diff, 실제 변경 경로, 테스트 명령과 출력을 사람이 확인해야 한다.
