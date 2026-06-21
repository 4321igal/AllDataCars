# AllDataCars — Telegram Fleet Maintenance Agent

A Telegram bot that acts as a smart maintenance agent for your fleet of vehicles.
Ask it a repair question and it will:

1. Store relevant data for each vehicle, including **FSM (Factory Service Manual)**
   PDFs and notes.
2. Send your question to an **LLM running on your own computer** — it drives the
   **Claude Code CLI** authenticated with your **Claude Max subscription** (no paid
   API key required).
3. Search the web for relevant **repair videos** and return a detailed,
   step-by-step explanation of the procedure for your specific vehicle.
4. Search the web for **PDF service manual** download options.
5. **Cache** every link it finds per vehicle, so the next search is faster and
   doesn't repeat work.

## How it works

```
Telegram ──> bot.py ──> agent.py ──> claude_client.py ──> `claude -p` (Max login)
                          │                                   │
                          └── db.py (SQLite) <── caches ──────┘  WebSearch / WebFetch
```

The bot is a thin layer over the Claude Code CLI. Per message it loads the active
vehicle's context and cached links from SQLite, builds a prompt, runs `claude -p`
with the WebSearch/WebFetch tools enabled, parses the answer plus a structured JSON
block of links, caches new links, and replies.

## Requirements

- Python 3.10+
- [Claude Code](https://claude.com/claude-code) CLI installed and **logged in with
  your Claude Max subscription** (run `claude` once interactively to authenticate).
- A Telegram bot token from [@BotFather](https://t.me/BotFather).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set TELEGRAM_BOT_TOKEN (and optionally ALLOWED_USER_IDS)
```

Verify the Claude CLI is authenticated:

```bash
echo "Reply with OK" | claude -p --output-format json
```

Run the bot:

```bash
python bot.py
```

## Usage

In Telegram:

- `/addvehicle Toyota Corolla 2015` — register a vehicle (becomes active).
- `/vehicles` — list your fleet; tap a button to switch the active vehicle.
- `/select <id>` — set the active vehicle by id.
- `/info` — show the active vehicle's details.
- Send a **PDF** — stored as an FSM document for the active vehicle (text extracted).
- `/fsm` — list stored FSM documents.
- `/links` — show cached videos and PDF manuals.
- Just **ask** — e.g. _"How do I replace the front brake pads?"_ — and you'll get a
  step-by-step guide plus video and PDF links.

## Configuration (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | — | **Required.** From @BotFather. |
| `CLAUDE_BIN` | `claude` | Path to the Claude Code CLI. |
| `CLAUDE_MODEL` | `sonnet` | Model alias/name for the CLI. |
| `CLAUDE_TIMEOUT` | `240` | Seconds to wait for a response (web search is slow). |
| `DB_PATH` | `data/fleet.db` | SQLite database location. |
| `FSM_DIR` | `fsm_files` | Where uploaded FSM PDFs are stored. |
| `ALLOWED_USER_IDS` | _(empty)_ | Comma-separated Telegram user IDs allowed to use the bot. Empty = everyone. |

## Switching to the metered API later

The design does not depend on the Max login specifically — the Claude CLI uses
whatever auth is configured. If you later set an `ANTHROPIC_API_KEY` in the CLI's
environment, the same code runs against the metered API with no changes.

> Note: using Max/subscription auth to power an always-on bot is a gray area with
> respect to Anthropic's terms and rate limits. Use responsibly.

## Project layout

| File | Purpose |
|------|---------|
| `bot.py` | Telegram handlers and entrypoint. |
| `agent.py` | Orchestration: context → Claude → parse → cache. |
| `claude_client.py` | Async wrapper around `claude -p`. |
| `prompts.py` | System and task prompt templates. |
| `db.py` | SQLite schema and CRUD. |
| `config.py` | Environment configuration. |
