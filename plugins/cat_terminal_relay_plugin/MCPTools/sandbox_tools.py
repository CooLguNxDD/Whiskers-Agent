"""
Sandbox execution tools for the Cat Terminal Relay plugin.

Provides native subprocess execution inside a sandboxed working directory, with
strict command allowlisting and optional privilege elevation.
"""

import asyncio
import logging
import os
import signal
from pathlib import Path

from utils.error_response import tool_error
from core.context import mcp
from ..command_guard import guard_pipeline
from ..services import elevation_service
from ..plugin_config import SETTINGS

logger = logging.getLogger("whiskers.plugins")
_TAGS = {"cat_terminal_relay_plugin"}

SANDBOX_TIMEOUT_S = SETTINGS.get("sandbox_timeout_s", 60)
SANDBOX_MAX_OUTPUT_BYTES = SETTINGS.get("sandbox_max_output_bytes", 65536)
# repo-root/sandbox by default; parents[3] == repo root from MCPTools/sandbox_tools.py
_SANDBOX_ROOT_DEFAULT = str(Path(__file__).resolve().parents[3] / "sandbox")
_KILL_GRACE_S = 3.0
_CHUNK_SIZE = 65536


def _sandbox_env(cwd: str) -> dict[str, str]:
    """Minimal env for a sandboxed child — never inherit server secrets."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": cwd,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TERM": os.environ.get("TERM", "xterm"),
    }


async def _read_capped(stream: asyncio.StreamReader | None, cap: int) -> tuple[bytes, bool]:
    """Read a stream into a bounded buffer; keep draining (discarding) past cap.

    Draining rather than stopping avoids stalling the child on a full pipe
    buffer once the cap is hit, so it can still exit on its own.
    """
    buf = bytearray()
    truncated = False
    if stream is None:
        return bytes(buf), truncated
    while True:
        chunk = await stream.read(_CHUNK_SIZE)
        if not chunk:
            break
        if len(buf) < cap:
            remaining = cap - len(buf)
            buf.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
        else:
            truncated = True
    return bytes(buf), truncated


async def _kill_process_group(proc: "asyncio.subprocess.Process") -> None:
    """Terminate the whole process group a sandboxed command spawned, then reap it."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_S)
            return
        except asyncio.TimeoutError:
            pass
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        try:
            await proc.wait()
        except Exception:
            logger.debug("sandbox: proc.wait() after kill raised", exc_info=True)


async def _caller_identity() -> tuple[str | None, set[str]]:
    """Return (subject, scopes) for the current MCP caller, or (None, set())."""
    if os.environ.get("CAT_TERMINAL_DEV_AUTH") == "1":
        dev = os.environ.get("CAT_TERMINAL_DEV_SUBJECT")
        if dev:
            return dev, {"terminal:use", "terminal:host"}
    try:
        from fastmcp.server.dependencies import get_http_headers
        headers = get_http_headers() or {}
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
        if not token:
            return None, set()

        from core.auth_service import get_auth_service
        principal = await get_auth_service().principal_from_bearer(token)
        if principal is None:
            return None, set()
        return principal.subject, set(principal.scopes)
    except Exception as exc:
        logger.warning("terminal relay: caller identity resolution failed: %s", exc)
        return None, set()


@mcp.tool(title="sandbox", tags=_TAGS,
          annotations={"readOnlyHint": False, "destructiveHint": True,
                       "openWorldHint": True})
async def exec_command(command: str, workdir: str | None = None,
                       timeout_s: int | None = None) -> dict:
    """Execute a shell command natively in the server sandbox and return
    captured stdout/stderr/exit-code. Stateless; allowlist + elevation gated."""
    # a. If not command or not command.strip():
    if not command or not command.strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["command"],
            "message": "Please provide: command"
        }

    # b. subject, scopes = await _caller_identity().
    subject, scopes = await _caller_identity()
    if subject is None:
        return tool_error("unauthorized", "No valid terminal credentials on this request.")
    from core.scope_management import ScopeGrant, evaluate_access
    from core.scope_management.principal import PrincipalKind
    from core.scope_management.request import AccessRequest
    from core.route_registry.operation_descriptor import AccessClass

    grant = ScopeGrant(scopes=list(scopes), kind=PrincipalKind.API_KEY)
    req = AccessRequest(
        core_domain="terminal.sandbox",
        access=AccessClass.WRITE,
        tool_name="exec_command",
        plugin_id="cat_terminal_relay_plugin",
        tags=tuple(_TAGS),
    )
    decision = evaluate_access(
        grant,
        required={"core:terminal.sandbox:write", "core:terminal:write"},
        request=req,
    )
    if not decision.allowed:
        return tool_error("forbidden", "Missing required scope: core:terminal.sandbox:write or core:terminal:write.")

    # c. verdict = guard_pipeline(command).
    verdict = guard_pipeline(command)
    if not verdict["allowed"]:
        return {
            "status": "error",
            "error": verdict["reason"],
            "binaries": verdict["binaries"],
            "message": f"Command rejected: {verdict['reason']}"
        }

    # d. Elevation is required for privileged binaries, or for ANY command
    #    when the sandbox_require_elevation setting is on (default True).
    require_elevation = SETTINGS.get("sandbox_require_elevation", True) or verdict["privileged"]
    if require_elevation and not elevation_service.is_elevated(f"sandbox:{subject}"):
        return {
            "status": "error",
            "error": "elevation_required",
            "elevate_url": "/api/terminal/session_gated/sandbox/elevate",
            "message": "This command needs step-up elevation. POST "
                       "/api/terminal/session_gated/sandbox/elevate then retry."
        }

    # e. Resolve workdir:
    root = Path(os.environ.get("CAT_SANDBOX_WORKDIR", _SANDBOX_ROOT_DEFAULT)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if workdir:
        cwd = (root / workdir).resolve()
        if cwd != root and not cwd.is_relative_to(root):
            return tool_error("invalid_workdir", "workdir must stay inside the sandbox root.")
    else:
        cwd = root

    # f. Execute (shell, since chaining is allowed) — its own process group so a
    #    timeout kill reaches every descendant, not just the /bin/sh wrapper; and a
    #    minimal env so the sandbox never sees server secrets.
    cwd_s = str(cwd)
    proc = await asyncio.create_subprocess_shell(
        command, cwd=cwd_s, env=_sandbox_env(cwd_s),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        effective_timeout = timeout_s or SANDBOX_TIMEOUT_S
        try:
            (out_b, out_trunc), (err_b, err_trunc) = await asyncio.wait_for(
                asyncio.gather(
                    _read_capped(proc.stdout, SANDBOX_MAX_OUTPUT_BYTES),
                    _read_capped(proc.stderr, SANDBOX_MAX_OUTPUT_BYTES),
                ),
                timeout=effective_timeout,
            )
            await proc.wait()
        except asyncio.TimeoutError:
            await _kill_process_group(proc)
            return {
                "status": "error",
                "error": "timeout",
                "message": f"Command exceeded {effective_timeout}s and was killed."
            }
    finally:
        if proc.returncode is None:
            await _kill_process_group(proc)

    # g. Truncate + decode:
    truncated = out_trunc or err_trunc
    stdout = out_b.decode(errors="replace")
    stderr = err_b.decode(errors="replace")
    logger.info(
        "AUDIT sandbox_exec subject=%s binaries=%s exit=%s",
        subject, verdict["binaries"], proc.returncode
    )
    return {
        "status": "ok",
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "binaries": verdict["binaries"],
        "workdir": str(cwd),
        "truncated": truncated
    }
