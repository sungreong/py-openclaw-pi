# OpenClaw Pi (LangChain) - 한국어 가이드

OpenClaw에서 영감을 받은 최소 코딩 에이전트 런타임입니다.

## 빠른 시작

1. `.env.example` 파일을 `.env`로 복사합니다.
2. `OPENAI_API_KEY`를 설정합니다.
3. 필요하면 `PI_READ_STRATEGY` 값을 조정합니다. (`smart` 또는 `legacy`)
4. 실행:
   - CLI: `python openclaw_pi_langchain.py "요청 프롬프트"`
   - Chat: `python chat.py`

## 멀티 에이전트 세션 예시

`PI_SESSION` 값을 다르게 지정하면 에이전트별 컨텍스트/히스토리를 분리할 수 있습니다.

### 1) 코드 분석 에이전트 (읽기 전용)

```powershell
python openclaw_pi_langchain.py "코드 구조를 요약하고 리스크 3개를 찾아줘" `
  --session analyst `
  --deny-tool write `
  --deny-tool edit `
  --deny-tool exec
```

### 2) 실행 에이전트 (명령 실행 중심)

```powershell
python openclaw_pi_langchain.py "테스트 실행하고 실패 원인 요약해줘" `
  --session runner `
  --allow-tool ls `
  --allow-tool find `
  --allow-tool grep `
  --allow-tool read `
  --allow-tool exec
```

### 3) 수정 에이전트 (편집 + 검증)

```powershell
python openclaw_pi_langchain.py "실패 테스트를 최소 수정으로 고치고 검증해줘" `
  --session fixer
```

### 4) 역할별 대화형 실행

```powershell
$env:PI_SESSION="analyst"; python chat.py
$env:PI_SESSION="runner"; python chat.py
$env:PI_SESSION="fixer"; python chat.py
```

권장 순서:
1. `analyst`가 문제 범위를 정리
2. `runner`가 실패를 재현
3. `fixer`가 수정 후 재검증

## 메모리 모드

- `PI_MEMORY_MODE=openclaw` (기본값)
  - 툴 기반 마크다운 메모리 사용: `memory_search`, `memory_get`, `memory_store`
  - 저장 위치: `.openclaw/memory/MEMORY.md`, `.openclaw/memory/YYYY-MM-DD.md`
- `PI_MEMORY_MODE=legacy`
  - 기존 자동 메모리 추출/회수 호환 흐름 사용

## 차단 경로 정책 (claudeignore 대안)

이 프로젝트는 `AGENTS.md` + 런타임 차단 경로 정책을 사용합니다.

기본 차단 경로:
- `.env`
- `.git/**`
- `.openclaw/memory/**`
- `secrets/**`
- `private/**`
- `node_modules/**`

설정:
- 환경변수: `PI_BLOCKED_PATHS` (쉼표 구분)
- CLI: `--blocked-path` (여러 번 지정 가능)

일반 파일 툴(`read/write/edit/ls/find/grep/exec`)은 차단 경로에 접근하지 않습니다.
메모리 툴은 관리된 방식으로 계속 사용 가능합니다.

## 주요 환경변수

- `OPENAI_API_KEY` (필수)
- `PI_MODEL`
- `PI_WORKSPACE`
- `PI_SESSION`
- `PI_MAX_MODEL_CALLS`
- `PI_TOOL_REPEAT_LIMIT` (기본 `3`; 동일 툴 호출이 한 실행에서 이 횟수 이상 반복되면 중단)
- `PI_EXEC_TIMEOUT`
- `PI_NO_MEMORY`
- `PI_MEMORY_MODE`
- `PI_MEMORY_DIR`
- `PI_BLOCKED_PATHS`
- `PI_READ_STRATEGY` (`smart` 기본, 또는 `legacy`)
- `PI_EXEC_PATH_CORRECTION` (기본 `false`, 제한적 경로 교정)
- `PI_PLAN_MODE` (`on|off`, 기본 `off`)

## Todo 툴 (세션 작업 추적)

Pi는 세션 내 할 일 목록을 관리하는 빌트인 `todo_read` / `todo_write` 툴을 제공합니다.

- `todo_write`: JSON 배열로 세션 할 일 목록을 교체합니다.
- `todo_read`: 상태 아이콘과 우선순위가 포함된 현재 목록을 반환합니다.

### 채팅에서 사용하기

자연어로 요청하면 됩니다:

```
todo 목록 보여줘
할 일 목록 만들어줘: 1) 버그 수정 (high), 2) 테스트 작성 (medium)
첫 번째 항목 완료 처리해줘
```

멀티스텝 작업을 요청하면, 에이전트가 자동으로 todo 목록을 생성하고 업데이트합니다.

### todo_write 입력 형식

JSON 배열로 입력합니다:

```json
[
  {"content": "로그인 버그 수정", "status": "pending", "priority": "high"},
  {"content": "유닛 테스트 작성", "status": "in_progress", "priority": "medium"},
  {"content": "문서 업데이트", "priority": "low"}
]
```

필드:
- `content` (필수): 작업 내용
- `status`: `pending` | `in_progress` | `completed` | `cancelled` (기본: `pending`)
- `priority`: `high` | `medium` | `low` (기본: `medium`)

ID는 자동 할당됩니다 (1부터 순서대로).

### todo_read 출력 형식

```
[ ] [high] #1 로그인 버그 수정
[~] [medium] #2 유닛 테스트 작성
[ ] [low] #3 문서 업데이트
```

상태 아이콘: `[ ]` 대기 · `[~]` 진행 중 · `[x]` 완료 · `[-]` 취소

> **참고:** Todo 상태는 세션 단위 인메모리 저장입니다. 에이전트를 재시작하면 초기화됩니다. (Claude Code의 TodoRead/TodoWrite와 동일한 방식)

## Plan 모드 (Claude 스타일)

- 활성화:
  - 채팅: `/plan on`
  - CLI: `python openclaw_pi_langchain.py --plan-mode on "요청 프롬프트"`
  - 환경변수: `PI_PLAN_MODE=on`
- Plan 모드에서는 읽기 전용 계획 동작을 강제합니다.
  - 차단 툴: `write`, `edit`, `exec`, `memory_store`
  - 허용 툴(기존 정책 범위 내): `read`, `ls`, `find`, `grep`, `memory_search`, `memory_get`
- Plan 모드에서는 스킬 precheck 실패를 완화해 즉시 실패 대신 계획 응답을 반환합니다.
- Legacy 자동 메모리 저장은 Plan 모드에서 비활성화됩니다.
- 비활성화: `/plan off` 또는 `--plan-mode off`

## 실행 실패 가드 (v1)

- `exec` 출력은 기존 텍스트 형식을 유지하면서 아래 메타를 추가합니다:
  - `result=ok|error`
  - `error_type`
  - `error_signature`
  - `retryable=true|false`
- 동일한 `exec` 실패는 세션 단위로 차단되어 반복 루프를 줄입니다.
- 최근 실패 요약(Failure Digest)을 주입해 전략 전환을 유도합니다.

## 툴 반복 호출 가드 (v1)

- 동일한 툴 호출(`tool_name + 정규화된 args`)을 실행 단위로 추적합니다.
- 같은 호출이 `PI_TOOL_REPEAT_LIMIT` 횟수(기본 `3`)에 도달하면 루프 방지를 위해 실행을 중단합니다.
- 중단 후에는 툴 없이 1회 복구 응답을 시도하여:
  - 수집된 컨텍스트로 가능한 직접 답변을 제공하거나,
  - 정보가 부족하면 핵심 후속 질문 1개를 제시합니다.

## Read 토큰 효율 (v1)

- 새 툴 없이 `read`에 `full` 플래그를 추가했습니다. (`read(path, full=true)`)
- `PI_READ_STRATEGY=smart`(기본)에서는 큰 파일을 메타 + head/tail 프리뷰로 반환합니다:
  - `line_count`, `char_count`, `truncated=true`, `grep` 힌트
- `PI_READ_STRATEGY=legacy`에서는 기존 전체 읽기 동작을 유지합니다.
- 턴당 read 출력 예산(기본 20,000 chars)을 적용해 대형 파일 반복 읽기를 완화합니다.
