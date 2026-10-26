# Re-indexing vectors after an embedding identity change

## Why

Every stored vector carries an identity `provider:model:dims` (`embeddings_core.model_id_for`)
in its `model` column. Search embeds the query with the active selection and filters
`WHERE model = <active identity>` (`db_layer/embeddings/search_engine.py`, dense **and**
sparse leg). Moving from LM Studio text (`openai:<model>:<dims>`) to
`gemma-multimodal:embeddinggemma-2:768` therefore makes every existing row invisible until it
is re-embedded from its source text and rewritten with the new vector **and** identity.

`scripts/reindex_embeddings.py` does that: dry-run first, resumable, tenant-scoped.
Logic: `db_layer/embeddings/reindex.py` (storage behind a `ReindexStore` Protocol);
SQL: `db_layer/embedding_reindex_store.py`.

## What is re-indexed

| Collection | Table | Source text | Handled |
| --- | --- | --- | --- |
| `memory` | `memory_content_vectors` | `content_text` | yes |
| `search` | `search_content_vectors` | `content_text` | yes |
| (route index) | `route_embeddings` | `doc_text` (nullable) | **no**: no `tenant_id`, rebuilt by the embedding worker |

Rows with empty `content_text` are reported as `no_text` / `skipped: no source text` and
never touched. Rows with `tenant_id IS NULL` are never selected (every statement filters
`tenant_id = N`; `tenant_id=None` raises). A stale row whose `(collection, content_hash)` already
exists under the target identity is `superseded`: the unique key forbids rewriting it, it is only
counted (delete it by hand once you are happy with the target rows).

`route_embeddings` rebuild path: change the active route embedding selection and restart or
trigger the sync: `core_graph.worker.job_producer` enqueues `embedding_jobs` with a hash that
includes the model id, and `embedding_worker` writes new `route_embeddings` rows under the new
identity. Nothing in this script touches it.

## Prerequisite: column width

`embedding` is `VECTOR(<width>)`, fixed when the table was created
(`core_042`, `embedding_dimensions()`). The script **refuses** (exit 2, nothing written) when the
target width differs from the column width. A 768-wide target on a 1024/1536-wide column needs a
separate migration first (not part of this tool).

## Runbook

1. **Back up** the vector tables before any `--execute`:
   ```bash
   pg_dump -Fc -t memory_content_vectors -t search_content_vectors "$DATABASE_URL" > vectors-$(date +%F).dump
   ```
2. **Dry-run** (default; zero embed calls, zero writes, exit 0):
   ```bash
   python scripts/reindex_embeddings.py --tenant-id 1
   python scripts/reindex_embeddings.py --all-tenants
   ```
   Prints per-collection `total / already_target / stale / no_text / superseded`, the column
   width and a note (`refused: ...` for a width mismatch), then a JSON summary. Targets default
   to the active selection (`plugins.search_plugin` / `semantic_index`, what the stores write
   with); override with `--target-provider/--target-model/--target-dimensions`.
3. **Execute** (the only way to write):
   ```bash
   python scripts/reindex_embeddings.py --tenant-id 1 --execute --batch-size 16 --concurrency 2
   ```
   Stale rows are keyset-paginated by `id`; each batch is one embed call then one UPDATE of
   vector + identity. `--concurrency` (max 8) bounds in-flight batches with a semaphore.
   `--limit N` caps rows per collection per tenant. The document prefix is applied inside
   `embed_documents_with`, never by this script. `--collection memory|search` is repeatable.
4. **Resume**: simply rerun. Rows already on the target identity are skipped by the stale
   filter, so no work is repeated. `--resume-after ID` additionally starts after a printed
   `cursor`. A failed batch is logged (counts and ids only, no content), recorded in
   `results[].failed`, and the run continues; exit code is 1 if any batch failed. Rerun to retry
   only those rows. Exit 2 = refused (scope or dimension guard).
5. **Rollback**: restore the dump (`pg_restore --clean -t memory_content_vectors -t search_content_vectors`),
   and set the embedding selection back to the old identity. In-place rewriting means there is no
   per-row undo, which is what the backup is for.

## Search behavior while mixed

Rewrite is in place, so a row is on exactly one identity at any time, and search only returns
rows on the identity of the *active* selection (no fallback, no cross-space scoring):

- Active selection still old: migrated rows disappear from results, unmigrated ones remain.
- Active selection switched to target: migrated rows appear, unmigrated ones are invisible.

Either way results are partial until the run finishes. Prefer a quiet window: run `--execute`,
then switch the active selection (or switch first and accept an empty-then-filling index),
and verify with a dry-run that `stale` is 0 for every tenant.
