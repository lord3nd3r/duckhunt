# DuckHunt Bot Configuration Guide

This document explains all configuration options in `config.json`.

## 📡 IRC Connection Settings (`connection`)

| Setting | Description | Example |
|---------|-------------|---------|
| `server` | IRC server hostname | `"irc.rizon.net"` |
| `port` | IRC server port | `6697` (SSL) or `6667` (non-SSL) |
| `nick` | Bot's nickname on IRC | `"DuckHunt"` |
| `channels` | List of channels to auto-join | `["#channel1", "#channel2"]` |
| `ssl` | Use SSL/TLS encryption | `true` (recommended) |
| `password` | Server password (I-Line auth) | Change if server requires auth |
| `max_retries` | Connection retry attempts | `3` |
| `retry_delay` | Seconds between retries | `5` |
| `timeout` | Connection timeout in seconds | `30` |

## 🔐 SASL Authentication (`sasl`)

SASL is used for NickServ authentication on connect.

| Setting | Description | Example |
|---------|-------------|---------|
| `enabled` | Enable SASL authentication | `false` |
| `username` | Registered nickname | `"your_registered_nick"` |
| `password` | NickServ password | `"your_nickserv_password"` |

**Note:** Change `password` from the default if enabling SASL!

## 👑 Bot Administration (`admins`)

Array of IRC nicknames with admin privileges.

```json
"admins": ["peorth", "computertech", "colby"]
```

## 🦆 Duck Spawning (`duck_spawning`)

| Setting | Description | Default |
|---------|-------------|---------|
| `spawn_min` | Minimum seconds between spawns | `10` |
| `spawn_max` | Maximum seconds between spawns | `30` |
| `timeout` | Global fallback timeout | `60` |
| `rearm_on_duck_shot` | Auto-rearm guns when duck shot | `true` |

## 🎯 Duck Types (`duck_types`)

### Normal Ducks (`normal`)
- `xp`: XP reward (default: `10`)
- `timeout`: Seconds before flying away (default: `60`)

### Golden Ducks (`golden`)
- `chance`: Spawn probability (default: `0.15` = 15%)
- `min_hp`: Minimum hit points (default: `3`)
- `max_hp`: Maximum hit points (default: `5`)
- `xp`: XP reward (default: `15`)
- `timeout`: Seconds before flying away (default: `60`)

### Fast Ducks (`fast`)
- `chance`: Spawn probability (default: `0.25` = 25%)
- `timeout`: Seconds before flying away (default: `20`)
- `xp`: XP reward (default: `12`)

**Note:** Chances are decimal percentages (0.15 = 15%, 0.25 = 25%)

## 👤 New Player Defaults (`player_defaults`)

Starting values for new players:

| Setting | Description | Default |
|---------|-------------|---------|
| `accuracy` | Starting accuracy percentage (0-100) | `75` |
| `magazines` | Starting number of magazines | `3` |
| `bullets_per_magazine` | Bullets per magazine | `6` |
| `jam_chance` | Gun jam percentage (0-100) | `15` |
| `xp` | Starting XP (also currency) | `0` |

## 🎮 Game Mechanics (`gameplay`)

| Setting | Description | Default |
|---------|-------------|---------|
| `befriend_success_rate` | Base befriend chance (%) | `75` |
| `befriend_xp` | XP from befriending | `5` |
| `accuracy_gain_on_hit` | Accuracy boost per hit | `1` |
| `accuracy_loss_on_miss` | Accuracy loss per miss | `2` |
| `min_accuracy` | Minimum accuracy limit | `10` |
| `max_accuracy` | Maximum accuracy limit | `100` |
| `min_befriend_success_rate` | Min befriend rate | `5` |
| `max_befriend_success_rate` | Max befriend rate | `95` |

## 🔧 Feature Toggles (`features`)

| Setting | Description | Default |
|---------|-------------|---------|
| `shop_enabled` | Enable shop system | `true` |
| `inventory_enabled` | Enable inventory system | `true` |
| `auto_rearm_enabled` | Enable auto gun rearming | `true` |

## ⚖️ System Limits (`limits`)

| Setting | Description | Default |
|---------|-------------|---------|
| `max_inventory_items` | Max items per player | `20` |
| `max_temp_effects` | Max temporary effects | `20` |

---

## 🔧 Configuration Access

The bot uses dot notation to access nested settings:

```python
# Examples:
server = bot.get_config('connection.server')
normal_xp = bot.get_config('duck_types.normal.xp')
player_accuracy = bot.get_config('player_defaults.accuracy')
```

## 📝 Tips

1. **Percentages:** Most percentage values use 0-100 scale, but spawn chances use 0.0-1.0 decimals
2. **Authentication:** Set real passwords when using server auth or SASL
3. **Balance:** Adjust XP rewards and duck spawn rates to balance gameplay
4. **Testing:** Change one setting at a time to test effects
5. **Backup:** Keep a backup of working config before major changes