"""
Risk Management Engine for StockWarren
Adapted from FutureWarren's safety-first approach
One-way trailing stops, position sizing, daily loss limits
"""

import logging
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TradeStats:
    """Track trading statistics"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    daily_pnl: float = 0.0
    max_drawdown: float = 0.0
    current_drawdown: float = 0.0
    peak_equity: float = 0.0
    consecutive_losses: int = 0
    trading_day: date = field(default_factory=date.today)

    @property
    def win_rate(self) -> float:
        return (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0.0

    @property
    def profit_factor(self) -> float:
        if self.losing_trades == 0:
            return float("inf") if self.winning_trades > 0 else 0.0
        avg_win = self.total_pnl / self.winning_trades if self.winning_trades > 0 else 0
        avg_loss = abs(self.total_pnl) / self.losing_trades if self.losing_trades > 0 else 1
        return avg_win / avg_loss if avg_loss > 0 else 0.0


@dataclass
class Position:
    """Track an active position"""
    symbol: str
    side: str               # "buy" or "sell"
    qty: float
    entry_price: float
    entry_time: datetime
    stop_price: float
    target_price: float
    trailing_stop_active: bool = False
    trailing_stop_price: float = 0.0
    highest_price: float = 0.0    # MFE tracking for longs
    lowest_price: float = 999999  # MFE tracking for shorts
    trade_type: str = "day"       # "day" or "swing"
    order_ids: list = field(default_factory=list)


class RiskManager:
    """Manages risk, position sizing, and trade safety"""

    def __init__(self, config: dict):
        # Daily limits
        self.max_daily_loss = config.get("max_daily_loss", 500.0)
        self.max_daily_loss_pct = config.get("max_daily_loss_pct", 2.0)

        # Position limits
        self.max_positions = config.get("max_positions", 5)
        self.max_position_pct = config.get("max_position_pct", 20.0)
        self.min_cash_reserve_pct = config.get("min_cash_reserve_pct", 10.0)

        # Stop loss settings
        self.default_stop_loss_pct = config.get("default_stop_loss_pct", 2.0)
        self.default_take_profit_pct = config.get("default_take_profit_pct", 4.0)

        # Trailing stop
        self.trailing_stop_activation_pct = config.get("trailing_stop_activation_pct", 2.0)
        self.trailing_stop_distance_pct = config.get("trailing_stop_distance_pct", 1.0)

        # Safety
        self.max_consecutive_losses = config.get("max_consecutive_losses", 3)
        self.pause_duration_minutes = config.get("pause_duration_minutes", 60)
        self.min_risk_reward_ratio = config.get("min_risk_reward_ratio", 2.0)

        # State
        self.stats = TradeStats()
        self.active_positions: dict[str, Position] = {}
        self.is_paused = False
        self.pause_until: Optional[datetime] = None

    def is_trading_allowed(self, portfolio_value: float) -> tuple[bool, str]:
        """Check if trading is currently allowed"""
        # Check pause
        if self.is_paused:
            if self.pause_until and datetime.now() < self.pause_until:
                return False, f"Trading paused until {self.pause_until.strftime('%H:%M')}"
            else:
                self.is_paused = False
                logger.info("Trading pause ended")

        # Reset daily stats if new day
        self._check_daily_reset()

        # Check daily loss limit (absolute)
        if self.stats.daily_pnl <= -self.max_daily_loss:
            return False, f"Daily loss limit reached: ${self.stats.daily_pnl:.2f}"

        # Check daily loss limit (percentage)
        if portfolio_value > 0:
            daily_loss_pct = (abs(self.stats.daily_pnl) / portfolio_value) * 100
            if self.stats.daily_pnl < 0 and daily_loss_pct >= self.max_daily_loss_pct:
                return False, f"Daily loss % limit reached: {daily_loss_pct:.1f}%"

        # Check consecutive losses
        if self.stats.consecutive_losses >= self.max_consecutive_losses:
            self._pause_trading()
            return False, f"Max consecutive losses ({self.max_consecutive_losses}) reached"

        # Check position limit
        if len(self.active_positions) >= self.max_positions:
            return False, f"Max positions ({self.max_positions}) reached"

        return True, "Trading allowed"

    def calculate_position_size(self, symbol: str, price: float,
                                 stop_price: float, portfolio_value: float,
                                 cash: float) -> int:
        """Calculate position size based on risk parameters"""
        # Maximum position value based on portfolio percentage
        max_position_value = portfolio_value * (self.max_position_pct / 100.0)

        # Cash reserve check
        min_cash = portfolio_value * (self.min_cash_reserve_pct / 100.0)
        available_cash = cash - min_cash
        if available_cash <= 0:
            logger.warning(f"Cash reserve limit reached. Cash: ${cash:.2f}, Min: ${min_cash:.2f}")
            return 0

        max_position_value = min(max_position_value, available_cash)

        # Risk-based sizing: risk per trade = 1% of portfolio
        risk_per_share = abs(price - stop_price)
        if risk_per_share <= 0:
            logger.warning("Invalid stop price - equal to or beyond entry price")
            return 0

        risk_amount = portfolio_value * 0.01  # 1% risk per trade
        risk_based_qty = int(risk_amount / risk_per_share)

        # Value-based sizing
        value_based_qty = int(max_position_value / price)

        # Take the smaller of the two
        qty = min(risk_based_qty, value_based_qty)

        # Ensure at least 1 share
        return max(1, qty) if qty > 0 else 0

    def calculate_stop_price(self, entry_price: float, is_long: bool,
                              atr_stop: float = None) -> float:
        """Calculate initial stop loss price"""
        if atr_stop is not None:
            return atr_stop

        if is_long:
            return entry_price * (1 - self.default_stop_loss_pct / 100.0)
        else:
            return entry_price * (1 + self.default_stop_loss_pct / 100.0)

    def calculate_target_price(self, entry_price: float, is_long: bool) -> float:
        """Calculate take profit target price"""
        if is_long:
            return entry_price * (1 + self.default_take_profit_pct / 100.0)
        else:
            return entry_price * (1 - self.default_take_profit_pct / 100.0)

    def check_risk_reward(self, entry_price: float, stop_price: float,
                           target_price: float) -> tuple[bool, float]:
        """Check if trade meets minimum risk/reward ratio"""
        risk = abs(entry_price - stop_price)
        reward = abs(target_price - entry_price)

        if risk <= 0:
            return False, 0.0

        ratio = reward / risk
        return ratio >= self.min_risk_reward_ratio, ratio

    def update_trailing_stop(self, symbol: str, current_price: float) -> Optional[float]:
        """
        Update trailing stop for a position
        CRITICAL: Stop can ONLY tighten, NEVER loosen (from FutureWarren)
        """
        if symbol not in self.active_positions:
            return None

        pos = self.active_positions[symbol]
        is_long = pos.side == "buy"
        new_stop = None

        if is_long:
            # Track highest price
            if current_price > pos.highest_price:
                pos.highest_price = current_price

            # Check if trailing stop should activate
            profit_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
            if profit_pct >= self.trailing_stop_activation_pct:
                pos.trailing_stop_active = True

            if pos.trailing_stop_active:
                trail_distance = pos.highest_price * (self.trailing_stop_distance_pct / 100.0)
                calculated_stop = pos.highest_price - trail_distance

                # ONE-WAY: Stop can ONLY move UP for longs
                if calculated_stop > pos.stop_price:
                    new_stop = calculated_stop
                    pos.stop_price = new_stop
                    pos.trailing_stop_price = new_stop
                    logger.info(
                        f"[{symbol}] Trailing stop tightened to ${new_stop:.2f} "
                        f"(high: ${pos.highest_price:.2f})"
                    )
        else:
            # Short position
            if current_price < pos.lowest_price:
                pos.lowest_price = current_price

            profit_pct = ((pos.entry_price - current_price) / pos.entry_price) * 100
            if profit_pct >= self.trailing_stop_activation_pct:
                pos.trailing_stop_active = True

            if pos.trailing_stop_active:
                trail_distance = pos.lowest_price * (self.trailing_stop_distance_pct / 100.0)
                calculated_stop = pos.lowest_price + trail_distance

                # ONE-WAY: Stop can ONLY move DOWN for shorts
                if calculated_stop < pos.stop_price:
                    new_stop = calculated_stop
                    pos.stop_price = new_stop
                    pos.trailing_stop_price = new_stop
                    logger.info(
                        f"[{symbol}] Trailing stop tightened to ${new_stop:.2f} "
                        f"(low: ${pos.lowest_price:.2f})"
                    )

        return new_stop

    def register_position(self, symbol: str, side: str, qty: float,
                           entry_price: float, stop_price: float,
                           target_price: float, trade_type: str = "day") -> Position:
        """Register a new active position"""
        pos = Position(
            symbol=symbol,
            side=side,
            qty=qty,
            entry_price=entry_price,
            entry_time=datetime.now(),
            stop_price=stop_price,
            target_price=target_price,
            trade_type=trade_type,
            highest_price=entry_price,
            lowest_price=entry_price,
        )
        self.active_positions[symbol] = pos
        logger.info(
            f"Position registered: {side} {qty} {symbol} @ ${entry_price:.2f} "
            f"stop=${stop_price:.2f} target=${target_price:.2f}"
        )
        return pos

    def close_position(self, symbol: str, exit_price: float) -> float:
        """Close a position and record the result"""
        if symbol not in self.active_positions:
            return 0.0

        pos = self.active_positions.pop(symbol)

        if pos.side == "buy":
            pnl = (exit_price - pos.entry_price) * pos.qty
        else:
            pnl = (pos.entry_price - exit_price) * pos.qty

        self._record_trade(pnl)

        logger.info(
            f"Position closed: {symbol} P&L=${pnl:.2f} "
            f"(entry=${pos.entry_price:.2f} exit=${exit_price:.2f})"
        )
        return pnl

    def get_stats(self) -> dict:
        """Get current trading statistics"""
        return {
            "total_trades": self.stats.total_trades,
            "winning_trades": self.stats.winning_trades,
            "losing_trades": self.stats.losing_trades,
            "win_rate": self.stats.win_rate,
            "total_pnl": self.stats.total_pnl,
            "daily_pnl": self.stats.daily_pnl,
            "max_drawdown": self.stats.max_drawdown,
            "consecutive_losses": self.stats.consecutive_losses,
            "active_positions": len(self.active_positions),
            "is_paused": self.is_paused,
        }

    def _record_trade(self, pnl: float):
        """Record a trade result"""
        self.stats.total_trades += 1
        self.stats.total_pnl += pnl
        self.stats.daily_pnl += pnl

        if pnl > 0:
            self.stats.winning_trades += 1
            self.stats.consecutive_losses = 0
        else:
            self.stats.losing_trades += 1
            self.stats.consecutive_losses += 1

        # Drawdown tracking
        if self.stats.total_pnl > self.stats.peak_equity:
            self.stats.peak_equity = self.stats.total_pnl
        current_dd = self.stats.peak_equity - self.stats.total_pnl
        if current_dd > self.stats.max_drawdown:
            self.stats.max_drawdown = current_dd

    def _pause_trading(self):
        """Pause trading after consecutive losses"""
        self.is_paused = True
        from datetime import timedelta
        self.pause_until = datetime.now() + timedelta(minutes=self.pause_duration_minutes)
        logger.warning(
            f"Trading paused for {self.pause_duration_minutes} minutes "
            f"after {self.stats.consecutive_losses} consecutive losses"
        )

    def _check_daily_reset(self):
        """Reset daily stats if it's a new trading day"""
        today = date.today()
        if self.stats.trading_day != today:
            logger.info(f"New trading day: {today}. Resetting daily stats.")
            self.stats.daily_pnl = 0.0
            self.stats.consecutive_losses = 0
            self.stats.trading_day = today
            self.is_paused = False
            self.pause_until = None
