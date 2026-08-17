from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import piagent_subagent_chat as subagent_chat
from scripts import run_agent_multiturn_10 as multiturn_eval


def test_multiturn_spec_has_10_ordered_scenarios_and_real_followups() -> None:
    spec = multiturn_eval._load_spec()
    scenarios = spec["scenarios"]

    assert [item["id"] for item in scenarios] == [f"M{number:02d}" for number in range(1, 11)]
    assert [item["level"] for item in scenarios] == sorted(item["level"] for item in scenarios)
    assert all(len(item["turns"]) >= 2 for item in scenarios)
    assert sum(len(item["turns"]) for item in scenarios) == 37
    assert len(scenarios[-1]["turns"]) == 10


def test_multiturn_selection_supports_ranges_and_ids() -> None:
    assert multiturn_eval._parse_selection("1-3,M05,10") == ["M01", "M02", "M03", "M05", "M10"]


def test_multiturn_seed_workspace_is_deterministic(tmp_path: Path) -> None:
    workspace = multiturn_eval._seed_workspace(tmp_path)

    assert "ATLAS-900" in (workspace / "multiturn" / "project.md").read_text(encoding="utf-8")
    assert "DELTA-88" in (workspace / "multiturn" / "subagent.txt").read_text(encoding="utf-8")


def test_custom_multiturn_checks_cover_correction_wait_and_compaction() -> None:
    result = SimpleNamespace(
        final_text="DB=SQLite",
        awaiting_user_input=True,
        tool_calls=[],
        tool_results=[],
        audit_file=None,
    )
    history = [
        {"role": "system", "content": "Conversation summary for continuation: prior facts"},
        {"role": "assistant", "content": "DB=SQLite"},
    ]

    assert multiturn_eval._check(
        {"type": "final_not_contains", "value": "PostgreSQL"}, result=result, history=history
    )["passed"]
    assert multiturn_eval._check(
        {"type": "awaiting_user_input", "value": True}, result=result, history=history
    )["passed"]
    assert multiturn_eval._check(
        {"type": "history_has_summary", "value": True}, result=result, history=history
    )["passed"]


class _FakeSession:
    session_id = "unit-session"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def prompt(self, text: str, **kwargs):
        self.calls.append(("prompt", text, kwargs))
        return _fake_result("첫 응답")

    def follow_up(self, text: str, **kwargs):
        self.calls.append(("follow_up", text, kwargs))
        return _fake_result("후속 응답")

    def state(self) -> dict[str, object]:
        return {"session_id": self.session_id, "has_last_result": bool(self.calls)}


def _fake_result(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        session_id="unit-session",
        final_text=text,
        tool_calls=[],
        tool_results=[],
        awaiting_user_input=False,
        user_question=None,
        audit_file=None,
    )


def test_subagent_jsonl_bridge_keeps_one_session_and_uses_follow_up() -> None:
    session = _FakeSession()
    source = io.StringIO(
        '{"prompt":"첫 질문","allowlist":["read"],"plan_mode":"on"}\n'
        '{"prompt":"후속 질문"}\n'
        '{"command":"state"}\n'
        '{"command":"exit"}\n'
    )
    sink = io.StringIO()

    assert subagent_chat.serve_jsonl(session, source, sink) == 0

    rows = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert [row["type"] for row in rows] == ["ready", "reply", "reply", "state", "closed"]
    assert session.calls == [
        ("prompt", "첫 질문", {"allowlist": ["read"], "plan_mode": "on"}),
        ("follow_up", "후속 질문", {}),
    ]
    assert rows[2]["text"] == "후속 응답"
    assert rows[-1]["turns"] == 2


def test_subagent_jsonl_bridge_accepts_simple_mode_and_paths() -> None:
    session = _FakeSession()
    source = io.StringIO(
        '{"prompt":"부분 수정","mode":"edit","paths":["src/app.py"]}\n'
        '{"command":"exit"}\n'
    )
    sink = io.StringIO()

    assert subagent_chat.serve_jsonl(session, source, sink) == 0
    assert session.calls == [
        (
            "prompt",
            "부분 수정",
            {"permission_profile": "edit", "edit_paths": ["src/app.py"]},
        )
    ]


def test_subagent_cli_edit_mode_requires_path_before_agent_start() -> None:
    with pytest.raises(SystemExit, match="--mode edit requires"):
        subagent_chat.main(["--mode", "edit", "--prompt", "change"])


def test_subagent_cli_rejects_unsafe_session_before_agent_start() -> None:
    with pytest.raises(SystemExit, match="session must match"):
        subagent_chat.main(["--session", "../outside", "--prompt", "hello"])
