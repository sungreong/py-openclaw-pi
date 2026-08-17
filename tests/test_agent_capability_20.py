from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from scripts import run_agent_capability_20 as capability_eval
from piagent.agent_registry import AgentRegistryMixin


def test_capability_spec_has_20_ordered_tasks_and_valid_dependencies() -> None:
    spec = capability_eval._load_spec()
    tasks = spec["tasks"]

    assert [task["id"] for task in tasks] == [f"T{number:02d}" for number in range(1, 21)]
    assert [task["level"] for task in tasks] == sorted(task["level"] for task in tasks)
    positions = {task["id"]: index for index, task in enumerate(tasks)}
    for task in tasks:
        for dependency in task.get("depends_on", []):
            assert positions[dependency] < positions[task["id"]]
        assert task["prompt"].strip()
        assert task["checks"]


def test_parse_task_selection_supports_ranges_and_ids() -> None:
    all_ids = [f"T{number:02d}" for number in range(1, 21)]

    assert capability_eval._parse_task_selection("1-3,T05,5,20", all_ids) == [
        "T01",
        "T02",
        "T03",
        "T05",
        "T20",
    ]


def test_session_context_supports_fresh_rerun_suffix() -> None:
    tasks = [{"session": "shared-session"}, {"session": "news-research"}]
    context = capability_eval._session_context("run", "work", tasks, session_suffix="fix2")

    assert context["session_shared_session"] == "eval20-run-shared-session-fix2"
    assert context["session_news_research"] == "eval20-run-news-research-fix2"
    assert context["shared_session"] == "eval20-run-shared-session"
    assert context["news_session"] == "eval20-run-news-research"


def test_seed_workspace_creates_deterministic_fixtures_without_overwrite(tmp_path: Path) -> None:
    workspace = capability_eval._seed_workspace(tmp_path)
    brief = workspace / "brief.md"
    assert "ORBIT-42" in brief.read_text(encoding="utf-8")
    assert "requested >= stock" in (workspace / "bugfix" / "calculator.py").read_text(encoding="utf-8")

    brief.write_text("preserved", encoding="utf-8")
    capability_eval._seed_workspace(tmp_path)
    assert brief.read_text(encoding="utf-8") == "preserved"


def test_evaluate_checks_tools_text_and_docx(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(capability_eval, "ROOT", tmp_path)
    report = tmp_path / "out" / "report.md"
    report.parent.mkdir(parents=True)
    report.write_text("Busan 60 RED 제한", encoding="utf-8")
    docx = tmp_path / "out" / "report.docx"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("word/document.xml", "<document />")

    task = {
        "checks": [
            {"type": "required_tools_all", "values": ["read", "write"]},
            {"type": "artifact_text_contains", "path": "out/report.md", "values": ["60", "RED"]},
            {"type": "artifact_docx", "path": "out/report.docx", "min_bytes": 1},
            {"type": "no_tool_errors"},
        ]
    }
    tool_calls = [{"name": "read"}, {"name": "write"}]
    tool_results = [
        {"name": "read", "content": "ok", "is_error": False},
        {"name": "write", "content": "ok", "is_error": False},
    ]

    checks, score, status = capability_eval._evaluate(task, "done", tool_calls, tool_results, [])

    assert all(row["passed"] for row in checks)
    assert score == 5.0
    assert status == "pass"


def test_write_summary_keeps_full_task_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(capability_eval, "ROOT", tmp_path)
    task_dir = tmp_path / "tasks"
    task_dir.mkdir()
    record = {
        "id": "T01",
        "level": 1,
        "title": "기초 산술",
        "question": "질문",
        "status": "pass",
        "score": 5.0,
        "tool_calls": [],
        "tool_names": [],
        "tool_error_count": 0,
        "elapsed_seconds": 1.2,
        "audit_file": "audit.jsonl",
        "checks": [{"passed": True, "label": "정답"}],
        "final_text": "정답: 396",
    }
    (task_dir / "T01.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    records = capability_eval._write_summary(tmp_path, "unit", "local-bedrock", "model")

    assert len(records) == 1
    assert json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))["score"] == 5.0
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "정답: 396" in summary
    assert "5.00/5" in summary


def test_data_report_skill_allows_every_required_tool() -> None:
    text = (capability_eval.ROOT / "skills" / "data-report-writer" / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]
    required_block = frontmatter.split("required_tools:", 1)[1].split("tool_allow:", 1)[0]
    allow_block = frontmatter.split("tool_allow:", 1)[1].split("tool_deny:", 1)[0]
    required = {line.removeprefix("-").strip() for line in required_block.splitlines() if line.strip().startswith("-")}
    allowed = {line.removeprefix("-").strip() for line in allow_block.splitlines() if line.strip().startswith("-")}

    assert required <= allowed
    assert "do not preflight a new output directory with `ls`" in text


def test_auto_skill_conflict_detects_an_explicit_tool_hidden_by_skill_policy() -> None:
    registry = AgentRegistryMixin()
    base_tools = [SimpleNamespace(name="read"), SimpleNamespace(name="word_report_create")]
    skill_tools = [SimpleNamespace(name="read")]

    assert registry._auto_skill_tool_conflicts(
        "Use word_report_create to create the requested DOCX.",
        base_tools,
        skill_tools,
    ) == ["word_report_create"]
    assert registry._auto_skill_tool_conflicts(
        "Use word_report_create_extra only.",
        base_tools,
        skill_tools,
    ) == []
