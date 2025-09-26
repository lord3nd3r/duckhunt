"""
Simplified Database management for DuckHunt Bot
Only essential player fields
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
                # Try to load the main database file
                with open(self.db_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                # Validate loaded data structure
                if not isinstance(data, dict):
                    raise ValueError("Database root is not a dictionary")
                    
                players_data = data.get('players', {})
                if not isinstance(players_data, dict):
                    raise ValueError("Players data is not a dictionary")
                
                # Validate each player entry
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
        """Sanitize and validate player data"""
        try:
            sanitized = {}
            
            # Ensure required fields with safe defaults
            sanitized['nick'] = str(player_data.get('nick', 'Unknown'))[:50]  # Limit nick length
            sanitized['xp'] = max(0, int(player_data.get('xp', 0)))  # Non-negative XP
            sanitized['ducks_shot'] = max(0, int(player_data.get('ducks_shot', 0)))
            sanitized['ducks_befriended'] = max(0, int(player_data.get('ducks_befriended', 0)))
            sanitized['shots_fired'] = max(0, int(player_data.get('shots_fired', 0)))
            sanitized['shots_missed'] = max(0, int(player_data.get('shots_missed', 0)))
            default_accuracy = self.bot.get_config('default_accuracy', 75) if self.bot else 75
            max_accuracy = self.bot.get_config('max_accuracy', 100) if self.bot else 100
            sanitized['accuracy'] = max(0, min(max_accuracy, int(player_data.get('accuracy', default_accuracy))))  # 0-max_accuracy range
            sanitized['gun_confiscated'] = bool(player_data.get('gun_confiscated', False))
            
            # Ammo system with validation
            sanitized['current_ammo'] = max(0, min(50, int(player_data.get('current_ammo', 6))))
            sanitized['magazines'] = max(0, min(20, int(player_data.get('magazines', 3))))
            
            # Confiscated ammo (optional fields)
            if 'confiscated_ammo' in player_data:
                sanitized['confiscated_ammo'] = max(0, min(50, int(player_data.get('confiscated_ammo', 0))))
            if 'confiscated_magazines' in player_data:
                sanitized['confiscated_magazines'] = max(0, min(20, int(player_data.get('confiscated_magazines', 0))))
            sanitized['bullets_per_magazine'] = max(1, min(50, int(player_data.get('bullets_per_magazine', 6))))
            sanitized['jam_chance'] = max(0, min(100, int(player_data.get('jam_chance', 5))))
            
            # Safe inventory handling
            inventory = player_data.get('inventory', {})
            if isinstance(inventory, dict):
                sanitized['inventory'] = {str(k)[:10]: max(0, int(v)) for k, v in inventory.items() if isinstance(v, (int, float))}
            else:
                sanitized['inventory'] = {}
            
            # Safe temporary effects
            temp_effects = player_data.get('temporary_effects', [])
            if isinstance(temp_effects, list):
                sanitized['temporary_effects'] = temp_effects[:20]  # Limit to 20 effects
            else:
                sanitized['temporary_effects'] = []
            
            return sanitized
            
        except Exception as e:
            self.logger.error(f"Error sanitizing player data: {e}")
            return self.create_player('Unknown')
    
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
                f.flush()  # Ensure data is written to disk
                os.fsync(f.fileno())  # Force write to disk
            
            # Atomic replace: move temp file to actual file
            if os.name == 'nt':  # Windows
                if os.path.exists(self.db_file):
                    os.remove(self.db_file)
                os.rename(temp_file, self.db_file)
            else:  # Unix-like systems
                os.rename(temp_file, self.db_file)
            
            self.logger.debug(f"Database saved successfully with {len(data['players'])} players")
            
        except PermissionError:
            self.logger.error("Permission denied when saving database")
        except OSError as e:
            self.logger.error(f"OS error saving database: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected error saving database: {e}")
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
            
            nick_lower = nick.lower().strip()[:50]  # Limit nick length and sanitize
            
            if nick_lower not in self.players:
                self.players[nick_lower] = self.create_player(nick)
            else:
                # Ensure existing players have all required fields and sanitize data
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
            
            # Ensure new fields exist (migration from older versions)
            if 'ducks_befriended' not in player:
                validated_player['ducks_befriended'] = 0
            if 'inventory' not in player:
                validated_player['inventory'] = {}
            if 'temporary_effects' not in player:
                validated_player['temporary_effects'] = []
            if 'jam_chance' not in player:
                validated_player['jam_chance'] = 5  # Default 5% jam chance
            
            # Migrate from old ammo/chargers system to magazine system
            if 'magazines' not in player and ('ammo' in player or 'chargers' in player):
                self.logger.info(f"Migrating {nick} from old ammo system to magazine system")
                
                old_ammo = player.get('ammo', 6)
                old_chargers = player.get('chargers', 2)
                
                validated_player['current_ammo'] = max(0, min(50, int(old_ammo)))
                validated_player['magazines'] = max(1, min(20, int(old_chargers) + 1))  # +1 for current loaded magazine
                validated_player['bullets_per_magazine'] = 6
            
            # Update nick in case it changed
            validated_player['nick'] = str(nick)[:50]
            
            return validated_player
            
        except Exception as e:
            self.logger.error(f"Error migrating player data for {nick}: {e}")
            return self.create_player(nick)
    
    def create_player(self, nick):
        """Create a new player with configurable starting stats and inventory"""
        try:
            # Sanitize nick
            safe_nick = str(nick)[:50] if nick else 'Unknown'
            
            # Get configurable defaults from bot config
            if self.bot:
                accuracy = self.bot.get_config('player_defaults.accuracy', 75)
                magazines = self.bot.get_config('player_defaults.magazines', 3)
                bullets_per_mag = self.bot.get_config('player_defaults.bullets_per_magazine', 6)
                jam_chance = self.bot.get_config('player_defaults.jam_chance', 5)
                xp = self.bot.get_config('player_defaults.xp', 0)
            else:
                # Fallback defaults if no bot config available
                accuracy = 75
                magazines = 3
                bullets_per_mag = 6
                jam_chance = 5
                xp = 0
            
            return {
                'nick': safe_nick,
                'xp': xp,
                'ducks_shot': 0,
                'ducks_befriended': 0,
                'shots_fired': 0,  # Total shots fired
                'shots_missed': 0,  # Total shots that missed
                'current_ammo': bullets_per_mag,  # Bullets in current magazine
                'magazines': magazines,     # Total magazines (including current)  
                'bullets_per_magazine': bullets_per_mag,  # Bullets per magazine
                'accuracy': accuracy,     # Starting accuracy from config
                'jam_chance': jam_chance,    # Base gun jamming chance from config
                'gun_confiscated': False,
                'inventory': {},  # Empty starting inventory
                'temporary_effects': []  # List of temporary effects
            }
        except Exception as e:
            self.logger.error(f"Error creating player for {nick}: {e}")
            return {
                'nick': 'Unknown',
                'xp': 0,
                'ducks_shot': 0,
                'ducks_befriended': 0,
                'current_ammo': 6,
                'magazines': 3,
                'bullets_per_magazine': 6,
                'accuracy': 75,
                'jam_chance': 5,
                'gun_confiscated': False,
                'inventory': {},
                'temporary_effects': []
            }

    def get_leaderboard(self, category='xp', limit=3):
        """Get top players by specified category"""
        try:
            # Create list of (nick, value) tuples
            leaderboard = []
            
            for nick, player_data in self.players.items():
                if category == 'xp':
                    value = player_data.get('xp', 0)
                elif category == 'ducks_shot':
                    value = player_data.get('ducks_shot', 0)
                else:
                    continue
                    
                leaderboard.append((nick, value))
            
            # Sort by value (descending) and take top N
            leaderboard.sort(key=lambda x: x[1], reverse=True)
            return leaderboard[:limit]
            
        except Exception as e:
            self.logger.error(f"Error getting leaderboard for {category}: {e}")
            return []