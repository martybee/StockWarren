"""Rule 6 — unrecognized broker responses must produce NO NEW ORDERS.
validate_order_response() classifies every response; "unrecognized" trips the
kill switch and halts the tick's remaining entries."""

from src.engine.safety import (
    ACCEPTED_ORDER_STATUSES,
    TERMINAL_BAD_STATUSES,
    validate_order_response,
)
from conftest import MockAlpaca, fresh_rm, good_bars, make_bot


# ---------- Classification ----------

def test_every_accepted_status_is_ok():
    for status in ACCEPTED_ORDER_STATUSES:
        result = validate_order_response({"id": "x", "status": status})
        assert result.ok and result.classification == "ok", status


def test_status_matching_is_case_insensitive():
    assert validate_order_response({"id": "x", "status": "NEW"}).ok


def test_every_terminal_bad_status_is_rejected_not_unrecognized():
    for status in TERMINAL_BAD_STATUSES:
        result = validate_order_response({"id": "x", "status": status})
        assert not result.ok
        assert result.classification == "rejected", status


def test_none_is_unrecognized():
    assert validate_order_response(None).classification == "unrecognized"


def test_non_dict_is_unrecognized():
    assert validate_order_response("filled").classification == "unrecognized"


def test_missing_id_is_unrecognized():
    assert validate_order_response({"status": "new"}).classification == "unrecognized"


def test_empty_id_is_unrecognized():
    assert validate_order_response({"id": "", "status": "new"}).classification == "unrecognized"


def test_missing_status_is_unrecognized():
    assert validate_order_response({"id": "x"}).classification == "unrecognized"


def test_unknown_status_is_unrecognized():
    result = validate_order_response({"id": "x", "status": "banana"})
    assert not result.ok
    assert result.classification == "unrecognized"


# ---------- Wiring: what the bot does with each classification ----------

def test_unrecognized_response_trips_kill_switch_and_halts(tmp_path):
    alpaca = MockAlpaca(bars=good_bars())
    alpaca.limit_order_response = {"id": "entry-1", "status": "quantum_fill"}
    rm = fresh_rm()
    bot = make_bot(tmp_path, alpaca, rm)

    bot._evaluate_symbol("TEST", 9000.0, 9000.0)

    assert bot.kill_switch.is_tripped(), "Rule 6 breach must trip the kill switch"
    assert bot._halt_new_orders is True, "remaining entries this tick must halt"
    assert ("cancel_order", "entry-1") in alpaca.calls, "suspect order must be cancelled"
    assert "TEST" not in rm.active_positions


def test_none_response_trips_kill_switch(tmp_path):
    alpaca = MockAlpaca(bars=good_bars())
    alpaca.limit_order_response = None
    rm = fresh_rm()
    bot = make_bot(tmp_path, alpaca, rm)

    bot._evaluate_symbol("TEST", 9000.0, 9000.0)

    assert bot.kill_switch.is_tripped()
    assert "TEST" not in rm.active_positions


def test_normal_rejection_does_not_trip_the_switch(tmp_path):
    alpaca = MockAlpaca(bars=good_bars())
    alpaca.limit_order_response = {"id": "entry-1", "status": "rejected"}
    rm = fresh_rm()
    bot = make_bot(tmp_path, alpaca, rm)

    bot._evaluate_symbol("TEST", 9000.0, 9000.0)

    assert not bot.kill_switch.is_tripped(), "a predictable rejection is not a Rule 6 breach"
    assert bot._halt_new_orders is False
    assert "TEST" not in rm.active_positions
