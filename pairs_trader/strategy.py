"""The trading rules, free of any broker or data source so the backtest and the
live runner make identical decisions from identical closes."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class Pair:
    y: str
    x: str

    @property
    def name(self) -> str:
        return f"{self.y}/{self.x}"


def parse_pairs(raw: str) -> list[Pair]:
    pairs = []
    for item in raw.split(","):
        y, _, x = item.strip().upper().partition("/")
        if not y or not x:
            raise ValueError(f"pair {item!r} is not Y/X")
        pairs.append(Pair(y, x))
    symbols = [s for p in pairs for s in (p.y, p.x)]
    # Each symbol's position belongs to exactly one pair; sharing one would make
    # two pairs fight over the same shares.
    if len(symbols) != len(set(symbols)):
        raise ValueError("a symbol appears in more than one pair")
    return pairs


@dataclass(frozen=True)
class Params:
    lookback: int = 60
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 3.5
    max_hold_days: int = 20
    # Share of account equity put into one pair, long and short legs together.
    pair_gross: float = 0.5


class Side(str, Enum):
    FLAT = "flat"
    # Long Y, short X: entered when Y is cheap relative to X.
    LONG = "long_spread"
    # Short Y, long X: entered when Y is rich relative to X.
    SHORT = "short_spread"


class Action(str, Enum):
    HOLD = "hold"
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT_REVERT = "exit_revert"
    EXIT_STOP = "exit_stop"
    EXIT_TIME = "exit_time"


EXITS = {Action.EXIT_REVERT, Action.EXIT_STOP, Action.EXIT_TIME}


@dataclass(frozen=True)
class Fit:
    alpha: float
    beta: float
    z: float


@dataclass
class PairState:
    side: Side = Side.FLAT
    held_days: int = 0
    # Set after a stop or time exit; blocks re-entry until the spread has
    # normalized, so a broken pair is not bought straight back.
    cooldown: bool = False


def fit_spread(y_closes: list[float], x_closes: list[float]) -> Fit | None:
    """OLS of log(Y) on log(X); z is the latest residual in standard deviations."""
    if len(y_closes) != len(x_closes) or len(y_closes) < 3:
        return None
    log_y = [math.log(v) for v in y_closes]
    log_x = [math.log(v) for v in x_closes]
    mean_x = statistics.fmean(log_x)
    mean_y = statistics.fmean(log_y)
    var_x = sum((v - mean_x) ** 2 for v in log_x)
    if var_x <= 0:
        return None
    beta = sum((a - mean_x) * (b - mean_y) for a, b in zip(log_x, log_y)) / var_x
    alpha = mean_y - beta * mean_x
    spread = [b - (alpha + beta * a) for a, b in zip(log_x, log_y)]
    std = statistics.pstdev(spread)
    if std <= 0:
        return None
    return Fit(alpha=alpha, beta=beta, z=(spread[-1] - statistics.fmean(spread)) / std)


def decide(state: PairState, fit: Fit, params: Params) -> Action:
    z = fit.z
    if state.side is Side.LONG:
        if z >= -params.exit_z:
            return Action.EXIT_REVERT
        if z <= -params.stop_z:
            return Action.EXIT_STOP
        if state.held_days >= params.max_hold_days:
            return Action.EXIT_TIME
        return Action.HOLD
    if state.side is Side.SHORT:
        if z <= params.exit_z:
            return Action.EXIT_REVERT
        if z >= params.stop_z:
            return Action.EXIT_STOP
        if state.held_days >= params.max_hold_days:
            return Action.EXIT_TIME
        return Action.HOLD

    if state.cooldown:
        return Action.HOLD
    # A negative hedge ratio means the two legs would not offset each other.
    if fit.beta <= 0:
        return Action.HOLD
    if -params.stop_z < z <= -params.entry_z:
        return Action.ENTER_LONG
    if params.entry_z <= z < params.stop_z:
        return Action.ENTER_SHORT
    return Action.HOLD


def apply(state: PairState, action: Action, fit: Fit, params: Params) -> PairState:
    """The pair's state after `action` fills, before the next day's decision."""
    if action is Action.ENTER_LONG:
        return PairState(side=Side.LONG)
    if action is Action.ENTER_SHORT:
        return PairState(side=Side.SHORT)
    if action is Action.EXIT_REVERT:
        return PairState()
    if action in (Action.EXIT_STOP, Action.EXIT_TIME):
        return PairState(cooldown=True)
    if state.side is not Side.FLAT:
        return PairState(side=state.side, held_days=state.held_days + 1)
    if state.cooldown and abs(fit.z) <= params.exit_z:
        return PairState()
    return state


def leg_notionals(beta: float, gross: float) -> tuple[float, float]:
    """Dollar amounts for Y and X so that X's leg is beta times Y's: a 1% move
    in X then offsets a beta% move in Y."""
    return gross / (1 + beta), gross * beta / (1 + beta)
