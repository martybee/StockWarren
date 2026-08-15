"""
Stock Scanner for StockWarren
Scans market for trading opportunities based on configurable criteria
"""

import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Result of a stock scan"""
    symbol: str
    score: float            # 0-100 opportunity score
    signal_direction: int   # 1 buy, -1 sell
    volume: int
    price: float
    change_pct: float
    avg_volume: int
    volume_ratio: float
    volume_basis: str       # how volume_ratio was derived — see _volume_context()
    reasons: list


class StockScanner:
    """Scan for stock trading opportunities.

    NOTE ON SCOPE: this is a research tool, not part of the trading path. The bot
    never calls it — TradingBot iterates its watchlist directly and evaluates each
    symbol with the indicators + ML filter. Nothing here can add, remove or
    reprioritise what gets traded; it only populates the dashboard's Scan Results.
    """

    def __init__(self, alpaca_client, config: dict):
        self.client = alpaca_client
        self.min_price = config.get("min_price", 1.0)
        self.max_price = config.get("max_price", 1000.0)
        # Liquidity is judged on the 20-day AVERAGE, never on today's bar: today's
        # bar is partial for most of the day, so gating on it silently hid liquid
        # names (HOOD and AMD were both dropped pre-market for "low volume" while
        # averaging tens of millions of shares a day).
        self.min_avg_volume = config.get("min_avg_volume", 500000)
        self.min_score = config.get("min_score", 30)
        self.top_results = config.get("top_results", 20)
        self.avg_volume_days = 20     # baseline length for the volume average
        # Calendar days to request to be sure of covering avg_volume_days of
        # TRADING days, with room for weekends and holidays.
        self.history_days = 45

    def scan_watchlist(self, symbols: list) -> list:
        """Scan a watchlist of symbols for opportunities"""
        ctx = self._volume_context()
        results = []

        for symbol in symbols:
            try:
                result = self._analyze_symbol(symbol, ctx)
                if result and result.score >= self.min_score:
                    results.append(result)
            except Exception as e:
                logger.warning(f"Failed to scan {symbol}: {e}")

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:self.top_results]

    def scan_market(self, symbols: list = None) -> list:
        """Scan the broader market for opportunities"""
        if symbols is None:
            symbols = self._get_active_stocks()

        ctx = self._volume_context()
        results = []
        for symbol in symbols:
            try:
                result = self._analyze_symbol(symbol, ctx)
                # A market-wide sweep is noisier than a curated watchlist, so it
                # sits one tier above the configured floor.
                if result and result.score >= max(self.min_score, 50):
                    results.append(result)
            except Exception as e:
                logger.debug(f"Skipping {symbol}: {e}")

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:self.top_results]

    def _volume_context(self) -> dict:
        """Decide how today's volume can fairly be compared to a 20-day average.

        Computed ONCE per scan (one clock call, not one per symbol).

        The naive comparison — today's daily bar over a 20-day average — is only
        valid when today's bar is complete. For most of the trading day it is not,
        and pre-market it is a different animal entirely. Left uncorrected the
        ratio sat at 0.03-0.08 all morning, which meant the volume-surge component
        (30 of the 100 available points, the largest single bucket) could never
        fire while the market was actually open.

        Three regimes:
          open        -> today's bar is partial; project it to a full day using
                         the fraction of the session elapsed ("pace")
          pre-market  -> today's bar holds only pre-market prints, which do not
                         scale to a session; compare the PREVIOUS complete
                         session instead
          otherwise   -> after-hours, weekend or holiday: today's bar is complete,
                         so compare it as-is
        """
        try:
            from src.utils import market_calendar as mcal
            st = mcal.get_status(self.client)
        except Exception as e:
            logger.warning("Scanner could not read market status (%s); "
                           "treating today's bar as complete.", e)
            return {"mode": "complete", "elapsed": 1.0, "today": datetime.now().date()}

        if st.is_open and st.next_close:
            now = st.current_time_et
            open_dt = now.replace(hour=mcal.REGULAR_OPEN.hour,
                                  minute=mcal.REGULAR_OPEN.minute,
                                  second=0, microsecond=0)
            total = (st.next_close - open_dt).total_seconds()
            elapsed = (now - open_dt).total_seconds()
            # Floor at 2%: in the first ~8 minutes the projection is wild, and
            # dividing by a near-zero fraction would manufacture huge ratios.
            frac = min(1.0, max(0.02, elapsed / total)) if total > 0 else 1.0
            return {"mode": "pace", "elapsed": frac, "today": now.date()}

        return {"mode": "complete", "elapsed": 1.0,
                "today": st.current_time_et.date()}

    def _daily_bars(self, symbol: str):
        """The last ~21 completed daily bars, newest last.

        Two traps in the underlying API, both of which silently produced garbage:

        1. AlpacaClient.get_bars() defaults `start` to 5 days ago regardless of
           `limit`, so asking for 20 daily bars returned 2-3. The "20-day average
           volume" was really a 2-day average.
        2. `limit` truncates from the START of the window, not the end, so simply
           widening `start` returns OLDER bars. The window has to be wide and the
           limit generous, then take the tail.
        """
        start = datetime.now() - timedelta(days=self.history_days)
        bars = self.client.get_bars(symbol, timeframe="1Day", start=start, limit=200)
        if bars is None or len(bars) < 2:
            return None
        return bars.tail(self.avg_volume_days + 1)

    def _analyze_symbol(self, symbol: str, ctx: dict = None) -> Optional[ScanResult]:
        """Analyze a single symbol for trading opportunity"""
        ctx = ctx or self._volume_context()

        # Volume and OHLC come from the DAILY BAR SERIES, never from the snapshot.
        # The snapshot is an IEX-only feed on this plan: it reported ~1.8M shares
        # for a name that trades ~54M consolidated. Dividing snapshot volume by a
        # get_bars average compared two different feeds, which pinned every ratio
        # near 0.05 no matter the time of day or how heavily the stock traded.
        try:
            bars = self._daily_bars(symbol)
        except Exception as e:
            logger.debug("No daily bars for %s: %s", symbol, e)
            return None
        if bars is None:
            return None

        ref = bars.iloc[-1]           # most recent completed (or in-progress) session
        prev_close = float(bars["close"].iloc[-2])
        history = bars.iloc[:-1]      # the baseline, excluding the reference bar
        avg_volume = float(history["volume"].mean())

        # Liquidity gate on the AVERAGE, so it doesn't swing with time of day.
        if avg_volume < self.min_avg_volume:
            return None

        # Live price if we can get one — that part of the snapshot is fine, a
        # traded price is a traded price. Fall back to the bar close.
        price = float(ref["close"])
        try:
            snap = self.client.get_snapshot(symbol)
            if snap and snap.get("latest_trade_price"):
                price = float(snap["latest_trade_price"])
        except Exception:
            pass

        if price < self.min_price or price > self.max_price:
            return None

        change_pct = ((price - prev_close) / prev_close) * 100 if prev_close else 0.0
        volume = float(ref["volume"])

        # Is the reference bar today's, still forming? Only then does pacing apply.
        ref_is_today = False
        try:
            ref_is_today = bars.index[-1].date() == ctx.get("today")
        except Exception:
            pass

        if ref_is_today and ctx.get("mode") == "pace":
            basis = "pace"
            comparable_volume = volume / ctx.get("elapsed", 1.0)
        elif ref_is_today:
            basis = "full day"      # today, but the session has finished
            comparable_volume = volume
        else:
            basis = "last session"  # pre-market/weekend/holiday: yesterday's close
            comparable_volume = volume
        volume_ratio = comparable_volume / avg_volume if avg_volume > 0 else 1.0

        daily = {"open": float(ref["open"]), "high": float(ref["high"]),
                 "low": float(ref["low"]), "close": float(ref["close"])}
        prev = {"close": prev_close}

        # Score the opportunity
        score = 0.0
        reasons = []

        # Volume surge scoring. The label names the basis so a "3x" read early in
        # the session is not mistaken for 3x of a completed day.
        vol_note = {"pace": " (projected)", "last session": " (last session)"}.get(basis, "")
        if volume_ratio >= 3.0:
            score += 30
            reasons.append(f"Volume surge: {volume_ratio:.1f}x average{vol_note}")
        elif volume_ratio >= 2.0:
            score += 20
            reasons.append(f"High volume: {volume_ratio:.1f}x average{vol_note}")
        elif volume_ratio >= 1.5:
            score += 10
            reasons.append(f"Above avg volume: {volume_ratio:.1f}x{vol_note}")

        # Price movement scoring
        abs_change = abs(change_pct)
        if abs_change >= 5.0:
            score += 25
            reasons.append(f"Large move: {change_pct:+.1f}%")
        elif abs_change >= 3.0:
            score += 15
            reasons.append(f"Significant move: {change_pct:+.1f}%")
        elif abs_change >= 1.5:
            score += 10
            reasons.append(f"Moderate move: {change_pct:+.1f}%")

        # Daily range scoring (intraday volatility)
        daily_range = ((daily["high"] - daily["low"]) / price) * 100
        if daily_range >= 4.0:
            score += 15
            reasons.append(f"Wide range: {daily_range:.1f}%")
        elif daily_range >= 2.0:
            score += 10
            reasons.append(f"Good range: {daily_range:.1f}%")

        # Volume + price agreement
        if volume_ratio >= 2.0 and abs_change >= 2.0:
            score += 15
            reasons.append("Volume confirms price move")

        # Gap detection
        if prev and prev.get("close"):
            gap_pct = ((daily["open"] - prev["close"]) / prev["close"]) * 100
            if abs(gap_pct) >= 2.0:
                score += 10
                reasons.append(f"Gap: {gap_pct:+.1f}%")

        # Direction
        direction = 1 if change_pct > 0 else -1 if change_pct < 0 else 0

        return ScanResult(
            symbol=symbol,
            score=min(100, score),
            signal_direction=direction,
            volume=volume,
            price=price,
            change_pct=change_pct,
            avg_volume=int(avg_volume),
            volume_ratio=volume_ratio,
            volume_basis=basis,
            reasons=reasons,
        )

    def _get_active_stocks(self) -> list:
        """Get a list of active, tradeable stocks"""
        try:
            assets = self.client.trading_client.get_all_assets()
            symbols = [
                a.symbol for a in assets
                if a.tradable and a.status == "active"
                and a.exchange in ("NYSE", "NASDAQ")
                and not a.symbol.endswith("W")  # exclude warrants
                and "." not in a.symbol          # exclude preferred shares
            ]
            return symbols[:500]  # Limit to prevent API rate limiting
        except Exception as e:
            logger.error(f"Failed to get active stocks: {e}")
            return []


class WatchlistManager:
    """Manage stock watchlists"""

    def __init__(self, config_symbols: str = ""):
        self.symbols = []
        if config_symbols:
            self.symbols = [s.strip().upper() for s in config_symbols.split(",") if s.strip()]

    def add(self, symbol: str):
        symbol = symbol.upper().strip()
        if symbol not in self.symbols:
            self.symbols.append(symbol)

    def remove(self, symbol: str):
        symbol = symbol.upper().strip()
        if symbol in self.symbols:
            self.symbols.remove(symbol)

    def get_symbols(self) -> list:
        return list(self.symbols)

    def set_symbols(self, symbols: list):
        self.symbols = [s.upper().strip() for s in symbols]
