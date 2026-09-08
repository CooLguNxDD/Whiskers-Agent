"""Shared helpers for source-code adapters."""

from __future__ import annotations

import re
from pathlib import Path

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


def is_http_method(value: str) -> bool:
    """True when value is a standard HTTP verb."""
    return value.upper() in _HTTP_METHODS


def iter_source_files(root: Path, globs: list[str]) -> list[Path]:
    """Collect source files matching any of the given globs under root."""
    files: set[Path] = set()
    for pattern in globs:
        files.update(p for p in root.rglob(pattern.lstrip("/")) if p.is_file())
    return sorted(files)


def relative_to_root(path: Path, root: Path) -> str:
    """Repo-relative POSIX path, falling back to the given path."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def operation_id_from(method: str, path: str, handler: str = "") -> str:
    """Build a stable camelCase-ish operationId from method + path or handler."""
    if handler:
        base = re.sub(r"(Controller|Handler|View)$", "", handler)
        if base:
            return method.lower() + base[0].upper() + base[1:]
    slug = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")
    parts = [p for p in slug.split("_") if p]
    camel = "".join(p[:1].upper() + p[1:] for p in parts)
    return f"{method.lower()}{camel}" if camel else f"{method.lower()}Root"
