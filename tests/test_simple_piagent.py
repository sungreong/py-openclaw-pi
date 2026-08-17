from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import piagent
import simple_piagent


class _DummyModel:
    def invoke(self, _messages):
        return SimpleNamespace(content="ok")


def test_simple_config_uses_hash_memory_and_disables_mcp(tmp_path: Path):
    config = simple_piagent.build_config(str(tmp_path))

    assert config.workspace_path() == tmp_path.resolve()
    assert config.memory_search_backend == "hash"
    assert config.memory_embedding_provider == "hash"
    assert config.mcp_enabled is False
    assert config.skills_enabled is True
    assert config.enable_subagents is True
    assert config.workspace_extensions_enabled is False
    assert config.allow_package_install is False


def test_simple_check_lists_core_tools_without_model_call(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("LOCAL_BEDROCK_BASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("LOCAL_BEDROCK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(piagent, "init_chat_model", lambda *args, **kwargs: _DummyModel())

    exit_code = simple_piagent.main(["--check", "--workspace", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["model_route"] == "offline-check"
    assert payload["mcp_enabled"] is False
    assert payload["memory_backend"] == "hash"
    assert {"read", "write", "exec", "web_search", "memory_store", "work_note_update", "delegate_task"} <= set(
        payload["tools"]
    )
    assert "OPENAI_API_KEY" not in os.environ


def test_simple_check_can_opt_into_workspace_tool_folder(tmp_path: Path, monkeypatch, capsys):
    tool_dir = tmp_path / ".piagent" / "tools" / "sample"
    tool_dir.mkdir(parents=True)
    (tool_dir / "tool.py").write_text(
        "from langchain.tools import tool\n"
        "@tool('sample_lookup')\n"
        "def sample_lookup(query: str) -> str:\n"
        "    \"\"\"Return a fixed sample result.\"\"\"\n"
        "    return query\n"
        "TOOLS = [sample_lookup]\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("LOCAL_BEDROCK_BASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("LOCAL_BEDROCK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(piagent, "init_chat_model", lambda *args, **kwargs: _DummyModel())

    exit_code = simple_piagent.main(
        ["--check", "--workspace", str(tmp_path), "--workspace-extensions"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["workspace_extensions_enabled"] is True
    assert "sample_lookup" in payload["tools"]


def test_read_prompt_file_preserves_utf8_korean(tmp_path: Path):
    prompt_file = tmp_path / "request.txt"
    prompt_file.write_text("베트남 최신 뉴스를 분석해줘", encoding="utf-8")
    config = simple_piagent.build_config(str(tmp_path))

    assert simple_piagent.read_prompt_file(config, "request.txt") == "베트남 최신 뉴스를 분석해줘"


def test_read_prompt_file_obeys_workspace_block_policy(tmp_path: Path):
    blocked = tmp_path / ".env"
    blocked.write_text("not-a-real-secret", encoding="utf-8")
    config = simple_piagent.build_config(str(tmp_path))

    try:
        simple_piagent.read_prompt_file(config, ".env")
    except ValueError as exc:
        assert "blocked path" in str(exc)
    else:
        raise AssertionError("blocked prompt path was accepted")


def test_simple_cli_exposes_prompt_file_skill_and_call_limit():
    parser_source = Path(simple_piagent.__file__).read_text(encoding="utf-8")

    assert '"--prompt-file"' in parser_source
    assert '"--mode"' in parser_source
    assert '"--edit-path"' in parser_source
    assert '"--skill"' in parser_source
    assert '"--workspace-extensions"' in parser_source
    assert '"--max-model-calls"' in parser_source
