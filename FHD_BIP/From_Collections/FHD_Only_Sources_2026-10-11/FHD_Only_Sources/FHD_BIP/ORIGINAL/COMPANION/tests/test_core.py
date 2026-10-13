from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fhd_bip.audio import microphone_threshold_dbfs, scale_pcm16
from fhd_bip.command_bridge import CommandBridge
from fhd_bip.game_context import AuthoritativeGameContext
from fhd_bip.game_tools import GameToolRegistry
from fhd_bip.config import Settings
from fhd_bip.proactive import ProactiveCommentManager
from fhd_bip.provider_base import ProviderCallbacks
from fhd_bip.provider_manager import ProviderManager
from fhd_bip.providers.gemini_live import GeminiLiveProvider
from fhd_bip.providers.openai_compatible import OpenAICompatibleProvider
from fhd_bip.providers.openai_gpt import OpenAIGPTProvider
from fhd_bip.tts import WindowsSapiTTS


class GameContextTests(unittest.TestCase):
    def test_extended_exact_context_and_unknown_tools(self):
        context = AuthoritativeGameContext()
        tools = GameToolRegistry(context)
        self.assertFalse(tools.call("get_ship_fuel")["known"])
        changed = context.update({
            "type": "state",
            "system": "Солнце",
            "planet": "Земля",
            "map_position": {"x": 12, "y": 34},
            "fuel": 17,
            "hull_percent": 72,
            "credits": 1450200,
            "planets": [{"id": 101, "name": "Земля"}, {"id": 102, "name": "Марс"}],
            "equipment": [{"name": "Двигатель", "condition_percent": 88}],
            "quests": [{"name": "Доставка", "days_left": 4}],
            "messages": [{"text": "Получено задание", "important": True}],
        })
        self.assertTrue(changed)
        self.assertEqual(tools.call("get_ship_fuel")["value"], 17)
        self.assertEqual(tools.call("get_player_credits")["value"], 1450200)
        self.assertEqual(tools.call("get_galaxy_map_position")["value"], {"x": 12, "y": 34})
        self.assertEqual(len(tools.call("get_known_planets")["value"]), 2)
        context.update({"type": "state", "fuel": None, "credits": None})
        self.assertFalse(tools.call("get_ship_fuel")["known"])
        self.assertFalse(tools.call("get_player_credits")["known"])

    def test_system_change_clears_location_bound_data(self):
        context = AuthoritativeGameContext()
        context.update({"type": "system_context", "system": "A", "planet": "A-1", "planets": ["A-1"]})
        context.update({"type": "location", "system": "B"})
        self.assertEqual(context.system, "B")
        self.assertEqual(context.planet, "")
        self.assertEqual(context.planets, [])

    def test_navigation_and_trade_catalogs_are_authoritative(self):
        context = AuthoritativeGameContext()
        changed = context.update({
            "type": "capabilities",
            "integrations": ["FHD_Autopilot", "FHD_Trade"],
            "known_systems": [
                {"id": 17, "name": "Солнце", "open": True},
                {"id": 23, "name": "Альтаир", "open": True},
            ],
            "trade_goods": [{"id": 4, "name": "Медицина"}],
        })
        self.assertTrue(changed)
        self.assertEqual(context.known_systems[1]["name"], "Альтаир")
        self.assertEqual(context.trade_goods[0]["id"], 4)
        self.assertEqual(context.integrations, ["FHD_Autopilot", "FHD_Trade"])


class GameActionToolTests(unittest.TestCase):
    def _registry(self, root: Path):
        context = AuthoritativeGameContext()
        context.update({
            "type": "capabilities",
            "integrations": ["FHD_Autopilot", "FHD_Trade"],
            "known_systems": [
                {"id": 17, "name": "Солнце", "open": True},
                {"id": 23, "name": "Альтаир", "open": True},
            ],
            "trade_goods": [{"id": 4, "name": "Медицина"}],
        })
        bridge = CommandBridge(root / "commands.jsonl")
        return GameToolRegistry(context, bridge), bridge

    def test_autopilot_command_uses_exact_known_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry, bridge = self._registry(Path(tmp))
            result = registry.call("start_autopilot_route", {"system_name": "альтаир"})
            self.assertTrue(result["accepted"])
            self.assertEqual(result["state"], "queued")
            queued = [json.loads(line) for line in bridge.path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(queued[0]["action"], "autopilot.route_and_start")
            self.assertEqual(queued[0]["payload"]["system_id"], 23)
            self.assertEqual(queued[0]["payload"]["system_name"], "Альтаир")

    def test_unknown_system_is_not_queued(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry, bridge = self._registry(Path(tmp))
            result = registry.call("find_system_on_map", {"system_name": "Выдуманная"})
            self.assertFalse(result["accepted"])
            self.assertEqual(result["error"], "unknown_system")
            self.assertEqual(bridge.path.read_text(encoding="utf-8"), "")

    def test_missing_mod_integration_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = AuthoritativeGameContext()
            context.update({
                "type": "capabilities",
                "known_systems": [{"id": 23, "name": "Альтаир", "open": True}],
                "trade_goods": [{"id": 4, "name": "Медицина"}],
            })
            bridge = CommandBridge(Path(tmp) / "commands.jsonl")
            registry = GameToolRegistry(context, bridge)
            route = registry.call("start_autopilot_route", {"system_name": "Альтаир"})
            trade = registry.call("search_trade_good_and_mark", {"good_name": "Медицина"})
            self.assertEqual(route["error"], "integration_unavailable")
            self.assertEqual(trade["error"], "integration_unavailable")
            self.assertEqual(bridge.path.read_text(encoding="utf-8"), "")

    def test_trade_command_uses_fhd_trade_marker_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry, bridge = self._registry(Path(tmp))
            result = registry.call("search_trade_good_and_mark", {"good_name": "медицина"})
            self.assertTrue(result["accepted"])
            command = json.loads(bridge.path.read_text(encoding="utf-8").strip())
            self.assertEqual(command["action"], "trade.search_and_mark")
            self.assertEqual(command["payload"]["good_id"], 4)
            self.assertEqual(command["payload"]["marker_owner"], "FHD_Trade")
            self.assertTrue(command["payload"]["preserve_foreign_markers"])

    def test_only_matching_command_result_completes_pending_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry, bridge = self._registry(Path(tmp))
            queued = registry.call("open_galaxy_map")
            self.assertEqual(bridge.pending, 1)
            self.assertFalse(bridge.record_result({
                "type": "command_result", "command_id": "unknown", "status": "completed",
            }))
            self.assertTrue(bridge.record_result({
                "type": "command_result",
                "command_id": queued["command_id"],
                "action": "galaxy_map.open",
                "status": "completed",
            }))
            self.assertEqual(bridge.pending, 0)
            self.assertEqual(bridge.last_result["status"], "completed")


class ProactiveTests(unittest.TestCase):
    def test_important_events_and_duplicate_cooldown(self):
        manager = ProactiveCommentManager()
        event = {"type": "state", "fuel": 10}
        self.assertTrue(manager.consider(event, now=1000).speak)
        self.assertFalse(manager.consider(event, now=1001).speak)
        self.assertFalse(manager.consider({"type": "state", "fuel": 80}, now=1200).speak)


class AudioTests(unittest.TestCase):
    def test_volume_and_sensitivity_are_real_transformations(self):
        samples = (1000).to_bytes(2, "little", signed=True) + (-1000).to_bytes(2, "little", signed=True)
        half = scale_pcm16(samples, 50)
        self.assertEqual(int.from_bytes(half[:2], "little", signed=True), 500)
        self.assertEqual(scale_pcm16(samples, 0), b"\x00" * len(samples))
        self.assertLess(microphone_threshold_dbfs("medium", 5), microphone_threshold_dbfs("medium", 0))


class GeminiProtocolTests(unittest.TestCase):
    def _provider(self, allow_interruption: bool = False):
        callbacks = ProviderCallbacks(lambda _b: None, lambda _r, _t: None, lambda _s: None)
        registry = GameToolRegistry(AuthoritativeGameContext())
        return GeminiLiveProvider(
            "test-key", "gemini-3.1-flash-live-preview", "Kore", "prompt", callbacks,
            allow_voice_interruption=allow_interruption,
            tool_declarations=registry.declarations(),
            tool_handler=registry.call,
        )

    def test_setup_uses_supported_fields_and_no_interruption_default(self):
        setup = self._provider()._setup_message()["setup"]
        self.assertNotIn("proactivity", setup)
        self.assertEqual(setup["realtimeInputConfig"]["activityHandling"], "NO_INTERRUPTION")
        self.assertEqual(len(setup["tools"][0]["functionDeclarations"]), 15)

    def test_tool_response_shape(self):
        class FakeSocket:
            def __init__(self):
                self.messages = []

            async def send(self, value):
                self.messages.append(json.loads(value))

        async def run_test():
            provider = self._provider()
            provider.ws = FakeSocket()
            provider._ready.set()
            await provider._handle_message(json.dumps({
                "toolCall": {"functionCalls": [{"id": "1", "name": "get_ship_fuel", "args": {}}]}
            }))
            sent = provider.ws.messages[0]
            response = sent["toolResponse"]["functionResponses"][0]
            self.assertEqual(response["id"], "1")
            self.assertFalse(response["response"]["result"]["known"])

        asyncio.run(run_test())

    def test_api_key_is_removed_from_errors(self):
        provider = self._provider()
        provider.api_key = "secret/key+value"
        detail = provider._sanitize_error("failed wss://example.test/ws?key=secret%2Fkey%2Bvalue")
        self.assertNotIn("secret", detail)
        self.assertIn("key=***", detail)


class ProviderManagerTests(unittest.TestCase):
    def test_settings_preserve_profiles_and_clamp_timeout(self):
        settings = Settings.from_dict({
            "provider": "deepseek",
            "fallback_providers": ["qwen", "bad", "lmstudio", "gigachat"],
            "request_timeout_sec": 999,
            "tts_voice": "Microsoft Irina Desktop",
            "provider_profiles": {
                "deepseek": {
                    "model": "deepseek-flash",
                    "endpoint": "https://api.deepseek.com/chat/completions",
                    "vision": False,
                }
            },
        })
        self.assertEqual(settings.provider, "deepseek")
        self.assertEqual(settings.fallback_providers, ["qwen", "none", "lmstudio"])
        self.assertEqual(settings.request_timeout_sec, 120.0)
        self.assertEqual(settings.profile("deepseek")["model"], "deepseek-flash")
        self.assertEqual(settings.tts_voice, "Microsoft Irina Desktop")

    def test_tts_voice_is_selected_by_name(self):
        self.assertIn("FHD_BIP_TTS_VOICE", WindowsSapiTTS._SCRIPT)
        self.assertIn("SelectVoice", WindowsSapiTTS._SCRIPT)

    def test_gpt_voice_is_selected_for_openai_tts(self):
        callbacks = ProviderCallbacks(lambda audio: self.assertEqual(audio, b"pcm"), lambda _r, _t: None, lambda _s: None)
        provider = OpenAIGPTProvider(
            provider_id="openai", api_key="secret", model="gpt-4o-mini",
            endpoint="https://api.openai.com/v1/chat/completions", system_prompt="system",
            callbacks=callbacks, voice="marin",
        )
        payloads = []
        provider._post_json = lambda payload: payloads.append(payload) or {
            "choices": [{"message": {"role": "assistant", "content": "Проверка GPT."}}]
        }
        speech_payloads = []
        provider._synthesize = lambda text: speech_payloads.append((text, provider.voice)) or b"pcm"
        provider._ready = True
        asyncio.run(provider.send_text("Скажи тест"))
        self.assertEqual(speech_payloads, [("Проверка GPT.", "marin")])

    def test_remote_plain_http_endpoint_is_rejected(self):
        callbacks = ProviderCallbacks(lambda _b: None, lambda _r, _t: None, lambda _s: None)
        provider = OpenAICompatibleProvider(
            "openai_compatible", "secret", "model", "http://example.com/v1/chat/completions",
            "system", callbacks,
        )
        with self.assertRaisesRegex(RuntimeError, "HTTPS"):
            asyncio.run(provider.connect())

    def test_gigachat_uses_functions_dialect(self):
        callbacks = ProviderCallbacks(lambda _b: None, lambda _r, _t: None, lambda _s: None)
        context = AuthoritativeGameContext()
        context.update({"type": "state", "fuel": 42})
        registry = GameToolRegistry(context)
        provider = OpenAICompatibleProvider(
            "gigachat", "token", "GigaChat-2-Max",
            "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
            "system", callbacks, tool_declarations=registry.declarations(), tool_handler=registry.call,
        )
        payloads = []

        def fake_post(payload):
            payloads.append(payload)
            if len(payloads) == 1:
                return {"choices": [{"message": {
                    "role": "assistant", "content": "",
                    "function_call": {"name": "get_ship_fuel", "arguments": {}},
                }}]}
            return {"choices": [{"message": {"role": "assistant", "content": "Топливо: 42."}}]}

        provider._post_json = fake_post
        answer = provider._complete("Сколько топлива?")
        self.assertEqual(answer, "Топливо: 42.")
        self.assertIn("functions", payloads[0])
        self.assertNotIn("tools", payloads[0])
        self.assertTrue(any(item.get("role") == "function" for item in payloads[1]["messages"]))

    def test_openai_tool_call_queues_validated_autopilot_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            callbacks = ProviderCallbacks(lambda _b: None, lambda _r, _t: None, lambda _s: None)
            context = AuthoritativeGameContext()
            context.update({
                "type": "capabilities",
                "integrations": ["FHD_Autopilot"],
                "known_systems": [{"id": 23, "name": "Альтаир", "open": True}],
            })
            bridge = CommandBridge(Path(tmp) / "commands.jsonl")
            registry = GameToolRegistry(context, bridge)
            provider = OpenAICompatibleProvider(
                "openai_compatible", "token", "model", "https://example.test/v1/chat/completions",
                "system", callbacks, tool_declarations=registry.declarations(), tool_handler=registry.call,
            )
            payloads = []

            def fake_post(payload):
                payloads.append(payload)
                if len(payloads) == 1:
                    return {"choices": [{"message": {
                        "role": "assistant", "content": None,
                        "tool_calls": [{
                            "id": "call-route", "type": "function",
                            "function": {
                                "name": "start_autopilot_route",
                                "arguments": json.dumps({"system_name": "Альтаир"}, ensure_ascii=False),
                            },
                        }],
                    }}]}
                return {"choices": [{"message": {"role": "assistant", "content": "Команда передана игре."}}]}

            provider._post_json = fake_post
            self.assertEqual(provider._complete("Летим в Альтаир"), "Команда передана игре.")
            command = json.loads(bridge.path.read_text(encoding="utf-8").strip())
            self.assertEqual(command["action"], "autopilot.route_and_start")
            self.assertTrue(any(item.get("role") == "tool" for item in payloads[1]["messages"]))

    def test_real_http_tool_call_and_fallback_to_lmstudio(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):
                body = b'{"data":[]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                requests.append(payload)
                if self.path.startswith("/bad/"):
                    body = b'{"error":"temporary outage"}'
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                has_tool_result = any(item.get("role") == "tool" for item in payload.get("messages", []))
                if has_tool_result:
                    message = {"role": "assistant", "content": "Точные данные о топливе не получены."}
                else:
                    message = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call-1", "type": "function",
                            "function": {"name": "get_ship_fuel", "arguments": "{}"},
                        }],
                    }
                body = json.dumps({"choices": [{"message": message}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            settings = Settings(provider="openai_compatible", fallback_providers=["lmstudio", "none", "none"])
            settings.provider_profiles["openai_compatible"] = {
                "model": "broken-model",
                "endpoint": f"http://127.0.0.1:{server.server_port}/bad/chat/completions",
                "vision": False,
            }
            settings.provider_profiles["lmstudio"] = {
                "model": "test-model",
                "endpoint": f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                "vision": False,
            }
            heard = []
            callbacks = ProviderCallbacks(lambda _b: None, lambda role, text: heard.append((role, text)), lambda _s: None)
            context = AuthoritativeGameContext()
            tools = GameToolRegistry(context)
            manager = ProviderManager(
                settings, "system", callbacks, tools.declarations(), tools.call,
                lambda provider: "test-token" if provider == "openai_compatible" else None,
            )

            async def run_test():
                await manager.connect()
                self.assertEqual(manager.active_provider, "openai_compatible")
                self.assertFalse(manager.may_use_audio_input)
                await manager.send_text("Сколько топлива?")
                self.assertEqual(manager.active_provider, "lmstudio")
                self.assertEqual(manager.fallback_provider, "lmstudio")
                await manager.close(notify=False)

            asyncio.run(run_test())
            self.assertEqual(heard[-1], ("assistant", "Точные данные о топливе не получены."))
            self.assertEqual(len(requests), 3)
            self.assertIn("tools", requests[1])
            self.assertEqual(tools.last_tool, "get_ship_fuel")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
