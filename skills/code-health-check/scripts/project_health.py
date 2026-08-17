from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


IGNORED_DIRS = {
    ".agents",
    ".claude",
    ".codegraph",
    ".codex",
    ".cursor",
    ".gemini",
    ".git",
    ".mps",
    ".openclaw",
    ".openclaw_pi",
    ".piagent",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".skillbridge",
    ".venv",
    "venv",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
    "node_modules",
    "private",
    "secrets",
}
SOURCE_LANGUAGES = {
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cs": "C#",
    ".go": "Go",
    ".h": "C/C++ Header",
    ".hpp": "C++ Header",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript JSX",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".php": "PHP",
    ".ps1": "PowerShell",
    ".py": "Python",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".scala": "Scala",
    ".sh": "Shell",
    ".swift": "Swift",
    ".ts": "TypeScript",
    ".tsx": "TypeScript JSX",
}
MAX_FILE_BYTES = 2_000_000


def _is_test_file(relative_path: Path) -> bool:
    lowered_parts = {part.lower() for part in relative_path.parts[:-1]}
    name = relative_path.name.lower()
    return "tests" in lowered_parts or "test" in lowered_parts or name.startswith("test_") or name.endswith("_test.py")


def scan_project(root: Path) -> dict[str, object]:
    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise ValueError(f"project path is not a directory: {root}")

    language_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "lines": 0})
    largest_files: list[dict[str, object]] = []
    scanned_file_count = 0
    source_file_count = 0
    non_test_source_file_count = 0
    test_file_count = 0
    source_lines = 0
    non_test_source_lines = 0
    test_lines = 0
    todo_fixme_count = 0
    skipped_file_count = 0

    for path in sorted(resolved_root.rglob("*")):
        try:
            relative = path.relative_to(resolved_root)
        except ValueError:
            skipped_file_count += 1
            continue
        if any(part.lower() in IGNORED_DIRS for part in relative.parts[:-1]):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if relative.name.lower() == ".env":
            continue

        scanned_file_count += 1
        language = SOURCE_LANGUAGES.get(path.suffix.lower())
        if language is None:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                skipped_file_count += 1
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped_file_count += 1
            continue

        line_count = len(content.splitlines())
        is_test = _is_test_file(relative)
        source_file_count += 1
        source_lines += line_count
        if is_test:
            test_file_count += 1
            test_lines += line_count
        else:
            non_test_source_file_count += 1
            non_test_source_lines += line_count
        todo_fixme_count += content.lower().count("todo") + content.lower().count("fixme")
        language_stats[language]["files"] += 1
        language_stats[language]["lines"] += line_count
        largest_files.append({"path": relative.as_posix(), "lines": line_count, "is_test": is_test})

    largest_files.sort(key=lambda item: (-int(item["lines"]), str(item["path"])))
    ordered_languages = {
        name: language_stats[name]
        for name in sorted(language_stats, key=lambda item: (-language_stats[item]["lines"], item))
    }
    return {
        "status": "ok",
        "root": str(resolved_root),
        "scanned_file_count": scanned_file_count,
        "source_file_count": source_file_count,
        "non_test_source_file_count": non_test_source_file_count,
        "test_file_count": test_file_count,
        "source_lines": source_lines,
        "non_test_source_lines": non_test_source_lines,
        "test_lines": test_lines,
        "test_to_non_test_line_percent": round(test_lines / non_test_source_lines * 100, 1)
        if non_test_source_lines
        else 0.0,
        "todo_fixme_count": todo_fixme_count,
        "skipped_file_count": skipped_file_count,
        "languages": ordered_languages,
        "largest_source_files": largest_files[:5],
        "limitations": [
            "Static text inventory only; it does not execute tests or measure runtime correctness.",
            "TODO/FIXME is a lexical occurrence count and may include comments, strings, or documentation.",
            "Generated state, dependencies, artifacts, private paths, and files over 2 MB are excluded.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a deterministic code health inventory as JSON")
    parser.add_argument("--path", default=".", help="Project directory to scan")
    args = parser.parse_args()
    try:
        result = scan_project(Path(args.path))
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
