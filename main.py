#!/usr/bin/env python3
"""
StockWarren - Automated Stock Trading Bot
Main entry point for running the trading bot and dashboard

Usage:
    python main.py              # Start bot + dashboard
    python main.py --bot-only   # Start bot without dashboard
    python main.py --dash-only  # Start dashboard without bot
"""

import os
import sys
import argparse
import logging
import threading
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/stockwarren.log"),
    ],
)
logger = logging.getLogger("StockWarren")


def main():
    parser = argparse.ArgumentParser(description="StockWarren Trading Bot")
    parser.add_argument("--bot-only", action="store_true", help="Run bot without dashboard")
    parser.add_argument("--dash-only", action="store_true", help="Run dashboard without bot")
    parser.add_argument("--config", default="config/settings.ini", help="Config file path")
    parser.add_argument("--host", default="127.0.0.1", help="Dashboard host")
    parser.add_argument("--port", type=int, default=5000, help="Dashboard port")
    args = parser.parse_args()

    # Ensure log directory exists
    os.makedirs("logs", exist_ok=True)
    os.makedirs("data/models", exist_ok=True)

    # Check API keys
    if not os.getenv("ALPACA_API_KEY") or not os.getenv("ALPACA_SECRET_KEY"):
        logger.error(
            "Alpaca API keys not found!\n"
            "1. Copy .env.example to .env\n"
            "2. Add your Alpaca API key and secret key\n"
            "3. Get keys at: https://app.alpaca.markets/"
        )
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("StockWarren Trading Bot v1.0")
    logger.info("=" * 50)

    # Initialize trading bot
    from src.engine.trading_bot import TradingBot
    from src.engine.scheduler import TradeScheduler
    bot = TradingBot(config_path=args.config)

    # Initialize trade scheduler
    scheduler = TradeScheduler(bot.alpaca, bot.risk_manager)
    scheduler.start()

    account = bot.alpaca.get_account()
    logger.info(f"Mode: {'PAPER' if bot.alpaca.paper else 'LIVE'}")
    logger.info(f"Portfolio: ${account['portfolio_value']:,.2f}")
    logger.info(f"Cash: ${account['cash']:,.2f}")
    logger.info(f"Watchlist: {', '.join(bot.watchlist.get_symbols())}")

    if args.bot_only:
        # Run bot only (no dashboard)
        logger.info("Starting bot (no dashboard)...")
        bot.start()

    elif args.dash_only:
        # Run dashboard only (no bot)
        logger.info(f"Starting dashboard at http://{args.host}:{args.port}")
        from gui.app import run_dashboard, set_bot, set_scheduler
        set_bot(bot)
        set_scheduler(scheduler)
        run_dashboard(host=args.host, port=args.port)

    else:
        # Run both bot and dashboard
        logger.info(f"Starting bot + dashboard at http://{args.host}:{args.port}")

        from gui.app import run_dashboard, set_bot, set_scheduler
        set_bot(bot)
        set_scheduler(scheduler)

        # Start bot in background thread
        bot_thread = threading.Thread(target=bot.start, daemon=True)
        bot_thread.start()

        # Run dashboard in main thread (blocking)
        run_dashboard(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
