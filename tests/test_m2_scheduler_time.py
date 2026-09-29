"""Engine M2 — timezone-aware scheduling (hard rule 8: timestamp-touching
code ships with tests, DST-transition and half-day fixtures included).

Semantics under test: a naive scheduled_time is America/New_York wall time
(ambiguous fall-back times resolve to the FIRST occurrence, PEP 495 fold=0);
offset-aware strings are honored; all elapsed-time math happens in aware UTC,
so a DST transition can no longer double-fire or hour-shift a trade."""

from datetime import datetime, timezone

import pytest

from src.engine.scheduler import (
    ScheduledTradeStatus,
    TradeScheduler,
    parse_scheduled_time,
)

UTC = timezone.utc


class StubClient:
    """Offline broker stand-in for the scheduler's execution path."""

    def __init__(self, market_open=True):
        self.market_open = market_open
        self.orders = []

    def is_market_open(self):
        return self.market_open

    def place_market_order(self, **kw):
        self.orders.append(("market", kw))
        return {"id": "sched-ord-1", "status": "accepted"}

    def place_limit_order(self, **kw):
        self.orders.append(("limit", kw))
        return {"id": "sched-lim-1", "status": "accepted"}

    def place_stop_order(self, **kw):
        self.orders.append(("stop", kw))
        return {"id": "sched-stop-1", "status": "accepted"}

    def get_latest_quote(self, symbol):
        return {"bid": 10.0, "ask": 10.2}


def make_scheduler(monkeypatch, tmp_path, client=None) -> TradeScheduler:
    monkeypatch.chdir(tmp_path)
    return TradeScheduler(client if client is not None else StubClient())


def pend(s, when, **kw):
    return s.schedule_trade(symbol="F", side="buy", qty=1,
                            order_type="market", scheduled_time=when, **kw)


# ==================== parse_scheduled_time ====================

def test_naive_means_new_york_summer():
    assert parse_scheduled_time("2026-06-15T10:30:00") == \
        datetime(2026, 6, 15, 14, 30, tzinfo=UTC)          # EDT = UTC-4


def test_naive_means_new_york_winter():
    assert parse_scheduled_time("2026-01-15T10:30:00") == \
        datetime(2026, 1, 15, 15, 30, tzinfo=UTC)          # EST = UTC-5


def test_offset_aware_string_honored_as_given():
    assert parse_scheduled_time("2026-11-01T01:45:00-05:00") == \
        datetime(2026, 11, 1, 6, 45, tzinfo=UTC)


def test_fall_back_ambiguous_time_is_first_occurrence():
    # 2026-11-01: clocks fall back; 01:45 exists twice. fold=0 => EDT (first).
    assert parse_scheduled_time("2026-11-01T01:45:00") == \
        datetime(2026, 11, 1, 5, 45, tzinfo=UTC)


def test_spring_forward_nonexistent_time_is_deterministic():
    # 2026-03-08: 02:30 never happens on the wall clock. PEP 495 extrapolates
    # with the pre-transition offset (EST, UTC-5) => 07:30 UTC, deterministic.
    assert parse_scheduled_time("2026-03-08T02:30:00") == \
        datetime(2026, 3, 8, 7, 30, tzinfo=UTC)


def test_garbage_returns_none():
    assert parse_scheduled_time("tomorrow 4pm") is None
    assert parse_scheduled_time("") is None
    assert parse_scheduled_time(None) is None


# ==================== creation-time validation ====================

def test_schedule_trade_rejects_unparseable_time(monkeypatch, tmp_path):
    s = make_scheduler(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        pend(s, "tomorrow 4pm")
    assert s.scheduled_trades == []


def test_created_at_now_carries_utc_offset(monkeypatch, tmp_path):
    s = make_scheduler(monkeypatch, tmp_path)
    trade = pend(s, "2099-01-04T10:00:00")
    assert "-04:00" in trade.created_at or "-05:00" in trade.created_at


# ==================== execution windows across DST ====================

def test_fires_inside_the_30s_window(monkeypatch, tmp_path):
    client = StubClient()
    s = make_scheduler(monkeypatch, tmp_path, client)
    trade = pend(s, "2026-11-01T01:45:00")                 # = 05:45 UTC (EDT)
    s._check_and_execute(now=datetime(2026, 11, 1, 5, 45, 10, tzinfo=UTC))
    assert trade.status == ScheduledTradeStatus.EXECUTED
    assert client.orders and client.orders[0][0] == "market"
    assert "-0" in trade.executed_at                       # aware timestamp


def test_fall_back_repeat_of_wall_clock_does_not_fire(monkeypatch, tmp_path):
    """THE DST regression test. At 06:46 UTC the New York wall clock reads
    01:46 EST — one minute past the scheduled 01:45 *for the second time
    today*. Naive wall-clock math says 60 elapsed seconds and fires an
    hour-late duplicate; real elapsed time is 3661s => MISSED, no order."""
    client = StubClient()
    s = make_scheduler(monkeypatch, tmp_path, client)
    trade = pend(s, "2026-11-01T01:45:00")                 # first occurrence, EDT
    s._check_and_execute(now=datetime(2026, 11, 1, 6, 46, 1, tzinfo=UTC))
    assert client.orders == [], "fired an hour late across the DST fall-back"
    assert trade.status == ScheduledTradeStatus.MISSED


def test_missed_beyond_five_minutes(monkeypatch, tmp_path):
    client = StubClient()
    s = make_scheduler(monkeypatch, tmp_path, client)
    trade = pend(s, "2026-11-01T01:45:00")
    s._check_and_execute(now=datetime(2026, 11, 1, 5, 52, 0, tzinfo=UTC))
    assert trade.status == ScheduledTradeStatus.MISSED
    assert client.orders == []


def test_late_but_within_grace_window_fires(monkeypatch, tmp_path):
    client = StubClient()
    s = make_scheduler(monkeypatch, tmp_path, client)
    trade = pend(s, "2026-11-01T01:45:00")
    s._check_and_execute(now=datetime(2026, 11, 1, 5, 47, 0, tzinfo=UTC))
    assert trade.status == ScheduledTradeStatus.EXECUTED


# ==================== half-day / market-closed fixture ====================

def test_half_day_scheduled_after_early_close_is_missed(monkeypatch, tmp_path):
    """2026-11-27 is the half-day after Thanksgiving (13:00 ET close). A trade
    scheduled 14:00 ET reaches its window, but the market-open check refuses
    it: MISSED, no order. The broker clock, not the wall clock, decides."""
    client = StubClient(market_open=False)
    s = make_scheduler(monkeypatch, tmp_path, client)
    trade = pend(s, "2026-11-27T14:00:00")                 # = 19:00 UTC (EST)
    s._check_and_execute(now=datetime(2026, 11, 27, 19, 0, 5, tzinfo=UTC))
    assert trade.status == ScheduledTradeStatus.MISSED
    assert "closed" in trade.error_message.lower()
    assert client.orders == []


# ==================== unfireable pending trades fail loudly ====================

def test_unparseable_pending_trade_fails_to_history(monkeypatch, tmp_path):
    s = make_scheduler(monkeypatch, tmp_path)
    trade = pend(s, "2099-01-04T10:00:00")
    trade.scheduled_time = "garbage"                       # simulates old data
    s._check_and_execute(now=datetime(2099, 1, 4, 15, 0, tzinfo=UTC))
    assert trade.status == ScheduledTradeStatus.FAILED
    assert trade in s.history
    assert trade not in s.scheduled_trades
