---
title: PiAgent 초기 설정과 사용 가이드
theme: tutorial
intent: onboarding
toc: true
---

# PiAgent 초기 설정과 사용 가이드

이 문서는 Windows 개발자가 PiAgent를 처음 설치해 다음 흐름을 재현하는 데 필요한 내용을 정리합니다. Docker로 가장 짧게 시작하려면 [빠른 시작](QUICKSTART.kr.md), 프로젝트 규칙·권한·스킬 등 설정 항목을 찾아보려면 [설정 가이드](CONFIGURATION.kr.md)를 먼저 보세요.

1. Python 또는 Docker로 실행 환경을 준비합니다.
2. OpenAI 또는 Local Bedrock 모델을 연결합니다.
3. 진단 명령으로 도구와 스킬이 로드됐는지 확인합니다.
4. 질문, 코드 분석, 파일 편집, 보고서 생성을 실행합니다.
5. 필요하면 `.piagent/skills`와 `.piagent/tools`로 기능을 확장합니다.

> 보안 주의: `.env`와 API 키를 프롬프트, 보고서, 로그, Git 커밋에 넣지 마세요. PiAgent의 일반 파일 도구는 기본적으로 `.env` 읽기를 차단합니다.

## 1. 어떤 실행기를 사용할까?

| 실행기 | 권장 용도 | 주요 특성 |
| --- | --- | --- |
| `simple_piagent.py` | 첫 실행, 일상 질문, 작은 코드 작업, 스킬·도구 실험 | 기본 도구와 스킬, 해시 메모리, MCP 비활성 |
| `openclaw_pi_langchain.py` | 사용자 격리, 세부 권한, Plan 모드, MCP 설정 | 전체 CLI 옵션 제공 |
| `chat.py` | 같은 세션으로 여러 차례 대화 | `/mode`, `/skills`, `/skill`, `/plan`, `/status` 지원 |

권장 시작점은 `simple_piagent.py`입니다. 세부 정책이 필요해지면 전체 실행기로 전환해도 같은 `piagent/` 런타임을 사용합니다.

현재 저장소의 진단 기준으로 기본 도구 31개와 번들 스킬 5개가 로드됩니다. 실제 개수는 확장과 설정에 따라 달라질 수 있으므로 설치 후 `--check`로 확인하세요.

## 2. 준비 사항

로컬 방식은 Python 3.12, Git, PowerShell이 필요합니다. Docker 방식은 Docker Desktop과 Docker Compose v2가 필요합니다. 두 방식 모두 OpenAI API 키 또는 Local Bedrock API 키가 필요합니다.

Docker 이미지도 Python 3.12를 사용합니다. 로컬 환경 차이를 줄이려면 Docker 방식을 선택하세요.

## 3. 로컬 Python 초기 설정

### 3.1 저장소로 이동

```powershell
Set-Location "C:\Users\leesu\Documents\ProjectCode\01_2026_EXP\PiAgent"
```

다른 위치에 설치했다면 해당 저장소 경로로 바꾸세요.

### 3.2 가상환경 생성과 활성화

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

### 3.3 패키지 설치

개발과 전체 테스트까지 포함한 권장 설치:

```powershell
python -m pip install -r requirements-piagent.lock.txt
```

`simple_piagent.py` 실행에 필요한 최소 패키지만 설치하려면:

```powershell
python -m pip install -r requirements-piagent-minimal.txt
```

최소 구성에는 개발 테스트와 선택 제공자 패키지가 포함되지 않습니다. 저장소 개발이나 전체 회귀 테스트가 목적이면 lock 파일을 사용하세요.

## 4. 모델 연결

먼저 예시 파일을 복사합니다.

```powershell
Copy-Item .env.example .env
```

`.env`에서 OpenAI와 Local Bedrock 중 한 가지 방식을 설정합니다. 세 Local Bedrock 변수가 모두 있으면 PiAgent는 Local Bedrock 경로를 우선 사용합니다.

### 선택 A: OpenAI

```dotenv
OPENAI_API_KEY=<OpenAI API 키>
PI_MODEL=gpt-4o-mini
```

### 선택 B: AWS Local Bedrock의 OpenAI 호환 API

```dotenv
OPENAI_API_KEY=
LOCAL_BEDROCK_BASE_URL=https://bedrock-runtime.ap-northeast-1.amazonaws.com
LOCAL_BEDROCK_MODEL_ID=openai.gpt-oss-120b-1:0
LOCAL_BEDROCK_API_KEY=<Bedrock API 키>
```

Local Bedrock 설정 규칙:

- 세 변수를 모두 설정해야 합니다.
- endpoint는 `https://bedrock-runtime.<region>.amazonaws.com` 형식이어야 합니다.
- root endpoint를 설정하면 PiAgent가 `/openai/v1`을 자동으로 붙입니다.
- Local Bedrock에서는 `LOCAL_BEDROCK_MODEL_ID`가 실제 모델을 결정합니다.
- 키를 `.env.example`, 코드, 테스트 fixture에 저장하지 마세요.

## 5. 설치 진단과 첫 실행

### 5.1 모델 호출 없는 진단

```powershell
python simple_piagent.py --check
```

정상이라면 JSON에 다음 항목이 표시됩니다.

- `status: "ok"`
- `model_route: "openai"` 또는 `"local-bedrock"`
- `tool_count`와 `tools`
- 발견된 `skills`
- 확장과 패키지 설치 활성화 여부

이 명령은 모델에 질문을 보내거나 키 값을 출력하지 않습니다.

### 5.2 첫 질문

```powershell
python simple_piagent.py "현재 워크스페이스 구조를 읽고 핵심 모듈을 설명해줘"
```

한국어가 Windows 셸 인코딩 때문에 깨진다면 UTF-8 프롬프트 파일을 사용하세요.

```powershell
python simple_piagent.py --prompt-file prompts\first-task.ko.txt
```

프롬프트 파일은 워크스페이스 안에 있어야 하고 UTF-8이어야 하며 최대 1MB입니다.

### 5.3 대화형 실행

```powershell
python chat.py --workspace . --session chat-main --mode review --no-mcp
```

모델을 호출하지 않고 설정, 도구, 스킬만 먼저 확인하려면:

```powershell
python chat.py --check --workspace . --session chat-main --no-mcp
```

채팅 명령:

- `/status`: 현재 세션·스킬·Plan 상태
- `/mode review`: 읽기·검색·계획만 허용
- `/mode edit <path1,path2>`: 지정한 기존 파일만 부분 수정 허용
- `/mode full`: 설정된 전체 도구 허용. 격리 환경에서만 권장
- `/skills`: 사용할 수 있는 스킬 목록
- `/tools`: 활성 도구 목록
- `/skill <name>`: 특정 스킬 고정
- `/skill auto`, `/skill off`: 자동 선택 또는 스킬 중지
- `/plan on`, `/plan off`: Plan 모드 전환
- `/session <name>`: 다른 영속 세션으로 전환
- `/last`: 현재 세션의 직전 답변 표시
- `exit`, `quit`, `:q`: 종료

CLI 옵션, 단발 실행, 실제 세션 기억 결과와 현재 에이전트 성숙도는 [PiAgent 현재 수준과 대화형 에이전트 사용법](AGENT_LEVEL_AND_CHAT.kr.md)을 참고하세요.

## 6. 기본 사용법

### 코드 읽기와 분석

```powershell
python simple_piagent.py "piagent에서 도구가 등록되는 흐름을 읽고 관련 파일과 책임을 정리해줘"
```

### 코드 수정과 테스트

```powershell
python simple_piagent.py --mode edit --edit-path piagent/session.py `
  "session.py의 실패 원인을 찾아 지정 파일을 최소 수정해줘"
```

기본 `review` 모드는 읽기와 계획만 허용합니다. `edit` 모드는 지정한 기존 파일에서 단일 원자적 치환만 허용하고, 새 파일 작성·일괄 치환·셸 실행은 차단합니다. 새 파일 생성이나 테스트 실행까지 맡길 때만 격리 환경에서 `--mode full`을 사용하세요.

### 읽기 전용 계획

```powershell
python openclaw_pi_langchain.py `
  "이 기능을 수정하기 전에 구조와 구현 계획만 정리해줘" `
  --plan-mode on `
  --permission-mode plan
```

Plan 모드에서는 파일 편집과 명령 실행 같은 변경 도구가 차단되고 계획 기록용 도구만 허용됩니다.

### 사용자와 세션 분리

```powershell
python openclaw_pi_langchain.py `
  "sample/data.csv를 분석해 reports/summary.md로 저장해줘" `
  --user-id alice `
  --session analysis-01
```

- `--workspace`: 에이전트가 읽고 작업할 루트
- `--user-id`: 사용자별 artifact, session, audit, memory 격리
- `--session`: 이어서 사용할 대화 히스토리 이름

사용자 격리 모드의 출력은 기본적으로 `artifacts/users/<user-id>/` 아래로 이동합니다.

## 7. 스킬 사용

스킬은 특정 문제를 처리할 절차와 규칙을 담은 `SKILL.md`입니다.

### 7.1 번들 스킬

```text
skills/
└─ code-health-check/
   ├─ SKILL.md
   └─ scripts/
```

자동 선택:

```powershell
python simple_piagent.py "코드 상태를 점검하고 한국어 보고서를 reports/code-health.md에 저장해줘"
```

명시적 선택:

```powershell
python simple_piagent.py `
  "코드 상태 보고서를 작성해줘" `
  --skill code-health-check `
  --session code-health-demo
```

### 7.2 회사 규칙과 reference

큰 정책 문서는 선택된 스킬의 `references/`에 분리할 수 있습니다.

```text
skills/
└─ naru-git-workflow/
   ├─ SKILL.md
   └─ references/
      └─ git-policy.md
```

```powershell
python simple_piagent.py `
  "NaruWorks 규칙에 따라 버그 수정 브랜치명, 커밋 제목, PR 체크리스트를 작성해줘" `
  --skill naru-git-workflow
```

현재 `naru-git-workflow`와 `naru-ui-design-review`는 가상 회사 규칙을 적용하는 평가용 예시입니다. 실제 조직에서는 승인한 정책으로 reference를 교체하고 회귀 테스트를 추가하세요.

Python 코딩 가이드는 `naru-python-coding-guide` 예시로 확인할 수 있습니다.

```powershell
python simple_piagent.py `
  "회사 코딩 가이드에 따라 이 Python 코드를 리뷰해줘" `
  --skill naru-python-coding-guide
```

정책 문답·코드 리뷰·구현을 GPT-OSS-120B로 실제 실행한 결과는 [회사 코딩 가이드 실실행 평가](NARU_PYTHON_CODING_GUIDE_EVAL.kr.md)를 참고하세요.

## 8. 워크스페이스 확장

프로젝트별 확장은 해당 워크스페이스의 `.piagent` 아래에 둡니다.

```text
.piagent/
├─ skills/
│  └─ news-research-report/
│     └─ SKILL.md
├─ tools/
│  └─ word-report/
│     └─ tool.py
└─ packages/
```

이 기능은 임의 Python 코드를 import할 수 있으므로 기본적으로 꺼져 있습니다. 신뢰하는 워크스페이스에서만 활성화하세요.

`.env` 설정:

```dotenv
PI_WORKSPACE_EXTENSIONS_ENABLED=true
PI_WORKSPACE_EXTENSION_DIR=.piagent
```

또는 전체 CLI에서 한 번만 활성화:

```powershell
python openclaw_pi_langchain.py "확장 도구를 확인해줘" --workspace-extensions
```

활성화 확인:

```powershell
python simple_piagent.py --check
```

### 8.1 최소 `SKILL.md`

```markdown
---
name: project-release-review
description: 이 프로젝트의 배포 계획과 체크리스트를 검토할 때 사용한다.
---

# Project Release Review

1. `references/release-policy.md`를 먼저 읽는다.
2. 확인하지 않은 테스트는 통과했다고 쓰지 않는다.
3. 결과에 정책 ID와 남은 위험을 포함한다.
```

### 8.2 최소 `tool.py`

도구 하나마다 폴더를 만들고 그 안에 `tool.py`를 둡니다.

```python
from langchain.tools import tool


@tool
def stock_symbol(value: str) -> str:
    """Normalize a stock ticker symbol."""
    return value.strip().upper()


TOOLS = [stock_symbol]
```

도구 규칙:

- `.piagent/tools/<도구-폴더>/tool.py` 형식을 사용합니다.
- 폴더명은 소문자, 숫자, 하이픈만 사용합니다.
- `TOOLS` 또는 tool 목록을 반환하는 `get_tools()`를 제공해야 합니다.
- 내장 도구와 이름이 충돌하면 시작에 실패합니다.
- 외부 패키지 import는 가능하면 도구 함수 안에서 수행합니다.
- 신뢰하지 않는 저장소에서는 로딩하지 않습니다.

### 8.3 프로젝트 공통 지침 (`INSTRUCTIONS.md`)

프로젝트에서 항상 지켜야 할 규칙은 `.piagent/INSTRUCTIONS.md`에 UTF-8 Markdown으로 작성할 수 있습니다. 파일이 없거나 비어 있으면 기존 동작은 변하지 않습니다. 파일이 있으면 매 요청의 시스템 프롬프트에 추가되며, workspace 경계와 권한 같은 PiAgent의 안전 정책을 완화할 수는 없습니다.

```markdown
# 프로젝트 응답 규칙

- 항상 한국어로 답변하세요.
- 중요한 결론을 먼저 말하고, 검증하지 않은 내용은 추정이라고 표시하세요.
```

지침 파일은 최대 64KB입니다. 프로젝트의 신뢰할 수 있는 규칙만 넣고, API 키나 비밀번호 같은 비밀정보는 넣지 마세요. 적용 우선순위와 권한 모드별 사용법은 [설정 가이드](CONFIGURATION.kr.md#1-프로젝트-공통-규칙)를 참고하세요.

## 9. 작업 중 Python 패키지 설치

`python_package_install`은 기본적으로 비활성화되어 있고, 허용 목록에 있는 PyPI 패키지만 `.piagent/packages`에 설치합니다.

```dotenv
PI_ALLOW_PACKAGE_INSTALL=true
PI_PACKAGE_INSTALL_ALLOWLIST=python-docx==1.2.0,matplotlib==3.11.1
PI_PACKAGE_INSTALL_TIMEOUT=180
```

운영 원칙:

- 가능한 한 정확한 버전을 고정합니다.
- URL, 로컬 경로, 임의 pip 옵션, 허용되지 않은 패키지는 차단됩니다.
- Plan 모드에서는 설치 도구가 노출되지 않습니다.
- 설치는 `.piagent/packages`에만 적용되며 시스템 Python을 변경하지 않습니다.
- 새 패키지를 자동 허용하지 말고 검토 후 allowlist에 추가하세요.

## 10. 세션과 메모리

같은 `--session` 값을 사용하면 이전 대화 히스토리를 이어갑니다. 각 실행의 사용자 요청과 최종 답변은 검색 가능한 세션 조각에도 저장됩니다.

- `session_fragment_search`: 키워드로 이전 대화 조각 검색
- `session_fragment_get`: 검색된 ID의 원문 조회

장기 선호나 결정은 `memory_search`, `memory_get`, `memory_store`로 관리합니다. `simple_piagent.py`는 `.openclaw_pi/simple/memory` 아래의 해시 검색을 사용합니다. 전체 실행기의 기본 OpenClaw 메모리는 관리된 메모리 도구로만 접근하며 일반 파일 읽기는 차단됩니다.

예시 요청:

```text
이 세션에서 ORCHID 프로젝트 출력 형식을 먼저 검색하고, 필요한 기억 조각을 읽은 뒤 같은 형식으로 작성해줘.
```

## 11. Docker Compose 초기 설정

Compose는 저장소를 `/app`에 마운트하고 `.env`를 컨테이너 환경변수로 주입합니다.

### 11.1 빌드와 시작

```powershell
docker compose up -d --build
```

### 11.2 컨테이너 안에서 진단

```powershell
docker compose exec pi_agent python simple_piagent.py --check --workspace /app
```

### 11.3 실제 질문

```powershell
docker compose exec pi_agent python simple_piagent.py `
  --workspace /app `
  --session docker-demo `
  "현재 프로젝트 구조와 실행 방법을 요약해줘"
```

워크스페이스 확장을 켠 일회성 진단:

```powershell
docker compose run --rm `
  -e PI_WORKSPACE_EXTENSIONS_ENABLED=true `
  pi_agent python simple_piagent.py --check --workspace /app
```

### 11.4 Docker 저장소 회귀 테스트

호스트 `.env`와 바인드 마운트를 제외하고 이미지에 복사된 `tests/`를 실행하는 테스트 전용 구성:

```powershell
docker compose --env-file .env.example `
  -f docker-compose.yml -f docker-compose.test.yml `
  up --build --abort-on-container-exit --exit-code-from pi_agent
```

이 구성은 실제 모델을 호출하지 않고 테스트용 가짜 키를 사용합니다.

## 12. 검증 명령

```powershell
python simple_piagent.py --check
python openclaw_pi_langchain.py --help
python -m pytest tests -q
```

핵심 런타임만 좁게 검증하려면:

```powershell
python -m pytest tests/test_piagent_core.py tests/test_simple_piagent.py -q
```

`python -m pytest -q`처럼 경로를 생략하면 `artifacts/` 아래에 남아 있는 별도 평가용 테스트까지 수집될 수 있습니다. 저장소의 관리 대상 회귀 테스트만 확인할 때는 `tests` 경로를 명시하세요.

## 13. 문제 해결

### `set OPENAI_API_KEY or all LOCAL_BEDROCK_* variables`

모델 설정이 없거나 Local Bedrock 세 변수 중 일부가 빠졌습니다. `.env`의 변수 이름과 빈 값을 확인하세요.

### `Local Bedrock configuration is incomplete`

`LOCAL_BEDROCK_BASE_URL`, `LOCAL_BEDROCK_MODEL_ID`, `LOCAL_BEDROCK_API_KEY`를 모두 설정해야 합니다.

### Bedrock endpoint 검증 실패

HTTPS Amazon Bedrock Runtime endpoint의 root 또는 `/openai/v1` 경로만 사용하세요. 일반 웹 주소나 임의 프록시는 허용되지 않습니다.

### 스킬 또는 `.piagent` 도구가 보이지 않음

1. `python simple_piagent.py --check`로 현재 목록을 확인합니다.
2. 기본 스킬은 `skills/<name>/SKILL.md`에 있는지 확인합니다.
3. 확장은 `.piagent/skills` 또는 `.piagent/tools`에 있는지 확인합니다.
4. 확장이라면 `PI_WORKSPACE_EXTENSIONS_ENABLED=true`인지 확인합니다.
5. 도구 폴더명과 `TOOLS` 또는 `get_tools()` 계약을 확인합니다.

### 패키지 설치가 거부됨

`PI_ALLOW_PACKAGE_INSTALL=true`와 exact pin을 포함한 `PI_PACKAGE_INSTALL_ALLOWLIST`를 확인하세요. 허용 목록에 없는 패키지는 의도적으로 설치되지 않습니다.

### Docker에서 수정 사항이 안 보임

`docker compose exec pi_agent pwd` 결과가 `/app`인지 확인하고, 의존성이나 Dockerfile을 바꿨다면 `docker compose up -d --build`로 다시 빌드하세요.

### 같은 명령이 반복되어 차단됨

PiAgent는 동일 도구 반복과 반복 실패를 감지합니다. 감사 로그와 직전 도구 결과를 확인한 뒤 원인을 수정해 다시 실행하세요.

## 14. 현재 한계

- 작은 코드 수정과 테스트 실행은 가능하지만, 큰 변경은 작업을 나누고 사람이 diff를 검토해야 합니다.
- 공개 웹 검색은 검색 제공자와 페이지 접근성에 영향을 받습니다.
- 최신 뉴스 분석은 assisted research 수준이며, deep research 완료를 완전히 보장하지 않습니다.
- DOCX 구조 생성은 가능하지만 LibreOffice 기반 페이지 렌더·육안 검수가 기본 이미지에 포함되지는 않습니다.
- 모델이 도구를 고르므로 스킬 지시만으로 모든 다단계 절차 완료가 항상 보장되지는 않습니다.
- Git·회사 정책·보안처럼 중요한 결과는 실행 증거와 정책 인용을 사람이 확인해야 합니다.

기존 기능별 평가와 우선순위는 [PiAgent GPT-OSS-120B 기능 평가 보고서](PIAGENT_CAPABILITY_REPORT.kr.md)를 참고하세요. 난이도별 20개 질문을 동일 모델로 실제 수행한 최신 결과는 [20개 과제 실실행 평가](AGENT_CAPABILITY_20_RESULT.kr.md)에 정리했습니다.

## 15. 초기 설정 완료 체크리스트

- [ ] Python 3.12 가상환경 또는 Docker 이미지를 준비했다.
- [ ] lock 파일 또는 최소 requirements로 패키지를 설치했다.
- [ ] `.env`에 한 가지 모델 경로를 설정했다.
- [ ] `.env`가 Git 추적 대상이 아님을 확인했다.
- [ ] `python simple_piagent.py --check`가 `status: ok`를 반환했다.
- [ ] `model_route`가 의도한 제공자를 가리킨다.
- [ ] 첫 질문을 실행하고 최종 답변과 audit 경로를 확인했다.
- [ ] 코드 변경 전에는 Plan 모드 또는 좁은 작업 범위를 사용한다.
- [ ] `.piagent` 확장은 신뢰하는 워크스페이스에서만 활성화한다.
- [ ] 패키지 allowlist에는 검토한 exact pin만 넣었다.
- [ ] 중요한 보고서와 코드 변경은 테스트와 사람 검토를 거친다.
