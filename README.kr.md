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
- `PI_EXEC_TIMEOUT`
- `PI_NO_MEMORY`
- `PI_MEMORY_MODE`
- `PI_MEMORY_DIR`
- `PI_BLOCKED_PATHS`
- `PI_READ_STRATEGY` (`smart` 기본, 또는 `legacy`)
- `PI_EXEC_PATH_CORRECTION` (기본 `false`, 제한적 경로 교정)

## 실행 실패 가드 (v1)

- `exec` 출력은 기존 텍스트 형식을 유지하면서 아래 메타를 추가합니다:
  - `result=ok|error`
  - `error_type`
  - `error_signature`
  - `retryable=true|false`
- 동일한 `exec` 실패는 세션 단위로 차단되어 반복 루프를 줄입니다.
- 최근 실패 요약(Failure Digest)을 주입해 전략 전환을 유도합니다.

## Read 토큰 효율 (v1)

- 새 툴 없이 `read`에 `full` 플래그를 추가했습니다. (`read(path, full=true)`)
- `PI_READ_STRATEGY=smart`(기본)에서는 큰 파일을 메타 + head/tail 프리뷰로 반환합니다:
  - `line_count`, `char_count`, `truncated=true`, `grep` 힌트
- `PI_READ_STRATEGY=legacy`에서는 기존 전체 읽기 동작을 유지합니다.
- 턴당 read 출력 예산(기본 20,000 chars)을 적용해 대형 파일 반복 읽기를 완화합니다.
