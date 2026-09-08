"""Generic in-process LLM tool-calling loop.

Reusable by any specialist subgraph — nothing portfolio-specific lives here.
See ``spec.py`` (AgentSpec/ToolRef), ``toolset.py`` (catalog -> tool schemas),
``runner.py`` (the actual bind_tools loop + dispatch via ``execute_operation``).
"""

from __future__ import annotations

from core_graph.agent_loop.runner import AgentRunResult, run_agent
from core_graph.agent_loop.spec import AgentSpec, ToolRef

__all__ = ["AgentSpec", "ToolRef", "AgentRunResult", "run_agent"]
