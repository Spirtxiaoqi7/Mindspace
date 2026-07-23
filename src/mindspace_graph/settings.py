"""Environment-driven product settings with safe local defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


@dataclass(slots=True)
class AppSettings:
    app_name: str = "Mindspace Graph"
    host: str = "127.0.0.1"
    port: int = 8765
    debug: bool = False
    runtime_dir: Path = field(default_factory=lambda: Path.cwd() / "runtime")
    model_root: Path = field(default_factory=lambda: Path.cwd() / "assets" / "models")
    llm_mode: str = "openai"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_context_window: int = 64_000
    context_compaction_enabled: bool = True
    context_compaction_model: str = ""
    context_compaction_max_tokens: int = 1200
    context_compaction_soft_ratio: float = 0.65
    context_compaction_hard_ratio: float = 0.82
    context_compaction_patch_limit: int = 32
    context_compaction_retain_turns: int = 8
    context_compaction_delay_seconds: float = 1.5
    role_audit_enabled: bool = True
    role_audit_model: str = ""
    tts_provider: str = "siliconflow"
    tts_worker_url: str = "http://127.0.0.1:5055"
    tts_reference_audio: str = ""
    tts_reference_text: str = ""
    tts_siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    tts_siliconflow_api_key: str = ""
    tts_siliconflow_model: str = "fnlp/MOSS-TTSD-v0.5"
    tts_siliconflow_voice: str = "fnlp/MOSS-TTSD-v0.5:alex"
    tts_siliconflow_gain: float = 0.0
    tts_siliconflow_sample_rate: int = 24000
    tts_gpt_sovits_worker_url: str = "http://127.0.0.1:5055"
    tts_gpt_sovits_voice: str = "v4-changli"
    asr_provider: str = "funasr"
    asr_base_url: str = "ws://127.0.0.1:8766/ws"
    asr_api_key: str = ""
    asr_model: str = "paraformer-zh-streaming"
    asr_device: str = "cuda:0"
    emotion_enabled: bool = False
    emotion_deadline_ms: int = 500
    auto_tts: bool = False

    @classmethod
    def from_env(cls) -> AppSettings:
        _load_env_file(Path.cwd() / ".env")
        default_runtime = Path(__file__).resolve().parents[2] / "runtime"
        return cls(
            app_name=os.environ.get("MINDSPACE_APP_NAME", "Mindspace Graph"),
            host=os.environ.get("MINDSPACE_HOST", "127.0.0.1"),
            port=int(os.environ.get("MINDSPACE_PORT", "8765")),
            debug=_bool("MINDSPACE_DEBUG", False),
            runtime_dir=Path(
                os.environ.get("MINDSPACE_RUNTIME_DIR", str(default_runtime))
            ).resolve(),
            model_root=Path(
                os.environ.get(
                    "MINDSPACE_MODEL_ROOT",
                    str(Path(__file__).resolve().parents[2] / "assets" / "models"),
                )
            ).resolve(),
            llm_mode=os.environ.get("MINDSPACE_LLM_MODE", "openai").strip().lower(),
            llm_base_url=os.environ.get("MINDSPACE_LLM_BASE_URL", "https://api.deepseek.com"),
            llm_api_key=os.environ.get("MINDSPACE_LLM_API_KEY", ""),
            llm_model=os.environ.get("MINDSPACE_LLM_MODEL", "deepseek-chat"),
            llm_context_window=int(os.environ.get("MINDSPACE_LLM_CONTEXT_WINDOW", "64000")),
            context_compaction_enabled=_bool("MINDSPACE_CONTEXT_COMPACTION_ENABLED", True),
            context_compaction_model=os.environ.get(
                "MINDSPACE_CONTEXT_COMPACTION_MODEL", ""
            ).strip(),
            context_compaction_max_tokens=int(
                os.environ.get("MINDSPACE_CONTEXT_COMPACTION_MAX_TOKENS", "1200")
            ),
            context_compaction_soft_ratio=float(
                os.environ.get("MINDSPACE_CONTEXT_COMPACTION_SOFT_RATIO", "0.65")
            ),
            context_compaction_hard_ratio=float(
                os.environ.get("MINDSPACE_CONTEXT_COMPACTION_HARD_RATIO", "0.82")
            ),
            context_compaction_patch_limit=int(
                os.environ.get("MINDSPACE_CONTEXT_COMPACTION_PATCH_LIMIT", "32")
            ),
            context_compaction_retain_turns=int(
                os.environ.get("MINDSPACE_CONTEXT_COMPACTION_RETAIN_TURNS", "8")
            ),
            context_compaction_delay_seconds=float(
                os.environ.get("MINDSPACE_CONTEXT_COMPACTION_DELAY_SECONDS", "1.5")
            ),
            role_audit_enabled=_bool("MINDSPACE_ROLE_AUDIT_ENABLED", True),
            role_audit_model=os.environ.get("MINDSPACE_ROLE_AUDIT_MODEL", "").strip(),
            tts_provider=os.environ.get("MINDSPACE_TTS_PROVIDER", "siliconflow").strip().lower(),
            tts_worker_url=os.environ.get("MINDSPACE_TTS_WORKER_URL", "http://127.0.0.1:5055"),
            tts_reference_audio=os.environ.get("MINDSPACE_TTS_REFERENCE_AUDIO", ""),
            tts_reference_text=os.environ.get("MINDSPACE_TTS_REFERENCE_TEXT", ""),
            tts_siliconflow_base_url=os.environ.get(
                "MINDSPACE_TTS_SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
            ),
            tts_siliconflow_api_key=os.environ.get("MINDSPACE_TTS_SILICONFLOW_API_KEY", ""),
            tts_siliconflow_model=os.environ.get(
                "MINDSPACE_TTS_SILICONFLOW_MODEL", "fnlp/MOSS-TTSD-v0.5"
            ),
            tts_siliconflow_voice=os.environ.get(
                "MINDSPACE_TTS_SILICONFLOW_VOICE", "fnlp/MOSS-TTSD-v0.5:alex"
            ),
            tts_siliconflow_gain=float(os.environ.get("MINDSPACE_TTS_SILICONFLOW_GAIN", "0")),
            tts_siliconflow_sample_rate=int(
                os.environ.get("MINDSPACE_TTS_SILICONFLOW_SAMPLE_RATE", "24000")
            ),
            tts_gpt_sovits_worker_url=os.environ.get(
                "MINDSPACE_TTS_GPT_SOVITS_WORKER_URL", "http://127.0.0.1:5055"
            ),
            tts_gpt_sovits_voice=os.environ.get(
                "MINDSPACE_TTS_GPT_SOVITS_VOICE", "v4-changli"
            ).strip(),
            asr_provider=os.environ.get("MINDSPACE_ASR_PROVIDER", "funasr").strip().lower(),
            asr_base_url=os.environ.get("MINDSPACE_ASR_BASE_URL", "ws://127.0.0.1:8766/ws"),
            asr_api_key=os.environ.get("MINDSPACE_ASR_API_KEY", ""),
            asr_model=os.environ.get("MINDSPACE_ASR_MODEL", "paraformer-zh-streaming"),
            asr_device=os.environ.get("MINDSPACE_ASR_DEVICE", "cuda:0"),
            # The emotion sidechain is intentionally dormant.  Keep the setting
            # in the schema so a future implementation can reuse the interface.
            emotion_enabled=False,
            emotion_deadline_ms=int(os.environ.get("MINDSPACE_EMOTION_DEADLINE_MS", "500")),
            auto_tts=_bool("MINDSPACE_AUTO_TTS", False),
        )

    def ensure_directories(self) -> None:
        for relative in (
            "config",
            "data/profiles",
            "data/sessions",
            "data/audio",
            "data/avatars",
            "logs",
        ):
            (self.runtime_dir / relative).mkdir(parents=True, exist_ok=True)

    def public_config(self) -> dict[str, object]:
        return {
            "app_name": self.app_name,
            "llm_mode": self.llm_mode,
            "model": self.llm_model,
            "llm_context_window": self.llm_context_window,
            "context_compaction_enabled": self.context_compaction_enabled,
            "tts_provider": self.tts_provider,
            "asr_provider": self.asr_provider,
            "emotion_enabled": self.emotion_enabled,
            "auto_tts": self.auto_tts,
            "model_root": str(self.model_root),
            "shortcuts": {
                "send": "Enter",
                "newline": "Shift+Enter",
                "interrupt": "Escape",
                "focus_composer": "Ctrl+K",
                "voice_input": "Ctrl+Shift+M",
                "new_session": "Ctrl+N",
            },
        }
