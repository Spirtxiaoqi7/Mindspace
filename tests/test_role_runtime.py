from mindspace_graph.character_card import normalize_card
from mindspace_graph.role_runtime import build_runtime_role_state, compact_system_prompt


def test_v2_runtime_state_prefers_saved_name_alias_and_memory() -> None:
    state = build_runtime_role_state(
        ai_profile={
            "v2_card": {
                "name": "林岚",
                "description": "独立插画师",
                "personality": "坦率而克制",
                "scenario": "长期朋友",
                "extensions": {
                    "mindspace": {
                        "user_name": "小柒",
                        "user_alias": "老公",
                        "relationship": "妻子与丈夫",
                    }
                },
            }
        },
        character_memory={"preferences": ["不喜欢被随意改称呼"], "tasks": ["周四提交画稿"]},
        user_profile={"identity": {"preferred_name": "用户"}},
        request_user_name="访客",
    )
    assert state["user_name"] == "小柒"
    assert state["user_alias"] == "老公"
    assert state["tasks"] == ["周四提交画稿"]


def test_compact_prompt_excludes_v2_examples() -> None:
    state = build_runtime_role_state(
        ai_profile={"v2_card": {"name": "林岚", "personality": "直接", "scenario": "朋友"}},
        character_memory={},
        user_profile={"identity": {"preferred_name": "小柒"}},
    )
    prompt = compact_system_prompt(state)
    assert "mes_example" not in prompt
    assert "first_mes" not in prompt
    assert len(prompt) < 500


def test_v2_card_keeps_per_character_user_name() -> None:
    card = normalize_card(
        {
            "data": {
                "name": "林岚",
                "extensions": {"mindspace": {"user_name": "小柒", "user_alias": "老公"}},
            }
        }
    )
    assert card["data"]["extensions"]["mindspace"]["user_name"] == "小柒"
