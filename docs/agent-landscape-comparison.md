# PiAgent 관련 오픈소스/제품군 비교 메모

작성일: 2026-07-08  
대상: PiAgent를 직접 구현하면서 참고할 오픈소스 코딩 에이전트, 대화형 개인 AI, 사내 업무 에이전트, 로컬 AI 비서, 로봇 제어 모델 계열

## 한 줄 결론

현재 PiAgent는 `earendil-works/pi`처럼 "코딩 에이전트 하네스"에 가장 가깝다. 다만 구현 언어와 설계 철학은 다르다. 우리 쪽은 Python/LangChain 기반으로 안전 정책, work note, user artifact 격리, plan mode, MCP/custom tool, OpenClaw 스타일 memory를 직접 구현하는 학습용 런타임이다. LibreChat/Open WebUI는 완성형 대화 UI 플랫폼이고, Mem0/Letta는 장기 기억/상태 에이전트 레이어이며, LangGraph는 사내 업무 에이전트용 오케스트레이션 런타임, OpenPI는 로봇 행동 모델이다.

## 실무 관점 요약

| 목적 | 가장 먼저 볼 것 | 이유 | PiAgent와의 관계 |
| --- | --- | --- | --- |
| 오픈소스 코딩 에이전트 만들기 | `earendil-works/pi` | Pi agent harness, coding agent CLI, agent core, TUI가 한 저장소에 있음 | 가장 직접 비교 대상. 확장 방식, 패키징, TUI, release hardening을 참고 |
| Inflection Pi 같은 대화형 개인 AI 만들기 | LibreChat / Open WebUI + Mem0 / Letta | 핵심은 tool 실행보다 대화 UX, 멀티 모델, 장기 기억, 사용자별 상태 | PiAgent chat은 아직 터미널 중심. 장기 기억 UX와 프로필/선호 저장은 이쪽을 참고 |
| 사내 업무 에이전트 만들기 | LangGraph + permission gate + RAG | durable execution, human-in-the-loop, persistence, 감사/승인 흐름이 중요 | PiAgent는 guard/hook/audit은 있으나 graph checkpoint, 승인 UI, RAG 운영 구조는 약함 |
| 로봇 제어 AI 연구 | Physical-Intelligence/openpi | π0 계열은 LLM 챗봇이 아니라 vision-language-action 로봇 정책 모델 | 이름은 비슷하지만 완전히 다른 축. PiAgent에는 직접 적용하지 않는 편이 좋음 |
| 로컬 AI 비서 만들기 | Open WebUI + Ollama + Mem0 | 개인 PC에서 UI, 로컬 모델, 문서/RAG, memory를 빠르게 조합 가능 | PiAgent는 로컬 모델 연결은 가능하지만 UI와 모델 관리 경험은 약함 |

## 현재 PiAgent의 위치

현재 저장소 기준 PiAgent가 이미 갖춘 기능:

- Python/LangChain 기반 agent runtime
- CLI와 `chat.py` 터미널 대화 모드
- builtin tools: read, write, edit, multiedit, ls, find, grep, exec, exec_readonly, web_fetch, web_search, todo, memory, work note 등
- workspace guard와 blocked path 정책
- user artifact 격리
- plan mode와 permission mode
- custom Python tool module 로딩
- MCP stdio tool 로딩
- OpenClaw 스타일 markdown memory와 flat memory store
- session/audit/evidence 저장
- tool result offload, read budget, repeat guard, exec failure guard
- read-only subagent delegation
- 기능별 모듈화된 `piagent/` 패키지

현재 PiAgent가 약한 부분:

- 웹 기반 대화 UX, 계정, 팀 관리 없음
- 장기 기억을 사용자에게 설명/수정/삭제하는 UX가 약함
- 사내 업무용 RAG ingestion/indexing 파이프라인 없음
- LangGraph식 checkpoint/resume graph는 없음
- human-in-the-loop 승인 UI는 hook/policy 수준에 머무름
- 로컬 모델 관리와 모델 다운로드 UX 없음
- 프로덕션 배포, 멀티 테넌트 인증, 권한 모델은 없음

## 사례별 검토

### 1. `earendil-works/pi`

공식 README는 이 저장소를 "Pi Agent Harness"라고 설명하며, `pi-coding-agent`, `pi-agent-core`, `pi-ai`, `pi-tui` 같은 패키지를 함께 둔다. 특히 `pi-coding-agent`는 interactive coding agent CLI이고, `pi-agent-core`는 tool calling과 state management를 맡는다.

우리 PiAgent와 가장 가까운 점:

- 둘 다 "agent harness"라는 성격이 강하다.
- 코딩 에이전트 CLI가 중심이다.
- tool calling, state, skills/extensions 같은 확장 지점을 둔다.
- 사용자가 자기 워크플로에 맞게 바꿀 수 있는 방향을 지향한다.

차이:

- `earendil-works/pi`는 TypeScript/npm 생태계, TUI, package 단위 확장을 중심으로 한다.
- 우리 PiAgent는 Python/LangChain 기반이며, 학습용으로 runtime을 직접 쪼개서 구현하는 데 초점이 있다.
- `earendil-works/pi` README는 기본적으로 파일시스템/프로세스/네트워크/credential 접근을 제한하는 built-in permission system이 없고, 더 강한 경계가 필요하면 container/sandbox를 쓰라고 설명한다. 반대로 우리 PiAgent는 `WorkspaceGuard`, blocked path, user artifact isolation, plan mode, exec_readonly, dangerous command block 같은 정책을 런타임 내부에 이미 둔다.

배울 점:

- npm package 구조, TUI 렌더링, extension/skill/package 배포 방식
- supply-chain hardening: exact pinning, lifecycle script 제한, release smoke test
- agent session 공유와 재현 가능한 테스트 흐름

따라 하지 않아도 되는 점:

- 지금 단계에서 TypeScript/npm 패키지 구조를 그대로 옮길 필요는 없다.
- TUI 고도화보다 먼저 Python 런타임의 tool contract와 policy test를 더 단단히 하는 것이 낫다.

### 2. LibreChat

LibreChat은 self-hosted AI chat platform이다. 여러 AI provider를 하나의 privacy-focused interface에 묶고, agents, MCP, artifacts, code interpreter, custom actions, conversation search, multi-user authentication 같은 기능을 제공한다. 별도 문서의 User Memory는 user-specific key/value store이며, memory agent가 각 chat request 시작 시 이 저장소를 읽고 쓰는 구조라고 설명한다.

우리 PiAgent와 가까운 점:

- multi-provider AI 대화
- agents, MCP, code interpreter/custom action 같은 tool 확장
- memory를 대화 맥락에 주입하는 방향

차이:

- LibreChat은 웹 앱/제품에 가깝다. 사용자, 인증, conversation search, UI, artifact display가 중요하다.
- 우리 PiAgent는 터미널 기반 개발자 런타임이다.
- PiAgent memory는 개발자가 직접 도구로 검색/저장하는 형태가 중심이고, 사용자에게 memory를 시각적으로 관리하는 UX는 없다.

배울 점:

- 사용자별 memory 설정/관리 UX
- agent builder, predefined agents, custom actions 같은 제품화된 구성 방식
- conversation search와 artifacts UX

PiAgent에 적용하면 좋은 다음 기능:

- `chat.py`에서 memory가 언제 검색/저장되는지 더 잘 보여주기
- memory list/edit/delete 도구 또는 chat command 추가
- session search UX 개선

### 3. Open WebUI

Open WebUI는 Ollama와 OpenAI-compatible API를 지원하는 self-hosted AI platform이며, offline 동작, RAG, plugin/tool calling, pipelines, MCP/OpenAPI 연결을 강조한다. 기능 문서는 Python tools, pipelines, MCP, OpenAPI servers로 기능을 붙일 수 있다고 설명한다.

우리 PiAgent와 가까운 점:

- local/cloud provider를 모두 연결할 수 있는 구조
- RAG, tools, MCP, pipelines 같은 확장 방향
- self-hosted/private AI 지향

차이:

- Open WebUI는 사용자가 바로 쓰는 웹 UI와 모델 관리 경험이 핵심이다.
- PiAgent는 코딩 에이전트 runtime과 안전한 tool execution이 핵심이다.
- Open WebUI는 UI 플랫폼이고, PiAgent는 하네스/실험 런타임이다.

배울 점:

- Ollama 연결 UX
- RAG 문서 업로드/검색 UX
- tool/function/pipeline을 사용자에게 보여주는 방식

PiAgent에 적용하면 좋은 다음 기능:

- `PI_MODEL`이 Ollama/OpenAI-compatible endpoint를 쉽게 쓰도록 예시 강화
- tool list와 tool permission 상태를 chat에서 보기 좋게 출력
- RAG는 당장 전체 플랫폼처럼 만들기보다, "workspace docs index → query tool" thin slice부터 구현

### 4. Mem0

Mem0는 AI agents/apps용 universal memory layer다. 공식 README와 사이트는 persistent context, memory extraction, embedding/vector storage, production memory infrastructure를 강조한다.

우리 PiAgent와 가까운 점:

- 장기 기억과 personalized context가 중요하다.
- agent가 이전 대화/사용자 선호/사실을 재사용해야 한다.

차이:

- Mem0는 memory layer 자체가 제품/라이브러리의 중심이다.
- PiAgent는 memory가 여러 기능 중 하나이며, file/tool/policy/run loop가 더 큰 비중을 차지한다.

배울 점:

- memory record schema
- add/search/update/delete memory lifecycle
- memory evaluation과 stale memory 처리

PiAgent에 적용하면 좋은 다음 기능:

- `memory_store`에 kind/source/confidence/expiry 같은 metadata 보강
- memory search 결과에 "언제 저장됐고 왜 쓰는지" 표시
- 사용자가 memory를 삭제하거나 수정할 수 있는 명령 추가

### 5. Letta

Letta는 stateful agents platform이며, advanced memory, self-improvement, agent state를 강조한다. 최신 docs는 Agent SDK와 MemFS 같은 memory system을 안내한다.

우리 PiAgent와 가까운 점:

- agent state와 memory가 agent 성능의 핵심이라는 관점
- 단순 chat history보다 구조화된 상태가 중요하다는 방향

차이:

- Letta는 stateful agent platform이다. agent identity, memory filesystem, hosted/server architecture가 중심이다.
- PiAgent는 workspace 안에서 coding task를 수행하는 local runtime이다.

배울 점:

- agent state를 파일/메모리/도구 권한과 분리해 모델링하는 방식
- stateful agent lifecycle
- memory를 단순 텍스트 저장이 아니라 agent 운영체계의 일부로 보는 관점

PiAgent에 적용하면 좋은 다음 기능:

- session state와 memory state의 경계 정리
- work note와 memory의 역할 분리: work note는 task-local, memory는 cross-session
- memory pruning/compaction 정책

### 6. LangGraph

LangGraph 공식 문서는 LangGraph를 durable execution, streaming, human-in-the-loop, persistence를 제공하는 orchestration runtime으로 설명한다. Human-in-the-loop 문서는 tool call이 위험할 때 정책에 따라 실행을 멈추고 사람 결정을 기다리는 middleware를 제공한다고 설명한다.

우리 PiAgent와 가까운 점:

- tool execution을 상태와 정책으로 통제해야 한다.
- 장기 실행/중단/재개/검증이 중요하다.
- 사내 업무 에이전트에는 승인, 감사 로그, persistence가 필요하다.

차이:

- LangGraph는 graph runtime이다. node/edge/checkpointer/store 중심이다.
- PiAgent는 단일 agent loop와 tools 중심이다.
- PiAgent의 hook/permission mode는 있지만, graph-level checkpoint/resume과 명시적 interrupt UI는 없다.

배울 점:

- 승인 대기 상태를 first-class state로 다루는 방식
- checkpointer/store로 재개 가능한 업무 흐름 만들기
- production observability와 evaluation 연결

PiAgent에 적용하면 좋은 다음 기능:

- permission gate를 `ask_user`와 연결해 실제 승인/거절 루프 구현
- audit log를 사람이 읽는 decision ledger로 정리
- 장기 task는 LangGraph 스타일 state machine으로 분리할지 검토

### 7. Physical-Intelligence/openpi

OpenPI는 robotics용 open-source models/packages 저장소다. 공식 README는 로봇 모델과 패키지를 담고 있으며, π0/π0.5 계열 로봇 정책 모델과 example을 제공한다. Physical Intelligence 블로그는 π0-derived model을 로봇 시스템에 연결하는 client 예제와 pretrained checkpoints를 언급한다.

우리 PiAgent와 가까운 점:

- 이름이 비슷하고 "Pi" 계열로 보일 수 있다.

차이:

- 실질적으로는 완전히 다르다.
- OpenPI는 vision-language-action robot policy model이다.
- PiAgent는 LLM 기반 코딩/업무 도구 실행 agent다.

배울 점:

- 지금 단계에서는 거의 없다.
- 로봇 제어 연구를 시작할 때만 별도 트랙으로 본다.

PiAgent에 적용하면 안 되는 것:

- 로봇 inference/model checkpoint 구조를 PiAgent runtime에 섞지 않는다.
- 이름 유사성만으로 architecture 참고 대상으로 삼지 않는다.

### 8. Ollama

Ollama는 로컬에서 open model을 실행하고 관리하는 도구다. 공식 GitHub/사이트는 macOS, Windows, Linux에서 모델을 실행하고, 다른 agents/applications와 연결하는 흐름을 제공한다. Open WebUI 문서는 Ollama API protocol을 연결 대상으로 보고, 보통 `11434` 포트의 Ollama API와 연동한다고 설명한다.

우리 PiAgent와 가까운 점:

- 로컬 모델 실행과 privacy-preserving workflow에 유용하다.
- PiAgent의 `PI_MODEL`/LangChain provider 설정과 조합할 수 있다.

차이:

- Ollama는 agent runtime이 아니라 model runner다.
- memory, tool policy, audit, workspace guard는 PiAgent가 담당해야 한다.

배울 점:

- local-first 설치/모델 관리 UX
- OpenAI-compatible/local endpoint 연결 문서

PiAgent에 적용하면 좋은 다음 기능:

- Ollama 예시 설정 문서 추가
- local model smoke test command 추가
- `chat.py /status`에 provider/endpoint 힌트 표시

## 우리 PiAgent 기준 추천 우선순위

### 1순위: 코딩 에이전트 하네스 완성도

참고: `earendil-works/pi`, LangGraph 일부 개념

- tool contract를 더 명확히 문서화
- permission gate를 실제 승인 루프로 개선
- audit/evidence를 사람이 읽기 좋은 형태로 출력
- custom tool/module loading의 보안 테스트 확대

### 2순위: 대화형 UX와 기억

참고: LibreChat, Open WebUI, Mem0, Letta

- `chat.py`에서 단계, memory, skill, tool 상태 표시 강화
- memory list/edit/delete UX
- user preference와 task memory 분리
- session search와 work note 탐색 명령

### 3순위: 사내 업무 에이전트 준비

참고: LangGraph, Open WebUI RAG

- workspace 문서 RAG thin slice
- permission approval queue
- audit log report generation
- read-only/plan/execute 역할 분리

### 4순위: 로컬 모델 연결

참고: Ollama + Open WebUI

- Ollama/OpenAI-compatible 설정 예시
- local model용 fallback prompt/token 설정
- 간단한 local provider smoke test

## 지금 만든 PiAgent와 비교한 핵심 차이

| 축 | 현재 PiAgent | 참고 사례의 강점 | 차이/갭 |
| --- | --- | --- | --- |
| 코딩 agent harness | Python/LangChain, tool registry, plan mode, guard, memory | `earendil-works/pi`는 TS/npm 패키지, TUI, Pi packages, supply-chain hardening | 배포/패키징/TUI는 약하지만 내부 안전 정책은 더 직접 구현됨 |
| 대화 UI | 터미널 `chat.py` | LibreChat/Open WebUI는 웹 UI, conversation, artifacts, auth | PiAgent는 개발자용 터미널 중심 |
| 장기 기억 | markdown/flat memory, memory tools | Mem0/Letta는 memory/state가 제품의 중심 | PiAgent는 memory UX와 lifecycle이 약함 |
| 사내 업무 안정성 | audit, hooks, permission mode, evidence | LangGraph는 persistence/HITL/durable execution이 강함 | PiAgent는 graph checkpoint/resume이 없음 |
| 로컬 모델 | LangChain provider 설정으로 가능 | Ollama/Open WebUI는 모델 관리/로컬 UI가 강함 | PiAgent는 runner가 아니라 harness |
| 로봇 제어 | 해당 없음 | OpenPI는 robot policy model | 참고 대상이 다름 |

## 다음 구현 후보

1. `chat.py` UX 강화
   - 단계 표시: request received → context build → tool call → model response → verification
   - 현재 skill/plan/memory/tool state 표시
   - tool call summary와 audit path 표시

2. memory 관리 명령
   - `/memory search <query>`
   - `/memory recent`
   - `/memory delete <id>`
   - memory를 work note와 구분해 보여주기

3. permission gate 개선
   - 위험 tool call 감지 시 `ask_user`로 승인 요청
   - 승인/거절 결과를 audit log에 decision ledger로 저장
   - plan mode에서는 mutating tool이 왜 없는지 사용자에게 표시

4. Ollama/local model 문서
   - `PI_MODEL`과 provider 설정 예시
   - OpenAI-compatible local endpoint 예시
   - Open WebUI와 PiAgent를 같이 쓰는 패턴

5. RAG thin slice
   - `docs/` 또는 `README*`를 색인하는 최소 도구
   - 검색 결과 source path/line 포함
   - 나중에 LangGraph식 workflow로 확장 가능하게 경계 설정

## 출처와 확인한 내용

| 출처 | 확인한 내용 |
| --- | --- |
| [earendil-works/pi GitHub](https://github.com/earendil-works/pi) | Pi Agent Harness, `pi-coding-agent`, `pi-agent-core`, `pi-ai`, permission/containerization, supply-chain hardening |
| [Pi coding-agent package](https://github.com/earendil-works/pi/tree/main/packages/coding-agent) | coding agent CLI package 구조 |
| [LibreChat GitHub](https://github.com/danny-avila/LibreChat) | self-hosted AI chat platform, agents, MCP, artifacts, code interpreter, auth |
| [LibreChat User Memory](https://www.librechat.ai/docs/features/memory) | user-specific key/value memory store와 memory agent 흐름 |
| [Open WebUI GitHub](https://github.com/open-webui/open-webui) | self-hosted/offline AI platform, Ollama/OpenAI-compatible API, RAG |
| [Open WebUI Features](https://docs.openwebui.com/features/) | Python tools, pipelines, MCP, OpenAPI 확장 |
| [Open WebUI Ollama guide](https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-ollama/) | Ollama API protocol과 Open WebUI 연결 |
| [Mem0 GitHub](https://github.com/mem0ai/mem0) | AI agents/apps용 universal memory layer |
| [Letta GitHub](https://github.com/letta-ai/letta) | stateful agents와 advanced memory |
| [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview) | durable execution, streaming, human-in-the-loop, persistence |
| [LangChain HITL docs](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) | tool call 승인/중단 정책 |
| [Physical-Intelligence/openpi](https://github.com/Physical-Intelligence/openpi) | robotics용 open-source model/package 저장소 |
| [Ollama GitHub](https://github.com/ollama/ollama) | local model runner와 agent/application 연결 |
