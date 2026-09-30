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
import time
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
_manager = None
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
    if _manager:
        _manager.stop_all()

    logger.info("Shutdown complete")
    sys.exit(0)


def main():
    global _manager, _scheduler, _eod_manager

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

    # Per-account keys are resolved by the AccountManager below; a missing pair
    # just marks that account "not configured" instead of aborting startup.
    if not os.getenv("ALPACA_API_KEY") or not os.getenv("ALPACA_SECRET_KEY"):
        logger.warning(
            "Default account keys (ALPACA_API_KEY/SECRET) not set. "
            "Add per-account keys to .env; see .env.example."
        )

    logger.info("=" * 60)
    logger.info("StockWarren Trading Bot v1.1")
    logger.info("=" * 60)

    # Wait for API to be reachable before doing anything else (uses the default
    # account keys as a probe; skipped entirely if they aren't set).
    if not args.skip_startup_check and os.getenv("ALPACA_API_KEY"):
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

    # Staleness guard (M6): stamp which code this process is actually running,
    # so a long-lived process can never silently masquerade as current again.
    from src.utils.build_info import RUNNING_COMMIT, STARTED_AT
    supervised = os.getenv("STOCKWARREN_SUPERVISED") == "1"
    logger.info("Process build: commit %s, started %s%s",
                (RUNNING_COMMIT or "unknown")[:12], STARTED_AT.isoformat(),
                " [supervised]" if supervised else "")

    # Under supervision on macOS, tie sleep-prevention to THIS process's
    # lifetime: caffeinate -w exits by itself when we do. No hand-run PIDs.
    if supervised and sys.platform == "darwin":
        try:
            import subprocess
            subprocess.Popen(["caffeinate", "-s", "-w", str(os.getpid())])
            logger.info("caffeinate attached: Mac stays awake while this process lives")
        except Exception as e:
            logger.warning("Could not attach caffeinate (%s) — continuing without it", e)

    # Initialize trade audit logger
    audit_logger = TradeAuditLogger(log_dir="logs/trades")
    logger.info("Trade audit logger initialized")

    # Initialize all trading accounts (one bot per configured account)
    from src.engine.account_manager import AccountManager
    from src.engine.scheduler import TradeScheduler
    from src.engine.eod_manager import EODManager
    from src.utils import market_calendar as mcal

    manager = AccountManager(config_path=args.config, audit_logger=audit_logger)
    _manager = manager

    logger.info("Accounts: %s", ", ".join(
        f"{a.name}[{'ready' if a.configured else (a.error or 'n/a')}]" for a in manager.accounts
    ) or "none defined")

    configured = manager.configured_accounts()

    # Warm the company-description cache for the symbols actually in use, in the
    # background, so the autocomplete is instant for them from the first
    # keystroke. Cosmetic only — a daemon thread that never blocks startup or
    # shutdown, and a no-op once the on-disk cache is warm.
    if configured:
        try:
            from src.utils.company_profiles import prefetch_async
            watched = sorted({s for a in configured for s in a.bot.watchlist.get_symbols()})
            prefetch_async(watched)
        except Exception as e:
            logger.debug("Company-profile prefetch skipped: %s", e)

    if not configured:
        logger.warning("No accounts configured — add keys to .env. "
                       "Dashboard will run, but nothing will trade.")
        if args.bot_only:
            logger.error("--bot-only requires at least one configured account. Exiting.")
            sys.exit(3)

    # Scheduler + EOD manager are tied to the primary (first configured) account.
    primary = configured[0].bot if configured else None
    scheduler = None
    if primary is not None:
        scheduler = TradeScheduler(primary.alpaca, primary.risk_manager)
        scheduler.start()
        _scheduler = scheduler

        close_min = int(primary.config.get("day_trading", "close_before_eod_minutes", fallback="15"))
        eod_manager = EODManager(
            alpaca_client=primary.alpaca, risk_manager=primary.risk_manager,
            close_minutes_before_eod=close_min, audit_logger=audit_logger,
        )
        eod_manager.start()
        _eod_manager = eod_manager

        try:
            account = primary.alpaca.get_account()
            logger.info(f"Primary account '{primary.name}': "
                        f"${account['portfolio_value']:,.2f} portfolio, ${account['cash']:,.2f} cash")
        except Exception as e:
            logger.error(f"Failed to fetch primary account: {e}")
        try:
            status = mcal.get_status(primary.alpaca)
            state = "OPEN" if status.is_open else "CLOSED"
            logger.info(f"Market is {state}.")
        except Exception as e:
            logger.warning(f"Could not determine market status: {e}")

    # Crash-restart policy (M6, CHART_PLAN §14 Decision 3). Evaluated AFTER
    # AccountManager and TradeScheduler have loaded their state files, so the
    # clean-state check sees this boot's validation results. --dash-only never
    # starts bots, so it needs no gate.
    decision = None
    if not args.dash_only:
        from src.engine.startup_policy import evaluate_startup
        decision = evaluate_startup(
            account_ids=[a.id for a in configured], supervised=supervised)

    if args.bot_only:
        if not decision.start_bots:
            logger.critical("--bot-only refused by startup policy: %s. Exiting.",
                            "; ".join(decision.reasons))
            sys.exit(4)
        logger.info("Starting %d bot(s) (no dashboard)...", len(configured))
        manager.start_all()
        while not _shutdown_requested:      # keep main thread alive for signals
            time.sleep(60)

    elif args.dash_only:
        logger.info(f"Starting dashboard at http://{args.host}:{args.port}")
        from gui.app import run_dashboard, set_account_manager, set_scheduler
        set_account_manager(manager)
        set_scheduler(scheduler)
        run_dashboard(host=args.host, port=args.port)

    else:
        from gui.app import run_dashboard, set_account_manager, set_scheduler
        set_account_manager(manager)
        set_scheduler(scheduler)
        if decision.start_bots:
            logger.info(f"Starting {len(configured)} bot(s) + dashboard at http://{args.host}:{args.port}")
            manager.start_all()
        else:
            # Decision 3: the process comes up DASHBOARD-ONLY — alive, visible,
            # not trading — rather than crash-looping or trading off dirty state.
            logger.critical(
                "DASHBOARD-ONLY: bots not started (%s). Dashboard at "
                "http://%s:%s — a human resolves the cause, then starts the "
                "bots from the UI or restarts the service.",
                "; ".join(decision.reasons), args.host, args.port)
        run_dashboard(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
