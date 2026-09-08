"""MTU-2 contract: httpx client teardown is idempotent and exception-safe.

ExternalOAuthRelay and PluginAuthRegistry each hold a persistent
``httpx.AsyncClient``. ``aclose()`` must be safe to call more than once and must
not raise when the client was never (or only half-) initialised, so a partially
failed startup or a double teardown can never leak the connection pool.
"""

import pytest

from oauth.oauth_relay import ExternalOAuthRelay
from core.plugin_loader.plugin_auth_registry import PluginAuthRegistry


class _FakeHttp:
    def __init__(self):
        self.close_count = 0

    async def aclose(self):
        self.close_count += 1


@pytest.mark.asyncio
async def test_relay_aclose_idempotent():
    relay = ExternalOAuthRelay.__new__(ExternalOAuthRelay)
    fake = _FakeHttp()
    relay._http = fake
    await relay.aclose()
    await relay.aclose()  # second call must be a safe no-op
    assert fake.close_count == 1


@pytest.mark.asyncio
async def test_relay_aclose_no_client():
    # Half-initialised instance: _http never assigned -> must not raise.
    relay = ExternalOAuthRelay.__new__(ExternalOAuthRelay)
    await relay.aclose()


@pytest.mark.asyncio
async def test_auth_registry_aclose_idempotent():
    reg = PluginAuthRegistry.__new__(PluginAuthRegistry)
    fake = _FakeHttp()
    reg._http = fake
    await reg.aclose()
    await reg.aclose()
    assert fake.close_count == 1


@pytest.mark.asyncio
async def test_auth_registry_aclose_no_client():
    reg = PluginAuthRegistry.__new__(PluginAuthRegistry)
    await reg.aclose()
