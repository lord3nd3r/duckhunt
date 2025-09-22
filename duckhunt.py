"""
DuckHunt IRC Bot - Main Entry Point
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
        with open('config.json') as f:
            config = json.load(f)
        
        bot = DuckHuntBot(config)
        bot.logger.info("🦆 Starting DuckHunt Bot...")
        
        asyncio.run(bot.run())
        
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except FileNotFoundError:
        print("❌ config.json not found!")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
