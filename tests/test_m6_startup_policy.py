"""Engine M6 — the crash-restart policy as decided in CHART_PLAN §14
Decision 3: auto-resume only if clean (no kill switch, no quarantined state
file), and >= 3 SUPERVISED restarts within one hour trips every account's
kill switch. Manual restarts are deliberate and never trip the breaker."""

from datetime import datetime, timedelta, timezone

import pytest

from src.engine.startup_policy import (
    BREAKER_THRESHOLD,
    evaluate_startup,
    kill_switch_files,
    record_start,
)
from src.utils import state_schema
from src.utils.state_schema import SCHEMA_VERSION

UTC = timezone.utc
T0 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
ACCOUNTS = ["alpha", "beta", "gamma"]


@pytest.fixture(autouse=True)
def clean_quarantine_register():
    """QUARANTINED is process-global; other test modules legitimately grow it.
    These tests reason about 'this boot', so isolate it per test."""
    saved = list(state_schema.QUARANTINED)
    state_schema.QUARANTINED.clear()
    yield
    state_schema.QUARANTINED[:] = saved


def evaluate(tmp_path, supervised=True, now=T0, accounts=ACCOUNTS):
    return evaluate_startup(account_ids=accounts, supervised=supervised,
                            now=now, data_dir=str(tmp_path))


# ==================== clean-state check ====================

def test_clean_first_start_may_trade(tmp_path):
    decision = evaluate(tmp_path)
    assert decision.start_bots is True
    assert decision.breaker_tripped is False
    history = tmp_path / "restart_history.json"
    assert history.exists()
    import json
    assert json.loads(history.read_text())["schema_version"] == SCHEMA_VERSION


def test_present_kill_switch_blocks_bots(tmp_path):
    (tmp_path / "KILL_SWITCH_alpha.lock").write_text("tripped earlier")
    decision = evaluate(tmp_path)
    assert decision.start_bots is False
    assert any("kill switch" in r.lower() for r in decision.reasons)


def test_global_kill_switch_also_blocks(tmp_path):
    (tmp_path / "KILL_SWITCH.lock").write_text("tripped")
    assert evaluate(tmp_path).start_bots is False


def test_quarantined_state_file_blocks_bots(tmp_path):
    state_schema.QUARANTINED.append("data/scheduled_trades.json")
    decision = evaluate(tmp_path)
    assert decision.start_bots is False
    assert any("quarantined" in r for r in decision.reasons)


def test_manual_start_gets_the_same_clean_check(tmp_path):
    (tmp_path / "KILL_SWITCH_beta.lock").write_text("tripped")
    decision = evaluate(tmp_path, supervised=False)
    assert decision.start_bots is False, "rule 5 applies to manual boots too"


# ==================== crash-loop breaker ====================

def test_breaker_trips_on_third_supervised_start_within_hour(tmp_path):
    assert evaluate(tmp_path, now=T0).start_bots is True
    assert evaluate(tmp_path, now=T0 + timedelta(minutes=5)).start_bots is True
    third = evaluate(tmp_path, now=T0 + timedelta(minutes=10))
    assert third.breaker_tripped is True
    assert third.start_bots is False
    # Every account's switch is engaged, and the reason names the breaker.
    locks = kill_switch_files(str(tmp_path))
    assert len(locks) == len(ACCOUNTS)
    assert "crash-loop breaker" in (tmp_path / "KILL_SWITCH_alpha.lock").read_text()


def test_breaker_is_sticky_via_the_kill_switches(tmp_path):
    for i in range(BREAKER_THRESHOLD):
        evaluate(tmp_path, now=T0 + timedelta(minutes=i))
    # The next start is outside nothing — switches exist, so still no bots.
    later = evaluate(tmp_path, now=T0 + timedelta(hours=3))
    assert later.start_bots is False
    assert any("kill switch" in r.lower() for r in later.reasons)


def test_manual_restarts_never_trip_the_breaker(tmp_path):
    for i in range(6):
        decision = evaluate(tmp_path, supervised=False,
                            now=T0 + timedelta(minutes=i))
    assert decision.start_bots is True
    assert kill_switch_files(str(tmp_path)) == []


def test_mixed_starts_count_only_supervised_ones(tmp_path):
    evaluate(tmp_path, supervised=True, now=T0)
    for i in range(1, 5):
        evaluate(tmp_path, supervised=False, now=T0 + timedelta(minutes=i))
    second = evaluate(tmp_path, supervised=True, now=T0 + timedelta(minutes=6))
    assert second.breaker_tripped is False, "manual starts must not count"
    third = evaluate(tmp_path, supervised=True, now=T0 + timedelta(minutes=7))
    assert third.breaker_tripped is True


def test_old_supervised_starts_age_out_of_the_window(tmp_path):
    evaluate(tmp_path, now=T0 - timedelta(hours=2))
    evaluate(tmp_path, now=T0 - timedelta(minutes=90))
    recent = evaluate(tmp_path, now=T0)
    assert recent.breaker_tripped is False
    assert recent.start_bots is True


# ==================== the history file is a state file like any other ====================

def test_corrupt_history_quarantines_and_fails_closed(tmp_path):
    (tmp_path / "restart_history.json").write_text('{"starts": [{')
    decision = evaluate(tmp_path)
    # Quarantined per M2 rules...
    assert list(tmp_path.glob("restart_history.json.invalid-*"))
    # ...and a quarantine IS an unclean boot: dashboard-only.
    assert decision.start_bots is False
    # A fresh, versioned history was still written for next time.
    import json
    fresh = json.loads((tmp_path / "restart_history.json").read_text())
    assert fresh["schema_version"] == SCHEMA_VERSION and len(fresh["starts"]) == 1


def test_history_prunes_entries_older_than_a_day(tmp_path):
    record_start(True, now=T0 - timedelta(hours=30), data_dir=str(tmp_path))
    starts = record_start(True, now=T0, data_dir=str(tmp_path))
    assert len(starts) == 1


def test_damaged_history_entries_dropped_quietly(tmp_path):
    import json
    (tmp_path / "restart_history.json").write_text(json.dumps({
        "schema_version": 1,
        "starts": [{"ts": "not-a-time"}, "not-a-dict",
                   {"ts": T0.isoformat(), "supervised": True}],
    }))
    starts = record_start(True, now=T0 + timedelta(minutes=1),
                          data_dir=str(tmp_path))
    assert len(starts) == 2  # the one valid old entry + this start


# ==================== staleness guard ====================

def test_is_stale_truth_table():
    from src.utils.build_info import is_stale
    assert is_stale("a" * 40, "a" * 40) is False
    assert is_stale("a" * 40, "b" * 40) is True
    assert is_stale(None, "b" * 40) is None, "unknown must not raise false alarms"
    assert is_stale("a" * 40, None) is None


def test_running_commit_captured_in_this_repo():
    from src.utils.build_info import RUNNING_COMMIT
    assert RUNNING_COMMIT is not None and len(RUNNING_COMMIT) == 40


def test_staleness_payload_shape():
    from src.utils.build_info import staleness
    p = staleness()
    assert set(p) == {"started_at", "running_commit", "disk_commit", "stale"}
    assert p["stale"] in (True, False, None)
