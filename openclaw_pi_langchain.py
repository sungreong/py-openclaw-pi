# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
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
    enable_memory: bool = True
    memory_dir: str = ".openclaw_pi/memory"
    memory_limit: int = 200
    memory_recall_limit: int = 5
    memory_search_backend: str = "sqlite-vec"
    memory_embedding_provider: str = "auto"
    memory_embedding_model: str = "text-embedding-3-small"

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
        self.memory_store = FlatMemoryStore(config.memory_root())
        self.memory_index = SqliteVecMemoryIndex(config.memory_root() / "memory_vec.sqlite", self.audit_logger)
        self.embedding_client = MemoryEmbeddingClient(
            provider=config.memory_embedding_provider,
            model=config.memory_embedding_model,
        )

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
        recalled = self._recall_memories(session_id=session_id, prompt=prompt)
        memory_message = self._memory_context_message(recalled)

        input_messages = [*history]
        if memory_message:
            input_messages.append(memory_message)
        input_messages.append({"role": "user", "content": prompt})

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
        self._write_memories(session_id=session_id, prompt=prompt, final_text=final_text)

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
    parser.add_argument("--no-memory", action="store_true", default=os.getenv("PI_NO_MEMORY", "false").lower() == "true")
    parser.add_argument("--memory-limit", type=int, default=int(os.getenv("PI_MEMORY_LIMIT", "200")))
    parser.add_argument("--memory-recall-limit", type=int, default=int(os.getenv("PI_MEMORY_RECALL_LIMIT", "5")))
    parser.add_argument("--memory-dir", default=os.getenv("PI_MEMORY_DIR", ".openclaw_pi/memory"))
    parser.add_argument("--memory-search-backend", default=os.getenv("PI_MEMORY_SEARCH_BACKEND", "sqlite-vec"))
    parser.add_argument("--memory-embedding-provider", default=os.getenv("PI_MEMORY_EMBEDDING_PROVIDER", "auto"))
    parser.add_argument("--memory-embedding-model", default=os.getenv("PI_MEMORY_EMBEDDING_MODEL", "text-embedding-3-small"))
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
        enable_memory=not args.no_memory,
        memory_limit=max(1, args.memory_limit),
        memory_recall_limit=max(1, args.memory_recall_limit),
        memory_dir=args.memory_dir,
        memory_search_backend=args.memory_search_backend,
        memory_embedding_provider=args.memory_embedding_provider,
        memory_embedding_model=args.memory_embedding_model,
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
