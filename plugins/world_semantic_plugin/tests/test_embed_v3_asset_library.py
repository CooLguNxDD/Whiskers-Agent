"""WorldGen v3 asset library: pack roots, per-view vectors, max-sim search, local never mirrored.

Fake embedding client + in-memory store (service level), plus a real-store round trip on the
plugin's SQL double and SQL-compile checks for the filter/ordering contract. No DB, no network.
"""
import io
import logging
import os
import re
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Delete, Insert

from db_layer.embeddings.multimodal import DOCUMENT_PREFIX_NO_TITLE, QUERY_PREFIX
from plugins.world_semantic_plugin import asset_adapters, asset_packs, asset_v3
from plugins.world_semantic_plugin import asset_index as api
from plugins.world_semantic_plugin.stores import asset_store
from core_graph.worker import embedding_worker as worker

SEL = {"provider": "gemma-multimodal", "model": "synthetic", "dimensions": 3}
MODEL = "gemma-multimodal:synthetic:3"


@pytest.fixture(autouse=True)
def _reset_model_roles_state():
    """Stdlib-level tests: skip the repo-wide core_graph reset fixture."""
    yield


class Embedder:
    """Fake S01 client: vectors are looked up by image bytes / unprefixed text; every call is kept."""

    def __init__(self):
        self.inputs, self.by_bytes, self.by_text, self._color = [], {}, {}, 0

    def image(self, path, vector):
        self._color += 1
        buf = io.BytesIO()
        Image.new("RGB", (2, 2), (self._color % 256, self._color // 256, 7)).save(buf, format="PNG")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(buf.getvalue())
        self.by_bytes[buf.getvalue()] = vector

    async def __call__(self, sel, inputs):
        self.inputs.extend(inputs)
        out = []
        for item in inputs:
            if item.media is not None:
                out.append(self.by_bytes[item.media.data])
            else:
                bare = re.sub(r"^(title: [^|]*\| text: |task: search result \| query: )", "", item.text)
                out.append(self.by_text.get(bare, [0.0, 0.0, 1.0]))
        return out


class MemStore:
    """In-memory stand-in for stores/asset_store.py using the same pure decision/filter helpers."""

    def __init__(self):
        self.assets, self.vectors, self.jobs, self.finished = {}, {}, [], []

    async def stage_v3_assets(self, records, *, tenant_id, world_id, model_id, dimensions):
        enq = pend = idx = 0
        for r in sorted(records, key=lambda r: r["asset_id"]):
            key = (tenant_id, world_id, r["asset_id"])
            old = self.assets.get(key)
            active = old is not None and any(j["payload"]["asset_id"] == r["asset_id"]
                                             and j["payload"]["tenant_id"] == tenant_id
                                             and j["payload"]["generation"] == old["generation"]
                                             and not j.get("done") for j in self.jobs)
            verdict = asset_v3.decide_stage(old and old["content_hash"], old and old["status"],
                                            r["content_hash"], active)
            if verdict == "indexed":
                idx += 1
                continue
            if verdict == "pending":
                pend += 1
                continue
            meta = r["meta"]
            gen = f"g{len(self.jobs)}"
            self.assets[key] = {"content_hash": r["content_hash"], "status": "enqueued", "generation": gen,
                                "model": model_id, "meta": meta, "kind": meta["kind"],
                                "pack_id": meta["pack_id"], "distribution": meta["distribution"],
                                "tri_count": meta["tri_count"], "role": meta["role"],
                                "biome": meta["biome"], "licence": meta["licence"]}
            self.vectors.pop(key, None)
            self.jobs.append({"id": len(self.jobs) + 1, "content_hash": r["content_hash"],
                              "payload": {"schema": "v3", "tenant_id": tenant_id, "world_id": world_id,
                                          "asset_id": r["asset_id"], "generation": gen, "model": model_id,
                                          "dimensions": dimensions, "caption": r["caption"],
                                          "views": r["views"]}})
            enq += 1
        return {"status": "enqueued" if enq or pend else "indexed", "enqueued": enq, "pending": pend,
                "indexed": idx}

    async def finish_v3_job(self, job, rows, *, failed=False):
        p = job["payload"]
        key = (p["tenant_id"], p["world_id"], p["asset_id"])
        row = self.assets.get(key)
        job["done"] = True
        if row is None or row["generation"] != p["generation"]:
            return
        row["status"] = "failed" if failed else "indexed"
        if not failed:
            self.vectors[key] = [dict(r, model=p["model"]) for r in rows]
        self.finished.append((p["asset_id"], failed))

    async def search_asset_vectors(self, vector, *, tenant_id, world_id, model_id, k, filters, kind=None,
                                   tags=None):
        sims = {}
        for (t, w, a), rows in self.vectors.items():
            row = self.assets[(t, w, a)]
            if (t, w) != (tenant_id, world_id) or row["status"] != "indexed" or row["model"] != model_id:
                continue
            if (kind and row["kind"] != kind) or (tags and not set(tags) <= set(row["meta"]["tags"])):
                continue
            if not asset_v3.filter_matches(row, filters):
                continue
            usable = [asset_v3.cosine(vector, v["embedding"]) for v in rows if v["model"] == model_id]
            if usable:
                sims[a] = usable
        ranked = asset_v3.rank_assets(sims)[:k]
        return [dict(r, metadata=self.assets[(tenant_id, world_id, r["asset_id"])]["meta"]) for r in ranked]


class Lab:
    """Temp pack roots + fake embedder + in-memory store wired into the service layer."""

    def __init__(self, tmp_path, monkeypatch):
        self.packs, self.local = tmp_path / "packs", tmp_path / "packs-local"
        self.packs.mkdir()
        self.local.mkdir()
        self.roots = asset_packs.register_pack_roots({"packs": str(self.packs), "packs-local": str(self.local)})
        self.emb, self.store = Embedder(), MemStore()
        monkeypatch.setattr(api, "resolve_asset_embedding", self._resolve)
        monkeypatch.setattr(api.embeddings_core, "embed_multimodal_with", self.emb)
        self.sel = SEL

    async def _resolve(self):
        return self.sel

    def record(self, pack, rel, views, *, caption=None, caption_vec=None, root="packs", role="tree",
               biome="forest", licence="CC0", tris=1000, digest="h" * 8, distribution=None, free=()):
        base = (self.packs if root == "packs" else self.local) / pack
        paths = []
        for i, vec in enumerate(views):
            name = f"previews/{rel.replace('/', '_')}_{i}.png"
            self.emb.image(base / name, vec)
            paths.append(name)
        if caption:
            self.emb.by_text[caption] = caption_vec or [0.0, 0.0, 1.0]
        row = {"asset_id": f"{pack}/{rel}", "pack_id": pack, "content_hash": digest,
               "tags": {"role": role, "biome": biome, "licence": licence, "free": list(free)},
               "tri_count": tris, "views": paths}
        if caption:
            row["caption"] = caption
        if distribution:
            row["distribution"] = distribution
        return row

    async def ingest(self, records, *, root="packs", tenant=7, world="lib"):
        body = {"version": 3, "root": root, "assets": records}
        return await asset_v3.index_pack_assets(body, tenant_id=tenant, world_id=world,
                                                pack_roots=self.roots, store=self.store)

    async def drain(self):
        """Run every not-yet-finished job through the real consumer against the memory store."""
        for job in [j for j in self.store.jobs if not j.get("done")]:
            await asset_v3.consume_v3_job(job, store=self.store)

    async def search(self, query="q", *, tenant=7, world="lib", k=10, allow_local=False, **filters):
        self.emb.by_text.setdefault(query, [1.0, 0.0, 0.0])
        flt = asset_v3.normalize_filters(allow_local=allow_local, **filters)
        return await asset_v3.search_pack_assets(query, None, tenant_id=tenant, world_id=world, kind=None,
                                                 tags=[], k=k, filters=flt, store=self.store)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    return Lab(tmp_path, monkeypatch)


def ids(hits):
    return [h["asset_id"] for h in hits]


# ------------------------------------------------------------------ pack roots

def test_pack_roots_registered_with_distribution_defaults(lab, tmp_path):
    assert lab.roots["packs"].distribution == "shared"
    assert lab.roots["packs-local"].distribution == "local"
    with pytest.raises(ValueError):
        asset_packs.register_pack_roots({"elsewhere": str(tmp_path)})
    with pytest.raises(ValueError):
        asset_packs.register_pack_roots({"packs": str(tmp_path / "missing")})
    with pytest.raises(ValueError):
        asset_packs.register_pack_roots({})


@pytest.mark.asyncio
async def test_traversal_and_symlink_escape_are_rejected_per_record(lab, tmp_path):
    good = lab.record("forest", "props/oak.glb", [[1, 0, 0]])
    outside = tmp_path / "outside.png"
    lab.emb.image(outside, [1, 0, 0])
    escape = lab.record("forest", "props/escape.glb", [[1, 0, 0]])
    escape["views"] = ["previews/../../../outside.png"]
    absolute = lab.record("forest", "props/abs.glb", [[1, 0, 0]])
    absolute["views"] = [str(outside)]
    link = lab.record("forest", "props/link.glb", [])
    link["caption"] = "linked"
    link["views"] = ["previews/link.png"]
    try:
        os.symlink(outside, lab.packs / "forest" / "previews" / "link.png")
    except (OSError, NotImplementedError):
        link = None
    bad_id = lab.record("forest", "props/x.glb", [[1, 0, 0]])
    bad_id["asset_id"] = "forest/../x.glb"
    rows = [good, escape, absolute, bad_id] + ([link] if link else [])
    result = await lab.ingest(rows)
    assert result["enqueued"] == 1
    assert {e["asset_id"] for e in result["errors"]} == {r["asset_id"] for r in rows[1:]}
    assert [j["payload"]["asset_id"] for j in lab.store.jobs] == ["forest/props/oak.glb"]


@pytest.mark.asyncio
async def test_local_root_defaults_to_local_and_refuses_shared(lab):
    local = lab.record("secret", "props/a.glb", [[1, 0, 0]], root="packs-local")
    forged = lab.record("secret", "props/b.glb", [[1, 0, 0]], root="packs-local", distribution="shared")
    result = await lab.ingest([local, forged], root="packs-local")
    assert result["enqueued"] == 1 and len(result["errors"]) == 1
    assert lab.store.assets[(7, "lib", "secret/props/a.glb")]["distribution"] == "local"
    stricter = lab.record("forest", "props/c.glb", [[1, 0, 0]], distribution="local")
    await lab.ingest([stricter])
    assert lab.store.assets[(7, "lib", "forest/props/c.glb")]["distribution"] == "local"
    shared = lab.record("forest", "props/d.glb", [[1, 0, 0]])
    await lab.ingest([shared])
    assert lab.store.assets[(7, "lib", "forest/props/d.glb")]["distribution"] == "shared"


# ------------------------------------------------------------------ ingest

@pytest.mark.asyncio
async def test_record_ingest_noop_reembed_and_malformed_isolation(lab):
    oak = lab.record("forest", "props/oak.glb", [[1, 0, 0]], caption="an oak", digest="aaaaaaaa")
    pine = lab.record("forest", "props/pine.glb", [[0, 1, 0]], digest="bbbbbbbb")
    broken = lab.record("forest", "props/bad.glb", [[0, 1, 0]])
    broken["tri_count"] = -5
    notdict = "oops"
    result = await lab.ingest([oak, broken, pine, notdict])
    assert result["status"] == "enqueued" and result["enqueued"] == 2
    assert [e["index"] for e in result["errors"]] == [1, 3]
    assert set(lab.store.assets) == {(7, "lib", "forest/props/oak.glb"), (7, "lib", "forest/props/pine.glb")}
    await lab.drain()
    assert all(a["status"] == "indexed" for a in lab.store.assets.values())
    calls = len(lab.emb.inputs)

    again = await lab.ingest([oak, pine])
    assert again["status"] == "indexed" and again["enqueued"] == 0 and again["indexed"] == 2
    await lab.drain()
    assert len(lab.emb.inputs) == calls  # unchanged content_hash: nothing re-embedded

    changed = dict(oak, content_hash="cccccccc")
    lab.emb.image(lab.packs / "forest" / "previews/props_oak.glb_0.png", [0.5, 0.5, 0])
    result = await lab.ingest([changed, pine])
    assert result["enqueued"] == 1 and result["indexed"] == 1
    key = (7, "lib", "forest/props/oak.glb")
    assert key not in lab.store.vectors  # old vectors replaced, not mixed
    await lab.drain()
    assert len(lab.emb.inputs) == calls + 2  # one view + caption for the changed asset only
    assert len(lab.store.vectors[key]) == 2


@pytest.mark.asyncio
async def test_unsupported_batch_shapes_raise(lab):
    with pytest.raises(ValueError):
        await asset_v3.index_pack_assets({"version": 3, "root": "nope", "assets": []}, tenant_id=7,
                                         world_id="lib", pack_roots=lab.roots, store=lab.store)
    with pytest.raises(ValueError):
        await asset_v3.index_pack_assets({"version": 3, "root": "packs", "assets": []}, tenant_id=7,
                                         world_id="lib", pack_roots={}, store=lab.store)
    with pytest.raises(ValueError):
        await asset_v3.index_pack_assets({"version": 3, "root": "packs", "assets": {}}, tenant_id=7,
                                         world_id="lib", pack_roots=lab.roots, store=lab.store)


# ------------------------------------------------------------------ vectors + prefixes

@pytest.mark.asyncio
async def test_view_and_caption_vectors_written_with_prefix_on_text_only(lab):
    rec = lab.record("forest", "props/oak.glb", [[1, 0, 0], [0, 1, 0]], caption="a tall oak",
                     caption_vec=[0, 0, 2])
    await lab.ingest([rec])
    await lab.drain()
    rows = lab.store.vectors[(7, "lib", "forest/props/oak.glb")]
    assert [(r["vector_kind"], r["view_idx"]) for r in rows] == [("view", 0), ("view", 1), ("caption", 0)]
    assert rows[2]["embedding"] == pytest.approx([0, 0, 1])  # unit-normalised
    images = [i for i in lab.emb.inputs if i.media is not None]
    texts = [i for i in lab.emb.inputs if i.media is None]
    assert len(images) == 2 and all(i.text is None for i in images)
    assert [t.text for t in texts] == [DOCUMENT_PREFIX_NO_TITLE + "a tall oak"]
    hits = await lab.search("river oak")
    assert lab.emb.inputs[-1].text == QUERY_PREFIX + "river oak" and lab.emb.inputs[-1].media is None
    assert ids(hits) == ["forest/props/oak.glb"]


@pytest.mark.asyncio
async def test_embedding_is_semaphore_bounded(lab, monkeypatch):
    rec = lab.record("forest", "props/many.glb", [[1, 0, 0]] * 1, caption="c")
    rec["views"] = []
    for i in range(asset_packs.MAX_VIEWS):
        name = f"previews/many_{i}.png"
        lab.emb.image(lab.packs / "forest" / name, [1, 0, 0])
        rec["views"].append(name)
    live = peak = 0
    real = lab.emb

    async def gated(sel, inputs):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        assert len(inputs) <= asset_v3.EMBED_BATCH
        import asyncio
        await asyncio.sleep(0)
        live -= 1
        return await real(sel, inputs)

    monkeypatch.setattr(asset_v3, "EMBED_BATCH", 3)
    monkeypatch.setattr(asset_v3, "EMBED_CONCURRENCY", 2)
    monkeypatch.setattr(api.embeddings_core, "embed_multimodal_with", gated)
    await lab.ingest([rec])
    await lab.drain()
    assert peak <= 2
    assert len(lab.store.vectors[(7, "lib", "forest/props/many.glb")]) == asset_packs.MAX_VIEWS + 1


@pytest.mark.asyncio
async def test_failed_embedding_leaves_no_vectors_and_other_identity_is_not_scored(lab):
    rec = lab.record("forest", "props/oak.glb", [[1, 0, 0]])
    await lab.ingest([rec])

    async def boom(sel, inputs):
        raise RuntimeError("provider down")

    lab_embed = api.embeddings_core.embed_multimodal_with
    api.embeddings_core.embed_multimodal_with = boom
    try:
        await lab.drain()
    finally:
        api.embeddings_core.embed_multimodal_with = lab_embed
    assert lab.store.finished == [("forest/props/oak.glb", True)]
    assert (7, "lib", "forest/props/oak.glb") not in lab.store.vectors
    await lab.ingest([rec])
    await lab.drain()
    assert ids(await lab.search()) == ["forest/props/oak.glb"]
    lab.sel = dict(SEL, model="other")  # a different identity must not see the old vectors
    assert await lab.search() == []


# ------------------------------------------------------------------ scoring

@pytest.mark.asyncio
async def test_max_sim_beats_mean_then_mean_breaks_ties_then_asset_id(lab):
    # query vector is (1,0,0)
    spiky = lab.record("p", "a_spiky", [[1, 0, 0], [0, 1, 0]], digest="aaaaaaa1")  # max 1.0, mean 0.5
    steady = lab.record("p", "b_steady", [[0.8, 0.6, 0], [0.8, 0.6, 0]], digest="aaaaaaa2")  # max .8, mean .8
    tie_hi = lab.record("p", "c_tie_hi", [[1, 0, 0], [1, 0, 0]], digest="aaaaaaa3")  # max 1.0, mean 1.0
    tie_lo = lab.record("p", "d_tie_lo", [[1, 0, 0], [0, 0, 1]], digest="aaaaaaa4")  # max 1.0, mean 0.5
    tie_eq = lab.record("p", "a_tie_eq", [[1, 0, 0], [0, 0, 1]], digest="aaaaaaa5")  # same as d_tie_lo
    await lab.ingest([spiky, steady, tie_hi, tie_lo, tie_eq])
    await lab.drain()
    hits = await lab.search()
    assert ids(hits) == ["p/c_tie_hi", "p/a_spiky", "p/a_tie_eq", "p/d_tie_lo", "p/b_steady"]
    assert hits[0]["score"] == pytest.approx(1.0) and hits[0]["mean_score"] == pytest.approx(1.0)
    # max-sim wins over a better mean: spiky (mean .5) outranks steady (mean .8)
    assert ids(hits).index("p/a_spiky") < ids(hits).index("p/b_steady")
    assert ids(await lab.search()) == ids(hits)  # deterministic


@pytest.mark.asyncio
async def test_caption_vector_counts_as_one_more_view(lab):
    only_caption = lab.record("p", "cap", [[0, 1, 0]], caption="matches query", caption_vec=[1, 0, 0])
    plain = lab.record("p", "plain", [[0.6, 0.8, 0]])
    await lab.ingest([only_caption, plain])
    await lab.drain()
    assert ids(await lab.search()) == ["p/cap", "p/plain"]


# ------------------------------------------------------------------ filters

async def library(lab):
    rows = [
        lab.record("forest", "a.glb", [[1, 0, 0]], role="tree", biome="forest", licence="CC0", tris=500),
        lab.record("forest", "b.glb", [[0.9, 0.1, 0]], role="rock", biome="forest", licence="CC-BY", tris=9000),
        lab.record("desert", "c.glb", [[0.8, 0.2, 0]], role="tree", biome="desert", licence="CC0", tris=2000),
        lab.record("desert", "d.glb", [[0.7, 0.3, 0]], role="rock", biome="desert", licence="MIT", tris=100),
    ]
    await lab.ingest(rows)
    local = lab.record("vault", "e.glb", [[1, 0, 0]], root="packs-local", role="tree", biome="forest",
                       licence="CC0", tris=50)
    await lab.ingest([local], root="packs-local")
    await lab.drain()


@pytest.mark.asyncio
async def test_each_filter_narrows_results(lab):
    await library(lab)
    everything = ids(await lab.search())
    assert set(everything) == {"forest/a.glb", "forest/b.glb", "desert/c.glb", "desert/d.glb"}
    assert set(ids(await lab.search(role="tree"))) == {"forest/a.glb", "desert/c.glb"}
    assert set(ids(await lab.search(biome="desert"))) == {"desert/c.glb", "desert/d.glb"}
    assert set(ids(await lab.search(licence="CC-BY"))) == {"forest/b.glb"}
    assert set(ids(await lab.search(pack="desert"))) == {"desert/c.glb", "desert/d.glb"}
    assert set(ids(await lab.search(max_tris=2000))) == {"forest/a.glb", "desert/c.glb", "desert/d.glb"}
    assert set(ids(await lab.search(role="tree", biome="forest", max_tris=600))) == {"forest/a.glb"}
    assert await lab.search(role="nothing") == []
    assert set(ids(await lab.search(allow_local=True, distribution="local"))) == {"vault/e.glb"}
    assert set(ids(await lab.search(allow_local=True, distribution="any"))) == set(everything) | {"vault/e.glb"}


@pytest.mark.asyncio
async def test_tight_filter_still_returns_top_k(lab):
    # 20 great non-matching assets would fill any fixed pre-filter top-k; 6 weak matches must survive.
    rows = [lab.record("many", f"x{i:02d}.glb", [[1, 0, 0]], role="rock", digest=f"x{i:07d}") for i in range(20)]
    rows += [lab.record("many", f"t{i}.glb", [[0.1, 0.9, 0]], role="tree", digest=f"t{i:07d}") for i in range(6)]
    await lab.ingest(rows[:13])
    await lab.ingest(rows[13:])
    await lab.drain()
    hits = await lab.search(role="tree", k=5)
    assert len(hits) == 5 and all("/t" in h for h in ids(hits))


def test_filters_are_validated_and_local_needs_capability():
    with pytest.raises(PermissionError):
        asset_v3.normalize_filters(distribution="local")
    with pytest.raises(PermissionError):
        asset_v3.normalize_filters(distribution="any")
    assert asset_v3.normalize_filters(distribution="local", allow_local=True)["distribution"] == "local"
    assert asset_v3.normalize_filters()["distribution"] == "shared"
    for bad in ({"max_tris": -1}, {"max_tris": True}, {"role": ""}, {"distribution": "world"}):
        with pytest.raises(ValueError):
            asset_v3.normalize_filters(allow_local=True, **bad)


def test_search_sql_filters_inside_the_query_before_limit():
    stmt = asset_store.build_search_statement(
        [1.0, 0.0, 0.0], tenant_id=7, world_id="lib", model_id=MODEL, k=4, kind="prop", tags=["oak"],
        filters={"role": "tree", "biome": "forest", "licence": "CC0", "pack": "forest", "max_tris": 900,
                 "distribution": "shared"})
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql, params = str(compiled), compiled.params
    for needle in ("max(", "avg(", "GROUP BY", "ORDER BY", "LIMIT", "world_asset_vectors", "world_asset_embeddings"):
        assert needle in sql
    assert sql.index("WHERE") < sql.index("GROUP BY") < sql.index("ORDER BY") < sql.index("LIMIT")
    for value in (7, "lib", MODEL, "tree", "forest", "CC0", 900, "shared", 4, "prop"):
        assert value in params.values()
    assert re.search(r"tri_count <= ", sql) and "tri_count IS NOT NULL" in sql
    assert sql.count("tenant_id =") >= 3  # tenant pinned in both vector sources and the asset row
    unfiltered = str(asset_store.build_search_statement([1.0, 0.0, 0.0], tenant_id=7, world_id="lib",
                                                       model_id=MODEL, k=4,
                                                       filters={"distribution": "any"}).compile(
        dialect=postgresql.dialect()))
    assert "distribution" not in unfiltered.split("GROUP BY")[0].split("WHERE")[-1]


# ------------------------------------------------------------------ local never mirrored

@pytest.mark.asyncio
async def test_local_assets_never_reach_the_mirror(lab, caplog):
    await library(lab)
    sent = []

    async def sink(asset):
        sent.append(asset["asset_id"])

    shared_listing = await lab.search()
    everything = await lab.search(allow_local=True, distribution="any")
    assert "vault/e.glb" not in ids(shared_listing) and "vault/e.glb" in ids(everything)
    with caplog.at_level(logging.INFO, logger="whiskers.plugins.world_semantic"):
        result = await asset_adapters.mirror_assets(everything, sink, tenant_id=7, world_id="lib")
    assert result == {"mirrored": 4, "skipped_local": 1}
    assert "vault/e.glb" not in sent and len(sent) == 4
    assert any("skipped local asset" in r.message and "vault/e.glb" in r.message for r in caplog.records)
    # malformed / unknown metadata fails closed
    assert not asset_adapters.is_mirrorable(None) and not asset_adapters.is_mirrorable({"distribution": "x"})
    assert asset_adapters.is_mirrorable({"distribution": "shared"})


@pytest.mark.asyncio
async def test_local_search_requires_local_capable_context(lab):
    await library(lab)
    with pytest.raises(PermissionError):
        await lab.search(distribution="local")
    with pytest.raises(PermissionError):
        await lab.search(distribution="any")


def test_local_capability_comes_from_server_config(tmp_path, monkeypatch):
    from plugins.world_semantic_plugin import plugin_config
    (tmp_path / "packs").mkdir()
    (tmp_path / "packs-local").mkdir()
    monkeypatch.setitem(plugin_config.SETTINGS, "asset_pack_roots", {
        "7": {"lib": {"packs": str(tmp_path / "packs"), "packs-local": str(tmp_path / "packs-local")},
              "shared-only": {"packs": str(tmp_path / "packs")}}})
    assert asset_packs.local_capable(7, "lib")
    assert not asset_packs.local_capable(7, "shared-only")
    assert not asset_packs.local_capable(8, "lib")
    assert asset_adapters._v3_context(7, "lib")["allow_local"] is True
    assert asset_adapters._v3_context(7, "shared-only")["allow_local"] is False


# ------------------------------------------------------------------ tenancy

@pytest.mark.asyncio
async def test_tenant_isolation(lab):
    rec = lab.record("forest", "a.glb", [[1, 0, 0]])
    other = lab.record("forest", "z.glb", [[1, 0, 0]], digest="zzzzzzzz")
    await lab.ingest([rec], tenant=7)
    await lab.ingest([other], tenant=8)
    await lab.drain()
    assert ids(await lab.search(tenant=7)) == ["forest/a.glb"]
    assert ids(await lab.search(tenant=8)) == ["forest/z.glb"]
    assert await lab.search(tenant=9) == []
    assert await lab.search(tenant=7, world="elsewhere") == []
    stmt = asset_store.build_search_statement([1.0, 0.0, 0.0], tenant_id=8, world_id="lib", model_id=MODEL,
                                              k=3, filters={})
    assert 8 in stmt.compile(dialect=postgresql.dialect()).params.values()


# ------------------------------------------------------------------ real store round trip

class VectorDatabase:
    """Plugin SQL double (test_asset_index.Database) extended with the vector table."""

    def __new__(cls):
        from plugins.world_semantic_plugin.tests.test_asset_index import Database, Result

        class Impl(Database):
            def __init__(self):
                super().__init__()
                self.vectors = []

            async def execute(self, stmt, parameters=None):
                if isinstance(stmt, (Insert, Delete)) and stmt.table.name == "world_asset_vectors":
                    p = stmt.compile(dialect=postgresql.dialect()).params
                    if isinstance(stmt, Insert):
                        self.vectors.append(p)
                    else:
                        key = (p["tenant_id_1"], p["world_id_1"], p["asset_id_1"])
                        self.vectors = [v for v in self.vectors
                                        if (v["tenant_id"], v["world_id"], v["asset_id"]) != key]
                    return Result()
                return await super().execute(stmt, parameters)

        return Impl()


@pytest.mark.asyncio
async def test_real_store_stage_consume_noop_and_replace(lab, monkeypatch):
    db = VectorDatabase()
    monkeypatch.setattr(asset_store, "get_async_session", db.session)
    asset_store.register_consumer()
    try:
        rec = lab.record("forest", "props/oak.glb", [[1, 0, 0], [0, 1, 0]], caption="oak", digest="aaaaaaaa")
        body = {"version": 3, "root": "packs", "assets": [rec, dict(rec, asset_id="forest/../bad")]}
        result = await api.index_assets(body, tenant_id=7, world_id="lib", pack_roots=lab.roots)
        assert result["status"] == "enqueued" and result["enqueued"] == 1 and len(result["errors"]) == 1
        row = db.assets[7, "lib", "forest/props/oak.glb"]
        assert (row.pack_id, row.distribution, row.tri_count, row.role) == ("forest", "shared", 1000, "tree")
        digest = next(iter(db.jobs))
        await worker.process_batch([db.claimed(digest)])
        assert row.status == "indexed" and db.jobs[digest].status == "done"
        assert sorted((v["vector_kind"], v["view_idx"]) for v in db.vectors) == [
            ("caption", 0), ("view", 0), ("view", 1)]
        assert all(v["model"] == MODEL and v["generation"] == row.generation for v in db.vectors)
        embedded = len(lab.emb.inputs)

        again = await api.index_assets({"version": 3, "root": "packs", "assets": [rec]}, tenant_id=7,
                                       world_id="lib", pack_roots=lab.roots)
        assert again["status"] == "indexed" and again["enqueued"] == 0 and len(db.jobs) == 1
        assert len(lab.emb.inputs) == embedded

        changed = await api.index_assets({"version": 3, "root": "packs",
                                          "assets": [dict(rec, content_hash="bbbbbbbb")]},
                                         tenant_id=7, world_id="lib", pack_roots=lab.roots)
        assert changed["enqueued"] == 1 and db.vectors == []
        assert db.assets[7, "lib", "forest/props/oak.glb"].status == "enqueued"
    finally:
        asset_store.unregister_consumer()


@pytest.mark.asyncio
async def test_v3_search_routing_and_v1_default_path(lab, monkeypatch):
    seen = {}

    async def fake_v3(query, image, **kw):
        seen.update(kw, query=query)
        return []

    monkeypatch.setattr(asset_v3, "search_pack_assets", fake_v3)
    await api.search_assets("oak", tenant_id=7, world_id="lib", role="tree", pack_roots=lab.roots)
    assert seen["filters"] == {"role": "tree", "distribution": "shared"} and seen["tenant_id"] == 7
    seen.clear()
    await api.search_assets("oak", tenant_id=7, world_id="lib", pack_roots=lab.roots,
                            distribution="any", allow_local=True)
    assert seen["filters"]["distribution"] == "any"
    with pytest.raises(PermissionError):
        await api.search_assets("oak", tenant_id=7, world_id="lib", distribution="local")
    assert asset_v3.v3_engaged({}, {}) is False
