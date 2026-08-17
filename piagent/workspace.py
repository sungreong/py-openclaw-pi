from __future__ import annotations

from .deps import *

class WorkspaceGuard:
    ARTIFACT_PREFIXES = ("reports", "artifacts", "outputs")

    def __init__(
        self,
        workspace_dir: Path,
        blocked_paths: Optional[Sequence[str]] = None,
        user_id: Optional[str] = None,
    ):
        self.workspace_dir = workspace_dir.resolve()
        patterns = list(blocked_paths or [])
        self.blocked_patterns = [self._normalize_pattern(p) for p in patterns if self._normalize_pattern(p)]
        self.user_id = _sanitize_user_id(user_id)

    @staticmethod
    def _normalize_pattern(raw: str) -> str:
        text = str(raw or "").strip().replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        text = text.strip("/")
        return text.lower()

    @staticmethod
    def _normalize_relpath(raw: str) -> str:
        text = str(raw or "").replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        text = text.strip("/")
        return text.lower()

    def is_blocked(self, resolved_path: Path) -> bool:
        try:
            rel = resolved_path.relative_to(self.workspace_dir).as_posix()
        except ValueError:
            return True
        rel_norm = self._normalize_relpath(rel)
        if not rel_norm:
            rel_norm = "."
        if self.user_id:
            parts = [p for p in rel_norm.split("/") if p]
            if len(parts) >= 3 and parts[0] == "artifacts" and parts[1] == "users":
                if _sanitize_user_id(parts[2]) != self.user_id:
                    return True
        for pattern in self.blocked_patterns:
            if fnmatch.fnmatch(rel_norm, pattern):
                return True
            if pattern.endswith("/**"):
                root = pattern[:-3].rstrip("/")
                if rel_norm == root or rel_norm.startswith(root + "/"):
                    return True
        return False

    def assert_allowed(self, resolved_path: Path) -> None:
        if self.is_blocked(resolved_path):
            raise ValueError(f"blocked path by policy: {resolved_path.relative_to(self.workspace_dir)}")

    def user_artifact_root(self) -> Optional[Path]:
        if not self.user_id:
            return None
        return (self.workspace_dir / "artifacts" / "users" / self.user_id).resolve()

    def _enforce_user_namespace(self, resolved_path: Path) -> None:
        if not self.user_id:
            return
        try:
            rel = resolved_path.relative_to(self.workspace_dir).as_posix()
        except ValueError as exc:
            raise ValueError("path escapes workspace") from exc
        parts = [p for p in rel.split("/") if p]
        if len(parts) >= 3 and parts[0] == "artifacts" and parts[1] == "users":
            owner = parts[2]
            if owner != self.user_id:
                raise ValueError(
                    f"user artifact isolation violation: path belongs to user '{owner}', current user='{self.user_id}'"
                )

    def _rewrite_user_artifact_path(self, candidate: Path) -> Path:
        if not self.user_id:
            return candidate
        root = self.user_artifact_root()
        if root is None:
            return candidate

        if candidate.is_absolute():
            try:
                rel = candidate.resolve().relative_to(self.workspace_dir)
                parts = rel.parts
            except Exception:
                return candidate
        else:
            parts = candidate.parts

        if len(parts) >= 3 and parts[0].lower() == "artifacts" and parts[1].lower() == "users":
            if _sanitize_user_id(parts[2]) == self.user_id:
                return self.workspace_dir / Path(*parts)
            return self.workspace_dir / Path(*parts)

        if not parts:
            return candidate
        first = str(parts[0]).lower()
        if first not in self.ARTIFACT_PREFIXES:
            return candidate

        if first == "reports":
            suffix = Path("reports", *parts[1:])
        elif first == "outputs":
            suffix = Path("outputs", *parts[1:])
        else:
            suffix = Path("artifacts", *parts[1:])
        return root / suffix

    def resolve(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        candidate = self._rewrite_user_artifact_path(candidate)
        if not candidate.is_absolute():
            candidate = self.workspace_dir / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.workspace_dir)
        except ValueError as exc:
            raise ValueError(f"Path escapes workspace: {raw_path}") from exc
        self._enforce_user_namespace(resolved)
        self.assert_allowed(resolved)
        return resolved

__all__ = [name for name in globals() if not name.startswith("__")]
