"""Runs several strategies in one process against one Alpaca account.

LumiBot's `Trader.run_all` refuses more than one live strategy, but the rest
of LumiBot is built for several: a broker keeps a list of subscribers, opens
one trading stream, and routes each order event to the strategy named on the
order. So `live` builds one broker, hands it to every strategy, and starts
their executor threads itself, as `run_all` would have.

This leans on a few LumiBot internals (`Trader._set_logger`,
`Strategy._executor`, and the broker's position and order hooks below).
lumibot is pinned exactly, and tests/test_live.py runs the real classes, so a
bump that moves them fails the suite rather than the deploy.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

from lumibot.brokers import Alpaca
from lumibot.traders import Trader

from .base import ManagedStrategy


class SharedAlpaca(Alpaca):
    """An Alpaca broker shared by several strategies.

    Alpaca does not record which strategy holds a position, so LumiBot labels
    every position with whichever strategy synced last, and every order it
    finds at the broker with whichever strategy asked first. With one strategy
    that is harmless. With several, a strategy can lose sight of its own
    position or stop order, or close nothing because the position is labelled
    as another's. Here a position or order found at the broker belongs to the
    strategy that trades its symbol (`check_disjoint` allows at most one).
    Symbols no strategy claims keep LumiBot's label."""

    def __init__(self, config: dict, owners: Mapping[str, str]) -> None:
        # Set before the base constructor, which may already sync.
        self.owners = dict(owners)
        super().__init__(config)

    def sync_positions(self, strategy) -> None:
        # Given no strategy, LumiBot updates positions but keeps their labels.
        super().sync_positions(None)

    def _set_initial_positions(self, strategy) -> None:
        # Every strategy calls this as it starts, and the base appends the
        # account's positions each time; a sync adds only the missing ones.
        self.sync_positions(None)

    def _parse_broker_position(self, broker_position, strategy, orders=None):
        position = super()._parse_broker_position(broker_position, strategy, orders)
        position.strategy = self.owners.get(position.asset.symbol, position.strategy)
        return position

    def _parse_broker_order(self, response, strategy_name, strategy_object=None):
        symbol = getattr(response, "symbol", None)
        if symbol is None and isinstance(response, dict):
            symbol = response.get("symbol")
        owner = self.owners.get(str(symbol).upper()) if symbol else None
        return super()._parse_broker_order(response, owner or strategy_name, strategy_object=strategy_object)


def run(strategies: Sequence[ManagedStrategy], poll_seconds: float = 1.0) -> str:
    """Start every strategy and block until one crashes, returning its name.

    A crash is LumiBot calling `on_bot_crash`, or an executor thread ending:
    live, a healthy one runs until the process does. LumiBot has logged why;
    the caller should exit so the container restarts with all of them."""
    trader = Trader()
    for strategy in strategies:
        trader.add_strategy(strategy)
    # The live log levels run_all sets up.
    trader._set_logger()
    executors = [strategy._executor for strategy in strategies]
    for executor in executors:
        executor.start()
    while True:
        for strategy, executor in zip(strategies, executors):
            if strategy.crash is not None or not executor.is_alive():
                return strategy.name
        time.sleep(poll_seconds)
