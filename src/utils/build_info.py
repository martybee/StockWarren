"""
Staleness guard (engine M6): which code is this process actually running?

Born from the 88-day gotcha (SESSION_NOTES 2026-07-19): a long-lived process
silently runs old code, and a documented feature looks broken when it simply
was never loaded. The guard makes that visible instead of discoverable:

- `RUNNING_COMMIT` is the git HEAD captured ONCE, at import time — i.e. the
  commit the running process was started from.
- `disk_commit()` re-reads HEAD from the working tree (cached briefly), i.e.
  the code a restart WOULD load.
- `staleness()` compares the two for the dashboard header and /api/status.

Read-only with respect to git; degrades to "unknown" (never crashes startup)
when git is missing or this isn't a checkout.
"""

import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _git_head() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=5,
        )
        commit = out.stdout.strip()
        return commit if out.returncode == 0 and len(commit) == 40 else None
    except Exception as e:
        logger.debug("build_info: git rev-parse failed: %s", e)
        return None


# Captured once at import: the code this PROCESS is running.
RUNNING_COMMIT: Optional[str] = _git_head()
STARTED_AT: datetime = datetime.now(timezone.utc)

_disk_cache: tuple = (0.0, None)   # (fetched_at_monotonic, commit)
_DISK_TTL_S = 15.0                 # polled every 5s by the dashboard; don't fork git each time


def disk_commit() -> Optional[str]:
    """HEAD as it stands on disk right now (what a restart would load)."""
    global _disk_cache
    now = time.monotonic()
    fetched_at, cached = _disk_cache
    if now - fetched_at < _DISK_TTL_S:
        return cached
    commit = _git_head()
    _disk_cache = (now, commit)
    return commit


def is_stale(running: Optional[str], disk: Optional[str]) -> Optional[bool]:
    """True/False when both commits are known; None when either is unknown —
    an honest 'cannot verify', never a false alarm and never false comfort."""
    if running is None or disk is None:
        return None
    return running != disk


def staleness() -> dict:
    """The /api/status 'process' block."""
    disk = disk_commit()
    return {
        "started_at": STARTED_AT.isoformat(),
        "running_commit": RUNNING_COMMIT,
        "disk_commit": disk,
        "stale": is_stale(RUNNING_COMMIT, disk),
    }
