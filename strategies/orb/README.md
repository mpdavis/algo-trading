# orb

An intraday opening range breakout on SPY. It marks the high and low of the
first five minutes of the session, then trades the first minute that closes
outside that range: long above it, short below it. The stop sits at the far
side of the range, and whatever is still open is closed at 15:50. Nothing is
held overnight, and there is at most one trade a day.

It is here mainly to exercise the paper account every day: an entry, a stop
order, and either a stop-out or the end-of-day close. Do not expect an edge
from it. Published tests of this rule (Zarattini and Aziz, *Can Day Trading
Really Be Profitable?*, 2023) found one on QQQ with leverage and a different
entry, not on SPY as configured here.

## Rules

Every minute from the open, on finished one-minute bars (a bar starting 10:04
counts once it closes at 10:05):

| Step | Rule |
|---|---|
| Range | high and low of the bars from 09:30 to 09:30 + `range_minutes` |
| Entry | the first close above the range buys, the first close below it sells short, as a market order; none at or after `last_entry` |
| Stop | the range's low under a long, its high over a short, as a stop order good for the day, placed once the entry fills |
| Exit | the stop, or a market close at `flatten_at` |
| Size | risk `risk_per_trade` of equity between the last close and the stop, at most `max_position` of equity |
| Once a day | after a trade ends, or `last_entry` passes, nothing more until tomorrow |

Live, the minute bars come straight from Alpaca's IEX feed, which is free and
real-time but carries only IEX's own trades. LumiBot's Alpaca data delays
stock bars by 16 minutes to suit the free plan, which would put every decision
a quarter of an hour late. Backtests use LumiBot's bars.

A short needs SPY to be shortable and easy to borrow, as checked with Alpaca.
With the defaults the range is usually well under 1% of SPY's price, so
`max_position` sets the size, not `risk_per_trade`.

LumiBot stops iterating shortly before the close (at 15:54 in backtests, 15:58
live) and calls `before_market_closes`, which flattens too, so a `flatten_at`
later than that still never leaves a position open overnight.

Account positions are the source of truth. `state.json` keeps today's range,
today's trade (side, size, stop, fill, and how it ended) and the last 20 days
that traded, for the dashboard. Every step reconciles: a fill gets its stop, a
position gone flat ends the trade (the stop filled, or it was closed by hand),
a stop with no position under it is cancelled, and a SPY position that is not
today's trade (left over from another day, or opened by hand) is closed. A step
waits while an entry or a close is still working. Today's trade is saved before
its order is sent, so a crash in between cannot leave a position the strategy
does not recognise as its own.

It only ever reads, orders, cancels or closes its own symbol, and never
cancels or liquidates account-wide.

## Parameters

| Parameter | Default | Environment |
|---|---|---|
| `symbol` | `SPY` | `ORB_SYMBOL` |
| `range_minutes` | `5` | `ORB_RANGE_MINUTES` |
| `last_entry` / `flatten_at` | `15:00` / `15:50` | `ORB_LAST_ENTRY`, `ORB_FLATTEN_AT` (New York time, `HH:MM`) |
| `risk_per_trade` | `0.01` | `ORB_RISK_PER_TRADE` (share of equity lost at the stop) |
| `max_position` | `0.25` | `ORB_MAX_POSITION` (share of equity, long or short) |

## Layout

- `rules.py`: the range, the breakout, the stop and the size, with no broker
  or data source.
- `session.py`: one step against a `Broker` protocol: reconcile, flatten,
  keep the stop, or look for the breakout; save state.
- `strategy.py`: the LumiBot strategy, iterating every minute.
  `LumibotBroker` adapts it to the `Broker` protocol, so backtests and live
  trading run the same session code.

## Backtest

It needs minute bars, which Yahoo keeps for only a few days, so backtest it on
Alpaca's with paper keys:

```sh
ALPACA_API_KEY=… ALPACA_API_SECRET=… algo-trading backtest orb --data alpaca --start 2026-07-01 --end 2026-10-01
```

Checked end to end in LumiBot on Yahoo's 1-minute SPY bars for
2026-10-06 to 2026-10-13 (six sessions, through 11:21 on the last): six
trades, three long and three short, all 32 shares (the 25% cap). Four hit
their stop and two were flattened at 15:50. Every fill got its stop within a
minute, and nothing was held overnight. That is a check of the mechanics, not
of the strategy.
