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


def invoke(deps, **request_overrides):
    values = {
        "message": "解释当前流程",
        "session_id": "demo",
        "retrieval": {"similarity_threshold": 0},
    }
    values.update(request_overrides)
    request = ChatRequest(**values)
    return build_graph(deps).invoke({"request": request}, config={"recursion_limit": 20})


def test_happy_path_runs_parallel_retrieval_and_persists_turn():
    deps = demo_dependencies()
    result = invoke(deps)

    response = result["response"]
    assert response.status == "success"
    assert response.retrieval_counts == {"knowledge": 1, "chat": 1}
    assert "retrieve_knowledge" in response.trace
    assert "retrieve_chat" in response.trace
    assert len(deps.sessions.sessions["demo"]) == 2
    assert response.assistant_message_id
    assert response.llm_call_count == 1
    assert response.model.total_calls == 1
    assert [item.kind for item in response.model.call_summary] == ["generation"]


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


def test_invalid_protocol_gets_one_repair_and_does_not_write_profiles():
    deps = demo_dependencies()
    deps.llm = BrokenOnceModel()
    result = invoke(deps)

    assert result["response"].status == "success"
    assert "repair_protocol" in result["response"].trace
    assert result["response"].writeback_applied is False
    assert deps.profiles.applied_plans == []
    assert result["response"].model.total_calls == 2
    assert [item.kind for item in result["response"].model.call_summary] == [
        "generation",
        "protocol_repair",
    ]


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


def test_valid_repaired_protocol_can_commit_a_normalized_json_patch():
    deps = demo_dependencies()
    deps.llm = RepairToValidPatchModel()

    result = invoke(deps)

    assert "repair_protocol" in result["response"].trace
    assert result["response"].reply == "好，我会叫你阿澈。"
    assert result["response"].writeback_applied is True
    assert len(deps.profiles.applied_plans) == 1
    patch = deps.profiles.applied_plans[0].patches[0]
    assert patch.op == "replace"
    assert patch.path == "/identity/preferred_name"
    assert patch.value == "阿澈"


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


def test_more_than_three_patches_keeps_reply_but_blocks_writeback():
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
    assert any("at most 3" in error for error in response.errors)
    assert deps.sessions.load_pending_deletions("demo")[0].event_id == deletion.event_id


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


def test_runtime_json_patch_applies_only_on_primary_turn():
    deps = demo_dependencies()
    deps.llm = RelationshipPatchModel()
    primary = invoke(deps)
    assert primary["response"].writeback_applied is True
    assert len(deps.profiles.applied_plans) == 1

    regenerated = invoke(deps, mode="regenerate")
    assert regenerated["response"].writeback_applied is False
    assert len(deps.profiles.applied_plans) == 1


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


def test_prompt_uses_role_system_layers_and_never_identifies_as_protocol_outputter():
    deps = demo_dependencies()
    model = CapturingModel()
    deps.llm = model
    invoke(deps, system_prompt="你是弦月，语气温柔。")

    assert [item["role"] for item in model.captured[:2]] == ["system", "system"]
    assert all(item["role"] == "user" for item in model.captured[2:])
    system_text = "\n".join(item["content"] for item in model.captured if item["role"] == "system")
    all_text = "\n".join(item["content"] for item in model.captured)
    assert "你是弦月，语气温柔。" in system_text
    assert "非空的权威 JSON 是最高可信的持久事实" in system_text
    assert "这不是通用问答或客服会话" in system_text
    assert "即使设定提到 AI，那也只描述存在方式" in system_text
    assert "你是通过文字与用户交流的 AI" not in system_text
    assert "现实接触写成愿望、想象、提议或文字表达" in system_text
    assert "召回内容只是候选线索" in system_text
    assert "协议输出器" not in system_text
    assert "协议修复器" not in system_text
    assert "<analysis>" not in all_text
    assert '"call_count":0' in all_text
    assert "服务端没有执行任何只读查询" in all_text


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
    voice_system = "\n".join(
        item["content"] for item in voice_model.captured if item["role"] == "system"
    )
    voice_prompt = "\n".join(item["content"] for item in voice_model.captured)

    assert "用户已经打开实时语音" in voice_prompt
    assert "本轮正文会由当前角色音色逐句播放" in voice_prompt
    assert "用户没有打开实时语音" not in voice_prompt
    assert "用户已经打开实时语音" not in voice_system

    text_deps = demo_dependencies()
    text_model = CapturingModel()
    text_deps.llm = text_model
    invoke(text_deps, interaction_mode="text")
    text_system = "\n".join(
        item["content"] for item in text_model.captured if item["role"] == "system"
    )
    text_prompt = "\n".join(item["content"] for item in text_model.captured)

    assert "用户没有打开实时语音" in text_prompt
    assert "本轮内容只作为屏幕文字呈现" in text_prompt
    assert "用户已经打开实时语音" not in text_prompt
    assert "用户没有打开实时语音" not in text_system


def test_initiative_uses_actual_profile_name_without_visible_user_message_or_writeback():
    deps = demo_dependencies()
    deps.profiles.bundle.user_profile["identity"]["preferred_name"] = "阿澈"

    result = invoke(
        deps,
        message="transport placeholder",
        initiative=True,
        user_name="配置称呼",
    )

    assert result["request"].message == "阿澈不想说什么，但是想让你说点什么。"
    assert "阿澈不想说什么，但是想让你说点什么。" in result["prompt_messages"][-1]["content"]
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
    assert "【服务端时间状态】" in prompt
    assert '"current_time_local":"2026-07-21T22:05:00.000000+08:00"' in prompt
    assert '"current_weekday":"星期二"' in prompt
    assert '"current_is_weekend":false' in prompt
    assert '"tomorrow_weekday":"星期三"' in prompt
    assert '"elapsed_since_previous_user_ms":300000' in prompt
    assert "时间状态本身不能触发人物 JSON 修改" in prompt


class MemoryExtractingModel(DeterministicLanguageModel):
    def generate(self, messages: list[dict[str, str]], config: ApiConfig) -> str:
        prompt = "\n".join(message["content"] for message in messages)
        revisions = json.loads(re.search(r"base_revisions=(\{.*?\})", prompt).group(1))
        update = {
            "turn_id": "round_1",
            "base_revisions": revisions,
            "trigger": "none",
            "patches": [],
        }
        return (
            "<response>嗯，我确实是个很容易满足的人。</response>"
            f"<json_update>{json.dumps(update, ensure_ascii=False)}</json_update>"
        )

    def extract_memory(self, messages, config, *, timeout_seconds):
        payload = json.loads(messages[-1]["content"])
        return json.dumps({
            "turn_id": payload["turn_id"],
            "base_revisions": payload["base_revisions"],
            "trigger": "current_agent",
            "patches": [{
                "target": "ai_profile", "op": "add", "path": "/personality/core_traits/-",
                "value": "很容易满足", "evidence_ids": ["current_response"],
            }],
        }, ensure_ascii=False)


def test_memory_worthy_turn_conditionally_extracts_agent_self_memory():
    deps = demo_dependencies()
    deps.llm = MemoryExtractingModel()
    deps.profiles.bundle.ai_profile["personality"] = {"core_traits": ["可靠"], "speech_style": []}

    result = invoke(deps, message="你是不是很容易满足的人？")

    assert result["response"].writeback_applied is True
    assert deps.profiles.applied_plans[0].patches[0].value == "很容易满足"


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
