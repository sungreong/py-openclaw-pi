from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from piagent import NullCallbacks, OpenClawPiLangChain, PiAgentConfig  # noqa: E402


SPEC_PATH = ROOT / "evaluation" / "agent_capability_20_tasks.json"
RUNS_ROOT = ROOT / "artifacts" / "agent-capability-20" / "runs"


class EvalCallbacks(NullCallbacks):
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    def on_tool_start(self, tool_name: str, args: dict[str, Any]) -> None:
        del args
        print(f"[{self.task_id}] tool:start {tool_name}", flush=True)

    def on_tool_end(self, tool_name: str, output: str, is_error: bool) -> None:
        del output
        state = "error" if is_error else "ok"
        print(f"[{self.task_id}] tool:end   {tool_name} [{state}]", flush=True)

    def on_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "custom" and payload.get("message"):
            print(f"[{self.task_id}] event {str(payload['message'])[:180]}", flush=True)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_workspace(run_root: Path) -> Path:
    workspace = run_root / "workspace"
    if workspace.exists():
        return workspace

    _write_text(
        workspace / "brief.md",
        "# 프로젝트 브리프\n\n- 프로젝트 코드명: ORBIT-42\n- 담당팀: Platform Lab\n",
    )
    _write_text(workspace / "notes" / "feature.txt", "FEATURE_ALPHA=enabled\nowner=platform\n")
    _write_text(workspace / "notes" / "history.txt", "FEATURE_BETA=disabled\n")
    _write_text(
        workspace / "sales.csv",
        "region,sales,returns\nSeoul,10,1\nBusan,30,2\nIncheon,20,1\n",
    )
    _write_text(
        workspace / "plan_target.py",
        "def divide(a: float, b: float) -> float:\n    return a / b\n",
    )
    _write_text(
        workspace / "review" / "legacy.py",
        "from datetime import datetime\n\n"
        "def load_user(user_id, cache=[]):\n"
        "    try:\n"
        "        cache.append(user_id)\n"
        "        return {'loaded_at': datetime.now(), 'items': cache}\n"
        "    except Exception:\n"
        "        return None\n",
    )
    _write_text(
        workspace / "bugfix" / "calculator.py",
        "def reserve(requested: int, stock: int) -> int:\n"
        "    if requested >= stock:\n"
        "        raise ValueError('insufficient stock')\n"
        "    return stock - requested\n",
    )
    _write_text(
        workspace / "bugfix" / "test_calculator.py",
        "import pytest\n\n"
        "from calculator import reserve\n\n"
        "def test_reserve_less_than_stock():\n"
        "    assert reserve(2, 5) == 3\n\n"
        "def test_reserve_equal_to_stock():\n"
        "    assert reserve(5, 5) == 0\n\n"
        "def test_reserve_more_than_stock():\n"
        "    with pytest.raises(ValueError):\n"
        "        reserve(6, 5)\n",
    )
    _write_text(
        workspace / "word-source.md",
        "# 운영 현황 보고서\n\n"
        "## 핵심 지표\n\n"
        "| 지역 | 매출 | 반품 |\n"
        "| --- | ---: | ---: |\n"
        "| Seoul | 10 | 1 |\n"
        "| Busan | 30 | 2 |\n"
        "| Incheon | 20 | 1 |\n\n"
        "## 조치\n\n- Busan 수요 원인 확인\n- 반품률 모니터링\n",
    )
    return workspace


def _load_spec() -> dict[str, Any]:
    data = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 20:
        raise ValueError("evaluation spec must contain exactly 20 tasks")
    ids = [str(task.get("id", "")) for task in tasks]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"T\d{2}", item) for item in ids):
        raise ValueError("task IDs must be unique TNN values")
    return data


def _parse_task_selection(raw: str, all_ids: Sequence[str]) -> list[str]:
    if not str(raw or "").strip():
        return list(all_ids)
    selected: list[str] = []
    for token in re.split(r"[\s,]+", raw.strip()):
        if not token:
            continue
        range_match = re.fullmatch(r"(\d{1,2})-(\d{1,2})", token)
        if range_match:
            start, end = (int(range_match.group(1)), int(range_match.group(2)))
            step = 1 if end >= start else -1
            selected.extend(f"T{number:02d}" for number in range(start, end + step, step))
            continue
        number_match = re.fullmatch(r"(?:T)?(\d{1,2})", token, flags=re.IGNORECASE)
        if not number_match:
            raise ValueError(f"invalid task selection: {token}")
        selected.append(f"T{int(number_match.group(1)):02d}")
    unknown = [item for item in selected if item not in all_ids]
    if unknown:
        raise ValueError("unknown tasks: " + ", ".join(unknown))
    return list(dict.fromkeys(selected))


def _safe_run_id(raw: str) -> str:
    candidate = str(raw or "").strip() or datetime.now().strftime("%Y%m%d-%H%M%S")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", candidate):
        raise ValueError("run-id must use letters, numbers, dot, underscore, or hyphen")
    return candidate


def _model_route() -> tuple[str, str]:
    bedrock_names = (
        "LOCAL_BEDROCK_BASE_URL",
        "LOCAL_BEDROCK_MODEL_ID",
        "LOCAL_BEDROCK_API_KEY",
    )
    present = [bool(os.getenv(name, "").strip()) for name in bedrock_names]
    if any(present) and not all(present):
        raise ValueError("all three LOCAL_BEDROCK_* variables are required")
    if all(present):
        return "local-bedrock", os.getenv("LOCAL_BEDROCK_MODEL_ID", "").strip()
    if os.getenv("OPENAI_API_KEY", "").strip():
        return "openai", os.getenv("PI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    raise ValueError("set OPENAI_API_KEY or all three LOCAL_BEDROCK_* variables")


def _build_config(run_root: Path, max_model_calls: int) -> PiAgentConfig:
    state = run_root / "state"
    return PiAgentConfig(
        model=os.getenv("PI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        workspace_dir=str(ROOT),
        session_dir=str(state / "sessions"),
        audit_dir=str(state / "audit"),
        memory_dir=str(state / "memory"),
        memory_mode="openclaw",
        memory_search_backend="hash",
        memory_embedding_provider="hash",
        max_model_calls=max(1, int(max_model_calls)),
        exec_timeout_s=max(30, int(os.getenv("PI_EXEC_TIMEOUT", "60"))),
        allow_shell=True,
        allow_write=True,
        allow_package_install=False,
        workspace_extensions_enabled=True,
        workspace_extension_dir=".piagent",
        mcp_enabled=False,
        skills_enabled=True,
        skills_dir="skills",
        skill_mode="auto",
        enable_subagents=True,
        hooks_config_path=str(state / "pi_hooks.json"),
    )


def _format_value(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**context)
    if isinstance(value, list):
        return [_format_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _format_value(item, context) for key, item in value.items()}
    return value


def _relative(path: Optional[Path]) -> str:
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _read_audit_events(path: Optional[Path]) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _resolve_check_path(raw: str) -> Path:
    path = (ROOT / raw).resolve()
    path.relative_to(ROOT)
    return path


def _check_result(
    check: dict[str, Any],
    final_text: str,
    tool_calls: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
) -> tuple[bool, str, dict[str, Any]]:
    kind = str(check.get("type", ""))
    tool_names = [str(item.get("name", "")) for item in tool_calls]
    lowered_final = final_text.casefold()
    evidence: dict[str, Any] = {}

    if kind == "final_contains":
        value = str(check.get("value", ""))
        ok = value.casefold() in lowered_final
        return ok, f"final contains {value!r}", evidence
    if kind == "final_order":
        values = [str(item) for item in check.get("values", [])]
        positions = [lowered_final.find(value.casefold()) for value in values]
        ok = all(position >= 0 for position in positions) and positions == sorted(positions)
        evidence["positions"] = positions
        return ok, "final preserves required order", evidence
    if kind == "tool_count_max":
        maximum = int(check.get("value", 0))
        evidence["actual"] = len(tool_calls)
        return len(tool_calls) <= maximum, f"tool call count <= {maximum}", evidence
    if kind == "required_tools_all":
        required = [str(item) for item in check.get("values", [])]
        missing = [name for name in required if name not in tool_names]
        evidence["actual"] = tool_names
        evidence["missing"] = missing
        return not missing, "all required tools were called", evidence
    if kind == "required_tools_any":
        choices = [str(item) for item in check.get("values", [])]
        matched = [name for name in choices if name in tool_names]
        evidence["actual"] = tool_names
        evidence["matched"] = matched
        return bool(matched), "at least one accepted tool was called", evidence
    if kind == "forbidden_tools":
        forbidden = [str(item) for item in check.get("values", [])]
        called = [name for name in tool_names if name in forbidden]
        evidence["called"] = called
        return not called, "forbidden tools were not called", evidence
    if kind == "no_tool_errors":
        errors = [
            {"name": item.get("name"), "content": str(item.get("content", ""))[:300]}
            for item in tool_results
            if bool(item.get("is_error"))
        ]
        evidence["errors"] = errors
        return not errors, "tool results contain no errors", evidence
    if kind == "tool_result_contains":
        target = str(check.get("tool", ""))
        value = str(check.get("value", ""))
        contents = [str(item.get("content", "")) for item in tool_results if str(item.get("name", "")) == target]
        ok = any(value.casefold() in content.casefold() for content in contents)
        evidence["result_count"] = len(contents)
        return ok, f"{target} result contains {value!r}", evidence
    if kind == "audit_skill_selected":
        expected = str(check.get("value", ""))
        selected = [
            str(event.get("payload", {}).get("skill_id", ""))
            for event in audit_events
            if event.get("type") == "skill_selected"
        ]
        evidence["selected"] = selected
        return expected in selected, f"selected skill is {expected}", evidence
    if kind in {"artifact_text_contains", "file_text_contains"}:
        path = _resolve_check_path(str(check.get("path", "")))
        if not path.is_file():
            return False, f"text file exists: {_relative(path)}", {"exists": False}
        text = path.read_text(encoding="utf-8", errors="replace")
        values = check.get("values") or [check.get("value", "")]
        missing = [str(value) for value in values if str(value).casefold() not in text.casefold()]
        evidence.update({"exists": True, "bytes": path.stat().st_size, "missing": missing})
        return not missing, f"text file contains required values: {_relative(path)}", evidence
    if kind == "artifact_docx":
        path = _resolve_check_path(str(check.get("path", "")))
        minimum = int(check.get("min_bytes", 1))
        valid_zip = path.is_file() and zipfile.is_zipfile(path)
        has_document = False
        if valid_zip:
            with zipfile.ZipFile(path) as archive:
                has_document = "word/document.xml" in archive.namelist()
        size = path.stat().st_size if path.is_file() else 0
        evidence.update({"exists": path.is_file(), "bytes": size, "valid_zip": valid_zip, "has_document_xml": has_document})
        return valid_zip and has_document and size >= minimum, f"valid DOCX exists: {_relative(path)}", evidence
    if kind == "external_pytest":
        path = _resolve_check_path(str(check.get("path", "")))
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", str(path), "-q"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        output = (completed.stdout + "\n" + completed.stderr).strip()
        evidence.update({"exit_code": completed.returncode, "output": output[-2000:]})
        return completed.returncode == 0, "independent pytest passes", evidence
    return False, f"unknown check type: {kind}", evidence


def _evaluate(
    task: dict[str, Any],
    final_text: str,
    tool_calls: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], float, str]:
    rows: list[dict[str, Any]] = []
    for check in task.get("checks", []):
        try:
            ok, label, evidence = _check_result(
                check,
                final_text=final_text,
                tool_calls=tool_calls,
                tool_results=tool_results,
                audit_events=audit_events,
            )
        except Exception as exc:  # evaluation must record checker failures, not hide them
            ok, label, evidence = False, f"checker error: {check.get('type')}", {"error": str(exc)}
        rows.append({"passed": ok, "label": label, "evidence": evidence})
    passed = sum(1 for row in rows if row["passed"])
    ratio = passed / len(rows) if rows else 0.0
    score = round(5.0 * ratio, 2)
    status = "pass" if ratio == 1.0 else ("partial" if ratio >= 0.5 else "fail")
    return rows, score, status


def _run_one(task: dict[str, Any], run_root: Path, context: dict[str, str], route: str, model_id: str) -> dict[str, Any]:
    expanded = _format_value(task, context)
    task_id = str(expanded["id"])
    session_id = context[f"session_{expanded['session'].replace('-', '_')}"]
    print(f"\n[{task_id}] {expanded['title']} (level={expanded['level']}, session={session_id})", flush=True)
    started = time.monotonic()
    result = None
    error = ""
    agent = OpenClawPiLangChain(_build_config(run_root, int(expanded.get("max_model_calls", 8))))
    try:
        result = agent.run(
            session_id=session_id,
            prompt=str(expanded["prompt"]),
            callbacks=EvalCallbacks(task_id),
            allowlist=expanded.get("allowlist"),
            denylist=expanded.get("denylist"),
            skill_name=(str(expanded.get("skill", "")).strip() or None),
            skill_mode=(str(expanded.get("skill_mode", "")).strip() or None),
            plan_mode=(str(expanded.get("plan_mode", "")).strip() or None),
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        agent.close()
    elapsed = round(time.monotonic() - started, 3)

    final_text = result.final_text if result is not None else ""
    tool_calls = result.tool_calls if result is not None else []
    tool_results = result.tool_results if result is not None else []
    audit_path = result.audit_file if result is not None else None
    audit_events = _read_audit_events(audit_path)
    checks, score, status = _evaluate(expanded, final_text, tool_calls, tool_results, audit_events)
    if error:
        checks.append({"passed": False, "label": "agent run completed without exception", "evidence": {"error": error}})
        score = 0.0
        status = "fail"

    record = {
        "id": task_id,
        "level": expanded["level"],
        "title": expanded["title"],
        "capability": expanded["capability"],
        "question": expanded["prompt"],
        "session_id": session_id,
        "depends_on": expanded.get("depends_on", []),
        "model_route": route,
        "model_id": model_id,
        "elapsed_seconds": elapsed,
        "status": status,
        "score": score,
        "max_score": 5.0,
        "error": error,
        "final_text": final_text,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "tool_names": [str(item.get("name", "")) for item in tool_calls],
        "tool_error_count": sum(1 for item in tool_results if item.get("is_error")),
        "audit_file": _relative(audit_path),
        "checks": checks,
    }
    task_path = run_root / "tasks" / f"{task_id}.json"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    if task_path.is_file():
        attempts = task_path.parent / "attempts"
        attempts.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        (attempts / f"{task_id}-{stamp}.json").write_bytes(task_path.read_bytes())
    task_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(1 for row in checks if row.get("passed"))
    print(f"[{task_id}] {status.upper()} score={score:.2f}/5 checks={passed}/{len(checks)} elapsed={elapsed:.1f}s", flush=True)
    return record


def _load_records(run_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted((run_root / "tasks").glob("T??.json")) if (run_root / "tasks").is_dir() else []:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _report_markdown(run_id: str, route: str, model_id: str, records: list[dict[str, Any]]) -> str:
    total_score = round(sum(float(row.get("score", 0)) for row in records), 2)
    max_score = 5.0 * len(records)
    status_counts = Counter(str(row.get("status", "unknown")) for row in records)
    tools = Counter(name for row in records for name in row.get("tool_names", []))
    level_scores: dict[int, list[float]] = defaultdict(list)
    for row in records:
        level_scores[int(row.get("level", 0))].append(float(row.get("score", 0)))

    lines = [
        "---",
        f"title: PiAgent 20-task capability evaluation — {run_id}",
        "theme: report",
        "intent: reference",
        "toc: true",
        "---",
        "",
        "# PiAgent 20개 실실행 역량 평가",
        "",
        f"- 실행 ID: `{run_id}`",
        f"- 모델 경로: `{route}`",
        f"- 모델 ID: `{model_id}`",
        f"- 실행 과제: {len(records)}/20",
        f"- 총점: **{total_score:.2f}/{max_score:.2f}**",
        f"- 판정: pass {status_counts['pass']}, partial {status_counts['partial']}, fail {status_counts['fail']}",
        "",
        "## 우선 결론 {: .briefing-lead}",
        "",
        "이 문서는 실제 모델 호출, Tool 증거, 파일 산출물 및 독립 검증을 기준으로 생성됐다. "
        "완료되지 않은 과제는 성공으로 간주하지 않는다.",
        "",
        "## 과제별 결과",
        "",
        "| ID | L | 과제 | 판정 | 점수 | Tool 호출 | 오류 | 시간(초) |",
        "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in records:
        lines.append(
            f"| {row['id']} | {row['level']} | {row['title']} | {row['status']} | "
            f"{float(row['score']):.2f}/5 | {len(row.get('tool_calls', []))} | "
            f"{row.get('tool_error_count', 0)} | {float(row.get('elapsed_seconds', 0)):.1f} |"
        )

    lines.extend(["", "## 난이도별 점수", "", "| 난이도 | 획득 | 가능 | 비율 |", "| ---: | ---: | ---: | ---: |"]) 
    for level in sorted(level_scores):
        earned = sum(level_scores[level])
        possible = 5.0 * len(level_scores[level])
        ratio = (earned / possible * 100) if possible else 0
        lines.append(f"| L{level} | {earned:.2f} | {possible:.2f} | {ratio:.1f}% |")

    lines.extend(["", "## 실제 Tool 사용", "", "| Tool | 호출 수 |", "| --- | ---: |"]) 
    if tools:
        for name, count in tools.most_common():
            lines.append(f"| `{name}` | {count} |")
    else:
        lines.append("| - | 0 |")

    lines.extend(["", "## 상세 결과와 실패 증거", ""])
    for row in records:
        lines.extend(
            [
                f"### {row['id']}. {row['title']}",
                "",
                f"- 질문: {row['question']}",
                f"- 판정: **{row['status']}**, {float(row['score']):.2f}/5",
                f"- Tool 흐름: `{' → '.join(row.get('tool_names', [])) or 'none'}`",
                f"- 감사 로그: `{row.get('audit_file') or '-'}`",
                "- 검증:",
            ]
        )
        for check in row.get("checks", []):
            marker = "PASS" if check.get("passed") else "FAIL"
            lines.append(f"  - {marker}: {check.get('label')}")
        final_preview = re.sub(r"\s+", " ", str(row.get("final_text", ""))).strip()
        if len(final_preview) > 500:
            final_preview = final_preview[:497] + "..."
        lines.extend([f"- 최종 답변 요약: {final_preview or '(empty)'}", ""])

    lines.extend(
        [
            "## 검증 범위와 한계",
            "",
            "- 점수는 질문별 선언된 자동 검사를 같은 비중으로 합산했다.",
            "- 뉴스 결과는 실행 시점의 외부 RSS 제공 상태에 영향을 받는다.",
            "- DOCX는 ZIP/OOXML 구조를 검사했지만 페이지 렌더와 육안 레이아웃 검수는 별도다.",
            "- 모델 답변의 문체 품질보다 Tool 증거, 정확한 값, 산출물 존재, 독립 테스트를 우선했다.",
            "- 개별 Task의 전체 응답과 Tool 결과는 `tasks/TNN.json`에 보존된다.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_summary(run_root: Path, run_id: str, route: str, model_id: str) -> list[dict[str, Any]]:
    records = _load_records(run_root)
    payload = {
        "run_id": run_id,
        "model_route": route,
        "model_id": model_id,
        "completed_tasks": len(records),
        "total_tasks": 20,
        "score": round(sum(float(row.get("score", 0)) for row in records), 2),
        "max_score": 5.0 * len(records),
        "records": records,
    }
    (run_root / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_root / "summary.md").write_text(_report_markdown(run_id, route, model_id, records), encoding="utf-8")
    return records


def _session_context(
    run_id: str,
    work_rel: str,
    tasks: Iterable[dict[str, Any]],
    session_suffix: str = "",
) -> dict[str, str]:
    context = {"run_id": run_id, "work_rel": work_rel}
    suffix = f"-{session_suffix}" if session_suffix else ""
    base_sessions: dict[str, str] = {}
    for task in tasks:
        key = str(task["session"])
        base_sessions[key] = f"eval20-{run_id}-{key}"
        context[f"session_{key.replace('-', '_')}"] = base_sessions[key] + suffix
    # A targeted rerun gets a fresh current session while still reading the
    # dependency sessions produced by the original full run.
    context["shared_session"] = base_sessions["shared-session"]
    context["news_session"] = base_sessions["news-research"]
    return context


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run PiAgent's 20-task Local Bedrock capability evaluation")
    parser.add_argument("--run-id", default="", help="Stable run ID; use with --resume after interruption")
    parser.add_argument("--tasks", default="", help="Task IDs or ranges, e.g. 1-5,9,T12")
    parser.add_argument("--resume", action="store_true", help="Skip task result files that already exist")
    parser.add_argument("--session-suffix", default="", help="Use fresh sessions for a targeted rerun")
    parser.add_argument("--list", action="store_true", help="List the 20 tasks without model calls")
    args = parser.parse_args(argv)

    try:
        spec = _load_spec()
        all_tasks = spec["tasks"]
        all_ids = [str(task["id"]) for task in all_tasks]
        selected_ids = _parse_task_selection(args.tasks, all_ids)
        run_id = _safe_run_id(args.run_id)
        session_suffix = str(args.session_suffix or "").strip()
        if session_suffix and not re.fullmatch(r"[a-zA-Z0-9_.-]{1,40}", session_suffix):
            raise ValueError("session-suffix must use letters, numbers, dot, underscore, or hyphen")
    except ValueError as exc:
        parser.error(str(exc))

    if args.list:
        for task in all_tasks:
            print(f"{task['id']} L{task['level']} {task['title']} — {task['capability']}")
        return 0

    try:
        route, model_id = _model_route()
    except ValueError as exc:
        parser.error(str(exc))

    run_root = RUNS_ROOT / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    workspace = _seed_workspace(run_root)
    work_rel = workspace.relative_to(ROOT).as_posix()
    context = _session_context(run_id, work_rel, all_tasks, session_suffix=session_suffix)
    selected = [task for task in all_tasks if task["id"] in selected_ids]

    metadata = {
        "run_id": run_id,
        "started_at": datetime.now().astimezone().isoformat(),
        "model_route": route,
        "model_id": model_id,
        "selected_tasks": selected_ids,
        "session_suffix": session_suffix,
        "spec": _relative(SPEC_PATH),
    }
    (run_root / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"run_id={run_id} route={route} model={model_id} tasks={len(selected)}", flush=True)
    print(f"output={_relative(run_root)}", flush=True)

    for task in selected:
        task_path = run_root / "tasks" / f"{task['id']}.json"
        if args.resume and task_path.is_file():
            print(f"[{task['id']}] skip existing result", flush=True)
            continue
        _run_one(task, run_root=run_root, context=context, route=route, model_id=model_id)
        _write_summary(run_root, run_id, route, model_id)

    records = _write_summary(run_root, run_id, route, model_id)
    score = sum(float(row.get("score", 0)) for row in records)
    print(f"\ncompleted={len(records)}/20 score={score:.2f}/{5 * len(records):.2f}", flush=True)
    print(f"summary={_relative(run_root / 'summary.md')}", flush=True)
    return 0 if len(records) == 20 else 1


if __name__ == "__main__":
    raise SystemExit(main())
