"""MTU-3 contract: in-memory auth dicts evict stale entries (no unbounded growth).

Two in-memory maps grew without bound on long-lived servers:
  * ``OAuthService_FastMCPProvider._pending_auths`` — abandoned authorize flows
    were only removed on successful callback, never on timeout.
  * ``ExternalOAuthRelay._refresh_locks`` — one asyncio.Lock per
    (plugin_id, provider) pair, pruned only when the dict hit 1000 entries.

Both now carry per-entry timestamps and a sweep that evicts entries older than
a TTL. A lock that is currently held must never be evicted.
"""

import asyncio
import time

import pytest

import oauth.oauth_service as svc_mod
from oauth.oauth_service import OAuthService_FastMCPProvider, AUTH_CODE_TTL
from oauth.oauth_relay import ExternalOAuthRelay


def _new_provider():
    p = OAuthService_FastMCPProvider.__new__(OAuthService_FastMCPProvider)
    p._pending_auths = {}
    p._pending_ts = {}
    return p


def test_sweep_pending_auths_evicts_stale():
    p = _new_provider()
    now = svc_mod._now().timestamp()
    p._pending_auths = {"old": {"client_id": "a"}, "fresh": {"client_id": "b"}}
    p._pending_ts = {"old": now - (AUTH_CODE_TTL + 100), "fresh": now}

    removed = p._sweep_pending_auths()

    assert removed == 1
    assert "old" not in p._pending_auths
    assert "old" not in p._pending_ts
    assert "fresh" in p._pending_auths


def test_sweep_pending_auths_keeps_fresh():
    p = _new_provider()
    now = svc_mod._now().timestamp()
    p._pending_auths = {"a": {}, "b": {}}
    p._pending_ts = {"a": now, "b": now}
    assert p._sweep_pending_auths() == 0
    assert len(p._pending_auths) == 2


def _new_relay():
    r = ExternalOAuthRelay.__new__(ExternalOAuthRelay)
    r._refresh_locks = {}
    r._refresh_lock_ts = {}
    r._manifests = {}
    return r


@pytest.mark.asyncio
async def test_sweep_refresh_locks_evicts_old_unheld():
    r = _new_relay()
    k_old = ("p1", "prov")
    k_fresh = ("p2", "prov")
    r._refresh_locks = {k_old: asyncio.Lock(), k_fresh: asyncio.Lock()}
    now = time.monotonic()
    r._refresh_lock_ts = {k_old: now - 100_000, k_fresh: now}

    removed = r._sweep_refresh_locks(ttl=3600)

    assert k_old not in r._refresh_locks
    assert k_old not in r._refresh_lock_ts
    assert k_fresh in r._refresh_locks
    assert removed == 1


@pytest.mark.asyncio
async def test_sweep_refresh_locks_never_evicts_held_lock():
    r = _new_relay()
    k_held = ("p1", "prov")
    r._refresh_locks = {k_held: asyncio.Lock()}
    r._refresh_lock_ts = {k_held: time.monotonic() - 100_000}  # old

    await r._refresh_locks[k_held].acquire()
    try:
        removed = r._sweep_refresh_locks(ttl=3600)
    finally:
        r._refresh_locks[k_held].release()

    assert removed == 0
    assert k_held in r._refresh_locks  # held lock survives despite age
