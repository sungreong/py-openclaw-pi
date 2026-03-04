# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Protocol, Sequence

from dotenv import load_dotenv

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, ToolMessage
from langchain.tools import tool
from langgraph.config import get_stream_writer

# 스크립트 실행 시 현재 디렉토리 또는 상위 디렉토리의 .env 파일을 찾아 환경 변수로 동적 할당합니다.
# override=True 로 설정하여, 도커 시동 시 잡혀있던 환경변수보다 수정된 .env 값이 우선하도록 합니다.
load_dotenv(override=True)


def _now_ts() -> float:
    return time.time()


@dataclass(slots=True)
class PiAgentConfig:
    model: str = "gpt-5"
    workspace_dir: str = "."
    session_dir: str = ".openclaw_pi/sessions"
    audit_dir: str = ".openclaw_pi/audit"
    temperature: float = 0.0
    max_tokens: Optional[int] = None
    max_model_calls: int = 16
    exec_timeout_s: int = 60
    allow_shell: bool = True
    allow_write: bool = True
    compact_after_messages: int = 24
    keep_last_messages: int = 8
    compaction_model: Optional[str] = None
    enable_compaction: bool = True

    def workspace_path(self) -> Path:
        return Path(self.workspace_dir).resolve()

    def session_root(self) -> Path:
        return Path(self.session_dir).resolve()

    def audit_root(self) -> Path:
        return Path(self.audit_dir).resolve()


@dataclass(slots=True)
class PiRunResult:
    session_id: str
    final_text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    audit_file: Optional[Path] = None


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


class WorkspaceGuard:
    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir.resolve()

    def resolve(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self.workspace_dir / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.workspace_dir)
        except ValueError as exc:
            raise ValueError(f"Path escapes workspace: {raw_path}") from exc
        return resolved


class FlatSessionStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, session_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", session_id)
        return self.root / f"{safe}.json"

    def load(self, session_id: str) -> list[dict[str, str]]:
        path = self.path_for(session_id)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        out: list[dict[str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            content = str(item.get("content", ""))
            if role and content is not None:
                out.append({"role": role, "content": content})
        return out

    def save(self, session_id: str, messages: Sequence[dict[str, str]]) -> Path:
        path = self.path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(list(messages), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path


class AuditLogger:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, session_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", session_id)
        return self.root / f"{safe}.jsonl"

    def log(self, session_id: str, event_type: str, payload: dict[str, Any]) -> Path:
        path = self.path_for(session_id)
        record = {
            "ts": _now_ts(),
            "type": event_type,
            "payload": payload,
        }
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path


def _shorten(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    keep = max(1, limit - 80)
    return text[:keep] + f"\n\n...[truncated {len(text) - keep} chars]"


def _text_from_content_blocks(blocks: Any) -> str:
    parts: list[str] = []
    if not isinstance(blocks, list):
        return ""
    for block in blocks:
        if isinstance(block, dict):
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block.get("content"), str):
                parts.append(block["content"])
        else:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def extract_text(message_or_chunk: Any) -> str:
    if message_or_chunk is None:
        return ""
    if isinstance(message_or_chunk, str):
        return message_or_chunk
    content = getattr(message_or_chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text = _text_from_content_blocks(content)
        if text:
            return text
    content_blocks = getattr(message_or_chunk, "content_blocks", None)
    if isinstance(content_blocks, list):
        text = _text_from_content_blocks(content_blocks)
        if text:
            return text
    return ""


class OpenClawPiLangChain:
    def __init__(
        self,
        config: PiAgentConfig,
        extra_tools: Optional[Sequence[Any]] = None,
    ):
        self.config = config
        self.workspace_dir = config.workspace_path()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.guard = WorkspaceGuard(self.workspace_dir)
        self.session_store = FlatSessionStore(config.session_root())
        self.audit_logger = AuditLogger(config.audit_root())

        self.model = init_chat_model(
            config.model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )
        self.compaction_model = init_chat_model(
            config.compaction_model or config.model,
            temperature=0,
            max_tokens=1200,
        )

        tools = self._build_default_tools()
        if extra_tools:
            tools.extend(extra_tools)
        self.all_tools = tools

    def _build_default_tools(self) -> list[Any]:
        guard = self.guard
        workspace_dir = self.workspace_dir
        exec_timeout_s = self.config.exec_timeout_s
        allow_write = self.config.allow_write
        allow_shell = self.config.allow_shell

        @tool("read")
        def read(path: str) -> str:
            """Read a UTF-8 text file from the workspace."""
            try:
                file_path = guard.resolve(path)
                if not file_path.exists():
                    return f"Error: File '{file_path}' not found."
                if file_path.is_dir():
                    return f"Error: '{file_path}' is a directory, not a file."
                return _shorten(file_path.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:
                return f"Error reading file '{path}': {e}"

        @tool("write")
        def write(path: str, content: str) -> str:
            """Create or overwrite a UTF-8 text file inside the workspace."""
            if not allow_write:
                return "Error: write tool is disabled"
            try:
                file_path = guard.resolve(path)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
                return f"wrote {len(content)} chars to {file_path.relative_to(workspace_dir)}"
            except Exception as e:
                return f"Error writing file '{path}': {e}"

        @tool("edit")
        def edit(path: str, old: str, new: str, replace_all: bool = False) -> str:
            """Edit a file by replacing one snippet with another."""
            if not allow_write:
                return "Error: edit tool is disabled"
            try:
                file_path = guard.resolve(path)
                text = file_path.read_text(encoding="utf-8", errors="replace")
                count = text.count(old)
                if count == 0:
                    return "Error: target snippet not found in the file."
                if count > 1 and not replace_all:
                    return f"Error: target snippet appears {count} times; set replace_all=true to replace all."
                updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
                file_path.write_text(updated, encoding="utf-8")
                return f"updated {file_path.relative_to(workspace_dir)}; replacements={count if replace_all else 1}"
            except Exception as e:
                return f"Error editing file '{path}': {e}"

        @tool("ls")
        def ls(path: str = ".") -> str:
            """List files and folders inside the workspace."""
            try:
                dir_path = guard.resolve(path)
                if not dir_path.exists():
                    return f"Error: Directory '{dir_path}' not found."
                if not dir_path.is_dir():
                    return f"Error: '{dir_path}' is not a directory."
                rows: list[str] = []
                for child in sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                    kind = "dir" if child.is_dir() else "file"
                    rel = child.relative_to(workspace_dir)
                    rows.append(f"[{kind}] {rel}")
                return "\n".join(rows[:1000]) if rows else "empty directory"
            except Exception as e:
                return f"Error listing directory '{path}': {e}"

        @tool("find")
        def find(glob: str = "**/*") -> str:
            """Find files by glob pattern inside the workspace."""
            try:
                rows = []
                for path in sorted(workspace_dir.glob(glob)):
                    if path.name.startswith(".git"):
                        continue
                    if path.is_file():
                        rows.append(str(path.relative_to(workspace_dir)))
                return "\n".join(rows[:2000]) if rows else "no matches"
            except Exception as e:
                return f"Error finding files for pattern '{glob}': {e}"

        @tool("grep")
        def grep(pattern: str, path: str = ".") -> str:
            """Search for a regex pattern in text files inside the workspace."""
            try:
                base = guard.resolve(path)
                try:
                    regex = re.compile(pattern)
                except re.error as reg_e:
                    return f"Error: Invalid regex pattern '{pattern}' - {reg_e}"
                
                hits: list[str] = []
                files: Iterable[Path]
                if base.is_file():
                    files = [base]
                else:
                    files = sorted(p for p in base.rglob("*") if p.is_file())
                
                for file_path in files:
                    rel = file_path.relative_to(workspace_dir)
                    try:
                        text = file_path.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue
                    for i, line in enumerate(text.splitlines(), start=1):
                        if regex.search(line):
                            hits.append(f"{rel}:{i}: {line}")
                            if len(hits) >= 500:
                                return "\n".join(hits)
                return "\n".join(hits) or "no matches"
            except Exception as e:
                return f"Error searching pattern '{pattern}': {e}"

        @tool("exec")
        def exec_tool(command: str, cwd: str = ".", timeout_s: int = exec_timeout_s) -> str:
            """Run a shell command inside the workspace and return stdout/stderr."""
            if not allow_shell:
                return "Error: exec tool is disabled"
            
            try:
                run_dir = guard.resolve(cwd)
                if not run_dir.is_dir():
                    return f"Error: Directory '{run_dir}' not found."

                writer: Optional[Callable[[str], None]]
                try:
                    writer = get_stream_writer()
                except Exception:
                    writer = None
                
                if writer:
                    writer(f"exec started: {command}")
                
                try:
                    completed = subprocess.run(
                        command,
                        cwd=str(run_dir),
                        shell=True,
                        text=True,
                        capture_output=True,
                        timeout=max(1, int(timeout_s)),
                        encoding="utf-8",
                        errors="replace",
                    )
                    
                    output = (
                        f"cwd={run_dir.relative_to(workspace_dir)}\n"
                        f"exit_code={completed.returncode}\n"
                        f"stdout:\n{completed.stdout}\n"
                        f"stderr:\n{completed.stderr}"
                    )
                    
                    if writer:
                        writer(f"exec finished: exit_code={completed.returncode}")
                    return _shorten(output, 24000)
                except subprocess.TimeoutExpired:
                    if writer:
                        writer(f"exec timed out after {timeout_s}s: {command}")
                    return f"Error: Command timed out after {timeout_s} seconds."
            except Exception as e:
                return f"Error executing command '{command}': {e}"

        return [read, write, edit, ls, find, grep, exec_tool]

    def _filter_tools(
        self,
        allowlist: Optional[Sequence[str]] = None,
        denylist: Optional[Sequence[str]] = None,
    ) -> list[Any]:
        allow = {name.strip().lower() for name in (allowlist or []) if name.strip()}
        deny = {name.strip().lower() for name in (denylist or []) if name.strip()}
        tools = self.all_tools
        if allow:
            tools = [tool_obj for tool_obj in tools if tool_obj.name.lower() in allow]
        if deny:
            tools = [tool_obj for tool_obj in tools if tool_obj.name.lower() not in deny]
        return tools

    def _build_system_prompt(self, tools: Sequence[Any], session_id: str) -> str:
        tool_lines = []
        for tool_obj in tools:
            description = getattr(tool_obj, "description", "") or ""
            description = " ".join(description.split())
            tool_lines.append(f"- {tool_obj.name}: {description}")

        tool_block = "\n".join(tool_lines)
        return (
            "You are Pi, a minimal coding agent inspired by OpenClaw's embedded Pi runtime.\n\n"
            "Behavior rules:\n"
            "1. Use tools instead of guessing.\n"
            "2. Read files before editing them unless the user explicitly asked for a fresh file.\n"
            "3. Prefer precise edits over full rewrites when possible.\n"
            "4. Stay inside the workspace unless the user explicitly expands scope.\n"
            "5. After tool use, summarize what you learned or changed.\n"
            "6. If a shell command fails, inspect the error and retry only when there is a clear fix.\n\n"
            f"Workspace: {self.workspace_dir}\n"
            f"Session ID: {session_id}\n\n"
            "Available tools:\n"
            f"{tool_block}"
        )

    def _history_to_text(self, messages: Sequence[dict[str, str]]) -> str:
        rows = []
        for message in messages:
            role = message["role"].upper()
            content = message["content"].strip()
            if not content:
                continue
            rows.append(f"{role}:\n{content}")
        return "\n\n".join(rows)

    def _compact_history(self, history: list[dict[str, str]], session_id: str) -> list[dict[str, str]]:
        if not self.config.enable_compaction:
            return history
        if len(history) <= self.config.compact_after_messages:
            return history
        head = history[: -self.config.keep_last_messages]
        tail = history[-self.config.keep_last_messages :]
        summary_prompt = [
            {
                "role": "system",
                "content": (
                    "Summarize this coding-agent conversation for future continuation. "
                    "Keep concrete facts only: goals, decisions, edited files, command results, "
                    "failures, and open questions. Use a compact bullet list."
                ),
            },
            {"role": "user", "content": self._history_to_text(head)},
        ]
        summary_response = self.compaction_model.invoke(summary_prompt)
        summary_text = extract_text(summary_response).strip()
        compacted = [
            {
                "role": "system",
                "content": "Conversation summary for continuation:\n" + summary_text,
            },
            *tail,
        ]
        self.audit_logger.log(
            session_id,
            "compaction",
            {
                "before_messages": len(history),
                "after_messages": len(compacted),
                "summary_chars": len(summary_text),
            },
        )
        return compacted

    def _create_agent(self, tools: Sequence[Any], system_prompt: str):
        middleware = [
            ModelCallLimitMiddleware(
                run_limit=self.config.max_model_calls,
                exit_behavior="end",
            )
        ]
        return create_agent(
            model=self.model,
            tools=list(tools),
            system_prompt=system_prompt,
            middleware=middleware,
        )

    def run(
        self,
        session_id: str,
        prompt: str,
        callbacks: Optional[PiCallbacks] = None,
        allowlist: Optional[Sequence[str]] = None,
        denylist: Optional[Sequence[str]] = None,
    ) -> PiRunResult:
        callbacks = callbacks or NullCallbacks()
        tools = self._filter_tools(allowlist=allowlist, denylist=denylist)
        system_prompt = self._build_system_prompt(tools, session_id=session_id)
        agent = self._create_agent(tools=tools, system_prompt=system_prompt)

        history = self.session_store.load(session_id)
        history = self._compact_history(history, session_id=session_id)
        self.session_store.save(session_id, history)

        self.audit_logger.log(session_id, "user_prompt", {"text": prompt})
        input_messages = [*history, {"role": "user", "content": prompt}]

        seen_tool_starts: set[str] = set()
        seen_tool_ends: set[str] = set()
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        partial_chunks: list[str] = []
        final_text = ""

        for stream_mode, chunk in agent.stream(
            {"messages": input_messages},
            stream_mode=["updates", "messages", "custom"],
        ):
            if stream_mode == "messages":
                token, _metadata = chunk
                text = extract_text(token)
                if text:
                    partial_chunks.append(text)
                    callbacks.on_partial_reply(text)

            elif stream_mode == "custom":
                payload = {"message": chunk if isinstance(chunk, str) else repr(chunk)}
                callbacks.on_event("custom", payload)
                self.audit_logger.log(session_id, "custom", payload)

            elif stream_mode == "updates":
                for step_name, data in chunk.items():
                    if not isinstance(data, dict):
                        # data가 None이거나 dict가 아닐 경우 리스트로 캐스팅하거나 무시
                        messages = data if isinstance(data, list) else []
                    else:
                        messages = data.get("messages", [])
                    if not messages:
                        continue
                    message = messages[-1]

                    if isinstance(message, AIMessage):
                        if message.tool_calls:
                            for call in message.tool_calls:
                                call_id = str(call.get("id", "")) or json.dumps(call, sort_keys=True)
                                if call_id in seen_tool_starts:
                                    continue
                                seen_tool_starts.add(call_id)
                                item = {
                                    "id": call.get("id"),
                                    "name": call.get("name"),
                                    "args": call.get("args", {}),
                                }
                                tool_calls.append(item)
                                callbacks.on_tool_start(str(item["name"]), dict(item["args"] or {}))
                                self.audit_logger.log(session_id, "tool_start", item)
                        else:
                            candidate = extract_text(message).strip()
                            if candidate:
                                final_text = candidate

                    elif isinstance(message, ToolMessage):
                        tool_call_id = str(getattr(message, "tool_call_id", ""))
                        if tool_call_id and tool_call_id in seen_tool_ends:
                            continue
                        if tool_call_id:
                            seen_tool_ends.add(tool_call_id)
                        content = extract_text(message)
                        is_error = str(getattr(message, "status", "")).lower() == "error"
                        name = getattr(message, "name", None) or step_name
                        item = {
                            "tool_call_id": tool_call_id,
                            "name": name,
                            "content": content,
                            "is_error": is_error,
                        }
                        tool_results.append(item)
                        callbacks.on_tool_end(str(name), content, is_error)
                        self.audit_logger.log(session_id, "tool_end", item)

        if not final_text:
            final_text = "".join(partial_chunks).strip()

        updated_history = [
            *history,
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": final_text},
        ]
        self.session_store.save(session_id, updated_history)
        audit_file = self.audit_logger.log(
            session_id,
            "assistant_final",
            {"text": final_text, "tool_calls": len(tool_calls), "tool_results": len(tool_results)},
        )

        return PiRunResult(
            session_id=session_id,
            final_text=final_text,
            tool_calls=tool_calls,
            tool_results=tool_results,
            audit_file=audit_file,
        )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenClaw Pi-like agent rebuilt with LangChain")
    parser.add_argument("prompt", help="user prompt")
    parser.add_argument("--model", default=os.getenv("PI_MODEL", "gpt-4o"))
    parser.add_argument("--workspace", default=os.getenv("PI_WORKSPACE", "."))
    parser.add_argument("--session", default=os.getenv("PI_SESSION", "main"))
    parser.add_argument("--session-dir", default=os.getenv("PI_SESSION_DIR", ".openclaw_pi/sessions"))
    parser.add_argument("--audit-dir", default=os.getenv("PI_AUDIT_DIR", ".openclaw_pi/audit"))
    parser.add_argument("--max-model-calls", type=int, default=int(os.getenv("PI_MAX_MODEL_CALLS", "16")))
    parser.add_argument("--exec-timeout", type=int, default=int(os.getenv("PI_EXEC_TIMEOUT", "60")))
    parser.add_argument("--deny-tool", action="append", default=[t.strip() for t in os.getenv("PI_DENY_TOOL", "").split(",")] if os.getenv("PI_DENY_TOOL") else [])
    parser.add_argument("--allow-tool", action="append", default=[t.strip() for t in os.getenv("PI_ALLOW_TOOL", "").split(",")] if os.getenv("PI_ALLOW_TOOL") else [])
    parser.add_argument("--no-write", action="store_true", default=os.getenv("PI_NO_WRITE", "false").lower() == "true")
    parser.add_argument("--no-shell", action="store_true", default=os.getenv("PI_NO_SHELL", "false").lower() == "true")
    parser.add_argument("--no-compaction", action="store_true", default=os.getenv("PI_NO_COMPACTION", "false").lower() == "true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    config = PiAgentConfig(
        model=args.model,
        workspace_dir=args.workspace,
        session_dir=args.session_dir,
        audit_dir=args.audit_dir,
        max_model_calls=args.max_model_calls,
        exec_timeout_s=args.exec_timeout,
        allow_write=not args.no_write,
        allow_shell=not args.no_shell,
        enable_compaction=not args.no_compaction,
    )
    agent = OpenClawPiLangChain(config)
    result = agent.run(
        session_id=args.session,
        prompt=args.prompt,
        callbacks=ConsoleCallbacks(),
        allowlist=args.allow_tool,
        denylist=args.deny_tool,
    )
    print("\n\n--- final ---")
    print(result.final_text)
    if result.audit_file:
        print(f"\naudit: {result.audit_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
