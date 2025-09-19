#!/usr/bin/env python3
"""
Database functionality for DuckHunt Bot
"""

import json
import os
import time
import asyncio
import logging


class DuckDB:
    """Database management for DuckHunt Bot"""
    
    def __init__(self, db_file="duckhunt.json"):
        self.db_file = db_file
        self.players = {}
        self._save_pending = False
        self.logger = logging.getLogger('DuckHuntBot.DB')
        
    def get_config(self, path, default=None):
        """Helper method to get config values (needs to be set by bot)"""
        # This will be set by the main bot class
        if hasattr(self, '_config_getter'):
            return self._config_getter(path, default)
        return default
        
    def set_config_getter(self, config_getter):
        """Set the config getter function from the main bot"""
        self._config_getter = config_getter
        
    def load_database(self):
        """Load player data from JSON file"""
        if os.path.exists(self.db_file):
            try:
                with open(self.db_file, 'r') as f:
                    data = json.load(f)
                    self.players = data.get('players', {})
                self.logger.info(f"Loaded {len(self.players)} players from {self.db_file}")
            except (json.JSONDecodeError, IOError) as e:
                self.logger.error(f"Error loading database: {e}")
                self.players = {}
        else:
            self.players = {}
            self.logger.info(f"Created new database: {self.db_file}")
            
    def save_database(self):
        """Save all player data to JSON file with error handling"""
        try:
            # Atomic write to prevent corruption
            temp_file = f"{self.db_file}.tmp"
            data = {
                'players': self.players,
                'last_save': str(time.time())
            }
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            # Atomic rename to replace old file
            os.replace(temp_file, self.db_file)
            
        except IOError as e:
            self.logger.error(f"Error saving database: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected database save error: {e}")
            
    def _get_starting_accuracy(self):
        """Get starting accuracy with optional randomization"""
        base_accuracy = self.get_config('new_players.starting_accuracy', 65) or 65
        if self.get_config('new_players.random_stats.enabled', False):
            import random
            variance = self.get_config('new_players.random_stats.accuracy_variance', 10) or 10
            return max(10, min(95, base_accuracy + random.randint(-variance, variance)))
        return base_accuracy
        
    def _get_starting_reliability(self):
        """Get starting reliability with optional randomization"""
        base_reliability = self.get_config('new_players.starting_reliability', 70) or 70
        if self.get_config('new_players.random_stats.enabled', False):
            import random
            variance = self.get_config('new_players.random_stats.reliability_variance', 10) or 10
            return max(10, min(95, base_reliability + random.randint(-variance, variance)))
        return base_reliability
            
    def get_player(self, user):
        """Get player data, creating if doesn't exist"""
        if '!' not in user:
            nick = user.lower()
        else:
            nick = user.split('!')[0].lower()
            
        if nick in self.players:
            player = self.players[nick]
            # Ensure backward compatibility by adding missing fields
            self._ensure_player_fields(player)
            return player
        else:
            return self.create_player(nick)
    
    def create_player(self, nick):
        """Create a new player with default stats"""
        player = {
            'shots': 6,
            'max_shots': 6,
            'chargers': 2,
            'max_chargers': 2,
            'reload_time': 5.0,
            'ducks_shot': 0,
            'ducks_befriended': 0,
            'accuracy_bonus': 0,
            'xp_bonus': 0,
            'charm_bonus': 0,
            'exp': 0,
            'money': 100,
            'last_hunt': 0,
            'last_reload': 0,
            'level': 1,
            'inventory': {},
            'ignored_users': [],
            # Gun mechanics (eggdrop style)
            'jammed': False,
            'jammed_count': 0,
            'total_ammo_used': 0,
            'shot_at': 0,
            'wild_shots': 0,
            'accuracy': self._get_starting_accuracy(),
            'reliability': self._get_starting_reliability(),
            'gun_confiscated': False,
            'confiscated_count': 0
        }
        
        self.players[nick] = player
        self.logger.info(f"Created new player: {nick}")
        return player
            
    def _ensure_player_fields(self, player):
        """Ensure player has all required fields for backward compatibility"""
        required_fields = {
            'shots': player.get('ammo', 6),  # Map old 'ammo' to 'shots'
            'max_shots': player.get('max_ammo', 6),  # Map old 'max_ammo' to 'max_shots'
            'chargers': player.get('chargers', 2),  # Map old 'chargers' (magazines)
            'max_chargers': player.get('max_chargers', 2),  # Map old 'max_chargers'
            'reload_time': 5.0,
            'ducks_shot': player.get('caught', 0),  # Map old 'caught' to 'ducks_shot'
            'ducks_befriended': player.get('befriended', 0),  # Use existing befriended count
            'accuracy_bonus': 0,
            'xp_bonus': 0,
            'charm_bonus': 0,
            'exp': player.get('xp', 0),  # Map old 'xp' to 'exp'
            'money': player.get('coins', 100),  # Map old 'coins' to 'money'
            'last_hunt': 0,
            'last_reload': 0,
            'level': 1,
            'inventory': {},
            'ignored_users': [],
            # Gun mechanics (eggdrop style)
            'jammed': False,
            'jammed_count': player.get('jammed_count', 0),
            'total_ammo_used': player.get('total_ammo_used', 0),
            'shot_at': player.get('shot_at', 0),
            'wild_shots': player.get('wild_shots', 0),
            'accuracy': player.get('accuracy', 65),
            'reliability': player.get('reliability', 70),
            'gun_confiscated': player.get('gun_confiscated', False),
            'confiscated_count': player.get('confiscated_count', 0)
        }
        
        for field, default_value in required_fields.items():
            if field not in player:
                player[field] = default_value
        
    def save_player(self, user):
        """Save player data - batch saves for performance"""
        if not self._save_pending:
            self._save_pending = True
            # Schedule delayed save to batch multiple writes
            asyncio.create_task(self._delayed_save())
    
    async def _delayed_save(self):
        """Batch save to reduce disk I/O"""
        await asyncio.sleep(0.5)  # Small delay to batch saves
        try:
            self.save_database()
            self.logger.debug("Database batch save completed")
        except Exception as e:
            self.logger.error(f"Database batch save failed: {e}")
        finally:
            self._save_pending = False