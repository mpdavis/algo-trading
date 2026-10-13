"""The ORB rules as a LumiBot strategy. LumiBot owns scheduling, the broker
connection and backtesting; each iteration hands `session.step` a `Broker`
view of the strategy, so the decisions come from the tested code in
`rules.py` and `session.py`."""

from __future__ import annotations

import time
from dataclasses import asdict, fields
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from lumibot.strategies import Strategy

from algo_trading.base import ManagedStrategy

from .rules import Bar, Params
from .session import Holding, Order, StateFile, step

EXCHANGE = ZoneInfo("America/New_York")


def exchange_time(ts: datetime) -> datetime:
    """Naive New York time, which is what the rules compare; LumiBot's own
    timestamps are already in it when they carry no zone."""
    return ts.astimezone(EXCHANGE).replace(tzinfo=None) if ts.tzinfo else ts


class LumibotBroker:
    """The `session.Broker` protocol over a running LumiBot strategy."""

    def __init__(self, strategy: Strategy) -> None:
        self.s = strategy
        # Order ids handed to the session, back to LumiBot's own objects.
        self._orders: dict[str, object] = {}
        self._client = None

    def equity(self) -> float:
        return float(self.s.portfolio_value)

    def position(self, symbol: str) -> Holding | None:
        # LumiBot reports shorts as negative quantities.
        for pos in self.s.get_positions():
            if pos.asset.symbol == symbol and getattr(pos.asset, "asset_type", "stock") == "stock":
                qty = int(float(pos.quantity))
                return Holding(qty, float(pos.avg_fill_price or 0)) if qty else None
        return None

    def orders(self, symbol: str) -> list[Order]:
        out = []
        for o in self.s.get_orders():
            if o.asset.symbol != symbol or not o.is_active():
                continue
            kind = str(o.order_type or "market")
            price = o.stop_price if kind == "stop" else o.limit_price
            self._orders[o.identifier] = o
            out.append(Order(o.identifier, symbol, str(o.side), kind, int(float(o.quantity)),
                             None if price is None else float(price)))
        return out

    def minute_bars(self, symbol: str, count: int) -> list[Bar]:
        if self.s.is_backtesting:
            return self._backtest_bars(symbol, count)
        return self._live_bars(symbol, count)

    def _backtest_bars(self, symbol: str, count: int) -> list[Bar]:
        bars = self.s.get_historical_prices(symbol, count, "minute")
        frame = bars.df if bars is not None else None
        if frame is None:
            return []
        return [
            Bar(exchange_time(ts.to_pydatetime()), float(r.open), float(r.high), float(r.low), float(r.close))
            for ts, r in frame.iterrows()
        ]

    def _live_bars(self, symbol: str, count: int) -> list[Bar]:
        """Real-time bars from Alpaca's IEX feed. LumiBot's own Alpaca data
        delays stock bars 16 minutes (the free plan's SIP limit), which would
        put the range and every breakout a quarter of an hour behind; IEX is
        free and current, if thinner."""
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        if self._client is None:
            self._client = StockHistoricalDataClient(self.s.broker.api_key, self.s.broker.api_secret)
        start = datetime.now(timezone.utc) - timedelta(minutes=count + 1)
        request = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute, start=start,
                                   feed=DataFeed.IEX)
        found = self._client.get_stock_bars(request).data.get(symbol, [])
        return [Bar(exchange_time(b.timestamp), float(b.open), float(b.high), float(b.low), float(b.close))
                for b in found]

    def shortable(self, symbol: str) -> bool:
        # Backtests have no borrow constraints; live Alpaca exposes its client.
        api = getattr(self.s.broker, "api", None)
        if self.s.is_backtesting or api is None:
            return True
        asset = api.get_asset(symbol)
        return bool(asset.shortable and asset.easy_to_borrow)

    def market(self, symbol: str, qty: int) -> None:
        side = "buy" if qty > 0 else "sell_short"
        self.s.submit_order(self.s.create_order(symbol, abs(qty), side, time_in_force="day"))

    def place_stop(self, symbol: str, qty: int, stop: float) -> None:
        # Sells under a long, buys back over a short; both expire at the close.
        side = "sell" if qty > 0 else "buy"
        self.s.submit_order(self.s.create_order(symbol, abs(qty), side, stop_price=stop, time_in_force="day"))

    def cancel(self, order: Order) -> None:
        o = self._orders.pop(order.id, None)
        if o is None:
            return
        self.s.cancel_order(o)
        if self.s.is_backtesting:
            return
        # Alpaca holds a position's shares for its stop until the cancel lands;
        # a close sent before then is rejected for insufficient quantity.
        for _ in range(20):
            if not o.is_active():
                return
            time.sleep(0.5)

    def close(self, symbol: str) -> None:
        self.s.close_position(symbol)


def rules_from(parameters: dict) -> Params:
    """Params from the strategy's parameters, cast to each field's type, so an
    environment override like ORB_RANGE_MINUTES=15 arrives as an int."""
    return Params(**{f.name: type(getattr(Params(), f.name))(parameters[f.name]) for f in fields(Params)})


class OrbStrategy(ManagedStrategy):
    parameters = {
        **ManagedStrategy.parameters,
        **asdict(Params()),
    }

    @classmethod
    def symbols(cls, parameters: dict) -> set[str]:
        return {str(parameters["symbol"]).upper()}

    def initialize(self) -> None:
        # Every minute, live and in backtests: the range, the breakout and the
        # flatten all happen on minute bars.
        self.sleeptime = "1M"
        self.set_market("NYSE")
        self.rules = rules_from({**self.parameters, "symbol": str(self.parameters["symbol"]).upper()})
        self.state = StateFile(self.data_dir / "state.json")
        self.view = LumibotBroker(self)
        self.closing = False

    def before_market_closes(self) -> None:
        # LumiBot stops iterating `minutes_before_closing` before the bell (one
        # minute live, five in backtests) and calls this instead. Flattening
        # here too means a late `flatten_at` can never leave a position open
        # overnight.
        self.closing = True
        try:
            self.on_trading_iteration()
        finally:
            self.closing = False

    def iterate(self) -> None:
        step(self.view, self.rules, self.state, exchange_time(self.get_datetime()), closing=self.closing)
