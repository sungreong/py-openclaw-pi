from __future__ import annotations

from .deps import *
from .models import *
from .workspace import WorkspaceGuard
from .stores import *
from .utils import *
from .mcp import McpStdioClient

class AgentHooksMixin:
    def _queue_audit(self, event_type: str, payload: dict[str, Any]) -> None:
        self._pending_audit_events.append((event_type, payload))

    def _flush_pending_audit(self, session_id: str) -> None:
        if not self._pending_audit_events:
            return
        for event_type, payload in self._pending_audit_events:
            self.audit_logger.log(session_id, event_type, payload)
        self._pending_audit_events = []

    def _resolve_hooks_config_path(self) -> Path:
        raw = str(self.config.hooks_config_path or "pi_hooks.json").strip()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.workspace_dir / candidate
        return candidate.resolve()

    def _load_hooks(self) -> dict[str, list[HookSpec]]:
        empty: dict[str, list[HookSpec]] = {
            "session_start": [],
            "pre_tool_use": [],
            "permission_request": [],
            "post_tool_use": [],
            "pre_compact": [],
            "post_compact": [],
            "verification": [],
            "run_end": [],
        }
        path = self._resolve_hooks_config_path()
        if not path.exists():
            return empty
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return empty
            for event in list(empty.keys()):
                rows = data.get(event, [])
                if not isinstance(rows, list):
                    continue
                parsed: list[HookSpec] = []
                for i, row in enumerate(rows):
                    if not isinstance(row, dict):
                        continue
                    htype = str(row.get("type", "")).strip().lower()
                    if htype not in {"command", "prompt"}:
                        continue
                    content = str(row.get("command" if htype == "command" else "prompt", "")).strip()
                    if not content:
                        continue
                    timeout_s = max(1, int(row.get("timeout_s", 30)))
                    parsed.append(
                        HookSpec(
                            hook_type=htype,  # type: ignore[arg-type]
                            content=content,
                            timeout_s=timeout_s,
                            name=str(row.get("name", f"{event}_{i+1}")).strip() or f"{event}_{i+1}",
                        )
                    )
                empty[event] = parsed
            return empty
        except Exception as e:
            self._queue_audit("hook_config_invalid", {"path": str(path), "error": str(e)})
            return empty

    def _run_command_hook(self, spec: HookSpec, payload: dict[str, Any]) -> tuple[bool, str]:
        try:
            completed = subprocess.run(
                spec.content,
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                shell=True,
                timeout=max(1, int(spec.timeout_s)),
                encoding="utf-8",
                errors="replace",
            )
            stdout = str(completed.stdout or "").strip()
            if completed.returncode == 2:
                return True, stdout or f"blocked by hook {spec.name}"
            low = stdout.lower()
            if "decision:block" in low:
                return True, stdout or f"blocked by hook {spec.name}"
            if stdout.startswith("{") and stdout.endswith("}"):
                try:
                    parsed = json.loads(stdout)
                    decision = str(parsed.get("decision", "")).strip().lower()
                    if decision == "block":
                        return True, str(parsed.get("reason", "")).strip() or f"blocked by hook {spec.name}"
                except Exception:
                    pass
            return False, ""
        except Exception as e:
            return False, f"hook error({spec.name}): {e}"

    def _run_prompt_hook(self, spec: HookSpec, payload: dict[str, Any]) -> tuple[bool, str]:
        system_text = (
            "You are a strict policy hook. Return only JSON object: "
            '{"decision":"allow"|"block","reason":"..."}'
        )
        user_text = (
            f"Policy prompt:\n{spec.content}\n\n"
            f"Event payload JSON:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
            "Decide now."
        )
        try:
            response = self.compaction_model.invoke(
                [{"role": "system", "content": system_text}, {"role": "user", "content": user_text}]
            )
            raw = extract_text(response).strip()
            parsed = json.loads(raw) if raw else {}
            decision = str(parsed.get("decision", "allow")).strip().lower()
            reason = str(parsed.get("reason", "")).strip()
            return (decision == "block"), reason
        except Exception as e:
            return False, f"hook error({spec.name}): {e}"

    def _run_hooks(self, event: str, payload: dict[str, Any], allow_block: bool) -> tuple[bool, str]:
        hooks = self._hooks.get(event, [])
        if not hooks:
            return False, ""
        for spec in hooks:
            started = _now_ts()
            if spec.hook_type == "command":
                blocked, reason = self._run_command_hook(spec, payload)
            else:
                blocked, reason = self._run_prompt_hook(spec, payload)
            elapsed_ms = int((_now_ts() - started) * 1000)
            self.audit_logger.log(
                str(self._active_session_id or "main"),
                "hook_run",
                {
                    "event": event,
                    "name": spec.name,
                    "type": spec.hook_type,
                    "blocked": bool(blocked),
                    "elapsed_ms": elapsed_ms,
                    "reason": reason,
                },
            )
            if blocked and allow_block:
                return True, reason or f"blocked by hook {spec.name}"
        return False, ""

    def _wrap_single_tool_with_hooks(self, tool_obj: Any) -> Any:
        if getattr(tool_obj, "_pi_hook_wrapped", False):
            return tool_obj
        name = str(getattr(tool_obj, "name", "")).strip()
        func = getattr(tool_obj, "func", None)
        if not name or not callable(func):
            return tool_obj
        description = str(getattr(tool_obj, "description", "") or "").strip()
        args_schema = getattr(tool_obj, "args_schema", None)

        def _wrapped(**kwargs: Any) -> str:
            repeat_blocked = self._check_cross_turn_repeat_guard(name, kwargs)
            if repeat_blocked:
                return repeat_blocked

            pre_payload = {"tool_name": name, "tool_input": kwargs}
            blocked, reason = self._run_hooks("pre_tool_use", pre_payload, allow_block=True)
            if blocked:
                return f"Blocked by pre_tool_use hook: {reason or '-'}"

            is_error = False
            result_text = ""
            try:
                raw = func(**kwargs)
                result_text = str(raw)
                return result_text
            except Exception as e:
                is_error = True
                result_text = f"Error in tool '{name}': {e}"
                return result_text
            finally:
                post_payload = {
                    "tool_name": name,
                    "tool_input": kwargs,
                    "tool_output": result_text,
                    "is_error": is_error,
                }
                self._run_hooks("post_tool_use", post_payload, allow_block=False)

        try:
            wrapped = StructuredTool.from_function(
                func=_wrapped,
                name=name,
                description=description or f"Wrapped tool: {name}",
                args_schema=args_schema,
            )
            setattr(wrapped, "_pi_hook_wrapped", True)
            return wrapped
        except Exception:
            return tool_obj

    def _wrap_tools_with_hooks(self, tools: Sequence[Any]) -> list[Any]:
        if (
            not self.config.repeat_guard_enabled
            and not any(self._hooks.get(key) for key in ("pre_tool_use", "post_tool_use"))
        ):
            return list(tools)
        return [self._wrap_single_tool_with_hooks(t) for t in tools]

__all__ = [name for name in globals() if not name.startswith("__")]
