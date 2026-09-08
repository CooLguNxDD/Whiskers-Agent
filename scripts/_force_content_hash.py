"""Force-persist content hashes for all configured plugins; report failures."""
import asyncio
import json
import selectors
import sys
import traceback

from core.plugin_loader.resolver import build_specs
from db_layer.plugin_registry_store import DBPluginRegistry
from utils.config_registry import PLUGIN_CONFIG_PATH


async def main() -> None:
    with open(PLUGIN_CONFIG_PATH, "r", encoding="utf-8") as f:
        packages = list((json.load(f) or {}).get("plugins") or [])
    specs, _ = build_specs(packages)
    store = DBPluginRegistry()
    for s in specs:
        print(f"--- {s.name} hash_len={len(s.content_hash or '')}")
        try:
            rec = await store.get(s.name)
            print(f"  row_exists={rec is not None}")
            if not s.content_hash:
                print("  SKIP empty hash")
                continue
            await store.set_content_hash(
                s.name,
                s.content_hash,
                (s.manifest or {}).get("version", "0.0.0"),
            )
            rec2 = await store.get(s.name)
            if rec2 is None:
                h = None
            else:
                meta = rec2.meta if isinstance(rec2.meta, dict) else {}
                h = getattr(rec2, "content_hash", None) or meta.get("content_hash")
            print(f"  after={h[:40] if h else None}")
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(main())
