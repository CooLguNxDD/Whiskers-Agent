"""Tests for portfolio delete tools (delete_project / delete_job_layout / delete_bake_runs).

No DB, no network: store + vector helpers + ``execute_operation`` are patched.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from plugins.portfolio_plugin.MCPTools.delete_tools import (
    BAKE_RUN_ID_LIST_CAP,
    delete_bake_runs,
    delete_job_layout,
    delete_project,
)

TENANT = 7
CTX = "portfolio_plugin__context"
LAYOUTS = "portfolio_plugin__layouts"
_VSTORE = "db_layer.search_content_vectors_store"
_STORE = "plugins.portfolio_plugin.store"
_EXEC = "core.route_registry.execute.execute_operation"
_TENANT_FN = "plugins.portfolio_plugin.tenant.require_tenant_id"

PROJECT_ROW = {"slug": "my-proj", "name": "My Proj", "is_active": True}
LAYOUT_ROW = {"short_id": "abc123", "tenant_id": TENANT}


@pytest.fixture
def mocks():
    """Patch every collaborator; yields a dict of the mocks keyed by short name."""
    with ExitStack() as stack:
        def p(target, **kw):
            return stack.enter_context(patch(target, **kw))

        m = {
            "tenant": p(_TENANT_FN, return_value=TENANT),
            "get_project": p(f"{_STORE}.get_project", new=AsyncMock(return_value=PROJECT_ROW)),
            "delete_project": p(f"{_STORE}.delete_project", new=AsyncMock(return_value=True)),
            "get_layout": p(f"{_STORE}.get_job_layout_for_tenant", new=AsyncMock(return_value=LAYOUT_ROW)),
            "forks": p(f"{_STORE}.list_job_layout_forks", new=AsyncMock(return_value=[])),
            "delete_layout": p(f"{_STORE}.delete_job_layout", new=AsyncMock(return_value=True)),
            "select_runs": p(
                f"{_STORE}.select_bake_runs_for_delete",
                new=AsyncMock(return_value=(2, ["r1", "r2"])),
            ),
            "delete_runs": p(f"{_STORE}.delete_bake_runs", new=AsyncMock(return_value=2)),
            "count_vec": p(
                f"{_VSTORE}.count_search_content_vectors_by_meta", new=AsyncMock(return_value=3)
            ),
            "delete_vec": p(
                f"{_VSTORE}.delete_search_content_vectors_by_meta", new=AsyncMock(return_value=3)
            ),
            "exec": p(_EXEC, new=AsyncMock(return_value={"applications": []})),
        }
        yield m


def _no_writes(m):
    for k in ("delete_project", "delete_layout", "delete_runs", "delete_vec"):
        m[k].assert_not_awaited()


# --------------------------------------------------------------------- delete_project


@pytest.mark.asyncio
async def test_delete_project_dry_run_is_default_and_writes_nothing(mocks):
    res = await delete_project("my-proj")
    assert res["status"] == "ok"
    assert res["dry_run"] is True
    assert res["project_deleted"] is True
    assert res["vectors_deleted"] == 3
    assert res["vector_collection"] == CTX
    assert any("re-create" in w and "is_active=false" in w for w in res["warnings"])
    _no_writes(mocks)
    mocks["count_vec"].assert_awaited_once_with(CTX, "slug_hint", "my-proj", tenant_id=TENANT)


@pytest.mark.asyncio
async def test_delete_project_real_run_uses_principal_tenant(mocks):
    res = await delete_project("my-proj", dry_run=False)
    assert res["status"] == "ok" and res["dry_run"] is False
    assert res["project_deleted"] is True and res["vectors_deleted"] == 3
    mocks["delete_project"].assert_awaited_once_with("my-proj", TENANT)
    mocks["delete_vec"].assert_awaited_once_with(CTX, "slug_hint", "my-proj", tenant_id=TENANT)
    mocks["get_project"].assert_awaited_once_with("my-proj", tenant_id=TENANT)


@pytest.mark.asyncio
async def test_delete_project_dry_and_real_have_same_keys(mocks):
    dry = await delete_project("my-proj")
    real = await delete_project("my-proj", dry_run=False)
    assert set(dry) == set(real)


@pytest.mark.asyncio
async def test_delete_project_not_found_is_clean(mocks):
    mocks["get_project"].return_value = None
    mocks["count_vec"].return_value = 0
    res = await delete_project("nope", dry_run=False)
    assert res == {"status": "not_found", "dry_run": False, "slug": "nope"}
    _no_writes(mocks)


@pytest.mark.asyncio
async def test_delete_project_orphan_vectors_without_row_are_removed(mocks):
    mocks["get_project"].return_value = None
    res = await delete_project("gone", dry_run=False)
    assert res["status"] == "ok" and res["project_deleted"] is False
    mocks["delete_vec"].assert_awaited_once()
    mocks["delete_project"].assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_project_fails_closed_without_tenant(mocks):
    mocks["tenant"].return_value = None
    res = await delete_project("my-proj", dry_run=False)
    assert res == {"status": "error", "error": "tenant_unresolved"}
    mocks["get_project"].assert_not_awaited()
    mocks["count_vec"].assert_not_awaited()
    _no_writes(mocks)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, 0, -5, "abc"])
async def test_require_tenant_id_rejects_unresolved(raw):
    from core.context import current_tenant_id
    from plugins.portfolio_plugin.tenant import require_tenant_id

    token = current_tenant_id.set(raw)
    try:
        assert require_tenant_id() is None
    finally:
        current_tenant_id.reset(token)


@pytest.mark.asyncio
async def test_delete_project_requires_slug(mocks):
    res = await delete_project("  ", dry_run=False)
    assert res["error"] == "missing_required_fields"
    _no_writes(mocks)


# ------------------------------------------------------------------ delete_job_layout


@pytest.mark.asyncio
async def test_delete_job_layout_dry_run_writes_nothing(mocks):
    res = await delete_job_layout("abc123")
    assert res["status"] == "ok" and res["dry_run"] is True
    assert res["layout_deleted"] is True and res["vectors_deleted"] == 3
    assert res["vector_collection"] == LAYOUTS
    assert res["reference_check"] == "ok"
    _no_writes(mocks)
    mocks["count_vec"].assert_awaited_once_with(LAYOUTS, "short_id", "abc123", tenant_id=TENANT)


@pytest.mark.asyncio
async def test_delete_job_layout_real_run_uses_principal_tenant(mocks):
    res = await delete_job_layout("abc123", dry_run=False)
    assert res["status"] == "ok" and res["layout_deleted"] is True
    mocks["delete_layout"].assert_awaited_once_with("abc123", TENANT)
    mocks["delete_vec"].assert_awaited_once_with(LAYOUTS, "short_id", "abc123", tenant_id=TENANT)
    mocks["get_layout"].assert_awaited_once_with("abc123", TENANT)
    mocks["forks"].assert_awaited_once_with("abc123", TENANT)


@pytest.mark.asyncio
async def test_delete_job_layout_dispatches_reference_op_via_catalog(mocks):
    await delete_job_layout("abc123")
    mocks["exec"].assert_awaited_once()
    args, kwargs = mocks["exec"].await_args
    assert args[:3] == (
        "job_search_plugin",
        "job_search_plugin__list_applications_for_portfolio_job",
        {"portfolio_job_id": "abc123"},
    )


def _catalog_with(known: dict):
    """execute_operation double: only op ids in *known* resolve, others raise not_found."""
    from core.route_registry.execute import ExecuteError

    async def fake(plugin_id, operation_id, args, **kw):
        if operation_id not in known:
            raise ExecuteError("not_found", f"Unknown operation ({plugin_id!r}, {operation_id!r})", status=404)
        return known[operation_id]

    return fake


@pytest.mark.asyncio
async def test_delete_job_layout_force_check_sees_applications_via_qualified_op(mocks):
    # Only the qualified id is registered, as static_tool_loader._qualify does.
    mocks["exec"].side_effect = _catalog_with(
        {"job_search_plugin__list_applications_for_portfolio_job": {"applications": [{"id": 9}]}}
    )
    res = await delete_job_layout("abc123", dry_run=False)
    assert res["status"] == "refused" and res["reason"] == "referenced_by_applications"
    assert res["applications"] == [9] and res["reference_check"] == "ok"
    _no_writes(mocks)


@pytest.mark.asyncio
async def test_delete_job_layout_falls_back_to_unqualified_op(mocks):
    mocks["exec"].side_effect = _catalog_with(
        {"list_applications_for_portfolio_job": {"applications": [{"id": 4}]}}
    )
    res = await delete_job_layout("abc123", dry_run=False)
    assert res["status"] == "refused" and res["applications"] == [4]


@pytest.mark.asyncio
async def test_delete_job_layout_unknown_op_refuses_without_force(mocks):
    mocks["exec"].side_effect = _catalog_with({})
    res = await delete_job_layout("abc123", dry_run=False)
    assert res["status"] == "refused" and res["reason"] == "reference_check_unavailable"
    assert res["reference_check"] == "unavailable" and res["reference_check_reason"] == "ExecuteError"
    _no_writes(mocks)
    forced = await delete_job_layout("abc123", force=True, dry_run=False)
    assert forced["status"] == "ok" and forced["reference_check"] == "unavailable"


@pytest.mark.asyncio
async def test_delete_job_layout_fails_closed_without_tenant(mocks):
    mocks["tenant"].return_value = None
    res = await delete_job_layout("abc123", dry_run=False)
    assert res == {"status": "error", "error": "tenant_unresolved"}
    mocks["get_layout"].assert_not_awaited()
    mocks["exec"].assert_not_awaited()
    _no_writes(mocks)


@pytest.mark.asyncio
async def test_delete_job_layout_refuses_when_applications_reference_it(mocks):
    mocks["exec"].return_value = {"applications": [{"id": 11}, {"id": 12}]}
    res = await delete_job_layout("abc123", dry_run=False)
    assert res["status"] == "refused"
    assert res["reason"] == "referenced_by_applications"
    assert res["applications"] == [11, 12]
    _no_writes(mocks)


@pytest.mark.asyncio
async def test_delete_job_layout_dry_run_also_refuses(mocks):
    mocks["exec"].return_value = {"applications": [{"id": 11}]}
    res = await delete_job_layout("abc123")
    assert res["status"] == "refused" and res["dry_run"] is True


@pytest.mark.asyncio
async def test_delete_job_layout_force_proceeds_despite_applications(mocks):
    mocks["exec"].return_value = {"applications": [{"id": 11}]}
    res = await delete_job_layout("abc123", force=True, dry_run=False)
    assert res["status"] == "ok" and res["forced"] is True
    assert res["applications"] == [11]
    mocks["delete_layout"].assert_awaited_once_with("abc123", TENANT)
    mocks["delete_vec"].assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_job_layout_unwraps_data_envelope(mocks):
    mocks["exec"].return_value = {"_meta": {}, "data": {"applications": [{"id": 5}]}}
    res = await delete_job_layout("abc123", dry_run=False)
    assert res["status"] == "refused" and res["applications"] == [5]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "effect",
    [
        {"side_effect": RuntimeError("no such op")},
        {"return_value": {"status": "error", "message": "boom"}},
        {"return_value": {"nope": 1}},
        {"return_value": None},
    ],
)
async def test_delete_job_layout_reference_check_unavailable_refuses_without_force(mocks, effect):
    mocks["exec"].side_effect = effect.get("side_effect")
    if "return_value" in effect:
        mocks["exec"].return_value = effect["return_value"]
    res = await delete_job_layout("abc123", dry_run=False)
    assert res["status"] == "refused"
    assert res["reason"] == "reference_check_unavailable"
    assert res["reference_check"] == "unavailable"
    _no_writes(mocks)


@pytest.mark.asyncio
async def test_delete_job_layout_force_proceeds_when_reference_check_unavailable(mocks):
    mocks["exec"].side_effect = RuntimeError("missing")
    res = await delete_job_layout("abc123", force=True, dry_run=False)
    assert res["status"] == "ok"
    assert res["reference_check"] == "unavailable"
    mocks["delete_layout"].assert_awaited_once_with("abc123", TENANT)


@pytest.mark.asyncio
async def test_delete_job_layout_lists_forks_but_never_deletes_them(mocks):
    mocks["forks"].return_value = ["fork01", "fork02"]
    res = await delete_job_layout("abc123", dry_run=False)
    assert res["status"] == "ok"
    assert res["forks"] == ["fork01", "fork02"]
    # Only the parent short_id is ever passed to a delete.
    mocks["delete_layout"].assert_awaited_once_with("abc123", TENANT)
    mocks["delete_vec"].assert_awaited_once_with(LAYOUTS, "short_id", "abc123", tenant_id=TENANT)


@pytest.mark.asyncio
async def test_delete_job_layout_not_found_is_clean(mocks):
    mocks["get_layout"].return_value = None
    mocks["count_vec"].return_value = 0
    res = await delete_job_layout("zzz", dry_run=False)
    assert res == {"status": "not_found", "dry_run": False, "short_id": "zzz"}
    mocks["exec"].assert_not_awaited()
    _no_writes(mocks)


# ------------------------------------------------------------------- delete_bake_runs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"run_id": "r1", "older_than_days": 5},
        {"older_than_days": 0},
        {"older_than_days": -3},
        {"older_than_days": "7"},
        {"older_than_days": 2.5},
        {"older_than_days": True},
    ],
)
async def test_delete_bake_runs_argument_validation(mocks, kwargs):
    res = await delete_bake_runs(dry_run=False, **kwargs)
    assert res["status"] == "error" and res["error"] == "invalid_arguments"
    mocks["select_runs"].assert_not_awaited()
    _no_writes(mocks)


@pytest.mark.asyncio
async def test_delete_bake_runs_blank_run_id_rejected(mocks):
    res = await delete_bake_runs(run_id="  ", dry_run=False)
    assert res["error"] == "missing_required_fields"
    _no_writes(mocks)


@pytest.mark.asyncio
async def test_delete_bake_runs_fails_closed_without_tenant(mocks):
    mocks["tenant"].return_value = None
    res = await delete_bake_runs(run_id="r1", dry_run=False)
    assert res == {"status": "error", "error": "tenant_unresolved"}
    mocks["select_runs"].assert_not_awaited()
    _no_writes(mocks)


@pytest.mark.asyncio
async def test_delete_bake_runs_dry_run_by_run_id(mocks):
    res = await delete_bake_runs(run_id="r1")
    assert res["status"] == "ok" and res["dry_run"] is True
    assert res["run_ids"] == ["r1", "r2"] and res["total"] == 2
    _no_writes(mocks)
    mocks["select_runs"].assert_awaited_once_with(
        tenant_id=TENANT, run_id="r1", created_before=None, limit=BAKE_RUN_ID_LIST_CAP
    )


@pytest.mark.asyncio
async def test_delete_bake_runs_real_run_by_run_id(mocks):
    res = await delete_bake_runs(run_id="r1", dry_run=False)
    assert res["status"] == "ok" and res["deleted"] == 2
    mocks["delete_runs"].assert_awaited_once_with(
        tenant_id=TENANT, run_id="r1", created_before=None
    )


@pytest.mark.asyncio
async def test_delete_bake_runs_older_than_days_filters_on_cutoff(mocks):
    before = datetime.now(timezone.utc)
    res = await delete_bake_runs(older_than_days=30, dry_run=False)
    after = datetime.now(timezone.utc)
    assert res["status"] == "ok"
    kw = mocks["delete_runs"].await_args.kwargs
    assert kw["tenant_id"] == TENANT and kw["run_id"] is None
    assert before - timedelta(days=30) <= kw["created_before"] <= after - timedelta(days=30)
    assert mocks["select_runs"].await_args.kwargs["created_before"] == kw["created_before"]


@pytest.mark.asyncio
async def test_delete_bake_runs_caps_listed_ids_and_reports_total(mocks):
    mocks["select_runs"].return_value = (450, [f"r{i}" for i in range(BAKE_RUN_ID_LIST_CAP)])
    res = await delete_bake_runs(older_than_days=1)
    assert res["total"] == 450 and len(res["run_ids"]) == BAKE_RUN_ID_LIST_CAP
    assert res["truncated"] is True
    assert mocks["select_runs"].await_args.kwargs["limit"] == BAKE_RUN_ID_LIST_CAP


@pytest.mark.asyncio
async def test_delete_bake_runs_nothing_matching_is_clean(mocks):
    mocks["select_runs"].return_value = (0, [])
    res = await delete_bake_runs(run_id="ghost", dry_run=False)
    assert res["status"] == "not_found" and res["total"] == 0
    _no_writes(mocks)


# --------------------------------------------------------- compiled-statement tenancy


class _CaptureSession:
    """Async-session stand-in recording every statement passed to ``execute``."""

    def __init__(self):
        self.stmts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        self.stmts.append((stmt, params))
        res = MagicMock()
        res.rowcount = 1
        res.scalar.return_value = 1
        res.scalar_one_or_none.return_value = None
        res.scalars.return_value.all.return_value = []
        return res

    async def commit(self):
        pass


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_store_statements_are_tenant_scoped():
    from plugins.portfolio_plugin import store

    session = _CaptureSession()
    with patch(f"{_STORE}.get_async_session", return_value=session):
        await store.get_job_layout_for_tenant("abc", 7)
        await store.list_job_layout_forks("abc", 7)
        await store.delete_job_layout("abc", 7)
        await store.select_bake_runs_for_delete(tenant_id=7, run_id="r1")
        await store.delete_bake_runs(tenant_id=7, run_id="r1")
        await store.delete_bake_runs(
            tenant_id=7, created_before=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
    assert len(session.stmts) == 7  # select_bake_runs_for_delete runs a count + an id select
    for stmt, _ in session.stmts:
        sql = _sql(stmt)
        assert "tenant_id = 7" in sql, sql


@pytest.mark.asyncio
async def test_store_older_than_statement_filters_created_at():
    from plugins.portfolio_plugin import store

    session = _CaptureSession()
    with patch(f"{_STORE}.get_async_session", return_value=session):
        await store.delete_bake_runs(
            tenant_id=7, created_before=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
    sql = _sql(session.stmts[0][0])
    assert sql.startswith("DELETE FROM portfolio_bake_runs")
    assert "created_at <" in sql and "tenant_id = 7" in sql
    assert "run_id" not in sql


@pytest.mark.asyncio
async def test_store_delete_bake_runs_requires_selector():
    from plugins.portfolio_plugin import store

    with pytest.raises(ValueError):
        await store.delete_bake_runs(tenant_id=7)


@pytest.mark.asyncio
async def test_store_delete_project_statement_is_tenant_scoped():
    from plugins.portfolio_plugin import store

    session = _CaptureSession()
    with patch(f"{_STORE}.get_async_session", return_value=session):
        await store.delete_project("slug", 7)
    assert "tenant_id = 7" in _sql(session.stmts[0][0])


@pytest.mark.asyncio
async def test_vector_count_and_delete_by_meta_bind_tenant():
    from db_layer import search_content_vectors_store as vs

    session = _CaptureSession()
    with patch.object(vs, "get_async_session", return_value=session):
        await vs.count_search_content_vectors_by_meta(CTX, "slug_hint", "s", tenant_id=7)
        await vs.delete_search_content_vectors_by_meta(CTX, "slug_hint", "s", tenant_id=7)
    count_stmt, count_params = session.stmts[0]
    del_stmt, del_params = session.stmts[1]
    assert "tenant_id = :tenant_id" in str(count_stmt) and count_params["tenant_id"] == 7
    assert str(count_stmt).lstrip().startswith("SELECT COUNT(*)")
    assert "tenant_id = :tenant_id" in str(del_stmt) and del_params["tenant_id"] == 7


def test_portfolio_plugin_never_imports_job_search_plugin():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pat = re.compile(r"^\s*(from|import)\s+plugins\.job_search_plugin", re.M)
    offenders = [
        str(p) for p in root.rglob("*.py")
        if p.name != "test_delete_tools.py" and pat.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []
