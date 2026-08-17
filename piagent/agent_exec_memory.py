from __future__ import annotations

from .deps import *
from .models import *
from .workspace import WorkspaceGuard
from .stores import *
from .utils import *
from .mcp import McpStdioClient

class AgentExecMemoryMixin:
    def _is_package_install_shell_command(self, command: str) -> bool:
        low = str(command or "").strip().lower()
        patterns = (
            r"(?:^|[;&|]\s*)(?:pip|pip3)\s+install\b",
            r"(?:^|[;&|]\s*)(?:python(?:3(?:\.\d+)?)?|py)\s+-m\s+pip\s+install\b",
            r"(?:^|[;&|]\s*)uv\s+pip\s+install\b",
            r"(?:^|[;&|]\s*)poetry\s+add\b",
            r"(?:^|[;&|]\s*)conda\s+install\b",
        )
        return any(re.search(pattern, low) for pattern in patterns)

    def _infer_python_script_run_dir(self, command: str) -> Optional[Path]:
        try:
            tokens = shlex.split(str(command or ""))
        except Exception:
            return None
        if len(tokens) < 2:
            return None
        launcher = str(tokens[0]).lower()
        if launcher not in {"python", "python3", "py", "venv/bin/python", "./venv/bin/python"}:
            return None
        script = Path(tokens[1].strip().strip("\"'"))
        if not script.is_absolute():
            return None
        if not script.exists():
            return None
        root = self._artifact_root()
        try:
            script.resolve().relative_to(root)
        except Exception:
            return None
        return script.resolve().parent

    def _is_binary_data(self, raw: bytes, path: Path) -> bool:
        ext = path.suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".pdf", ".zip", ".gz", ".exe", ".dll", ".bin"}:
            return True
        if b"\x00" in raw[:2048]:
            return True
        return False

    def _is_dangerous_shell_command(self, command: str) -> bool:
        low = str(command or "").strip().lower()
        dangerous_patterns = (
            r"\brm\s+-rf\s+/",
            r"\brm\s+-rf\s+\*",
            r"\brm\s+-rf\s+\.",
            r"\bgit\s+reset\s+--hard\b",
            r"\bgit\s+clean\s+-[^\s]*f",
            r"\bgit\s+push\b.*\s--force\b",
            r"\bsudo\b",
            r"\bchmod\s+777\b",
            r"\bchown\b",
            r"\b(drop|truncate)\s+(database|table)\b",
            r"\b(pip|pip3)\s+install\b",
            r"\bpython\s+-m\s+pip\s+install\b",
            r"\b(npm|pnpm|yarn)\s+(install|add|remove)\b",
            r"\bcurl\b.*\|\s*(sh|bash)\b",
            r"\bwget\b.*\|\s*(sh|bash)\b",
            r"\bdel\s+/f\s+/s\s+/q\b",
            r"\bformat\s+[a-z]:",
            r"\bmkfs\.",
            r"\bshutdown\b",
            r"\breboot\b",
            r":\(\)\s*\{\s*:\|\:&\s*\};:",
        )
        return any(re.search(p, low) for p in dangerous_patterns)

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
            if key in {"cwd", "exit_code", "result", "error_type", "error_signature", "retryable", "full_result_path"}:
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
        flat_memories = self.memory_store.load(session_id)
        markdown_memories = self._recall_markdown_memories(session_id=session_id, prompt=prompt)
        memories = self._merge_recalled_memories(flat_memories, markdown_memories)
        if not memories:
            return []

        if backend == "sqlite-vec" and flat_memories:
            try:
                query_embedding = self.embedding_client.embed_query(prompt)
                vec_selected = self.memory_index.search(
                    session_id=session_id,
                    query_embedding=query_embedding,
                    limit=self.config.memory_recall_limit,
                    provider=self.embedding_client.provider,
                    model=self.embedding_client.model,
                )
                selected = self._merge_recalled_memories(vec_selected, markdown_memories)[: self.config.memory_recall_limit]
                self.audit_logger.log(
                    session_id,
                    "memory_recall",
                    {
                        "backend": "sqlite-vec",
                        "requested": self.config.memory_recall_limit,
                        "returned": len(selected),
                        "markdown_returned": len(markdown_memories),
                        "embedding_provider": self.embedding_client.provider,
                        "embedding_model": self.embedding_client.model,
                        "sqlite_vec_ready": self.memory_index.sqlite_vec_ready,
                    },
                )
                return selected
            except Exception as e:
                self.audit_logger.log(session_id, "memory_recall_fallback", {"reason": str(e)})

        prompt_tokens = set(re.findall(r"[a-zA-Z0-9가-힣_]+", prompt.lower()))

        def score(item: dict[str, Any]) -> tuple[int, int, float]:
            text_tokens = set(re.findall(r"[a-zA-Z0-9가-힣_]+", str(item.get("content", "")).lower()))
            overlap = len(prompt_tokens & text_tokens)
            tags = {str(tag).lower() for tag in item.get("tags", []) if str(tag).strip()}
            preference_boost = 4 if tags & {"user_preference", "preference", "language"} else 0
            ts = self._memory_ts(item)
            return preference_boost, overlap, ts

        selected = sorted(memories, key=score, reverse=True)[: self.config.memory_recall_limit]
        selected = [m for m in selected if str(m.get("content", "")).strip()]
        self.audit_logger.log(
            session_id,
            "memory_recall",
            {
                "backend": "keyword",
                "requested": self.config.memory_recall_limit,
                "returned": len(selected),
                "flat_available": len(flat_memories),
                "markdown_available": len(markdown_memories),
            },
        )
        return selected

    def _memory_ts(self, item: dict[str, Any]) -> float:
        raw_ts = item.get("ts")
        if isinstance(raw_ts, (int, float)):
            return float(raw_ts)
        raw_timestamp = str(item.get("timestamp", "") or "")
        if raw_timestamp:
            try:
                return datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00")).timestamp()
            except Exception:
                return 0.0
        return 0.0

    def _recall_markdown_memories(self, session_id: str, prompt: str) -> list[dict[str, Any]]:
        if (self.config.memory_mode or "").strip().lower() != "openclaw":
            return []
        limit = max(1, int(self.config.memory_recall_limit))
        rows = self.markdown_memory_store.search(prompt, limit=limit)
        preference_rows = [
            row
            for row in self.markdown_memory_store.recent(session_id=session_id, limit=25)
            if {str(tag).lower() for tag in row.get("tags", []) if str(tag).strip()}
            & {"user_preference", "preference", "language"}
        ]
        normalized: list[dict[str, Any]] = []
        for row in [*preference_rows, *rows]:
            tags = [str(tag) for tag in row.get("tags", []) if str(tag).strip()]
            kind = "preference" if {tag.lower() for tag in tags} & {"user_preference", "preference", "language"} else "fact"
            normalized.append(
                {
                    "id": row.get("id"),
                    "kind": kind,
                    "content": row.get("content", ""),
                    "tags": tags,
                    "session_id": row.get("session_id", ""),
                    "timestamp": row.get("timestamp", ""),
                    "source": "markdown",
                }
            )
        return self._merge_recalled_memories(normalized, [])

    def _merge_recalled_memories(
        self,
        primary: Sequence[dict[str, Any]],
        secondary: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in [*primary, *secondary]:
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            key = self._normalize_memory_text(content)
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(item))
        return merged

    def _memory_context_message(self, recalled: Sequence[dict[str, Any]]) -> Optional[dict[str, str]]:
        if not recalled:
            return None
        rows: list[str] = []
        budget = 1200
        consumed = 0
        for mem in recalled:
            tags = ",".join(str(tag) for tag in mem.get("tags", []) if str(tag).strip())
            tag_suffix = f" [tags={tags}]" if tags else ""
            line = f"- ({mem.get('kind', 'fact')}) {mem.get('content', '')}{tag_suffix}".strip()
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

    def _session_note_path(self, session_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(session_id or "main"))
        return self.session_store.root / f"{safe}.notes.md"

    def _append_session_note(
        self,
        session_id: str,
        *,
        prompt: str,
        final_text: str,
        tool_calls: Sequence[dict[str, Any]],
        tool_results: Sequence[dict[str, Any]],
    ) -> None:
        if not self.config.enable_session_notes:
            return
        parts = [
            f"## {datetime.now(timezone.utc).isoformat()}",
            f"- prompt: {_shorten(prompt, 180)}",
            f"- final: {_shorten(final_text, 240)}",
            f"- tool_calls: {len(tool_calls)}",
            f"- tool_results: {len(tool_results)}",
        ]
        failed = [r for r in tool_results if bool(r.get("is_error", False))]
        if failed:
            parts.append("- failures:")
            for row in failed[:4]:
                parts.append(f"  - {row.get('name')}: {_shorten(str(row.get('content', '')), 180)}")
        path = self._session_note_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write("\n".join(parts) + "\n\n")
        notes = self._session_notes.setdefault(str(session_id or "main"), [])
        notes.append(parts[1])
        if len(notes) > 20:
            del notes[: len(notes) - 20]
        self.audit_logger.log(session_id, "session_note_write", {"path": str(path), "chars": sum(len(p) for p in parts)})

__all__ = [name for name in globals() if not name.startswith("__")]
