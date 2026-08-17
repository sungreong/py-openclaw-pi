from __future__ import annotations

from .deps import *
from .utils import *

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


class SessionFragmentStore:
    """Append-only, keyword-searchable fragments from user and assistant session turns."""

    MAX_FRAGMENT_CHARS = 900
    FRAGMENT_OVERLAP_CHARS = 120

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, session_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(session_id or "main"))
        return self.root / f"{safe}.fragments.jsonl"

    @classmethod
    def _split_content(cls, content: str) -> list[str]:
        text = str(content or "").replace("\r\n", "\n").strip()
        if not text:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(text):
            hard_end = min(len(text), start + cls.MAX_FRAGMENT_CHARS)
            end = hard_end
            if hard_end < len(text):
                boundary = max(text.rfind("\n", start, hard_end), text.rfind(" ", start, hard_end))
                if boundary >= start + cls.MAX_FRAGMENT_CHARS // 2:
                    end = boundary
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            start = max(start + 1, end - cls.FRAGMENT_OVERLAP_CHARS)
        return chunks

    def append_turn(self, session_id: str, prompt: str, final_text: str) -> int:
        path = self.path_for(session_id)
        timestamp = _now_ts()
        turn_key = f"turn-{int(timestamp * 1_000_000)}"
        records: list[dict[str, Any]] = []
        for message_index, (role, content) in enumerate((("user", prompt), ("assistant", final_text))):
            for chunk_index, chunk in enumerate(self._split_content(content)):
                digest = hashlib.sha256(
                    f"{session_id}\n{turn_key}\n{role}\n{message_index}\n{chunk_index}\n{chunk}".encode("utf-8")
                ).hexdigest()[:16]
                records.append(
                    {
                        "id": f"frag-{digest}",
                        "ts": timestamp,
                        "turn_id": turn_key,
                        "role": role,
                        "chunk_index": chunk_index,
                        "content": chunk,
                        "char_count": len(chunk),
                    }
                )
        if not records:
            return 0
        with path.open("a", encoding="utf-8") as fp:
            for record in records:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        return len(records)

    def load(self, session_id: str) -> list[dict[str, Any]]:
        path = self.path_for(session_id)
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            content = str(item.get("content", "")).strip()
            fragment_id = str(item.get("id", "")).strip()
            if role in {"user", "assistant"} and content and fragment_id:
                rows.append(item)
        return rows

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    @classmethod
    def _query_terms(cls, query: str) -> list[str]:
        normalized = cls._normalize(query)
        terms = re.findall(r"[a-zA-Z0-9가-힣_]+", normalized)
        return list(dict.fromkeys(term for term in terms if len(term) >= 2))

    @classmethod
    def _snippet(cls, content: str, terms: Sequence[str], limit: int = 360) -> str:
        compact = re.sub(r"\s+", " ", str(content or "")).strip()
        if len(compact) <= limit:
            return compact
        lowered = compact.lower()
        positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
        center = min(positions) if positions else 0
        start = max(0, center - limit // 3)
        end = min(len(compact), start + limit)
        prefix = "…" if start else ""
        suffix = "…" if end < len(compact) else ""
        return prefix + compact[start:end].strip() + suffix

    def search(self, session_id: str, query: str, limit: int = 5, role: str = "") -> list[dict[str, Any]]:
        normalized_query = self._normalize(query)
        terms = self._query_terms(query)
        if not normalized_query or not terms:
            return []
        role_filter = str(role or "").strip().lower()
        scored: list[tuple[int, float, int, dict[str, Any]]] = []
        for position, row in enumerate(self.load(session_id)):
            row_role = str(row.get("role", "")).lower()
            if role_filter and row_role != role_filter:
                continue
            normalized_content = self._normalize(str(row.get("content", "")))
            matched = [term for term in terms if term in normalized_content]
            if not matched:
                continue
            score = len(matched) * 10
            if normalized_query in normalized_content:
                score += 100
            if len(matched) == len(terms):
                score += 25
            result = {
                "id": row.get("id"),
                "role": row_role,
                "turn_id": row.get("turn_id"),
                "ts": row.get("ts"),
                "score": score,
                "matched_terms": matched,
                "snippet": self._snippet(str(row.get("content", "")), matched),
            }
            scored.append((score, float(row.get("ts", 0) or 0), position, result))
        scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [item[3] for item in scored[: max(1, min(20, int(limit)))]]

    def get_by_ids(self, session_id: str, ids: Sequence[str]) -> list[dict[str, Any]]:
        requested = [str(item).strip() for item in ids if str(item).strip()]
        if not requested:
            return []
        rows_by_id = {str(row.get("id", "")): row for row in self.load(session_id)}
        return [rows_by_id[fragment_id] for fragment_id in requested if fragment_id in rows_by_id]


class SessionEvidenceStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, session_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", session_id)
        return self.root / f"{safe}.evidence.jsonl"

    def load(self, session_id: str, limit: int = 0) -> list[dict[str, Any]]:
        path = self.path_for(session_id)
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
        if int(limit) > 0:
            return rows[-int(limit) :]
        return rows

    def append(self, session_id: str, record: dict[str, Any]) -> Path:
        path = self.path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
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

    def recent(self, session_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for file_path in self._iter_daily_files():
            rows.extend(self._parse_entries_from_file(file_path))
            if len(rows) >= max(1, int(limit)) * 2:
                break
        sid = str(session_id or "").strip()
        if sid:
            session_rows = [row for row in rows if str(row.get("session_id", "")).strip() == sid]
            global_rows = [row for row in rows if str(row.get("session_id", "")).strip() != sid]
            rows = session_rows + global_rows
        rows.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
        return rows[: max(1, int(limit))]

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

__all__ = [name for name in globals() if not name.startswith("__")]
