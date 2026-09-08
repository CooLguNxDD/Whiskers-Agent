"""H3 hex helpers with meters↔lat/lng patch projection.

Maps a game-world XZ plane onto a tiny lat/lng patch so real H3 libraries
(kRing, parent/child, polyfill) work without spherical geodesy concerns at
typical game scales (few km).

Projection (linear, equator-safe for small patches):
  lat = anchor_lat + z_meters / meters_per_degree
  lng = anchor_lng + x_meters / (meters_per_degree * cos(anchor_lat))
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import h3

# Default patch: origin at (0,0) lat/lng with ~1 degree ≈ 111.32 km.
DEFAULT_ANCHOR_LAT = 0.0
DEFAULT_ANCHOR_LNG = 0.0
DEFAULT_METERS_PER_DEGREE = 111_320.0
DEFAULT_BASE_RES = 10
# Vertical slab height for 3D hex prisms (hex_id + y_layer).
DEFAULT_LAYER_HEIGHT = 32.0


@dataclass(frozen=True)
class Projection:
    """World meters → lat/lng patch parameters for one world."""

    anchor_lat: float = DEFAULT_ANCHOR_LAT
    anchor_lng: float = DEFAULT_ANCHOR_LNG
    meters_per_degree: float = DEFAULT_METERS_PER_DEGREE
    base_res: int = DEFAULT_BASE_RES

    def meters_to_latlng(self, x: float, z: float) -> tuple[float, float]:
        """Map world (x, z) meters to (lat, lng). Y is ignored."""
        lat = self.anchor_lat + (z / self.meters_per_degree)
        cos_lat = math.cos(math.radians(self.anchor_lat))
        # Avoid div-by-zero near poles (we default to equator).
        scale_x = self.meters_per_degree * max(abs(cos_lat), 1e-6)
        lng = self.anchor_lng + (x / scale_x)
        return lat, lng

    def latlng_to_meters(self, lat: float, lng: float) -> tuple[float, float]:
        """Inverse of meters_to_latlng → (x, z)."""
        z = (lat - self.anchor_lat) * self.meters_per_degree
        cos_lat = math.cos(math.radians(self.anchor_lat))
        scale_x = self.meters_per_degree * max(abs(cos_lat), 1e-6)
        x = (lng - self.anchor_lng) * scale_x
        return x, z


def cell(x: float, z: float, proj: Projection | None = None, res: int | None = None) -> str:
    """Return H3 cell id for world-space (x, z) at the given resolution."""
    p = proj or Projection()
    r = p.base_res if res is None else res
    lat, lng = p.meters_to_latlng(x, z)
    return h3.latlng_to_cell(lat, lng, r)


def y_layer(y: float, layer_height: float = DEFAULT_LAYER_HEIGHT) -> int:
    """Discrete vertical layer: floor(y / layer_height). Negative-Y safe."""
    h = float(layer_height) if layer_height and layer_height > 0 else DEFAULT_LAYER_HEIGHT
    return int(math.floor(float(y) / h))


def cell3(
    x: float,
    y: float,
    z: float,
    proj: Projection | None = None,
    layer_height: float = DEFAULT_LAYER_HEIGHT,
    res: int | None = None,
) -> tuple[str, int]:
    """Return (hex_id, y_layer) for world-space position."""
    return cell(x, z, proj=proj, res=res), y_layer(y, layer_height)


def cell_center_meters(hex_id: str, proj: Projection | None = None) -> tuple[float, float]:
    """Return world (x, z) of the cell center."""
    p = proj or Projection()
    lat, lng = h3.cell_to_latlng(hex_id)
    return p.latlng_to_meters(lat, lng)


def kring(hex_id: str, k: int = 1) -> list[str]:
    """Disk of hexes around center (inclusive), H3 grid_disk."""
    if k < 0:
        raise ValueError("k must be >= 0")
    return list(h3.grid_disk(hex_id, k))


def parent(hex_id: str, res: int | None = None) -> str:
    """Parent cell. If res is None, parent at resolution-1 (or self at res 0)."""
    current = h3.get_resolution(hex_id)
    if res is None:
        if current <= 0:
            return hex_id
        res = current - 1
    if res > current:
        raise ValueError(f"parent res {res} coarser than cell res {current}")
    if res == current:
        return hex_id
    return h3.cell_to_parent(hex_id, res)


def ancestors(hex_id: str, stop_res: int = 0) -> list[str]:
    """Return [hex_id, parent, ..., root] down to stop_res (inclusive)."""
    chain = [hex_id]
    current = hex_id
    while h3.get_resolution(current) > stop_res:
        current = parent(current)
        chain.append(current)
    return chain


def polyfill_rect(
    min_x: float,
    min_z: float,
    max_x: float,
    max_z: float,
    proj: Projection | None = None,
    res: int | None = None,
) -> list[str]:
    """H3 cells covering an axis-aligned world-space rectangle (XZ).

    Uses ``polygon_to_cells`` (cell centers inside poly). For small rects
    relative to cell size that can be empty, also unions cells of the four
    corners so the result is never vacuously empty for a non-degenerate box.
    """
    p = proj or Projection()
    r = p.base_res if res is None else res
    # Outer ring only — do not repeat the first vertex (h3 LatLngPoly style).
    corners_xz = [
        (min_x, min_z),
        (max_x, min_z),
        (max_x, max_z),
        (min_x, max_z),
    ]
    ring = [p.meters_to_latlng(x, z) for x, z in corners_xz]
    poly = h3.LatLngPoly(ring)
    cells = set(h3.polygon_to_cells(poly, r))
    # Guarantee coverage of corners (center-in-poly can miss thin game-scale boxes).
    for x, z in corners_xz:
        cells.add(cell(x, z, p, r))
    return list(cells)


def bearing_northish(from_hex: str, to_hex: str, proj: Projection | None = None) -> float:
    """Approximate bearing degrees from from_hex center to to_hex (0 = +Z / north)."""
    p = proj or Projection()
    x0, z0 = cell_center_meters(from_hex, p)
    x1, z1 = cell_center_meters(to_hex, p)
    dx, dz = x1 - x0, z1 - z0
    # atan2(dx, dz): 0 = +Z, 90 = +X
    return math.degrees(math.atan2(dx, dz)) % 360.0


def is_north_of(from_hex: str, to_hex: str, half_angle_deg: float = 45.0, proj: Projection | None = None) -> bool:
    """True if to_hex lies roughly north of from_hex (bearing near 0°)."""
    b = bearing_northish(from_hex, to_hex, proj)
    # Wrap: north sector is [0, half] U [360-half, 360)
    return b <= half_angle_deg or b >= (360.0 - half_angle_deg)


def ensure_cells(hex_ids: Iterable[str]) -> list[str]:
    """Deduplicate while preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for h in hex_ids:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def projection_from_world_row(row: dict | None) -> Projection:
    """Build Projection from a worlds table row (or defaults)."""
    if not row:
        return Projection()
    return Projection(
        anchor_lat=float(row.get("anchor_lat", DEFAULT_ANCHOR_LAT)),
        anchor_lng=float(row.get("anchor_lng", DEFAULT_ANCHOR_LNG)),
        meters_per_degree=float(row.get("meters_per_degree", DEFAULT_METERS_PER_DEGREE)),
        base_res=int(row.get("base_res", DEFAULT_BASE_RES)),
    )


def layer_height_from_world_row(row: dict | None) -> float:
    """Per-world vertical layer height (meters)."""
    if not row:
        return DEFAULT_LAYER_HEIGHT
    try:
        h = float(row.get("layer_height", DEFAULT_LAYER_HEIGHT))
    except (TypeError, ValueError):
        return DEFAULT_LAYER_HEIGHT
    return h if h > 0 else DEFAULT_LAYER_HEIGHT
