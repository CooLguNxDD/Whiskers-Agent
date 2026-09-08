"""
dynamic_route_loader — collect RouteDescriptors from JSON spec trees.

Walks a folder layout like::

    mcp-tools-context/
      <domain>/
        read|write_update|delete/
          mcp-tools.json    ← list of tool definitions

Each entry has the shape produced by pro_plugin's existing
``dynamic_tools_loader``::

    {
      "name": "ListSessions",
      "description": "GET /api/v1/... — Human readable summary",
      "inputSchema": {
        "type": "object",
        "properties": {...},
        "required": [...]
      },
      "_meta": {"method": "GET", "path": "/api/v1/...", "domain": "..."}
    }

The loader emits one RouteDescriptor per entry with ``is_fast_path=False`` —
execution flows through the dynamic builder/executor path, not a registered
callable.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

from core.route_registry.route_descriptor import RouteDescriptor

logger = logging.getLogger("whiskers")


def _iter_tool_files(root: Path) -> Iterable[Path]:
    """Recursively yield paths to mcp-tools.json manifests within the given directory."""
    if not root.exists():
        return []
    return root.rglob("mcp-tools.json")


def collect_from_json_tree(
    plugin_id: str,
    root_path: str | Path,
    *,
    valid_ops: set[str] | None = None,
) -> list[RouteDescriptor]:
    """Walk a JSON tree and emit RouteDescriptors for every operation found.

    Parameters
    ----------
    plugin_id
        Identifier the registry keys routes by.
    root_path
        Folder containing per-domain subfolders with ``mcp-tools.json`` files.
    valid_ops
        If set, only files inside subfolders named in this set are loaded
        (e.g. ``{"read"}`` to skip mutating operations). When ``None``, all
        files are loaded.
    """
    root = Path(root_path)
    descriptors: list[RouteDescriptor] = []
    seen: set[tuple[str, str]] = set()

    for tools_file in _iter_tool_files(root):
        op_dir = tools_file.parent.name
        if valid_ops is not None and op_dir not in valid_ops:
            continue
        try:
            entries = json.loads(tools_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(
                "dynamic_route_loader: failed to parse %s: %s", tools_file, exc,
            )
            continue
        if not isinstance(entries, list):
            continue

        for entry in entries:
            raw_op_id = str(entry.get("name") or "").strip()
            if not raw_op_id:
                continue
            prefix = f"{plugin_id}__"
            op_id = raw_op_id if raw_op_id.startswith(prefix) else f"{prefix}{raw_op_id}"
            key = (plugin_id, op_id)
            if key in seen:
                continue
            seen.add(key)

            meta = entry.get("_meta") or {}
            tags = tuple(filter(None, [
                plugin_id,
                op_dir,
                meta.get("domain", ""),
            ]))

            method = str(meta.get("method", "GET")).upper()
            access = "read" if method in ("GET", "HEAD", "OPTIONS") else "write"
            descriptors.append(RouteDescriptor(
                plugin_id=plugin_id,
                operation_id=op_id,
                description=str(entry.get("description") or op_id),
                parameters=entry.get("inputSchema") or {},
                method=method,
                path_template=str(meta.get("path", "")),
                is_fast_path=False,
                callable_ref=None,
                tags=tags,
                access=access,
            ))

    if not descriptors:
        logger.warning(
            "dynamic_route_loader: collected 0 routes for plugin '%s' "
            "(root=%s). Verify the JSON tree exists at this path.",
            plugin_id, root,
        )
    else:
        logger.info(
            "dynamic_route_loader: collected %d routes for plugin '%s' from %s",
            len(descriptors), plugin_id, root,
        )
    return descriptors
