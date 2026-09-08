"""Parse project date ranges out of source prose.

Discovery documents (Notion pages, GitHub READMEs) often carry a
human-written period line — ``**Period Covered:** September 2025 – July
2026`` — that ``compose/quality.py`` deliberately strips before it ever
reaches display copy (it is README chrome, not prose). This module is the
one place that period text is actually useful: turning it into structured
``started_on`` / ``ended_on`` dates before it is thrown away.

``compose/fish.py`` and the timeline builders consume the parsed dates, not
the source text — dates travel through the DB as typed columns, never as
prose that must be re-parsed downstream.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date

_MONTHS = {
    name.lower(): i
    for i, name in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}
# Common abbreviations map to the same index via startswith() in _parse_month_token.

_PRESENT_RE = re.compile(r"^(present|now|ongoing|current)$", re.IGNORECASE)

# "**Period Covered:** September 2025 – July 2026" (bold + colon optional,
# dash/en-dash/em-dash/"to" as the range separator).
_PERIOD_LINE_RE = re.compile(
    r"(?im)^\s*\*{0,2}\s*period\s+covered\s*\*{0,2}\s*:?\s*(?P<range>.+?)\s*$"
)
# Require whitespace around the separator so a bare hyphen inside an ISO
# token ("2025-09") is never mistaken for the range separator.
_RANGE_SPLIT_RE = re.compile(r"\s+(?:–|—|-|to)\s+", re.IGNORECASE)

# Individual date tokens: "September 2025", "Sep 2025", "2025-09", "2025".
_MONTH_YEAR_RE = re.compile(r"(?P<month>[A-Za-z]+)\.?\s+(?P<year>\d{4})")
_ISO_MONTH_RE = re.compile(r"(?P<year>\d{4})-(?P<month>\d{1,2})")
_YEAR_ONLY_RE = re.compile(r"(?P<year>\d{4})")


def _month_index(token: str) -> int | None:
    """Resolve a month name/abbreviation ("Sep", "September") to 1-12."""
    token_l = token.lower()
    for name, idx in _MONTHS.items():
        if name == token_l or name.startswith(token_l) and len(token_l) >= 3:
            return idx
    return None


def _parse_date_token(token: str, *, end_of_month: bool) -> date | None:
    """Parse one side of a period range into a date, or None if unparseable."""
    token = token.strip().strip(".")
    if not token:
        return None

    m = _MONTH_YEAR_RE.match(token)
    if m:
        month = _month_index(m.group("month"))
        year = int(m.group("year"))
        if month:
            day = monthrange(year, month)[1] if end_of_month else 1
            return date(year, month, day)

    m = _ISO_MONTH_RE.match(token)
    if m:
        year, month = int(m.group("year")), int(m.group("month"))
        if 1 <= month <= 12:
            day = monthrange(year, month)[1] if end_of_month else 1
            return date(year, month, day)

    m = _YEAR_ONLY_RE.fullmatch(token)
    if m:
        year = int(m.group("year"))
        return date(year, 12, 31) if end_of_month else date(year, 1, 1)

    return None


def parse_period(text: str) -> tuple[date | None, date | None, bool]:
    """Extract (started_on, ended_on, matched) from source prose.

    ``matched`` is True whenever a "Period Covered" line was found, even if
    one side failed to parse — callers use it to distinguish "no period
    line present" from "period line present but malformed", since the
    latter still counts as a discovery signal worth logging.

    ``ended_on is None`` with ``matched is True`` means an explicit
    "Present"/"Ongoing" end, or a range with only a start.
    """
    if not text:
        return None, None, False

    line_match = _PERIOD_LINE_RE.search(text)
    if not line_match:
        return None, None, False

    range_text = line_match.group("range").strip().strip("*").strip()
    if not range_text:
        return None, None, True

    parts = _RANGE_SPLIT_RE.split(range_text, maxsplit=1)
    start_token = parts[0].strip() if parts else ""
    end_token = parts[1].strip() if len(parts) > 1 else ""

    started_on = _parse_date_token(start_token, end_of_month=False) if start_token else None

    ended_on: date | None = None
    if end_token and not _PRESENT_RE.match(end_token):
        ended_on = _parse_date_token(end_token, end_of_month=True)
    # end_token empty or "Present"/"Ongoing"/"Now"/"Current" -> ended_on stays None (ongoing)

    return started_on, ended_on, True


def format_period(started_on: date | str | None, ended_on: date | str | None) -> str | None:
    """Render a stored date pair back to display text, e.g. "Sep 2025 – Jul 2026".

    Returns None when there is nothing to show. An open end renders as
    "– present".
    """
    def _as_date(v: date | str | None) -> date | None:
        if v is None:
            return None
        if isinstance(v, date):
            return v
        try:
            return date.fromisoformat(str(v)[:10])
        except ValueError:
            return None

    start = _as_date(started_on)
    end = _as_date(ended_on)
    if start is None and end is None:
        return None

    def _fmt(d: date) -> str:
        return f"{d.strftime('%b')} {d.year}"

    if start is not None and end is not None:
        return f"{_fmt(start)} – {_fmt(end)}"
    if start is not None:
        return f"{_fmt(start)} – present"
    return _fmt(end)  # type: ignore[arg-type]
