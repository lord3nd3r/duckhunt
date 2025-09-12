# DuckHunt Bot Configuration Guide

This document explains all the configuration options available in `config.json` to customize your DuckHunt bot experience.

## Basic IRC Settings
```json
{
    "server": "irc.rizon.net",     // IRC server hostname
    "port": 6697,                  // IRC server port (6667 for non-SSL, 6697 for SSL)
    "nick": "DuckHunt",            // Bot's nickname
    "channels": ["#channel"],      // List of channels to join
    "ssl": true,                   // Enable SSL/TLS connection
    "password": "",                // Server password (if required)
    "admins": ["nick1", "nick2"]   // List of admin nicknames
}
```

## SASL Authentication
```json
"sasl": {
    "enabled": true,               // Enable SASL authentication
    "username": "botaccount",      // NickServ account username
    "password": "botpassword"      // NickServ account password
}
```

## Duck Spawning Configuration
```json
"duck_spawn_min": 1800,           // Minimum time between duck spawns (seconds)
"duck_spawn_max": 5400,           // Maximum time between duck spawns (seconds)
"duck_timeout_min": 45,           // Minimum time duck stays alive (seconds)
"duck_timeout_max": 75,           // Maximum time duck stays alive (seconds)
"sleep_hours": [],                // Hours when no ducks spawn [start_hour, end_hour]
"max_ducks_per_channel": 3,       // Maximum ducks that can exist per channel
```

### Duck Types
Configure different duck types with spawn rates and rewards:
```json
"duck_types": {
    "normal": {
        "enabled": true,           // Enable this duck type
        "spawn_rate": 70,          // Percentage chance to spawn (out of 100)
        "xp_reward": 10,           // XP gained when caught
        "health": 1                // How many hits to kill
    },
    "golden": {
        "enabled": true,
        "spawn_rate": 8,
        "xp_reward": 50,
        "health": 1
    }
    // ... more duck types
}
```

## Duck Befriending System
```json
"befriending": {
    "enabled": true,               // Enable !bef command
    "base_success_rate": 65,       // Base chance of successful befriend (%)
    "max_success_rate": 90,        // Maximum possible success rate (%)
    "level_bonus_per_level": 2,    // Success bonus per player level (%)
    "level_bonus_cap": 20,         // Maximum level bonus (%)
    "luck_bonus_per_point": 3,     // Success bonus per luck point (%)
    "xp_reward": 8,                // XP gained on successful befriend
    "xp_reward_min": 1,            // Minimum XP from befriending
    "xp_reward_max": 3,            // Maximum XP from befriending
    "failure_xp_penalty": 1,       // XP lost on failed befriend
    "scared_away_chance": 10,      // Chance duck flies away on failure (%)
    "lucky_item_chance": 5         // Base chance for lucky item drops (%)
}
```

## Shooting Mechanics
```json
"shooting": {
    "enabled": true,               // Enable !bang command
    "base_accuracy": 85,           // Starting player accuracy (%)
    "base_reliability": 90,        // Starting gun reliability (%)
    "jam_chance_base": 10,         // Base gun jam chance (%)
    "friendly_fire_enabled": true, // Allow shooting other players
    "friendly_fire_chance": 5,     // Chance of friendly fire (%)
    "reflex_shot_bonus": 5,        // Bonus for quick shots (%)
    "miss_xp_penalty": 5,          // XP lost on missed shot
    "wild_shot_xp_penalty": 10,    // XP lost on wild shot
    "teamkill_xp_penalty": 20      // XP lost on team kill
}
```

## Weapon System
```json
"weapons": {
    "enabled": true,               // Enable weapon mechanics
    "starting_weapon": "pistol",   // Default weapon for new players
    "starting_ammo": 6,            // Starting ammo count
    "max_ammo_base": 6,            // Base maximum ammo capacity
    "starting_chargers": 2,        // Starting reload items
    "max_chargers_base": 2,        // Base maximum reload items
    "durability_enabled": true,    // Enable weapon wear/breaking
    "confiscation_enabled": true   // Allow admin gun confiscation
}
```

## Economy System
```json
"economy": {
    "enabled": true,               // Enable coin/shop system
    "starting_coins": 100,         // Coins for new players
    "shop_enabled": true,          // Enable !shop command
    "trading_enabled": true,       // Enable !trade command
    "theft_enabled": true,         // Enable !steal command
    "theft_success_rate": 30,      // Chance theft succeeds (%)
    "theft_penalty": 50,           // Coins lost if theft fails
    "banking_enabled": true,       // Enable banking system
    "interest_rate": 5,            // Bank interest rate (%)
    "loan_enabled": true           // Enable loan system
}
```

## Player Progression
```json
"progression": {
    "enabled": true,               // Enable XP/leveling system
    "max_level": 40,               // Maximum player level
    "xp_multiplier": 1.0,          // Global XP multiplier
    "level_benefits_enabled": true, // Level bonuses (accuracy, etc.)
    "titles_enabled": true,        // Show player titles
    "prestige_enabled": false      // Enable prestige system
}
```

## Karma System
```json
"karma": {
    "enabled": true,               // Enable karma tracking
    "hit_bonus": 2,                // Karma for successful shots
    "golden_hit_bonus": 5,         // Karma for golden duck hits
    "teamkill_penalty": 10,        // Karma lost for team kills
    "wild_shot_penalty": 3,        // Karma lost for wild shots
    "miss_penalty": 1,             // Karma lost for misses
    "befriend_success_bonus": 2,   // Karma for successful befriends
    "befriend_fail_penalty": 1     // Karma lost for failed befriends
}
```

## Items and Powerups
```json
"items": {
    "enabled": true,               // Enable item system
    "lucky_items_enabled": true,   // Enable lucky item drops
    "lucky_item_base_chance": 5,   // Base lucky item chance (%)
    "detector_enabled": true,      // Enable duck detector item
    "silencer_enabled": true,      // Enable silencer item
    "sunglasses_enabled": true,    // Enable sunglasses item
    "explosive_ammo_enabled": true, // Enable explosive ammo
    "sabotage_enabled": true,      // Enable sabotage mechanics
    "insurance_enabled": true,     // Enable insurance system
    "decoy_enabled": true          // Enable decoy ducks
}
```

## Social Features
```json
"social": {
    "leaderboards_enabled": true,  // Enable !top command
    "duck_alerts_enabled": true,   // Enable duck spawn notifications
    "private_messages_enabled": true, // Allow PM commands
    "statistics_sharing_enabled": true, // Enable !stats sharing
    "achievements_enabled": false  // Enable achievement system
}
```

## Moderation Features
```json
"moderation": {
    "ignore_system_enabled": true, // Enable !ignore command
    "rate_limiting_enabled": true, // Prevent command spam
    "rate_limit_cooldown": 2.0,    // Seconds between commands
    "admin_commands_enabled": true, // Enable admin commands
    "ban_system_enabled": true,    // Enable player banning
    "database_reset_enabled": true, // Allow database resets
    "admin_rearm_gives_full_ammo": true,     // Admin !rearm gives full ammo
    "admin_rearm_gives_full_chargers": true  // Admin !rearm gives full chargers
}
```

## Advanced Features
```json
"advanced": {
    "gun_jamming_enabled": true,   // Enable gun jam mechanics
    "weather_effects_enabled": false, // Weather affecting gameplay
    "seasonal_events_enabled": false, // Special holiday events
    "daily_challenges_enabled": false, // Daily quest system
    "guild_system_enabled": false, // Player guilds/teams
    "pvp_enabled": false           // Player vs player combat
}
```

## Message Customization
```json
"messages": {
    "custom_duck_messages_enabled": true, // Varied duck spawn messages
    "color_enabled": true,         // IRC color codes in messages
    "emoji_enabled": true,         // Unicode emojis in messages
    "verbose_messages": true,      // Detailed action messages
    "success_sound_effects": true  // Text sound effects
}
```

## Database Settings
```json
"database": {
    "auto_save_enabled": true,     // Automatic database saving
    "auto_save_interval": 300,     // Auto-save every N seconds
    "backup_enabled": true,        // Create database backups
    "backup_interval": 3600,       // Backup every N seconds
    "compression_enabled": false   // Compress database files
}
```

## Debug Options
```json
"debug": {
    "debug_mode": false,           // Enable debug features
    "verbose_logging": false,      // Extra detailed logs
    "command_logging": false,      // Log all commands
    "performance_monitoring": false // Track performance metrics
}
```

## Configuration Tips

1. **Duck Spawn Timing**: Adjust `duck_spawn_min/max` based on channel activity
2. **Difficulty**: Lower `befriending.base_success_rate` for harder gameplay
3. **Economy**: Adjust XP rewards to balance progression
4. **Features**: Disable unwanted features by setting `enabled: false`
5. **Performance**: Enable rate limiting and disable verbose logging for busy channels
6. **Testing**: Use debug mode and shorter spawn times for testing

## Example Configurations

### Casual Server (Easy)
```json
"befriending": {
    "base_success_rate": 80,
    "max_success_rate": 95
},
"economy": {
    "starting_coins": 200
}
```

### Competitive Server (Hard)
```json
"befriending": {
    "base_success_rate": 45,
    "max_success_rate": 75
},
"shooting": {
    "base_accuracy": 70,
    "friendly_fire_chance": 10
}
```

### Minimal Features
```json
"befriending": { "enabled": false },
"items": { "enabled": false },
"karma": { "enabled": false },
"social": { "leaderboards_enabled": false }
```
