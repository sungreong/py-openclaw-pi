from __future__ import annotations

from types import SimpleNamespace

import httpx
from openai import OpenAI

from piagent.agent_core import _local_bedrock_model_settings
from piagent.markdown_loop import (
    LangChainEvidenceVerifier,
    MarkdownResearchLoop,
    McpMarkdownSearchBackend,
    Verification,
)


class _Backend:
    def __init__(self):
        self.searches: list[str] = []
        self.reads: list[str] = []

    def search(self, query: str, limit: int):
        self.searches.append(query)
        index = len(self.searches)
        return [
            {
                "root_id": "workspace",
                "relative_path": f"doc-{index}.md",
                "title": f"Document {index}",
                "score": 0.9,
            }
        ][:limit]

    def read(self, root_id: str, relative_path: str, max_lines: int):
        self.reads.append(relative_path)
        return {"text": f"evidence from {relative_path}", "max_lines": max_lines}


class _Writer:
    def __init__(self):
        self.calls = 0

    def draft(self, question, evidence):
        self.calls += 1
        return f"draft-{self.calls}-from-{len(evidence)}-documents"


class _Verifier:
    def __init__(self, verdicts: list[Verification]):
        self.verdicts = list(verdicts)
        self.calls = 0

    def verify(self, question, draft, evidence, iteration):
        verdict = self.verdicts[self.calls]
        self.calls += 1
        return verdict


def test_loop_stops_when_independent_verifier_accepts_evidence():
    backend = _Backend()
    writer = _Writer()
    verifier = _Verifier([Verification(True, "supported")])

    result = MarkdownResearchLoop(backend, writer, verifier).run("What changed?")

    assert result.stop_reason == "sufficient"
    assert result.answer == "draft-1-from-1-documents"
    assert result.iterations == 1
    assert result.query_history == ["What changed?"]
    assert backend.searches == ["What changed?"]
    assert writer.calls == verifier.calls == 1


def test_loop_refines_query_and_accumulates_new_evidence():
    backend = _Backend()
    verifier = _Verifier(
        [
            Verification(False, "missing implementation detail", "implementation detail"),
            Verification(True, "now supported"),
        ]
    )

    result = MarkdownResearchLoop(backend, _Writer(), verifier).run("Explain the loop", max_iterations=3)

    assert result.stop_reason == "sufficient"
    assert result.iterations == 2
    assert result.query_history == ["Explain the loop", "implementation detail"]
    assert len(result.evidence) == 2
    assert result.answer == "draft-2-from-2-documents"


def test_loop_enforces_max_iterations_even_when_verifier_keeps_rejecting():
    verifier = _Verifier(
        [
            Verification(False, "gap one", "query two"),
            Verification(False, "gap two", "query three"),
        ]
    )

    result = MarkdownResearchLoop(_Backend(), _Writer(), verifier).run("Question", max_iterations=2)

    assert result.stop_reason == "max_iterations"
    assert result.iterations == 2
    assert result.verification_reason == "gap two"


def test_loop_stops_on_repeated_refinement_query():
    result = MarkdownResearchLoop(
        _Backend(),
        _Writer(),
        _Verifier([Verification(False, "no progress", "Question")]),
    ).run("Question", max_iterations=5)

    assert result.stop_reason == "stalled"
    assert result.iterations == 1
    assert result.query_history == ["Question"]


def test_malformed_verifier_output_fails_closed():
    bad_model = SimpleNamespace(invoke=lambda _messages: SimpleNamespace(content="not-json"))
    result = MarkdownResearchLoop(
        _Backend(),
        _Writer(),
        LangChainEvidenceVerifier(bad_model),
    ).run("Question")

    assert result.stop_reason == "error"
    assert result.error.startswith("verification failed:")


class _McpClient:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "search_markdown":
            return {
                "structuredContent": {
                    "results": [
                        {
                            "rootId": "workspace",
                            "relativePath": "reports/loop.md",
                            "title": "Loop report",
                        }
                    ]
                }
            }
        return {"content": [{"type": "text", "text": '{"text":"verified markdown"}'}]}


def test_mcp_adapter_uses_markdown_search_tool_contract():
    client = _McpClient()
    backend = McpMarkdownSearchBackend(client, root_id="workspace")

    hits = backend.search("loop engineering", 4)
    document = backend.read("workspace", "reports/loop.md", 120)

    assert hits[0]["relativePath"] == "reports/loop.md"
    assert document["text"] == "verified markdown"
    assert client.calls == [
        (
            "search_markdown",
            {"root_id": "workspace", "query": "loop engineering", "limit": 4, "sort_by": "relevance"},
        ),
        (
            "read_markdown",
            {"root_id": "workspace", "relative_path": "reports/loop.md", "start_line": 1, "max_lines": 120},
        ),
    ]


def test_bedrock_url_reaches_openai_chat_completions_path_without_network(monkeypatch):
    monkeypatch.setenv("LOCAL_BEDROCK_BASE_URL", "https://bedrock-runtime.ap-northeast-1.amazonaws.com")
    monkeypatch.setenv("LOCAL_BEDROCK_MODEL_ID", "openai.gpt-oss-120b-1:0")
    monkeypatch.setenv("LOCAL_BEDROCK_API_KEY", "fake-contract-key")
    model_id, settings = _local_bedrock_model_settings()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "contract-test",
                "object": "chat.completion",
                "created": 0,
                "model": model_id,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = OpenAI(base_url=settings["base_url"], api_key=settings["api_key"], http_client=http_client)
    response = client.chat.completions.create(model=model_id, messages=[{"role": "user", "content": "ping"}])

    assert response.choices[0].message.content == "ok"
    assert str(requests[0].url) == (
        "https://bedrock-runtime.ap-northeast-1.amazonaws.com/openai/v1/chat/completions"
    )
    assert requests[0].headers["authorization"] == "Bearer fake-contract-key"
