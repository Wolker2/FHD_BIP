from __future__ import annotations

import asyncio
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from ..provider_base import ProviderRequestError, TextProvider


class GigaChatProvider(TextProvider):
    provider_id = "gigachat"
    supports_images = False
    OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

    def __init__(
        self,
        authorization_key: str,
        base_url: str,
        model: str,
        *,
        scope: str = "GIGACHAT_API_PERS",
        timeout_sec: float = 30.0,
    ):
        self.authorization_key = authorization_key.strip()
        if self.authorization_key.lower().startswith("basic "):
            self.authorization_key = self.authorization_key[6:].strip()
        self.base_url = (base_url or "https://api.giga.chat/v1").strip().rstrip("/")
        self.model = (model or "GigaChat-3-Ultra").strip()
        self.scope = (scope or "GIGACHAT_API_PERS").strip()
        self.timeout_sec = max(3.0, min(120.0, float(timeout_sec)))
        self._token = ""
        self._token_expires_at = 0.0

    @staticmethod
    def _decode_json(raw: bytes) -> dict[str, Any]:
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ProviderRequestError("GigaChat вернул некорректный JSON") from None
        if not isinstance(data, dict):
            raise ProviderRequestError("GigaChat вернул неожиданный ответ")
        return data

    def _urlopen(self, req: urllib.request.Request) -> bytes:
        try:
            # Intentionally use normal certificate verification. GigaChat's docs
            # require the proper Russian trusted CA certificates; BIP never turns
            # TLS verification off to make a broken setup appear to work.
            with urllib.request.urlopen(req, timeout=self.timeout_sec, context=ssl.create_default_context()) as response:
                return response.read(8 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read(8192).decode("utf-8", errors="replace")
            except Exception:
                pass
            raise ProviderRequestError(f"GigaChat HTTP {exc.code}: {body[:500].strip() or exc.reason}") from None
        except ssl.SSLError:
            raise ProviderRequestError(
                "Ошибка TLS GigaChat. Установите доверенные сертификаты НУЦ Минцифры, как указано в документации GigaChat."
            ) from None
        except urllib.error.URLError as exc:
            raise ProviderRequestError(f"GigaChat: ошибка соединения: {exc.reason}") from None
        except TimeoutError:
            raise ProviderRequestError("GigaChat: таймаут API") from None

    def _get_token_sync(self) -> str:
        now = time.time()
        if self._token and now < self._token_expires_at - 60:
            return self._token
        if not self.authorization_key:
            raise ProviderRequestError("Не сохранён ключ авторизации GigaChat")
        data = urllib.parse.urlencode({"scope": self.scope}).encode("ascii")
        req = urllib.request.Request(
            self.OAUTH_URL,
            data=data,
            headers={
                "Authorization": f"Basic {self.authorization_key}",
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "FHD_BIP/0.6.4",
            },
            method="POST",
        )
        payload = self._decode_json(self._urlopen(req))
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderRequestError("GigaChat не вернул access_token")
        expires_raw = payload.get("expires_at", now + 25 * 60)
        try:
            expires = float(expires_raw)
            if expires > 10_000_000_000:
                expires /= 1000.0
        except (TypeError, ValueError):
            expires = now + 25 * 60
        self._token = token
        self._token_expires_at = expires
        return token

    def _complete_sync(
        self,
        system_prompt: str,
        history: list[dict[str, str]],
        user_text: str,
    ) -> str:
        token = self._get_token_sync()
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for item in history[-24:]:
            role = item.get("role")
            content = item.get("content")
            if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                messages.append({"role": role, "content": content[:12000]})
        messages.append({"role": "user", "content": user_text})
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "FHD_BIP/0.6.4",
            },
            method="POST",
        )
        data = self._decode_json(self._urlopen(req))
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderRequestError("GigaChat не вернул choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderRequestError("GigaChat вернул пустой ответ")
        return content.strip()

    async def complete(
        self,
        system_prompt: str,
        history: list[dict[str, str]],
        user_text: str,
        image_jpeg: bytes | None = None,
    ) -> str:
        return await asyncio.to_thread(self._complete_sync, system_prompt, history, user_text)
