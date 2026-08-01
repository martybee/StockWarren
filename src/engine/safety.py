"""
StockWarren Trading Safety Layer — the "Trading Constitution".

These are HARD invariants. They are fail-closed: when in doubt, the correct
outcome is always NO TRADE / NO NEW ORDER / SMALLER SIZE, never "proceed and hope".

The seven rules (enforcement point in parentheses):

  1. The strategy may PROPOSE trades, but the risk engine can always REJECT them.
     -> RiskManager.approve_order() is the single mandatory veto gate.
  2. The strategy may PROPOSE code changes, but cannot modify production code
     automatically.  (Governance rule — enforced by process, see CLAUDE.md. The
     running bot has no code-editing capability by construction.)
  3. It cannot change position limits, loss limits or leverage.
     -> RiskManager limits are read-only properties backed by a frozen record;
        the STRATEGY has no setter and cannot mutate them. A human operator may
        retune them via RiskManager.apply_operator_override(), which validates
        and CLAMPS every value to hard ceilings (RiskManager.OVERRIDABLE).
        MAX_LEVERAGE is fixed at 1.0 (cash only) and is never overridable.
  4. It cannot disable stops or kill switches.
     -> Every entry MUST be protected by a stop or it is immediately closed
        (no naked positions). KillSwitch, once tripped, can only be reset by a
        human removing the on-disk file — the bot exposes no reset method.
  5. Missing, stale, contradictory or malformed data must produce NO TRADE.
     -> validate_market_data() gates every evaluation.
  6. Unrecognized broker responses must produce NO NEW ORDERS.
     -> validate_order_response() classifies every fill; "unrecognized" trips
        the halt / kill switch.
  7. Any uncertainty should reduce the position or result in no trade.
     -> compute_uncertainty_factor() scales size in [0, 1]; below the floor => 0.

This module is deliberately dependency-light (pandas only) and side-effect free
except for KillSwitch, which touches a single file.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Leverage is a hard constant, not a config value. Cash-only, 1x, always.
MAX_LEVERAGE = 1.0

# Position size may never be scaled ABOVE what the strategy asked for; if the
# uncertainty factor falls below this floor, the trade is dropped entirely.
MIN_SIZE_FACTOR = 0.34

# Broker order statuses we recognize as a successfully-working order.
ACCEPTED_ORDER_STATUSES = frozenset({
    "new", "accepted", "pending_new", "accepted_for_bidding",
    "partially_filled", "filled", "done_for_day", "calculated",
    "pending_replace", "replaced",
})

# Broker order statuses we recognize as a terminal FAILURE (order did not work).
TERMINAL_BAD_STATUSES = frozenset({
    "rejected", "canceled", "cancelled", "expired", "suspended", "stopped",
})

# The seven rules, in structured form — the single source of truth consumed by
# the dashboard (GET /api/safety). Keep this in sync with the module docstring
# and the "Trading Safety Constitution" table in CLAUDE.md.
RULES = [
    {"n": 1,
     "rule": "The strategy may propose trades, but the risk engine can always reject them.",
     "enforced_by": "RiskManager.approve_order()"},
    {"n": 2,
     "rule": "It may propose code changes, but cannot modify production code automatically.",
     "enforced_by": "governance — the running bot has no code-editing capability"},
    {"n": 3,
     "rule": "It cannot change position limits, loss limits or leverage.",
     "enforced_by": "strategy-immutable (frozen _RiskLimits); human operator may tune within hard caps; leverage fixed at 1.0"},
    {"n": 4,
     "rule": "It cannot disable stops or kill switches.",
     "enforced_by": "no naked positions + file-backed KillSwitch (no reset method)"},
    {"n": 5,
     "rule": "Missing, stale, contradictory or malformed data must produce NO TRADE.",
     "enforced_by": "validate_market_data()"},
    {"n": 6,
     "rule": "Unrecognized broker responses must produce NO NEW ORDERS.",
     "enforced_by": "validate_order_response() -> trips halt / kill switch"},
    {"n": 7,
     "rule": "Any uncertainty should reduce the position or result in no trade.",
     "enforced_by": "compute_uncertainty_factor() (0 below MIN_SIZE_FACTOR)"},
]


@dataclass(frozen=True)
class DataCheck:
    ok: bool
    reason: str


@dataclass(frozen=True)
class OrderCheck:
    ok: bool
    reason: str
    classification: str  # "ok" | "rejected" | "unrecognized"


# ==================== Rule 5: market data validation ====================

def validate_market_data(
    df: Optional["pd.DataFrame"],
    bar_interval_min: int,
    min_bars: int = 50,
    max_stale_bars: float = 3.0,
    now: Optional[datetime] = None,
) -> DataCheck:
    """
    Reject missing / stale / contradictory / malformed market data.

    A DataCheck.ok == False here means NO TRADE (Rule 5). Every failure mode
    below is a reason a human eyeballing the tape would also refuse to trade.
    """
    # Missing
    if df is None:
        return DataCheck(False, "data is None")
    if not isinstance(df, pd.DataFrame):
        return DataCheck(False, f"data is not a DataFrame ({type(df).__name__})")
    if len(df) < min_bars:
        return DataCheck(False, f"insufficient bars: {len(df)} < {min_bars}")

    required = ("open", "high", "low", "close", "volume")
    missing_cols = [c for c in required if c not in df.columns]
    if missing_cols:
        return DataCheck(False, f"missing columns: {missing_cols}")

    # Malformed: NaN / inf in the most recent bars (the ones we act on)
    recent = df.tail(min_bars)[list(required)]
    if recent.isnull().values.any():
        return DataCheck(False, "NaN present in recent OHLCV")
    import numpy as np
    if not np.isfinite(recent.to_numpy(dtype="float64")).all():
        return DataCheck(False, "non-finite value present in recent OHLCV")

    last = df.iloc[-1]

    # Malformed: non-positive prices
    for col in ("open", "high", "low", "close"):
        if float(last[col]) <= 0:
            return DataCheck(False, f"non-positive {col}: {last[col]}")

    # Contradictory: OHLC relationships must hold on the acting bar
    hi, lo = float(last["high"]), float(last["low"])
    op, cl = float(last["open"]), float(last["close"])
    if hi < lo:
        return DataCheck(False, f"high < low ({hi} < {lo})")
    if not (lo <= op <= hi):
        return DataCheck(False, f"open {op} outside [low {lo}, high {hi}]")
    if not (lo <= cl <= hi):
        return DataCheck(False, f"close {cl} outside [low {lo}, high {hi}]")
    if float(last["volume"]) < 0:
        return DataCheck(False, f"negative volume: {last['volume']}")

    # Contradictory: timestamps must be strictly increasing
    if not df.index.is_monotonic_increasing:
        return DataCheck(False, "timestamps not monotonically increasing")

    # Stale: the most recent bar must be recent relative to the bar interval.
    last_ts = df.index[-1]
    if isinstance(last_ts, pd.Timestamp):
        last_dt = last_ts.to_pydatetime()
    else:
        try:
            last_dt = pd.Timestamp(last_ts).to_pydatetime()
        except Exception:
            return DataCheck(False, "unparseable last timestamp")

    now = now or datetime.now(timezone.utc)
    # Normalize tz so subtraction never throws.
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    age_min = (now - last_dt).total_seconds() / 60.0
    if age_min < 0:
        return DataCheck(False, f"last bar is in the future by {-age_min:.1f} min")
    max_age = max_stale_bars * max(1, bar_interval_min)
    if age_min > max_age:
        return DataCheck(False, f"stale data: last bar {age_min:.1f} min old (max {max_age:.0f})")

    return DataCheck(True, "ok")


# ==================== Rule 6: broker response validation ====================

def validate_order_response(order: Optional[dict]) -> OrderCheck:
    """
    Classify a broker order response.

    - "ok"           -> recognized, working order; safe to register the position.
    - "rejected"     -> recognized terminal failure; do NOT register, but the
                        broker behaved predictably (normal rejection).
    - "unrecognized" -> malformed or unknown-status response. Per Rule 6 this
                        must produce NO NEW ORDERS: caller trips the halt/kill.
    """
    if order is None:
        return OrderCheck(False, "order response is None", "unrecognized")
    if not isinstance(order, dict):
        return OrderCheck(False, f"order response not a dict ({type(order).__name__})", "unrecognized")

    order_id = order.get("id")
    if not order_id or not isinstance(order_id, str):
        return OrderCheck(False, "order response has no valid id", "unrecognized")

    status = order.get("status")
    if status is None or not isinstance(status, str) or status == "":
        return OrderCheck(False, "order response has no status", "unrecognized")

    status_l = status.lower()
    if status_l in ACCEPTED_ORDER_STATUSES:
        return OrderCheck(True, f"accepted ({status_l})", "ok")
    if status_l in TERMINAL_BAD_STATUSES:
        return OrderCheck(False, f"broker rejected order ({status_l})", "rejected")

    # Anything we don't recognize is treated as dangerous.
    return OrderCheck(False, f"unrecognized order status: {status!r}", "unrecognized")


# ==================== Rule 7: uncertainty -> size ====================

def compute_uncertainty_factor(
    strength: float,
    min_strength: float,
    confirmations: int,
    min_confirmations: int,
    rr_ratio: float,
    min_rr_ratio: float,
    ml_confidence: Optional[float] = None,
    ml_trained: bool = False,
) -> float:
    """
    Map signal quality to a size multiplier in [0, 1].

    Marginal signals (just over threshold, few confirmations, borderline R:R,
    low ML confidence) shrink the position. If the combined factor drops below
    MIN_SIZE_FACTOR the trade should be dropped (return 0.0). Rule 7.
    """
    factors = []

    # Strength headroom above the minimum (min -> 0.5x, min+35 -> 1.0x)
    if strength < min_strength:
        return 0.0
    factors.append(min(1.0, 0.5 + (strength - min_strength) / 70.0))

    # Confirmation headroom (exactly min -> 0.6x, min+2 -> 1.0x)
    if confirmations < min_confirmations:
        return 0.0
    factors.append(min(1.0, 0.6 + 0.2 * (confirmations - min_confirmations)))

    # Risk/reward headroom (at min -> 0.7x, 2x min -> 1.0x)
    if rr_ratio < min_rr_ratio:
        return 0.0
    if min_rr_ratio > 0:
        factors.append(min(1.0, 0.7 + 0.3 * (rr_ratio - min_rr_ratio) / min_rr_ratio))

    # ML confidence, only if the model is actually trained
    if ml_trained and ml_confidence is not None:
        factors.append(max(0.0, min(1.0, ml_confidence / 100.0)))

    factor = 1.0
    for f in factors:
        factor *= f

    if factor < MIN_SIZE_FACTOR:
        return 0.0
    return round(factor, 4)


# ==================== Rule 4: kill switch ====================

class KillSwitch:
    """
    A one-way kill switch backed by a file on disk.

    The bot can TRIP it (create the file) but has no method to RESET it — a
    human must remove the file out-of-band. This makes Rule 4 ("cannot disable
    kill switches") structural rather than a matter of trust.
    """

    def __init__(self, path: str = "data/KILL_SWITCH.lock"):
        self.path = path

    def is_tripped(self) -> bool:
        return os.path.exists(self.path)

    def reason(self) -> str:
        try:
            with open(self.path, "r") as f:
                return f.read().strip()
        except Exception:
            return ""

    def trip(self, reason: str) -> None:
        """Trip the switch. Idempotent; never overwrites the original reason."""
        if self.is_tripped():
            logger.critical("KILL SWITCH already engaged (%s); new cause: %s",
                            self.reason(), reason)
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            stamp = datetime.now(timezone.utc).isoformat()
            with open(self.path, "w") as f:
                f.write(f"{stamp} | {reason}\n")
            logger.critical("KILL SWITCH ENGAGED: %s", reason)
        except Exception as e:  # If we cannot even write the switch, fail loud.
            logger.critical("KILL SWITCH trip FAILED to persist (%s): %s", reason, e)

    @staticmethod
    def manual_reset_instructions(path: str = "data/KILL_SWITCH.lock") -> str:
        return f"Remove '{path}' by hand (human action) to resume trading."
