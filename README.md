# DuckHunt IRC Bot

DuckHunt is an asyncio-based IRC bot that runs a classic “duck hunting” mini-game in IRC channels.

## Credits

- Originally written by **Computertech**
- New features, fixes, and maintenance added by **End3r**

## Features

- Per-channel stats (same nick has separate stats per channel)
- Multiple duck types (normal / golden / fast)
- Shop + inventory items
- Admin commands (rearm/disarm/ignore, spawn ducks, join/leave channels)
- `!globalducks` totals across channels
- JSON persistence to disk (`duckhunt.json`)

## Quick start

### Requirements

- Python 3.8+

### Run

From the repo root:

```bash
python3 duckhunt.py
```

## Configuration

Edit `config.json`:

- `connection.server`, `connection.port`, `connection.nick`
- `connection.channels` (list of channels to join on connect)
- `connection.ssl` and optional password/SASL settings

Security note: don’t commit real IRC passwords/tokens in `config.json`.

## Persistence

Player stats are saved to `duckhunt.json`.

- Stats are stored per channel.
- If you run `!join` / `!leave`, the bot updates `config.json` so channel changes persist across restarts.

## Commands

### Player commands

- `!bang`
- `!reload`
- `!shop`
- `!buy <item_id>`
- `!use <item_id> [target]`
- `!duckstats [player]`
- `!topduck`
- `!give <item_id> <player>`
- `!globalducks [player]` (totals across all configured channels)
- `!duckhelp` (sends a PM with examples)

### Admin commands

- `!rearm <player|all>`
- `!disarm <player>`
- `!ignore <player>` / `!unignore <player>`
- `!ducklaunch [duck_type]` (in-channel)
- `!ducklaunch <#channel> [duck_type]` (in PM)
- `!join <#channel>` / `!leave <#channel>` (persists to `config.json`)

## Repo layout

```
duckhunt/
├── duckhunt.py          # Entry point
├── config.json          # Bot configuration
├── duckhunt.json        # Player database (generated/updated at runtime)
└── src/
    ├── duckhuntbot.py   # IRC bot + command routing
    ├── game.py          # Duck game logic
    ├── db.py            # Persistence layer
    ├── shop.py          # Shop/inventory
    ├── levels.py        # Level system
    └── ...
```

**Happy Duck Hunting!**