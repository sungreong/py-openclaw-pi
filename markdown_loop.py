from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from typing import Optional, Sequence

from piagent.agent_core import _facade_init_chat_model, _local_bedrock_model_settings
from piagent.markdown_loop import (
    LangChainDraftWriter,
    LangChainEvidenceVerifier,
    MarkdownResearchLoop,
    McpMarkdownSearchBackend,
)
from piagent.mcp import McpStdioClient


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Minimal markdown_search loop: search, read, draft, verify, refine")
    parser.add_argument("question", nargs="?", default="")
    parser.add_argument("--query", default="")
    parser.add_argument("--mcp-command", default="")
    parser.add_argument("--mcp-arg", action="append", default=[])
    parser.add_argument("--root-id", default="workspace")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    if args.check:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "loop": ["search", "read", "draft", "verify", "refine-or-stop"],
                    "required_mcp_tools": ["search_markdown", "read_markdown"],
                    "separate_writer_and_verifier": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    question = str(args.question).strip() or input("Question > ").strip()
    if not question:
        parser.error("question is required")
    if not str(args.mcp_command).strip():
        parser.error("--mcp-command is required (the repository does not bundle a markdown_search server)")

    bedrock_model, model_kwargs = _local_bedrock_model_settings()
    model_name = bedrock_model or os.getenv("PI_MODEL", "gpt-4o")
    if not bedrock_model and not os.getenv("OPENAI_API_KEY", "").strip():
        parser.error("set OPENAI_API_KEY or all LOCAL_BEDROCK_* variables")

    writer_model = _facade_init_chat_model(model_name, temperature=0, max_tokens=1800, **model_kwargs)
    verifier_model = _facade_init_chat_model(model_name, temperature=0, max_tokens=900, **model_kwargs)
    client = McpStdioClient("markdown_search", args.mcp_command, args.mcp_arg, {}, 30)
    client.start()
    try:
        loop = MarkdownResearchLoop(
            McpMarkdownSearchBackend(client, root_id=args.root_id),
            LangChainDraftWriter(writer_model),
            LangChainEvidenceVerifier(verifier_model),
        )
        result = loop.run(
            question,
            initial_query=args.query,
            max_iterations=args.max_iterations,
        )
    finally:
        client.close()

    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.stop_reason == "sufficient" else 2


if __name__ == "__main__":
    raise SystemExit(main())
