"""Portfolio layout harness — retrieve/record past juried LayoutPlan structures.

Domain analogue of ``core_graph/harness`` (retrieve_harness / soft_apply_recipe_to_plan
/ record_success_recipe), deliberately NOT built on top of it: every signature there is
typed to GOAP operation chains (``recipes[].op_ids``, ``pick_best_recipe(recipes,
candidates)``, ``_op_ids_from_state`` reading ``state["plan"][].operation_id``).
Layout memory is about matrix bands and block types, not operation ids -- generalizing
the GOAP harness would mean overloading ``op_ids`` to mean "block types", the same
hidden-coupling pathology that produced the Phase 1 bug class this whole engine exists
to fix. Same pattern, same config shape (``graph.harness`` <-> ``layout_harness``),
independent code.

Dark-launched: ``write_wins`` / ``write_losses`` / ``seed_from_memory`` all default to
``False`` in ``layout_config._LAYOUT_HARNESS_DEFAULTS`` -- flip once the record shape
and replace-stale behavior are verified on a real run.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.layout_harness")

_OWNER = "portfolio_plugin"
_WIN_NAME = "layouts"
_LOSS_NAME = "layout_antipatterns"


def _collections() -> tuple[str, str]:
    from core.memory.registry import plugin_collection

    return plugin_collection(_OWNER, _WIN_NAME), plugin_collection(_OWNER, _LOSS_NAME)


def _namespace_registered(collection: str) -> bool:
    from core.memory.registry import get_memory_registry

    return get_memory_registry().get(collection) is not None


@dataclass
class LayoutHarnessContext:
    """Retrieved harness state for one planning attempt."""

    enabled: bool = True
    wins: list[dict[str, Any]] = field(default_factory=list)
    losses: list[dict[str, Any]] = field(default_factory=list)
    best: dict[str, Any] | None = None
    block: str = ""


def _band_signature(dag: dict[str, Any] | None) -> list[list[Any]]:
    """[(level, label, node_count), ...] -- compact, stable band shape for
    both the embedded content_text and the memory dedupe key."""
    if not isinstance(dag, dict):
        return []
    levels = dag.get("levels")
    if not isinstance(levels, list):
        return []
    out: list[list[Any]] = []
    for lvl in levels:
        if not isinstance(lvl, dict):
            continue
        nodes = lvl.get("nodes") or []
        out.append([lvl.get("level"), str(lvl.get("label") or ""), len(nodes) if isinstance(nodes, list) else 0])
    return out


def _plan_steps_meta(plan: Any) -> list[dict[str, Any]]:
    """Strip a LayoutPlan/dict down to {id,block_type,kind,band,top_k,query} --
    exactly enough to re-materialize via parse_layout_plan + materialize_layout_plan,
    at ~1KB instead of the full layout's ~80KB. NEVER props/prose/slugs (visitor-
    facing content has no business in a shared cross-session memory store)."""
    steps = getattr(plan, "steps", None)
    if steps is None and isinstance(plan, dict):
        steps = plan.get("steps")
    out: list[dict[str, Any]] = []
    for s in steps or []:
        if hasattr(s, "model_dump"):
            s = s.model_dump(mode="json", exclude_none=True)
        if not isinstance(s, dict):
            continue
        entry = {
            "id": s.get("id"),
            "block_type": s.get("block_type"),
            "top_k": s.get("top_k"),
            "query": s.get("query"),
        }
        if s.get("kind") and s["kind"] != "auto":
            entry["kind"] = s["kind"]
        if s.get("band"):
            entry["band"] = s["band"]
        out.append(entry)
    return out


def _block_types(layout: dict[str, Any] | None) -> list[str]:
    if not isinstance(layout, dict):
        return []
    blocks = layout.get("blocks")
    if not isinstance(blocks, list):
        return []
    return [str(b.get("type") or "") for b in blocks if isinstance(b, dict) and b.get("type")]


def _layout_key(*, recipe_id: str | None, goal_class: str, audience: str, block_types: list[str], band_sig: list) -> str:
    raw = json.dumps(
        {"recipe_id": recipe_id, "goal_class": goal_class, "audience": audience, "block_types": block_types, "band_sig": band_sig},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_content_text(
    *,
    goal_class: str,
    audience: str,
    theme: str,
    recipe_id: str | None,
    score: float,
    query: str,
    band_sig: list,
    block_types: list[str],
    dimensions: dict[str, Any] | None,
    must_fix: list[str],
) -> str:
    """Must vary per round or UNIQUE(collection, content_hash, model) silently
    returns deduped:True -- score/dims/must_fix/timestamp all vary, so the
    embedding text does too. Replace-stale (by layout_key) is what actually
    bounds growth, not this dedupe."""
    bands = " › ".join(f"L{lvl} {label}[{n}]" for lvl, label, n in band_sig) or "(no bands)"
    dims = " ".join(f"{k}={v:.2f}" for k, v in (dimensions or {}).items()) if dimensions else ""
    mf = "; ".join(must_fix) if must_fix else "none"
    return (
        f"Layout {goal_class} · audience={audience} · theme={theme or 'auto'} · "
        f"recipe={recipe_id or 'none'} · score={score:.2f}\n"
        f"Brief: {query[:240]}\n"
        f"Bands: {bands}\n"
        f"Blocks: {','.join(block_types)}\n"
        f"Dims: {dims}\n"
        f"MustFix: {mf}\n"
        f"At: {datetime.now(timezone.utc).isoformat()}"
    )


async def retrieve_layout_harness(
    query: str,
    *,
    tenant_id: int,
    goal_class: str,
    audience: str = "",
    theme: str = "",
) -> LayoutHarnessContext:
    """Fetch past winning/losing LayoutPlan structures for a similar brief,
    scoped to this goal_class at the SQL level. Fails open to a disabled,
    empty context -- never blocks planning."""
    from plugins.portfolio_plugin.layout.layout_config import get_portfolio_layout_config

    cfg = (get_portfolio_layout_config() or {}).get("layout_harness") or {}
    if not cfg.get("enabled", True):
        return LayoutHarnessContext(enabled=False)

    win_collection, loss_collection = _collections()
    if not (_namespace_registered(win_collection) and _namespace_registered(loss_collection)):
        logger.warning("layout_harness: namespace not registered, skipping retrieve")
        return LayoutHarnessContext(enabled=False)

    try:
        from db_layer.search_content_vectors_store import search_search_content_vectors_filtered

        wins, losses = [], []
        if query and query.strip():
            wins = await search_search_content_vectors_filtered(
                query, win_collection, int(cfg.get("win_top_k", 3)),
                tenant_id=tenant_id, meta_equals={"goal_class": goal_class},
            )
            losses = await search_search_content_vectors_filtered(
                query, loss_collection, int(cfg.get("loss_top_k", 3)),
                tenant_id=tenant_id, meta_equals={"goal_class": goal_class},
            )
    except Exception:
        logger.exception("layout_harness: retrieve failed, continuing without memory")
        return LayoutHarnessContext(enabled=True)

    best = None
    if wins:
        best = max(wins, key=lambda w: float((w.get("metadata") or {}).get("score") or 0.0))
    return LayoutHarnessContext(enabled=True, wins=wins, losses=losses, best=best)


def format_layout_harness_block(ctx: LayoutHarnessContext, *, max_chars: int = 2000) -> str:
    """Render the prompt plane inserted after '## SELECTED RECIPE', before
    '## SKILL' in compose_layout_system_prompt -- memory reads as a
    refinement of the skeleton, not a competitor."""
    if not ctx.enabled or (not ctx.wins and not ctx.losses):
        return ""
    lines = ["## LAYOUT MEMORY (past juried pages)"]
    if ctx.wins:
        lines.append("### Winners")
        for w in ctx.wins:
            meta = w.get("metadata") or {}
            first_line = str(w.get("content_text") or "").splitlines()[0] if w.get("content_text") else ""
            lines.append(f"- {meta.get('score', '?')} · {first_line}")
    if ctx.losses:
        lines.append("### Avoid")
        for loss in ctx.losses:
            meta = loss.get("metadata") or {}
            mf = ", ".join(meta.get("must_fix") or [])[:200]
            first_line = str(loss.get("content_text") or "").splitlines()[0] if loss.get("content_text") else ""
            lines.append(f"- {meta.get('score', '?')} · {first_line} — {mf}")
    block = "\n".join(lines)
    return block[:max_chars]


def seed_plan_from_memory(
    seed: dict[str, Any],
    ctx: LayoutHarnessContext,
    *,
    similarity_threshold: float,
    min_score: float,
    allow: bool = True,
) -> dict[str, Any]:
    """Replace the seed's steps with the best remembered win's plan_steps when
    it clearly outperformed this recipe's own skeleton -- the analogue of
    soft_apply_recipe_to_plan. Keeps the recipe's id/theme/quality floor.

    This is what makes round 2 differ from round 1 even with NO LLM at all
    (the deterministic path was previously the only path, and it never
    varied) -- but memory can also entrench a mediocre layout, so it fires
    only when the remembered win clears BOTH the retrieval similarity
    threshold and a minimum score, and ``allow`` lets a caller veto seeding
    outright (e.g. the very first bake for a new company/role with no prior
    record must not inherit a stranger's structure sight-unseen).
    """
    if not allow or not ctx.enabled or not ctx.best:
        return seed
    meta = ctx.best.get("metadata") or {}
    plan_steps = meta.get("plan_steps")
    similarity = ctx.best.get("similarity")
    score = meta.get("score")
    if not isinstance(plan_steps, list) or not plan_steps:
        return seed
    if similarity is not None and similarity < similarity_threshold:
        return seed
    if score is None or float(score) < min_score:
        return seed
    out = dict(seed)
    out["steps"] = plan_steps
    out["_seeded_from_memory"] = True
    return out


async def record_layout_success(
    *,
    tenant_id: int,
    query: str,
    goal_class: str,
    plan: Any,
    layout: dict[str, Any],
    jury: dict[str, Any],
    recipe_id: str | None = None,
    direction_id: str | None = None,
    short_id: str | None = None,
) -> dict[str, Any] | None:
    """Write the winning round. Fail-open (never raises into the caller)."""
    from plugins.portfolio_plugin.layout.layout_config import get_portfolio_layout_config

    cfg = (get_portfolio_layout_config() or {}).get("layout_harness") or {}
    if not cfg.get("enabled", True) or not cfg.get("write_wins", False):
        return None
    score = float(jury.get("composite") or 0.0)
    if score < float(cfg.get("min_score_to_record", 6.0)):
        return None
    return await _record(
        collection_name=_WIN_NAME, tenant_id=tenant_id, query=query, goal_class=goal_class,
        plan=plan, layout=layout, jury=jury, recipe_id=recipe_id, direction_id=direction_id,
        short_id=short_id, passed=True,
    )


async def record_layout_antipattern(
    *,
    tenant_id: int,
    query: str,
    goal_class: str,
    plan: Any,
    jury: dict[str, Any],
    recipe_id: str | None = None,
    layout: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Write the best-loser round only -- never every failing round (noise +
    unbounded growth). Fail-open."""
    from plugins.portfolio_plugin.layout.layout_config import get_portfolio_layout_config

    cfg = (get_portfolio_layout_config() or {}).get("layout_harness") or {}
    if not cfg.get("enabled", True) or not cfg.get("write_losses", False):
        return None
    return await _record(
        collection_name=_LOSS_NAME, tenant_id=tenant_id, query=query, goal_class=goal_class,
        plan=plan, layout=layout, jury=jury, recipe_id=recipe_id, direction_id=None,
        short_id=None, passed=False,
    )


async def _record(
    *,
    collection_name: str,
    tenant_id: int,
    query: str,
    goal_class: str,
    plan: Any,
    layout: dict[str, Any] | None,
    jury: dict[str, Any],
    recipe_id: str | None,
    direction_id: str | None,
    short_id: str | None,
    passed: bool,
) -> dict[str, Any] | None:
    from core.memory.registry import plugin_collection
    from db_layer.search_content_vectors_store import (
        add_search_content_vector,
        delete_search_content_vectors_by_meta,
    )

    collection = plugin_collection(_OWNER, collection_name)
    if not _namespace_registered(collection):
        logger.warning("layout_harness: %s not registered, skipping write", collection)
        return None

    audience = getattr(plan, "audience", None) or (plan.get("audience") if isinstance(plan, dict) else None) or "default"
    theme = getattr(plan, "theme", None) or (plan.get("theme") if isinstance(plan, dict) else None) or ""
    plan_steps = _plan_steps_meta(plan)
    block_types = _block_types(layout)
    dag = (layout or {}).get("meta", {}).get("dag") if isinstance(layout, dict) else None
    band_sig = _band_signature(dag)
    score = float(jury.get("composite") or 0.0)
    must_fix = list(jury.get("must_fix") or [])
    theme_overrides = (layout or {}).get("meta", {}).get("themeOverrides") if isinstance(layout, dict) else None
    layout_key = _layout_key(
        recipe_id=recipe_id, goal_class=goal_class, audience=audience,
        block_types=block_types, band_sig=band_sig,
    )

    content_text = _build_content_text(
        goal_class=goal_class, audience=audience, theme=theme, recipe_id=recipe_id,
        score=score, query=query, band_sig=band_sig, block_types=block_types,
        dimensions=jury.get("dimensions"), must_fix=must_fix,
    )
    meta = {
        "kind": "layout_record" if passed else "layout_antipattern",
        "goal_class": goal_class,
        "audience": audience,
        "theme": theme,
        "recipe_id": recipe_id,
        "direction_id": direction_id,
        "score": score,
        "passed": str(passed).lower(),
        "dimensions": jury.get("dimensions") or {},
        "must_fix": must_fix,
        "block_types": block_types,
        "band_signature": band_sig,
        "plan_steps": plan_steps,
        "theme_overrides_keys": sorted(theme_overrides.keys()) if isinstance(theme_overrides, dict) else [],
        "layout_key": layout_key,
        "short_id": short_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        # Replace-stale: one winner survives per structural signature, a
        # better-scoring re-run supersedes rather than accumulating rows
        # forever (the "At:" timestamp alone in content_text would otherwise
        # defeat the content_hash unique-constraint dedupe entirely).
        await delete_search_content_vectors_by_meta(collection, "layout_key", layout_key, tenant_id=tenant_id)
        return await add_search_content_vector(collection, content_text, meta, tenant_id=tenant_id)
    except Exception:
        logger.exception("layout_harness: write to %s failed", collection)
        return None
