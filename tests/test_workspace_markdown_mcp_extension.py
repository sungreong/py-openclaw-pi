from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / ".piagent" / "tools" / "markdown-search" / "tool.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("piagent_markdown_mcp_test", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_endpoint_accepts_only_approved_local_hosts(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("PI_MARKDOWN_SEARCH_MCP_URL", "http://host.docker.internal:8811/mcp")
    assert module._endpoint() == "http://host.docker.internal:8811/mcp"

    monkeypatch.setenv("PI_MARKDOWN_SEARCH_MCP_URL", "https://example.test/mcp")
    with pytest.raises(ValueError, match="approved local host"):
        module._endpoint()


def test_markdown_search_calls_only_bounded_remote_contract(monkeypatch):
    module = _load_module()
    captured = {}

    def fake_call(name, arguments):
        captured.update(name=name, arguments=arguments)
        return {"query": arguments["query"], "results": [{"relative_path": "wiki/a.md"}]}

    monkeypatch.setattr(module, "_call_mcp", fake_call)
    payload = json.loads(
        module.markdown_mcp_search.invoke(
            {"query": "  agent   runtime  ", "limit": 999, "sort_by": "relevance"}
        )
    )

    assert payload["status"] == "ok"
    assert captured["name"] == "search_markdown"
    assert captured["arguments"]["query"] == "agent runtime"
    assert captured["arguments"]["limit"] == 10
    assert captured["arguments"]["excerpt_chars"] == 700


def test_markdown_read_requires_safe_search_result_path(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "_call_mcp", lambda *_args, **_kwargs: {"content": "ok"})

    valid = json.loads(module.markdown_mcp_read.invoke({"relative_path": "wiki/concepts/agent.md"}))
    escaped = json.loads(module.markdown_mcp_read.invoke({"relative_path": "../private.md"}))
    wrong_type = json.loads(module.markdown_mcp_read.invoke({"relative_path": "wiki/data.json"}))

    assert valid == {"status": "ok", "content": "ok"}
    assert escaped["status"] == "error"
    assert wrong_type["status"] == "error"


def test_mcp_result_prefers_structured_content():
    module = _load_module()
    payload = module._result_payload(
        {
            "result": {
                "isError": False,
                "structuredContent": {"results": [{"relative_path": "wiki/a.md"}]},
                "content": [{"type": "text", "text": "ignored"}],
            }
        }
    )

    assert payload == {"results": [{"relative_path": "wiki/a.md"}]}
