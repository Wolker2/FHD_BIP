from __future__ import annotations

from .openai_compatible import OpenAICompatibleProvider


class LMStudioProvider(OpenAICompatibleProvider):
    def __init__(self, api_key: str | None, base_url: str, model: str, *, timeout_sec: float = 30.0, supports_images: bool = False):
        super().__init__(
            "lmstudio",
            base_url or "http://127.0.0.1:1234/v1",
            model,
            api_key,
            timeout_sec=timeout_sec,
            supports_images=supports_images,
        )
