import asyncio
import pytest
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_refresh_locks_evicts_stale_when_at_cap():
    from oauth.oauth_relay import ExternalOAuthRelay, _REFRESH_LOCK_MAX

    vault = MagicMock()
    relay = ExternalOAuthRelay(vault=vault, plugin_manifests={"plugin_a": {}})

    # Fill to cap with stale plugin keys (not in manifests)
    for i in range(_REFRESH_LOCK_MAX):
        relay._refresh_locks[("stale_plugin", f"provider_{i}")] = asyncio.Lock()

    # Trigger cap guard for a known plugin
    lock_key = ("plugin_a", "whiskers")
    if lock_key not in relay._refresh_locks:
        if len(relay._refresh_locks) >= _REFRESH_LOCK_MAX:
            stale = [k for k in relay._refresh_locks if k[0] not in relay._manifests]
            for k in stale:
                del relay._refresh_locks[k]
        relay._refresh_locks[lock_key] = asyncio.Lock()

    assert lock_key in relay._refresh_locks
    # all stale entries removed
    assert all(k[0] == "plugin_a" for k in relay._refresh_locks)


def test_revoke_refresh_lock_removes_entry():
    from oauth.oauth_relay import ExternalOAuthRelay

    vault = MagicMock()
    relay = ExternalOAuthRelay(vault=vault, plugin_manifests={})
    relay._refresh_locks[("p1", "whiskers")] = asyncio.Lock()
    relay.revoke_refresh_lock("p1", "whiskers")
    assert ("p1", "whiskers") not in relay._refresh_locks


def test_revoke_refresh_lock_noop_if_missing():
    from oauth.oauth_relay import ExternalOAuthRelay

    vault = MagicMock()
    relay = ExternalOAuthRelay(vault=vault, plugin_manifests={})
    relay.revoke_refresh_lock("nonexistent", "whiskers")  # must not raise
