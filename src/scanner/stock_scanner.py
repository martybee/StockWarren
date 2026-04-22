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
    reasons: list


class StockScanner:
    """Scan for stock trading opportunities"""

    def __init__(self, alpaca_client, config: dict):
        self.client = alpaca_client
        self.min_volume = config.get("min_volume", 500000)
        self.min_price = config.get("min_price", 5.0)
        self.max_price = config.get("max_price", 500.0)
        self.min_market_cap = config.get("min_market_cap", 500)
        self.top_results = config.get("top_results", 20)
        self.exclude_penny_stocks = config.get("exclude_penny_stocks", True)

    def scan_watchlist(self, symbols: list) -> list:
        """Scan a watchlist of symbols for opportunities"""
        results = []

        for symbol in symbols:
            try:
                result = self._analyze_symbol(symbol)
                if result and result.score > 30:
                    results.append(result)
            except Exception as e:
                logger.warning(f"Failed to scan {symbol}: {e}")

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:self.top_results]

    def scan_market(self, symbols: list = None) -> list:
        """Scan the broader market for opportunities"""
        if symbols is None:
            symbols = self._get_active_stocks()

        results = []
        for symbol in symbols:
            try:
                result = self._analyze_symbol(symbol)
                if result and result.score > 50:
                    results.append(result)
            except Exception as e:
                logger.debug(f"Skipping {symbol}: {e}")

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:self.top_results]

    def _analyze_symbol(self, symbol: str) -> Optional[ScanResult]:
        """Analyze a single symbol for trading opportunity"""
        try:
            snapshot = self.client.get_snapshot(symbol)
        except Exception:
            return None

        if not snapshot or not snapshot.get("daily_bar"):
            return None

        daily = snapshot["daily_bar"]
        prev = snapshot.get("prev_daily_bar", {})

        price = daily["close"]
        volume = daily["volume"]

        # Price filters
        if price < self.min_price or price > self.max_price:
            return None

        # Volume filter
        if volume < self.min_volume:
            return None

        # Calculate metrics
        change_pct = 0
        if prev and prev.get("close"):
            change_pct = ((price - prev["close"]) / prev["close"]) * 100

        # Get historical data for average volume
        try:
            bars = self.client.get_bars(symbol, timeframe="1Day", limit=20)
            avg_volume = bars["volume"].mean() if len(bars) > 0 else volume
        except Exception:
            avg_volume = volume

        volume_ratio = volume / avg_volume if avg_volume > 0 else 1.0

        # Score the opportunity
        score = 0.0
        reasons = []

        # Volume surge scoring
        if volume_ratio >= 3.0:
            score += 30
            reasons.append(f"Volume surge: {volume_ratio:.1f}x average")
        elif volume_ratio >= 2.0:
            score += 20
            reasons.append(f"High volume: {volume_ratio:.1f}x average")
        elif volume_ratio >= 1.5:
            score += 10
            reasons.append(f"Above avg volume: {volume_ratio:.1f}x")

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
