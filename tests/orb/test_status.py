from datetime import date

from strategies.orb import status
from strategies.orb.rules import Side
from strategies.orb.session import Day, State, StateFile, Trade


def test_status_before_first_session(tmp_path):
    s = status(tmp_path)
    assert s.facts == [("Session", "never")]
    assert s.rows == []


def test_status_shows_today_then_past_trades(tmp_path):
    StateFile(tmp_path / "state.json").save(State(
        today=Day(date(2026, 10, 13), 581.0, 579.5, Trade(Side.SHORT, 40, 581.0, "09:41", 579.2),
                  note="holding short 40, stop 581.00"),
        history=[
            Day(date(2026, 10, 12), 575.0, 573.0, Trade(Side.LONG, 50, 573.0, "09:36", 575.2, 578.4, "flatten"), True),
            Day(date(2026, 10, 9), 570.0, 568.0, Trade(Side.LONG, 50, 568.0, "10:02", None, None, "unfilled"), True),
        ],
    ))
    s = status(tmp_path)
    assert s.facts == [("Session", "2026-10-13"), ("Now", "holding short 40, stop 581.00")]
    assert s.rows == [
        ["2026-10-13", "579.50–581.00", "short 40 at 09:41", "579.20", "581.00", "—", "open", "—"],
        ["2026-10-12", "573.00–575.00", "long 50 at 09:36", "575.20", "573.00", "≈578.40", "flatten", "≈+160.00"],
        ["2026-10-09", "568.00–570.00", "long 50 at 10:02", "—", "568.00", "—", "unfilled", "—"],
    ]


def test_status_shows_a_day_without_a_trade(tmp_path):
    StateFile(tmp_path / "state.json").save(State(today=Day(date(2026, 10, 13), note="waiting for the opening range")))
    assert status(tmp_path).rows == [["2026-10-13", "—", "—", "—", "—", "—", "—", "—"]]
