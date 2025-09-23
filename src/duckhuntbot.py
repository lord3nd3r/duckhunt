import asyncio
import ssl
import json
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
        def signal_handler(signum, frame):
            signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
            self.logger.info(f"🛑 Received {signal_name} (Ctrl+C), shutting down immediately...")
            self.shutdown_requested = True
            
            # Cancel all running tasks immediately
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
    
    async def handle_duckstats(self, nick, channel, player):
        """Handle !duckstats command - show player stats and inventory"""
        # Get player level info
        level_info = self.levels.get_player_level_info(player)
        level = self.levels.calculate_player_level(player)
        
        # Build stats message
        stats_parts = [
            f"Level {level} {level_info.get('name', 'Unknown')}",
            f"XP: {player.get('xp', 0)}",
            f"Ducks Shot: {player.get('ducks_shot', 0)}",
            f"Ducks Befriended: {player.get('ducks_befriended', 0)}",
            f"Accuracy: {player.get('accuracy', 65)}%",
            f"Ammo: {player.get('current_ammo', 0)}/{player.get('bullets_per_magazine', 6)}",
            f"Magazines: {player.get('magazines', 1)}"
        ]
        
        stats_message = f"{nick} > Stats: {' | '.join(stats_parts)}"
        self.send_message(channel, stats_message)
        
        # Show inventory if not empty
        inventory_info = self.shop.get_inventory_display(player)
        if not inventory_info["empty"]:
            items_text = []
            for item in inventory_info["items"]:
                items_text.append(f"{item['id']}: {item['name']} x{item['quantity']}")
            inventory_message = f"{nick} > Inventory: {' | '.join(items_text)}"
            self.send_message(channel, inventory_message)
    
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
            player['gun_confiscated'] = False
            
            # Update magazines based on player level
            self.levels.update_player_magazines(player)
            player['current_ammo'] = player.get('bullets_per_magazine', 6)
            
            message = self.messages.get('admin_rearm_player', target=target, admin=nick)
            self.send_message(channel, message)
        else:
            # Rearm the admin themselves
            player = self.db.get_player(nick)
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
        duck_message = self.messages.get('duck_spawn')
        
        # Only send the duck spawn message, no admin notification
        self.send_message(channel, duck_message)
    
    
    async def message_loop(self):
        """Main message processing loop with responsive shutdown"""
        try:
            while not self.shutdown_requested and self.reader:
                try:
                    # Use a timeout on readline to make it more responsive to shutdown
                    line = await asyncio.wait_for(self.reader.readline(), timeout=1.0)
                except asyncio.TimeoutError:
                    # Check shutdown flag and continue
                    continue
                    
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
        """Main bot loop with fast shutdown handling"""
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
            
            # Wait for shutdown signal or task completion with frequent checks
            while not self.shutdown_requested:
                done, pending = await asyncio.wait(
                    [game_task, message_task],
                    timeout=0.1,  # Check every 100ms for shutdown
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # If any task completed, break out
                if done:
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
        """Close IRC connection quickly"""
        if self.writer:
            try:
                if not self.writer.is_closing():
                    # Send quit message quickly without waiting
                    try:
                        quit_message = self.config.get('quit_message', 'DuckHunt Bot shutting down')
                        self.send_raw(f"QUIT :{quit_message}")
                        await asyncio.sleep(0.1)  # Very brief wait
                    except:
                        pass  # Don't block on quit message
                    
                    self.writer.close()
                    await asyncio.wait_for(self.writer.wait_closed(), timeout=1.0)
                self.logger.info("🔌 IRC connection closed")
            except asyncio.TimeoutError:
                self.logger.warning("⚠️ Connection close timed out - forcing close")
            except Exception as e:
                self.logger.error(f"❌ Error closing connection: {e}")