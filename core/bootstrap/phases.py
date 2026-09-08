"""Boot phase implementations."""
import logging
from typing import List
from core.bootstrap.types import BootContext, Phase

logger = logging.getLogger("whiskers")

async def phase_0_resolve_plugins(ctx: BootContext):
    """Phase 0: Resolve plugin dependency DAG (pure planning)."""
    from core.proxy.proxy_manager import proxy_manager

    proxy_manager.subscribe(ctx.registry.events)

    import json
    from core.plugin_loader.plugin_loader import parse_tier
    from core.plugin_loader import resolver
    
    from utils.config_registry import PLUGIN_CONFIG_PATH
    config_path = PLUGIN_CONFIG_PATH
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    packages = config.get("plugins", [])
    system_tier = parse_tier(config.get("tier", "lite"))
    
    plan = resolver.resolve(packages, system_tier)
    ctx.plan = plan
    logger.info("Phase 0 (Resolve): Resolved plugin specs: %s", [spec.name for spec in plan.order])


async def phase_1_load_plugins(ctx: BootContext):
    """Phase 1: Load plugins using the resolved plan, then run lifecycle hooks.

    ``discover_and_load_plugins_async`` only imports packages and calls each
    ``register()`` (which appends the Plugin instance to the lifecycle registry).
    The ``on_load``/``on_ready`` hooks — where plugins enable their tool tags and
    contribute routes to the RouteRegistry — run via ``initialize_plugins()``.
    Both must complete here so Phase 3 (route indexing) sees a populated registry.
    """
    from core.plugin_loader.plugin_loader import discover_and_load_plugins_async
    await discover_and_load_plugins_async(ctx.registry)
    await ctx.registry.lifecycle.initialize_plugins()
    logger.info("Phase 1 (Load): All plugins loaded, registered, and initialized.")


async def phase_mount_http_routes(ctx: BootContext):
    """Drain late-bound route declarations into the live router after plugins load."""
    try:
        from core.context import http_route_registry
    except Exception as exc:
        logger.warning("phase_mount_http_routes: failed to import http_route_registry: %s", exc)
        return
    if not http_route_registry.is_http_active:
        logger.info("HTTP route mount: skipped (stdio transport / no app bound).")
        return
    n = http_route_registry.drain_pending()
    logger.info("HTTP route mount: drained %d late-bound route(s) into live router.", n)


async def phase_2_mount_proxies(ctx: BootContext):
    """Phase 2: Load + mount all proxies."""
    # Register the tools_discovered event listener on the registry events bus
    def handle_proxy_tools_discovered(payload: dict):
        """Handle newly discovered proxy tools by loading and registering them."""
        name = payload.get("name")
        tools = payload.get("tools")
        custom_description = payload.get("custom_description")
        workspace_label = payload.get("workspace_label")
        if not name or not tools:
            return
            
        from core.proxy.proxy_manager import proxy_manager
        provider = proxy_manager._active_handles.get(name)
        if not provider:
            logger.warning("No active provider found for proxy %s", name)
            return
            
        from core.proxy_tools.proxy_tool_loader import collect_from_proxy
        from core.context import route_registry
        
        descriptors = collect_from_proxy(
            name, tools, provider, custom_description=custom_description, workspace_label=workspace_label
        )
        if descriptors:
            ctx.registry.events.emit("routes.contribute", f"proxy_{name}", descriptors)
            logger.info("Contributed %d routes from proxy %s to route registry", len(descriptors), name)

    ctx.registry.events.on("proxy.tools_discovered", handle_proxy_tools_discovered)

    if ctx.db_available:
        await ctx.registry.events.emit_async("system.boot_proxies")
        logger.info("Phase 2 (Proxies): Persisted proxies loaded and mounted.")
    else:
        logger.info("Phase 2 (Proxies): Skipped (DATABASE_URL not set).")


async def phase_3_index_routes(ctx: BootContext):
    """Phase 3: Index routes (plugin and proxy tools) to pgvector in the background."""
    if ctx.db_available:
        from core.context import route_registry
        from core_graph.worker import enqueue_pending, register_all, get_worker_registry
        await enqueue_pending(route_registry)
        register_all(get_worker_registry())
        get_worker_registry().start("embedding_worker")
        logger.info("Phase 3 (Index): Route embedding worker started (%d routes contributed).", len(route_registry))
    else:
        logger.info("Phase 3 (Index): Skipped (DATABASE_URL not set).")


async def phase_4_verify_graph(ctx: BootContext):
    """Phase 4: Confirm graph compiles on-demand and vector client connection is healthy."""
    logger.info("Phase 4 (Graph Verification): run_graph compiles on-demand.")
    if ctx.db_available:
        try:
            from db_layer.embeddings.embeddings_core import _get_embeddings_async
            client = await _get_embeddings_async()
            if client:
                logger.info("Phase 4 (Graph Verification): Vector embeddings database connection verified.")
        except Exception as exc:
            logger.warning("Phase 4 (Graph Verification): Could not probe embeddings client: %s", exc)




async def phase_5_finalize(ctx: BootContext):
    """Phase 5: Tool visibility, scheduler, and startup log banner."""
    if ctx.db_available:
        try:
            from core.context import tool_visibility
            from db_layer.tool_config_store import get_disabled_tools
            hidden = tool_visibility.apply_persisted(await get_disabled_tools())
            logger.info("Phase 5 (Finalize): tool_visibility hid %d disabled tool(s) at startup", hidden)
        except Exception as exc:
            logger.warning("Phase 5 (Finalize): Failed to apply tool visibility settings: %s", exc)

        # Gateway (run_graph unified) mode: hide every registered MCP tool except
        # GATEWAY_ALWAYS_VISIBLE so run_graph (+ discover/auth) is the only
        # client-visible entry. Hidden tools stay reachable via the route registry.
        try:
            from db_layer.gateway_settings_store import (
                get_gateway_unified,
                collect_gateway_unified_hide_names,
            )
            if await get_gateway_unified():
                from core.context import tool_visibility
                names = await collect_gateway_unified_hide_names()
                count = tool_visibility.apply_hidden(names)
                logger.info(
                    "Phase 5 (Finalize): gateway unified ON — hid %d tool(s); run_graph is the unified entry",
                    count,
                )
        except Exception as exc:
            logger.warning("Phase 5 (Finalize): Failed to apply gateway hidden tools: %s", exc)

        # Inject live tools-summary into MCP server instructions (for gateway clients)
        try:
            from db_layer.gateway_settings_store import build_tools_summary, refresh_tools_summary
            summary = await build_tools_summary(limit=40)
            if summary:
                from core.context import mcp_context_builder, mcp as _mcp_for_instr
                mcp_context_builder.add_context(summary)
                mcp_context_builder.update_mcp_instructions(_mcp_for_instr)
                logger.info("Phase 5 (Finalize): injected tools-summary into MCP instructions")
            # ensure refresh helper is warm (no-op call)
            await refresh_tools_summary()
        except Exception as exc:
            logger.warning("Phase 5 (Finalize): Failed to inject tools summary: %s", exc)

        try:
            from core_graph.worker import get_worker_registry
            started = get_worker_registry().start("checkpoint_sweeper")
            if started:
                logger.info("Phase 5 (Finalize): Ephemeral checkpoint sweeper started")
        except Exception as exc:
            logger.warning("Phase 5 (Finalize): Failed to start checkpoint sweeper: %s", exc)

        try:
            from core_graph.worker import get_worker_registry
            get_worker_registry().start("telemetry_ttl_sweeper")
            logger.info("Phase 5 (Finalize): Telemetry TTL sweeper started")
        except Exception as exc:
            logger.warning("Phase 5 (Finalize): Failed to start telemetry TTL sweeper: %s", exc)

        try:
            from core_graph.worker import get_worker_registry
            get_worker_registry().start("artifact_sweeper")
            logger.info("Phase 5 (Finalize): Artifact sweeper started")
        except Exception as exc:
            logger.warning("Phase 5 (Finalize): Failed to start artifact sweeper: %s", exc)
            
    # Run the startup logging banner. The coroutine is injected via ctx by the
    # entrypoint — never import whiskers_agent_mcp here, as that re-executes the
    # entry module and resets the registry singleton (see BootContext.log_startup).
    try:
        if ctx.log_startup is not None:
            await ctx.log_startup()
    except Exception as exc:
        logger.warning("Phase 5 (Finalize): Could not print startup banner: %s", exc)


async def phase_6_registry_sync(ctx: BootContext):
    """Phase 6: Start cross-process tool-visibility LISTEN/NOTIFY sync + load DB model-role/gate overrides."""
    if ctx.db_available:
        try:
            from core.clustering.registry_sync import start_registry_sync
            task = await start_registry_sync()
            if task is not None:
                logger.info("Phase 6 (Registry Sync): cross-process tool-visibility sync active.")
        except Exception as exc:
            logger.warning("Phase 6 (Registry Sync): failed to start: %s", exc)

        try:
            from core_graph.model_roles.db_overlay import apply_db_overrides
            applied = await apply_db_overrides()
            logger.info("Phase 6 (Registry Sync): %d model-role DB override(s) applied.", applied)
        except Exception as exc:
            logger.warning("Phase 6 (Registry Sync): model-role DB overlay load skipped: %s", exc)

        try:
            from core.scope_management.gate_overlay import apply_db_overrides as apply_gate_db_overrides
            applied_gates = await apply_gate_db_overrides()
            logger.info("Phase 6 (Registry Sync): %d plugin-gate DB override(s) applied.", applied_gates)
        except Exception as exc:
            logger.warning("Phase 6 (Registry Sync): plugin-gate DB overlay load skipped: %s", exc)


async def phase_keypair_guard(ctx: BootContext):
    """Phase 1c: Fatal guard that the active auth keypair decrypts under current MASTER_KEY.

    Runs after plugin load (so context is ready) but early. Skips if no DB.
    ensure_keypair is called to obtain/reuse the active kid (never overwrites existing).
    _load_private_key forces a pgp_sym_decrypt which will fail loudly for MASTER_KEY drift.
    """
    if not ctx.db_available:
        logger.info("Phase Keypair Guard: skipped (DATABASE_URL not available)")
        return
    try:
        from core.context import oauth_provider
        svc = getattr(oauth_provider, "_svc", None) if oauth_provider is not None else None
        if svc is None:
            logger.info("Phase Keypair Guard: skipped (OAuthService not enabled)")
            return
        kid = await svc.ensure_keypair()
        await svc._load_private_key(kid)
        logger.info("Phase Keypair Guard: active keypair kid=%s decrypts successfully", kid)
    except Exception as exc:
        kid_ref = kid if "kid" in locals() else "unknown"
        logger.critical(
            "MASTER_KEY does not match active keypair (kid=%s); refusing to start: %s",
            kid_ref,
            exc,
        )
        raise


async def phase_ensure_default_admin_user(ctx: BootContext) -> None:
    """Phase 1d: Ensure the default admin user is seeded in the database."""
    if not ctx.db_available:
        logger.info("Phase Ensure Default Admin User: skipped (DATABASE_URL not available)")
        return
    try:
        from core.user_management import ensure_default_admin_user
        await ensure_default_admin_user()
        logger.info("Phase Ensure Default Admin User: check/seed completed successfully")
    except Exception as exc:
        logger.error("Phase Ensure Default Admin User: failed: %s", exc, exc_info=True)


async def phase_scope_registry_preseed(ctx: BootContext) -> None:
    """Pre-seed the PermissionRegistry from persisted meta.scopes (non-fatal).

    Covers boot-excluded/lazy-loaded plugins so ``get_valid_scopes()`` and the
    ``/api/auth/scope-vocabulary`` endpoint are complete immediately after
    restart, before such plugins actually load. Plugins that do load normally
    overwrite these entries with a freshly computed registration (and a
    matching fingerprint) during Phase 1.
    """
    if not ctx.db_available:
        return
    try:
        from db_layer.plugin_registry_store import DBPluginRegistry
        from core.scope_management.registration import get_permission_registry

        db_registry = DBPluginRegistry()
        reg = get_permission_registry()
        records = await db_registry.get_active()
        seeded = 0
        for record in records:
            if reg.get_plugin_permissions(record.id):
                continue  # already registered (loaded eagerly this boot)
            entries = (record.meta or {}).get("scopes") or []
            if entries:
                reg.register_plugin_permissions(record.id, entries, replace=True)
                seeded += 1
        if seeded:
            logger.info("Phase scope-registry-preseed: seeded %d plugin(s) from persisted meta.scopes", seeded)
    except Exception as exc:
        logger.warning("Phase scope-registry-preseed: skipped (%s)", exc)


async def phase_scope_health(ctx: BootContext) -> None:
    """Post-proxy scope health self-test (non-fatal)."""
    try:
        from core.scope_management.health import run_boot_scope_health
        warnings = run_boot_scope_health()
        if warnings:
            logger.warning("Phase scope-health: %d warning(s)", len(warnings))
        else:
            logger.info("Phase scope-health: OK")
    except Exception as exc:
        logger.error("Phase scope-health: failed: %s", exc, exc_info=True)


async def phase_plugin_reconcile(ctx: BootContext) -> None:
    """Mark ghost plugins rows stale; clear stale for present disk/proxy rows.

    Compares ``plugins`` table against configured package specs and
    ``proxy_servers`` names. Writes only on transitions so steady-state boot
    cost is approximately one SELECT. Preserves ``is_active``.
    """
    if not getattr(ctx, "db_available", False):
        return
    try:
        from db_layer.plugin_registry_store import DBPluginRegistry, PluginMetaKey
        from core.plugin_loader import resolver
        from utils.config_registry import PLUGIN_CONFIG_PATH
        import json

        db_registry = DBPluginRegistry()
        records = await db_registry.get_all()

        packages: list[str] = []
        try:
            with open(PLUGIN_CONFIG_PATH, "r", encoding="utf-8") as f:
                packages = list((json.load(f) or {}).get("plugins") or [])
        except Exception as cfg_exc:
            # Fail closed: skip reconciliation rather than mark every live plugin stale
            # when the config file is unreadable (empty packages would treat all as ghosts).
            logger.warning(
                "Phase 2b: failed to read plugin_config (%s) — skipping reconciliation",
                cfg_exc,
            )
            return

        specs, _skipped = resolver.build_specs(packages)
        spec_names = {
            resolver._normalize_plugin_name(s.name) for s in specs
        }

        proxy_names: set[str] = set()
        proxy_lookup_ok = True
        try:
            from core.proxy.proxy_manager import proxy_manager
            proxy_dicts = await proxy_manager.list_proxies()
            proxy_names = {p["name"] for p in proxy_dicts}
        except Exception as px_exc:
            proxy_lookup_ok = False
            logger.warning("Phase 2b: proxy_servers lookup failed (%s)", px_exc)

        stale_count = 0
        recovered = 0
        for record in records:
            rid = record.id or ""
            if rid.startswith("proxy_"):
                if not proxy_lookup_ok:
                    continue
                proxy_short = rid[len("proxy_"):]
                present = proxy_short in proxy_names
            else:
                present = resolver._normalize_plugin_name(rid) in spec_names

            already_stale = bool((record.meta or {}).get(PluginMetaKey.STALE.value))
            if not present:
                if not already_stale:
                    if await db_registry.mark_stale(rid):
                        stale_count += 1
            else:
                if already_stale:
                    if await db_registry.clear_stale(rid):
                        recovered += 1

        logger.info("Phase 2b: %d stale, %d recovered", stale_count, recovered)
    except Exception as exc:
        logger.warning("Phase 2b: Plugin Registry Reconciliation skipped (%s)", exc)


PHASES: List[Phase] = [
    Phase(name="Phase 0: Plugin Dependency Resolution", run=phase_0_resolve_plugins, fatal=True),
    Phase(name="Phase 1: Plugin Loading & Module Registration", run=phase_1_load_plugins, fatal=True),
    Phase(name="Phase 1b: Mount Late-Bound HTTP/WS Routes", run=phase_mount_http_routes, fatal=False),
    Phase(name="Phase 1c: Keypair / MASTER_KEY Decrypt Guard", run=phase_keypair_guard, fatal=True),
    Phase(name="Phase 1d: Seed Default Admin User", run=phase_ensure_default_admin_user, fatal=False),
    Phase(name="Phase 2: Upstream Proxy Mounting & Tool Routing", run=phase_2_mount_proxies, fatal=False),
    Phase(name="Phase 2b: Plugin Registry Reconciliation", run=phase_plugin_reconcile, fatal=False),
    Phase(name="scope-registry-preseed", run=phase_scope_registry_preseed, fatal=False),
    Phase(name="scope-health", run=phase_scope_health, fatal=False),
    Phase(name="Phase 3: Route Embedding Indexing", run=phase_3_index_routes, fatal=False),
    Phase(name="Phase 4: LangGraph Readiness Verification", run=phase_4_verify_graph, fatal=False),
    Phase(name="Phase 5: Apply Tool Exposure & Start Scheduler & Logging", run=phase_5_finalize, fatal=False),
    Phase(name="Phase 6: Cross-Process Registry Sync", run=phase_6_registry_sync, fatal=False),
]
