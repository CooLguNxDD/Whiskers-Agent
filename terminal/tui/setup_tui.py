"""Master setup TUI for Whiskers Agent MCP Server — rich interactive hub.

Wraps guided setup, JSON config editor, plugin boot-list manager, vault
credential manager, and live DB settings into a single rich menu.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Repo root on sys.path before package imports (direct file / importlib load).
_REPO_BOOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_BOOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOT))

from terminal.bootstrap import ensure_import_paths  # noqa: E402


# --- sys.path bootstrap (before any project imports) ---
_REPO, _SCRIPTS_DIR = ensure_import_paths()

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO / ".env")

from rich.console import Console  # noqa: E402
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt  # noqa: E402
from rich.table import Table  # noqa: E402

from terminal.tui.theme import get_console, screen_theme_switcher  # noqa: E402
from terminal.tui.ui import (  # noqa: E402
    breadcrumb,
    confirm_discard,
    discover_plugins,
    glyph,
    menu,
    run_with_progress,
    status_header,
)

import setup as _setup  # noqa: E402  (terminal/script/setup.py on sys.path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_available() -> bool:
    """Return True when DATABASE_URL is set in environment."""
    return bool(os.environ.get("DATABASE_URL", "").strip())


def _vault_available() -> bool:
    """Return True when both DATABASE_URL and MASTER_KEY are set."""
    return _db_available() and bool(os.environ.get("MASTER_KEY", "").strip())


def _is_editable_leaf(val: object) -> bool:
    """Return True for leaf values the TUI can edit inline."""
    if isinstance(val, bool):
        return True
    if isinstance(val, (int, float)):
        return True
    if isinstance(val, str) and len(val) <= 120 and "\n" not in val:
        return True
    if isinstance(val, list) and all(isinstance(x, str) for x in val):
        return True
    return False


def _cast_value(original: object, raw: str) -> object:
    """Cast raw string back to the type of original."""
    if isinstance(original, bool):
        return raw.lower() in ("true", "1", "yes", "on")
    if isinstance(original, int):
        return int(raw)
    if isinstance(original, float):
        return float(raw)
    if isinstance(original, list):
        return [x.strip() for x in raw.split(",") if x.strip()]
    return raw


def _default_args() -> argparse.Namespace:
    """Build a throwaway args namespace compatible with setup.py run_* functions."""
    return argparse.Namespace(
        yes=False,
        dev=False,
        provider=None,
        dry_run=False,
        force=False,
        from_step=None,
        reset_plugins=False,
    )


def _step_status_label(step: str) -> str:
    """Return ``[done]`` / ``[failed]`` / ``[pending]`` from the resume state file."""
    rec = (_setup.load_setup_state().get("steps") or {}).get(step) or {}
    status = rec.get("status")
    if status == "ok":
        return "[done]"
    if status == "failed":
        return "[failed]"
    if status == "skipped":
        return "[skipped]"
    return "[pending]"


# ---------------------------------------------------------------------------
# Screen 1: Guided setup
# ---------------------------------------------------------------------------

def screen_guided_setup(console: Console) -> None:
    """Interactive submenu that dispatches to setup.py run_* functions."""
    steps = {
        "1": ("doctor", lambda a: _setup.run_doctor()),
        "2": ("env", lambda a: _setup.run_env(a)),
        "3": ("llm", lambda a: _setup.run_llm(a)),
        "4": ("db", lambda a: _setup.run_db(a)),
        "5": ("migrate", lambda a: _setup.run_migrations()),
        "6": ("plugin_migrate", lambda a: _setup.run_plugin_migrate(a)),
        "7": ("admin", lambda a: _setup.run_admin(a)),
        "8": ("plugins", lambda a: _setup.run_plugins(a)),
        "9": ("up", lambda a: _setup.run_up(a)),
        "10": ("health", lambda a: _setup.run_health(a)),
    }

    while True:
        items = [
            (key, f"{name}  {_step_status_label(name)}")
            for key, (name, _) in steps.items()
        ]
        choice = menu(
            console,
            "Guided Setup  (each step runs immediately)",
            items,
            extras=[("a", "Run all steps"), ("r", "Resume (skip [done])")],
        )
        if choice == "b":
            break

        args = _default_args()

        if choice in ("a", "r"):
            args.yes = True  # do not re-enter this TUI from the pipeline's tui step
            args.force = choice == "a"
            rc = _setup.run_all(args)
            if rc and rc != 0:
                console.print(f"[red]Pipeline returned exit code {rc}[/red]")
        else:
            name, fn = steps[choice]
            console.print(f"  [dim]{glyph(console, 'arrow')} {name}[/dim]")
            # Only steps with no child-process stdout of their own get the spinner —
            # db/up/migrate/plugin_migrate shell out to docker/alembic and their
            # output would interleave badly inside a Live-rendered status region.
            spinner_steps = {"health"}
            if name in spinner_steps:
                rc = run_with_progress(console, name, fn, args)
            else:
                rc = fn(args)
            if rc and rc != 0:
                console.print(f"[red]Step '{name}' returned exit code {rc}[/red]")


# ---------------------------------------------------------------------------
# Screen 2: Server config editor
# ---------------------------------------------------------------------------

_CONFIG_FILES = {
    "1": ("server_config.json",   _REPO / "config" / "server_config.json"),
    "2": ("tools_api_config.json", _REPO / "config" / "tools_api_config.json"),
    "3": ("embedding_config.json", _REPO / "config" / "embedding_config.json"),
    "4": ("plugin_config.json",    _REPO / "config" / "plugin_config.json"),
}


def screen_server_config(console: Console) -> None:
    """Interactive JSON editor for server config files."""
    while True:
        items = []
        for key, (name, path) in _CONFIG_FILES.items():
            exists = glyph(console, "ok") if path.is_file() else f"{glyph(console, 'fail')} (missing)"
            items.append((key, f"{name}  [dim]{exists}[/dim]"))
        choice = menu(console, "Server Config", items, prompt="Select config file")
        if choice == "b":
            break
        name, path = _CONFIG_FILES[choice]
        if not path.is_file():
            console.print(f"[red]File not found: {path}[/red]")
            continue
        _edit_json_file(console, name, path)


def _edit_json_file(console: Console, label: str, path: Path) -> None:
    """Drill-down editor for a JSON config file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        console.print(f"[red]Failed to load config '{label}': {e}[/red]")
        return
    if not isinstance(data, dict):
        console.print("[red]Top-level value is not a JSON object — edit file directly.[/red]")
        return

    sections = {str(i + 1): k for i, k in enumerate(data.keys())}
    choices = list(sections.keys()) + ["b"]
    dirty = False

    def _save() -> None:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        console.print(f"[green]{glyph(console, 'ok')} Saved {label}[/green]")
        console.print(
            f"[yellow]{glyph(console, 'warn')} Restart required — config is read at import time.[/yellow]"
        )

    try:
        while True:
            console.print()
            console.print(f"[bold]{label}[/bold] — sections")
            t = Table(show_header=True)
            t.add_column("#")
            t.add_column("Section")
            t.add_column("Keys", justify="right")
            for key, section in sections.items():
                val = data[section]
                count = len(val) if isinstance(val, dict) else "—"
                t.add_row(key, section, str(count))
            console.print(t)
            console.print("  [bold]b[/bold]. Back" + (" (unsaved changes)" if dirty else ""))
            choice = Prompt.ask("Select section", choices=choices, show_choices=False)
            if choice == "b":
                if confirm_discard(dirty, "Save changes?"):
                    _save()
                break
            section_key = sections[choice]
            section_val = data[section_key]
            if not isinstance(section_val, dict):
                if _is_editable_leaf(section_val):
                    new_val = _prompt_leaf(console, section_key, section_val)
                    if new_val is not None and new_val != section_val:
                        data[section_key] = new_val
                        dirty = True
                        console.print(f"[green]{glyph(console, 'ok')} {section_key} = {new_val!r}[/green]")
                else:
                    console.print(f"[dim]Section '{section_key}' is a {type(section_val).__name__} — edit file directly.[/dim]")
                continue
            changed = _edit_section(console, section_key, section_val)
            if changed:
                dirty = True
    except KeyboardInterrupt:
        if confirm_discard(dirty, "Interrupted — save changes?"):
            _save()
        raise


def _edit_section(console: Console, section: str, obj: dict) -> bool:
    """Edit leaf fields of a config section; return True if anything changed."""
    leaf_keys = list(obj.keys())
    choices = [str(i + 1) for i in range(len(leaf_keys))] + ["b"]
    changed = False

    while True:
        console.print()
        console.print(f"[bold]{section}[/bold]")
        t = Table(show_header=True)
        t.add_column("#")
        t.add_column("Key")
        t.add_column("Value")
        t.add_column("Editable")
        for i, k in enumerate(leaf_keys):
            v = obj[k]
            editable = glyph(console, "ok") if _is_editable_leaf(v) else "—"
            display = repr(v) if not isinstance(v, str) else f'"{v[:60]}..."' if len(v) > 60 else f'"{v}"'
            t.add_row(str(i + 1), k, display, editable)
        console.print(t)
        console.print("  [bold]b[/bold]. Back")
        choice = Prompt.ask("Select key to edit", choices=choices, show_choices=False)
        if choice == "b":
            break
        idx = int(choice) - 1
        key = leaf_keys[idx]
        original = obj[key]
        if not _is_editable_leaf(original):
            console.print(f"[dim]'{key}' is a {type(original).__name__} — edit the JSON file directly.[/dim]")
            continue
        new_val = _prompt_leaf(console, key, original)
        if new_val is not None and new_val != original:
            obj[key] = new_val
            changed = True
            console.print(f"[green]{glyph(console, 'ok')} {key} = {new_val!r}[/green]")
    return changed


def _prompt_leaf(console: Console, key: str, original: object) -> object | None:
    """Prompt for a new value; return None to skip."""
    try:
        if isinstance(original, bool):
            return Confirm.ask(f"  {key}", default=original)
        if isinstance(original, int):
            return IntPrompt.ask(f"  {key}", default=original)
        if isinstance(original, float):
            return FloatPrompt.ask(f"  {key}", default=original)
        if isinstance(original, list):
            current = ", ".join(original)
            raw = Prompt.ask(f"  {key} (comma-separated)", default=current)
            return _cast_value(original, raw)
        # short str
        raw = Prompt.ask(f"  {key}", default=original)
        return raw
    except (KeyboardInterrupt, EOFError):
        return None


# ---------------------------------------------------------------------------
# Screen 3: Plugins (boot list)
# ---------------------------------------------------------------------------

_PLUGIN_CONFIG_PATH = _REPO / "config" / "plugin_config.json"


def screen_plugins(console: Console) -> None:
    """Enable/disable plugins in the boot list and mirror to DB."""
    all_plugins = _setup.list_available_plugins()
    if not all_plugins:
        console.print("[yellow]No plugins found in plugins/ directory.[/yellow]")
        return

    # Load current boot list
    enabled: list[str] = []
    tier: int = 100
    if _PLUGIN_CONFIG_PATH.is_file():
        try:
            cfg = json.loads(_PLUGIN_CONFIG_PATH.read_text(encoding="utf-8"))
            tier = cfg.get("tier", 100)
            raw_plugins = cfg.get("plugins", [])
            # strip "plugins." prefix for display
            enabled = [p.split(".")[-1] if "." in p else p for p in raw_plugins]
        except (OSError, json.JSONDecodeError):
            pass

    all_names = [p["name"] for p in all_plugins]
    enabled_set = set(enabled)
    dirty = False

    choices_map = {str(i + 1): p["name"] for i, p in enumerate(all_plugins)}
    choices = list(choices_map.keys()) + ["t", "e", "s", "b"]

    def _save_boot_list() -> None:
        enabled_list = [n for n in all_names if n in enabled_set]
        _setup.write_plugin_boot_list(enabled_list, tier, _PLUGIN_CONFIG_PATH)
        console.print(
            f"[green]{glyph(console, 'ok')} Wrote plugin_config.json "
            f"(tier={tier}, {len(enabled_list)} plugins)[/green]"
        )

    try:
        while True:
            console.print()
            console.print(f"[bold]Plugins[/bold]  (tier={tier})")
            t = Table(show_header=True)
            t.add_column("#")
            t.add_column("Plugin")
            t.add_column("Enabled")
            t.add_column("Tier")
            t.add_column("Requires")
            for i, p in enumerate(all_plugins):
                enabled_str = (
                    f"[green]{glyph(console, 'ok')}[/green]"
                    if p["name"] in enabled_set
                    else f"[dim]{glyph(console, 'fail')}[/dim]"
                )
                t.add_row(
                    str(i + 1),
                    p["name"],
                    enabled_str,
                    str(p.get("tier") or "—"),
                    ", ".join(p.get("requires") or []) or "—",
                )
            console.print(t)
            console.print("  [bold]t[/bold]. Toggle enable/disable")
            console.print("  [bold]e[/bold]. Edit default tier")
            console.print("  [bold]s[/bold]. Save and apply")
            console.print("  [bold]b[/bold]. Back")
            choice = Prompt.ask("Action", choices=choices, show_choices=False)

            if choice == "b":
                break
            elif choice == "t":
                num = Prompt.ask("Plugin # to toggle", choices=list(choices_map.keys()), show_choices=False)
                name = choices_map[num]
                if name in enabled_set:
                    enabled_set.discard(name)
                    console.print(f"[dim]Disabled {name}[/dim]")
                else:
                    enabled_set.add(name)
                    console.print(f"[green]Enabled {name}[/green]")
                dirty = True
            elif choice == "e":
                tier = IntPrompt.ask("Default tier", default=tier)
                dirty = True
            elif choice == "s":
                _save_boot_list()
                enabled_list = [n for n in all_names if n in enabled_set]
                if _db_available():
                    result = asyncio.run(_setup.mirror_plugin_active(enabled_list, all_names))
                    if result.get("skipped"):
                        console.print("[dim]  DB mirror skipped (DATABASE_URL not set)[/dim]")
                    else:
                        console.print(f"  [green]{glyph(console, 'ok')} DB active flags updated[/green]")
                else:
                    console.print("[dim]  DB mirror skipped (DATABASE_URL not set)[/dim]")
                dirty = False
            elif choice in choices_map:
                name = choices_map[choice]
                if name in enabled_set:
                    enabled_set.discard(name)
                    console.print(f"[dim]Disabled {name}[/dim]")
                else:
                    enabled_set.add(name)
                    console.print(f"[green]Enabled {name}[/green]")
                dirty = True

        if confirm_discard(dirty, "Unsaved changes — save now?"):
            _save_boot_list()
            console.print(f"[green]{glyph(console, 'ok')} Saved[/green]")
    except KeyboardInterrupt:
        if confirm_discard(dirty, "Interrupted — save changes?"):
            _save_boot_list()
        raise


# ---------------------------------------------------------------------------
# Screen 4a: Local credentials (credentials.json files, no vault needed)
# ---------------------------------------------------------------------------

def _discover_plugins_local() -> list[dict]:
    """Scan plugins/*/manifest.json; thin wrapper over ``ui.discover_plugins``."""
    return discover_plugins(_REPO / "plugins")


def _load_creds_file(plugin_dir: Path) -> dict:
    """Load credentials.json from a plugin dir; return empty dict if absent/invalid."""
    cred_path = plugin_dir / "credentials.json"
    if not cred_path.exists():
        return {}
    try:
        data = json.loads(cred_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_creds_file(plugin_dir: Path, data: dict) -> None:
    """Write credentials.json to a plugin dir."""
    cred_path = plugin_dir / "credentials.json"
    cred_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def screen_local_credentials(console: Console) -> None:
    """Edit per-plugin credentials.json files directly (no vault/DB required)."""
    plugins = _discover_plugins_local()
    if not plugins:
        console.print("[yellow]No plugins found in plugins/ directory.[/yellow]")
        return

    choices_map = {str(i + 1): p for i, p in enumerate(plugins)}
    choices = list(choices_map.keys()) + ["b"]

    while True:
        console.print()
        console.print("[bold]Local Credentials (credentials.json)[/bold]")
        console.print("[dim]These files are gitignored. Use vault for production secrets.[/dim]")
        t = Table(show_header=True)
        t.add_column("#", style="accent", width=3)
        t.add_column("Plugin")
        t.add_column("File", justify="center")
        t.add_column("Keys set", justify="right")
        t.add_column("Required", justify="center")

        for i, p in enumerate(plugins, 1):
            creds = _load_creds_file(p["dir"])
            # Filter out comment keys
            real_keys = {k: v for k, v in creds.items() if not k.startswith("_") and v}
            file_exists = (p["dir"] / "credentials.json").exists()
            file_str = "[green]✓ exists[/green]" if file_exists else "[dim]✗ missing[/dim]"
            req = p["required"]
            missing_req = [k for k in req if not real_keys.get(k)]
            req_str = (
                f"[green]✓ {len(req)}/{len(req)}[/green]" if req and not missing_req
                else f"[red]✗ {len(missing_req)} missing[/red]" if missing_req
                else "[dim]none[/dim]"
            )
            t.add_row(str(i), p["id"], file_str, str(len(real_keys)), req_str)
        console.print(t)
        console.print("  [bold]b[/bold]. Back")

        choice = Prompt.ask(
            f"Select plugin [1-{len(plugins)}] or [bold]b[/bold] to go back",
            choices=choices, show_choices=False,
        )
        if choice == "b":
            break
        plugin = choices_map[choice]
        _screen_local_plugin_creds(console, plugin)


def _screen_local_plugin_creds(console: Console, plugin: dict) -> None:
    """Edit credentials.json for a single plugin."""
    plugin_dir: Path = plugin["dir"]
    required: list[str] = plugin["required"]
    optional: list[str] = plugin["optional"]
    all_declared = list(dict.fromkeys(required + optional))  # preserve order, dedupe

    while True:
        creds = _load_creds_file(plugin_dir)
        real_creds = {k: v for k, v in creds.items() if not k.startswith("_")}

        console.print()
        console.print(f"[bold]{plugin['id']}[/bold] — credentials.json")
        t = Table(show_header=True)
        t.add_column("#", style="accent", width=3)
        t.add_column("Key")
        t.add_column("Type", justify="center")
        t.add_column("Set", justify="center")

        rows: list[tuple[str, str]] = []  # (key, type)
        for k in all_declared:
            ktype = "required" if k in required else "optional"
            rows.append((k, ktype))
        # Also show any extra keys already in file
        for k in real_creds:
            if k not in all_declared:
                rows.append((k, "custom"))

        for i, (k, ktype) in enumerate(rows, 1):
            set_str = "[green]✓ set[/green]" if real_creds.get(k) else "[dim]✗ empty[/dim]"
            type_str = (
                "[red]required[/red]" if ktype == "required"
                else "[dim]optional[/dim]" if ktype == "optional"
                else "[cyan]custom[/cyan]"
            )
            t.add_row(str(i), k, type_str, set_str)
        console.print(t)

        idx_choices = [str(i) for i in range(1, len(rows) + 1)]
        console.print("  [bold]a[/bold]. Add/edit a custom key")
        console.print("  [bold]d[/bold]. Delete a key")
        console.print("  [bold]s[/bold]. Scaffold from template (credentials_example.json)")
        vault_note = "[green](vault available)[/green]" if _vault_available() else "[dim](vault unavailable — needs DATABASE_URL + MASTER_KEY)[/dim]"
        console.print(f"  [bold]v[/bold]. Push to vault {vault_note}")
        console.print("  [bold]b[/bold]. Back")
        action_choices = idx_choices + ["a", "d", "s", "v", "b"]
        action = Prompt.ask("Select key [#] or action", choices=action_choices, show_choices=False)

        if action == "b":
            break

        elif action == "a":
            key_name = Prompt.ask("Key name").strip()
            if not key_name:
                continue
            value = Prompt.ask(f"Value for [bold]{key_name}[/bold]", password=True).strip()
            if value:
                creds[key_name] = value
                _save_creds_file(plugin_dir, creds)
                console.print(f"[green]✓ Saved {key_name}[/green]")

        elif action == "d":
            if not real_creds:
                console.print("[dim]No credentials set yet.[/dim]")
                continue
            del_choices = [str(i) for i in range(1, len(rows) + 1)] + ["c"]
            del_choice = Prompt.ask(
                "Pick key # to delete or [bold]c[/bold] to cancel",
                choices=del_choices, show_choices=False,
            )
            if del_choice == "c":
                continue
            key_to_del, _ = rows[int(del_choice) - 1]
            if Confirm.ask(f"Delete [bold]{key_to_del}[/bold]?"):
                creds.pop(key_to_del, None)
                _save_creds_file(plugin_dir, creds)
                console.print(f"[green]✓ Deleted {key_to_del}[/green]")

        elif action == "s":
            # Scaffold from credentials_example.json
            example_path = plugin_dir / "credentials_example.json"
            if not example_path.exists():
                console.print("[yellow]No credentials_example.json found. Run --scaffold-credentials first.[/yellow]")
                continue
            try:
                example = json.loads(example_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                console.print(f"[red]Cannot read example file: {e}[/red]")
                continue
            seeded = 0
            for k, v in example.items():
                if k.startswith("_"):
                    continue
                if k not in creds or not creds[k]:
                    creds[k] = ""
                    seeded += 1
            _save_creds_file(plugin_dir, creds)
            console.print(f"[green]✓ Seeded {seeded} key(s) from template. Edit values above.[/green]")

        elif action == "v":
            # Push credentials.json → vault
            if not _vault_available():
                console.print("[red]Vault unavailable — set DATABASE_URL and MASTER_KEY in .env first.[/red]")
                continue

            if not real_creds:
                console.print("[dim]No credentials set in credentials.json — nothing to push.[/dim]")
                continue

            overwrite_vault = Confirm.ask(
                "Overwrite existing vault entries for keys already present?",
                default=False,
            )

            async def _push_to_vault() -> None:
                from core.context import vault as _vault  # noqa: PLC0415
                from core.plugin_loader.credentials_loader import seed_vault_from_credentials  # noqa: PLC0415
                import json as _json  # noqa: PLC0415
                _manifest_path = plugin_dir / "manifest.json"
                _pid = plugin["id"]
                if _manifest_path.exists():
                    try:
                        _pid = _json.loads(_manifest_path.read_text(encoding="utf-8")).get("name", _pid)
                    except (OSError, _json.JSONDecodeError):
                        pass
                return await seed_vault_from_credentials(
                    plugin_dir, _pid, _vault, overwrite=overwrite_vault
                )

            try:
                seed_res = asyncio.run(_push_to_vault())
                if seed_res.seeded:
                    console.print(f"[green]✓ Pushed to vault: {', '.join(seed_res.seeded)}[/green]")
                if seed_res.skipped:
                    console.print(f"[dim]  Already in vault (skipped): {', '.join(seed_res.skipped)}[/dim]")
                if seed_res.empty:
                    console.print(f"[yellow]  Empty/blank (skipped): {', '.join(seed_res.empty)}[/yellow]")
                if seed_res.errors:
                    console.print(f"[red]  Errors: {', '.join(seed_res.errors)}[/red]")
                if not seed_res.seeded and not seed_res.errors:
                    console.print("[dim]Nothing new pushed.[/dim]")
            except Exception as _e:  # noqa: BLE001
                console.print(f"[red]Vault push failed: {_e}[/red]")

        else:
            # Direct key edit by index
            idx = int(action) - 1
            key_name, _ = rows[idx]
            value = Prompt.ask(f"Value for [bold]{key_name}[/bold]", password=True).strip()
            if value:
                creds[key_name] = value
                _save_creds_file(plugin_dir, creds)
                console.print(f"[green]✓ Saved {key_name}[/green]")
            else:
                console.print("[dim]Empty value — skipped.[/dim]")


def screen_credentials(console: Console) -> None:
    """Vault credential manager — reuses manage_credentials screens."""
    if not _vault_available():
        console.print("[red]DATABASE_URL and MASTER_KEY must be set for vault access.[/red]")
        return

    from terminal.tui.credentials import screen_key_manager, screen_plugin_picker  # noqa: PLC0415

    async def _vault_flow() -> None:
        from core.context import vault  # noqa: PLC0415

        while True:
            plugin = await screen_plugin_picker(console, vault)
            if plugin is None:
                break
            await screen_key_manager(console, vault, plugin)

    asyncio.run(_vault_flow())


# ---------------------------------------------------------------------------
# Screen 5: Live settings (DB)
# ---------------------------------------------------------------------------

def screen_live_settings(console: Console) -> None:
    """DB-backed live settings: gateway flag, LLM pool, step-model policy."""
    if not _db_available():
        console.print("[red]DATABASE_URL must be set for live DB settings.[/red]")
        return
    asyncio.run(_live_settings_loop(console))


async def _live_settings_loop(console: Console) -> None:
    """One event loop for the live-settings submenu (avoids a new engine per action)."""
    while True:
        choice = menu(
            console,
            "Live Settings (DB)  (changes apply without restart)",
            [
                ("1", "Gateway unified flag"),
                ("2", "LLM pool"),
                ("3", "Step-model policy"),
                ("4", "Model-role ladders"),
            ],
        )
        if choice == "b":
            break
        elif choice == "1":
            await _screen_gateway(console)
        elif choice == "2":
            await _screen_llm_pool(console)
        elif choice == "3":
            await _screen_step_policy(console)
        elif choice == "4":
            await _screen_model_roles(console)


async def _screen_gateway(console: Console) -> None:
    """Toggle the gateway unified flag in the DB."""
    from db_layer.gateway_settings_store import get_gateway_unified, set_gateway_unified  # noqa: PLC0415

    current = await get_gateway_unified()
    console.print(f"  gateway_unified = [bold]{current}[/bold]")
    if Confirm.ask(f"Set to [bold]{not current}[/bold]?"):
        await set_gateway_unified(not current)
        console.print(f"[green]✓ gateway_unified → {not current}[/green]")


async def _screen_llm_pool(console: Console) -> None:
    """List and manage the LLM pool entries."""
    from core.llm_config_service import (  # noqa: PLC0415
        get_active_map,
        list_pool,
        set_active,
        set_pool_entry_active,
        add_pool_entry,
        delete_pool_entry,
    )

    while True:
        pool = await list_pool()
        active = await get_active_map()
        active_ids = {v.get("id") for v in active.values() if isinstance(v, dict)}

        console.print()
        t = Table(show_header=True, title="LLM Pool")
        t.add_column("#")
        t.add_column("Name")
        t.add_column("Provider")
        t.add_column("Kind")
        t.add_column("Model")
        t.add_column("Active")
        t.add_column("Enabled")
        for i, entry in enumerate(pool):
            is_active = entry.get("id") in active_ids
            is_enabled = entry.get("is_enabled", True)
            t.add_row(
                str(i + 1),
                entry.get("name", ""),
                entry.get("provider", ""),
                entry.get("kind", ""),
                entry.get("model", ""),
                "[green]✓[/green]" if is_active else "",
                "[green]✓[/green]" if is_enabled else "[dim]✗[/dim]",
            )
        console.print(t)

        choices = [str(i + 1) for i in range(len(pool))] + ["a", "c", "d", "x", "b"]
        console.print("  [bold]a[/bold]. Activate entry for a kind")
        console.print("  [bold]c[/bold]. Create/add entry manually")
        console.print("  [bold]d[/bold]. Toggle enable/disable")
        console.print("  [bold]x[/bold]. Delete entry")
        console.print("  [bold]b[/bold]. Back")
        choice = Prompt.ask("Action", choices=choices, show_choices=False)

        if choice == "b":
            break
        elif choice == "a":
            if not pool:
                console.print("[dim]Pool is empty.[/dim]")
                continue
            idx_str = Prompt.ask("Entry #", choices=[str(i + 1) for i in range(len(pool))], show_choices=False)
            entry = pool[int(idx_str) - 1]
            kind = Prompt.ask("Kind", choices=list(_setup._LLM_KINDS), show_choices=True)
            await set_active(kind, entry["id"])
            console.print(f"[green]✓ {kind} → {entry['name']}[/green]")
        elif choice == "c":
            name = Prompt.ask("Entry Name (e.g. custom-gpt4o)").strip()
            if not name:
                console.print("[red]Name cannot be empty.[/red]")
                continue
            provider = Prompt.ask("Provider", choices=["openai", "anthropic", "gemini", "gemini-vertex", "claude-cli", "agy-cli", "grok-cli"], default="openai")
            kind = Prompt.ask("Kind", choices=["chat", "embedding", "route", "core"], default="chat")
            model = Prompt.ask("Model Name (e.g. gpt-4o)").strip()
            if not model:
                console.print("[red]Model cannot be empty.[/red]")
                continue
            dims_str = Prompt.ask("Dimensions (optional, e.g. 1536)", default="")
            dimensions = int(dims_str) if dims_str.isdigit() else None
            base_url = Prompt.ask("Base URL (optional, e.g. http://localhost:1234/v1)", default="").strip() or None
            api_key = Prompt.ask("API Key (optional, blank to omit)", password=True, default="").strip() or None
            strength_str = Prompt.ask("Strength (default 1.0)", default="1.0")
            try:
                strength = float(strength_str)
            except ValueError:
                strength = 1.0
            
            await add_pool_entry(
                name=name,
                provider=provider,
                model=model,
                kind=kind,
                dimensions=dimensions,
                base_url=base_url,
                api_key=api_key,
                strength=strength
            )
            console.print(f"[green]✓ Created entry '{name}' successfully.[/green]")
        elif choice == "x":
            if not pool:
                console.print("[dim]Pool is empty.[/dim]")
                continue
            idx_str = Prompt.ask("Entry # to delete", choices=[str(i + 1) for i in range(len(pool))], show_choices=False)
            entry = pool[int(idx_str) - 1]
            if Confirm.ask(f"Are you sure you want to delete '{entry['name']}'?"):
                await delete_pool_entry(entry["id"])
                console.print(f"[green]✓ Deleted entry '{entry['name']}' successfully.[/green]")
        elif choice == "d":
            if not pool:
                console.print("[dim]Pool is empty.[/dim]")
                continue
            idx_str = Prompt.ask("Entry #", choices=[str(i + 1) for i in range(len(pool))], show_choices=False)
            entry = pool[int(idx_str) - 1]
            currently_enabled = entry.get("is_enabled", True)
            await set_pool_entry_active(entry["id"], not currently_enabled)
            console.print(f"[green]✓ {entry['name']} → {'enabled' if not currently_enabled else 'disabled'}[/green]")


async def _screen_step_policy(console: Console) -> None:
    """Edit the GOAP step-model policy."""
    from db_layer.step_model_settings_store import DEFAULT_POLICY, get_step_model_policy, set_step_model_policy  # noqa: PLC0415

    policy = await get_step_model_policy()

    console.print()
    t = Table(show_header=True, title="Step-Model Policy")
    t.add_column("Field")
    t.add_column("Current Value")
    t.add_column("Editable")
    for k, v in policy.items():
        editable = "✓" if k in ("strategy", "parallel_enabled", "fanout_concurrency") else "— (admin UI)"
        t.add_row(k, repr(v) if not isinstance(v, str) else v, editable)
    console.print(t)

    patch: dict = {}
    strategies = ["off", "strength", "task_type", "explicit"]

    if Confirm.ask("Edit strategy?"):
        strategy = Prompt.ask("Strategy", choices=strategies, default=policy.get("strategy", "strength"), show_choices=True)
        patch["strategy"] = strategy

    if Confirm.ask("Edit parallel_enabled?"):
        patch["parallel_enabled"] = Confirm.ask("parallel_enabled", default=bool(policy.get("parallel_enabled", True)))

    if Confirm.ask("Edit fanout_concurrency?"):
        patch["fanout_concurrency"] = IntPrompt.ask("fanout_concurrency", default=int(policy.get("fanout_concurrency", 5)))

    if patch:
        await set_step_model_policy(patch)
        console.print(f"[green]✓ Policy updated: {patch}[/green]")
    else:
        console.print("[dim]No changes.[/dim]")


async def _screen_model_roles(console: Console) -> None:
    """List/edit model-role ladders (core_graph/model_roles/) + the effort_map.

    Scope deliberately narrower than the admin web UI (which owns full
    Validation/entry_conditions editing): list roles, edit one role's ladder
    as a comma-separated selector string, edit the effort_map, or reset a
    role to its core/plugin default.
    """
    from core_graph.model_roles.db_overlay import apply_db_overrides
    from core_graph.model_roles.registry import get_model_role, get_model_role_registry, list_model_roles
    from core_graph.model_roles.resolver import get_effort_map, _ALIASES_NON_CORE
    from core_graph.model_roles.role_spec import ModelRoleSpecError, _EFFORTS, parse_model_role_spec
    from db_layer.model_role_store import set_effort_map, set_model_role_override

    await apply_db_overrides()  # pick up any prior DB overrides before listing

    while True:
        console.print()
        roles = list_model_roles()
        registry = get_model_role_registry()
        t = Table(show_header=True, title="Model Role Ladders")
        t.add_column("role_id")
        t.add_column("source")
        t.add_column("ladder")
        t.add_column("terminal_fallback")
        for spec in roles:
            ladder_str = ", ".join(r.selector for r in spec.ladder)
            t.add_row(spec.role_id, registry.source_of(spec.role_id) or "?", ladder_str, spec.terminal_fallback)
        console.print(t)

        effort_map = get_effort_map()
        console.print(f"  effort_map: [dim]{effort_map}[/dim]")

        console.print()
        console.print("  [bold]1[/bold]. Edit a role's ladder")
        console.print("  [bold]2[/bold]. Edit effort_map")
        console.print("  [bold]3[/bold]. Reset a role to default")
        console.print("  [bold]b[/bold]. Back")
        choice = Prompt.ask("Select", choices=["1", "2", "3", "b"], show_choices=False)
        if choice == "b":
            break

        if choice == "1":
            role_id = Prompt.ask("role_id", choices=[s.role_id for s in roles], show_choices=False)
            current = get_model_role(role_id)
            if current is None:
                console.print("[red]unknown role_id[/red]")
                continue
            default_ladder = ", ".join(r.selector for r in current.ladder)
            raw = Prompt.ask("Ladder (comma-separated selectors)", default=default_ladder)
            selectors = [s.strip() for s in raw.split(",") if s.strip()]
            spec_dict = {
                "role_id": role_id,
                "description": current.description,
                "ladder": [{"selector": s} for s in selectors],
                "validate": None,
                "entry_conditions": [],
                "escalate_on_exception": current.escalate_on_exception,
                "escalate_on_invalid": current.escalate_on_invalid,
                "terminal_fallback": current.terminal_fallback,
            }
            if current.validate is not None:
                spec_dict["validate"] = {
                    "require_json": current.validate.require_json,
                    "required_keys": list(current.validate.required_keys),
                    "enum_field": current.validate.enum_field,
                    "enum_values": list(current.validate.enum_values),
                    "non_empty": current.validate.non_empty,
                }
            if current.entry_conditions:
                spec_dict["entry_conditions"] = [
                    {"field": c.field, "op": c.op, "value": c.value, "advance": c.advance}
                    for c in current.entry_conditions
                ]
            try:
                parse_model_role_spec(spec_dict, owner="db")  # validate before persisting
                await set_model_role_override(role_id, spec_dict)
            except ModelRoleSpecError as exc:
                console.print(f"[red]invalid spec: {exc}[/red]")
                continue
            await apply_db_overrides()
            console.print(f"[green]✓ {role_id} ladder updated[/green]")

        elif choice == "2":
            new_map = dict(effort_map)
            aliases = sorted(_ALIASES_NON_CORE)
            for level in sorted(_EFFORTS):
                alias = Prompt.ask(f"effort_map[{level}]", choices=aliases, default=new_map.get(level, aliases[0]))
                new_map[level] = alias
            try:
                await set_effort_map(new_map)
            except ModelRoleSpecError as exc:
                console.print(f"[red]invalid effort_map: {exc}[/red]")
                continue
            await apply_db_overrides()
            console.print("[green]✓ effort_map updated[/green]")

        elif choice == "3":
            db_role_ids = [s.role_id for s in roles if registry.source_of(s.role_id) == "db"]
            if not db_role_ids:
                console.print("[dim]No role has a DB override to reset.[/dim]")
                continue
            role_id = Prompt.ask("role_id to reset", choices=db_role_ids, show_choices=False)
            await set_model_role_override(role_id, None)
            await apply_db_overrides()
            console.print(f"[green]✓ {role_id} reset to default[/green]")


# ---------------------------------------------------------------------------
# Screen 6: Doctor / Health
# ---------------------------------------------------------------------------

def screen_doctor_health(console: Console) -> None:
    """Run prerequisite checks and optionally the health check."""
    console.print()
    console.print("[bold]Doctor[/bold]")
    _setup.run_doctor()
    if Confirm.ask("Run health check (requires server up)?"):
        run_with_progress(console, "health", _setup.run_health)


# ---------------------------------------------------------------------------
# Screen 8: Tenant management
# ---------------------------------------------------------------------------

def screen_tenants(console: Console) -> None:
    """Provide the TUI screen for viewing, creating, and managing tenants and users.

    Lists all tenants and allows navigating to tenant creation or user management.
    """
    if not _db_available():
        console.print("[red]DATABASE_URL must be set for tenant management.[/red]")
        return

    from core.user_management import list_tenants  # noqa: PLC0415

    while True:
        try:
            tenants = asyncio.run(list_tenants())
        except Exception as exc:
            console.print(f"[red]Error listing tenants: {exc}[/red]")
            Prompt.ask("Press Enter to retry")
            continue

        console.print()
        t = Table(show_header=True, title="Tenants")
        t.add_column("#")
        t.add_column("id")
        t.add_column("name")
        t.add_column("slug")
        t.add_column("active")
        for i, tenant in enumerate(tenants):
            is_active = tenant.get("is_active", True)
            t.add_row(
                str(i + 1),
                str(tenant.get("id", "")),
                tenant.get("name", ""),
                tenant.get("slug", ""),
                "[green]✓[/green]" if is_active else "[dim]✗[/dim]"
            )
        console.print(t)

        choices = [str(i + 1) for i in range(len(tenants))] + ["c", "b"]
        if tenants:
            console.print(f"  [bold]1..{len(tenants)}[/bold]. Manage tenant")
        console.print("  [bold]c[/bold]. Create new tenant")
        console.print("  [bold]b[/bold]. Back")
        choice = Prompt.ask("Select", choices=choices, show_choices=False)

        if choice == "b":
            break
        elif choice == "c":
            try:
                asyncio.run(_create_tenant_flow(console))
            except Exception as exc:
                console.print(f"[red]Error in tenant creation flow: {exc}[/red]")
        else:
            try:
                asyncio.run(_manage_tenant(console, tenants[int(choice) - 1]))
            except Exception as exc:
                console.print(f"[red]Error in tenant management flow: {exc}[/red]")


def _prompt_password_confirm(console: Console) -> str | None:
    """Prompt for a password and confirmation, requiring they match.

    Limits input to a maximum of 3 attempts for secure user creation workflows.
    """
    for _ in range(3):
        password = Prompt.ask("Password", password=True)
        confirm = Prompt.ask("Confirm password", password=True)
        if password == confirm:
            return password
        console.print("[red]Passwords do not match.[/red]")
    console.print("[red]Too many password attempts. Aborted.[/red]")
    return None


async def _create_tenant_flow(console: Console) -> None:
    """Guide the user through creating a new tenant and its master user.

    Prompts for tenant name/slug and master user credentials to seed a new tenant.
    """
    from core.user_management import create_tenant, create_user  # noqa: PLC0415
    import re  # noqa: PLC0415

    name = Prompt.ask("Tenant name").strip()
    if not name:
        console.print("[red]Tenant name cannot be empty.[/red]")
        return

    derived_slug = name.lower()
    derived_slug = re.sub(r"[\s_]+", "-", derived_slug)
    derived_slug = re.sub(r"[^a-z0-9\-]", "", derived_slug)
    derived_slug = re.sub(r"-+", "-", derived_slug).strip("-")

    slug = Prompt.ask("Slug", default=derived_slug).strip()
    if not slug:
        console.print("[red]Slug cannot be empty.[/red]")
        return

    username = Prompt.ask("Master username").strip()
    if not username:
        console.print("[red]Master username cannot be empty.[/red]")
        return

    password = _prompt_password_confirm(console)
    if password is None:
        return

    if not Confirm.ask(f"Create tenant '{name}' (slug '{slug}') with master account '{username}'?"):
        console.print("[dim]Cancelled.[/dim]")
        return

    try:
        tenant = await create_tenant(name, slug)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    # Attempt to create the master user, retrying up to 3 times total
    user_created = False
    for attempt in range(3):
        if attempt > 0:
            console.print(f"[yellow]Retrying master user creation (attempt {attempt + 1}/3)...[/yellow]")
            username = Prompt.ask("Master username").strip()
            if not username:
                console.print("[red]Master username cannot be empty.[/red]")
                continue
            password = _prompt_password_confirm(console)
            if password is None:
                continue

        try:
            await create_user(username, password, role="master", tenant_id=tenant["id"])
            console.print(f"[green]✓ Tenant '{name}' (ID {tenant['id']}) created with master user '{username}'.[/green]")
            user_created = True
            break
        except ValueError as exc:
            console.print(f"[red]Master user creation failed: {exc}[/red]")

    if not user_created:
        console.print(
            f"[yellow]Tenant was created (ID {tenant['id']}), but master user creation failed after multiple attempts.[/yellow]"
        )
        console.print("[yellow]Please retry adding a user to this tenant via the manage-tenant screen.[/yellow]")


async def _manage_tenant(console: Console, tenant: dict) -> None:
    """Manage a specific tenant, displaying existing users and allowing additions.

    Provides an interface to create new role-based users within the tenant.
    """
    from core.user_management import create_user, list_users  # noqa: PLC0415

    while True:
        console.print()
        console.print(f"[bold]Manage Tenant: {tenant['name']}[/bold]")
        console.print(f"  ID: {tenant['id']}")
        console.print(f"  Slug: {tenant['slug']}")
        console.print(f"  Active: {'Yes' if tenant.get('is_active', True) else 'No'}")

        users = await list_users(tenant_id=tenant["id"])

        t = Table(show_header=True, title="Users")
        t.add_column("Username")
        t.add_column("Role")
        t.add_column("Active")
        for u in users:
            t.add_row(
                u.get("username", ""),
                u.get("role", ""),
                "[green]✓[/green]" if u.get("is_active", True) else "[dim]✗[/dim]"
            )
        console.print(t)

        choices = ["a", "r", "b"]
        console.print("  [bold]a[/bold]. Add user to this tenant")
        console.print("  [bold]r[/bold]. Reset user password")
        console.print("  [bold]b[/bold]. Back")
        choice = Prompt.ask("Action", choices=choices, show_choices=False)

        if choice == "b":
            break
        elif choice == "a":
            username = Prompt.ask("Username").strip()
            if not username:
                console.print("[red]Username cannot be empty.[/red]")
                continue
            password = _prompt_password_confirm(console)
            if password is None:
                continue

            from utils.server_config import ROLES_CONFIG  # noqa: PLC0415
            role = Prompt.ask("Role", choices=list(ROLES_CONFIG.keys()), show_choices=True)

            try:
                await create_user(username, password, role=role, tenant_id=tenant["id"])
                console.print(f"[green]✓ User '{username}' created successfully.[/green]")
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
        elif choice == "r":
            if not users:
                console.print("[dim]No users found in this tenant.[/dim]")
                continue
            user_choices = [str(i + 1) for i in range(len(users))] + ["c"]
            console.print()
            console.print("[bold]Select user to reset password:[/bold]")
            for i, u in enumerate(users, 1):
                console.print(f"  {i}. {u['username']} ({u['role']})")
            console.print("  [bold]c[/bold]. Cancel")
            uc = Prompt.ask("User", choices=user_choices, show_choices=False)
            if uc == "c":
                continue
            target_user = users[int(uc) - 1]
            console.print(f"Resetting password for [bold cyan]{target_user['username']}[/bold cyan]:")
            new_pw = _prompt_password_confirm(console)
            if new_pw is None:
                continue
            from core.user_management import update_user_password  # noqa: PLC0415
            try:
                ok = await update_user_password(target_user["username"], new_pw)
                if ok:
                    console.print(f"[green]✓ Password for '{target_user['username']}' updated successfully.[/green]")
                else:
                    console.print(f"[red]User '{target_user['username']}' not found.[/red]")
            except Exception as exc:
                console.print(f"[red]Failed to update password: {exc}[/red]")


# ---------------------------------------------------------------------------
# Screen 9: Restart server
# ---------------------------------------------------------------------------

def screen_restart(console: Console) -> None:
    """Restart the MCP server container (drops this session if run inside it)."""
    name = os.environ.get("WHISKERS_CONTAINER_NAME", "whiskers-agent-server")
    console.print(f"[bold]Restart server[/bold] — target container: [cyan]{name}[/cyan]")
    console.print("[yellow]If the TUI is running inside this container, this session will end when it restarts. The server comes back automatically.[/yellow]")
    if not Confirm.ask(f"Restart {name} now?"):
        console.print("[dim]Cancelled.[/dim]")
        return

    try:
        rc = _setup.run_restart(_default_args())
        if rc == 0:
            console.print("[green]✓ Restart triggered.[/green]")
        else:
            console.print("[red]Restart failed (see output above).[/red]")
    except Exception as exc:
        console.print(f"[red]Restart error: {exc}[/red]")


# ---------------------------------------------------------------------------
# Screen 10: Override routes
# ---------------------------------------------------------------------------

def _run_repo_script(
    console: Console,
    title: str,
    description: str,
    script_name: str,
    extra_args: list[str] | None = None,
    *,
    require_db: bool = False,
    success: str = "Completed successfully.",
    failure: str | None = None,
    db_error: str = "DATABASE_URL must be set.",
) -> None:
    """Confirm and run a repo script under a progress spinner."""
    import subprocess

    if require_db and not _db_available():
        console.print(f"[red]{db_error}[/red]")
        return
    console.print()
    console.print(f"[bold]{title}[/bold]")
    console.print(description)
    if not Confirm.ask("Are you sure you want to run this?"):
        console.print("[dim]Cancelled.[/dim]")
        return
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO)
    script_path = _REPO / "scripts" / script_name
    argv = [sys.executable, str(script_path), *(extra_args or [])]

    def _run():
        return subprocess.run(argv, env=env, cwd=_REPO)

    try:
        result = run_with_progress(console, script_name, _run)
        if result.returncode == 0:
            console.print(f"[green]{glyph(console, 'ok')} {success}[/green]")
        else:
            fail_msg = (failure or "Failed with exit code {rc}.").format(rc=result.returncode)
            console.print(f"[red]{fail_msg}[/red]")
    except Exception as exc:
        console.print(f"[red]Error executing {script_name}: {exc}[/red]")


def screen_override_routes(console: Console) -> None:
    """Override all route embeddings by running scripts/upsert_all_route.py --override."""
    _run_repo_script(
        console,
        "Override/Re-index All Routes",
        "This will discover, validate, and enqueue embeddings for all active plugin routes in the DB, forcing re-embedding.",
        "upsert_all_route.py",
        ["--override"],
        require_db=True,
        success="Route override completed successfully.",
        failure="Route override failed with exit code {rc}.",
        db_error="DATABASE_URL must be set to run route overrides.",
    )


def screen_regen_graph(console: Console) -> None:
    """Regenerate the frontend graph topology TS file by running scripts/gen_graph_topology.py."""
    _run_repo_script(
        console,
        "Regenerate Frontend Graph Topology",
        "This will resolve the current backend LangGraph spec and regenerate the frontend TypeScript definitions at:\n  `frontend/cat-admin-frontend/src/components/goap/graphTopology.gen.ts`",
        "gen_graph_topology.py",
        success="Frontend graph topology regenerated successfully.",
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

_MENU: dict[str, tuple[str, object]] = {
    "1": ("Guided setup",                  screen_guided_setup),
    "2": ("Server config",                 screen_server_config),
    "3": ("Plugins",                       screen_plugins),
    "4": ("Credentials — vault (DB)",      screen_credentials),
    "5": ("Credentials — local files",     screen_local_credentials),
    "6": ("Live settings (DB)",            screen_live_settings),
    "7": ("Doctor / Health",               screen_doctor_health),
    "8": ("Tenant management",             screen_tenants),
    "9": ("Restart server",                screen_restart),
    "10": ("Override all routes (DB)",     screen_override_routes),
    "11": ("Regenerate frontend graph",    screen_regen_graph),
    "12": ("TUI theme",                    None),
}


def _needs_first_run() -> bool:
    """Return True when .env or plugin_config.json has not been scaffolded."""
    return not (_REPO / ".env").is_file() or not (_REPO / "config" / "plugin_config.json").is_file()


def _print_chrome(console: Console, nav: list[str], *, force: bool = False) -> None:
    """Clear the screen and draw status header + breadcrumb.

    Status probes (docker, DB, MCP, LLM) are cached for a few seconds by
    ``collect_status_row``; pass ``force=True`` right after a screen that may
    have changed state (guided setup, restart, plugin save) to refresh it.
    """
    console.clear()
    from terminal.tui.ui import collect_status_row

    line = collect_status_row(
        repo=_REPO,
        db_available=_db_available,
        vault_available=_vault_available,
        check_mcp=_setup.check_mcp_endpoint,
        check_llm=_setup.check_active_llm,
        force=force,
    )
    status_header(console, line)
    console.print(f"[muted]{breadcrumb(nav)}[/muted]")


def screen_first_run(console: Console) -> str:
    """Three-item wizard shown when the repo is not yet configured."""
    return menu(
        console,
        "First-run setup",
        [
            ("1", "Set everything up now"),
            ("2", "Advanced menu"),
            ("3", "Quit"),
        ],
        include_back=False,
        include_quit=False,
    )


def main() -> None:
    """Run the main TUI menu loop."""
    console = get_console()
    nav = ["Home"]
    first_run = _needs_first_run()
    refresh_status = False

    while True:
        _print_chrome(console, nav, force=refresh_status)
        refresh_status = False
        console.print("[heading]Whiskers Agent MCP — Setup[/heading]")
        console.print("DB/vault screens need DATABASE_URL + MASTER_KEY in .env\n")

        if first_run:
            choice = screen_first_run(console)
            if choice == "3":
                console.print("[dim]Bye.[/dim]")
                break
            if choice == "1":
                nav.append("Guided setup")
                try:
                    screen_guided_setup(console)
                except KeyboardInterrupt:
                    console.print("\n[dim]Cancelled.[/dim]")
                nav.pop()
                first_run = _needs_first_run()
                refresh_status = True
                continue
            first_run = False
            continue

        items = [(k, label) for k, (label, _) in _MENU.items()]
        choice = menu(console, "", items, include_back=False, include_quit=True)
        if choice == "q":
            console.print("[dim]Bye.[/dim]")
            break
        if choice == "12":
            nav.append("TUI theme")
            try:
                console = screen_theme_switcher(console)
            except KeyboardInterrupt:
                console.print("\n[dim]Cancelled.[/dim]")
            nav.pop()
            continue
        label, fn = _MENU[choice]
        nav.append(label)
        try:
            fn(console)
        except KeyboardInterrupt:
            console.print("\n[dim]Cancelled.[/dim]")
        refresh_status = True
        nav.pop()


if __name__ == "__main__":
    main()
