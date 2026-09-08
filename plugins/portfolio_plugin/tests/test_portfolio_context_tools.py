"""
Unit tests for Portfolio Plugin context tools (fetch_external_context, get_project_context).

Coverage:
- dispatch: unknown kind → error; notion without MCP proxy → not_configured; url/gdoc id extraction
- notion page body via proxy notion-fetch (no REST NOTION_API_KEY)
- github: meta + readme parsed; token header set when key present, absent otherwise
- truncation honoured (max_chars)
- all kinds construct client via _safe_async_client (patched)
- get_project_context: mock store row with 2 sources → both resolved in order
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from plugins.portfolio_plugin.MCPTools.context_tools import (
    fetch_external_context,
    fetch_repo_insight,
    get_project_context,
    list_owned_repos,
    _extract_notion_id,
    _extract_gdoc_id,
    _insight_cache,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_resp(status_code: int, text: str = "", json_data: dict | None = None, headers: dict | None = None):
    """Build a minimal mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = {"content-type": "application/json", **(headers or {})}
    resp.json = MagicMock(return_value=json_data or {})
    return resp


class _FakeAsyncClient:
    """Context-manager fake for _safe_async_client that records calls."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self._calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def get(self, url: str, **kwargs):
        self._calls.append(("GET", url, kwargs))
        return self._responses.pop(0)


# ── dispatch ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_kind_returns_error():
    """Unknown kind should return status=error listing supported kinds."""
    res = await fetch_external_context(kind="ftp", ref="example.com")
    assert res["status"] == "error"
    assert res["error"] == "unsupported_kind"
    assert "ftp" in res["message"]
    for k in ("github", "url", "notion", "gdoc"):
        assert k in res["message"]


@pytest.mark.asyncio
async def test_missing_kind_returns_error():
    res = await fetch_external_context(kind="", ref="something")
    assert res["status"] == "error"
    assert res["error"] == "missing_required_fields"


@pytest.mark.asyncio
async def test_missing_ref_returns_error():
    res = await fetch_external_context(kind="url", ref="")
    assert res["status"] == "error"
    assert res["error"] == "missing_required_fields"


# ── Notion id/url extraction ──────────────────────────────────────────────────

def test_extract_notion_id_from_url():
    url = "https://www.notion.so/My-Page-abc12345678901234567890123456789"
    page_id = _extract_notion_id(url)
    # Should be 32 hex chars formatted as 8-4-4-4-12
    parts = page_id.split("-")
    assert len(parts) == 5
    assert len(parts[0]) == 8


def test_extract_notion_id_passthrough_bare():
    """Bare ID string without UUID chars passes through unchanged."""
    bare = "abc123"
    assert _extract_notion_id(bare) == bare


def test_extract_gdoc_id_from_url():
    url = "https://docs.google.com/document/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit"
    doc_id = _extract_gdoc_id(url)
    assert doc_id == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"


def test_extract_gdoc_id_passthrough_bare():
    bare = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"
    assert _extract_gdoc_id(bare) == bare


# ── notion without mounted MCP proxy → not_configured ────────────────────────

@pytest.mark.asyncio
async def test_notion_not_configured_without_proxy():
    """Without a Notion MCP proxy mount, kind=notion returns not_configured (no REST key path)."""
    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._find_notion_proxy_op",
        return_value=None,
    ):
        res = await fetch_external_context(kind="notion", ref="abc12345678901234567890123456789")
    assert res["status"] == "not_configured"
    assert res["kind"] == "notion"
    assert res.get("error") == "proxy_not_mounted"


@pytest.mark.asyncio
async def test_notion_fetch_via_mcp_proxy():
    """Notion page body is loaded through invoke_proxy(notion-fetch), not REST."""
    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._find_notion_proxy_op",
        return_value=("proxy_Notion-AndrewDev", "notion-fetch"),
    ), patch(
        "plugins.portfolio_plugin.discovery.sources.invoke_proxy",
        new_callable=AsyncMock,
        return_value={"markdown": "# Whiskers Agent\n\nMCP platform notes."},
    ) as mock_invoke:
        res = await fetch_external_context(
            kind="notion",
            ref="3352783c-aabd-801d-9a05-ecdf106def00",
        )
    assert res["status"] == "ok"
    assert "Whiskers Agent" in res["content"]
    assert res["via"] == "proxy_Notion-AndrewDev/notion-fetch"
    mock_invoke.assert_awaited_once()


# ── github: meta + readme; token header ──────────────────────────────────────

@pytest.mark.asyncio
async def test_github_meta_and_readme_with_token():
    """GitHub fetch with token: Authorization header set; meta and readme returned."""
    repo_json = {
        "full_name": "CooLguNxDD/CatPortfolio",
        "description": "Cat portfolio",
        "topics": ["typescript", "react"],
        "stargazers_count": 42,
        "language": "TypeScript",
        "homepage": "https://example.com",
        "pushed_at": "2026-07-01T00:00:00Z",
    }
    readme_text = "# CatPortfolio\n\nA cool portfolio."

    fake_client = _FakeAsyncClient([
        _make_resp(200, json_data=repo_json),
        _make_resp(200, text=readme_text),
    ])

    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._resolve_key",
        new_callable=AsyncMock,
    ) as mock_key, \
    patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._safe_async_client",
        return_value=fake_client,
    ):
        mock_key.return_value = "ghp_test_token"
        res = await fetch_external_context(kind="github", ref="CooLguNxDD/CatPortfolio", use=["meta", "readme"])

    assert res["status"] == "ok"
    assert res["kind"] == "github"
    assert res["meta"]["full_name"] == "CooLguNxDD/CatPortfolio"
    assert res["meta"]["stars"] == 42
    assert res["content"] == readme_text
    assert res["truncated"] is False

    # First call (repo meta) should carry Authorization header
    first_call_kwargs = fake_client._calls[0][2]
    assert "Authorization" in first_call_kwargs.get("headers", {})
    assert "ghp_test_token" in first_call_kwargs["headers"]["Authorization"]


@pytest.mark.asyncio
async def test_github_no_token_no_auth_header():
    """Without token, Authorization header must NOT be sent."""
    repo_json = {
        "full_name": "CooLguNxDD/CatPortfolio",
        "description": None,
        "topics": [],
        "stargazers_count": 0,
        "language": None,
        "homepage": None,
        "pushed_at": None,
    }

    fake_client = _FakeAsyncClient([
        _make_resp(200, json_data=repo_json),
        _make_resp(200, text="readme"),
    ])

    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._resolve_key",
        new_callable=AsyncMock,
    ) as mock_key, \
    patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._safe_async_client",
        return_value=fake_client,
    ):
        mock_key.return_value = None
        res = await fetch_external_context(kind="github", ref="CooLguNxDD/CatPortfolio")

    assert res["status"] == "ok"
    first_call_kwargs = fake_client._calls[0][2]
    assert "Authorization" not in first_call_kwargs.get("headers", {})


@pytest.mark.asyncio
async def test_github_meta_only_skips_readme():
    """use=['meta'] must not make a second request for the README."""
    repo_json = {
        "full_name": "CooLguNxDD/OpenCat-Mcp-Full",
        "description": "OCT",
        "topics": [],
        "stargazers_count": 1,
        "language": "Python",
        "homepage": None,
        "pushed_at": "2026-07-01T00:00:00Z",
    }

    fake_client = _FakeAsyncClient([_make_resp(200, json_data=repo_json)])

    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._resolve_key",
        new_callable=AsyncMock,
    ) as mock_key, \
    patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._safe_async_client",
        return_value=fake_client,
    ):
        mock_key.return_value = None
        res = await fetch_external_context(kind="github", ref="CooLguNxDD/OpenCat-Mcp-Full", use=["meta"])

    assert res["status"] == "ok"
    assert res["content"] is None
    assert len(fake_client._calls) == 1  # only meta request made


# ── truncation ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_truncation_honored_for_url():
    """Content longer than max_chars is truncated and truncated flag is True."""
    long_content = "A" * 500

    fake_client = _FakeAsyncClient([_make_resp(200, text=long_content)])

    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._safe_async_client",
        return_value=fake_client,
    ):
        res = await fetch_external_context(kind="url", ref="https://example.com", max_chars=100)

    assert res["status"] == "ok"
    assert len(res["content"]) == 100
    assert res["truncated"] is True


@pytest.mark.asyncio
async def test_no_truncation_when_within_limit():
    short_content = "Hello world"

    fake_client = _FakeAsyncClient([_make_resp(200, text=short_content)])

    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._safe_async_client",
        return_value=fake_client,
    ):
        res = await fetch_external_context(kind="url", ref="https://example.com", max_chars=12000)

    assert res["truncated"] is False
    assert res["content"] == short_content


# ── _safe_async_client used for all kinds ─────────────────────────────────────

@pytest.mark.asyncio
async def test_url_kind_uses_safe_async_client():
    """url kind must construct the httpx client via _safe_async_client."""
    fake_client = _FakeAsyncClient([_make_resp(200, text="content")])

    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._safe_async_client",
        return_value=fake_client,
    ) as mock_safe:
        await fetch_external_context(kind="url", ref="https://example.com")

    mock_safe.assert_called_once()


@pytest.mark.asyncio
async def test_gdoc_kind_uses_safe_async_client():
    """gdoc kind must construct the httpx client via _safe_async_client."""
    fake_client = _FakeAsyncClient([_make_resp(200, text="doc content")])

    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._safe_async_client",
        return_value=fake_client,
    ) as mock_safe:
        await fetch_external_context(kind="gdoc", ref="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms")

    mock_safe.assert_called_once()


@pytest.mark.asyncio
async def test_github_kind_uses_safe_async_client():
    """github kind must construct the httpx client via _safe_async_client."""
    repo_json = {
        "full_name": "CooLguNxDD/CatPortfolio", "description": None, "topics": [],
        "stargazers_count": 0, "language": None, "homepage": None, "pushed_at": None,
    }
    fake_client = _FakeAsyncClient([
        _make_resp(200, json_data=repo_json),
        _make_resp(200, text="readme"),
    ])

    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._resolve_key",
        new_callable=AsyncMock, return_value=None,
    ), patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._safe_async_client",
        return_value=fake_client,
    ) as mock_safe:
        await fetch_external_context(kind="github", ref="CooLguNxDD/CatPortfolio")

    mock_safe.assert_called()


# ── allowlist / insight / owned repos ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_github_allowlist_denies_foreign_repo():
    """Out-of-allowlist GitHub refs return repo_not_allowed without HTTP."""
    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._resolve_key",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._safe_async_client",
    ) as mock_safe:
        res = await fetch_external_context(kind="github", ref="torvalds/linux", use=["meta"])

    assert res["status"] == "error"
    assert res["error"] == "repo_not_allowed"
    assert "allowed_owners" in res
    mock_safe.assert_not_called()


@pytest.mark.asyncio
async def test_github_allowlist_allows_configured_owner():
    """CooLguNxDD/* is allowed via manifest allowlist / hero links."""
    repo_json = {
        "full_name": "CooLguNxDD/OpenCat-Mcp-Full",
        "description": "OCT",
        "topics": ["mcp"],
        "stargazers_count": 1,
        "language": "Python",
        "homepage": None,
        "pushed_at": "2026-07-01T00:00:00Z",
    }
    fake_client = _FakeAsyncClient([_make_resp(200, json_data=repo_json)])

    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._resolve_key",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._safe_async_client",
        return_value=fake_client,
    ):
        res = await fetch_external_context(
            kind="github", ref="CooLguNxDD/OpenCat-Mcp-Full", use=["meta"]
        )

    assert res["status"] == "ok"
    assert res["meta"]["full_name"] == "CooLguNxDD/OpenCat-Mcp-Full"


@pytest.mark.asyncio
async def test_fetch_repo_insight_aggregates_with_failing_facet():
    """Insight returns ok with per-facet errors when one endpoint fails."""
    _insight_cache.clear()
    repo_json = {
        "full_name": "CooLguNxDD/CatPortfolio",
        "description": "portfolio",
        "topics": ["react"],
        "stargazers_count": 3,
        "language": "TypeScript",
        "homepage": None,
        "pushed_at": "2026-07-01T00:00:00Z",
        "default_branch": "main",
    }
    # Order of concurrent gets is nondeterministic — use a smart fake.
    class _InsightClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, url: str, **kwargs):
            if url.endswith("/CatPortfolio") and "/git/" not in url and "/commits" not in url:
                return _make_resp(200, json_data=repo_json)
            if url.endswith("/languages"):
                return _make_resp(200, json_data={"TypeScript": 80, "CSS": 20})
            if "/commits" in url:
                return _make_resp(500, text="boom")  # failing facet
            if "/releases/latest" in url:
                return _make_resp(404, text="none")
            if "/readme" in url:
                return _make_resp(200, text="# Hello")
            if "/git/trees/" in url:
                return _make_resp(200, json_data={"tree": [{"path": "src", "type": "tree"}]})
            return _make_resp(404, text="nope")

    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._resolve_key",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._safe_async_client",
        return_value=_InsightClient(),
    ):
        res = await fetch_repo_insight(ref="CooLguNxDD/CatPortfolio")

    assert res["status"] == "ok"
    assert res["meta"]["full_name"] == "CooLguNxDD/CatPortfolio"
    assert res["languages"]["TypeScript"] == 80.0
    assert res["readme"] == "# Hello"
    assert "commits" in (res.get("errors") or {})


@pytest.mark.asyncio
async def test_fetch_repo_insight_cache_hit():
    """Second identical insight call is served from the TTL cache."""
    _insight_cache.clear()
    repo_json = {
        "full_name": "CooLguNxDD/CatPortfolio",
        "description": "x",
        "topics": [],
        "stargazers_count": 0,
        "language": None,
        "homepage": None,
        "pushed_at": None,
        "default_branch": "main",
    }
    call_count = {"n": 0}

    class _CountingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, url: str, **kwargs):
            call_count["n"] += 1
            if url.endswith("/languages"):
                return _make_resp(200, json_data={"Python": 1})
            if "/commits" in url:
                return _make_resp(200, json_data=[])
            if "/releases/latest" in url:
                return _make_resp(404, text="")
            if "/readme" in url:
                return _make_resp(200, text="r")
            if "/git/trees/" in url:
                return _make_resp(200, json_data={"tree": []})
            return _make_resp(200, json_data=repo_json)

    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._resolve_key",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._safe_async_client",
        return_value=_CountingClient(),
    ):
        first = await fetch_repo_insight(ref="CooLguNxDD/CatPortfolio", use=["meta"])
        second = await fetch_repo_insight(ref="CooLguNxDD/CatPortfolio", use=["meta"])

    assert first["cache"] == "miss"
    assert second["cache"] == "hit"
    assert call_count["n"] >= 1
    # second call must not open a new client get for the same key
    first_calls = call_count["n"]
    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._resolve_key",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._safe_async_client",
        return_value=_CountingClient(),
    ):
        third = await fetch_repo_insight(ref="CooLguNxDD/CatPortfolio", use=["meta"])
    assert third["cache"] == "hit"
    assert call_count["n"] == first_calls  # no new HTTP on pure cache hit path


@pytest.mark.asyncio
async def test_list_owned_repos_not_configured():
    """Without GITHUB_TOKEN, list_owned_repos returns not_configured."""
    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools._resolve_key",
        new_callable=AsyncMock,
        return_value=None,
    ):
        res = await list_owned_repos()
    assert res["status"] == "not_configured"


@pytest.mark.asyncio
async def test_fetch_repo_insight_denies_foreign_repo():
    res = await fetch_repo_insight(ref="torvalds/linux")
    assert res["status"] == "error"
    assert res["error"] == "repo_not_allowed"


# ── get_project_context ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_project_context_two_sources():
    """get_project_context resolves both context_sources in order and returns them."""
    project_row = {
        "slug": "catportfolio",
        "name": "CatPortfolio",
        "context_sources": [
            {"id": "cat-repo", "kind": "github", "ref": "CooLguNxDD/CatPortfolio", "use": ["meta"]},
            {"id": "cat-url", "kind": "url", "ref": "https://example.com"},
        ],
    }

    github_result = {"status": "ok", "kind": "github", "ref": "CooLguNxDD/CatPortfolio", "meta": {}, "content": None, "truncated": False}
    url_result = {"status": "ok", "kind": "url", "ref": "https://example.com", "content": "page", "truncated": False}

    call_results = [github_result, url_result]
    call_index = {"i": 0}

    async def fake_fetch(kind, ref, use=None, max_chars=12000):
        res = dict(call_results[call_index["i"]])
        call_index["i"] += 1
        return res

    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools.get_project",
        new_callable=AsyncMock,
        return_value=project_row,
    ), patch(
        "plugins.portfolio_plugin.MCPTools.context_tools.current_tenant_id",
    ) as mock_tid, patch(
        "plugins.portfolio_plugin.MCPTools.context_tools.fetch_external_context",
        side_effect=fake_fetch,
    ):
        mock_tid.get.return_value = 1
        res = await get_project_context(slug="catportfolio")

    assert res["status"] == "ok"
    assert res["slug"] == "catportfolio"
    assert len(res["sources"]) == 2
    assert res["sources"][0]["source_id"] == "cat-repo"
    assert res["sources"][1]["source_id"] == "cat-url"


@pytest.mark.asyncio
async def test_get_project_context_not_found():
    """get_project_context returns error when project slug is unknown."""
    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools.get_project",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "plugins.portfolio_plugin.MCPTools.context_tools.current_tenant_id",
    ) as mock_tid:
        mock_tid.get.return_value = 1
        res = await get_project_context(slug="nonexistent")

    assert res["status"] == "error"
    assert res["error"] == "project_not_found"


@pytest.mark.asyncio
async def test_get_project_context_blank_slug():
    res = await get_project_context(slug="  ")
    assert res["status"] == "error"
    assert res["error"] == "missing_required_fields"


@pytest.mark.asyncio
async def test_get_project_context_empty_sources():
    """Project with no context_sources returns ok with empty sources list."""
    project_row = {"slug": "empty", "name": "Empty", "context_sources": []}

    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools.get_project",
        new_callable=AsyncMock,
        return_value=project_row,
    ), patch(
        "plugins.portfolio_plugin.MCPTools.context_tools.current_tenant_id",
    ) as mock_tid:
        mock_tid.get.return_value = 1
        res = await get_project_context(slug="empty")

    assert res["status"] == "ok"
    assert res["sources"] == []
