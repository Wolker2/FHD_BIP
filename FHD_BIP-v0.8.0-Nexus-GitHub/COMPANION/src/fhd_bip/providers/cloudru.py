from __future__ import annotations

from .openai_compatible import OpenAICompatibleProvider


class CloudRuProvider(OpenAICompatibleProvider):
    def __init__(self, api_key: str, base_url: str, model: str, *, timeout_sec: float = 30.0, supports_images: bool = False):
        super().__init__(
            "cloudru",
            base_url or "https://foundation-models.api.cloud.ru/v1",
            model or "ai-sage/GigaChat3-10B-A1.8B",
            api_key,
            timeout_sec=timeout_sec,
            supports_images=supports_images,
        )
