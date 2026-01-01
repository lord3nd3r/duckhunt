# DuckHunt IRC Bot

DuckHunt is an asyncio-based IRC bot that runs a classic "duck hunting" mini-game in IRC channels.

## Credits

- Originally written by **Computertech**
- New features, fixes, and maintenance added by **End3r**

## Features

- **Multi-channel support** - Bot can be in multiple channels
- **Per-channel player stats** - Stats are tracked separately per channel
- **Global leaderboard** - View the global top 5 across all channels
- **Three duck types** - Normal, Golden (multi-HP), and Fast ducks
- **Shop system** - Buy items to improve your hunting
- **Leveling system** - Gain XP and increase your level
- **Admin commands** - Join/leave channels, spawn ducks, manage players
- **JSON persistence** - All stats saved to disk
- **Auto-save** - Progress saved automatically after each action

## Quick Start

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
- `admins` (list of admin nicks or nick+hostmask patterns)

**Security note:** `config.json` is ignored by git - don't commit real IRC passwords/tokens.

### Duck Types

Three duck types with different behaviors:

- **Normal** - Standard duck, 1 HP, base XP
- **Golden** - Multi-HP duck (3-5 HP), high XP, awards XP per hit
- **Fast** - Quick duck, 1 HP, flies away faster

Duck spawn behavior is configured in `config.json` under `duck_types`:

- `duck_types.golden.chance` - Probability of a golden duck (default: 0.15)
- `duck_types.fast.chance` - Probability of a fast duck (default: 0.25)
- `duck_types.golden.min_hp` / `duck_types.golden.max_hp` - Golden duck HP range

## Persistence

Player stats are saved to `duckhunt.json`:

- **Per-channel stats** - Players have separate stats per channel (stored under `channels`)
- **Global top 5** - `!globaltop` aggregates XP across all channels
- **Auto-save** - Database saved after each action (shoot, reload, shop, etc.)
- **Atomic writes** - Safe file handling prevents database corruption
- **Retry logic** - Automatic retry on save failures

## Commands

### Player Commands

- `!bang` - Shoot at a duck
- `!bef` or `!befriend` - Try to befriend a duck
- `!reload` - Reload your gun
- `!shop` - View available items
- `!shop buy <item_id>` - Purchase an item from the shop
- `!duckstats [player]` - View hunting statistics for the current channel
- `!topduck` - View leaderboard (top hunters)
- `!globaltop` - View global leaderboard (top 5 across all channels)
- `!duckhelp` - Get detailed command list via PM

### Admin Commands

- `!rearm <player|all>` - Give player a gun
- `!disarm <player>` - Confiscate player's gun
- `!ignore <player>` / `!unignore <player>` - Ignore/unignore commands
- `!ducklaunch [duck_type]` - Force spawn a duck (normal, golden, fast)
- `!join <#channel>` - Make bot join a channel
- `!part <#channel>` - Make bot leave a channel

Admin commands work in PM or in-channel.

## Shop Items

Basic shop items available (use `!shop` to see current inventory):

- **Bullets** - Ammunition refills
- **Magazines** - Extra ammo capacity
- **Gun Improvements** - Better accuracy, less jamming
- **Gun License** - Buy back your confiscated gun
- **Insurance** - Protection from penalties

Use `!shop buy <id>` to purchase.

## Gameplay

### How to Play

1. Wait for a duck to spawn (appears randomly in channel)
2. Type `!bang` to shoot it
3. Earn XP for successful hits
4. Level up to improve your stats
5. Buy items from `!shop` to enhance your hunting

### Duck Behavior

- **Normal ducks** - Standard targets, 1 shot to kill
- **Golden ducks** - Tougher! Multiple HP, gives XP per hit
- **Fast ducks** - Quick! They fly away faster than normal

### Stats Tracked

- XP (experience points)
- Ducks shot
- Ducks befriended
- Shots fired
- Accuracy percentage
- Current level

Note: stats are tracked per-channel; use `!globaltop` for an across-channels view.

## Repo Layout

```
duckhunt/
├── duckhunt.py          # Entry point
├── config.json          # Bot configuration (ignored by git)
├── config.json.example  # Safe template to copy
├── duckhunt.json        # Player database (auto-generated)
├── levels.json          # Level definitions
├── shop.json            # Shop item catalog
├── messages.json        # Bot messages
└── src/
    ├── duckhuntbot.py   # IRC bot + command routing
    ├── game.py          # Duck game logic
    ├── db.py            # Database persistence
    ├── shop.py          # Shop/inventory system
    ├── levels.py        # Leveling system
    ├── sasl.py          # SASL authentication
    ├── error_handling.py # Error recovery
    └── utils.py         # Utility functions
```

## Recent Updates

- ✅ Fixed golden duck XP bug (now awards XP on each hit)
- ✅ Added `!join` and `!part` admin commands
- ✅ Improved `!duckhelp` with detailed PM
- ✅ Simplified to 3 core duck types for stability
- ✅ Enhanced database save reliability

**Happy Duck Hunting!** 🦆