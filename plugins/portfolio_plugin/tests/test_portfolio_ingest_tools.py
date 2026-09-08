"""MCP ingest tools unit tests."""
import inspect
from unittest.mock import AsyncMock, patch

import pytest

from plugins.portfolio_plugin.discovery.normalize import chunk_text


def test_chunk_text():
    chunks = chunk_text("a" * 25000, max_chars=10000)
    assert len(chunks) == 3
    assert all(len(c) <= 10000 for c in chunks)


def test_ingest_tools_have_no_tenant_id_kwarg():
    """Caller-supplied tenant_id is a cross-tenant write risk — params removed."""
    from plugins.portfolio_plugin.MCPTools.ingest_tools import (
        ingest_portfolio_context,
        reconcile_portfolio_projects,
    )

    for fn in (ingest_portfolio_context, reconcile_portfolio_projects):
        params = inspect.signature(fn).parameters
        assert "tenant_id" not in params, f"{fn.__name__} must not accept tenant_id"


@pytest.mark.asyncio
async def test_ingest_portfolio_context_text():
    from plugins.portfolio_plugin.MCPTools.ingest_tools import ingest_portfolio_context

    captured = {}

    async def fake_index(docs, **kwargs):
        captured["docs"] = docs
        captured["tenant_id"] = kwargs.get("tenant_id")
        return {"status": "ok", "indexed": len(docs), "deduped": 0, "replaced": 0, "errors": []}

    with patch(
        "plugins.portfolio_plugin.MCPTools.ingest_tools.index_docs",
        new=AsyncMock(side_effect=fake_index),
    ), patch(
        # Patch the bound name in ingest_tools (from-import), not the tenant module.
        "plugins.portfolio_plugin.MCPTools.ingest_tools.require_tenant_id",
        return_value=1,
    ):
        res = await ingest_portfolio_context(
            docs=[
                {
                    "text": "Helix AI contribution report with substantial content.",
                    "slug_hint": "helix-ai",
                    "title": "Helix AI",
                    "tags": ["ai", "Helix"],
                }
            ],
        )
    assert res["status"] == "ok"
    assert captured["docs"]
    assert captured["tenant_id"] == 1
    assert captured["docs"][0].slug_hint == "helix-ai"
    assert "(1/" not in captured["docs"][0].title


@pytest.mark.asyncio
async def test_ingest_fails_closed_without_tenant():
    from plugins.portfolio_plugin.MCPTools.ingest_tools import ingest_portfolio_context

    with patch(
        "plugins.portfolio_plugin.MCPTools.ingest_tools.require_tenant_id",
        return_value=None,
    ):
        res = await ingest_portfolio_context(
            docs=[{"text": "hello world content", "slug_hint": "x"}],
        )
    assert res["status"] == "error"
    assert res["error"] == "tenant_unresolved"


@pytest.mark.asyncio
async def test_reconcile_fails_closed_without_tenant():
    from plugins.portfolio_plugin.MCPTools.ingest_tools import reconcile_portfolio_projects

    with patch(
        "plugins.portfolio_plugin.MCPTools.ingest_tools.require_tenant_id",
        return_value=None,
    ):
        res = await reconcile_portfolio_projects(dry_run=True)
    assert res["status"] == "error"
    assert res["error"] == "tenant_unresolved"


def test_path_sandbox_rejects_cwd_style_escape(tmp_path, monkeypatch):
    """Ingest path sandbox must not allow arbitrary monorepo/cwd files."""
    from plugins.portfolio_plugin.MCPTools import ingest_tools

    # Point allowlist only at a fixture dir under the plugin package shape.
    fixture = tmp_path / "safe"
    fixture.mkdir()
    allowed = fixture / "ok.txt"
    allowed.write_text("safe body", encoding="utf-8")
    denied = tmp_path / "secret.env"
    denied.write_text("SECRET=1", encoding="utf-8")

    monkeypatch.setattr(
        ingest_tools,
        "_path_allowlist_roots",
        lambda: [fixture],
    )
    assert ingest_tools._safe_path(str(allowed)) is not None
    assert ingest_tools._safe_path(str(denied)) is None
