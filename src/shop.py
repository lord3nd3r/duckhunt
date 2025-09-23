"""
Shop system for DuckHunt Bot
Handles loading items, purchasing, and item effects including player-vs-player actions
"""

import json
import os
import time
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
    
    def purchase_item(self, player: Dict[str, Any], item_id: int, target_player: Optional[Dict[str, Any]] = None, store_in_inventory: bool = False) -> Dict[str, Any]:
        """
        Purchase an item and either store in inventory or apply immediately
        Returns a result dictionary with success status and details
        """
        item = self.get_item(item_id)
        if not item:
            return {"success": False, "error": "invalid_id", "message": "Invalid item ID"}
        
        # If storing in inventory and item requires a target, that's invalid
        if store_in_inventory and item.get('target_required', False):
            return {
                "success": False,
                "error": "invalid_storage",
                "message": f"{item['name']} cannot be stored - it targets other players",
                "item_name": item['name']
            }
        
        # Check if item requires a target (only when not storing)
        if not store_in_inventory and item.get('target_required', False) and not target_player:
            return {
                "success": False, 
                "error": "target_required", 
                "message": f"{item['name']} requires a target player",
                "item_name": item['name']
            }
        
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
        
        if store_in_inventory:
            # Add to inventory
            inventory = player.get('inventory', {})
            item_id_str = str(item_id)
            current_count = inventory.get(item_id_str, 0)
            inventory[item_id_str] = current_count + 1
            player['inventory'] = inventory
            
            return {
                "success": True,
                "item_name": item['name'],
                "price": item['price'],
                "remaining_xp": player['xp'],
                "stored_in_inventory": True,
                "inventory_count": inventory[item_id_str]
            }
        else:
            # Apply effect immediately
            if item.get('target_required', False) and target_player:
                effect_result = self._apply_item_effect(target_player, item)
                
                return {
                    "success": True,
                    "item_name": item['name'],
                    "price": item['price'],
                    "remaining_xp": player['xp'],
                    "effect": effect_result,
                    "target_affected": True
                }
            else:
                # Apply effect to purchaser
                effect_result = self._apply_item_effect(player, item)
                
                return {
                    "success": True,
                    "item_name": item['name'],
                    "price": item['price'],
                    "remaining_xp": player['xp'],
                    "effect": effect_result,
                    "target_affected": False
                }
    
    def _apply_item_effect(self, player: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
        """Apply the effect of an item to a player"""
        item_type = item.get('type', 'unknown')
        amount = item.get('amount', 0)
        
        if item_type == 'ammo':
            # Add bullets to current magazine
            current_ammo = player.get('current_ammo', 0)
            bullets_per_mag = player.get('bullets_per_magazine', 6)
            new_ammo = min(current_ammo + amount, bullets_per_mag)
            added_bullets = new_ammo - current_ammo
            player['current_ammo'] = new_ammo
            return {
                "type": "ammo",
                "added": added_bullets,
                "new_total": new_ammo,
                "max": bullets_per_mag
            }
        
        elif item_type == 'magazine':
            # Add magazines to player's inventory
            current_magazines = player.get('magazines', 1)
            new_magazines = current_magazines + amount
            player['magazines'] = new_magazines
            return {
                "type": "magazine",
                "added": amount,
                "new_total": new_magazines
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
        
        elif item_type == 'jam_resistance':
            # Reduce gun jamming chance (lower is better)
            current_jam = player.get('jam_chance', 5)  # Default 5% jam chance
            new_jam = max(current_jam - amount, 0)  # Can't go below 0%
            player['jam_chance'] = new_jam
            return {
                "type": "jam_resistance",
                "reduced": current_jam - new_jam,
                "new_total": new_jam
            }
        
        elif item_type == 'max_ammo':
            # Increase maximum ammo capacity
            current_max = player.get('max_ammo', 6)
            new_max = current_max + amount
            player['max_ammo'] = new_max
            return {
                "type": "max_ammo",
                "added": amount,
                "new_total": new_max
            }
        
        elif item_type == 'chargers':
            # Add reload chargers
            current_chargers = player.get('chargers', 2)
            new_chargers = current_chargers + amount
            player['chargers'] = new_chargers
            return {
                "type": "chargers",
                "added": amount,
                "new_total": new_chargers
            }
        
        elif item_type == 'duck_attraction':
            # Increase chance of ducks appearing when this player is online
            current_attraction = player.get('duck_attraction', 0)
            new_attraction = current_attraction + amount
            player['duck_attraction'] = new_attraction
            return {
                "type": "duck_attraction",
                "added": amount,
                "new_total": new_attraction
            }
        
        elif item_type == 'critical_hit':
            # Chance for critical hits (double XP)
            current_crit = player.get('critical_chance', 0)
            new_crit = min(current_crit + amount, 25)  # Max 25% crit chance
            player['critical_chance'] = new_crit
            return {
                "type": "critical_hit",
                "added": new_crit - current_crit,
                "new_total": new_crit
            }
        
        elif item_type == 'sabotage_jam':
            # Increase target's gun jamming chance temporarily
            current_jam = player.get('jam_chance', 5)
            new_jam = min(current_jam + amount, 50)  # Max 50% jam chance
            player['jam_chance'] = new_jam
            
            # Add temporary effect tracking
            if 'temporary_effects' not in player:
                player['temporary_effects'] = []
            
            effect = {
                'type': 'jam_increase',
                'amount': amount,
                'expires_at': time.time() + item.get('duration', 5) * 60  # duration in minutes
            }
            player['temporary_effects'].append(effect)
            
            return {
                "type": "sabotage_jam",
                "added": new_jam - current_jam,
                "new_total": new_jam,
                "duration": item.get('duration', 5)
            }
        
        elif item_type == 'sabotage_accuracy':
            # Reduce target's accuracy temporarily
            current_acc = player.get('accuracy', 65)
            new_acc = max(current_acc + amount, 10)  # Min 10% accuracy (amount is negative)
            player['accuracy'] = new_acc
            
            # Add temporary effect tracking
            if 'temporary_effects' not in player:
                player['temporary_effects'] = []
            
            effect = {
                'type': 'accuracy_reduction',
                'amount': amount,
                'expires_at': time.time() + item.get('duration', 3) * 60
            }
            player['temporary_effects'].append(effect)
            
            return {
                "type": "sabotage_accuracy", 
                "reduced": current_acc - new_acc,
                "new_total": new_acc,
                "duration": item.get('duration', 3)
            }
        
        elif item_type == 'steal_ammo':
            # Steal ammo from target player
            current_ammo = player.get('ammo', 0)
            stolen = min(amount, current_ammo)
            player['ammo'] = max(current_ammo - stolen, 0)
            
            return {
                "type": "steal_ammo",
                "stolen": stolen,
                "remaining": player['ammo']
            }
        
        elif item_type == 'clean_gun':
            # Clean gun to reduce jamming chance (positive amount reduces jam chance)
            current_jam = player.get('jam_chance', 5)  # Default 5% jam chance
            new_jam = max(current_jam + amount, 0)  # amount is negative for cleaning
            player['jam_chance'] = new_jam
            
            return {
                "type": "clean_gun",
                "reduced": current_jam - new_jam,
                "new_total": new_jam
            }
        
        else:
            self.logger.warning(f"Unknown item type: {item_type}")
            return {"type": "unknown", "message": f"Unknown effect type: {item_type}"}
    
    def use_inventory_item(self, player: Dict[str, Any], item_id: int, target_player: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Use an item from player's inventory
        Returns a result dictionary with success status and details
        """
        item = self.get_item(item_id)
        if not item:
            return {"success": False, "error": "invalid_id", "message": "Invalid item ID"}
        
        inventory = player.get('inventory', {})
        item_id_str = str(item_id)
        
        if item_id_str not in inventory or inventory[item_id_str] <= 0:
            return {
                "success": False,
                "error": "not_in_inventory",
                "message": f"You don't have any {item['name']} in your inventory",
                "item_name": item['name']
            }
        
        # Check if item requires a target
        if item.get('target_required', False) and not target_player:
            return {
                "success": False, 
                "error": "target_required", 
                "message": f"{item['name']} requires a target player",
                "item_name": item['name']
            }
        
        # Remove item from inventory
        inventory[item_id_str] -= 1
        if inventory[item_id_str] <= 0:
            del inventory[item_id_str]
        player['inventory'] = inventory
        
        # Apply effect
        if item.get('target_required', False) and target_player:
            effect_result = self._apply_item_effect(target_player, item)
            
            return {
                "success": True,
                "item_name": item['name'],
                "effect": effect_result,
                "target_affected": True,
                "remaining_in_inventory": inventory.get(item_id_str, 0)
            }
        else:
            # Apply effect to user
            effect_result = self._apply_item_effect(player, item)
            
            return {
                "success": True,
                "item_name": item['name'],
                "effect": effect_result,
                "target_affected": False,
                "remaining_in_inventory": inventory.get(item_id_str, 0)
            }
    
    def get_inventory_display(self, player: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get formatted inventory display for a player
        Returns dict with inventory info
        """
        inventory = player.get('inventory', {})
        if not inventory:
            return {
                "empty": True,
                "message": "Your inventory is empty"
            }
        
        items = []
        for item_id_str, quantity in inventory.items():
            item_id = int(item_id_str)
            item = self.get_item(item_id)
            if item:
                items.append({
                    "id": item_id,
                    "name": item['name'],
                    "quantity": quantity,
                    "description": item.get('description', 'No description')
                })
        
        return {
            "empty": False,
            "items": items,
            "total_items": len(items)
        }

    def reload_items(self) -> int:
        """Reload items from file and return count"""
        old_count = len(self.items)
        self.load_items()
        new_count = len(self.items)
        self.logger.info(f"Shop reloaded: {old_count} -> {new_count} items")
        return new_count
    
    def get_shop_display(self, player, message_manager):
        """Get formatted shop display"""
        items = []
        for item_id, item in self.get_items().items():
            item_text = message_manager.get('shop_item_format',
                                          id=item_id,
                                          name=item['name'],
                                          price=item['price'])
            items.append(item_text)
        
        shop_text = message_manager.get('shop_display',
                                      items=" | ".join(items),
                                      xp=player.get('xp', 0))
        
        return shop_text