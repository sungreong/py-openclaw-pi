from __future__ import annotations

from .deps import *
from .models import PiCallbacks, PiRunResult
from .permissions import normalize_edit_paths, normalize_permission_profile


class PiAgentSession:
    """Stateful SDK wrapper around an OpenClawPiLangChain agent.

    The agent remains the execution engine; this object keeps one session's
    user-facing state together so CLI, chat, RPC, and tests can share it.
    """

    def __init__(
        self,
        agent: Any,
        session_id: str = "main",
        *,
        callbacks: Optional[PiCallbacks] = None,
        skill_name: Optional[str] = None,
        skill_mode: Optional[str] = None,
        plan_mode: Optional[str] = None,
        allowlist: Optional[Sequence[str]] = None,
        denylist: Optional[Sequence[str]] = None,
        permission_profile: Optional[str] = None,
        edit_paths: Optional[Sequence[str]] = None,
    ):
        self.agent = agent
        self.session_id = str(session_id or "main")
        self.callbacks = callbacks
        self.skill_name = str(skill_name).strip() if skill_name else None
        self.skill_mode = str(skill_mode).strip() if skill_mode else None
        self.plan_mode = str(plan_mode).strip() if plan_mode else None
        self.allowlist = list(allowlist or [])
        self.denylist = list(denylist or [])
        self.permission_profile = normalize_permission_profile(permission_profile)
        self.edit_paths = normalize_edit_paths(edit_paths)
        if self.permission_profile == "edit" and not self.edit_paths:
            raise ValueError("edit mode requires at least one edit path")
        self.last_result: Optional[PiRunResult] = None

    def prompt(
        self,
        text: str,
        *,
        callbacks: Optional[PiCallbacks] = None,
        skill_name: Optional[str] = None,
        skill_mode: Optional[str] = None,
        plan_mode: Optional[str] = None,
        allowlist: Optional[Sequence[str]] = None,
        denylist: Optional[Sequence[str]] = None,
        permission_profile: Optional[str] = None,
        edit_paths: Optional[Sequence[str]] = None,
    ) -> PiRunResult:
        """Run one prompt in this session, with optional per-call overrides."""
        result = self.agent.run(
            session_id=self.session_id,
            prompt=str(text or ""),
            callbacks=callbacks if callbacks is not None else self.callbacks,
            skill_name=self.skill_name if skill_name is None else skill_name,
            skill_mode=self.skill_mode if skill_mode is None else skill_mode,
            plan_mode=self.plan_mode if plan_mode is None else plan_mode,
            allowlist=self.allowlist if allowlist is None else allowlist,
            denylist=self.denylist if denylist is None else denylist,
            permission_profile=(
                self.permission_profile if permission_profile is None else permission_profile
            ),
            edit_paths=self.edit_paths if edit_paths is None else edit_paths,
        )
        self.last_result = result
        return result

    def follow_up(self, text: str, **kwargs: Any) -> PiRunResult:
        """Alias for prompt(), emphasizing same-session continuation."""
        return self.prompt(text, **kwargs)

    def set_skill(self, name: Optional[str], mode: str = "manual") -> None:
        """Pin, clear, or switch skill selection for future prompts."""
        normalized_mode = str(mode or "manual").strip().lower()
        if name is None or normalized_mode == "off":
            self.skill_name = None
            self.skill_mode = "off" if normalized_mode == "off" else "auto"
            return
        if normalized_mode not in {"manual", "auto", "off"}:
            raise ValueError("skill mode must be manual, auto, or off")
        self.skill_name = str(name).strip() or None
        self.skill_mode = normalized_mode

    def set_skill_auto(self) -> None:
        self.skill_name = None
        self.skill_mode = "auto"

    def set_plan_mode(self, mode: str) -> None:
        normalized = str(mode or "off").strip().lower()
        if normalized not in {"on", "off"}:
            raise ValueError("plan mode must be on or off")
        self.plan_mode = normalized

    def set_callbacks(self, callbacks: Optional[PiCallbacks]) -> None:
        self.callbacks = callbacks

    def set_permission_profile(
        self,
        mode: str,
        *,
        edit_paths: Optional[Sequence[str]] = None,
    ) -> None:
        """Switch between review, scoped edit, and full access."""
        profile = normalize_permission_profile(mode)
        paths = self.edit_paths if edit_paths is None else normalize_edit_paths(edit_paths)
        if profile == "edit" and not paths:
            raise ValueError("edit mode requires at least one edit path")
        self.permission_profile = profile
        self.edit_paths = paths if profile == "edit" else []

    def set_tool_policy(
        self,
        *,
        allowlist: Optional[Sequence[str]] = None,
        denylist: Optional[Sequence[str]] = None,
    ) -> None:
        if allowlist is not None:
            self.allowlist = list(allowlist)
        if denylist is not None:
            self.denylist = list(denylist)

    def state(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "skill_name": self.skill_name,
            "skill_mode": self.skill_mode,
            "plan_mode": self.plan_mode,
            "allowlist": list(self.allowlist),
            "denylist": list(self.denylist),
            "permission_profile": self.permission_profile,
            "edit_paths": list(self.edit_paths),
            "has_last_result": self.last_result is not None,
        }

    def close(self) -> None:
        close = getattr(self.agent, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> "PiAgentSession":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


__all__ = ["PiAgentSession"]
