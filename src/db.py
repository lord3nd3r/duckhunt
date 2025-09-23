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
        else:
            # Ensure existing players have new fields and migrate from old system
            player = self.players[nick_lower]
            if 'ducks_befriended' not in player:
                player['ducks_befriended'] = 0
            if 'inventory' not in player:
                player['inventory'] = {}
            if 'temporary_effects' not in player:
                player['temporary_effects'] = []
            
            # Migrate from old ammo/chargers system to magazine system
            if 'magazines' not in player:
                # Convert old system: assume they had full magazines
                old_ammo = player.get('ammo', 6)
                old_chargers = player.get('chargers', 2)
                
                player['current_ammo'] = old_ammo
                player['magazines'] = old_chargers + 1  # +1 for current loaded magazine
                player['bullets_per_magazine'] = 6
                
                # Remove old fields
                if 'ammo' in player:
                    del player['ammo']
                if 'max_ammo' in player:
                    del player['max_ammo']
                if 'chargers' in player:
                    del player['chargers']
                if 'max_chargers' in player:
                    del player['max_chargers']
        
        return self.players[nick_lower]
    
    def create_player(self, nick):
        """Create a new player with basic stats"""
        return {
            'nick': nick,
            'xp': 0,
            'ducks_shot': 0,
            'ducks_befriended': 0,
            'current_ammo': 6,  # Bullets in current magazine
            'magazines': 3,     # Total magazines (including current)
            'bullets_per_magazine': 6,  # Bullets per magazine
            'accuracy': 65,
            'gun_confiscated': False,
            'inventory': {},  # {item_id: quantity}
            'temporary_effects': []  # List of temporary effects
        }