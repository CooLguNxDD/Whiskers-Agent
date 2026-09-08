"""CLI: ingest a live backend, spec file, or source tree into a route manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Allow `python Tools/openapi_pipeline/ingest.py` without installing the package.
if __package__ is None:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Tools.openapi_pipeline.adapters.detect import parse_source
from Tools.openapi_pipeline.adapters.live import fetch_live_spec, load_spec_file
from Tools.openapi_pipeline.adapters.spec import pick_server_url, spec_to_routes
from Tools.openapi_pipeline.build import build_spec, passthrough_spec
from Tools.openapi_pipeline.config import IngestConfig, load_ingest_config


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def ingest(
    *,
    config: IngestConfig,
    from_url: str | None = None,
    from_spec: Path | None = None,
    from_source: Path | None = None,
    out_dir: Path,
    write_openapi: bool = True,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Run ingest and write artifacts. Returns a summary dict."""
    repo_root = _repo_root()
    spec: dict[str, Any] | None = None
    spec_source = ""
    routes: list[dict]

    if from_url:
        spec, spec_source = fetch_live_spec(from_url, timeout=timeout)
        routes = spec_to_routes(spec, adapter="live")
        if not config.server or config.server == "http://localhost":
            config.server = pick_server_url(spec, from_url.rstrip("/"))
        if spec.get("info", {}).get("title") and config.title == "API":
            config.title = str(spec["info"]["title"])
    elif from_spec:
        spec = load_spec_file(from_spec)
        spec_source = str(from_spec)
        routes = spec_to_routes(spec, adapter="spec")
        if spec.get("info", {}).get("title") and config.title == "API":
            config.title = str(spec["info"]["title"])
        if not config.server or config.server == "http://localhost":
            config.server = pick_server_url(spec, config.server)
    elif from_source:
        source_root = from_source.resolve()
        routes = parse_source(config.adapter, source_root, repo_root, config)
    else:
        raise ValueError("one of from_url, from_spec, or from_source is required")

    already = sum(1 for r in routes if r.get("already_specified"))
    manifest_path = out_dir / "route-manifest.json"
    _write_json(manifest_path, routes)

    openapi_path = out_dir / "openapi.json"
    if write_openapi:
        if spec is not None and spec.get("openapi"):
            document = passthrough_spec(spec, config)
        else:
            document = build_spec(routes, config)
        _write_json(openapi_path, document)

    if already == len(routes) and routes:
        _write_json(out_dir / "enriched-manifest.json", routes)

    return {
        "routes": len(routes),
        "already_specified": already,
        "adapter": config.adapter if from_source else ("live" if from_url else "spec"),
        "spec_source": spec_source,
        "manifest": str(manifest_path),
        "openapi": str(openapi_path) if write_openapi else None,
        "skip_enrichment": bool(routes) and already == len(routes),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Ingest a live OpenAPI spec, a spec file, or backend source into a route manifest"
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-url", help="Running backend origin or exact OpenAPI URL")
    src.add_argument("--from-spec", help="Path to openapi.json / swagger.yaml")
    src.add_argument("--from-source", help="Directory of backend source to scan")
    parser.add_argument(
        "--adapter",
        default=None,
        help="Source adapter: auto|express|fastapi|flask (source ingest only)",
    )
    parser.add_argument("--config", help="JSON ingest config (prefixes, security maps, title)")
    parser.add_argument("--out-dir", default="openapi", help="Artifact directory")
    parser.add_argument("--title", help="OpenAPI info.title override")
    parser.add_argument("--server", help="OpenAPI servers[0].url override")
    parser.add_argument("--base-path", help="Prefix joined onto source-scanned paths")
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout for --from-url")
    parser.add_argument("--no-openapi", action="store_true", help="Write only route-manifest.json")
    args = parser.parse_args(argv)

    config = load_ingest_config(Path(args.config) if args.config else None)
    if args.adapter:
        config.adapter = args.adapter
    if args.title:
        config.title = args.title
    if args.server:
        config.server = args.server
    if args.base_path:
        config.base_path = args.base_path

    try:
        summary = ingest(
            config=config,
            from_url=args.from_url,
            from_spec=Path(args.from_spec) if args.from_spec else None,
            from_source=Path(args.from_source) if args.from_source else None,
            out_dir=Path(args.out_dir),
            write_openapi=not args.no_openapi,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"adapter:            {summary['adapter']}")
    if summary["spec_source"]:
        print(f"spec source:        {summary['spec_source']}")
    print(f"routes:             {summary['routes']}")
    print(f"already specified:  {summary['already_specified']}")
    print(f"manifest:           {summary['manifest']}")
    if summary["openapi"]:
        print(f"openapi:            {summary['openapi']}")
    if summary["skip_enrichment"]:
        print("enrichment:         skip (live/file spec already has operations)")
    else:
        print("enrichment:         run Step 2 (source scan has no request/response shapes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
