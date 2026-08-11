from __future__ import annotations

import json
from copy import deepcopy

from fastapi.testclient import TestClient

from mindspace_graph.api import _character_summary, create_app
from mindspace_graph.destiny import DestinySeed, DestinyService, public_destiny_definition
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
    labels = (
        "邻家姐姐",
        "年上御姐",
        "淘气妹妹",
        "冷静搭档",
        "慢热朋友",
        "直球恋人",
        "嘴硬室友",
        "温和同事",
    )
    return {
        "people": [
            {"id": f"p{index}", "label": label, "summary": f"{label}说话方式和相处节奏明显不同。"}
            for index, label in enumerate(labels, start=1)
        ]
    }


def cards_payload(slot_start: int = 0, slot_count: int = 12) -> dict:
    slots = public_destiny_definition()["slots"][slot_start : slot_start + slot_count]
    levels = ("low", "neutral", "normal", "high")
    return {
        "cards": [
            [
                f"p{person}",
                slot["id"],
                f"人物{person}的{slot['axis']}",
                f"聊天中会以人物{person}的方式表现出{slot['axis']}。",
                levels[(person + slot["index"]) % 4],
            ]
            for person in range(1, 9)
            for slot in slots
        ]
    }


def compact_cards_payload(slot_start: int = 0, slot_count: int = 12) -> dict:
    slots = public_destiny_definition()["slots"][slot_start : slot_start + slot_count]
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


def test_full_destiny_journey_uses_two_card_calls_inside_three_visible_stages(tmp_path, monkeypatch):
    app = create_app(make_settings(tmp_path))
    model_results = [people_payload(), cards_payload(0, 6), cards_payload(6, 6), card_payload()]
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
    assert calls == ["destiny_archetypes", "destiny_cards", "destiny_cards", "destiny_synthesis"]
    assert journey["model_calls"] == {"archetypes": 1, "cards": 2, "synthesis": 1}


def test_failed_first_half_preserves_successful_second_half_and_retries_only_first(tmp_path, monkeypatch):
    app = create_app(make_settings(tmp_path))
    first_half = cards_payload(0, 6)
    outputs = [people_payload(), {"cards": first_half["cards"][:-1]}, cards_payload(6, 6), first_half]
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
    assert persisted["model_calls"] == {"archetypes": 1, "cards": 2, "synthesis": 0}
    assert persisted["card_batches"]["first"]["status"] == "failed"
    assert persisted["card_batches"]["second"]["status"] == "ready"
    assert len(persisted["cards_by_slot"]) == 6
    assert call_count == 3

    retried = client.post(f"/api/v1/destiny/journeys/{journey_id}/cards")

    assert retried.status_code == 200
    assert retried.json()["status"] == "cards_ready"
    assert len(retried.json()["cards_by_slot"]) == 12
    assert retried.json()["model_calls"] == {"archetypes": 1, "cards": 3, "synthesis": 0}
    assert call_count == 4


def test_failed_second_half_preserves_successful_first_half_and_retries_only_second(tmp_path, monkeypatch):
    app = create_app(make_settings(tmp_path))
    second_half = cards_payload(6, 6)
    outputs = [people_payload(), cards_payload(0, 6), {"cards": second_half["cards"][:-1]}, second_half]
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

    failed = client.post(f"/api/v1/destiny/journeys/{journey_id}/cards")
    persisted = client.get(f"/api/v1/destiny/journeys/{journey_id}").json()

    assert failed.status_code == 422
    assert persisted["card_batches"]["first"]["status"] == "ready"
    assert persisted["card_batches"]["second"]["status"] == "failed"
    assert len(persisted["cards_by_slot"]) == 6
    assert call_count == 3

    retried = client.post(f"/api/v1/destiny/journeys/{journey_id}/cards")

    assert retried.status_code == 200
    assert retried.json()["status"] == "cards_ready"
    assert retried.json()["model_calls"] == {"archetypes": 1, "cards": 3, "synthesis": 0}
    assert call_count == 4


def test_category_name_cannot_be_accepted_as_a_direct_card_label(tmp_path, monkeypatch):
    app = create_app(make_settings(tmp_path))
    malformed = cards_payload(0, 6)
    for row in malformed["cards"]:
        row[2] = next(slot["axis"] for slot in public_destiny_definition()["slots"] if slot["id"] == row[1])
    outputs = [people_payload(), malformed, cards_payload(6, 6)]
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
    assert persisted["model_calls"] == {"archetypes": 1, "cards": 2, "synthesis": 0}
    assert call_count == 3


def test_category_label_uses_a_valid_short_summary_prefix_without_a_second_model_call(tmp_path, monkeypatch):
    app = create_app(make_settings(tmp_path))
    normalized = cards_payload(0, 6)
    for row in normalized["cards"]:
        row[2] = next(slot["axis"] for slot in public_destiny_definition()["slots"] if slot["id"] == row[1])
        row[3] = "温柔体贴，聊天时会耐心回应对方的情绪。"
    second_half = cards_payload(6, 6)
    for row in second_half["cards"]:
        row[2] = next(slot["axis"] for slot in public_destiny_definition()["slots"] if slot["id"] == row[1])
        row[3] = "温柔体贴，聊天时会耐心回应对方的情绪。"
    outputs = [people_payload(), normalized, second_half]
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
    assert call_count == 3


def test_reading_invalid_cards_is_non_destructive_and_explicit_post_rebuilds_them(tmp_path, monkeypatch):
    app = create_app(make_settings(tmp_path))
    outputs = [
        people_payload(),
        cards_payload(0, 6),
        cards_payload(6, 6),
        cards_payload(0, 6),
        cards_payload(6, 6),
    ]
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
    journey["status"] = "review_ready"
    journey["selections"] = {first_slot["id"]: deepcopy(journey["cards_by_slot"][first_slot["id"]][0])}
    journey["final_card"] = {"spec": "chara_card_v2", "data": {"name": "待修复角色"}}
    app.state.destiny.database.put_document(app.state.destiny._key(journey_id), journey)
    before = deepcopy(app.state.destiny.database.get_document(app.state.destiny._key(journey_id)))

    restored = client.get(f"/api/v1/destiny/journeys/{journey_id}").json()

    assert restored["status"] == "review_ready"
    assert restored["cards_by_slot"] == before["cards_by_slot"]
    assert restored["selections"] == before["selections"]
    assert restored["final_card"] == before["final_card"]
    assert restored["read_state"]["state"] == "cards_invalid"
    assert app.state.destiny.database.get_document(app.state.destiny._key(journey_id)) == before
    assert restored["archetypes"] == people_payload()["people"]
    assert restored["model_calls"] == {"archetypes": 1, "cards": 2, "synthesis": 0}

    rebuilt = client.post(f"/api/v1/destiny/journeys/{journey_id}/cards")
    assert rebuilt.status_code == 200
    assert rebuilt.json()["status"] == "cards_ready"
    assert rebuilt.json()["selections"] == {}
    assert rebuilt.json()["final_card"] is None
    assert call_count == 5


def test_reading_legacy_incomplete_journey_returns_status_without_writing(tmp_path):
    app = create_app(make_settings(tmp_path))
    journey = app.state.destiny.create(DestinySeed.model_validate(seed_payload()))
    journey["schema_version"] = "1.0.0"
    key = app.state.destiny._key(journey["journey_id"])
    app.state.destiny.database.put_document(key, journey)
    before = deepcopy(app.state.destiny.database.get_document(key))

    response = TestClient(app).get(f"/api/v1/destiny/journeys/{journey['journey_id']}")

    assert response.status_code == 200
    assert response.json()["read_state"]["state"] == "legacy_incomplete"
    assert response.json()["read_state"]["action"] == "restart"
    assert app.state.destiny.database.get_document(key) == before


def test_destiny_prompts_keep_the_simple_character_creation_contract():
    archetypes = DestinyService._archetype_messages(seed_payload())
    cards = DestinyService._cards_messages(
        people_payload()["people"], "女", tuple(public_destiny_definition()["slots"][:6])
    )

    assert (
        archetypes[0]["content"] == '你是聊天角色创作者。只返回 JSON：{"people":[{"id":"p1","label":"","summary":""}]}'
    )
    assert '[[["慢热敏感","会先观察语气再回应","normal"]]]' in cards[0]["content"]
    assert "6 个分类，共 48 张" in cards[1]["content"]
    assert "分类名、分类 ID 或人物方向标签" in cards[1]["content"]
    assert "不超过 32 个汉字" in cards[1]["content"]
    assert not any(
        word in "\n".join(message["content"] for message in [*archetypes, *cards])
        for word in ("玄学", "命宫", "宿", "推演", "阴阳")
    )


def test_cards_accept_compact_matrix_top_level_array_and_harmless_json_damage():
    people = people_payload()["people"]
    slots = tuple(public_destiny_definition()["slots"][:6])
    compact = compact_cards_payload(0, 6)["cards"]

    top_level = DestinyService._normalize_cards(json.dumps(compact, ensure_ascii=False), people, "女", slots)
    trailing_comma = json.dumps({"cards": compact}, ensure_ascii=False, separators=(",", ":")).replace("]]}", "]],}")
    repaired = DestinyService._normalize_cards(trailing_comma, people, "女", slots)

    assert len(top_level) == 6
    assert sum(len(cards) for cards in repaired.values()) == 48


def test_cards_recover_rows_when_outer_json_is_missing_a_comma():
    people = people_payload()["people"]
    slots = tuple(public_destiny_definition()["slots"][:6])
    raw = json.dumps(cards_payload(0, 6), ensure_ascii=False, separators=(",", ":"))
    broken = raw.replace("],[", "][", 1)

    recovered = DestinyService._normalize_cards(broken, people, "女", slots)

    assert sum(len(cards) for cards in recovered.values()) == 48


def test_cards_accept_ordered_object_aliases_without_repeated_ids():
    people = people_payload()["people"]
    slots = tuple(public_destiny_definition()["slots"][:6])
    rows = [
        {
            "标签": f"人物{person}特征{slot['index']}",
            "表现": f"聊天时会呈现人物{person}的具体回应方式。",
            "互动意愿": "normal",
        }
        for person in range(1, 9)
        for slot in slots
    ]

    normalized = DestinyService._normalize_cards(json.dumps(rows, ensure_ascii=False), people, "女", slots)

    assert sum(len(cards) for cards in normalized.values()) == 48


def test_app_startup_recovers_an_interrupted_destiny_stage(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    journey = app.state.destiny.create(DestinySeed.model_validate(seed_payload()))
    journey["status"] = "cards_generating"
    journey["operation_id"] = "interrupted-operation"
    app.state.destiny.database.put_document(app.state.destiny._key(journey["journey_id"]), journey)

    recovered = create_app(settings).state.destiny.get(journey["journey_id"])

    assert recovered["status"] == "cards_failed"
    assert "Core 在本阶段完成前中断" in recovered["errors"][-1]["message"]


def test_truncated_cards_report_a_clear_retryable_error(tmp_path, monkeypatch):
    app = create_app(make_settings(tmp_path))
    outputs = [
        json.dumps(people_payload(), ensure_ascii=False),
        '{"cards":[[["慢热","会先观察"',
        json.dumps(cards_payload(6, 6), ensure_ascii=False),
    ]
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
    assert persisted["model_calls"] == {"archetypes": 1, "cards": 2, "synthesis": 0}
    assert len(persisted["cards_by_slot"]) == 6
    assert call_count == 3


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

    filename = avatar["src"].rsplit("/", maxsplit=1)[-1]
    deleted = client.delete(f"/api/v1/destiny/avatars/{filename}")
    assert deleted.status_code == 200
    assert not (tmp_path / "runtime" / "data" / "avatars" / filename).exists()


def test_committed_destiny_avatar_moves_to_the_character_directory(tmp_path, monkeypatch):
    app = create_app(make_settings(tmp_path))

    async def scripted_synthesis(*args, **kwargs):  # noqa: ANN001, ARG001
        return json.dumps(card_payload(), ensure_ascii=False)

    monkeypatch.setattr(app.state.destiny, "_generate", scripted_synthesis)
    client = TestClient(app)
    avatar = client.post(
        "/api/v1/destiny/avatars",
        files={"file": ("portrait.png", b"\x89PNG\r\n\x1a\nminimal", "image/png")},
    ).json()["avatar"]
    created = client.post("/api/v1/destiny/journeys", json={**seed_payload(), "avatar": avatar})
    assert created.status_code == 200
    journey = created.json()
    journey_id = journey["journey_id"]

    journey = client.post(
        f"/api/v1/destiny/journeys/{journey_id}/archetypes?use_default=true&expected_revision={journey['revision']}"
    ).json()
    journey = client.post(
        f"/api/v1/destiny/journeys/{journey_id}/cards?use_default=true&expected_revision={journey['revision']}"
    ).json()
    for slot in public_destiny_definition()["slots"]:
        card_id = journey["cards_by_slot"][slot["id"]][0]["card_id"]
        selected = client.put(
            f"/api/v1/destiny/journeys/{journey_id}/selections/{slot['id']}",
            json={"card_id": card_id, "expected_revision": journey["revision"]},
        )
        assert selected.status_code == 200
        journey = selected.json()

    synthesized = client.post(
        f"/api/v1/destiny/journeys/{journey_id}/synthesize?expected_revision={journey['revision']}"
    )
    assert synthesized.status_code == 200
    committed = client.post(
        f"/api/v1/destiny/journeys/{journey_id}/commit?expected_revision={synthesized.json()['revision']}"
    )
    assert committed.status_code == 200
    character = committed.json()["character"]

    source_name = avatar["src"].rsplit("/", maxsplit=1)[-1]
    assert not (tmp_path / "runtime" / "data" / "avatars" / source_name).exists()
    assert character["avatar"]["src"].startswith("/api/v1/character/files/")
    destination = character["avatar"]["src"].removeprefix("/api/v1/character/files/")
    assert (tmp_path / "runtime" / "data" / "characters" / destination).is_file()


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
