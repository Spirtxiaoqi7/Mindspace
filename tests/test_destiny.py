from __future__ import annotations

import json
from copy import deepcopy

from fastapi.testclient import TestClient

from mindspace_graph.api import _character_summary, create_app
from mindspace_graph.destiny import DestinyService, public_destiny_definition
from mindspace_graph.settings import AppSettings


def make_settings(tmp_path):
    return AppSettings(
        runtime_dir=tmp_path / "runtime",
        llm_mode="demo",
        tts_provider="browser",
        asr_provider="browser",
    )


def seed_payload() -> dict:
    return {
        "ai_name": "林见月",
        "ai_gender": "女",
        "user_name": "测试用户",
        "user_alias": "阿澈",
        "relationship": "陪伴者",
        "relationship_context": "已经相处一段时间，习惯在日常小事里互相照看。",
        "character_expectation": "希望她温柔但有自己的决定，能一起经历真实的日常。",
        "adult_character": True,
        "avatar": {},
    }


def people_payload() -> dict:
    labels = ("邻家姐姐", "年上御姐", "淘气弟弟", "冷静搭档", "慢热朋友", "直球恋人", "嘴硬室友", "温和同事")
    return {"people": [{"id": f"p{index}", "label": label, "summary": f"{label}说话方式和相处节奏明显不同。"} for index, label in enumerate(labels, start=1)]}


def cards_payload() -> dict:
    slots = public_destiny_definition()["slots"]
    levels = ("low", "neutral", "normal", "high")
    return {"cards": [[f"p{person}", slot["id"], f"人物{person}的{slot['axis']}", f"聊天中会以人物{person}的方式表现出{slot['axis']}。", levels[(person + slot["index"]) % 4]] for person in range(1, 9) for slot in slots]}


def compact_cards_payload() -> dict:
    slots = public_destiny_definition()["slots"]
    levels = ("low", "neutral", "normal", "high")
    return {
        "cards": [
            [
                [
                    f"人物{person}的{slot['axis']}",
                    f"聊天中会以人物{person}的方式表现出{slot['axis']}。",
                    levels[(person + slot["index"]) % 4],
                ]
                for slot in slots
            ]
            for person in range(1, 9)
        ]
    }


def card_payload() -> dict:
    return {
        "description": "她以清醒温和的方式陪伴用户，在日常中保有自己的判断。",
        "personality": "细腻、独立，先确认事实再表达立场；相处时会给出稳定而不模板化的回应。",
        "scenario": "她与用户处在长期陪伴的日常关系中，会一起面对计划、情绪和选择。",
        "first_mes": "你好，阿澈。我在，今天想先从哪件小事开始？",
        "alternate_greetings": ["我在这儿，慢慢说。", "今天的节奏交给你决定。"],
        "mes_example": "{{user}} 今天有点乱。\n{{char}} 那我们先只拎出最重要的一件，不急着把一切解释清楚。",
    }


def test_destiny_definition_exposes_v7_slots_and_willingness_levels():
    definition = public_destiny_definition()

    assert definition["archetype_count"] == 8
    assert len(definition["slots"]) == 12
    assert len({slot["id"] for slot in definition["slots"]}) == 12
    assert set(definition["interaction_willingness"]) == {"low", "neutral", "normal", "high"}
    assert all("metrics" not in slot for slot in definition["slots"])


def test_full_destiny_journey_uses_exactly_three_generation_calls(tmp_path, monkeypatch):
    app = create_app(make_settings(tmp_path))
    model_results = [people_payload(), cards_payload(), card_payload()]
    calls: list[str] = []

    async def scripted_model(messages, **kwargs):  # noqa: ANN001, ARG001
        calls.append(kwargs["request_kind"])
        return json.dumps(deepcopy(model_results[len(calls) - 1]), ensure_ascii=False)

    monkeypatch.setattr(app.state.destiny, "_generate", scripted_model)
    client = TestClient(app)
    created = client.post("/api/v1/destiny/journeys", json=seed_payload())
    assert created.status_code == 200
    journey = created.json()
    journey_id = journey["journey_id"]

    journey = client.post(f"/api/v1/destiny/journeys/{journey_id}/archetypes").json()
    assert journey["status"] == "archetypes_ready"
    assert journey["archetypes"] == people_payload()["people"]

    journey = client.post(f"/api/v1/destiny/journeys/{journey_id}/cards").json()
    assert journey["status"] == "cards_ready"
    assert len(journey["cards_by_slot"]) == 12
    assert sum(len(cards) for cards in journey["cards_by_slot"].values()) == 96

    for slot in public_destiny_definition()["slots"]:
        card = journey["cards_by_slot"][slot["id"]][0]
        response = client.put(
            f"/api/v1/destiny/journeys/{journey_id}/selections/{slot['id']}",
            json={"card_id": card["card_id"], "expected_revision": journey["revision"]},
        )
        assert response.status_code == 200
        journey = response.json()

    assert journey["status"] == "selections_ready"
    assert len(journey["selections"]) == 12
    journey = client.post(f"/api/v1/destiny/journeys/{journey_id}/synthesize").json()
    assert journey["status"] == "review_ready"
    assert journey["final_card"]["spec"] == "chara_card_v2"
    assert journey["final_card"]["data"]["name"] == "林见月"
    assert journey["final_card"]["data"]["memory"] == {"preferences": [], "tasks": []}

    committed = client.post(f"/api/v1/destiny/journeys/{journey_id}/commit")
    assert committed.status_code == 200
    assert committed.json()["character"]["display_name"] == "林见月"
    assert calls == ["destiny_archetypes", "destiny_cards", "destiny_synthesis"]
    assert journey["model_calls"] == {"archetypes": 1, "cards": 1, "synthesis": 1}


def test_invalid_cards_are_persisted_without_a_hidden_repair_call(tmp_path, monkeypatch):
    app = create_app(make_settings(tmp_path))
    outputs = [people_payload(), {"cards": cards_payload()["cards"][:-1]}]
    call_count = 0

    async def malformed_model(*args, **kwargs):  # noqa: ANN001, ARG001
        nonlocal call_count
        value = outputs[call_count]
        call_count += 1
        return json.dumps(value, ensure_ascii=False)

    monkeypatch.setattr(app.state.destiny, "_generate", malformed_model)
    client = TestClient(app, raise_server_exceptions=False)
    journey = client.post("/api/v1/destiny/journeys", json=seed_payload()).json()
    journey_id = journey["journey_id"]
    assert client.post(f"/api/v1/destiny/journeys/{journey_id}/archetypes").status_code == 200
    response = client.post(f"/api/v1/destiny/journeys/{journey_id}/cards")
    persisted = client.get(f"/api/v1/destiny/journeys/{journey_id}").json()

    assert response.status_code == 422
    assert persisted["status"] == "cards_failed"
    assert persisted["model_calls"] == {"archetypes": 1, "cards": 1, "synthesis": 0}
    assert call_count == 2


def test_category_name_cannot_be_accepted_as_a_direct_card_label(tmp_path, monkeypatch):
    app = create_app(make_settings(tmp_path))
    malformed = cards_payload()
    for row in malformed["cards"]:
        row[2] = next(slot["axis"] for slot in public_destiny_definition()["slots"] if slot["id"] == row[1])
    outputs = [people_payload(), malformed]
    call_count = 0

    async def category_label_model(*args, **kwargs):  # noqa: ANN001, ARG001
        nonlocal call_count
        value = outputs[call_count]
        call_count += 1
        return json.dumps(value, ensure_ascii=False)

    monkeypatch.setattr(app.state.destiny, "_generate", category_label_model)
    client = TestClient(app, raise_server_exceptions=False)
    journey = client.post("/api/v1/destiny/journeys", json=seed_payload()).json()
    journey_id = journey["journey_id"]
    assert client.post(f"/api/v1/destiny/journeys/{journey_id}/archetypes").status_code == 200
    response = client.post(f"/api/v1/destiny/journeys/{journey_id}/cards")
    persisted = client.get(f"/api/v1/destiny/journeys/{journey_id}").json()

    assert response.status_code == 422
    assert "直接标签不能重复分类名" in response.json()["detail"]
    assert persisted["status"] == "cards_failed"
    assert persisted["model_calls"] == {"archetypes": 1, "cards": 1, "synthesis": 0}
    assert call_count == 2


def test_category_label_uses_a_valid_short_summary_prefix_without_a_second_model_call(tmp_path, monkeypatch):
    app = create_app(make_settings(tmp_path))
    normalized = cards_payload()
    for row in normalized["cards"]:
        row[2] = next(slot["axis"] for slot in public_destiny_definition()["slots"] if slot["id"] == row[1])
        row[3] = "温柔体贴，聊天时会耐心回应对方的情绪。"
    outputs = [people_payload(), normalized]
    call_count = 0

    async def category_label_model(*args, **kwargs):  # noqa: ANN001, ARG001
        nonlocal call_count
        value = outputs[call_count]
        call_count += 1
        return json.dumps(value, ensure_ascii=False)

    monkeypatch.setattr(app.state.destiny, "_generate", category_label_model)
    client = TestClient(app)
    journey = client.post("/api/v1/destiny/journeys", json=seed_payload()).json()
    journey_id = journey["journey_id"]
    assert client.post(f"/api/v1/destiny/journeys/{journey_id}/archetypes").status_code == 200
    response = client.post(f"/api/v1/destiny/journeys/{journey_id}/cards")

    assert response.status_code == 200
    assert all(card["label"] == "温柔体贴" for cards in response.json()["cards_by_slot"].values() for card in cards)
    assert call_count == 2


def test_incomplete_journey_with_legacy_category_labels_requires_cards_only_retry(tmp_path, monkeypatch):
    app = create_app(make_settings(tmp_path))
    outputs = [people_payload(), cards_payload()]
    call_count = 0

    async def valid_model(*args, **kwargs):  # noqa: ANN001, ARG001
        nonlocal call_count
        value = outputs[call_count]
        call_count += 1
        return json.dumps(value, ensure_ascii=False)

    monkeypatch.setattr(app.state.destiny, "_generate", valid_model)
    client = TestClient(app)
    journey = client.post("/api/v1/destiny/journeys", json=seed_payload()).json()
    journey_id = journey["journey_id"]
    assert client.post(f"/api/v1/destiny/journeys/{journey_id}/archetypes").status_code == 200
    journey = client.post(f"/api/v1/destiny/journeys/{journey_id}/cards").json()
    first_slot = public_destiny_definition()["slots"][0]
    journey["cards_by_slot"][first_slot["id"]][0]["label"] = first_slot["axis"]
    app.state.destiny.database.put_document(app.state.destiny._key(journey_id), journey)

    restored = client.get(f"/api/v1/destiny/journeys/{journey_id}").json()

    assert restored["status"] == "cards_failed"
    assert restored["cards_by_slot"] == {}
    assert restored["selections"] == {}
    assert restored["archetypes"] == people_payload()["people"]
    assert restored["model_calls"] == {"archetypes": 1, "cards": 1, "synthesis": 0}
    assert "继续生成命签" in restored["errors"][-1]["message"]


def test_destiny_prompts_keep_the_simple_character_creation_contract():
    archetypes = DestinyService._archetype_messages(seed_payload())
    cards = DestinyService._cards_messages(people_payload()["people"])

    assert archetypes[0]["content"] == '你是聊天角色创作者。只返回 JSON：{"people":[{"id":"p1","label":"","summary":""}]}'
    assert '[["p1","emotional_baseline","","","normal"]]' in cards[0]["content"]
    assert "分类名、分类 ID 或人物方向标签" in cards[1]["content"]
    assert "不超过 32 个汉字" in cards[1]["content"]
    assert not any(word in "\n".join(message["content"] for message in [*archetypes, *cards]) for word in ("玄学", "命宫", "宿", "推演", "阴阳"))


def test_truncated_cards_report_a_clear_retryable_error(tmp_path, monkeypatch):
    app = create_app(make_settings(tmp_path))
    outputs = [json.dumps(people_payload(), ensure_ascii=False), '{"cards":[[["慢热","会先观察"']
    call_count = 0

    async def truncated_model(*args, **kwargs):  # noqa: ANN001, ARG001
        nonlocal call_count
        value = outputs[call_count]
        call_count += 1
        return value

    monkeypatch.setattr(app.state.destiny, "_generate", truncated_model)
    client = TestClient(app, raise_server_exceptions=False)
    journey = client.post("/api/v1/destiny/journeys", json=seed_payload()).json()
    journey_id = journey["journey_id"]
    assert client.post(f"/api/v1/destiny/journeys/{journey_id}/archetypes").status_code == 200

    response = client.post(f"/api/v1/destiny/journeys/{journey_id}/cards")
    persisted = client.get(f"/api/v1/destiny/journeys/{journey_id}").json()

    assert response.status_code == 422
    assert "JSON 完成前中断" in response.json()["detail"]
    assert "JSON 完成前中断" in persisted["errors"][-1]["message"]
    assert persisted["model_calls"] == {"archetypes": 1, "cards": 1, "synthesis": 0}
    assert call_count == 2


def test_destiny_avatar_upload_is_local_to_the_journey(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    response = client.post(
        "/api/v1/destiny/avatars",
        files={"file": ("portrait.png", b"\x89PNG\r\n\x1a\nminimal", "image/png")},
    )

    assert response.status_code == 200
    avatar = response.json()["avatar"]
    assert avatar["src"].startswith("/api/v1/avatar/files/destiny-")
    assert avatar["scale"] == 1.0


def test_destiny_seed_never_persists_a_browser_blob_avatar(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    payload = seed_payload()
    payload["avatar"] = {"src": "blob:http://127.0.0.1/temporary", "scale": 1.4, "x": 4, "y": -2}

    response = client.post("/api/v1/destiny/journeys", json=payload)

    assert response.status_code == 200
    assert response.json()["seed"]["avatar"] == {}


def test_character_summary_replaces_historical_blob_avatar():
    summary = _character_summary({"character_id": "character-1", "avatar": {"src": "blob:http://127.0.0.1/expired"}})

    assert summary["avatar"]["src"] == "/assets/avatar-ai-default.webp"
