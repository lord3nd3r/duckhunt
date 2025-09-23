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
    
    def __init__(self, db_file="duckhunt.json"):
        self.db_file = db_file
        self.players = {}
        self.logger = logging.getLogger('DuckHuntBot.DB')
        self.load_database()
    
    def load_database(self):
        """Load player data from JSON file"""
        try:
            if os.path.exists(self.db_file):
                with open(self.db_file, 'r') as f:
                    data = json.load(f)
                    self.players = data.get('players', {})
                    self.logger.info(f"Loaded {len(self.players)} players from {self.db_file}")
            else:
                self.players = {}
                self.logger.info(f"No existing database found, starting fresh")
        except Exception as e:
            self.logger.error(f"Error loading database: {e}")
            self.players = {}
    
    def save_database(self):
        """Save all player data to JSON file"""
        try:
            data = {
                'players': self.players,
                'last_save': str(time.time())
            }
            
            # Create backup
            if os.path.exists(self.db_file):
                backup_file = f"{self.db_file}.backup"
                try:
                    with open(self.db_file, 'r') as src, open(backup_file, 'w') as dst:
                        dst.write(src.read())
                except Exception as e:
                    self.logger.warning(f"Failed to create backup: {e}")
            
            # Save main file
            with open(self.db_file, 'w') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            self.logger.error(f"Error saving database: {e}")
    
    def get_player(self, nick):
        """Get player data, creating if doesn't exist"""
        nick_lower = nick.lower()
        
        if nick_lower not in self.players:
            self.players[nick_lower] = self.create_player(nick)
        
        return self.players[nick_lower]
    
    def create_player(self, nick):
        """Create a new player with basic stats"""
        return {
            'nick': nick,
            'xp': 0,
            'ducks_shot': 0,
            'ammo': 6,
            'max_ammo': 6,
            'chargers': 2,
            'max_chargers': 2,
            'accuracy': 65,
            'gun_confiscated': False
        }