# -*- coding: utf-8 -*-
import argparse
import json
import os
import re
import sys
import time
from typing import Any, Optional, Sequence

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
    PERMISSION_PROFILES,
    PiAgentConfig,
    PiAgentSession,
    PiCallbacks,
    WorkspaceGuard,
)


_MAX_PROMPT_FILE_BYTES = 1_000_000


def _configure_stdio() -> None:
    """
    Windows/터미널 환경에서 한글 출력 깨짐을 줄이기 위해 UTF-8로 재설정.
    지원하지 않는 환경에서는 조용히 무시.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="strict")
            except Exception:
                pass


def _validate_console_input(value: str) -> str:
    """Reject text that was decoded with surrogate escapes before a model call."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(
            "입력 문자를 UTF-8로 처리할 수 없습니다. Windows에서 Docker를 사용할 때는 "
            "[Console]::InputEncoding과 [Console]::OutputEncoding을 UTF-8로 설정한 뒤 "
            "채팅을 다시 시작하세요."
        ) from exc
    return value


def _decode_console_input(raw: bytes) -> str:
    """Decode Docker TTY input from UTF-8 or the Windows Korean console encoding."""
    for encoding in ("utf-8", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(
        "콘솔 입력을 UTF-8 또는 CP949로 해석할 수 없습니다. 터미널 인코딩을 확인한 뒤 "
        "채팅을 다시 시작하세요."
    )


def _read_console_input(prompt: str) -> str:
    """Read raw terminal bytes so Docker Desktop does not decode them prematurely."""
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        return _validate_console_input(input(prompt))

    print(prompt, end="", flush=True)
    raw = buffer.readline()
    if not raw:
        raise EOFError
    return _validate_console_input(_decode_console_input(raw.rstrip(b"\r\n")))


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


def _normalize_session_id(raw: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(raw or "").strip()).strip("._-")
    if not safe:
        raise ValueError("session name must contain a letter or number")
    return safe[:120]


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


def _format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(seconds, 60)
    return f"{int(minutes)}m {remainder:.0f}s"


def _redact_for_display(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(token in lowered for token in ("key", "token", "secret", "password", "authorization")):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): _redact_for_display(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_for_display(item, key) for item in value[:8]]
    if isinstance(value, str):
        compact = value.replace("\n", "\\n")
        return compact[:240] + ("..." if len(compact) > 240 else "")
    return value


def _summarize_payload(payload: dict[str, Any], limit: int = 520) -> str:
    try:
        safe = _redact_for_display(payload)
        text = json.dumps(safe, ensure_ascii=False, sort_keys=True)
    except Exception:
        text = str(payload)
    return text[:limit] + ("..." if len(text) > limit else "")


def _preview_text(text: str, limit: int = 260) -> str:
    compact = " ".join(str(text or "").split())
    return compact[:limit] + ("..." if len(compact) > limit else "")


def _print_help() -> None:
    print(f"{Fore.CYAN}Commands:{Style.RESET_ALL}")
    print("  /help                 show commands")
    print("  /status               show current chat state")
    print("  /skills               list available skills")
    print("  /tools                list active tools")
    print("  /skill <name>         pin a skill")
    print("  /skill auto|off       switch skill mode")
    print("  /mode                 show simplified permission mode")
    print("  /mode review|full     switch permission mode")
    print("  /mode edit <paths>    allow focused edits in comma-separated files")
    print("  /plan                 show plan mode")
    print("  /plan on|off          toggle plan mode")
    print("  /session              show current session")
    print("  /session <name>       switch persistent session")
    print("  /last                 show previous final answer")
    print("  exit, quit, :q        exit chat")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive PiAgent chat")
    parser.add_argument("--workspace", default=os.getenv("PI_WORKSPACE", "."))
    parser.add_argument("--session", default=os.getenv("PI_SESSION", "chat_main"))
    parser.add_argument("--user-id", default=os.getenv("PI_USER_ID", ""))
    parser.add_argument("--model", default=os.getenv("PI_MODEL", "gpt-4o-mini"))
    parser.add_argument(
        "--mode",
        choices=PERMISSION_PROFILES,
        default=os.getenv("PI_MODE", "review").strip().lower() or "review",
        help="review=read/plan, edit=scoped partial edits, full=all configured tools",
    )
    parser.add_argument(
        "--edit-path",
        action="append",
        default=_safe_csv_env("PI_EDIT_PATHS", []),
        help="Existing file that edit mode may modify; repeatable",
    )
    parser.add_argument("--skill", default=os.getenv("PI_SKILL", ""))
    parser.add_argument(
        "--skill-mode",
        choices=["auto", "manual", "off"],
        default=os.getenv("PI_SKILL_MODE", "auto").strip().lower() or "auto",
    )
    parser.add_argument(
        "--plan-mode",
        choices=["on", "off"],
        default=_normalize_plan_mode(os.getenv("PI_PLAN_MODE", "off")),
    )
    parser.add_argument(
        "--permission-mode",
        choices=["default", "plan", "accept_edits", "dont_ask"],
        default=os.getenv("PI_PERMISSION_MODE", "default").strip().lower(),
    )
    parser.add_argument(
        "--max-model-calls",
        type=int,
        default=max(1, _safe_int_env("PI_MAX_MODEL_CALLS", 16)),
    )
    parser.add_argument("--allow-tool", action="append", default=[])
    parser.add_argument("--deny-tool", action="append", default=[])
    parser.add_argument("--workspace-extensions", action="store_true")
    parser.add_argument("--no-mcp", action="store_true")
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--no-shell", action="store_true")
    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument("--once", default="", help="Run one prompt and exit")
    prompt_group.add_argument(
        "--prompt-file",
        default="",
        help="Run one UTF-8 prompt file inside the workspace and exit",
    )
    parser.add_argument("--check", action="store_true", help="Inspect configuration without a model call")
    return parser


def build_config(args: argparse.Namespace) -> PiAgentConfig:
    raw_permission = str(args.permission_mode or "default").strip().lower()
    permission_mode = (
        raw_permission
        if raw_permission in {"default", "plan", "accept_edits", "dont_ask"}
        else "default"
    )
    return PiAgentConfig(
        model=str(args.model),
        workspace_dir=str(args.workspace),
        session_dir=os.getenv("PI_SESSION_DIR", ".openclaw_pi/sessions"),
        audit_dir=os.getenv("PI_AUDIT_DIR", ".openclaw_pi/audit"),
        max_model_calls=max(1, int(args.max_model_calls)),
        tool_repeat_limit=max(1, _safe_int_env("PI_TOOL_REPEAT_LIMIT", 3)),
        session_evidence_limit=max(1, _safe_int_env("PI_SESSION_EVIDENCE_LIMIT", 6)),
        session_evidence_summary_chars=max(60, _safe_int_env("PI_SESSION_EVIDENCE_SUMMARY_CHARS", 220)),
        repeat_guard_enabled=os.getenv("PI_REPEAT_GUARD_ENABLED", "true").lower()
        in {"1", "true", "yes", "on"},
        repeat_confirm_token=(os.getenv("PI_REPEAT_CONFIRM_TOKEN", "[재실행 승인]").strip() or "[재실행 승인]"),
        exec_timeout_s=_safe_int_env("PI_EXEC_TIMEOUT", 60),
        allow_write=not bool(args.no_write)
        and os.getenv("PI_NO_WRITE", "false").lower() not in {"1", "true", "yes", "on"},
        allow_shell=not bool(args.no_shell)
        and os.getenv("PI_NO_SHELL", "false").lower() not in {"1", "true", "yes", "on"},
        allow_package_install=os.getenv("PI_ALLOW_PACKAGE_INSTALL", "false").lower()
        in {"1", "true", "yes", "on"},
        package_install_allowlist=_safe_csv_env("PI_PACKAGE_INSTALL_ALLOWLIST", []),
        package_install_timeout_s=max(10, _safe_int_env("PI_PACKAGE_INSTALL_TIMEOUT", 180)),
        enable_compaction=os.getenv("PI_NO_COMPACTION", "false").lower()
        not in {"1", "true", "yes", "on"},
        enable_memory=not bool(args.no_memory)
        and os.getenv("PI_NO_MEMORY", "false").lower() not in {"1", "true", "yes", "on"},
        memory_mode=os.getenv("PI_MEMORY_MODE", "openclaw"),
        memory_dir=os.getenv("PI_MEMORY_DIR", ".openclaw/memory"),
        memory_limit=max(1, _safe_int_env("PI_MEMORY_LIMIT", 200)),
        memory_recall_limit=max(1, _safe_int_env("PI_MEMORY_RECALL_LIMIT", 5)),
        memory_search_backend=os.getenv("PI_MEMORY_SEARCH_BACKEND", "sqlite-vec"),
        memory_embedding_provider=os.getenv("PI_MEMORY_EMBEDDING_PROVIDER", "auto"),
        memory_embedding_model=os.getenv("PI_MEMORY_EMBEDDING_MODEL", "text-embedding-3-small"),
        read_strategy=os.getenv("PI_READ_STRATEGY", "smart"),
        custom_tool_modules=_safe_csv_env("PI_CUSTOM_TOOL_MODULES", []),
        workspace_extensions_enabled=bool(args.workspace_extensions)
        or os.getenv("PI_WORKSPACE_EXTENSIONS_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"},
        workspace_extension_dir=(os.getenv("PI_WORKSPACE_EXTENSION_DIR", ".piagent").strip() or ".piagent"),
        mcp_enabled=not bool(args.no_mcp)
        and os.getenv("PI_MCP_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
        mcp_config_path=os.getenv("PI_MCP_CONFIG", "mcp_servers.json"),
        mcp_fail_fast=os.getenv("PI_MCP_FAIL_FAST", "false").lower() in {"1", "true", "yes"},
        mcp_timeout_s=max(1, _safe_int_env("PI_MCP_TIMEOUT", 20)),
        skills_enabled=os.getenv("PI_SKILLS_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
        skills_dir=os.getenv("PI_SKILLS_DIR", "skills"),
        skill_mode=str(args.skill_mode),
        skill_name=(str(args.skill).strip() or None),
        plan_mode=_normalize_plan_mode(str(args.plan_mode)),
        permission_mode=permission_mode,
        enable_subagents=os.getenv("PI_NO_SUBAGENTS", "false").lower() not in {"1", "true", "yes"},
        max_tool_result_chars=max(1000, _safe_int_env("PI_MAX_TOOL_RESULT_CHARS", 24000)),
        tool_result_artifact_dir=(os.getenv("PI_TOOL_RESULT_ARTIFACT_DIR", "tool-results").strip() or "tool-results"),
        enable_session_notes=os.getenv("PI_NO_SESSION_NOTES", "false").lower() not in {"1", "true", "yes"},
        enable_work_notes=os.getenv("PI_NO_WORK_NOTES", "false").lower() not in {"1", "true", "yes"},
        work_note_artifact_dir=(os.getenv("PI_WORK_NOTE_ARTIFACT_DIR", "work-notes").strip() or "work-notes"),
        work_note_auto_update=os.getenv("PI_NO_WORK_NOTE_AUTO_UPDATE", "false").lower()
        not in {"1", "true", "yes"},
        user_id=(str(args.user_id).strip() or None),
        hooks_config_path=(os.getenv("PI_HOOKS_CONFIG", "pi_hooks.json").strip() or "pi_hooks.json"),
        enable_exec_path_correction=os.getenv("PI_EXEC_PATH_CORRECTION", "false").lower() == "true",
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


def _read_prompt_file(config: PiAgentConfig, raw_path: str) -> str:
    guard = WorkspaceGuard(config.workspace_path(), config.blocked_paths)
    path = guard.resolve(str(raw_path))
    if not path.is_file():
        raise ValueError(f"prompt file not found: {raw_path}")
    if path.stat().st_size > _MAX_PROMPT_FILE_BYTES:
        raise ValueError("prompt file exceeds the 1 MB safety limit")
    try:
        return path.read_text(encoding="utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("prompt file must be UTF-8 encoded") from exc


def _model_route() -> str:
    if os.getenv("LOCAL_BEDROCK_BASE_URL", "").strip():
        return "local-bedrock"
    if os.getenv("OPENAI_API_KEY", "").strip():
        return "openai"
    return "unconfigured"


def run_check(config: PiAgentConfig, session_id: str) -> dict[str, Any]:
    had_openai_key = bool(os.getenv("OPENAI_API_KEY", "").strip())
    has_bedrock = bool(os.getenv("LOCAL_BEDROCK_BASE_URL", "").strip())
    if not had_openai_key and not has_bedrock:
        os.environ["OPENAI_API_KEY"] = "offline-check-only"
    try:
        agent = OpenClawPiLangChain(config)
        try:
            return {
                "status": "ok",
                "model_route": _model_route() if had_openai_key or has_bedrock else "offline-check",
                "workspace": str(config.workspace_path()),
                "session": str(session_id),
                "user_id": config.user_id,
                "tool_count": len(agent.all_tools),
                "tools": sorted(str(getattr(tool, "name", "")) for tool in agent.all_tools),
                "skills": [row["id"] for row in agent.list_skills()],
                "memory": config.enable_memory,
                "mcp": config.mcp_enabled,
                "workspace_extensions": config.workspace_extensions_enabled,
            }
        finally:
            agent.close()
    finally:
        if not had_openai_key and not has_bedrock:
            os.environ.pop("OPENAI_API_KEY", None)


class ColoredChatCallbacks(PiCallbacks):
    """
    터미널에서 실행 상태와 AI 응답을 색상으로 구분해 보여주는 콜백
    """

    def __init__(self):
        self.is_first_chunk = True
        self.has_any_output = False
        self.turn_index = 0
        self.turn_started_at = 0.0
        self.tool_started_at: dict[str, float] = {}
        self.tool_count = 0
        self.error_count = 0

    def start_turn(self, *, prompt: str, session_id: str, skill_mode: str, skill_name: str | None, plan_mode: str) -> None:
        self.turn_index += 1
        self.turn_started_at = time.perf_counter()
        self.tool_started_at = {}
        self.tool_count = 0
        self.error_count = 0
        self.is_first_chunk = True
        self.has_any_output = False
        prompt_preview = _preview_text(prompt, 120)
        skill_state = skill_name or skill_mode
        sys.stdout.write(
            f"\n{Fore.MAGENTA}{Style.BRIGHT}[Turn {self.turn_index}]"
            f"{Style.RESET_ALL} session={session_id} skill={skill_state} plan={plan_mode}\n"
            f"{Fore.MAGENTA}[Stage 1/5] Received request{Style.RESET_ALL}: {prompt_preview}\n"
            f"{Fore.MAGENTA}[Stage 2/5] Preparing context, memory, skills, and tools...{Style.RESET_ALL}\n"
        )
        sys.stdout.flush()

    def finish_turn(self, result: Any) -> None:
        elapsed = _format_duration(time.perf_counter() - self.turn_started_at) if self.turn_started_at else "-"
        audit = getattr(result, "audit_file", None)
        audit_text = f" audit={audit}" if audit else ""
        sys.stdout.write(
            f"{Fore.MAGENTA}[Stage 5/5] Turn finished{Style.RESET_ALL} "
            f"elapsed={elapsed} tools={self.tool_count} errors={self.error_count}{audit_text}\n"
        )
        sys.stdout.flush()

    def on_partial_reply(self, text: str) -> None:
        if self.is_first_chunk:
            sys.stdout.write(
                f"\n{Fore.GREEN}[Stage 4/5] Composing answer{Style.RESET_ALL}\n"
                f"{Fore.GREEN}{Style.BRIGHT}Pi:{Style.RESET_ALL} "
            )
            self.is_first_chunk = False
        self.has_any_output = True
        sys.stdout.write(f"{Fore.GREEN}{text}{Style.RESET_ALL}")
        sys.stdout.flush()

    def on_tool_start(self, tool_name: str, args: dict[str, Any]) -> None:
        self.tool_count += 1
        call_key = f"{self.tool_count}:{tool_name}"
        self.tool_started_at[call_key] = time.perf_counter()
        sys.stdout.write(
            f"\n{Fore.YELLOW}[Stage 3/5] Tool #{self.tool_count} started"
            f"{Style.RESET_ALL}: {tool_name}\n"
            f"{Fore.YELLOW}  args:{Style.RESET_ALL} {_summarize_payload(args)}\n"
        )
        sys.stdout.flush()

    def on_tool_end(self, tool_name: str, output: str, is_error: bool) -> None:
        if is_error:
            self.error_count += 1
        elapsed = "-"
        for key in reversed(list(self.tool_started_at.keys())):
            if key.endswith(f":{tool_name}"):
                elapsed = _format_duration(time.perf_counter() - self.tool_started_at.pop(key))
                break
        color = Fore.RED if is_error else Fore.CYAN
        status = "ERROR" if is_error else "SUCCESS"
        sys.stdout.write(
            f"{color}[Tool {status}]{Style.RESET_ALL} {tool_name} elapsed={elapsed}\n"
            f"{color}  output:{Style.RESET_ALL} {_preview_text(output)}\n"
        )
        sys.stdout.flush()

    def on_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "custom":
            sys.stdout.write(
                f"\n{Fore.MAGENTA}[Runtime] {payload.get('message', payload)}"
                f"{Style.RESET_ALL}\n"
            )
            sys.stdout.flush()


def _execute_turn(
    session: PiAgentSession,
    callbacks: ColoredChatCallbacks,
    prompt: str,
    *,
    skill_mode: str,
    skill_name: Optional[str],
    plan_mode: str,
) -> Any:
    callbacks.start_turn(
        prompt=prompt,
        session_id=session.session_id,
        skill_mode=skill_mode,
        skill_name=skill_name,
        plan_mode=plan_mode,
    )
    result = session.prompt(prompt)
    if not callbacks.has_any_output:
        fallback = (result.final_text or "").strip() or "(응답이 비어 있습니다)"
        print(
            f"\n{Fore.GREEN}[Stage 4/5] Final response received{Style.RESET_ALL}\n"
            f"{Fore.GREEN}{Style.BRIGHT}Pi:{Style.RESET_ALL} {Fore.GREEN}{fallback}{Style.RESET_ALL}"
        )
    callbacks.finish_turn(result)
    print()
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    config = build_config(args)

    if args.mode == "edit" and not args.edit_path:
        print(f"{Fore.RED}--mode edit requires at least one --edit-path{Style.RESET_ALL}")
        return 2

    if args.check:
        try:
            print(json.dumps(run_check(config, str(args.session)), ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:
            print(f"{Fore.RED}진단 실패: {exc}{Style.RESET_ALL}")
            return 2

    try:
        one_shot_prompt = (
            _read_prompt_file(config, str(args.prompt_file))
            if str(args.prompt_file).strip()
            else str(args.once).strip()
        )
        if str(args.prompt_file).strip() and not one_shot_prompt:
            raise ValueError("prompt file is empty")
    except ValueError as exc:
        print(f"{Fore.RED}프롬프트 입력 실패: {exc}{Style.RESET_ALL}")
        return 2

    try:
        agent = OpenClawPiLangChain(config)
    except Exception as e:
        print(f"{Fore.RED}에이전트 초기화 실패: {e}{Style.RESET_ALL}")
        print(
            f"{Fore.YELLOW}힌트: OPENAI_API_KEY(또는 선택한 모델 제공자) 설정을 확인하세요."
            f"{Style.RESET_ALL}"
        )
        return 2

    session_id = str(args.session or "chat_main")
    callbacks = ColoredChatCallbacks()
    current_skill_mode = str(args.skill_mode or "auto")
    current_skill_name = str(args.skill).strip() or None
    current_plan_mode = _normalize_plan_mode(str(args.plan_mode))
    allowlist = [*_safe_csv_env("PI_ALLOW_TOOL", []), *list(args.allow_tool)]
    denylist = [*_safe_csv_env("PI_DENY_TOOL", []), *list(args.deny_tool)]
    if current_skill_name:
        matched_skill = _match_skill_row(agent.list_skills(), current_skill_name)
        if matched_skill is None:
            print(f"{Fore.RED}알 수 없는 스킬: {current_skill_name}{Style.RESET_ALL}")
            agent.close()
            return 2
        current_skill_name = str(matched_skill["id"])
    session = PiAgentSession(
        agent,
        session_id=session_id,
        callbacks=callbacks,
        skill_name=current_skill_name,
        skill_mode=current_skill_mode,
        plan_mode=current_plan_mode,
        allowlist=allowlist,
        denylist=denylist,
        permission_profile=args.mode,
        edit_paths=args.edit_path,
    )

    if one_shot_prompt:
        try:
            result = _execute_turn(
                session,
                callbacks,
                one_shot_prompt,
                skill_mode=current_skill_mode,
                skill_name=current_skill_name,
                plan_mode=current_plan_mode,
            )
            return 3 if result.awaiting_user_input else 0
        except Exception as exc:
            print(f"{Fore.RED}에이전트 실행 실패: {exc}{Style.RESET_ALL}")
            return 1
        finally:
            session.close()

    print(f"{Fore.CYAN}{Style.BRIGHT}==================================================")
    print("OpenClaw Pi Interactive Chat Mode Started")
    print(f"Workspace: {config.workspace_dir}")
    print(f"Model route: {_model_route()}")
    print(f"Model: {os.getenv('LOCAL_BEDROCK_MODEL_ID', '').strip() or config.model}")
    print(f"Session: {session_id}")
    if config.user_id:
        print(f"User ID: {config.user_id}")
    print(f"Skill mode: {current_skill_mode}")
    print(f"Permission mode: {session.permission_profile}")
    print(f"Plan mode: {current_plan_mode}")
    if config.enable_work_notes:
        print(f"Work notes: {config.work_note_artifact_dir}")
    print("메시지를 입력하세요. 종료하려면 'exit' 또는 'quit' 입력.")
    print("Commands: /help, /status, /mode, /skills, /tools, /skill, /plan, /session, /last")
    print(f"재실행 1회 승인 토큰: {config.repeat_confirm_token}")
    print(f"=================================================={Style.RESET_ALL}\n")

    while True:
        try:
            # 사용자 입력
            user_input = _read_console_input(
                f"\n{Fore.CYAN}{Style.BRIGHT}You: {Style.RESET_ALL}"
            )
            stripped = user_input.strip()
            if not stripped:
                continue

            low = stripped.lower()

            if low in ["exit", "quit", ":q"]:
                print(
                    f"\n{Fore.YELLOW}채팅을 종료합니다. 안녕히 계세요!{Style.RESET_ALL}"
                )
                break

            if low == "/help":
                _print_help()
                continue

            if low == "/status":
                state = current_skill_name or ("(auto)" if current_skill_mode == "auto" else "(none)")
                print(f"{Fore.CYAN}Status:{Style.RESET_ALL}")
                print(f"  session: {session_id}")
                print(f"  workspace: {config.workspace_dir}")
                print(f"  model route: {_model_route()}")
                print(f"  model: {os.getenv('LOCAL_BEDROCK_MODEL_ID', '').strip() or config.model}")
                print(f"  user: {config.user_id or '-'}")
                print(f"  skill mode: {current_skill_mode}")
                print(f"  skill: {state}")
                print(f"  permission mode: {session.permission_profile}")
                print(f"  edit paths: {', '.join(session.edit_paths) or '-'}")
                print(f"  plan mode: {current_plan_mode}")
                print(f"  memory: {'on' if config.enable_memory else 'off'} ({config.memory_mode})")
                print(f"  tools: {len(agent.all_tools)}")
                print(f"  session state: {json.dumps(session.state(), ensure_ascii=False)}")
                continue

            if low == "/tools":
                active_tools = agent._filter_tools(
                    allowlist=session.allowlist,
                    denylist=session.denylist,
                )
                active_tools = agent._apply_permission_profile_to_tools(
                    active_tools,
                    session.permission_profile,
                )
                effective_plan = "on" if session.permission_profile == "review" else session.plan_mode
                active_tools = agent._apply_plan_policy_to_tools(
                    active_tools,
                    agent._resolve_plan_policy(effective_plan),
                )
                names = sorted(str(getattr(tool, "name", "")) for tool in active_tools)
                print(f"{Fore.CYAN}Active tools ({len(names)}):{Style.RESET_ALL}")
                for name in names:
                    print(f"  - {name}")
                continue

            if low.startswith("/mode"):
                parts = stripped.split(maxsplit=2)
                mode = parts[1].strip().lower() if len(parts) > 1 else ""
                if not mode:
                    print(
                        f"{Fore.CYAN}Mode={session.permission_profile}, "
                        f"edit paths={', '.join(session.edit_paths) or '-'}{Style.RESET_ALL}"
                    )
                    continue
                if mode not in PERMISSION_PROFILES:
                    print(f"{Fore.YELLOW}Usage: /mode review|full|edit <path1,path2>{Style.RESET_ALL}")
                    continue
                edit_paths = None
                if mode == "edit":
                    edit_paths = [item.strip() for item in (parts[2] if len(parts) > 2 else "").split(",") if item.strip()]
                    if not edit_paths:
                        print(f"{Fore.YELLOW}Usage: /mode edit <path1,path2>{Style.RESET_ALL}")
                        continue
                try:
                    session.set_permission_profile(mode, edit_paths=edit_paths)
                except ValueError as exc:
                    print(f"{Fore.YELLOW}{exc}{Style.RESET_ALL}")
                    continue
                agent.audit_logger.log(
                    session_id,
                    "permission_profile_switched",
                    {"mode": session.permission_profile, "edit_paths": session.edit_paths},
                )
                print(
                    f"{Fore.CYAN}Permission mode changed to {session.permission_profile}; "
                    f"edit paths={', '.join(session.edit_paths) or '-'}{Style.RESET_ALL}"
                )
                continue

            if low.startswith("/session"):
                parts = stripped.split(maxsplit=1)
                arg = parts[1].strip() if len(parts) > 1 else ""
                if not arg:
                    print(f"{Fore.CYAN}Session={session_id}{Style.RESET_ALL}")
                    continue
                try:
                    new_session_id = _normalize_session_id(arg)
                except ValueError as exc:
                    print(f"{Fore.YELLOW}{exc}{Style.RESET_ALL}")
                    continue
                old_session_id = session_id
                session_id = new_session_id
                session.session_id = new_session_id
                session.last_result = None
                agent.audit_logger.log(
                    new_session_id,
                    "session_switched",
                    {"from": old_session_id, "to": new_session_id},
                )
                print(f"{Fore.CYAN}Session changed to {new_session_id}{Style.RESET_ALL}")
                continue

            if low == "/last":
                if session.last_result is None:
                    print(f"{Fore.YELLOW}No answer has been produced in this session yet.{Style.RESET_ALL}")
                else:
                    print(f"{Fore.GREEN}{session.last_result.final_text}{Style.RESET_ALL}")
                continue

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
                    session.set_skill_auto()
                    agent.audit_logger.log(
                        session_id, "skill_switched", {"mode": "auto", "skill": None}
                    )
                    print(f"{Fore.CYAN}Skill mode changed to auto{Style.RESET_ALL}")
                    continue
                if low == "off":
                    current_skill_mode = "off"
                    current_skill_name = None
                    session.set_skill(None, mode="off")
                    agent.audit_logger.log(
                        session_id, "skill_switched", {"mode": "off", "skill": None}
                    )
                    print(f"{Fore.CYAN}Skill mode changed to off{Style.RESET_ALL}")
                    continue
                matched_skill = _match_skill_row(agent.list_skills(), arg)
                if matched_skill is None:
                    print(f"{Fore.YELLOW}Unknown skill: {arg}. Use /skills to list names.{Style.RESET_ALL}")
                    continue
                current_skill_mode = "manual"
                current_skill_name = str(matched_skill["id"])
                session.set_skill(current_skill_name, mode=current_skill_mode)
                agent.audit_logger.log(
                    session_id, "skill_switched", {"mode": "manual", "skill": current_skill_name}
                )
                print(f"{Fore.CYAN}Skill pinned: {current_skill_name}{Style.RESET_ALL}")
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
                session.set_plan_mode(current_plan_mode)
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
                    session.set_skill(current_skill_name, mode=current_skill_mode)
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

            _execute_turn(
                session,
                callbacks,
                user_input,
                skill_mode=current_skill_mode,
                skill_name=current_skill_name,
                plan_mode=current_plan_mode,
            )

        except (KeyboardInterrupt, EOFError):
            print(f"\n{Fore.YELLOW}강제 종료 (Ctrl+C). 안녕히 계세요!{Style.RESET_ALL}")
            break
        except Exception as e:
            print(f"\n{Fore.RED}예외 발생: {e}{Style.RESET_ALL}")

    agent.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
