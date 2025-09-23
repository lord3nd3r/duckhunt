"""
Shop system for DuckHunt Bot
Handles loading items, purchasing, and item effects
"""

import json
import os
import logging
from typing import Dict, Any, Optional


class ShopManager:
    """Manages the DuckHunt shop system"""
    
    def __init__(self, shop_file: str = "shop.json"):
        self.shop_file = shop_file
        self.items = {}
        self.logger = logging.getLogger('DuckHuntBot.Shop')
        self.load_items()
    
    def load_items(self):
        """Load shop items from JSON file"""
        try:
            if os.path.exists(self.shop_file):
                with open(self.shop_file, 'r', encoding='utf-8') as f:
                    shop_data = json.load(f)
                    # Convert string keys to integers for easier handling
                    self.items = {int(k): v for k, v in shop_data.get('items', {}).items()}
                    self.logger.info(f"Loaded {len(self.items)} shop items from {self.shop_file}")
            else:
                # Fallback items if file doesn't exist
                self.items = self._get_default_items()
                self.logger.warning(f"{self.shop_file} not found, using default items")
        except Exception as e:
            self.logger.error(f"Error loading shop items: {e}, using defaults")
            self.items = self._get_default_items()
    
    def _get_default_items(self) -> Dict[int, Dict[str, Any]]:
        """Default fallback shop items"""
        return {
            1: {"name": "Single Bullet", "price": 5, "description": "1 extra bullet", "type": "ammo", "amount": 1},
            2: {"name": "Accuracy Boost", "price": 20, "description": "+10% accuracy", "type": "accuracy", "amount": 10},
            3: {"name": "Lucky Charm", "price": 30, "description": "+5% duck spawn chance", "type": "luck", "amount": 5}
        }
    
    def get_items(self) -> Dict[int, Dict[str, Any]]:
        """Get all shop items"""
        return self.items.copy()
    
    def get_item(self, item_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific shop item by ID"""
        return self.items.get(item_id)
    
    def is_valid_item(self, item_id: int) -> bool:
        """Check if item ID exists"""
        return item_id in self.items
    
    def can_afford(self, player_xp: int, item_id: int) -> bool:
        """Check if player can afford an item"""
        item = self.get_item(item_id)
        if not item:
            return False
        return player_xp >= item['price']
    
    def purchase_item(self, player: Dict[str, Any], item_id: int) -> Dict[str, Any]:
        """
        Purchase an item and apply its effects to the player
        Returns a result dictionary with success status and details
        """
        item = self.get_item(item_id)
        if not item:
            return {"success": False, "error": "invalid_id", "message": "Invalid item ID"}
        
        player_xp = player.get('xp', 0)
        if player_xp < item['price']:
            return {
                "success": False, 
                "error": "insufficient_xp", 
                "message": f"Need {item['price']} XP, have {player_xp} XP",
                "item_name": item['name'],
                "price": item['price'],
                "current_xp": player_xp
            }
        
        # Deduct XP
        player['xp'] = player_xp - item['price']
        
        # Apply item effect
        effect_result = self._apply_item_effect(player, item)
        
        return {
            "success": True,
            "item_name": item['name'],
            "price": item['price'],
            "remaining_xp": player['xp'],
            "effect": effect_result
        }
    
    def _apply_item_effect(self, player: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
        """Apply the effect of an item to a player"""
        item_type = item.get('type', 'unknown')
        amount = item.get('amount', 0)
        
        if item_type == 'ammo':
            # Add ammo up to max capacity
            current_ammo = player.get('ammo', 0)
            max_ammo = player.get('max_ammo', 6)
            new_ammo = min(current_ammo + amount, max_ammo)
            player['ammo'] = new_ammo
            return {
                "type": "ammo",
                "added": new_ammo - current_ammo,
                "new_total": new_ammo,
                "max": max_ammo
            }
        
        elif item_type == 'accuracy':
            # Increase accuracy up to 100%
            current_accuracy = player.get('accuracy', 65)
            new_accuracy = min(current_accuracy + amount, 100)
            player['accuracy'] = new_accuracy
            return {
                "type": "accuracy",
                "added": new_accuracy - current_accuracy,
                "new_total": new_accuracy
            }
        
        elif item_type == 'luck':
            # Store luck bonus (would be used in duck spawning logic)
            current_luck = player.get('luck_bonus', 0)
            new_luck = current_luck + amount
            player['luck_bonus'] = new_luck
            return {
                "type": "luck",
                "added": amount,
                "new_total": new_luck
            }
        
        else:
            self.logger.warning(f"Unknown item type: {item_type}")
            return {"type": "unknown", "message": f"Unknown effect type: {item_type}"}
    
    def reload_items(self) -> int:
        """Reload items from file and return count"""
        old_count = len(self.items)
        self.load_items()
        new_count = len(self.items)
        self.logger.info(f"Shop reloaded: {old_count} -> {new_count} items")
        return new_count