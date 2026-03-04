# 프로젝트 이름

이 프로젝트는 OpenClaw Pi 런타임을 활용하여 AI 기능을 구현하는 애플리케이션입니다. Docker를 사용하여 컨테이너화된 환경에서 실행됩니다.

## 디렉토리 및 파일 구조

- `.openclaw_pi/`: OpenClaw Pi 런타임의 설정과 데이터를 포함합니다.
- `__pycache__/`: Python 바이트코드 캐시 파일을 포함합니다.
- `ai_docs/`: AI 관련 문서를 포함하고 있습니다.
- `.env`: 환경 변수를 정의하는 파일입니다.
- `chat.py`: 채팅 기능을 구현한 Python 스크립트입니다.
- `docker-compose.yml`: 다중 컨테이너 Docker 애플리케이션을 정의하고 실행하는 데 사용됩니다.
- `Dockerfile`: 프로젝트의 Docker 이미지를 생성하는 데 사용됩니다.
- `openclaw_pi_langchain.py`: 애플리케이션의 주요 Python 스크립트입니다.
- `requirements-openclaw-pi-langchain.txt`: Python 의존성을 나열한 파일입니다.

## 설치 및 실행

1. Docker와 Docker Compose를 설치합니다.
2. `.env` 파일을 설정합니다.
3. 다음 명령어를 사용하여 Docker 컨테이너를 빌드하고 실행합니다:
   ```bash
   docker-compose up --build
   ```

## 경량 메모리 모드

에이전트는 기본적으로 세션별 장기 메모리를 `.openclaw_pi/memory/<session>.jsonl`에 저장합니다.
메모리는 규칙 기반으로 선호/제약/할 일/사실을 추출하며, 다음 턴 입력 전에 관련 항목만 일부 재주입합니다.

- 비활성화: `PI_NO_MEMORY=true` 또는 CLI `--no-memory`
- 저장 개수 제한: `PI_MEMORY_LIMIT` 또는 CLI `--memory-limit`
- 재호출 개수 제한: `PI_MEMORY_RECALL_LIMIT` 또는 CLI `--memory-recall-limit`
- 메모리 디렉터리: `PI_MEMORY_DIR` 또는 CLI `--memory-dir`
- 검색 백엔드: `PI_MEMORY_SEARCH_BACKEND` 또는 CLI `--memory-search-backend` (`sqlite-vec`/`keyword`)
- 임베딩 제공자: `PI_MEMORY_EMBEDDING_PROVIDER` 또는 CLI `--memory-embedding-provider` (`auto`/`openai`/`hash`)
- 임베딩 모델: `PI_MEMORY_EMBEDDING_MODEL` 또는 CLI `--memory-embedding-model` (예: `text-embedding-3-small`)

`sqlite-vec` 백엔드는 OpenClaw 방식처럼 SQLite + 벡터 거리(`vec_distance_cosine`)를 우선 사용하고,
확장 로딩이 불가능한 환경에서는 동일 DB에서 코사인 점수 폴백 검색으로 동작합니다.

## 기여

기여를 원하신다면, 이 저장소를 포크하고 변경 사항을 반영한 후 풀 리퀘스트를 제출해 주세요.

## 라이선스

이 프로젝트는 MIT 라이선스 하에 배포됩니다.
