#!/usr/bin/env python3
"""
Whiskers Agent Semantic Tools Generator
=================================
Generates semantic search and upsert MCP tools following the standard
state-machine dispatch pattern. Tools are automatically gated on DATABASE_URL.

Supports:
  - Search tools: global-only or scoped (query + scope field)
  - Upsert tools: embed and persist entity data for future search

Usage
-----
    # Generate from a YAML config
    python Tools/semantic_tools_generator.py --config semantic_config.yaml \\
        --output plugins/my_plugin/MCPTools/SemanticTools/my_tools.py

    # Also emit TOOL_SCHEMAS entries for LangGraph
    python Tools/semantic_tools_generator.py --config semantic_config.yaml \\
        --output plugins/my_plugin/MCPTools/SemanticTools/my_tools.py --schema

    # Include plugin registration hint comment in the generated file
    python Tools/semantic_tools_generator.py --config semantic_config.yaml \\
        --output plugins/my_plugin/MCPTools/SemanticTools/my_tools.py --plugin

    # Interactive mode
    python Tools/semantic_tools_generator.py --interactive

    # Print an example YAML config to stdout
    python Tools/semantic_tools_generator.py --example

    # Scaffold a plugin migration for an embedding table (upgrade(conn) pattern)
    python Tools/semantic_tools_generator.py --gen-migration \\
        --plugin-id my_plugin --migration-name add_record_embeddings \\
        --entity record --embed-table record_embeddings --id-field record_id

    # Dry-run the embedding migration (print to stdout)
    python Tools/semantic_tools_generator.py --gen-migration \\
        --plugin-id my_plugin --migration-name add_record_embeddings \\
        --entity record --embed-table record_embeddings --id-field record_id --dry-run
"""

import argparse
import json
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
from _write_utils import emit_generated, write_text_safe, WriteError  # noqa: E402

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ---------------------------------------------------------------------------
# Module-level wrapper template
# ---------------------------------------------------------------------------

_MODULE_TEMPLATE = '''\
"""{module_doc}
These tools require DATABASE_URL to be configured.
"""

import os
import logging
from typing import Any

from core.context import mcp

logger = logging.getLogger("whiskers")

_DB_AVAILABLE = bool(os.environ.get("DATABASE_URL", "").strip())

if not _DB_AVAILABLE:
    logger.info("{module_name} SKIPPED — DATABASE_URL not set")
else:
{body}'''


# ---------------------------------------------------------------------------
# Search tool templates
# ---------------------------------------------------------------------------

# Scoped search: query-only (global) OR query+scope_field (scoped)
_SEARCH_SCOPED_TEMPLATE = '''\
    # ── {tool_name}: state helpers ──────────────────────────────────────────

    def _determine_{entity}_search_state(
        query: "str | None",
        {scope_field}: "str | None",
    ) -> str:
        """Map (query, {scope_field}) combination to an execution state.

        States:
          - 'scoped_search': query + {scope_field} both provided
          - 'global_search': query only
          - 'invalid':       no query provided
        """
        if query and {scope_field}:
            return "scoped_search"
        if query:
            return "global_search"
        return "invalid"

    async def _handle_{entity}_global_search(
        query: str, top_k: int
    ) -> "dict[str, Any]":
        """Handler: global semantic search (no scope)."""
        try:
            from {embedding_module} import {search_fn}
            results = await {search_fn}(query, top_k)
            return {{"mode": "global_search", "results": results, "count": len(results)}}
        except Exception as exc:
            logger.exception("{tool_name} global_search failed")
            return {{"status": "error", "message": str(exc)}}

    async def _handle_{entity}_scoped_search(
        query: str, {scope_field}: str, top_k: int
    ) -> "dict[str, Any]":
        """Handler: semantic search scoped to a {scope_field}."""
        try:
            from {embedding_module} import {search_fn}
            results = await {search_fn}(query, top_k, {scope_field}={scope_field})
            return {{
                "mode": "scoped_search",
                "{scope_field}": {scope_field},
                "results": results,
                "count": len(results),
            }}
        except Exception as exc:
            logger.exception("{tool_name} scoped_search failed")
            return {{"status": "error", "message": str(exc)}}

    @mcp.tool()
    async def {tool_name}(
        query: "str | None" = None,
        {scope_field}: "str | None" = None,
        top_k: int = {top_k_default},
    ) -> "dict[str, Any]":
        """{docstring}

        Supports two modes:
          - Global search: provide query only.
          - Scoped search: provide query + {scope_field} to narrow results.
{examples_block}
        Args:
            query: Natural language search query.
            {scope_field}: {scope_field_description}
            top_k: Maximum results to return (default {top_k_default}).

        Returns:
            A dict with mode, results list, and count.
        """
        state = _determine_{entity}_search_state(query, {scope_field})
        state_handlers: dict = {{
            "scoped_search": lambda: _handle_{entity}_scoped_search(query, {scope_field}, top_k),
            "global_search": lambda: _handle_{entity}_global_search(query, top_k),
        }}
        handler = state_handlers.get(state)
        if handler is None:
            return {{
                "status": "error",
                "error": "missing_required_fields",
                "missing_fields": ["query"],
                "message": "Please provide a search query.",
            }}
        return await handler()
'''

# Global-only search: query required, no scope field
_SEARCH_GLOBAL_ONLY_TEMPLATE = '''\
    # ── {tool_name}: state helpers ──────────────────────────────────────────

    def _determine_{entity}_search_state(query: "str | None") -> str:
        """Map query to execution state.

        States:
          - 'global_search': query provided and non-empty
          - 'invalid':       no query
        """
        if query and query.strip():
            return "global_search"
        return "invalid"

    async def _handle_{entity}_global_search(
        query: str, top_k: int
    ) -> "dict[str, Any]":
        """Handler: global semantic search."""
        try:
            from {embedding_module} import {search_fn}
            results = await {search_fn}(query, top_k)
            return {{"status": "success", "mode": "global_search", "results": results, "count": len(results)}}
        except Exception as exc:
            logger.exception("{tool_name} failed")
            return {{"status": "error", "message": str(exc)}}

    @mcp.tool()
    async def {tool_name}(
        query: str,
        top_k: int = {top_k_default},
    ) -> "dict[str, Any]":
        """{docstring}
{examples_block}
        Args:
            query: Natural language search query (required).
            top_k: Maximum results to return (default {top_k_default}).

        Returns:
            A dict with status, results list, and count.
        """
        state = _determine_{entity}_search_state(query)
        if state == "global_search":
            return await _handle_{entity}_global_search(query.strip(), top_k)
        return {{
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["query"],
            "message": "Please provide a search query.",
        }}
'''


# ---------------------------------------------------------------------------
# Upsert tool template
# ---------------------------------------------------------------------------

_UPSERT_TOOL_TEMPLATE = '''\
    @mcp.tool()
    async def {tool_name}(
        {id_field}: {id_type},
        content: str,
        metadata: "dict | None" = None,
    ) -> "dict[str, Any]":
        """{docstring}

        Args:
            {id_field}: {id_description}
            content: {content_description}
            metadata: Optional metadata dict stored alongside the embedding.

        Returns:
            A dict with status and confirmation message, or an error dict.
        """
        missing = []
        if not {id_field}:
            missing.append("{id_field} ({id_description})")
        if not content or not content.strip():
            missing.append("content ({content_description})")
        if missing:
            return {{
                "status": "error",
                "error": "missing_required_fields",
                "missing_fields": missing,
                "message": f"Please provide: {{', '.join(missing)}}",
            }}

        try:
            from {embedding_module} import {upsert_fn}
            await {upsert_fn}(str({id_field}), content.strip(), metadata or {{}})
            return {{
                "status": "success",
                "message": f"Upserted embedding for {entity} '{{{id_field}}}'",
                "{id_field}": {id_field},
            }}
        except Exception as exc:
            logger.exception("{tool_name} failed")
            return {{"status": "error", "message": str(exc)}}
'''


# ---------------------------------------------------------------------------
# Example YAML config
# ---------------------------------------------------------------------------

_EXAMPLE_CONFIG = """\
# Whiskers Agent Semantic Tools Generator — example configuration
# Save as semantic_config.yaml and run:
#   python semantic_tools_generator.py --config semantic_config.yaml --output MCPTools/SemanticTools/record_search.py

module_doc: "Semantic record search and embedding tools. Powered by PostgreSQL + pgvector."
module_name: "record_semantic_tools"

search_tools:
  - name: semantic_search_records
    entity: record
    description: "Search records using natural language semantic similarity. Uses pgvector cosine similarity over record embeddings."
    embedding_module: "plugins.my_plugin.embeddings.record_embeddings"
    search_fn: search_records
    scope_field: null              # null = global-only; a field name = adds scoped mode
    scope_field_description: null
    top_k_default: 5
    examples:
      - "records tagged urgent from last week"
      - "record mentioning a billing dispute"

  - name: semantic_search_notes
    entity: note
    description: "Search notes using natural language semantic similarity. Optionally scoped to a single notebook."
    embedding_module: "plugins.my_plugin.embeddings.note_embeddings"
    search_fn: search_notes
    scope_field: notebook_id   # enables scoped_search mode
    scope_field_description: "Optional notebook ID to narrow search to one notebook."
    top_k_default: 5
    examples:
      - "note about the pricing change"
      - "notes mentioning a shipping delay"

upsert_tools:
  - name: upsert_record_embedding
    entity: record
    description: "Embed and persist record data for semantic search. Creates or updates the record's entry in the pgvector index."
    embedding_module: "plugins.my_plugin.embeddings.record_embeddings"
    upsert_fn: upsert_record
    id_field: record_id
    id_type: str
    id_description: "The record's unique identifier."
    content_description: "Text to embed (e.g. title, tags, notes)."

  - name: upsert_note_embedding
    entity: note
    description: "Embed and persist a note for semantic search. Creates or updates the note entry in the pgvector index."
    embedding_module: "plugins.my_plugin.embeddings.note_embeddings"
    upsert_fn: upsert_note
    id_field: note_id
    id_type: str
    id_description: "The note's unique identifier."
    content_description: "Note text to embed."
"""


# ---------------------------------------------------------------------------
# Code-generation helpers
# ---------------------------------------------------------------------------

def _build_examples_block(examples: list[str] | None) -> str:
    """Format examples list into docstring lines."""
    if not examples:
        return ""
    lines = ["        Example queries:"]
    for ex in examples:
        lines.append(f'          - "{ex}"')
    return "\n".join(lines)


def generate_search_tool(spec: dict) -> str:
    """Generate a single search tool block from a spec dict."""
    scope_field = spec.get("scope_field") or None
    examples_block = _build_examples_block(spec.get("examples"))
    entity = spec["entity"]

    common = {
        "tool_name": spec["name"],
        "entity": entity,
        "docstring": spec.get("description", f"Semantic search over {entity} embeddings."),
        "embedding_module": spec["embedding_module"],
        "search_fn": spec["search_fn"],
        "top_k_default": spec.get("top_k_default", 5),
        "examples_block": examples_block,
    }

    if scope_field:
        return _SEARCH_SCOPED_TEMPLATE.format(
            **common,
            scope_field=scope_field,
            scope_field_description=spec.get("scope_field_description", f"Optional {scope_field} to narrow the search."),
        )
    else:
        return _SEARCH_GLOBAL_ONLY_TEMPLATE.format(**common)


def generate_upsert_tool(spec: dict) -> str:
    """Generate a single upsert tool block from a spec dict."""
    return _UPSERT_TOOL_TEMPLATE.format(
        tool_name=spec["name"],
        entity=spec["entity"],
        docstring=spec.get("description", f"Upsert {spec['entity']} embedding for semantic search."),
        embedding_module=spec["embedding_module"],
        upsert_fn=spec["upsert_fn"],
        id_field=spec["id_field"],
        id_type=spec.get("id_type", "str"),
        id_description=spec.get("id_description", f"The {spec['entity']} unique ID."),
        content_description=spec.get("content_description", "Text content to embed."),
    )


def generate_module(config: dict) -> str:
    """Generate a full Python module from a config dict."""
    body_parts = []

    for spec in config.get("search_tools", []):
        body_parts.append(generate_search_tool(spec))

    for spec in config.get("upsert_tools", []):
        body_parts.append(generate_upsert_tool(spec))

    body = "\n\n".join(body_parts)

    return _MODULE_TEMPLATE.format(
        module_doc=config.get("module_doc", "Auto-generated semantic MCP tools."),
        module_name=config.get("module_name", "semantic_tools"),
        body=body,
    )


# ---------------------------------------------------------------------------
# TOOL_SCHEMAS entry generator (for langgraph_flows/tool_schemas.py)
# ---------------------------------------------------------------------------

def generate_schema_entries(config: dict) -> str:
    """Generate TOOL_SCHEMAS dict entries for all tools in the config."""
    lines = ["# Add the following entries to TOOL_SCHEMAS in langgraph_flows/tool_schemas.py:"]

    for spec in config.get("search_tools", []):
        scope = spec.get("scope_field")
        required = [] if scope else ["query"]
        optional = (["query", scope, "top_k"] if scope else ["top_k"])
        optional = [f for f in optional if f]

        descs = {"query": spec.get("description", "Natural language search query.")}
        if scope:
            descs[scope] = spec.get("scope_field_description", f"Optional {scope} to narrow search.")
        descs["top_k"] = "Maximum results to return."

        lines.append(f'    "{spec["name"]}": {{')
        lines.append(f'        "required": {json.dumps(required)},')
        lines.append(f'        "optional": {json.dumps(optional)},')
        lines.append('        "descriptions": {')
        for k, v in descs.items():
            lines.append(f'            "{k}": "{v}",')
        lines.append("        },")
        lines.append("    },")

    for spec in config.get("upsert_tools", []):
        id_field = spec["id_field"]
        lines.append(f'    "{spec["name"]}": {{')
        lines.append(f'        "required": ["{id_field}", "content"],')
        lines.append('        "optional": ["metadata"],')
        lines.append('        "descriptions": {')
        lines.append(f'            "{id_field}": "{spec.get("id_description", "")}",')
        lines.append('            "content": "Text to embed.",')
        lines.append('            "metadata": "Optional metadata dict.",')
        lines.append("        },")
        lines.append("    },")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def interactive_mode() -> dict:
    """Walk the user through creating a semantic tool spec interactively. Returns config dict."""
    print("\n=== Whiskers Agent Semantic Tools Generator (Interactive) ===\n")

    module_doc = input("Module docstring (one line): ").strip() or "Auto-generated semantic MCP tools."
    module_name = input("Module name (snake_case): ").strip() or "semantic_tools"

    search_tools = []
    upsert_tools = []

    while True:
        print(f"\n--- Tool type ---")
        tool_type = input("  Tool type (search/upsert/done) [done]: ").strip().lower()
        if tool_type in ("done", ""):
            break

        if tool_type == "search":
            name = input("  Function name (e.g. semantic_search_records): ").strip()
            if not name:
                continue
            entity = input("  Entity name (e.g. record, message): ").strip()
            description = input("  Description: ").strip()
            embedding_module = input("  Embedding module import path: ").strip()
            search_fn = input("  Search function name in that module: ").strip()
            scope_field = input("  Scope field (blank = global-only, e.g. conversation_id): ").strip() or None
            scope_desc = None
            if scope_field:
                scope_desc = input(f"  {scope_field} description: ").strip()
            top_k = int(input("  Default top_k [5]: ").strip() or "5")

            examples = []
            print("  Example queries (blank to stop):")
            while True:
                ex = input("    Example: ").strip()
                if not ex:
                    break
                examples.append(ex)

            search_tools.append({
                "name": name,
                "entity": entity,
                "description": description,
                "embedding_module": embedding_module,
                "search_fn": search_fn,
                "scope_field": scope_field,
                "scope_field_description": scope_desc,
                "top_k_default": top_k,
                "examples": examples,
            })

        elif tool_type == "upsert":
            name = input("  Function name (e.g. upsert_record_embedding): ").strip()
            if not name:
                continue
            entity = input("  Entity name: ").strip()
            description = input("  Description: ").strip()
            embedding_module = input("  Embedding module import path: ").strip()
            upsert_fn = input("  Upsert function name in that module: ").strip()
            id_field = input("  ID field name (e.g. record_id): ").strip()
            id_type = input("  ID field type [str]: ").strip() or "str"
            id_desc = input(f"  {id_field} description: ").strip()
            content_desc = input("  content description: ").strip()

            upsert_tools.append({
                "name": name,
                "entity": entity,
                "description": description,
                "embedding_module": embedding_module,
                "upsert_fn": upsert_fn,
                "id_field": id_field,
                "id_type": id_type,
                "id_description": id_desc,
                "content_description": content_desc,
            })

    return {
        "module_doc": module_doc,
        "module_name": module_name,
        "search_tools": search_tools,
        "upsert_tools": upsert_tools,
    }


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
# Migration scaffolding (embedding tables via PluginSchemaMigrator)
# ---------------------------------------------------------------------------

# Template: one BIGSERIAL + id_field + content + embedding + model + meta + synced_at
_EMBED_TABLE_MIGRATION_TEMPLATE = '''\
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


def upgrade(conn) -> None:
    """Create {table} embedding table if it does not exist."""
    dim = os.environ.get("EMBED_DIMENSIONS", "1536")

    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id          BIGSERIAL PRIMARY KEY,
                {id_field}  VARCHAR NOT NULL UNIQUE,
                content     TEXT NOT NULL,
                embedding   VECTOR({{dim}}),
                model       VARCHAR NOT NULL DEFAULT \'\',
                meta        JSONB DEFAULT \'{{}}\',
                synced_at   TIMESTAMPTZ DEFAULT now()
            )
            """
        )
    )
'''


def _generate_embed_migration(
    plugin_id: str,
    name: str,
    entity: str,
    embed_table: str,
    id_field: str,
    description: str = "",
    revision: str = "",
) -> str:
    """Generate an embedding table migration file content string.

    Args:
        plugin_id:   Plugin identifier.
        name:        Migration name (slug).
        entity:      Entity name (record, message, …).
        embed_table: PostgreSQL table name for the embeddings.
        id_field:    Primary identifier column (e.g. ``record_id``).
        description: One-line description.
        revision:    4-digit revision string.

    Returns:
        Full Python source of the migration file.
    """
    import datetime

    if not description:
        description = f"Create {embed_table} embedding table for {entity} semantic search."
    if not revision:
        revision = "NNNN"

    created_date = datetime.date.today().isoformat()

    return _EMBED_TABLE_MIGRATION_TEMPLATE.format(
        description=description,
        plugin_id=plugin_id,
        revision=revision,
        created_date=created_date,
        table=embed_table,
        id_field=id_field,
    )


def _run_gen_migration(args: "argparse.Namespace") -> None:
    """Scaffold an embedding table migration for --gen-migration.

    Delegates revision-number tracking and file write to migration_generator,
    and generates embedding-specific DDL via _generate_embed_migration.
    """
    from migration_generator import (
        _next_revision,
        _migrations_dir,
        _resolve_output_path,
    )

    plugin_id: str = getattr(args, "plugin_id", "") or ""
    name: str = getattr(args, "migration_name", "") or ""
    entity: str = getattr(args, "entity", "") or ""
    embed_table: str = getattr(args, "embed_table", "") or ""
    id_field: str = getattr(args, "id_field", "") or f"{entity}_id" if entity else "entity_id"
    desc: str = getattr(args, "migration_description", "") or ""
    mig_revision: str = getattr(args, "migration_revision", "") or ""
    dry_run: bool = bool(getattr(args, "dry_run", False))

    for req, flag in ((plugin_id, "--plugin-id"), (name, "--migration-name"),
                      (embed_table, "--embed-table")):
        if not req:
            print(f"ERROR: {flag} is required for --gen-migration.", file=sys.stderr)
            sys.exit(1)

    import re
    mig_dir = _migrations_dir(plugin_id)
    revision = mig_revision if (mig_revision and len(mig_revision) == 4 and mig_revision.isdigit()) \
        else _next_revision(mig_dir)

    code = _generate_embed_migration(
        plugin_id=plugin_id,
        name=name,
        entity=entity or embed_table,
        embed_table=embed_table,
        id_field=id_field or f"{entity or 'entity'}_id",
        description=desc,
        revision=revision,
    )

    if dry_run:
        out_path = _resolve_output_path(plugin_id, revision, name)
        print(f"# [dry-run] Would write: {out_path}")
        print(code)
        return

    out_path = _resolve_output_path(plugin_id, revision, name)
    try:
        write_text_safe(out_path, code)
        print(f"✓ Embedding migration generated → {out_path}")
    except WriteError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """
    Main entrypoint for generating semantic tools.
    """
    parser = argparse.ArgumentParser(
        description="Generate Whiskers Agent semantic MCP tool modules from YAML/JSON configs.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--config", help="Path to a YAML or JSON semantic tool config file.")
    group.add_argument("--interactive", action="store_true", help="Interactive semantic tool builder.")
    group.add_argument("--example", action="store_true", help="Print an example YAML config to stdout.")
    group.add_argument(
        "--gen-migration",
        action="store_true",
        dest="gen_migration",
        help=(
            "Scaffold a plugin-local embedding table migration (upgrade(conn) pattern). "
            "Requires --plugin-id, --migration-name, --embed-table. "
            "Output: plugins/<plugin>/migrations/NNNN_<name>.py — "
            "compatible with PluginSchemaMigrator (db_layer/plugin_schema_migrator.py)."
        ),
    )

    parser.add_argument("--output", "-o", help="Output file path (default: stdout).")
    parser.add_argument(
        "--schema", action="store_true",
        help="Also print TOOL_SCHEMAS entries for the LangGraph agent.",
    )
    parser.add_argument(
        "--plugin", action="store_true",
        help="Prepend a plugin registration hint comment to the generated file.",
    )
    # --gen-migration arguments
    parser.add_argument(
        "--plugin-id", metavar="PLUGIN_ID",
        help="(--gen-migration) Plugin ID (e.g. my_plugin).",
    )
    parser.add_argument(
        "--migration-name", metavar="NAME",
        help="(--gen-migration) Migration name in snake_case (e.g. add_lab_embeddings).",
    )
    parser.add_argument(
        "--entity", metavar="ENTITY",
        help="(--gen-migration) Entity name (e.g. record). Used in comments and column naming.",
    )
    parser.add_argument(
        "--embed-table", metavar="TABLE", dest="embed_table",
        help="(--gen-migration) Embedding table name (e.g. lab_embeddings).",
    )
    parser.add_argument(
        "--id-field", metavar="FIELD", dest="id_field",
        help="(--gen-migration) Unique ID column (e.g. lab_id). Default: <entity>_id.",
    )
    parser.add_argument(
        "--migration-description", metavar="TEXT", dest="migration_description", default="",
        help="(--gen-migration) One-line description for the migration docstring.",
    )
    parser.add_argument(
        "--migration-revision", metavar="NNNN", dest="migration_revision", default="",
        help="(--gen-migration) Explicit 4-digit revision (auto-derived if omitted).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="(--gen-migration) Print to stdout without writing to disk.",
    )
    args = parser.parse_args()

    # ── gen-migration ─────────────────────────────────────────────────────────
    if getattr(args, "gen_migration", False):
        _run_gen_migration(args)
        return

    if args.example:
        print(_EXAMPLE_CONFIG)
        return

    if args.interactive:
        config = interactive_mode()
    else:
        config = load_config(args.config)

    code = generate_module(config)

    if args.plugin:
        module_name = config.get("module_name", "semantic_tools")
        hint = (
            f"# Plugin registration hint:\n"
            f"# In MCPTools/SemanticTools/__init__.py, add:\n"
            f"#   from . import {module_name}  # noqa: F401\n"
            f"\n"
        )
        code = hint + code

    emit_generated(code, args.output)

    if args.schema:
        print("\n" + "=" * 60)
        print(generate_schema_entries(config))


if __name__ == "__main__":
    main()
