"""
Main DuckHunt IRC Bot
"""

import asyncio
import ssl
import json
import random
import logging
import sys
import os
import time
import signal
from typing import Optional

from .logging_utils import setup_logger
from .utils import parse_message, InputValidator
from .db import DuckDB
from .game import DuckGame
from .sasl import SASLHandler


class DuckHuntBot:
    """Main IRC Bot for DuckHunt game"""
    
    def __init__(self, config):
        self.config = config
        self.logger = setup_logger("DuckHuntBot")
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.registered = False
        self.channels_joined = set()
        self.shutdown_requested = False
        
        self.db = DuckDB()
        self.db.set_config_getter(self.get_config)
        self.game = DuckGame(self, self.db)
        
        self.sasl_handler = SASLHandler(self, config)
        
        self.admins = [admin.lower() for admin in self.config.get('admins', ['colby'])]
        self.ignored_nicks = set()
        
        self.duck_spawn_times = {}
        self.channel_records = {}
        
        self.dropped_items = {}
        
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
        """Get configuration value using dot notation"""
        keys = path.split('.')
        value = self.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value
        
    async def connect(self):
        """Connect to IRC server"""
        try:
            ssl_context = None
            if self.config.get('ssl', False):
                ssl_context = ssl.create_default_context()
                
            self.reader, self.writer = await asyncio.open_connection(
                self.config['server'], 
                self.config['port'],
                ssl=ssl_context
            )
            
            self.logger.info(f"Connected to {self.config['server']}:{self.config['port']}")
            
            if self.config.get('password'):
                self.send_raw(f"PASS {self.config['password']}")
                
            await self.register_user()
            
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            raise
            
    async def register_user(self):
        """Register user with IRC server"""
        nick = self.config['nick']
        self.send_raw(f'NICK {nick}')
        self.send_raw(f'USER {nick} 0 * :DuckHunt Bot')
        
    def send_raw(self, msg):
        """Send raw IRC message"""
        if self.writer and not self.writer.is_closing():
            try:
                self.writer.write(f'{msg}\r\n'.encode())
            except Exception as e:
                self.logger.error(f"Error sending message: {e}")
                
    def send_message(self, target, msg):
        """Send message to target (channel or user)"""
        self.send_raw(f'PRIVMSG {target} :{msg}')
        
    def is_admin(self, user):
        """Check if user is admin by nick only"""
        if '!' not in user:
            return False
        nick = user.split('!')[0].lower()
        return nick in self.admins
        
    def get_random_player_for_friendly_fire(self, shooter_nick):
        """Get random player for friendly fire accident"""
        other_players = [nick for nick in self.db.players.keys() 
                        if nick.lower() != shooter_nick.lower()]
        if other_players:
            return random.choice(other_players)
        return None
        
    async def send_user_message(self, nick, channel, message, message_type='default'):
        """Send message to user respecting their output mode preferences"""
        player = self.db.get_player(f"{nick}!user@host")
        if not player:
            self.send_message(channel, f"{nick} > {message}")
            return
            
        force_public_types = self.get_config('message_output.force_public', {}) or {}
        if force_public_types.get(message_type, False):
            self.send_message(channel, f"{nick} > {message}")
            return
            
        output_mode = player.get('settings', {}).get('output_mode', 'PUBLIC')
        
        if output_mode == 'NOTICE':
            self.send_raw(f'NOTICE {nick} :{message}')
        elif output_mode == 'PRIVMSG':
            self.send_message(nick, message)
        else:
            self.send_message(channel, f"{nick} > {message}")
            
    async def auto_rearm_confiscated_guns(self, channel, shooter_nick):
        """Auto-rearm confiscated guns when someone shoots a duck"""
        if not self.get_config('weapons.auto_rearm_on_duck_shot', True):
            return
            
        rearmed_players = []
        for nick, player in self.db.players.items():
            if player.get('gun_confiscated', False):
                player['gun_confiscated'] = False
                player['ammo'] = player.get('max_ammo', 6)
                player['chargers'] = player.get('max_chargers', 2)
                rearmed_players.append(nick)
                
        if rearmed_players:
            self.logger.info(f"Auto-rearmed guns for: {', '.join(rearmed_players)}")
            self.send_message(channel, 
                f"{self.colors['green']}Guns have been returned to all hunters! "
                f"({len(rearmed_players)} players rearmed){self.colors['reset']}")
            self.db.save_database()
            
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            self.logger.info(f"Received signal {signum}, shutting down...")
            self.shutdown_requested = True
            for task in asyncio.all_tasks():
                if not task.done():
                    task.cancel()
            
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, signal_handler)
            
    async def handle_message(self, prefix, command, params, trailing):
        """Handle incoming IRC messages"""
        try:
            if command == '001':
                self.registered = True
                self.logger.info("Successfully registered with IRC server")
                
                for channel in self.config['channels']:
                    self.send_raw(f'JOIN {channel}')
                    
            elif command == 'JOIN':
                if params and prefix.split('!')[0] == self.config['nick']:
                    channel = params[0]
                    self.channels_joined.add(channel)
                    self.logger.info(f"Joined channel: {channel}")
                    
            elif command == 'PRIVMSG':
                if len(params) >= 1:
                    target = params[0]
                    message = trailing
                    
                    if message.startswith('!') or target == self.config['nick']:
                        await self.handle_command(prefix, target, message)
                        
            elif command == 'PING':
                self.send_raw(f'PONG :{trailing}')
                
            elif command == 'CAP':
                await self.sasl_handler.handle_cap_response(params, trailing)
            elif command == 'AUTHENTICATE':
                await self.sasl_handler.handle_authenticate_response(params)
            elif command in ['903', '904', '905', '906', '907']:
                await self.sasl_handler.handle_sasl_result(command, params, trailing)
                
        except Exception as e:
            self.logger.error(f"Error handling message: {e}")
            
    async def handle_command(self, user, channel, message):
        """Handle bot commands"""
        if not user:
            return
            
        try:
            nick = user.split('!')[0]
            nick_lower = nick.lower()
            
            if not InputValidator.validate_nickname(nick):
                return
                
            if nick_lower in self.ignored_nicks:
                return
                
            message = InputValidator.sanitize_message(message)
            if not message:
                return
                
            is_private = channel == self.config['nick']
            response_target = nick if is_private else channel
            
            if message.startswith('!'):
                cmd_parts = message[1:].split()
            else:
                cmd_parts = message.split()
                
            if not cmd_parts:
                return
                
            cmd = cmd_parts[0].lower()
            args = cmd_parts[1:] if len(cmd_parts) > 1 else []
            
            player = self.db.get_player(user)
            if not player:
                return
                
            await self.process_command(nick, response_target, cmd, args, player, user)
            
        except Exception as e:
            self.logger.error(f"Error in command handler: {e}")
            
    async def process_command(self, nick, target, cmd, args, player, user):
        """Process individual commands"""
        if cmd == 'bang':
            await self.handle_bang(nick, target, player)
        elif cmd == 'reload':
            await self.handle_reload(nick, target, player)
        elif cmd == 'bef' or cmd == 'befriend':
            await self.handle_befriend(nick, target, player)
        elif cmd == 'duckstats':
            await self.handle_duckstats(nick, target, player)
        elif cmd == 'shop':
            await self.handle_shop(nick, target, player)
        elif cmd == 'sell':
            if args:
                await self.handle_sell(nick, target, args[0], player)
            else:
                await self.send_user_message(nick, target, "Usage: !sell <item_number>")
        elif cmd == 'use':
            if args:
                target_nick = args[1] if len(args) > 1 else None
                await self.handle_use(nick, target, args[0], player, target_nick)
            else:
                await self.send_user_message(nick, target, "Usage: !use <item_number> [target_player]")
        elif cmd == 'duckhelp':
            await self.handle_duckhelp(nick, target)
        elif cmd == 'ignore':
            if args:
                await self.handle_ignore(nick, target, args[0])
            else:
                await self.send_user_message(nick, target, "Usage: !ignore <player>")
        elif cmd == 'delignore':
            if args:
                await self.handle_delignore(nick, target, args[0])
            else:
                await self.send_user_message(nick, target, "Usage: !delignore <player>")
        elif cmd == 'topduck':
            await self.handle_topduck(nick, target)
        elif cmd == 'snatch':
            await self.handle_snatch(nick, target, player)
        elif cmd == 'rearm' and self.is_admin(user):
            target_nick = args[0] if args else None
            await self.handle_rearm(nick, target, player, target_nick)
        elif cmd == 'disarm' and self.is_admin(user):
            target_nick = args[0] if args else None
            await self.handle_disarm(nick, target, target_nick)
        elif cmd == 'ducklaunch' and self.is_admin(user):
            await self.handle_ducklaunch(nick, target)
        elif cmd == 'reset' and self.is_admin(user):
            if len(args) >= 2 and args[1] == 'confirm':
                await self.handle_reset_confirm(nick, target, args[0])
            elif args:
                await self.handle_reset(nick, target, args[0])
            else:
                await self.send_user_message(nick, target, "Usage: !reset <player> [confirm]")
        else:
            pass
            
    async def handle_bang(self, nick, channel, player):
        """Handle !bang command - shoot at duck (eggdrop style)"""
        if player.get('gun_confiscated', False):
            await self.send_user_message(nick, channel, f"{nick} > Your gun has been confiscated! You cannot shoot.")
            return
            
        if player.get('jammed', False):
            message = f"{nick} > Gun jammed! Use !reload"
            await self.send_user_message(nick, channel, message)
            return
            
        if player['shots'] <= 0:
            message = f"{nick} > *click* You're out of ammo! | Ammo: {player['shots']}/{player['max_shots']} | Chargers: {player.get('chargers', 0)}/{player.get('max_chargers', 2)}"
            await self.send_user_message(nick, channel, message)
            return
        
        if channel not in self.game.ducks or not self.game.ducks[channel]:
            player['shots'] -= 1
            player['total_ammo_used'] = player.get('total_ammo_used', 0) + 1
            player['wild_shots'] = player.get('wild_shots', 0) + 1
            
            if self.game.gun_jams(player):
                player['jammed'] = True
                player['jammed_count'] = player.get('jammed_count', 0) + 1
                message = f"{nick} > *BANG* You shot at nothing! What were you aiming at? *click* Gun jammed! | 0 xp | {self.colors['red']}GUN CONFISCATED{self.colors['reset']}"
            else:
                message = f"{nick} > *BANG* You shot at nothing! What were you aiming at? | 0 xp | {self.colors['red']}GUN CONFISCATED{self.colors['reset']}"
            
            player['gun_confiscated'] = True
            player['confiscated_count'] = player.get('confiscated_count', 0) + 1
            
            self.send_message(channel, message)
            self.db.save_database()
            return
        
        duck = self.game.ducks[channel][0]
        player['shots'] -= 1
        player['total_ammo_used'] = player.get('total_ammo_used', 0) + 1
        player['shot_at'] = player.get('shot_at', 0) + 1
        
        if self.game.gun_jams(player):
            player['jammed'] = True
            player['jammed_count'] = player.get('jammed_count', 0) + 1
            message = f"{nick} > *BANG* *click* Gun jammed while shooting! | Ammo: {player['shots']}/{player['max_shots']}"
            self.send_message(channel, f"{self.colors['red']}{message}{self.colors['reset']}")
        else:
            hit_chance = min(0.7 + (player.get('accuracy', 0) * 0.001), 0.95)
            if random.random() < hit_chance:
                await self.handle_duck_hit(nick, channel, player, duck)
            else:
                await self.handle_duck_miss(nick, channel, player)
        
        self.db.save_database()
    
    async def handle_duck_hit(self, nick, channel, player, duck):
        """Handle successful duck hit (eggdrop style)"""
        self.game.ducks[channel].remove(duck)
        
        shot_time = time.time()
        reaction_time = shot_time - duck.get('spawn_time', shot_time)
        
        points_earned = duck['points']
        xp_earned = duck['xp']
        
        if reaction_time < 2.0:
            quick_bonus = int(points_earned * 0.5)
            points_earned += quick_bonus
            quick_shot_msg = f" [Quick shot bonus: +{quick_bonus}]"
        else:
            quick_shot_msg = ""
            
        xp_earned = int(xp_earned * (1 + player.get('xp_bonus', 0) * 0.001))
        
        player['ducks_shot'] += 1
        player['exp'] += xp_earned
        player['money'] += points_earned
        player['last_hunt'] = time.time()
        
        current_accuracy = player.get('accuracy', 65)
        player['accuracy'] = min(current_accuracy + 1, 95)
        
        if 'best_time' not in player or reaction_time < player['best_time']:
            player['best_time'] = reaction_time
            
        player['total_reflex_time'] = player.get('total_reflex_time', 0) + reaction_time
        player['reflex_shots'] = player.get('reflex_shots', 0) + 1
        
        await self.check_level_up(nick, channel, player)
        
        message = f"{nick} > *BANG*     you shot down the duck in {reaction_time:.2f} seconds.     \\_X<   *KWAK*   [+{xp_earned} xp] [TOTAL DUCKS: {player['ducks_shot']}]"
        self.send_message(channel, f"{self.colors['green']}{message}{self.colors['reset']}")
        
        if random.random() < 0.1:
            await self.drop_random_item(nick, channel)
    
    async def handle_duck_miss(self, nick, channel, player):
        """Handle duck miss (eggdrop style)"""
        current_accuracy = player.get('accuracy', 65)
        player['accuracy'] = max(current_accuracy - 2, 10)
        
        player['missed'] = player.get('missed', 0) + 1
        
        message = f"{nick} > *BANG* You missed the duck! | Ammo: {player['shots']}/{player['max_shots']} | Chargers: {player.get('chargers', 0)}/{player.get('max_chargers', 2)}"
        self.send_message(channel, f"{self.colors['red']}{message}{self.colors['reset']}")
        
        if channel in self.game.ducks and len(self.game.ducks[channel]) > 1:
            for other_duck in self.game.ducks[channel][:]:
                if random.random() < 0.2:
                    self.game.ducks[channel].remove(other_duck)
                    self.send_message(channel, f"-.,¸¸.-·°'`'°·-.,¸¸.-·°'`'°·   \\_o>   The other ducks fly away, scared by the noise!")
    
    async def handle_reload(self, nick, channel, player):
        """Handle reload command (eggdrop style) - reload ammo and clear jams"""
        current_time = time.time()
        
        if player.get('gun_confiscated', False):
            await self.send_user_message(nick, channel, f"{nick} > Your gun has been confiscated! You cannot reload.")
            return
        
        if player.get('jammed', False):
            player['jammed'] = False
            player['last_reload'] = current_time
            
            message = f"{nick} > *click click* You unjammed your gun! | Ammo: {player['shots']}/{player['max_shots']} | Chargers: {player.get('chargers', 0)}/{player.get('max_chargers', 2)}"
            self.send_message(channel, f"{self.colors['cyan']}{message}{self.colors['reset']}")
            self.db.save_database()
            return
            
        if player['shots'] >= player['max_shots']:
            message = f"{nick} > Gun is already fully loaded! | Ammo: {player['shots']}/{player['max_shots']} | Chargers: {player.get('chargers', 0)}/{player.get('max_chargers', 2)}"
            await self.send_user_message(nick, channel, message)
            return
        
        if player.get('chargers', 0) <= 0:
            message = f"{nick} > No chargers left to reload with! | Ammo: {player['shots']}/{player['max_shots']} | Chargers: 0/{player.get('max_chargers', 2)}"
            await self.send_user_message(nick, channel, message)
            return
        
        if current_time - player.get('last_reload', 0) < player['reload_time']:
            remaining = int(player['reload_time'] - (current_time - player.get('last_reload', 0)))
            message = f"{nick} > Reload cooldown: {remaining} seconds remaining | Ammo: {player['shots']}/{player['max_shots']} | Chargers: {player.get('chargers', 0)}/{player.get('max_chargers', 2)}"
            await self.send_user_message(nick, channel, message)
            return
        
        old_shots = player['shots']
        player['shots'] = player['max_shots']
        player['chargers'] = max(0, player.get('chargers', 2) - 1)
        player['last_reload'] = current_time
        shots_added = player['shots'] - old_shots
        
        message = f"{nick} > *click clack* Reloaded! +{shots_added} shots | Ammo: {player['shots']}/{player['max_shots']} | Chargers: {player['chargers']}/{player.get('max_chargers', 2)}"
        
        self.send_message(channel, f"{self.colors['cyan']}{message}{self.colors['reset']}")
        
        self.db.save_database()
    
    async def handle_befriend(self, nick, channel, player):
        """Handle !bef command - befriend a duck"""
        if channel not in self.game.ducks or not self.game.ducks[channel]:
            await self.send_user_message(nick, channel, "There are no ducks to befriend!")
            return
            
        duck = self.game.ducks[channel][0]
        
        befriend_chance = 0.5 + (player.get('charm_bonus', 0) * 0.001)
        
        if random.random() < befriend_chance:
            self.game.ducks[channel].remove(duck)
            
            xp_earned = duck['xp']
            friendship_bonus = duck['points'] // 2
            
            player['exp'] += xp_earned
            player['money'] += friendship_bonus
            player['ducks_befriended'] += 1
            
            await self.check_level_up(nick, channel, player)
            
            effects = [
                ("luck", 10, "You feel lucky!"),
                ("charm_bonus", 5, "The duck teaches you about friendship!"),
                ("accuracy_bonus", 3, "The duck gives you aiming tips!")
            ]
            
            if random.random() < 0.3:
                effect, amount, message = random.choice(effects)
                player[effect] = player.get(effect, 0) + amount
                bonus_msg = f" {message}"
            else:
                bonus_msg = ""
            
            message = (f"{nick} befriended a {duck['type']} duck! "
                      f"+{friendship_bonus} coins, +{xp_earned} XP.{bonus_msg}")
            self.send_message(channel, f"{self.colors['magenta']}{message}{self.colors['reset']}")
            
            if random.random() < 0.15:
                await self.award_random_item(nick, channel, player)
        else:
            miss_messages = [
                f"The {duck['type']} duck doesn't trust you yet!",
                f"The {duck['type']} duck flies away from you!",
                f"You need to be more patient with the {duck['type']} duck!",
                f"The {duck['type']} duck looks at you suspiciously!"
            ]
            
            message = f"{nick} {random.choice(miss_messages)}"
            self.send_message(channel, f"{self.colors['yellow']}{message}{self.colors['reset']}")
            
            player['charm_bonus'] = max(player.get('charm_bonus', 0) - 1, -50)
        
        self.db.save_database()
    
    async def handle_shop(self, nick, channel, player):
        """Handle shop command"""
        shop_items = [
            "=== DUCK HUNT SHOP ===",
            "1. Extra Shots (3) - $50",
            "2. Faster Reload - $100", 
            "3. Accuracy Charm - $75",
            "4. Lucky Charm - $125",
            "5. Friendship Bracelet - $80",
            "6. Duck Caller - $200",
            "7. Camouflage - $150",
            "8. Energy Drink - $60",
            "==================",
            f"Your money: ${player['money']}",
            "Use !use <item_id> to purchase/use items"
        ]
        for line in shop_items:
            await self.send_user_message(nick, channel, line)
    
    async def handle_sell(self, nick, channel, item_id, player):
        """Handle sell command"""
        try:
            item_id = int(item_id)
        except ValueError:
            await self.send_user_message(nick, channel, "Invalid item ID!")
            return
        
        if 'inventory' not in player:
            player['inventory'] = {}
        
        item_key = str(item_id)
        if item_key not in player['inventory'] or player['inventory'][item_key] <= 0:
            await self.send_user_message(nick, channel, "You don't have that item!")
            return
        
        shop_items = {
            1: {'name': 'Extra Shots', 'price': 50},
            2: {'name': 'Faster Reload', 'price': 100}, 
            3: {'name': 'Accuracy Charm', 'price': 75},
            4: {'name': 'Lucky Charm', 'price': 125},
            5: {'name': 'Friendship Bracelet', 'price': 80},
            6: {'name': 'Duck Caller', 'price': 200},
            7: {'name': 'Camouflage', 'price': 150},
            8: {'name': 'Energy Drink', 'price': 60}
        }
        item_info = shop_items.get(item_id)
        if not item_info:
            await self.send_user_message(nick, channel, "Invalid item!")
            return
        
        player['inventory'][item_key] -= 1
        if player['inventory'][item_key] <= 0:
            del player['inventory'][item_key]
        
        sell_price = item_info['price'] // 2
        player['money'] += sell_price
        
        message = f"Sold {item_info['name']} for ${sell_price}!"
        await self.send_user_message(nick, channel, message)
        
        self.db.save_database()
    
    async def handle_use(self, nick, channel, item_id, player, target_nick=None):
        """Handle use command"""
        try:
            item_id = int(item_id)
        except ValueError:
            await self.send_user_message(nick, channel, "Invalid item ID!")
            return
        
        if 'inventory' not in player:
            player['inventory'] = {}
        
        shop_items = {
            1: {'name': 'Extra Shots', 'price': 50, 'consumable': True},
            2: {'name': 'Faster Reload', 'price': 100, 'consumable': True}, 
            3: {'name': 'Accuracy Charm', 'price': 75, 'consumable': False},
            4: {'name': 'Lucky Charm', 'price': 125, 'consumable': False},
            5: {'name': 'Friendship Bracelet', 'price': 80, 'consumable': False},
            6: {'name': 'Duck Caller', 'price': 200, 'consumable': True},
            7: {'name': 'Camouflage', 'price': 150, 'consumable': True},
            8: {'name': 'Energy Drink', 'price': 60, 'consumable': True}
        }
        
        item_key = str(item_id)
        item_info = shop_items.get(item_id)
        
        if not item_info:
            await self.send_user_message(nick, channel, "Invalid item ID!")
            return
        
        if item_key in player['inventory'] and player['inventory'][item_key] > 0:
            await self.use_item_effect(player, item_id, nick, channel, target_nick)
            player['inventory'][item_key] -= 1
            if player['inventory'][item_key] <= 0:
                del player['inventory'][item_key]
        else:
            if player['money'] >= item_info['price']:
                if item_info.get('consumable', True):
                    player['money'] -= item_info['price']
                    await self.use_item_effect(player, item_id, nick, channel, target_nick)
                else:
                    player['money'] -= item_info['price']
                    player['inventory'][item_key] = player['inventory'].get(item_key, 0) + 1
                    await self.send_user_message(nick, channel, f"Purchased {item_info['name']}!")
            else:
                await self.send_user_message(nick, channel, 
                    f"Not enough money! Need ${item_info['price']}, you have ${player['money']}")
                return
        
        self.db.save_database()
    
    async def handle_topduck(self, nick, channel):
        """Handle topduck command - show leaderboard"""
        sorted_players = sorted(
            [(name, data) for name, data in self.db.players.items()],
            key=lambda x: x[1]['ducks_shot'],
            reverse=True
        )
        
        if not sorted_players:
            await self.send_user_message(nick, channel, "No players found!")
            return
        
        await self.send_user_message(nick, channel, "=== TOP DUCK HUNTERS ===")
        for i, (name, data) in enumerate(sorted_players[:5], 1):
            stats = f"{i}. {name}: {data['ducks_shot']} ducks (Level {data['level']})"
            await self.send_user_message(nick, channel, stats)
    
    async def handle_snatch(self, nick, channel, player):
        """Handle snatch command - grab dropped items competitively"""
        import time
        
        if channel not in self.dropped_items or not self.dropped_items[channel]:
            await self.send_user_message(nick, channel, f"{nick} > There are no items to snatch!")
            return
        
        item = self.dropped_items[channel].pop(0)
        
        current_time = time.time()
        if current_time - item['timestamp'] > 60:
            await self.send_user_message(nick, channel, f"{nick} > The item has disappeared!")
            self.dropped_items[channel] = [
                i for i in self.dropped_items[channel] 
                if current_time - i['timestamp'] <= 60
            ]
            return
        
        if 'inventory' not in player:
            player['inventory'] = {}
        
        item_key = item['item_id']
        player['inventory'][item_key] = player['inventory'].get(item_key, 0) + 1
        
        message = f"{nick} snatched a {item['item_name']}! ⚡"
        self.send_message(channel, f"{self.colors['cyan']}{message}{self.colors['reset']}")

    async def handle_rearm(self, nick, channel, player, target_nick=None):
        """Handle rearm command - restore confiscated guns"""
        if target_nick:
            target_player = self.db.get_player(target_nick.lower())
            if target_player:
                target_player['gun_confiscated'] = False
                target_player['shots'] = target_player['max_shots']
                target_player['chargers'] = target_player.get('max_chargers', 2)
                target_player['jammed'] = False
                target_player['last_reload'] = 0
                message = f"{nick} returned {target_nick}'s confiscated gun! | Ammo: {target_player['shots']}/{target_player['max_shots']} | Chargers: {target_player['chargers']}/{target_player.get('max_chargers', 2)}"
                self.send_message(channel, f"{self.colors['cyan']}{message}{self.colors['reset']}")
            else:
                await self.send_user_message(nick, channel, "Player not found!")
        else:
            if not player.get('gun_confiscated', False):
                await self.send_user_message(nick, channel, f"{nick} > Your gun is not confiscated!")
                return
                
            if self.is_admin(nick):
                player['gun_confiscated'] = False
                player['shots'] = player['max_shots']
                player['chargers'] = player.get('max_chargers', 2)
                player['jammed'] = False
                player['last_reload'] = 0
                message = f"{nick} > Gun returned by admin! | Ammo: {player['shots']}/{player['max_shots']} | Chargers: {player['chargers']}/{player.get('max_chargers', 2)}"
                self.send_message(channel, f"{self.colors['cyan']}{message}{self.colors['reset']}")
            else:
                await self.send_user_message(nick, channel, f"{nick} > Your gun has been confiscated! Wait for an admin or automatic return.")
        
        self.db.save_database()
    
    async def handle_disarm(self, nick, channel, target_nick):
        """Handle disarm command (admin only)"""
        target_player = self.db.get_player(target_nick.lower())
        if target_player:
            target_player['shots'] = 0
            message = f"Admin {nick} disarmed {target_nick}!"
            self.send_message(channel, f"{self.colors['red']}{message}{self.colors['reset']}")
            
            self.db.save_database()
        else:
            await self.send_user_message(nick, channel, "Player not found!")
    
    async def handle_ducklaunch(self, nick, channel):
        """Handle !ducklaunch admin command"""
        duck = await self.game.spawn_duck_now(channel)
        if duck:
            self.send_message(channel, 
                f"{self.colors['green']}Admin {nick} launched a duck!{self.colors['reset']}")
        else:
            await self.send_user_message(nick, channel, "Failed to spawn duck (channel may be full)!")
    
    async def handle_duckstats(self, nick, channel, player):
        """Handle duckstats command"""
        stats_msg = (
            f"{nick}'s duck hunting stats: "
            f"Level {player['level']} | "
            f"Ducks shot: {player['ducks_shot']} | "
            f"Befriended: {player['ducks_befriended']} | "
            f"Money: ${player['money']} | "
            f"XP: {player['exp']}/{self.get_xp_for_level(player['level'] + 1)}"
        )
        await self.send_user_message(nick, channel, stats_msg)
        
        if 'inventory' in player and player['inventory']:
            shop_items = {
                1: 'Extra Shots', 2: 'Faster Reload', 3: 'Accuracy Charm', 4: 'Lucky Charm',
                5: 'Friendship Bracelet', 6: 'Duck Caller', 7: 'Camouflage', 8: 'Energy Drink',
                9: 'Armor Vest', 10: 'Gunpowder', 11: 'Sight', 12: 'Silencer',
                13: 'Explosive Ammo', 14: 'Mirror', 15: 'Sunglasses', 16: 'Clothes',
                17: 'Grease', 18: 'Brush', 19: 'Sand', 20: 'Water',
                21: 'Sabotage Kit', 22: 'Life Insurance', 23: 'Decoy'
            }
            
            inventory_items = []
            for item_id, quantity in player['inventory'].items():
                item_name = shop_items.get(int(item_id), f"Item {item_id}")
                inventory_items.append(f"{item_name} x{quantity}")
            
            if inventory_items:
                inventory_msg = f"Inventory: {', '.join(inventory_items)}"
                await self.send_user_message(nick, channel, inventory_msg)
    
    async def handle_duckhelp(self, nick, channel):
        """Handle duckhelp command"""
        help_lines = [
            "=== DUCK HUNT COMMANDS ===",
            "!bang - Shoot at ducks",
            "!reload - Reload your gun", 
            "!bef - Befriend a duck",
            "!duckstats - View your statistics",
            "!shop - View the shop",
            "!inventory - View your items",
            "!use <id> - Use/buy shop items",
            "!sell <id> - Sell inventory items",
            "!topduck - View leaderboard",
            "!rearm - Quick reload (costs money)",
            "!ducklaunch - Spawn duck (admin)",
            "!disarm <nick> - Disarm player (admin)",
            "!reset <nick> - Reset player (admin)",
            "========================"
        ]
        for line in help_lines:
            await self.send_user_message(nick, channel, line)
    
    async def handle_ignore(self, nick, channel, target_nick):
        """Handle ignore command"""
        if 'ignored_users' not in self.db.players[nick.lower()]:
            self.db.players[nick.lower()]['ignored_users'] = []
        
        ignored_list = self.db.players[nick.lower()]['ignored_users']
        if target_nick.lower() not in ignored_list:
            ignored_list.append(target_nick.lower())
            await self.send_user_message(nick, channel, f"Now ignoring {target_nick}")
            self.db.save_database()
        else:
            await self.send_user_message(nick, channel, f"{target_nick} is already ignored")
    
    async def handle_delignore(self, nick, channel, target_nick):
        """Handle delignore command"""
        if 'ignored_users' not in self.db.players[nick.lower()]:
            await self.send_user_message(nick, channel, f"{target_nick} is not ignored")
            return
        
        ignored_list = self.db.players[nick.lower()]['ignored_users']
        if target_nick.lower() in ignored_list:
            ignored_list.remove(target_nick.lower())
            await self.send_user_message(nick, channel, f"No longer ignoring {target_nick}")
            self.db.save_database()
        else:
            await self.send_user_message(nick, channel, f"{target_nick} is not ignored")
            
    async def handle_reset(self, nick, channel, target_nick):
        """Handle !reset admin command (requires confirmation)"""
        await self.send_user_message(nick, channel, 
            f"⚠️ WARNING: This will completely reset {target_nick}'s progress! "
            f"Use `!reset {target_nick} confirm` to proceed.")
            
    async def handle_reset_confirm(self, nick, channel, target_nick):
        """Handle !reset confirm admin command"""
        if target_nick.lower() in self.db.players:
            del self.db.players[target_nick.lower()]
            self.send_message(channel, 
                f"{self.colors['red']}Admin {nick} has reset {target_nick}'s progress!{self.colors['reset']}")
            self.db.save_database()
        else:
            await self.send_user_message(nick, channel, "Player not found!")
    
    async def check_level_up(self, nick, channel, player):
        """Check if player leveled up"""
        current_level = player['level']
        new_level = self.calculate_level(player['exp'])
        
        if new_level > current_level:
            player['level'] = new_level
            
            player['max_shots'] = min(player['max_shots'] + 1, 10)
            player['reload_time'] = max(player['reload_time'] - 0.5, 2.0)
            
            message = (f"🎉 {nick} leveled up to level {new_level}! "
                      f"Max shots: {player['max_shots']}, "
                      f"Reload time: {player['reload_time']}s")
            self.send_message(channel, f"{self.colors['yellow']}{message}{self.colors['reset']}")
    
    def calculate_level(self, exp):
        """Calculate level from experience points"""
        import math
        return int(math.sqrt(exp / 100)) + 1
    
    def get_xp_for_level(self, level):
        """Get XP required for a specific level"""
        return (level - 1) ** 2 * 100
    
    async def drop_random_item(self, nick, channel):
        """Drop a random item to the ground for competitive snatching"""
        import time
        
        item_ids = [1, 2, 3, 4, 5, 6, 7, 8]
        item_id = random.choice(item_ids)
        item_key = str(item_id)
        
        item_names = {
            '1': 'Extra Shots', '2': 'Faster Reload', '3': 'Accuracy Charm',
            '4': 'Lucky Charm', '5': 'Friendship Bracelet', '6': 'Duck Caller',
            '7': 'Camouflage', '8': 'Energy Drink'
        }
        
        item_name = item_names.get(item_key, f'Item {item_id}')
        
        if channel not in self.dropped_items:
            self.dropped_items[channel] = []
        
        dropped_item = {
            'item_id': item_key,
            'item_name': item_name,
            'timestamp': time.time(),
            'dropper': nick
        }
        self.dropped_items[channel].append(dropped_item)
        
        message = f"🎁 A {item_name} has been dropped! Type !snatch to grab it!"
        self.send_message(channel, f"{self.colors['magenta']}{message}{self.colors['reset']}")

    async def award_random_item(self, nick, channel, player):
        """Award a random item to player"""
        if 'inventory' not in player:
            player['inventory'] = {}
        
        item_ids = [1, 2, 3, 4, 5, 6, 7, 8]
        item_id = random.choice(item_ids)
        item_key = str(item_id)
        
        player['inventory'][item_key] = player['inventory'].get(item_key, 0) + 1
        
        item_names = {
            '1': 'Extra Shots', '2': 'Faster Reload', '3': 'Accuracy Charm',
            '4': 'Lucky Charm', '5': 'Friendship Bracelet', '6': 'Duck Caller',
            '7': 'Camouflage', '8': 'Energy Drink'
        }
        
        item_name = item_names.get(item_key, f'Item {item_id}')
        message = f"🎁 {nick} found a {item_name}!"
        self.send_message(channel, f"{self.colors['magenta']}{message}{self.colors['reset']}")
    
    async def use_item_effect(self, player, item_id, nick, channel, target_nick=None):
        """Apply item effects"""
        effects = {
            1: "Extra Shots! +3 shots",
            2: "Faster Reload! -1s reload time",
            3: "Accuracy Charm! +5 accuracy",
            4: "Lucky Charm! +10 luck",
            5: "Friendship Bracelet! +5 charm",
            6: "Duck Caller! Next duck spawns faster",
            7: "Camouflage! Ducks can't see you for 60s",
            8: "Energy Drink! +50 energy"
        }
        
        if item_id == 1:
            player['shots'] = min(player['shots'] + 3, player['max_shots'])
        elif item_id == 2:
            player['reload_time'] = max(player['reload_time'] - 1, 1)
        elif item_id == 3:
            player['accuracy_bonus'] = player.get('accuracy_bonus', 0) + 5
        elif item_id == 4:
            player['luck'] = player.get('luck', 0) + 10
        elif item_id == 5:
            player['charm_bonus'] = player.get('charm_bonus', 0) + 5
        elif item_id == 6:
            pass
        elif item_id == 7:
            player['camouflaged_until'] = time.time() + 60
        elif item_id == 8:
            player['energy'] = player.get('energy', 100) + 50
        
        effect_msg = effects.get(item_id, "Unknown effect")
        await self.send_user_message(nick, channel, f"Used item: {effect_msg}")
    
    async def cleanup_expired_items(self):
        """Background task to clean up expired dropped items"""
        import time
        
        while not self.shutdown_requested:
            try:
                current_time = time.time()
                
                for channel in list(self.dropped_items.keys()):
                    if channel in self.dropped_items:
                        original_count = len(self.dropped_items[channel])
                        
                        self.dropped_items[channel] = [
                            item for item in self.dropped_items[channel]
                            if current_time - item['timestamp'] <= 60
                        ]
                        
                        removed_count = original_count - len(self.dropped_items[channel])
                        if removed_count > 0:
                            self.logger.debug(f"Cleaned up {removed_count} expired items from {channel}")
                
                await asyncio.sleep(30)
                
            except Exception as e:
                self.logger.error(f"Error in cleanup_expired_items: {e}")
                await asyncio.sleep(30)
        
    async def run(self):
        """Main bot run loop"""
        tasks = []
        try:
            self.setup_signal_handlers()
            self.db.load_database()
            await self.connect()
            
            tasks = [
                asyncio.create_task(self.message_loop()),
                asyncio.create_task(self.game.spawn_ducks()),
                asyncio.create_task(self.game.duck_timeout_checker()),
                asyncio.create_task(self.cleanup_expired_items()),
            ]
            
            try:
                while not self.shutdown_requested:
                    for task in tasks:
                        if task.done() and task.exception():
                            self.logger.error(f"Task failed: {task.exception()}")
                            self.shutdown_requested = True
                            break
                    
                    await asyncio.sleep(0.1)
                    
            except asyncio.CancelledError:
                self.logger.info("Main loop cancelled")
            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt received")
                self.shutdown_requested = True
                
        except Exception as e:
            self.logger.error(f"Bot error: {e}")
            raise
        finally:
            self.logger.info("Shutting down bot...")
            
            for task in tasks:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        self.logger.error(f"Error cancelling task: {e}")
            
            try:
                self.db.save_database()
                self.logger.info("Database saved")
            except Exception as e:
                self.logger.error(f"Error saving database: {e}")
            
            if self.writer and not self.writer.is_closing():
                try:
                    self.send_raw("QUIT :Bot shutting down")
                    self.writer.close()
                    await self.writer.wait_closed()
                    self.logger.info("IRC connection closed")
                except Exception as e:
                    self.logger.error(f"Error closing connection: {e}")
            
            self.logger.info("Bot shutdown complete")
            
    async def message_loop(self):
        """Handle incoming IRC messages"""
        while not self.shutdown_requested and self.reader:
            try:
                line = await asyncio.wait_for(self.reader.readline(), timeout=1.0)
                if not line:
                    self.logger.warning("Empty line received, connection may be closed")
                    break
                    
                line = line.decode().strip()
                if line:
                    prefix, command, params, trailing = parse_message(line)
                    await self.handle_message(prefix, command, params, trailing)
                    
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                self.logger.info("Message loop cancelled")
                break
            except Exception as e:
                self.logger.error(f"Message loop error: {e}")
                break
                
        self.logger.info("Message loop ended")