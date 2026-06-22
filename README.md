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

## Quick start on Windows (one click)

The easiest path: after cloning, just run **`start.bat`** (double-click it, or run
`start.bat` in a terminal). It creates the virtual environment, installs
dependencies, opens `.env` for you to paste your Telegram token, runs the preflight,
and starts the bot — no PowerShell activation needed. You still need Python 3.10+,
the Claude CLI logged in with Max, and your bot token.

## Run on your own computer

Because the bot uses your Claude **Max** login via the CLI, run it on the machine
where `claude` is authenticated (your own computer).

```bash
# 1. Get the code
git clone <repo-url> AllDataCars        # or: git pull
cd AllDataCars
git checkout claude/telegram-fleet-agent-uttm8a

# 2. Python deps (virtualenv recommended)
python3 -m venv .venv
source .venv/bin/activate                # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Log in to the Claude CLI with your Max account (one-time, interactive)
claude                                    # complete login, then exit
echo "Reply with OK" | claude -p --output-format json   # should print JSON with "result"

# 4. Configure secrets
cp .env.example .env                      # Windows: copy .env.example .env
#   edit .env -> set TELEGRAM_BOT_TOKEN=<token from @BotFather>

# 5. Preflight + run
python doctor.py                          # all checks should be green
python bot.py
```

Or use the launcher, which runs the preflight then the bot:

```bash
./run.sh        # macOS / Linux
run.bat         # Windows
```

`python doctor.py` checks Python, dependencies, the Claude CLI + Max login, and the
Telegram token, with fix hints for anything that's off.

### Troubleshooting

- **`ModuleNotFoundError: _cffi_backend`** when importing `telegram`:
  `pip install --upgrade cffi cryptography`.
- **Claude auth probe fails**: run `claude` once interactively to log in, then retry.
- **Answers time out**: web search can take 30–120s — raise `CLAUDE_TIMEOUT` in `.env`.
- **Keep the token safe**: it lives only in `.env` (git-ignored). If it ever leaks,
  regenerate it in @BotFather with `/revoke`.
- **Lock the bot to yourself**: set `ALLOWED_USER_IDS` in `.env` to your Telegram user
  ID (get it from @userinfobot).

## Usage

### Guided flow (the simple path)

Just send **`/start`** and follow along:

1. The bot asks for a **license plate** (מספר רכב).
2. You send the plate.
3. The bot looks it up in both `data.gov.il` registries (regular + personal import)
   and shows the car's details, then sets it as your active vehicle.
4. The bot asks what you want to know.
5. You ask (e.g. "איך מחליפים רפידות בלם?") and get a step-by-step answer plus
   repair videos and PDF manuals. Keep asking, or send a new plate to switch cars.

### Commands (power-user path)

The commands below also work alongside the guided flow:

- `/addvehicle Toyota Corolla 2015` — register a vehicle (becomes active).
- `/vehicles` — list your fleet; tap a button to switch the active vehicle.
- `/select <id>` — set the active vehicle by id.
- `/info` — show the active vehicle's details.
- Send a **PDF** — stored as an FSM document for the active vehicle (text extracted).
- `/fsm` — list stored FSM documents.
- `/fsm <path>` — register a **folder of markdown FSM files** (e.g. a full Factory
  Service Manual) for the active vehicle. The bot then lets Claude grep that folder
  (via `--add-dir`) to ground repair answers in the real manual — no size limit.
- `/plate <מספר רכב>` — look up a license plate in the Israeli public registries
  (`data.gov.il`): the private/commercial registry **and** the personal-import
  registry. If found, shows the car's details and offers a one-tap **➕ Add to fleet**
  button that auto-fills make/model/year/engine.
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
