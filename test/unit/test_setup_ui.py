"""Unit tests for terminal/tui/ui.py helpers."""

from unittest.mock import MagicMock, patch

from rich.console import Console

from terminal.tui.ui import (
    breadcrumb,
    confirm_discard,
    glyph,
    menu,
    run_with_progress,
)
from terminal.tui.theme import DEFAULT_TUI_THEME, get_console


def test_glyph_ascii_fallback():
    console = MagicMock()
    console.encoding = "ascii"
    assert glyph(console, "ok") == "[OK]"
    assert glyph(console, "fail") == "[X]"
    assert glyph(console, "warn") == "[!]"
    assert glyph(console, "arrow") == "->"


def test_breadcrumb():
    assert breadcrumb(["Home", "Live Settings", "LLM Pool"]) == "Home > Live Settings > LLM Pool"


def test_menu_calls_prompt_ask():
    console = MagicMock()
    with patch("terminal.tui.ui.Prompt.ask", return_value="1") as ask:
        choice = menu(console, "Title", [("1", "One")], extras=[("x", "Extra")])
        assert choice == "1"
        choices = ask.call_args.kwargs["choices"]
        assert "1" in choices and "x" in choices and "b" in choices


def test_confirm_discard_clean():
    with patch("terminal.tui.ui.Confirm.ask") as ask:
        assert confirm_discard(False) is False
        ask.assert_not_called()


def test_confirm_discard_dirty():
    with patch("terminal.tui.ui.Confirm.ask", return_value=True) as ask:
        assert confirm_discard(True) is True
        ask.assert_called_once()


def test_run_with_progress():
    console = Console(record=True)
    assert run_with_progress(console, "wait", lambda: 42) == 42


def test_unknown_theme_falls_back(monkeypatch):
    monkeypatch.setenv("WHISKERS_TUI_THEME", "not-a-real-theme")
    with patch("terminal.tui.theme.warnings.warn") as warn:
        console = get_console()
        warn.assert_called()
    from terminal.tui.theme import THEMES

    assert DEFAULT_TUI_THEME in THEMES
    assert console.get_style("ok") is not None
