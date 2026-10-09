from types import SimpleNamespace

import pytest

from algo_trading.base import ManagedStrategy
from algo_trading.heartbeat import heartbeat


def fake(tmp_path, iterate, backtesting=False):
    return SimpleNamespace(name="demo", data_dir=tmp_path, is_backtesting=backtesting, iterate=iterate)


def test_heartbeat_follows_a_completed_iteration(tmp_path):
    ManagedStrategy.on_trading_iteration(fake(tmp_path, lambda: None))
    assert heartbeat(tmp_path).exists()


def test_failed_live_iteration_is_logged_and_skips_the_heartbeat(tmp_path, caplog):
    def boom():
        raise RuntimeError("broker down")

    ManagedStrategy.on_trading_iteration(fake(tmp_path, boom))
    assert not heartbeat(tmp_path).exists()
    assert "demo: iteration failed" in caplog.text


def test_failed_backtest_iteration_raises(tmp_path):
    def boom():
        raise RuntimeError("bad data")

    with pytest.raises(RuntimeError):
        ManagedStrategy.on_trading_iteration(fake(tmp_path, boom, backtesting=True))
