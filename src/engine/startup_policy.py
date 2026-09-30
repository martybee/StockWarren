"""
Crash-restart policy (engine M6) — CHART_PLAN §14 Decision 3, as code:

  "On supervised restart, the bots resume trading only if KILL_SWITCH.lock is
   absent AND all state files pass validation; three or more restarts within
   one hour trips the kill switch automatically and the process comes up
   dashboard-only. Reopened at engine M8."

Mechanics:
- Every process start is recorded in data/restart_history.json (a versioned
  state file like any other — corrupt means quarantined, and a quarantine is
  itself an unclean state).
- Only SUPERVISED starts (STOCKWARREN_SUPERVISED=1, set by the launchd plist)
  count toward the crash-loop breaker: a human restarting by hand three times
  during an evening of dev work is deliberate, not a crash loop.
- The breaker trips EVERY configured account's kill switch: a process-level
  crash loop is shared fate, and per the constitution only a human removing
  the lock files resumes trading.
- Clean-state check applies to ALL bot-starting modes, supervised or manual:
  a present kill switch or a quarantined state file means bots do not start.
  This is Rule 5 applied to our own boot.
"""

import glob
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.engine.safety import KillSwitch
from src.utils import state_schema
from src.utils.state_schema import SCHEMA_VERSION, quarantine, version_gate

logger = logging.getLogger(__name__)

HISTORY_FILE = "restart_history.json"
BREAKER_THRESHOLD = 3            # supervised starts within the window...
BREAKER_WINDOW = timedelta(hours=1)   # ...this window
HISTORY_RETENTION = timedelta(hours=24)


@dataclass
class StartupDecision:
    start_bots: bool
    breaker_tripped: bool = False
    reasons: list = field(default_factory=list)


def _load_history(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as e:
        quarantine(path, f"unparseable JSON: {e}")
        return []
    if not isinstance(data, dict) or not isinstance(data.get("starts", []), list):
        quarantine(path, "wrong top-level shape")
        return []
    ok, _ = version_gate(data, path)
    if not ok:
        quarantine(path, "unacceptable schema_version")
        return []
    starts = []
    for entry in data.get("starts", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("ts"), str):
            continue  # bookkeeping damage: drop quietly, the file still loads
        try:
            ts = datetime.fromisoformat(entry["ts"])
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        starts.append({"ts": ts, "supervised": bool(entry.get("supervised"))})
    return starts


def record_start(supervised: bool, now: datetime = None,
                 data_dir: str = "data") -> list[dict]:
    """Append this start to the history (pruned to 24h) and return the list."""
    now = now or datetime.now(timezone.utc)
    path = os.path.join(data_dir, HISTORY_FILE)
    starts = [s for s in _load_history(path) if now - s["ts"] <= HISTORY_RETENTION]
    starts.append({"ts": now, "supervised": supervised})
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "schema_version": SCHEMA_VERSION,
                "starts": [{"ts": s["ts"].isoformat(),
                            "supervised": s["supervised"]} for s in starts],
            }, f, indent=2)
    except OSError as e:
        logger.error("Could not persist restart history: %s", e)
    return starts


def kill_switch_files(data_dir: str = "data") -> list[str]:
    """Every kill-switch lock present: global and per-account."""
    return sorted(glob.glob(os.path.join(data_dir, "KILL_SWITCH*.lock")))


def evaluate_startup(account_ids: list[str], supervised: bool,
                     now: datetime = None, data_dir: str = "data") -> StartupDecision:
    """Decide whether bots may start trading this boot. Fail-closed.

    Call AFTER all state files have been loaded (AccountManager, TradeScheduler)
    so state_schema.QUARANTINED reflects this boot's validation results.
    """
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []

    starts = record_start(supervised, now=now, data_dir=data_dir)

    # --- Crash-loop breaker (supervised starts only) ---
    breaker_tripped = False
    if supervised:
        recent = [s for s in starts
                  if s["supervised"] and now - s["ts"] <= BREAKER_WINDOW]
        if len(recent) >= BREAKER_THRESHOLD:
            breaker_tripped = True
            reason = (f"crash-loop breaker: {len(recent)} supervised restarts "
                      f"within one hour (threshold {BREAKER_THRESHOLD})")
            reasons.append(reason)
            for aid in account_ids:
                KillSwitch(os.path.join(data_dir, f"KILL_SWITCH_{aid}.lock")).trip(reason)
            logger.critical(
                "CRASH-LOOP BREAKER TRIPPED: %s. Kill switches engaged for %s; "
                "coming up dashboard-only. A human must remove the lock files "
                "to resume trading.", reason, ", ".join(account_ids) or "no accounts")

    # --- Clean-state check (all modes) ---
    switches = kill_switch_files(data_dir)
    if switches:
        reasons.append(f"kill switch present: {', '.join(switches)}")
    if state_schema.QUARANTINED:
        reasons.append(
            f"state file(s) quarantined this boot: {', '.join(state_schema.QUARANTINED)}")

    start_bots = not reasons
    if start_bots:
        logger.info("Startup policy: clean state, bots may start "
                    "(%d start(s) recorded in the last 24h).", len(starts))
    else:
        logger.critical("Startup policy: bots will NOT start — %s",
                        "; ".join(reasons))
    return StartupDecision(start_bots=start_bots,
                           breaker_tripped=breaker_tripped, reasons=reasons)
