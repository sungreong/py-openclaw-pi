from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from .mcp import McpStdioClient


@dataclass(frozen=True)
class MarkdownEvidence:
    root_id: str
    relative_path: str
    title: str
    text: str
    score: float = 0.0


@dataclass(frozen=True)
class Verification:
    sufficient: bool
    reason: str
    next_query: str = ""


@dataclass(frozen=True)
class MarkdownLoopResult:
    answer: str
    evidence: list[MarkdownEvidence]
    iterations: int
    query_history: list[str]
    stop_reason: str
    verification_reason: str
    error: str = ""


class MarkdownSearchBackend(Protocol):
    def search(self, query: str, limit: int) -> list[dict[str, Any]]: ...

    def read(self, root_id: str, relative_path: str, max_lines: int) -> dict[str, Any]: ...


class DraftWriter(Protocol):
    def draft(self, question: str, evidence: list[MarkdownEvidence]) -> str: ...


class EvidenceVerifier(Protocol):
    def verify(
        self,
        question: str,
        draft: str,
        evidence: list[MarkdownEvidence],
        iteration: int,
    ) -> Verification: ...


class _LoopState(TypedDict, total=False):
    question: str
    query: str
    max_iterations: int
    search_limit: int
    read_limit: int
    iteration: int
    hits: list[dict[str, Any]]
    evidence: list[MarkdownEvidence]
    draft: str
    verification: Verification
    query_history: list[str]
    stop_reason: str
    error: str


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("model response must be a JSON object")
    return value


class LangChainDraftWriter:
    def __init__(self, model: Any):
        self.model = model

    def draft(self, question: str, evidence: list[MarkdownEvidence]) -> str:
        sources = json.dumps([asdict(item) for item in evidence], ensure_ascii=False)
        response = self.model.invoke(
            [
                ("system", "Answer only from the supplied markdown evidence. State uncertainty when evidence is missing."),
                ("user", f"Question:\n{question}\n\nEvidence JSON:\n{sources}"),
            ]
        )
        return _message_text(response).strip()


class LangChainEvidenceVerifier:
    def __init__(self, model: Any):
        self.model = model

    def verify(
        self,
        question: str,
        draft: str,
        evidence: list[MarkdownEvidence],
        iteration: int,
    ) -> Verification:
        sources = json.dumps([asdict(item) for item in evidence], ensure_ascii=False)
        response = self.model.invoke(
            [
                (
                    "system",
                    "Independently verify whether the draft answers the question using only the evidence. "
                    "Return one JSON object with keys sufficient (boolean), reason (string), next_query (string). "
                    "If insufficient, next_query must target the most important missing fact.",
                ),
                (
                    "user",
                    f"Iteration: {iteration}\nQuestion:\n{question}\n\nDraft:\n{draft}\n\nEvidence JSON:\n{sources}",
                ),
            ]
        )
        payload = _json_object(_message_text(response))
        sufficient = payload.get("sufficient")
        if not isinstance(sufficient, bool):
            raise ValueError("verifier response.sufficient must be boolean")
        return Verification(
            sufficient=sufficient,
            reason=str(payload.get("reason", "")).strip(),
            next_query=str(payload.get("next_query", "")).strip(),
        )


def _mcp_payload(result: dict[str, Any]) -> Any:
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    texts: list[str] = []
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            texts.append(str(item.get("text", "")))
    text = "\n".join(texts).strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


class McpMarkdownSearchBackend:
    """Adapter for an already-started markdown_search-compatible stdio MCP client."""

    def __init__(self, client: McpStdioClient, root_id: str = "workspace"):
        self.client = client
        self.root_id = str(root_id).strip() or "workspace"

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        payload = _mcp_payload(
            self.client.call_tool(
                "search_markdown",
                {"root_id": self.root_id, "query": query, "limit": int(limit), "sort_by": "relevance"},
            )
        )
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = next(
                (payload[key] for key in ("results", "hits", "matches") if isinstance(payload.get(key), list)),
                [],
            )
        else:
            rows = []
        return [row for row in rows if isinstance(row, dict)]

    def read(self, root_id: str, relative_path: str, max_lines: int) -> dict[str, Any]:
        payload = _mcp_payload(
            self.client.call_tool(
                "read_markdown",
                {
                    "root_id": root_id or self.root_id,
                    "relative_path": relative_path,
                    "start_line": 1,
                    "max_lines": int(max_lines),
                },
            )
        )
        return payload if isinstance(payload, dict) else {"text": str(payload)}


def _hit_value(hit: dict[str, Any], *names: str) -> str:
    for name in names:
        value = hit.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _read_text(payload: dict[str, Any]) -> str:
    for key in ("text", "content", "markdown", "body"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return json.dumps(payload, ensure_ascii=False)


class MarkdownResearchLoop:
    def __init__(
        self,
        backend: MarkdownSearchBackend,
        writer: DraftWriter,
        verifier: EvidenceVerifier,
    ):
        self.backend = backend
        self.writer = writer
        self.verifier = verifier
        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(_LoopState)
        graph.add_node("search", self._search)
        graph.add_node("read", self._read)
        graph.add_node("draft", self._draft)
        graph.add_node("verify", self._verify)
        graph.add_node("refine", self._refine)
        graph.add_edge(START, "search")
        graph.add_conditional_edges("search", self._route_error, {"continue": "read", "stop": END})
        graph.add_conditional_edges("read", self._route_error, {"continue": "draft", "stop": END})
        graph.add_edge("draft", "verify")
        graph.add_conditional_edges(
            "verify",
            self._route_verification,
            {"refine": "refine", "stop": END},
        )
        graph.add_edge("refine", "search")
        return graph.compile()

    @staticmethod
    def _route_error(state: _LoopState) -> str:
        return "stop" if state.get("error") else "continue"

    @staticmethod
    def _route_verification(state: _LoopState) -> str:
        return "refine" if not state.get("stop_reason") else "stop"

    def _search(self, state: _LoopState) -> dict[str, Any]:
        query = str(state.get("query", "")).strip()
        history = list(state.get("query_history", []))
        if not query or query in history:
            return {"stop_reason": "stalled", "error": "query is empty or repeated"}
        try:
            hits = self.backend.search(query, int(state.get("search_limit", 5)))
        except Exception as exc:
            return {"stop_reason": "error", "error": f"search failed: {exc}"}
        return {
            "hits": hits,
            "iteration": int(state.get("iteration", 0)) + 1,
            "query_history": [*history, query],
        }

    def _read(self, state: _LoopState) -> dict[str, Any]:
        accumulated = list(state.get("evidence", []))
        known = {(item.root_id, item.relative_path) for item in accumulated}
        try:
            for hit in state.get("hits", []):
                root_id = _hit_value(hit, "root_id", "rootId") or "workspace"
                path = _hit_value(hit, "relative_path", "relativePath", "path")
                if not path or (root_id, path) in known:
                    continue
                payload = self.backend.read(root_id, path, int(state.get("read_limit", 240)))
                accumulated.append(
                    MarkdownEvidence(
                        root_id=root_id,
                        relative_path=path,
                        title=_hit_value(hit, "title", "name") or path,
                        text=_read_text(payload),
                        score=float(hit.get("score", hit.get("rank", 0.0)) or 0.0),
                    )
                )
                known.add((root_id, path))
        except Exception as exc:
            return {"stop_reason": "error", "error": f"read failed: {exc}"}
        return {"evidence": accumulated}

    def _draft(self, state: _LoopState) -> dict[str, Any]:
        try:
            answer = self.writer.draft(state["question"], list(state.get("evidence", [])))
        except Exception as exc:
            return {"draft": "", "stop_reason": "error", "error": f"draft failed: {exc}"}
        return {"draft": str(answer).strip()}

    def _verify(self, state: _LoopState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        try:
            verdict = self.verifier.verify(
                state["question"],
                state.get("draft", ""),
                list(state.get("evidence", [])),
                int(state.get("iteration", 0)),
            )
        except Exception as exc:
            return {"stop_reason": "error", "error": f"verification failed: {exc}"}
        if verdict.sufficient:
            return {"verification": verdict, "stop_reason": "sufficient"}
        if int(state.get("iteration", 0)) >= int(state.get("max_iterations", 3)):
            return {"verification": verdict, "stop_reason": "max_iterations"}
        next_query = verdict.next_query.strip()
        if not next_query or next_query in state.get("query_history", []):
            return {"verification": verdict, "stop_reason": "stalled"}
        return {"verification": verdict, "query": next_query, "stop_reason": ""}

    @staticmethod
    def _refine(state: _LoopState) -> dict[str, Any]:
        return {"hits": [], "stop_reason": "", "error": ""}

    def run(
        self,
        question: str,
        *,
        initial_query: str = "",
        max_iterations: int = 3,
        search_limit: int = 5,
        read_limit: int = 240,
    ) -> MarkdownLoopResult:
        clean_question = str(question).strip()
        if not clean_question:
            raise ValueError("question is required")
        final = self.graph.invoke(
            {
                "question": clean_question,
                "query": str(initial_query).strip() or clean_question,
                "max_iterations": max(1, int(max_iterations)),
                "search_limit": max(1, int(search_limit)),
                "read_limit": max(1, int(read_limit)),
                "iteration": 0,
                "hits": [],
                "evidence": [],
                "draft": "",
                "query_history": [],
                "stop_reason": "",
                "error": "",
            }
        )
        verdict = final.get("verification")
        return MarkdownLoopResult(
            answer=str(final.get("draft", "")),
            evidence=list(final.get("evidence", [])),
            iterations=int(final.get("iteration", 0)),
            query_history=list(final.get("query_history", [])),
            stop_reason=str(final.get("stop_reason", "error")),
            verification_reason=verdict.reason if isinstance(verdict, Verification) else "",
            error=str(final.get("error", "")),
        )


__all__ = [
    "DraftWriter",
    "EvidenceVerifier",
    "LangChainDraftWriter",
    "LangChainEvidenceVerifier",
    "MarkdownEvidence",
    "MarkdownLoopResult",
    "MarkdownResearchLoop",
    "MarkdownSearchBackend",
    "McpMarkdownSearchBackend",
    "Verification",
]
