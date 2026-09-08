"""TUI Catppuccin theme selection."""

from __future__ import annotations

from terminal.tui.theme import DEFAULT_TUI_THEME, THEMES, get_console


def test_default_flavor_is_mocha():
    assert DEFAULT_TUI_THEME == "mocha"
    assert {"mocha", "frappe", "macchiato", "latte", "cozy", "neon", "paper"} <= set(THEMES)


def test_get_console_applies_known_flavor(monkeypatch):
    monkeypatch.delenv("WHISKERS_TUI_THEME", raising=False)
    c = get_console("latte")
    assert c.get_style("accent").color is not None


def test_get_console_unknown_flavor_falls_back(monkeypatch):
    monkeypatch.delenv("WHISKERS_TUI_THEME", raising=False)
    c = get_console("not-a-flavor")
    # Unknown names used to return a colorless Console(); fallback keeps markup.
    assert c.get_style("ok").color is not None


def test_get_console_reads_env(monkeypatch):
    monkeypatch.setenv("WHISKERS_TUI_THEME", "frappe")
    c = get_console()
    assert c.get_style("ok").color is not None
