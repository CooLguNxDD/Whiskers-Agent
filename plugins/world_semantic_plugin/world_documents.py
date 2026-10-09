"""Validated Unity inspect/snapshot documents; no I/O except trusted-root image reads.

An inspect disc must fit inside exactly one projected H3 cell. Snapshot request
center is separate evidence because the legacy result does not echo its center.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import h3
from PIL import Image

from db_layer.embeddings.multimodal import EmbeddingMedia, _validate_media
from plugins.world_semantic_plugin.hexmath import Projection, cell, cell_center_meters

MAX_IMAGE_BYTES = 10_000_000
MAX_METADATA_BYTES = 1_000_000
MAX_REVISION = 2**63 - 1


def _number(value, label):
    try:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError
        return float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"{label} must be finite numeric data") from None


def _identity(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or value != value.strip():
        raise ValueError(f"invalid {label}")
    return value


def validate_projection(projection: Projection) -> None:
    """Reject invalid patch parameters instead of replacing them with global defaults."""
    if not isinstance(projection, Projection):
        raise ValueError("established world projection is required")
    lat = _number(projection.anchor_lat, "anchor_lat")
    lng = _number(projection.anchor_lng, "anchor_lng")
    scale = _number(projection.meters_per_degree, "meters_per_degree")
    if abs(lat) >= 89 or abs(lng) > 180 or scale <= 0:
        raise ValueError("invalid projection patch")
    if type(projection.base_res) is not int or not 0 <= projection.base_res <= 15:
        raise ValueError("invalid H3 resolution")


@dataclass(frozen=True)
class AuthorizedWorld:
    """Server-only capability: construct AFTER authenticating and authorizing this world.

    This is not a request body schema. S04 must obtain tenant from the principal,
    check world access, and load the established projection; never deserialize it.
    """

    tenant_id: int
    world_id: str
    projection: Projection

    def __post_init__(self):
        if type(self.tenant_id) is not int or self.tenant_id < 1:
            raise ValueError("authorized tenant_id is required")
        _identity(self.world_id, "world_id")
        validate_projection(self.projection)

    @property
    def namespace(self) -> str:
        """Collision-free tenant/world namespace, disjoint from legacy vector queries."""
        return "world-hex-v1:" + json.dumps([self.tenant_id, self.world_id], separators=(",", ":"))


def require_context(context) -> AuthorizedWorld:
    """Fail closed when no server-issued world capability was provided."""
    if not isinstance(context, AuthorizedWorld):
        raise ValueError("authorized world context is required")
    return context


def _envelope(raw, name):
    if not isinstance(raw, dict):
        raise ValueError(f"{name} must be a Unity result object")
    for flag in ("success", "ok"):
        if flag in raw and type(raw[flag]) is not bool:
            raise ValueError(f"{name} has invalid success flag")
        if raw.get(flag) is False:
            raise ValueError(f"{name} failed in Unity")
    # SuccessResponse wrapper or the raw data returned by Sample/Capture.
    data = raw.get("data", raw)
    if (not isinstance(data, dict) or data.get("ok") is False
            or ("ok" in data and type(data["ok"]) is not bool)):
        raise ValueError(f"{name} has invalid data")
    return data


def _xz(raw, name):
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError(f"{name} must be [x,z] Unity meters")
    return [_number(v, name) for v in raw]


def _json(value, depth=0):
    if depth > 16:
        raise ValueError("metadata is too deeply nested")
    if isinstance(value, dict):
        if len(value) > 2048 or any(not isinstance(k, str) or len(k) > 200 for k in value):
            raise ValueError("invalid metadata keys")
        result = {k: _json(value[k], depth + 1) for k in sorted(value)}
        # Tags are sets; geometry point sequences, bounds and flow vectors are not.
        for key in ("tags", "craftRoles"):
            if key in result and isinstance(result[key], list):
                result[key] = sorted(result[key], key=_dump)
        return result
    if isinstance(value, list):
        if len(value) > 2048:
            raise ValueError("metadata array is too large")
        return [_json(v, depth + 1) for v in value]
    if value is None or type(value) is bool:
        return value
    if isinstance(value, str):
        if len(value) > 10000:
            raise ValueError("metadata string is too large")
        return value
    # Normalize 1 and 1.0 identically (Unity serializers differ).
    numeric = _number(value, "metadata")
    return int(numeric) if numeric.is_integer() else numeric


def _dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _histogram(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a histogram")
    result = _json(value)
    if any(type(n) is not int or n < 0 for n in result.values()):
        raise ValueError(f"{name} counts must be nonnegative integers")
    return result


def _disc_in_hex(center, radius, hex_id, projection):
    # H3 boundary projected to the SAME local linear patch as Unity's XZ frame.
    boundary = [projection.latlng_to_meters(lat, lng) for lat, lng in h3.cell_to_boundary(hex_id)]
    x, z = center
    for i, (ax, az) in enumerate(boundary):
        bx, bz = boundary[(i + 1) % len(boundary)]
        dx, dz = bx - ax, bz - az
        length = math.hypot(dx, dz)
        if length == 0:
            raise ValueError("invalid projected hex boundary")
        distance = abs(dx * (z - az) - dz * (x - ax)) / length
        if radius > distance + 1e-6:
            raise ValueError("inspect sample crosses hex boundary; supply a per-hex sample")


def _snapshot_bytes(snapshot, trusted_root):
    mime = snapshot.get("mime_type", "image/png")
    if mime not in ("image/png", "image/jpeg"):
        raise ValueError("snapshot must be PNG/JPEG")
    if "base64" in snapshot:
        encoded = snapshot["base64"]
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ValueError("invalid or oversized snapshot base64")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise ValueError("invalid snapshot base64") from None
    else:
        if trusted_root is None:
            raise ValueError("snapshot path requires an explicit trusted ingestion root")
        path = snapshot.get("path")
        if not isinstance(path, str) or not path or ":" in path or "\\" in path:
            raise ValueError("invalid snapshot path")
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("snapshot path must stay beneath trusted root")
        try:
            root = Path(trusted_root).resolve(strict=True)
            resolved = (root / relative).resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise ValueError("snapshot path escapes trusted root")
            # Root is operator-controlled and immutable to ingestion callers; bounded read.
            with resolved.open("rb") as stream:
                data = stream.read(MAX_IMAGE_BYTES + 1)
        except (OSError, RuntimeError):
            raise ValueError("snapshot cannot be read beneath trusted root") from None
    media = EmbeddingMedia(data=data, mime_type=mime)
    _validate_media(media, MAX_IMAGE_BYTES)
    with Image.open(io.BytesIO(data)) as image:
        for field, actual in (("width", image.width), ("height", image.height)):
            if field in snapshot and (type(snapshot[field]) is not int or snapshot[field] != actual):
                raise ValueError("snapshot dimensions do not match image bytes")
    return media


@dataclass(frozen=True)
class WorldDocument:
    """Canonical content with revision evidence; byte payloads never become embedding text."""

    context: AuthorizedWorld
    hex_id: str
    revision: int
    model_id: str
    summary: str
    media: EmbeddingMedia | None
    center_pos: list[float]
    content_hash: str

    def job_payload(self) -> dict:
        """Queue inline validated bytes (not Unity-local paths), without endpoint or credentials."""
        return {
            "namespace": self.context.namespace, "tenant_id": self.context.tenant_id,
            "world_id": self.context.world_id, "projection": asdict(self.context.projection),
            "hex_id": self.hex_id, "revision": self.revision, "model_id": self.model_id,
            "summary": self.summary, "content_hash": self.content_hash,
            "image_base64": base64.b64encode(self.media.data).decode("ascii") if self.media else None,
            "mime_type": self.media.mime_type if self.media else None,
        }


def document_hash(namespace, hex_id, model_id, summary, media):
    """Hash canonical identity/summary, exact image bytes and MIME; exclude revision/paths."""
    text = _dump([namespace, hex_id, model_id, summary, media.mime_type if media else None])
    digest = hashlib.sha256(text.encode("utf-8"))
    digest.update(b"\x00")
    if media:
        digest.update(media.data)
    return digest.hexdigest()


def normalize_document(context: AuthorizedWorld, payload: dict, model_id: str,
                       *, trusted_root: Path | None = None) -> WorldDocument:
    """Adapt actual Unity envelopes into one spatially associated, change-aware hex document."""
    context = require_context(context)
    if not isinstance(payload, dict) or payload.get("world_id") != context.world_id:
        raise ValueError("payload world_id must match authorized world")
    if "tenant_id" in payload and (type(payload["tenant_id"]) is not int or payload["tenant_id"] != context.tenant_id):
        raise ValueError("payload tenant mismatch")
    if "projection" in payload and payload["projection"] != asdict(context.projection):
        raise ValueError("payload projection must match established projection")
    revision = payload.get("revision")
    if type(revision) is not int or not 1 <= revision <= MAX_REVISION:
        raise ValueError("revision must be a positive monotonic integer")
    inspect = _envelope(payload.get("inspect"), "inspect")
    center = _xz(inspect.get("center"), "inspect center")
    projection = context.projection
    lat, lng = projection.meters_to_latlng(*center)
    if not -89 < lat < 89 or not -180 <= lng <= 180:
        raise ValueError("inspect center outside projection patch")
    hex_id = payload.get("hex_id", cell(*center, projection))
    if (not isinstance(hex_id, str) or not h3.is_valid_cell(hex_id)
            or h3.get_resolution(hex_id) != projection.base_res
            or hex_id != cell(*center, projection)):
        raise ValueError("invalid hex or inspect/hex spatial mismatch")
    radius = _number(inspect.get("radius"), "inspect radius")
    if radius <= 0:
        raise ValueError("inspect radius must be positive")
    _disc_in_hex(center, radius, hex_id, projection)
    facts = {"sample_center_xz": center, "sample_radius_m": radius}
    for name in ("histogram", "slope", "propHistogram"):
        if name in inspect:
            facts[name] = _histogram(inspect[name], name)
    for name in ("height", "sampledSolid", "columns", "waterPercent", "waterLevel", "waterDepth",
                 "flow", "propHistogramSemantics", "structures", "waterBodies", "splines"):
        if name in inspect:
            facts[name] = _json(inspect[name])
    # Only collection members are unordered; spline points and flow vectors retain order.
    for name in ("structures", "waterBodies", "splines"):
        if name in facts:
            if not isinstance(facts[name], list):
                raise ValueError(f"{name} must be a list")
            facts[name] = sorted(facts[name], key=_dump)
    if "height" in facts:
        height = facts["height"]
        if not isinstance(height, dict) or any(k not in height for k in ("min", "max", "avg")):
            raise ValueError("height must supply min/max/avg")
        low, high, avg = [_number(height[k], "height") for k in ("min", "max", "avg")]
        if not low <= avg <= high:
            raise ValueError("invalid height range")
    for name in ("waterPercent", "waterLevel", "waterDepth", "sampledSolid", "columns"):
        if name in facts:
            value = _number(facts[name], name)
            if name == "waterPercent" and not 0 <= value <= 100:
                raise ValueError("waterPercent outside 0..100")
            if name in ("waterDepth", "sampledSolid", "columns") and value < 0:
                raise ValueError(f"{name} must be nonnegative")
            if name in ("sampledSolid", "columns") and not value.is_integer():
                raise ValueError(f"{name} must be an integer")
    body = _dump(facts)
    if len(body.encode("utf-8")) > MAX_METADATA_BYTES:
        raise ValueError("inspect metadata exceeds limit")
    media = None
    snapshot_meta = None
    if "snapshot" in payload and payload["snapshot"] is not None:
        snapshot = _envelope(payload["snapshot"], "snapshot")
        # Capture doesn't echo center: caller must preserve original request center.
        snap_center = _xz(snapshot.get("center", payload.get("snapshot_center")), "snapshot center")
        if any(abs(a - b) > 1e-6 for a, b in zip(center, snap_center)):
            raise ValueError("snapshot center must correspond to inspect sample")
        sx = _number(snapshot.get("sizeX"), "snapshot sizeX")
        sz = _number(snapshot.get("sizeZ"), "snapshot sizeZ")
        if sx <= 0 or sz <= 0:
            raise ValueError("snapshot size must be positive")
        _disc_in_hex(snap_center, math.hypot(sx, sz) / 2, hex_id, projection)
        media = _snapshot_bytes(snapshot, trusted_root)
        snapshot_meta = {"center_xz": snap_center, "sizeX": sx, "sizeZ": sz}
        if "view" in snapshot:
            if snapshot["view"] not in ("top", "iso", "camera"):
                raise ValueError("invalid snapshot view")
            snapshot_meta["view"] = snapshot["view"]
    summary = (f"World {context.world_id}; hex {hex_id}. Projection: {_dump(asdict(projection))}. "
               "Local sample within hex (Unity XZ meters). "
               f"Supplied terrain/water/spline/prop facts: {body}")
    if snapshot_meta is not None:
        summary += " Corresponding snapshot footprint: " + _dump(snapshot_meta)
    content_hash = document_hash(context.namespace, hex_id, model_id, summary, media)
    return WorldDocument(context, hex_id, revision, model_id, summary, media,
                         list(cell_center_meters(hex_id, projection)), content_hash)
