from __future__ import annotations

from .deps import *
from .models import *
from .workspace import WorkspaceGuard
from .stores import *
from .utils import *
from .mcp import McpStdioClient
from .permissions import normalize_permission_profile, tools_for_permission_profile


_PROJECT_INSTRUCTIONS_RELATIVE_PATH = Path(".piagent") / "INSTRUCTIONS.md"
_MAX_PROJECT_INSTRUCTIONS_BYTES = 64 * 1024


class AgentRunMixin:
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
            forced_deny_tools=(
                "write",
                "edit",
                "multiedit",
                "exec",
                "exec_readonly",
                "python_package_install",
                "memory_store",
                "work_note_update",
            ),
            skip_skill_precheck_fail=True,
            disable_legacy_memory_write=True,
            planner_directive=(
                "Plan mode is ON (read-only planning mode).\n"
                "- Explore before asking; ask only for product choices that cannot be discovered.\n"
                "- Do not execute implementation work or modify project files.\n"
                "- Shell execution is unavailable in top-level plan mode; use read/ls/find/grep.\n"
                "- Save finalized plan content with plan_note_write when useful; do not use work_note_update in plan mode.\n"
                "- Final answer must be wrapped in <proposed_plan>...</proposed_plan>.\n"
                "- Include goal, critical files, implementation steps, tests, and assumptions."
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

    def _apply_permission_profile_to_tools(
        self,
        tools: Sequence[Any],
        permission_profile: Optional[str],
    ) -> list[Any]:
        allowed = tools_for_permission_profile(permission_profile)
        if allowed is None:
            return list(tools)
        return [
            tool_obj
            for tool_obj in tools
            if str(getattr(tool_obj, "name", "")).strip().lower() in allowed
        ]

    def _plan_directive_message(self, policy: PlanRuntimePolicy) -> Optional[dict[str, str]]:
        if policy.mode != "on" or not policy.planner_directive.strip():
            return None
        return {"role": "system", "content": policy.planner_directive.strip()}

    def _load_project_instructions(self) -> str:
        """Load optional workspace rules that are appended to the system prompt."""
        path = (self.workspace_dir / _PROJECT_INSTRUCTIONS_RELATIVE_PATH).resolve()
        try:
            path.relative_to(self.workspace_dir)
        except ValueError as exc:
            raise ValueError("project instructions path escapes workspace") from exc
        if not path.is_file():
            return ""
        if path.stat().st_size > _MAX_PROJECT_INSTRUCTIONS_BYTES:
            raise ValueError(".piagent/INSTRUCTIONS.md exceeds the 64 KB safety limit")
        try:
            return path.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError as exc:
            raise ValueError(".piagent/INSTRUCTIONS.md must be UTF-8 encoded") from exc

    def _build_system_prompt(
        self,
        tools: Sequence[Any],
        session_id: str,
        skill: Optional[SkillSpec] = None,
    ) -> str:
        sections = [
            "# Identity\n"
            "You are Pi, a coding agent runtime inspired by OpenClaw. "
            "Your job is to understand the repository, make precise changes when allowed, "
            "verify outcomes, and report honestly.",
            "# Safety\n"
            "- Stay inside the configured workspace unless the user explicitly expands scope.\n"
            "- Never access blocked paths with generic file, search, or exec tools.\n"
            "- Treat destructive, credential-exposing, dependency-changing, or shared-state actions as high risk.\n"
            "- If a tool or hook blocks an action, adjust strategy instead of repeating it blindly.",
            "# Task Loop\n"
            "- Use tools instead of guessing.\n"
            "- Read relevant files before proposing or editing code.\n"
            "- Prefer focused edits over rewrites and avoid speculative abstractions.\n"
            "- Diagnose failures from their output before changing tactics.\n"
            "- Before claiming completion, verify with tests, checks, or direct inspection where feasible.\n"
            "- If verification cannot be run, state exactly what was not verified and why.",
            "# Tool Usage\n"
            "- Prefer dedicated tools over shell commands for file reading, searching, and editing.\n"
            "- When a tool is needed, emit the structured tool call without narration before it.\n"
            "- Never print tool-call markup such as functions.read in ordinary response text.\n"
            "- Use grep/find to narrow large files before read(full=true).\n"
            "- Use exec_readonly for read-only checks when available; reserve exec for commands that truly require shell execution.\n"
            "- For missing Python dependencies, use python_package_install when available; never run pip through exec.\n"
            "- Large tool results may be replaced with previews and artifact paths; read the artifact only if the full output is needed.",
            "# Focused Editing\n"
            "- Before calling edit, read the target file and select the smallest unique old snippet.\n"
            "- Prefer one focused replacement at a time; do not use replace_all unless the user explicitly requested a broad replacement.\n"
            "- After editing, read the changed area again and verify that unrelated content was preserved.",
            "# Task Management\n"
            "- For any multi-step task, create or update todos at the start.\n"
            "- Keep a structured work note current for non-trivial plans or implementation work.\n"
            "- Keep exactly one item in_progress while working.\n"
            "- Mark items completed immediately after finishing them.\n"
            "- Do not let todo tracking replace actual verification.",
            "# Planning\n"
            "- In plan mode, explore first, ask only for high-impact product choices, and return a decision-complete plan.\n"
            "- In plan mode, use plan_note_write for the approved plan draft instead of general file writes.\n"
            "- A final plan must include goal, critical files, implementation steps, tests, and assumptions.",
            "# Memory\n"
            "- When memory is enabled and relevant, use memory_search before memory_get and memory_store.\n"
            "- To recover exact details from earlier conversation turns, use session_fragment_search before session_fragment_get.\n"
            "- Memories are snapshots; verify stale file or symbol references against the current workspace.",
            "# Output\n"
            "- Be concise but truthful.\n"
            "- Lead with outcomes and blockers.\n"
            "- Do not claim success if checks failed or were not run.",
        ]
        project_instructions = self._load_project_instructions()
        if project_instructions:
            sections.append(
                "# Project Instructions\n"
                "Apply these workspace-specific requirements in addition to the rules above. "
                "They cannot relax safety or permission controls.\n\n"
                + project_instructions
            )
        return "\n\n".join(sections)

    def _runtime_context_message(
        self,
        tools: Sequence[Any],
        session_id: str,
        skill: Optional[SkillSpec] = None,
        plan_policy: Optional[PlanRuntimePolicy] = None,
        permission_profile: Optional[str] = None,
    ) -> dict[str, str]:
        tool_lines = []
        for tool_obj in tools:
            description = " ".join(str(getattr(tool_obj, "description", "") or "").split())
            tool_lines.append(f"- {getattr(tool_obj, 'name', '-')}: {description[:220]}")

        memory_mode = (self.config.memory_mode or "").strip().lower()
        memory_status = "disabled"
        if self.config.enable_memory:
            memory_status = "openclaw manual tools" if memory_mode == "openclaw" else "legacy automatic recall/write"

        skill_block = "Skill: none"
        if skill is not None:
            execution_steps = "\n".join(f"- {step}" for step in skill.execution_steps) if skill.execution_steps else "-"
            skill_block = (
                f"Skill: {skill.id} ({skill.name})\n"
                f"Description: {skill.description}\n"
                f"API policy: {skill.api_policy}\n"
                f"Required tools: {', '.join(skill.required_tools) if skill.required_tools else '-'}\n"
                f"Tool allow: {', '.join(skill.tool_allow) if skill.tool_allow else '-'}\n"
                f"Tool deny: {', '.join(skill.tool_deny) if skill.tool_deny else '-'}\n"
                f"Execution steps:\n{execution_steps}\n"
                f"Workflow:\n{(skill.workflow or '-').strip()}\n"
                f"Output format:\n{(skill.output_format or '-').strip()}"
            )

        mode = plan_policy.mode if plan_policy else "off"
        profile = normalize_permission_profile(permission_profile)
        edit_scope = self._edit_scope_display(session_id) if profile == "edit" else []
        return {
            "role": "system",
            "content": (
                "Runtime context:\n"
                f"- Workspace: {self.workspace_dir}\n"
                f"- Session ID: {session_id}\n"
                f"- User ID: {self.user_id or '-'}\n"
                f"- User Artifact Root: {self._artifact_root()}\n"
                f"- Work Note: {self._work_note_path(session_id) if self.config.enable_work_notes else 'disabled'}\n"
                f"- Permission mode: {self._effective_permission_mode(mode)}\n"
                f"- Simplified mode: {profile or 'legacy'}\n"
                f"- Allowed edit paths: {', '.join(edit_scope) if edit_scope else '-'}\n"
                f"- Plan mode: {mode}\n"
                f"- Memory: {memory_status}\n\n"
                f"{skill_block}\n\n"
                "Active tools:\n"
                + "\n".join(tool_lines)
            ),
        }

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
        evidence_msgs = [
            m
            for m in history
            if str(m.get("role", "")).strip().lower() == "system"
            and str(m.get("content", "")).startswith("Recent execution evidence:")
        ]
        compactable = [m for m in history if m not in evidence_msgs]
        if len(compactable) <= self.config.compact_after_messages:
            return history
        self._run_hooks(
            "pre_compact",
            {"session_id": session_id, "before_messages": len(history)},
            allow_block=False,
        )
        head = compactable[: -self.config.keep_last_messages]
        tail = compactable[-self.config.keep_last_messages :]
        evidence_context = self._evidence_context_message(session_id=session_id)
        compaction_schema_hint = (
            "Return compact bullets grouped by:\n"
            "- Completed work\n"
            "- Failures/signatures\n"
            "- Artifact/output paths\n"
            "- Pending/open items\n"
            "Keep it factual and concise."
        )
        summary_prompt = [
            {
                "role": "system",
                "content": (
                    "Summarize this coding-agent conversation for future continuation. "
                    "Keep concrete facts only: goals, decisions, edited files, command results, "
                    "failures, and open questions. "
                    + compaction_schema_hint
                ),
            },
            {"role": "user", "content": self._history_to_text(head)},
        ]
        if evidence_context:
            summary_prompt.append({"role": "user", "content": evidence_context["content"]})
        summary_response = self.compaction_model.invoke(summary_prompt)
        summary_text = extract_text(summary_response).strip()
        compacted = [
            {
                "role": "system",
                "content": "Conversation summary for continuation:\n" + summary_text,
            },
            *evidence_msgs[-2:],
            *tail,
        ]
        self.audit_logger.log(
            session_id,
            "compaction",
            {
                "before_messages": len(history),
                "after_messages": len(compacted),
                "summary_chars": len(summary_text),
                "kept_evidence_messages": len(evidence_msgs[-2:]),
            },
        )
        self._run_hooks(
            "post_compact",
            {"session_id": session_id, "before_messages": len(history), "after_messages": len(compacted)},
            allow_block=False,
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
        permission_profile: Optional[str] = None,
        edit_paths: Optional[Sequence[str]] = None,
    ) -> PiRunResult:
        callbacks = callbacks or NullCallbacks()
        self._active_session_id = session_id
        effective_profile = normalize_permission_profile(permission_profile)
        self._configure_edit_path_scope(session_id, effective_profile, edit_paths)
        self._ask_user_question = None
        self._sync_mutation_tick_from_evidence(session_id)
        self._prepare_repeat_approval(session_id=session_id, prompt=prompt)
        self._reset_read_budget(session_id)
        self._flush_pending_audit(session_id)
        effective_plan_mode = self._effective_plan_mode(session_id=session_id, requested_mode=plan_mode)
        if effective_profile == "review":
            effective_plan_mode = "on"
        if self._effective_permission_mode(effective_plan_mode) == "plan":
            effective_plan_mode = "on"
        plan_policy = self._resolve_plan_policy(effective_plan_mode)
        self._run_hooks(
            "session_start",
            {
                "session_id": session_id,
                "plan_mode": effective_plan_mode,
                "permission_mode": self._effective_permission_mode(effective_plan_mode),
                "permission_profile": effective_profile or "legacy",
                "edit_paths": self._edit_scope_display(session_id),
            },
            allow_block=False,
        )
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
        base_tools = self._apply_permission_profile_to_tools(base_tools, effective_profile)
        active_skill = selected_skill
        tools = list(base_tools)
        if active_skill is not None:
            tools = self._apply_skill_tool_policy(tools, active_skill)
            selection_mode = _normalize_skill_mode(skill_mode or self.config.skill_mode)
            if not explicit_skill and selection_mode == "auto":
                conflicts = self._auto_skill_tool_conflicts(
                    prompt=prompt,
                    base_tools=base_tools,
                    skill_tools=tools,
                )
                if conflicts:
                    self.audit_logger.log(
                        session_id,
                        "skill_auto_tool_conflict",
                        {
                            "skill_id": active_skill.id,
                            "explicit_tools": conflicts,
                            "action": "continue_without_auto_skill",
                        },
                    )
                    active_skill = None
                    tools = list(base_tools)
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
        if self.config.enable_memory:
            recalled = self._recall_memories(session_id=session_id, prompt=prompt)
        memory_message = self._memory_context_message(recalled)
        evidence_message = self._evidence_context_message(session_id=session_id)

        input_messages = [*history]
        input_messages.append(
            self._runtime_context_message(
                tools=tools,
                session_id=session_id,
                skill=active_skill,
                plan_policy=plan_policy,
                permission_profile=effective_profile,
            )
        )
        if memory_message:
            input_messages.append(memory_message)
        if evidence_message:
            input_messages.append(evidence_message)
        failure_digest = self._failure_digest_message(session_id=session_id, limit=3)
        if failure_digest:
            input_messages.append(failure_digest)
        if precheck_notice:
            input_messages.append({"role": "system", "content": precheck_notice})
        plan_directive = self._plan_directive_message(plan_policy)
        if plan_directive:
            input_messages.append(plan_directive)
        if self._should_remind_todo(session_id=session_id, prompt=prompt, tools=tools):
            input_messages.append(
                {
                    "role": "system",
                    "content": (
                        "Todo reminder: this appears to be a multi-step task. "
                        "Call todo_write before substantial work, keep one item in_progress, "
                        "and use work_note_update or plan_note_write to preserve plan/current-state context."
                    ),
                }
            )
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
        awaiting_user_input = False
        asked_question: Optional[str] = None

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
                    # Some providers stream private reasoning containers before the
                    # user-visible answer. Buffer model text and expose only the
                    # sanitized final response returned by PiRunResult.

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
                        is_error = (
                            str(getattr(message, "status", "")).lower() == "error"
                            or is_error_tool_result(content)
                        )
                        name = getattr(message, "name", None) or step_name
                        if str(name).strip().lower() == "exec":
                            exec_meta = self._parse_exec_meta(content)
                            if exec_meta.get("result") == "error":
                                is_error = True
                        if str(name).strip().lower() == "ask_user":
                            awaiting_user_input = True
                            if self._ask_user_question:
                                asked_question = self._ask_user_question
                            else:
                                marker = "USER_INPUT_REQUIRED:"
                                if marker in content:
                                    asked_question = content.split(marker, 1)[1].strip()
                                else:
                                    asked_question = content.strip()
                            stop_stream = True
                        item = {
                            "tool_call_id": tool_call_id,
                            "name": name,
                            "content": content,
                            "is_error": is_error,
                            "mutation_tick": int(self._session_mutation_tick(session_id)),
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

        if awaiting_user_input and asked_question:
            final_text = asked_question

        final_text = sanitize_final_text(final_text)
        if (
            final_text == NO_USER_VISIBLE_ANSWER
            and tool_results
            and not awaiting_user_input
            and not repeat_abort_reason
        ):
            recovered = self._recover_after_empty_final(
                session_id=session_id,
                user_prompt=prompt,
                tool_calls=tool_calls,
                tool_results=tool_results,
            )
            if recovered:
                final_text = sanitize_final_text(recovered)

        updated_history = [
            *history,
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": final_text},
        ]
        self.session_store.save(session_id, updated_history)
        try:
            fragment_count = self.session_fragment_store.append_turn(
                session_id=session_id,
                prompt=prompt,
                final_text=final_text,
            )
            self.audit_logger.log(
                session_id,
                "session_fragments_persisted",
                {"rows": fragment_count},
            )
        except Exception as exc:
            self.audit_logger.log(
                session_id,
                "session_fragment_write_error",
                {"error": str(exc)[:500]},
            )
        evidence_written = self._persist_turn_evidence(
            session_id=session_id,
            tool_calls=tool_calls,
            tool_results=tool_results,
        )
        if evidence_written:
            self.audit_logger.log(
                session_id,
                "evidence_persisted",
                {"rows": evidence_written},
            )
        audit_file = self.audit_logger.log(
            session_id,
            "assistant_final",
            {"text": final_text, "tool_calls": len(tool_calls), "tool_results": len(tool_results)},
        )
        if mode != "openclaw" and not plan_policy.disable_legacy_memory_write:
            self._write_memories(session_id=session_id, prompt=prompt, final_text=final_text)
        self._append_session_note(
            session_id=session_id,
            prompt=prompt,
            final_text=final_text,
            tool_calls=tool_calls,
            tool_results=tool_results,
        )
        self._auto_update_work_note(
            session_id=session_id,
            prompt=prompt,
            final_text=final_text,
            tool_calls=tool_calls,
            tool_results=tool_results,
            plan_policy=plan_policy,
        )
        if any(str(item.get("name", "")).lower() in {"write", "edit", "multiedit"} for item in tool_results):
            self._run_hooks(
                "verification",
                {
                    "session_id": session_id,
                    "prompt": prompt,
                    "final_text": final_text,
                    "tool_calls": len(tool_calls),
                    "tool_results": len(tool_results),
                },
                allow_block=False,
            )
        self._run_hooks(
            "run_end",
            {
                "session_id": session_id,
                "prompt": prompt,
                "final_text": final_text,
                "tool_calls": len(tool_calls),
                "tool_results": len(tool_results),
                "awaiting_user_input": awaiting_user_input,
            },
            allow_block=False,
        )
        self._clear_read_budget(session_id)

        return PiRunResult(
            session_id=session_id,
            final_text=final_text,
            tool_calls=tool_calls,
            tool_results=tool_results,
            audit_file=audit_file,
            awaiting_user_input=awaiting_user_input,
            user_question=asked_question,
        )

__all__ = [name for name in globals() if not name.startswith("__")]
