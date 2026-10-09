"""One trading session: read yesterday's closes and the account, decide each
pair, place orders. Account positions are the source of truth for what is
held; the state file only remembers what positions cannot (entry day and
cooldown), and which session last ran."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from .rules import (
    EXITS,
    Action,
    Pair,
    PairState,
    Params,
    Side,
    apply,
    decide,
    fit_spread,
    leg_notionals,
)

log = logging.getLogger(__name__)


class Broker(Protocol):
    def equity(self) -> float: ...
    def positions(self) -> dict[str, int]: ...
    def open_orders(self, symbols: list[str]) -> bool: ...
    def daily_closes(self, symbols: list[str], before: date, sessions: int) -> dict[str, dict[date, float]]: ...
    def shortable(self, symbol: str) -> bool: ...
    def submit(self, symbol: str, qty: int) -> None: ...
    def close(self, symbol: str) -> None: ...


@dataclass
class Stored:
    side: Side = Side.FLAT
    entered: date | None = None
    cooldown: bool = False


@dataclass(frozen=True)
class Decision:
    """The last session's reading of a pair, kept for the dashboard."""

    z: float
    beta: float
    action: str


class StateFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _raw(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def load(self) -> tuple[date | None, dict[str, Stored]]:
        raw = self._raw()
        pairs = {
            name: Stored(
                side=Side(p["side"]),
                entered=date.fromisoformat(p["entered"]) if p.get("entered") else None,
                cooldown=bool(p.get("cooldown")),
            )
            for name, p in raw.get("pairs", {}).items()
        }
        last = raw.get("last_session")
        return (date.fromisoformat(last) if last else None), pairs

    def decisions(self) -> dict[str, Decision]:
        return {name: Decision(**d) for name, d in self._raw().get("decisions", {}).items()}

    def save(self, last_session: date, pairs: dict[str, Stored], decisions: dict[str, Decision] | None = None) -> None:
        raw = {
            "last_session": last_session.isoformat(),
            "pairs": {
                name: {"side": s.side.value, "entered": s.entered.isoformat() if s.entered else None, "cooldown": s.cooldown}
                for name, s in pairs.items()
            },
            "decisions": {name: vars(d) for name, d in (decisions or {}).items()},
        }
        # Write-then-rename, so a crash mid-write cannot leave a truncated file.
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, indent=2))
        os.replace(tmp, self.path)


def held_side(positions: dict[str, int], pair: Pair) -> Side | None:
    """The side the account holds for a pair; None when the legs do not match
    a pair trade (one leg missing, or both the same direction)."""
    qy, qx = positions.get(pair.y, 0), positions.get(pair.x, 0)
    if qy == 0 and qx == 0:
        return Side.FLAT
    if qy > 0 and qx < 0:
        return Side.LONG
    if qy < 0 and qx > 0:
        return Side.SHORT
    return None


def run_session(broker: Broker, pairs: list[Pair], params: Params, state: StateFile, today: date) -> bool:
    """Returns False when the session must be retried later."""
    _, stored = state.load()
    symbols = sorted({s for p in pairs for s in (p.y, p.x)})
    # Unfilled orders are not positions yet, so deciding now could enter a
    # pair a second time.
    if broker.open_orders(symbols):
        log.warning("orders still open for %s; deferring the session", ", ".join(symbols))
        return False
    # Enough sessions for the fit window and for counting a full holding period.
    closes = broker.daily_closes(symbols, before=today, sessions=max(params.lookback, params.max_hold_days) + 5)
    positions = broker.positions()
    equity = broker.equity()
    log.info("session %s equity=%.2f positions=%s", today, equity, {s: q for s, q in positions.items() if s in symbols})
    decisions: dict[str, Decision] = {}

    for pair in pairs:
        prev = stored.get(pair.name, Stored())
        sessions = sorted(set(closes[pair.y]) & set(closes[pair.x]))
        days = sessions[-params.lookback :]
        if len(days) < params.lookback:
            log.warning("%s: only %d sessions of closes, need %d; skipping", pair.name, len(days), params.lookback)
            continue
        fit = fit_spread([closes[pair.y][d] for d in days], [closes[pair.x][d] for d in days])
        if fit is None:
            log.warning("%s: spread fit failed; skipping", pair.name)
            continue

        side = held_side(positions, pair)
        if side is None:
            log.error("%s: legs out of balance (%s=%d, %s=%d); flattening both", pair.name, pair.y,
                      positions.get(pair.y, 0), pair.x, positions.get(pair.x, 0))
            _close_legs(broker, pair, positions)
            stored[pair.name] = Stored(cooldown=True)
            continue
        if side is Side.FLAT and prev.side is not Side.FLAT:
            log.warning("%s: positions closed outside the trader; cooling down", pair.name)
            prev = Stored(cooldown=True)
        elif side is not Side.FLAT and prev.side is not side:
            log.warning("%s: adopting %s found in the account", pair.name, side.value)
            prev = Stored(side=side, entered=days[-1])

        # Sessions closed since the entry filled, excluding the entry day itself.
        held_days = 0
        if side is not Side.FLAT and prev.entered:
            held_days = max(sum(1 for d in sessions if d >= prev.entered) - 1, 0)
        pstate = PairState(side=side, held_days=held_days, cooldown=prev.cooldown)
        action = decide(pstate, fit, params)
        log.info("%s: z=%+.2f beta=%.3f side=%s held=%d -> %s",
                 pair.name, fit.z, fit.beta, side.value, held_days, action.value)
        decisions[pair.name] = Decision(z=round(fit.z, 3), beta=round(fit.beta, 4), action=action.value)

        if action in EXITS:
            _close_legs(broker, pair, positions)
        elif action in (Action.ENTER_LONG, Action.ENTER_SHORT):
            if not _enter(broker, pair, action, fit.beta, params.pair_gross * equity, closes, days[-1]):
                continue

        nxt = apply(pstate, action, fit, params)
        entered = None
        if action in (Action.ENTER_LONG, Action.ENTER_SHORT):
            entered = today
        elif nxt.side is not Side.FLAT:
            entered = prev.entered
        stored[pair.name] = Stored(side=nxt.side, entered=entered, cooldown=nxt.cooldown)

    state.save(today, stored, decisions)
    return True


def _enter(broker: Broker, pair: Pair, action: Action, beta: float, gross: float,
           closes: dict[str, dict[date, float]], last: date) -> bool:
    n_y, n_x = leg_notionals(beta, gross)
    q_y, q_x = int(n_y // closes[pair.y][last]), int(n_x // closes[pair.x][last])
    if q_y == 0 or q_x == 0:
        log.warning("%s: position too small for whole shares (%s=%d, %s=%d)", pair.name, pair.y, q_y, pair.x, q_x)
        return False
    if action is Action.ENTER_LONG:
        orders = [(pair.x, -q_x), (pair.y, q_y)]
    else:
        orders = [(pair.y, -q_y), (pair.x, q_x)]
    short_symbol = orders[0][0]
    if not broker.shortable(short_symbol):
        log.warning("%s: %s is not easy to borrow; skipping entry", pair.name, short_symbol)
        return False
    for symbol, qty in orders:
        log.info("%s: %s %d %s", pair.name, "BUY" if qty > 0 else "SELL SHORT", abs(qty), symbol)
        broker.submit(symbol, qty)
    return True


def _close_legs(broker: Broker, pair: Pair, positions: dict[str, int]) -> None:
    for symbol in (pair.y, pair.x):
        if positions.get(symbol, 0):
            log.info("%s: closing %d %s", pair.name, positions[symbol], symbol)
            broker.close(symbol)

