from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from strategies.orb.rules import Bar, Params
from strategies.orb.session import Holding
from strategies.orb.strategy import LumibotBroker, OrbStrategy, exchange_time, rules_from


class FakeStrategy:
    is_backtesting = True
    portfolio_value = 100_000.0

    def __init__(self, positions=(), orders=(), frame=None):
        self._positions = positions
        self._orders = orders
        self._frame = frame
        self.asked = []
        self.submitted = []
        self.cancelled = []
        self.closed = []

    def get_positions(self):
        return list(self._positions)

    def get_orders(self):
        return list(self._orders)

    def get_historical_prices(self, symbol, length, timestep):
        self.asked.append((symbol, length, timestep))
        return SimpleNamespace(df=self._frame)

    def create_order(self, symbol, quantity, side, **kw):
        return (symbol, quantity, side, kw)

    def submit_order(self, order):
        self.submitted.append(order)

    def cancel_order(self, order):
        self.cancelled.append(order)

    def close_position(self, symbol):
        self.closed.append(symbol)


def position(symbol, qty, price, asset_type="stock"):
    return SimpleNamespace(asset=SimpleNamespace(symbol=symbol, asset_type=asset_type), quantity=qty,
                           avg_fill_price=price)


def order(symbol, side, kind, qty, stop=None, active=True, id="o1"):
    return SimpleNamespace(asset=SimpleNamespace(symbol=symbol), side=side, order_type=kind, quantity=qty,
                           stop_price=stop, limit_price=None, identifier=id, is_active=lambda: active)


def test_position_is_signed_and_only_for_the_symbol():
    s = FakeStrategy(positions=[position("QQQ", 5, 400), position("SPY", -7.0, 580.5)])
    assert LumibotBroker(s).position("SPY") == Holding(-7, 580.5)
    assert LumibotBroker(FakeStrategy(positions=[position("QQQ", 5, 400)])).position("SPY") is None
    assert LumibotBroker(FakeStrategy(positions=[position("SPY", 0, 0)])).position("SPY") is None


def test_orders_lists_active_orders_for_the_symbol():
    s = FakeStrategy(orders=[
        order("SPY", "sell", "stop", 10, stop=579.0, id="a"),
        order("SPY", "buy", "market", 10, active=False, id="b"),
        order("KNX", "sell", "stop", 3, stop=30.0, id="c"),
    ])
    (o,) = LumibotBroker(s).orders("SPY")
    assert (o.id, o.symbol, o.side, o.kind, o.qty, o.price) == ("a", "SPY", "sell", "stop", 10, 579.0)


def test_cancel_hands_back_lumibots_own_order():
    lumi = order("SPY", "sell", "stop", 10, stop=579.0, id="a")
    s = FakeStrategy(orders=[lumi])
    broker = LumibotBroker(s)
    (o,) = broker.orders("SPY")
    broker.cancel(o)
    assert s.cancelled == [lumi]


def test_minute_bars_in_new_york_time():
    index = pd.to_datetime(["2026-10-13 13:30", "2026-10-13 13:31"]).tz_localize("UTC")
    frame = pd.DataFrame({"open": [1, 2.0], "high": [3, 4.0], "low": [0.5, 1.5], "close": [2, 3.0]}, index=index)
    s = FakeStrategy(frame=frame)
    bars = LumibotBroker(s).minute_bars("SPY", 2)
    assert s.asked == [("SPY", 2, "minute")]
    assert bars == [Bar(datetime(2026, 10, 13, 9, 30), 1, 3, 0.5, 2), Bar(datetime(2026, 10, 13, 9, 31), 2, 4, 1.5, 3)]


def test_live_minute_bars_come_from_the_iex_feed(monkeypatch):
    import alpaca.data.historical as historical

    asked = []

    class Client:
        def __init__(self, key, secret):
            asked.append((key, secret))

        def get_stock_bars(self, request):
            asked.append((request.symbol_or_symbols, request.feed.value, str(request.timeframe)))
            bar = SimpleNamespace(timestamp=datetime(2026, 10, 13, 13, 30, tzinfo=timezone.utc),
                                  open=1, high=3, low=0.5, close=2)
            return SimpleNamespace(data={"SPY": [bar]})

    monkeypatch.setattr(historical, "StockHistoricalDataClient", Client)
    s = FakeStrategy()
    s.is_backtesting = False
    s.broker = SimpleNamespace(api_key="k", api_secret="s")
    assert LumibotBroker(s).minute_bars("SPY", 10) == [Bar(datetime(2026, 10, 13, 9, 30), 1, 3, 0.5, 2)]
    assert asked == [("k", "s"), ("SPY", "iex", "1Min")]
    assert s.asked == []


def test_minute_bars_empty_without_data():
    assert LumibotBroker(FakeStrategy()).minute_bars("SPY", 5) == []


def test_exchange_time_drops_the_zone():
    assert exchange_time(datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc)) == datetime(2026, 10, 13, 10, 0)
    assert exchange_time(datetime(2026, 10, 13, 10, 0)) == datetime(2026, 10, 13, 10, 0)


def test_entries_are_market_orders_and_stops_close_either_side():
    s = FakeStrategy()
    broker = LumibotBroker(s)
    broker.market("SPY", 10)
    broker.market("SPY", -10)
    broker.place_stop("SPY", 10, 579.0)
    broker.place_stop("SPY", -10, 583.0)
    assert s.submitted == [
        ("SPY", 10, "buy", {"time_in_force": "day"}),
        ("SPY", 10, "sell_short", {"time_in_force": "day"}),
        ("SPY", 10, "sell", {"stop_price": 579.0, "time_in_force": "day"}),
        ("SPY", 10, "buy", {"stop_price": 583.0, "time_in_force": "day"}),
    ]


def test_close_closes_only_the_named_symbol():
    s = FakeStrategy()
    LumibotBroker(s).close("SPY")
    assert s.closed == ["SPY"]


def test_every_rule_parameter_is_a_strategy_parameter():
    assert rules_from(OrbStrategy.parameters) == Params()


def test_rules_from_casts_overrides():
    r = rules_from({**OrbStrategy.parameters, "range_minutes": "15", "risk_per_trade": "0.005"})
    assert (r.range_minutes, r.risk_per_trade) == (15, 0.005)


def test_symbols_follow_the_symbol_parameter():
    assert OrbStrategy.symbols(OrbStrategy.parameters) == {"SPY"}
    assert OrbStrategy.symbols({"symbol": "qqq"}) == {"QQQ"}


def test_never_sells_everything():
    # Shared account: a strategy-wide liquidation would close the others' positions too.
    import inspect

    import strategies.orb.session as session
    import strategies.orb.strategy as strategy

    for module in (session, strategy):
        assert "sell_all" not in inspect.getsource(module)
        assert "cancel_open_orders" not in inspect.getsource(module)
