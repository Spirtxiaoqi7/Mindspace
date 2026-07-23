"""Local entry point that runs without API keys or external services."""

from __future__ import annotations

import argparse
import json

from mindspace_graph.adapters.in_memory import demo_dependencies
from mindspace_graph.graph import build_graph
from mindspace_graph.models import ChatRequest


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Mindspace LangGraph decomposition")
    parser.add_argument("message", nargs="?", default="请演示一次调度")
    parser.add_argument("--diagram", action="store_true", help="print Mermaid graph source")
    args = parser.parse_args()

    graph = build_graph(demo_dependencies())
    if args.diagram:
        print(graph.get_graph().draw_mermaid())
        return

    request = ChatRequest(
        message=args.message, session_id="demo", retrieval={"similarity_threshold": 0}
    )
    result = graph.invoke({"request": request}, config={"recursion_limit": 20})
    print(json.dumps(result["response"].model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
