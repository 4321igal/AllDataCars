"""SQLite storage layer for the fleet agent.

Stores vehicles, their FSM (Factory Service Manual) docs, cached web resources
(repair videos / PDF manuals), per-user active-vehicle state, and a short
message history used for context.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import DB_PATH

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    make       TEXT,
    model      TEXT,
    year       TEXT,
    vin        TEXT,
    engine     TEXT,
    plate      TEXT,
    notes      TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fsm_docs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id   INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    kind         TEXT NOT NULL,           -- 'pdf' | 'note'
    source_url   TEXT,
    local_path   TEXT,
    content_text TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id  INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,            -- 'video' | 'pdf'
    topic       TEXT,
    title       TEXT,
    url         TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE(vehicle_id, url)
);

CREATE TABLE IF NOT EXISTS user_state (
    user_id           INTEGER PRIMARY KEY,
    active_vehicle_id INTEGER REFERENCES vehicles(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER REFERENCES vehicles(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL,
    role       TEXT NOT NULL,             -- 'user' | 'assistant'
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _schema_is_healthy() -> bool:
    """Return True if an existing DB has a usable schema.

    The 'foreign key mismatch' error appears when the parent ``vehicles`` table
    exists but lost its PRIMARY KEY (e.g. a partially-written DB from a crashed
    first run). Detect that so we can recreate the file instead of failing on
    every message.
    """
    try:
        with _connect() as conn:
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "vehicles" not in tables:
                return True  # nothing (or partial) yet — executescript will build it
            info = conn.execute("PRAGMA table_info(vehicles)").fetchall()
            # A healthy vehicles table has a primary-key column (pk > 0).
            return any(r["pk"] for r in info)
    except sqlite3.DatabaseError:
        return False


def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    if Path(DB_PATH).exists() and not _schema_is_healthy():
        backup = Path(f"{DB_PATH}.corrupt.bak")
        backup.unlink(missing_ok=True)
        Path(DB_PATH).rename(backup)
        logger.warning(
            "Existing database had a malformed schema; backed it up to %s and "
            "recreating a clean one.",
            backup,
        )

    with _connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Lightweight migrations: add columns introduced after the first release."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(vehicles)").fetchall()}
    if "plate" not in cols:
        conn.execute("ALTER TABLE vehicles ADD COLUMN plate TEXT")


# --------------------------------------------------------------------------- #
# Vehicles
# --------------------------------------------------------------------------- #
def add_vehicle(
    user_id: int,
    name: str,
    make: str = "",
    model: str = "",
    year: str = "",
    vin: str = "",
    engine: str = "",
    plate: str = "",
    notes: str = "",
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO vehicles
                   (user_id, name, make, model, year, vin, engine, plate, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, name, make, model, year, vin, engine, plate, notes, _now()),
        )
        return int(cur.lastrowid)


def find_vehicle_by_plate(user_id: int, plate: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM vehicles WHERE user_id = ? AND plate = ? ORDER BY id LIMIT 1",
            (user_id, plate),
        ).fetchone()
        return dict(row) if row else None


def list_vehicles(user_id: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM vehicles WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_vehicle(vehicle_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)
        ).fetchone()
        return dict(row) if row else None


def update_vehicle_notes(vehicle_id: int, notes: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE vehicles SET notes = ? WHERE id = ?", (notes, vehicle_id)
        )


# --------------------------------------------------------------------------- #
# Active-vehicle state
# --------------------------------------------------------------------------- #
def set_active_vehicle(user_id: int, vehicle_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO user_state (user_id, active_vehicle_id)
               VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET active_vehicle_id = excluded.active_vehicle_id""",
            (user_id, vehicle_id),
        )


def get_active_vehicle(user_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT active_vehicle_id FROM user_state WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row or row["active_vehicle_id"] is None:
        return None
    return get_vehicle(int(row["active_vehicle_id"]))


# --------------------------------------------------------------------------- #
# FSM docs
# --------------------------------------------------------------------------- #
def add_fsm_doc(
    vehicle_id: int,
    title: str,
    kind: str,
    source_url: str = "",
    local_path: str = "",
    content_text: str = "",
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO fsm_docs
                   (vehicle_id, title, kind, source_url, local_path, content_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (vehicle_id, title, kind, source_url, local_path, content_text, _now()),
        )
        return int(cur.lastrowid)


def get_fsm_docs(vehicle_id: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM fsm_docs WHERE vehicle_id = ? ORDER BY id", (vehicle_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Cached links (videos / PDFs)
# --------------------------------------------------------------------------- #
def upsert_link(
    vehicle_id: int,
    kind: str,
    url: str,
    title: str = "",
    description: str = "",
    topic: str = "",
) -> bool:
    """Insert a link, ignoring duplicates. Returns True if a new row was added."""
    if not url:
        return False
    with _connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO links
                   (vehicle_id, kind, topic, title, url, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (vehicle_id, kind, topic, title, url, description, _now()),
        )
        return cur.rowcount > 0


def get_cached_links(
    vehicle_id: int, kind: Optional[str] = None
) -> list[dict[str, Any]]:
    with _connect() as conn:
        if kind:
            rows = conn.execute(
                "SELECT * FROM links WHERE vehicle_id = ? AND kind = ? ORDER BY id DESC",
                (vehicle_id, kind),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM links WHERE vehicle_id = ? ORDER BY id DESC",
                (vehicle_id,),
            ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Message history
# --------------------------------------------------------------------------- #
def add_message(vehicle_id: Optional[int], user_id: int, role: str, content: str) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO messages (vehicle_id, user_id, role, content, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (vehicle_id, user_id, role, content, _now()),
        )


def recent_messages(vehicle_id: Optional[int], user_id: int, limit: int = 6) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT role, content FROM messages
               WHERE user_id = ? AND (vehicle_id IS ? OR vehicle_id = ?)
               ORDER BY id DESC LIMIT ?""",
            (user_id, vehicle_id, vehicle_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]
