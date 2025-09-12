# 🦆 DuckHunt IRC Bot

A feature-rich IRC game bot where players hunt ducks, upgrade weapons, trade items, and compete on leaderboards!

## 🚀 Features

### 🎯 Core Game Mechanics
- **Different Duck Types**: Common, Rare, Golden, and Armored ducks with varying rewards
- **Weapon System**: Multiple weapon types (Basic Gun, Shotgun, Rifle) with durability
- **Ammunition Types**: Standard, Rubber Bullets, Explosive Rounds
- **Weapon Attachments**: Laser Sight, Extended Magazine, Bipod
- **Accuracy & Reliability**: Skill-based hit/miss and reload failure mechanics

### 🏦 Economy System
- **Shop**: Buy/sell weapons, attachments, and upgrades
- **Banking**: Deposit coins for interest, take loans
- **Trading**: Trade coins and items with other players
- **Insurance**: Protect your equipment from damage
- **Hunting Licenses**: Unlock premium features and bonuses

### 👤 Player Progression
- **Hunter Levels**: Gain XP and level up for better abilities
- **Account System**: Register accounts with password authentication
- **Multiple Auth Methods**: Nick-based, hostmask, or registered account
- **Persistent Stats**: All progress saved to SQLite database

### 🏆 Social Features
- **Leaderboards**: Compete for top rankings
- **Duck Alerts**: Get notified when rare ducks spawn
- **Sabotage**: Interfere with other players (for a cost!)
- **Comprehensive Help**: Detailed command reference

## 📋 Requirements

- Python 3.7+
- asyncio support
- SQLite3 (included with Python)

## 🛠️ Installation

1. Clone or download the bot files
2. Edit `config.json` with your IRC server details:
   ```json
   {
       "server": "irc.libera.chat",
       "port": 6697,
       "nick": "DuckHuntBot",
       "channels": ["#yourchannel"],
       "ssl": true,
       "sasl": false,
       "password": "",
       "duck_spawn_min": 60,
       "duck_spawn_max": 300
   }
   ```

3. Test the bot:
   ```bash
   python test_bot.py
   ```

4. Run the bot:
   ```bash
   python duckhunt.py
   ```

## 🎮 Commands

### 🎯 Hunting
- `!bang` - Shoot at a duck (accuracy-based hit/miss)
- `!reload` - Reload weapon (can fail based on reliability)
- `!catch` - Catch a duck with your hands
- `!bef` - Befriend a duck instead of shooting

### 🛒 Economy
- `!shop` - View available items
- `!buy <number>` - Purchase items
- `!sell <number>` - Sell equipment
- `!bank` - Banking services
- `!trade <player> <item> <amount>` - Trade with others

### 📊 Stats & Info
- `!stats` - Detailed combat statistics
- `!duckstats` - Personal hunting record
- `!leaderboard` - Top players ranking
- `!license` - Hunting license management

### ⚙️ Settings
- `!alerts` - Toggle duck spawn notifications
- `!help` - Complete command reference

### 🔐 Account System
- `/msg BotNick register <username> <password>` - Register account
- `/msg BotNick identify <username> <password>` - Login to account

### 🎮 Advanced
- `!sabotage <player>` - Sabotage another hunter's weapon

## 🗂️ File Structure

```
duckhunt/
├── src/
│   ├── duckhuntbot.py    # Main IRC bot logic
│   ├── game.py           # Game mechanics and commands
│   ├── db.py             # SQLite database handling
│   ├── auth.py           # Authentication system
│   ├── items.py          # Duck types, weapons, attachments
│   ├── logging_utils.py  # Colored logging setup
│   └── utils.py          # IRC message parsing
├── config.json           # Bot configuration
├── duckhunt.py          # Main entry point
├── test_bot.py          # Test script
└── README.md            # This file
```

## 🎯 Game Balance

### Duck Types & Rewards
- **Common Duck** 🦆: 1 coin, 10 XP (70% spawn rate)
- **Rare Duck** 🦆✨: 3 coins, 25 XP (20% spawn rate)  
- **Golden Duck** 🥇🦆: 10 coins, 50 XP (8% spawn rate)
- **Armored Duck** 🛡️🦆: 15 coins, 75 XP (2% spawn rate, 3 health)

### Weapon Stats
- **Basic Gun**: 0% accuracy bonus, 100 durability, 1 attachment slot
- **Shotgun**: -10% accuracy, 80 durability, 2 slots, spread shot
- **Rifle**: +20% accuracy, 120 durability, 3 slots

### Progression
- Players start with 100 coins and basic stats
- Level up by gaining XP from successful hunts
- Unlock better equipment and abilities as you progress

## 🔧 Configuration

Edit `config.json` to customize:
- IRC server and channels
- Duck spawn timing (min/max seconds)
- SSL and SASL authentication
- Bot nickname

## 🛡️ Security

- Passwords are hashed with PBKDF2
- Account data stored separately from temporary nick data
- Multiple authentication methods supported
- Database uses prepared statements to prevent injection

## 🐛 Troubleshooting

1. **Bot won't connect**: Check server/port in config.json
2. **Database errors**: Ensure write permissions in bot directory
3. **Commands not working**: Verify bot has joined the channel
4. **Test failures**: Run `python test_bot.py` to diagnose issues

## 🎖️ Contributing

Feel free to add new features:
- More duck types and weapons
- Additional mini-games
- Seasonal events
- Guild/team systems
- Advanced trading mechanics

## 📄 License

This bot is provided as-is for educational and entertainment purposes.

---

🦆 **Happy Hunting!** 🦆
