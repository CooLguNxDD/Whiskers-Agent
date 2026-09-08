"""Auto-inject CLI instructions + MCP config for Mode B GoapAgent runs.

Loads the package instruction file and, when enabled, writes a temporary
Claude-compatible MCP config JSON that points the headless CLI at this
Whiskers Agent HTTP endpoint with a bearer token. Drivers then either pass
``--mcp-config`` (Claude) or place discovery files in the spawn workdir
(Grok / Antigravity / generic) via :mod:`cli.mcp_place`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from core_graph.goap_agent.env_flags import env_truthy

logger = logging.getLogger("whiskers_agent.goap_agent.cli_inject")

_PKG_DIR = Path(__file__).resolve().parent
DEFAULT_INSTRUCTIONS_PATH = _PKG_DIR / "instructions" / "CLI_AGENT.md"
DEFAULT_MCP_SERVER_NAME = "whiskers-goap"


@dataclass
class CliInjection:
    """Prepared instruction text + optional MCP config for one CLI spawn."""

    instructions: str
    instructions_path: str | None = None
    mcp_config_path: str | None = None
    mcp_url: str | None = None
    token_source: str = "none"  # env | minted | none | disabled | explicit_config
    cleanup_paths: list[str] = field(default_factory=list)
    strict_mcp: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
    # Workdir discovery files (Grok/Agy/generic); restored on cleanup.
    _mcp_placement: Any = field(default=None, repr=False)
    # Held from a successful place_mcp_discovery() through cleanup() so
    # concurrent runs sharing *workdir* can't interleave place/restore.
    _workdir_lock: asyncio.Lock | None = field(default=None, repr=False)

    async def place_mcp_discovery(self, agent: str, workdir: str) -> None:
        """Install agent-specific MCP discovery files under *workdir*."""
        if not self.mcp_config_path or not workdir:
            return
        from core_graph.goap_agent.cli.mcp_place import get_workdir_lock, place_mcp_for_agent

        lock = get_workdir_lock(workdir)
        await lock.acquire()
        self._workdir_lock = lock
        try:
            placement = place_mcp_for_agent(
                agent,
                workdir=workdir,
                mcp_config_path=self.mcp_config_path,
            )
            self._mcp_placement = placement
            if placement.meta:
                self.meta["mcp_place"] = dict(placement.meta)
        except Exception as exc:
            logger.warning(
                "GoapAgent MCP place failed agent=%s workdir=%s: %s",
                agent,
                workdir,
                exc,
            )
            self._workdir_lock.release()
            self._workdir_lock = None

    def cleanup(self) -> None:
        """Best-effort remove temp files/dirs created for this injection."""
        if self._mcp_placement is not None:
            try:
                self._mcp_placement.cleanup()
            except Exception:
                logger.debug("cli_inject.py: swallowed exception", exc_info=True)
            self._mcp_placement = None
        if self._workdir_lock is not None:
            try:
                self._workdir_lock.release()
            except Exception:
                logger.debug("cli_inject.py: swallowed exception", exc_info=True)
            self._workdir_lock = None
        dirs: list[Path] = []
        for path in list(self.cleanup_paths):
            try:
                p = Path(path)
                if p.is_file():
                    p.unlink(missing_ok=True)
                elif p.is_dir():
                    dirs.append(p)
            except Exception:
                logger.debug("cli_inject.py: swallowed exception", exc_info=True)
        # Remove empty temp dirs after files (reverse so nested first).
        for p in sorted(dirs, key=lambda x: len(str(x)), reverse=True):
            try:
                for child in p.iterdir():
                    if child.is_file():
                        child.unlink(missing_ok=True)
                p.rmdir()
            except Exception:
                logger.debug("cli_inject.py: swallowed exception", exc_info=True)
        self.cleanup_paths.clear()



def instructions_path() -> Path:
    """Resolve path to the CLI instruction markdown file."""
    override = (os.environ.get("GOAP_AGENT_INSTRUCTIONS_PATH") or "").strip()
    if override:
        return Path(override)
    return DEFAULT_INSTRUCTIONS_PATH


def load_cli_instructions(*, path: Path | str | None = None) -> str:
    """Load GoapAgent CLI system instructions from disk (or empty on miss)."""
    p = Path(path) if path else instructions_path()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("GoapAgent CLI instructions missing: %s", p)
        return ""
    except Exception as exc:
        logger.warning("GoapAgent CLI instructions unreadable (%s): %s", p, exc)
        return ""
    return text.strip()


def auto_mcp_enabled() -> bool:
    """Whether Mode B should auto-write --mcp-config (default on)."""
    return env_truthy("GOAP_AGENT_AUTO_MCP", default=True)


def strict_mcp_enabled() -> bool:
    """Whether to pass --strict-mcp-config (default on when auto-injecting)."""
    return env_truthy("GOAP_AGENT_STRICT_MCP", default=True)


def resolve_mcp_url() -> str:
    """Resolve the MCP HTTP endpoint the child CLI should call.

    Order:
      1. GOAP_AGENT_MCP_URL / CLI_AGENT_MCP_URL (full URL including /mcp)
      2. MCP_SERVER_URL + /mcp (loopback-normalized for in-container self-call)
    """
    for key in ("GOAP_AGENT_MCP_URL", "CLI_AGENT_MCP_URL"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw.rstrip("/")

    base = (os.environ.get("MCP_SERVER_URL") or "http://127.0.0.1:10000").strip().rstrip("/")
    # Prefer loopback when the public URL is localhost-ish so Docker-child
    # processes reach the same server process reliably.
    try:
        parsed = urlparse(base)
        host = (parsed.hostname or "").lower()
        if host in ("localhost", "0.0.0.0", "::", "[::]"):
            port = parsed.port
            netloc = f"127.0.0.1:{port}" if port else "127.0.0.1"
            base = urlunparse((parsed.scheme or "http", netloc, "", "", "", "")).rstrip("/")
        elif host in ("whiskers-agent", "whiskers-agent-server", "mcp-server"):
            # Compose service name from another container — keep as-is.
            pass
    except Exception:
        logger.debug("cli_inject.py: swallowed exception", exc_info=True)

    if base.endswith("/mcp"):
        return base
    return f"{base}/mcp"


def resolve_mcp_token(*, explicit: str | None = None) -> tuple[str | None, str]:
    """Resolve bearer for child MCP calls. Returns (token|None, source)."""
    if explicit is not None and str(explicit).strip() != "":
        return str(explicit).strip(), "explicit"

    for key in ("GOAP_AGENT_MCP_TOKEN", "CLI_AGENT_MCP_TOKEN"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw, "env"

    return None, "none"


async def mint_goap_agent_token() -> tuple[str | None, str]:
    """Mint a short-lived Layer-1 access token for the GoapAgent CLI child.

    Falls back to (None, source) when OAuth is off or minting fails.
    """
    try:
        from core.context import OAUTH_ENABLED
        import core.context as _ctx
    except Exception as exc:
        logger.debug("GoapAgent mint: core.context import failed: %s", exc)
        return None, "none"

    if not OAUTH_ENABLED:
        return None, "oauth_disabled"

    svc = getattr(_ctx, "_oauth_svc", None)
    if svc is None:
        return None, "oauth_unavailable"

    try:
        from core.scope_management import Scope, get_valid_scopes
        from core.scope_management.roles import playground_mcp_scopes

        # Prefer admin-class scopes so node-driven plugin calls are not
        # fail-closed mid-run; fall back to full vocabulary + GoapAgent tokens.
        scopes = list(playground_mcp_scopes("admin"))
        if not scopes:
            scopes = sorted(
                set(get_valid_scopes())
                | {
                    Scope.ADMIN,
                    "plugin:GoapAgent",
                    "group:GoapAgent:read",
                    "group:GoapAgent:write",
                }
            )
        # Ensure GoapAgent vocabulary is present even if role map is thin.
        for tok in (
            "plugin:GoapAgent",
            "group:GoapAgent:read",
            "group:GoapAgent:write",
        ):
            if tok not in scopes:
                scopes.append(tok)

        client_id = (os.environ.get("GOAP_AGENT_OAUTH_CLIENT_ID") or "goap-agent-cli").strip()
        await svc.ensure_internal_client(client_id, scopes=" ".join(scopes))
        # Admin role is deliberate: deny-all gateway keys still get a capable
        # oneshot child; ocat_source="goap_agent_cli" is the recursion guard so
        # nested run_graph runs the native graph. Override the whole mint path
        # with GOAP_AGENT_MCP_TOKEN. GoapAgent_run_cli_agent still inherits the
        # outer caller's bearer when present (does not always mint).
        pair = await svc._issue_token_pair(
            client_id,
            scopes,
            extra_claims={
                "whiskers_role": "admin",
                "ocat_role": "admin",
                "whiskers_source": "goap_agent_cli",
                "ocat_source": "goap_agent_cli",
            },
        )
        token = (pair or {}).get("access_token")
        if token:
            return str(token), "minted"
        return None, "mint_empty"
    except Exception as exc:
        logger.warning("GoapAgent CLI token mint failed: %s", exc)
        return None, "mint_failed"


def build_mcp_config_dict(
    *,
    url: str,
    token: str | None = None,
    server_name: str | None = None,
) -> dict[str, Any]:
    """Build a Claude Code ``mcpServers`` document for HTTP MCP."""
    name = (server_name or os.environ.get("GOAP_AGENT_MCP_SERVER_NAME") or DEFAULT_MCP_SERVER_NAME).strip()
    entry: dict[str, Any] = {
        "type": "http",
        "url": url,
    }
    if token:
        entry["headers"] = {"Authorization": f"Bearer {token}"}
    return {"mcpServers": {name: entry}}


def write_mcp_config_file(
    config: dict[str, Any],
    *,
    directory: str | None = None,
) -> str:
    """Write MCP config JSON to a temp file; return absolute path."""
    dir_path = directory
    if not dir_path:
        dir_path = tempfile.mkdtemp(prefix="goap_agent_mcp_")
    else:
        Path(dir_path).mkdir(parents=True, exist_ok=True)

    path = Path(dir_path) / "mcp-config.json"
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        logger.debug("cli_inject.py: swallowed exception", exc_info=True)
    return str(path.resolve())


def write_instructions_file(
    text: str,
    *,
    directory: str | None = None,
) -> str:
    """Write instruction markdown to a temp file for --append-system-prompt-file."""
    dir_path = directory
    if not dir_path:
        dir_path = tempfile.mkdtemp(prefix="goap_agent_instr_")
    else:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
    path = Path(dir_path) / "CLI_AGENT.md"
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return str(path.resolve())


async def prepare_cli_injection(
    *,
    session_id: str | None = None,
    mcp_config_path: str | None = None,
    inject_mcp: bool | None = None,
    bearer_token: str | None = None,
    agent: str | None = None,
) -> CliInjection:
    """Load instructions and optionally auto-write MCP config for a CLI spawn.

    Parameters
    ----------
    session_id:
        Folded into instructions when the caller already has a session.
    mcp_config_path:
        Explicit config path — skips auto-write when set.
    inject_mcp:
        Override for auto MCP (None → ``GOAP_AGENT_AUTO_MCP`` default on).
    bearer_token:
        Optional pre-resolved bearer (e.g. from the outer MCP request).
    agent:
        Driver name (``claude`` / ``agy`` / ``grok`` / ``generic``). All
        drivers receive auto-MCP when enabled; discovery placement differs
        per agent (see :meth:`CliInjection.place_mcp_discovery`).
    """
    instr = load_cli_instructions()
    if session_id:
        extra = (
            f"\n\n## Session\n\n"
            f"Use existing `session_id={session_id}` when calling GoapAgent tools.\n"
        )
        instr = (instr + extra).strip() if instr else extra.strip()

    result = CliInjection(instructions=instr)
    src_path = instructions_path()
    if src_path.is_file():
        result.meta["instructions_source"] = str(src_path)

    do_inject = auto_mcp_enabled() if inject_mcp is None else bool(inject_mcp)
    agent_name = (agent or os.environ.get("CLI_AGENT_PROVIDER") or "claude").strip().lower()
    result.meta["agent"] = agent_name
    # Claude-only flag; other drivers ignore. Default on when auto-injecting.
    claude_like = agent_name in ("claude", "claude-cli", "")
    result.strict_mcp = bool(claude_like and strict_mcp_enabled())

    # Materialise instructions file for all agents (Claude uses
    # --append-system-prompt-file; others may prepend body or {instructions_file}).
    tmp_dir: str | None = None
    if instr:
        tmp_dir = tempfile.mkdtemp(prefix="goap_agent_cli_")
        # Track the dir so runner can chown it for whiskers-claude and cleanup.
        result.cleanup_paths.append(tmp_dir)
        ipath = write_instructions_file(instr, directory=tmp_dir)
        result.instructions_path = ipath
        result.cleanup_paths.append(ipath)

    if not do_inject:
        result.token_source = "disabled"
        return result

    if mcp_config_path:
        result.mcp_config_path = mcp_config_path
        result.token_source = "explicit_config"
        return result

    # Explicit path via env without auto-write
    env_cfg = (os.environ.get("GOAP_AGENT_MCP_CONFIG") or "").strip()
    if env_cfg and Path(env_cfg).is_file():
        result.mcp_config_path = env_cfg
        result.token_source = "explicit_config"
        return result

    url = resolve_mcp_url()
    result.mcp_url = url

    token, source = resolve_mcp_token(explicit=bearer_token)
    if not token and source == "none":
        token, source = await mint_goap_agent_token()

    result.token_source = source
    cfg = build_mcp_config_dict(url=url, token=token)
    if tmp_dir is None:
        tmp_dir = tempfile.mkdtemp(prefix="goap_agent_cli_")
        result.cleanup_paths.append(tmp_dir)
    path = write_mcp_config_file(cfg, directory=tmp_dir)
    result.mcp_config_path = path
    result.cleanup_paths.append(path)
    result.meta.update({
        "mcp_url": url,
        "mcp_server_name": next(iter(cfg["mcpServers"])),
        "has_bearer": bool(token),
    })
    logger.info(
        "GoapAgent CLI inject: agent=%s mcp_config=%s url=%s token=%s strict=%s",
        agent_name,
        path,
        url,
        source,
        result.strict_mcp,
    )
    return result


def compose_prompt(user_goal: str, *, injection: CliInjection, prepend_instructions: bool = True) -> str:
    """Build the full CLI prompt (instructions optional — prefer system-prompt-file)."""
    goal = (user_goal or "").strip()
    if not prepend_instructions or not injection.instructions:
        return goal
    return f"{injection.instructions}\n\n[user goal]\n{goal}".strip()
