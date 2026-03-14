---
id: data-report-writer
name: data-report-writer
description: Create evidence-based markdown reports from local data files by running the skill's Python profiler script first, then saving to reports/.
triggers:
  - summary report
  - markdown report
  - save report
  - report from csv
  - data report
  - 요약 리포트
  - 리포트 작성
  - 마크다운 보고서
required_tools:
  - exec
  - read
  - write
tool_allow:
  - exec
  - read
  - find
  - ls
  - write
tool_deny:
  - edit
api_policy: tool_first
---
Use this skill when the user wants a saved report document, not only a chat response.

Execution flow:
1. Identify source data path from the user request.
2. If path is missing, ask for one concrete path and stop.
3. Run the profiler script first with `exec`:
   - `python skills/data-report-writer/scripts/data_profile.py --path "<source_path>"`
4. Parse profiler JSON output and treat it as primary evidence.
5. Optionally use `read` for a small targeted preview only when needed.
6. Build report content from observed facts only (script output + verified file preview).
7. Save markdown report:
   - default: `reports/<source_stem>-summary.md`
   - if exists: `reports/<source_stem>-summary-2.md` (and increment)
8. Return:
   - source path
   - saved report path
   - 3-5 key highlights

Quality rules:
- Never invent schema, metrics, or values.
- Numeric claims must come from profiler output.
- If data quality is weak, include explicit caveats.
- Keep recommendations practical and short.

Resource usage order:
1. Run profiler script: `skills/data-report-writer/scripts/data_profile.py`
2. Read rubric: `skills/data-report-writer/references/report-rubric.md`
3. Reuse template: `skills/data-report-writer/assets/report-template.md`
4. Use sample data only for demo requests without a user file:
   - `skills/data-report-writer/data/sample_ops_metrics.csv`
5. Align style with example output:
   - `skills/data-report-writer/examples/expected_report.md`

Failure handling:
- If profiler execution fails, report the exact command error and ask for a valid file path or format.
- Supported profiler formats: `.csv`, `.tsv`, `.json`, `.txt`, `.md`.
