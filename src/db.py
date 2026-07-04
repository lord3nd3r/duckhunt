"""
Enhanced Database management for DuckHunt Bot
Focus on fixing missing field errors with improved error handling
"""

import json
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

from .error_handling import ErrorRecovery, RetryConfig, sanitize_user_input, with_retry


class DuckDB:
    """Simplified database management"""

    def __init__(self, db_file="duckhunt.json", bot=None):
        # Resolve relative paths against the project root (repo root), not the process CWD.
        # This prevents "stats wiped" symptoms when the bot is launched from a different working dir.
        if os.path.isabs(db_file):
            self.db_file = db_file
        else:
            project_root = os.path.dirname(os.path.dirname(__file__))
            self.db_file = os.path.join(project_root, db_file)
        self.bot = bot
        # Channel-scoped data: {"#channel": {"players": {"nick": player_dict}}}
        self.channels = {}
        self.logger = logging.getLogger("DuckHuntBot.DB")

        # Error recovery configuration
        self.error_recovery = ErrorRecovery()
        self.save_retry_config = RetryConfig(
            max_attempts=3, base_delay=0.5, max_delay=5.0
        )

        # Actual disk writes (including retry backoff and os.fsync) run on a single
        # dedicated background thread so they can never block the bot's asyncio event
        # loop. A single worker guarantees writes are serialized in submission order.
        self._save_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="duckdb-save"
        )
        # Most recently submitted save future. The single worker serializes writes,
        # so once this future is done, every earlier queued save is done too - it's
        # what flush_pending_saves() waits on (bounded by its timeout).
        self._last_save_future = None

        data = self.load_database()
        # Hydrate in-memory state from disk.
        if isinstance(data, dict) and isinstance(data.get("channels"), dict):
            self.channels = data["channels"]
        else:
            self.channels = {}

    @staticmethod
    def _normalize_channel(channel: str) -> str:
        """Normalize channel keys (case-insensitive). Non-channel contexts go to a reserved bucket."""
        if not isinstance(channel, str):
            return "__unknown__"
        channel = channel.strip()
        if not channel:
            return "__unknown__"
        # Preserve internal buckets used by the bot/database.
        # This allows explicit references like '__global__' without being remapped to '__pm__'.
        if channel.startswith("__") and channel.endswith("__"):
            return channel
        if channel.startswith("#") or channel.startswith("&"):
            return channel.lower()
        return "__pm__"

    def is_ignored(self, nick: str, channel: str) -> bool:
        """Return True if nick is ignored for this channel or globally."""
        try:
            if not isinstance(nick, str) or not nick.strip():
                return False
            nick_clean = sanitize_user_input(
                nick,
                max_length=50,
                allowed_chars="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]{}^`|\\",
            )
            nick_lower = nick_clean.lower().strip()
            if not nick_lower:
                return False

            # Channel-scoped ignore
            player = self.get_player_if_exists(nick_lower, channel)
            if isinstance(player, dict) and bool(player.get("ignored", False)):
                return True

            # Global ignore bucket
            global_player = self.get_player_if_exists(nick_lower, "__global__")
            return bool(global_player and global_player.get("ignored", False))
        except Exception:
            return False

    def set_global_ignored(self, nick: str, ignored: bool) -> bool:
        """Set global ignored flag for nick in the in-memory `__global__` bucket.

        NOTE: This does not write to disk itself - callers must call `save_database()`
        afterwards (as `_send_admin_usage_or_execute` in duckhuntbot.py does) for the
        change to actually persist, consistent with every other in-memory mutation in
        this class.
        """
        try:
            player = self.get_player(nick, "__global__")
            if not isinstance(player, dict):
                return False
            player["ignored"] = bool(ignored)
            return True
        except Exception:
            return False

    @property
    def players(self):
        """Backward-compatible flattened view of all players across channels.

        WARNING: This property flattens per-channel data. If the same nick exists
        in multiple channels, only the last one encountered is kept (last-write-wins).
        For accurate multi-channel support, use get_player(nick, channel) instead.
        """
        flattened = {}
        try:
            for channel_key, channel_data in (self.channels or {}).items():
                players = (
                    channel_data.get("players", {})
                    if isinstance(channel_data, dict)
                    else {}
                )
                if isinstance(players, dict):
                    for nick, player in players.items():
                        # Check for conflicts (same nick in multiple channels)
                        if nick in flattened and flattened[nick] != player:
                            self.logger.warning(
                                f"Nick collision in backward-compat players property: {nick} not from "
                                f"channel {channel_key}. This nick exists in multiple channels - "
                                f"using multi-channel aware methods instead."
                            )
                        flattened[nick] = player
        except Exception:
            return {}
        return flattened

    def load_database(self) -> dict:
        """Load the database, creating it if it doesn't exist.

        IMPORTANT: This never silently overwrites an existing, unreadable database file.
        If the file is missing entirely there's nothing to lose, so a fresh DB is created.
        Otherwise (empty file, corrupt JSON, unexpected error) the unreadable file is first
        quarantined (copied aside, never deleted) and we attempt to recover from the rolling
        `.bak` snapshot written before the last successful save. Only if no backup is usable
        do we fall back to a brand-new empty database - and even then the original bad file
        is preserved on disk for manual recovery.
        """
        try:
            if not os.path.exists(self.db_file):
                self.logger.info(
                    f"Database file {self.db_file} not found, creating new one"
                )
                return self._create_default_database()

            with open(self.db_file, "r") as f:
                content = f.read().strip()

            if not content:
                return self._recover_or_default("Database file is empty")

            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                return self._recover_or_default(
                    f"Database file contains invalid JSON: {e}"
                )

            # Validate basic structure
            if not isinstance(data, dict):
                return self._recover_or_default("Database root is not a dictionary")

            return self._normalize_loaded_data(data)

        except Exception as e:
            return self._recover_or_default(f"Unexpected error loading database: {e}")

    def _normalize_loaded_data(self, data: dict) -> dict:
        """Apply metadata init + legacy migration to a freshly parsed database dict."""
        # Initialize metadata if missing
        if "metadata" not in data:
            data["metadata"] = {
                "version": "1.0",
                "created": datetime.now().isoformat(),
                "last_modified": datetime.now().isoformat(),
            }

        # Migrate legacy flat structure (players) -> channels
        if "channels" not in data or not isinstance(data.get("channels"), dict):
            legacy_players = (
                data.get("players") if isinstance(data.get("players"), dict) else {}
            )
            channels = {}
            if isinstance(legacy_players, dict):
                for legacy_nick, legacy_player in legacy_players.items():
                    try:
                        last_channel = (
                            legacy_player.get("last_activity_channel")
                            if isinstance(legacy_player, dict)
                            else None
                        )
                        channel_key = (
                            self._normalize_channel(last_channel)
                            if last_channel
                            else "__global__"
                        )
                        channels.setdefault(channel_key, {"players": {}})
                        if isinstance(channels[channel_key].get("players"), dict):
                            channels[channel_key]["players"][
                                str(legacy_nick).lower()
                            ] = legacy_player
                    except Exception:
                        continue

            data["channels"] = channels
            data["metadata"]["version"] = "2.0"

        # Ensure channels structure exists
        if "channels" not in data:
            data["channels"] = {}

        # Update last_modified
        data["metadata"]["last_modified"] = datetime.now().isoformat()

        total_players = 0
        try:
            for _c, cdata in data.get("channels", {}).items():
                if isinstance(cdata, dict) and isinstance(cdata.get("players"), dict):
                    total_players += len(cdata["players"])
        except Exception:
            total_players = 0
        self.logger.info(
            f"Successfully loaded database with {total_players} players across {len(data.get('channels', {}))} channels"
        )
        return data

    def _recover_or_default(self, reason: str) -> dict:
        """Handle an unreadable primary database file without ever discarding data.

        1. Quarantine (copy, never delete) the unreadable file for manual recovery.
        2. Try to recover from the rolling `.bak` snapshot written before the last save.
        3. Only if that also fails, start a fresh empty database (the bad file still
           remains on disk under its quarantine name).
        """
        self.logger.error(f"Database load problem: {reason}")
        quarantine_path = self._quarantine_unreadable_file()

        recovered = self._try_load_backup()
        if recovered is not None:
            self.logger.warning(
                "Recovered database from backup file (.bak) after the primary file failed to load."
            )
            return recovered

        if quarantine_path:
            self.logger.error(
                f"No usable backup found. Starting with a new empty database. "
                f"The unreadable file was preserved at: {quarantine_path} for manual recovery."
            )
        else:
            self.logger.error(
                "No usable backup found. Starting with a new empty database."
            )
        return self._create_default_database()

    def _quarantine_unreadable_file(self) -> Optional[str]:
        """Copy (never move/delete) the current db_file aside before it gets overwritten.

        Uses a microsecond-precision timestamp, and falls back to an incrementing suffix
        if a collision somehow still occurs, so multiple quarantine events can never
        overwrite each other's preserved data.
        """
        if not os.path.exists(self.db_file):
            return None
        try:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            quarantine_path = f"{self.db_file}.corrupt-{timestamp}"
            suffix = 1
            while os.path.exists(quarantine_path):
                quarantine_path = f"{self.db_file}.corrupt-{timestamp}-{suffix}"
                suffix += 1
            shutil.copy2(self.db_file, quarantine_path)
            self.logger.warning(
                f"Preserved unreadable database file at: {quarantine_path}"
            )
            return quarantine_path
        except Exception as e:
            self.logger.error(f"Failed to preserve unreadable database file: {e}")
            return None

    def _try_load_backup(self) -> Optional[dict]:
        """Attempt to load the rolling .bak snapshot written before the last successful save."""
        backup_path = f"{self.db_file}.bak"
        if not os.path.exists(backup_path):
            return None
        try:
            with open(backup_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if not content:
                return None
            data = json.loads(content)
            if not isinstance(data, dict):
                return None
            return self._normalize_loaded_data(data)
        except Exception as e:
            self.logger.error(f"Backup file {backup_path} is also unreadable: {e}")
            return None

    def _create_default_database(self) -> dict:
        """Create a new default database file with proper structure"""
        try:
            default_data = {
                "channels": {},
                "last_save": str(time.time()),
                "version": "2.0",
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "description": "DuckHunt Bot Player Database",
            }

            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(default_data, f, indent=2, ensure_ascii=False, sort_keys=True)

            self.logger.info(f"Created new database file: {self.db_file}")
            return default_data

        except Exception as e:
            self.logger.error(f"Failed to create default database: {e}")
            # Return a minimal valid structure even if file creation fails
            return {
                "channels": {},
                "last_save": str(time.time()),
                "version": "2.0",
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "description": "DuckHunt Bot Player Database",
            }

    @staticmethod
    def _safe_int(value, default=0, min_val=None, max_val=None) -> int:
        """Best-effort coercion to int with bounds. Never raises; falls back to default."""
        try:
            result = int(float(value))
        except (ValueError, TypeError):
            try:
                result = int(default)
            except (ValueError, TypeError):
                result = 0
        if min_val is not None:
            result = max(min_val, result)
        if max_val is not None:
            result = min(max_val, result)
        return result

    @staticmethod
    def _safe_float(value, default=0.0, min_val=None) -> float:
        """Best-effort coercion to float with a floor. Never raises; falls back to default."""
        try:
            result = float(value)
        except (ValueError, TypeError):
            try:
                result = float(default)
            except (ValueError, TypeError):
                result = 0.0
        if min_val is not None:
            result = max(min_val, result)
        return result

    def _sanitize_player_data(self, player_data):
        """Sanitize and validate player data, ensuring ALL required fields exist.

        Every field is coerced individually with safe fallbacks (see `_safe_int` /
        `_safe_float`), so a single malformed field (e.g. a non-numeric `xp` string)
        can never cause the rest of the player's stats to be discarded.
        """
        if not isinstance(player_data, dict):
            player_data = {}

        # Get default values from config or fallbacks. Guarded independently so a config
        # lookup error can't cascade into wiping the player's actual stats below.
        try:
            default_accuracy = (
                self.bot.get_config("player_defaults.accuracy", 75) if self.bot else 75
            )
            max_accuracy = (
                self.bot.get_config("gameplay.max_accuracy", 100) if self.bot else 100
            )
            default_magazines = (
                self.bot.get_config("player_defaults.magazines", 3) if self.bot else 3
            )
            default_bullets_per_mag = (
                self.bot.get_config("player_defaults.bullets_per_magazine", 6)
                if self.bot
                else 6
            )
            default_jam_chance = (
                self.bot.get_config("player_defaults.jam_chance", 15)
                if self.bot
                else 15
            )
        except Exception as e:
            self.logger.warning(
                f"Error reading config defaults during sanitize, using hardcoded fallbacks: {e}"
            )
            default_accuracy, max_accuracy = 75, 100
            default_magazines, default_bullets_per_mag, default_jam_chance = 3, 6, 15

        sanitized = {}

        # Core required fields - these MUST exist for messages to work
        sanitized["nick"] = str(player_data.get("nick", "Unknown"))[:50]
        sanitized["xp"] = self._safe_int(player_data.get("xp", 0), 0, min_val=0)
        sanitized["ducks_shot"] = self._safe_int(
            player_data.get("ducks_shot", 0), 0, min_val=0
        )
        sanitized["ducks_befriended"] = self._safe_int(
            player_data.get("ducks_befriended", 0), 0, min_val=0
        )
        sanitized["shots_fired"] = self._safe_int(
            player_data.get("shots_fired", 0), 0, min_val=0
        )
        sanitized["shots_missed"] = self._safe_int(
            player_data.get("shots_missed", 0), 0, min_val=0
        )

        # Equipment and stats
        sanitized["accuracy"] = self._safe_int(
            player_data.get("accuracy", default_accuracy),
            default_accuracy,
            0,
            max_accuracy,
        )
        sanitized["gun_confiscated"] = bool(player_data.get("gun_confiscated", False))

        # Activity / admin flags
        sanitized["last_activity_channel"] = str(
            player_data.get("last_activity_channel", "")
        )[:100]
        sanitized["last_activity_time"] = self._safe_float(
            player_data.get("last_activity_time", 0.0), 0.0
        )
        sanitized["ignored"] = bool(player_data.get("ignored", False))

        # Ammo system with validation
        sanitized["current_ammo"] = self._safe_int(
            player_data.get("current_ammo", default_bullets_per_mag),
            default_bullets_per_mag,
            0,
            50,
        )
        sanitized["magazines"] = self._safe_int(
            player_data.get("magazines", default_magazines), default_magazines, 0, 20
        )
        sanitized["bullets_per_magazine"] = self._safe_int(
            player_data.get("bullets_per_magazine", default_bullets_per_mag),
            default_bullets_per_mag,
            1,
            50,
        )
        sanitized["jam_chance"] = self._safe_int(
            player_data.get("jam_chance", default_jam_chance),
            default_jam_chance,
            0,
            100,
        )

        # Confiscated ammo (optional fields but with safe defaults)
        sanitized["confiscated_ammo"] = self._safe_int(
            player_data.get("confiscated_ammo", 0), 0, 0, 50
        )
        sanitized["confiscated_magazines"] = self._safe_int(
            player_data.get("confiscated_magazines", 0), 0, 0, 20
        )

        inventory = player_data.get("inventory", {})
        clean_inventory = {}
        if isinstance(inventory, dict):
            # Fetch valid item IDs from shop if available, otherwise read shop.json
            # directly (same file/location shop.py loads from), and only fall back to a
            # hardcoded snapshot as a last resort - this avoids the hardcoded list quietly
            # drifting out of sync with shop.json as items are added/removed/renumbered.
            valid_ids = None
            if self.bot and hasattr(self.bot, "shop") and self.bot.shop:
                try:
                    valid_ids = {str(k) for k in self.bot.shop.get_items().keys()}
                except Exception:
                    valid_ids = None

            if not valid_ids:
                try:
                    shop_path = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "shop.json",
                    )
                    with open(shop_path, "r", encoding="utf-8") as f:
                        shop_data = json.load(f)
                    valid_ids = {str(k) for k in shop_data.get("items", {}).keys()}
                except Exception:
                    valid_ids = None

            if not valid_ids:
                # Last-resort fallback if shop.json is also unavailable. Keep in sync with
                # shop.json's item IDs (1: ammo, 2: magazine, 4: clean_gun, 5: attract_ducks,
                # 7: buy_gun_back, 13: temporary_accuracy, 14: xp_shield).
                valid_ids = {"1", "2", "4", "5", "7", "13", "14"}

            for k, v in inventory.items():
                try:
                    clean_key = str(k)[:20]
                    if clean_key not in valid_ids:
                        continue
                    clean_value = (
                        self._safe_int(v, 0, min_val=0)
                        if isinstance(v, (int, float, str))
                        else 0
                    )
                    if clean_value > 0:
                        clean_inventory[clean_key] = clean_value
                except Exception:
                    continue
        sanitized["inventory"] = clean_inventory

        # Safe temporary effects
        temp_effects = player_data.get("temporary_effects", [])
        clean_effects = []
        if isinstance(temp_effects, list):
            for effect in temp_effects[:20]:
                if isinstance(effect, dict) and "type" in effect:
                    clean_effects.append(effect)
        sanitized["temporary_effects"] = clean_effects

        # Add any missing fields that messages might reference
        additional_fields = {
            "best_time": 0.0,
            "worst_time": 0.0,
            "total_time_hunting": 0.0,
            "level": 1,
            "xp_gained": 0,
            "hp_remaining": 0,
            "victim": "",
            "xp_lost": 0,
            # Streak & social features
            "current_streak": 0,
            "best_streak": 0,
            # Achievement system
            "achievements": [],
            # Daily bonus
            "last_daily": 0.0,
            "daily_streak": 0,
            "last_daily_date": "",
            # Bang cooldown
            "last_bang_time": 0.0,
            # Economy tracking (for High Roller achievement)
            "total_xp_spent": 0,
            # Confiscation counter (for Trigger Happy achievement)
            "gun_confiscated_count": 0,
        }

        for field, default_value in additional_fields.items():
            if field not in sanitized:
                if field in ("best_time", "worst_time", "total_time_hunting"):
                    sanitized[field] = self._safe_float(
                        player_data.get(field, default_value),
                        default_value,
                        min_val=0.0,
                    )
                else:
                    sanitized[field] = player_data.get(field, default_value)

        return sanitized

    def save_database(self) -> bool:
        """Persist all player data to disk.

        Builds the sanitized save payload synchronously on the calling thread (fast,
        in-memory, no I/O - so there's no risk of the background thread iterating
        `self.channels` while it's concurrently mutated). The actual disk write -
        including retry backoff (`time.sleep`) and `os.fsync` - then runs on a
        dedicated single-worker background thread, so it can never block the bot's
        asyncio event loop. Safe to call from both sync and async code, exactly as
        before, with no `await` needed.

        The return value only reflects whether the save was *scheduled* successfully,
        not whether it has completed - call `flush_pending_saves()` (e.g. during
        shutdown, or before an os.execv restart) to block until all pending writes
        are actually done, so a save can never be silently lost to a killed thread.
        """
        try:
            data = self._build_save_payload()
        except Exception as e:
            self.logger.error(f"Error preparing database for save: {e}")
            return False

        try:
            future = self._save_executor.submit(self._write_database_to_disk, data)
            future.add_done_callback(self._log_save_result)
            self._last_save_future = future
            return True
        except RuntimeError as e:
            # Executor already shut down (e.g. a save was triggered after
            # flush_pending_saves() during shutdown) - fall back to a direct
            # synchronous write so this save is never silently lost.
            self.logger.warning(
                f"Save executor unavailable ({e}); writing synchronously instead."
            )
            try:
                return self._write_database_to_disk(data)
            except Exception as e2:
                self.logger.error(f"Synchronous fallback save failed: {e2}")
                return False

    def _log_save_result(self, future) -> None:
        """Done-callback for background saves: surface any failure to the logs."""
        try:
            future.result()
        except Exception as e:
            self.logger.error(f"Background database save failed: {e}")

    def flush_pending_saves(self, timeout: float = 10.0) -> None:
        """Block (up to `timeout` seconds) until all queued background saves complete.

        Call this once, during final shutdown (including immediately before an
        os.execv restart), so the last save can never be lost to a killed background
        thread. After this returns, the executor rejects new submissions, so later
        `save_database()` calls fall back to writing synchronously instead - see above.

        Waiting on the most recently submitted future is sufficient: the executor has
        a single worker, so all earlier queued saves necessarily finish first.
        """
        try:
            # Stop accepting new background saves, but don't block here -
            # shutdown(wait=True) has no timeout, which made the parameter a no-op.
            self._save_executor.shutdown(wait=False)
            future = self._last_save_future
            if future is not None:
                try:
                    future.result(timeout=timeout)
                except TimeoutError:
                    self.logger.error(
                        f"Timed out after {timeout}s waiting for pending database "
                        "saves to finish; the last save may not have completed."
                    )
                except Exception:
                    # Write failures are already logged by _log_save_result.
                    pass
        except Exception as e:
            self.logger.error(f"Error flushing pending database saves: {e}")

    def _build_save_payload(self) -> dict:
        """Snapshot + sanitize current in-memory state into a plain, self-contained
        dict that's safe to hand off to a background thread (sanitization always
        produces brand-new per-player dicts, so there are no shared mutable
        references back into the live `self.channels` structure).
        """
        data = {"channels": {}, "last_save": str(time.time()), "version": "2.0"}

        valid_count = 0
        for channel_key, channel_data in (self.channels or {}).items():
            if not isinstance(channel_key, str) or not isinstance(channel_data, dict):
                continue
            players = channel_data.get("players", {})
            if not isinstance(players, dict):
                continue

            out_channel_key = str(channel_key)
            data["channels"].setdefault(out_channel_key, {"players": {}})
            for nick, player_data in players.items():
                if isinstance(nick, str) and isinstance(player_data, dict):
                    try:
                        sanitized_nick = sanitize_user_input(nick, max_length=50)
                        data["channels"][out_channel_key]["players"][sanitized_nick] = (
                            self._sanitize_player_data(player_data)
                        )
                        valid_count += 1
                    except Exception as e:
                        self.logger.warning(
                            f"Error processing player {nick} in {out_channel_key} during save: {e}"
                        )

        self.logger.debug(f"Prepared save payload with {valid_count} players")
        return data

    @with_retry(
        RetryConfig(max_attempts=3, base_delay=0.5, max_delay=5.0),
        exceptions=(OSError, PermissionError, IOError),
    )
    def _write_database_to_disk(self, data: dict) -> bool:
        """Write an already-built save payload to disk.

        Runs on the background save executor thread - the blocking retry backoff and
        os.fsync here never touch the asyncio event loop.
        """
        temp_file = f"{self.db_file}.tmp"

        try:
            # Write to temporary file first (atomic write)
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())

            # Verify temp file was written correctly
            try:
                with open(temp_file, "r", encoding="utf-8") as f:
                    json.load(f)  # Verify it's valid JSON
            except json.JSONDecodeError:
                raise IOError("Temporary file contains invalid JSON")

            # Keep a rolling backup of the last known-good file before replacing it, so a
            # corrupted/interrupted write can never take down the only copy of the data.
            if os.path.exists(self.db_file):
                try:
                    shutil.copy2(self.db_file, f"{self.db_file}.bak")
                except Exception as e:
                    self.logger.warning(f"Could not update .bak backup file: {e}")

            # os.replace is atomic on both POSIX and Windows (unlike os.rename, which fails
            # on Windows if the destination already exists).
            os.replace(temp_file, self.db_file)

            self.logger.debug("Database saved successfully")
            return True

        except Exception as e:
            self.logger.error(f"Error writing database to disk: {e}")
            raise  # Re-raise for retry mechanism
        finally:
            # Clean up temp file if it still exists
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass

    def get_players_for_channel(self, channel: str) -> Dict[str, Any]:
        """Get the players dict for a channel, creating the channel bucket if needed."""
        channel_key = self._normalize_channel(channel)
        bucket = self.channels.setdefault(channel_key, {"players": {}})
        if not isinstance(bucket, dict):
            bucket = {"players": {}}
            self.channels[channel_key] = bucket
        if "players" not in bucket or not isinstance(bucket.get("players"), dict):
            bucket["players"] = {}
        return bucket["players"]

    def iter_all_players(
        self,
    ) -> Generator[Tuple[str, str, Dict[str, Any]], None, None]:
        """Yield (channel_key, nick, player_dict) for all players in all channels."""
        for channel_key, channel_data in (self.channels or {}).items():
            if not isinstance(channel_data, dict):
                continue
            players = channel_data.get("players", {})
            if not isinstance(players, dict):
                continue
            for nick, player in players.items():
                yield channel_key, nick, player

    def get_player_if_exists(self, nick: str, channel: str) -> Optional[dict]:
        """Return player dict for nick+channel if present; does not create records."""
        try:
            if not isinstance(nick, str) or not nick.strip():
                return None
            nick_clean = sanitize_user_input(
                nick,
                max_length=50,
                allowed_chars="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]{}^`|\\",
            )
            nick_lower = nick_clean.lower().strip()
            if not nick_lower:
                return None
            channel_key = self._normalize_channel(channel)
            channel_data = self.channels.get(channel_key)
            if not isinstance(channel_data, dict):
                return None
            players = channel_data.get("players")
            if not isinstance(players, dict):
                return None
            player = players.get(nick_lower)
            return player if isinstance(player, dict) else None
        except Exception:
            return None

    def get_player(self, nick: str, channel: str) -> dict:
        """Get player data for a specific channel, creating if doesn't exist with comprehensive validation"""
        try:
            # Validate and sanitize nick
            if not isinstance(nick, str) or not nick.strip():
                self.logger.warning(f"Invalid nick provided: {nick}")
                return self.error_recovery.safe_execute(
                    lambda: self.create_player("Unknown"),
                    fallback={"nick": "Unknown", "xp": 0, "ducks_shot": 0},
                    logger=self.logger,
                )

            # Sanitize nick input
            nick_clean = sanitize_user_input(
                nick,
                max_length=50,
                allowed_chars="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]{}^`|\\",
            )
            nick_lower = nick_clean.lower().strip()

            if not nick_lower:
                self.logger.warning(f"Empty nick after sanitization: {nick}")
                return self.create_player("Unknown")

            players = self.get_players_for_channel(channel)

            if nick_lower not in players:
                players[nick_lower] = self.create_player(nick_clean)
            else:
                # Ensure existing players have all required fields
                player = players[nick_lower]
                if not isinstance(player, dict):
                    self.logger.warning(
                        f"Invalid player data for {nick_lower}, recreating"
                    )
                    players[nick_lower] = self.create_player(nick_clean)
                else:
                    # Migrate and validate existing player data with error recovery.
                    # NOTE: _migrate_and_validate_player no longer raises for normal data
                    # (see its docstring), so this fallback should never actually be used.
                    validated = self.error_recovery.safe_execute(
                        lambda: self._migrate_and_validate_player(player, nick_clean),
                        fallback=self.create_player(nick_clean),
                        logger=self.logger,
                    )
                    players[nick_lower] = validated

            return players[nick_lower]

        except Exception as e:
            self.logger.error(f"Critical error getting player {nick}: {e}")
            return self.create_player(nick if isinstance(nick, str) else "Unknown")

    def _migrate_and_validate_player(self, player, nick):
        """Migrate old player data and validate all fields.

        `_sanitize_player_data` never raises, so `validated_player` is always fully
        populated before we even attempt the legacy ammo/chargers migration below. If that
        migration step hits an unexpected error, we still return the sanitized data rather
        than discarding the player's progress via `create_player`.
        """
        validated_player = self._sanitize_player_data(player)

        try:
            # Migrate from old ammo/chargers system to magazine system if needed
            if (
                isinstance(player, dict)
                and "magazines" not in player
                and ("ammo" in player or "chargers" in player)
            ):
                self.logger.info(
                    f"Migrating {nick} from old ammo system to magazine system"
                )

                old_ammo = self._safe_int(player.get("ammo", 6), 6, min_val=0)
                old_chargers = self._safe_int(player.get("chargers", 2), 2, min_val=0)

                validated_player["current_ammo"] = max(0, min(50, old_ammo))
                validated_player["magazines"] = max(1, min(20, old_chargers + 1))
                validated_player["bullets_per_magazine"] = 6
        except Exception as e:
            self.logger.error(f"Error migrating legacy ammo fields for {nick}: {e}")

        # Update nick in case it changed
        validated_player["nick"] = str(nick)[:50]

        return validated_player

    def create_player(self, nick: str) -> Dict[str, Any]:
        """Create a new player with all required fields"""
        try:
            safe_nick = str(nick)[:50] if nick else "Unknown"

            # Get configurable defaults from bot config
            if self.bot:
                accuracy = self.bot.get_config("player_defaults.accuracy", 75)
                magazines = self.bot.get_config("player_defaults.magazines", 3)
                bullets_per_mag = self.bot.get_config(
                    "player_defaults.bullets_per_magazine", 6
                )
                jam_chance = self.bot.get_config("player_defaults.jam_chance", 15)
                xp = self.bot.get_config("player_defaults.xp", 0)
            else:
                accuracy = 75
                magazines = 3
                bullets_per_mag = 6
                jam_chance = 15
                xp = 0

            return {
                "nick": safe_nick,
                "xp": xp,
                "ducks_shot": 0,
                "ducks_befriended": 0,
                "shots_fired": 0,
                "shots_missed": 0,
                "current_ammo": bullets_per_mag,
                "magazines": magazines,
                "bullets_per_magazine": bullets_per_mag,
                "accuracy": accuracy,
                "jam_chance": jam_chance,
                "gun_confiscated": False,
                "confiscated_ammo": 0,
                "confiscated_magazines": 0,
                "inventory": {},
                "temporary_effects": [],
                "last_activity_channel": "",
                "last_activity_time": 0.0,
                "ignored": False,
                "best_time": 0.0,
                "worst_time": 0.0,
                "total_time_hunting": 0.0,
                "level": 1,
                "xp_gained": 0,
                "hp_remaining": 0,
                "victim": "",
                "xp_lost": 0,
                # Streak & achievements
                "current_streak": 0,
                "best_streak": 0,
                "achievements": [],
                # Daily bonus
                "last_daily": 0.0,
                "daily_streak": 0,
                "last_daily_date": "",
                # Bang cooldown
                "last_bang_time": 0.0,
                # Economy tracking
                "total_xp_spent": 0,
                "gun_confiscated_count": 0,
            }
        except Exception as e:
            self.logger.error(f"Error creating player for {nick}: {e}")
            return {
                "nick": "Unknown",
                "xp": 0,
                "ducks_shot": 0,
                "ducks_befriended": 0,
                "shots_fired": 0,
                "shots_missed": 0,
                "current_ammo": 6,
                "magazines": 3,
                "bullets_per_magazine": 6,
                "accuracy": 75,
                "jam_chance": 15,
                "gun_confiscated": False,
                "confiscated_ammo": 0,
                "confiscated_magazines": 0,
                "inventory": {},
                "temporary_effects": [],
                "last_activity_channel": "",
                "last_activity_time": 0.0,
                "ignored": False,
                "best_time": 0.0,
                "worst_time": 0.0,
                "total_time_hunting": 0.0,
                "level": 1,
                "xp_gained": 0,
                "hp_remaining": 0,
                "victim": "",
                "xp_lost": 0,
            }

    def get_leaderboard(self, channel: str, category="xp", limit=3):
        """Get top players by specified category for a given channel"""
        try:
            leaderboard = []

            players = self.get_players_for_channel(channel)
            for nick, player_data in players.items():
                sanitized_data = self._sanitize_player_data(player_data)

                if category == "xp":
                    value = sanitized_data.get("xp", 0)
                elif category == "ducks_shot":
                    value = sanitized_data.get("ducks_shot", 0)
                else:
                    continue

                leaderboard.append((nick, value))

            leaderboard.sort(key=lambda x: x[1], reverse=True)
            return leaderboard[:limit]

        except Exception as e:
            self.logger.error(f"Error getting leaderboard for {category}: {e}")
            return []
