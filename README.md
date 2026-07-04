# DuckHunt IRC Bot

DuckHunt is an asyncio-based IRC bot that runs a classic "duck hunting" mini-game in IRC channels.

## Credits

- Originally written by **Computertech**
- New features, fixes, and maintenance added by **End3r**

## Features

- **Multi-channel support** - Bot can be in multiple channels simultaneously
- **Per-channel player stats** - Stats are tracked separately per channel
- **Global leaderboard** - View the global top 5 across all channels
- **Achievement System** - Earn badges for milestones (e.g., First Blood, Sharpshooter, Golden Slayer)
- **Duck Types & Flocks** - Normal, Golden, Fast, Ninja, and flock events
- **Shop system** - Buy items, use them, or gift them to others
- **Leveling system** - Gain XP, increase your level, and unlock permanent upgrades
- **JSON persistence & Auto-save** - All stats saved to disk automatically after each action

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
- `connection.ssl_verify` (default `true`) validates the server's TLS certificate.
  Only set to `false` for testing or trusted self-signed certificates; disabling it
  removes protection against man-in-the-middle attacks.
- `admins` (list of admin nicks). Each entry can be either:
  - a plain string nick, e.g. `"colby"` — simple, but authenticates by nick alone, so
    anyone who takes that nick on the server (e.g. after the real admin disconnects) is
    granted admin access. The bot logs a startup warning for any admin configured this way.
  - a dict with a hostmask, e.g. `{"nick": "colby", "hostmask": "*!colby@trusted.host"}` —
    recommended, since it also requires the connecting user/host to match.

Duck spawning is controlled by `duck_spawning.spawn_min` and `duck_spawning.spawn_max` (in seconds). Default is 1–2 hours (`3600`–`7200`).

**Security note:** `config.json` is ignored by git — don't commit real IRC passwords/tokens.

### Duck Types

Four duck types plus flock events:

- **Normal** - Standard duck, 1 HP, base XP.
- **Golden** - Multi-HP duck (3–5 HP), higher XP, awards XP per hit.
- **Fast** - Quick duck, 1 HP, flies away faster.
- **Ninja** - Has a dodge chance making it harder to hit.
- **Flock** - 2–4 normal ducks spawn at once — shoot them one by one.

Duck spawn behavior is configured in `config.json` under `duck_types`.

## Persistence

Player stats are saved to `duckhunt.json`:

- **Per-channel stats** - Players have separate stats per channel (stored under `channels`).
- **Global top 5** - `!globaltop` aggregates XP across all channels.
- **Atomic writes & retry logic** - Safe file handling prevents database corruption.

## Commands

### Player Commands

- `!bang` - Shoot at a duck
- `!bef` / `!befriend` - Try to befriend a duck
- `!reload` - Reload your gun
- `!daily` - Claim your daily XP bonus (resets every 24h, builds streaks)
- `!duckstats [player]` - View hunting statistics for the current channel
- `!profile` - Get a detailed hunter stat card sent to your PM
- `!topduck` - View channel leaderboard
- `!globaltop` - View global leaderboard (top 5 across all channels)
- `!achievements` - Check your earned badges (sent via PM)
- `!effects` - View active temporary buffs and their timers
- `!inv` - Quick inline view of your inventory
- `!duckhelp` - Get the full command list via PM

### Shop Commands

- `!shop` - View available items
- `!shop buy <item_id>` - Purchase an item from the shop
- `!use <item_id> [target]` - Use an item from your inventory
- `!give <item_id> <player>` - Give an inventory item to another player

### Admin Commands

- `!rearm <player|all>` - Give a player a gun
- `!disarm <player>` - Confiscate a player's gun
- `!ignore <player>` / `!unignore <player>` - Ignore/unignore commands from a player
- `!ducklaunch [duck_type]` - Force spawn a duck (normal, golden, fast, ninja, flock)
- `!join <#channel>` - Make the bot join a channel
- `!part <#channel>` - Make the bot leave a channel
- `!reload` (in PM) - Restart the bot process smoothly

## Shop Items

Seven items available (use `!shop` to see current prices and IDs):

| ID | Name | Cost | Effect |
|----|------|------|--------|
| 1 | Single Bullet | 5 XP | Add 1 bullet to your current magazine |
| 2 | Magazine | 15 XP | Add a spare magazine |
| 4 | Gun Brush | 20 XP | Reduce your jam chance by 10% |
| 5 | Bread | 50 XP | Double duck spawn rate for 20 minutes |
| 7 | Buy Gun Back | 40 XP | Recover your confiscated gun |
| 13 | Scope | 60 XP | +20% accuracy for your next 5 shots |
| 14 | Body Armor | 100 XP | Absorbs your next XP-loss event |

## Gameplay

### How to Play

1. Wait for a duck to spawn (appears randomly in channel, roughly every 1–2 hours).
2. Type `!bang` to shoot it or `!bef` to befriend it.
3. Earn XP for successful hits or befriending.
4. Level up to improve your stats (accuracy, magazine size, jam chance).
5. Buy items from `!shop` to enhance your hunting.

### Stats Tracked

- XP, Level, Current & Best Hit Streaks
- Ducks shot & befriended
- Accuracy, Hit Rate, Daily Bonus Streak
- Current Inventory, Active Effects
- Achievements Earned
- Total XP spent in the shop

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

## Development & Improvements

### Recent Fixes & Enhancements

- **Asynchronous Non-blocking Message Loops** - Converted the bot message-sending queue to run asynchronously, resolving loop blocking issues caused by synchronous message-throttling delay sleep.
- **Config & Duck Types Syncing** - Removed dead/unimplemented duck types from the configuration template and added proper config schema for the `ninja` duck.
- **Decoy Duck Removal** - Completely removed vestigial decoy duck references, comments, and handling branches from the shooting and befriending mechanics.
- **Redundant Schema Clean-up** - Stripped legacy player database fields (`ammo`, `max_ammo`, `chargers`) from defaults, creation, and runtime sanitization logic.
- **Dynamic Inventory Sanitization** - Implemented automated sanitization on player load to filter out and remove invalid or orphaned item IDs.
- **Command & Shop Fixes** - Corrected config key path mismatches, synced the default fallback shop with the active catalog, and updated the mystery box fallback pool.
- **Multi-channel safety** - Database warns when player nicks collide across channels, preventing silent data loss.
- **Robust error handling** - Improved async function detection and lambda logging in error recovery.
- **Type hints** - Core database functions now have full type annotations for better IDE support.
- **Comprehensive error recovery** - Circuit breakers and retry logic prevent cascading failures.

### Code Quality

- Atomic database writes with JSON validation.
- Comprehensive input sanitization.
- Health check monitoring.
- Per-player rate limiting to prevent abuse.

**Happy Duck Hunting!**
