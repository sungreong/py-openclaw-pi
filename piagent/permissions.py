from __future__ import annotations

from typing import Optional, Sequence


PERMISSION_PROFILES = ("review", "edit", "full")

# Profiles intentionally name built-in tools instead of trusting arbitrary
# workspace/MCP extensions. Advanced allow/deny lists remain available, but a
# profile is always an upper bound on what a turn may use.
REVIEW_PROFILE_TOOLS = frozenset(
    {
        "read",
        "ls",
        "find",
        "grep",
        "delegate_task",
        "ask_user",
        "web_fetch",
        "web_search",
        "tool_search",
        "mcp_list_resources",
        "mcp_read_resource",
        "mcp_list_resource_templates",
        "memory_search",
        "memory_get",
        "session_fragment_search",
        "session_fragment_get",
        "work_note_read",
        "work_note_search",
        "plan_note_write",
        "todo_read",
        "todo_write",
    }
)
EDIT_PROFILE_TOOLS = REVIEW_PROFILE_TOOLS | {"edit"}


def normalize_permission_profile(value: Optional[str]) -> Optional[str]:
    """Normalize the small public permission surface while preserving legacy mode."""
    if value is None:
        return None
    normalized = str(value).strip().lower().replace("-", "_")
    aliases = {
        "plan": "review",
        "read_only": "review",
        "readonly": "review",
        "accept_edits": "edit",
        "acceptedits": "edit",
        "unsafe": "full",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in PERMISSION_PROFILES:
        raise ValueError(f"mode must be one of: {', '.join(PERMISSION_PROFILES)}")
    return normalized


def tools_for_permission_profile(profile: Optional[str]) -> Optional[frozenset[str]]:
    normalized = normalize_permission_profile(profile)
    if normalized is None or normalized == "full":
        return None
    if normalized == "review":
        return REVIEW_PROFILE_TOOLS
    return EDIT_PROFILE_TOOLS


def normalize_edit_paths(paths: Optional[Sequence[str]]) -> list[str]:
    if paths is None:
        return []
    normalized = [str(path).strip() for path in paths]
    if any(not path for path in normalized):
        raise ValueError("edit paths must be non-empty strings")
    return normalized


__all__ = [
    "PERMISSION_PROFILES",
    "REVIEW_PROFILE_TOOLS",
    "EDIT_PROFILE_TOOLS",
    "normalize_permission_profile",
    "tools_for_permission_profile",
    "normalize_edit_paths",
]
