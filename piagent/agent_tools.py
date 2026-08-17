from __future__ import annotations

from .deps import *
from .models import *
from .workspace import WorkspaceGuard
from .stores import *
from .utils import *
from .mcp import McpStdioClient

_PYPI_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PYTHON_IMPORT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_EXACT_VERSION_RE = re.compile(r"^[0-9][A-Za-z0-9.!+_-]{0,63}$")


def _canonical_package_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", str(value or "").strip()).lower()


def _safe_pip_detail(value: str, limit: int = 1600) -> str:
    text = str(value or "")
    text = re.sub(r"(https?://)([^\s/@]+(?::[^\s/@]*)?@)", r"\1***@", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)\b(api[_-]?key|token|password)=\S+", r"\1=***", text)
    compact = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    return compact[-max(200, int(limit)) :]

class AgentToolsMixin:
    def _python_package_target(self) -> Path:
        target = self._resolve_workspace_extension_root() / "packages"
        if self.user_id:
            target = target / "users" / self.user_id
        return target

    def _activate_python_package_target(self) -> Path:
        target = self._python_package_target()
        target_text = str(target)
        if target.exists() and target_text not in sys.path:
            sys.path.append(target_text)
        existing = [item for item in os.getenv("PYTHONPATH", "").split(os.pathsep) if item]
        if target.exists() and target_text not in existing:
            os.environ["PYTHONPATH"] = os.pathsep.join([*existing, target_text])
        importlib.invalidate_caches()
        return target

    def _build_default_tools(self) -> list[Any]:
        guard = self.guard
        workspace_dir = self.workspace_dir
        exec_timeout_s = self.config.exec_timeout_s
        allow_write = self.config.allow_write
        allow_shell = self.config.allow_shell
        allow_package_install = self.config.allow_package_install

        @tool("read")
        def read(path: str, full: bool = False, offset: int = 1, limit: int = 0) -> str:
            """Read a file. Supports text preview/full mode and optional line range with offset/limit."""
            try:
                session_id = str(self._active_session_id or "main")
                file_path = guard.resolve(path)
                if not file_path.exists():
                    mapped = self._resolve_artifact_alias_path(session_id, path)
                    if mapped is not None:
                        file_path = mapped
                    else:
                        return f"Error: File '{file_path}' not found."
                if file_path.is_dir():
                    return f"Error: '{file_path}' is a directory, not a file."
                used = self._read_budget_used(session_id)
                budget_limit = max(1, int(self.config.read_output_budget_chars))
                if used >= budget_limit:
                    return self._read_budget_guard_message(used=used)

                raw = file_path.read_bytes()
                rel_path = file_path.relative_to(workspace_dir)
                if self._is_binary_data(raw, file_path):
                    guessed = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
                    digest = hashlib.sha1(raw).hexdigest()[:16]
                    output = (
                        f"path={rel_path.as_posix()}\n"
                        f"type=binary\n"
                        f"mime={guessed}\n"
                        f"size_bytes={len(raw)}\n"
                        f"sha1_16={digest}\n"
                        "hint=Use specialized tools for image/pdf/binary inspection."
                    )
                else:
                    text = raw.decode("utf-8", errors="replace")
                    if int(offset) > 1 or int(limit) > 0:
                        lines = text.splitlines()
                        start = max(1, int(offset))
                        max_lines = max(1, int(limit)) if int(limit) > 0 else max(1, len(lines))
                        selected = lines[start - 1 : start - 1 + max_lines]
                        joined = "\n".join(selected)
                        output = (
                            f"path={rel_path.as_posix()}\n"
                            f"offset={start}\n"
                            f"limit={max_lines}\n"
                            f"line_count_returned={len(selected)}\n\n"
                            f"{joined}"
                        )
                    else:
                        output = self._smart_read_output(
                            rel_path=rel_path,
                            text=text,
                            full=bool(full),
                        )
                output = self._maybe_offload_tool_result("read", output)
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
                session_id = str(self._active_session_id or "main")
                enforced_path = self._force_user_artifact_output_path(path, session_id)
                file_path = guard.resolve(enforced_path)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
                self._bump_session_mutation_tick(self._active_session_id)
                self._register_artifact_alias(session_id, path, file_path)
                return f"wrote {len(content)} chars to {file_path.relative_to(workspace_dir)}"
            except Exception as e:
                return f"Error writing file '{path}': {e}"

        @tool("edit")
        def edit(path: str, old: str, new: str, replace_all: bool = False) -> str:
            """Atomically replace one exact snippet in an existing UTF-8 file."""
            if not allow_write:
                return "Error: edit tool is disabled"
            try:
                session_id = str(self._active_session_id or "main")
                file_path = guard.resolve(path)
                if not file_path.exists():
                    mapped = self._resolve_artifact_alias_path(session_id, path)
                    if mapped is not None:
                        file_path = mapped
                self._assert_edit_path_allowed(session_id, file_path)
                if self._has_scoped_edit_permission(session_id) and replace_all:
                    return "Error: edit mode allows one focused replacement; replace_all is disabled."
                if not old:
                    return "Error: target snippet must not be empty."
                if old == new:
                    return "Error: old and new snippets are identical."
                with file_path.open("r", encoding="utf-8", newline="") as source:
                    text = source.read()
                newline = "\r\n" if "\r\n" in text else "\n"
                normalized_old = old.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)
                normalized_new = new.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)
                count = text.count(normalized_old)
                if count == 0:
                    return "Error: target snippet not found in the file."
                if count > 1 and not replace_all:
                    return f"Error: target snippet appears {count} times; set replace_all=true to replace all."
                updated = (
                    text.replace(normalized_old, normalized_new)
                    if replace_all
                    else text.replace(normalized_old, normalized_new, 1)
                )
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", newline="", delete=False, dir=str(file_path.parent)
                ) as tmp:
                    tmp.write(updated)
                    temp_name = tmp.name
                os.replace(temp_name, file_path)
                with file_path.open("r", encoding="utf-8", newline="") as verified:
                    if verified.read() != updated:
                        return "Error: edit verification failed after atomic replace."
                self._bump_session_mutation_tick(self._active_session_id)
                return f"updated {file_path.relative_to(workspace_dir)}; replacements={count if replace_all else 1}"
            except Exception as e:
                return f"Error editing file '{path}': {e}"

        @tool("multiedit")
        def multiedit(path: str, edits_json: str) -> str:
            """Apply multiple text replacements atomically. edits_json is a JSON array of {old,new,replace_all?}."""
            if not allow_write:
                return "Error: multiedit tool is disabled"
            try:
                session_id = str(self._active_session_id or "main")
                file_path = guard.resolve(path)
                if not file_path.exists():
                    mapped = self._resolve_artifact_alias_path(session_id, path)
                    if mapped is not None:
                        file_path = mapped
                text = file_path.read_text(encoding="utf-8", errors="replace")
                original = text
                ops = json.loads(edits_json)
                if not isinstance(ops, list):
                    return "Error: edits_json must be a JSON array."
                total_replacements = 0
                for i, op in enumerate(ops, start=1):
                    if not isinstance(op, dict):
                        return f"Error: edit #{i} must be an object."
                    old = str(op.get("old", ""))
                    new = str(op.get("new", ""))
                    replace_all = bool(op.get("replace_all", False))
                    if not old:
                        return f"Error: edit #{i} old is empty."
                    if old == new:
                        return f"Error: edit #{i} old and new are identical."
                    count = text.count(old)
                    if count == 0:
                        return f"Error: edit #{i} target snippet not found."
                    if count > 1 and not replace_all:
                        return f"Error: edit #{i} target snippet appears {count} times; set replace_all=true."
                    text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
                    total_replacements += count if replace_all else 1
                if text == original:
                    return "No changes applied."
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(file_path.parent)) as tmp:
                    tmp.write(text)
                    temp_name = tmp.name
                os.replace(temp_name, file_path)
                self._bump_session_mutation_tick(self._active_session_id)
                return f"updated {file_path.relative_to(workspace_dir)}; replacements={total_replacements}"
            except Exception as e:
                return f"Error in multiedit for '{path}': {e}"

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
        def find(glob: str = "**/*", path: str = ".", head_limit: int = 2000, offset: int = 0) -> str:
            """Find files by glob pattern inside the workspace with simple pagination."""
            try:
                base = guard.resolve(path)
                if not base.exists():
                    return f"Error: Directory '{path}' not found."
                if not base.is_dir():
                    return f"Error: '{path}' is not a directory."
                static_prefix = re.split(r"[\*\?\[]", glob, maxsplit=1)[0].strip().strip("/\\")
                if static_prefix:
                    guard.resolve(str(Path(path) / static_prefix))
                rows = []
                for item in sorted(base.glob(glob)):
                    if item.name.startswith(".git"):
                        continue
                    if guard.is_blocked(item):
                        continue
                    if item.is_file():
                        rows.append(str(item.relative_to(workspace_dir)))
                start = max(0, int(offset))
                limit = max(0, int(head_limit))
                selected = rows[start:] if limit == 0 else rows[start : start + limit]
                suffix = ""
                if limit and len(rows) > start + limit:
                    suffix = f"\n[Showing results with pagination = limit: {limit}, offset: {start}]"
                return ("\n".join(selected) + suffix) if selected else "no matches"
            except Exception as e:
                return f"Error finding files for pattern '{glob}': {e}"

        @tool("grep")
        def grep(
            pattern: str,
            path: str = ".",
            glob: str = "",
            output_mode: str = "files_with_matches",
            head_limit: int = 250,
            offset: int = 0,
            case_insensitive: bool = False,
        ) -> str:
            """Search regex matches. output_mode: files_with_matches, content, or count."""
            try:
                base = guard.resolve(path)
                if not base.exists():
                    return f"Error: Path '{path}' not found."
                try:
                    flags = re.IGNORECASE if bool(case_insensitive) else 0
                    regex = re.compile(pattern, flags)
                except re.error as reg_e:
                    return f"Error: Invalid regex pattern '{pattern}' - {reg_e}"

                mode = str(output_mode or "files_with_matches").strip().lower()
                if mode not in {"files_with_matches", "content", "count"}:
                    return "Error: output_mode must be files_with_matches, content, or count."
                glob_filter = str(glob or "").strip()
                glob_patterns = [p for p in re.split(r"[\s,]+", glob_filter) if p]
                limit = max(0, int(head_limit))
                start = max(0, int(offset))

                hits: list[str] = []
                counts: dict[str, int] = {}
                files_with_matches: set[str] = set()
                files: Iterable[Path]
                if base.is_file():
                    files = [base]
                else:
                    files = sorted(p for p in base.rglob("*") if p.is_file())

                for file_path in files:
                    if guard.is_blocked(file_path):
                        continue
                    rel = file_path.relative_to(workspace_dir)
                    rel_posix = rel.as_posix()
                    if glob_patterns and not any(fnmatch.fnmatch(rel_posix, pat) or fnmatch.fnmatch(file_path.name, pat) for pat in glob_patterns):
                        continue
                    try:
                        text = file_path.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue
                    for i, line in enumerate(text.splitlines(), start=1):
                        if regex.search(line):
                            files_with_matches.add(rel_posix)
                            counts[rel_posix] = counts.get(rel_posix, 0) + 1
                            if mode == "content":
                                hits.append(f"{rel_posix}:{i}: {line}")

                if mode == "files_with_matches":
                    rows = sorted(files_with_matches)
                    selected = rows[start:] if limit == 0 else rows[start : start + limit]
                    if not selected:
                        return "No files found"
                    body = f"Found {len(rows)} file(s)\n" + "\n".join(selected)
                elif mode == "count":
                    rows = [f"{name}:{count}" for name, count in sorted(counts.items())]
                    selected = rows[start:] if limit == 0 else rows[start : start + limit]
                    if not selected:
                        return "No matches found"
                    body = "\n".join(selected) + f"\n\nFound {sum(counts.values())} occurrence(s) across {len(counts)} file(s)."
                else:
                    selected = hits[start:] if limit == 0 else hits[start : start + limit]
                    body = "\n".join(selected) if selected else "No matches found"
                if limit:
                    total = len(hits) if mode == "content" else len(counts)
                    if total > start + limit:
                        body += f"\n\n[Showing results with pagination = limit: {limit}, offset: {start}]"
                return self._maybe_offload_tool_result("grep", body)
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
                session_id = str(self._active_session_id or "main")
                command = self._rewrite_exec_command_artifact_aliases(session_id, command)
                cwd_rel = str(run_dir.relative_to(workspace_dir))
                if self.user_id:
                    for match in re.finditer(r"artifacts[\\/]+users[\\/]+([^\\/\\s\"']+)", str(command or ""), flags=re.IGNORECASE):
                        owner = _sanitize_user_id(match.group(1))
                        if owner and owner != self.user_id:
                            return (
                                f"cwd={cwd_rel}\n"
                                "exit_code=blocked\n"
                                "stdout:\n\n"
                                "stderr:\nBlocked cross-user artifact access in exec command.\n"
                                "result=error\n"
                                "error_type=USER_SCOPE_VIOLATION\n"
                                "error_signature=scope_block\n"
                                "retryable=false"
                            )
                if self._is_package_install_shell_command(command):
                    return (
                        f"cwd={cwd_rel}\n"
                        "exit_code=blocked\n"
                        "stdout:\n\n"
                        "stderr:\nUse python_package_install for allowlisted workspace dependencies.\n"
                        "result=error\n"
                        "error_type=PACKAGE_INSTALL_POLICY\n"
                        "error_signature=package_install_policy\n"
                        "retryable=false"
                    )
                if self._is_dangerous_shell_command(command) and os.getenv("PI_EXEC_ALLOW_DANGEROUS", "false").lower() not in {"1", "true", "yes"}:
                    self._run_hooks(
                        "permission_request",
                        {"tool_name": "exec", "tool_input": {"command": command, "cwd": cwd_rel}, "reason": "dangerous_command"},
                        allow_block=False,
                    )
                    return (
                        f"cwd={cwd_rel}\n"
                        "exit_code=blocked\n"
                        "stdout:\n\n"
                        "stderr:\nBlocked dangerous shell command by policy.\n"
                        "result=error\n"
                        "error_type=DANGEROUS_COMMAND\n"
                        "error_signature=policy_block\n"
                        "retryable=false"
                    )

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
                    return self._maybe_offload_tool_result("exec", _shorten(blocked_output, int(self.config.max_tool_result_chars)))

                final_command, correction_note = self._maybe_correct_exec_command(command, run_dir)
                effective_run_dir = run_dir
                if str(cwd).strip() in {".", "./"}:
                    inferred = self._infer_python_script_run_dir(final_command)
                    if inferred is not None:
                        effective_run_dir = inferred
                        cwd_rel = str(effective_run_dir.relative_to(workspace_dir))
                        note = f"exec cwd inferred from script path: {cwd_rel}"
                        correction_note = f"{correction_note}; {note}" if correction_note else note

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
                        cwd=str(effective_run_dir),
                        shell=True,
                        text=True,
                        capture_output=True,
                        timeout=max(1, int(timeout_s)),
                        encoding="utf-8",
                        errors="replace",
                    )
                    error_type = self._exec_error_type(completed.returncode, completed.stderr)
                    result = "ok" if completed.returncode == 0 else "error"
                    retried = False
                    if result == "error" and self._exec_retryable(error_type):
                        retry_patterns = ("temporar", "resource busy", "try again", "timeout")
                        low_stderr = (completed.stderr or "").lower()
                        if any(p in low_stderr for p in retry_patterns):
                            retried = True
                            completed = subprocess.run(
                                final_command,
                                cwd=str(effective_run_dir),
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
                        f"retried={str(retried).lower()}\n"
                        f"retryable={retryable}"
                    )
                    
                    if writer:
                        writer(f"exec finished: exit_code={completed.returncode}")
                    return self._maybe_offload_tool_result("exec", output)
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
                    return self._maybe_offload_tool_result("exec", timeout_output)
            except Exception as e:
                return f"Error executing command '{command}': {e}"

        @tool("exec_readonly")
        def exec_readonly(command: str, cwd: str = ".", timeout_s: int = exec_timeout_s) -> str:
            """Run a shell command only when it is classified as read-only."""
            ok, reason = self._readonly_command_verdict(command)
            if not ok:
                return (
                    f"exit_code=blocked\n"
                    f"stderr:\nBlocked non-read-only command: {reason}\n"
                    "result=error\n"
                    "error_type=READONLY_POLICY\n"
                    "error_signature=readonly_block\n"
                    "retryable=false"
                )
            return str(
                exec_tool.invoke(
                    {
                        "command": command,
                        "cwd": cwd,
                        "timeout_s": timeout_s,
                    }
                )
            )

        @tool("delegate_task")
        def delegate_task(description: str, prompt: str, agent_type: str = "explore") -> str:
            """Delegate a bounded task to a read-only subagent: explore, plan, or verify."""
            return self._delegate_task_impl(description=description, prompt=prompt, agent_type=agent_type)

        @tool("ask_user")
        def ask_user(question: str) -> str:
            """Request a short clarification from the user and pause the current run."""
            q = str(question or "").strip()
            if not q:
                return "Error: question is empty."
            self._ask_user_question = q
            return f"USER_INPUT_REQUIRED: {q}"

        @tool("enter_plan_mode")
        def enter_plan_mode() -> str:
            """Set current session plan mode to on."""
            sid = str(self._active_session_id or "main")
            mode = self._set_session_plan_mode(sid, "on")
            self.audit_logger.log(sid, "plan_switched", {"mode": mode, "via": "tool"})
            return f"Plan mode switched to {mode}."

        @tool("exit_plan_mode")
        def exit_plan_mode() -> str:
            """Set current session plan mode to off."""
            sid = str(self._active_session_id or "main")
            mode = self._set_session_plan_mode(sid, "off")
            self.audit_logger.log(sid, "plan_switched", {"mode": mode, "via": "tool"})
            return f"Plan mode switched to {mode}."

        @tool("python_package_install")
        def python_package_install(package: str, import_name: str = "", version: str = "") -> str:
            """Install one allowlisted PyPI package into the workspace and verify its Python import.

            Only a bare package name and optional exact version are accepted. For packages whose
            import differs from the PyPI name, pass import_name (for example python-docx -> docx).
            """
            if not allow_shell:
                return "Error: python package installation requires allow_shell=true"
            if not allow_package_install:
                return "Error: python package installation is disabled; set PI_ALLOW_PACKAGE_INSTALL=true"

            package_name = str(package or "").strip()
            requested_version = str(version or "").strip()
            module_name = str(import_name or "").strip() or package_name.replace("-", "_")
            if not _PYPI_NAME_RE.fullmatch(package_name):
                return "Error: package must be a bare PyPI name; URLs, paths, extras, and pip options are blocked"
            if requested_version and not _EXACT_VERSION_RE.fullmatch(requested_version):
                return "Error: version must be one exact version without comparison operators"
            if not _PYTHON_IMPORT_RE.fullmatch(module_name):
                return "Error: import_name must be a dotted Python module name"

            requested_canonical = _canonical_package_name(package_name)
            allowed_version = ""
            matched = False
            for entry in self.config.package_install_allowlist:
                raw_entry = str(entry or "").strip()
                allowed_name, separator, pinned_version = raw_entry.partition("==")
                if _canonical_package_name(allowed_name) != requested_canonical:
                    continue
                if not _PYPI_NAME_RE.fullmatch(allowed_name.strip()):
                    continue
                if separator and not _EXACT_VERSION_RE.fullmatch(pinned_version.strip()):
                    continue
                matched = True
                allowed_version = pinned_version.strip() if separator else ""
                break
            if not matched:
                return f"Error: package '{package_name}' is not in package_install_allowlist"
            if allowed_version and requested_version and requested_version != allowed_version:
                return f"Error: package '{package_name}' is pinned to {allowed_version} by policy"

            effective_version = allowed_version or requested_version
            package_spec = package_name + (f"=={effective_version}" if effective_version else "")
            target = self._activate_python_package_target()
            if importlib.util.find_spec(module_name) is not None:
                return f"status=already_available\npackage={package_spec}\nimport={module_name}"

            target.mkdir(parents=True, exist_ok=True)
            install_command = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--target",
                str(target),
                package_spec,
            ]
            try:
                installed = subprocess.run(
                    install_command,
                    cwd=str(self.workspace_dir),
                    capture_output=True,
                    text=True,
                    timeout=max(10, int(self.config.package_install_timeout_s)),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return f"Error: package installation timed out for {package_spec}"
            except Exception as exc:
                return f"Error: package installation failed to start: {exc}"
            if installed.returncode != 0:
                detail = _safe_pip_detail(installed.stderr or installed.stdout)
                return f"Error: pip install failed for {package_spec}\n{detail}"

            self._activate_python_package_target()
            verify_env = dict(os.environ)
            target_text = str(target)
            current_pythonpath = verify_env.get("PYTHONPATH", "")
            verify_env["PYTHONPATH"] = os.pathsep.join(
                [item for item in (current_pythonpath, target_text) if item]
            )
            try:
                verified = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import importlib,sys; importlib.import_module(sys.argv[1])",
                        module_name,
                    ],
                    cwd=str(self.workspace_dir),
                    env=verify_env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except Exception as exc:
                return f"Error: installed {package_spec}, but import verification could not start: {exc}"
            if verified.returncode != 0:
                detail = _safe_pip_detail(verified.stderr or verified.stdout)
                return f"Error: installed {package_spec}, but import '{module_name}' failed\n{detail}"

            self._bump_session_mutation_tick(self._active_session_id)
            relative_target = target.relative_to(self.workspace_dir).as_posix()
            return (
                "status=installed\n"
                f"package={package_spec}\n"
                f"import={module_name}\n"
                f"target={relative_target}\n"
                "verification=import_ok"
            )

        @tool("web_fetch")
        def web_fetch(url: str, max_chars: int = 12000) -> str:
            """Fetch a web page and return a plain-text preview."""
            target = str(url or "").strip()
            if not re.match(r"^https?://", target, flags=re.IGNORECASE):
                return "Error: web_fetch supports http/https URLs only."
            blocked, reason = self._is_private_or_metadata_url(target)
            if blocked:
                return f"Error: blocked URL by SSRF policy: {reason}"
            try:
                req = urllib.request.Request(
                    target,
                    headers={"User-Agent": "PiAgent/1.0 (+tool:web_fetch)"},
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    content_type = str(resp.headers.get("Content-Type", ""))
                    raw = resp.read()
                text = raw.decode("utf-8", errors="replace")
                if "text/html" in content_type.lower():
                    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
                    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
                    text = re.sub(r"(?is)<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                clipped = text[: max(200, int(max_chars))]
                output = (
                    f"url={target}\n"
                    f"content_type={content_type or '-'}\n"
                    f"char_count={len(text)}\n\n"
                    f"{clipped}"
                )
                return self._maybe_offload_tool_result("web_fetch", output)
            except Exception as e:
                return f"Error fetching URL '{target}': {e}"

        @tool("web_search")
        def web_search(query: str, limit: int = 5) -> str:
            """Search the web using a lightweight API and return top snippets."""
            q = str(query or "").strip()
            if not q:
                return "Error: query is empty."
            max_rows = max(1, min(10, int(limit)))
            url = (
                "https://api.duckduckgo.com/?"
                + urllib.parse.urlencode(
                    {"q": q, "format": "json", "no_redirect": 1, "no_html": 1, "skip_disambig": 1}
                )
            )
            blocked, reason = self._is_private_or_metadata_url(url)
            if blocked:
                return f"Error: blocked search URL by SSRF policy: {reason}"
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "PiAgent/1.0 (+tool:web_search)"},
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                rows: list[str] = []
                abstract_text = str(payload.get("AbstractText", "")).strip()
                abstract_url = str(payload.get("AbstractURL", "")).strip()
                if abstract_text:
                    rows.append(f"- {abstract_text} ({abstract_url or '-'})")
                related = payload.get("RelatedTopics", [])
                if isinstance(related, list):
                    for item in related:
                        if len(rows) >= max_rows:
                            break
                        if isinstance(item, dict) and isinstance(item.get("Topics"), list):
                            for sub in item.get("Topics", []):
                                if len(rows) >= max_rows:
                                    break
                                if isinstance(sub, dict):
                                    text = str(sub.get("Text", "")).strip()
                                    first_url = str(sub.get("FirstURL", "")).strip()
                                    if text:
                                        rows.append(f"- {text} ({first_url or '-'})")
                        elif isinstance(item, dict):
                            text = str(item.get("Text", "")).strip()
                            first_url = str(item.get("FirstURL", "")).strip()
                            if text:
                                rows.append(f"- {text} ({first_url or '-'})")
                if not rows:
                    return "No web search results."
                return "\n".join(rows[:max_rows])
            except Exception as e:
                return f"Error searching web: {e}"

        @tool("tool_search")
        def tool_search(query: str, limit: int = 10) -> str:
            """Search available tools by name/description/source."""
            q = str(query or "").strip().lower()
            if not q:
                return "Error: query is empty."
            max_rows = max(1, min(50, int(limit)))
            matched: list[tuple[int, str]] = []
            for tool_obj in self.all_tools:
                name = str(getattr(tool_obj, "name", "")).strip()
                desc = str(getattr(tool_obj, "description", "") or "").strip()
                source = str(self._tool_sources.get(name, "unknown"))
                hay = f"{name}\n{desc}\n{source}".lower()
                score = 0
                if q == name.lower():
                    score += 10
                if q in name.lower():
                    score += 5
                if q in desc.lower():
                    score += 2
                if q in source.lower():
                    score += 1
                if score > 0:
                    matched.append((score, f"- {name} [source={source}] :: {desc[:140]}"))
            matched.sort(key=lambda x: (-x[0], x[1]))
            if not matched:
                return "No tools matched."
            return "\n".join([row for _score, row in matched[:max_rows]])

        @tool("mcp_list_resources")
        def mcp_list_resources(server: str = "") -> str:
            """List MCP resources from one server or all connected servers."""
            target = str(server or "").strip()
            selected = (
                {target: self._mcp_clients.get(target)}
                if target
                else dict(self._mcp_clients)
            )
            lines: list[str] = []
            for server_name, client in selected.items():
                if client is None:
                    lines.append(f"- server={server_name} error=not_connected")
                    continue
                try:
                    rows = client.list_resources()
                    if not rows:
                        lines.append(f"- server={server_name} resources=0")
                        continue
                    for row in rows:
                        lines.append(
                            f"- server={server_name} uri={row.get('uri')} name={row.get('name', '-')}"
                        )
                except Exception as e:
                    lines.append(f"- server={server_name} error={e}")
            return "\n".join(lines) if lines else "No MCP servers connected."

        @tool("mcp_read_resource")
        def mcp_read_resource(server: str, uri: str) -> str:
            """Read a specific MCP resource by server and URI."""
            server_name = str(server or "").strip()
            resource_uri = str(uri or "").strip()
            if not server_name or not resource_uri:
                return "Error: server and uri are required."
            client = self._mcp_clients.get(server_name)
            if client is None:
                return f"Error: MCP server '{server_name}' is unavailable."
            try:
                result = client.read_resource(resource_uri)
                return _render_mcp_result(result)
            except Exception as e:
                return f"Error reading MCP resource: {e}"

        @tool("mcp_list_resource_templates")
        def mcp_list_resource_templates(server: str = "") -> str:
            """List MCP resource templates from one server or all connected servers."""
            target = str(server or "").strip()
            selected = (
                {target: self._mcp_clients.get(target)}
                if target
                else dict(self._mcp_clients)
            )
            lines: list[str] = []
            for server_name, client in selected.items():
                if client is None:
                    lines.append(f"- server={server_name} error=not_connected")
                    continue
                try:
                    rows = client.list_resource_templates()
                    if not rows:
                        lines.append(f"- server={server_name} templates=0")
                        continue
                    for row in rows:
                        lines.append(
                            f"- server={server_name} uriTemplate={row.get('uriTemplate')} name={row.get('name', '-')}"
                        )
                except Exception as e:
                    lines.append(f"- server={server_name} error={e}")
            return "\n".join(lines) if lines else "No MCP servers connected."

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
                mirror = {
                    "id": entry["id"],
                    "ts": self._memory_ts(entry),
                    "kind": "preference"
                    if {tag.lower() for tag in tag_list} & {"user_preference", "preference", "language"}
                    else "fact",
                    "content": entry["content"],
                    "tags": tag_list,
                    "source": "memory_store",
                }
                self.memory_store.append(str(self._active_session_id or "main"), mirror)
                if (self.config.memory_search_backend or "").lower() == "sqlite-vec":
                    try:
                        embedding = self.embedding_client.embed_query(str(mirror.get("content", "")))
                        self.memory_index.upsert_memory(
                            session_id=str(self._active_session_id or "main"),
                            memory=mirror,
                            embedding=embedding,
                            provider=self.embedding_client.provider,
                            model=self.embedding_client.model,
                        )
                    except Exception as index_error:
                        self.audit_logger.log(self._active_session_id or "main", "memory_store_index_error", {"error": str(index_error)})
                return f"Stored memory {entry['id']} in {entry['file']}"
            except Exception as e:
                return f"Error storing memory entry: {e}"

        def _target_session_id(raw: str) -> str:
            target = str(raw or self._active_session_id or "main").strip()
            if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", target):
                raise ValueError("session_id must use 1-128 letters, numbers, dot, underscore, or hyphen")
            return target

        @tool("session_fragment_search")
        def session_fragment_search(
            query: str,
            session_id: str = "",
            limit: int = 5,
            role: str = "",
        ) -> str:
            """Search append-only session conversation fragments; returns IDs and snippets for later retrieval."""
            clean_query = re.sub(r"\s+", " ", str(query or "")).strip()[:500]
            role_filter = str(role or "").strip().lower()
            if not clean_query:
                return json.dumps({"status": "error", "error": "query is required"})
            if role_filter not in {"", "user", "assistant"}:
                return json.dumps({"status": "error", "error": "role must be user, assistant, or empty"})
            try:
                target_session = _target_session_id(session_id)
                rows = self.session_fragment_store.search(
                    session_id=target_session,
                    query=clean_query,
                    limit=max(1, min(20, int(limit))),
                    role=role_filter,
                )
                return json.dumps(
                    {
                        "status": "ok" if rows else "no_results",
                        "session_id": target_session,
                        "query": clean_query,
                        "role": role_filter or "any",
                        "result_count": len(rows),
                        "results": rows,
                    },
                    ensure_ascii=False,
                )
            except (OSError, ValueError) as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        @tool("session_fragment_get")
        def session_fragment_get(ids: str, session_id: str = "") -> str:
            """Retrieve full session fragments by IDs returned from session_fragment_search."""
            parsed_ids = [item for item in re.split(r"[\s,]+", str(ids or "")) if item]
            if not parsed_ids:
                return json.dumps({"status": "error", "error": "at least one fragment ID is required"})
            if len(parsed_ids) > 20:
                return json.dumps({"status": "error", "error": "at most 20 fragment IDs may be requested"})
            try:
                target_session = _target_session_id(session_id)
                rows = self.session_fragment_store.get_by_ids(target_session, parsed_ids)
                return json.dumps(
                    {
                        "status": "ok" if rows else "no_results",
                        "session_id": target_session,
                        "requested_count": len(parsed_ids),
                        "result_count": len(rows),
                        "missing_ids": [item for item in parsed_ids if item not in {str(row.get('id', '')) for row in rows}],
                        "results": rows,
                    },
                    ensure_ascii=False,
                )
            except (OSError, ValueError) as exc:
                return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        @tool("work_note_read")
        def work_note_read(session_id: str = "") -> str:
            """Read the structured work note for the current or specified session."""
            if not self.config.enable_work_notes:
                return "Error: work notes are disabled."
            sid = str(session_id or self._active_session_id or "main")
            try:
                path = self._ensure_work_note(sid)
                output = f"path={self._relative_workspace_path(path)}\n\n{path.read_text(encoding='utf-8', errors='replace')}"
                return self._maybe_offload_tool_result("work_note_read", output)
            except Exception as e:
                return f"Error reading work note: {e}"

        @tool("work_note_update")
        def work_note_update(section: str, content: str, mode: str = "append") -> str:
            """Update a structured work note section. mode is append or replace."""
            sid = str(self._active_session_id or "main")
            if self._effective_plan_mode(session_id=sid, requested_mode=None) == "on":
                return "Error: work_note_update is disabled in plan mode. Use plan_note_write for plan content."
            try:
                return self._update_work_note_section(sid, section, content, mode=mode)
            except Exception as e:
                return f"Error updating work note: {e}"

        @tool("work_note_search")
        def work_note_search(pattern: str, section: str = "") -> str:
            """Search the structured work note by regex, optionally within one section."""
            sid = str(self._active_session_id or "main")
            try:
                return self._search_work_note(sid, pattern, section=section)
            except Exception as e:
                return f"Error searching work note: {e}"

        @tool("plan_note_write")
        def plan_note_write(content: str) -> str:
            """Write the current plan into the session work note. Allowed in plan mode."""
            sid = str(self._active_session_id or "main")
            if not str(content or "").strip():
                return "Error: content is required."
            try:
                out = self._update_work_note_section(sid, "Task Spec", content, mode="replace")
                self._update_work_note_section(
                    sid,
                    "Current State",
                    "Plan drafted in plan mode. Await user approval before implementation.",
                    mode="replace",
                )
                return out
            except Exception as e:
                return f"Error writing plan note: {e}"

        @tool("todo_read")
        def todo_read() -> str:
            """Read the current session todo list. Returns all items with id, status, priority, and content."""
            items = self._session_todos(str(self._active_session_id or "main"))
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
        def todo_write(todos: str | list[dict[str, Any]]) -> str:
            """Replace the session todo list. todos may be a JSON string or an array of objects with fields:
            content (str, required), status ('pending'|'in_progress'|'completed'|'cancelled'),
            priority ('high'|'medium'|'low'). IDs are assigned automatically."""
            import json as _json
            try:
                raw = _json.loads(todos) if isinstance(todos, str) else todos
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
                sid = str(self._active_session_id or "main")
                self._session_todo_items[sid] = new_items
                return f"Todo list updated: {len(new_items)} item(s)."
            except _json.JSONDecodeError as e:
                return f"Error: Invalid JSON - {e}"
            except Exception as e:
                return f"Error updating todos: {e}"

        return [
            read,
            write,
            edit,
            multiedit,
            ls,
            find,
            grep,
            exec_tool,
            exec_readonly,
            delegate_task,
            ask_user,
            enter_plan_mode,
            exit_plan_mode,
            python_package_install,
            web_fetch,
            web_search,
            tool_search,
            mcp_list_resources,
            mcp_read_resource,
            mcp_list_resource_templates,
            memory_search,
            memory_get,
            memory_store,
            session_fragment_search,
            session_fragment_get,
            work_note_read,
            work_note_update,
            work_note_search,
            plan_note_write,
            todo_read,
            todo_write,
        ]

__all__ = [name for name in globals() if not name.startswith("__")]
