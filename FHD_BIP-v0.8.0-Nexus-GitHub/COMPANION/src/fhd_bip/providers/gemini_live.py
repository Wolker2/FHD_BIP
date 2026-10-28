from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import time
from typing import Any, Callable
from urllib.parse import quote

import websockets

from ..provider_base import LiveProvider, ProviderCallbacks


class GeminiLiveProvider(LiveProvider):
    """Resilient raw-WebSocket client for the Gemini Live API."""

    ENDPOINT = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    SETUP_TIMEOUT_SEC = 20.0
    RECONNECT_BASE_DELAY_SEC = 1.0
    MAX_RECONNECT_DELAY_SEC = 30.0

    def __init__(
        self,
        api_key: str,
        model: str,
        voice: str,
        system_prompt: str,
        callbacks: ProviderCallbacks,
        microphone_sensitivity: str = "low",
        allow_voice_interruption: bool = False,
        tool_declarations: list[dict[str, Any]] | None = None,
        tool_handler: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.system_prompt = system_prompt
        self.cb = callbacks
        self.microphone_sensitivity = microphone_sensitivity if microphone_sensitivity in {"low", "medium", "high"} else "low"
        self.allow_voice_interruption = bool(allow_voice_interruption)
        self.tool_declarations = list(tool_declarations or [])
        self.tool_handler = tool_handler
        self.ws = None
        self._manager_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._closing = False
        self._ever_ready = False
        self._cycle_was_ready = False
        self._session_handle: str | None = None
        self._last_error = ""
        self._connect_started_at = 0.0
        self.last_latency_ms: int | None = None
        self.tool_calls_handled = 0

    def _vad_config(self) -> dict:
        # Local SpeechGate already rejects ambient noise. These server settings
        # improve pickup of normal/quiet speech without making the low preset noisy.
        if self.microphone_sensitivity == "high":
            start = "START_SENSITIVITY_HIGH"
            prefix = 70
        elif self.microphone_sensitivity == "medium":
            start = "START_SENSITIVITY_HIGH"
            prefix = 90
        else:
            start = "START_SENSITIVITY_LOW"
            prefix = 110
        return {
            "disabled": False,
            "startOfSpeechSensitivity": start,
            "prefixPaddingMs": prefix,
            "endOfSpeechSensitivity": "END_SENSITIVITY_LOW",
            "silenceDurationMs": 750,
        }

    def _setup_message(self) -> dict:
        setup: dict = {
            "model": f"models/{self.model}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": self.voice}
                    }
                },
            },
            "systemInstruction": {"parts": [{"text": self.system_prompt}]},
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            "realtimeInputConfig": {
                "automaticActivityDetection": self._vad_config(),
                "activityHandling": (
                    "START_OF_ACTIVITY_INTERRUPTS"
                    if self.allow_voice_interruption
                    else "NO_INTERRUPTION"
                ),
                "turnCoverage": "TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO",
            },
            "contextWindowCompression": {"slidingWindow": {}},
            "sessionResumption": (
                {"handle": self._session_handle}
                if self._session_handle
                else {}
            ),
        }
        if self.tool_declarations:
            setup["tools"] = [{"functionDeclarations": self.tool_declarations}]
        return {"setup": setup}

    async def connect(self) -> None:
        if self._manager_task and not self._manager_task.done():
            return
        self._closing = False
        self._ready.clear()
        self._connect_started_at = time.monotonic()
        self.cb.on_status("Подключение к Gemini Live…")
        self._manager_task = asyncio.create_task(
            self._connection_manager(), name="GeminiConnectionManager"
        )

        ready_wait = asyncio.create_task(self._ready.wait())
        try:
            done, _ = await asyncio.wait(
                {ready_wait, self._manager_task},
                timeout=self.SETUP_TIMEOUT_SEC,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if ready_wait in done and ready_wait.result():
                return
            if self._manager_task in done:
                await self._manager_task
                raise RuntimeError(f"Gemini Live не подключён: {self._last_error}")
            raise TimeoutError("Gemini Live не подтвердил настройку сессии за 20 секунд")
        except Exception:
            await self.close(notify=False)
            raise
        finally:
            ready_wait.cancel()
            try:
                await ready_wait
            except BaseException:
                pass

    async def _connection_manager(self) -> None:
        delay = self.RECONNECT_BASE_DELAY_SEC
        while not self._closing:
            attempted_resume = bool(self._session_handle)
            self._cycle_was_ready = False
            try:
                await self._connection_cycle()
                self._last_error = "сервер закрыл WebSocket без сообщения об ошибке"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                detail = self._sanitize_error(str(exc).strip())
                self._last_error = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__

            self._ready.clear()
            if self._closing:
                break
            if not self._ever_ready:
                return

            if attempted_resume and not self._cycle_was_ready:
                self._session_handle = None
                self.cb.on_status("Возобновление сессии отклонено; создаётся новая сессия…")
            else:
                self.cb.on_status(f"Связь с Gemini потеряна ({self._last_error}). Переподключение…")

            if self._cycle_was_ready:
                delay = self.RECONNECT_BASE_DELAY_SEC
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, self.MAX_RECONNECT_DELAY_SEC)

    async def _connection_cycle(self) -> None:
        cycle_started_at = time.monotonic()
        url = f"{self.ENDPOINT}?key={quote(self.api_key, safe='')}"
        ws = await websockets.connect(
            url,
            max_size=8 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            open_timeout=15,
        )
        self.ws = ws
        try:
            await ws.send(json.dumps(self._setup_message(), ensure_ascii=False, separators=(",", ":")))

            # The protocol forbids audio/video/text before setupComplete.
            while not self._ready.is_set():
                raw = await asyncio.wait_for(ws.recv(), timeout=self.SETUP_TIMEOUT_SEC)
                await self._handle_message(raw)

            self._cycle_was_ready = True
            self.last_latency_ms = round((time.monotonic() - cycle_started_at) * 1000)
            self._last_error = ""
            restored = self._ever_ready
            self._ever_ready = True
            self.cb.on_status("Связь с Gemini восстановлена" if restored else "Gemini Live готов")

            async for raw in ws:
                await self._handle_message(raw)
        finally:
            if self.ws is ws:
                self.ws = None
                self._ready.clear()
            try:
                await ws.close()
            except Exception:
                pass

    async def _handle_message(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            msg = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return
        if not isinstance(msg, dict):
            return

        if "setupComplete" in msg:
            self._ready.set()

        update = msg.get("sessionResumptionUpdate") or {}
        if update.get("resumable") and update.get("newHandle"):
            self._session_handle = str(update["newHandle"])

        go_away = msg.get("goAway") or {}
        if go_away:
            left = go_away.get("timeLeft", "скоро")
            self.cb.on_status(f"Gemini обновит соединение через {left}")

        content = msg.get("serverContent") or {}
        input_text = (content.get("inputTranscription") or {}).get("text")
        if input_text:
            self.cb.on_text("user", input_text)
        output_text = (content.get("outputTranscription") or {}).get("text")
        if output_text:
            self.cb.on_text("assistant", output_text)

        model_turn = content.get("modelTurn") or {}
        for part in model_turn.get("parts") or []:
            inline = part.get("inlineData") or {}
            data = inline.get("data")
            mime = inline.get("mimeType", "")
            if data and ("audio" in mime or not mime):
                try:
                    self.cb.on_audio(base64.b64decode(data, validate=True))
                except (binascii.Error, ValueError, TypeError):
                    continue

        if content.get("interrupted"):
            self.cb.on_interrupted()

        tool_call = msg.get("toolCall") or {}
        function_calls = tool_call.get("functionCalls") or []
        if function_calls:
            await self._handle_tool_calls(function_calls)

    async def _handle_tool_calls(self, function_calls: list[Any]) -> None:
        responses: list[dict[str, Any]] = []
        for call in function_calls[:16]:
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("id") or "")
            name = str(call.get("name") or "")
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            if not call_id or not name:
                continue
            try:
                result = (
                    self.tool_handler(name, args)
                    if self.tool_handler
                    else {"known": False, "value": None, "error": "tool_handler_unavailable"}
                )
            except Exception as exc:
                result = {"known": False, "value": None, "error": type(exc).__name__}
            responses.append({"id": call_id, "name": name, "response": {"result": result}})
            self.tool_calls_handled += 1
        if responses:
            await self._send_json({"toolResponse": {"functionResponses": responses}})

    def diagnostics(self) -> dict[str, Any]:
        return {
            "connected": self._ready.is_set() and self.ws is not None and not self._closing,
            "last_error": self._last_error or None,
            "latency_ms": self.last_latency_ms,
            "tool_calls_handled": self.tool_calls_handled,
            "voice_interruption": self.allow_voice_interruption,
            "native_audio": True,
            "audio_input": True,
            "vision": True,
        }

    def _sanitize_error(self, detail: str) -> str:
        if self.api_key:
            detail = detail.replace(self.api_key, "***")
            detail = detail.replace(quote(self.api_key, safe=""), "***")
        return re.sub(r"([?&]key=)[^&\s]+", r"\1***", detail, flags=re.IGNORECASE)

    async def _send_json(self, obj: dict) -> bool:
        if not self._ready.is_set() or self.ws is None or self._closing:
            return False
        async with self._send_lock:
            ws = self.ws
            if ws is None or not self._ready.is_set() or self._closing:
                return False
            await ws.send(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
            return True

    async def send_audio(self, pcm16: bytes) -> None:
        if pcm16:
            await self._send_json({
                "realtimeInput": {
                    "audio": {
                        "data": base64.b64encode(pcm16).decode("ascii"),
                        "mimeType": "audio/pcm;rate=16000",
                    }
                }
            })

    async def end_audio_stream(self) -> None:
        await self._send_json({"realtimeInput": {"audioStreamEnd": True}})

    async def send_frame(self, jpeg: bytes) -> None:
        if jpeg:
            await self._send_json({
                "realtimeInput": {
                    "video": {
                        "data": base64.b64encode(jpeg).decode("ascii"),
                        "mimeType": "image/jpeg",
                    }
                }
            })

    async def send_text(self, text: str) -> None:
        if text.strip():
            await self._send_json({"realtimeInput": {"text": text.strip()}})

    async def close(self, notify: bool = True) -> None:
        self._closing = True
        self._ready.clear()
        task = self._manager_task
        self._manager_task = None
        if task:
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        ws = self.ws
        self.ws = None
        if ws:
            try:
                await ws.close()
            except Exception:
                pass
        if notify:
            self.cb.on_status("Отключено")
