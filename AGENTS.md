# AGENTS.md

PiAgent 저장소에서 작업하는 에이전트와 개발자를 위한 운영 지침이다. 이 문서는 1000줄 미만으로 유지한다.

## 프로젝트 목표

사용자의 목표는 OpenClaw에서 영감을 받은 `Pi Agent`를 직접 구현해보는 것이다.

이 저장소는 "완성된 제품"보다 "직접 만들며 이해하는 에이전트 런타임"에 가깝다. 따라서 새 기능을 붙일 때는 큰 프레임워크를 더 얹기보다, 현재 코드의 동작 원리를 작게 확장하고 테스트로 확인하는 방식을 우선한다.

## 현재 구현 리뷰

핵심 런타임은 `piagent/` 패키지로 모듈화되어 있다. `openclaw_pi_langchain.py`는 기존 import와 CLI 실행을 유지하는 호환 래퍼다. 현재 구현된 주요 축은 다음과 같다.

- `PiAgentConfig`: 모델, 워크스페이스, 세션, 메모리, MCP, 스킬, 플랜 모드, 권한 모드 설정
- `WorkspaceGuard`: 워크스페이스 이탈 방지, 차단 경로 적용, 사용자별 artifact 경로 격리
- 세션/감사/증거 저장소: `FlatSessionStore`, `AuditLogger`, `SessionEvidenceStore`
- 메모리: markdown 기반 OpenClaw 메모리, flat store, sqlite-vec 또는 hash 기반 검색
- 도구 레지스트리: builtin, custom Python tool, MCP tool 병합
- 스킬 시스템: `skills/*/SKILL.md` 탐색, 자동/수동 선택, tool allow/deny 적용
- 플랜 모드: mutating tool 차단, `plan_note_write` 허용, `<proposed_plan>` 유도
- 서브에이전트: `explore`, `plan`, `verify` 읽기 전용 위임
- 실행 안전장치: read budget, tool result offload, exec failure guard, cross-turn repeat guard
- CLI 진입점: `python openclaw_pi_langchain.py "prompt"` 또는 `piagent/cli.py`
- 대화형 진입점: `python chat.py`

테스트는 `tests/test_piagent_core.py`에 집중되어 있고, 현재 회귀 범위는 도구 등록, artifact 격리, hook 차단, work note, plan mode, read/grep/find, memory recall, delegate task 등을 포함한다.

## 바이브 기획 기준

이 프로젝트의 다음 개발은 "에이전트가 알아서 다 하게 만들기"가 아니라, 사용자가 직접 Pi Agent의 내부 구조를 이해하고 한 기능씩 완성하는 방향이어야 한다.

### 아이디어 한 줄

로컬 워크스페이스에서 안전하게 읽고, 계획하고, 제한된 도구로 실행하며, 스킬과 메모리를 활용하는 작은 코딩 에이전트 런타임을 직접 구현한다.

### 대상 사용자

- 1차 사용자: 이 저장소의 개발자
- 2차 사용자: 로컬 프로젝트에서 최소 에이전트 런타임을 실험하려는 개발자

### 해결하려는 문제

기성 코딩 에이전트는 편하지만 내부 동작을 직접 바꾸고 배우기 어렵다. 이 프로젝트는 모델 호출, 도구 등록, 권한 정책, 메모리, 스킬, 세션 기록, 검증 루프를 직접 구현 가능한 단위로 분해해 학습과 실험을 가능하게 한다.

### 사용 전후 변화

- 사용 전: 에이전트 런타임의 기능을 외부 제품이나 거대한 프레임워크에 의존한다.
- 사용 후: 작은 Python 코드베이스에서 tool, policy, memory, skill, session behavior를 직접 수정하고 테스트할 수 있다.

## MVP 범위

현재 저장소 기준 MVP는 이미 상당 부분 구현되어 있다. 앞으로는 아래 흐름을 안정화하는 것을 MVP 완료 기준으로 둔다.

1. 사용자가 CLI 또는 chat으로 프롬프트를 입력한다.
2. Pi Agent가 안전 정책과 워크스페이스 컨텍스트를 반영한다.
3. 필요한 도구만 선택해 읽기, 계획, 실행, 저장을 수행한다.
4. 결과와 tool evidence를 세션에 남긴다.
5. 동일 실패나 반복 실행을 감지한다.
6. 테스트로 핵심 정책이 깨지지 않았음을 확인한다.

## 비목표

이번 단계에서 하지 않을 것:

- 범용 SaaS 제품화
- 멀티 유저 웹 UI
- 결제, 계정, 권한 관리
- 원격 코드 실행 서비스
- 모든 MCP transport 지원
- 모든 파일 형식 처리
- 자동 self-improvement 루프
- 프로덕션급 보안 감사 시스템
- 새 대형 프레임워크 도입

## 안전 정책

아래 차단 경로는 사용자가 명시적으로 정책 업데이트를 요청하지 않는 한 읽기, 목록화, grep, 편집, 실행 대상으로 삼지 않는다.

차단 경로:

- `.env`
- `.git/**`
- `.openclaw/memory/**`
- `secrets/**`
- `private/**`
- `node_modules/**`

추가 규칙:

- secrets, credentials, API key를 출력하지 않는다.
- 숨김 메타데이터나 대형 디렉터리를 기본으로 덤프하지 않는다.
- destructive command는 사용자가 명시적으로 요청하지 않는 한 실행하지 않는다.
- 워크스페이스 밖 경로로 읽기/쓰기하지 않는다.
- 사용자별 artifact 격리 규칙을 우회하지 않는다.

## 메모리 정책

OpenClaw 메모리 도구가 필요한 경우 최소 범위로 사용한다.

권장 순서:

1. `memory_search`
2. `memory_get`
3. `memory_store`

메모리 읽기는 현재 작업과 직접 관련된 항목만 대상으로 한다. `.openclaw/memory/**` 파일을 일반 파일 도구로 직접 읽지 않는다.

## 작업 원칙

- 먼저 현재 구조를 읽고, 기존 패턴을 따른다.
- 관련 없는 리팩터링을 하지 않는다.
- 새 라이브러리는 마지막 선택지로 둔다.
- 기능을 추가하면 테스트도 같이 추가하거나 기존 테스트를 갱신한다.
- 실패한 검증 명령은 숨기지 않는다.
- 문서와 코드가 어긋나면 문서도 함께 고친다.
- 긴 출력은 필요한 부분만 요약한다.
- `piagent/agent_*.py` mixin 파일은 기능 경계별로 나뉘어 있으므로 변경 전 관련 mixin과 테스트를 좁게 읽는다.

## Codegraph 검색 원칙

코드 위치, 호출 관계, 영향도, 구조 흐름을 찾는 작업은 raw `grep`/파일 읽기보다 Codegraph를 먼저 사용한다.

사용 트리거:

- "어디에 있어?", "find", "what calls", "impact", "흐름", "구조", "호출 관계"처럼 코드 탐색이 핵심인 질문
- `piagent/` 안의 특정 class/function/method 위치 탐색
- 변경 전 관련 symbol, caller, callee, 영향 범위를 좁혀야 하는 경우

권장 순서:

1. Codegraph MCP 도구가 있으면 먼저 사용한다.
   - 구조/흐름 파악: `codegraph_explore`
   - symbol 위치: `codegraph_search`
   - 호출자/피호출자/영향도: `codegraph_callers`, `codegraph_callees`, `codegraph_impact`
   - 인덱스 상태: `codegraph_status`
2. MCP 도구가 없지만 `.codegraph`가 있으면 CLI를 사용한다.
   - 상태 확인: `codegraph status --json .`
   - symbol 검색: `codegraph query --json --path . --limit 20 <query>`
   - 파일 목록: `codegraph files --json --path . --format flat`
3. Codegraph가 없거나 실패할 때만 `rg`, `Get-Content`, 파일 직접 읽기로 fallback한다.

추가 규칙:

- MCP가 없어서 CLI로 전환하는 경우 한 줄로 이유를 알린다.
- Codegraph 결과로 후보를 좁힌 뒤, 실제 수정 전에는 해당 파일의 좁은 주변부를 읽고 확인한다.
- 인덱스가 stale이거나 pending changes가 있으면 `codegraph sync .` 또는 필요한 경우 `codegraph index .` 후 다시 확인한다.
- 차단 경로와 secrets 정책은 Codegraph 검색에도 그대로 적용한다.
- 넓은 grep으로 대량 출력하기보다, Codegraph로 symbol과 관계를 먼저 좁힌 뒤 필요한 최소 범위만 읽는다.

## 코드베이스 지도

- `openclaw_pi_langchain.py`: 기존 public import와 script 실행을 유지하는 호환 래퍼
- `piagent/agent_core.py`: `OpenClawPiLangChain` 조립과 초기화
- `piagent/agent_tools.py`: builtin tool 정의
- `piagent/agent_run.py`: run loop, prompt/runtime context, compaction
- `piagent/agent_registry.py`: tool registry, custom tool, MCP tool, skill discovery
- `piagent/agent_hooks.py`: hook loading and tool wrapping
- `piagent/agent_worknotes.py`: work note and plan note support
- `piagent/agent_state.py`: permission, artifact alias, evidence repeat guard state
- `piagent/agent_exec_memory.py`: exec failure handling, read budget, memory/session notes
- `piagent/agent_subagents.py`: read-only subagent delegation
- `piagent/models.py`: config/result/spec dataclasses and callbacks
- `piagent/workspace.py`: workspace guard and blocked path policy
- `piagent/stores.py`: session, evidence, audit, memory stores
- `piagent/mcp.py`: stdio MCP client
- `piagent/utils.py`, `piagent/deps.py`: shared helpers and imports
- `piagent/cli.py`: CLI argument parsing and main entrypoint
- `chat.py`: 대화형 터미널 UI와 slash command
- `tests/test_piagent_core.py`: 핵심 회귀 테스트
- `skills/data-report-writer/SKILL.md`: 샘플 스킬
- `requirements-piagent.txt`: Python 의존성
- `mcp_servers.json`: MCP server 설정 예시
- `Dockerfile`, `docker-compose.yml`: 컨테이너 실행 환경
- `README.md`, `README.kr.md`: 사용자 문서

## 변경 전 체크리스트

작업 시작 전에 다음을 확인한다.

- 이 변경이 MVP 학습 목표에 직접 연결되는가?
- 어떤 기존 테스트가 관련되는가?
- 새 테스트를 어디에 둘 것인가?
- 차단 경로나 secrets에 접근하지 않는가?
- 읽기 전용 계획이 필요한 작업인가, 즉시 구현해도 되는 작업인가?

## 기능 요구사항 기준

새 기능 요구사항은 acceptance criteria를 포함해야 한다.

예시:

| ID | 요구사항 | Acceptance Criteria |
|---|---|---|
| FR-1 | plan mode는 mutating tool을 숨긴다 | `write`, `edit`, `multiedit`, `exec`, `memory_store`, `work_note_update`가 plan mode tool set에 없다 |
| FR-2 | 사용자 artifact는 격리된다 | `--user-id alice`일 때 `reports/x.md`가 `artifacts/users/alice/reports/x.md`로 저장된다 |
| FR-3 | 반복 실행은 차단된다 | 같은 tool signature가 이전 evidence와 일치하면 승인 토큰 없이 재실행되지 않는다 |

## 보안 검토 기준

다음 변경은 고위험으로 보고 threat model을 먼저 작성한다.

- 인증, 세션, 토큰 처리
- 외부 API 호출
- 파일 업로드 또는 HTML/Markdown 렌더링
- shell 실행 정책
- MCP server 연결
- LLM 출력이 명령어, 코드, 정책 판단으로 이어지는 흐름
- 메모리 저장 또는 검색 정책

확인할 것:

- secrets가 코드, 로그, 프롬프트, 테스트 fixture에 남지 않는가?
- 모델 출력이 실행되기 전에 검증되는가?
- 권한 체크가 UI나 프롬프트에만 의존하지 않는가?
- 실패 시 안전하게 멈추는가?
- 비용 폭주나 무한 루프를 막는 한도가 있는가?

## 오버엔지니어링 방지

아래 신호가 보이면 범위를 줄인다.

- 아직 사용자 흐름이 하나뿐인데 plugin architecture를 확장하려 한다.
- 테스트 하나로 확인할 수 있는 기능에 새 추상화를 만든다.
- 수동 검증 가능한 동작을 먼저 자동화부터 하려 한다.
- 현 단계에서 필요 없는 UI, 계정, 권한, 배포 기능을 붙이려 한다.
- 이미 모듈화된 경계를 무시하고 여러 `agent_*.py` 영역을 한꺼번에 바꾼다.

## 추천 작업 분해

첫 번째 작업은 항상 작은 end-to-end thin slice로 잡는다.

### T1. 현재 회귀 테스트 안정화

- 목표: 기존 테스트가 로컬에서 통과하는지 확인하고 실패 원인을 좁힌다.
- 변경 예상 파일: 없음 또는 `tests/test_piagent_core.py`
- 제외 범위: 새 기능 추가
- 검증 명령: `python -m pytest tests/test_piagent_core.py`
- Acceptance Criteria: 실패가 있으면 실패 테스트명, 원인, 다음 수정 후보가 정리된다.

### T2. 가장 작은 사용자 흐름 문서화

- 목표: CLI 실행부터 tool evidence 저장까지 한 흐름을 README와 테스트 관점에서 정리한다.
- 변경 예상 파일: `README.md`, `README.kr.md`, 테스트 필요 시 `tests/test_piagent_core.py`
- 제외 범위: 런타임 구조 개편
- 검증 명령: `python -m pytest tests/test_piagent_core.py`
- Acceptance Criteria: 새 사용자가 하나의 프롬프트 실행 흐름을 재현할 수 있다.

### T3. 기능 하나 선택 후 정책 테스트 추가

- 목표: memory, skill, MCP, plan mode 중 하나를 골라 정책 기반 테스트를 강화한다.
- 변경 예상 파일: 관련 `piagent/*.py`, `tests/test_piagent_core.py`
- 제외 범위: 여러 기능 동시 수정
- 검증 명령: `python -m pytest tests/test_piagent_core.py`
- Acceptance Criteria: 새 edge case가 테스트로 고정된다.

### T4. 구조 경계 검토

- 목표: `piagent/` 모듈 경계가 기능 책임에 맞는지 확인하고, 실제로 분리할 가치가 있는 경계를 제안한다.
- 변경 예상 파일: 계획 문서 또는 작은 모듈 1개
- 제외 범위: 대규모 리라이트
- 검증 명령: `python -m pytest tests/test_piagent_core.py`
- Acceptance Criteria: 기존 public CLI/chat 동작이 유지된다.

## 구현 에이전트용 기본 프롬프트

계획 전용:

```text
이 저장소를 먼저 탐색해줘. 아직 코드를 수정하지 마.
목표 기능: [기능명]
확인할 것:
1. 관련 파일과 책임
2. 기존 구현 패턴
3. 테스트 위치와 실행 명령
4. 변경 위험이 높은 영역
5. 구현 전에 확인해야 할 질문

탐색 후에는 구현 계획만 작성하고, 승인 전에는 코드를 수정하지 마.
```

구현 전용:

```text
승인된 계획의 [Task ID]만 구현해줘.
제약:
- 관련 없는 리팩터링 금지
- 새 라이브러리 추가 금지. 필요하면 먼저 이유 설명
- 변경 파일 최소화
- 테스트 추가 또는 업데이트
- 완료 후 변경 파일, 실행한 검증 명령, 실패한 명령, 남은 리스크 보고
```

## 검증 명령

기본 검증:

```powershell
python -m pytest tests/test_piagent_core.py
```

필요 시 수동 실행:

```powershell
python openclaw_pi_langchain.py "간단한 요청"
python chat.py
```

MCP, 외부 API, 실제 모델 호출 검증은 환경변수와 비용이 관련될 수 있으므로, 테스트 더블이나 명시적 사용자 승인 없이 확장하지 않는다.

## Go / No-go 기준

Go:

- 대상 사용자와 문제가 명확하다.
- 변경 범위와 제외 범위가 작다.
- 테스트 또는 수동 검증 방법이 있다.
- 차단 경로와 secrets 정책을 지킨다.
- 기존 CLI/chat 사용성을 깨뜨리지 않는다.

No-go:

- 비목표가 없다.
- "좋은 UX", "안전하게" 같은 말만 있고 검증 기준이 없다.
- 보안 고위험 기능인데 threat model이 없다.
- 새 라이브러리나 구조 개편이 기능보다 앞선다.
- 테스트 실패를 설명하지 않고 완료 처리한다.

## 문서 유지 규칙

- 이 파일은 1000줄 미만으로 유지한다.
- 새로 합의된 안전 정책은 여기에 반영한다.
- 구현 세부 튜토리얼은 README로 옮기고, AGENTS.md에는 작업 원칙과 정책만 둔다.
- 현재 구현 상태가 크게 바뀌면 "현재 구현 리뷰"를 갱신한다.
