#!/usr/bin/env python3
"""
Agent execution entry point implementing langgraph flow routines.

Whiskers Agent Route Embedding Test
===================================
CLI tool to test route embedding, including enqueuing jobs, running the async worker,
monitoring progress, and running semantic route searches.

Usage:
------
    # Run interactive search REPL (will automatically index any new routes first)
    python agent.py
    
    # Run a single query search and exit
    python agent.py --query "how do I search the catalog?"
    
    # Run search with custom top_k
    python agent.py --query "send a message" --top-k 3
"""

import os
import sys
import json
import asyncio
import argparse
import logging
import traceback
from typing import Any

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging - keep it clean and minimal for standard output
logging.basicConfig(
    level=logging.WARNING,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("whiskers.agent")

# Import database and embedding utilities
from db_layer.connection import get_async_session
from db_layer.embeddings.embeddings_routes import search_routes, get_route_count, get_indexed_operation_ids
from db_layer.embeddings.embeddings_core import _DEFAULT_MODELS
from sqlalchemy import select, func
from db_layer.models import EmbeddingJob, RouteEmbedding


async def get_pending_jobs_count() -> int:
    """Get the number of pending or processing embedding jobs."""
    async with get_async_session() as session:
        stmt = select(func.count()).select_from(EmbeddingJob).where(
            EmbeddingJob.status.in_(["pending", "processing"])
        )
        res = await session.execute(stmt)
        return res.scalar() or 0


async def get_failed_jobs() -> list[dict[str, Any]]:
    """Retrieve failed embedding jobs with errors."""
    async with get_async_session() as session:
        stmt = select(EmbeddingJob).where(EmbeddingJob.status == "failed").order_by(EmbeddingJob.created_at.desc())
        res = await session.execute(stmt)
        return [
            {
                "plugin_id": job.plugin_id,
                "operation_id": job.operation_id,
                "last_error": job.last_error,
            }
            for job in res.scalars().all()
        ]


async def print_system_info():
    """Print database, plugin, embedding environment, and system configurations."""
    print("=" * 80)
    print(" \033[1;36mWhiskers Agent Route Embedding Test Tool — System Info\033[0m")
    print("=" * 80)
    
    # 1. Config checks
    from utils.server_config import SERVER_CONFIG
    from utils.tools_api_config import TOOLS_CONFIG
    
    if SERVER_CONFIG:
        print(f"\033[1;32m[+]\033[0m server_config.json:    \033[1;32mLOADED\033[0m ({len(SERVER_CONFIG)} top-level keys)")
    else:
        print("\033[1;31m[-] server_config.json:    NOT LOADED or Empty\033[0m")
        
    if TOOLS_CONFIG:
        print(f"\033[1;32m[+]\033[0m tools_api_config.json: \033[1;32mLOADED\033[0m ({len(TOOLS_CONFIG)} top-level keys)")
    else:
        print("\033[1;31m[-] tools_api_config.json: NOT LOADED or Empty\033[0m")

    # 2. Database check
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("\033[1;31m[-] DATABASE_URL: NOT CONFIGURED\033[0m (Embedded routes require a database)")
        return False
    else:
        # Hide password for security
        import re
        masked_url = re.sub(r':([^@/]+)@', r':******@', db_url)
        print(f"\033[1;32m[+]\033[0m DATABASE_URL: {masked_url}")
        
    master_key = os.environ.get("MASTER_KEY", "")
    if not master_key:
        print("\033[1;31m[-] MASTER_KEY: NOT CONFIGURED\033[0m (Required for pgcrypto)")
        return False
    else:
        print(f"\033[1;32m[+]\033[0m MASTER_KEY: Configured ({len(master_key)} chars)")

    # 3. Embedding config
    from core.llm_config_service import resolve_route_embedding
    from db_layer.embeddings.embeddings_core import model_id_for

    route_sel = await resolve_route_embedding()
    route_model = model_id_for(route_sel)

    print(f"\033[1;32m[+]\033[0m Route Embedding Model:   \033[1;33m{route_model}\033[0m")

    print("=" * 80)
    return True


async def initialize_and_sync():
    """Initialize plugins, register routes, and run embedding worker for pending jobs."""
    # 1. Initialize the PluginRegistry
    from core.plugin_loader.plugin_registry import PluginRegistry, _set_registry
    from core.plugin_loader.plugin_loader import discover_and_load_plugins_async
    from core.context import mcp, vault, oauth_relay, route_registry
    
    print("\n\033[1;34m[*]\033[0m Initializing Plugin Registry and loading plugins...")
    registry = PluginRegistry(mcp, vault=vault, relay=oauth_relay)
    _set_registry(registry)
    
    # Discover and load plugins
    await discover_and_load_plugins_async(registry)
    
    # Initialize all loaded plugins (this populates route_registry)
    await registry.lifecycle.initialize_plugins()

    # Load and mount proxies if database is configured
    try:
        from core.proxy.proxy_manager import proxy_manager
        # Register the tools_discovered event listener
        def handle_proxy_tools_discovered(payload: dict):
            name = payload.get("name")
            tools = payload.get("tools")
            custom_description = payload.get("custom_description")
            workspace_label = payload.get("workspace_label")
            if not name or not tools:
                return
            from core.proxy.proxy_manager import proxy_manager
            provider = proxy_manager._active_handles.get(name)
            if not provider:
                return
            from core.proxy_tools.proxy_tool_loader import collect_from_proxy
            from core.context import route_registry
            descriptors = collect_from_proxy(
                name, tools, provider, custom_description=custom_description, workspace_label=workspace_label
            )
            if descriptors:
                route_registry.contribute(descriptors)

        registry.events.on("proxy.tools_discovered", handle_proxy_tools_discovered)
        await proxy_manager.load_persisted()
    except Exception as exc:
        print(f"\033[1;33m[WARNING] Failed to load/mount proxies: {exc}\033[0m")
    
    print(f"\033[1;32m[+]\033[0m Plugins loaded: {', '.join([p.name for p in registry.lifecycle._plugins])}")
    print(f"\033[1;32m[+]\033[0m Total routes contributed to RouteRegistry: {len(route_registry)}")

    # 2. Enqueue pending route embedding jobs
    from core_graph.worker import enqueue_pending, run_worker
    
    print("\n\033[1;34m[*]\033[0m Enqueuing pending routes for embedding...")
    enqueued = await enqueue_pending(route_registry)
    print(f"\033[1;32m[+]\033[0m Enqueued {enqueued} new/modified routes for embedding.")
    
    # 3. Check for any pending or processing jobs in the queue
    pending = await get_pending_jobs_count()
    if pending > 0:
        print(f"\033[1;34m[*]\033[0m Starting background embedding worker to process {pending} job(s)...")
        # Run worker with a frequent poll interval (1 second) for fast testing
        run_worker(batch_size=10, idle_poll=1.0)
    else:
        print("\033[1;32m[+]\033[0m No pending embedding jobs in queue.")
        
    # Check failed jobs
    failed_jobs = await get_failed_jobs()
    if failed_jobs:
        print(f"\n\033[1;33m[WARNING] Found {len(failed_jobs)} failed embedding jobs in the DB:\033[0m")
        for job in failed_jobs[:5]:
            print(f"  - Plugin: {job['plugin_id']}, Operation: {job['operation_id']}")
            print(f"    Error: {job['last_error']}")
        if len(failed_jobs) > 5:
            print(f"  ... and {len(failed_jobs) - 5} more.")
            
    # Print final DB stats
    route_count = await get_route_count()
    print(f"\n\033[1;32m[+]\033[0m Total Route Embeddings currently indexed in DB: \033[1;32m{route_count}\033[0m")


async def run_search(query: str, top_k: int):
    """Run a semantic route embedding search and display results."""
    print(f"\n\033[1;34m[*]\033[0m Searching for: '\033[1;33m{query}\033[0m' (top_k={top_k})")
    results = await search_routes(query, top_k=top_k)
    
    if not results:
        print("\033[1;31m[-] No matching routes found in database.\033[0m")
        return
        
    print("\n" + "=" * 120)
    print(f"\033[1m{'Plugin ID':<22} | {'Operation ID':<30} | {'Score':<6} | {'Method':<6} | {'Path Template'}\033[0m")
    print("=" * 120)
    for r in results:
        score_color = "\033[1;32m" if r['score'] >= 0.7 else ("\033[1;33m" if r['score'] >= 0.4 else "\033[1;30m")
        method_color = "\033[1;32m" if r['method'].upper() == 'GET' else "\033[1;34m"
        
        print(f"{r['plugin_id']:<22} | {r['operation_id']:<30} | {score_color}{r['score']:.4f}\033[0m | {method_color}{r['method']:<6}\033[0m | {r['path_template']}")
        print(f"  \033[1;30mDescription:\033[0m {r['description']}")
        if r.get('parameters'):
            print(f"  \033[1;30mParameters:\033[0m  {json.dumps(r['parameters'])}")
        print("-" * 120)


async def run_graph_query(query: str, force_execute: bool = False):
    """Run a query through the dynamic LangGraph orchestrator."""
    from core.llm_provider_management import LLMProvider, get_chat_llm, _llm_available
    from core_graph import build_dynamic_graph
    from core.context import route_registry
    from langchain_core.messages import HumanMessage

    api_url = os.environ.get("WHISKERS_API_URL") or os.environ.get("API_URL", "http://localhost:8000")
    project_id = os.environ.get("WHISKERS_PROJECT_ID") or os.environ.get("PROJECT_ID", "default")

    if not _llm_available():
        print("\n\033[1;31m[-] LLM provider not configured.\033[0m")
        print("Please check your .env file and set LLM_PROVIDER and the relevant API key.")
        return

    # Initialize the LLM provider
    provider_str = os.environ.get("LLM_PROVIDER", "openai").lower()
    model = os.environ.get("LLM_MODEL", "")
    try:
        provider = LLMProvider(provider_str)
    except ValueError:
        print(f"\n\033[1;31m[-] Unsupported LLM_PROVIDER='{provider_str}'\033[0m")
        return
        
    llm = get_chat_llm(provider, model)

    from utils.server_config import LONG_CHAIN_THRESHOLD, CONFIDENCE_EXECUTE_THRESHOLD, CONFIDENCE_CONFIRM_THRESHOLD
    print(f"\033[1;32m[+]\033[0m Graph thresholds loaded: long_chain={LONG_CHAIN_THRESHOLD}, execute={CONFIDENCE_EXECUTE_THRESHOLD:.0%}, confirm={CONFIDENCE_CONFIRM_THRESHOLD:.0%}")

    print(f"\033[1;34m[*]\033[0m Compiling dynamic graph (LLM: {provider_str.upper()})...")
    graph = build_dynamic_graph(
        llm,
        context_params={"project_id": project_id},
        api_url=api_url,
        route_registry=route_registry,
    )
    
    # LOCAL_CLI: unrestricted scopes (scope gate allows None scopes for this kind)
    from core.scope_management import PrincipalKind
    initial_state = {
        "user_query": query,
        "candidates": [],
        "plan": [],
        "current_step_index": 0,
        "step_results": [],
        "parallel_groups": [],
        "selected": None,
        "confidence": 0.0,
        "gate_decision": "execute",
        "clarification_question": None,
        "payload": None,
        "response": None,
        "retry_count": 0,
        "force_execute": force_execute,
        "messages": [HumanMessage(content=query)],
        "caller_scopes": None,
        "caller_kind": PrincipalKind.LOCAL_CLI,
        "caller_role": None,
    }
    
    print(f"\033[1;34m[*]\033[0m Executing query through dynamic graph...")
    
    def print_cat_progress(step_index: int, total_steps: int, current_op: str = ""):
        """Print a cute cat runner progress bar."""
        if total_steps <= 0:
            return
        width = 25
        pos = min(int((step_index / total_steps) * width), width)
        track = []
        for i in range(width):
            if i == pos:
                track.append("\033[1;33m🐱🐾\033[0m")
            elif i < pos:
                track.append("\033[1;32m=\033[0m")
            else:
                track.append("\033[1;30m-\033[0m")
        track_str = "".join(track)
        op_desc = f" ({current_op})" if current_op else ""
        flag = "🏁" if step_index >= total_steps else ""
        print(f"       {track_str} {flag} \033[1;36m[Step {step_index}/{total_steps}]\033[0m{op_desc}", flush=True)

    result = None
    _last_node = "init"
    try:
        async for event in graph.astream(initial_state):
            for node_name, state_update in event.items():
                if node_name == "__metadata__":
                    continue
                _last_node = node_name
                if result is None:
                    result = dict(initial_state)
                if state_update is not None:
                    result.update(state_update)

                # Dynamic visual reporting
                if node_name == "embedder":
                    print(" 🐱🐾  \033[1;36m[RAG Search]\033[0m Embedding query and matching API routes...")
                elif node_name == "planner":
                    plan = result.get("plan") or []
                    decision = result.get("gate_decision")
                    print(f" 🐱🐾  \033[1;36m[Planner]\033[0m Formulating execution plan. Decision: \033[1;33m{decision.upper()}\033[0m")
                    if plan:
                        print(f"       \033[1;30mPlan decomposed into {len(plan)} steps.\033[0m")
                        print_cat_progress(0, len(plan), "Starting plan...")
                elif node_name == "builder":
                    plan = result.get("plan") or []
                    idx = result.get("current_step_index", 0)
                    op_id = plan[idx]["operation_id"] if idx < len(plan) else "unknown"
                    print(f" 🐱🐾  \033[1;36m[Builder]\033[0m Constructing payload for \033[1;32m{op_id}\033[0m...")
                elif node_name == "executor":
                    plan = result.get("plan") or []
                    idx = result.get("current_step_index", 0)
                    op_id = plan[idx]["operation_id"] if idx < len(plan) else "unknown"
                    response = result.get("response") or {}
                    status = response.get("status") if isinstance(response, dict) else "ok"
                    print(f" 🐱🐾  \033[1;36m[Executor]\033[0m Executed! Result status: \033[1;32m{status}\033[0m")
                elif node_name == "validator":
                    print(" 🐱🐾  \033[1;36m[Validator]\033[0m Validating execution response shape...")
                elif node_name == "step_dispatcher":
                    plan = result.get("plan") or []
                    idx = result.get("current_step_index", 0)
                    op_id = plan[idx]["operation_id"] if idx < len(plan) else "Done"
                    print_cat_progress(idx, len(plan), op_id)
                elif node_name == "confirm_node":
                    print(" 🐱🐾  \033[1;33m[Confirm Node]\033[0m Plan suspended. Awaiting user confirmation...")
                elif node_name == "clarify_node":
                    print(" 🐱🐾  \033[1;33m[Clarify Node]\033[0m Clarification required.")
    except Exception as exc:
        print(f"\033[1;31m[-] Dynamic graph execution failed in [{_last_node}] node:\033[0m {type(exc).__name__}: {exc}")
        print("\033[1;31mTraceback:\033[0m")
        traceback.print_exc()
        return

    plan = result.get("plan") or []
    confidence = result.get("confidence", 0.0)
    decision = result.get("gate_decision")
    
    print("\n" + "=" * 80)
    print(" \033[1;36mDynamic Graph Execution Summary\033[0m")
    print("=" * 80)
    print(f"\033[1mQuery:\033[0m      {query}")
    print(f"\033[1mConfidence:\033[0m {confidence:.2%}")
    print(f"\033[1mDecision:\033[0m   \033[1;33m{decision.upper()}\033[0m")
    print("-" * 80)

    if plan:
        print("\033[1mExecution Plan:\033[0m")
        for i, step in enumerate(plan, 1):
            args_blob = step.get("args") or {}
            bindings = step.get("arg_bindings") or {}
            args_preview = ", ".join(
                [f"{k}={v}" for k, v in args_blob.items()] +
                [f"{k}=<{v}>" for k, v in bindings.items()]
            )
            fp_flag = " \033[1;32m[fast-path]\033[0m" if step.get("is_fast_path") else ""
            print(f"  {i}. {step['operation_id']} (plugin: {step.get('plugin_id')}){fp_flag}")
            if args_preview:
                print(f"     Args: {args_preview}")
        print("-" * 80)

    response = result.get("response")
    if response:
        status = response.get("status")
        if status == "confirmation_needed":
            print(f"\033[1;33m[!] CONFIRMATION REQUIRED:\033[0m")
            print(f"  {response.get('message')}")
            print("-" * 80)
            try:
                confirm = input("\033[1;35mDo you want to confirm and execute this plan? (y/N):\033[0m ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "no"
                print("\nExecution cancelled (no interactive input stream available).")
            if confirm in ("y", "yes"):
                await run_graph_query(query, force_execute=True)
            else:
                print("Execution cancelled.")
        elif status == "clarification_needed":
            print(f"\033[1;33m[!] CLARIFICATION REQUIRED:\033[0m")
            print(f"  {response.get('message')}")
            candidates = response.get("candidates") or []
            if candidates:
                print("\n  Top matching candidates:")
                for c in candidates:
                    print(f"    - [{c['method']}] {c['path']} (score: {c['score']:.4f})")
                    print(f"      Description: {c['description']}")
        elif status == "need_input":
            print(f"\033[1;33m[!] INPUT REQUIRED:\033[0m")
            print(f"  {response.get('message')}")
            if response.get("missing_params"):
                print(f"  Missing parameters: {', '.join(response.get('missing_params'))}")
        elif status == "error":
            print(f"\033[1;31m[-] Execution Error:\033[0m")
            print(f"  {response.get('message')}")
        else:
            print("\033[1;32m[+] Execution Completed Successfully!\033[0m")
            data = response.get("data")
            if data:
                print("\033[1mResult Data:\033[0m")
                print(json.dumps(data, indent=2))
            else:
                print(json.dumps(response, indent=2))
    else:
        print("\033[1;33m[!] No response envelope returned by graph.\033[0m")
        selected = result.get("selected")
        if selected:
            print(f"Best Route Match: {selected.get('method')} {selected.get('path') or selected.get('path_template')}")
    print("=" * 80 + "\n")


async def run_interactive_repl(top_k: int, default_mode: str = "search", dangerously_skip_permissions: bool = False):
    """Run an interactive REPL for querying route embeddings or executing graph plans."""
    print("\n" + "=" * 80)
    print("  \033[1;36mWhiskers Agent Route Embedding & Dynamic Graph CLI REPL\033[0m")
    print("  Commands:")
    print("    /mode          - Toggle default mode between Search and Graph")
    print("    /graph <query> - Execute a query using the Dynamic Graph plan")
    print("    /g <query>     - Short alias for /graph")
    print("    quit / exit    - Exit the REPL")
    print("=" * 80 + "\n")
    
    mode = default_mode
    while True:
        try:
            mode_color = "\033[1;32mSEARCH\033[0m" if mode == "search" else "\033[1;33mGRAPH\033[0m"
            prompt = f"\033[1;30m[{mode_color}]\033[0m \033[1;35mQuery:\033[0m "
            query = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
            
        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break
            
        if query.lower() == "/mode":
            mode = "graph" if mode == "search" else "search"
            print(f"\033[1;34m[*]\033[0m Toggled default mode to \033[1m{mode.upper()}\033[0m")
            continue
            
        is_graph_query = False
        target_query = query
        while True:
            stripped = False
            if target_query.startswith("/graph "):
                is_graph_query = True
                target_query = target_query[len("/graph "):].strip()
                stripped = True
            elif target_query.startswith("/g "):
                is_graph_query = True
                target_query = target_query[len("/g "):].strip()
                stripped = True
            if not stripped:
                break
                
        if mode == "graph":
            is_graph_query = True
            
        try:
            if is_graph_query:
                await run_graph_query(target_query, force_execute=dangerously_skip_permissions)
            else:
                await run_search(target_query, top_k)
        except Exception as exc:
            action = "graph execution" if is_graph_query else "search"
            print(f"\033[1;31mError during {action}:\033[0m {type(exc).__name__}: {exc}")
            traceback.print_exc()



async def run_reindex(plugin_id: str | None = None, proxy_name: str | None = None):
    """Execute route reindexing."""
    from db_layer.route_store import delete_plugin_route_embeddings, delete_all_route_embeddings
    from core.context import route_registry
    from core_graph.worker import enqueue_pending, run_worker
    
    if plugin_id:
        print(f"\n\033[1;34m[*]\033[0m Forcing route reindexing for plugin '{plugin_id}'...")
        await delete_plugin_route_embeddings(plugin_id)
    elif proxy_name:
        proxy_pid = f"proxy_{proxy_name}"
        print(f"\n\033[1;34m[*]\033[0m Forcing route reindexing for proxy '{proxy_name}'...")
        
        # We need to re-snapshot the proxy tools to re-contribute them
        from core.proxy.proxy_manager import proxy_manager
        provider = proxy_manager._active_handles.get(proxy_name)
        if not provider:
            print(f"\033[1;31m[-] Error: Proxy '{proxy_name}' is not mounted or active.\033[0m")
            return
            
        tools = await provider.server.list_tools()
        
        await delete_plugin_route_embeddings(proxy_pid)
            
        # Re-contribute via event bus
        from core.plugin_loader.plugin_registry import get_registry
        registry = get_registry()
        registry.events.emit("proxy.tools_discovered", {"name": proxy_name, "tools": tools})
    else:
        print("\n\033[1;34m[*]\033[0m Forcing full route reindexing...")
        await delete_all_route_embeddings()
    # Enqueue pending
    enqueued = await enqueue_pending(route_registry)
    print(f"\033[1;32m[+]\033[0m Enqueued {enqueued} routes for embedding.")
    
    # Run the worker to process the jobs
    pending = await get_pending_jobs_count()
    if pending > 0:
        print(f"\033[1;34m[*]\033[0m Running embedding worker to process {pending} job(s)...")
        run_worker(batch_size=10, idle_poll=1.0)
        
        while True:
            await asyncio.sleep(1)
            pending = await get_pending_jobs_count()
            if pending == 0:
                break
        print("\033[1;32m[+]\033[0m Embedding worker finished processing all jobs.")
    else:
        print("\033[1;32m[+]\033[0m No jobs enqueued.")


def list_graph_tools():
    """Print all candidate tools registered in the RouteRegistry."""
    from core.context import route_registry
    
    routes = route_registry.all_routes()
    if not routes:
        print("\n\033[1;33mRouteRegistry is empty. No candidate tools registered.\033[0m")
        return
        
    print("=" * 80)
    print(f" \033[1;36mRegistered Graph Tools — Candidates for LangGraph runner ({len(routes)} total)\033[0m")
    print("=" * 80)
    
    by_plugin: dict[str, list] = {}
    for r in routes:
        by_plugin.setdefault(r.plugin_id, []).append(r)
        
    for plugin_id, plist in sorted(by_plugin.items()):
        print(f"\n\033[1;34mPlugin:\033[0m \033[1;33m{plugin_id}\033[0m ({len(plist)} tools)")
        print("-" * 80)
        for r in sorted(plist, key=lambda x: x.operation_id):
            desc = r.description.strip().split("\n")[0] if r.description else "No description"
            if len(desc) > 65:
                desc = desc[:62] + "..."
            print(f"  - \033[1;32m{r.operation_id}\033[0m ({r.method}): {r.path_template}")
            print(f"    \033[0;90mDescription: {desc}\033[0m")
    print("=" * 80)


async def amain():
    """Main asynchronous entrypoint for the CLI agent."""
    parser = argparse.ArgumentParser(
        description="Whiskers Agent Route Embedding Test – test route indexing and matching",
    )
    parser.add_argument(
        "--query", "-q",
        default=None,
        help="A specific search query to run once instead of starting interactive REPL.",
    )
    parser.add_argument(
        "--top-k", "-k",
        type=int,
        default=5,
        help="Number of matching route candidates to return (default: 5).",
    )
    parser.add_argument(
        "--graph", "-g",
        action="store_true",
        help="Execute the query using the dynamic LangGraph orchestrator instead of a simple semantic search.",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Force full reindexing by dropping existing embeddings and jobs, then recreating them.",
    )
    parser.add_argument(
        "--reindex-plugin",
        default=None,
        help="Force reindexing for a specific plugin by name.",
    )
    parser.add_argument(
        "--reindex-proxy",
        default=None,
        help="Force reindexing for a specific proxy by name.",
    )
    parser.add_argument(
        "--list-graph-tools",
        action="store_true",
        help="List all route-embedding candidate tools currently available to the LangGraph runner.",
    )
    parser.add_argument(
        "--dangerously-skip-permissions",
        action="store_true",
        help="Bypass all permission gate prompts and checks (forces execute without user confirmation).",
    )
    args = parser.parse_args()

    # Disable excessive loggers to keep output tidy
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    
    if args.dangerously_skip_permissions:
        os.environ["DANGEROUSLY_SKIP_PERMISSIONS"] = "true"
    
    # 1. Print environment/system details
    ok = await print_system_info()
    if not ok:
        print("[-] System not fully configured. Please check your database settings.")
        sys.exit(1)
        
    # 2. Run initialization and sync enqueued routes
    try:
        await initialize_and_sync()
    except Exception as exc:
        logger.exception("Failed during plugin initialization or route sync")
        sys.exit(1)

    # Handle List Graph Tools / Reindexing Arguments
    if args.list_graph_tools:
        list_graph_tools()
        sys.exit(0)

    if args.reindex or args.reindex_plugin or args.reindex_proxy:
        try:
            await run_reindex(plugin_id=args.reindex_plugin, proxy_name=args.reindex_proxy)
            sys.exit(0)
        except Exception as exc:
            print(f"\033[1;31mError during reindexing:\033[0m {exc}")
            traceback.print_exc()
            sys.exit(1)
        
    # 3. Perform search (single query or REPL)
    try:
        if args.query:
            query_str = args.query.strip()
            is_graph = args.graph
            while True:
                stripped = False
                if query_str.startswith("/graph "):
                    is_graph = True
                    query_str = query_str[len("/graph "):].strip()
                    stripped = True
                elif query_str.startswith("/g "):
                    is_graph = True
                    query_str = query_str[len("/g "):].strip()
                    stripped = True
                if not stripped:
                    break
                    
            if is_graph:
                await run_graph_query(query_str, force_execute=args.dangerously_skip_permissions)
            else:
                await run_search(query_str, args.top_k)
        else:
            default_mode = "graph" if args.graph else "search"
            await run_interactive_repl(args.top_k, default_mode=default_mode, dangerously_skip_permissions=args.dangerously_skip_permissions)
    finally:
        from core_graph.worker import stop_worker
        await stop_worker()


def main():
    """Synchronous wrapper for the CLI agent loop."""
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print("\nGoodbye!")


if __name__ == "__main__":
    main()


