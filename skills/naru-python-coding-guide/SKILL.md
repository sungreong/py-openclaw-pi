---
name: naru-python-coding-guide
description: 회사 코딩 가이드, 사내 코딩 규칙, NaruWorks Python, Python 코드 리뷰, 구현, 리팩터링, 테스트 질문에 회사 표준을 적용한다. Use for company-specific Python guidance, code generation, reviews, fixes, and compliance checks.
---

# NaruWorks Python Coding Guide

Apply the fictional company rules in `references/python-coding-guide.md`.

## Workflow

1. Read `skills/naru-python-coding-guide/references/python-coding-guide.md` directly before answering or changing code. Do not call `find`, `grep`, or `ls` to discover this known reference path.
2. For a policy question, answer from the reference and cite every applicable rule ID.
3. For a code review, read the target, separate observed violations, verified compliance, and unknowns, and cite exact code evidence.
4. For implementation, inspect the smallest relevant scope, make only the requested change, add or update focused tests, and run the narrowest relevant verification.
   - When the user requests a new output path, call `write` directly; it creates parent directories. Do not call `ls` on a not-yet-created directory.
   - If a read-only check reports that an expected new output path is missing, continue by creating it instead of ending the task.
   - For a self-contained new output, do not search the whole repository for example classes. Implement the smallest policy-compliant code in the requested files.
5. Do not edit source for a review-only request. Do not claim a test passed unless its command completed successfully.
6. If a requirement conflicts with the guide, explain the conflict and request a documented exception rather than silently ignoring the rule.

## Implementation Completion Gate

Do not end an implementation turn immediately after writing files. Complete these steps in order:

1. Write the requested implementation.
2. Write or update the focused tests.
3. Run the exact narrow test command with `exec`.
4. Verify the requested files with `ls` or `read`.
5. Return a user-visible summary containing the command and observed result.

If a step fails, attempt a targeted correction or report the exact failure. Never emit only internal reasoning after a write.

## Output Contract

- Cite policy IDs such as `[PY-04]` next to the recommendation or finding they support.
- Label commands that were not executed exactly `not run`.
- For code reviews, include the selector, function, or exact expression that proves each finding.
- For implementations, summarize changed files, test evidence, and remaining unknowns.
- Never reveal credentials or quote secret values found in code or logs.
