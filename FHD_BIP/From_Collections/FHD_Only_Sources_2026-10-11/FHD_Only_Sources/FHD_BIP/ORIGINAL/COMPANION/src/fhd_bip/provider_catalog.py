from __future__ import annotations

from copy import deepcopy

PROVIDER_LABELS: dict[str, str] = {
    "auto": "Автоматически",
    "gemini": "Gemini Live",
    "openai": "ChatGPT / OpenAI API",
    "gigachat": "GigaChat",
    "deepseek": "DeepSeek",
    "qwen": "Qwen / DashScope",
    "yandex": "YandexGPT / Yandex AI Studio",
    "cloudru": "Cloud.ru Foundation Models",
    "lmstudio": "Локальная модель / LM Studio",
    "openai_compatible": "Пользовательский OpenAI-compatible API",
}

VALID_PROVIDERS = set(PROVIDER_LABELS)
FALLBACK_PROVIDERS = [p for p in PROVIDER_LABELS if p != "auto"]

DEFAULT_PROVIDER_MODELS: dict[str, str] = {
    # Gemini is the full Live model when it is selected as primary. When Gemini
    # is used by the text fallback manager it automatically switches to the
    # stable text model below.
    "gemini": "gemini-3.1-flash-live-preview",
    "openai": "gpt-5.6-luna",
    "gigachat": "GigaChat-3-Ultra",
    "deepseek": "deepseek-flash",
    "qwen": "qwen3.7-plus",
    "yandex": "gpt://<folder_ID>/yandexgpt/latest",
    "cloudru": "ai-sage/GigaChat3-10B-A1.8B",
    "lmstudio": "",
    "openai_compatible": "",
}

GEMINI_TEXT_FALLBACK_MODEL = "gemini-3.8-flash"

DEFAULT_PROVIDER_BASE_URLS: dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openai": "https://api.openai.com/v1",
    "gigachat": "https://api.giga.chat/v1",
    "deepseek": "https://api.deepseek.com",
    # Alibaba now recommends workspace-specific URLs for most regions. Leave
    # this blank rather than silently sending users to the wrong workspace.
    "qwen": "",
    "yandex": "https://ai.api.cloud.yandex.net/v1",
    "cloudru": "https://foundation-models.api.cloud.ru/v1",
    "lmstudio": "http://127.0.0.1:1234/v1",
    "openai_compatible": "",
}

DEFAULT_PROVIDER_SCOPES: dict[str, str] = {
    "gigachat": "GIGACHAT_API_PERS",
}

DEFAULT_PROVIDER_SEND_IMAGES: dict[str, bool] = {
    "gemini": True,
    "openai": True,
    "gigachat": False,
    "deepseek": True,
    "qwen": True,
    "yandex": False,
    "cloudru": False,
    "lmstudio": False,
    "openai_compatible": False,
}

DEFAULT_PROVIDER_USE_TOOLS: dict[str, bool] = {
    "gemini": True,
    "openai": True,
    "gigachat": False,
    "deepseek": True,
    "qwen": True,
    "yandex": True,
    "cloudru": True,
    "lmstudio": False,
    "openai_compatible": False,
}

KEY_REQUIRED: dict[str, bool] = {
    "gemini": True,
    "openai": True,
    "gigachat": True,
    "deepseek": True,
    "qwen": True,
    "yandex": True,
    "cloudru": True,
    "lmstudio": False,
    # Custom OpenAI-compatible endpoints may be a local server without auth.
    "openai_compatible": False,
}

DEFAULT_FALLBACK_ORDER = [
    "gemini",
    "openai",
    "gigachat",
    "deepseek",
    "qwen",
    "yandex",
    "cloudru",
    "lmstudio",
    "openai_compatible",
]


def provider_label(provider_id: str) -> str:
    return PROVIDER_LABELS.get(provider_id, provider_id)


def provider_id_from_label(label: str) -> str:
    for provider_id, text in PROVIDER_LABELS.items():
        if text == label:
            return provider_id
    return "gemini"


def copy_models() -> dict[str, str]:
    return deepcopy(DEFAULT_PROVIDER_MODELS)


def copy_base_urls() -> dict[str, str]:
    return deepcopy(DEFAULT_PROVIDER_BASE_URLS)


def copy_scopes() -> dict[str, str]:
    return deepcopy(DEFAULT_PROVIDER_SCOPES)


def copy_send_images() -> dict[str, bool]:
    return deepcopy(DEFAULT_PROVIDER_SEND_IMAGES)


def copy_use_tools() -> dict[str, bool]:
    return deepcopy(DEFAULT_PROVIDER_USE_TOOLS)
