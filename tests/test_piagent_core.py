from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import piagent
from piagent import agent_core, agent_tools
from piagent import cli as pi_cli
import openclaw_pi_langchain as pi


class _DummyModel:
    def invoke(self, _messages):
        return SimpleNamespace(content='{"decision":"allow","reason":""}')


def _make_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **overrides) -> pi.OpenClawPiLangChain:
    monkeypatch.setattr(pi, "init_chat_model", lambda *args, **kwargs: _DummyModel())
    config_values = dict(
        model="dummy",
        workspace_dir=str(tmp_path),
        session_dir=str(tmp_path / ".sessions"),
        audit_dir=str(tmp_path / ".audit"),
        memory_dir=str(tmp_path / ".memory"),
        memory_embedding_provider="hash",
        mcp_enabled=False,
        skills_enabled=False,
        hooks_config_path=str(tmp_path / "pi_hooks.json"),
    )
    config_values.update(overrides)
    cfg = pi.PiAgentConfig(**config_values)
    return pi.OpenClawPiLangChain(cfg)


def _tool(agent: pi.OpenClawPiLangChain, name: str):
    for item in agent.all_tools:
        if str(getattr(item, "name", "")).strip() == name:
            return item
    raise AssertionError(f"tool not found: {name}")


def test_import_defaults_avoid_optional_torch_probe():
    assert os.environ.get("USE_TORCH") == "0"
    assert os.environ.get("TRANSFORMERS_VERBOSITY") == "error"


def test_cli_defaults_to_review_and_accepts_scoped_edit_paths():
    default_args = pi_cli.parse_args(["inspect"])
    edit_args = pi_cli.parse_args(
        ["change", "--mode", "edit", "--edit-path", "piagent/session.py"]
    )

    assert default_args.mode == "review"
    assert edit_args.mode == "edit"
    assert edit_args.edit_path == ["piagent/session.py"]


def test_piagent_direct_import_model_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(piagent, "init_chat_model", lambda *args, **kwargs: _DummyModel())
    agent = piagent.OpenClawPiLangChain(
        piagent.PiAgentConfig(
            model="dummy",
            workspace_dir=str(tmp_path),
            session_dir=str(tmp_path / ".sessions"),
            audit_dir=str(tmp_path / ".audit"),
            memory_dir=str(tmp_path / ".memory"),
            memory_embedding_provider="hash",
            mcp_enabled=False,
            skills_enabled=False,
            hooks_config_path=str(tmp_path / "pi_hooks.json"),
        )
    )
    try:
        assert agent.model.invoke([]).content
    finally:
        agent.close()


def test_local_bedrock_env_configures_openai_compatible_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def capture_model(*args, **kwargs):
        calls.append((args, kwargs))
        return _DummyModel()

    monkeypatch.setenv("LOCAL_BEDROCK_BASE_URL", "https://bedrock-runtime.ap-northeast-1.amazonaws.com")
    monkeypatch.setenv("LOCAL_BEDROCK_MODEL_ID", "openai.gpt-oss-120b-1:0")
    monkeypatch.setenv("LOCAL_BEDROCK_API_KEY", "test-bedrock-key")
    monkeypatch.setattr(pi, "init_chat_model", capture_model)

    cfg = pi.PiAgentConfig(
        model="unused-default",
        workspace_dir=str(tmp_path),
        session_dir=str(tmp_path / ".sessions"),
        audit_dir=str(tmp_path / ".audit"),
        memory_dir=str(tmp_path / ".memory"),
        memory_embedding_provider="hash",
        mcp_enabled=False,
        skills_enabled=False,
        hooks_config_path=str(tmp_path / "pi_hooks.json"),
    )
    agent = pi.OpenClawPiLangChain(cfg)
    try:
        assert len(calls) == 2
        for args, kwargs in calls:
            assert args[0] == "openai.gpt-oss-120b-1:0"
            assert kwargs["model_provider"] == "openai"
            assert kwargs["base_url"] == "https://bedrock-runtime.ap-northeast-1.amazonaws.com/openai/v1"
            assert kwargs["api_key"] == "test-bedrock-key"
    finally:
        agent.close()


def test_local_bedrock_env_requires_all_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOCAL_BEDROCK_BASE_URL", "https://bedrock-runtime.ap-northeast-1.amazonaws.com")
    monkeypatch.setenv("LOCAL_BEDROCK_MODEL_ID", "openai.gpt-oss-120b-1:0")
    monkeypatch.delenv("LOCAL_BEDROCK_API_KEY", raising=False)

    with pytest.raises(ValueError, match="LOCAL_BEDROCK_API_KEY"):
        agent_core._local_bedrock_model_settings()


def test_local_bedrock_env_rejects_non_bedrock_host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOCAL_BEDROCK_BASE_URL", "https://example.com")
    monkeypatch.setenv("LOCAL_BEDROCK_MODEL_ID", "openai.gpt-oss-120b-1:0")
    monkeypatch.setenv("LOCAL_BEDROCK_API_KEY", "test-bedrock-key")

    with pytest.raises(ValueError, match="Amazon Bedrock Runtime endpoint"):
        agent_core._local_bedrock_model_settings()


def test_new_tools_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        names = {str(getattr(t, "name", "")) for t in agent.all_tools}
        assert "ask_user" in names
        assert "enter_plan_mode" in names
        assert "exit_plan_mode" in names
        assert "web_search" in names
        assert "web_fetch" in names
        assert "tool_search" in names
        assert "mcp_list_resources" in names
        assert "mcp_read_resource" in names
        assert "mcp_list_resource_templates" in names
        assert "multiedit" in names
        assert "exec_readonly" in names
        assert "delegate_task" in names
        assert "work_note_read" in names
        assert "work_note_update" in names
        assert "work_note_search" in names
        assert "plan_note_write" in names
    finally:
        agent.close()


def test_user_artifact_rewrite_and_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch, user_id="alice")
    try:
        rewritten = agent.guard.resolve("reports/daily.md")
        expected = (tmp_path / "artifacts" / "users" / "alice" / "reports" / "daily.md").resolve()
        assert rewritten == expected
        with pytest.raises(ValueError):
            agent.guard.resolve("artifacts/users/bob/reports/x.md")
    finally:
        agent.close()


def test_namespaced_store_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch, user_id="bob")
    try:
        assert "users" in str(agent.session_store.root)
        assert "bob" in str(agent.session_store.root)
        assert "users" in str(agent.audit_logger.root)
        assert "bob" in str(agent.audit_logger.root)
        assert "users" in str(agent.memory_store.root)
        assert "bob" in str(agent.memory_store.root)
    finally:
        agent.close()


def test_hook_lite_pre_tool_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    hooks = {
        "pre_tool_use": [{"type": "command", "name": "always-block", "command": "echo decision:block", "timeout_s": 5}],
        "post_tool_use": [],
        "run_end": [],
    }
    (tmp_path / "pi_hooks.json").write_text(json.dumps(hooks), encoding="utf-8")
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        write_tool = _tool(agent, "write")
        out = write_tool.invoke({"path": "a.txt", "content": "hello"})
        assert "Blocked by pre_tool_use hook" in str(out)
    finally:
        agent.close()


def test_basic_workflow_compatibility(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch, user_id="u1")
    try:
        write_tool = _tool(agent, "write")
        edit_tool = _tool(agent, "edit")
        multiedit_tool = _tool(agent, "multiedit")
        read_tool = _tool(agent, "read")
        todo_write_tool = _tool(agent, "todo_write")
        todo_read_tool = _tool(agent, "todo_read")

        out = write_tool.invoke({"path": "reports/x.txt", "content": "alpha beta gamma"})
        assert "wrote" in str(out)

        out = edit_tool.invoke({"path": "reports/x.txt", "old": "beta", "new": "BETA", "replace_all": False})
        assert "updated" in str(out)

        ops = json.dumps(
            [
                {"old": "alpha", "new": "ALPHA", "replace_all": False},
                {"old": "gamma", "new": "GAMMA", "replace_all": False},
            ]
        )
        out = multiedit_tool.invoke({"path": "reports/x.txt", "edits_json": ops})
        assert "updated" in str(out)

        text = str(read_tool.invoke({"path": "reports/x.txt", "full": True}))
        assert "ALPHA BETA GAMMA" in text

        todo_write_tool.invoke(
            {
                "todos": json.dumps(
                    [
                        {"content": "one", "status": "pending", "priority": "high"},
                        {"content": "two", "status": "in_progress", "priority": "medium"},
                    ]
                )
            }
        )
        todos = str(todo_read_tool.invoke({}))
        assert "#1 one" in todos
        assert "#2 two" in todos

        structured_out = str(
            todo_write_tool.invoke(
                {
                    "todos": [
                        {"content": "structured", "status": "in_progress", "priority": "low"},
                    ]
                }
            )
        )
        assert "Todo list updated: 1 item(s)." in structured_out
        assert "#1 structured" in str(todo_read_tool.invoke({}))

        artifact_file = tmp_path / "artifacts" / "users" / "u1" / "reports" / "x.txt"
        assert artifact_file.exists()
        assert not (tmp_path / "reports" / "x.txt").exists()
    finally:
        agent.close()


def test_tool_search_and_mcp_resource_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        tool_search = _tool(agent, "tool_search")
        result = str(tool_search.invoke({"query": "ask_user", "limit": 5}))
        assert "ask_user" in result

        mcp_list = _tool(agent, "mcp_list_resources")
        out = str(mcp_list.invoke({}))
        assert "No MCP servers connected." in out
    finally:
        agent.close()


def test_custom_tool_module_respects_blocked_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    blocked_dir = tmp_path / "blocked"
    blocked_dir.mkdir()
    tool_file = blocked_dir / "custom_tool.py"
    tool_file.write_text(
        "from langchain.tools import tool\n"
        "@tool('blocked_custom')\n"
        "def blocked_custom() -> str:\n"
        "    return 'should not load'\n"
        "TOOLS = [blocked_custom]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="blocked path by policy"):
        _make_agent(
            tmp_path,
            monkeypatch,
            custom_tool_modules=["blocked/custom_tool.py"],
            blocked_paths=["blocked/**"],
        )


def test_custom_tool_module_name_respects_blocked_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    package_dir = tmp_path / "blockedpkg"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "custom_tool.py").write_text(
        "from langchain.tools import tool\n"
        "@tool('blocked_custom')\n"
        "def blocked_custom() -> str:\n"
        "    return 'should not load'\n"
        "TOOLS = [blocked_custom]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="blocked path by policy"):
        _make_agent(
            tmp_path,
            monkeypatch,
            custom_tool_modules=["blockedpkg.custom_tool"],
            blocked_paths=["blockedpkg/**"],
        )


def test_web_fetch_blocks_loopback_addresses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        web_fetch = _tool(agent, "web_fetch")
        out = str(web_fetch.invoke({"url": "http://127.0.0.1:8080"}))
        assert "blocked URL by SSRF policy" in out
        assert "127.0.0.1" in out
    finally:
        agent.close()


def test_generic_write_forced_into_user_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch, user_id="alice")
    try:
        write_tool = _tool(agent, "write")
        read_tool = _tool(agent, "read")

        out = str(write_tool.invoke({"path": "time_series_data.csv", "content": "date,value\n2023-01-01,1"}))
        assert "artifacts/users/alice/workspace/time_series_data.csv" in out.replace("\\", "/")

        text = str(read_tool.invoke({"path": "time_series_data.csv", "full": True}))
        assert "date,value" in text
    finally:
        agent.close()


def test_session_evidence_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch, user_id="alice")
    try:
        written = agent._persist_turn_evidence(
            session_id="main",
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "write",
                    "args": {"path": "reports/test.md", "content": "hello"},
                }
            ],
            tool_results=[
                {
                    "tool_call_id": "call-1",
                    "name": "write",
                    "content": "wrote 5 chars to artifacts/users/alice/reports/test.md",
                    "is_error": False,
                    "mutation_tick": 3,
                }
            ],
        )
        assert written == 1
        rows = agent.evidence_store.load("main")
        assert len(rows) == 1
        row = rows[0]
        assert row["tool_name"] == "write"
        assert row["args_signature"] == agent._tool_args_signature(
            "write", {"path": "reports/test.md", "content": "hello"}
        )
        assert row["is_error"] is False
        assert row["error_signature"] == "-"
        assert row["mutation_tick"] == 3
        assert "artifacts/users/alice/reports/test.md" in row["artifact_paths"]
    finally:
        agent.close()


def test_repeat_guard_blocks_identical_signature(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch, user_id="alice")
    try:
        sid = "main"
        agent._active_session_id = sid
        signature = agent._tool_args_signature(
            "write", {"path": "reports/a.txt", "content": "same-content"}
        )
        agent._append_evidence_record(
            sid,
            {
                "tool_name": "write",
                "args_signature": signature,
                "is_error": False,
                "result_summary": "wrote previous output",
                "error_signature": "-",
                "mutation_tick": 0,
                "artifact_paths": ["artifacts/users/alice/reports/a.txt"],
                "ts": 1.0,
            },
        )
        write_tool = _tool(agent, "write")
        out = str(write_tool.invoke({"path": "reports/a.txt", "content": "same-content"}))
        assert "Repeat guard" in out
        assert "previous_result=wrote previous output" in out
    finally:
        agent.close()


def test_repeat_guard_approval_token_allows_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        sid = "main"
        agent._active_session_id = sid
        todos_arg = json.dumps(
            [{"content": "task one", "status": "pending", "priority": "high"}],
            ensure_ascii=False,
        )
        signature = agent._tool_args_signature("todo_write", {"todos": todos_arg})
        agent._append_evidence_record(
            sid,
            {
                "tool_name": "todo_write",
                "args_signature": signature,
                "is_error": False,
                "result_summary": "Todo list updated: 1 item(s).",
                "error_signature": "-",
                "mutation_tick": 0,
                "artifact_paths": [],
                "ts": 1.0,
            },
        )
        agent._prepare_repeat_approval(
            sid, f"같은거 다시 해줘 {agent.config.repeat_confirm_token}"
        )
        todo_write_tool = _tool(agent, "todo_write")
        out1 = str(todo_write_tool.invoke({"todos": todos_arg}))
        assert "Todo list updated: 1 item(s)." in out1
        out2 = str(todo_write_tool.invoke({"todos": todos_arg}))
        assert "Repeat guard" in out2
    finally:
        agent.close()


def test_evidence_injected_into_next_turn_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    class _FakeStreamAgent:
        def stream(self, payload, stream_mode):  # type: ignore[no-untyped-def]
            captured["messages"] = payload["messages"]
            yield (
                "updates",
                {
                    "final": {
                        "messages": [pi.AIMessage(content="ok")],
                    }
                },
            )

    try:
        agent._append_evidence_record(
            "main",
            {
                "tool_name": "exec",
                "args_signature": "abc123",
                "is_error": True,
                "result_summary": "ModuleNotFoundError: No module named pandas",
                "error_signature": "sig-1",
                "mutation_tick": 0,
                "artifact_paths": ["artifacts/users/alice/reports/time_series_plot.png"],
                "ts": 1.0,
            },
        )
        monkeypatch.setattr(agent, "_create_agent", lambda tools, system_prompt: _FakeStreamAgent())
        result = agent.run(session_id="main", prompt="다음 단계 뭐야?")
        assert result.final_text == "ok"
        msgs = captured.get("messages")
        assert isinstance(msgs, list)
        assert any(
            isinstance(m, dict)
            and str(m.get("role", "")).lower() == "system"
            and "Recent execution evidence:" in str(m.get("content", ""))
            for m in msgs
        )
    finally:
        agent.close()


def test_empty_reasoning_only_final_recovers_once_from_tool_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    class _RecoveryModel:
        def invoke(self, messages):  # type: ignore[no-untyped-def]
            assert "Do not call tools" in messages[0]["content"]
            assert "verified evidence" in messages[1]["content"]
            return SimpleNamespace(content="근거를 바탕으로 작성한 최종 답변")

    class _FakeStreamAgent:
        def stream(self, payload, stream_mode):  # type: ignore[no-untyped-def]
            yield (
                "updates",
                {
                    "tools": {
                        "messages": [
                            pi.ToolMessage(
                                content="verified evidence",
                                tool_call_id="call-1",
                                name="read",
                            )
                        ]
                    }
                },
            )
            yield (
                "updates",
                {"final": {"messages": [pi.AIMessage(content="<reasoning>private only")]}},
            )

    agent = _make_agent(tmp_path, monkeypatch)
    agent.model = _RecoveryModel()
    try:
        monkeypatch.setattr(agent, "_create_agent", lambda tools, system_prompt: _FakeStreamAgent())
        result = agent.run(session_id="recover", prompt="근거로 답해줘")

        assert result.final_text == "근거를 바탕으로 작성한 최종 답변"
        audit = agent.audit_logger.path_for("recover").read_text(encoding="utf-8")
        assert "empty_final_recovery_start" in audit
        assert "empty_final_recovery_ok" in audit
    finally:
        agent.close()


def test_compaction_keeps_recent_evidence_messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(
        tmp_path,
        monkeypatch,
        compact_after_messages=4,
        keep_last_messages=2,
    )
    try:
        history = [
            {
                "role": "system",
                "content": "Recent execution evidence:\n- write [ok] sig=x summary=wrote report.md",
            },
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
            {"role": "user", "content": "e"},
            {"role": "assistant", "content": "f"},
        ]
        compacted = agent._compact_history(history, session_id="main")
        assert any(
            m.get("role") == "system"
            and str(m.get("content", "")).startswith("Recent execution evidence:")
            for m in compacted
        )
        assert any(
            m.get("role") == "system"
            and str(m.get("content", "")).startswith("Conversation summary for continuation:")
            for m in compacted
        )
    finally:
        agent.close()


def test_prompt_builder_static_and_runtime_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch, user_id="alice")
    try:
        prompt = agent._build_system_prompt(agent.all_tools, session_id="main")
        assert "# Identity" in prompt
        assert "# Tool Usage" in prompt
        assert str(tmp_path) not in prompt

        ctx = agent._runtime_context_message(agent.all_tools, session_id="main")
        text = ctx["content"]
        assert "Runtime context:" in text
        assert str(tmp_path) in text
        assert "Active tools:" in text
    finally:
        agent.close()


def test_optional_project_instructions_are_added_to_system_prompt_and_can_require_korean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    agent = _make_agent(tmp_path, monkeypatch)
    captured: dict[str, str] = {}

    class _FakeStreamAgent:
        def __init__(self, system_prompt: str):
            self.system_prompt = system_prompt

        def stream(self, payload, stream_mode):  # type: ignore[no-untyped-def]
            del payload, stream_mode
            answer = "중요한 답변입니다." if "항상 한국어로 답변하세요" in self.system_prompt else "Important answer."
            yield ("updates", {"final": {"messages": [pi.AIMessage(content=answer)]}})

    try:
        assert "# Project Instructions" not in agent._build_system_prompt(agent.all_tools, "main")
        instructions = tmp_path / ".piagent" / "INSTRUCTIONS.md"
        instructions.parent.mkdir()
        instructions.write_text("항상 한국어로 답변하세요.", encoding="utf-8")

        def _fake_create_agent(tools, system_prompt):  # type: ignore[no-untyped-def]
            del tools
            captured["system_prompt"] = system_prompt
            return _FakeStreamAgent(system_prompt)

        monkeypatch.setattr(agent, "_create_agent", _fake_create_agent)
        result = agent.run(session_id="main", prompt="Give me the important answer.")

        assert "# Project Instructions" in captured["system_prompt"]
        assert "항상 한국어로 답변하세요." in captured["system_prompt"]
        assert result.final_text == "중요한 답변입니다."
    finally:
        agent.close()


def test_plan_mode_denies_mutating_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        policy = agent._resolve_plan_policy("on")
        tools = agent._apply_plan_policy_to_tools(agent.all_tools, policy)
        names = {str(getattr(t, "name", "")) for t in tools}
        assert "write" not in names
        assert "edit" not in names
        assert "multiedit" not in names
        assert "exec" not in names
        assert "exec_readonly" not in names
        assert "python_package_install" not in names
        assert "memory_store" not in names
        assert "work_note_update" not in names
        assert "plan_note_write" in names
        assert "read" in names
        assert "<proposed_plan>" in policy.planner_directive
    finally:
        agent.close()


def test_python_package_install_is_disabled_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        installer = _tool(agent, "python_package_install")
        out = str(installer.invoke({"package": "python-docx", "import_name": "docx"}))
        assert "installation is disabled" in out
        assert not (tmp_path / ".piagent" / "packages").exists()
    finally:
        agent.close()


def test_python_package_install_rejects_options_and_non_allowlisted_packages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    agent = _make_agent(
        tmp_path,
        monkeypatch,
        allow_package_install=True,
        package_install_allowlist=["python-docx==1.2.0"],
    )
    calls = []
    monkeypatch.setattr(agent_tools.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    try:
        installer = _tool(agent, "python_package_install")
        option_out = str(installer.invoke({"package": "python-docx --index-url https://example.com"}))
        denied_out = str(installer.invoke({"package": "matplotlib"}))
        wrong_pin = str(installer.invoke({"package": "python-docx", "version": "9.9.9", "import_name": "docx"}))
        assert "bare PyPI name" in option_out
        assert "not in package_install_allowlist" in denied_out
        assert "pinned to 1.2.0" in wrong_pin
        assert calls == []
    finally:
        agent.close()


def test_python_package_install_uses_workspace_target_and_verifies_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    agent = _make_agent(
        tmp_path,
        monkeypatch,
        allow_package_install=True,
        package_install_allowlist=["python-docx==1.2.0"],
        package_install_timeout_s=45,
    )
    calls: list[tuple[list[str], dict]] = []

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((list(command), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(agent_tools.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(agent_tools.subprocess, "run", fake_run)
    try:
        installer = _tool(agent, "python_package_install")
        out = str(installer.invoke({"package": "python-docx", "import_name": "docx"}))

        target = tmp_path / ".piagent" / "packages"
        assert "status=installed" in out
        assert "package=python-docx==1.2.0" in out
        assert "verification=import_ok" in out
        assert target.exists()
        assert calls[0][0][:4] == [agent_tools.sys.executable, "-m", "pip", "install"]
        assert "--target" in calls[0][0]
        assert str(target) in calls[0][0]
        assert calls[0][0][-1] == "python-docx==1.2.0"
        assert calls[0][1]["timeout"] == 45
        assert calls[1][0][-1] == "docx"
        assert str(target) in calls[1][1]["env"]["PYTHONPATH"]
    finally:
        agent.close()


def test_workspace_extensions_are_opt_in_and_load_folder_tool_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    tool_dir = tmp_path / ".piagent" / "tools" / "echo-tool"
    tool_dir.mkdir(parents=True)
    (tool_dir / "tool.py").write_text(
        "from langchain.tools import tool\n"
        "@tool\n"
        "def workspace_echo(text: str) -> str:\n"
        "    \"\"\"Echo text from a workspace extension.\"\"\"\n"
        "    return f'workspace:{text}'\n"
        "TOOLS = [workspace_echo]\n",
        encoding="utf-8",
    )

    disabled_agent = _make_agent(tmp_path, monkeypatch)
    try:
        assert all(getattr(item, "name", "") != "workspace_echo" for item in disabled_agent.all_tools)
    finally:
        disabled_agent.close()

    enabled_agent = _make_agent(tmp_path, monkeypatch, workspace_extensions_enabled=True)
    try:
        extension_tool = _tool(enabled_agent, "workspace_echo")
        assert extension_tool.invoke({"text": "hello"}) == "workspace:hello"
    finally:
        enabled_agent.close()


def test_workspace_extension_discovers_standard_skill_frontmatter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    skill_dir = tmp_path / ".piagent" / "skills" / "stock-report"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: stock-report\n"
        "description: Create a stock report when the user requests price analysis.\n"
        "---\n"
        "Use verified market data and state its observation date.\n",
        encoding="utf-8",
    )

    agent = _make_agent(
        tmp_path,
        monkeypatch,
        skills_enabled=True,
        workspace_extensions_enabled=True,
    )
    try:
        assert "stock-report" in agent.skills_by_id
        skill = agent.skills_by_id["stock-report"]
        assert "verified market data" in skill.workflow
        assert Path(skill.source_path) == skill_dir / "SKILL.md"
    finally:
        agent.close()


def test_workspace_extension_ignores_flat_tool_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    tools_dir = tmp_path / ".piagent" / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "flat.py").write_text("raise RuntimeError('must not load')\n", encoding="utf-8")

    agent = _make_agent(tmp_path, monkeypatch, workspace_extensions_enabled=True)
    try:
        assert all(getattr(item, "name", "") != "flat" for item in agent.all_tools)
    finally:
        agent.close()


def test_workspace_extension_root_cannot_escape_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(ValueError, match="workspace_extension_dir escapes workspace"):
        _make_agent(
            tmp_path,
            monkeypatch,
            workspace_extensions_enabled=True,
            workspace_extension_dir="../outside",
        )


def test_python_package_install_redacts_index_credentials_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    agent = _make_agent(
        tmp_path,
        monkeypatch,
        allow_package_install=True,
        package_install_allowlist=["safe-package"],
    )
    monkeypatch.setattr(agent_tools.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="index https://user:secret@example.com token=secret-value",
        ),
    )
    try:
        installer = _tool(agent, "python_package_install")
        out = str(installer.invoke({"package": "safe-package", "import_name": "safe_package"}))
        assert "pip install failed" in out
        assert "secret-value" not in out
        assert "user:secret" not in out
        assert "***" in out
    finally:
        agent.close()


def test_exec_cannot_bypass_package_install_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    agent = _make_agent(tmp_path, monkeypatch)
    monkeypatch.setenv("PI_EXEC_ALLOW_DANGEROUS", "true")

    def fail_if_called(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("shell subprocess must not run")

    monkeypatch.setattr(agent_tools.subprocess, "run", fail_if_called)
    try:
        exec_tool = _tool(agent, "exec")
        for command in (
            "pip install python-docx",
            "python -m pip install python-docx",
            "uv pip install python-docx",
        ):
            out = str(exec_tool.invoke({"command": command, "cwd": "."}))
            assert "PACKAGE_INSTALL_POLICY" in out
            assert "python_package_install" in out
    finally:
        agent.close()


def test_permission_mode_plan_applies_plan_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch, permission_mode="plan")
    captured: dict[str, object] = {}

    class _FakeStreamAgent:
        def stream(self, payload, stream_mode):  # type: ignore[no-untyped-def]
            captured["messages"] = payload["messages"]
            yield (
                "updates",
                {
                    "final": {
                        "messages": [pi.AIMessage(content="planned")],
                    }
                },
            )

    try:
        def _fake_create_agent(tools, system_prompt):  # type: ignore[no-untyped-def]
            captured["tools"] = tools
            return _FakeStreamAgent()

        monkeypatch.setattr(agent, "_create_agent", _fake_create_agent)
        result = agent.run(session_id="main", prompt="plan the work")
        assert result.final_text == "planned"
        names = {str(getattr(t, "name", "")) for t in captured["tools"]}  # type: ignore[index]
        assert "write" not in names
        assert "exec" not in names
    finally:
        agent.close()


def test_simplified_permission_profiles_cap_available_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    agent = _make_agent(tmp_path, monkeypatch)
    captured: list[set[str]] = []

    class _FakeStreamAgent:
        def stream(self, payload, stream_mode):  # type: ignore[no-untyped-def]
            yield ("updates", {"final": {"messages": [pi.AIMessage(content="ok")]}})

    def capture_tools(tools, system_prompt):  # type: ignore[no-untyped-def]
        captured.append({str(getattr(item, "name", "")) for item in tools})
        return _FakeStreamAgent()

    monkeypatch.setattr(agent, "_create_agent", capture_tools)
    try:
        agent.run(
            session_id="review",
            prompt="inspect",
            allowlist=["read", "write", "exec"],
            permission_profile="review",
        )
        agent.run(
            session_id="edit",
            prompt="change",
            permission_profile="edit",
            edit_paths=["target.py"],
        )
        agent.run(session_id="full", prompt="work", permission_profile="full")

        review_tools, edit_tools, full_tools = captured
        assert "read" in review_tools
        assert "edit" not in review_tools
        assert "write" not in review_tools
        assert "exec" not in review_tools
        assert "edit" in edit_tools
        assert "write" not in edit_tools
        assert "multiedit" not in edit_tools
        assert "exec" not in edit_tools
        assert {"edit", "write", "multiedit", "exec"}.issubset(full_tools)
    finally:
        agent.close()


def test_scoped_edit_only_changes_an_explicit_existing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    allowed = tmp_path / "allowed.py"
    blocked = tmp_path / "blocked.py"
    allowed.write_bytes(b"VALUE = 1\r\nKEEP = True\r\n")
    blocked.write_text("VALUE = 1\n", encoding="utf-8")
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        agent._active_session_id = "scoped-edit"
        agent._configure_edit_path_scope("scoped-edit", "edit", ["allowed.py"])
        edit_tool = _tool(agent, "edit")

        changed = str(
            edit_tool.invoke(
                {"path": "allowed.py", "old": "VALUE = 1\n", "new": "VALUE = 2\n"}
            )
        )
        denied_path = str(
            edit_tool.invoke(
                {"path": "blocked.py", "old": "VALUE = 1", "new": "VALUE = 2"}
            )
        )
        denied_bulk = str(
            edit_tool.invoke(
                {
                    "path": "allowed.py",
                    "old": "KEEP = True",
                    "new": "KEEP = False",
                    "replace_all": True,
                }
            )
        )

        assert "updated allowed.py" in changed
        assert allowed.read_bytes() == b"VALUE = 2\r\nKEEP = True\r\n"
        assert "edit mode blocks" in denied_path
        assert blocked.read_text(encoding="utf-8") == "VALUE = 1\n"
        assert "replace_all is disabled" in denied_bulk
    finally:
        agent.close()


def test_edit_profile_requires_an_existing_file_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        with pytest.raises(ValueError, match="requires at least one edit path"):
            agent.run(session_id="edit", prompt="change", permission_profile="edit")
        with pytest.raises(ValueError, match="only accepts existing files"):
            agent.run(
                session_id="edit",
                prompt="change",
                permission_profile="edit",
                edit_paths=["missing.py"],
            )
    finally:
        agent.close()


def test_tool_result_offload_for_large_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch, user_id="alice", max_tool_result_chars=1200)
    try:
        big = "x" * 3000
        (tmp_path / "big.txt").write_text(big, encoding="utf-8")
        read_tool = _tool(agent, "read")
        out = str(read_tool.invoke({"path": "big.txt", "full": True}))
        assert "tool_result_truncated=true" in out
        assert "full_result_path=artifacts/users/alice/tool-results/" in out.replace("\\", "/")
    finally:
        agent.close()


def test_exec_readonly_blocks_mutating_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        exec_readonly = _tool(agent, "exec_readonly")
        out = str(exec_readonly.invoke({"command": "rm -rf .", "cwd": "."}))
        assert "READONLY_POLICY" in out
    finally:
        agent.close()


def test_exec_readonly_invokes_allowed_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=" M src/payment.py\n",
            stderr="",
        ),
    )
    try:
        exec_readonly = _tool(agent, "exec_readonly")
        out = str(exec_readonly.invoke({"command": "git status --short", "cwd": "."}))
        assert "exit_code=0" in out
        assert "M src/payment.py" in out
        assert "StructuredTool" not in out
    finally:
        agent.close()


def test_delegate_task_uses_readonly_subagent_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    class _FakeStreamAgent:
        def stream(self, payload, stream_mode):  # type: ignore[no-untyped-def]
            captured["messages"] = payload["messages"]
            yield (
                "updates",
                {
                    "final": {
                        "messages": [pi.AIMessage(content="subagent report")],
                    }
                },
            )

    def _fake_create_agent(tools, system_prompt):  # type: ignore[no-untyped-def]
        captured["tools"] = {str(getattr(t, "name", "")) for t in tools}
        captured["system_prompt"] = system_prompt
        return _FakeStreamAgent()

    try:
        monkeypatch.setattr(agent, "_create_agent", _fake_create_agent)
        delegate = _tool(agent, "delegate_task")
        out = str(delegate.invoke({"description": "scan code", "prompt": "find important files", "agent_type": "explore"}))
        assert "subagent report" in out
        tools = captured["tools"]
        assert isinstance(tools, set)
        assert "read" in tools
        assert "work_note_read" in tools
        assert "work_note_search" in tools
        assert "write" not in tools
        assert "edit" not in tools
        assert "work_note_update" not in tools
        assert "delegate_task" not in tools
    finally:
        agent.close()


def test_session_note_written_after_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)

    class _FakeStreamAgent:
        def stream(self, payload, stream_mode):  # type: ignore[no-untyped-def]
            yield (
                "updates",
                {
                    "final": {
                        "messages": [pi.AIMessage(content="ok")],
                    }
                },
            )

    try:
        monkeypatch.setattr(agent, "_create_agent", lambda tools, system_prompt: _FakeStreamAgent())
        result = agent.run(session_id="main", prompt="implement a thing with tests")
        assert result.final_text == "ok"
        note_path = agent._session_note_path("main")
        assert note_path.exists()
        assert "implement a thing" in note_path.read_text(encoding="utf-8")
    finally:
        agent.close()


def test_session_fragment_store_search_get_and_survives_history_rewrite(tmp_path: Path):
    fragment_store = pi.SessionFragmentStore(tmp_path / ".sessions")
    session_store = pi.FlatSessionStore(tmp_path / ".sessions")

    written = fragment_store.append_turn(
        "research-main",
        "베트남 뉴스 보고서는 한국어로 작성하고 출처 날짜를 표시해줘.",
        "요청한 형식으로 베트남 경제 뉴스를 정리했습니다.",
    )
    assert written == 2

    found = fragment_store.search("research-main", "한국어 출처 날짜", role="user")
    assert len(found) == 1
    assert found[0]["role"] == "user"
    assert "한국어" in found[0]["snippet"]

    full_rows = fragment_store.get_by_ids("research-main", [str(found[0]["id"])])
    assert full_rows[0]["content"] == "베트남 뉴스 보고서는 한국어로 작성하고 출처 날짜를 표시해줘."

    # Normal history may be compacted/replaced; append-only fragments must remain searchable.
    session_store.save("research-main", [{"role": "assistant", "content": "compressed summary"}])
    assert fragment_store.search("research-main", "베트남 뉴스")


def test_session_fragment_store_chunks_long_content(tmp_path: Path):
    fragment_store = pi.SessionFragmentStore(tmp_path / ".sessions")
    long_prompt = ("앞부분 데이터 " * 180) + " 마지막검색표식 ORCHID-7319"

    count = fragment_store.append_turn("long-session", long_prompt, "처리 완료")
    rows = fragment_store.load("long-session")

    assert count == len(rows)
    assert len([row for row in rows if row["role"] == "user"]) >= 2
    assert all(int(row["char_count"]) <= pi.SessionFragmentStore.MAX_FRAGMENT_CHARS for row in rows)
    assert fragment_store.search("long-session", "ORCHID-7319", role="user")


def test_session_fragment_tools_search_then_get_full_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        agent._active_session_id = "tool-session"
        agent.session_fragment_store.append_turn(
            "tool-session",
            "프로젝트 식별자는 JADE-2048이며 결과는 표로 작성한다.",
            "식별자와 출력 형식을 기억했습니다.",
        )
        search_tool = _tool(agent, "session_fragment_search")
        get_tool = _tool(agent, "session_fragment_get")

        search_payload = json.loads(str(search_tool.invoke({"query": "JADE-2048", "role": "user"})))
        assert search_payload["status"] == "ok"
        assert search_payload["result_count"] == 1

        fragment_id = search_payload["results"][0]["id"]
        get_payload = json.loads(str(get_tool.invoke({"ids": fragment_id})))
        assert get_payload["status"] == "ok"
        assert get_payload["results"][0]["content"] == "프로젝트 식별자는 JADE-2048이며 결과는 표로 작성한다."
    finally:
        agent.close()


def test_session_fragment_tools_validate_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        search_tool = _tool(agent, "session_fragment_search")
        get_tool = _tool(agent, "session_fragment_get")

        assert json.loads(str(search_tool.invoke({"query": ""})))["status"] == "error"
        assert json.loads(str(search_tool.invoke({"query": "hello", "role": "system"})))["status"] == "error"
        assert json.loads(str(search_tool.invoke({"query": "hello", "session_id": "../outside"})))["status"] == "error"
        assert json.loads(str(get_tool.invoke({"ids": ""})))["status"] == "error"
        too_many = ",".join(f"frag-{index}" for index in range(21))
        assert json.loads(str(get_tool.invoke({"ids": too_many})))["status"] == "error"
    finally:
        agent.close()


def test_session_fragments_are_written_after_run_and_user_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    class _FakeStreamAgent:
        def stream(self, payload, stream_mode):  # type: ignore[no-untyped-def]
            yield (
                "updates",
                {"final": {"messages": [pi.AIMessage(content="응답 표식은 AMBER-9081입니다.")]}},
            )

    alice = _make_agent(tmp_path, monkeypatch, user_id="alice")
    try:
        monkeypatch.setattr(alice, "_create_agent", lambda tools, system_prompt: _FakeStreamAgent())
        result = alice.run(session_id="shared-name", prompt="사용자 요청 표식은 AMBER-9081입니다.")
        assert result.final_text == "응답 표식은 AMBER-9081입니다."
        assert alice.session_fragment_store.search("shared-name", "AMBER-9081", role="user")
        assert alice.session_fragment_store.search("shared-name", "AMBER-9081", role="assistant")
        audit = alice.audit_logger.path_for("shared-name").read_text(encoding="utf-8")
        assert "session_fragments_persisted" in audit
    finally:
        alice.close()

    bob = _make_agent(tmp_path, monkeypatch, user_id="bob")
    try:
        assert bob.session_fragment_store.search("shared-name", "AMBER-9081") == []
    finally:
        bob.close()


def test_work_note_tools_create_update_search_and_offload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch, user_id="alice", max_tool_result_chars=1200)
    try:
        update = _tool(agent, "work_note_update")
        read = _tool(agent, "work_note_read")
        search = _tool(agent, "work_note_search")

        out = str(update.invoke({"section": "Critical Files", "content": "src/app.py handles the Pi loop", "mode": "append"}))
        assert "updated work note section 'Critical Files'" in out
        assert "artifacts/users/alice/work-notes/main.md" in out.replace("\\", "/")

        text = str(read.invoke({}))
        assert "# Critical Files" in text
        assert "src/app.py handles the Pi loop" in text

        found = str(search.invoke({"pattern": "Pi loop", "section": "Critical Files"}))
        assert "Critical Files:" in found
        assert "Pi loop" in found

        big = "needle\n" * 800
        update.invoke({"section": "Worklog", "content": big, "mode": "replace"})
        found_big = str(search.invoke({"pattern": "needle"}))
        assert "tool_result_truncated=true" in found_big
        assert "full_result_path=artifacts/users/alice/tool-results/" in found_big.replace("\\", "/")
    finally:
        agent.close()


def test_plan_note_write_allowed_but_work_note_update_filtered_in_plan_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch, user_id="alice")
    try:
        policy = agent._resolve_plan_policy("on")
        names = {str(getattr(t, "name", "")) for t in agent._apply_plan_policy_to_tools(agent.all_tools, policy)}
        assert "plan_note_write" in names
        assert "work_note_update" not in names

        plan_write = _tool(agent, "plan_note_write")
        out = str(plan_write.invoke({"content": "PLAN:\n- inspect\n- implement\n- test"}))
        assert "updated work note section 'Task Spec'" in out
        note = agent._work_note_path("main").read_text(encoding="utf-8")
        assert "PLAN:" in note
        assert "Await user approval" in note
    finally:
        agent.close()


def test_grep_modes_glob_pagination_and_find_pagination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("needle one\nother\n", encoding="utf-8")
        (src / "b.py").write_text("NEEDLE two\nneedle three\n", encoding="utf-8")
        (src / "c.txt").write_text("needle text\n", encoding="utf-8")

        grep = _tool(agent, "grep")
        find = _tool(agent, "find")

        files = str(grep.invoke({"pattern": "needle", "path": "src", "glob": "*.py"}))
        assert "Found 2 file(s)" in files
        assert "src/a.py" in files
        assert "src/c.txt" not in files

        content = str(grep.invoke({"pattern": "needle", "path": "src", "output_mode": "content", "head_limit": 1}))
        assert "src/a.py:1: needle one" in content
        assert "pagination" in content

        count = str(grep.invoke({"pattern": "needle", "path": "src", "output_mode": "count", "case_insensitive": True}))
        assert "src/a.py:1" in count
        assert "src/b.py:2" in count
        assert "Found 4 occurrence(s)" in count

        found = str(find.invoke({"glob": "*.py", "path": "src", "head_limit": 1}))
        assert "src\\a.py" in found or "src/a.py" in found
        assert "pagination" in found
    finally:
        agent.close()


def test_multi_step_prompt_injects_work_note_reminder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    class _FakeStreamAgent:
        def stream(self, payload, stream_mode):  # type: ignore[no-untyped-def]
            captured["messages"] = payload["messages"]
            yield (
                "updates",
                {
                    "final": {
                        "messages": [pi.AIMessage(content="ok")],
                    }
                },
            )

    try:
        monkeypatch.setattr(agent, "_create_agent", lambda tools, system_prompt: _FakeStreamAgent())
        agent.run(session_id="main", prompt="implement feature, add tests, and update docs")
        messages = captured["messages"]
        assert isinstance(messages, list)
        joined = "\n".join(str(item.get("content", "")) for item in messages if isinstance(item, dict))
        assert "work_note_update or plan_note_write" in joined
    finally:
        agent.close()


def test_auto_work_note_update_after_run_and_plan_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch, user_id="alice")

    class _FakeStreamAgent:
        def stream(self, payload, stream_mode):  # type: ignore[no-untyped-def]
            yield (
                "updates",
                {
                    "final": {
                        "messages": [pi.AIMessage(content="<proposed_plan>\n# Goal\nDo it\n</proposed_plan>")],
                    }
                },
            )

    try:
        monkeypatch.setattr(agent, "_create_agent", lambda tools, system_prompt: _FakeStreamAgent())
        agent.run(session_id="main", prompt="plan the implementation", plan_mode="on")
        note = agent._work_note_path("main").read_text(encoding="utf-8")
        assert "# Goal" in note
        assert "Plan mode produced a proposed implementation plan" in note
        assert "Tool calls: 0" in note
    finally:
        agent.close()


def test_memory_store_is_recalled_in_next_run_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch, memory_search_backend="keyword")
    captured: dict[str, object] = {}

    class _FakeStreamAgent:
        def stream(self, payload, stream_mode):  # type: ignore[no-untyped-def]
            captured["messages"] = payload["messages"]
            yield (
                "updates",
                {
                    "final": {
                        "messages": [pi.AIMessage(content="ok")],
                    }
                },
            )

    try:
        store = _tool(agent, "memory_store")
        out = str(
            store.invoke(
                {
                    "content": "User prefers responses in Korean.",
                    "tags": "user_preference,language",
                }
            )
        )
        assert "Stored memory" in out

        monkeypatch.setattr(agent, "_create_agent", lambda tools, system_prompt: _FakeStreamAgent())
        agent.run(session_id="main", prompt="What style should you use?")
        messages = captured["messages"]
        assert isinstance(messages, list)
        joined = "\n".join(str(item.get("content", "")) for item in messages if isinstance(item, dict))
        assert "Relevant memory:" in joined
        assert "User prefers responses in Korean." in joined
        assert "tags=user_preference,language" in joined
    finally:
        agent.close()


def test_memory_store_mirrors_to_flat_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agent = _make_agent(tmp_path, monkeypatch, memory_search_backend="keyword")
    try:
        store = _tool(agent, "memory_store")
        store.invoke({"content": "User prefers concise Korean responses.", "tags": "user_preference,language"})
        flat_rows = agent.memory_store.load("main")
        assert any("concise Korean" in str(row.get("content", "")) for row in flat_rows)
        assert any(row.get("kind") == "preference" for row in flat_rows)
    finally:
        agent.close()
