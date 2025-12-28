import asyncio
import os
import ssl
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
from .error_handling import ErrorRecovery, HealthChecker, sanitize_user_input, safe_format_message


class DuckHuntBot:
    def __init__(self, config):
        self.config = config
        self.logger = setup_logger("DuckHuntBot")
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.registered = False
        self.channels_joined = set()
        # Track requested joins so we can report success/failure.
        # channel -> requester nick (or None for startup/rejoin)
        self.pending_joins = {}
        self.shutdown_requested = False
        self.rejoin_attempts = {}  # Track rejoin attempts per channel
        self.rejoin_tasks = {}     # Track active rejoin tasks
        
        self.logger.info("🤖 Initializing DuckHunt Bot components...")
        
        # Initialize error recovery systems
        self.error_recovery = ErrorRecovery()
        self.health_checker = HealthChecker(check_interval=60.0)
        
        self.db = DuckDB(bot=self)
        self.game = DuckGame(self, self.db)
        self.messages = MessageManager()
        
        self.sasl_handler = SASLHandler(self, config)

        # Config file path for persisting runtime config changes (e.g., admin join/leave).
        self.config_file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.json')
        
        # Set up health checks
        self._setup_health_checks()
        
        admins_list = self.get_config('admins', ['colby']) or ['colby']
        self.admins = [admin.lower() for admin in admins_list]
        self.logger.info(f"Configured {len(self.admins)} admin(s): {', '.join(self.admins)}")
        
        levels_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'levels.json')
        self.levels = LevelManager(levels_file)
        
        shop_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'shop.json')
        self.shop = ShopManager(shop_file, self.levels)
    
    def _setup_health_checks(self):
        """Set up health monitoring checks"""
        try:
            # Database health check
            self.health_checker.add_check(
                'database',
                lambda: self.db is not None and sum(1 for _ in self.db.iter_all_players()) >= 0,
                critical=True
            )
            
            # IRC connection health check
            self.health_checker.add_check(
                'irc_connection',
                lambda: self.writer is not None and not self.writer.is_closing(),
                critical=True
            )
            
            # Message system health check
            self.health_checker.add_check(
                'messages',
                lambda: self.messages is not None and len(self.messages.messages) > 0,
                critical=False
            )
            
            self.logger.debug("Health checks configured")
        except Exception as e:
            self.logger.error(f"Error setting up health checks: {e}")
        
    def get_config(self, path, default=None):
        keys = path.split('.')
        value = self.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def _channel_key(self, channel: str) -> str:
        """Normalize channel names for internal comparisons (IRC channels are case-insensitive)."""
        if not isinstance(channel, str):
            return ""
        channel = channel.strip()
        if channel.startswith('#') or channel.startswith('&'):
            return channel.lower()
        return channel
    
    def is_admin(self, user):
        if '!' not in user:
            return False
        
        nick = user.split('!')[0].lower()
        
        admin_config = self.get_config('admins', [])
        if not isinstance(admin_config, list):
            admin_config = []
        
        for admin_entry in admin_config:
            if isinstance(admin_entry, str):
                if admin_entry.lower() == nick:
                    self.logger.warning(f"Admin access granted via nick-only authentication: {user}")
                    return True
            elif isinstance(admin_entry, dict):
                if admin_entry.get('nick', '').lower() == nick:
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
        channel_ctx = channel if isinstance(channel, str) and channel.startswith('#') else None
        player = self.db.get_player(target, channel_ctx)
        action_func(player)
        
        message = self.messages.get(success_message_key, target=target, admin=nick)
        self.send_message(channel, message)
        self.db.save_database()
        return True

    def _get_admin_target_player(self, nick, channel, target_nick):
        """
        Helper method to get target player for admin commands with validation.
        Returns (player, error_message) - if error_message is not None, command should return early.
        """
        is_private_msg = not channel.startswith('#')
        channel_ctx = channel if isinstance(channel, str) and channel.startswith('#') else None
        
        if not is_private_msg:
            if target_nick.lower() == nick.lower():
                target_nick = target_nick.lower()
                player = self.db.get_player(target_nick, channel_ctx)
                return player, None
            else:
                is_valid, player, error_msg = self.validate_target_player(target_nick, channel)
                if not is_valid:
                    return None, error_msg
                return player, None
        else:
            target_nick = target_nick.lower()
            player = self.db.get_player(target_nick, channel_ctx)
            return player, None

    def _get_validated_target_player(self, nick, channel, target_nick):
        """
        Helper method to validate and get target player for regular commands.
        Returns (player, None) on success or (None, error_message) on failure.
        """
        if target_nick:
            is_valid, target_player, error_msg = self.validate_target_player(target_nick, channel)
            if not is_valid:
                return None, f"{nick} > {error_msg}"
            return target_player, None
        return None, None
    
    def setup_signal_handlers(self):
        """Setup signal handlers for immediate shutdown"""
        def signal_handler(signum, _frame):
            signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            self.logger.info(f"🛑 Received {signal_name} (Ctrl+C), shutting down immediately...")
            self.shutdown_requested = True
            try:
                loop = asyncio.get_running_loop()
                tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in tasks:
                    task.cancel()
                self.logger.info(f"Cancelled {len(tasks)} running tasks")
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
                    timeout=self.get_config('connection.timeout', 30) or 30.0
                )
                
                self.logger.info(f"Successfully connected to {server}:{port}")
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
                retry_delay *= 2
        
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
    
    async def schedule_rejoin(self, channel):
        """Schedule automatic rejoin attempts for a channel after being kicked"""
        try:
            # Cancel any existing rejoin task for this channel
            if channel in self.rejoin_tasks:
                self.rejoin_tasks[channel].cancel()
            
            # Initialize rejoin attempt counter
            if channel not in self.rejoin_attempts:
                self.rejoin_attempts[channel] = 0
            
            max_attempts = self.get_config('connection.auto_rejoin.max_rejoin_attempts', 10) or 10
            retry_interval = self.get_config('connection.auto_rejoin.retry_interval', 30) or 30
            
            self.logger.info(f"Scheduling rejoin for {channel} in {retry_interval} seconds")
            
            # Create and store the rejoin task
            self.rejoin_tasks[channel] = asyncio.create_task(
                self._rejoin_channel_loop(channel, max_attempts, retry_interval)
            )
            
        except Exception as e:
            self.logger.error(f"Error scheduling rejoin for {channel}: {e}")
    
    async def _rejoin_channel_loop(self, channel, max_attempts, retry_interval):
        """Internal loop for attempting to rejoin a channel"""
        try:
            while (self.rejoin_attempts[channel] < max_attempts and 
                   not self.shutdown_requested and
                   channel not in self.channels_joined):
                
                self.rejoin_attempts[channel] += 1
                
                self.logger.info(f"Rejoin attempt {self.rejoin_attempts[channel]}/{max_attempts} for {channel}")
                
                # Wait before attempting rejoin
                await asyncio.sleep(retry_interval)
                
                # Check if we're still connected and registered
                if not self.registered or not self.writer or self.writer.is_closing():
                    self.logger.warning(f"Cannot rejoin {channel}: not connected to server")
                    continue
                
                # Attempt to rejoin
                if self.send_raw(f"JOIN {channel}"):
                    self.pending_joins[channel] = None
                    self.logger.info(f"Sent JOIN for {channel} (waiting for server confirmation)")
                else:
                    self.logger.warning(f"Failed to send JOIN command for {channel}")
            
            # If we've exceeded max attempts or channel was successfully joined
            if channel in self.channels_joined:
                self.rejoin_attempts[channel] = 0
                self.logger.info(f"Rejoin confirmed for {channel}")
            elif self.rejoin_attempts[channel] >= max_attempts:
                self.logger.error(f"Exhausted all {max_attempts} rejoin attempts for {channel}")
            
            # Clean up
            if channel in self.rejoin_tasks:
                del self.rejoin_tasks[channel]
                
        except asyncio.CancelledError:
            self.logger.debug(f"Rejoin task for {channel} was cancelled")
        except Exception as e:
            self.logger.error(f"Error in rejoin loop for {channel}: {e}")
        finally:
            # Ensure cleanup
            if channel in self.rejoin_tasks:
                del self.rejoin_tasks[channel]
    
    def send_message(self, target, msg):
        """Send message to target (channel or user) with enhanced error handling"""
        if not isinstance(target, str) or not isinstance(msg, str):
            self.logger.warning(f"Invalid message parameters: target={type(target)}, msg={type(msg)}")
            return False
        
        return self.error_recovery.safe_execute(
            lambda: self._send_message_impl(target, msg),
            fallback=False,
            logger=self.logger
        )
    
    def _send_message_impl(self, target, msg):
        """Internal implementation of send_message"""
        try:
            # Sanitize target and message
            safe_target = sanitize_user_input(target, max_length=100, 
                                            allowed_chars='#&+!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]{}^`|\\')
            safe_msg = sanitize_user_input(msg, max_length=400)
            
            if not safe_target or not safe_msg:
                self.logger.warning(f"Empty target or message after sanitization")
                return False
            
            # Split long messages to prevent IRC limits
            max_msg_length = 400  # IRC message limit minus PRIVMSG overhead
            
            if len(safe_msg) <= max_msg_length:
                messages = [safe_msg]
            else:
                # Split into chunks
                messages = []
                words = safe_msg.split(' ')
                current_msg = ''
                
                for word in words:
                    if len(current_msg + ' ' + word) <= max_msg_length:
                        current_msg += (' ' if current_msg else '') + word
                    else:
                        if current_msg:
                            messages.append(current_msg)
                        current_msg = word[:max_msg_length]  # Truncate very long words
                
                if current_msg:
                    messages.append(current_msg)
            
            # Send all message parts
            success_count = 0
            for i, message_part in enumerate(messages):
                if i > 0:  # Small delay between messages to avoid flooding
                    time.sleep(0.1)
                
                if self.send_raw(f"PRIVMSG {safe_target} :{message_part}"):
                    success_count += 1
                else:
                    self.logger.error(f"Failed to send message part {i+1}/{len(messages)}")
            
            return success_count == len(messages)
                
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
        
            if command == "CAP":
                await self.sasl_handler.handle_cap_response(params, trailing)
                return
                
            elif command == "AUTHENTICATE":
                await self.sasl_handler.handle_authenticate_response(params)
                return
                
            elif command in ["903", "904", "905", "906", "907", "908"]:
                await self.sasl_handler.handle_sasl_result(command, params, trailing)
                return
            
            elif command == "001":
                self.registered = True
                self.logger.info("Successfully registered with IRC server")
                
                channels = self.get_config('connection.channels', []) or []
                for channel in channels:
                    try:
                        self.send_raw(f"JOIN {channel}")
                        self.pending_joins[self._channel_key(channel)] = None
                    except Exception as e:
                        self.logger.error(f"Error joining channel {channel}: {e}")

            # JOIN failures (numeric replies)
            elif command in {"403", "405", "437", "471", "473", "474", "475", "477"}:
                # Common formats:
                # 471 <me> <#chan> :Cannot join channel (+l)
                # 475 <me> <#chan> :Cannot join channel (+k)
                # 477 <me> <#chan> :You need to be identified...
                our_nick = self.get_config('connection.nick', 'DuckHunt') or 'DuckHunt'
                if params and len(params) >= 2 and params[0].lower() == our_nick.lower():
                    failed_channel = params[1]
                    reason = trailing or "Join rejected"
                    failed_key = self._channel_key(failed_channel)
                    self.channels_joined.discard(failed_key)
                    requester = self.pending_joins.pop(failed_key, None)
                    self.logger.warning(f"Failed to join {failed_channel}: ({command}) {reason}")
                    if requester:
                        try:
                            self.send_message(requester, f"{requester} > Failed to join {failed_channel}: {reason}")
                        except Exception:
                            pass
                return
            
            elif command == "JOIN":
                if prefix:
                    # Some servers send: ":nick!user@host JOIN :#chan" (channel in trailing)
                    channel = None
                    if len(params) >= 1:
                        channel = params[0]
                    elif trailing and isinstance(trailing, str) and trailing.startswith('#'):
                        channel = trailing

                    if not channel:
                        return

                    joiner_nick = prefix.split('!')[0] if '!' in prefix else prefix
                    our_nick = self.get_config('connection.nick', 'DuckHunt') or 'DuckHunt'
                    
                    # Check if we successfully joined (or rejoined) a channel
                    if joiner_nick and joiner_nick.lower() == our_nick.lower():
                        channel_key = self._channel_key(channel)
                        self.channels_joined.add(channel_key)
                        self.logger.info(f"Successfully joined channel {channel}")

                        # If this was an admin-requested join, persist it now.
                        requester = self.pending_joins.pop(channel_key, None)
                        if requester:
                            try:
                                channels = self._config_channels_list()
                                if not any(self._channel_key(c) == channel_key for c in channels if isinstance(c, str)):
                                    channels.append(channel)
                                    self._persist_config()
                                self.send_message(requester, f"{requester} > Joined {channel}.")
                            except Exception:
                                pass
                        else:
                            # Startup/rejoin joins shouldn't change config here.
                            self.pending_joins.pop(channel_key, None)
                        
                        # Cancel any pending rejoin attempts for this channel
                        if channel_key in self.rejoin_tasks:
                            self.rejoin_tasks[channel_key].cancel()
                            del self.rejoin_tasks[channel_key]
                        
                        # Reset rejoin attempts counter
                        if channel_key in self.rejoin_attempts:
                            self.rejoin_attempts[channel_key] = 0
            
            elif command == "PRIVMSG":
                if len(params) >= 1:
                    target = params[0]
                    message = trailing or ""
                    await self.handle_command(prefix, target, message)
            
            elif command == "KICK":
                if len(params) >= 2:
                    channel = params[0]
                    kicked_nick = params[1]
                    kicker = prefix.split('!')[0] if prefix and '!' in prefix else prefix
                    reason = trailing or "No reason given"
                    
                    # Check if we were the one kicked
                    our_nick = self.get_config('connection.nick', 'DuckHunt') or 'DuckHunt'
                    if kicked_nick and kicked_nick.lower() == our_nick.lower():
                        self.logger.warning(f"Kicked from {channel} by {kicker}: {reason}")
                        
                        # Remove from joined channels
                        channel_key = self._channel_key(channel)
                        self.channels_joined.discard(channel_key)
                        
                        # Schedule rejoin if auto-rejoin is enabled
                        if self.get_config('connection.auto_rejoin.enabled', True):
                            asyncio.create_task(self.schedule_rejoin(channel_key))
            
            elif command == "PING":
                try:
                    self.send_raw(f"PONG :{trailing}")
                except Exception as e:
                    self.logger.error(f"Error responding to PING: {e}")
                    
        except Exception as e:
            self.logger.error(f"Critical error in handle_message: {e}")
    
    async def handle_command(self, user, channel, message):
        """Handle bot commands with enhanced error handling and input validation"""
        try:
            # Validate input parameters
            if not isinstance(message, str) or not message.startswith('!'):
                return
            
            if not isinstance(user, str) or not isinstance(channel, str):
                self.logger.warning(f"Invalid user/channel types: {type(user)}, {type(channel)}")
                return
            
            # Sanitize inputs
            safe_message = sanitize_user_input(message, max_length=500)
            safe_user = sanitize_user_input(user, max_length=200) 
            safe_channel = sanitize_user_input(channel, max_length=100)

            # Normalize channel casing for internal consistency.
            if isinstance(safe_channel, str) and (safe_channel.startswith('#') or safe_channel.startswith('&')):
                safe_channel = self._channel_key(safe_channel)
            
            if not safe_message.startswith('!'):
                return
            
            try:
                parts = safe_message[1:].split()
            except Exception as e:
                self.logger.warning(f"Error parsing command '{message}': {e}")
                return
                
            if not parts:
                return
            
            cmd = parts[0].lower()
            args = parts[1:] if len(parts) > 1 else []
            
            # Extract and validate nick with enhanced error handling
            nick = self.error_recovery.safe_execute(
                lambda: safe_user.split('!')[0] if '!' in safe_user else safe_user,
                fallback='Unknown',
                logger=self.logger
            )
            
            if not nick or nick == 'Unknown':
                self.logger.warning(f"Could not extract valid nick from user string: {user}")
                return
            
            # Sanitize nick further
            nick = sanitize_user_input(nick, max_length=50, 
                                     allowed_chars='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]{}^`|\\')
            
            # Get player data with error recovery
            channel_ctx = safe_channel if isinstance(safe_channel, str) and safe_channel.startswith('#') else None
            player = self.error_recovery.safe_execute(
                lambda: self.db.get_player(nick, channel_ctx),
                fallback={'nick': nick, 'xp': 0, 'ducks_shot': 0, 'gun_confiscated': False},
                logger=self.logger
            )
            
            if player is None:
                player = {'nick': nick, 'xp': 0, 'ducks_shot': 0, 'gun_confiscated': False}
            
            # Update activity tracking safely
            if safe_channel.startswith('#'):
                try:
                    player['last_activity_channel'] = safe_channel
                    player['last_activity_time'] = time.time()
                except Exception as e:
                    self.logger.warning(f"Error updating player activity for {nick}: {e}")
            
            try:
                if player.get('ignored', False) and not self.is_admin(user):
                    return
            except Exception as e:
                self.logger.error(f"Error checking admin/ignore status: {e}")
                return
            
            await self._execute_command_safely(cmd, nick, safe_channel, player, args, safe_user)
            
        except Exception as e:
            self.logger.error(f"Critical error in handle_command: {e}")
    
    async def _execute_command_safely(self, cmd, nick, channel, player, args, user):
        """Execute individual commands with enhanced error isolation and user feedback"""
        try:
            # Sanitize command arguments
            safe_args = []
            for arg in args:
                safe_arg = sanitize_user_input(str(arg), max_length=100)
                if safe_arg:
                    safe_args.append(safe_arg)
            
            # Execute command with error recovery
            command_executed = False
            
            if cmd == "bang":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_bang(nick, channel, player),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "bef" or cmd == "befriend":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_bef(nick, channel, player),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "reload":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_reload(nick, channel, player),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "shop":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_shop(nick, channel, player, safe_args),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "duckstats":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_duckstats(nick, channel, player, safe_args),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "topduck":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_topduck(nick, channel),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "use":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_use(nick, channel, player, safe_args),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "give":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_give(nick, channel, player, safe_args),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "duckhelp":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_duckhelp(nick, channel, player),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "globalducks":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_globalducks(nick, channel, safe_args, user),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "rearm" and self.is_admin(user):
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_rearm(nick, channel, safe_args),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "disarm" and self.is_admin(user):
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_disarm(nick, channel, safe_args),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "ignore" and self.is_admin(user):
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_ignore(nick, channel, safe_args),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "unignore" and self.is_admin(user):
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_unignore(nick, channel, safe_args),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "ducklaunch" and self.is_admin(user):
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_ducklaunch(nick, channel, safe_args),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "join" and self.is_admin(user):
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_join_channel(nick, channel, safe_args),
                    fallback=None,
                    logger=self.logger
                )
            elif (cmd == "leave" or cmd == "part") and self.is_admin(user):
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_leave_channel(nick, channel, safe_args),
                    fallback=None,
                    logger=self.logger
                )
            
            # If no command was executed, it might be an unknown command
            if not command_executed:
                self.logger.debug(f"Unknown command '{cmd}' from {nick}")
                
        except Exception as e:
            self.logger.error(f"Critical error executing command '{cmd}' for user {nick}: {e}")
            
            # Provide user-friendly error message
            try:
                if channel.startswith('#'):
                    error_msg = safe_format_message(
                        "{nick} > ⚠️ Something went wrong processing your command. Please try again in a moment.",
                        nick=nick
                    )
                    self.send_message(channel, error_msg)
                else:
                    self.logger.debug("Skipping error message for private channel")
            except Exception as send_error:
                self.logger.error(f"Error sending user error message: {send_error}")
    
    def validate_target_player(self, target_nick, channel):
        """
        Validate that a target player is a valid hunter
        Returns (is_valid, player_data, error_message)
        
        TODO: Implement proper channel membership tracking to ensure 
        the target is actually present in the channel
        """
        if not target_nick:
            return False, None, "No target specified"
        
        target_nick = target_nick.lower().strip()
        
        if not target_nick:
            return False, None, "Invalid target nickname"

        channel_ctx = channel if isinstance(channel, str) and channel.startswith('#') else None
        player = self.db.get_player(target_nick, channel_ctx)
        if not player:
            return False, None, f"Player '{target_nick}' not found. They need to participate in the game first."
        
        has_activity = (
            player.get('xp', 0) > 0 or 
            player.get('shots_fired', 0) > 0 or 
            'current_ammo' in player or
            'magazines' in player
        )
        
        if not has_activity:
            return False, None, f"Player '{target_nick}' has no hunting activity. They may not be an active hunter."
        
        return True, player, None
    
    def is_user_in_channel_sync(self, nick, channel):
        """
        Check if a user is likely in the channel based on recent activity (synchronous version)
        
        This is a practical approach that doesn't require complex IRC response parsing.
        We assume if someone has been active recently, they're still in the channel.
        """
        try:
            channel_ctx = channel if isinstance(channel, str) and channel.startswith('#') else None
            player = self.db.get_player(nick, channel_ctx)
            if not player:
                return False
            
            last_activity_channel = player.get('last_activity_channel')
            last_activity_time = player.get('last_activity_time', 0)
            current_time = time.time()
            
            if (last_activity_channel == channel and 
                current_time - last_activity_time < 1800):
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking channel membership for {nick} in {channel}: {e}")
            return True
    
    async def handle_bang(self, nick, channel, player):
        """Handle !bang command"""
        result = self.game.shoot_duck(nick, channel, player)
        message = self.messages.get(result['message_key'], **result['message_args'])
        self.send_message(channel, message)
        
        if result.get('success') and result.get('dropped_item'):
            dropped_item = result['dropped_item']
            duck_type = dropped_item['duck_type']
            item_name = dropped_item['item_name']
            
            drop_message_key = f'duck_drop_{duck_type}'
            drop_message = self.messages.get(drop_message_key, 
                nick=nick, 
                item_name=item_name
            )
            self.send_message(channel, drop_message)
    
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
        if args and len(args) >= 1:
            start_idx = 0
            if args[0].lower() == "buy":
                start_idx = 1
            
            if len(args) > start_idx:
                try:
                    item_id = int(args[start_idx])
                    target_nick = args[start_idx + 1] if len(args) > start_idx + 1 else None
                    
                    store_in_inventory = target_nick is None
                    await self.handle_shop_buy(nick, channel, player, item_id, target_nick, store_in_inventory)
                    return
                except (ValueError, IndexError):
                    message = self.messages.get('shop_buy_usage', nick=nick)
                    self.send_message(channel, message)
                    return
        
        shop_text = self.shop.get_shop_display(player, self.messages)
        self.send_message(channel, shop_text)
    
    async def handle_shop_buy(self, nick, channel, player, item_id, target_nick=None, store_in_inventory=False):
        """Handle buying an item from the shop"""
        target_player = None
        
        target_player, error_message = self._get_validated_target_player(nick, channel, target_nick)
        if error_message:
            self.send_message(channel, error_message)
            return
        
        result = self.shop.purchase_item(player, item_id, target_player, store_in_inventory)
        
        if not result["success"]:
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
    
    async def handle_duckstats(self, nick, channel, player, args=None):
        """Handle !duckstats command"""
        if args and len(args) > 0:
            target_nick = args[0]
            channel_ctx = channel if isinstance(channel, str) and channel.startswith('#') else None
            target_player = self.db.get_player(target_nick, channel_ctx)
            if not target_player:
                message = f"{nick} > Player '{target_nick}' not found."
                self.send_message(channel, message)
                return
            display_nick = target_nick
            display_player = target_player
        else:
            display_nick = nick
            display_player = player
        bold = self.messages.messages.get('colours', {}).get('bold', '')
        reset = self.messages.messages.get('colours', {}).get('reset', '')
        green = self.messages.messages.get('colours', {}).get('green', '')
        blue = self.messages.messages.get('colours', {}).get('blue', '')
        yellow = self.messages.messages.get('colours', {}).get('yellow', '')
        red = self.messages.messages.get('colours', {}).get('red', '')
        
        # Get player level info
        level_info = self.levels.get_player_level_info(display_player)
        level = level_info['level']
        level_name = level_info['name']
        
        # Build stats message
        xp = display_player.get('xp', 0)
        ducks_shot = display_player.get('ducks_shot', 0)
        ducks_befriended = display_player.get('ducks_befriended', 0)
        accuracy = display_player.get('accuracy', self.get_config('player_defaults.accuracy', 75))
        
        # Calculate additional stats
        total_ducks_encountered = ducks_shot + ducks_befriended
        shots_missed = display_player.get('shots_missed', 0)
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
        current_ammo = display_player.get('current_ammo', 0)
        magazines = display_player.get('magazines', 0)
        bullets_per_mag = display_player.get('bullets_per_magazine', 6)
        jam_chance = display_player.get('jam_chance', 0)
        
        # Gun status
        gun_status = "Armed" if not display_player.get('gun_confiscated', False) else "Confiscated"
        
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
        inventory = display_player.get('inventory', {})
        if inventory:
            items = []
            for item_id, quantity in inventory.items():
                item = self.shop.get_item(int(item_id))
                if item:
                    items.append(f"{item['name']} x{quantity}")
            if items:
                stats_parts.append(f"Items: {', '.join(items)}")
        
        # Add temporary effects if any
        temp_effects = display_player.get('temporary_effects', [])
        if temp_effects:
            active_effects = [effect.get('name', 'Unknown Effect') for effect in temp_effects if isinstance(effect, dict)]
            if active_effects:
                stats_parts.append(f"Effects:{','.join(active_effects)}")
        
        # Send as one compact message
        stats_message = f"{bold}{display_nick}{reset}: {' | '.join(stats_parts)}"
        self.send_message(channel, stats_message)
    
    async def handle_topduck(self, nick, channel):
        """Handle !topduck command - show leaderboards"""
        try:
            # Apply color formatting
            bold = self.messages.messages.get('colours', {}).get('bold', '')
            reset = self.messages.messages.get('colours', {}).get('reset', '')
            
            channel_ctx = channel if isinstance(channel, str) and channel.startswith('#') else None

            # Get top 3 by XP (channel-scoped)
            top_xp = self.db.get_leaderboard_for_channel(channel_ctx, 'xp', 3)

            # Get top 3 by ducks shot (channel-scoped)
            top_ducks = self.db.get_leaderboard_for_channel(channel_ctx, 'ducks_shot', 3)
            
            # Format XP leaderboard as single line
            if top_xp:
                xp_rankings = []
                for i, (player_nick, xp) in enumerate(top_xp, 1):
                    medal = "#1" if i == 1 else "#2" if i == 2 else "#3"
                    xp_rankings.append(f"{medal} {player_nick}:{xp}XP")
                xp_line = f"Top XP: {bold}{reset} " + " | ".join(xp_rankings)
                self.send_message(channel, xp_line)
            else:
                self.send_message(channel, "No XP data available yet!")
            
            # Format ducks shot leaderboard as single line
            if top_ducks:
                duck_rankings = []
                for i, (player_nick, ducks) in enumerate(top_ducks, 1):
                    medal = "#1" if i == 1 else "#2" if i == 2 else "#3"
                    duck_rankings.append(f"{medal} {player_nick}:{ducks}")
                duck_line = f"Top Hunters: {bold}{reset} " + " | ".join(duck_rankings)
                self.send_message(channel, duck_line)
            else:
                self.send_message(channel, "No duck hunting data available yet!")
                
        except Exception as e:
            self.logger.error(f"Error in handle_topduck: {e}")
            self.send_message(channel, f"{nick} > Error retrieving leaderboard data.")
    
    async def handle_duckhelp(self, nick, channel, _player):
        """Handle !duckhelp command

        Sends help to the user via private message (PM/DM) with examples.
        """

        dm_target = nick  # PRIVMSG target for a PM is the nick

        help_lines = [
            "DuckHunt Commands (sent via PM)",
            "Player commands:",
            "- !bang — shoot when a duck appears. Example: !bang",
            "- !reload — reload your weapon. Example: !reload",
            "- !shop — list shop items. Example: !shop",
            "- !buy <item_id> — buy from shop. Example: !buy 3",
            "- !use <item_id> [target] — use an inventory item. Example: !use 7 OR !use 9 SomeNick",
            "- !duckstats [player] — view stats/inventory. Example: !duckstats OR !duckstats SomeNick",
            "- !topduck — show leaderboards. Example: !topduck",
            "- !give <item_id> <player> — gift an owned item. Example: !give 2 SomeNick",
            "- !globalducks [player] — duck totals across all configured channels. Example: !globalducks OR !globalducks SomeNick",
            "- !duckhelp — show this help. Example: !duckhelp",
        ]

        # Include admin commands only for admins.
        # (Using nick list avoids relying on hostmask parsing.)
        if nick.lower() in self.admins:
            help_lines.extend([
                "Admin commands:",
                "- !rearm <player|all> — rearm a player. Example: !rearm SomeNick OR !rearm all",
                "- !disarm <player> — confiscate gun. Example: !disarm SomeNick",
                "- !ignore <player> — ignore a player. Example: !ignore SomeNick",
                "- !unignore <player> — unignore a player. Example: !unignore SomeNick",
                "- !ducklaunch [duck_type] — force spawn. Example: !ducklaunch golden",
                "- (PM) !ducklaunch <#channel> [duck_type] — Example: !ducklaunch #ct fast",
                "- !join <#channel> — make the bot join a channel. Example: !join #ct",
                "- !leave <#channel> — make the bot leave a channel. Example: !leave #ct",
            ])

        for line in help_lines:
            self.send_message(dm_target, line)

        # If invoked in a channel, add a brief confirmation to check PMs.
        if isinstance(channel, str) and channel.startswith('#'):
            self.send_message(channel, f"{nick} > I sent you a PM with commands and examples. Please check your PM window.")

    async def handle_globalducks(self, nick, channel, args, user):
        """User: !globalducks [player] — totals across all configured channels.

        Non-admins can query themselves only. Admins can query other nicks.
        """
        try:
            channels = self.get_config('connection.channels', []) or []
            if not isinstance(channels, list):
                channels = []

            target_nick = nick
            if args and len(args) >= 1:
                requested = sanitize_user_input(
                    str(args[0]),
                    max_length=50,
                    allowed_chars='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]{}^`|\\'
                )
                if requested:
                    target_nick = requested

            # Anyone can query anyone via !globalducks <player>

            totals = self.db.get_global_duck_totals(target_nick, channels)
            shot = totals.get('ducks_shot', 0)
            bef = totals.get('ducks_befriended', 0)
            total = totals.get('total_ducks', shot + bef)
            counted = totals.get('channels_counted', 0)

            if not channels:
                self.send_message(channel, f"{nick} > No configured channels to total.")
                return

            self.send_message(
                channel,
                f"{nick} > Global totals for {target_nick} across configured channels: {shot} shot, {bef} befriended ({total} total) [{counted}/{len(channels)} channels have stats]."
            )
        except Exception as e:
            self.logger.error(f"Error in handle_globalducks: {e}")
            self.send_message(channel, f"{nick} > Error calculating global totals.")

    def _sanitize_channel_name(self, channel_name: str) -> str:
        """Validate/sanitize an IRC channel name."""
        if not isinstance(channel_name, str):
            return ""
        safe = sanitize_user_input(
            channel_name.strip(),
            max_length=100,
            allowed_chars='#&+!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]{}^`|\\'
        )
        if not safe:
            return ""
        if not (safe.startswith('#') or safe.startswith('&')):
            return ""
        return safe

    def _config_channels_list(self):
        """Return the mutable in-memory config channel list, creating it if needed."""
        if not isinstance(self.config, dict):
            self.config = {}
        connection = self.config.get('connection')
        if not isinstance(connection, dict):
            connection = {}
            self.config['connection'] = connection
        channels = connection.get('channels')
        if not isinstance(channels, list):
            channels = []
            connection['channels'] = channels
        return channels

    def _persist_config(self) -> bool:
        """Persist current config to disk (best-effort, atomic write)."""
        try:
            config_dir = os.path.dirname(self.config_file_path)
            os.makedirs(config_dir, exist_ok=True)

            tmp_path = f"{self.config_file_path}.tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                # Keep it stable/human-readable.
                import json
                json.dump(self.config, f, indent=4, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, self.config_file_path)
            return True
        except Exception as e:
            self.logger.error(f"Failed to persist config to disk: {e}")
            return False

    async def handle_join_channel(self, nick, channel, args):
        """Admin: !join <#channel> (supports PM and channel invocation)."""
        if not args:
            self.send_message(channel, f"{nick} > Usage: !join <#channel>")
            return

        target_channel = self._sanitize_channel_name(args[0])
        if not target_channel:
            self.send_message(channel, f"{nick} > Invalid channel. Usage: !join <#channel>")
            return

        target_key = self._channel_key(target_channel)

        if target_key in self.channels_joined:
            self.send_message(channel, f"{nick} > I'm already in {target_channel}.")
            return

        if not self.send_raw(f"JOIN {target_channel}"):
            self.send_message(channel, f"{nick} > Couldn't send JOIN (not connected?).")
            return

        # Wait for server JOIN confirmation before marking joined/persisting.
        self.pending_joins[target_key] = nick
        self.send_message(channel, f"{nick} > Attempting to join {target_channel}...")

    async def handle_leave_channel(self, nick, channel, args):
        """Admin: !leave <#channel> / !part <#channel> (supports PM and channel invocation)."""
        if not args:
            self.send_message(channel, f"{nick} > Usage: !leave <#channel>")
            return

        target_channel = self._sanitize_channel_name(args[0])
        if not target_channel:
            self.send_message(channel, f"{nick} > Invalid channel. Usage: !leave <#channel>")
            return

        target_key = self._channel_key(target_channel)

        # Cancel any pending rejoin attempts and forget state.
        if target_key in self.rejoin_tasks:
            try:
                self.rejoin_tasks[target_key].cancel()
            except Exception:
                pass
            del self.rejoin_tasks[target_key]
        if target_key in self.rejoin_attempts:
            del self.rejoin_attempts[target_key]

        self.channels_joined.discard(target_key)

        # Update in-memory config so reconnects do not rejoin the channel.
        channels = self._config_channels_list()
        try:
            channels[:] = [c for c in channels if not (isinstance(c, str) and self._channel_key(c) == target_key)]
        except Exception:
            pass

        # Persist across restarts
        if not self._persist_config():
            self.send_message(channel, f"{nick} > Removed {target_channel} from my channel list, but failed to write config.json (check permissions).")
            # Continue attempting PART anyway.

        # Send PART even if we don't think we're in it (server will ignore or error).
        if not self.send_raw(f"PART {target_channel} :Requested by {nick}"):
            self.send_message(channel, f"{nick} > Couldn't send PART (not connected?).")
            return

        self.send_message(channel, f"{nick} > Leaving {target_channel}.")
    
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
            target_player, error_message = self._get_validated_target_player(nick, channel, target_nick)
            if error_message:
                self.send_message(channel, error_message)
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
                elif effect_type == 'perfect_aim':
                    duration_seconds = int(effect.get('duration', 1800))
                    minutes = max(1, duration_seconds // 60)
                    message = self.messages.get('use_perfect_aim', nick=nick, duration_minutes=minutes)
                elif effect_type == 'duck_radar':
                    duration_seconds = int(effect.get('duration', 21600))
                    hours = max(1, duration_seconds // 3600)
                    message = self.messages.get('use_duck_radar', nick=nick, duration_hours=hours)
                elif effect_type == 'summon_duck':
                    # Summoning needs a channel context. If used in PM, pick the first configured channel.
                    delay = int(effect.get('delay', 0))
                    target_channel = channel
                    is_private_msg = not isinstance(target_channel, str) or not target_channel.startswith('#')
                    if is_private_msg:
                        channels = self.get_config('connection.channels', []) or []
                        target_channel = channels[0] if channels else None

                    if not target_channel:
                        message = f"{nick} > I don't know which channel to summon a duck in. Use this in a channel."
                    else:
                        # PM commands should only spawn normal/fast/golden.
                        spawn_type = None
                        if is_private_msg:
                            import random
                            golden_chance = self.get_config('duck_types.golden.chance', self.get_config('golden_duck_chance', 0.15))
                            fast_chance = self.get_config('duck_types.fast.chance', self.get_config('fast_duck_chance', 0.25))
                            try:
                                golden_chance = float(golden_chance)
                            except (TypeError, ValueError):
                                golden_chance = 0.15
                            try:
                                fast_chance = float(fast_chance)
                            except (TypeError, ValueError):
                                fast_chance = 0.25

                            r = random.random()
                            if r < max(0.0, golden_chance):
                                spawn_type = 'golden'
                            elif r < max(0.0, golden_chance) + max(0.0, fast_chance):
                                spawn_type = 'fast'
                            else:
                                spawn_type = 'normal'

                        if delay <= 0:
                            if spawn_type:
                                await self.game.force_spawn_duck(target_channel, spawn_type)
                            else:
                                await self.game.spawn_duck(target_channel)
                            message = self.messages.get('use_summon_duck', nick=nick, channel=target_channel)
                        else:
                            async def delayed_summon():
                                try:
                                    await asyncio.sleep(delay)
                                    if target_channel in self.channels_joined:
                                        if spawn_type:
                                            await self.game.force_spawn_duck(target_channel, spawn_type)
                                        else:
                                            await self.game.spawn_duck(target_channel)
                                except asyncio.CancelledError:
                                    return
                                except Exception:
                                    return

                            asyncio.create_task(delayed_summon())
                            minutes = max(1, delay // 60)
                            message = self.messages.get('use_summon_duck_delayed', nick=nick, channel=target_channel, delay_minutes=minutes)
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
    
    async def handle_give(self, nick, channel, player, args):
        """Handle !give command - give inventory items to other players"""
        if not args or len(args) < 2:
            self.send_message(channel, f"{nick} > Usage: !give <item_id> <player>")
            return
        
        try:
            item_id = int(args[0])
            target_nick = args[1]
            
            # Validate target player
            target_player, error_message = self._get_validated_target_player(nick, channel, target_nick)
            if error_message:
                self.send_message(channel, f"{nick} > {error_message}")
                return
            
            if not target_player:
                self.send_message(channel, f"{nick} > Player {target_nick} not found.")
                return
            
            # Check if player has the item in inventory
            inventory = player.get('inventory', {})
            if str(item_id) not in inventory or inventory[str(item_id)] <= 0:
                self.send_message(channel, f"{nick} > You don't have that item. Use !duckstats to check your inventory.")
                return
            
            # Get item info from shop
            shop_items = self.shop.get_items()
            if item_id not in shop_items:
                self.send_message(channel, f"{nick} > Invalid item ID.")
                return
            
            item = shop_items[item_id]
            
            # Remove from giver's inventory
            inventory[str(item_id)] -= 1
            if inventory[str(item_id)] <= 0:
                del inventory[str(item_id)]
            
            # Add to receiver's inventory
            target_inventory = target_player.get('inventory', {})
            target_inventory[str(item_id)] = target_inventory.get(str(item_id), 0) + 1
            target_player['inventory'] = target_inventory
            
            # Send appropriate gift message based on item type
            item_type = item.get('type', '')
            if item_type == 'ammo':
                message = self.messages.get('gift_ammo', 
                    nick=nick, target_nick=target_nick, amount=item.get('amount', 1))
            elif item_type == 'magazine':
                message = self.messages.get('gift_magazine', 
                    nick=nick, target_nick=target_nick)
            elif item_type == 'clean_gun':
                message = self.messages.get('gift_gun_brush', 
                    nick=nick, target_nick=target_nick)
            elif item_type == 'insurance':
                message = self.messages.get('gift_insurance', 
                    nick=nick, target_nick=target_nick)
            elif item_type == 'dry_clothes':
                message = self.messages.get('gift_dry_clothes', 
                    nick=nick, target_nick=target_nick)
            elif item_type == 'buy_gun_back':
                message = self.messages.get('gift_buy_gun_back', 
                    nick=nick, target_nick=target_nick)
            else:
                # Generic gift message for other items
                message = f"{nick} > Gave {item['name']} to {target_nick}!"
            
            self.send_message(channel, message)
            self.db.save_database()
            
        except ValueError:
            self.send_message(channel, f"{nick} > Usage: !give <item_id> <player>")
    
    async def handle_rearm(self, nick, channel, args):
        """Handle !rearm command (admin only) - supports private messages"""
        is_private_msg = not channel.startswith('#')
        
        if args:
            target_nick = args[0]
            
            # Check if admin wants to rearm all players
            if target_nick.lower() == 'all':
                rearmed_count = 0
                channel_ctx = channel if isinstance(channel, str) and channel.startswith('#') else None
                for player_nick, player in self.db.get_players_for_channel(channel_ctx).items():
                    if player.get('gun_confiscated', False):
                        player['gun_confiscated'] = False
                        self.levels.update_player_magazines(player)
                        player['current_ammo'] = player.get('bullets_per_magazine', 6)
                        rearmed_count += 1
                
                if is_private_msg:
                    message = f"{nick} > Rearmed all players ({rearmed_count} players)"
                else:
                    message = self.messages.get('admin_rearm_all', admin=nick)
                self.send_message(channel, message)
                self.db.save_database()
                return
            
            player, error_msg = self._get_admin_target_player(nick, channel, target_nick)
            
            if error_msg:
                message = f"{nick} > {error_msg}"
                self.send_message(channel, message)
                return
            
            # Rearm the target player
            if player is not None:
                player['gun_confiscated'] = False
                self.levels.update_player_magazines(player)
                player['current_ammo'] = player.get('bullets_per_magazine', 6)
            
            if is_private_msg:
                message = f"{nick} > Rearmed {target_nick}"
            else:
                message = self.messages.get('admin_rearm_player', target=target_nick, admin=nick)
            self.send_message(channel, message)
        else:
            if is_private_msg:
                self.send_message(channel, f"{nick} > Usage: !rearm <player|all>")
                return
            
            # Rearm the admin themselves (only in channels)
            channel_ctx = channel if isinstance(channel, str) and channel.startswith('#') else None
            player = self.db.get_player(nick, channel_ctx)
            
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
        player, error_msg = self._get_admin_target_player(nick, channel, target_nick)
        
        if error_msg:
            message = f"{nick} > {error_msg}"
            self.send_message(channel, message)
            return
        
        # Ensure player is not None before accessing it
        if player is None:
            message = f"{nick} > Failed to get player data for {target_nick}"
            self.send_message(channel, message)
            return
        
        # Disarm the target player
        player['gun_confiscated'] = True
        
        if is_private_msg:
            message = f"{nick} > Disarmed {target_nick}"
        else:
            message = self.messages.get('admin_disarm', target=target_nick, admin=nick)
        
        self.send_message(channel, message)
        self.db.save_database()
    
    def _send_admin_usage_or_execute(self, nick, channel, args, usage_command, private_usage, message_key, action_func):
        """Helper for simple admin commands that don't need complex validation"""
        is_private_msg = not channel.startswith('#')
        
        if not args:
            if is_private_msg:
                self.send_message(channel, f"{nick} > Usage: {private_usage}")
            else:
                message = self.messages.get(usage_command)
                self.send_message(channel, message)
            return
        
        target = args[0].lower()
        channel_ctx = channel if isinstance(channel, str) and channel.startswith('#') else None
        player = self.db.get_player(target, channel_ctx)
        
        action_func(player)
        
        if is_private_msg:
            action_name = "Ignored" if message_key == 'admin_ignore' else "Unignored"
            message = f"{nick} > {action_name} {target}"
        else:
            message = self.messages.get(message_key, target=target, admin=nick)
        
        self.send_message(channel, message)
        self.db.save_database()

    async def handle_ignore(self, nick, channel, args):
        """Handle !ignore command (admin only) - supports private messages"""
        self._send_admin_usage_or_execute(
            nick, channel, args,
            usage_command='usage_ignore',
            private_usage='!ignore <player>',
            message_key='admin_ignore',
            action_func=lambda player: player.update({'ignored': True})
        )
    
    async def handle_unignore(self, nick, channel, args):
        """Handle !unignore command (admin only) - supports private messages"""
        self._send_admin_usage_or_execute(
            nick, channel, args,
            usage_command='usage_unignore',
            private_usage='!unignore <player>',
            message_key='admin_unignore',
            action_func=lambda player: player.update({'ignored': False})
        )

    async def handle_ducklaunch(self, nick, channel, args):
        """Handle !ducklaunch command (admin only) - supports duck type specification"""
        # For private messages, need to specify a target channel
        target_channel = channel
        is_private_msg = not channel.startswith('#')
        
        if is_private_msg:
            if not args:
                self.send_message(channel, f"{nick} > Usage: !ducklaunch [channel] [duck_type]")
                return
            target_channel = args[0]
            duck_type_arg = args[1] if len(args) > 1 else "normal"
        else:
            duck_type_arg = args[0] if args else "normal"

        target_key = self._channel_key(target_channel)
        
        # Validate target channel
        if target_key not in self.channels_joined:
            if is_private_msg:
                self.send_message(channel, f"{nick} > Channel {target_channel} is not available for duckhunt")
            else:
                message = self.messages.get('admin_ducklaunch_not_enabled')
                self.send_message(channel, message)
            return
        
        # Validate duck type
        duck_type_arg = duck_type_arg.lower()
        if is_private_msg:
            valid_types = {'normal', 'fast', 'golden'}
        else:
            duck_types_cfg = self.get_config('duck_types', {}) or {}
            if not isinstance(duck_types_cfg, dict):
                duck_types_cfg = {}
            valid_types = set(['normal']) | set(duck_types_cfg.keys())

        if duck_type_arg not in valid_types:
            valid_list = ', '.join(sorted(valid_types))
            self.send_message(channel, f"{nick} > Invalid duck type '{duck_type_arg}'. Valid types: {valid_list}")
            return

        # Force spawn the specified duck type (supports multi-spawn types like couple/family)
        await self.game.force_spawn_duck(target_key, duck_type_arg)
        
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
            
            self.logger.info("Bot is now running! Press Ctrl+C to stop.")
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
            
            self.logger.info("Shutdown initiated, cleaning up...")
            
        except asyncio.CancelledError:
            self.logger.info("🛑 Main loop cancelled")
        except Exception as e:
            self.logger.error(f"Bot error: {e}")
        finally:
            # Fast cleanup - cancel tasks immediately with short timeout
            tasks_to_cancel = [task for task in [game_task, message_task] if task and not task.done()]
            
            # Cancel all rejoin tasks
            for channel, task in list(self.rejoin_tasks.items()):
                if task and not task.done():
                    task.cancel()
                    tasks_to_cancel.append(task)
                    self.logger.debug(f"Cancelled rejoin task for {channel}")
            
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
                    self.logger.warning("Task cancellation timed out")
            
            # Final database save
            try:
                self.db.save_database()
                self.logger.info("Database saved")
            except Exception as e:
                self.logger.error(f"Error saving database: {e}")
            
            # Fast connection close
            await self._close_connection()
            
            self.logger.info("Bot shutdown complete")
    
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
                    self.logger.warning("Connection close timed out - forcing close")
                except Exception as e:
                    self.logger.debug(f"Error during connection close: {e}")
                    
            self.logger.info("🔌 IRC connection closed")
            
        except Exception as e:
            self.logger.error(f"Critical error closing connection: {e}")
        finally:
            # Ensure writer is cleared regardless of errors
            self.writer = None
            self.reader = None
    
