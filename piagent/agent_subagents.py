from __future__ import annotations

from .deps import *
from .models import *
from .workspace import WorkspaceGuard
from .stores import *
from .utils import *
from .mcp import McpStdioClient

class AgentSubagentsMixin:
    def _subagent_tool_names(self, agent_type: str) -> set[str]:
        base_readonly = {
            "read",
            "ls",
            "find",
            "grep",
            "web_fetch",
            "web_search",
            "tool_search",
            "mcp_list_resources",
            "mcp_read_resource",
            "mcp_list_resource_templates",
            "memory_search",
            "memory_get",
            "session_fragment_search",
            "session_fragment_get",
            "work_note_read",
            "work_note_search",
        }
        if agent_type in {"explore", "plan", "verify"}:
            base_readonly.add("exec_readonly")
        return base_readonly

    def _subagent_system_prompt(self, agent_type: str) -> str:
        common = (
            "You are a Pi subagent. Complete only the delegated task and return a concise report.\n"
            "Do not ask the user questions. Do not delegate further. Do not modify files.\n"
            "Use absolute or workspace-relative paths in findings."
        )
        if agent_type == "explore":
            return (
                common
                + "\nRole: explore. Search and read existing code efficiently. Return key files, facts, and uncertainties."
            )
        if agent_type == "plan":
            return (
                common
                + "\nRole: plan. Explore enough to design a concrete implementation plan. "
                "Return goal, critical files, steps, tests, and assumptions."
            )
        if agent_type == "verify":
            return (
                common
                + "\nRole: verify. Inspect the stated changes and run read-only checks/tests when possible. "
                "Return PASS, FAIL, or PARTIAL with exact evidence."
            )
        return common

    def _delegate_task_impl(self, description: str, prompt: str, agent_type: str) -> str:
        if not self.config.enable_subagents:
            return "Error: subagents are disabled."
        atype = str(agent_type or "").strip().lower()
        if atype not in {"explore", "plan", "verify"}:
            return "Error: agent_type must be one of: explore, plan, verify."
        task = str(prompt or "").strip()
        if not task:
            return "Error: prompt is empty."
        wanted = self._subagent_tool_names(atype)
        tools = [
            tool_obj
            for tool_obj in self.all_tools
            if str(getattr(tool_obj, "name", "")).strip() in wanted
        ]
        if not tools:
            return "Error: no subagent-safe tools are available."
        parent_session = str(self._active_session_id or "main")
        digest = hashlib.sha1(f"{atype}|{description}|{task}".encode("utf-8", errors="replace")).hexdigest()[:10]
        sub_session = f"{parent_session}__{atype}_{digest}"
        system_prompt = self._subagent_system_prompt(atype)
        agent = self._create_agent(tools=tools, system_prompt=system_prompt)
        prior_session = self._active_session_id
        partial_chunks: list[str] = []
        final_text = ""
        started = _now_ts()
        self.audit_logger.log(
            parent_session,
            "subagent_start",
            {"agent_type": atype, "description": str(description or "").strip(), "sub_session": sub_session},
        )
        try:
            self._active_session_id = sub_session
            runtime = self._runtime_context_message(
                tools=tools,
                session_id=sub_session,
                skill=None,
                plan_policy=PlanRuntimePolicy(mode="off"),
            )
            payload = {
                "messages": [
                    runtime,
                    {
                        "role": "user",
                        "content": (
                            f"Delegated task description: {str(description or '').strip() or atype}\n\n"
                            f"Task:\n{task}"
                        ),
                    },
                ]
            }
            for stream_mode, chunk in agent.stream(payload, stream_mode=["updates", "messages", "custom"]):
                if stream_mode == "messages":
                    token, _metadata = chunk
                    text = extract_text(token)
                    if text:
                        partial_chunks.append(text)
                elif stream_mode == "updates":
                    for _step_name, data in chunk.items():
                        messages = data.get("messages", []) if isinstance(data, dict) else []
                        if messages:
                            message = messages[-1]
                            if isinstance(message, AIMessage) and not message.tool_calls:
                                candidate = extract_text(message).strip()
                                if candidate:
                                    final_text = candidate
            if not final_text:
                final_text = "".join(partial_chunks).strip()
            if not final_text:
                final_text = "(subagent returned no text)"
            output = (
                f"subagent_type={atype}\n"
                f"description={str(description or '').strip() or '-'}\n"
                f"sub_session={sub_session}\n\n"
                f"{final_text}"
            )
            self.audit_logger.log(
                parent_session,
                "subagent_stop",
                {"agent_type": atype, "sub_session": sub_session, "elapsed_ms": int((_now_ts() - started) * 1000)},
            )
            return self._maybe_offload_tool_result(f"delegate_{atype}", output)
        except Exception as e:
            self.audit_logger.log(
                parent_session,
                "subagent_error",
                {"agent_type": atype, "sub_session": sub_session, "error": str(e)},
            )
            return f"Error running subagent {atype}: {e}"
        finally:
            self._active_session_id = prior_session

__all__ = [name for name in globals() if not name.startswith("__")]
