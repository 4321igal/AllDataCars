"""Async wrapper around the Claude Code CLI (`claude -p`).

The CLI is authenticated with the user's Claude Max subscription, so no
ANTHROPIC_API_KEY is required — the LLM runs "through the user's computer".
Claude's built-in WebSearch / WebFetch tools handle the web searches for
repair videos and PDF manuals.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil

from config import CLAUDE_BIN, CLAUDE_MODEL, CLAUDE_TIMEOUT

logger = logging.getLogger(__name__)

DEFAULT_TOOLS = ("WebSearch", "WebFetch")


class ClaudeError(RuntimeError):
    """Raised when the Claude CLI fails, times out, or returns an error."""


def _resolve_invocation(cmd: list[str]) -> list[str]:
    """Resolve the Claude binary so it runs reliably across platforms.

    On Windows, npm installs the CLI as ``claude.cmd`` (a batch shim). A bare
    ``claude`` passed to ``create_subprocess_exec`` fails because Windows only
    resolves ``.exe`` on PATH, and a ``.cmd`` cannot be executed directly — it
    needs ``cmd.exe``. We resolve the full path with ``shutil.which`` (which
    honors PATHEXT and finds ``claude.cmd``) and, for a ``.cmd``/``.bat`` shim,
    invoke it through ``cmd.exe /c``.
    """
    exe = shutil.which(cmd[0]) or cmd[0]
    rest = cmd[1:]
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c", exe, *rest]
    return [exe, *rest]


async def query(
    prompt: str,
    system: str = "",
    allowed_tools: tuple[str, ...] = DEFAULT_TOOLS,
    timeout: int = CLAUDE_TIMEOUT,
    model: str = CLAUDE_MODEL,
    add_dirs: tuple[str, ...] = (),
) -> str:
    """Run a single headless Claude query and return the text result.

    The prompt is fed on stdin to avoid shell-escaping / argument-length issues.
    When ``add_dirs`` is given (e.g. a vehicle's FSM markdown folder), those
    directories are exposed to Claude via ``--add-dir`` and the Read/Grep/Glob
    tools are enabled so it can search the manual itself.
    """
    if add_dirs:
        allowed_tools = tuple(dict.fromkeys((*allowed_tools, "Read", "Grep", "Glob")))

    cmd = [
        CLAUDE_BIN,
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
    ]
    if allowed_tools:
        cmd += ["--allowedTools", *allowed_tools]
    for directory in add_dirs:
        cmd += ["--add-dir", directory]
    if system:
        cmd += ["--append-system-prompt", system]

    cmd = _resolve_invocation(cmd)

    logger.info(
        "Invoking Claude CLI (model=%s, tools=%s, add_dirs=%d)",
        model, ",".join(allowed_tools), len(add_dirs),
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ClaudeError(
            f"Claude CLI not found at '{CLAUDE_BIN}'. Install Claude Code and log in "
            f"with your Max subscription, or set CLAUDE_BIN."
        ) from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")), timeout=timeout
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ClaudeError(
            f"Claude CLI timed out after {timeout}s. Try a simpler question or raise CLAUDE_TIMEOUT."
        ) from exc

    if proc.returncode != 0:
        err = stderr.decode("utf-8", "replace").strip()
        raise ClaudeError(f"Claude CLI exited with code {proc.returncode}: {err[:500]}")

    raw = stdout.decode("utf-8", "replace").strip()
    if not raw:
        raise ClaudeError("Claude CLI returned no output.")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClaudeError(f"Could not parse Claude CLI output as JSON: {raw[:500]}") from exc

    if payload.get("is_error") or payload.get("subtype") not in (None, "success"):
        raise ClaudeError(f"Claude reported an error: {payload.get('result', payload)}")

    result = payload.get("result")
    if not isinstance(result, str) or not result.strip():
        raise ClaudeError("Claude CLI returned an empty result field.")

    return result.strip()
