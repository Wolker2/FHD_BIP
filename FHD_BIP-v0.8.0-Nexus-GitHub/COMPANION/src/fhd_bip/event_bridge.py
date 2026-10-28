from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable

from .config import app_dir

MAX_EVENT_BYTES = 64 * 1024


class EventBridge:
    def __init__(self, path: Path | None = None):
        self.path = path or app_dir() / "bridge" / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._stop = asyncio.Event()

    async def run(self, callback: Callable[[dict], Awaitable[None]], poll_sec: float = 0.25) -> None:
        self._stop.clear()
        pos = self.path.stat().st_size
        while not self._stop.is_set():
            try:
                size = self.path.stat().st_size
                if size < pos:
                    pos = 0
                if size > pos:
                    with self.path.open("rb") as f:
                        f.seek(pos)
                        while True:
                            line_start = f.tell()
                            line = f.readline(MAX_EVENT_BYTES + 1)
                            if not line:
                                break
                            # The native bridge normally appends one complete line in a
                            # single write.  Still, don't lose a JSON event if another
                            # writer is interrupted between the payload and newline.
                            if not line.endswith(b"\n") and len(line) <= MAX_EVENT_BYTES:
                                pos = line_start
                                break
                            pos = f.tell()
                            if len(line) > MAX_EVENT_BYTES:
                                continue
                            try:
                                obj = json.loads(line.decode("utf-8").strip())
                            except Exception:
                                continue
                            if isinstance(obj, dict):
                                await callback(obj)
            except FileNotFoundError:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.touch(exist_ok=True)
                pos = 0
            await asyncio.sleep(poll_sec)

    def stop(self) -> None:
        self._stop.set()
