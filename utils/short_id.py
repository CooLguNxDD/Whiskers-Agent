"""
short_id — generates short, URL-safe, human-readable ids (e.g. ``wel_tel_successor_a7k2m9xqp1``).

Used to key job-specific portfolio layout artifacts (Feature B "bake & send").
Pure stdlib, DB-agnostic: callers inject an ``exists`` hook for collision checks.

Suffix entropy is deliberately wide (≥10 alnum chars ≈ 60 bits) so public
``?j=`` / layout ids are not enumerable from a known company/role slug.
"""

from __future__ import annotations

import re
import secrets
import string
from typing import Callable

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
# Public route regex in portfolio routes: ^[a-z0-9_]{1,80}$
_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits
_DEFAULT_SUFFIX_LEN = 10


class ShortIdCollisionError(Exception):
    """Raised when generate_short_id can't find a free id within max_attempts."""


def slugify(text: str, max_words: int = 3, max_len: int = 24) -> str:
    """Lowercase, strip non-alphanumerics, underscore-join up to max_words, cap length."""
    if not text:
        return ""
    normalized = _NON_ALNUM_RE.sub(" ", text.lower()).strip()
    words = [w for w in normalized.split(" ") if w][:max_words]
    slug = "_".join(words)
    return slug[:max_len].rstrip("_")


def _random_suffix(length: int = _DEFAULT_SUFFIX_LEN) -> str:
    """Cryptographic random suffix restricted to [a-z0-9] for route-safe ids."""
    n = max(1, int(length))
    return "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(n))


def generate_short_id(
    seed_tokens: list[str],
    suffix_len: int = _DEFAULT_SUFFIX_LEN,
    exists: Callable[[str], bool] | None = None,
    max_attempts: int = 5,
    *,
    suffix_digits: int | None = None,
) -> str:
    """Build a short id like ``wel_tel_successor_a7k2m9xqp1`` from seed tokens.

    Retries with a fresh random alnum suffix while ``exists(candidate)`` is
    True, up to ``max_attempts``. ``exists`` is optional so this stays
    unit-testable without a DB.

    ``suffix_digits`` is accepted only as a deprecated alias for ``suffix_len``
    (older call sites); prefer ``suffix_len``. Suffixes are always
    ``[a-z0-9]`` of length ≥ 10 by default (not a small decimal range).
    """
    if suffix_digits is not None:
        # Legacy kwarg name — treat as length, never as decimal digit width.
        suffix_len = max(int(suffix_digits), _DEFAULT_SUFFIX_LEN)

    base = slugify("_".join(t for t in seed_tokens if t))
    if not base:
        base = "portfolio"

    length = max(int(suffix_len), 1)
    for _ in range(max_attempts):
        suffix = _random_suffix(length)
        candidate = f"{base}_{suffix}"
        if exists is None or not exists(candidate):
            return candidate

    raise ShortIdCollisionError(
        f"could not generate a unique short id for base={base!r} after {max_attempts} attempts"
    )
