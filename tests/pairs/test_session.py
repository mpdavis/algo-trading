import math
from datetime import date, timedelta

import pytest

from strategies.pairs.rules import Pair, Params, Side, parse_pairs
from strategies.pairs.session import StateFile, Stored, held_side, run_session

PAIR = Pair("AAA", "BBB")
PARAMS = Params(lookback=30)
TODAY = date(2026, 10, 9)


# Shifts of AAA's last close and the spread z-score each produces.
CHEAP_Y = -0.008  # z -2.8: enter long spread
RICH_Y = 0.014  # z +2.7: enter short spread
STRETCHED = -0.005  # z -2.2: hold a long spread
REVERTED = 0.003  # z  0.0: exit


def history(last_y_shift=REVERTED, sessions=40):
    """BBB drifts up; AAA tracks it at beta 1 with a small wiggle, and the last
    close is pushed by `last_y_shift` (log points) to dislocate the spread."""
    days = [TODAY - timedelta(days=sessions - i) for i in range(sessions)]
    x = {d: 50 * math.exp(0.004 * i) for i, d in enumerate(days)}
    y = {d: 2 * x[d] * math.exp(0.003 * ((-1) ** i)) for i, d in enumerate(days)}
    y[days[-1]] *= math.exp(last_y_shift)
    return {"AAA": y, "BBB": x}


class FakeBroker:
    def __init__(self, closes, positions=None, equity=100_000.0, shortable=True, pending=False):
        self.closes = closes
        self.pos = dict(positions or {})
        self.eq = equity
        self.can_short = shortable
        self.pending = pending
        self.orders = []
        self.closed = []

    def open_orders(self, symbols):
        return self.pending

    def equity(self):
        return self.eq

    def positions(self):
        return dict(self.pos)

    def daily_closes(self, symbols, before, sessions):
        return {s: {d: v for d, v in self.closes[s].items() if d < before} for s in symbols}

    def shortable(self, symbol):
        return self.can_short

    def submit(self, symbol, qty):
        self.orders.append((symbol, qty))

    def close(self, symbol):
        self.closed.append(symbol)


@pytest.fixture
def state(tmp_path):
    return StateFile(tmp_path / "state.json")


def test_enters_long_spread_short_leg_first(state):
    broker = FakeBroker(history(CHEAP_Y))
    assert run_session(broker, [PAIR], PARAMS, state, TODAY)
    assert [(s, q > 0) for s, q in broker.orders] == [("BBB", False), ("AAA", True)]
    # Half the 50k pair budget each side at beta ~1.
    for symbol, qty in broker.orders:
        price = max(broker.closes[symbol].items())[1]
        assert abs(qty) * price == pytest.approx(25_000, rel=0.05)
    last, pairs = state.load()
    assert last == TODAY
    assert pairs[PAIR.name] == Stored(side=Side.LONG, entered=TODAY)


def test_enters_short_spread(state):
    broker = FakeBroker(history(RICH_Y))
    run_session(broker, [PAIR], PARAMS, state, TODAY)
    assert [(s, q > 0) for s, q in broker.orders] == [("AAA", False), ("BBB", True)]


def test_skips_entry_when_short_leg_unborrowable(state):
    broker = FakeBroker(history(CHEAP_Y), shortable=False)
    run_session(broker, [PAIR], PARAMS, state, TODAY)
    assert broker.orders == []
    assert state.load()[1].get(PAIR.name, Stored()).side is Side.FLAT


def test_open_orders_defer_the_session(state):
    broker = FakeBroker(history(CHEAP_Y), pending=True)
    assert not run_session(broker, [PAIR], PARAMS, state, TODAY)
    assert broker.orders == []
    assert not state.path.exists()


def test_exits_both_legs_on_reversion(state):
    state.save(TODAY - timedelta(days=3), {PAIR.name: Stored(side=Side.LONG, entered=TODAY - timedelta(days=3))})
    broker = FakeBroker(history(), positions={"AAA": 100, "BBB": -200})
    run_session(broker, [PAIR], PARAMS, state, TODAY)
    assert broker.closed == ["AAA", "BBB"]
    assert state.load()[1][PAIR.name] == Stored()


def test_time_stop_counts_sessions_since_entry(state):
    closes = history(STRETCHED)
    days = sorted(closes["AAA"])
    entered = days[-PARAMS.max_hold_days - 1]
    state.save(entered, {PAIR.name: Stored(side=Side.LONG, entered=entered)})
    broker = FakeBroker(closes, positions={"AAA": 100, "BBB": -200})
    run_session(broker, [PAIR], PARAMS, state, TODAY)
    assert broker.closed == ["AAA", "BBB"]
    assert state.load()[1][PAIR.name] == Stored(cooldown=True)


def test_unbalanced_legs_are_flattened(state):
    broker = FakeBroker(history(), positions={"AAA": 100})
    run_session(broker, [PAIR], PARAMS, state, TODAY)
    assert broker.closed == ["AAA"]
    assert broker.orders == []
    assert state.load()[1][PAIR.name].cooldown


def test_manual_close_starts_cooldown(state):
    state.save(TODAY - timedelta(days=1), {PAIR.name: Stored(side=Side.SHORT, entered=TODAY - timedelta(days=2))})
    broker = FakeBroker(history(CHEAP_Y))
    run_session(broker, [PAIR], PARAMS, state, TODAY)
    assert broker.orders == []
    assert state.load()[1][PAIR.name].cooldown


def test_adopts_positions_missing_from_state(state):
    broker = FakeBroker(history(STRETCHED), positions={"AAA": 100, "BBB": -200})
    run_session(broker, [PAIR], PARAMS, state, TODAY)
    assert broker.orders == [] and broker.closed == []
    assert state.load()[1][PAIR.name].side is Side.LONG


@pytest.mark.parametrize(
    ("positions", "expected"),
    [({}, Side.FLAT), ({"AAA": 5, "BBB": -3}, Side.LONG), ({"AAA": -5, "BBB": 3}, Side.SHORT),
     ({"AAA": 5}, None), ({"AAA": 5, "BBB": 3}, None)],
)
def test_held_side(positions, expected):
    assert held_side(positions, PAIR) is expected


def test_parse_pairs():
    assert parse_pairs("jbht/knx, UPS/ODFL") == [Pair("JBHT", "KNX"), Pair("UPS", "ODFL")]
    with pytest.raises(ValueError):
        parse_pairs("JBHT/KNX,KNX/ODFL")
    with pytest.raises(ValueError):
        parse_pairs("JBHT")
