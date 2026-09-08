"""Scan Flask ``@app.route`` / ``@bp.route`` decorators."""

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

_ROUTE_RE = re.compile(
    r"""@(?:app|bp|blueprint|api)\.route\(\s*['"]([^'"]+)['"]([^)]*)\)""",
    re.IGNORECASE,
)
_METHODS_RE = re.compile(r"""methods\s*=\s*\[([^\]]+)\]""", re.IGNORECASE)
_METHOD_LIT_RE = re.compile(r"""['"](\w+)['"]""")
_FLASK_PARAM_RE = re.compile(r"<(?:(?:int|string|float|path|uuid):)?(\w+)>")


def flask_path_to_openapi(path: str) -> str:
    """Convert Flask ``<int:id>`` converters to OpenAPI ``{id}``."""
    return _FLASK_PARAM_RE.sub(r"{\1}", path)

_ROUTE_RE = re.compile(
    r"""@(?:app|bp|blueprint|api)\.route\(\s*['"]([^'"]+)['"]([^)]*)\)""",
    re.IGNORECASE,
)
_METHODS_RE = re.compile(r"""methods\s*=\s*\[([^\]]+)\]""", re.IGNORECASE)
_METHOD_LIT_RE = re.compile(r"""['"](\w+)['"]""")


def parse_flask_source(
    root: Path,
    repo_root: Path,
    config: IngestConfig,
) -> list[dict]:
    """Collect Flask routes under ``root``."""
    globs = config.source_globs or ["*.py"]
    prefix = config.base_path
    routes: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for src in iter_source_files(root, globs):
        text = src.read_text(encoding="utf-8", errors="replace")
        rel = relative_to_root(src, repo_root)
        for match in _ROUTE_RE.finditer(text):
            path = match.group(1)
            extras = match.group(2) or ""
            methods_match = _METHODS_RE.search(extras)
            if methods_match:
                methods = [m.group(1).upper() for m in _METHOD_LIT_RE.finditer(methods_match.group(1))]
            else:
                methods = ["GET"]
            for method in methods:
                _append(routes, seen, method, path, rel, prefix)
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
    full = join_prefix(prefix, flask_path_to_openapi(path))
    key = (method, full)
    if key in seen:
        return
    seen.add(key)
    routes.append(route_to_dict({
        "method": method,
        "path": full,
        "operationId": operation_id_from(method, full),
        "adapter": "flask",
        "handlerFile": rel,
        "controllerFile": rel,
        "description": "",
    }))
