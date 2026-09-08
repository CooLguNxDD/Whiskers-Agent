"""Portfolio discovery → semantic index → reconcile pipeline."""

from plugins.portfolio_plugin.discovery.pipeline import (
    CONTEXT_COLLECTION,
    run_discovery,
    run_rebuild_index,
)
from plugins.portfolio_plugin.discovery.normalize import ContextDoc
from plugins.portfolio_plugin.discovery.resolver import ResolvedSource, resolve_sources

__all__ = [
    "CONTEXT_COLLECTION",
    "ContextDoc",
    "ResolvedSource",
    "resolve_sources",
    "run_discovery",
    "run_rebuild_index",
]
