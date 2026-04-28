"""
Slippage and Fill Quality Tracker

Tracks the difference between expected price (when signal fires) and
actual fill price. Critical for paper-vs-live reality check.

Paper trading on Alpaca fills at the NBBO, but live trading has real
slippage from latency, market makers, and liquidity. Tracking this
gives you a baseline before going live.
"""

import csv
import logging
import os
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FillRecord:
    timestamp: str
    symbol: str
    side: str
    qty: float
    expected_price: float
    fill_price: float
    slippage_dollars: float       # Positive = worse than expected
    slippage_pct: float           # Positive = worse than expected
    submission_to_fill_ms: int    # Time from order submit to fill
    order_type: str
    order_id: str


class SlippageTracker:
    """Tracks fill quality across all orders"""

    def __init__(self, csv_path: str = "logs/slippage.csv"):
        self.csv_path = Path(csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

        # Recent fills for quick stats
        self.recent_fills: deque[FillRecord] = deque(maxlen=500)

        # Pending orders tracking (order_id -> expected_price + submit_time)
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

        # Initialize CSV header if new file
        if not self.csv_path.exists():
            self._write_header()

        # Load recent fills from CSV
        self._load_recent()

    def _write_header(self):
        with open(self.csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "symbol", "side", "qty",
                "expected_price", "fill_price",
                "slippage_dollars", "slippage_pct",
                "submission_to_fill_ms",
                "order_type", "order_id",
            ])

    def _load_recent(self):
        if not self.csv_path.exists():
            return
        try:
            with open(self.csv_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        record = FillRecord(
                            timestamp=row["timestamp"],
                            symbol=row["symbol"],
                            side=row["side"],
                            qty=float(row["qty"]),
                            expected_price=float(row["expected_price"]),
                            fill_price=float(row["fill_price"]),
                            slippage_dollars=float(row["slippage_dollars"]),
                            slippage_pct=float(row["slippage_pct"]),
                            submission_to_fill_ms=int(row["submission_to_fill_ms"]),
                            order_type=row["order_type"],
                            order_id=row["order_id"],
                        )
                        self.recent_fills.append(record)
                    except (ValueError, KeyError):
                        continue
        except Exception as e:
            logger.warning(f"Failed to load slippage history: {e}")

    def record_order_submission(self, order_id: str, symbol: str, side: str,
                                 expected_price: float, order_type: str):
        """Call this when an order is submitted"""
        with self._lock:
            self._pending[order_id] = {
                "symbol": symbol,
                "side": side.lower(),
                "expected_price": expected_price,
                "order_type": order_type,
                "submit_time": time.time(),
            }

    def record_fill(self, order_id: str, qty: float, fill_price: float):
        """Call this when an order fills"""
        with self._lock:
            pending = self._pending.pop(order_id, None)

        if not pending:
            logger.debug(f"No pending record for filled order {order_id}")
            return

        expected = pending["expected_price"]
        side = pending["side"]
        latency_ms = int((time.time() - pending["submit_time"]) * 1000)

        # Slippage: positive = worse for us
        # Buy: paid more than expected = positive slippage
        # Sell: received less than expected = positive slippage
        if side == "buy":
            slip_dollars = fill_price - expected
        else:
            slip_dollars = expected - fill_price

        slip_pct = (slip_dollars / expected * 100) if expected > 0 else 0

        record = FillRecord(
            timestamp=datetime.now().isoformat(),
            symbol=pending["symbol"],
            side=side,
            qty=qty,
            expected_price=expected,
            fill_price=fill_price,
            slippage_dollars=slip_dollars,
            slippage_pct=slip_pct,
            submission_to_fill_ms=latency_ms,
            order_type=pending["order_type"],
            order_id=order_id,
        )

        with self._lock:
            self.recent_fills.append(record)

        # Append to CSV
        try:
            with open(self.csv_path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(asdict(record).keys()))
                writer.writerow(asdict(record))
        except Exception as e:
            logger.error(f"Failed to write slippage record: {e}")

        # Log warnings for unusually bad fills
        if abs(slip_pct) > 0.5:
            logger.warning(
                f"High slippage on {pending['symbol']}: "
                f"expected=${expected:.4f} fill=${fill_price:.4f} "
                f"slip={slip_pct:+.2f}%"
            )

    def get_stats(self) -> dict:
        """Compute aggregate slippage statistics"""
        with self._lock:
            fills = list(self.recent_fills)

        if not fills:
            return {
                "total_fills": 0,
                "avg_slippage_pct": 0.0,
                "median_slippage_pct": 0.0,
                "worst_slippage_pct": 0.0,
                "avg_fill_latency_ms": 0,
                "by_order_type": {},
            }

        slip_pcts = [f.slippage_pct for f in fills]
        latencies = [f.submission_to_fill_ms for f in fills]

        # Group by order type
        by_type: dict[str, list[float]] = {}
        for f in fills:
            by_type.setdefault(f.order_type, []).append(f.slippage_pct)

        return {
            "total_fills": len(fills),
            "avg_slippage_pct": statistics.mean(slip_pcts),
            "median_slippage_pct": statistics.median(slip_pcts),
            "worst_slippage_pct": max(slip_pcts, key=abs),
            "avg_fill_latency_ms": int(statistics.mean(latencies)),
            "median_fill_latency_ms": int(statistics.median(latencies)),
            "by_order_type": {
                t: {
                    "count": len(v),
                    "avg_slippage_pct": statistics.mean(v),
                    "worst_pct": max(v, key=abs),
                }
                for t, v in by_type.items()
            },
        }

    def get_recent_fills(self, limit: int = 50) -> list[dict]:
        """Get recent fills as dicts"""
        with self._lock:
            return [asdict(f) for f in list(self.recent_fills)[-limit:]]
