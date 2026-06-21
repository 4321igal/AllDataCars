"""Prompt templates for the fleet maintenance agent."""
from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """\
You are AllDataCars, an expert automotive fleet maintenance agent that helps the \
owner service and repair their vehicles. You are knowledgeable about Factory \
Service Manual (FSM) procedures, torque specs, fluids, and part numbers.

For every request about a maintenance or repair task you MUST:
1. Give a clear, accurate, SAFETY-CONSCIOUS step-by-step explanation of the \
procedure, tailored to the specific vehicle (make/model/year/engine) and grounded \
in the FSM context provided when available. Include tools, torque specs, fluids \
and safety warnings where relevant.
2. Find relevant REPAIR VIDEOS on the web (YouTube and similar) that demonstrate \
the exact procedure for this vehicle. Use the WebSearch / WebFetch tools.
3. Find PDF download options for the relevant service manual / repair documentation.
4. REUSE the cached links provided to you when they are relevant instead of \
searching again. Only search the web for what is missing.

Write the human-readable answer in the SAME LANGUAGE the user wrote in (Hebrew or \
English). Keep it practical and well-structured with headings and numbered steps.

After the human-readable answer, output the discovered resources as a single \
fenced JSON code block (and nothing after it), in EXACTLY this shape:

```json
{
  "videos": [
    {"title": "...", "url": "https://...", "description": "..."}
  ],
  "pdfs": [
    {"title": "...", "url": "https://...", "description": "..."}
  ]
}
```

Only include URLs you are confident are real and reachable. If you reused a cached \
link, still include it in the JSON. If a category has nothing, use an empty list.
"""


def _vehicle_block(vehicle: dict[str, Any]) -> str:
    fields = [
        ("Name", vehicle.get("name")),
        ("Make", vehicle.get("make")),
        ("Model", vehicle.get("model")),
        ("Year", vehicle.get("year")),
        ("Engine", vehicle.get("engine")),
        ("VIN", vehicle.get("vin")),
        ("Notes", vehicle.get("notes")),
    ]
    lines = [f"- {label}: {value}" for label, value in fields if value]
    return "\n".join(lines) if lines else "- (no details recorded)"


def _fsm_block(fsm_docs: list[dict[str, Any]]) -> str:
    if not fsm_docs:
        return "(no FSM documents stored for this vehicle)"
    chunks = []
    for doc in fsm_docs:
        header = f"### FSM: {doc.get('title') or doc.get('kind')}"
        text = (doc.get("content_text") or "").strip()
        if text:
            # Cap each doc so the prompt stays a reasonable size.
            text = text[:6000]
            chunks.append(f"{header}\n{text}")
        elif doc.get("source_url"):
            chunks.append(f"{header}\nSource: {doc['source_url']}")
        else:
            chunks.append(header)
    return "\n\n".join(chunks)


def _links_block(links: list[dict[str, Any]]) -> str:
    if not links:
        return "(none cached yet)"
    lines = []
    for link in links:
        lines.append(
            f"- [{link.get('kind')}] {link.get('title') or ''} {link['url']}".strip()
        )
    return "\n".join(lines)


def _history_block(history: list[dict[str, Any]]) -> str:
    if not history:
        return ""
    lines = ["## Recent conversation"]
    for msg in history:
        role = "User" if msg["role"] == "user" else "Agent"
        content = msg["content"].strip()
        if len(content) > 800:
            content = content[:800] + "…"
        lines.append(f"{role}: {content}")
    return "\n".join(lines) + "\n\n"


def build_user_prompt(
    question: str,
    vehicle: dict[str, Any] | None,
    fsm_docs: list[dict[str, Any]],
    cached_links: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> str:
    """Assemble the full prompt sent to Claude for a maintenance question."""
    if vehicle:
        vehicle_section = (
            "## Active vehicle\n"
            + _vehicle_block(vehicle)
            + "\n\n## FSM context\n"
            + _fsm_block(fsm_docs)
            + "\n\n## Cached resources (reuse if relevant)\n"
            + _links_block(cached_links)
            + "\n\n"
        )
    else:
        vehicle_section = (
            "## Active vehicle\n(No vehicle selected. Answer generically and remind "
            "the user to add/select a vehicle with /addvehicle for tailored help.)\n\n"
        )

    return (
        vehicle_section
        + _history_block(history)
        + "## User request\n"
        + question.strip()
    )
