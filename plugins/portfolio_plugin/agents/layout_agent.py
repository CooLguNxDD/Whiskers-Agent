"""Agentic Portfolio Layout Engine — LayoutPlan via agent_loop + materialize + jury."""

from __future__ import annotations

import json
import logging
from typing import Any

from plugins.portfolio_plugin.compose.blackboard import PortfolioDraft, get_draft_store
from plugins.portfolio_plugin.compose.floor import FLOOR_CLONE_TYPES

logger = logging.getLogger("whiskers.plugins.portfolio_plugin.agents.layout_agent")

LAYOUT_PLAN_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "version": {"type": "integer"},
        "recipe_id": {"type": "string"},
        "audience": {"type": "string"},
        "theme": {"type": "string"},
        "theme_overrides": {"type": "object"},
        "direction": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "label": {"type": "string"},
                "theme": {"type": "string"},
                "density": {"type": "string"},
                "lede": {"type": "string"},
            },
        },
        "meta": {"type": "object"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "kind": {"type": "string"},
                    "block_type": {"type": "string"},
                    "query": {"type": "string"},
                    "top_k": {"type": "integer"},
                    "slugs": {"type": "array", "items": {"type": "string"}},
                    "props": {"type": "object"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                    "layout": {
                        "type": "object",
                        "properties": {
                            "span": {"type": "integer"},
                            "order": {"type": "integer"},
                        },
                    },
                    "band": {
                        "type": "object",
                        "properties": {
                            "level": {"type": "integer"},
                            "label": {"type": "string"},
                        },
                    },
                },
                "required": ["id", "block_type"],
            },
        },
        "quality_targets": {"type": "object"},
        "brief": {"type": "string"},
    },
    "required": ["steps"],
}


def _layout_tool_refs():
    from core_graph.agent_loop.spec import ToolRef

    return [
        ToolRef("portfolio_plugin", "*search_portfolio_context"),
        ToolRef("portfolio_plugin", "*get_design_context"),
        ToolRef("portfolio_plugin", "*build_layout_block"),
        ToolRef("portfolio_plugin", "*compose_scoped_layout"),
        ToolRef("portfolio_plugin", "*critique_layout"),
        ToolRef("portfolio_plugin", "*design_layout"),
    ]


def _load_skill_body(skill_name: str, *, max_chars: int = 8000) -> str:
    from plugins.portfolio_plugin.layout.skill_meta import load_skill_body

    return load_skill_body(skill_name or "layout-plan-authoring", max_chars=int(max_chars))


def _seed_plan_from_recipe(
    *,
    query: str,
    goal_class: str,
    theme: str,
    recipe: dict[str, Any] | None,
    previous_errors: list[str] | None,
) -> dict[str, Any]:
    """LLM-down seed: prefer floor composer block plan, else plan_for_goal."""
    from plugins.portfolio_plugin.layout.layout_plan import block_plan_to_layout_plan

    skeleton = list((recipe or {}).get("skeleton") or [])
    quality = (recipe or {}).get("quality") if recipe else None
    recipe_id = (recipe or {}).get("id") or (recipe or {}).get("name")
    if not skeleton:
        from plugins.portfolio_plugin.compose.recipes import plan_for_goal

        skeleton = plan_for_goal(goal_class, query=query)
    plan = block_plan_to_layout_plan(
        skeleton,
        recipe_id=recipe_id,
        theme=theme or (recipe or {}).get("default_theme") or None,
        brief=query,
        quality=quality if isinstance(quality, dict) else None,
    )
    data = plan.model_dump(mode="json", exclude_none=True)
    if previous_errors:
        data["_previous_errors"] = list(previous_errors)[:12]
    return data


async def _seed_plan_from_floor(
    *,
    query: str,
    goal_class: str,
    theme: str,
    recipe: dict[str, Any] | None,
    previous_errors: list[str] | None,
    tenant_id: int = 1,
) -> dict[str, Any]:
    from plugins.portfolio_plugin.compose.floor import build_floor_layout, floor_to_block_plan
    from plugins.portfolio_plugin.layout.layout_plan import block_plan_to_layout_plan

    try:
        floor = await build_floor_layout(query, tenant_id=tenant_id, theme=theme or "")
        skeleton = floor_to_block_plan(floor)
    except Exception:
        skeleton = []
    if not skeleton:
        return _seed_plan_from_recipe(
            query=query,
            goal_class=goal_class,
            theme=theme,
            recipe=recipe,
            previous_errors=previous_errors,
        )
    quality = (recipe or {}).get("quality") if recipe else None
    plan = block_plan_to_layout_plan(
        skeleton,
        recipe_id=(recipe or {}).get("name") or "floor",
        theme=theme or (recipe or {}).get("default_theme") or None,
        brief=query,
        quality=quality if isinstance(quality, dict) else None,
    )
    data = plan.model_dump(mode="json", exclude_none=True)
    data["plan_source"] = "floor"
    if previous_errors:
        data["_previous_errors"] = list(previous_errors)[:12]
    return data


async def run_layout_agent(
    *,
    draft: PortfolioDraft | None = None,
    draft_id: str | None = None,
    query: str = "",
    goal_class: str = "redesign",
    theme: str = "",
    tenant_id: int,
    previous_errors: list[str] | None = None,
    refresh: bool = False,
    company: str = "",
    role: str = "",
    evidence_pack: dict[str, Any] | None = None,
    structure_mode: str | None = None,
) -> dict[str, Any]:
    """Agentic compose: evidence + catalog → LayoutPlan → materialize → jury.

    In free structure mode (default for bake/redesign), RAG/projects are a
    budgeted evidence pack and the agent assembles blocks from the GenUI
    catalog. Recipe skeleton is quality/theme fallback only.

    Falls back to deterministic materialize of recipe skeleton when LLM/agent
    unavailable. Multi-round jury loops up to max_plan_rounds.
    """
    from plugins.portfolio_plugin.render.design_system import (
        auto_pick_direction,
        compose_layout_system_prompt,
        load_design_system,
        propose_directions,
        tokens_to_theme_overrides,
    )
    from plugins.portfolio_plugin.layout.evidence_pack import (
        build_evidence_pack,
        format_block_catalog,
        format_evidence_budget,
        is_floor_clone,
    )
    from plugins.portfolio_plugin.layout.layout_config import (
        get_portfolio_layout_config,
        use_free_structure,
    )
    from plugins.portfolio_plugin.layout.layout_harness import (
        format_layout_harness_block,
        record_layout_antipattern,
        record_layout_success,
        retrieve_layout_harness,
    )
    from plugins.portfolio_plugin.layout.layout_jury import critique_layout
    from plugins.portfolio_plugin.layout.layout_plan import (
        materialize_layout_plan,
        merge_quality_floor,
        parse_layout_plan,
    )
    from plugins.portfolio_plugin.layout.skill_meta import match_skill_for_goal

    cfg = get_portfolio_layout_config()
    store = get_draft_store()
    d = draft or (store.get(draft_id) if draft_id else None)
    q = (query or (d.query if d else "") or (d.goal if d else "")).strip()
    if not q:
        return {
            "status": "error",
            "error": "missing_query",
            "message": "layout agent requires query or draft.goal",
        }

    gclass = goal_class or "redesign"
    free = (
        str(structure_mode).strip().lower() == "free"
        if structure_mode is not None
        else use_free_structure(gclass, cfg)
    )
    structure_mode_s = "free" if free else "recipe_seed"

    rec = match_skill_for_goal(gclass, q)
    recipe_pub = rec.to_public() if rec else None  # skill meta
    theme_s = theme or (d.theme if d else "") or (rec.default_theme if rec else "") or ""

    direction = None
    if cfg.get("direction_lock", True):
        direction = auto_pick_direction(q, theme_hint=theme_s)
        if direction.get("theme") and not theme_s:
            theme_s = direction["theme"]

    ds_id = str(cfg.get("design_system") or "default")
    ds = load_design_system(ds_id)
    overrides = tokens_to_theme_overrides(ds.get("tokens"))
    skill_body = _load_skill_body(
        ((recipe_pub or {}).get("name") or (rec.name if rec else "")) or "layout-plan-authoring",
        max_chars=int(cfg.get("skill_max_chars") or 8000),
    )

    # Evidence pack: multi-store **planner context only** (not layout shape, not tool output)
    pack = evidence_pack if isinstance(evidence_pack, dict) else None
    if pack is None:
        try:
            pack = await build_evidence_pack(
                q,
                tenant_id=int(tenant_id),
                top_k_docs=int(cfg.get("evidence_top_k_docs") or 28),
                top_k_projects=int(cfg.get("evidence_top_k_projects") or 24),
                web_enrich=bool(cfg.get("web_enrich", True)) and free,
                company=company,
                role=role,
            )
        except Exception as exc:
            logger.warning("layout agent evidence pack failed open: %s", exc)
            pack = {
                "status": "error",
                "projects": [],
                "docs": [],
                "web": [],
                "context_only": True,
                "errors": [str(exc)[:200]],
            }

    evidence_budget = format_evidence_budget(
        pack, max_chars=int(cfg.get("evidence_max_chars") or 6500)
    )
    block_catalog = (
        format_block_catalog(max_chars=int(cfg.get("block_catalog_max_chars") or 4500))
        if free
        else ""
    )

    harness_cfg = cfg.get("layout_harness") or {}
    harness_ctx = await retrieve_layout_harness(
        q, tenant_id=int(tenant_id), goal_class=gclass,
        audience=(d.audience if d else "") or "", theme=theme_s,
    )
    harness_block = format_layout_harness_block(
        harness_ctx, max_chars=int(harness_cfg.get("max_block_chars", 2000))
    )

    system_prompt = compose_layout_system_prompt(
        design_system_id=ds_id,
        craft_names=list((recipe_pub or {}).get("craft") or []) or None,
        recipe=recipe_pub,
        skill_body=skill_body,
        harness_block=harness_block,
        evidence_budget=evidence_budget,
        structure_mode=structure_mode_s,
        block_catalog=block_catalog,
    )

    max_rounds = int(cfg.get("max_plan_rounds") or 3)
    prev = list(previous_errors or [])
    best: dict[str, Any] | None = None
    best_score = -1.0
    jury_history: list[dict[str, Any]] = []
    directions = propose_directions(q, theme_hint=theme_s)
    clone_hinted = False

    if d is not None:
        d.phase = "direction" if direction else "recipe"
        d.theme = theme_s or d.theme
        d.query = q
        d.touch()

    for round_i in range(max_rounds):
        plan_dict, plan_source = await _resolve_plan(
            query=q,
            goal_class=gclass,
            theme=theme_s,
            recipe=recipe_pub,
            system_prompt=system_prompt,
            previous_errors=prev,
            direction=direction,
            theme_overrides=overrides or None,
            max_steps=int(cfg.get("max_agent_steps") or 10),
            max_seconds=float(cfg.get("max_agent_seconds") or 90.0),
            tenant_id=int(tenant_id),
            harness_ctx=harness_ctx,
            harness_cfg=harness_cfg,
            jury_threshold=float(cfg.get("jury_threshold") or 7.5),
            # Never seed round 1 from memory -- a brand-new brief gets a
            # genuine first attempt (LLM or plain recipe seed) before memory
            # is allowed to override the seed. Only round 2+ (after jury
            # feedback already exists) can be replaced by a remembered win.
            allow_memory_seed=round_i > 0,
            structure_mode=structure_mode_s,
            evidence_budget=evidence_budget,
            pack=pack,
        )

        # Free mode: if agent cloned the recipe skeleton, force one replan with hard hint
        if (
            free
            and bool(cfg.get("anti_skeleton_clone", True))
            and plan_source == "agent"
            and is_floor_clone(plan_dict, (recipe_pub or {}).get('skeleton') or FLOOR_CLONE_TYPES)
            and round_i + 1 < max_rounds
            and not clone_hinted
        ):
            logger.info("layout agent: skeleton clone detected — forcing replan")
            clone_hinted = True
            prev = list(prev) + [
                "structure_clone: plan matched recipe skeleton type sequence — "
                "restructure: change order/types, drop filler widgets if weak, "
                "emphasize job-relevant projects with explicit slugs"
            ]
            plan_dict, plan_source = await _resolve_plan(
                query=q,
                goal_class=gclass,
                theme=theme_s,
                recipe=recipe_pub,
                system_prompt=system_prompt,
                previous_errors=prev,
                direction=direction,
                theme_overrides=overrides or None,
                max_steps=int(cfg.get("max_agent_steps") or 10),
                max_seconds=float(cfg.get("max_agent_seconds") or 90.0),
                tenant_id=int(tenant_id),
                harness_ctx=harness_ctx,
                harness_cfg=harness_cfg,
                jury_threshold=float(cfg.get("jury_threshold") or 7.5),
                allow_memory_seed=False,
                structure_mode=structure_mode_s,
                evidence_budget=evidence_budget,
                pack=pack,
            )

        if d is not None:
            d.phase = "plan"
            d.touch()

        try:
            plan_obj = parse_layout_plan(plan_dict)
        except Exception as exc:
            logger.info("layout agent plan parse fail round %s: %s", round_i, exc)
            plan_dict = _seed_plan_from_recipe(
                query=q,
                goal_class=gclass,
                theme=theme_s,
                recipe=recipe_pub,
                previous_errors=prev,
            )
            plan_source = "seed"
            if direction:
                plan_dict["direction"] = direction
            if overrides:
                plan_dict["theme_overrides"] = overrides
            plan_obj = parse_layout_plan(plan_dict)

        if d is not None:
            d.phase = "materialize"
            d.touch()

        mat = await materialize_layout_plan(
            plan_obj,
            tenant_id=int(tenant_id),
            query=q,
            refresh=bool(refresh) or round_i > 0,
        )
        layout = mat.get("layout") if isinstance(mat, dict) else None

        # Steps that silently failed to build (e.g. an authored chart/comparison
        # with unresolvable source_refs) previously vanished with only a debug
        # log — the agent never learned a type it picked didn't actually ship.
        # Fold them into must_fix feedback so the next round re-plans around
        # the failure instead of repeating it blind (bake-parity fix).
        # Compare the formatted string against prev (which stores step_failed:…).
        mat_step_errors = (mat or {}).get("step_errors") if isinstance(mat, dict) else None
        if isinstance(mat_step_errors, list) and mat_step_errors:
            for e in mat_step_errors[:8]:
                formatted = f"step_failed: {e}"
                if formatted not in prev:
                    prev = list(prev) + [formatted]

        # Plan may raise the recipe's quality bar, never lower it (merge_quality_floor).
        recipe_quality = (recipe_pub or {}).get("quality") if recipe_pub else None
        quality = merge_quality_floor(recipe_quality, plan_obj.quality_targets.model_dump())
        jury = await critique_layout(
            layout if isinstance(layout, dict) else None,
            query=q,
            goal_class=gclass,
            theme=theme_s,
            quality=quality if isinstance(quality, dict) else None,
        )
        jury_history.append(
            {
                "round": round_i + 1,
                "composite": jury.get("composite"),
                "passed": jury.get("passed"),
                "must_fix": jury.get("must_fix"),
            }
        )

        if d is not None:
            d.phase = "critique"
            if isinstance(layout, dict):
                d.layout = layout
                blocks = layout.get("blocks")
                if isinstance(blocks, list):
                    d.sections = [b for b in blocks if isinstance(b, dict)]
            d.touch()

        score = float(jury.get("composite") or 0.0)
        # Stamp free-mode provenance onto layout.meta for bake/debug
        if isinstance(layout, dict):
            meta = layout.get("meta") if isinstance(layout.get("meta"), dict) else {}
            meta = dict(meta)
            meta["structureMode"] = structure_mode_s
            if isinstance(pack, dict) and pack.get("pack_hash"):
                inv = pack.get("inventory") if isinstance(pack.get("inventory"), dict) else {}
                # Meta counts only — never stamp raw projects/docs onto layout output.
                meta["evidence"] = {
                    "pack_hash": pack.get("pack_hash"),
                    "context_only": True,
                    "project_count": inv.get("project_count", len(pack.get("projects") or [])),
                    "doc_count": inv.get("context_docs_in_pack", len(pack.get("docs") or [])),
                    "context_index_count": inv.get("context_index_count"),
                    "virtual_count": inv.get(
                        "virtual_project_count", len(pack.get("virtual_projects") or [])
                    ),
                    "web_count": len(pack.get("web") or []),
                }
            if free and is_floor_clone(layout, (recipe_pub or {}).get('skeleton') or FLOOR_CLONE_TYPES):
                meta["structureClone"] = True
            layout = {**layout, "meta": meta}

        candidate = {
            "status": mat.get("status") if isinstance(mat, dict) else "error",
            "layout": layout,
            "audience": (mat or {}).get("audience") if isinstance(mat, dict) else None,
            "mode": (mat or {}).get("mode") if isinstance(mat, dict) else None,
            "plan": plan_obj.model_dump(mode="json", exclude_none=True),
            "jury": jury,
            "recipe_id": (recipe_pub or {}).get("id"),
            "direction": direction,
            "directions": directions,
            "goal_class": gclass,
            "engine": "layout_agent",
            "round": round_i + 1,
            "plan_source": plan_source,
            "structure_mode": structure_mode_s,
            "evidence_pack_hash": (pack or {}).get("pack_hash") if isinstance(pack, dict) else None,
        }
        if score >= best_score and isinstance(layout, dict):
            best_score = score
            best = candidate

        if jury.get("passed"):
            if d is not None:
                d.phase = "compose"
                d.touch()
                candidate["draft"] = d.to_public()
            candidate["jury_history"] = jury_history
            candidate["status"] = "ok" if isinstance(layout, dict) else candidate.get("status")
            if isinstance(layout, dict):
                await record_layout_success(
                    tenant_id=int(tenant_id), query=q, goal_class=gclass, plan=plan_obj,
                    layout=layout, jury=jury, recipe_id=(recipe_pub or {}).get("id"),
                    direction_id=(direction or {}).get("id"),
                )
            return candidate

        prev = list(jury.get("must_fix") or []) + [
            e for e in prev if e not in (jury.get("must_fix") or [])
        ]

    # ship_best -- jury never passed. Record the best loser only (never every
    # failing round: noise + unbounded growth).
    if best is not None and isinstance(best.get("layout"), dict):
        try:
            from plugins.portfolio_plugin.layout.layout_plan import parse_layout_plan as _parse

            best_plan_obj = _parse(best["plan"]) if isinstance(best.get("plan"), dict) else None
        except Exception:
            best_plan_obj = None
        if best_plan_obj is not None:
            await record_layout_antipattern(
                tenant_id=int(tenant_id), query=q, goal_class=gclass, plan=best_plan_obj,
                jury=best.get("jury") or {}, recipe_id=(recipe_pub or {}).get("id"),
                layout=best.get("layout"),
            )

    out = best or {
        "status": "error",
        "error": "layout_agent_failed",
        "message": "No layout produced",
        "goal_class": gclass,
        "engine": "layout_agent",
    }
    out["ship_best"] = True
    out["jury_history"] = jury_history
    if d is not None and isinstance(out.get("layout"), dict):
        d.layout = out["layout"]
        d.phase = "compose"
        d.touch()
        out["draft"] = d.to_public()
    # A best loser is not an approved page. Report it as "partial" and let the
    # caller's quality contract decide — forcing "ok" here is what let a layout
    # that failed the jury every round ship as a clean bake.
    if isinstance(out.get("layout"), dict):
        out["status"] = "partial"
    return out


async def _resolve_plan(
    *,
    query: str,
    goal_class: str,
    theme: str,
    recipe: dict[str, Any] | None,
    system_prompt: str,
    previous_errors: list[str],
    direction: dict[str, str] | None,
    theme_overrides: dict[str, str] | None,
    max_steps: int,
    max_seconds: float,
    tenant_id: int,
    harness_ctx: Any = None,
    harness_cfg: dict[str, Any] | None = None,
    jury_threshold: float = 7.5,
    allow_memory_seed: bool = True,
    structure_mode: str = "recipe_seed",
    evidence_budget: str = "",
    pack: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """Try agent_loop for LayoutPlan; fall back to recipe seed.

    Returns ``(plan_dict, plan_source)`` where ``plan_source`` is ``"agent"``
    when the LLM tool-calling loop actually produced the plan, ``"memory"``
    when a remembered win replaced the seed's steps (``seed_plan_from_memory``
    -- see ``plugins.portfolio_plugin.layout.layout_harness``), or ``"seed"`` when
    neither applied. Callers must not fold this into the plan dict itself:
    both ``LayoutPlan`` and ``UILayoutMeta`` are extra="ignore" and would
    silently swallow an in-band marker the same way the missing tenant_id
    argument was silently swallowed before this fix.

    Free structure mode still keeps a recipe skeleton only as **LLM-down
    fallback** — it is not pasted into the agent user prompt as the page to
    copy.
    """
    free = str(structure_mode or "").strip().lower() == "free"
    if free:
        seed = await _seed_plan_from_floor(
            query=query,
            goal_class=goal_class,
            theme=theme,
            recipe=recipe,
            previous_errors=previous_errors,
            tenant_id=int(tenant_id),
        )
    else:
        seed = _seed_plan_from_recipe(
            query=query,
            goal_class=goal_class,
            theme=theme,
            recipe=recipe,
            previous_errors=previous_errors,
        )
    if direction:
        seed["direction"] = direction
        if direction.get("theme") and not seed.get("theme"):
            seed["theme"] = direction["theme"]
    if theme_overrides:
        seed["theme_overrides"] = theme_overrides

    # Free mode: give the agent a minimal shell (theme/quality), not full skeleton
    agent_hint_plan: dict[str, Any]
    if free:
        agent_hint_plan = {
            "version": 1,
            "recipe_id": (recipe or {}).get("id"),
            "theme": seed.get("theme") or theme or None,
            "theme_overrides": theme_overrides,
            "direction": direction,
            "quality_targets": seed.get("quality_targets") or (recipe or {}).get("quality"),
            "brief": query,
            "steps": [],  # agent fills from catalog + evidence
            "_hint": (
                "Fill steps[] from BLOCK CATALOG + EVIDENCE. "
                "Example patterns (not mandatory): hero→proof band→deep dive→CTA."
            ),
        }
        # Soft suggest primary project slugs from evidence pack
        if isinstance(pack, dict):
            slugs = [
                str(p.get("slug"))
                for p in (pack.get("projects") or [])
                if isinstance(p, dict) and p.get("slug")
            ][:6]
            if slugs:
                agent_hint_plan["_available_slugs"] = slugs
    else:
        agent_hint_plan = seed

    seeded_from_memory = False
    if (
        not free
        and harness_ctx is not None
        and (harness_cfg or {}).get("seed_from_memory", False)
    ):
        from plugins.portfolio_plugin.layout.layout_harness import seed_plan_from_memory

        seed = seed_plan_from_memory(
            seed,
            harness_ctx,
            similarity_threshold=float((harness_cfg or {}).get("similarity_threshold", 0.78)),
            min_score=jury_threshold + 0.5,
            allow=allow_memory_seed,
        )
        seeded_from_memory = bool(seed.pop("_seeded_from_memory", False))
        agent_hint_plan = seed
    plan_source_fallback = "memory" if seeded_from_memory else "seed"

    try:
        from core.llm_provider_management import llm_available

        if not llm_available():
            return seed, plan_source_fallback
    except Exception:
        return seed, plan_source_fallback

    try:
        from core_graph.agent_loop.runner import run_agent
        from core_graph.agent_loop.spec import AgentSpec
        from core.scope_management import get_request_principal

        principal = get_request_principal()
        caller_scopes = principal.scopes if principal is not None else None

        spec = AgentSpec(
            name="portfolio_layout",
            system_prompt=system_prompt,
            tools=_layout_tool_refs(),
            max_steps=max_steps,
            max_seconds=max_seconds,
            output_schema=LAYOUT_PLAN_OUTPUT_SCHEMA,
            caller_scopes=caller_scopes,
        )
        err_hint = ""
        if previous_errors:
            err_hint = "\nPrevious jury must_fix (address these):\n- " + "\n- ".join(
                previous_errors[:10]
            )
        if free:
            user_prompt = (
                f"Create a LayoutPlan for goal_class={goal_class} (FREE structure).\n"
                f"Brief: {query}\n"
                f"Preferred theme: {theme or 'auto'}\n"
                "Evidence is already in the system prompt — ground every claim there.\n"
                "Do NOT copy a fixed skeleton. Assemble steps from the BLOCK CATALOG.\n"
                f"Plan shell (fill steps):\n```json\n{json.dumps(agent_hint_plan, indent=2)[:2500]}\n```"
                f"{err_hint}\n"
                "Return only the LayoutPlan JSON with a non-empty steps array."
            )
        else:
            user_prompt = (
                f"Create a LayoutPlan for goal_class={goal_class}.\n"
                f"Brief: {query}\n"
                f"Preferred theme: {theme or 'auto'}\n"
                f"Seed skeleton (you may restructure):\n```json\n{json.dumps(agent_hint_plan, indent=2)[:3500]}\n```"
                f"{err_hint}\n"
                "Search portfolio context before adding authored prose/composite. "
                "Return only the LayoutPlan JSON."
            )
        result = await run_agent(spec, user_prompt, tenant_id=tenant_id)
        output = getattr(result, "output", None) or (
            result.get("output") if isinstance(result, dict) else None
        )
        if isinstance(output, dict) and output.get("steps"):
            # Merge seed defaults
            if not output.get("theme") and seed.get("theme"):
                output["theme"] = seed["theme"]
            if not output.get("theme_overrides") and theme_overrides:
                output["theme_overrides"] = theme_overrides
            if not output.get("direction") and direction:
                output["direction"] = direction
            if not output.get("recipe_id") and seed.get("recipe_id"):
                output["recipe_id"] = seed["recipe_id"]
            if not output.get("quality_targets") and seed.get("quality_targets"):
                output["quality_targets"] = seed["quality_targets"]
            output["brief"] = output.get("brief") or query
            return output, "agent"
    except Exception:
        logger.exception("layout agent LLM path failed, using seed plan")

    return seed, plan_source_fallback
