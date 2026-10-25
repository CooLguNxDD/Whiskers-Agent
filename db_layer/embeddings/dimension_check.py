"""Embedding-dimension preflight: configured width vs pgvector column width and a stored sample.

``compare_dimensions`` is pure. ``collect_dimension_facts`` reads schema metadata and the
width of at most one stored vector per table (read-only, ``LIMIT 1``, never row contents),
so it is tenant-agnostic by construction. Callers (``setup.py doctor``, boot health) treat
every issue as a warning, never as a failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

# Tables whose ``embedding`` column the search engine / route index query with the configured width.
EMBEDDING_TABLES: tuple[str, ...] = (
    "memory_content_vectors",
    "search_content_vectors",
    "route_embeddings",
)

REINDEX_HINT = "re-embed to the configured width with scripts/reindex_embeddings.py (see docs/embedding-reindex.md)"

_VECTOR_TYPE_RE = re.compile(r"^vector\((\d+)\)$")

_COLUMN_TYPE_SQL = text(
    "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
    "WHERE attrelid = to_regclass(:t) AND attname = 'embedding' AND NOT attisdropped"
)


@dataclass(frozen=True)
class DimensionIssue:
    """One disagreement between the configured width and a stored one."""

    collection: str
    configured: int
    found: int
    kind: str  # "column" | "sample"
    message: str


def compare_dimensions(
    configured: int,
    column_dims: dict[str, int | None],
    sampled: dict[str, int | None],
) -> list[DimensionIssue]:
    """Return an issue per table whose column or sampled vector width differs from ``configured``.

    ``None`` (table absent, unconstrained column, no stored vector) is ignored.
    """
    issues: list[DimensionIssue] = []
    for kind, facts, what in (
        ("column", column_dims, "pgvector column is vector({found})"),
        ("sample", sampled, "a stored vector has {found} dims"),
    ):
        for collection, found in facts.items():
            if found is None or found == configured:
                continue
            issues.append(
                DimensionIssue(
                    collection=collection,
                    configured=configured,
                    found=found,
                    kind=kind,
                    message=f"{collection}: {what.format(found=found)} but EMBED_DIMENSIONS resolves to {configured}",
                )
            )
    return issues


def _parse_vector_width(type_name: Any) -> int | None:
    """Width from a ``format_type`` string such as ``vector(1024)``; None when unconstrained/other."""
    if not isinstance(type_name, str):
        return None
    m = _VECTOR_TYPE_RE.match(type_name.strip())
    return int(m.group(1)) if m else None


async def collect_dimension_facts(
    session: Any,
) -> tuple[dict[str, int | None], dict[str, int | None]]:
    """Read column widths and one sampled vector width per embedding table (read-only)."""
    columns: dict[str, int | None] = {}
    sampled: dict[str, int | None] = {}
    for table in EMBEDDING_TABLES:
        row = (await session.execute(_COLUMN_TYPE_SQL, {"t": table})).fetchone()
        columns[table] = _parse_vector_width(row[0]) if row else None
        if row is None:
            sampled[table] = None  # absent table: nothing to sample
            continue
        # ``table`` comes from the fixed EMBEDDING_TABLES constant, never from input.
        sample = (
            await session.execute(
                text(f"SELECT vector_dims(embedding) FROM {table} WHERE embedding IS NOT NULL LIMIT 1")
            )
        ).fetchone()
        sampled[table] = int(sample[0]) if sample and sample[0] is not None else None
    return columns, sampled


async def run_dimension_check() -> list[DimensionIssue]:
    """Resolve the configured width, collect facts over a fresh session and compare."""
    from core.embedding_dimensions import embedding_dimensions
    from db_layer.connection import get_async_session

    configured = embedding_dimensions()
    async with get_async_session() as session:
        columns, sampled = await collect_dimension_facts(session)
    return compare_dimensions(configured, columns, sampled)
