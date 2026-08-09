"""Conditional, evidence-bound extraction of conversational profile changes."""

from __future__ import annotations

import json
import re
from typing import Any

from mindspace_graph.memory_registry import DEFAULT_MEMORY_REGISTRY
from mindspace_graph.models import ChatRequest, JsonUpdatePlan, ProfileBundle

_MEMORY_WORTHY = re.compile(
    r"(?:"
    r"记住|记一下|写进(?:档案|记忆)|修改(?:档案|资料|设定)|更新(?:档案|资料|设定)|"
    r"改一下(?:档案|资料|设定)|删掉(?:档案|记忆)|忘掉|"
    r"我(?:叫|是|从事|喜欢|不喜欢|讨厌|偏好|习惯|希望|想要|打算|决定|正在|最近在)|"
    r"以后(?:叫我|不要|别|请)|"
    r"你(?:是|喜欢|不喜欢|讨厌|希望|想要|打算|决定|会不会|是不是|这是|有点)|"
    r"我们(?:是|刚|已经|决定|约定)|"
    r"今天(?:发生|决定|开始)|刚刚(?:发生|决定|说好)"
    r")",
    re.IGNORECASE,
)


def should_extract_memory(message: str) -> bool:
    """Gate the private extractor so ordinary turns add no model latency."""

    text = re.sub(r"\s+", "", message or "")
    if not text or text in {"嗯", "哦", "好", "好的", "行", "可以", "知道了"}:
        return False
    return bool(_MEMORY_WORTHY.search(text))


def build_memory_extraction_messages(
    request: ChatRequest,
    profiles: ProfileBundle,
    response: str,
) -> list[dict[str, str]]:
    fields = [
        {
            "target": field.target,
            "path": field.path,
            "scope": field.scope,
            "lifecycle": field.lifecycle,
            "value_kind": field.value_kind,
        }
        for field in DEFAULT_MEMORY_REGISTRY.fields
    ]
    payload = {
        "turn_id": f"round_{request.round}",
        "base_revisions": profiles.revisions,
        "current_user": request.message,
        "current_response": response,
        "profiles": {
            "character_memory": profiles.character_memory,
        },
        "writable_fields": fields,
    }
    rules = """你是状态差量提取器，只返回一个 JSON 对象，不要解释。
最多给出 3 个叶子 Patch；没有明确且值得保留的变化时 trigger=none、patches=[]。
用户直接陈述的偏好、当前任务或明确要求，使用 trigger=current_user，
evidence_ids 只能是 [\"current_user\"]，value 必须是当前用户原文中直接出现的最短片段。
AI 在 current_response 中明确承认的自身偏好或待办，可使用
trigger=current_agent；它只能修改 scope=agent 的字段，evidence_ids 只能是
[\"current_response\"]，value 必须逐字出现在 current_response。
自动提取只能写入 character_memory 的 preferences 或 tasks；不得改写角色身份、人格、关系、
边界、系统提示或任何运行状态。一次性的角色扮演命令不进长期记忆。
疑问、猜测、玩笑、单字确认、模型推断、时间本身、历史与工具结果都不是写入证据。
同一计划只使用一种 trigger。不得修改未列出的路径，不得修改技术字段；
current_agent 不得 remove。列表新增用 path 末尾 /-，标量使用 replace。"""
    return [
        {"role": "system", "content": rules},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def parse_memory_plan(raw: str) -> JsonUpdatePlan:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        value: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        decoder = json.JSONDecoder()
        value = None
        for match in re.finditer(r"\{", text):
            try:
                value, _ = decoder.raw_decode(text[match.start() :])
                break
            except json.JSONDecodeError:
                continue
        if value is None:
            raise ValueError("memory extractor did not return a JSON object") from exc
    if isinstance(value, dict) and isinstance(value.get("json_update"), dict):
        value = value["json_update"]
    if not isinstance(value, dict):
        raise ValueError("memory extractor output must be a JSON object")
    trigger = str(value.get("trigger") or "none")
    normalized_patches: list[Any] = []
    for raw_patch in value.get("patches", []) if isinstance(value.get("patches"), list) else []:
        if not isinstance(raw_patch, dict):
            normalized_patches.append(raw_patch)
            continue
        patch = dict(raw_patch)
        path = str(patch.get("path") or "")
        if not patch.get("target") and path:
            matches = {
                field.target
                for field in DEFAULT_MEMORY_REGISTRY.fields
                if path == field.path or path == f"{field.path}/-"
            }
            if len(matches) == 1:
                patch["target"] = matches.pop()
        if not patch.get("evidence_ids"):
            if trigger == "current_user":
                patch["evidence_ids"] = ["current_user"]
            elif trigger == "current_agent":
                patch["evidence_ids"] = ["current_response"]
        normalized_patches.append(patch)
    value["patches"] = normalized_patches
    return JsonUpdatePlan.model_validate(value)
