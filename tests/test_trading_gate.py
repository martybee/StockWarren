"""Prove the dashboard can ask "is trading blocked?" without touching the engine.

    venv/bin/python tests/test_trading_gate.py

Why this test is load-bearing
-----------------------------
RiskManager.is_trading_allowed() is not a query — it expires pauses, rolls the
daily/weekly stats, moves the peak-equity baseline every drawdown number is
measured from, sets the flag the bot reads to trip the kill switch, and can start
a pause. The dashboard polls every 5 seconds. If the safety badge ever called it,
a browser tab left open would silently corrupt risk state.

So the badge calls RiskManager.evaluate_gate(), which must be PURE. This test
asserts three things:

  (a) evaluate_gate() leaves the RiskManager byte-identical (deep state compare).
  (b) is_trading_allowed() returns the SAME (allowed, reason) as evaluate_gate()
      on an identically-constructed twin — so delegating the verdict did not
      change engine behaviour.
  (c) Every GATE_* code has a badge-state mapping AND copy in dashboard.js, so a
      new blocking reason cannot ship as a blank badge.
"""
import copy
import pathlib
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.engine.risk_manager import RiskManager
from src.engine import safety
from src.engine.safety import GATE_STATE

failures = []
checks = 0


def check(label, cond):
    global checks
    checks += 1
    if not cond:
        failures.append(label)
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")


BASE_CFG = {
    "max_daily_loss": 200.0,
    "max_daily_loss_pct": 1.5,
    "max_weekly_loss_pct": 3.0,
    "max_consecutive_losses": 3,
    "max_positions": 2,
    "shutdown_drawdown_pct": 8.0,
    "review_drawdown_pct": 5.0,
    "risk_per_trade_pct": 0.25,
}
EQUITY = 9000.0


def fresh():
    return RiskManager(dict(BASE_CFG))


def snapshot_state(rm):
    """Everything that could conceivably be mutated, as a comparable structure."""
    return copy.deepcopy({
        "stats": vars(rm.stats),
        "drawdown_pct": rm.drawdown_pct,
        "review_flagged": rm.review_flagged,
        "shutdown_triggered": rm.shutdown_triggered,
        "is_paused": rm.is_paused,
        "pause_until": rm.pause_until,
        "active_positions": {k: vars(v) for k, v in rm.active_positions.items()},
    })


# Each scenario mutates a fresh RiskManager into an interesting state.
def sc_clean(rm):
    pass


def sc_daily_loss(rm):
    rm.stats.daily_pnl = -250.0            # beyond max_daily_loss 200


def sc_daily_loss_pct(rm):
    rm.stats.daily_pnl = -150.0            # 1.67% of 9000, over the 1.5% cap


def sc_weekly_loss_pct(rm):
    rm.stats.weekly_pnl = -300.0           # 3.33% of 9000, over the 3.0% cap


def sc_consecutive(rm):
    rm.stats.consecutive_losses = 3


def sc_paused(rm):
    rm.is_paused = True
    rm.pause_until = datetime.now() + timedelta(minutes=30)


def sc_pause_expired(rm):
    rm.is_paused = True
    rm.pause_until = datetime.now() - timedelta(minutes=1)


def sc_drawdown(rm):
    rm.stats.peak_portfolio_value = 10000.0
    rm.drawdown_pct = 10.0                 # over shutdown_drawdown_pct 8.0


def sc_max_positions(rm):
    from src.engine.risk_manager import Position
    for sym in ("AAA", "BBB"):
        rm.active_positions[sym] = Position(
            symbol=sym, side="buy", qty=1, entry_price=10.0,
            entry_time=datetime.now(), stop_price=9.0, target_price=12.0)


SCENARIOS = [
    ("clean", sc_clean), ("daily_loss", sc_daily_loss),
    ("daily_loss_pct", sc_daily_loss_pct), ("weekly_loss_pct", sc_weekly_loss_pct),
    ("consecutive_losses", sc_consecutive), ("paused", sc_paused),
    ("pause_expired", sc_pause_expired), ("drawdown_shutdown", sc_drawdown),
    ("max_positions", sc_max_positions),
]

print("\n(a) evaluate_gate() must not mutate the RiskManager")
for name, setup in SCENARIOS:
    rm = fresh()
    setup(rm)
    before = snapshot_state(rm)
    for _ in range(3):                      # repeat: a 5s poll calls it forever
        rm.evaluate_gate(EQUITY)
    check(f"{name}: state unchanged after 3 evaluate_gate() calls",
          snapshot_state(rm) == before)

print("\n(b) evaluate_gate() agrees with is_trading_allowed() on a twin")
for name, setup in SCENARIOS:
    a, b = fresh(), fresh()
    setup(a)
    setup(b)
    pure = a.evaluate_gate(EQUITY)
    allowed, reason = b.is_trading_allowed(EQUITY)
    # pause_expired legitimately differs: is_trading_allowed CLEARS the stale
    # pause as a side effect and then re-evaluates, which is its job. The pure
    # function reports the state as it actually stands right now.
    if name == "pause_expired":
        check(f"{name}: tick-loop clears the expired pause (pure reports as-is)",
              allowed is True and pure.allowed is True)
        continue
    check(f"{name}: same verdict ({pure.code})",
          (pure.allowed, pure.reason) == (allowed, reason))

print("\n(c) Every gate code is classified, and has copy in the dashboard")
codes = {v for k, v in vars(safety).items()
         if k.startswith("GATE_") and isinstance(v, str) and k != "GATE_STATE"}
for c in sorted(codes):
    check(f"{c!r} has a badge state", c in GATE_STATE)

js = (pathlib.Path(__file__).resolve().parent.parent
      / "gui/static/js/dashboard.js").read_text()
for state in sorted(set(GATE_STATE.values())):
    check(f"badge state {state!r} has copy in dashboard.js",
          re.search(rf"\b{state}\b", js) is not None)

print("\n(d) The pure evaluator classifies each scenario as expected")
EXPECT = {
    "clean": "ok", "daily_loss": "daily_loss", "daily_loss_pct": "daily_loss_pct",
    "weekly_loss_pct": "weekly_loss_pct", "consecutive_losses": "consecutive_losses",
    "paused": "paused", "pause_expired": "ok", "drawdown_shutdown": "drawdown_shutdown",
    "max_positions": "max_positions",
}
for name, setup in SCENARIOS:
    rm = fresh()
    setup(rm)
    got = rm.evaluate_gate(EQUITY).code
    check(f"{name} -> {EXPECT[name]}", got == EXPECT[name])

print("\n" + "=" * 60)
if failures:
    print(f"FAIL — {len(failures)} of {checks} checks failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(f"PASS — {checks} checks")
