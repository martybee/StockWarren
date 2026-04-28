"""
Alpaca API Client for StockWarren
Handles authentication, orders, positions, and market data
Supports both paper trading and live trading
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    StopOrderRequest,
    StopLimitOrderRequest,
    TrailingStopOrderRequest,
    GetOrdersRequest,
)
from alpaca.trading.enums import (
    OrderSide,
    TimeInForce,
    OrderType,
    OrderStatus,
    QueryOrderStatus,
)
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestQuoteRequest,
    StockSnapshotRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from src.utils.retry import retry_on_failure

logger = logging.getLogger(__name__)


class AlpacaClient:
    """Wrapper around Alpaca's trading and data APIs"""

    def __init__(self, api_key: str = None, secret_key: str = None,
                 paper: bool = True):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY")
        self.secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
        self.paper = paper

        if not self.api_key or not self.secret_key:
            raise ValueError(
                "Alpaca API keys required. Set ALPACA_API_KEY and ALPACA_SECRET_KEY "
                "environment variables or pass them directly."
            )

        # Trading client
        self.trading_client = TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            paper=self.paper,
        )

        # Data client (no auth needed for free tier)
        self.data_client = StockHistoricalDataClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
        )

        logger.info(f"Alpaca client initialized (paper={self.paper})")

    # ==================== Health Check ====================

    def health_check(self) -> dict:
        """
        Quick health check without retries. Used to verify API availability.
        Returns dict with 'healthy', 'latency_ms', and optional 'error'.
        """
        import time as _time
        start = _time.time()
        try:
            # Use clock endpoint - no authentication issues, fast
            self.trading_client.get_clock()
            latency_ms = int((_time.time() - start) * 1000)
            return {"healthy": True, "latency_ms": latency_ms}
        except Exception as e:
            latency_ms = int((_time.time() - start) * 1000)
            return {
                "healthy": False,
                "latency_ms": latency_ms,
                "error": str(e),
            }

    def wait_for_api(self, timeout: float = 120.0, interval: float = 5.0) -> bool:
        """
        Block until API becomes available, or timeout. Used at startup.
        Returns True if API is reachable, False on timeout.
        """
        from src.utils.retry import retry_until
        return retry_until(
            condition=lambda: self.health_check()["healthy"],
            timeout=timeout,
            interval=interval,
            description="Alpaca API connectivity",
        )

    # ==================== Account ====================

    @retry_on_failure(max_attempts=4, initial_delay=1.0, backoff_factor=2.0)
    def get_account(self) -> dict:
        """Get account information"""
        account = self.trading_client.get_account()
        return {
            "id": account.id,
            "status": account.status.value if account.status else "unknown",
            "cash": float(account.cash),
            "portfolio_value": float(account.portfolio_value),
            "buying_power": float(account.buying_power),
            "equity": float(account.equity),
            "last_equity": float(account.last_equity),
            "long_market_value": float(account.long_market_value),
            "short_market_value": float(account.short_market_value),
            "day_trade_count": account.daytrade_count,
            "pattern_day_trader": account.pattern_day_trader,
            "trading_blocked": account.trading_blocked,
            "account_blocked": account.account_blocked,
        }

    @retry_on_failure(max_attempts=4, initial_delay=1.0)
    def is_market_open(self) -> bool:
        """Check if the market is currently open"""
        clock = self.trading_client.get_clock()
        return clock.is_open

    @retry_on_failure(max_attempts=4, initial_delay=1.0)
    def get_market_hours(self) -> dict:
        """Get market hours for today"""
        clock = self.trading_client.get_clock()
        return {
            "is_open": clock.is_open,
            "next_open": str(clock.next_open),
            "next_close": str(clock.next_close),
        }

    # ==================== Orders ====================

    @retry_on_failure(max_attempts=2, initial_delay=0.5, backoff_factor=2.0)
    def place_market_order(self, symbol: str, qty: float, side: str,
                           time_in_force: str = "day") -> dict:
        """Place a market order"""
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif = self._parse_tif(time_in_force)

        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=tif,
        )

        order = self.trading_client.submit_order(request)
        logger.info(f"Market order placed: {side} {qty} {symbol}")
        return self._order_to_dict(order)

    @retry_on_failure(max_attempts=2, initial_delay=0.5, backoff_factor=2.0)
    def place_limit_order(self, symbol: str, qty: float, side: str,
                          limit_price: float, time_in_force: str = "day") -> dict:
        """Place a limit order"""
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif = self._parse_tif(time_in_force)

        request = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=tif,
            limit_price=limit_price,
        )

        order = self.trading_client.submit_order(request)
        logger.info(f"Limit order placed: {side} {qty} {symbol} @ {limit_price}")
        return self._order_to_dict(order)

    @retry_on_failure(max_attempts=3, initial_delay=0.5, backoff_factor=2.0)
    def place_stop_order(self, symbol: str, qty: float, side: str,
                         stop_price: float, time_in_force: str = "day") -> dict:
        """Place a stop order"""
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif = self._parse_tif(time_in_force)

        request = StopOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=tif,
            stop_price=stop_price,
        )

        order = self.trading_client.submit_order(request)
        logger.info(f"Stop order placed: {side} {qty} {symbol} stop @ {stop_price}")
        return self._order_to_dict(order)

    def place_stop_limit_order(self, symbol: str, qty: float, side: str,
                               stop_price: float, limit_price: float,
                               time_in_force: str = "day") -> dict:
        """Place a stop-limit order"""
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif = self._parse_tif(time_in_force)

        request = StopLimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=tif,
            stop_price=stop_price,
            limit_price=limit_price,
        )

        order = self.trading_client.submit_order(request)
        logger.info(f"Stop-limit order: {side} {qty} {symbol} stop {stop_price} limit {limit_price}")
        return self._order_to_dict(order)

    def place_trailing_stop_order(self, symbol: str, qty: float, side: str,
                                   trail_percent: float = None,
                                   trail_price: float = None,
                                   time_in_force: str = "day") -> dict:
        """Place a trailing stop order"""
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif = self._parse_tif(time_in_force)

        kwargs = {
            "symbol": symbol,
            "qty": qty,
            "side": order_side,
            "time_in_force": tif,
        }

        if trail_percent is not None:
            kwargs["trail_percent"] = trail_percent
        elif trail_price is not None:
            kwargs["trail_price"] = trail_price

        request = TrailingStopOrderRequest(**kwargs)
        order = self.trading_client.submit_order(request)
        logger.info(f"Trailing stop order: {side} {qty} {symbol}")
        return self._order_to_dict(order)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order"""
        try:
            self.trading_client.cancel_order_by_id(order_id)
            logger.info(f"Order {order_id} cancelled")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def cancel_all_orders(self) -> int:
        """Cancel all open orders"""
        statuses = self.trading_client.cancel_orders()
        count = len(statuses)
        logger.info(f"Cancelled {count} orders")
        return count

    @retry_on_failure(max_attempts=3, initial_delay=1.0)
    def get_orders(self, status: str = "open", limit: int = 50) -> list:
        """Get orders by status"""
        query_status = QueryOrderStatus.OPEN if status == "open" else QueryOrderStatus.ALL
        request = GetOrdersRequest(status=query_status, limit=limit)
        orders = self.trading_client.get_orders(request)
        return [self._order_to_dict(o) for o in orders]

    def get_order(self, order_id: str) -> dict:
        """Get a specific order"""
        order = self.trading_client.get_order_by_id(order_id)
        return self._order_to_dict(order)

    # ==================== Positions ====================

    @retry_on_failure(max_attempts=3, initial_delay=1.0)
    def get_positions(self) -> list:
        """Get all open positions"""
        positions = self.trading_client.get_all_positions()
        return [self._position_to_dict(p) for p in positions]

    def get_position(self, symbol: str) -> Optional[dict]:
        """Get position for a specific symbol"""
        try:
            position = self.trading_client.get_open_position(symbol)
            return self._position_to_dict(position)
        except Exception:
            return None

    def close_position(self, symbol: str, qty: float = None) -> dict:
        """Close a position (full or partial)"""
        if qty:
            order = self.trading_client.close_position(
                symbol, close_options={"qty": str(qty)}
            )
        else:
            order = self.trading_client.close_position(symbol)
        logger.info(f"Position closed: {symbol}")
        return self._order_to_dict(order)

    def close_all_positions(self) -> list:
        """Close all positions (emergency)"""
        results = self.trading_client.close_all_positions(cancel_orders=True)
        logger.warning("ALL POSITIONS CLOSED (emergency)")
        return results

    # ==================== Market Data ====================

    @retry_on_failure(max_attempts=3, initial_delay=1.0)
    def get_bars(self, symbol: str, timeframe: str = "5Min",
                 start: datetime = None, limit: int = 200) -> "pd.DataFrame":
        """Get historical bar data"""
        import pandas as pd

        tf = self._parse_timeframe(timeframe)

        if start is None:
            start = datetime.now() - timedelta(days=5)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            limit=limit,
        )

        bars = self.data_client.get_stock_bars(request)
        df = bars.df

        if isinstance(df.index, pd.MultiIndex):
            df = df.droplevel("symbol")

        df.index = pd.to_datetime(df.index)
        return df

    @retry_on_failure(max_attempts=3, initial_delay=0.5)
    def get_latest_quote(self, symbol: str) -> dict:
        """Get the latest quote for a symbol"""
        request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quotes = self.data_client.get_stock_latest_quote(request)
        quote = quotes[symbol]
        return {
            "symbol": symbol,
            "bid": float(quote.bid_price),
            "ask": float(quote.ask_price),
            "bid_size": quote.bid_size,
            "ask_size": quote.ask_size,
            "timestamp": str(quote.timestamp),
        }

    def get_snapshot(self, symbol: str) -> dict:
        """Get a market snapshot for a symbol"""
        request = StockSnapshotRequest(symbol_or_symbols=symbol)
        snapshots = self.data_client.get_stock_snapshot(request)
        snap = snapshots[symbol]
        return {
            "symbol": symbol,
            "latest_trade_price": float(snap.latest_trade.price) if snap.latest_trade else None,
            "latest_trade_size": snap.latest_trade.size if snap.latest_trade else None,
            "daily_bar": {
                "open": float(snap.daily_bar.open),
                "high": float(snap.daily_bar.high),
                "low": float(snap.daily_bar.low),
                "close": float(snap.daily_bar.close),
                "volume": snap.daily_bar.volume,
            } if snap.daily_bar else None,
            "prev_daily_bar": {
                "open": float(snap.previous_daily_bar.open),
                "high": float(snap.previous_daily_bar.high),
                "low": float(snap.previous_daily_bar.low),
                "close": float(snap.previous_daily_bar.close),
                "volume": snap.previous_daily_bar.volume,
            } if snap.previous_daily_bar else None,
        }

    # ==================== Helpers ====================

    def _parse_tif(self, tif: str) -> TimeInForce:
        tif_map = {
            "day": TimeInForce.DAY,
            "gtc": TimeInForce.GTC,
            "ioc": TimeInForce.IOC,
            "fok": TimeInForce.FOK,
        }
        return tif_map.get(tif.lower(), TimeInForce.DAY)

    def _parse_timeframe(self, tf: str) -> TimeFrame:
        tf_lower = tf.lower()
        if "1min" in tf_lower:
            return TimeFrame(1, TimeFrameUnit.Minute)
        elif "5min" in tf_lower:
            return TimeFrame(5, TimeFrameUnit.Minute)
        elif "15min" in tf_lower:
            return TimeFrame(15, TimeFrameUnit.Minute)
        elif "30min" in tf_lower:
            return TimeFrame(30, TimeFrameUnit.Minute)
        elif "1hour" in tf_lower or "1h" in tf_lower:
            return TimeFrame(1, TimeFrameUnit.Hour)
        elif "1day" in tf_lower or "1d" in tf_lower:
            return TimeFrame(1, TimeFrameUnit.Day)
        else:
            return TimeFrame(5, TimeFrameUnit.Minute)

    def _order_to_dict(self, order) -> dict:
        return {
            "id": str(order.id),
            "symbol": order.symbol,
            "qty": float(order.qty) if order.qty else 0,
            "filled_qty": float(order.filled_qty) if order.filled_qty else 0,
            "side": order.side.value if order.side else "",
            "type": order.type.value if order.type else "",
            "status": order.status.value if order.status else "",
            "limit_price": float(order.limit_price) if order.limit_price else None,
            "stop_price": float(order.stop_price) if order.stop_price else None,
            "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            "time_in_force": order.time_in_force.value if order.time_in_force else "",
            "created_at": str(order.created_at) if order.created_at else "",
            "submitted_at": str(order.submitted_at) if order.submitted_at else "",
            "filled_at": str(order.filled_at) if order.filled_at else "",
        }

    def _position_to_dict(self, position) -> dict:
        return {
            "symbol": position.symbol,
            "qty": float(position.qty),
            "side": position.side.value if position.side else "",
            "avg_entry_price": float(position.avg_entry_price),
            "market_value": float(position.market_value),
            "cost_basis": float(position.cost_basis),
            "unrealized_pl": float(position.unrealized_pl),
            "unrealized_plpc": float(position.unrealized_plpc),
            "current_price": float(position.current_price),
            "change_today": float(position.change_today),
        }
