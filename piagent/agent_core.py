from __future__ import annotations

from .deps import *
from .models import *
from .workspace import WorkspaceGuard
from .stores import *
from .utils import *
from .mcp import McpStdioClient

import sys

from .agent_hooks import AgentHooksMixin
from .agent_registry import AgentRegistryMixin
from .agent_worknotes import AgentWorkNotesMixin
from .agent_state import AgentStateMixin
from .agent_exec_memory import AgentExecMemoryMixin
from .agent_subagents import AgentSubagentsMixin
from .agent_tools import AgentToolsMixin
from .agent_run import AgentRunMixin


def _facade_init_chat_model(*args: Any, **kwargs: Any) -> Any:
    for module_name in ("openclaw_pi_langchain", "piagent"):
        facade = sys.modules.get(module_name)
        candidate = getattr(facade, "init_chat_model", None) if facade is not None else None
        if candidate is not None and candidate is not init_chat_model:
            return candidate(*args, **kwargs)
    return init_chat_model(*args, **kwargs)


def _local_bedrock_model_settings() -> tuple[Optional[str], dict[str, Any]]:
    env_names = (
        "LOCAL_BEDROCK_BASE_URL",
        "LOCAL_BEDROCK_MODEL_ID",
        "LOCAL_BEDROCK_API_KEY",
    )
    values = {name: str(os.getenv(name, "")).strip() for name in env_names}
    if not any(values.values()):
        return None, {}

    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f"Local Bedrock configuration is incomplete; missing: {', '.join(missing)}")

    parsed = urllib.parse.urlsplit(values["LOCAL_BEDROCK_BASE_URL"])
    hostname = str(parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or not hostname.startswith("bedrock-runtime.")
        or not hostname.endswith(".amazonaws.com")
    ):
        raise ValueError("LOCAL_BEDROCK_BASE_URL must be an HTTPS Amazon Bedrock Runtime endpoint")

    path = parsed.path.rstrip("/")
    if path not in {"", "/openai/v1"}:
        raise ValueError("LOCAL_BEDROCK_BASE_URL path must be empty or /openai/v1")
    base_url = urllib.parse.urlunsplit(("https", parsed.netloc, "/openai/v1", "", ""))

    return values["LOCAL_BEDROCK_MODEL_ID"], {
        "model_provider": "openai",
        "base_url": base_url,
        "api_key": values["LOCAL_BEDROCK_API_KEY"],
    }


class OpenClawPiLangChain(
    AgentHooksMixin,
    AgentRegistryMixin,
    AgentWorkNotesMixin,
    AgentStateMixin,
    AgentExecMemoryMixin,
    AgentSubagentsMixin,
    AgentToolsMixin,
    AgentRunMixin,
):
    def __init__(
        self,
        config: PiAgentConfig,
        extra_tools: Optional[Sequence[Any]] = None,
    ):
        self.config = config
        self.user_id = _sanitize_user_id(config.user_id)
        self.workspace_dir = config.workspace_path()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.guard = WorkspaceGuard(self.workspace_dir, config.blocked_paths, user_id=self.user_id)

        session_root = config.session_root()
        audit_root = config.audit_root()
        memory_root = config.memory_root()
        if self.user_id:
            session_root = session_root / "users" / self.user_id
            audit_root = audit_root / "users" / self.user_id
            memory_root = memory_root / "users" / self.user_id

        self.session_store = FlatSessionStore(session_root)
        self.session_fragment_store = SessionFragmentStore(session_root)
        self.evidence_store = SessionEvidenceStore(session_root)
        self.audit_logger = AuditLogger(audit_root)
        self.memory_store = FlatMemoryStore(memory_root)
        self.markdown_memory_store = OpenClawMarkdownMemoryStore(memory_root)
        self.memory_index = SqliteVecMemoryIndex(memory_root / "memory_vec.sqlite", self.audit_logger)
        self.embedding_client = MemoryEmbeddingClient(
            provider=config.memory_embedding_provider,
            model=config.memory_embedding_model,
        )
        self._active_session_id = "main"
        self._session_edit_path_scopes: dict[str, frozenset[Path]] = {}
        self._session_exec_failure_keys: dict[str, set[str]] = {}
        self._session_exec_failure_recent: dict[str, list[dict[str, Any]]] = {}
        self._session_mutation_ticks: dict[str, int] = {}
        self._session_turn_read_chars: dict[str, int] = {}
        self._pending_audit_events: list[tuple[str, dict[str, Any]]] = []
        self._tool_sources: dict[str, str] = {}
        self._mcp_clients: dict[str, McpStdioClient] = {}
        self.skills_by_id: dict[str, SkillSpec] = {}
        self._session_todo_items: dict[str, list[dict[str, Any]]] = {}
        self._session_plan_mode_overrides: dict[str, str] = {}
        self._session_artifact_path_map: dict[str, dict[str, str]] = {}
        self._session_evidence_cache: dict[str, list[dict[str, Any]]] = {}
        self._session_repeat_approval_remaining: dict[str, int] = {}
        self._session_notes: dict[str, list[str]] = {}
        self._ask_user_question: Optional[str] = None
        self._hooks = self._load_hooks()
        self._activate_python_package_target()

        local_bedrock_model, local_bedrock_kwargs = _local_bedrock_model_settings()
        effective_model = local_bedrock_model or config.model
        self.model = _facade_init_chat_model(
            effective_model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            **local_bedrock_kwargs,
        )
        self.compaction_model = _facade_init_chat_model(
            local_bedrock_model or config.compaction_model or config.model,
            temperature=0,
            max_tokens=1200,
            **local_bedrock_kwargs,
        )

        self.all_tools = self._build_tool_registry(extra_tools=extra_tools)
        self.all_tools = self._wrap_tools_with_hooks(self.all_tools)
        self.skills_by_id = self._discover_skills()

__all__ = ["OpenClawPiLangChain"]
