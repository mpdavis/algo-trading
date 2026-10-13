"""`algo-trading live` with Alpaca's network calls stubbed out, run as a
subprocess by test_live.py. Everything else is real: the CLI, the strategies,
the broker class, and LumiBot's strategy executors.

It prints `HARNESS stream` for each trading stream a broker opens and
`HARNESS trading <name>` once each strategy's executor reaches its trading
loop. With HARNESS_CRASH=<name>:<stage>, that strategy raises instead, in
`initialize` or in its trading `session`.
"""

import os
import sys
import time

from lumibot.brokers.alpaca import Alpaca
from lumibot.strategies.strategy_executor import StrategyExecutor


def say(line):
    print(f"HARNESS {line}", flush=True)


def get_stream_object(self):
    say("stream")
    return object()


def run_stream(self):
    self._stream_established()


crash = os.environ.get("HARNESS_CRASH", ":").split(":")


def check_crash(self, stage):
    if [self.strategy.name, stage] == crash:
        raise RuntimeError(f"harness crash in {stage}")


real_initialize = StrategyExecutor._initialize


def initialize(self):
    check_crash(self, "initialize")
    return real_initialize(self)


def run_trading_session(self):
    check_crash(self, "session")
    say(f"trading {self.strategy.name}")
    while not self.stop_event.is_set():
        time.sleep(0.1)


Alpaca._get_stream_object = get_stream_object
Alpaca._register_stream_events = lambda self: None
Alpaca._run_stream = run_stream
Alpaca._get_balances_at_broker = lambda self, quote_asset, strategy: (100_000.0, 0.0, 100_000.0)
Alpaca._pull_broker_positions = lambda self, strategy=None: []
Alpaca._pull_broker_all_orders = lambda self: []
StrategyExecutor._initialize = initialize
StrategyExecutor._run_trading_session = run_trading_session

from algo_trading.__main__ import main  # noqa: E402

sys.argv = ["algo-trading", "live"]
main()
