"""Incremental layout patch merge — append/update blocks without full-page rebuild.

Used by ``design_layout`` (read-only preview) and ``patch_job_layout`` (derived
persist). Identity key is the stable block ``id`` string.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.patch")

_VALID_PATCH_MODES = frozenset({"append_or_update", "update_only", "replace"})


def insert_before_cta(blocks: list[dict], block: dict, *, max_total: int = 20) -> list[dict]:
    """Append *block* before the first ``quickActions`` CTA when present."""
    out = list(blocks)
    if len(out) >= max_total:
        return out
    qa_i = next(
        (i for i, b in enumerate(out) if isinstance(b, dict) and str(b.get("type") or "") == "quickActions"),
        None,
    )
    if qa_i is None:
        out.append(block)
    else:
        out.insert(qa_i, block)
    return out


def merge_layout_blocks(
    base_blocks: list[dict],
    patch_blocks: list[dict],
    *,
    patch_mode: str = "append_or_update",
    max_total: int = 20,
) -> tuple[list[dict], list[str], list[str]]:
    """Merge patch blocks into base by block id.

    Returns ``(blocks, patched_ids, warnings)``.
    """
    mode = (patch_mode or "append_or_update").strip() or "append_or_update"
    if mode not in _VALID_PATCH_MODES:
        mode = "append_or_update"

    warnings: list[str] = []
    if mode == "replace":
        cleaned = [dict(b) for b in patch_blocks if isinstance(b, dict)]
        if len(cleaned) > max_total:
            warnings.append(f"cap: truncated replace to {max_total} blocks")
            cleaned = cleaned[:max_total]
        patched = [str(b.get("id") or "") for b in cleaned if b.get("id")]
        return cleaned, [p for p in patched if p], warnings

    base = [dict(b) for b in base_blocks if isinstance(b, dict)]
    by_id: dict[str, int] = {}
    for i, b in enumerate(base):
        bid = str(b.get("id") or "").strip()
        if bid:
            by_id[bid] = i

    patched_ids: list[str] = []
    for raw in patch_blocks:
        if not isinstance(raw, dict):
            continue
        patch = dict(raw)
        bid = str(patch.get("id") or "").strip()
        if not bid:
            warnings.append("patch block missing id — skipped")
            continue

        if bid in by_id:
            idx = by_id[bid]
            existing = base[idx]
            # Key-wise layout hint merge so span survives an order-only patch.
            base_hint = existing.get("layout") if isinstance(existing.get("layout"), dict) else {}
            patch_hint = patch.get("layout") if isinstance(patch.get("layout"), dict) else None
            merged = dict(existing)
            merged.update(patch)
            if patch_hint is not None:
                merged["layout"] = {**base_hint, **patch_hint}
            elif "layout" in existing and "layout" not in patch:
                merged["layout"] = existing["layout"]
            base[idx] = merged
            patched_ids.append(bid)
        elif mode == "update_only":
            warnings.append(f"update_only: unknown id '{bid}' dropped")
        else:
            # append_or_update — land before trailing quickActions
            if len(base) >= max_total:
                warnings.append(f"cap: dropped append of '{bid}' (max_total={max_total})")
                continue
            base = insert_before_cta(base, patch, max_total=max_total)
            # Refresh index for any later patch against the same new id
            by_id = {}
            for i, b in enumerate(base):
                b_id = str(b.get("id") or "").strip()
                if b_id:
                    by_id[b_id] = i
            patched_ids.append(bid)

    if len(base) > max_total:
        # Cap prefers keeping earlier (base) blocks; drop newest appends.
        warnings.append(f"cap: truncated merged layout to {max_total} blocks")
        base = base[:max_total]
        keep = {str(b.get("id") or "") for b in base}
        patched_ids = [p for p in patched_ids if p in keep]

    return base, patched_ids, warnings


def merge_layout_meta(
    base_meta: dict,
    patch_meta: dict,
    *,
    patched_ids: list[str],
) -> dict:
    """Merge meta: base theme/audience survive; sources union; stamp patched mode."""
    base = dict(base_meta or {}) if isinstance(base_meta, dict) else {}
    patch = dict(patch_meta or {}) if isinstance(patch_meta, dict) else {}
    out = dict(base)

    # Theme / audience / accent survive unless patch explicitly overrides.
    for key in ("theme", "audience", "accent", "themeOverrides"):
        if key in patch and patch[key] is not None and patch[key] != "":
            out[key] = patch[key]

    # Sources: union deduped by ref.
    sources: list[Any] = []
    seen: set[str] = set()
    for bag in (base.get("sources"), patch.get("sources")):
        if not isinstance(bag, list):
            continue
        for s in bag:
            if isinstance(s, dict):
                ref = str(s.get("ref") or "").strip()
                if ref and ref not in seen:
                    seen.add(ref)
                    sources.append(s)
            else:
                ref = str(s).strip()
                if ref and ref not in seen:
                    seen.add(ref)
                    sources.append({"ref": ref})
    if sources:
        out["sources"] = sources

    out["generatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    out["mode"] = "patched"
    out["patchedBlockIds"] = list(patched_ids)
    # Drop private compose keys that should not leak to clients.
    out.pop("_sectionErrors", None)
    return out


def merge_dag_bands(existing_dag: dict | None, blocks: list[dict]) -> dict | None:
    """Keep existing band structure; file new block ids into derived bands.

    Falls back to a full ``_stamp_dag_from_blocks`` when no prior dag exists.
    """
    from plugins.portfolio_plugin.compose.composer import (
        _DAG_COLS_BY_LEVEL,
        _DAG_LEVEL_BY_TYPE,
        _stamp_dag_from_blocks,
    )

    if not isinstance(existing_dag, dict) or not isinstance(existing_dag.get("levels"), list):
        return _stamp_dag_from_blocks(blocks)

    block_ids_ordered: list[tuple[str, str]] = []  # (id, type)
    block_id_set: set[str] = set()
    for b in blocks:
        if not isinstance(b, dict):
            continue
        bid = str(b.get("id") or "").strip()
        if not bid:
            continue
        block_ids_ordered.append((bid, str(b.get("type") or "")))
        block_id_set.add(bid)

    by_level: dict[int, dict[str, Any]] = {}
    for raw in existing_dag.get("levels") or []:
        if not isinstance(raw, dict):
            continue
        try:
            level = int(raw.get("level"))
        except (TypeError, ValueError):
            continue
        nodes = [str(n) for n in (raw.get("nodes") or []) if str(n) in block_id_set]
        entry: dict[str, Any] = {
            "level": level,
            "label": raw.get("label") or "",
            "nodes": nodes,
        }
        if "cols" in raw:
            entry["cols"] = raw["cols"]
        by_level[level] = entry

    known: set[str] = set()
    for entry in by_level.values():
        known.update(entry.get("nodes") or [])

    for bid, btype in block_ids_ordered:
        if bid in known:
            continue
        level, label = _DAG_LEVEL_BY_TYPE.get(btype, (9, "More"))
        entry = by_level.setdefault(
            level,
            {"level": level, "label": label, "nodes": []},
        )
        if not entry.get("label"):
            entry["label"] = label
        cols = _DAG_COLS_BY_LEVEL.get(level)
        if cols is not None:
            entry["cols"] = cols
        entry.setdefault("nodes", []).append(bid)
        known.add(bid)

    # Drop empty bands
    levels = [by_level[k] for k in sorted(by_level.keys()) if by_level[k].get("nodes")]
    if not levels:
        return _stamp_dag_from_blocks(blocks)
    n = max(1, len(levels) - 1)
    for i, lvl in enumerate(levels):
        lvl["at"] = round(i / n, 2)
    return {"levels": levels}


async def compose_patch_layout(
    spec: dict,
    *,
    tenant_id: int = 1,
    base_layout: dict,
    patch_mode: str = "append_or_update",
) -> dict:
    """Compose patch sections and merge into *base_layout*.

    Returns ``{status, layout, patched_block_ids, section_errors, warnings}``.
    """
    from plugins.portfolio_plugin.compose.composer import compose_custom_layout
    from plugins.portfolio_plugin.compose.context_enrich import _MAX_TOTAL_BLOCKS
    from plugins.portfolio_plugin.schema.ui_layout_schema import validate_layout

    if not isinstance(base_layout, dict):
        return {
            "status": "error",
            "errors": ["base_layout: must be a layout object"],
            "section_errors": [],
            "warnings": [],
        }
    base_blocks = base_layout.get("blocks")
    if not isinstance(base_blocks, list):
        return {
            "status": "error",
            "errors": ["base_layout.blocks: must be a list"],
            "section_errors": [],
            "warnings": [],
        }

    raw_spec = dict(spec or {}) if isinstance(spec, dict) else {}
    mode = str(raw_spec.pop("patch_mode", None) or patch_mode or "append_or_update")
    raw_spec.pop("base_layout", None)
    raw_spec.pop("patch_from_short_id", None)

    sections = raw_spec.get("sections")
    if not isinstance(sections, list) or len(sections) == 0:
        return {
            "status": "error",
            "errors": [
                "sections: required non-empty list of block dicts "
                "(from build_layout_block) or named sections"
            ],
            "section_errors": [],
            "warnings": [],
        }

    # Inherit base audience/theme when patch omits them.
    base_meta = base_layout.get("meta") if isinstance(base_layout.get("meta"), dict) else {}
    if not raw_spec.get("audience") and base_meta.get("audience"):
        raw_spec["audience"] = base_meta["audience"]
    if not raw_spec.get("theme") and base_meta.get("theme"):
        raw_spec["theme"] = base_meta["theme"]

    patch_layout, hard_errors = await compose_custom_layout(
        raw_spec,
        tenant_id=int(tenant_id),
        soft_sections=True,
    )
    if hard_errors and patch_layout is None:
        return {
            "status": "error",
            "errors": hard_errors,
            "section_errors": hard_errors,
            "warnings": [],
        }

    section_errors: list[str] = []
    patch_blocks: list[dict] = []
    patch_meta: dict = {}
    if isinstance(patch_layout, dict):
        patch_meta = dict(patch_layout.get("meta") or {})
        sec_errs = patch_meta.pop("_sectionErrors", None)
        if isinstance(sec_errs, list):
            section_errors = [str(e) for e in sec_errs]
        patch_blocks = [b for b in (patch_layout.get("blocks") or []) if isinstance(b, dict)]

    if not patch_blocks:
        return {
            "status": "error",
            "errors": section_errors or hard_errors or ["no patch blocks produced"],
            "section_errors": section_errors or hard_errors or [],
            "warnings": [],
        }

    merged_blocks, patched_ids, warnings = merge_layout_blocks(
        [b for b in base_blocks if isinstance(b, dict)],
        patch_blocks,
        patch_mode=mode,
        max_total=_MAX_TOTAL_BLOCKS,
    )
    merged_meta = merge_layout_meta(base_meta, patch_meta, patched_ids=patched_ids)
    existing_dag = base_meta.get("dag") if isinstance(base_meta.get("dag"), dict) else None
    dag = merge_dag_bands(existing_dag, merged_blocks)
    if dag:
        merged_meta["dag"] = dag

    candidate = {
        "version": int(base_layout.get("version") or 1),
        "meta": merged_meta,
        "blocks": merged_blocks,
    }
    validated, verrs = validate_layout(candidate)
    if validated is None:
        return {
            "status": "error",
            "errors": verrs or ["layout validation failed"],
            "section_errors": section_errors,
            "warnings": warnings,
            "patched_block_ids": patched_ids,
        }
    return {
        "status": "ok",
        "layout": validated,
        "patched_block_ids": patched_ids,
        "section_errors": section_errors,
        "warnings": warnings,
    }
