# Multi-Channel Support Implementation

## What Multi-Channel Means
- Players have **separate stats in each channel**
- Nick "Bob" in #channel1 has different XP than "Bob" in #channel2
- Database structure: `channels -> #channel1 -> players -> bob`

## Changes Needed

### 1. Database Structure (db.py)
```python
{
  "channels": {
    "#channel1": {
      "players": {
        "bob": { "xp": 100, ... },
        "alice": { "xp": 50, ... }
      }
    },
    "#channel2": {
      "players": {
        "bob": { "xp": 20, ... }  # Different stats!
      }
    }
  }
}
```

### 2. Database Methods
- `get_player(nick, channel)` - Get player in specific channel
- `get_players_for_channel(channel)` - Get all players in a channel
- `iter_all_players()` - Iterate over all channels and players

### 3. Command Changes (duckhuntbot.py)
- Pass `channel` parameter when calling `db.get_player(nick, channel)`
- Channel normalization (case-insensitive)

### 4. Stats Commands
- `!duckstats` shows stats for current channel
- `!globalducks` shows combined stats across all channels

## Benefits
- Fair: Can't bring channel1 XP into channel2
- Better: Each channel has own leaderboard
- Clean: Stats don't mix between channels
