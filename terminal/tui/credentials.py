#!/usr/bin/env python3
"""
Interactive credential manager for the Whiskers Agent MCP server.

Presents a rich TUI for selecting a plugin and adding / updating /
deleting its vault-encrypted credentials. Reads required_credentials
and optional_credentials from each plugin's manifest.json to guide
the user, and falls back to free-form custom key entry.

Usage:
    python -m terminal.tui.credentials
    python terminal/script/manage_credentials.py

Prerequisites:
    DATABASE_URL and MASTER_KEY must be set (via .env or environment).
    The DB must be reachable from the host (same as add_credentials.py).
"""

import asyncio
import os
import sys
from pathlib import Path

# Repo root on sys.path before package imports (direct file / importlib load).
_REPO_BOOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_BOOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOT))

from terminal.bootstrap import ensure_import_paths, repo_root  # noqa: E402

ensure_import_paths()

from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    return repo_root()


def _discover_plugins() -> list[dict]:
    """Scan plugins/*/manifest.json and return plugin metadata dicts."""
    from terminal.tui.ui import discover_plugins

    return discover_plugins(_repo_root() / "plugins")


async def _get_vault_status(vault, plugin_id: str, keys: list[str]) -> dict[str, bool]:
    """Return {key: present} for each key (no decrypt needed)."""
    from core.plugin_loader.credentials_loader import normalize_credential_keys

    key_names = normalize_credential_keys(keys)
    stored = set(await vault.list_keys(plugin_id))
    return {k: (k in stored) for k in key_names}


# ---------------------------------------------------------------------------
# Rich UI helpers
# ---------------------------------------------------------------------------

def _build_plugin_table(console, plugins: list[dict], status_map: dict[str, dict[str, bool]]):
    """Render a Rich table listing all plugins with credential status."""
    from terminal.tui.ui import build_plugin_table

    build_plugin_table(console, plugins, status_map)


def _build_key_table(console, plugin: dict, key_status: dict[str, bool]):
    """Render key status for a single plugin."""
    from terminal.tui.ui import build_key_table

    return build_key_table(console, plugin, key_status)


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

async def screen_plugin_picker(console, vault) -> dict | None:
    """Show plugin list and return the chosen plugin dict, or None to quit."""
    from rich.prompt import Prompt
    from terminal.tui.ui import menu

    plugins = _discover_plugins()
    if not plugins:
        console.print("[red]No plugins found under plugins/*/manifest.json[/red]")
        return None

    # Build vault status for all plugins
    all_keys = {}
    for p in plugins:
        all_keys[p["id"]] = p["required"] + p["optional"]

    status_map: dict[str, dict[str, bool]] = {}
    for p in plugins:
        keys = all_keys[p["id"]]
        if keys:
            status_map[p["id"]] = await _get_vault_status(vault, p["id"], keys)
        else:
            status_map[p["id"]] = {}

    console.print()
    _build_plugin_table(console, plugins, status_map)
    items = [(str(i), p["id"]) for i, p in enumerate(plugins, 1)]
    choice = menu(
        console,
        "",
        items,
        extras=[("q", "Quit")],
        include_back=False,
        include_quit=False,
        prompt=f"Pick plugin [1-{len(plugins)}] or [bold]q[/bold] to quit",
    )
    if choice == "q":
        return None
    return plugins[int(choice) - 1]


async def screen_key_manager(console, vault, plugin: dict) -> None:
    """Manage credentials for a single plugin in a loop."""
    from rich.prompt import Prompt, Confirm

    while True:
        console.print()
        # Refresh status
        all_cred_keys = list(dict.fromkeys(plugin["required"] + plugin["optional"]))
        key_status = await _get_vault_status(vault, plugin["id"], all_cred_keys)
        declared_rows = _build_key_table(console, plugin, key_status)
        console.print()

        # Build menu
        options = [str(i) for i in range(1, len(declared_rows) + 1)]
        options += ["c", "d", "b"]

        console.print(
            f"  [bold]c[/bold] – enter custom key name"
            f"  [bold]d[/bold] – delete a key"
            f"  [bold]b[/bold] – back to plugin list"
        )
        action = Prompt.ask(
            "Select key to set [#] or action",
            choices=options,
            show_choices=False,
        )

        if action == "b":
            break

        elif action == "d":
            # Delete menu
            stored_keys = await vault.list_keys(plugin["id"])
            if not stored_keys:
                console.print("[dim]No credentials stored for this plugin.[/dim]")
                continue
            console.print()
            for i, k in enumerate(stored_keys, 1):
                console.print(f"  {i}. {k}")
            del_choices = [str(i) for i in range(1, len(stored_keys) + 1)] + ["c"]
            del_choice = Prompt.ask(
                "Pick key to delete or [bold]c[/bold] to cancel",
                choices=del_choices,
                show_choices=False,
            )
            if del_choice == "c":
                continue
            key_to_del = stored_keys[int(del_choice) - 1]
            if Confirm.ask(f"Delete [bold]{key_to_del}[/bold] from [bold]{plugin['id']}[/bold]?"):
                await vault.delete(plugin["id"], key_to_del)
                console.print(f"[green]✓ Deleted {key_to_del}[/green]")

        elif action == "c":
            # Custom key name
            key_name = Prompt.ask("Key name").strip()
            if not key_name:
                continue
            await _prompt_and_store(console, vault, plugin["id"], key_name)

        else:
            # Declared key by index
            idx = int(action) - 1
            key_name, _ktype, _present = declared_rows[idx]
            await _prompt_and_store(console, vault, plugin["id"], key_name)


async def _prompt_and_store(console, vault, plugin_id: str, key_name: str) -> None:
    """Prompt for a secret value (hidden) and store it in the vault."""
    from rich.prompt import Prompt

    value = Prompt.ask(f"Value for [bold]{key_name}[/bold]", password=True).strip()
    if not value:
        console.print("[dim]Empty value — skipped.[/dim]")
        return
    await vault.set(plugin_id, key_name, value)
    console.print(f"[green]✓ Stored {key_name} for {plugin_id}[/green]")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def _seed_from_files(
    console,
    vault,
    *,
    plugin_id: str | None = None,
    all_plugins: bool = False,
    overwrite: bool = False,
) -> None:
    """Non-interactive: seed vault from credentials.json for one or all plugins."""
    from core.plugin_loader.credentials_loader import seed_vault_from_credentials

    plugins = _discover_plugins()
    if not plugins:
        console.print("[red]No plugins found.[/red]")
        return

    targets = plugins if all_plugins else [p for p in plugins if p["id"] == plugin_id]
    if not targets:
        available = ", ".join(p["id"] for p in plugins)
        console.print(f"[red]Plugin '{plugin_id}' not found. Available: {available}[/red]")
        return

    for p in targets:
        plugin_dir = p["dir"]
        cred_path = plugin_dir / "credentials.json"
        if not cred_path.exists():
            console.print(f"  [dim]{p['id']}: no credentials.json — skipping[/dim]")
            continue

        result = await seed_vault_from_credentials(
            plugin_dir, p["id"], vault, overwrite=overwrite
        )
        icon = "[green]✓[/green]" if result.ok else "[red]✗[/red]"
        console.print(f"  {icon} {p['id']}: {result.summary()}")


async def main() -> None:
    """Entry point: guard env, parse args, then run the appropriate mode."""
    import argparse
    from terminal.tui.theme import get_console

    parser = argparse.ArgumentParser(
        description="Whiskers Agent interactive credential manager.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m terminal.tui.credentials                # interactive TUI\n"
            "  python -m terminal.tui.credentials --from-file --plugin-id pro_plugin\n"
            "  python -m terminal.tui.credentials --from-file --all-plugins\n"
            "  python -m terminal.tui.credentials --from-file --all-plugins --overwrite"
        ),
    )
    parser.add_argument(
        "--from-file",
        action="store_true",
        dest="from_file",
        help="Non-interactive: seed vault from each plugin's credentials.json.",
    )
    parser.add_argument(
        "--plugin-id",
        metavar="ID",
        help="(--from-file) Seed only this plugin.",
    )
    parser.add_argument(
        "--all-plugins",
        action="store_true",
        dest="all_plugins",
        help="(--from-file) Seed all discovered plugins.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="(--from-file) Overwrite existing vault entries.",
    )
    args = parser.parse_args()

    console = get_console()

    db_url = os.environ.get("DATABASE_URL", "").strip()
    master_key = os.environ.get("MASTER_KEY", "").strip()
    if not db_url:
        console.print("[red]Error: DATABASE_URL is not set.[/red]")
        sys.exit(1)
    if not master_key:
        console.print("[red]Error: MASTER_KEY is not set.[/red]")
        sys.exit(1)

    from core.context import vault

    if args.from_file:
        if not args.plugin_id and not args.all_plugins:
            console.print("[red]Specify --plugin-id <ID> or --all-plugins with --from-file.[/red]")
            sys.exit(1)
        console.print("[heading]Whiskers Agent — Seed vault from credentials.json[/heading]")
        await _seed_from_files(
            console,
            vault,
            plugin_id=args.plugin_id,
            all_plugins=args.all_plugins,
            overwrite=args.overwrite,
        )
        return

    # Interactive TUI mode (default)
    console.print("[heading]Whiskers Agent Credential Manager[/heading]")
    console.print("Credentials are encrypted with pgcrypto (pgp_sym_encrypt) in Postgres.")

    while True:
        try:
            plugin = await screen_plugin_picker(console, vault)
            if plugin is None:
                console.print("[dim]Bye.[/dim]")
                break
            await screen_key_manager(console, vault, plugin)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelled.[/dim]")
            break


if __name__ == "__main__":
    load_dotenv(repo_root() / ".env")
    asyncio.run(main())
