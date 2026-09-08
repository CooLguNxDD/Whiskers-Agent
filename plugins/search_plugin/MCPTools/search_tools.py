"""Web search and URL fetch tools for the search_plugin (Track 1)."""

import asyncio
import html.parser
import logging
import os
import re
import time
from typing import Any

from core.context import mcp
from core.proxy.ssrf_safety import _safe_async_client

_PLUGIN_ID = "search_plugin"
logger = logging.getLogger("whiskers.search_plugin")

# Prefer the renamed ``ddgs`` package (multi-engine metasearch). Fall back to
# legacy ``duckduckgo_search`` so older images still boot.
try:
    from ddgs import DDGS  # type: ignore[import-not-found]

    # Engines that still return hits when Bing-only scrapers are rate-limited.
    _DDG_BACKENDS: tuple[str, ...] = ("duckduckgo", "yahoo", "bing", "auto")
except ImportError:
    try:
        from duckduckgo_search import DDGS  # type: ignore[import-not-found, no-redef]

        _DDG_BACKENDS = ("auto", "html", "lite", "bing")
    except ImportError:  # graceful when lib absent (tests patch st_mod.DDGS)
        DDGS = None  # type: ignore[misc, assignment]
        _DDG_BACKENDS = ()

_VALID_PROVIDERS = frozenset({"auto", "tavily", "brave", "duckduckgo"})
# Cap raw HTML fed to the stripper so unbounded bodies cannot burn CPU.
_HTML_STRIP_MAX_INPUT = 32_000
# Empty-result retries: scrapers often return HTTP 200 with zero parseable hits.
_DDG_RETRY_BACKOFF_S = (0.35, 0.7, 1.1)
_DDG_EMPTY_HINT = (
    "Keyless search returned no results (often rate-limited). "
    "Set TAVILY_API_KEY or BRAVE_SEARCH_API_KEY on search_plugin for reliable search."
)


class _HTMLStripper(html.parser.HTMLParser):
    """Stdlib HTML text extractor that drops script/style body content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignore = False

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag.lower() in ("script", "style"):
            self._ignore = True
        elif not self._ignore:
            # Preserve word boundaries between adjacent tags (h1/p, etc.)
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style"):
            self._ignore = False
        elif not self._ignore:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._ignore:
            self._parts.append(data)

    def get_text(self) -> str:
        """Return accumulated visible text with collapsed whitespace."""
        return re.sub(r"\s+", " ", "".join(self._parts)).strip()


def _strip_html_text(text: str, max_input: int = _HTML_STRIP_MAX_INPUT) -> str:
    """Strip HTML tags without catastrophic regex backtracking on script/style."""
    capped = text[:max_input]
    stripper = _HTMLStripper()
    try:
        stripper.feed(capped)
        stripper.close()
        return stripper.get_text()
    except Exception:
        # Fallback: tag strip only on capped input (no script-body regex).
        plain = re.sub(r"<[^>]+>", " ", capped)
        return re.sub(r"\s+", " ", plain).strip()


async def _resolve_key(key_name: str) -> str | None:
    """Resolve a credential key: vault first, then os.environ fallback.

    Vault lookup is skipped gracefully when the DB is unavailable.
    """
    try:
        from core.context import vault  # None when DB not configured
        if vault is not None:
            val = await vault.get(_PLUGIN_ID, key_name)
            if val:
                return val
    except Exception:
        logger.debug("search_tools.py: swallowed exception", exc_info=True)
    return os.environ.get(key_name) or None


def _record_web_search_telemetry(latency_ms: int, ok: bool, error_type: str | None = None) -> None:
    """Best-effort telemetry for the DDG path (mirrors safe_api_call fields)."""
    try:
        from core.telemetry import collector
        kwargs: dict[str, Any] = {
            "tool": "web_search",
            "plugin_id": "search_plugin",
            "model": None,
            "latency_ms": latency_ms,
            "ok": ok,
        }
        if error_type is not None:
            kwargs["error_type"] = error_type
            kwargs["status_code"] = 500
        collector.record_tool_call(**kwargs)
    except Exception as exc:
        logger.warning("web_search telemetry failed: %s", exc)


def _normalize_ddg_hit(row: dict[str, Any]) -> dict[str, Any] | None:
    """Map provider-specific hit fields to the common title/url/content shape."""
    url = row.get("href") or row.get("url") or row.get("link")
    if not url:
        return None
    return {
        "title": row.get("title"),
        "url": url,
        "content": row.get("body") or row.get("content") or row.get("description") or "",
    }


def _ddg_text_sync(query: str, max_results: int, backend: str) -> list[dict[str, Any]]:
    """Blocking DDGS text search for one backend (runs in a worker thread)."""
    client = DDGS()
    kwargs: dict[str, Any] = {"max_results": max_results}
    # Older duckduckgo_search uses backend=; newer ddgs accepts the same kwarg.
    if backend:
        kwargs["backend"] = backend
    # Prefer context-manager path when available (resource cleanup).
    if hasattr(client, "__enter__"):
        with client as ddgs:
            raw = ddgs.text(query, **kwargs)
    else:
        raw = client.text(query, **kwargs)
    return list(raw or [])


async def _search_duckduckgo(query: str, max_results: int) -> dict[str, Any]:
    """Keyless web search via ``ddgs`` / ``duckduckgo_search`` with backend retries.

    Scrapers frequently return HTTP 200 with an empty hit list (rate limits /
    HTML shape changes). Try several backends with short backoff before giving up.
    """
    if DDGS is None:
        return {
            "status": "error",
            "error": "configuration_error",
            "message": "ddgs library not installed (pip install ddgs)",
        }

    t0 = time.perf_counter()
    capped = min(max(1, max_results), 20)
    backends = _DDG_BACKENDS or ("auto",)
    last_exc: Exception | None = None
    any_success_empty = False

    for attempt, backend in enumerate(backends):
        try:
            raw = await asyncio.to_thread(_ddg_text_sync, query, capped, backend)
            results = [
                hit
                for hit in (_normalize_ddg_hit(r) for r in raw)
                if hit is not None
            ]
            if results:
                _record_web_search_telemetry(
                    int((time.perf_counter() - t0) * 1000), ok=True
                )
                return {
                    "status": "ok",
                    "provider": "duckduckgo",
                    "backend": backend,
                    "results": results,
                }
            any_success_empty = True
            logger.info(
                "web_search ddg empty backend=%s attempt=%s query=%r",
                backend,
                attempt,
                query[:80],
            )
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "web_search ddg backend=%s failed: %s", backend, exc
            )

        if attempt < len(backends) - 1:
            delay = _DDG_RETRY_BACKOFF_S[
                min(attempt, len(_DDG_RETRY_BACKOFF_S) - 1)
            ]
            await asyncio.sleep(delay)

    latency_ms = int((time.perf_counter() - t0) * 1000)

    # Every backend raised → hard error (matches prior single-shot behavior).
    if last_exc is not None and not any_success_empty:
        _record_web_search_telemetry(
            latency_ms, ok=False, error_type=type(last_exc).__name__
        )
        return {
            "status": "error",
            "error": "request_failed",
            "message": f"web_search: {last_exc}",
            "status_code": 500,
        }

    # Soft empty: completed searches but no hits (or mixed empty + errors).
    _record_web_search_telemetry(latency_ms, ok=True)
    out: dict[str, Any] = {
        "status": "ok",
        "provider": "duckduckgo",
        "results": [],
        "message": _DDG_EMPTY_HINT,
    }
    if last_exc is not None:
        out["last_error"] = str(last_exc)
    return out


async def _search_tavily(query: str, max_results: int, tavily_key: str) -> dict[str, Any]:
    """Tavily search branch (safe_api_call path)."""

    try:
        async with _safe_async_client(timeout=30) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": tavily_key,
                    "query": query,
                    "max_results": min(max_results, 10),
                    "search_depth": "basic",
                    "include_answer": True,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            results = [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "content": item.get("content"),
                    "score": item.get("score"),
                }
                for item in data.get("results", [])
            ]
            return {
                "status": "ok",
                "provider": "tavily",
                "answer": data.get("answer"),
                "results": results,
            }
    except Exception as exc:
        return {
            "status": "error",
            "error": "api_error",
            "message": str(exc),
            "context": f"web_search: {query}"
        }


async def _search_brave(query: str, max_results: int, brave_key: str) -> dict[str, Any]:
    """Brave Search branch (safe_api_call path)."""

    try:
        async with _safe_async_client(timeout=30) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": min(max_results, 20)},
                headers={"X-Subscription-Token": brave_key, "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

            results = [
                {
                    "title": x.get("title"),
                    "url": x.get("url"),
                    "content": x.get("description"),
                }
                for x in data.get("web", {}).get("results", [])
            ]
            return {
                "status": "ok",
                "provider": "brave",
                "results": results,
            }
    except Exception as exc:
        return {
            "status": "error",
            "error": "api_error",
            "message": str(exc),
            "context": f"web_search: {query}"
        }


@mcp.tool(
    title="web_search",
    tags={"search_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def web_search(
    query: str,
    max_results: int = 5,
    provider: str = "auto",
) -> dict[str, Any]:
    """Search the open internet / search on web by free-text query.

    Preferred tool whenever the user asks to search the web, look up online,
    google something, or research a topic. Returns ranked hits (title, url,
    snippet) via Tavily, Brave, or keyless DuckDuckGo — do **not** substitute
    ``fetch_url`` of a google.com/search or duckduckgo SERP URL.

    ``provider`` is ``auto`` (Tavily key → Brave key → keyless DuckDuckGo),
    or an explicit ``tavily`` / ``brave`` / ``duckduckgo`` force.
    Credentials are resolved lazily only for the provider(s) needed.
    """
    if not query.strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["query"],
            "message": "query is required",
        }

    provider_norm = (provider or "auto").strip().lower()
    if provider_norm not in _VALID_PROVIDERS:
        return {
            "status": "error",
            "error": "invalid_provider",
            "message": "provider must be one of: auto, tavily, brave, duckduckgo",
        }

    if provider_norm == "tavily":
        tavily_key = await _resolve_key("TAVILY_API_KEY")
        if not tavily_key:
            return {
                "status": "error",
                "error": "configuration_error",
                "message": "Set TAVILY_API_KEY",
            }
        return await _search_tavily(query, max_results, tavily_key)

    if provider_norm == "brave":
        brave_key = await _resolve_key("BRAVE_SEARCH_API_KEY")
        if not brave_key:
            return {
                "status": "error",
                "error": "configuration_error",
                "message": "Set BRAVE_SEARCH_API_KEY",
            }
        return await _search_brave(query, max_results, brave_key)

    if provider_norm == "duckduckgo":
        return await _search_duckduckgo(query, max_results)

    # auto: Tavily → Brave → DuckDuckGo (keyless final fallback)
    tavily_key = await _resolve_key("TAVILY_API_KEY")
    if tavily_key:
        return await _search_tavily(query, max_results, tavily_key)
    brave_key = await _resolve_key("BRAVE_SEARCH_API_KEY")
    if brave_key:
        return await _search_brave(query, max_results, brave_key)
    return await _search_duckduckgo(query, max_results)


@mcp.tool(
    title="fetch_url",
    tags={"search_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fetch_url(url: str, max_chars: int = 8000) -> dict[str, Any]:
    """Fetch and extract text from a known article/page URL.

    Not a web search tool — do not invent google.com/search or duckduckgo.com
    URLs. For open-web research use ``web_search`` first, then optionally
    ``fetch_url`` on a concrete result link.
    """
    if not url.strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["url"],
            "message": "url is required",
        }

    try:
        async with _safe_async_client(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "WhiskersAgent/1.0"},
            )
            resp.raise_for_status()

            text = resp.text
            content_type = resp.headers.get("content-type", "").lower()
            if "text/html" in content_type:
                cap = max(max_chars * 4, _HTML_STRIP_MAX_INPUT)
                text = _strip_html_text(text, max_input=cap)

            return {
                "status": "ok",
                "url": url,
                "content": text[:max_chars],
                "truncated": len(text) > max_chars,
            }
    except Exception as exc:
        return {
            "status": "error",
            "error": "api_error",
            "message": str(exc),
            "context": f"fetch_url: {url}"
        }
