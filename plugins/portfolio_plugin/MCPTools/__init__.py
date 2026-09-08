"""MCPTools package — registers portfolio_plugin tools."""

from . import portfolio_tools  # noqa: F401
from . import context_tools  # noqa: F401
from . import bake_tools  # noqa: F401
from . import bake_admin_tools  # noqa: F401 — list_bake_runs operator surface
from . import discovery_tools  # noqa: F401
from . import context_search_tools  # noqa: F401
from . import ingest_tools  # noqa: F401
from . import portfolio_agent_tools  # noqa: F401 — PortfolioAgent_run
from . import ask_tools  # noqa: F401 — ask-mode block patching
