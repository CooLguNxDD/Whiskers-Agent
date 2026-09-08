"""Drop privileges for headless CLI agent subprocesses.

Docker images run the MCP server as root, but Claude Code refuses
``--dangerously-skip-permissions`` when euid==0. We spawn CLI agents as a
dedicated non-root user (default ``whiskers-claude``) via ``preexec_fn``
setuid/setgid so tool auto-approve works headlessly.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core_graph.goap_agent.env_flags import env_truthy

logger = logging.getLogger("whiskers_agent.goap_agent.cli")

DEFAULT_CLI_USER = "whiskers-claude"


@dataclass(frozen=True)
class CliRunUser:
    """Resolved OS account for a CLI subprocess."""

    name: str
    uid: int
    gid: int
    home: str



def configured_cli_user_name() -> str | None:
    """Username from env, or default when privilege-drop is enabled.

    Env:
      - ``CLI_AGENT_USER`` / ``GOAP_AGENT_CLI_USER`` — explicit user (empty = disable)
      - ``CLI_AGENT_DROP_PRIVS`` — default on; set 0 to keep parent identity
    """
    for key in ("CLI_AGENT_USER", "GOAP_AGENT_CLI_USER"):
        raw = os.environ.get(key)
        if raw is not None:
            val = raw.strip()
            return val or None
    if not env_truthy("CLI_AGENT_DROP_PRIVS", default=True):
        return None
    # Default only when we appear to be root (typical Docker).
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return DEFAULT_CLI_USER
    except Exception:
        logger.debug("user_drop.py: swallowed exception", exc_info=True)
    return None


def resolve_cli_run_user(name: str | None = None) -> CliRunUser | None:
    """Look up *name* (or configured default). Return None if unavailable."""
    username = (name if name is not None else configured_cli_user_name()) or ""
    username = username.strip()
    if not username:
        return None
    try:
        import pwd
    except ImportError:
        logger.debug("cli user drop unavailable (no pwd module)")
        return None
    try:
        pw = pwd.getpwnam(username)
    except KeyError:
        logger.warning(
            "CLI agent user %r not found — running as current process identity",
            username,
        )
        return None
    return CliRunUser(
        name=pw.pw_name,
        uid=int(pw.pw_uid),
        gid=int(pw.pw_gid),
        home=str(pw.pw_dir or f"/home/{pw.pw_name}"),
    )


def is_root_uid(uid: int | None = None) -> bool:
    """True when *uid* (default current euid) is 0."""
    if uid is None:
        try:
            return bool(hasattr(os, "geteuid") and os.geteuid() == 0)
        except Exception:
            return False
    return int(uid) == 0


def can_drop_to(user: CliRunUser) -> bool:
    """True when this process can setuid to *user* (root, or already that uid)."""
    try:
        euid = os.geteuid() if hasattr(os, "geteuid") else None
    except Exception:
        return False
    if euid is None:
        return False
    if euid == 0:
        return True
    return euid == user.uid


def make_preexec_fn(user: CliRunUser) -> Callable[[], None] | None:
    """Return a ``preexec_fn`` that setgid/setuid to *user*, or None if no-op."""
    if not can_drop_to(user):
        logger.warning(
            "Cannot drop privileges to %s (euid may lack CAP_SETUID) — spawn as self",
            user.name,
        )
        return None
    try:
        euid = os.geteuid()
    except Exception:
        return None
    if euid == user.uid:
        return None  # already the target user

    def _drop() -> None:
        # Order: groups → gid → uid (can't setgid after setuid without CAP).
        try:
            os.initgroups(user.name, user.gid)
        except Exception:
            logger.debug("user_drop.py: swallowed exception", exc_info=True)
        os.setgid(user.gid)
        os.setuid(user.uid)

    return _drop


def apply_user_env(env: dict[str, str], user: CliRunUser) -> dict[str, str]:
    """Stamp HOME/USER/LOGNAME for the drop user onto a child env copy."""
    out = dict(env)
    out["HOME"] = user.home
    out["USER"] = user.name
    out["LOGNAME"] = user.name
    # Prefer an explicit config dir under the drop user's home when unset.
    if not (out.get("CLAUDE_CONFIG_DIR") or "").strip():
        out["CLAUDE_CONFIG_DIR"] = str(Path(user.home) / ".claude")
    return out


def ensure_user_home(user: CliRunUser) -> None:
    """Create the drop user's home + CLI config dirs if missing (best-effort)."""
    home = Path(user.home)
    config_dirs = (home / ".claude", home / ".grok")
    try:
        home.mkdir(parents=True, exist_ok=True)
        for d in config_dirs:
            d.mkdir(parents=True, exist_ok=True)
        if is_root_uid():
            os.chown(home, user.uid, user.gid)
            for d in config_dirs:
                os.chown(d, user.uid, user.gid)
    except Exception as exc:
        logger.debug("ensure_user_home(%s) failed: %s", user.name, exc)


def chown_path(path: str | Path, user: CliRunUser, *, recursive: bool = False) -> None:
    """Best-effort chown so the drop user can read temp MCP/instruction files."""
    if not is_root_uid():
        return
    p = Path(path)
    try:
        if not p.exists():
            return
        os.chown(p, user.uid, user.gid)
        if recursive and p.is_dir():
            for root, dirs, files in os.walk(p):
                for name in dirs + files:
                    try:
                        os.chown(os.path.join(root, name), user.uid, user.gid)
                    except Exception:
                        logger.debug("user_drop.py: swallowed exception", exc_info=True)
    except Exception as exc:
        logger.debug("chown_path(%s → %s) failed: %s", path, user.name, exc)


def ensure_readable_by_user(path: str | Path, user: CliRunUser) -> None:
    """Chown and chmod a file/dir so the CLI user can read it.

    Also makes each parent under ``/tmp`` (or the path's ancestors that look
    like our ``goap_agent_*`` temp dirs) traversable — ``tempfile.mkdtemp``
    creates ``0o700`` root-owned directories that block the drop user even when
    the leaf file itself is world-readable.
    """
    p = Path(path)
    if not p.exists():
        return
    try:
        # Walk parents first so the leaf is reachable after drop.
        for ancestor in reversed(p.parents):
            name = ancestor.name
            if not name.startswith("goap_agent_"):
                continue
            if not ancestor.exists():
                continue
            chown_path(ancestor, user)
            try:
                os.chmod(ancestor, 0o755)
            except Exception:
                logger.debug("user_drop.py: swallowed exception", exc_info=True)

        chown_path(p, user, recursive=p.is_dir())
        mode = 0o755 if p.is_dir() else 0o644
        # MCP config holds a bearer — keep owner-only after chown to drop user.
        if p.is_file() and p.name == "mcp-config.json":
            mode = 0o600
        os.chmod(p, mode)
    except Exception as exc:
        logger.debug("ensure_readable_by_user(%s) failed: %s", path, exc)


def spawn_kwargs_for_user(user: CliRunUser | None) -> dict[str, Any]:
    """Extra kwargs for ``asyncio.create_subprocess_exec`` (preexec_fn only)."""
    if user is None:
        return {}
    fn = make_preexec_fn(user)
    if fn is None:
        return {}
    return {"preexec_fn": fn}
