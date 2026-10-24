"""Re-index logic: in-memory ReindexStore, fake embedder, no DB, no network."""

import asyncio

import pytest

from db_layer.embeddings.reindex import (
    ReindexError,
    StaleRow,
    plan_reindex,
    run_reindex,
)

TARGET_SEL = {"provider": "gemma-multimodal", "model": "embeddinggemma-2", "dimensions": 768}
TARGET = "gemma-multimodal:embeddinggemma-2:768"
OLD = "openai:text-embedding-qwen3:1024"


class FakeStore:
    """Rows are dicts {id, tenant_id, model, text, vec}; one table per collection."""

    def __init__(self, tables, width=768, tenants=(1, 2)):
        self.tables = tables
        self.width = width
        self.tenants = list(tenants)
        self.writes = []  # (collection, tenant_id, [ids])
        self.fail_update_for = set()

    async def list_tenants(self):
        return self.tenants

    async def vector_width(self, collection):
        return self.width

    def _rows(self, collection, tenant_id):
        return [r for r in self.tables[collection] if r["tenant_id"] == tenant_id]

    async def count_rows(self, collection, tenant_id, target_identity):
        rows = self._rows(collection, tenant_id)
        has = lambda r: bool((r["text"] or "").strip())
        return {
            "total": len(rows),
            "already_target": sum(r["model"] == target_identity for r in rows),
            "stale": sum(r["model"] != target_identity and has(r) for r in rows),
            "no_text": sum(r["model"] != target_identity and not has(r) for r in rows),
            "superseded": 0,
        }

    async def fetch_stale(self, collection, tenant_id, target_identity, after_id, limit):
        rows = [
            r for r in self._rows(collection, tenant_id)
            if r["model"] != target_identity and (r["text"] or "").strip()
            and (after_id is None or r["id"] > after_id)
        ]
        rows.sort(key=lambda r: r["id"])
        return [StaleRow(r["id"], r["text"]) for r in rows[:limit]]

    async def update_batch(self, collection, tenant_id, target_identity, items):
        ids = [i for i, _ in items]
        if self.fail_update_for & set(ids):
            raise RuntimeError("boom with secret content")
        by_id = {r["id"]: r for r in self._rows(collection, tenant_id)}
        for i, vec in items:
            by_id[i]["model"], by_id[i]["vec"] = target_identity, vec
        self.writes.append((collection, tenant_id, ids))
        return len(items)


class FakeEmbed:
    def __init__(self, dims=768, delay=0.0, fail_on_call=None, interrupt_on_call=None):
        self.dims, self.delay = dims, delay
        self.fail_on_call, self.interrupt_on_call = fail_on_call, interrupt_on_call
        self.calls, self.batch_sizes = 0, []
        self.in_flight = self.max_in_flight = 0

    async def __call__(self, sel, texts):
        self.calls += 1
        n = self.calls
        self.batch_sizes.append(len(texts))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            if n == self.fail_on_call:
                raise RuntimeError("provider down")
            if n == self.interrupt_on_call:
                raise asyncio.CancelledError()
            return [[0.5] * self.dims for _ in texts]
        finally:
            self.in_flight -= 1


def rows(n, tenant_id=1, start=1, model=OLD, text="hello"):
    return [
        {"id": start + i, "tenant_id": tenant_id, "model": model, "text": f"{text} {start + i}", "vec": None}
        for i in range(n)
    ]


def run(coro):
    return asyncio.run(coro)


def test_dry_run_makes_no_embed_calls_or_writes():
    store = FakeStore({"memory": rows(5), "search": rows(3, start=100)})
    embed = FakeEmbed()
    report = run(run_reindex(store, TARGET_SEL, 1, embed_fn=embed))  # dry_run defaults True
    assert report.dry_run and embed.calls == 0 and store.writes == []
    assert report.results == [] and report.ok
    assert all(r["model"] == OLD for r in store.tables["memory"])


def test_plan_counts_per_collection():
    mem = rows(3) + rows(2, start=10, model=TARGET) + rows(1, start=20)
    mem[-1]["text"] = "  "
    store = FakeStore({"memory": mem, "search": rows(4, start=100)})
    plans = {p.collection: p for p in run(plan_reindex(store, TARGET, 1))}
    m = plans["memory"]
    assert (m.total, m.already_target, m.stale, m.no_text) == (6, 2, 3, 1)
    assert m.note == "skipped: no source text"
    s = plans["search"]
    assert (s.total, s.already_target, s.stale, s.no_text) == (4, 0, 4, 0)


def test_collection_filter_and_unknown_collection():
    store = FakeStore({"memory": rows(2), "search": rows(2, start=50)})
    plans = run(plan_reindex(store, TARGET, 1, ["search"]))
    assert [p.collection for p in plans] == ["search"]
    with pytest.raises(ValueError):
        run(plan_reindex(store, TARGET, 1, ["route_embeddings"]))


def test_execute_rewrites_vector_and_identity_and_respects_batch_size():
    store = FakeStore({"memory": rows(10), "search": []})
    embed = FakeEmbed()
    report = run(run_reindex(store, TARGET_SEL, 1, dry_run=False, batch_size=4, concurrency=1, embed_fn=embed))
    assert embed.batch_sizes == [4, 4, 2]
    assert max(embed.batch_sizes) <= 4
    assert all(r["model"] == TARGET and len(r["vec"]) == 768 for r in store.tables["memory"])
    assert report.ok and report.results[0].updated == 10 and report.results[0].cursor == 10


def test_resumable_after_interrupt_does_not_reembed_done_rows():
    store = FakeStore({"memory": rows(10), "search": []})
    first = FakeEmbed(interrupt_on_call=3)
    with pytest.raises(asyncio.CancelledError):
        run(run_reindex(store, TARGET_SEL, 1, dry_run=False, batch_size=2, concurrency=1, embed_fn=first))
    done = [r["id"] for r in store.tables["memory"] if r["model"] == TARGET]
    assert done == [1, 2, 3, 4]  # batches 1 and 2 landed

    second = FakeEmbed()
    report = run(run_reindex(store, TARGET_SEL, 1, dry_run=False, batch_size=2, concurrency=1, embed_fn=second))
    assert second.calls == 3 and sum(second.batch_sizes) == 6  # only the 6 remaining rows
    assert all(r["model"] == TARGET for r in store.tables["memory"]) and report.ok


def test_limit_returns_cursor_and_resume_after_continues():
    store = FakeStore({"memory": rows(10), "search": []})
    r1 = run(run_reindex(store, TARGET_SEL, 1, dry_run=False, batch_size=2, concurrency=1,
                         limit=4, embed_fn=FakeEmbed()))
    cursor = r1.results[0].cursor
    assert cursor == 4 and r1.results[0].updated == 4
    embed = FakeEmbed()
    r2 = run(run_reindex(store, TARGET_SEL, 1, dry_run=False, batch_size=2, concurrency=1,
                         resume_after=cursor, embed_fn=embed))
    assert r2.results[0].updated == 6 and sum(embed.batch_sizes) == 6


def test_in_flight_batches_never_exceed_semaphore_bound():
    store = FakeStore({"memory": rows(40), "search": []})
    embed = FakeEmbed(delay=0.01)
    run(run_reindex(store, TARGET_SEL, 1, dry_run=False, batch_size=2, concurrency=3, embed_fn=embed))
    assert 1 < embed.max_in_flight <= 3
    assert all(r["model"] == TARGET for r in store.tables["memory"])


def test_concurrency_bounds_are_validated():
    store = FakeStore({"memory": rows(1), "search": []})
    for bad in (0, 9):
        with pytest.raises(ValueError):
            run(run_reindex(store, TARGET_SEL, 1, dry_run=False, concurrency=bad, embed_fn=FakeEmbed()))


def test_tenant_scoping_leaves_other_tenant_untouched():
    store = FakeStore({"memory": rows(4, tenant_id=1) + rows(4, tenant_id=2, start=50), "search": []})
    run(run_reindex(store, TARGET_SEL, 1, dry_run=False, embed_fn=FakeEmbed()))
    assert all(r["model"] == TARGET for r in store.tables["memory"] if r["tenant_id"] == 1)
    assert all(r["model"] == OLD and r["vec"] is None for r in store.tables["memory"] if r["tenant_id"] == 2)
    assert {t for _, t, _ in store.writes} == {1}


@pytest.mark.parametrize("bad", [None, True, "1"])
def test_missing_tenant_raises(bad):
    store = FakeStore({"memory": rows(2), "search": []})
    embed = FakeEmbed()
    with pytest.raises(ValueError):
        run(run_reindex(store, TARGET_SEL, bad, dry_run=False, embed_fn=embed))
    with pytest.raises(ValueError):
        run(plan_reindex(store, TARGET, bad))
    assert embed.calls == 0 and store.writes == []


def test_rows_without_source_text_are_reported_skipped_never_guessed():
    mem = rows(3) + rows(2, start=10)
    for r in mem[3:]:
        r["text"] = ""
    store = FakeStore({"memory": mem, "search": []})
    embed = FakeEmbed()
    report = run(run_reindex(store, TARGET_SEL, 1, dry_run=False, embed_fn=embed))
    assert sum(embed.batch_sizes) == 3
    assert report.results[0].skipped == "2 row(s) skipped: no source text"
    assert all(r["model"] == OLD and r["vec"] is None for r in mem[3:])


def test_dimension_mismatch_refuses_before_any_write():
    store = FakeStore({"memory": rows(4), "search": rows(4, start=50)}, width=1024)
    embed = FakeEmbed()
    with pytest.raises(ReindexError, match="dimension guard"):
        run(run_reindex(store, TARGET_SEL, 1, dry_run=False, embed_fn=embed))
    assert embed.calls == 0 and store.writes == []
    # dry-run still reports it instead of raising
    report = run(run_reindex(store, TARGET_SEL, 1))
    assert not report.plan[0].dimension_ok and "refused" in report.plan[0].note


def test_wrong_width_from_provider_fails_batch_without_write():
    store = FakeStore({"memory": rows(4), "search": []})
    report = run(run_reindex(store, TARGET_SEL, 1, dry_run=False, embed_fn=FakeEmbed(dims=512)))
    assert not report.ok and store.writes == []
    assert all(r["model"] == OLD for r in store.tables["memory"])


def test_failed_batch_is_recorded_and_other_batches_complete():
    store = FakeStore({"memory": rows(6), "search": []})
    store.fail_update_for = {3}  # batch 2 (ids 3,4) fails at the write
    report = run(run_reindex(store, TARGET_SEL, 1, dry_run=False, batch_size=2, concurrency=1,
                             embed_fn=FakeEmbed()))
    assert not report.ok and report.failed_batches == 1
    failed = report.results[0].failed[0]
    assert (failed.first_id, failed.last_id, failed.rows, failed.error) == (3, 4, 2, "RuntimeError")
    assert "secret" not in str(report.as_dict())  # counts/ids/type only, never content
    done = sorted(r["id"] for r in store.tables["memory"] if r["model"] == TARGET)
    assert done == [1, 2, 5, 6]
    # rerun retries only the failed rows
    store.fail_update_for = set()
    embed = FakeEmbed()
    again = run(run_reindex(store, TARGET_SEL, 1, dry_run=False, batch_size=2, embed_fn=embed))
    assert again.ok and sum(embed.batch_sizes) == 2


def test_embed_failure_does_not_stop_run():
    store = FakeStore({"memory": rows(6), "search": []})
    report = run(run_reindex(store, TARGET_SEL, 1, dry_run=False, batch_size=2, concurrency=1,
                             embed_fn=FakeEmbed(fail_on_call=1)))
    assert report.failed_batches == 1 and report.results[0].updated == 4


def test_sql_store_requires_tenant():
    from db_layer.embedding_reindex_store import SqlReindexStore

    store = SqlReindexStore()
    with pytest.raises(ValueError):
        run(store.count_rows("memory", None, TARGET))
    with pytest.raises(ValueError):
        run(store.fetch_stale("search", None, TARGET, None, 5))
    with pytest.raises(ValueError):
        run(store.update_batch("search", None, TARGET, [(1, [0.1])]))
