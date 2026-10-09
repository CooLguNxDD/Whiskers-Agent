"""Public plugin auth boundary carries only validated explicit tenant identity."""
from unittest.mock import AsyncMock

import pytest
from core.auth_service import AuthService


@pytest.mark.asyncio
async def test_api_key_principal_carries_tenant_without_default(monkeypatch):
    from core.api_key_management import store
    lookup = AsyncMock(return_value={"key_id": "synthetic", "subject": "tester", "tenant_id": 7, "scopes": ["all"]})
    monkeypatch.setattr(store, "lookup_active_by_token", lookup)
    svc = AuthService()
    assert (await svc.principal_from_bearer("octk_synthetic")).tenant_id == 7
    for invalid in (None, 0, True, "7"):
        lookup.return_value["tenant_id"] = invalid
        assert (await svc.principal_from_bearer("octk_synthetic")).tenant_id is None


@pytest.mark.asyncio
async def test_jwt_principal_uses_verified_tenant_claims(monkeypatch):
    validate = AsyncMock(return_value={"sub": "tester", "scopes": ["all"], "ocat_tenant": 7})
    svc = AuthService()
    monkeypatch.setattr(svc, "_require_svc", lambda: type("Service", (), {"validate_token": validate})())
    assert (await svc.principal_from_bearer("synthetic-jwt")).tenant_id == 7
    validate.return_value["whiskers_tenant"] = 8
    assert (await svc.principal_from_bearer("synthetic-jwt")).tenant_id == 8
    validate.return_value["whiskers_tenant"] = True
    assert (await svc.principal_from_bearer("synthetic-jwt")).tenant_id is None
