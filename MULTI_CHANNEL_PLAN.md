# Multi-channel support

This note is historical. The behavior below is what the bot does now. It is not a list of remaining work.

## What it means

- Players have separate stats in each channel.
- Nick "Bob" in `#channel1` has different XP from "Bob" in `#channel2`.
- Database shape: `channels` → `#channel1` → `players` → `bob`.

```json
{
  "channels": {
    "#channel1": {
      "players": {
        "bob": { "xp": 100 },
        "alice": { "xp": 50 }
      }
    },
    "#channel2": {
      "players": {
        "bob": { "xp": 20 }
      }
    }
  }
}
```

## What the code does

- `get_player(nick, channel)` returns that channel's player.
- `get_players_for_channel(channel)` returns that channel's players.
- `iter_all_players()` walks every channel.
- Channel keys are lowercased. `#Channel` and `#channel` are the same bucket.
- `!duckstats` shows the current channel.
- `!topduck` is that channel's leaderboard.
- `!globaltop` is the top 5 by XP across channels. There is no `!globalducks` command.

Stats from one channel are not spent in another.
