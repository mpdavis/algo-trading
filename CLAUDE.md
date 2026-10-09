# CLAUDE.md

Guidance for Claude Code when working in this repository. What the trader does
is in `README.md`.

## Where it runs

This repo only builds the image. The stack lives in
[mpdavis/homelab](https://github.com/mpdavis/homelab) under
`docker/apps/trading/`, which doco-cd deploys to the compose host.

## Layout

- `strategy.py`: the rules, with no broker or data source.
- `session.py`: one trading day against a `Broker` protocol: reconcile
  positions, decide each pair, place orders, save state.
- `lumibot_strategy.py`: the only code that knows LumiBot. `LumibotBroker`
  adapts a running strategy to the `Broker` protocol, so backtests and live
  trading go through the same `session.run_session`.

## Release flow

1. Merge to `main` → `build.yml` publishes `ghcr.io/mpdavis/pairs-trader:1.0.<run_number>`
   (only when the image's build context changed; PRs build but never push).
2. Renovate in homelab sees the new tag and opens the pin bump there.

## Checks

- `Tests / test` is the required check on `main`. It has no `paths` filter on
  purpose; a required check that gets skipped blocks the PR forever.
- Run the suite on the Python the image ships (`Dockerfile` `FROM`):
  `pip install -e ".[dev]" && pytest -q`. LumiBot's dependencies run to about
  2 GB, so a container is easier than a local install.
