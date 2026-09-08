"""Scan Express ``app|router.(get|post|…)`` and ``router.route().METHOD`` calls."""

from __future__ import annotations

import re
from pathlib import Path

from Tools.openapi_pipeline.adapters._common import (
    is_http_method,
    iter_source_files,
    operation_id_from,
    relative_to_root,
)
from Tools.openapi_pipeline.config import IngestConfig
from Tools.openapi_pipeline.models import route_to_dict
from Tools.openapi_pipeline.paths import join_prefix

_DIRECT_RE = re.compile(
    r"""(?:app|router|r)\.(get|post|put|patch|delete|head|options)\(\s*['"`]([^'"`]+)['"`]""",
    re.IGNORECASE,
)
_ROUTE_CHAIN_RE = re.compile(
    r"""(?:app|router|r)\.route\(\s*['"`]([^'"`]+)['"`]\s*\)((?:\s*\.\s*(?:get|post|put|patch|delete|head|options)\s*\([^)]*\))+)""",
    re.IGNORECASE,
)
_CHAIN_METHOD_RE = re.compile(r"\.\s*(get|post|put|patch|delete|head|options)\s*\(", re.IGNORECASE)


def parse_express_source(
    root: Path,
    repo_root: Path,
    config: IngestConfig,
) -> list[dict]:
    """Collect Express routes under ``root``."""
    globs = config.source_globs or ["*.js", "*.ts", "*.mjs", "*.cjs"]
    prefix = config.base_path
    routes: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for src in iter_source_files(root, globs):
        text = src.read_text(encoding="utf-8", errors="replace")
        rel = relative_to_root(src, repo_root)
        for match in _DIRECT_RE.finditer(text):
            method, path = match.group(1).upper(), match.group(2)
            _append(routes, seen, method, path, rel, prefix)
        for match in _ROUTE_CHAIN_RE.finditer(text):
            path = match.group(1)
            for method_match in _CHAIN_METHOD_RE.finditer(match.group(2)):
                _append(routes, seen, method_match.group(1).upper(), path, rel, prefix)
    return routes


def _append(
    routes: list[dict],
    seen: set[tuple[str, str]],
    method: str,
    path: str,
    rel: str,
    prefix: str,
) -> None:
    if not is_http_method(method):
        return
    full = join_prefix(prefix, path)
    key = (method, full)
    if key in seen:
        return
    seen.add(key)
    routes.append(route_to_dict({
        "method": method,
        "path": full,
        "operationId": operation_id_from(method, full),
        "adapter": "express",
        "handlerFile": rel,
        "controllerFile": rel,
        "description": "",
    }))
