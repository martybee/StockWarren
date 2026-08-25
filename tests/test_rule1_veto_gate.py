"""Rule 1 — the strategy may PROPOSE trades; RiskManager.approve_order() can
always REJECT them. Every rejection returns qty == 0; every approval may shrink
but never grow the proposed size. Fail-closed throughout."""

import pytest

from conftest import fresh_rm, EQUITY


APPROVE = dict(
    symbol="TEST", side="buy", qty=112, price=10.0,
    stop_price=9.8, target_price=10.4,
    portfolio_value=EQUITY, cash=EQUITY, uncertainty_factor=0.6,
)


def approve(rm, **kw):
    args = dict(APPROVE)
    args.update(kw)
    return rm.approve_order(**args)


def test_happy_path_approves_and_scales_by_uncertainty(rm):
    ok, reason, qty = approve(rm)
    assert ok
    assert qty == int(112 * 0.6)  # uncertainty shrank the size


def test_rejection_always_returns_qty_zero(rm):
    ok, _, qty = approve(rm, qty=-5)
    assert not ok and qty == 0


def test_non_positive_qty_rejected(rm):
    assert approve(rm, qty=0)[0] is False
    assert approve(rm, qty=-1)[0] is False


def test_non_positive_price_rejected(rm):
    assert approve(rm, price=0.0)[0] is False
    assert approve(rm, price=-10.0)[0] is False


def test_blocked_gate_rejects_daily_loss(rm):
    rm.stats.daily_pnl = -250.0  # beyond max_daily_loss 200
    ok, reason, qty = approve(rm)
    assert not ok and qty == 0
    assert "loss" in reason.lower()


def test_no_reentry_into_held_symbol(rm):
    rm.register_position("TEST", "buy", 10, 10.0, 9.8, 10.4)
    ok, reason, _ = approve(rm)
    assert not ok
    assert "already holding" in reason


def test_one_position_per_correlation_group():
    rm = fresh_rm(correlation_groups={"ev_auto": "F,NIO,RIVN"})
    rm.register_position("F", "buy", 10, 10.0, 9.8, 10.4)
    ok, reason, _ = approve(rm, symbol="NIO")
    assert not ok
    assert "correlated" in reason


def test_long_stop_must_be_below_entry(rm):
    ok, reason, _ = approve(rm, stop_price=10.2)
    assert not ok
    assert "stop" in reason.lower()


def test_short_stop_must_be_above_entry(rm):
    ok, reason, _ = approve(rm, side="sell", stop_price=9.8, target_price=9.4)
    assert not ok
    assert "stop" in reason.lower()


def test_poor_risk_reward_rejected(rm):
    # reward 0.3 / risk 0.2 = 1.5 < min 2.0
    ok, reason, _ = approve(rm, target_price=10.3)
    assert not ok
    assert "R:R" in reason


def test_zero_uncertainty_rejected(rm):
    ok, reason, _ = approve(rm, uncertainty_factor=0.0)
    assert not ok


def test_size_rounding_to_zero_rejected(rm):
    ok, reason, _ = approve(rm, qty=1, uncertainty_factor=0.4)
    assert not ok
    assert "zero" in reason.lower()


def test_leverage_capped_at_cash(rm):
    # 50 shares * $10 = $500 notional, but only $100 cash: 1x max => 10 shares.
    ok, _, qty = approve(rm, qty=50, cash=100.0, uncertainty_factor=1.0)
    assert ok
    assert qty == 10
    assert qty * 10.0 <= 100.0  # notional never exceeds cash (MAX_LEVERAGE 1.0)


def test_insufficient_cash_for_one_share_rejected(rm):
    ok, reason, qty = approve(rm, cash=5.0, uncertainty_factor=1.0)
    assert not ok and qty == 0
    assert "buying power" in reason


def test_oversize_proposal_is_trimmed_through_every_cap(rm):
    # 5000 shares -> cash cap 900 -> position cap 360 -> per-trade risk cap 225.
    ok, _, qty = approve(rm, qty=5000, uncertainty_factor=1.0)
    assert ok
    assert qty == 225
    risk = abs(APPROVE["price"] - APPROVE["stop_price"]) * qty
    assert risk <= EQUITY * (rm.max_risk_per_trade_pct / 100.0) + 1e-9


def test_combined_open_risk_cap_rejects_when_full(rm):
    # An existing position already consumes the entire 1% ($90) open-risk budget.
    rm.register_position("OTHER", "buy", 450, 10.0, 9.8, 10.4)
    ok, reason, qty = approve(rm)
    assert not ok and qty == 0
    assert "combined open-risk" in reason


def test_approval_never_grows_the_proposed_qty(rm):
    ok, _, qty = approve(rm, qty=10, uncertainty_factor=1.0)
    assert ok
    assert qty <= 10
