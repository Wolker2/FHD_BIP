from __future__ import annotations

import asyncio

from ..provider_base import LiveProvider, ProviderCallbacks
from ..voice import WindowsTTS
from .provider_manager import ProviderManager


SILENCE_TOKENS = {
    "",
    "silence",
    "[silence]",
    "тишина",
    "[тишина]",
    "<silence>",
}


class ManagedTextProviderAdapter(LiveProvider):
    """Adapts ordinary chat providers to BIP's existing runtime boundary.

    Frames are kept locally and attached only when BIP actually asks the model a
    question/proactive check; GAME_CONTEXT/GAME_EVENT are stored as authoritative
    context without forcing an API reply for every game event.
    """

    def __init__(
        self,
        manager: ProviderManager,
        callbacks: ProviderCallbacks,
        *,
        tts_enabled: bool = True,
    ):
        self.manager = manager
        self.cb = callbacks
        self.latest_frame: bytes | None = None
        self.tts = WindowsTTS(tts_enabled)
        self._lock = asyncio.Lock()
        self._closed = False

    async def connect(self) -> None:
        configured = self.manager.configured_chain()
        if not configured:
            raise RuntimeError("Нет ни одного полностью настроенного AI-провайдера")
        names = ", ".join(configured)
        self.cb.on_status(f"AI Provider Manager готов: {names}")

    async def close(self) -> None:
        self._closed = True

    async def send_audio(self, pcm16: bytes) -> None:
        # Voice input is intentionally a separate layer. v0.6.4 keeps Gemini
        # Live as the production speech-to-speech path; text providers use typed
        # input until a selected STT backend is configured in the next stage.
        return

    async def end_audio_stream(self) -> None:
        return

    async def send_frame(self, jpeg: bytes) -> None:
        if jpeg:
            self.latest_frame = bytes(jpeg)

    async def send_text(self, text: str) -> None:
        if self._closed:
            return
        clean = text.strip()
        if not clean:
            return
        if clean.startswith("[GAME_EVENT]") or clean.startswith("[GAME_CONTEXT]"):
            self.manager.ingest_context(clean)
            return
        service = clean.startswith("[PROACTIVE_")
        request_text = clean
        if service:
            request_text += "\nЕсли реплика не нужна, ответь ровно [SILENCE] и больше ничего."
        async with self._lock:
            response = await self.manager.complete(
                request_text,
                self.latest_frame,
                remember=not service,
            )
        normalized = response.strip().lower()
        if normalized in SILENCE_TOKENS:
            return
        self.cb.on_text("assistant", response)
        try:
            pcm = await self.tts.synthesize(response)
        except Exception as exc:
            self.cb.on_status(f"Windows TTS недоступен: {str(exc)[:200]}")
            return
        if pcm:
            self.cb.on_audio(pcm)

    def diagnostics(self) -> dict:
        return self.manager.diagnostics()
