"""
Main Trading Bot Engine for StockWarren
Orchestrates signals, risk management, and order execution
"""

import logging
import time
from datetime import datetime
from typing import Optional
from configparser import ConfigParser

from alpaca.client import AlpacaClient
from src.indicators.technical import TechnicalIndicators
from src.ml.signal_validator import SignalValidator
from src.scanner.stock_scanner import StockScanner, WatchlistManager
from src.engine.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class TradingBot:
    """Main trading bot that coordinates all components"""

    def __init__(self, config_path: str = "config/settings.ini"):
        self.config = ConfigParser()
        self.config.read(config_path)
        self.running = False

        # Parse config sections
        trading_cfg = dict(self.config["trading"])
        signal_cfg = dict(self.config["signals"])
        risk_cfg = dict(self.config["risk_management"])
        indicator_cfg = dict(self.config["indicators"])
        scanner_cfg = dict(self.config["scanner"])

        # Convert numeric config values
        self._convert_config_types(trading_cfg)
        self._convert_config_types(signal_cfg)
        self._convert_config_types(risk_cfg)
        self._convert_config_types(indicator_cfg)
        self._convert_config_types(scanner_cfg)

        # Merge signal weights into indicator config
        for key in signal_cfg:
            if key.startswith("weight_"):
                indicator_cfg[key] = signal_cfg[key]

        # Initialize components
        paper = self.config.get("trading", "mode", fallback="both") != "live_only"
        self.alpaca = AlpacaClient(paper=True)  # Always start with paper
        self.indicators = TechnicalIndicators(indicator_cfg)
        self.ml_validator = SignalValidator(model_dir="data/models")
        self.risk_manager = RiskManager(risk_cfg)
        self.scanner = StockScanner(self.alpaca, scanner_cfg)
        self.watchlist = WatchlistManager(
            self.config.get("watchlist", "symbols", fallback="AAPL,MSFT,GOOGL")
        )

        # Settings
        self.min_signal_strength = signal_cfg.get("min_signal_strength", 65)
        self.min_confirmations = signal_cfg.get("min_confirmations", 2)
        self.bar_interval = self.config.get("performance", "bar_interval", fallback="5")
        self.market_hours_only = trading_cfg.get("market_hours_only", True)

        # Trade log
        self.trade_log = []

        logger.info("StockWarren Trading Bot initialized")

    def start(self):
        """Start the trading bot main loop"""
        self.running = True
        logger.info("Trading bot started")

        account = self.alpaca.get_account()
        logger.info(
            f"Account: ${account['portfolio_value']:.2f} portfolio, "
            f"${account['cash']:.2f} cash, "
            f"{'PAPER' if self.alpaca.paper else 'LIVE'} mode"
        )

        while self.running:
            try:
                self._tick()
                time.sleep(int(self.bar_interval) * 60)  # Wait for next bar
            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                self.stop()
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                time.sleep(30)

    def stop(self):
        """Stop the trading bot"""
        self.running = False
        logger.info("Trading bot stopped")

    def _tick(self):
        """Execute one iteration of the trading loop"""
        # Check market hours
        if self.market_hours_only and not self.alpaca.is_market_open():
            logger.debug("Market is closed, skipping tick")
            return

        # Get account state
        account = self.alpaca.get_account()
        portfolio_value = account["portfolio_value"]
        cash = account["cash"]

        # Check if trading is allowed
        allowed, reason = self.risk_manager.is_trading_allowed(portfolio_value)
        if not allowed:
            logger.info(f"Trading not allowed: {reason}")
            # Still update trailing stops for existing positions
            self._update_positions()
            return

        # Update existing positions (trailing stops, etc.)
        self._update_positions()

        # Scan for new opportunities
        symbols = self.watchlist.get_symbols()
        for symbol in symbols:
            if symbol in self.risk_manager.active_positions:
                continue  # Already in this position

            try:
                self._evaluate_symbol(symbol, portfolio_value, cash)
            except Exception as e:
                logger.warning(f"Failed to evaluate {symbol}: {e}")

    def _evaluate_symbol(self, symbol: str, portfolio_value: float, cash: float):
        """Evaluate a symbol for potential entry"""
        # Get market data
        timeframe = f"{self.bar_interval}Min"
        df = self.alpaca.get_bars(symbol, timeframe=timeframe, limit=200)

        if df is None or len(df) < 50:
            return

        # Run technical analysis
        composite = self.indicators.analyze(df)

        # Check signal strength and confirmations
        if composite.strength < self.min_signal_strength:
            return
        if composite.confirmations < self.min_confirmations:
            return

        # ML validation (if trained)
        ml_result = self.ml_validator.validate_signal(df, composite)
        if self.ml_validator.is_trained and not ml_result.approved:
            logger.debug(
                f"[{symbol}] Signal rejected by ML (confidence: {ml_result.confidence:.1f}%)"
            )
            return

        # Determine trade direction
        is_long = composite.direction == 1
        side = "buy" if is_long else "sell"

        # Calculate stop and target
        current_price = df["close"].iloc[-1]
        atr_stop = self.indicators.get_atr_stop_price(df, is_long)
        stop_price = self.risk_manager.calculate_stop_price(current_price, is_long, atr_stop)
        target_price = self.risk_manager.calculate_target_price(current_price, is_long)

        # Check risk/reward
        rr_ok, rr_ratio = self.risk_manager.check_risk_reward(
            current_price, stop_price, target_price
        )
        if not rr_ok:
            logger.debug(f"[{symbol}] R:R ratio too low: {rr_ratio:.2f}")
            return

        # Calculate position size
        qty = self.risk_manager.calculate_position_size(
            symbol, current_price, stop_price, portfolio_value, cash
        )
        if qty <= 0:
            return

        # Place entry order
        logger.info(
            f"SIGNAL: {side.upper()} {qty} {symbol} @ ~${current_price:.2f} "
            f"stop=${stop_price:.2f} target=${target_price:.2f} "
            f"R:R={rr_ratio:.2f} strength={composite.strength:.0f}%"
        )

        try:
            # Use limit order slightly above/below current price
            if is_long:
                limit_price = round(current_price * 1.001, 2)  # 0.1% above
            else:
                limit_price = round(current_price * 0.999, 2)  # 0.1% below

            order = self.alpaca.place_limit_order(
                symbol=symbol,
                qty=qty,
                side=side,
                limit_price=limit_price,
                time_in_force="day",
            )

            # Register position with risk manager
            self.risk_manager.register_position(
                symbol=symbol,
                side=side,
                qty=qty,
                entry_price=current_price,
                stop_price=stop_price,
                target_price=target_price,
            )

            # Place stop loss order
            stop_side = "sell" if is_long else "buy"
            self.alpaca.place_stop_order(
                symbol=symbol,
                qty=qty,
                side=stop_side,
                stop_price=round(stop_price, 2),
                time_in_force="gtc",
            )

            # Log trade
            self._log_trade({
                "time": datetime.now().isoformat(),
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "entry_price": current_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "signal_strength": composite.strength,
                "confirmations": composite.confirmations,
                "ml_confidence": ml_result.confidence if ml_result else 0,
                "rr_ratio": rr_ratio,
                "order_id": order.get("id"),
            })

            # Notify
            self._notify_trade(symbol, side, qty, current_price, stop_price, target_price)

        except Exception as e:
            logger.error(f"Failed to place order for {symbol}: {e}")

    def _update_positions(self):
        """Update trailing stops and check targets for active positions"""
        positions = self.alpaca.get_positions()

        for pos_data in positions:
            symbol = pos_data["symbol"]
            current_price = pos_data["current_price"]

            if symbol not in self.risk_manager.active_positions:
                continue

            # Update trailing stop
            new_stop = self.risk_manager.update_trailing_stop(symbol, current_price)

            if new_stop is not None:
                # Cancel old stop order and place new one
                try:
                    pos = self.risk_manager.active_positions[symbol]
                    is_long = pos.side == "buy"
                    stop_side = "sell" if is_long else "buy"

                    # Cancel existing stop orders for this symbol
                    orders = self.alpaca.get_orders(status="open")
                    for order in orders:
                        if (order["symbol"] == symbol and
                            order["type"] in ("stop", "stop_limit") and
                            order["side"] == stop_side):
                            self.alpaca.cancel_order(order["id"])

                    # Place new tighter stop
                    self.alpaca.place_stop_order(
                        symbol=symbol,
                        qty=pos.qty,
                        side=stop_side,
                        stop_price=round(new_stop, 2),
                        time_in_force="gtc",
                    )
                except Exception as e:
                    logger.error(f"Failed to update stop for {symbol}: {e}")

            # Check if target reached
            pos = self.risk_manager.active_positions[symbol]
            is_long = pos.side == "buy"

            target_hit = (
                (is_long and current_price >= pos.target_price) or
                (not is_long and current_price <= pos.target_price)
            )

            if target_hit:
                logger.info(f"[{symbol}] Target reached at ${current_price:.2f}")
                try:
                    self.alpaca.close_position(symbol)
                    pnl = self.risk_manager.close_position(symbol, current_price)

                    # Train ML with outcome
                    df = self.alpaca.get_bars(symbol, limit=200)
                    if df is not None:
                        composite = self.indicators.analyze(df)
                        self.ml_validator.add_training_sample(df, composite, pnl > 0)

                    self._notify_close(symbol, current_price, pnl, "target")
                except Exception as e:
                    logger.error(f"Failed to close position {symbol}: {e}")

    def emergency_stop(self):
        """Emergency: close all positions and cancel all orders"""
        logger.warning("EMERGENCY STOP ACTIVATED")
        self.alpaca.cancel_all_orders()
        self.alpaca.close_all_positions()
        self.running = False

    def get_status(self) -> dict:
        """Get current bot status for dashboard"""
        try:
            account = self.alpaca.get_account()
        except Exception:
            account = {"portfolio_value": 0, "cash": 0, "equity": 0}

        return {
            "running": self.running,
            "paper_mode": self.alpaca.paper,
            "account": account,
            "stats": self.risk_manager.get_stats(),
            "active_positions": len(self.risk_manager.active_positions),
            "watchlist": self.watchlist.get_symbols(),
            "market_open": self.alpaca.is_market_open() if self.running else False,
        }

    def _log_trade(self, trade: dict):
        """Log a trade to history"""
        self.trade_log.append(trade)

        # Also write to CSV
        import csv
        import os
        log_file = "logs/trade_history.csv"
        file_exists = os.path.exists(log_file)

        with open(log_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trade.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(trade)

    def _notify_trade(self, symbol, side, qty, price, stop, target):
        """Send trade notification (placeholder for notification system)"""
        pass  # Implemented by notification module

    def _notify_close(self, symbol, price, pnl, reason):
        """Send close notification (placeholder for notification system)"""
        pass  # Implemented by notification module

    def _convert_config_types(self, config_dict: dict):
        """Convert config string values to appropriate types"""
        for key, value in config_dict.items():
            if isinstance(value, str):
                # Try int
                try:
                    config_dict[key] = int(value)
                    continue
                except ValueError:
                    pass
                # Try float
                try:
                    config_dict[key] = float(value)
                    continue
                except ValueError:
                    pass
                # Try bool
                if value.lower() in ("true", "yes", "on"):
                    config_dict[key] = True
                elif value.lower() in ("false", "no", "off"):
                    config_dict[key] = False
