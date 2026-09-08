"""GoapAgent — MCP node surface + CLI agent backend for the dynamic graph.

MCP tools (tag ``GoapAgent``) expose graph nodes for external headless agents.
CLI drivers power both Mode B (orchestrator subprocess) and Mode A
(LangChain chat model backed by ``claude`` / ``agy``).

Mode B auto-injects ``instructions/CLI_AGENT.md`` plus a temporary
``--mcp-config`` (see ``cli_inject.prepare_cli_injection``).

Never registers HTTP routes used by native playground/GOAP REST.
"""

from core_graph.goap_agent.session_store import SessionStore, get_session_store

__all__ = ["SessionStore", "get_session_store"]
