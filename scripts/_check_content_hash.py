"""One-shot: print plugins.content_hash (column) with legacy meta fallback."""
import asyncio
import selectors
import sys
from db_layer.plugin_registry_store import DBPluginRegistry


async def main() -> None:
    rows = await DBPluginRegistry().get_all()
    for r in rows:
        meta = r.meta if isinstance(r.meta, dict) else {}
        h = getattr(r, "content_hash", None) or meta.get("content_hash") or ""
        hist = meta.get("version_history") or []
        print(
            f"{r.id}\thash={h[:48] if h else None}\tstale={meta.get('stale')}\thist={len(hist)}"
        )
    print(f"total={len(rows)}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(main())
