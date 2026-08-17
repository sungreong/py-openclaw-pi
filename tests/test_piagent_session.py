from __future__ import annotations

from types import SimpleNamespace

import pytest

import openclaw_pi_langchain as pi


class _DummyModel:
    def invoke(self, _messages):
        return SimpleNamespace(content='{"decision":"allow","reason":""}')


def _make_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(pi, "init_chat_model", lambda *args, **kwargs: _DummyModel())
    return pi.OpenClawPiLangChain(
        pi.PiAgentConfig(
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


def test_session_tracks_state_and_delegates_run(tmp_path, monkeypatch):
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

    monkeypatch.setattr(agent, "_create_agent", lambda tools, system_prompt: _FakeStreamAgent())

    session = pi.PiAgentSession(
        agent,
        session_id="sdk-main",
        skill_mode="off",
        plan_mode="off",
        denylist=["exec"],
    )
    result = session.prompt("hello")

    assert result.session_id == "sdk-main"
    assert result.final_text == "ok"
    assert session.last_result is result
    assert session.state()["denylist"] == ["exec"]
    assert session.state()["has_last_result"] is True
    agent.close()


def test_session_mode_setters(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        session = pi.PiAgentSession(agent, session_id="sdk-main")
        session.set_plan_mode("on")
        session.set_skill("data-report-writer")
        session.set_tool_policy(allowlist=["read"], denylist=["write"])
        session.set_permission_profile("edit", edit_paths=["app.py"])

        assert session.state()["plan_mode"] == "on"
        assert session.state()["skill_name"] == "data-report-writer"
        assert session.state()["skill_mode"] == "manual"
        assert session.state()["allowlist"] == ["read"]
        assert session.state()["denylist"] == ["write"]
        assert session.state()["permission_profile"] == "edit"
        assert session.state()["edit_paths"] == ["app.py"]

        session.set_skill_auto()
        assert session.state()["skill_name"] is None
        assert session.state()["skill_mode"] == "auto"
    finally:
        agent.close()


def test_session_rejects_edit_profile_without_paths(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, monkeypatch)
    try:
        with pytest.raises(ValueError, match="requires at least one edit path"):
            pi.PiAgentSession(agent, permission_profile="edit")
    finally:
        agent.close()


def test_session_follow_up_reuses_saved_conversation_history(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, monkeypatch)
    captured: list[list[dict[str, str]]] = []

    class _HistoryAwareStreamAgent:
        def stream(self, payload, stream_mode):  # type: ignore[no-untyped-def]
            messages = payload["messages"]
            captured.append(messages)
            previous = any(
                isinstance(item, dict) and "SESSION-CODE-17" in str(item.get("content", ""))
                for item in messages[:-1]
            )
            content = "SESSION-CODE-17" if previous else "코드는 SESSION-CODE-17입니다."
            yield ("updates", {"final": {"messages": [pi.AIMessage(content=content)]}})

    monkeypatch.setattr(agent, "_create_agent", lambda tools, system_prompt: _HistoryAwareStreamAgent())
    try:
        session = pi.PiAgentSession(agent, session_id="follow-up-main")
        first = session.prompt("코드는 SESSION-CODE-17입니다.")
        second = session.follow_up("앞선 코드만 답하세요.")

        assert "SESSION-CODE-17" in first.final_text
        assert second.final_text == "SESSION-CODE-17"
        assert len(captured) == 2
        assert any(item.get("role") == "assistant" for item in captured[1] if isinstance(item, dict))
    finally:
        agent.close()


def test_session_context_manager_closes_agent(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, monkeypatch)
    called = {"closed": False}

    def _close():
        called["closed"] = True

    monkeypatch.setattr(agent, "close", _close)
    with pi.PiAgentSession(agent, session_id="sdk-main"):
        pass

    assert called["closed"] is True
