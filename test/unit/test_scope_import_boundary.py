"""Assert is_allowed decision logic lives only in core.scope_management."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Directories to scan for forbidden `def is_allowed`
SCAN_DIRS = ["core", "api", "core_graph", "oauth"]

# Files allowed to re-export is_allowed (shim only — no def body)
ALLOWED_REEXPORT = {
    "core/api_key_management/scopes.py",
    "core/scope_management/__init__.py",
}


def test_no_is_allowed_def_outside_package():
    """Only scope_management may define is_allowed logic."""
    offenders: list[str] = []
    for dirname in SCAN_DIRS:
        root = REPO / dirname
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if rel.startswith("core/scope_management/"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "def is_allowed" in text:
                # Shim re-export only is OK if it does not contain a real body
                # with decision logic (look for "return bool" / "caller_scopes is None")
                if rel in ALLOWED_REEXPORT and "caller_scopes is None" not in text:
                    continue
                offenders.append(rel)
    assert offenders == [], f"is_allowed defined outside package: {offenders}"
