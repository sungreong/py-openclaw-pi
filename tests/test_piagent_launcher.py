from __future__ import annotations

from pathlib import Path


def test_piagent_shell_launcher_dispatches_host_and_container_contexts():
    script = (Path(__file__).resolve().parents[1] / "piagent.sh").read_text(encoding="utf-8")

    assert script.startswith("#!/usr/bin/env sh")
    assert "-f /.dockerenv" in script
    assert "scripts/piagent-host.sh" in script
    assert "scripts/piagent-container.sh" in script


def test_piagent_host_and_container_scripts_have_separate_responsibilities():
    root = Path(__file__).resolve().parents[1]
    host = (root / "scripts" / "piagent-host.sh").read_text(encoding="utf-8")
    container = (root / "scripts" / "piagent-container.sh").read_text(encoding="utf-8")

    assert "docker compose up -d --build pi_agent" in host
    assert "/app/scripts/piagent-container.sh" in host
    assert "mode=\"review\"" in container
    assert "--full" in container
    assert "--edit" in container
    assert "--check" in container
    assert "python chat.py" in container
    assert "docker compose" not in container


def test_piagent_powershell_launcher_exposes_the_same_core_modes():
    script = (Path(__file__).resolve().parents[1] / "piagent.ps1").read_text(encoding="utf-8")

    assert "[string]$Mode = \"review\"" in script
    assert 'ValidateSet("review", "full", "edit")' in script
    assert "[switch]$Check" in script
    assert "docker compose up -d --build pi_agent" in script
    assert '"/app/scripts/piagent-container.sh"' in script
