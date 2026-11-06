# DuckHunt IRC Bot

[![Donate - PayPal](https://img.shields.io/badge/Donate-PayPal-0070ba?style=for-the-badge&logo=paypal&logoColor=white)](https://3nd3r.net/paypal) [![Donate - Cash App](https://img.shields.io/badge/Donate-Cash%20App-00d632?style=for-the-badge&logo=cashapp&logoColor=white)](https://3nd3r.net/cashapp) [![Donate - Venmo](https://img.shields.io/badge/Donate-Venmo-008cff?style=for-the-badge&logo=venmo&logoColor=white)](https://3nd3r.net/venmo) [![Donate - BTC](https://img.shields.io/badge/Donate-BTC-f7931a?style=for-the-badge&logo=bitcoin&logoColor=white)](https://3nd3r.net/btc)

DuckHunt is an asyncio-based IRC bot that runs a classic "duck hunting" mini-game in IRC channels.

## Credits

- Originally written by **Computertech**
- New features, fixes, and maintenance added by **End3r**

## Features

- **Multi-channel support** - Bot can be in multiple channels simultaneously
- **Per-channel game toggle** - Admins can enable or disable the game per channel (`!dh on` / `!dh off`)
- **Persistent channel membership** - Remembers and rejoins dynamically joined channels across disconnects and restarts
- **Per-channel player stats** - Stats are tracked separately per channel
- **Global leaderboard** - View the global top 5 across all channels
- **Achievement System** - Earn badges for milestones (e.g., First Blood, Sharpshooter, Golden Slayer)
- **Duck Types & Flocks** - Normal, Golden, Fast, Ninja, and flock events
- **Shop system** - Buy items, use them, or gift them to others
- **Leveling system** - Gain XP and level up. Higher levels are harder: the accuracy bonus falls, you carry fewer magazines, and base jam rises from 0% to 8%. Spawn timing does not change with level
- **JSON persistence** - Actions schedule a save. The newest snapshot is written in the background; a crash before that write finishes can drop it

## Quick Start

### Requirements

- Python 3.9+ (`pyproject.toml` requires `>=3.9`)

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
- `admins`. Each entry is either:
  - a plain nick string, e.g. `"yourNickHere"`. That matches the nickname only, so anyone
    who takes the nick is an admin. The bot logs a startup warning for every nick-only entry.
  - an object with both `nick` and `hostmask`, for example
    `{"nick": "yourNickHere", "hostmask": "*!*@trusted.example"}`. The nick must match,
    and the full `nick!ident@host` prefix must match the mask (case-insensitive `fnmatch`).
    Many networks prefix the ident with `~` (`YourNick!~ident@trusted.example`). Include
    that `~` only when it is actually there, for example `*!~ident@trusted.example`.
    `*!ident@trusted.example` does not match a `~ident` prefix. `~` is not always present.
- `commands.prefix` (default `"!"`) - the character(s) that trigger bot commands, e.g.
  `!bang`, `@bang`, `$bang`. Must be non-empty and contain no whitespace. All command
  usage/help text (including `!duckhelp`) automatically reflects whatever prefix you configure.

Duck waits are `duck_spawning.spawn_min` and `duck_spawning.spawn_max`, in seconds. `config.json.example` uses 1200–3600 (20–60 minutes). If those keys are omitted, the code uses 300–900 seconds (5–15 minutes). When `duck_spawning.flock_chance` is omitted, about 8% of spawns (`0.08`) are flocks. Set that key only to override the 8%.

**Security note:** `config.json` is ignored by git — don't commit real IRC passwords/tokens.

### Duck Types

Four duck types plus flock events:

- **Normal** - Standard duck, 1 HP, base XP.
- **Golden** - Multi-HP duck (3–5 HP), higher XP, awards XP per hit.
- **Fast** - Quick duck, 1 HP, flies away faster.
- **Ninja** - Has a dodge chance. A killing shot uses the ninja drop table when that table is configured; otherwise it uses the normal duck's chance and table.
- **Flock** - 2–4 ducks at once. Each one you kill rolls once. With no flock drop table, that roll is the normal duck's chance and table.

A killing shot can drop one item, up to `limits.max_inventory_items` (20 in the example). Misses, ninja dodges, befriends, fly-aways, and a golden duck's non-killing hits do not drop. In the example, normal is 15%, fast 25%, ninja 30%, and golden 50% on the killing blow. A type that has its own non-empty table uses that table and its `drop_chance`. `drop_chance` 0 skips ordinary rolls; the check is `random > chance`, so a roll of exactly 0.0 would still drop. `features.shop_enabled`, `features.inventory_enabled`, and `features.auto_rearm_enabled` are in the example and are not read. Rearm-on-kill is `duck_spawning.rearm_on_duck_shot`.

`player_defaults.jam_chance` (15 in the example) is the old stored baseline, not the chance a hunter rolls. New players also store `jam_modifier: 0`. Displayed jam, and the `!bang` roll, are the level's base jam plus `jam_modifier`. A Gun Brush subtracts 10 points from that modifier. Using one while effective jam is already 0% is refused, and the brush stays in inventory. Do not delete `jam_modifier` from `duckhunt.json`.

## Persistence

Player stats are saved to `duckhunt.json`:

- **Per-channel stats** - Players have separate stats per channel (stored under `channels`).
- **Global top 5** - `!globaltop` aggregates XP across all channels.
- **Writes** - A save is built in memory, written to a temp file, and moved into place. The previous file becomes `.bak`, and the older backup becomes `.bak.1`. An unreadable database is not overwritten. Shutdown waits for the latest queued save. A crash before the background write finishes can lose that snapshot. Several saves during a slow write collapse to the newest one.

## Commands

All commands below use the default `!` prefix. This is configurable via `commands.prefix`
in `config.json` (see [Configuration](#configuration)) — if you set it to e.g. `@`, use
`@bang` instead of `!bang`, and so on for every command.

Hunting and shop commands work in a channel. In a private message they are refused, so they do not create a separate PM player. `!duckhelp` works in PM. Admin commands work in PM. In a channel, `!reload` reloads your gun. In PM, an admin's `!reload` restarts the process.

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
- `!shop buy <item_name|id> [target]` - Purchase an item (by exact name, id, or one unambiguous word)
- `!use <item_name|id> [target]` - Use an item from your inventory (same name matching)
- `!give <item_name|id> <player>` - Give an inventory item to another player (by name or ID)

### Admin Commands

- `!dh <on|off|status> [#channel]` - Enable or disable DuckHunt in a channel (per-channel toggle)
- `!rearm <player|all>` - In a channel, give that player (or everyone, or yourself if you omit the argument) a gun. In PM: `!rearm all` or `!rearm <#channel> <player>`
- `!disarm <player>` - Confiscate a player's gun
- `!ignore <player>` / `!unignore <player>` - Ignore/unignore commands from a player
- `!ducklaunch [duck_type]` - In a channel, force a duck (normal, golden, fast, ninja, flock). In PM: `!ducklaunch <#channel> [duck_type]`
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

1. Wait for a duck. With `config.json.example` that is every 20–60 minutes (1200–3600 seconds).
2. In the channel, type `!bang` to shoot it or `!bef` to befriend it.
3. Earn XP for successful hits or befriending.
4. Leveling makes the hunt harder. The accuracy bonus falls (from +25 to −30), the number of magazines you carry shrinks (from 3 to 1; each magazine is still 6 bullets), and base jam rises from 0% to 8%. Spawn timing does not change.
5. Buy items from `!shop`. A Gun Brush subtracts 10 jam points from `jam_modifier`. It does not change the level table.

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
├── src/
    ├── duckhuntbot.py   # IRC bot + command routing
    ├── game.py          # Duck game logic
    ├── db.py            # Database persistence
    ├── shop.py          # Shop/inventory system
    ├── levels.py        # Leveling system
    ├── sasl.py          # SASL authentication
    ├── error_handling.py # Error recovery
    ├── logging_utils.py # Logging setup
    └── utils.py         # Utility functions
└── tests/               # Offline unittest modules (no IRC, no real duckhunt.json)
```

Run the offline suite from the repo root:

```bash
python3 -m unittest discover -s tests
python3 -m py_compile src/*.py duckhunt.py
```

## Development & Improvements

### Recent Fixes & Enhancements

- **Per-Channel Game Toggle (`!dh on` / `!dh off` / `!dh status`)** - Bot admins can enable or disable DuckHunt on a per-channel basis. Duck spawning pauses and gameplay commands are blocked in disabled channels while admin commands remain active.
- **Persistent Channel Membership** - Channels joined via `!join` or parted via `!part` persist in the database, ensuring the bot automatically rejoins all active channels across IRC lag/ping timeouts and full bot restarts.
- **Item Name Support & Display Fixes** - Commands (`!use`, `!give`, `!shop buy`) now support full item names (e.g. `!use scope`, `!give bread Alice`), and all output across inventory, profile, and duck drops strictly uses real item names instead of generic item numbers.
- **Asynchronous Non-blocking Message Loops** - Converted the bot message-sending queue to run asynchronously, resolving loop blocking issues caused by synchronous message-throttling delay sleep.
- **Config & Duck Types Syncing** - Removed dead/unimplemented duck types from the configuration template and added proper config schema for the `ninja` duck.
- **Decoy Duck Removal** - Completely removed vestigial decoy duck references, comments, and handling branches from the shooting and befriending mechanics.
- **Redundant Schema Clean-up** - Stripped legacy player database fields (`ammo`, `max_ammo`, `chargers`) from defaults, creation, and runtime sanitization logic.
- **Configurable Command Prefix** - Added `commands.prefix` config option so the bot's trigger character (`!`, `@`, `$`, etc.) is no longer hardcoded. All help/usage text and message templates dynamically reflect the configured prefix.
- **Dead Decoy Message Strings Removed** - Cleaned up orphaned `decoy_duck_flies_away`/`bang_decoy`/`bef_decoy` message strings left over after decoy duck removal.
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
