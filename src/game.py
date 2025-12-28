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
                # Pick a target channel first so spawn multipliers are per-channel
                channels = list(self.bot.channels_joined)
                if not channels:
                    await asyncio.sleep(1)
                    continue

                channel = random.choice(channels)

                # Wait random time between spawns, but in small chunks for responsiveness
                min_wait = self.bot.get_config('duck_spawning.spawn_min', 300)  # 5 minutes
                max_wait = self.bot.get_config('duck_spawning.spawn_max', 900)  # 15 minutes
                
                # Check for active bread effects to modify spawn timing
                spawn_multiplier = self._get_active_spawn_multiplier(channel)
                if spawn_multiplier > 1.0:
                    # Reduce wait time when bread is active
                    min_wait = int(min_wait / spawn_multiplier)
                    max_wait = int(max_wait / spawn_multiplier)
                
                wait_time = random.randint(min_wait, max_wait)
                
                # Sleep in 1-second intervals to allow for quick cancellation
                for _ in range(wait_time):
                    await asyncio.sleep(1)
                
                # Spawn duck in the chosen channel (if still joined)
                if channel in self.bot.channels_joined:
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
        
        # Don't spawn if there are already ducks present
        if self.ducks[channel]:
            return

        duck_type = self._choose_duck_type()

        # Special spawns that create multiple normal ducks.
        if duck_type in ('couple', 'family'):
            count = 2 if duck_type == 'couple' else random.randint(3, 4)
            for _ in range(count):
                duck = self._create_duck(channel, 'normal')
                self.ducks[channel].append(duck)
            self.logger.info(f"{duck_type} spawned {count} ducks in {channel}")
        else:
            duck = self._create_duck(channel, duck_type)
            self.ducks[channel].append(duck)
            hp = duck.get('max_hp', 1)
            if duck_type != 'normal':
                self.logger.info(f"{duck_type} duck (hidden) spawned in {channel} with {hp} HP")
            else:
                self.logger.info(f"Normal duck spawned in {channel}")

        # Notify players with Duck Radar
        try:
            for player_name, player_data in self.db.get_players_for_channel(channel).items():
                if self._has_active_effect(player_data, 'duck_radar'):
                    self.bot.send_message(player_name, self.bot.messages.get('radar_alert', channel=channel))
        except Exception:
            pass
        
        # All duck types use the same spawn message - type is hidden!
        message = self.bot.messages.get('duck_spawn')
        self.bot.send_message(channel, message)

    def _choose_duck_type(self):
        """Choose a duck type using duck_types.*.chance (with legacy fallbacks)."""
        try:
            duck_types = self.bot.get_config('duck_types', {}) or {}
            if not isinstance(duck_types, dict):
                duck_types = {}

            weighted = []
            total = 0.0

            for dtype, cfg in duck_types.items():
                if dtype == 'normal':
                    continue
                chance = None
                if isinstance(cfg, dict):
                    chance = cfg.get('chance')

                # Legacy fallbacks
                if chance is None and dtype == 'golden':
                    chance = self.bot.get_config('golden_duck_chance', None)
                if chance is None and dtype == 'fast':
                    chance = self.bot.get_config('fast_duck_chance', None)

                try:
                    chance = float(chance)
                except (TypeError, ValueError):
                    chance = 0.0

                if chance > 0:
                    weighted.append((dtype, chance))
                    total += chance

            if total <= 0:
                return 'normal'

            r = random.random()
            if r >= min(1.0, total):
                return 'normal'

            pick = random.random() * total
            cumulative = 0.0
            for dtype, weight in weighted:
                cumulative += weight
                if pick <= cumulative:
                    return dtype
            return weighted[-1][0]
        except Exception:
            return 'normal'

    def _create_duck(self, channel, duck_type):
        """Create a duck dict for a given type."""
        cfg = self.bot.get_config(f'duck_types.{duck_type}', {}) or {}
        if not isinstance(cfg, dict):
            cfg = {}

        # Legacy golden HP keys
        if duck_type == 'golden' and ('min_hp' not in cfg and 'max_hp' not in cfg):
            cfg = dict(cfg)
            cfg['min_hp'] = self.bot.get_config('golden_duck_min_hp', 3)
            cfg['max_hp'] = self.bot.get_config('golden_duck_max_hp', 5)

        min_hp = cfg.get('min_hp', cfg.get('hp', 1))
        max_hp = cfg.get('max_hp', cfg.get('hp', 1))
        try:
            min_hp = int(min_hp)
            max_hp = int(max_hp)
        except (TypeError, ValueError):
            min_hp = 1
            max_hp = 1
        min_hp = max(1, min_hp)
        max_hp = max(min_hp, max_hp)
        hp = random.randint(min_hp, max_hp)

        return {
            'id': f"{duck_type}_duck_{int(time.time())}_{random.randint(1000, 9999)}",
            'spawn_time': time.time(),
            'channel': channel,
            'duck_type': duck_type,
            'max_hp': hp,
            'current_hp': hp
        }
    
    def shoot_duck(self, nick, channel, player):
        """Handle shooting at a duck"""
        # Status effects
        if self._has_active_effect(player, 'eliminated'):
            return {
                'success': False,
                'message_key': 'player_eliminated',
                'message_args': {'nick': nick}
            }
        if self._has_active_effect(player, 'poisoned'):
            return {
                'success': False,
                'message_key': 'player_poisoned',
                'message_args': {'nick': nick}
            }

        # Check if gun is confiscated
        if player.get('gun_confiscated', False):
            return {
                'success': False,
                'message_key': 'bang_not_armed',
                'message_args': {'nick': nick}
            }
        
        # Check if clothes are wet
        if self._is_player_wet(player):
            return {
                'success': False,
                'message_key': 'bang_wet_clothes',
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
            # Wild shot - gun confiscated for unsafe shooting
            player['shots_fired'] = player.get('shots_fired', 0) + 1  # Track wild shots too
            player['shots_missed'] = player.get('shots_missed', 0) + 1  # Wild shots count as misses
            # Use ammo for the shot, then store remaining ammo before confiscation
            remaining_ammo = player.get('current_ammo', 1) - 1
            player['confiscated_ammo'] = remaining_ammo
            player['confiscated_magazines'] = player.get('magazines', 0)
            player['current_ammo'] = 0  # No ammo while confiscated
            player['gun_confiscated'] = True
            self.db.save_database()
            return {
                'success': False,
                'message_key': 'bang_no_duck',
                'message_args': {'nick': nick}
            }
        
        # Shoot at duck
        player['current_ammo'] = player.get('current_ammo', 1) - 1
        player['shots_fired'] = player.get('shots_fired', 0) + 1  # Track total shots fired
        # Calculate hit chance using level-modified accuracy
        modified_accuracy = self.bot.levels.get_modified_accuracy(player)
        hit_chance = modified_accuracy / 100.0
        if self._has_active_effect(player, 'perfect_aim'):
            hit_chance = 1.0
        if random.random() < hit_chance:
            # Hit! Get the duck and reveal its type
            duck = self.ducks[channel][0]
            duck_type = duck.get('duck_type', 'normal')

            # Multi-HP ducks: treat as "boss" style.
            if duck.get('max_hp', 1) > 1:
                duck['current_hp'] -= 1
                per_hit_xp = self._get_duck_xp_per_hit(duck_type)

                if duck['current_hp'] > 0:
                    accuracy_gain = self.bot.get_config('gameplay.accuracy_gain_on_hit', self.bot.get_config('accuracy_gain_on_hit', 1))
                    max_accuracy = self.bot.get_config('gameplay.max_accuracy', self.bot.get_config('max_accuracy', 100))
                    player['accuracy'] = min(player.get('accuracy', self.bot.get_config('player_defaults.accuracy', 75)) + accuracy_gain, max_accuracy)
                    self.db.save_database()

                    message_key = {
                        'golden': 'bang_hit_golden',
                        'concrete': 'bang_hit_concrete',
                        'holy_grail': 'bang_hit_holy_grail',
                        'diamond': 'bang_hit_diamond'
                    }.get(duck_type, 'bang_hit_golden')

                    return {
                        'success': True,
                        'hit': True,
                        'message_key': message_key,
                        'message_args': {
                            'nick': nick,
                            'hp_remaining': duck['current_hp'],
                            'xp_gained': per_hit_xp,
                            'ducks_shot': player.get('ducks_shot', 0)
                        }
                    }

                # Killed!
                self.ducks[channel].pop(0)
                xp_gained = per_hit_xp * int(duck.get('max_hp', 1))
                message_key = {
                    'golden': 'bang_hit_golden_killed',
                    'concrete': 'bang_hit_concrete_killed',
                    'holy_grail': 'bang_hit_holy_grail_killed',
                    'diamond': 'bang_hit_diamond_killed'
                }.get(duck_type, 'bang_hit_golden_killed')
            else:
                # Single-HP ducks
                self.ducks[channel].pop(0)
                xp_gained = self._get_duck_xp_per_hit(duck_type)
                message_key = {
                    'normal': 'bang_hit',
                    'fast': 'bang_hit_fast',
                    'explosive': 'bang_hit_explosive'
                }.get(duck_type, 'bang_hit')

                if duck_type == 'explosive':
                    self._add_temporary_effect(player, 'eliminated', 2 * 3600)
            
            # Apply XP and level changes
            old_level = self.bot.levels.calculate_player_level(player)
            player['xp'] = player.get('xp', 0) + xp_gained
            player['ducks_shot'] = player.get('ducks_shot', 0) + 1
            accuracy_gain = self.bot.get_config('gameplay.accuracy_gain_on_hit', self.bot.get_config('accuracy_gain_on_hit', 1))
            max_accuracy = self.bot.get_config('gameplay.max_accuracy', self.bot.get_config('max_accuracy', 100))
            player['accuracy'] = min(player.get('accuracy', self.bot.get_config('player_defaults.accuracy', 75)) + accuracy_gain, max_accuracy)
            
            # Check if player leveled up and update magazines if needed
            new_level = self.bot.levels.calculate_player_level(player)
            if new_level != old_level:
                self.bot.levels.update_player_magazines(player)
            
            # If config option enabled, rearm all disarmed players when duck is shot
            if self.bot.get_config('duck_spawning.rearm_on_duck_shot', False):
                self._rearm_all_disarmed_players(channel)
            
            # Check for item drops
            dropped_item = self._check_item_drop(player, duck_type)
            
            self.db.save_database()
            
            # Include drop info in the return
            result = {
                'success': True,
                'hit': True,
                'message_key': message_key,
                'message_args': {
                    'nick': nick,
                    'xp_gained': xp_gained,
                    'ducks_shot': player['ducks_shot']
                }
            }
            
            # Add drop info if an item was dropped
            if dropped_item:
                result['dropped_item'] = dropped_item
            
            return result
        else:
            # Miss! Duck stays in the channel
            player['shots_missed'] = player.get('shots_missed', 0) + 1  # Track missed shots
            
            # Lose 1 XP for missing
            player['xp'] = max(0, player.get('xp', 0) - 1)
            
            accuracy_loss = self.bot.get_config('gameplay.accuracy_loss_on_miss', 2)
            min_accuracy = self.bot.get_config('gameplay.min_accuracy', 10)
            player['accuracy'] = max(player.get('accuracy', self.bot.get_config('player_defaults.accuracy', 75)) - accuracy_loss, min_accuracy)
            
            # Check for friendly fire (chance to hit another hunter)
            friendly_fire_chance = 0.15  # 15% chance of hitting another hunter on miss
            if random.random() < friendly_fire_chance:
                # Get other armed players in the same channel
                armed_players = []
                for other_nick, other_player in self.db.get_players_for_channel(channel).items():
                    if (other_nick.lower() != nick.lower() and 
                        not other_player.get('gun_confiscated', False) and
                        other_player.get('current_ammo', 0) > 0):
                        armed_players.append((other_nick, other_player))
                
                if armed_players:
                    # Hit a random armed hunter
                    victim_nick, victim_player = random.choice(armed_players)
                    
                    # Check if shooter has insurance protection
                    has_insurance = self._check_insurance_protection(player, 'friendly_fire')
                    
                    if has_insurance:
                        # Protected by insurance - no penalties
                        self.db.save_database()
                        return {
                            'success': True,
                            'hit': False,
                            'friendly_fire': True,
                            'victim': victim_nick,
                            'message_key': 'bang_friendly_fire_insured',
                            'message_args': {
                                'nick': nick,
                                'victim': victim_nick
                            }
                        }
                    else:
                        # Apply friendly fire penalties - gun confiscated for unsafe shooting
                        xp_loss = min(player.get('xp', 0) // 4, 25)  # Lose 25% XP or max 25 XP
                        player['xp'] = max(0, player.get('xp', 0) - xp_loss)
                        # Store current ammo state before confiscation (no shot fired yet in friendly fire)
                        player['confiscated_ammo'] = player.get('current_ammo', 0)
                        player['confiscated_magazines'] = player.get('magazines', 0)
                        player['current_ammo'] = 0  # No ammo while confiscated
                        player['gun_confiscated'] = True
                        
                        self.db.save_database()
                        return {
                            'success': True,
                            'hit': False,
                            'friendly_fire': True,
                            'victim': victim_nick,
                            'message_key': 'bang_friendly_fire_penalty',
                            'message_args': {
                                'nick': nick,
                                'victim': victim_nick,
                                'xp_lost': xp_loss
                            }
                        }
            
            self.db.save_database()
            return {
                'success': True,
                'hit': False,
                'message_key': 'bang_miss',
                'message_args': {'nick': nick}
            }
    
    def befriend_duck(self, nick, channel, player):
        """Handle befriending a duck"""
        # Status effects
        if self._has_active_effect(player, 'eliminated'):
            return {
                'success': False,
                'message_key': 'player_eliminated',
                'message_args': {'nick': nick}
            }
        if self._has_active_effect(player, 'poisoned'):
            return {
                'success': False,
                'message_key': 'player_poisoned',
                'message_args': {'nick': nick}
            }

        # Check for duck
        if channel not in self.ducks or not self.ducks[channel]:
            return {
                'success': False,
                'message_key': 'bef_no_duck',
                'message_args': {'nick': nick}
            }
        
        # Check befriend success rate from config and level modifiers
        base_rate = self.bot.get_config('gameplay.befriend_success_rate', 75)
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

            duck_type = duck.get('duck_type', 'normal')

            # Poison effects
            if duck_type == 'poisonous':
                self._add_temporary_effect(player, 'poisoned', 2 * 3600)
                self.db.save_database()
                return {
                    'success': True,
                    'befriended': True,
                    'message_key': 'bef_poisoned',
                    'message_args': {
                        'nick': nick,
                        'duration_hours': 2
                    }
                }
            if duck_type == 'radioactive':
                self._add_temporary_effect(player, 'poisoned', 8 * 3600)
                self.db.save_database()
                return {
                    'success': True,
                    'befriended': True,
                    'message_key': 'bef_poisoned',
                    'message_args': {
                        'nick': nick,
                        'duration_hours': 8
                    }
                }
            
            # Lower XP gain than shooting
            xp_gained = self.bot.get_config('gameplay.befriend_xp', 5)
            old_level = self.bot.levels.calculate_player_level(player)
            player['xp'] = player.get('xp', 0) + xp_gained
            player['ducks_befriended'] = player.get('ducks_befriended', 0) + 1
            
            # Check if player leveled up and update magazines if needed
            new_level = self.bot.levels.calculate_player_level(player)
            if new_level != old_level:
                self.bot.levels.update_player_magazines(player)
            
            # If config option enabled, rearm all disarmed players when duck is befriended
            if self.bot.get_config('rearm_on_duck_shot', False):
                self._rearm_all_disarmed_players(channel)
            
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
    
    def _rearm_all_disarmed_players(self, channel):
        """Rearm all players who have been disarmed (gun confiscated) in a channel"""
        try:
            rearmed_count = 0
            for player_name, player_data in self.db.get_players_for_channel(channel).items():
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
    
    def _get_active_spawn_multiplier(self, channel):
        """Get the current spawn rate multiplier from active bread effects in a channel"""
        import time
        max_multiplier = 1.0
        current_time = time.time()
        
        try:
            for player_name, player_data in self.db.get_players_for_channel(channel).items():
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
    
    def _is_player_wet(self, player):
        """Check if player has wet clothes that prevent shooting"""
        import time
        current_time = time.time()
        
        effects = player.get('temporary_effects', [])
        for effect in effects:
            if (effect.get('type') == 'wet_clothes' and 
                effect.get('expires_at', 0) > current_time):
                return True
        return False
    
    def _check_insurance_protection(self, player, protection_type):
        """Check if player has active insurance protection"""
        import time
        current_time = time.time()
        
        try:
            effects = player.get('temporary_effects', [])
            for effect in effects:
                if (effect.get('type') == 'insurance' and 
                    effect.get('protection') == protection_type and
                    effect.get('expires_at', 0) > current_time):
                    return True
            return False
        except Exception as e:
            self.logger.error(f"Error checking insurance protection: {e}")
            return False
    
    def _clean_expired_effects(self):
        """Remove expired temporary effects from all players"""
        import time
        current_time = time.time()
        
        try:
            for _channel, player_name, player_data in self.db.iter_all_players():
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
    
    def _check_item_drop(self, player, duck_type):
        """
        Check if the duck drops an item and add it to player's inventory
        Returns the dropped item info or None
        """
        import random
        
        try:
            # Get drop chance for this duck type
            drop_chance = self.bot.get_config(f'duck_types.{duck_type}.drop_chance', 0.0)
            
            # Roll for drop
            if random.random() > drop_chance:
                return None  # No drop
            
            # Get drop table for this duck type (fallback to normal)
            drop_table_key = f'{duck_type}_duck_drops'
            drop_table = self.bot.get_config(f'item_drops.{drop_table_key}', [])
            if not drop_table:
                drop_table = self.bot.get_config('item_drops.normal_duck_drops', [])
            
            if not drop_table:
                return None
            
            # Weighted random selection
            total_weight = sum(item.get('weight', 1) for item in drop_table)
            if total_weight <= 0:
                return None
                
            random_weight = random.randint(1, total_weight)
            current_weight = 0
            
            for drop_item in drop_table:
                current_weight += drop_item.get('weight', 1)
                if random_weight <= current_weight:
                    item_id = drop_item.get('item_id')
                    if item_id:
                        # Add item to player's inventory
                        inventory = player.get('inventory', {})
                        item_key = str(item_id)
                        inventory[item_key] = inventory.get(item_key, 0) + 1
                        player['inventory'] = inventory
                        
                        # Get item info from shop
                        item_info = self.bot.shop.get_item(item_id)
                        item_name = item_info.get('name', f'Item {item_id}') if item_info else f'Item {item_id}'
                        
                        self.logger.info(f"Duck dropped {item_name} for player {player.get('nick', 'Unknown')}")
                        
                        return {
                            'item_id': item_id,
                            'item_name': item_name,
                            'duck_type': duck_type
                        }
                    break
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error in _check_item_drop: {e}")
            return None

    def _has_active_effect(self, player, effect_type):
        import time
        current_time = time.time()
        effects = player.get('temporary_effects', [])
        for effect in effects:
            if effect.get('type') == effect_type and effect.get('expires_at', 0) > current_time:
                return True
        return False

    def _add_temporary_effect(self, player, effect_type, duration_seconds):
        import time
        if 'temporary_effects' not in player:
            player['temporary_effects'] = []
        duration_seconds = int(duration_seconds)
        duration_seconds = max(1, min(duration_seconds, 7 * 24 * 3600))  # cap 7 days
        player['temporary_effects'].append({
            'type': effect_type,
            'expires_at': time.time() + duration_seconds
        })

    def _get_duck_xp_per_hit(self, duck_type):
        """Get XP value for a duck type (supports duck_types.*.xp and legacy keys)."""
        xp = self.bot.get_config(f'duck_types.{duck_type}.xp', None)
        if xp is None:
            # Legacy keys
            if duck_type == 'golden':
                xp = self.bot.get_config('golden_duck_xp', 15)
            elif duck_type == 'fast':
                xp = self.bot.get_config('fast_duck_xp', 12)
            else:
                xp = self.bot.get_config('normal_duck_xp', 10)
        try:
            return int(xp)
        except (TypeError, ValueError):
            return 10