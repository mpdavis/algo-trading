# pairs

For each pair it fits `log(Y) = alpha + beta * log(X)` over a rolling window.
When the spread stretches it buys the cheap stock and shorts the rich one,
sized so the two legs offset each other, and closes both legs together when
the spread reverts.

## Rules

Once per trading day, at the open, using the previous sessions' closes:

| Spread z-score | Flat | Long spread (long Y, short X) | Short spread (short Y, long X) |
|---|---|---|---|
| ≤ −`entry_z`, > −`stop_z` | enter long spread | | |
| ≥ `entry_z`, < `stop_z` | enter short spread | | |
| crosses back inside `exit_z` | | exit | exit |
| beyond `stop_z` against the trade | | exit, cool down | exit, cool down |
| held `max_hold_days` sessions | | exit, cool down | exit, cool down |

A pair in cooldown is not traded again until its spread is back inside
`exit_z`. Each pair gets `pair_gross` of account equity, split between the legs
as `1 : beta`. Live shorts need the stock to be shortable and easy to borrow.

Account positions are the source of truth. `state.json` only keeps each pair's
entry day, its cooldown, the last session traded, and the last decision (for
the dashboard). Every session reconciles: a pair closed by hand starts a
cooldown, a pair found open is adopted, and a pair with one leg missing is
flattened. A session waits while any of its orders are still open.

## Parameters

| Parameter | Default | Environment |
|---|---|---|
| `pairs` | `JBHT/KNX,UPS/ODFL` | `PAIRS_PAIRS` (`Y/X`, comma-separated; a symbol in one pair only) |
| `lookback` | `60` | `PAIRS_LOOKBACK` (sessions in the fit window) |
| `entry_z` / `exit_z` / `stop_z` | `2.0` / `0.5` / `3.5` | `PAIRS_ENTRY_Z`, … |
| `max_hold_days` | `20` | `PAIRS_MAX_HOLD_DAYS` (sessions) |
| `pair_gross` | `0.5` | `PAIRS_PAIR_GROSS` (share of equity per pair, both legs) |

## Layout

- `rules.py`: the rules, with no broker or data source.
- `session.py`: one trading day against a `Broker` protocol: reconcile
  positions, decide each pair, place orders, save state.
- `strategy.py`: the LumiBot strategy. `LumibotBroker` adapts it to the
  `Broker` protocol, so backtests and live trading run the same session code.

Backtests fill at the open and do not charge borrow fees. Paper accounts do not
charge them either; live accounts do.
