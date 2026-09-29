"""
Persistence for human-operator risk-limit overrides.

Overrides set from the dashboard are stored here so they survive a restart.
They are a thin layer ON TOP of config/settings.ini — the RiskManager clamps
them to hard ceilings when applying, so this file only ever holds bounded values.

Validated on load (engine M2, fail-closed): a structurally bad file is
quarantined and the caller gets {} — which means the config-baseline limits,
the safe default. Value-level damage (a non-numeric entry, an unknown key) is
dropped with a warning while the intact values still apply; the clamping in
RiskManager.apply_operator_override() remains the authority on ranges.
"""

import json
import logging
import os

from src.utils.state_schema import SCHEMA_VERSION, quarantine, version_gate

logger = logging.getLogger(__name__)

DEFAULT_PATH = "data/operator_overrides.json"


def load_overrides(path: str = DEFAULT_PATH) -> dict:
    """Return the persisted overrides dict, or {} (config baseline) if absent
    or invalid. A structurally invalid file is quarantined, never reused."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except Exception as e:
        quarantine(path, f"unparseable JSON: {e}")
        return {}

    if not isinstance(data, dict):
        quarantine(path, f"top level is {type(data).__name__}, expected object")
        return {}

    ok, version = version_gate(data, path)
    if not ok:
        quarantine(path, "unacceptable schema_version")
        return {}
    if version == 0:
        logger.info("%s is legacy v0; it will be upgraded on next save", path)

    overrides: dict = {}
    for key, value in data.items():
        if key == "schema_version":
            continue
        if not isinstance(key, str):
            logger.warning("%s: dropping non-string key %r", path, key)
            continue
        # bool is an int subclass; a True/False "limit" is damage, not a value.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            logger.warning(
                "%s: dropping %r — value %r is not numeric (Rule 5: malformed "
                "data never becomes a limit)", path, key, value,
            )
            continue
        overrides[key] = value
    return overrides


def save_overrides(overrides: dict, path: str = DEFAULT_PATH) -> None:
    """Persist the given overrides dict (already validated/clamped upstream)."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        payload = dict(overrides)
        payload["schema_version"] = SCHEMA_VERSION
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
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
