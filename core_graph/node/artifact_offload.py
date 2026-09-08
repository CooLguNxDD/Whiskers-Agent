"""Shared MinIO artifact offload for graph nodes (round_summary + summary).

Keeps step_results and the finalize ``response`` envelope in sync so large
tool payloads do not reappear after offload rewrites ``step_results`` alone.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("whiskers")


def _public_artifact_refs(artifacts: list[dict]) -> list[dict[str, Any]]:
    """Strip internal MinIO keys for envelope / tool surfaces."""
    out: list[dict[str, Any]] = []
    for a in artifacts:
        if not isinstance(a, dict) or not a.get("short_id"):
            continue
        sid = a.get("short_id")
        out.append(
            {
                "kind": a.get("kind"),
                "short_id": sid,
                "bytes": a.get("bytes"),
                "content_type": a.get("content_type"),
                "session_id": a.get("session_id"),
                "path": a.get("path") or a.get("source_path"),
                "console_path": a.get("console_path")
                or f"/api/artifacts/session_gated/{sid}",
            }
        )
    return out


def _rebuild_response_data(response: dict | None, step_results: list) -> dict | None:
    """If response was a plan-complete envelope, rebind ``data`` from slim results."""
    if not isinstance(response, dict):
        return response
    # step_dispatcher plan-complete shape: {status, steps_executed, data}
    if response.get("steps_executed") is None and response.get("status") != "partial":
        return response
    results = list(step_results or [])
    data = results if len(results) > 1 else (results[0] if results else {})
    return {**response, "data": data}


async def offload_step_results_for_state(
    state: dict,
    *,
    step_results: list | None = None,
) -> tuple[list, list[dict], dict | None]:
    """Offload large string leaves; return (slim_step_results, artifacts, slim_response).

    Walks both ``step_results`` and the plan-complete ``response`` so fat tool
    bodies cannot hide only on the finalize envelope. Fail-safe on any error.
    """
    from utils.server_config import (
        ARTIFACT_OFFLOAD_ENABLED,
        ARTIFACT_OFFLOAD_MAX_PER_ROUND,
        ARTIFACT_OFFLOAD_MIN_FIELD_BYTES,
    )
    from core.artifact_store.store import collect_string_leaves, extract_large_artifacts

    results = list(step_results if step_results is not None else (state.get("step_results") or []))
    artifacts = list(state.get("artifacts") or [])
    response = state.get("response") if isinstance(state.get("response"), dict) else None

    if not ARTIFACT_OFFLOAD_ENABLED:
        logger.debug("artifact offload disabled")
        return results, artifacts, response

    if not results and not response:
        logger.info("artifact offload: empty step_results/response (skip)")
        return results, artifacts, response

    try:
        from core.context import current_tenant_id

        try:
            tenant_id = int(current_tenant_id.get() or 1)
        except Exception as exc:
            logger.warning("Failed to resolve tenant_id, defaulting to 1: %s", exc, exc_info=True)
            tenant_id = 1
        session_id = state.get("session_id")
        min_bytes = ARTIFACT_OFFLOAD_MIN_FIELD_BYTES
        max_art = ARTIFACT_OFFLOAD_MAX_PER_ROUND

        # Diagnostics: total + max leaf on both carriers
        sr_leaves = collect_string_leaves(results, path="step_results")
        resp_leaves = collect_string_leaves(response, path="response") if response else []
        sr_total = sum(n for _, n in sr_leaves)
        sr_max = max((n for _, n in sr_leaves), default=0)
        resp_total = sum(n for _, n in resp_leaves)
        resp_max = max((n for _, n in resp_leaves), default=0)

        new_refs: list[dict] = []

        if results:
            slim, refs = await extract_large_artifacts(
                results,
                session_id=session_id,
                tenant_id=tenant_id,
                min_bytes=min_bytes,
                max_artifacts=max_art,
                path_root="step_results",
            )
            if refs:
                results = slim if isinstance(slim, list) else results
                new_refs.extend(refs)

        # Prefer rebinding response from slim step_results rather than a second
        # walk of response (same CSV lived under both carriers → duplicate MinIO
        # puts). Only walk response when step_results produced nothing but
        # response still has large leaves.
        if new_refs:
            response = _rebuild_response_data(response, results)
        elif response is not None and resp_max >= max(512, min_bytes // 2):
            slim_resp, refs_r = await extract_large_artifacts(
                response,
                session_id=session_id,
                tenant_id=tenant_id,
                min_bytes=min_bytes,
                max_artifacts=max_art,
                path_root="response",
            )
            if refs_r:
                response = slim_resp if isinstance(slim_resp, dict) else response
                new_refs.extend(refs_r)
                response = _rebuild_response_data(response, results)

        logger.info(
            "artifact offload: session=%s tenant=%s min=%s "
            "sr_total=%s sr_max=%s resp_total=%s resp_max=%s refs=%s",
            session_id,
            tenant_id,
            min_bytes,
            sr_total,
            sr_max,
            resp_total,
            resp_max,
            len(new_refs),
        )

        if new_refs:
            # Dedupe refs by short_id for the envelope list.
            seen_ids: set[str] = set()
            merged: list[dict] = []
            for a in list(artifacts) + new_refs:
                sid = a.get("short_id") if isinstance(a, dict) else None
                if not sid or sid in seen_ids:
                    continue
                seen_ids.add(sid)
                merged.append(a)
            artifacts = merged

        return results, artifacts, response
    except Exception as exc:
        logger.warning(
            "artifact offload failed (fail-safe leave intact): %s", exc, exc_info=True
        )
        return results, artifacts, response
