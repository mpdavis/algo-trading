"""The trading rules, free of any broker or data source so the backtest and the
live runner make identical decisions from identical minute bars."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum

OPEN = time(9, 30)


@dataclass(frozen=True)
class Bar:
    """One minute bar, labelled by the minute it starts (09:30 covers
    09:30:00 to 09:30:59), in exchange time."""

    start: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Params:
    symbol: str = "SPY"
    # Minutes after the open that make up the opening range.
    range_minutes: int = 5
    # No new entry at or after this time; the range has stopped meaning much.
    last_entry: str = "15:00"
    # Everything is closed at this time, so nothing is held overnight. Backtests
    # stop iterating at 15:54, live at 15:58; the strategy also flattens then.
    flatten_at: str = "15:50"
    # Equity lost if the stop at the far side of the range fills.
    risk_per_trade: float = 0.01
    # Largest position, long or short, as a share of equity.
    max_position: float = 0.25

    @property
    def range_end(self) -> time:
        return (datetime.combine(datetime.min, OPEN) + timedelta(minutes=self.range_minutes)).time()

    @property
    def last_entry_time(self) -> time:
        return time.fromisoformat(self.last_entry)

    @property
    def flatten_time(self) -> time:
        return time.fromisoformat(self.flatten_at)


@dataclass(frozen=True)
class Range:
    high: float
    low: float


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


def finished(bars: list[Bar], now: datetime) -> list[Bar]:
    """The bars that closed by `now`: a bar starting 10:04 is done at 10:05."""
    return [b for b in bars if b.start + timedelta(minutes=1) <= now]


def opening_range(bars: list[Bar], p: Params) -> Range | None:
    """High and low of today's first `range_minutes` minutes; None until
    they have all closed. A thin feed can skip a quiet minute, so it takes
    whatever bars fall inside the window once the window is over."""
    day = bars[-1].start.date() if bars else None
    inside = [b for b in bars if b.start.date() == day and OPEN <= b.start.time() < p.range_end]
    after = any(b.start.date() == day and b.start.time() >= p.range_end for b in bars)
    if not inside or not after:
        return None
    return Range(max(b.high for b in inside), min(b.low for b in inside))


def breakout(r: Range, last_close: float) -> Side | None:
    """Long on a close above the range, short on one below it."""
    if last_close > r.high:
        return Side.LONG
    if last_close < r.low:
        return Side.SHORT
    return None


def stop_price(r: Range, side: Side) -> float:
    """The far side of the range: a long is wrong once price is back under the
    range's low, a short once it is back over the high."""
    return round(r.low if side is Side.LONG else r.high, 2)


def shares(equity: float, entry: float, stop: float, p: Params) -> int:
    """Whole shares risking `risk_per_trade` of equity between `entry` and
    `stop`, and no more than `max_position` of equity."""
    risk_per_share = abs(entry - stop)
    if entry <= 0 or risk_per_share <= 0:
        return 0
    by_risk = equity * p.risk_per_trade / risk_per_share
    by_size = equity * p.max_position / entry
    return max(int(min(by_risk, by_size)), 0)
