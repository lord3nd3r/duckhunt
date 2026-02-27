import asyncio
import os
import sys
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
        # Track requested joins / pending server confirmation.
        # Used by auto-rejoin and (in newer revisions) admin join/leave reporting.
        self.pending_joins = {}
        self.shutdown_requested = False
        self.restart_requested = False
        self.rejoin_attempts = {}  # Track rejoin attempts per channel
        self.rejoin_tasks = {}     # Track active rejoin tasks
        
        self.logger.info("🤖 Initializing DuckHunt Bot components...")
        
        # Initialize error recovery systems
        self.error_recovery = ErrorRecovery()
        self.health_checker = HealthChecker(check_interval=60.0)
        
        self.db = DuckDB(bot=self)
        self.game = DuckGame(self, self.db)
        # Rate limiting state: per-nick token buckets to slow scripted abuse
        # Structure: {nick_lower: {'tokens': float, 'last_refill': float}}
        self._rate_limiters = {}
        # Default rate-limit configuration (can be overridden via config.json)
        self._rl_capacity = float(self.get_config('anti_abuse.rate_limit_capacity', 3))
        self._rl_refill_secs = float(self.get_config('anti_abuse.rate_limit_refill_secs', 1.5))
        messages_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'messages.json')
        self.messages = MessageManager(messages_file)
        
        self.sasl_handler = SASLHandler(self, config)
        
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
                lambda: self.db is not None and hasattr(self.db, 'channels'),
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
        """Normalize channel for internal comparisons (IRC channels are case-insensitive)."""
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
    


    def _get_admin_target_player(self, nick, channel, target_nick):
        """
        Helper method to get target player for admin commands with validation.
        Returns (player, error_message) - if error_message is not None, command should return early.
        """
        is_private_msg = not channel.startswith('#')
        
        if not is_private_msg:
            if target_nick.lower() == nick.lower():
                target_nick = target_nick.lower()
                player = self.db.get_player(target_nick, channel)
                return player, None
            else:
                is_valid, player, error_msg = self.validate_target_player(target_nick, channel)
                if not is_valid:
                    return None, error_msg
                return player, None
        else:
            target_nick = target_nick.lower()
            player = self.db.get_player(target_nick, channel)
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
            # Backward/forward compatibility: ensure attribute exists.
            if not hasattr(self, 'pending_joins') or not isinstance(self.pending_joins, dict):
                self.pending_joins = {}

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

                # Check if we're still connected and registered
                if not self.registered or not self.writer or self.writer.is_closing():
                    self.logger.warning(f"Cannot rejoin {channel}: not connected to server")
                    await asyncio.sleep(retry_interval)
                    continue
                
                # Attempt to rejoin
                if self.send_raw(f"JOIN {channel}"):
                    self.pending_joins[channel] = None
                    self.logger.info(f"Sent JOIN for {channel} (waiting for server confirmation)")
                else:
                    self.logger.warning(f"Failed to send JOIN command for {channel}")

                # Wait before next attempt (if needed)
                await asyncio.sleep(retry_interval)
            
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
            safe_msg = sanitize_user_input(msg, max_length=4000)
            
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
                        # Wait for server JOIN confirmation before marking joined.
                        if not hasattr(self, 'pending_joins') or not isinstance(self.pending_joins, dict):
                            self.pending_joins = {}
                        self.pending_joins[self._channel_key(channel)] = None
                    except Exception as e:
                        self.logger.error(f"Error joining channel {channel}: {e}")

            # JOIN failures (numeric replies)
            elif command in {"403", "405", "437", "471", "473", "474", "475", "477", "438", "439"}:
                # Common formats:
                # 471 <me> <#chan> :Cannot join channel (+l)
                # 474 <me> <#chan> :Cannot join channel (+b)
                # 477 <me> <#chan> :You need to be identified...
                our_nick = self.get_config('connection.nick', 'DuckHunt') or 'DuckHunt'
                if params and len(params) >= 2 and params[0].lower() == our_nick.lower():
                    failed_channel = params[1]
                    reason = trailing or "Join rejected"
                    failed_key = self._channel_key(failed_channel)
                    self.channels_joined.discard(failed_key)
                    if hasattr(self, 'pending_joins') and isinstance(self.pending_joins, dict):
                        self.pending_joins.pop(failed_key, None)
                    self.logger.warning(f"Failed to join {failed_channel}: ({command}) {reason}")
                return
            
            elif command == "JOIN":
                if prefix:
                    # Some servers send either:
                    #   :nick!user@host JOIN #chan
                    # or
                    #   :nick!user@host JOIN :#chan
                    channel = None
                    if len(params) >= 1:
                        channel = params[0]
                    elif trailing and isinstance(trailing, str) and trailing.startswith('#'):
                        channel = trailing

                    if not channel:
                        return

                    safe_channel = sanitize_user_input(channel, max_length=100, allowed_chars='#&+!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]{}^`|\\')
                    channel_key = self._channel_key(safe_channel)
                    joiner_nick = prefix.split('!')[0] if '!' in prefix else prefix
                    our_nick = self.get_config('connection.nick', 'DuckHunt') or 'DuckHunt'
                    
                    # Check if we successfully joined (or rejoined) a channel
                    if joiner_nick and joiner_nick.lower() == our_nick.lower():
                        self.channels_joined.add(channel_key)
                        self.logger.info(f"Successfully joined channel {channel}")

                        # Clear pending join marker
                        if hasattr(self, 'pending_joins') and isinstance(self.pending_joins, dict):
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
            # Include @, ., * so hostmask-based admin auth (nick!user@host.domain) works correctly.
            safe_user = sanitize_user_input(user, max_length=200,
                                            allowed_chars='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]{}^`|\\!@.*:')
            safe_channel = sanitize_user_input(channel, max_length=100, allowed_chars='#&+!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]{}^`|\\')
            
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
            player = self.error_recovery.safe_execute(
                lambda: self.db.get_player(nick, safe_channel),
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
                if self.db.is_ignored(nick, safe_channel) and not self.is_admin(user):
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

            # Rate-limited command set
            limited_cmds = {'bang', 'bef', 'befriend', 'shop', 'use'}

            # Check rate limiter for these commands
            if cmd in limited_cmds:
                nick_key = nick.lower()
                now = time.time()
                rl = self._rate_limiters.get(nick_key)
                if rl is None:
                    rl = {'tokens': self._rl_capacity, 'last_refill': now}
                    self._rate_limiters[nick_key] = rl

                # Refill tokens
                since = now - rl['last_refill']
                if since > 0:
                    refill = since / self._rl_refill_secs
                    rl['tokens'] = min(self._rl_capacity, rl['tokens'] + refill)
                    rl['last_refill'] = now

                if rl['tokens'] < 1.0:
                    # Deny execution and notify user
                    try:
                        msg = self.messages.get('rate_limited', nick=nick)
                        self.send_message(channel, msg)
                    except Exception:
                        self.send_message(channel, f"{nick} > You're doing that too quickly — slow down!")
                    return
                else:
                    rl['tokens'] -= 1.0

            # Special case: admin PM-only bot restart uses !reload.
            # In channels, !reload remains the gameplay reload command.
            if cmd == "reload" and not channel.startswith('#') and self.is_admin(user):
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_reloadbot(nick, channel),
                    fallback=None,
                    logger=self.logger
                )

            if command_executed:
                return
            
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
            elif cmd == "globaltop":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_globaltop(nick, channel),
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
            elif cmd == "part" and self.is_admin(user):
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_part_channel(nick, channel, safe_args),
                    fallback=None,
                    logger=self.logger
                )
            elif cmd == "daily":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_daily(nick, channel, player),
                    fallback=None, logger=self.logger
                )
            elif cmd == "effects":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_effects(nick, channel, player),
                    fallback=None, logger=self.logger
                )
            elif cmd == "achievements":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_achievements(nick, channel, player),
                    fallback=None, logger=self.logger
                )
            elif cmd == "inv":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_inv(nick, channel, player),
                    fallback=None, logger=self.logger
                )
            elif cmd == "profile":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_profile(nick, channel, player),
                    fallback=None, logger=self.logger
                )
            elif cmd == "weather":
                command_executed = True
                await self.error_recovery.safe_execute_async(
                    lambda: self.handle_weather(nick, channel),
                    fallback=None, logger=self.logger
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
        
        player = self.db.get_player_if_exists(target_nick, channel)
        if not player:
            return False, None, f"Player '{target_nick}' not found in {channel}. They need to participate in this channel first."
        
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
            player = self.db.get_player_if_exists(nick, channel)
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
        if message.startswith('[Missing'):
            message = f"{nick} > 🦆 ..." 
        self.send_message(channel, message)

        # Flock: show remaining count
        remaining_flock = result.get('message_args', {}).get('remaining_flock', 0)
        if remaining_flock and remaining_flock > 0:
            self.send_message(channel, f"🦆 {remaining_flock} duck(s) still in the flock!")

        # Boss duck kill: announce contributors and bonus XP
        if result.get('boss_contributors'):
            contributors = result['boss_contributors']
            bonus_xp = result.get('boss_bonus_xp', 0)
            total_hits = sum(contributors.values())
            parts = []
            for cn, hits in sorted(contributors.items(), key=lambda x: -x[1]):
                share = int(bonus_xp * hits / total_hits) if total_hits else 0
                parts.append(f"{cn}({hits} hits, +{share}XP)")
            self.send_message(channel,
                f"💀 Boss duck defeated! Contributors: {' | '.join(parts)}")

        # Item drops
        if result.get('success') and result.get('dropped_item'):
            dropped_item = result['dropped_item']
            duck_type = dropped_item['duck_type']
            item_name = dropped_item['item_name']
            drop_message = self.messages.get(f'duck_drop_{duck_type}',
                nick=nick, item_name=item_name)
            if drop_message.startswith('[Missing'):
                drop_message = f"{nick} > 🎁 The duck dropped: {item_name}!"
            self.send_message(channel, drop_message)

        # Achievement announcements
        for ach in result.get('new_achievements', []):
            self.send_message(channel,
                f"🏆 {nick} unlocked achievement: {ach['icon']} {ach['name']} — {ach['description']}!")


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
            target_player = self.db.get_player_if_exists(target_nick, channel)
            if not target_player:
                message = f"{nick} > Player '{target_nick}' not found in {channel}."
                self.send_message(channel, message)
                return
            display_nick = target_nick
            display_player = target_player
        else:
            display_nick = nick
            display_player = player
        # Safely extract only the control byte from colour mappings so numeric
        # colour parameters don't accidentally consume adjacent digits (e.g. XP values).
        colours_map = self.messages.messages.get('colours', {}) if isinstance(self.messages.messages.get('colours', {}), dict) else {}
        def _ctrl(c):
            return (c[0] if isinstance(c, str) and c else '')
        bold = _ctrl(colours_map.get('bold', ''))
        reset = _ctrl(colours_map.get('reset', ''))
        green = _ctrl(colours_map.get('green', ''))
        blue = _ctrl(colours_map.get('blue', ''))
        yellow = _ctrl(colours_map.get('yellow', ''))
        red = _ctrl(colours_map.get('red', ''))
        
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
        bullets_per_mag = display_player.get('bullets_per_magazine', 6)
        jam_chance = display_player.get('jam_chance', 0)
        
        # Spare magazine count:
        # - active_spares: reloads available from level-based magazine slots (can reload without inventory item)
        # - inv_mags: Magazine items sitting in inventory (these are consumed on !reload when slots run out)
        # Show them separately so the number matches what !inv shows
        active_spares = max(0, display_player.get('magazines', 1) - 1)
        inv_mags = 0
        inventory = display_player.get('inventory', {})
        for item_id_str, qty in inventory.items():
            if qty > 0:
                try:
                    item = self.shop.get_item(int(item_id_str))
                    if item and item.get('type') == 'magazine':
                        inv_mags += qty
                except ValueError:
                    pass
        # Total reloads available = active level-slots + inventory Magazine items
        total_spares = active_spares + inv_mags
        
        # Gun status
        gun_status = "Armed" if not display_player.get('gun_confiscated', False) else "Confiscated"
        
        # Build compact stats message with subtle colors
        stats_parts = [
            f"Lv{level} {level_name}",
            # Place the numeric XP before the colour control so clients don't
            # treat the following digits as colour parameters (which would
            # truncate the displayed number). Colour the 'XP' suffix instead.
            f"{xp}{green}XP{reset}{xp_progress}",
            f"{ducks_shot} shot",
            f"{ducks_befriended} befriended",
            f"{accuracy}% accuracy",
            f"{hit_rate}% hit rate",
            f"{green if gun_status == 'Armed' else red}{gun_status}{reset}",
            f"{current_ammo}/{bullets_per_mag} ammo | {total_spares} spare magazines",
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
            
            # Get top 3 by XP
            top_xp = self.db.get_leaderboard(channel, 'xp', 3)
            
            # Get top 3 by ducks shot
            top_ducks = self.db.get_leaderboard(channel, 'ducks_shot', 3)
            
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

    async def handle_globaltop(self, nick, channel):
        """Handle !globaltop command - show top players across all channels (by XP)."""
        try:
            bold = self.messages.messages.get('colours', {}).get('bold', '')
            reset = self.messages.messages.get('colours', {}).get('reset', '')

            def _display_channel_key(channel_key: str) -> str:
                """Convert internal channel keys to a user-friendly label."""
                if not isinstance(channel_key, str) or not channel_key:
                    return "unknown"
                if channel_key.startswith('#') or channel_key.startswith('&'):
                    return channel_key
                if channel_key == '__global__':
                    return "legacy"
                if channel_key == '__pm__':
                    return "pm"
                if channel_key == '__unknown__':
                    return "unknown"
                return channel_key

            entries = []  # (xp, player_nick, channel_key)
            for channel_key, player_nick, player_data in self.db.iter_all_players():
                if not isinstance(player_data, dict):
                    continue
                try:
                    xp = int(player_data.get('xp', 0) or 0)
                except (ValueError, TypeError):
                    xp = 0
                if xp <= 0:
                    continue
                entries.append((xp, str(player_nick), str(channel_key)))

            if not entries:
                self.send_message(channel, f"{nick} > No global XP data available yet!")
                return

            entries.sort(key=lambda t: t[0], reverse=True)
            top5 = entries[:5]

            parts = []
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            for idx, (xp, player_nick, channel_key) in enumerate(top5, 1):
                prefix = medals.get(idx, f"#{idx}")
                channel_label = _display_channel_key(channel_key)
                parts.append(f"{prefix} {player_nick} {xp}XP {channel_label}")

            line = f"Top XP: {bold}{reset} " + " | ".join(parts)
            self.send_message(channel, line)
        except Exception as e:
            self.logger.error(f"Error in handle_globaltop: {e}")
            self.send_message(channel, f"{nick} > Error retrieving global leaderboard data.")
    
    async def handle_duckhelp(self, nick, channel, _player):
        """Handle !duckhelp command - sends detailed help via PM"""
        
        # Send notification to channel
        if channel.startswith('#'):
            self.send_message(channel, f"{nick} > Please check your PM for the duckhunt command list.")
        
        help_lines = [
            "=== DuckHunt Commands ===",
            "",
            "BASIC COMMANDS:",
            "  !bang - Shoot at a duck",
            "  !bef or !befriend - Try to befriend a duck",
            "  !reload - Reload your gun",
            "",
            "INFO COMMANDS:",
            "  !duckstats [player] - View duck hunting statistics",
            "  !topduck - View leaderboard (top hunters)",
            "  !globaltop - View global leaderboard (top 5 across all channels)",
            "  !profile - Detailed stat card sent to your PM",
            "  !inv - Quick view your inventory",
            "  !effects - Show active temporary effects and timers",
            "  !achievements - View your earned achievement badges (PM)",
            "  !weather - Check current hunting weather in this channel",
            "  !daily - Claim your daily XP bonus (resets every 24h)",
            "",
            "SHOP COMMANDS:",
            "  !shop - View available items",
            "  !shop buy <item_id> - Purchase an item from the shop",
            "  !use <item_id> [target] - Use an item from your inventory",
            "  !give <item_id> <player> - Give an inventory item to another player",
            "",
            "DUCK TYPES:",
            "  Normal duck  - Standard XP",
            "  Golden duck  - Multiple HP, big XP reward",
            "  Fast duck    - Flies away quickly",
            "  Ninja duck   - Has a dodge chance! (!bef only)",
            "  Decoy duck   - !bang confiscates your gun, !bef rewards you",
            "  Boss duck    - High HP, cooperative kill, XP split among contributors",
            "  Flock        - Multiple ducks at once, shoot them one by one",
            "",
            "SHOP ITEMS:",
            "  Binoculars   - Reveals current duck type to you (PM)",
            "  Hunting Dog  - Retrieves a duck that flies away (active 12h)",
            "  Scope        - +20% accuracy for your next 5 shots (active 12h)",
            "  Body Armor   - Absorbs your next XP loss event",
            "  Decoy Trap   - Target's next !bef fails with XP penalty",
            "  Mystery Box  - Random item from a weighted pool",
            "",
            "ADMIN COMMANDS:",
            "  !rearm <player|all> - Give player a gun",
            "  !disarm <player> - Confiscate player's gun",
            "  !ignore <player> - Ignore player's commands",
            "  !unignore <player> - Unignore player",
            "  !ducklaunch [duck_type] - Force spawn a duck (normal, golden, fast, ninja, boss, decoy)",
            "  !join #channel - Make bot join a channel",
            "  !part #channel - Make bot leave a channel",
            "",
            "TIPS:",
            "- Ducks spawn randomly, including flocks and rare boss ducks!",
            "- Weather affects jam chance, accuracy, and XP — use !weather to check",
            "- Claim !daily every day to build your streak and earn bonus XP",
            "- Boss ducks require teamwork — all contributors get XP!",
            "- Decoy ducks: !bef them for a reward, don't !bang!",
            "",
            "Good luck hunting! 🦆"
        ]
        
        # Send lines as PM with a small delay to avoid IRC excess flood
        for line in help_lines:
            self.send_message(nick, line)
            await asyncio.sleep(0.4)

    async def handle_reloadbot(self, nick, channel):
        """Admin-only: restart the bot process via PM (!reload) to apply code changes."""
        if channel.startswith('#'):
            self.send_message(channel, f"{nick} > Use this command in PM only.")
            return
        self.send_message(nick, "Restarting bot now...")
        try:
            self.db.save_database()
        except Exception:
            pass
        self.restart_requested = True
        self.shutdown_requested = True

    # -------------------------------------------------------------------
    # New command handlers
    # -------------------------------------------------------------------

    async def handle_daily(self, nick, channel, player):
        """Handle !daily — claim a daily XP bonus once per 24 hours."""
        import random as _random
        now = time.time()
        last_daily = player.get('last_daily', 0)
        if now - last_daily < 86400:
            remaining = int(86400 - (now - last_daily))
            h, rem = divmod(remaining, 3600)
            m = rem // 60
            self.send_message(channel,
                f"{nick} > ⏳ Daily already claimed! Come back in {h}h {m}m.")
            return

        # Track daily streak
        import datetime as _dt
        today = _dt.date.today().isoformat()
        last_date = player.get('last_daily_date', '')
        yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
        if last_date == yesterday:
            player['daily_streak'] = player.get('daily_streak', 0) + 1
        elif last_date != today:
            player['daily_streak'] = 1
        player['last_daily_date'] = today
        player['last_daily'] = now

        daily_streak = player.get('daily_streak', 1)
        xp_bonus = _random.randint(10, 25) + (daily_streak - 1) * 2  # Streak bonus
        player['xp'] = player.get('xp', 0) + xp_bonus

        streak_msg = f" (🔥 {daily_streak}-day streak!" + (
            " Bonus XP included!" if daily_streak > 1 else '') + ')' if daily_streak > 0 else ""
        self.send_message(channel,
            f"{nick} > 🎁 Daily bonus claimed! +{xp_bonus} XP{streak_msg} Come back tomorrow!")

        new_ach = self.game._check_achievements(player, 'daily')
        for ach in new_ach:
            self.send_message(channel,
                f"🏆 {nick} unlocked: {ach['icon']} {ach['name']} — {ach['description']}!")
        self.db.save_database()

    async def handle_effects(self, nick, channel, player):
        """Handle !effects — show active temporary effects with remaining time."""
        effects = player.get('temporary_effects', [])
        now = time.time()
        active = [e for e in effects if isinstance(e, dict) and e.get('expires_at', 0) > now]
        if not active:
            self.send_message(channel, f"{nick} > No active effects. Visit !shop to get some!")
            return
        parts = []
        for e in active:
            remaining = int(e.get('expires_at', 0) - now)
            h, rem = divmod(remaining, 3600)
            m, s   = divmod(rem, 60)
            name = e.get('name', e.get('type', 'Unknown').replace('_', ' ').title())
            if h > 0:
                time_str = f"{h}h {m}m"
            elif m > 0:
                time_str = f"{m}m {s}s"
            else:
                time_str = f"{s}s"
            extra = ''
            if e.get('type') == 'temporary_accuracy':
                extra = f" ({e.get('shots_remaining', '?')} shots left)"
            parts.append(f"{name}{extra} [{time_str}]")
        self.send_message(channel, f"{nick} > Active effects: {' | '.join(parts)}")

    async def handle_achievements(self, nick, channel, player):
        """Handle !achievements — show earned achievement badges via PM."""
        achievements = player.get('achievements', [])
        if not achievements:
            self.send_message(channel,
                f"{nick} > No achievements yet! Keep hunting! 🦆")
            return
        self.send_message(nick, f"=== {nick}'s Achievements ({len(achievements)} earned) ===")
        for ach in achievements:
            if isinstance(ach, dict):
                self.send_message(nick,
                    f"  {ach.get('icon','🏆')} {ach.get('name','?')} — {ach.get('description','')}")
        if channel.startswith('#'):
            self.send_message(channel,
                f"{nick} > Check your PM for your {len(achievements)} achievement(s)! 🏆")

    async def handle_inv(self, nick, channel, player):
        """Handle !inv — compact inventory display."""
        inventory = player.get('inventory', {})
        if not inventory:
            self.send_message(channel,
                f"{nick} > Inventory empty. Use !shop to buy items!")
            return
        parts = []
        for item_id_str, qty in inventory.items():
            item = self.shop.get_item(int(item_id_str))
            if item:
                parts.append(f"{item['name']} x{qty} (#{item_id_str})")
            else:
                parts.append(f"Item #{item_id_str} x{qty}")
        self.send_message(channel, f"{nick} > 🎒 Inventory: {' | '.join(parts)}")

    async def handle_profile(self, nick, channel, player):
        """Handle !profile — detailed player stat card sent via PM."""
        level_info   = self.levels.get_player_level_info(player)
        level        = level_info['level']
        level_name   = level_info['name']
        xp           = player.get('xp', 0)
        ducks_shot   = player.get('ducks_shot', 0)
        ducks_bef    = player.get('ducks_befriended', 0)
        accuracy     = player.get('accuracy', 75)
        shots_fired  = player.get('shots_fired', 0)
        shots_missed = player.get('shots_missed', 0)
        hit_rate     = round(ducks_shot / shots_fired * 100, 1) if shots_fired else 0
        streak       = player.get('current_streak', 0)
        best_streak  = player.get('best_streak', 0)
        daily_streak = player.get('daily_streak', 0)
        achievements = player.get('achievements', [])
        xp_needed    = level_info.get('needed_for_next', 0)
        next_name    = level_info.get('next_level_name', 'Max')
        gun_status   = '🔫 Armed' if not player.get('gun_confiscated', False) else '🚫 Confiscated'
        current_ammo = player.get('current_ammo', 0)
        bullets_per  = player.get('bullets_per_magazine', 6)
        magazines    = player.get('magazines', 0)
        jam_chance   = player.get('jam_chance', 0)
        total_spent  = player.get('total_xp_spent', 0)

        lines = [
            f"=== 🦆 {nick}'s Hunter Profile ===",
            f"  Level   : {level} — {level_name}",
            f"  XP      : {xp}" + (f" (need {xp_needed} for {next_name})" if xp_needed else " (Max level!)"),
            f"  Ducks   : {ducks_shot} shot, {ducks_bef} befriended",
            f"  Accuracy: {accuracy}% (hit rate {hit_rate}%)",
            f"  Streak  : {streak} current | {best_streak} best",
            f"  Daily   : {daily_streak}-day streak",
            f"  Gun     : {gun_status} | {current_ammo}/{bullets_per} | {magazines} mags | {jam_chance}% jam",
            f"  Spending: {total_spent} XP spent in shop",
            f"  Badges  : {len(achievements)} achievement(s) earned",
        ]
        # Weather at glance
        try:
            weather = self.game.get_channel_weather(channel)
            from .game import WEATHER_STATES
            w_cfg = WEATHER_STATES.get(weather['state'], WEATHER_STATES['clear'])
            lines.append(f"  Weather : {w_cfg['name']}")
        except Exception:
            pass
        # Inventory
        inventory = player.get('inventory', {})
        if inventory:
            inv_parts = []
            for iid, qty in inventory.items():
                item = self.shop.get_item(int(iid))
                inv_parts.append(f"{item['name']} x{qty}" if item else f"#{iid} x{qty}")
            lines.append(f"  Items   : {' | '.join(inv_parts)}")
        # Active effects
        now = time.time()
        active_fx = [e for e in player.get('temporary_effects', [])
                     if isinstance(e, dict) and e.get('expires_at', 0) > now]
        if active_fx:
            fx_names = [e.get('name', e.get('type', '?')) for e in active_fx]
            lines.append(f"  Effects : {' | '.join(fx_names)}")
        for line in lines:
            self.send_message(nick, line)
        if channel.startswith('#'):
            self.send_message(channel, f"{nick} > Profile sent to your PM! 📬")

    async def handle_weather(self, nick, channel):
        """Handle !weather — show current channel weather."""
        try:
            from .game import WEATHER_STATES
            weather = self.game.get_channel_weather(channel)
            w_cfg   = WEATHER_STATES.get(weather['state'], WEATHER_STATES['clear'])
            expires_in = int(weather.get('expires_at', 0) - time.time())
            h, rem = divmod(max(0, expires_in), 3600)
            m = rem // 60
            time_left = f"{h}h {m}m" if h > 0 else f"{m}m"
            modifiers = []
            if w_cfg['jam_modifier']:      modifiers.append(f"+{w_cfg['jam_modifier']}% jam")
            if w_cfg['accuracy_modifier']: modifiers.append(f"{w_cfg['accuracy_modifier']}% accuracy")
            if w_cfg['xp_modifier'] != 1:  modifiers.append(f"{w_cfg['xp_modifier']}x XP")
            mod_str = (" | " + ", ".join(modifiers)) if modifiers else " | No modifiers"
            self.send_message(channel,
                f"{nick} > {w_cfg['name']}{mod_str} | Changes in ~{time_left}")
        except Exception as e:
            self.logger.error(f"Error in handle_weather: {e}")
            self.send_message(channel, f"{nick} > Could not retrieve weather data.")

    
    
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
                        active_spares = max(0, effect.get('magazines_restored', 1) - 1)
                        inv_spares = 0
                        inventory = player.get('inventory', {})
                        for item_id_str, qty in inventory.items():
                            if qty > 0:
                                try:
                                    item = self.shop.get_item(int(item_id_str))
                                    if item and item.get('type') == 'magazine':
                                        inv_spares += (qty * item.get('amount', 1))
                                except ValueError:
                                    pass
                        total_spares = active_spares + inv_spares
                        
                        message = self.messages.get('use_buy_gun_back', nick=nick,
                            ammo_restored=effect.get('ammo_restored', 0),
                            total_spares=total_spares)
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
                if is_private_msg:
                    for _ch, _pn, p in self.db.iter_all_players():
                        if p.get('gun_confiscated', False):
                            p['gun_confiscated'] = False
                            self.levels.update_player_magazines(p)
                            p['current_ammo'] = p.get('bullets_per_magazine', 6)
                            rearmed_count += 1
                else:
                    for _pn, p in self.db.get_players_for_channel(channel).items():
                        if p.get('gun_confiscated', False):
                            p['gun_confiscated'] = False
                            self.levels.update_player_magazines(p)
                            p['current_ammo'] = p.get('bullets_per_magazine', 6)
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
            player = self.db.get_player(nick, channel)
            
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
        player = self.db.get_player(target, channel)

        action_func(player, target)
        
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
            action_func=lambda player, target: (player.update({'ignored': True}), self.db.set_global_ignored(target, True))
        )
    
    async def handle_unignore(self, nick, channel, args):
        """Handle !unignore command (admin only) - supports private messages"""
        self._send_admin_usage_or_execute(
            nick, channel, args,
            usage_command='usage_unignore',
            private_usage='!unignore <player>',
            message_key='admin_unignore',
            action_func=lambda player, target: (player.update({'ignored': False}), self.db.set_global_ignored(target, False))
        )

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
        
        # Normalize/sanitize target channel (IRC channels are case-insensitive)
        target_channel = sanitize_user_input(
            target_channel,
            max_length=100,
            allowed_chars='#&+!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-[]{}^`|\\'
        )
        target_channel_key = self._channel_key(target_channel)

        # Validate target channel
        if target_channel_key not in self.channels_joined:
            if is_private_msg:
                self.send_message(channel, f"{nick} > Channel {target_channel} is not available for duckhunt")
            else:
                message = self.messages.get('admin_ducklaunch_not_enabled')
                self.send_message(channel, message)
            return
        
        # Validate duck type
        duck_type_arg = duck_type_arg.lower()
        valid_types = ["normal", "golden", "fast", "ninja", "decoy", "boss", "flock"]
        if duck_type_arg not in valid_types:
            self.send_message(channel, f"{nick} > Invalid duck type '{duck_type_arg}'. Valid types: {', '.join(valid_types)}")
            return
        
        # Force spawn the specified duck type
        import time
        import random
        
        if target_channel_key not in self.game.ducks:
            self.game.ducks[target_channel_key] = []
        
        current_time = time.time()
        duck_id = f"{duck_type_arg}_duck_{int(current_time)}_{random.randint(1000, 9999)}"

        if duck_type_arg == "flock":
            import asyncio
            asyncio.create_task(self.game._spawn_flock(target_channel, target_channel_key, current_time))
            if is_private_msg:
                self.send_message(channel, f"{nick} > Launched flock in {target_channel_key}")
            return
            
        elif duck_type_arg == "boss":
            min_hp = int(self.get_config('duck_types.boss.min_hp', 8))
            max_hp = int(self.get_config('duck_types.boss.max_hp', 15))
            hp = random.randint(min_hp, max_hp)
            duck = {
                'id': duck_id, 'spawn_time': current_time, 'channel': target_channel_key,
                'duck_type': 'boss', 'max_hp': hp, 'current_hp': hp, 'contributors': {}
            }
            msg = self.messages.get('boss_duck_spawn', hp=hp)
            if msg.startswith('[Missing'):
                msg = f"💀 A BOSS DUCK has appeared with {hp} HP! Everyone !bang to take it down!"
            
            try:
                from .game import WEATHER_STATES
                weather = self.game.get_channel_weather(target_channel_key)
                w_cfg = WEATHER_STATES.get(weather['state'], WEATHER_STATES['clear'])
                msg += f" [Weather: {w_cfg['name']}]"
            except Exception:
                pass
                
            self.game.ducks[target_channel_key].append(duck)
            self.send_message(target_channel_key, msg)
            if is_private_msg:
                self.send_message(channel, f"{nick} > Launched boss duck in {target_channel_key}")
            return

        elif duck_type_arg == "golden":
            min_hp_val = self.get_config('duck_types.golden.min_hp', 3)
            max_hp_val = self.get_config('duck_types.golden.max_hp', 5)
            min_hp = int(min_hp_val) if min_hp_val is not None else 3
            max_hp = int(max_hp_val) if max_hp_val is not None else 5
            hp = random.randint(min_hp, max_hp)
            duck = {
                'id': duck_id, 'spawn_time': current_time, 'channel': target_channel_key,
                'duck_type': 'golden', 'max_hp': hp, 'current_hp': hp
            }
        elif duck_type_arg == "ninja":
            dodge = float(self.get_config('duck_types.ninja.dodge_chance', 0.35))
            duck = {
                'id': duck_id, 'spawn_time': current_time, 'channel': target_channel_key,
                'duck_type': 'ninja', 'max_hp': 1, 'current_hp': 1, 'dodge_chance': dodge
            }
        else:
            # normal, fast, decoy have 1 HP
            duck = {
                'id': duck_id, 'spawn_time': current_time, 'channel': target_channel_key,
                'duck_type': duck_type_arg, 'max_hp': 1, 'current_hp': 1
            }

        self.game.ducks[target_channel_key].append(duck)
        # Use the preferred spawn template (ornate dotted prefix) to match admin-launched output
        duck_message = self.messages.get_choice('duck_spawn', match='·.¸¸.·´¯`·.¸¸.·´¯`·.')
        
        try:
            from .game import WEATHER_STATES
            weather = self.game.get_channel_weather(target_channel_key)
            w_cfg = WEATHER_STATES.get(weather['state'], WEATHER_STATES['clear'])
            duck_message += f" [Weather: {w_cfg['name']}]"
        except Exception:
            pass
        
        # Send duck spawn message to target channel
        self.send_message(target_channel_key, duck_message)
        
        # Send confirmation to admin (either in channel or private message)
        if is_private_msg:
            self.send_message(channel, f"{nick} > Launched {duck_type_arg} duck in {target_channel_key}")
    
    
    async def handle_join_channel(self, nick, channel, args):
        """Handle !join command (admin only) - join a channel"""
        if not args:
            self.send_message(channel, f"{nick} > Usage: !join <#channel>")
            return
        
        target_channel = args[0]
        
        # Validate channel format
        if not target_channel.startswith('#'):
            self.send_message(channel, f"{nick} > Invalid channel format. Must start with #")
            return
        
        # Normalize the channel key (IRC channels are case-insensitive)
        target_channel_key = self._channel_key(target_channel)
        
        # Check if already joined (compare normalized keys)
        if target_channel_key in self.channels_joined:
            self.send_message(channel, f"{nick} > Already in {target_channel}")
            return
        
        # Send JOIN command and register as pending (server confirms via JOIN event)
        if self.send_raw(f"JOIN {target_channel}"):
            if not hasattr(self, 'pending_joins') or not isinstance(self.pending_joins, dict):
                self.pending_joins = {}
            self.pending_joins[target_channel_key] = None
            self.send_message(channel, f"{nick} > Joining {target_channel}...")
            self.logger.info(f"Admin {nick} requested bot join {target_channel}")
        else:
            self.send_message(channel, f"{nick} > Failed to send JOIN for {target_channel}")
    
    async def handle_part_channel(self, nick, channel, args):
        """Handle !part command (admin only) - leave a channel"""
        if not args:
            self.send_message(channel, f"{nick} > Usage: !part <#channel>")
            return
        
        target_channel = args[0]
        
        # Validate channel format
        if not target_channel.startswith('#'):
            self.send_message(channel, f"{nick} > Invalid channel format. Must start with #")
            return
        
        # Normalize channel key (IRC channels are case-insensitive)
        target_channel_key = self._channel_key(target_channel)
        
        # Check if in channel (compare normalized keys)
        if target_channel_key not in self.channels_joined:
            self.send_message(channel, f"{nick} > Not in {target_channel}")
            return
        
        # Send PART command and remove from joined set
        if self.send_raw(f"PART {target_channel_key}"):
            self.channels_joined.discard(target_channel_key)
            self.send_message(channel, f"{nick} > Left {target_channel}")
            self.logger.info(f"Admin {nick} made bot leave {target_channel}")
        else:
            self.send_message(channel, f"{nick} > Failed to leave {target_channel}")
    
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

            # If restart was requested (admin command), re-exec the process.
            if self.restart_requested:
                self.logger.warning("Restart requested; re-executing process...")
                os.execv(sys.executable, [sys.executable] + sys.argv)
    
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
    
