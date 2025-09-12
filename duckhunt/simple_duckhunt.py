#!/usr/bin/env python3
"""
Standalone DuckHunt IRC Bot with JSON Database Storage
"""

import asyncio
import ssl
import json
import random
import logging
import sys
import os
import base64
import subprocess
import time
import uuid
import signal
from functools import partial
from typing import Optional

# Import SASL handler
from src.sasl import SASLHandler

# Simple colored logger
class ColorFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': '\033[94m',
        'INFO': '\033[92m',
        'WARNING': '\033[93m',
        'ERROR': '\033[91m',
        'CRITICAL': '\033[95m',
        'ENDC': '\033[0m',
    }
    def format(self, record):
        color = self.COLORS.get(record.levelname, '')
        endc = self.COLORS['ENDC']
        msg = super().format(record)
        return f"{color}{msg}{endc}"

def setup_logger():
    logger = logging.getLogger('DuckHuntBot')
    
    # Clear any existing handlers
    logger.handlers.clear()
    
    # Console handler with colors
    console_handler = logging.StreamHandler(sys.stdout)
    color_formatter = ColorFormatter('[%(asctime)s] %(levelname)s: %(message)s')
    console_handler.setFormatter(color_formatter)
    logger.addHandler(console_handler)
    
    # File handler without colors
    file_handler = logging.FileHandler('duckhunt.log', mode='a', encoding='utf-8')
    file_formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger

# Simple IRC message parser
def parse_message(line):
    prefix = ''
    trailing = ''
    if line.startswith(':'):
        prefix, line = line[1:].split(' ', 1)
    if ' :' in line:
        line, trailing = line.split(' :', 1)
    parts = line.split()
    command = parts[0] if parts else ''
    params = parts[1:] if len(parts) > 1 else []
    return prefix, command, params, trailing

class SimpleIRCBot:
    def __init__(self, config):
        self.config = config
        self.logger = setup_logger()
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.registered = False
        self.channels_joined = set()
        self.players = {}  # Memory cache for speed
        self.ducks = {}  # Format: {channel: [{'alive': True, 'spawn_time': time, 'id': uuid}, ...]}
        self.db_file = "duckhunt.json"
        self.admins = [admin.lower() for admin in self.config.get('admins', ['colby'])]  # Load from config only, case insensitive
        self.ignored_nicks = set()  # Nicks to ignore commands from
        self.command_cooldowns = {}  # Rate limiting for commands
        self.duck_timeout_min = self.config.get('duck_timeout_min', 45)  # Minimum duck timeout
        self.duck_timeout_max = self.config.get('duck_timeout_max', 75)  # Maximum duck timeout
        self.duck_spawn_min = self.config.get('duck_spawn_min', 1800)  # Minimum duck spawn time (30 min)
        self.duck_spawn_max = self.config.get('duck_spawn_max', 5400)  # Maximum duck spawn time (90 min)
        self.shutdown_requested = False  # Graceful shutdown flag
        self.running_tasks = set()  # Track running tasks for cleanup
        
        # Initialize SASL handler
        self.sasl_handler = SASLHandler(self, config)
        
        # IRC Color codes
        self.colors = {
            'red': '\x0304',
            'green': '\x0303',
            'blue': '\x0302',
            'yellow': '\x0308',
            'orange': '\x0307',
            'purple': '\x0306',
            'cyan': '\x0311',
            'white': '\x0300',
            'black': '\x0301',
            'gray': '\x0314',
            'reset': '\x03'
        }
        
        # 40-level progression system with titles
        self.levels = [
            {'xp': 0, 'title': 'Duck Harasser'},
            {'xp': 10, 'title': 'Unemployed'},
            {'xp': 25, 'title': 'Hunter Apprentice'},
            {'xp': 45, 'title': 'Duck Tracker'},
            {'xp': 70, 'title': 'Sharp Shooter'},
            {'xp': 100, 'title': 'Hunter'},
            {'xp': 135, 'title': 'Experienced Hunter'},
            {'xp': 175, 'title': 'Skilled Hunter'},
            {'xp': 220, 'title': 'Expert Hunter'},
            {'xp': 270, 'title': 'Master Hunter'},
            {'xp': 325, 'title': 'Duck Slayer'},
            {'xp': 385, 'title': 'Duck Terminator'},
            {'xp': 450, 'title': 'Duck Destroyer'},
            {'xp': 520, 'title': 'Duck Exterminator'},
            {'xp': 595, 'title': 'Duck Assassin'},
            {'xp': 675, 'title': 'Legendary Hunter'},
            {'xp': 760, 'title': 'Elite Hunter'},
            {'xp': 850, 'title': 'Supreme Hunter'},
            {'xp': 945, 'title': 'Ultimate Hunter'},
            {'xp': 1045, 'title': 'Godlike Hunter'},
            {'xp': 1150, 'title': 'Duck Nightmare'},
            {'xp': 1260, 'title': 'Duck Executioner'},
            {'xp': 1375, 'title': 'Duck Eliminator'},
            {'xp': 1495, 'title': 'Duck Obliterator'},
            {'xp': 1620, 'title': 'Duck Annihilator'},
            {'xp': 1750, 'title': 'Duck Devastator'},
            {'xp': 1885, 'title': 'Duck Vanquisher'},
            {'xp': 2025, 'title': 'Duck Conqueror'},
            {'xp': 2170, 'title': 'Duck Dominator'},
            {'xp': 2320, 'title': 'Duck Emperor'},
            {'xp': 2475, 'title': 'Duck Overlord'},
            {'xp': 2635, 'title': 'Duck Deity'},
            {'xp': 2800, 'title': 'Duck God'},
            {'xp': 2970, 'title': 'Duck Nemesis'},
            {'xp': 3145, 'title': 'Duck Apocalypse'},
            {'xp': 3325, 'title': 'Duck Armageddon'},
            {'xp': 3510, 'title': 'Duck Ragnarok'},
            {'xp': 3700, 'title': 'Duck Cataclysm'},
            {'xp': 3895, 'title': 'Duck Holocaust'},
            {'xp': 4095, 'title': 'Duck Genesis'}
        ]
        
        # Sleep hours configuration (when ducks don't spawn)
        self.sleep_hours = self.config.get('sleep_hours', [])  # Format: [[22, 30], [8, 0]] for 22:30 to 08:00
        
        # Duck planning system
        self.daily_duck_plan = {}  # Format: {channel: [(hour, minute), ...]}
        
        # Karma system
        self.karma_events = ['teamkill', 'miss', 'wild_shot', 'hit', 'golden_hit']
        
        self.load_database()
    
    def get_config(self, path, default=None):
        """Get nested configuration value with fallback to default"""
        keys = path.split('.')
        value = self.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value
    
    async def attempt_nickserv_auth(self):
        """Delegate to SASL handler for NickServ auth"""
        # For simple bot, we'll implement NickServ auth here
        sasl_config = self.config.get('sasl', {})
        username = sasl_config.get('username', '')
        password = sasl_config.get('password', '')
        
        if username and password:
            self.logger.info(f"Attempting NickServ identification for {username}")
            # Try both common NickServ commands
            self.send_raw(f'PRIVMSG NickServ :IDENTIFY {username} {password}')
            # Some networks use just the password if nick matches
            await asyncio.sleep(1)
            self.send_raw(f'PRIVMSG NickServ :IDENTIFY {password}')
            self.logger.info("NickServ identification commands sent")
        else:
            self.logger.debug("No SASL credentials available for NickServ fallback")
    
    async def handle_nickserv_response(self, message):
        """Handle responses from NickServ"""
        message_lower = message.lower()
        
        if any(phrase in message_lower for phrase in [
            'you are now identified', 'password accepted', 'you are already identified',
            'authentication successful', 'you have been identified'
        ]):
            self.logger.info("NickServ identification successful!")
            
        elif any(phrase in message_lower for phrase in [
            'invalid password', 'incorrect password', 'access denied',
            'authentication failed', 'not registered', 'nickname is not registered'
        ]):
            self.logger.error(f"NickServ identification failed: {message}")
            
        else:
            self.logger.debug(f"NickServ message: {message}")
        
    def get_player_level(self, xp):
        """Get player level and title based on XP"""
        for i in range(len(self.levels) - 1, -1, -1):
            if xp >= self.levels[i]['xp']:
                return i + 1, self.levels[i]['title']
        return 1, self.levels[0]['title']
    
    def get_xp_for_next_level(self, xp):
        """Get XP needed for next level"""
        level, _ = self.get_player_level(xp)
        if level < len(self.levels):
            return self.levels[level]['xp'] - xp
        return 0  # Max level reached
    
    def calculate_penalty_by_level(self, base_penalty, xp):
        """Calculate penalty based on player level"""
        level, _ = self.get_player_level(xp)
        # Higher levels get higher penalties
        return base_penalty + (level - 1) * 0.5
    
    def update_karma(self, player, event):
        """Update player karma based on event"""
        if 'karma' not in player:
            player['karma'] = 0
        
        karma_changes = {
            'hit': 2,
            'golden_hit': 5,
            'teamkill': -10,
            'wild_shot': -3,
            'miss': -1
        }
        
        player['karma'] += karma_changes.get(event, 0)
    
    def is_sleep_time(self):
        """Check if current time is within sleep hours"""
        if not self.sleep_hours:
            return False
            
        import datetime
        now = datetime.datetime.now()
        current_time = now.hour * 60 + now.minute
        
        for sleep_start, sleep_end in self.sleep_hours:
            start_minutes = sleep_start[0] * 60 + sleep_start[1]
            end_minutes = sleep_end[0] * 60 + sleep_end[1]
            
            if start_minutes <= end_minutes:  # Same day
                if start_minutes <= current_time <= end_minutes:
                    return True
            else:  # Crosses midnight
                if current_time >= start_minutes or current_time <= end_minutes:
                    return True
        return False
    
    def calculate_gun_reliability(self, player):
        """Calculate gun reliability percentage"""
        base_reliability = player.get('reliability', 70)
        grease_bonus = 10 if player.get('grease', 0) > 0 else 0
        brush_bonus = 5 if player.get('brush', 0) > 0 else 0
        return min(base_reliability + grease_bonus + brush_bonus, 95)
    
    def gun_jams(self, player):
        """Check if gun jams when firing"""
        reliability = self.calculate_gun_reliability(player)
        return random.randint(1, 100) > reliability
        
    async def scare_other_ducks(self, channel, shot_duck_id):
        """Successful shots can scare other ducks away"""
        if not self.config.get('successful_shots_scare_ducks', True):
            return
            
        channel_ducks = self.ducks.get(channel, [])
        for duck in channel_ducks:
            if duck.get('alive') and duck['id'] != shot_duck_id:
                # 30% chance to scare each remaining duck
                if random.randint(1, 100) <= 30:
                    duck['scared'] = True
                    duck['alive'] = False
                    
    async def scare_duck_on_miss(self, channel, target_duck):
        """Duck may be scared by missed shots"""
        if target_duck.get('hit_attempts', 0) >= 2:  # Duck gets scared after 2+ attempts
            if random.randint(1, 100) <= 40:  # 40% chance to scare
                target_duck['scared'] = True
                target_duck['alive'] = False
                self.send_message(channel, f"The duck got scared and flew away! (\\_o<) *flap flap*")
                
    async def find_bushes_items(self, nick, channel, player):
        """Find items in bushes after killing a duck"""
        if random.randint(1, 100) <= 12:  # 12% chance to find something
            found_items = [
                "Handful of sand", "Water bucket", "Four-leaf clover", "Mirror", 
                "Grease", "Brush for gun", "Spare clothes", "Sunglasses",
                "Piece of bread", "Life insurance"
            ]
            found_item = random.choice(found_items)
            
            # Add item to player inventory
            item_key = found_item.lower().replace(' ', '_').replace("'", "")
            if 'four_leaf_clover' in item_key:
                item_key = 'luck'
                player['luck'] = player.get('luck', 0) + 1
            elif item_key in player:
                player[item_key] = player.get(item_key, 0) + 1
                
            self.send_message(channel, f"{nick} > {self.colors['cyan']}You found {found_item} in the bushes!{self.colors['reset']}")
            self.save_player(f"{nick}!user@host")  # Save player data
            
    def load_database(self):
        """Load player data from JSON file"""
        if os.path.exists(self.db_file):
            try:
                with open(self.db_file, 'r') as f:
                    data = json.load(f)
                    self.players = data.get('players', {})
                self.logger.info(f"Loaded {len(self.players)} players from {self.db_file}")
            except (json.JSONDecodeError, IOError) as e:
                self.logger.error(f"Error loading database: {e}")
                self.players = {}
        else:
            self.players = {}
            self.logger.info(f"Created new database: {self.db_file}")
            
    def save_database(self):
        """Save all player data to JSON file with error handling"""
        try:
            # Atomic write to prevent corruption
            temp_file = f"{self.db_file}.tmp"
            data = {
                'players': self.players,
                'last_save': str(time.time())
            }
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            # Atomic rename to replace old file
            import os
            os.replace(temp_file, self.db_file)
            
        except IOError as e:
            self.logger.error(f"Error saving database: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected database save error: {e}")
            
    def is_admin(self, user):
        """Check if user is admin by nick only"""
        if '!' not in user:
            return False
        nick = user.split('!')[0].lower()
        return nick in self.admins
    
    async def send_user_message(self, nick, channel, message):
        """Send message to user respecting their notice/private message preferences"""
        player = self.get_player(f"{nick}!*@*")
        
        # Default to channel notices if player not found or no settings
        use_notices = True
        if player and 'settings' in player:
            use_notices = player['settings'].get('notices', True)
        
        if use_notices:
            # Send to channel
            self.send_message(channel, message)
        else:
            # Send as private message
            private_msg = message.replace(f"{nick} > ", "")  # Remove nick prefix for PM
            self.send_message(nick, private_msg)
    
    def get_random_player_for_friendly_fire(self, shooter_nick):
        """Get a random player (except shooter) for friendly fire"""
        eligible_players = []
        shooter_lower = shooter_nick.lower()
        
        for nick in self.players.keys():
            if nick != shooter_lower:  # Don't hit yourself
                eligible_players.append(nick)
        
        if eligible_players:
            return random.choice(eligible_players)
        return None
        
    async def connect(self):
        server = self.config['server']
        port = self.config['port']
        ssl_context = ssl.create_default_context() if self.config.get('ssl', True) else None
        
        self.logger.info(f"Connecting to {server}:{port} (SSL: {ssl_context is not None})")
        
        self.reader, self.writer = await asyncio.open_connection(
            server, port, ssl=ssl_context
        )
        self.logger.info("Connected successfully!")
        
        # Start SASL negotiation if enabled
        if await self.sasl_handler.start_negotiation():
            return True
        else:
            # Standard registration without SASL
            await self.register_user()
            return True
        
    async def register_user(self):
        """Register the user with the IRC server"""
        self.logger.info(f"Registering as {self.config['nick']}")
        self.send_raw(f'NICK {self.config["nick"]}')
        self.send_raw(f'USER {self.config["nick"]} 0 * :DuckHunt Bot')
        
        # Send password if configured (for servers that require it)
        if self.config.get('password'):
            self.send_raw(f'PASS {self.config["password"]}')
            
    def send_raw(self, msg):
        # Skip debug logging for speed
        # self.logger.debug(f"-> {msg}")
        if self.writer:
            self.writer.write((msg + '\r\n').encode())
        
    def send_message(self, target, msg):
        # Skip logging during gameplay for speed (uncomment for debugging)
        # self.logger.info(f"Sending to {target}: {msg}")
        self.send_raw(f'PRIVMSG {target} :{msg}')
        # Remove drain() for faster responses - let TCP handle buffering
        
    def get_player(self, user):
        """Get player data by nickname only (case insensitive)"""
        if '!' not in user:
            return None
            
        nick = user.split('!')[0].lower()  # Case insensitive
        
        # Use nick as database key
        if nick in self.players:
            player = self.players[nick]
            # Backward compatibility: ensure all required fields exist
            if 'missed' not in player:
                player['missed'] = 0
            if 'inventory' not in player:
                player['inventory'] = {}
            return player
            
        # Create new player with configurable defaults
        player_data = {
            'xp': 0,
            'caught': 0,
            'befriended': 0,  # Separate counter for befriended ducks
            'missed': 0,
            'ammo': self.get_config('weapons.starting_ammo', 6),
            'max_ammo': self.get_config('weapons.max_ammo_base', 6),
            'chargers': self.get_config('weapons.starting_chargers', 2),
            'max_chargers': self.get_config('weapons.max_chargers_base', 2),
            'accuracy': self.get_config('shooting.base_accuracy', 65),
            'reliability': self.get_config('shooting.base_reliability', 70),
            'weapon': self.get_config('weapons.starting_weapon', 'pistol'),
            'gun_confiscated': False,
            'explosive_ammo': False,
            'settings': {
                'notices': True,  # True for notices, False for private messages
                'private_messages': False
            },
            # Inventory system
            'inventory': {},
            # New advanced stats
            'golden_ducks': 0,
            'karma': 0,
            'deflection': 0,
            'defense': 0,
            'jammed': False,
            'jammed_count': 0,
            'deaths': 0,
            'neutralized': 0,
            'deflected': 0,
            'best_time': 999.9,
            'total_reflex_time': 0.0,
            'reflex_shots': 0,
            'wild_shots': 0,
            'accidents': 0,
            'total_ammo_used': 0,
            'shot_at': 0,
            'lucky_shots': 0,
            # Shop items
            'luck': 0,
            'detector': 0,
            'silencer': 0,
            'sunglasses': 0,
            'clothes': 0,
            'grease': 0,
            'brush': 0,
            'mirror': 0,
            'sand': 0,
            'water': 0,
            'sabotage': 0,
            'life_insurance': 0,
            'liability': 0,
            'decoy': 0,
            'bread': 0,
            'duck_detector': 0,
            'mechanical': 0
        }
        
        self.players[nick] = player_data
        self.save_database()  # Auto-save new players
        return player_data
        
    def save_player(self, user):
        """Save player data - batch saves for performance"""
        if not hasattr(self, '_save_pending'):
            self._save_pending = False
        
        if not self._save_pending:
            self._save_pending = True
            # Schedule delayed save to batch multiple writes
            asyncio.create_task(self._delayed_save())
    
    async def _delayed_save(self):
        """Batch save to reduce disk I/O"""
        await asyncio.sleep(0.5)  # Small delay to batch saves
        self.save_database()
        self._save_pending = False
        
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum):
            signal_name = signal.Signals(signum).name
            self.logger.info(f"Received {signal_name}, initiating graceful shutdown...")
            self.shutdown_requested = True
            
        # Handle common shutdown signals
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, lambda s, f: signal_handler(s))
        if hasattr(signal, 'SIGINT'):
            signal.signal(signal.SIGINT, lambda s, f: signal_handler(s))
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, lambda s, f: signal_handler(s))

    def is_rate_limited(self, user, command, cooldown=2.0):
        """Check if user is rate limited for a command"""
        now = time.time()
        key = f"{user}:{command}"
        
        if key in self.command_cooldowns:
            if now - self.command_cooldowns[key] < cooldown:
                return True
        
        self.command_cooldowns[key] = now
        return False

    async def handle_command(self, user, channel, message):
        if not user:
            return
                
        nick = user.split('!')[0]
        nick_lower = nick.lower()
        
        # Check if user is ignored
        if nick_lower in self.ignored_nicks:
            return
        
        # Determine if this is a private message to the bot
        is_private = channel == self.config['nick']
        
        # For private messages, use the nick as the target for responses
        response_target = nick if is_private else channel
        
        # Handle private messages (no ! prefix needed)
        if is_private:
            cmd = message.strip().lower()
            
            # Private message admin commands
            if self.is_admin(user):
                if cmd == 'restart':
                    await self.handle_restart(nick, response_target)
                    return
                elif cmd == 'quit':
                    await self.handle_quit(nick, response_target)
                    return
                elif cmd == 'launch' or cmd == 'ducklaunch':
                    # For private messages, launch in all channels
                    for chan in self.channels_joined:
                        await self.spawn_duck_now(chan)
                    self.send_message(response_target, f"{nick} > Launched ducks in all channels!")
                    return
                elif cmd == 'golden' or cmd == 'goldenduck':
                    # Launch golden ducks
                    for chan in self.channels_joined:
                        await self.spawn_duck_now(chan, force_golden=True)
                    self.send_message(response_target, f"{nick} > Launched {self.colors['yellow']}GOLDEN DUCKS{self.colors['reset']} in all channels!")
                    return
                elif cmd.startswith('ignore '):
                    target_nick = cmd[7:].strip().lower()
                    await self.handle_ignore(nick, response_target, target_nick)
                    return
                elif cmd.startswith('delignore '):
                    target_nick = cmd[10:].strip().lower()
                    await self.handle_delignore(nick, response_target, target_nick)
                    return
                else:
                    # Unknown private command
                    self.send_message(response_target, f"{nick} > Admin commands: restart, quit, launch, golden, ignore <nick>, delignore <nick>")
                    return
            else:
                # Non-admin private message
                self.send_message(response_target, f"{nick} > Private commands are admin-only. Use !help in a channel for game commands.")
                return
        
        # Handle channel messages (must start with !)
        if not message.startswith('!'):
            return
            
        # Extract just the command part (first word) to handle emojis and extra text
        cmd = message.strip().lower().split()[0]
        # Keep the original message for commands that need arguments
        full_cmd = message.strip().lower()
        
        # Regular game commands (channel only)
        # Inline common commands for speed
        if cmd == '!bang':
            # Rate limit shooting to prevent spam
            if self.is_rate_limited(user, 'bang', 1.0):
                return
                
            player = self.get_player(user)
            if not player:
                return
                
            # Check if gun is confiscated
            if player.get('gun_confiscated', False):
                self.send_message(channel, f"{nick} > {self.colors['red']}Your gun has been confiscated! Buy a new gun from the shop (item #5).{self.colors['reset']}")
                return
                
            # Check if gun is jammed
            if player.get('jammed', False):
                self.send_message(channel, f"{nick} > {self.colors['red']}Your gun is jammed! Use !reload to unjam it.{self.colors['reset']}")
                return
                
            # Check ammo
            if player['ammo'] <= 0:
                self.send_message(channel, f"{nick} > Your gun is empty! | Ammo: 0/{player['max_ammo']} | Chargers: {player['chargers']}/{player['max_chargers']}")
                return
                
            # Check for gun jamming before shooting
            if self.gun_jams(player):
                player['jammed'] = True
                player['jammed_count'] = player.get('jammed_count', 0) + 1
                jam_sound = "•click• •click•" if player.get('silencer', 0) > 0 else "*CLICK* *CLICK*"
                self.send_message(channel, f"{nick} > {jam_sound} Your gun jammed! Use !reload to unjam it.")
                self.save_player(user)
                return
                
            # Get ducks in this channel
            channel_ducks = self.ducks.get(channel, [])
            alive_ducks = [duck for duck in channel_ducks if duck.get('alive')]
            
            # Consume ammo
            player['ammo'] -= 1
            player['total_ammo_used'] = player.get('total_ammo_used', 0) + 1
            
            if alive_ducks:
                # Target the oldest duck (first in, first out)
                target_duck = alive_ducks[0]
                shot_time = time.time() - target_duck['spawn_time']
                is_golden = target_duck.get('type') == 'golden'
                
                # Calculate hit chance (golden ducks are harder to hit)
                base_accuracy = player['accuracy']
                if is_golden:
                    base_accuracy = max(base_accuracy - 30, 10)  # Golden ducks much harder
                
                # Apply bonuses
                if player.get('sunglasses', 0) > 0:
                    base_accuracy += 5  # Sunglasses help
                if player.get('mirror', 0) > 0:
                    base_accuracy += 3  # Mirror helps
                    
                hit_chance = min(base_accuracy, 95)  # Cap at 95%
                
                # Record shot attempt
                player['shot_at'] = player.get('shot_at', 0) + 1
                target_duck['hit_attempts'] = target_duck.get('hit_attempts', 0) + 1
                
                # Check for hit
                if random.randint(1, 100) <= hit_chance:
                    # HIT!
                    player['caught'] += 1
                    target_duck['alive'] = False
                    
                    # Update reflex time stats
                    player['reflex_shots'] = player.get('reflex_shots', 0) + 1
                    player['total_reflex_time'] = player.get('total_reflex_time', 0) + shot_time
                    if shot_time < player.get('best_time', 999.9):
                        player['best_time'] = shot_time
                    
                    # Calculate XP and rewards
                    if is_golden:
                        player['golden_ducks'] = player.get('golden_ducks', 0) + 1
                        base_xp = 50  # Golden ducks give much more XP
                        self.update_karma(player, 'golden_hit')
                    else:
                        base_xp = 15  # Normal XP
                        self.update_karma(player, 'hit')
                    
                    # Lucky shot bonus
                    luck_multiplier = 1 + (player.get('luck', 0) * 0.1)  # 10% per luck point
                    is_lucky = random.randint(1, 100) <= (5 + player.get('luck', 0))
                    if is_lucky:
                        player['lucky_shots'] = player.get('lucky_shots', 0) + 1
                        luck_multiplier *= 1.5  # 50% bonus for lucky shot
                        
                    xp_earned = int(base_xp * luck_multiplier)
                    player['xp'] += xp_earned
                    
                    # Sound effects based on ammo type
                    if player.get('explosive_ammo', False):
                        shot_sound = "•BOUM•" if player.get('silencer', 0) > 0 else "*BOUM*"
                        explosive_text = f" {self.colors['orange']}[explosive ammo]{self.colors['reset']}"
                    else:
                        shot_sound = "•bang•" if player.get('silencer', 0) > 0 else "*BANG*"
                        explosive_text = ""
                    
                    # Lucky shot text
                    lucky_text = f" {self.colors['yellow']}[lucky shot!]{self.colors['reset']}" if is_lucky else ""
                    
                    # Build hit message
                    level, title = self.get_player_level(player['xp'])
                    
                    if is_golden:
                        golden_count = player.get('golden_ducks', 0)
                        hit_msg = f"{nick} > {self.colors['yellow']}{shot_sound}{self.colors['reset']} You shot down the {self.colors['yellow']}★ GOLDEN DUCK ★{self.colors['reset']} in {shot_time:.3f}s! Total: {player['caught']} ducks ({self.colors['yellow']}{golden_count} golden{self.colors['reset']}) | Level {level}: {title} | [{self.colors['yellow']}{xp_earned} xp{self.colors['reset']}]{explosive_text}{lucky_text}"
                    else:
                        hit_msg = f"{nick} > {self.colors['green']}{shot_sound}{self.colors['reset']} You shot down the duck in {shot_time:.3f}s! Total: {player['caught']} ducks | Level {level}: {title} | [{self.colors['green']}{xp_earned} xp{self.colors['reset']}]{explosive_text}{lucky_text}"
                    
                    self.send_message(channel, hit_msg)
                    
                    # Scare other ducks if enabled (successful shots can scare ducks)
                    await self.scare_other_ducks(channel, target_duck['id'])
                    
                    # Find items in bushes (rare chance)
                    await self.find_bushes_items(nick, channel, player)
                    
                else:
                    # MISS!
                    player['missed'] += 1
                    self.update_karma(player, 'miss')
                    
                    # Calculate miss penalty based on level
                    miss_penalty = int(self.calculate_penalty_by_level(-2, player['xp']))
                    player['xp'] += miss_penalty
                    
                    # Bullet ricochet chance (can hit other players)
                    ricochet_chance = 8  # 8% base chance
                    if player.get('explosive_ammo', False):
                        ricochet_chance = 15  # Higher with explosive
                        
                    ricochet_msg = ""
                    if random.randint(1, 100) <= ricochet_chance:
                        ricochet_target = self.get_random_player_for_friendly_fire(nick)
                        if ricochet_target:
                            target_player = self.players[ricochet_target]
                            ricochet_dmg = -3
                            target_player['xp'] += ricochet_dmg
                            target_player['shot_at'] = target_player.get('shot_at', 0) + 1
                            ricochet_msg = f" {self.colors['red']}[RICOCHET: {ricochet_target} hit for {ricochet_dmg} xp]{self.colors['reset']}"
                    
                    # Scare duck on miss
                    await self.scare_duck_on_miss(channel, target_duck)
                    
                    miss_sound = "•click•" if player.get('silencer', 0) > 0 else "*CLICK*"
                    await self.send_user_message(nick, channel, f"{nick} > {miss_sound} You missed the duck! [miss: {miss_penalty} xp]{ricochet_msg}")
                    
            else:
                # No duck present - wild fire!
                player['wild_shots'] = player.get('wild_shots', 0) + 1
                self.update_karma(player, 'wild_shot')
                
                # Calculate penalties based on level
                miss_penalty = int(self.calculate_penalty_by_level(-2, player['xp']))
                wild_penalty = int(self.calculate_penalty_by_level(-3, player['xp']))
                player['xp'] += miss_penalty + wild_penalty
                
                # Confiscate gun
                player['gun_confiscated'] = True
                
                # Higher chance of hitting other players when no duck
                friendly_fire_chance = 25  # 25% when no duck
                friendly_fire_msg = ""
                
                if random.randint(1, 100) <= friendly_fire_chance:
                    ff_target = self.get_random_player_for_friendly_fire(nick)
                    if ff_target:
                        target_player = self.players[ff_target]
                        ff_dmg = int(self.calculate_penalty_by_level(-4, target_player['xp']))
                        target_player['xp'] += ff_dmg
                        target_player['shot_at'] = target_player.get('shot_at', 0) + 1
                        player['accidents'] = player.get('accidents', 0) + 1
                        self.update_karma(player, 'teamkill')
                        friendly_fire_msg = f" {self.colors['red']}[ACCIDENT: {ff_target} injured for {ff_dmg} xp]{self.colors['reset']}"
                
                wild_sound = "•BOUM•" if player.get('explosive_ammo', False) else "*BANG*"
                if player.get('silencer', 0) > 0:
                    wild_sound = "•" + wild_sound[1:-1] + "•"
                    
                confiscated_msg = f" {self.colors['red']}[GUN CONFISCATED]{self.colors['reset']}"
                await self.send_user_message(nick, channel, f"{nick} > {wild_sound} You shot at nothing! What were you aiming at? [miss: {miss_penalty} xp] [wild fire: {wild_penalty} xp]{confiscated_msg}{friendly_fire_msg}")
            
            # Save after each shot
            self.save_player(user)
                
        elif cmd == '!bef':
            # Check if befriending is enabled
            if not self.get_config('befriending.enabled', True):
                self.send_message(channel, f"{nick} > Duck befriending is currently disabled!")
                return
                
            player = self.get_player(user)
            if not player:
                return
                
            # Get ducks in this channel
            channel_ducks = self.ducks.get(channel, [])
            alive_ducks = [duck for duck in channel_ducks if duck.get('alive')]
            
            if alive_ducks:
                # Target the oldest duck (first in, first out)
                target_duck = alive_ducks[0]
                bef_time = time.time() - target_duck['spawn_time']
                
                # Calculate befriend success chance using config values
                level, _ = self.get_player_level(player['xp'])
                base_success = self.get_config('befriending.base_success_rate', 65) or 65
                max_success = self.get_config('befriending.max_success_rate', 90) or 90
                level_bonus_per_level = self.get_config('befriending.level_bonus_per_level', 2) or 2
                level_bonus_cap = self.get_config('befriending.level_bonus_cap', 20) or 20
                luck_bonus_per_point = self.get_config('befriending.luck_bonus_per_point', 3) or 3
                
                level_bonus = min(level * level_bonus_per_level, level_bonus_cap)
                luck_bonus = player.get('luck', 0) * luck_bonus_per_point
                success_chance = min(base_success + level_bonus + luck_bonus, max_success)
                
                # Check if befriend attempt succeeds
                if random.randint(1, 100) <= success_chance:
                    # Successful befriend
                    player['befriended'] = player.get('befriended', 0) + 1
                    
                    # XP rewards from config
                    xp_min = self.get_config('befriending.xp_reward_min', 1) or 1
                    xp_max = self.get_config('befriending.xp_reward_max', 3) or 3
                    
                    xp_earned = random.randint(xp_min, xp_max)
                    player['xp'] += xp_earned
                    
                    # Mark duck as befriended (dead)
                    target_duck['alive'] = False
                    
                    # Lucky items with configurable chance
                    if self.get_config('items.lucky_items_enabled', True):
                        lucky_items = ["four-leaf clover", "rabbit's foot", "horseshoe", "lucky penny", "magic feather"]
                        base_luck_chance = self.get_config('befriending.lucky_item_chance', 5) + player.get('luck', 0)
                        lucky_item = random.choice(lucky_items) if random.randint(1, 100) <= base_luck_chance else None
                        lucky_text = f" [{lucky_item}]" if lucky_item else ""
                    else:
                        lucky_text = ""
                    
                    remaining_ducks = len([d for d in channel_ducks if d.get('alive')])
                    duck_count_text = f" | {remaining_ducks} ducks remain" if remaining_ducks > 0 else ""
                    
                    self.send_message(channel, f"{nick} > You befriended a duck in {bef_time:.3f}s! Total friends: {player['befriended']} ducks on {channel}. \\_o< *quack* [+{xp_earned} xp]{lucky_text}{duck_count_text}")
                    
                    # Update karma for successful befriend
                    if self.get_config('karma.enabled', True):
                        karma_bonus = self.get_config('karma.befriend_success_bonus', 2)
                        player['karma'] = player.get('karma', 0) + karma_bonus
                    
                    # Save to database after befriending
                    self.save_player(user)
                else:
                    # Duck refuses to be befriended
                    refusal_messages = [
                        f"{nick} > The duck looks at you suspiciously and waddles away! \\_o< *suspicious quack*",
                        f"{nick} > The duck refuses to be friends and flaps away angrily! \\_O< *angry quack*", 
                        f"{nick} > The duck ignores your friendship attempts and goes back to swimming! \\_o< *indifferent quack*",
                        f"{nick} > The duck seems shy and hides behind some reeds! \\_o< *shy quack*",
                        f"{nick} > The duck is too busy looking for food to be your friend! \\_o< *hungry quack*",
                        f"{nick} > The duck gives you a cold stare and swims to the other side! \\_O< *cold quack*",
                        f"{nick} > The duck prefers to stay wild and free! \\_o< *wild quack*",
                        f"{nick} > The duck thinks you're trying too hard and keeps its distance! \\_o< *skeptical quack*"
                    ]
                    
                    # Small chance the duck gets scared and flies away (configurable)
                    scared_chance = self.get_config('befriending.scared_away_chance', 10) or 10
                    if random.randint(1, 100) <= scared_chance:
                        target_duck['alive'] = False
                        scared_messages = [
                            f"{nick} > Your friendship attempt scared the duck away! It flies off into the sunset! \\_o< *departing quack*",
                            f"{nick} > The duck panics at your approach and escapes! \\_O< *panicked quack* *flap flap*"
                        ]
                        self.send_message(channel, random.choice(scared_messages))
                    else:
                        self.send_message(channel, random.choice(refusal_messages))
                    
                    # XP penalty for failed befriend attempt (configurable)
                    xp_penalty = self.get_config('befriending.failure_xp_penalty', 1)
                    player['xp'] = max(0, player['xp'] - xp_penalty)
                    
                    # Update karma for failed befriend
                    if self.get_config('karma.enabled', True):
                        karma_penalty = self.get_config('karma.befriend_fail_penalty', 1)
                        player['karma'] = player.get('karma', 0) - karma_penalty
                    
                    # Save player data
                    self.save_player(user)
            else:
                self.send_message(channel, f"{nick} > There is no duck to befriend!")
                
        elif cmd == '!reload':
            player = self.get_player(user)
            if not player:
                return
                
            # Check if gun is jammed (reload unjams it)
            if player.get('jammed', False):
                player['jammed'] = False
                unjam_sound = "•click click•" if player.get('silencer', 0) > 0 else "*click click*"
                self.send_message(channel, f"{nick} > {unjam_sound} You unjammed your gun! | Ammo: {player['ammo']}/{player['max_ammo']} | Chargers: {player['chargers']}/{player['max_chargers']}")
                self.save_player(user)
                return
                
            if player['ammo'] == player['max_ammo']:
                self.send_message(channel, f"{nick} > Your gun doesn't need to be reloaded. | Ammo: {player['ammo']}/{player['max_ammo']} | Chargers: {player['chargers']}/{player['max_chargers']}")
                return
                
            if player['chargers'] <= 0:
                self.send_message(channel, f"{nick} > You don't have any chargers left! | Ammo: {player['ammo']}/{player['max_ammo']} | Chargers: 0/{player['max_chargers']}")
                return
                
            # Calculate reload reliability
            reload_reliability = self.calculate_gun_reliability(player)
            
            if random.randint(1, 100) <= reload_reliability:
                player['chargers'] -= 1
                player['ammo'] = player['max_ammo']
                reload_sound = "•click•" if player.get('silencer', 0) > 0 else "*click*"
                self.send_message(channel, f"{nick} > {reload_sound} You reloaded your gun! | Ammo: {player['ammo']}/{player['max_ammo']} | Chargers: {player['chargers']}/{player['max_chargers']}")
            else:
                # Gun jams during reload
                player['jammed'] = True
                player['jammed_count'] = player.get('jammed_count', 0) + 1
                jam_sound = "•CLACK• •click click•" if player.get('silencer', 0) > 0 else "*CLACK* *click click*"
                self.send_message(channel, f"{nick} > {jam_sound} Your gun jammed while reloading! Use !reload again to unjam it.")
                
            # Save to database after reload
            self.save_player(user)
                
        elif cmd == '!stats':
            await self.handle_stats(nick, channel, user)
        elif cmd == '!help':
            await self.handle_help(nick, channel)
        elif full_cmd.startswith('!shop'):
            # Handle !shop or !shop <item_id>
            parts = full_cmd.split()
            if len(parts) == 1:
                # Just !shop - show shop listing
                await self.handle_shop(nick, channel, user)
            elif len(parts) >= 2:
                # !shop <item_id> - purchase item
                item_id = parts[1]
                await self.handle_buy(nick, channel, item_id, user)
        elif full_cmd.startswith('!use '):
            parts = full_cmd[5:].split()
            if len(parts) >= 1:
                item_id = parts[0]
                target_nick = parts[1] if len(parts) >= 2 else None
                await self.handle_use(nick, channel, item_id, user, target_nick)
            else:
                self.send_message(channel, f"{nick} > Usage: !use <item_id> [target_nick]")
        elif full_cmd.startswith('!sell '):
            item_id = full_cmd[6:].strip()
            await self.handle_sell(nick, channel, item_id, user)
        elif full_cmd.startswith('!trade '):
            parts = full_cmd[7:].split()
            if len(parts) >= 3:
                target_nick, item, amount = parts[0], parts[1], parts[2]
                await self.handle_trade(nick, channel, user, target_nick, item, amount)
            else:
                self.send_message(channel, f"{nick} > Usage: !trade <nick> <coins|ammo|chargers> <amount>")
        elif full_cmd.startswith('!rearm ') and self.is_admin(user):  # Admin only
            # Allow rearming other players or self
            target_nick = full_cmd[7:].strip()
            await self.handle_rearm(nick, channel, user, target_nick)
        elif cmd == '!rearm' and self.is_admin(user):  # Admin only
            # Rearm self
            await self.handle_rearm(nick, channel, user, nick)
        elif cmd == '!duck' and self.is_admin(user):  # Admin only
            await self.spawn_duck_now(channel)
        elif cmd == '!golden' and self.is_admin(user):  # Admin only
            await self.spawn_duck_now(channel, force_golden=True)
        elif cmd == '!listplayers' and self.is_admin(user):  # Admin only
            await self.handle_listplayers(nick, channel)
        elif full_cmd.startswith('!ban ') and self.is_admin(user):  # Admin only
            target_nick = full_cmd[5:].strip()
            await self.handle_ban(nick, channel, target_nick)
        elif full_cmd.startswith('!reset ') and self.is_admin(user):  # Admin only
            target_nick = full_cmd[7:].strip()
            await self.handle_reset(nick, channel, target_nick)
        elif cmd == '!resetdb' and self.is_admin(user):  # Admin only
            await self.handle_reset_database(nick, channel, user)
        elif full_cmd.startswith('!resetdb confirm ') and self.is_admin(user):  # Admin only
            confirmation = full_cmd[17:].strip()
            await self.handle_reset_database_confirm(nick, channel, user, confirmation)
        elif cmd == '!restart' and self.is_admin(user):  # Admin only
            await self.handle_restart(nick, channel)
        elif cmd == '!quit' and self.is_admin(user):  # Admin only
            await self.handle_quit(nick, channel)
        elif cmd == '!ducklaunch' and self.is_admin(user):  # Admin only
            await self.spawn_duck_now(channel)
        elif cmd == '!ducks':
            # Show duck count for all users
            channel_ducks = self.ducks.get(channel, [])
            alive_ducks = [duck for duck in channel_ducks if duck.get('alive')]
            dead_ducks = [duck for duck in channel_ducks if not duck.get('alive')]
            
            if alive_ducks:
                duck_list = []
                for duck in alive_ducks:
                    duck_type = duck.get('type', 'normal')
                    spawn_time = time.time() - duck['spawn_time']
                    if duck_type == 'golden':
                        duck_list.append(f"{self.colors['yellow']}Golden Duck{self.colors['reset']} ({spawn_time:.1f}s)")
                    else:
                        duck_list.append(f"Duck ({spawn_time:.1f}s)")
                self.send_message(channel, f"{nick} > Active ducks: {', '.join(duck_list)}")
            else:
                self.send_message(channel, f"{nick} > No ducks currently active.")
                
        elif cmd == '!top' or cmd == '!leaderboard':
            # Show top players by XP
            if not self.players:
                self.send_message(channel, f"{nick} > No players found!")
                return
                
            # Sort players by XP
            sorted_players = sorted(self.players.items(), key=lambda x: x[1]['xp'], reverse=True)
            top_5 = sorted_players[:5]
            
            self.send_message(channel, f"{self.colors['cyan']}🏆 TOP HUNTERS LEADERBOARD 🏆{self.colors['reset']}")
            for i, (player_nick, player_data) in enumerate(top_5, 1):
                level, title = self.get_player_level(player_data['xp'])
                total_ducks = player_data.get('caught', 0) + player_data.get('befriended', 0)
                golden = player_data.get('golden_ducks', 0)
                golden_text = f" ({self.colors['yellow']}{golden} golden{self.colors['reset']})" if golden > 0 else ""
                
                if i == 1:
                    rank_color = self.colors['yellow']  # Gold
                elif i == 2:
                    rank_color = self.colors['gray']    # Silver
                elif i == 3:
                    rank_color = self.colors['orange']  # Bronze
                else:
                    rank_color = self.colors['white']
                    
                self.send_message(channel, f"{rank_color}#{i}{self.colors['reset']} {player_nick} - Level {level}: {title} | XP: {player_data['xp']} | Ducks: {total_ducks}{golden_text}")
                
        elif cmd == '!levels':
            # Show level progression table
            self.send_message(channel, f"{self.colors['cyan']}🎯 LEVEL PROGRESSION SYSTEM 🎯{self.colors['reset']}")
            
            # Show first 10 levels as example
            for i in range(min(10, len(self.levels))):
                level_data = self.levels[i]
                next_xp = self.levels[i + 1]['xp'] if i + 1 < len(self.levels) else "MAX"
                self.send_message(channel, f"Level {i + 1}: {level_data['title']} (XP: {level_data['xp']} - {next_xp})")
                
            if len(self.levels) > 10:
                self.send_message(channel, f"... and {len(self.levels) - 10} more levels up to Level {len(self.levels)}: {self.levels[-1]['title']}")
                
        elif full_cmd.startswith('!level '):
            # Show specific player's level info
            target_nick = full_cmd[7:].strip().lower()
            if target_nick in self.players:
                target_player = self.players[target_nick]
                level, title = self.get_player_level(target_player['xp'])
                xp_for_next = self.get_xp_for_next_level(target_player['xp'])
                
                if xp_for_next > 0:
                    next_info = f"Next level in {xp_for_next} XP"
                else:
                    next_info = "MAX LEVEL REACHED!"
                    
                self.send_message(channel, f"{nick} > {target_nick}: Level {level} - {self.colors['cyan']}{title}{self.colors['reset']} | {next_info}")
            else:
                self.send_message(channel, f"{nick} > Player {target_nick} not found!")
                
        elif cmd == '!karma':
            # Show karma leaderboard
            if not self.players:
                self.send_message(channel, f"{nick} > No players found!")
                return
                
            # Sort by karma
            karma_players = [(nick, data) for nick, data in self.players.items() if data.get('karma', 0) != 0]
            karma_players.sort(key=lambda x: x[1].get('karma', 0), reverse=True)
            
            if not karma_players:
                self.send_message(channel, f"{nick} > No karma data available!")
                return
                
            self.send_message(channel, f"{self.colors['purple']}☯ KARMA LEADERBOARD ☯{self.colors['reset']}")
            for i, (player_nick, player_data) in enumerate(karma_players[:5], 1):
                karma = player_data.get('karma', 0)
                karma_color = self.colors['green'] if karma >= 0 else self.colors['red']
                karma_text = "Saint" if karma >= 50 else "Good" if karma >= 10 else "Evil" if karma <= -10 else "Chaotic" if karma <= -50 else "Neutral"
                
                self.send_message(channel, f"#{i} {player_nick} - {karma_color}Karma: {karma}{self.colors['reset']} ({karma_text})")
                
        elif cmd == '!ducks':
            # Show duck count for all users
            channel_ducks = self.ducks.get(channel, [])
            alive_ducks = [duck for duck in channel_ducks if duck.get('alive')]
            dead_ducks = [duck for duck in channel_ducks if not duck.get('alive')]
            
            if alive_ducks:
                oldest_time = min(time.time() - duck['spawn_time'] for duck in alive_ducks)
                self.send_message(channel, f"{nick} > {len(alive_ducks)} ducks in {channel} | Oldest: {oldest_time:.1f}s | Dead: {len(dead_ducks)} | Timeout: {self.duck_timeout_min}-{self.duck_timeout_max}s")
            else:
                self.send_message(channel, f"{nick} > No ducks in {channel} | Dead: {len(dead_ducks)}")
        elif cmd == '!output' or full_cmd.startswith('!output '):
            parts = cmd.split(maxsplit=1)
            output_type = parts[1] if len(parts) > 1 else ''
            await self.handle_output(nick, channel, user, output_type)
        elif full_cmd.startswith('!ignore ') and self.is_admin(user):  # Admin only
            target_nick = full_cmd[8:].strip().lower()
            await self.handle_ignore(nick, channel, target_nick)
        elif full_cmd.startswith('!delignore ') and self.is_admin(user):  # Admin only
            target_nick = full_cmd[11:].strip().lower()
            await self.handle_delignore(nick, channel, target_nick)
        elif full_cmd.startswith('!giveitem ') and self.is_admin(user):  # Admin only
            parts = full_cmd[10:].split()
            if len(parts) >= 2:
                target_nick, item = parts[0], parts[1]
                await self.handle_admin_giveitem(nick, channel, target_nick, item)
            else:
                self.send_message(channel, f"{nick} > Usage: !giveitem <nick> <item_id>")
        elif full_cmd.startswith('!givexp ') and self.is_admin(user):  # Admin only
            parts = full_cmd[8:].split()
            if len(parts) >= 2:
                target_nick, amount = parts[0], parts[1]
                await self.handle_admin_givexp(nick, channel, target_nick, amount)
            else:
                self.send_message(channel, f"{nick} > Usage: !givexp <nick> <amount>")
                
    async def handle_stats(self, nick, channel, user):
        player = self.get_player(user)
        if not player:
            self.send_message(channel, f"{nick} > Player data not found!")
            return
        
        # Get level and title
        level, title = self.get_player_level(player['xp'])
        xp_for_next = self.get_xp_for_next_level(player['xp'])
        
        # Calculate advanced stats
        total_shots = player.get('caught', 0) + player.get('missed', 0)
        effective_accuracy = (player.get('caught', 0) / total_shots * 100) if total_shots > 0 else 0
        average_time = (player.get('total_reflex_time', 0) / player.get('reflex_shots', 1)) if player.get('reflex_shots', 0) > 0 else 0
        
        # Gun status
        gun_status = ""
        if player.get('gun_confiscated', False):
            gun_status += f" {self.colors['red']}[CONFISCATED]{self.colors['reset']}"
        if player.get('jammed', False):
            gun_status += f" {self.colors['yellow']}[JAMMED]{self.colors['reset']}"
        if player.get('explosive_ammo', False):
            gun_status += f" {self.colors['orange']}[EXPLOSIVE]{self.colors['reset']}"
        
        # Duck stats with colors
        duck_stats = []
        if player.get('caught', 0) > 0:
            duck_stats.append(f"Shot:{player['caught']}")
        if player.get('befriended', 0) > 0:
            duck_stats.append(f"Befriended:{player['befriended']}")
        if player.get('golden_ducks', 0) > 0:
            duck_stats.append(f"{self.colors['yellow']}Golden:{player['golden_ducks']}{self.colors['reset']}")
        
        duck_display = f"Ducks:({', '.join(duck_stats)})" if duck_stats else "Ducks:0"
        
        # Main stats line
        stats_line1 = f"{nick} > {duck_display} | Level {level}: {self.colors['cyan']}{title}{self.colors['reset']} | XP: {player['xp']}"
        if xp_for_next > 0:
            stats_line1 += f" (next: {xp_for_next})"
        
        # Combat stats line
        karma_color = self.colors['green'] if player.get('karma', 0) >= 0 else self.colors['red']
        karma_display = f"{karma_color}Karma:{player.get('karma', 0)}{self.colors['reset']}"
        
        stats_line2 = f"{nick} > {karma_display} | Accuracy: {player['accuracy']}% (effective: {effective_accuracy:.1f}%) | Reliability: {self.calculate_gun_reliability(player)}%"
        
        # Equipment line
        weapon_name = player.get('weapon', 'pistol').replace('_', ' ').title()
        stats_line3 = f"{nick} > Weapon: {weapon_name}{gun_status} | Ammo: {player['ammo']}/{player['max_ammo']} | Chargers: {player['chargers']}/{player['max_chargers']}"
        
        # Advanced stats line
        best_time = player.get('best_time', 999.9)
        best_display = f"{best_time:.3f}s" if best_time < 999 else "none"
        
        stats_line4 = f"{nick} > Best time: {best_display} | Avg time: {average_time:.3f}s | Jams: {player.get('jammed_count', 0)} | Accidents: {player.get('accidents', 0)} | Lucky shots: {player.get('lucky_shots', 0)}"
        
        # Send all stats
        await self.send_user_message(nick, channel, stats_line1)
        await self.send_user_message(nick, channel, stats_line2)
        await self.send_user_message(nick, channel, stats_line3)
        await self.send_user_message(nick, channel, stats_line4)
        
        # Inventory display
        if player.get('inventory'):
            inventory_items = []
            shop_items = {
                '1': 'Extra bullet', '2': 'Extra clip', '3': 'AP ammo', '4': 'Explosive ammo',
                '5': 'Gun restore', '6': 'Grease', '7': 'Sight', '8': 'Detector', '9': 'Silencer',
                '10': 'Clover', '11': 'Shotgun', '12': 'Rifle', '13': 'Sniper', '14': 'Auto shotgun',
                '15': 'Sand', '16': 'Water', '17': 'Sabotage', '18': 'Life insurance',
                '19': 'Liability insurance', '20': 'Decoy', '21': 'Bread', '22': 'Duck detector', '23': 'Mechanical duck'
            }
            
            for item_id, count in player['inventory'].items():
                item_name = shop_items.get(item_id, f"Item #{item_id}")
                inventory_items.append(f"{item_id}:{item_name}({count})")
                
            if inventory_items:
                max_slots = self.get_config('economy.max_inventory_slots', 20)
                total_items = sum(player['inventory'].values())
                inventory_display = f"{nick} > {self.colors['magenta']}Inventory ({total_items}/{max_slots}):{self.colors['reset']} {' | '.join(inventory_items[:10])}"
                if len(inventory_items) > 10:
                    inventory_display += f" ... and {len(inventory_items) - 10} more"
                await self.send_user_message(nick, channel, inventory_display)
        
    async def handle_rearm(self, nick, channel, user, target_nick):
        """Rearm a player whose gun was confiscated"""
        player = self.get_player(user)
        target_nick_lower = target_nick.lower()
        
        if not player:
            self.send_message(channel, f"{nick} > Player data not found!")
            return
            
        # Check if target exists
        if target_nick_lower not in self.players:
            self.send_message(channel, f"{nick} > Player {target_nick} not found!")
            return
            
        target_player = self.players[target_nick_lower]
        
        # Check if target's gun is confiscated
        if not target_player.get('gun_confiscated', False):
            self.send_message(channel, f"{nick} > {target_nick}'s gun is not confiscated!")
            return
            
        # Admins can rearm anyone for free
        is_admin = self.is_admin(user)
        
        if is_admin:
            # Admin rearm - no cost, configurable restoration
            target_player['gun_confiscated'] = False
            
            # Configure ammo restoration
            if self.get_config('moderation.admin_rearm_gives_full_ammo', True):
                target_player['ammo'] = target_player['max_ammo']  # Full ammo
                ammo_text = "full ammo"
            else:
                target_player['ammo'] = min(target_player['ammo'] + 1, target_player['max_ammo'])  # Just +1 ammo
                ammo_text = "+1 ammo"
            
            # Configure charger restoration
            if self.get_config('moderation.admin_rearm_gives_full_chargers', True):
                target_player['chargers'] = target_player.get('max_chargers', 2)  # Full chargers
                charger_text = ", full chargers"
            else:
                charger_text = ""
            
            if target_nick_lower == nick.lower():
                self.send_message(channel, f"{nick} > {self.colors['green']}Admin command: Gun restored with {ammo_text}{charger_text}.{self.colors['reset']}")
            else:
                self.send_message(channel, f"{nick} > {self.colors['green']}Admin command: {target_nick}'s gun restored with {ammo_text}{charger_text}.{self.colors['reset']}")
            self.save_database()
        elif target_nick_lower == nick.lower():
            # Regular player rearming self - costs XP
            rearm_cost = 40
            if player['xp'] < rearm_cost:
                self.send_message(channel, f"{nick} > You need {rearm_cost} XP to rearm yourself (you have {player['xp']} XP)")
                return
                
            player['xp'] -= rearm_cost
            player['gun_confiscated'] = False
            player['ammo'] = player['max_ammo']  # Full ammo when rearmed
            self.send_message(channel, f"{nick} > {self.colors['green']}You rearmed yourself! [-{rearm_cost} XP] Gun restored with full ammo.{self.colors['reset']}")
            self.save_player(user)
        else:
            # Regular player rearming someone else - costs XP (friendly gesture)
            rearm_cost_xp = 5
            if player['xp'] < rearm_cost_xp:
                self.send_message(channel, f"{nick} > You need {rearm_cost_xp} XP to rearm {target_nick} (you have {player['xp']} XP)")
                return
                
            player['xp'] -= rearm_cost_xp
            target_player['gun_confiscated'] = False
            target_player['ammo'] = target_player['max_ammo']  # Full ammo when rearmed
            self.send_message(channel, f"{nick} > {self.colors['green']}You rearmed {target_nick}! [-{rearm_cost_xp} XP] {target_nick}'s gun restored with full ammo.{self.colors['reset']}")
            self.save_player(user)
            self.save_database()
        
    async def handle_help(self, nick, channel):
        help_lines = [
            f"{nick} > {self.colors['cyan']}🦆 DUCKHUNT 🦆{self.colors['reset']} !bang !bef !reload !stats !top !shop !buy <id>",
            f"{nick} > {self.colors['yellow']}Golden ducks: 50 XP{self.colors['reset']} | {self.colors['red']}Gun jamming & ricochets ON{self.colors['reset']} | Timeout: {self.duck_timeout_min}-{self.duck_timeout_max}s"
        ]
        if self.is_admin(f"{nick}!*@*"):  # Check if admin
            help_lines.append(f"{nick} > {self.colors['red']}Admin:{self.colors['reset']} !duck !golden !ban !reset !resetdb !rearm !giveitem !givexp | /msg {self.config['nick']} restart|quit")
        for line in help_lines:
            self.send_message(channel, line)
            
    async def handle_output(self, nick, channel, user, output_type):
        """Handle output mode setting (PRIVMSG or NOTICE)"""
        player = self.get_player(user)
        if not player:
            self.send_message(channel, f"{nick} > Player data not found!")
            return
        
        # Ensure player has settings (for existing players)
        if 'settings' not in player:
            player['settings'] = {
                'notices': True
            }
        
        output_type = output_type.upper()
        
        if output_type == 'PRIVMSG':
            player['settings']['notices'] = False
            self.save_database()
            self.send_message(channel, f"{nick} > Output mode set to {self.colors['cyan']}PRIVMSG{self.colors['reset']} (private messages)")
            
        elif output_type == 'NOTICE':
            player['settings']['notices'] = True
            self.save_database()
            self.send_message(channel, f"{nick} > Output mode set to {self.colors['cyan']}NOTICE{self.colors['reset']} (channel notices)")
            
        else:
            current_mode = 'NOTICE' if player['settings']['notices'] else 'PRIVMSG'
            self.send_message(channel, f"{nick} > Current output mode: {self.colors['cyan']}{current_mode}{self.colors['reset']} | Usage: !output PRIVMSG or !output NOTICE")
            
    async def handle_shop(self, nick, channel, user):
        player = self.get_player(user)
        if not player:
            self.send_message(channel, f"{nick} > Player data not found!")
            return
        
        # Show compact shop in eggdrop style
        shop_msg = f"[Duck Hunt] Purchasable items: 1-Extra bullet(7xp) 2-Extra clip(20xp) 3-AP ammo(15xp) 4-Explosive ammo(25xp) 5-Repurchase gun(40xp) 6-Grease(8xp) 7-Sight(6xp) 8-Infrared detector(15xp) 9-Silencer(5xp) 10-Four-leaf clover(13xp) 11-Shotgun(100xp) 12-Assault rifle(200xp) 13-Sniper rifle(350xp) 14-Auto shotgun(500xp) 15-Sand(7xp) 16-Water bucket(10xp) 17-Sabotage(14xp) 18-Life insurance(10xp) 19-Liability insurance(5xp) 20-Decoy(80xp) 21-Bread(50xp) 22-Duck detector(50xp) 23-Mechanical duck(50xp)"
        self.send_message(channel, f"{nick} > {shop_msg}")
        self.send_message(channel, f"{nick} > Your XP: {player['xp']} | Use !shop <id> to purchase")
            
    async def handle_buy(self, nick, channel, item, user):
        """Buy items and add to inventory"""
        player = self.get_player(user)
        if not player:
            self.send_message(channel, f"{nick} > Player data not found!")
            return
        
        # Check if inventory system is enabled
        if not self.get_config('economy.inventory_system_enabled', True):
            self.send_message(channel, f"{nick} > Inventory system is disabled!")
            return
            
        # Initialize inventory if not exists
        if 'inventory' not in player:
            player['inventory'] = {}
        
        # Eggdrop-style shop items with XP costs
        shop_items = {
            '1': {'name': 'Extra bullet', 'cost': 7},
            '2': {'name': 'Extra clip', 'cost': 20},
            '3': {'name': 'AP ammo', 'cost': 15},
            '4': {'name': 'Explosive ammo', 'cost': 25},
            '5': {'name': 'Repurchase confiscated gun', 'cost': 40},
            '6': {'name': 'Grease', 'cost': 8},
            '7': {'name': 'Sight', 'cost': 6},
            '8': {'name': 'Infrared detector', 'cost': 15},
            '9': {'name': 'Silencer', 'cost': 5},
            '10': {'name': 'Four-leaf clover', 'cost': 13},
            '11': {'name': 'Shotgun', 'cost': 100},
            '12': {'name': 'Assault rifle', 'cost': 200},
            '13': {'name': 'Sniper rifle', 'cost': 350},
            '14': {'name': 'Automatic shotgun', 'cost': 500},
            '15': {'name': 'Handful of sand', 'cost': 7},
            '16': {'name': 'Water bucket', 'cost': 10},
            '17': {'name': 'Sabotage', 'cost': 14},
            '18': {'name': 'Life insurance', 'cost': 10},
            '19': {'name': 'Liability insurance', 'cost': 5},
            '20': {'name': 'Decoy', 'cost': 80},
            '21': {'name': 'Piece of bread', 'cost': 50},
            '22': {'name': 'Ducks detector', 'cost': 50},
            '23': {'name': 'Mechanical duck', 'cost': 50}
        }
        
        if item not in shop_items:
            self.send_message(channel, f"{nick} > Invalid item ID. Use !shop to see available items.")
            return
            
        shop_item = shop_items[item]
        cost = shop_item['cost']
        
        if player['xp'] < cost:
            self.send_message(channel, f"{nick} > Not enough XP! You need {cost} XP but only have {player['xp']}.")
            return
            
        # Check inventory space
        max_slots = self.get_config('economy.max_inventory_slots', 20)
        if max_slots is None:
            max_slots = 20
        total_items = sum(player['inventory'].values())
        if total_items >= max_slots:
            self.send_message(channel, f"{nick} > Inventory full! ({total_items}/{max_slots}) Use items or increase capacity.")
            return
            
        # Purchase the item and add to inventory
        player['xp'] -= cost
        if item in player['inventory']:
            player['inventory'][item] += 1
        else:
            player['inventory'][item] = 1
            
        self.send_message(channel, f"{nick} > Purchased {shop_item['name']}! Added to inventory ({total_items + 1}/{max_slots})")
        
        # Save to database after purchase
        self.save_player(user)
        
    async def handle_sell(self, nick, channel, item_id, user):
        """Sell items from inventory for 70% of original cost"""
        player = self.get_player(user)
        if not player:
            self.send_message(channel, f"{nick} > Player data not found!")
            return
        
        # Check if inventory system is enabled
        if not self.get_config('economy.inventory_system_enabled', True):
            self.send_message(channel, f"{nick} > Inventory system is disabled!")
            return
            
        # Initialize inventory if not exists
        if 'inventory' not in player:
            player['inventory'] = {}
        
        # Check if item is in inventory
        if item_id not in player['inventory'] or player['inventory'][item_id] <= 0:
            self.send_message(channel, f"{nick} > You don't have that item! Check !stats to see your inventory.")
            return
        
        # Get shop item data for pricing
        shop_items = {
            '1': {'name': 'Extra bullet', 'cost': 7},
            '2': {'name': 'Extra clip', 'cost': 20},
            '3': {'name': 'AP ammo', 'cost': 15},
            '4': {'name': 'Explosive ammo', 'cost': 25},
            '5': {'name': 'Repurchase confiscated gun', 'cost': 40},
            '6': {'name': 'Grease', 'cost': 8},
            '7': {'name': 'Sight', 'cost': 6},
            '8': {'name': 'Infrared detector', 'cost': 15},
            '9': {'name': 'Silencer', 'cost': 5},
            '10': {'name': 'Four-leaf clover', 'cost': 13},
            '11': {'name': 'Shotgun', 'cost': 100},
            '12': {'name': 'Assault rifle', 'cost': 200},
            '13': {'name': 'Sniper rifle', 'cost': 350},
            '14': {'name': 'Automatic shotgun', 'cost': 500},
            '15': {'name': 'Handful of sand', 'cost': 7},
            '16': {'name': 'Water bucket', 'cost': 10},
            '17': {'name': 'Sabotage', 'cost': 14},
            '18': {'name': 'Life insurance', 'cost': 10},
            '19': {'name': 'Liability insurance', 'cost': 5},
            '20': {'name': 'Decoy', 'cost': 80},
            '21': {'name': 'Piece of bread', 'cost': 50},
            '22': {'name': 'Ducks detector', 'cost': 50},
            '23': {'name': 'Mechanical duck', 'cost': 50}
        }
        
        if item_id not in shop_items:
            self.send_message(channel, f"{nick} > Invalid item ID!")
            return
            
        shop_item = shop_items[item_id]
        original_cost = shop_item['cost']
        sell_price = int(original_cost * 0.7)  # 70% of original cost
        
        # Remove item from inventory
        player['inventory'][item_id] -= 1
        if player['inventory'][item_id] <= 0:
            del player['inventory'][item_id]
            
        # Give XP back
        player['xp'] += sell_price
        
        total_items = sum(player['inventory'].values())
        max_slots = self.get_config('economy.max_inventory_slots', 20)
        
        self.send_message(channel, f"{nick} > Sold {shop_item['name']} for {sell_price}xp! Inventory: ({total_items}/{max_slots})")
        
        # Save to database after sale
        self.save_player(user)
        
    async def handle_use(self, nick, channel, item_id, user, target_nick=None):
        """Use an item from inventory"""
        player = self.get_player(user)
        if not player:
            self.send_message(channel, f"{nick} > Player data not found!")
            return
            
        # Check if item is in inventory
        if item_id not in player['inventory'] or player['inventory'][item_id] <= 0:
            self.send_message(channel, f"{nick} > You don't have that item! Check !stats to see your inventory.")
            return
            
        # Get shop item data for reference
        shop_items = {
            '1': {'name': 'Extra bullet', 'effect': 'ammo'},
            '2': {'name': 'Extra clip', 'effect': 'max_ammo'},
            '3': {'name': 'AP ammo', 'effect': 'accuracy'},
            '4': {'name': 'Explosive ammo', 'effect': 'explosive'},
            '5': {'name': 'Repurchase confiscated gun', 'effect': 'gun'},
            '6': {'name': 'Grease', 'effect': 'reliability'},
            '7': {'name': 'Sight', 'effect': 'accuracy'},
            '8': {'name': 'Infrared detector', 'effect': 'detector'},
            '9': {'name': 'Silencer', 'effect': 'silencer'},
            '10': {'name': 'Four-leaf clover', 'effect': 'luck'},
            '11': {'name': 'Shotgun', 'effect': 'shotgun'},
            '12': {'name': 'Assault rifle', 'effect': 'rifle'},
            '13': {'name': 'Sniper rifle', 'effect': 'sniper'},
            '14': {'name': 'Automatic shotgun', 'effect': 'auto_shotgun'},
            '15': {'name': 'Handful of sand', 'effect': 'sand'},
            '16': {'name': 'Water bucket', 'effect': 'water'},
            '17': {'name': 'Sabotage', 'effect': 'sabotage'},
            '18': {'name': 'Life insurance', 'effect': 'life_insurance'},
            '19': {'name': 'Liability insurance', 'effect': 'liability'},
            '20': {'name': 'Decoy', 'effect': 'decoy'},
            '21': {'name': 'Piece of bread', 'effect': 'bread'},
            '22': {'name': 'Ducks detector', 'effect': 'duck_detector'},
            '23': {'name': 'Mechanical duck', 'effect': 'mechanical'}
        }
        
        if item_id not in shop_items:
            self.send_message(channel, f"{nick} > Invalid item ID!")
            return
            
        shop_item = shop_items[item_id]
        effect = shop_item['effect']
        
        # Determine target player
        if target_nick and target_nick.lower() != nick.lower():
            # Using on someone else
            target_nick_lower = target_nick.lower()
            if target_nick_lower not in self.players:
                self.send_message(channel, f"{nick} > Player {target_nick} not found!")
                return
            target_player = self.players[target_nick_lower]
            using_on_other = True
        else:
            # Using on self
            target_player = player
            target_nick = nick
            using_on_other = False
            
        # Remove item from inventory
        player['inventory'][item_id] -= 1
        if player['inventory'][item_id] <= 0:
            del player['inventory'][item_id]
            
        # Apply item effects
        if effect == 'ammo':
            target_player['ammo'] = min(target_player['max_ammo'], target_player['ammo'] + 1)
            if using_on_other:
                self.send_message(channel, f"{nick} > Used {shop_item['name']} on {target_nick}! +1 ammo")
            else:
                self.send_message(channel, f"{nick} > Used {shop_item['name']}! +1 ammo")
        elif effect == 'water':
            # Water bucket - splash attack on target player
            if using_on_other:
                # Reduce target's accuracy temporarily
                target_player['accuracy'] = max(10, target_player['accuracy'] - 15)
                self.send_message(channel, f"{nick} > *SPLASH!* You soaked {target_nick} with water! Their accuracy reduced by 15%!")
            else:
                self.send_message(channel, f"{nick} > You splashed yourself with water... why?")
        elif effect == 'sand':
            # Handful of sand - blind target temporarily
            if using_on_other:
                target_player['accuracy'] = max(5, target_player['accuracy'] - 20)
                self.send_message(channel, f"{nick} > *POCKET SAND!* You threw sand in {target_nick}'s eyes! Their accuracy reduced by 20%!")
            else:
                self.send_message(channel, f"{nick} > You threw sand in your own eyes... brilliant strategy!")
        # Add more effects as needed...
        else:
            # Default effects for other items
            self.send_message(channel, f"{nick} > Used {shop_item['name']}! (Effect: {effect})")
            
        # Save changes
        self.save_player(user)
        if using_on_other:
            # Save target player too if different
            target_user = f"{target_nick.lower()}!user@host"  # Simplified - would need real user data
            self.save_database()
            
    async def handle_trade(self, nick, channel, user, target_nick, item, amount):
        """Trade items with other players"""
        player = self.get_player(user)
        if not player:
            return
            
        try:
            amount = int(amount)
        except ValueError:
            self.send_message(channel, f"{nick} > Amount must be a number!")
            return
            
        if amount <= 0:
            self.send_message(channel, f"{nick} > Amount must be positive!")
            return
            
        if amount > 10000:  # Prevent excessive amounts
            self.send_message(channel, f"{nick} > Amount too large! Maximum: 10,000")
            return
            
        # Find target player (simplified - would need to track online users in real implementation)
        if item == 'coins':
            if player['coins'] < amount:
                self.send_message(channel, f"{nick} > You don't have {amount} coins!")
                return
            player['coins'] -= amount
            self.send_message(channel, f"{nick} > Offering {amount} coins to {target_nick}. They can !accept or !decline.")
            # In real implementation, store pending trade
            
        elif item == 'ammo':
            if player['ammo'] < amount:
                self.send_message(channel, f"{nick} > You don't have {amount} ammo!")
                return
            self.send_message(channel, f"{nick} > Offering {amount} ammo to {target_nick}.")
            
        elif item == 'chargers':
            if player['chargers'] < amount:
                self.send_message(channel, f"{nick} > You don't have {amount} chargers!")
                return
            self.send_message(channel, f"{nick} > Offering {amount} chargers to {target_nick}.")
            
        else:
            self.send_message(channel, f"{nick} > Can't trade '{item}'. Use: coins, ammo, or chargers")
            
        self.save_player(user)
        
    async def handle_listplayers(self, nick, channel):
        """Admin command to list all players"""
        if not self.players:
            self.send_message(channel, f"{nick} > No players in database.")
            return
            
        player_list = []
        for nick_key, data in self.players.items():
            shot_count = data['caught']
            befriended_count = data.get('befriended', 0)
            total_ducks = shot_count + befriended_count
            player_list.append(f"{nick_key}(Ducks:{total_ducks},Shot:{shot_count},Befriended:{befriended_count})")
            
        players_str = " | ".join(player_list[:10])  # Limit to first 10
        if len(self.players) > 10:
            players_str += f" ... and {len(self.players) - 10} more"
            
        self.send_message(channel, f"{nick} > Players: {players_str}")
        
    async def handle_ban(self, nick, channel, target_nick):
        """Admin command to ban a player"""
        target_nick_lower = target_nick.lower()
        if target_nick_lower in self.players:
            del self.players[target_nick_lower]
            self.send_message(channel, f"{nick} > Banned and reset {target_nick}")
            self.save_database()
        else:
            self.send_message(channel, f"{nick} > Player {target_nick} not found!")
            
    async def handle_reset(self, nick, channel, target_nick):
        """Admin command to reset a player's stats"""
        target_nick_lower = target_nick.lower()
        if target_nick_lower in self.players:
            # Reset to defaults
            self.players[target_nick_lower] = {
                'caught': 0, 'ammo': 10, 'max_ammo': 10,
                'chargers': 2, 'max_chargers': 2, 'xp': 0,
                'accuracy': 85, 'reliability': 90, 'gun_level': 1,
                'luck': 0, 'gun_type': 'pistol'
            }
            self.send_message(channel, f"{nick} > Reset {target_nick}'s stats to defaults")
            self.save_database()
        else:
            self.send_message(channel, f"{nick} > Player {target_nick} not found!")
            
    async def handle_reset_database(self, nick, channel, user):
        """Admin command to reset entire database - requires confirmation"""
        self.send_message(channel, f"{nick} > {self.colors['red']}⚠️  DATABASE RESET WARNING ⚠️{self.colors['reset']}")
        self.send_message(channel, f"{nick} > This will DELETE ALL player data, statistics, and progress!")
        self.send_message(channel, f"{nick} > {self.colors['yellow']}Players affected: {len(self.players)}{self.colors['reset']}")
        self.send_message(channel, f"{nick} > To confirm, type: {self.colors['cyan']}!resetdb confirm DESTROY_ALL_DATA{self.colors['reset']}")
        self.send_message(channel, f"{nick} > {self.colors['red']}This action CANNOT be undone!{self.colors['reset']}")
        
    async def handle_reset_database_confirm(self, nick, channel, user, confirmation):
        """Confirm and execute database reset"""
        if confirmation != "DESTROY_ALL_DATA":
            self.send_message(channel, f"{nick} > {self.colors['red']}Incorrect confirmation code. Database reset cancelled.{self.colors['reset']}")
            return
            
        # Log the reset action
        self.logger.warning(f"DATABASE RESET initiated by admin {nick} - All player data will be destroyed")
        
        # Backup current database
        import shutil
        backup_name = f"duckhunt_backup_{int(time.time())}.json"
        try:
            shutil.copy2(self.db_file, backup_name)
            self.send_message(channel, f"{nick} > {self.colors['cyan']}Database backed up to: {backup_name}{self.colors['reset']}")
        except Exception as e:
            self.logger.error(f"Failed to create backup: {e}")
            self.send_message(channel, f"{nick} > {self.colors['red']}Warning: Could not create backup!{self.colors['reset']}")
        
        # Clear all data
        player_count = len(self.players)
        self.players.clear()
        self.ducks.clear()
        self.ignored_nicks.clear()
        
        # Save empty database
        self.save_database()
        
        # Confirmation messages
        self.send_message(channel, f"{nick} > {self.colors['green']}✅ DATABASE RESET COMPLETE{self.colors['reset']}")
        self.send_message(channel, f"{nick} > {self.colors['yellow']}{player_count} player records deleted{self.colors['reset']}")
        self.send_message(channel, f"{nick} > All ducks cleared, fresh start initiated")
        self.logger.warning(f"Database reset completed by {nick} - {player_count} players deleted")
            
    async def handle_restart(self, nick, channel):
        """Admin command to restart the bot"""
        self.send_message(channel, f"{nick} > Restarting bot...")
        self.logger.info(f"Bot restart requested by {nick}")
        
        # Close connections gracefully
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        
        # Save any pending data
        self.save_database()
        
        # Restart the Python process
        self.logger.info("Restarting Python process...")
        python = sys.executable
        script = sys.argv[0]
        args = sys.argv[1:]
        
        # Use subprocess to restart
        subprocess.Popen([python, script] + args)
        
        # Exit current process
        sys.exit(0)
        
    async def handle_quit(self, nick, channel):
        """Admin command to quit the bot"""
        self.send_message(channel, f"{nick} > Shutting down bot...")
        self.logger.info(f"Bot shutdown requested by {nick}")
        # Close connections gracefully
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        # Exit with code 0 for normal shutdown
        import sys
        sys.exit(0)
        
    async def handle_ignore(self, nick, channel, target_nick):
        """Admin command to ignore a user"""
        if target_nick in self.ignored_nicks:
            self.send_message(channel, f"{nick} > {target_nick} is already ignored!")
            return
            
        self.ignored_nicks.add(target_nick)
        self.send_message(channel, f"{nick} > Now ignoring {target_nick}. Total ignored: {len(self.ignored_nicks)}")
        self.logger.info(f"{nick} added {target_nick} to ignore list")
        
    async def handle_delignore(self, nick, channel, target_nick):
        """Admin command to stop ignoring a user"""
        if target_nick not in self.ignored_nicks:
            self.send_message(channel, f"{nick} > {target_nick} is not ignored!")
            return
            
        self.ignored_nicks.remove(target_nick)
        self.send_message(channel, f"{nick} > No longer ignoring {target_nick}. Total ignored: {len(self.ignored_nicks)}")
        self.logger.info(f"{nick} removed {target_nick} from ignore list")
    
    async def handle_admin_giveitem(self, nick, channel, target_nick, item):
        """Admin command to give an item to a player"""
        target_nick_lower = target_nick.lower()
        
        # Check if target exists
        if target_nick_lower not in self.players:
            self.send_message(channel, f"{nick} > Player {target_nick} not found!")
            return
        
        # Shop items reference for item names
        shop_items = {
            '1': {'name': 'Extra bullet', 'effect': 'ammo'},
            '2': {'name': 'Extra clip', 'effect': 'max_ammo'},
            '3': {'name': 'AP ammo', 'effect': 'accuracy'},
            '4': {'name': 'Explosive ammo', 'effect': 'explosive'},
            '5': {'name': 'Repurchase confiscated gun', 'effect': 'gun'},
            '6': {'name': 'Grease', 'effect': 'reliability'},
            '7': {'name': 'Sight', 'effect': 'accuracy'},
            '8': {'name': 'Infrared detector', 'effect': 'detector'},
            '9': {'name': 'Silencer', 'effect': 'silencer'},
            '10': {'name': 'Four-leaf clover', 'effect': 'luck'},
            '11': {'name': 'Sunglasses', 'effect': 'sunglasses'},
            '12': {'name': 'Spare clothes', 'effect': 'clothes'},
            '13': {'name': 'Brush for gun', 'effect': 'brush'},
            '14': {'name': 'Mirror', 'effect': 'mirror'},
            '15': {'name': 'Handful of sand', 'effect': 'sand'},
            '16': {'name': 'Water bucket', 'effect': 'water'},
            '17': {'name': 'Sabotage', 'effect': 'sabotage'},
            '18': {'name': 'Life insurance', 'effect': 'life_insurance'},
            '19': {'name': 'Liability insurance', 'effect': 'liability'},
            '20': {'name': 'Decoy', 'effect': 'decoy'},
            '21': {'name': 'Piece of bread', 'effect': 'bread'},
            '22': {'name': 'Ducks detector', 'effect': 'duck_detector'},
            '23': {'name': 'Mechanical duck', 'effect': 'mechanical'}
        }
        
        if item not in shop_items:
            self.send_message(channel, f"{nick} > Invalid item ID '{item}'. Use item IDs 1-23.")
            return
        
        target_player = self.players[target_nick_lower]
        shop_item = shop_items[item]
        effect = shop_item['effect']
        
        # Apply the item effect
        if effect == 'ammo':
            target_player['ammo'] = min(target_player['ammo'] + 1, target_player['max_ammo'])
        elif effect == 'max_ammo':
            target_player['max_ammo'] += 1
            target_player['ammo'] = target_player['max_ammo']  # Fill ammo
        elif effect == 'accuracy':
            target_player['accuracy'] = min(target_player['accuracy'] + 5, 100)
        elif effect == 'explosive':
            target_player['explosive_ammo'] = True
        elif effect == 'gun':
            target_player['gun_confiscated'] = False
            target_player['ammo'] = target_player['max_ammo']
        elif effect == 'reliability':
            target_player['reliability'] = min(target_player['reliability'] + 5, 100)
        elif effect == 'luck':
            target_player['luck'] = target_player.get('luck', 0) + 1
        # Add other effects as needed
        
        self.send_message(channel, f"{nick} > {self.colors['green']}Gave {shop_item['name']} to {target_nick}!{self.colors['reset']}")
        self.save_database()
    
    async def handle_admin_givexp(self, nick, channel, target_nick, amount):
        """Admin command to give XP to a player"""
        target_nick_lower = target_nick.lower()
        
        # Check if target exists
        if target_nick_lower not in self.players:
            self.send_message(channel, f"{nick} > Player {target_nick} not found!")
            return
        
        try:
            xp_amount = int(amount)
        except ValueError:
            self.send_message(channel, f"{nick} > Amount must be a number!")
            return
        
        if abs(xp_amount) > 50000:  # Prevent excessive XP changes
            self.send_message(channel, f"{nick} > XP amount too large! Maximum: ±50,000")
            return
        
        target_player = self.players[target_nick_lower]
        old_xp = target_player['xp']
        target_player['xp'] = max(0, target_player['xp'] + xp_amount)  # Prevent negative XP
        
        color = self.colors['green'] if xp_amount >= 0 else self.colors['red']
        sign = '+' if xp_amount >= 0 else ''
        self.send_message(channel, f"{nick} > {color}Gave {sign}{xp_amount} XP to {target_nick}! (Total: {target_player['xp']} XP){self.colors['reset']}")
        self.save_database()
    
    def get_duck_spawn_message(self):
        """Get a random duck spawn message with different types"""
        duck_types = [
            {"msg": "-.,¸¸.-·°'`'°·-.,¸¸.-·°'`'°· \\_O<   QUACK", "type": "normal"},  # Normal duck
            {"msg": "-._..-'`'°-,_,.-'`'°-,_,.-'`'°-,_,.-°  \\_o<  A duck waddles by! QUACK QUACK", "type": "normal"},  # Waddling duck
            {"msg": "~~~°*°~~~°*°~~~°*°~~~  \\_O<  SPLASH! A duck lands in the water! QUACK!", "type": "normal"},  # Water duck
            {"msg": "***GOLDEN***  \\_O<  *** A golden duck appears! *** QUACK QUACK! ***GOLDEN***", "type": "golden"},  # Golden duck (rare)
            {"msg": "°~°*°~°*°~°  \\_o<  Brrr! A winter duck appears! QUACK!", "type": "normal"},  # Winter duck
            {"msg": ".,¸¸.-·°'`'°·-.,¸¸.-·°'`'°·  \\_O<  A spring duck blooms into view! QUACK!", "type": "normal"},  # Spring duck
            {"msg": "***ZAP***  \\_O<  BZZT! An electric duck sparks to life! QUACK! ***ZAP***", "type": "normal"},  # Electric duck
            {"msg": "~*~*~*~  \\_o<  A sleepy night duck appears... *yawn* quack...", "type": "normal"},  # Night duck
        ]
        
        # Golden duck is rare (5% chance)
        if random.random() < 0.05:
            golden_duck = [d for d in duck_types if d["type"] == "golden"][0]
            return golden_duck
        else:
            # Choose from normal duck types
            normal_ducks = [d for d in duck_types if d["type"] == "normal"]
            return random.choice(normal_ducks)
    
    async def spawn_duck_now(self, channel, force_golden=False):
        """Admin command to spawn a duck immediately"""
        # Create duck with unique ID and type
        duck_id = str(uuid.uuid4())[:8]  # Short ID for easier tracking
        
        if force_golden:
            # Force spawn a golden duck
            duck_info = {
                "msg": f"{self.colors['yellow']}***GOLDEN***{self.colors['reset']}  \\_$<  {self.colors['yellow']}*** A golden duck appears! ***{self.colors['reset']} QUACK QUACK! {self.colors['yellow']}***GOLDEN***{self.colors['reset']}",
                "type": "golden"
            }
        else:
            duck_info = self.get_duck_spawn_message()
            
        duck_timeout = random.randint(self.duck_timeout_min, self.duck_timeout_max)
        duck = {
            'alive': True, 
            'spawn_time': time.time(),
            'id': duck_id,
            'type': duck_info['type'],
            'message': duck_info['msg'],
            'timeout': duck_timeout
        }
        
        # Initialize channel duck list if needed
        if channel not in self.ducks:
            self.ducks[channel] = []
        
        # Add duck to channel
        self.ducks[channel].append(duck)
        
        # Send spawn message
        self.send_message(channel, duck_info['msg'])
        self.logger.info(f"Admin spawned {duck_info['type']} duck {duck_id} in {channel}")
        return True
        return True  # Return True to indicate duck was spawned
        
    async def spawn_ducks(self):
        # Spawn first duck immediately after joining
        await asyncio.sleep(5)  # Brief delay for players to see the bot joined
        for channel in self.channels_joined:
            await self.spawn_duck_now(channel)
        
        # Start duck timeout checker
        asyncio.create_task(self.duck_timeout_checker())
        
        while not self.shutdown_requested:
            wait_time = random.randint(self.duck_spawn_min, self.duck_spawn_max)
            self.logger.info(f"Waiting {wait_time//60}m {wait_time%60}s for next duck")
            
            # Sleep in chunks to check shutdown flag
            for _ in range(wait_time):
                if self.shutdown_requested:
                    self.logger.info("Duck spawning stopped due to shutdown request")
                    return
                await asyncio.sleep(1)
            
            # Spawn only one duck per channel if no alive ducks exist
            for channel in self.channels_joined:
                if self.shutdown_requested:
                    return
                    
                # Check if there are any alive ducks in this channel
                channel_ducks = self.ducks.get(channel, [])
                alive_ducks = [duck for duck in channel_ducks if duck.get('alive')]
                
                # Only spawn if no ducks are alive (one duck at a time naturally)
                if not alive_ducks:
                    await self.spawn_duck_now(channel)
                    break  # Only spawn in the first available channel
                    
    async def duck_timeout_checker(self):
        """Remove ducks that have been around too long"""
        while not self.shutdown_requested:
            await asyncio.sleep(10)  # Check every 10 seconds
            current_time = time.time()
            
            for channel in list(self.ducks.keys()):
                if channel in self.ducks:
                    ducks_to_remove = []
                    for i, duck in enumerate(self.ducks[channel]):
                        duck_timeout = duck.get('timeout', 60)  # Use individual timeout or default to 60
                        if duck['alive'] and (current_time - duck['spawn_time']) > duck_timeout:
                            # Duck wandered off
                            ducks_to_remove.append(i)
                            self.send_message(channel, f"A duck wandered off... *quack quack* (timeout after {duck_timeout}s)")
                            self.logger.info(f"Duck {duck['id']} timed out in {channel}")
                    
                    # Remove timed out ducks (in reverse order to maintain indices)
                    for i in reversed(ducks_to_remove):
                        del self.ducks[channel][i]
        
    async def listen(self):
        """Listen for IRC messages with shutdown handling"""
        while not self.shutdown_requested:
            try:
                if not self.reader:
                    self.logger.error("No reader available")
                    break
                    
                # Use timeout to allow checking shutdown flag
                try:
                    line = await asyncio.wait_for(self.reader.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue  # Check shutdown flag
                    
                if not line:
                    self.logger.warning("Connection closed by server")
                    break
                    
                line = line.decode(errors='ignore').strip()
                if not line:
                    continue
                    
                self.logger.debug(f"<- {line}")
                
                if line.startswith('PING'):
                    self.send_raw('PONG ' + line.split()[1])
                    continue
                    
                prefix, command, params, trailing = parse_message(line)
                
            except Exception as e:
                self.logger.error(f"Error in listen loop: {e}")
                await asyncio.sleep(1)  # Brief pause before retry
                continue
            
            # Handle SASL authentication responses
            if command == 'CAP':
                await self.sasl_handler.handle_cap_response(params, trailing)
                    
            elif command == 'AUTHENTICATE':
                await self.sasl_handler.handle_authenticate_response(params)
                    
            elif command in ['903', '904', '905', '906', '907', '908']:  # SASL responses
                await self.sasl_handler.handle_sasl_result(command, params, trailing)
            
            elif command == '001':  # Welcome
                self.registered = True
                auth_status = " (SASL authenticated)" if self.sasl_handler.is_authenticated() else ""
                self.logger.info(f"Successfully registered!{auth_status}")
                
                # If SASL failed, try NickServ identification
                if not self.sasl_handler.is_authenticated():
                    await self.attempt_nickserv_auth()
                
                for chan in self.config['channels']:
                    self.logger.info(f"Joining {chan}")
                    self.send_raw(f'JOIN {chan}')
                    
            elif command == 'JOIN' and prefix and prefix.startswith(self.config['nick']):
                channel = trailing or (params[0] if params else '')
                if channel:
                    self.channels_joined.add(channel)
                    self.logger.info(f"Successfully joined {channel}")
                    
            elif command == 'PRIVMSG' and trailing:
                target = params[0] if params else ''
                sender = prefix.split('!')[0] if prefix else ''
                
                # Handle NickServ responses
                if sender.lower() == 'nickserv':
                    await self.handle_nickserv_response(trailing)
                elif trailing == 'VERSION':
                    self.send_raw(f'NOTICE {sender} :VERSION DuckHunt Bot v1.0')
                else:
                    await self.handle_command(prefix, target, trailing)

    async def cleanup(self):
        """Enhanced cleanup with graceful shutdown"""
        self.logger.info("Starting cleanup process...")
        
        try:
            # Cancel all running tasks
            for task in self.running_tasks.copy():
                if not task.done():
                    self.logger.debug(f"Cancelling task: {task.get_name()}")
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        self.logger.error(f"Error cancelling task: {e}")
            
            # Send goodbye message to all channels
            if self.writer and not self.writer.is_closing():
                for channel in self.channels_joined:
                    self.send_message(channel, "🦆 DuckHunt Bot shutting down. Thanks for playing! 🦆")
                    await asyncio.sleep(0.1)  # Brief delay between messages
                
                self.send_raw('QUIT :DuckHunt Bot shutting down gracefully')
                await asyncio.sleep(1.0)  # Give time for QUIT and messages to send
                
                self.writer.close()
                await self.writer.wait_closed()
                self.logger.info("IRC connection closed")
            
            # Final database save with verification
            self.save_database()
            self.logger.info(f"Final database save completed - {len(self.players)} players saved")
            
            # Clear in-memory data
            self.players.clear()
            self.ducks.clear()
            self.command_cooldowns.clear()
            
            self.logger.info("Cleanup completed successfully")
            
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
            import traceback
            traceback.print_exc()

    async def run(self):
        """Main bot entry point with enhanced shutdown handling"""
        try:
            # Setup signal handlers
            self.setup_signal_handlers()
            
            self.logger.info("Starting DuckHunt Bot...")
            self.load_database()
            await self.connect()
            
            # Create and track main tasks
            listen_task = asyncio.create_task(self.listen(), name="listen")
            duck_task = asyncio.create_task(self.wait_and_spawn_ducks(), name="duck_spawner")
            
            self.running_tasks.add(listen_task)
            self.running_tasks.add(duck_task)
            
            # Main execution loop with shutdown monitoring
            done, pending = await asyncio.wait(
                [listen_task, duck_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # If we get here, one task completed (likely due to error or shutdown)
            if self.shutdown_requested:
                self.logger.info("Shutdown requested, stopping all tasks...")
            else:
                self.logger.warning("A main task completed unexpectedly")
                
            # Cancel remaining tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                    
        except KeyboardInterrupt:
            self.logger.info("Keyboard interrupt received")
            self.shutdown_requested = True
        except Exception as e:
            self.logger.error(f"Fatal error in main loop: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await self.cleanup()
        
    async def wait_and_spawn_ducks(self):
        """Duck spawning with shutdown handling"""
        # Wait for registration and channel joins
        while not self.registered or not self.channels_joined and not self.shutdown_requested:
            await asyncio.sleep(1)
        
        if self.shutdown_requested:
            return
            
        self.logger.info("Starting duck spawning...")
        await self.spawn_ducks()

def main():
    """Enhanced main entry point with better shutdown handling"""
    bot = None
    try:
        # Load configuration
        with open('config.json') as f:
            config = json.load(f)
        
        # Create bot instance
        bot = SimpleIRCBot(config)
        bot.logger.info("DuckHunt Bot initializing...")
        
        # Run bot with graceful shutdown
        try:
            asyncio.run(bot.run())
        except KeyboardInterrupt:
            bot.logger.info("Keyboard interrupt received in main")
        except Exception as e:
            bot.logger.error(f"Runtime error: {e}")
            import traceback
            traceback.print_exc()
        
        bot.logger.info("DuckHunt Bot shutdown complete")
        
    except KeyboardInterrupt:
        print("\n🦆 DuckHunt Bot stopped by user")
    except FileNotFoundError:
        print("❌ Error: config.json not found")
        print("Please create a config.json file with your IRC server settings")
    except json.JSONDecodeError as e:
        print(f"❌ Error: Invalid config.json - {e}")
        print("Please check your config.json file syntax")
    except Exception as e:
        print(f"💥 Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Ensure final message
        print("🦆 Thanks for using DuckHunt Bot!")

if __name__ == '__main__':
    main()
