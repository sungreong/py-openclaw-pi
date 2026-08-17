---
title: PiAgent 설정 가이드
theme: report
intent: reference
toc: true
---

# PiAgent 설정 가이드

이 문서는 PiAgent의 프로젝트 규칙, 권한, 세션, 사용자 격리, 스킬·도구 확장, 메모리, MCP를 설정하는 방법을 설명합니다. 아직 첫 실행 전이라면 [빠른 시작](QUICKSTART.kr.md)부터 진행하세요.

## 설정 위치 한눈에 보기

| 목적 | 위치 또는 옵션 | 적용 시점 |
| --- | --- | --- |
| 모델 키·모델 이름 | `.env` | 프로세스 시작 시 |
| 프로젝트 공통 규칙 | `.piagent/INSTRUCTIONS.md` | 매 요청 시 |
| 프로젝트 스킬·도구 | `.piagent/skills`, `.piagent/tools` | 확장 로딩이 켜진 시작 시 |
| 기본 스킬 | `skills/<name>/SKILL.md` | 시작 시 |
| 권한 모드 | `--mode`, 채팅의 `/mode` | 요청 또는 세션 시 |
| 세션·사용자 격리 | `--session`, `--user-id` | 실행 시 |
| 메모리·MCP·기타 | `.env` 또는 CLI 옵션 | 프로세스 시작 시 |

## 1. 프로젝트 공통 규칙

### 파일 위치와 작성 방법

프로젝트 루트에 아래 파일을 만듭니다.

```text
.piagent/INSTRUCTIONS.md
```

예시:

```markdown
# 프로젝트 공통 규칙

- 항상 한국어로 답변하세요.
- 코드 변경 전 관련 파일을 읽으세요.
- 변경 후 가능한 테스트를 실행하고, 실행하지 못했으면 이유를 말하세요.
- API 키, 토큰, 비밀번호를 응답이나 산출물에 쓰지 마세요.
```

PiAgent는 파일이 존재하고 내용이 비어 있지 않을 때만 내용을 시스템 프롬프트에 추가합니다. 파일이 없으면 기존 프롬프트만 사용하므로, 설정하지 않아도 정상 동작합니다.

### 우선순위와 제한

프로젝트 공통 규칙은 PiAgent의 기본 안전 정책에 **추가**됩니다. 다음은 규칙 파일로 허용할 수 없습니다.

- 차단 경로 또는 워크스페이스 밖 경로 접근
- 현재 권한 모드에서 숨겨진 도구 사용
- `review` 모드에서 파일 작성 또는 명령 실행
- 런타임이 차단한 위험 작업 재시도

파일은 UTF-8이어야 하며 64KB를 넘으면 실행을 중단하고 오류를 알립니다. 비밀정보는 이 파일에 넣지 마세요.

### 적용 확인

채팅을 재시작할 필요는 없습니다. 다음 요청부터 적용됩니다. 예를 들어 파일에 “항상 한국어로 답변하세요”를 작성한 뒤 다음 요청을 보냅니다.

```text
이 프로젝트에서 가장 중요한 실행 방법을 세 문장으로 요약해줘.
```

응답 언어는 모델의 생성 결과이므로 절대적 보장은 아니지만, 해당 지침은 모든 요청의 시스템 프롬프트에 전달됩니다.

## 2. 권한 모드

### `review`: 분석과 계획

```powershell
python chat.py --workspace . --session review-main --mode review --no-mcp
```

읽기·검색·계획만 허용합니다. `review`는 Plan 모드를 강제하므로 코드 작성 요청도 `<proposed_plan>` 형태의 계획으로 반환됩니다.

### `edit`: 기존 파일의 작은 수정

```powershell
python chat.py --workspace . --session edit-main --mode edit `
  --edit-path piagent/session.py --no-mcp
```

지정한 기존 파일에서 단일 치환만 허용합니다. 새 파일 작성, 테스트 실행, 패키지 설치, 여러 파일 수정은 할 수 없습니다.

### `full`: 구현과 검증

```powershell
python chat.py --workspace . --session implementation-main --mode full --no-mcp
```

새 파일 생성, 여러 파일 수정, 테스트 실행이 필요한 작업에 사용합니다. Docker Compose 기본 구성은 호스트 프로젝트를 `/app`에 연결하므로, 컨테이너 안의 변경도 호스트에 즉시 반영됩니다. 신뢰하는 요청과 작업 폴더에서만 사용하세요.

## 3. 세션과 사용자 격리

`--session`은 대화 이력을 구분하고, `--user-id`는 산출물·세션·감사 로그·메모리를 사용자별로 분리합니다.

```powershell
python openclaw_pi_langchain.py `
  --workspace . --session alice-report --user-id alice `
  --mode full "sample/data.csv를 분석해 보고서를 작성해줘"
```

`--user-id alice`를 사용하면 일반 산출물은 `artifacts/users/alice/` 아래로 격리됩니다. 같은 세션을 이어가려면 동일한 `--session` 값을 사용하세요.

## 4. 스킬과 워크스페이스 확장

### 스킬

반복되는 작업 절차는 `SKILL.md`로 정의합니다.

```text
skills/
└─ release-review/
   └─ SKILL.md
```

```markdown
---
name: release-review
description: 배포 전 변경 사항과 검증 결과를 검토할 때 사용한다.
---

1. 변경 파일과 테스트 결과를 먼저 확인한다.
2. 검증하지 않은 항목은 통과했다고 쓰지 않는다.
```

기본 스킬은 `skills/` 아래에서 자동 발견합니다. 특정 스킬을 고정하려면 `--skill release-review` 또는 채팅에서 `/skill release-review`를 사용합니다.

### `.piagent` 확장

신뢰하는 프로젝트에서만 확장 도구와 스킬을 쓰려면 `.env`에 아래를 설정합니다.

```dotenv
PI_WORKSPACE_EXTENSIONS_ENABLED=true
```

이후 `.piagent/skills/<name>/SKILL.md`와 `.piagent/tools/<name>/tool.py`를 발견합니다. `tool.py`는 Python 코드로 import되므로, 신뢰하지 않는 저장소에서는 확장 로딩을 켜지 마세요.

## 5. 메모리, MCP, 패키지 설치

| 기능 | 기본값 | 설정 방법 | 주의사항 |
| --- | --- | --- | --- |
| 메모리 | 켜짐 | `PI_NO_MEMORY=true`로 끄기 | 현재 작업과 직접 관련된 정보만 저장·조회 |
| MCP | 켜짐 | `--no-mcp`로 끄기 | 연결 대상과 도구 범위를 검토 |
| 패키지 설치 | 꺼짐 | `PI_ALLOW_PACKAGE_INSTALL=true`와 allowlist | 정확한 버전만 허용 목록에 추가 |

패키지 설치 allowlist 예시:

```dotenv
PI_ALLOW_PACKAGE_INSTALL=true
PI_PACKAGE_INSTALL_ALLOWLIST=python-docx==1.2.0,matplotlib==3.11.1
```

더 많은 환경변수와 Local Bedrock 설정은 [초기 설정과 사용 가이드](GETTING_STARTED.kr.md)를 참고하세요.

## 6. 권장 초기 설정

처음 프로젝트에 적용할 때는 다음 순서를 권장합니다.

1. `.env`에 모델 연결만 설정합니다.
2. `simple_piagent.py --check`로 모델 호출 없이 진단합니다.
3. `.piagent/INSTRUCTIONS.md`에 응답 언어·검증 원칙·금지사항만 짧게 작성합니다.
4. `review` 모드에서 구조 분석과 구현 계획을 받습니다.
5. 변경 범위를 확인한 뒤 `edit` 또는 `full` 모드로 실행합니다.
6. 필요해진 경우에만 스킬, 확장 도구, MCP, 패키지 설치를 추가합니다.

## 문제 해결과 다음 문서

- 설치·Docker·한글 입력 문제: [초기 설정과 사용 가이드](GETTING_STARTED.kr.md#11-docker-compose-초기-설정)
- 채팅 슬래시 명령과 세션 전환: [대화형 에이전트 사용법](AGENT_LEVEL_AND_CHAT.kr.md)
- 전체 빠른 실행 순서: [빠른 시작](QUICKSTART.kr.md)

