from datetime import datetime, timezone
from types import SimpleNamespace

from algo_trading.base import Status, heartbeat
from algo_trading.dashboard import create_app, sparkline
from algo_trading.registry import Entry
from strategies.pairs import PairsStrategy


class FakeAlpaca:
    def __init__(self, fail=()):
        self.fail = set(fail)

    def _check(self, what):
        if what in self.fail:
            raise RuntimeError(f"{what} unavailable")

    def get_account(self):
        self._check("account")
        return SimpleNamespace(equity="101250.50", last_equity="100000", cash="40000", buying_power="250000",
                               long_market_value="30000", short_market_value="-29000")

    def get_all_positions(self):
        self._check("positions")
        return [
            SimpleNamespace(symbol="JBHT", qty="120", avg_entry_price="150", current_price="155",
                            market_value="18600", unrealized_pl="600", unrealized_plpc="0.0333"),
            SimpleNamespace(symbol="KNX", qty="-300", avg_entry_price="50", current_price="51",
                            market_value="-15300", unrealized_pl="-300", unrealized_plpc="-0.02"),
        ]

    def get_orders(self, request):
        self._check("orders")
        return [SimpleNamespace(filled_at=datetime(2026, 10, 9, 13, 31, tzinfo=timezone.utc), submitted_at=None,
                                symbol="KNX", side=SimpleNamespace(value="sell"), qty="300", filled_qty="300",
                                filled_avg_price="50", status=SimpleNamespace(value="filled"))]

    def get_portfolio_history(self, request):
        self._check("history")
        return SimpleNamespace(timestamp=[1788912000, 1788998400, 1789084800], equity=[100000.0, 99500.0, 101250.5])


def entry_with(status):
    return Entry("demo", PairsStrategy, status, "A demo strategy.")


def client(app):
    app.config["TESTING"] = True
    return app.test_client()


def test_page_shows_account_holdings_orders_and_strategies(tmp_path):
    (tmp_path / "demo").mkdir()
    heartbeat(tmp_path / "demo").touch()
    status = Status(facts=[("Last session", "2026-10-09")], columns=["Pair", "z-score"], rows=[["JBHT/KNX", "-2.31"]])
    app = create_app(lambda: FakeAlpaca(), [entry_with(lambda d: status)], tmp_path)

    page = client(app).get("/").get_data(as_text=True)

    assert "$101,250.50" in page
    assert "+$1,250.50 today (+1.25%)" in page
    assert "$29,000.00" in page  # short exposure shown as a positive amount
    assert "JBHT" in page and "-$15,300.00" in page and "short" in page
    assert "filled" in page and "2026-10-09 13:31" in page
    assert "<polyline" in page
    assert "Running" in page and "A demo strategy." in page
    assert "JBHT/KNX" in page and "-2.31" in page
    assert "Paper" in page


def test_one_failing_call_leaves_the_rest_of_the_page(tmp_path):
    app = create_app(lambda: FakeAlpaca(fail={"positions", "history"}), [entry_with(None)], tmp_path)
    page = client(app).get("/").get_data(as_text=True)
    assert "Positions: positions unavailable" in page
    assert "Equity history: history unavailable" in page
    assert "$101,250.50" in page
    assert "Not running" in page


def test_broken_strategy_status_is_reported_not_raised(tmp_path):
    (tmp_path / "demo").mkdir()

    def broken(data_dir):
        raise ValueError("bad state file")

    page = client(create_app(lambda: FakeAlpaca(), [entry_with(broken)], tmp_path)).get("/").get_data(as_text=True)
    assert "Status unavailable: bad state file" in page


def test_live_account_is_flagged(tmp_path):
    app = create_app(lambda: FakeAlpaca(), [], tmp_path, {"ALPACA_IS_PAPER": "false"})
    assert "Live money" in client(app).get("/").get_data(as_text=True)


def test_healthz(tmp_path):
    assert client(create_app(lambda: FakeAlpaca(), [], tmp_path)).get("/healthz").data == b"ok"


def test_sparkline_spans_the_box():
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    coords = sparkline([(t, 10.0), (t, 20.0), (t, 15.0)], width=100, height=50, pad=0).split()
    assert coords == ["0.0,50.0", "50.0,0.0", "100.0,25.0"]
    assert sparkline([(t, 1.0)]) == ""
