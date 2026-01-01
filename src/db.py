"""
Enhanced Database management for DuckHunt Bot
Focus on fixing missing field errors with improved error handling
"""

import json
import logging
import time
import os
from datetime import datetime
from .error_handling import with_retry, RetryConfig, ErrorRecovery, sanitize_user_input


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
        self.logger = logging.getLogger('DuckHuntBot.DB')
        
        # Error recovery configuration
        self.error_recovery = ErrorRecovery()
        self.save_retry_config = RetryConfig(max_attempts=3, base_delay=0.5, max_delay=5.0)
        
        data = self.load_database()
        # Hydrate in-memory state from disk.
        if isinstance(data, dict) and isinstance(data.get('channels'), dict):
            self.channels = data['channels']
        else:
            self.channels = {}

    @staticmethod
    def _normalize_channel(channel: str) -> str:
        """Normalize channel keys (case-insensitive). Non-channel contexts go to a reserved bucket."""
        if not isinstance(channel, str):
            return '__unknown__'
        channel = channel.strip()
        if not channel:
            return '__unknown__'
        # Preserve internal buckets used by the bot/database.
        # This allows explicit references like '__global__' without being remapped to '__pm__'.
        if channel.startswith('__') and channel.endswith('__'):
            return channel
        if channel.startswith('#') or channel.startswith('&'):
            return channel.lower()
        return '__pm__'

    def is_ignored(self, nick: str, channel: str) -> bool:
        """Return True if nick is ignored for this channel or globally."""
        try:
            if not isinstance(nick, str) or not nick.strip():
                return False
            nick_clean = sanitize_user_input(
                nick,
                max_length=50,
                allowed_chars='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]{}^`|\\'
            )
            nick_lower = nick_clean.lower().strip()
            if not nick_lower:
                return False

            # Channel-scoped ignore
            player = self.get_player_if_exists(nick_lower, channel)
            if isinstance(player, dict) and bool(player.get('ignored', False)):
                return True

            # Global ignore bucket
            global_player = self.get_player_if_exists(nick_lower, '__global__')
            return bool(global_player and global_player.get('ignored', False))
        except Exception:
            return False

    def set_global_ignored(self, nick: str, ignored: bool) -> bool:
        """Set global ignored flag for nick (persisted)."""
        try:
            player = self.get_player(nick, '__global__')
            if not isinstance(player, dict):
                return False
            player['ignored'] = bool(ignored)
            return True
        except Exception:
            return False

    @property
    def players(self):
        """Backward-compatible flattened view of all players across channels."""
        flattened = {}
        try:
            for _channel_key, channel_data in (self.channels or {}).items():
                players = channel_data.get('players', {}) if isinstance(channel_data, dict) else {}
                if isinstance(players, dict):
                    for nick, player in players.items():
                        # Last-write-wins if the same nick exists in multiple channels.
                        flattened[nick] = player
        except Exception:
            return {}
        return flattened
    
    def load_database(self) -> dict:
        """Load the database, creating it if it doesn't exist"""
        try:
            if not os.path.exists(self.db_file):
                self.logger.info(f"Database file {self.db_file} not found, creating new one")
                return self._create_default_database()
            
            with open(self.db_file, 'r') as f:
                content = f.read().strip()
                
            if not content:
                self.logger.warning("Database file is empty, creating new database")
                return self._create_default_database()
            
            data = json.loads(content)
            
            # Validate basic structure
            if not isinstance(data, dict):
                raise ValueError("Database root is not a dictionary")
            
            # Initialize metadata if missing
            if 'metadata' not in data:
                data['metadata'] = {
                    'version': '1.0',
                    'created': datetime.now().isoformat(),
                    'last_modified': datetime.now().isoformat()
                }

            # Migrate legacy flat structure (players) -> channels
            if 'channels' not in data or not isinstance(data.get('channels'), dict):
                legacy_players = data.get('players') if isinstance(data.get('players'), dict) else {}
                channels = {}
                if isinstance(legacy_players, dict):
                    for legacy_nick, legacy_player in legacy_players.items():
                        try:
                            last_channel = legacy_player.get('last_activity_channel') if isinstance(legacy_player, dict) else None
                            channel_key = self._normalize_channel(last_channel) if last_channel else '__global__'
                            channels.setdefault(channel_key, {'players': {}})
                            if isinstance(channels[channel_key].get('players'), dict):
                                channels[channel_key]['players'][str(legacy_nick).lower()] = legacy_player
                        except Exception:
                            continue

                data['channels'] = channels
                data['metadata']['version'] = '2.0'

            # Ensure channels structure exists
            if 'channels' not in data:
                data['channels'] = {}
            
            # Update last_modified
            data['metadata']['last_modified'] = datetime.now().isoformat()
            
            total_players = 0
            try:
                for _c, cdata in data.get('channels', {}).items():
                    if isinstance(cdata, dict) and isinstance(cdata.get('players'), dict):
                        total_players += len(cdata['players'])
            except Exception:
                total_players = 0
            self.logger.info(f"Successfully loaded database with {total_players} players across {len(data.get('channels', {}))} channels")
            return data
            
        except (json.JSONDecodeError, ValueError) as e:
            self.logger.error(f"Database corruption detected: {e}. Creating new database.")
            return self._create_default_database()
        except Exception as e:
            self.logger.error(f"Error loading database: {e}")
            return self._create_default_database()
    
    def _create_default_database(self) -> dict:
        """Create a new default database file with proper structure"""
        try:
            default_data = {
                "channels": {},
                "last_save": str(time.time()),
                "version": "2.0",
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "description": "DuckHunt Bot Player Database"
            }
            
            with open(self.db_file, 'w', encoding='utf-8') as f:
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
                "description": "DuckHunt Bot Player Database"
            }
    
    def _sanitize_player_data(self, player_data):
        """Sanitize and validate player data, ensuring ALL required fields exist"""
        try:
            sanitized = {}
            
            # Get default values from config or fallbacks
            default_accuracy = self.bot.get_config('player_defaults.accuracy', 75) if self.bot else 75
            max_accuracy = self.bot.get_config('gameplay.max_accuracy', 100) if self.bot else 100
            default_magazines = self.bot.get_config('player_defaults.magazines', 3) if self.bot else 3
            default_bullets_per_mag = self.bot.get_config('player_defaults.bullets_per_magazine', 6) if self.bot else 6
            default_jam_chance = self.bot.get_config('player_defaults.jam_chance', 15) if self.bot else 15
            
            # Core required fields - these MUST exist for messages to work
            sanitized['nick'] = str(player_data.get('nick', 'Unknown'))[:50]
            sanitized['xp'] = max(0, int(float(player_data.get('xp', 0))))
            sanitized['ducks_shot'] = max(0, int(float(player_data.get('ducks_shot', 0))))
            sanitized['ducks_befriended'] = max(0, int(float(player_data.get('ducks_befriended', 0))))
            sanitized['shots_fired'] = max(0, int(float(player_data.get('shots_fired', 0))))
            sanitized['shots_missed'] = max(0, int(float(player_data.get('shots_missed', 0))))
            
            # Equipment and stats
            sanitized['accuracy'] = max(0, min(max_accuracy, int(float(player_data.get('accuracy', default_accuracy)))))
            sanitized['gun_confiscated'] = bool(player_data.get('gun_confiscated', False))

            # Activity / admin flags
            sanitized['last_activity_channel'] = str(player_data.get('last_activity_channel', ''))[:100]
            try:
                sanitized['last_activity_time'] = float(player_data.get('last_activity_time', 0.0))
            except (ValueError, TypeError):
                sanitized['last_activity_time'] = 0.0
            sanitized['ignored'] = bool(player_data.get('ignored', False))
            
            # Ammo system with validation
            sanitized['current_ammo'] = max(0, min(50, int(float(player_data.get('current_ammo', default_bullets_per_mag)))))
            sanitized['magazines'] = max(0, min(20, int(float(player_data.get('magazines', default_magazines)))))
            sanitized['bullets_per_magazine'] = max(1, min(50, int(float(player_data.get('bullets_per_magazine', default_bullets_per_mag)))))
            sanitized['jam_chance'] = max(0, min(100, int(float(player_data.get('jam_chance', default_jam_chance)))))
            
            # Confiscated ammo (optional fields but with safe defaults)
            sanitized['confiscated_ammo'] = max(0, min(50, int(float(player_data.get('confiscated_ammo', 0)))))
            sanitized['confiscated_magazines'] = max(0, min(20, int(float(player_data.get('confiscated_magazines', 0)))))
            
            # Safe inventory handling
            inventory = player_data.get('inventory', {})
            if isinstance(inventory, dict):
                clean_inventory = {}
                for k, v in inventory.items():
                    try:
                        clean_key = str(k)[:20]
                        clean_value = max(0, int(float(v))) if isinstance(v, (int, float, str)) else 0
                        if clean_value > 0:
                            clean_inventory[clean_key] = clean_value
                    except (ValueError, TypeError):
                        continue
                sanitized['inventory'] = clean_inventory
            else:
                sanitized['inventory'] = {}
            
            # Safe temporary effects
            temp_effects = player_data.get('temporary_effects', [])
            if isinstance(temp_effects, list):
                clean_effects = []
                for effect in temp_effects[:20]:
                    if isinstance(effect, dict) and 'type' in effect:
                        clean_effects.append(effect)
                sanitized['temporary_effects'] = clean_effects
            else:
                sanitized['temporary_effects'] = []
            
            # Add any missing fields that messages might reference
            additional_fields = {
                'best_time': 0.0,
                'worst_time': 0.0,
                'total_time_hunting': 0.0,
                'level': 1,
                'xp_gained': 0,  # For message templates
                'hp_remaining': 0,  # For golden duck messages
                'victim': '',  # For friendly fire messages
                'xp_lost': 0,  # For penalty messages
                'ammo': 0,  # Legacy field
                'max_ammo': 0,  # Legacy field
                'chargers': 0  # Legacy field
            }
            
            for field, default_value in additional_fields.items():
                if field not in sanitized:
                    if field in ['best_time', 'worst_time', 'total_time_hunting']:
                        sanitized[field] = max(0.0, float(player_data.get(field, default_value)))
                    else:
                        sanitized[field] = player_data.get(field, default_value)
            
            return sanitized
            
        except Exception as e:
            self.logger.error(f"Error sanitizing player data: {e}")
            return self.create_player(player_data.get('nick', 'Unknown') if isinstance(player_data, dict) else 'Unknown')
    
    @with_retry(RetryConfig(max_attempts=3, base_delay=0.5, max_delay=5.0), 
                exceptions=(OSError, PermissionError, IOError))
    def save_database(self):
        """Save all player data to JSON file with retry logic and comprehensive error handling"""
        return self._save_database_impl()
    
    def _save_database_impl(self):
        """Internal implementation of database save"""
        temp_file = f"{self.db_file}.tmp"
        
        try:
            # Prepare data with validation
            data = {
                'channels': {},
                'last_save': str(time.time()),
                'version': '2.0'
            }

            # Validate and clean player data before saving
            valid_count = 0
            for channel_key, channel_data in (self.channels or {}).items():
                if not isinstance(channel_key, str) or not isinstance(channel_data, dict):
                    continue
                players = channel_data.get('players', {})
                if not isinstance(players, dict):
                    continue

                out_channel_key = str(channel_key)
                data['channels'].setdefault(out_channel_key, {'players': {}})
                for nick, player_data in players.items():
                    if isinstance(nick, str) and isinstance(player_data, dict):
                        try:
                            sanitized_nick = sanitize_user_input(nick, max_length=50)
                            data['channels'][out_channel_key]['players'][sanitized_nick] = self._sanitize_player_data(player_data)
                            valid_count += 1
                        except Exception as e:
                            self.logger.warning(f"Error processing player {nick} in {out_channel_key} during save: {e}")

            # Saving an empty database is valid (e.g., first run or after admin wipes).
            # Previously this raised and prevented the file from being written/updated.
            
            # Write to temporary file first (atomic write)
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            
            # Verify temp file was written correctly
            try:
                with open(temp_file, 'r', encoding='utf-8') as f:
                    json.load(f)  # Verify it's valid JSON
            except json.JSONDecodeError:
                raise IOError("Temporary file contains invalid JSON")
            
            # Atomic replace
            if os.name == 'nt':  # Windows
                if os.path.exists(self.db_file):
                    os.remove(self.db_file)
                os.rename(temp_file, self.db_file)
            else:  # Unix-like systems
                os.rename(temp_file, self.db_file)
            
            self.logger.debug(f"Database saved successfully with {valid_count} players")
            return True
            
        except Exception as e:
            self.logger.error(f"Error in database save implementation: {e}")
            raise  # Re-raise for retry mechanism
        finally:
            # Clean up temp file if it still exists
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass
    
    def get_players_for_channel(self, channel: str) -> dict:
        """Get the players dict for a channel, creating the channel bucket if needed."""
        channel_key = self._normalize_channel(channel)
        bucket = self.channels.setdefault(channel_key, {'players': {}})
        if not isinstance(bucket, dict):
            bucket = {'players': {}}
            self.channels[channel_key] = bucket
        if 'players' not in bucket or not isinstance(bucket.get('players'), dict):
            bucket['players'] = {}
        return bucket['players']

    def iter_all_players(self):
        """Yield (channel_key, nick, player_dict) for all players in all channels."""
        for channel_key, channel_data in (self.channels or {}).items():
            if not isinstance(channel_data, dict):
                continue
            players = channel_data.get('players', {})
            if not isinstance(players, dict):
                continue
            for nick, player in players.items():
                yield channel_key, nick, player

    def get_player_if_exists(self, nick, channel: str):
        """Return player dict for nick+channel if present; does not create records."""
        try:
            if not isinstance(nick, str) or not nick.strip():
                return None
            nick_clean = sanitize_user_input(nick, max_length=50,
                                           allowed_chars='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]{}^`|\\')
            nick_lower = nick_clean.lower().strip()
            if not nick_lower:
                return None
            channel_key = self._normalize_channel(channel)
            channel_data = self.channels.get(channel_key)
            if not isinstance(channel_data, dict):
                return None
            players = channel_data.get('players')
            if not isinstance(players, dict):
                return None
            player = players.get(nick_lower)
            return player if isinstance(player, dict) else None
        except Exception:
            return None

    def get_player(self, nick, channel: str):
        """Get player data for a specific channel, creating if doesn't exist with comprehensive validation"""
        try:
            # Validate and sanitize nick
            if not isinstance(nick, str) or not nick.strip():
                self.logger.warning(f"Invalid nick provided: {nick}")
                return self.error_recovery.safe_execute(
                    lambda: self.create_player('Unknown'),
                    fallback={'nick': 'Unknown', 'xp': 0, 'ducks_shot': 0},
                    logger=self.logger
                )
            
            # Sanitize nick input
            nick_clean = sanitize_user_input(nick, max_length=50, 
                                           allowed_chars='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]{}^`|\\')
            nick_lower = nick_clean.lower().strip()
            
            if not nick_lower:
                self.logger.warning(f"Empty nick after sanitization: {nick}")
                return self.create_player('Unknown')

            players = self.get_players_for_channel(channel)

            if nick_lower not in players:
                players[nick_lower] = self.create_player(nick_clean)
            else:
                # Ensure existing players have all required fields
                player = players[nick_lower]
                if not isinstance(player, dict):
                    self.logger.warning(f"Invalid player data for {nick_lower}, recreating")
                    players[nick_lower] = self.create_player(nick_clean)
                else:
                    # Migrate and validate existing player data with error recovery
                    validated = self.error_recovery.safe_execute(
                        lambda: self._migrate_and_validate_player(player, nick_clean),
                        fallback=self.create_player(nick_clean),
                        logger=self.logger
                    )
                    players[nick_lower] = validated

            return players[nick_lower]
            
        except Exception as e:
            self.logger.error(f"Critical error getting player {nick}: {e}")
            return self.create_player(nick if isinstance(nick, str) else 'Unknown')
    
    def _migrate_and_validate_player(self, player, nick):
        """Migrate old player data and validate all fields"""
        try:
            # Start with sanitized data
            validated_player = self._sanitize_player_data(player)
            
            # Migrate from old ammo/chargers system to magazine system if needed
            if 'magazines' not in player and ('ammo' in player or 'chargers' in player):
                self.logger.info(f"Migrating {nick} from old ammo system to magazine system")
                
                old_ammo = player.get('ammo', 6)
                old_chargers = player.get('chargers', 2)
                
                validated_player['current_ammo'] = max(0, min(50, int(old_ammo)))
                validated_player['magazines'] = max(1, min(20, int(old_chargers) + 1))
                validated_player['bullets_per_magazine'] = 6
            
            # Update nick in case it changed
            validated_player['nick'] = str(nick)[:50]
            
            return validated_player
            
        except Exception as e:
            self.logger.error(f"Error migrating player data for {nick}: {e}")
            return self.create_player(nick)
    
    def create_player(self, nick):
        """Create a new player with all required fields"""
        try:
            safe_nick = str(nick)[:50] if nick else 'Unknown'
            
            # Get configurable defaults from bot config
            if self.bot:
                accuracy = self.bot.get_config('player_defaults.accuracy', 75)
                magazines = self.bot.get_config('player_defaults.magazines', 3)
                bullets_per_mag = self.bot.get_config('player_defaults.bullets_per_magazine', 6)
                jam_chance = self.bot.get_config('player_defaults.jam_chance', 15)
                xp = self.bot.get_config('player_defaults.xp', 0)
            else:
                accuracy = 75
                magazines = 3
                bullets_per_mag = 6
                jam_chance = 15
                xp = 0
            
            return {
                'nick': safe_nick,
                'xp': xp,
                'ducks_shot': 0,
                'ducks_befriended': 0,
                'shots_fired': 0,
                'shots_missed': 0,
                'current_ammo': bullets_per_mag,
                'magazines': magazines,
                'bullets_per_magazine': bullets_per_mag,
                'accuracy': accuracy,
                'jam_chance': jam_chance,
                'gun_confiscated': False,
                'confiscated_ammo': 0,
                'confiscated_magazines': 0,
                'inventory': {},
                'temporary_effects': [],
                'last_activity_channel': '',
                'last_activity_time': 0.0,
                'ignored': False,
                # Additional fields to prevent KeyErrors
                'best_time': 0.0,
                'worst_time': 0.0,
                'total_time_hunting': 0.0,
                'level': 1,
                'xp_gained': 0,
                'hp_remaining': 0,
                'victim': '',
                'xp_lost': 0,
                'ammo': bullets_per_mag,  # Legacy
                'max_ammo': bullets_per_mag,  # Legacy
                'chargers': magazines - 1  # Legacy
            }
        except Exception as e:
            self.logger.error(f"Error creating player for {nick}: {e}")
            return {
                'nick': 'Unknown',
                'xp': 0,
                'ducks_shot': 0,
                'ducks_befriended': 0,
                'shots_fired': 0,
                'shots_missed': 0,
                'current_ammo': 6,
                'magazines': 3,
                'bullets_per_magazine': 6,
                'accuracy': 75,
                'jam_chance': 15,
                'gun_confiscated': False,
                'confiscated_ammo': 0,
                'confiscated_magazines': 0,
                'inventory': {},
                'temporary_effects': [],
                'last_activity_channel': '',
                'last_activity_time': 0.0,
                'ignored': False,
                'best_time': 0.0,
                'worst_time': 0.0,
                'total_time_hunting': 0.0,
                'level': 1,
                'xp_gained': 0,
                'hp_remaining': 0,
                'victim': '',
                'xp_lost': 0,
                'ammo': 6,
                'max_ammo': 6,
                'chargers': 2
            }

    def get_leaderboard(self, channel: str, category='xp', limit=3):
        """Get top players by specified category for a given channel"""
        try:
            leaderboard = []

            players = self.get_players_for_channel(channel)
            for nick, player_data in players.items():
                sanitized_data = self._sanitize_player_data(player_data)
                
                if category == 'xp':
                    value = sanitized_data.get('xp', 0)
                elif category == 'ducks_shot':
                    value = sanitized_data.get('ducks_shot', 0)
                else:
                    continue
                    
                leaderboard.append((nick, value))
            
            leaderboard.sort(key=lambda x: x[1], reverse=True)
            return leaderboard[:limit]
            
        except Exception as e:
            self.logger.error(f"Error getting leaderboard for {category}: {e}")
            return []