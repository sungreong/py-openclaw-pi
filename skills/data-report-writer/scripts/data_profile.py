import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _resolve_workspace_path(raw_path: str) -> Path:
    workspace = Path.cwd().resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("path escapes current workspace") from exc
    return resolved


def _to_float(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        num = float(text)
    except ValueError:
        return None
    if not math.isfinite(num):
        return None
    return num


def _numeric_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {}
    total = sum(values)
    count = len(values)
    return {
        "count": count,
        "sum": total,
        "mean": total / count,
        "min": min(values),
        "max": max(values),
    }


def _profile_dict_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys: list[str] = sorted({str(k) for row in rows for k in row.keys()})
    numeric_values: dict[str, list[float]] = defaultdict(list)
    missing_counts: dict[str, int] = defaultdict(int)
    category_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for row in rows:
        for key in keys:
            raw = row.get(key, "")
            text = str(raw).strip()
            if not text:
                missing_counts[key] += 1
                continue
            num = _to_float(text)
            if num is None:
                category_counts[key][text] += 1
            else:
                numeric_values[key].append(num)

    numeric_stats = {
        col: _numeric_summary(values)
        for col, values in numeric_values.items()
        if values
    }

    top_categories: dict[str, list[dict[str, Any]]] = {}
    for col, counter in category_counts.items():
        if not counter:
            continue
        top_categories[col] = [
            {"value": value, "count": count}
            for value, count in counter.most_common(5)
        ]

    return {
        "row_count": len(rows),
        "columns": keys,
        "column_count": len(keys),
        "numeric_stats": numeric_stats,
        "missing_counts": dict(missing_counts),
        "top_categories": top_categories,
    }


def _detect_delimiter(sample: str, fallback: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        return str(dialect.delimiter)
    except Exception:
        return fallback


def _profile_csv_or_tsv(path: Path, delimiter_hint: str) -> dict[str, Any]:
    sample = path.read_text(encoding="utf-8-sig", errors="replace")[:4096]
    delimiter = _detect_delimiter(sample, delimiter_hint)
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp, delimiter=delimiter)
        rows = [dict(row) for row in reader]
    profile = _profile_dict_rows(rows)
    profile["delimiter"] = "\\t" if delimiter == "\t" else delimiter
    return profile


def _profile_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        if all(isinstance(item, dict) for item in data):
            return _profile_dict_rows([dict(item) for item in data])
        values = [_to_float(item) for item in data]
        clean = [v for v in values if v is not None]
        return {
            "row_count": len(data),
            "columns": ["value"],
            "column_count": 1,
            "numeric_stats": {"value": _numeric_summary(clean)} if clean else {},
            "missing_counts": {"value": len([v for v in values if v is None])},
            "top_categories": {},
        }
    if isinstance(data, dict):
        return _profile_dict_rows([data])
    raise ValueError("unsupported JSON shape; expected object or array")


def _profile_text(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    return {
        "line_count": len(lines),
        "char_count": len(text),
        "sample_head": lines[:10],
    }


def profile_file(path: Path) -> dict[str, Any]:
    ext = path.suffix.lower()
    if ext == ".csv":
        payload = _profile_csv_or_tsv(path, ",")
        file_type = "csv"
    elif ext == ".tsv":
        payload = _profile_csv_or_tsv(path, "\t")
        file_type = "tsv"
    elif ext == ".json":
        payload = _profile_json(path)
        file_type = "json"
    elif ext in {".txt", ".md"}:
        payload = _profile_text(path)
        file_type = "text"
    else:
        raise ValueError("unsupported file extension")
    return {
        "source_path": path.as_posix(),
        "file_type": file_type,
        **payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile local data files for report generation.")
    parser.add_argument("--path", required=True, help="Relative or absolute path to data file.")
    args = parser.parse_args()

    try:
        source = _resolve_workspace_path(args.path)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"file not found: {args.path}")
        result = profile_file(source)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
