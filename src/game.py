"""
Simplified Game mechanics for DuckHunt Bot
Basic duck spawning and timeout only
"""

import asyncio
import random
import time
import logging


class DuckGame:
    """Simplified game mechanics - just duck spawning"""
    
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
        """Simple duck spawning loop"""
        try:
            while True:
                # Wait random time between spawns
                min_wait = self.bot.get_config('duck_spawn_min', 300)  # 5 minutes
                max_wait = self.bot.get_config('duck_spawn_max', 900)  # 15 minutes
                wait_time = random.randint(min_wait, max_wait)
                
                await asyncio.sleep(wait_time)
                
                # Spawn duck in random channel
                channels = list(self.bot.channels_joined)
                if channels:
                    channel = random.choice(channels)
                    await self.spawn_duck(channel)
        
        except asyncio.CancelledError:
            self.logger.info("Duck spawning loop cancelled")
    
    async def duck_timeout_loop(self):
        """Simple duck timeout loop"""
        try:
            while True:
                await asyncio.sleep(10)  # Check every 10 seconds
                
                current_time = time.time()
                channels_to_clear = []
                
                for channel, ducks in self.ducks.items():
                    ducks_to_remove = []
                    for duck in ducks:
                        if current_time - duck['spawn_time'] > self.bot.get_config('duck_timeout', 60):
                            ducks_to_remove.append(duck)
                    
                    for duck in ducks_to_remove:
                        ducks.remove(duck)
                        message = self.bot.messages.get('duck_flies_away')
                        self.bot.send_message(channel, message)
                    
                    if not ducks:
                        channels_to_clear.append(channel)
                
                # Clean up empty channels
                for channel in channels_to_clear:
                    if channel in self.ducks and not self.ducks[channel]:
                        del self.ducks[channel]
        
        except asyncio.CancelledError:
            self.logger.info("Duck timeout loop cancelled")
    
    async def spawn_duck(self, channel):
        """Spawn a duck in the channel"""
        if channel not in self.ducks:
            self.ducks[channel] = []
        
        # Don't spawn if there's already a duck
        if self.ducks[channel]:
            return
        
        duck = {
            'id': f"duck_{int(time.time())}_{random.randint(1000, 9999)}",
            'spawn_time': time.time(),
            'channel': channel
        }
        
        self.ducks[channel].append(duck)
        
        # Send spawn message
        message = self.bot.messages.get('duck_spawn')
        self.bot.send_message(channel, message)
        
        self.logger.info(f"Duck spawned in {channel}")