# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib
import importlib.util
import json
import math
import os
import queue
import re
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Protocol, Sequence

from dotenv import load_dotenv
from pydantic import Field, create_model

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain.tools import tool
from langgraph.config import get_stream_writer

# 스크립트 실행 시 현재 디렉토리 또는 상위 디렉토리의 .env 파일을 찾아 환경 변수로 동적 할당합니다.
# override=True 로 설정하여, 도커 시동 시 잡혀있던 환경변수보다 수정된 .env 값이 우선하도록 합니다.
load_dotenv(override=True)


def _now_ts() -> float:
    return time.time()


DEFAULT_BLOCKED_PATHS = (
    ".env",
    ".git/**",
    ".openclaw/memory/**",
    "secrets/**",
    "private/**",
    "node_modules/**",
)


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


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
    read_strategy: str = "smart"
    read_small_line_limit: int = 400
    read_small_char_limit: int = 16384
    read_preview_head_lines: int = 120
    read_preview_tail_lines: int = 80
    read_output_budget_chars: int = 20000
    custom_tool_modules: list[str] = field(default_factory=list)
    mcp_enabled: bool = True
    mcp_config_path: str = "mcp_servers.json"
    mcp_fail_fast: bool = False
    mcp_timeout_s: int = 20
    skills_enabled: bool = True
    skills_dir: str = "skills"
    skill_mode: str = "auto"
    skill_name: Optional[str] = None
    plan_mode: str = "off"
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
    source_path: str = ""


@dataclass(slots=True)
class PlanRuntimePolicy:
    mode: str = "off"
    forced_deny_tools: tuple[str, ...] = ()
    skip_skill_precheck_fail: bool = False
    disable_legacy_memory_write: bool = False
    planner_directive: str = ""


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
    def __init__(self, workspace_dir: Path, blocked_paths: Optional[Sequence[str]] = None):
        self.workspace_dir = workspace_dir.resolve()
        patterns = list(blocked_paths or [])
        self.blocked_patterns = [self._normalize_pattern(p) for p in patterns if self._normalize_pattern(p)]

    @staticmethod
    def _normalize_pattern(raw: str) -> str:
        text = str(raw or "").strip().replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        text = text.strip("/")
        return text.lower()

    @staticmethod
    def _normalize_relpath(raw: str) -> str:
        text = str(raw or "").replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        text = text.strip("/")
        return text.lower()

    def is_blocked(self, resolved_path: Path) -> bool:
        try:
            rel = resolved_path.relative_to(self.workspace_dir).as_posix()
        except ValueError:
            return True
        rel_norm = self._normalize_relpath(rel)
        if not rel_norm:
            rel_norm = "."
        for pattern in self.blocked_patterns:
            if fnmatch.fnmatch(rel_norm, pattern):
                return True
            if pattern.endswith("/**"):
                root = pattern[:-3].rstrip("/")
                if rel_norm == root or rel_norm.startswith(root + "/"):
                    return True
        return False

    def assert_allowed(self, resolved_path: Path) -> None:
        if self.is_blocked(resolved_path):
            raise ValueError(f"blocked path by policy: {resolved_path.relative_to(self.workspace_dir)}")

    def resolve(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self.workspace_dir / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.workspace_dir)
        except ValueError as exc:
            raise ValueError(f"Path escapes workspace: {raw_path}") from exc
        self.assert_allowed(resolved)
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


class FlatMemoryStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, session_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", session_id)
        return self.root / f"{safe}.jsonl"

    def load(self, session_id: str) -> list[dict[str, Any]]:
        path = self.path_for(session_id)
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            kind = str(item.get("kind", "fact")).strip() or "fact"
            if not content:
                continue
            out.append(
                {
                    "id": str(item.get("id", "")),
                    "ts": float(item.get("ts", _now_ts())),
                    "kind": kind,
                    "content": content,
                    "tags": item.get("tags", []),
                    "source_turn": item.get("source_turn", {}),
                }
            )
        return out

    def append(self, session_id: str, memory: dict[str, Any]) -> Path:
        path = self.path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(memory, ensure_ascii=False) + "\n")
        return path

    def overwrite(self, session_id: str, memories: Sequence[dict[str, Any]]) -> Path:
        path = self.path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            for memory in memories:
                fp.write(json.dumps(memory, ensure_ascii=False) + "\n")
        return path


class OpenClawMarkdownMemoryStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_file = self.root / "MEMORY.md"
        self._ensure_index_file()

    def _ensure_index_file(self) -> None:
        if self.index_file.exists():
            return
        self.index_file.write_text(
            "# Memory Index\n\n"
            "This file summarizes key long-term memories.\n"
            "Detailed entries are stored in daily files (YYYY-MM-DD.md).\n\n"
            "## Entries\n",
            encoding="utf-8",
        )

    def _daily_file(self, ts: Optional[datetime] = None) -> Path:
        point = ts or datetime.now(timezone.utc)
        return self.root / f"{point.strftime('%Y-%m-%d')}.md"

    def _new_memory_id(self, session_id: str, content: str) -> str:
        raw = f"{session_id}:{content}:{time.time()}".encode("utf-8")
        short = hashlib.sha1(raw).hexdigest()[:8]
        return f"mem-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{short}"

    def _iter_daily_files(self) -> list[Path]:
        rows = [path for path in self.root.glob("*.md") if path.is_file() and path.name != "MEMORY.md"]
        return sorted(rows, key=lambda p: p.name, reverse=True)

    def _parse_entries_from_file(self, path: Path) -> list[dict[str, Any]]:
        text = path.read_text(encoding="utf-8", errors="replace")
        heading_re = re.compile(r"^####\s+(mem-[\w-]+)\s*$", flags=re.MULTILINE)
        matches = list(heading_re.finditer(text))
        entries: list[dict[str, Any]] = []
        for i, match in enumerate(matches):
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[start:end].strip()
            memory_id = match.group(1)
            ts_match = re.search(r"^- Timestamp:\s*(.+)$", block, flags=re.MULTILINE)
            tags_match = re.search(r"^- Tags:\s*(.+)$", block, flags=re.MULTILINE)
            session_match = re.search(r"^- Session:\s*(.+)$", block, flags=re.MULTILINE)
            content_match = re.search(r"^- Content:\s*\n(.+)$", block, flags=re.MULTILINE | re.DOTALL)
            content = content_match.group(1).strip() if content_match else block
            if not content.strip():
                continue
            tags = [x.strip() for x in (tags_match.group(1).strip() if tags_match else "").split(",") if x.strip()]
            entries.append(
                {
                    "id": memory_id,
                    "timestamp": ts_match.group(1).strip() if ts_match else "",
                    "tags": tags,
                    "session_id": session_match.group(1).strip() if session_match else "",
                    "content": content.strip(),
                    "file": path.name,
                }
            )
        return entries

    def append(self, session_id: str, content: str, tags: Optional[Sequence[str]] = None) -> dict[str, Any]:
        normalized = content.strip()
        if not normalized:
            raise ValueError("memory content cannot be empty")
        timestamp = datetime.now(timezone.utc)
        memory_id = self._new_memory_id(session_id, normalized)
        daily_file = self._daily_file(timestamp)
        safe_tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
        body = (
            f"#### {memory_id}\n"
            f"- Timestamp: {timestamp.isoformat()}\n"
            f"- Tags: {', '.join(safe_tags) if safe_tags else '-'}\n"
            f"- Session: {session_id}\n"
            "- Content:\n"
            f"{normalized}\n\n"
        )
        with daily_file.open("a", encoding="utf-8") as fp:
            fp.write(body)

        snippet = re.sub(r"\s+", " ", normalized)
        if len(snippet) > 120:
            snippet = snippet[:117] + "..."
        with self.index_file.open("a", encoding="utf-8") as fp:
            fp.write(
                f"- {memory_id} | {daily_file.name} | tags={','.join(safe_tags) if safe_tags else '-'} | {snippet}\n"
            )
        return {
            "id": memory_id,
            "timestamp": timestamp.isoformat(),
            "tags": safe_tags,
            "session_id": session_id,
            "content": normalized,
            "file": daily_file.name,
        }

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        needle = query.strip().lower()
        if not needle:
            return []
        terms = [x for x in re.split(r"\s+", needle) if x]
        if not terms:
            return []
        scored: list[dict[str, Any]] = []
        for file_path in self._iter_daily_files():
            for entry in self._parse_entries_from_file(file_path):
                haystack = " ".join(
                    [str(entry.get("content", "")), " ".join(entry.get("tags", [])), str(entry.get("session_id", ""))]
                ).lower()
                score = sum(haystack.count(term) for term in terms)
                if score > 0:
                    scored.append({**entry, "score": score})
        scored.sort(key=lambda x: (int(x.get("score", 0)), str(x.get("timestamp", ""))), reverse=True)
        return scored[: max(1, int(limit))]

    def get_by_ids(self, ids: Sequence[str]) -> list[dict[str, Any]]:
        wanted = {x.strip() for x in ids if x.strip()}
        if not wanted:
            return []
        found: list[dict[str, Any]] = []
        for file_path in self._iter_daily_files():
            for entry in self._parse_entries_from_file(file_path):
                if entry.get("id") in wanted:
                    found.append(entry)
        found.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
        return found


class MemoryEmbeddingClient:
    def __init__(self, provider: str, model: str):
        self.requested_provider = (provider or "auto").strip().lower()
        self.model = (model or "text-embedding-3-small").strip()
        self.provider = self.requested_provider
        self._openai_embeddings: Any = None
        self.error: Optional[str] = None

        if self.requested_provider in {"openai", "auto"}:
            try:
                from langchain_openai import OpenAIEmbeddings

                self._openai_embeddings = OpenAIEmbeddings(model=self.model)
                self.provider = "openai"
            except Exception as e:
                self.error = str(e)
                if self.requested_provider == "openai":
                    raise
                self.provider = "hash"
        elif self.requested_provider == "hash":
            self.provider = "hash"
        else:
            self.provider = "hash"

    def _hash_embed(self, text: str, dims: int = 64) -> list[float]:
        values: list[float] = []
        seed = text.encode("utf-8", errors="replace")
        block = seed
        while len(values) < dims:
            block = hashlib.sha256(block).digest()
            for byte in block:
                values.append((byte / 127.5) - 1.0)
                if len(values) >= dims:
                    break
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]

    def embed_query(self, text: str) -> list[float]:
        if self.provider == "openai" and self._openai_embeddings is not None:
            vec = self._openai_embeddings.embed_query(text)
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            return [float(v) / norm for v in vec]
        return self._hash_embed(text)


def _to_float32_blob(values: Sequence[float]) -> bytes:
    return struct.pack("<" + "f" * len(values), *[float(v) for v in values])


def _from_float32_blob(blob: bytes) -> list[float]:
    if not blob:
        return []
    count = len(blob) // 4
    return list(struct.unpack("<" + "f" * count, blob[: count * 4]))


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    return float(sum(x * y for x, y in zip(a, b)))


class SqliteVecMemoryIndex:
    def __init__(self, db_path: Path, audit_logger: AuditLogger):
        self.db_path = db_path
        self.audit_logger = audit_logger
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.sqlite_vec_ready = False
        self._init_schema()
        self._try_load_sqlite_vec()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_vectors (
                memory_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                ts REAL NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                dim INTEGER NOT NULL,
                embedding BLOB NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_vectors_session_model ON memory_vectors(session_id, provider, model, ts)"
        )
        self.conn.commit()

    def _try_load_sqlite_vec(self) -> None:
        try:
            import sqlite_vec

            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.sqlite_vec_ready = True
        except Exception:
            self.sqlite_vec_ready = False

    def upsert_memory(
        self,
        session_id: str,
        memory: dict[str, Any],
        embedding: Sequence[float],
        provider: str,
        model: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO memory_vectors
            (memory_id, session_id, kind, content, ts, provider, model, dim, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(memory.get("id", "")),
                session_id,
                str(memory.get("kind", "fact")),
                str(memory.get("content", "")),
                float(memory.get("ts", _now_ts())),
                provider,
                model,
                len(embedding),
                _to_float32_blob(embedding),
            ),
        )
        self.conn.commit()

    def trim_session(self, session_id: str, keep_ids: set[str]) -> None:
        if not keep_ids:
            self.conn.execute("DELETE FROM memory_vectors WHERE session_id = ?", (session_id,))
            self.conn.commit()
            return
        placeholders = ",".join("?" for _ in keep_ids)
        self.conn.execute(
            f"DELETE FROM memory_vectors WHERE session_id = ? AND memory_id NOT IN ({placeholders})",
            (session_id, *sorted(keep_ids)),
        )
        self.conn.commit()

    def search(
        self,
        session_id: str,
        query_embedding: Sequence[float],
        limit: int,
        provider: str,
        model: str,
    ) -> list[dict[str, Any]]:
        if not query_embedding:
            return []
        if self.sqlite_vec_ready:
            try:
                rows = self.conn.execute(
                    """
                    SELECT memory_id, kind, content, ts, vec_distance_cosine(embedding, ?) AS dist
                    FROM memory_vectors
                    WHERE session_id = ? AND provider = ? AND model = ? AND dim = ?
                    ORDER BY dist ASC
                    LIMIT ?
                    """,
                    (_to_float32_blob(query_embedding), session_id, provider, model, len(query_embedding), limit),
                ).fetchall()
                return [
                    {
                        "id": row[0],
                        "kind": row[1],
                        "content": row[2],
                        "ts": row[3],
                        "score": 1.0 - float(row[4]),
                    }
                    for row in rows
                ]
            except Exception:
                pass

        rows = self.conn.execute(
            """
            SELECT memory_id, kind, content, ts, embedding
            FROM memory_vectors
            WHERE session_id = ? AND provider = ? AND model = ? AND dim = ?
            """,
            (session_id, provider, model, len(query_embedding)),
        ).fetchall()
        scored: list[dict[str, Any]] = []
        for row in rows:
            score = _cosine_similarity(query_embedding, _from_float32_blob(row[4]))
            scored.append({"id": row[0], "kind": row[1], "content": row[2], "ts": row[3], "score": score})
        scored.sort(key=lambda x: (float(x.get("score", -1.0)), float(x.get("ts", 0.0))), reverse=True)
        return scored[:limit]

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


def _is_tool_like(candidate: Any) -> bool:
    if candidate is None:
        return False
    name = str(getattr(candidate, "name", "")).strip()
    if not name:
        return False
    return callable(getattr(candidate, "invoke", None)) or callable(getattr(candidate, "run", None))


def _safe_tool_name(value: str) -> str:
    # OpenAI function/tool name pattern: ^[a-zA-Z0-9_-]+$
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(value or "").strip()).strip("_-")


def _tool_name_keys(value: str) -> set[str]:
    raw = str(value or "").strip().lower()
    safe = _safe_tool_name(raw).lower()
    return {k for k in {raw, safe} if k}


def _to_bool(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _to_str_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [item.strip() for item in re.split(r"[,\n]", raw) if item.strip()]
    if isinstance(raw, (list, tuple, set)):
        out: list[str] = []
        for item in raw:
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    text = str(raw).strip()
    return [text] if text else []


def _yaml_scalar(raw: str) -> Any:
    text = str(raw or "").strip()
    if not text:
        return ""
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        body = text[1:-1].strip()
        if not body:
            return []
        return [part.strip().strip("'").strip('"') for part in body.split(",") if part.strip()]
    low = text.lower()
    if low in {"true", "yes", "on"}:
        return True
    if low in {"false", "no", "off"}:
        return False
    return text


def _parse_simple_yaml_frontmatter(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    active_list_key: Optional[str] = None
    active_block_key: Optional[str] = None
    block_lines: list[str] = []
    lines = str(text or "").splitlines()
    for raw in lines:
        line = raw.rstrip()
        if active_block_key:
            if line.startswith("  ") or line.startswith("\t"):
                block_lines.append(line[2:] if line.startswith("  ") else line.lstrip("\t"))
                continue
            data[active_block_key] = "\n".join(block_lines).strip()
            active_block_key = None
            block_lines = []
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if m:
            key = m.group(1).strip().lower()
            value = m.group(2).strip()
            if not value:
                data[key] = []
                active_list_key = key
                active_block_key = None
            else:
                if value in {"|", ">"}:
                    active_block_key = key
                    block_lines = []
                    active_list_key = None
                else:
                    data[key] = _yaml_scalar(value)
                    active_list_key = None
                    active_block_key = None
            continue
        m_item = re.match(r"^\s*-\s+(.*)$", line)
        if m_item and active_list_key:
            item = _yaml_scalar(m_item.group(1).strip())
            bucket = data.get(active_list_key)
            if not isinstance(bucket, list):
                bucket = []
                data[active_list_key] = bucket
            bucket.append(item)
    if active_block_key:
        data[active_block_key] = "\n".join(block_lines).strip()
    return data


def _split_frontmatter(content: str) -> tuple[Optional[str], str]:
    text = str(content or "")
    if not text.startswith("---"):
        return None, text
    m = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$", text, flags=re.DOTALL)
    if not m:
        return None, text
    return m.group(1), m.group(2)


def _normalize_skill_mode(raw: Any) -> str:
    mode = str(raw or "auto").strip().lower()
    return mode if mode in {"auto", "manual", "off"} else "auto"


def _normalize_plan_mode(raw: Any) -> str:
    mode = str(raw or "off").strip().lower()
    return mode if mode in {"on", "off"} else "off"


def _tool_clone_with_name(tool_obj: Any, new_name: str) -> Any:
    current = str(getattr(tool_obj, "name", "")).strip()
    target = str(new_name or "").strip()
    if not target:
        raise ValueError("tool name cannot be empty")
    if current == target:
        return tool_obj

    clone_candidates = ["model_copy", "copy"]
    for method_name in clone_candidates:
        method = getattr(tool_obj, method_name, None)
        if callable(method):
            try:
                clone = method(deep=True)
                setattr(clone, "name", target)
                return clone
            except Exception:
                pass
    try:
        setattr(tool_obj, "name", target)
        return tool_obj
    except Exception as exc:
        raise ValueError(f"tool rename failed ({current} -> {target}): {exc}") from exc


def _json_schema_type_to_python(spec: Any) -> Any:
    if not isinstance(spec, dict):
        return Any
    kind = spec.get("type")
    if isinstance(kind, list):
        kinds = [k for k in kind if k != "null"]
        kind = kinds[0] if kinds else "string"
    if kind == "string":
        return str
    if kind == "integer":
        return int
    if kind == "number":
        return float
    if kind == "boolean":
        return bool
    return Any


def _build_args_schema_from_json_schema(schema: Any, model_name: str):
    if not isinstance(schema, dict):
        return None
    if schema.get("type") not in {None, "object"}:
        return None
    props = schema.get("properties", {})
    if not isinstance(props, dict) or not props:
        return None
    required = set(schema.get("required", [])) if isinstance(schema.get("required", []), list) else set()
    fields: dict[str, tuple[Any, Any]] = {}
    for raw_key, spec in props.items():
        key = str(raw_key).strip()
        if not key:
            continue
        py_type = _json_schema_type_to_python(spec)
        description = ""
        if isinstance(spec, dict):
            description = str(spec.get("description", "")).strip()
        if key in required:
            fields[key] = (py_type, Field(description=description))
        else:
            fields[key] = (Optional[py_type], Field(default=None, description=description))
    if not fields:
        return None
    safe_model_name = re.sub(r"[^a-zA-Z0-9_]", "_", model_name)
    return create_model(safe_model_name, **fields)


def _render_mcp_result(result: Any) -> str:
    if not isinstance(result, dict):
        return str(result)
    items = result.get("content")
    rows: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("type") == "text":
                rows.append(str(item.get("text", "")))
            else:
                rows.append(json.dumps(item, ensure_ascii=False))
    if not rows:
        rows.append(json.dumps(result, ensure_ascii=False))
    output = "\n".join(x for x in rows if str(x).strip()) or "(empty result)"
    if bool(result.get("isError")):
        return f"Error: {output}"
    return output


class McpStdioClient:
    def __init__(self, name: str, command: str, args: Sequence[str], env: dict[str, str], timeout_s: int):
        self.name = name
        self.command = command
        self.args = list(args)
        self.env = dict(env)
        self.timeout_s = max(1, int(timeout_s))
        self._proc: Optional[subprocess.Popen[Any]] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._jsonrpc_id = 0
        self._lock = threading.Lock()
        self._tools: list[dict[str, Any]] = []

    def start(self) -> None:
        if self._proc is not None:
            return
        merged_env = dict(os.environ)
        merged_env.update({str(k): str(v) for k, v in self.env.items()})
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            env=merged_env,
        )
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True, name=f"mcp-reader-{self.name}")
        self._reader_thread.start()
        self._initialize()
        self._tools = self._list_tools()

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if not proc:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._request("tools/call", {"name": tool_name, "arguments": arguments or {}})
        if not isinstance(result, dict):
            return {"content": [{"type": "text", "text": str(result)}]}
        return result

    def _read_one_message(self) -> dict[str, Any]:
        if self._proc is None or self._proc.stdout is None:
            raise RuntimeError("MCP process is not running")
        stdout = self._proc.stdout
        headers: dict[str, str] = {}
        while True:
            line = stdout.readline()
            if not line:
                raise RuntimeError("MCP stdout closed")
            if line in (b"\r\n", b"\n"):
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if ":" in decoded:
                key, value = decoded.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        content_len = int(headers.get("content-length", "0"))
        if content_len <= 0:
            raise RuntimeError("MCP message missing Content-Length")
        payload = stdout.read(content_len)
        if not payload:
            raise RuntimeError("MCP message payload is empty")
        return json.loads(payload.decode("utf-8", errors="replace"))

    def _reader_loop(self) -> None:
        try:
            while self._proc is not None:
                msg = self._read_one_message()
                self._messages.put(msg)
        except Exception as e:
            self._messages.put({"__error__": str(e)})

    def _write_message(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("MCP process is not running")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._proc.stdin.write(header + body)
        self._proc.stdin.flush()

    def _next_id(self) -> int:
        with self._lock:
            self._jsonrpc_id += 1
            return self._jsonrpc_id

    def _request(self, method: str, params: Optional[dict[str, Any]] = None) -> Any:
        rid = self._next_id()
        self._write_message({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.time() + float(self.timeout_s)
        while True:
            remaining = max(0.01, deadline - time.time())
            if remaining <= 0:
                raise TimeoutError(f"MCP request timed out: {method}")
            try:
                msg = self._messages.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"MCP response timeout: {method}") from None
            if "__error__" in msg:
                raise RuntimeError(str(msg["__error__"]))
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                raise RuntimeError(json.dumps(msg.get("error"), ensure_ascii=False))
            return msg.get("result")

    def _initialize(self) -> None:
        _ = self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "openclaw-pi", "version": "1.1.0"},
            },
        )
        try:
            self._write_message({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        except Exception:
            pass

    def _list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list", {})
        if isinstance(result, dict):
            tools = result.get("tools", [])
            if isinstance(tools, list):
                return [x for x in tools if isinstance(x, dict)]
        return []


class OpenClawPiLangChain:
    def __init__(
        self,
        config: PiAgentConfig,
        extra_tools: Optional[Sequence[Any]] = None,
    ):
        self.config = config
        self.workspace_dir = config.workspace_path()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.guard = WorkspaceGuard(self.workspace_dir, config.blocked_paths)
        self.session_store = FlatSessionStore(config.session_root())
        self.audit_logger = AuditLogger(config.audit_root())
        self.memory_store = FlatMemoryStore(config.memory_root())
        self.markdown_memory_store = OpenClawMarkdownMemoryStore(config.memory_root())
        self.memory_index = SqliteVecMemoryIndex(config.memory_root() / "memory_vec.sqlite", self.audit_logger)
        self.embedding_client = MemoryEmbeddingClient(
            provider=config.memory_embedding_provider,
            model=config.memory_embedding_model,
        )
        self._active_session_id = "main"
        self._session_exec_failure_keys: dict[str, set[str]] = {}
        self._session_exec_failure_recent: dict[str, list[dict[str, Any]]] = {}
        self._session_mutation_ticks: dict[str, int] = {}
        self._session_turn_read_chars: dict[str, int] = {}
        self._todo_items: list[dict[str, Any]] = []
        self._pending_audit_events: list[tuple[str, dict[str, Any]]] = []
        self._tool_sources: dict[str, str] = {}
        self._mcp_clients: dict[str, McpStdioClient] = {}
        self.skills_by_id: dict[str, SkillSpec] = {}

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

        self.all_tools = self._build_tool_registry(extra_tools=extra_tools)
        self.skills_by_id = self._discover_skills()

    def _queue_audit(self, event_type: str, payload: dict[str, Any]) -> None:
        self._pending_audit_events.append((event_type, payload))

    def _flush_pending_audit(self, session_id: str) -> None:
        if not self._pending_audit_events:
            return
        for event_type, payload in self._pending_audit_events:
            self.audit_logger.log(session_id, event_type, payload)
        self._pending_audit_events = []

    def _build_tool_registry(self, extra_tools: Optional[Sequence[Any]]) -> list[Any]:
        builtin_tools = self._build_default_tools()
        self._register_tool_batch(builtin_tools, source="builtin")
        builtin_names = {str(getattr(t, "name", "")).strip() for t in builtin_tools}

        custom_tools = self._load_custom_tools(builtin_names=builtin_names)
        self._register_tool_batch(custom_tools, source="custom")

        used_custom_names = set(builtin_names)
        used_custom_names.update(str(getattr(t, "name", "")).strip() for t in custom_tools)
        raw_inline_custom = [t for t in (extra_tools or []) if _is_tool_like(t)]
        inline_custom = self._normalize_custom_tool_names(
            raw_tools=raw_inline_custom,
            module_short="inline",
            builtin_names=builtin_names,
            used_names=used_custom_names,
        )
        self._register_tool_batch(inline_custom, source="custom")

        mcp_tools = self._load_mcp_tools()
        self._register_tool_batch(mcp_tools, source="mcp")

        all_tools = [*builtin_tools, *custom_tools, *inline_custom, *mcp_tools]
        summary = {
            "total": len(all_tools),
            "builtin": len(builtin_tools),
            "custom": len(custom_tools) + len(inline_custom),
            "mcp": len(mcp_tools),
            "tools": [str(getattr(t, "name", "")) for t in all_tools],
        }
        self._queue_audit("tool_registry_summary", summary)
        return all_tools

    def _register_tool_batch(self, tools: Sequence[Any], source: str) -> None:
        for tool_obj in tools:
            name = str(getattr(tool_obj, "name", "")).strip()
            if not name:
                continue
            self._tool_sources[name] = source

    def _module_name_for_path(self, file_path: Path) -> str:
        base = _safe_tool_name(file_path.stem) or "custom"
        stamp = str(int(time.time() * 1000))
        return f"pi_custom_{base}_{stamp}"

    def _import_custom_module(self, module_ref: str):
        ref = str(module_ref or "").strip()
        if not ref:
            raise ValueError("empty module reference")
        is_file_ref = ref.endswith(".py") or "/" in ref or "\\" in ref
        if is_file_ref:
            candidate = Path(ref)
            if not candidate.is_absolute():
                candidate = self.workspace_dir / candidate
            resolved = candidate.resolve()
            try:
                resolved.relative_to(self.workspace_dir)
            except ValueError as exc:
                raise ValueError(f"custom tool module path escapes workspace: {ref}") from exc
            if not resolved.exists() or not resolved.is_file():
                raise ValueError(f"custom tool module file not found: {resolved}")
            spec = importlib.util.spec_from_file_location(self._module_name_for_path(resolved), resolved)
            if spec is None or spec.loader is None:
                raise ValueError(f"unable to create module spec: {resolved}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        sys.path.insert(0, str(self.workspace_dir))
        try:
            return importlib.import_module(ref)
        finally:
            if sys.path and sys.path[0] == str(self.workspace_dir):
                sys.path.pop(0)

    def _extract_custom_tools_from_module(self, module: types.ModuleType) -> list[Any]:
        if callable(getattr(module, "get_tools", None)):
            loaded = module.get_tools()
        elif hasattr(module, "TOOLS"):
            loaded = getattr(module, "TOOLS")
        else:
            raise ValueError("custom tool module requires get_tools() or TOOLS")
        if not isinstance(loaded, (list, tuple)):
            raise ValueError("custom tool module output must be a list/tuple of tools")
        out = []
        for item in loaded:
            if _is_tool_like(item):
                out.append(item)
        if not out:
            raise ValueError("no valid tools found in module")
        return out

    def _normalize_custom_tool_names(
        self,
        raw_tools: Sequence[Any],
        module_short: str,
        builtin_names: set[str],
        used_names: set[str],
    ) -> list[Any]:
        normalized: list[Any] = []
        safe_module_short = _safe_tool_name(module_short) or "custom"
        used_keys = {_safe_tool_name(name).lower() for name in used_names if str(name).strip()}
        builtin_keys = {_safe_tool_name(name).lower() for name in builtin_names if str(name).strip()}
        for tool_obj in raw_tools:
            original_name = str(getattr(tool_obj, "name", "")).strip()
            if not original_name:
                continue
            target_name = _safe_tool_name(original_name) or "tool"
            if target_name.lower() in builtin_keys:
                raise ValueError(f"custom tool name conflicts with builtin tool: {original_name}")
            if target_name.lower() in used_keys:
                base = f"custom_{safe_module_short}_{_safe_tool_name(original_name) or 'tool'}"
                target_name = base
                suffix = 2
                while target_name.lower() in used_keys:
                    target_name = f"{base}_{suffix}"
                    suffix += 1
                tool_obj = _tool_clone_with_name(tool_obj, target_name)
            elif original_name != target_name:
                tool_obj = _tool_clone_with_name(tool_obj, target_name)
            used_names.add(target_name)
            used_keys.add(target_name.lower())
            normalized.append(tool_obj)
        return normalized

    def _load_custom_tools(self, builtin_names: set[str]) -> list[Any]:
        custom_refs = [x for x in (self.config.custom_tool_modules or []) if str(x).strip()]
        if not custom_refs:
            return []
        used_names = set(builtin_names)
        loaded_tools: list[Any] = []
        for module_ref in custom_refs:
            try:
                module = self._import_custom_module(str(module_ref))
                raw_tools = self._extract_custom_tools_from_module(module)
                module_name = _safe_tool_name(getattr(module, "__name__", "custom")) or "custom"
                module_short = module_name.split(".")[-1]
                loaded_tools.extend(
                    self._normalize_custom_tool_names(
                        raw_tools=raw_tools,
                        module_short=module_short,
                        builtin_names=builtin_names,
                        used_names=used_names,
                    )
                )
                self._queue_audit(
                    "custom_tool_load_ok",
                    {
                        "module": str(module_ref),
                        "tool_count": len(raw_tools),
                        "tools": [str(getattr(t, "name", "")) for t in raw_tools],
                    },
                )
            except Exception as e:
                self._queue_audit(
                    "custom_tool_load_fail",
                    {"module": str(module_ref), "error": str(e)},
                )
                raise
        return loaded_tools

    def _resolve_mcp_config_path(self) -> Path:
        raw = str(self.config.mcp_config_path or "mcp_servers.json").strip()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.workspace_dir / candidate
        return candidate.resolve()

    def _load_mcp_config(self) -> list[dict[str, Any]]:
        if not self.config.mcp_enabled:
            return []
        config_path = self._resolve_mcp_config_path()
        if not config_path.exists():
            return []
        text = config_path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("mcp config must be a JSON object")
        servers = data.get("servers", [])
        if not isinstance(servers, list):
            raise ValueError("mcp config 'servers' must be a list")
        out: list[dict[str, Any]] = []
        names: set[str] = set()
        for row in servers:
            if not isinstance(row, dict):
                continue
            name = _safe_tool_name(row.get("name"))
            if not name:
                continue
            if name in names:
                raise ValueError(f"duplicate MCP server name: {name}")
            names.add(name)
            enabled = _to_bool(row.get("enabled", True), default=True)
            transport = str(row.get("transport", "stdio")).strip().lower()
            command = str(row.get("command", "")).strip()
            args = row.get("args", [])
            env = row.get("env", {})
            timeout_s = int(row.get("timeout_s", self.config.mcp_timeout_s))
            out.append(
                {
                    "name": name,
                    "enabled": enabled,
                    "transport": transport,
                    "command": command,
                    "args": args if isinstance(args, list) else [],
                    "env": env if isinstance(env, dict) else {},
                    "timeout_s": max(1, timeout_s),
                }
            )
        return out

    def _make_mcp_langchain_tool(
        self,
        server_name: str,
        remote_tool_name: str,
        remote_description: str,
        input_schema: Any,
    ):
        mcp_tool_name = f"mcp_{server_name}_{_safe_tool_name(remote_tool_name) or 'tool'}"
        args_schema = _build_args_schema_from_json_schema(
            input_schema,
            f"McpTool_{_safe_tool_name(server_name)}_{_safe_tool_name(remote_tool_name)}",
        )

        def _invoke_mcp_tool(**kwargs: Any) -> str:
            client = self._mcp_clients.get(server_name)
            if client is None:
                return f"Error: MCP server '{server_name}' is unavailable."
            self.audit_logger.log(
                str(self._active_session_id or "main"),
                "mcp_tool_call",
                {"server": server_name, "tool": remote_tool_name, "args_keys": sorted(kwargs.keys())},
            )
            result = client.call_tool(remote_tool_name, kwargs or {})
            return _render_mcp_result(result)

        description = (remote_description or "").strip() or f"MCP tool {remote_tool_name} from server {server_name}."
        if args_schema is not None:
            return StructuredTool.from_function(
                func=_invoke_mcp_tool,
                name=mcp_tool_name,
                description=description,
                args_schema=args_schema,
            )

        @tool(mcp_tool_name)
        def _noarg_mcp_tool() -> str:
            """Call an MCP tool that does not require arguments."""
            return _invoke_mcp_tool()

        _noarg_mcp_tool.description = description
        return _noarg_mcp_tool

    def _load_mcp_tools(self) -> list[Any]:
        servers = self._load_mcp_config()
        if not servers:
            return []
        loaded: list[Any] = []
        for server in servers:
            if not server.get("enabled", True):
                continue
            name = str(server.get("name", "")).strip()
            transport = str(server.get("transport", "stdio")).strip().lower()
            if transport != "stdio":
                self._queue_audit(
                    "mcp_server_connect_fail",
                    {"server": name, "error": f"unsupported transport: {transport}"},
                )
                continue
            command = str(server.get("command", "")).strip()
            if not command:
                self._queue_audit("mcp_server_connect_fail", {"server": name, "error": "missing command"})
                continue
            try:
                client = McpStdioClient(
                    name=name,
                    command=command,
                    args=[str(x) for x in (server.get("args", []) or [])],
                    env={str(k): str(v) for k, v in (server.get("env", {}) or {}).items()},
                    timeout_s=int(server.get("timeout_s", self.config.mcp_timeout_s)),
                )
                client.start()
                self._mcp_clients[name] = client
                remote_tools = client.tools()
                for row in remote_tools:
                    remote_name = str(row.get("name", "")).strip()
                    if not remote_name:
                        continue
                    loaded.append(
                        self._make_mcp_langchain_tool(
                            server_name=name,
                            remote_tool_name=remote_name,
                            remote_description=str(row.get("description", "")).strip(),
                            input_schema=row.get("inputSchema", {}),
                        )
                    )
                self._queue_audit(
                    "mcp_server_connect_ok",
                    {"server": name, "tool_count": len(remote_tools)},
                )
            except Exception as e:
                try:
                    client.close()
                except Exception:
                    pass
                self._queue_audit("mcp_server_connect_fail", {"server": name, "error": str(e)})
                if self.config.mcp_fail_fast:
                    raise
        return loaded

    def close(self) -> None:
        for _name, client in list(self._mcp_clients.items()):
            try:
                client.close()
            except Exception:
                pass
        self._mcp_clients = {}

    def _resolve_skills_root(self) -> Path:
        raw = str(self.config.skills_dir or "skills").strip()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.workspace_dir / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.workspace_dir)
        except ValueError as exc:
            raise ValueError(f"skills_dir escapes workspace: {raw}") from exc
        return resolved

    def _load_skill_from_path(self, path: Path) -> SkillSpec:
        content = path.read_text(encoding="utf-8")
        frontmatter_raw, body = _split_frontmatter(content)
        if frontmatter_raw is None:
            raise ValueError("SKILL.md frontmatter is required")
        meta = _parse_simple_yaml_frontmatter(frontmatter_raw)
        skill_id = _safe_tool_name(meta.get("id") or path.parent.name)
        if not skill_id:
            raise ValueError("skill id is empty")
        name = str(meta.get("name") or skill_id).strip()
        description = str(meta.get("description") or "").strip()
        if not description:
            description = body.strip().splitlines()[0].strip() if body.strip() else f"Skill {skill_id}"
        triggers = _to_str_list(meta.get("triggers"))
        required_tools = _to_str_list(meta.get("required_tools"))
        required_env = _to_str_list(meta.get("required_env"))
        tool_allow = _to_str_list(meta.get("tool_allow"))
        tool_deny = _to_str_list(meta.get("tool_deny"))
        api_policy = str(meta.get("api_policy") or "tool_first").strip().lower() or "tool_first"
        workflow = str(meta.get("workflow") or "").strip()
        output_format = str(meta.get("output_format") or "").strip()
        body_text = body.strip()
        if body_text:
            if not workflow:
                workflow = body_text
            elif not output_format:
                output_format = body_text
        return SkillSpec(
            id=skill_id,
            name=name,
            description=description,
            triggers=triggers,
            required_tools=required_tools,
            required_env=required_env,
            tool_allow=tool_allow,
            tool_deny=tool_deny,
            api_policy=api_policy,
            workflow=workflow,
            output_format=output_format,
            source_path=str(path),
        )

    def _discover_skills(self) -> dict[str, SkillSpec]:
        if not self.config.skills_enabled:
            return {}
        skills_root = self._resolve_skills_root()
        if not skills_root.exists() or not skills_root.is_dir():
            return {}
        discovered: dict[str, SkillSpec] = {}
        for skill_file in sorted(skills_root.glob("*/SKILL.md")):
            try:
                skill = self._load_skill_from_path(skill_file)
                if skill.id in discovered:
                    raise ValueError(f"duplicate skill id: {skill.id}")
                discovered[skill.id] = skill
                self._queue_audit(
                    "skill_discovered",
                    {"skill_id": skill.id, "name": skill.name, "path": skill.source_path},
                )
            except Exception as e:
                self._queue_audit(
                    "skill_invalid",
                    {"path": str(skill_file), "error": str(e)},
                )
        return discovered

    def list_skills(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for skill_id in sorted(self.skills_by_id.keys()):
            skill = self.skills_by_id[skill_id]
            rows.append(
                {
                    "id": skill.id,
                    "name": skill.name,
                    "description": skill.description,
                    "triggers": list(skill.triggers),
                    "required_tools": list(skill.required_tools),
                    "required_env": list(skill.required_env),
                    "api_policy": skill.api_policy,
                }
            )
        return rows

    def _find_skill_by_name(self, name: str) -> Optional[SkillSpec]:
        target = _safe_tool_name(name).lower()
        if not target:
            return None
        if target in self.skills_by_id:
            return self.skills_by_id[target]
        for skill in self.skills_by_id.values():
            if _safe_tool_name(skill.name).lower() == target:
                return skill
        return None

    def _score_skill(self, skill: SkillSpec, prompt: str) -> int:
        text = str(prompt or "").lower()
        if not text:
            return 0
        score = 0
        name_tokens = [t for t in re.split(r"[\s_.-]+", skill.name.lower()) if len(t) >= 3]
        for token in name_tokens:
            if token in text:
                score += 2
        desc_tokens = [t for t in re.split(r"[\s_.-]+", skill.description.lower()) if len(t) >= 4]
        for token in desc_tokens[:8]:
            if token in text:
                score += 1
        for trigger in skill.triggers:
            trig = str(trigger).strip().lower()
            if trig and trig in text:
                score += 5
        return score

    def _select_skill(
        self,
        prompt: str,
        skill_name: Optional[str],
        skill_mode: Optional[str],
        session_id: str,
    ) -> Optional[SkillSpec]:
        if not self.config.skills_enabled:
            self.audit_logger.log(session_id, "skill_not_selected", {"reason": "skills_disabled"})
            return None
        mode = _normalize_skill_mode(skill_mode or self.config.skill_mode)
        explicit = str(skill_name or self.config.skill_name or "").strip()
        if mode == "off":
            self.audit_logger.log(session_id, "skill_not_selected", {"reason": "skill_mode_off"})
            return None
        if explicit:
            selected = self._find_skill_by_name(explicit)
            if not selected:
                self.audit_logger.log(
                    session_id,
                    "skill_not_selected",
                    {"reason": "skill_name_not_found", "skill_name": explicit, "mode": mode},
                )
                return None
            self.audit_logger.log(
                session_id,
                "skill_selected",
                {"skill_id": selected.id, "skill_name": selected.name, "mode": "manual"},
            )
            return selected
        if mode == "manual":
            self.audit_logger.log(session_id, "skill_not_selected", {"reason": "manual_without_skill_name"})
            return None
        best: Optional[SkillSpec] = None
        best_score = 0
        for skill in self.skills_by_id.values():
            score = self._score_skill(skill, prompt)
            if score > best_score:
                best = skill
                best_score = score
        if not best or best_score <= 0:
            self.audit_logger.log(session_id, "skill_not_selected", {"reason": "auto_no_match"})
            return None
        self.audit_logger.log(
            session_id,
            "skill_selected",
            {"skill_id": best.id, "skill_name": best.name, "mode": "auto", "score": best_score},
        )
        return best

    def _apply_skill_tool_policy(self, tools: Sequence[Any], skill: SkillSpec) -> list[Any]:
        out = list(tools)
        if skill.tool_allow:
            allow_keys: set[str] = set()
            for name in skill.tool_allow:
                allow_keys.update(_tool_name_keys(str(name)))
            out = [
                tool_obj
                for tool_obj in out
                if _tool_name_keys(str(getattr(tool_obj, "name", ""))).intersection(allow_keys)
            ]
        if skill.tool_deny:
            deny_keys: set[str] = set()
            for name in skill.tool_deny:
                deny_keys.update(_tool_name_keys(str(name)))
            out = [
                tool_obj
                for tool_obj in out
                if not _tool_name_keys(str(getattr(tool_obj, "name", ""))).intersection(deny_keys)
            ]
        return out

    def _skill_precheck(self, tools: Sequence[Any], skill: SkillSpec) -> tuple[bool, str]:
        tool_map: dict[str, Any] = {}
        for tool_obj in tools:
            for key in _tool_name_keys(str(getattr(tool_obj, "name", ""))):
                tool_map[key] = tool_obj
        missing_tools: list[str] = []
        for required in skill.required_tools:
            req_keys = _tool_name_keys(str(required))
            if req_keys and not any(key in tool_map for key in req_keys):
                missing_tools.append(required)
        missing_env: list[str] = []
        for env_name in skill.required_env:
            key = str(env_name).strip()
            if key and not os.getenv(key):
                missing_env.append(key)
        if not missing_tools and not missing_env:
            return True, ""
        rows = [f"Skill precheck failed: {skill.id}"]
        if missing_tools:
            rows.append("Missing required tools: " + ", ".join(missing_tools))
        if missing_env:
            rows.append("Missing required env vars: " + ", ".join(missing_env))
        rows.append("Tip: enable matching MCP/custom tools or adjust allow/deny settings.")
        return False, "\n".join(rows)

    def _recover_after_tool_repeat_abort(
        self,
        session_id: str,
        user_prompt: str,
        repeat_abort_reason: str,
        tool_calls: Sequence[dict[str, Any]],
        tool_results: Sequence[dict[str, Any]],
    ) -> Optional[str]:
        """
        When a run is aborted due to repeated identical tool calls, perform one
        no-tool recovery model pass so the assistant can still provide a useful
        response or ask for a precise follow-up.
        """

        def _clip(text: Any, limit: int = 280) -> str:
            raw = str(text or "").strip().replace("\n", " ")
            if len(raw) <= limit:
                return raw
            return raw[:limit] + "..."

        observed_calls = [
            f"- {str(item.get('name', '')).strip()} args={_clip(json.dumps(item.get('args', {}), ensure_ascii=False))}"
            for item in list(tool_calls)[-4:]
        ]
        observed_results = [
            f"- {str(item.get('name', '')).strip()} [{('error' if item.get('is_error') else 'ok')}] {_clip(item.get('content', ''))}"
            for item in list(tool_results)[-4:]
        ]
        observed_block = "\n".join(
            [
                "Recent tool calls:",
                *(observed_calls or ["- (none)"]),
                "Recent tool outputs:",
                *(observed_results or ["- (none)"]),
            ]
        )

        recovery_system = (
            "You are Pi. The previous run was aborted because the same tool call repeated too many times.\n"
            "Recovery mode rules:\n"
            "1) Do not call tools.\n"
            "2) Give the best possible direct answer from observed context.\n"
            "3) If information is insufficient, ask one specific follow-up question.\n"
            "4) Keep the reply concise and actionable."
        )
        recovery_user = (
            f"Original user request:\n{user_prompt}\n\n"
            f"Abort reason:\n{repeat_abort_reason}\n\n"
            f"{observed_block}\n\n"
            "Now provide the user-facing response."
        )
        try:
            self.audit_logger.log(
                session_id,
                "tool_repeat_recovery_start",
                {"reason": repeat_abort_reason},
            )
            response = self.model.invoke(
                [
                    {"role": "system", "content": recovery_system},
                    {"role": "user", "content": recovery_user},
                ]
            )
            recovered = extract_text(response).strip()
            if not recovered:
                return None
            self.audit_logger.log(
                session_id,
                "tool_repeat_recovery_ok",
                {"chars": len(recovered)},
            )
            return recovered
        except Exception as e:
            self.audit_logger.log(
                session_id,
                "tool_repeat_recovery_fail",
                {"error": str(e)},
            )
            return None

    def _normalize_command(self, command: str) -> str:
        return re.sub(r"\s+", " ", str(command or "").strip())

    def _effective_read_strategy(self) -> str:
        strategy = str(self.config.read_strategy or "smart").strip().lower()
        return strategy if strategy in {"smart", "legacy"} else "smart"

    def _read_budget_used(self, session_id: str) -> int:
        return int(self._session_turn_read_chars.get(str(session_id or "main"), 0))

    def _reset_read_budget(self, session_id: str) -> None:
        self._session_turn_read_chars[str(session_id or "main")] = 0

    def _clear_read_budget(self, session_id: str) -> None:
        self._session_turn_read_chars.pop(str(session_id or "main"), None)

    def _consume_read_budget(self, session_id: str, amount: int) -> int:
        key = str(session_id or "main")
        used = int(self._session_turn_read_chars.get(key, 0))
        used += max(0, int(amount))
        self._session_turn_read_chars[key] = used
        return used

    def _read_budget_guard_message(self, used: int) -> str:
        limit = max(1, int(self.config.read_output_budget_chars))
        return (
            f"read budget exceeded for this turn (used={used}, limit={limit}). "
            "Use grep/find to narrow lines first, then retry read for targeted content."
        )

    def _smart_read_output(self, rel_path: Path, text: str, full: bool) -> str:
        strategy = self._effective_read_strategy()
        if full or strategy == "legacy":
            return _shorten(text)

        char_count = len(text)
        lines = text.splitlines()
        line_count = len(lines)
        if char_count <= int(self.config.read_small_char_limit) or line_count <= int(self.config.read_small_line_limit):
            return _shorten(text)

        head_n = max(1, int(self.config.read_preview_head_lines))
        tail_n = max(1, int(self.config.read_preview_tail_lines))
        head = "\n".join(lines[:head_n])
        tail = "\n".join(lines[-tail_n:]) if line_count > tail_n else "\n".join(lines)
        preview = (
            f"path={rel_path.as_posix()}\n"
            f"line_count={line_count}\n"
            f"char_count={char_count}\n"
            "truncated=true\n"
            "hint=use grep for target lines, then read(path, full=true) for full content\n\n"
            f"--- head (first {head_n} lines) ---\n"
            f"{head}\n\n"
            f"--- tail (last {tail_n} lines) ---\n"
            f"{tail}"
        )
        return _shorten(preview)

    def _core_stderr_line(self, stderr: str) -> str:
        for line in reversed((stderr or "").splitlines()):
            cleaned = re.sub(r"\s+", " ", line.strip())
            if cleaned:
                return cleaned[:240]
        return "-"

    def _exec_error_type(self, exit_code: int, stderr: str) -> str:
        if int(exit_code) == 0:
            return "-"
        lower = (stderr or "").lower()
        if "no such file or directory" in lower or "can't open file" in lower:
            return "FILE_NOT_FOUND"
        if "permission denied" in lower:
            return "PERMISSION_DENIED"
        if "command not found" in lower or "is not recognized as an internal or external command" in lower:
            return "COMMAND_NOT_FOUND"
        return "COMMAND_FAILED"

    def _exec_retryable(self, error_type: str) -> bool:
        return error_type in {"COMMAND_FAILED"}

    def _exec_failure_signature(self, cwd_rel: str, command: str, stderr: str) -> str:
        payload = "|".join(
            [
                "exec",
                cwd_rel.strip() or ".",
                self._normalize_command(command),
                self._core_stderr_line(stderr),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]

    def _session_failure_key_set(self, session_id: str) -> set[str]:
        key = str(session_id or "main")
        if key not in self._session_exec_failure_keys:
            self._session_exec_failure_keys[key] = set()
        return self._session_exec_failure_keys[key]

    def _session_failure_recent(self, session_id: str) -> list[dict[str, Any]]:
        key = str(session_id or "main")
        if key not in self._session_exec_failure_recent:
            self._session_exec_failure_recent[key] = []
        return self._session_exec_failure_recent[key]

    def _session_mutation_tick(self, session_id: str) -> int:
        key = str(session_id or "main")
        return int(self._session_mutation_ticks.get(key, 0))

    def _bump_session_mutation_tick(self, session_id: str) -> int:
        key = str(session_id or "main")
        next_tick = self._session_mutation_tick(key) + 1
        self._session_mutation_ticks[key] = next_tick
        return next_tick

    def _remember_exec_failure(
        self,
        session_id: str,
        *,
        cwd_rel: str,
        command: str,
        error_type: str,
        error_signature: str,
        stderr_core: str,
    ) -> None:
        mutation_tick = self._session_mutation_tick(session_id)
        dedup_key = f"exec|{cwd_rel}|{self._normalize_command(command)}|{error_signature}|t={mutation_tick}"
        self._session_failure_key_set(session_id).add(dedup_key)
        recent = self._session_failure_recent(session_id)
        recent.append(
            {
                "tool": "exec",
                "cwd": cwd_rel,
                "command": self._normalize_command(command),
                "error_type": error_type,
                "error_signature": error_signature,
                "stderr_core": stderr_core,
                "mutation_tick": mutation_tick,
                "ts": _now_ts(),
            }
        )
        if len(recent) > 30:
            del recent[: len(recent) - 30]

    def _is_duplicate_exec_failure(self, session_id: str, cwd_rel: str, command: str) -> Optional[dict[str, str]]:
        normalized_command = self._normalize_command(command)
        current_tick = self._session_mutation_tick(session_id)
        recent = self._session_failure_recent(session_id)
        for item in reversed(recent):
            if item.get("cwd") != cwd_rel:
                continue
            if item.get("command") != normalized_command:
                continue
            if int(item.get("mutation_tick", -1)) != current_tick:
                continue
            sig = str(item.get("error_signature", "")).strip()
            dedup_key = f"exec|{cwd_rel}|{normalized_command}|{sig}|t={current_tick}"
            if sig and dedup_key in self._session_failure_key_set(session_id):
                return {
                    "error_signature": sig,
                    "error_type": str(item.get("error_type", "COMMAND_FAILED")),
                    "stderr_core": str(item.get("stderr_core", "-")),
                }
        return None

    def _parse_exec_meta(self, content: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for raw in (content or "").splitlines():
            if "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            key = key.strip()
            if key in {"result", "error_type", "error_signature", "retryable"}:
                out[key] = value.strip()
        return out

    def _failure_digest_message(self, session_id: str, limit: int = 3) -> Optional[dict[str, str]]:
        recent = self._session_failure_recent(session_id)
        if not recent:
            return None
        rows = recent[-max(1, int(limit)) :]
        lines: list[str] = []
        for item in rows:
            lines.append(
                f"- exec cwd={item.get('cwd')} cmd={item.get('command')} "
                f"type={item.get('error_type')} sig={item.get('error_signature')} "
                f"stderr={item.get('stderr_core')}"
            )
        return {
            "role": "system",
            "content": (
                "Failure Digest (recent exec failures):\n"
                + "\n".join(lines)
                + "\nAvoid repeating the same exec. Prefer read/ls/find/grep to verify paths and files first."
            ),
        }

    def _maybe_correct_exec_command(self, command: str, run_dir: Path) -> tuple[str, Optional[str]]:
        if not self.config.enable_exec_path_correction:
            return command, None
        parts = re.split(r"\s+", str(command or "").strip())
        if len(parts) < 2:
            return command, None
        launcher = parts[0].lower()
        if launcher not in {"python", "python3", "py"}:
            return command, None
        script = parts[1].strip().strip("\"'")
        if not script:
            return command, None
        script_path = Path(script.replace("\\", "/"))
        if script_path.is_absolute():
            return command, None
        candidate_now = (run_dir / script_path).resolve()
        if candidate_now.exists():
            return command, None
        script_parts = list(script_path.parts)
        if len(script_parts) < 2:
            return command, None
        if script_parts[0] != run_dir.name:
            return command, None
        corrected_script = "/".join(script_parts[1:])
        corrected = " ".join([parts[0], corrected_script, *parts[2:]])
        corrected_candidate = (run_dir / corrected_script).resolve()
        if not corrected_candidate.exists():
            return command, None
        note = f"exec path correction applied: {script} -> {corrected_script}"
        return corrected, note

    def _normalize_memory_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())

    def _recall_memories(self, session_id: str, prompt: str) -> list[dict[str, Any]]:
        if not self.config.enable_memory:
            return []
        backend = (self.config.memory_search_backend or "keyword").strip().lower()
        memories = self.memory_store.load(session_id)
        if not memories:
            return []

        if backend == "sqlite-vec":
            try:
                query_embedding = self.embedding_client.embed_query(prompt)
                selected = self.memory_index.search(
                    session_id=session_id,
                    query_embedding=query_embedding,
                    limit=self.config.memory_recall_limit,
                    provider=self.embedding_client.provider,
                    model=self.embedding_client.model,
                )
                self.audit_logger.log(
                    session_id,
                    "memory_recall",
                    {
                        "backend": "sqlite-vec",
                        "requested": self.config.memory_recall_limit,
                        "returned": len(selected),
                        "embedding_provider": self.embedding_client.provider,
                        "embedding_model": self.embedding_client.model,
                        "sqlite_vec_ready": self.memory_index.sqlite_vec_ready,
                    },
                )
                return selected
            except Exception as e:
                self.audit_logger.log(session_id, "memory_recall_fallback", {"reason": str(e)})

        prompt_tokens = set(re.findall(r"[a-zA-Z0-9가-힣_]+", prompt.lower()))

        def score(item: dict[str, Any]) -> tuple[int, float]:
            text_tokens = set(re.findall(r"[a-zA-Z0-9가-힣_]+", str(item.get("content", "")).lower()))
            overlap = len(prompt_tokens & text_tokens)
            ts = float(item.get("ts", 0.0))
            return overlap, ts

        selected = sorted(memories, key=score, reverse=True)[: self.config.memory_recall_limit]
        selected = [m for m in selected if str(m.get("content", "")).strip()]
        self.audit_logger.log(
            session_id,
            "memory_recall",
            {
                "backend": "keyword",
                "requested": self.config.memory_recall_limit,
                "returned": len(selected),
            },
        )
        return selected

    def _memory_context_message(self, recalled: Sequence[dict[str, Any]]) -> Optional[dict[str, str]]:
        if not recalled:
            return None
        rows: list[str] = []
        budget = 1200
        consumed = 0
        for mem in recalled:
            line = f"- ({mem.get('kind', 'fact')}) {mem.get('content', '')}".strip()
            if consumed + len(line) + 1 > budget:
                break
            rows.append(line)
            consumed += len(line) + 1
        if not rows:
            return None
        return {"role": "system", "content": "Relevant memory:\n" + "\n".join(rows)}

    def _extract_memories(self, prompt: str, final_text: str) -> list[dict[str, Any]]:
        if not self.config.enable_memory:
            return []
        candidates: list[dict[str, Any]] = []
        combined = f"{prompt}\n{final_text}"
        lines = [line.strip("-• \t") for line in combined.splitlines() if line.strip()]
        patterns = [
            (r"\b(I prefer|I like|선호|좋아해)\b", "preference"),
            (r"\b(always|never|must|should not|제약|항상|절대)\b", "constraint"),
            (r"\b(goal|todo|task|목표|할 일)\b", "task"),
        ]
        for line in lines:
            kind = "fact"
            for pattern, detected in patterns:
                if re.search(pattern, line, flags=re.IGNORECASE):
                    kind = detected
                    break
            if len(line) < 12 or len(line) > 220:
                continue
            candidates.append({"kind": kind, "content": line})
        dedup: dict[str, dict[str, Any]] = {}
        for item in candidates:
            dedup[self._normalize_memory_text(str(item["content"]))] = item
        return list(dedup.values())[:8]

    def _write_memories(self, session_id: str, prompt: str, final_text: str) -> None:
        if not self.config.enable_memory:
            return
        existing = self.memory_store.load(session_id)
        recent_norm = {self._normalize_memory_text(str(item.get("content", ""))) for item in existing[-50:]}
        extracted = self._extract_memories(prompt, final_text)
        appended = 0
        for item in extracted:
            normalized = self._normalize_memory_text(str(item.get("content", "")))
            if not normalized or normalized in recent_norm:
                continue
            memory_record = {
                "id": f"mem-{int(_now_ts() * 1000)}-{appended}",
                "ts": _now_ts(),
                "kind": item.get("kind", "fact"),
                "content": item.get("content", ""),
                "tags": [],
                "source_turn": {"prompt": _shorten(prompt, 160), "reply": _shorten(final_text, 160)},
            }
            self.memory_store.append(session_id, memory_record)
            if (self.config.memory_search_backend or "").lower() == "sqlite-vec":
                try:
                    embedding = self.embedding_client.embed_query(str(memory_record.get("content", "")))
                    self.memory_index.upsert_memory(
                        session_id=session_id,
                        memory=memory_record,
                        embedding=embedding,
                        provider=self.embedding_client.provider,
                        model=self.embedding_client.model,
                    )
                except Exception as e:
                    self.audit_logger.log(session_id, "memory_index_write_error", {"reason": str(e)})
            recent_norm.add(normalized)
            appended += 1

        if appended:
            self.audit_logger.log(
                session_id,
                "memory_write",
                {
                    "appended": appended,
                    "backend": self.config.memory_search_backend,
                    "embedding_provider": self.embedding_client.provider,
                    "embedding_model": self.embedding_client.model,
                },
            )

        all_memories = self.memory_store.load(session_id)
        if len(all_memories) > self.config.memory_limit:
            trimmed = all_memories[-self.config.memory_limit :]
            self.memory_store.overwrite(session_id, trimmed)
            self.memory_index.trim_session(
                session_id,
                {str(item.get("id", "")) for item in trimmed if str(item.get("id", ""))},
            )
            self.audit_logger.log(
                session_id,
                "memory_trim",
                {"before": len(all_memories), "after": len(trimmed)},
            )

    def _build_default_tools(self) -> list[Any]:
        guard = self.guard
        workspace_dir = self.workspace_dir
        exec_timeout_s = self.config.exec_timeout_s
        allow_write = self.config.allow_write
        allow_shell = self.config.allow_shell

        @tool("read")
        def read(path: str, full: bool = False) -> str:
            """Read a UTF-8 text file. Large files default to head/tail preview unless full=true."""
            try:
                file_path = guard.resolve(path)
                if not file_path.exists():
                    return f"Error: File '{file_path}' not found."
                if file_path.is_dir():
                    return f"Error: '{file_path}' is a directory, not a file."
                session_id = str(self._active_session_id or "main")
                used = self._read_budget_used(session_id)
                limit = max(1, int(self.config.read_output_budget_chars))
                if used >= limit:
                    return self._read_budget_guard_message(used=used)

                text = file_path.read_text(encoding="utf-8", errors="replace")
                output = self._smart_read_output(
                    rel_path=file_path.relative_to(workspace_dir),
                    text=text,
                    full=bool(full),
                )
                self._consume_read_budget(session_id, len(output))
                return output
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
                self._bump_session_mutation_tick(self._active_session_id)
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
                self._bump_session_mutation_tick(self._active_session_id)
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
                    if guard.is_blocked(child):
                        continue
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
                static_prefix = re.split(r"[\*\?\[]", glob, maxsplit=1)[0].strip()
                if static_prefix:
                    guard.resolve(static_prefix)
                rows = []
                for path in sorted(workspace_dir.glob(glob)):
                    if path.name.startswith(".git"):
                        continue
                    if guard.is_blocked(path):
                        continue
                    if path.is_file():
                        rows.append(str(path.relative_to(workspace_dir)))
                return "\n".join(rows[:2000]) if rows else "no matches"
            except Exception as e:
                return f"Error finding files for pattern '{glob}': {e}"

        @tool("grep")
        def grep(pattern: str, path: str = ".") -> str:
            """Search regex matches first to target line ranges before reading large files."""
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
                    if guard.is_blocked(file_path):
                        continue
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
                cwd_rel = str(run_dir.relative_to(workspace_dir))
                session_id = str(self._active_session_id or "main")

                duplicate = self._is_duplicate_exec_failure(
                    session_id=session_id,
                    cwd_rel=cwd_rel,
                    command=command,
                )
                if duplicate:
                    blocked_output = (
                        f"cwd={cwd_rel}\n"
                        "exit_code=blocked\n"
                        "stdout:\n\n"
                        "stderr:\nBlocked duplicate exec failure. "
                        "Strategy change required: inspect paths/files with read/ls/find/grep before retrying.\n"
                        "result=error\n"
                        "error_type=DUPLICATE_FAILURE\n"
                        f"error_signature={duplicate.get('error_signature', '-')}\n"
                        "retryable=false"
                    )
                    return _shorten(blocked_output, 24000)

                final_command, correction_note = self._maybe_correct_exec_command(command, run_dir)

                writer: Optional[Callable[[str], None]]
                try:
                    writer = get_stream_writer()
                except Exception:
                    writer = None
                
                if writer:
                    writer(f"exec started: {final_command}")
                    if correction_note:
                        writer(correction_note)
                
                try:
                    completed = subprocess.run(
                        final_command,
                        cwd=str(run_dir),
                        shell=True,
                        text=True,
                        capture_output=True,
                        timeout=max(1, int(timeout_s)),
                        encoding="utf-8",
                        errors="replace",
                    )
                    error_type = self._exec_error_type(completed.returncode, completed.stderr)
                    result = "ok" if completed.returncode == 0 else "error"
                    retryable = "true" if (result == "error" and self._exec_retryable(error_type)) else "false"
                    error_signature = (
                        self._exec_failure_signature(cwd_rel, final_command, completed.stderr)
                        if result == "error"
                        else "-"
                    )
                    if result == "error":
                        self._remember_exec_failure(
                            session_id,
                            cwd_rel=cwd_rel,
                            command=final_command,
                            error_type=error_type,
                            error_signature=error_signature,
                            stderr_core=self._core_stderr_line(completed.stderr),
                        )
                    
                    output = (
                        f"cwd={cwd_rel}\n"
                        f"exit_code={completed.returncode}\n"
                        f"stdout:\n{completed.stdout}\n"
                        f"stderr:\n{completed.stderr}\n"
                        f"result={result}\n"
                        f"error_type={error_type}\n"
                        f"error_signature={error_signature}\n"
                        f"retryable={retryable}"
                    )
                    
                    if writer:
                        writer(f"exec finished: exit_code={completed.returncode}")
                    return _shorten(output, 24000)
                except subprocess.TimeoutExpired:
                    if writer:
                        writer(f"exec timed out after {timeout_s}s: {final_command}")
                    error_signature = self._exec_failure_signature(
                        cwd_rel,
                        final_command,
                        f"timeout after {timeout_s}s",
                    )
                    self._remember_exec_failure(
                        session_id,
                        cwd_rel=cwd_rel,
                        command=final_command,
                        error_type="TIMEOUT",
                        error_signature=error_signature,
                        stderr_core=f"timeout after {timeout_s}s",
                    )
                    timeout_output = (
                        f"cwd={cwd_rel}\n"
                        "exit_code=timeout\n"
                        "stdout:\n\n"
                        f"stderr:\nCommand timed out after {timeout_s} seconds.\n"
                        "result=error\n"
                        "error_type=TIMEOUT\n"
                        f"error_signature={error_signature}\n"
                        "retryable=true"
                    )
                    return timeout_output
            except Exception as e:
                return f"Error executing command '{command}': {e}"

        @tool("memory_search")
        def memory_search(query: str, limit: int = 5) -> str:
            """Search memory and return matching memory IDs and snippets."""
            if not self.config.enable_memory:
                return "Memory is disabled."
            if (self.config.memory_mode or "").strip().lower() != "openclaw":
                return "memory_search is only available when PI_MEMORY_MODE=openclaw."
            try:
                rows = self.markdown_memory_store.search(query=query, limit=max(1, int(limit)))
                if not rows:
                    return "No memory matches."
                out: list[str] = []
                for row in rows:
                    snippet = re.sub(r"\s+", " ", str(row.get("content", "")).strip())
                    if len(snippet) > 140:
                        snippet = snippet[:137] + "..."
                    out.append(
                        f"- id={row.get('id')} score={row.get('score')} file={row.get('file')} tags={','.join(row.get('tags', [])) or '-'} :: {snippet}"
                    )
                return "\n".join(out)
            except Exception as e:
                return f"Error searching memory: {e}"

        @tool("memory_get")
        def memory_get(ids: str) -> str:
            """Get full memory entries by IDs. Input: comma or whitespace separated IDs."""
            if not self.config.enable_memory:
                return "Memory is disabled."
            if (self.config.memory_mode or "").strip().lower() != "openclaw":
                return "memory_get is only available when PI_MEMORY_MODE=openclaw."
            try:
                parsed_ids = [x for x in re.split(r"[\s,]+", ids or "") if x.strip()]
                rows = self.markdown_memory_store.get_by_ids(parsed_ids)
                if not rows:
                    return "No memory entries found for the requested IDs."
                blocks: list[str] = []
                for row in rows:
                    blocks.append(
                        "\n".join(
                            [
                                f"ID: {row.get('id')}",
                                f"Timestamp: {row.get('timestamp')}",
                                f"File: {row.get('file')}",
                                f"Tags: {', '.join(row.get('tags', [])) or '-'}",
                                f"Session: {row.get('session_id') or '-'}",
                                "Content:",
                                str(row.get("content", "")),
                            ]
                        )
                    )
                return "\n\n---\n\n".join(blocks)
            except Exception as e:
                return f"Error getting memory entries: {e}"

        @tool("memory_store")
        def memory_store(content: str, tags: str = "") -> str:
            """Store a memory entry in OpenClaw markdown memory files."""
            if not self.config.enable_memory:
                return "Memory is disabled."
            if (self.config.memory_mode or "").strip().lower() != "openclaw":
                return "memory_store is only available when PI_MEMORY_MODE=openclaw."
            try:
                tag_list = [x.strip() for x in re.split(r"[\s,]+", tags or "") if x.strip()]
                entry = self.markdown_memory_store.append(
                    session_id=self._active_session_id,
                    content=content,
                    tags=tag_list,
                )
                return f"Stored memory {entry['id']} in {entry['file']}"
            except Exception as e:
                return f"Error storing memory entry: {e}"

        @tool("todo_read")
        def todo_read() -> str:
            """Read the current session todo list. Returns all items with id, status, priority, and content."""
            items = self._todo_items
            if not items:
                return "No todos yet."
            lines = []
            for item in items:
                status_icon = {
                    "pending": "[ ]",
                    "in_progress": "[~]",
                    "completed": "[x]",
                    "cancelled": "[-]",
                }.get(item.get("status", "pending"), "[ ]")
                priority = item.get("priority", "medium")
                lines.append(
                    f"{status_icon} [{priority}] #{item['id']} {item['content']}"
                )
            return "\n".join(lines)

        @tool("todo_write")
        def todo_write(todos: str) -> str:
            """Replace the session todo list. todos is a JSON array of objects with fields:
            content (str, required), status ('pending'|'in_progress'|'completed'|'cancelled'),
            priority ('high'|'medium'|'low'). IDs are assigned automatically."""
            import json as _json
            try:
                raw = _json.loads(todos)
                if not isinstance(raw, list):
                    return "Error: todos must be a JSON array."
                valid_statuses = {"pending", "in_progress", "completed", "cancelled"}
                valid_priorities = {"high", "medium", "low"}
                new_items = []
                for i, item in enumerate(raw, start=1):
                    if not isinstance(item, dict) or not item.get("content"):
                        return f"Error: item #{i} must have a 'content' field."
                    status = item.get("status", "pending")
                    if status not in valid_statuses:
                        return f"Error: item #{i} has invalid status '{status}'."
                    priority = item.get("priority", "medium")
                    if priority not in valid_priorities:
                        return f"Error: item #{i} has invalid priority '{priority}'."
                    new_items.append({
                        "id": i,
                        "content": str(item["content"]),
                        "status": status,
                        "priority": priority,
                    })
                self._todo_items = new_items
                return f"Todo list updated: {len(new_items)} item(s)."
            except _json.JSONDecodeError as e:
                return f"Error: Invalid JSON - {e}"
            except Exception as e:
                return f"Error updating todos: {e}"

        return [read, write, edit, ls, find, grep, exec_tool, memory_search, memory_get, memory_store, todo_read, todo_write]

    def _filter_tools(
        self,
        allowlist: Optional[Sequence[str]] = None,
        denylist: Optional[Sequence[str]] = None,
    ) -> list[Any]:
        allow: set[str] = set()
        deny: set[str] = set()
        for name in (allowlist or []):
            allow.update(_tool_name_keys(str(name)))
        for name in (denylist or []):
            deny.update(_tool_name_keys(str(name)))
        tools = self.all_tools
        if allow:
            tools = [
                tool_obj
                for tool_obj in tools
                if _tool_name_keys(str(getattr(tool_obj, "name", ""))).intersection(allow)
            ]
        if deny:
            tools = [
                tool_obj
                for tool_obj in tools
                if not _tool_name_keys(str(getattr(tool_obj, "name", ""))).intersection(deny)
            ]
        return tools

    def _resolve_plan_policy(self, plan_mode: Optional[str]) -> PlanRuntimePolicy:
        mode = _normalize_plan_mode(plan_mode or self.config.plan_mode)
        if mode != "on":
            return PlanRuntimePolicy(mode="off")
        return PlanRuntimePolicy(
            mode="on",
            forced_deny_tools=("write", "edit", "exec", "memory_store"),
            skip_skill_precheck_fail=True,
            disable_legacy_memory_write=True,
            planner_directive=(
                "Plan mode is ON (read-only planning mode).\n"
                "- Do not execute implementation work.\n"
                "- Do not modify files or run shell commands that change state.\n"
                "- Provide an actionable execution plan grounded in observed evidence.\n"
                "- If critical context is missing, ask up to 3 specific follow-up questions."
            ),
        )

    def _apply_plan_policy_to_tools(self, tools: Sequence[Any], policy: PlanRuntimePolicy) -> list[Any]:
        if policy.mode != "on" or not policy.forced_deny_tools:
            return list(tools)
        deny_keys: set[str] = set()
        for name in policy.forced_deny_tools:
            deny_keys.update(_tool_name_keys(name))
        return [
            tool_obj
            for tool_obj in tools
            if not _tool_name_keys(str(getattr(tool_obj, "name", ""))).intersection(deny_keys)
        ]

    def _plan_directive_message(self, policy: PlanRuntimePolicy) -> Optional[dict[str, str]]:
        if policy.mode != "on" or not policy.planner_directive.strip():
            return None
        return {"role": "system", "content": policy.planner_directive.strip()}

    def _build_system_prompt(
        self,
        tools: Sequence[Any],
        session_id: str,
        skill: Optional[SkillSpec] = None,
    ) -> str:
        tool_lines = []
        for tool_obj in tools:
            description = getattr(tool_obj, "description", "") or ""
            description = " ".join(description.split())
            tool_lines.append(f"- {tool_obj.name}: {description}")

        tool_block = "\n".join(tool_lines)
        
        memory_status = (
            "Memory system: ENABLED."
            if self.config.enable_memory
            else "Memory system: DISABLED - Conversations are not persisted across sessions."
        )
        if self.config.enable_memory:
            memory_mode = (self.config.memory_mode or "").strip().lower()
            if memory_mode == "openclaw":
                memory_status += (
                    " OpenClaw mode active. Use memory_search -> memory_get -> memory_store."
                )
            else:
                memory_status += " Legacy mode active. Automatic memory recall/write is enabled."
        
        active_tool_names = {getattr(t, "name", "") for t in tools}
        todo_block = ""
        if "todo_read" in active_tool_names and "todo_write" in active_tool_names:
            todo_block = (
                "\nTask Management:\n"
                "Use todo_read and todo_write tools VERY frequently to track progress.\n"
                "- At the start of any multi-step task: create a todo list with todo_write\n"
                "- Before starting each step: call todo_read to review pending items\n"
                "- Update status to 'in_progress' when starting, 'completed' when done\n"
                "- Use todo_write to break large complex tasks into smaller steps\n"
                "- After every few actions: call todo_read to stay on track\n"
                "Statuses: pending | in_progress | completed | cancelled\n"
                "Priorities: high | medium | low\n"
            )

        skill_block = "Skill: none\n"
        if skill is not None:
            workflow = (skill.workflow or "- No workflow provided.").strip()
            output_format = (skill.output_format or "- No strict output format.").strip()
            required_tools = ", ".join(skill.required_tools) if skill.required_tools else "-"
            tool_allow = ", ".join(skill.tool_allow) if skill.tool_allow else "-"
            tool_deny = ", ".join(skill.tool_deny) if skill.tool_deny else "-"
            skill_block = (
                f"Skill: {skill.id} ({skill.name})\n"
                f"Skill Description: {skill.description}\n"
                f"Skill API Policy: {skill.api_policy}\n"
                f"Skill Required Tools: {required_tools}\n"
                f"Skill Tool Allow: {tool_allow}\n"
                f"Skill Tool Deny: {tool_deny}\n"
                "Skill Rules:\n"
                "- Prefer MCP/custom tools for API access (tool-first).\n"
                "- If needed and exec is available, curl fallback is allowed.\n"
                "- Never hardcode secrets/tokens in commands. Use environment variables only.\n"
                f"Skill Workflow:\n{workflow}\n"
                f"Skill Output Format:\n{output_format}\n"
            )

        return (
            "You are Pi, a minimal coding agent inspired by OpenClaw's embedded Pi runtime.\n\n"
            "Behavior rules:\n"
            "1. Use tools instead of guessing.\n"
            "2. Read files before editing them unless the user explicitly asked for a fresh file. "
            "For large files, use grep/find first to narrow scope.\n"
            "3. Prefer precise edits over full rewrites when possible.\n"
            "4. Stay inside the workspace unless the user explicitly expands scope.\n"
            "5. After tool use, summarize what you learned or changed.\n"
            "6. If a shell command fails, inspect the error and retry only when there is a clear fix.\n\n"
            "Policy: Sensitive/blocked paths are never accessed by generic file tools.\n\n"
            f"Workspace: {self.workspace_dir}\n"
            f"Session ID: {session_id}\n"
            f"{memory_status}\n"
            f"{todo_block}\n"
            f"{skill_block}\n"
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
        skill_name: Optional[str] = None,
        skill_mode: Optional[str] = None,
        plan_mode: Optional[str] = None,
    ) -> PiRunResult:
        callbacks = callbacks or NullCallbacks()
        self._active_session_id = session_id
        self._reset_read_budget(session_id)
        self._flush_pending_audit(session_id)
        plan_policy = self._resolve_plan_policy(plan_mode)
        selected_skill = self._select_skill(
            prompt=prompt,
            skill_name=skill_name,
            skill_mode=skill_mode,
            session_id=session_id,
        )
        explicit_skill = str(skill_name or self.config.skill_name or "").strip()
        if explicit_skill and selected_skill is None and _normalize_skill_mode(skill_mode or self.config.skill_mode) != "off":
            message = f"Requested skill not found: {explicit_skill}"
            self._clear_read_budget(session_id)
            return PiRunResult(session_id=session_id, final_text=message)
        base_tools = self._filter_tools(allowlist=allowlist, denylist=denylist)
        active_skill = selected_skill
        tools = list(base_tools)
        if active_skill is not None:
            tools = self._apply_skill_tool_policy(tools, active_skill)
        tools = self._apply_plan_policy_to_tools(tools, plan_policy)
        if plan_policy.mode == "on":
            self.audit_logger.log(
                session_id,
                "plan_policy_applied",
                {"mode": plan_policy.mode, "forced_deny": list(plan_policy.forced_deny_tools)},
            )
        precheck_notice: Optional[str] = None
        if active_skill is not None:
            ok, reason = self._skill_precheck(tools, active_skill)
            if not ok:
                if plan_policy.skip_skill_precheck_fail:
                    self.audit_logger.log(
                        session_id,
                        "skill_precheck_skipped_plan_mode",
                        {"skill_id": active_skill.id, "reason": reason},
                    )
                else:
                    self.audit_logger.log(
                        session_id,
                        "skill_precheck_fail",
                        {"skill_id": active_skill.id, "reason": reason},
                    )
                    fallback_message = (
                        f"{reason}\n"
                        "Continuing without skill constraints for this turn."
                    )
                    callbacks.on_event("custom", {"message": fallback_message})
                    self.audit_logger.log(
                        session_id,
                        "skill_precheck_fallback",
                        {"skill_id": active_skill.id, "reason": reason},
                    )
                    precheck_notice = (
                        f"Skill precheck failed for '{active_skill.id}'. "
                        "Proceed without that skill and continue with best-effort tool usage."
                    )
                    active_skill = None
                    tools = self._apply_plan_policy_to_tools(base_tools, plan_policy)
            else:
                self.audit_logger.log(
                    session_id,
                    "skill_precheck_ok",
                    {"skill_id": active_skill.id, "tool_count": len(tools)},
                )
        system_prompt = self._build_system_prompt(tools, session_id=session_id, skill=active_skill)
        agent = self._create_agent(tools=tools, system_prompt=system_prompt)

        history = self.session_store.load(session_id)
        history = self._compact_history(history, session_id=session_id)
        self.session_store.save(session_id, history)

        self.audit_logger.log(session_id, "user_prompt", {"text": prompt})
        mode = (self.config.memory_mode or "").strip().lower()
        recalled: list[dict[str, Any]] = []
        if mode != "openclaw":
            recalled = self._recall_memories(session_id=session_id, prompt=prompt)
        memory_message = self._memory_context_message(recalled)

        input_messages = [*history]
        if memory_message:
            input_messages.append(memory_message)
        failure_digest = self._failure_digest_message(session_id=session_id, limit=3)
        if failure_digest:
            input_messages.append(failure_digest)
        if precheck_notice:
            input_messages.append({"role": "system", "content": precheck_notice})
        plan_directive = self._plan_directive_message(plan_policy)
        if plan_directive:
            input_messages.append(plan_directive)
        input_messages.append({"role": "user", "content": prompt})

        seen_tool_starts: set[str] = set()
        seen_tool_ends: set[str] = set()
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        partial_chunks: list[str] = []
        final_text = ""
        repeat_limit = max(1, int(self.config.tool_repeat_limit))
        tool_call_signature_counts: dict[str, int] = {}
        repeat_abort_reason: Optional[str] = None
        stop_stream = False

        def _tool_call_signature(name: Any, args: Any) -> str:
            tool_name = str(name or "").strip().lower() or "<unknown>"
            try:
                encoded_args = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            except TypeError:
                encoded_args = repr(args)
            if len(encoded_args) > 300:
                encoded_args = encoded_args[:300] + "..."
            return f"{tool_name}:{encoded_args}"

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
                                signature = _tool_call_signature(call.get("name"), call.get("args", {}))
                                count = tool_call_signature_counts.get(signature, 0) + 1
                                tool_call_signature_counts[signature] = count
                                if count >= repeat_limit:
                                    repeat_abort_reason = (
                                        f"Aborted: identical tool call repeated {count} times "
                                        f"(limit={repeat_limit})."
                                    )
                                    callbacks.on_event(
                                        "custom",
                                        {
                                            "message": repeat_abort_reason,
                                            "tool_name": call.get("name"),
                                            "args": call.get("args", {}),
                                            "signature": signature,
                                        },
                                    )
                                    self.audit_logger.log(
                                        session_id,
                                        "tool_repeat_abort",
                                        {
                                            "reason": repeat_abort_reason,
                                            "tool_name": call.get("name"),
                                            "args": call.get("args", {}),
                                            "signature": signature,
                                            "repeat_count": count,
                                            "repeat_limit": repeat_limit,
                                        },
                                    )
                                    stop_stream = True
                                    break
                                item = {
                                    "id": call.get("id"),
                                    "name": call.get("name"),
                                    "args": call.get("args", {}),
                                }
                                tool_calls.append(item)
                                callbacks.on_tool_start(str(item["name"]), dict(item["args"] or {}))
                                self.audit_logger.log(session_id, "tool_start", item)
                            if stop_stream:
                                break
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
                        if str(name).strip().lower() == "exec":
                            exec_meta = self._parse_exec_meta(content)
                            if exec_meta.get("result") == "error":
                                is_error = True
                        item = {
                            "tool_call_id": tool_call_id,
                            "name": name,
                            "content": content,
                            "is_error": is_error,
                        }
                        tool_results.append(item)
                        callbacks.on_tool_end(str(name), content, is_error)
                        self.audit_logger.log(session_id, "tool_end", item)
                if stop_stream:
                    break
            if stop_stream:
                break

        if repeat_abort_reason:
            recovered = self._recover_after_tool_repeat_abort(
                session_id=session_id,
                user_prompt=prompt,
                repeat_abort_reason=repeat_abort_reason,
                tool_calls=tool_calls,
                tool_results=tool_results,
            )
            final_text = recovered or repeat_abort_reason
        elif not final_text:
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
        if mode != "openclaw" and not plan_policy.disable_legacy_memory_write:
            self._write_memories(session_id=session_id, prompt=prompt, final_text=final_text)
        self._clear_read_budget(session_id)

        return PiRunResult(
            session_id=session_id,
            final_text=final_text,
            tool_calls=tool_calls,
            tool_results=tool_results,
            audit_file=audit_file,
        )


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
    parser.add_argument("--session-dir", default=os.getenv("PI_SESSION_DIR", ".openclaw_pi/sessions"))
    parser.add_argument("--audit-dir", default=os.getenv("PI_AUDIT_DIR", ".openclaw_pi/audit"))
    parser.add_argument("--max-model-calls", type=int, default=int(os.getenv("PI_MAX_MODEL_CALLS", "16")))
    parser.add_argument("--tool-repeat-limit", type=int, default=int(os.getenv("PI_TOOL_REPEAT_LIMIT", "3")))
    parser.add_argument("--exec-timeout", type=int, default=int(os.getenv("PI_EXEC_TIMEOUT", "60")))
    parser.add_argument("--deny-tool", action="append", default=[t.strip() for t in os.getenv("PI_DENY_TOOL", "").split(",")] if os.getenv("PI_DENY_TOOL") else [])
    parser.add_argument("--allow-tool", action="append", default=[t.strip() for t in os.getenv("PI_ALLOW_TOOL", "").split(",")] if os.getenv("PI_ALLOW_TOOL") else [])
    parser.add_argument("--no-write", action="store_true", default=os.getenv("PI_NO_WRITE", "false").lower() == "true")
    parser.add_argument("--no-shell", action="store_true", default=os.getenv("PI_NO_SHELL", "false").lower() == "true")
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
    config = PiAgentConfig(
        model=args.model,
        workspace_dir=args.workspace,
        session_dir=args.session_dir,
        audit_dir=args.audit_dir,
        max_model_calls=args.max_model_calls,
        tool_repeat_limit=max(1, int(args.tool_repeat_limit)),
        exec_timeout_s=args.exec_timeout,
        allow_write=not args.no_write,
        allow_shell=not args.no_shell,
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
        mcp_enabled=bool(args.mcp_enabled) and not args.no_mcp,
        mcp_config_path=args.mcp_config,
        mcp_fail_fast=args.mcp_fail_fast,
        mcp_timeout_s=max(1, int(args.mcp_timeout)),
        skills_enabled=bool(args.skills_enabled) and not args.no_skills,
        skills_dir=args.skills_dir,
        skill_mode=args.skill_mode,
        skill_name=(str(args.skill).strip() or None),
        plan_mode=args.plan_mode,
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
