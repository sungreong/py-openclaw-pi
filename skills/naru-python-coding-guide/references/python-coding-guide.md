# NaruWorks Python Coding Policy

This is a fictional company policy used to evaluate whether PiAgent loads and follows project-specific coding guidance.

| ID | Rule |
| --- | --- |
| PY-01 | Target Python 3.12. Every public function and method must annotate all parameters and its return type. Avoid `Any` at service boundaries unless the reason is documented. |
| PY-02 | A public service operation must return a named `@dataclass(frozen=True)` result, not a raw `dict` or positional tuple. |
| PY-03 | Expected domain failures must raise a subclass of `NaruDomainError` with a stable code formatted `NRU_<AREA>_<REASON>`. Never use `None`, `False`, or an empty collection as a failure sentinel. |
| PY-04 | Business time must be timezone-aware UTC and supplied through `clock: Callable[[], datetime]`. Reject a naive value returned by `clock()`; do not call `datetime.now()` directly in domain logic. |
| PY-05 | Structured logs must put a dot-delimited event name in `extra={"event": "naru.<area>.<verb>"}`. Do not interpolate tokens, passwords, credentials, or full payloads into log messages. |
| PY-06 | Never use a mutable default argument. Accept `None` and allocate a new collection inside the function when optional collection input is required. |
| PY-07 | Catch only expected exception types. When translating an infrastructure exception to `NaruDomainError`, preserve the cause with `raise ... from exc`. Broad `except Exception` is allowed only at a process boundary that logs and re-raises. |
| PY-08 | Async code must not call blocking I/O or `time.sleep`. Use an async client and `await asyncio.sleep(...)`. |
| PY-09 | Pytest names use `test_<unit>__when_<condition>__then_<outcome>`. Cover the happy path, one boundary, and each defined failure. An unexecuted command must be reported exactly `not run`. |
| PY-10 | Keep a change to one logical concern. Do not mix formatting, dependency upgrades, or unrelated refactors into the same change. |
| PY-11 | A deliberate exception requires an adjacent comment formatted `# guide-exception: PY-XX reason=<text> owner=<team> expires=YYYY-MM-DD`. An explanation in a PR description alone is insufficient. |

## Approved Example

```python
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone


class NaruDomainError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Deadline:
    expires_at: datetime


def build_deadline(seconds: int, clock: Callable[[], datetime]) -> Deadline:
    now = clock()
    if now.tzinfo is None or now.utcoffset() is None:
        raise NaruDomainError("NRU_TIME_NAIVE", "clock must return timezone-aware UTC")
    if now.utcoffset() != timezone.utc.utcoffset(now):
        raise NaruDomainError("NRU_TIME_NOT_UTC", "clock must return UTC")
    return Deadline(expires_at=now)
```
