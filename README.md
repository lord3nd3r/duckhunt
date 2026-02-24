# DuckHunt IRC Bot

DuckHunt is an asyncio-based IRC bot that runs a classic "duck hunting" mini-game in IRC channels.

## Credits

- Originally written by **Computertech**
- New features, fixes, and maintenance added by **End3r**

## Features

- **Multi-channel support** - Bot can be in multiple channels
- **Per-channel player stats** - Stats are tracked separately per channel
- **Global leaderboard** - View the global top 5 across all channels
- **Dynamic Weather System** - Weather changes (Clear, Rain, Fog, Storm) affecting accuracy, jam chance, XP, and duck flight time. Weather now rotates silently per-channel and is shown when a duck is spawned or when queried with `!weather`.
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

## Shop Item Reference (Detailed)

This section documents every shop item, its cost, and exactly what it does and how to use it.

- ID 1 — Single Bullet (5 XP)
    - Type: `ammo` — Adds 1 bullet to your current magazine (up to magazine capacity).
    - Usage: Buy and `!use 1` to add bullets immediately, or `!shop 1` then store to inventory.
    - Notes: Fails if magazine is already full.

- ID 2 — Magazine (15 XP)
    - Type: `magazine` — Adds 1 spare magazine to your inventory (subject to per-level max).
    - Usage: Buy and `!use 2` to consume from inventory or buy with storage.
    - Notes: Cannot exceed level-based magazine limit; `ShopManager` enforces max per-level.

- ID 3 — Sand (10 XP)
    - Type: `sabotage_jam` — Increases a target player's jam chance by 15% and applies a temporary effect.
    - Usage: Must be used with a target (cannot be applied to yourself). Buy then `!use 3 <player>` or purchase with target.
    - Notes: Effect is temporary and tracked in `temporary_effects` on the target.

- ID 4 — Gun Brush (20 XP)
    - Type: `clean_gun` — Reduces the purchaser's jam chance by 10%.
    - Usage: Buy and auto-applies to buyer (or store and `!use 4`).

- ID 5 — Bread (50 XP)
    - Type: `attract_ducks` — Temporarily increases duck spawn rate (default 2x) for the configured duration (default 20 minutes).
    - Usage: Buy and apply — effect added to buyer's `temporary_effects`. Bread affects the whole channel and cannot target a single player.

- ID 6 — Hunter's Insurance (75 XP)
    - Type: `insurance` — Grants protection from friendly-fire penalties for 24 hours (default); prevents XP loss and gun confiscation from friendly fire.
    - Usage: Buy and auto-applies as a temporary effect on purchaser.

- ID 7 — Buy Gun Back (40 XP)
    - Type: `buy_gun_back` — If your gun is confiscated, restores it along with the ammo/magazines that were held when confiscated.
    - Usage: Buy to immediately restore if `gun_confiscated` is true; otherwise returns a helpful message.

- ID 8 — Bucket of Water (25 XP)
    - Type: `splash_water` — Soaks a target player; adds a `wet_clothes` temporary effect preventing them from shooting until they dry or use `Dry Clothes`.
    - Usage: Must target a player (`!use 8 <player>`). Duration is configured (default 5 minutes).

- ID 9 — Dry Clothes (30 XP)
    - Type: `dry_clothes` — Removes `wet_clothes` effects from the purchaser, allowing shooting again.
    - Usage: Buy and use on yourself (or store/use from inventory).

- ID 10 — 4-Leaf Clover (250 XP)
    - Type: `clover_luck` — Strong temporary boost: sets minimum hit and befriend chances (defaults to 95%) for its duration (default 10 minutes).
    - Usage: Buy and it adds/extends a `clover_luck` temporary effect on the purchaser.
    - Notes: Buying additional clovers extends the active duration and preserves the best min-chance values.

- ID 11 — Binoculars (30 XP)
    - Type: `reveal_duck` — Reveals the current duck type in the channel via PM to the purchaser.
    - Usage: Buy then use to receive a private reveal message.

- ID 12 — Hunting Dog (80 XP)
    - Type: `second_chance` — Adds a temporary `second_chance` effect; when the next duck flies away, the dog retrieves and re-spawns it immediately.
    - Usage: Buy and auto-applies as a temporary effect (duration default 1 hour).

- ID 13 — Scope (60 XP)
    - Type: `temporary_accuracy` — Grants +20% accuracy for the purchaser for the next 5 shots (configurable); effect expires after a duration if unused.
    - Usage: Buy and apply; tracked under `temporary_effects` with `shots_remaining`.

- ID 14 — Body Armor (100 XP)
    - Type: `xp_shield` — Absorbs one XP-loss event (e.g., miss penalty, friendly fire) while active; duration default 24 hours.
    - Usage: Buy to add `xp_shield` to purchaser's temporary effects.

- ID 15 — Decoy Trap (45 XP)
    - Type: `trap` — Plant on a player so their next `!bef` attempt fails and causes an XP penalty.
    - Usage: Must target another player when used or purchased for gifting. Traps expire after their duration.

- ID 16 — Mystery Box (35 XP)
    - Type: `mystery` — Consumes the box and randomly awards one item/effect from a weighted pool defined in `shop.json`.
    - Usage: Buy and open (`!use 16`) to receive a random inner item; inner effects apply immediately.

Notes on inventory and limits:
- Items can be purchased and either applied immediately or stored in inventory if supported. `ShopManager` enforces `max_per_item` and `max_total_items` limits loaded from `config.json` (defaults apply if config missing).
- Many items add entries to a player's `temporary_effects` list; those effects are time-limited and code checks `expires_at` before applying bonuses/penalties.
- Harmful items (e.g., `Sand`, `Bucket of Water`) require a target and will fail if no valid target is provided.

*** Happy Hunting! ***

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
- ✅ Weather rotations are now silent (no periodic broadcast); current weather is appended to duck spawn messages and still available via `!weather`.
- ✅ Spawn messages favour an ornate prefix template for consistent appearance when ducks are spawned or admin-launched.
- ✅ Added 15 milestone achievements for players to unlock.
- ✅ Added Ninja, Boss, Decoy ducks and Flock spawning.
- ✅ Added `!daily`, `!profile`, `!inv`, `!effects`, `!weather`, `!achievements` commands.
- ✅ Added 6 new shop items (Binoculars, Hunting Dog, Scope, Body Armor, Decoy Trap, Mystery Box).

**Happy Duck Hunting!** 🦆