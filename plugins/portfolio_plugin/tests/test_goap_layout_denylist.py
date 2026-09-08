"""GOAP denylist for REST-only portfolio layout generation."""

from db_layer.embeddings.embeddings_routes import (
    GOAP_CANDIDATE_DENYLIST,
    disabled_route_keys,
)


def test_generate_layout_for_query_in_denylist():
    """generate_layout_for_query must never enter the GOAP candidate pool."""
    key = ("portfolio_plugin", "portfolio_plugin__generate_layout_for_query")
    assert key in GOAP_CANDIDATE_DENYLIST


def test_ingest_tools_in_denylist():
    """Operator ingest/reconcile tools must never enter the GOAP candidate pool."""
    assert (
        "portfolio_plugin",
        "portfolio_plugin__ingest_portfolio_context",
    ) in GOAP_CANDIDATE_DENYLIST
    assert (
        "portfolio_plugin",
        "portfolio_plugin__reconcile_portfolio_projects",
    ) in GOAP_CANDIDATE_DENYLIST


def test_bake_and_patch_in_denylist():
    """Operator bake/patch writes must never enter the GOAP candidate pool."""
    assert (
        "portfolio_plugin",
        "portfolio_plugin__bake_portfolio_for_job",
    ) in GOAP_CANDIDATE_DENYLIST
    assert (
        "portfolio_plugin",
        "portfolio_plugin__patch_job_layout",
    ) in GOAP_CANDIDATE_DENYLIST
    assert (
        "portfolio_plugin",
        "portfolio_plugin__list_bake_runs",
    ) in GOAP_CANDIDATE_DENYLIST


import pytest


@pytest.mark.asyncio
async def test_disabled_route_keys_includes_denylist(monkeypatch):
    """disabled_route_keys unions DB flags with the static denylist."""

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *args, **kwargs):
            class _R:
                def all(self_inner):
                    return []

            return _R()

    monkeypatch.setattr(
        "db_layer.embeddings.embeddings_routes.get_async_session",
        lambda: _FakeSession(),
    )
    keys = await disabled_route_keys()
    assert ("portfolio_plugin", "portfolio_plugin__generate_layout_for_query") in keys
