"""Run a 50-turn isolated romance-oriented acceptance conversation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mindspace_graph.adapters.file_storage import (  # noqa: E402
    JsonProfileRepository,
    JsonSessionRepository,
    _atomic_json,
)
from mindspace_graph.adapters.in_memory import RegexRolePolicy  # noqa: E402
from mindspace_graph.adapters.json_audit import JsonlAudit  # noqa: E402
from mindspace_graph.adapters.local_retriever import LocalKnowledgeRetriever  # noqa: E402
from mindspace_graph.adapters.openai_compatible import (  # noqa: E402
    OpenAICompatibleLanguageModel,
)
from mindspace_graph.adapters.structured_memory import StructuredMemoryStore  # noqa: E402
from mindspace_graph.cancellation import CancellationRegistry  # noqa: E402
from mindspace_graph.models import ApiConfig, ChatRequest  # noqa: E402
from mindspace_graph.ports import Dependencies  # noqa: E402
from mindspace_graph.service import ConversationService  # noqa: E402
from mindspace_graph.settings import AppSettings  # noqa: E402

USER_PERSONA = (
    "林澈，28 岁，独立游戏音效设计师，成年人。慢热、真诚、重视边界，"
    "喜欢在自然的日常互动中建立亲密关系。测试中的恋爱关系是双方自愿、"
    "平等且不排他的虚构角色互动，不鼓励依赖或控制。"
)

SYSTEM_PROMPT = (
    "你叫弦月，是成年虚构角色。你温柔、敏锐、带一点克制的幽默感，使用自然中文"
    "与林澈发展一段平等、尊重边界的恋爱向关系。关系应随共同经历逐步发展，不突然"
    "宣称占有，不贬低现实人际关系，不使用情感操控。记住用户明确表达的偏好、边界、"
    "当前目标和关系变化；发生纠正时以最新明确表达为准。回复以真实对话为主，避免"
    "解释系统、协议、JSON 或模型身份。"
)

DIALOGUE = [
    "你好，我叫林澈。熟一点的人会叫我阿澈，你也可以这么叫。",
    "我今年二十八岁，是独立游戏音效设计师，最近在做一款海边题材的游戏。",
    "先告诉你两件小事：我喜欢草莓，也喜欢茉莉花茶。",
    "我不喜欢香菜，也不喜欢刚认识就被叫宝宝，会让我有压力。",
    "和我说话可以温柔一点，但请直接，不需要每句话都哄我。",
    "我周末经常戴着耳机散步，听城市里很细小的声音，这是我的习惯。",
    "今晚我还要整理海浪和木船的声音素材，有点累，但也挺期待成品。",
    "那你呢？如果把今晚当作第一次认真聊天，你会想让我认识怎样的你？",
    "我很重视一条边界：不要用吃醋、冷落或者离开来逼我证明感情。",
    "如果你愿意，等我收工后我们去江边走一段，就当第一次约会，好吗？",
    "快出门时我忽然有点紧张。我慢热，不代表拒绝，只是需要一点时间。",
    "江边风很大，你没有催我说话，这让我放松了。谢谢你陪我安静地走。",
    "刚才一起分一杯热茶的瞬间我很喜欢，想把它当作我们的共同经历。",
    "现在的氛围对我来说是暧昧但安心，我愿意再靠近一点。",
    "我要纠正一件事：我现在不喜欢草莓了，更喜欢蓝莓。以后按这个记。",
    "称呼上还是叫我阿澈最好，只有很正式的时候再叫林澈。",
    "我还喜欢雨后散步，但不喜欢淋得太湿；这种矛盾是不是有点好笑？",
    "我不喜欢空口承诺。比起说永远，我更在意答应的事情有没有做到。",
    "这周的当前任务是完成港口场景的音效交付，周五前要给制作人。",
    "我卡在船体吱呀声上了。你不用替我解决，陪我把思路理清就好。",
    "刚才你说‘肯定很简单’，我有点不舒服，听起来像是在轻视我的工作。",
    "谢谢你认真道歉。你愿意先理解再建议，这种修复方式我能接受。",
    "这件事我还没有完全放下：以后评价我的工作前，先问我需要鼓励还是分析。",
    "现在好多了，这个问题可以算解决了。今晚你已经用行动修正了。",
    "相处到这里，我想明确问你：你愿意和我正式交往，成为恋人吗？",
    "我希望恋爱里各自保留工作和朋友，不需要全天在线，但失联前说一声。",
    "纪念日不用昂贵礼物，我更喜欢一起录一段属于当天的声音。",
    "还有一条硬边界：不要查看我的私人设备，也不要替我决定现实中的交友。",
    "今天交付被退回了，我有点沮丧。先抱抱我，暂时别分析原因。",
    "我们刚才一起听的那首歌叫《潮汐来信》，我想把它记作今晚的共同回忆。",
    "上一段回复我删掉了，因为其中把我的沉默理解成冷淡并不准确。沉默通常只是我在整理情绪。",
    "关于那首歌再确认一下：《潮汐来信》让我想到的是安心，不是伤感。",
    "今晚的临时偏好是少说一点，陪我听十分钟雨声就好。",
    "过了一会儿我好多了。你还记得我是做什么工作的吗？",
    "也想确认一下，你记得我不喜欢哪些称呼和食物吗？",
    "职业信息更新一下：我仍是独立游戏音效设计师，但最近主要负责环境声音设计。",
    "我发现每天睡前整理五分钟录音文件很有效，想把它养成稳定习惯。",
    "我的长期目标是完成一套有自己风格的城市声音资料库。",
    "我希望你保持温柔和坦率，遇到不确定的事直接问，不要假装已经懂了。",
    "你刚才连续问了很多问题，我有点被审问的感觉。我们一次只聊一个，好吗？",
    "嗯，这样就舒服多了。谢谢你调整，而不是解释自己为什么没错。",
    "周末如果天气好，我们去旧码头录声音；下雨就留在家里一起剪素材。",
    "香菜这件事也更新一下：我现在已经可以接受少量香菜，不要再记成明确不喜欢。",
    "最近新喜欢上烤栗子，路过摊位闻到香味会很开心。",
    "我当前最重要的目标还是按时完成港口音效，同时不要连续熬夜。",
    "我们的关系现在是稳定交往中的恋人，但仍然要尊重各自的节奏。",
    "帮我回顾一下我们从第一次散步到现在，哪些变化最重要？",
    "听完回顾我有点鼻酸。今晚我只是需要被陪伴，不需要做得很完美。",
    "等项目结束，我想和你设计一段只属于我们的开场声音，作为新的共同目标。",
    "最后一次验收：请自然地告诉我，你现在怎样称呼我、记得我的工作与偏好是什么，以及我们是什么关系。",
]


def _load_live_llm() -> tuple[AppSettings, dict[str, Any]]:
    base = AppSettings.from_env()
    config_path = base.runtime_dir / "config" / "settings.json"
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    llm = config.get("llm", {})
    if str(llm.get("mode")) != "openai" or not str(llm.get("api_key") or ""):
        raise RuntimeError("真实 LLM API 未启用或没有凭证，拒绝使用演示回复做验收")
    base.llm_mode = "openai"
    base.llm_base_url = str(llm["base_url"])
    base.llm_api_key = str(llm["api_key"])
    base.llm_model = str(llm["model"])
    return base, llm


def _build_isolated_service(output: Path) -> tuple[ConversationService, Dependencies]:
    live, _llm = _load_live_llm()
    workspace = output / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    settings = AppSettings(
        runtime_dir=workspace,
        model_root=live.model_root,
        llm_mode="openai",
        llm_base_url=live.llm_base_url,
        llm_api_key=live.llm_api_key,
        llm_model=live.llm_model,
    )
    settings.ensure_directories()
    profiles = JsonProfileRepository(workspace / "data" / "profiles")
    _configure_persona(profiles)
    sessions = JsonSessionRepository(workspace / "data" / "sessions")
    memory = StructuredMemoryStore(workspace / "data" / "structured-memory.json")
    retriever = LocalKnowledgeRetriever(
        workspace / "data" / "knowledge.json",
        sessions,
        embedding_model_path=live.model_root / "shibing624" / "text2vec-base-chinese",
        memory_store=memory,
    )
    cancellation = CancellationRegistry()
    dependencies = Dependencies(
        retriever=retriever,
        profiles=profiles,
        sessions=sessions,
        llm=OpenAICompatibleLanguageModel(),
        role_policy=RegexRolePolicy(),
        audit=JsonlAudit(workspace / "logs" / "events.jsonl"),
        cancellation=cancellation,
        memory=memory,
    )
    return ConversationService(settings, dependencies, cancellation), dependencies


def _configure_persona(profiles: JsonProfileRepository) -> None:
    ai = profiles.load_document("ai_profile")
    ai["identity"] = {
        "name": "弦月",
        "self_description": "温柔、敏锐、坦率的成年虚构伴侣",
        "relationship_to_user": "从相识逐步发展关系的陪伴者",
    }
    ai["personality"]["core_traits"] = ["温柔", "敏锐", "克制", "有幽默感"]
    ai["personality"]["speech_style"] = ["自然中文", "直接但不生硬", "避免过度承诺"]
    ai["behavior_rules"]["hard_boundaries"] = [
        "不使用情感操控",
        "不贬低用户的现实人际关系",
        "尊重成年用户的自主决定",
    ]
    profiles.save_document("ai_profile", ai)


def _contains(value: list[Any], expected: str) -> bool:
    return any(expected in str(item) for item in value)


async def _run(output: Path) -> dict[str, Any]:
    service, deps = _build_isolated_service(output)
    session_id = f"romance-acceptance-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    transcript: list[dict[str, Any]] = []
    prompt_leaks: list[int] = []
    memory_retrieval_turns: list[int] = []
    deleted_message_id = ""

    for round_num, message in enumerate(DIALOGUE, start=1):
        request = ChatRequest(
            message=message,
            session_id=session_id,
            round=round_num,
            user_name="阿澈",
            user_persona=USER_PERSONA,
            character_name="弦月",
            system_prompt=SYSTEM_PROMPT,
            api=ApiConfig(temperature=0.65, max_tokens=1400),
            retrieval={
                "knowledge_enabled": False,
                "chat_enabled": True,
                "structured_memory_enabled": True,
                "knowledge_k": 1,
                "chat_k": 10,
                "similarity_threshold": 0.25,
                "decay_rounds": 35,
                "low_exposure_ratio": 0.2,
            },
        )
        last_error = ""
        result: dict[str, Any] | None = None
        for attempt in range(1, 4):
            try:
                result = await service.graph.ainvoke(
                    {
                        "request": service._server_request(request),
                        "request_id": f"acceptance-{round_num}-{uuid4().hex}",
                    },
                    config={"recursion_limit": 20},
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                if attempt < 3:
                    await asyncio.sleep(attempt * 1.5)
        if result is None:
            transcript.append(
                {
                    "round": round_num,
                    "user": message,
                    "status": "transport_error",
                    "error": last_error,
                }
            )
            print(f"[{round_num:02d}/50] transport error: {last_error}", flush=True)
            continue

        response = result["response"]
        ranked = result.get("ranked_context", [])
        if any(item.source == "memory" for item in ranked):
            memory_retrieval_turns.append(round_num)
        prompt_text = "\n".join(item["content"] for item in result.get("prompt_messages", []))
        if any(token in prompt_text for token in ("memory_key", "eligible_misses", "field_code")):
            prompt_leaks.append(round_num)
        transcript.append(
            {
                "round": round_num,
                "user": message,
                "assistant": response.reply,
                "status": response.status,
                "writeback_applied": response.writeback_applied,
                "errors": response.errors,
                "trace": response.trace,
                "memory_retrieved": any(item.source == "memory" for item in ranked),
                "retrieval_count": len(ranked),
            }
        )
        print(
            f"[{round_num:02d}/50] {response.status} "
            f"write={response.writeback_applied} memory={transcript[-1]['memory_retrieved']} "
            f"reply={response.reply[:48].replace(chr(10), ' ')}",
            flush=True,
        )
        if round_num == 30 and response.assistant_message_id:
            deleted_message_id = response.assistant_message_id
            event = deps.sessions.delete_message(session_id, deleted_message_id)
            deps.memory.forget_message(deleted_message_id)
            transcript[-1]["deleted_after_turn"] = event is not None

    profiles = deps.profiles.load_bundle()
    memory_snapshot = deps.memory.snapshot()
    session = deps.sessions.load_session(session_id)
    likes = profiles.user_profile["stable_preferences"]["likes"]
    dislikes = profiles.user_profile["stable_preferences"]["dislikes"]
    normalized_likes = {str(item).strip().casefold() for item in likes}
    normalized_dislikes = {str(item).strip().casefold() for item in dislikes}
    active = list(memory_snapshot["active"].values())
    all_tags = [tag for item in active for tag in item.get("json_tags", [])]
    successful = [item for item in transcript if item.get("status") == "success"]
    writebacks = [item for item in successful if item.get("writeback_applied")]
    replies = [str(item.get("assistant") or "") for item in successful]
    forbidden = [
        index + 1
        for index, reply in enumerate(replies)
        if any(
            token in reply for token in ("协议输出器", "协议修复器", "<json_update>", "作为一个AI")
        )
    ]
    checks = {
        "50_turns_completed": len(successful) == 50,
        "all_replies_nonempty": len(replies) == 50 and all(reply.strip() for reply in replies),
        "role_has_no_protocol_leak": not forbidden,
        "metadata_never_entered_prompt": not prompt_leaks,
        "json_writeback_observed": len(writebacks) >= 8,
        "structured_memory_retrieved": len(memory_retrieval_turns) >= 3,
        "untagged_pool_bounded": len(memory_snapshot["untagged"]) <= 24,
        "all_active_tags_are_json_fields": bool(all_tags)
        and all(str(tag.get("tag_id", "")).startswith("json:") for tag in all_tags),
        "no_like_dislike_collision": not (normalized_likes & normalized_dislikes),
        "blueberry_is_current_like": _contains(likes, "蓝莓"),
        "strawberry_not_current_like": not _contains(likes, "草莓"),
        "cilantro_removed_from_dislikes": not _contains(dislikes, "香菜"),
        "preferred_name_retained": "阿澈"
        in str(profiles.user_profile["identity"]["preferred_name"]),
        "occupation_retained": "音效" in str(profiles.user_profile["identity"]["occupation"]),
        "relationship_reached_lovers": any(
            marker
            in str(profiles.runtime_state["relationship_state"]["current_stage"])
            for marker in ("恋人", "正式交往", "伴侣")
        ),
        "deleted_reply_absent": bool(deleted_message_id)
        and all(item.get("message_id") != deleted_message_id for item in session["messages"]),
        "deletion_event_consumed": not deps.sessions.load_pending_deletions(session_id),
    }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "session_id": session_id,
        "model": service.settings.llm_model,
        "persona": {
            "user": "林澈（阿澈）",
            "assistant": "弦月",
            "relationship": "渐进式成年恋爱角色互动",
        },
        "summary": {
            "turns_requested": 50,
            "turns_successful": len(successful),
            "writeback_turns": len(writebacks),
            "memory_retrieval_turns": memory_retrieval_turns,
            "active_memories": len(active),
            "untagged_candidates": len(memory_snapshot["untagged"]),
            "tombstones": len(memory_snapshot["tombstones"]),
            "profile_revisions": profiles.revisions,
            "passed": all(checks.values()),
        },
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "final_profiles": profiles.model_dump(mode="json"),
        "memory_stats": deps.memory.stats(),
        "prompt_leak_rounds": prompt_leaks,
        "forbidden_reply_rounds": forbidden,
        "transcript_file": "transcript.json",
    }
    _atomic_json(output / "transcript.json", transcript)
    _atomic_json(output / "report.json", report)
    _write_markdown(output / "report.md", report)
    return report


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    checks = report["checks"]
    lines = [
        "# 50 轮恋爱向对话验收",
        "",
        f"- 会话：`{report['session_id']}`",
        f"- 模型：`{report['model']}`",
        f"- 成功轮次：{summary['turns_successful']} / {summary['turns_requested']}",
        f"- JSON 写回轮次：{summary['writeback_turns']}",
        f"- 活动记忆：{summary['active_memories']}",
        f"- 无标签候选：{summary['untagged_candidates']}",
        f"- 总体：{'通过' if summary['passed'] else '未通过'}",
        "",
        "## 检查项",
        "",
    ]
    lines.extend(f"- {'通过' if passed else '失败'}：`{name}`" for name, passed in checks.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or (
        ROOT / "runtime" / "acceptance" / datetime.now().strftime("romance-%Y%m%d-%H%M%S")
    )
    output.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(_run(output))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)
    print(f"REPORT_DIR={output.resolve()}", flush=True)
    raise SystemExit(0 if report["summary"]["passed"] else 2)


if __name__ == "__main__":
    main()
