from datetime import date
from types import SimpleNamespace

import pandas as pd

from strategies.pairs.strategy import LumibotBroker


class FakeStrategy:
    is_backtesting = True
    portfolio_value = 100_000.0
    broker = SimpleNamespace()

    def __init__(self, positions=(), orders=(), frames=None):
        self._positions = positions
        self._orders = orders
        self._frames = frames or {}
        self.submitted = []
        self.closed = []

    def get_positions(self):
        return list(self._positions)

    def get_orders(self):
        return list(self._orders)

    def get_historical_prices(self, symbol, length, timestep):
        assert timestep == "day"
        return SimpleNamespace(df=self._frames.get(symbol))

    def create_order(self, symbol, quantity, side):
        return (symbol, quantity, side)

    def submit_order(self, order):
        self.submitted.append(order)

    def close_position(self, symbol):
        self.closed.append(symbol)


def position(symbol, qty, asset_type="stock"):
    return SimpleNamespace(asset=SimpleNamespace(symbol=symbol, asset_type=asset_type), quantity=qty)


def test_positions_keep_short_sign_and_skip_non_stock():
    s = FakeStrategy(positions=[position("AAA", 10), position("BBB", -7.0), position("USD", 500, "forex")])
    assert LumibotBroker(s).positions() == {"AAA": 10, "BBB": -7}


def test_daily_closes_drop_the_current_session():
    index = pd.to_datetime(["2026-10-07 16:00", "2026-10-08 16:00", "2026-10-09 16:00"]).tz_localize("America/New_York")
    frame = pd.DataFrame({"close": [10.0, 11.0, 12.0]}, index=index)
    s = FakeStrategy(frames={"AAA": frame})
    closes = LumibotBroker(s).daily_closes(["AAA", "BBB"], before=date(2026, 10, 9), sessions=2)
    assert closes == {"AAA": {date(2026, 10, 7): 10.0, date(2026, 10, 8): 11.0}, "BBB": {}}


def test_submit_opens_shorts_with_sell_short():
    s = FakeStrategy()
    broker = LumibotBroker(s)
    broker.submit("AAA", 5)
    broker.submit("BBB", -3)
    assert s.submitted == [("AAA", 5, "buy"), ("BBB", 3, "sell_short")]


def test_open_orders_only_counts_active_orders_for_our_symbols():
    def order(symbol, active):
        return SimpleNamespace(asset=SimpleNamespace(symbol=symbol), is_active=lambda: active)

    broker = LumibotBroker(FakeStrategy(orders=[order("ZZZ", True), order("AAA", False)]))
    assert not broker.open_orders(["AAA", "BBB"])
    broker = LumibotBroker(FakeStrategy(orders=[order("BBB", True)]))
    assert broker.open_orders(["AAA", "BBB"])


def test_backtests_treat_everything_as_shortable():
    assert LumibotBroker(FakeStrategy()).shortable("AAA")
