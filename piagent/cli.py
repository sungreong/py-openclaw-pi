from __future__ import annotations

from .deps import *
from .models import *
from .workspace import WorkspaceGuard
from .stores import *
from .utils import *
from .mcp import McpStdioClient
from .permissions import PERMISSION_PROFILES

from .agent_core import OpenClawPiLangChain

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenClaw Pi-like agent rebuilt with LangChain")
    env_blocked = _split_csv(os.getenv("PI_BLOCKED_PATHS", ""))
    env_custom_modules = _split_csv(os.getenv("PI_CUSTOM_TOOL_MODULES", ""))
    mcp_enabled_default = os.getenv("PI_MCP_ENABLED", "true").lower() not in {"0", "false", "no"}
    skills_enabled_default = os.getenv("PI_SKILLS_ENABLED", "true").lower() not in {"0", "false", "no"}
    default_blocked = env_blocked or list(DEFAULT_BLOCKED_PATHS)
    parser.add_argument("prompt", nargs="?", default="", help="user prompt")
    parser.add_argument("--model", default=os.getenv("PI_MODEL", "gpt-4o"))
    parser.add_argument("--workspace", default=os.getenv("PI_WORKSPACE", "."))
    parser.add_argument("--session", default=os.getenv("PI_SESSION", "main"))
    parser.add_argument("--user-id", default=os.getenv("PI_USER_ID", ""))
    parser.add_argument(
        "--mode",
        choices=PERMISSION_PROFILES,
        default=os.getenv("PI_MODE", "review").strip().lower() or "review",
        help="review=read/plan, edit=scoped partial edits, full=all configured tools",
    )
    parser.add_argument(
        "--edit-path",
        action="append",
        default=_split_csv(os.getenv("PI_EDIT_PATHS", "")),
        help="Existing file that edit mode may modify; repeatable",
    )
    parser.add_argument("--session-dir", default=os.getenv("PI_SESSION_DIR", ".openclaw_pi/sessions"))
    parser.add_argument("--audit-dir", default=os.getenv("PI_AUDIT_DIR", ".openclaw_pi/audit"))
    parser.add_argument("--max-model-calls", type=int, default=int(os.getenv("PI_MAX_MODEL_CALLS", "16")))
    parser.add_argument("--tool-repeat-limit", type=int, default=int(os.getenv("PI_TOOL_REPEAT_LIMIT", "3")))
    parser.add_argument(
        "--session-evidence-limit",
        type=int,
        default=int(os.getenv("PI_SESSION_EVIDENCE_LIMIT", "6")),
    )
    parser.add_argument(
        "--session-evidence-summary-chars",
        type=int,
        default=int(os.getenv("PI_SESSION_EVIDENCE_SUMMARY_CHARS", "220")),
    )
    parser.add_argument(
        "--repeat-guard-enabled",
        default=os.getenv("PI_REPEAT_GUARD_ENABLED", "true"),
    )
    parser.add_argument(
        "--repeat-confirm-token",
        default=os.getenv("PI_REPEAT_CONFIRM_TOKEN", "[재실행 승인]"),
    )
    parser.add_argument("--exec-timeout", type=int, default=int(os.getenv("PI_EXEC_TIMEOUT", "60")))
    parser.add_argument("--deny-tool", action="append", default=[t.strip() for t in os.getenv("PI_DENY_TOOL", "").split(",")] if os.getenv("PI_DENY_TOOL") else [])
    parser.add_argument("--allow-tool", action="append", default=[t.strip() for t in os.getenv("PI_ALLOW_TOOL", "").split(",")] if os.getenv("PI_ALLOW_TOOL") else [])
    parser.add_argument("--no-write", action="store_true", default=os.getenv("PI_NO_WRITE", "false").lower() == "true")
    parser.add_argument("--no-shell", action="store_true", default=os.getenv("PI_NO_SHELL", "false").lower() == "true")
    parser.add_argument(
        "--allow-package-install",
        action="store_true",
        default=os.getenv("PI_ALLOW_PACKAGE_INSTALL", "false").lower() in {"1", "true", "yes", "on"},
    )
    parser.add_argument(
        "--package-install-allowlist",
        action="append",
        default=_split_csv(os.getenv("PI_PACKAGE_INSTALL_ALLOWLIST", "")),
        help="Allow one PyPI name or exact pin; repeatable (example: python-docx==1.2.0)",
    )
    parser.add_argument(
        "--package-install-timeout",
        type=int,
        default=int(os.getenv("PI_PACKAGE_INSTALL_TIMEOUT", "180")),
    )
    parser.add_argument("--no-compaction", action="store_true", default=os.getenv("PI_NO_COMPACTION", "false").lower() == "true")
    parser.add_argument("--no-memory", action="store_true", default=os.getenv("PI_NO_MEMORY", "false").lower() == "true")
    parser.add_argument("--memory-mode", default=os.getenv("PI_MEMORY_MODE", "openclaw"))
    parser.add_argument("--memory-limit", type=int, default=int(os.getenv("PI_MEMORY_LIMIT", "200")))
    parser.add_argument("--memory-recall-limit", type=int, default=int(os.getenv("PI_MEMORY_RECALL_LIMIT", "5")))
    parser.add_argument("--memory-dir", default=os.getenv("PI_MEMORY_DIR", ".openclaw/memory"))
    parser.add_argument("--memory-search-backend", default=os.getenv("PI_MEMORY_SEARCH_BACKEND", "sqlite-vec"))
    parser.add_argument("--memory-embedding-provider", default=os.getenv("PI_MEMORY_EMBEDDING_PROVIDER", "auto"))
    parser.add_argument("--memory-embedding-model", default=os.getenv("PI_MEMORY_EMBEDDING_MODEL", "text-embedding-3-small"))
    parser.add_argument("--read-strategy", default=os.getenv("PI_READ_STRATEGY", "smart"))
    parser.add_argument(
        "--custom-tool-module",
        action="append",
        default=env_custom_modules,
        help="Python module reference or .py file path to load custom tools (repeatable)",
    )
    parser.add_argument(
        "--workspace-extensions",
        action="store_true",
        default=os.getenv("PI_WORKSPACE_EXTENSIONS_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        help="Load trusted .piagent/skills/*/SKILL.md and .piagent/tools/*/tool.py extensions",
    )
    parser.add_argument("--workspace-extension-dir", default=os.getenv("PI_WORKSPACE_EXTENSION_DIR", ".piagent"))
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        default=False,
        help="Disable MCP server tool loading",
    )
    parser.add_argument("--mcp-enabled", action="store_true", default=mcp_enabled_default)
    parser.add_argument("--mcp-config", default=os.getenv("PI_MCP_CONFIG", "mcp_servers.json"))
    parser.add_argument(
        "--mcp-fail-fast",
        action="store_true",
        default=os.getenv("PI_MCP_FAIL_FAST", "false").lower() in {"1", "true", "yes"},
        help="Fail startup when any enabled MCP server fails to connect",
    )
    parser.add_argument("--mcp-timeout", type=int, default=int(os.getenv("PI_MCP_TIMEOUT", "20")))
    parser.add_argument(
        "--no-skills",
        action="store_true",
        default=False,
    )
    parser.add_argument("--skills-enabled", action="store_true", default=skills_enabled_default)
    parser.add_argument("--skills-dir", default=os.getenv("PI_SKILLS_DIR", "skills"))
    parser.add_argument("--skill-mode", default=os.getenv("PI_SKILL_MODE", "auto"), choices=["auto", "manual", "off"])
    parser.add_argument("--skill", default=os.getenv("PI_SKILL", ""))
    parser.add_argument("--plan-mode", default=os.getenv("PI_PLAN_MODE", "off"), choices=["on", "off"])
    parser.add_argument(
        "--permission-mode",
        default=os.getenv("PI_PERMISSION_MODE", "default"),
        choices=["default", "plan", "accept_edits", "dont_ask"],
    )
    parser.add_argument(
        "--no-subagents",
        action="store_true",
        default=os.getenv("PI_NO_SUBAGENTS", "false").lower() in {"1", "true", "yes"},
    )
    parser.add_argument("--max-tool-result-chars", type=int, default=int(os.getenv("PI_MAX_TOOL_RESULT_CHARS", "24000")))
    parser.add_argument("--tool-result-artifact-dir", default=os.getenv("PI_TOOL_RESULT_ARTIFACT_DIR", "tool-results"))
    parser.add_argument(
        "--no-session-notes",
        action="store_true",
        default=os.getenv("PI_NO_SESSION_NOTES", "false").lower() in {"1", "true", "yes"},
    )
    parser.add_argument(
        "--no-work-notes",
        action="store_true",
        default=os.getenv("PI_NO_WORK_NOTES", "false").lower() in {"1", "true", "yes"},
    )
    parser.add_argument("--work-note-artifact-dir", default=os.getenv("PI_WORK_NOTE_ARTIFACT_DIR", "work-notes"))
    parser.add_argument(
        "--no-work-note-auto-update",
        action="store_true",
        default=os.getenv("PI_NO_WORK_NOTE_AUTO_UPDATE", "false").lower() in {"1", "true", "yes"},
    )
    parser.add_argument("--hooks-config", default=os.getenv("PI_HOOKS_CONFIG", "pi_hooks.json"))
    parser.add_argument("--list-skills", action="store_true")
    parser.add_argument(
        "--exec-path-correction",
        action="store_true",
        default=os.getenv("PI_EXEC_PATH_CORRECTION", "false").lower() == "true",
    )
    parser.add_argument("--blocked-path", action="append", default=default_blocked)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.mode == "edit" and not args.edit_path:
        raise SystemExit("--mode edit requires at least one --edit-path")
    config = PiAgentConfig(
        model=args.model,
        workspace_dir=args.workspace,
        session_dir=args.session_dir,
        audit_dir=args.audit_dir,
        max_model_calls=args.max_model_calls,
        tool_repeat_limit=max(1, int(args.tool_repeat_limit)),
        session_evidence_limit=max(1, int(args.session_evidence_limit)),
        session_evidence_summary_chars=max(60, int(args.session_evidence_summary_chars)),
        repeat_guard_enabled=_to_bool(args.repeat_guard_enabled, default=True),
        repeat_confirm_token=(str(args.repeat_confirm_token).strip() or "[재실행 승인]"),
        exec_timeout_s=args.exec_timeout,
        allow_write=not args.no_write,
        allow_shell=not args.no_shell,
        allow_package_install=bool(args.allow_package_install),
        package_install_allowlist=[x for x in args.package_install_allowlist if str(x).strip()],
        package_install_timeout_s=max(10, int(args.package_install_timeout)),
        enable_compaction=not args.no_compaction,
        enable_memory=not args.no_memory,
        memory_mode=args.memory_mode,
        memory_limit=max(1, args.memory_limit),
        memory_recall_limit=max(1, args.memory_recall_limit),
        memory_dir=args.memory_dir,
        memory_search_backend=args.memory_search_backend,
        memory_embedding_provider=args.memory_embedding_provider,
        memory_embedding_model=args.memory_embedding_model,
        read_strategy=args.read_strategy,
        custom_tool_modules=[x for x in (args.custom_tool_module or []) if str(x).strip()],
        workspace_extensions_enabled=bool(args.workspace_extensions),
        workspace_extension_dir=str(args.workspace_extension_dir).strip() or ".piagent",
        mcp_enabled=bool(args.mcp_enabled) and not args.no_mcp,
        mcp_config_path=args.mcp_config,
        mcp_fail_fast=args.mcp_fail_fast,
        mcp_timeout_s=max(1, int(args.mcp_timeout)),
        skills_enabled=bool(args.skills_enabled) and not args.no_skills,
        skills_dir=args.skills_dir,
        skill_mode=args.skill_mode,
        skill_name=(str(args.skill).strip() or None),
        plan_mode=args.plan_mode,
        permission_mode=args.permission_mode,
        enable_subagents=not args.no_subagents,
        max_tool_result_chars=max(1000, int(args.max_tool_result_chars)),
        tool_result_artifact_dir=str(args.tool_result_artifact_dir).strip() or "tool-results",
        enable_session_notes=not args.no_session_notes,
        enable_work_notes=not args.no_work_notes,
        work_note_artifact_dir=str(args.work_note_artifact_dir).strip() or "work-notes",
        work_note_auto_update=not args.no_work_note_auto_update,
        user_id=(str(args.user_id).strip() or None),
        hooks_config_path=str(args.hooks_config).strip() or "pi_hooks.json",
        enable_exec_path_correction=args.exec_path_correction,
        blocked_paths=[x for x in (args.blocked_path or []) if str(x).strip()],
    )
    agent = OpenClawPiLangChain(config)
    try:
        if args.list_skills:
            skills = agent.list_skills()
            if not skills:
                print("No skills found.")
            else:
                for row in skills:
                    print(
                        f"- {row['id']} :: {row['name']} | triggers={','.join(row['triggers']) or '-'} "
                        f"| required_tools={','.join(row['required_tools']) or '-'} "
                        f"| required_env={','.join(row['required_env']) or '-'}"
                    )
            return 0
        if not str(args.prompt).strip():
            raise SystemExit("prompt is required unless --list-skills is used")
        result = agent.run(
            session_id=args.session,
            prompt=args.prompt,
            callbacks=ConsoleCallbacks(),
            allowlist=args.allow_tool,
            denylist=args.deny_tool,
            skill_name=(str(args.skill).strip() or None),
            skill_mode=args.skill_mode,
            plan_mode=args.plan_mode,
            permission_profile=args.mode,
            edit_paths=args.edit_path,
        )
    finally:
        agent.close()
    print("\n\n--- final ---")
    print(result.final_text)
    if result.audit_file:
        print(f"\naudit: {result.audit_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = ["parse_args", "main"]
