"""Backend-generated SVG motifs for ``archDiagram`` (``kind: "svg"``) blocks.

Pure stdlib string templating -- no cairo/matplotlib/pillow, zero new
dependencies. Output is embedded directly as a data: URI ``<img src>`` on the
frontend (``ArchDiagram.tsx``), so scripts inside the SVG never execute
(safe by construction: an ``<img>`` never runs inline ``<script>``, and this
module never emits one anyway) and the result must stay plain, deterministic
markup.

SVG inside a data URI does NOT inherit the page's CSS custom properties, so
colors here are resolved literals per theme rather than ``var(--...)``
references -- a real limitation, not an oversight (the composite prompting
skill documents the same caveat to the agent for its own leaf colors).
"""

from __future__ import annotations

import math
from xml.sax.saxutils import escape as _xml_escape

from utils.theme_registry import THEME_REGISTRY

# SVG-local keys → CSS token names in the shared registry. Name map only —
# never a per-theme color table.
_SVG_TOKEN_MAP: dict[str, str] = {
    "bg": "bg",
    "fg": "fg",
    "muted": "fg-subtle",
    "accent": "amber",
    "accent2": "pink",
}
_SVG_DEFAULTS: dict[str, str] = {
    "bg": "#0b0f14",
    "fg": "#e8f4ff",
    "muted": "#7d93a6",
    "accent": "#39e0c8",
    "accent2": "#ff5fa8",
}
_DEFAULT_THEME = "neon"
# Hard cap before source becomes a data URI -- above this, callers should
# offload to MinIO (Phase 6c) and switch to an asset reference instead.
MAX_SVG_CHARS = 24_000


def _colors(theme: str) -> dict[str, str]:
    """Resolve SVG fill/stroke literals from the shared theme registry."""
    defn = THEME_REGISTRY.get(theme) or THEME_REGISTRY.get(_DEFAULT_THEME)
    hexmap = defn.hex if defn else {}
    out = dict(_SVG_DEFAULTS)
    for svg_key, css_name in _SVG_TOKEN_MAP.items():
        value = hexmap.get(css_name)
        if value:
            out[svg_key] = value
    return out


def _esc(text: str) -> str:
    return _xml_escape(str(text or ""))


def _truncate_label(text: str, max_len: int = 40) -> str:
    text = str(text or "").strip()
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def render_timeline_svg(
    items: list[dict[str, str]],
    *,
    title: str = "",
    theme: str = _DEFAULT_THEME,
) -> str:
    """Horizontal timeline: [{date, title, tag?}, ...] -> dots on a rail."""
    c = _colors(theme)
    items = [i for i in (items or []) if isinstance(i, dict) and i.get("title")][:8]
    if not items:
        items = [{"date": "", "title": "No items"}]
    w, h = 720, 200
    pad = 48
    n = len(items)
    step = (w - 2 * pad) / max(1, n - 1) if n > 1 else 0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img" aria-label="{_esc(title or "Timeline")}">',
        f'<rect width="{w}" height="{h}" fill="{c["bg"]}"/>',
    ]
    if title:
        parts.append(f'<text x="{pad}" y="28" fill="{c["fg"]}" font-family="sans-serif" font-size="16" font-weight="600">{_esc(title)}</text>')
    rail_y = h // 2 + 10
    parts.append(f'<line x1="{pad}" y1="{rail_y}" x2="{w - pad}" y2="{rail_y}" stroke="{c["muted"]}" stroke-width="2"/>')
    for i, item in enumerate(items):
        x = pad if n == 1 else pad + i * step
        parts.append(f'<circle cx="{x:.1f}" cy="{rail_y}" r="6" fill="{c["accent"]}"/>')
        label = _truncate_label(item.get("title") or "")
        date = _truncate_label(item.get("date") or "", 16)
        parts.append(f'<text x="{x:.1f}" y="{rail_y - 16}" fill="{c["fg"]}" font-family="sans-serif" font-size="12" text-anchor="middle">{_esc(label)}</text>')
        if date:
            parts.append(f'<text x="{x:.1f}" y="{rail_y + 24}" fill="{c["muted"]}" font-family="sans-serif" font-size="10" text-anchor="middle">{_esc(date)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_stack_svg(
    layers: list[dict[str, str]],
    *,
    title: str = "",
    theme: str = _DEFAULT_THEME,
) -> str:
    """Vertical stack of layers: [{label, sublabel?}, ...] -> stacked bands
    (e.g. a request path: client -> gateway -> service -> db)."""
    c = _colors(theme)
    layers = [x for x in (layers or []) if isinstance(x, dict) and x.get("label")][:8]
    if not layers:
        layers = [{"label": "No layers"}]
    w = 480
    band_h = 56
    gap = 12
    pad = 20
    n = len(layers)
    h = pad * 2 + n * band_h + (n - 1) * gap + (28 if title else 0)
    top = pad + (28 if title else 0)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img" aria-label="{_esc(title or "Stack diagram")}">',
        f'<rect width="{w}" height="{h}" fill="{c["bg"]}"/>',
    ]
    if title:
        parts.append(f'<text x="{pad}" y="24" fill="{c["fg"]}" font-family="sans-serif" font-size="16" font-weight="600">{_esc(title)}</text>')
    for i, layer in enumerate(layers):
        y = top + i * (band_h + gap)
        fill = c["accent"] if i % 2 == 0 else c["accent2"]
        parts.append(
            f'<rect x="{pad}" y="{y}" width="{w - 2 * pad}" height="{band_h}" rx="8" '
            f'fill="{fill}" fill-opacity="0.18" stroke="{fill}" stroke-width="1.5"/>'
        )
        parts.append(
            f'<text x="{w / 2:.0f}" y="{y + band_h / 2 - 4:.0f}" fill="{c["fg"]}" '
            f'font-family="sans-serif" font-size="14" font-weight="600" text-anchor="middle">{_esc(_truncate_label(layer.get("label") or ""))}</text>'
        )
        sub = layer.get("sublabel")
        if sub:
            parts.append(
                f'<text x="{w / 2:.0f}" y="{y + band_h / 2 + 14:.0f}" fill="{c["muted"]}" '
                f'font-family="sans-serif" font-size="11" text-anchor="middle">{_esc(_truncate_label(sub, 60))}</text>'
            )
        if i < n - 1:
            arrow_y = y + band_h + gap / 2
            parts.append(f'<line x1="{w / 2:.0f}" y1="{y + band_h}" x2="{w / 2:.0f}" y2="{arrow_y + 4:.0f}" stroke="{c["muted"]}" stroke-width="1.5"/>')
    parts.append("</svg>")
    return "".join(parts)


def render_metric_ring_svg(
    metrics: list[dict[str, str]],
    *,
    title: str = "",
    theme: str = _DEFAULT_THEME,
) -> str:
    """Row of ring/donut metric gauges: [{label, value (0-100 or "N%")}, ...]."""
    c = _colors(theme)
    metrics = [m for m in (metrics or []) if isinstance(m, dict) and m.get("label")][:4]
    if not metrics:
        metrics = [{"label": "No metrics", "value": 0}]
    ring_r = 44
    cell_w = 140
    pad = 20
    n = len(metrics)
    w = pad * 2 + n * cell_w
    h = 160 + (28 if title else 0)
    top = 24 + (28 if title else 0)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img" aria-label="{_esc(title or "Metrics")}">',
        f'<rect width="{w}" height="{h}" fill="{c["bg"]}"/>',
    ]
    if title:
        parts.append(f'<text x="{pad}" y="24" fill="{c["fg"]}" font-family="sans-serif" font-size="16" font-weight="600">{_esc(title)}</text>')
    for i, m in enumerate(metrics):
        cx = pad + cell_w * i + cell_w / 2
        cy = top + ring_r
        raw = str(m.get("value") or "0").rstrip("%")
        try:
            pct = max(0.0, min(100.0, float(raw)))
        except ValueError:
            pct = 0.0
        circumference = 2 * math.pi * ring_r
        filled = circumference * (pct / 100.0)
        parts.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{ring_r}" fill="none" stroke="{c["muted"]}" stroke-opacity="0.25" stroke-width="10"/>')
        parts.append(
            f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{ring_r}" fill="none" stroke="{c["accent"]}" stroke-width="10" '
            f'stroke-linecap="round" stroke-dasharray="{filled:.1f} {circumference:.1f}" '
            f'transform="rotate(-90 {cx:.0f} {cy:.0f})"/>'
        )
        value_label = str(m.get("value") or "0")
        parts.append(f'<text x="{cx:.0f}" y="{cy + 5:.0f}" fill="{c["fg"]}" font-family="sans-serif" font-size="16" font-weight="700" text-anchor="middle">{_esc(_truncate_label(value_label, 8))}</text>')
        parts.append(f'<text x="{cx:.0f}" y="{cy + ring_r + 22:.0f}" fill="{c["muted"]}" font-family="sans-serif" font-size="11" text-anchor="middle">{_esc(_truncate_label(m.get("label") or "", 20))}</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_topology_svg(
    nodes: list[dict[str, str]],
    edges: list[dict[str, str]],
    *,
    title: str = "",
    theme: str = _DEFAULT_THEME,
) -> str:
    """Radial node/edge topology: nodes [{id,label}], edges [{from,to}]."""
    c = _colors(theme)
    nodes = [n for n in (nodes or []) if isinstance(n, dict) and n.get("id")][:10]
    edges = [e for e in (edges or []) if isinstance(e, dict) and e.get("from") and e.get("to")][:20]
    w, h = 480, 420
    cx, cy = w / 2, h / 2 + (14 if title else 0)
    radius = 150
    n = max(1, len(nodes))
    positions: dict[str, tuple[float, float]] = {}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img" aria-label="{_esc(title or "Topology")}">',
        f'<rect width="{w}" height="{h}" fill="{c["bg"]}"/>',
    ]
    if title:
        parts.append(f'<text x="20" y="24" fill="{c["fg"]}" font-family="sans-serif" font-size="16" font-weight="600">{_esc(title)}</text>')
    if not nodes:
        parts.append(f'<text x="{cx:.0f}" y="{cy:.0f}" fill="{c["muted"]}" font-family="sans-serif" font-size="14" text-anchor="middle">No nodes</text>')
        parts.append("</svg>")
        return "".join(parts)
    if n == 1:
        positions[str(nodes[0]["id"])] = (cx, cy)
    else:
        for i, node in enumerate(nodes):
            angle = 2 * math.pi * i / n - math.pi / 2
            positions[str(node["id"])] = (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
    edge_parts = []
    for edge in edges:
        a = positions.get(str(edge["from"]))
        b = positions.get(str(edge["to"]))
        if a and b:
            edge_parts.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" stroke="{c["muted"]}" stroke-width="1.5"/>')
    parts.extend(edge_parts)
    for node in nodes:
        x, y = positions[str(node["id"])]
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8" fill="{c["accent"]}"/>')
        parts.append(f'<text x="{x:.1f}" y="{y - 14:.1f}" fill="{c["fg"]}" font-family="sans-serif" font-size="11" text-anchor="middle">{_esc(_truncate_label(node.get("label") or node["id"], 24))}</text>')
    parts.append("</svg>")
    return "".join(parts)


MOTIFS = {
    "timeline": render_timeline_svg,
    "stack": render_stack_svg,
    "metric_ring": render_metric_ring_svg,
    "topology": render_topology_svg,
}


def render_motif(motif: str, data: dict, *, title: str = "", theme: str = _DEFAULT_THEME) -> str | None:
    """Dispatch by motif name; returns None (never raises) for an unknown
    motif or oversized output so callers can fall through to the existing
    mermaid path."""
    fn = MOTIFS.get(motif)
    if fn is None:
        return None
    try:
        if motif == "topology":
            svg = fn(data.get("nodes") or [], data.get("edges") or [], title=title, theme=theme)
        else:
            key = {"timeline": "items", "stack": "layers", "metric_ring": "metrics"}[motif]
            svg = fn(data.get(key) or [], title=title, theme=theme)
    except Exception:
        return None
    if len(svg) > MAX_SVG_CHARS:
        return None
    return svg
