"""Tenant-isolation guard for world_semantic_plugin stores and adapters.

Static half (AST): every public ``stores/*.py`` function either takes a required,
non-Optional ``tenant_id`` (or an ``AuthorizedWorld``/``WorldDocument`` capability that
carries it) or is listed in ``TENANT_EXEMPT`` with a reason. Behavioral half: with the
SQL session doubled (fixtures from ``test_semantic_v2_e2e``), foreign / unowned / absent
worlds produce one identical not-found outcome with nothing run afterwards, and a
tenantless principal is rejected before any store call.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from plugins.world_semantic_plugin import asset_adapters, world_adapters
from plugins.world_semantic_plugin.stores import world_store
from plugins.world_semantic_plugin.tests.test_semantic_v2_e2e import (  # noqa: F401  (sdb is a fixture)
    ABSENT_WORLD, FOREIGN_WORLD, OPERATIONS, OTHER_TENANT, READ, TENANT, UNOWNED_WORLD, WORLD, WRITE,
    act_as, assert_untouched, body_of, downstream_spies, envelope, http, principal, sdb,
)

STORES_DIR = Path(world_adapters.__file__).parent / "stores"
# Parameter annotations whose value carries the authorized tenant (built only by _authorize_world).
TENANT_CARRIERS = frozenset({"AuthorizedWorld", "WorldDocument"})

# function -> why it legitimately has no tenant_id parameter. Key is "<module>.<function>".
TENANT_EXEMPT: dict[str, str] = {
    "asset_store.consume_asset_job": "worker-internal: tenant comes from the queued job payload staged by stage_assets",
    "asset_store.register_consumer": "worker registration, touches no data",
    "asset_store.unregister_consumer": "worker registration, touches no data",
    "asset_store.finish_v3_job": "worker-internal completion keyed by the staged job's own tenant identity",
    "embed_job_store.build_object_embed_text": "pure text builder, no I/O",
    "embed_job_store.build_hex_embed_text": "pure text builder, no I/O",
    "embed_job_store.build_hex_layer_embed_text": "pure text builder, no I/O",
    "embed_job_store.enqueue_unity_world_embed_jobs": "legacy public spatial pipeline keyed by world_id; not reachable from semantic adapters",
    "job_store.enqueue_index_job": "legacy public spatial index queue keyed by world_id",
    "job_store.claim_index_jobs": "worker-internal claim over the legacy spatial queue, tenant-agnostic by design",
    "job_store.mark_job_done": "worker-internal completion by job id",
    "job_store.mark_job_failed": "worker-internal completion by job id",
    "job_store.get_job": "legacy public spatial job status by job id",
    "job_store.list_jobs": "legacy public spatial job listing keyed by world_id",
    "job_store.queue_stats": "legacy public spatial queue stats keyed by world_id",
    "unity_world_vectors_store.resolve_unity_world_embedding": "reads embedding configuration only",
    "unity_world_vectors_store.identity_content_hash": "pure hash, no I/O",
    "unity_world_vectors_store.delete_world_vectors": "legacy spatial cleanup keyed by namespace/world_id string",
    "unity_world_vectors_store.delete_doc": "legacy spatial cleanup keyed by namespace/world_id string",
    "unity_world_vectors_store.search_unity_world_vectors": "legacy spatial search keyed by namespace/world_id string",
    "unity_world_vectors_store.hex_similarity_scores": "legacy spatial scoring keyed by namespace/world_id string",
    "unity_world_vectors_store.coerce_embedding": "pure vector normalization, no I/O",
    "unity_world_vectors_store.build_upsert_stmt": "statement builder keyed by namespace; semantic callers pass the tenant-bound namespace",
    "world_hex_store.validate_selection": "pure embedding-selection validation, no I/O",
    "world_hex_store.build_completion_stmt": "worker-internal; payload was validated against its tenant-bound namespace",
    "world_hex_store.process_world_hex_jobs": "worker-internal; rebuilds AuthorizedWorld from the queued payload",
    "world_store.ensure_world": "legacy public spatial ingest keyed by world_id",
    "world_store.get_world": "legacy public spatial read keyed by world_id; semantic adapters must use get_world_for_tenant",
    "world_store.full_index": "legacy public spatial ingest keyed by world_id",
    "world_store.apply_diff": "legacy public spatial ingest keyed by world_id",
    "world_store.list_objects_in_hex": "legacy public spatial query keyed by world_id",
    "world_store.get_hex": "legacy public spatial query keyed by world_id",
    "world_store.query_objects_near": "legacy public spatial query keyed by world_id",
    "world_store.describe_region": "legacy public spatial query keyed by world_id",
}
# Legacy world_id-only reads/writes the semantic adapters must never reach for.
LEGACY_UNSCOPED_WORLD_STORE = frozenset({
    "ensure_world", "get_world", "full_index", "apply_diff", "list_objects_in_hex",
    "get_hex", "query_objects_near", "describe_region",
})


# ── Static half ──────────────────────────────────────────────────────────

def _public_functions(source: str):
    tree = ast.parse(source)
    return [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")]


def _annotation_names(node: ast.expr | None) -> set[str]:
    """Every Name/Attribute/None identifier in an annotation (string annotations are parsed)."""
    if node is None:
        return set()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _annotation_names(ast.parse(node.value, mode="eval").body)
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
        elif isinstance(sub, ast.Constant) and sub.value is None:
            names.add("None")
    return names


def _params(fn) -> dict[str, tuple[ast.arg, ast.expr | None]]:
    """name -> (arg, default node or None) for positional and keyword-only params."""
    a = fn.args
    pos = a.posonlyargs + a.args
    out = {arg.arg: (arg, None) for arg in pos}
    for arg, default in zip(pos[len(pos) - len(a.defaults):], a.defaults):
        out[arg.arg] = (arg, default)
    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        out[arg.arg] = (arg, default)
    return out


def _carries_tenant(fn) -> bool:
    return any(_annotation_names(arg.annotation) & TENANT_CARRIERS for arg, _ in _params(fn).values())


def tenant_violations(module: str, source: str, exempt: dict[str, str]) -> list[str]:
    """Violations for one stores module: bad ``tenant_id`` params and undeclared tenant-less functions."""
    problems = []
    for fn in _public_functions(source):
        key = f"{module}.{fn.name}"
        params = _params(fn)
        if "tenant_id" in params:
            arg, default = params["tenant_id"]
            if default is not None and isinstance(default, ast.Constant) and default.value is None:
                problems.append(f"{key}: tenant_id defaults to None")
            if "None" in _annotation_names(arg.annotation) or "Optional" in _annotation_names(arg.annotation):
                problems.append(f"{key}: tenant_id is annotated Optional")
            if key in exempt:
                problems.append(f"{key}: exempt entry is stale, function takes tenant_id")
        elif _carries_tenant(fn):
            if key in exempt:
                problems.append(f"{key}: exempt entry is stale, function takes a tenant capability")
        elif key not in exempt:
            problems.append(f"{key}: no tenant_id parameter and not listed in TENANT_EXEMPT")
    return problems


def _store_modules():
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(STORES_DIR.glob("*.py")) if p.stem != "__init__"}


def test_every_store_function_is_tenant_scoped_or_explicitly_exempt():
    problems = []
    for module, source in _store_modules().items():
        problems += tenant_violations(module, source, TENANT_EXEMPT)
    assert not problems, "\n".join(problems)


def test_exempt_entries_are_not_stale_and_have_reasons():
    existing = {f"{m}.{fn.name}" for m, src in _store_modules().items() for fn in _public_functions(src)}
    stale = sorted(set(TENANT_EXEMPT) - existing)
    assert not stale, f"TENANT_EXEMPT names functions that no longer exist: {stale}"
    assert all(reason.strip() for reason in TENANT_EXEMPT.values())


def test_guard_detects_optional_default_none_and_missing_tenant():
    bad = (
        "def a(world_id, tenant_id=None): ...\n"
        "async def b(world_id, *, tenant_id: int | None): ...\n"
        "def c(world_id, tenant_id: Optional[int] = 1): ...\n"
        "def d(world_id): ...\n"
        "def e(world_id, tenant_id: 'int | None' = 3): ...\n"
        "def _private(world_id): ...\n"
        "def ok(world_id, *, tenant_id: int): ...\n"
        "def ok_capability(context: AuthorizedWorld, query): ...\n"
    )
    found = tenant_violations("m", bad, {})
    assert sorted(v.split(":")[0] for v in found) == ["m.a", "m.b", "m.c", "m.d", "m.e"]
    stale = tenant_violations("m", "def ok(tenant_id: int): ...", {"m.ok": "x"})
    assert stale == ["m.ok: exempt entry is stale, function takes tenant_id"]


def test_semantic_adapters_never_use_unscoped_legacy_world_store_functions():
    """world_id-only store reads would bypass ownership; adapters must go through get_world_for_tenant."""
    for module in (world_adapters, asset_adapters):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        used |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert not used & LEGACY_UNSCOPED_WORLD_STORE, f"{module.__name__} reaches an unscoped store function"
    tree = ast.parse(Path(world_adapters.__file__).read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "get_world_for_tenant"]
    assert calls and all(len(c.args) + len(c.keywords) == 2 for c in calls)


# ── Behavioral half ──────────────────────────────────────────────────────

WORLD_OPS = [op for op in OPERATIONS if op[0].endswith("world")]


@pytest.mark.asyncio
async def test_foreign_unowned_and_absent_worlds_give_one_identical_not_found(sdb, monkeypatch):
    act_as(monkeypatch, principal(scopes=(READ, WRITE)))
    spies = downstream_spies(monkeypatch)
    before, provider_calls, statements = sdb.snapshot(), len(sdb.provider.calls), len(sdb.statements)
    for name, handler, path, body, call in WORLD_OPS:
        http_outcomes, mcp_outcomes = set(), set()
        for world in (FOREIGN_WORLD, UNOWNED_WORLD, ABSENT_WORLD):
            response = await handler(http(path, dict(body, world_id=world) if "inspect" in body else body, world))
            http_outcomes.add((response.status_code, repr(body_of(response)), response.media_type))
            with pytest.raises(LookupError) as raised:
                await call(world)
            mcp_outcomes.add((type(raised.value), raised.value.args, raised.value.__cause__))
        assert len(http_outcomes) == 1, f"{name}: HTTP outcome leaks world state: {http_outcomes}"
        assert next(iter(http_outcomes))[:2] == (404, repr({"status": "error", "error": "world_not_found"}))
        assert len(mcp_outcomes) == 1, f"{name}: MCP outcome leaks world state: {mcp_outcomes}"
        assert next(iter(mcp_outcomes)) == (world_adapters.WorldNotFound, ("world not found",), None)
    assert sdb.search_calls == []
    assert_untouched(spies, sdb, before, provider_calls, statements)
    # Lookups only ever ran as the caller's tenant, never as a wildcard.
    assert sdb.lookups and {t for _, t in sdb.lookups} == {TENANT}
    # Denial is per tenant: the real owner still resolves its own world.
    act_as(monkeypatch, principal(tenant=OTHER_TENANT))
    context = await world_adapters._authorize_world(await world_adapters.principal_for_tool(), FOREIGN_WORLD, "search_world", "read")
    assert (context.tenant_id, context.world_id) == (OTHER_TENANT, FOREIGN_WORLD)


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant", [None, 0, -1, True, "7", 7.0])
async def test_unusable_principal_tenant_is_rejected_before_any_store_call(sdb, monkeypatch, tenant):
    act_as(monkeypatch, principal(tenant=tenant))
    spies = downstream_spies(monkeypatch)
    before, provider_calls, statements = sdb.snapshot(), len(sdb.provider.calls), len(sdb.statements)
    for name, handler, path, body, call in OPERATIONS:
        world = WORLD if name.endswith("world") else "library"
        assert (await handler(http(path, body, world))).status_code == 403, name
        with pytest.raises(PermissionError, match="explicit tenant"):
            await call(world)
    assert sdb.lookups == [] and sdb.search_calls == []
    assert_untouched(spies, sdb, before, provider_calls, statements)


@pytest.mark.asyncio
async def test_world_store_refuses_unscoped_tenant_without_touching_sql(monkeypatch):
    def no_session(*_a, **_k):
        raise AssertionError("SQL session opened for an unusable tenant")

    monkeypatch.setattr(world_store, "get_async_session", no_session)
    for bad in (None, 0, -1, True, "7"):
        assert await world_store.get_world_for_tenant(WORLD, bad) is None
        with pytest.raises(ValueError, match="tenant_id"):
            await world_store.assign_world_tenant(WORLD, bad)
    # tenant_id has no default: omitting it is a TypeError, never an unscoped query.
    with pytest.raises(TypeError):
        await world_store.get_world_for_tenant(WORLD)  # type: ignore[call-arg]
    sig = inspect.signature(world_store.get_world_for_tenant)
    assert sig.parameters["tenant_id"].default is inspect.Parameter.empty
