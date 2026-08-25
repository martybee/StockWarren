"""Rule 5 — missing, stale, contradictory, or malformed market data must
produce NO TRADE. safety.validate_market_data() gates every evaluation."""

import numpy as np
import pandas as pd

from src.engine.safety import validate_market_data
from conftest import MockAlpaca, fresh_rm, good_bars, make_bot


def check(df, **kw):
    args = dict(bar_interval_min=5)
    args.update(kw)
    return validate_market_data(df, **args)


# ---------- Missing ----------

def test_none_is_no_trade():
    assert check(None).ok is False


def test_wrong_type_is_no_trade():
    assert check([1, 2, 3]).ok is False


def test_insufficient_bars_is_no_trade():
    assert check(good_bars(n=10)).ok is False


def test_missing_column_is_no_trade(bars):
    assert check(bars.drop(columns=["volume"])).ok is False


# ---------- Malformed ----------

def test_nan_in_recent_bars_is_no_trade(bars):
    bars.iloc[-1, bars.columns.get_loc("close")] = np.nan
    assert check(bars).ok is False


def test_infinite_value_is_no_trade(bars):
    bars.iloc[-3, bars.columns.get_loc("high")] = np.inf
    assert check(bars).ok is False


def test_non_positive_price_is_no_trade(bars):
    bars.iloc[-1, bars.columns.get_loc("low")] = 0.0
    assert check(bars).ok is False


def test_negative_volume_is_no_trade(bars):
    bars.iloc[-1, bars.columns.get_loc("volume")] = -100.0
    assert check(bars).ok is False


# ---------- Contradictory ----------

def test_high_below_low_is_no_trade(bars):
    bars.iloc[-1, bars.columns.get_loc("high")] = 9.0
    bars.iloc[-1, bars.columns.get_loc("low")] = 11.0
    bars.iloc[-1, bars.columns.get_loc("open")] = 10.0
    bars.iloc[-1, bars.columns.get_loc("close")] = 10.0
    assert check(bars).ok is False


def test_open_outside_range_is_no_trade(bars):
    bars.iloc[-1, bars.columns.get_loc("open")] = 99.0
    assert check(bars).ok is False


def test_close_outside_range_is_no_trade(bars):
    bars.iloc[-1, bars.columns.get_loc("close")] = 0.5
    assert check(bars).ok is False


def test_non_monotonic_timestamps_is_no_trade(bars):
    idx = list(bars.index)
    idx[-1], idx[-2] = idx[-2], idx[-1]
    bars.index = pd.DatetimeIndex(idx)
    assert check(bars).ok is False


# ---------- Stale / future ----------

def test_stale_data_is_no_trade():
    stale_end = pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=100)
    assert check(good_bars(end=stale_end)).ok is False


def test_future_bar_is_no_trade():
    future_end = pd.Timestamp.now(tz="UTC") + pd.Timedelta(hours=2)
    assert check(good_bars(end=future_end)).ok is False


# ---------- Sanity + wiring ----------

def test_good_data_passes(bars):
    result = check(bars)
    assert result.ok, result.reason


def test_bot_places_no_order_on_bad_data(tmp_path):
    alpaca = MockAlpaca(bars=good_bars(n=10))   # insufficient bars
    bot = make_bot(tmp_path, alpaca, fresh_rm())

    bot._evaluate_symbol("TEST", 9000.0, 9000.0)

    assert not alpaca.called("place_limit_order")


def test_bot_places_no_order_on_missing_data(tmp_path):
    alpaca = MockAlpaca(bars=None)
    bot = make_bot(tmp_path, alpaca, fresh_rm())

    bot._evaluate_symbol("TEST", 9000.0, 9000.0)

    assert not alpaca.called("place_limit_order")
