"""Market-neutral pairs trading: when a cointegrated pair's log-price spread
stretches, buy the cheap stock and short the rich one, sized by the rolling
hedge ratio, and close both legs when the spread reverts."""

from __future__ import annotations

from pathlib import Path

from algo_trading.base import Status

from .session import StateFile, Stored
from .strategy import PairsStrategy

STRATEGY = PairsStrategy


def status(data_dir: Path) -> Status:
    state = StateFile(data_dir / "state.json")
    last, pairs = state.load()
    decisions = state.decisions()
    rows = []
    for name in sorted(set(pairs) | set(decisions)):
        held = pairs.get(name, Stored())
        d = decisions.get(name)
        position = held.side.value.replace("_", " ")
        if held.entered:
            position += f" since {held.entered.isoformat()}"
        if held.cooldown:
            position += " (cooling down)"
        rows.append([
            name,
            f"{d.z:+.2f}" if d else "—",
            f"{d.beta:.3f}" if d else "—",
            d.action.replace("_", " ") if d else "—",
            position,
        ])
    return Status(
        facts=[("Last session", last.isoformat() if last else "never")],
        columns=["Pair", "z-score", "Hedge ratio", "Last decision", "Position"],
        rows=rows,
    )
