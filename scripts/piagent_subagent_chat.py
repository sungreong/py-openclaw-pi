from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional, Sequence, TextIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from piagent import PERMISSION_PROFILES, NullCallbacks, OpenClawPiLangChain, PiAgentSession  # noqa: E402
from simple_piagent import build_config  # noqa: E402


_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,128}")


def _reply_payload(turn: int, result: Any) -> dict[str, Any]:
    return {
        "type": "reply",
        "turn": turn,
        "session_id": str(result.session_id),
        "text": str(result.final_text),
        "tools": [str(item.get("name", "")) for item in result.tool_calls],
        "tool_errors": sum(1 for item in result.tool_results if item.get("is_error")),
        "awaiting_user_input": bool(result.awaiting_user_input),
        "user_question": result.user_question,
        "audit_file": str(result.audit_file) if result.audit_file else "",
    }


def _emit(stream: TextIO, payload: dict[str, Any]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stream.flush()


def _request_overrides(request: dict[str, Any]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for key in ("allowlist", "denylist"):
        if key not in request:
            continue
        value = request[key]
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError(f"{key} must be a list of non-empty strings")
        overrides[key] = [item.strip() for item in value]
    for key in ("skill_name", "skill_mode", "plan_mode"):
        if key in request and request[key] is not None:
            overrides[key] = str(request[key]).strip()
    if "mode" in request and request["mode"] is not None:
        overrides["permission_profile"] = str(request["mode"]).strip()
    path_key = "paths" if "paths" in request else "edit_paths" if "edit_paths" in request else None
    if path_key is not None:
        value = request[path_key]
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError(f"{path_key} must be a list of non-empty strings")
        overrides["edit_paths"] = [item.strip() for item in value]
    return overrides


def serve_jsonl(session: PiAgentSession, source: TextIO, sink: TextIO) -> int:
    """Serve one persistent PiAgent session over newline-delimited JSON."""
    turn = 0
    _emit(sink, {"type": "ready", "session_id": session.session_id})
    for raw_line in source:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _emit(sink, {"type": "error", "error": f"invalid JSON: {exc.msg}"})
            continue
        if not isinstance(request, dict):
            _emit(sink, {"type": "error", "error": "request must be a JSON object"})
            continue
        command = str(request.get("command", "")).strip().lower()
        if command in {"exit", "quit"}:
            _emit(sink, {"type": "closed", "session_id": session.session_id, "turns": turn})
            return 0
        if command == "state":
            _emit(sink, {"type": "state", **session.state(), "turns": turn})
            continue
        prompt = str(request.get("prompt", "")).strip()
        if not prompt:
            _emit(sink, {"type": "error", "error": "prompt is required"})
            continue
        turn += 1
        try:
            overrides = _request_overrides(request)
            result = session.prompt(prompt, **overrides) if turn == 1 else session.follow_up(prompt, **overrides)
            _emit(sink, _reply_payload(turn, result))
        except Exception as exc:
            _emit(sink, {"type": "error", "turn": turn, "error": f"{type(exc).__name__}: {exc}"})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent PiAgent subagent bridge")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--session", default="codex-piagent")
    parser.add_argument("--user-id", default="codex", help="Isolate session, audit, and memory state")
    parser.add_argument("--state-dir", default=".openclaw_pi/codex-subagent")
    parser.add_argument("--max-model-calls", type=int, default=8)
    parser.add_argument(
        "--mode",
        choices=PERMISSION_PROFILES,
        default="review",
        help="review=read/plan, edit=scoped partial edits, full=all configured tools",
    )
    parser.add_argument(
        "--edit-path",
        action="append",
        default=[],
        help="Existing file that edit mode may modify; repeat for multiple files",
    )
    parser.add_argument("--prompt", action="append", default=[], help="Run one or more prompts, in order")
    parser.add_argument("--jsonl", action="store_true", help="Use machine-readable stdin/stdout protocol")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = Path(args.workspace).resolve()
    session_id = str(args.session).strip()
    if not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise SystemExit("session must match [A-Za-z0-9_.-]{1,128}")
    user_id = str(args.user_id).strip()
    if not _SESSION_ID_PATTERN.fullmatch(user_id):
        raise SystemExit("user-id must match [A-Za-z0-9_.-]{1,128}")
    config = build_config(str(workspace))
    state_root = (workspace / str(args.state_dir)).resolve()
    try:
        state_root.relative_to(workspace)
    except ValueError as exc:
        raise SystemExit("state-dir must stay inside the workspace") from exc
    config.session_dir = str(state_root / "sessions")
    config.audit_dir = str(state_root / "audit")
    config.memory_dir = str(state_root / "memory")
    config.hooks_config_path = str(state_root / "pi_hooks.json")
    config.max_model_calls = max(1, int(args.max_model_calls))
    config.user_id = user_id

    if args.mode == "edit" and not args.edit_path:
        raise SystemExit("--mode edit requires at least one --edit-path")

    agent = OpenClawPiLangChain(config)
    session = PiAgentSession(
        agent,
        session_id=session_id,
        callbacks=NullCallbacks(),
        permission_profile=args.mode,
        edit_paths=args.edit_path,
    )
    try:
        if args.jsonl:
            return serve_jsonl(session, sys.stdin, sys.stdout)
        if args.prompt:
            for turn, prompt in enumerate(args.prompt, start=1):
                result = session.prompt(prompt) if turn == 1 else session.follow_up(prompt)
                _emit(sys.stdout, _reply_payload(turn, result))
            return 0
        print(f"PiAgent subagent ready: session={session.session_id}. /exit to quit.")
        turn = 0
        while True:
            try:
                prompt = input("Codex > ").strip()
            except EOFError:
                break
            if prompt.lower() in {"/exit", "/quit"}:
                break
            if not prompt:
                continue
            turn += 1
            result = session.prompt(prompt) if turn == 1 else session.follow_up(prompt)
            print(f"PiAgent > {result.final_text}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
