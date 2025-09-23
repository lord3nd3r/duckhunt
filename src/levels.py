"""
Level system for DuckHunt Bot
Manages player levels and difficulty scaling
"""

import json
import os
import logging
from typing import Dict, Any, Optional, Tuple


class LevelManager:
    """Manages the DuckHunt level system and difficulty scaling"""
    
    def __init__(self, levels_file: str = "levels.json"):
        self.levels_file = levels_file
        self.levels_data = {}
        self.logger = logging.getLogger('DuckHuntBot.Levels')
        self.load_levels()
    
    def load_levels(self):
        """Load level definitions from JSON file"""
        try:
            if os.path.exists(self.levels_file):
                with open(self.levels_file, 'r', encoding='utf-8') as f:
                    self.levels_data = json.load(f)
                    level_count = len(self.levels_data.get('levels', {}))
                    self.logger.info(f"Loaded {level_count} levels from {self.levels_file}")
            else:
                # Fallback levels if file doesn't exist
                self.levels_data = self._get_default_levels()
                self.logger.warning(f"{self.levels_file} not found, using default levels")
        except Exception as e:
            self.logger.error(f"Error loading levels: {e}, using defaults")
            self.levels_data = self._get_default_levels()
    
    def _get_default_levels(self) -> Dict[str, Any]:
        """Default fallback level system"""
        return {
            "level_calculation": {
                "method": "total_ducks",
                "description": "Level based on total ducks interacted with"
            },
            "levels": {
                "1": {
                    "name": "Duck Novice",
                    "min_ducks": 0,
                    "max_ducks": 9,
                    "befriend_success_rate": 85,
                    "accuracy_modifier": 5,
                    "duck_spawn_speed_modifier": 1.0,
                    "description": "Just starting out"
                },
                "2": {
                    "name": "Duck Hunter",
                    "min_ducks": 10,
                    "max_ducks": 99,
                    "befriend_success_rate": 75,
                    "accuracy_modifier": 0,
                    "duck_spawn_speed_modifier": 0.8,
                    "description": "Getting experienced"
                }
            }
        }
    
    def calculate_player_level(self, player: Dict[str, Any]) -> int:
        """Calculate a player's current level based on their stats"""
        method = self.levels_data.get('level_calculation', {}).get('method', 'total_ducks')
        
        if method == 'total_ducks':
            total_ducks = player.get('ducks_shot', 0) + player.get('ducks_befriended', 0)
        elif method == 'xp':
            total_ducks = player.get('xp', 0) // 10  # 10 XP per "duck equivalent"
        else:
            total_ducks = player.get('ducks_shot', 0) + player.get('ducks_befriended', 0)
        
        # Find the appropriate level
        levels = self.levels_data.get('levels', {})
        for level_num in sorted(levels.keys(), key=int, reverse=True):
            level_data = levels[level_num]
            if total_ducks >= level_data.get('min_ducks', 0):
                return int(level_num)
        
        return 1  # Default to level 1
    
    def get_level_data(self, level: int) -> Optional[Dict[str, Any]]:
        """Get level data for a specific level"""
        return self.levels_data.get('levels', {}).get(str(level))
    
    def get_player_level_info(self, player: Dict[str, Any]) -> Dict[str, Any]:
        """Get complete level information for a player"""
        level = self.calculate_player_level(player)
        level_data = self.get_level_data(level)
        
        if not level_data:
            return {
                "level": 1,
                "name": "Duck Novice",
                "description": "Default level",
                "befriend_success_rate": 75,
                "accuracy_modifier": 0,
                "duck_spawn_speed_modifier": 1.0
            }
        
        total_ducks = player.get('ducks_shot', 0) + player.get('ducks_befriended', 0)
        
        # Calculate progress to next level
        next_level_data = self.get_level_data(level + 1)
        if next_level_data:
            ducks_needed = next_level_data.get('min_ducks', 0) - total_ducks
            next_level_name = next_level_data.get('name', f"Level {level + 1}")
        else:
            ducks_needed = 0
            next_level_name = "Max Level"
        
        return {
            "level": level,
            "name": level_data.get('name', f"Level {level}"),
            "description": level_data.get('description', ''),
            "befriend_success_rate": level_data.get('befriend_success_rate', 75),
            "accuracy_modifier": level_data.get('accuracy_modifier', 0),
            "duck_spawn_speed_modifier": level_data.get('duck_spawn_speed_modifier', 1.0),
            "total_ducks": total_ducks,
            "ducks_needed_for_next": max(0, ducks_needed),
            "next_level_name": next_level_name
        }
    
    def get_modified_accuracy(self, player: Dict[str, Any]) -> int:
        """Get player's accuracy modified by their level"""
        base_accuracy = player.get('accuracy', 65)
        level_info = self.get_player_level_info(player)
        modifier = level_info.get('accuracy_modifier', 0)
        
        # Apply modifier and clamp between 10-100
        modified_accuracy = base_accuracy + modifier
        return max(10, min(100, modified_accuracy))
    
    def get_modified_befriend_rate(self, player: Dict[str, Any], base_rate: float = 75.0) -> float:
        """Get player's befriend success rate modified by their level"""
        level_info = self.get_player_level_info(player)
        level_rate = level_info.get('befriend_success_rate', base_rate)
        
        # Return as percentage (0-100)
        return max(5.0, min(95.0, level_rate))
    
    def get_duck_spawn_modifier(self, player_levels: list) -> float:
        """Get duck spawn speed modifier based on highest level player in channel"""
        if not player_levels:
            return 1.0
        
        # Use the modifier from the highest level player (makes it harder for everyone)
        max_level = max(player_levels)
        level_data = self.get_level_data(max_level)
        
        if level_data:
            return level_data.get('duck_spawn_speed_modifier', 1.0)
        
        return 1.0
    
    def reload_levels(self) -> int:
        """Reload levels from file and return count"""
        old_count = len(self.levels_data.get('levels', {}))
        self.load_levels()
        new_count = len(self.levels_data.get('levels', {}))
        self.logger.info(f"Levels reloaded: {old_count} -> {new_count} levels")
        return new_count