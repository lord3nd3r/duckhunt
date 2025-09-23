"""
Game mechanics for DuckHunt Bot
Handles duck spawning, shooting, befriending, and other game actions
"""

import asyncio
import random
import time
import logging


class DuckGame:
    """Game mechanics for DuckHunt - shooting, befriending, reloading"""
    
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        self.ducks = {}  # {channel: [duck1, duck2, ...]}
        self.logger = logging.getLogger('DuckHuntBot.Game')
        self.spawn_task = None
        self.timeout_task = None
    
    async def start_game_loops(self):
        """Start the game loops"""
        self.spawn_task = asyncio.create_task(self.duck_spawn_loop())
        self.timeout_task = asyncio.create_task(self.duck_timeout_loop())
        
        try:
            await asyncio.gather(self.spawn_task, self.timeout_task)
        except asyncio.CancelledError:
            self.logger.info("Game loops cancelled")
    
    async def duck_spawn_loop(self):
        """Duck spawning loop with responsive shutdown"""
        try:
            while True:
                # Wait random time between spawns, but in small chunks for responsiveness
                min_wait = self.bot.get_config('duck_spawn_min', 300)  # 5 minutes
                max_wait = self.bot.get_config('duck_spawn_max', 900)  # 15 minutes
                wait_time = random.randint(min_wait, max_wait)
                
                # Sleep in 1-second intervals to allow for quick cancellation
                for _ in range(wait_time):
                    await asyncio.sleep(1)
                
                # Spawn duck in random channel
                channels = list(self.bot.channels_joined)
                if channels:
                    channel = random.choice(channels)
                    await self.spawn_duck(channel)
        
        except asyncio.CancelledError:
            self.logger.info("Duck spawning loop cancelled")
    
    async def duck_timeout_loop(self):
        """Duck timeout loop with responsive shutdown"""
        try:
            while True:
                # Check every 2 seconds instead of 10 for more responsiveness
                await asyncio.sleep(2)
                
                current_time = time.time()
                channels_to_clear = []
                
                for channel, ducks in self.ducks.items():
                    ducks_to_remove = []
                    for duck in ducks:
                        if current_time - duck['spawn_time'] > self.bot.get_config('duck_timeout', 60):
                            ducks_to_remove.append(duck)
                    
                    for duck in ducks_to_remove:
                        ducks.remove(duck)
                        message = self.bot.messages.get('duck_flies_away')
                        self.bot.send_message(channel, message)
                    
                    if not ducks:
                        channels_to_clear.append(channel)
                
                # Clean up empty channels
                for channel in channels_to_clear:
                    if channel in self.ducks and not self.ducks[channel]:
                        del self.ducks[channel]
        
        except asyncio.CancelledError:
            self.logger.info("Duck timeout loop cancelled")
    
    async def spawn_duck(self, channel):
        """Spawn a duck in the channel"""
        if channel not in self.ducks:
            self.ducks[channel] = []
        
        # Don't spawn if there's already a duck
        if self.ducks[channel]:
            return
        
        duck = {
            'id': f"duck_{int(time.time())}_{random.randint(1000, 9999)}",
            'spawn_time': time.time(),
            'channel': channel
        }
        
        self.ducks[channel].append(duck)
        
        # Send spawn message
        message = self.bot.messages.get('duck_spawn')
        self.bot.send_message(channel, message)
        
        self.logger.info(f"Duck spawned in {channel}")
    
    def shoot_duck(self, nick, channel, player):
        """Handle shooting at a duck"""
        # Check if gun is confiscated
        if player.get('gun_confiscated', False):
            return {
                'success': False,
                'message_key': 'bang_not_armed',
                'message_args': {'nick': nick}
            }
        
        # Check ammo
        if player.get('current_ammo', 0) <= 0:
            return {
                'success': False,
                'message_key': 'bang_no_ammo',
                'message_args': {'nick': nick}
            }
        
        # Check for duck
        if channel not in self.ducks or not self.ducks[channel]:
            # Wild shot - gun confiscated
            player['current_ammo'] = player.get('current_ammo', 1) - 1
            player['gun_confiscated'] = True
            self.db.save_database()
            return {
                'success': False,
                'message_key': 'bang_no_duck',
                'message_args': {'nick': nick}
            }
        
        # Shoot at duck
        player['current_ammo'] = player.get('current_ammo', 1) - 1
        
        # Calculate hit chance using level-modified accuracy
        modified_accuracy = self.bot.levels.get_modified_accuracy(player)
        hit_chance = modified_accuracy / 100.0
        
        if random.random() < hit_chance:
            # Hit! Remove the duck
            duck = self.ducks[channel].pop(0)
            xp_gained = 10
            old_level = self.bot.levels.calculate_player_level(player)
            player['xp'] = player.get('xp', 0) + xp_gained
            player['ducks_shot'] = player.get('ducks_shot', 0) + 1
            player['accuracy'] = min(player.get('accuracy', 65) + 1, 100)
            
            # Check if player leveled up and update magazines if needed
            new_level = self.bot.levels.calculate_player_level(player)
            if new_level != old_level:
                self.bot.levels.update_player_magazines(player)
            
            self.db.save_database()
            return {
                'success': True,
                'hit': True,
                'message_key': 'bang_hit',
                'message_args': {
                    'nick': nick,
                    'xp_gained': xp_gained,
                    'ducks_shot': player['ducks_shot']
                }
            }
        else:
            # Miss! Duck stays in the channel
            player['accuracy'] = max(player.get('accuracy', 65) - 2, 10)
            self.db.save_database()
            return {
                'success': True,
                'hit': False,
                'message_key': 'bang_miss',
                'message_args': {'nick': nick}
            }
    
    def befriend_duck(self, nick, channel, player):
        """Handle befriending a duck"""
        # Check for duck
        if channel not in self.ducks or not self.ducks[channel]:
            return {
                'success': False,
                'message_key': 'bef_no_duck',
                'message_args': {'nick': nick}
            }
        
        # Check befriend success rate from config and level modifiers
        base_rate = self.bot.get_config('befriend_success_rate', 75)
        try:
            if base_rate is not None:
                base_rate = float(base_rate)
            else:
                base_rate = 75.0
        except (ValueError, TypeError):
            base_rate = 75.0
            
        # Apply level-based modification to befriend rate
        level_modified_rate = self.bot.levels.get_modified_befriend_rate(player, base_rate)
        success_rate = level_modified_rate / 100.0
        
        if random.random() < success_rate:
            # Success - befriend the duck
            duck = self.ducks[channel].pop(0)
            
            # Lower XP gain than shooting (5 instead of 10)
            xp_gained = 5
            old_level = self.bot.levels.calculate_player_level(player)
            player['xp'] = player.get('xp', 0) + xp_gained
            player['ducks_befriended'] = player.get('ducks_befriended', 0) + 1
            
            # Check if player leveled up and update magazines if needed
            new_level = self.bot.levels.calculate_player_level(player)
            if new_level != old_level:
                self.bot.levels.update_player_magazines(player)
            
            self.db.save_database()
            return {
                'success': True,
                'befriended': True,
                'message_key': 'bef_success',
                'message_args': {
                    'nick': nick,
                    'xp_gained': xp_gained,
                    'ducks_befriended': player['ducks_befriended']
                }
            }
        else:
            # Failure - duck flies away, remove from channel
            duck = self.ducks[channel].pop(0)
            
            self.db.save_database()
            return {
                'success': True,
                'befriended': False,
                'message_key': 'bef_failed',
                'message_args': {'nick': nick}
            }
    
    def reload_gun(self, nick, channel, player):
        """Handle reloading a gun (switching to a new magazine)"""
        if player.get('gun_confiscated', False):
            return {
                'success': False,
                'message_key': 'reload_not_armed',
                'message_args': {'nick': nick}
            }
        
        current_ammo = player.get('current_ammo', 0)
        bullets_per_mag = player.get('bullets_per_magazine', 6)
        
        # Check if current magazine is already full
        if current_ammo >= bullets_per_mag:
            return {
                'success': False,
                'message_key': 'reload_already_loaded',
                'message_args': {'nick': nick}
            }
        
        # Check if they have spare magazines
        total_magazines = player.get('magazines', 1)
        if total_magazines <= 1:  # Only the current magazine
            return {
                'success': False,
                'message_key': 'reload_no_chargers',
                'message_args': {'nick': nick}
            }
        
        # Reload: discard current magazine and load a new full one
        player['current_ammo'] = bullets_per_mag
        player['magazines'] = total_magazines - 1
        
        self.db.save_database()
        return {
            'success': True,
            'message_key': 'reload_success',
            'message_args': {
                'nick': nick,
                'ammo': player['current_ammo'],
                'max_ammo': bullets_per_mag,
                'chargers': player['magazines'] - 1  # Spare magazines (excluding current)
            }
        }