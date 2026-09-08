import time
import pytest
from oauth.oauth_provider import LegacyOAuthProvider


def test_sweep_expired_entry_removed():
    """Verify that an expired pending entry (older than 10 minutes) is removed by sweep."""
    provider = LegacyOAuthProvider.__new__(LegacyOAuthProvider)
    provider._pending_auths = {}
    provider._pending_pkce = {}

    now = time.time()
    expired_time = now - 700

    provider._pending_auths["expired_state"] = {
        "client_id": "test_client",
        "_created_at": expired_time,
    }
    provider._pending_pkce["expired_state"] = {
        "code_verifier": "test_verifier",
        "_created_at": expired_time,
    }

    provider._sweep_pending()

    assert "expired_state" not in provider._pending_auths
    assert "expired_state" not in provider._pending_pkce


def test_sweep_fresh_entry_retained():
    """Verify that a fresh pending entry is retained by sweep."""
    provider = LegacyOAuthProvider.__new__(LegacyOAuthProvider)
    provider._pending_auths = {}
    provider._pending_pkce = {}

    now = time.time()

    provider._pending_auths["fresh_state"] = {
        "client_id": "test_client",
        "_created_at": now,
    }
    provider._pending_pkce["fresh_state"] = {
        "code_verifier": "test_verifier",
        "_created_at": now,
    }

    provider._sweep_pending()

    assert "fresh_state" in provider._pending_auths
    assert "fresh_state" in provider._pending_pkce
    assert provider._pending_auths["fresh_state"]["client_id"] == "test_client"
    assert provider._pending_pkce["fresh_state"]["code_verifier"] == "test_verifier"
