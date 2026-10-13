from datetime import datetime, time

import pytest

from strategies.orb.rules import Bar, Params, Range, Side, breakout, finished, opening_range, shares, stop_price

P = Params()


def bar(hhmm, low, high, close=None, day=13):
    h, m = map(int, hhmm.split(":"))
    mid = close if close is not None else (low + high) / 2
    return Bar(datetime(2026, 10, day, h, m), mid, high, low, mid)


def test_range_times_come_from_the_parameters():
    assert P.range_end == time(9, 35)
    assert Params(range_minutes=15).range_end == time(9, 45)
    assert (P.last_entry_time, P.flatten_time) == (time(15, 0), time(15, 50))


def test_finished_keeps_only_bars_that_have_closed():
    bars = [bar("09:30", 1, 2), bar("09:31", 1, 2)]
    assert finished(bars, datetime(2026, 10, 13, 9, 31, 59)) == bars[:1]
    assert finished(bars, datetime(2026, 10, 13, 9, 32)) == bars


def test_opening_range_is_the_first_five_minutes():
    bars = [bar("09:30", 100, 101), bar("09:31", 99.5, 100.5), bar("09:34", 100, 101.5),
            bar("09:35", 90, 110)]
    assert opening_range(bars, P) == Range(101.5, 99.5)


def test_opening_range_waits_until_the_window_is_over():
    bars = [bar(f"09:3{i}", 100, 101) for i in range(5)]
    assert opening_range(bars, P) is None
    assert opening_range([], P) is None


def test_opening_range_ignores_yesterday():
    bars = [bar("09:30", 50, 200, day=12), bar("09:30", 100, 101), bar("09:35", 100, 101)]
    assert opening_range(bars, P) == Range(101, 100)


@pytest.mark.parametrize("close,side", [(102.0, Side.LONG), (98.0, Side.SHORT), (101.0, None), (100.0, None)])
def test_breakout_needs_a_close_outside_the_range(close, side):
    assert breakout(Range(101, 100), close) is side


def test_stop_is_the_far_side_of_the_range():
    r = Range(101.234, 99.876)
    assert stop_price(r, Side.LONG) == 99.88
    assert stop_price(r, Side.SHORT) == 101.23


def test_shares_risk_one_percent_of_equity():
    # $1,000 at risk over $5 a share is 200 shares, $20,400: under the 25% cap.
    assert shares(100_000, 102, 97, P) == 200
    assert shares(100_000, 97, 102, P) == 200


def test_shares_capped_at_max_position():
    # A tight range would risk $1,000 with 10,000 shares; the cap allows 245.
    assert shares(100_000, 102, 101.9, P) == 245


def test_shares_zero_without_risk():
    assert shares(100_000, 100, 100, P) == 0
    assert shares(100_000, 0, 1, P) == 0
