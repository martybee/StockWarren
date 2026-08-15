"""
AccountManager — runs multiple Alpaca paper accounts, each with its own
TradingBot and strategy, so their performance can be compared head-to-head.

Accounts and strategies are defined in config ([accounts], [account:<id>],
[strategy:<name>]). Keys come from .env (per-account env var names). An account
whose keys are missing is kept as "not configured" — it never crashes startup.
"""

import os
import logging
import threading
from configparser import ConfigParser

logger = logging.getLogger(__name__)


class Account:
    """One configured (or not-yet-configured) trading account."""

    def __init__(self, account_id, name, strategy_name, configured, bot=None, error=None):
        self.id = account_id
        self.name = name
        self.strategy_name = strategy_name
        self.configured = configured
        self.bot = bot
        self.error = error
        self._thread = None

    def to_summary(self):
        return {
            "id": self.id,
            "name": self.name,
            "strategy": self.strategy_name,
            "configured": self.configured,
            "running": bool(self.bot and self.bot.running),
            "ml_enabled": bool(self.bot and getattr(self.bot, "ml_enabled", False)),
            # In-memory read, no broker call — keeps to_summary() cheap enough for
            # the 5s poll. Lets the dashboard show every account's watchlist
            # without switching accounts to look at each one.
            "watchlist": self.bot.watchlist.get_symbols() if self.bot else [],
            "error": self.error,
        }


class AccountManager:
    """Owns every trading account and orchestrates them together."""

    def __init__(self, config_path="config/settings.ini", audit_logger=None):
        self.config_path = config_path
        self.config = ConfigParser()
        self.config.read(config_path)
        self.audit_logger = audit_logger
        self.accounts = []       # list[Account], in config order
        self._by_id = {}
        self._build()

    def _strategy_overrides(self, strategy_name):
        section = f"strategy:{strategy_name}"
        if strategy_name and self.config.has_section(section):
            return dict(self.config[section])
        return {}

    def _build(self):
        if not self.config.has_section("accounts"):
            logger.warning("No [accounts] section in config — no multi-account setup.")
            return
        ids = [s.strip() for s in self.config.get("accounts", "ids", fallback="").split(",") if s.strip()]
        from src.engine.trading_bot import TradingBot

        for acc_id in ids:
            sec = f"account:{acc_id}"
            if not self.config.has_section(sec):
                logger.warning("Account '%s' has no [%s] section — skipping.", acc_id, sec)
                continue
            a = dict(self.config[sec])
            name = a.get("name", acc_id)
            strategy_name = a.get("strategy", "")
            enabled = str(a.get("enabled", "true")).lower() in ("true", "yes", "on", "1")
            key_env = a.get("api_key_env", "ALPACA_API_KEY")
            secret_env = a.get("secret_key_env", "ALPACA_SECRET_KEY")
            api_key = os.getenv(key_env)
            secret_key = os.getenv(secret_env)

            if not enabled:
                self._add(Account(acc_id, name, strategy_name, configured=False, error="disabled"))
                continue
            if not api_key or not secret_key:
                self._add(Account(acc_id, name, strategy_name, configured=False,
                                  error=f"missing keys ({key_env}/{secret_env})"))
                logger.info("Account '%s' not configured (set %s and %s in .env).",
                            acc_id, key_env, secret_env)
                continue
            try:
                bot = TradingBot(
                    config_path=self.config_path,
                    api_key=api_key, secret_key=secret_key,
                    strategy=self._strategy_overrides(strategy_name),
                    name=name, account_id=acc_id,
                )
                if self.audit_logger is not None:
                    bot.audit_logger = self.audit_logger
                self._add(Account(acc_id, name, strategy_name, configured=True, bot=bot))
                logger.info("Account '%s' (%s) initialized — strategy '%s'.",
                            acc_id, name, strategy_name or "base")
            except Exception as e:
                logger.error("Failed to initialize account '%s': %s", acc_id, e, exc_info=True)
                self._add(Account(acc_id, name, strategy_name, configured=False, error=str(e)))

    def _add(self, account):
        self.accounts.append(account)
        self._by_id[account.id] = account

    # ---- accessors ----
    def get(self, account_id):
        return self._by_id.get(account_id)

    def get_bot(self, account_id):
        a = self._by_id.get(account_id)
        return a.bot if a else None

    def configured_accounts(self):
        return [a for a in self.accounts if a.configured and a.bot]

    def first_configured_id(self):
        for a in self.accounts:
            if a.configured and a.bot:
                return a.id
        return None

    def list_summary(self):
        return [a.to_summary() for a in self.accounts]

    # ---- lifecycle ----
    def start_all(self):
        for a in self.configured_accounts():
            t = threading.Thread(target=a.bot.start, daemon=True, name=f"bot-{a.id}")
            a._thread = t
            t.start()
            logger.info("Started bot thread for account '%s'.", a.id)

    def stop_all(self):
        for a in self.configured_accounts():
            try:
                a.bot.stop()
            except Exception as e:
                logger.error("Error stopping account '%s': %s", a.id, e)

    # ---- comparison ----
    def compare(self):
        """Leaderboard rows (ranked by total P&L) for each configured account."""
        rows = []
        for a in self.configured_accounts():
            bot = a.bot
            try:
                equity = bot.alpaca.get_account().get("portfolio_value", None)
            except Exception:
                equity = None
            stats = bot.risk_manager.get_stats()
            rows.append({
                "id": a.id, "name": a.name, "strategy": a.strategy_name,
                "running": bot.running, "ml_enabled": bot.ml_enabled, "equity": equity,
                "total_pnl": stats.get("total_pnl", 0.0),
                "daily_pnl": stats.get("daily_pnl", 0.0),
                "win_rate": stats.get("win_rate", 0.0),
                "total_trades": stats.get("total_trades", 0),
                "max_drawdown": stats.get("max_drawdown", 0.0),
                "drawdown_pct": stats.get("drawdown_pct", 0.0),
                "open_positions": stats.get("active_positions", 0),
            })
        rows.sort(key=lambda r: (r["total_pnl"] if r["total_pnl"] is not None else -1e18), reverse=True)
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        return rows
