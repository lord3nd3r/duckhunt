# DuckHunt IRC Bot

DuckHunt is an asyncio-based IRC bot that runs a classic “duck hunting” mini-game in IRC channels.

## Credits

- Originally written by **Computertech**
- New features, fixes, and maintenance added by **End3r**

## Features

- Per-channel stats (same nick has separate stats per channel)
- Multiple duck types (normal / golden / fast + special variants)
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

Copy the example config and edit it:

```bash
cp config.json.example config.json
```

Then edit `config.json`:

- `connection.server`, `connection.port`, `connection.nick`
- `connection.channels` (list of channels to join on connect)
- `connection.ssl` and optional password/SASL settings

Security note: `config.json` is ignored by git in this repo; don’t commit real IRC passwords/tokens.

### Duck types

Duck types are configured under `duck_types` in `config.json`.

Built-in variants supported by the game logic:

- `normal`, `fast`, `golden`
- `concrete` (multi-HP)
- `holy_grail` (multi-HP)
- `diamond` (multi-HP)
- `explosive` (on kill: eliminates the shooter for 2 hours)
- `poisonous` (on befriend: poisons the befriender for 2 hours)
- `radioactive` (on befriend: poisons the befriender for 8 hours)
- `couple` (spawns 2 ducks at once)
- `family` (spawns 3–4 ducks at once)

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

### Shop items (IDs)

Use `!shop` / `!buy <id>` / `!use <id>`.

- `10` Sniper Rifle: perfect aim for 30 minutes
- `11` Sniper Scope: perfect aim for 60 minutes
- `12` Duck Whistle: instantly summons a duck (if none are present)
- `13` Duck Caller: instantly summons a duck (if none are present)
- `14` Duck Horn: instantly summons a duck (if none are present)
- `15` Duck Decoy: summons a duck in ~1 hour (if none are present)
- `16` Duck Radar: DM alert when a duck spawns in that channel (6 hours)

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
├── config.json          # Bot configuration (ignored by git)
├── config.json.example  # Safe template to copy
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