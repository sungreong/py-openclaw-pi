# NaruWorks Git Policy

This is a fictional company policy used for PiAgent evaluation.

| ID | Rule |
|---|---|
| GIT-01 | Branch names must be `type/NW-####-kebab-summary`. Allowed types: `feature`, `fix`, `refactor`, `docs`, `test`, `chore`. |
| GIT-02 | Production bug fixes use `fix`; emergency release branches still use `fix`, never `hotfix`. |
| GIT-03 | Commit subjects must be `type(scope): Korean imperative summary`, use an allowed GIT-01 type, and remain at most 72 characters. |
| GIT-04 | One branch and commit must represent one logical change. Separate unrelated formatting or dependency updates. |
| GIT-05 | Direct pushes to `main` and `release/*` are prohibited. Changes require a pull request and one reviewer outside the author’s team. |
| GIT-06 | Every PR description must include issue ID, summary, risk, test evidence, rollback plan, and UI screenshots when visible UI changes exist. |
| GIT-07 | Unexecuted checks must be labeled `not run`; planned commands are not test evidence. |
| GIT-08 | Never stage `.env`, credentials, generated agent state, or files under `private/` and `secrets/`. |

## Examples

- Valid branch: `fix/NW-4821-payment-timeout`
- Valid commit: `fix(payment): 결제 시간 초과 처리를 수정하라`
- Invalid branch: `hotfix/payment-timeout`
- Invalid evidence: `pytest 실행 예정`
