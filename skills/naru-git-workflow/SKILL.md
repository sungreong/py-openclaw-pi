---
name: naru-git-workflow
description: Apply the fictional NaruWorks company Git policy to branch names, commit messages, pull-request plans, release changes, and repository status reviews. Use for NaruWorks Git questions, 회사 Git 규칙, 브랜치명 추천, 커밋 메시지 검토, PR 체크리스트, or preparing a company-compliant change plan without mutating Git state.
---

# NaruWorks Git Workflow

Apply the company policy from `references/git-policy.md`; do not rely on generic Git conventions when the policy is more specific.

## Workflow

1. `read` `skills/naru-git-workflow/references/git-policy.md` before making any recommendation.
2. When the request asks about the current repository, call `exec_readonly` with exactly `git status --short`. Do not use `exec` for Git inspection.
3. Build recommendations from the user request, observed status, and cited policy IDs.
4. Never create a branch, stage, commit, push, or change files. This skill produces a review or plan only.
5. When an output path is requested, `write` the Markdown plan and use `ls` to verify it.

## Output Contract

Include:

- proposed branch name
- proposed commit subject
- PR title
- required PR checklist
- observed repository state, or `not inspected`
- assumptions and blockers, using `unknown` for anything not directly observed
- policy citations such as `[GIT-01]`

For a bug-fix PR plan, cite every applicable policy from `[GIT-01]` through `[GIT-08]`; do not silently apply a rule without its ID. Always include the one-logical-change rule `[GIT-04]` and protected-branch review rule `[GIT-05]` in the checklist. Mark unexecuted tests and CI checks exactly `not run` per `[GIT-07]`. Use `unknown` for other unverified facts such as branch existence, dependency changes, UI impact, or sensitive-file safety. Do not claim a test passed unless its command and successful output were observed. Do not expose blocked paths, credentials, or file contents from repository status.
