import asyncio
import ssl
import os
import time
import signal
from typing import Optional

from .logging_utils import setup_logger
from .utils import parse_irc_message, MessageManager
from .db import DuckDB
from .game import DuckGame
from .sasl import SASLHandler
from .shop import ShopManager
from .levels import LevelManager


class DuckHuntBot:
    """Simplified IRC Bot for DuckHunt game"""
    
    def __init__(self, config):
        self.config = config
        self.logger = setup_logger("DuckHuntBot")
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.registered = False
        self.channels_joined = set()
        self.shutdown_requested = False
        
        self.logger.info("🤖 Initializing DuckHunt Bot components...")
        
        self.db = DuckDB(bot=self)
        self.game = DuckGame(self, self.db)
        self.messages = MessageManager()
        
        self.sasl_handler = SASLHandler(self, config)
        
        admins_list = self.get_config('admins', ['colby']) or ['colby']
        self.admins = [admin.lower() for admin in admins_list]
        self.logger.info(f"👑 Configured {len(self.admins)} admin(s): {', '.join(self.admins)}")
        
        # Initialize level manager first
        levels_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'levels.json')
        self.levels = LevelManager(levels_file)
        
        # Initialize shop manager with levels reference
        shop_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shop.json')
        self.shop = ShopManager(shop_file, self.levels)
        
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
    
    def is_admin(self, user):
        """Check if user is admin with enhanced security checks"""
        if '!' not in user:
            return False
        
        nick = user.split('!')[0].lower()
        
        # Check admin configuration - support both nick-only (legacy) and hostmask patterns
        admin_config = self.get_config('admins', [])
        
        # Ensure admin_config is a list
        if not isinstance(admin_config, list):
            admin_config = []
        
        for admin_entry in admin_config:
            if isinstance(admin_entry, str):
                # Simple nick-based check (less secure but compatible)
                if admin_entry.lower() == nick:
                    self.logger.warning(f"Admin access granted via nick-only authentication: {user}")
                    return True
            elif isinstance(admin_entry, dict):
                # Enhanced hostmask-based authentication
                if admin_entry.get('nick', '').lower() == nick:
                    # Check hostmask pattern if provided
                    required_pattern = admin_entry.get('hostmask')
                    if required_pattern:
                        import fnmatch
                        if fnmatch.fnmatch(user.lower(), required_pattern.lower()):
                            self.logger.info(f"Admin access granted via hostmask: {user}")
                            return True
                        else:
                            self.logger.warning(f"Admin nick match but hostmask mismatch: {user} vs {required_pattern}")
                            return False
                    else:
                        # Nick-only fallback
                        self.logger.warning(f"Admin access granted via nick-only (no hostmask configured): {user}")
                        return True
        
        return False
    
    def _handle_single_target_admin_command(self, args, usage_message_key, action_func, success_message_key, nick, channel):
        """Helper for admin commands that target a single player"""
        if not args:
            message = self.messages.get(usage_message_key)
            self.send_message(channel, message)
            return False
        
        target = args[0].lower()
        player = self.db.get_player(target)
        action_func(player)
        
        message = self.messages.get(success_message_key, target=target, admin=nick)
        self.send_message(channel, message)
        self.db.save_database()
        return True
    
    def setup_signal_handlers(self):
        """Setup signal handlers for immediate shutdown"""
        def signal_handler(signum, _frame):
            signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            self.logger.info(f"🛑 Received {signal_name} (Ctrl+C), shutting down immediately...")
            self.shutdown_requested = True
            try:
                # Get the current event loop and cancel all tasks
                loop = asyncio.get_running_loop()
                tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in tasks:
                    task.cancel()
                self.logger.info(f"🔄 Cancelled {len(tasks)} running tasks")
            except Exception as e:
                self.logger.error(f"Error cancelling tasks: {e}")
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def connect(self):
        """Connect to IRC server with comprehensive error handling"""
        max_retries = self.get_config('connection.max_retries', 3) or 3
        retry_delay = self.get_config('connection.retry_delay', 5) or 5
        
        for attempt in range(max_retries):
            try:
                ssl_context = None
                if self.get_config('connection.ssl', False):
                    ssl_context = ssl.create_default_context()
                    # Add SSL context configuration for better compatibility
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                
                server = self.get_config('connection.server', 'irc.libera.chat')
                port = self.get_config('connection.port', 6667)
                self.logger.info(f"Attempting to connect to {server}:{port} (attempt {attempt + 1}/{max_retries})")
                
                self.reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        server, 
                        port,
                        ssl=ssl_context
                    ),
                    timeout=self.get_config('connection.timeout', 30) or 30.0  # Connection timeout from config
                )
                
                self.logger.info(f"✅ Successfully connected to {server}:{port}")
                return
                
            except asyncio.TimeoutError:
                self.logger.error(f"Connection attempt {attempt + 1} timed out after 30 seconds")
            except ssl.SSLError as e:
                self.logger.error(f"SSL error on attempt {attempt + 1}: {e}")
            except OSError as e:
                self.logger.error(f"Network error on attempt {attempt + 1}: {e}")
            except Exception as e:
                self.logger.error(f"Unexpected connection error on attempt {attempt + 1}: {e}")
            
            if attempt < max_retries - 1:
                self.logger.info(f"Retrying connection in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
        
        # If all attempts failed
        raise ConnectionError(f"Failed to connect after {max_retries} attempts")
    
    def send_raw(self, msg):
        """Send raw IRC message with error handling"""
        if not self.writer or self.writer.is_closing():
            self.logger.warning(f"Cannot send message: connection not available")
            return False
            
        try:
            encoded_msg = f"{msg}\r\n".encode('utf-8', errors='replace')
            self.writer.write(encoded_msg)
            return True
        except ConnectionResetError:
            self.logger.error("Connection reset while sending message")
            return False
        except BrokenPipeError:
            self.logger.error("Broken pipe while sending message")
            return False
        except OSError as e:
            self.logger.error(f"Network error while sending message: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error while sending message: {e}")
            return False
    
    def send_message(self, target, msg):
        """Send message to target (channel or user) with error handling"""
        if not isinstance(target, str) or not isinstance(msg, str):
            self.logger.warning(f"Invalid message parameters: target={type(target)}, msg={type(msg)}")
            return False
            
        # Sanitize message to prevent IRC injection
        try:
            # Remove potential IRC control characters
            sanitized_msg = msg.replace('\r', '').replace('\n', ' ').strip()
            if not sanitized_msg:
                return False
                
            return self.send_raw(f"PRIVMSG {target} :{sanitized_msg}")
        except Exception as e:
            self.logger.error(f"Error sanitizing/sending message: {e}")
            return False
    
    async def send_server_password(self):
        """Send server password if configured (must be sent immediately after connection)"""
        password = self.get_config('connection.password')
        if password and password != "your_iline_password_here":
            self.logger.info("🔐 Sending server password")
            self.send_raw(f"PASS {password}")
            return True
        return False

    async def register_user(self):
        """Register user with IRC server (NICK/USER commands)"""
        nick = self.get_config('connection.nick', 'DuckHunt')
        self.send_raw(f"NICK {nick}")
        self.send_raw(f"USER {nick} 0 * :{nick}")
    
    async def handle_message(self, prefix, command, params, trailing):
        """Handle incoming IRC messages with comprehensive error handling"""
        try:
            # Validate input parameters
            if not isinstance(command, str):
                self.logger.warning(f"Invalid command type: {type(command)}")
                return
                
            if params is None:
                params = []
            elif not isinstance(params, list):
                self.logger.warning(f"Invalid params type: {type(params)}")
                params = []
                
            if trailing is None:
                trailing = ""
            elif not isinstance(trailing, str):
                self.logger.warning(f"Invalid trailing type: {type(trailing)}")
                trailing = str(trailing)
        
            # Handle SASL-related messages
            if command == "CAP":
                await self.sasl_handler.handle_cap_response(params, trailing)
                return
                
            elif command == "AUTHENTICATE":
                await self.sasl_handler.handle_authenticate_response(params)
                return
                
            elif command in ["903", "904", "905", "906", "907", "908"]:
                await self.sasl_handler.handle_sasl_result(command, params, trailing)
                return
            
            elif command == "001":  # Welcome message
                self.registered = True
                self.logger.info("Successfully registered with IRC server")
                
                # Join channels
                channels = self.get_config('connection.channels', []) or []
                for channel in channels:
                    try:
                        self.send_raw(f"JOIN {channel}")
                        self.channels_joined.add(channel)
                    except Exception as e:
                        self.logger.error(f"Error joining channel {channel}: {e}")
            
            elif command == "PRIVMSG":
                if len(params) >= 1:
                    target = params[0]
                    message = trailing or ""
                    await self.handle_command(prefix, target, message)
            
            elif command == "PING":
                try:
                    self.send_raw(f"PONG :{trailing}")
                except Exception as e:
                    self.logger.error(f"Error responding to PING: {e}")
                    
        except Exception as e:
            self.logger.error(f"Critical error in handle_message: {e}")
            # Continue execution to prevent bot crashes
    
    async def handle_command(self, user, channel, message):
        """Handle bot commands with comprehensive error handling"""
        try:
            # Validate inputs
            if not isinstance(message, str) or not message.startswith('!'):
                return
            
            if not isinstance(user, str) or not isinstance(channel, str):
                self.logger.warning(f"Invalid user/channel types: {type(user)}, {type(channel)}")
                return
            
            # Safely parse command
            try:
                parts = message[1:].split()
            except Exception as e:
                self.logger.warning(f"Error parsing command '{message}': {e}")
                return
                
            if not parts:
                return
            
            cmd = parts[0].lower()
            args = parts[1:] if len(parts) > 1 else []
            
            # Safely extract nick
            try:
                nick = user.split('!')[0] if '!' in user else user
                if not nick:
                    self.logger.warning(f"Empty nick from user string: {user}")
                    return
            except Exception as e:
                self.logger.error(f"Error extracting nick from '{user}': {e}")
                return
            
            # Get player data safely
            try:
                player = self.db.get_player(nick)
                if player is None:
                    player = {}
            except Exception as e:
                self.logger.error(f"Error getting player data for {nick}: {e}")
                player = {}
            
            # Track activity for channel membership validation
            if channel.startswith('#'):  # Only track for channel messages
                player['last_activity_channel'] = channel
                player['last_activity_time'] = time.time()
            
            # Check if player is ignored (unless it's an admin)
            try:
                if player.get('ignored', False) and not self.is_admin(user):
                    return
            except Exception as e:
                self.logger.error(f"Error checking admin/ignore status: {e}")
                return
            
            # Handle commands with individual error isolation
            await self._execute_command_safely(cmd, nick, channel, player, args, user)
            
        except Exception as e:
            self.logger.error(f"Critical error in handle_command: {e}")
            # Continue execution to prevent bot crashes
    
    async def _execute_command_safely(self, cmd, nick, channel, player, args, user):
        """Execute individual commands with error isolation"""
        try:
            if cmd == "bang":
                await self.handle_bang(nick, channel, player)
            elif cmd == "bef" or cmd == "befriend":
                await self.handle_bef(nick, channel, player)
            elif cmd == "reload":
                await self.handle_reload(nick, channel, player)
            elif cmd == "shop":
                await self.handle_shop(nick, channel, player, args)
            elif cmd == "duckstats":
                await self.handle_duckstats(nick, channel, player)
            elif cmd == "topduck":
                await self.handle_topduck(nick, channel)
            elif cmd == "use":
                await self.handle_use(nick, channel, player, args)
            elif cmd == "duckhelp":
                await self.handle_duckhelp(nick, channel, player)
            elif cmd == "rearm" and self.is_admin(user):
                await self.handle_rearm(nick, channel, args)
            elif cmd == "disarm" and self.is_admin(user):
                await self.handle_disarm(nick, channel, args)
            elif cmd == "ignore" and self.is_admin(user):
                await self.handle_ignore(nick, channel, args)
            elif cmd == "unignore" and self.is_admin(user):
                await self.handle_unignore(nick, channel, args)
            elif cmd == "ducklaunch" and self.is_admin(user):
                await self.handle_ducklaunch(nick, channel, args)
        except Exception as e:
            self.logger.error(f"Error executing command '{cmd}' for user {nick}: {e}")
            # Send a generic error message to the user to indicate something went wrong
            try:
                error_msg = f"{nick} > An error occurred processing your command. Please try again."
                self.send_message(channel, error_msg)
            except Exception as send_error:
                self.logger.error(f"Error sending error message: {send_error}")
    
    def validate_target_player(self, target_nick, channel):
        """
        Validate that a target player is a valid hunter
        Returns (is_valid, player_data, error_message)
        
        TODO: Implement proper channel membership tracking to ensure 
        the target is actually present in the channel
        """
        if not target_nick:
            return False, None, "No target specified"
        
        # Normalize the nickname
        target_nick = target_nick.lower().strip()
        
        # Check if target_nick is empty after normalization
        if not target_nick:
            return False, None, "Invalid target nickname"
        
        # Check if player exists in database
        player = self.db.get_player(target_nick)
        if not player:
            return False, None, f"Player '{target_nick}' not found. They need to participate in the game first."
        
        # Check if player has any game activity (basic validation they're a hunter)
        has_activity = (
            player.get('xp', 0) > 0 or 
            player.get('shots_fired', 0) > 0 or 
            'current_ammo' in player or
            'magazines' in player
        )
        
        if not has_activity:
            return False, None, f"Player '{target_nick}' has no hunting activity. They may not be an active hunter."
        
        # Check if player is currently in the channel (for channel messages only)
        if channel.startswith('#'):
            is_in_channel = self.is_user_in_channel_sync(target_nick, channel)
            if not is_in_channel:
                return False, None, f"Player '{target_nick}' is not currently in {channel}."
        
        return True, player, None
    
    def is_user_in_channel_sync(self, nick, channel):
        """
        Check if a user is likely in the channel based on recent activity (synchronous version)
        
        This is a practical approach that doesn't require complex IRC response parsing.
        We assume if someone has been active recently, they're still in the channel.
        """
        try:
            player = self.db.get_player(nick)
            if not player:
                return False
            
            # Check if they've been active in this channel recently
            last_activity_channel = player.get('last_activity_channel')
            last_activity_time = player.get('last_activity_time', 0)
            current_time = time.time()
            
            # If they were active in this channel within the last 30 minutes, assume they're still here
            if (last_activity_channel == channel and 
                current_time - last_activity_time < 1800):  # 30 minutes
                return True
            
            # If no recent activity in this channel, they might not be here
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking channel membership for {nick} in {channel}: {e}")
            return True  # Default to allowing the command if we can't check
    
    async def handle_bang(self, nick, channel, player):
        """Handle !bang command"""
        result = self.game.shoot_duck(nick, channel, player)
        message = self.messages.get(result['message_key'], **result['message_args'])
        self.send_message(channel, message)
    
    async def handle_bef(self, nick, channel, player):
        """Handle !bef (befriend) command"""
        result = self.game.befriend_duck(nick, channel, player)
        message = self.messages.get(result['message_key'], **result['message_args'])
        self.send_message(channel, message)
    
    async def handle_reload(self, nick, channel, player):
        """Handle !reload command"""
        result = self.game.reload_gun(nick, channel, player)
        message = self.messages.get(result['message_key'], **result['message_args'])
        self.send_message(channel, message)
    
    async def handle_shop(self, nick, channel, player, args=None):
        """Handle !shop command"""
        # Handle buying: !shop buy <item_id> [target] or !shop <item_id> [target]
        if args and len(args) >= 1:
            # Check for "buy" subcommand or direct item ID
            start_idx = 0
            if args[0].lower() == "buy":
                start_idx = 1
            
            if len(args) > start_idx:
                try:
                    item_id = int(args[start_idx])
                    target_nick = args[start_idx + 1] if len(args) > start_idx + 1 else None
                    
                    # If no target specified, store in inventory. If target specified, use immediately.
                    store_in_inventory = target_nick is None
                    await self.handle_shop_buy(nick, channel, player, item_id, target_nick, store_in_inventory)
                    return
                except (ValueError, IndexError):
                    message = self.messages.get('shop_buy_usage', nick=nick)
                    self.send_message(channel, message)
                    return
        
        # Display shop items using ShopManager
        shop_text = self.shop.get_shop_display(player, self.messages)
        self.send_message(channel, shop_text)
    
    async def handle_shop_buy(self, nick, channel, player, item_id, target_nick=None, store_in_inventory=False):
        """Handle buying an item from the shop"""
        target_player = None
        
        # Get target player if specified and validate they're in channel
        if target_nick:
            # Use the same validation as other commands
            is_valid, target_player, error_msg = self.validate_target_player(target_nick, channel)
            if not is_valid:
                message = f"{nick} > {error_msg}"
                self.send_message(channel, message)
                return
        
        # Use ShopManager to handle the purchase
        result = self.shop.purchase_item(player, item_id, target_player, store_in_inventory)
        
        if not result["success"]:
            # Handle different error types
            if result["error"] == "invalid_id":
                message = self.messages.get('shop_buy_invalid_id', nick=nick)
            elif result["error"] == "insufficient_xp":
                message = self.messages.get('shop_buy_insufficient_xp', 
                                          nick=nick,
                                          item_name=result["item_name"],
                                          price=result["price"],
                                          current_xp=result["current_xp"])
            elif result["error"] == "target_required":
                message = f"{nick} > {result['message']}"
            elif result["error"] == "invalid_storage":
                message = f"{nick} > {result['message']}"
            else:
                message = f"{nick} > Error: {result['message']}"
            
            self.send_message(channel, message)
            return
        
        # Purchase successful
        if result.get("stored_in_inventory"):
            message = f"{nick} > Successfully purchased {result['item_name']} for {result['price']} XP! Stored in inventory (x{result['inventory_count']}). Remaining XP: {result['remaining_xp']}"
        elif result.get("target_affected"):
            message = f"{nick} > Used {result['item_name']} on {target_nick}! Remaining XP: {result['remaining_xp']}"
        else:
            message = self.messages.get('shop_buy_success',
                                      nick=nick,
                                      item_name=result["item_name"],
                                      price=result["price"],
                                      remaining_xp=result["remaining_xp"])
        
        self.send_message(channel, message)
        self.db.save_database()
    
    async def handle_duckstats(self, nick, channel, player):
        """Handle !duckstats command"""
        # Apply color formatting
        bold = self.messages.messages.get('colours', {}).get('bold', '')
        reset = self.messages.messages.get('colours', {}).get('reset', '')
        green = self.messages.messages.get('colours', {}).get('green', '')
        blue = self.messages.messages.get('colours', {}).get('blue', '')
        yellow = self.messages.messages.get('colours', {}).get('yellow', '')
        red = self.messages.messages.get('colours', {}).get('red', '')
        
        # Get player level info
        level_info = self.levels.get_player_level_info(player)
        level = level_info['level']
        level_name = level_info['name']
        
        # Build stats message
        xp = player.get('xp', 0)
        ducks_shot = player.get('ducks_shot', 0)
        ducks_befriended = player.get('ducks_befriended', 0)
        accuracy = player.get('accuracy', self.get_config('player_defaults.accuracy', 75))
        
        # Calculate additional stats
        total_ducks_encountered = ducks_shot + ducks_befriended
        shots_missed = player.get('shots_missed', 0)
        total_shots = ducks_shot + shots_missed
        hit_rate = round((ducks_shot / total_shots * 100) if total_shots > 0 else 0, 1)
        
        # Get level progression info
        xp_needed = level_info.get('needed_for_next', 0)
        next_level_name = level_info.get('next_level_name', 'Max Level')
        if xp_needed > 0:
            xp_progress = f" (Need {xp_needed} XP for {next_level_name})"
        else:
            xp_progress = " (Max level reached!)"
        
        # Ammo info
        current_ammo = player.get('current_ammo', 0)
        magazines = player.get('magazines', 0)
        bullets_per_mag = player.get('bullets_per_magazine', 6)
        jam_chance = player.get('jam_chance', 0)
        
        # Gun status
        gun_status = "Armed" if not player.get('gun_confiscated', False) else "Confiscated"
        
        # Build compact stats message with subtle colors
        stats_parts = [
            f"Lv{level} {level_name}",
            f"{green}{xp}XP{reset}{xp_progress}",
            f"{ducks_shot} shot",
            f"{ducks_befriended} befriended",
            f"{accuracy}% accuracy",
            f"{hit_rate}% hit rate",
            f"{green if gun_status == 'Armed' else red}{gun_status}{reset}",
            f"{current_ammo}/{bullets_per_mag}|{magazines} mags",
            f"{jam_chance}% jam chance"
        ]
        
        # Add inventory if player has items
        inventory = player.get('inventory', {})
        if inventory:
            items = []
            for item_id, quantity in inventory.items():
                item = self.shop.get_item(int(item_id))
                if item:
                    items.append(f"{item['name']} x{quantity}")
            if items:
                stats_parts.append(f"Items: {', '.join(items)}")
        
        # Add temporary effects if any
        temp_effects = player.get('temporary_effects', [])
        if temp_effects:
            active_effects = [effect.get('name', 'Unknown Effect') for effect in temp_effects if isinstance(effect, dict)]
            if active_effects:
                stats_parts.append(f"Effects:{','.join(active_effects)}")
        
        # Send as one compact message
        stats_message = f"{bold}{nick}{reset}: {' | '.join(stats_parts)}"
        self.send_message(channel, stats_message)
    
    async def handle_topduck(self, nick, channel):
        """Handle !topduck command - show leaderboards"""
        try:
            # Apply color formatting
            bold = self.messages.messages.get('colours', {}).get('bold', '')
            reset = self.messages.messages.get('colours', {}).get('reset', '')
            
            # Get top 3 by XP
            top_xp = self.db.get_leaderboard('xp', 3)
            
            # Get top 3 by ducks shot
            top_ducks = self.db.get_leaderboard('ducks_shot', 3)
            
            # Format XP leaderboard as single line
            if top_xp:
                xp_rankings = []
                for i, (player_nick, xp) in enumerate(top_xp, 1):
                    medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
                    xp_rankings.append(f"{medal}{player_nick}:{xp}XP")
                xp_line = f"🏆 {bold}Top XP{reset} " + " | ".join(xp_rankings)
                self.send_message(channel, xp_line)
            else:
                self.send_message(channel, "🏆 No XP data available yet!")
            
            # Format ducks shot leaderboard as single line
            if top_ducks:
                duck_rankings = []
                for i, (player_nick, ducks) in enumerate(top_ducks, 1):
                    medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
                    duck_rankings.append(f"{medal}{player_nick}:{ducks}")
                duck_line = f"🦆 {bold}Top Hunters{reset} " + " | ".join(duck_rankings)
                self.send_message(channel, duck_line)
            else:
                self.send_message(channel, "🦆 No duck hunting data available yet!")
                
        except Exception as e:
            self.logger.error(f"Error in handle_topduck: {e}")
            self.send_message(channel, f"{nick} > Error retrieving leaderboard data.")
    
    async def handle_duckhelp(self, nick, channel, _player):
        """Handle !duckhelp command"""
        help_lines = [
            self.messages.get('help_header'),
            self.messages.get('help_user_commands'),
            self.messages.get('help_help_command')
        ]
        
        # Add admin commands if user is admin
        if self.is_admin(f"{nick}!user@host"):
            help_lines.append(self.messages.get('help_admin_commands'))
        
        for line in help_lines:
            self.send_message(channel, line)
    
    async def handle_use(self, nick, channel, player, args):
        """Handle !use command"""
        if not args:
            message = f"{nick} > Usage: !use <item_id> [target]"
            self.send_message(channel, message)
            return
        
        try:
            item_id = int(args[0])
            target_nick = args[1] if len(args) > 1 else None
            target_player = None
            
            # Get target player if specified
            if target_nick:
                is_valid, target_player, error_msg = self.validate_target_player(target_nick, channel)
                if not is_valid:
                    message = f"{nick} > {error_msg}"
                    self.send_message(channel, message)
                    return
            
            # Use item from inventory
            result = self.shop.use_inventory_item(player, item_id, target_player)
            
            if not result["success"]:
                message = f"{nick} > {result['message']}"
            else:
                # Handle specific item effect messages
                effect = result.get('effect', {})
                effect_type = effect.get('type', '')
                
                if effect_type == 'attract_ducks':
                    # Use specific message for bread
                    message = self.messages.get('use_attract_ducks', 
                        nick=nick, 
                        spawn_multiplier=effect.get('spawn_multiplier', 2.0),
                        duration=effect.get('duration', 10)
                    )
                elif effect_type == 'insurance':
                    # Use specific message for insurance
                    message = self.messages.get('use_insurance',
                        nick=nick,
                        duration=effect.get('duration', 24)
                    )
                elif effect_type == 'buy_gun_back':
                    # Use specific message for buying gun back
                    if effect.get('restored', False):
                        message = self.messages.get('use_buy_gun_back', nick=nick,
                            ammo_restored=effect.get('ammo_restored', 0),
                            magazines_restored=effect.get('magazines_restored', 0))
                    else:
                        message = self.messages.get('use_buy_gun_back_not_needed', nick=nick)
                elif effect_type == 'splash_water':
                    # Use specific message for water splash
                    message = self.messages.get('use_splash_water', 
                        nick=nick, 
                        target_nick=target_nick,
                        duration=effect.get('duration', 5))
                elif effect_type == 'dry_clothes':
                    # Use specific message for dry clothes
                    if effect.get('was_wet', False):
                        message = self.messages.get('use_dry_clothes', nick=nick)
                    else:
                        message = self.messages.get('use_dry_clothes_not_needed', nick=nick)
                elif result.get("target_affected"):
                    # Check if it's a gift (beneficial effect to target)
                    if effect.get('is_gift', False):
                        # Use specific gift messages based on item type
                        if effect_type == 'ammo':
                            message = self.messages.get('gift_ammo', 
                                nick=nick, target_nick=target_nick, amount=effect.get('amount', 1))
                        elif effect_type == 'magazine':
                            message = self.messages.get('gift_magazine', 
                                nick=nick, target_nick=target_nick)
                        elif effect_type == 'clean_gun':
                            message = self.messages.get('gift_gun_brush', 
                                nick=nick, target_nick=target_nick)
                        elif effect_type == 'insurance':
                            message = self.messages.get('gift_insurance', 
                                nick=nick, target_nick=target_nick)
                        elif effect_type == 'dry_clothes':
                            message = self.messages.get('gift_dry_clothes', 
                                nick=nick, target_nick=target_nick)
                        elif effect_type == 'buy_gun_back':
                            message = self.messages.get('gift_buy_gun_back', 
                                nick=nick, target_nick=target_nick)
                        else:
                            message = f"{nick} > Gave {result['item_name']} to {target_nick}!"
                    else:
                        message = f"{nick} > Used {result['item_name']} on {target_nick}!"
                else:
                    message = f"{nick} > Used {result['item_name']}!"
                
                # Add remaining count if any (not for bread message which has its own format)
                if effect_type != 'attract_ducks' and result.get("remaining_in_inventory", 0) > 0:
                    message += f" ({result['remaining_in_inventory']} remaining)"
            
            self.send_message(channel, message)
            self.db.save_database()
            
        except ValueError:
            message = f"{nick} > Invalid item ID. Use !duckstats to see your items."
            self.send_message(channel, message)
    
    async def handle_rearm(self, nick, channel, args):
        """Handle !rearm command (admin only) - supports private messages"""
        is_private_msg = not channel.startswith('#')
        
        if args:
            target_nick = args[0]
            
        # Validate target player (only for channel messages, skip validation if targeting self)
        player = None
        if not is_private_msg:
            # If targeting self, skip validation since the user is obviously in the channel
            if target_nick.lower() == nick.lower():
                target_nick = target_nick.lower()
                player = self.db.get_player(target_nick)
                if player is None:
                    player = self.db.create_player(target_nick)
                    self.db.players[target_nick] = player
            else:
                is_valid, player, error_msg = self.validate_target_player(target_nick, channel)
                if not is_valid:
                    message = f"{nick} > {error_msg}"
                    self.send_message(channel, message)
                    return
                # Ensure player is properly stored in database
                target_nick = target_nick.lower()
                if target_nick not in self.db.players:
                    self.db.players[target_nick] = player
        else:
            # For private messages, allow targeting any nick (admin override)
            target_nick = target_nick.lower()
            player = self.db.get_player(target_nick)
            if player is None:
                # Create new player data for the target
                player = self.db.create_player(target_nick)
                self.db.players[target_nick] = player
        
        # At this point player is guaranteed to be not None
        if player is not None:
            player['gun_confiscated'] = False            # Update magazines based on player level
            self.levels.update_player_magazines(player)
            player['current_ammo'] = player.get('bullets_per_magazine', 6)
            # Player data is already modified in place and will be saved by save_database()
            
            if is_private_msg:
                message = f"{nick} > Rearmed {target_nick}"
            else:
                message = self.messages.get('admin_rearm_player', target=target_nick, admin=nick)
            self.send_message(channel, message)
        else:
            if is_private_msg:
                self.send_message(channel, f"{nick} > Usage: !rearm <player>")
                return
            
            # Rearm the admin themselves (only in channels)
            player = self.db.get_player(nick)
            if player is None:
                player = {}
            player['gun_confiscated'] = False
            
            # Update magazines based on admin's level
            self.levels.update_player_magazines(player)
            player['current_ammo'] = player.get('bullets_per_magazine', 6)
            
            message = self.messages.get('admin_rearm_self', admin=nick)
            self.send_message(channel, message)
        
        self.db.save_database()

    async def handle_disarm(self, nick, channel, args):
        """Handle !disarm command (admin only) - supports private messages"""
        is_private_msg = not channel.startswith('#')
        
        if not args:
            if is_private_msg:
                self.send_message(channel, f"{nick} > Usage: !disarm <player>")
            else:
                message = self.messages.get('usage_disarm')
                self.send_message(channel, message)
            return
        
        target_nick = args[0]
        
        # Validate target player (only for channel messages, skip validation if targeting self)
        player = None
        if not is_private_msg:
            # If targeting self, skip validation since the user is obviously in the channel
            if target_nick.lower() == nick.lower():
                target_nick = target_nick.lower()
                player = self.db.get_player(target_nick)
                if player is None:
                    player = self.db.create_player(target_nick)
                    self.db.players[target_nick] = player
            else:
                is_valid, player, error_msg = self.validate_target_player(target_nick, channel)
                if not is_valid:
                    message = f"{nick} > {error_msg}"
                    self.send_message(channel, message)
                    return
                # Ensure player is properly stored in database
                target_nick = target_nick.lower()
                if target_nick not in self.db.players:
                    self.db.players[target_nick] = player
        else:
            # For private messages, allow targeting any nick (admin override)
            target_nick = target_nick.lower()
            player = self.db.get_player(target_nick)
            if player is None:
                # Create new player data for the target
                player = self.db.create_player(target_nick)
                self.db.players[target_nick] = player
        
        # At this point player is guaranteed to be not None
        if player is not None:
            player['gun_confiscated'] = True
        # Player data is already modified in place and will be saved by save_database()
        
        if is_private_msg:
            message = f"{nick} > Disarmed {target_nick}"
        else:
            message = self.messages.get('admin_disarm', target=target_nick, admin=nick)
        
        self.send_message(channel, message)
        self.db.save_database()
    
    async def handle_ignore(self, nick, channel, args):
        """Handle !ignore command (admin only) - supports private messages"""
        is_private_msg = not channel.startswith('#')
        
        if not args:
            if is_private_msg:
                self.send_message(channel, f"{nick} > Usage: !ignore <player>")
            else:
                message = self.messages.get('usage_ignore')
                self.send_message(channel, message)
            return
        
        target = args[0].lower()
        player = self.db.get_player(target)
        if player is None:
            player = {}
        player['ignored'] = True
        
        if is_private_msg:
            message = f"{nick} > Ignored {target}"
        else:
            message = self.messages.get('admin_ignore', target=target, admin=nick)
        
        self.send_message(channel, message)
        self.db.save_database()
    
    async def handle_unignore(self, nick, channel, args):
        """Handle !unignore command (admin only) - supports private messages"""
        is_private_msg = not channel.startswith('#')
        
        if not args:
            if is_private_msg:
                self.send_message(channel, f"{nick} > Usage: !unignore <player>")
            else:
                message = self.messages.get('usage_unignore')
                self.send_message(channel, message)
            return
        
        target = args[0].lower()
        player = self.db.get_player(target)
        if player is None:
            player = {}
        player['ignored'] = False
        
        if is_private_msg:
            message = f"{nick} > Unignored {target}"
        else:
            message = self.messages.get('admin_unignore', target=target, admin=nick)
        
        self.send_message(channel, message)
        self.db.save_database()

    async def handle_ducklaunch(self, nick, channel, args):
        """Handle !ducklaunch command (admin only) - supports duck type specification"""
        # For private messages, need to specify a target channel
        target_channel = channel
        is_private_msg = not channel.startswith('#')
        
        if is_private_msg:
            if not args:
                self.send_message(channel, f"{nick} > Usage: !ducklaunch [channel] [duck_type] - duck_type can be: normal, golden, fast")
                return
            target_channel = args[0]
            duck_type_arg = args[1] if len(args) > 1 else "normal"
        else:
            duck_type_arg = args[0] if args else "normal"
        
        # Validate target channel
        if target_channel not in self.channels_joined:
            if is_private_msg:
                self.send_message(channel, f"{nick} > Channel {target_channel} is not available for duckhunt")
            else:
                message = self.messages.get('admin_ducklaunch_not_enabled')
                self.send_message(channel, message)
            return
        
        # Validate duck type
        duck_type_arg = duck_type_arg.lower()
        valid_types = ["normal", "golden", "fast"]
        if duck_type_arg not in valid_types:
            self.send_message(channel, f"{nick} > Invalid duck type '{duck_type_arg}'. Valid types: {', '.join(valid_types)}")
            return
        
        # Force spawn the specified duck type
        import time
        import random
        
        if target_channel not in self.game.ducks:
            self.game.ducks[target_channel] = []
        
        # Create duck based on specified type
        current_time = time.time()
        duck_id = f"{duck_type_arg}_duck_{int(current_time)}_{random.randint(1000, 9999)}"
        
        if duck_type_arg == "golden":
            min_hp_val = self.get_config('duck_types.golden.min_hp', 3)
            max_hp_val = self.get_config('duck_types.golden.max_hp', 5)
            min_hp = int(min_hp_val) if min_hp_val is not None else 3
            max_hp = int(max_hp_val) if max_hp_val is not None else 5
            hp = random.randint(min_hp, max_hp)
            duck = {
                'id': duck_id,
                'spawn_time': current_time,
                'channel': target_channel,
                'duck_type': 'golden',
                'max_hp': hp,
                'current_hp': hp
            }
        else:
            # Both normal and fast ducks have 1 HP
            duck = {
                'id': duck_id,
                'spawn_time': current_time,
                'channel': target_channel,
                'duck_type': duck_type_arg,
                'max_hp': 1,
                'current_hp': 1
            }
        
        self.game.ducks[target_channel].append(duck)
        duck_message = self.messages.get('duck_spawn')
        
        # Send duck spawn message to target channel
        self.send_message(target_channel, duck_message)
        
        # Send confirmation to admin (either in channel or private message)
        if is_private_msg:
            self.send_message(channel, f"{nick} > Launched {duck_type_arg} duck in {target_channel}")
        else:
            # In channel, only send the duck message (no admin notification to avoid spam)
            pass
    
    
    async def message_loop(self):
        """Main message processing loop with comprehensive error handling"""
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        try:
            while not self.shutdown_requested and self.reader:
                try:
                    # Use a timeout on readline to make it more responsive to shutdown
                    line = await asyncio.wait_for(self.reader.readline(), timeout=1.0)
                    
                    # Reset error counter on successful read
                    consecutive_errors = 0
                    
                except asyncio.TimeoutError:
                    # Check shutdown flag and continue
                    continue
                except ConnectionResetError:
                    self.logger.error("Connection reset by peer")
                    break
                except OSError as e:
                    self.logger.error(f"Network error during read: {e}")
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        self.logger.error("Too many consecutive network errors, breaking message loop")
                        break
                    await asyncio.sleep(1)  # Brief delay before retry
                    continue
                except Exception as e:
                    self.logger.error(f"Unexpected error reading from stream: {e}")
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        self.logger.error("Too many consecutive read errors, breaking message loop")
                        break
                    continue
                    
                # Check if connection is closed
                if not line:
                    self.logger.info("Connection closed by server")
                    break
                
                # Safely decode with comprehensive error handling
                try:
                    line = line.decode('utf-8', errors='replace').strip()
                except (UnicodeDecodeError, AttributeError) as e:
                    self.logger.warning(f"Failed to decode message: {e}")
                    continue
                except Exception as e:
                    self.logger.error(f"Unexpected error decoding message: {e}")
                    continue
                    
                if not line:
                    continue
                
                # Process the message with full error isolation
                try:
                    prefix, command, params, trailing = parse_irc_message(line)
                    await self.handle_message(prefix, command, params, trailing)
                except ValueError as e:
                    self.logger.warning(f"Malformed IRC message ignored: {line[:100]}... Error: {e}")
                except Exception as e:
                    self.logger.error(f"Error processing message '{line[:100]}...': {e}")
                    # Continue processing other messages even if one fails
        
        except asyncio.CancelledError:
            self.logger.info("Message loop cancelled")
        except Exception as e:
            self.logger.error(f"Critical message loop error: {e}")
        finally:
            self.logger.info("Message loop ended")
    
    async def run(self):
        """Main bot loop with fast shutdown handling"""
        self.setup_signal_handlers()
        
        game_task = None
        message_task = None
        
        try:
            await self.connect()
            
            # Send server password immediately after connection (RFC requirement)
            await self.send_server_password()
            
            # Check if SASL should be used
            if self.sasl_handler.should_authenticate():
                await self.sasl_handler.start_negotiation()
            else:
                await self.register_user()
            
            # Start game loops
            game_task = asyncio.create_task(self.game.start_game_loops())
            message_task = asyncio.create_task(self.message_loop())
            
            self.logger.info("🦆 Bot is now running! Press Ctrl+C to stop.")
            # Wait for shutdown signal or task completion with frequent checks
            while not self.shutdown_requested:
                done, _pending = await asyncio.wait(
                    [game_task, message_task],
                    timeout=0.1,  # Check every 100ms for shutdown
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # If any task completed, break out
                if done:
                    break
                    break
            
            self.logger.info("🔄 Shutdown initiated, cleaning up...")
            
        except asyncio.CancelledError:
            self.logger.info("🛑 Main loop cancelled")
        except Exception as e:
            self.logger.error(f"❌ Bot error: {e}")
        finally:
            # Fast cleanup - cancel tasks immediately with short timeout
            tasks_to_cancel = [task for task in [game_task, message_task] if task and not task.done()]
            for task in tasks_to_cancel:
                task.cancel()
            
            # Wait briefly for tasks to cancel
            if tasks_to_cancel:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks_to_cancel, return_exceptions=True),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    self.logger.warning("⚠️ Task cancellation timed out")
            
            # Quick database save
            try:
                self.db.save_database()
                self.logger.info("💾 Database saved")
            except Exception as e:
                self.logger.error(f"❌ Error saving database: {e}")
            
            # Fast connection close
            await self._close_connection()
            
            self.logger.info("✅ Bot shutdown complete")
    
    async def _close_connection(self):
        """Close IRC connection with comprehensive error handling"""
        if not self.writer:
            return
            
        try:
            if not self.writer.is_closing():
                # Send quit message with timeout
                try:
                    quit_message = self.config.get('quit_message', 'DuckHunt Bot shutting down')
                    if self.send_raw(f"QUIT :{quit_message}"):
                        await asyncio.sleep(0.2)  # Brief wait for message to send
                except Exception as e:
                    self.logger.debug(f"Error sending quit message: {e}")
                
                # Close the writer
                try:
                    self.writer.close()
                    await asyncio.wait_for(self.writer.wait_closed(), timeout=2.0)
                except asyncio.TimeoutError:
                    self.logger.warning("⚠️ Connection close timed out - forcing close")
                except Exception as e:
                    self.logger.debug(f"Error during connection close: {e}")
                    
            self.logger.info("🔌 IRC connection closed")
            
        except Exception as e:
            self.logger.error(f"❌ Critical error closing connection: {e}")
        finally:
            # Ensure writer is cleared regardless of errors
            self.writer = None
            self.reader = None