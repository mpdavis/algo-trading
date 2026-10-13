"""Which build is running, from the APP_VERSION and APP_REVISION the image is
built with (see the Dockerfile). Outside an image both are unset."""

from __future__ import annotations

from collections.abc import Mapping


def version(environ: Mapping[str, str]) -> str:
    """The image tag and short commit, e.g. "1.0.14 (75df87d)"; "dev" when
    not running from a published image."""
    tag = environ.get("APP_VERSION") or "dev"
    revision = environ.get("APP_REVISION") or ""
    if revision and revision != "unknown":
        return f"{tag} ({revision[:7]})"
    return tag
