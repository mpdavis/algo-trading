"""One step of the trading day, run every minute: reconcile the account with
today's trade, then wait for the range, enter on the first breakout, keep a
stop at the far side of the range, and close everything before the bell.
Account positions are the source of truth for what is held; the state file
remembers what positions cannot (today's range and trade, and the days
before), and is what the dashboard shows."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from .rules import OPEN, Bar, Params, Range, Side, breakout, finished, opening_range, shares, stop_price

log = logging.getLogger(__name__)

# Finished days kept for the dashboard.
HISTORY = 20


@dataclass(frozen=True)
class Holding:
    qty: int  # negative for a short
    avg_price: float


@dataclass(frozen=True)
class Order:
    id: str
    symbol: str
    side: str  # "buy" or "sell"
    kind: str  # "market", "stop", ...
    qty: int
    price: float | None = None


class Broker(Protocol):
    def equity(self) -> float: ...
    def position(self, symbol: str) -> Holding | None: ...
    def orders(self, symbol: str) -> list[Order]: ...
    def minute_bars(self, symbol: str, count: int) -> list[Bar]: ...
    def shortable(self, symbol: str) -> bool: ...
    def market(self, symbol: str, qty: int) -> None: ...
    def place_stop(self, symbol: str, qty: int, stop: float) -> None: ...
    def cancel(self, order: Order) -> None: ...
    def close(self, symbol: str) -> None: ...


@dataclass
class Trade:
    side: Side
    qty: int
    stop: float
    entered_at: str  # HH:MM the entry was sent
    # The average fill, once the account shows the position.
    entry: float | None = None
    # How it ended: "stop", "flatten", "unfilled", "mismatch". The exit price
    # is approximate: the stop price, or the last minute's close when flattened.
    exit: float | None = None
    reason: str | None = None


@dataclass
class Day:
    day: date
    high: float | None = None
    low: float | None = None
    trade: Trade | None = None
    # Set once nothing more will be entered today.
    done: bool = False
    # What the last step decided, for the dashboard.
    note: str = ""

    @property
    def range(self) -> Range | None:
        return None if self.high is None or self.low is None else Range(self.high, self.low)


@dataclass
class State:
    today: Day | None = None
    history: list[Day] = field(default_factory=list)


class StateFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> State:
        if not self.path.exists():
            return State()
        raw = json.loads(self.path.read_text())
        today = raw.get("today")
        return State(
            today=_day(today) if today else None,
            history=[_day(d) for d in raw.get("history", [])],
        )

    def save(self, state: State) -> None:
        raw = {
            "today": _raw_day(state.today) if state.today else None,
            "history": [_raw_day(d) for d in state.history],
        }
        # Write-then-rename, so a crash mid-write cannot leave a truncated file.
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, indent=2))
        os.replace(tmp, self.path)


def _raw_day(d: Day) -> dict:
    raw = asdict(d)
    raw["day"] = d.day.isoformat()
    if d.trade:
        raw["trade"]["side"] = d.trade.side.value
    return raw


def _day(raw: dict) -> Day:
    t = raw.get("trade")
    return Day(
        day=date.fromisoformat(raw["day"]),
        high=raw.get("high"),
        low=raw.get("low"),
        trade=Trade(**{**t, "side": Side(t["side"])}) if t else None,
        done=bool(raw.get("done")),
        note=raw.get("note", ""),
    )


def step(broker: Broker, params: Params, state: StateFile, now: datetime, closing: bool = False) -> None:
    """`closing` flattens whatever the time: the market is about to close."""
    s = state.load()
    if s.today is None or s.today.day != now.date():
        if s.today is not None and s.today.trade is not None:
            s.history = [s.today, *s.history][:HISTORY]
        s.today = Day(now.date())
    if now.time() < OPEN:
        return
    _step(broker, params, s.today, now, closing, lambda: state.save(s))
    state.save(s)


def _step(broker: Broker, p: Params, today: Day, now: datetime, closing: bool, save) -> None:
    symbol = p.symbol
    orders = broker.orders(symbol)
    stops = [o for o in orders if o.kind == "stop"]
    # An entry or a close still working means the account is about to change.
    if any(o.kind != "stop" for o in orders):
        today.note = "waiting for an order to fill"
        return
    held = broker.position(symbol)
    trade = today.trade

    # Reconcile what the account holds with today's trade.
    if held and (trade is None or trade.reason or _side(held) is not trade.side):
        # Nothing of this strategy's should be open now: left from another day,
        # opened by hand, or the wrong way round.
        log.error("%s: %d shares not from today's trade; closing them", symbol, held.qty)
        if trade and not trade.reason:
            trade.reason = "mismatch"
        _close(broker, symbol, stops)
        today.done = True
        today.note = "closed a position that was not today's trade"
        return
    if trade and held and trade.entry is None:
        trade.entry = held.avg_price
        log.info("%s: %s %d filled at %.2f", symbol, trade.side.value, abs(held.qty), held.avg_price)
    if trade and not held and not trade.reason:
        trade.reason = "unfilled" if trade.entry is None else "stop"
        if trade.entry is not None:
            trade.exit = trade.stop
        log.info("%s: %s trade over (%s)", symbol, trade.side.value, trade.reason)
        today.done = True
    if not held:
        # A stop with nothing to protect would open a new position if it fired.
        for o in stops:
            log.warning("%s: cancelling a stop with no position under it", symbol)
            broker.cancel(o)
        stops = []

    if closing or now.time() >= p.flatten_time:
        if held and trade:
            bars = finished(broker.minute_bars(symbol, 2), now)
            log.info("%s: flattening %d before the close", symbol, held.qty)
            _close(broker, symbol, stops)
            trade.reason = "flatten"
            trade.exit = bars[-1].close if bars else None
        today.done = True
        today.note = "flat for the night"
        return

    if held and trade:
        _ensure_stop(broker, symbol, held.qty, trade.stop, stops)
        today.note = f"holding {trade.side.value} {abs(held.qty)}, stop {trade.stop:.2f}"
        return
    if today.done:
        today.note = today.note or "done for the day"
        return

    r = today.range
    bars = finished(broker.minute_bars(symbol, 3 if r else _minutes_since_open(now) + 2), now)
    bars = [b for b in bars if b.start.date() == now.date()]
    if r is None:
        r = opening_range(bars, p)
        if r is None:
            today.note = "waiting for the opening range"
            return
        today.high, today.low = r.high, r.low
        log.info("%s: opening range %.2f-%.2f", symbol, r.low, r.high)
    if now.time() >= p.last_entry_time:
        today.done = True
        today.note = f"no breakout before {p.last_entry}"
        return
    if not bars:
        today.note = "no minute bars yet"
        return
    last = bars[-1].close
    side = breakout(r, last)
    if side is None:
        today.note = f"inside the range at {last:.2f}"
        return
    if side is Side.SHORT and not broker.shortable(symbol):
        today.done = True
        today.note = f"{symbol} is not easy to borrow; no short today"
        return
    stop = stop_price(r, side)
    qty = shares(broker.equity(), last, stop, p)
    if qty == 0:
        today.done = True
        today.note = "position too small for a whole share"
        return
    today.trade = Trade(side, qty, stop, now.strftime("%H:%M"))
    today.note = f"entering {side.value} {qty} at about {last:.2f}, stop {stop:.2f}"
    # Saved before the order goes in: if the process dies in between, the
    # next step finds the position and knows it is today's trade.
    save()
    log.info("%s: close %.2f broke the range %.2f-%.2f; %s %d, stop %.2f",
             symbol, last, r.low, r.high, "BUY" if side is Side.LONG else "SELL SHORT", qty, stop)
    broker.market(symbol, qty if side is Side.LONG else -qty)


def _side(h: Holding) -> Side:
    return Side.LONG if h.qty > 0 else Side.SHORT


def _minutes_since_open(now: datetime) -> int:
    return max((now.hour - OPEN.hour) * 60 + now.minute - OPEN.minute, 0)


def _ensure_stop(broker: Broker, symbol: str, qty: int, stop: float, stops: list[Order]) -> None:
    if (len(stops) == 1 and stops[0].qty == abs(qty) and stops[0].price is not None
            and abs(stops[0].price - stop) < 0.005):
        return
    for o in stops:
        broker.cancel(o)
    log.info("%s: stop %d at %.2f", symbol, qty, stop)
    broker.place_stop(symbol, qty, stop)


def _close(broker: Broker, symbol: str, stops: list[Order]) -> None:
    # The stop holds the shares; it goes first, or Alpaca rejects the close.
    for o in stops:
        broker.cancel(o)
    broker.close(symbol)

