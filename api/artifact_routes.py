"""Back-compat shim — artifact REST routes live in ``core.artifact_store.routes``."""

# Side-effect: register session-gated HTTP routes when this module is imported.
from core.artifact_store.routes import (  # noqa: F401
    api_artifacts_delete,
    api_artifacts_download,
    api_artifacts_get,
    api_artifacts_list,
)
