---
name: naru-ui-design-review
description: Review HTML, CSS, or UI components against the fictional NaruWorks design system and accessibility rules. Use for NaruWorks UI review, 회사 디자인 규칙, 디자인 시스템 점검, 화면 품질 검사, 색상·간격·버튼·오류 상태 검토, or producing a policy-cited design audit without editing the source.
---

# NaruWorks UI Design Review

Review an interface against the company rules in `references/ui-policy.md`.

## Workflow

1. `read` `skills/naru-ui-design-review/references/ui-policy.md` before evaluating the target.
2. `read` the user-provided HTML, CSS, or component file.
3. `grep` the same target with `pattern=#[0-9A-Fa-f]{6}|border-radius|padding|height|outline`, `output_mode=content`, and `head_limit=80`.
4. Compare only observed evidence with the policy. Do not invent rendered behavior or contrast ratios.
5. Do not modify the source unless the user explicitly requests implementation. For a review request, create only the requested report.
6. `write` the Markdown review and `ls` its directory to verify the artifact.

## Finding Contract

For every finding include:

- severity: `P1`, `P2`, or `P3`
- policy ID such as `[UI-04]`
- exact selector or visible text evidence
- why it violates or cannot yet prove compliance
- concrete remediation

Include a `Verified compliant` section and an `Unknowns requiring rendered inspection` section. Source inspection cannot prove focus visibility, font rendering, responsive layout, or color contrast; label those unknown rather than passed.

Use the policy table’s default severity for each rule. Do not upgrade or downgrade it unless the source proves a more specific user impact, and explain any override.

Apply copy rules literally: text such as `실패했습니다` violates `[UI-07]` when recovery guidance is absent, but do not call it user-blaming unless the copy explicitly attributes fault to the user. Treat a policy-approved color as compliant only when the element’s semantic purpose is observable from the source.
