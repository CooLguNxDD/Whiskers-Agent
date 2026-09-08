"""Unit tests for portfolio discovery indexing helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from plugins.portfolio_plugin.discovery.index import (
    CONTEXT_COLLECTION,
    index_docs,
    is_fresh,
    newest_indexed_at,
)
from plugins.portfolio_plugin.discovery.normalize import ContextDoc
from plugins.portfolio_plugin.discovery.pipeline import run_discovery


def test_context_collection_name():
    assert CONTEXT_COLLECTION == "portfolio_plugin__context"


@pytest.mark.asyncio
async def test_index_docs_dedupe_and_count():
    docs = [
        ContextDoc(
            source="github",
            ref="a/b",
            kind="github",
            title="b",
            text="hello world content",
            slug_hint="b",
            tags=[],
        )
    ]
    with patch(
        "db_layer.search_content_vectors_store.add_search_content_vector",
        new_callable=AsyncMock,
        side_effect=[{"status": "ok", "id": 1}, {"status": "ok", "deduped": True}],
    ) as add:
        r1 = await index_docs(docs, tenant_id=1)
        r2 = await index_docs(docs, tenant_id=1)
    assert r1["indexed"] == 1
    assert r2["deduped"] == 1
    assert add.await_count == 2
    # tenant required
    assert add.await_args.kwargs.get("tenant_id") == 1 or add.await_args[1].get("tenant_id") == 1


def test_is_fresh_and_newest():
    now = datetime.now(timezone.utc)
    hits = [
        {
            "metadata": {
                "indexed_at": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            }
        }
    ]
    assert is_fresh(hits, freshness_s=7200) is True
    assert is_fresh(hits, freshness_s=10) is False
    assert newest_indexed_at([]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("index_on_dry_run, should_index", [(False, False), (True, True)])
async def test_run_discovery_dry_run_index_opt_in_never_writes_projects(
    index_on_dry_run, should_index
):
    fake_resolved = []
    with (
        patch(
            "plugins.portfolio_plugin.discovery.pipeline.resolve_sources",
            return_value=fake_resolved,
        ),
        patch(
            "plugins.portfolio_plugin.discovery.pipeline.fetch_all_sources",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "plugins.portfolio_plugin.discovery.pipeline.normalize_source_results",
            new_callable=AsyncMock,
            return_value=[
                ContextDoc(
                    source="github",
                    ref="a/b",
                    kind="github",
                    title="b",
                    text="desc",
                    slug_hint="b",
                    tags=[],
                )
            ],
        ),
        patch(
            "plugins.portfolio_plugin.store.list_projects",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "plugins.portfolio_plugin.discovery.pipeline.index_docs",
            new_callable=AsyncMock,
        ) as idx,
        patch(
            "plugins.portfolio_plugin.discovery.pipeline.apply_reconcile",
            new_callable=AsyncMock,
            return_value={"status": "ok"},
        ) as reconcile,
    ):
        result = await run_discovery(
            dry_run=True,
            do_index=True,
            index_on_dry_run=index_on_dry_run,
            tenant_id=1,
        )
    assert result["status"] == "ok"
    assert result["dry_run"] is True
    assert result["doc_count"] == 1
    assert result["reconcile_plan"]
    if should_index:
        idx.assert_awaited_once()
    else:
        idx.assert_not_awaited()
    reconcile.assert_awaited_once()
    assert reconcile.await_args.kwargs["write_back"] is False


@pytest.mark.asyncio
async def test_one_failing_source_does_not_fail_run():
    from plugins.portfolio_plugin.discovery.sources import fetch_all_sources
    from plugins.portfolio_plugin.discovery.resolver import ResolvedSource

    sources = [
        ResolvedSource(
            capability="github.repos.list",
            plugin_id=None,
            operation_id=None,
            local_tool="list_owned_repos",
            input_schema={},
            args_template={},
        ),
        ResolvedSource(
            capability="notion.pages.search",
            plugin_id="proxy_Notion-x",
            operation_id="notion-search",
            local_tool=None,
            input_schema={},
            args_template={"query": "x"},
        ),
    ]

    async def _local(name, args):
        return {"status": "ok", "repos": [], "count": 0}

    async def _proxy(pid, oid, args):
        return {"status": "error", "message": "notion down"}

    with (
        patch(
            "plugins.portfolio_plugin.discovery.sources.invoke_local_tool",
            new_callable=AsyncMock,
            side_effect=_local,
        ),
        patch(
            "plugins.portfolio_plugin.discovery.sources.invoke_proxy",
            new_callable=AsyncMock,
            side_effect=_proxy,
        ),
    ):
        results = await fetch_all_sources(sources, budget_s=5)
    assert len(results) == 2
    statuses = {r["capability"]: r["status"] for r in results}
    assert statuses["github.repos.list"] == "ok"
    assert statuses["notion.pages.search"] == "error"
