import asyncio
import time
import pytest
from unittest.mock import patch

from api.admin_routes import (
    _check_rate_limit,
    _check_rate_limit_async,
    _prune_login_attempts,
    _record_attempt,
    _record_attempt_async,
    _safe_internal_redirect
)

# Mirroring actual configuration in api.admin_routes
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 1  # 60 seconds lockout window

@pytest.fixture(autouse=True)
def reset_rate_limiter():
    import api.admin_routes as ar
    ar._login_attempts.clear()
    ar._last_rate_limit_prune = 0.0
    yield

def test_allows_under_limit():
    for _ in range(4):
        _record_attempt("127.0.0.1")
    assert _check_rate_limit("127.0.0.1") is None

def test_blocks_at_limit():
    for _ in range(5):
        _record_attempt("127.0.0.1")
    retry_after = _check_rate_limit("127.0.0.1")
    assert retry_after is not None
    assert retry_after > 0

def test_retry_after_is_positive():
    for _ in range(5):
        _record_attempt("127.0.0.1")
    retry_after = _check_rate_limit("127.0.0.1")
    assert retry_after > 0
    assert retry_after <= LOGIN_LOCKOUT_MINUTES * 60

def test_different_ips_are_independent():
    for _ in range(5):
        _record_attempt("127.0.0.1")
    
    assert _check_rate_limit("127.0.0.1") is not None
    assert _check_rate_limit("192.168.1.1") is None

def test_prune_removes_expired_ips():
    import api.admin_routes as ar
    past_time = time.monotonic() - (LOGIN_LOCKOUT_MINUTES * 60 + 10)
    ar._login_attempts["127.0.0.1"] = [past_time] * 5
    
    _prune_login_attempts(time.monotonic())
    
    assert "127.0.0.1" not in ar._login_attempts

def test_prune_respects_interval():
    import api.admin_routes as ar
    past_time = time.monotonic() - (LOGIN_LOCKOUT_MINUTES * 60 + 10)
    ar._login_attempts["127.0.0.1"] = [past_time] * 5
    
    ar._last_rate_limit_prune = time.monotonic()  # Just pruned
    _prune_login_attempts(time.monotonic())
    
    # Still there because prune skipped
    assert "127.0.0.1" in ar._login_attempts

@pytest.mark.asyncio
async def test_record_attempt_async_rechecks_under_lock():
    """Concurrent reserve-slot path must re-check before recording."""
    for _ in range(MAX_LOGIN_ATTEMPTS):
        assert await _record_attempt_async("10.0.0.9") is None
    blocked = await _record_attempt_async("10.0.0.9")
    assert blocked is not None
    assert blocked > 0
    assert await _check_rate_limit_async("10.0.0.9") is not None

@pytest.mark.asyncio
async def test_concurrent_record_attempt_async_respects_max():
    """Many concurrent coroutines must not exceed the attempt cap."""
    import api.admin_routes as ar

    async def once():
        return await _record_attempt_async("10.0.0.42")

    results = await asyncio.gather(*[once() for _ in range(20)])
    allowed = sum(1 for r in results if r is None)
    blocked = sum(1 for r in results if r is not None)
    assert allowed == MAX_LOGIN_ATTEMPTS
    assert blocked == 20 - MAX_LOGIN_ATTEMPTS
    assert len(ar._login_attempts.get("10.0.0.42", [])) == MAX_LOGIN_ATTEMPTS

def test_safe_redirect_allows_internal():
    assert _safe_internal_redirect("/dashboard") == "/dashboard"

def test_safe_redirect_rejects_external():
    assert _safe_internal_redirect("http://evil.com") == "/"

def test_safe_redirect_rejects_protocol_relative():
    assert _safe_internal_redirect("//evil.com") == "/"

def test_safe_redirect_rejects_no_leading_slash():
    assert _safe_internal_redirect("evil.com/path") == "/"

def test_safe_redirect_empty_returns_slash():
    assert _safe_internal_redirect("") == "/"
    assert _safe_internal_redirect(None) == "/"
