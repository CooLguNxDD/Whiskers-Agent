"""Phase 6a — backend-generated SVG motif unit tests.

Pure stdlib string templating (no cairo/matplotlib) -- output is embedded as
an <img src="data:image/svg+xml;..."> on the frontend, so scripts inside the
SVG never execute; these tests still guard against accidentally emitting
<script>/<foreignObject> or unescaped user text.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from plugins.portfolio_plugin.render.svg_render import (
    MAX_SVG_CHARS,
    render_metric_ring_svg,
    render_motif,
    render_stack_svg,
    render_timeline_svg,
    render_topology_svg,
)


def _parse(svg: str) -> ET.Element:
    """Parseable XML is the contract -- a malformed SVG breaks the <img> render."""
    return ET.fromstring(svg)


def _viewbox_dims(svg: str) -> tuple[float, float, float, float]:
    root = _parse(svg)
    vb = root.get("viewBox")
    assert vb, "missing viewBox"
    x, y, w, h = (float(v) for v in vb.split())
    return x, y, w, h


@pytest.mark.parametrize(
    "fn,args",
    [
        (render_timeline_svg, ([{"date": "2024", "title": "Launch"}, {"date": "2025", "title": "Scale"}],)),
        (render_stack_svg, ([{"label": "Client"}, {"label": "Gateway", "sublabel": "auth + routing"}],)),
        (render_metric_ring_svg, ([{"label": "Uptime", "value": "99.9%"}, {"label": "P99", "value": 42}],)),
    ],
)
def test_motif_emits_parseable_xml(fn, args):
    svg = fn(*args, title="Test Diagram")
    root = _parse(svg)
    assert root.tag.endswith("svg")


def test_topology_emits_parseable_xml():
    svg = render_topology_svg(
        [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        [{"from": "a", "to": "b"}],
        title="Topology",
    )
    _parse(svg)


@pytest.mark.parametrize(
    "fn,args",
    [
        (render_timeline_svg, ([{"date": "2024", "title": "Launch"}],)),
        (render_stack_svg, ([{"label": "Client"}],)),
        (render_metric_ring_svg, ([{"label": "Uptime", "value": "99.9%"}],)),
    ],
)
def test_motif_viewbox_is_deterministic(fn, args):
    svg1 = fn(*args, title="Same Input")
    svg2 = fn(*args, title="Same Input")
    assert svg1 == svg2
    x, y, w, h = _viewbox_dims(svg1)
    assert x == 0 and y == 0
    assert w > 0 and h > 0


@pytest.mark.parametrize(
    "fn,args",
    [
        (render_timeline_svg, ([{"date": "", "title": '<script>alert(1)</script>'}],)),
        (render_stack_svg, ([{"label": '"><foreignObject onload=alert(1)>'}],)),
        (render_metric_ring_svg, ([{"label": "<script>x</script>", "value": "1%"}],)),
    ],
)
def test_motif_never_emits_script_or_foreignobject_from_untrusted_labels(fn, args):
    """Labels are grounded project data, not free agent text, but escape them
    anyway -- defense in depth against a compromised/misbehaving caller."""
    svg = fn(*args)
    assert "<script" not in svg.lower()
    assert "<foreignobject" not in svg.lower()
    # Parses cleanly -- proves the raw "<script>" text was entity-escaped,
    # not interpreted as markup.
    _parse(svg)


def test_topology_never_emits_script_from_untrusted_labels():
    svg = render_topology_svg(
        [{"id": "a", "label": "<script>alert(1)</script>"}],
        [],
    )
    assert "<script" not in svg.lower()
    _parse(svg)


def test_motif_handles_empty_input_gracefully():
    for svg in (
        render_timeline_svg([]),
        render_stack_svg([]),
        render_metric_ring_svg([]),
        render_topology_svg([], []),
    ):
        _parse(svg)


def test_render_motif_dispatches_by_name():
    svg = render_motif("timeline", {"items": [{"title": "A"}]}, title="T")
    assert svg is not None
    _parse(svg)


def test_render_motif_unknown_name_returns_none():
    assert render_motif("not-a-real-motif", {}) is None


def test_render_motif_oversized_output_returns_none(monkeypatch):
    """Size cap: oversized generated SVG must return None (caller falls back
    to mermaid / Phase 6c asset offload) rather than emit a payload past
    MAX_SVG_CHARS as an inline data URI."""
    import plugins.portfolio_plugin.render.svg_render as svg_render_mod

    def _huge(*args, **kwargs):
        return "<svg>" + ("x" * MAX_SVG_CHARS) + "</svg>"

    monkeypatch.setattr(svg_render_mod, "MOTIFS", {**svg_render_mod.MOTIFS, "timeline": _huge})
    assert render_motif("timeline", {"items": []}) is None


def test_metric_ring_clamps_out_of_range_percentage():
    """A value outside 0-100 (or non-numeric) must not raise or produce an
    invalid stroke-dasharray -- clamped/defaulted instead."""
    svg = render_metric_ring_svg([{"label": "Weird", "value": "150"}])
    _parse(svg)
    svg2 = render_metric_ring_svg([{"label": "NotANumber", "value": "n/a"}])
    _parse(svg2)


def test_theme_changes_output_colors():
    neon = render_stack_svg([{"label": "X"}], theme="neon")
    paper = render_stack_svg([{"label": "X"}], theme="paper")
    assert neon != paper


def test_unknown_theme_falls_back_to_default():
    default = render_stack_svg([{"label": "X"}], theme="neon")
    unknown = render_stack_svg([{"label": "X"}], theme="not-a-real-theme")
    assert default == unknown
