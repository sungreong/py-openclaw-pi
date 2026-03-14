# -*- coding: utf-8 -*-
import json
import os
import re
import sys
from typing import Any

from dotenv import load_dotenv

load_dotenv(override=True)

# colorama 패키지 임포트 (설치되어 있지 않다면 색상 없이 동작)
try:
    from colorama import Fore, Style, init

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


def _configure_stdio() -> None:
    """
    Windows/터미널 환경에서 한글 출력 깨짐을 줄이기 위해 UTF-8로 재설정.
    지원하지 않는 환경에서는 조용히 무시.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def _safe_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        print(f"잘못된 정수 환경변수: {name}={raw!r}, 기본값 {default} 사용")
        return default


def _safe_csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _normalize_plan_mode(raw: str) -> str:
    mode = str(raw or "off").strip().lower()
    return mode if mode in {"on", "off"} else "off"


def _wants_execute_now(raw_text: str) -> bool:
    text = str(raw_text or "").strip().lower()
    if not text:
        return False
    # planning-only requests should not auto-disable plan mode
    plan_words = ("계획", "플랜", "plan", "전략", "strategy")
    if any(word in text for word in plan_words):
        return False
    korean_execute_words = (
        "실행해",
        "실행해줘",
        "진행해",
        "진행해줘",
        "바로 해",
        "바로해",
        "적용해",
        "적용해줘",
        "시작해",
        "시작해줘",
    )
    if any(word in text for word in korean_execute_words):
        return True
    english_execute_patterns = (
        r"\bgo ahead\b",
        r"\bproceed\b",
        r"\bexecute\b",
        r"\brun( it| now)?\b",
    )
    return any(re.search(pattern, text) for pattern in english_execute_patterns)


def _match_skill_row(rows: list[dict[str, Any]], raw_name: str) -> dict[str, Any] | None:
    target = str(raw_name or "").strip().lower()
    if not target:
        return None
    for row in rows:
        skill_id = str(row.get("id", "")).strip().lower()
        skill_name = str(row.get("name", "")).strip().lower()
        if target in {skill_id, skill_name}:
            return row
    return None


class ColoredChatCallbacks(PiCallbacks):
    """
    터미널에서 실행 상태와 AI 응답을 색상으로 구분해 보여주는 콜백
    """

    def __init__(self):
        self.is_first_chunk = True
        self.has_any_output = False

    def on_partial_reply(self, text: str) -> None:
        if self.is_first_chunk:
            sys.stdout.write(f"\n{Fore.GREEN}{Style.BRIGHT}Pi:{Style.RESET_ALL} ")
            self.is_first_chunk = False
        self.has_any_output = True
        sys.stdout.write(f"{Fore.GREEN}{text}{Style.RESET_ALL}")
        sys.stdout.flush()

    def on_tool_start(self, tool_name: str, args: dict[str, Any]) -> None:
        # 툴 시작은 노란색으로 표시
        sys.stdout.write(
            f"\n{Fore.YELLOW}[Running Tool: {tool_name}]{Style.RESET_ALL} args: "
            f"{json.dumps(args, ensure_ascii=False)}\n"
        )
        sys.stdout.flush()

    def on_tool_end(self, tool_name: str, output: str, is_error: bool) -> None:
        # 툴 종료는 성공/실패에 따라 색상 구분
        color = Fore.RED if is_error else Fore.CYAN
        status = "ERROR" if is_error else "SUCCESS"
        preview = output[:200] + ("..." if len(output) > 200 else "")
        sys.stdout.write(
            f"{color}[{status}] {preview.replace(chr(10), ' ')}{Style.RESET_ALL}\n"
        )
        sys.stdout.flush()

    def on_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "custom":
            sys.stdout.write(
                f"\n{Fore.MAGENTA}[System: {payload.get('message', payload)}]"
                f"{Style.RESET_ALL}\n"
            )
            sys.stdout.flush()


def main():
    _configure_stdio()

    # 기존 parse_args 의존 없이 환경변수 중심으로 구성
    config = PiAgentConfig(
        model=os.getenv("PI_MODEL", "gpt-4o-mini"),
        workspace_dir=os.getenv("PI_WORKSPACE", "."),
        session_dir=os.getenv("PI_SESSION_DIR", ".openclaw_pi/sessions"),
        audit_dir=os.getenv("PI_AUDIT_DIR", ".openclaw_pi/audit"),
        max_model_calls=_safe_int_env("PI_MAX_MODEL_CALLS", 16),
        tool_repeat_limit=max(1, _safe_int_env("PI_TOOL_REPEAT_LIMIT", 3)),
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
        memory_embedding_model=os.getenv(
            "PI_MEMORY_EMBEDDING_MODEL", "text-embedding-3-small"
        ),
        read_strategy=os.getenv("PI_READ_STRATEGY", "smart"),
        custom_tool_modules=[
            item.strip()
            for item in os.getenv("PI_CUSTOM_TOOL_MODULES", "").split(",")
            if item.strip()
        ],
        mcp_enabled=os.getenv("PI_MCP_ENABLED", "true").lower()
        in {"1", "true", "yes", "on"},
        mcp_config_path=os.getenv("PI_MCP_CONFIG", "mcp_servers.json"),
        mcp_fail_fast=os.getenv("PI_MCP_FAIL_FAST", "false").lower()
        in {"1", "true", "yes"},
        mcp_timeout_s=max(1, _safe_int_env("PI_MCP_TIMEOUT", 20)),
        skills_enabled=os.getenv("PI_SKILLS_ENABLED", "true").lower()
        in {"1", "true", "yes", "on"},
        skills_dir=os.getenv("PI_SKILLS_DIR", "skills"),
        skill_mode=os.getenv("PI_SKILL_MODE", "auto"),
        skill_name=(os.getenv("PI_SKILL", "").strip() or None),
        plan_mode=_normalize_plan_mode(os.getenv("PI_PLAN_MODE", "off")),
        enable_exec_path_correction=os.getenv("PI_EXEC_PATH_CORRECTION", "false").lower()
        == "true",
        blocked_paths=_safe_csv_env(
            "PI_BLOCKED_PATHS",
            [
                ".env",
                ".git/**",
                ".openclaw/memory/**",
                "secrets/**",
                "private/**",
                "node_modules/**",
            ],
        ),
    )

    try:
        agent = OpenClawPiLangChain(config)
    except Exception as e:
        print(f"{Fore.RED}에이전트 초기화 실패: {e}{Style.RESET_ALL}")
        print(
            f"{Fore.YELLOW}힌트: OPENAI_API_KEY(또는 선택한 모델 제공자) 설정을 확인하세요."
            f"{Style.RESET_ALL}"
        )
        return

    session_id = os.getenv("PI_SESSION", "chat_main")
    callbacks = ColoredChatCallbacks()
    current_skill_mode = os.getenv("PI_SKILL_MODE", "auto").strip().lower() or "auto"
    current_skill_name = os.getenv("PI_SKILL", "").strip() or None
    current_plan_mode = _normalize_plan_mode(os.getenv("PI_PLAN_MODE", "off"))

    print(f"{Fore.CYAN}{Style.BRIGHT}==================================================")
    print("OpenClaw Pi Interactive Chat Mode Started")
    print(f"Workspace: {config.workspace_dir}")
    print(f"Model: {config.model}")
    print(f"Session: {session_id}")
    print(f"Skill mode: {current_skill_mode}")
    print(f"Plan mode: {current_plan_mode}")
    print("메시지를 입력하세요. 종료하려면 'exit' 또는 'quit' 입력.")
    print("Skill commands: /skills, /skill <name>, /skill auto, /skill off")
    print("Plan commands: /plan, /plan on, /plan off")
    print(f"=================================================={Style.RESET_ALL}\n")

    while True:
        try:
            # 사용자 입력
            user_input = input(f"\n{Fore.CYAN}{Style.BRIGHT}You: {Style.RESET_ALL}")
            stripped = user_input.strip()
            if not stripped:
                continue

            low = stripped.lower()

            if low in ["exit", "quit", ":q"]:
                print(
                    f"\n{Fore.YELLOW}채팅을 종료합니다. 안녕히 계세요!{Style.RESET_ALL}"
                )
                break

            if low == "/skills":
                rows = agent.list_skills()
                if not rows:
                    print(
                        f"{Fore.YELLOW}No skills found in {config.skills_dir}{Style.RESET_ALL}"
                    )
                else:
                    print(f"{Fore.CYAN}Available skills:{Style.RESET_ALL}")
                    for row in rows:
                        triggers = ",".join(row.get("triggers", [])) or "-"
                        print(f"  - {row['id']} :: {row['name']} (triggers={triggers})")
                continue

            if low.startswith("/skill"):
                parts = stripped.split(maxsplit=1)
                arg = parts[1].strip() if len(parts) > 1 else ""
                if not arg:
                    state = current_skill_name or "(none)"
                    print(
                        f"{Fore.CYAN}Skill mode={current_skill_mode}, skill={state}{Style.RESET_ALL}"
                    )
                    continue
                low = arg.lower()
                if low == "auto":
                    current_skill_mode = "auto"
                    current_skill_name = None
                    agent.audit_logger.log(
                        session_id, "skill_switched", {"mode": "auto", "skill": None}
                    )
                    print(f"{Fore.CYAN}Skill mode changed to auto{Style.RESET_ALL}")
                    continue
                if low == "off":
                    current_skill_mode = "off"
                    current_skill_name = None
                    agent.audit_logger.log(
                        session_id, "skill_switched", {"mode": "off", "skill": None}
                    )
                    print(f"{Fore.CYAN}Skill mode changed to off{Style.RESET_ALL}")
                    continue
                current_skill_mode = "manual"
                current_skill_name = arg
                agent.audit_logger.log(
                    session_id, "skill_switched", {"mode": "manual", "skill": arg}
                )
                print(f"{Fore.CYAN}Skill pinned: {arg}{Style.RESET_ALL}")
                continue

            if low.startswith("/plan"):
                parts = stripped.split(maxsplit=1)
                arg = parts[1].strip().lower() if len(parts) > 1 else ""
                if not arg:
                    print(f"{Fore.CYAN}Plan mode={current_plan_mode}{Style.RESET_ALL}")
                    continue
                if arg not in {"on", "off"}:
                    print(f"{Fore.YELLOW}Usage: /plan on|off{Style.RESET_ALL}")
                    continue
                current_plan_mode = arg
                agent.audit_logger.log(
                    session_id,
                    "plan_switched",
                    {"mode": current_plan_mode},
                )
                print(f"{Fore.CYAN}Plan mode changed to {current_plan_mode}{Style.RESET_ALL}")
                continue

            if stripped.startswith("/"):
                cmd_token, _, remainder = stripped.partition(" ")
                maybe_skill = cmd_token[1:].strip()
                rows = agent.list_skills()
                matched = _match_skill_row(rows, maybe_skill)
                if matched is not None:
                    current_skill_mode = "manual"
                    current_skill_name = str(matched.get("id", maybe_skill)).strip() or maybe_skill
                    agent.audit_logger.log(
                        session_id,
                        "skill_switched",
                        {"mode": "manual", "skill": current_skill_name, "via": "slash-shortcut"},
                    )
                    print(f"{Fore.CYAN}Skill pinned: {current_skill_name}{Style.RESET_ALL}")
                    if remainder.strip():
                        user_input = remainder.strip()
                    else:
                        continue

            # AI 응답 상태 초기화 후 실행
            callbacks.is_first_chunk = True
            callbacks.has_any_output = False
            if current_plan_mode == "on" and _wants_execute_now(user_input):
                current_plan_mode = "off"
                agent.audit_logger.log(
                    session_id,
                    "plan_switched",
                    {"mode": "off", "via": "auto_intent"},
                )
                print(
                    f"{Fore.CYAN}Plan mode auto-switched to off by execution intent{Style.RESET_ALL}"
                )
            result = agent.run(
                session_id=session_id,
                prompt=user_input,
                callbacks=callbacks,
                skill_name=current_skill_name,
                skill_mode=current_skill_mode,
                plan_mode=current_plan_mode,
            )
            # 스트리밍이 없었던 경우 반환 텍스트를 명시적으로 출력
            if not callbacks.has_any_output:
                fallback = (result.final_text or "").strip() or "(응답이 비어 있습니다)"
                print(f"\n{Fore.GREEN}{Style.BRIGHT}Pi:{Style.RESET_ALL} {Fore.GREEN}{fallback}{Style.RESET_ALL}")
            # 출력 마무리 줄바꿈
            print()

        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}강제 종료 (Ctrl+C). 안녕히 계세요!{Style.RESET_ALL}")
            break
        except Exception as e:
            print(f"\n{Fore.RED}예외 발생: {e}{Style.RESET_ALL}")

    agent.close()


if __name__ == "__main__":
    main()
