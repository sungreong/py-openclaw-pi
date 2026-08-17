# NaruWorks UI Policy

This is a fictional company policy used for PiAgent evaluation.

| ID | Default severity | Rule |
|---|---|---|
| UI-01 | P2 | Primary action color is `#1457D9`; accent is `#00A88F`; destructive action is `#D92D20`. Unapproved brand colors require design approval. |
| UI-02 | P3 | Use an 8px spacing grid. Padding and gaps must be multiples of 8, except a 4px inline icon gap. |
| UI-03 | P3 | Interactive controls use 8px radius. Cards and dialogs use 12px radius. Pill shapes require a semantic tag or filter purpose. |
| UI-04 | P1 | Buttons and icon controls must have a minimum 44px height and width. |
| UI-05 | P1 | Keyboard focus must use a visible 3px `#7AA7FF` outline or an approved equivalent with documented contrast. |
| UI-06 | P1 | Error state must not rely on color alone; include an icon or explicit Korean text describing the problem and recovery. |
| UI-07 | P2 | Korean interface copy must be concise and respectful. Do not blame the user. A standalone `실패했습니다` is non-compliant because it lacks a recovery action, but that phrase alone is not evidence of user blame. |
| UI-08 | P3 | Body font is `Pretendard, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`. |
| UI-09 | unknown | Text and control contrast must meet WCAG AA, but source colors alone are insufficient proof; verify against the rendered background. |

## Severity Mapping

- `P1`: blocks task completion or creates a serious accessibility failure
- `P2`: violates an interaction, error, or brand rule with meaningful user impact
- `P3`: consistency or maintainability issue with limited immediate impact
