"""Structured job-posting ingest: paste parse, board identity, blocked-fetch class.

Used by ``fetch_job_posting`` so a pasted Indeed/agency JD lands in the same
slot schema as an ATS fetch (title/company/location/compensation/job_id) and a
Cloudflare/401 on a NO_API board is ``needs_browser_scrape``, never a ghost-job
signal and never an unstructured ``api_error``.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

# Staffing / recruiting firms observed on Canadian + US boards. Match is
# case-insensitive and token-bounded so "Hays" does not hit "Hayes".
KNOWN_AGENCIES: frozenset[str] = frozenset(
    {
        "corgta",
        "robert half",
        "randstad",
        "adecco",
        "manpower",
        "kelly services",
        "teksystems",
        "tek systems",
        "insight global",
        "hays",
        "michael page",
        "pagegroup",
        "aerotek",
        "kforce",
        "modis",
        "apex systems",
        "cybercoders",
        "robert walters",
        "hudson",
        "s.i. systems",
        "si systems",
        "procom",
        "brainhunter",
        "eagle professional",
        "express employment",
        "ajilon",
        "roberthalf",
        "hays recruiting",
        "randstad digital",
        "randstad technologies",
    }
)

_AGENCY_PHRASES: tuple[str, ...] = (
    "our client",
    "one of our clients",
    "on behalf of our client",
    "on behalf of a client",
    "staffing agency",
    "recruitment agency",
    "recruiting firm",
    "in a contract capacity",
    "contract capacity",
    "clients in a contract",
)

_NO_API_HOST_MARKERS: tuple[tuple[str, str], ...] = (
    ("indeed.", "indeed"),
    ("linkedin.", "linkedin"),
)

# fromjk is a related listing Indeed attaches for "similar jobs" — never identity.
_INDEED_JK_KEYS: tuple[str, ...] = ("jk", "vjk")

_LINKEDIN_VIEW_RE = re.compile(r"/jobs/view/(\d+)", re.IGNORECASE)

_PAY_RE = re.compile(
    r"\$?\s*([\d]{2,3}(?:,\d{3})+(?:\.\d+)?|[\d]{4,7}(?:\.\d+)?)"
    r"\s*(?:[-–—]|to)\s*"
    r"\$?\s*([\d]{2,3}(?:,\d{3})+(?:\.\d+)?|[\d]{4,7}(?:\.\d+)?)"
    r"(?:\s*(CAD|USD|EUR|GBP|AUD))?",
    re.IGNORECASE,
)

_CURRENCY_RE = re.compile(r"\b(CAD|USD|EUR|GBP|AUD)\b", re.IGNORECASE)
_JOB_TYPE_RE = re.compile(
    r"\b(full[- ]time|part[- ]time|contract(?:or)?|internship|temporary|permanent|casual)\b",
    re.IGNORECASE,
)
_LOCATION_CHIP_RE = re.compile(
    r"(?i)"
    r"(?:remote(?:\s*\([^)]+\))?"
    r"|(?:[A-ZÀ-ÿ][\w.'-]+(?:\s+[A-ZÀ-ÿ][\w.'-]+)*,\s*[A-Z]{2}"
    r"(?:\s*[•·|,]\s*Remote(?:\s*\([^)]+\))?)?))"
)

_LABEL_RE = re.compile(
    r"(?im)^\s*(title|role|position|company|employer|location|salary|pay|compensation|job type|type)\s*[:\-]\s*(.+?)\s*$"
)

_CHROME_LINE_RE = re.compile(
    r"(?i)^(job details|here's how the job details|here’s how the job details|"
    r"apply now|report job|save job|full job description|profile insights|"
    r"benefits|indeed|linkedin)\s*$"
)

_CONTRACT_BODY_RE = re.compile(
    r"(?i)\b(contract capacity|in a contract|contractor|c2c|corp-to-corp|"
    r"1099|fixed[- ]term contract|contract role)\b"
)
_FTE_BODY_RE = re.compile(r"(?i)\b(full[- ]time|permanent|salaried)\b")

_GATE_RE = re.compile(
    r"(?i)cloudflare|cf-ray|just a moment|attention required|"
    r"challenge-platform|login[- ]required|please log in|sign in to view|"
    r"\b401\b|\b403\b|captcha|hcaptcha|recaptcha|access denied|"
    r"enable javascript and cookies|sorry, you have been blocked"
)
_GONE_RE = re.compile(r"(?i)\b404\b|\b410\b|not found|no longer (?:available|accepting)")


def empty_compensation() -> dict[str, Any]:
    """ATS-shaped compensation block with unknown bounds."""
    return {"min": None, "max": None, "currency": None}


def extract_board_identity(url: str) -> dict[str, str]:
    """Pull provider + board job_id (Indeed jk/vjk, LinkedIn numeric id) from a URL.

    Returns ``{provider, job_id, source_url}``. Unknown hosts leave provider/job_id empty
    and still echo ``source_url`` so a paste+URL pair never drops the link.
    """
    raw = (url or "").strip()
    if not raw:
        return {"provider": "", "job_id": "", "source_url": ""}
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    provider = ""
    job_id = ""
    for marker, name in _NO_API_HOST_MARKERS:
        if marker in host:
            provider = name
            break
    if provider == "indeed":
        qs = parse_qs(parsed.query)
        for key in _INDEED_JK_KEYS:
            values = qs.get(key) or []
            if values and values[0].strip():
                job_id = values[0].strip()
                break
    elif provider == "linkedin":
        qs = parse_qs(parsed.query)
        current = (qs.get("currentJobId") or qs.get("jobId") or [""])[0].strip()
        if current:
            job_id = current
        else:
            m = _LINKEDIN_VIEW_RE.search(parsed.path or "")
            if m:
                job_id = m.group(1)
    return {"provider": provider, "job_id": job_id, "source_url": raw}


def is_no_api_provider(provider: str, url: str = "") -> bool:
    """True for Indeed/LinkedIn (and any future NO_API host detected from URL)."""
    if (provider or "").lower() in {"indeed", "linkedin"}:
        return True
    return bool(extract_board_identity(url).get("provider"))


def classify_fetch_block(result: dict[str, Any] | None, content: str = "") -> str | None:
    """Classify a failed or challenge-page fetch.

    Returns ``\"needs_browser_scrape\"`` for Cloudflare/401/403/captcha/login walls.
    Returns ``None`` for a real miss (404/410/gone) or a generic transport error —
    those stay ``api_error`` / ``job_not_found``. Never returns a ghost-job signal;
    ghost scoring lives in ``check_job_liveness`` only.
    """
    hay = " ".join(
        [
            str((result or {}).get("error") or ""),
            str((result or {}).get("message") or ""),
            str((result or {}).get("status_code") or ""),
            (content or "")[:4000],
        ]
    )
    if _GATE_RE.search(hay):
        return "needs_browser_scrape"
    if _GONE_RE.search(hay):
        return None
    return None


def _money(value: str) -> int | None:
    digits = re.sub(r"[^\d.]", "", value or "")
    if not digits:
        return None
    try:
        return int(float(digits))
    except ValueError:
        return None


def _parse_compensation(text: str, location: str = "") -> dict[str, Any]:
    comp = empty_compensation()
    if not text:
        return comp
    m = _PAY_RE.search(text)
    if not m:
        return comp
    comp["min"] = _money(m.group(1))
    comp["max"] = _money(m.group(2))
    currency = (m.group(3) or "").upper()
    if not currency:
        nearby = text[max(0, m.start() - 20) : m.end() + 20]
        cm = _CURRENCY_RE.search(nearby) or _CURRENCY_RE.search(text)
        currency = (cm.group(1).upper() if cm else "")
    if not currency and re.search(r"(?i)\b(canada|cad|montr[eé]al|toronto|vancouver)\b", f"{text} {location}"):
        currency = "CAD"
    elif not currency and "$" in m.group(0):
        currency = "USD"
    comp["currency"] = currency or None
    return comp


def _norm_job_type(raw: str) -> str:
    token = re.sub(r"\s+", " ", (raw or "").strip().lower())
    token = token.replace("-", " ")
    aliases = {
        "full time": "Full-time",
        "part time": "Part-time",
        "contract": "Contract",
        "contractor": "Contract",
        "internship": "Internship",
        "temporary": "Temporary",
        "permanent": "Permanent",
        "casual": "Casual",
    }
    return aliases.get(token, raw.strip())


def _employment_class(job_type: str, body: str) -> str:
    """Resolve fte vs contract vs unknown when the UI chip and body disagree."""
    chip = (job_type or "").lower().replace("-", " ")
    chip_contract = "contract" in chip and "full time" not in chip
    chip_fte = "full time" in chip or chip == "permanent"
    body_contract = bool(_CONTRACT_BODY_RE.search(body or ""))
    body_fte = bool(_FTE_BODY_RE.search(body or ""))
    if chip_fte and body_contract:
        return "unknown"
    if chip_contract or body_contract:
        return "contract"
    if chip_fte or body_fte:
        return "fte"
    return "unknown"


def _looks_like_agency(name: str) -> bool:
    key = re.sub(r"[^a-z0-9.]+", " ", (name or "").lower()).strip()
    if not key:
        return False
    if key in KNOWN_AGENCIES:
        return True
    return any(key == agency or key.startswith(f"{agency} ") for agency in KNOWN_AGENCIES)


def _agency_from_body(text: str) -> bool:
    hay = (text or "").lower()
    return any(phrase in hay for phrase in _AGENCY_PHRASES)


def _extract_labeled(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _LABEL_RE.finditer(text or ""):
        label = m.group(1).lower()
        value = m.group(2).strip()
        if label in {"title", "role", "position"} and "title" not in out:
            out["title"] = value
        elif label in {"company", "employer"} and "company" not in out:
            out["company"] = value
        elif label == "location" and "location" not in out:
            out["location"] = value
        elif label in {"salary", "pay", "compensation"} and "pay" not in out:
            out["pay"] = value
        elif label in {"job type", "type"} and "job_type" not in out:
            out["job_type"] = value
    return out


def _header_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _CHROME_LINE_RE.match(line):
            break
        if line.lower() in {"pay", "salary", "job type", "location"}:
            continue
        lines.append(line)
        if len(lines) >= 12:
            break
    return lines


def parse_pasted_posting(raw_text: str) -> dict[str, Any]:
    """Parse a pasted JD (Indeed/agency/plain) into the ATS ingest slot schema.

    Never invents an unnamed client's legal name. When the poster is a staffing
    agency, ``company`` stays empty and ``via`` / ``posting_entity`` hold the
    agency; ``end_employer`` is null.
    """
    text = (raw_text or "").strip()
    labeled = _extract_labeled(text)
    headers = _header_lines(text)

    title = labeled.get("title", "")
    posted_as = labeled.get("company", "")
    location = labeled.get("location", "")
    job_type = _norm_job_type(labeled.get("job_type", ""))
    pay_blob = labeled.get("pay", "")

    if not title and headers:
        title = headers[0]
    if not posted_as:
        for line in headers[1:]:
            if _PAY_RE.search(line) or _LOCATION_CHIP_RE.search(line):
                continue
            if _JOB_TYPE_RE.fullmatch(line.strip()):
                continue
            posted_as = line
            break
    if not location:
        for line in headers:
            m = _LOCATION_CHIP_RE.search(line)
            if m:
                location = m.group(0).strip()
                break
        if not location:
            m = _LOCATION_CHIP_RE.search(text)
            if m:
                location = m.group(0).strip()
    if not job_type:
        for line in headers:
            m = _JOB_TYPE_RE.search(line)
            if m and _PAY_RE.search(line):
                job_type = _norm_job_type(m.group(1))
                break
        if not job_type:
            m = _JOB_TYPE_RE.search(text[:1500])
            if m:
                job_type = _norm_job_type(m.group(1))
    if not pay_blob:
        for line in headers:
            if _PAY_RE.search(line):
                pay_blob = line
                break
        if not pay_blob:
            m = _PAY_RE.search(text)
            if m:
                pay_blob = m.group(0)

    compensation = _parse_compensation(pay_blob or text, location)
    is_agency = _looks_like_agency(posted_as) or _agency_from_body(text)
    posting_entity = posted_as
    via = posted_as if is_agency else ""
    end_employer = None
    company = "" if is_agency else posted_as
    if is_agency:
        # Conservative: only accept an explicit "client: Name" / "our client, Name"
        # label. A bare "our client" stays unnamed — never invent.
        named = re.search(
            r"(?i)(?:our client|the client|client)\s*[:,—-]\s*([A-Z][\w&.\- ]{1,40})",
            text,
        )
        if named:
            candidate = named.group(1).strip().rstrip(".")
            if candidate and not _looks_like_agency(candidate) and candidate.lower() not in {"in a contract capacity", "s"}:
                end_employer = candidate
                company = candidate

    employment_class = _employment_class(job_type, text)

    return {
        "title": title,
        "company": company,
        "location": location,
        "compensation": compensation,
        "job_type": job_type,
        "posting_entity": posting_entity,
        "via": via,
        "end_employer": end_employer,
        "employment_class": employment_class,
        "is_agency": is_agency,
        "clean_description": text,
    }


def posting_envelope(
    *,
    status: str = "ok",
    provider: str = "",
    job_id: str = "",
    title: str = "",
    company: str = "",
    location: str = "",
    compensation: dict[str, Any] | None = None,
    clean_description: str = "",
    source_url: str = "",
    posted_at: Any = None,
    job_type: str = "",
    posting_entity: str = "",
    via: str = "",
    end_employer: str | None = None,
    employment_class: str = "unknown",
    needs_browser_scrape: bool = False,
    ingest_status: str = "ok",
    ingest_method: str = "",
    is_agency: bool = False,
    error: str = "",
    message: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ATS-shaped fetch_job_posting return dict (additive keys only)."""
    out: dict[str, Any] = {
        "status": status,
        "provider": provider,
        "job_id": job_id,
        "title": title,
        "company": company,
        "location": location,
        "compensation": compensation if compensation is not None else empty_compensation(),
        "clean_description": clean_description,
        "source_url": source_url,
        "posted_at": posted_at,
        "job_type": job_type,
        "posting_entity": posting_entity,
        "via": via,
        "end_employer": end_employer,
        "employment_class": employment_class,
        "needs_browser_scrape": needs_browser_scrape,
        "ingest_status": ingest_status,
        "ingest_method": ingest_method,
        "is_agency": is_agency,
        "is_potential_ghost_job": False,
    }
    if error:
        out["error"] = error
    if message:
        out["message"] = message
    if extra:
        out.update(extra)
    return out
