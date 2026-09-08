"""Project timeline parsing (discovery/period.py) — "Period Covered" line
extraction and the format_period round trip used by the fish tank / timeline
blocks."""

from __future__ import annotations

from datetime import date

from plugins.portfolio_plugin.discovery.period import format_period, parse_period


def test_parse_period_exact_notion_line():
    started, ended, matched = parse_period(
        "**Period Covered:** September 2025 – July 2026"
    )
    assert matched is True
    assert started == date(2025, 9, 1)
    assert ended == date(2026, 7, 31)


def test_parse_period_present_end_is_ongoing():
    started, ended, matched = parse_period("Period Covered: March 2024 – Present")
    assert matched is True
    assert started == date(2024, 3, 1)
    assert ended is None


def test_parse_period_bare_years():
    started, ended, matched = parse_period("**Period Covered:** 2022 - 2023")
    assert matched is True
    assert started == date(2022, 1, 1)
    assert ended == date(2023, 12, 31)


def test_parse_period_iso_month():
    started, ended, matched = parse_period("Period Covered: 2025-09 to 2026-07")
    assert matched is True
    assert started == date(2025, 9, 1)
    assert ended == date(2026, 7, 31)


def test_parse_period_no_line_present():
    started, ended, matched = parse_period("# My Project\n\nJust a regular README.")
    assert matched is False
    assert started is None
    assert ended is None


def test_parse_period_embedded_in_longer_document():
    text = (
        "# Helix AI\n\n"
        "Some intro paragraph about the project.\n\n"
        "**Period Covered:** September 2025 – July 2026\n\n"
        "## Architecture\n\nMore content here."
    )
    started, ended, matched = parse_period(text)
    assert matched is True
    assert started == date(2025, 9, 1)
    assert ended == date(2026, 7, 31)


def test_format_period_range():
    assert format_period(date(2025, 9, 1), date(2026, 7, 31)) == "Sep 2025 – Jul 2026"


def test_format_period_ongoing():
    assert format_period(date(2024, 3, 1), None) == "Mar 2024 – present"


def test_format_period_iso_strings():
    assert format_period("2025-09-01", "2026-07-31") == "Sep 2025 – Jul 2026"


def test_format_period_nothing_known():
    assert format_period(None, None) is None
