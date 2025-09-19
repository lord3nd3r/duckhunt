#!/usr/bin/env python3
"""
Game mechanics for DuckHunt Bot
"""

import asyncio
import random
import time
import uuid
import logging
from typing import Dict, Any, Optional, List


class DuckGame:
    """Game mechanics and duck management"""
    
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        self.ducks = {}  # Format: {channel: [{'alive': True, 'spawn_time': time, 'id': uuid}, ...]}
        self.logger = logging.getLogger('DuckHuntBot.Game')
        
        # Colors for IRC messages
        self.colors = {
            'red': '\x0304',
            'green': '\x0303',
            'yellow': '\x0308',
            'blue': '\x0302',
            'cyan': '\x0311',
            'magenta': '\x0306',
            'white': '\x0300',
            'bold': '\x02',
            'reset': '\x03',
            'underline': '\x1f'
        }
        
    def get_config(self, path, default=None):
        """Helper method to get config values"""
        return self.bot.get_config(path, default)
        
    def get_player_level(self, xp):
        """Calculate player level from XP"""
        if xp < 0:
            return 0
        return int((xp ** 0.5) / 2) + 1
    
    def get_xp_for_next_level(self, xp):
        """Calculate XP needed for next level"""
        level = self.get_player_level(xp)
        return ((level * 2) ** 2) - xp
    
    def calculate_penalty_by_level(self, base_penalty, xp):
        """Reduce penalties for higher level players"""
        level = self.get_player_level(xp)
        return max(1, base_penalty - (level - 1))
    
    def update_karma(self, player, event):
        """Update player karma based on events"""
        if not self.get_config('karma.enabled', True):
            return
            
        karma_changes = {
            'hit': self.get_config('karma.hit_bonus', 2),
            'golden_hit': self.get_config('karma.golden_hit_bonus', 5),
            'teamkill': -self.get_config('karma.teamkill_penalty', 10),
            'wild_shot': -self.get_config('karma.wild_shot_penalty', 3),
            'miss': -self.get_config('karma.miss_penalty', 1),
            'befriend_success': self.get_config('karma.befriend_success_bonus', 2),
            'befriend_fail': -self.get_config('karma.befriend_fail_penalty', 1)
        }
        
        if event in karma_changes:
            player['karma'] = player.get('karma', 0) + karma_changes[event]
    
    def is_sleep_time(self):
        """Check if ducks should not spawn due to sleep hours"""
        sleep_hours = self.get_config('sleep_hours', [])
        if not sleep_hours or len(sleep_hours) != 2:
            return False
            
        import datetime
        current_hour = datetime.datetime.now().hour
        start_hour, end_hour = sleep_hours
        
        if start_hour <= end_hour:
            return start_hour <= current_hour <= end_hour
        else:  # Crosses midnight
            return current_hour >= start_hour or current_hour <= end_hour
    
    def calculate_gun_reliability(self, player):
        """Calculate gun reliability with modifiers"""
        base_reliability = player.get('reliability', 70)
        # Add weapon modifiers, items, etc.
        return min(100, max(0, base_reliability))
    
    def gun_jams(self, player):
        """Check if gun jams (eggdrop style)"""
        # Base jamming probability is inverse of reliability
        reliability = player.get('reliability', 70)
        jam_chance = max(1, 101 - reliability)  # Higher reliability = lower jam chance
        
        # Additional factors that increase jam chance
        if player.get('total_ammo_used', 0) > 100:
            jam_chance += 2  # Gun gets more prone to jamming with use
            
        if player.get('jammed_count', 0) > 5:
            jam_chance += 1  # Previously jammed guns are more prone to jamming
            
        # Roll for jam (1-100, jam if roll <= jam_chance)
        return random.randint(1, 100) <= jam_chance
                
    async def scare_other_ducks(self, channel, shot_duck_id):
        """Scare other ducks when one is shot"""
        if channel not in self.ducks:
            return
            
        for duck in self.ducks[channel][:]:  # Copy list to avoid modification during iteration
            if duck['id'] != shot_duck_id and duck['alive']:
                # 30% chance to scare away other ducks
                if random.random() < 0.3:
                    duck['alive'] = False
                    self.ducks[channel].remove(duck)
                    
    async def scare_duck_on_miss(self, channel, target_duck):
        """Scare duck when someone misses"""
        if target_duck and random.random() < 0.15:  # 15% chance
            target_duck['alive'] = False
            if channel in self.ducks and target_duck in self.ducks[channel]:
                self.ducks[channel].remove(target_duck)
                
    async def find_bushes_items(self, nick, channel, player):
        """Find random items in bushes"""
        if not self.get_config('items.enabled', True):
            return
            
        if random.random() < 0.1:  # 10% chance
            items = [
                ("a mirror", "mirror", "You can now deflect shots!"),
                ("some sand", "sand", "Throw this to blind opponents!"),
                ("a rusty bullet", None, "It's too rusty to use..."),
                ("some bread crumbs", "bread", "Feed ducks to make them friendly!"),
            ]
            
            found_item, item_key, message = random.choice(items)
            
            if item_key and item_key in player:
                player[item_key] = player.get(item_key, 0) + 1
            elif item_key in player:
                player[item_key] = player.get(item_key, 0) + 1
                
            await self.bot.send_user_message(nick, channel, 
                f"You found {found_item} in the bushes! {message}")
    
    def get_duck_spawn_message(self):
        """Get random duck spawn message (eggdrop style)"""
        messages = [
            "-.,¸¸.-·°'`'°·-.,¸¸.-·°'`'°·   \\_O<   QUACK",
            "-.,¸¸.-·°'`'°·-.,¸¸.-·°'`'°·   \\_o<   QUACK!", 
            "-.,¸¸.-·°'`'°·-.,¸¸.-·°'`'°·   \\_O<   QUAAACK!",
            "-.,¸¸.-·°'`'°·-.,¸¸.-·°'`'°·   \\_ö<   Quack?",
            "-.,¸¸.-·°'`'°·-.,¸¸.-·°'`'°·   \\_O<   *QUACK*"
        ]
        return random.choice(messages)
    
    async def spawn_duck_now(self, channel, force_golden=False):
        """Spawn a duck immediately in the specified channel"""
        if channel not in self.ducks:
            self.ducks[channel] = []
            
        max_ducks = self.get_config('max_ducks_per_channel', 3)
        if len([d for d in self.ducks[channel] if d['alive']]) >= max_ducks:
            self.logger.debug(f"Max ducks already in {channel}")
            return
            
        # Determine duck type
        if force_golden:
            duck_type = "golden"
        else:
            rand = random.random()
            if rand < 0.02:
                duck_type = "armored"
            elif rand < 0.10:
                duck_type = "golden"
            elif rand < 0.30:
                duck_type = "rare"
            elif rand < 0.40:
                duck_type = "fast"
            else:
                duck_type = "normal"
                
        # Get duck configuration
        duck_config = self.get_config(f'duck_types.{duck_type}', {})
        if not duck_config.get('enabled', True):
            duck_type = "normal"
            duck_config = self.get_config('duck_types.normal', {})
            
        # Create duck
        duck = {
            'id': str(uuid.uuid4())[:8],
            'type': duck_type,
            'alive': True,
            'spawn_time': time.time(),
            'health': duck_config.get('health', 1),
            'max_health': duck_config.get('health', 1)
        }
        
        self.ducks[channel].append(duck)
        
        # Send spawn message
        messages = duck_config.get('messages', [self.get_duck_spawn_message()])
        spawn_message = random.choice(messages)
        
        self.bot.send_message(channel, spawn_message)
        self.logger.info(f"Spawned {duck_type} duck in {channel}")
        
        # Alert users who have alerts enabled
        await self.send_duck_alerts(channel, duck_type)
        
        return duck
    
    async def send_duck_alerts(self, channel, duck_type):
        """Send alerts to users who have them enabled"""
        if not self.get_config('social.duck_alerts_enabled', True):
            return
            
        # Implementation would iterate through players with alerts enabled
        # For now, just log
        self.logger.debug(f"Duck alerts for {duck_type} duck in {channel}")
    
    async def spawn_ducks(self):
        """Main duck spawning loop"""
        while not self.bot.shutdown_requested:
            try:
                if self.is_sleep_time():
                    await asyncio.sleep(300)  # Check every 5 minutes during sleep
                    continue
                    
                for channel in self.bot.channels_joined:
                    if self.bot.shutdown_requested:
                        break
                        
                    if channel not in self.ducks:
                        self.ducks[channel] = []
                        
                    # Clean up dead ducks
                    self.ducks[channel] = [d for d in self.ducks[channel] if d['alive']]
                    
                    max_ducks = self.get_config('max_ducks_per_channel', 3)
                    alive_ducks = len([d for d in self.ducks[channel] if d['alive']])
                    
                    if alive_ducks < max_ducks:
                        min_spawn_time = self.get_config('duck_spawn_min', 1800)
                        max_spawn_time = self.get_config('duck_spawn_max', 5400)
                        
                        if random.random() < 0.1:  # 10% chance each check
                            await self.spawn_duck_now(channel)
                            
                await asyncio.sleep(random.randint(60, 300))  # Check every 1-5 minutes
                
            except asyncio.CancelledError:
                self.logger.info("Duck spawning loop cancelled")
                break
            except Exception as e:
                self.logger.error(f"Error in duck spawning: {e}")
                await asyncio.sleep(60)
    
    async def duck_timeout_checker(self):
        """Check for ducks that should timeout"""
        while not self.bot.shutdown_requested:
            try:
                current_time = time.time()
                
                for channel in list(self.ducks.keys()):
                    if self.bot.shutdown_requested:
                        break
                        
                    if channel not in self.ducks:
                        continue
                        
                    for duck in self.ducks[channel][:]:  # Copy to avoid modification
                        if not duck['alive']:
                            continue
                            
                        age = current_time - duck['spawn_time']
                        min_timeout = self.get_config('duck_timeout_min', 45)
                        max_timeout = self.get_config('duck_timeout_max', 75)
                        
                        timeout = random.randint(min_timeout, max_timeout)
                        
                        if age > timeout:
                            duck['alive'] = False
                            self.ducks[channel].remove(duck)
                            
                            # Send timeout message (eggdrop style)
                            timeout_messages = [
                                "-.,¸¸.-·°'`'°·-.,¸¸.-·°'`'°·   \\_o>   The duck flew away!",
                                "-.,¸¸.-·°'`'°·-.,¸¸.-·°'`'°·   \\_O>   *FLAP FLAP FLAP*",
                                "-.,¸¸.-·°'`'°·-.,¸¸.-·°'`'°·   \\_o>   The duck got tired of waiting and left!",
                                "-.,¸¸.-·°'`'°·-.,¸¸.-·°'`'°·   \\_O>   *KWAK* The duck escaped!"
                            ]
                            self.bot.send_message(channel, random.choice(timeout_messages))
                            self.logger.debug(f"Duck timed out in {channel}")
                
                await asyncio.sleep(10)  # Check every 10 seconds
                
            except asyncio.CancelledError:
                self.logger.info("Duck timeout checker cancelled")
                break
            except Exception as e:
                self.logger.error(f"Error in duck timeout checker: {e}")
                await asyncio.sleep(30)
    
    def get_alive_ducks(self, channel):
        """Get list of alive ducks in channel"""
        if channel not in self.ducks:
            return []
        return [d for d in self.ducks[channel] if d['alive']]
    
    def get_duck_by_id(self, channel, duck_id):
        """Get duck by ID"""
        if channel not in self.ducks:
            return None
        for duck in self.ducks[channel]:
            if duck['id'] == duck_id and duck['alive']:
                return duck
        return None