from mindspace_graph.tool_chain import ToolExecutionResult, enforce_tool_claims


def _web_result() -> ToolExecutionResult:
    return ToolExecutionResult(
        call_id="call_web",
        tool="web",
        level=3,
        status="success",
        parameter_summary="北京明天天气",
        source_count=2,
    )


def test_web_turn_replaces_false_event_memory_saved_claim() -> None:
    response = "我帮你查了天气。明早八点的提醒我记下了，但还没存进系统。"

    guarded, violations = enforce_tool_claims(response, _web_result())

    assert "我帮你查了天气" in guarded
    assert "我记下了" not in guarded
    assert "这条提醒尚未保存，请下一轮单独确认" in guarded
    assert violations


def test_web_turn_keeps_truthful_not_saved_statement() -> None:
    response = "我帮你查了天气。这条提醒尚未保存，请下一轮单独确认。"

    guarded, violations = enforce_tool_claims(response, _web_result())

    assert guarded == response
    assert violations == []
