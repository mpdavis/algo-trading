import math

import pytest

from pairs_trader.strategy import Action, Fit, PairState, Params, Side, apply, decide, fit_spread, leg_notionals

P = Params()


def fit(z, beta=1.0):
    return Fit(alpha=0.0, beta=beta, z=z)


@pytest.mark.parametrize(
    ("z", "expected"),
    [
        (-2.5, Action.ENTER_LONG),
        (-2.0, Action.ENTER_LONG),
        (2.5, Action.ENTER_SHORT),
        (1.9, Action.HOLD),
        (-1.9, Action.HOLD),
        # Past the stop band the pair already looks broken: never enter.
        (-3.6, Action.HOLD),
        (3.6, Action.HOLD),
    ],
)
def test_flat_entries(z, expected):
    assert decide(PairState(), fit(z), P) is expected


def test_no_entry_with_negative_hedge_ratio():
    assert decide(PairState(), fit(-2.5, beta=-0.4), P) is Action.HOLD


@pytest.mark.parametrize(
    ("side", "z", "held", "expected"),
    [
        (Side.LONG, -0.4, 0, Action.EXIT_REVERT),
        (Side.LONG, -3.5, 0, Action.EXIT_STOP),
        (Side.LONG, -1.5, 20, Action.EXIT_TIME),
        (Side.LONG, -1.5, 19, Action.HOLD),
        (Side.SHORT, 0.4, 0, Action.EXIT_REVERT),
        (Side.SHORT, 3.5, 0, Action.EXIT_STOP),
        (Side.SHORT, 1.5, 20, Action.EXIT_TIME),
        (Side.SHORT, 1.5, 3, Action.HOLD),
    ],
)
def test_exits(side, z, held, expected):
    assert decide(PairState(side=side, held_days=held), fit(z), P) is expected


def test_cooldown_blocks_entry_until_spread_normalizes():
    state = apply(PairState(side=Side.LONG), Action.EXIT_STOP, fit(-3.6), P)
    assert state.cooldown
    assert decide(state, fit(-2.5), P) is Action.HOLD
    state = apply(state, Action.HOLD, fit(-2.5), P)
    assert state.cooldown
    state = apply(state, Action.HOLD, fit(0.3), P)
    assert not state.cooldown
    assert decide(state, fit(-2.5), P) is Action.ENTER_LONG


def test_revert_exit_does_not_cool_down():
    assert apply(PairState(side=Side.SHORT), Action.EXIT_REVERT, fit(0.2), P) == PairState()


def test_holding_counts_days():
    state = apply(PairState(), Action.ENTER_LONG, fit(-2.2), P)
    for _ in range(3):
        state = apply(state, Action.HOLD, fit(-1.5), P)
    assert state == PairState(side=Side.LONG, held_days=3)


def test_leg_notionals_offset_beta():
    y, x = leg_notionals(0.5, 30_000)
    assert y + x == pytest.approx(30_000)
    assert x == pytest.approx(0.5 * y)


def test_fit_recovers_hedge_ratio_and_flags_dislocation():
    log_x = [math.log(100) + 0.01 * i for i in range(60)]
    wiggle = [0.002 * ((-1) ** i) for i in range(60)]
    log_y = [0.3 + 1.5 * lx + w for lx, w in zip(log_x, wiggle)]
    log_y[-1] -= 0.05
    f = fit_spread([math.exp(v) for v in log_y], [math.exp(v) for v in log_x])
    assert f.beta == pytest.approx(1.5, abs=0.05)
    assert f.z < -2


def test_fit_rejects_flat_series():
    assert fit_spread([10.0] * 10, [5.0] * 10) is None
