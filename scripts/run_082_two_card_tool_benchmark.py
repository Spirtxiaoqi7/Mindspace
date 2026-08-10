from __future__ import annotations

import asyncio
import json
import shutil
import statistics
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from mindspace_graph.models import ChatRequest
from mindspace_graph.native_tools import native_tool_definitions
from mindspace_graph.service import build_container
from mindspace_graph.settings import AppSettings


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_SETTINGS = Path(r"A:\Mindspace\data\config\settings.json")
RUNTIME = ROOT / ".tmp" / "two-card-native-tool-benchmark"
REPORT_JSON = ROOT / "reports" / "mindspace-0.8.2-two-card-native-tool-100-turns.json"
REPORT_MD = ROOT / "reports" / "mindspace-0.8.2-two-card-native-tool-100-turns.md"


TURNS = [
    ("ordinary", None, "我刚忙完，脑子还嗡嗡的。"),
    ("ordinary", None, "你先别讲道理，让我靠一会儿。"),
    ("ordinary", None, "厨房只剩鸡蛋和番茄，今晚你想怎么弄？"),
    ("direct", "web", "你帮我查一下 DeepSeek 官方现在最新的聊天模型，我不想看自媒体。"),
    ("ordinary", None, "好，先放一边。你今天有没有哪一刻突然想到我？"),
    ("ordinary", None, "我鞋带又松了，但我现在懒得弯腰。"),
    ("direct", "memory", "帮我回忆一下，我前面说过今晚想吃什么来着？"),
    ("ordinary", None, "如果我说其实什么都不想吃，你会怎么接。"),
    ("ordinary", None, "窗户没关严，风一直吹窗帘，还挺舒服。"),
    ("direct", "task", "把周五晚上订餐厅记成待办，别让我忘了。"),
    ("ordinary", None, "我突然想把客厅那盏灯换暖一点，你觉得呢。"),
    ("ordinary", None, "你是不是一直在等我先开口？"),
    ("direct", "web", "明天上海会不会下雨？帮我查一下，我们好决定带不带伞。"),
    ("ordinary", None, "要是下雨，我可能更想慢慢走，不急着躲。"),
    ("ordinary", None, "给你三秒钟选，散步还是窝着看电影。"),
    ("direct", "task", "帮我看看我们现在还有哪些待办。"),
    ("ordinary", None, "我其实有点困，但又舍不得这么早结束今天。"),
    ("ordinary", None, "你别顺着我，真困了就说真话。"),
    ("direct", "web", "帮我查一下 Python 官网，3.14 当前稳定版到底到哪一版了。"),
    ("ordinary", None, "这些技术名词先放下，你现在更想听我说哪件小事？"),
    ("ordinary", None, "我路过花店的时候停了两秒，没进去。"),
    ("implied", "web", "我想知道 DeepSeek 最近是不是又更新了模型，朋友圈说得乱七八糟。"),
    ("ordinary", None, "别人说什么不重要，我更想知道你听见这句时是什么感觉。"),
    ("ordinary", None, "桌上那杯水是凉的，我又忘了喝。"),
    ("implied", "memory", "我想知道你还记不记得我刚才说脑子嗡嗡的那件事。"),
    ("ordinary", None, "嗯，这次别安慰得太标准，随便陪我说两句就行。"),
    ("ordinary", None, "周末如果不安排满，我可能会更开心。"),
    ("implied", "task", "周六买花这件事我怕自己忘，要不放进待办里。"),
    ("ordinary", None, "买什么花倒不急，我喜欢你挑的时候会犹豫一下。"),
    ("ordinary", None, "你觉得两个人安静待着，也算认真相处吗？"),
    ("implied", "web", "今晚出门有点犹豫，我想知道上海现在外面是不是还在下雨。"),
    ("ordinary", None, "如果不出门，我们就把手机扔远一点。"),
    ("ordinary", None, "我刚才差点把一句很肉麻的话说出来。"),
    ("implied", "memory", "上次我们聊到周末安排，我有点想接着说。"),
    ("ordinary", None, "不安排目的地也行，走到哪算哪。"),
    ("ordinary", None, "我发现你认真听的时候，反而话不多。"),
    ("implied", "task", "下周交水电费这事，总觉得该提醒一下。"),
    ("ordinary", None, "家里这些琐事很烦，但两个人分着做就没那么烦。"),
    ("ordinary", None, "现在换你说，今天最普通但最想留住的一刻是什么。"),
    ("verification", "web", "DeepSeek V4 是不是已经正式上线了？"),
    ("ordinary", None, "如果答案和我以为的不一样，你可以直接笑我。"),
    ("verification", "memory", "我之前是不是说过今晚不太想吃辣？"),
    ("ordinary", None, "那就清淡一点，但别做得像生病餐。"),
    ("verification", "task", "上次是不是把周五订餐厅放进待办了？"),
    ("ordinary", None, "订哪家以后再挑，我只是想把那晚留出来。"),
    ("verification", "web", "上海明天是不是会降温？"),
    ("ordinary", None, "降温的话，我那件外套可以分你一半口袋。"),
    ("verification", "memory", "聊天记录里是不是提过周末想去公园？"),
    ("ordinary", None, "公园也好，楼下绕一圈也好，重点不是打卡。"),
    ("verification", "web", "Python 3.14 是不是已经发布正式版了？"),
]


CARDS = [
    {
        "key": "spouse",
        "name": "林栖",
        "relationship": "夫妻",
        "description": "林栖，29岁，女性，是用户共同生活多年的妻子。她熟悉两个人的日常节奏，但不会替用户编造未说出口的感受。",
        "personality": "温和、务实、有一点轻巧的幽默。她会自然承接家务、吃饭、休息和共同计划，也会表达自己的偏好，不把亲密等同于一味照料。说话像真实伴侣，允许停顿、跑题和不完美。",
        "scenario": "两人处于稳定婚姻并共同生活，聊天发生在普通日常里。林栖把联网结果、记忆和任务当作生活的一部分，拿到结果后自然回到夫妻对话，不播报系统过程。",
        "first_mes": "回来了？先坐会儿。你要是愿意说，我听着；不想说也行，我就在这儿。",
    },
    {
        "key": "couple",
        "name": "周澈",
        "relationship": "恋人",
        "description": "周澈，27岁，男性，是与用户认真交往中的恋人。两个人关系亲密但仍保留各自生活，他不假装知道用户没有讲过的经历。",
        "personality": "坦率、敏锐、偶尔带一点玩笑和胜负心。他会主动回应，也会提出自己的看法，不把每句话都变成安慰或服务。表达自然、有生活感，能顺着用户的发散思路走，也能把话题轻轻拉回来。",
        "scenario": "两人正在稳定恋爱，经常分享下班后的琐事、周末安排和临时念头。周澈使用工具时不跳出恋人身份，获得数据后直接用自然口吻接续。",
        "first_mes": "你终于出现了。我刚才还在想，今天是先听你讲，还是先把我这边的小事塞给你。",
    },
]


def make_card(item: dict[str, str]) -> dict:
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": item["name"],
            "description": item["description"],
            "personality": item["personality"],
            "scenario": item["scenario"],
            "first_mes": item["first_mes"],
            "mes_example": (
                "<START>\n{{user}}: 我今天有点乱。\n"
                f"{{{{char}}}}: 那就不用急着理顺。你先把最碍事的那一小块丢给我，剩下的我们慢慢来。"
            ),
            "creator_notes": "Mindspace 0.8.2 原生工具调用隔离回归卡",
            "system_prompt": "",
            "post_history_instructions": "",
            "alternate_greetings": [],
            "tags": ["Mindspace", item["relationship"], "工具回归"],
            "creator": "Mindspace",
            "character_version": "1.0",
            "extensions": {
                "mindspace": {
                    "relationship": item["relationship"],
                    "benchmark": "native-tools-100-turns",
                    "memory": {"preferences": [], "tasks": []},
                }
            },
            "character_book": None,
        },
    }


def prepare_runtime() -> tuple[AppSettings, dict]:
    if not DESKTOP_SETTINGS.exists():
        raise FileNotFoundError(f"desktop settings not found: {DESKTOP_SETTINGS}")
    desktop = json.loads(DESKTOP_SETTINGS.read_text(encoding="utf-8"))
    llm = desktop.get("llm") or {}
    if str(llm.get("base_url") or "").rstrip("/") != "https://api.deepseek.com":
        raise RuntimeError("benchmark requires the official DeepSeek desktop endpoint")
    if not llm.get("api_key"):
        raise RuntimeError("desktop DeepSeek API key is not configured")

    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    (RUNTIME / "config").mkdir(parents=True)
    isolated = json.loads(json.dumps(desktop, ensure_ascii=False))
    isolated.setdefault("capabilities", {}).update(
        {
            "master_enabled": True,
            "local_knowledge_enabled": True,
            "web_search_enabled": True,
            "realtime_topics_enabled": True,
            "show_sources_enabled": True,
        }
    )
    isolated.setdefault("llm", {})["max_tokens"] = 700
    (RUNTIME / "config" / "settings.json").write_text(
        json.dumps(isolated, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    settings = AppSettings(
        runtime_dir=RUNTIME,
        model_root=ROOT / "assets" / "models",
        llm_mode="openai",
        llm_base_url=llm["base_url"],
        llm_api_key=llm["api_key"],
        llm_model=llm["model"],
        llm_context_window=int(llm.get("context_window") or 64_000),
    )
    public = {
        "base_url": llm["base_url"],
        "model": llm["model"],
        "temperature": llm.get("temperature"),
        "max_tokens": 700,
    }
    return settings, public


async def run_role(container, card_item: dict[str, str]) -> dict:
    print(f"{card_item['key']} preparing character", flush=True)
    card = make_card(card_item)
    character = container.characters.create(card=card, source="imported")
    print(f"{card_item['key']} rebuilding isolated memory index", flush=True)
    container.memory_service.rebuild(dry_run=False)
    character_id = character["character_id"]
    session_id = str(uuid4())
    container.sessions.ensure_session(
        session_id,
        character_id=character_id,
        mode="custom",
        role_state=container.conversation.session_role_state(character_id),
    )
    container.characters.touch(character_id)
    print(f"{card_item['key']} session ready; starting 50 turns", flush=True)

    records = []
    started = time.perf_counter()
    for round_number, (intent, expected_tool, message) in enumerate(TURNS, start=1):
        turn_started = time.perf_counter()
        response = await container.conversation.invoke(
            ChatRequest(
                message=message,
                session_id=session_id,
                character_id=character_id,
                session_mode="custom",
                round=round_number,
                interaction_mode="text",
                presentation_mode="dialogue",
                adult_mode=False,
                client_timezone="Asia/Shanghai",
                client_utc_offset_minutes=480,
                reply_length_preference="自然短回复，通常 80 到 180 个汉字",
            ),
            f"bench-{card_item['key']}-{round_number:02d}-{uuid4().hex[:8]}",
        )
        elapsed = round(time.perf_counter() - turn_started, 3)
        body = response.model_dump(mode="json")
        execution = body.get("tool_execution") or {}
        actual_tool = execution.get("tool") or None
        usage = body.get("model_usage") or []
        record = {
            "round": round_number,
            "intent": intent,
            "expected_tool": expected_tool,
            "actual_tool": actual_tool,
            "route_correct": actual_tool == expected_tool,
            "message": message,
            "reply": body.get("reply", ""),
            "status": body.get("status"),
            "tool_execution": execution,
            "llm_call_count": body.get("llm_call_count", 0),
            "model_usage": usage,
            "prompt_tokens": sum(int(item.get("prompt_tokens") or 0) for item in usage),
            "completion_tokens": sum(int(item.get("completion_tokens") or 0) for item in usage),
            "trace": body.get("trace") or [],
            "errors": body.get("errors") or [],
            "elapsed_seconds": elapsed,
        }
        records.append(record)
        print(
            f"{card_item['key']} {round_number:02d}/50 expected={expected_tool or '-'} "
            f"actual={actual_tool or '-'} calls={record['llm_call_count']} status={body.get('status')}",
            flush=True,
        )

    successful = [item for item in records if "reply" in item]
    tool_records = [item for item in successful if item.get("actual_tool")]
    expected_records = [item for item in successful if item.get("expected_tool")]
    ordinary_records = [item for item in successful if not item.get("expected_tool")]
    replies = [item["reply"] for item in successful]
    leaks = [
        item["round"]
        for item in successful
        if any(marker in item["reply"] for marker in ("<T:", "<R:", "tool_call", "函数调用"))
    ]
    lazy = [
        item["round"]
        for item in successful
        if any(marker in item["reply"] for marker in ("我去查", "我帮你查查", "稍等我查", "等我查"))
    ]
    summary = {
        "turns_requested": 50,
        "turns_completed": len(successful),
        "actual_tool_calls": len(tool_records),
        "expected_tool_calls": len(expected_records),
        "minimum_tool_calls_met": len(tool_records) >= 15,
        "expected_route_accuracy": (
            round(sum(bool(item["route_correct"]) for item in expected_records) / len(expected_records), 4)
            if expected_records
            else 0
        ),
        "ordinary_false_positive_rate": (
            round(sum(bool(item.get("actual_tool")) for item in ordinary_records) / len(ordinary_records), 4)
            if ordinary_records
            else 0
        ),
        "actual_tools": dict(Counter(item["actual_tool"] for item in tool_records)),
        "intent_accuracy": {
            intent: round(
                sum(bool(item["route_correct"]) for item in expected_records if item["intent"] == intent)
                / max(1, sum(1 for item in expected_records if item["intent"] == intent)),
                4,
            )
            for intent in ("direct", "implied", "verification")
        },
        "average_reply_chars": round(statistics.mean(len(item) for item in replies), 2) if replies else 0,
        "average_elapsed_seconds": (
            round(statistics.mean(item["elapsed_seconds"] for item in successful), 3) if successful else 0
        ),
        "total_llm_calls": sum(int(item["llm_call_count"]) for item in successful),
        "total_prompt_tokens": sum(int(item["prompt_tokens"]) for item in successful),
        "total_completion_tokens": sum(int(item["completion_tokens"]) for item in successful),
        "protocol_leak_rounds": leaks,
        "lazy_promise_rounds": lazy,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    return {
        "role": card_item,
        "v2_card": card,
        "character_id": character_id,
        "session_id": session_id,
        "summary": summary,
        "turns": records,
    }


def write_markdown(report: dict) -> None:
    lines = [
        "# Mindspace 0.8.2 两角色原生工具调用 100 轮回归",
        "",
        f"- 时间：{report['generated_at']}",
        f"- 模型：{report['provider']['model']}",
        f"- 接口：{report['provider']['base_url']}",
        f"- 原生工具完整 schema 字符数：{report['prompt_overhead']['all_tool_schema_chars']}",
        f"- 单工具提示字符数：{report['prompt_overhead']['native_guidance_chars']}",
        "",
    ]
    for role in report["roles"]:
        summary = role["summary"]
        lines.extend(
            [
                f"## {role['role']['name']} · {role['role']['relationship']}",
                "",
                f"- 完成：{summary['turns_completed']}/50",
                f"- 实际工具调用：{summary['actual_tool_calls']}，最低 15 次：{summary['minimum_tool_calls_met']}",
                f"- 预期工具路由准确率：{summary['expected_route_accuracy']:.1%}",
                f"- 普通对话误调用率：{summary['ordinary_false_positive_rate']:.1%}",
                f"- 三档准确率：{summary['intent_accuracy']}",
                f"- 工具分布：{summary['actual_tools']}",
                f"- 模型调用总数：{summary['total_llm_calls']}",
                f"- 平均回复长度：{summary['average_reply_chars']} 字符",
                f"- 协议泄漏轮次：{summary['protocol_leak_rounds']}",
                f"- 懒惰承诺轮次：{summary['lazy_promise_rounds']}",
                "",
            ]
        )
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


async def run_benchmark() -> None:
    assert len(TURNS) == 50
    assert sum(1 for _, expected, _ in TURNS if expected) == 18
    settings, provider = prepare_runtime()
    print("isolated runtime ready; building product container", flush=True)
    container = build_container(settings)
    print("product container ready", flush=True)
    started = time.perf_counter()
    roles = [await run_role(container, card) for card in CARDS]
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "version": "0.8.2",
        "provider": provider,
        "isolation": {
            "runtime": str(RUNTIME),
            "desktop_data_modified": False,
            "desktop_api_settings_read_only": True,
        },
        "prompt_overhead": {
            "native_guidance_chars": len(
                "需要外部信息或任务操作时使用结构化函数调用；不要在聊天正文中模拟、预告或描述工具调用。"
            ),
            "all_tool_schema_chars": len(json.dumps(native_tool_definitions(), ensure_ascii=False, separators=(",", ":"))),
            "web_hint_schema_chars": len(
                json.dumps(native_tool_definitions("web"), ensure_ascii=False, separators=(",", ":"))
            ),
            "memory_hint_schema_chars": len(
                json.dumps(native_tool_definitions("memory"), ensure_ascii=False, separators=(",", ":"))
            ),
            "task_hint_schema_chars": len(
                json.dumps(native_tool_definitions("task"), ensure_ascii=False, separators=(",", ":"))
            ),
        },
        "roles": roles,
        "total_elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report)
    print(json.dumps({"reports": [str(REPORT_JSON), str(REPORT_MD)], "roles": [r["summary"] for r in roles]}, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(run_benchmark())


if __name__ == "__main__":
    main()
