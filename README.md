# DuckHunt IRC Bot

A feature-rich IRC bot that brings the classic duck hunting game to your IRC channels. Players can shoot, befriend, and collect various types of ducks while managing their equipment and competing for high scores.

## ✨ Features

- **Multiple Duck Types**: Normal, Golden (high HP), and Fast (quick timeout) ducks
- 🎯 **Accuracy System**: Dynamic accuracy that improves with hits and degrades with misses
- **Weapon Management**: Magazines, bullets, and gun jamming mechanics
- 🛒 **Shop System**: Buy equipment and items with XP (currency)
- 🎒 **Inventory System**: Collect and use various items (bread, grease, sights, etc.)
- 👥 **Player Statistics**: Track shots, hits, misses, and best times
- **Fully Configurable**: Every game parameter can be customized via config
- 🔐 **Authentication**: Support for both server passwords and SASL/NickServ auth
- 📊 **Admin Commands**: Comprehensive bot management and player administration

## 🚀 Quick Start

### Prerequisites

- Python 3.8+ 
- Virtual environment (recommended)

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/duckhunt-bot.git
   cd duckhunt-bot
   ```

2. **Set up virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Linux/Mac
   # or
   .venv\Scripts\activate     # Windows
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure the bot:**
   - Copy `config.json.example` to `config.json` (if available)
   - Edit `config.json` with your IRC server settings
   - See [CONFIG.md](CONFIG.md) for detailed configuration guide

5. **Run the bot:**
   ```bash
   python duckhunt.py
   ```

## ⚙️ Configuration

The bot uses a nested JSON configuration system. Key settings include:

### Connection Settings
```json
{
  "connection": {
    "server": "irc.your-server.net",
    "port": 6697,
    "nick": "DuckHunt",
    "channels": ["#your-channel"],
    "ssl": true
  }
}
```

### Duck Types & Rewards
```json
{
  "duck_types": {
    "normal": { "xp": 10, "timeout": 60 },
    "golden": { "chance": 0.15, "min_hp": 3, "max_hp": 5, "xp": 15 },
    "fast": { "chance": 0.25, "timeout": 20, "xp": 12 }
  }
}
```

**📖 See [CONFIG.md](CONFIG.md) for complete configuration documentation.**

## 🎮 Game Commands

### Player Commands
- `!shoot` - Shoot at a duck
- `!reload` - Reload your weapon
- `!befriend` - Try to befriend a duck
- `!stats [player]` - View player statistics
- `!shop` - View the shop
- `!buy <item>` - Purchase an item
- `!inventory` - Check your inventory
- `!use <item>` - Use an item from inventory

### Admin Commands  
- `!spawn` - Manually spawn a duck
- `!give <player> <item> <quantity>` - Give items to players
- `!setstat <player> <stat> <value>` - Modify player stats
- `!reload_config` - Reload configuration without restart

## Duck Types

| Type | Spawn Rate | HP | Timeout | XP Reward |
|------|------------|----|---------|-----------| 
| Normal | 60% | 1 | 60s | 10 |
| Golden | 15% | 3-5 | 60s | 15 |
| Fast | 25% | 1 | 20s | 12 |

## 🛒 Shop Items

- **Bread** - Attracts more ducks
- **Gun Grease** - Reduces jam chance
- **Sight** - Improves accuracy
- **Silencer** - Enables stealth shooting
- **Explosive Ammo** - Extra damage
- **Lucky Charm** - Increases rewards
- **Duck Detector** - Reveals duck locations

## 📁 Project Structure

```
duckhunt/
├── duckhunt.py          # Main bot entry point
├── config.json          # Bot configuration
├── CONFIG.md           # Configuration documentation
├── src/
│   ├── duckhuntbot.py  # Core bot IRC functionality
│   ├── game.py         # Duck game mechanics
│   ├── db.py           # Player database management
│   ├── shop.py         # Shop system
│   ├── levels.py       # Player leveling system
│   ├── sasl.py         # SASL authentication
│   └── utils.py        # Utility functions
├── shop.json           # Shop item definitions
├── levels.json         # Level progression data
├── messages.json       # Bot response messages
└── duckhunt.json       # Player database
```

## Development

### Adding New Features

The bot is designed with modularity in mind:

1. **Game mechanics** are in `src/game.py`
2. **IRC functionality** is in `src/duckhuntbot.py`  
3. **Database operations** are in `src/db.py`
4. **Configuration** uses dot notation: `bot.get_config('duck_types.normal.xp')`

### Testing Configuration

Use the built-in config tester:
```bash
python test_config.py
```

## 🛠️ Troubleshooting

### Common Issues

1. **Connection fails**: Check server, port, and SSL settings in config
2. **SASL authentication fails**: Verify username/password and ensure nick is registered
3. **Bot doesn't respond**: Check channel permissions and admin list
4. **Config errors**: Validate JSON syntax and see CONFIG.md for proper values

### Debug Mode

Enable detailed logging by setting log level in the code or add verbose output.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Add tests if applicable
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Inspired by classic IRC duck hunting bots
- Built with Python's asyncio for modern async IRC handling
- Thanks to all contributors and testers

## 📞 Support

- 📖 **Documentation**: See [CONFIG.md](CONFIG.md) for configuration help
- 🐛 **Issues**: Report bugs via GitHub Issues
- 💬 **Discussion**: Join our IRC channel for help and discussion

---

**Happy Duck Hunting!**