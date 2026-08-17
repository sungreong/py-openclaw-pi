---
name: code-health-check
description: Create an evidence-based code health report for a local project. Use for code health check, project health review, repository inspection, 프로젝트 상태 점검, 코드 품질 점검, or requests to summarize source files, tests, languages, TODOs, and large files before coding.
---

# Code Health Check

Produce a concise Markdown report from deterministic workspace evidence.

## Workflow

1. Run the bundled scanner before making any quantitative claim:

   ```text
   python skills/code-health-check/scripts/project_health.py --path .
   ```

   Use this exact path. Do not search for the script first.

2. Treat the scanner JSON as the source of truth for counts. Do not estimate missing values.
3. Read at most three targeted files only when needed to explain an observed risk. Do not edit project source code.
4. Write the report directly to the user-specified path. If no path is given, use `reports/code-health-report.md`. The `write` tool creates parent directories; do not run `mkdir`.
5. Verify that the saved file exists with `ls` or `read` before claiming completion.

## Evidence Sample Mode

When the request contains `include evidence sample` or `근거 샘플 포함`, execute this exact tool sequence:

1. `exec`: run the bundled scanner command above. Do not call `find` or `ls` first.
2. From `largest_source_files`, select the first row whose `is_test` value is `false`.
3. `read`: read that exact path with `offset=1` and `limit=80`.
4. `grep`: search that same file with `pattern=TODO|FIXME`, `output_mode=count`, and `head_limit=20`.
5. `write`: create the Markdown report with an additional `## Evidence Sample` section containing the selected path, a brief observation from the read result, and the exact grep count. Do not infer project-wide meaning from one file.
6. `ls`: verify the report path.

Use the dedicated `read` and `grep` tools, not shell commands. Extra discovery calls are unnecessary because the scanner returns the target path.

## Report Contract

Include these sections:

- `# Code Health Report`
- `## Scope`
- `## Inventory` with all code files, non-test source files, test files, total code lines, non-test source lines, test lines, the scanner-provided test-line percentage, and skipped files
- `## Languages` using the scanner language counts
- `## Signals` with TODO/FIXME lexical occurrence count and largest source files
- `## Assessment` separating observed facts from interpretation
- `## Recommended Next Checks` with no more than five concrete items

State that this is a static inventory, not proof of runtime correctness. Treat TODO/FIXME as lexical occurrences, not necessarily unresolved comments, and do not infer unresolved work from the count. Do not infer test coverage from file or line ratios. Do not recalculate ratios already provided by the scanner. Do not claim tests passed unless a test command was separately executed and its output was observed.

## Safety

- Keep all reads and output inside the workspace.
- Never scan or report `.env`, `.git`, secrets, private data, dependency trees, or generated agent state.
- Do not install packages; the bundled scanner uses only the Python standard library.
