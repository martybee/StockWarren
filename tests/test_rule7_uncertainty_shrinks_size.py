"""Rule 7 — any uncertainty reduces the position or produces no trade.
compute_uncertainty_factor() maps signal quality to [0, 1]; below
MIN_SIZE_FACTOR the trade is dropped entirely (factor 0)."""

from src.engine.safety import MIN_SIZE_FACTOR, compute_uncertainty_factor


def factor(strength=90.0, min_strength=65.0, confirmations=4,
           min_confirmations=2, rr_ratio=4.0, min_rr_ratio=2.0, **kw):
    return compute_uncertainty_factor(
        strength=strength, min_strength=min_strength,
        confirmations=confirmations, min_confirmations=min_confirmations,
        rr_ratio=rr_ratio, min_rr_ratio=min_rr_ratio, **kw)


def test_below_min_strength_is_no_trade():
    assert factor(strength=64.9) == 0.0


def test_below_min_confirmations_is_no_trade():
    assert factor(confirmations=1) == 0.0


def test_below_min_risk_reward_is_no_trade():
    assert factor(rr_ratio=1.9) == 0.0


def test_marginal_everything_collapses_below_floor_to_zero():
    # Exactly at every threshold: 0.5 * 0.6 * 0.7 = 0.21 < MIN_SIZE_FACTOR -> 0.
    assert factor(strength=65.0, confirmations=2, rr_ratio=2.0) == 0.0


def test_strong_everything_is_full_size():
    assert factor(strength=100.0, confirmations=4, rr_ratio=4.0) == 1.0


def test_factor_never_exceeds_one():
    assert factor(strength=500.0, confirmations=50, rr_ratio=100.0) == 1.0


def test_weaker_signal_never_gets_a_larger_position():
    weak = factor(strength=75.0, confirmations=3, rr_ratio=2.5)
    strong = factor(strength=100.0, confirmations=4, rr_ratio=4.0)
    assert weak <= strong


def test_trained_ml_confidence_scales_size_down():
    # Full-size signal (factor 1.0) halved by a 50%-confidence trained model.
    kw = dict(strength=100.0, confirmations=4, rr_ratio=4.0)
    assert factor(**kw) == 1.0
    assert factor(ml_confidence=50.0, ml_trained=True, **kw) == 0.5


def test_untrained_ml_confidence_is_ignored():
    assert factor(ml_confidence=1.0, ml_trained=False) == factor()


def test_output_is_zero_or_between_floor_and_one():
    """The Rule 7 contract itself: every output is 0 (no trade) or a usable
    size factor in [MIN_SIZE_FACTOR, 1]."""
    for s in (65.0, 70.0, 80.0, 90.0, 100.0):
        for c in (2, 3, 4, 6):
            for rr in (2.0, 2.5, 3.0, 5.0):
                f = factor(strength=s, confirmations=c, rr_ratio=rr)
                assert f == 0.0 or MIN_SIZE_FACTOR <= f <= 1.0, (s, c, rr, f)
