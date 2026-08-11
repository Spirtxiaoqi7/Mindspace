"""OpenAI-compatible provider presets exposed to the desktop settings UI."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


LLM_PROVIDERS: tuple[dict[str, Any], ...] = (
    {"id": "deepseek", "label": "DeepSeek", "base_url": "https://api.deepseek.com", "models": ["deepseek-chat", "deepseek-reasoner"]},
    {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1", "models": ["gpt-5", "gpt-4.1", "gpt-4o"]},
    {"id": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "models": []},
    {"id": "siliconflow", "label": "SiliconFlow 硅基流动", "base_url": "https://api.siliconflow.com/v1", "models": []},
    {"id": "gemini", "label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "models": ["gemini-2.5-pro", "gemini-2.5-flash"]},
    {"id": "xai", "label": "xAI", "base_url": "https://api.x.ai/v1", "models": []},
    {"id": "groq", "label": "Groq", "base_url": "https://api.groq.com/openai/v1", "models": []},
    {"id": "mistral", "label": "Mistral AI", "base_url": "https://api.mistral.ai/v1", "models": ["mistral-large-latest", "mistral-small-latest"]},
    {"id": "together", "label": "Together AI", "base_url": "https://api.together.xyz/v1", "models": []},
    {"id": "fireworks", "label": "Fireworks AI", "base_url": "https://api.fireworks.ai/inference/v1", "models": []},
    {"id": "perplexity", "label": "Perplexity", "base_url": "https://api.perplexity.ai", "models": ["sonar", "sonar-pro"]},
    {"id": "cerebras", "label": "Cerebras", "base_url": "https://api.cerebras.ai/v1", "models": []},
    {"id": "nvidia", "label": "NVIDIA NIM", "base_url": "https://integrate.api.nvidia.com/v1", "models": []},
    {"id": "moonshot", "label": "Moonshot / Kimi", "base_url": "https://api.moonshot.cn/v1", "models": []},
    {"id": "zhipu", "label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "models": []},
    {"id": "dashscope", "label": "阿里云百炼 DashScope", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "models": []},
    {"id": "volcengine", "label": "火山方舟 Ark", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "models": []},
    {"id": "qianfan", "label": "百度千帆", "base_url": "https://qianfan.baidubce.com/v2", "models": []},
    {"id": "github", "label": "GitHub Models", "base_url": "https://models.github.ai/inference", "models": []},
    {"id": "lmstudio", "label": "LM Studio 本地", "base_url": "http://127.0.0.1:1234/v1", "models": [], "requires_key": False},
    {"id": "ollama", "label": "Ollama 本地", "base_url": "http://127.0.0.1:11434/v1", "models": [], "requires_key": False},
    {"id": "vllm", "label": "vLLM 本地", "base_url": "http://127.0.0.1:8000/v1", "models": [], "requires_key": False},
    {"id": "llamacpp", "label": "llama.cpp 本地", "base_url": "http://127.0.0.1:8080/v1", "models": [], "requires_key": False},
    {"id": "custom", "label": "自定义 OpenAI 兼容接口", "base_url": "", "models": [], "requires_key": False, "custom": True},
)

_BY_ID = {str(item["id"]): item for item in LLM_PROVIDERS}


def provider_catalog() -> list[dict[str, Any]]:
    return deepcopy(list(LLM_PROVIDERS))


def provider_by_id(provider_id: str) -> dict[str, Any]:
    return deepcopy(_BY_ID.get(provider_id, _BY_ID["custom"]))


def infer_provider_id(base_url: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/").lower()
    for item in LLM_PROVIDERS:
        preset = str(item.get("base_url") or "").rstrip("/").lower()
        if preset and normalized == preset:
            return str(item["id"])
    return "custom"


def normalize_base_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    suffix = "/chat/completions"
    if value.lower().endswith(suffix):
        value = value[: -len(suffix)].rstrip("/")
    return value
