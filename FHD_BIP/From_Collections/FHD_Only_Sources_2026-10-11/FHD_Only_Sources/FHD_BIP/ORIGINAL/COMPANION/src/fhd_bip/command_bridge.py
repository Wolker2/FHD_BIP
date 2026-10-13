from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import app_dir

MAX_COMMAND_BYTES = 16 * 1024
MAX_QUEUE_BYTES = 1024 * 1024
ALLOWED_ACTIONS = frozenset({
    "galaxy_map.open",
    "galaxy_map.focus_system",
    "autopilot.route_and_start",
    "trade.search_and_mark",
})


class CommandBridge:
    """Write allowlisted game commands to the local FHD_BIP bridge queue.

    A queued command is not treated as completed.  The in-game integration must
    execute it and append a matching ``command_result`` event to events.jsonl.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or app_dir() / "bridge" / "commands.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._lock = threading.Lock()
        self.submitted = 0
        self.last_command_id = ""
        self.last_action = ""
        self.last_result: dict[str, Any] = {}
        self._pending_ids: set[str] = set()

    def submit(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if action not in ALLOWED_ACTIONS:
            return self._rejected("action_not_allowed")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return self._rejected("invalid_payload")

        command_id = uuid.uuid4().hex
        command = {
            "type": "command",
            "schema": "fhd_bip.command.v1",
            "command_id": command_id,
            "action": action,
            "payload": payload,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }
        try:
            encoded = (json.dumps(command, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        except (TypeError, ValueError):
            return self._rejected("invalid_payload")
        if len(encoded) > MAX_COMMAND_BYTES:
            return self._rejected("command_too_large")

        with self._lock:
            try:
                if self.path.stat().st_size > MAX_QUEUE_BYTES:
                    self.path.write_bytes(b"")
                descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
                try:
                    os.write(descriptor, encoded)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError as exc:
                return self._rejected("queue_write_failed", detail=str(exc)[:240])

        with self._lock:
            self.submitted += 1
            self.last_command_id = command_id
            self.last_action = action
            self._pending_ids.add(command_id)
        return {
            "accepted": True,
            "state": "queued",
            "command_id": command_id,
            "action": action,
            "source": "FHD_BIP_command_bridge",
            "instruction": (
                "Команда только поставлена в очередь. Не сообщать об успешном выполнении "
                "до события command_result с тем же command_id и status=completed."
            ),
        }

    @staticmethod
    def _rejected(error: str, detail: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "accepted": False,
            "state": "rejected",
            "error": error,
            "source": "FHD_BIP_command_bridge",
            "instruction": "Действие не выполнено; сообщить причину без выдумывания результата.",
        }
        if detail:
            result["detail"] = detail
        return result

    def record_result(self, event: dict[str, Any]) -> bool:
        if not isinstance(event, dict) or event.get("type") != "command_result":
            return False
        command_id = event.get("command_id")
        status = event.get("status")
        if status not in {"accepted", "running", "completed", "failed", "rejected"}:
            return False
        clean: dict[str, Any] = {"command_id": command_id, "status": status}
        action = event.get("action")
        message = event.get("message")
        if isinstance(action, str) and action in ALLOWED_ACTIONS:
            clean["action"] = action
        if isinstance(message, str) and message.strip():
            clean["message"] = message.strip()[:320]
        with self._lock:
            if not isinstance(command_id, str) or command_id not in self._pending_ids:
                return False
            self.last_result = clean
            if status in {"completed", "failed", "rejected"}:
                self._pending_ids.discard(command_id)
        return True

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._pending_ids)
