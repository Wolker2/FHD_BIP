from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProactiveDecision:
    speak: bool
    priority: str = "normal"
    reason: str = ""


class ProactiveCommentManager:
    """Select important real events without turning every bridge line into speech."""

    GLOBAL_COOLDOWN_SEC = 18.0
    DUPLICATE_COOLDOWN_SEC = 180.0

    def __init__(self):
        self._last_spoken_at = 0.0
        self._fingerprints: dict[str, float] = {}

    def consider(self, event: dict[str, Any], now: float | None = None) -> ProactiveDecision:
        if not isinstance(event, dict):
            return ProactiveDecision(False)
        clock = time.monotonic() if now is None else now
        event_type = str(event.get("type") or "").strip().lower()
        severity = str(event.get("severity") or "").strip().lower()
        status = str(event.get("status") or "").strip().lower()
        reason = ""
        priority = "normal"

        fuel = event.get("fuel")
        hull = event.get("hull_percent")
        if isinstance(fuel, (int, float)) and not isinstance(fuel, bool) and fuel <= 15:
            reason, priority = "low_fuel", "critical"
        elif isinstance(hull, (int, float)) and not isinstance(hull, bool) and hull <= 25:
            reason, priority = "critical_hull", "critical"
        elif event_type == "death":
            reason, priority = "death", "critical"
        elif severity in {"critical", "emergency", "danger"}:
            reason, priority = f"severity_{severity}", "critical"
        elif event_type == "combat" and status in {"start", "started", "attack", "danger"}:
            reason, priority = "combat", "high"
        elif event_type == "quest" and status in {"accepted", "completed", "failed", "expired"}:
            reason, priority = f"quest_{status}", "high"
        elif event_type == "equipment" and status in {"broken", "damaged", "repaired"}:
            reason, priority = f"equipment_{status}", "high"
        elif event_type in {"message", "game_message"} and bool(event.get("important")):
            reason, priority = "important_message", "high"
        elif event_type == "location" and event.get("system"):
            reason, priority = "new_location", "normal"
        else:
            return ProactiveDecision(False)

        fingerprint = json.dumps(
            {
                "type": event_type, "reason": reason, "id": event.get("id"),
                "status": status, "text": event.get("text"),
                "system": event.get("system"), "planet": event.get("planet"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )[:800]
        last_same = self._fingerprints.get(fingerprint, 0.0)
        if clock - last_same < self.DUPLICATE_COOLDOWN_SEC:
            return ProactiveDecision(False)
        if priority != "critical" and clock - self._last_spoken_at < self.GLOBAL_COOLDOWN_SEC:
            return ProactiveDecision(False)
        self._fingerprints[fingerprint] = clock
        self._last_spoken_at = clock
        if len(self._fingerprints) > 256:
            cutoff = clock - self.DUPLICATE_COOLDOWN_SEC
            self._fingerprints = {key: value for key, value in self._fingerprints.items() if value >= cutoff}
        return ProactiveDecision(True, priority, reason)
