#!/usr/bin/env python3
"""Skill-local entrypoint for generic ingest (delegates to Tools.openapi_pipeline)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO))

from Tools.openapi_pipeline.ingest import main

if __name__ == "__main__":
    raise SystemExit(main())
