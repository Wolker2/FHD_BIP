from __future__ import annotations

from ..provider_base import LiveProvider


class OpenAIRealtimeProvider(LiveProvider):
    """Reserved provider slot for a future release.

    FHD_BIP v0.6.0 ships Gemini Live as the only production-enabled provider.
    The provider boundary is retained so other realtime services can be added
    without changing screen capture, memory, UI or the game bridge.
    """

    async def connect(self) -> None:
        raise RuntimeError("OpenAI Realtime provider is not enabled in FHD_BIP v0.6.0")

    async def close(self) -> None: pass
    async def send_audio(self, pcm16: bytes) -> None: pass
    async def end_audio_stream(self) -> None: pass
    async def send_frame(self, jpeg: bytes) -> None: pass
    async def send_text(self, text: str) -> None: pass
