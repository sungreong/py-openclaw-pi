from __future__ import annotations

from pathlib import Path


def test_current_docs_do_not_refer_to_removed_root_entrypoints():
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "README.kr.md",
        root / "docs" / "MARKDOWN_LOOP_ENGINEERING_REPORT.kr.md",
    ]

    for path in paths:
        assert "python markdown_loop.py" not in path.read_text(encoding="utf-8")


def test_documentation_uses_the_current_mcp_template_path():
    root = Path(__file__).resolve().parents[1]
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    config = (root / "docs" / "CONFIGURATION.kr.md").read_text(encoding="utf-8")

    assert "examples/mcp_servers.example.json" in agents
    assert "examples/mcp_servers.example.json" in config
