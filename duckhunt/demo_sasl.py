#!/usr/bin/env python3
"""
SASL Integration Demo for DuckHunt Bot
This script demonstrates how the modular SASL authentication works
"""

import asyncio
import json
import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.sasl import SASLHandler
from src.logging_utils import setup_logger

class MockBot:
    """Mock bot for testing SASL without IRC connection"""
    def __init__(self, config):
        self.config = config
        self.logger = setup_logger("MockBot")
        self.messages_sent = []
        
    def send_raw(self, message):
        """Mock send_raw that just logs the message"""
        self.messages_sent.append(message)
        self.logger.info(f"SEND: {message}")
        
    async def register_user(self):
        """Mock registration"""
        self.logger.info("Mock user registration completed")

async def demo_sasl_flow():
    """Demonstrate the SASL authentication flow"""
    print("🔐 SASL Authentication Flow Demo")
    print("=" * 50)
    
    # Load config
    with open('config.json') as f:
        config = json.load(f)
    
    # Override with test credentials for demo
    config['sasl'] = {
        'enabled': True,
        'username': 'testuser',
        'password': 'testpass123'
    }
    
    # Create mock bot and SASL handler
    bot = MockBot(config)
    sasl_handler = SASLHandler(bot, config)
    
    print("\n1️⃣  Starting SASL negotiation...")
    if await sasl_handler.start_negotiation():
        print("✅ SASL negotiation started successfully")
    else:
        print("❌ SASL negotiation failed to start")
        return
    
    print("\n2️⃣  Simulating server CAP response...")
    # Simulate server listing SASL capability
    params = ['*', 'LS', '*']
    trailing = 'sasl multi-prefix extended-join'
    await sasl_handler.handle_cap_response(params, trailing)
    
    print("\n3️⃣  Simulating server acknowledging SASL capability...")
    # Simulate server acknowledging SASL
    params = ['*', 'ACK']
    trailing = 'sasl'
    await sasl_handler.handle_cap_response(params, trailing)
    
    print("\n4️⃣  Simulating server ready for authentication...")
    # Simulate server ready for auth
    params = ['+']
    await sasl_handler.handle_authenticate_response(params)
    
    print("\n5️⃣  Simulating successful authentication...")
    # Simulate successful authentication
    params = ['DuckHunt']
    trailing = 'You are now logged in as duckhunt'
    await sasl_handler.handle_sasl_result('903', params, trailing)
    
    print(f"\n📤 Messages sent to server:")
    for i, msg in enumerate(bot.messages_sent, 1):
        print(f"  {i}. {msg}")
    
    print(f"\n🔍 Authentication status: {'✅ Authenticated' if sasl_handler.is_authenticated() else '❌ Not authenticated'}")
    
    print("\n" + "=" * 50)
    print("✨ SASL flow demonstration complete!")

async def demo_sasl_failure():
    """Demonstrate SASL failure handling"""
    print("\n\n🚫 SASL Failure Handling Demo")
    print("=" * 50)
    
    # Create mock bot with wrong credentials
    config = {
        'sasl': {
            'enabled': True,
            'username': 'testuser',
            'password': 'wrong_password'
        }
    }
    bot = MockBot(config)
    sasl_handler = SASLHandler(bot, config)
    
    print("\n1️⃣  Starting SASL with wrong credentials...")
    await sasl_handler.start_negotiation()
    
    # Simulate failed authentication
    params = ['DuckHunt']
    trailing = 'Invalid credentials'
    await sasl_handler.handle_sasl_result('904', params, trailing)
    
    print(f"\n🔍 Authentication status: {'✅ Authenticated' if sasl_handler.is_authenticated() else '❌ Not authenticated'}")
    print("✅ Failure handled gracefully - bot will fallback to NickServ")

if __name__ == '__main__':
    asyncio.run(demo_sasl_flow())
    asyncio.run(demo_sasl_failure())
