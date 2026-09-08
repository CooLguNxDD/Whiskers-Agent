"""Unit tests for paste parse, board identity, agency class, and blocked-fetch."""

from pathlib import Path

from plugins.job_search_plugin.portfolio_link import public_portfolio_url, sanitize_portfolio_url
from plugins.job_search_plugin.posting_ingest import (
    classify_fetch_block,
    extract_board_identity,
    parse_pasted_posting,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CORGTA_PASTE = (FIXTURES / "corgta_indeed_paste.txt").read_text(encoding="utf-8")
INDEED_URL = (
    "https://ca.indeed.com/viewjob?jk=ded0d7160e9a67f0"
    "&fromjk=f74eff626fe1aa7f"
)


def test_extract_indeed_jk_not_fromjk():
    ident = extract_board_identity(INDEED_URL)
    assert ident["provider"] == "indeed"
    assert ident["job_id"] == "ded0d7160e9a67f0"
    assert ident["job_id"] != "f74eff626fe1aa7f"


def test_extract_indeed_vjk_fallback():
    ident = extract_board_identity("https://www.indeed.com/viewjob?vjk=abc123def")
    assert ident["provider"] == "indeed"
    assert ident["job_id"] == "abc123def"


def test_extract_linkedin_job_id():
    ident = extract_board_identity("https://www.linkedin.com/jobs/view/9876543210/")
    assert ident["provider"] == "linkedin"
    assert ident["job_id"] == "9876543210"


def test_parse_raw_text_only_fills_structured_slots():
    parsed = parse_pasted_posting(
        "Staff Backend Engineer\nAcme Corp\nRemote (US)\n$180,000–$210,000 USD\n\n"
        "Build APIs. Full-time permanent role."
    )
    assert parsed["title"] == "Staff Backend Engineer"
    assert parsed["company"] == "Acme Corp"
    assert "Remote" in parsed["location"]
    assert parsed["compensation"]["min"] == 180000
    assert parsed["compensation"]["max"] == 210000
    assert parsed["compensation"]["currency"] == "USD"
    assert parsed["is_agency"] is False
    assert parsed["end_employer"] is None
    assert parsed["via"] == ""


def test_parse_corgta_agency_paste():
    parsed = parse_pasted_posting(CORGTA_PASTE)
    assert parsed["title"].startswith("Intermediate Fullstack Developer")
    assert parsed["via"] == "CorGTA"
    assert parsed["posting_entity"] == "CorGTA"
    assert parsed["end_employer"] is None
    assert parsed["company"] == ""
    assert "Montréal" in parsed["location"] or "Montreal" in parsed["location"]
    assert parsed["compensation"]["min"] == 110000
    assert parsed["compensation"]["max"] == 150000
    assert parsed["compensation"]["currency"] == "CAD"
    assert parsed["job_type"] == "Full-time"
    assert parsed["employment_class"] == "unknown"
    assert parsed["is_agency"] is True


def test_classify_cloudflare_401_is_scrape_not_ghost():
    assert (
        classify_fetch_block(
            {"status": "error", "message": "Client error '401 Unauthorized' for url; Cloudflare"}
        )
        == "needs_browser_scrape"
    )
    assert classify_fetch_block({"message": "404 Not Found — job no longer available"}) is None


def test_public_portfolio_url_omits_localhost_and_empty():
    assert public_portfolio_url("abc", domain="localhost:11000") == ""
    assert public_portfolio_url("abc", domain="http://localhost:11000") == ""
    assert public_portfolio_url("abc", domain="127.0.0.1:11000") == ""
    assert public_portfolio_url("abc", domain="") == ""
    assert public_portfolio_url("", domain="portfolio.cat.io") == ""
    assert public_portfolio_url("abc", domain="portfolio.cat.io") == "https://portfolio.cat.io/?j=abc"


def test_sanitize_portfolio_url_drops_loopback():
    assert sanitize_portfolio_url("http://localhost:11000/?j=abc") == ""
    assert sanitize_portfolio_url("/?j=abc") == ""
    assert sanitize_portfolio_url("https://portfolio.cat.io/?j=abc") == "https://portfolio.cat.io/?j=abc"
