"""
Market calendar with timezone-aware US Equity hours

All times are in America/New_York. Handles:
- Standard hours: 9:30 AM - 4:00 PM ET
- Pre-market: 4:00 AM - 9:30 AM ET
- After-hours: 4:00 PM - 8:00 PM ET
- US holidays (uses Alpaca's clock as source of truth when available)
- Half-day closes (1:00 PM ET on certain holidays)

Trust Alpaca's clock for the live `is_open` decision.
Use this module for time math (e.g. "minutes until close").
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dt_time
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Regular session
REGULAR_OPEN = dt_time(9, 30)
REGULAR_CLOSE = dt_time(16, 0)

# Extended hours
PREMARKET_OPEN = dt_time(4, 0)
AFTERHOURS_CLOSE = dt_time(20, 0)


@dataclass
class MarketStatus:
    is_open: bool
    is_premarket: bool
    is_afterhours: bool
    current_time_et: datetime
    next_open: Optional[datetime]
    next_close: Optional[datetime]
    minutes_until_open: int
    minutes_until_close: int


def now_et() -> datetime:
    """Current time in US Eastern timezone"""
    return datetime.now(ET)


def to_et(dt: datetime) -> datetime:
    """Convert any datetime to ET"""
    if dt.tzinfo is None:
        # Assume UTC if naive
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(ET)


def is_weekend(dt: datetime = None) -> bool:
    if dt is None:
        dt = now_et()
    return dt.weekday() >= 5  # 5=Sat, 6=Sun


def is_regular_session(dt: datetime = None) -> bool:
    """True if currently in 9:30-16:00 ET on a weekday (no holiday check)"""
    if dt is None:
        dt = now_et()
    if is_weekend(dt):
        return False
    t = dt.time()
    return REGULAR_OPEN <= t < REGULAR_CLOSE


def is_premarket(dt: datetime = None) -> bool:
    if dt is None:
        dt = now_et()
    if is_weekend(dt):
        return False
    t = dt.time()
    return PREMARKET_OPEN <= t < REGULAR_OPEN


def is_afterhours(dt: datetime = None) -> bool:
    if dt is None:
        dt = now_et()
    if is_weekend(dt):
        return False
    t = dt.time()
    return REGULAR_CLOSE <= t < AFTERHOURS_CLOSE


def minutes_until(target: datetime, now: datetime = None) -> int:
    """Whole minutes from now until target (negative if past)"""
    if now is None:
        now = now_et()
    delta = target - now
    return int(delta.total_seconds() / 60)


def next_regular_open(dt: datetime = None) -> datetime:
    """Compute the next time the regular session opens (ignoring holidays)"""
    if dt is None:
        dt = now_et()

    # Today's open
    candidate = dt.replace(
        hour=REGULAR_OPEN.hour,
        minute=REGULAR_OPEN.minute,
        second=0,
        microsecond=0,
    )

    # If we're past today's open, move to next day
    if dt >= candidate:
        candidate += timedelta(days=1)

    # Skip weekends
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)

    return candidate


def next_regular_close(dt: datetime = None) -> datetime:
    """Compute the next time the regular session closes (ignoring holidays)"""
    if dt is None:
        dt = now_et()

    candidate = dt.replace(
        hour=REGULAR_CLOSE.hour,
        minute=REGULAR_CLOSE.minute,
        second=0,
        microsecond=0,
    )

    # If we're past today's close, find next trading day's close
    if dt >= candidate or is_weekend(dt):
        candidate += timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)

    return candidate


def get_status(alpaca_client=None) -> MarketStatus:
    """
    Get current market status, preferring Alpaca's authoritative clock
    when a client is provided.
    """
    now = now_et()

    if alpaca_client is not None:
        try:
            clock_info = alpaca_client.get_market_hours()
            is_open = clock_info.get("is_open", False)

            next_open_str = clock_info.get("next_open")
            next_close_str = clock_info.get("next_close")

            next_open = (
                datetime.fromisoformat(next_open_str).astimezone(ET)
                if next_open_str else next_regular_open(now)
            )
            next_close = (
                datetime.fromisoformat(next_close_str).astimezone(ET)
                if next_close_str else next_regular_close(now)
            )

            return MarketStatus(
                is_open=is_open,
                is_premarket=is_premarket(now) and not is_open,
                is_afterhours=is_afterhours(now) and not is_open,
                current_time_et=now,
                next_open=next_open,
                next_close=next_close,
                minutes_until_open=minutes_until(next_open, now),
                minutes_until_close=minutes_until(next_close, now),
            )
        except Exception as e:
            logger.warning(f"Failed to get Alpaca clock, falling back to local: {e}")

    # Local fallback (no holiday check)
    is_open = is_regular_session(now)
    return MarketStatus(
        is_open=is_open,
        is_premarket=is_premarket(now),
        is_afterhours=is_afterhours(now),
        current_time_et=now,
        next_open=next_regular_open(now),
        next_close=next_regular_close(now),
        minutes_until_open=minutes_until(next_regular_open(now), now),
        minutes_until_close=minutes_until(next_regular_close(now), now),
    )
