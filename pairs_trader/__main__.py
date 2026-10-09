from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime


def strategy_parameters() -> dict:
    from .lumibot_strategy import PairsStrategy

    params = dict(PairsStrategy.parameters)
    for key, cast in (("pairs", str), ("lookback", int), ("entry_z", float), ("exit_z", float),
                      ("stop_z", float), ("max_hold_days", int), ("pair_gross", float)):
        if key.upper() in os.environ:
            params[key] = cast(os.environ[key.upper()])
    return params


def cmd_live() -> None:
    from lumibot.brokers import Alpaca
    from lumibot.traders import Trader

    from .lumibot_strategy import PairsStrategy

    paper = os.environ.get("ALPACA_IS_PAPER", "true").lower() != "false"
    if not paper and os.environ.get("ALLOW_LIVE_TRADING") != "yes":
        sys.exit("ALPACA_IS_PAPER=false trades real money; also set ALLOW_LIVE_TRADING=yes to confirm")
    broker = Alpaca({
        "API_KEY": os.environ["ALPACA_API_KEY"],
        "API_SECRET": os.environ["ALPACA_API_SECRET"],
        "PAPER": paper,
    })
    params = strategy_parameters()
    params["state_file"] = os.environ.get("STATE_FILE", "/data/state.json")
    params["heartbeat_file"] = os.environ.get("HEARTBEAT_FILE", "/tmp/heartbeat")
    trader = Trader()
    trader.add_strategy(PairsStrategy(broker=broker, parameters=params))
    # LumiBot configures only its own loggers, so without a handler here the
    # session's z-scores, decisions and orders never reach the container log.
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s: %(message)s"))
    ours = logging.getLogger("pairs_trader")
    ours.addHandler(handler)
    ours.setLevel(logging.INFO)
    ours.propagate = False
    trader.run_all()


def cmd_backtest(start: str, end: str, budget: float) -> None:
    from lumibot.backtesting import YahooDataBacktesting

    from .lumibot_strategy import PairsStrategy

    out = PairsStrategy.run_backtest(
        YahooDataBacktesting,
        datetime.fromisoformat(start),
        datetime.fromisoformat(end),
        budget=budget,
        benchmark_asset="SPY",
        parameters=strategy_parameters(),
        show_plot=False,
        show_tearsheet=False,
        save_tearsheet=False,
        show_indicators=False,
        show_progress_bar=False,
    )
    results = out[0] if isinstance(out, tuple) else out
    for key in ("total_return", "cagr", "volatility", "sharpe", "max_drawdown", "romad"):
        value = (results or {}).get(key)
        if isinstance(value, dict):
            value = value.get("drawdown")
        if value is not None:
            print(f"{key:13} {float(value):.4f}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="pairs-trader")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("live", help="trade the Alpaca account through LumiBot")
    bt = sub.add_parser("backtest", help="replay the strategy in LumiBot on Yahoo daily bars")
    bt.add_argument("--start", required=True)
    bt.add_argument("--end", default=datetime.now().date().isoformat())
    bt.add_argument("--budget", type=float, default=100_000.0)
    args = ap.parse_args()

    if args.cmd == "live":
        cmd_live()
    else:
        cmd_backtest(args.start, args.end, args.budget)


if __name__ == "__main__":
    main()
