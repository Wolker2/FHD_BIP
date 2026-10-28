from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from typing import Any

from .openai_compatible import OpenAICompatibleProvider


class OpenAIGPTProvider(OpenAICompatibleProvider):
    """OpenAI GPT text model with OpenAI TTS audio output.

    Chat Completions remains the reasoning/tool channel.  The separate Speech
    endpoint turns the final answer into 24 kHz PCM for the existing BIP audio
    output path, so the selected OpenAI voice is preserved across replies.
    """

    TTS_ENDPOINT = "https://api.openai.com/v1/audio/speech"

    def __init__(self, *args, voice: str = "coral", **kwargs):
        super().__init__(*args, **kwargs)
        self.voice = voice.strip() or "coral"
        self.tts_latency_ms: int | None = None

    async def send_text(self, text: str) -> None:
        text = text.strip()
        if not text or not self._ready or self._closing:
            return
        if text.startswith("[GAME_CONTEXT]"):
            self._exact_context = text
            return
        if text.startswith("[GAME_EVENT]"):
            self._recent_events.append(text)
            return
        async with self._request_lock:
            started = time.monotonic()
            try:
                answer = await asyncio.to_thread(self._complete, text)
                self.last_latency_ms = round((time.monotonic() - started) * 1000)
                self._last_error = ""
            except Exception as exc:
                self._last_error = self._sanitize(str(exc))
                raise RuntimeError(f"{self.provider_id}: {self._last_error}") from exc
            if not answer or answer.strip().upper() in {"[SILENCE]", "SILENCE", "[ТИШИНА]"}:
                return
            self.cb.on_text("assistant", answer.strip())
            try:
                speech_started = time.monotonic()
                pcm = await asyncio.to_thread(self._synthesize, answer.strip())
                self.tts_latency_ms = round((time.monotonic() - speech_started) * 1000)
                if pcm:
                    self.cb.on_audio(pcm)
            except Exception as exc:
                # The GPT answer remains visible even if the optional audio
                # endpoint is unavailable; ProviderManager can still fail over
                # on the next request.
                self._last_error = self._sanitize(str(exc))
                self.cb.on_status(f"GPT ответ получен, но голос недоступен: {self._last_error}")

    def _synthesize(self, text: str) -> bytes:
        payload = {
            "model": "gpt-4o-mini-tts",
            "voice": self.voice,
            "input": text,
            "response_format": "pcm",
            "instructions": "Говори на русском языке спокойно, естественно и в характере бортового ИИ.",
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.TTS_ENDPOINT,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "audio/pcm",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                return response.read(16 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise RuntimeError(f"TTS HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"TTS соединение недоступно: {exc.reason}") from exc

    def diagnostics(self) -> dict[str, Any]:
        return {
            **super().diagnostics(),
            "provider_voice": self.voice,
            "remote_tts": True,
            "tts_latency_ms": self.tts_latency_ms,
        }
