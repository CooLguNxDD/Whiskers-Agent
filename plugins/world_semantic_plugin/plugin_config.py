"""Plugin-scoped configuration for world_semantic_plugin."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.plugin_loader.plugin_registry import Plugin, PluginContext

logger = logging.getLogger("whiskers.plugins")

_manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))

PLUGIN_ID = _manifest.get("name", Path(__file__).parent.name)
SETTINGS = _manifest.get("settings", {})


class WorldSemanticPlugin(Plugin):
    """Spatial world context + H3 indexing for agent scene understanding."""

    name = _manifest.get("name", "world_semantic_plugin")
    version = _manifest.get("version", "0.1.0")
    tier = _manifest.get("tier", "free")
    auth_delegate = None
    module_paths = ["plugins.world_semantic_plugin.MCPTools"]

    async def on_load(self, ctx: PluginContext) -> None:
        await super().on_load(ctx)
        from plugins.world_semantic_plugin.routes import register_routes

        register_routes()
        logger.info("%s HTTP routes registered", self.name)

        # Register + start durable index worker (WorkerRegistry).
        try:
            from core_graph.worker.worker_registry import get_worker_registry
            from plugins.world_semantic_plugin.worker import register as register_index_worker

            reg = get_worker_registry()
            # register() is idempotent, so a re-load cannot shadow the live spec.
            register_index_worker(reg)
            started = reg.start("world_index_worker")
            logger.info(
                "%s world_index_worker %s",
                self.name,
                "started" if started is not None or reg.is_running("world_index_worker") else "start_skipped",
            )
        except Exception:
            logger.exception("%s failed to start world_index_worker", self.name)

    async def on_unload(self, ctx: PluginContext) -> None:
        try:
            from core_graph.worker.worker_registry import get_worker_registry

            await get_worker_registry().stop("world_index_worker")
        except Exception:
            logger.exception("%s failed to stop world_index_worker", self.name)

        from core.context import http_route_registry

        http_route_registry.unmount_owner("world_semantic_plugin")
        await super().on_unload(ctx)
