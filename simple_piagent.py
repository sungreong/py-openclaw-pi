from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Optional, Sequence

from piagent import (
    PERMISSION_PROFILES,
    ConsoleCallbacks,
    OpenClawPiLangChain,
    PiAgentConfig,
    WorkspaceGuard,
)


_MAX_PROMPT_FILE_BYTES = 1_000_000


def build_config(workspace: str) -> PiAgentConfig:
    workspace_path = Path(workspace).resolve()
    state_root = workspace_path / ".openclaw_pi" / "simple"
    return PiAgentConfig(
        model=os.getenv("PI_MODEL", "gpt-4o"),
        workspace_dir=str(workspace_path),
        session_dir=str(state_root / "sessions"),
        audit_dir=str(state_root / "audit"),
        memory_dir=str(state_root / "memory"),
        memory_search_backend="hash",
        memory_embedding_provider="hash",
        allow_package_install=os.getenv("PI_ALLOW_PACKAGE_INSTALL", "false").lower()
        in {"1", "true", "yes", "on"},
        package_install_allowlist=[
            item.strip()
            for item in os.getenv("PI_PACKAGE_INSTALL_ALLOWLIST", "").split(",")
            if item.strip()
        ],
        package_install_timeout_s=max(10, int(os.getenv("PI_PACKAGE_INSTALL_TIMEOUT", "180"))),
        workspace_extensions_enabled=os.getenv("PI_WORKSPACE_EXTENSIONS_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"},
        workspace_extension_dir=(os.getenv("PI_WORKSPACE_EXTENSION_DIR", ".piagent").strip() or ".piagent"),
        mcp_enabled=False,
        skills_enabled=True,
        skills_dir="skills",
        enable_subagents=True,
        hooks_config_path=str(state_root / "pi_hooks.json"),
    )


def run_check(config: PiAgentConfig) -> dict[str, object]:
    had_openai_key = bool(os.getenv("OPENAI_API_KEY", "").strip())
    has_bedrock = bool(os.getenv("LOCAL_BEDROCK_BASE_URL", "").strip())
    if not had_openai_key and not has_bedrock:
        os.environ["OPENAI_API_KEY"] = "offline-check-only"

    try:
        agent = OpenClawPiLangChain(config)
        try:
            tools = sorted(str(getattr(tool, "name", "")).strip() for tool in agent.all_tools)
            skills = [row["id"] for row in agent.list_skills()]
            return {
                "status": "ok",
                "model_route": "local-bedrock" if has_bedrock else ("openai" if had_openai_key else "offline-check"),
                "workspace": str(config.workspace_path()),
                "tool_count": len(tools),
                "tools": tools,
                "skills": skills,
                "mcp_enabled": config.mcp_enabled,
                "memory_backend": config.memory_search_backend,
                "workspace_extensions_enabled": config.workspace_extensions_enabled,
                "package_install_enabled": config.allow_package_install,
                "package_install_allowlist": list(config.package_install_allowlist),
            }
        finally:
            agent.close()
    finally:
        if not had_openai_key and not has_bedrock:
            os.environ.pop("OPENAI_API_KEY", None)


def read_prompt_file(config: PiAgentConfig, raw_path: str) -> str:
    guard = WorkspaceGuard(config.workspace_path(), config.blocked_paths)
    prompt_path = guard.resolve(str(raw_path))
    if not prompt_path.is_file():
        raise ValueError(f"prompt file not found: {raw_path}")
    if prompt_path.stat().st_size > _MAX_PROMPT_FILE_BYTES:
        raise ValueError("prompt file exceeds the 1 MB safety limit")
    try:
        return prompt_path.read_text(encoding="utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("prompt file must be UTF-8 encoded") from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Minimal PiAgent runner with builtin tools")
    parser.add_argument("prompt", nargs="?", default="")
    parser.add_argument("--prompt-file", default="", help="Read a UTF-8 prompt file inside the workspace")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--session", default="simple")
    parser.add_argument(
        "--mode",
        choices=PERMISSION_PROFILES,
        default=os.getenv("PI_MODE", "review").strip().lower() or "review",
        help="review=read/plan, edit=scoped partial edits, full=all configured tools",
    )
    parser.add_argument(
        "--edit-path",
        action="append",
        default=[item.strip() for item in os.getenv("PI_EDIT_PATHS", "").split(",") if item.strip()],
        help="Existing file that edit mode may modify; repeatable",
    )
    parser.add_argument("--skill", default="", help="Select a discovered skill by name")
    parser.add_argument(
        "--workspace-extensions",
        action="store_true",
        default=os.getenv("PI_WORKSPACE_EXTENSIONS_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"},
        help="Load trusted .piagent/skills/*/SKILL.md and .piagent/tools/*/tool.py extensions",
    )
    parser.add_argument(
        "--max-model-calls",
        type=int,
        default=max(1, int(os.getenv("PI_MAX_MODEL_CALLS", "16"))),
        help="Maximum model calls for this run",
    )
    parser.add_argument("--check", action="store_true", help="List the active tools without calling a model")
    args = parser.parse_args(argv)

    config = build_config(args.workspace)
    config.workspace_extensions_enabled = bool(args.workspace_extensions)
    config.max_model_calls = max(1, int(args.max_model_calls))
    if args.check:
        print(json.dumps(run_check(config), ensure_ascii=False, indent=2))
        return 0

    if str(args.prompt).strip() and str(args.prompt_file).strip():
        parser.error("use either a prompt argument or --prompt-file, not both")
    try:
        prompt = (
            read_prompt_file(config, str(args.prompt_file))
            if str(args.prompt_file).strip()
            else (str(args.prompt).strip() or input("You > ").strip())
        )
    except ValueError as exc:
        parser.error(str(exc))
    if not prompt:
        parser.error("prompt is required")
    if args.mode == "edit" and not args.edit_path:
        parser.error("--mode edit requires at least one --edit-path")
    if not os.getenv("OPENAI_API_KEY", "").strip() and not os.getenv("LOCAL_BEDROCK_BASE_URL", "").strip():
        parser.error("set OPENAI_API_KEY or all LOCAL_BEDROCK_* variables before running a prompt")

    agent = OpenClawPiLangChain(config)
    try:
        result = agent.run(
            session_id=str(args.session),
            prompt=prompt,
            callbacks=ConsoleCallbacks(),
            skill_name=(str(args.skill).strip() or None),
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
