"""Shared filesystem helpers for Whiskers Agent tool generator CLIs."""

from __future__ import annotations

import sys
from pathlib import Path


class WriteError(Exception):
    """Raised when a generator cannot persist output to disk."""


def write_text_safe(
    path: str | Path,
    content: str,
    *,
    encoding: str = "utf-8",
    create_parents: bool = True,
) -> Path:
    """Write UTF-8 text, optionally creating parent dirs; surface OSError clearly."""
    target = Path(path)
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(content, encoding=encoding)
    except OSError as exc:
        raise WriteError(f"cannot write {target}: {exc}") from exc
    return target


def emit_generated(code: str, output: str | Path | None) -> None:
    """Write generated code to ``output`` or print to stdout; exit 1 on I/O failure."""
    if output:
        try:
            path = write_text_safe(output, code)
        except WriteError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"Generated → {path}")
    else:
        sys.stdout.write(code)