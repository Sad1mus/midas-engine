"""Estado persistente del kill switch.

Cuando se dispara, TODAS las señales se vetan hasta que un humano lo desactive.
El estado se guarda en SQLite para que sobreviva reinicios del proceso.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import structlog

from core.config import settings

logger = structlog.get_logger(__name__)


class KillSwitch:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or settings.sqlite_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kill_switch (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    triggered INTEGER NOT NULL DEFAULT 0,
                    reason TEXT,
                    triggered_at TEXT,
                    cleared_at TEXT
                )
                """
            )
            conn.execute("INSERT OR IGNORE INTO kill_switch (id, triggered) VALUES (1, 0)")

    def is_triggered(self) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute("SELECT triggered FROM kill_switch WHERE id = 1").fetchone()
        return bool(row[0]) if row else False

    def status(self) -> dict[str, str | bool | None]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT triggered, reason, triggered_at, cleared_at FROM kill_switch WHERE id = 1"
            ).fetchone()
        if not row:
            return {"triggered": False}
        return {
            "triggered": bool(row[0]),
            "reason": row[1],
            "triggered_at": row[2],
            "cleared_at": row[3],
        }

    def trigger(self, reason: str) -> None:
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE kill_switch SET triggered = 1, reason = ?, triggered_at = ? WHERE id = 1",
                (reason, now),
            )
        logger.error("kill_switch.triggered", reason=reason, at=now)

    def clear(self, cleared_by: str) -> None:
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE kill_switch SET triggered = 0, cleared_at = ?, reason = ? WHERE id = 1",
                (now, f"cleared by {cleared_by} at {now}"),
            )
        logger.warning("kill_switch.cleared", by=cleared_by, at=now)
