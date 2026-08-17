from __future__ import annotations

import json
from pathlib import Path


def test_root_does_not_keep_legacy_markdown_loop_or_live_mcp_config():
    root = Path(__file__).resolve().parents[1]

    assert not (root / "markdown_loop.py").exists()
    assert not (root / "mcp_servers.json").exists()


def test_mcp_example_is_tracked_outside_the_live_config_path():
    root = Path(__file__).resolve().parents[1]
    example = root / "examples" / "mcp_servers.example.json"

    config = json.loads(example.read_text(encoding="utf-8"))
    assert config["servers"]
    assert all(server["enabled"] is False for server in config["servers"])
