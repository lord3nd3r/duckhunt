# DuckHunt IRC Bot

DuckHunt is an asyncio-based IRC bot that runs a classic "duck hunting" mini-game in IRC channels.

## Credits

- Originally written by **Computertech**
- New features, fixes, and maintenance added by **End3r**

## Features

- **Multi-channel support** - Bot can be in multiple channels
- **Per-channel player stats** - Stats are tracked separately per channel
- **Global leaderboard** - View the global top 5 across all channels
- **Dynamic Weather System** - Weather changes (Clear, Rain, Fog, Storm) affecting accuracy, jam chance, XP, and duck flight time.
- **Achievement System** - Earn badges for milestones (e.g., First Blood, Sharpshooter, Boss Slayer).
- **Six Duck Types & Flocks** - Normal, Golden, Fast, Ninja, Decoy, Boss, and multiple ducks spawning at once.
- **Shop system** - Buy items, use them, or gift them to others.
- **Leveling system** - Gain XP, increase your level, and unlock permanent upgrades.
- **JSON persistence & Auto-save** - All stats saved to disk automatically after each action.

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

Six duck types with different behaviors:

- **Normal** - Standard duck, 1 HP, base XP.
- **Golden** - Multi-HP duck (3-5 HP), high XP, awards XP per hit.
- **Fast** - Quick duck, 1 HP, flies away faster.
- **Ninja** - Has a permanent dodge chance making it harder to hit.
- **Boss** - Massive HP, requires cooperative shooting. XP split proportionally.
- **Decoy** - Shooting it gets your gun confiscated, but befriending it gives a reward.
- **Flock** - 2-4 normal ducks spawn at the exact same time!

Duck spawn behavior is configured in `config.json` under `duck_types`.

## Persistence

Player stats are saved to `duckhunt.json`:

- **Per-channel stats** - Players have separate stats per channel (stored under `channels`).
- **Global top 5** - `!globaltop` aggregates XP across all channels.
- **Atomic writes & Retry logic** - Safe file handling prevents database corruption.

## Commands

### Player Commands

- `!bang` - Shoot at a duck
- `!bef` or `!befriend` - Try to befriend a duck
- `!reload` - Reload your gun
- `!daily` - Claim your daily XP bonus (resets every 24h, builds streaks)
- `!duckstats [player]` - View hunting statistics for the current channel
- `!profile` - Get a detailed hunter stat card sent to your PM
- `!weather` - Check current weather conditions and modifiers in the channel
- `!topduck` - View leaderboard (top hunters in the channel)
- `!globaltop` - View global leaderboard (top 5 across all channels)
- `!achievements` - Check your earned badges (sent via PM)
- `!effects` - View any active temporary buffs/debuffs and their timers
- `!inv` - Quick inline view of your inventory
- `!duckhelp` - Get detailed command list via PM

### Shop Commands

- `!shop` - View available items
- `!shop buy <item_id>` - Purchase an item from the shop
- `!use <item_id> [target]` - Use an item from your inventory
- `!give <item_id> <player>` - Give an inventory item to another player

### Admin Commands

- `!rearm <player|all>` - Give player a gun
- `!disarm <player>` - Confiscate player's gun
- `!ignore <player>` / `!unignore <player>` - Ignore/unignore commands
- `!ducklaunch [duck_type]` - Force spawn a duck (normal, golden, fast, ninja, boss, decoy)
- `!join <#channel>` - Make bot join a channel
- `!part <#channel>` - Make bot leave a channel
- `!reload` (in PM) - Restarts the bot process smoothly

Admin commands work in PM or in-channel (except bot restart).

## Shop Items

Basic shop items available (use `!shop` to see current inventory and IDs):

- **Ammunition & Magazines** - Ensure you never run out of bullets.
- **Gun Brush & Sand** - Clean your gun or sabotage someone else's.
- **Bread** - Increases duck spawn rate significantly.
- **Insurance & Body Armor** - Prevents XP loss or friendly fire penalties.
- **Buy Gun Back** - Skip the penalty and recover your confiscated weapon.
- **Water & Clothes** - Soak someone to prevent shooting, or dry yourself off.
- **Binoculars** - Peek at what type of duck is currently active in the channel (PM).
- **Hunting Dog** - Retrieves the next duck that flies away and gives you a second chance.
- **Scope** - Provides a massive accuracy boost for your next 5 shots.
- **Decoy Trap** - Plant on a player to ruin their next `!bef` attempt and make them lose XP.
- **Mystery Box** - Open a random box for a chance at high-tier items.

## Gameplay

### How to Play

1. Wait for a duck to spawn (appears randomly in channel).
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

## Recent Updates

- ✅ Refactored and fixed codebase logic bugs, dead code, and admin hostmask auth routing.
- ✅ Added 4 dynamic weather states (Clear, Rain, Fog, Storm) changing gameplay odds.
- ✅ Added 15 milestone achievements for players to unlock.
- ✅ Added Ninja, Boss, Decoy ducks and Flock spawning.
- ✅ Added `!daily`, `!profile`, `!inv`, `!effects`, `!weather`, `!achievements` commands.
- ✅ Added 6 new shop items (Binoculars, Hunting Dog, Scope, Body Armor, Decoy Trap, Mystery Box).

**Happy Duck Hunting!** 🦆