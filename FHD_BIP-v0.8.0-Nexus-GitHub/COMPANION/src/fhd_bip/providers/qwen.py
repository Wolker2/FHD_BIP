from __future__ import annotations

from .openai_compatible import OpenAICompatibleProvider


class QwenProvider(OpenAICompatibleProvider):
    """Alibaba Model Studio OpenAI-compatible Qwen profile.

    The Base URL is intentionally user-configurable because current Alibaba
    endpoints differ by region/workspace.
    """

    def __init__(self, api_key: str, base_url: str, model: str, *, timeout_sec: float = 30.0, supports_images: bool = True):
        super().__init__(
            "qwen",
            base_url,
            model or "qwen3.7-plus",
            api_key,
            timeout_sec=timeout_sec,
            supports_images=supports_images,
        )
