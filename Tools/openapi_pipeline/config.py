"""Ingest configuration: prefixes, security maps, and adapter selection."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_SECURITY_SCHEMES: dict[str, dict[str, Any]] = {
    "BearerAuth": {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Bearer access token",
    },
}

KNOWN_OPENAPI_PATHS = (
    "/openapi.json",
    "/openapi.yaml",
    "/openapi.yml",
    "/swagger.json",
    "/swagger.yaml",
    "/v3/api-docs",
    "/v3/api-docs.yaml",
    "/api-docs",
    "/api/openapi.json",
    "/docs/openapi.json",
    "/docs/swagger.json",
)


@dataclass
class IngestConfig:
    """Operator-supplied ingest options. Empty maps mean adapter defaults."""

    adapter: str = "auto"
    title: str = "API"
    api_version: str = "1.0.0"
    server: str = "http://localhost"
    base_path: str = ""
    router_prefixes: dict[str, str] = field(default_factory=dict)
    middleware_security: dict[str, str] = field(default_factory=dict)
    security_schemes: dict[str, dict[str, Any]] = field(default_factory=dict)
    ignored_path_segments: list[str] = field(default_factory=lambda: ["api"])
    source_globs: list[str] = field(default_factory=list)

    def resolved_security_schemes(self) -> dict[str, dict[str, Any]]:
        """Explicit security_schemes from config, else BearerAuth."""
        if self.security_schemes:
            return dict(self.security_schemes)
        return dict(DEFAULT_SECURITY_SCHEMES)

    def resolved_router_prefixes(self) -> dict[str, str]:
        """Optional router-name → URL prefix map from config."""
        return dict(self.router_prefixes)

    def resolved_middleware_security(self) -> dict[str, str]:
        """Optional middleware-name → OpenAPI scheme map from config."""
        return dict(self.middleware_security)


def load_ingest_config(path: Path | None) -> IngestConfig:
    """Load ingest config JSON, or return defaults when path is None."""
    if path is None:
        return IngestConfig()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("ingest config must be a JSON object")
    known = {f.name for f in IngestConfig.__dataclass_fields__.values()}
    kwargs = {k: v for k, v in data.items() if k in known}
    return IngestConfig(**kwargs)
