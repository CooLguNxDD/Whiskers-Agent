"""Graph response envelope sanitize / finalize.

Extracted from ``core_graph.mcp_tool`` so discovery, invocation, and envelope
shaping can evolve independently. ``mcp_tool`` re-exports these symbols for
back-compat (tests monkeypatch ``core_graph.mcp_tool._sanitize_envelope`` etc.).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from utils.server_config import ENVELOPE_BUDGET_BYTES as _ENVELOPE_BUDGET

logger = logging.getLogger("whiskers")

_TRUNCATED_REF = {
    "_truncated": True,
    "hint": "response too large; use pagination / narrower query / fetch by id",
}
# Fields that must always survive intact — they are what the chat client and
# downstream logic read; they are also small and prose-only.
_ENVELOPE_SACRED = frozenset({
    "summary", "message", "content", "carry", "layout",
    "status", "steps_executed", "step_count", "gate_decision",
    "confidence", "selected_route",
    # Small MinIO offload ref list (short_id/kind/bytes) — never strip.
    "artifacts",
    # Small {node_id: model_name} shorthand derived from model_audit — never strip.
    "model_used",
    # Ask-mode overlay — CatPortfolio merges these; never strip or truncate.
    "blocks",
    "patched_block_ids",
    "focus_slug",
    "highlight_slugs",
    "dag",
    "pending_job",
    "flow_id",
    "goal_class",
    "recommendations",
})


def _model_used_shorthand(model_audit: Any) -> dict[str, str]:
    """Collapse ``state['model_audit']`` into ``{node_id: model_name}``.

    One entry per node that ran a role ladder this turn; later entries win
    (a node visited more than once across goal-loop rounds reflects its most
    recent model). Escalated ladders report the model that actually won,
    not the failed cheap rung — full per-rung detail stays in ``model_audit``
    for callers that want it (currently the admin playground only).
    """
    out: dict[str, str] = {}
    if not isinstance(model_audit, list):
        return out
    for entry in model_audit:
        if not isinstance(entry, dict):
            continue
        node = entry.get("node")
        model = entry.get("model")
        if node and model:
            out[node] = model
    return out

# Shape applied to list carrier fields before falling back to truncation.
# Full 11-step pipeline (offload_minio → strip → CSV) so large leaves hit
# MinIO before dense format; matches the API/tool response_shape path.
_ENVELOPE_COMPRESSION_SHAPE: dict = {
    "normalize": False,
    "offload_minio": True,
    "strip_base64": True,
    "strip_hex": True,
    "strip_empty": True,
    "limit": 20,
    "response_format": "csv",
    "include_meta": False,
}


async def _compress_carrier(value: list) -> object:
    """Run a list carrier field through the full async 11-step pipeline → CSV.

    Uses ``apply_shape_async`` so ``offload_minio`` can capture large leaves
    before strip/CSV. Returns the compressed value, or the original list on
    any error so the caller can still fall back to the truncation sentinel.
    """
    try:
        from utils.response_shape import apply_shape_async

        compressed = await apply_shape_async(
            value,
            _ENVELOPE_COMPRESSION_SHAPE,
            tool_name="envelope_compress",
        )
        # Offload may wrap {_meta, data} when refs present without include_meta.
        if isinstance(compressed, dict) and "data" in compressed and "_meta" in compressed:
            return compressed["data"]
        return compressed
    except Exception as exc:
        logger.debug("envelope compress failed (non-fatal): %s", exc)
        return value  # compression failed — caller falls through to sentinel


def _tools_to_csv(tools: list[dict]) -> str:
    """Flatten discovery tool cards → CSV (reuses utils.response_format.to_csv).

    Joins list fields (required_params, tags) with ';'. Drops the duplicate
    'description' column (== summary) to trim width.
    """
    from utils.response_format import to_csv
    flat = []
    for t in tools:
        row = dict(t)
        row.pop("description", None)  # == summary, redundant column
        for k in ("required_params", "tags"):
            v = row.get(k)
            if isinstance(v, list):
                row[k] = ";".join(str(x) for x in v)
        flat.append(row)
    return to_csv(flat)


async def _sanitize_envelope(out: dict, max_bytes: int = _ENVELOPE_BUDGET) -> dict:
    """Compress and soft-cap carrier fields so the envelope fits within
    *max_bytes* of UTF-8 JSON, guaranteeing a JSON-valid response.

    **Primary strategy**: run list fields through the full async 11-step
    ``response_shape`` pipeline (offload_minio → strip → CSV). A multi-MB
    Jules payload offloads patches then compresses to dense CSV.

    **Fallback strategy** (if still over budget after compression): replace
    the field with a compact ``{_truncated, item_count, hint}`` reference.

    The envelope is returned **unchanged** when it is already within budget
    (zero overhead on the common case).

    Sacred fields (``summary``, ``message``, ``carry``, ``layout``, …) are
    **never** touched.

    Pipeline order when over budget:
    1. Compress ``data`` via offload + strip/limit + CSV.
    2. Compress ``step_results`` via offload + strip/limit + CSV.
    3. If still over budget: replace ``data`` with the truncation sentinel.
    4. If still over budget: replace ``step_results`` with the sentinel.
    5. Last resort: replace remaining non-sacred fields with the sentinel.
    """
    try:
        size = len(json.dumps(out, default=str).encode())
    except Exception as exc:
        logger.debug("envelope size measure failed: %s", exc)
        return out  # unparseable — return as-is, let FastMCP handle it

    if size <= max_bytes:
        return out  # fast path: nothing to do

    out = dict(out)  # shallow copy so we don't mutate the caller's dict

    # --- Step 1: compress `data` list via full async shape pipeline -------------
    original_count: int | None = None
    if isinstance(out.get("data"), list):
        original_count = len(out["data"])
        out["data"] = await _compress_carrier(out["data"])
        try:
            size = len(json.dumps(out, default=str).encode())
        except Exception as exc:
            logger.debug("envelope size remeasure skipped: %s", exc)

    if size <= max_bytes:
        return out

    # --- Step 2: compress `step_results` list via full async shape pipeline -----
    original_count_sr: int | None = None
    if isinstance(out.get("step_results"), list):
        original_count_sr = len(out["step_results"])
        out["step_results"] = await _compress_carrier(out["step_results"])
        try:
            size = len(json.dumps(out, default=str).encode())
        except Exception as exc:
            logger.debug("envelope size remeasure skipped: %s", exc)

    if size <= max_bytes:
        return out

    # --- Step 3: sentinel fallback for `data` ------------------------------------
    if not (isinstance(out.get("data"), dict) and out["data"].get("_truncated")):
        out["data"] = dict(_TRUNCATED_REF, item_count=original_count if original_count is not None else 0)
        try:
            size = len(json.dumps(out, default=str).encode())
        except Exception as exc:
            logger.debug("envelope size remeasure skipped: %s", exc)

    if size <= max_bytes:
        return out

    # --- Step 4: sentinel fallback for `step_results` ----------------------------
    if not (isinstance(out.get("step_results"), dict) and out["step_results"].get("_truncated")):
        out["step_results"] = dict(_TRUNCATED_REF, item_count=original_count_sr if original_count_sr is not None else 0)
        try:
            size = len(json.dumps(out, default=str).encode())
        except Exception as exc:
            logger.debug("envelope size remeasure skipped: %s", exc)

    if size <= max_bytes:
        return out

    # --- Step 5: last resort — replace remaining non-sacred oversized fields ----
    for field in list(out.keys()):
        if field in _ENVELOPE_SACRED:
            continue
        out[field] = dict(_TRUNCATED_REF)
        try:
            size = len(json.dumps(out, default=str).encode())
        except Exception as exc:
            logger.debug("envelope size remeasure skipped: %s", exc)
        if size <= max_bytes:
            break

    return out


async def _finalize_graph_response(result: dict | None) -> dict[str, Any]:
    """Extract the graph's final envelope from its end state.

    The dynamic graph parks its output on ``response``; fall back to a summary
    built from ``selected`` if no response was produced.

    The returned envelope is passed through :func:`_sanitize_envelope` (async
    11-step compress with MinIO offload) to guarantee it fits within FastMCP's
    500 KB response cap (avoiding destructive byte-truncation that produces
    invalid JSON).
    """
    result = result or {}
    response = result.get("response")
    summary = result.get("summary")
    artifacts = result.get("artifacts")
    model_used = _model_used_shorthand(result.get("model_audit"))
    if response:
        if not isinstance(response, dict):
            return {"status": "error", "message": str(response)}
        out = dict(response)
        if summary:
            if not out.get("summary"):
                out["summary"] = summary
            if not out.get("message"):
                out["message"] = summary
        # Prefer state-level artifacts if response omitted them (sacred refs).
        if not out.get("artifacts") and artifacts:
            try:
                from core_graph.node.artifact_offload import _public_artifact_refs
                out["artifacts"] = _public_artifact_refs(
                    artifacts if isinstance(artifacts, list) else []
                )
            except Exception as exc:
                logger.debug("Failed to build public artifact refs, using raw: %s", exc, exc_info=True)
                out["artifacts"] = artifacts
        # Prefer slim step_results for data when response still has fat bodies.
        step_results = result.get("step_results")
        if (
            isinstance(step_results, list)
            and out.get("steps_executed") is not None
            and step_results
        ):
            out["data"] = (
                step_results if len(step_results) > 1 else step_results[0]
            )
        # A step_dispatcher "recover"/"context_check" leg can terminate the run on a
        # stale per-step failure (goap_goal.get_terminal_response salvages carry.layout
        # from working_memory but doesn't touch status/message) — if a usable layout
        # made it through, the turn produced real output; don't report it as failed.
        layout = out.get("layout")
        if not isinstance(layout, dict):
            carry = out.get("carry")
            layout = carry.get("layout") if isinstance(carry, dict) else None
        if isinstance(layout, dict) and out.get("status") == "error":
            out["status"] = "ok"
            if summary:
                out["message"] = summary
        if model_used:
            out["model_used"] = model_used
        return await _sanitize_envelope(out)

    selected = result.get("selected")
    selected = selected if isinstance(selected, dict) else {}
    fallback = {
        "status": "ok",
        "gate_decision": result.get("gate_decision"),
        "confidence": result.get("confidence"),
        "selected_route": (
            f"{selected.get('method', '')} "
            f"{selected.get('path') or selected.get('path_template', '')}"
        ).strip() or None,
    }
    if result.get("summary"):
        fallback["summary"] = result["summary"]
    if result.get("step_results"):
        fallback["step_results"] = result["step_results"]
    if artifacts:
        try:
            from core_graph.node.artifact_offload import _public_artifact_refs
            fallback["artifacts"] = _public_artifact_refs(
                artifacts if isinstance(artifacts, list) else []
            )
        except Exception as exc:
            logger.debug("Failed to build public artifact refs, using raw: %s", exc, exc_info=True)
            fallback["artifacts"] = artifacts
    if model_used:
        fallback["model_used"] = model_used
    return await _sanitize_envelope(fallback)



def is_oneshot_envelope(payload: dict[str, Any] | None) -> bool:
    """Heuristic: oneshot CLI result has status + message and usually meta.cli."""
    if not isinstance(payload, dict):
        return False
    meta = payload.get("meta")
    if isinstance(meta, dict) and "cli" in meta:
        return True
    # Error/unavailable envelopes without full graph state
    if payload.get("status") in ("ok", "error", "unavailable") and "user_query" not in payload:
        if "message" in payload or "summary" in payload:
            return True
    return False
