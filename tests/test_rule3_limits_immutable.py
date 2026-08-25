"""Rule 3 — the strategy cannot change position limits, loss limits, or
leverage. Limits live in a frozen record behind read-only properties; only a
human operator may retune them, clamped to hard ceilings; leverage is a
constant 1.0 and is never overridable."""

from dataclasses import FrozenInstanceError

import pytest

from src.engine.risk_manager import RiskManager
from src.engine.safety import MAX_LEVERAGE
from conftest import fresh_rm


def test_leverage_is_one_cash_only():
    assert MAX_LEVERAGE == 1.0
    assert fresh_rm().max_leverage == 1.0


def test_limits_record_is_frozen(rm):
    with pytest.raises(FrozenInstanceError):
        rm._limits.max_positions = 99


def test_limit_properties_have_no_setters(rm):
    for prop in ("max_positions", "max_daily_loss", "max_position_pct",
                 "risk_per_trade_pct", "max_leverage", "shutdown_drawdown_pct"):
        with pytest.raises(AttributeError):
            setattr(rm, prop, 999)


def test_leverage_is_not_operator_overridable(rm):
    assert all("leverage" not in k for k in RiskManager.OVERRIDABLE)
    result = rm.apply_operator_override({"max_leverage": 10})
    assert result["applied"] == {}
    assert rm.max_leverage == 1.0


def test_operator_override_clamps_to_hard_ceilings(rm):
    result = rm.apply_operator_override({"max_positions": 999,
                                         "risk_per_trade_pct": 50.0,
                                         "shutdown_drawdown_pct": 100.0})
    assert result["applied"]["max_positions"] == 20      # hard max
    assert result["applied"]["risk_per_trade_pct"] == 5.0
    assert result["applied"]["shutdown_drawdown_pct"] == 50.0
    assert rm.max_positions == 20


def test_operator_override_clamps_at_the_floor_too(rm):
    result = rm.apply_operator_override({"max_positions": 0})
    assert result["applied"]["max_positions"] == 1


def test_unknown_and_non_numeric_overrides_ignored(rm):
    before = rm.get_limits()
    result = rm.apply_operator_override({"banana": 12, "max_positions": "lots"})
    assert result["applied"] == {}
    assert any("max_positions" in n for n in result["notes"])
    assert rm.get_limits() == before


def test_reset_restores_config_baseline(rm):
    baseline = rm.get_limits()
    rm.apply_operator_override({"max_positions": 7, "max_daily_loss_pct": 4.0})
    assert rm.get_limits() != baseline
    rm.reset_operator_override()
    assert rm.get_limits() == baseline
