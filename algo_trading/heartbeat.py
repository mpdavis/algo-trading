"""The heartbeat file, kept free of LumiBot so the container healthcheck can
read it without importing it."""

from __future__ import annotations

from pathlib import Path


def heartbeat(data_dir: Path) -> Path:
    return data_dir / "heartbeat"
