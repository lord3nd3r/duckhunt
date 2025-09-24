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
                min_wait = self.bot.get_config('duck_spawning.spawn_min', 300)  # 5 minutes
                max_wait = self.bot.get_config('duck_spawning.spawn_max', 900)  # 15 minutes
                
                # Check for active bread effects to modify spawn timing
                spawn_multiplier = self._get_active_spawn_multiplier()
                if spawn_multiplier > 1.0:
                    # Reduce wait time when bread is active
                    min_wait = int(min_wait / spawn_multiplier)
                    max_wait = int(max_wait / spawn_multiplier)
                
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
                        # Get timeout for each duck type from config
                        duck_type = duck.get('duck_type', 'normal')
                        timeout = self.bot.get_config(f'duck_types.{duck_type}.timeout', 60)
                        
                        if current_time - duck['spawn_time'] > timeout:
                            ducks_to_remove.append(duck)
                    
                    for duck in ducks_to_remove:
                        ducks.remove(duck)
                        # Use appropriate fly away message based on duck type - revealing the type!
                        duck_type = duck.get('duck_type', 'normal')
                        if duck_type == 'golden':
                            message = self.bot.messages.get('golden_duck_flies_away')
                        elif duck_type == 'fast':
                            message = self.bot.messages.get('fast_duck_flies_away')
                        else:
                            message = self.bot.messages.get('duck_flies_away')
                        self.bot.send_message(channel, message)
                    
                    if not ducks:
                        channels_to_clear.append(channel)
                
                # Clean up empty channels
                for channel in channels_to_clear:
                    if channel in self.ducks and not self.ducks[channel]:
                        del self.ducks[channel]
                
                # Clean expired effects every loop iteration
                self._clean_expired_effects()
        
        except asyncio.CancelledError:
            self.logger.info("Duck timeout loop cancelled")
    
    async def spawn_duck(self, channel):
        """Spawn a duck in the channel"""
        if channel not in self.ducks:
            self.ducks[channel] = []
        
        # Don't spawn if there's already a duck
        if self.ducks[channel]:
            return
        
        # Determine duck type randomly
        golden_chance = self.bot.get_config('golden_duck_chance', 0.15)
        fast_chance = self.bot.get_config('fast_duck_chance', 0.25)
        
        rand = random.random()
        if rand < golden_chance:
            # Golden duck - high HP, high XP
            min_hp = self.bot.get_config('golden_duck_min_hp', 3)
            max_hp = self.bot.get_config('golden_duck_max_hp', 5)
            hp = random.randint(min_hp, max_hp)
            duck_type = 'golden'
            duck = {
                'id': f"golden_duck_{int(time.time())}_{random.randint(1000, 9999)}",
                'spawn_time': time.time(),
                'channel': channel,
                'duck_type': duck_type,
                'max_hp': hp,
                'current_hp': hp
            }
            self.logger.info(f"Golden duck (hidden) spawned in {channel} with {hp} HP")
        elif rand < golden_chance + fast_chance:
            # Fast duck - normal HP, flies away faster
            duck_type = 'fast'
            duck = {
                'id': f"fast_duck_{int(time.time())}_{random.randint(1000, 9999)}",
                'spawn_time': time.time(),
                'channel': channel,
                'duck_type': duck_type,
                'max_hp': 1,
                'current_hp': 1
            }
            self.logger.info(f"Fast duck (hidden) spawned in {channel}")
        else:
            # Normal duck
            duck_type = 'normal'
            duck = {
                'id': f"duck_{int(time.time())}_{random.randint(1000, 9999)}",
                'spawn_time': time.time(),
                'channel': channel,
                'duck_type': duck_type,
                'max_hp': 1,
                'current_hp': 1
            }
            self.logger.info(f"Normal duck spawned in {channel}")
        
        # All duck types use the same spawn message - type is hidden!
        message = self.bot.messages.get('duck_spawn')
        self.ducks[channel].append(duck)
        self.bot.send_message(channel, message)
    
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
        
        # Check for gun jamming using level-based jam chance
        jam_chance = self.bot.levels.get_jam_chance(player) / 100.0  # Convert percentage to decimal
        if random.random() < jam_chance:
            # Gun jammed! Use ammo but don't shoot
            player['current_ammo'] = player.get('current_ammo', 1) - 1
            self.db.save_database()
            return {
                'success': False,
                'message_key': 'bang_gun_jammed',
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
            # Hit! Get the duck and reveal its type
            duck = self.ducks[channel][0]
            duck_type = duck.get('duck_type', 'normal')
            
            if duck_type == 'golden':
                # Golden duck - multi-hit with high XP
                duck['current_hp'] -= 1
                xp_gained = self.bot.get_config('golden_duck_xp', 15)
                
                if duck['current_hp'] > 0:
                    # Still alive, reveal it's golden but don't remove
                    accuracy_gain = self.bot.get_config('accuracy_gain_on_hit', 1)
                    max_accuracy = self.bot.get_config('max_accuracy', 100)
                    player['accuracy'] = min(player.get('accuracy', self.bot.get_config('default_accuracy', 75)) + accuracy_gain, max_accuracy)
                    self.db.save_database()
                    return {
                        'success': True,
                        'hit': True,
                        'message_key': 'bang_hit_golden',
                        'message_args': {
                            'nick': nick,
                            'hp_remaining': duck['current_hp'],
                            'xp_gained': xp_gained
                        }
                    }
                else:
                    # Golden duck killed!
                    self.ducks[channel].pop(0)
                    xp_gained = xp_gained * duck['max_hp']  # Bonus XP for killing
                    message_key = 'bang_hit_golden_killed'
            elif duck_type == 'fast':
                # Fast duck - normal HP but higher XP
                self.ducks[channel].pop(0)
                xp_gained = self.bot.get_config('fast_duck_xp', 12)
                message_key = 'bang_hit_fast'
            else:
                # Normal duck
                self.ducks[channel].pop(0)
                xp_gained = self.bot.get_config('normal_duck_xp', 10)
                message_key = 'bang_hit'
            
            # Apply XP and level changes
            old_level = self.bot.levels.calculate_player_level(player)
            player['xp'] = player.get('xp', 0) + xp_gained
            player['ducks_shot'] = player.get('ducks_shot', 0) + 1
            accuracy_gain = self.bot.get_config('accuracy_gain_on_hit', 1)
            max_accuracy = self.bot.get_config('max_accuracy', 100)
            player['accuracy'] = min(player.get('accuracy', self.bot.get_config('default_accuracy', 75)) + accuracy_gain, max_accuracy)
            
            # Check if player leveled up and update magazines if needed
            new_level = self.bot.levels.calculate_player_level(player)
            if new_level != old_level:
                self.bot.levels.update_player_magazines(player)
            
            # If config option enabled, rearm all disarmed players when duck is shot
            if self.bot.get_config('rearm_on_duck_shot', False):
                self._rearm_all_disarmed_players()
            
            self.db.save_database()
            return {
                'success': True,
                'hit': True,
                'message_key': message_key,
                'message_args': {
                    'nick': nick,
                    'xp_gained': xp_gained,
                    'ducks_shot': player['ducks_shot']
                }
            }
        else:
            # Miss! Duck stays in the channel
            accuracy_loss = self.bot.get_config('accuracy_loss_on_miss', 2)
            min_accuracy = self.bot.get_config('min_accuracy', 10)
            player['accuracy'] = max(player.get('accuracy', self.bot.get_config('default_accuracy', 75)) - accuracy_loss, min_accuracy)
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
            
            # Lower XP gain than shooting
            xp_gained = self.bot.get_config('befriend_duck_xp', 5)
            old_level = self.bot.levels.calculate_player_level(player)
            player['xp'] = player.get('xp', 0) + xp_gained
            player['ducks_befriended'] = player.get('ducks_befriended', 0) + 1
            
            # Check if player leveled up and update magazines if needed
            new_level = self.bot.levels.calculate_player_level(player)
            if new_level != old_level:
                self.bot.levels.update_player_magazines(player)
            
            # If config option enabled, rearm all disarmed players when duck is befriended
            if self.bot.get_config('rearm_on_duck_shot', False):
                self._rearm_all_disarmed_players()
            
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
    
    def _rearm_all_disarmed_players(self):
        """Rearm all players who have been disarmed (gun confiscated)"""
        try:
            rearmed_count = 0
            for player_name, player_data in self.db.players.items():
                if player_data.get('gun_confiscated', False):
                    player_data['gun_confiscated'] = False
                    # Update magazines based on player level
                    self.bot.levels.update_player_magazines(player_data)
                    player_data['current_ammo'] = player_data.get('bullets_per_magazine', 6)
                    rearmed_count += 1
            
            if rearmed_count > 0:
                self.logger.info(f"Auto-rearmed {rearmed_count} disarmed players after duck shot")
        except Exception as e:
            self.logger.error(f"Error in _rearm_all_disarmed_players: {e}")
    
    def _get_active_spawn_multiplier(self):
        """Get the current spawn rate multiplier from active bread effects"""
        import time
        max_multiplier = 1.0
        current_time = time.time()
        
        try:
            for player_name, player_data in self.db.players.items():
                effects = player_data.get('temporary_effects', [])
                for effect in effects:
                    if (effect.get('type') == 'attract_ducks' and 
                        effect.get('expires_at', 0) > current_time):
                        multiplier = effect.get('spawn_multiplier', 1.0)
                        max_multiplier = max(max_multiplier, multiplier)
            
            return max_multiplier
        except Exception as e:
            self.logger.error(f"Error getting spawn multiplier: {e}")
            return 1.0
    
    def _clean_expired_effects(self):
        """Remove expired temporary effects from all players"""
        import time
        current_time = time.time()
        
        try:
            for player_name, player_data in self.db.players.items():
                effects = player_data.get('temporary_effects', [])
                active_effects = []
                
                for effect in effects:
                    if effect.get('expires_at', 0) > current_time:
                        active_effects.append(effect)
                
                if len(active_effects) != len(effects):
                    player_data['temporary_effects'] = active_effects
                    self.logger.debug(f"Cleaned expired effects for {player_name}")
                    
        except Exception as e:
            self.logger.error(f"Error cleaning expired effects: {e}")