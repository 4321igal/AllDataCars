#!/usr/bin/env python3
"""Ask Claude questions about the Jeep FSM using markdown files."""
import asyncio
import sys
from claude_client import query

FSM_DIR = r"2005-20010-Jeep-Grand-Cherokee-WK-FSM\FSM_Sections\markdown"


async def ask(question: str) -> str:
    """Ask Claude a question using the FSM directory."""
    result, _ = await query(
        prompt=question,
        system="You are a knowledgeable mechanic assistant. Answer questions about the vehicle based on the FSM documentation provided.",
        add_dirs=(FSM_DIR,),
    )
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ask_claude.py '<question>'")
        print("Example: python ask_claude.py 'איך מחליפים רפידות בלם?'")
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    try:
        answer = asyncio.run(ask(question))
        print(answer)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
