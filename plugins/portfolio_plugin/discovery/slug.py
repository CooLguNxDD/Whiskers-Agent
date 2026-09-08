"""Slug generation + cross-format slug/ref/title matching.

Two different needs share this module:

- ``canonical_slug`` — hyphen-joined slug for *new* discovery-created project
  rows (matches the hand-authored seed convention, e.g. ``cat-portfolio``).
  Existing rows are never migrated/renamed.
- ``normalize_key`` — alnum-only lowercase key used to compare a project slug
  against an indexed doc's ``slug_hint`` / ``ref`` / ``title`` / link href
  regardless of hyphen vs underscore vs no-separator formatting. This is the
  actual fix for the index↔DB bridge: no slug *generation* rule can make
  ``oct`` equal ``opencat-mcp-full``, so matching also falls back to comparing
  against the project's own ``context_sources[].ref`` / ``links[].href``.
"""

from __future__ import annotations

import re

from utils.short_id import slugify

_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def canonical_slug(text: str, *, max_words: int = 8, max_len: int = 48) -> str:
    """Hyphen-joined slug for new discovery-created project rows."""
    s = slugify(text or "", max_words=max_words, max_len=max_len) or "project"
    return s.replace("_", "-")


def normalize_key(text: str | None) -> str:
    """Alnum-only lowercase key for cross-format slug/ref/title/href matching."""
    return _ALNUM_RE.sub("", (text or "").lower())


def keys_match(slug: str | None, *candidates: str | None) -> bool:
    """True when ``slug``'s normalized key is a substring match against any candidate."""
    sk = normalize_key(slug)
    if not sk:
        return False
    for cand in candidates:
        ck = normalize_key(cand)
        if not ck:
            continue
        if sk == ck or (len(sk) > 4 and sk in ck) or (len(ck) > 4 and ck in sk):
            return True
    return False
