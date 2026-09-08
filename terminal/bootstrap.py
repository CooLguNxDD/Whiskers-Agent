"""Shared path bootstrap for terminal TUIs."""

from __future__ import annotations

import sys
import asyncio
from pathlib import Path

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except AttributeError:
        pass



def repo_root() -> Path:
    """Return the repository root directory."""
    return Path(__file__).resolve().parent.parent


def script_dir() -> Path:
    """Return terminal/script (setup CLI + entry scripts)."""
    return repo_root() / "terminal" / "script"


def ensure_import_paths() -> tuple[Path, Path]:
    """Put repo root and terminal/script on sys.path; return (root, script_dir)."""
    root = repo_root()
    scripts = script_dir()
    for path in (root, scripts):
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return root, scripts