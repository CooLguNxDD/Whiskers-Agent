"""CLI agent driver protocol and shared result types."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger("whiskers")


@dataclass
class CliRunOptions:
    """Optional flags for a single headless agent invocation."""

    workdir: str | None = None
    timeout_s: float | None = None
    mcp_config_path: str | None = None
    strict_mcp_config: bool = False
    append_system_prompt: str | None = None
    append_system_prompt_file: str | None = None
    allowed_tools: list[str] | None = None
    model: str | None = None
    extra_args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    max_output_bytes: int | None = None
    # When True, drivers may enable extra internal verbosity (e.g. claude --verbose).
    verbose: bool | None = None
    # Override per-run file logging (None → GOAP_AGENT_LOG default).
    log_enabled: bool | None = None
    # OS user for the CLI subprocess (None → CLI_AGENT_USER / whiskers-claude).
    # Empty string disables privilege drop for this call.
    run_as_user: str | None = None


@dataclass
class CliResult:
    """Structured result from a headless CLI agent run."""

    status: str  # ok | error | timeout | binary_not_found
    text: str = ""
    raw_stdout: str = ""
    raw_stderr: str = ""
    returncode: int | None = None
    agent: str = ""
    cmd: list[str] = field(default_factory=list)
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    # Paths into logs/goap_agent/<run_id>/ when file logging is on.
    log: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for MCP tool responses."""
        return {
            "status": self.status,
            "text": self.text,
            "returncode": self.returncode,
            "agent": self.agent,
            "cmd": self.cmd,
            "error": self.error,
            "stderr": (self.raw_stderr or "")[-4000:],
            "meta": self.meta,
            "log": self.log or None,
        }


class CliAgentDriver(Protocol):
    """Builds argv and parses stdout for one headless agent binary family."""

    name: str

    def binary(self) -> str:
        """Resolved binary path or name."""
        ...

    def available(self) -> bool:
        """True when the binary is on PATH."""
        ...

    def build_cmd(self, prompt: str, *, options: CliRunOptions) -> list[str]:
        """Return argv list (no shell)."""
        ...

    def parse_result(self, stdout: str, stderr: str, returncode: int) -> CliResult:
        """Parse process output into CliResult."""
        ...


def which_binary(name: str) -> str | None:
    """Return absolute path if *name* is executable on PATH."""
    if not name:
        return None
    # Absolute / relative path already provided
    if os.path.sep in name or (os.path.altsep and os.path.altsep in name):
        return name if os.path.isfile(name) and os.access(name, os.X_OK) else None
    return shutil.which(name)


def materialize_root_private_binary(name: str) -> str | None:
    """If *name* resolves under ``/root`` (mode 700), copy to a world-exec path.

    xAI's install script leaves ``/usr/local/bin/grok`` → ``/root/.grok/...``.
    Privilege-dropped CLI children (``whiskers-claude``) cannot traverse
    ``/root`` and get ``Permission denied``. Returns the path to use (absolute
    when materialised or already public); None if the binary is missing.
    """
    found = which_binary(name)
    if not found:
        return None
    try:
        real = os.path.realpath(found)
    except OSError:
        return found
    # Already outside root home — fine for drop users.
    if not real.startswith("/root" + os.sep) and real != "/root":
        return found
    # Need root to replace a system bin path.
    try:
        if not (hasattr(os, "geteuid") and os.geteuid() == 0):
            return found
    except Exception:
        return found
    base = os.path.basename(found.rstrip(os.sep)) or "cli-bin"
    dest = os.path.join("/usr/local/bin", base)
    tmp = f"{dest}.{os.getpid()}.whiskers-materialize"
    try:
        shutil.copy2(real, tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, dest)
        return dest
    except Exception:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            # Best-effort temp cleanup after copy failure.
            logger.debug("cli.base: temp unlink after copy failure", exc_info=True)
        return found


def env_csv(name: str, default: str = "") -> list[str]:
    """Split a comma-separated env var into non-empty tokens."""
    raw = os.environ.get(name, default) or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def default_timeout_s() -> float:
    """CLI agent timeout from env (seconds)."""
    try:
        return float(os.environ.get("CLI_AGENT_TIMEOUT_S", "300"))
    except ValueError:
        return 300.0


def default_workdir() -> str:
    """Working directory for CLI agents."""
    for key in ("CLI_AGENT_WORKDIR", "CAT_SANDBOX_WORKDIR"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    # repo root: core_graph/goap_agent/cli/base.py → parents[3]
    from pathlib import Path
    return str(Path(__file__).resolve().parents[3])


def default_max_output_bytes() -> int:
    """Stdout/stderr capture cap."""
    try:
        return int(os.environ.get("CLI_AGENT_MAX_OUTPUT_BYTES", "524288"))
    except ValueError:
        return 524288


def max_concurrent() -> int:
    """Max simultaneous CLI subprocesses."""
    try:
        return max(1, int(os.environ.get("CLI_AGENT_MAX_CONCURRENT", "2")))
    except ValueError:
        return 2
