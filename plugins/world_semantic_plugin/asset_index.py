"""Bounded craft-v3 ingestion and multimodal asset search (no transport or worker loop)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import re
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath

from db_layer.embeddings import embeddings_core
from db_layer.embeddings.multimodal import EmbeddingInput, EmbeddingMedia, validate_embedding_media
from db_layer.embeddings.search_engine import SearchSpec, search_by_vector
from plugins.world_semantic_plugin.asset_models import WorldAssetEmbedding as Asset

KINDS = frozenset({"prop", "block", "texture", "audio", "stamp"})
MAX_ASSETS = 32
MAX_MEDIA_BYTES = 10_000_000
MAX_BATCH_BYTES = 24_000_000
MAX_INDEX_BYTES = 1_000_000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def validate_namespace(tenant_id: int, world_id: str) -> None:
    """Require a positive authorized tenant and a bounded opaque world namespace."""
    if type(tenant_id) is not int or tenant_id < 1 or not isinstance(world_id, str) or not _ID.fullmatch(world_id):
        raise ValueError("invalid tenant/world namespace")


async def resolve_asset_embedding() -> dict:
    """Resolve the shared configured selection, requiring multimodal capability even for text."""
    from core.llm_config_service import resolve_tool_embedding
    from core.llm_provider_management import get_llm_provider_registry
    sel = await resolve_tool_embedding("plugins.world_semantic_plugin", "asset_semantic")
    spec = get_llm_provider_registry().get((sel.get("provider") or "").lower())
    if spec is None or spec.multimodal_embeddings_factory is None:
        raise ValueError("asset indexing/search requires a configured multimodal embedding provider")
    # Canonical width comes from S01, never unity's padding/truncation policy.
    dimensions_for(sel)
    return sel


def dimensions_for(sel: dict) -> int:
    """Extract and bound the configured S01 identity width, without guessing a dimension."""
    try:
        width = int(embeddings_core.model_id_for(sel).rsplit(":", 1)[1])
    except (ValueError, TypeError):
        raise ValueError("invalid asset embedding dimensions") from None
    if not 1 <= width <= 16000:
        raise ValueError("asset embedding dimensions outside supported range")
    return width


def _string(value, name, maximum=512):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"invalid {name}")
    return value.strip()


def _labels(value, name):
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError(f"{name} must be a bounded string list")
    return sorted(set(_string(v, name, 128) for v in value))


def _relative_path(value):
    value = _string(value, "reference/variant path", 1024)
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if (posix.is_absolute() or windows.is_absolute() or windows.drive or "\\" in value
            or ":" in value or ".." in posix.parts):
        raise ValueError("path must remain beneath trusted ingestion root")
    return value


def _metadata(row):
    if not isinstance(row, dict):
        raise ValueError("asset must be an object")
    identity = _string(row.get("id"), "asset id", 128)
    if not _ID.fullmatch(identity):
        raise ValueError("invalid asset id")
    kind = row.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("unsupported asset kind")
    variants = row.get("variants")
    if not isinstance(variants, list) or not 1 <= len(variants) <= 32:
        raise ValueError("variants must be a nonempty bounded list")
    clean_variants = []
    for v in variants:
        if not isinstance(v, dict):
            raise ValueError("variant must be an object")
        digest = v.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("invalid variant sha256")
        clean_variants.append({"name": _string(v.get("name"), "variant name", 128),
                               "path": _relative_path(v.get("path")), "sha256": digest.lower()})
    names = [v["name"] for v in clean_variants]
    if len(set(names)) != len(names) or row.get("defaultVariant") not in names:
        raise ValueError("duplicate variant or unknown defaultVariant")
    meta = {"id": identity, "kind": kind,
            "description": _string(row.get("description"), "description", 8192),
            "tags": _labels(row.get("tags"), "tags"),
            "craftRoles": _labels(row.get("craftRoles"), "craftRoles"),
            "variants": sorted(clean_variants, key=lambda v: v["name"]),
            "defaultVariant": row["defaultVariant"]}
    if row.get("bounds") is not None:
        bounds = row["bounds"]
        if (not isinstance(bounds, list) or len(bounds) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in bounds)):
            raise ValueError("bounds must be three finite nonnegative metres")
        meta["bounds"] = [float(v) for v in bounds]
    return meta


def descriptive_document(meta: dict) -> str:
    """Render canonical searchable metadata, invariant to input object/list ordering."""
    return (f"Asset {meta['id']} ({meta['kind']}): {meta['description']}\n"
            f"Tags: {', '.join(meta['tags'])}. Craft roles: {', '.join(meta['craftRoles'])}.\n"
            f"Bounds (metres): {json.dumps(meta.get('bounds'), separators=(',', ':'))}.\n"
            f"Default variant: {meta['defaultVariant']}. Variants: "
            + json.dumps(meta["variants"], sort_keys=True, separators=(",", ":")))


def _read_reference(root: Path, value, modality):
    path = _relative_path(value)
    suffix = PurePosixPath(path).suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".wav": "audio/wav"}.get(suffix)
    if mime is None or not mime.startswith(modality + "/"):
        raise ValueError("unsupported reference media format")
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError("reference escapes trusted ingestion root")
    try:
        if not stat.S_ISREG(target.stat().st_mode):
            raise ValueError("reference must be a regular file")
        # Bounded read, including when a file grows after stat; trusted root is operator-owned.
        with target.open("rb") as source:
            data = source.read(MAX_MEDIA_BYTES + 1)
    except FileNotFoundError:
        return path, None
    except OSError:
        raise ValueError("reference is not a readable regular file") from None
    media = EmbeddingMedia(data, mime)
    validate_embedding_media(media, MAX_MEDIA_BYTES)
    return path, media


def _prepare(body, root, tenant_id, world_id, model_id):
    if not isinstance(body, dict) or type(body.get("version")) is not int or body["version"] != 1:
        raise ValueError("asset index requires version 1")
    rows = body.get("assets")
    if not isinstance(rows, list) or len(rows) > MAX_ASSETS:
        raise ValueError("assets must be a list of at most 32 records")
    try:
        encoded = json.dumps(body, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("asset index must be finite JSON") from None
    if len(encoded.encode()) > MAX_INDEX_BYTES:
        raise ValueError("asset index exceeds metadata payload limit")
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("trusted ingestion root must be a directory")
    prepared, identities, total_bytes = [], set(), 0
    for row in rows:
        meta = _metadata(row)
        if meta["id"] in identities:
            raise ValueError("duplicate asset id in index")
        identities.add(meta["id"])
        refs = row.get("reference", {})
        if refs is None:
            refs = {}
        if not isinstance(refs, dict):
            raise ValueError("reference must be an object or null")
        references, media_parts, missing = {}, [], []
        for modality in ("image", "audio"):
            value = refs.get(modality)
            if value is None:
                references[modality] = None
                continue
            path, medium = _read_reference(root, value, modality)
            references[modality] = path
            if medium is None:
                missing.append(modality)
            else:
                total_bytes += len(medium.data)
                if total_bytes > MAX_BATCH_BYTES:
                    raise ValueError("asset reference batch exceeds byte limit")
                media_parts.append({"mime_type": medium.mime_type, "data": base64.b64encode(medium.data).decode("ascii")})
        meta["reference"] = references
        text = descriptive_document(meta)
        # Bytes are immutable job snapshots; path-only hashes cannot detect replacement files.
        canonical = {"tenant_id": tenant_id, "world_id": world_id, "metadata": meta,
                     "model": model_id, "aggregation": "unit-mean-v1", "media": media_parts,
                     "missing_media": missing}
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        prepared.append({"asset_id": meta["id"], "meta": meta, "content_text": text,
                         "content_hash": digest, "media": media_parts, "missing_media": missing})
    return prepared


async def index_assets(body: dict, *, tenant_id: int, world_id: str, trusted_root: Path) -> dict:
    """Validate the entire bounded index before atomically staging desired state + durable jobs."""
    validate_namespace(tenant_id, world_id)
    sel = await resolve_asset_embedding()
    model_id = embeddings_core.model_id_for(sel)
    prepared = await asyncio.to_thread(_prepare, body, trusted_root, tenant_id, world_id, model_id)
    from plugins.world_semantic_plugin.stores.asset_store import stage_assets
    result = await stage_assets(prepared, tenant_id=tenant_id, world_id=world_id,
                                model_id=model_id, dimensions=dimensions_for(sel))
    result["warnings"] = [{"asset_id": r["asset_id"], "missing_media": r["missing_media"]}
                          for r in prepared if r["missing_media"]]
    return result


def decode_image(image: dict) -> EmbeddingMedia:
    """Accept only inline {mime_type, data: strict base64}; never accept URLs or file paths."""
    if not isinstance(image, dict) or image.get("mime_type") not in ("image/png", "image/jpeg"):
        raise ValueError("image requires PNG/JPEG mime_type and base64 data")
    encoded = image.get("data")
    if not isinstance(encoded, str) or len(encoded) > (MAX_MEDIA_BYTES + 2) // 3 * 4:
        raise ValueError("invalid or oversized base64 image")
    try:
        medium = EmbeddingMedia(base64.b64decode(encoded, validate=True), image["mime_type"])
    except (ValueError, TypeError):
        raise ValueError("invalid base64 image") from None
    validate_embedding_media(medium, MAX_MEDIA_BYTES)
    return medium


def aggregate_vectors(vectors: list[list[float]], dimensions: int) -> list[float]:
    """Normalize each exact-width finite vector, then normalize their equal-weight mean."""
    if not vectors:
        raise ValueError("missing embedding vectors")
    unit = []
    for vec in vectors:
        if (not isinstance(vec, list) or len(vec) != dimensions
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in vec)):
            raise ValueError("invalid asset embedding vector")
        norm = math.hypot(*vec)
        if not math.isfinite(norm) or norm == 0:
            raise ValueError("asset embedding has invalid norm")
        unit.append([v / norm for v in vec])
    mean = [sum(v[i] for v in unit) / len(unit) for i in range(dimensions)]
    norm = math.hypot(*mean)
    if norm == 0:
        raise ValueError("asset media vectors cancel; cannot create searchable representation")
    return [v / norm for v in mean]


def _row(row):
    return {"asset_id": row.asset_id, "metadata": row.meta}


_SPEC = SearchSpec(name="world_assets", model=Asset, select_cols=(Asset.asset_id, Asset.meta),
                   embedding_col=Asset.embedding, model_col=Asset.model,
                   key_fn=lambda r: r["asset_id"], row_to_dict=_row)


async def search_assets(query: str | None = None, *, image: dict | None = None, tenant_id: int,
                        world_id: str, kind: str | None = None, tags: list[str] | None = None,
                        k: int = 10) -> list[dict]:
    """Search current indexed assets; query XOR inline image, all tags must match before top-k."""
    validate_namespace(tenant_id, world_id)
    if (query is None) == (image is None):
        raise ValueError("supply exactly one of query or image")
    if type(k) is not int or not 1 <= k <= 100:
        raise ValueError("k must be an integer from 1 to 100")
    if kind is not None and (not isinstance(kind, str) or kind not in KINDS):
        raise ValueError("unsupported asset kind filter")
    tags = _labels(tags, "tags") if tags is not None else []
    document = EmbeddingInput(text=_string(query, "query", 8192)) if image is None else EmbeddingInput(media=await asyncio.to_thread(decode_image, image))
    sel = await resolve_asset_embedding()
    vectors = await embeddings_core.embed_multimodal_with(sel, [document])
    if len(vectors) != 1:
        raise ValueError("asset query embedding cardinality mismatch")
    vector = aggregate_vectors(vectors, dimensions_for(sel))

    def filters(stmt):
        stmt = stmt.where(Asset.tenant_id == tenant_id, Asset.world_id == world_id,
                          Asset.status == "indexed")
        if kind is not None:
            stmt = stmt.where(Asset.kind == kind)
        if tags:
            stmt = stmt.where(Asset.meta["tags"].contains(tags))
        return stmt

    return await search_by_vector(_SPEC, vector, k, embeddings_core.model_id_for(sel), filters, None)
