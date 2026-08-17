---
title: PiAgent 빠른 시작
theme: report
intent: tutorial
toc: true
---

# PiAgent 빠른 시작

이 문서는 Docker Compose와 PowerShell로 PiAgent를 처음 실행하고, 프로젝트 규칙을 적용해 첫 작업을 맡기는 가장 짧은 경로입니다. 세부 옵션은 [설정 가이드](CONFIGURATION.kr.md), 설치 방법과 문제 해결은 [초기 설정과 사용 가이드](GETTING_STARTED.kr.md)에서 확인하세요.

## 시작 전 확인

- Docker Desktop과 Docker Compose v2가 설치되어 있어야 합니다.
- OpenAI API 키 또는 Local Bedrock 연결 정보가 필요합니다.
- 아래 예시는 Windows PowerShell 기준입니다.
- `.env`에는 비밀정보가 들어가므로 Git에 커밋하지 않습니다.

## 1. 모델 연결 설정

프로젝트 루트에서 예시 파일을 복사합니다.

```powershell
Copy-Item .env.example .env
```

`.env`를 열어 한 가지 모델 경로를 설정합니다. OpenAI 예시:

```dotenv
OPENAI_API_KEY=<OpenAI API 키>
PI_MODEL=gpt-4o-mini
```

Local Bedrock을 쓸 때는 `LOCAL_BEDROCK_BASE_URL`, `LOCAL_BEDROCK_MODEL_ID`, `LOCAL_BEDROCK_API_KEY`를 모두 설정합니다. 키 값은 채팅, 로그, 문서, Git에 넣지 마세요.

## 2. 컨테이너 시작과 진단

```powershell
docker compose build
docker compose up -d
docker compose exec pi_agent python simple_piagent.py --check
```

`--check`는 모델을 호출하지 않고 도구·스킬·모델 연결 경로만 확인합니다. `status: "ok"`가 보이면 다음 단계로 진행합니다.

## 3. 프로젝트 공통 규칙 만들기

선택 사항입니다. 프로젝트마다 항상 지켜야 할 규칙이 있다면, 프로젝트 루트에 `.piagent/INSTRUCTIONS.md`를 만들고 UTF-8로 저장합니다.

```text
.piagent/
└─ INSTRUCTIONS.md
```

예시:

```markdown
# 프로젝트 공통 규칙

- 항상 한국어로 답변하세요.
- 중요한 결론을 먼저 말하세요.
- 검증하지 않은 내용은 추정이라고 표시하세요.
```

파일이 없거나 비어 있으면 아무 규칙도 추가되지 않습니다. 파일이 있으면 매 요청의 시스템 프롬프트에 추가됩니다. 이 파일은 최대 64KB이며, 권한·워크스페이스 경계 같은 런타임 안전 정책을 완화할 수 없습니다. 자세한 동작은 [프로젝트 공통 규칙 설정](CONFIGURATION.kr.md#1-프로젝트-공통-규칙)을 참고하세요.

## 4. 채팅 시작

새 파일 작성이나 테스트 실행까지 맡길 때는 `full` 모드가 필요합니다.

```powershell
docker compose exec pi_agent python chat.py `
  --workspace /app --session first-work --mode full --no-mcp
```

채팅이 열리면 다음처럼 요청합니다.

```text
현재 프로젝트 구조를 읽고, README에 없는 실행 방법이 있으면 알려줘.
```

```text
요청한 기능의 모듈과 테스트 파일을 만들고, 테스트를 실행한 뒤 변경 파일과 결과를 한국어로 요약해줘.
```

Docker Compose는 현재 폴더를 컨테이너의 `/app`에 연결합니다. 따라서 `full` 모드에서 생성·수정한 파일은 호스트 프로젝트에도 즉시 반영됩니다.

## 5. 권한 모드 선택

| 요청 목적 | 실행 모드 | 할 수 있는 일 |
| --- | --- | --- |
| 구조 파악, 코드 리뷰, 구현 계획 | `review` | 읽기·검색·계획만 가능 |
| 기존 파일 한 곳의 작은 수정 | `edit` | 지정한 기존 파일에서 한 번의 치환만 가능 |
| 새 파일 생성, 여러 파일 수정, 테스트 실행 | `full` | 설정된 전체 도구 사용 |

`review`는 내부적으로 Plan 모드를 강제하므로, “코드를 작성해서 저장해줘”라고 요청해도 계획만 반환합니다. `edit`는 새 파일을 만들 수 없습니다. 상세 제약과 명령은 [권한 모드 설정](CONFIGURATION.kr.md#2-권한-모드)을 참고하세요.

## 다음 단계

- 프로젝트 공통 규칙, 세션, 사용자 격리, 스킬, 메모리, MCP 설정: [설정 가이드](CONFIGURATION.kr.md)
- 로컬 Python 설치, Local Bedrock, 확장 도구, 문제 해결: [초기 설정과 사용 가이드](GETTING_STARTED.kr.md)
- 채팅 명령과 현재 지원 범위: [대화형 에이전트 사용법](AGENT_LEVEL_AND_CHAT.kr.md)
