from datetime import date

from strategies.pairs import status
from strategies.pairs.rules import Side
from strategies.pairs.session import Decision, StateFile, Stored


def test_status_before_first_session(tmp_path):
    s = status(tmp_path)
    assert s.facts == [("Last session", "never")]
    assert s.rows == []


def test_status_shows_each_pair(tmp_path):
    StateFile(tmp_path / "state.json").save(
        date(2026, 10, 9),
        {
            "JBHT/KNX": Stored(side=Side.LONG, entered=date(2026, 10, 7)),
            "UPS/ODFL": Stored(cooldown=True),
        },
        {
            "JBHT/KNX": Decision(z=-1.2, beta=0.81, action="hold"),
            "UPS/ODFL": Decision(z=3.71, beta=0.42, action="hold"),
        },
    )
    s = status(tmp_path)
    assert s.facts == [("Last session", "2026-10-09")]
    assert s.rows == [
        ["JBHT/KNX", "-1.20", "0.810", "hold", "long spread since 2026-10-07"],
        ["UPS/ODFL", "+3.71", "0.420", "hold", "flat (cooling down)"],
    ]
