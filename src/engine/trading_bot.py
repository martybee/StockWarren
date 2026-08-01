"""
Main Trading Bot Engine for StockWarren
Orchestrates signals, risk management, and order execution
"""

import logging
import time
from datetime import datetime
from typing import Optional
from configparser import ConfigParser

from broker.client import AlpacaClient
from src.indicators.technical import TechnicalIndicators
from src.ml.signal_validator import SignalValidator
from src.scanner.stock_scanner import StockScanner, WatchlistManager
from src.engine.risk_manager import RiskManager
from src.engine.safety import (
    KillSwitch,
    validate_market_data,
    validate_order_response,
    compute_uncertainty_factor,
)

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

        # Position/cash limits are declared in [trading] but enforced by the
        # RiskManager — forward them so they aren't silently ignored.
        for key in ("max_positions", "max_position_pct", "min_cash_reserve_pct"):
            if key in trading_cfg:
                risk_cfg[key] = trading_cfg[key]

        # Initialize components
        paper = self.config.get("trading", "mode", fallback="both") != "live_only"
        self.alpaca = AlpacaClient(paper=True)  # Always start with paper
        self.indicators = TechnicalIndicators(indicator_cfg)
        self.ml_validator = SignalValidator(model_dir="data/models")
        self.risk_manager = RiskManager(risk_cfg)
        # Re-apply any human-operator overrides persisted from a previous session
        # (bounded by RiskManager's hard caps; leverage is never overridden).
        from src.utils.overrides import load_overrides
        persisted_overrides = load_overrides()
        if persisted_overrides:
            result = self.risk_manager.apply_operator_override(persisted_overrides)
            logger.info("Re-applied persisted operator overrides: %s", result.get("applied"))
        self.scanner = StockScanner(self.alpaca, scanner_cfg)
        self.watchlist = WatchlistManager(
            self.config.get("watchlist", "symbols", fallback="AAPL,MSFT,GOOGL")
        )

        # Settings
        self.min_signal_strength = signal_cfg.get("min_signal_strength", 65)
        self.min_confirmations = signal_cfg.get("min_confirmations", 2)
        self.bar_interval = self.config.get("performance", "bar_interval", fallback="5")
        try:
            self.bar_interval_min = int(self.bar_interval)
        except (TypeError, ValueError):
            self.bar_interval_min = 5
        self.market_hours_only = trading_cfg.get("market_hours_only", True)

        # Safety layer (Rule 4: kill switch the bot can trip but not reset)
        self.kill_switch = KillSwitch()
        self._halt_new_orders = False  # per-tick latch, set by Rule 6 breaches

        # Trade log
        self.trade_log = []

        if self.kill_switch.is_tripped():
            logger.critical(
                "KILL SWITCH is engaged at startup: %s. No new orders will be placed. %s",
                self.kill_switch.reason(), KillSwitch.manual_reset_instructions()
            )

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
        # Reset the per-tick "no new orders" latch (Rule 6).
        self._halt_new_orders = False

        # Rule 4: kill switch halts NEW orders. Existing positions are still
        # managed (protective stops keep tightening) — we never abandon a
        # position just because entries are frozen.
        if self.kill_switch.is_tripped():
            logger.critical("KILL SWITCH engaged (%s) — managing open positions only, no new orders.",
                            self.kill_switch.reason())
            self._update_positions()
            return

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
            if self._halt_new_orders:
                logger.warning("New orders halted for this cycle (Rule 6). Skipping remaining symbols.")
                break
            if symbol in self.risk_manager.active_positions:
                continue  # Already in this position

            try:
                self._evaluate_symbol(symbol, portfolio_value, cash)
            except Exception as e:
                logger.warning(f"Failed to evaluate {symbol}: {e}")

    def _evaluate_symbol(self, symbol: str, portfolio_value: float, cash: float):
        """Evaluate a symbol for potential entry (every safety gate applied)."""
        # Rule 5: missing / stale / contradictory / malformed data => NO TRADE.
        timeframe = f"{self.bar_interval}Min"
        df = self.alpaca.get_bars(symbol, timeframe=timeframe, limit=200)
        data_check = validate_market_data(df, self.bar_interval_min)
        if not data_check.ok:
            logger.debug(f"[{symbol}] NO TRADE — data check failed: {data_check.reason}")
            return

        # The strategy PROPOSES a signal (Rule 1: this is only a proposal).
        composite = self.indicators.analyze(df)
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
        current_price = float(df["close"].iloc[-1])
        atr_stop = self.indicators.get_atr_stop_price(df, is_long)
        stop_price = self.risk_manager.calculate_stop_price(current_price, is_long, atr_stop)
        target_price = self.risk_manager.calculate_target_price(current_price, is_long)

        # Risk/reward (needed both as a gate and as an uncertainty input)
        rr_ok, rr_ratio = self.risk_manager.check_risk_reward(
            current_price, stop_price, target_price
        )
        if not rr_ok:
            logger.debug(f"[{symbol}] R:R ratio too low: {rr_ratio:.2f}")
            return

        # Rule 7: any uncertainty shrinks the position (or drops the trade).
        uncertainty_factor = compute_uncertainty_factor(
            strength=composite.strength,
            min_strength=self.min_signal_strength,
            confirmations=composite.confirmations,
            min_confirmations=self.min_confirmations,
            rr_ratio=rr_ratio,
            min_rr_ratio=self.risk_manager.min_risk_reward_ratio,
            ml_confidence=ml_result.confidence if ml_result else None,
            ml_trained=self.ml_validator.is_trained,
        )
        if uncertainty_factor <= 0:
            logger.debug(f"[{symbol}] NO TRADE — signal too uncertain to size")
            return

        # The strategy's proposed size...
        proposed_qty = self.risk_manager.calculate_position_size(
            symbol, current_price, stop_price, portfolio_value, cash
        )

        # Rule 1: ...but the risk engine has the final say and can shrink/reject.
        approved, reason, qty = self.risk_manager.approve_order(
            symbol=symbol, side=side, qty=proposed_qty, price=current_price,
            stop_price=stop_price, target_price=target_price,
            portfolio_value=portfolio_value, cash=cash,
            uncertainty_factor=uncertainty_factor,
        )
        if not approved:
            logger.info(f"[{symbol}] Risk engine REJECTED order: {reason}")
            return

        logger.info(
            f"SIGNAL: {side.upper()} {qty} {symbol} @ ~${current_price:.2f} "
            f"stop=${stop_price:.2f} target=${target_price:.2f} "
            f"R:R={rr_ratio:.2f} strength={composite.strength:.0f}% uncert=x{uncertainty_factor:.2f}"
        )

        # ---- Place entry order ----
        limit_price = round(current_price * (1.001 if is_long else 0.999), 2)
        try:
            order = self.alpaca.place_limit_order(
                symbol=symbol, qty=qty, side=side,
                limit_price=limit_price, time_in_force="day",
            )
        except Exception as e:
            logger.error(f"[{symbol}] Entry order submission failed: {e}")
            return

        # Rule 6: validate the broker's response before trusting it.
        order_check = validate_order_response(order)
        if not order_check.ok:
            if order_check.classification == "unrecognized":
                self.kill_switch.trip(
                    f"Unrecognized broker response for {symbol}: {order_check.reason}"
                )
                self._halt_new_orders = True
                logger.critical(
                    f"[{symbol}] {order_check.reason} — kill switch tripped; no new orders."
                )
                oid = order.get("id") if isinstance(order, dict) else None
                if oid:
                    self.alpaca.cancel_order(oid)
            else:
                logger.warning(
                    f"[{symbol}] Entry not established: {order_check.reason}. No position registered."
                )
            return

        # Entry accepted — register with the risk manager.
        self.risk_manager.register_position(
            symbol=symbol, side=side, qty=qty,
            entry_price=current_price, stop_price=stop_price, target_price=target_price,
        )

        # Rule 4: a position without a protective stop must not exist. If the
        # stop cannot be placed and accepted, unwind the entry immediately.
        stop_side = "sell" if is_long else "buy"
        try:
            stop_order = self.alpaca.place_stop_order(
                symbol=symbol, qty=qty, side=stop_side,
                stop_price=round(stop_price, 2), time_in_force="gtc",
            )
            stop_check = validate_order_response(stop_order)
            if not stop_check.ok:
                raise RuntimeError(f"stop not accepted: {stop_check.reason}")
        except Exception as e:
            logger.critical(
                f"[{symbol}] STOP PLACEMENT FAILED ({e}). Unwinding entry — no naked positions."
            )
            self._unwind_unprotected(
                symbol, order.get("id") if isinstance(order, dict) else None
            )
            return

        # Log + notify
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
            "uncertainty_factor": uncertainty_factor,
            "order_id": order.get("id"),
        })
        self._notify_trade(symbol, side, qty, current_price, stop_price, target_price)

    def _unwind_unprotected(self, symbol: str, entry_order_id: Optional[str]):
        """Rule 4 fail-safe: cancel the entry and flatten any resulting position."""
        if entry_order_id:
            try:
                self.alpaca.cancel_order(entry_order_id)
            except Exception as e:
                logger.error(f"[{symbol}] Failed to cancel unprotected entry order: {e}")
        try:
            self.alpaca.close_position(symbol)
        except Exception as e:
            logger.error(f"[{symbol}] Failed to flatten unprotected position: {e}")
        # Stop tracking a position we just killed.
        self.risk_manager.active_positions.pop(symbol, None)

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
        """Emergency: close all positions, cancel all orders, and engage the
        kill switch so no new orders can be placed until a human clears it."""
        logger.warning("EMERGENCY STOP ACTIVATED")
        self.kill_switch.trip("Emergency stop activated")
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
