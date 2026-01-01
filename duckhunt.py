"""
DuckHunt IRC Bot - Simplified Entry Point
Commands: !bang, !reload, !shop, !rearm, !disarm
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.duckhuntbot import DuckHuntBot


def main():
    """Main entry point for DuckHunt Bot"""
    try:
        config_file = 'config.json'
        if not os.path.exists(config_file):
            print("❌ config.json not found!")
            sys.exit(1)
            
        with open(config_file) as f:
            config = json.load(f)
        
        bot = DuckHuntBot(config)
        bot.logger.info("Starting DuckHunt Bot...")
        
        # Run the bot
        asyncio.run(bot.run())
        
    except KeyboardInterrupt:
        print("\n🛑 Shutdown interrupted by user")
    except FileNotFoundError:
        print("❌ config.json not found!")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    else:
        print("👋 DuckHunt Bot stopped gracefully")


if __name__ == '__main__':
    main()