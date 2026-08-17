from __future__ import annotations

from pathlib import Path


def test_piagent_shell_launcher_exposes_safe_modes_and_docker_entrypoint():
    script = (Path(__file__).resolve().parents[1] / "piagent.sh").read_text(encoding="utf-8")

    assert script.startswith("#!/usr/bin/env sh")
    assert "mode=\"review\"" in script
    assert "--full" in script
    assert "--edit" in script
    assert "--check" in script
    assert "docker compose up -d --build pi_agent" in script
    assert "python chat.py --workspace /app" in script


def test_piagent_powershell_launcher_exposes_the_same_core_modes():
    script = (Path(__file__).resolve().parents[1] / "piagent.ps1").read_text(encoding="utf-8")

    assert "[string]$Mode = \"review\"" in script
    assert 'ValidateSet("review", "full", "edit")' in script
    assert "[switch]$Check" in script
    assert "docker compose up -d --build pi_agent" in script
    assert '"python", "chat.py"' in script
