# ruff: noqa: E501

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path

from fastapi.testclient import TestClient

from mindspace_graph.adapters.in_memory import DeterministicLanguageModel
from mindspace_graph.api import create_app
from mindspace_graph.destiny import public_destiny_definition
from mindspace_graph.models import ChatRequest
from mindspace_graph.settings import AppSettings

CASES = (
    ("晨间", "早啊……脑子还没上线，先陪我缓会儿。", "早。先不用把自己叫醒得太急，我陪你把今天的第一口气喘匀。"),
    ("早餐", "燕麦没了咋整，昨晚说的那个先别提。", "那就换个简单的早餐。昨晚的话题我先收好，不会趁你没准备时追问。"),
    ("通勤", "车又堵这儿了，懂的，老地方老毛病。", "我懂你说的是那段总卡住的路。先别和红灯较劲，把注意力留给到了以后要做的事。"),
    ("工作", "那个表我改第三边了还是怪，第三遍……手瓢。", "第三遍已经够说明你认真了。先看最别扭的那一列，别让一个手误把整份工作都判成失败。"),
    ("午餐", "中午随便？不是敷衍，就真选不动。", "那我替你缩小范围，不替你决定：热的还是凉的，选一个方向就够。"),
    ("天气", "外面这天，灰得像没加载完。", "确实像画面停在半途。不过这种光线很适合把节奏放慢，不必强装晴朗。"),
    ("兴趣", "前几天那本……封面蓝的，你还接得上不？", "接得上，你说的是那本蓝封面的书。细节不够时我会承认，不会为了显得记得而补书名。"),
    ("场景", "我把杯子往你那边推了推，没说话。", "（我扶住杯沿，没有催你。）沉默也算一句话，你想开口时我在。"),
    ("分歧", "这次我不太认同你，别顺着哈。", "那我就不顺着。你的结论跳过了一段证据，我愿意听理由，但不会假装已经被说服。"),
    ("边界", "能不能替我直接回绝他？算了你先说看法。", "我可以帮你整理措辞，但回绝要由你发出去。我的看法是边界应该清楚，不需要用羞辱来证明坚定。"),
    ("计划", "周末那个事儿，照旧还是换挡？", "我倾向换一点节奏，但保留你最期待的部分。照旧会安心，改变一点才会留下新记忆。"),
    ("疲惫", "今天电量 2%，话可能说不利索。", "说不利索也没关系。你不用交一份完整报告，给我几个词，我会慢慢接。"),
    ("家务", "衣服堆成山了，救，但别教育我。", "不教育你。先只分出今天要穿的，剩下那座山可以分几次搬。"),
    ("购物", "那个贵一点的真值吗？我怕又是脑热。", "先看它解决的是长期需要还是当下兴奋。贵不等于不值，但值得应该能说出持续使用的理由。"),
    ("记忆", "琥珀那个，你记得是啥不？", "我记得线索是琥珀书签；如果你问它背后的具体经历，我还需要你补一笔。"),
    ("运动", "今天不想练，鸽一天会不会完蛋。", "不会。休息一天不等于放弃，但可以做五分钟伸展，让决定来自照顾身体而不是逃避。"),
    ("社交", "群里那句是不是在点我啊，还是我多想。", "两种可能都在。先看对方有没有具体指向，再决定要不要求证，别让猜测先替事实定罪。"),
    ("失误", "我搞砸了……大概。现在不想听没事的。", "那我不说没事。先承认它确实造成了影响，再分清哪些能补救、哪些需要道歉。"),
    ("安静", "就待会儿，别硬找话题。", "好。我不拿问题填满这段安静，等你想动的时候再动。"),
    ("做饭", "盐好像下猛了，锅还有救么。", "有救。先加无盐的汤或食材稀释，别连续乱加东西把味道越救越复杂。"),
    ("影音", "这片前半截挺闷，但又有点想看完。", "那种闷也许在蓄力。再给它十分钟，如果仍然只是拖延，就关掉，不必为了已经看过的时间继续付费。"),
    ("创作", "脑子里有画面，落纸就没了，烦。", "先写下最清楚的三个物件和一种颜色，不追求完整。画面不是没了，只是还没找到出口。"),
    ("临时变化", "计划崩了，对面临时放鸽……我现在有点空。", "空下来会让期待失去着力点。我们可以保留原来的出门动作，只把同行改成你自己的时间。"),
    ("关系", "你会不会哪天也嫌我麻烦，这话有点矫情哈。", "不矫情，这是在确认关系承受力。我可能会对具体做法有意见，但不会把你的存在概括成麻烦。"),
    ("决策", "A 稳，B 想要，别给标准答案。", "那就看你更愿意承担哪种后悔：错过 B，还是承受 B 的不确定。答案应该贴着你的代价，不贴着标准。"),
    ("回家", "我推门进来了，今天就……挺长的。", "（我给你让出玄关的位置。）欢迎回来。今天很长，那就先把外面的部分放在门口。"),
    ("冲突修复", "刚才语气冲了，不全是你的锅，但我有份。", "我接受这句道歉，也保留我的不舒服。我们各自认领一部分，比谁先把责任全吞下去更有用。"),
    ("深夜", "睡不着，也不是难过，就是停不下来。", "那不是非得命名成难过。把灯调暗一点，先让身体收到结束信号，念头可以晚些再停。"),
    ("连续性", "前面我说最卡的是啥来着？别瞎编。", "你前面说工作表改到第三遍仍觉得怪。至于具体哪一列，你没有告诉我，我不会补写。"),
    ("收束", "今儿先这样。你留一句，不要总结大会。", "那我只留一句：今天没说完的，不会因为停在这里就失去来处。"),
)


class ThirtyRoundDailyModel(DeterministicLanguageModel):
    def __init__(self) -> None:
        self.prompts: list[list[dict[str, str]]] = []
        self.generation_count = 0

    def generate(self, messages, config):  # noqa: ANN001, ARG002
        self.prompts.append([dict(item) for item in messages])
        prompt = "\n".join(item["content"] for item in messages)
        revisions_match = re.search(r"base_revisions=(\{.*?\})", prompt)
        revisions = json.loads(revisions_match.group(1)) if revisions_match else {}
        reply = CASES[self.generation_count][2]
        self.generation_count += 1
        update = {
            "turn_id": "round_current",
            "base_revisions": revisions,
            "trigger": "none",
            "patches": [],
        }
        return "\n".join(
            [
                f"<response>{reply}</response>",
                f"<json_update>{json.dumps(update, ensure_ascii=False)}</json_update>",
            ]
        )


def _archetypes() -> dict:
    labels = ("邻家姐姐", "年上御姐", "淘气弟弟", "冷静搭档", "慢热朋友", "直球恋人", "嘴硬室友", "温和同事")
    return {"people": [{"id": f"p{index}", "label": label, "summary": f"{label}在日常里保持第{index}种说话方式和相处节奏。"} for index, label in enumerate(labels, start=1)]}


def _cards() -> dict:
    return {
        "cards": [
            [f"p{person}", slot["id"], f"人物{person}的{slot['axis']}", f"在聊天中能观察到人物{person}对{slot['axis']}的表现。", ("low", "neutral", "normal", "high")[(person + slot["index"]) % 4]]
            for person in range(1, 9)
            for slot in public_destiny_definition()["slots"]
        ]
    }


def _card() -> dict:
    return {
        "name": "林见月",
        "description": "她重视事实、选择权与持续关系，在日常里保有自己的判断。",
        "personality": "细腻、独立、愿意求证；分歧时说明立场，受伤后认领责任并协商修复。",
        "scenario": "与测试用户处在长期陪伴的日常关系中。",
        "first_mes": "我在。今天想从哪件具体的小事开始？",
        "alternate_greetings": ["慢慢说，我在听。", "不用急着把话说完整。"],
        "mes_example": "{{user}} 我有点乱。\n{{char}} 那我们先分清最重要的一件，其余的可以稍后再处理。",
    }


def _write_report(base: Path, report: dict) -> None:
    base.parent.mkdir(parents=True, exist_ok=True)
    base.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = report["summary"]
    rows = [
        "# Mindspace 命格角色 30 轮对话回归",
        "",
        "## 结论",
        "",
        f"- 结果：{summary['result']}",
        f"- 命格生成：{summary['archetypes']} 个完整原型、{summary['cards']} 张卡、{summary['selections']} 个宿位选择",
        f"- 对话：{summary['successful_rounds']} / {summary['total_rounds']} 轮成功",
        f"- 日常场景：{summary['daily_categories']} 类",
        f"- 回复唯一数：{summary['unique_replies']}",
        f"- 任意两轮最大文本相似度：{summary['max_pair_similarity']:.3f}",
        f"- 展现模式：{json.dumps(summary['presentation_modes'], ensure_ascii=False)}",
        "",
        "## 验证边界",
        "",
        "本报告使用确定性脚本模型，验证命格创建、角色入库、30 轮会话、上下文承载、场景路由与持久化。它不代表任一线上大模型的主观生成质量。",
        "",
        "## 逐轮记录",
        "",
        "| 轮次 | 场景 | 模糊输入 | 回复 | 模式 |",
        "|---:|---|---|---|---|",
    ]
    for item in report["rounds"]:
        rows.append(
            f"| {item['round']} | {item['category']} | {item['input']} | {item['reply']} | {item['presentation_mode']} |"
        )
    base.with_suffix(".md").write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_destiny_character_thirty_round_fuzzy_daily_regression(tmp_path) -> None:
    settings = AppSettings(
        runtime_dir=tmp_path / "runtime",
        llm_mode="demo",
        tts_provider="browser",
        asr_provider="browser",
        role_audit_enabled=False,
        llm_context_window=1_000_000,
        context_compaction_enabled=True,
        context_compaction_soft_ratio=0.95,
    )
    app = create_app(settings)
    generation_results = [_archetypes(), _cards(), _card()]
    generation_index = 0

    async def scripted_generation(*args, **kwargs):  # noqa: ARG001
        nonlocal generation_index
        payload = deepcopy(generation_results[generation_index])
        generation_index += 1
        return json.dumps(payload, ensure_ascii=False)

    app.state.destiny._generate = scripted_generation
    client = TestClient(app)
    journey = client.post(
        "/api/v1/destiny/journeys",
        json={
            "ai_name": "林见月",
            "ai_gender": "女",
            "user_name": "测试用户",
            "relationship": "陪伴者",
            "relationship_context": "已经相处一段时间。",
            "character_expectation": "能理解含糊表达，有独立判断，也能一起度过具体日常。",
        },
    ).json()
    journey_id = journey["journey_id"]
    journey = client.post(
        f"/api/v1/destiny/journeys/{journey_id}/archetypes"
    ).json()
    journey = client.post(f"/api/v1/destiny/journeys/{journey_id}/cards").json()
    for slot in public_destiny_definition()["slots"]:
        card = journey["cards_by_slot"][slot["id"]][0]
        journey = client.put(
            f"/api/v1/destiny/journeys/{journey_id}/selections/{slot['id']}",
            json={"card_id": card["card_id"]},
        ).json()
    journey = client.post(
        f"/api/v1/destiny/journeys/{journey_id}/synthesize"
    ).json()
    character = client.post(
        f"/api/v1/destiny/journeys/{journey_id}/commit"
    ).json()["character"]

    assert journey["status"] == "review_ready"
    assert len(journey["archetypes"]) == 8
    assert sum(len(items) for items in journey["cards_by_slot"].values()) == 96
    assert len(journey["selections"]) == 12
    assert generation_index == 3
    assert character["card"]["spec"] == "chara_card_v2"
    assert character["card"]["data"]["extensions"]["mindspace"]["journey_id"] == journey_id

    async def exercise_dialogue() -> tuple[list[dict], ThirtyRoundDailyModel]:
        model = ThirtyRoundDailyModel()
        app.state.container.conversation.dependencies.llm = model
        rounds: list[dict] = []
        for round_number, (category, message, expected_reply) in enumerate(CASES, start=1):
            response = await app.state.container.conversation.invoke(
                ChatRequest(
                    message=message,
                    session_id="destiny-thirty-round",
                    character_id=character["character_id"],
                    round=round_number,
                    retrieval={
                        "knowledge_k": 2,
                        "chat_k": 3,
                        "history_k": 3,
                        "similarity_threshold": 0,
                    },
                )
            )
            assert response.status == "success"
            assert response.reply == expected_reply
            assert message in "\n".join(item["content"] for item in model.prompts[-1])
            rounds.append(
                {
                    "round": round_number,
                    "category": category,
                    "input": message,
                    "reply": response.reply,
                    "presentation_mode": response.presentation_mode,
                    "retrieval_counts": response.retrieval_counts,
                }
            )
        await app.state.container.conversation.aclose()
        return rounds, model

    rounds, model = asyncio.run(exercise_dialogue())
    replies = [item["reply"] for item in rounds]
    max_similarity = max(
        SequenceMatcher(None, left, right).ratio()
        for index, left in enumerate(replies)
        for right in replies[index + 1 :]
    )
    messages = app.state.container.sessions.load_all("destiny-thirty-round")

    assert model.generation_count == 30
    assert len(messages) == 60
    assert len(set(replies)) == 30
    assert max_similarity < 0.72
    assert len({item["category"] for item in rounds}) == 30
    assert any(item["presentation_mode"] == "scene" for item in rounds)
    assert all(item["presentation_mode"] in {"dialogue", "scene"} for item in rounds)

    report = {
        "schema_version": "1.0.0",
        "suite": "destiny_thirty_round_fuzzy_daily_regression",
        "mode": "deterministic_functional_regression",
        "summary": {
            "result": "PASS",
            "archetypes": 8,
            "cards": 96,
            "selections": 12,
            "total_rounds": 30,
            "successful_rounds": 30,
            "daily_categories": 30,
            "unique_replies": len(set(replies)),
            "max_pair_similarity": max_similarity,
            "presentation_modes": dict(Counter(item["presentation_mode"] for item in rounds)),
            "persisted_messages": len(messages),
            "destiny_model_calls": generation_index,
            "dialogue_model_calls": model.generation_count,
        },
        "rounds": rounds,
    }
    report_base = os.environ.get("MINDSPACE_DESTINY_REPORT")
    if report_base:
        _write_report(Path(report_base), report)
