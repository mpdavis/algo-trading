# CLAUDE.md

Guidance for Claude Code when working in this repository. What it does and how
to add a strategy is in `README.md`.

## Where it runs

This repo only builds the image. The stack lives in
[mpdavis/homelab](https://github.com/mpdavis/homelab) under
`docker/apps/trading/`, which doco-cd deploys to the compose host: one service
runs `live`, another runs `dashboard`, both from the same image.

## Layout

- `algo_trading/`: the framework. `base.py` (`ManagedStrategy`, `Status`, the
  heartbeat), `registry.py` (finding strategies, env parameter overrides),
  `dashboard.py` and `templates/`, and the CLI in `__main__.py`.
- `strategies/<name>/`: one LumiBot strategy each. Keep a strategy's trading
  logic free of LumiBot where it can be, as `pairs` does, so it is testable
  without a broker.
- `tests/`: framework tests at the top, one directory per strategy.

## Release flow

1. Merge to `main` → `build.yml` publishes `ghcr.io/mpdavis/algo-trading:1.0.<run_number>`
   (only when the image's build context changed; PRs build but never push).
2. Renovate in homelab sees the new tag and opens the pin bump there.

## Checks

- `Tests / test` is the required check on `main`. It has no `paths` filter on
  purpose; a required check that gets skipped blocks the PR forever.
- Run the suite on the Python the image ships (`Dockerfile` `FROM`):
  `pip install -e ".[dev]" && pytest -q`. LumiBot's dependencies run to about
  2 GB, so a container is easier than a local install.
