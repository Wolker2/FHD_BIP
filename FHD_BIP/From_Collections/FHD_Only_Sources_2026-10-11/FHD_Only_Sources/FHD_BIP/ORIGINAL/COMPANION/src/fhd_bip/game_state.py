from __future__ import annotations

import json
import math
from typing import Any

MAX_GAME_NAME_LENGTH = 120
MAX_SYSTEM_PLANETS = 64
STATE_EVENT_TYPES = {"state", "location", "system_snapshot"}


def _clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > MAX_GAME_NAME_LENGTH:
        return None
    return value


def _clean_coordinate(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return value


class ExactGameState:
    """Latest structured location received from the trusted local game bridge."""

    def __init__(self) -> None:
        self._current_system: str | None = None
        self._current_planet: str | None = None
        self._system_planets: list[str] = []
        self._map_x: int | float | None = None
        self._map_y: int | float | None = None

    def update(self, event: dict[str, Any]) -> bool:
        if event.get("type") not in STATE_EVENT_TYPES:
            return False
        before = self.snapshot()
        event_type = event["type"]
        system = _clean_name(event.get("system"))

        if system and system != self._current_system:
            self._current_system = system
            self._current_planet = None
            self._system_planets = []
            self._map_x = None
            self._map_y = None

        if event_type in {"state", "location"}:
            planet = _clean_name(event.get("planet"))
            if planet:
                self._current_planet = planet
            elif event_type == "location" and "planet" in event:
                self._current_planet = None

        if event_type == "system_snapshot" and system:
            planets = event.get("planets")
            if isinstance(planets, list):
                cleaned: list[str] = []
                for item in planets[:MAX_SYSTEM_PLANETS]:
                    name = _clean_name(item)
                    if name and name not in cleaned:
                        cleaned.append(name)
                self._system_planets = cleaned

        map_x = _clean_coordinate(event.get("map_x"))
        map_y = _clean_coordinate(event.get("map_y"))
        if map_x is not None and map_y is not None:
            self._map_x = map_x
            self._map_y = map_y

        return self.snapshot() != before

    def snapshot(self) -> dict[str, Any] | None:
        if not self._current_system:
            return None
        result: dict[str, Any] = {
            "telemetry": "verified_game_bridge",
            "current_system": self._current_system,
        }
        if self._current_planet:
            result["current_planet"] = self._current_planet
        if self._system_planets:
            result["current_system_planets"] = list(self._system_planets)
        if self._map_x is not None and self._map_y is not None:
            result["galaxy_map_position"] = {"x": self._map_x, "y": self._map_y}
        return result

    def prompt_context(self) -> str | None:
        snapshot = self.snapshot()
        if snapshot is None:
            return None
        compact = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        return f"[GAME_STATE_EXACT] {compact}"
