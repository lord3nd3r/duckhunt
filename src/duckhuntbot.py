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
        
        self.db = DuckDB()
        self.game = DuckGame(self, self.db)
        self.messages = MessageManager()
        
        self.sasl_handler = SASLHandler(self, config)
        
        self.admins = [admin.lower() for admin in self.config.get('admins', ['colby'])]
        
        # Initialize shop manager
        shop_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shop.json')
        self.shop = ShopManager(shop_file)
        
        # Initialize level manager
        levels_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'levels.json')
        self.levels = LevelManager(levels_file)
        
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
        """Check if user is admin by nick only"""
        if '!' not in user:
            return False
        nick = user.split('!')[0].lower()
        return nick in self.admins
    
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
        max_retries = 3
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                ssl_context = None
                if self.config.get('ssl', False):
                    ssl_context = ssl.create_default_context()
                    # Add SSL context configuration for better compatibility
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                
                self.logger.info(f"Attempting to connect to {self.config['server']}:{self.config['port']} (attempt {attempt + 1}/{max_retries})")
                
                self.reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        self.config['server'], 
                        self.config['port'],
                        ssl=ssl_context
                    ),
                    timeout=30.0  # 30 second connection timeout
                )
                
                self.logger.info(f"✅ Successfully connected to {self.config['server']}:{self.config['port']}")
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
    
    async def register_user(self):
        """Register user with IRC server"""
        if self.config.get('password'):
            self.send_raw(f"PASS {self.config['password']}")
        
        self.send_raw(f"NICK {self.config['nick']}")
        self.send_raw(f"USER {self.config['nick']} 0 * :{self.config['nick']}")
    
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
                for channel in self.config.get('channels', []):
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
        
        # Get target player if specified
        if target_nick:
            target_player = self.db.get_player(target_nick)
            if not target_player:
                message = f"{nick} > Target player '{target_nick}' not found"
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
        
        # Get player level info
        level_info = self.levels.get_player_level_info(player)
        level = level_info['level']
        level_name = level_info['level_data']['name']
        
        # Build stats message
        xp = player.get('xp', 0)
        ducks_shot = player.get('ducks_shot', 0)
        ducks_befriended = player.get('ducks_befriended', 0)
        accuracy = player.get('accuracy', 65)
        
        # Ammo info
        current_ammo = player.get('current_ammo', 0)
        magazines = player.get('magazines', 0)
        bullets_per_mag = player.get('bullets_per_magazine', 6)
        
        # Gun status
        gun_status = "🔫 Armed" if not player.get('gun_confiscated', False) else "❌ Confiscated"
        
        stats_lines = [
            f"📊 {bold}Duck Hunt Stats for {nick}{reset}",
            f"🏆 Level {level}: {level_name}",
            f"⭐ XP: {xp}",
            f"🦆 Ducks Shot: {ducks_shot}",
            f"💚 Ducks Befriended: {ducks_befriended}",
            f"🎯 Accuracy: {accuracy}%",
            f"🔫 Status: {gun_status}",
            f"💀 Ammo: {current_ammo}/{bullets_per_mag} | Magazines: {magazines}"
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
                stats_lines.append(f"🎒 Inventory: {', '.join(items)}")
        
        # Send each line
        for line in stats_lines:
            self.send_message(channel, line)
    
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
                target_player = self.db.get_player(target_nick)
                if not target_player:
                    message = f"{nick} > Target player '{target_nick}' not found"
                    self.send_message(channel, message)
                    return
            
            # Use item from inventory
            result = self.shop.use_inventory_item(player, item_id, target_player)
            
            if not result["success"]:
                message = f"{nick} > {result['message']}"
            else:
                if result.get("target_affected"):
                    message = f"{nick} > Used {result['item_name']} on {target_nick}!"
                else:
                    message = f"{nick} > Used {result['item_name']}!"
                
                # Add remaining count if any
                if result.get("remaining_in_inventory", 0) > 0:
                    message += f" ({result['remaining_in_inventory']} remaining)"
            
            self.send_message(channel, message)
            self.db.save_database()
            
        except ValueError:
            message = f"{nick} > Invalid item ID. Use !duckstats to see your items."
            self.send_message(channel, message)
    
    async def handle_rearm(self, nick, channel, args):
        """Handle !rearm command (admin only)"""
        if args:
            target = args[0].lower()
            player = self.db.get_player(target)
            if player is None:
                player = {}
            player['gun_confiscated'] = False
            
            # Update magazines based on player level
            self.levels.update_player_magazines(player)
            player['current_ammo'] = player.get('bullets_per_magazine', 6)
            
            message = self.messages.get('admin_rearm_player', target=target, admin=nick)
            self.send_message(channel, message)
        else:
            # Rearm the admin themselves
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
        """Handle !disarm command (admin only)"""
        def disarm_player(player):
            player['gun_confiscated'] = True
        
        self._handle_single_target_admin_command(
            args, 'usage_disarm', disarm_player, 'admin_disarm', nick, channel
        )
    
    async def handle_ignore(self, nick, channel, args):
        """Handle !ignore command (admin only)"""
        def ignore_player(player):
            player['ignored'] = True
        
        self._handle_single_target_admin_command(
            args, 'usage_ignore', ignore_player, 'admin_ignore', nick, channel
        )
    
    async def handle_unignore(self, nick, channel, args):
        """Handle !unignore command (admin only)"""
        def unignore_player(player):
            player['ignored'] = False
        
        self._handle_single_target_admin_command(
            args, 'usage_unignore', unignore_player, 'admin_unignore', nick, channel
        )

    async def handle_ducklaunch(self, _nick, channel, _args):
        """Handle !ducklaunch command (admin only)"""
        if channel not in self.channels_joined:
            message = self.messages.get('admin_ducklaunch_not_enabled')
            self.send_message(channel, message)
            return
        
        # Force spawn a duck
        if channel not in self.game.ducks:
            self.game.ducks[channel] = []
        self.game.ducks[channel].append({"spawn_time": time.time()})
        duck_message = self.messages.get('duck_spawn')
        
        # Only send the duck spawn message, no admin notification
        self.send_message(channel, duck_message)
    
    
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