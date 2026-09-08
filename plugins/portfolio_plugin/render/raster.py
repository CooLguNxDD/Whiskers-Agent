"""SVG -> PNG rasterization for baked portfolio assets (Phase 6c).

Uses ``cairosvg`` (~2 apt libs: libcairo2, libpangocairo-1.0-0) rather than
Playwright/chromium (~20 apt libs, hundreds of MB) -- the only thing this
needs to rasterize is the deterministic markup ``svg_render.py`` already
produces, not a full browser. Feeding the same svg_render output makes
raster a derivative of the vector path: one drawing implementation, not two.

Fails open (returns None, never raises) when cairosvg isn't installed --
e.g. before the Docker image has been rebuilt with the new dependency --
so callers degrade to "no raster available" rather than crashing.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("whiskers.plugins.portfolio")

_cairosvg_available: bool | None = None


def cairosvg_available() -> bool:
    """Cheap, cached import probe."""
    global _cairosvg_available
    if _cairosvg_available is None:
        try:
            import cairosvg  # noqa: F401

            _cairosvg_available = True
        except Exception:
            _cairosvg_available = False
    return _cairosvg_available


def svg_to_png_bytes(svg: str, *, width: int | None = None) -> bytes | None:
    """Rasterize an SVG string to PNG bytes. Returns None (never raises) if
    cairosvg is unavailable or rasterization fails for any reason."""
    if not svg or not cairosvg_available():
        return None
    try:
        import cairosvg

        return cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=width)
    except Exception:
        logger.warning("svg_to_png_bytes: rasterization failed", exc_info=True)
        return None
