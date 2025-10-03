"""
Simplified Database management for DuckHunt Bot
Focus on fixing missing field errors
"""

import json
import logging
import time
import os


class DuckDB:
    """Simplified database management"""
    
    def __init__(self, db_file="duckhunt.json", bot=None):
        self.db_file = db_file
        self.bot = bot
        self.players = {}
        self.logger = logging.getLogger('DuckHuntBot.DB')
        self.load_database()
    
    def load_database(self):
        """Load player data from JSON file with comprehensive error handling"""
        try:
            if os.path.exists(self.db_file):
                with open(self.db_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                # Validate loaded data structure
                if not isinstance(data, dict):
                    raise ValueError("Database root is not a dictionary")
                    
                players_data = data.get('players', {})
                if not isinstance(players_data, dict):
                    raise ValueError("Players data is not a dictionary")
                
                # Validate each player entry and ensure required fields
                valid_players = {}
                for nick, player_data in players_data.items():
                    if isinstance(player_data, dict) and isinstance(nick, str):
                        # Sanitize and validate player data
                        valid_players[nick] = self._sanitize_player_data(player_data)
                    else:
                        self.logger.warning(f"Skipping invalid player entry: {nick}")
                
                self.players = valid_players
                self.logger.info(f"Loaded {len(self.players)} players from {self.db_file}")
                
            else:
                self.players = {}
                self.logger.info("No existing database found, starting fresh")
                
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.logger.error(f"Database file corrupted: {e}")
            self.players = {}
        except Exception as e:
            self.logger.error(f"Error loading database: {e}")
            self.players = {}
    
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
    
    def save_database(self):
        """Save all player data to JSON file with comprehensive error handling"""
        temp_file = f"{self.db_file}.tmp"
        
        try:
            # Prepare data with validation
            data = {
                'players': {},
                'last_save': str(time.time())
            }
            
            # Validate and clean player data before saving
            for nick, player_data in self.players.items():
                if isinstance(nick, str) and isinstance(player_data, dict):
                    data['players'][nick] = self._sanitize_player_data(player_data)
                else:
                    self.logger.warning(f"Skipping invalid player data during save: {nick}")
            
            # Write to temporary file first (atomic write)
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            
            # Atomic replace
            if os.name == 'nt':  # Windows
                if os.path.exists(self.db_file):
                    os.remove(self.db_file)
                os.rename(temp_file, self.db_file)
            else:  # Unix-like systems
                os.rename(temp_file, self.db_file)
            
            self.logger.debug(f"Database saved successfully with {len(data['players'])} players")
            
        except Exception as e:
            self.logger.error(f"Error saving database: {e}")
        finally:
            # Clean up temp file if it still exists
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass
    
    def get_player(self, nick):
        """Get player data, creating if doesn't exist with comprehensive validation"""
        try:
            # Validate and sanitize nick
            if not isinstance(nick, str) or not nick.strip():
                self.logger.warning(f"Invalid nick provided: {nick}")
                return None
            
            nick_lower = nick.lower().strip()[:50]
            
            if nick_lower not in self.players:
                self.players[nick_lower] = self.create_player(nick)
            else:
                # Ensure existing players have all required fields
                player = self.players[nick_lower]
                if not isinstance(player, dict):
                    self.logger.warning(f"Invalid player data for {nick_lower}, recreating")
                    self.players[nick_lower] = self.create_player(nick)
                else:
                    # Migrate and validate existing player data
                    self.players[nick_lower] = self._migrate_and_validate_player(player, nick)
            
            return self.players[nick_lower]
            
        except Exception as e:
            self.logger.error(f"Error getting player {nick}: {e}")
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

    def get_leaderboard(self, category='xp', limit=3):
        """Get top players by specified category"""
        try:
            leaderboard = []
            
            for nick, player_data in self.players.items():
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