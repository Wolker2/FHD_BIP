from __future__ import annotations

import asyncio
import json
import random
import threading
import time
from typing import Callable

from .audio import AudioIO, SpeechGate, microphone_threshold_dbfs, pcm16_dbfs
from .capture import capture_window_jpeg, is_target_window_active
from .command_bridge import CommandBridge
from .config import Settings
from .event_bridge import EventBridge
from .game_context import AuthoritativeGameContext
from .game_tools import GameToolRegistry
from .memory import MemoryStore
from .prompt import build_system_prompt
from .proactive import ProactiveCommentManager
from .provider_base import ProviderCallbacks
from .provider_manager import ProviderManager
from .secrets import get_api_key
from .tts import WindowsSapiTTS

PROACTIVE_INTERVALS_SEC = {
    "low": (240.0, 360.0),
    "medium": (100.0, 160.0),
    "high": (50.0, 90.0),
    "chatty": (25.0, 50.0),
}
PROACTIVE_TRIGGER = """[PROACTIVE_CHECK]
Оцени последние кадры игры и свежие игровые события. Если сейчас уместна одна
короткая живая реплика бортового ИИ — произнеси её в выбранном характере.
Если говорить не о чем, сохрани полную тишину."""

BANTER_CHANCE = {
    "low": 0.22,
    "medium": 0.32,
    "high": 0.40,
    "chatty": 0.48,
}
BANTER_TRIGGER = """[PROACTIVE_BANTER_CHECK]
Посмотри на последние кадры и свежие игровые события. Если есть достоверный
повод — коротко подколоть игрока, саркастично прокомментировать его действие,
поумничать, слегка задрать или усмехнуться/посмеяться над ситуацией. Стиль
строго соответствует текущему характеру. Не выдумывай действие ради шутки.
Если реального повода нет или ситуация критическая — сохрани полную тишину."""
PROACTIVE_FRAME_WIDTH = 1920
PROACTIVE_FRAME_HEIGHT = 1080
PROACTIVE_FRAME_QUALITY = 88


def proactive_interval_range(initiative: str) -> tuple[float, float]:
    return PROACTIVE_INTERVALS_SEC.get(initiative, PROACTIVE_INTERVALS_SEC["medium"])


def banter_chance(initiative: str) -> float:
    return BANTER_CHANCE.get(initiative, BANTER_CHANCE["medium"])


class BIPRuntime:
    AUDIO_QUEUE_CHUNKS = 16
    AUDIO_STREAM_END = object()

    def __init__(
        self,
        settings: Settings,
        on_status: Callable[[str], None],
        on_text: Callable[[str, str], None],
        on_state: Callable[[str], None] | None = None,
        on_mic_level: Callable[[float, float, bool], None] | None = None,
    ):
        self.settings = settings
        self.on_status = on_status
        self.on_text = on_text
        self.on_state = on_state or (lambda _state: None)
        self.on_mic_level = on_mic_level or (lambda _level, _threshold, _active: None)
        self.memory = MemoryStore()
        self.audio = AudioIO()
        self.audio.set_output_volume(self.settings.output_volume_percent)
        self.speech_gate = SpeechGate(
            threshold_dbfs=microphone_threshold_dbfs(
                self.settings.microphone_sensitivity,
                self.settings.microphone_sensitivity_trim_db,
            ),
            sensitivity_trim_db=self.settings.microphone_sensitivity_trim_db,
        )
        self.bridge = EventBridge()
        self.command_bridge = CommandBridge()
        self.game_context = AuthoritativeGameContext()
        self.game_tools = GameToolRegistry(self.game_context, self.command_bridge)
        self.proactive_manager = ProactiveCommentManager()
        self.provider_manager: ProviderManager | None = None
        self.text_tts = WindowsSapiTTS()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.provider = None
        self._screen_task = None
        self._bridge_task = None
        self._audio_task = None
        self._proactive_task = None
        self._audio_q: asyncio.Queue[bytes | object] | None = None
        self._started = threading.Event()
        self._stop_requested = threading.Event()
        self._last_game_active: bool | None = None
        self._last_runtime_error = ""
        self._reported_speech_detected = False
        now = time.monotonic()
        self._last_user_activity_at = now
        self._last_assistant_audio_at = now

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self._stop_requested.clear()
        self._started.clear()
        self._last_runtime_error = ""
        self.on_state("starting")
        self.thread = threading.Thread(target=self._thread_main, name="FHD-BIP-Runtime", daemon=True)
        self.thread.start()

    def _thread_main(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        failed = False
        try:
            self.loop.run_until_complete(self._start_async())
            self._started.set()
            if not self._stop_requested.is_set():
                self.on_state("running")
                self.loop.run_forever()
        except Exception as exc:
            failed = True
            if not self._stop_requested.is_set():
                self._last_runtime_error = str(exc)
                self.on_status(f"Ошибка запуска: {exc}")
                self.on_state("error")
        finally:
            try:
                self.loop.run_until_complete(self._stop_async())
            except Exception:
                pass
            self.memory.close()
            self.loop.close()
            self.loop = None
            if not failed or self._stop_requested.is_set():
                self.on_state("stopped")

    async def _start_async(self) -> None:
        if self._stop_requested.is_set():
            return
        prompt = build_system_prompt(
            self.settings.personality,
            self.settings.initiative,
            self.memory.summary(),
            assistant_name=self.settings.assistant_name,
            player_address=self.settings.player_address,
        )
        callbacks = ProviderCallbacks(
            on_audio=self._on_model_audio,
            on_text=self._on_transcript,
            on_status=self.on_status,
            on_interrupted=self._on_interrupted,
        )
        self.provider_manager = ProviderManager(
            self.settings,
            prompt,
            callbacks,
            self.game_tools.declarations(),
            self.game_tools.call,
            get_api_key,
        )
        self.provider = self.provider_manager
        await self.provider.connect()
        if self._stop_requested.is_set():
            return

        self._audio_q = asyncio.Queue(maxsize=self.AUDIO_QUEUE_CHUNKS)
        self._audio_task = asyncio.create_task(self._audio_sender_loop(), name="BIPAudioSender")

        def mic_chunk(chunk: bytes):
            if self.loop and self.provider and not self._stop_requested.is_set():
                self.loop.call_soon_threadsafe(self._handle_mic_chunk, chunk)

        pcm_audio_needed = self.provider_manager.needs_pcm_audio
        if pcm_audio_needed:
            enable_input = self.settings.capture_microphone
            self.audio.start(mic_chunk, enable_input=enable_input)
        if self.settings.capture_screen:
            self._screen_task = asyncio.create_task(self._screen_loop(), name="BIPScreenStreamer")
        if self.settings.receive_game_events:
            self._bridge_task = asyncio.create_task(self.bridge.run(self._on_game_event), name="BIPEventBridge")
        self._proactive_task = asyncio.create_task(
            self._proactive_loop(), name="BIPProactiveCommentary"
        )
        game_active = self._game_is_active()
        self._last_game_active = game_active
        if self.settings.only_when_game_active and not game_active:
            self.on_status("БИП готов. Переключитесь в окно игры — вне игры микрофон и голос приостановлены.")
        elif self.provider_manager.supports_audio_input:
            self.on_status("БИП работает. Микрофон активен; говорите обычным голосом.")
        else:
            self.on_status(
                f"БИП работает через {self.provider_manager.active_provider}. "
                "Ответы озвучиваются Windows; ввод — через строку текста."
            )

    def _game_is_active(self) -> bool:
        return (
            not self.settings.only_when_game_active
            or is_target_window_active(self.settings.window_title_contains)
        )

    def set_microphone_sensitivity(self, mode: str, trim_db: float) -> None:
        """Apply local SpeechGate tuning immediately; provider VAD updates on reconnect."""
        threshold = microphone_threshold_dbfs(mode, trim_db)
        self.speech_gate.configure_sensitivity(threshold, trim_db)

    def _handle_mic_chunk(self, chunk: bytes) -> None:
        if not chunk or self._audio_q is None:
            return
        raw_level_dbfs = pcm16_dbfs(chunk)
        game_active = self._game_is_active()
        if game_active != self._last_game_active:
            self._last_game_active = game_active
            self.on_status(
                "Окно игры активно — микрофон включён"
                if game_active
                else "Окно игры не активно — микрофон приостановлен"
            )
        if not game_active:
            self.on_mic_level(
                raw_level_dbfs,
                self.speech_gate.effective_threshold_dbfs(),
                False,
            )
            if self.speech_gate.reset():
                self._queue_audio_item(self.AUDIO_STREAM_END)
            return
        if self.provider_manager and not self.provider_manager.supports_audio_input:
            self.on_mic_level(raw_level_dbfs, self.speech_gate.effective_threshold_dbfs(), False)
            if self.speech_gate.reset():
                self._queue_audio_item(self.AUDIO_STREAM_END)
            return
        # In the default non-interrupting mode, do not feed BIP's own speaker
        # audio back into Gemini as the next user turn. Intentional barge-in can
        # be restored with the dedicated UI option.
        if not self.settings.allow_voice_interruption and self.audio.is_output_active:
            self.on_mic_level(
                raw_level_dbfs,
                self.speech_gate.effective_threshold_dbfs(),
                False,
            )
            if self.speech_gate.reset():
                self._queue_audio_item(self.AUDIO_STREAM_END)
            return
        chunks, stream_ended = self.speech_gate.process(chunk)
        self.on_mic_level(
            self.speech_gate.last_level_dbfs,
            self.speech_gate.last_effective_threshold_dbfs,
            self.speech_gate.is_active,
        )
        if chunks and not self._reported_speech_detected:
            self._reported_speech_detected = True
            self.on_status("Голос обнаружен — БИП слушает")
        if chunks:
            self._last_user_activity_at = time.monotonic()
        for accepted in chunks:
            self._queue_audio_item(accepted)
        if stream_ended:
            self._queue_audio_item(self.AUDIO_STREAM_END)

    def _queue_audio_item(self, item: bytes | object) -> None:
        if self._audio_q is None:
            return
        if self._audio_q.full():
            try:
                self._audio_q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._audio_q.put_nowait(item)
        except asyncio.QueueFull:
            pass

    async def _audio_sender_loop(self) -> None:
        assert self._audio_q is not None
        while True:
            item = await self._audio_q.get()
            if self.provider:
                try:
                    if item is self.AUDIO_STREAM_END:
                        await self.provider.end_audio_stream()
                    else:
                        await self.provider.send_audio(item)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.on_status(f"Передача микрофона временно недоступна: {type(exc).__name__}")

    def _on_model_audio(self, pcm16: bytes) -> None:
        self._last_assistant_audio_at = time.monotonic()
        if self._game_is_active():
            self.audio.play(pcm16)

    def _on_interrupted(self) -> None:
        self.audio.clear_output()
        self.text_tts.stop()
        if self.settings.allow_voice_interruption and self.speech_gate.is_active:
            self.on_status("Реплика БИПа прервана подтверждённой речью")

    def _on_transcript(self, role: str, text: str) -> None:
        if self.settings.remember_transcripts:
            self.memory.add_transcript(role, text)
        self.on_text(role, text)
        if (
            role == "assistant"
            and self.settings.speak_text_providers
            and self.provider_manager
            and not self.provider_manager.has_native_audio
            and self._game_is_active()
        ):
            if self.text_tts.speak(
                text,
                self.audio.output_volume_percent,
                self.settings.tts_voice,
            ):
                self._last_assistant_audio_at = time.monotonic()

    async def _screen_loop(self) -> None:
        # Gemini Live video/images are intentionally limited here to <= 1 fps.
        interval = max(1.0, float(self.settings.frame_interval_sec))
        last_warn = 0.0
        while True:
            if not self._game_is_active():
                await asyncio.sleep(interval)
                continue
            try:
                jpeg = await asyncio.to_thread(
                    capture_window_jpeg,
                    self.settings.window_title_contains,
                    self.settings.max_frame_width,
                    self.settings.max_frame_height,
                    self.settings.jpeg_quality,
                )
                if self.provider:
                    await self.provider.send_frame(jpeg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                now = time.time()
                if now - last_warn > 10:
                    self.on_status(f"Экран временно недоступен: {exc}")
                    last_warn = now
            await asyncio.sleep(interval)

    async def _proactive_loop(self) -> None:
        minimum, maximum = proactive_interval_range(self.settings.initiative)
        while True:
            await asyncio.sleep(random.uniform(minimum, maximum))
            if not self.provider or not self._game_is_active():
                continue
            now = time.monotonic()
            if self.speech_gate.is_active:
                continue
            if now - self._last_user_activity_at < 12.0:
                continue
            if now - self._last_assistant_audio_at < 8.0:
                continue
            if self.settings.capture_screen:
                try:
                    jpeg = await asyncio.to_thread(
                        capture_window_jpeg,
                        self.settings.window_title_contains,
                        max(self.settings.max_frame_width, PROACTIVE_FRAME_WIDTH),
                        max(self.settings.max_frame_height, PROACTIVE_FRAME_HEIGHT),
                        max(self.settings.jpeg_quality, PROACTIVE_FRAME_QUALITY),
                    )
                    await self.provider.send_frame(jpeg)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            trigger = (
                BANTER_TRIGGER
                if random.random() < banter_chance(self.settings.initiative)
                else PROACTIVE_TRIGGER
            )
            await self.provider.send_text(trigger)

    async def _on_game_event(self, event: dict) -> None:
        self.memory.add_event(event)
        self.command_bridge.record_result(event)
        context_changed = self.game_context.update(event)
        if not self.provider or not self._game_is_active():
            return
        compact = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        await self.provider.send_text(f"[GAME_EVENT] {compact}")
        if context_changed:
            await self.provider.send_text(self.game_context.as_model_message())
        decision = self.proactive_manager.consider(event)
        now = time.monotonic()
        if (
            decision.speak
            and not self.speech_gate.is_active
            and now - self._last_user_activity_at >= 4.0
            and now - self._last_assistant_audio_at >= 4.0
        ):
            await self.provider.send_text(
                f"[IMPORTANT_GAME_EVENT priority={decision.priority} reason={decision.reason}] "
                "Коротко сообщи или прокомментируй это реальное событие. При угрозе сначала польза; "
                "при обычном событии допустим характерный юмор. Не выдумывай отсутствующие детали."
            )

    def diagnostics(self) -> dict[str, object]:
        provider_data = self.provider.diagnostics() if self.provider and hasattr(self.provider, "diagnostics") else {}
        connected = bool(provider_data.get("connected"))
        active_provider = provider_data.get("active_provider") or self.settings.provider
        profile = self.settings.profile(str(active_provider))
        audio_input = bool(provider_data.get("audio_input"))
        native_audio = bool(provider_data.get("native_audio"))
        return {
            "version": "0.8.0",
            "provider": active_provider,
            "provider_chain": provider_data.get("provider_chain"),
            "model": profile.get("model") or self.settings.model,
            "api_connection": "connected" if connected else "disconnected",
            "stt": "Gemini Live input transcription" if audio_input else "text input only",
            "tts": (
                "Gemini Live native audio" if active_provider == "gemini" and native_audio
                else "OpenAI TTS" if active_provider == "openai" and provider_data.get("remote_tts")
                else "Windows SAPI" if self.settings.speak_text_providers else "disabled"
            ),
            "microphone": "voice" if self.speech_gate.is_active else ("enabled" if audio_input else "disabled for this provider"),
            "voice_interruption": "enabled" if self.settings.allow_voice_interruption else "disabled",
            "game_active": self._game_is_active(),
            "game_data": "received" if self.game_context.events_received else "waiting",
            "current_system": self.game_context.system or None,
            "current_planet": self.game_context.planet or None,
            "game_tools": self.game_tools.count,
            "tool_calls": self.game_tools.calls,
            "last_tool": self.game_tools.last_tool or None,
            "commands_queued": self.command_bridge.submitted,
            "commands_pending": self.command_bridge.pending,
            "last_command": self.command_bridge.last_action or None,
            "last_command_id": self.command_bridge.last_command_id or None,
            "last_command_result": self.command_bridge.last_result or None,
            "last_api_error": provider_data.get("last_error") or self._last_runtime_error or None,
            "fallback_provider": self.provider_manager.fallback_provider if self.provider_manager else None,
            "latency_ms": provider_data.get("latency_ms"),
            "endpoint": provider_data.get("endpoint"),
            "last_failover_error": provider_data.get("last_failover_error"),
        }

    def send_text(self, text: str) -> None:
        if not text.strip() or not self.loop or not self.provider:
            return
        if (
            self.settings.remember_transcripts
            and self.provider_manager
            and not self.provider_manager.supports_audio_input
        ):
            self.memory.add_transcript("user", text.strip())
        future = asyncio.run_coroutine_threadsafe(self.provider.send_text(text.strip()), self.loop)

        def report_error(done) -> None:
            try:
                done.result()
            except Exception as exc:
                if self._stop_requested.is_set():
                    return
                self._last_runtime_error = str(exc)
                self.on_status(f"Не удалось получить ответ AI: {exc}")

        future.add_done_callback(report_error)

    def stop(self) -> None:
        self._stop_requested.set()
        loop = self.loop
        if loop and not loop.is_closed():
            fut = asyncio.run_coroutine_threadsafe(self._stop_async(), loop)
            try:
                fut.result(timeout=5)
            except Exception:
                pass
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if self.thread:
            self.thread.join(timeout=5)
            self.thread = None

    async def _stop_async(self) -> None:
        for task in (
            self._screen_task,
            self._bridge_task,
            self._audio_task,
            self._proactive_task,
        ):
            if task:
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
        self._screen_task = None
        self._bridge_task = None
        self._audio_task = None
        self._proactive_task = None
        self._audio_q = None
        self.speech_gate.reset()
        self.bridge.stop()
        self.text_tts.stop()
        self.audio.stop()
        if self.provider:
            await self.provider.close()
            self.provider = None
