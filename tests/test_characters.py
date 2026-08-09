from __future__ import annotations

import io
import json
import zipfile
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from mindspace_graph.adapters.file_storage import DEFAULT_PROFILES
from mindspace_graph.api import create_app
from mindspace_graph.characters import (
    BLUEPRINT_BLOCK_IDS,
    BLUEPRINT_MIN_EFFECTIVE_TOKENS,
    MIGRATION_KEY,
    CharacterDraftInput,
    CharacterRepository,
    apply_generated_blueprint,
    blueprint_quality,
    character_generation_messages,
    effective_character_tokens,
    local_blueprint_from_draft,
    local_profile_from_draft,
    parse_generated_profile,
)
from mindspace_graph.fate_forge import (
    FATE_SLOTS,
    FateOptionsRequest,
    fate_generation_packet,
    fate_options_messages,
    normalize_fate_forge,
    parse_fate_options,
    public_fate_catalog,
)
from mindspace_graph.product_database import ProductDatabase
from mindspace_graph.settings import AppSettings


def make_settings(tmp_path):
    return AppSettings(
        runtime_dir=tmp_path / "runtime",
        llm_mode="demo",
        tts_provider="browser",
        asr_provider="browser",
    )


def draft_payload(name: str = "林澈") -> dict:
    return {
        "ai_name": name,
        "ai_gender": "女",
        "core_traits": ["温柔", "坦率"],
        "flaw": "有些固执",
        "relationship": "朋友",
        "relationship_context": "TA很依赖我，平时百依百顺，受到冷落时会明显不安。",
        "user_name": "测试用户",
        "user_alias": "用户",
    }


def draft_input(name: str = "林澈") -> CharacterDraftInput:
    return CharacterDraftInput.model_validate(draft_payload(name))


def fate_payload(*, all_gold: bool = False) -> dict:
    selections = []
    for index, slot in enumerate(FATE_SLOTS):
        rarity = "gold" if all_gold else ("red" if index == 0 else "blue")
        answer = ("yes", "no", "custom")[index % 3] if rarity == "gold" else ""
        selections.append(
            {
                "slot_id": slot["id"],
                "fate_id": f"generated-{slot['id']}-{rarity}",
                "rarity": rarity,
                "title": f"实时命格 {index + 1}",
                "summary": f"根据用户描述生成的方向 {index + 1}",
                "question": f"你喜欢方向 {index + 1} 吗？" if rarity == "gold" else "",
                "yes_direction": f"采用方向 {index + 1}" if rarity == "gold" else "",
                "no_direction": f"反转方向 {index + 1}" if rarity == "gold" else "",
                "answer": answer,
                "custom": f"用户自定义方向 {index + 1}" if answer == "custom" else "",
            }
        )
    return {"schema_version": "1.0.0", "seed": "test-seed", "selections": selections}


def test_fate_catalog_has_twelve_empty_structural_slots():
    catalog = public_fate_catalog()

    assert catalog["schema_version"] == "2.0.0"
    assert len(catalog["slots"]) == 12
    assert len({slot["id"] for slot in catalog["slots"]}) == 12
    assert all("fates" not in slot for slot in catalog["slots"])


def test_fate_options_prompt_contains_only_source_material_and_output_shape():
    request = FateOptionsRequest(
        relationship="恋人",
        user_content="TA对我百依百顺，也很依赖我。",
        modification="让情绪更黏人",
        slot_ids=["origin"],
    )
    messages = fate_options_messages(request)
    payload = json.loads(messages[1]["content"])

    assert messages[0]["content"].startswith("这是命定系统选项输出。只输出JSON")
    assert "禁止" not in messages[0]["content"]
    assert "独立" not in json.dumps(messages, ensure_ascii=False)
    assert payload["relationship"] == "恋人"
    assert payload["user_content"] == "TA对我百依百顺，也很依赖我。"
    assert payload["modification"] == "让情绪更黏人"


def test_dynamic_fate_options_are_parsed_without_static_catalog_ids():
    raw = json.dumps({"slots": [{"slot_id": "origin", "options": [
        {"rarity": "red", "title": "黏缚", "summary": "受到冷落时会不断确认关系"},
        {"rarity": "blue", "title": "随行", "summary": "自然跟随用户的选择"},
        {"rarity": "gold", "title": "唯命", "summary": "把用户愿望置于核心", "question": "喜欢TA完全听你的吗？", "yes_direction": "完全听从", "no_direction": "只在日常配合"},
    ]}]}, ensure_ascii=False)
    options = parse_fate_options(raw, ["origin"])

    assert [item["rarity"] for item in options["origin"]] == ["red", "blue", "gold"]
    assert all(item["id"].startswith("generated-origin-") for item in options["origin"])


def test_fate_options_endpoint_requires_configured_realtime_model(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    response = client.post("/api/v1/characters/fate-options", json={
        "relationship": "恋人",
        "user_content": "TA很依赖我，平时百依百顺。",
        "modification": "",
        "slot_ids": [],
    })

    assert response.status_code == 422
    assert "配置可用的模型 API" in response.json()["detail"]


def test_all_gold_fates_require_and_preserve_three_answer_branches():
    normalized = normalize_fate_forge(fate_payload(all_gold=True))
    packet = fate_generation_packet(normalized)

    assert normalized["rarity_counts"] == {"red": 0, "blue": 0, "gold": 12}
    assert {item["answer"] for item in normalized["selections"]} == {
        "yes",
        "no",
        "custom",
    }
    assert any(
        item["resolved_direction"].startswith("用户自定义方向") for item in packet["selections"]
    )


def test_fate_input_rejects_incomplete_matrix_and_compiles_into_eight_blocks():
    incomplete = fate_payload()
    incomplete["selections"].pop()
    with pytest.raises(ValueError, match="十二"):
        normalize_fate_forge(incomplete)

    payload = draft_payload()
    payload["fate_forge"] = fate_payload()
    value = CharacterDraftInput.model_validate(payload)
    blueprint = local_blueprint_from_draft(value)

    assert blueprint["authoring_provenance"]["system"] == "fate_system"
    assert set(blueprint["blocks"]) == set(BLUEPRINT_BLOCK_IDS)
    assert all("命格：" in block["content"] for block in blueprint["blocks"].values())


def test_character_generation_requests_eight_authored_blocks_not_full_profile():
    selected = draft_input()
    fallback = local_profile_from_draft(selected)

    messages = character_generation_messages(selected, fallback)
    payload = json.loads(messages[-1]["content"])

    assert "target_json" not in payload
    assert set(payload["output_format"]["blocks"]) == set(BLUEPRINT_BLOCK_IDS)
    assert len({item["dimension"] for item in payload["output_format"]["blocks"].values()}) == 7
    assert len(messages[-1]["content"]) < 2_500
    assert messages[0]["content"] == '这是角色卡输出。只输出JSON：{"blocks":{block_id:{"content":"..."}}}'
    assert "禁止" not in messages[0]["content"]
    assert "独立" not in messages[0]["content"]


@pytest.mark.parametrize("gender", ["女", "男", "不指定"])
def test_local_blueprint_is_complete_gender_neutral_and_over_one_thousand_tokens(gender):
    selected = CharacterDraftInput.model_validate({**draft_payload(), "ai_gender": gender})
    blueprint = local_blueprint_from_draft(selected)

    assert set(blueprint["blocks"]) == set(BLUEPRINT_BLOCK_IDS)
    assert blueprint_quality(blueprint)["complete"] is True
    assert effective_character_tokens(blueprint) >= BLUEPRINT_MIN_EFFECTIVE_TOKENS
    assert "她" not in json.dumps(blueprint, ensure_ascii=False)
    assert blueprint["locked_facts"]["pronoun"] == "TA"
    assert "不靠舞台动作制造人格感" in blueprint["blocks"]["voice_style"]["content"]
    assert "目标可以完全服务于用户" in blueprint["blocks"]["agency_goals"]["content"]


def test_selected_blueprint_rewrite_cannot_modify_unselected_blocks():
    current = local_blueprint_from_draft(draft_input())
    selected_id = "voice_style"
    original_identity = deepcopy(current["blocks"]["identity_story"])
    rewritten = "表达更克制，但不冷淡。" + current["blocks"][selected_id]["content"]
    raw = json.dumps(
        {
            "blocks": {
                selected_id: {"content": rewritten},
                "identity_story": {"content": "越界改写不应生效"},
            }
        },
        ensure_ascii=False,
    )

    result, warnings, accepted = apply_generated_blueprint(
        raw, current, selected_block_ids=[selected_id]
    )

    assert accepted == 1
    assert result["blocks"][selected_id]["content"].startswith("表达更克制")
    assert result["blocks"]["identity_story"] == original_identity
    assert any("越界" in item for item in warnings)


def test_compact_character_json_merges_into_server_owned_profile():
    selected = draft_input()
    fallback = local_profile_from_draft(selected)
    raw = json.dumps(
        {
            "summary": "会表达自己意见的长期陪伴者",
            "personality": "温柔但不迁就，坦率且偶尔固执",
            "speech_style": ["自然口语", "偶尔反问"],
            "likes": ["雨夜", "旧书"],
            "dislikes": ["敷衍"],
            "values": ["真诚", "独立"],
            "habits": ["思考时会停顿"],
            "initiative_sources": ["未完成的话题"],
            "preferred_interactions": ["直接交流"],
            "conflict_style": "先说清分歧",
            "repair_style": "承认具体问题",
            "relationship_tone": "平等且连续",
            "greeting": "我在，今天从哪里继续？",
        },
        ensure_ascii=False,
    )

    profile, warnings = parse_generated_profile(raw, fallback)

    assert warnings == []
    assert profile["identity"]["name"] == "林澈"
    assert profile["identity"]["gender"] == "女"
    assert profile["identity"]["relationship_to_user"] == "朋友"
    assert "温柔但不迁就" in profile["identity"]["self_description"]
    assert profile["roleplay"]["selfhood"]["likes"] == ["雨夜", "旧书"]
    assert profile["roleplay"]["examples"]["casual"] == ["我在，今天从哪里继续？"]


def test_truncated_or_labeled_character_output_recovers_fields_independently():
    selected = draft_input()
    fallback = local_profile_from_draft(selected)
    raw = (
        "性格：外柔内刚，遇到分歧会直接说明\n"
        "like: 雨夜、旧书\n"
        "说话风格：自然口语、句子简洁\n"
        "开场白：我在。你想先聊哪件事？\n"
        'dislikes: ["敷衍", "冷处理"'
    )

    profile, warnings = parse_generated_profile(raw, fallback)

    assert warnings
    assert "外柔内刚" in profile["identity"]["self_description"]
    assert profile["roleplay"]["selfhood"]["likes"] == ["雨夜", "旧书"]
    assert profile["personality"]["speech_style"] == ["自然口语", "句子简洁"]
    assert profile["roleplay"]["examples"]["casual"] == ["我在。你想先聊哪件事？"]
    assert profile["roleplay"]["selfhood"]["dislikes"] == ["敷衍", "冷处理"]


def test_legacy_profile_migration_is_idempotent_and_binds_sessions(tmp_path):
    settings = make_settings(tmp_path)
    legacy = deepcopy(DEFAULT_PROFILES["ai_profile"])
    legacy["identity"]["name"] = "旧角色"
    legacy["revision"] = 3
    profile_path = settings.runtime_dir / "data" / "profiles" / "ai-profile.json"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    app = create_app(settings)
    client = TestClient(app)

    first = client.get("/api/v1/characters").json()["items"]
    second_app = create_app(settings)
    second = TestClient(second_app).get("/api/v1/characters").json()["items"]

    assert len(first) == 1
    assert len(second) == 1
    assert first[0]["character_id"] == second[0]["character_id"]
    assert first[0]["source"] == "migrated"


def test_character_migration_rolls_back_database_and_keeps_backup(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    legacy_path = settings.runtime_dir / "data" / "profiles" / "ai-profile.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(
        json.dumps(DEFAULT_PROFILES["ai_profile"], ensure_ascii=False),
        encoding="utf-8",
    )

    def fail_store(self, record):  # noqa: ARG001
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(CharacterRepository, "_store", fail_store)
    with pytest.raises(RuntimeError, match="simulated migration failure"):
        create_app(settings)

    database = ProductDatabase(settings.runtime_dir / "data" / "context" / "context.db")
    assert database.has_document(MIGRATION_KEY) is False
    assert database.list_documents("character:") == []
    assert legacy_path.exists()
    assert (
        settings.runtime_dir / "data" / "backups" / "character-migration-0.6.0" / "manifest.json"
    ).exists()


def test_draw_draft_uses_at_most_one_model_call_and_commits(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))

    draft = client.post("/api/v1/character-drafts", json=draft_payload()).json()
    generated = client.post(f"/api/v1/character-drafts/{draft['draft_id']}/generate").json()
    result = client.post(
        f"/api/v1/character-drafts/{draft['draft_id']}/commit",
        json={"profile": generated["profile"]},
    )

    assert generated["model_call_count"] <= 1
    assert generated["generation_mode"] in {"llm", "local_template"}
    assert result.status_code == 200
    character = result.json()["character"]
    assert character["source"] == "draw"
    assert character["display_name"] == "林澈"
    assert character["gender"] == "女"


def test_session_character_binding_rejects_silent_rebind(tmp_path):
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)
    original = client.get("/api/v1/characters").json()["items"][0]

    draft = client.post("/api/v1/character-drafts", json=draft_payload("第二角色")).json()
    created = client.post(f"/api/v1/character-drafts/{draft['draft_id']}/commit").json()[
        "character"
    ]
    session_id = "role-bound-session"
    assert (
        client.post(
            "/api/v1/sessions",
            json={"session_id": session_id, "character_id": original["character_id"]},
        ).status_code
        == 200
    )

    response = client.post(
        "/api/v1/sessions",
        json={"session_id": session_id, "character_id": created["character_id"]},
    )
    assert response.status_code == 409


def test_card_export_import_generates_new_id_and_excludes_private_data(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    card = {
        "spec": "chara_card_v2", "spec_version": "2.0",
        "data": {"name": "林澈", "description": "独立的同行者", "personality": "冷静直接", "scenario": "日常陪伴", "first_mes": "你好。", "mes_example": "{{user}} 嗨\n{{char}} 我在。"},
    }
    character = client.post("/api/v1/characters", json={"source": "custom", "card": card}).json()["character"]

    exported = client.get(f"/api/v1/characters/{character['character_id']}/export")
    assert exported.status_code == 200
    exported_card = exported.json()
    assert exported_card["spec"] == "chara_card_v2"
    assert "api_key" not in json.dumps(exported_card)

    imported = client.post(
        "/api/v1/characters/import",
        files={
            "file": (
                "card.json",
                exported.content,
                "application/json",
            )
        },
    )
    assert imported.status_code == 200
    assert imported.json()["character"]["character_id"] != character["character_id"]


def test_card_import_rejects_zip_path_traversal(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.json", "{}")
        archive.writestr("manifest.json", "{}")
        archive.writestr("ai-profile.json", "{}")

    response = client.post(
        "/api/v1/characters/import",
        files={"file": ("bad.mindspace-card", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 422


def test_historical_chat_retrieval_is_scoped_to_character(tmp_path):
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)
    first = client.get("/api/v1/characters").json()["items"][0]
    draft = client.post("/api/v1/character-drafts", json=draft_payload("隔离角色")).json()
    second = client.post(f"/api/v1/character-drafts/{draft['draft_id']}/commit").json()["character"]
    for session_id, character, phrase in (
        ("history-a", first, "青铜风铃只属于角色甲"),
        ("history-b", second, "紫色雨伞只属于角色乙"),
    ):
        client.post(
            "/api/v1/sessions",
            json={"session_id": session_id, "character_id": character["character_id"]},
        )
        response = client.post(
            "/api/v1/chat",
            json={
                "message": phrase,
                "session_id": session_id,
                "character_id": character["character_id"],
                "round": 1,
                "retrieval": {"rag_enabled": False},
            },
        )
        assert response.status_code == 200

    chunks = app.state.container.conversation.dependencies.retriever.search_chat(
        "只属于角色",
        "new-session-a",
        20,
        character_id=first["character_id"],
        messages=[],
    )
    joined = "\n".join(item.text for item in chunks)
    assert "青铜风铃只属于角色甲" in joined
    assert "紫色雨伞只属于角色乙" not in joined
