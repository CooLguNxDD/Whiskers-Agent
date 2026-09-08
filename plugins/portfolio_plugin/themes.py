"""Shared layout theme vocabulary for bake, jury, design-context, and SVG motifs.

Re-exports ``utils.theme_registry`` so existing import sites stay on this module.
A new theme is a JSON file under ``frontend/cat-admin-frontend/src/themes/``.
"""

from __future__ import annotations

from utils.theme_registry import SUPPORTED_THEMES, public_raw_defs

THEME_VOCAB = "|".join(SUPPORTED_THEMES)

__all__ = ["SUPPORTED_THEMES", "THEME_VOCAB", "public_raw_defs"]
