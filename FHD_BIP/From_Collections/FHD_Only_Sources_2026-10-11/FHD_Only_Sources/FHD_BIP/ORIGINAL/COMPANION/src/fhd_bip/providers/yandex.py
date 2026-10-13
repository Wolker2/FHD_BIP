from __future__ import annotations

from .openai_compatible import OpenAICompatibleProvider


class YandexProvider(OpenAICompatibleProvider):
    def __init__(self, api_key: str, base_url: str, model: str, *, timeout_sec: float = 30.0, supports_images: bool = False):
        super().__init__(
            "yandex",
            base_url or "https://ai.api.cloud.yandex.net/v1",
            model,
            api_key,
            timeout_sec=timeout_sec,
            supports_images=supports_images,
        )
