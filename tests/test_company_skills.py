from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import openclaw_pi_langchain as pi


REPO_ROOT = Path(__file__).resolve().parents[1]


class _DummyModel:
    def invoke(self, _messages):
        return SimpleNamespace(content='{"decision":"allow","reason":""}')


def _agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> pi.OpenClawPiLangChain:
    monkeypatch.setattr(pi, "init_chat_model", lambda *args, **kwargs: _DummyModel())
    return pi.OpenClawPiLangChain(
        pi.PiAgentConfig(
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
    )


def test_company_skills_are_discovered_and_auto_selected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    agent = _agent(tmp_path, monkeypatch)
    try:
        assert {
            "naru-git-workflow",
            "naru-ui-design-review",
            "naru-python-coding-guide",
        }.issubset(agent.skills_by_id)

        git_skill = agent._select_skill(
            prompt="NaruWorks Git 규칙으로 브랜치명과 PR 체크리스트를 작성해줘.",
            skill_name=None,
            skill_mode="auto",
            session_id="company-git-auto",
        )
        ui_skill = agent._select_skill(
            prompt="NaruWorks UI review로 회사 디자인 규칙 위반을 점검해줘.",
            skill_name=None,
            skill_mode="auto",
            session_id="company-ui-auto",
        )
        coding_skill = agent._select_skill(
            prompt="회사 코딩 가이드에 따라 이 코드를 리뷰해줘.",
            skill_name=None,
            skill_mode="auto",
            session_id="company-python-auto",
        )

        assert git_skill is not None and git_skill.id == "naru-git-workflow"
        assert ui_skill is not None and ui_skill.id == "naru-ui-design-review"
        assert coding_skill is not None and coding_skill.id == "naru-python-coding-guide"
        assert "references/git-policy.md" in git_skill.workflow
        assert "references/ui-policy.md" in ui_skill.workflow
        assert "dependency changes" in git_skill.workflow
        assert "Use `unknown` for other unverified facts" in git_skill.workflow
        assert "do not call it user-blaming" in ui_skill.workflow
        assert "exactly `not run`" in git_skill.workflow
        assert "default severity" in ui_skill.workflow
        assert "references/python-coding-guide.md" in coding_skill.workflow
        assert "exactly `not run`" in coding_skill.workflow
        assert "Do not end an implementation turn immediately after writing files" in coding_skill.workflow
        assert "Do not call `find`, `grep`, or `ls`" in coding_skill.workflow
    finally:
        agent.close()


def test_company_policy_references_and_ui_fixture_keep_evaluation_markers():
    git_policy = (REPO_ROOT / "skills/naru-git-workflow/references/git-policy.md").read_text(encoding="utf-8")
    ui_policy = (REPO_ROOT / "skills/naru-ui-design-review/references/ui-policy.md").read_text(encoding="utf-8")
    fixture = (REPO_ROOT / "tests/fixtures/naru-dashboard.html").read_text(encoding="utf-8")

    assert "type/NW-####-kebab-summary" in git_policy
    assert "never `hotfix`" in git_policy
    assert "one reviewer outside the author’s team" in git_policy
    assert "#1457D9" in ui_policy
    assert "minimum 44px" in ui_policy
    assert "3px `#7AA7FF`" in ui_policy
    assert "phrase alone is not evidence of user blame" in ui_policy
    assert "| UI-01 | P2 |" in ui_policy
    assert "| UI-05 | P1 |" in ui_policy

    assert "#6C5CE7" in fixture
    assert "padding: 13px" in fixture
    assert "border-radius: 20px" in fixture
    assert "height: 32px" in fixture
    assert "outline: none" in fixture
    assert "실패했습니다" in fixture


def test_python_coding_guide_keeps_policy_and_fixture_markers():
    policy = (REPO_ROOT / "skills/naru-python-coding-guide/references/python-coding-guide.md").read_text(
        encoding="utf-8"
    )
    fixture = (REPO_ROOT / "tests/fixtures/naru_python_legacy.py").read_text(encoding="utf-8")

    assert "@dataclass(frozen=True)" in policy
    assert "NRU_<AREA>_<REASON>" in policy
    assert "clock: Callable[[], datetime]" in policy
    assert 'extra={"event": "naru.<area>.<verb>"}' in policy
    assert "test_<unit>__when_<condition>__then_<outcome>" in policy
    assert "# guide-exception: PY-XX" in policy

    assert "items: list[str] = []" in fixture
    assert "datetime.now()" in fixture
    assert 'api_key={api_key}' in fixture
    assert "except Exception" in fixture
    assert "return None" in fixture
    assert "time.sleep(1)" in fixture
