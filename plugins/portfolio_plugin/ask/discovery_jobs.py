"""Async, inventory-safe discovery jobs for ask-mode misses.

A visitor asking about a project that is not in ``portfolio_projects`` is the
one case where an ask needs live sources. The turn waits on the job (bounded
by ``ask.discovery_budget_s``) and then spawns a virtual fish from the
indexed docs — no ``portfolio_projects`` write, no new ``?j=`` link.

Hard invariants:
  * ``dry_run=True`` and ``write_back=False`` are forced, never read from
    settings — public traffic cannot write project rows
  * ``do_index`` is the one public-traffic write and targets
    ``portfolio_plugin__context`` only (``ask.discovery_index``)
  * one job per normalized query, TTL-capped, bounded job table
  * ``asyncio.wait_for`` on ``discovery_budget_s`` — a slow source expires the
    job instead of leaking a task
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.ask.discovery")

_JOB_TTL_S = 600.0
_MAX_JOBS = 128

# job_id -> {status, query, created_at, projects, error}
_jobs: dict[str, dict[str, Any]] = {}
_by_query: dict[str, str] = {}
_tasks: dict[str, asyncio.Task] = {}


def _ask_settings() -> dict[str, Any]:
    from plugins.portfolio_plugin.plugin_config import SETTINGS

    cfg = SETTINGS.get("ask") if isinstance(SETTINGS, dict) else None
    return dict(cfg) if isinstance(cfg, dict) else {}


def _budget_s() -> float:
    try:
        return max(1.0, min(60.0, float(_ask_settings().get("discovery_budget_s", 20))))
    except (TypeError, ValueError):
        return 20.0


def _index_enabled() -> bool:
    """Whether a public ask may write the context index (never project rows)."""
    return bool(_ask_settings().get("discovery_index", False))


def _tenant_int(raw: Any) -> int:
    """Coerce a context tenant id to int; never raise into a caller."""
    try:
        return int(raw) if raw is not None else 1
    except (TypeError, ValueError):
        return 1


def _drop_job(jid: str) -> None:
    """Remove one job and cancel its background task if still running."""
    _jobs.pop(jid, None)
    task = _tasks.pop(jid, None)
    if task is not None and not task.done():
        task.cancel()


def _evict(now: float) -> None:
    """Drop expired jobs; hard-cap the table."""
    stale = [jid for jid, rec in _jobs.items() if (now - rec["created_at"]) >= _JOB_TTL_S]
    for jid in stale:
        _drop_job(jid)
    if len(_jobs) >= _MAX_JOBS:
        oldest = sorted(_jobs.items(), key=lambda kv: kv[1]["created_at"])
        for jid, _ in oldest[: len(_jobs) - _MAX_JOBS + 1]:
            _drop_job(jid)
    live = set(_jobs)
    for q, jid in list(_by_query.items()):
        if jid not in live:
            _by_query.pop(q, None)


async def _run_job(job_id: str, query: str, tenant_id: int) -> None:
    """Read-only discovery for one query. Never raises into the event loop."""
    from plugins.portfolio_plugin.discovery.pipeline import run_discovery

    try:
        result = await asyncio.wait_for(
            run_discovery(
                scope="all",
                # dry_run / write_back stay forced: a public ask must not be
                # able to write inventory no matter how discovery is configured.
                # do_index is the deliberate exception — it writes the
                # portfolio_plugin__context vector collection only, so a
                # re-ask of the same topic can resolve off the index.
                dry_run=True,
                write_back=False,
                do_index=_index_enabled(),
                # dry_run stays forced True (see above), so indexing needs this
                # explicit opt-in too — run_discovery only indexes under dry_run
                # when both do_index and index_on_dry_run are set. Without it,
                # ask.discovery_index was a dead setting: do_index alone never
                # fires while dry_run=True.
                index_on_dry_run=_index_enabled(),
                tenant_id=_tenant_int(tenant_id),
            ),
            timeout=_budget_s(),
        )
    except asyncio.TimeoutError:
        rec = _jobs.get(job_id)
        if rec is not None:
            rec.update(status="error", error=f"discovery exceeded {_budget_s():.0f}s budget")
        return
    except Exception as exc:
        logger.warning("ask discovery job %s failed: %s", job_id, exc)
        rec = _jobs.get(job_id)
        if rec is not None:
            rec.update(status="error", error=str(exc)[:300])
        return

    docs = result.get("docs") if isinstance(result, dict) else None
    candidates = _candidates_for_query(docs, query)
    rec = _jobs.get(job_id)
    if rec is None:
        return
    rec.update(
        status="ready" if candidates else "empty",
        projects=candidates,
        error="",
    )


def _candidates_for_query(docs: Any, query: str) -> list[dict[str, Any]]:
    """Rank discovered docs against the question — read-only, no persistence."""
    from plugins.portfolio_plugin.compose.job_tailor import _score_blob, job_tokens

    tokens = job_tokens("", "", query)
    if not tokens or not isinstance(docs, list):
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        blob = " ".join(
            str(doc.get(k) or "")
            for k in ("title", "slug_hint", "slug", "summary", "excerpt", "body")
        )
        score = _score_blob(blob, tokens)
        if score > 0:
            scored.append(
                (
                    score,
                    {
                        "slug": str(doc.get("slug") or doc.get("slug_hint") or ""),
                        "name": str(doc.get("title") or ""),
                        "summary": str(doc.get("summary") or doc.get("excerpt") or "")[:600],
                        "ref": str(doc.get("ref") or ""),
                        "source": str(doc.get("source") or ""),
                        "score": round(score, 4),
                    },
                )
            )
    scored.sort(key=lambda t: -t[0])
    return [c for _, c in scored[:5] if c["slug"]]


def start_discovery(query: str, *, tenant_id: int = 1) -> dict[str, Any]:
    """Start (or rejoin) a read-only discovery job. Returns ``{job_id, status}``."""
    q = " ".join(str(query or "").lower().split())
    if not q:
        return {"status": "error", "error": "query required"}
    if not bool(_ask_settings().get("discovery_fallback")):
        return {"status": "disabled", "error": "ask.discovery_fallback is off"}

    now = time.time()
    _evict(now)

    existing = _by_query.get(q)
    if existing and existing in _jobs:
        rec = _jobs[existing]
        if rec["status"] in ("pending", "ready"):
            return {"status": rec["status"], "job_id": existing, "query": q}
        # A dead job (error/empty) must not pin the query for the rest of its
        # TTL — a re-ask deserves a fresh attempt, not the same stale miss.
        _drop_job(existing)
        _by_query.pop(q, None)

    job_id = uuid.uuid4().hex[:16]
    _jobs[job_id] = {
        "status": "pending",
        "query": q,
        "created_at": now,
        "projects": [],
        "error": "",
    }
    _by_query[q] = job_id
    try:
        _tasks[job_id] = asyncio.create_task(_run_job(job_id, q, _tenant_int(tenant_id)))
    except RuntimeError as exc:  # no running loop (sync caller / test)
        _jobs[job_id].update(status="error", error=str(exc)[:200])
    return {"status": "pending", "job_id": job_id, "query": q}


def get_discovery(job_id: str) -> dict[str, Any]:
    """Poll a discovery job. ``pending|ready|empty|error|unknown``."""
    _evict(time.time())
    rec = _jobs.get(str(job_id or "").strip())
    if rec is None:
        return {"status": "unknown", "job_id": job_id, "projects": []}
    return {
        "status": rec["status"],
        "job_id": job_id,
        "query": rec["query"],
        "projects": list(rec["projects"]),
        "error": rec["error"],
    }


async def await_discovery(job_id: str, *, timeout_s: float = 0.0) -> dict[str, Any]:
    """Wait for a discovery job's task, then return its record.

    ``timeout_s <= 0`` uses ``discovery_budget_s``. On timeout the current
    (usually ``pending``) record is returned and the task is left running —
    a late finish still serves the next ask of the same query. Unknown ids
    return ``status: "unknown"`` and never raise.
    """
    jid = str(job_id or "").strip()
    if not jid:
        return get_discovery(job_id)
    timeout = timeout_s if timeout_s and timeout_s > 0 else _budget_s()
    task = _tasks.get(jid)
    if task is not None and not task.done():
        # shield: wait_for would otherwise cancel the job on timeout.
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            return get_discovery(jid)
        except asyncio.CancelledError:
            # The await is shielded, so a CancelledError here means the
            # *caller's* await was cancelled, not the shielded task —
            # propagate rather than swallow so cancellation still works.
            if not task.cancelled():
                raise
        except Exception as exc:
            logger.warning(
                "ask discovery task %s failed while awaiting: %s", jid, exc, exc_info=True
            )
    return get_discovery(jid)


def reset_jobs() -> None:
    """Clear the job table (tests)."""
    _jobs.clear()
    _by_query.clear()
    _tasks.clear()
