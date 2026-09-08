"""Ask mode — surgical block patching for visitor questions.

A visitor question resolves to 1-3 changed blocks instead of a full page
rebuild. The router is deterministic (no LLM); an LLM only authors prose.
See CLAUDE.md §6 and ``flow_specs/portfolio_ask_v1.json``.
"""

from plugins.portfolio_plugin.ask.contract import assess_patch_quality
from plugins.portfolio_plugin.ask.router import AskPlan, route_ask
from plugins.portfolio_plugin.ask.targets import SACRED_BLOCK_TYPES, resolve_targets

__all__ = [
    "AskPlan",
    "SACRED_BLOCK_TYPES",
    "assess_patch_quality",
    "resolve_targets",
    "route_ask",
]
