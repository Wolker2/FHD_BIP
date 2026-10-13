from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from .config import Settings
from .provider_base import ProviderCallbacks
from .providers import GeminiLiveProvider, OpenAIGPTProvider, OpenAICompatibleProvider


@dataclass(frozen=True)
class ProviderDescriptor:
    provider_id: str
    title: str
    mode: str


PROVIDER_CATALOG = (
    ProviderDescriptor("gemini", "Gemini Live", "native_live_voice"),
    ProviderDescriptor("openai", "GPT (OpenAI)", "openai_tts_voice"),
    ProviderDescriptor("gigachat", "GigaChat", "openai_compatible_text"),
    ProviderDescriptor("yandex", "YandexGPT", "openai_compatible_text"),
    ProviderDescriptor("deepseek", "DeepSeek", "openai_compatible_text"),
    ProviderDescriptor("qwen", "Qwen", "openai_compatible_text"),
    ProviderDescriptor("cloudru", "Cloud.ru", "openai_compatible_text"),
    ProviderDescriptor("lmstudio", "LM Studio", "openai_compatible_text"),
    ProviderDescriptor("openai_compatible", "Пользовательский OpenAI-compatible", "openai_compatible_text"),
)

AUTO_ORDER = ("gemini", "openai", "deepseek", "qwen", "gigachat", "yandex", "cloudru", "lmstudio", "openai_compatible")


class ProviderManager:
    """Managed provider chain with automatic failover and shared context."""

    def __init__(
        self,
        settings: Settings,
        system_prompt: str,
        callbacks: ProviderCallbacks,
        tool_declarations: list[dict[str, Any]],
        tool_handler,
        key_loader: Callable[[str], str | None],
    ):
        self.settings = settings
        self.system_prompt = system_prompt
        self.callbacks = callbacks
        self.tool_declarations = tool_declarations
        self.tool_handler = tool_handler
        self.key_loader = key_loader
        self.active_provider: str | None = None
        self.fallback_provider: str | None = None
        self._provider = None
        self._candidate_index = -1
        self._switch_lock = asyncio.Lock()
        self._closing = False
        self._last_error = ""
        self._exact_context = ""
        self._latest_frame: bytes | None = None
        self._conversation: deque[str] = deque(maxlen=16)
        self._candidates = self._build_candidates()

    def _build_candidates(self) -> list[str]:
        requested = list(AUTO_ORDER) if self.settings.provider == "auto" else [self.settings.provider]
        if self.settings.auto_fallback:
            requested.extend(self.settings.fallback_providers)
            if self.settings.provider != "auto":
                requested.append("lmstudio")
        result: list[str] = []
        for provider in requested:
            if provider in {item.provider_id for item in PROVIDER_CATALOG} and provider not in result:
                result.append(provider)
        return result

    def _wrapped_callbacks(self) -> ProviderCallbacks:
        def on_text(role: str, text: str) -> None:
            clean = text.strip()
            if clean:
                self._conversation.append(f"{role}: {clean[:800]}")
            self.callbacks.on_text(role, text)

        return ProviderCallbacks(
            on_audio=self.callbacks.on_audio,
            on_text=on_text,
            on_status=self.callbacks.on_status,
            on_interrupted=self.callbacks.on_interrupted,
        )

    def _create(self, provider_id: str):
        profile = self.settings.profile(provider_id)
        key = self.key_loader(provider_id) or ""
        prompt = self.system_prompt
        if self._conversation:
            prompt += "\n\nНедавний разговор до переключения провайдера:\n" + "\n".join(self._conversation)
        callbacks = self._wrapped_callbacks()
        if provider_id == "gemini":
            if not key:
                raise RuntimeError("Gemini: API-ключ не найден")
            model = str(profile.get("model") or self.settings.model)
            return GeminiLiveProvider(
                key, model, self.settings.voice, prompt, callbacks,
                microphone_sensitivity=self.settings.microphone_sensitivity,
                allow_voice_interruption=self.settings.allow_voice_interruption,
                tool_declarations=self.tool_declarations,
                tool_handler=self.tool_handler,
            )
        if provider_id == "openai":
            return OpenAIGPTProvider(
                provider_id=provider_id,
                api_key=key,
                model=str(profile.get("model") or "gpt-4o-mini"),
                endpoint=str(profile.get("endpoint") or "https://api.openai.com/v1/chat/completions"),
                system_prompt=prompt,
                callbacks=callbacks,
                timeout_sec=self.settings.request_timeout_sec,
                vision=bool(profile.get("vision")),
                voice=self.settings.gpt_voice,
                tool_declarations=self.tool_declarations,
                tool_handler=self.tool_handler,
            )
        return OpenAICompatibleProvider(
            provider_id=provider_id,
            api_key=key,
            model=str(profile.get("model") or ""),
            endpoint=str(profile.get("endpoint") or ""),
            system_prompt=prompt,
            callbacks=callbacks,
            timeout_sec=self.settings.request_timeout_sec,
            vision=bool(profile.get("vision")),
            tool_declarations=self.tool_declarations,
            tool_handler=self.tool_handler,
        )

    async def connect(self) -> None:
        self._closing = False
        errors: list[str] = []
        for index, provider_id in enumerate(self._candidates):
            try:
                provider = self._create(provider_id)
                await provider.connect()
                self._provider = provider
                self._candidate_index = index
                self.active_provider = provider_id
                self.fallback_provider = provider_id if index > 0 else None
                self._last_error = " | ".join(errors)[-1800:]
                if index > 0:
                    self.callbacks.on_status(f"Включён резервный AI: {provider_id}")
                return
            except Exception as exc:
                errors.append(str(exc))
        self._last_error = " | ".join(errors)[-1800:]
        raise RuntimeError("Ни один AI-провайдер не подключён: " + self._last_error)

    async def _switch(self, failed_provider, failed_error: Exception) -> bool:
        if not self.settings.auto_fallback or self._closing:
            return False
        async with self._switch_lock:
            # Another concurrent sender may already have completed failover.
            if self._provider is not failed_provider:
                return self._provider is not None
            failed_name = self.active_provider or "AI"
            current = self._provider
            if current:
                try:
                    await current.close()
                except Exception:
                    pass
            errors = [f"{failed_name}: {failed_error}"]
            for index in range(self._candidate_index + 1, len(self._candidates)):
                provider_id = self._candidates[index]
                try:
                    provider = self._create(provider_id)
                    await provider.connect()
                    if self._latest_frame:
                        await provider.send_frame(self._latest_frame)
                    if self._exact_context:
                        await provider.send_text(self._exact_context)
                    self._provider = provider
                    self._candidate_index = index
                    self.active_provider = provider_id
                    self.fallback_provider = provider_id
                    self.callbacks.on_status(f"{failed_name} недоступен. Автоматически включён резерв: {provider_id}")
                    self._last_error = " | ".join(errors)[-1800:]
                    return True
                except Exception as exc:
                    errors.append(f"{provider_id}: {exc}")
            self._last_error = " | ".join(errors)[-1800:]
            return False

    async def _send_with_failover(self, method: str, *args, retry: bool = True) -> None:
        provider = self._provider
        if not provider:
            raise RuntimeError("AI-провайдер не подключён")
        try:
            await getattr(provider, method)(*args)
        except Exception as exc:
            switched = await self._switch(provider, exc)
            if not switched or not retry or not self._provider:
                raise
            await getattr(self._provider, method)(*args)

    async def send_audio(self, pcm16: bytes) -> None:
        await self._send_with_failover("send_audio", pcm16)

    async def end_audio_stream(self) -> None:
        await self._send_with_failover("end_audio_stream")

    async def send_frame(self, jpeg: bytes) -> None:
        if jpeg:
            self._latest_frame = bytes(jpeg)
        await self._send_with_failover("send_frame", jpeg)

    async def send_text(self, text: str) -> None:
        if text.startswith("[GAME_CONTEXT]"):
            self._exact_context = text
        elif text and not text.startswith("["):
            self._conversation.append(f"user: {text[:800]}")
        await self._send_with_failover("send_text", text)

    async def close(self, notify: bool = True) -> None:
        self._closing = True
        provider = self._provider
        self._provider = None
        if provider:
            try:
                if isinstance(provider, GeminiLiveProvider):
                    await provider.close(notify=notify)
                else:
                    await provider.close()
                    if notify:
                        self.callbacks.on_status("Отключено")
            except Exception:
                pass

    @property
    def supports_audio_input(self) -> bool:
        return self.active_provider == "gemini"

    @property
    def has_native_audio(self) -> bool:
        return self.active_provider in {"gemini", "openai"}

    @property
    def needs_pcm_audio(self) -> bool:
        start = max(0, self._candidate_index)
        return any(candidate in {"gemini", "openai"} for candidate in self._candidates[start:])

    @property
    def may_use_audio_input(self) -> bool:
        start = max(0, self._candidate_index)
        return "gemini" in self._candidates[start:]

    def diagnostics(self) -> dict[str, Any]:
        provider_data = self._provider.diagnostics() if self._provider and hasattr(self._provider, "diagnostics") else {}
        return {
            **provider_data,
            "active_provider": self.active_provider,
            "fallback_provider": self.fallback_provider,
            "provider_chain": list(self._candidates),
            "last_failover_error": self._last_error or None,
        }
