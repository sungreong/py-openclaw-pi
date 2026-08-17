from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import PurePosixPath
from typing import Any

from langchain.tools import tool


_DEFAULT_ENDPOINT = "http://127.0.0.1:8811/mcp"
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "host.docker.internal"}
_MAX_RESPONSE_BYTES = 3_000_000
_PROTOCOL_VERSION = "2025-03-26"


def _endpoint() -> str:
    value = os.getenv("PI_MARKDOWN_SEARCH_MCP_URL", _DEFAULT_ENDPOINT).strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Markdown MCP URL must use http or https")
    if (parsed.hostname or "").lower() not in _ALLOWED_HOSTS:
        raise ValueError("Markdown MCP URL must use an approved local host")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Markdown MCP URL must not contain credentials or a fragment")
    return value


def _decode_response(content_type: str, body: bytes) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace").strip()
    if "text/event-stream" in str(content_type or "").lower():
        data_lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        if not data_lines:
            raise ValueError("MCP server returned an empty event stream")
        text = data_lines[-1]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("MCP server response must be a JSON object")
    return value


def _post(payload: dict[str, Any], session_id: str = "", timeout_s: int = 20) -> tuple[dict[str, Any], str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "PiAgent/1.0 (+tool:markdown-search-mcp)",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(
        _endpoint(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1, min(60, int(timeout_s)))) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise ValueError("MCP response exceeds the 3 MB safety limit")
            next_session = str(response.headers.get("Mcp-Session-Id", "") or session_id)
            if response.status == 202 and not raw:
                return {}, next_session
            return _decode_response(str(response.headers.get("Content-Type", "")), raw), next_session
    except urllib.error.HTTPError as exc:
        detail = exc.read(600).decode("utf-8", errors="replace").strip()
        raise ValueError(f"MCP HTTP {exc.code}: {detail}") from exc


def _result_payload(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("error"):
        raise ValueError(f"MCP JSON-RPC error: {response['error']}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise ValueError("MCP response has no result object")
    if result.get("isError"):
        raise ValueError(f"MCP tool returned an error: {result.get('content', [])}")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for item in result.get("content", []):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = str(item.get("text", "")).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        if isinstance(parsed, dict):
            return parsed
    return {"content": result.get("content", [])}


def _call_mcp(remote_tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    initialize, session_id = _post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "piagent-markdown-search", "version": "1.0"},
            },
        }
    )
    if initialize.get("error"):
        raise ValueError(f"MCP initialize failed: {initialize['error']}")
    if not session_id:
        raise ValueError("MCP server did not return a session ID")
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id)
    response, _ = _post(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": remote_tool, "arguments": arguments},
        },
        session_id,
    )
    return _result_payload(response)


def _safe_relative_markdown_path(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".md":
        raise ValueError("relative_path must be a safe relative Markdown path returned by search")
    return path.as_posix()


@tool("markdown_mcp_search")
def markdown_mcp_search(
    query: str,
    root_id: str = "workspace",
    limit: int = 5,
    sort_by: str = "relevance",
) -> str:
    """Search the configured read-only Markdown MCP index and return bounded result excerpts."""
    clean_query = " ".join(str(query or "").split())[:400]
    clean_root = str(root_id or "workspace").strip()[:100]
    clean_sort = str(sort_by or "relevance").strip().lower()
    if not clean_query:
        return json.dumps({"status": "error", "error": "query is required"})
    if clean_sort not in {"relevance", "recent"}:
        return json.dumps({"status": "error", "error": "sort_by must be relevance or recent"})
    try:
        result = _call_mcp(
            "search_markdown",
            {
                "query": clean_query,
                "root_id": clean_root,
                "limit": max(1, min(10, int(limit))),
                "sort_by": clean_sort,
                "excerpt_chars": 700,
            },
        )
        return json.dumps({"status": "ok", **result}, ensure_ascii=False)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return json.dumps({"status": "error", "error": str(exc)[:1000]}, ensure_ascii=False)


@tool("markdown_mcp_read")
def markdown_mcp_read(
    relative_path: str,
    root_id: str = "workspace",
    start_line: int = 1,
    max_lines: int = 80,
) -> str:
    """Read a bounded line range from a Markdown path returned by markdown_mcp_search."""
    try:
        result = _call_mcp(
            "read_markdown",
            {
                "root_id": str(root_id or "workspace").strip()[:100],
                "relative_path": _safe_relative_markdown_path(relative_path),
                "start_line": max(1, int(start_line)),
                "max_lines": max(1, min(200, int(max_lines))),
            },
        )
        return json.dumps({"status": "ok", **result}, ensure_ascii=False)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return json.dumps({"status": "error", "error": str(exc)[:1000]}, ensure_ascii=False)


TOOLS = [markdown_mcp_search, markdown_mcp_read]
