# -*- coding: utf-8 -*-
import json
import os
import sys
from typing import Any
from dotenv import load_dotenv

load_dotenv(override=True)

# colorama 패키지 임포트 (설치되어 있지 않다면 나중에 requirements.txt 추가 필요)
try:
    from colorama import init, Fore, Style
    init(autoreset=True)
except ImportError:
    class Fore:
        GREEN = ""
        CYAN = ""
        YELLOW = ""
        RED = ""
        MAGENTA = ""
        RESET = ""
    class Style:
        BRIGHT = ""
        NORMAL = ""
        RESET_ALL = ""

from openclaw_pi_langchain import (
    OpenClawPiLangChain,
    PiAgentConfig,
    PiCallbacks,
)


def _safe_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        print(f"⚠️ 잘못된 정수 환경변수 {name}={raw!r}, 기본값 {default} 사용")
        return default


def _safe_csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


class ColoredChatCallbacks(PiCallbacks):
    """
    터미널에서 툴 실행 상태와 AI 응답을 색깔로 예쁘게 구분하여 보여주는 커스텀 콜백
    """
    def __init__(self):
        self.is_first_chunk = True

    def on_partial_reply(self, text: str) -> None:
        if self.is_first_chunk:
            sys.stdout.write(f"\n{Fore.GREEN}{Style.BRIGHT}🤖 Pi:{Style.RESET_ALL} ")
            self.is_first_chunk = False
        sys.stdout.write(f"{Fore.GREEN}{text}{Style.RESET_ALL}")
        sys.stdout.flush()

    def on_tool_start(self, tool_name: str, args: dict[str, Any]) -> None:
        # 툴이 시작될 때 노란색으로 표시 (불필요한 줄바꿈 방지)
        sys.stdout.write(f"\n{Fore.YELLOW}🛠️  [Running Tool: {tool_name}]{Style.RESET_ALL} args: {json.dumps(args, ensure_ascii=False)}\n")
        sys.stdout.flush()

    def on_tool_end(self, tool_name: str, output: str, is_error: bool) -> None:
        # 툴 종료 시 성공/실패 여부에 따라 색상 다르게 표시
        color = Fore.RED if is_error else Fore.CYAN
        status = "ERROR" if is_error else "SUCCESS"
        preview = output[:200] + ("..." if len(output) > 200 else "")
        sys.stdout.write(f"{color}└─[{status}] {preview.replace(chr(10), ' ')}{Style.RESET_ALL}\n")
        sys.stdout.flush()

    def on_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "custom":
            sys.stdout.write(f"\n{Fore.MAGENTA}ℹ️  [System: {payload.get('message', payload)}]{Style.RESET_ALL}\n")
            sys.stdout.flush()


def main():
    # 파서 설정을 가져오되, 사용자 프롬프트(prompt) 부분은 필수가 아니도록 수정하거나 무시합니다.
    # 기존 parse_args를 그대로 쓰면 프롬프트가 필수로 들어가므로 직접 세팅합니다.
    config = PiAgentConfig(
        model=os.getenv("PI_MODEL", "gpt-4o-mini"),
        workspace_dir=os.getenv("PI_WORKSPACE", "."),
        session_dir=os.getenv("PI_SESSION_DIR", ".openclaw_pi/sessions"),
        audit_dir=os.getenv("PI_AUDIT_DIR", ".openclaw_pi/audit"),
        max_model_calls=_safe_int_env("PI_MAX_MODEL_CALLS", 16),
        exec_timeout_s=_safe_int_env("PI_EXEC_TIMEOUT", 60),
        allow_write=os.getenv("PI_NO_WRITE", "false").lower() == "false",
        allow_shell=os.getenv("PI_NO_SHELL", "false").lower() == "false",
        enable_compaction=os.getenv("PI_NO_COMPACTION", "false").lower() == "false",
        enable_memory=os.getenv("PI_NO_MEMORY", "false").lower() == "false",
        memory_mode=os.getenv("PI_MEMORY_MODE", "openclaw"),
        memory_dir=os.getenv("PI_MEMORY_DIR", ".openclaw/memory"),
        memory_limit=max(1, _safe_int_env("PI_MEMORY_LIMIT", 200)),
        memory_recall_limit=max(1, _safe_int_env("PI_MEMORY_RECALL_LIMIT", 5)),
        memory_search_backend=os.getenv("PI_MEMORY_SEARCH_BACKEND", "sqlite-vec"),
        memory_embedding_provider=os.getenv("PI_MEMORY_EMBEDDING_PROVIDER", "auto"),
        memory_embedding_model=os.getenv("PI_MEMORY_EMBEDDING_MODEL", "text-embedding-3-small"),
        read_strategy=os.getenv("PI_READ_STRATEGY", "smart"),
        enable_exec_path_correction=os.getenv("PI_EXEC_PATH_CORRECTION", "false").lower() == "true",
        blocked_paths=_safe_csv_env(
            "PI_BLOCKED_PATHS",
            [".env", ".git/**", ".openclaw/memory/**", "secrets/**", "private/**", "node_modules/**"],
        ),
    )
    
    try:
        agent = OpenClawPiLangChain(config)
    except Exception as e:
        print(f"{Fore.RED}에이전트 초기화 실패: {e}{Style.RESET_ALL}")
        print(
            f"{Fore.YELLOW}힌트: OPENAI_API_KEY(또는 선택한 모델 제공자 키) 설정을 확인하세요.{Style.RESET_ALL}"
        )
        return

    session_id = os.getenv("PI_SESSION", "chat_main")
    callbacks = ColoredChatCallbacks()

    print(f"{Fore.CYAN}{Style.BRIGHT}==================================================")
    print("🚀 OpenClaw Pi Interactive Chat Mode Started")
    print(f"Workspace: {config.workspace_dir}")
    print(f"Model: {config.model}")
    print(f"Session: {session_id}")
    print("아래에 지시사항을 입력하세요. 종료하려면 'exit' 또는 'quit' 입력.")
    print(f"=================================================={Style.RESET_ALL}\n")

    while True:
        try:
            # 유저 입력 (하늘색/파란색 톤)
            user_input = input(f"\n{Fore.CYAN}{Style.BRIGHT}👤 You: {Style.RESET_ALL}")
            if not user_input.strip():
                continue
            
            if user_input.strip().lower() in ["exit", "quit", ":q"]:
                print(f"\n{Fore.YELLOW}채팅을 종료합니다. 안녕히 계세요! 👋{Style.RESET_ALL}")
                break

            # AI 응답 상태 초기화 및 실행
            callbacks.is_first_chunk = True
            _ = agent.run(
                session_id=session_id,
                prompt=user_input,
                callbacks=callbacks
            )
            # 스트리밍이 끝나면 보기 좋게 줄바꿈
            print()

        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}강제 종료 (Ctrl+C). 안녕히 계세요! 👋{Style.RESET_ALL}")
            break
        except Exception as e:
            print(f"\n{Fore.RED}예외 발생: {e}{Style.RESET_ALL}")


if __name__ == "__main__":
    main()
