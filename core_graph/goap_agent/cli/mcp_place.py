"""Place auto-injected MCP config where each CLI agent discovers servers.

Claude Code takes ``--mcp-config`` (handled by the driver). Grok and Antigravity
read project-scoped files under the spawn *workdir*. Generic drivers receive
the path via env / ``{mcp_config}`` template expansion.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("whiskers_agent.goap_agent.cli")

# Marker so we only rewrite sections we own and can restore cleanly.
_GROK_SECTION_BEGIN = "# BEGIN whiskers-goap-agent mcp"
_GROK_SECTION_END = "# END whiskers-goap-agent mcp"

# Concurrent Grok/Agy Mode B runs sharing the same workdir (the common case —
# default_workdir() is the repo root) would otherwise interleave place/restore
# and corrupt or drop each other's discovery file section. One lock per
# workdir serializes place → run → cleanup for that workdir only.
_workdir_locks: dict[str, asyncio.Lock] = {}


def get_workdir_lock(workdir: str) -> asyncio.Lock:
    """Return the process-wide lock guarding MCP discovery placement for *workdir*."""
    lock = _workdir_locks.get(workdir)
    if lock is None:
        lock = asyncio.Lock()
        _workdir_locks[workdir] = lock
    return lock


@dataclass
class McpPlacement:
    """Tracks workdir discovery files written for one CLI spawn."""

    paths_created: list[str] = field(default_factory=list)
    restore: list[tuple[str, str | None]] = field(default_factory=list)
    # path → original text (None = file did not exist; delete on cleanup)
    meta: dict[str, Any] = field(default_factory=dict)

    def cleanup(self) -> None:
        """Restore overwritten files and remove ones we created."""
        for path, original in self.restore:
            try:
                p = Path(path)
                if original is None:
                    if p.is_file():
                        p.unlink(missing_ok=True)
                    # Best-effort: remove empty parents we created (.grok / .agents)
                    try:
                        p.parent.rmdir()
                    except OSError:
                        pass
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(original, encoding="utf-8")
            except Exception as exc:
                logger.debug("mcp_place restore %s failed: %s", path, exc)
        for path in self.paths_created:
            try:
                p = Path(path)
                if p.is_file() and not any(r[0] == path for r in self.restore):
                    p.unlink(missing_ok=True)
            except Exception:
                logger.debug("mcp_place.py: swallowed exception", exc_info=True)
        self.paths_created.clear()
        self.restore.clear()


def _read_claude_mcp_config(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("mcp config root must be an object")
    return data


def _first_server(cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    servers = cfg.get("mcpServers") or {}
    if not isinstance(servers, dict) or not servers:
        raise ValueError("mcp config missing mcpServers")
    name, entry = next(iter(servers.items()))
    if not isinstance(entry, dict):
        raise ValueError("mcp server entry must be an object")
    return str(name), entry


def claude_config_to_agy(cfg: dict[str, Any]) -> dict[str, Any]:
    """Rewrite Claude ``type/url`` entries to Antigravity ``serverUrl`` shape."""
    out_servers: dict[str, Any] = {}
    for name, entry in (cfg.get("mcpServers") or {}).items():
        if not isinstance(entry, dict):
            continue
        new_entry = dict(entry)
        url = new_entry.pop("url", None) or new_entry.get("serverUrl")
        new_entry.pop("type", None)
        if url:
            new_entry["serverUrl"] = url
        out_servers[str(name)] = new_entry
    return {"mcpServers": out_servers}


def claude_config_to_grok_toml_section(cfg: dict[str, Any]) -> str:
    """Render a Grok ``[mcp_servers.<name>]`` TOML block from Claude-style JSON."""
    name, entry = _first_server(cfg)
    url = entry.get("url") or entry.get("serverUrl") or ""
    headers = entry.get("headers") if isinstance(entry.get("headers"), dict) else {}
    lines = [
        _GROK_SECTION_BEGIN,
        f"[mcp_servers.{name}]",
        f'url = "{_toml_escape(str(url))}"',
        "enabled = true",
    ]
    if headers:
        # headers = { "Authorization" = "Bearer …" }
        parts = []
        for hk, hv in headers.items():
            parts.append(f'"{_toml_escape(str(hk))}" = "{_toml_escape(str(hv))}"')
        lines.append("headers = { " + ", ".join(parts) + " }")
    lines.append(_GROK_SECTION_END)
    lines.append("")
    return "\n".join(lines)


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _upsert_grok_toml(existing: str | None, section: str) -> str:
    """Insert or replace our marked section inside a Grok config.toml body."""
    body = existing or ""
    pattern = re.compile(
        re.escape(_GROK_SECTION_BEGIN) + r".*?" + re.escape(_GROK_SECTION_END) + r"\n?",
        re.DOTALL,
    )
    if pattern.search(body):
        return pattern.sub(section.rstrip() + "\n", body)
    if body and not body.endswith("\n"):
        body += "\n"
    if body and not body.endswith("\n\n"):
        body += "\n"
    return body + section


def _merge_json_mcp(existing: dict[str, Any] | None, overlay: dict[str, Any]) -> dict[str, Any]:
    base = dict(existing or {})
    servers = dict(base.get("mcpServers") or {}) if isinstance(base.get("mcpServers"), dict) else {}
    for name, entry in (overlay.get("mcpServers") or {}).items():
        servers[str(name)] = entry
    base["mcpServers"] = servers
    return base


def place_mcp_for_agent(
    agent: str,
    *,
    workdir: str,
    mcp_config_path: str,
) -> McpPlacement:
    """Write discovery files so *agent* loads the Whiskers Agent server.

    *mcp_config_path* must be a Claude-style ``mcpServers`` JSON file (what
    ``cli_inject.write_mcp_config_file`` produces).
    """
    placement = McpPlacement()
    agent_l = (agent or "").strip().lower()
    cfg = _read_claude_mcp_config(mcp_config_path)
    wd = Path(workdir)

    if agent_l in ("claude", "claude-cli", ""):
        # Driver passes --mcp-config; nothing to place in workdir.
        placement.meta["strategy"] = "cli_flag"
        return placement

    if agent_l in ("grok", "grok-cli"):
        target = wd / ".grok" / "config.toml"
        original: str | None = None
        if target.is_file():
            original = target.read_text(encoding="utf-8")
        section = claude_config_to_grok_toml_section(cfg)
        new_body = _upsert_grok_toml(original, section)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_body, encoding="utf-8")
        try:
            os.chmod(target, 0o600)
        except Exception:
            logger.debug("mcp_place.py: swallowed exception", exc_info=True)
        placement.restore.append((str(target), original))
        placement.meta["strategy"] = "grok_project_toml"
        placement.meta["path"] = str(target)
        logger.info("GoapAgent MCP place: grok config → %s", target)
        return placement

    if agent_l in ("agy", "agy-cli", "antigravity"):
        target = wd / ".agents" / "mcp_config.json"
        original = None
        existing_obj: dict[str, Any] | None = None
        if target.is_file():
            original = target.read_text(encoding="utf-8")
            try:
                parsed = json.loads(original)
                if isinstance(parsed, dict):
                    existing_obj = parsed
            except json.JSONDecodeError:
                existing_obj = None
        merged = _merge_json_mcp(existing_obj, claude_config_to_agy(cfg))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(target, 0o600)
        except Exception:
            logger.debug("mcp_place.py: swallowed exception", exc_info=True)
        placement.restore.append((str(target), original))
        placement.meta["strategy"] = "agy_workspace_json"
        placement.meta["path"] = str(target)
        logger.info("GoapAgent MCP place: agy config → %s", target)
        return placement

    if agent_l in ("generic",):
        # Generic expands {mcp_config} / env; optional Claude-compat .mcp.json
        # for agents that scan the project root.
        target = wd / ".mcp.json"
        original = None
        if target.is_file():
            original = target.read_text(encoding="utf-8")
        target.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(target, 0o600)
        except Exception:
            logger.debug("mcp_place.py: swallowed exception", exc_info=True)
        placement.restore.append((str(target), original))
        placement.meta["strategy"] = "project_mcp_json"
        placement.meta["path"] = str(target)
        logger.info("GoapAgent MCP place: generic .mcp.json → %s", target)
        return placement

    # Unknown agent: still drop Claude-compat .mcp.json (widely supported).
    target = wd / ".mcp.json"
    original = None
    if target.is_file():
        original = target.read_text(encoding="utf-8")
    target.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except Exception:
        logger.debug("mcp_place.py: swallowed exception", exc_info=True)
    placement.restore.append((str(target), original))
    placement.meta["strategy"] = "project_mcp_json"
    placement.meta["path"] = str(target)
    return placement
