"""A read-only page over the Alpaca account and the strategies' state: balance,
holdings, recent orders, the equity curve, and what each strategy last did."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, render_template

from .base import Status, heartbeat
from .registry import Entry

log = logging.getLogger(__name__)


@dataclass
class StrategyView:
    name: str
    summary: str
    last_iteration: datetime | None
    status: Status | None
    error: str | None = None


@dataclass
class Snapshot:
    account: dict[str, float] = field(default_factory=dict)
    positions: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    strategies: list[StrategyView] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _f(value: Any) -> float:
    return float(value) if value not in (None, "") else 0.0


def account_snapshot(client: Any) -> Snapshot:
    """Everything the page shows from Alpaca. Each section fails on its own, so
    one bad call leaves the rest of the page intact."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest, GetPortfolioHistoryRequest

    snap = Snapshot()
    try:
        a = client.get_account()
        snap.account = {
            "equity": _f(a.equity),
            "last_equity": _f(a.last_equity),
            "cash": _f(a.cash),
            "buying_power": _f(a.buying_power),
            "long_value": _f(a.long_market_value),
            # Alpaca reports short market value as a negative number.
            "short_value": abs(_f(a.short_market_value)),
        }
    except Exception as err:
        snap.errors.append(f"Account: {err}")
    try:
        snap.positions = sorted(
            (
                {
                    "symbol": p.symbol,
                    "qty": _f(p.qty),
                    "avg_entry": _f(p.avg_entry_price),
                    "price": _f(p.current_price),
                    "value": _f(p.market_value),
                    "pl": _f(p.unrealized_pl),
                    "pl_pct": _f(p.unrealized_plpc) * 100,
                }
                for p in client.get_all_positions()
            ),
            key=lambda p: -abs(p["value"]),
        )
    except Exception as err:
        snap.errors.append(f"Positions: {err}")
    try:
        orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, limit=25))
        snap.orders = [
            {
                "time": o.filled_at or o.submitted_at,
                "symbol": o.symbol,
                "side": getattr(o.side, "value", o.side),
                "qty": _f(o.qty),
                "filled_qty": _f(o.filled_qty),
                "price": _f(o.filled_avg_price) or None,
                "status": getattr(o.status, "value", o.status),
            }
            for o in orders
        ]
    except Exception as err:
        snap.errors.append(f"Orders: {err}")
    try:
        h = client.get_portfolio_history(GetPortfolioHistoryRequest(period="3M", timeframe="1D"))
        snap.equity_curve = [
            (datetime.fromtimestamp(ts, timezone.utc), float(eq))
            for ts, eq in zip(h.timestamp, h.equity)
            if eq is not None
        ]
    except Exception as err:
        snap.errors.append(f"Equity history: {err}")
    return snap


def strategy_views(entries: list[Entry], data_root: Path) -> list[StrategyView]:
    views = []
    for e in entries:
        data_dir = data_root / e.name
        beat = heartbeat(data_dir)
        last = datetime.fromtimestamp(beat.stat().st_mtime, timezone.utc) if beat.exists() else None
        view = StrategyView(e.name, e.summary, last, None)
        if e.status is not None and data_dir.exists():
            try:
                view.status = e.status(data_dir)
            except Exception as err:
                log.exception("status for %s failed", e.name)
                view.error = str(err)
        views.append(view)
    return views


def sparkline(points: list[tuple[datetime, float]], width: int = 640, height: int = 120, pad: int = 6) -> str:
    """SVG polyline coordinates for the equity curve, scaled to the box."""
    if len(points) < 2:
        return ""
    values = [v for _, v in points]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = (width - 2 * pad) / (len(values) - 1)
    return " ".join(
        f"{pad + i * step:.1f},{pad + (height - 2 * pad) * (1 - (v - lo) / span):.1f}" for i, v in enumerate(values)
    )


def create_app(client_factory: Callable[[], Any], entries: list[Entry], data_root: Path,
               environ: Mapping[str, str] | None = None) -> Flask:
    app = Flask(__name__)
    paper = (environ or {}).get("ALPACA_IS_PAPER", "true").lower() != "false"

    @app.template_filter("money")
    def money(value: float | None) -> str:
        if value is None:
            return "—"
        return f"-${abs(value):,.2f}" if value < 0 else f"${value:,.2f}"

    @app.template_filter("signed_money")
    def signed_money(value: float) -> str:
        return ("+" if value > 0 else "") + money(value)

    @app.template_filter("ago")
    def ago(value: datetime | None) -> str:
        if value is None:
            return "never"
        seconds = (datetime.now(timezone.utc) - value).total_seconds()
        for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
            if seconds >= size:
                return f"{int(seconds // size)}{unit} ago"
        return "just now"

    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.get("/")
    def index():
        snap = account_snapshot(client_factory())
        snap.strategies = strategy_views(entries, data_root)
        return render_template(
            "index.html",
            snap=snap,
            paper=paper,
            spark=sparkline(snap.equity_curve),
            now=datetime.now(timezone.utc),
        )

    return app
