#!/usr/bin/env python3
"""
Telegram Multi-Account Bot - Railway Edition
High-speed auto-reply and forward system for managing thousands of groups
"""
import asyncio
import logging
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot import TelegramBotManager


def setup_logging():
    """Setup logging for Railway"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    # Reduce noise
    logging.getLogger('telethon').setLevel(logging.WARNING)
    logging.getLogger('telethon.network').setLevel(logging.ERROR)


async def main():
    """Main entry point"""
    setup_logging()

    logger = logging.getLogger(__name__)
    logger.info("=" * 50)
    logger.info("Telegram Bot Starting on Railway...")
    logger.info("=" * 50)

    # Check required env vars
    bot_token = os.getenv('BOT_TOKEN', '')
    api_id = os.getenv('API_ID', '')
    api_hash = os.getenv('API_HASH', '')

    if not bot_token or not api_id or not api_hash:
        logger.error("Missing required environment variables!")
        logger.error("Please set BOT_TOKEN, API_ID, and API_HASH")
        logger.error("Bot will start but control bot won't work until configured.")

    manager = TelegramBotManager()

    try:
        await manager.start()
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise
    finally:
        await manager.stop()
        logger.info("Bot stopped gracefully")


if __name__ == "__main__":
    asyncio.run(main())
