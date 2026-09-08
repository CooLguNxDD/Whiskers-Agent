"""MinIO artifact offload for large GOAP step_results / response fields.

Extracts oversized string leaves, uploads them to MinIO, persists a short_id
row in artifact_links, and rewrites the leaf to a tiny marker. Retrieval is
session-gated REST or MCP tools — never public URLs.

Part of the ``core.artifact_store`` feature package.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from copy import deepcopy
from typing import Any

from core.artifact_store.minio_client import (
    get_object_bytes,
    get_presigned_url,
    put_object_bytes,
)
from utils.short_id import generate_short_id

logger = logging.getLogger("whiskers_agent")

ARTIFACT_BUCKET = os.environ.get("WHISKERS_ARTIFACT_BUCKET") or os.environ.get("ARTIFACT_BUCKET", "whiskers-artifacts")
_SHORT_ID_RE = re.compile(r"^[a-z0-9_]{1,80}$")
_MAX_WALK_DEPTH = 12
_MAX_SHORT_ID_ATTEMPTS = 5

# Console path hint only — still requires session cookie.
CONSOLE_PATH_TMPL = "/api/artifacts/session_gated/{short_id}"


def is_valid_short_id(short_id: str) -> bool:
    """Return True when short_id matches the allowed alphabet/length."""
    return bool(short_id) and bool(_SHORT_ID_RE.match(short_id))


def _kind_from_path(path: str) -> str:
    """Derive a short kind token from a JSON path for short_id seeding."""
    leaf = path.rsplit(".", 1)[-1] if path else "blob"
    leaf = re.sub(r"[^a-zA-Z0-9_]", "", leaf) or "blob"
    lower = leaf.lower()
    if "diff" in lower or "patch" in lower or "unidiff" in lower:
        return "diff"
    if "bash" in lower or "output" in lower or "log" in lower:
        return "log"
    if "report" in lower or "message" in lower or "markdown" in lower or "md" in lower:
        return "report"
    return lower[:24] or "blob"


def _ext_for_content_type(content_type: str, kind: str) -> str:
    ct = (content_type or "").lower()
    if "json" in ct:
        return "json"
    if "html" in ct:
        return "html"
    if "csv" in ct:
        return "csv"
    if "patch" in ct or "diff" in ct or kind == "diff":
        return "diff"
    return "md"


def collect_string_leaves(
    node: Any,
    *,
    path: str = "root",
    depth: int = 0,
    out: list[tuple[str, int]] | None = None,
) -> list[tuple[str, int]]:
    """Return (path, utf8_byte_len) for every string leaf (diagnostic / ranking)."""
    if out is None:
        out = []
    if depth > _MAX_WALK_DEPTH:
        return out
    if isinstance(node, str):
        out.append((path, len(node.encode("utf-8"))))
    elif isinstance(node, dict):
        for k, v in node.items():
            child = f"{path}.{k}" if path else str(k)
            collect_string_leaves(v, path=child, depth=depth + 1, out=out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            collect_string_leaves(v, path=f"{path}[{i}]", depth=depth + 1, out=out)
    return out


async def offload_text(
    content: str,
    *,
    kind: str,
    session_id: str | None,
    tenant_id: int,
    source_path: str | None = None,
    ext: str | None = None,
    content_type: str = "text/markdown",
) -> dict[str, Any]:
    """Upload text to MinIO, mint short_id, persist ArtifactLink, return ref dict.

    Raises on hard failures so the caller can leave the field untouched.
    """
    if tenant_id is None:
        raise ValueError("tenant_id is required for offload_text")

    from db_layer.artifact_link_store import (
        artifact_link_short_id_exists,
        create_artifact_link,
    )

    data = content.encode("utf-8")
    n_bytes = len(data)
    sha8 = hashlib.sha256(data).hexdigest()[:8]
    kind_slug = re.sub(r"[^a-z0-9_]", "", (kind or "blob").lower())[:24] or "blob"
    file_ext = ext or _ext_for_content_type(content_type, kind_slug)
    sess = (session_id or "nosession").replace("/", "_")[:64]
    object_key = f"{int(tenant_id)}/{sess}/{kind_slug}-{sha8}.{file_ext}"

    await asyncio.to_thread(
        put_object_bytes,
        ARTIFACT_BUCKET,
        object_key,
        data,
        content_type,
    )

    short_id = None
    for _ in range(_MAX_SHORT_ID_ATTEMPTS):
        candidate = generate_short_id(["art", kind_slug])
        if not await artifact_link_short_id_exists(candidate):
            short_id = candidate
            break
    if short_id is None:
        raise RuntimeError(f"short_id collision for kind={kind_slug}")

    await create_artifact_link(
        short_id=short_id,
        bucket=ARTIFACT_BUCKET,
        object_key=object_key,
        content_type=content_type,
        kind=kind_slug,
        bytes=n_bytes,
        source_path=source_path,
        tenant_id=int(tenant_id),
        session_id=session_id,
    )

    return {
        "kind": kind_slug,
        "short_id": short_id,
        "bytes": n_bytes,
        "content_type": content_type,
        "session_id": session_id,
        "path": source_path,
        "console_path": CONSOLE_PATH_TMPL.format(short_id=short_id),
        "object_key": object_key,
    }


def _clamp_preview_chars(preview_chars: int | None) -> int:
    """Clamp preview budget to [0, 8000]; None → config default."""
    if preview_chars is None:
        try:
            from utils.server_config import ARTIFACT_OFFLOAD_PREVIEW_CHARS

            preview_chars = int(ARTIFACT_OFFLOAD_PREVIEW_CHARS)
        except Exception as exc:
            logger.warning("_clamp_preview_chars: failed to parse ARTIFACT_OFFLOAD_PREVIEW_CHARS: %s", exc)
            preview_chars = 1500
    return max(0, min(int(preview_chars), 8000))


def is_offload_marker(value: str) -> bool:
    """True when a string leaf was already replaced by an offload marker."""
    return isinstance(value, str) and value.startswith("[offloaded:")


# Credential-shaped tokens scrubbed from inline previews before LLM context.
_CREDENTIAL_PREVIEW_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{12,}\b"),
    re.compile(r"(?i)\bAuthorization:\s*\S+"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
)


def _redact_preview_text(text: str) -> str:
    """Scrub credential-shaped substrings and long base64/hex runs from previews.

    Reuses ``utils.response_shape`` strip_base64 / strip_hex predicates so
    offload markers never inline secrets into LLM context.
    """
    if not text:
        return text
    out = text
    for pat in _CREDENTIAL_PREVIEW_RES:
        out = pat.sub("[redacted]", out)
    try:
        from utils.response_shape import strip_base64_fields, _strip_hex

        cleaned = strip_base64_fields(out, placeholder="[base64 stripped]")
        if isinstance(cleaned, str):
            out = cleaned
        cleaned = _strip_hex(out, placeholder="[hash]")
        if isinstance(cleaned, str):
            out = cleaned
    except Exception as exc:
        # Fail open: credential regex already ran; strip helpers are best-effort.
        logger.debug("preview base64/hex strip skipped: %s", exc)
    return out


def _marker(
    path: str,
    short_id: str,
    n_bytes: int,
    *,
    content: str | None = None,
    preview_chars: int = 0,
) -> str:
    """Build an offload marker; optionally append a bounded inline preview.

    Agents should use the preview for status/summary decisions and call
    ``fetch_artifact`` only when the full body is required. Previews are
    redacted (credentials + long base64/hex) before inlining.
    """
    base = (
        f"[offloaded: {path} short_id={short_id} ({n_bytes} bytes) "
        f"— call fetch_artifact ONLY if the preview below is insufficient "
        f"for your next step; console "
        f"{CONSOLE_PATH_TMPL.format(short_id=short_id)}]"
    )
    if preview_chars <= 0 or content is None:
        return base
    raw_preview = content[:preview_chars]
    preview = _redact_preview_text(raw_preview)
    ellipsis = "…" if len(content) > preview_chars else ""
    return f"{base}\n--- preview ---\n{preview}{ellipsis}\n--- end preview ---"


def _set_at_path(root: Any, path: str, value: str) -> bool:
    """Set a string leaf on a deepcopy structure using collect_string_leaves paths.

    Paths look like ``step_results[0].data.data`` or ``response.data.data``.
    Returns True when the leaf was found and replaced.
    """
    # Strip optional root label prefix used for diagnostics.
    p = path
    for prefix in ("step_results", "response", "root"):
        if p == prefix:
            return False
        if p.startswith(prefix + ".") or p.startswith(prefix + "["):
            p = p[len(prefix) :]
            break

    # Parse into tokens: .key  or  [idx]
    tokens: list[str | int] = []
    i = 0
    while i < len(p):
        if p[i] == ".":
            i += 1
            j = i
            while j < len(p) and p[j] not in ".[":
                j += 1
            if j > i:
                tokens.append(p[i:j])
            i = j
        elif p[i] == "[":
            j = p.find("]", i)
            if j < 0:
                return False
            try:
                tokens.append(int(p[i + 1 : j]))
            except ValueError:
                return False
            i = j + 1
        else:
            # bare key at start
            j = i
            while j < len(p) and p[j] not in ".[":
                j += 1
            if j > i:
                tokens.append(p[i:j])
            i = j

    if not tokens:
        return False
    cur = root
    for tok in tokens[:-1]:
        try:
            cur = cur[tok]
        except (KeyError, IndexError, TypeError):
            return False
    last = tokens[-1]
    try:
        cur[last] = value
        return True
    except (KeyError, IndexError, TypeError):
        return False


async def extract_large_artifacts(
    payload: Any,
    *,
    session_id: str | None,
    tenant_id: int,
    min_bytes: int = 2000,
    max_artifacts: int = 8,
    path_root: str = "step_results",
    preview_chars: int | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    """Walk a copy of payload; offload large string leaves; return (slim, refs).

    Fail-safe per field: MinIO/DB errors leave that leaf unchanged.

    Adaptive rule: if no single leaf is ``>= min_bytes`` but the total UTF-8
    size of all string leaves exceeds ``min_bytes``, still offload the largest
    leaves (so a ~5–6 KB shaped CSV just under a higher threshold still offloads).

    When ``preview_chars > 0``, markers include an inline preview and leaves that
    fully fit in the preview budget are left inline (no MinIO hop).
    """
    if payload is None or min_bytes <= 0 or max_artifacts <= 0:
        return payload, []

    preview_budget = _clamp_preview_chars(preview_chars)
    slim = deepcopy(payload)
    refs: list[dict[str, Any]] = []

    leaves = collect_string_leaves(slim, path=path_root)
    # Skip already-offloaded markers from a prior pass (summary second-chance).
    leaves = [
        (p, n)
        for p, n in leaves
        if n >= 32  # ignore tiny strings
    ]
    if not leaves:
        return slim, []

    leaves_sorted = sorted(leaves, key=lambda x: x[1], reverse=True)
    total = sum(n for _, n in leaves_sorted)
    max_leaf = leaves_sorted[0][1] if leaves_sorted else 0

    # Primary: any leaf >= min_bytes.
    # Adaptive: total payload over min_bytes → take largest leaves until under
    # budget or max_artifacts, using a soft floor of min_bytes // 2 (min 512).
    soft_floor = max(512, min_bytes // 2)
    if max_leaf >= min_bytes:
        candidates = [(p, n) for p, n in leaves_sorted if n >= min_bytes]
    elif total >= min_bytes:
        candidates = [(p, n) for p, n in leaves_sorted if n >= soft_floor]
        logger.info(
            "artifact offload adaptive: total=%s max_leaf=%s min=%s soft_floor=%s candidates=%s",
            total,
            max_leaf,
            min_bytes,
            soft_floor,
            len(candidates),
        )
    else:
        logger.debug(
            "artifact offload skip: total=%s max_leaf=%s min=%s path_root=%s",
            total,
            max_leaf,
            min_bytes,
            path_root,
        )
        return slim, []

    path_set = {p for p, _ in candidates[:max_artifacts]}
    # Content-hash dedupe so step_results + response walks of the same CSV
    # share one short_id / one MinIO object.
    seen_sha: dict[str, dict[str, Any]] = {}

    def _apply_marker(val: str, path: str, ref: dict[str, Any]) -> str:
        return _marker(
            path,
            ref["short_id"],
            int(ref.get("bytes") or 0),
            content=val,
            preview_chars=preview_budget,
        )

    async def _offload_leaf(val: str, path: str) -> str | None:
        """Offload one string leaf; return marker or None to leave inline."""
        # Fully fits in the preview budget → keep inline (no second hop).
        if preview_budget > 0 and len(val) <= preview_budget:
            return None
        kind = _kind_from_path(path)
        try:
            sha = hashlib.sha256(val.encode("utf-8")).hexdigest()
            if sha in seen_sha:
                ref = {**seen_sha[sha], "path": path}
                refs.append(ref)
                return _apply_marker(val, path, ref)
            ref = await offload_text(
                val,
                kind=kind,
                session_id=session_id,
                tenant_id=tenant_id,
                source_path=path,
            )
            seen_sha[sha] = ref
            refs.append(ref)
            return _apply_marker(val, path, ref)
        except Exception as exc:
            logger.warning("artifact offload failed path=%s: %s", path, exc)
            return None

    async def _walk(node: Any, path: str, depth: int) -> None:
        if len(refs) >= max_artifacts or depth > _MAX_WALK_DEPTH:
            return
        if isinstance(node, dict):
            for key, val in list(node.items()):
                child_path = f"{path}.{key}" if path else str(key)
                if isinstance(val, str):
                    if is_offload_marker(val):
                        continue
                    if child_path in path_set and len(refs) < max_artifacts:
                        marker = await _offload_leaf(val, child_path)
                        if marker is not None:
                            node[key] = marker
                    continue
                if isinstance(val, (dict, list)):
                    await _walk(val, child_path, depth + 1)
        elif isinstance(node, list):
            for i, val in enumerate(node):
                child_path = f"{path}[{i}]"
                if isinstance(val, str):
                    if is_offload_marker(val):
                        continue
                    if child_path in path_set and len(refs) < max_artifacts:
                        marker = await _offload_leaf(val, child_path)
                        if marker is not None:
                            node[i] = marker
                    continue
                if isinstance(val, (dict, list)):
                    await _walk(val, child_path, depth + 1)

    await _walk(slim, path_root, 0)
    return slim, refs

async def resolve_artifact_meta(short_id: str, tenant_id: int) -> dict[str, Any] | None:
    """Return artifact metadata for tenant-scoped short_id, or None."""
    if not is_valid_short_id(short_id):
        return None
    from db_layer.artifact_link_store import get_artifact_link_by_short_id

    return await get_artifact_link_by_short_id(short_id, tenant_id)


async def read_artifact_bytes(short_id: str, tenant_id: int) -> tuple[dict[str, Any], bytes] | None:
    """Load artifact meta + object body; None if missing or wrong tenant."""
    meta = await resolve_artifact_meta(short_id, tenant_id)
    if not meta:
        return None
    try:
        body = await asyncio.to_thread(
            get_object_bytes, meta["bucket"], meta["object_key"]
        )
    except Exception as exc:
        logger.warning("artifact read failed short_id=%s: %s", short_id, exc, exc_info=True)
        return None
    return meta, body


async def mint_download_url(
    short_id: str,
    tenant_id: int,
    expires_s: int = 3600,
) -> str | None:
    """Mint a short-lived MinIO presigned URL for an authenticated caller."""
    meta = await resolve_artifact_meta(short_id, tenant_id)
    if not meta:
        return None
    try:
        return await asyncio.to_thread(
            get_presigned_url,
            meta["bucket"],
            meta["object_key"],
            int(expires_s),
        )
    except Exception as exc:
        logger.warning("artifact presign failed short_id=%s: %s", short_id, exc, exc_info=True)
        return None
