from __future__ import annotations

from .deps import *
from .models import *
from .workspace import WorkspaceGuard
from .stores import *
from .utils import *
from .mcp import McpStdioClient

class AgentWorkNotesMixin:
    def _recover_after_empty_final(
        self,
        session_id: str,
        user_prompt: str,
        tool_calls: Sequence[dict[str, Any]],
        tool_results: Sequence[dict[str, Any]],
    ) -> Optional[str]:
        """Perform one no-tool pass when tool work succeeded but final text was only private reasoning."""

        def _clip(value: Any, limit: int = 1800) -> str:
            text = str(value or "").strip()
            return text if len(text) <= limit else text[:limit] + "...[truncated]"

        calls = [
            f"- {str(item.get('name', '')).strip()} args={_clip(json.dumps(item.get('args', {}), ensure_ascii=False), 500)}"
            for item in list(tool_calls)[-4:]
        ]
        results = [
            f"- {str(item.get('name', '')).strip()} [{('error' if item.get('is_error') else 'ok')}]\n"
            f"{_clip(item.get('content', ''))}"
            for item in list(tool_results)[-4:]
        ]
        recovery_messages = [
            {
                "role": "system",
                "content": (
                    "You are Pi completing a tool-assisted task. The prior model output contained no "
                    "user-visible final answer. Do not call tools. Answer the original request using only "
                    "the supplied tool evidence. Return plain user-facing text without reasoning, analysis, "
                    "thinking, tool-call markup, or provider control tokens. State uncertainty when evidence "
                    "is insufficient."
                ),
            },
            {
                "role": "user",
                "content": "\n\n".join(
                    [
                        f"Original request:\n{user_prompt}",
                        "Tool calls:\n" + "\n".join(calls or ["- (none)"]),
                        "Tool evidence:\n" + "\n".join(results or ["- (none)"]),
                        "Now provide the final answer.",
                    ]
                ),
            },
        ]
        try:
            self.audit_logger.log(
                session_id,
                "empty_final_recovery_start",
                {"tool_calls": len(tool_calls), "tool_results": len(tool_results)},
            )
            response = self.model.invoke(recovery_messages)
            recovered = extract_text(response).strip()
            if not recovered:
                return None
            self.audit_logger.log(
                session_id,
                "empty_final_recovery_ok",
                {"chars": len(recovered)},
            )
            return recovered
        except Exception as exc:
            self.audit_logger.log(
                session_id,
                "empty_final_recovery_fail",
                {"error": str(exc)[:500]},
            )
            return None

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

    def _effective_plan_mode(self, session_id: str, requested_mode: Optional[str]) -> str:
        explicit = _normalize_plan_mode(requested_mode or "")
        if explicit in {"on", "off"} and str(requested_mode or "").strip():
            return explicit
        override = self._session_plan_mode_overrides.get(str(session_id or "main"))
        if override in {"on", "off"}:
            return str(override)
        return _normalize_plan_mode(self.config.plan_mode)

    def _set_session_plan_mode(self, session_id: str, mode: str) -> str:
        normalized = _normalize_plan_mode(mode)
        self._session_plan_mode_overrides[str(session_id or "main")] = normalized
        return normalized

    def _artifact_root(self) -> Path:
        rooted = self.guard.user_artifact_root()
        if rooted is not None:
            rooted.mkdir(parents=True, exist_ok=True)
            return rooted
        fallback = (self.workspace_dir / "artifacts").resolve()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    def _tool_result_root(self) -> Path:
        raw = str(self.config.tool_result_artifact_dir or "tool-results").strip().strip("/\\")
        safe = Path(raw or "tool-results")
        root = (self._artifact_root() / safe).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _relative_workspace_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.workspace_dir).as_posix()
        except Exception:
            return str(path)

    def _maybe_offload_tool_result(self, label: str, content: str) -> str:
        limit = max(1000, int(self.config.max_tool_result_chars))
        text = str(content or "")
        if len(text) <= limit:
            return text
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
        safe_label = _safe_tool_name(label) or "tool_result"
        path = self._tool_result_root() / f"{int(_now_ts() * 1000)}-{safe_label}-{digest}.txt"
        path.write_text(text, encoding="utf-8")
        rel = self._relative_workspace_path(path)
        head_limit = max(500, limit // 2)
        tail_limit = max(500, limit // 4)
        return (
            "tool_result_truncated=true\n"
            f"full_result_path={rel}\n"
            f"char_count={len(text)}\n"
            f"sha256_16={digest}\n\n"
            f"--- preview head ---\n{text[:head_limit]}\n\n"
            f"--- preview tail ---\n{text[-tail_limit:]}"
        )

    def _work_note_root(self) -> Path:
        raw = str(self.config.work_note_artifact_dir or "work-notes").strip().strip("/\\")
        safe = Path(raw or "work-notes")
        root = (self._artifact_root() / safe).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _work_note_path(self, session_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(session_id or "main"))
        return self._work_note_root() / f"{safe}.md"

    def _work_note_template(self, session_id: str) -> str:
        now = datetime.now(timezone.utc).isoformat()
        lines = [
            f"# {WORK_NOTE_SECTIONS[0]}",
            f"PiAgent work note for session `{session_id or 'main'}`.",
            "",
        ]
        for section in WORK_NOTE_SECTIONS[1:]:
            lines.extend([f"# {section}", "", ""])
        lines.extend([f"<!-- created_at: {now} -->", ""])
        return "\n".join(lines)

    def _ensure_work_note(self, session_id: str) -> Path:
        path = self._work_note_path(session_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self._work_note_template(session_id), encoding="utf-8")
            self.audit_logger.log(session_id, "work_note_create", {"path": str(path)})
        return path

    def _read_work_note(self, session_id: str) -> str:
        path = self._ensure_work_note(session_id)
        return path.read_text(encoding="utf-8", errors="replace")

    def _section_bounds(self, text: str, section: str) -> tuple[int, int]:
        target = section.strip().lower()
        lines = text.splitlines()
        header_index: Optional[int] = None
        for i, line in enumerate(lines):
            if line.startswith("# ") and line[2:].strip().lower() == target:
                header_index = i
                break
        if header_index is None:
            raise ValueError(f"unknown work note section: {section}")
        end = len(lines)
        for i in range(header_index + 1, len(lines)):
            if lines[i].startswith("# "):
                end = i
                break
        return header_index, end

    def _update_work_note_section(self, session_id: str, section: str, content: str, mode: str = "append") -> str:
        if not self.config.enable_work_notes:
            return "Error: work notes are disabled."
        normalized_section = section.strip()
        valid = {item.lower(): item for item in WORK_NOTE_SECTIONS}
        if normalized_section.lower() not in valid:
            return f"Error: section must be one of: {', '.join(WORK_NOTE_SECTIONS)}"
        if not str(content or "").strip():
            return "Error: content is required."
        normalized_mode = str(mode or "append").strip().lower()
        if normalized_mode not in {"append", "replace"}:
            return "Error: mode must be 'append' or 'replace'."

        path = self._ensure_work_note(session_id)
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        header, end = self._section_bounds(text, valid[normalized_section.lower()])
        block = str(content).strip()
        if normalized_mode == "append":
            stamp = datetime.now(timezone.utc).isoformat()
            existing = "\n".join(lines[header + 1 : end]).strip()
            next_content = (existing + "\n\n" if existing else "") + f"- {stamp}\n{block}"
        else:
            next_content = block
        replacement = [lines[header], *next_content.splitlines(), ""]
        updated = lines[:header] + replacement + lines[end:]
        path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
        self.audit_logger.log(
            session_id,
            "work_note_update",
            {"path": str(path), "section": valid[normalized_section.lower()], "mode": normalized_mode, "chars": len(block)},
        )
        return f"updated work note section '{valid[normalized_section.lower()]}' at {self._relative_workspace_path(path)}"

    def _search_work_note(self, session_id: str, pattern: str, section: str = "") -> str:
        if not self.config.enable_work_notes:
            return "Error: work notes are disabled."
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Error: Invalid regex pattern '{pattern}' - {e}"
        text = self._read_work_note(session_id)
        search_text = text
        section_name = str(section or "").strip()
        if section_name:
            try:
                header, end = self._section_bounds(text, section_name)
            except ValueError as e:
                return f"Error: {e}"
            lines = text.splitlines()
            search_text = "\n".join(lines[header:end])
        hits = []
        current = "-"
        for i, line in enumerate(search_text.splitlines(), start=1):
            if line.startswith("# "):
                current = line[2:].strip()
            if regex.search(line):
                hits.append(f"{current}:{i}: {line}")
        return self._maybe_offload_tool_result("work_note_search", "\n".join(hits) or "no matches")

    def _extract_proposed_plan(self, text: str) -> Optional[str]:
        match = re.search(r"<proposed_plan>\s*(.*?)\s*</proposed_plan>", text or "", flags=re.DOTALL | re.IGNORECASE)
        if not match:
            return None
        plan = match.group(1).strip()
        return plan or None

    def _auto_update_work_note(
        self,
        *,
        session_id: str,
        prompt: str,
        final_text: str,
        tool_calls: Sequence[dict[str, Any]],
        tool_results: Sequence[dict[str, Any]],
        plan_policy: PlanRuntimePolicy,
    ) -> None:
        if not (self.config.enable_work_notes and self.config.work_note_auto_update):
            return
        plan = self._extract_proposed_plan(final_text)
        if plan:
            self._update_work_note_section(session_id, "Task Spec", plan, mode="replace")
            self._update_work_note_section(
                session_id,
                "Current State",
                "Plan mode produced a proposed implementation plan. Await approval or proceed in implementation mode.",
                mode="replace",
            )
        failures = [r for r in tool_results if bool(r.get("is_error", False))]
        if failures:
            lines = [f"{row.get('name')}: {_shorten(str(row.get('content', '')), 240)}" for row in failures[:5]]
            self._update_work_note_section(session_id, "Errors", "\n".join(lines), mode="append")
        summary = [
            f"Prompt: {_shorten(prompt, 240)}",
            f"Final: {_shorten(final_text, 360)}",
            f"Tool calls: {len(tool_calls)}",
            f"Tool results: {len(tool_results)}",
            f"Plan mode: {plan_policy.mode}",
        ]
        self._update_work_note_section(session_id, "Worklog", "\n".join(summary), mode="append")

__all__ = [name for name in globals() if not name.startswith("__")]
