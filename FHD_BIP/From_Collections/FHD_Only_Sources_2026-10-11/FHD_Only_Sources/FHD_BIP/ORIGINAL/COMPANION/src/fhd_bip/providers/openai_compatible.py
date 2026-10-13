from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Callable
from urllib.parse import urlparse

from ..provider_base import LiveProvider, ProviderCallbacks


def _lower_schema(value: Any) -> Any:
    if isinstance(value, dict):
        out = {key: _lower_schema(item) for key, item in value.items()}
        if isinstance(out.get("type"), str):
            out["type"] = out["type"].lower()
        return out
    if isinstance(value, list):
        return [_lower_schema(item) for item in value]
    return value


class OpenAICompatibleProvider(LiveProvider):
    """Real chat-completions adapter used by cloud APIs and LM Studio.

    It keeps game context separate from conversation history, supports standard
    function calling, and can attach the latest game frame to vision models.
    """

    def __init__(
        self,
        provider_id: str,
        api_key: str,
        model: str,
        endpoint: str,
        system_prompt: str,
        callbacks: ProviderCallbacks,
        timeout_sec: float = 30.0,
        vision: bool = False,
        tool_declarations: list[dict[str, Any]] | None = None,
        tool_handler: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ):
        self.provider_id = provider_id
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.endpoint = endpoint.strip()
        self.system_prompt = system_prompt
        self.cb = callbacks
        self.timeout_sec = max(5.0, min(120.0, float(timeout_sec)))
        self.vision = bool(vision)
        self.tool_declarations = list(tool_declarations or [])
        self.tool_handler = tool_handler
        self._ready = False
        self._closing = False
        self._request_lock = asyncio.Lock()
        self._latest_frame: bytes | None = None
        self._exact_context = ""
        self._recent_events: deque[str] = deque(maxlen=12)
        self._history: deque[dict[str, Any]] = deque(maxlen=24)
        self._last_error = ""
        self.last_latency_ms: int | None = None
        self.tool_calls_handled = 0

    async def connect(self) -> None:
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError(f"{self.provider_id}: укажите полный HTTP(S) endpoint")
        if parsed.scheme == "http" and (parsed.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError(f"{self.provider_id}: удалённый API с ключом должен использовать HTTPS")
        if re.search(r"[?&](?:api_?key|key|token|access_token|secret)=", self.endpoint, flags=re.IGNORECASE):
            raise RuntimeError(f"{self.provider_id}: не помещайте ключ в endpoint; сохраните его отдельной кнопкой")
        if not self.model:
            raise RuntimeError(f"{self.provider_id}: не указано название модели")
        if self.provider_id != "lmstudio" and not self.api_key:
            raise RuntimeError(f"{self.provider_id}: API-ключ/токен не найден")
        if self.provider_id == "lmstudio":
            await asyncio.to_thread(self._probe_lmstudio)
        self._closing = False
        self._ready = True
        self.cb.on_status(f"{self.provider_id}: текстовый LLM готов")

    def _probe_lmstudio(self) -> None:
        marker = "/chat/completions"
        base = self.endpoint.split(marker, 1)[0].rstrip("/") if marker in self.endpoint else self.endpoint.rstrip("/")
        url = base + "/models"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=min(5.0, self.timeout_sec)) as response:
                if response.status >= 400:
                    raise RuntimeError(f"LM Studio HTTP {response.status}")
                response.read(1024 * 1024)
        except Exception as exc:
            raise RuntimeError("LM Studio не отвечает на /v1/models") from exc

    async def close(self) -> None:
        self._closing = True
        self._ready = False

    async def send_audio(self, pcm16: bytes) -> None:
        # Speech-to-text is deliberately not faked by this LLM adapter.
        return

    async def end_audio_stream(self) -> None:
        return

    async def send_frame(self, jpeg: bytes) -> None:
        if jpeg:
            self._latest_frame = bytes(jpeg)

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
            if answer and answer.strip().upper() not in {"[SILENCE]", "SILENCE", "[ТИШИНА]"}:
                self.cb.on_text("assistant", answer.strip())

    def _complete(self, text: str) -> str:
        messages: list[dict[str, Any]] = [{
            "role": "system",
            "content": self.system_prompt
            + "\nДля команды, при которой нужно промолчать, верни ровно [SILENCE] без других слов.",
        }]
        if self._exact_context:
            messages.append({"role": "system", "content": self._exact_context})
        if self._recent_events:
            messages.append({"role": "system", "content": "\n".join(self._recent_events)})
        messages.extend(list(self._history))

        user_content: Any = text
        if self.vision and self._latest_frame:
            encoded = base64.b64encode(self._latest_frame).decode("ascii")
            user_content = [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
            ]
        messages.append({"role": "user", "content": user_content})

        functions = [_lower_schema(declaration) for declaration in self.tool_declarations]
        tools = [{"type": "function", "function": declaration} for declaration in functions]
        for _round in range(4):
            payload: dict[str, Any] = {"model": self.model, "messages": messages}
            if functions and self.provider_id == "gigachat":
                # GigaChat's REST dialect uses the legacy OpenAI-style
                # `functions`/`function_call` fields instead of `tools`.
                payload["functions"] = functions
                payload["function_call"] = "auto"
            elif tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"
            response = self._post_json(payload)
            choices = response.get("choices") if isinstance(response, dict) else None
            if not isinstance(choices, list) or not choices:
                raise RuntimeError("API не вернул choices")
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if not isinstance(message, dict):
                raise RuntimeError("API не вернул message")
            tool_calls = message.get("tool_calls")
            if self.provider_id == "gigachat" and isinstance(message.get("function_call"), dict):
                tool_calls = [{
                    "id": f"gigachat-call-{self.tool_calls_handled + 1}",
                    "type": "function",
                    "function": message["function_call"],
                }]
            if isinstance(tool_calls, list) and tool_calls:
                messages.append(message)
                for call in tool_calls[:16]:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function") if isinstance(call.get("function"), dict) else {}
                    name = str(function.get("name") or "")
                    raw_args = function.get("arguments")
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                    except json.JSONDecodeError:
                        args = {}
                    result = self.tool_handler(name, args) if self.tool_handler else {
                        "known": False, "value": None, "error": "tool_handler_unavailable"
                    }
                    self.tool_calls_handled += 1
                    result_text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                    if self.provider_id == "gigachat":
                        messages.append({"role": "function", "name": name, "content": result_text})
                    else:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": str(call.get("id") or ""),
                            "content": result_text,
                        })
                continue
            answer = self._content_text(message.get("content"))
            self._history.append({"role": "user", "content": text})
            self._history.append({"role": "assistant", "content": answer})
            return answer
        raise RuntimeError("модель превысила лимит последовательных вызовов функций")

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                raw = response.read(8 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"соединение недоступно: {exc.reason}") from exc
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("API вернул некорректный JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("API вернул неожиданный ответ")
        return parsed

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts)
        return ""

    def _sanitize(self, detail: str) -> str:
        if self.api_key:
            detail = detail.replace(self.api_key, "***")
        detail = re.sub(r"(Bearer\s+)[^\s\"']+", r"\1***", detail, flags=re.IGNORECASE)
        return re.sub(r"([?&](?:api_?key|key|token|access_token|secret)=)[^&\s]+", r"\1***", detail, flags=re.IGNORECASE)[:1200]

    def diagnostics(self) -> dict[str, Any]:
        return {
            "connected": self._ready and not self._closing,
            "last_error": self._last_error or None,
            "latency_ms": self.last_latency_ms,
            "tool_calls_handled": self.tool_calls_handled,
            "native_audio": False,
            "audio_input": False,
            "vision": self.vision,
            "endpoint": self.endpoint,
        }
