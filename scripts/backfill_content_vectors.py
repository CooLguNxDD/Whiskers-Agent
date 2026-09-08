#!/usr/bin/env python3
"""Backfill content_vectors from known sources (placeholder).

Usage:
  python scripts/backfill_content_vectors.py --source docs --dry-run
  python scripts/backfill_content_vectors.py --source jobs --collection job_postings

Real iteration/upsert is TODO(track2); --dry-run always exits 0 after printing plan.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("whiskers.backfill_content_vectors")

_SOURCE_DEFAULT_COLLECTION = {
    "catlobster": "catlobster",
    "jobs": "job_postings",
    "docs": "docs",
}


async def backfill(args: argparse.Namespace) -> None:
    """Iterate source records into content_vectors (not yet implemented)."""
    raise NotImplementedError(
        "TODO(track2): iterate source records → add_content_vector"
    )


def main() -> int:
    """CLI entrypoint for content vector backfill."""
    parser = argparse.ArgumentParser(
        description="Backfill content_vectors from a named source (placeholder)."
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=sorted(_SOURCE_DEFAULT_COLLECTION.keys()),
        help="Logical source to iterate",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="Target collection namespace (default derived from --source)",
    )
    parser.add_argument(
        "--tenant-id",
        type=int,
        default=1,
        help="Tenant id to stamp on rows (default: 1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan only; do not write",
    )
    args = parser.parse_args()
    collection = args.collection or _SOURCE_DEFAULT_COLLECTION[args.source]

    logger.info(
        "backfill plan: source=%s collection=%s tenant_id=%s dry_run=%s",
        args.source,
        collection,
        args.tenant_id,
        args.dry_run,
    )

    if args.dry_run:
        print(
            f"dry-run: would backfill source={args.source!r} "
            f"collection={collection!r} tenant_id={args.tenant_id}"
        )
        return 0

    try:
        asyncio.run(backfill(args))
    except NotImplementedError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
