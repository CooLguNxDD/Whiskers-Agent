"""Back-compat shim — artifact offload store lives in ``core.artifact_store.store``."""

from core.artifact_store.store import (  # noqa: F401
    ARTIFACT_BUCKET,
    CONSOLE_PATH_TMPL,
    collect_string_leaves,
    extract_large_artifacts,
    is_valid_short_id,
    mint_download_url,
    offload_text,
    read_artifact_bytes,
    resolve_artifact_meta,
)

__all__ = [
    "ARTIFACT_BUCKET",
    "CONSOLE_PATH_TMPL",
    "collect_string_leaves",
    "extract_large_artifacts",
    "is_valid_short_id",
    "mint_download_url",
    "offload_text",
    "read_artifact_bytes",
    "resolve_artifact_meta",
]
