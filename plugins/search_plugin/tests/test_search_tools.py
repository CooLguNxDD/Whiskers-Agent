"""Unit tests for the search_tools MCP tools."""

import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from plugins.search_plugin.MCPTools.search_tools import fetch_url, web_search


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_vault(tavily=None, brave=None):
    """Return a mock vault that resolves TAVILY_API_KEY / BRAVE_SEARCH_API_KEY."""

    async def _get(plugin_id, key_name):
        mapping = {
            "TAVILY_API_KEY": tavily,
            "BRAVE_SEARCH_API_KEY": brave,
        }
        return mapping.get(key_name)

    v = MagicMock()
    v.get = AsyncMock(side_effect=_get)
    return v


# ---------------------------------------------------------------------------
# web_search — validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_search_empty_query():
    """web_search with empty query returns missing fields error; no API call made."""
    res = await web_search("")
    assert res == {
        "status": "error",
        "error": "missing_required_fields",
        "missing_fields": ["query"],
        "message": "query is required",
    }


# ---------------------------------------------------------------------------
# web_search — no keys (vault returns None, env not set)
# ---------------------------------------------------------------------------

def _mock_ddgs_client(text_return=None, text_side_effect=None):
    """Build a DDGS-like mock that supports context-manager usage."""
    mock_ddgs = MagicMock()
    mock_ddgs.return_value.__enter__.return_value = mock_ddgs.return_value
    if text_side_effect is not None:
        mock_ddgs.return_value.text.side_effect = text_side_effect
    else:
        mock_ddgs.return_value.text.return_value = text_return or []
    return mock_ddgs


@pytest.mark.asyncio
async def test_web_search_no_keys():
    """web_search with no keys falls back to DuckDuckGo (keyless)."""
    import plugins.search_plugin.MCPTools.search_tools as st_mod

    async def _no_key(_key_name):
        return None

    mock_ddgs = _mock_ddgs_client(
        text_return=[{"title": "Py", "href": "https://python.org", "body": "Python"}]
    )

    with (
        patch.object(st_mod, "_resolve_key", side_effect=_no_key),
        patch.object(st_mod, "DDGS", mock_ddgs),
        patch.object(st_mod, "_DDG_BACKENDS", ("auto",)),
    ):
        res = await web_search("python")

    assert res["status"] == "ok"
    assert res["provider"] == "duckduckgo"
    assert len(res["results"]) == 1
    assert res["results"][0]["url"] == "https://python.org"


# ---------------------------------------------------------------------------
# web_search — DuckDuckGo / provider param
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_search_ddg_normalization():
    """DDG title/href/body maps to title/url/content ok envelope."""
    import plugins.search_plugin.MCPTools.search_tools as st_mod

    mock_ddgs = _mock_ddgs_client(
        text_return=[
            {"title": "A", "href": "https://a.example", "body": "alpha"},
            {"title": "B", "href": "https://b.example", "body": "beta"},
        ]
    )

    with (
        patch.object(st_mod, "DDGS", mock_ddgs),
        patch.object(st_mod, "_DDG_BACKENDS", ("auto",)),
    ):
        res = await web_search("q", provider="duckduckgo")

    assert res == {
        "status": "ok",
        "provider": "duckduckgo",
        "backend": "auto",
        "results": [
            {"title": "A", "url": "https://a.example", "content": "alpha"},
            {"title": "B", "url": "https://b.example", "content": "beta"},
        ],
    }


@pytest.mark.asyncio
async def test_web_search_provider_duckduckgo_forced():
    """Explicit duckduckgo uses DDG and never resolves vault keys."""
    import plugins.search_plugin.MCPTools.search_tools as st_mod

    async def _should_not_resolve(_key_name):
        raise AssertionError("_resolve_key must not be called for provider=duckduckgo")

    mock_ddgs = _mock_ddgs_client(
        text_return=[{"title": "D", "href": "https://d.example", "body": "ddg"}]
    )

    with (
        patch.object(st_mod, "_resolve_key", side_effect=_should_not_resolve),
        patch.object(st_mod, "DDGS", mock_ddgs),
        patch.object(st_mod, "_DDG_BACKENDS", ("auto",)),
        patch("httpx.AsyncClient.post") as mock_post,
    ):
        res = await web_search("q", provider="duckduckgo")

    assert res["status"] == "ok"
    assert res["provider"] == "duckduckgo"
    mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_web_search_provider_tavily_missing_key():
    """Explicit tavily without key returns configuration_error."""
    import plugins.search_plugin.MCPTools.search_tools as st_mod

    async def _no_key(_key_name):
        return None

    with patch.object(st_mod, "_resolve_key", side_effect=_no_key):
        res = await web_search("q", provider="tavily")

    assert res == {
        "status": "error",
        "error": "configuration_error",
        "message": "Set TAVILY_API_KEY",
    }


@pytest.mark.asyncio
async def test_web_search_invalid_provider():
    """Unknown provider returns invalid_provider error."""
    res = await web_search("q", provider="bing")
    assert res == {
        "status": "error",
        "error": "invalid_provider",
        "message": "provider must be one of: auto, tavily, brave, duckduckgo",
    }


@pytest.mark.asyncio
async def test_web_search_ddg_error():
    """DDGS raising on all backends maps to request_failed envelope."""
    import plugins.search_plugin.MCPTools.search_tools as st_mod

    mock_ddgs = _mock_ddgs_client(text_side_effect=RuntimeError("network down"))

    with (
        patch.object(st_mod, "DDGS", mock_ddgs),
        patch.object(st_mod, "_DDG_BACKENDS", ("auto",)),
        patch.object(st_mod.asyncio, "sleep", new_callable=AsyncMock),
    ):
        res = await web_search("q", provider="duckduckgo")

    assert res["status"] == "error"
    assert res["error"] == "request_failed"
    assert "network down" in res["message"]
    assert res["status_code"] == 500


@pytest.mark.asyncio
async def test_web_search_ddgs_not_installed():
    """DDGS is None → configuration_error for missing library."""
    import plugins.search_plugin.MCPTools.search_tools as st_mod

    with patch.object(st_mod, "DDGS", None):
        res = await web_search("q", provider="duckduckgo")

    assert res == {
        "status": "error",
        "error": "configuration_error",
        "message": "ddgs library not installed (pip install ddgs)",
    }


@pytest.mark.asyncio
async def test_web_search_ddg_retries_next_backend_on_empty():
    """Empty first backend falls through to the next backend that has hits."""
    import plugins.search_plugin.MCPTools.search_tools as st_mod

    mock_ddgs = _mock_ddgs_client(
        text_side_effect=[
            [],  # backend a empty
            [{"title": "Hit", "href": "https://hit.example", "body": "ok"}],
        ]
    )

    with (
        patch.object(st_mod, "DDGS", mock_ddgs),
        patch.object(st_mod, "_DDG_BACKENDS", ("a", "b")),
        patch.object(st_mod.asyncio, "sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        res = await web_search("q", provider="duckduckgo")

    assert res["status"] == "ok"
    assert res["backend"] == "b"
    assert res["results"][0]["url"] == "https://hit.example"
    mock_sleep.assert_awaited()


@pytest.mark.asyncio
async def test_web_search_ddg_all_empty_includes_hint():
    """All backends empty → ok with results=[] and reliability hint."""
    import plugins.search_plugin.MCPTools.search_tools as st_mod

    mock_ddgs = _mock_ddgs_client(text_return=[])

    with (
        patch.object(st_mod, "DDGS", mock_ddgs),
        patch.object(st_mod, "_DDG_BACKENDS", ("a", "b")),
        patch.object(st_mod.asyncio, "sleep", new_callable=AsyncMock),
    ):
        res = await web_search("q", provider="duckduckgo")

    assert res["status"] == "ok"
    assert res["provider"] == "duckduckgo"
    assert res["results"] == []
    assert "TAVILY_API_KEY" in res["message"]


# ---------------------------------------------------------------------------
# web_search — vault path (Tavily from vault)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_search_tavily_from_vault():
    """web_search resolves Tavily key from vault and POSTs correctly."""
    import plugins.search_plugin.MCPTools.search_tools as st_mod

    async def _resolve(key_name):
        return "vault_tavily_key" if key_name == "TAVILY_API_KEY" else None

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "answer": "Python is a programming language.",
        "results": [
            {
                "title": "Welcome to Python.org",
                "url": "https://www.python.org/",
                "content": "The official home of the Python Programming Language.",
                "score": 0.99,
            }
        ],
    }

    with (
        patch.object(st_mod, "_resolve_key", side_effect=_resolve),
        patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post,
    ):
        res = await web_search("python", max_results=3)

    assert res["status"] == "ok"
    assert res["provider"] == "tavily"
    assert res["answer"] == "Python is a programming language."
    assert len(res["results"]) == 1
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["api_key"] == "vault_tavily_key"
    assert kwargs["json"]["query"] == "python"
    assert kwargs["json"]["max_results"] == 3


# ---------------------------------------------------------------------------
# web_search — env fallback path (Tavily from env, no vault)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_search_tavily_env_fallback():
    """web_search falls back to TAVILY_API_KEY env var when vault returns None."""
    import plugins.search_plugin.MCPTools.search_tools as st_mod

    # Simulate vault returning None (DB unavailable) → env fallback
    async def _resolve(key_name):
        return os.environ.get(key_name) or None

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"answer": "ok", "results": []}

    with (
        patch.dict(os.environ, {"TAVILY_API_KEY": "env_tavily_key"}),
        patch.object(st_mod, "_resolve_key", side_effect=_resolve),
        patch("httpx.AsyncClient.post", return_value=mock_response),
    ):
        res = await web_search("python")

    assert res["status"] == "ok"
    assert res["provider"] == "tavily"


# ---------------------------------------------------------------------------
# web_search — Brave from vault
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_search_brave_from_vault():
    """web_search uses Brave when only brave key is in vault."""
    import plugins.search_plugin.MCPTools.search_tools as st_mod

    async def _resolve(key_name):
        return "vault_brave_key" if key_name == "BRAVE_SEARCH_API_KEY" else None

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "web": {
            "results": [
                {
                    "title": "Python (programming language) - Wikipedia",
                    "url": "https://en.wikipedia.org/wiki/Python_(programming_language)",
                    "description": "Python is a high-level general-purpose programming language.",
                }
            ]
        }
    }

    with (
        patch.object(st_mod, "_resolve_key", side_effect=_resolve),
        patch("httpx.AsyncClient.get", return_value=mock_response) as mock_get,
    ):
        res = await web_search("python", max_results=7)

    assert res == {
        "status": "ok",
        "provider": "brave",
        "results": [
            {
                "title": "Python (programming language) - Wikipedia",
                "url": "https://en.wikipedia.org/wiki/Python_(programming_language)",
                "content": "Python is a high-level general-purpose programming language.",
            }
        ],
    }
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["q"] == "python"
    assert kwargs["params"]["count"] == 7
    assert kwargs["headers"]["X-Subscription-Token"] == "vault_brave_key"


# ---------------------------------------------------------------------------
# fetch_url
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_url_empty():
    """fetch_url with empty url returns missing fields error."""
    res = await fetch_url("")
    assert res == {
        "status": "error",
        "error": "missing_required_fields",
        "missing_fields": ["url"],
        "message": "url is required",
    }


@pytest.mark.asyncio
async def test_fetch_url_html():
    """fetch_url with HTML response strips tags and truncates correctly."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html><body><h1>Python</h1><p>An easy <b>programming</b> language.</p></body></html>"
    mock_response.headers = {"content-type": "text/html"}

    with patch("httpx.AsyncClient.get", return_value=mock_response) as mock_get:
        # Full stripped: "Python An easy programming language." (length 35)
        # We test with max_chars = 15
        res = await fetch_url("https://example.com", max_chars=15)
        assert res == {
            "status": "ok",
            "url": "https://example.com",
            "content": "Python An easy ",
            "truncated": True,
        }
        mock_get.assert_called_once_with(
            "https://example.com",
            headers={"User-Agent": "WhiskersAgent/1.0"},
        )


@pytest.mark.asyncio
async def test_fetch_url_non_html():
    """fetch_url with non-HTML response preserves formatting and doesn't strip."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "Hello\nWorld! <b>Keep</b> me."
    mock_response.headers = {"content-type": "text/plain"}

    with patch("httpx.AsyncClient.get", return_value=mock_response) as mock_get:
        res = await fetch_url("https://example.com/plain", max_chars=100)
        assert res == {
            "status": "ok",
            "url": "https://example.com/plain",
            "content": "Hello\nWorld! <b>Keep</b> me.",
            "truncated": False,
        }


@pytest.mark.asyncio
async def test_fetch_url_strips_script_style():
    """fetch_url removes <script> and <style> bodies from HTML output (not just tags)."""
    html = (
        "<html><head>"
        "<script>var secret = 1; function foo(){}</script>"
        "<style>.cls { color: red; font-size: 12px; }</style>"
        "</head><body><h1>Visible Title</h1><p>Readable content here.</p></body></html>"
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = html
    mock_response.headers = {"content-type": "text/html; charset=utf-8"}

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        res = await fetch_url("https://example.com/page", max_chars=500)

    assert res["status"] == "ok"
    content = res["content"]
    # Script body must not appear in stripped output
    assert "var secret" not in content
    assert "function foo" not in content
    # Style body must not appear in stripped output
    assert "color: red" not in content
    assert "font-size" not in content
    # Visible text must be present
    assert "Visible Title" in content
    assert "Readable content" in content
