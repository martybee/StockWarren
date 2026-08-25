"""Shared fixtures for the engine M1 safety-invariant suite.

Everything here runs OFFLINE: the broker is a recording fake, market data is
synthesized, and the kill-switch file lives in pytest's tmp_path. No test may
open a network connection — that is itself part of the M1 contract.

The TradingBot instances used in bot-level tests are built with
``object.__new__`` and hand-wired attributes instead of running ``__init__``
(which constructs a real AlpacaClient and reads config/settings.ini). This
tests the engine code exactly as it stands, per the M1 guardrail: no
refactoring-for-testability in the same change as the tests.
"""

import sys
import pathlib
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.engine.risk_manager import RiskManager  # noqa: E402
from src.engine.safety import KillSwitch  # noqa: E402
from src.engine.trading_bot import TradingBot  # noqa: E402

# Mirrors the real $9k-account configuration (settings.ini / CLAUDE.md).
BASE_CFG = {
    "max_daily_loss": 200.0,
    "max_daily_loss_pct": 1.5,
    "max_weekly_loss_pct": 3.0,
    "max_consecutive_losses": 3,
    "max_positions": 2,
    "max_position_pct": 40.0,
    "min_cash_reserve_pct": 10.0,
    "risk_per_trade_pct": 0.25,
    "max_risk_per_trade_pct": 0.5,
    "max_combined_open_risk_pct": 1.0,
    "review_drawdown_pct": 5.0,
    "shutdown_drawdown_pct": 8.0,
    "min_risk_reward_ratio": 2.0,
}

EQUITY = 9000.0


def fresh_rm(correlation_groups=None, **overrides) -> RiskManager:
    cfg = dict(BASE_CFG)
    cfg.update(overrides)
    return RiskManager(cfg, correlation_groups=correlation_groups)


def good_bars(n=200, interval_min=5, price=10.0, end=None) -> pd.DataFrame:
    """A DataFrame that passes validate_market_data: fresh, monotonic, sane OHLCV."""
    if end is None:
        end = pd.Timestamp.now(tz="UTC").floor("min")
    idx = pd.date_range(end=end, periods=n, freq=f"{interval_min}min")
    close = np.full(n, float(price))
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(n, 10_000.0),
        },
        index=idx,
    )


class MockAlpaca:
    """Recording fake of broker.client.AlpacaClient. Never touches the network."""

    paper = True

    def __init__(self, bars=None):
        self.bars = bars
        self.calls = []
        self.limit_order_response = {"id": "entry-1", "status": "new"}
        self.stop_order_response = {"id": "stop-1", "status": "accepted"}
        self.account = {"portfolio_value": EQUITY, "cash": EQUITY}
        self.positions = []

    def called(self, name):
        return [c for c in self.calls if c[0] == name]

    def get_account(self):
        self.calls.append(("get_account",))
        return dict(self.account)

    def is_market_open(self):
        self.calls.append(("is_market_open",))
        return True

    def get_bars(self, symbol, timeframe="5Min", limit=200):
        self.calls.append(("get_bars", symbol))
        return self.bars

    def place_limit_order(self, **kw):
        self.calls.append(("place_limit_order", kw))
        return self.limit_order_response

    def place_stop_order(self, **kw):
        self.calls.append(("place_stop_order", kw))
        return self.stop_order_response

    def cancel_order(self, order_id):
        self.calls.append(("cancel_order", order_id))

    def close_position(self, symbol):
        self.calls.append(("close_position", symbol))

    def get_positions(self):
        self.calls.append(("get_positions",))
        return list(self.positions)


class StubIndicators:
    """Deterministic signal source: strong enough to clear every threshold."""

    def __init__(self, strength=90.0, confirmations=4, direction=1):
        self.strength = strength
        self.confirmations = confirmations
        self.direction = direction

    def analyze(self, df):
        return SimpleNamespace(
            strength=self.strength,
            confirmations=self.confirmations,
            direction=self.direction,
        )

    def get_atr_stop_price(self, df, is_long):
        return None  # fall through to the default %-based stop


def make_bot(tmp_path, alpaca, rm, symbols=("TEST",)) -> TradingBot:
    """A TradingBot wired by hand (no __init__): real engine code, fake edges."""
    bot = object.__new__(TradingBot)
    bot.account_id = "test"
    bot.name = "test"
    bot.alpaca = alpaca
    bot.indicators = StubIndicators()
    bot.ml_enabled = False          # `and` short-circuits: ml_validator never touched
    bot.ml_validator = None
    bot.risk_manager = rm
    bot.kill_switch = KillSwitch(path=str(tmp_path / "KILL_SWITCH_test.lock"))
    bot._halt_new_orders = False
    bot.last_tick_at = None
    bot.trade_log = []
    bot.min_signal_strength = 65
    bot.min_confirmations = 2
    bot.bar_interval = "5"
    bot.bar_interval_min = 5
    bot.market_hours_only = False
    bot.watchlist = SimpleNamespace(get_symbols=lambda: list(symbols))
    bot.running = False
    # Instance-level overrides: keep the CSV writer and notifier out of tests.
    bot._log_trade = lambda trade: bot.trade_log.append(trade)
    bot._notify_trade = lambda *a, **k: None
    return bot


@pytest.fixture
def rm():
    return fresh_rm()


@pytest.fixture
def bars():
    return good_bars()
