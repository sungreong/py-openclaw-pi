---
title: PiAgent 빠른 시작
theme: report
intent: tutorial
toc: true
---

# PiAgent 빠른 시작

이 문서는 Docker Compose 런처(`piagent.ps1` 또는 `piagent.sh`)로 PiAgent를 처음 실행하고, 프로젝트 규칙을 적용해 첫 작업을 맡기는 가장 짧은 경로입니다. 세부 옵션은 [설정 가이드](CONFIGURATION.kr.md), 설치 방법과 문제 해결은 [초기 설정과 사용 가이드](GETTING_STARTED.kr.md)에서 확인하세요.

## 시작 전 확인

- Docker Desktop과 Docker Compose v2가 설치되어 있어야 합니다.
- OpenAI API 키 또는 Local Bedrock 연결 정보가 필요합니다.
- Windows에서는 `piagent.ps1`, Git Bash·WSL·macOS·Linux에서는 `piagent.sh`를 사용합니다.
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

## 2. 실행 위치에 맞게 시작과 진단

호스트와 컨테이너 내부의 역할은 다릅니다. 호스트 스크립트는 Docker Compose로 컨테이너를 준비하고, 컨테이너 내부 스크립트는 Python을 바로 실행합니다. 두 위치에서 같은 `piagent.sh`를 실행해도 자동으로 알맞은 동작을 선택합니다.

### 호스트에서 실행 (권장)

Windows PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\piagent.ps1 -Check
```

Git Bash·WSL·macOS·Linux shell:

```sh
./piagent.sh --check
```

호스트 스크립트는 Docker 컨테이너를 준비합니다. `--check`에서는 모델을 호출하지 않고 도구·스킬·모델 연결 경로만 확인합니다. `.env`가 없으면 `.env.example`을 복사한 뒤 멈추므로, 키를 입력하고 같은 명령을 다시 실행하세요. `status: "ok"`가 보이면 다음 단계로 진행합니다.

MCP는 선택 기능입니다. 사용할 때만 예시를 개인 설정 파일로 복사하고 필요한 서버만 `enabled: true`로 바꾸세요. 이 설정에는 명령과 환경변수가 들어갈 수 있으므로 Git에 올리지 않습니다.

```powershell
Copy-Item examples/mcp_servers.example.json mcp_servers.json
.\piagent.ps1 -Mcp
```

### 이미 컨테이너 안에 있을 때

먼저 컨테이너에 접속합니다.

```sh
docker compose exec pi_agent bash
```

프롬프트가 `root@...:/app#`처럼 바뀐 뒤에는 Docker를 다시 실행하지 않습니다. 같은 명령이 Python을 직접 실행합니다.

```sh
./piagent.sh --check
./piagent.sh --full --session first-work
```

컨테이너 내부에서는 `chmod +x ./piagent.sh`가 필요 없습니다. 실행 권한이 보존되지 않은 환경에서는 `sh ./piagent.sh --check`로 실행할 수 있습니다.

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
.\piagent.ps1 -Mode full -Session first-work
```

Git Bash·WSL·macOS·Linux에서는 다음처럼 실행합니다.

```sh
./piagent.sh --full --session first-work
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

자주 쓰는 실행 예시:

```powershell
.\piagent.ps1                                      # 안전한 분석·계획 채팅
.\piagent.ps1 -Mode full                           # 새 파일·테스트가 필요한 구현
.\piagent.ps1 -Mode edit -EditPath piagent/session.py
.\piagent.ps1 -Mcp                                 # 설정된 MCP를 함께 사용
```

```sh
./piagent.sh                         # POSIX shell의 안전한 분석·계획 채팅
./piagent.sh --full                  # 새 파일·테스트가 필요한 구현
./piagent.sh --edit piagent/session.py
./piagent.sh --mcp                   # 설정된 MCP를 함께 사용
```

## 다음 단계

- 프로젝트 공통 규칙, 세션, 사용자 격리, 스킬, 메모리, MCP 설정: [설정 가이드](CONFIGURATION.kr.md)
- 로컬 Python 설치, Local Bedrock, 확장 도구, 문제 해결: [초기 설정과 사용 가이드](GETTING_STARTED.kr.md)
- 채팅 명령과 현재 지원 범위: [대화형 에이전트 사용법](AGENT_LEVEL_AND_CHAT.kr.md)
