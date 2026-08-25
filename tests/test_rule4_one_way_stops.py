"""Rule 4 (stops) — the one-way trailing stop can ONLY tighten, NEVER loosen.
The single most important invariant in the codebase (FutureWarren heritage).
See risk_manager.py:update_trailing_stop."""

from conftest import fresh_rm


def test_unknown_symbol_returns_none(rm):
    assert rm.update_trailing_stop("NOPE", 100.0) is None


def test_no_trailing_before_activation_threshold(rm):
    rm.register_position("TEST", "buy", 10, 100.0, 98.0, 104.0)
    # +1% profit < 2% activation threshold: stop must not move.
    rm.update_trailing_stop("TEST", 101.0)
    pos = rm.active_positions["TEST"]
    assert pos.trailing_stop_active is False
    assert pos.stop_price == 98.0


def test_long_stop_only_ever_tightens(rm):
    rm.register_position("TEST", "buy", 10, 100.0, 98.0, 104.0)
    pos = rm.active_positions["TEST"]
    prev = pos.stop_price
    # Rally, retrace, rally higher, crash: the stop must be non-decreasing.
    for price in (103.0, 102.0, 100.0, 105.0, 104.0, 110.0, 90.0):
        rm.update_trailing_stop("TEST", price)
        assert pos.stop_price >= prev, (
            f"stop LOOSENED from {prev} to {pos.stop_price} at price {price}"
        )
        prev = pos.stop_price
    # It did actually trail up at some point.
    assert pos.stop_price > 98.0


def test_long_retrace_does_not_move_stop_down(rm):
    rm.register_position("TEST", "buy", 10, 100.0, 98.0, 104.0)
    pos = rm.active_positions["TEST"]
    rm.update_trailing_stop("TEST", 105.0)          # activates + tightens
    tightened = pos.stop_price
    assert tightened > 98.0
    rm.update_trailing_stop("TEST", 100.0)          # deep retrace
    assert pos.stop_price == tightened              # unchanged, not loosened


def test_short_stop_only_ever_tightens_downward(rm):
    rm.register_position("TEST", "sell", 10, 100.0, 102.0, 96.0)
    pos = rm.active_positions["TEST"]
    prev = pos.stop_price
    for price in (97.0, 98.0, 100.0, 95.0, 96.0, 90.0, 110.0):
        rm.update_trailing_stop("TEST", price)
        assert pos.stop_price <= prev, (
            f"short stop LOOSENED from {prev} to {pos.stop_price} at price {price}"
        )
        prev = pos.stop_price
    assert pos.stop_price < 102.0


def test_trailing_never_lowers_an_externally_tightened_long_stop(rm):
    """The one-way guard's real job: if the stop is ALREADY tighter than the
    trail formula says (ATR stop, manual tightening, any future stop source),
    the trailing logic must leave it alone — never drag it back down.
    This is the test that dies if `>` is weakened to `!=` or `>=` logic drifts."""
    rm.register_position("TEST", "buy", 10, 100.0, 98.0, 104.0)
    pos = rm.active_positions["TEST"]
    rm.update_trailing_stop("TEST", 105.0)      # activates; stop -> 103.95
    pos.stop_price = 104.5                      # tightened further out-of-band
    result = rm.update_trailing_stop("TEST", 105.0)  # trail formula says 103.95
    assert result is None
    assert pos.stop_price == 104.5, "trailing logic LOOSENED a tighter stop"


def test_trailing_never_raises_an_externally_tightened_short_stop(rm):
    rm.register_position("TEST", "sell", 10, 100.0, 102.0, 96.0)
    pos = rm.active_positions["TEST"]
    rm.update_trailing_stop("TEST", 95.0)       # activates; stop -> 95.95
    pos.stop_price = 95.5                       # tighter (lower) for a short
    result = rm.update_trailing_stop("TEST", 95.0)   # formula says 95.95
    assert result is None
    assert pos.stop_price == 95.5, "trailing logic LOOSENED a tighter short stop"


def test_returned_value_matches_position_state(rm):
    rm.register_position("TEST", "buy", 10, 100.0, 98.0, 104.0)
    new_stop = rm.update_trailing_stop("TEST", 105.0)
    assert new_stop is not None
    assert rm.active_positions["TEST"].stop_price == new_stop
    # No movement => returns None, state untouched.
    assert rm.update_trailing_stop("TEST", 104.0) is None
    assert rm.active_positions["TEST"].stop_price == new_stop
