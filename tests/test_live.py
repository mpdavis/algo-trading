import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from lumibot.brokers.alpaca import Alpaca

from algo_trading.heartbeat import heartbeat
from algo_trading.live import SharedAlpaca

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ("pairs", "orb", "selloff")


def start_live(tmp_path, **env):
    """`algo-trading live` on every strategy, through tests/live_harness.py."""
    return subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "live_harness.py")],
        cwd=tmp_path,  # LumiBot writes logs/ here
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={
            "PATH": "",
            "PYTHONPATH": str(ROOT),
            "HOME": str(tmp_path),
            "DATA_DIR": str(tmp_path / "data"),
            "ALPACA_API_KEY": "key",
            "ALPACA_API_SECRET": "secret",
            "STRATEGIES": ",".join(STRATEGIES),
            "PAIRS_PAIRS": "JBHT/KNX,UPS/ODFL",
            **env,
        },
    )


def collect(proc):
    lines = []
    reader = threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True)
    reader.start()
    return lines, reader


def test_live_runs_every_strategy_on_one_stream(tmp_path):
    proc = start_live(tmp_path)
    lines, reader = collect(proc)
    try:
        trading = lambda: {l.split()[-1] for l in lines if l.startswith("HARNESS trading")}  # noqa: E731
        deadline = time.monotonic() + 90
        while trading() != set(STRATEGIES) and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        assert trading() == set(STRATEGIES), "".join(lines)
        # Longer than the runner's poll, so a strategy that had stopped would
        # have taken the process down by now.
        time.sleep(3)
        assert proc.poll() is None, "".join(lines)
        assert sum(l.startswith("HARNESS stream") for l in lines) == 1, "".join(lines)
        for name in STRATEGIES:
            assert heartbeat(tmp_path / "data" / name).exists()
    finally:
        proc.kill()
        proc.wait()
        reader.join()
        proc.stdout.close()


@pytest.mark.parametrize("stage", ["initialize", "session"])
def test_live_exits_when_one_strategy_crashes(tmp_path, stage):
    proc = start_live(tmp_path, HARNESS_CRASH=f"orb:{stage}")
    try:
        out, _ = proc.communicate(timeout=90)
    finally:
        proc.kill()
    assert proc.returncode == 1, out
    assert "orb crashed; exiting" in out


@pytest.fixture
def broker(monkeypatch):
    """A SharedAlpaca whose account holds JBHT (pairs), SPY (orb) and AAPL
    (no strategy's), with no network and no stream."""
    held = [
        SimpleNamespace(symbol=s, asset_class="us_equity", qty="10", avg_entry_price="100", side="long",
                        unrealized_pl=None, current_price=None, market_value=None)
        for s in ("JBHT", "SPY", "AAPL")
    ]
    monkeypatch.setattr(Alpaca, "_get_stream_object", lambda self: None)
    monkeypatch.setattr(Alpaca, "_pull_broker_positions", lambda self, strategy=None: held)
    owners = {"JBHT": "pairs", "KNX": "pairs", "SPY": "orb"}
    return SharedAlpaca({"API_KEY": "key", "API_SECRET": "secret", "PAPER": True}, owners)


def symbols(broker, strategy):
    return {p.asset.symbol for p in broker.get_tracked_positions(strategy)}


def test_each_position_belongs_to_the_strategy_trading_its_symbol(broker):
    # Each strategy loads the account's positions as it starts.
    for name in STRATEGIES:
        broker._set_initial_positions(SimpleNamespace(name=name))
    assert len(broker.get_tracked_positions()) == 3
    # A refresh, which LumiBot runs before every get_positions(), relabels
    # every position as the caller's unless the broker stops it.
    broker.refresh_positions(SimpleNamespace(name="selloff"))
    assert symbols(broker, "pairs") == {"JBHT"}
    assert symbols(broker, "orb") == {"SPY"}
    assert symbols(broker, "selloff") == set()


def test_orders_found_at_the_broker_belong_to_the_strategy_trading_their_symbol(broker, monkeypatch):
    monkeypatch.setattr(Alpaca, "_parse_broker_order",
                        lambda self, response, strategy_name, strategy_object=None: strategy_name)
    assert broker._parse_broker_order(SimpleNamespace(symbol="SPY"), "pairs") == "orb"
    assert broker._parse_broker_order({"symbol": "knx"}, "selloff") == "pairs"
    assert broker._parse_broker_order(SimpleNamespace(symbol="AAPL"), "pairs") == "pairs"

