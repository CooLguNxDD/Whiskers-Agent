"""
Plugin-scoped configuration for portfolio_plugin.

Operational settings live in ``manifest.json`` (discovery, allowlists).
Design surface (hero, presets, tokens, quick_actions, audiences) lives under
``design_systems/<id>/settings.json`` and is merged into ``SETTINGS`` at import.
Project inventory is discovery write-back + MCP ``upsert_project`` — no seed script.
"""

import json
import logging
from pathlib import Path
from typing import Any

from core.plugin_loader.plugin_registry import Plugin, PluginContext

logger = logging.getLogger("whiskers.plugins.portfolio.config")

_PLUGIN_DIR = Path(__file__).parent
_manifest = json.loads((_PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))

PLUGIN_ID = _manifest.get("name", _PLUGIN_DIR.name)

# Keys that belong to the design-system package, not operational plugin config.
DESIGN_SETTING_KEYS: tuple[str, ...] = (
    "hero",
    "quick_actions",
    "audiences",
    "layout_presets",
    "design_tokens",
)


def _load_design_settings(design_system_id: str = "default") -> dict[str, Any]:
    """Load design surface settings from design_systems/<id>/settings.json."""
    ds_id = (design_system_id or "default").strip() or "default"
    if ".." in ds_id or "/" in ds_id or "\\" in ds_id:
        ds_id = "default"
    path = _PLUGIN_DIR / "design_systems" / ds_id / "settings.json"
    if not path.is_file():
        # Fall back to default package
        path = _PLUGIN_DIR / "design_systems" / "default" / "settings.json"
    if not path.is_file():
        logger.warning("portfolio design settings missing at %s", path)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("portfolio design settings unreadable %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("portfolio design settings must be a JSON object: %s", path)
        return {}
    return {k: data[k] for k in DESIGN_SETTING_KEYS if k in data}


def _build_settings() -> dict[str, Any]:
    """Merge manifest operational settings + design-system package settings.

    Design keys from ``design_systems/<id>/settings.json`` are the source of
    truth. Legacy design keys still present in manifest.json are ignored so the
    package is the single home for hero/presets/tokens.
    """
    raw = _manifest.get("settings") if isinstance(_manifest.get("settings"), dict) else {}
    operational = {k: v for k, v in raw.items() if k not in DESIGN_SETTING_KEYS}
    # design package id: portfolio_layout.design_system → settings.design_system → default
    pl = operational.get("portfolio_layout") if isinstance(operational.get("portfolio_layout"), dict) else {}
    ds_id = str(
        (pl or {}).get("design_system")
        or operational.get("design_system")
        or "default"
    )
    design = _load_design_settings(ds_id)
    merged = {**operational, **design}
    return merged


SETTINGS: dict[str, Any] = _build_settings()


def reload_settings() -> dict[str, Any]:
    """Reload manifest + design settings (tests / hot path). Mutates SETTINGS."""
    global _manifest
    _manifest = json.loads((_PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    SETTINGS.clear()
    SETTINGS.update(_build_settings())
    return SETTINGS


# GOAP-hidden tools: public REST / operator MCP only.
# Policy: operator writes are denylisted; only compose tools are GOAP-visible.
# Keep in sync with GOAP_CANDIDATE_DENYLIST in db_layer/embeddings/embeddings_routes.py.
_GOAP_HIDDEN_OPS = (
    "portfolio_plugin__generate_layout_for_query",
    "portfolio_plugin__discover_portfolio_context",
    "portfolio_plugin__rebuild_portfolio_index",
    "portfolio_plugin__ingest_portfolio_context",
    "portfolio_plugin__reconcile_portfolio_projects",
    "portfolio_plugin__bake_portfolio_for_job",
    "portfolio_plugin__patch_job_layout",
    "portfolio_plugin__list_bake_runs",
    "portfolio_plugin__list_ask_turns",
    # Ask mode: FlowSpec-dispatched only, never an ad-hoc GOAP plan step.
    "portfolio_plugin__route_portfolio_ask",
    "portfolio_plugin__build_ask_overlay",
    "portfolio_plugin__spawn_pooled_fish",
    "portfolio_plugin__start_context_discovery",
    "portfolio_plugin__get_context_discovery",
    "portfolio_plugin__await_context_discovery",
    "portfolio_plugin__ensure_ask_answer",
)

# Substrings that mark a goal as portfolio-owned (used with classify_specialist_goal).
# Bare "layout"/"discover" alone are intentionally absent — too broad for claim.
_PORTFOLIO_GOAL_KEYS = (
    "portfolio",
    "portfolio layout",
    "genui",
    "redesign",
    "bake",
    "short_id",
    "star story",
    "project grid",
    "catportfolio",
    "resume site",
    "discover portfolio",
    "reindex portfolio",
    "reindex",
)


def portfolio_domain_claims(goal: str) -> bool:
    """Return True when the portfolio specialist should own this goal.

    Load-bearing decline rule: ``discover`` and ``scoped_ask`` require an
    explicit portfolio signal; bare layout/discover asks fall through to the
    generic MCP specialist. ``bake_for_job`` / ``redesign`` are portfolio-owned
    verbs even without a keyword (classifier already recognized them).
    """
    from plugins.portfolio_plugin.compose.recipes import classify_specialist_goal

    gclass = classify_specialist_goal(goal or "")
    q = (goal or "").lower()
    has_portfolio_signal = any(k in q for k in _PORTFOLIO_GOAL_KEYS)
    # bake/redesign classifiers are portfolio-owned verbs; discover
    # and scoped_ask require an explicit portfolio signal.
    if gclass in ("discover", "scoped_ask") and not has_portfolio_signal:
        return False
    if gclass not in ("bake_for_job", "redesign", "discover", "scoped_ask"):
        return False
    if not (gclass in ("bake_for_job", "redesign") or has_portfolio_signal):
        return False
    return True


class PortfolioPlugin(Plugin):
    """Portfolio layout generator plugin.
    """
    name = _manifest.get("name", "portfolio_plugin")
    version = _manifest.get("version", "1.0.0")
    tier = _manifest.get("tier", "free")
    auth_delegate = None
    module_paths = ["plugins.portfolio_plugin.MCPTools"]

    async def on_load(self, ctx: PluginContext) -> None:
        """Initialize the plugin and register HTTP routes + specialist domain."""
        await super().on_load(ctx)
        from plugins.portfolio_plugin.routes import register_routes
        register_routes()
        self._register_specialist_domain()

    async def on_ready(self, ctx: PluginContext) -> None:
        """Hide operator tools from GOAP and optionally kick off discovery."""
        await super().on_ready(ctx)
        try:
            from db_layer.route_store import set_route_enabled_by_operation

            for op in _GOAP_HIDDEN_OPS:
                try:
                    updated = await set_route_enabled_by_operation(PLUGIN_ID, op, False)
                    if updated:
                        logger.info(
                            "%s: disabled %s from route_embeddings (%d row(s))",
                            self.name, op, updated,
                        )
                except Exception as exc:
                    logger.debug("%s: hide %s: %s", self.name, op, exc)
        except Exception as exc:
            logger.warning(
                "%s: failed to hide GOAP-denylisted ops: %s",
                self.name, exc,
            )

        # Self-register the discovery worker: core must not import plugins.
        try:
            from core_graph.worker.worker_registry import get_worker_registry
            from plugins.portfolio_plugin.discovery.worker import register as register_discovery_worker

            reg = get_worker_registry()
            register_discovery_worker(reg)
            reg.start("portfolio_discovery_worker")
        except Exception as exc:
            logger.warning("%s: discovery worker registration failed open: %s", self.name, exc)

        # Self-register the ask-turns retention sweeper (mirrors the discovery
        # worker above — plugin-owned table, plugin-owned sweeper).
        try:
            from core_graph.worker.worker_registry import get_worker_registry
            from plugins.portfolio_plugin.ask.ttl_sweeper import register as register_ask_ttl_sweeper

            reg = get_worker_registry()
            register_ask_ttl_sweeper(reg)
            reg.start("portfolio_ask_turns_ttl_sweeper")
        except Exception as exc:
            logger.warning("%s: ask-turns TTL sweeper registration failed open: %s", self.name, exc)

        # Non-blocking discovery when enabled and index empty/stale.
        try:
            disc = SETTINGS.get("discovery") if isinstance(SETTINGS, dict) else {}
            if isinstance(disc, dict) and disc.get("enabled", True):
                import asyncio

                async def _run_boot_discovery() -> None:
                    from plugins.portfolio_plugin.discovery.pipeline import (
                        index_is_empty_or_stale,
                        run_discovery,
                    )
                    from plugins.portfolio_plugin.tenant import PORTFOLIO_TENANT_ID

                    freshness = float(disc.get("freshness_s") or 21600)
                    stale = await index_is_empty_or_stale(
                        tenant_id=PORTFOLIO_TENANT_ID, freshness_s=freshness
                    )
                    if not stale:
                        logger.debug("%s: discovery index fresh; skip boot run", self.name)
                        return
                    # Explicit tenant for worker-like path
                    from core.context import current_tenant_id

                    token = current_tenant_id.set(PORTFOLIO_TENANT_ID)
                    try:
                        result = await run_discovery(
                            scope="all",
                            dry_run=False,
                            write_back=bool(disc.get("write_back", False)),
                            do_index=True,
                            tenant_id=PORTFOLIO_TENANT_ID,
                        )
                        logger.info(
                            "%s: boot discovery done docs=%s write_back=%s",
                            self.name,
                            result.get("doc_count"),
                            result.get("write_back"),
                        )
                    finally:
                        current_tenant_id.reset(token)

                async def _boot_discovery() -> None:
                    budget_s = float(disc.get("budget_s") or 30)
                    try:
                        await asyncio.wait_for(_run_boot_discovery(), timeout=budget_s)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "%s: boot discovery exceeded budget_s=%s, abandoned", self.name, budget_s
                        )
                    except Exception as exc:
                        logger.warning("%s: boot discovery failed open: %s", self.name, exc)

                from utils.tasks import spawn_supervised
                spawn_supervised(_boot_discovery(), name="portfolio_boot_discovery")
        except Exception as exc:
            logger.warning("%s: boot discovery schedule failed: %s", self.name, exc)

    async def on_unload(self, ctx: PluginContext) -> None:
        """Unload the plugin and unregister HTTP routes + specialist domain."""
        from core.context import http_route_registry
        http_route_registry.unmount_owner("portfolio_plugin")
        try:
            from core_graph.worker.worker_registry import get_worker_registry

            await get_worker_registry().stop("portfolio_discovery_worker")
        except Exception as exc:
            logger.debug("%s: discovery worker stop: %s", self.name, exc)
        try:
            from core_graph.worker.worker_registry import get_worker_registry

            await get_worker_registry().stop("portfolio_ask_turns_ttl_sweeper")
        except Exception as exc:
            logger.debug("%s: ask-turns TTL sweeper stop: %s", self.name, exc)
        try:
            from core_graph.subgraphs.specialist.registry import unregister_specialist_domain

            unregister_specialist_domain("portfolio")
        except Exception as exc:
            logger.debug("%s: specialist domain unregister: %s", self.name, exc)
        try:
            from core_graph.subgraphs.registry import unregister_subgraph

            unregister_subgraph("portfolio_specialist")
        except Exception as exc:
            logger.debug("%s: portfolio_specialist subgraph unregister: %s", self.name, exc)
        await super().on_unload(ctx)

    def _register_specialist_domain(self) -> None:
        """Hook portfolio multi-phase pipeline into specialist + SubgraphRegistry."""
        try:
            from core_graph.subgraphs.registry import SubgraphSpec, register_subgraph
            from core_graph.subgraphs.specialist.registry import register_specialist_domain
            from plugins.portfolio_plugin.pipeline import run_portfolio_pipeline

            sa = SETTINGS.get("specialist_agent") if isinstance(SETTINGS, dict) else {}
            if isinstance(sa, dict) and sa.get("enabled") is False:
                logger.info("%s: specialist_agent disabled in settings", self.name)
                return

            async def _portfolio_domain(
                goal: str,
                *,
                tenant_id: int,
                session_id: str | None = None,
                **kwargs,
            ):
                # Claim portfolio GenUI goals; decline pure generic so the
                # generic MCP specialist can run (see portfolio_domain_claims).
                if not portfolio_domain_claims(goal or ""):
                    return None
                return await run_portfolio_pipeline(
                    goal,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    theme=str(kwargs.get("theme") or ""),
                    force_discover=bool(kwargs.get("force_discover") or False),
                    job_signals=kwargs.get("job_signals"),
                )

            register_specialist_domain("portfolio", _portfolio_domain)
            register_subgraph(
                SubgraphSpec(
                    id="portfolio_specialist",
                    name="Portfolio Specialist Domain Agent",
                    description=(
                        "Portfolio discover/compose/validate/bake pipeline "
                        "registered as the specialist domain handler."
                    ),
                    handler=_portfolio_domain,
                    metadata={
                        "plugin_id": PLUGIN_ID,
                        "domain": "portfolio",
                        "kind": "domain_agent",
                    },
                )
            )
            logger.info(
                "%s: registered specialist domain 'portfolio' + subgraph portfolio_specialist",
                self.name,
            )
        except Exception as exc:
            logger.warning("%s: specialist domain register failed: %s", self.name, exc)
