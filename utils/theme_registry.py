"""Shared theme registry — Python mirror of ``src/themes/registry.ts``.

Canonical palette source is ``frontend/cat-admin-frontend/src/themes/*.theme.json``.
Consumers (portfolio plugin, TUI) look up resolved vars / hex here instead of
authoring id lists or parallel palettes.

Fail-safe, not fail-closed: a missing themes directory falls back to a tiny
built-in 3-entry hex registry so plugin boot never depends on frontend source.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("whiskers")

_MAX_EXTENDS_DEPTH = 10
_MAX_VAR_DEPTH = 8
_OKLCH_RE = re.compile(
    r"^oklch\(\s*([0-9.]+)\s+([0-9.]+)\s+(-?[0-9.]+)(?:\s*/\s*[0-9.]+)?\s*\)$",
    re.IGNORECASE,
)
_VAR_RE = re.compile(
    r"^var\(\s*--([a-zA-Z0-9-]+)(?:\s*,\s*(.+))?\s*\)$",
)
_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_PREFERRED_ORDER = (
    "cozy",
    "neon",
    "paper",
    "latte",
    "frappe",
    "macchiato",
    "mocha",
)

# Last-resort hex when frontend JSON is absent (today's SVG literals + TUI keys).
_FALLBACK_HEX: dict[str, dict[str, str]] = {
    "cozy": {
        "bg": "#1c1712",
        "fg": "#f3e9dc",
        "fg-subtle": "#a08f7a",
        "amber": "#e8a33d",
        "pink": "#e8895d",
        "cyan": "#e8a33d",
        "neon": "#e8a33d",
        "warn": "#e8a33d",
        "danger": "#e8895d",
        "ok": "#e8a33d",
    },
    "neon": {
        "bg": "#0b0f14",
        "fg": "#e8f4ff",
        "fg-subtle": "#7d93a6",
        "amber": "#39e0c8",
        "pink": "#ff5fa8",
        "cyan": "#39e0c8",
        "neon": "#39e0c8",
        "warn": "#39e0c8",
        "danger": "#ff5fa8",
        "ok": "#39e0c8",
    },
    "paper": {
        "bg": "#faf8f4",
        "fg": "#2a241c",
        "fg-subtle": "#8a8072",
        "amber": "#c77b3d",
        "pink": "#5b7a6b",
        "cyan": "#5b7a6b",
        "neon": "#5b7a6b",
        "warn": "#c77b3d",
        "danger": "#c77b3d",
        "ok": "#5b7a6b",
    },
}
_FALLBACK_META: dict[str, tuple[str, str]] = {
    "cozy": ("Cozy Cyberpunk", "warm dark"),
    "neon": ("Neon Alley", "cool dark"),
    "paper": ("Paper", "light"),
}


@dataclass
class ThemeDef:
    """Resolved theme: JSON id/label plus vars and successfully converted hex."""

    id: str
    label: str
    description: str = ""
    vars: dict[str, str] = field(default_factory=dict)
    hex: dict[str, str] = field(default_factory=dict)


def _theme_dir() -> Path:
    """Repo-relative path to the canonical theme JSON files."""
    return Path(__file__).resolve().parents[1] / "frontend" / "cat-admin-frontend" / "src" / "themes"


def _is_valid_raw_theme(obj: object) -> bool:
    """Mirror ``registry.ts::isValidRawTheme`` — reject, don't crash."""
    if not isinstance(obj, dict):
        return False
    tid = obj.get("id")
    label = obj.get("label")
    vars_ = obj.get("vars")
    if not isinstance(tid, str) or not tid:
        return False
    if not isinstance(label, str) or not label:
        return False
    if not isinstance(vars_, dict):
        return False
    for value in vars_.values():
        if not isinstance(value, str):
            return False
    if "description" in obj and obj["description"] is not None and not isinstance(obj["description"], str):
        return False
    if "default" in obj and obj["default"] is not None and not isinstance(obj["default"], bool):
        return False
    if "extends" in obj and obj["extends"] is not None and not isinstance(obj["extends"], str):
        return False
    return True


def load_raw_themes(theme_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Glob ``*.theme.json``; skip + log malformed files."""
    directory = theme_dir if theme_dir is not None else _theme_dir()
    out: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.theme.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.error("Theme file at %s is malformed or invalid.", path)
            continue
        if not _is_valid_raw_theme(raw):
            logger.error("Theme file at %s is malformed or invalid.", path)
            continue
        out[str(raw["id"])] = raw
    return out


def resolve_theme_vars(
    theme_id: str,
    raw_themes: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Ancestors-first ``extends`` merge. Cycle / missing ancestor / max depth → own vars."""
    visited: set[str] = set()
    chain: list[dict[str, Any]] = []
    current_id: str | None = theme_id

    while current_id:
        if current_id in visited:
            logger.error(
                'Cycle detected in theme extends chain starting from theme "%s" involving theme "%s".',
                theme_id,
                current_id,
            )
            own = raw_themes.get(theme_id) or {}
            return dict(own.get("vars") or {})

        current_theme = raw_themes.get(current_id)
        if current_theme is None:
            logger.error(
                'Theme "%s" extended by another theme is missing in the registry.',
                current_id,
            )
            own = raw_themes.get(theme_id) or {}
            return dict(own.get("vars") or {})

        visited.add(current_id)
        chain.append(current_theme)

        if len(chain) > _MAX_EXTENDS_DEPTH:
            logger.error('Max inheritance depth reached resolving theme "%s".', theme_id)
            own = raw_themes.get(theme_id) or {}
            return dict(own.get("vars") or {})

        nxt = current_theme.get("extends")
        current_id = nxt if isinstance(nxt, str) and nxt else None

    resolved: dict[str, str] = {}
    for theme in reversed(chain):
        vars_ = theme.get("vars") or {}
        if isinstance(vars_, dict):
            resolved.update({k: v for k, v in vars_.items() if isinstance(v, str)})
    return resolved


def _normalize_hex(value: str) -> str | None:
    m = _HEX_RE.match(value.strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2]
    return f"#{h.lower()}"


def _srgb_encode(c: float) -> float:
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


def _oklch_components_to_hex(L: float, C: float, H: float) -> str:
    """OKLCH → OKLab → linear sRGB → gamma-encoded ``#rrggbb``."""
    hr = math.radians(H)
    a = C * math.cos(hr)
    b = C * math.sin(hr)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l = l_ ** 3
    m = m_ ** 3
    s = s_ ** 3
    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    channels = [
        max(0, min(255, int(round(_srgb_encode(ch) * 255))))
        for ch in (r, g, bl)
    ]
    return f"#{channels[0]:02x}{channels[1]:02x}{channels[2]:02x}"


def oklch_to_hex(value: str, resolved_vars: dict[str, str] | None = None) -> str | None:
    """Parse ``oklch(L C H)`` or ``#rgb``/``#rrggbb``; resolve one-level ``var(--name)``.

    Returns ``None`` on anything unparseable so callers can fall back.
    """
    if not value or not isinstance(value, str):
        return None
    return _oklch_to_hex_inner(value.strip(), resolved_vars or {}, set())


def _oklch_to_hex_inner(
    value: str,
    resolved_vars: dict[str, str],
    seen: set[str],
) -> str | None:
    hex_hit = _normalize_hex(value)
    if hex_hit is not None:
        return hex_hit

    var_m = _VAR_RE.match(value)
    if var_m:
        name = var_m.group(1)
        fallback = (var_m.group(2) or "").strip() or None
        if name in seen or len(seen) >= _MAX_VAR_DEPTH:
            return None
        nxt = resolved_vars.get(name)
        if nxt:
            seen.add(name)
            converted = _oklch_to_hex_inner(nxt.strip(), resolved_vars, seen)
            if converted is not None:
                return converted
        if fallback:
            return _oklch_to_hex_inner(fallback, resolved_vars, seen)
        return None

    oklch_m = _OKLCH_RE.match(value)
    if not oklch_m:
        return None
    try:
        L = float(oklch_m.group(1))
        C = float(oklch_m.group(2))
        H = float(oklch_m.group(3))
    except ValueError:
        return None
    try:
        return _oklch_components_to_hex(L, C, H)
    except (OverflowError, ValueError):
        return None


def _hex_map(resolved_vars: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, raw in resolved_vars.items():
        converted = oklch_to_hex(raw, resolved_vars)
        if converted is not None:
            out[key] = converted
    return out


def _ordered_ids(ids: list[str]) -> tuple[str, ...]:
    preferred = tuple(t for t in _PREFERRED_ORDER if t in ids)
    extra = tuple(sorted(t for t in ids if t not in _PREFERRED_ORDER))
    return preferred + extra


def _fallback_registry() -> dict[str, ThemeDef]:
    registry: dict[str, ThemeDef] = {}
    for tid, hexmap in _FALLBACK_HEX.items():
        label, desc = _FALLBACK_META[tid]
        registry[tid] = ThemeDef(
            id=tid,
            label=label,
            description=desc,
            vars={},
            hex=dict(hexmap),
        )
    return registry


def _build_registry(theme_dir: Path | None = None) -> dict[str, ThemeDef]:
    raw = load_raw_themes(theme_dir)
    if not raw:
        logger.error(
            "Theme directory missing or empty (%s); using built-in cozy/neon/paper fallback.",
            theme_dir if theme_dir is not None else _theme_dir(),
        )
        return _fallback_registry()

    registry: dict[str, ThemeDef] = {}
    for tid, raw_theme in raw.items():
        resolved = resolve_theme_vars(tid, raw)
        registry[tid] = ThemeDef(
            id=str(raw_theme["id"]),
            label=str(raw_theme["label"]),
            description=str(raw_theme.get("description") or ""),
            vars=resolved,
            hex=_hex_map(resolved),
        )
    return registry


def public_raw_defs(theme_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Unresolved theme files for CatPortfolio ``gen:themes`` (label/extends/vars)."""
    out: dict[str, dict[str, Any]] = {}
    for tid, raw in load_raw_themes(theme_dir).items():
        entry: dict[str, Any] = {
            "label": raw.get("label"),
            "vars": raw.get("vars") or {},
        }
        if raw.get("description") is not None:
            entry["description"] = raw.get("description")
        if raw.get("extends"):
            entry["extends"] = raw.get("extends")
        if "default" in raw and raw["default"] is not None:
            entry["default"] = raw["default"]
        out[tid] = entry
    return out


def reload_registry(theme_dir: Path | None = None) -> dict[str, ThemeDef]:
    """Rebuild module-level ``THEME_REGISTRY`` / ``SUPPORTED_THEMES`` (tests + import)."""
    global THEME_REGISTRY, SUPPORTED_THEMES
    THEME_REGISTRY = _build_registry(theme_dir)
    SUPPORTED_THEMES = _ordered_ids(list(THEME_REGISTRY.keys()))
    return THEME_REGISTRY


THEME_REGISTRY: dict[str, ThemeDef] = {}
SUPPORTED_THEMES: tuple[str, ...] = ()
reload_registry()
