"""Pick an ingest adapter from flags, config, or source heuristics."""

from __future__ import annotations

from pathlib import Path

from Tools.openapi_pipeline.adapters.express import parse_express_source
from Tools.openapi_pipeline.adapters.fastapi import parse_fastapi_source
from Tools.openapi_pipeline.adapters.flask import parse_flask_source
from Tools.openapi_pipeline.config import IngestConfig

ADAPTER_NAMES = (
    "spec",
    "live",
    "express",
    "fastapi",
    "flask",
    "auto",
)


def detect_source_adapter(root: Path) -> str:
    """Guess a source adapter by scanning a small sample of files."""
    fastapi_hits = 0
    flask_hits = 0
    express_hits = 0
    sample = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".js", ".ts", ".py", ".mjs", ".cjs"}:
            continue
        sample += 1
        if sample > 200:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "@app.get(" in text or "@router.get(" in text or "FastAPI(" in text:
            fastapi_hits += 1
        if "@app.route(" in text or "@bp.route(" in text or "Flask(" in text:
            flask_hits += 1
        if "express()" in text or "Router()" in text or "app.get(" in text:
            express_hits += 1
    scored = [
        ("fastapi", fastapi_hits),
        ("flask", flask_hits),
        ("express", express_hits),
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    if scored[0][1] > 0:
        return scored[0][0]
    return "express" if any(root.rglob("*.js")) else "fastapi"


def parse_source(
    adapter: str,
    source_root: Path,
    repo_root: Path,
    config: IngestConfig,
) -> list[dict]:
    """Dispatch to a source adapter. ``auto`` detects from files under source_root."""
    chosen = adapter if adapter != "auto" else detect_source_adapter(source_root)
    config.adapter = chosen
    if chosen == "express":
        return parse_express_source(source_root, repo_root, config)
    if chosen == "fastapi":
        return parse_fastapi_source(source_root, repo_root, config)
    if chosen == "flask":
        return parse_flask_source(source_root, repo_root, config)
    raise ValueError(f"unknown source adapter: {chosen}")
