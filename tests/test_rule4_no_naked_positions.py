"""Rule 4 (kill switch + naked positions) — the bot can TRIP the kill switch
but has no way to reset it; a tripped switch means no new orders while open
positions keep being managed; and an entry whose protective stop is not
accepted is unwound immediately (no naked positions)."""

import os

from src.engine.safety import KillSwitch
from conftest import MockAlpaca, fresh_rm, good_bars, make_bot


# ---------- KillSwitch semantics ----------

def test_untripped_by_default(tmp_path):
    ks = KillSwitch(str(tmp_path / "k.lock"))
    assert ks.is_tripped() is False


def test_trip_creates_file_with_reason(tmp_path):
    ks = KillSwitch(str(tmp_path / "k.lock"))
    ks.trip("drawdown breach")
    assert ks.is_tripped()
    assert "drawdown breach" in ks.reason()


def test_trip_is_idempotent_and_preserves_first_reason(tmp_path):
    ks = KillSwitch(str(tmp_path / "k.lock"))
    ks.trip("first cause")
    ks.trip("second cause")
    assert "first cause" in ks.reason()
    assert "second cause" not in ks.reason()


def test_killswitch_exposes_no_reset_method(tmp_path):
    resetty = [n for n in dir(KillSwitch)
               if not n.startswith("_")
               and any(w in n.lower() for w in ("reset", "clear", "remove",
                                                "delete", "untrip", "disarm"))]
    # The only reset-ish name is the STRING helper that tells a human what to do.
    assert resetty == ["manual_reset_instructions"]
    ks = KillSwitch(str(tmp_path / "k.lock"))
    ks.trip("cause")
    KillSwitch.manual_reset_instructions(ks.path)   # calling it must not reset
    assert ks.is_tripped()


def test_only_a_human_file_removal_resets(tmp_path):
    ks = KillSwitch(str(tmp_path / "k.lock"))
    ks.trip("cause")
    os.remove(ks.path)   # the documented human action
    assert ks.is_tripped() is False


# ---------- Bot wiring ----------

def test_tripped_switch_blocks_new_orders_but_manages_positions(tmp_path):
    alpaca = MockAlpaca(bars=good_bars())
    bot = make_bot(tmp_path, alpaca, fresh_rm())
    bot.kill_switch.trip("test halt")

    bot._tick()

    assert alpaca.called("get_positions"), "open positions must still be managed"
    assert not alpaca.called("get_account")
    assert not alpaca.called("get_bars")
    assert not alpaca.called("place_limit_order"), "no new orders under a tripped switch"


def test_untripped_bot_places_entry_and_stop(tmp_path):
    """Canary for the harness: the same bot WITH a clear switch does trade."""
    alpaca = MockAlpaca(bars=good_bars())
    rm = fresh_rm()
    bot = make_bot(tmp_path, alpaca, rm)

    bot._tick()

    assert alpaca.called("place_limit_order")
    assert alpaca.called("place_stop_order")
    assert "TEST" in rm.active_positions


def test_rejected_stop_unwinds_the_entry(tmp_path):
    alpaca = MockAlpaca(bars=good_bars())
    alpaca.stop_order_response = {"id": "stop-1", "status": "rejected"}
    rm = fresh_rm()
    bot = make_bot(tmp_path, alpaca, rm)

    bot._evaluate_symbol("TEST", 9000.0, 9000.0)

    assert ("cancel_order", "entry-1") in alpaca.calls
    assert ("close_position", "TEST") in alpaca.calls
    assert "TEST" not in rm.active_positions, "naked position survived"


def test_malformed_stop_response_also_unwinds(tmp_path):
    alpaca = MockAlpaca(bars=good_bars())
    alpaca.stop_order_response = None
    rm = fresh_rm()
    bot = make_bot(tmp_path, alpaca, rm)

    bot._evaluate_symbol("TEST", 9000.0, 9000.0)

    assert ("close_position", "TEST") in alpaca.calls
    assert "TEST" not in rm.active_positions
