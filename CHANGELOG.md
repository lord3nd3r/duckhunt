# Changelog

## 2026-11-06

Fixes on top of the 2026-11-05 source snapshot. Nothing here was deployed, and no live database was opened. Restart the bot yourself after you copy these files in.

### Gun jam and Gun Brush

`!bang` rolled the level's jam chance and ignored the jam value Gun Brush writes. `!duckstats` and `!profile` showed that stored value, so a hunter could read 0% and still jam.

The level table is the base chance. Brushes, jam resistance, and sabotage are a permanent `jam_modifier` (percentage points, negative when the gun is cleaner). The number shown in stats is the number the shot rolls. 0 does not roll, so it cannot jam.

Existing saves are migrated once:

- A stored jam of 15 (the old default, never brushed) becomes modifier 0. The hunter jams at the level rate. Level 1 is 0%, level 6 is 5%, level 8 is 8%.
- Each point the stored value sits below 15 is kept. A Gun Brush is -10, so one brush zeros every current level (the highest base is 8). A stored 0, the old clamp after cleaning, becomes -15 and also jams at 0%.
- After `jam_modifier` is saved, later writes of the displayed percentage do not get migrated again.

A brush bought while the effective chance is already 0 is refused and stays in inventory. A brush used while there is jam to remove keeps the full -10 across level-ups and restarts, even when the current level cannot show all 10 points.

### Other corrections

- `!duckhelp` no longer sleeps inside the read loop. Outgoing chat is still paced, and the pending chat queue stops at 100 messages. PING, PONG, JOIN, and QUIT are not part of that queue.
- `!profile`, `!achievements`, `!topduck`, `!globaltop`, `!duckstats`, `!duckhelp`, `!inv`, `!effects`, `!give`, and `!daily` use the same rate limit as `!bang` and `!shop`. A denied command sends one slow-down notice per refill window.
- Startup no longer treats "stat failed" as "file missing". An unreadable or directory path is left in place and saves are refused. A missing file is still created, with `O_EXCL`, for a first run. Corrupt JSON is copied aside, then `.bak` and `.bak.1` are tried. A new empty file replaces the primary only after that copy succeeded.
- Saves keep the newest snapshot when the disk is slow, write it through a unique temp file, and publish `.bak` by rename. The previous backup is kept as `.bak.1`. A directory fsync is attempted and ignored when the filesystem rejects it.
- `get_player` updates the existing player dict instead of swapping it out. Unknown small fields are kept. Temporary effects keep the newest 20, so a purchase at the cap is not dropped on the next save.
- Admin hostmasks keep `~` and `/`. Channel targets keep `.` (`#foo.bar`). Controls, spaces, commas, and colons are still stripped.
- `!use` / `!shop` match an id, the full name, or one unambiguous whole word. `!shop buy a` no longer buys a Magazine. `!use gun brush` uses the brush. `!use gun brush bob` still takes a target. `find_item("²")` does not raise.
- Shop inventory limits come from the config object the bot already loaded.
- On disconnect, joined channels are remembered and then cleared, spawn loops stop, and ducks from the dead connection are dropped. Reconnect delay goes back to the initial delay only after a session that received `001`. A TCP connect that dies during registration backs off.
- Spawn and duck-timeout loops log a bad iteration and continue. Cancellation still stops them.
- A hunter is "active" only with XP, shots, ducks, or shop spend. Having `current_ammo` on every saved record no longer counts. This is not an account-age rule.
- The bot's own `NICK` updates `current_nick`. `PING` without a colon is echoed. A replacement rejoin task is not removed by the task it cancelled.
- Outgoing IRC text is split on UTF-8 bytes so a line stays within 512 bytes. Notices are split the same way instead of being cut at 400 characters.
- `!duckstats` keeps the colour number (green stays colour 3).
- `!give` no longer prefixes the nick twice when the target is missing.
- Private messages reply to the sender. `!bang`, `!shop`, and the other game commands in PM are refused and do not create a `__pm__` player. `!duckhelp` and the existing admin PM commands still run.
- A multi-line `CAP LS` is collected before deciding SASL is missing. Numeric `907` does not send `NICK`/`USER` again when the bot is already registered.
- A second shutdown signal does not cancel tasks that are already flushing the database.

### Reviewed and not changed

- `duck_spawn_speed_modifier` is in the level table and has a helper, but nothing applies it to the spawn timer. The descriptions say higher levels see fewer ducks; the number itself does not say whether it multiplies the wait or the rate. Spawn timing is unchanged.

## 2026-11-06 — Item drops for every duck type

A killing shot rolls one item drop. Normal, fast, golden, and ninja keep the drop chance and table in config when that type has a non-empty `*_duck_drops` list (`config.json.example`: normal 15%, fast 25%, ninja 30%, golden 50%). Flock members have no table. Each member you kill uses the normal duck's chance and `normal_duck_drops`. Any other type without a table does the same, so a missing table is not a silent 0% drop. A type that has a table and `drop_chance` 0 still never drops.

Misses, ninja dodges, befriends, fly-aways, and a golden duck's non-killing hits do not drop. One flock is not one shared roll: each member is one shot and at most one item. The same inventory total cap as before still applies. No config edit is required for a bot whose config already has normal duck drops.
- `!daily` remains a 24 hour cooldown. The streak still uses the local calendar date, and the per-day bonus is still unbounded. The help text already says 24 hours.
- Nicks and channels still use `str.lower()`. RFC 1459 casemapping would risk merging or splitting saved names, so there is no automatic rewrite.
- No player-count check was added to block saves. A small or empty database can be legitimate.

### Saved fields

New players store `jam_modifier: 0` next to the old `jam_chance`. After the first load of an old record, `jam_modifier` is the value that matters. `jam_chance` may also be rewritten to the effective percentage when a brush or sabotage is used. Do not delete `jam_modifier` from `duckhunt.json`.
