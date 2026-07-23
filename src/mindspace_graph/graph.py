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

    # 图分成五个阶段：装载与召回、能力调度、Prompt/生成、确定性校验、唯一提交。
    # 除两路召回和能力执行器内部的只读批次外，主图按阶段顺序推进。
    builder.add_node("validate_request", nodes.validate_request)
    builder.add_node("load_context", nodes.load_context)
    builder.add_node("retrieve_knowledge", nodes.retrieve_knowledge)
    builder.add_node("retrieve_chat", nodes.retrieve_chat)
    builder.add_node("rank_context", nodes.rank_context)
    builder.add_node("capability_route", nodes.capability_route)
    builder.add_node("plan_capabilities", nodes.plan_capabilities)
    builder.add_node("execute_capabilities", nodes.execute_capabilities)
    builder.add_node("review_capabilities", nodes.review_capabilities)
    builder.add_node("compose_prompt", nodes.compose_prompt)
    builder.add_node("generate_candidate", nodes.generate_candidate)
    builder.add_node("parse_protocol", nodes.parse_protocol)
    builder.add_node("repair_protocol", nodes.repair_protocol)
    builder.add_node("validate_role", nodes.validate_role)
    builder.add_node("validate_json_update", nodes.validate_json_update)
    builder.add_node("persist_turn", nodes.persist_turn)
    builder.add_node("finalize_error", nodes.finalize_error)

    builder.add_edge(START, "validate_request")
    builder.add_edge("validate_request", "load_context")
    builder.add_edge("load_context", "retrieve_knowledge")
    builder.add_edge("load_context", "retrieve_chat")
    # LangGraph 会并行执行这两个无写入分支；rank_context 等待两边都返回后再继续。
    builder.add_edge(["retrieve_knowledge", "retrieve_chat"], "rank_context")
    builder.add_edge("rank_context", "capability_route")
    builder.add_conditional_edges(
        "capability_route",
        nodes.route_capability_plan,
        {"planner": "plan_capabilities", "execute": "execute_capabilities"},
    )
    builder.add_edge("plan_capabilities", "execute_capabilities")
    builder.add_edge("execute_capabilities", "review_capabilities")
    builder.add_edge("review_capabilities", "compose_prompt")
    builder.add_edge("compose_prompt", "generate_candidate")
    builder.add_edge("generate_candidate", "parse_protocol")
    builder.add_conditional_edges(
        "parse_protocol",
        nodes.route_protocol,
        {"valid": "validate_role", "repair": "repair_protocol", "fail": "finalize_error"},
    )
    builder.add_edge("repair_protocol", "parse_protocol")
    # 此时 response.delta 可能已经展示并被 TTS 播放。角色校验失败只能阻止 JSON 写回，
    # 不能再调用模型替换用户已经看见或听见的正文。
    builder.add_edge("validate_role", "validate_json_update")
    builder.add_edge("validate_json_update", "persist_turn")
    builder.add_edge("persist_turn", END)
    builder.add_edge("finalize_error", END)
    return builder.compile(checkpointer=checkpointer)
