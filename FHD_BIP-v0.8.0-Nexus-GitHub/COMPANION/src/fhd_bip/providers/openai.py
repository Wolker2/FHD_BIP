from __future__ import annotations

import asyncio
import base64
import json
import urllib.request
from typing import Any, Callable

from ..provider_base import ProviderRequestError
from .openai_compatible import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    """Official OpenAI Responses API provider for FHD_BIP.

    OpenAI is intentionally a first-class provider rather than being routed
    through the generic Chat Completions compatibility layer. This keeps BIP
    aligned with the current OpenAI Responses API while preserving the same
    ProviderManager/fallback interface used by the other providers.
    """

    MAX_TOOL_ROUNDS = 4

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        timeout_sec: float = 30.0,
        supports_images: bool = True,
    ):
        super().__init__(
            "openai",
            base_url or "https://api.openai.com/v1",
            model or "gpt-5.6-luna",
            api_key,
            timeout_sec=timeout_sec,
            supports_images=supports_images,
        )

    def _post_responses_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            self._endpoint("/responses"),
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        return self._http_json(req)

    @staticmethod
    def _response_text(data: dict[str, Any]) -> str:
        # Raw Responses API responses contain text inside output[].content[].
        chunks: list[str] = []
        output = data.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") in {"output_text", "text"}:
                        text = part.get("text")
                        if isinstance(text, str) and text:
                            chunks.append(text)
        if chunks:
            return "".join(chunks).strip()
        # Some compatible gateways expose a convenience output_text field.
        direct = data.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        err = data.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            raise ProviderRequestError(err["message"][:500])
        raise ProviderRequestError("OpenAI вернул пустой текст")

    @staticmethod
    def _history_text(history: list[dict[str, str]], user_text: str) -> str:
        lines: list[str] = []
        if history:
            lines.append("Недавний диалог:")
        for item in history[-24:]:
            role = item.get("role")
            content = item.get("content")
            if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                who = "ИГРОК" if role == "user" else "БИП"
                lines.append(f"{who}: {content[:12000]}")
        if lines:
            lines.append("\nТекущая реплика игрока:")
        lines.append(user_text)
        return "\n".join(lines)

    def _initial_input(
        self,
        history: list[dict[str, str]],
        user_text: str,
        image_jpeg: bytes | None,
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [
            {"type": "input_text", "text": self._history_text(history, user_text)}
        ]
        if image_jpeg and self.supports_images:
            encoded = base64.b64encode(image_jpeg).decode("ascii")
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{encoded}",
                }
            )
        return [{"role": "user", "content": content}]

    def _complete_responses_sync(
        self,
        model: str,
        system_prompt: str,
        history: list[dict[str, str]],
        user_text: str,
        image_jpeg: bytes | None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "instructions": system_prompt,
            "input": self._initial_input(history, user_text, image_jpeg),
            "store": False,
        }
        return self._response_text(self._post_responses_sync(payload))

    @staticmethod
    def _responses_tools(chat_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in chat_tools:
            if not isinstance(item, dict) or item.get("type") != "function":
                continue
            fn = item.get("function")
            if not isinstance(fn, dict):
                continue
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            out.append(
                {
                    "type": "function",
                    "name": name,
                    "description": str(fn.get("description") or ""),
                    "parameters": fn.get("parameters")
                    or {"type": "object", "properties": {}, "additionalProperties": False},
                    "strict": True,
                }
            )
        return out

    @staticmethod
    def _function_calls(data: dict[str, Any]) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        output = data.get("output")
        if not isinstance(output, list):
            return calls
        for item in output:
            if isinstance(item, dict) and item.get("type") == "function_call":
                calls.append(item)
        return calls

    def _complete_with_tools_responses_sync(
        self,
        model: str,
        system_prompt: str,
        history: list[dict[str, str]],
        user_text: str,
        image_jpeg: bytes | None,
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str], Any],
    ) -> str:
        input_items: list[dict[str, Any]] = self._initial_input(history, user_text, image_jpeg)
        response_tools = self._responses_tools(tools)
        for _round in range(self.MAX_TOOL_ROUNDS + 1):
            payload: dict[str, Any] = {
                "model": model,
                "instructions": system_prompt,
                "input": input_items,
                "tools": response_tools,
                "tool_choice": "auto",
                "store": False,
            }
            data = self._post_responses_sync(payload)
            calls = self._function_calls(data)
            if not calls:
                return self._response_text(data)

            output = data.get("output")
            if isinstance(output, list):
                # Preserve model reasoning/function-call items exactly as returned.
                input_items.extend(item for item in output if isinstance(item, dict))

            for call in calls:
                call_id = str(call.get("call_id") or call.get("id") or "").strip()
                name = str(call.get("name") or "").strip()
                if not call_id:
                    call_id = f"bip_openai_tool_{len(input_items)}"
                if not name:
                    result: Any = {"error": "missing_tool_name"}
                else:
                    try:
                        result = tool_executor(name)
                    except KeyError:
                        result = {"error": "unknown_tool", "tool": name}
                    except Exception as exc:
                        result = {"error": "tool_failed", "tool": name, "detail": type(exc).__name__}
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": self._safe_tool_result(result),
                    }
                )

        raise ProviderRequestError("Превышен предел циклов OpenAI game tool calling")

    async def complete(
        self,
        system_prompt: str,
        history: list[dict[str, str]],
        user_text: str,
        image_jpeg: bytes | None = None,
    ) -> str:
        model = await self._resolved_model_name()
        return await asyncio.to_thread(
            self._complete_responses_sync,
            model,
            system_prompt,
            history,
            user_text,
            image_jpeg,
        )

    async def complete_with_tools(
        self,
        system_prompt: str,
        history: list[dict[str, str]],
        user_text: str,
        image_jpeg: bytes | None,
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str], Any],
    ) -> str:
        if not tools:
            return await self.complete(system_prompt, history, user_text, image_jpeg)
        model = await self._resolved_model_name()
        return await asyncio.to_thread(
            self._complete_with_tools_responses_sync,
            model,
            system_prompt,
            history,
            user_text,
            image_jpeg,
            tools,
            tool_executor,
        )

    async def probe(self) -> dict[str, str]:
        """Validate key/model through /models and a tiny Responses request."""
        def run() -> dict[str, str]:
            listed = self._list_models_sync()
            model = self.model or listed
            payload = {
                "model": model,
                "instructions": "Reply with exactly OK.",
                "input": "OK?",
                "store": False,
            }
            text = self._response_text(self._post_responses_sync(payload))
            return {"model_from_api": listed, "requested_model": model, "reply": text[:80]}

        return await asyncio.to_thread(run)
