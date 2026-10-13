from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .config import app_dir


class MemoryStore:
    def __init__(self, path: Path | None = None):
        self.path = path or app_dir() / "memory.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.RLock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS events(ts REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS transcripts(ts REAL NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS facts(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL)")
        self.db.commit()

    def add_event(self, payload: dict) -> None:
        with self._lock:
            self.db.execute("INSERT INTO events VALUES(?,?,?)", (time.time(), payload.get("type", "event"), json.dumps(payload, ensure_ascii=False)))
            self.db.commit()

    def add_transcript(self, role: str, text: str) -> None:
        if not text.strip():
            return
        with self._lock:
            self.db.execute("INSERT INTO transcripts VALUES(?,?,?)", (time.time(), role, text.strip()))
            self.db.commit()

    def set_fact(self, key: str, value: str) -> None:
        with self._lock:
            self.db.execute("INSERT INTO facts(key,value,updated) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated", (key, value, time.time()))
            self.db.commit()

    def summary(self, max_facts: int = 20) -> str:
        with self._lock:
            rows = self.db.execute("SELECT key,value FROM facts ORDER BY updated DESC LIMIT ?", (max_facts,)).fetchall()
        return "\n".join(f"- {k}: {v}" for k, v in rows)

    def close(self) -> None:
        with self._lock:
            self.db.close()
