"""Persistence for per-account watchlists.

Watchlists edited from the dashboard used to live only in memory, so every
restart silently reverted all accounts to `[watchlist] symbols` in
config/settings.ini — quietly discarding any curation.

The model mirrors src/utils/overrides.py: config seeds a brand-new account, and
once an account has been edited its saved list wins on startup. One file per
account, so curating one account can never disturb another.

An EMPTY saved list is meaningful ("I removed everything") and is honoured, which
is why load_watchlist() returns None rather than [] to signal "nothing saved".
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


def path_for(account_id: str) -> str:
    return f"data/watchlist_{account_id}.json"


def load_watchlist(account_id: str):
    """Saved symbols for an account, or None when it has never been edited.

    None (not []) means "fall back to config" — an empty list is a real,
    deliberate state and must not be confused with the absence of a file.
    """
    path = path_for(account_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        symbols = data.get("symbols") if isinstance(data, dict) else data
        if not isinstance(symbols, list):
            logger.warning("Watchlist file %s has an unexpected shape; ignoring.", path)
            return None
        return [str(s).strip().upper() for s in symbols if str(s).strip()]
    except Exception as e:
        # Never let a corrupt file stop an account from starting — fall back to
        # config, which is always valid.
        logger.error("Failed to load watchlist from %s: %s", path, e)
        return None


def save_watchlist(account_id: str, symbols: list) -> None:
    path = path_for(account_id)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({"symbols": list(symbols)}, f, indent=2)
        logger.info("Watchlist saved for '%s': %s", account_id, ",".join(symbols) or "(empty)")
    except Exception as e:
        logger.error("Failed to save watchlist for '%s' to %s: %s", account_id, path, e)


def clear_watchlist(account_id: str) -> None:
    """Drop the saved list so config seeds this account again on next start."""
    path = path_for(account_id)
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info("Watchlist for '%s' cleared (%s removed)", account_id, path)
    except Exception as e:
        logger.error("Failed to clear watchlist for '%s': %s", account_id, e)
