"""Preflight checks for running AllDataCars on your own computer.

Run `python doctor.py` before `python bot.py`. It verifies Python, dependencies,
the Claude CLI + Max login, and the Telegram token, printing clear fix hints.
Exit code 0 means you're ready to run the bot.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys

OK = "✅"
BAD = "❌"


def check_python() -> bool:
    ok = sys.version_info >= (3, 10)
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    print(f"{OK if ok else BAD} Python {ver} {'(>= 3.10)' if ok else '— need >= 3.10'}")
    return ok


def check_packages() -> bool:
    ok = True
    for mod, hint in (
        ("telegram", "pip install -r requirements.txt"),
        ("pypdf", "pip install -r requirements.txt"),
        ("dotenv", "pip install -r requirements.txt"),
    ):
        try:
            __import__(mod)
            print(f"{OK} package '{mod}' importable")
        except Exception as exc:  # noqa: BLE001
            ok = False
            extra = ""
            if "_cffi_backend" in str(exc) or "cffi" in str(exc).lower():
                extra = "  → fix: pip install --upgrade cffi cryptography"
            print(f"{BAD} package '{mod}' failed to import: {exc}{extra or '  → ' + hint}")
    return ok


def check_claude_cli() -> bool:
    from config import CLAUDE_BIN

    path = shutil.which(CLAUDE_BIN) or CLAUDE_BIN
    if not shutil.which(CLAUDE_BIN):
        print(f"{BAD} Claude CLI '{CLAUDE_BIN}' not found on PATH "
              f"— install Claude Code or set CLAUDE_BIN in .env")
        return False
    try:
        out = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=30,
            shell=(path.lower().endswith((".cmd", ".bat"))),
        )
        ver = (out.stdout or out.stderr).strip().splitlines()[0] if out.stdout or out.stderr else "?"
        print(f"{OK} Claude CLI found: {path} ({ver})")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} Could not run '{CLAUDE_BIN} --version': {exc}")
        return False


def check_claude_auth() -> bool:
    """Probe the CLI to confirm it is logged in (Max subscription)."""
    try:
        from claude_client import ClaudeError, query
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} Could not load claude_client: {exc}")
        return False

    try:
        result = asyncio.run(
            query("Reply with exactly: OK", allowed_tools=(), timeout=60)
        )
        print(f"{OK} Claude CLI authenticated — got a response ({result[:20]!r})")
        return True
    except ClaudeError as exc:
        print(f"{BAD} Claude CLI not usable: {exc}")
        print("   → Run 'claude' once interactively to log in with your Max account.")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} Unexpected error probing Claude: {exc}")
        return False


def check_token() -> bool:
    from config import TELEGRAM_BOT_TOKEN

    if TELEGRAM_BOT_TOKEN:
        masked = TELEGRAM_BOT_TOKEN[:6] + "…" + TELEGRAM_BOT_TOKEN[-4:]
        print(f"{OK} TELEGRAM_BOT_TOKEN is set ({masked})")
        return True
    print(f"{BAD} TELEGRAM_BOT_TOKEN is empty — set it in .env (from @BotFather)")
    return False


def main() -> int:
    print("AllDataCars preflight\n" + "-" * 40)
    results = [
        check_python(),
        check_packages(),
        check_claude_cli(),
        check_claude_auth(),
        check_token(),
    ]
    print("-" * 40)
    if all(results):
        print(f"{OK} All checks passed — start the bot with: python bot.py")
        return 0
    print(f"{BAD} Some checks failed — fix the items above, then re-run python doctor.py")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
