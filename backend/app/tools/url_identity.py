"""Canonical resource identity without discarding meaningful query data."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def normalize_resource_url(url: str) -> str:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port is not None and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    return urlunsplit(
        (scheme, host, parsed.path or "/", parsed.query, "")
    )


def url_fragment(url: str) -> str | None:
    return urlsplit(url).fragment or None
