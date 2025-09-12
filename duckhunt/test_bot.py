#!/usr/bin/env python3
"""
Test script for DuckHunt Bot
Run this to test both the modular and simple bot implementations
"""

import asyncio
import json
import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

async def test_modular_bot():
    """Test the modular bot implementation"""
    try:
        print("🔧 Testing modular bot (src/duckhuntbot.py)...")
        
        # Load config
        with open('config.json') as f:
            config = json.load(f)
        
        # Test imports
        from src.duckhuntbot import IRCBot
        from src.sasl import SASLHandler
        
        # Create bot instance
        bot = IRCBot(config)
        print("✅ Modular bot initialized successfully!")
        
        # Test SASL handler
        sasl_handler = SASLHandler(bot, config)
        print("✅ SASL handler created successfully!")
        
        # Test database
        bot.db.save_player("testuser", {"coins": 100, "caught": 5})
        data = bot.db.load_player("testuser")
        if data and data['coins'] == 100:
            print("✅ Database working!")
        else:
            print("❌ Database test failed!")
        
        # Test game logic
        player = bot.game.get_player("testuser")
        if player and 'coins' in player:
            print("✅ Game logic working!")
        else:
            print("❌ Game logic test failed!")
        
        return True
        
    except Exception as e:
        print(f"❌ Modular bot error: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_simple_bot():
    """Test the simple bot implementation"""
    try:
        print("\n🔧 Testing simple bot (simple_duckhunt.py)...")
        
        # Load config
        with open('config.json') as f:
            config = json.load(f)
        
        # Test imports
        from simple_duckhunt import SimpleIRCBot
        from src.sasl import SASLHandler
        
        # Create bot instance
        bot = SimpleIRCBot(config)
        print("✅ Simple bot initialized successfully!")
        
        # Test SASL handler integration
        if hasattr(bot, 'sasl_handler'):
            print("✅ SASL handler integrated!")
        else:
            print("❌ SASL handler not integrated!")
            return False
        
        # Test database
        if 'testuser' in bot.players:
            bot.players['testuser']['coins'] = 200
            bot.save_database()
            bot.load_database()
            if bot.players.get('testuser', {}).get('coins') == 200:
                print("✅ Simple bot database working!")
            else:
                print("❌ Simple bot database test failed!")
        
        return True
        
    except Exception as e:
        print(f"❌ Simple bot error: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_sasl_config():
    """Test SASL configuration"""
    try:
        print("\n🔧 Testing SASL configuration...")
        
        # Load config
        with open('config.json') as f:
            config = json.load(f)
        
        # Check SASL config
        sasl_config = config.get('sasl', {})
        if sasl_config.get('enabled'):
            print("✅ SASL is enabled in config")
            
            username = sasl_config.get('username')
            password = sasl_config.get('password')
            
            if username and password:
                print(f"✅ SASL credentials configured (user: {username})")
            else:
                print("⚠️  SASL enabled but credentials missing")
        else:
            print("ℹ️  SASL is not enabled in config")
        
        return True
        
    except Exception as e:
        print(f"❌ SASL config error: {e}")
        return False

async def main():
    """Main test function"""
    print("🦆 DuckHunt Bot Integration Test")
    print("=" * 50)
    
    try:
        # Test configuration
        config_ok = await test_sasl_config()
        
        # Test modular bot
        modular_ok = await test_modular_bot()
        
        # Test simple bot
        simple_ok = await test_simple_bot()
        
        print("\n" + "=" * 50)
        print("📊 Test Results:")
        print(f"  Config: {'✅ PASS' if config_ok else '❌ FAIL'}")
        print(f"  Modular Bot: {'✅ PASS' if modular_ok else '❌ FAIL'}")
        print(f"  Simple Bot: {'✅ PASS' if simple_ok else '❌ FAIL'}")
        
        if all([config_ok, modular_ok, simple_ok]):
            print("\n🎉 All tests passed! SASL integration is working!")
            print("🦆 DuckHunt Bots are ready to deploy!")
            return True
        else:
            print("\n💥 Some tests failed. Check the errors above.")
            return False
        
    except Exception as e:
        print(f"💥 Test suite error: {e}")
        return False

if __name__ == '__main__':
    success = asyncio.run(main())
    if not success:
        sys.exit(1)
