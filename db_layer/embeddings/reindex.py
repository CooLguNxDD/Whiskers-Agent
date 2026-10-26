"""Dry-run-first, resumable, tenant-scoped re-embed of stored vectors.

A vector row's identity is ``provider:model:dims`` (``model_id_for``). Moving to a
new embedding model means every stale row must be re-embedded from its source text
and rewritten with the new vector *and* identity. Search filters on the identity
strictly (``search_engine.py``), so this module never mixes spaces in one row.

All SQL lives behind the narrow ``ReindexStore`` Protocol (SQLAlchemy
implementation: ``db_layer/embedding_reindex_store.py``) so the logic here is
testable with an in-memory fake.

Collections are the two vector tables, named after their ``SearchSpec``
(``memory`` -> ``memory_content_vectors``, ``search`` -> ``search_content_vectors``).
``route_embeddings`` is deliberately absent: it has no tenant column and is rebuilt
by the embedding worker (see ``docs/embedding-reindex.md``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, Sequence

logger = logging.getLogger("whiskers.reindex")

REINDEX_COLLECTIONS: tuple[str, ...] = ("memory", "search")
MAX_CONCURRENCY = 8

EmbedFn = Callable[[dict, list[str]], Awaitable[list[list[float]]]]


class ReindexError(RuntimeError):
    """Refusal to run (bad scope or dimension guard); raised before any write."""


@dataclass(frozen=True)
class StaleRow:
    """One re-embeddable row: primary key plus its source text."""

    id: int
    text: str


class ReindexStore(Protocol):
    """Everything the re-index needs from storage; every call is tenant-scoped."""

    async def list_tenants(self) -> list[int]:
        """Known tenant ids that own vector rows (NULL tenant excluded)."""

    async def vector_width(self, collection: str) -> int | None:
        """Declared pgvector column width, or None when unconstrained."""

    async def count_rows(self, collection: str, tenant_id: int, target_identity: str) -> dict[str, int]:
        """Return {total, already_target, stale, no_text, superseded} for one tenant."""

    async def fetch_stale(
        self, collection: str, tenant_id: int, target_identity: str, after_id: int | None, limit: int
    ) -> list[StaleRow]:
        """Stale rows with source text, ``id > after_id``, ascending by id (keyset page)."""

    async def update_batch(
        self,
        collection: str,
        tenant_id: int,
        target_identity: str,
        items: Sequence[tuple[int, list[float]]],
    ) -> int:
        """Write vector + identity for the batch in ONE statement; return rows updated."""


@dataclass
class CollectionPlan:
    """Per-collection counts for one tenant."""

    collection: str
    total: int = 0
    already_target: int = 0
    stale: int = 0
    no_text: int = 0
    superseded: int = 0
    column_width: int | None = None
    dimension_ok: bool = True
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class FailedBatch:
    """A batch that did not complete; counts and ids only, never content."""

    collection: str
    first_id: int
    last_id: int
    rows: int
    error: str


@dataclass
class CollectionResult:
    """Outcome of re-embedding one collection."""

    collection: str
    embedded: int = 0
    updated: int = 0
    batches: int = 0
    failed: list[FailedBatch] = field(default_factory=list)
    skipped: str = ""
    cursor: int | None = None


@dataclass
class ReindexReport:
    """Run summary: plan, results and the per-collection resume cursors."""

    tenant_id: int
    target_identity: str
    dry_run: bool
    plan: list[CollectionPlan] = field(default_factory=list)
    results: list[CollectionResult] = field(default_factory=list)

    @property
    def failed_batches(self) -> int:
        return sum(len(r.failed) for r in self.results)

    @property
    def ok(self) -> bool:
        return self.failed_batches == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "target_identity": self.target_identity,
            "dry_run": self.dry_run,
            "ok": self.ok,
            "failed_batches": self.failed_batches,
            "plan": [p.as_dict() for p in self.plan],
            "results": [
                {
                    "collection": r.collection,
                    "embedded": r.embedded,
                    "updated": r.updated,
                    "batches": r.batches,
                    "skipped": r.skipped,
                    "cursor": r.cursor,
                    "failed": [f.__dict__ for f in r.failed],
                }
                for r in self.results
            ],
        }


def _require_tenant(tenant_id: Any) -> int:
    """Reject None/non-int so a missing tenant can never drop the WHERE clause."""
    if tenant_id is None or isinstance(tenant_id, bool) or not isinstance(tenant_id, int):
        raise ValueError("tenant_id is required (an int); refusing to run unscoped")
    return tenant_id


def _resolve_collections(collections: Sequence[str] | None) -> list[str]:
    names = list(collections) if collections else list(REINDEX_COLLECTIONS)
    unknown = [c for c in names if c not in REINDEX_COLLECTIONS]
    if unknown:
        raise ValueError(f"unknown collection(s) {unknown}; choose from {list(REINDEX_COLLECTIONS)}")
    return list(dict.fromkeys(names))


def identity_dimensions(target_identity: str) -> int:
    """Width encoded in a ``provider:model:dims`` identity."""
    try:
        dims = int(target_identity.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        raise ValueError("target identity must look like provider:model:dims") from None
    if dims < 1:
        raise ValueError("target identity width must be positive")
    return dims


async def plan_reindex(
    store: ReindexStore,
    target_identity: str,
    tenant_id: int,
    collections: Sequence[str] | None = None,
) -> list[CollectionPlan]:
    """Count total / already_target / stale / no_text per collection (read-only)."""
    tenant_id = _require_tenant(tenant_id)
    dims = identity_dimensions(target_identity)
    plans: list[CollectionPlan] = []
    for name in _resolve_collections(collections):
        counts = await store.count_rows(name, tenant_id, target_identity)
        width = await store.vector_width(name)
        plan = CollectionPlan(
            collection=name,
            total=counts.get("total", 0),
            already_target=counts.get("already_target", 0),
            stale=counts.get("stale", 0),
            no_text=counts.get("no_text", 0),
            superseded=counts.get("superseded", 0),
            column_width=width,
            dimension_ok=width is None or width == dims,
        )
        if not plan.dimension_ok:
            plan.note = f"refused: target width {dims} != column width {width}"
        elif plan.no_text:
            plan.note = "skipped: no source text"
        plans.append(plan)
    return plans


async def _default_embed(sel: dict, texts: list[str]) -> list[list[float]]:
    # Document prefixing happens inside embed_documents_with; never add one here.
    from db_layer.embeddings.embeddings_core import embed_documents_with

    return await embed_documents_with(sel, texts)


async def _reindex_collection(
    store: ReindexStore,
    embed_fn: EmbedFn,
    sel: dict,
    target_identity: str,
    dims: int,
    tenant_id: int,
    collection: str,
    batch_size: int,
    concurrency: int,
    limit: int | None,
    resume_after: int | None,
) -> CollectionResult:
    result = CollectionResult(collection=collection, cursor=resume_after)
    sem = asyncio.Semaphore(concurrency)
    remaining = limit

    async def run_batch(rows: list[StaleRow]) -> None:
        async with sem:
            try:
                vectors = await embed_fn(sel, [r.text for r in rows])
                if len(vectors) != len(rows):
                    raise ValueError("embedding count != batch size")
                if any(len(v) != dims for v in vectors):
                    raise ValueError(f"embedding width != target width {dims}")
                result.embedded += len(rows)
                updated = await store.update_batch(
                    collection, tenant_id, target_identity, [(r.id, v) for r, v in zip(rows, vectors)]
                )
                result.updated += updated
            except Exception as exc:  # noqa: BLE001 - recorded, run continues
                result.failed.append(
                    FailedBatch(collection, rows[0].id, rows[-1].id, len(rows), type(exc).__name__)
                )
                logger.warning(
                    "reindex batch failed collection=%s tenant=%s rows=%d error=%s",
                    collection, tenant_id, len(rows), type(exc).__name__,
                )
            finally:
                result.batches += 1

    while remaining is None or remaining > 0:
        window = batch_size * concurrency
        if remaining is not None:
            window = min(window, remaining)
        page = await store.fetch_stale(collection, tenant_id, target_identity, result.cursor, window)
        if not page:
            break
        batches = [page[i : i + batch_size] for i in range(0, len(page), batch_size)]
        await asyncio.gather(*(run_batch(b) for b in batches))
        result.cursor = page[-1].id
        if remaining is not None:
            remaining -= len(page)
        if len(page) < window:
            break
    return result


async def run_reindex(
    store: ReindexStore,
    sel: dict,
    tenant_id: int,
    *,
    collections: Sequence[str] | None = None,
    batch_size: int = 16,
    concurrency: int = 2,
    dry_run: bool = True,
    limit: int | None = None,
    resume_after: int | None = None,
    embed_fn: EmbedFn | None = None,
) -> ReindexReport:
    """Re-embed stale rows of ``tenant_id`` into the identity of ``sel``.

    ``dry_run`` (the default) only plans: zero embed calls, zero writes. Executing
    refuses up front when the target width differs from a column width. Stale rows
    are keyset-paginated by id and rewritten (vector + identity) one statement per
    batch, so a rerun skips finished rows and ``resume_after`` continues a cursor.
    A failed batch is recorded and the run continues; check ``report.ok``.
    """
    from db_layer.embeddings.embeddings_core import model_id_for

    tenant_id = _require_tenant(tenant_id)
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError(f"concurrency must be between 1 and {MAX_CONCURRENCY}")
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")

    target = model_id_for(sel)
    dims = identity_dimensions(target)
    names = _resolve_collections(collections)
    report = ReindexReport(tenant_id=tenant_id, target_identity=target, dry_run=dry_run)
    report.plan = await plan_reindex(store, target, tenant_id, names)
    if dry_run:
        return report

    bad = [p for p in report.plan if not p.dimension_ok]
    if bad:
        raise ReindexError(
            "dimension guard: " + "; ".join(f"{p.collection}: {p.note}" for p in bad)
            + " (alter the column via a migration first; nothing was written)"
        )

    embed = embed_fn or _default_embed
    for name in names:
        res = await _reindex_collection(
            store, embed, sel, target, dims, tenant_id, name, batch_size, concurrency, limit, resume_after
        )
        plan = next(p for p in report.plan if p.collection == name)
        if plan.no_text:
            res.skipped = f"{plan.no_text} row(s) skipped: no source text"
        report.results.append(res)
    return report
