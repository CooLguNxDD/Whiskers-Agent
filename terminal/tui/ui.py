"""Shared rich TUI helpers for setup and credential screens.

Every interactive helper calls ``rich.prompt.Prompt.ask`` / ``Confirm.ask``
so existing ``patch.object(setup_tui.Prompt, "ask")`` tests keep working
(the class method is shared).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

GLYPH: dict[str, str] = {
    "ok": "✓",
    "fail": "✗",
    "warn": "⚠",
    "arrow": "→",
}
_ASCII: dict[str, str] = {
    "ok": "[OK]",
    "fail": "[X]",
    "warn": "[!]",
    "arrow": "->",
}

_STATUS_TTL_S = 5.0
_status_cache: dict[str, Any] = {"t": 0.0, "line": None}


def glyph(console: Console | None, name: str) -> str:
    """Return a status glyph, falling back to ASCII when the console encoding cannot encode it."""
    char = GLYPH.get(name, name)
    encoding = "utf-8"
    if console is not None:
        raw = getattr(console, "encoding", None)
        if isinstance(raw, str) and raw:
            encoding = raw
    try:
        char.encode(encoding)
        return char
    except (LookupError, UnicodeEncodeError, TypeError):
        return _ASCII.get(name, name)


def breadcrumb(stack: list[str]) -> str:
    """Render a navigation trail such as ``Home > Live Settings > LLM Pool``."""
    return " > ".join(part for part in stack if part)


def menu(
    console: Console,
    title: str,
    items: list[tuple[str, str]],
    extras: list[tuple[str, str]] | None = None,
    *,
    prompt: str = "Select",
    include_back: bool = True,
    include_quit: bool = False,
) -> str:
    """Print a numbered menu plus letter actions and return the chosen key.

    Always offers ``b`` (back) and/or ``q`` (quit) unless already in extras.
    """
    console.print()
    if title:
        console.print(f"[bold]{title}[/bold]")
    keys: list[str] = []
    for key, label in items:
        console.print(f"  [bold]{key}[/bold]. {label}")
        keys.append(key)
    extra_keys: list[str] = []
    for key, label in extras or []:
        console.print(f"  [bold]{key}[/bold]. {label}")
        extra_keys.append(key)
    if include_back and "b" not in extra_keys and "b" not in keys:
        console.print("  [bold]b[/bold]. Back")
        extra_keys.append("b")
    if include_quit and "q" not in extra_keys and "q" not in keys:
        console.print("  [bold]q[/bold]. Quit")
        extra_keys.append("q")
    choices = keys + extra_keys
    return Prompt.ask(prompt, choices=choices, show_choices=False)


def confirm_discard(dirty: bool, prompt: str = "Unsaved changes — save now?") -> bool:
    """Ask whether to save pending edits. Returns False when nothing is dirty."""
    if not dirty:
        return False
    return Confirm.ask(prompt)


def run_with_progress(console: Console, label: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run ``fn`` under a rich status spinner and print elapsed seconds."""
    started = time.monotonic()
    with console.status(f"{label}..."):
        result = fn(*args, **kwargs)
    elapsed = time.monotonic() - started
    console.print(f"  [dim]{label} ({elapsed:.1f}s)[/dim]")
    return result


def _normalize_credential_keys(raw) -> list[str]:
    """Same contract as ``core.plugin_loader.credentials_loader.normalize_credential_keys``.

    Implemented here so the TUI does not import ``core`` (FastMCP) at menu time.
    """
    if not raw or not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name: str | None = None
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            key = item.get("key") or item.get("name") or item.get("id")
            if isinstance(key, str):
                name = key.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def discover_plugins(plugins_dir: Path) -> list[dict]:
    """Scan ``plugins/*/manifest.json`` and return plugin credential metadata.

    Each dict: ``{id, dir, dir_name, required, optional}``. Credential lists
    accept string or ``{key, description}`` object form.
    """
    result: list[dict] = []
    if not plugins_dir.is_dir():
        return result
    for manifest_path in sorted(plugins_dir.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        result.append(
            {
                "id": manifest.get("name", manifest_path.parent.name),
                "dir": manifest_path.parent,
                "dir_name": manifest_path.parent.name,
                "required": _normalize_credential_keys(
                    manifest.get("required_credentials", [])
                ),
                "optional": _normalize_credential_keys(
                    manifest.get("optional_credentials", [])
                ),
            }
        )
    return result


def build_plugin_table(
    console: Console,
    plugins: list[dict],
    status_map: dict[str, dict[str, bool]],
) -> None:
    """Render plugins with required/optional credential status."""
    table = Table(title="Plugin Credentials", show_lines=True)
    table.add_column("#", style="bold cyan", justify="right", width=3)
    table.add_column("Plugin", style="bold")
    table.add_column("Required", justify="center")
    table.add_column("Optional", justify="center")
    table.add_column("Status")
    ok_g = glyph(console, "ok")
    fail_g = glyph(console, "fail")
    warn_g = glyph(console, "warn")

    for i, plugin in enumerate(plugins, 1):
        pid = plugin["id"]
        req = plugin["required"]
        opt = plugin["optional"]
        pstatus = status_map.get(pid, {})

        if req:
            missing = [k for k in req if not pstatus.get(k)]
            if missing:
                req_summary = f"[red]{fail_g} {len(missing)}/{len(req)} missing[/red]"
            else:
                req_summary = f"[green]{ok_g} {len(req)}/{len(req)}[/green]"
        else:
            req_summary = "[dim]none[/dim]"

        if opt:
            present = sum(1 for k in opt if pstatus.get(k))
            color = "green" if present else "dim"
            opt_summary = f"[{color}]{present}/{len(opt)} set[/{color}]"
        else:
            opt_summary = "[dim]none[/dim]"

        all_ok = not req or not [k for k in req if not pstatus.get(k)]
        status_icon = (
            f"[green]{ok_g} ready[/green]" if all_ok else f"[yellow]{warn_g} incomplete[/yellow]"
        )
        table.add_row(str(i), pid, req_summary, opt_summary, status_icon)

    console.print(table)


def build_key_table(
    console: Console,
    plugin: dict,
    key_status: dict[str, bool],
) -> list[tuple[str, str, bool]]:
    """Render key status for one plugin; return ``[(key, type, present), ...]``."""
    table = Table(title=f"[bold]{plugin['id']}[/bold] credentials", show_lines=True)
    table.add_column("#", style="bold cyan", justify="right", width=3)
    table.add_column("Key", style="bold")
    table.add_column("Type", justify="center")
    table.add_column("Stored", justify="center")
    ok_g = glyph(console, "ok")
    fail_g = glyph(console, "fail")

    rows: list[tuple[str, str, bool]] = []
    for k in plugin["required"]:
        rows.append((k, "required", key_status.get(k, False)))
    for k in plugin["optional"]:
        if k not in plugin["required"]:
            rows.append((k, "optional", key_status.get(k, False)))

    for i, (key, ktype, present) in enumerate(rows, 1):
        stored_str = (
            f"[green]{ok_g} stored[/green]" if present else f"[red]{fail_g} missing[/red]"
        )
        type_str = "[red]required[/red]" if ktype == "required" else "[dim]optional[/dim]"
        table.add_row(str(i), key, type_str, stored_str)

    console.print(table)
    return rows


def _docker_daemon_ok() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=8,
        )
        return result.returncode == 0
    except Exception:
        return False


def collect_status_row(
    *,
    repo: Path,
    db_available: Callable[[], bool],
    vault_available: Callable[[], bool],
    check_mcp: Callable[..., bool],
    check_llm: Callable[..., Any] | None = None,
    force: bool = False,
) -> str:
    """Build a one-line status bar, cached for ``_STATUS_TTL_S`` seconds.

    The underlying probes (``docker info``, a DB round-trip, an MCP poll, an
    LLM check) are slow enough to make every redraw of the main menu block on
    them; cache the assembled line and only re-probe once the TTL lapses or
    ``force`` is passed (e.g. right after a step that changes state).
    """
    now = time.monotonic()
    if (
        not force
        and _status_cache["line"] is not None
        and now - float(_status_cache["t"]) < _STATUS_TTL_S
    ):
        return _status_cache["line"]

    line = _build_status_row(
        repo=repo,
        db_available=db_available,
        vault_available=vault_available,
        check_mcp=check_mcp,
        check_llm=check_llm,
    )
    _status_cache["t"] = now
    _status_cache["line"] = line
    return line


def _build_status_row(
    *,
    repo: Path,
    db_available: Callable[[], bool],
    vault_available: Callable[[], bool],
    check_mcp: Callable[..., bool],
    check_llm: Callable[..., Any] | None = None,
) -> str:
    """Actually run the status probes (uncached)."""
    env_ok = (repo / ".env").is_file()
    docker_ok = _docker_daemon_ok()
    db_ok = db_available()
    vault_ok = vault_available()
    try:
        mcp_ok = bool(check_mcp(timeout_s=1))
    except TypeError:
        mcp_ok = bool(check_mcp())
    except Exception:
        mcp_ok = False
    llm_ok = False
    if check_llm is not None:
        try:
            import asyncio

            mapping = check_llm()
            if asyncio.iscoroutine(mapping):
                mapping = asyncio.run(mapping)
            llm_ok = bool(mapping)
        except Exception:
            llm_ok = False
    theme = (os.environ.get("WHISKERS_TUI_THEME") or "mocha").strip() or "mocha"

    def cell(label: str, ok: bool) -> str:
        mark = glyph(None, "ok" if ok else "fail")
        style = "ok" if ok else "danger"
        return f"[{style}]{label} {mark}[/{style}]"

    parts = [
        cell("docker", docker_ok),
        cell(".env", env_ok),
        cell("db", db_ok),
        cell("vault", vault_ok),
        cell("server", mcp_ok),
        cell("llm", llm_ok),
        f"[muted]theme {theme}[/muted]",
    ]
    return "  ".join(parts)


def status_header(console: Console, line: str | None = None) -> None:
    """Print the status bar line. ``collect_status_row`` owns the TTL cache."""
    if line is None:
        line = _status_cache["line"]
    if line is not None:
        console.print(line)


def invalidate_status_cache() -> None:
    """Drop the cached status-header line."""
    _status_cache["line"] = None
    _status_cache["t"] = 0.0
