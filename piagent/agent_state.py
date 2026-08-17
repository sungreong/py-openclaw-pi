from __future__ import annotations

from .deps import *
from .models import *
from .workspace import WorkspaceGuard
from .stores import *
from .utils import *
from .mcp import McpStdioClient

class AgentStateMixin:
    def _configure_edit_path_scope(
        self,
        session_id: str,
        permission_profile: Optional[str],
        edit_paths: Optional[Sequence[str]],
    ) -> None:
        from .permissions import normalize_edit_paths, normalize_permission_profile

        profile = normalize_permission_profile(permission_profile)
        sid = str(session_id or "main")
        if profile != "edit":
            self._session_edit_path_scopes.pop(sid, None)
            return

        requested = normalize_edit_paths(edit_paths)
        if not requested:
            raise ValueError("edit mode requires at least one edit path")

        resolved: set[Path] = set()
        for raw_path in requested:
            path = self.guard.resolve(raw_path)
            if not path.exists():
                raise ValueError(f"edit mode only accepts existing files: {raw_path}")
            if not path.is_file():
                raise ValueError(f"edit path must be a file: {raw_path}")
            resolved.add(path)
        self._session_edit_path_scopes[sid] = frozenset(resolved)

    def _assert_edit_path_allowed(self, session_id: str, path: Path) -> None:
        sid = str(session_id or "main")
        scope = self._session_edit_path_scopes.get(sid)
        if scope is None:
            return
        resolved = path.resolve()
        if resolved not in scope:
            allowed = ", ".join(
                str(item.relative_to(self.workspace_dir)) for item in sorted(scope, key=str)
            )
            raise PermissionError(
                f"edit mode blocks '{path}'; allowed edit paths: {allowed or '-'}"
            )

    def _has_scoped_edit_permission(self, session_id: str) -> bool:
        return str(session_id or "main") in self._session_edit_path_scopes

    def _edit_scope_display(self, session_id: str) -> list[str]:
        scope = self._session_edit_path_scopes.get(str(session_id or "main"), frozenset())
        return [str(path.relative_to(self.workspace_dir)) for path in sorted(scope, key=str)]

    def _effective_permission_mode(self, plan_mode: Optional[str] = None) -> str:
        if _normalize_plan_mode(plan_mode or self.config.plan_mode) == "on":
            return "plan"
        mode = str(self.config.permission_mode or "default").strip().lower()
        return mode if mode in {"default", "plan", "accept_edits", "dont_ask"} else "default"

    def _readonly_command_verdict(self, command: str) -> tuple[bool, str]:
        try:
            tokens = shlex.split(str(command or ""), posix=os.name != "nt")
        except Exception:
            return False, "could not parse command"
        if not tokens:
            return False, "empty command"
        lowered = [t.lower() for t in tokens]
        command_text = " ".join(lowered)
        if any(op in command_text for op in (" > ", " >> ", " 2>", " | tee ", "&& tee ")):
            return False, "redirects and tee can write files"
        mutating = {
            "rm", "del", "erase", "rmdir", "mkdir", "touch", "copy", "cp", "mv", "move",
            "git add", "git commit", "git push", "git reset", "git checkout", "git clean",
            "pip install", "python -m pip install", "npm install", "pnpm install", "yarn add",
            "npm run build", "npm run lint -- --fix", "ruff --fix", "black", "prettier --write",
            "pytest --snapshot-update",
        }
        joined = " ".join(lowered[:4])
        for pattern in mutating:
            if joined.startswith(pattern) or command_text.startswith(pattern):
                return False, f"mutating command pattern: {pattern}"
        safe_roots = {
            "ls", "dir", "pwd", "cat", "type", "head", "tail", "find", "grep", "rg", "wc",
            "git", "python", "python3", "py", "pytest", "ruff", "mypy", "npm", "pnpm", "yarn",
        }
        root = lowered[0]
        if root not in safe_roots:
            return False, f"command root is not known read-only: {root}"
        if root == "git" and len(lowered) > 1 and lowered[1] not in {"status", "diff", "log", "show", "rev-parse", "branch"}:
            return False, f"git subcommand is not read-only: {lowered[1]}"
        if root in {"npm", "pnpm", "yarn"} and "test" not in lowered and "lint" not in lowered and "typecheck" not in lowered:
            return False, "package command is not an obvious check"
        return True, "read-only command"

    def _is_private_or_metadata_url(self, url: str) -> tuple[bool, str]:
        try:
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname
            if not host:
                return True, "missing host"
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            for info in infos:
                raw_ip = info[4][0]
                ip = ipaddress.ip_address(raw_ip)
                if (
                    ip.is_loopback
                    or ip.is_private
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                    or ip.is_unspecified
                ):
                    return True, f"blocked private/link-local address: {ip}"
            return False, ""
        except Exception as e:
            return True, f"url safety check failed: {e}"

    def _session_todos(self, session_id: str) -> list[dict[str, Any]]:
        key = str(session_id or "main")
        if key not in self._session_todo_items:
            self._session_todo_items[key] = []
        return self._session_todo_items[key]

    def _should_remind_todo(self, session_id: str, prompt: str, tools: Sequence[Any]) -> bool:
        names = {str(getattr(t, "name", "")).strip() for t in tools}
        if "todo_write" not in names or "todo_read" not in names:
            return False
        if self._session_todos(session_id):
            return False
        text = str(prompt or "").lower()
        markers = ("implement", "fix", "refactor", "plan", "개발", "구현", "수정", "분석", "테스트", "계획")
        return len(text) > 80 or any(marker in text for marker in markers)

    def _session_artifact_aliases(self, session_id: str) -> dict[str, str]:
        key = str(session_id or "main")
        if key not in self._session_artifact_path_map:
            self._session_artifact_path_map[key] = {}
        return self._session_artifact_path_map[key]

    def _register_artifact_alias(self, session_id: str, requested_path: str, resolved_path: Path) -> None:
        if not self.user_id:
            return
        aliases = self._session_artifact_aliases(session_id)
        req = str(requested_path or "").strip()
        if req:
            aliases[req] = str(resolved_path)
            aliases[req.replace("\\", "/")] = str(resolved_path)
        try:
            rel = resolved_path.relative_to(self.workspace_dir).as_posix()
            aliases[rel] = str(resolved_path)
            aliases[f"./{rel}"] = str(resolved_path)
        except Exception:
            pass

    def _force_user_artifact_output_path(self, raw_path: str, session_id: str) -> str:
        if not self.user_id:
            return raw_path
        text = str(raw_path or "").strip()
        if not text:
            return raw_path
        root = self._artifact_root()
        candidate = Path(text)
        if candidate.is_absolute():
            try:
                rel = candidate.resolve().relative_to(self.workspace_dir)
            except Exception:
                return raw_path
        else:
            rel = Path(text)

        if rel.parts[:3] == ("artifacts", "users", self.user_id):
            return raw_path
        if len(rel.parts) >= 3 and rel.parts[0] == "artifacts" and rel.parts[1] == "users":
            return raw_path

        if not rel.parts:
            return raw_path

        first = str(rel.parts[0]).lower()
        if first in {"reports", "artifacts", "outputs"}:
            return raw_path

        # Strong isolation: top-level ad-hoc files are always redirected in user mode,
        # even if they already exist from previous non-isolated runs.
        if len(rel.parts) > 1:
            workspace_target = (self.workspace_dir / rel).resolve()
            if workspace_target.exists():
                return raw_path

        forced = (root / "workspace" / rel).resolve()
        self._register_artifact_alias(session_id, text, forced)
        return str(forced)

    def _resolve_artifact_alias_path(self, session_id: str, raw_path: str) -> Optional[Path]:
        aliases = self._session_artifact_aliases(session_id)
        key = str(raw_path or "").strip()
        if not key:
            return None
        mapped = aliases.get(key) or aliases.get(key.replace("\\", "/"))
        if not mapped:
            return None
        path = Path(mapped)
        if path.exists():
            return path
        return None

    def _rewrite_exec_command_artifact_aliases(self, session_id: str, command: str) -> str:
        aliases = self._session_artifact_aliases(session_id)
        if not aliases:
            return command
        try:
            tokens = shlex.split(str(command or ""))
        except Exception:
            return command
        if not tokens:
            return command
        changed = False
        rebuilt: list[str] = []
        for token in tokens:
            lookup = token.strip().strip("\"'")
            mapped = aliases.get(lookup) or aliases.get(lookup.replace("\\", "/"))
            if mapped:
                rebuilt.append(shlex.quote(mapped))
                changed = True
            else:
                rebuilt.append(token)
        if not changed:
            return command
        return " ".join(rebuilt)

    def _session_evidence_rows(self, session_id: str, refresh: bool = False) -> list[dict[str, Any]]:
        key = str(session_id or "main")
        if refresh or key not in self._session_evidence_cache:
            window = max(50, int(self.config.session_evidence_limit) * 12)
            self._session_evidence_cache[key] = self.evidence_store.load(key, limit=window)
        return self._session_evidence_cache[key]

    def _sync_mutation_tick_from_evidence(self, session_id: str) -> None:
        key = str(session_id or "main")
        rows = self._session_evidence_rows(key, refresh=True)
        if not rows:
            return
        current = self._session_mutation_tick(key)
        max_tick = current
        for row in rows:
            try:
                tick = int(row.get("mutation_tick", 0))
            except Exception:
                tick = 0
            if tick > max_tick:
                max_tick = tick
        if max_tick > current:
            self._session_mutation_ticks[key] = max_tick

    def _tool_args_signature(self, tool_name: str, args: Any) -> str:
        normalized_name = str(tool_name or "").strip().lower() or "<unknown>"
        try:
            payload = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except TypeError:
            payload = repr(args)
        digest = hashlib.sha256(f"{normalized_name}|{payload}".encode("utf-8", errors="replace")).hexdigest()
        return digest[:24]

    def _summarize_tool_output(self, content: Any) -> str:
        limit = max(60, int(self.config.session_evidence_summary_chars))
        text = str(content or "").strip()
        if not text:
            return "-"
        compact = re.sub(r"\s+", " ", text)
        return _shorten(compact, limit=limit)

    def _extract_error_signature(self, content: Any) -> str:
        text = str(content or "")
        meta = self._parse_exec_meta(text)
        sig = str(meta.get("error_signature", "")).strip()
        if sig and sig != "-":
            return sig
        matched = re.search(r"\berror_signature=([^\s]+)", text)
        if matched:
            value = str(matched.group(1)).strip()
            if value and value != "-":
                return value
        return "-"

    def _extract_artifact_paths_from_text(self, content: Any) -> list[str]:
        text = str(content or "").replace("\\", "/")
        patterns = [
            r"(artifacts/users/[^/\s`\"']+/[^\s`\"']+)",
            r"((?:reports|outputs)/[^\s`\"']+)",
            r"\bto\s+((?:artifacts|reports|outputs)/[^\s`\"']+)",
        ]
        found: list[str] = []
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                raw = str(match.group(1)).strip().rstrip(".,;")
                if not raw:
                    continue
                if raw.startswith("./"):
                    raw = raw[2:]
                if raw not in found:
                    found.append(raw)
                if len(found) >= 8:
                    return found
        return found

    def _append_evidence_record(self, session_id: str, record: dict[str, Any]) -> None:
        key = str(session_id or "main")
        sanitized = dict(record)
        self.evidence_store.append(key, sanitized)
        rows = self._session_evidence_rows(key)
        rows.append(sanitized)
        if len(rows) > 300:
            del rows[: len(rows) - 300]

    def _persist_turn_evidence(
        self,
        session_id: str,
        tool_calls: Sequence[dict[str, Any]],
        tool_results: Sequence[dict[str, Any]],
    ) -> int:
        key = str(session_id or "main")
        if not tool_results:
            return 0

        call_sig_by_id: dict[str, str] = {}
        call_args_by_id: dict[str, Any] = {}
        fallback_sigs_by_tool: dict[str, list[str]] = {}
        fallback_args_by_tool: dict[str, list[Any]] = {}
        for item in tool_calls:
            name = str(item.get("name", "")).strip().lower() or "<unknown>"
            args = item.get("args", {})
            signature = self._tool_args_signature(name, item.get("args", {}))
            call_id = str(item.get("id", "")).strip()
            if call_id:
                call_sig_by_id[call_id] = signature
                call_args_by_id[call_id] = args
            fallback_sigs_by_tool.setdefault(name, []).append(signature)
            fallback_args_by_tool.setdefault(name, []).append(args)

        written = 0
        for item in tool_results:
            name = str(item.get("name", "")).strip().lower() or "<unknown>"
            call_id = str(item.get("tool_call_id", "")).strip()
            signature = call_sig_by_id.get(call_id)
            call_args = call_args_by_id.get(call_id)
            if not signature:
                pool = fallback_sigs_by_tool.get(name, [])
                signature = pool.pop(0) if pool else self._tool_args_signature(name, {})
                arg_pool = fallback_args_by_tool.get(name, [])
                call_args = arg_pool.pop(0) if arg_pool else {}
            content = str(item.get("content", ""))
            exec_meta = self._parse_exec_meta(content)
            try:
                mutation_tick = int(item.get("mutation_tick", self._session_mutation_tick(key)))
            except Exception:
                mutation_tick = int(self._session_mutation_tick(key))
            record = {
                "tool_name": name,
                "args_signature": signature,
                "is_error": bool(item.get("is_error", False)),
                "result_summary": self._summarize_tool_output(content),
                "error_signature": self._extract_error_signature(content),
                "mutation_tick": mutation_tick,
                "artifact_paths": self._extract_artifact_paths_from_text(content),
                "tool_input": call_args if isinstance(call_args, dict) else {},
                "exec": exec_meta if name in {"exec", "exec_readonly"} else {},
                "ts": _now_ts(),
            }
            self._append_evidence_record(key, record)
            written += 1
        return written

    def _evidence_context_message(self, session_id: str) -> Optional[dict[str, str]]:
        rows = self._session_evidence_rows(session_id)
        if not rows:
            return None
        keep = max(1, int(self.config.session_evidence_limit))
        recent = rows[-keep:]
        lines: list[str] = []
        for row in recent:
            tool_name = str(row.get("tool_name", "-"))
            state = "error" if bool(row.get("is_error", False)) else "ok"
            summary = str(row.get("result_summary", "-"))
            signature = str(row.get("args_signature", "-"))
            artifact_paths = row.get("artifact_paths", [])
            artifact_text = ", ".join(str(x) for x in artifact_paths[:3]) if isinstance(artifact_paths, list) else ""
            suffix = f" artifacts={artifact_text}" if artifact_text else ""
            exec_meta = row.get("exec", {})
            exec_text = ""
            if isinstance(exec_meta, dict) and exec_meta:
                exec_text = (
                    f" cwd={exec_meta.get('cwd', '-')}"
                    f" exit_code={exec_meta.get('exit_code', '-')}"
                    f" result={exec_meta.get('result', '-')}"
                )
            lines.append(f"- {tool_name} [{state}] sig={signature}{exec_text} summary={summary}{suffix}")
        if not lines:
            return None
        return {
            "role": "system",
            "content": (
                "Recent execution evidence:\n"
                + "\n".join(lines)
                + "\nUse this evidence first before re-running the same tools."
            ),
        }

    def _prepare_repeat_approval(self, session_id: str, prompt: str) -> None:
        key = str(session_id or "main")
        token = str(self.config.repeat_confirm_token or "").strip()
        if token and token in str(prompt or ""):
            self._session_repeat_approval_remaining[key] = 1
        else:
            self._session_repeat_approval_remaining[key] = 0

    def _consume_repeat_approval(self, session_id: str) -> bool:
        key = str(session_id or "main")
        remaining = int(self._session_repeat_approval_remaining.get(key, 0))
        if remaining <= 0:
            return False
        self._session_repeat_approval_remaining[key] = remaining - 1
        return True

    def _repeat_guard_targets(self) -> set[str]:
        return {"exec", "write", "edit", "multiedit", "todo_write"}

    def _check_cross_turn_repeat_guard(self, tool_name: str, tool_input: dict[str, Any]) -> Optional[str]:
        if not self.config.repeat_guard_enabled:
            return None
        normalized = str(tool_name or "").strip().lower()
        if normalized not in self._repeat_guard_targets():
            return None
        session_id = str(self._active_session_id or "main")
        signature = self._tool_args_signature(normalized, tool_input or {})
        tick = int(self._session_mutation_tick(session_id))
        matched: Optional[dict[str, Any]] = None
        for row in reversed(self._session_evidence_rows(session_id)):
            if str(row.get("tool_name", "")).strip().lower() != normalized:
                continue
            if str(row.get("args_signature", "")).strip() != signature:
                continue
            try:
                row_tick = int(row.get("mutation_tick", -1))
            except Exception:
                row_tick = -1
            if row_tick != tick:
                continue
            matched = row
            break
        if matched is None:
            return None
        if self._consume_repeat_approval(session_id):
            self.audit_logger.log(
                session_id,
                "repeat_guard_approved",
                {
                    "tool_name": normalized,
                    "args_signature": signature,
                    "mutation_tick": tick,
                    "token": self.config.repeat_confirm_token,
                },
            )
            return None
        summary = str(matched.get("result_summary", "-"))
        paths = matched.get("artifact_paths", [])
        path_text = ", ".join(str(x) for x in paths[:3]) if isinstance(paths, list) and paths else "-"
        self.audit_logger.log(
            session_id,
            "repeat_guard_block",
            {
                "tool_name": normalized,
                "args_signature": signature,
                "mutation_tick": tick,
                "summary": summary,
            },
        )
        return (
            "Repeat guard: identical tool call already executed in current state.\n"
            f"tool={normalized}\n"
            f"args_signature={signature}\n"
            f"previous_result={summary}\n"
            f"artifact_paths={path_text}\n"
            f"To re-run once, include '{self.config.repeat_confirm_token}' in your message."
        )

__all__ = [name for name in globals() if not name.startswith("__")]
