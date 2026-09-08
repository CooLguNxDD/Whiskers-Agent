"""Portfolio Layout Engine config (plugin settings.portfolio_layout)."""

from __future__ import annotations

from typing import Any


_LAYOUT_HARNESS_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "win_top_k": 3,
    "loss_top_k": 3,
    "similarity_threshold": 0.78,
    "max_block_chars": 2000,
    "min_score_to_record": 6.0,
    # write_wins on: the harness cannot be validated while it writes nothing,
    # and recording a win is inert until something reads it. seed_from_memory
    # is the read side and stays off -- it is the highest-leverage and
    # highest-risk of the three (memory can entrench a mediocre layout).
    "write_wins": True,
    "write_losses": False,
    "seed_from_memory": False,
}

_DEFAULTS: dict[str, Any] = {
    "mode": "auto",  # auto | agentic | fast
    "agentic_goal_classes": ["redesign", "bake_for_job"],
    "jury_threshold": 7.5,
    # Off during soak: with it on, a bake that fails the quality contract
    # persists nothing and the ?j= link 404s (CatPortfolio then renders the
    # master layout). Read portfolio_bake_runs violation counts before flipping.
    "hard_fail_on_quality": False,
    "max_plan_rounds": 3,
    "jury_use_llm": False,
    "direction_lock": True,
    "design_system": "default",
    # More tool-calling room so free-structure agents can explore the catalog.
    "max_agent_steps": 16,
    "max_agent_seconds": 120.0,
    # free = RAG/projects are evidence; agent owns block structure from catalog.
    # recipe_seed = legacy skeleton-first prompt (Ask/scoped may keep this).
    "structure_mode": "free",  # free | recipe_seed
    # Multi-store evidence pack defaults (planner context only; not tool output).
    "evidence_top_k_docs": 28,
    "evidence_top_k_projects": 24,
    "web_enrich": True,
    "anti_skeleton_clone": True,
    # Post-materialize enrichment (timeline/prose/comparison + 2-col cards).
    "context_enrich": True,
    "context_enrich_interactive": True,
    # Prompt budget knobs (manifest can override; previously dropped silently).
    "skill_max_chars": 8000,
    "evidence_max_chars": 9000,
    "evidence_doc_chars": 400,
    "block_catalog_max_chars": 4500,
    "layout_harness": dict(_LAYOUT_HARNESS_DEFAULTS),
}


def get_portfolio_layout_config() -> dict[str, Any]:
    """Return portfolio_layout config from plugin SETTINGS, merged with defaults.

    Source of truth: ``plugins/portfolio_plugin/manifest.json`` →
    ``settings.portfolio_layout`` (not server_config graph).
    """
    raw: dict[str, Any] | None = None
    top_ds = "default"
    try:
        from plugins.portfolio_plugin.plugin_config import SETTINGS

        if isinstance(SETTINGS, dict):
            top_ds = str(SETTINGS.get("design_system") or "default")
            candidate = SETTINGS.get("portfolio_layout")
            if isinstance(candidate, dict):
                raw = candidate
    except Exception:
        raw = None

    out = dict(_DEFAULTS)
    out["layout_harness"] = dict(_LAYOUT_HARNESS_DEFAULTS)  # avoid aliasing the module-level default
    if not out.get("design_system"):
        out["design_system"] = top_ds
    else:
        # Prefer explicit top-level design_system when portfolio_layout omits it
        if raw is None or "design_system" not in (raw or {}):
            out["design_system"] = top_ds

    if isinstance(raw, dict):
        for k, v in raw.items():
            if k == "layout_harness" and isinstance(v, dict):
                # Nested merge, not overwrite -- a manifest override that only
                # sets e.g. {"write_wins": true} must not silently drop the
                # rest of the defaults (the same "unknown keys dropped
                # silently" failure class this whole block exists to avoid).
                out["layout_harness"] = {**out["layout_harness"], **v}
            elif k in _DEFAULTS:
                out[k] = v

    # Normalize agentic classes
    agc = out.get("agentic_goal_classes")
    if isinstance(agc, list):
        out["agentic_goal_classes"] = [str(x) for x in agc]
    else:
        out["agentic_goal_classes"] = list(_DEFAULTS["agentic_goal_classes"])
    try:
        out["jury_threshold"] = float(out.get("jury_threshold", 7.5))
    except (TypeError, ValueError):
        out["jury_threshold"] = 7.5
    try:
        out["max_plan_rounds"] = max(1, min(int(out.get("max_plan_rounds", 3)), 6))
    except (TypeError, ValueError):
        out["max_plan_rounds"] = 3
    try:
        out["max_agent_steps"] = max(1, min(int(out.get("max_agent_steps", 10)), 40))
    except (TypeError, ValueError):
        out["max_agent_steps"] = 10
    try:
        out["max_agent_seconds"] = float(out.get("max_agent_seconds", 90.0) or 90.0)
    except (TypeError, ValueError):
        out["max_agent_seconds"] = 90.0
    out["jury_use_llm"] = bool(out.get("jury_use_llm", False))
    out["direction_lock"] = bool(out.get("direction_lock", True))
    out["web_enrich"] = bool(out.get("web_enrich", True))
    out["anti_skeleton_clone"] = bool(out.get("anti_skeleton_clone", True))
    sm = str(out.get("structure_mode") or "free").strip().lower()
    if sm not in ("free", "recipe_seed"):
        sm = "free"
    out["structure_mode"] = sm
    try:
        out["evidence_top_k_docs"] = max(1, min(int(out.get("evidence_top_k_docs", 12)), 40))
    except (TypeError, ValueError):
        out["evidence_top_k_docs"] = 12
    try:
        out["evidence_top_k_projects"] = max(1, min(int(out.get("evidence_top_k_projects", 6)), 20))
    except (TypeError, ValueError):
        out["evidence_top_k_projects"] = 6

    lh = out.get("layout_harness") if isinstance(out.get("layout_harness"), dict) else {}
    lh_out = dict(_LAYOUT_HARNESS_DEFAULTS)
    lh_out.update({k: v for k, v in lh.items() if k in _LAYOUT_HARNESS_DEFAULTS})
    for flag in ("enabled", "write_wins", "write_losses", "seed_from_memory"):
        lh_out[flag] = bool(lh_out.get(flag))
    for count_key, cap in (("win_top_k", 10), ("loss_top_k", 10)):
        try:
            lh_out[count_key] = max(1, min(int(lh_out.get(count_key, 3)), cap))
        except (TypeError, ValueError):
            lh_out[count_key] = _LAYOUT_HARNESS_DEFAULTS[count_key]
    try:
        lh_out["similarity_threshold"] = float(lh_out.get("similarity_threshold", 0.78))
    except (TypeError, ValueError):
        lh_out["similarity_threshold"] = 0.78
    try:
        lh_out["max_block_chars"] = max(200, int(lh_out.get("max_block_chars", 2000)))
    except (TypeError, ValueError):
        lh_out["max_block_chars"] = 2000
    try:
        lh_out["min_score_to_record"] = float(lh_out.get("min_score_to_record", 6.0))
    except (TypeError, ValueError):
        lh_out["min_score_to_record"] = 6.0
    out["layout_harness"] = lh_out
    out["hard_fail_on_quality"] = bool(out.get("hard_fail_on_quality"))
    mode = str(out.get("mode") or "auto").strip().lower()
    if mode not in ("auto", "agentic", "fast"):
        mode = "auto"
    out["mode"] = mode
    out["design_system"] = str(out.get("design_system") or top_ds or "default")
    return out


def use_agentic_layout(goal_class: str, config: dict[str, Any] | None = None) -> bool:
    """Whether redesign/bake (etc.) should use the layout agent."""
    cfg = config or get_portfolio_layout_config()
    mode = str(cfg.get("mode") or "auto")
    if mode == "fast":
        return False
    if mode == "agentic":
        return True
    # auto
    gclass = (goal_class or "scoped_ask").strip()
    agentic_classes = cfg.get("agentic_goal_classes") or []
    return gclass in agentic_classes


def use_free_structure(goal_class: str, config: dict[str, Any] | None = None) -> bool:
    """Whether planner should treat RAG as evidence (not skeleton-as-layout).

    Default free for bake/redesign when ``structure_mode=free``. Scoped ask
    can still use recipe_seed via config without disabling agentic mode.
    """
    cfg = config or get_portfolio_layout_config()
    if str(cfg.get("structure_mode") or "free") != "free":
        return False
    gclass = (goal_class or "").strip()
    # free structure is most valuable on full-page goals
    return gclass in ("bake_for_job", "redesign") or gclass in (
        cfg.get("agentic_goal_classes") or []
    )
