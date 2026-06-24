"""Orchestration: build context -> query Claude -> parse -> cache links."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import db
from claude_client import ClaudeError, query
from prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

# Matches the LAST fenced ```json ... ``` block in the response.
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class AgentResult:
    answer: str
    videos: list[dict[str, str]] = field(default_factory=list)
    pdfs: list[dict[str, str]] = field(default_factory=list)
    new_links: int = 0


def parse_response(text: str) -> tuple[str, list[dict[str, str]], list[dict[str, str]]]:
    """Split the human answer from the trailing JSON links block.

    Returns (clean_answer, videos, pdfs). If no/invalid JSON is found, the full
    text is treated as the answer and link lists are empty.
    """
    matches = list(_JSON_BLOCK_RE.finditer(text))
    if not matches:
        return text.strip(), [], []

    block = matches[-1]
    videos: list[dict[str, str]] = []
    pdfs: list[dict[str, str]] = []
    try:
        data = json.loads(block.group(1))
        videos = [v for v in data.get("videos", []) if isinstance(v, dict) and v.get("url")]
        pdfs = [p for p in data.get("pdfs", []) if isinstance(p, dict) and p.get("url")]
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Failed to parse links JSON block; keeping full text.")
        return text.strip(), [], []

    clean = (text[: block.start()] + text[block.end():]).strip()
    return clean, videos, pdfs


async def handle_message(user_id: int, text: str) -> AgentResult:
    """Handle a free-text maintenance question for the user's active vehicle."""
    vehicle = db.get_active_vehicle(user_id)
    vehicle_id = vehicle["id"] if vehicle else None

    fsm_docs = db.get_fsm_docs(vehicle_id) if vehicle_id else []
    cached_links = db.get_cached_links(vehicle_id) if vehicle_id else []
    history = db.recent_messages(vehicle_id, user_id, limit=6)

    prompt = build_user_prompt(text, vehicle, fsm_docs, cached_links, history)
    fsm_dirs = tuple(db.get_fsm_dirs(vehicle_id)) if vehicle_id else ()

    db.add_message(vehicle_id, user_id, "user", text)
    db.append_history_file(user_id, "user", text, vehicle=vehicle)

    try:
        raw, token_info = await query(prompt, system=SYSTEM_PROMPT, add_dirs=fsm_dirs)
    except ClaudeError:
        raise

    answer, videos, pdfs = parse_response(raw)

    new_links = 0
    if vehicle_id:
        for v in videos:
            if db.upsert_link(
                vehicle_id, "video", v.get("url", ""),
                title=v.get("title", ""), description=v.get("description", ""),
                topic=text[:120],
            ):
                new_links += 1
        for p in pdfs:
            if db.upsert_link(
                vehicle_id, "pdf", p.get("url", ""),
                title=p.get("title", ""), description=p.get("description", ""),
                topic=text[:120],
            ):
                new_links += 1

    db.add_message(vehicle_id, user_id, "assistant", answer)
    db.append_history_file(user_id, "assistant", answer, token_info=token_info, vehicle=vehicle)

    return AgentResult(answer=answer, videos=videos, pdfs=pdfs, new_links=new_links)
