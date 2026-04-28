"""
Logging configuration for StockWarren

Sets up:
- Rotating file logs (10MB max, 5 backups) for general logs
- Separate trade audit log that NEVER rotates (compliance-friendly)
- Console output with rich colors when available
- Per-module log level control
"""

import logging
import logging.handlers
import os
import sys
from datetime import datetime
from pathlib import Path


TRADE_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | TRADE | %(message)s"
)

GENERAL_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)


def setup_logging(
    log_dir: str = "logs",
    console_level: str = "INFO",
    file_level: str = "DEBUG",
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
):
    """Configure application-wide logging"""

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Root logger - capture everything
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Remove existing handlers (avoid duplicates on reload)
    for h in root.handlers[:]:
        root.removeHandler(h)

    formatter = logging.Formatter(GENERAL_LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")

    # Console handler - colorized if rich is available
    try:
        from rich.logging import RichHandler
        console_handler = RichHandler(
            rich_tracebacks=True,
            show_time=True,
            show_path=False,
            markup=False,
        )
        console_handler.setLevel(getattr(logging, console_level))
    except ImportError:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(getattr(logging, console_level))
    root.addHandler(console_handler)

    # Main rotating log
    main_log = log_path / "stockwarren.log"
    main_handler = logging.handlers.RotatingFileHandler(
        main_log,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    main_handler.setFormatter(formatter)
    main_handler.setLevel(getattr(logging, file_level))
    root.addHandler(main_handler)

    # Error-only log (filtered ERROR+ for quick triage)
    error_log = log_path / "errors.log"
    error_handler = logging.handlers.RotatingFileHandler(
        error_log,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.WARNING)
    root.addHandler(error_handler)

    # Quiet down chatty libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("engineio").setLevel(logging.WARNING)
    logging.getLogger("socketio").setLevel(logging.WARNING)

    logging.info(f"Logging initialized. Files: {main_log}, {error_log}")
    return root


class TradeAuditLogger:
    """
    Append-only audit log for every trade event.
    Files are dated and never rotated — keep forever for compliance.
    """

    def __init__(self, log_dir: str = "logs/trades"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger("trade_audit")
        logger.setLevel(logging.INFO)
        logger.propagate = False

        # Avoid duplicate handlers
        if logger.handlers:
            return logger

        today = datetime.now().strftime("%Y-%m")
        log_file = self.log_dir / f"trades_{today}.log"

        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter(TRADE_LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(handler)
        return logger

    def log_signal(self, symbol: str, direction: str, strength: float, reasons: list):
        self._logger.info(
            f"SIGNAL | {symbol} | {direction} | strength={strength:.0f} | "
            f"reasons={'; '.join(reasons)}"
        )

    def log_order_placed(self, symbol: str, side: str, qty: float,
                          order_type: str, price: float, order_id: str):
        self._logger.info(
            f"ORDER_PLACED | {symbol} | {side.upper()} | qty={qty} | "
            f"type={order_type} | price=${price:.2f} | id={order_id}"
        )

    def log_order_filled(self, symbol: str, side: str, qty: float,
                         expected_price: float, fill_price: float,
                         order_id: str):
        slippage = fill_price - expected_price
        slippage_pct = (slippage / expected_price * 100) if expected_price > 0 else 0
        # For sells, slippage flips sign
        if side.lower() == "sell":
            slippage = -slippage
            slippage_pct = -slippage_pct

        self._logger.info(
            f"ORDER_FILLED | {symbol} | {side.upper()} | qty={qty} | "
            f"expected=${expected_price:.4f} | fill=${fill_price:.4f} | "
            f"slippage=${slippage:+.4f} ({slippage_pct:+.3f}%) | id={order_id}"
        )

    def log_order_rejected(self, symbol: str, side: str, qty: float, reason: str):
        self._logger.warning(
            f"ORDER_REJECTED | {symbol} | {side.upper()} | qty={qty} | reason={reason}"
        )

    def log_position_closed(self, symbol: str, exit_price: float,
                             pnl: float, reason: str):
        self._logger.info(
            f"POSITION_CLOSED | {symbol} | exit=${exit_price:.4f} | "
            f"pnl=${pnl:+.2f} | reason={reason}"
        )

    def log_stop_updated(self, symbol: str, old_stop: float, new_stop: float):
        self._logger.info(
            f"STOP_UPDATED | {symbol} | ${old_stop:.4f} -> ${new_stop:.4f}"
        )

    def log_emergency(self, reason: str):
        self._logger.critical(f"EMERGENCY | {reason}")
