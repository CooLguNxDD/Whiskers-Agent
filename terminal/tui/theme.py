"""Rich TUI palettes derived from the shared theme JSON registry.

Builtin color names (cyan, green, …) are remapped to flavor hex so existing
markup retints without call-site edits. Semantic names (accent, ok, warn,
danger, muted, heading) are for new markup. No CSS token maps to a distinct
blue, so rich's builtin blue is left alone.
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

from rich.console import Console
from rich.theme import Theme

from utils.theme_registry import THEME_REGISTRY

# rich style name → CSS token in ThemeDef.hex
_RICH_TOKEN_MAP: dict[str, str] = {
    "cyan": "cyan",
    "green": "neon",
    "yellow": "warn",
    "red": "danger",
    "magenta": "pink",
    "dim": "fg-subtle",
    "accent": "amber",
    "ok": "ok",
    "warn": "warn",
    "danger": "danger",
    "muted": "fg-subtle",
}


def _flavor_styles(hexmap: dict[str, str]) -> dict[str, str]:
    styles: dict[str, str] = {}
    for rich_name, css_name in _RICH_TOKEN_MAP.items():
        value = hexmap.get(css_name)
        if value:
            styles[rich_name] = value
    amber = hexmap.get("amber")
    if amber:
        styles["heading"] = f"bold {amber}"
    return styles


THEMES: dict[str, Theme] = {
    name: Theme(_flavor_styles(defn.hex))
    for name, defn in THEME_REGISTRY.items()
    if _flavor_styles(defn.hex)
}
DEFAULT_TUI_THEME = "mocha"


def resolve_tui_theme_name(flavor: str | None = None) -> str:
    """Return a known THEME_REGISTRY key, defaulting to mocha."""
    name = (
        flavor
        or os.environ.get("WHISKERS_TUI_THEME")
        or DEFAULT_TUI_THEME
    ).strip().lower()
    if name in THEMES:
        return name
    return DEFAULT_TUI_THEME


def get_console(flavor: str | None = None) -> Console:
    """Return a Console themed from ``flavor`` or ``WHISKERS_TUI_THEME`` (mocha).

    Unknown names warn and fall back to ``DEFAULT_TUI_THEME`` rather than a
    colorless ``Console()`` (which would drop ``[accent]``/``[ok]`` markup).
    """
    requested = (
        flavor
        or os.environ.get("WHISKERS_TUI_THEME")
        or DEFAULT_TUI_THEME
    ).strip().lower()
    name = requested
    if name not in THEMES:
        warnings.warn(
            f"Unknown WHISKERS_TUI_THEME {requested!r}; falling back to {DEFAULT_TUI_THEME!r}",
            UserWarning,
            stacklevel=2,
        )
        print(
            f"warning: unknown WHISKERS_TUI_THEME {requested!r}; using {DEFAULT_TUI_THEME!r}",
            file=sys.stderr,
        )
        name = DEFAULT_TUI_THEME
    return Console(theme=THEMES[name])


def persist_tui_theme(flavor: str, env_path: Path | None = None) -> None:
    """Write WHISKERS_TUI_THEME into ``.env`` via setup.render_env."""
    if flavor not in THEMES:
        raise ValueError(f"unknown theme {flavor!r}")
    os.environ["WHISKERS_TUI_THEME"] = flavor
    root = Path(__file__).resolve().parent.parent.parent
    path = env_path or (root / ".env")
    if not path.is_file():
        path.write_text(f"WHISKERS_TUI_THEME={flavor}\n", encoding="utf-8")
        return
    text = path.read_text(encoding="utf-8")
    try:
        import setup as _setup

        path.write_text(_setup.render_env(text, {"WHISKERS_TUI_THEME": flavor}), encoding="utf-8")
    except Exception:
        lines = []
        found = False
        for line in text.splitlines():
            if line.startswith("WHISKERS_TUI_THEME="):
                lines.append(f"WHISKERS_TUI_THEME={flavor}")
                found = True
            else:
                lines.append(line)
        if not found:
            lines.append(f"WHISKERS_TUI_THEME={flavor}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def screen_theme_switcher(console: Console) -> Console:
    """List THEME_REGISTRY keys, persist the choice, return a new Console."""
    from rich.prompt import Prompt

    keys = sorted(THEMES)
    current = resolve_tui_theme_name()
    console.print()
    console.print("[bold]TUI theme[/bold]")
    for i, key in enumerate(keys, 1):
        mark = " (current)" if key == current else ""
        console.print(f"  [bold]{i}[/bold]. {key}{mark}")
    choices = [str(i) for i in range(1, len(keys) + 1)] + ["b"]
    choice = Prompt.ask("Select theme", choices=choices, show_choices=False)
    if choice == "b":
        return console
    picked = keys[int(choice) - 1]
    persist_tui_theme(picked)
    console.print(f"[green]WHISKERS_TUI_THEME={picked}[/green]")
    return get_console(picked)
