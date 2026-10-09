# pairs-trader

Market-neutral pairs trading on an Alpaca account, run by
[LumiBot](https://github.com/Lumiwealth/lumibot). For each pair it fits
`log(Y) = alpha + beta * log(X)` over a rolling window, and when the spread
stretches it buys the cheap stock and shorts the rich one, sized so the two
legs offset each other. Both legs close together when the spread reverts.

## Rules

Once per trading day, at the open, using the previous sessions' closes:

| Spread z-score | Flat | Long spread (long Y, short X) | Short spread (short Y, long X) |
|---|---|---|---|
| ≤ −`ENTRY_Z`, > −`STOP_Z` | enter long spread | | |
| ≥ `ENTRY_Z`, < `STOP_Z` | enter short spread | | |
| crosses back inside `EXIT_Z` | | exit | exit |
| beyond `STOP_Z` against the trade | | exit, cool down | exit, cool down |
| held `MAX_HOLD_DAYS` sessions | | exit, cool down | exit, cool down |

A pair in cooldown is not traded again until its spread is back inside
`EXIT_Z`. Each pair gets `PAIR_GROSS` of account equity, split between the legs
as `1 : beta`. Live shorts need the stock to be shortable and easy to borrow.

Account positions are the source of truth. The state file (`STATE_FILE`) only
keeps each pair's entry day, its cooldown, and the last session traded. Every
session reconciles: a pair closed by hand starts a cooldown, a pair found open
is adopted, and a pair with one leg missing is flattened. A session waits while
any of its orders are still open.

## Commands

```sh
pairs-trader backtest --start 2024-10-09 --end 2025-10-08   # LumiBot on Yahoo daily bars, no keys needed
pairs-trader live                                           # what the container runs
```

## Configuration

| Variable | Default | |
|---|---|---|
| `ALPACA_API_KEY`, `ALPACA_API_SECRET` | required for `live` | |
| `ALPACA_IS_PAPER` | `true` | `false` also requires `ALLOW_LIVE_TRADING=yes` |
| `PAIRS` | `JBHT/KNX,UPS/ODFL` | `Y/X`, comma-separated; a symbol may appear in one pair only |
| `LOOKBACK` | `60` | sessions in the fit window |
| `ENTRY_Z` / `EXIT_Z` / `STOP_Z` | `2.0` / `0.5` / `3.5` | |
| `MAX_HOLD_DAYS` | `20` | sessions |
| `PAIR_GROSS` | `0.5` | share of equity per pair, both legs |
| `STATE_FILE` | `/data/state.json` | |
| `HEARTBEAT_FILE` | `/tmp/heartbeat` | touched after every successful iteration |

## Caveats

Backtests fill at the open and do not charge borrow fees. Paper accounts do not
charge them either; live accounts do. The trader assumes it is the only thing
trading its symbols in the account.
