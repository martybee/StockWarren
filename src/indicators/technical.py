"""
Technical Indicator Engine for StockWarren
Calculates RSI, MACD, VWAP, Bollinger Bands, EMA crossovers, Volume analysis, and ATR
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional


@dataclass
class IndicatorSignal:
    """Result of a single indicator analysis"""
    name: str
    value: float           # Raw indicator value
    signal: int            # -1 (sell), 0 (neutral), 1 (buy)
    strength: float        # 0-100 confidence
    description: str       # Human-readable explanation


@dataclass
class CompositeSignal:
    """Combined signal from all indicators"""
    direction: int         # -1 (sell), 0 (neutral), 1 (buy)
    strength: float        # 0-100 weighted score
    confirmations: int     # Number of agreeing indicators
    signals: list          # List of IndicatorSignal
    timestamp: pd.Timestamp


class TechnicalIndicators:
    """Calculate and analyze technical indicators for trading signals"""

    def __init__(self, config: dict):
        # RSI settings
        self.rsi_period = config.get("rsi_period", 14)
        self.rsi_overbought = config.get("rsi_overbought", 70)
        self.rsi_oversold = config.get("rsi_oversold", 30)

        # MACD settings
        self.macd_fast = config.get("macd_fast", 12)
        self.macd_slow = config.get("macd_slow", 26)
        self.macd_signal = config.get("macd_signal", 9)

        # Bollinger Bands
        self.bb_period = config.get("bb_period", 20)
        self.bb_std_dev = config.get("bb_std_dev", 2.0)

        # EMA settings
        self.ema_fast = config.get("ema_fast", 9)
        self.ema_slow = config.get("ema_slow", 21)
        self.ema_trend = config.get("ema_trend", 50)

        # Volume settings
        self.volume_sma_period = config.get("volume_sma_period", 20)
        self.volume_surge_multiplier = config.get("volume_surge_multiplier", 2.0)

        # ATR settings
        self.atr_period = config.get("atr_period", 14)
        self.atr_stop_multiplier = config.get("atr_stop_multiplier", 2.0)

        # Signal weights
        self.weights = {
            "rsi": config.get("weight_rsi", 15),
            "macd": config.get("weight_macd", 20),
            "vwap": config.get("weight_vwap", 15),
            "bollinger": config.get("weight_bollinger", 10),
            "ema_crossover": config.get("weight_ema_crossover", 15),
            "volume": config.get("weight_volume", 15),
            "atr": config.get("weight_atr", 10),
        }

    def calculate_rsi(self, df: pd.DataFrame) -> IndicatorSignal:
        """Calculate RSI and generate signal"""
        close = df["close"]
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)

        avg_gain = gain.ewm(span=self.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(span=self.rsi_period, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]

        if pd.isna(current_rsi):
            return IndicatorSignal("RSI", 50.0, 0, 0.0, "Insufficient data")

        if current_rsi <= self.rsi_oversold:
            distance = self.rsi_oversold - current_rsi
            strength = min(100, 50 + (distance / self.rsi_oversold) * 50)
            return IndicatorSignal("RSI", current_rsi, 1, strength,
                                   f"RSI oversold at {current_rsi:.1f}")
        elif current_rsi >= self.rsi_overbought:
            distance = current_rsi - self.rsi_overbought
            strength = min(100, 50 + (distance / (100 - self.rsi_overbought)) * 50)
            return IndicatorSignal("RSI", current_rsi, -1, strength,
                                   f"RSI overbought at {current_rsi:.1f}")
        else:
            return IndicatorSignal("RSI", current_rsi, 0, 0.0,
                                   f"RSI neutral at {current_rsi:.1f}")

    def calculate_macd(self, df: pd.DataFrame) -> IndicatorSignal:
        """Calculate MACD and generate signal"""
        close = df["close"]
        ema_fast = close.ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.macd_slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.macd_signal, adjust=False).mean()
        histogram = macd_line - signal_line

        current_hist = histogram.iloc[-1]
        prev_hist = histogram.iloc[-2] if len(histogram) > 1 else 0

        if pd.isna(current_hist):
            return IndicatorSignal("MACD", 0.0, 0, 0.0, "Insufficient data")

        # Crossover detection
        if current_hist > 0 and prev_hist <= 0:
            strength = min(100, abs(current_hist) / (close.iloc[-1] * 0.001) * 50 + 50)
            return IndicatorSignal("MACD", current_hist, 1, strength,
                                   "MACD bullish crossover")
        elif current_hist < 0 and prev_hist >= 0:
            strength = min(100, abs(current_hist) / (close.iloc[-1] * 0.001) * 50 + 50)
            return IndicatorSignal("MACD", current_hist, -1, strength,
                                   "MACD bearish crossover")
        elif current_hist > 0:
            momentum = (current_hist - prev_hist) / max(abs(prev_hist), 0.001)
            strength = min(100, max(0, momentum * 50 + 25))
            return IndicatorSignal("MACD", current_hist, 1, strength,
                                   "MACD bullish momentum")
        elif current_hist < 0:
            momentum = (prev_hist - current_hist) / max(abs(prev_hist), 0.001)
            strength = min(100, max(0, momentum * 50 + 25))
            return IndicatorSignal("MACD", current_hist, -1, strength,
                                   "MACD bearish momentum")
        else:
            return IndicatorSignal("MACD", current_hist, 0, 0.0, "MACD neutral")

    def calculate_vwap(self, df: pd.DataFrame) -> IndicatorSignal:
        """Calculate VWAP and generate signal"""
        if "volume" not in df.columns:
            return IndicatorSignal("VWAP", 0.0, 0, 0.0, "No volume data")

        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        cumulative_tp_vol = (typical_price * df["volume"]).cumsum()
        cumulative_vol = df["volume"].cumsum()
        vwap = cumulative_tp_vol / cumulative_vol.replace(0, np.nan)

        current_price = df["close"].iloc[-1]
        current_vwap = vwap.iloc[-1]

        if pd.isna(current_vwap):
            return IndicatorSignal("VWAP", 0.0, 0, 0.0, "Insufficient data")

        deviation_pct = ((current_price - current_vwap) / current_vwap) * 100

        if deviation_pct < -0.5:
            strength = min(100, abs(deviation_pct) * 30)
            return IndicatorSignal("VWAP", current_vwap, 1, strength,
                                   f"Price {abs(deviation_pct):.2f}% below VWAP (buy)")
        elif deviation_pct > 0.5:
            strength = min(100, abs(deviation_pct) * 30)
            return IndicatorSignal("VWAP", current_vwap, -1, strength,
                                   f"Price {deviation_pct:.2f}% above VWAP (sell)")
        else:
            return IndicatorSignal("VWAP", current_vwap, 0, 0.0,
                                   f"Price near VWAP ({deviation_pct:+.2f}%)")

    def calculate_bollinger_bands(self, df: pd.DataFrame) -> IndicatorSignal:
        """Calculate Bollinger Bands and generate signal"""
        close = df["close"]
        sma = close.rolling(window=self.bb_period).mean()
        std = close.rolling(window=self.bb_period).std()

        upper_band = sma + (std * self.bb_std_dev)
        lower_band = sma - (std * self.bb_std_dev)

        current_price = close.iloc[-1]
        current_upper = upper_band.iloc[-1]
        current_lower = lower_band.iloc[-1]
        current_sma = sma.iloc[-1]

        if pd.isna(current_upper):
            return IndicatorSignal("Bollinger", 0.0, 0, 0.0, "Insufficient data")

        band_width = current_upper - current_lower
        position = (current_price - current_lower) / band_width if band_width > 0 else 0.5

        if current_price <= current_lower:
            strength = min(100, (1 - position) * 100)
            return IndicatorSignal("Bollinger", position, 1, strength,
                                   "Price at/below lower Bollinger Band")
        elif current_price >= current_upper:
            strength = min(100, position * 100)
            return IndicatorSignal("Bollinger", position, -1, strength,
                                   "Price at/above upper Bollinger Band")
        elif position < 0.3:
            strength = (0.3 - position) / 0.3 * 60
            return IndicatorSignal("Bollinger", position, 1, strength,
                                   "Price near lower Bollinger Band")
        elif position > 0.7:
            strength = (position - 0.7) / 0.3 * 60
            return IndicatorSignal("Bollinger", position, -1, strength,
                                   "Price near upper Bollinger Band")
        else:
            return IndicatorSignal("Bollinger", position, 0, 0.0,
                                   "Price within Bollinger Bands")

    def calculate_ema_crossover(self, df: pd.DataFrame) -> IndicatorSignal:
        """Calculate EMA crossovers and generate signal"""
        close = df["close"]
        ema_fast = close.ewm(span=self.ema_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.ema_slow, adjust=False).mean()
        ema_trend = close.ewm(span=self.ema_trend, adjust=False).mean()

        fast_current = ema_fast.iloc[-1]
        slow_current = ema_slow.iloc[-1]
        trend_current = ema_trend.iloc[-1]
        fast_prev = ema_fast.iloc[-2] if len(ema_fast) > 1 else fast_current
        slow_prev = ema_slow.iloc[-2] if len(ema_slow) > 1 else slow_current

        if pd.isna(fast_current) or pd.isna(trend_current):
            return IndicatorSignal("EMA Cross", 0.0, 0, 0.0, "Insufficient data")

        # Golden cross (fast crosses above slow)
        if fast_current > slow_current and fast_prev <= slow_prev:
            above_trend = close.iloc[-1] > trend_current
            strength = 80 if above_trend else 60
            return IndicatorSignal("EMA Cross", fast_current - slow_current, 1, strength,
                                   f"EMA{self.ema_fast} crossed above EMA{self.ema_slow}")

        # Death cross (fast crosses below slow)
        elif fast_current < slow_current and fast_prev >= slow_prev:
            below_trend = close.iloc[-1] < trend_current
            strength = 80 if below_trend else 60
            return IndicatorSignal("EMA Cross", fast_current - slow_current, -1, strength,
                                   f"EMA{self.ema_fast} crossed below EMA{self.ema_slow}")

        # Trend following
        elif fast_current > slow_current and close.iloc[-1] > trend_current:
            spread_pct = (fast_current - slow_current) / close.iloc[-1] * 100
            strength = min(50, spread_pct * 100)
            return IndicatorSignal("EMA Cross", fast_current - slow_current, 1, strength,
                                   "Bullish EMA alignment")

        elif fast_current < slow_current and close.iloc[-1] < trend_current:
            spread_pct = (slow_current - fast_current) / close.iloc[-1] * 100
            strength = min(50, spread_pct * 100)
            return IndicatorSignal("EMA Cross", fast_current - slow_current, -1, strength,
                                   "Bearish EMA alignment")
        else:
            return IndicatorSignal("EMA Cross", fast_current - slow_current, 0, 0.0,
                                   "EMA signals mixed")

    def calculate_volume_analysis(self, df: pd.DataFrame) -> IndicatorSignal:
        """Analyze volume patterns"""
        if "volume" not in df.columns:
            return IndicatorSignal("Volume", 0.0, 0, 0.0, "No volume data")

        volume = df["volume"]
        close = df["close"]
        vol_sma = volume.rolling(window=self.volume_sma_period).mean()

        current_vol = volume.iloc[-1]
        avg_vol = vol_sma.iloc[-1]
        price_change = close.iloc[-1] - close.iloc[-2] if len(close) > 1 else 0

        if pd.isna(avg_vol) or avg_vol == 0:
            return IndicatorSignal("Volume", 0.0, 0, 0.0, "Insufficient data")

        vol_ratio = current_vol / avg_vol

        # Volume surge with price movement
        if vol_ratio >= self.volume_surge_multiplier:
            if price_change > 0:
                strength = min(100, vol_ratio * 25)
                return IndicatorSignal("Volume", vol_ratio, 1, strength,
                                       f"Volume surge ({vol_ratio:.1f}x) with price up")
            elif price_change < 0:
                strength = min(100, vol_ratio * 25)
                return IndicatorSignal("Volume", vol_ratio, -1, strength,
                                       f"Volume surge ({vol_ratio:.1f}x) with price down")
            else:
                return IndicatorSignal("Volume", vol_ratio, 0, 30.0,
                                       f"Volume surge ({vol_ratio:.1f}x) no price change")

        # Below average volume
        elif vol_ratio < 0.5:
            return IndicatorSignal("Volume", vol_ratio, 0, 0.0,
                                   f"Low volume ({vol_ratio:.1f}x average)")
        else:
            return IndicatorSignal("Volume", vol_ratio, 0, 0.0,
                                   f"Normal volume ({vol_ratio:.1f}x average)")

    def calculate_atr(self, df: pd.DataFrame) -> IndicatorSignal:
        """Calculate ATR for volatility assessment and stop placement"""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = true_range.ewm(span=self.atr_period, adjust=False).mean()

        current_atr = atr.iloc[-1]
        current_price = close.iloc[-1]
        avg_atr = atr.mean()

        if pd.isna(current_atr):
            return IndicatorSignal("ATR", 0.0, 0, 0.0, "Insufficient data")

        atr_pct = (current_atr / current_price) * 100
        volatility_ratio = current_atr / avg_atr if avg_atr > 0 else 1.0

        # High volatility may present opportunity or risk
        if volatility_ratio > 1.5:
            strength = min(100, volatility_ratio * 30)
            return IndicatorSignal("ATR", current_atr, 0, strength,
                                   f"High volatility ({atr_pct:.2f}% ATR, {volatility_ratio:.1f}x avg)")
        elif volatility_ratio < 0.5:
            return IndicatorSignal("ATR", current_atr, 0, 20.0,
                                   f"Low volatility ({atr_pct:.2f}% ATR) - squeeze potential")
        else:
            return IndicatorSignal("ATR", current_atr, 0, 0.0,
                                   f"Normal volatility ({atr_pct:.2f}% ATR)")

    def get_atr_stop_price(self, df: pd.DataFrame, is_long: bool) -> float:
        """Calculate stop price based on ATR"""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = true_range.ewm(span=self.atr_period, adjust=False).mean()

        current_atr = atr.iloc[-1]
        current_price = close.iloc[-1]

        if is_long:
            return current_price - (current_atr * self.atr_stop_multiplier)
        else:
            return current_price + (current_atr * self.atr_stop_multiplier)

    def analyze(self, df: pd.DataFrame) -> CompositeSignal:
        """Run all indicators and generate composite signal"""
        signals = [
            ("rsi", self.calculate_rsi(df)),
            ("macd", self.calculate_macd(df)),
            ("vwap", self.calculate_vwap(df)),
            ("bollinger", self.calculate_bollinger_bands(df)),
            ("ema_crossover", self.calculate_ema_crossover(df)),
            ("volume", self.calculate_volume_analysis(df)),
            ("atr", self.calculate_atr(df)),
        ]

        weighted_score = 0.0
        total_weight = 0.0
        buy_count = 0
        sell_count = 0
        indicator_signals = []

        for name, signal in signals:
            weight = self.weights.get(name, 10)
            weighted_score += signal.signal * signal.strength * (weight / 100.0)
            total_weight += weight

            if signal.signal == 1:
                buy_count += 1
            elif signal.signal == -1:
                sell_count += 1

            indicator_signals.append(signal)

        # Normalize score to -100 to 100
        if total_weight > 0:
            normalized_score = weighted_score / (total_weight / 100.0)
        else:
            normalized_score = 0.0

        # Determine direction
        if normalized_score > 0:
            direction = 1
            confirmations = buy_count
            strength = min(100, abs(normalized_score))
        elif normalized_score < 0:
            direction = -1
            confirmations = sell_count
            strength = min(100, abs(normalized_score))
        else:
            direction = 0
            confirmations = 0
            strength = 0.0

        return CompositeSignal(
            direction=direction,
            strength=strength,
            confirmations=confirmations,
            signals=indicator_signals,
            timestamp=pd.Timestamp.now(),
        )
