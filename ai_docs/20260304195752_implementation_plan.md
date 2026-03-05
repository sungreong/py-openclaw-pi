# OpenClaw Pi Agent 개선 구현 계획서

## 1. 목적 (Goal)
- LangChain / LangGraph 기반 에이전트 루프에서 툴 실행 중 예외(Exception)가 발생하여 프로그램 전체가 강제 종료되는 치명적인 문제(Panic)를 해결합니다.
- 예외 발생 시 이를 LLM 에이전트에게 문자열 형태(`Error: ...`)로 반환하여 에이전트가 스스로 실수를 인지하고 복구(Self-correction)할 수 있도록 구조를 개선합니다.

## 2. 사용자 피드백 요청 (User Review Required)
- **Tool Error Handling 방식:** 모든 도구(Tool) 내부에 범용적인 `try-except` 블록을 추가하여, 파이썬 런타임 에러(예: 텍스트 파일이 없거나, 정규식 문법 오류, 명령어 시간 초과 등)가 발생해도 에이전트가 정상적으로 다음 생각을 이어갈 수 있도록 조치하고자 합니다. 진행 전 승인 부탁드립니다.
- 워크스페이스 내 파일 크기 초과/분할 모듈화 규정(사용자 Rule)은 현재 이 단일 에이전트 스크립트 특성상 1,000줄 미만(약 650줄)이므로 별도로 파일을 쪼개진 않겠습니다.

## 3. 상세 개선 사항 (Proposed Changes)

### `openclaw_pi_langchain.py`
모든 `@tool` 함수 내부에 안정성 확보 코드를 추가합니다.

#### [MODIFY] `read` 툴
- 파일 미존재(`FileNotFoundError`), 디렉터리 접근(`IsADirectoryError`), 권한 오류 등을 포착하여 `return f"Error: {e}"` 형태로 반환하도록 수정.

#### [MODIFY] `write`, `edit` 툴
- 디렉터리 생성 및 파일 수정 시 발생할 수 있는 오류를 `try-except Exception as e:` 로 캐치.

#### [MODIFY] `ls`, `find`, `grep` 툴
- `grep`에서 부적절한 정규표현식(Regex)을 입력했을 때 발생하는 `re.error` 예외 처리.
- `ls`에서 접근 권한이 없는 디렉터리 조회 시 발생하는 예외 처리.

#### [MODIFY] `exec_tool` 툴
- 셸 명령어 실행 중 지정된 시간을 초과할 경우 발생하는 `subprocess.TimeoutExpired`를 깔끔하게 캐치하여 `return f"Error: Command timed out after {timeout_s} seconds."` 형태로 응답.
- 그 외 `psutil` 기반 강제 종료나 프로세스 꼬임 방지를 위한 안전망(Subprocess error handling) 보완.

## 4. 검증 계획 (Verification Plan)
- **임의의 존재하지 않는 파일 읽기 테스트**
  - 터미널(또는 도커 컨테이너 내부)에서 사용자가 실행하셨던 `python openclaw_pi_langchain.py --model gpt-4o --workspace . --session main "Read non_existent_file.txt"` 명령을 실행하여, 이전처럼 Python Crash StackTrace가 출력되지 않고 LLM이 파일이 없음을 인지하고 정상적으로 답변하는지 확인합니다.
- **오랜 시간 걸리는 명령어 테스트**
  - 타임아웃 테스트: `python openclaw_pi_langchain.py "exec sleep 100"` 등을 지시했을 때, 컨테이너가 멈추지 않고 타임아웃 에러를 정상적으로 Agent에 리턴하는지 확인합니다.
