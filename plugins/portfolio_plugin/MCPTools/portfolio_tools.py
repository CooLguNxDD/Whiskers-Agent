"""
MCP Tools for the Portfolio Plugin.

Exposes tools for managing projects and composing layout.json configurations.
``add_star_story`` / ``get_star_stories`` are deprecated (portfolio_star retired);
bake uses portfolio_projects + portfolio_plugin__context as planner context only.
"""

import json
import logging
from core.context import mcp, current_tenant_id

# Imports required at module top for test patching
from plugins.portfolio_plugin.store import (
    list_projects,
    get_project,
    upsert_project as store_upsert_project,
    delete_project,
)
from plugins.portfolio_plugin.compose import composer
from plugins.portfolio_plugin.themes import SUPPORTED_THEMES, public_raw_defs

logger = logging.getLogger("whiskers.plugins.portfolio.tools")


@mcp.tool(
    title="get_projects",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_projects(audience: str = "", include_inactive: bool = False) -> dict:
    """List portfolio projects, optionally filtered by audience and status."""
    aud = audience if audience else None
    tenant_id = current_tenant_id.get()
    projects = await list_projects(audience=aud, include_inactive=include_inactive, tenant_id=tenant_id)
    return {"status": "ok", "count": len(projects), "projects": projects}


@mcp.tool(
    title="get_star_stories",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_star_stories(query: str, k: int = 3) -> dict:
    """Deprecated: portfolio_star store is retired.

    Bake/layout use portfolio_projects + portfolio_plugin__context as planner
    context only. Prefer ``search_portfolio_context`` / evidence pack.
    """
    if not query or not query.strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["query"],
        }

    # Soft-search context corpus for rows that already carry STAR metadata.
    stories: list[dict] = []
    try:
        from plugins.portfolio_plugin.discovery.index import search_context

        rows = await search_context(
            query, tenant_id=current_tenant_id.get(), top_k=max(int(k) * 4, 8)
        )
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            situation = meta.get("situation")
            task = meta.get("task")
            action = meta.get("action")
            result = meta.get("result")
            if not (situation and task and action and result):
                continue
            stories.append(
                {
                    "id": row.get("id"),
                    "situation": situation,
                    "task": task,
                    "action": action,
                    "result": result,
                    "tags": meta.get("tags") or [],
                    "similarity": row.get("similarity"),
                    "ref": meta.get("ref"),
                }
            )
            if len(stories) >= max(1, int(k)):
                break
    except Exception as exc:
        logger.debug("get_star_stories context fallback: %s", exc)

    return {
        "status": "deprecated",
        "deprecated": True,
        "hint": (
            "portfolio_star is retired. Use portfolio_projects + "
            "portfolio_plugin__context as bake context (not tool output). "
            "Author starStory props from evidence, or search_portfolio_context."
        ),
        "count": len(stories),
        "stories": stories,
    }


@mcp.tool(
    title="upsert_project",
    tags={"portfolio_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def upsert_project(
    slug: str,
    name: str | None = None,
    summary: str | None = None,
    tags: list[str] | None = None,
    metrics: list[dict] | None = None,
    links: list[dict] | None = None,
    audiences: list[str] | None = None,
    sort_order: int | None = None,
    is_active: bool | None = None,
    context_sources: list[dict] | None = None,
) -> dict:
    """Create or update a portfolio project."""
    if not slug or not slug.strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["slug"],
        }

    tenant_id = current_tenant_id.get()
    project = await get_project(slug, tenant_id=tenant_id)
    if project is None:
        missing = []
        if not name or not name.strip():
            missing.append("name")
        if not summary or not summary.strip():
            missing.append("summary")
        if missing:
            return {
                "status": "error",
                "error": "missing_required_fields",
                "missing_fields": missing,
            }

    details = []
    if audiences is not None:
        if not isinstance(audiences, list):
            details.append("audiences must be a list")
        else:
            for idx, a in enumerate(audiences):
                if not isinstance(a, str):
                    details.append(f"audiences[{idx}] must be a string")

    if metrics is not None:
        if not isinstance(metrics, list):
            details.append("metrics must be a list")
        else:
            for idx, m in enumerate(metrics):
                if not isinstance(m, dict):
                    details.append(f"metrics[{idx}] must be a dict")
                    continue
                if "label" not in m or "value" not in m or m.get("label") is None or m.get("value") is None:
                    details.append(f"metrics[{idx}] must contain 'label' and 'value'")

    if links is not None:
        if not isinstance(links, list):
            details.append("links must be a list")
        else:
            for idx, l in enumerate(links):
                if not isinstance(l, dict):
                    details.append(f"links[{idx}] must be a dict")
                    continue
                label = l.get("label")
                href = l.get("href")
                if label is None or href is None:
                    details.append(f"links[{idx}] must contain 'label' and 'href'")
                    continue
                href_str = str(href)
                if not (href_str.startswith("http://") or href_str.startswith("https://")):
                    details.append(f"links[{idx}] href must start with http:// or https://")

    if details:
        return {
            "status": "error",
            "error": "invalid_fields",
            "details": details,
        }

    if context_sources is not None:
        if not isinstance(context_sources, list):
            return {"status": "error", "error": "invalid_fields", "details": ["context_sources must be a list"]}
        allowed_kinds = ("github", "url", "notion", "gdoc")
        for idx, cs in enumerate(context_sources):
            if not isinstance(cs, dict) or not cs.get("kind") or not cs.get("ref"):
                return {
                    "status": "error",
                    "error": "invalid_fields",
                    "details": [f"context_sources[{idx}] must be a dict with 'kind' and 'ref'"],
                }
            kind = str(cs.get("kind", "")).lower()
            if kind not in allowed_kinds:
                return {
                    "status": "error",
                    "error": "invalid_fields",
                    "details": [f"context_sources[{idx}].kind must be one of {list(allowed_kinds)}"],
                }

    fields = {}
    all_args = {
        "name": name,
        "summary": summary,
        "tags": tags,
        "metrics": metrics,
        "links": links,
        "audiences": audiences,
        "sort_order": sort_order,
        "is_active": is_active,
        "context_sources": context_sources,
    }
    for k, v in all_args.items():
        if v is not None:
            fields[k] = v

    updated = await store_upsert_project(slug, tenant_id=tenant_id, **fields)
    return {"status": "ok", "project": updated}


@mcp.tool(
    title="add_star_story",
    tags={"portfolio_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def add_star_story(
    situation: str,
    task: str,
    action: str,
    result: str,
    tags: list[str] | None = None,
) -> dict:
    """Deprecated: portfolio_star store is retired — no longer writes.

    Prefer ``upsert_project`` and discovery indexing into
    ``portfolio_plugin__context``. Layout agents author starStory from
    multi-store evidence at bake time.
    """
    missing = []
    if not situation or not situation.strip():
        missing.append("situation")
    if not task or not task.strip():
        missing.append("task")
    if not action or not action.strip():
        missing.append("action")
    if not result or not result.strip():
        missing.append("result")

    if missing:
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": missing,
        }

    _ = tags  # retained for wire back-compat
    return {
        "status": "deprecated",
        "deprecated": True,
        "error": "portfolio_star_retired",
        "hint": (
            "portfolio_star is retired. Upsert projects and run discovery so "
            "portfolio_plugin__context holds evidence. Bake treats both stores "
            "as planner context only (not tool output)."
        ),
        "written": False,
    }


@mcp.tool(
    title="get_design_context",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_design_context(audience: str = "default") -> dict:
    """Serve design tokens, audience template, hero settings, and supported block
    types for portfolio generation agents.

    Also injects active design-system package (USAGE/DESIGN/tokens) and craft
    module names for the Portfolio Layout Engine prompt plane.
    """
    import plugins.portfolio_plugin.schema.ui_layout_schema as ui_layout_schema
    from plugins.portfolio_plugin.plugin_config import SETTINGS
    from plugins.portfolio_plugin.render.design_system import (
        load_craft_modules,
        load_design_system,
        tokens_to_theme_overrides,
    )
    from plugins.portfolio_plugin.layout.layout_config import get_portfolio_layout_config
    from plugins.portfolio_plugin.schema.block_catalog import fail_open_block_catalog

    normalized, template = composer.get_audience_template(audience)
    presets = SETTINGS.get("layout_presets", {}) or {}
    cfg = get_portfolio_layout_config()
    ds = load_design_system(str(cfg.get("design_system") or "default"))
    craft = load_craft_modules()
    token_overrides = tokens_to_theme_overrides(ds.get("tokens"))
    design_tokens = dict(SETTINGS.get("design_tokens", {}) or {})
    if token_overrides:
        design_tokens = {**design_tokens, **token_overrides}
    return {
        "status": "ok",
        "audience": normalized,
        "audience_template": template,
        "hero": SETTINGS.get("hero"),
        "design_tokens": design_tokens,
        "layout_presets": list(presets.keys()) if isinstance(presets, dict) else [],
        "layout_preset_specs": presets if isinstance(presets, dict) else {},
        "supported_themes": list(SUPPORTED_THEMES),
        "theme_defs": public_raw_defs(),
        "supported_block_types": sorted(ui_layout_schema.BLOCK_TYPES),
        "schema_docstring": ui_layout_schema.__doc__,
        "block_catalog": fail_open_block_catalog(),
        "quick_actions": SETTINGS.get("quick_actions") or [],
        "fragments": [],
        "design_system": {
            "id": ds.get("id"),
            "usage_md": (ds.get("usage_md") or "")[:4000],
            "design_md": (ds.get("design_md") or "")[:5000],
            "tokens": ds.get("tokens") or {},
            "theme_overrides": token_overrides,
        },
        "craft_modules": {k: v[:1500] for k, v in craft.items()},
        "portfolio_layout_config": {
            "mode": cfg.get("mode"),
            "agentic_goal_classes": cfg.get("agentic_goal_classes"),
            "jury_threshold": cfg.get("jury_threshold"),
        },
    }


@mcp.tool(
    title="get_layout_schema_fingerprint",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_layout_schema_fingerprint() -> dict:
    """Return block types and a sha256 of the GenUI schema source so agents
    can detect drift between the TS schema and this Python mirror.

    Canonical module: ``plugins.portfolio_plugin.schema.ui_layout_schema``.
    """
    import hashlib
    import inspect
    import plugins.portfolio_plugin.schema.ui_layout_schema as ui_layout_schema

    source = inspect.getsource(ui_layout_schema)
    return {
        "status": "ok",
        "block_types": sorted(ui_layout_schema.BLOCK_TYPES),
        "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "module": "plugins.portfolio_plugin.schema.ui_layout_schema",
    }


@mcp.tool(
    title="emit_layout",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": False},
)
async def emit_layout(
    audience: str = "default",
    star_query: str = "",
    star_k: int = 0,
    refresh: bool = True,
) -> dict:
    """Compose and emit the complete portfolio UI layout.

    Defaults to live-refresh of project ``context_sources`` (fail-open, no DB
    write). Pass ``refresh=False`` for a pure DB snapshot.
    """
    tenant_id = current_tenant_id.get()
    layout = await composer.compose_layout(
        audience=audience,
        star_query=star_query or None,
        star_k=star_k or None,
        tenant_id=tenant_id,
        refresh=bool(refresh),
    )
    return {"status": "ok", "layout": layout}


@mcp.tool(
    title="design_layout",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": False},
)
async def design_layout(spec: dict | str | None = None, preset: str = "") -> dict:
    """Assemble a creative portfolio layout from a design-builder spec.

    Named sections (hero/statStrip/projectGrid/starStory) pull real DB data
    (the whole ranked inventory — unscoped); dict entries are literal blocks.

    **Prefer the scoped agentic loop over whole-page named sections**: call
    ``search_portfolio_context(query)`` to see what's actually indexed, then
    ``build_layout_block(block_type, query=..., slugs=[...])`` per block you
    want (each call returns ONE validated, scoped, cited block — projectGrid
    with just the projects that matter, prose grounded in retrieved docs,
    etc.), then pass the accumulated block dicts here as literal ``sections``
    entries. Citations ride through automatically into the layout's
    ``meta.sources``. Incomplete composed-section objects and thin archDiagram
    stubs are still auto-filled from portfolio data as a convenience.
    On other validation failures returns structured errors for self-correction.
    Read-only: never writes the database. Prefer over emit_layout for vibe,
    custom section order/scoping, or inline diagrams/prose.

    **Incremental patch (read-only preview):** pass
    ``spec={"base_layout": <layout>, "sections": [block1, block2],
    "patch_mode": "append_or_update"}``. Merges 1–2 blocks into the base by
    block id without persisting. For a durable derived ``?j=`` fork use
    ``patch_job_layout`` instead (chat turns with a short_id session).

    Call with a ``spec`` object (e.g. {"audience": "peer", "theme": "neon",
    "sections": [...]}) and/or a ``preset`` name (e.g. "showcase"). At least
    one of the two is required — never call with empty args. Explicit
    ``sections: []`` is rejected (use omit-sections for template, or pass
    real blocks from ``build_layout_block`` / ``compose_scoped_layout``).
    """
    if isinstance(spec, str):
        text = spec.strip()
        if text:
            try:
                parsed_spec = json.loads(text)
            except (json.JSONDecodeError, TypeError, ValueError):
                return {
                    "status": "error",
                    "errors": ["spec: must be a JSON object (string value was not valid JSON)"],
                    "message": (
                        "design_layout 'spec' argument was a string that failed to parse as JSON. "
                        "Pass a JSON object, e.g. {\"audience\": \"peer\", \"sections\": [...]}."
                    ),
                    "hint": "Pass a design spec with audience/theme/sections (or a preset name).",
                }
            spec = parsed_spec
        else:
            spec = None

    if spec is not None and not isinstance(spec, dict):
        return {
            "status": "error",
            "errors": ["spec: must be a JSON object"],
            "message": "design_layout 'spec' argument must be a JSON object, not a list/number/bool.",
            "hint": "Pass a design spec with audience/theme/sections (or a preset name).",
        }

    if not spec and not preset:
        return {
            "status": "error",
            "errors": ["spec: missing required parameter — pass a spec object or a preset name"],
            "message": (
                "design_layout requires a 'spec' JSON object argument (missing required "
                "parameter 'spec'), or a 'preset' name. Call again with args like "
                '{"spec": {"audience": "peer", "theme": "neon", "sections": [...]}} '
                'or {"preset": "showcase"}.'
            ),
            "hint": "Named sections: hero, statStrip, projectGrid, starStory. Presets: showcase, minimal, deep-dive.",
        }

    tenant_id = current_tenant_id.get()
    # Incremental patch path (read-only): base_layout in spec.
    base_layout = None
    patch_mode = "append_or_update"
    if isinstance(spec, dict):
        base_layout = spec.get("base_layout")
        if isinstance(spec.get("patch_mode"), str) and spec["patch_mode"].strip():
            patch_mode = spec["patch_mode"].strip()
    if isinstance(base_layout, dict):
        from plugins.portfolio_plugin.compose.patch import compose_patch_layout

        result = await compose_patch_layout(
            spec or {},
            tenant_id=int(tenant_id) if tenant_id is not None else 1,
            base_layout=base_layout,
            patch_mode=patch_mode,
        )
        if result.get("status") != "ok":
            errors = result.get("errors") or result.get("section_errors") or ["patch failed"]
            joined = "; ".join(str(e) for e in errors)
            return {
                "status": "error",
                "errors": errors,
                "section_errors": result.get("section_errors") or [],
                "warnings": result.get("warnings") or [],
                "message": f"design_layout patch failed: {joined}",
                "hint": (
                    "Pass 1–2 blocks from build_layout_block as sections, with a full "
                    "base_layout. For durable ?j= forks use patch_job_layout."
                ),
            }
        return {
            "status": "ok",
            "layout": result.get("layout"),
            "patched_block_ids": result.get("patched_block_ids") or [],
            "section_errors": result.get("section_errors") or [],
            "warnings": result.get("warnings") or [],
        }

    layout, errors = await composer.compose_custom_layout(
        spec=spec or {},
        tenant_id=tenant_id,
        preset=preset or "",
    )
    if errors or layout is None:
        joined = "; ".join(errors) if errors else "layout validation failed"
        return {
            "status": "error",
            "errors": errors or ["layout validation failed"],
            "message": (
                f"design_layout spec failed validation (fix and retry, max 2 retries): {joined}"
            ),
            "hint": (
                "Fix blocks and retry (max 2 retries). For scoped/grounded content, use "
                "search_portfolio_context + build_layout_block per block instead of "
                "hand-writing section dicts. Named sections (whole inventory, unscoped): "
                "hero, statStrip, projectGrid, starStory. archDiagram needs "
                "title+kind+source (or a thin query stub — server fills mermaid)."
            ),
        }
    return {"status": "ok", "layout": layout}


@mcp.tool(
    title="list_layout_fragments",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def list_layout_fragments(audience: str = "") -> dict:
    """Deprecated — fragment catalog removed; use floor / compose_scoped_layout."""
    return {
        "status": "error",
        "error": "fragments_removed",
        "hint": "Use compose_scoped_layout or bake_portfolio_for_job / design_layout.",
        "fragments": [],
    }


@mcp.tool(
    title="compose_from_fragments",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": False},
)
async def compose_from_fragments(
    page: list[dict] | None = None,
    audience: str = "default",
    theme: str = "",
    query: str = "",
) -> dict:
    """Deprecated — fragment compose removed; redirects to floor composer."""
    from plugins.portfolio_plugin.compose.floor import build_floor_layout
    from core.context import current_tenant_id
    tid = current_tenant_id() or 1
    layout = await build_floor_layout(
        query or "portfolio",
        tenant_id=int(tid),
        audience=audience or "default",
        theme=theme or "",
    )
    return {
        "status": "ok" if layout.get("blocks") else "error",
        "layout": layout,
        "deprecated": True,
        "mode": "floor",
        "hint": "compose_from_fragments removed; returned floor layout.",
    }


@mcp.tool(
    title="generate_layout_for_query",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": False},
)
async def generate_layout_for_query(user_query: str, session_id: str = "") -> dict:
    """Compose a scoped GenUI layout from free-text (public Ask / layout-for-query).

    Deterministic, non-LLM server path: ranks projects, builds blocks via
    ``build_layout_block``, assembles with multi-column ``layout.span`` —
    no hardcoded regex fragment routing. Falls back to audience template.
    Hidden from GOAP — interactive agents should use
    ``search_portfolio_context`` + ``build_layout_block`` + ``design_layout``
    (or one-shot ``compose_scoped_layout``). ``session_id`` reserved.
    """
    from plugins.portfolio_plugin.compose.intent_compose import compose_intent_layout

    if not user_query or not user_query.strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["user_query"],
        }

    tenant_id = current_tenant_id.get() or 1
    try:
        tenant_id = int(tenant_id)
    except (TypeError, ValueError):
        tenant_id = 1
    return await compose_intent_layout(
        user_query,
        tenant_id=tenant_id,
        refresh=True,
        use_fragments=False,
    )


@mcp.tool(
    title="compose_scoped_layout",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": False},
)
async def compose_scoped_layout_tool(
    query: str,
    theme: str = "",
    top_k: int = 3,
    refresh: bool = False,
) -> dict:
    """One-shot retrieve→build→assemble for a scoped portfolio layout.

    Use when a single ``run_graph`` step must produce a grounded multi-column
    page without multi-turn block accumulation. Prefer the multi-step
    ``search_portfolio_context`` + ``build_layout_block`` × N + ``design_layout``
    loop for fully custom prose/composite authorship.
    """
    from plugins.portfolio_plugin.compose.compose_scoped import compose_scoped_layout

    if not query or not str(query).strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["query"],
        }
    tenant_id = current_tenant_id.get() or 1
    try:
        tenant_id = int(tenant_id)
    except (TypeError, ValueError):
        tenant_id = 1
    return await compose_scoped_layout(
        str(query).strip(),
        tenant_id=tenant_id,
        theme=theme or "",
        refresh=bool(refresh),
        top_k=max(1, min(int(top_k or 3), 10)),
    )


@mcp.tool(
    title="list_layout_recipes",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def list_layout_recipes(goal_class: str = "") -> dict:
    """Deprecated — recipes folded into skills; returns skill meta list."""
    from plugins.portfolio_plugin.layout.skill_meta import list_skill_meta, match_skill_for_goal
    rows = [m.to_public() for m in list_skill_meta()]
    if goal_class:
        rows = [r for r in rows if not r.get("goal_classes") or goal_class in r["goal_classes"]]
    return {"status": "ok", "recipes": rows, "skills": rows, "deprecated": True}


@mcp.tool(
    title="get_layout_recipe",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_layout_recipe(recipe_id: str) -> dict:
    """Deprecated — returns skill meta by name."""
    from plugins.portfolio_plugin.layout.skill_meta import get_skill_meta
    m = get_skill_meta(recipe_id)
    if not m:
        return {"status": "error", "error": "not_found", "deprecated": True}
    return {"status": "ok", "recipe": m.to_public(), "deprecated": True}


@mcp.tool(
    title="critique_layout",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def critique_layout_tool(
    layout: dict | str | None = None,
    query: str = "",
    goal_class: str = "scoped_ask",
    theme: str = "",
    threshold: float = 0.0,
) -> dict:
    """Multi-dim Layout Jury scores (brief fit, evidence, structure, schema, brand)."""
    from plugins.portfolio_plugin.layout.layout_jury import critique_layout

    lay = layout
    if isinstance(layout, str):
        try:
            lay = json.loads(layout)
        except Exception:
            return {"status": "error", "error": "invalid_layout_json"}
    thr = float(threshold) if threshold and float(threshold) > 0 else None
    return await critique_layout(
        lay if isinstance(lay, dict) else None,
        query=query or "",
        goal_class=goal_class or "scoped_ask",
        theme=theme or "",
        threshold=thr,
    )


@mcp.tool(
    title="materialize_layout_plan",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": False},
)
async def materialize_layout_plan_tool(
    plan: dict | str | None = None,
    query: str = "",
    refresh: bool = False,
) -> dict:
    """Materialize a LayoutPlan IR into a schema-validated GenUI layout."""
    from plugins.portfolio_plugin.layout.layout_plan import materialize_layout_plan

    if plan is None:
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["plan"],
        }
    data = plan
    if isinstance(plan, str):
        try:
            data = json.loads(plan)
        except Exception:
            return {"status": "error", "error": "invalid_plan_json"}
    tenant_id = current_tenant_id.get() or 1
    try:
        tenant_id = int(tenant_id)
    except (TypeError, ValueError):
        tenant_id = 1
    return await materialize_layout_plan(
        data if isinstance(data, dict) else {},
        tenant_id=tenant_id,
        query=query or None,
        refresh=bool(refresh),
    )


@mcp.tool(
    title="list_layout_directions",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def list_layout_directions(brief: str = "", theme_hint: str = "") -> dict:
    """Propose 3 visual directions (OD-style direction lock) for a portfolio brief."""
    from plugins.portfolio_plugin.render.design_system import propose_directions

    dirs = propose_directions(brief or "", theme_hint=theme_hint or "")
    return {"status": "ok", "directions": dirs, "recommended": dirs[0] if dirs else None}
