"""Assert test/ never imports or mock-patches real plugin code.

Plugin-owned code (tools, stores, models) is tested from
plugins/<plugin>/tests/, not from test/ — see the plugin test/data-access
relocation notes in CLAUDE.md. This guards against drift back the other way:
a plugin-specific test landing in test/unit or test/integration again, or a
core test reaching into plugin internals via ``from plugins.X import`` /
``patch("plugins.X....")`` instead of exercising the generic mechanism with
a synthetic double.

Loader/registry tests intentionally use synthetic plugin ids (``plugins.demo``,
``plugins.alpha``, ``plugins.fake_plugin``, ...) that do not correspond to a
real installed package under plugins/ — those are fine and not flagged here,
since this test only matches against REAL_PLUGIN_PACKAGES.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

SCAN_DIRS = ["test"]

# Real installed plugin packages (dirs directly under plugins/, excluding
# __pycache__). Kept as a literal list rather than a live filesystem scan so
# a newly added plugin doesn't silently start being scanned for until this
# file is updated deliberately alongside it.
REAL_PLUGIN_PACKAGES = [
    "cat_terminal_relay_plugin",
    "job_search_plugin",
    "jules_plugin",
    "memory_plugin",
    "portfolio_plugin",
    "search_plugin",
    "test_plugin",
    "world_semantic_plugin",
]

_PKG_ALT = "|".join(re.escape(p) for p in REAL_PLUGIN_PACKAGES)

# `import plugins.portfolio_plugin...` / `from plugins.portfolio_plugin... import`
_IMPORT_RE = re.compile(rf"^\s*(?:from|import)\s+plugins\.(?:{_PKG_ALT})\b", re.MULTILINE)
# `patch("plugins.portfolio_plugin....")` / `patch('plugins.portfolio_plugin....')`
_PATCH_RE = re.compile(rf"""["']plugins\.(?:{_PKG_ALT})\.""")


_SELF = Path(__file__).resolve().relative_to(REPO).as_posix()


def test_no_real_plugin_imports_or_patches_under_test_dir():
    """test/ must not import or mock.patch real plugin package internals."""
    offenders: list[str] = []
    for dirname in SCAN_DIRS:
        root = REPO / dirname
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if rel == _SELF:
                continue  # this file's own docstring/example patterns, not a real offender
            text = path.read_text(encoding="utf-8", errors="replace")
            if _IMPORT_RE.search(text) or _PATCH_RE.search(text):
                offenders.append(rel)
    assert offenders == [], (
        "test/ files importing or patching real plugin code (move these "
        f"tests under plugins/<plugin>/tests/ instead): {offenders}"
    )
