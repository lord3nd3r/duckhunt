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
    
    def __init__(self, shop_file: str = "shop.json", levels_manager=None):
        self.shop_file = shop_file
        self.levels = levels_manager
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
            # Add to inventory with bounds checking
            inventory = player.get('inventory', {})
            item_id_str = str(item_id)
            current_count = inventory.get(item_id_str, 0)
            
            # Load inventory limits from config
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.json')
            max_per_item = 99  # Default limit per item type
            max_total_items = 20  # Default total items limit
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                max_total_items = config.get('gameplay', {}).get('max_inventory_items', 20)
                max_per_item = config.get('gameplay', {}).get('max_per_item_type', 99)
            except:
                pass  # Use defaults
            
            # Check individual item limit
            if current_count >= max_per_item:
                return {
                    "success": False,
                    "error": "item_limit_reached",
                    "message": f"Cannot hold more than {max_per_item} {item['name']}s",
                    "item_name": item['name']
                }
            
            # Check total inventory size limit
            total_items = sum(inventory.values())
            if total_items >= max_total_items:
                return {
                    "success": False,
                    "error": "inventory_full",
                    "message": f"Inventory full! (max {max_total_items} items)",
                    "item_name": item['name']
                }
            
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
            # Add magazines (limit checking is done before this function is called)
            current_magazines = player.get('magazines', 1)
            
            if self.levels:
                level_info = self.levels.get_player_level_info(player)
                max_magazines = level_info.get('magazines', 3)
                # Don't exceed maximum magazines for level
                magazines_to_add = min(amount, max_magazines - current_magazines)
            else:
                # Fallback if levels not available
                magazines_to_add = amount
            
            new_magazines = current_magazines + magazines_to_add
            player['magazines'] = new_magazines
            return {
                "type": "magazine",
                "added": magazines_to_add,
                "new_total": new_magazines
            }
        
        elif item_type == 'accuracy':
            # Increase accuracy up to 100%
            current_accuracy = player.get('accuracy', 75)
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
            new_luck = min(max(current_luck + amount, -50), 100)  # Bounded between -50 and +100
            player['luck_bonus'] = new_luck
            return {
                "type": "luck",
                "added": new_luck - current_luck,
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
            current_acc = player.get('accuracy', 75)
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
            new_jam = min(max(current_jam + amount, 0), 100)  # Bounded between 0% and 100%
            player['jam_chance'] = new_jam
            
            return {
                "type": "clean_gun",
                "reduced": current_jam - new_jam,
                "new_total": new_jam
            }
        
        elif item_type == 'attract_ducks':
            # Add bread effect to increase duck spawn rate
            if 'temporary_effects' not in player:
                player['temporary_effects'] = []
            
            duration = item.get('duration', 600)  # 10 minutes default
            spawn_multiplier = item.get('spawn_multiplier', 2.0)  # 2x spawn rate default
            
            effect = {
                'type': 'attract_ducks',
                'spawn_multiplier': spawn_multiplier,
                'expires_at': time.time() + duration
            }
            player['temporary_effects'].append(effect)
            
            return {
                "type": "attract_ducks",
                "spawn_multiplier": spawn_multiplier,
                "duration": duration // 60  # return duration in minutes
            }

        elif item_type == 'perfect_aim':
            # Temporarily force shots to hit (bot/game enforces this)
            if 'temporary_effects' not in player:
                player['temporary_effects'] = []

            duration = int(item.get('duration', 1800))  # seconds
            effect = {
                'type': 'perfect_aim',
                'expires_at': time.time() + max(1, duration)
            }
            player['temporary_effects'].append(effect)

            return {
                "type": "perfect_aim",
                "duration": duration
            }

        elif item_type == 'duck_radar':
            # DM alert on duck spawns (game loop sends the DM)
            if 'temporary_effects' not in player:
                player['temporary_effects'] = []

            duration = int(item.get('duration', 21600))  # seconds
            effect = {
                'type': 'duck_radar',
                'expires_at': time.time() + max(1, duration)
            }
            player['temporary_effects'].append(effect)

            return {
                "type": "duck_radar",
                "duration": duration
            }

        elif item_type == 'summon_duck':
            # Actual spawning is handled by the bot (needs channel context)
            delay = int(item.get('delay', 0))
            delay = max(0, min(delay, 86400))  # cap to 24h
            return {
                "type": "summon_duck",
                "delay": delay
            }
        
        elif item_type == 'insurance':
            # Add insurance protection against friendly fire
            if 'temporary_effects' not in player:
                player['temporary_effects'] = []
            
            duration = item.get('duration', 86400)  # 24 hours default
            protection_type = item.get('protection', 'friendly_fire')
            
            effect = {
                'type': 'insurance',
                'protection': protection_type,
                'expires_at': time.time() + duration,
                'name': 'Hunter\'s Insurance'
            }
            player['temporary_effects'].append(effect)
            
            return {
                "type": "insurance",
                "protection": protection_type,
                "duration": duration // 3600  # return duration in hours
            }
        
        elif item_type == 'buy_gun_back':
            # Restore confiscated gun with original ammo
            was_confiscated = player.get('gun_confiscated', False)
            
            if was_confiscated:
                player['gun_confiscated'] = False
                # Restore original ammo and magazines from when gun was confiscated
                restored_ammo = player.get('confiscated_ammo', 0)
                restored_magazines = player.get('confiscated_magazines', 1)
                player['current_ammo'] = restored_ammo
                player['magazines'] = restored_magazines
                # Clean up the stored values
                player.pop('confiscated_ammo', None)
                player.pop('confiscated_magazines', None)
                    
                return {
                    "type": "buy_gun_back",
                    "restored": True,
                    "ammo_restored": restored_ammo
                }
            else:
                return {
                    "type": "buy_gun_back", 
                    "restored": False,
                    "message": "Your gun is not confiscated"
                }
        

        
        elif item_type == 'dry_clothes':
            # Remove wet clothes effect
            
            # Remove any wet clothes effects
            if 'temporary_effects' in player:
                original_count = len(player['temporary_effects'])
                player['temporary_effects'] = [
                    effect for effect in player['temporary_effects']
                    if effect.get('type') != 'wet_clothes'
                ]
                new_count = len(player['temporary_effects'])
                was_wet = original_count > new_count
            else:
                was_wet = False
            
            return {
                "type": "dry_clothes",
                "was_wet": was_wet,
                "message": "You changed into dry clothes!" if was_wet else "You weren't wet!"
            }
        
        else:
            self.logger.warning(f"Unknown item type: {item_type}")
            return {"type": "unknown", "message": f"Unknown effect type: {item_type}"}
    
    def _apply_splash_water_effect(self, target_player: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
        """Apply splash water effect to target player"""
        # Load config directly without import issues
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.json')
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            wet_duration = config.get('gameplay', {}).get('wet_clothes_duration', 300)  # 5 minutes default
        except:
            wet_duration = 300  # Default 5 minutes
        
        if 'temporary_effects' not in target_player:
            target_player['temporary_effects'] = []
            
        # Add wet clothes effect
        wet_effect = {
            'type': 'wet_clothes',
            'expires_at': time.time() + wet_duration
        }
        target_player['temporary_effects'].append(wet_effect)
        
        return {
            "type": "splash_water",
            "target_soaked": True,
            "duration": wet_duration // 60  # return duration in minutes
        }
    
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
        
        # Special restrictions: Some items require targets, bread cannot have targets
        if item['type'] == 'attract_ducks' and target_player:
            return {
                "success": False,
                "error": "bread_no_target",
                "message": "Bread affects everyone in the channel - you cannot target a specific player",
                "item_name": item['name']
            }
        
        # Items that must have targets when used (but can be stored in inventory)
        target_required_items = ['sabotage_jam', 'splash_water']
        if item['type'] in target_required_items and not target_player:
            return {
                "success": False, 
                "error": "target_required", 
                "message": f"{item['name']} requires a target player to use",
                "item_name": item['name']
            }
        
        # Special checks for ammo/magazine limits
        if item['type'] == 'magazine' and self.levels:
            affected_player = target_player if target_player else player
            current_magazines = affected_player.get('magazines', 1)
            level_info = self.levels.get_player_level_info(affected_player)
            max_magazines = level_info.get('magazines', 3)
            
            if current_magazines >= max_magazines:
                return {
                    "success": False,
                    "error": "max_magazines_reached",
                    "message": f"Already at maximum magazines ({max_magazines}) for current level!",
                    "item_name": item['name']
                }
        elif item['type'] == 'ammo':
            affected_player = target_player if target_player else player
            current_ammo = affected_player.get('current_ammo', 0)
            bullets_per_mag = affected_player.get('bullets_per_magazine', 6)
            
            if current_ammo >= bullets_per_mag:
                return {
                    "success": False,
                    "error": "magazine_full",
                    "message": f"Current magazine is already full ({bullets_per_mag}/{bullets_per_mag})!",
                    "item_name": item['name']
                }
        
        # Remove item from inventory
        inventory[item_id_str] -= 1
        if inventory[item_id_str] <= 0:
            del inventory[item_id_str]
        player['inventory'] = inventory
        
        # Determine who gets the effect
        if target_player:
            # Special handling for harmful effects
            if item['type'] == 'splash_water':
                effect_result = self._apply_splash_water_effect(target_player, item)
                target_affected = True
            elif item['type'] == 'sabotage_jam':
                effect_result = self._apply_item_effect(target_player, item)
                target_affected = True
            else:
                # Beneficial items - give to target (gifting)
                effect_result = self._apply_item_effect(target_player, item)
                target_affected = True
                # Mark as gift in the result
                effect_result['is_gift'] = True
            
            return {
                "success": True,
                "item_name": item['name'],
                "effect": effect_result,
                "target_affected": target_affected,
                "remaining_in_inventory": inventory.get(item_id_str, 0)
            }
        else:
            # Apply effect to user (no target specified)
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