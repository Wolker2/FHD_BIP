from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING, Callable

from ..config import Settings
from ..provider_base import ProviderRequestError, TextProvider
from ..provider_catalog import (
    DEFAULT_FALLBACK_ORDER,
    DEFAULT_PROVIDER_BASE_URLS,
    DEFAULT_PROVIDER_MODELS,
    DEFAULT_PROVIDER_SCOPES,
    GEMINI_TEXT_FALLBACK_MODEL,
    KEY_REQUIRED,
    VALID_PROVIDERS,
    provider_label,
)
from ..secrets import SecretStoreError, get_api_key
from .cloudru import CloudRuProvider
from .deepseek import DeepSeekProvider
from .gigachat import GigaChatProvider
from .lmstudio import LMStudioProvider
from .openai_compatible import OpenAICompatibleProvider
from .openai import OpenAIProvider
from .qwen import QwenProvider
from .yandex import YandexProvider

if TYPE_CHECKING:
    from ..game_tools import GameToolRegistry


class ProviderManager:
    def __init__(
        self,
        settings: Settings,
        system_prompt: str,
        on_status: Callable[[str], None],
        *,
        exclude: set[str] | None = None,
        game_tools: "GameToolRegistry | None" = None,
    ):
        self.settings = settings
        self.system_prompt = system_prompt
        self.on_status = on_status
        self.exclude = set(exclude or ())
        self.game_tools = game_tools
        self.history: deque[dict[str, str]] = deque(maxlen=24)
        self.context: deque[str] = deque(maxlen=16)
        self.last_provider = ""
        self.last_model = ""
        self.last_error = ""
        self.last_latency_ms = 0
        self._providers: dict[str, TextProvider] = {}

    def _chain(self) -> list[str]:
        if self.settings.provider == "auto":
            raw = list(self.settings.fallback_providers or DEFAULT_FALLBACK_ORDER)
        else:
            raw = [self.settings.provider]
            if self.settings.auto_fallback_enabled:
                raw.extend(self.settings.fallback_providers or DEFAULT_FALLBACK_ORDER)
        out: list[str] = []
        for provider_id in raw:
            if provider_id in VALID_PROVIDERS and provider_id != "auto" and provider_id not in self.exclude and provider_id not in out:
                out.append(provider_id)
        return out

    def ingest_context(self, text: str) -> None:
        clean = text.strip()
        if clean:
            self.context.append(clean[:24000])

    def _profile_model(self, provider_id: str) -> str:
        model = str(self.settings.provider_models.get(provider_id, "") or "").strip()
        if provider_id == "gemini" and (not model or "live" in model.lower()):
            return GEMINI_TEXT_FALLBACK_MODEL
        return model or DEFAULT_PROVIDER_MODELS.get(provider_id, "")

    def _profile_base_url(self, provider_id: str) -> str:
        return str(self.settings.provider_base_urls.get(provider_id, "") or DEFAULT_PROVIDER_BASE_URLS.get(provider_id, "")).strip()

    def _key(self, provider_id: str) -> str | None:
        try:
            return get_api_key(provider_id)
        except SecretStoreError as exc:
            self.last_error = f"Хранилище ключей: {exc}"
            return None

    def _is_configured(self, provider_id: str) -> tuple[bool, str]:
        key = self._key(provider_id)
        if KEY_REQUIRED.get(provider_id, False) and not key:
            return False, "нет API-ключа"
        model = self._profile_model(provider_id)
        base_url = self._profile_base_url(provider_id)
        if provider_id == "qwen" and not base_url:
            return False, "не задан workspace Base URL Qwen"
        if provider_id == "yandex" and (not model or "<folder_id>" in model.lower()):
            return False, "в модели YandexGPT не заменён <folder_ID>"
        if provider_id in {"lmstudio", "openai_compatible"} and not base_url:
            return False, "не задан Base URL"
        if provider_id == "openai_compatible" and not model:
            # Custom endpoints may implement /models; blank model is allowed.
            return True, ""
        return True, ""

    def _build(self, provider_id: str) -> TextProvider:
        cached = self._providers.get(provider_id)
        if cached:
            return cached
        key = self._key(provider_id)
        model = self._profile_model(provider_id)
        base_url = self._profile_base_url(provider_id)
        timeout = self.settings.provider_timeout_sec
        supports_images = bool(self.settings.provider_send_images.get(provider_id, False))
        if provider_id == "gigachat":
            provider: TextProvider = GigaChatProvider(
                key or "",
                base_url,
                model,
                scope=self.settings.provider_scopes.get("gigachat", DEFAULT_PROVIDER_SCOPES["gigachat"]),
                timeout_sec=timeout,
            )
        elif provider_id == "openai":
            provider = OpenAIProvider(key or "", base_url, model, timeout_sec=timeout, supports_images=supports_images)
        elif provider_id == "deepseek":
            provider = DeepSeekProvider(key or "", base_url, model, timeout_sec=timeout, supports_images=supports_images)
        elif provider_id == "qwen":
            provider = QwenProvider(key or "", base_url, model, timeout_sec=timeout, supports_images=supports_images)
        elif provider_id == "yandex":
            provider = YandexProvider(key or "", base_url, model, timeout_sec=timeout, supports_images=supports_images)
        elif provider_id == "cloudru":
            provider = CloudRuProvider(key or "", base_url, model, timeout_sec=timeout, supports_images=supports_images)
        elif provider_id == "lmstudio":
            provider = LMStudioProvider(key, base_url, model, timeout_sec=timeout, supports_images=supports_images)
        else:
            # Gemini text fallback and arbitrary OpenAI-compatible APIs share
            # the common transport. Gemini Live itself is handled by runtime.py.
            provider = OpenAICompatibleProvider(
                provider_id,
                base_url,
                model,
                key,
                timeout_sec=timeout,
                supports_images=supports_images,
            )
        self._providers[provider_id] = provider
        return provider

    def configured_chain(self) -> list[str]:
        result: list[str] = []
        for provider_id in self._chain():
            ok, _ = self._is_configured(provider_id)
            if ok:
                result.append(provider_id)
        return result

    def _effective_system_prompt(self) -> str:
        if not self.context:
            return self.system_prompt
        return (
            self.system_prompt
            + "\n\nПоследние структурированные игровые данные. Они авторитетнее догадок модели:\n"
            + "\n".join(self.context)
        )

    async def complete(
        self,
        user_text: str,
        image_jpeg: bytes | None = None,
        *,
        remember: bool = True,
    ) -> str:
        chain = self._chain()
        if not chain:
            raise ProviderRequestError("Не настроено ни одного AI-провайдера")
        errors: list[str] = []
        for provider_id in chain:
            ok, why = self._is_configured(provider_id)
            if not ok:
                errors.append(f"{provider_label(provider_id)}: {why}")
                continue
            provider = self._build(provider_id)
            started = time.perf_counter()
            try:
                frame = image_jpeg if provider.supports_images else None
                use_tools = bool(
                    self.game_tools
                    and self.settings.provider_use_tools.get(provider_id, False)
                    and isinstance(provider, OpenAICompatibleProvider)
                )
                if use_tools:
                    try:
                        text = await provider.complete_with_tools(
                            self._effective_system_prompt(),
                            list(self.history),
                            user_text,
                            frame,
                            self.game_tools.openai_schemas(),
                            self.game_tools.call,
                        )
                    except ProviderRequestError as tool_exc:
                        # Tool support is model-specific even behind a compatible API.
                        # A 400/422 or explicit unsupported-tools error should not make
                        # an otherwise healthy provider unusable: retry once without tools.
                        low = str(tool_exc).lower()
                        if any(token in low for token in (
                            "http 400", "http 422", "unsupported", "not support",
                            "tool", "function calling", "function_call",
                        )):
                            self.on_status(
                                f"{provider_label(provider_id)}: game tools не поддержаны этой моделью; "
                                "продолжаю без tool calling"
                            )
                            text = await provider.complete(
                                self._effective_system_prompt(),
                                list(self.history),
                                user_text,
                                frame,
                            )
                        else:
                            raise
                else:
                    text = await provider.complete(
                        self._effective_system_prompt(),
                        list(self.history),
                        user_text,
                        frame,
                    )
                latency = int((time.perf_counter() - started) * 1000)
                self.last_provider = provider_id
                self.last_model = str(getattr(provider, "_resolved_model", None) or self._profile_model(provider_id))
                self.last_latency_ms = latency
                self.last_error = ""
                if remember:
                    self.history.append({"role": "user", "content": user_text})
                    self.history.append({"role": "assistant", "content": text})
                if provider_id != self.settings.provider and self.settings.provider != "auto":
                    self.on_status(
                        f"Fallback: ответил {provider_label(provider_id)} ({latency} мс)"
                    )
                return text
            except Exception as exc:
                detail = str(exc).strip() or type(exc).__name__
                # API keys are never included by our providers; still cap errors.
                detail = detail[:500]
                self.last_error = f"{provider_label(provider_id)}: {detail}"
                errors.append(self.last_error)
                # Show the real sanitized provider error. Older builds reduced
                # every failure to "недоступен", hiding whether the problem
                # was key, balance, obsolete model, URL, TLS or timeout.
                self.on_status(f"{provider_label(provider_id)}: {detail}")
                if self.settings.auto_fallback_enabled:
                    self.on_status(f"{provider_label(provider_id)} недоступен; пробую резервный AI…")
        raise ProviderRequestError("Все AI-провайдеры недоступны: " + " | ".join(errors[-5:]))

    def diagnostics(self) -> dict:
        chain = self._chain()
        configured = self.configured_chain()
        return {
            "selected_provider": self.settings.provider,
            "chain": chain,
            "configured_chain": configured,
            "last_provider": self.last_provider,
            "last_model": self.last_model,
            "last_latency_ms": self.last_latency_ms,
            "last_error": self.last_error,
            "game_tools_enabled": bool(self.game_tools),
            "game_tool_count": len(self.game_tools.names()) if self.game_tools else 0,
        }
