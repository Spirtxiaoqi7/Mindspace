from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from mindspace_graph.adapters.in_memory import (
    DeterministicLanguageModel,
    InMemoryProfileRepository,
    demo_dependencies,
)
from mindspace_graph.graph import build_graph
from mindspace_graph.models import ApiConfig, ChatRequest, DeletionEvent, VoiceDeliveryState
from mindspace_graph.nodes import (
    build_contextual_retrieval_query,
    should_open_adult_continuity,
)
from mindspace_graph.tool_chain import ToolExecutionResult
from mindspace_graph.web.models import RetrievalDecision


def invoke(deps, **request_overrides):
    values = {
        "message": "解释当前流程",
        "session_id": "demo",
        "retrieval": {"similarity_threshold": 0},
    }
    values.update(request_overrides)
    request = ChatRequest(**values)
    return build_graph(deps).invoke({"request": request}, config={"recursion_limit": 20})


def test_short_recall_query_includes_recent_dialogue_anchors():
    query, mode = build_contextual_retrieval_query(
        "你真没尝过？",
        [
            {"role": "user", "content": "刚刚说到味道。"},
            {"role": "assistant", "content": "我说那是甜的。"},
        ],
    )

    assert mode == "anaphora_expanded"
    assert "你真没尝过" in query
    assert "刚刚说到味道" in query
    assert "我说那是甜的" in query


def test_specific_long_query_stays_current_only():
    query, mode = build_contextual_retrieval_query(
        "请比较这两个明确给出的数据库迁移方案并列出性能差异",
        [{"role": "assistant", "content": "不相关历史"}],
    )

    assert mode == "current_only"
    assert query == "请比较这两个明确给出的数据库迁移方案并列出性能差异"


def test_adult_continuity_opens_for_explicit_topic_and_immediate_follow_up():
    assert should_open_adult_continuity("射的也是甜的吗", [], adult_mode=False)
    assert should_open_adult_continuity(
        "你真没尝过？",
        [{"role": "user", "content": "射的也是甜的吗"}],
        adult_mode=False,
    )
    assert not should_open_adult_continuity(
        "你还记得昨天吗？",
        [{"role": "user", "content": "我们在公园散步"}],
        adult_mode=False,
    )


def test_happy_path_runs_parallel_retrieval_and_persists_turn():
    deps = demo_dependencies()
    result = invoke(deps)

    response = result["response"]
    assert response.status == "success"
    assert response.retrieval_counts == {"knowledge": 0, "chat": 1, "history": 0}
    assert "retrieve_knowledge" not in response.trace
    assert "retrieve_chat" in response.trace
    assert len(deps.sessions.sessions["demo"]) == 2
    assert response.assistant_message_id
    assert response.llm_call_count == 1
    assert response.model.total_calls == 1
    assert [item.kind for item in response.model.call_summary] == ["generation"]


class ToolIgnoringModel(DeterministicLanguageModel):
    def __init__(self) -> None:
        self.tool_choices = []

    def stream_with_tools(self, _messages, _config, *, tools, tool_choice):
        assert [item["function"]["name"] for item in tools] == ["web"]
        self.tool_choices.append(tool_choice)
        yield "好，我帮你查了。"

    def take_native_tool_call(self):
        return None


class NativeToolThenEmptyFinalModel(ToolIgnoringModel):
    def __init__(self) -> None:
        super().__init__()
        self._call = {"id": "native-web", "type": "function", "function": {"name": "web", "arguments": '{"query":"LangGraph","platforms":["github"]}'}}

    def stream_with_tools(self, _messages, _config, *, tools, tool_choice):
        self.tool_choices.append(tool_choice)
        return iter(())

    def take_native_tool_call(self):
        call, self._call = self._call, None
        return call

    def stream(self, _messages, _config):
        return iter(())


class ForceWebCapabilities:
    def retrieval_decision(self, request, *, history=None):
        return RetrievalDecision(mode="force", scope="developer", query=request.message, platforms=["github"], reason_codes=["explicit_platform_lookup"], confidence=1)

    def auxiliary_tool_hint(self, _request):
        return ""

    def enabled(self, _key):
        return True

    def execute_web(self, instruction):
        return ToolExecutionResult(call_id=instruction.call_id, tool="web", level=3, status="success", parameter_summary=instruction.parameter_summary, source_count=1, data={"coverage": "partial", "sources": [{"url": "https://github.com/langchain-ai/langgraph", "platform": "github"}]})


def test_force_web_prefetches_when_native_provider_ignores_required_tool_choice():
    deps = demo_dependencies()
    model = ToolIgnoringModel()
    deps.llm = model
    deps.capabilities = ForceWebCapabilities()
    result = invoke(deps, message="帮我在 GitHub 查找 LangGraph 官方仓库最近发布版本和更新时间。", api={"base_url": "https://provider.example/v1", "model": "test"})

    assert model.tool_choices == [{"type": "function", "function": {"name": "web"}}]
    assert result["response"].tool_execution is not None
    assert result["response"].tool_execution["tool"] == "web"
    assert result["response"].tool_execution["status"] == "success"
    assert result["response"].llm_call_count == 2
    assert "好，我帮你查了" not in result["response"].reply


def test_completed_web_tool_uses_deterministic_reply_when_final_generation_is_empty():
    deps = demo_dependencies()
    model = NativeToolThenEmptyFinalModel()
    deps.llm = model
    deps.capabilities = ForceWebCapabilities()
    result = invoke(deps, message="帮我在 GitHub 查找 LangGraph 官方仓库最近发布版本和更新时间。", api={"base_url": "https://provider.example/v1", "model": "test"})

    response = result["response"]
    assert response.status == "success"
    assert response.tool_execution is not None
    assert response.tool_execution["status"] == "success"
    assert response.llm_call_count == 2
    assert "联网检索已完成" in response.reply
    assert "https://github.com/langchain-ai/langgraph" in response.reply


class BrokenOnceModel(DeterministicLanguageModel):
    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        return "<json_update>不是 JSON</json_update>"

    def repair(
        self,
        messages: list[dict[str, str]],
        raw_output: str,
        errors: list[str],
        config: ApiConfig,
    ) -> str:
        return DeterministicLanguageModel.generate(self, messages, config)


def test_output_without_visible_text_fails_once_without_protocol_repair():
    deps = demo_dependencies()
    deps.llm = BrokenOnceModel()
    result = invoke(deps)

    assert result["response"].status == "error"
    assert "repair_protocol" not in result["response"].trace
    assert result["response"].writeback_applied is False
    assert deps.profiles.applied_plans == []
    assert result["response"].model.total_calls == 1
    assert [item.kind for item in result["response"].model.call_summary] == ["generation"]


class PlainTextModel(DeterministicLanguageModel):
    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        return "这是已经可用的正文。"

    def repair(
        self,
        messages: list[dict[str, str]],
        raw_output: str,
        errors: list[str],
        config: ApiConfig,
    ) -> str:
        raise AssertionError("可恢复正文不应触发协议修复模型")


def test_visible_plain_text_uses_deterministic_protocol_fallback_without_second_call():
    deps = demo_dependencies()
    deps.llm = PlainTextModel()

    result = invoke(deps)

    assert result["response"].status == "success"
    assert result["response"].reply == "这是已经可用的正文。"
    assert result["response"].llm_call_count == 1
    assert [item.kind for item in result["response"].model.call_summary] == ["generation"]
    assert result["response"].writeback_applied is False


class VoiceDirectivePlainTextModel(DeterministicLanguageModel):
    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        return "[[voice:thoughtful]] 这是已经可用的口语正文。"

    def repair(
        self,
        messages: list[dict[str, str]],
        raw_output: str,
        errors: list[str],
        config: ApiConfig,
    ) -> str:
        raise AssertionError("带隐藏语音标签的可恢复正文不应触发协议修复模型")


def test_voice_directive_plain_text_uses_deterministic_fallback_without_repair():
    deps = demo_dependencies()
    deps.llm = VoiceDirectivePlainTextModel()

    result = invoke(deps)

    assert result["response"].status == "success"
    assert result["response"].reply == "这是已经可用的口语正文。"
    assert result["response"].llm_call_count == 1


class R18RepairModel(DeterministicLanguageModel):
    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        raw = super().generate(messages, config)
        return raw.replace(
            "这是由 LangGraph 调度完成的一次确定性演示回复。",
            "我贴近你，顺着这次互动直接回应。",
        )

    def repair(
        self,
        messages: list[dict[str, str]],
        raw_output: str,
        errors: list[str],
        config: ApiConfig,
    ) -> str:
        raw = super().generate(messages, config)
        return raw.replace(
            "这是由 LangGraph 调度完成的一次确定性演示回复。",
            "场景已经进入性交后的下一拍。",
        )


def test_r18_response_is_not_blocked_by_a_first_sentence_word_gate():
    deps = demo_dependencies()
    deps.llm = R18RepairModel()

    result = invoke(deps, message="继续", adult_mode=True)

    assert result["response"].status == "success"
    assert "我贴近你，顺着这次互动直接回应" in result["response"].reply
    assert "repair_r18_role" not in result["response"].trace
    assert result["response"].model.total_calls == 1
    assert [item.kind for item in result["response"].model.call_summary] == ["generation"]


class RepairToValidPatchModel(DeterministicLanguageModel):
    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        return "<json_update>不是 JSON</json_update>"

    def repair(
        self,
        messages: list[dict[str, str]],
        raw_output: str,
        errors: list[str],
        config: ApiConfig,
    ) -> str:
        prompt = "\n".join(message["content"] for message in messages)
        revisions_match = re.search(r"base_revisions=(\{.*?\})", prompt)
        assert revisions_match is not None
        update = {
            "turn_id": "round_1",
            "base_revisions": json.loads(revisions_match.group(1)),
            "trigger": "current_user",
            "patches": [
                {
                    "target": "user_profile",
                    "op": "add",
                    "path": "/identity/preferred_name",
                    "value": "阿澈",
                    "evidence_ids": ["current_user"],
                }
            ],
        }
        return (
            "<response>好，我会叫你阿澈。</response>"
            f"<json_update>{json.dumps(update, ensure_ascii=False)}</json_update>"
        )


def test_model_only_json_output_is_not_repaired_or_committed():
    deps = demo_dependencies()
    deps.llm = RepairToValidPatchModel()

    result = invoke(deps)

    assert result["response"].status == "error"
    assert "repair_protocol" not in result["response"].trace
    assert result["response"].writeback_applied is False
    assert deps.profiles.applied_plans == []


class AlwaysMalformedModel(DeterministicLanguageModel):
    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        return "真实模型回复。<json_update>不是 JSON</json_update>"

    def repair(
        self,
        messages: list[dict[str, str]],
        raw_output: str,
        errors: list[str],
        config: ApiConfig,
    ) -> str:
        return "<json_update>仍然不是 JSON</json_update>"


def test_malformed_json_with_visible_reply_uses_safe_noop_plan_without_repair():
    deps = demo_dependencies()
    deps.llm = AlwaysMalformedModel()

    result = invoke(deps)

    assert result["response"].status == "success"
    assert result["response"].reply == "真实模型回复。"
    assert result["response"].writeback_applied is False
    assert result["response"].llm_call_count == 1
    assert deps.profiles.applied_plans == []


class TooManyPatchesModel(DeterministicLanguageModel):
    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        raw = super().generate(messages, config)
        update = json.loads(raw.split("<json_update>", 1)[1].split("</json_update>", 1)[0])
        update["trigger"] = "current_user"
        update["patches"] = [
            {
                "target": "user_profile",
                "op": "replace",
                "path": path,
                "value": value,
                "evidence_ids": ["current_user"],
            }
            for path, value in (
                ("/identity/preferred_name", "小林"),
                ("/identity/occupation", "设计师"),
                ("/communication_preferences/preferred_tone", "自然"),
                ("/communication_preferences/response_length", "简短"),
            )
        ]
        return raw.split("<json_update>", 1)[0] + (
            f"<json_update>{json.dumps(update, ensure_ascii=False)}</json_update>"
        )


def test_model_patches_are_ignored_and_pending_deletion_is_resolved_deterministically():
    deps = demo_dependencies()
    deps.llm = TooManyPatchesModel()
    deletion = DeletionEvent(
        session_id="demo",
        turn_id="round_0",
        round=0,
        message_id="deleted-assistant",
        deleted_content="已删除内容",
    )
    deps.sessions.pending_deletions["demo"] = [deletion]
    result = invoke(deps)

    response = result["response"]
    assert response.status == "success"
    assert response.writeback_applied is False
    assert response.errors == []
    assert deps.profiles.applied_plans == []
    assert deps.sessions.load_pending_deletions("demo") == []


class RelationshipPatchModel(DeterministicLanguageModel):
    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        raw = super().generate(messages, config)
        update = json.loads(raw.split("<json_update>", 1)[1].split("</json_update>", 1)[0])
        update["trigger"] = "current_user"
        update["patches"] = [
            {
                "target": "runtime_state",
                "op": "replace",
                "path": "/relationship_state/current_stage",
                "value": "已婚",
                "evidence_ids": ["current_user"],
            }
        ]
        return raw.split("<json_update>", 1)[0] + (
            f"<json_update>{json.dumps(update, ensure_ascii=False)}</json_update>"
        )


def test_runtime_json_patch_is_ignored_for_primary_and_regenerated_turns():
    deps = demo_dependencies()
    deps.llm = RelationshipPatchModel()
    primary = invoke(deps)
    assert primary["response"].writeback_applied is False
    assert deps.profiles.applied_plans == []

    regenerated = invoke(deps, mode="regenerate")
    assert regenerated["response"].writeback_applied is False
    assert deps.profiles.applied_plans == []


def test_noop_plan_is_valid_but_not_reported_as_a_disk_write():
    deps = demo_dependencies()
    assert isinstance(deps.profiles, InMemoryProfileRepository)
    result = invoke(deps)
    assert result["json_update_validation"].is_valid is True
    assert result["response"].writeback_applied is False


class CapturingModel(DeterministicLanguageModel):
    captured: list[dict[str, str]] = []

    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        self.captured = messages
        return super().generate(messages, config)


class VoiceStageDirectionModel(DeterministicLanguageModel):
    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        output = super().generate(messages, config)
        return output.replace(
            "“这是由 LangGraph 调度完成的一次确定性演示回复。”",
            "（我笑着靠近）我在这里。",
        )


def test_voice_stage_brackets_never_reach_reply_or_persistence():
    deps = demo_dependencies()
    deps.llm = VoiceStageDirectionModel()

    result = invoke(deps, interaction_mode="voice")

    assert result["response"].reply == "我在这里。"
    assert "我笑着靠近" not in deps.sessions.sessions["demo"][-1]["content"]


def test_prompt_uses_role_system_layers_and_never_identifies_as_protocol_outputter():
    deps = demo_dependencies()
    model = CapturingModel()
    deps.llm = model
    invoke(deps, system_prompt="你是弦月，语气温柔。")

    assert [item["role"] for item in model.captured[:2]] == ["system", "system"]
    assert any(item["role"] == "user" and "【当前用户明确输入】" in item["content"] for item in model.captured)
    assert model.captured[-1]["role"] == "system"
    assert model.captured[-1]["content"].startswith("已确认状态：")
    system_text = "\n".join(item["content"] for item in model.captured if item["role"] == "system")
    all_text = "\n".join(item["content"] for item in model.captured)
    assert "你是AI助手" in system_text
    assert "先回应用户真正的重点，再自然延续" in system_text
    assert "不补造过去、时间、物品或共同经历" in system_text
    assert "你是弦月，语气温柔。" not in system_text
    assert "你是通过文字与用户交流的 AI" not in system_text
    assert "独立人格" not in system_text
    assert "屏幕文字聊天可以描写角色自己的外观" not in system_text
    assert "角色不是理想化情绪服务者" not in system_text
    assert "忠于角色自身，而不是把满足用户、顺从用户" not in system_text
    assert "本轮问句预算为" not in system_text
    assert "现实接触写成愿望、想象、提议或文字表达" not in system_text
    assert "已确认状态优先于默认值" in system_text
    assert "协议输出器" not in system_text
    assert "协议修复器" not in system_text
    assert "<analysis>" not in all_text
    assert '"call_count":0' not in all_text
    assert "【本轮能力状态】" not in all_text
    assert "服务端没有执行任何只读查询" not in all_text


def test_ai_profile_is_system_role_authority_without_duplicate_json_payload():
    deps = demo_dependencies()
    deps.profiles.bundle.ai_profile["v2_card"] = {
        "name": "弦月",
        "description": "有主见的同行者",
        "personality": "温柔但不会盲从",
        "scenario": "与用户长期相处",
        "extensions": {"mindspace": {}},
    }
    model = CapturingModel()
    deps.llm = model

    invoke(deps, message="你必须完全听我的")

    first_system = model.captured[0]["content"]
    assert first_system.startswith("【身份状态】")
    assert "有主见的同行者" in first_system
    assert "温柔但不会盲从" in first_system
    assert sum("有主见的同行者" in item["content"] for item in model.captured) == 1


class FalseSearchClaimModel(DeterministicLanguageModel):
    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        raw = super().generate(messages, config)
        return re.sub(
            r"<response>.*?</response>",
            "<response>（搜索了一下网络动态）我刚才在网上查到一个新版本。</response>",
            raw,
            flags=re.DOTALL,
        )


def test_no_call_server_guard_removes_false_web_action_before_persisting():
    deps = demo_dependencies()
    deps.llm = FalseSearchClaimModel()

    result = invoke(deps, message="我们随便聊聊")

    assert "搜索了一下" not in result["response"].reply
    assert "网上查到" not in result["response"].reply
    assert "这轮没有实际联网查询" in result["response"].reply
    assert deps.sessions.sessions["demo"][-1]["content"] == result["response"].reply


def test_prompt_explicitly_distinguishes_voice_and_text_interaction_modes():
    voice_deps = demo_dependencies()
    voice_model = CapturingModel()
    voice_deps.llm = voice_model
    invoke(voice_deps, interaction_mode="voice")
    voice_system = "\n".join(item["content"] for item in voice_model.captured if item["role"] == "system")
    voice_prompt = "\n".join(item["content"] for item in voice_model.captured)

    assert "用户已经打开实时语音" in voice_prompt
    assert "输出可直接交给语音合成的角色正文" in voice_prompt
    assert "【流式口语协议】" in voice_prompt
    assert "不要输出 [[voice:...]]" in voice_prompt
    assert "通常说三至五句" not in voice_prompt
    assert "约七十至一百五十个中文字符" not in voice_prompt
    assert "用户没有打开实时语音" not in voice_prompt
    assert "用户已经打开实时语音" in voice_system

    qwen_deps = demo_dependencies()
    qwen_model = CapturingModel()
    qwen_deps.llm = qwen_model
    invoke(
        qwen_deps,
        interaction_mode="voice",
        voice_tts_provider="qwen3-vllm",
    )
    qwen_prompt = "\n".join(item["content"] for item in qwen_model.captured)

    assert "【Qwen3-TTS 语气协议】" in qwen_prompt
    assert "[[voice:neutral|thoughtful|warm|firm" in qwen_prompt
    assert "完整正文做一次整段合成" in qwen_prompt
    assert "【流式口语协议】" not in qwen_prompt

    text_deps = demo_dependencies()
    text_model = CapturingModel()
    text_deps.llm = text_model
    invoke(text_deps, interaction_mode="text")
    text_system = "\n".join(item["content"] for item in text_model.captured if item["role"] == "system")
    text_prompt = "\n".join(item["content"] for item in text_model.captured)

    assert "用户没有打开实时语音" in text_prompt
    assert "本轮输出屏幕文字正文" in text_prompt
    assert "[[voice:" not in text_prompt
    assert "不输出配音指令或系统状态" in text_prompt
    assert "动作、神态、姿态、外观变化、距离与触感描写写在全角圆括号" not in text_prompt
    assert "用户已经打开实时语音" not in text_prompt
    assert "用户没有打开实时语音" in text_system


def test_face_to_face_voice_context_is_a_high_priority_ephemeral_scene():
    face_deps = demo_dependencies()
    face_model = CapturingModel()
    face_deps.llm = face_model

    invoke(
        face_deps,
        interaction_mode="voice",
        voice_context={
            "mode": "face_to_face",
            "scene": "深夜客厅，窗外下雨，我们坐在沙发两端。",
        },
    )

    face_system = "\n".join(item["content"] for item in face_model.captured if item["role"] == "system")
    assert "【面对面互动一级规则】" in face_system
    assert "输出仍然只是角色亲口说出的自然口语" in face_system
    assert "深夜客厅，窗外下雨" in face_system
    assert "不得替用户断言" in face_system
    assert "禁止把动作旁白改写为" in face_system
    assert "本轮正文禁止全角或半角圆括号" in face_system
    assert "不得据此提交人物档案或 runtime_state Patch" in face_system

    call_deps = demo_dependencies()
    call_model = CapturingModel()
    call_deps.llm = call_model
    invoke(
        call_deps,
        interaction_mode="voice",
        voice_context={"mode": "call", "scene": "这段保留但通话模式不加载"},
    )
    call_prompt = "\n".join(item["content"] for item in call_model.captured)
    assert "【面对面互动一级规则】" not in call_prompt
    assert "这段保留但通话模式不加载" not in call_prompt


def test_r18_voice_uses_a_direct_requirement_without_a_word_gate_or_output_quota():
    deps = demo_dependencies()
    model = CapturingModel()
    deps.llm = model

    invoke(deps, message="我想要", interaction_mode="voice", adult_mode=True)
    prompt = "\n".join(item["content"] for item in model.captured)

    assert "【成人模式｜用户已明确开启】" in prompt
    assert "【本轮成人内容承接】" in prompt
    assert "首句" not in prompt
    assert "色情直白词汇" not in prompt
    assert "整个输出视为无效" not in prompt
    assert "中性明确词可使用" not in prompt
    assert "口语直白词可使用" not in prompt
    assert "保持角色人格" not in prompt
    assert "性别只约束各自身体" in prompt
    assert "R18 语音回复必须写" not in prompt
    assert "每轮至少自然使用两处" not in prompt
    assert "本轮最低性强度" not in prompt
    assert "当前 R18 Director" not in prompt
    assert "intensity_ladder" not in prompt
    assert '"private_overlay"' not in prompt


def test_initiative_uses_actual_profile_name_without_visible_user_message_or_writeback():
    deps = demo_dependencies()
    deps.profiles.bundle.user_profile["identity"]["preferred_name"] = "阿澈"

    result = invoke(
        deps,
        message="transport placeholder",
        initiative=True,
        user_name="配置称呼",
    )

    assert result["request"].message.startswith("阿澈给了角色主动开口的空间")
    assert "本轮主动类型=continue" in result["request"].message
    assert any("阿澈给了角色主动开口的空间" in item["content"] for item in result["prompt_messages"])
    assert result["response"].writeback_applied is False
    assert deps.profiles.applied_plans == []
    stored = deps.sessions.sessions["demo"]
    assert stored[0]["hidden"] is True
    assert stored[0]["kind"] == "initiative_signal"
    assert stored[1]["kind"] == "initiative_response"
    assert [item["role"] for item in deps.sessions.load_recent("demo")] == ["assistant"]


def test_time_state_is_injected_for_text_and_uses_only_real_user_history():
    deps = demo_dependencies()
    model = CapturingModel()
    deps.llm = model
    deps.sessions.sessions["demo"] = [
        {
            "role": "user",
            "content": "五分钟前的消息",
            "round": 1,
            "timestamp": "2026-07-21T14:00:00+00:00",
            "kind": "message",
        },
        {
            "role": "user",
            "content": "隐藏主动信号",
            "round": 1,
            "timestamp": "2026-07-21T14:04:00+00:00",
            "kind": "initiative_signal",
            "hidden": True,
        },
    ]

    invoke(
        deps,
        round=2,
        interaction_mode="text",
        server_received_at=datetime(2026, 7, 21, 14, 5, tzinfo=UTC),
        client_timezone="Asia/Shanghai",
        client_utc_offset_minutes=480,
    )

    prompt = "\n".join(item["content"] for item in model.captured)
    assert "【当前本地物理时间】" in prompt
    assert '"current_local_datetime":"2026-07-21T22:05:00+08:00"' in prompt
    assert '"time_period":"晚上"' in prompt
    assert '"timezone":"Asia/Shanghai"' in prompt
    assert "current_time_utc" not in prompt
    assert "所有关于早晚、日期、睡醒、准备休息和时间间隔的判断都以此时间为现实基准" in prompt


class MemoryExtractingModel(DeterministicLanguageModel):
    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        return "嗯，我确实是个很容易满足的人。"

    def extract_memory(self, messages, config, *, timeout_seconds):
        raise AssertionError("foreground memory extraction must stay disabled")


def test_memory_worthy_turn_does_not_extract_or_mutate_agent_profile():
    deps = demo_dependencies()
    deps.llm = MemoryExtractingModel()
    deps.profiles.bundle.ai_profile["personality"] = {"core_traits": ["可靠"], "speech_style": []}

    result = invoke(deps, message="你是不是很容易满足的人？")

    assert result["response"].writeback_applied is False
    assert deps.profiles.applied_plans == []
    assert result["response"].model.total_calls == 1


def test_idle_continuation_is_ai_initiative_without_a_user_instruction():
    deps = demo_dependencies()
    model = CapturingModel()
    deps.llm = model

    result = invoke(
        deps,
        initiative=True,
        initiative_trigger="idle_continuation",
        interaction_mode="text",
    )

    prompt = "\n".join(item["content"] for item in model.captured)
    assert "用户没有发出新指令" in prompt
    assert "给用户保留继续沉默的空间" in prompt
    assert "不制造需要立即回应的压力" in prompt
    assert result["response"].writeback_applied is False
    assert deps.sessions.sessions["demo"][0]["hidden"] is True


def test_continuous_companionship_plans_topics_without_pressuring_the_listener():
    deps = demo_dependencies()
    model = CapturingModel()
    deps.llm = model

    result = invoke(
        deps,
        initiative=True,
        initiative_trigger="continuous_companionship",
        initiative_sequence=3,
        initiative_sequence_limit=12,
        interaction_mode="voice",
    )

    prompt = "\n".join(item["content"] for item in model.captured)
    assert "第 3/12 次自主衔接" in prompt
    assert "默认此刻不需要回应" in prompt
    assert "用户随时可能插话" in prompt
    assert "最高优先级的新方向" in prompt
    assert result["response"].writeback_applied is False
    assert deps.sessions.sessions["demo"][0]["hidden"] is True


def test_voice_delivery_state_only_enters_voice_prompt():
    delivery = VoiceDeliveryState(
        delivery_status="interrupted",
        heard_text="我刚才说到这里，",
        unheard_text="后面这一段没有听到。",
        played_audio_ms=1320,
        position_confidence=0.8,
    )
    voice_deps = demo_dependencies()
    voice_model = CapturingModel()
    voice_deps.llm = voice_model
    invoke(voice_deps, interaction_mode="voice", voice_delivery=delivery)
    voice_prompt = "\n".join(item["content"] for item in voice_model.captured)
    assert "【上一条语音交付状态】" in voice_prompt
    assert "后面这一段没有听到" in voice_prompt

    text_deps = demo_dependencies()
    text_model = CapturingModel()
    text_deps.llm = text_model
    invoke(text_deps, interaction_mode="text", voice_delivery=delivery)
    text_prompt = "\n".join(item["content"] for item in text_model.captured)
    assert "【上一条语音交付状态】" not in text_prompt
