"""Fail when pre-rebrand branding reappears in the tree.

The product was renamed from "OpenCat Tunnel" to "Whiskers Agent", and the
codebase descends from a healthcare-domain product before that. Both vocabularies
were swept out in one pass; without a guard they grow back one copy-pasted
docstring at a time.

Run directly (``python scripts/check_legacy_branding.py``) or via
``scripts/run_tests.py``, which invokes it before pytest. Exits non-zero and
prints every offending ``path:line`` on a hit.

Adding an allowlist entry is a deliberate act: the entries below are places where
the old name is the *correct* content — frozen migration history, the legacy
scope map those migrations cross-check, and the audit reports that catalogue the
rename. Anything else is a real hit.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Old product names + the healthcare vocabulary the codebase inherited.
BANNED = re.compile(
    r"weltel|opencat|open-cat|cat-tunnel|cattunnel|clinician|(?<![a-z])clinic(?![a-z])|(?<![a-z])patients?(?![a-z])",
    re.IGNORECASE,
)

# Paths where a legacy name is the correct content, not a leftover.
ALLOWLISTED_PATHS: tuple[str, ...] = (
    # Audit reports: they exist to catalogue the old names.
    "docs/cleanup-audit/",
    # Frozen migration history — an applied revision is never rewritten.
    "migrations/versions/core/core_041_retire_plugin_alembic_branches.py",
    "migrations/versions/core/core_047_scope_cutover.py",
    "migrations/versions/core/core_050_whiskers_scope_rename.py",
    # The live legacy-token map core_047 duplicates, plus the contract that
    # exercises the legacy `opencat` API-key scope it maps from.
    "core/scope_management/legacy_map.py",
    "core/scope_management/contracts.py",
    # This file names what it bans.
    "scripts/check_legacy_branding.py",
    # Real table names created by core_036/core_044, still present in the schema.
    "test/unit/test_search_engine.py",
    "migrations/versions/core/core_036_tenant_columns.py",
    "migrations/versions/core/core_044_embeddings_hybrid.py",
    # The dispatch record of the audit that drove the rename — it names the
    # terms that were hunted, so the old names are its content.
    "jules-dispatch.md",
)

# Individual lines that may keep a legacy name (substring match on the line).
ALLOWLISTED_LINE_SUBSTRINGS: tuple[str, ...] = (
    # The GitHub repo has not been renamed yet; see docs/cleanup-audit.
    "OpenCat-Mcp-Full",
    "Open-Cat-Tunnel-MCP",
    "opencat-mcp-full",
)


def tracked_files() -> list[Path]:
    """Every git-tracked file, so untracked scratch output is ignored."""
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO_ROOT / line for line in out.splitlines() if line]


def is_allowlisted_path(rel_path: str) -> bool:
    return any(rel_path.startswith(p) or rel_path == p for p in ALLOWLISTED_PATHS)


def scan() -> list[tuple[str, int, str]]:
    """Return ``(rel_path, line_no, line)`` for every banned-term hit."""
    hits: list[tuple[str, int, str]] = []
    for path in tracked_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if is_allowlisted_path(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable — nothing to lint
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not BANNED.search(line):
                continue
            if any(s in line for s in ALLOWLISTED_LINE_SUBSTRINGS):
                continue
            hits.append((rel, line_no, line.strip()))
    return hits


def main() -> int:
    hits = scan()
    if not hits:
        print("legacy-branding check: clean")
        return 0
    print(f"legacy-branding check: {len(hits)} hit(s)\n")
    for rel, line_no, line in hits:
        print(f"{rel}:{line_no}: {line[:160]}")
    print(
        "\nThe product is Whiskers Agent. Rename the hit, or — only when the old "
        "name is genuinely the correct content — add it to the allowlist in "
        "scripts/check_legacy_branding.py with a comment saying why."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
