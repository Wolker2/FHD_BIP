from __future__ import annotations

from .openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    def __init__(self, api_key: str, base_url: str, model: str, *, timeout_sec: float = 30.0, supports_images: bool = True):
        super().__init__(
            "deepseek",
            base_url or "https://api.deepseek.com",
            model or "deepseek-flash",
            api_key,
            timeout_sec=timeout_sec,
            supports_images=supports_images,
        )


    async def probe(self) -> dict[str, str]:
        """Validate auth + model with a tiny request. Never returns the key."""
        import asyncio

        def run() -> dict[str, str]:
            listed = self._list_models_sync()
            model = self.model or listed
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly OK"}],
                "stream": False,
                "thinking": {"type": "disabled"},
            }
            text = self._extract_text(self._post_chat_sync(payload))
            return {"model_from_api": listed, "requested_model": model, "reply": text[:80]}

        return await asyncio.to_thread(run)
