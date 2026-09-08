#!/usr/bin/env python3
"""
Whiskers Agent Plugin Migration Generator
====================================
Generates plugin-local migration files following the ``upgrade(conn)`` pattern
used by ``PluginSchemaMigrator`` (``db_layer/plugin_schema_migrator.py``).

Migration files are placed under ``plugins/<plugin>/migrations/NNNN_name.py``
and executed automatically on plugin load by ``PluginSchemaMigrator``.

Unlike Alembic core migrations (which use ``alembic.op`` and a ``revision`` /
``down_revision`` chain), plugin migrations use a simpler ordered-filename
approach and receive a live SQLAlchemy connection from the migrator.

Reference:
  - Pattern: ``plugins/my_plugin/migrations/0001_init.py``
  - Runner:   ``db_layer/plugin_schema_migrator.py``

Usage
-----
    # List next revision number for a plugin
    python Tools/migration_generator.py --plugin my_plugin --name add_records --list-next

    # Scaffold a CREATE TABLE migration
    python Tools/migration_generator.py \\
        --plugin my_plugin --name add_records \\
        --type create_table \\
        --table records \\
        --columns "id:BIGSERIAL PRIMARY KEY" "record_id:VARCHAR NOT NULL" \\
                  "payload:TEXT NOT NULL" "recorded_at:TIMESTAMPTZ DEFAULT now()"

    # Scaffold an ADD COLUMN migration
    python Tools/migration_generator.py \\
        --plugin my_plugin --name add_record_status \\
        --type add_column \\
        --table records \\
        --columns "status:VARCHAR"

    # Scaffold a blank (custom) migration
    python Tools/migration_generator.py \\
        --plugin my_plugin --name custom_backfill \\
        --type blank

    # Interactive mode
    python Tools/migration_generator.py --interactive

    # Print an example YAML config
    python Tools/migration_generator.py --example
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
from _write_utils import WriteError, emit_generated, write_text_safe  # noqa: E402

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Filename pattern enforced by PluginSchemaMigrator (must match exactly).
_STEP_FILENAME_RE = re.compile(r"^\d{4}_[a-z0-9_]+\.(sql|py)$")

_MIGRATION_TYPES = ("create_table", "add_column", "drop_column", "add_index",
                    "create_table_if_not_exists", "blank")


# ---------------------------------------------------------------------------
# Template: module docstring + imports header
# ---------------------------------------------------------------------------

_FILE_HEADER = '''\
"""{description}

Plugin: {plugin_id}
Revision: {revision}
Created: {created_date}

Applied by PluginSchemaMigrator — receives a live SQLAlchemy connection.
See: db_layer/plugin_schema_migrator.py
"""

from __future__ import annotations

import os

from sqlalchemy import text
'''

# ---------------------------------------------------------------------------
# Templates per migration type
# ---------------------------------------------------------------------------

_UPGRADE_HEADER = "\n\ndef upgrade(conn) -> None:"

_CREATE_TABLE_BODY = '''\
    """Create {table} table if it does not exist."""
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS {table} (
{columns_block}
            )
            """
        )
    )
'''

_ADD_COLUMN_BODY = '''\
    """Add column(s) to {table}."""
{add_stmts}
'''

_DROP_COLUMN_BODY = '''\
    """Drop column(s) from {table}."""
{drop_stmts}
'''

_ADD_INDEX_BODY = '''\
    """Add index on {table}.{columns}."""
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({columns})"
        )
    )
'''

_BLANK_BODY = '''\
    """TODO: implement this migration step."""
    # conn.execute(text("..."))
    pass
'''

_EXAMPLE_CONFIG = """\
# Whiskers Agent Plugin Migration Generator — example configuration
# Save as migration_config.yaml and run:
#   python Tools/migration_generator.py --config migration_config.yaml

plugin_id: my_plugin
name: add_records
description: "Create records table for domain record data."
type: create_table
table: records
columns:
  - "id          BIGSERIAL PRIMARY KEY"
  - "record_id   VARCHAR NOT NULL"
  - "payload     TEXT NOT NULL"
  - "status      VARCHAR DEFAULT 'pending'"
  - "recorded_at TIMESTAMPTZ DEFAULT now()"
  - "meta        JSONB DEFAULT '{}'"
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _plugins_dir() -> Path:
    return _REPO_ROOT / "plugins"


def _migrations_dir(plugin_id: str) -> Path:
    return _plugins_dir() / plugin_id / "migrations"


def _next_revision(migrations_dir: Path) -> str:
    """Return the next zero-padded 4-digit revision number."""
    if not migrations_dir.is_dir():
        return "0001"
    existing = sorted(
        int(p.name[:4])
        for p in migrations_dir.iterdir()
        if p.is_file() and _STEP_FILENAME_RE.match(p.name)
    )
    nxt = (existing[-1] + 1) if existing else 1
    return f"{nxt:04d}"


def _slugify(name: str) -> str:
    """Convert a name to a safe filename slug (lowercase, underscores only)."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower())
    return slug.strip("_")


def _format_columns_block(columns: list[str], indent: int = 16) -> str:
    """Format column definitions for a CREATE TABLE body."""
    pad = " " * indent
    lines = []
    for i, col in enumerate(columns):
        comma = "," if i < len(columns) - 1 else ""
        lines.append(f"{pad}{col.strip()}{comma}")
    return "\n".join(lines)


def _format_add_column_stmts(table: str, columns: list[str]) -> str:
    """Format ADD COLUMN IF NOT EXISTS statements."""
    stmts = []
    for col in columns:
        col_name = col.strip().split()[0]
        # Derive the full column definition minus the name for the type portion
        col_def = col.strip()[len(col_name):].strip()
        stmt = (
            f'    conn.execute(\n'
            f'        text(\n'
            f'            "ALTER TABLE {table} '\
            f'ADD COLUMN IF NOT EXISTS {col_name} {col_def}"\n'
            f'        )\n'
            f'    )'
        )
        stmts.append(stmt)
    return "\n".join(stmts)


def _format_drop_column_stmts(table: str, columns: list[str]) -> str:
    """Format DROP COLUMN IF EXISTS statements."""
    stmts = []
    for col in columns:
        col_name = col.strip().split()[0]
        stmt = (
            f'    conn.execute(\n'
            f'        text(\n'
            f'            "ALTER TABLE {table} '\
            f'DROP COLUMN IF EXISTS {col_name}"\n'
            f'        )\n'
            f'    )'
        )
        stmts.append(stmt)
    return "\n".join(stmts)


def _format_index_name(table: str, columns: str) -> str:
    cols_slug = _slugify(columns.replace(",", "_"))
    return f"idx_{table}_{cols_slug}"


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------

def generate_migration(
    plugin_id: str,
    name: str,
    migration_type: str,
    *,
    table: str = "",
    columns: list[str] | None = None,
    description: str = "",
    revision: str = "",
    created_date: str = "",
) -> str:
    """Generate a plugin migration file as a string.

    Args:
        plugin_id:      Plugin identifier (e.g. ``my_plugin``).
        name:           Human name for the migration (becomes part of filename).
        migration_type: One of ``create_table``, ``add_column``, ``drop_column``,
                        ``add_index``, ``create_table_if_not_exists``, ``blank``.
        table:          Target table name (required for schema-modifying types).
        columns:        Column definitions (list of SQL column strings).
        description:    One-line description for the module docstring.
        revision:       Explicit revision string (e.g. ``0003``). Auto-derived if blank.
        created_date:   Date string for the header; defaults to today.
    """
    import datetime

    slug = _slugify(name)
    if not created_date:
        created_date = datetime.date.today().isoformat()
    if not description:
        description = f"{migration_type.replace('_', ' ').title()}: {slug}."

    cols = columns or []

    header = _FILE_HEADER.format(
        description=description,
        plugin_id=plugin_id,
        revision=revision or "NNNN",
        created_date=created_date,
    )

    body = _UPGRADE_HEADER

    if migration_type in ("create_table", "create_table_if_not_exists"):
        columns_block = _format_columns_block(cols) if cols else " " * 16 + "-- TODO: add columns"
        body += "\n" + _CREATE_TABLE_BODY.format(
            table=table,
            columns_block=columns_block,
        )

    elif migration_type == "add_column":
        add_stmts = _format_add_column_stmts(table, cols) if cols else (
            '    # conn.execute(text("ALTER TABLE {table} ADD COLUMN IF NOT EXISTS name TYPE"))'
        ).format(table=table)
        body += "\n" + _ADD_COLUMN_BODY.format(table=table, add_stmts=add_stmts)

    elif migration_type == "drop_column":
        drop_stmts = _format_drop_column_stmts(table, cols) if cols else (
            '    # conn.execute(text("ALTER TABLE {table} DROP COLUMN IF EXISTS name"))'
        ).format(table=table)
        body += "\n" + _DROP_COLUMN_BODY.format(table=table, drop_stmts=drop_stmts)

    elif migration_type == "add_index":
        cols_str = ", ".join(c.strip().split()[0] for c in cols) if cols else "column_name"
        index_name = _format_index_name(table, cols_str)
        body += "\n" + _ADD_INDEX_BODY.format(
            table=table,
            columns=cols_str,
            index_name=index_name,
        )

    else:  # blank
        body += "\n" + _BLANK_BODY

    return header + body


def _resolve_output_path(plugin_id: str, revision: str, name: str) -> Path:
    """Build the canonical output path for a plugin migration file."""
    slug = _slugify(name)
    filename = f"{revision}_{slug}.py"
    return _migrations_dir(plugin_id) / filename


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    """Load a YAML or JSON config file."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        if not HAS_YAML:
            print("ERROR: pyyaml is required for YAML configs.  pip install pyyaml", file=sys.stderr)
            sys.exit(1)
        return yaml.safe_load(text)
    return json.loads(text)


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def interactive_mode() -> tuple[str, dict]:
    """Walk the user through creating a migration spec. Returns (plugin_id, spec)."""
    print("\n=== Whiskers Agent Plugin Migration Generator (Interactive) ===\n")

    plugin_id = input("  Plugin ID (e.g. my_plugin): ").strip()
    if not plugin_id:
        print("ERROR: plugin_id is required.", file=sys.stderr)
        sys.exit(1)

    name = input("  Migration name (snake_case, e.g. add_records): ").strip()
    if not name:
        print("ERROR: name is required.", file=sys.stderr)
        sys.exit(1)

    description = input("  Description (one line): ").strip()

    print(f"  Migration type ({', '.join(_MIGRATION_TYPES)}):")
    mtype = input("  Type [create_table]: ").strip().lower() or "create_table"
    if mtype not in _MIGRATION_TYPES:
        print(f"ERROR: Unknown type '{mtype}'. Choose from: {', '.join(_MIGRATION_TYPES)}", file=sys.stderr)
        sys.exit(1)

    table = ""
    columns: list[str] = []
    if mtype not in ("blank",):
        table = input("  Table name: ").strip()
        if mtype in ("create_table", "create_table_if_not_exists", "add_column"):
            print("  Column definitions (e.g. 'id BIGSERIAL PRIMARY KEY', blank to stop):")
            while True:
                col = input("    Column: ").strip()
                if not col:
                    break
                columns.append(col)
        elif mtype in ("drop_column", "add_index"):
            print("  Column names (blank to stop):")
            while True:
                col = input("    Column: ").strip()
                if not col:
                    break
                columns.append(col)

    return plugin_id, {
        "name": name,
        "description": description,
        "type": mtype,
        "table": table,
        "columns": columns,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Main entrypoint for the plugin migration generator CLI."""
    parser = argparse.ArgumentParser(
        description=(
            "Generate Whiskers Agent plugin migration files (upgrade(conn) pattern).\n\n"
            "Output files go to plugins/<plugin>/migrations/NNNN_<name>.py\n"
            "and are picked up automatically by PluginSchemaMigrator on plugin load."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plugin", metavar="PLUGIN_ID",
                       help="Plugin ID (e.g. my_plugin).  Use with --name.")
    group.add_argument("--config", metavar="FILE",
                       help="Path to a YAML/JSON config file (batch generation).")
    group.add_argument("--interactive", action="store_true",
                       help="Interactive migration builder.")
    group.add_argument("--example", action="store_true",
                       help="Print an example YAML config to stdout.")

    # --plugin arguments
    parser.add_argument("--name", metavar="NAME",
                        help="Migration name in snake_case (required with --plugin).")
    parser.add_argument(
        "--type", metavar="TYPE", dest="migration_type",
        choices=_MIGRATION_TYPES,
        default="blank",
        help=f"Migration type. One of: {', '.join(_MIGRATION_TYPES)}. Default: blank.",
    )
    parser.add_argument("--table", metavar="TABLE", default="",
                        help="Target table name.")
    parser.add_argument("--columns", metavar="COL", nargs="+", default=[],
                        help='Column definitions, e.g. "id BIGSERIAL PRIMARY KEY" '
                             '"name VARCHAR NOT NULL".')
    parser.add_argument("--description", metavar="TEXT", default="",
                        help="One-line description for the migration docstring.")
    parser.add_argument("--revision", metavar="NNNN", default="",
                        help="Explicit 4-digit revision (auto-derived if omitted).")

    # output control
    parser.add_argument("--output", "-o", metavar="FILE",
                        help="Output file (default: plugins/<plugin>/migrations/NNNN_<name>.py).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print generated content to stdout; do not write to disk.")
    parser.add_argument("--list-next", action="store_true",
                        help="Print the next revision number for --plugin and exit.")

    args = parser.parse_args()

    # ── example ──────────────────────────────────────────────────────────────
    if args.example:
        print(_EXAMPLE_CONFIG)
        return

    # ── interactive ───────────────────────────────────────────────────────────
    if args.interactive:
        plugin_id, spec = interactive_mode()
        _run_single(plugin_id, spec, dry_run=getattr(args, "dry_run", False), output=getattr(args, "output", None))
        return

    # ── config (batch) mode ───────────────────────────────────────────────────
    if args.config:
        cfg = load_config(args.config)
        # Support single or list of migrations
        migrations = cfg if isinstance(cfg, list) else [cfg]
        for mig in migrations:
            pid = mig.get("plugin_id", "")
            if not pid:
                print("ERROR: each config entry requires plugin_id.", file=sys.stderr)
                sys.exit(1)
            _run_single(pid, mig, dry_run=getattr(args, "dry_run", False), output=None)
        return

    # ── --plugin mode ─────────────────────────────────────────────────────────
    plugin_id = args.plugin
    if not args.name and not args.list_next:
        parser.error("--name is required when using --plugin (unless --list-next).")

    mig_dir = _migrations_dir(plugin_id)

    if args.list_next:
        print(_next_revision(mig_dir))
        return

    spec = {
        "name": args.name,
        "description": args.description,
        "type": args.migration_type,
        "table": args.table,
        "columns": args.columns,
        "revision": args.revision,
    }
    _run_single(plugin_id, spec, dry_run=args.dry_run, output=args.output)


def _run_single(plugin_id: str, spec: dict, *, dry_run: bool, output: str | None) -> None:
    """Generate a single migration file from a spec dict."""
    name = spec.get("name", "")
    if not name:
        print("ERROR: 'name' is required in the migration spec.", file=sys.stderr)
        sys.exit(1)

    mig_dir = _migrations_dir(plugin_id)

    # Resolve revision number
    revision = str(spec.get("revision", "")).strip()
    if not revision or not re.match(r"^\d{4}$", revision):
        revision = _next_revision(mig_dir)

    code = generate_migration(
        plugin_id=plugin_id,
        name=name,
        migration_type=spec.get("type", "blank"),
        table=spec.get("table", ""),
        columns=spec.get("columns") or [],
        description=spec.get("description", ""),
        revision=revision,
    )

    if dry_run:
        print(f"# [dry-run] Would write: {_resolve_output_path(plugin_id, revision, name)}")
        print(code)
        return

    if output:
        out_path = Path(output)
    else:
        out_path = _resolve_output_path(plugin_id, revision, name)

    try:
        write_text_safe(out_path, code)
        print(f"✓ Generated → {out_path.relative_to(_REPO_ROOT)}")
    except WriteError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
