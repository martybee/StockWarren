"""
Persistence for human-operator risk-limit overrides.

Overrides set from the dashboard are stored here so they survive a restart.
They are a thin layer ON TOP of config/settings.ini — the RiskManager clamps
them to hard ceilings when applying, so this file only ever holds bounded values.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_PATH = "data/operator_overrides.json"


def load_overrides(path: str = DEFAULT_PATH) -> dict:
    """Return the persisted overrides dict, or {} if none / unreadable."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error("Failed to load operator overrides from %s: %s", path, e)
        return {}


def save_overrides(overrides: dict, path: str = DEFAULT_PATH) -> None:
    """Persist the given overrides dict (already validated/clamped upstream)."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(overrides, f, indent=2, sort_keys=True)
        logger.info("Operator overrides saved: %s", overrides)
    except Exception as e:
        logger.error("Failed to save operator overrides to %s: %s", path, e)


def clear_overrides(path: str = DEFAULT_PATH) -> None:
    """Remove the overrides file (revert to config baseline on next start)."""
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info("Operator overrides cleared (%s removed)", path)
    except Exception as e:
        logger.error("Failed to clear operator overrides at %s: %s", path, e)
