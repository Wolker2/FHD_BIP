from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _text(value: Any, limit: int = 160) -> str:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else ""


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _value(event: dict[str, Any], nested: dict[str, Any], key: str) -> tuple[bool, Any]:
    if key in event:
        return True, event[key]
    if key in nested:
        return True, nested[key]
    return False, None


def _record_list(value: Any, allowed_keys: tuple[str, ...], limit: int = 64) -> list[dict[str, Any]]:
    """Keep only bounded, JSON-safe records supplied by the game bridge."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value[:limit]:
        if isinstance(item, str):
            name = _text(item)
            if name:
                out.append({"name": name})
            continue
        if not isinstance(item, dict):
            continue
        clean: dict[str, Any] = {}
        for key in allowed_keys:
            raw = item.get(key)
            if isinstance(raw, bool):
                clean[key] = raw
            elif isinstance(raw, (int, float)):
                clean[key] = raw
            else:
                text = _text(raw, 320 if key in {"text", "description"} else 160)
                if text:
                    clean[key] = text
        if clean:
            out.append(clean)
    return out


def _text_list(value: Any, limit: int = 16) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[:limit]:
        text = _text(item, 80)
        if text and text not in out:
            out.append(text)
    return out


LOCATION_KEYS = ("id", "name", "type", "race", "owner", "status", "distance")
EQUIPMENT_KEYS = ("id", "name", "type", "status", "condition", "condition_percent", "damage", "damage_percent", "slot")
QUEST_KEYS = ("id", "name", "status", "deadline", "days_left", "giver", "target", "description", "reward")
MESSAGE_KEYS = ("id", "text", "kind", "severity", "source", "date", "important")
SYSTEM_KEYS = ("id", "name", "status", "owner", "open", "known", "x", "y")
TRADE_GOOD_KEYS = ("id", "name", "type", "known")


@dataclass
class AuthoritativeGameContext:
    """Exact game facts received from FHD_BIP's own game bridge.

    Missing values stay missing. Screen vision never mutates this state and
    therefore cannot turn a guess into an authoritative game fact.
    """

    system: str = ""
    system_id: int | str | None = None
    planet: str = ""
    station: str = ""
    form: str = ""
    map_x: int | float | None = None
    map_y: int | float | None = None
    planets: list[dict[str, Any]] = field(default_factory=list)
    stations: list[dict[str, Any]] = field(default_factory=list)
    ship_status: dict[str, Any] = field(default_factory=dict)
    fuel: int | float | None = None
    hull_percent: int | float | None = None
    credits: int | float | None = None
    game_date: str = ""
    equipment: list[dict[str, Any]] = field(default_factory=list)
    quests: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    nearby_objects: list[dict[str, Any]] = field(default_factory=list)
    known_systems: list[dict[str, Any]] = field(default_factory=list)
    trade_goods: list[dict[str, Any]] = field(default_factory=list)
    integrations: list[str] = field(default_factory=list)
    revision: int = 0
    events_received: int = 0

    def update(self, event: dict[str, Any]) -> bool:
        if not isinstance(event, dict):
            return False
        before = self.snapshot()
        event_type = _text(event.get("type"), 40).lower()
        nested = event.get("context") if isinstance(event.get("context"), dict) else {}

        system_present, system_raw = _value(event, nested, "system")
        system = _text(system_raw)
        if system and system != self.system:
            self.system = system
            self.system_id = None
            self.planet = ""
            self.station = ""
            self.map_x = None
            self.map_y = None
            self.planets = []
            self.stations = []
            self.nearby_objects = []
        elif system_present and not system:
            self.system = ""
            self.system_id = None
            self.planet = ""
            self.station = ""
            self.map_x = None
            self.map_y = None
            self.planets = []
            self.stations = []
            self.nearby_objects = []
        system_id_present, system_id = _value(event, nested, "system_id")
        if isinstance(system_id, (int, str)) and not isinstance(system_id, bool) and str(system_id).strip():
            self.system_id = system_id
        elif system_id_present:
            self.system_id = None

        planet = _text(event.get("planet") or nested.get("planet"))
        station = _text(event.get("station") or nested.get("station"))
        form = _text(event.get("form") or nested.get("form"))
        if planet or event_type in {"location", "state", "system_context"}:
            self.planet = planet
        if station or event_type in {"location", "state", "system_context"}:
            self.station = station
        if form:
            self.form = form

        map_data = event.get("map_position") or nested.get("map_position")
        if not isinstance(map_data, dict):
            map_data = {}
        map_x_present = "map_x" in event or "map_x" in nested or "x" in map_data
        map_y_present = "map_y" in event or "map_y" in nested or "y" in map_data
        map_x = _number(event.get("map_x", nested.get("map_x", map_data.get("x"))))
        map_y = _number(event.get("map_y", nested.get("map_y", map_data.get("y"))))
        if map_x_present:
            self.map_x = map_x
        if map_y_present:
            self.map_y = map_y

        planets_value = event.get("planets", nested.get("planets"))
        stations_value = event.get("stations", nested.get("stations"))
        nearby_value = event.get("nearby_objects", nested.get("nearby_objects"))
        if "planets" in event or "planets" in nested:
            self.planets = _record_list(planets_value, LOCATION_KEYS)
        if "stations" in event or "stations" in nested:
            self.stations = _record_list(stations_value, LOCATION_KEYS)
        if "nearby_objects" in event or "nearby_objects" in nested:
            self.nearby_objects = _record_list(nearby_value, LOCATION_KEYS)

        ship = event.get("ship_status", nested.get("ship_status"))
        if isinstance(ship, dict):
            clean_ship: dict[str, Any] = {}
            for key in ("name", "type", "status", "cargo", "cargo_capacity", "speed", "mass"):
                raw = ship.get(key)
                if isinstance(raw, (bool, int, float)):
                    clean_ship[key] = raw
                else:
                    text = _text(raw)
                    if text:
                        clean_ship[key] = text
            self.ship_status = clean_ship
        elif "ship_status" in event or "ship_status" in nested:
            self.ship_status = {}

        fuel_present, fuel_raw = _value(event, nested, "fuel")
        hull_present, hull_raw = _value(event, nested, "hull_percent")
        credits_present, credits_raw = _value(event, nested, "credits")
        date_present, date_raw = _value(event, nested, "game_date")
        if not date_present and "date" in event:
            date_present, date_raw = True, event.get("date")
        fuel = _number(fuel_raw)
        hull = _number(hull_raw)
        credits = _number(credits_raw)
        date = _text(date_raw)
        if fuel_present:
            self.fuel = fuel
        if hull_present:
            self.hull_percent = hull
        if credits_present:
            self.credits = credits
        if date_present:
            self.game_date = date

        equipment_value = event.get("equipment", nested.get("equipment"))
        quests_value = event.get("quests", nested.get("quests"))
        messages_value = event.get("messages", nested.get("messages"))
        if "equipment" in event or "equipment" in nested:
            self.equipment = _record_list(equipment_value, EQUIPMENT_KEYS)
        if "quests" in event or "quests" in nested:
            self.quests = _record_list(quests_value, QUEST_KEYS)
        if "messages" in event or "messages" in nested:
            self.messages = _record_list(messages_value, MESSAGE_KEYS, limit=32)
        if event_type in {"game_message", "message"}:
            single = _record_list([event], MESSAGE_KEYS, limit=1)
            if single:
                self.messages = (self.messages + single)[-32:]

        systems_value = event.get("known_systems", nested.get("known_systems"))
        goods_value = event.get("trade_goods", nested.get("trade_goods"))
        integrations_value = event.get("integrations", nested.get("integrations"))
        if "known_systems" in event or "known_systems" in nested:
            self.known_systems = _record_list(systems_value, SYSTEM_KEYS, limit=512)
        if "trade_goods" in event or "trade_goods" in nested:
            self.trade_goods = _record_list(goods_value, TRADE_GOOD_KEYS, limit=128)
        if "integrations" in event or "integrations" in nested:
            self.integrations = _text_list(integrations_value)

        after = self.snapshot()
        self.events_received += 1
        if after != before:
            self.revision += 1
            return True
        return False

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {"source": "FHD_BIP_game_bridge", "authority": "exact_game_data"}
        if self.system:
            out["system"] = self.system
        if self.system_id is not None:
            out["system_id"] = self.system_id
        if self.planet:
            out["planet"] = self.planet
        if self.station:
            out["station"] = self.station
        if self.form:
            out["form"] = self.form
        if self.map_x is not None and self.map_y is not None:
            out["galaxy_map_position"] = {"x": self.map_x, "y": self.map_y}
        if self.planets:
            out["planets_in_current_system"] = self.planets
        if self.stations:
            out["stations_in_current_system"] = self.stations
        if self.ship_status:
            out["ship_status"] = self.ship_status
        if self.fuel is not None:
            out["fuel"] = self.fuel
        if self.hull_percent is not None:
            out["hull_percent"] = self.hull_percent
        if self.credits is not None:
            out["credits"] = self.credits
        if self.game_date:
            out["game_date"] = self.game_date
        if self.equipment:
            out["equipment"] = self.equipment
        if self.quests:
            out["active_quests"] = self.quests
        if self.messages:
            out["game_messages"] = self.messages
        if self.nearby_objects:
            out["nearby_objects"] = self.nearby_objects
        if self.known_systems:
            out["known_systems"] = self.known_systems
        if self.trade_goods:
            out["trade_goods"] = self.trade_goods
        if self.integrations:
            out["integrations"] = self.integrations
        return out

    def has_location(self) -> bool:
        return bool(self.system or self.planet or self.station)

    def as_model_message(self) -> str:
        payload = self.snapshot()
        payload["revision"] = self.revision
        return "[GAME_CONTEXT] " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
