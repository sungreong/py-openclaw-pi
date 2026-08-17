# PiAgent Central Skill 카탈로그

조회일: 2026-08-14  
Central Skill Home: `C:\Users\leesu\skill-bridge-repo`  
대상 Agent: `.agents`

## 결론

현재 PiAgent에 필요한 Central 그룹은 이미 Workspace에 대부분 반영되어 있다. 즉시 가져올 새 그룹은 없으며, 기존 그룹을 다시 복사하면 얻는 이점보다 변경된 `skill-manager`를 덮어쓸 위험이 더 크다.

| 우선순위 | Central 그룹 | 용도 | 비교 결과 | 권장 작업 |
| --- | --- | --- | --- | --- |
| 필수 | `lang-agent` | LangChain·LangGraph·Deep Agents 개발 | 11/11 `same` | 복사하지 않고 현재판 사용 |
| 높음 | `vibe-planning` | 기획·문서·보고서·프레젠테이션 | 16 `same`, `skill-manager` 1 `changed` | 그룹 전체 복사 금지, 변경 diff 검토 |
| 선택 | `ui-ux-skill-group` | UI·슬라이드·시각화 설계 | 7/7 `same` | 시각 결과물 작업 때 사용 |
| 낮음 | `front-design` | 프론트엔드 디자인 전 과정 | 21개 대형 그룹 | 현재 PiAgent MVP에는 과도함 |

## Central 현황

| Agent | Central 스킬 수 |
| --- | ---: |
| `agents` | 48 |
| `codex` | 17 |
| `claude` | 43 |
| `cursor` | 8 |
| `gemini` | 29 |
| `antigravity` | 8 |

`.agents` 비교 결과:

- Central: 48개
- Workspace: 53개
- 양쪽 공유: 46개
- Central 전용: `skill-bridge-manager`, `skill-generator`
- Workspace 전용: `codegraph-search-skills`, `codegraph-setup`, `humanize`, `humanize-korean`, `humanize-redo`, `markdown-manager`, `markdown-writer`

## 필수 그룹: lang-agent

11개 스킬 모두 Central과 Workspace의 폴더 내용이 동일하다.

| 스킬 | 역할 |
| --- | --- |
| `framework-selection` | LangChain, LangGraph, Deep Agents 중 적합한 계층 선택 |
| `langchain-dependencies` | 패키지 버전, 설치, 호환성 관리 |
| `langchain-fundamentals` | `create_agent`, 도구, 기본 middleware 구현 |
| `langchain-middleware` | 승인, custom middleware, structured output |
| `langchain-rag` | 문서 분할, 임베딩, vector store 기반 RAG |
| `langgraph-fundamentals` | StateGraph, node, edge, Command, Send, streaming |
| `langgraph-human-in-the-loop` | `interrupt()`, 승인·검증·오류 처리 |
| `langgraph-persistence` | checkpointer, thread, store, time travel |
| `deep-agents-core` | `create_deep_agent()`와 harness 구조 |
| `deep-agents-memory` | State·Store·Filesystem backend |
| `deep-agents-orchestration` | subagent, todo, human approval |

PiAgent는 현재 LangChain `create_agent` 기반의 자체 런타임이므로, 일상 변경에서는 `framework-selection`, `langchain-dependencies`, `langchain-fundamentals`, `langchain-middleware`가 가장 직접적이다. Deep Agents 3종은 향후 프레임워크 전환을 검토할 때만 필요하다.

## 문서·보고서·시각화 관련 스킬

Central에는 독립된 문서 그룹이 없고 관련 스킬이 `vibe-planning` 그룹에 포함되어 있다.

| 스킬 | 적용 범위 | PiAgent 활용 |
| --- | --- | --- |
| `document-production-advisor` | Markdown 보고서, blog HTML, DOCX/PPTX handoff 검증 | 역량 보고서와 기술 문서 작성 |
| `md-presentation-composer` | 기존 Markdown을 보고서·발표 구조로 재구성 | deck-ready Markdown 제작 |
| `slides` | Chart.js 기반 HTML 프레젠테이션 | 브라우저형 발표 자료 |
| `design-system` | 디자인 토큰과 컴포넌트 명세 | 시각 자료의 일관성 확보 |
| `source-graph-search` | Markdown source graph 검색 | 연결된 문서 탐색 |
| `ui-styling` | Tailwind·shadcn 기반 UI 구현 | HTML 대시보드 제작 |
| `ui-ux-pro-max` | 팔레트·폰트·차트·UX 가이드 | 대시보드와 UI 품질 개선 |
| `vibe-planning` | 아이디어를 PRD·MVP·작업 명세로 변환 | 다음 PiAgent thin slice 기획 |

Workspace 전용 `markdown-manager`, `markdown-writer`는 Central에 없지만 문서 작업에 직접 유용하다. 향후 Central에 보내 문서 전용 그룹으로 관리하는 편이 `vibe-planning` 전체 17개를 이동하는 것보다 좁고 명확하다.

## UI/UX 그룹

`ui-ux-skill-group`의 7개 스킬은 모두 `same` 상태다.

- `banner-design`
- `brand`
- `design`
- `design-system`
- `slides`
- `ui-styling`
- `ui-ux-pro-max`

이 그룹은 HTML 대시보드, 프레젠테이션, 배너 등 시각 결과물이 명시된 작업에만 적용하는 것이 적절하다. 일반 런타임 수정에는 필요하지 않다.

## 변경·위험 상태

### skill-manager

Central과 Workspace의 `skill-manager`는 `changed` 상태다. Workspace에는 Central에 없는 `references/selection.md`가 있으며 `SKILL.md`, 진단·복사·그룹 갱신 스크립트도 서로 다르다.

따라서 Central 그룹을 그대로 가져오면 현재 Workspace 기능이 후퇴할 수 있다. `Skill Bridge: Review Sync Changes`에서 diff를 검토하기 전에는 덮어쓰지 않는다.

### Central 전용 스킬

| 스킬 | 상태 | 판정 |
| --- | --- | --- |
| `skill-bridge-manager` | 6개 파일, 현재 `skill-manager`와 역할 중복 | 설치 불필요 |
| `skill-generator` | 1개 파일, description이 placeholder 템플릿 상태 | `risk`, 설치 보류 |

## 권장 그룹 정리

현재 상태를 유지하면서 다음 구성이 가장 작다.

1. 런타임 개발: `lang-agent` 중 LangChain/LangGraph 관련 스킬
2. Markdown 문서: Workspace의 `markdown-utils`
3. 보고서·발표: `document-production-advisor`, `md-presentation-composer`, `slides`
4. 시각 UI: 필요할 때만 `ui-ux-skill-group`
5. 한국어 최종 윤문: `korean-writer-skills`

즉시 Central에서 가져올 파일은 없다. 다음 실제 Skill Bridge 작업 후보는 Workspace의 `markdown-utils`를 Central로 보내고, 문서 전용 Central 그룹을 만드는 것이다. 이 작업은 Central 변경이므로 별도 승인 후 수행한다.
