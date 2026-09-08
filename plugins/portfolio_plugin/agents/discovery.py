"""Discovery agent — MCP-proxy-first GitHub/Notion inventory into PortfolioDraft.

Agentic-first: an LLM tool-calling loop (``core_graph.agent_loop``) inventories
repos and judges portfolio-worthiness itself — no hardcoded repo allowlist
decision, no substring keyword filter deciding what counts as "a project".
Falls back to the deterministic ``discovery.pipeline.run_discovery`` sweep
when no LLM is configured/available or the agent run produced no findings, so
behavior degrades gracefully rather than silently doing nothing.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.portfolio_plugin.compose.blackboard import PortfolioDraft, get_draft_store

logger = logging.getLogger("whiskers.plugins.portfolio_plugin.agents.discovery")

_DISCOVERY_TOOLS = None  # lazy — avoid import cost until first agentic run


def _discovery_tool_refs():
    global _DISCOVERY_TOOLS
    if _DISCOVERY_TOOLS is None:
        from core_graph.agent_loop.spec import ToolRef

        _DISCOVERY_TOOLS = [
            ToolRef("portfolio_plugin", "*list_owned_repos"),
            ToolRef("portfolio_plugin", "*fetch_repo_insight"),
            ToolRef("portfolio_plugin", "*fetch_external_context"),
            ToolRef("portfolio_plugin", "*get_project_context"),
            ToolRef("search_plugin", "*web_search"),
            # Narrow, read-only globs — a mounted GitHub Copilot proxy carries
            # 40+ tools including writes (create/merge/delete); a discovery
            # agent has no business seeing those, so bind only inventory/read
            # operations rather than the blanket "proxy_github*"/"*".
            ToolRef("proxy_github*", "*search_repositories*"),
            ToolRef("proxy_github*", "*list_repos*"),
            ToolRef("proxy_github*", "*get_repo*"),
            ToolRef("proxy_github*", "*get_readme*"),
            ToolRef("proxy_github*", "*get_file_contents*"),
            ToolRef("proxy_Notion*", "*search*"),
            ToolRef("proxy_Notion*", "*fetch*"),
            ToolRef("proxy_notion*", "*search*"),
            ToolRef("proxy_notion*", "*fetch*"),
        ]
    return _DISCOVERY_TOOLS


def _scope_wants_github(scope: str) -> bool:
    return scope in ("all", "github")


def _scope_wants_notion(scope: str) -> bool:
    return scope in ("all", "notion")


def _proxy_bound(resolved: list[dict[str, Any]], capability_substr: str) -> bool:
    needle = capability_substr.lower()
    for r in resolved or []:
        cap = str(r.get("capability") or "").lower()
        pid = str(r.get("plugin_id") or "")
        if needle in cap and pid:
            return True
    return False


def _honest_capability_report(
    *,
    scope: str,
    resolved: list[dict[str, Any]],
    fetched: list[dict[str, Any]],
    doc_count: int,
) -> dict[str, Any] | None:
    """Return an error envelope when expected capabilities are missing (not silent ok)."""
    errors: list[dict[str, Any]] = []
    hints: list[str] = []

    if _scope_wants_notion(scope) and not _proxy_bound(resolved, "notion"):
        errors.append(
            {
                "error": "proxy_not_mounted",
                "capability": "notion.pages.search",
                "hints": [
                    "Mount a Notion MCP proxy (prefer proxy_Notion-* / proxy_notion-*).",
                    "Ensure OperationCatalog is live (call via MCP server, not bare docker exec).",
                    "Notion page body uses proxy notion-fetch — not REST NOTION_API_KEY.",
                ],
            }
        )
        hints.append("Notion discovery requires a mounted Notion proxy op.")

    # GitHub: local fallback exists; only error when neither proxy nor local tool bound.
    if _scope_wants_github(scope):
        gh_proxy = _proxy_bound(resolved, "github")
        gh_local = any(
            (r.get("local_tool") == "list_owned_repos") for r in (resolved or [])
        )
        if not gh_proxy and not gh_local:
            errors.append(
                {
                    "error": "proxy_not_mounted",
                    "capability": "github.repos.list",
                    "hints": [
                        "Mount proxy_github-* or ensure list_owned_repos fallback is available.",
                        "Set GITHUB_TOKEN (vault) and/or github_allowlist.repos for public fetch.",
                    ],
                }
            )

    if not errors:
        return None

    # Notion-only scope with no proxy: hard fail (never silent empty success).
    if scope == "notion" and not _proxy_bound(resolved, "notion"):
        return {
            "status": "error",
            "error": "discovery_capability_missing",
            "errors": errors,
            "hints": hints,
            "resolved": resolved,
            "fetched": fetched,
            "doc_count": doc_count,
        }

    # Soft partial when some docs arrived (e.g. github ok, notion missing on all).
    if doc_count > 0:
        return {
            "status": "partial",
            "error": "discovery_capability_partial",
            "errors": errors,
            "hints": hints,
            "resolved": resolved,
            "fetched": fetched,
            "doc_count": doc_count,
        }

    # No docs and capabilities missing.
    return {
        "status": "error",
        "error": "discovery_capability_missing",
        "errors": errors,
        "hints": hints,
        "resolved": resolved,
        "fetched": fetched,
        "doc_count": 0,
    }


async def _findings_to_docs(findings: list[dict[str, Any]]) -> list[Any]:
    """Convert agent findings into ContextDocs for the deterministic index/reconcile step.

    The discovery prompt tells the agent to prefer an owner-scoped listing
    tool, but it's also allowed to call a mounted generic `search_repositories`
    op (resolver.py's "repos" intent binds either). A generic search is not
    scoped to the authenticated owner, so a superficially-matching but
    unrelated third party's repo (e.g. a different GitHub user's same-named
    project) can come back and get silently indexed as this owner's own work.
    Gate every github ref through the same broad allowlist (static owners ∪
    hero links ∪ token login) that fetch_external_context already enforces
    for live fetches — never trust the agent's self-reported ownership.
    """
    from plugins.portfolio_plugin.discovery.normalize import (
        ContextDoc,
        is_discovery_allowed_ref,
        strip_directives,
    )

    docs = []
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        sources = f.get("context_sources") or []
        primary = sources[0] if sources and isinstance(sources[0], dict) else {}
        # Refs only ever come from the agent's structured context_sources field
        # (which itself must trace back to the repo-listing tool's own output,
        # per the discovery prompt's anti-injection rule) — never from prose.
        ref = str(primary.get("ref") or "").strip()
        kind = str(primary.get("kind") or "github").strip() or "github"
        if kind == "github" and ref and not await is_discovery_allowed_ref(ref, strict=False):
            logger.warning("agentic discovery: rejected out-of-allowlist ref %s", ref)
            continue
        slug = str(f.get("slug") or "").strip()
        name = str(f.get("name") or slug or ref or "Untitled").strip()
        summary = strip_directives(str(f.get("summary") or ""))
        if not summary.strip():
            summary = f"Discovered {kind} project {ref or slug}."
        links = f.get("links") if isinstance(f.get("links"), list) else []
        url = None
        for link in links:
            if isinstance(link, dict) and str(link.get("href") or "").startswith(("http://", "https://")):
                url = str(link["href"])
                break
        tags = f.get("tags") if isinstance(f.get("tags"), list) else []
        text = strip_directives(f"# {name}\n\n{summary}")
        if not ref and not slug:
            continue
        docs.append(
            ContextDoc(
                source=kind,
                ref=ref or slug,
                kind=kind,
                title=name,
                text=text[:50000],
                url=url,
                updated_at=None,
                slug_hint=slug or None,
                tags=[str(t) for t in tags if t],
            )
        )
    return docs


async def _run_agentic_discovery(
    *,
    scope: str,
    tenant_id: int,
    write_back: bool | None,
    do_index: bool,
    force: bool,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    """Attempt the agent tool-calling discovery loop. Returns None to signal fallback."""
    from core.llm_provider_management import llm_available

    if not llm_available():
        return None

    try:
        from core_graph.agent_loop.runner import run_agent
        from core_graph.agent_loop.spec import AgentSpec
        from plugins.portfolio_plugin.prompts.discovery_prompt import (
            DISCOVERY_OUTPUT_SCHEMA,
            DISCOVERY_SYSTEM_PROMPT,
        )
    except Exception as exc:
        logger.warning("agentic discovery unavailable (import): %s", exc)
        return None

    from core.context.transport import is_local_stdio
    from core.scope_management import get_request_principal

    principal = get_request_principal()
    if principal is not None:
        caller_scopes = list(principal.scopes) if principal.scopes is not None else []
    elif is_local_stdio():
        # Local CLI/stdio: unrestricted (LOCAL_CLI) is intentional.
        caller_scopes = None
    else:
        # Anonymous HTTP: empty scopes → is_allowed fails closed (not LOCAL_CLI).
        caller_scopes = []
        logger.info(
            "No request principal on non-stdio transport; discovery agent uses empty scopes."
        )

    spec = AgentSpec(
        name="portfolio_discovery",
        system_prompt=DISCOVERY_SYSTEM_PROMPT,
        tools=_discovery_tool_refs(),
        max_steps=10,
        max_seconds=75.0,
        output_schema=DISCOVERY_OUTPUT_SCHEMA,
        caller_scopes=caller_scopes,
    )
    prompt = (
        f"Inventory and curate portfolio-worthy projects. scope={scope}. "
        "Include any actively-developed project you find, not just ones you've seen "
        "mentioned before — call the repo listing tool and judge from what it returns."
    )
    try:
        agent_result = await run_agent(spec, prompt, tenant_id=tenant_id)
    except Exception as exc:
        logger.warning("agentic discovery run failed open: %s", exc)
        return None

    if agent_result.status not in ("ok", "partial"):
        return None
    findings = (agent_result.output or {}).get("findings") if isinstance(agent_result.output, dict) else None
    if not findings:
        return None

    docs = await _findings_to_docs(findings)
    if not docs:
        return None

    from plugins.portfolio_plugin.discovery.index import index_docs
    from plugins.portfolio_plugin.discovery.reconcile import apply_reconcile, plan_reconcile
    from plugins.portfolio_plugin.discovery.resolver import _discovery_settings
    from plugins.portfolio_plugin.plugin_config import SETTINGS

    try:
        from plugins.portfolio_plugin.store import list_projects

        existing = await list_projects(include_inactive=True, tenant_id=tenant_id)
    except Exception as exc:
        logger.warning("agentic discovery: list_projects failed open: %s", exc)
        existing = []

    disc = _discovery_settings(SETTINGS if isinstance(SETTINGS, dict) else {})
    effective_write_back = (
        False
        if dry_run
        else (bool(write_back) if write_back is not None else bool(disc.get("write_back")))
    )

    plan = plan_reconcile(docs, existing)
    if not do_index or dry_run:
        index_result = {
            "status": "skipped",
            "reason": "dry_run" if dry_run else "do_index_false",
        }
    else:
        index_result = await index_docs(docs, tenant_id=tenant_id, force=force)
    reconcile_result = await apply_reconcile(plan, tenant_id=tenant_id, write_back=effective_write_back)

    # Derive a "resolved capabilities" view from what the agent actually called,
    # so the honest-capability-report gate applies identically to both paths.
    resolved: list[dict[str, Any]] = []
    for call in agent_result.tool_calls:
        name = str(call.get("name") or "")
        if "list_owned_repos" in name or "github" in name.lower():
            resolved.append({"capability": "github.repos.list", "plugin_id": name, "local_tool": None})
        if "notion" in name.lower():
            resolved.append({"capability": "notion.pages.search", "plugin_id": name, "local_tool": None})

    return {
        "status": "ok",
        "agentic": True,
        "dry_run": bool(dry_run),
        "write_back": effective_write_back,
        "scope": scope,
        "resolved": resolved,
        "fetched": [],
        "docs": [d.to_dict() for d in docs],
        "doc_count": len(docs),
        "findings": findings,
        "reconcile_plan": plan,
        "index": index_result,
        "reconcile": reconcile_result,
        "agent_steps": agent_result.steps,
        "agent_errors": agent_result.errors,
        "hints": [],
    }


async def run_discovery_agent(
    *,
    draft: PortfolioDraft | None = None,
    draft_id: str | None = None,
    scope: str = "all",
    dry_run: bool = True,
    force: bool = False,
    write_back: bool | None = None,
    do_index: bool = False,
    tenant_id: int | None = None,
    use_agentic: bool | None = None,
) -> dict[str, Any]:
    """Run portfolio discovery and optionally fold results into a draft.

    Tries the agentic tool-calling loop first (when an LLM is configured);
    falls back to the deterministic capability-resolver sweep when the agent
    is unavailable or produced no findings. Returns the discovery envelope,
    possibly upgraded to ``error``/``partial`` when Notion (or GitHub)
    capabilities are missing.

    ``dry_run`` gates writes (index/reconcile), not which planner runs — the
    agentic loop is bound to read-only tool globs (see ``_discovery_tool_refs``)
    so it's safe to run under dry_run too. Pass ``use_agentic=False`` to force
    the deterministic sweep regardless of LLM availability.
    """
    from core.context import current_tenant_id
    from plugins.portfolio_plugin.discovery.pipeline import run_discovery

    tid = tenant_id
    if tid is None:
        try:
            raw = current_tenant_id.get()
            if raw is not None:
                tid = int(raw)
            else:
                logger.info("No current_tenant_id contextvar found, defaulting discovery tenant_id to 1.")
                tid = 1
        except Exception as exc:
            logger.warning("Failed to resolve tenant_id from contextvar: %s. Defaulting to 1.", exc)
            tid = 1

    scope_n = (scope or "all").strip().lower()
    if scope_n not in ("all", "github", "notion"):
        return {
            "status": "error",
            "error": "invalid_scope",
            "message": "scope must be all|github|notion",
        }

    result: dict[str, Any] | None = None
    if use_agentic is not False:
        try:
            result = await _run_agentic_discovery(
                scope=scope_n,
                tenant_id=int(tid),
                write_back=write_back,
                do_index=do_index,
                force=force,
                dry_run=bool(dry_run),
            )
        except Exception as exc:
            logger.warning("agentic discovery path errored, falling back: %s", exc)
            result = None

    if result is None:
        try:
            result = await run_discovery(
                scope=scope_n,
                dry_run=bool(dry_run),
                force=bool(force),
                write_back=write_back,
                do_index=bool(do_index) and not dry_run,
                tenant_id=int(tid),
            )
        except Exception as exc:
            logger.exception("run_discovery_agent failed")
            return {"status": "error", "error": "discovery_failed", "message": str(exc)[:500]}

    resolved = list(result.get("resolved") or [])
    fetched = list(result.get("fetched") or [])
    doc_count = int(result.get("doc_count") or 0)

    upgrade = _honest_capability_report(
        scope=scope_n,
        resolved=resolved,
        fetched=fetched,
        doc_count=doc_count,
    )
    if upgrade is not None:
        # Preserve useful payload for debugging
        upgrade["docs"] = result.get("docs") or []
        upgrade["reconcile_plan"] = result.get("reconcile_plan")
        upgrade["hints"] = list(result.get("hints") or []) + list(upgrade.get("hints") or [])
        result = {**result, **upgrade}

    # Fold into draft when provided / found
    store = get_draft_store()
    d = draft
    if d is None and draft_id:
        d = store.get(draft_id)
    if d is not None:
        d.resolved_sources = resolved
        d.context_docs = list(result.get("docs") or [])
        d.phase = "discover"
        d.touch()
        result["draft"] = d.to_public()

    return result
