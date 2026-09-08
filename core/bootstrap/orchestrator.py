"""
Server Boot Pipeline Orchestrator for Whiskers Agent.
"""
import logging
from core.bootstrap.types import BootContext
from core.bootstrap.registry import BootPhaseRegistry
from core.bootstrap.phases import PHASES

logger = logging.getLogger("whiskers")

# Create the default global registry and populate it with the default phases
boot_registry = BootPhaseRegistry()
boot_registry.set_phases(PHASES)

async def run_pipeline(ctx: BootContext) -> None:
    """Run all startup phases sequentially using the global boot registry."""
    await boot_registry.run_pipeline(ctx)

async def run_teardown(ctx: BootContext) -> None:
    """Teardown the boot context in reverse order of startup."""
    logger.info("Initiating server teardown...")
    if ctx.db_available:
        try:
            from core.clustering.registry_sync import stop_registry_sync
            await stop_registry_sync()
            logger.info("Stopped registry sync listener")
        except Exception as exc:
            logger.warning("Registry sync teardown failed: %s", exc)

        try:
            from core_graph.worker import get_worker_registry
            await get_worker_registry().stop_all()
        except Exception as exc:
            logger.warning("Worker teardown failed: %s", exc)

        try:
            from core_graph.mcp_tool import shutdown_checkpointer
            await shutdown_checkpointer()
            logger.info("Closed Postgres checkpointer pool")
        except Exception as exc:
            logger.warning("Checkpointer shutdown failed: %s", exc)

        try:
            from core.telemetry.collector import collector
            await collector.stop()
            logger.info("TelemetryCollector stopped successfully")
        except Exception as exc:
            logger.warning("TelemetryCollector stop failed: %s", exc)

        try:
            from db_layer.connection import dispose_async_engine
            await dispose_async_engine()
            logger.info("Disposed async database engine")
        except Exception as exc:
            logger.warning("Database engine disposal failed: %s", exc)

    try:
        await ctx.registry.lifecycle.teardown_plugins()
        logger.info("Plugins torn down successfully")
    except Exception as exc:
        logger.warning("Plugin teardown failed: %s", exc)

    if getattr(ctx.registry, '_relay', None) is not None:
        try:
            await ctx.registry._relay.aclose()
        except Exception as exc:
            logger.warning("ExternalOAuthRelay close failed: %s", exc)

    if getattr(ctx.registry, 'auth', None) is not None:
        try:
            await ctx.registry.auth.aclose()
        except Exception as exc:
            logger.warning("PluginAuthRegistry close failed: %s", exc)

    try:
        if getattr(ctx.registry, 'events', None) is not None:
            await ctx.registry.events.drain()
            logger.info("Event bus drained successfully")
    except Exception as exc:
        logger.warning("Event bus drain failed: %s", exc)

    logger.info("Server shutdown complete.")
