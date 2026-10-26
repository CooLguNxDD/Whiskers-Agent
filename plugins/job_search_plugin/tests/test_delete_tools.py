"""Delete tools: tenant scoping, dry-run-first, guarded PDF cleanup (no DB, no network)."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from core.artifact_store import DEFAULT_BUCKET
from plugins.job_search_plugin import store
from plugins.job_search_plugin.MCPTools import delete_tools as dt

TENANT = 7


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def tenant():
    from core.context import current_tenant_id

    token = current_tenant_id.set(TENANT)
    yield TENANT
    current_tenant_id.reset(token)


class FakeArtifacts:
    def __init__(self, fail_on=None, missing=()):
        self.removed = []
        self.fail_on = fail_on
        self.missing = set(missing)

    async def remove_bytes(self, bucket, key):
        if key == self.fail_on:
            raise RuntimeError("minio down")
        if key in self.missing:
            err = RuntimeError("gone")
            err.code = "NoSuchKey"
            raise err
        self.removed.append((bucket, key))


@pytest.fixture
def artifacts(monkeypatch):
    fake = FakeArtifacts()
    monkeypatch.setattr(dt, "get_artifact_store", lambda: fake)
    return fake


def app_row(app_id=1, profile_id=5, resume=None, cover=None):
    return {
        "id": app_id,
        "applicant_profile_id": profile_id,
        "job_id": "job-1",
        "provider": "greenhouse",
        "status": "drafted",
        "resume_object_key": resume,
        "cover_letter_object_key": cover,
        "portfolio_job_id": None,
    }


def patch_store(monkeypatch, **overrides):
    """Replace every store call the tools use with an AsyncMock; return the mocks."""
    defaults = {
        "get_application_scoped": None,
        "get_profile_scoped": None,
        "list_applications_for_profile_scoped": [],
        "list_applications_for_portfolio_job_scoped": [],
        "count_profile_embeddings_scoped": 0,
        "find_shared_object_keys": set(),
        "delete_application_scoped": True,
        "delete_profile_scoped": {
            "deleted": True, "applications_deleted": 0, "embeddings_deleted": 0,
        },
        "get_liveness_by_hash": None,
        "delete_liveness_scoped": True,
    }
    defaults.update(overrides)
    mocks = {}
    for name, value in defaults.items():
        mock = AsyncMock(return_value=value)
        monkeypatch.setattr(store, name, mock)
        mocks[name] = mock
    return mocks


# --- tenant fail-closed ------------------------------------------------------

BAD_TENANTS = [None, 0, -3]


@pytest.mark.parametrize("bad", BAD_TENANTS)
def test_every_tool_fails_closed_without_positive_tenant(monkeypatch, artifacts, bad):
    from core.context import current_tenant_id

    mocks = patch_store(monkeypatch)
    token = current_tenant_id.set(bad)
    try:
        results = [
            run(dt.delete_application(1, dry_run=False)),
            run(dt.delete_applicant_profile(1, force=True, dry_run=False)),
            run(dt.delete_job_liveness(url="https://x.test/1", dry_run=False)),
            run(dt.list_applications_for_portfolio_job("abc")),
        ]
    finally:
        current_tenant_id.reset(token)
    for res in results:
        assert res["status"] == "error" and res["error"] == "unauthorized"
    for mock in mocks.values():
        mock.assert_not_called()
    assert artifacts.removed == []


# --- delete_application ------------------------------------------------------


def test_delete_application_dry_run_writes_nothing(monkeypatch, tenant, artifacts):
    mocks = patch_store(
        monkeypatch,
        get_application_scoped=app_row(resume="5/job-1/resume.pdf", cover="5/job-1/cover_letter.pdf"),
    )
    res = run(dt.delete_application(1))
    assert res["dry_run"] is True and res["status"] == "ok"
    assert res["deleted_ids"] == [1]
    assert res["application"] == {
        "id": 1, "job_id": "job-1", "provider": "greenhouse", "status": "drafted",
    }
    assert res["pdf_keys"]["removed"] == ["5/job-1/resume.pdf", "5/job-1/cover_letter.pdf"]
    mocks["delete_application_scoped"].assert_not_called()
    assert artifacts.removed == []
    mocks["get_application_scoped"].assert_awaited_once_with(1, TENANT)


def test_delete_application_real_run_removes_pdfs_then_row(monkeypatch, tenant, artifacts):
    mocks = patch_store(
        monkeypatch,
        get_application_scoped=app_row(resume="5/job-1/resume.pdf", cover="5/job-1/cover_letter.pdf"),
    )
    res = run(dt.delete_application(1, dry_run=False))
    assert res["status"] == "ok" and res["dry_run"] is False
    assert res["pdf_keys"]["removed"] == ["5/job-1/resume.pdf", "5/job-1/cover_letter.pdf"]
    assert artifacts.removed == [
        (DEFAULT_BUCKET, "5/job-1/resume.pdf"),
        (DEFAULT_BUCKET, "5/job-1/cover_letter.pdf"),
    ]
    mocks["delete_application_scoped"].assert_awaited_once_with(1, TENANT)
    # shared-key lookup excludes the row being deleted, same tenant
    args, kwargs = mocks["find_shared_object_keys"].call_args
    assert args[1] == TENANT and kwargs["exclude_application_ids"] == [1]


def test_dry_run_and_real_run_return_same_keys(monkeypatch, tenant, artifacts):
    patch_store(monkeypatch, get_application_scoped=app_row(resume="5/a.pdf"))
    dry = run(dt.delete_application(1))
    real = run(dt.delete_application(1, dry_run=False))
    assert set(dry) == set(real)
    assert dry["pdf_keys"] == real["pdf_keys"]


def test_delete_application_not_found_is_clean(monkeypatch, tenant, artifacts):
    mocks = patch_store(monkeypatch)
    res = run(dt.delete_application(99, dry_run=False))
    assert res["status"] == "not_found" and res["dry_run"] is False
    mocks["delete_application_scoped"].assert_not_called()


@pytest.mark.parametrize("bad_id", [None, 0, -1, "abc", True])
def test_delete_application_rejects_bad_id(monkeypatch, tenant, artifacts, bad_id):
    mocks = patch_store(monkeypatch)
    assert run(dt.delete_application(bad_id))["status"] == "error"
    mocks["get_application_scoped"].assert_not_called()


# --- key guard ---------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "9/job-1/resume.pdf",  # foreign profile prefix
        "50/job-1/resume.pdf",  # prefix of another id that merely starts with "5"
        "5/../9/job-1/resume.pdf",  # traversal
        "5/job-1/../../x.pdf",
        "/5/job-1/resume.pdf",  # leading slash
        "",  # empty
        "5/",  # prefix only
        "5\\job-1\\resume.pdf",  # backslash
    ],
)
def test_invalid_keys_are_skipped_never_removed(monkeypatch, tenant, artifacts, key):
    mocks = patch_store(monkeypatch, get_application_scoped=app_row(profile_id=5, resume=key))
    res = run(dt.delete_application(1, dry_run=False))
    assert res["status"] == "ok"
    assert res["pdf_keys"]["skipped_invalid"] == [key]
    assert res["pdf_keys"]["removed"] == []
    assert artifacts.removed == []
    mocks["delete_application_scoped"].assert_awaited_once()  # row still deleted


def test_none_key_is_ignored(monkeypatch, tenant, artifacts):
    patch_store(monkeypatch, get_application_scoped=app_row(resume=None, cover=None))
    res = run(dt.delete_application(1, dry_run=False))
    assert res["pdf_keys"] == {"removed": [], "kept_shared": [], "skipped_invalid": []}


def test_duplicate_key_removed_once(monkeypatch, tenant, artifacts):
    patch_store(
        monkeypatch, get_application_scoped=app_row(resume="5/a.pdf", cover="5/a.pdf")
    )
    run(dt.delete_application(1, dry_run=False))
    assert artifacts.removed == [(DEFAULT_BUCKET, "5/a.pdf")]


# --- shared-key guard --------------------------------------------------------


def test_shared_key_is_kept(monkeypatch, tenant, artifacts):
    patch_store(
        monkeypatch,
        get_application_scoped=app_row(resume="5/a.pdf", cover="5/b.pdf"),
        find_shared_object_keys={"5/a.pdf"},
    )
    res = run(dt.delete_application(1, dry_run=False))
    assert res["pdf_keys"]["kept_shared"] == ["5/a.pdf"]
    assert res["pdf_keys"]["removed"] == ["5/b.pdf"]
    assert artifacts.removed == [(DEFAULT_BUCKET, "5/b.pdf")]


# --- storage failure rule ----------------------------------------------------


def test_hard_storage_failure_keeps_row_and_stops(monkeypatch, tenant):
    fake = FakeArtifacts(fail_on="5/a.pdf")
    monkeypatch.setattr(dt, "get_artifact_store", lambda: fake)
    mocks = patch_store(
        monkeypatch, get_application_scoped=app_row(resume="5/a.pdf", cover="5/b.pdf")
    )
    res = run(dt.delete_application(1, dry_run=False))
    assert res["status"] == "error" and res["key"] == "5/a.pdf"
    assert "minio down" not in str(res)
    assert fake.removed == []  # nothing further removed after the failure
    mocks["delete_application_scoped"].assert_not_called()


def test_object_not_found_counts_as_removed(monkeypatch, tenant):
    fake = FakeArtifacts(missing={"5/a.pdf"})
    monkeypatch.setattr(dt, "get_artifact_store", lambda: fake)
    mocks = patch_store(monkeypatch, get_application_scoped=app_row(resume="5/a.pdf"))
    res = run(dt.delete_application(1, dry_run=False))
    assert res["status"] == "ok"
    assert res["pdf_keys"]["removed"] == ["5/a.pdf"]
    mocks["delete_application_scoped"].assert_awaited_once()


def test_dry_run_never_touches_artifact_store(monkeypatch, tenant):
    def boom():
        raise AssertionError("artifact store must not be opened on a dry run")

    monkeypatch.setattr(dt, "get_artifact_store", boom)
    patch_store(monkeypatch, get_application_scoped=app_row(resume="5/a.pdf"))
    assert run(dt.delete_application(1))["status"] == "ok"


# --- delete_applicant_profile ------------------------------------------------

PROFILE = {"id": 5, "resume_object_key": "5/base_resume.pdf", "full_name": "A"}


def test_profile_refuses_with_applications_without_force(monkeypatch, tenant, artifacts):
    mocks = patch_store(
        monkeypatch,
        get_profile_scoped=PROFILE,
        list_applications_for_profile_scoped=[app_row(1), app_row(2)],
    )
    for dry in (True, False):
        res = run(dt.delete_applicant_profile(5, dry_run=dry))
        assert res["status"] == "refused"
        assert res["application_ids"] == [1, 2]
        assert res["dry_run"] is dry
    mocks["delete_profile_scoped"].assert_not_called()
    assert artifacts.removed == []


def test_profile_dry_run_force_reports_counts_only(monkeypatch, tenant, artifacts):
    mocks = patch_store(
        monkeypatch,
        get_profile_scoped=PROFILE,
        list_applications_for_profile_scoped=[
            app_row(1, resume="5/j1/resume.pdf"),
            app_row(2, cover="5/j2/cover_letter.pdf"),
        ],
        count_profile_embeddings_scoped=4,
    )
    res = run(dt.delete_applicant_profile(5, force=True))
    assert res["dry_run"] is True and res["status"] == "ok"
    assert res["applications_deleted"] == 2 and res["embeddings_deleted"] == 4
    assert res["pdf_keys"]["removed"] == [
        "5/base_resume.pdf", "5/j1/resume.pdf", "5/j2/cover_letter.pdf",
    ]
    mocks["delete_profile_scoped"].assert_not_called()
    assert artifacts.removed == []
    mocks["get_profile_scoped"].assert_awaited_once_with(5, tenant_id=TENANT)


def test_profile_force_real_run_cascade_counts_and_order(monkeypatch, tenant):
    order = []

    class Fake(FakeArtifacts):
        async def remove_bytes(self, bucket, key):
            order.append(("pdf", key))
            await super().remove_bytes(bucket, key)

    fake = Fake()
    monkeypatch.setattr(dt, "get_artifact_store", lambda: fake)
    mocks = patch_store(
        monkeypatch,
        get_profile_scoped=PROFILE,
        list_applications_for_profile_scoped=[app_row(1, resume="5/j1/resume.pdf")],
        count_profile_embeddings_scoped=3,
        delete_profile_scoped={"deleted": True, "applications_deleted": 1, "embeddings_deleted": 3},
    )

    async def delete_row(*a, **k):
        order.append(("row", None))
        return {"deleted": True, "applications_deleted": 1, "embeddings_deleted": 3}

    mocks["delete_profile_scoped"].side_effect = delete_row
    res = run(dt.delete_applicant_profile(5, force=True, dry_run=False))
    assert res["status"] == "ok" and res["dry_run"] is False
    assert res["applications_deleted"] == 1 and res["embeddings_deleted"] == 3
    assert order[-1] == ("row", None)  # PDFs first, row last
    assert {k for t, k in order if t == "pdf"} == {"5/base_resume.pdf", "5/j1/resume.pdf"}
    mocks["delete_profile_scoped"].assert_awaited_once_with(5, TENANT)
    _, kwargs = mocks["find_shared_object_keys"].call_args
    assert kwargs["exclude_profile_id"] == 5 and kwargs["exclude_application_ids"] == [1]


def test_profile_without_applications_needs_no_force(monkeypatch, tenant, artifacts):
    mocks = patch_store(monkeypatch, get_profile_scoped=PROFILE)
    res = run(dt.delete_applicant_profile(5, dry_run=False))
    assert res["status"] == "ok"
    mocks["delete_profile_scoped"].assert_awaited_once_with(5, TENANT)


def test_profile_pdf_failure_keeps_row(monkeypatch, tenant):
    fake = FakeArtifacts(fail_on="5/base_resume.pdf")
    monkeypatch.setattr(dt, "get_artifact_store", lambda: fake)
    mocks = patch_store(monkeypatch, get_profile_scoped=PROFILE)
    res = run(dt.delete_applicant_profile(5, dry_run=False))
    assert res["status"] == "error" and res["key"] == "5/base_resume.pdf"
    mocks["delete_profile_scoped"].assert_not_called()


def test_profile_foreign_prefix_keys_skipped(monkeypatch, tenant, artifacts):
    patch_store(
        monkeypatch,
        get_profile_scoped={"id": 5, "resume_object_key": "9/base_resume.pdf"},
        list_applications_for_profile_scoped=[app_row(1, resume="9/j/resume.pdf")],
    )
    res = run(dt.delete_applicant_profile(5, force=True, dry_run=False))
    assert sorted(res["pdf_keys"]["skipped_invalid"]) == ["9/base_resume.pdf", "9/j/resume.pdf"]
    assert artifacts.removed == []


def test_profile_not_found(monkeypatch, tenant, artifacts):
    mocks = patch_store(monkeypatch)
    res = run(dt.delete_applicant_profile(5, force=True, dry_run=False))
    assert res["status"] == "not_found"
    mocks["delete_profile_scoped"].assert_not_called()


# --- delete_job_liveness -----------------------------------------------------

ROW = {"id": 11, "url": "https://x.test/1", "company": "Acme", "role_title": "Eng"}


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"url": ""}, {"url": "https://x.test/1", "url_hash": "ab" * 32}],
)
def test_liveness_requires_exactly_one_selector(monkeypatch, tenant, kwargs):
    mocks = patch_store(monkeypatch)
    res = run(dt.delete_job_liveness(**kwargs, dry_run=False))
    assert res["status"] == "error"
    mocks["get_liveness_by_hash"].assert_not_called()
    mocks["delete_liveness_scoped"].assert_not_called()


def test_liveness_url_resolves_via_store_hash(monkeypatch, tenant):
    mocks = patch_store(monkeypatch, get_liveness_by_hash=ROW)
    expected = store._hash_url("https://x.test/1")
    res = run(dt.delete_job_liveness(url="https://x.test/1"))
    assert res["status"] == "ok" and res["dry_run"] is True
    assert res["url_hash"] == expected and res["deleted_ids"] == [11]
    mocks["get_liveness_by_hash"].assert_awaited_once_with(expected, TENANT)
    mocks["delete_liveness_scoped"].assert_not_called()


def test_liveness_url_hash_real_run(monkeypatch, tenant):
    mocks = patch_store(monkeypatch, get_liveness_by_hash=ROW)
    digest = store._hash_url("https://x.test/1")
    res = run(dt.delete_job_liveness(url_hash=digest.upper(), dry_run=False))
    assert res["status"] == "ok" and res["dry_run"] is False
    mocks["delete_liveness_scoped"].assert_awaited_once_with(digest, TENANT)


def test_liveness_foreign_or_null_tenant_row_is_not_found(monkeypatch, tenant):
    # the scoped lookup returns None for other-tenant / NULL-tenant rows
    mocks = patch_store(monkeypatch, get_liveness_by_hash=None)
    res = run(dt.delete_job_liveness(url="https://x.test/1", dry_run=False))
    assert res["status"] == "not_found"
    mocks["delete_liveness_scoped"].assert_not_called()


# --- list_applications_for_portfolio_job -------------------------------------


def test_list_applications_for_portfolio_job_shape_and_tenant(monkeypatch, tenant):
    rows = [app_row(1), app_row(2, profile_id=6)]
    mocks = patch_store(monkeypatch, list_applications_for_portfolio_job_scoped=rows)
    res = run(dt.list_applications_for_portfolio_job("abc123"))
    assert res == {
        "applications": [
            {"id": 1, "applicant_profile_id": 5, "job_id": "job-1", "status": "drafted"},
            {"id": 2, "applicant_profile_id": 6, "job_id": "job-1", "status": "drafted"},
        ]
    }
    mocks["list_applications_for_portfolio_job_scoped"].assert_awaited_once_with("abc123", TENANT)


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_list_applications_for_portfolio_job_empty_arg(monkeypatch, tenant, empty):
    mocks = patch_store(monkeypatch)
    assert run(dt.list_applications_for_portfolio_job(empty))["status"] == "error"
    mocks["list_applications_for_portfolio_job_scoped"].assert_not_called()


# --- tool registration / tags -------------------------------------------------


def _declared_tags() -> dict[str, set[str]]:
    """Map tool function name -> tags from the @mcp.tool decorators in delete_tools.py."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(dt))
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call):
                for kw in deco.keywords:
                    if kw.arg == "tags":
                        found[node.name] = {e.value for e in kw.value.elts}
    return found


@pytest.mark.parametrize(
    "name,tag",
    [
        ("delete_application", "delete"),
        ("delete_applicant_profile", "delete"),
        ("delete_job_liveness", "delete"),
        ("list_applications_for_portfolio_job", "read"),
    ],
)
def test_tools_are_tagged(name, tag):
    tags = _declared_tags()[name]
    assert tags == {"job_search_plugin", tag}


def test_delete_tools_default_to_dry_run():
    import inspect

    for fn in (dt.delete_application, dt.delete_applicant_profile, dt.delete_job_liveness):
        target = getattr(fn, "fn", fn)
        assert inspect.signature(target).parameters["dry_run"].default is True
        assert "tenant_id" not in inspect.signature(target).parameters


# --- store statements ----------------------------------------------------------


class CaptureSession:
    def __init__(self, scalar=None, rows_per_call=None):
        self.statements = []
        self._scalar = scalar
        self._rows_per_call = list(rows_per_call or [])

    async def execute(self, stmt):
        self.statements.append(stmt)
        scalar = self._scalar
        rows = self._rows_per_call.pop(0) if self._rows_per_call else []
        return SimpleNamespace(
            rowcount=0,
            scalar_one_or_none=lambda: scalar,
            scalar_one=lambda: 0,
            scalars=lambda: SimpleNamespace(all=lambda: []),
            all=lambda: rows,
        )

    async def commit(self):
        pass


def capture(monkeypatch, session):
    @asynccontextmanager
    async def ctx():
        yield session

    monkeypatch.setattr(store, "get_async_session", ctx)


def sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


@pytest.mark.parametrize(
    "call",
    [
        lambda: store.get_application_scoped(1, TENANT),
        lambda: store.list_applications_for_profile_scoped(1, TENANT),
        lambda: store.list_applications_for_portfolio_job_scoped("x", TENANT),
        lambda: store.count_profile_embeddings_scoped(1, TENANT),
        lambda: store.find_shared_object_keys(["5/a.pdf"], TENANT),
        lambda: store.delete_application_scoped(1, TENANT),
        lambda: store.get_liveness_by_hash("ab" * 32, TENANT),
        lambda: store.delete_liveness_scoped("ab" * 32, TENANT),
    ],
)
def test_store_statements_filter_tenant(monkeypatch, call):
    session = CaptureSession()
    capture(monkeypatch, session)
    run(call())
    assert session.statements
    for stmt in session.statements:
        compiled = stmt.compile(dialect=postgresql.dialect())
        assert "tenant_id" in str(compiled)
        assert TENANT in compiled.params.values()


def test_delete_profile_scoped_filters_tenant_and_misses_foreign_row(monkeypatch):
    session = CaptureSession(scalar=None)  # ownership lookup finds nothing
    capture(monkeypatch, session)
    out = run(store.delete_profile_scoped(5, TENANT))
    assert out == {"deleted": False, "applications_deleted": 0, "embeddings_deleted": 0}
    assert len(session.statements) == 1  # no delete issued for a row it does not own
    assert "tenant_id" in sql(session.statements[0])


def test_delete_profile_scoped_delete_statement_has_tenant(monkeypatch):
    session = CaptureSession(scalar=5)
    capture(monkeypatch, session)
    run(store.delete_profile_scoped(5, TENANT))
    delete_stmt = session.statements[-1]
    text = sql(delete_stmt)
    assert text.startswith("DELETE FROM job_applicant_profiles") and "tenant_id" in text


def test_null_tenant_rows_never_match():
    """`tenant_id = <int>` is never true for NULL, so scoped lookups skip unowned rows."""
    from plugins.job_search_plugin.models import JobApplication

    clause = JobApplication.tenant_id == TENANT
    assert "IS NULL" not in str(clause.compile(dialect=postgresql.dialect())).upper()
    assert (JobApplication.tenant_id == None).compile(dialect=postgresql.dialect())  # noqa: E711


def test_shared_key_lookup_excludes_doomed_rows_and_scopes_tenant(monkeypatch):
    session = CaptureSession(rows_per_call=[[("5/a.pdf", None)], []])
    capture(monkeypatch, session)
    shared = run(
        store.find_shared_object_keys(
            ["5/a.pdf", "5/b.pdf"], TENANT, exclude_application_ids=[1], exclude_profile_id=5
        )
    )
    assert shared == {"5/a.pdf"}
    app_sql, prof_sql = (sql(s) for s in session.statements)
    assert "job_applications.id NOT IN" in app_sql
    assert "job_applications.applicant_profile_id !=" in app_sql
    assert "job_applicant_profiles.id !=" in prof_sql
    assert "tenant_id" in app_sql and "tenant_id" in prof_sql


def test_shared_key_lookup_with_no_keys_skips_db(monkeypatch):
    def no_session():
        raise AssertionError("no query expected")

    monkeypatch.setattr(store, "get_async_session", no_session)
    assert run(store.find_shared_object_keys([], TENANT)) == set()
    assert run(store.find_shared_object_keys([""], TENANT)) == set()
