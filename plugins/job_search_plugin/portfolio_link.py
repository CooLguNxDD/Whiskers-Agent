"""Recruiter-safe CatPortfolio URL builder.

A bake short_id is only useful when a human can open it. ``localhost:11000``
(the local CatPortfolio docker/nginx) and empty ``CATPORTFOLIO_PUBLIC_DOMAIN``
must never be written into an application record, resume header, or cover letter.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})


def _hostname(raw: str) -> str:
    blob = (raw or "").strip().rstrip("/")
    if not blob:
        return ""
    if "://" not in blob:
        blob = f"https://{blob}"
    return (urlsplit(blob).hostname or "").lower()


def is_public_portfolio_host(domain: str) -> bool:
    """True when ``domain`` is a sendable public host (not loopback / empty)."""
    host = _hostname(domain)
    if not host:
        return False
    if host in _LOOPBACK_HOSTS:
        return False
    if host.endswith(".localhost") or host.endswith(".local"):
        return False
    return True


def public_portfolio_url(short_id: str | None, *, domain: str | None = None) -> str:
    """Return ``https://{domain}/?j={short_id}`` or ``\"\"`` when the link would be dead.

    Empty / whitespace short_id, unset domain, and localhost / loopback hosts
    all omit the link. Relative ``/?j=`` is also omitted — a recruiter cannot
    open it.
    """
    sid = (short_id or "").strip()
    if not sid:
        return ""
    raw = domain if domain is not None else os.environ.get("CATPORTFOLIO_PUBLIC_DOMAIN", "")
    raw = (raw or "").strip().rstrip("/")
    if not raw or not is_public_portfolio_host(raw):
        return ""
    if "://" in raw:
        return f"{raw}?j={sid}" if raw.endswith("/") else f"{raw}/?j={sid}"
    return f"https://{raw}/?j={sid}"


def sanitize_portfolio_url(url: str | None) -> str:
    """Pass through a caller-supplied portfolio URL only when its host is public."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("/"):
        return ""
    if not is_public_portfolio_host(raw):
        return ""
    return raw
