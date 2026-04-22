"""
Notification System for StockWarren
Supports Discord webhooks and email alerts
"""

import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class Notifier:
    """Send trading notifications via Discord and email"""

    def __init__(self, config: dict):
        # Discord
        self.discord_enabled = config.get("discord_enabled", False)
        self.discord_webhook = os.getenv("DISCORD_WEBHOOK_URL", "")

        # Email
        self.email_enabled = config.get("email_enabled", False)
        self.smtp_host = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "587"))
        self.email_user = os.getenv("EMAIL_USERNAME", "")
        self.email_pass = os.getenv("EMAIL_PASSWORD", "")
        self.email_to = os.getenv("EMAIL_TO", "")

        # What to notify
        self.notify_on_entry = config.get("notify_on_entry", True)
        self.notify_on_exit = config.get("notify_on_exit", True)
        self.notify_daily_summary = config.get("notify_daily_summary", True)
        self.notify_on_error = config.get("notify_on_error", True)

    def notify_trade_entry(self, symbol: str, side: str, qty: float,
                            price: float, stop: float, target: float,
                            signal_strength: float, rr_ratio: float):
        """Notify on trade entry"""
        if not self.notify_on_entry:
            return

        direction_emoji = "BUY" if side.lower() == "buy" else "SELL"
        pnl_target = abs(target - price) * qty
        risk_amount = abs(price - stop) * qty

        message = (
            f"NEW TRADE: {direction_emoji} {qty} {symbol}\n"
            f"Entry: ${price:.2f}\n"
            f"Stop: ${stop:.2f} (Risk: ${risk_amount:.2f})\n"
            f"Target: ${target:.2f} (Reward: ${pnl_target:.2f})\n"
            f"R:R = {rr_ratio:.1f}\n"
            f"Signal Strength: {signal_strength:.0f}%\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}"
        )

        self._send(f"Trade Entry: {side.upper()} {symbol}", message)

    def notify_trade_exit(self, symbol: str, exit_price: float,
                           pnl: float, reason: str):
        """Notify on trade exit"""
        if not self.notify_on_exit:
            return

        result = "WIN" if pnl > 0 else "LOSS"
        message = (
            f"TRADE CLOSED: {symbol}\n"
            f"Result: {result}\n"
            f"P&L: ${pnl:+.2f}\n"
            f"Exit Price: ${exit_price:.2f}\n"
            f"Reason: {reason}\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}"
        )

        self._send(f"Trade Exit: {symbol} ({result})", message)

    def notify_daily_summary_report(self, stats: dict):
        """Send daily trading summary"""
        if not self.notify_daily_summary:
            return

        message = (
            f"DAILY SUMMARY - {datetime.now().strftime('%Y-%m-%d')}\n"
            f"{'='*30}\n"
            f"Total Trades: {stats.get('total_trades', 0)}\n"
            f"Winning: {stats.get('winning_trades', 0)}\n"
            f"Losing: {stats.get('losing_trades', 0)}\n"
            f"Win Rate: {stats.get('win_rate', 0):.1f}%\n"
            f"Daily P&L: ${stats.get('daily_pnl', 0):+.2f}\n"
            f"Total P&L: ${stats.get('total_pnl', 0):+.2f}\n"
            f"Max Drawdown: ${stats.get('max_drawdown', 0):.2f}\n"
        )

        self._send("Daily Trading Summary", message)

    def notify_error(self, error_message: str):
        """Notify on error or warning"""
        if not self.notify_on_error:
            return

        message = (
            f"ERROR ALERT\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}\n"
            f"Message: {error_message}"
        )

        self._send("StockWarren Error Alert", message)

    def notify_emergency_stop(self):
        """Notify on emergency stop activation"""
        message = (
            f"EMERGENCY STOP ACTIVATED\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}\n"
            f"All positions closed and orders cancelled.\n"
            f"Bot has been stopped."
        )

        self._send("EMERGENCY STOP", message)

    def _send(self, subject: str, message: str):
        """Send notification via all enabled channels"""
        if self.discord_enabled and self.discord_webhook:
            self._send_discord(message)

        if self.email_enabled and self.email_user:
            self._send_email(subject, message)

    def _send_discord(self, message: str):
        """Send Discord webhook notification"""
        try:
            payload = {
                "content": f"```\n{message}\n```",
                "username": "StockWarren Bot",
            }
            response = requests.post(
                self.discord_webhook,
                json=payload,
                timeout=10,
            )
            if response.status_code not in (200, 204):
                logger.warning(f"Discord notification failed: {response.status_code}")
        except Exception as e:
            logger.error(f"Discord notification error: {e}")

    def _send_email(self, subject: str, body: str):
        """Send email notification"""
        try:
            msg = MIMEMultipart()
            msg["From"] = self.email_user
            msg["To"] = self.email_to
            msg["Subject"] = f"[StockWarren] {subject}"

            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.email_user, self.email_pass)
                server.send_message(msg)

            logger.info(f"Email sent: {subject}")
        except Exception as e:
            logger.error(f"Email notification error: {e}")
