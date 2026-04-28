"""
End-of-Day Flattening Manager

Closes all day-trade positions before market close to comply with PDT rules
and avoid overnight risk. Configurable via close_before_eod_minutes setting.

Runs as a background thread that wakes up periodically and checks if EOD
flattening should occur.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional

from src.utils import market_calendar as mcal

logger = logging.getLogger(__name__)


class EODManager:
    """Manages end-of-day position flattening for day trades"""

    def __init__(
        self,
        alpaca_client,
        risk_manager,
        close_minutes_before_eod: int = 15,
        check_interval: int = 30,
        audit_logger=None,
    ):
        self.client = alpaca_client
        self.risk_manager = risk_manager
        self.close_minutes_before_eod = close_minutes_before_eod
        self.check_interval = check_interval
        self.audit_logger = audit_logger

        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._already_flattened_today = False
        self._last_flatten_date = None

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(
            f"EOD manager started (will flatten day trades "
            f"{self.close_minutes_before_eod} min before close)"
        )

    def stop(self):
        self.running = False
        logger.info("EOD manager stopped")

    def _run_loop(self):
        while self.running:
            try:
                self._check_and_flatten()
            except Exception as e:
                logger.error(f"EOD check error: {e}", exc_info=True)
            time.sleep(self.check_interval)

    def _check_and_flatten(self):
        # Reset the flag at start of new trading day
        today = mcal.now_et().date()
        if self._last_flatten_date != today:
            self._already_flattened_today = False
            self._last_flatten_date = today

        if self._already_flattened_today:
            return

        status = mcal.get_status(self.client)

        # Only act when market is open
        if not status.is_open:
            return

        # Time to flatten?
        if status.minutes_until_close <= self.close_minutes_before_eod:
            self._flatten_day_trades()
            self._already_flattened_today = True

    def _flatten_day_trades(self):
        """Close all positions marked as day trades"""
        logger.warning(
            f"EOD FLATTENING: closing day-trade positions "
            f"({mcal.now_et().strftime('%H:%M:%S ET')})"
        )

        if self.audit_logger:
            self.audit_logger.log_emergency(
                "EOD flattening triggered for day-trade positions"
            )

        # Get current positions
        try:
            positions = self.client.get_positions()
        except Exception as e:
            logger.error(f"EOD: failed to fetch positions: {e}")
            return

        closed_count = 0
        for pos_data in positions:
            symbol = pos_data["symbol"]

            # Only flatten if registered as a day trade in risk manager
            risk_pos = self.risk_manager.active_positions.get(symbol)
            if risk_pos is None:
                # Unknown position - flatten to be safe
                logger.warning(
                    f"EOD: closing untracked position {symbol} "
                    f"(not in risk manager)"
                )
            elif risk_pos.trade_type != "day":
                logger.info(
                    f"EOD: keeping swing position {symbol} "
                    f"(trade_type={risk_pos.trade_type})"
                )
                continue

            try:
                # Cancel any open orders for this symbol first
                orders = self.client.get_orders(status="open")
                for o in orders:
                    if o["symbol"] == symbol:
                        self.client.cancel_order(o["id"])

                # Close position with market order
                self.client.close_position(symbol)
                closed_count += 1

                current_price = pos_data.get("current_price", 0)
                pnl = self.risk_manager.close_position(symbol, current_price)

                logger.info(
                    f"EOD: closed {symbol} P&L=${pnl:+.2f}"
                )

                if self.audit_logger:
                    self.audit_logger.log_position_closed(
                        symbol, current_price, pnl, "EOD_FLATTEN"
                    )

            except Exception as e:
                logger.error(f"EOD: failed to close {symbol}: {e}")
                if self.audit_logger:
                    self.audit_logger.log_emergency(
                        f"EOD close failed for {symbol}: {e}"
                    )

        logger.info(f"EOD flattening complete: {closed_count} positions closed")
