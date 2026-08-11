from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from .tool_chain import ToolInstruction, validate_task_command
from .web.routing import infer_platforms

NATIVE_TOOL_GUIDANCE = (
    "需要外部信息或任务操作时使用结构化函数调用；"
    "不要在聊天正文中模拟、预告或描述工具调用。"
)
_EXPLICIT_CLOCK = re.compile(
    r"(?:(?:[01]?\d|2[0-3]):[0-5]\d|"
    r"(?:凌晨|早上|上午|中午|下午|傍晚|晚上)?\s*[零一二两三四五六七八九十\d]{1,3}\s*"
    r"(?:点|时)(?:半|[零一二三四五六七八九十\d]{1,3}\s*分)?)"
)


def native_tool_definitions(tool_hint: str = "") -> list[dict[str, Any]]:
    definitions = {
        "web": _function(
            "web",
            "Read public current or external information. Use search, open_page, or find_in_page.",
            {
                "query": {"type": "string", "description": "Verbatim search intent; required."},
                "scope": {"type": "string", "enum": ["auto", "official", "news", "social", "developer", "realtime"]},
                "platforms": {"type": "array", "items": {"type": "string"}},
                "recency": {"type": "string", "enum": ["live", "today", "week", "month", "any"]},
                "action": {"type": "string", "enum": ["search", "open_page", "find_in_page"]},
                "url": {"type": "string"},
                "find": {"type": "string"},
            },
            ["query"],
        ),
        "memory": _function(
            "memory",
            "查询当前角色的历史聊天、偏好、任务、结构化记忆和本地知识库。",
            {"query": {"type": "string", "description": "自然语言查询词"}},
            ["query"],
        ),
        "task_list": _function(
            "task_list",
            "列出或查询当前角色的任务。",
            {"query": {"type": "string", "description": "可为空的筛选词"}},
            ["query"],
        ),
        "task_create": _function(
            "task_create",
            "创建当前角色的任务。",
            {
                "title": {"type": "string", "description": "任务标题"},
                "due_at": {
                    "type": ["string", "null"],
                    "description": "ISO 8601 截止时间；用户未提供时为 null",
                },
            },
            ["title", "due_at"],
        ),
        "task_update": _function(
            "task_update",
            "修改当前角色的既有任务。",
            {
                "id": {"type": "string", "description": "任务 ID"},
                "title": {"type": ["string", "null"], "description": "新标题或 null"},
                "due_at": {
                    "type": ["string", "null"],
                    "description": "新截止时间或 null",
                },
            },
            ["id", "title", "due_at"],
        ),
        "task_complete": _function(
            "task_complete",
            "完成当前角色的一项任务。",
            {"id": {"type": "string", "description": "任务 ID"}},
            ["id"],
        ),
    }
    if tool_hint == "web":
        names = ("web",)
    elif tool_hint == "memory":
        names = ("memory",)
    elif tool_hint == "task":
        names = ("task_list", "task_create", "task_update", "task_complete")
    else:
        names = tuple(definitions)
    return [definitions[name] for name in names]


def native_tool_choice(tool_hint: str = "") -> str | dict[str, Any]:
    # Thinking-capable OpenAI-compatible models may support tools while
    # rejecting named/required tool_choice variants. A forced web turn already
    # exposes only the web tool and has a deterministic host-side fallback.
    return "auto"


def native_call_to_instruction(call: dict[str, Any], *, user_message: str) -> ToolInstruction:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = str(function.get("name") or "").strip()
    raw_arguments = function.get("arguments")
    if isinstance(raw_arguments, str):
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid native tool arguments: {exc.msg}") from exc
    elif isinstance(raw_arguments, dict):
        arguments = raw_arguments
    else:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ValueError("native tool arguments must be an object")

    call_id = str(call.get("id") or "").strip() or uuid4().hex
    if name in {"web", "memory"}:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError(f"{name} query is required")
        if len(query) > 2000:
            raise ValueError(f"{name} query is too long")
        command = {key: arguments[key] for key in ("scope", "platforms", "recency", "action", "url", "find") if key in arguments} if name == "web" else None
        if name == "web":
            model_platforms = command.get("platforms") if isinstance(command.get("platforms"), list) else []
            platforms = list(dict.fromkeys([*infer_platforms(user_message), *(str(item).strip().lower() for item in model_platforms if str(item).strip())]))
            if platforms:
                command["platforms"] = platforms
            command["original_intent"] = user_message
        return ToolInstruction(call_id=call_id, tool=name, level=3, parameter=query, command=command)
    task_ops = {
        "task_list": "list",
        "task_create": "create",
        "task_update": "update",
        "task_complete": "complete",
    }
    if name not in task_ops:
        raise ValueError(f"unknown native tool: {name or '<empty>'}")
    command = {"op": task_ops[name]}
    for key in ("query", "title", "due_at", "id"):
        if key in arguments:
            command[key] = arguments[key]
    if command.get("due_at") not in (None, "") and not _EXPLICIT_CLOCK.search(user_message):
        command["due_at"] = None
    error = validate_task_command(command)
    if error:
        raise ValueError(error)
    compact = json.dumps(command, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return ToolInstruction(call_id=call_id, tool="task", level=2, parameter=compact, command=command)


def supports_native_tools(base_url: str, model: str = "") -> bool:
    """Probe any configured OpenAI-compatible endpoint; never vendor-allowlist."""
    from mindspace_graph.provider_capabilities import PROVIDER_CAPABILITIES, ProviderCapabilityState

    return bool((base_url or "").strip()) and PROVIDER_CAPABILITIES.get(base_url, model).state != ProviderCapabilityState.UNSUPPORTED


def _function(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }
