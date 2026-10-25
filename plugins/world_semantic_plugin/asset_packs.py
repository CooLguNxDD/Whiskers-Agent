"""WorldGen v3 pack roots and pack-record validation (no database, no provider calls)."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from db_layer.embeddings.multimodal import EmbeddingMedia, validate_embedding_media

# Root folder name -> default distribution of every record ingested beneath it.
ROOT_DISTRIBUTION = {"packs": "shared", "packs-local": "local"}
DISTRIBUTIONS = frozenset({"shared", "local"})
V3_VERSION = 3
MAX_V3_ASSETS = 32
MAX_VIEWS = 8
MAX_ASSET_ID = 128
MAX_VIEW_BYTES = 10_000_000
MAX_V3_BATCH_BYTES = 24_000_000
V3_KINDS = frozenset({"prop", "block", "texture", "stamp"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_. -]{0,127}\Z")
_HASH = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]{7,127}\Z")
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


@dataclass(frozen=True)
class PackRoot:
    """One resolved ingestion root: its folder name, absolute path and distribution default."""

    name: str
    path: Path
    distribution: str


def register_pack_roots(config) -> dict[str, PackRoot]:
    """Resolve `{"packs": dir, "packs-local": dir}`; unknown names or non-directories are rejected."""
    if not isinstance(config, dict) or not config:
        raise ValueError("pack roots must be a nonempty object")
    roots = {}
    for name, value in config.items():
        if name not in ROOT_DISTRIBUTION:
            raise ValueError("pack root must be named 'packs' or 'packs-local'")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("pack root path must be a nonempty string")
        try:
            path = Path(value).resolve(strict=True)
        except OSError:
            raise ValueError("pack root does not exist") from None
        if not path.is_dir():
            raise ValueError("pack root must be a directory")
        roots[name] = PackRoot(name, path, ROOT_DISTRIBUTION[name])
    return roots


def pack_roots_for(tenant_id: int, world_id: str) -> dict[str, PackRoot]:
    """Server-side `asset_pack_roots[tenant][world]` mapping; empty when none is configured."""
    from plugins.world_semantic_plugin.plugin_config import SETTINGS
    configured = SETTINGS.get("asset_pack_roots", {})
    value = configured.get(str(tenant_id), {}).get(world_id) if isinstance(configured, dict) else None
    return register_pack_roots(value) if value else {}


def local_capable(tenant_id: int, world_id: str) -> bool:
    """A namespace may read local assets only when a `packs-local` root is configured for it."""
    return "packs-local" in pack_roots_for(tenant_id, world_id)


def _segments(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"invalid {name}")
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if (posix.is_absolute() or windows.is_absolute() or windows.drive or "\\" in value or ":" in value
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError(f"{name} must remain beneath the pack root")
    if not all(_SEGMENT.fullmatch(part) for part in value.split("/")):
        raise ValueError(f"invalid {name} segment")
    return value


def _read_view(root: PackRoot, pack_id: str, rel: str) -> EmbeddingMedia:
    rel = _segments(rel, "view path", 512)
    mime = _MIME.get(PurePosixPath(rel).suffix.lower())
    if mime is None:
        raise ValueError("views must be PNG or JPEG images")
    target = (root.path / pack_id / rel).resolve()
    # Symlinks are followed by resolve(); anything landing outside the root (or the pack) is refused.
    if not target.is_relative_to(root.path) or not target.is_relative_to(root.path / pack_id):
        raise ValueError("view escapes the pack root")
    try:
        if not stat.S_ISREG(target.stat().st_mode):
            raise ValueError("view must be a regular file")
        with target.open("rb") as source:
            data = source.read(MAX_VIEW_BYTES + 1)
    except FileNotFoundError:
        raise ValueError("view file not found") from None
    except OSError:
        raise ValueError("view is not a readable regular file") from None
    media = EmbeddingMedia(data, mime)
    validate_embedding_media(media, MAX_VIEW_BYTES)
    return media


def _label(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(f"invalid {name}")
    return value.strip()


def validate_record(row, root: PackRoot) -> dict:
    """Validate one v3 pack record and read its view bytes; raises ValueError (per-record error)."""
    if not isinstance(row, dict):
        raise ValueError("record must be an object")
    pack_id = row.get("pack_id")
    if not isinstance(pack_id, str) or not _ID.fullmatch(pack_id):
        raise ValueError("invalid pack_id")
    asset_id = row.get("asset_id")
    if not isinstance(asset_id, str) or len(asset_id) > MAX_ASSET_ID or not asset_id.startswith(pack_id + "/"):
        raise ValueError("asset_id must be <pack_id>/<rel_path>")
    _segments(asset_id[len(pack_id) + 1:], "asset rel_path", MAX_ASSET_ID)
    digest = row.get("content_hash")
    if not isinstance(digest, str) or not _HASH.fullmatch(digest):
        raise ValueError("invalid content_hash")
    tags = row.get("tags")
    if not isinstance(tags, dict):
        raise ValueError("tags must be an object with role, biome and licence")
    free = tags.get("free", [])
    if not isinstance(free, list) or len(free) > 64:
        raise ValueError("tags.free must be a bounded string list")
    kind = row.get("kind", "prop")
    if kind not in V3_KINDS:
        raise ValueError("unsupported asset kind")
    declared = row.get("distribution")
    if declared is not None and declared not in DISTRIBUTIONS:
        raise ValueError("distribution must be 'shared' or 'local'")
    distribution = declared or root.distribution
    # A local root can never hold shared records; a shared root may hold stricter local ones.
    if root.distribution == "local" and distribution != "local":
        raise ValueError("packs-local records cannot be distribution=shared")
    tri_count = row.get("tri_count")
    if type(tri_count) is not int or not 0 <= tri_count <= 2_000_000_000:
        raise ValueError("tri_count must be a nonnegative integer")
    caption = row.get("caption")
    if caption is not None:
        caption = _label_text(caption)
    views = row.get("views")
    if not isinstance(views, list) or len(views) > MAX_VIEWS or (not views and caption is None):
        raise ValueError("record needs 1-8 views or a caption")
    paths = [_segments(v, "view path", 512) for v in views]
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate view path")
    media = [_read_view(root, pack_id, p) for p in paths]
    meta = {"id": asset_id, "kind": kind, "pack_id": pack_id, "distribution": distribution,
            "tri_count": tri_count, "role": _label(tags.get("role"), "tags.role"),
            "biome": _label(tags.get("biome"), "tags.biome"),
            "licence": _label(tags.get("licence"), "tags.licence"),
            "tags": sorted({_label(t, "tag") for t in free}), "views": paths,
            "pack_content_hash": digest.lower()}
    meta["craftRoles"] = [meta["role"]]
    if caption is not None:
        meta["caption"] = caption
    return {"asset_id": asset_id, "meta": meta, "caption": caption,
            "views": [{"mime_type": m.mime_type, "data": base64.b64encode(m.data).decode("ascii")}
                      for m in media], "bytes": sum(len(m.data) for m in media)}


def _label_text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        raise ValueError("invalid caption")
    return value.strip()


def job_hash(prepared: dict, *, tenant_id: int, world_id: str, model_id: str) -> str:
    """Namespace- and model-bound identity: unchanged pack hash + model means no re-embed."""
    canonical = {"v": V3_VERSION, "tenant_id": tenant_id, "world_id": world_id, "model": model_id,
                 "asset_id": prepared["asset_id"], "pack_content_hash": prepared["meta"]["pack_content_hash"]}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def prepare_records(body: dict, roots: dict[str, PackRoot], *, tenant_id: int, world_id: str,
                    model_id: str) -> tuple[list[dict], list[dict]]:
    """Validate a version-3 body; returns (prepared, per-record errors). Never raises per record."""
    if not isinstance(body, dict) or body.get("version") != V3_VERSION or type(body.get("version")) is not int:
        raise ValueError("pack index requires version 3")
    root = roots.get(body.get("root"))
    if root is None:
        raise ValueError("pack index requires a configured root ('packs' or 'packs-local')")
    rows = body.get("assets")
    if not isinstance(rows, list) or len(rows) > MAX_V3_ASSETS:
        raise ValueError("assets must be a list of at most 32 records")
    try:
        json.dumps(body, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("pack index must be finite JSON") from None
    prepared, errors, seen, total = [], [], set(), 0
    for index, row in enumerate(rows):
        label = row.get("asset_id") if isinstance(row, dict) and isinstance(row.get("asset_id"), str) else None
        try:
            record = validate_record(row, root)
            if record["asset_id"] in seen:
                raise ValueError("duplicate asset_id in batch")
            if total + record["bytes"] > MAX_V3_BATCH_BYTES:
                raise ValueError("batch exceeds view byte limit")
        except ValueError as exc:
            errors.append({"index": index, "asset_id": label, "error": str(exc)})
            continue
        seen.add(record["asset_id"])
        total += record["bytes"]
        record["content_hash"] = job_hash(record, tenant_id=tenant_id, world_id=world_id, model_id=model_id)
        prepared.append(record)
    return prepared, errors
