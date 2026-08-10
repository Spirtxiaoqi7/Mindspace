"""LangGraph topology for one Mindspace conversational turn."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from mindspace_graph.nodes import NodeFactory
from mindspace_graph.ports import Dependencies
from mindspace_graph.state import TurnState


def build_graph(dependencies: Dependencies, *, checkpointer: Any | None = None):
    """构建单轮前台对话图。

    这里是判断“某个函数是否真的在线上执行”的唯一拓扑依据。NodeFactory 中即使存在
    同名方法，只要没有在这里注册并连边，就不是当前主链路的一部分。
    """

    nodes = NodeFactory(dependencies)
    builder = StateGraph(TurnState)

    # 单轮只允许一次 provider-native function call；工具结果不会进入普通聊天历史。
    builder.add_node("validate_request", nodes.validate_request)
    builder.add_node("load_context", nodes.load_context)
    builder.add_node("retrieve_chat", nodes.retrieve_chat)
    builder.add_node("rank_context", nodes.rank_context)
    builder.add_node("tool_hint", nodes.tool_hint)
    builder.add_node("compose_prompt", nodes.compose_prompt)
    builder.add_node("generate_candidate", nodes.generate_candidate)
    builder.add_node("authorize_tool", nodes.authorize_tool)
    builder.add_node("review_task", nodes.review_task)
    builder.add_node("execute_tool", nodes.execute_tool)
    builder.add_node("inject_result", nodes.inject_tool_result)
    builder.add_node("generate_final", nodes.generate_final)
    builder.add_node("parse_protocol", nodes.parse_protocol)
    builder.add_node("validate_role", nodes.validate_role)
    builder.add_node("validate_json_update", nodes.validate_json_update)
    builder.add_node("persist_turn", nodes.persist_turn)
    builder.add_node("finalize_error", nodes.finalize_error)

    builder.add_edge(START, "validate_request")
    builder.add_edge("validate_request", "load_context")
    builder.add_edge("load_context", "retrieve_chat")
    builder.add_edge("retrieve_chat", "rank_context")
    builder.add_edge("rank_context", "tool_hint")
    builder.add_edge("tool_hint", "compose_prompt")
    builder.add_edge("compose_prompt", "generate_candidate")
    builder.add_conditional_edges(
        "generate_candidate",
        nodes.route_tool_request,
        {"tool": "authorize_tool", "answer": "parse_protocol"},
    )
    builder.add_conditional_edges(
        "authorize_tool",
        nodes.route_tool_authorization,
        {"task": "review_task", "execute": "execute_tool", "inject": "inject_result"},
    )
    builder.add_conditional_edges(
        "review_task",
        nodes.route_task_review,
        {"execute": "execute_tool", "inject": "inject_result"},
    )
    builder.add_edge("execute_tool", "inject_result")
    builder.add_edge("inject_result", "generate_final")
    builder.add_edge("generate_final", "parse_protocol")
    builder.add_conditional_edges(
        "parse_protocol",
        nodes.route_protocol,
        {"valid": "validate_role", "fail": "finalize_error"},
    )
    # Foreground role validation is deterministic diagnostics only.  Semantic
    # role review and continuity summarization run after the visible turn, so a
    # second model call can never delay the current reply.
    builder.add_edge("validate_role", "validate_json_update")
    builder.add_edge("validate_json_update", "persist_turn")
    builder.add_edge("persist_turn", END)
    builder.add_edge("finalize_error", END)
    return builder.compile(checkpointer=checkpointer)
