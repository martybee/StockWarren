"""
Risk Management Engine for StockWarren
Adapted from FutureWarren's safety-first approach
One-way trailing stops, position sizing, daily loss limits
"""

import logging
from datetime import datetime, date
from dataclasses import dataclass, field, replace
from typing import Optional

from src.engine.safety import (
    MAX_LEVERAGE,
    GATE_CONSECUTIVE_LOSSES,
    GateSnapshot,
    GateVerdict,
    evaluate_trading_gate,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RiskLimits:
    """
    Immutable snapshot of every risk limit (Rule 3: limits cannot change at
    runtime). Frozen => any attempt to reassign a field raises FrozenInstanceError,
    and the RiskManager exposes these only through read-only properties.
    """
    max_daily_loss: float
    max_daily_loss_pct: float
    max_positions: int
    max_position_pct: float
    min_cash_reserve_pct: float
    risk_per_trade_pct: float
    default_stop_loss_pct: float
    default_take_profit_pct: float
    trailing_stop_activation_pct: float
    trailing_stop_distance_pct: float
    max_consecutive_losses: int
    pause_duration_minutes: int
    min_risk_reward_ratio: float
    max_risk_per_trade_pct: float          # hard ceiling on a single trade's risk
    max_combined_open_risk_pct: float      # cap on summed risk across open positions
    max_weekly_loss_pct: float             # halt for the week past this loss
    review_drawdown_pct: float             # flag for strategy review at this drawdown
    shutdown_drawdown_pct: float           # trip the kill switch at this drawdown
    max_leverage: float = MAX_LEVERAGE


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
    weekly_pnl: float = 0.0
    trading_week: tuple = field(default_factory=lambda: tuple(date.today().isocalendar()[:2]))
    peak_portfolio_value: float = 0.0

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

    # Absolute ceilings for human-operator overrides. A human may TUNE limits
    # within these bounds via apply_operator_override(); exceeding them requires
    # a code change (defense in depth). Leverage is deliberately absent — it is
    # NEVER overridable and stays MAX_LEVERAGE (Rule 3).
    OVERRIDABLE = {
        "max_positions":              {"type": "int",   "min": 1,    "max": 20,      "label": "Max Positions"},
        "max_position_pct":           {"type": "float", "min": 1.0,  "max": 100.0,   "label": "Max Position % of Portfolio"},
        "risk_per_trade_pct":         {"type": "float", "min": 0.05, "max": 5.0,     "label": "Risk per Trade %"},
        "max_risk_per_trade_pct":     {"type": "float", "min": 0.1,  "max": 5.0,     "label": "Max Risk per Trade %"},
        "max_combined_open_risk_pct": {"type": "float", "min": 0.25, "max": 20.0,    "label": "Max Combined Open Risk %"},
        "max_daily_loss":             {"type": "float", "min": 1.0,  "max": 100000.0, "label": "Max Daily Loss $"},
        "max_daily_loss_pct":         {"type": "float", "min": 0.5,  "max": 50.0,    "label": "Daily Loss Limit %"},
        "max_weekly_loss_pct":        {"type": "float", "min": 0.5,  "max": 50.0,    "label": "Weekly Loss Limit %"},
        "review_drawdown_pct":        {"type": "float", "min": 1.0,  "max": 50.0,    "label": "Strategy Review Drawdown %"},
        "shutdown_drawdown_pct":      {"type": "float", "min": 1.0,  "max": 50.0,    "label": "Full Shutdown Drawdown %"},
        "min_risk_reward_ratio":      {"type": "float", "min": 1.0,  "max": 10.0,    "label": "Min Risk:Reward"},
    }

    def __init__(self, config: dict, correlation_groups: dict = None):
        # All risk limits live in a single frozen record. The STRATEGY can never
        # mutate them (Rule 3) — access is via read-only properties, no setters.
        # A human operator may replace the whole record via apply_operator_override().
        self._limits = _RiskLimits(
            max_daily_loss=config.get("max_daily_loss", 500.0),
            max_daily_loss_pct=config.get("max_daily_loss_pct", 2.0),
            max_positions=config.get("max_positions", 5),
            max_position_pct=config.get("max_position_pct", 20.0),
            min_cash_reserve_pct=config.get("min_cash_reserve_pct", 10.0),
            risk_per_trade_pct=config.get("risk_per_trade_pct", 1.0),
            default_stop_loss_pct=config.get("default_stop_loss_pct", 2.0),
            default_take_profit_pct=config.get("default_take_profit_pct", 4.0),
            trailing_stop_activation_pct=config.get("trailing_stop_activation_pct", 2.0),
            trailing_stop_distance_pct=config.get("trailing_stop_distance_pct", 1.0),
            max_consecutive_losses=config.get("max_consecutive_losses", 3),
            pause_duration_minutes=config.get("pause_duration_minutes", 60),
            min_risk_reward_ratio=config.get("min_risk_reward_ratio", 2.0),
            max_risk_per_trade_pct=config.get("max_risk_per_trade_pct", 0.50),
            max_combined_open_risk_pct=config.get("max_combined_open_risk_pct", 1.0),
            max_weekly_loss_pct=config.get("max_weekly_loss_pct", 3.0),
            review_drawdown_pct=config.get("review_drawdown_pct", 5.0),
            shutdown_drawdown_pct=config.get("shutdown_drawdown_pct", 8.0),
        )
        # Immutable snapshot of the config baseline, for reset_operator_override().
        self._base_limits = self._limits

        # Correlation groups: symbol -> group name (ungrouped symbols are their
        # own group). "One risk allocation per correlated group."
        self._corr_map: dict[str, str] = {}
        for group, syms in (correlation_groups or {}).items():
            if isinstance(syms, str):
                syms = [s.strip() for s in syms.split(",") if s.strip()]
            for s in syms:
                self._corr_map[s.upper()] = group

        # State (mutable — these are NOT limits)
        self.stats = TradeStats()
        self.active_positions: dict[str, Position] = {}
        self.is_paused = False
        self.pause_until: Optional[datetime] = None
        # Drawdown state (updated by is_trading_allowed)
        self.drawdown_pct = 0.0
        self.review_flagged = False      # >= review_drawdown_pct
        self.shutdown_triggered = False  # >= shutdown_drawdown_pct (bot trips kill switch)

    # ---- Read-only limit accessors (Rule 3: limits cannot change) ----
    @property
    def max_daily_loss(self) -> float: return self._limits.max_daily_loss

    @property
    def max_daily_loss_pct(self) -> float: return self._limits.max_daily_loss_pct

    @property
    def max_positions(self) -> int: return self._limits.max_positions

    @property
    def max_position_pct(self) -> float: return self._limits.max_position_pct

    @property
    def min_cash_reserve_pct(self) -> float: return self._limits.min_cash_reserve_pct

    @property
    def default_stop_loss_pct(self) -> float: return self._limits.default_stop_loss_pct

    @property
    def default_take_profit_pct(self) -> float: return self._limits.default_take_profit_pct

    @property
    def trailing_stop_activation_pct(self) -> float: return self._limits.trailing_stop_activation_pct

    @property
    def trailing_stop_distance_pct(self) -> float: return self._limits.trailing_stop_distance_pct

    @property
    def max_consecutive_losses(self) -> int: return self._limits.max_consecutive_losses

    @property
    def pause_duration_minutes(self) -> int: return self._limits.pause_duration_minutes

    @property
    def min_risk_reward_ratio(self) -> float: return self._limits.min_risk_reward_ratio

    @property
    def max_leverage(self) -> float: return self._limits.max_leverage

    @property
    def risk_per_trade_pct(self) -> float: return self._limits.risk_per_trade_pct

    @property
    def max_risk_per_trade_pct(self) -> float: return self._limits.max_risk_per_trade_pct

    @property
    def max_combined_open_risk_pct(self) -> float: return self._limits.max_combined_open_risk_pct

    @property
    def max_weekly_loss_pct(self) -> float: return self._limits.max_weekly_loss_pct

    @property
    def review_drawdown_pct(self) -> float: return self._limits.review_drawdown_pct

    @property
    def shutdown_drawdown_pct(self) -> float: return self._limits.shutdown_drawdown_pct

    def open_risk_amount(self) -> float:
        """Total $ at risk across open positions = sum(|entry - stop| * qty)."""
        return sum(abs(p.entry_price - p.stop_price) * p.qty
                   for p in self.active_positions.values())

    def correlation_group(self, symbol: str) -> str:
        """Correlation group for a symbol (its own symbol if ungrouped)."""
        return self._corr_map.get(symbol.upper(), symbol.upper())

    # ---- Human-operator overrides (NOT the strategy — see Rule 3) ----
    def apply_operator_override(self, overrides: dict) -> dict:
        """
        Set risk limits at runtime. This is a deliberate HUMAN-OPERATOR action,
        never called by the trading loop. Every value is validated and CLAMPED
        to [min, hard-max] from OVERRIDABLE; unknown keys and leverage are
        ignored. Returns {"applied": {...}, "notes": [...], "limits": {...}}.

        Rule 3 still holds: the strategy cannot reach this path, and even the
        operator is bounded by the hard ceilings above.
        """
        applied: dict = {}
        notes: list[str] = []
        for key, spec in self.OVERRIDABLE.items():
            if key not in overrides or overrides[key] is None or overrides[key] == "":
                continue
            raw = overrides[key]
            try:
                val = int(raw) if spec["type"] == "int" else float(raw)
            except (TypeError, ValueError):
                notes.append(f"{key}: ignored non-numeric value {raw!r}")
                continue
            lo, hi = spec["min"], spec["max"]
            clamped = max(lo, min(hi, val))
            if clamped != val:
                notes.append(f"{key}: {val} clamped to {clamped} (allowed {lo}-{hi})")
            applied[key] = clamped

        if applied:
            self._limits = replace(self._limits, **applied)
            logger.critical(
                "OPERATOR OVERRIDE applied: %s%s",
                applied, (" | " + "; ".join(notes)) if notes else "",
            )
        return {"applied": applied, "notes": notes, "limits": self.get_limits()}

    def reset_operator_override(self) -> dict:
        """Revert all limits to the config baseline (settings.ini)."""
        self._limits = self._base_limits
        logger.warning("OPERATOR OVERRIDE reset — limits restored to config baseline")
        return self.get_limits()

    def get_limits(self) -> dict:
        """Current effective limits (config baseline + any operator overrides)."""
        L = self._limits
        return {
            "max_positions": L.max_positions,
            "max_position_pct": L.max_position_pct,
            "risk_per_trade_pct": L.risk_per_trade_pct,
            "max_risk_per_trade_pct": L.max_risk_per_trade_pct,
            "max_combined_open_risk_pct": L.max_combined_open_risk_pct,
            "max_daily_loss": L.max_daily_loss,
            "max_daily_loss_pct": L.max_daily_loss_pct,
            "max_weekly_loss_pct": L.max_weekly_loss_pct,
            "review_drawdown_pct": L.review_drawdown_pct,
            "shutdown_drawdown_pct": L.shutdown_drawdown_pct,
            "min_cash_reserve_pct": L.min_cash_reserve_pct,
            "min_risk_reward_ratio": L.min_risk_reward_ratio,
            "max_consecutive_losses": L.max_consecutive_losses,
            "max_leverage": L.max_leverage,
        }

    def approve_order(self, *, symbol: str, side: str, qty: int, price: float,
                      stop_price: float, target_price: float,
                      portfolio_value: float, cash: float,
                      uncertainty_factor: float) -> tuple[bool, str, int]:
        """
        THE single mandatory veto gate (Rule 1). The strategy proposes; this
        method disposes. Returns (approved, reason, final_qty). A rejection
        always returns final_qty == 0. All checks are fail-closed.
        """
        # Proposed quantity must be sane
        if qty <= 0:
            return False, "non-positive proposed qty", 0
        if price <= 0:
            return False, "non-positive price", 0

        # Global trading gate (pause, daily loss, consecutive losses, max positions)
        allowed, reason = self.is_trading_allowed(portfolio_value)
        if not allowed:
            return False, reason, 0

        # Never re-enter a symbol we already hold
        if symbol in self.active_positions:
            return False, "already holding this symbol", 0

        # Correlation: at most one risk allocation per correlated group
        new_group = self.correlation_group(symbol)
        for held in self.active_positions:
            if self.correlation_group(held) == new_group:
                return False, f"correlated position already open in '{new_group}' ({held})", 0

        # Stop must be on the correct, loss-limiting side of entry (protects Rule 4)
        is_long = side.lower() == "buy"
        if is_long and not (stop_price < price):
            return False, f"long stop {stop_price} not below entry {price}", 0
        if not is_long and not (stop_price > price):
            return False, f"short stop {stop_price} not above entry {price}", 0

        # Minimum risk/reward
        rr_ok, rr_ratio = self.check_risk_reward(price, stop_price, target_price)
        if not rr_ok:
            return False, f"R:R {rr_ratio:.2f} < {self.min_risk_reward_ratio}", 0

        # Rule 7: uncertainty shrinks size; below the floor it is already 0.
        if uncertainty_factor <= 0:
            return False, "uncertainty factor collapsed to zero", 0
        final_qty = int(qty * uncertainty_factor)
        if final_qty <= 0:
            return False, "size rounded to zero after uncertainty scaling", 0

        # Rule 3 (leverage): cash-only, 1x. Notional may never exceed buying
        # power, and never exceeds the per-position % cap.
        notional = final_qty * price
        max_by_leverage = cash * self.max_leverage
        if notional > max_by_leverage:
            # Trim to the largest affordable size rather than reject outright.
            final_qty = int(max_by_leverage / price)
            if final_qty <= 0:
                return False, "insufficient buying power for 1 share (no leverage)", 0
            notional = final_qty * price

        max_position_value = portfolio_value * (self.max_position_pct / 100.0)
        if notional > max_position_value + 1e-9:
            final_qty = int(max_position_value / price)
            if final_qty <= 0:
                return False, "position cap below 1 share", 0

        # ---- Risk-based ceilings (trim or reject) ----
        risk_per_share = abs(price - stop_price)

        # Max risk per trade: a single trade's $risk may never exceed the cap.
        max_trade_risk = portfolio_value * (self.max_risk_per_trade_pct / 100.0)
        if risk_per_share * final_qty > max_trade_risk + 1e-9:
            final_qty = int(max_trade_risk / risk_per_share)
            if final_qty <= 0:
                return False, f"1 share risks more than max per-trade risk ({self.max_risk_per_trade_pct}%)", 0

        # Max combined open risk: summed risk of all open positions + this trade.
        combined_cap = portfolio_value * (self.max_combined_open_risk_pct / 100.0)
        room = combined_cap - self.open_risk_amount()
        if room <= 1e-9:
            return False, f"combined open-risk limit ({self.max_combined_open_risk_pct}%) reached", 0
        if risk_per_share * final_qty > room + 1e-9:
            final_qty = int(room / risk_per_share)
            if final_qty <= 0:
                return False, "no room under combined open-risk limit for 1 share", 0

        return True, f"approved qty={final_qty} (uncert x{uncertainty_factor:.2f})", final_qty

    def gate_snapshot(self, portfolio_value: float) -> GateSnapshot:
        """Immutable copy of everything the gate rules read. Pure — no mutation.

        Safe to call from anywhere, including request handlers. Contrast with
        is_trading_allowed(), which is tick-loop-only because it mutates.
        """
        return GateSnapshot(
            portfolio_value=portfolio_value,
            is_paused=self.is_paused,
            pause_until=self.pause_until,
            now=datetime.now(),
            daily_pnl=self.stats.daily_pnl,
            weekly_pnl=self.stats.weekly_pnl,
            consecutive_losses=self.stats.consecutive_losses,
            active_positions=len(self.active_positions),
            drawdown_pct=self.drawdown_pct,
            max_daily_loss=self.max_daily_loss,
            max_daily_loss_pct=self.max_daily_loss_pct,
            max_weekly_loss_pct=self.max_weekly_loss_pct,
            max_consecutive_losses=self.max_consecutive_losses,
            max_positions=self.max_positions,
            shutdown_drawdown_pct=self.shutdown_drawdown_pct,
        )

    def evaluate_gate(self, portfolio_value: float) -> GateVerdict:
        """Would a new position be allowed right now? PURE — mutates nothing.

        This is what the dashboard calls. is_trading_allowed() is NOT safe to call
        from a request handler; see the comment on that method.
        """
        return evaluate_trading_gate(self.gate_snapshot(portfolio_value))

    def is_trading_allowed(self, portfolio_value: float) -> tuple[bool, str]:
        """Tick-loop entry point: update time-based state, then decide.

        WARNING — THIS MUTATES. It expires pauses, rolls the daily/weekly stats,
        moves the peak-equity baseline that drawdown is measured from, sets
        shutdown_triggered (which the bot reads to trip the kill switch), and can
        start a pause. Call it ONLY from the tick loop. Anything that merely wants
        to know the answer — dashboards, status endpoints — must call
        evaluate_gate(), which is pure.

        The rules themselves live in safety.evaluate_trading_gate(); this method
        does the state updates and then delegates the verdict, so the engine and
        the dashboard cannot disagree about what "blocked" means.
        """
        # Check pause
        if self.is_paused:
            if self.pause_until and datetime.now() < self.pause_until:
                return False, f"Trading paused until {self.pause_until.strftime('%H:%M')}"
            else:
                self.is_paused = False
                logger.info("Trading pause ended")

        # Reset daily / weekly stats on new day / week
        self._check_daily_reset()
        self._check_weekly_reset()

        # Drawdown thresholds (peak-to-current on portfolio equity)
        if portfolio_value > 0:
            if portfolio_value > self.stats.peak_portfolio_value:
                self.stats.peak_portfolio_value = portfolio_value
            peak = self.stats.peak_portfolio_value
            self.drawdown_pct = ((peak - portfolio_value) / peak * 100) if peak > 0 else 0.0

            # Full shutdown: hardest stop — bot trips the kill switch on this flag.
            self.shutdown_triggered = self.drawdown_pct >= self.shutdown_drawdown_pct

            # Strategy review: warn but keep trading (surfaced in the UI).
            # Evaluated before the verdict because it is a warning tier, not a stop.
            if self.drawdown_pct >= self.review_drawdown_pct:
                if not self.review_flagged:
                    logger.warning("STRATEGY REVIEW: drawdown %.1f%% >= %.1f%% threshold",
                                   self.drawdown_pct, self.review_drawdown_pct)
                self.review_flagged = True
            else:
                self.review_flagged = False

        # Single source of the rules — see safety.evaluate_trading_gate().
        verdict = evaluate_trading_gate(self.gate_snapshot(portfolio_value))

        # Breaching the consecutive-loss limit STARTS the cool-off. That is a state
        # change, so it stays here rather than in the pure evaluator.
        if verdict.code == GATE_CONSECUTIVE_LOSSES and not self.is_paused:
            self._pause_trading()

        return verdict.allowed, verdict.reason

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

        risk_amount = portfolio_value * (self.risk_per_trade_pct / 100.0)  # configurable risk per trade
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
            "weekly_pnl": self.stats.weekly_pnl,
            "max_drawdown": self.stats.max_drawdown,
            "drawdown_pct": round(self.drawdown_pct, 2),
            "consecutive_losses": self.stats.consecutive_losses,
            "active_positions": len(self.active_positions),
            "open_risk_amount": round(self.open_risk_amount(), 2),
            "is_paused": self.is_paused,
            "review_flagged": self.review_flagged,
            "shutdown_triggered": self.shutdown_triggered,
        }

    def _record_trade(self, pnl: float):
        """Record a trade result"""
        self.stats.total_trades += 1
        self.stats.total_pnl += pnl
        self.stats.daily_pnl += pnl
        self.stats.weekly_pnl += pnl

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

    def _check_weekly_reset(self):
        """Reset weekly P&L at the start of a new ISO week"""
        week = tuple(date.today().isocalendar()[:2])
        if self.stats.trading_week != week:
            logger.info(f"New trading week: {week}. Resetting weekly P&L.")
            self.stats.weekly_pnl = 0.0
            self.stats.trading_week = week
