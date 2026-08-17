from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import openclaw_pi_langchain as pi


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "code-health-check"
SCANNER = SKILL_ROOT / "scripts" / "project_health.py"


class _DummyModel:
    def invoke(self, _messages):
        return SimpleNamespace(content='{"decision":"allow","reason":""}')


def test_code_health_scanner_reports_fixture_and_ignores_private_state(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".piagent" / "packages").mkdir(parents=True)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src" / "app.py").write_text("# TODO: validate\nprint('ok')\n", encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text("# FIXME: assertion\ndef test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=must-not-scan\n", encoding="utf-8")
    (tmp_path / ".agents" / "hidden.py").write_text("# TODO hidden\n", encoding="utf-8")
    (tmp_path / ".piagent" / "packages" / "dep.py").write_text("# TODO dependency\n", encoding="utf-8")
    (tmp_path / "node_modules" / "package.js").write_text("// TODO dependency\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(SCANNER), "--path", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["scanned_file_count"] == 2
    assert payload["source_file_count"] == 2
    assert payload["non_test_source_file_count"] == 1
    assert payload["test_file_count"] == 1
    assert payload["non_test_source_lines"] == 2
    assert payload["test_lines"] == 3
    assert payload["test_to_non_test_line_percent"] == 150.0
    assert payload["todo_fixme_count"] == 2
    assert payload["languages"] == {"Python": {"files": 2, "lines": 5}}
    assert {row["path"] for row in payload["largest_source_files"]} == {
        "src/app.py",
        "tests/test_app.py",
    }


def test_code_health_skill_is_discovered_and_auto_selected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(pi, "init_chat_model", lambda *args, **kwargs: _DummyModel())
    config = pi.PiAgentConfig(
        model="dummy",
        workspace_dir=str(REPO_ROOT),
        session_dir=str(tmp_path / ".sessions"),
        audit_dir=str(tmp_path / ".audit"),
        memory_dir=str(tmp_path / ".memory"),
        memory_embedding_provider="hash",
        mcp_enabled=False,
        skills_enabled=True,
        skills_dir="skills",
        hooks_config_path=str(tmp_path / "pi_hooks.json"),
    )
    agent = pi.OpenClawPiLangChain(config)
    try:
        assert "code-health-check" in agent.skills_by_id
        skill = agent._select_skill(
            prompt="Run a code health check and save the project health report.",
            skill_name=None,
            skill_mode="auto",
            session_id="skill-auto",
        )
        assert skill is not None
        assert skill.id == "code-health-check"
        assert "project_health.py" in skill.workflow
        assert "Evidence Sample Mode" in skill.workflow
        assert "`exec`: run the bundled scanner" in skill.workflow
        assert "`read`: read that exact path" in skill.workflow
        assert "`grep`: search that same file" in skill.workflow
        assert "`write`: create the Markdown report" in skill.workflow
        assert "`ls`: verify the report path" in skill.workflow
        assert Path(skill.source_path) == SKILL_ROOT / "SKILL.md"
        audit = agent.audit_logger.path_for("skill-auto").read_text(encoding="utf-8")
        assert '"skill_selected"' in audit
        assert '"mode": "auto"' in audit
    finally:
        agent.close()
