"""Intraday opening range breakout on SPY: mark the high and low of the first
five minutes, go long on a close above the range or short on one below it,
stop at the far side of the range, and close by 15:50 so nothing is held
overnight. At most one trade a day."""

from __future__ import annotations

from pathlib import Path

from algo_trading.base import Status

from .session import Day, StateFile
from .strategy import OrbStrategy

STRATEGY = OrbStrategy


def _row(d: Day) -> list[str]:
    t = d.trade
    rng = f"{d.low:.2f}–{d.high:.2f}" if d.range else "—"
    if t is None:
        return [d.day.isoformat(), rng, "—", "—", "—", "—", "—", "—"]
    pl = "—"
    if t.entry is not None and t.exit is not None:
        sign = 1 if t.side.value == "long" else -1
        pl = f"≈{sign * (t.exit - t.entry) * t.qty:+,.2f}"
    return [
        d.day.isoformat(),
        rng,
        f"{t.side.value} {t.qty} at {t.entered_at}",
        f"{t.entry:.2f}" if t.entry is not None else "—",
        f"{t.stop:.2f}",
        f"≈{t.exit:.2f}" if t.exit is not None else "—",
        t.reason or "open",
        pl,
    ]


def status(data_dir: Path) -> Status:
    s = StateFile(data_dir / "state.json").load()
    today = s.today
    facts = [("Session", today.day.isoformat() if today else "never")]
    if today:
        facts.append(("Now", today.note or "—"))
    days = ([today] if today else []) + s.history
    return Status(
        facts=facts,
        columns=["Day", "Opening range", "Trade", "Entry", "Stop", "Exit", "How it ended", "P&L"],
        rows=[_row(d) for d in days],
    )
