# OpenClaw Pi (LangChain) - 한국어 가이드

OpenClaw에서 영감을 받은 최소 코딩 에이전트 런타임입니다.

## 문서 시작점

| 하고 싶은 일 | 먼저 볼 문서 |
| --- | --- |
| Docker로 첫 실행하고 첫 작업 맡기기 | [빠른 시작](docs/QUICKSTART.kr.md) |
| 프로젝트 규칙·권한·세션·스킬·MCP 설정 | [설정 가이드](docs/CONFIGURATION.kr.md) |
| 로컬 Python 설치, Local Bedrock, 전체 옵션, 문제 해결 | [초기 설정과 사용 가이드](docs/GETTING_STARTED.kr.md) |
| 채팅 명령과 현재 지원 범위 확인 | [대화형 에이전트 사용법](docs/AGENT_LEVEL_AND_CHAT.kr.md) |

처음에는 빠른 시작을 완료한 뒤, 필요한 설정만 설정 가이드에서 추가하세요.

Docker Compose를 사용한다면 Windows PowerShell에서는 `.\piagent.ps1`, Git Bash·WSL·macOS·Linux에서는 `./piagent.sh`로 안전한 `review` 채팅을 바로 열 수 있습니다. 이 명령은 호스트에서는 Compose를 준비하고, `root@...:/app#`처럼 이미 `pi_agent` 컨테이너 안에서는 Python을 직접 실행합니다. 구현 작업은 `-Mode full` 또는 `--full`, 설정 확인은 `-Check` 또는 `--check`를 사용하세요. 자세한 구분은 [빠른 시작의 실행 위치 안내](docs/QUICKSTART.kr.md#2-실행-위치에-맞게-시작과-진단)를 참고하세요.

## 명령 빠른 참조

1. `.env.example` 파일을 `.env`로 복사합니다.
2. `OPENAI_API_KEY` 또는 Local Bedrock 환경변수 3개를 설정합니다.
3. 필요하면 `PI_READ_STRATEGY` 값을 조정합니다. (`smart` 또는 `legacy`)
4. 실행:
   - 분석/계획: `python openclaw_pi_langchain.py --mode review "요청 프롬프트"`
   - 지정 파일 부분 수정: `python openclaw_pi_langchain.py --mode edit --edit-path piagent/session.py "요청 프롬프트"`
   - 전체 작업(격리 환경 권장): `python openclaw_pi_langchain.py --mode full "요청 프롬프트"`
   - Chat 진단: `python chat.py --check --workspace . --no-mcp`
   - Chat: `python chat.py --workspace . --session chat-main --mode review --no-mcp`

대화 스크립트의 전체 옵션과 현재 에이전트 수준은 [대화형 에이전트 사용법](docs/AGENT_LEVEL_AND_CHAT.kr.md)을 참고하세요. GPT-OSS-120B로 쉬운 작업부터 복합 작업까지 20개를 실제 실행한 결과는 [20개 과제 실실행 평가](docs/AGENT_CAPABILITY_20_RESULT.kr.md)에 있습니다.

### Codex에서 PiAgent를 하위 에이전트로 사용

한 PiAgent 프로세스를 유지하면서 JSONL 표준 입출력으로 여러 턴을 주고받을 수 있습니다. 세션 ID와 사용자 ID는 영문자, 숫자, `.`, `_`, `-`만 사용합니다.

```powershell
python -u scripts/piagent_subagent_chat.py --jsonl `
  --session codex-job-1 --user-id codex --mode review
```

일반 사용자는 세 가지 권한 모드만 선택하면 됩니다.

| 모드 | 허용 범위 | 권장 용도 |
|---|---|---|
| `review` (기본값) | 읽기·검색·계획만 허용 | 코드 분석, 구현 계획 검토 |
| `edit` | 지정한 기존 파일에서 `edit` 단일 치환만 허용 | 승인한 파일의 작은 부분 수정 |
| `full` | 설정된 전체 Tool 허용 | 격리된 환경의 자율 작업 |

부분 수정은 대상 파일을 실행 시 명시합니다. 이 모드에서는 `write`, `multiedit`, `exec`, 패키지 설치가 노출되지 않으며, 지정하지 않은 파일과 `replace_all`은 런타임에서 다시 차단됩니다.

```powershell
python -u scripts/piagent_subagent_chat.py --jsonl `
  --session codex-edit-1 --user-id codex --mode edit `
  --edit-path piagent/session.py
```

프로세스가 `ready`를 출력하면 한 줄씩 요청합니다.

```json
{"prompt":"이 작업의 확인 코드는 PEER-314입니다. 기억하세요."}
{"prompt":"앞선 확인 코드를 답하세요.","mode":"review","skill_mode":"off"}
{"prompt":"이 파일의 setter 한 곳만 수정하세요.","mode":"edit","paths":["piagent/session.py"]}
{"command":"state"}
{"command":"exit"}
```

요청별 `mode`와 `paths`가 기본 인터페이스입니다. 기존 `allowlist`, `denylist`, `skill_name`, `skill_mode`, `plan_mode`는 고급 제어와 하위 호환을 위해 유지됩니다. 상세 Tool 목록을 함께 지정해도 `mode`의 안전 상한을 넓힐 수는 없습니다.

10개 멀티턴 시나리오 목록과 실제 모델 평가는 다음 명령으로 실행합니다.

```powershell
python scripts/run_agent_multiturn_10.py --list
python scripts/run_agent_multiturn_10.py --run-id my-multiturn-run
```

평가는 같은 세션의 `prompt → follow_up`을 사용하며, 10개 시나리오에서 총 37턴을 실행합니다. 정의는 [`evaluation/agent_multiturn_10_scenarios.json`](evaluation/agent_multiturn_10_scenarios.json)에 있습니다.

## 고정 개발환경

Docker 개발환경은 `requirements-piagent.lock.txt`를 사용해 검증된 Python 패키지 버전을 설치합니다.
이미지 빌드는 lock 파일로 패키지 버전을 고정하고, `docker-compose.yml`의 `.env`는 런타임 환경변수 주입에만 사용합니다.

```powershell
docker compose build --no-cache
docker compose run --rm pi_agent python -m pytest tests/test_piagent_core.py tests/test_chat_ui.py tests/test_piagent_session.py
```

호스트 `.env`와 개발용 바인드 마운트를 제외한 격리 테스트는 전용 override를 사용합니다. 이 명령은 이미지 안에 복사된 소스로 전체 회귀 테스트를 실행합니다.

```powershell
docker compose --env-file .env.example `
  -f docker-compose.yml -f docker-compose.test.yml build pi_agent

docker compose --env-file .env.example `
  -f docker-compose.yml -f docker-compose.test.yml run --rm pi_agent
```

`docker-compose.test.yml`은 실제 모델을 호출하지 않는 테스트 전용 가짜 키를 사용합니다. 최신 주가, 차트 이미지, Word/DOCX 생성을 실행하려면 별도의 데이터 도구와 문서 생성 패키지가 이미지에 포함되어야 합니다.

로컬 Python 환경도 같은 버전으로 맞추려면:

```powershell
python -m pip install -r requirements-piagent.lock.txt
```

패키지를 의도적으로 업그레이드할 때는 `requirements-piagent.txt`와 lock 파일을 함께 갱신하고 전체 테스트를 실행합니다.

## 워크스페이스 확장 (`.piagent`)

신뢰하는 워크스페이스에서 확장 로딩을 켜면 PiAgent가 아래 구조를 자동 발견합니다.

```text
.piagent/
├─ skills/
│  └─ stock-report/
│     └─ SKILL.md
├─ tools/
│  └─ stock-price/
│     └─ tool.py
└─ packages/               # python_package_install 전용, 자동 생성
```

활성화:

```powershell
$env:PI_WORKSPACE_EXTENSIONS_ENABLED="true"
```

최소 `SKILL.md`:

```markdown
---
name: stock-report
description: 최근 주가 분석과 시각화 보고서를 요청할 때 사용한다.
---

검증된 시장 데이터만 사용하고 관측 날짜와 출처를 기록한다.
```

최소 `tool.py`:

```python
from langchain.tools import tool

@tool
def stock_symbol(value: str) -> str:
    """Normalize a stock ticker symbol."""
    return value.strip().upper()

TOOLS = [stock_symbol]
```

`tool.py`는 LangChain tool 객체 목록을 담은 `TOOLS` 또는 tool 목록을 반환하는 `get_tools()`를 제공해야 합니다. 도구 폴더명은 소문자·숫자·하이픈만 허용하며, 내장 도구 이름과 충돌하면 시작에 실패합니다. 임의 Python 코드가 import되므로 신뢰하지 않는 저장소에서는 확장 로딩을 켜지 마세요.

추가 패키지가 필요한 도구는 해당 import를 함수 안에서 수행하세요. 패키지가 없으면 `missing_dependency=<PyPI 이름>`을 반환하도록 만들면 모델이 `python_package_install`을 호출한 뒤 도구를 다시 실행할 수 있습니다. 모듈 최상단에서 누락된 외부 패키지를 import하면 에이전트 시작 단계에서 도구 로딩이 실패합니다.

### 워크스페이스 Python 패키지 설치

설치 도구는 기본적으로 꺼져 있고 allowlist를 반드시 요구합니다.

```powershell
$env:PI_ALLOW_PACKAGE_INSTALL="true"
$env:PI_PACKAGE_INSTALL_ALLOWLIST="python-docx==1.2.0,matplotlib==3.11.1,pandas==3.0.5,yfinance==1.6.0"
```

활성화 후 모델은 `python_package_install(package, import_name, version)`을 사용할 수 있습니다. 설치 위치는 `.piagent/packages`이며 `--index-url`, URL, 로컬 경로, extras와 임의 pip 옵션은 차단됩니다. 정확한 버전 pin 사용을 권장합니다. Plan 모드에서는 이 도구가 노출되지 않습니다.

## 자주 쓰는 실행 예시

### 사용자별 격리 실행 (`--user-id`)

`--user-id`를 주면 보고서, CSV, 이미지, 세션, 감사 로그, 메모리 저장소가 사용자별로 분리됩니다.

```powershell
python openclaw_pi_langchain.py "sample/data.csv 분석 보고서를 작성해줘" `
  --user-id alice
```

이 경우 일반 출력 파일은 기본적으로 아래 위치에 저장됩니다:

```text
artifacts/users/alice/...
artifacts/users/alice/workspace/...
```

채팅 모드에서는 환경변수로 고정하는 방식이 편합니다:

PowerShell:

```powershell
$env:PI_USER_ID="alice"
$env:PI_SESSION="alice-main"
python chat.py
```

Windows CMD:

```bat
set PI_USER_ID=alice
set PI_SESSION=alice-main
python chat.py
```

Linux/macOS shell:

```bash
export PI_USER_ID=alice
export PI_SESSION=alice-main
python chat.py
```

### 읽기 전용 계획 모드

코드를 바꾸기 전에 구조 분석과 개발 계획만 받고 싶을 때 사용합니다.

```powershell
python openclaw_pi_langchain.py "이 기능을 어떻게 고칠지 계획만 세워줘" `
  --plan-mode on `
  --permission-mode plan
```

Plan 모드에서는 `write`, `edit`, `multiedit`, `exec`, `exec_readonly`, `memory_store`, `work_note_update`가 차단됩니다. 대신 `plan_note_write`는 허용되어 계획 내용을 세션 작업 문서에 저장할 수 있습니다.

### 작업 문서(work note)

Pi는 복잡한 계획/구현 작업을 위해 구조화된 작업 문서를 유지합니다. `--user-id alice`를 사용하면 기본 경로는 `artifacts/users/alice/work-notes/<session>.md`입니다.

```powershell
python openclaw_pi_langchain.py "이 리팩터링을 계획하고 작업 문서에 남겨줘" `
  --user-id alice `
  --session alice-main `
  --plan-mode on
```

관련 도구:

- `work_note_read`: 현재 세션 작업 문서 읽기
- `work_note_update`: 일반 구현 모드에서 섹션 업데이트
- `work_note_search`: 이전 결정/에러/파일 정보를 작업 문서에서 검색
- `plan_note_write`: Plan 모드에서 계획 내용 저장

### 산출물/긴 출력 오프로드

긴 `read`, `grep`, `exec`, `web_fetch` 결과는 모델 컨텍스트에 전부 넣지 않고 artifact 파일로 저장됩니다.

```powershell
python openclaw_pi_langchain.py "큰 로그 파일에서 에러 원인을 찾아줘" `
  --user-id alice `
  --max-tool-result-chars 12000 `
  --tool-result-artifact-dir tool-results
```

모델에는 `full_result_path=artifacts/users/alice/tool-results/...` 같은 경로와 미리보기만 전달됩니다.

### 서브에이전트 사용

Pi는 내부 도구 `delegate_task`를 통해 `explore`, `plan`, `verify` 서브에이전트를 사용할 수 있습니다. 사용자는 자연어로 요청하면 됩니다.

```powershell
python openclaw_pi_langchain.py "서브에이전트로 먼저 구조를 탐색하고, 수정 계획과 검증 포인트를 정리해줘"
```

서브에이전트를 끄고 싶으면:

```powershell
python openclaw_pi_langchain.py "혼자서 분석해줘" --no-subagents
```

### 안전한 읽기 전용 명령 실행

모델은 `exec_readonly` 도구를 사용할 수 있으며, 명령이 읽기 전용으로 분류되지 않으면 실행하지 않습니다.

```powershell
python openclaw_pi_langchain.py "git status와 테스트 목록만 확인해줘"
```

위험하거나 상태를 바꾸는 명령은 `exec`에서도 정책으로 차단될 수 있습니다.

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

PowerShell:

```powershell
$env:PI_SESSION="analyst"; python chat.py
$env:PI_SESSION="runner"; python chat.py
$env:PI_SESSION="fixer"; python chat.py
```

Windows CMD:

```bat
set PI_SESSION=analyst
python chat.py
set PI_SESSION=runner
python chat.py
set PI_SESSION=fixer
python chat.py
```

Linux/macOS shell:

```bash
PI_SESSION=analyst python chat.py
PI_SESSION=runner python chat.py
PI_SESSION=fixer python chat.py
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

## 예시 스킬: 코드 상태 점검

`skills/code-health-check/SKILL.md`는 번들 Python 스캐너를 먼저 실행하고 관찰된 수치로 Markdown 코드 상태 보고서를 만드는 예시입니다. 프롬프트에 `code health check`가 포함되면 자동 선택되며 `--skill code-health-check`로 명시할 수도 있습니다.

```powershell
python simple_piagent.py `
  "Run a code health check and save a Korean report to reports/code-health.md" `
  --session code-health-demo
```

스캐너는 표준 라이브러리만 사용하며 `.env`, 의존성 디렉터리, 에이전트 상태와 private 경로를 제외합니다. 결과는 정적 파일 통계이므로 테스트 통과나 런타임 정확성을 증명하지 않습니다.

### 회사 규칙 reference 예시

- `naru-git-workflow`: `references/git-policy.md`를 읽어 회사 브랜치·커밋·PR 규칙을 적용
- `naru-ui-design-review`: `references/ui-policy.md`를 읽어 회사 디자인 토큰과 접근성 규칙으로 HTML/CSS 검토
- `naru-python-coding-guide`: `references/python-coding-guide.md`를 읽어 회사 Python 문답·리뷰·구현 규칙 적용

NaruWorks 규칙은 실제 회사 정보가 아닌 평가용 가상 정책입니다. 상세 규칙을 `SKILL.md`에 전부 넣지 않고 `references/`로 분리했기 때문에, 선택된 스킬만 필요한 회사 컨텍스트를 읽습니다. Python 코딩 가이드의 실제 모델 평가와 한계는 `docs/NARU_PYTHON_CODING_GUIDE_EVAL.kr.md`에 정리했습니다.

## 세션 기억 조각

각 실행이 끝나면 사용자 요청과 최종 답변을 900자 이하 조각으로 나누어 세션 폴더의 `<session>.fragments.jsonl`에 추가 저장합니다. 일반 세션 히스토리가 길어져 요약·교체되더라도 이 파일은 append-only로 유지됩니다. 도구 실행 결과나 내부 추론은 저장하지 않습니다.

- `session_fragment_search(query, session_id, limit, role)`: 키워드로 조각을 찾아 ID와 짧은 미리보기를 반환
- `session_fragment_get(ids, session_id)`: 검색 결과 ID로 원문 조각을 최대 20개 조회

모델에는 “먼저 검색하고 필요한 ID만 원문 조회”하도록 안내합니다. `--user-id`를 사용하면 세션 히스토리와 마찬가지로 사용자별 디렉터리에 격리됩니다. 이것은 같은 세션의 대화를 복원하는 기능이며, 세션을 넘는 장기 선호·사실 기억은 기존 `memory_search`/`memory_get`/`memory_store`를 사용합니다.

예시 요청:

```text
이전 대화에서 ORCHID 프로젝트의 출력 형식을 찾아줘. session_fragment_search로 찾고 필요한 조각은 session_fragment_get으로 확인해.
```

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

- `OPENAI_API_KEY` (아래 로컬 Bedrock 환경변수를 사용하지 않을 때 필수)
- `LOCAL_BEDROCK_BASE_URL` (선택, Bedrock Runtime endpoint; `/openai/v1`은 자동 추가)
- `LOCAL_BEDROCK_MODEL_ID` (`LOCAL_BEDROCK_BASE_URL`과 함께 필수)
- `LOCAL_BEDROCK_API_KEY` (`LOCAL_BEDROCK_BASE_URL`과 함께 필수, 소스 관리에 저장 금지)
- `PI_MODEL`
- `PI_WORKSPACE`
- `PI_SESSION`
- `PI_MODE` (`review|edit|full`, 기본 `review`)
- `PI_EDIT_PATHS` (`edit` 모드에서 허용할 기존 파일의 쉼표 구분 목록)
- `PI_USER_ID` (선택, 사용자별 아티팩트/상태 분리)
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
- `PI_PERMISSION_MODE` (`default|plan|accept_edits|dont_ask`, 기본 `default`)
- `PI_NO_SUBAGENTS` (`true`면 `delegate_task` 비활성화)
- `PI_MAX_TOOL_RESULT_CHARS` (기본 `24000`; 초과 결과는 artifact로 오프로드)
- `PI_TOOL_RESULT_ARTIFACT_DIR` (기본 `tool-results`)
- `PI_NO_SESSION_NOTES` (`true`면 세션 노트 비활성화)
- `PI_NO_WORK_NOTES` (`true`면 구조화된 작업 문서 비활성화)
- `PI_WORK_NOTE_ARTIFACT_DIR` (기본 `work-notes`)
- `PI_NO_WORK_NOTE_AUTO_UPDATE` (`true`면 실행 종료 후 자동 worklog 추가 비활성화)
- `PI_HOOKS_CONFIG` (기본 `pi_hooks.json`)

### 로컬 Bedrock Runtime

다음 세 환경변수를 모두 설정하면 메인 모델과 압축 모델이 Bedrock의 OpenAI 호환 Chat Completions API를 사용합니다.

```powershell
$env:LOCAL_BEDROCK_BASE_URL="https://bedrock-runtime.ap-northeast-1.amazonaws.com"
$env:LOCAL_BEDROCK_MODEL_ID="openai.gpt-oss-120b-1:0"
$env:LOCAL_BEDROCK_API_KEY="<Bedrock API 키>"
python openclaw_pi_langchain.py "안녕"
```

Pi는 HTTPS Bedrock Runtime 호스트를 검증하고 root endpoint가 들어오면 `/openai/v1`을 자동으로 추가합니다. API 키는 소스 관리에 커밋하지 마세요.

## 주요 CLI 옵션

- `--user-id <id>`: 사용자별 artifact/session/audit/memory 격리
- `--session <id>`: 대화 히스토리 세션 분리
- `--workspace <path>`: 작업 루트 지정
- `--mode review|edit|full`: 간소화된 권한 모드
- `--edit-path <file>`: `edit` 모드에서 수정 가능한 기존 파일(반복 가능)
- `--plan-mode on|off`: 읽기 전용 계획 모드
- `--permission-mode default|plan|accept_edits|dont_ask`: 권한 모드
- `--no-subagents`: `delegate_task` 비활성화
- `--max-tool-result-chars <n>`: tool result preview 한도
- `--tool-result-artifact-dir <path>`: 긴 tool result 저장 디렉터리 이름
- `--no-session-notes`: 세션 노트 저장 비활성화
- `--no-work-notes`: 작업 문서 도구와 자동 업데이트 비활성화
- `--work-note-artifact-dir <path>`: 작업 문서 artifact 디렉터리 이름
- `--no-work-note-auto-update`: 실행 종료 후 자동 worklog 업데이트 비활성화
- `--allow-tool <name>` / `--deny-tool <name>`: 이번 실행에서 사용할 도구 제한
- `--blocked-path <pattern>`: 차단 경로 패턴 추가

## 사용자별 산출물 격리

`PI_USER_ID`(또는 `--user-id`)를 설정하면 산출물 경로가 강제 격리됩니다.

- `reports/**`, `artifacts/**`, `outputs/**` 경로는 자동으로
  - `artifacts/users/<user_id>/...`
  아래로 리라이트됩니다.
- 추가로 `write`로 생성하는 일반 새 파일(예: `time_series_data.csv`, `plot.py`)은
  - `artifacts/users/<user_id>/workspace/<원래상대경로>`
  로 강제 저장됩니다.
- 사용자 모드에서는 최상위 파일명(예: `foo.csv`, `script.py`)도, 루트에 같은 이름 파일이 이미 있어도 사용자 아티팩트 workspace로 강제 저장됩니다.
- `artifacts/users/<other_id>/...` 경로 접근은 차단됩니다.
- 세션/감사/메모리 저장소도 사용자 네임스페이스로 분리됩니다.

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
  - 차단 툴: `write`, `edit`, `multiedit`, `exec`, `exec_readonly`, `memory_store`, `work_note_update`
  - 허용 툴(기존 정책 범위 내): `read`, `ls`, `find`, `grep`, `memory_search`, `memory_get`, `work_note_read`, `work_note_search`, `plan_note_write`
- 최종 계획은 `<proposed_plan>...</proposed_plan>` 형식을 사용하도록 유도합니다.
- `plan_note_write`는 계획 내용을 `artifacts/users/<user>/work-notes/<session>.md`에 저장할 수 있습니다.
- Plan 모드에서는 스킬 precheck 실패를 완화해 즉시 실패 대신 계획 응답을 반환합니다.
- Legacy 자동 메모리 저장은 Plan 모드에서 비활성화됩니다.
- 비활성화: `/plan off` 또는 `--plan-mode off`

## 서브에이전트와 읽기 전용 실행

- `delegate_task(description, prompt, agent_type)` 도구가 추가되었습니다.
- 지원 타입: `explore`, `plan`, `verify`
- 서브에이전트는 읽기/검색/검증용 도구와 작업 문서 읽기/검색만 사용하며 `write`, `edit`, `multiedit`, `exec`, `memory_store`, `work_note_update`, 재위임은 사용할 수 없습니다.
- `exec_readonly(command, cwd, timeout_s)`는 읽기 전용으로 분류된 명령만 실행합니다.
- 큰 `read`, `grep`, `work_note_search`, `exec`, `web_fetch` 결과는 preview와 `full_result_path`만 모델에 반환하고 전체 내용은 artifact 파일에 저장합니다.

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

## Markdown 검색 검증 루프

`markdown_loop.py`는 `markdown_search` 호환 stdio MCP를 대상으로 검색 → 읽기 → 초안 → 별도 verifier → 질의 보정/종료를 실행하는 최소 LangGraph 예제입니다.

```powershell
python markdown_loop.py --check

python markdown_loop.py "루프 엔지니어링의 핵심은?" `
  --mcp-command python `
  --mcp-arg=-m `
  --mcp-arg=your_markdown_search_server
```

- 필요한 MCP 도구: `search_markdown`, `read_markdown`
- 안전 종료: 근거 충분, 최대 반복, 반복 질의, 검색/읽기/검증 오류
- 실제 서버 실행 명령은 저장소에 포함되어 있지 않습니다.
- 상세 설계·검증·한계: `docs/MARKDOWN_LOOP_ENGINEERING_REPORT.kr.md`

### 연결된 Markdown Search HTTP MCP 사용

`.piagent/tools/markdown-search/tool.py`는 이미 실행 중인 로컬 Streamable HTTP MCP를 읽기 전용 PiAgent 툴로 연결합니다. 노출되는 툴은 `markdown_mcp_search`, `markdown_mcp_read`이며, `markdown-mcp-research` 스킬이 검색 → 정확한 문서 경로 읽기 → 근거 기반 답변 순서를 적용합니다.

- 호스트 기본 URL: `http://127.0.0.1:8811/mcp`
- Docker Compose 기본 URL: `http://host.docker.internal:8811/mcp`
- 재정의 환경변수: `PI_MARKDOWN_SEARCH_MCP_URL`
- 안전 정책: 승인된 로컬 호스트, Markdown 상대 경로, 결과 수·읽기 라인·응답 크기 제한

```powershell
python simple_piagent.py --workspace-extensions --mode full `
  --skill markdown-mcp-research `
  "Markdown MCP에서 agent runtime 안전 문서를 찾아 경로와 함께 요약해줘"
```

현재 권한 프로필은 임의의 custom tool 이름을 자동 신뢰하지 않으므로 `full`로 확장 툴을 열고, 선택한 스킬의 `tool_allow`가 실제 활성 툴을 두 Markdown 툴과 `ask_user`로 다시 제한합니다. 신뢰할 수 없는 저장소에서는 `--workspace-extensions`를 켜지 마십시오.
