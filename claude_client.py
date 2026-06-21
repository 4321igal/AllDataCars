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

from config import CLAUDE_BIN, CLAUDE_MODEL, CLAUDE_TIMEOUT

logger = logging.getLogger(__name__)

DEFAULT_TOOLS = ("WebSearch", "WebFetch")


class ClaudeError(RuntimeError):
    """Raised when the Claude CLI fails, times out, or returns an error."""


async def query(
    prompt: str,
    system: str = "",
    allowed_tools: tuple[str, ...] = DEFAULT_TOOLS,
    timeout: int = CLAUDE_TIMEOUT,
    model: str = CLAUDE_MODEL,
) -> str:
    """Run a single headless Claude query and return the text result.

    The prompt is fed on stdin to avoid shell-escaping / argument-length issues.
    """
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
    if system:
        cmd += ["--append-system-prompt", system]

    logger.info("Invoking Claude CLI (model=%s, tools=%s)", model, ",".join(allowed_tools))

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
