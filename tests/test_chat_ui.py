from __future__ import annotations

from types import SimpleNamespace

import chat
import pytest


def test_chat_payload_summary_redacts_sensitive_values():
    summary = chat._summarize_payload(
        {
            "path": "report.md",
            "api_key": "secret-value",
            "nested": {"password": "pw"},
            "content": "x" * 500,
        }
    )
    assert "secret-value" not in summary
    assert "pw" not in summary
    assert "<redacted>" in summary
    assert len(summary) <= 523


def test_chat_callbacks_print_turn_stages(capsys):
    callbacks = chat.ColoredChatCallbacks()
    callbacks.start_turn(
        prompt="테스트 요청",
        session_id="test-session",
        skill_mode="auto",
        skill_name=None,
        plan_mode="off",
    )
    callbacks.on_tool_start("read", {"path": "README.md"})
    callbacks.on_tool_end("read", "path=README.md\nhello", is_error=False)
    callbacks.finish_turn(SimpleNamespace(audit_file=None))

    out = capsys.readouterr().out
    assert "[Stage 1/5] Received request" in out
    assert "[Stage 2/5] Preparing context" in out
    assert "[Stage 3/5] Tool #1 started" in out
    assert "[Tool SUCCESS]" in out
    assert "[Stage 5/5] Turn finished" in out


def test_chat_parser_builds_explicit_runtime_config(tmp_path):
    args = chat.build_parser().parse_args(
        [
            "--workspace",
            str(tmp_path),
            "--session",
            "coding-main",
            "--user-id",
            "alice",
            "--skill",
            "naru-python-coding-guide",
            "--plan-mode",
            "on",
            "--mode",
            "edit",
            "--edit-path",
            "chat.py",
            "--no-mcp",
            "--no-write",
            "--max-model-calls",
            "9",
        ]
    )
    config = chat.build_config(args)

    assert config.workspace_path() == tmp_path.resolve()
    assert args.session == "coding-main"
    assert config.user_id == "alice"
    assert config.skill_name == "naru-python-coding-guide"
    assert config.plan_mode == "on"
    assert args.mode == "edit"
    assert args.edit_path == ["chat.py"]
    assert config.mcp_enabled is False
    assert config.allow_write is False
    assert config.max_model_calls == 9


def test_chat_prompt_file_is_utf8_and_obeys_blocked_paths(tmp_path):
    prompt_path = tmp_path / "prompt.ko.txt"
    prompt_path.write_text("코드를 분석해줘", encoding="utf-8")
    blocked = tmp_path / ".env"
    blocked.write_text("SECRET=value", encoding="utf-8")
    args = chat.build_parser().parse_args(["--workspace", str(tmp_path)])
    config = chat.build_config(args)

    assert chat._read_prompt_file(config, "prompt.ko.txt") == "코드를 분석해줘"
    with pytest.raises(ValueError, match="blocked path by policy"):
        chat._read_prompt_file(config, ".env")


def test_chat_rejects_console_input_with_surrogate_escapes():
    with pytest.raises(ValueError, match="UTF-8"):
        chat._validate_console_input("\udce3")


def test_chat_decodes_console_input_as_utf8_or_windows_korean():
    assert chat._decode_console_input(b"hello") == "hello"
    assert chat._decode_console_input("안녕".encode("cp949")) == "안녕"


def test_chat_main_rejects_empty_prompt_file(tmp_path, capsys):
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")

    exit_code = chat.main(
        [
            "--workspace",
            str(tmp_path),
            "--prompt-file",
            "empty.txt",
            "--no-mcp",
        ]
    )

    assert exit_code == 2
    assert "prompt file is empty" in capsys.readouterr().out


def test_chat_check_does_not_require_model_call(tmp_path, monkeypatch):
    closed = {"value": False}

    class _FakeAgent:
        def __init__(self, _config):
            self.all_tools = [SimpleNamespace(name="read"), SimpleNamespace(name="write")]

        def list_skills(self):
            return [{"id": "demo"}]

        def close(self):
            closed["value"] = True

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LOCAL_BEDROCK_BASE_URL", raising=False)
    monkeypatch.setattr(chat, "OpenClawPiLangChain", _FakeAgent)
    args = chat.build_parser().parse_args(["--workspace", str(tmp_path), "--no-mcp"])
    snapshot = chat.run_check(chat.build_config(args), "check-session")

    assert snapshot["status"] == "ok"
    assert snapshot["model_route"] == "offline-check"
    assert snapshot["tools"] == ["read", "write"]
    assert snapshot["skills"] == ["demo"]
    assert closed["value"] is True
    assert "OPENAI_API_KEY" not in chat.os.environ


def test_chat_session_name_normalization_and_one_turn_output(capsys):
    assert chat._normalize_session_id("team alpha/1") == "team_alpha_1"
    with pytest.raises(ValueError):
        chat._normalize_session_id("///")

    class _FakeSession:
        session_id = "chat-test"

        def prompt(self, text):
            assert text == "hello"
            return SimpleNamespace(final_text="answer", audit_file=None)

    callbacks = chat.ColoredChatCallbacks()
    result = chat._execute_turn(
        _FakeSession(),
        callbacks,
        "hello",
        skill_mode="auto",
        skill_name=None,
        plan_mode="off",
    )

    assert result.final_text == "answer"
    assert "Pi:" in capsys.readouterr().out
