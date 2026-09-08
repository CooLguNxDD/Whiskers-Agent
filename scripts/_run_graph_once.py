"""One-shot headless run_graph for debugging."""
import asyncio
import json
import selectors
import sys

from core_graph.mcp_tool import run_graph_impl


async def main():
    """
    Main entrypoint for running graph once.
    """
    query = (
        "Search Notion for a 'cat' page, fetch its full content, "
        "list all features described on the page, and tell me which is the best one."
    )
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    result = await run_graph_impl(query, force_execute=True, session_id="cli-notion-cat-rerun")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(main())