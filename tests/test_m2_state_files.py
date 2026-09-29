"""Engine M2 — state-file validation is fail-closed (Rule 5 applied to our
OWN files). Structural damage quarantines the file (never overwrites the
evidence) and falls back to the safe default: empty scheduler state, or
config-baseline risk limits. Record/value-level damage is dropped loudly
while intact data still loads."""

import json
import pathlib

from src.utils.overrides import load_overrides, save_overrides
from src.utils.state_schema import SCHEMA_VERSION
from src.engine.scheduler import TradeScheduler
from conftest import fresh_rm


def invalid_siblings(path: pathlib.Path) -> list:
    return sorted(path.parent.glob(path.name + ".invalid-*"))


# ==================== operator overrides ====================

def test_save_then_load_round_trip(tmp_path):
    path = tmp_path / "ov.json"
    save_overrides({"max_positions": 3, "risk_per_trade_pct": 0.3}, str(path))
    on_disk = json.loads(path.read_text())
    assert on_disk["schema_version"] == SCHEMA_VERSION
    loaded = load_overrides(str(path))
    assert loaded == {"max_positions": 3, "risk_per_trade_pct": 0.3}
    assert "schema_version" not in loaded


def test_legacy_v0_overrides_load_and_upgrade(tmp_path):
    path = tmp_path / "ov.json"
    path.write_text('{"risk_per_trade_pct": 0.25}')   # today's prod shape
    assert load_overrides(str(path)) == {"risk_per_trade_pct": 0.25}
    save_overrides(load_overrides(str(path)), str(path))
    assert json.loads(path.read_text())["schema_version"] == SCHEMA_VERSION


def test_truncated_overrides_quarantined(tmp_path):
    path = tmp_path / "ov.json"
    path.write_text('{"max_po')                        # truncated mid-write
    assert load_overrides(str(path)) == {}             # config baseline
    assert not path.exists(), "corrupt file left in place to be overwritten"
    assert len(invalid_siblings(path)) == 1, "evidence not preserved"


def test_wrong_top_level_overrides_quarantined(tmp_path):
    path = tmp_path / "ov.json"
    path.write_text('[1, 2, 3]')
    assert load_overrides(str(path)) == {}
    assert len(invalid_siblings(path)) == 1


def test_future_schema_version_quarantined(tmp_path):
    path = tmp_path / "ov.json"
    path.write_text(json.dumps({"schema_version": 99, "max_positions": 3}))
    assert load_overrides(str(path)) == {}
    assert len(invalid_siblings(path)) == 1


def test_non_numeric_value_dropped_others_kept(tmp_path):
    path = tmp_path / "ov.json"
    path.write_text(json.dumps({"max_positions": "lots",
                                "risk_per_trade_pct": 0.3}))
    assert load_overrides(str(path)) == {"risk_per_trade_pct": 0.3}
    assert path.exists(), "value-level damage must not quarantine the file"


def test_boolean_value_is_not_a_limit(tmp_path):
    path = tmp_path / "ov.json"
    path.write_text(json.dumps({"max_positions": True}))
    assert load_overrides(str(path)) == {}


def test_above_ceiling_value_clamped_on_load(tmp_path):
    """Acceptance criterion: hand-edit a value above its hard ceiling ->
    clamped when the loaded overrides are applied at startup."""
    path = tmp_path / "ov.json"
    path.write_text(json.dumps({"risk_per_trade_pct": 50.0}))
    rm = fresh_rm()
    rm.apply_operator_override(load_overrides(str(path)))
    assert rm.risk_per_trade_pct == 5.0                # OVERRIDABLE hard max


# ==================== scheduled trades ====================

GOOD_TRADE = {
    "id": "ST-0001", "symbol": "F", "side": "buy", "qty": 5.0,
    "order_type": "market", "limit_price": None, "stop_loss_pct": None,
    "take_profit_pct": None, "scheduled_time": "2099-01-04T10:00:00",
    "status": "pending", "created_at": "2026-04-22T08:24:42",
    "executed_at": "", "result_order_id": "", "error_message": "", "notes": "",
}


def write_state(tmp_path, payload) -> pathlib.Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    path = data_dir / "scheduled_trades.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return path


def make_scheduler(monkeypatch, tmp_path) -> TradeScheduler:
    monkeypatch.chdir(tmp_path)
    return TradeScheduler(None)


def test_missing_file_starts_empty(monkeypatch, tmp_path):
    s = make_scheduler(monkeypatch, tmp_path)
    assert s.scheduled_trades == [] and s.history == []


def test_truncated_file_quarantined_then_fresh_save(monkeypatch, tmp_path):
    """Acceptance criterion: truncate the file mid-write -> the scheduler
    starts, fires NOTHING, quarantines the evidence, and the next save writes
    a clean versioned file."""
    path = write_state(tmp_path, '{"next_id": 4, "pending": [{"id": "ST-')
    s = make_scheduler(monkeypatch, tmp_path)
    assert s.scheduled_trades == [], "no trade may fire off an unreadable file"
    assert not path.exists()
    assert len(invalid_siblings(path)) == 1
    s._save_trades()
    assert json.loads(path.read_text())["schema_version"] == SCHEMA_VERSION


def test_todays_v0_file_loads_and_upgrades(monkeypatch, tmp_path):
    path = write_state(tmp_path, {
        "next_id": 2, "pending": [dict(GOOD_TRADE)],
        "history": [dict(GOOD_TRADE, id="ST-0000", status="cancelled")],
    })
    s = make_scheduler(monkeypatch, tmp_path)
    assert [t.id for t in s.scheduled_trades] == ["ST-0001"]
    assert [t.id for t in s.history] == ["ST-0000"]
    s._save_trades()
    assert json.loads(path.read_text())["schema_version"] == SCHEMA_VERSION


def test_future_schema_version_file_quarantined(monkeypatch, tmp_path):
    path = write_state(tmp_path, {"schema_version": 99, "next_id": 2,
                                  "pending": [dict(GOOD_TRADE)], "history": []})
    s = make_scheduler(monkeypatch, tmp_path)
    assert s.scheduled_trades == []
    assert len(invalid_siblings(path)) == 1


def test_wrong_shape_next_id_quarantined(monkeypatch, tmp_path):
    path = write_state(tmp_path, {"next_id": "four", "pending": [], "history": []})
    make_scheduler(monkeypatch, tmp_path)
    assert len(invalid_siblings(path)) == 1


def test_unknown_field_tolerated_bad_record_skipped(monkeypatch, tmp_path):
    """One damaged record must not take down the load (the old code's
    ScheduledTrade(**t) TypeError killed everything). Unknown fields are
    dropped with a warning; a record with a garbage qty is skipped."""
    write_state(tmp_path, {
        "next_id": 4,
        "pending": [
            dict(GOOD_TRADE, id="ST-0001", surprise_field="from-the-future"),
            dict(GOOD_TRADE, id="ST-0002", qty="lots"),
            dict(GOOD_TRADE, id="ST-0003"),
        ],
        "history": [dict(GOOD_TRADE, id="ST-0000", status="exploded")],
    })
    s = make_scheduler(monkeypatch, tmp_path)
    assert [t.id for t in s.scheduled_trades] == ["ST-0001", "ST-0003"]
    assert s.history == []                             # bad status skipped


def test_non_positive_qty_never_becomes_fireable(monkeypatch, tmp_path):
    write_state(tmp_path, {"next_id": 2,
                           "pending": [dict(GOOD_TRADE, qty=0)], "history": []})
    s = make_scheduler(monkeypatch, tmp_path)
    assert s.scheduled_trades == []


def test_stale_pending_trade_not_reloaded(monkeypatch, tmp_path):
    write_state(tmp_path, {
        "next_id": 2,
        "pending": [dict(GOOD_TRADE, scheduled_time="2020-01-06T10:00:00")],
        "history": [],
    })
    s = make_scheduler(monkeypatch, tmp_path)
    assert s.scheduled_trades == []
