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
from .utils import parse_irc_message, InputValidator, MessageManager
from .db import DuckDB
from .game import DuckGame
from .sasl import SASLHandler
from .shop import ShopManager


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
    
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            self.logger.info(f"🛑 Received {signal_name} (Ctrl+C), initiating graceful shutdown...")
            self.shutdown_requested = True
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def connect(self):
        """Connect to IRC server"""
        try:
            ssl_context = ssl.create_default_context() if self.config.get('ssl', False) else None
            self.reader, self.writer = await asyncio.open_connection(
                self.config['server'], 
                self.config['port'],
                ssl=ssl_context
            )
            self.logger.info(f"Connected to {self.config['server']}:{self.config['port']}")
        except Exception as e:
            self.logger.error(f"Failed to connect: {e}")
            raise
    
    def send_raw(self, msg):
        """Send raw IRC message"""
        if self.writer:
            self.writer.write(f"{msg}\r\n".encode('utf-8'))
    
    def send_message(self, target, msg):
        """Send message to target (channel or user)"""
        self.send_raw(f"PRIVMSG {target} :{msg}")
    
    async def register_user(self):
        """Register user with IRC server"""
        if self.config.get('password'):
            self.send_raw(f"PASS {self.config['password']}")
        
        self.send_raw(f"NICK {self.config['nick']}")
        self.send_raw(f"USER {self.config['nick']} 0 * :{self.config['nick']}")
    
    async def handle_message(self, prefix, command, params, trailing):
        """Handle incoming IRC messages"""
        if command == "001":  # Welcome message
            self.registered = True
            self.logger.info("Successfully registered with IRC server")
            
            # Join channels
            for channel in self.config.get('channels', []):
                self.send_raw(f"JOIN {channel}")
                self.channels_joined.add(channel)
        
        elif command == "PRIVMSG":
            if len(params) >= 1:
                target = params[0]
                message = trailing or ""
                await self.handle_command(prefix, target, message)
        
        elif command == "PING":
            self.send_raw(f"PONG :{trailing}")
    
    async def handle_command(self, user, channel, message):
        """Handle bot commands"""
        if not message.startswith('!'):
            return
        
        parts = message[1:].split()
        if not parts:
            return
        
        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []
        nick = user.split('!')[0] if '!' in user else user
        
        player = self.db.get_player(nick)
        
        # Check if player is ignored (unless it's an admin)
        if player.get('ignored', False) and not self.is_admin(user):
            return
        
        if cmd == "bang":
            await self.handle_bang(nick, channel, player)
        elif cmd == "bef" or cmd == "befriend":
            await self.handle_bef(nick, channel, player)
        elif cmd == "reload":
            await self.handle_reload(nick, channel, player)
        elif cmd == "shop":
            await self.handle_shop(nick, channel, player, args)
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
        elif cmd == "reloadshop" and self.is_admin(user):
            await self.handle_reloadshop(nick, channel, args)
    
    async def handle_bang(self, nick, channel, player):
        """Handle !bang command"""
        # Check if gun is confiscated
        if player.get('gun_confiscated', False):
            message = self.messages.get('bang_not_armed', nick=nick)
            self.send_message(channel, message)
            return
        
        # Check ammo
        if player['ammo'] <= 0:
            message = self.messages.get('bang_no_ammo', nick=nick)
            self.send_message(channel, message)
            return
        
        # Check for duck
        if channel not in self.game.ducks or not self.game.ducks[channel]:
            # Wild shot - gun confiscated
            player['ammo'] -= 1
            player['gun_confiscated'] = True
            message = self.messages.get('bang_no_duck', nick=nick)
            self.send_message(channel, message)
            self.db.save_database()
            return
        
        # Shoot at duck
        player['ammo'] -= 1
        
        # Calculate hit chance
        hit_chance = player.get('accuracy', 65) / 100.0
        if random.random() < hit_chance:
            # Hit! Remove the duck
            duck = self.game.ducks[channel].pop(0)
            xp_gained = 10
            player['xp'] = player.get('xp', 0) + xp_gained
            player['ducks_shot'] = player.get('ducks_shot', 0) + 1
            player['accuracy'] = min(player.get('accuracy', 65) + 1, 100)
            
            message = self.messages.get('bang_hit', 
                                      nick=nick, 
                                      xp_gained=xp_gained, 
                                      ducks_shot=player['ducks_shot'])
            self.send_message(channel, message)
        else:
            # Miss! Duck stays in the channel
            player['accuracy'] = max(player.get('accuracy', 65) - 2, 10)
            message = self.messages.get('bang_miss', nick=nick)
            self.send_message(channel, message)
        
        self.db.save_database()
    
    async def handle_bef(self, nick, channel, player):
        """Handle !bef (befriend) command"""
        # Check for duck
        if channel not in self.game.ducks or not self.game.ducks[channel]:
            message = self.messages.get('bef_no_duck', nick=nick)
            self.send_message(channel, message)
            return
        
        # Check befriend success rate from config (default 75%)
        success_rate_config = self.get_config('befriend_success_rate', 75)
        try:
            success_rate = float(success_rate_config) / 100.0
        except (ValueError, TypeError):
            success_rate = 0.75  # 75% default
        
        if random.random() < success_rate:
            # Success - befriend the duck
            duck = self.game.ducks[channel].pop(0)
            
            # Lower XP gain than shooting (5 instead of 10)
            xp_gained = 5
            player['xp'] = player.get('xp', 0) + xp_gained
            player['ducks_befriended'] = player.get('ducks_befriended', 0) + 1
            
            message = self.messages.get('bef_success', 
                                      nick=nick, 
                                      xp_gained=xp_gained, 
                                      ducks_befriended=player['ducks_befriended'])
            self.send_message(channel, message)
        else:
            # Failure - duck flies away, remove from channel
            duck = self.game.ducks[channel].pop(0)
            
            message = self.messages.get('bef_failed', nick=nick)
            self.send_message(channel, message)
        
        self.db.save_database()
    
    async def handle_reload(self, nick, channel, player):
        """Handle !reload command"""
        if player.get('gun_confiscated', False):
            message = self.messages.get('reload_not_armed', nick=nick)
            self.send_message(channel, message)
            return
        
        if player['ammo'] >= player.get('max_ammo', 6):
            message = self.messages.get('reload_already_loaded', nick=nick)
            self.send_message(channel, message)
            return
        
        if player.get('chargers', 2) <= 0:
            message = self.messages.get('reload_no_chargers', nick=nick)
            self.send_message(channel, message)
            return
        
        player['ammo'] = player.get('max_ammo', 6)
        player['chargers'] = player.get('chargers', 2) - 1
        
        message = self.messages.get('reload_success', 
                                  nick=nick, 
                                  ammo=player['ammo'], 
                                  max_ammo=player.get('max_ammo', 6),
                                  chargers=player['chargers'])
        self.send_message(channel, message)
        self.db.save_database()
    
    async def handle_shop(self, nick, channel, player, args=None):
        """Handle !shop command"""
        # Handle buying: !shop buy <item_id>
        if args and len(args) >= 2 and args[0].lower() == "buy":
            try:
                item_id = int(args[1])
                await self.handle_shop_buy(nick, channel, player, item_id)
                return
            except (ValueError, IndexError):
                message = self.messages.get('shop_buy_usage', nick=nick)
                self.send_message(channel, message)
                return
        
        # Display shop items
        items = []
        for item_id, item in self.shop.get_items().items():
            item_text = self.messages.get('shop_item_format',
                                        id=item_id,
                                        name=item['name'],
                                        price=item['price'])
            items.append(item_text)
        
        shop_text = self.messages.get('shop_display',
                                    items=" | ".join(items),
                                    xp=player.get('xp', 0))
        
        self.send_message(channel, shop_text)
    
    async def handle_shop_buy(self, nick, channel, player, item_id):
        """Handle buying an item from the shop"""
        # Use ShopManager to handle the purchase
        result = self.shop.purchase_item(player, item_id)
        
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
            else:
                message = f"{nick} > Error: {result['message']}"
            
            self.send_message(channel, message)
            return
        
        # Purchase successful
        message = self.messages.get('shop_buy_success',
                                  nick=nick,
                                  item_name=result["item_name"],
                                  price=result["price"],
                                  remaining_xp=result["remaining_xp"])
        self.send_message(channel, message)
        self.db.save_database()
    
    async def handle_duckhelp(self, nick, channel, player):
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
    
    async def handle_rearm(self, nick, channel, args):
        """Handle !rearm command (admin only)"""
        if args:
            target = args[0].lower()
            player = self.db.get_player(target)
            player['gun_confiscated'] = False
            player['ammo'] = player.get('max_ammo', 6)
            player['chargers'] = 2
            message = self.messages.get('admin_rearm_player', target=target, admin=nick)
            self.send_message(channel, message)
        else:
            # Rearm everyone
            for player_data in self.db.players.values():
                player_data['gun_confiscated'] = False
                player_data['ammo'] = player_data.get('max_ammo', 6)
                player_data['chargers'] = 2
            message = self.messages.get('admin_rearm_all', admin=nick)
            self.send_message(channel, message)
        
        self.db.save_database()
    
    async def handle_disarm(self, nick, channel, args):
        """Handle !disarm command (admin only)"""
        if not args:
            message = self.messages.get('usage_disarm')
            self.send_message(channel, message)
            return
        
        target = args[0].lower()
        player = self.db.get_player(target)
        player['gun_confiscated'] = True
        
        message = self.messages.get('admin_disarm', target=target, admin=nick)
        self.send_message(channel, message)
        self.db.save_database()
    
    async def handle_ignore(self, nick, channel, args):
        """Handle !ignore command (admin only)"""
        if not args:
            message = self.messages.get('usage_ignore')
            self.send_message(channel, message)
            return
        
        target = args[0].lower()
        player = self.db.get_player(target)
        player['ignored'] = True
        
        message = self.messages.get('admin_ignore', target=target, admin=nick)
        self.send_message(channel, message)
        self.db.save_database()
    
    async def handle_unignore(self, nick, channel, args):
        """Handle !unignore command (admin only)"""
        if not args:
            message = self.messages.get('usage_unignore')
            self.send_message(channel, message)
            return
        
        target = args[0].lower()
        player = self.db.get_player(target)
        player['ignored'] = False
        
        message = self.messages.get('admin_unignore', target=target, admin=nick)
        self.send_message(channel, message)
        self.db.save_database()
    
    async def handle_ducklaunch(self, nick, channel, args):
        """Handle !ducklaunch command (admin only)"""
        if channel not in self.channels_joined:
            message = self.messages.get('admin_ducklaunch_not_enabled')
            self.send_message(channel, message)
            return
        
        # Force spawn a duck
        if channel not in self.game.ducks:
            self.game.ducks[channel] = []
        self.game.ducks[channel].append({"spawn_time": time.time()})
        admin_message = self.messages.get('admin_ducklaunch', admin=nick)
        duck_message = self.messages.get('duck_spawn')
        
        self.send_message(channel, admin_message)
        self.send_message(channel, duck_message)
    
    async def handle_reloadshop(self, nick, channel, args):
        """Handle !reloadshop admin command"""
        old_count = len(self.shop.get_items())
        new_count = self.shop.reload_items()
        
        message = f"[ADMIN] Shop reloaded by {nick} - {new_count} items loaded"
        self.send_message(channel, message)
        self.logger.info(f"Shop reloaded by admin {nick}: {old_count} -> {new_count} items")
    
    
    async def message_loop(self):
        """Main message processing loop"""
        try:
            while not self.shutdown_requested and self.reader:
                line = await self.reader.readline()
                if not line:
                    break
                
                line = line.decode('utf-8').strip()
                if not line:
                    continue
                
                try:
                    prefix, command, params, trailing = parse_irc_message(line)
                    await self.handle_message(prefix, command, params, trailing)
                except Exception as e:
                    self.logger.error(f"Error processing message '{line}': {e}")
        
        except asyncio.CancelledError:
            self.logger.info("Message loop cancelled")
        except Exception as e:
            self.logger.error(f"Message loop error: {e}")
        finally:
            self.logger.info("Message loop ended")
    
    async def run(self):
        """Main bot loop with improved shutdown handling"""
        self.setup_signal_handlers()
        
        game_task = None
        message_task = None
        
        try:
            await self.connect()
            await self.register_user()
            
            # Start game loops
            game_task = asyncio.create_task(self.game.start_game_loops())
            message_task = asyncio.create_task(self.message_loop())
            
            self.logger.info("🦆 Bot is now running! Press Ctrl+C to stop.")
            
            # Wait for shutdown signal or task completion
            done, pending = await asyncio.wait(
                [game_task, message_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # Cancel remaining tasks
            for task in pending:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        self.logger.debug(f"Task cancelled: {task}")
                    except asyncio.TimeoutError:
                        self.logger.debug(f"Task timed out: {task}")
        
        except asyncio.CancelledError:
            self.logger.info("🛑 Main loop cancelled")
        except Exception as e:
            self.logger.error(f"❌ Bot error: {e}")
        finally:
            self.logger.info("🔄 Final cleanup...")
            
            # Ensure tasks are cancelled
            for task in [game_task, message_task]:
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            # Final database save
            try:
                self.db.save_database()
                self.logger.info("💾 Database saved")
            except Exception as e:
                self.logger.error(f"❌ Error saving database: {e}")
            
            # Close IRC connection
            await self._close_connection()
            
            self.logger.info("✅ Bot shutdown complete")
    
    async def _graceful_shutdown(self):
        """Perform graceful shutdown steps"""
        try:
            # Send quit message to IRC
            if self.writer and not self.writer.is_closing():
                self.logger.info("📤 Sending QUIT message to IRC...")
                quit_message = self.config.get('quit_message', 'DuckHunt Bot shutting down')
                self.send_raw(f"QUIT :{quit_message}")
                
                # Give IRC server time to process quit
                await asyncio.sleep(0.5)
            
            # Save database
            self.logger.info("💾 Saving database...")
            self.db.save_database()
            
        except Exception as e:
            self.logger.error(f"❌ Error during graceful shutdown: {e}")
    
    async def _close_connection(self):
        """Close IRC connection safely"""
        if self.writer:
            try:
                if not self.writer.is_closing():
                    self.writer.close()
                    await asyncio.wait_for(self.writer.wait_closed(), timeout=3.0)
                self.logger.info("🔌 IRC connection closed")
            except asyncio.TimeoutError:
                self.logger.warning("⚠️ Connection close timed out")
            except Exception as e:
                self.logger.error(f"❌ Error closing connection: {e}")