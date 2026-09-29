"""
Versioning + quarantine for StockWarren's JSON state files (engine M2).

Two rules, both fail-closed (constitution rule 5 applied to our OWN files):

1. Every state file carries "schema_version". A file without one is a legacy
   v0 file — accepted, and upgraded in place on its next save. A file with a
   version NEWER than this code understands is quarantined: we never guess at
   a future format.

2. A structurally invalid file (unparseable JSON, wrong top-level shape) is
   QUARANTINED, never overwritten: it is renamed to <name>.invalid-<utc-stamp>
   so the next save cannot destroy the evidence, and the caller falls back to
   its safe default (empty scheduler state / config-baseline risk limits).

Stdlib only, deliberately tiny. SCHEMA.md is the spec this enforces.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def quarantine(path: str, reason: str) -> Optional[str]:
    """Rename a bad state file out of harm's way. Returns the new path.

    Never deletes: the renamed file is the post-mortem evidence. If even the
    rename fails, we log CRITICAL and return None — the caller must STILL fall
    back to its safe default and must not trust the file.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = f"{path}.invalid-{stamp}"
    try:
        os.rename(path, target)
        logger.critical(
            "STATE FILE QUARANTINED: %s -> %s (%s). "
            "Falling back to the safe default; inspect the file by hand.",
            path, target, reason,
        )
        return target
    except OSError as e:
        logger.critical(
            "STATE FILE INVALID and quarantine rename FAILED: %s (%s); "
            "rename error: %s. Treating contents as untrusted anyway.",
            path, reason, e,
        )
        return None


def version_gate(data: dict, path: str) -> tuple[bool, int]:
    """Check a loaded dict's schema_version. Returns (acceptable, version).

    Missing key => legacy v0, acceptable (upgraded on next save).
    Non-integer or newer than SCHEMA_VERSION => not acceptable (fail closed).
    """
    raw = data.get("schema_version", 0)
    if not isinstance(raw, int) or isinstance(raw, bool):
        logger.critical("%s: schema_version is not an integer (%r)", path, raw)
        return False, 0
    if raw > SCHEMA_VERSION:
        logger.critical(
            "%s: schema_version %d is newer than this code understands (%d)",
            path, raw, SCHEMA_VERSION,
        )
        return False, raw
    return True, raw
