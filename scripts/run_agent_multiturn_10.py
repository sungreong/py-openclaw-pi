from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from piagent import NullCallbacks, OpenClawPiLangChain, PiAgentSession  # noqa: E402
from scripts import run_agent_capability_20 as capability_eval  # noqa: E402


SPEC_PATH = ROOT / "evaluation" / "agent_multiturn_10_scenarios.json"
RUNS_ROOT = ROOT / "artifacts" / "agent-multiturn-10" / "runs"


class MultiTurnCallbacks(NullCallbacks):
    def __init__(self, scenario_id: str, turn: int) -> None:
        self.label = f"{scenario_id}/turn-{turn:02d}"

    def on_tool_start(self, tool_name: str, args: dict[str, Any]) -> None:
        del args
        print(f"[{self.label}] tool:start {tool_name}", flush=True)

    def on_tool_end(self, tool_name: str, output: str, is_error: bool) -> None:
        del output
        print(f"[{self.label}] tool:end   {tool_name} [{'error' if is_error else 'ok'}]", flush=True)


def _load_spec() -> dict[str, Any]:
    data = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 10:
        raise ValueError("multi-turn evaluation spec must contain exactly 10 scenarios")
    expected_ids = [f"M{number:02d}" for number in range(1, 11)]
    if [str(item.get("id", "")) for item in scenarios] != expected_ids:
        raise ValueError("scenario IDs must be ordered M01 through M10")
    if [int(item.get("level", 0)) for item in scenarios] != sorted(int(item.get("level", 0)) for item in scenarios):
        raise ValueError("scenario levels must be ordered")
    for scenario in scenarios:
        turns = scenario.get("turns")
        if not isinstance(turns, list) or len(turns) < 2:
            raise ValueError(f"{scenario['id']} must contain at least two turns")
        for turn in turns:
            if not str(turn.get("prompt", "")).strip() or not turn.get("checks"):
                raise ValueError(f"{scenario['id']} has an invalid turn")
    return data


def _parse_selection(raw: str) -> list[str]:
    all_ids = [f"M{number:02d}" for number in range(1, 11)]
    if not str(raw or "").strip():
        return all_ids
    selected: list[str] = []
    for token in re.split(r"[\s,]+", raw.strip()):
        match = re.fullmatch(r"(\d{1,2})-(\d{1,2})", token)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            for number in range(min(start, end), max(start, end) + 1):
                value = f"M{number:02d}"
                if value in all_ids and value not in selected:
                    selected.append(value)
            continue
        value = f"M{int(token):02d}" if token.isdigit() else token.upper()
        if value not in all_ids:
            raise ValueError(f"unknown scenario: {token}")
        if value not in selected:
            selected.append(value)
    return selected


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_workspace(run_root: Path) -> Path:
    workspace = run_root / "workspace"
    _write_text(
        workspace / "multiturn" / "project.md",
        "# Project\n\n- code: ATLAS-900\n- owner: Blue Team\n- budget: 900\n",
    )
    _write_text(
        workspace / "multiturn" / "release.md",
        "# Release\n\nProduction deployment requires a smoke test and a rollback path.\n",
    )
    _write_text(workspace / "multiturn" / "subagent.txt", "marker=DELTA-88\nowner=research\n")
    return workspace


def _format(value: Any, context: dict[str, str]) -> Any:
    return capability_eval._format_value(value, context)


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[‐‑‒–—−]", "-", text).casefold()


def _check(
    check: dict[str, Any],
    *,
    result: Any,
    history: list[dict[str, str]],
) -> dict[str, Any]:
    kind = str(check.get("type", ""))
    final_text = str(result.final_text)
    normalized_final = _normalize_text(final_text)
    if kind == "final_contains":
        value = str(check.get("value", ""))
        return {
            "passed": _normalize_text(value) in normalized_final,
            "label": f"final contains {value!r}",
            "evidence": {},
        }
    if kind == "final_contains_all":
        values = [str(item) for item in check.get("values", [])]
        missing = [item for item in values if _normalize_text(item) not in normalized_final]
        return {"passed": not missing, "label": "final contains all required values", "evidence": {"missing": missing}}
    if kind == "final_not_contains":
        value = str(check.get("value", ""))
        return {
            "passed": _normalize_text(value) not in normalized_final,
            "label": f"final omits {value!r}",
            "evidence": {},
        }
    if kind == "awaiting_user_input":
        expected = bool(check.get("value"))
        actual = bool(result.awaiting_user_input)
        return {"passed": actual == expected, "label": f"awaiting_user_input is {expected}", "evidence": {"actual": actual}}
    if kind == "history_has_summary":
        expected = bool(check.get("value"))
        actual = any(
            item.get("role") == "system"
            and str(item.get("content", "")).startswith("Conversation summary for continuation:")
            for item in history
        )
        return {"passed": actual == expected, "label": f"compacted history summary is present", "evidence": {"actual": actual}}
    passed, label, evidence = capability_eval._check_result(
        check,
        final_text=final_text,
        tool_calls=result.tool_calls,
        tool_results=result.tool_results,
        audit_events=capability_eval._read_audit_events(result.audit_file),
    )
    return {"passed": passed, "label": label, "evidence": evidence}


def _run_scenario(
    scenario: dict[str, Any],
    *,
    run_root: Path,
    context: dict[str, str],
    route: str,
    model_id: str,
) -> dict[str, Any]:
    expanded = _format(scenario, context)
    scenario_id = str(expanded["id"])
    session_id = f"mt10-{run_root.name}-{scenario_id.lower()}"
    config = capability_eval._build_config(run_root, max_model_calls=8)
    config.compact_after_messages = int(expanded.get("compact_after_messages", config.compact_after_messages))
    config.keep_last_messages = int(expanded.get("keep_last_messages", config.keep_last_messages))
    agent = OpenClawPiLangChain(config)
    session = PiAgentSession(
        agent,
        session_id=session_id,
        callbacks=NullCallbacks(),
        skill_mode="off",
        allowlist=expanded.get("allowlist"),
    )
    started = time.monotonic()
    turn_records: list[dict[str, Any]] = []
    error = ""
    try:
        for index, turn in enumerate(expanded["turns"], start=1):
            if "plan_mode" in turn:
                session.set_plan_mode(str(turn["plan_mode"]))
            print(f"\n[{scenario_id}] turn {index}/{len(expanded['turns'])}: {str(turn['prompt'])[:120]}", flush=True)
            callbacks = MultiTurnCallbacks(scenario_id, index)
            turn_started = time.monotonic()
            turn_allowlist = turn.get("allowlist")
            result = session.prompt(
                str(turn["prompt"]), callbacks=callbacks, allowlist=turn_allowlist
            ) if index == 1 else session.follow_up(
                str(turn["prompt"]), callbacks=callbacks, allowlist=turn_allowlist
            )
            history = agent.session_store.load(session_id)
            checks = [_check(check, result=result, history=history) for check in turn["checks"]]
            checks.append(
                {
                    "passed": not any(item.get("is_error") for item in result.tool_results),
                    "label": "tool results contain no errors",
                    "evidence": {},
                }
            )
            turn_records.append(
                {
                    "turn": index,
                    "prompt": turn["prompt"],
                    "final_text": result.final_text,
                    "elapsed_seconds": round(time.monotonic() - turn_started, 3),
                    "awaiting_user_input": result.awaiting_user_input,
                    "user_question": result.user_question,
                    "tool_calls": result.tool_calls,
                    "tool_results": result.tool_results,
                    "tool_names": [str(item.get("name", "")) for item in result.tool_calls],
                    "checks": checks,
                }
            )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        history = agent.session_store.load(session_id)
        session.close()

    all_checks = [check for turn in turn_records for check in turn["checks"]]
    passed = sum(1 for check in all_checks if check["passed"])
    ratio = passed / len(all_checks) if all_checks and not error else 0.0
    score = round(10.0 * ratio, 2)
    status = "pass" if ratio == 1.0 else ("partial" if ratio >= 0.5 else "fail")
    return {
        "id": scenario_id,
        "level": expanded["level"],
        "title": expanded["title"],
        "session_id": session_id,
        "model_route": route,
        "model_id": model_id,
        "status": status,
        "score": score,
        "max_score": 10.0,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "error": error,
        "turn_count": len(turn_records),
        "turns": turn_records,
        "stored_history": history,
    }


def _write_results(run_root: Path, run_id: str, route: str, model_id: str, records: list[dict[str, Any]]) -> None:
    score = round(sum(float(item["score"]) for item in records), 2)
    passed = sum(1 for item in records if item["status"] == "pass")
    payload = {
        "run_id": run_id,
        "model_route": route,
        "model_id": model_id,
        "scenario_count": len(records),
        "turn_count": sum(int(item["turn_count"]) for item in records),
        "score": score,
        "max_score": 100.0,
        "passed": passed,
        "partial": sum(1 for item in records if item["status"] == "partial"),
        "failed": sum(1 for item in records if item["status"] == "fail"),
        "tool_calls": sum(len(turn["tool_calls"]) for item in records for turn in item["turns"]),
        "tool_errors": sum(
            1
            for item in records
            for turn in item["turns"]
            for result in turn["tool_results"]
            if result.get("is_error")
        ),
        "records": records,
    }
    _write_text(run_root / "results.json", json.dumps(payload, ensure_ascii=False, indent=2))
    lines = [
        "# PiAgent multi-turn 10 evaluation",
        "",
        f"- Run: `{run_id}`",
        f"- Model: `{route}` / `{model_id}`",
        f"- Result: **{score:.2f}/100** ({passed} pass, {payload['partial']} partial, {payload['failed']} fail)",
        f"- Turns: {payload['turn_count']}, tool calls: {payload['tool_calls']}, tool errors: {payload['tool_errors']}",
        "",
        "| ID | Level | Scenario | Turns | Result | Score |",
        "| --- | ---: | --- | ---: | --- | ---: |",
    ]
    for item in records:
        lines.append(
            f"| {item['id']} | {item['level']} | {item['title']} | {item['turn_count']} | {item['status']} | {item['score']:.2f}/10 |"
        )
    lines.extend(["", "## Conversation evidence", ""])
    for item in records:
        lines.append(f"### {item['id']} — {item['title']}")
        lines.append("")
        for turn in item["turns"]:
            lines.append(f"**Turn {turn['turn']} — User**")
            lines.append("")
            lines.append(str(turn["prompt"]))
            lines.append("")
            lines.append(f"**Turn {turn['turn']} — PiAgent**")
            lines.append("")
            lines.append(str(turn["final_text"]))
            lines.append("")
            lines.append(f"Tools: `{', '.join(turn['tool_names']) or '-'}`")
            lines.append("")
    _write_text(run_root / "summary.md", "\n".join(lines).rstrip() + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run 10 real PiAgent multi-turn scenarios")
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--scenarios", default="", help="Example: 1-3,M09")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    spec = _load_spec()
    if args.list:
        for item in spec["scenarios"]:
            print(f"{item['id']} L{item['level']} {item['title']} ({len(item['turns'])} turns)")
        return 0
    route, model_id = capability_eval._model_route()
    selected = _parse_selection(str(args.scenarios))
    run_root = RUNS_ROOT / str(args.run_id)
    workspace = _seed_workspace(run_root)
    context = {"work_rel": workspace.resolve().relative_to(ROOT).as_posix()}
    records: list[dict[str, Any]] = []
    for scenario in spec["scenarios"]:
        if scenario["id"] not in selected:
            continue
        record = _run_scenario(scenario, run_root=run_root, context=context, route=route, model_id=model_id)
        records.append(record)
        _write_text(run_root / "scenarios" / f"{record['id']}.json", json.dumps(record, ensure_ascii=False, indent=2))
        print(f"[{record['id']}] {record['status']} {record['score']:.2f}/10", flush=True)
    _write_results(run_root, str(args.run_id), route, model_id, records)
    print(f"\nresults: {capability_eval._relative(run_root / 'results.json')}")
    return 0 if all(item["status"] == "pass" for item in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
