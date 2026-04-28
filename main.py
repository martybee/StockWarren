#!/usr/bin/env python3
"""
StockWarren - Automated Stock Trading Bot
Main entry point for running the trading bot and dashboard

Usage:
    python main.py              # Start bot + dashboard
    python main.py --bot-only   # Start bot without dashboard
    python main.py --dash-only  # Start dashboard without bot
    python main.py --skip-startup-check   # Skip API health check on startup
"""

import os
import sys
import argparse
import logging
import signal
import threading
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Ensure log directories exist before importing anything that logs
os.makedirs("logs", exist_ok=True)
os.makedirs("logs/trades", exist_ok=True)
os.makedirs("data/models", exist_ok=True)

# Set up logging FIRST, before importing anything else
from src.utils.logging_setup import setup_logging, TradeAuditLogger
setup_logging(
    log_dir="logs",
    console_level="INFO",
    file_level="DEBUG",
)
logger = logging.getLogger("StockWarren")


# Global references for graceful shutdown
_bot = None
_scheduler = None
_eod_manager = None
_shutdown_requested = False


def shutdown_handler(signum, frame):
    """Handle SIGTERM/SIGINT for graceful shutdown"""
    global _shutdown_requested
    if _shutdown_requested:
        logger.warning("Shutdown already in progress")
        return
    _shutdown_requested = True

    sig_name = signal.Signals(signum).name
    logger.warning(f"Received {sig_name}, shutting down gracefully...")

    if _eod_manager:
        _eod_manager.stop()
    if _scheduler:
        _scheduler.stop()
    if _bot:
        _bot.stop()

    logger.info("Shutdown complete")
    sys.exit(0)


def main():
    global _bot, _scheduler, _eod_manager

    parser = argparse.ArgumentParser(description="StockWarren Trading Bot")
    parser.add_argument("--bot-only", action="store_true", help="Run bot without dashboard")
    parser.add_argument("--dash-only", action="store_true", help="Run dashboard without bot")
    parser.add_argument("--config", default="config/settings.ini", help="Config file path")
    parser.add_argument("--host", default="127.0.0.1", help="Dashboard host")
    parser.add_argument("--port", type=int, default=5000, help="Dashboard port")
    parser.add_argument("--skip-startup-check", action="store_true",
                        help="Skip waiting for Alpaca API on startup")
    parser.add_argument("--startup-timeout", type=int, default=120,
                        help="How long to wait for Alpaca API at startup (seconds)")
    args = parser.parse_args()

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    # Check API keys
    if not os.getenv("ALPACA_API_KEY") or not os.getenv("ALPACA_SECRET_KEY"):
        logger.error(
            "Alpaca API keys not found!\n"
            "1. Copy .env.example to .env\n"
            "2. Add your Alpaca API key and secret key\n"
            "3. Get keys at: https://app.alpaca.markets/"
        )
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("StockWarren Trading Bot v1.1")
    logger.info("=" * 60)

    # Wait for API to be reachable before doing anything else
    if not args.skip_startup_check:
        logger.info(f"Checking Alpaca API connectivity (timeout: {args.startup_timeout}s)...")
        from broker.client import AlpacaClient

        try:
            probe = AlpacaClient(paper=True)
        except Exception as e:
            logger.error(f"Failed to construct Alpaca client: {e}")
            sys.exit(1)

        # Quick first check
        health = probe.health_check()
        if health["healthy"]:
            logger.info(f"Alpaca API reachable (latency: {health['latency_ms']}ms)")
        else:
            logger.warning(f"Alpaca API not responding: {health.get('error', 'unknown')}")
            logger.warning(f"Waiting up to {args.startup_timeout}s for API to become available...")
            if not probe.wait_for_api(timeout=args.startup_timeout):
                logger.error(
                    "Alpaca API did not become available. Exiting.\n"
                    "Check your API keys, network, and Alpaca status: "
                    "https://status.alpaca.markets/"
                )
                sys.exit(2)
            logger.info("Alpaca API is now reachable, continuing startup")

    # Initialize trade audit logger
    audit_logger = TradeAuditLogger(log_dir="logs/trades")
    logger.info("Trade audit logger initialized")

    # Initialize trading bot
    from src.engine.trading_bot import TradingBot
    from src.engine.scheduler import TradeScheduler
    from src.engine.eod_manager import EODManager
    from src.utils import market_calendar as mcal

    try:
        bot = TradingBot(config_path=args.config)
        bot.audit_logger = audit_logger
    except Exception as e:
        logger.error(f"Failed to initialize trading bot: {e}", exc_info=True)
        sys.exit(3)

    _bot = bot

    # Initialize trade scheduler
    scheduler = TradeScheduler(bot.alpaca, bot.risk_manager)
    scheduler.start()
    _scheduler = scheduler

    # Initialize EOD manager (closes day trades before market close)
    close_min = int(bot.config.get("day_trading", "close_before_eod_minutes", fallback="15"))
    eod_manager = EODManager(
        alpaca_client=bot.alpaca,
        risk_manager=bot.risk_manager,
        close_minutes_before_eod=close_min,
        audit_logger=audit_logger,
    )
    eod_manager.start()
    _eod_manager = eod_manager

    # Get account info (will retry automatically if transient failure)
    try:
        account = bot.alpaca.get_account()
        logger.info(f"Mode: {'PAPER' if bot.alpaca.paper else 'LIVE'}")
        logger.info(f"Portfolio: ${account['portfolio_value']:,.2f}")
        logger.info(f"Cash: ${account['cash']:,.2f}")
        logger.info(f"Watchlist: {', '.join(bot.watchlist.get_symbols())}")
    except Exception as e:
        logger.error(f"Failed to fetch account: {e}")
        # Continue anyway - dashboard will show the error

    # Log market status
    try:
        status = mcal.get_status(bot.alpaca)
        if status.is_open:
            logger.info(
                f"Market is OPEN. Closes in {status.minutes_until_close} min "
                f"({status.next_close.strftime('%H:%M ET')})"
            )
        else:
            logger.info(
                f"Market is CLOSED. Opens in {status.minutes_until_open} min "
                f"({status.next_open.strftime('%Y-%m-%d %H:%M ET')})"
            )
    except Exception as e:
        logger.warning(f"Could not determine market status: {e}")

    if args.bot_only:
        logger.info("Starting bot (no dashboard)...")
        bot.start()

    elif args.dash_only:
        logger.info(f"Starting dashboard at http://{args.host}:{args.port}")
        from gui.app import run_dashboard, set_bot, set_scheduler
        set_bot(bot)
        set_scheduler(scheduler)
        run_dashboard(host=args.host, port=args.port)

    else:
        logger.info(f"Starting bot + dashboard at http://{args.host}:{args.port}")
        from gui.app import run_dashboard, set_bot, set_scheduler
        set_bot(bot)
        set_scheduler(scheduler)

        bot_thread = threading.Thread(target=bot.start, daemon=True)
        bot_thread.start()

        run_dashboard(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
