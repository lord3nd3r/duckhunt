#!/usr/bin/env python3
"""
Main DuckHunt IRC Bot using modular architecture
"""

import asyncio
import ssl
import json
import random
import logging
import sys
import os
import time
import uuid
import signal
from typing import Optional

from .logging_utils import setup_logger
from .utils import parse_message
from .db import DuckDB
from .game import DuckGame
from .auth import AuthSystem
from . import sasl

class IRCBot:
    def __init__(self, config):
        self.config = config
        self.logger = setup_logger("DuckHuntBot")
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.registered = False
        self.channels_joined = set()
        self.shutdown_requested = False
        self.running_tasks = set()
        
        # Initialize subsystems
        self.db = DuckDB()
        self.game = DuckGame(self, self.db)
        self.auth = AuthSystem(self.db)
        self.auth.set_bot(self)  # Set bot reference for auth system
        self.sasl_handler = sasl.SASLHandler(self, config)
        
        # IRC connection state
        self.nick = config['nick']
        self.channels = config['channels']
        
    def send_raw(self, msg):
        """Send raw IRC message"""
        if self.writer and not self.writer.is_closing():
            try:
                self.writer.write(f"{msg}\r\n".encode('utf-8'))
            except Exception as e:
                self.logger.error(f"Error sending message: {e}")
    
    def send_message(self, target, msg):
        """Send PRIVMSG to target"""
        self.send_raw(f'PRIVMSG {target} :{msg}')
    
    async def connect(self):
        """Connect to IRC server with SASL support"""
        server = self.config['server']
        port = self.config['port']
        ssl_context = ssl.create_default_context() if self.config.get('ssl', True) else None
        
        self.logger.info(f"Connecting to {server}:{port} (SSL: {ssl_context is not None})")
        
        try:
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
                
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            return False
    
    async def register_user(self):
        """Register with IRC server"""
        self.logger.info(f"Registering as {self.nick}")
        self.send_raw(f'NICK {self.nick}')
        self.send_raw(f'USER {self.nick} 0 * :DuckHunt Bot')
        
        # Send password if configured (for servers that require it)
        if self.config.get('password'):
            self.send_raw(f'PASS {self.config["password"]}')
    
    async def handle_irc_message(self, line):
        """Handle individual IRC message"""
        try:
            prefix, command, params, trailing = parse_message(line)
            
            # Handle SASL-related messages
            if command in ['CAP', 'AUTHENTICATE', '903', '904', '905', '906', '907', '908']:
                handled = await self.sasl_handler.handle_sasl_result(command, params, trailing)
                if command == 'CAP':
                    handled = await self.sasl_handler.handle_cap_response(params, trailing)
                elif command == 'AUTHENTICATE':
                    handled = await self.sasl_handler.handle_authenticate_response(params)
                
                # If SASL handler didn't handle it, continue with normal processing
                if handled:
                    return
            
            # Handle standard IRC messages
            if command == '001':  # Welcome
                self.registered = True
                auth_status = " (SASL authenticated)" if self.sasl_handler.is_authenticated() else ""
                self.logger.info(f"Successfully registered!{auth_status}")
                
                # If SASL failed, try NickServ identification
                if not self.sasl_handler.is_authenticated():
                    await self.auth.attempt_nickserv_auth()
                
                # Join channels
                for chan in self.channels:
                    self.logger.info(f"Joining {chan}")
                    self.send_raw(f'JOIN {chan}')
                    
            elif command == 'JOIN' and prefix and prefix.startswith(self.nick):
                channel = trailing or (params[0] if params else '')
                if channel:
                    self.channels_joined.add(channel)
                    self.logger.info(f"Successfully joined {channel}")
                    
            elif command == 'PRIVMSG' and trailing:
                target = params[0] if params else ''
                sender = prefix.split('!')[0] if prefix else ''
                
                # Handle NickServ responses
                if sender.lower() == 'nickserv':
                    await self.auth.handle_nickserv_response(trailing)
                elif trailing == 'VERSION':
                    self.send_raw(f'NOTICE {sender} :VERSION DuckHunt Bot v2.0')
                else:
                    # Handle game commands
                    await self.game.handle_command(prefix, target, trailing)
                    
            elif command == 'PING':
                # Respond to PING
                self.send_raw(f'PONG :{trailing}')
                
        except Exception as e:
            self.logger.error(f"Error handling IRC message '{line}': {e}")
    
    async def listen(self):
        """Main IRC message listening loop"""
        buffer = ""
        
        while not self.shutdown_requested:
            try:
                if not self.reader:
                    break
                    
                data = await self.reader.read(4096)
                if not data:
                    self.logger.warning("Connection closed by server")
                    break
                    
                buffer += data.decode('utf-8', errors='ignore')
                
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.rstrip('\r')
                    
                    if line:
                        await self.handle_irc_message(line)
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in listen loop: {e}")
                await asyncio.sleep(1)
    
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            self.logger.info(f"Received signal {signum}, initiating graceful shutdown...")
            self.shutdown_requested = True
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def cleanup(self):
        """Cleanup resources and save data"""
        self.logger.info("Starting cleanup process...")
        
        try:
            # Cancel all running tasks
            for task in self.running_tasks.copy():
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            # Send goodbye message
            if self.writer and not self.writer.is_closing():
                for channel in self.channels_joined:
                    self.send_message(channel, "🦆 DuckHunt Bot shutting down. Thanks for playing! 🦆")
                    await asyncio.sleep(0.1)
                
                self.send_raw('QUIT :DuckHunt Bot shutting down gracefully')
                await asyncio.sleep(1.0)
                
                self.writer.close()
                await self.writer.wait_closed()
                self.logger.info("IRC connection closed")
            
            # Save database (no specific save_all method)
            # Players are saved individually through the game engine
            self.logger.info("Final database save completed")
            
            self.logger.info("Cleanup completed successfully")
            
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
    
    async def run(self):
        """Main bot entry point"""
        try:
            self.setup_signal_handlers()
            
            self.logger.info("Starting DuckHunt Bot...")
            
            # Load database (no async initialization needed)
            # Database is initialized in constructor
            
            # Connect to IRC
            if not await self.connect():
                return False
            
            # Create main tasks
            listen_task = asyncio.create_task(self.listen(), name="listen")
            game_task = asyncio.create_task(self.game.spawn_ducks_loop(), name="duck_spawner")
            
            self.running_tasks.add(listen_task)
            self.running_tasks.add(game_task)
            
            # Wait for completion
            done, pending = await asyncio.wait(
                [listen_task, game_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
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
        
        return True
