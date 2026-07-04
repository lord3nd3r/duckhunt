# DuckHunt Code Review — TODO

Findings from full codebase review (2026-07-03). Checked items are fixed.

## 🔴 Critical

- [x] **SASL logs plaintext password/auth data** — `src/sasl.py`. Removed all logging of the
      password, auth string, its length, and any base64 blob prefix. Verified with a mock-bot
      test covering the full CAP→AUTHENTICATE→903 flow.
- [x] **SSL certificate validation unconditionally disabled** — `src/duckhuntbot.py` `connect()`.
      Certificate verification is now ON by default; only disabled via an explicit
      `connection.ssl_verify: false` config flag (documented in `config.json.example` and
      README), with a warning logged when it's turned off.
- [x] **Blocking sync I/O on the event loop on every game action** — `src/db.py`
      `save_database()`. Now builds the sanitized save payload synchronously (fast, in-memory)
      then hands the actual disk write - including retry backoff and `os.fsync` - to a
      dedicated single-worker background thread, so it can never block the asyncio event loop.
      Added `flush_pending_saves()`, called during shutdown/before an `os.execv` restart in
      `duckhuntbot.py`, so the final save can never be lost to a killed thread. All ~20
      existing call sites needed zero changes (public sync API unchanged). Verified with a
      test injecting an artificial 500ms disk delay: `save_database()` returned in ~0.2ms while
      the event loop kept ticking, and `flush_pending_saves()` correctly blocked until the
      write completed.
- [x] **Database could be silently wiped on load errors** — `src/db.py` `load_database()`
      previously called `_create_default_database()` (which overwrites the file on disk) on
      ANY parse/read error, with no backup. Fixed: added a rolling `.bak` snapshot written
      before every save (via `os.replace`, atomic on both POSIX/Windows), and on load failure
      the unreadable file is now quarantined (copied aside, timestamped, never deleted) before
      attempting recovery from `.bak`. Verified via a script: corrupting the main file recovers
      from `.bak` with no data loss; corrupting both preserves both bad files on disk and
      starts fresh only as a last resort.
- [x] **A single bad player field could wipe that player's entire record** —
      `src/db.py` `_sanitize_player_data` / `_migrate_and_validate_player` previously wrapped
      the whole function in one `try/except` that discarded ALL of a player's stats
      (XP, kills, achievements, etc.) if even one field failed to parse. Fixed with new
      `_safe_int`/`_safe_float` helpers that coerce each field independently with safe
      defaults, so one malformed field can no longer cascade into full data loss. Verified
      with a test player containing a corrupt `xp` field: other stats/achievements survived.
- [x] **Windows atomic-write path wasn't actually atomic** — `_save_database_impl` used to
      `os.remove` then `os.rename` on Windows, leaving a gap where a crash could destroy the
      file. Fixed by using `os.replace()` (atomic on both POSIX and Windows) for all platforms.

## 🟠 High

- [x] **Admin PM commands don't reply to the admin** — `src/duckhuntbot.py`. In PM, the
      `channel` param is actually the bot's own nick, not the sender, so every reply/usage
      message was being sent nowhere useful. Fixed by introducing `reply_target = nick if
      is_private_msg else channel` in `handle_rearm`, `handle_disarm`,
      `_send_admin_usage_or_execute` (backing `handle_ignore`/`handle_unignore`),
      `handle_ducklaunch`, `handle_join_channel`, and `handle_part_channel`, and sending all
      admin-facing confirmation/usage/error messages to it instead of `channel`.
      Also fixed the deeper bug this uncovered: `_get_admin_target_player`'s PM branch (and
      `_send_admin_usage_or_execute`'s unconditional `db.get_player(target, channel)`) were
      looking up/creating players scoped to the bot's own nick, which `db.py`'s
      `_normalize_channel` silently maps to a shared `__pm__` bucket - a phantom player
      completely disconnected from the target's real per-channel stats/ammo. `!rearm`/`!disarm`
      via PM now require an explicit target channel (`!rearm <channel> <player>`, matching the
      existing `!ducklaunch` pattern) so they operate on the player's real record.
      `!ignore`/`!unignore` didn't need this since `set_global_ignored`/`is_ignored` already
      use a channel-independent `__global__` bucket - only their reply-target was broken.
      Verified with an async test harness (isolated temp `DuckDB`, captured `send_message`
      calls): PM replies now go to the admin's nick, `!rearm`/`!disarm` mutate the real
      channel-scoped player (not `__pm__`), and in-channel behavior is unchanged.
- [x] **Admin config crash on dict-form entries** — `src/duckhuntbot.py` `__init__` did
      `admin.lower()` assuming all `admins` config entries are strings, crashing at startup if
      a dict-form `{"nick":..., "hostmask":...}` entry (supported by `is_admin`) was used.
      Fixed to build `self.admins` defensively, extracting `.get("nick")` from dict entries.
      Verified with a config containing a mixed string/dict admin list: no crash, correct
      nick list logged.
- [x] **Nick-only admin auth is spoofable** — `src/duckhuntbot.py:114-144` allows admin auth by
      nick match alone with no hostmask verification. Since tightening the default behavior
      would be a breaking change for existing configs, this was addressed by clearly
      documenting the risk and making it loud rather than silent: a one-time startup warning
      now lists every admin configured without a hostmask, `config.json.example` and the
      README document the safer `{"nick":..., "hostmask":...}` form, and the existing
      per-use warning in `is_admin` was left in place. Verified: a mixed string/dict admin
      config produces exactly one startup warning naming only the nick-only entry.
- [x] **Shop inventory-limit config path mismatch** — `src/shop.py` `_load_inventory_limits`
      read `config['gameplay']['max_inventory_items']` but the real key is
      `limits.max_inventory_items` (confirmed in `config.json`/`config.json.example`), so the
      configured limit was always silently ignored in favor of the hardcoded default. Fixed to
      read from `config['limits']`. `max_per_item_type` still isn't set in the real
      `config.json` (so it keeps its hardcoded default of 99), but it's now wired correctly and
      documented as an optional key in `config.json.example`. Verified by loading the real
      `config.json`: `max_total_items` now correctly picks up the configured value of 20.
- [x] **Golden duck HP mechanic bypassed by `!bef`** — `src/game.py` `befriend_duck` killed a
      golden duck in one successful befriend roll regardless of remaining HP, unlike `!bang`
      which requires multiple hits. Fixed to decrement `duck['current_hp']` on each successful
      roll and only remove/fully-befriend the duck once HP reaches 0, granting `befriend_xp`
      per successful roll (added a new `bef_success_golden` message for the partial-progress
      case). Verified with a 3-HP golden duck and a 100%-success-rate config: the duck now
      survives the first two successful rolls (partial-progress message, HP counting down)
      and is only fully befriended on the third, with total XP awarded across all three
      rolls equal to `befriend_xp * max_hp`, matching the intended per-roll reward.
- [x] **Golden duck partial-hit message shows XP that's never granted** — `src/game.py`
      `_process_hit`. `xp_gained` was shown in the `bang_hit_golden` (partial-hit) message but
      only actually added to `player['xp']` on the final kill shot (via `xp_gained *= max_hp`
      lump sum). Fixed by crediting `xp_gained` to the player immediately on every hit
      (partial and killing), and removing the `* max_hp` multiplier on the kill shot since
      prior hits already received their own share. Total XP awarded across a full kill is
      unchanged (`golden_duck_xp * max_hp`); only the timing/display now matches the actual
      balance. Verified with a 3-HP golden duck: each hit's displayed `xp_gained` now exactly
      matches the player's actual XP delta for that hit, and the 3-hit total equals 45
      (15 xp x 3) as before.

## 🟡 Medium

### db.py
- [x] `set_global_ignored` docstring claims persistence but never calls `save_database()`.
      Fixed the docstring to accurately describe the in-memory-only behavior and point to
      the caller-responsible-for-saving pattern used throughout this class.
- [x] Hardcoded fallback list of valid inventory item IDs drifts from `shop.json`. Fixed
      `_sanitize_player_data` to fall back to reading `shop.json` directly (same file/path
      `shop.py` uses) when `self.bot.shop` isn't available, only falling back to the
      hardcoded snapshot as a last resort. Verified with `bot=None` (forcing the shop.json
      fallback path): an invalid item ID was correctly filtered out of a player's inventory.

### shop.py
- [x] ~60% of `_apply_item_effect` is dead code for item types no longer in `shop.json`
      (`accuracy`, `luck`, `sabotage_*`, `steal_ammo`, `insurance`, etc.). Investigated each:
      `clover_luck`/`insurance`/`dry_clothes` are still actively read/consumed elsewhere
      (`game.py`'s `_get_active_effect`/`_check_insurance_protection`, `duckhuntbot.py`'s
      `handle_use`/`handle_give` message branches) even though no current `shop.json` item
      grants them, so they're intentional extension points, not simple dead code - removing
      them would also require touching those other files and is a product decision (should
      these items come back to the shop, or should all their consumers be ripped out too?)
      that's out of scope for a code-review pass. Left as-is, but see the two concrete fixes
      below. `max_ammo`/`chargers`, however, directly contradicted the README's explicit
      claim that these legacy fields were stripped from "defaults, creation, and runtime
      sanitization logic" - removed those two branches since they could silently resurrect
      the legacy fields on a config change, with no real value (db.py's sanitization would
      just strip the fields back out on the next load/save anyway).
- [x] `steal_ammo` deletes ammo from target without ever crediting the buyer. Root cause:
      `_apply_item_effect` only ever received one player (whoever the effect targets), with
      no way to reference the purchaser at all. Fixed by adding an optional `buyer` parameter
      (only populated for `target_required` items, wired through from `purchase_item`), and
      crediting the stolen ammo to `buyer` (bounded by their magazine capacity) when present.
      Verified with a direct effect-application test: target loses 3 ammo, buyer gains 3
      (bounded by magazine capacity).
- [x] `max_ammo` / `chargers` effect branches contradict README's claim these legacy fields
      were removed. Fixed above (branches removed; unmatched types now correctly fall through
      to the `"unknown"` effect handler).
- [x] `target_required` flag is set on zero shop items — targeting branch is unreachable.
      Confirmed: the full targeting mechanism (`purchase_item`'s `target_player` parameter,
      the `target_required` checks, and `_apply_item_effect`'s targeted-call path) is intact
      and functional (exercised directly in testing), it's just that no current `shop.json`
      item opts into it. Left as-is since this is a content/config decision (whether to add
      a targeted item), not a code defect.
- [x] XP is deducted before effect application with no rollback on exception. Fixed by
      reordering `purchase_item`'s immediate-use branch to apply the effect first (inside a
      try/except) and only deduct XP after the effect succeeds; on failure it now returns an
      `effect_failed` error with no XP charged, instead of silently losing XP on an
      unhandled exception. Verified with a `ShopManager` subclass whose `_apply_item_effect`
      always raises: purchase fails cleanly and the player's XP is provably unchanged.

### duckhuntbot.py
- [ ] `_execute_command_safely` is a 200+ line if/elif chain — refactor to dispatch table.
      Deferred: this is a pure maintainability refactor (not a bug) touching the single
      highest-traffic dispatch path in the bot, where the risk of a subtle regression
      (mis-wired args, a command silently falling through) outweighs the benefit for this
      pass. Left as-is; a dispatch-table refactor would be a good follow-up with its own
      dedicated testing pass.
- [x] Unbounded per-nick rate-limiter dict, no eviction (memory growth vector). Fixed by
      adding a configurable `anti_abuse.rate_limit_max_tracked` cap (default 2000) and a
      `_prune_rate_limiters()` helper that evicts the least-recently-active entries down to
      half that cap whenever it's exceeded (checked cheaply, only when a new nick is first
      added). Verified with 20 synthetic entries and a cap of 10: pruning correctly reduced
      to 5 entries, keeping only the most recently active ones.
- [x] `asyncio.create_task(...)` in `send_message` without retaining a reference (GC risk).
      Added a `_track_task()` helper that retains a strong reference in a new
      `self._background_tasks` set (removed via a done-callback), and switched every
      fire-and-forget `create_task` call that wasn't already retained elsewhere
      (`send_message`, the auto-rejoin scheduling in `handle_message`, and the flock-spawn
      task in `handle_ducklaunch`) to use it. `schedule_rejoin`'s and `run()`'s tasks were
      already retained via `self.rejoin_tasks`/local variables and didn't need changes.
- [x] Dead code: `is_user_in_channel_sync` (removed - confirmed zero callers anywhere in the
      codebase). `self.admins` is no longer flagged: it's now populated correctly (see the
      Critical/High admin-config fixes above) and is actively used for the startup
      admin-count/security-warning log lines, so it's no longer unused dead weight.

### error_handling.py
- [ ] `CircuitBreaker` fully implemented but never used anywhere. Deferred: wiring it up
      requires deciding *which* operations should trip it and what the right
      threshold/timeout/fallback behavior is for each - a product/architecture decision
      with real behavioral impact (a misconfigured breaker could start rejecting
      legitimate operations), not a mechanical bug fix. Left unused but in place for a
      future deliberate integration.
- [ ] `HealthChecker.run_checks()` never called anywhere. Same reasoning as above - health
      checks are already registered (`_setup_health_checks`), but nothing periodically
      invokes `run_checks()` or acts on its results (e.g. alerting/auto-restart). Wiring
      this up is a legitimate improvement but involves deciding what should happen when a
      check fails, which is out of scope for this pass.
- [x] `safe_format_message` regex mishandles format specs like `{value:>10}`. Fixed
      `replace_missing` to split the field on `!`/`:` before checking/rebuilding it, so a
      present key with a format spec (e.g. `{value:>10}`) is no longer misidentified as
      missing. Verified: a template mixing a present specced key and a genuinely missing
      key now formats the former correctly and only placeholders the latter.

### levels.py
- [x] Ammo/magazine redistribution on level-up can silently round up and grant free ammo when
      totals don't divide evenly. Fixed `update_player_magazines` to compute the new total
      magazine count (including the current one) from a floor division of the player's
      actual carried-over ammo, so a level-up can never round a partial magazine's worth of
      ammo up into a free full spare magazine. Added a `full_reload=True` option (used by
      the admin `!rearm` command and auto-rearm-on-duck-shot, which are intentionally meant
      to fully restore a player's loadout rather than prorate it) that bypasses the
      proration and behaves like the old code. Verified with a low-ammo level-up: the
      player's implied total ammo after leveling up never exceeds what they had before,
      while `full_reload=True` still fully restores capacity as before.
- [x] **(found during testing, not on the original list)** `get_player_level_info` never
      included `magazines`/`bullets_per_magazine` in its returned dict, even though
      `levels.json` defines a *decreasing* magazine count per level as a difficulty curve
      (e.g. level 8 "Duck Deity" is meant to have only 1 magazine, vs. 3 at level 1). Every
      caller that read `level_info.get("magazines", 3)` (`update_player_magazines`, and
      `shop.py`'s per-level magazine-purchase cap in `_check_item_usable`/
      `_apply_item_effect`) was silently always hitting the hardcoded default of 3,
      meaning the entire level-based magazine-capacity difficulty mechanic had never
      actually applied. Fixed by passing `magazines`/`bullets_per_magazine` through from
      `level_data`. **Behavioral note:** high-level existing players will now see their
      magazine capacity correctly drop to match their level's configured value the next
      time they level up or get rearmed (not retroactively/immediately on bot restart -
      only at the next trigger event) - this is a gameplay-balance change, not just a
      code-cleanup, since it makes a previously-inert difficulty mechanic active. Verified
      with a level 1→8 transition: magazine cap correctly drops from 3 to 1, and both the
      level-up path and the rearm path now respect it.

### logging_utils.py
- [x] `EMOJIS` dicts contain no actual emojis, contradicting docstrings. The "emoji" was
      always just the level name repeated as plain text (e.g. `'INFO': 'INFO'`), which was
      also visibly duplicating the level name in every single log line (e.g.
      `21:12:05 INFO INFO     DuckHuntBot ...`). Removed the redundant `EMOJIS` dicts and
      their use in all three formatters (`EnhancedColourFormatter`, `EnhancedFileFormatter`,
      `UnifiedFormatter`), and fixed the module docstring's "emojis" claim. Verified: log
      output no longer repeats the level name.
- [x] Performance logger would double-log via propagation to parent logger, if ever used.
      `get_performance_logger()` creates `"DuckHuntBot.Performance"` via `setup_logger()`,
      which gives it its own full set of handlers - since it's a dotted child of
      `"DuckHuntBot"` (also set up via `setup_logger()`), records would otherwise also
      propagate up and get emitted a second time by `"DuckHuntBot"`'s handlers. Fixed by
      setting `propagate = False` on every logger `setup_logger()` configures. Verified this
      doesn't affect other component loggers like `"DuckHuntBot.DB"`/`"SASL"` that rely on
      plain `logging.getLogger(...)` + propagation to reach `"DuckHuntBot"`'s handlers (they
      don't go through `setup_logger()`, so their `propagate` default is untouched).
- [x] Logs directory path not resolved relative to project root like config is. Fixed
      `setup_logger()` to resolve `logs/` against the project root (same pattern already
      used by `db.py` for its database path) instead of the process CWD. Verified by running
      from a different working directory: the log file now correctly lands in the project's
      `logs/` folder every time, matching the existing `db.py` behavior this was inconsistent
      with.

### sasl.py
- [x] CAP negotiation has no timeout — can hang forever if server doesn't respond. Fixed as
      Fixed as part of the Critical SASL fix above (15s negotiation timeout watchdog).
- [x] `"sasl" in caps` check may miss spec-compliant `CAP LS 302` responses like `sasl=PLAIN`.
      Fixed as part of the Critical SASL fix above (`_has_sasl_cap()`).

## ✅ Feature Requests
- [x] Command prefix (`!`) was hardcoded in `handle_command`'s message parsing, and
      duplicated as literal `!` text throughout every help/usage string in `messages.json`
      and `duckhuntbot.py`. Added `commands.prefix` to `config.json`/`config.json.example`
      (default `"!"`, validated non-empty/no-whitespace at startup with a warning +
      fallback). `MessageManager` now substitutes a `{prefix}` placeholder in all message
      templates, and every hardcoded `!command` reference in help text, usage errors, and
      admin command messages in `duckhuntbot.py` was switched to use the configured
      prefix dynamically. Also removed 3 orphaned decoy-duck message strings
      (`decoy_duck_flies_away`, `bang_decoy`, `bef_decoy`) found while auditing the
      hardcoded-`!` references, plus confirmed decoy ducks are no longer spawnable
      (already removed from `config.json`/`config.json.example`/`game.py` in an earlier pass).

## Notes
- No SQL/eval/pickle usage anywhere (JSON only); no data races found in core duck-shooting
  logic (single-threaded asyncio, no `await` mid-mutation in hot paths).
