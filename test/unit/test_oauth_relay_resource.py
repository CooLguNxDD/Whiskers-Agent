"""RFC 8707 resource indicator on Layer-2 OAuth authorize/token/refresh."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from oauth.oauth_relay import (
    ExternalOAuthRelay,
    _oauth_form_extras,
    canonical_resource_url,
)


def test_canonical_resource_url_strips_fragment_and_lowercases_host():
    assert (
        canonical_resource_url("https://MCP.Atlassian.com/v2/mcp#frag")
        == "https://mcp.atlassian.com/v2/mcp"
    )


def test_oauth_form_extras_omits_resource_when_unset():
    assert _oauth_form_extras({"authorize_url": "https://x/a", "token_url": "https://x/t"}) == {}


def test_oauth_form_extras_includes_resource():
    assert _oauth_form_extras({"resource": "https://mcp.atlassian.com/v2/mcp"}) == {
        "resource": "https://mcp.atlassian.com/v2/mcp"
    }


@pytest.mark.asyncio
async def test_build_authorize_url_includes_resource_param():
    relay = ExternalOAuthRelay(
        vault=AsyncMock(),
        plugin_manifests={
            "proxy_atlassian": {
                "external_oauth": {
                    "upstream": {
                        "authorize_url": "https://mcp.atlassian.com/v1/authorize",
                        "token_url": "https://mcp.atlassian.com/v1/token",
                        "client_id": "cid",
                        "scopes": ["read"],
                        "resource": "https://mcp.atlassian.com/v2/mcp",
                    }
                }
            }
        },
    )
    relay._ensure_plugin_registered = AsyncMock()
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    with patch("oauth.oauth_relay.get_async_session") as mock_session:
        mock_session.return_value.__aenter__.return_value = session
        url = await relay.build_authorize_url(
            "proxy_atlassian", "upstream", "http://localhost:10000/oauth/plugin/upstream/callback"
        )
    assert "resource=https%3A%2F%2Fmcp.atlassian.com%2Fv2%2Fmcp" in url
    assert "client_id=cid" in url


def test_upstream_oauth_manifest_stamps_canonical_resource():
    from core.proxy.proxy_registration import upstream_oauth_manifest

    manifest = upstream_oauth_manifest(
        "atlassian",
        {"authorize_url": "https://mcp.atlassian.com/v1/authorize", "token_url": "https://mcp.atlassian.com/v1/token", "client_id": "cid"},
        resource="https://MCP.Atlassian.com/v2/mcp#x",
    )
    assert manifest["external_oauth"]["upstream"]["resource"] == "https://mcp.atlassian.com/v2/mcp"
