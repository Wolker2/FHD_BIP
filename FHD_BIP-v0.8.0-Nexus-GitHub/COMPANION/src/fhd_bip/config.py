from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = "FHD_BIP"
LEGACY_APP_NAME = "FHD_AICompanion"
CURRENT_VERSION = "0.8.0"
DEFAULT_MODEL = "gemini-3.1-flash-live-preview"
VALID_PROVIDERS = {
    "auto", "gemini", "gigachat", "yandex", "deepseek", "qwen",
    "cloudru", "openai", "lmstudio", "openai_compatible",
}
DEFAULT_PROVIDER_PROFILES: dict[str, dict[str, Any]] = {
    "gemini": {"model": DEFAULT_MODEL, "endpoint": "", "vision": True},
    "openai": {
        "model": "gpt-4o-mini",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "vision": True,
    },
    "gigachat": {
        "model": "GigaChat-2-Max",
        "endpoint": "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
        "vision": False,
    },
    "yandex": {"model": "", "endpoint": "", "vision": False},
    "deepseek": {
        "model": "deepseek-flash",
        "endpoint": "https://api.deepseek.com/chat/completions",
        "vision": False,
    },
    "qwen": {
        "model": "qwen-plus",
        "endpoint": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        "vision": False,
    },
    "cloudru": {"model": "", "endpoint": "", "vision": False},
    "lmstudio": {
        "model": "local-model",
        "endpoint": "http://localhost:1234/v1/chat/completions",
        "vision": False,
    },
    "openai_compatible": {"model": "", "endpoint": "", "vision": False},
}
VALID_VOICES = {"Kore", "Puck", "Charon", "Fenrir", "Aoede"}
VALID_GPT_VOICES = {
    "alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx",
    "sage", "shimmer", "verse", "marin", "cedar",
}
VALID_PERSONALITIES = {"balanced", "sarcastic", "cheerful", "military", "gaal_scientist", "peleng_buddy"}
VALID_INITIATIVES = {"low", "medium", "high", "chatty"}
VALID_MICROPHONE_SENSITIVITIES = {"low", "medium", "high"}


def _local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    return Path.home() / ".local" / "share"


def app_dir() -> Path:
    path = _local_appdata() / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def legacy_app_dir() -> Path:
    return _local_appdata() / LEGACY_APP_NAME


def migrate_legacy_data() -> list[str]:
    """Copy user-owned v0.1 data once. Never overwrites existing FHD_BIP data."""
    src = legacy_app_dir()
    dst = app_dir()
    moved: list[str] = []
    if not src.exists():
        return moved
    for name in ("config.json", "memory.sqlite3"):
        old = src / name
        new = dst / name
        if old.exists() and not new.exists():
            try:
                shutil.copy2(old, new)
                moved.append(name)
            except OSError:
                pass
    return moved


@dataclass
class Settings:
    provider: str = "gemini"
    model: str = DEFAULT_MODEL
    provider_profiles: dict[str, dict[str, Any]] = field(default_factory=lambda: _default_profiles())
    fallback_providers: list[str] = field(default_factory=lambda: ["none", "none", "lmstudio"])
    auto_fallback: bool = True
    request_timeout_sec: float = 30.0
    speak_text_providers: bool = True
    tts_voice: str = ""
    gpt_voice: str = "coral"
    voice: str = "Kore"
    assistant_name: str = "БИП"
    player_address: str = "рейнджер"
    personality: str = "balanced"
    initiative: str = "medium"
    capture_screen: bool = True
    capture_microphone: bool = True
    microphone_sensitivity: str = "low"
    microphone_sensitivity_trim_db: float = 0.0
    allow_voice_interruption: bool = False
    only_when_game_active: bool = True
    output_volume_percent: int = 100
    receive_game_events: bool = True
    window_title_contains: str = "Space Rangers"
    frame_interval_sec: float = 1.0
    jpeg_quality: int = 85
    max_frame_width: int = 1920
    max_frame_height: int = 1080
    remember_transcripts: bool = True

    def __post_init__(self) -> None:
        if self.provider_profiles is None:
            self.provider_profiles = _default_profiles()
        if self.fallback_providers is None:
            self.fallback_providers = ["none", "none", "lmstudio"]

    def profile(self, provider: str) -> dict[str, Any]:
        profile = self.provider_profiles.get(provider, {})
        return dict(profile) if isinstance(profile, dict) else {}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        if not isinstance(data, dict):
            return cls()
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        try:
            result = cls(**valid)
        except TypeError:
            return cls()
        if result.provider not in VALID_PROVIDERS:
            result.provider = "gemini"
        if not isinstance(result.model, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", result.model):
            result.model = DEFAULT_MODEL
        result.provider_profiles = _clean_profiles(result.provider_profiles)
        # Keep old configs compatible: their top-level Gemini model remains authoritative.
        if result.model:
            result.provider_profiles["gemini"]["model"] = result.model
        if not isinstance(result.fallback_providers, list):
            result.fallback_providers = ["none", "none", "lmstudio"]
        cleaned_fallbacks: list[str] = []
        for item in result.fallback_providers[:3]:
            value = item if isinstance(item, str) and item in VALID_PROVIDERS - {"auto"} else "none"
            cleaned_fallbacks.append(value)
        result.fallback_providers = (cleaned_fallbacks + ["none", "none", "none"])[:3]
        result.auto_fallback = _bool(result.auto_fallback, True)
        result.request_timeout_sec = _number(result.request_timeout_sec, 30.0, 5.0, 120.0)
        result.speak_text_providers = _bool(result.speak_text_providers, True)
        result.tts_voice = _clean_text(result.tts_voice, "", 200)
        if result.gpt_voice not in VALID_GPT_VOICES:
            result.gpt_voice = "coral"
        if result.voice not in VALID_VOICES:
            result.voice = "Kore"
        if result.personality not in VALID_PERSONALITIES:
            result.personality = "balanced"
        if result.initiative not in VALID_INITIATIVES:
            result.initiative = "medium"
        if result.microphone_sensitivity not in VALID_MICROPHONE_SENSITIVITIES:
            result.microphone_sensitivity = "low"
        result.microphone_sensitivity_trim_db = _number(
            result.microphone_sensitivity_trim_db, 0.0, -6.0, 10.0
        )
        result.assistant_name = _clean_text(result.assistant_name, "БИП", 40)
        result.player_address = _clean_text(result.player_address, "рейнджер", 40)
        result.window_title_contains = _clean_text(result.window_title_contains, "Space Rangers", 200)
        result.capture_screen = _bool(result.capture_screen, True)
        result.capture_microphone = _bool(result.capture_microphone, True)
        result.only_when_game_active = _bool(result.only_when_game_active, True)
        result.allow_voice_interruption = _bool(result.allow_voice_interruption, False)
        result.output_volume_percent = int(
            _number(result.output_volume_percent, 100, 0, 100)
        )
        result.receive_game_events = _bool(result.receive_game_events, True)
        result.remember_transcripts = _bool(result.remember_transcripts, True)
        result.frame_interval_sec = _number(result.frame_interval_sec, 1.0, 1.0, 10.0)
        result.jpeg_quality = int(_number(result.jpeg_quality, 85, 50, 95))
        result.max_frame_width = int(_number(result.max_frame_width, 1920, 640, 3840))
        result.max_frame_height = int(_number(result.max_frame_height, 1080, 360, 2160))
        return result


def _clean_text(value: Any, default: str, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else default


def _default_profiles() -> dict[str, dict[str, Any]]:
    return {key: dict(value) for key, value in DEFAULT_PROVIDER_PROFILES.items()}


def _clean_profiles(value: Any) -> dict[str, dict[str, Any]]:
    profiles = _default_profiles()
    if not isinstance(value, dict):
        return profiles
    for provider in VALID_PROVIDERS - {"auto"}:
        raw = value.get(provider)
        if not isinstance(raw, dict):
            continue
        model = raw.get("model")
        endpoint = raw.get("endpoint")
        vision = raw.get("vision")
        if isinstance(model, str):
            profiles[provider]["model"] = model.strip()[:160]
        if isinstance(endpoint, str):
            profiles[provider]["endpoint"] = endpoint.strip()[:500]
        if isinstance(vision, bool):
            profiles[provider]["vision"] = vision
    return profiles


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _number(value: Any, default: float, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


class ConfigStore:
    def __init__(self, path: Path | None = None):
        self.path = path or app_dir() / "config.json"

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return Settings.from_dict(data)
        except Exception:
            return Settings()

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
