"""Debug content_hash compute + optional force-persist for all build_specs plugins."""
import asyncio
import json
import selectors
import sys

from core.plugin_loader.resolver import build_specs
from db_layer.plugin_registry_store import DBPluginRegistry
from utils.config_registry import PLUGIN_CONFIG_PATH


async def main() -> None:
    with open(PLUGIN_CONFIG_PATH, "r", encoding="utf-8") as f:
        packages = list((json.load(f) or {}).get("plugins") or [])
    specs, skipped = build_specs(packages)
    print("=== build_specs content_hash ===")
    for s in specs:
        h = s.content_hash or ""
        print(f"  {s.name}\t{h[:56] if h else 'EMPTY'}")
    if skipped:
        print("skipped:", skipped)

    store = DBPluginRegistry()
    rows = {r.id: r for r in await store.get_all()}
    print("=== DB vs spec ===")
    for s in specs:
        rec = rows.get(s.name)
        if rec is None:
            db_h = None
        else:
            meta = rec.meta if isinstance(rec.meta, dict) else {}
            db_h = getattr(rec, "content_hash", None) or meta.get("content_hash")
        print(f"  {s.name}\tspec={bool(s.content_hash)}\tdb={bool(db_h)}\tmatch={db_h == s.content_hash if db_h else False}")

    if "--force" in sys.argv:
        print("=== force persist ===")
        for s in specs:
            if not s.content_hash:
                print(f"  skip empty {s.name}")
                continue
            await store.set_content_hash(
                s.name,
                s.content_hash,
                (s.manifest or {}).get("version", "0.0.0"),
            )
            print(f"  wrote {s.name}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(main())
