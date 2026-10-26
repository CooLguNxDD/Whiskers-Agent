#!/usr/bin/env python3
"""Re-embed stored vectors into a new embedding identity (provider:model:dims).

Dry-run is the default: it prints per-collection counts and makes zero embed calls
and zero writes. Nothing is written without --execute. Tenant scope is mandatory.

Usage:
  python scripts/reindex_embeddings.py --tenant-id 1
  python scripts/reindex_embeddings.py --tenant-id 1 --execute --batch-size 16 --concurrency 2
  python scripts/reindex_embeddings.py --all-tenants --collection memory --execute
  python scripts/reindex_embeddings.py --tenant-id 1 --execute --resume-after 4200

Exit codes: 0 ok (or dry-run), 1 a batch failed, 2 refused (bad scope / dimension guard).
Runbook: docs/embedding-reindex.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("whiskers.reindex_embeddings")

MAX_CONCURRENCY = 8


def build_parser() -> argparse.ArgumentParser:
    """Argument parser; importing it needs no DB or provider config."""
    p = argparse.ArgumentParser(
        description="Dry-run-first, resumable, tenant-scoped re-embed to a new embedding identity.",
    )
    scope = p.add_mutually_exclusive_group(required=True)
    scope.add_argument("--tenant-id", type=int, help="Tenant whose vectors are re-embedded")
    scope.add_argument("--all-tenants", action="store_true", help="Iterate known tenants one at a time")
    p.add_argument("--collection", action="append", dest="collections", metavar="NAME",
                   help="Vector table to process: memory | search (repeatable; default both)")
    p.add_argument("--batch-size", type=int, default=16, help="Texts per embed call (default 16)")
    p.add_argument("--concurrency", type=int, default=2,
                   help=f"In-flight batches (default 2, max {MAX_CONCURRENCY})")
    p.add_argument("--limit", type=int, default=None, help="Max rows per collection per tenant this run")
    p.add_argument("--resume-after", type=int, default=None, metavar="ID",
                   help="Continue after this primary key (cursor printed by a previous run)")
    p.add_argument("--execute", action="store_true",
                   help="Actually embed and write (absent = dry-run: counts only)")
    p.add_argument("--target-provider", default=None, help="Default: active embedding selection")
    p.add_argument("--target-model", default=None, help="Default: active embedding selection")
    p.add_argument("--target-dimensions", type=int, default=None, help="Default: active embedding selection")
    return p


async def _target_selection(args: argparse.Namespace) -> dict:
    """Active selection (what live writers/searchers use), overridden by --target-* flags."""
    from core.llm_config_service import resolve_tool_embedding

    sel = dict(await resolve_tool_embedding("plugins.search_plugin", "semantic_index"))
    if args.target_provider and args.target_provider.lower() != (sel.get("provider") or "").lower():
        for k in ("base_url", "api_key", "source", "model", "dimensions"):
            sel.pop(k, None)
    if args.target_provider:
        sel["provider"] = args.target_provider.lower()
    if args.target_model:
        sel["model"] = args.target_model
    if args.target_dimensions:
        sel["dimensions"] = args.target_dimensions
    return sel


def _print_plan(tenant_id: int, target: str, plans) -> None:
    print(f"\ntenant {tenant_id} -> target identity {target}")
    cols = ("collection", "total", "already_target", "stale", "no_text", "superseded", "column_width", "note")
    print("  " + "  ".join(f"{c:<14}" for c in cols))
    for plan in plans:
        row = plan.as_dict()
        print("  " + "  ".join(f"{str(row[c]):<14}" for c in cols))


async def _run(args: argparse.Namespace) -> int:
    from db_layer.embedding_reindex_store import SqlReindexStore
    from db_layer.embeddings.reindex import ReindexError, run_reindex

    store = SqlReindexStore()
    sel = await _target_selection(args)
    tenants = await store.list_tenants() if args.all_tenants else [args.tenant_id]
    reports, exit_code = [], 0
    for tenant_id in tenants:
        try:
            report = await run_reindex(
                store, sel, tenant_id,
                collections=args.collections, batch_size=args.batch_size,
                concurrency=args.concurrency, dry_run=not args.execute,
                limit=args.limit, resume_after=args.resume_after,
            )
        except ReindexError as exc:
            logger.error("tenant %s refused: %s", tenant_id, exc)
            reports.append({"tenant_id": tenant_id, "refused": str(exc)})
            exit_code = max(exit_code, 2)
            continue
        _print_plan(tenant_id, report.target_identity, report.plan)
        reports.append(report.as_dict())
        if not report.ok:
            exit_code = max(exit_code, 1)
    print(json.dumps({"dry_run": not args.execute, "exit_code": exit_code, "tenants": reports}, indent=2))
    return exit_code


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.tenant_id is not None and args.tenant_id < 1:
        parser.error("--tenant-id must be a positive integer")
    if args.batch_size < 1:
        parser.error("--batch-size must be >= 1")
    if not 1 <= args.concurrency <= MAX_CONCURRENCY:
        parser.error(f"--concurrency must be between 1 and {MAX_CONCURRENCY}")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")
    from db_layer.embeddings.reindex import REINDEX_COLLECTIONS
    for name in args.collections or []:
        if name not in REINDEX_COLLECTIONS:
            parser.error(f"--collection must be one of {list(REINDEX_COLLECTIONS)}")
    try:
        return asyncio.run(_run(args))
    except ValueError as exc:
        logger.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
