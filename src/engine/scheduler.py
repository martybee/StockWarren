"""
Trade Scheduler for StockWarren
Queue trades to execute at specific times during market hours
"""

import logging
import threading
import time
import json
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class ScheduledTradeStatus(str, Enum):
    PENDING = "pending"
    EXECUTED = "executed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    MISSED = "missed"  # Market was closed at scheduled time


@dataclass
class ScheduledTrade:
    """A trade scheduled to execute at a specific time"""
    id: str
    symbol: str
    side: str                    # "buy" or "sell"
    qty: float
    order_type: str              # "market", "limit"
    limit_price: Optional[float] = None
    stop_loss_pct: Optional[float] = None    # Optional stop loss %
    take_profit_pct: Optional[float] = None  # Optional take profit %
    scheduled_time: str = ""     # ISO format: "2026-04-22T10:30:00"
    status: str = ScheduledTradeStatus.PENDING
    created_at: str = ""
    executed_at: str = ""
    result_order_id: str = ""
    error_message: str = ""
    notes: str = ""

    def to_dict(self):
        return asdict(self)


class TradeScheduler:
    """Manages scheduled trades and executes them at the right time"""

    def __init__(self, alpaca_client, risk_manager=None):
        self.client = alpaca_client
        self.risk_manager = risk_manager
        self.scheduled_trades: list[ScheduledTrade] = []
        self.history: list[ScheduledTrade] = []
        self.running = False
        self._thread = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._data_file = "data/scheduled_trades.json"

        self._load_trades()

    def schedule_trade(self, symbol: str, side: str, qty: float,
                       order_type: str, scheduled_time: str,
                       limit_price: float = None,
                       stop_loss_pct: float = None,
                       take_profit_pct: float = None,
                       notes: str = "") -> ScheduledTrade:
        """Schedule a new trade"""
        with self._lock:
            trade = ScheduledTrade(
                id=f"ST-{self._next_id:04d}",
                symbol=symbol.upper().strip(),
                side=side.lower(),
                qty=float(qty),
                order_type=order_type.lower(),
                limit_price=limit_price,
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
                scheduled_time=scheduled_time,
                status=ScheduledTradeStatus.PENDING,
                created_at=datetime.now().isoformat(),
                notes=notes,
            )
            self._next_id += 1
            self.scheduled_trades.append(trade)
            self._save_trades()

        logger.info(
            f"Trade scheduled: {trade.id} - {trade.side.upper()} {trade.qty} "
            f"{trade.symbol} @ {trade.scheduled_time}"
        )
        return trade

    def cancel_trade(self, trade_id: str) -> bool:
        """Cancel a scheduled trade"""
        with self._lock:
            for trade in self.scheduled_trades:
                if trade.id == trade_id and trade.status == ScheduledTradeStatus.PENDING:
                    trade.status = ScheduledTradeStatus.CANCELLED
                    self.history.append(trade)
                    self.scheduled_trades.remove(trade)
                    self._save_trades()
                    logger.info(f"Scheduled trade cancelled: {trade_id}")
                    return True
        return False

    def get_pending_trades(self) -> list[dict]:
        """Get all pending scheduled trades"""
        with self._lock:
            return [t.to_dict() for t in self.scheduled_trades
                    if t.status == ScheduledTradeStatus.PENDING]

    def get_history(self) -> list[dict]:
        """Get executed/cancelled/failed trade history"""
        with self._lock:
            return [t.to_dict() for t in self.history[-50:]]

    def get_all_trades(self) -> list[dict]:
        """Get all scheduled trades (pending + history)"""
        with self._lock:
            all_trades = [t.to_dict() for t in self.scheduled_trades]
            all_trades.extend([t.to_dict() for t in self.history[-50:]])
            return sorted(all_trades, key=lambda x: x.get("scheduled_time", ""), reverse=True)

    def start(self):
        """Start the scheduler background thread"""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Trade scheduler started")

    def stop(self):
        """Stop the scheduler"""
        self.running = False
        logger.info("Trade scheduler stopped")

    def _run_loop(self):
        """Main scheduler loop - checks every 5 seconds"""
        while self.running:
            try:
                self._check_and_execute()
            except Exception as e:
                logger.error(f"Scheduler error: {e}", exc_info=True)
            time.sleep(5)

    def _check_and_execute(self):
        """Check if any scheduled trades should be executed now"""
        now = datetime.now()

        with self._lock:
            trades_to_execute = []
            for trade in self.scheduled_trades:
                if trade.status != ScheduledTradeStatus.PENDING:
                    continue

                try:
                    sched_time = datetime.fromisoformat(trade.scheduled_time)
                except (ValueError, TypeError):
                    continue

                # Execute if we're within 30 seconds of the scheduled time
                time_diff = (now - sched_time).total_seconds()
                if time_diff >= 0 and time_diff < 30:
                    trades_to_execute.append(trade)
                elif time_diff >= 30:
                    # Missed the window (more than 30 seconds late)
                    # Still execute if within 5 minutes
                    if time_diff < 300:
                        trades_to_execute.append(trade)
                    else:
                        trade.status = ScheduledTradeStatus.MISSED
                        trade.error_message = f"Missed by {int(time_diff)}s"
                        self.history.append(trade)
                        self.scheduled_trades.remove(trade)
                        logger.warning(f"Scheduled trade missed: {trade.id}")

        # Execute outside the lock
        for trade in trades_to_execute:
            self._execute_trade(trade)

    def _execute_trade(self, trade: ScheduledTrade):
        """Execute a scheduled trade"""
        logger.info(f"Executing scheduled trade: {trade.id} - {trade.side} {trade.qty} {trade.symbol}")

        # Check if market is open
        try:
            if not self.client.is_market_open():
                trade.status = ScheduledTradeStatus.MISSED
                trade.error_message = "Market is closed"
                logger.warning(f"Scheduled trade {trade.id} skipped - market closed")
                self._finalize_trade(trade)
                return
        except Exception as e:
            logger.error(f"Failed to check market status: {e}")

        try:
            # Place the order
            if trade.order_type == "market":
                order = self.client.place_market_order(
                    symbol=trade.symbol,
                    qty=trade.qty,
                    side=trade.side,
                    time_in_force="day",
                )
            elif trade.order_type == "limit" and trade.limit_price:
                order = self.client.place_limit_order(
                    symbol=trade.symbol,
                    qty=trade.qty,
                    side=trade.side,
                    limit_price=trade.limit_price,
                    time_in_force="day",
                )
            else:
                trade.status = ScheduledTradeStatus.FAILED
                trade.error_message = "Invalid order type or missing limit price"
                self._finalize_trade(trade)
                return

            trade.status = ScheduledTradeStatus.EXECUTED
            trade.executed_at = datetime.now().isoformat()
            trade.result_order_id = order.get("id", "")

            logger.info(
                f"Scheduled trade executed: {trade.id} - "
                f"Order ID: {trade.result_order_id}"
            )

            # Place stop loss if configured
            if trade.stop_loss_pct and trade.stop_loss_pct > 0:
                try:
                    quote = self.client.get_latest_quote(trade.symbol)
                    current_price = (quote["bid"] + quote["ask"]) / 2

                    if trade.side == "buy":
                        stop_price = round(current_price * (1 - trade.stop_loss_pct / 100), 2)
                        stop_side = "sell"
                    else:
                        stop_price = round(current_price * (1 + trade.stop_loss_pct / 100), 2)
                        stop_side = "buy"

                    self.client.place_stop_order(
                        symbol=trade.symbol,
                        qty=trade.qty,
                        side=stop_side,
                        stop_price=stop_price,
                        time_in_force="gtc",
                    )
                    logger.info(f"Stop loss placed for {trade.symbol} at ${stop_price:.2f}")
                except Exception as e:
                    logger.error(f"Failed to place stop loss for {trade.id}: {e}")

            # Place take profit if configured
            if trade.take_profit_pct and trade.take_profit_pct > 0:
                try:
                    quote = self.client.get_latest_quote(trade.symbol)
                    current_price = (quote["bid"] + quote["ask"]) / 2

                    if trade.side == "buy":
                        tp_price = round(current_price * (1 + trade.take_profit_pct / 100), 2)
                        tp_side = "sell"
                    else:
                        tp_price = round(current_price * (1 - trade.take_profit_pct / 100), 2)
                        tp_side = "buy"

                    self.client.place_limit_order(
                        symbol=trade.symbol,
                        qty=trade.qty,
                        side=tp_side,
                        limit_price=tp_price,
                        time_in_force="gtc",
                    )
                    logger.info(f"Take profit placed for {trade.symbol} at ${tp_price:.2f}")
                except Exception as e:
                    logger.error(f"Failed to place take profit for {trade.id}: {e}")

        except Exception as e:
            trade.status = ScheduledTradeStatus.FAILED
            trade.error_message = str(e)
            logger.error(f"Scheduled trade {trade.id} failed: {e}")

        self._finalize_trade(trade)

    def _finalize_trade(self, trade: ScheduledTrade):
        """Move trade from pending to history"""
        with self._lock:
            if trade in self.scheduled_trades:
                self.scheduled_trades.remove(trade)
            self.history.append(trade)
            self._save_trades()

    def _save_trades(self):
        """Save scheduled trades to disk"""
        os.makedirs(os.path.dirname(self._data_file), exist_ok=True)
        data = {
            "next_id": self._next_id,
            "pending": [t.to_dict() for t in self.scheduled_trades],
            "history": [t.to_dict() for t in self.history[-100:]],
        }
        with open(self._data_file, "w") as f:
            json.dump(data, f, indent=2)

    def _load_trades(self):
        """Load scheduled trades from disk"""
        if not os.path.exists(self._data_file):
            return

        try:
            with open(self._data_file) as f:
                data = json.load(f)

            self._next_id = data.get("next_id", 1)

            for t in data.get("pending", []):
                trade = ScheduledTrade(**t)
                # Only load if still in the future
                try:
                    sched_time = datetime.fromisoformat(trade.scheduled_time)
                    if sched_time > datetime.now() - timedelta(minutes=5):
                        self.scheduled_trades.append(trade)
                except (ValueError, TypeError):
                    pass

            for t in data.get("history", []):
                self.history.append(ScheduledTrade(**t))

            logger.info(
                f"Loaded {len(self.scheduled_trades)} pending and "
                f"{len(self.history)} historical scheduled trades"
            )
        except Exception as e:
            logger.error(f"Failed to load scheduled trades: {e}")
