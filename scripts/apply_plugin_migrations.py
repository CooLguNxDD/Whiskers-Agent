#!/usr/bin/env python3
"""
Apply plugin-local schema migrations (manifest "schema" block) standalone,
without booting the full FastMCP app.

Normally plugin DDL is applied by PluginLifecycleManager during app boot
(core/plugin_loader/lifecycle_manager.py::run_plugin_migrations), one plugin
at a time as it loads. CI and a bare `python scripts/run_tests.py` never
boot the app, so a fresh Postgres never gets plugin-owned tables
(portfolio_projects, portfolio_job_layouts, ...) — only the Alembic core
chain runs. This script drives the same PluginSchemaMigrator entrypoint
directly, scanning `plugins/*/manifest.json` on disk (independent of
config/plugin_config.json, which is gitignored/host-local) so it covers
every plugin the test suite actually imports.

Usage:
    python scripts/apply_plugin_migrations.py
"""

import asyncio
import sys
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")


def _discover_packages() -> list[str]:
    """Every plugins/<pkg>/manifest.json on disk, as dotted package names."""
    packages = []
    for manifest_path in sorted((PROJECT_ROOT / "plugins").glob("*/manifest.json")):
        pkg_dir = manifest_path.parent.name
        packages.append(f"plugins.{pkg_dir}")
    return packages


async def main() -> int:
    from core.plugin_loader.resolver import build_specs
    from core.plugin_loader.lifecycle_manager import PluginLifecycleManager

    packages = _discover_packages()
    specs, skipped = build_specs(packages)
    for pkg, reason in skipped:
        print(f"[apply_plugin_migrations] skipping {pkg}: {reason}")

    applied = 0
    for spec in specs:
        if not spec.schema_cfg:
            continue
        print(f"[apply_plugin_migrations] {spec.name}: applying schema migrations...")
        await PluginLifecycleManager.run_plugin_migrations(spec)
        applied += 1

    print(f"[apply_plugin_migrations] done — {applied} plugin(s) migrated.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
