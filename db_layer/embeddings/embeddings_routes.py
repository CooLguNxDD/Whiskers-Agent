"""
Route embedding search.

Upsert is owned by ``core_graph.worker.embedding_worker`` since core_014.
The legacy ``upsert_route_embeddings`` / ``upsert_route_embeddings_bulk``
functions remain as no-op shims for backwards compatibility; new callers
should contribute routes via ``RouteRegistry.contribute()`` and let the
async worker handle embedding.
"""

import json
import logging
import os
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from db_layer.connection import get_async_session
from db_layer.models import RouteEmbedding
from db_layer.embeddings.embeddings_core import embed, embed_batch
from db_layer.embeddings.search_engine import SearchSpec
from db_layer.embeddings.search_engine import search as _engine_search

logger = logging.getLogger("whiskers")

# Path to the generated OpenAPI spec
_OPENAPI_PATH = Path(__file__).resolve().parents[2] / "Pro" / "pro_tools_context" / "openapi.json"


def _build_embed_text(operation_id: str, method: str, path: str,
                      summary: str, description: str, tags: list[str]) -> str:
    """Build a rich text representation of a route for embedding."""
    parts = [
        f"{method.upper()} {path}",
        f"Operation: {operation_id}",
    ]
    if summary:
        parts.append(f"Summary: {summary}")
    if description:
        parts.append(f"Description: {description}")
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    return "\n".join(parts)


def _load_openapi_spec(spec_path: Path) -> dict:
    """Helper to synchronously check file existence and load the OpenAPI spec from disk."""
    if not spec_path.exists():
        raise FileNotFoundError(f"OpenAPI spec not found at {spec_path}")
    with open(spec_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _extract_parameters(operation: dict) -> dict:
    """Extract parameter schema from an OpenAPI operation object."""
    params = {
        "path": [],
        "query": [],
        "body": None,
    }
    for p in operation.get("parameters", []):
        loc = p.get("in", "query")
        entry = {
            "name": p.get("name"),
            "required": p.get("required", False),
            "schema": p.get("schema", {}),
            "description": p.get("description", ""),
        }
        if loc == "path":
            params["path"].append(entry)
        elif loc == "query":
            params["query"].append(entry)

    # Request body (POST/PUT/PATCH)
    req_body = operation.get("requestBody", {})
    if req_body:
        content = req_body.get("content", {})
        json_schema = content.get("application/json", {}).get("schema", {})
        if json_schema:
            params["body"] = json_schema

    return params


async def upsert_route_embeddings(openapi_path: str | None = None) -> int:  # noqa: ARG001
    """DEPRECATED — embedding is now owned by the async worker (core_014).

    Returns 0 and logs a one-time warning. Plugins should contribute routes
    via ``RouteRegistry.contribute()``; the worker will pick them up.
    """
    logger.warning(
        "upsert_route_embeddings() is a no-op since core_014; "
        "contribute routes via RouteRegistry instead."
    )
    return 0


def _route_row_to_dict(row) -> dict:
    """Project a RouteEmbedding search row into the candidate dict shape
    consumed by core_graph (planner/executor)."""
    params = row.parameters or (row.meta or {}).get("parameters", {}) or {}
    return {
        "plugin_id": row.plugin_id,
        "operation_id": row.operation_id,
        "path": row.path,
        "path_template": row.path_template or row.path,
        "method": row.method,
        "description": row.description,
        "parameters": params,
        "is_fast_path": bool(row.is_fast_path),
        "metadata": row.meta,
    }


def _route_row_filter(cand: dict) -> bool:
    """Drop GOAP-denylisted ops (REST/fast-path-only tools, GoapAgent surface)."""
    return (cand["plugin_id"], cand["operation_id"]) not in GOAP_CANDIDATE_DENYLIST


_ROUTE_SEARCH_SPEC = SearchSpec(
    name="routes",
    model=RouteEmbedding,
    select_cols=(
        RouteEmbedding.plugin_id,
        RouteEmbedding.operation_id,
        RouteEmbedding.path,
        RouteEmbedding.path_template,
        RouteEmbedding.method,
        RouteEmbedding.description,
        RouteEmbedding.parameters,
        RouteEmbedding.is_fast_path,
        RouteEmbedding.meta,
    ),
    embedding_col=RouteEmbedding.embedding,
    model_col=RouteEmbedding.model,
    search_doc_col=RouteEmbedding.search_doc,
    key_fn=lambda c: (c["plugin_id"], c["operation_id"]),
    row_to_dict=_route_row_to_dict,
)


def _route_extra_filters(stmt):
    return stmt.where(RouteEmbedding.is_enabled.is_(True))


async def hybrid_search_routes(query: str, top_k: int = 3) -> list[dict]:
    """Route candidate search via the shared hybrid/dense search engine.

    Kept as a named entry point (rather than inlining into ``search_routes``)
    since ``hybrid_search_routes`` is referenced directly elsewhere (tests,
    docs) as "the route search implementation".
    """
    from core.llm_config_service import resolve_route_embedding

    sel = await resolve_route_embedding()
    return await _engine_search(
        _ROUTE_SEARCH_SPEC,
        query,
        sel,
        top_k,
        extra_filters=_route_extra_filters,
        row_filter=_route_row_filter,
    )


async def search_routes(query: str, top_k: int = 3) -> list[dict]:
    """Embed query → cosine similarity → top-k route candidates with scores.

    Returns dicts shaped for ``core_graph`` consumers: each candidate carries
    ``plugin_id``, ``is_fast_path``, and parsed ``parameters`` so the planner
    and executor can route fast-path vs dynamic without an extra DB hop.
    """
    return await hybrid_search_routes(query, top_k)


async def list_enabled_routes(plugin_id: str | None = None) -> list[dict]:
    """Return all semantically-enabled routes as candidate dicts (no embedding).

    Powers the ``discover_tools`` catalog: scans ``route_embeddings`` live each
    call so plugin hot-reloads surface immediately. Distinct rows per
    (plugin, operation) — collapses the per-model duplication. Optional
    ``plugin_id`` filter narrows to one plugin.
    """
    async with get_async_session() as session:
        stmt = (
            select(
                RouteEmbedding.plugin_id,
                RouteEmbedding.operation_id,
                RouteEmbedding.path,
                RouteEmbedding.path_template,
                RouteEmbedding.method,
                RouteEmbedding.description,
                RouteEmbedding.parameters,
                RouteEmbedding.is_fast_path,
                RouteEmbedding.meta,
            )
            .where(RouteEmbedding.is_enabled.is_(True))
            .distinct(RouteEmbedding.plugin_id, RouteEmbedding.operation_id)
            .order_by(RouteEmbedding.plugin_id, RouteEmbedding.operation_id)
        )
        if plugin_id:
            stmt = stmt.where(RouteEmbedding.plugin_id == plugin_id)
        result = await session.execute(stmt)
        routes: list[dict] = []
        for row in result.all():
            if (row.plugin_id, row.operation_id) in GOAP_CANDIDATE_DENYLIST:
                continue
            params = row.parameters or (row.meta or {}).get("parameters", {}) or {}
            routes.append({
                "plugin_id": row.plugin_id,
                "operation_id": row.operation_id,
                "path": row.path,
                "path_template": row.path_template or row.path,
                "method": row.method,
                "description": row.description,
                "score": 0.0,
                "parameters": params,
                "is_fast_path": bool(row.is_fast_path),
                "metadata": row.meta,
            })
        return routes


async def get_route_count() -> int:
    """Return the number of indexed routes."""
    async with get_async_session() as session:
        stmt = select(func.count()).select_from(RouteEmbedding)
        result = await session.execute(stmt)
        return result.scalar() or 0


async def is_index_empty() -> bool:
    """Check if route_embeddings table is empty."""
    return (await get_route_count()) == 0


async def get_indexed_operation_ids() -> set[str]:
    """Return the set of operation_ids already present in route_embeddings."""
    async with get_async_session() as session:
        stmt = select(RouteEmbedding.operation_id)
        result = await session.execute(stmt)
        return {row.operation_id for row in result.all()}


async def upsert_route_embeddings_bulk(  # noqa: ARG001
    openapi_path: str | None = None,
    batch_size: int = 50,
    only_operation_ids: set[str] | None = None,
) -> int:
    """DEPRECATED — bulk indexing is now owned by ``embedding_worker``.

    Returns 0 and logs a one-time warning. The worker drains ``embedding_jobs``
    on its own schedule; explicit bulk-index calls are no longer needed.
    """
    logger.warning(
        "upsert_route_embeddings_bulk() is a no-op since core_014; "
        "the async embedding_worker handles indexing automatically."
    )
    return 0

async def existing_route_hashes(model_id: str | None = None) -> dict[tuple[str, str], str]:
    """Map (plugin_id, operation_id) → content_hash currently in the DB for the specified model."""
    async with get_async_session() as session:
        if model_id:
            rows = (await session.execute(
                text("""
                    SELECT plugin_id, operation_id, content_hash
                    FROM route_embeddings
                    WHERE embedding IS NOT NULL AND model = :model_id
                """),
                {"model_id": model_id}
            )).all()
        else:
            rows = (await session.execute(
                text("""
                    SELECT plugin_id, operation_id, content_hash
                    FROM route_embeddings
                    WHERE embedding IS NOT NULL
                """)
            )).all()
    return {(r.plugin_id, r.operation_id): r.content_hash for r in rows}


# REST/fast-path-only tools that must never enter the GOAP candidate pool.
# Unioned with DB is_enabled=FALSE so a missing/recreated row cannot re-surface.
GOAP_CANDIDATE_DENYLIST: set[tuple[str, str]] = {
    ("portfolio_plugin", "portfolio_plugin__generate_layout_for_query"),
    # Discovery/index rebuild are operator tools — never visitor GOAP steps.
    ("portfolio_plugin", "portfolio_plugin__discover_portfolio_context"),
    ("portfolio_plugin", "portfolio_plugin__rebuild_portfolio_index"),
    ("portfolio_plugin", "portfolio_plugin__ingest_portfolio_context"),
    ("portfolio_plugin", "portfolio_plugin__reconcile_portfolio_projects"),
    # Bake/patch are operator writes (mint/mutate public ?j= layouts) — not GOAP.
    # Keep in sync with _GOAP_HIDDEN_OPS in plugins/portfolio_plugin/plugin_config.py.
    ("portfolio_plugin", "portfolio_plugin__bake_portfolio_for_job"),
    ("portfolio_plugin", "portfolio_plugin__patch_job_layout"),
    ("portfolio_plugin", "portfolio_plugin__list_bake_runs"),
    ("portfolio_plugin", "portfolio_plugin__list_ask_turns"),
    # Ask-mode tools are dispatched only by the portfolio_ask_v1 FlowSpec (and
    # its synthetic specialist/<flow_id> op). Letting the planner pick them
    # ad-hoc would reintroduce the ungoverned patching this flow replaces.
    ("portfolio_plugin", "portfolio_plugin__route_portfolio_ask"),
    ("portfolio_plugin", "portfolio_plugin__build_ask_overlay"),
    ("portfolio_plugin", "portfolio_plugin__start_context_discovery"),
    ("portfolio_plugin", "portfolio_plugin__get_context_discovery"),
    ("portfolio_plugin", "portfolio_plugin__await_context_discovery"),
    ("portfolio_plugin", "portfolio_plugin__ensure_ask_answer"),
    # Artifact retrieval is a host/CLI follow-up; never a GOAP plan step.
    ("core_graph", "list_artifacts"),
    ("core_graph", "get_artifact"),
    ("core_graph", "fetch_artifact"),
    # GoapAgent surface is MCP-only (node/CLI orchestration). Never a GOAP action.
    ("GoapAgent", "GoapAgent_list_nodes"),
    ("GoapAgent", "GoapAgent_create_session"),
    ("GoapAgent", "GoapAgent_get_state"),
    ("GoapAgent", "GoapAgent_destroy_session"),
    ("GoapAgent", "GoapAgent_invoke_node"),
    ("GoapAgent", "GoapAgent_submit_confirm"),
    ("GoapAgent", "GoapAgent_submit_clarify"),
    ("GoapAgent", "GoapAgent_run_cli_agent"),
    ("GoapAgent", "GoapAgent_list_cli_drivers"),
    ("GoapAgent", "GoapAgent_turn_init"),
    ("GoapAgent", "GoapAgent_triage"),
    ("GoapAgent", "GoapAgent_chat_node"),
    ("GoapAgent", "GoapAgent_decompose"),
    ("GoapAgent", "GoapAgent_embedder"),
    ("GoapAgent", "GoapAgent_planner"),
    ("GoapAgent", "GoapAgent_context_check"),
    ("GoapAgent", "GoapAgent_step_resolver"),
    ("GoapAgent", "GoapAgent_confirm_node"),
    ("GoapAgent", "GoapAgent_clarify_node"),
    ("GoapAgent", "GoapAgent_permission_gate"),
    ("GoapAgent", "GoapAgent_builder"),
    ("GoapAgent", "GoapAgent_wait_node"),
    ("GoapAgent", "GoapAgent_executor"),
    ("GoapAgent", "GoapAgent_validator"),
    ("GoapAgent", "GoapAgent_retry_node"),
    ("GoapAgent", "GoapAgent_step_dispatcher"),
    ("GoapAgent", "GoapAgent_summary_node"),
    ("GoapAgent", "GoapAgent_goap_goal"),
}


async def disabled_route_keys() -> set[tuple[str, str]]:
    """Return (plugin_id, operation_id) pairs that must stay out of GOAP search."""
    async with get_async_session() as session:
        rows = (await session.execute(
            text("""
                SELECT plugin_id, operation_id
                FROM route_embeddings
                WHERE is_enabled = FALSE
            """)
        )).all()
    return {(r.plugin_id, r.operation_id) for r in rows} | GOAP_CANDIDATE_DENYLIST
