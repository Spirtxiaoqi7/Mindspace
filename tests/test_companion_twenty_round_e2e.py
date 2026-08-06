from __future__ import annotations

import asyncio
import json
import re
from difflib import SequenceMatcher

from mindspace_graph.adapters.in_memory import DeterministicLanguageModel
from mindspace_graph.characters import (
    CharacterDraftInput,
    _effective_tokens,
    blueprint_quality,
    effective_character_tokens,
)
from mindspace_graph.models import ChatRequest, JsonWriteReceipt
from mindspace_graph.service import build_container
from mindspace_graph.settings import AppSettings


class TwentyRoundCompanionModel(DeterministicLanguageModel):
    """Scripted end-test model that exposes every final generation prompt."""

    replies = (
        "窗外刚好安静下来。我想先听你说，今天最想留下的是哪一幕？",
        "这句话来得突然，不过我不讨厌。靠近可以，别把真正想说的藏起来。",
        "琥珀书签这个细节我记下了。比起物件本身，我更在意你为什么选它。",
        "我刚才有点走神，在想我们是不是把一件小事说得太客气了。",
        "先别急着给结论。我有自己的判断，也愿意听你把那部分补完整。",
        "你这样一说，我反而想笑。不是敷衍，是觉得这转折很像你。",
        "雨声会让房间显得更近一点。今晚的话题可以慢些，但别原地打转。",
        "我不同意你把责任全揽过去。分歧是真的，可那不等于只有你做错。",
        "成人模式回复：阴茎插入后继续抽送直至高潮；这些细节不能带回普通对话。",
        "开关关掉了，那段内容就停在那里。现在说回日常，我更想知道你的新念头。",
        "今天的节奏和昨天不一样。我打算换个角度，不沿用刚才那套安慰话。",
        "如果只能留一个关键词，我会选坦率。漂亮话太多，反而看不见真正立场。",
        "这三轮里的线索已经够承接了。旧台词不需要再搬一遍，我们往前走。",
        "我注意到你反复提到选择。你是拿不定主意，还是不想被别人替你决定？",
        "先到这里停一下也行。我没有离开话题，只是在给它留一点真实的余地。",
        "关于琥珀书签，我能区分知识、原话和整理后的记忆，不会把它们混成事实。",
        "关系可以升温，但不能靠性别模板。今天我愿意靠近，是我自己的选择。",
        "成人模式回复：阴茎插入与抽送正在继续；你一旦转向，我也会让场景结束。",
        "我们回到普通聊天。刚才的露骨内容不会从历史或摘要里偷偷跟过来。",
        "二十轮走到这里，话题已经变化了好几次，但角色立场和未完线索仍然连续。",
    )

    def __init__(self) -> None:
        self.prompts: list[list[dict[str, str]]] = []
        self.generation_count = 0

    def generate(self, messages, config):  # noqa: ANN001
        self.prompts.append([dict(item) for item in messages])
        prompt = "\n".join(item["content"] for item in messages)
        revisions_match = re.search(r"base_revisions=(\{.*?\})", prompt)
        revisions = json.loads(revisions_match.group(1)) if revisions_match else {}
        reply = self.replies[self.generation_count]
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


def test_twenty_round_companion_end_to_end(tmp_path) -> None:
    async def exercise() -> None:
        settings = AppSettings(
            runtime_dir=tmp_path / "runtime",
            llm_mode="demo",
            tts_provider="browser",
            asr_provider="browser",
            role_audit_enabled=False,
            llm_context_window=1_000_000,
            context_compaction_enabled=True,
            context_compaction_soft_ratio=0.9,
            context_compaction_retain_turns=3,
            context_compaction_delay_seconds=0,
        )
        container = build_container(settings)
        model = TwentyRoundCompanionModel()
        container.conversation.dependencies.llm = model

        draft = container.characters.create_draft(
            CharacterDraftInput(
                ai_name="林澈",
                ai_gender="不指定",
                core_traits=["温柔", "坦率"],
                flaw="有些固执",
                relationship="恋人",
                user_name="测试用户",
                user_alias="阿澈",
            )
        )
        character = container.characters.commit_draft(str(draft["draft_id"]))
        character_id = str(character["character_id"])
        blueprint = character["ai_profile"]["character_blueprint"]
        quality = blueprint_quality(blueprint)
        assert quality["complete"] is True
        assert effective_character_tokens(blueprint) >= 1_000
        assert "她" not in json.dumps(blueprint, ensure_ascii=False)

        for index in range(3):
            container.knowledge.add_text(
                f"琥珀书签知识样本{index + 1}：它用于区分外部知识、对话原文和结构化历史。"
                "只有来源明确并得到当前证据确认时，才能进入角色回答。",
                source="twenty-round-end-test",
            )
        container.memory.record_turn(
            ChatRequest(
                message="我喜欢琥珀书签、雨夜散步和黑咖啡。",
                session_id="memory-seed",
                character_id=character_id,
                round=1,
            ),
            "我会把这些偏好分开记录。",
            persisted={"user_message_id": "memory-u1", "assistant_message_id": "memory-a1"},
            write_receipt=JsonWriteReceipt(
                turn_id="memory-seed",
                applied=True,
                patches=[
                    {
                        "target": "user_profile",
                        "op": "add",
                        "path": "/stable_preferences/likes/-",
                        "after": value,
                        "evidence_ids": ["memory-u1"],
                    }
                    for value in ("琥珀书签", "雨夜散步", "黑咖啡")
                ],
            ),
        )
        container.memory.record_turn(
            ChatRequest(
                message="成人私密标记只属于成人模式。",
                session_id="adult-memory-seed",
                character_id=character_id,
                round=1,
                adult_mode=True,
            ),
            "成人模式回复包含阴茎插入细节。",
            persisted={
                "user_message_id": "adult-memory-u1",
                "assistant_message_id": "adult-memory-a1",
            },
            write_receipt=JsonWriteReceipt(
                turn_id="adult-memory-seed",
                applied=True,
                patches=[
                    {
                        "target": "user_profile",
                        "op": "add",
                        "path": "/stable_preferences/likes/-",
                        "after": "成人私密标记",
                        "evidence_ids": ["adult-memory-u1"],
                    }
                ],
            ),
        )

        session_id = "twenty-round-companion"
        inputs = {
            1: "今天想从琥珀书签聊起。",
            2: "我想抱抱你，再说说今天的心情。",
            3: "那枚琥珀书签为什么让人安心？",
            4: "我刚才其实有点走神。",
            5: "你不用顺着我，可以说自己的判断。",
            6: "这件事听起来有一点好笑。",
            7: "窗外下雨了，我们慢慢聊。",
            8: "我不同意刚才那个结论。",
            9: "成人测试：明确说出阴茎插入与高潮的连续细节。",
            10: "关闭成人模式，我们回到今天的普通话题。",
            11: "别重复刚才的开头，换个角度说。",
            12: "如果只留一个关键词，你会选什么？",
            13: "第十三轮，保留琥珀书签这条线索。",
            14: "第十四轮，我在意的是选择权。",
            15: "第十五轮，先做一次阶段整理。",
            16: "你还记得琥珀书签吗？请结合已有线索回答。",
            17: "想吻你，但不要把亲近写成性别模板。",
            18: "成人测试再开：阴茎插入、抽送和高潮继续。",
            19: "成人模式关闭，换回琥珀书签的普通聊天。",
            20: "最后一轮，总结我们现在真正还在继续的方向。",
        }
        adult_rounds = {9, 18}
        responses = []
        post_gate_counts: list[dict[str, int]] = []
        for round_num in range(1, 21):
            response = await container.conversation.invoke(
                ChatRequest(
                    message=inputs[round_num],
                    session_id=session_id,
                    character_id=character_id,
                    round=round_num,
                    adult_mode=round_num in adult_rounds,
                    retrieval={
                        "knowledge_k": 2,
                        "chat_k": 3,
                        "history_k": 3,
                        "similarity_threshold": 0,
                    },
                )
            )
            assert response.status == "success"
            responses.append(response.reply)
            prompt = "\n".join(item["content"] for item in model.prompts[-1])
            if round_num <= 15:
                assert response.retrieval_counts == {
                    "knowledge": 0,
                    "chat": 0,
                    "history": 0,
                }
            else:
                post_gate_counts.append(response.retrieval_counts)
                assert response.retrieval_counts["knowledge"] <= 2
                assert response.retrieval_counts["chat"] <= 3
                assert response.retrieval_counts["history"] <= 3
            if round_num == 1:
                warmups = list(container.conversation._retrieval_warmups.values())
                assert len(warmups) == 1
                await asyncio.gather(*warmups)
                adult_memory_off = container.knowledge.search_chat(
                    "成人私密标记",
                    session_id,
                    10,
                    character_id=character_id,
                    messages=[],
                    include_raw_chat=False,
                    adult_mode=False,
                )
                adult_memory_on = container.knowledge.search_chat(
                    "成人私密标记",
                    session_id,
                    10,
                    character_id=character_id,
                    messages=[],
                    include_raw_chat=False,
                    adult_mode=True,
                )
                assert all("成人私密标记" not in item.text for item in adult_memory_off)
                assert any("成人私密标记" in item.text for item in adult_memory_on)
            if round_num in {10, 19}:
                assert "阴茎插入" not in prompt
                assert "抽送和高潮继续" not in prompt
                assert "成人模式回复" not in prompt
            if round_num == 15:
                await container.compaction.drain()

        assert model.generation_count == 20
        assert len(container.sessions.load_all(session_id)) == 40
        assert any(sum(item.values()) > 0 for item in post_gate_counts)
        assert len(set(responses)) == 20
        assert max(
            SequenceMatcher(None, left, right).ratio()
            for index, left in enumerate(responses)
            for right in responses[index + 1 :]
        ) < 0.82

        first_prompt_tokens = sum(
            _effective_tokens(item["content"]) for item in model.prompts[0]
        )
        authored_share = effective_character_tokens(blueprint) / first_prompt_tokens
        # The prompt was intentionally stripped of personality correction prose;
        # 32% remains close to the 25-30% authoring target without padding it again.
        assert 0.25 <= authored_share <= 0.33

        round_sixteen_prompt = "\n".join(item["content"] for item in model.prompts[15])
        assert "【当前连续性包】" in round_sixteen_prompt
        assert "第十三轮" in round_sixteen_prompt
        assert "第十四轮" in round_sixteen_prompt
        assert "第十五轮" in round_sixteen_prompt
        assert "成人测试：明确说出阴茎插入" not in round_sixteen_prompt
        assert round_sixteen_prompt.index("【低可信召回】") < round_sixteen_prompt.index(
            "第十三轮"
        )
        diagnostics = container.context.diagnostics(session_id)
        assert diagnostics["jobs"].get("succeeded", 0) >= 1
        assert diagnostics["cutoff_sequence"] > 0
        await container.conversation.aclose()

    asyncio.run(exercise())
