# DuckHunt IRC Bot

A competitive IRC game bot implementing the classic DuckHunt mechanics with modern features. Players compete to shoot ducks that spawn in channels, managing ammunition, accuracy, and collecting items in a persistent progression system.

## Features

### Core Gameplay
- **Duck Spawning**: Ducks appear randomly in configured channels with ASCII art
- **Shooting Mechanics**: Players use `!bang` to shoot ducks with limited ammunition
- **Accuracy System**: Hit chances based on player skill that improves with successful shots
- **Gun Jamming**: Weapons can jam and require reloading based on reliability stats
- **Wild Shots**: Shooting without targets results in gun confiscation

### Progression System
- **Experience Points**: Earned from successful duck kills and befriending
- **Level System**: 40 levels with titles and increasing XP requirements
- **Statistics Tracking**: Comprehensive stats including accuracy, best times, and shot counts
- **Leaderboards**: Top player rankings and personal statistics

### Item System
- **Shop**: 8 different items available for purchase with earned money
- **Inventory**: Persistent item storage with quantity tracking
- **Item Effects**: Consumable and permanent items affecting gameplay
- **Competitive Drops**: Items drop to the ground for any player to grab with `!snatch`

### Gun Mechanics
- **Ammunition Management**: Limited shots per magazine with reloading required
- **Charger System**: Multiple magazines with reload mechanics
- **Gun Confiscation**: Administrative punishment system for wild shooting
- **Reliability**: Weapon condition affecting jam probability

## Installation

### Requirements
- Python 3.7 or higher
- asyncio support
- SSL/TLS support for secure IRC connections

### Setup
1. Clone the repository
2. Install Python dependencies (none required beyond standard library)
3. Copy and configure `config.json`
4. Run the bot

```bash
python3 duckhunt.py
```

## Configuration

The bot uses `config.json` for all configuration. Key sections include:

### IRC Connection
```json
{
    "server": "irc.example.net",
    "port": 6697,
    "nick": "DuckHunt",
    "channels": ["#games"],
    "ssl": true
}
```

### SASL Authentication
```json
{
    "sasl": {
        "enabled": true,
        "username": "bot_username",
        "password": "bot_password"
    }
}
```

### Game Settings
- Duck spawn intervals and timing
- Sleep hours when ducks don't spawn
- Duck type probabilities and rewards
- Shop item prices and effects

## Commands

### Player Commands
- `!bang` - Shoot at a duck
- `!reload` - Reload your weapon
- `!bef` / `!befriend` - Attempt to befriend a duck instead of shooting
- `!shop` - View available items for purchase
- `!duckstats` - View your personal statistics
- `!topduck` - View the leaderboard
- `!snatch` - Grab items dropped by other players
- `!use <item_number> [target]` - Use an item from inventory
- `!sell <item_number>` - Sell an item for half price

### Admin Commands
- `!rearm [player]` - Restore confiscated guns
- `!disarm <player>` - Confiscate a player's gun
- `!ducklaunch` - Force spawn a duck
- `!reset <player> [confirm]` - Reset player statistics

## Architecture

### Modular Design
- `duckhuntbot.py` - Main bot class and IRC handling
- `game.py` - Duck spawning and game mechanics
- `db.py` - Player data persistence and management
- `utils.py` - Input validation and IRC message parsing
- `sasl.py` - SASL authentication implementation
- `logging_utils.py` - Enhanced logging with rotation

### Database
Player data is stored in JSON format with automatic backups. The system handles:
- Player statistics and progression
- Inventory and item management
- Configuration and preferences
- Historical data and records

### Concurrency
Built on Python's asyncio framework for handling:
- IRC message processing
- Duck spawning timers
- Background cleanup tasks
- Multiple simultaneous players

## Duck Types

- **Normal Duck**: Standard rewards and difficulty
- **Fast Duck**: Higher XP but harder to hit
- **Rare Duck**: Bonus rewards and special drops
- **Boss Duck**: Challenging encounters with significant rewards

## Item Types

1. **Extra Shots** - Temporary ammunition boost
2. **Faster Reload** - Reduced reload time
3. **Accuracy Charm** - Permanent accuracy improvement
4. **Lucky Charm** - Increased rare duck encounters
5. **Friendship Bracelet** - Better befriending success rates
6. **Duck Caller** - Faster duck spawning
7. **Camouflage** - Temporary stealth mode
8. **Energy Drink** - Energy restoration

## Development

The codebase follows clean architecture principles with:
- Separation of concerns between IRC, game logic, and data persistence
- Comprehensive error handling and logging
- Input validation and sanitization
- Graceful shutdown handling
- Signal-based process management

### Adding Features
New features can be added by:
1. Extending the command processing in `duckhuntbot.py`
2. Adding game mechanics to `game.py`
3. Updating data structures in `db.py`
4. Configuring behavior in `config.json`