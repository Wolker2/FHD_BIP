from __future__ import annotations

from typing import Any

from .command_bridge import CommandBridge
from .game_context import AuthoritativeGameContext


TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {"name": "get_current_system", "description": "Точная текущая система игрока и её ID из игрового моста."},
    {"name": "get_current_planet", "description": "Точная текущая планета или станция игрока из игрового моста."},
    {"name": "get_known_planets", "description": "Точный известный список планет текущей системы."},
    {"name": "get_galaxy_map_position", "description": "Точные координаты игрока на галактической карте."},
    {"name": "get_ship_status", "description": "Точное состояние корабля и корпуса."},
    {"name": "get_ship_fuel", "description": "Точное количество топлива, переданное игрой."},
    {"name": "get_equipment_status", "description": "Точное установленное оборудование и его повреждения."},
    {"name": "get_current_quests", "description": "Точные активные задания и сроки."},
    {
        "name": "get_game_messages",
        "description": "Последние точные системные сообщения игры.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"limit": {"type": "INTEGER", "description": "От 1 до 20 последних сообщений."}},
        },
    },
    {"name": "get_player_credits", "description": "Точное количество денег игрока."},
    {"name": "get_nearby_objects", "description": "Точные ближайшие корабли и объекты, если мост их передал."},
    {
        "name": "open_galaxy_map",
        "description": "Открыть галактическую карту. Возвращает queued; завершение подтверждает только command_result.",
    },
    {
        "name": "find_system_on_map",
        "description": "Найти и показать на галактической карте точную известную систему.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "system_name": {"type": "STRING", "description": "Точное название известной системы."},
            },
            "required": ["system_name"],
        },
    },
    {
        "name": "start_autopilot_route",
        "description": (
            "Открыть карту, найти точную известную систему, проложить к ней маршрут и запустить FHD_Autopilot."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "system_name": {"type": "STRING", "description": "Точное название известной системы назначения."},
            },
            "required": ["system_name"],
        },
    },
    {
        "name": "search_trade_good_and_mark",
        "description": (
            "Найти точный товар через FHD_Trade и поставить его собственную торговую метку, не затрагивая чужие метки."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "good_name": {"type": "STRING", "description": "Точное название товара из каталога FHD_Trade."},
            },
            "required": ["good_name"],
        },
    },
]


class GameToolRegistry:
    """Read exact game data and queue a small allowlist of validated actions."""

    def __init__(self, context: AuthoritativeGameContext, command_bridge: CommandBridge | None = None):
        self.context = context
        self.command_bridge = command_bridge
        self.calls = 0
        self.last_tool = ""

    @property
    def count(self) -> int:
        return len(TOOL_DECLARATIONS)

    def declarations(self) -> list[dict[str, Any]]:
        return TOOL_DECLARATIONS

    @staticmethod
    def _result(value: Any, known: bool | None = None) -> dict[str, Any]:
        if known is None:
            known = value is not None and value != "" and value != [] and value != {}
        return {
            "known": bool(known),
            "value": value if known else None,
            "source": "FHD_BIP_game_bridge",
            "instruction": "Не додумывать значение" if not known else "Точные данные игры",
        }

    @staticmethod
    def _normalize_name(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(value.casefold().replace("ё", "е").split())[:160]

    @staticmethod
    def _action_error(error: str, **extra: Any) -> dict[str, Any]:
        result = {
            "accepted": False,
            "state": "rejected",
            "error": error,
            "source": "FHD_BIP_action_validator",
            "instruction": "Действие не выполнено; не придумывать успешный результат.",
        }
        result.update(extra)
        return result

    def _has_integration(self, name: str) -> bool:
        expected = self._normalize_name(name)
        return any(self._normalize_name(item) == expected for item in self.context.integrations)

    def _find_exact(self, records: list[dict[str, Any]], requested: Any) -> tuple[dict[str, Any] | None, list[str]]:
        needle = self._normalize_name(requested)
        if not needle:
            return None, []
        matches = [item for item in records if self._normalize_name(item.get("name")) == needle]
        if len(matches) == 1:
            return matches[0], []
        candidates = [
            str(item.get("name"))
            for item in records
            if item.get("name") and needle in self._normalize_name(item.get("name"))
        ][:8]
        return None, candidates

    def _submit(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.command_bridge is None:
            return self._action_error("command_bridge_unavailable")
        result = self.command_bridge.submit(action, payload)
        if result.get("accepted"):
            result["target"] = payload
        return result

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls += 1
        self.last_tool = name
        args = arguments if isinstance(arguments, dict) else {}
        c = self.context
        if name == "get_current_system":
            return self._result({"name": c.system, "id": c.system_id} if c.system else None)
        if name == "get_current_planet":
            value = {"planet": c.planet or None, "station": c.station or None} if c.planet or c.station else None
            return self._result(value)
        if name == "get_known_planets":
            return self._result(c.planets)
        if name == "get_galaxy_map_position":
            value = {"x": c.map_x, "y": c.map_y} if c.map_x is not None and c.map_y is not None else None
            return self._result(value)
        if name == "get_ship_status":
            value = dict(c.ship_status)
            if c.hull_percent is not None:
                value["hull_percent"] = c.hull_percent
            return self._result(value)
        if name == "get_ship_fuel":
            return self._result(c.fuel)
        if name == "get_equipment_status":
            return self._result(c.equipment)
        if name == "get_current_quests":
            return self._result(c.quests)
        if name == "get_game_messages":
            raw_limit = args.get("limit", 10)
            limit = raw_limit if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) else 10
            return self._result(c.messages[-max(1, min(20, limit)):])
        if name == "get_player_credits":
            return self._result(c.credits)
        if name == "get_nearby_objects":
            return self._result(c.nearby_objects)
        if name == "open_galaxy_map":
            return self._submit("galaxy_map.open", {})
        if name in {"find_system_on_map", "start_autopilot_route"}:
            target, candidates = self._find_exact(c.known_systems, args.get("system_name"))
            if target is None:
                error = "system_catalog_unavailable" if not c.known_systems else "unknown_system"
                return self._action_error(error, candidates=candidates)
            if target.get("known") is False or target.get("open") is False:
                return self._action_error("system_not_available", system_name=target.get("name"))
            if target.get("id") is None:
                return self._action_error("system_id_unavailable", system_name=target.get("name"))
            payload = {"system_id": target.get("id"), "system_name": target.get("name")}
            if name == "find_system_on_map":
                return self._submit("galaxy_map.focus_system", payload)
            if not self._has_integration("FHD_Autopilot"):
                return self._action_error("integration_unavailable", integration="FHD_Autopilot")
            payload.update({
                "integration": "FHD_Autopilot",
                "open_map": True,
                "focus_system": True,
            })
            return self._submit("autopilot.route_and_start", payload)
        if name == "search_trade_good_and_mark":
            if not self._has_integration("FHD_Trade"):
                return self._action_error("integration_unavailable", integration="FHD_Trade")
            good, candidates = self._find_exact(c.trade_goods, args.get("good_name"))
            if good is None:
                error = "trade_catalog_unavailable" if not c.trade_goods else "unknown_good"
                return self._action_error(error, candidates=candidates)
            if good.get("known") is False:
                return self._action_error("good_not_available", good_name=good.get("name"))
            if good.get("id") is None:
                return self._action_error("good_id_unavailable", good_name=good.get("name"))
            return self._submit("trade.search_and_mark", {
                "good_id": good.get("id"),
                "good_name": good.get("name"),
                "integration": "FHD_Trade",
                "marker_owner": "FHD_Trade",
                "preserve_foreign_markers": True,
            })
        return {"known": False, "value": None, "source": "FHD_BIP_tool_registry", "error": "unknown_tool"}
