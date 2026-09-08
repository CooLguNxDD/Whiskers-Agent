"""Back-compat shim — MCP artifact tools live in ``core.artifact_store.tools``."""

# Side-effect registration + re-exports
from core.artifact_store.tools import (  # noqa: F401
    fetch_artifact,
    get_artifact,
    list_artifacts,
)

__all__ = ["list_artifacts", "get_artifact", "fetch_artifact"]
