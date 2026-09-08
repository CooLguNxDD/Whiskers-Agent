"""FastMCP context/elicitation helpers, candidate formatting, and JSON-response parsing.
Split out of core_graph/node/helpers.py (Phase 4 modularity refactor)."""
import asyncio
import contextlib
import json
import logging
import re
from typing import Any

from utils.server_config import MAX_FORMATTED_PLUGIN_SKILLS_CHARS, ELICIT_KEEPALIVE_INTERVAL_S

logger = logging.getLogger("whiskers")


def _current_mcp_context():
    """Return the live FastMCP Context if this runs inside a tool call, else None."""
    try:
        from fastmcp.server.dependencies import get_context
        return get_context()
    except Exception:
        return None


def _has_progress_channel(ctx) -> bool:
    """True only when the client sent a progressToken (so report_progress is
    delivered and can reset the host's request timeout)."""
    try:
        meta = ctx.request_context.meta
        return getattr(meta, "progressToken", None) is not None
    except Exception:
        return False


async def _progress_keepalive(ctx, interval: float, awaiting: str) -> None:
    """Emit periodic progress during long silent phases, resetting the host's
    per-request timeout so slow LLM/model loads or human waits can't trip -32001."""
    counter = 0
    try:
        while True:
            await asyncio.sleep(interval)
            counter += 1
            try:
                await ctx.report_progress(
                    counter, None,
                    json.dumps({"type": "keepalive", "awaiting": awaiting}),
                )
            except Exception:
                return  # progress channel gone; stop quietly
    except asyncio.CancelledError:
        raise


async def _elicit_keepalive(ctx, interval: float) -> None:
    """Keepalive wrapper for human elicitation waits."""
    await _progress_keepalive(ctx, interval, "elicitation")


async def _elicit(ctx, message: str, response_type):
    """Run an elicitation; return the accepted ``data`` or ``None`` (declined/
    cancelled/unsupported). Any transport/capability error degrades to ``None``
    so the node can fall back to the headless ``*_needed`` response path."""
    if not _has_progress_channel(ctx):
        logger.debug("Elicitation skipped: no progressToken on request meta; falling back to two-call path")
        return None
    from fastmcp.server.elicitation import AcceptedElicitation
    keepalive_task = asyncio.create_task(_elicit_keepalive(ctx, ELICIT_KEEPALIVE_INTERVAL_S))
    try:
        result = await ctx.elicit(message, response_type=response_type)
    except Exception as exc:  # client lacks elicitation capability, transport, etc.
        logger.debug("Elicitation unavailable, falling back to *_needed: %s", exc)
        return None
    finally:
        keepalive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keepalive_task
    if isinstance(result, AcceptedElicitation):
        return result.data
    return None


def _get_parameters_schema(candidate: dict) -> dict:
    """Return name -> schema dict, normalizing JSON-schema (properties+required) and flat proxy forms."""
    params = candidate.get("parameters", {}) or {}
    if isinstance(params, dict):
        if "properties" in params:
            props = params.get("properties", {}) or {}
            req = set(params.get("required", []) or [])
            out = {}
            for k, v in props.items():
                if isinstance(v, dict):
                    out[k] = {**v, "required": k in req}
                else:
                    out[k] = {"required": k in req}
            return out
        else:
            # flat form used by some proxy tools: {name: {required, type, description}}
            return {k: (v if isinstance(v, dict) else {}) for k, v in params.items()}
    return {}


def _param_hints(candidate: dict) -> list[str]:
    """Build richer per-param hints e.g. 'query* (string): search text...' for planner LLM."""
    from core_graph.goap.derive import _required_params
    req_set = set(_required_params(candidate))
    schema = _get_parameters_schema(candidate)
    hints = []
    for p in _required_params(candidate):
        sch = schema.get(p, {}) or {}
        mark = "*" if p in req_set else ""
        typ = sch.get("type") or ""
        if not typ and isinstance(sch.get("schema"), dict):
            typ = sch.get("schema", {}).get("type") or ""
        typ_str = f" ({typ})" if typ else ""
        desc = sch.get("description") or ""
        if desc:
            desc = " ".join(desc.strip().split())
            if len(desc) > 60:
                desc = desc[:57] + "..."
            desc = f": {desc}"
        hints.append(f"{p}{mark}{typ_str}{desc}")
    return hints


def _extract_workspace(candidate: dict) -> str | None:
    """Resolve workspace label from candidate fields, metadata, or workspace: tags."""
    for key in ("workspace_label", "workspace"):
        val = candidate.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    meta = candidate.get("metadata") or {}
    if isinstance(meta, dict):
        for key in ("workspace_label", "workspace"):
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for t in meta.get("tags") or []:
            if isinstance(t, str) and t.startswith("workspace:"):
                return t.split(":", 1)[1]
    return None


def _one_line(text: str, limit: int = 140) -> str:
    cleaned = " ".join((text or "").strip().split())
    if len(cleaned) > limit:
        return cleaned[: limit - 3] + "..."
    return cleaned


def normalize_tool_card(candidate: dict) -> dict:
    """Structured, schema-free tool card for discovery/planner-facing responses."""
    meta = candidate.get("metadata") or {}
    tags = [t for t in (meta.get("tags") or []) if isinstance(t, str) and not t.startswith("workspace:")]
    summary = _one_line(candidate.get("description") or "", limit=140)
    return {
        "operation_id": candidate.get("operation_id"),
        "plugin_id": candidate.get("plugin_id"),
        "workspace": _extract_workspace(candidate),
        "method": candidate.get("method"),
        "path": candidate.get("path") or candidate.get("path_template"),
        "summary": summary,
        "description": summary,  # back-compat for discover_tools clients
        "required_params": _param_hints(candidate),
        "tags": tags,
    }


def _format_candidates(candidates: list[dict]) -> str:
    from core_graph.goap.derive import _required_params
    lines = []
    for i, c in enumerate(candidates, 1):
        plugin_tag = f" [plugin={c.get('plugin_id', '?')}]"
        ws = _extract_workspace(c)
        ws_tag = f" · workspace: {ws}" if ws else ""
        fast = " (fast-path)" if c.get("is_fast_path") else ""
        item = (
            f"{i}. [{c.get('method', '')}] {c.get('path') or c.get('path_template', '')}{plugin_tag}{ws_tag}{fast}\n"
            f"   operationId: {c['operation_id']}\n"
            f"   description: {c['description']}\n"
            f"   score: {c['score']:.3f}"
        )
        req = _required_params(c)
        if req:
            hints = _param_hints(c)
            item += f"\n   params: {', '.join(hints)}"
        lines.append(item)
    return "\n".join(lines)


def _format_plugin_skills(candidates: list[dict]) -> str:
    """Build optional 'Plugin Capabilities / Skills' block from candidate plugin_ids.

    Only includes plugins that have registered non-empty skill text (via manifest "skills").
    Strips leading "proxy_" so lookup uses the short manifest name. Returns ""
    when nothing to inject (zero overhead for unskilled or irrelevant plugins).
    Applies a soft cap to protect planner prompt size.
    """
    if not candidates:
        return ""
    from core.plugin_loader.skill_registry import get_plugin_skills
    seen = set()
    blocks: list[str] = []
    total = 0
    MAX_TOTAL = MAX_FORMATTED_PLUGIN_SKILLS_CHARS
    for c in candidates:
        pid = c.get("plugin_id") or ""
        if not pid or pid in seen:
            continue
        seen.add(pid)
        short = pid[6:] if pid.startswith("proxy_") else pid
        text = get_plugin_skills(short)
        if not text:
            continue
        block = f"### Skills for plugin '{short}'\n{text}"
        if total + len(block) > MAX_TOTAL:
            # Truncate with marker instead of silently dropping the plugin.
            remain = MAX_TOTAL - total - 40
            if remain > 200:
                block = block[:remain].rstrip() + "\n…[truncated]"
                blocks.append(block)
                total = MAX_TOTAL
            break
        blocks.append(block)
        total += len(block) + 2
    if not blocks:
        return ""
    header = "Plugin Capabilities / Skills (workflow guidance — use for chaining these ops):\n"
    return header + "\n\n".join(blocks) + "\n"


def _parse_json_response(text: Any) -> dict | None:
    """Extract a JSON object from an LLM response, tolerant of markdown fences."""
    if not isinstance(text, str):
        if isinstance(text, list):
            parts = []
            for part in text:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and "text" in part:
                    parts.append(part["text"])
                elif hasattr(part, "text"):
                    parts.append(part.text)
                else:
                    parts.append(str(part))
            text = "".join(parts)
        else:
            text = str(text) if text is not None else ""

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass
    return None
