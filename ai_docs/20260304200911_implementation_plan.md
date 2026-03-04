# Interactive CLI Chat 구현 계획서

## 1. 목적 (Goal)
- 기존 `openclaw_pi_langchain.py` 코드는 단발성(Single-shot) 실행 방식이어서, 한 번 질문하고 에이전트가 답변하면 스크립트가 완전히 종료됩니다.
- 사용자가 CMD/터미널 환경에서 매번 스크립트를 재실행하지 않고 **연속적으로 에이전트와 대화**할 수 있도록 하는 대화형(Interactive) 인터페이스 스크립트(`chat.py`)를 개발합니다.

## 2. 제안 사항 (Proposed Changes)

### [NEW] `chat.py` 추가
- `openclaw_pi_langchain.py` 파일 내부에 있는 파서 도구와 `OpenClawPiLangChain`, 설정(`PiAgentConfig`) 클래스를 임포트하여 재사용합니다.
- `while True:` 루프를 사용하여 사용자 입력을 지속적으로 받습니다.
- 사용자가 `exit`, `quit` 등을 입력하면 루프를 종료하고 프로그램을 빠져나오도록 구현합니다.
- 매 턴마다 `agent.run(session_id, prompt)`를 호출하여, 기존에 구축된 메모리 구조(session store 및 `.openclaw_pi/sessions`)를 재활용, **대화 맥락이 그대로 유지되는** 멀티턴 대화형 챗봇 경험을 제공합니다.
- 콘솔 출력 시 유저 질문과 AI 답변이 헷갈리지 않도록 간단하게 구별 가능한 Prefix(`You:`, `Pi:`)를 추가해 직관적으로 만듭니다.

## 3. 사용자 리뷰 (User Review Required)
- 기존 코드를 건드리지 않고, 기존 환경변수(`.env`)와 LangChain 에이전트 클래스를 안전하게 불러와서 쓰는 **별도의 래퍼(wrapper) 스크립트** 형태로 만드는 방향입니다.
- 추가로 원하는 명령어나 특별한 UI/UX(색상 출력 등) 기능이 있으시면 말씀해 주세요.
