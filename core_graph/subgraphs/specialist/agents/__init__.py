"""Generic specialist agents (MCP tool-calling). Portfolio agents live in portfolio_plugin."""

from core_graph.subgraphs.specialist.agents.specialist_agent import (
    discover_tools_from_catalog,
    parse_tool_globs,
    run_specialist_agent,
)

__all__ = [
    "discover_tools_from_catalog",
    "parse_tool_globs",
    "run_specialist_agent",
]
