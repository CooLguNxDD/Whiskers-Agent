# WorldGen v3 asset library (world_semantic_plugin)

`index_assets` / `search_assets` ingest WorldGen v3 pack folders (`packs/` shareable,
`packs-local/` local-only), store per-view image vectors plus a caption vector per asset on the
embeddinggemma-2 identity (see [gemma-multimodal-embedding.md](gemma-multimodal-embedding.md)),
and score with max-sim over views (mean tie-break) behind role / biome / licence /
distribution / pack / max_tris filters. `distribution=local` assets are never mirrored.

Full request/response contract, scoring rule, distribution rules, errors and an example:
[`plugins/world_semantic_plugin/ASSET_API.md`](../plugins/world_semantic_plugin/ASSET_API.md)
(section "WorldGen v3 asset library").
