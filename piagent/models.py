from __future__ import annotations

from .deps import *

@dataclass(slots=True)
class PiAgentConfig:
    model: str = "gpt-5"
    workspace_dir: str = "."
    session_dir: str = ".openclaw_pi/sessions"
    audit_dir: str = ".openclaw_pi/audit"
    temperature: float = 0.0
    max_tokens: Optional[int] = None
    max_model_calls: int = 16
    tool_repeat_limit: int = 3
    exec_timeout_s: int = 60
    allow_shell: bool = True
    allow_write: bool = True
    allow_package_install: bool = False
    package_install_allowlist: list[str] = field(default_factory=list)
    package_install_timeout_s: int = 180
    compact_after_messages: int = 24
    keep_last_messages: int = 8
    compaction_model: Optional[str] = None
    enable_compaction: bool = True
    enable_memory: bool = True
    memory_mode: str = "openclaw"
    memory_dir: str = ".openclaw/memory"
    memory_limit: int = 200
    memory_recall_limit: int = 5
    memory_search_backend: str = "sqlite-vec"
    memory_embedding_provider: str = "auto"
    memory_embedding_model: str = "text-embedding-3-small"
    enable_exec_path_correction: bool = False
    session_evidence_limit: int = 6
    session_evidence_summary_chars: int = 220
    repeat_guard_enabled: bool = True
    repeat_confirm_token: str = "[재실행 승인]"
    read_strategy: str = "smart"
    read_small_line_limit: int = 400
    read_small_char_limit: int = 16384
    read_preview_head_lines: int = 120
    read_preview_tail_lines: int = 80
    read_output_budget_chars: int = 20000
    custom_tool_modules: list[str] = field(default_factory=list)
    workspace_extensions_enabled: bool = False
    workspace_extension_dir: str = ".piagent"
    mcp_enabled: bool = True
    mcp_config_path: str = "mcp_servers.json"
    mcp_fail_fast: bool = False
    mcp_timeout_s: int = 20
    skills_enabled: bool = True
    skills_dir: str = "skills"
    skill_mode: str = "auto"
    skill_name: Optional[str] = None
    plan_mode: str = "off"
    permission_mode: Literal["default", "plan", "accept_edits", "dont_ask"] = "default"
    enable_subagents: bool = True
    max_tool_result_chars: int = 24000
    tool_result_artifact_dir: str = "tool-results"
    enable_session_notes: bool = True
    enable_work_notes: bool = True
    work_note_artifact_dir: str = "work-notes"
    work_note_auto_update: bool = True
    user_id: Optional[str] = None
    hooks_config_path: str = "pi_hooks.json"
    blocked_paths: list[str] = field(default_factory=lambda: list(DEFAULT_BLOCKED_PATHS))

    def workspace_path(self) -> Path:
        return Path(self.workspace_dir).resolve()

    def session_root(self) -> Path:
        return Path(self.session_dir).resolve()

    def audit_root(self) -> Path:
        return Path(self.audit_dir).resolve()

    def memory_root(self) -> Path:
        return Path(self.memory_dir).resolve()


@dataclass(slots=True)
class PiRunResult:
    session_id: str
    final_text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    audit_file: Optional[Path] = None
    awaiting_user_input: bool = False
    user_question: Optional[str] = None


@dataclass(slots=True)
class SkillSpec:
    id: str
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    required_env: list[str] = field(default_factory=list)
    tool_allow: list[str] = field(default_factory=list)
    tool_deny: list[str] = field(default_factory=list)
    api_policy: str = "tool_first"
    workflow: str = ""
    output_format: str = ""
    selection_hints: list[str] = field(default_factory=list)
    required_paths: list[str] = field(default_factory=list)
    execution_steps: list[str] = field(default_factory=list)
    tool_priority: list[str] = field(default_factory=list)
    source_path: str = ""


@dataclass(slots=True)
class PlanRuntimePolicy:
    mode: str = "off"
    forced_deny_tools: tuple[str, ...] = ()
    skip_skill_precheck_fail: bool = False
    disable_legacy_memory_write: bool = False
    planner_directive: str = ""


@dataclass(slots=True)
class HookSpec:
    hook_type: Literal["command", "prompt"]
    content: str
    timeout_s: int = 30
    name: str = ""


class PiCallbacks(Protocol):
    def on_partial_reply(self, text: str) -> None: ...

    def on_tool_start(self, tool_name: str, args: dict[str, Any]) -> None: ...

    def on_tool_end(self, tool_name: str, output: str, is_error: bool) -> None: ...

    def on_event(self, event_type: str, payload: dict[str, Any]) -> None: ...


class NullCallbacks:
    def on_partial_reply(self, text: str) -> None:
        pass

    def on_tool_start(self, tool_name: str, args: dict[str, Any]) -> None:
        pass

    def on_tool_end(self, tool_name: str, output: str, is_error: bool) -> None:
        pass

    def on_event(self, event_type: str, payload: dict[str, Any]) -> None:
        pass


class ConsoleCallbacks(NullCallbacks):
    def on_partial_reply(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def on_tool_start(self, tool_name: str, args: dict[str, Any]) -> None:
        sys.stdout.write(f"\n[tool:start] {tool_name} {json.dumps(args, ensure_ascii=False)}\n")
        sys.stdout.flush()

    def on_tool_end(self, tool_name: str, output: str, is_error: bool) -> None:
        state = "error" if is_error else "ok"
        preview = output[:400]
        sys.stdout.write(f"\n[tool:end] {tool_name} [{state}]\n{preview}\n")
        sys.stdout.flush()

    def on_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "custom":
            sys.stdout.write(f"\n[event] {payload.get('message', payload)}\n")
            sys.stdout.flush()

__all__ = [name for name in globals() if not name.startswith("__")]
