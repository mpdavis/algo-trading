from datetime import date, datetime

import pytest

from strategies.orb.rules import Bar, Params, Side
from strategies.orb.session import Day, Holding, Order, State, StateFile, Trade, step

P = Params()
DAY = date(2026, 10, 13)


def at(hhmm, day=DAY):
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m)


def minute(hhmm, low, high, close):
    return Bar(at(hhmm), close, high, low, close)


# The first five minutes span 99.50-101.00; then a quiet minute inside it.
RANGE = [
    minute("09:30", 99.8, 100.6, 100.2),
    minute("09:31", 99.5, 100.4, 100.0),
    minute("09:32", 99.9, 101.0, 100.7),
    minute("09:33", 100.1, 100.8, 100.4),
    minute("09:34", 100.0, 100.6, 100.2),
    minute("09:35", 100.0, 100.5, 100.3),
]


class FakeBroker:
    def __init__(self, bars, held=None, orders=(), equity=100_000.0, shortable=True):
        self.bars = list(bars)
        self.held = held
        self.working = list(orders)
        self.eq = equity
        self.can_short = shortable
        self.entries = []
        self.stops = []
        self.cancelled = []
        self.closed = []

    def equity(self):
        return self.eq

    def position(self, symbol):
        assert symbol == "SPY"
        return self.held

    def orders(self, symbol):
        return [o for o in self.working if o.symbol == symbol]

    def minute_bars(self, symbol, count):
        return self.bars[-count:]

    def shortable(self, symbol):
        return self.can_short

    def market(self, symbol, qty):
        self.entries.append((symbol, qty))

    def place_stop(self, symbol, qty, stop):
        self.stops.append((symbol, qty, stop))

    def cancel(self, order):
        self.cancelled.append(order.id)
        self.working.remove(order)

    def close(self, symbol):
        self.closed.append(symbol)


@pytest.fixture
def state(tmp_path):
    return StateFile(tmp_path / "state.json")


def stop_order(qty, price, side="sell", id="s1"):
    return Order(id, "SPY", side, "stop", qty, price)


def test_waits_for_the_opening_range(state):
    broker = FakeBroker(RANGE[:3])
    step(broker, P, state, at("09:33"))
    today = state.load().today
    assert (today.range, today.note) == (None, "waiting for the opening range")
    assert broker.entries == []


def test_records_the_range_and_waits_inside_it(state):
    broker = FakeBroker(RANGE)
    step(broker, P, state, at("09:36"))
    today = state.load().today
    assert (today.high, today.low) == (101.0, 99.5)
    assert today.note == "inside the range at 100.30"
    assert broker.entries == []


def test_ignores_the_bar_still_forming(state):
    # 09:36 has not closed at 09:36:30; its breakout counts only at 09:37.
    broker = FakeBroker(RANGE + [minute("09:36", 100.5, 102, 101.8)])
    step(broker, P, state, at("09:36").replace(second=30))
    assert broker.entries == []
    step(broker, P, state, at("09:37"))
    assert broker.entries == [("SPY", 245)]


def test_goes_long_on_a_close_above_the_range(state):
    broker = FakeBroker(RANGE + [minute("09:36", 100.5, 102, 101.8)])
    step(broker, P, state, at("09:37"))
    # 1% of $100,000 over 101.80-99.50 is 434 shares; 25% of equity caps it at 245.
    assert broker.entries == [("SPY", 245)]
    trade = state.load().today.trade
    assert (trade.side, trade.qty, trade.stop, trade.entered_at) == (Side.LONG, 245, 99.5, "09:37")


def test_goes_short_on_a_close_below_the_range(state):
    broker = FakeBroker(RANGE + [minute("09:36", 98.5, 100, 98.9)], equity=20_000)
    step(broker, P, state, at("09:37"))
    # 1% of $20,000 over 101.00-98.90 is 95 shares; 25% of equity caps it at 50.
    assert broker.entries == [("SPY", -50)]
    assert state.load().today.trade.side is Side.SHORT


def test_sizing_by_risk_when_the_range_is_wide(state):
    wide = [minute(f"09:3{i}", 95, 105, 100) for i in range(5)] + [minute("09:35", 105, 106, 105.5)]
    broker = FakeBroker(wide)
    step(broker, P, state, at("09:36"))
    # $1,000 over 105.50-95.00 is 95 shares.
    assert broker.entries == [("SPY", 95)]


def test_no_short_when_hard_to_borrow(state):
    broker = FakeBroker(RANGE + [minute("09:36", 98.5, 100, 98.9)], shortable=False)
    step(broker, P, state, at("09:37"))
    assert broker.entries == []
    today = state.load().today
    assert today.done and "not easy to borrow" in today.note


def test_no_entry_after_the_last_entry_time(state):
    broker = FakeBroker(RANGE + [minute("15:00", 100.5, 102, 101.8)])
    step(broker, P, state, at("15:01"))
    assert broker.entries == []
    assert state.load().today.done


def test_one_trade_a_day(state):
    state.save(State(today=Day(DAY, 101.0, 99.5, Trade(Side.LONG, 245, 99.5, "09:37", 101.8, 99.5, "stop"),
                               done=True)))
    broker = FakeBroker(RANGE + [minute("10:00", 100.5, 103, 102.5)])
    step(broker, P, state, at("10:01"))
    assert broker.entries == []


def test_waits_while_the_entry_is_working(state):
    state.save(State(today=Day(DAY, 101.0, 99.5, Trade(Side.LONG, 245, 99.5, "09:37"))))
    broker = FakeBroker(RANGE, orders=[Order("e1", "SPY", "buy", "market", 245)])
    step(broker, P, state, at("09:38"))
    assert (broker.entries, broker.stops) == ([], [])
    assert state.load().today.note == "waiting for an order to fill"


def test_places_the_stop_once_filled_and_records_the_fill(state):
    state.save(State(today=Day(DAY, 101.0, 99.5, Trade(Side.LONG, 245, 99.5, "09:37"))))
    broker = FakeBroker(RANGE, held=Holding(245, 101.82))
    step(broker, P, state, at("09:38"))
    assert broker.stops == [("SPY", 245, 99.5)]
    assert state.load().today.trade.entry == 101.82


def test_short_stop_is_a_buy_above_the_range(state):
    state.save(State(today=Day(DAY, 101.0, 99.5, Trade(Side.SHORT, 50, 101.0, "09:37"))))
    broker = FakeBroker(RANGE, held=Holding(-50, 98.9))
    step(broker, P, state, at("09:38"))
    assert broker.stops == [("SPY", -50, 101.0)]


def test_keeps_a_matching_stop(state):
    state.save(State(today=Day(DAY, 101.0, 99.5, Trade(Side.LONG, 245, 99.5, "09:37", 101.82))))
    broker = FakeBroker(RANGE, held=Holding(245, 101.82), orders=[stop_order(245, 99.5)])
    step(broker, P, state, at("11:00"))
    assert (broker.stops, broker.cancelled) == ([], [])


def test_replaces_a_wrong_stop(state):
    state.save(State(today=Day(DAY, 101.0, 99.5, Trade(Side.LONG, 245, 99.5, "09:37", 101.82))))
    broker = FakeBroker(RANGE, held=Holding(245, 101.82), orders=[stop_order(100, 99.5)])
    step(broker, P, state, at("11:00"))
    assert broker.cancelled == ["s1"]
    assert broker.stops == [("SPY", 245, 99.5)]


def test_a_stopped_out_trade_ends_the_day(state):
    state.save(State(today=Day(DAY, 101.0, 99.5, Trade(Side.LONG, 245, 99.5, "09:37", 101.82))))
    broker = FakeBroker(RANGE + [minute("11:00", 102, 103, 102.5)])
    step(broker, P, state, at("11:01"))
    today = state.load().today
    assert (today.trade.reason, today.trade.exit, today.done) == ("stop", 99.5, True)
    assert broker.entries == []


def test_an_entry_that_never_filled_ends_the_day(state):
    state.save(State(today=Day(DAY, 101.0, 99.5, Trade(Side.LONG, 245, 99.5, "09:37"))))
    broker = FakeBroker(RANGE)
    step(broker, P, state, at("09:40"))
    today = state.load().today
    assert (today.trade.reason, today.trade.exit, today.done) == ("unfilled", None, True)


def test_cancels_a_stop_left_without_a_position(state):
    state.save(State(today=Day(DAY, 101.0, 99.5, Trade(Side.LONG, 245, 99.5, "09:37", 101.82))))
    broker = FakeBroker(RANGE, orders=[stop_order(245, 99.5)])
    step(broker, P, state, at("11:00"))
    assert broker.cancelled == ["s1"]


def test_flattens_before_the_close(state):
    state.save(State(today=Day(DAY, 101.0, 99.5, Trade(Side.LONG, 245, 99.5, "09:37", 101.82))))
    broker = FakeBroker(RANGE + [minute("15:49", 102, 103, 102.6)], held=Holding(245, 101.82),
                        orders=[stop_order(245, 99.5)])
    step(broker, P, state, at("15:50"))
    assert broker.cancelled == ["s1"]
    assert broker.closed == ["SPY"]
    today = state.load().today
    assert (today.trade.reason, today.trade.exit, today.done) == ("flatten", 102.6, True)


def test_flattens_whenever_the_market_is_closing(state):
    state.save(State(today=Day(DAY, 101.0, 99.5, Trade(Side.LONG, 245, 99.5, "09:37", 101.82))))
    broker = FakeBroker(RANGE, held=Holding(245, 101.82), orders=[stop_order(245, 99.5)])
    step(broker, Params(flatten_at="15:59"), state, at("15:54"), closing=True)
    assert broker.closed == ["SPY"]
    assert state.load().today.trade.reason == "flatten"


def test_closing_without_a_position_ends_the_day(state):
    broker = FakeBroker(RANGE + [minute("12:00", 100.5, 102, 101.8)])
    step(broker, P, state, at("12:01"), closing=True)
    assert broker.entries == []
    assert state.load().today.done


def test_closes_a_position_that_is_not_todays_trade(state):
    broker = FakeBroker(RANGE, held=Holding(10, 400.0), orders=[stop_order(10, 390)])
    step(broker, P, state, at("09:31"))
    assert broker.cancelled == ["s1"]
    assert broker.closed == ["SPY"]
    assert broker.entries == []


def test_closes_a_position_the_wrong_way_round(state):
    state.save(State(today=Day(DAY, 101.0, 99.5, Trade(Side.LONG, 245, 99.5, "09:37", 101.82))))
    broker = FakeBroker(RANGE, held=Holding(-245, 101.0))
    step(broker, P, state, at("10:00"))
    assert broker.closed == ["SPY"]
    assert state.load().today.trade.reason == "mismatch"


def test_does_nothing_before_the_open(state):
    broker = FakeBroker([], held=Holding(10, 400.0))
    step(broker, P, state, at("09:00"))
    assert broker.closed == []


def test_a_new_day_moves_yesterdays_trade_to_history(state):
    yesterday = Day(date(2026, 10, 12), 101.0, 99.5, Trade(Side.LONG, 245, 99.5, "09:37", 101.82, 102.6, "flatten"),
                    done=True)
    state.save(State(today=yesterday))
    step(FakeBroker(RANGE[:2]), P, state, at("09:32"))
    s = state.load()
    assert s.today.day == DAY
    assert s.history == [yesterday]


def test_a_day_without_a_trade_is_not_kept(state):
    state.save(State(today=Day(date(2026, 10, 12), 101.0, 99.5, done=True)))
    step(FakeBroker(RANGE[:2]), P, state, at("09:32"))
    assert state.load().history == []


def test_state_round_trips(state):
    s = State(today=Day(DAY, 101.0, 99.5, Trade(Side.SHORT, 50, 101.0, "09:37", 98.9), note="holding"),
              history=[Day(date(2026, 10, 12), 1.0, 0.5, Trade(Side.LONG, 1, 0.5, "10:00", 1.0, 0.5, "stop"), True)])
    state.save(s)
    assert state.load() == s
