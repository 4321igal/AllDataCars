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

from config import DB_PATH, HISTORY_DIR

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
    first run). We detect it two ways: the ``vehicles`` table must have a primary
    key, and the ``messages -> vehicles`` foreign key must actually resolve. The
    second check is a throwaway INSERT that is always rolled back, so it never
    persists anything.
    """
    conn = _connect()
    try:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "vehicles" not in tables:
            return True  # nothing (or partial) yet — executescript will build it

        info = conn.execute("PRAGMA table_info(vehicles)").fetchall()
        if not any(r["pk"] for r in info):
            return False

        if "messages" in tables:
            try:
                conn.execute(
                    "INSERT INTO messages (vehicle_id, user_id, role, content, created_at) "
                    "VALUES (NULL, -1, '_probe_', '_probe_', ?)",
                    (_now(),),
                )
            except sqlite3.OperationalError as exc:
                if "foreign key mismatch" in str(exc).lower():
                    return False
                raise
            finally:
                conn.rollback()  # never persist the probe row
        return True
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.rollback()
        conn.close()


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


def get_fsm_dirs(vehicle_id: int) -> list[str]:
    """Local FSM directory paths (kind='dir') registered for a vehicle."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT local_path FROM fsm_docs WHERE vehicle_id = ? AND kind = 'dir'",
            (vehicle_id,),
        ).fetchall()
    return [r["local_path"] for r in rows if r["local_path"]]


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
def _history_filename(vehicle: "dict | None") -> str:
    """Return a history filename based on today's date and the vehicle name."""
    import re
    date = _now()[:10]  # YYYY-MM-DD
    if vehicle and vehicle.get("name"):
        safe = re.sub(r"[^\w֐-׿]+", "_", vehicle["name"]).strip("_")
        return f"history_{date}_{safe}.md"
    return f"history_{date}.md"


def append_history_file(
    user_id: int,
    role: str,
    content: str,
    token_info: dict | None = None,
    vehicle: "dict | None" = None,
) -> None:
    """Append a message to the user's history file.

    Files are split per day and per vehicle: History/<user_id>/history_YYYY-MM-DD[_<vehicle>].md
    """
    folder = HISTORY_DIR / str(user_id)
    folder.mkdir(parents=True, exist_ok=True)
    ts = _now().replace("T", " ").split(".")[0] + " UTC"
    label = "משתמש" if role == "user" else "בוט"
    entry = f"### {ts}\n\n**{label}:** {content}\n"
    if token_info:
        entry += (
            f"\n> טוקנים: input={token_info['input_tokens']} "
            f"cache_read={token_info['cache_read_tokens']} "
            f"cache_create={token_info['cache_create_tokens']} "
            f"output={token_info['output_tokens']} | "
            f"${token_info['cost_usd']:.6f}\n"
        )
    entry += "\n---\n\n"
    filename = _history_filename(vehicle)
    with open(folder / filename, "a", encoding="utf-8") as f:
        f.write(entry)


def save_pdf_to_history(user_id: int, src_path: "Path", filename: str) -> "Path":
    """Copy a PDF sent by the user into History/<user_id>/pdfs/. Returns the dest path."""
    import shutil
    folder = HISTORY_DIR / str(user_id) / "pdfs"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / filename
    shutil.copy2(str(src_path), str(dest))
    return dest


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
