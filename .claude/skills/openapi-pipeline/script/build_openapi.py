#!/usr/bin/env python3
"""Step 3 — assemble OpenAPI 3.0 from a route manifest.

Security schemes come from ingest config (BearerAuth by default, or
`security_schemes` in --config).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO))

from Tools.openapi_pipeline.build import build_spec
from Tools.openapi_pipeline.config import IngestConfig, load_ingest_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Build OpenAPI 3.0 spec from route manifest")
    parser.add_argument("--input", "-i", default=str(_REPO / "openapi/enriched-manifest.json"))
    parser.add_argument("--fallback", default=str(_REPO / "openapi/route-manifest.json"))
    parser.add_argument("--out", "-o", default=str(_REPO / "openapi/openapi.json"))
    parser.add_argument("--server", default="http://localhost:8080")
    parser.add_argument("--title", default="API")
    parser.add_argument("--api-version", default="1.0.0")
    parser.add_argument("--config", help="Ingest config JSON (security schemes, ignored segments)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        fallback = Path(args.fallback)
        if fallback.exists():
            print(f"INFO: enriched manifest not found, falling back to {fallback}")
            input_path = fallback
        else:
            print(f"ERROR: neither {input_path} nor {fallback} exist", file=sys.stderr)
            return 1

    config = load_ingest_config(Path(args.config)) if args.config else IngestConfig()
    config.server = args.server
    config.title = args.title
    config.api_version = args.api_version

    routes = json.loads(input_path.read_text(encoding="utf-8"))
    print(f"Loaded {len(routes)} routes from {input_path}")
    spec = build_spec(routes, config)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    print(f"OpenAPI spec written to {out}")
    print(f"  paths:   {len(spec['paths'])}")
    print(f"  tags:    {len(spec['tags'])}")
    print(f"  schemes: {list(spec['components']['securitySchemes'].keys())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
