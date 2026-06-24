"""Configuration loaded from environment / .env file."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Claude Code CLI — authenticated with the user's Max subscription.
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "sonnet").strip()
CLAUDE_TIMEOUT = int(os.getenv("CLAUDE_TIMEOUT", "240"))

# Storage
DB_PATH = (BASE_DIR / os.getenv("DB_PATH", "data/fleet.db")).resolve()
FSM_DIR = (BASE_DIR / os.getenv("FSM_DIR", "fsm_files")).resolve()
HISTORY_DIR = (BASE_DIR / os.getenv("HISTORY_DIR", "History")).resolve()


def _parse_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                ids.add(int(part))
            except ValueError:
                pass
    return ids


# Empty set => everyone is allowed.
ALLOWED_USER_IDS: set[int] = _parse_ids(os.getenv("ALLOWED_USER_IDS", ""))


def ensure_dirs() -> None:
    """Create storage directories if they do not exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    FSM_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def validate() -> None:
    """Fail fast with a clear message if required config is missing."""
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in."
        )
