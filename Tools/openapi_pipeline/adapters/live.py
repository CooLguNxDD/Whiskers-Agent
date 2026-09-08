"""Fetch a live OpenAPI/Swagger document from a running backend."""

from __future__ import annotations

from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from Tools.openapi_pipeline.adapters.spec import is_openapi_or_swagger, parse_spec_text
from Tools.openapi_pipeline.config import KNOWN_OPENAPI_PATHS

_FETCH_TIMEOUT_S = 15
_MAX_BYTES = 20 * 1024 * 1024


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def fetch_bytes(url: str, timeout: float = _FETCH_TIMEOUT_S) -> bytes:
    """GET a URL and return the response body. HTTP(S) only."""
    if not _is_http_url(url):
        raise ValueError(f"refusing non-http(s) URL: {url}")
    req = Request(url, headers={"Accept": "application/json, application/yaml, text/yaml, */*"})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — CLI ingest, http(s) gated
        data = resp.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise ValueError(f"response from {url} exceeded {_MAX_BYTES} bytes")
    return data


def _try_parse(body: bytes, source: str) -> dict[str, Any] | None:
    try:
        text = body.decode("utf-8", errors="replace")
        data = parse_spec_text(text, source_name=source)
    except Exception:
        return None
    if isinstance(data, dict) and is_openapi_or_swagger(data):
        return data
    return None


def fetch_live_spec(url: str, timeout: float = _FETCH_TIMEOUT_S) -> tuple[dict[str, Any], str]:
    """Load an OpenAPI/Swagger document from ``url``.

    If ``url`` already returns a spec, use it. Otherwise probe well-known
    documentation paths under the same origin (FastAPI ``/openapi.json``,
    Spring ``/v3/api-docs``, Swagger UI ``/swagger.json``, …).
    """
    if not _is_http_url(url):
        raise ValueError(f"refusing non-http(s) URL: {url}")

    candidates = [url]
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    # Probe well-known paths when the given URL looks like a host/root, not a document.
    path = parsed.path or "/"
    looks_like_doc = path.rstrip("/").endswith((
        ".json", ".yaml", ".yml", "openapi", "swagger", "api-docs",
    )) or "openapi" in path.lower() or "swagger" in path.lower() or "api-docs" in path.lower()
    if not looks_like_doc:
        for suffix in KNOWN_OPENAPI_PATHS:
            candidates.append(urljoin(origin + "/", suffix.lstrip("/")))

    errors: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            body = fetch_bytes(candidate, timeout=timeout)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        spec = _try_parse(body, candidate)
        if spec is not None:
            return spec, candidate
        errors.append(f"{candidate}: not an OpenAPI/Swagger document")

    detail = "; ".join(errors[:8]) or "no candidates"
    raise FileNotFoundError(
        f"could not find an OpenAPI/Swagger document at {url} (tried well-known paths). {detail}"
    )


def load_spec_file(path) -> dict[str, Any]:
    """Load OpenAPI/Swagger from a local JSON or YAML file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    data = parse_spec_text(text, source_name=str(path))
    if not is_openapi_or_swagger(data):
        raise ValueError(f"{path} is not an OpenAPI or Swagger document")
    return data
